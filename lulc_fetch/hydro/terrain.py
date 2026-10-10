"""Terrain and runoff indicators (Analysis ▸ Hydrology ▸ Terrain & runoff indicators): predictor layers for flood,
erosion and wetness models.

- Slope (degrees), aspect (degrees from north).
- Curvature (Zevenbergen & Thorne 1987, 1/100 m as ArcGIS): profile (along the slope: negative = convex, flow speeds
  up), plan (across: negative = converging flow), total.
- TWI, topographic wetness index = ln(a / tan β), and SPI, stream power index = ln(a · tan β); a = specific catchment
  area from MFD (the usual choice), D-infinity or D8.
- HAND, height above the nearest drainage (Rennó et al. 2008): how far above the stream each cell is, following the
  flow to it, and the distance along the flow to that stream. Low HAND = floods first.
- Depressions: their depth (filled − original), and each as a polygon with its area, deepest point and volume.
- Flow length: down to where water leaves the DEM, and up to the divide.
- Flow paths: the path water takes downhill from points you give, with its length and drop."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .. import progress
from .common import Grid, Surface, line, open_dem, polygons, write, write_fc
from .flow import accumulate_multi, receivers_dinf, receivers_mfd
from .watershed import upstream_length

PRODUCTS = ("slope", "aspect", "curvature", "twi", "spi", "hand", "depressions", "flowlength", "flowpaths")


def slope_aspect(g: Grid, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    gy, gx = np.gradient(z)
    dzdx, dzdy = gx / g.dx, -gy / g.dy
    tan = np.hypot(dzdx, dzdy)
    aspect = (np.degrees(np.arctan2(-dzdx, -dzdy)) + 360) % 360
    aspect[tan < 1e-9] = -1
    return np.degrees(np.arctan(tan)), aspect, tan


def curvatures(g: Grid, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(profile, plan, total) curvature, 1/100 m."""
    p = np.pad(z, 1, mode="edge")
    z1, z2, z3 = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    z4, z5, z6 = p[1:-1, :-2], p[1:-1, 1:-1], p[1:-1, 2:]
    z7, z8, z9 = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dx, dy = g.dx, g.dy
    D = ((z4 + z6) / 2 - z5) / dx ** 2
    E = ((z2 + z8) / 2 - z5) / dy ** 2
    F = (-z1 + z3 + z7 - z9) / (4 * dx * dy)
    G = (-z4 + z6) / (2 * dx)
    H = (z2 - z8) / (2 * dy)
    gh = G * G + H * H
    with np.errstate(invalid="ignore", divide="ignore"):
        prof = np.where(gh > 1e-12, -2 * (D * G * G + E * H * H + F * G * H) / gh, 0.0)
        plan = np.where(gh > 1e-12, 2 * (D * H * H + E * G * G - F * G * H) / gh, 0.0)
    total = -2 * (D + E)
    return prof * 100, plan * 100, total * 100


def hand(s: Surface, stream: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(HAND m, distance to the drainage along the flow m, the drainage cell each cell reaches)."""
    n = s.down.size
    target = np.where(stream, np.arange(n), -1)
    dist = np.zeros(n)
    for lv in reversed(s.levels):                          # downstream cells first: take theirs
        m = ~stream[lv] & (s.down[lv] >= 0)
        c, d = lv[m], s.down[lv[m]]
        target[c] = target[d]
        dist[c] = dist[d] + s.step[c]
    zf = z.ravel()
    hnd = np.where(target >= 0, zf - zf[np.maximum(target, 0)], np.nan)
    return np.maximum(hnd, 0), np.where(target >= 0, dist, np.nan), target


def run(dem, out_dir: Path, *, products=("slope", "curvature", "twi", "spi", "hand"), flow: str = "mfd", stream_km2: float = 1.0,
        points: list[list[float]] | None = None, min_depression_m: float = 0.1, conditioned: bool = False, name: str | None = None) -> dict:
    bad = set(products) - set(PRODUCTS)
    if bad or not products:
        raise ValueError(f"products: {', '.join(PRODUCTS)}")
    if flow not in ("mfd", "dinf", "d8"):
        raise ValueError("flow: mfd, dinf or d8")
    out_dir = Path(out_dir)
    stem = name or Path(dem).stem
    g, z = open_dem(dem)
    progress.update(0.05, "Flow on the DEM")
    s = Surface(g, z, filled=conditioned)
    outs, summary = [], {}
    slope, aspect, tan = slope_aspect(g, s.filled)
    if "slope" in products:
        outs.append(write(g, out_dir / f"{stem}_slope.tif", slope, "Slope (degrees)"))
    if "aspect" in products:
        outs.append(write(g, out_dir / f"{stem}_aspect.tif", aspect, "Aspect (degrees from north; -1 flat)"))
    if "curvature" in products:
        progress.update(0.4, "Curvature")
        prof, plan, total = curvatures(g, s.filled)
        outs.append(write(g, out_dir / f"{stem}_curvature.tif", np.stack([prof, plan, total]),
                          ["Profile curvature (1/100 m; − convex, flow speeds up)", "Plan curvature (1/100 m; − converging flow)", "Total curvature (1/100 m)"]))
    if {"twi", "spi"} & set(products):
        progress.update(0.5, f"Specific catchment area ({flow.upper()})")
        valid = g.valid.ravel()
        cell = np.where(g.valid, g.cell_m2, 0).ravel()
        if flow == "d8":
            area = s.area_km2 * 1e6
        else:
            R, Wt = receivers_mfd(s.eps, g) if flow == "mfd" else receivers_dinf(s.eps, g)[:2]
            area = accumulate_multi(R, Wt, cell, valid)
        sca = (area / np.sqrt(np.maximum(cell, 1e-9))).reshape(g.h, g.w)
        tb = np.maximum(tan, 1e-3)
        if "twi" in products:
            twi = np.log(sca / tb)
            outs.append(write(g, out_dir / f"{stem}_twi.tif", twi, f"Topographic wetness index ln(a / tan β), {flow.upper()}"))
            summary["twi_mean"] = round(float(np.nanmean(np.where(g.valid, twi, np.nan))), 3)
        if "spi" in products:
            outs.append(write(g, out_dir / f"{stem}_spi.tif", np.log(np.maximum(sca * tb, 1e-6)), f"Stream power index ln(a · tan β), {flow.upper()}"))
    if "hand" in products:
        progress.update(0.65, "Height above the nearest drainage")
        stream = s.streams(stream_km2)
        hnd, dist, _ = hand(s, stream, s.filled)
        outs.append(write(g, out_dir / f"{stem}_hand.tif", np.stack([hnd.reshape(g.h, g.w), dist.reshape(g.h, g.w)]),
                          ["HAND: height above the nearest drainage (m)", "Distance along the flow to the drainage (m)"]))
        summary["hand_median_m"] = round(float(np.nanmedian(hnd[g.valid.ravel()])), 2)
    if "depressions" in products:
        progress.update(0.75, "Depressions")
        depth = np.where(g.valid, s.filled - z, np.nan)
        dep = np.nan_to_num(depth) > min_depression_m
        lab, n = ndi.label(np.nan_to_num(depth) > 1e-6, structure=np.ones((3, 3)))
        info = {}
        if n:
            keep = np.zeros(n + 1, bool)
            keep[np.unique(lab[dep])] = True
            keep[0] = False
            lab = np.where(keep[lab], lab, 0)
            cell = g.cell_m2
            ids = np.unique(lab[lab > 0])
            vol = ndi.sum(np.nan_to_num(depth) * cell, lab, ids)
            area = ndi.sum(cell, lab, ids)
            mx = ndi.maximum(np.nan_to_num(depth), lab, ids)
            spill = ndi.maximum(np.nan_to_num(s.filled), lab, ids)
            info = {int(k): {"area_m2": round(float(a), 1), "max_depth_m": round(float(m), 3), "volume_m3": round(float(v), 1), "spill_elev_m": round(float(sp), 2)}
                    for k, a, m, v, sp in zip(ids, area, mx, vol, spill)}
        outs.append(write(g, out_dir / f"{stem}_depression_depth.tif", np.where(dep | (lab > 0), depth, 0), "Depression depth (m): filled − original"))
        if info:
            outs.append(write_fc(polygons(g, lab, info, key="depression"), out_dir / f"{stem}_depressions.geojson"))
        summary["depressions"] = len(info)
        summary["depression_volume_m3"] = round(sum(v["volume_m3"] for v in info.values()), 1)
    if "flowlength" in products:
        progress.update(0.85, "Flow length")
        downl = np.zeros(s.down.size)
        for lv in reversed(s.levels):
            m = s.down[lv] >= 0
            downl[lv[m]] = downl[s.down[lv[m]]] + s.step[lv[m]]
            downl[lv[~m]] = s.step[lv[~m]]
        up = upstream_length(s)
        outs.append(write(g, out_dir / f"{stem}_flow_length.tif", np.stack([downl.reshape(g.h, g.w), up.reshape(g.h, g.w)]),
                          ["Flow length downstream, to where the water leaves the DEM (m)", "Flow length upstream, from the divide (m)"]))
    if "flowpaths" in products:
        if not points:
            raise ValueError("Flow paths: give the points to start from")
        from .common import from_lonlat
        rows, cols = from_lonlat(g, points)
        feats = []
        zf = s.filled.ravel()
        for k, (r, c) in enumerate(zip(rows, cols), 1):
            if not (0 <= r < g.h and 0 <= c < g.w) or not g.valid[r, c]:
                raise ValueError(f"Point {k} is outside the DEM")
            path, i = [], int(r * g.w + c)
            while i >= 0 and len(path) < g.h * g.w:
                path.append(i)
                i = int(s.down[i])
            ln = float(s.step[path[:-1]].sum())
            feats.append({"type": "Feature", "properties": {"point": k, "length_km": round(ln / 1000, 3), "drop_m": round(float(zf[path[0]] - zf[path[-1]]), 2)},
                          "geometry": {"type": "LineString", "coordinates": line(g, path if len(path) > 1 else path * 2)}})
        outs.append(write_fc({"features": feats}, out_dir / f"{stem}_flow_paths.geojson"))
        summary["flow_paths"] = len(feats)
    progress.update(1.0, "Done")
    return {"outputs": outs, **summary}
