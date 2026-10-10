"""Visibility on a DEM (Analysis ▸ Tools ▸ Raster & terrain ▸ Viewshed): what can be seen from one or more observers (a
viewshed; with several, how many of them see each place), and the line of sight between two points.

Rays run from the observer to every cell on the edge of the area (R2 algorithm, Franklin & Ray 1994): a cell is seen when
the angle up to it (with the target's height) is at least the steepest angle of the ground before it. Earth curvature and
refraction (k = 0.13) are allowed for, as in ArcGIS and GRASS. numpy only."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform

from . import progress
from .raster_ops import _pixel_metres, _profile

R_EARTH = 6_371_000.0
MAX_CELLS = 40_000_000


def _open(dem, band=1):
    src = rasterio.open(dem)
    if src.width * src.height > MAX_CELLS:
        src.close()
        raise ValueError(f"The DEM is {src.width:,} × {src.height:,} pixels → clip it to your area first")
    z = src.read(band, masked=True).astype("float32").filled(np.nan)
    dx, dy = _pixel_metres(src, src.height)
    dx = float(np.mean(dx)) if np.ndim(dx) else float(dx)
    return src, z, dx, float(dy)


def _rowcol(src, lon, lat):
    x, y = (lon, lat) if not src.crs or src.crs.to_epsg() == 4326 else (v[0] for v in warp_transform("EPSG:4326", src.crs, [lon], [lat]))
    r, c = rasterio.transform.rowcol(src.transform, x, y)
    if not (0 <= r < src.height and 0 <= c < src.width):
        raise ValueError(f"The point {lon:.5f}, {lat:.5f} is outside the DEM")
    return int(r), int(c)


def _drop(d, curvature: bool, refraction: float):
    return (d * d) / (2 * R_EARTH) * (1 - refraction) if curvature else 0.0


def viewshed_one(z, dx, dy, r0, c0, *, observer_h=1.7, target_h=0.0, max_dist_m=None, curvature=True, refraction=0.13) -> np.ndarray:
    """A bool raster: True where the observer at (r0, c0) sees the ground (plus target_h)."""
    h, w = z.shape
    zo = z[r0, c0]
    if not np.isfinite(zo):
        raise ValueError("The observer stands on a cell without a height")
    zo = zo + observer_h
    if max_dist_m:
        rr, rc = int(np.ceil(max_dist_m / dy)), int(np.ceil(max_dist_m / dx))
        top, bot, left, right = max(0, r0 - rr), min(h - 1, r0 + rr), max(0, c0 - rc), min(w - 1, c0 + rc)
    else:
        top, bot, left, right = 0, h - 1, 0, w - 1
    # every cell on the window's edge is the end of one ray
    edge = ([(top, c) for c in range(left, right + 1)] + [(bot, c) for c in range(left, right + 1)] +
            [(r, left) for r in range(top + 1, bot)] + [(r, right) for r in range(top + 1, bot)])
    ends = np.array(sorted(set(edge)))
    vis = np.zeros((h, w), bool)
    vis[r0, c0] = True
    chunk = max(1, 4_000_000 // max(1, max(bot - top, right - left) + 1))
    for s in range(0, len(ends), chunk):
        e = ends[s:s + chunk]
        dr, dc = e[:, 0] - r0, e[:, 1] - c0
        n = np.maximum(np.abs(dr), np.abs(dc))
        L = int(n.max())
        if L == 0:
            continue
        t = np.arange(1, L + 1)[None, :] / np.maximum(n, 1)[:, None]    # steps along each ray
        ok = t <= 1 + 1e-9
        rows = np.rint(r0 + t * dr[:, None]).astype(int).clip(0, h - 1)
        cols = np.rint(c0 + t * dc[:, None]).astype(int).clip(0, w - 1)
        dist = np.hypot((rows - r0) * dy, (cols - c0) * dx)
        dist = np.where(dist > 0, dist, np.nan)
        zg = z[rows, cols] - _drop(dist, curvature, refraction)
        ground = (zg - zo) / dist                                           # the slope up to the ground
        target = (zg + target_h - zo) / dist
        ground = np.where(ok & np.isfinite(ground), ground, -np.inf)
        before = np.maximum.accumulate(np.concatenate([np.full((len(e), 1), -np.inf), ground[:, :-1]], axis=1), axis=1)
        seen = ok & np.isfinite(target) & (target >= before)
        if max_dist_m:
            seen &= dist <= max_dist_m
        vis[rows[seen], cols[seen]] = True
    vis &= np.isfinite(z)
    return vis


def viewshed(dem, out: Path, observers: list[list[float]], *, observer_h=1.7, target_h=0.0, max_dist_m=None,
             curvature=True, refraction=0.13, band=1) -> dict:
    """Viewshed of one observer (1 seen, 0 not) or, with several, how many of them see each cell. observers: [lon, lat]."""
    if not observers:
        raise ValueError("Give at least one observer point")
    if len(observers) > 200:
        raise ValueError("Up to 200 observers at a time")
    src, z, dx, dy = _open(dem, band)
    with src:
        count = np.zeros(z.shape, "uint16")
        for k, (lon, lat) in enumerate(observers):
            r, c = _rowcol(src, lon, lat)
            count += viewshed_one(z, dx, dy, r, c, observer_h=observer_h, target_h=target_h, max_dist_m=max_dist_m,
                                  curvature=curvature, refraction=refraction)
            progress.update((k + 1) / len(observers), f"Observer {k + 1} of {len(observers)}")
        valid = np.isfinite(z)
        one = len(observers) == 1
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        prof = _profile(src, "uint8" if one or len(observers) < 255 else "uint16", 255 if one or len(observers) < 255 else 65535)
        with rasterio.open(out, "w", **prof) as d:
            a = np.where(valid, count, prof["nodata"]).astype(prof["dtype"])
            d.write(a, 1)
            d.set_band_description(1, "Visible (1) or not (0)" if one else "Number of observers that see the cell")
            if one:
                d.write_colormap(1, {0: (60, 60, 60, 150), 1: (34, 197, 94, 200)})
                d.update_tags(classes=json.dumps({"0": "Not visible", "1": "Visible"}))
        cell_km2 = dx * dy / 1e6
        seen = int(((count > 0) & valid).sum())
    return {"path": str(out), "observers": len(observers), "visible_km2": round(seen * cell_km2, 4),
            "visible_pct": round(100 * seen / max(1, int(valid.sum())), 2)}


def line_of_sight(dem, a: list[float], b: list[float], *, observer_h=1.7, target_h=0.0, curvature=True, refraction=0.13, band=1) -> dict:
    """Can a (lon, lat, + observer_h) see b (+ target_h)? The profile between them, where the view is blocked first, and
    the line split into seen / hidden parts (GeoJSON, EPSG:4326)."""
    src, z, dx, dy = _open(dem, band)
    with src:
        r0, c0 = _rowcol(src, *a)
        r1, c1 = _rowcol(src, *b)
        n = max(abs(r1 - r0), abs(c1 - c0), 1)
        t = np.linspace(0, 1, n + 1)
        rows, cols = np.rint(r0 + t * (r1 - r0)).astype(int), np.rint(c0 + t * (c1 - c0)).astype(int)
        dist = np.hypot((rows - r0) * dy, (cols - c0) * dx)
        zg = z[rows, cols].astype("float64") - _drop(dist, curvature, refraction)
        if not np.isfinite(zg[0]):
            raise ValueError("The observer stands on a cell without a height")
        zo = zg[0] + observer_h
        with np.errstate(invalid="ignore", divide="ignore"):
            ground = np.where(dist > 0, (zg - zo) / dist, -np.inf)
            target = np.where(dist > 0, (zg + target_h - zo) / dist, np.inf)
        before = np.maximum.accumulate(np.concatenate([[-np.inf], np.nan_to_num(ground[:-1], nan=-np.inf)]))
        seen = (target >= before) | (dist == 0)
        xs, ys = rasterio.transform.xy(src.transform, rows, cols)
        if src.crs and src.crs.to_epsg() != 4326:
            xs, ys = warp_transform(src.crs, "EPSG:4326", list(map(float, xs)), list(map(float, ys)))
        coords = [[round(float(x), 7), round(float(y), 7)] for x, y in zip(xs, ys)]
        feats, start = [], 0
        for i in range(1, len(seen) + 1):
            if i == len(seen) or seen[i] != seen[start]:
                seg = coords[max(0, start - 1 if start else 0):i]
                if len(seg) >= 2:
                    feats.append({"type": "Feature", "properties": {"visible": bool(seen[start]), "from_m": round(float(dist[start]), 1), "to_m": round(float(dist[i - 1]), 1)},
                                  "geometry": {"type": "LineString", "coordinates": seg}})
                start = i
        blocked = np.flatnonzero(~seen)
        first = int(blocked[0]) if blocked.size else None
    return {"visible": bool(seen[-1]), "distance_m": round(float(dist[-1]), 1),
            "first_blocked_m": round(float(dist[first]), 1) if first is not None else None,
            "profile": {"distance_m": [round(float(v), 1) for v in dist], "height_m": [None if not np.isfinite(v) else round(float(v), 2) for v in zg],
                        "sight_m": [round(float(zo + (zg[-1] + target_h - zo) * d / max(dist[-1], 1e-9)), 2) for d in dist], "visible": seen.tolist()},
            "lines": {"type": "FeatureCollection", "features": feats}}
