"""Index time series: the mean NDVI (or another index) of a point or a field in every Sentinel-2 scene of a date range,
read straight from the free catalogue (Earth Search on AWS, or another source) — only the few pixels of the area are
read from each scene. Clouds, shadows and no-data are masked with the scene classification (SCL)."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom
from rasterio.windows import Window, from_bounds
from shapely.geometry import mapping, shape

from . import progress
from .sources import get_source

INDICES = {   # name: (bands, formula on reflectance)
    "NDVI": (("B08", "B04"), lambda b: (b["B08"] - b["B04"]) / (b["B08"] + b["B04"])),
    "EVI": (("B08", "B04", "B02"), lambda b: 2.5 * (b["B08"] - b["B04"]) / (b["B08"] + 6 * b["B04"] - 7.5 * b["B02"] + 1)),
    "NDWI": (("B03", "B08"), lambda b: (b["B03"] - b["B08"]) / (b["B03"] + b["B08"])),
    "NDMI": (("B08", "B11"), lambda b: (b["B08"] - b["B11"]) / (b["B08"] + b["B11"])),
    "NDRE": (("B8A", "B05"), lambda b: (b["B8A"] - b["B05"]) / (b["B8A"] + b["B05"])),
    "SAVI": (("B08", "B04"), lambda b: 1.5 * (b["B08"] - b["B04"]) / (b["B08"] + b["B04"] + 0.5)),
}
RANGE = {"EVI": (-1, 2.5), "SAVI": (-1.5, 1.5)}   # plausible values; the others are within −1 … 1
MAX_SCENES = 400


def _area(geom: dict, buffer_m: float):
    """A point becomes a small square (buffer_m around it); polygons are used as they are."""
    g = shape(geom)
    if g.geom_type == "Point":
        d_lat = buffer_m / 110574.0
        d_lon = buffer_m / (111320.0 * max(math.cos(math.radians(g.y)), 0.01))
        from shapely.geometry import box
        g = box(g.x - d_lon, g.y - d_lat, g.x + d_lon, g.y + d_lat)
    if g.area > 0.01:   # degrees²: about 10 × 10 km
        raise ValueError("The area is too big for a time series (up to about 10 × 10 km) → choose a field or a point")
    return g


def _read(src_obj, item, band, geom_ll, out_shape=None):
    """The window of one band that covers the area, its transform, and the area's mask on it."""
    href = src_obj.href(item, band)
    with rasterio.Env(**src_obj.gdal_env()), rasterio.open(href) as s:
        g = shape(transform_geom("EPSG:4326", s.crs, mapping(geom_ll)))
        w = from_bounds(*g.bounds, transform=s.transform).round_offsets(op="floor").round_lengths(op="ceil")
        w = Window(w.col_off, w.row_off, max(1, w.width), max(1, w.height)).intersection(Window(0, 0, s.width, s.height))
        if out_shape is not None:
            a = s.read(1, window=w, out_shape=out_shape, resampling=rasterio.enums.Resampling.nearest)
            tr = s.window_transform(w) * s.window_transform(w).scale(w.width / out_shape[1], w.height / out_shape[0])
        else:
            a = s.read(1, window=w)
            tr = s.window_transform(w)
        inside = ~geometry_mask([mapping(g)], out_shape=a.shape, transform=tr, all_touched=True)
        return a.astype("float64"), inside


def series(geom: dict, start: str, end: str, *, index: str = "NDVI", source: str = "earth-search", max_cloud: float = 80,
           buffer_m: float = 15, min_clear: float = 0.5, **credentials) -> dict:
    """The index's mean, std and clear-pixel share of the area per scene date (cloudy dates are left out)."""
    if index not in INDICES:
        raise ValueError(f"Index: one of {', '.join(INDICES)}")
    bands, fn = INDICES[index]
    area = _area(geom, buffer_m)
    src = get_source(source, **credentials)
    progress.update(0.02, "Searching the catalogue")
    items = list(src.client().search(collections=[src.collection], intersects=mapping(area), datetime=f"{start}/{end}",
                                     max_items=MAX_SCENES, **src.search_kwargs(max_cloud)).items())
    if not items:
        raise ValueError(f"No Sentinel-2 scenes between {start} and {end} with less than {max_cloud:g} % cloud")
    by_date = {}
    for it in sorted(items, key=lambda i: (i.datetime, -(i.properties.get("eo:cloud_cover") or 0))):
        by_date[(it.datetime.date().isoformat(), src.tile(it))] = it   # one scene per date and tile
    items = list(by_date.values())

    def one(it):
        try:
            data, inside = {}, None
            for b in bands:
                a, ins = _read(src, it, b, area, out_shape=None if inside is None else inside.shape)
                data[b] = src.to_reflectance(it, a)
                inside = ins if inside is None else inside
            scl, _ = _read(src, it, "SCL", area, out_shape=inside.shape)
            cloud, shadow, nodata = src.mask_classes(scl)
            clear = inside & ~cloud & ~shadow & ~nodata & np.all([np.isfinite(d) & (d > 0) for d in data.values()], axis=0)
            share = float(clear.sum() / max(inside.sum(), 1))
            row = {"date": it.datetime.date().isoformat(), "scene": it.id, "tile": src.tile(it), "clear_pct": round(100 * share, 1),
                   "scene_cloud_pct": round(float(it.properties.get("eo:cloud_cover") or 0), 1)}
            if share >= min_clear:
                with np.errstate(all="ignore"):
                    v = fn(data)[clear]
                lo, hi = RANGE.get(index, (-1, 1))
                v = v[np.isfinite(v) & (v >= lo) & (v <= hi)]   # haze and cloud edges give impossible values
                if v.size:
                    row.update({"mean": round(float(v.mean()), 4), "std": round(float(v.std()), 4), "min": round(float(v.min()), 4),
                                "max": round(float(v.max()), 4), "pixels": int(v.size)})
            return row
        except Exception as e:  # noqa: BLE001 — one unreadable scene is left out
            return {"date": it.datetime.date().isoformat(), "scene": it.id, "error": str(e)[:200]}
    found = []
    with ThreadPoolExecutor(16) as ex:   # progress is reported here, in the job's own thread (it also cancels)
        futs = [ex.submit(one, it) for it in items]
        try:
            for n, fu in enumerate(as_completed(futs), 1):
                found.append(fu.result())
                progress.update(0.05 + 0.93 * n / len(items), f"Scene {n} of {len(items)} ({sum('mean' in r for r in found)} clear)")
        except BaseException:
            for fu in futs:
                fu.cancel()
            raise
    best = {}   # one row per date: where tiles overlap, the clearer scene
    for r in found:
        k = r["date"]
        if k not in best or ("mean" in r, r.get("clear_pct", 0), -r.get("scene_cloud_pct", 100)) > ("mean" in best[k], best[k].get("clear_pct", 0), -best[k].get("scene_cloud_pct", 100)):
            best[k] = r
    rows = sorted(best.values(), key=lambda r: r["date"])
    good = [r for r in rows if "mean" in r]
    if not good:
        raise ValueError("Every scene was cloudy over the area (or couldn't be read) → choose a longer date range or a bigger area")
    progress.update(1, "Done")
    return {"index": index, "rows": rows, "points": len(good), "scenes": len(rows), "area_m2": round(_area_m2(area), 1),
            "summary": {"min": min(r["mean"] for r in good), "max": max(r["mean"] for r in good),
                        "mean": round(sum(r["mean"] for r in good) / len(good), 4),
                        "peak_date": max(good, key=lambda r: r["mean"])["date"], "low_date": min(good, key=lambda r: r["mean"])["date"]}}


def _area_m2(g) -> float:
    c = g.centroid
    zone = int((c.x + 180) // 6) + 1
    crs = f"EPSG:{(32600 if c.y >= 0 else 32700) + zone}"
    return shape(transform_geom("EPSG:4326", crs, mapping(g))).area
