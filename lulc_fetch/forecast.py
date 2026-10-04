"""Forecasting from tables: the next hours / days / weeks / months of a value (AQI, rainfall, humidity, sales…) at one
place or at many (stations), from its own past, the calendar, nearby stations and other inputs (e.g. weather).

One "global" model is trained over every series at once (the usual approach for station networks): each row is one
station at one time step, and its features are

* the value's own past: lags (1, 2, 3 … steps and one / two cycles ago), recent averages and variability,
* the calendar: hour of day, day of week, day of year, month (as smooth cycles),
* where the station is (latitude / longitude) and the latest value at its nearest stations (inverse-distance weighted),
* other inputs at that time and one step before (weather…). For the future they come from the table when it has them
  (e.g. a weather forecast, rows after the last value) and otherwise repeat the last cycle.

Two ways to forecast several steps: step by step (predict the next step, feed the prediction back in: good for smooth
cycles such as hourly AQI) or direct (predict each step ahead straight from what is known at the start, with "how far
ahead" and the same time in earlier cycles as inputs, so errors don't build up: good for values that are often 0, such
as daily rain). Automatic tries the best model both ways. Everything is checked the honest way, by backtesting: trained
on the data before a cut-off, the model forecasts the horizon after it, for cut-offs spread over the last year (every
season), and the errors are compared with simple baselines (the last value, the same time one cycle ago). The backtest errors also give the forecast's uncertainty band (80 % of them were within it).

    from lulc_fetch import forecast
    info = forecast.describe("aqi.csv")
    rep = forecast.train("aqi.csv", "out/", time_col="time", target="aqi", series_col="name", horizon=48)
    fc = forecast.predict(rep["path"], "aqi_new.csv", horizon=24)
"""

from __future__ import annotations

import json
import logging
import re
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import progress

log = logging.getLogger(__name__)

# time step: pandas range code, unit name, cycle (season) length, default horizon, lags, windows for averages
FREQS = {
    "h": {"title": "Hourly", "unit": "hour", "range": "h", "season": 24, "horizon": 48,
          "lags": [1, 2, 3, 4, 6, 12, 24, 48, 72, 168], "wins": [3, 6, 24, 168]},
    "D": {"title": "Daily", "unit": "day", "range": "D", "season": 7, "horizon": 14,
          "lags": [1, 2, 3, 7, 14, 21, 28, 365], "wins": [3, 7, 28]},
    "W": {"title": "Weekly", "unit": "week", "range": "W-MON", "season": 52, "horizon": 8,
          "lags": [1, 2, 3, 4, 8, 13, 26, 52], "wins": [4, 13]},
    "MS": {"title": "Monthly", "unit": "month", "range": "MS", "season": 12, "horizon": 12,
           "lags": [1, 2, 3, 6, 12, 24], "wins": [3, 12]},
}

MODELS = {
    "auto": {"title": "Compare all and keep the best", "desc": "Backtests every model below and keeps the one with the lowest error (the baselines are always compared)."},
    "lightgbm": {"title": "LightGBM", "desc": "Gradient-boosted trees, fast and usually the most accurate on tables: the common choice for station AQI, humidity or rainfall forecasting. Handles missing values."},
    "xgboost": {"title": "XGBoost", "desc": "Gradient-boosted trees like LightGBM, a little slower; often as accurate."},
    "hgb": {"title": "Gradient boosting (scikit-learn)", "desc": "scikit-learn's histogram gradient boosting: like LightGBM, always available."},
    "rf": {"title": "Random Forest", "desc": "Many decision trees averaged: robust, little tuning, smooth forecasts; can't go beyond the values it has seen."},
    "linear": {"title": "Linear (ridge) regression", "desc": "A weighted sum of the features: simple, fast, explainable; misses non-linear effects."},
    "seasonal": {"title": "Same time last cycle (baseline)", "desc": "The value one cycle earlier (24 hours, 7 days, 12 months…): a baseline every model should beat."},
    "naive": {"title": "Last value (baseline)", "desc": "The last known value, held: the simplest baseline."},
}
BASELINES = ("naive", "seasonal")


def _available(key: str) -> bool:
    import importlib.util
    return key not in ("lightgbm", "xgboost") or importlib.util.find_spec(key) is not None


def schema() -> dict:
    return {"models": {k: {**m, "available": _available(k)} for k, m in MODELS.items()},
            "freqs": {k: {"title": f["title"], "unit": f["unit"], "season": f["season"], "horizon": f["horizon"]} for k, f in FREQS.items()}}


# ------------------------------------------------------------------ reading a table

def _read(path, columns: list[str] | None = None) -> pd.DataFrame:
    path = Path(path)
    cols = list(dict.fromkeys(c for c in columns if c)) if columns else None
    if path.suffix.lower() == ".parquet":
        try:
            return pd.read_parquet(path, columns=cols)
        except Exception:   # a column that isn't there: read it all, _panel says which is missing
            return pd.read_parquet(path)
    if cols:
        head = pd.read_csv(path, nrows=0).columns
        if all(c in head for c in cols):
            return pd.read_csv(path, usecols=cols, low_memory=False)
    return pd.read_csv(path, low_memory=False)


def _cfg_columns(cfg: dict) -> list[str]:
    return [cfg.get("time_col"), cfg.get("target"), cfg.get("series_col"), cfg.get("lat_col"), cfg.get("lon_col"), *cfg.get("inputs", [])]


def _times(s: pd.Series) -> pd.Series:
    """Dates / times of a column (text, numbers like 2024 or 20240131, or dates), timezone dropped (kept as local).
    Each distinct value is read once (a big table repeats the same dates for every station)."""
    if not pd.api.types.is_datetime64_any_dtype(s) and len(s) > 5000:
        codes, uniq = pd.factorize(s)
        if len(uniq) < len(s) // 2:
            u = _times(pd.Series(uniq))
            out = pd.Series(u.to_numpy().take(np.where(codes < 0, 0, codes)), index=s.index)
            out[codes < 0] = pd.NaT
            return out
    if pd.api.types.is_datetime64_any_dtype(s):
        t = s
    else:
        x = s.astype(str).str.strip()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t = pd.to_datetime(x, errors="coerce", format="mixed")
            if t.isna().mean() > 0.2 or _looks_dayfirst(x):
                t2 = pd.to_datetime(x, errors="coerce", format="mixed", dayfirst=True)
                if t2.notna().sum() >= t.notna().sum():
                    t = t2
            if t.isna().all() and pd.api.types.is_numeric_dtype(s):   # years (2015) or yyyymmdd
                v = pd.to_numeric(s, errors="coerce")
                if v.between(1800, 2200).mean() > 0.9:
                    t = pd.to_datetime(v.astype("Int64").astype(str), format="%Y", errors="coerce")
                elif v.between(18000101, 22001231).mean() > 0.9:
                    t = pd.to_datetime(v.astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    try:
        if getattr(t.dt, "tz", None) is not None:
            t = t.dt.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return t


def _looks_dayfirst(x: pd.Series) -> bool:
    """'13-01-2026 …' (day first, as CPCB writes dates): a first number above 12 somewhere."""
    m = x.head(500).str.extract(r"^(\d{1,2})[-/.](\d{1,2})[-/.]\d{4}")
    if m.isna().all().all():
        return False
    a = pd.to_numeric(m[0], errors="coerce")
    return bool((a > 12).any())


def _guess_freq(t: pd.Series, ids: pd.Series | None = None) -> str:
    df = pd.DataFrame({"t": t, "i": ids if ids is not None else 0}).dropna().drop_duplicates()
    d = df.sort_values(["i", "t"]).groupby("i")["t"].diff().dropna()
    d = d[d > pd.Timedelta(0)]
    if d.empty:
        raise ValueError("Every row has the same time: a forecast needs values at several times")
    hours = d.median() / pd.Timedelta(hours=1)
    if hours <= 1.5:
        return "h"
    if hours <= 36:
        return "D"
    if hours <= 10 * 24:
        return "W"
    if hours <= 45 * 24:
        return "MS"
    raise ValueError(f"The values are about {hours / 24 / 365:.1f} years apart: too far apart to forecast (hourly to monthly data)")


_TIME_NAMES = re.compile(r"^(time|date|datetime|timestamp|date_?time|day|month|year|period|ds|from_?date|from|last_?update|.*_time|.*_date|time_.*|date_.*)$", re.I)
_ID_NAMES = re.compile(r"(station|site|location|name|city|district|id|code|place|sensor|gauge|series|well|region|state)", re.I)
_TARGET_NAMES = ["aqi", "value", "pm2_5", "pm25", "pm2.5", "pm10", "rain", "rainfall", "precipitation", "temperature", "humidity",
                 "sales", "demand", "load", "yield", "ndvi", "y", "target"]


def describe(path) -> dict:
    """Columns and what each one probably is: time, value to forecast, series (station) id, lat / lon, other inputs."""
    from .tableview import _axis_score
    df = _read(path)
    if df.empty:
        raise ValueError("The table is empty")
    cols, time_col, best = [], None, 0.0
    for c in df.columns:
        s = df[c]
        num = pd.api.types.is_numeric_dtype(s)
        info = {"name": str(c), "type": "number" if num else "text", "unique": int(s.nunique(dropna=True)), "nulls": int(s.isna().sum())}
        if not num or _TIME_NAMES.match(str(c)) or pd.api.types.is_datetime64_any_dtype(s):
            t = _times(s.head(2000))
            share = float(t.notna().mean())
            if share >= 0.8 and t.nunique() > 1:
                info["type"] = "time"
                score = share + (0.5 if _TIME_NAMES.match(str(c)) else 0)
                if score > best:
                    time_col, best = str(c), score
        cols.append(info)
    names = [c["name"] for c in cols]
    lon = max(names, key=lambda n: _axis_score(n, "lon"), default=None)
    lat = max(names, key=lambda n: _axis_score(n, "lat"), default=None)
    lon = lon if lon and _axis_score(lon, "lon") >= 2 else None
    lat = lat if lat and _axis_score(lat, "lat") >= 2 else None
    numeric = [c["name"] for c in cols if c["type"] == "number" and c["name"] not in (lon, lat) and c["unique"] > 1]
    low = {n.lower(): n for n in numeric}
    target = next((low[n] for n in _TARGET_NAMES if n in low), None) or next((n for n in numeric if not _ID_NAMES.search(n)), None)
    series = None
    if time_col:
        n = len(df)
        cands = [c for c in cols if c["name"] not in (time_col, lon, lat, target) and 1 < c["unique"] <= max(2, n // 3)
                 and (c["type"] == "text" or _ID_NAMES.search(c["name"]))]
        cands.sort(key=lambda c: (not _ID_NAMES.search(c["name"]), c["type"] != "text", -c["unique"]))
        # a real series column: each series has several times
        for c in cands:
            if df.groupby(c["name"])[time_col].nunique().median() > 1:
                series = c["name"]
                break
    out = {"rows": int(len(df)), "columns": cols, "guess": {"time": time_col, "target": target, "series": series, "lat": lat, "lon": lon}}
    if time_col:
        t = _times(df[time_col])
        ids = df[series].astype(str) if series else None
        try:
            f = _guess_freq(t, ids)
            out["freq"] = f
            out["guess"]["horizon"] = FREQS[f]["horizon"]
        except ValueError as e:
            out["freq_error"] = str(e)
        out["start"], out["end"] = str(t.min()), str(t.max())
        out["series_count"] = int(df[series].nunique()) if series else 1
        if target:
            has = df[target].notna()
            last = t[has].max()
            out["last_value_time"] = str(last)
            out["future_rows"] = int((t > last).sum())
        out["inputs"] = [n for n in numeric if n != target and not re.match(r"^(no|sl_?no|s_?no|index|fid|objectid|id)$", n, re.I)]
    return out


# ------------------------------------------------------------------ table → panel (series × time)

def _panel(df: pd.DataFrame, cfg: dict, freq: str | None = None) -> dict:
    """Regular time steps for every series: Y (series × times, the value), X (inputs × series × times), coordinates."""
    tc, yc, sc = cfg["time_col"], cfg["target"], cfg.get("series_col")
    need = [c for c in [tc, yc, sc, cfg.get("lat_col"), cfg.get("lon_col"), *cfg.get("inputs", [])] if c]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"The table has no column {', '.join(miss)}")
    t = _times(df[tc]).to_numpy()
    ok = ~pd.isna(t)
    if not ok.any():
        raise ValueError(f"No dates / times could be read from {tc}")
    if sc:   # series as numbers 0 … S-1 (a big table repeats each name thousands of times)
        codes, uniq = pd.factorize(df[sc].astype(str).str.strip() if df[sc].dtype == object else df[sc], sort=True)
        ids = [str(u) for u in uniq]
        ok &= codes >= 0
    else:
        codes, ids = np.zeros(len(df), dtype=np.int64), ["all"]
    t, codes = pd.DatetimeIndex(t[ok]), codes[ok]
    freq = freq or _guess_freq(pd.Series(t), pd.Series(codes))
    F = FREQS[freq]
    if freq == "h":
        t = t.floor("h")
    elif freq == "D":
        t = t.floor("D")
    elif freq == "W":
        t = t.to_period("W-SUN").start_time
    else:
        t = t.to_period("M").start_time
    y = pd.to_numeric(df[yc], errors="coerce").to_numpy(dtype="float64")[ok]
    if not np.isfinite(y).any():
        raise ValueError(f"{yc} has no numbers")
    times = pd.date_range(t.min(), t.max(), freq=F["range"])
    if len(times) > 200000:
        raise ValueError(f"{len(times):,} time steps: too many (choose a coarser time step)")
    S, T = len(ids), len(times)
    if S * T > 60_000_000:
        raise ValueError(f"{S:,} series × {T:,} time steps is too big: use fewer series or a coarser time step")
    cell = codes * T + times.get_indexer(t)

    def grid(v):   # the mean of the values in each series × time step (several readings in one step are averaged)
        f = np.isfinite(v)
        s = np.bincount(cell[f], weights=v[f], minlength=S * T)
        n = np.bincount(cell[f], minlength=S * T)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n > 0, s / np.maximum(n, 1), np.nan).reshape(S, T)

    Y = grid(y)
    X = np.stack([grid(pd.to_numeric(df[k], errors="coerce").to_numpy(dtype="float64")[ok]) for k in cfg.get("inputs", [])]) \
        if cfg.get("inputs") else np.zeros((0, S, T))
    coords = None
    if cfg.get("lat_col") and cfg.get("lon_col"):
        c = pd.DataFrame({"i": codes, "lat": pd.to_numeric(df[cfg["lat_col"]], errors="coerce").to_numpy()[ok],
                          "lon": pd.to_numeric(df[cfg["lon_col"]], errors="coerce").to_numpy()[ok]})
        coords = c.dropna().groupby("i")[["lat", "lon"]].first().reindex(range(S)).to_numpy(dtype="float64")
    # short gaps in the value are filled (straight line); long gaps stay empty
    lim = max(1, F["season"] // 4)
    Yf = pd.DataFrame(Y.T).interpolate(limit=lim, limit_area="inside").to_numpy().T
    Xf = np.stack([pd.DataFrame(x.T).interpolate(limit=F["season"], limit_area="inside").to_numpy().T for x in X]) if len(X) else X
    has = np.isfinite(Y).any(0)
    t_obs = int(np.nonzero(has)[0].max()) + 1           # the time steps that have values; later rows = future inputs only
    return {"Y": Yf[:, :t_obs], "X": Xf, "times": times, "t_obs": t_obs, "ids": ids, "coords": coords, "freq": freq,
            "filled": int(np.isfinite(Yf[:, :t_obs]).sum() - np.isfinite(Y[:, :t_obs]).sum())}


# ------------------------------------------------------------------ features

def _neighbours(coords, k: int = 5):
    """Inverse-distance weights of each series' k nearest other series (None without coordinates or with < 3 series)."""
    if coords is None or len(coords) < 3 or not np.isfinite(coords).all():
        return None
    from scipy.sparse import csr_matrix
    from scipy.spatial import cKDTree
    lat, lon = np.radians(coords[:, 0]), np.radians(coords[:, 1])
    xyz = np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    k = min(k, len(coords) - 1)
    chord, near = cKDTree(xyz).query(xyz, k=k + 1)        # the first is the point itself
    dkm = 2 * 6371 * np.arcsin(np.clip(chord[:, 1:] / 2, 0, 1))
    w = 1.0 / (dkm + 0.5) ** 2
    rows = np.repeat(np.arange(len(coords)), k)
    return csr_matrix((w.ravel(), (rows, near[:, 1:].ravel())), shape=(len(coords), len(coords)))


def _calendar(times: pd.DatetimeIndex, freq: str) -> tuple[np.ndarray, list[str]]:
    cols, names = [], []
    def cyc(v, period, name):
        cols.extend([np.sin(2 * np.pi * v / period), np.cos(2 * np.pi * v / period)])
        names.extend([f"{name}|sin", f"{name}|cos"])
    if freq == "h":
        cyc(times.hour.to_numpy(), 24, "Hour of day")
        cols.append(times.dayofweek.to_numpy().astype(float))
        names.append("Day of week")
        cyc(times.dayofyear.to_numpy(), 365.25, "Day of year")
    elif freq == "D":
        cols.append(times.dayofweek.to_numpy().astype(float))
        names.append("Day of week")
        cyc(times.dayofyear.to_numpy(), 365.25, "Day of year")
    elif freq == "W":
        cyc(times.isocalendar().week.to_numpy().astype(float), 52.18, "Week of year")
    else:
        cyc(times.month.to_numpy().astype(float), 12, "Month")
    return np.column_stack(cols).astype("float64"), names


def _setup(P: dict, cfg: dict) -> dict:
    """What the features are for this data (kept in the model so forecasting rebuilds the same ones)."""
    F = FREQS[P["freq"]]
    T = P["t_obs"]
    lags = [l for l in F["lags"] if l <= 1 or l <= T // 2]
    wins = [w for w in F["wins"] if w <= max(3, T // 2)]
    season = F["season"] if F["season"] <= T // 2 else None
    return {"freq": P["freq"], "lags": lags, "wins": wins, "season": season, "use_nb": P["coords"] is not None and len(P["ids"]) >= 3,
            "use_coords": bool(P["coords"] is not None and np.isfinite(P["coords"]).all()), "inputs": cfg.get("inputs", [])}


def _names(st: dict, target: str) -> list[tuple[str, str]]:
    """(feature, group) names in the order _feat builds them."""
    u = FREQS[st["freq"]]["unit"]
    def ago(n):
        return f"{n} {u}{'s' if n > 1 else ''} ago"
    out = [(f"{target} {ago(l)}", f"{target}: recent values" if l < (st["season"] or 10 ** 9) else f"{target}: same time in earlier cycles") for l in st["lags"]]
    out += [(f"{target} average of the last {w} {u}s", f"{target}: recent averages") for w in st["wins"]]
    out += [(f"{target} variability (last {st['wins'][-1]} {u}s)", f"{target}: recent averages")]
    if st["use_nb"]:
        out += [(f"{target} at the nearest stations, {ago(1)}", "Nearby stations")]
    if st["use_coords"]:
        out += [("Latitude", "Location"), ("Longitude", "Location")]
    if st.get("strategy") == "direct" and st["season"]:
        out += [(f"{target} at the same time, {n} cycle{'s' if n > 1 else ''} before the latest known", f"{target}: same time in earlier cycles") for n in (1, 2, 3)]
    for k in st["inputs"]:
        out += [(k, k), (f"{k}, one {u} before", k)]
    out += [(n.split("|")[0] + (" (cycle)" if "|" in n else ""), n.split("|")[0]) for n in _calendar(pd.date_range("2000-01-01", periods=2, freq=FREQS[st["freq"]]["range"]), st["freq"])[1]]
    if st.get("strategy") == "direct":
        out += [(f"{u.capitalize()}s ahead", "How far ahead")]
    return out


def _origin(st: dict, Y: np.ndarray, coords, W, t: int) -> np.ndarray:
    """What is known at time step t about every series (from the values before t): its recent values, averages,
    variability, its nearest stations' latest value, where it is."""
    S = Y.shape[0]
    nan = np.full(S, np.nan)
    cols = [Y[:, t - l] if t - l >= 0 else nan for l in st["lags"]]
    for w in st["wins"]:
        seg = Y[:, max(0, t - w):t]
        ok = np.isfinite(seg)
        n = ok.sum(1)
        cols.append(np.where(n > 0, np.where(ok, seg, 0).sum(1) / np.maximum(n, 1), np.nan))
    seg = Y[:, max(0, t - st["wins"][-1]):t]
    ok = np.isfinite(seg)
    n = ok.sum(1)
    m = np.where(ok, seg, 0).sum(1) / np.maximum(n, 1)
    cols.append(np.where(n > 1, np.sqrt(np.where(ok, (seg - m[:, None]) ** 2, 0).sum(1) / np.maximum(n - 1, 1)), np.nan))
    if st["use_nb"]:
        v = Y[:, t - 1] if t >= 1 else nan
        okv = np.isfinite(v)
        den = W @ okv.astype(float)
        cols.append(np.where(den > 0, (W @ np.where(okv, v, 0)) / np.where(den > 0, den, 1), np.nan))
    if st["use_coords"]:
        cols.extend([coords[:, 0], coords[:, 1]])
    return np.column_stack(cols)


def _target(st: dict, Y: np.ndarray, X: np.ndarray, cal: np.ndarray, S: int, t: int, h: int) -> np.ndarray:
    """What is known about the time being forecast, h steps after t: the other inputs then and one step before (from
    the table, or repeated from the last cycle), the calendar, and (direct forecasting) how far ahead it is."""
    nan = np.full(S, np.nan)
    u = t + h
    cols = []
    if st.get("strategy") == "direct" and st["season"]:
        s = st["season"]
        back = -(-(h + 1) // s)          # whole cycles back from the forecast time to a known value
        for k in (0, 1, 2):
            i = u - s * (back + k)
            cols.append(Y[:, i] if i >= 0 else nan)
    for k in range(len(st["inputs"])):
        cols.append(X[k, :, u] if u < X.shape[2] else nan)
        cols.append(X[k, :, u - 1] if 1 <= u <= X.shape[2] else nan)
    cols.extend(np.repeat(cal[u][None], S, 0).T)
    if st.get("strategy") == "direct":
        cols.append(np.full(S, h + 1.0))
    return np.column_stack(cols) if cols else np.zeros((S, 0))


def _feat(st: dict, Y: np.ndarray, X: np.ndarray, cal: np.ndarray, coords, W, t: int, h: int = 0) -> np.ndarray:
    """The features of every series for predicting the value h steps after time step t (from what is known before t)."""
    return np.hstack([_origin(st, Y, coords, W, t), _target(st, Y, X, cal, Y.shape[0], t, h)])


MAX_ROWS = 1_500_000   # training rows kept (a random sample of series at each time step beyond this)


def _rows(st, Y, X, cal, coords, W, H: int = 1, start: int = 1):
    """Training rows: every series, time step t and step ahead h (1 … H for direct forecasting, 1 for step by step)
    with a value at t+h and at t-1. For very large tables (thousands of grid points × years of days, or long horizons)
    a random share of them, so memory stays bounded. Returns features, values and the time step of each value."""
    F, y, tt = [], [], []
    S, T = Y.shape
    hs = range(H) if st.get("strategy") == "direct" else range(1)
    share = min(1.0, MAX_ROWS / max(1, S * (T - start) * len(hs)))
    rng = np.random.default_rng(0)
    for t in range(start, T):
        has_prev = np.isfinite(Y[:, t - 1])
        if not has_prev.any():
            continue
        f0 = None
        for h in hs:
            if t + h >= T:
                break
            ok = has_prev & np.isfinite(Y[:, t + h])
            if share < 1:
                ok &= rng.random(S) < share
            if not ok.any():
                continue
            if f0 is None:
                f0 = _origin(st, Y, coords, W, t)
            F.append(np.hstack([f0[ok], _target(st, Y, X, cal, S, t, h)[ok]]))
            y.append(Y[ok, t + h])
            tt.append(np.full(int(ok.sum()), t + h))
    if not F:
        raise ValueError("No time steps with a value and the one before it: is the time step right?")
    return np.vstack(F).astype(np.float32), np.concatenate(y), np.concatenate(tt)


# ------------------------------------------------------------------ models

def _make(key: str, n: int):
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if key == "linear":
        from sklearn.linear_model import Ridge
        return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(), Ridge(alpha=1.0))
    if key == "rf":
        from sklearn.ensemble import RandomForestRegressor
        return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                             RandomForestRegressor(n_estimators=200, min_samples_leaf=3, max_features=0.5, n_jobs=-1, random_state=0))
    if key == "hgb":
        from sklearn.ensemble import HistGradientBoostingRegressor
        return HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, min_samples_leaf=max(5, min(20, n // 200)), random_state=0)
    if key == "lightgbm":
        from lightgbm import LGBMRegressor
        return LGBMRegressor(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=max(5, min(20, n // 200)),
                             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=0, verbose=-1, n_jobs=-1)
    if key == "xgboost":
        from xgboost import XGBRegressor
        return XGBRegressor(n_estimators=500, learning_rate=0.03, max_depth=6, subsample=0.8, colsample_bytree=0.8, random_state=0, n_jobs=-1)
    raise ValueError(f"Unknown model {key}")


def _fit(key, Fx, y):
    if key in BASELINES:
        return None
    m = _make(key, len(y))
    if len(y) > 150000:   # plenty: a random subset keeps training fast
        i = np.random.default_rng(0).choice(len(y), 150000, replace=False)
        Fx, y = Fx[i], y[i]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(Fx, y)
    return m


def _future_inputs(X: np.ndarray, start: int, end: int, season: int | None, known: bool) -> np.ndarray:
    """Inputs up to time step `end`: the table's values (when known for the future) or the last cycle repeated."""
    if not len(X):
        return np.zeros((0, X.shape[1], end))
    out = np.full((X.shape[0], X.shape[1], end), np.nan)
    n = min(X.shape[2], end)
    out[:, :, :n] = X[:, :, :n]
    if not known:
        out[:, :, start:] = np.nan
    step = season or 1
    for t in range(start, end):
        miss = ~np.isfinite(out[:, :, t])
        if miss.any() and t - step >= 0:
            out[:, :, t] = np.where(miss, out[:, :, t - step], out[:, :, t])
    return out


def _forecast(key, model, st, Y, X, cal, coords, W, start: int, H: int, lo: float | None = None, hi: float | None = None) -> np.ndarray:
    """Forecast time steps start … start+H-1 of every series: direct (each step from what is known at the start, so
    errors don't feed back) or step by step (each prediction fed back in as if it were a value)."""
    if key in BASELINES or st.get("strategy") != "direct":
        return _recursive(key, model, st, Y, X, cal, coords, W, start, H, lo, hi)
    Yk = Y[:, :start]                   # only what is known at the start
    f0 = _origin(st, Yk, coords, W, start)
    out = np.full((Y.shape[0], H), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for h in range(H):
            p = model.predict(np.hstack([f0, _target(st, Yk, X, cal, Y.shape[0], start, h)]))
            out[:, h] = np.clip(p, lo, hi) if lo is not None else p
    return out


def _recursive(key, model, st, Y, X, cal, coords, W, start: int, H: int, lo: float | None = None, hi: float | None = None) -> np.ndarray:
    """Forecast time steps start … start+H-1 of every series, feeding each step's prediction back in."""
    S = Y.shape[0]
    Yx = np.full((S, start + H), np.nan)
    Yx[:, :start] = Y[:, :start]
    last = pd.DataFrame(Y[:, :start].T).ffill().to_numpy()[-1] if start else np.full(S, np.nan)
    out = np.full((S, H), np.nan)
    for h in range(H):
        t = start + h
        if key == "naive":
            p = last
        elif key == "seasonal":
            s = st["season"] or 1
            p = Yx[:, t - s] if t - s >= 0 else last
            p = np.where(np.isfinite(p), p, last)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p = model.predict(_feat(st, Yx, X, cal, coords, W, t))
        if lo is not None:
            p = np.clip(p, lo, hi)
        Yx[:, t] = p
        out[:, h] = p
    return out


YEAR = {"h": 8760, "D": 365, "W": 52, "MS": 12}   # time steps in a year


def _origins(T: int, H: int, freq: str, n: int) -> list[int]:
    """Backtest cut-offs: n windows of H steps spread over the last year of the data (so a seasonal value is checked in
    every season, not only in the last weeks), each with at least a third of the data (and 2 horizons) before it."""
    n = max(1, n)
    lo_ = max(2 * H, T // 3)
    b = T - H
    if b < lo_:
        return [b]
    if n == 1:
        return [b]
    a = max(lo_, b - YEAR[freq] * (n - 1) / n)
    return sorted({int(round(v)) for v in np.linspace(a, b, n)})


def _metrics(err: np.ndarray, actual: np.ndarray) -> dict:
    e = err[np.isfinite(err)]
    if not e.size:
        return {"mae": None, "rmse": None, "bias": None, "mape": None, "n": 0}
    a = actual[np.isfinite(err)]
    nz = np.abs(a) > 1e-9
    return {"mae": float(np.mean(np.abs(e))), "rmse": float(np.sqrt(np.mean(e ** 2))), "bias": float(np.mean(e)),
            "mape": float(np.mean(np.abs(e[nz] / a[nz])) * 100) if nz.sum() > 0.5 * e.size else None, "n": int(e.size)}


def _bands(errs: np.ndarray) -> list:
    """How far off the backtests were at each step ahead: 80 % of their errors were within this (pooled with the
    neighbouring steps). The forecast ± this is the uncertainty band (about 80 %)."""
    H = errs.shape[-1]
    e = np.abs(errs.reshape(-1, H))
    out = []
    for h in range(H):
        w = max(1, H // 10)
        v = e[:, max(0, h - w):h + w + 1]
        v = v[np.isfinite(v)]
        out.append(float(np.quantile(v, 0.8)) if v.size >= 5 else None)
    return out


# ------------------------------------------------------------------ train

def train(table, out_dir, *, time_col: str, target: str, series_col: str | None = None, lat_col: str | None = None,
          lon_col: str | None = None, inputs: list[str] | None = None, freq: str = "auto", horizon: int | None = None,
          model: str = "auto", backtests: int = 3, future_inputs: str = "auto", clip: str = "auto", strategy: str = "auto",
          name: str = "forecast") -> dict:
    """Train a forecasting model on a table, check it by backtesting, forecast the next `horizon` steps and save it."""
    import joblib
    t0 = time.time()
    progress.update(0.01, "Reading the table")
    inputs = [k for k in (inputs or []) if k not in (time_col, target, series_col, lat_col, lon_col)]
    cfg = {"time_col": time_col, "target": target, "series_col": series_col or None, "lat_col": lat_col or None,
           "lon_col": lon_col or None, "inputs": inputs}
    df = _read(table, _cfg_columns(cfg))
    if bool(cfg["lat_col"]) != bool(cfg["lon_col"]):
        cfg["lat_col"] = cfg["lon_col"] = None
    P = _panel(df, cfg, None if freq == "auto" else freq)
    F = FREQS[P["freq"]]
    st = _setup(P, cfg)
    st["strategy"] = "direct" if strategy == "direct" else "recursive"
    Y, X, T = P["Y"], P["X"], P["t_obs"]
    H = int(horizon or F["horizon"])
    if H < 1:
        raise ValueError("The horizon must be at least 1 step")
    warn = []
    if T < 3 * H + 10:
        maxh = max(1, (T - 10) // 3)
        raise ValueError(f"Only {T} {F['unit']}s of data for a forecast {H} {F['unit']}s ahead: choose a horizon of {maxh} or less, or use a longer table")
    known = future_inputs == "known" or (future_inputs == "auto" and len(inputs) > 0 and X.shape[2] > T and np.isfinite(X[:, :, T:]).any())
    lo_hi = (None, None)
    finite = Y[np.isfinite(Y)]
    if clip == "auto" and finite.size and finite.min() >= 0:
        lo_hi = (0.0, None)   # a value that is never negative (AQI, rain…) isn't forecast below 0
    lo, hi = lo_hi[0], (np.inf if lo_hi[0] is not None else None)
    W = _neighbours(P["coords"]) if st["use_nb"] else None
    st["use_nb"] = W is not None
    cal_all = _calendar(pd.date_range(P["times"][0], periods=max(X.shape[2], T) + H, freq=F["range"]), P["freq"])[0]
    if model != "auto" and not _available(model):
        raise ValueError(f"{MODELS[model]['title']} isn't installed")
    origins = _origins(T, H, P["freq"], backtests)
    keys = [k for k in MODELS if k not in ("auto",) and _available(k)] if model == "auto" else list(dict.fromkeys([model, *BASELINES]))
    plan = ["recursive", "direct"] if strategy == "auto" else [st["strategy"]]
    sts, data, res = {}, {}, {}

    def backtest(strat, ks, p0, p1):
        s = sts[strat] = {**st, "strategy": strat}
        progress.update(p0, f"Building the features ({'direct' if strat == 'direct' else 'step by step'})")
        data[strat] = Fx, y, tt = _rows(s, Y, X, cal_all, P["coords"], W, H)
        n = len(ks) * len(origins)
        i = 0
        for o in origins:
            trn = tt < o
            Xo = _future_inputs(X, o, o + H, st["season"], known)
            for k in ks:
                i += 1
                progress.update(p0 + (p1 - p0) * i / n, f"Backtest {origins.index(o) + 1} of {len(origins)}: {MODELS[k]['title']}"
                                + (" (direct)" if strat == "direct" and k not in BASELINES else ""))
                m = _fit(k, Fx[trn], y[trn])
                pred = _forecast(k, m, s, Y, Xo, cal_all, P["coords"], W, o, H, lo, hi)
                act = Y[:, o:o + H]
                r = res.setdefault((k, strat if k not in BASELINES else "-"), {"err": [], "act": []})
                r["err"].append(pred - act)
                r["act"].append(act)

    def table():
        rows = []
        for (k, strat), r in res.items():
            e, a = np.stack(r["err"]), np.stack(r["act"])
            m = _metrics(e, a)
            title = MODELS[k]["title"] + (" · direct" if strat == "direct" and len(plan) > 1 else " · step by step" if strat == "recursive" and len(plan) > 1 else "")
            m.update(key=k, strategy=strat, title=title, baseline=k in BASELINES,
                     step_mae=[float(np.nanmean(np.abs(e[:, :, h]))) if np.isfinite(e[:, :, h]).any() else None for h in range(H)])
            rows.append(m)
        rows.sort(key=lambda r: (r["mae"] is None, r["mae"] if r["mae"] is not None else 0))
        return rows

    backtest(plan[0], keys, 0.05, 0.6 if len(plan) > 1 else 0.8)
    rows = table()
    if len(plan) > 1:   # the best model the other way too
        top = model if model != "auto" else next((r["key"] for r in rows if not r["baseline"]), None)
        if top:
            backtest(plan[1], [top], 0.6, 0.8)
            rows = table()
    base = min((r for r in rows if r["baseline"] and r["mae"] is not None), key=lambda r: r["mae"], default=None)
    for r in rows:
        r["skill"] = (1 - r["mae"] / base["mae"]) if base and base["mae"] and r["mae"] is not None else None
    best = rows[0] if model == "auto" else min((r for r in rows if r["key"] == model), key=lambda r: r["mae"] if r["mae"] is not None else np.inf)
    chosen, strat = best["key"], (best["strategy"] if best["strategy"] != "-" else plan[0])
    st = sts[strat]
    Fx, y, tt = data[strat]
    names = _names(st, target)
    if base and best["mae"] is not None and best["key"] != base["key"] and best["mae"] >= base["mae"]:
        warn.append(f"{MODELS[best['key']]['title']} doesn't beat the simple baseline ({base['title']}): the data may be too short or too noisy, or the inputs don't help.")
    width = _bands(np.stack(res[(chosen, best["strategy"])]["err"]))

    # which features matter: permutation importance on the last backtest window (one step ahead, actual history)
    importance = []
    if chosen not in BASELINES:
        progress.update(0.82, "Which inputs matter")
        try:
            from sklearn.inspection import permutation_importance
            o = origins[-1]
            m = _fit(chosen, Fx[tt < o], y[tt < o])
            te = (tt >= o) & (tt < o + H)
            if te.sum() >= 10:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    pi = permutation_importance(m, Fx[te], y[te], n_repeats=3, random_state=0, scoring="neg_mean_absolute_error",
                                                max_samples=min(1.0, 4000 / te.sum()))
                imp = np.maximum(pi.importances_mean, 0)
                groups: dict[str, float] = {}
                for (n, g), v in zip(names, imp):
                    groups[g] = groups.get(g, 0) + float(v)
                tot = sum(groups.values()) or 1
                importance = sorted(({"name": g, "pct": 100 * v / tot, "mae_rise": v} for g, v in groups.items()), key=lambda r: -r["pct"])
        except Exception as e:  # never lose the model because of this extra
            log.info("Feature importance skipped: %s", e)

    progress.update(0.9, f"Training {MODELS[chosen]['title']} on all the data")
    final = _fit(chosen, Fx, y)
    Xf = _future_inputs(X, T, T + H, st["season"], True)
    pred = _forecast(chosen, final, st, Y, Xf, cal_all, P["coords"], W, T, H, lo, hi)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "forecast"
    path = out_dir / f"{stem}.joblib"
    meta = {"version": 1, "kind": "forecast", "name": stem, "model": chosen, "model_title": MODELS[chosen]["title"] + (" · direct" if strat == "direct" and chosen not in BASELINES else ""), "cfg": cfg,
            "setup": st, "freq": P["freq"], "horizon": H, "band": width, "clip": [lo, None],
            "metrics": {k: v for k, v in best.items() if k != "step_mae"}, "compare": [{k: v for k, v in r.items() if k != "step_mae"} for r in rows], "strategy": strat,
            "baseline": {"key": base["key"], "title": base["title"], "mae": base["mae"]} if base else None,
            "importance": importance, "known_inputs": bool(known),
            "trained": {"table": str(table), "series": len(P["ids"]), "rows": int(len(y)), "start": str(P["times"][0]),
                        "end": str(P["times"][T - 1]), "backtests": len(origins)},
            "created": time.strftime("%Y-%m-%d %H:%M")}
    joblib.dump({"meta": meta, "model": final}, path)
    path.with_suffix(".json").write_text(json.dumps(meta, indent=1, default=str))
    rep = _result(P, pred, meta, T, H)
    rep.update(path=str(path), meta=meta, importance=importance, step_mae=best["step_mae"], compare=meta["compare"], seconds=round(time.time() - t0, 1),
               warnings=warn + _input_notes(inputs, X, T, H, known, F), filled=P["filled"])
    return _plain(rep)


def _plain(o):
    """numpy numbers / booleans / NaN → plain JSON values (the result goes to the browser)."""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    return o


def _input_notes(inputs, X, T, H, known, F) -> list[str]:
    if not inputs:
        return []
    have = int(np.isfinite(X[:, :, T:T + H]).all(axis=(0, 1)).sum()) if X.shape[2] > T else 0
    if have >= H:
        return [f"The future values of {', '.join(inputs)} came from the table (rows after the last value, e.g. a weather forecast)."]
    if have:
        return [f"The table has future {', '.join(inputs)} for {have} of the {H} {F['unit']}s; the rest repeat the last {F['unit']}s."]
    return [f"The table has no future values of {', '.join(inputs)}: they repeat the last cycle. Rows after the last value with these "
            f"filled (e.g. a weather forecast: Forecast ▸ Get AQI & weather data) make the forecast better."]


def _result(P, pred, meta, T, H) -> dict:
    """The forecast as table rows and, per series, the recent history and the forecast with its band (for the chart)."""
    F = FREQS[P["freq"]]
    ft = pd.date_range(P["times"][T - 1], periods=H + 1, freq=F["range"])[1:]
    width = meta["band"]
    lo = meta.get("clip", [None])[0]
    rows, series = [], []
    hist = min(T, max(3 * H, F["season"] * 3 if F["season"] else 0, 30))
    fmt = "%Y-%m-%d %H:%M" if P["freq"] == "h" else "%Y-%m-%d"
    for i, sid in enumerate(P["ids"]):
        lat, lon = (float(v) if np.isfinite(v) else None for v in P["coords"][i]) if P["coords"] is not None else (None, None)
        fc = pred[i]
        def band(sign, h):
            e = width[min(h, len(width) - 1)]
            if e is None:
                return None
            v = float(fc[h] + sign * e)
            return max(v, lo) if lo is not None else v
        low = [band(-1, h) for h in range(H)]
        high = [band(1, h) for h in range(H)]
        yh = P["Y"][i, T - hist:T]
        last_i = np.nonzero(np.isfinite(P["Y"][i]))[0]
        stale = bool(last_i.size == 0 or last_i[-1] < T - max(F["season"], H))
        for h in range(H):
            rows.append({"series": sid, "lat": lat, "lon": lon, "time": ft[h].strftime(fmt), "step": h + 1,
                         "forecast": round(float(fc[h]), 3), "low": None if low[h] is None else round(low[h], 3),
                         "high": None if high[h] is None else round(high[h], 3)})
        series.append({"id": sid, "lat": lat, "lon": lon,
                       "hist": [None if not np.isfinite(v) else round(float(v), 3) for v in yh], "fc": [round(float(v), 3) for v in fc],
                       "lo": [None if v is None else round(v, 3) for v in low], "hi": [None if v is None else round(v, 3) for v in high],
                       "last": None if not last_i.size else round(float(P["Y"][i, last_i[-1]]), 3), "stale": stale})
    return {"rows": rows, "series": series, "times": [t.strftime(fmt) for t in ft],
            "hist_times": [t.strftime(fmt) for t in P["times"][T - hist:T]], "freq": P["freq"], "unit": F["unit"], "horizon": H,
            "target": meta["cfg"]["target"], "model_title": meta["model_title"], "last_time": P["times"][T - 1].strftime(fmt)}


# ------------------------------------------------------------------ forecast with a saved model

def load(path) -> dict:
    import joblib
    obj = joblib.load(path)
    if not isinstance(obj, dict) or obj.get("meta", {}).get("kind") != "forecast":
        raise ValueError("Not a forecasting model (made with Forecast ▸ Train forecasting model)")
    return obj


def predict(model_path, table, *, horizon: int | None = None) -> dict:
    """Forecast the next `horizon` steps after the last value in `table` (the same columns as the training table)."""
    obj = load(model_path)
    meta, model = obj["meta"], obj["model"]
    progress.update(0.05, "Reading the table")
    df = _read(table, _cfg_columns(meta["cfg"]))
    P = _panel(df, meta["cfg"], meta["freq"])
    st = dict(meta["setup"])
    F = FREQS[P["freq"]]
    T = P["t_obs"]
    H = int(horizon or meta["horizon"])
    if H < 1 or H > 5000:
        raise ValueError("The horizon must be 1 to 5000 steps")
    W = _neighbours(P["coords"]) if st["use_nb"] else None
    if st["use_nb"] and W is None:
        raise ValueError("This model uses nearby stations: the table needs at least 3 series with latitude / longitude")
    if st["use_coords"] and (P["coords"] is None or not np.isfinite(P["coords"]).all()):
        raise ValueError("This model uses the stations' latitude / longitude: every series needs them")
    if T < max(st["lags"]) // 2:
        log.info("Short history (%d steps): the longer lags are empty", T)
    cal = _calendar(pd.date_range(P["times"][0], periods=max(P["X"].shape[2], T) + H, freq=F["range"]), P["freq"])[0]
    Xf = _future_inputs(P["X"], T, T + H, st["season"], True)
    lo = meta.get("clip", [None])[0]
    progress.update(0.3, f"Forecasting {H} {F['unit']}s")
    pred = _forecast(meta["model"], model, st, P["Y"], Xf, cal, P["coords"], W, T, H, lo, np.inf if lo is not None else None)
    rep = _result(P, pred, meta, T, H)
    warn = []
    if H > meta["horizon"]:
        warn.append(f"The model was checked {meta['horizon']} {F['unit']}s ahead; beyond that the uncertainty band repeats the last checked step and is probably too narrow.")
    stale = [s["id"] for s in rep["series"] if s["stale"]]
    if stale:
        warn.append(f"No recent values for {', '.join(map(str, stale[:5]))}{' …' if len(stale) > 5 else ''}: their forecast is unreliable.")
    rep.update(meta=meta, warnings=warn + _input_notes(meta["cfg"]["inputs"], P["X"], T, H, True, F))
    return _plain(rep)


def save_rows(rows: list[dict], path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def list_models(folder) -> list[dict]:
    out = []
    for p in sorted(Path(folder).glob("*.json"), key=lambda p: -p.stat().st_mtime):
        try:
            m = json.loads(p.read_text())
        except ValueError:
            continue
        if m.get("kind") == "forecast" and p.with_suffix(".joblib").exists():
            out.append({**m, "path": str(p.with_suffix(".joblib"))})
    return out

