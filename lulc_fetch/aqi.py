"""Live air quality from CPCB (India's Central Pollution Control Board): the public feed of every continuous monitoring
station (CAAQMS) with its position, the hour of the reading, the AQI and each pollutant's AQI sub-index.
No key or account needed. Readings are hourly; a station that is offline is missing from the feed.

    from lulc_fetch.aqi import cpcb_live
    rows = cpcb_live("Bengaluru")            # list of dicts: station, lat, lon, aqi, main_pollutant, pm25_index, pm10_index …
"""

from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

FEED = "https://airquality.cpcb.gov.in/caaqms/rss_feed"
POLLUTANTS = {"PM2.5": "pm25_index", "PM10": "pm10_index", "NO2": "no2_index", "SO2": "so2_index", "CO": "co_index", "OZONE": "o3_index",
              "NH3": "nh3_index"}   # each pollutant's AQI sub-index (the feed's "Avg"); the AQI is the highest of them


def cpcb_live(city: str | None = None, state: str | None = None) -> list[dict]:
    """The latest hourly reading of every station (of one city / state, matched case-insensitively; Bangalore = Bengaluru)."""
    import requests
    r = requests.get(FEED, timeout=60, headers={"User-Agent": "Mozilla/5.0 LULC-Fetch"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    alias = {"bangalore": "bengaluru", "bombay": "mumbai", "madras": "chennai", "calcutta": "kolkata"}
    want = alias.get((city or "").lower(), (city or "").lower())
    out = []
    for st in root.iter("State"):
        if state and st.get("id", "").lower() != state.lower():
            continue
        for c in st.iter("City"):
            cname = c.get("id", "")
            if want and alias.get(cname.lower(), cname.lower()) != want:
                continue
            for s in c.iter("Station"):
                row = {"station": s.get("id"), "city": cname, "state": st.get("id"), "lat": float(s.get("latitude")),
                       "lon": float(s.get("longitude")), "time": s.get("lastupdate")}
                aqi = s.find("Air_Quality_Index")
                row["aqi"] = int(aqi.get("Value")) if aqi is not None and (aqi.get("Value") or "").isdigit() else None
                row["main_pollutant"] = aqi.get("Predominant_Parameter") if aqi is not None else None
                for p in s.iter("Pollutant_Index"):
                    k = POLLUTANTS.get(p.get("id", "").upper())
                    if k:
                        avg = p.get("Avg")
                        row[k] = float(avg) if avg not in (None, "", "NA") else None
                out.append(row)
    return out


def save_csv(rows: list[dict], path: str | Path) -> Path:
    cols = ["station", "lat", "lon", "aqi", "main_pollutant", *POLLUTANTS.values(), "time", "city", "state"]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path
