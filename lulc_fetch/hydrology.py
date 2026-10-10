"""Hydrology from a DEM (Analysis ▸ Tools ▸ Raster & terrain ▸ Hydrology): fill sinks, D8 flow direction, flow
accumulation, streams (with their Strahler order, as a raster and as lines), watersheds (of points you give, or every
basin) and the topographic wetness index.

Sinks are filled with a priority flood (Barnes et al. 2014, with an epsilon so flats drain too); flow follows the steepest
of the 8 neighbours (D8, ESRI codes 1 E, 2 SE, 4 S, 8 SW, 16 W, 32 NW, 64 N, 128 NE; 0 where water leaves the DEM).
Accumulation, orders and basins are worked out level by level in flow order (vectorised), so a 2000 × 2000 DEM takes
seconds. numpy and scipy only."""

from __future__ import annotations

import heapq
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform

from . import progress
from .raster_ops import _pixel_metres, _profile

MAX_CELLS = 16_000_000   # bigger DEMs: resample or clip first
# D8: (row offset, column offset, ESRI code)
D8 = [(0, 1, 1), (1, 1, 2), (1, 0, 4), (1, -1, 8), (0, -1, 16), (-1, -1, 32), (-1, 0, 64), (-1, 1, 128)]
PRODUCTS = ("filled", "direction", "accumulation", "streams", "order", "basins", "twi")


def read_dem(path, band: int = 1):
    src = rasterio.open(path)
    if src.width * src.height > MAX_CELLS:
        src.close()
        raise ValueError(f"The DEM is {src.width:,} × {src.height:,} pixels: hydrology handles up to {MAX_CELLS / 1e6:.0f} million → "
                         "clip it to your area or make the pixels bigger (Resample) first")
    z = src.read(band, masked=True).astype("float64").filled(np.nan)
    return src, z


def fill_sinks(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(filled, filled_eps): the DEM with every sink filled to its spill height, and the same raised by tiny steps so that
    flats drain towards their outlet. Water leaves at the DEM's edge and next to no-data."""
    h, w = z.shape
    valid = np.isfinite(z)
    filled = np.where(valid, z, np.nan)
    eps = filled.copy()
    closed = ~valid
    # seeds: valid cells on the edge or next to no-data
    pad = np.pad(valid, 1, constant_values=False)
    inner = np.ones_like(valid)
    for dr, dc, _ in D8:
        inner &= pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
    seeds = np.flatnonzero(valid & ~inner)
    heap = [(float(eps.flat[i]), int(i)) for i in seeds]
    heapq.heapify(heap)
    closed.flat[seeds] = True
    fz, fe, cl = filled.ravel(), eps.ravel(), closed.ravel()
    offs = [(dr, dc) for dr, dc, _ in D8]
    n_done, total = 0, int(valid.sum())
    while heap:
        e, i = heapq.heappop(heap)
        r, c = divmod(i, w)
        fi = fz[i]
        for dr, dc in offs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w:
                j = rr * w + cc
                if cl[j]:
                    continue
                cl[j] = True
                if fz[j] < fi:
                    fz[j] = fi
                ej = fe[j]
                if ej <= e:
                    ej = fe[j] = math.nextafter(e, math.inf)
                heapq.heappush(heap, (ej, j))
        n_done += 1
        if n_done % 250_000 == 0:
            progress.update(0.05 + 0.35 * n_done / total, f"Filling sinks: {n_done:,} of {total:,} cells")
    return filled, eps


def flow_direction(eps: np.ndarray, dx, dy) -> tuple[np.ndarray, np.ndarray]:
    """(code, down): the D8 code of each cell and the flat index of the cell it drains to (-1: it leaves the DEM)."""
    h, w = eps.shape
    pad = np.pad(eps, 1, constant_values=np.nan)
    best = np.zeros((h, w))
    code = np.zeros((h, w), "uint8")
    down = np.full((h, w), -1, "int64")
    rows, cols = np.indices((h, w))
    dx = np.asarray(dx, dtype="float64")   # a number, or one per row (geographic DEMs)
    for dr, dc, k in D8:
        nb = pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
        dist = np.hypot(dr * dy, dc * dx)
        drop = (eps - nb) / dist
        drop = np.where(np.isfinite(drop), drop, -np.inf)
        better = drop > best
        best = np.where(better, drop, best)
        code[better] = k
        down[better] = ((rows + dr) * w + (cols + dc))[better]
    code[~np.isfinite(eps)] = 255
    return code, down.ravel()


def flow_levels(down: np.ndarray, valid: np.ndarray) -> list[np.ndarray]:
    """The cells in flow order, as levels: every cell comes after all the cells that drain into it."""
    n = down.size
    has = down >= 0
    indeg = np.bincount(down[has], minlength=n)
    frontier = np.flatnonzero(valid & (indeg == 0))
    levels = []
    while frontier.size:
        levels.append(frontier)
        d = down[frontier]
        d = d[d >= 0]
        if not d.size:
            break
        np.subtract.at(indeg, d, 1)
        u = np.unique(d)
        frontier = u[indeg[u] == 0]
    return levels


def accumulate(down: np.ndarray, levels: list[np.ndarray], weight: np.ndarray) -> np.ndarray:
    """Flow accumulation: each cell's weight plus everything that drains into it."""
    acc = weight.astype("float64").copy()
    for lv in levels:
        d = down[lv]
        m = d >= 0
        np.add.at(acc, d[m], acc[lv[m]])
    return acc


def strahler(down: np.ndarray, levels: list[np.ndarray], stream: np.ndarray) -> np.ndarray:
    """Strahler order of the stream cells (0 elsewhere): 1 at the heads; where two streams of the same order meet, one more."""
    order = np.zeros(down.size, "int32")
    top = np.zeros(down.size, "int32")    # the highest order flowing in
    cnt = np.zeros(down.size, "int32")    # how many inflows have it
    for lv in levels:
        s = lv[stream[lv]]
        if not s.size:
            continue
        order[s] = np.where(top[s] == 0, 1, top[s] + (cnt[s] >= 2))
        d = down[s]
        m = (d >= 0) & stream[np.maximum(d, 0)]
        s, d = s[m], d[m]
        if not s.size:
            continue
        o = order[s]
        # per downstream cell: the highest inflow order and how many reach it (cells of one level reach distinct or shared d)
        new_top = top.copy()
        np.maximum.at(new_top, d, o)
        raised = new_top[d] > top[d]
        reset = np.unique(d[raised])
        cnt[reset] = 0
        top = new_top
        np.add.at(cnt, d[o == top[d]], 1)
    return order


def label_basins(down: np.ndarray, levels: list[np.ndarray], outlets: dict[int, int]) -> np.ndarray:
    """Basin labels: the cells draining to each outlet (flat index → label), 0 elsewhere."""
    lab = np.zeros(down.size, "int32")
    for i, k in outlets.items():
        lab[i] = k
    for lv in reversed(levels):   # downstream cells first: a cell takes its downstream cell's label
        d = down[lv]
        m = (d >= 0) & (lab[lv] == 0)
        lab[lv[m]] = lab[d[m]]
    return lab


def _to_lonlat(src, rows, cols):
    xs, ys = rasterio.transform.xy(src.transform, rows, cols)
    xs, ys = np.atleast_1d(xs), np.atleast_1d(ys)
    if src.crs and src.crs.to_epsg() != 4326:
        xs, ys = warp_transform(src.crs, "EPSG:4326", list(map(float, xs)), list(map(float, ys)))
    return np.asarray(xs), np.asarray(ys)


def _from_lonlat(src, lons, lats):
    xs, ys = (lons, lats) if not src.crs or src.crs.to_epsg() == 4326 else warp_transform("EPSG:4326", src.crs, list(lons), list(lats))
    rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)
    return np.atleast_1d(rows), np.atleast_1d(cols)


def stream_lines(src, down: np.ndarray, stream: np.ndarray, order: np.ndarray, acc_km2: np.ndarray, w: int) -> dict:
    """The stream network as lines: one per link (from a head or a junction to the next junction or the outlet)."""
    s_idx = np.flatnonzero(stream)
    d = down[s_idx]
    inflow = np.bincount(d[(d >= 0) & stream[np.maximum(d, 0)]], minlength=down.size)
    starts = s_idx[(inflow[s_idx] != 1)]            # heads (0 inflows) and junctions (2+)
    feats, seen = [], np.zeros(down.size, bool)
    for k, i in enumerate(starts):
        path = [i]
        seen[i] = True
        j = down[i]
        while j >= 0 and stream[j]:
            path.append(j)
            if inflow[j] != 1 or seen[j]:
                break
            seen[j] = True
            j = down[j]
        if len(path) < 2:
            continue
        rows, cols = np.divmod(np.asarray(path), w)
        xs, ys = _to_lonlat(src, rows, cols)
        feats.append({"type": "Feature", "properties": {"order": int(order[i]), "length_cells": len(path) - 1, "area_km2": round(float(acc_km2[path[-2]]), 4)},
                      "geometry": {"type": "LineString", "coordinates": [[round(float(x), 7), round(float(y), 7)] for x, y in zip(xs, ys)]}})
        if k % 2000 == 0:
            progress.update(0.85 + 0.1 * k / max(1, len(starts)), f"Stream lines: {k:,} of {len(starts):,}")
    return {"type": "FeatureCollection", "features": feats}


def _basin_polygons(src, lab2d: np.ndarray, info: dict[int, dict]) -> dict:
    from rasterio import features
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as stransform, unary_union
    geoms: dict[int, list] = {}
    for g, v in features.shapes(lab2d.astype("int32"), mask=lab2d > 0, transform=src.transform):
        geoms.setdefault(int(v), []).append(shape(g))
    def to_ll(x, y, z=None):
        xs, ys = warp_transform(src.crs, "EPSG:4326", list(np.atleast_1d(x)), list(np.atleast_1d(y)))
        return np.asarray(xs), np.asarray(ys)
    feats = []
    for k, parts in geoms.items():
        g = unary_union(parts)
        if src.crs and src.crs.to_epsg() != 4326:
            g = stransform(to_ll, g)
        feats.append({"type": "Feature", "properties": {"basin": k, **info.get(k, {})}, "geometry": mapping(g)})
    return {"type": "FeatureCollection", "features": feats}


def run(dem, out_dir: Path, *, products=("filled", "direction", "accumulation", "streams"), stream_km2: float = 1.0,
        points: list[list[float]] | None = None, snap_m: float = 150.0, min_basin_km2: float | None = None, band: int = 1,
        name: str | None = None) -> dict:
    """Hydrology of a DEM. products: any of PRODUCTS; 'basins' with `points` ([lon, lat] outlets, snapped to the biggest
    flow within snap_m) gives their watersheds, without points every basin of at least min_basin_km2 (default 10 × the
    stream threshold). Returns the written files (rasters and GeoJSON) and a summary."""
    bad = set(products) - set(PRODUCTS)
    if bad or not products:
        raise ValueError(f"products: {', '.join(PRODUCTS)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = name or Path(dem).stem
    src, z = read_dem(dem, band)
    with src:
        valid = np.isfinite(z)
        if valid.sum() < 9:
            raise ValueError("The DEM has (almost) no heights")
        h, w = z.shape
        dx, dy = _pixel_metres(src, h)
        cell_km2 = (np.broadcast_to(np.asarray(dx, dtype="float64"), (h, 1)) * dy / 1e6).repeat(w, axis=1) if np.ndim(dx) else np.full((h, w), dx * dy / 1e6)
        progress.update(0.05, f"Filling sinks of {w:,} × {h:,} cells")
        filled, eps = fill_sinks(z)
        progress.update(0.42, "Flow direction (D8)")
        code, down = flow_direction(eps, dx, dy)
        progress.update(0.5, "Flow order")
        levels = flow_levels(down, valid.ravel())
        progress.update(0.6, "Flow accumulation")
        acc_cells = accumulate(down, levels, valid.ravel().astype("float64"))
        acc_km2 = accumulate(down, levels, np.where(valid, cell_km2, 0).ravel())
        out, summary = [], {"cells": int(valid.sum()), "sink_cells_filled": int((filled - z > 1e-9)[valid].sum()),
                            "max_fill_m": round(float(np.nanmax(filled - z)), 3), "largest_area_km2": round(float(acc_km2.max()), 3)}

        def write(fname, a, desc, dtype="float32", nodata=-9999.0, cmap=None, tags=None):
            p = out_dir / f"{stem}_{fname}.tif"
            prof = _profile(src, dtype, nodata)
            with rasterio.open(p, "w", **prof) as d:
                arr = a.reshape(h, w)
                if dtype.startswith("float"):
                    arr = np.where(np.isfinite(arr) & valid, arr, nodata)
                d.write(arr.astype(dtype), 1)
                d.set_band_description(1, desc)
                if cmap:
                    d.write_colormap(1, cmap)
                if tags:
                    d.update_tags(**tags)
            out.append(str(p))

        if "filled" in products:
            write("filled", filled, "Filled DEM (m)")
        if "direction" in products:
            write("flowdir", code, "Flow direction (D8: 1 E, 2 SE, 4 S, 8 SW, 16 W, 32 NW, 64 N, 128 NE)", "uint8", 255)
        if "accumulation" in products:
            write("flowacc", acc_km2, "Flow accumulation: upstream area (km²)")
        stream = (acc_km2 >= stream_km2) & valid.ravel()
        summary["stream_cells"] = int(stream.sum())
        order = None
        if {"streams", "order"} & set(products):
            if not stream.any():
                raise ValueError(f"No cell drains {stream_km2:g} km² → lower the stream threshold (the biggest is {acc_km2.max():.3g} km²)")
            progress.update(0.75, "Stream order (Strahler)")
            order = strahler(down, levels, stream)
            summary["max_order"] = int(order.max())
        if "order" in products:
            write("streamorder", order, "Strahler stream order (0: not a stream)", "uint8", 0)
        if "streams" in products:
            write("streams", stream.astype("uint8"), f"Streams (1: drains at least {stream_km2:g} km²)", "uint8", 0, {1: (29, 78, 216, 255)})
            fc = stream_lines(src, down, stream, order, acc_km2, w)
            p = out_dir / f"{stem}_streams.geojson"
            p.write_text(json.dumps(fc), encoding="utf-8")
            out.append(str(p))
            summary["stream_lines"] = len(fc["features"])
        if "basins" in products:
            progress.update(0.9, "Watersheds")
            outlets, info = {}, {}
            if points:
                rows, cols = _from_lonlat(src, [p[0] for p in points], [p[1] for p in points])
                r_snap = max(0, int(round(snap_m / float(np.mean(dx) if np.ndim(dx) else dx))))
                accm = acc_km2.reshape(h, w)
                for k, (r, c) in enumerate(zip(rows, cols), 1):
                    if not (0 <= r < h and 0 <= c < w):
                        raise ValueError(f"Point {k} is outside the DEM")
                    r0, r1, c0, c1 = max(0, r - r_snap), min(h, r + r_snap + 1), max(0, c - r_snap), min(w, c + r_snap + 1)
                    win = np.where(np.isfinite(accm[r0:r1, c0:c1]), accm[r0:r1, c0:c1], -1)
                    rr, cc = np.unravel_index(int(np.argmax(win)), win.shape)
                    i = (r0 + rr) * w + (c0 + cc)
                    outlets[i] = k
                lab = label_basins(down, levels, outlets)
                for i, k in outlets.items():
                    lon, lat = _to_lonlat(src, [i // w], [i % w])
                    info[k] = {"area_km2": round(float(acc_km2[i]), 4), "outlet_lon": round(float(lon[0]), 6), "outlet_lat": round(float(lat[0]), 6)}
            else:
                min_km2 = min_basin_km2 if min_basin_km2 is not None else 10 * stream_km2
                ends = np.flatnonzero(valid.ravel() & (down < 0) & (acc_km2 >= min_km2))
                ends = ends[np.argsort(-acc_km2[ends])]
                outlets = {int(i): k for k, i in enumerate(ends, 1)}
                if not outlets:
                    raise ValueError(f"No basin of {min_km2:g} km² or more → lower the basin size")
                lab = label_basins(down, levels, outlets)
                info = {k: {"area_km2": round(float(acc_km2[i]), 4)} for i, k in outlets.items()}
            summary["basins"] = len(outlets)
            write("basins", lab, "Basin (watershed) number", "int32", 0)
            fc = _basin_polygons(src, lab.reshape(h, w), info)
            p = out_dir / f"{stem}_basins.geojson"
            p.write_text(json.dumps(fc), encoding="utf-8")
            out.append(str(p))
            summary["basin_areas_km2"] = [info[k]["area_km2"] for k in sorted(info)][:50]
        if "twi" in products:
            progress.update(0.95, "Topographic wetness index")
            gy, gx = np.gradient(filled)
            tanb = np.hypot(gx / dx, gy / dy)
            width_m = np.sqrt(cell_km2) * 1000
            a = acc_km2.reshape(h, w) * 1e6 / width_m     # specific catchment area (m²/m)
            twi = np.log(a / np.maximum(tanb, 1e-3))
            write("twi", twi, "Topographic wetness index ln(a / tan β)")
        progress.update(1.0, "Done")
    return {"outputs": out, "summary": summary}
