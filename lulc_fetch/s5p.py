"""Sentinel-5P air quality (Analysis ▸ Forecast ▸ Sentinel-5P): NO₂, CO, SO₂, formaldehyde, ozone, methane and the UV
aerosol index from TROPOMI Level-2, averaged over a period on a regular grid over your area, with a daily series.

The Level-2 files (Microsoft Planetary Computer, free, no account) are read over the internet with GDAL's HDF5 driver:
only the rows over the area are fetched, never the whole orbit. Pixels below the product's recommended quality (qa_value)
are dropped, as ESA advises; each pixel's centre goes into the grid cell it falls in (mean of all good pixels)."""

from __future__ import annotations

import csv
import json
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from . import progress

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-5p-l2-netcdf"
# product: (variable in /PRODUCT, title, unit, factor from mol/m² (or the file's unit) to the unit, minimum qa_value)
PRODUCTS = {
    "no2": ("nitrogendioxide_tropospheric_column", "NO₂ tropospheric column", "µmol/m²", 1e6, 0.75),
    "co": ("carbonmonoxide_total_column", "CO total column", "mmol/m²", 1e3, 0.5),
    "so2": ("sulfurdioxide_total_vertical_column", "SO₂ total column", "µmol/m²", 1e6, 0.5),
    "hcho": ("formaldehyde_tropospheric_vertical_column", "Formaldehyde (HCHO) tropospheric column", "µmol/m²", 1e6, 0.5),
    "o3": ("ozone_total_vertical_column", "O₃ total column", "DU", 2241.15, 0.5),
    "ch4": ("methane_mixing_ratio_bias_corrected", "CH₄ column mixing ratio", "ppb", 1.0, 0.5),
    "aer-ai": ("aerosol_index_354_388", "UV aerosol index (354/388 nm)", "", 1.0, 0.8),
}
MAX_ITEMS = 120


def _vs(href: str) -> str:
    return "/vsicurl?use_head=no&max_retry=3&retry_delay=2&url=" + urllib.parse.quote(href, safe="")


# one environment per overpass: the HTTP block cache lets the second and later variables reuse the file's metadata
GDAL_OPTS = dict(GDAL_SKIP="netCDF", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT="60", VSI_CACHE="TRUE",
                 VSI_CACHE_SIZE=str(64 * 2 ** 20), CPL_VSIL_CURL_CHUNK_SIZE=str(2 ** 20), GDAL_HTTP_MULTIPLEX="YES")


def _read(href: str, var: str, window: Window | None = None) -> np.ndarray:
    with rasterio.open(f'HDF5:"{_vs(href)}"://PRODUCT/{var}') as s:
        a = s.read(1, window=window).astype("float64")
        tags = s.tags(1) or s.tags()
    fill = None
    for k in ("_FillValue", f"{var}__FillValue"):
        if k in tags:
            try:
                fill = float(str(tags[k]).split()[0].strip("{}"))
            except ValueError:
                pass
    sc = next((float(tags[k]) for k in tags if k.endswith("scale_factor")), None)
    of = next((float(tags[k]) for k in tags if k.endswith("add_offset")), None)
    if fill is not None:
        a[a == fill] = np.nan
    a[np.abs(a) > 1e30] = np.nan
    if sc is not None and var == "qa_value":
        a = a * sc + (of or 0)
    return a


def search(bbox, start: str, end: str, product: str) -> list:
    """Level-2 items of the product over bbox (west, south, east, north) between the dates; one per orbit (the offline
    product where there is one, else near-real-time)."""
    import planetary_computer
    import pystac_client
    if product not in PRODUCTS:
        raise ValueError(f"product: {', '.join(PRODUCTS)}")
    c = pystac_client.Client.open(STAC, modifier=planetary_computer.sign_inplace)
    items = list(c.search(collections=[COLLECTION], bbox=list(bbox), datetime=f"{start}/{end}",
                          query={"s5p:product_name": {"eq": product}}, max_items=MAX_ITEMS * 3).items())
    best: dict[str, object] = {}
    rank = {"OFFL": 0, "RPRO": 0, "NRTI": 1}
    for it in items:
        orbit = str(it.properties.get("sat:absolute_orbit") or it.id.split("_")[-1])
        mode = it.properties.get("s5p:processing_mode", "NRTI")
        cur = best.get(orbit)
        if cur is None or rank.get(mode, 2) < rank.get(cur.properties.get("s5p:processing_mode", "NRTI"), 2):
            best[orbit] = it
    out = sorted(best.values(), key=lambda i: i.datetime)
    if len(out) > MAX_ITEMS:
        raise ValueError(f"{len(out)} overpasses in that period → choose a shorter period (up to {MAX_ITEMS})")
    return out


def _granule(it, product: str, bbox) -> tuple[str, np.ndarray, np.ndarray, np.ndarray] | None:
    var, _, _, factor, qa_min = PRODUCTS[product]
    href = next(iter(it.assets.values())).href
    w, s, e, n = bbox
    with rasterio.Env(**GDAL_OPTS):
        lat = _read(href, "latitude")
        rows = np.flatnonzero(((lat >= s - 0.5) & (lat <= n + 0.5)).any(1))
        if not rows.size:
            return None
        r0, r1 = int(rows.min()), int(rows.max()) + 1
        win = Window(0, r0, lat.shape[1], r1 - r0)
        lat = lat[r0:r1]
        lon = _read(href, "longitude", win)
        if not ((lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)).any():
            return None
        v = _read(href, var, win) * factor
        qa = _read(href, "qa_value", win)
    ok = np.isfinite(v) & (qa >= qa_min) & (lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)
    return it.datetime.date().isoformat(), lon[ok], lat[ok], v[ok]


def average(bbox, start: str, end: str, product: str, out: Path, *, res: float = 0.05, workers: int = 4) -> dict:
    """The mean over the period on a grid of res degrees (band 1) and the number of good observations (band 2); a CSV of
    the area's daily mean."""
    w, s, e, n = bbox
    if (e - w) * (n - s) > 400:
        raise ValueError("The area is too big for this (more than 20° × 20°) → choose a smaller area")
    if not 0.01 <= res <= 1:
        raise ValueError("Cell size: 0.01 to 1 degree (TROPOMI pixels are about 0.05°)")
    var, title, unit, _, qa_min = PRODUCTS[product]
    progress.update(0.02, "Finding Sentinel-5P overpasses")
    items = search(bbox, start, end, product)
    if not items:
        raise ValueError("No Sentinel-5P data over the area in that period")
    gw, gh = max(1, int(np.ceil((e - w) / res))), max(1, int(np.ceil((n - s) / res)))
    total = np.zeros(gh * gw)
    count = np.zeros(gh * gw, "int64")
    daily: dict[str, list] = defaultdict(list)
    done, failed = 0, []

    def one(it):
        try:
            return _granule(it, product, bbox)
        except Exception as ex:   # a granule that cannot be read is skipped (and counted)
            failed.append(f"{it.id}: {str(ex)[:120]}")
            return None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(one, items):
            done += 1
            progress.update(0.05 + 0.9 * done / len(items), f"Overpass {done} of {len(items)}")
            if not r or not r[1].size:
                continue
            day, lo, la, v = r
            col = np.clip(((lo - w) / res).astype(int), 0, gw - 1)
            row = np.clip(((n - la) / res).astype(int), 0, gh - 1)
            idx = row * gw + col
            np.add.at(total, idx, v)
            np.add.at(count, idx, 1)
            daily[day].append(v)
    if not count.any():
        raise ValueError("Sentinel-5P had no good-quality pixel over the area in that period (clouds or snow) → try a longer period"
                         + (f"; {len(failed)} overpasses could not be read" if failed else ""))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / np.maximum(count, 1), np.nan).reshape(gh, gw).astype("float32")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", width=gw, height=gh, count=2, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(w, n, res, res), nodata=np.nan, compress="deflate") as d:
        d.write(mean, 1)
        d.write(count.reshape(gh, gw).astype("float32"), 2)
        d.set_band_description(1, f"{title} ({unit}), mean {start} to {end}" if unit else f"{title}, mean {start} to {end}")
        d.set_band_description(2, "Good observations")
        d.update_tags(product=product, variable=var, unit=unit, start=start, end=end, qa_min=str(qa_min))
    csv_path = out.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["date", f"mean_{product}" + (f"_{unit}" if unit else ""), "pixels"])
        for day in sorted(daily):
            v = np.concatenate(daily[day])
            wr.writerow([day, round(float(v.mean()), 6), int(v.size)])
    allv = mean[np.isfinite(mean)]
    return {"path": str(out), "csv": str(csv_path), "product": title, "unit": unit, "overpasses": len(items), "unreadable": len(failed),
            "days": len(daily), "mean": round(float(allv.mean()), 6), "min": round(float(allv.min()), 6), "max": round(float(allv.max()), 6),
            "size": [gw, gh], "errors": failed[:5]}
