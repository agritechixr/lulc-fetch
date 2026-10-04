"""Hourly air quality and weather for any points, free and without a key, from Open-Meteo (open-meteo.com; free for
non-commercial use, please credit it):

* air quality: CAMS (Copernicus Atmosphere Monitoring Service) global model, about 40 km cells: PM2.5, PM10, NO2, SO2,
  CO, O3 and dust, for up to 92 past days. Model values, not station measurements: nearby points can share a cell.
  The Indian AQI (CPCB method) is computed from them: 24-hour averages for PM / NO2 / SO2, 8-hour for CO and O3.
* weather: ECMWF / national weather models (about 9–11 km): temperature, humidity, wind, rain, cloud, pressure,
  sunshine, for up to 92 past days and the next 1–16 days (a real weather forecast, so a forecasting model can use it
  as known future inputs).

    from lulc_fetch.openmeteo import history
    rows = history([{"name": "Peenya", "lat": 13.03, "lon": 77.51}], past_days=60, forecast_days=7)
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import progress

log = logging.getLogger(__name__)

AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR = {"pm2_5": "pm2_5", "pm10": "pm10", "nitrogen_dioxide": "no2", "sulphur_dioxide": "so2", "carbon_monoxide": "co",
       "ozone": "o3", "dust": "dust"}                       # Open-Meteo name → column (all µg/m³)
WEATHER = {"temperature_2m": "temperature", "relative_humidity_2m": "humidity", "dew_point_2m": "dew_point",
           "precipitation": "rain", "cloud_cover": "cloud_cover", "surface_pressure": "pressure",
           "wind_speed_10m": "wind_speed", "wind_direction_10m": "wind_direction", "sunshine_duration": "sunshine"}
UNITS = {"pm2_5": "µg/m³", "pm10": "µg/m³", "no2": "µg/m³", "so2": "µg/m³", "co": "µg/m³", "o3": "µg/m³", "dust": "µg/m³",
         "aqi": "Indian AQI (CPCB)", "temperature": "°C", "humidity": "%", "dew_point": "°C", "rain": "mm", "cloud_cover": "%",
         "pressure": "hPa", "wind_speed": "km/h", "wind_direction": "°", "sunshine": "minutes in the hour"}

# CPCB breakpoints (concentration → AQI 0, 50, 100, 200, 300, 400, 500); the last band is open-ended, capped here
_BP = {"pm10": [0, 50, 100, 250, 350, 430, 600], "pm2_5": [0, 30, 60, 90, 120, 250, 380], "no2": [0, 40, 80, 180, 280, 400, 600],
       "so2": [0, 40, 80, 380, 800, 1600, 2400], "co": [0, 1, 2, 10, 17, 34, 50], "o3": [0, 50, 100, 168, 208, 748, 1000]}
_AQI = [0, 50, 100, 200, 300, 400, 500]
_HOURS = {"pm10": 24, "pm2_5": 24, "no2": 24, "so2": 24, "co": 8, "o3": 8}   # averaging period of each pollutant


def sub_index(pollutant: str, conc: np.ndarray) -> np.ndarray:
    """CPCB sub-index (0–500) of a concentration (µg/m³; CO in mg/m³), linear within each band."""
    return np.interp(conc, _BP[pollutant], _AQI, right=500.0)


def indian_aqi(series: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Hourly Indian AQI of one place from hourly concentrations (µg/m³): each pollutant averaged over its period
    (24 h, or 8 h for CO / O3; at least 3/4 of the hours present), its sub-index, and the highest of them. Like CPCB, an
    AQI needs PM2.5 or PM10 and at least three pollutants. Returns the AQI and the main pollutant (as names)."""
    subs = {}
    for k, hours in _HOURS.items():
        v = series.get(k)
        if v is None:
            continue
        v = np.asarray(v, dtype="float64") / (1000.0 if k == "co" else 1.0)   # CO in mg/m³
        ok = np.isfinite(v)
        c = np.convolve(np.where(ok, v, 0.0), np.ones(hours), "full")[: len(v)]
        n = np.convolve(ok.astype(float), np.ones(hours), "full")[: len(v)]
        mean = np.where(n >= 0.75 * hours, c / np.maximum(n, 1), np.nan)
        mean[: hours - 1] = np.nan                                          # not a full period yet
        subs[k] = np.where(np.isfinite(mean), sub_index(k, np.maximum(mean, 0)), np.nan)
    if not subs:
        return np.array([]), np.array([])
    names = list(subs)
    m = np.vstack([subs[k] for k in names])
    count = np.isfinite(m).sum(0)
    has_pm = np.zeros(m.shape[1], bool)
    for k in ("pm2_5", "pm10"):
        if k in subs:
            has_pm |= np.isfinite(subs[k])
    ok = has_pm & (count >= 3)
    best = np.argmax(np.where(np.isfinite(m), m, -1), axis=0)
    aqi = np.where(ok, np.round(np.nanmax(np.where(np.isfinite(m), m, -1), axis=0)), np.nan)
    label = {"pm2_5": "PM2.5", "pm10": "PM10", "no2": "NO2", "so2": "SO2", "co": "CO", "o3": "O3"}
    return aqi, np.array([label[names[i]] if o else "" for i, o in zip(best, ok)], dtype=object)


def _get(url: str, params: dict) -> list[dict]:
    import requests
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=90, headers={"User-Agent": "LULC-Fetch"})
            if r.status_code == 400:
                raise ValueError(f"Open-Meteo: {r.json().get('reason', r.text)[:200]}")
            r.raise_for_status()
            out = r.json()
            return out if isinstance(out, list) else [out]
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise ConnectionError("Open-Meteo didn't answer: check the internet connection and try again") from None
    return []


def history(points: list[dict], *, past_days: int = 60, forecast_days: int = 7, air: bool = True, weather: bool = True,
            tz: str = "auto") -> list[dict]:
    """One row per point and hour: name, lat, lon, time (local), the air columns (+ aqi, main_pollutant) up to now and
    the weather columns for the past days and the forecast days (rows after now have weather only)."""
    if not points:
        raise ValueError("No points")
    if not (air or weather):
        raise ValueError("Choose air quality, weather or both")
    past_days = int(max(1, min(92, past_days)))
    forecast_days = int(max(0, min(16, forecast_days)))
    rows: dict[tuple, dict] = {}
    chunks = [points[i:i + 50] for i in range(0, len(points), 50)]   # several places per request
    total = len(chunks) * (int(air) + int(weather))
    done = 0
    now = None
    for chunk in chunks:
        loc = {"latitude": ",".join(f"{p['lat']:.4f}" for p in chunk), "longitude": ",".join(f"{p['lon']:.4f}" for p in chunk),
               "timezone": tz, "past_days": past_days}
        parts = []
        if air:
            parts.append((AIR_URL, AIR, {**loc, "hourly": ",".join(AIR), "forecast_days": 1}))
        if weather:
            parts.append((WEATHER_URL, WEATHER, {**loc, "hourly": ",".join(WEATHER), "forecast_days": max(1, forecast_days)}))
        for url, names, params in parts:
            progress.update(done / max(total, 1), f"Open-Meteo: {'air quality' if url == AIR_URL else 'weather'} for {len(chunk)} place{'s' if len(chunk) > 1 else ''}")
            res = _get(url, params)
            done += 1
            for p, r in zip(chunk, res):
                h = r.get("hourly") or {}
                times = h.get("time") or []
                if now is None:   # the current hour where the places are
                    off = r.get("utc_offset_seconds", 0)
                    now = (datetime.now(timezone.utc) + timedelta(seconds=off)).strftime("%Y-%m-%dT%H:00")
                vals = {col: np.array([np.nan if v is None else v for v in h.get(src, [None] * len(times))], dtype="float64")
                        for src, col in names.items()}
                if url == AIR_URL:
                    keep = np.array([t <= now for t in times])
                    vals = {k: np.where(keep, v, np.nan) for k, v in vals.items()}
                    aqi, main = indian_aqi(vals)
                    vals["aqi"] = aqi
                for i, t in enumerate(times):
                    if url == AIR_URL and t > now:
                        continue
                    if url == WEATHER_URL and forecast_days == 0 and t > now:
                        continue
                    row = rows.setdefault((p["name"], t), {"name": p["name"], "lat": p["lat"], "lon": p["lon"], "time": t})
                    for k, v in vals.items():
                        row[k] = None if not np.isfinite(v[i]) else round(float(v[i]), 2)
                    if url == AIR_URL:
                        row["main_pollutant"] = main[i] or None
    out = sorted(rows.values(), key=lambda r: (r["name"], r["time"]))
    log.info("Open-Meteo: %d rows for %d places", len(out), len(points))
    return out


def save_csv(rows: list[dict], path: str | Path) -> Path:
    first = ["name", "lat", "lon", "time", "aqi", "main_pollutant", *AIR.values(), *WEATHER.values()]
    present = {k for r in rows for k in r}
    cols = [c for c in first if c in present]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path
