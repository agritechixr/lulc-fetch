"""Streamflow modelling (Analysis ▸ Hydrology ▸ Streamflow modelling): daily river flow from rainfall and evaporation,
learned from a record of observed flow, with three kinds of model compared on years they never saw.

- GR4J (Perrin, Michel & Andréassian 2003): the standard four-parameter conceptual rainfall–runoff model (a production
  store, groundwater exchange, a routing store and two unit hydrographs), calibrated on the KGE by a global search
  (differential evolution) and a local polish.
- Machine learning: LightGBM (or a Random Forest) on the rainfall of the last days, its running sums over 3 … 180 days,
  evaporation, temperature and the season; optionally yesterday's observed flow (a one-day-ahead forecast, not a
  simulation).
- Deep learning: an LSTM over the last 120 days of rainfall, evaporation and temperature (Kratzert et al. 2018), trained in
  the deep-learning helper process when the add-on is installed.

Forcing comes from the flow table's own columns (rain, PET) or, by default, from the ERA5 archive (Open-Meteo) at the
catchment's centre. Flow in m³/s is turned into mm/day with the catchment area. The first year warms the models up; the
rest is split in time: calibration (default the first 70 %) and validation (the rest). Scores: NSE, KGE, percent
bias, RMSE, and the same for the day-of-year climatology as a baseline to beat."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

from .. import progress

GR4J_BOUNDS = [(1.0, 2500.0), (-10.0, 5.0), (1.0, 600.0), (0.5, 6.0)]


# ------------------------------------------------------------------ GR4J
def _uh(x4: float) -> tuple[np.ndarray, np.ndarray]:
    def s1(t):
        return 0.0 if t <= 0 else 1.0 if t >= x4 else (t / x4) ** 2.5

    def s2(t):
        if t <= 0:
            return 0.0
        if t < x4:
            return 0.5 * (t / x4) ** 2.5
        if t < 2 * x4:
            return 1 - 0.5 * (2 - t / x4) ** 2.5
        return 1.0
    n1, n2 = int(math.ceil(x4)), int(math.ceil(2 * x4))
    uh1 = np.array([s1(i) - s1(i - 1) for i in range(1, n1 + 1)])
    uh2 = np.array([s2(i) - s2(i - 1) for i in range(1, n2 + 1)])
    return uh1, uh2


def gr4j(P: np.ndarray, E: np.ndarray, x, s0: float = 0.5, r0: float = 0.5) -> np.ndarray:
    """Daily flow (mm/day) of GR4J for rain P and potential evaporation E (mm/day)."""
    x1, x2, x3, x4 = map(float, x)
    uh1, uh2 = _uh(x4)
    n1, n2 = len(uh1), len(uh2)
    q1, q2 = [0.0] * n1, [0.0] * n2
    S, R = s0 * x1, r0 * x3
    out = np.empty(len(P))
    Pl, El = P.tolist(), E.tolist()
    u1, u2 = uh1.tolist(), uh2.tolist()
    for t in range(len(Pl)):
        p, e = Pl[t], El[t]
        if p >= e:
            pn, en = p - e, 0.0
            ts = math.tanh(min(pn / x1, 13))
            ps = x1 * (1 - (S / x1) ** 2) * ts / (1 + S / x1 * ts)
            es = 0.0
        else:
            pn, en = 0.0, e - p
            ts = math.tanh(min(en / x1, 13))
            es = S * (2 - S / x1) * ts / (1 + (1 - S / x1) * ts)
            ps = 0.0
        S = S - es + ps
        perc = S * (1 - (1 + (4 / 9 * S / x1) ** 4) ** -0.25)
        S -= perc
        pr = perc + pn - ps
        for i in range(n1):
            q1[i] += 0.9 * pr * u1[i]
        for i in range(n2):
            q2[i] += 0.1 * pr * u2[i]
        a, b = q1.pop(0), q2.pop(0)
        q1.append(0.0)
        q2.append(0.0)
        F = x2 * (R / x3) ** 3.5
        R = max(0.0, R + a + F)
        qr = R * (1 - (1 + (R / x3) ** 4) ** -0.25)
        R -= qr
        qd = max(0.0, b + F)
        out[t] = qr + qd
    return out


# ------------------------------------------------------------------ scores
def scores(obs: np.ndarray, sim: np.ndarray) -> dict:
    m = np.isfinite(obs) & np.isfinite(sim)
    o, s = obs[m], sim[m]
    if o.size < 10 or o.std() == 0:
        return {"nse": None, "kge": None, "pbias": None, "rmse": None, "n": int(o.size)}
    nse = 1 - ((s - o) ** 2).sum() / ((o - o.mean()) ** 2).sum()
    r = np.corrcoef(o, s)[0, 1] if s.std() > 0 else 0.0
    kge = 1 - math.sqrt((r - 1) ** 2 + (s.std() / o.std() - 1) ** 2 + (s.mean() / o.mean() - 1) ** 2)
    return {"nse": round(float(nse), 3), "kge": round(float(kge), 3), "pbias": round(float(100 * (s.sum() - o.sum()) / o.sum()), 2),
            "rmse": round(float(np.sqrt(((s - o) ** 2).mean())), 4), "n": int(o.size)}


def calibrate_gr4j(P, E, Q, cal: np.ndarray, warm: int, seed: int = 0, maxiter: int = 25) -> tuple[list[float], float]:
    from scipy.optimize import differential_evolution, minimize
    m = cal & np.isfinite(Q)
    m[:warm] = False
    o = Q[m]

    def loss(x):
        s = gr4j(P, E, x)[m]
        if s.std() == 0:
            return 10.0
        r = np.corrcoef(o, s)[0, 1]
        return math.sqrt((r - 1) ** 2 + (s.std() / o.std() - 1) ** 2 + (s.mean() / o.mean() - 1) ** 2)
    calls = {"n": 0}

    def cb(xk, convergence=None):
        calls["n"] += 1
        progress.update(0.15 + 0.4 * min(1, calls["n"] / maxiter), f"Calibrating GR4J: round {calls['n']} of {maxiter}")
    de = differential_evolution(loss, GR4J_BOUNDS, maxiter=maxiter, popsize=6, tol=1e-4, seed=seed, polish=False, callback=cb, init="latinhypercube")
    loc = minimize(loss, de.x, method="Nelder-Mead", options={"maxiter": 200, "xatol": 1e-2, "fatol": 1e-4})
    best = loc.x if loc.fun < de.fun else de.x
    best = [float(np.clip(v, lo, hi)) for v, (lo, hi) in zip(best, GR4J_BOUNDS)]
    return best, 1 - float(min(loc.fun, de.fun))


# ------------------------------------------------------------------ machine learning
def features(P, E, T, doy, Qprev=None) -> tuple[np.ndarray, list[str]]:
    cols, names = [], []
    for k in range(0, 8):
        cols.append(np.r_[np.zeros(k), P[:len(P) - k]])
        names.append(f"rain_lag{k}")
    cs = np.r_[0, np.cumsum(P)]
    for w in (3, 7, 14, 30, 60, 90, 180):
        idx = np.arange(len(P))
        cols.append(cs[idx + 1] - cs[np.maximum(idx + 1 - w, 0)])
        names.append(f"rain_sum{w}")
    ce = np.r_[0, np.cumsum(E)]
    for w in (7, 30):
        idx = np.arange(len(E))
        cols.append(ce[idx + 1] - ce[np.maximum(idx + 1 - w, 0)])
        names.append(f"pet_sum{w}")
    api = np.zeros(len(P))                                  # antecedent precipitation index (k = 0.9)
    for t in range(1, len(P)):
        api[t] = 0.9 * api[t - 1] + P[t]
    cols += [api, T, np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)]
    names += ["api", "temp", "doy_sin", "doy_cos"]
    if Qprev is not None:
        cols.append(Qprev)
        names.append("flow_yesterday")
    return np.column_stack(cols), names


def fit_ml(X, Q, cal, warm, model: str = "lgbm", seed: int = 0):
    m = cal & np.isfinite(Q)
    m[:warm] = False
    if model == "lgbm":
        from lightgbm import LGBMRegressor
        est = LGBMRegressor(n_estimators=600, learning_rate=0.03, num_leaves=31, min_child_samples=20, subsample=0.8, subsample_freq=1,
                            colsample_bytree=0.8, random_state=seed, verbose=-1)
    else:
        from sklearn.ensemble import RandomForestRegressor
        est = RandomForestRegressor(n_estimators=300, min_samples_leaf=3, random_state=seed, n_jobs=-1)
    est.fit(X[m], np.log1p(np.maximum(Q[m], 0)))           # log flow: highs and lows both count
    return est, np.expm1(est.predict(X)).clip(0)


# ------------------------------------------------------------------ LSTM (runs in the deep-learning helper process)
def train_lstm(npz: str, out_npz: str, seq: int = 120, epochs: int = 40, hidden: int = 48, seed: int = 0) -> dict:
    import torch
    from torch import nn
    torch.manual_seed(seed)
    d = np.load(npz)
    X, Q, cal, warm = d["X"].astype("float32"), d["Q"].astype("float32"), d["cal"].astype(bool), int(d["warm"])
    mu, sd = X[cal].mean(0), X[cal].std(0) + 1e-6
    Xn = (X - mu) / sd
    y = np.log1p(np.maximum(Q, 0))
    ym, ys = np.nanmean(y[cal]), np.nanstd(y[cal]) + 1e-6
    yn = (y - ym) / ys
    idx = np.array([t for t in range(max(seq, warm), len(X)) if cal[t] and np.isfinite(yn[t])])
    if idx.size < 100:
        raise ValueError("Too few days with observed flow to train an LSTM (at least 100 after the warm-up)")
    rng = np.random.default_rng(seed)
    rng.shuffle(idx)
    nval = max(20, idx.size // 10)
    val, trn = idx[:nval], idx[nval:]

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(X.shape[1], hidden, batch_first=True)
            self.drop = nn.Dropout(0.2)
            self.head = nn.Linear(hidden, 1)

        def forward(self, x):
            h, _ = self.lstm(x)
            return self.head(self.drop(h[:, -1])).squeeze(-1)
    dev = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    net = Net().to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=2e-3)
    Xt = torch.tensor(Xn)

    def batch(ix):
        xb = torch.stack([Xt[t - seq + 1:t + 1] for t in ix]).to(dev)
        return xb, torch.tensor(yn[ix], dtype=torch.float32).to(dev)
    best, best_state, bad = 1e9, None, 0
    for ep in range(epochs):
        net.train()
        rng.shuffle(trn)
        for b in range(0, trn.size, 256):
            xb, yb = batch(trn[b:b + 256])
            opt.zero_grad()
            loss = ((net(xb) - yb) ** 2).mean()
            loss.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            vl = float(np.mean([((net(xb) - yb) ** 2).mean().item() for xb, yb in (batch(val[b:b + 512]) for b in range(0, val.size, 512))]))
        progress.update(0.1 + 0.8 * (ep + 1) / epochs, f"LSTM epoch {ep + 1} of {epochs}: validation loss {vl:.3f}")
        if vl < best - 1e-4:
            best, bad = vl, 0
            best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= 8:
                break
    net.load_state_dict(best_state)
    net.eval()
    sim = np.full(len(X), np.nan, "float32")
    allx = np.arange(seq - 1, len(X))
    with torch.no_grad():
        for b in range(0, allx.size, 512):
            ix = allx[b:b + 512]
            xb = torch.stack([Xt[t - seq + 1:t + 1] for t in ix]).to(dev)
            sim[ix] = net(xb).cpu().numpy()
    sim = np.expm1(sim * ys + ym).clip(0)
    np.savez(out_npz, sim=sim)
    return {"epochs": ep + 1, "val_loss": round(best, 4), "device": dev}


# ------------------------------------------------------------------ the tool
def read_flow(path: str, date_col: str | None, flow_col: str | None) -> tuple[list[str], np.ndarray, dict]:
    import pandas as pd
    p = Path(path)
    df = pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xls") else pd.read_parquet(p) if p.suffix.lower() == ".parquet" else pd.read_csv(p)
    date_col = date_col or next((c for c in df.columns if "date" in str(c).lower() or "time" in str(c).lower()), df.columns[0])
    if not flow_col:
        cands = [c for c in df.columns if c != date_col and any(w in str(c).lower() for w in ("flow", "discharge", "q", "runoff"))]
        num = [c for c in df.columns if c != date_col and pd.api.types.is_numeric_dtype(df[c])]
        flow_col = (cands or num or [None])[0]
    if flow_col is None:
        raise ValueError("The table needs a column of observed flow")
    df["_d"] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
    df = df.dropna(subset=["_d"]).groupby("_d").mean(numeric_only=True).sort_index()
    full = pd.date_range(df.index.min(), df.index.max(), freq="D")
    df = df.reindex(full)
    extra = {c.lower(): df[c].to_numpy(float) for c in df.columns if c.lower() in ("rain", "rain_mm", "precipitation", "pet", "et0", "pet_mm", "temp", "temperature")}
    return [d.strftime("%Y-%m-%d") for d in full], df[flow_col].to_numpy(float), extra


def run(table: str, out_dir: Path, *, area_km2: float | None = None, lon: float | None = None, lat: float | None = None,
        date_col: str | None = None, flow_col: str | None = None, units: str = "m3s", cal_frac: float = 0.7,
        models=("gr4j", "lgbm"), past_flow: bool = False, lstm_runner=None, name: str = "streamflow") -> dict:
    from . import rainfall
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress.update(0.02, "Reading the flow record")
    dates, Qraw, extra = read_flow(table, date_col, flow_col)
    if np.isfinite(Qraw).sum() < 365:
        raise ValueError("Use at least a year of daily flow (several years is much better)")
    if units == "m3s":
        if not area_km2:
            raise ValueError("Flow in m³/s needs the catchment area (km²)")
        Q = Qraw * 86.4 / area_km2
    else:
        Q = Qraw.copy()
    rain = next((extra[k] for k in ("rain", "rain_mm", "precipitation") if k in extra), None)
    pet = next((extra[k] for k in ("pet", "et0", "pet_mm") if k in extra), None)
    temp = next((extra[k] for k in ("temp", "temperature") if k in extra), None)
    source = "the table"
    if rain is None or pet is None:
        if lon is None or lat is None:
            raise ValueError("The table has no rain / PET columns: give the catchment's location to fetch them (ERA5)")
        progress.update(0.05, "Rainfall and evaporation from the ERA5 archive")
        f = rainfall.daily_series(lon, lat, dates[0], dates[-1])
        rain = f["rain"] if rain is None else rain
        pet = f["et0"] if pet is None else pet
        temp = f["temp"] if temp is None else temp
        source = "ERA5 (Open-Meteo)"
    P, E = np.nan_to_num(rain), np.nan_to_num(pet)
    T = np.nan_to_num(temp) if temp is not None else np.zeros(len(P))
    n = len(Q)
    warm = min(365, n // 5)
    obs_idx = np.flatnonzero(np.isfinite(Q))
    split_day = obs_idx[warm:][int(cal_frac * len(obs_idx[warm:]))] if len(obs_idx) > warm + 50 else n
    cal = np.zeros(n, bool)
    cal[:split_day] = True
    val = ~cal
    import datetime as dt
    doy = np.array([dt.date.fromisoformat(d).timetuple().tm_yday for d in dates], float)
    sims, info = {}, {}
    # baseline: day-of-year mean of the calibration years
    clim = np.array([np.nanmean(Q[cal & (np.abs(doy - d) <= 7)]) if np.isfinite(Q[cal & (np.abs(doy - d) <= 7)]).any() else np.nan for d in doy])
    sims["climatology"] = clim
    if "gr4j" in models:
        x, _ = calibrate_gr4j(P, E, Q, cal, warm)
        sims["gr4j"] = gr4j(P, E, x)
        info["gr4j_params"] = {"x1_production_mm": round(x[0], 2), "x2_exchange_mm": round(x[1], 3), "x3_routing_mm": round(x[2], 2), "x4_unit_hydrograph_days": round(x[3], 3)}
    for mdl in ("lgbm", "rf"):
        if mdl in models:
            progress.update(0.6, f"Training {'LightGBM' if mdl == 'lgbm' else 'Random Forest'}")
            qprev = np.r_[np.nan, Q[:-1]] if past_flow else None
            X, names = features(P, E, T, doy, qprev)
            est, sim = fit_ml(X, Q, cal, warm, mdl)
            sims[mdl] = sim
            imp = getattr(est, "feature_importances_", None)
            if imp is not None:
                order = np.argsort(-imp)[:6]
                info[f"{mdl}_top_inputs"] = [names[i] for i in order]
    if "lstm" in models:
        if lstm_runner is None:
            raise ValueError("The LSTM needs the deep-learning add-on")
        progress.update(0.65, "Training the LSTM (deep-learning helper)")
        npz = out_dir / "_lstm_in.npz"
        np.savez(npz, X=np.column_stack([P, E, T, np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)]), Q=Q, cal=cal, warm=warm)
        out_npz = out_dir / "_lstm_out.npz"
        info["lstm"] = lstm_runner(str(npz), str(out_npz))
        sims["lstm"] = np.load(out_npz)["sim"].astype(float)
        npz.unlink(missing_ok=True)
        out_npz.unlink(missing_ok=True)
    titles = {"climatology": "Day-of-year mean (baseline)", "gr4j": "GR4J", "lgbm": "LightGBM", "rf": "Random Forest", "lstm": "LSTM"}
    table_rows = []
    for k, s in sims.items():
        c = cal.copy()
        c[:warm] = False
        table_rows.append({"model": titles[k], **{f"cal_{m}": v for m, v in scores(Q[c], s[c]).items() if m != "n"},
                           **{f"val_{m}": v for m, v in scores(Q[val], s[val]).items() if m != "n"}})
    best = max((r for r in table_rows if r["model"] != titles["climatology"] and r["val_kge"] is not None), key=lambda r: r["val_kge"], default=None)
    series = out_dir / f"{name}_simulated.csv"
    with open(series, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        unit = "mm_day"
        wr.writerow(["date", "period", f"observed_{unit}"] + [f"{k}_{unit}" for k in sims] + (["observed_m3s"] + [f"{k}_m3s" for k in sims] if area_km2 else []) + ["rain_mm", "pet_mm"])
        for t in range(n):
            row = [dates[t], "warm-up" if t < warm else "calibration" if cal[t] else "validation", _r(Q[t])] + [_r(s[t]) for s in sims.values()]
            if area_km2:
                row += [_r(Q[t] * area_km2 / 86.4)] + [_r(s[t] * area_km2 / 86.4) for s in sims.values()]
            wr.writerow(row + [_r(P[t]), _r(E[t])])
    metrics = out_dir / f"{name}_scores.csv"
    with open(metrics, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(table_rows[0]))
        wr.writeheader()
        wr.writerows(table_rows)
    progress.update(1.0, "Done")
    return {"csv": str(series), "scores_csv": str(metrics), "scores": table_rows, "best": best["model"] if best else None, "forcing": source,
            "days": n, "calibration_until": dates[split_day - 1] if split_day < n else dates[-1], "warm_up_days": warm, **info}


def _r(v):
    return "" if v is None or not np.isfinite(v) else round(float(v), 4)
