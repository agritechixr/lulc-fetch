"""DEM conditioning (Analysis ▸ Hydrology ▸ DEM preparation): make a DEM drain the way water does before any flow
analysis.

- Fill no-data holes: small gaps inside the DEM (voids, clouds in a photogrammetric DEM) filled from their edges by
  repeated normalised smoothing (as GDAL FillNodata); large gaps and the area outside the DEM stay empty.
- Align: onto another raster's grid (same pixels and extent), or a new pixel size.
- Burn streams: lower the DEM along known rivers / canals (a line layer) so flow follows them (stream burning), with an
  optional gentle trough on each side.
- Breach: carve a channel out of each pit down to its outlet instead of filling it (complete breaching, Lindsay 2016,
  done as a priority flood: Barnes 2016). Roads and embankments across valleys are cut instead of turning the valley
  into a lake. Breaching deeper than a limit falls back to filling.
- Fill: raise every pit to its spill height (priority flood).
Returns the conditioned DEM and how much each cell changed (+ filled, − breached)."""

from __future__ import annotations

import heapq
import math
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .. import progress
from .common import Grid, open_dem, read_like, write

D8 = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]


def fill_nodata(z: np.ndarray, max_cells: int = 2000, iters: int = 200) -> tuple[np.ndarray, int]:
    """Holes (NaN areas not touching the edge, up to max_cells) filled from their surroundings."""
    holes = ~np.isfinite(z)
    lab, n = ndi.label(holes)
    if not n:
        return z, 0
    sizes = np.bincount(lab.ravel())
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    fill = np.zeros(n + 1, bool)
    fill[1:] = sizes[1:] <= max_cells
    fill[edge] = False
    todo = fill[lab]
    if not todo.any():
        return z, 0
    out = z.copy()
    known = np.isfinite(out)
    val = np.where(known, out, 0.0)
    k = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], float)
    for _ in range(iters):
        num = ndi.convolve(val, k, mode="nearest")
        den = ndi.convolve(known.astype(float), k, mode="nearest")
        grow = todo & ~known & (den > 0)
        if not grow.any():
            break
        val[grow] = num[grow] / den[grow]
        known = known | grow
    # a few smoothing passes inside the filled holes only, so they are not stepped
    for _ in range(10):
        sm = ndi.uniform_filter(val, 3, mode="nearest")
        val[todo & known] = sm[todo & known]
    out[todo & known] = val[todo & known]
    return out, int((todo & known).sum())


def burn_streams(g: Grid, z: np.ndarray, lines: dict, depth_m: float = 5.0, buffer_m: float = 0.0) -> tuple[np.ndarray, int]:
    """Lower the DEM by depth_m along the lines (EPSG:4326 GeoJSON), sloping back up over buffer_m on each side."""
    from rasterio import features
    from shapely.geometry import shape

    from ..convert import _reproject_all
    geoms = [shape(f["geometry"]) for f in lines.get("features", []) if f.get("geometry") and "LineString" in f["geometry"]["type"]]
    if not geoms:
        raise ValueError("The stream layer has no lines")
    geoms = _reproject_all(geoms, "EPSG:4326", g.crs)
    on = features.rasterize(((gm, 1) for gm in geoms), out_shape=(g.h, g.w), transform=g.transform, all_touched=True, dtype="uint8") > 0
    on &= g.valid
    if not on.any():
        raise ValueError("No stream line crosses the DEM")
    drop = np.where(on, depth_m, 0.0)
    if buffer_m > 0:
        d = ndi.distance_transform_edt(~on, sampling=(g.dy, g.cell_m))
        near = (d > 0) & (d < buffer_m)
        drop[near] = depth_m * (1 - d[near] / buffer_m)
    return z - drop, int(on.sum())


def breach(z: np.ndarray, *, max_depth: float | None = None, max_length: int | None = None) -> tuple[np.ndarray, int, int]:
    """Complete breaching by priority flood: whenever a cell is lower than the cell it was reached from, the path back
    towards the outlet is lowered just below it (a carved channel). Carving deeper than max_depth (m) or longer than
    max_length (cells) is not done: that pit is filled instead. Returns (dem, breached cells, filled cells)."""
    h, w = z.shape
    valid = np.isfinite(z)
    out = np.where(valid, z, np.nan).astype("float64")
    zf = out.ravel()
    closed = (~valid).ravel().copy()
    parent = np.full(h * w, -1, "int64")
    pad = np.pad(valid, 1, constant_values=False)
    inner = np.ones_like(valid)
    for dr, dc in D8:
        inner &= pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
    seeds = np.flatnonzero(valid & ~inner)
    heap = [(float(zf[i]), int(i)) for i in seeds]
    heapq.heapify(heap)
    closed[seeds] = True
    carved = filled = 0
    total, done = int(valid.sum()), 0
    while heap:
        e, i = heapq.heappop(heap)
        r, c = divmod(i, w)
        for dr, dc in D8:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < h and 0 <= cc < w):
                continue
            j = rr * w + cc
            if closed[j]:
                continue
            closed[j] = True
            parent[j] = i
            if zf[j] <= zf[i]:
                # j sits in a pit: carve the path i → … → outlet below j, if within the limits
                target = zf[j]
                path, p, ok = [], i, True
                while p >= 0 and zf[p] >= target:
                    path.append(p)
                    if (max_length and len(path) > max_length) or (max_depth is not None and zf[p] - target > max_depth):
                        ok = False
                        break
                    p = parent[p]
                if ok:
                    step = target
                    for q in path:
                        step = math.nextafter(step, -math.inf)
                        zf[q] = step
                    carved += len(path)
                else:                     # too deep or too long: fill j instead
                    zf[j] = math.nextafter(zf[i], math.inf)
                    filled += 1
            heapq.heappush(heap, (float(zf[j]), j))
        done += 1
        if done % 250_000 == 0:
            progress.update(0.1 + 0.6 * done / total, f"Breaching: {done:,} of {total:,} cells")
    return zf.reshape(h, w), carved, filled


def condition(dem, out_dir: Path, *, steps=("fill_nodata", "breach"), like: str | None = None, res: float | None = None,
              streams: dict | None = None, burn_m: float = 5.0, burn_buffer_m: float = 0.0, max_breach_m: float | None = None,
              max_hole_cells: int = 2000, name: str | None = None) -> dict:
    """Run the chosen steps in order: align → fill_nodata → burn → breach → fill. Writes the conditioned DEM and the
    change from the original."""
    from .. import hydrology as H
    out_dir = Path(out_dir)
    stem = name or Path(dem).stem
    src_path = Path(dem)
    if like or res:
        import rasterio
        from rasterio.warp import Resampling, reproject
        progress.update(0.02, "Aligning the DEM")
        with rasterio.open(dem) as s:
            if like:
                with rasterio.open(like) as r:
                    crs, tr, w, h = r.crs, r.transform, r.width, r.height
            else:
                from rasterio.warp import calculate_default_transform
                tr, w, h = calculate_default_transform(s.crs, s.crs, s.width, s.height, *s.bounds, resolution=res)
                crs = s.crs
            a = np.full((h, w), np.nan, "float32")
            reproject(s.read(1, masked=True).astype("float32").filled(np.nan), a, src_transform=s.transform, src_crs=s.crs,
                      dst_transform=tr, dst_crs=crs, src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
            prof = {**s.profile, "driver": "GTiff", "crs": crs, "transform": tr, "width": w, "height": h, "dtype": "float32", "nodata": np.nan}
        src_path = out_dir / f"{stem}_aligned.tif"
        out_dir.mkdir(parents=True, exist_ok=True)
        with rasterio.open(src_path, "w", **prof) as d:
            d.write(a, 1)
    g, z0 = open_dem(src_path)
    z = z0.copy()
    info = {}
    if "fill_nodata" in steps:
        progress.update(0.05, "Filling no-data holes")
        z, n = fill_nodata(z, max_hole_cells)
        info["holes_filled_cells"] = n
        g.valid = np.isfinite(z)
    if streams:
        progress.update(0.08, "Burning the streams in")
        z, n = burn_streams(g, z, streams, burn_m, burn_buffer_m)
        info["burned_cells"] = n
    if "breach" in steps:
        progress.update(0.1, "Breaching pits")
        z, carved, nfill = breach(z, max_depth=max_breach_m)
        info["breached_cells"], info["filled_instead"] = carved, nfill
    if "fill" in steps:
        progress.update(0.75, "Filling the remaining pits")
        z = H.fill_sinks(z)[0]
    # what is left undrained (should be nothing after fill / breach)
    left = H.fill_sinks(z)[0] - z
    info["cells_still_in_pits"] = int((left[g.valid] > 1e-6).sum())
    change = np.where(np.isfinite(z0), z - z0, np.where(np.isfinite(z), 0.0, np.nan))
    info.update({"raised_cells": int((change > 1e-6).sum()), "lowered_cells": int((change < -1e-6).sum()),
                 "max_raise_m": round(float(np.nanmax(change)) if np.isfinite(change).any() else 0.0, 3),
                 "max_cut_m": round(float(-np.nanmin(change)) if np.isfinite(change).any() else 0.0, 3)})
    outs = [write(g, out_dir / f"{stem}_conditioned.tif", z, "Conditioned DEM (m)", tags={"hydro_conditioned": "1"}),
            write(g, out_dir / f"{stem}_dem_change.tif", change, "Change from the original DEM (m): + filled, − cut")]
    if like or res:
        outs.append(str(src_path))
    progress.update(1.0, "Done")
    return {"outputs": outs, "path": outs[0], **info, "size": [g.w, g.h]}
