"""Rainfall data (Analysis ▸ Hydrology ▸ Rainfall data), free and without an account.

- CHIRPS 2.0 (Funk et al. 2015; 0.05°, about 5.5 km, 1981–now, land between 50° S and 50° N): the total over your dates,
  or the mean annual rainfall over a run of years, on a grid over your area. Only the part of each file over the area
  is read (cloud-optimised GeoTIFFs); whole months use the monthly files, the rest the daily ones.
- Daily series at a place from the ERA5 reanalysis (Open-Meteo archive, 1940–now): rainfall, reference
  evapotranspiration (FAO-56) and mean temperature, as a table. These also feed Streamflow modelling.
- Design storms: the annual maximum 1-, 2-, 3- and 5-day rainfall of every year, fitted with a Gumbel (EV1)
  distribution (method of moments) → the rain expected once in 2, 5, 10, 25, 50 and 100 years, and an SCS Type II
  24-hour storm pattern for the Design flood hydrograph."""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.windows import Window, from_bounds

from .. import progress

CHIRPS_DAILY = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/cogs/p05/{y}/chirps-v2.0.{y}.{m:02d}.{d:02d}.cog"
CHIRPS_MONTHLY = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/cogs/chirps-v2.0.{y}.{m:02d}.cog"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
RETURN_PERIODS = (2, 5, 10, 25, 50, 100)
# SCS Type II 24-hour cumulative fraction of the storm, every hour (NRCS TR-55)
SCS_TYPE_II = [0, .011, .022, .034, .048, .063, .080, .098, .120, .147, .181, .235, .663, .772, .820, .854, .880, .898, .916, .930, .944, .957, .968, .978, 1.0]


def _window(src, bbox):
    w, s, e, n = bbox
    win = from_bounds(w, s, e, n, src.transform).round_offsets(op="floor").round_lengths(op="ceil")
    win = Window(win.col_off, win.row_off, max(1, win.width + 1), max(1, win.height + 1))
    return win.intersection(Window(0, 0, src.width, src.height))


def _read(url: str, bbox) -> tuple[np.ndarray, object]:
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT="90", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2"):
        with rasterio.open("/vsicurl/" + url) as s:
            win = _window(s, bbox)
            a = s.read(1, window=win).astype("float32")
            tr = s.window_transform(win)
    a[a < 0] = np.nan                     # −9999 over the sea
    return a, tr


def _pieces(start: dt.date, end: dt.date) -> list[tuple[str, str]]:
    """The files that make up start … end: whole months as monthly files, the other days as daily ones."""
    out, d = [], start
    while d <= end:
        last = dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        if d.day == 1 and last <= end:
            out.append(("month", CHIRPS_MONTHLY.format(y=d.year, m=d.month)))
            d = last + dt.timedelta(days=1)
        else:
            out.append(("day", CHIRPS_DAILY.format(y=d.year, m=d.month, d=d.day)))
            d += dt.timedelta(days=1)
    return out


def chirps_total(bbox, start: str, end: str, out: Path, *, workers: int = 8) -> dict:
    """CHIRPS rainfall summed over start … end (mm) on its 0.05° grid over the area."""
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if e < s:
        raise ValueError("The end date is before the start")
    if s < dt.date(1981, 1, 1):
        raise ValueError("CHIRPS starts on 1 January 1981")
    pieces = _pieces(s, e)
    if len(pieces) > 800:
        raise ValueError("That makes too many files → choose a shorter period (or whole months)")
    total, tr, done, failed = None, None, 0, []

    def one(p):
        try:
            return _read(p[1], bbox)
        except Exception as ex:
            failed.append(f"{p[1].rsplit('/', 1)[-1]}: {str(ex)[:100]}")
            return None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(one, pieces):
            done += 1
            progress.update(0.05 + 0.9 * done / len(pieces), f"CHIRPS file {done} of {len(pieces)}")
            if r is None:
                continue
            a, t = r
            total = a.copy() if total is None else np.where(np.isfinite(total), total + np.nan_to_num(a), np.nan)   # sea stays empty
            tr = t
    if total is None:
        raise ValueError("CHIRPS could not be read (it covers land between 50° S and 50° N, from 1981; recent days come a few weeks late)")
    if failed:
        raise ValueError(f"{len(failed)} of {len(pieces)} CHIRPS files could not be read (recent dates are published with a delay of a few weeks): {failed[0]}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", width=total.shape[1], height=total.shape[0], count=1, dtype="float32", crs="EPSG:4326",
                       transform=tr, nodata=np.nan, compress="deflate") as d:
        d.write(total, 1)
        d.set_band_description(1, f"Rainfall {start} to {end} (mm, CHIRPS 2.0)")
        d.update_tags(source="CHIRPS 2.0", start=start, end=end)
    v = total[np.isfinite(total)]
    return {"path": str(out), "files": len(pieces), "days": (e - s).days + 1, "mean_mm": round(float(v.mean()), 1),
            "min_mm": round(float(v.min()), 1), "max_mm": round(float(v.max()), 1), "size": [total.shape[1], total.shape[0]]}


def chirps_annual_mean(bbox, first_year: int, last_year: int, out: Path) -> dict:
    """Mean annual rainfall (mm / year) over whole years, from the monthly files."""
    if last_year < first_year:
        raise ValueError("The last year is before the first")
    if last_year - first_year + 1 > 40:
        raise ValueError("Up to 40 years")
    r = chirps_total(bbox, f"{first_year}-01-01", f"{last_year}-12-31", out)
    years = last_year - first_year + 1
    with rasterio.open(r["path"], "r+") as d:
        a = d.read(1) / years
        d.write(a, 1)
        d.set_band_description(1, f"Mean annual rainfall {first_year}–{last_year} (mm / year, CHIRPS 2.0)")
    v = a[np.isfinite(a)]
    return {**r, "years": years, "mean_mm": round(float(v.mean()), 1), "min_mm": round(float(v.min()), 1), "max_mm": round(float(v.max()), 1)}


def daily_series(lon: float, lat: float, start: str, end: str) -> dict:
    """Daily rainfall, reference evapotranspiration and mean temperature at a place (ERA5 via Open-Meteo)."""
    r = requests.get(ARCHIVE, params={"latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
                                      "daily": "precipitation_sum,et0_fao_evapotranspiration,temperature_2m_mean", "timezone": "UTC"},
                     timeout=180, headers={"User-Agent": "LULC-Fetch"})
    if r.status_code != 200:
        raise RuntimeError(f"Open-Meteo archive: {r.text[:200]}")
    d = r.json()["daily"]
    f = lambda k: np.array([np.nan if v is None else v for v in d[k]], float)
    return {"date": d["time"], "rain": f("precipitation_sum"), "et0": f("et0_fao_evapotranspiration"), "temp": f("temperature_2m_mean")}


def gumbel(maxima: np.ndarray, periods=RETURN_PERIODS) -> dict:
    """Gumbel (EV1) return levels by the method of moments."""
    x = maxima[np.isfinite(maxima)]
    if x.size < 10:
        raise ValueError("A frequency analysis needs at least 10 years")
    beta = x.std(ddof=1) * math.sqrt(6) / math.pi
    mu = x.mean() - 0.5772 * beta
    return {T: round(float(mu - beta * math.log(-math.log(1 - 1 / T))), 1) for T in periods}


def design_storms(lon: float, lat: float, first_year: int, last_year: int, out_csv: Path, series_csv: Path | None = None) -> dict:
    """Annual maximum 1-, 2-, 3- and 5-day rainfall of every year and their Gumbel return levels."""
    if last_year - first_year + 1 < 10:
        raise ValueError("Use at least 10 years (30 or more is better)")
    progress.update(0.1, f"Daily rainfall {first_year}–{last_year} from the ERA5 archive")
    s = daily_series(lon, lat, f"{first_year}-01-01", f"{last_year}-12-31")
    rain = np.nan_to_num(s["rain"])
    years = np.array([int(t[:4]) for t in s["date"]])
    out = {}
    for k in (1, 2, 3, 5):
        roll = np.convolve(rain, np.ones(k), "valid")
        yr = years[k - 1:]
        mx = np.array([roll[yr == y].max() for y in range(first_year, last_year + 1) if (yr == y).any()])
        out[k] = {"annual_max": mx, "levels": gumbel(mx)}
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["return_period_years"] + [f"{k}_day_mm" for k in out])
        for T in RETURN_PERIODS:
            wr.writerow([T] + [out[k]["levels"][T] for k in out])
    if series_csv:
        with open(series_csv, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["date", "rain_mm", "et0_mm", "temp_c"])
            for t, a, b, c in zip(s["date"], s["rain"], s["et0"], s["temp"]):
                wr.writerow([t, a, b, c])
    ann = np.array([rain[years == y].sum() for y in range(first_year, last_year + 1)])
    return {"csv": str(out_csv), "series_csv": str(series_csv) if series_csv else None, "years": int(last_year - first_year + 1),
            "mean_annual_mm": round(float(ann.mean()), 1), "levels": {f"{k}_day": out[k]["levels"] for k in out},
            "max_1day_mm": round(float(out[1]["annual_max"].max()), 1)}


def scs_hyetograph(total_mm: float, step_h: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """A 24-hour SCS Type II storm of total_mm: (hours, rain in each step mm)."""
    hrs = np.arange(0, 24 + 1e-9, step_h)
    cum = np.interp(hrs, np.arange(25), SCS_TYPE_II) * total_mm
    return hrs[1:], np.diff(cum)


def write_series(series: dict, path: Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["date", "rain_mm", "et0_mm", "temp_c"])
        for row in zip(series["date"], series["rain"], series["et0"], series["temp"]):
            wr.writerow(row)
    return str(path)

