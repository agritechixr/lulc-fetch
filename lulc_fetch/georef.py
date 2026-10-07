"""Georeference a picture without coordinates (a scanned map, a photo of a plan, a screenshot): control points pair a
pixel of the picture with a place on the map; the picture is warped to a GeoTIFF. Affine (3+ points), 2nd-order
polynomial (6+) or thin-plate spline (10+, exact at every point). Residuals show how well each point fits."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.warp import Resampling, calculate_default_transform, reproject
from rasterio.warp import transform as warp_transform

from . import progress

METHODS = {"affine": 3, "poly2": 6, "tps": 10}


def utm_of(points: list[dict]) -> str:
    lon = sum(p["lon"] for p in points) / len(points)
    lat = sum(p["lat"] for p in points) / len(points)
    return f"EPSG:{(32600 if lat >= 0 else 32700) + min(max(int((lon + 180) // 6) + 1, 1), 60)}"


def _terms(x, y, order):
    x, y = np.asarray(x, float), np.asarray(y, float)
    t = [np.ones_like(x), x, y]
    if order == 2:
        t += [x * x, x * y, y * y]
    return np.stack(t, axis=1)


def fit(points: list[dict], method: str = "affine") -> dict:
    """The fit of the control points ({px, py, lon, lat}: pixel column / row, and the map place): each point's residual
    in metres and pixels, and the RMSE. Thin-plate spline passes exactly through the points (residuals are 0)."""
    if method not in METHODS:
        raise ValueError("Method: affine, poly2 or tps")
    pts = [p for p in points if all(k in p for k in ("px", "py", "lon", "lat"))]
    need = METHODS[method]
    if len(pts) < need:
        raise ValueError(f"{dict(affine='Affine', poly2='Polynomial (2nd order)', tps='Thin-plate spline')[method]} needs at least {need} points (you have {len(pts)})")
    crs = utm_of(pts)
    xs, ys = warp_transform("EPSG:4326", crs, [p["lon"] for p in pts], [p["lat"] for p in pts])
    xs, ys = np.array(xs), np.array(ys)
    px, py = np.array([p["px"] for p in pts], float), np.array([p["py"] for p in pts], float)
    if method == "tps":
        res_m = np.zeros(len(pts))
        scale = None
    else:
        A = _terms(px, py, 1 if method == "affine" else 2)
        cx, *_ = np.linalg.lstsq(A, xs, rcond=None)
        cy, *_ = np.linalg.lstsq(A, ys, rcond=None)
        res_m = np.hypot(A @ cx - xs, A @ cy - ys)
        scale = float(math.hypot(cx[1], cy[1]))   # metres per pixel along the picture's x
    if method == "affine" and len(pts) == 3:
        res_m = np.zeros(3)   # 3 points always fit exactly: no check yet
    rmse = float(np.sqrt(np.mean(res_m ** 2)))
    return {"crs": crs, "method": method, "points": len(pts), "rmse_m": round(rmse, 3), "pixel_m": None if scale is None else round(scale, 4),
            "residuals_m": [round(float(r), 3) for r in res_m],
            "residuals_px": [round(float(r / scale), 2) if scale else 0.0 for r in res_m],
            "worst": int(np.argmax(res_m)) if len(pts) > need else None}


def warp(image: Path, points: list[dict], out: Path, *, method: str = "affine", crs: str | None = None, res: float | None = None) -> dict:
    """The picture as a GeoTIFF in the points' UTM zone (or `crs`), each band kept (RGB / RGBA / grey)."""
    f = fit(points, method)
    crs = crs or f["crs"]
    pts = [p for p in points if all(k in p for k in ("px", "py", "lon", "lat"))]
    # z and id given: rasterio writes a missing z as "None", which GDAL can't read
    gcps = [GroundControlPoint(row=p["py"], col=p["px"], x=p["lon"], y=p["lat"], z=0.0, id=str(i + 1), info="") for i, p in enumerate(pts)]
    opts = {"SRC_METHOD": "GCP_TPS"} if method == "tps" else {"MAX_GCP_ORDER": 1 if method == "affine" else 2}
    with rasterio.open(image) as src:
        if src.width * src.height > 80_000_000:
            raise ValueError("The picture is too big (more than 80 million pixels) → make it smaller first")
        data = src.read()
        count, dtype = src.count, src.dtypes[0]
        tr, w, h = calculate_default_transform("EPSG:4326", crs, src.width, src.height, gcps=gcps, resolution=res, **opts)
        if w * h > 120_000_000:
            raise ValueError("The georeferenced picture would be too big → check the points (one may be far off) or give a pixel size")
        out.parent.mkdir(parents=True, exist_ok=True)
        alpha = count in (2, 4)
        prof = dict(driver="GTiff", width=w, height=h, count=count, dtype=dtype, crs=crs, transform=tr, compress="deflate",
                    tiled=True, blockxsize=256, blockysize=256, **({} if alpha else {"nodata": 0}))
        with rasterio.open(out, "w", **prof) as d:
            for b in range(count):
                dst = np.zeros((h, w), dtype=dtype)
                reproject(data[b], dst, gcps=gcps, src_crs="EPSG:4326", dst_transform=tr, dst_crs=crs,
                          resampling=Resampling.bilinear, src_nodata=None, dst_nodata=0, **opts)
                d.write(dst, b + 1)
                progress.update((b + 1) / count, f"Band {b + 1} of {count}")
            if count >= 3:
                d.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green, rasterio.enums.ColorInterp.blue] + \
                    ([rasterio.enums.ColorInterp.alpha] if count == 4 else [])
            d.update_tags(georeferenced_from=Path(image).name, method=method, control_points=str(len(pts)), rmse_m=str(f["rmse_m"]))
    return {**f, "path": str(out), "size": [w, h], "res": round(abs(tr.a), 4)}
