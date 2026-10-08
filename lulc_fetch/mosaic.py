"""Mosaic / merge rasters: neighbouring tiles or scenes joined into one image.

    mosaic(paths, out, method="blend", balance="overlap", ...) -> {"path", "width", "height", ...}

Methods (where images overlap):
    blend    smooth seams: each image fades out towards its edges over a blend zone (feathering), so overlaps are a
             distance-weighted mix and no tile edge shows (default)
    first    the first image on top (in the order given), the others fill the gaps
    last     the last image on top
    mean / median / min / max   per pixel, of all the images there (median: also removes clouds seen in only one)
    mode     the most common value (class maps)

Colour balance ("overlap", default for imagery): before joining, each image's bands get a gain and offset that make it
match the images it overlaps (mean and spread in the overlap), working outwards from the first image, so scenes of
different dates or light don't show as patches. "none" keeps the values as they are.

Class maps (categorical=True): nearest-neighbour resampling, no blending or balancing (methods first, last, mode).

The output grid: the first image's coordinate system and pixel size (or `crs` / `res`), covering all the images. It is
written block by block, so large mosaics don't need to fit in memory.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

from . import progress, resample

METHODS = ("blend", "first", "last", "mean", "median", "min", "max", "mode")
CLASS_METHODS = ("first", "last", "mode")
MAX_OUT_PIXELS = 1_500_000_000   # per band: bigger → resample or clip first
COARSE = 1200                    # the long side of the overview grid used for blend weights and colour balance
BLOCK = 1024


def _grid(srcs, crs=None, res=None):
    """The output grid: CRS, transform, width, height covering every source."""
    first = srcs[0]
    dst_crs = CRS.from_user_input(crs) if crs else first.crs
    if dst_crs is None:
        raise ValueError(f"{Path(first.name).name} has no coordinate system")
    boxes = []
    for s in srcs:
        if s.crs is None:
            raise ValueError(f"{Path(s.name).name} has no coordinate system")
        boxes.append(transform_bounds(s.crs, dst_crs, *s.bounds, densify_pts=21) if s.crs != dst_crs else s.bounds)
    if res:
        rx = ry = float(res)
    elif first.crs == dst_crs:
        rx, ry = abs(first.transform.a), abs(first.transform.e)
    else:   # the first image's pixel size, in the new CRS
        b = boxes[0]
        rx, ry = (b[2] - b[0]) / first.width, (b[3] - b[1]) / first.height
    west, south = min(b[0] for b in boxes), min(b[1] for b in boxes)
    east, north = max(b[2] for b in boxes), max(b[3] for b in boxes)
    if first.crs == dst_crs and not res:   # keep the first image's pixel grid (no half-pixel shifts)
        ox, oy = first.transform.c, first.transform.f
        west = ox + math.floor((west - ox) / rx) * rx
        north = oy - math.floor((oy - north) / ry) * ry
    width, height = math.ceil((east - west) / rx - 1e-9), math.ceil((north - south) / ry - 1e-9)
    return dst_crs, from_origin(west, north, rx, ry), width, height


def _warp(src, bands: list[int], dst_transform, dst_crs, shape, method) -> np.ndarray:
    """The source's bands on a target grid (float64, NaN outside it or where it has no data)."""
    out = np.full((len(bands), *shape), np.nan)
    nod = src.nodata
    for i, b in enumerate(bands):
        reproject(rasterio.band(src, b), out[i], src_transform=src.transform, src_crs=src.crs, dst_transform=dst_transform,
                  dst_crs=dst_crs, src_nodata=nod, dst_nodata=np.nan, resampling=method)
    if nod is None and np.issubdtype(np.dtype(src.dtypes[0]), np.integer):   # no nodata set: 0 in every band is fill
        out[:, np.all(out == 0, axis=0)] = np.nan
    return out


def _coarse_grid(transform, width, height):
    f = max(1.0, max(width, height) / COARSE)
    w, h = max(1, round(width / f)), max(1, round(height / f))
    return transform * transform.scale(width / w, height / h), w, h


def _feather(valid: np.ndarray, zone_px: float) -> np.ndarray:
    """Blend weight of one image on the overview grid: 0 at its edges rising to 1 at zone_px inside (smooth)."""
    from scipy.ndimage import distance_transform_edt
    d = distance_transform_edt(np.pad(valid, 1, constant_values=False))[1:-1, 1:-1]
    w = np.clip(d / max(zone_px, 1e-6), 0, 1)
    w = w * w * (3 - 2 * w)   # smoothstep: no visible kink where the fade starts
    return np.where(valid, np.maximum(w, 1e-4), 0.0)


def _balance(coarse: list[np.ndarray], order: list[int]) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per image and band (gain, offset) matching each image to those already placed, over their overlap (mean and
    standard deviation), placing next the image that overlaps the placed ones most."""
    n, nb = len(coarse), coarse[0].shape[0]
    fix = [(np.ones(nb), np.zeros(nb)) for _ in range(n)]
    placed = coarse[order[0]].copy()
    todo = list(order[1:])
    while todo:
        have = np.isfinite(placed).all(0)
        overlaps = [int((have & np.isfinite(coarse[i]).all(0)).sum()) for i in todo]
        k = int(np.argmax(overlaps))
        i = todo.pop(k)
        a = coarse[i]
        both = have & np.isfinite(a).all(0)
        if overlaps[k] >= 50:
            g, o = np.ones(nb), np.zeros(nb)
            for b in range(nb):
                x, y = a[b][both], placed[b][both]
                sx, sy = x.std(), y.std()
                g[b] = np.clip(sy / sx, 0.5, 2.0) if sx > 1e-12 else 1.0
                o[b] = y.mean() - g[b] * x.mean()
            fix[i] = (g, o)
            a = a * g[:, None, None] + o[:, None, None]
        new = np.isfinite(a).all(0) & ~have
        placed[:, new] = a[:, new]
    return fix


def mosaic(paths: list, out: str | Path, *, method: str = "blend", balance: str = "overlap", categorical: bool = False,
           blend_px: int = 64, crs: str | None = None, res: float | None = None, resampling: str | None = None) -> dict:
    """Join rasters (the same bands, in the same order) into one GeoTIFF. See the module's notes for the methods."""
    if len(paths) < 2:
        raise ValueError("Choose at least two rasters to join")
    if method not in METHODS:
        raise ValueError(f"method: {', '.join(METHODS)}")
    if categorical and method not in CLASS_METHODS:
        raise ValueError(f"Class maps are joined with {', '.join(CLASS_METHODS)} (no blending: classes can't be averaged)")
    if method == "mode" and not categorical:
        raise ValueError("Most common value (mode) is for class maps")
    out = Path(out)
    srcs = [rasterio.open(p) for p in paths]
    try:
        nb = srcs[0].count
        for s in srcs[1:]:
            if s.count != nb:
                raise ValueError(f"{Path(s.name).name} has {s.count} bands and {Path(srcs[0].name).name} {nb}: join rasters with the same bands")
        dst_crs, dst_tr, width, height = _grid(srcs, crs, res)
        if width * height > MAX_OUT_PIXELS:
            raise ValueError(f"The mosaic would be {width:,} × {height:,} pixels: make the pixels bigger (pixel size) or clip the rasters first")
        rs = Resampling.nearest if categorical else resample.get(resampling, Resampling.bilinear)
        bands = list(range(1, nb + 1))
        in_dtype = np.dtype(srcs[0].dtypes[0])
        integer = np.issubdtype(in_dtype, np.integer)
        balance = "none" if categorical else balance
        smooth = method == "blend"

        # 1 · the overview grid: footprints (blend weights) and values (colour balance)
        progress.update(0.02, "Reading the images' overviews")
        ctr, cw, ch = _coarse_grid(dst_tr, width, height)
        coarse = [_warp(s, bands, ctr, dst_crs, (ch, cw), Resampling.average if not categorical else Resampling.nearest) for s in srcs]
        foot = [np.isfinite(c).all(0) for c in coarse]
        if not any(f.any() for f in foot):
            raise ValueError("The rasters have no data")
        fix = _balance(coarse, list(range(len(srcs)))) if balance == "overlap" else [(np.ones(nb), np.zeros(nb))] * len(srcs)
        zone = blend_px * cw / width   # the blend zone in overview pixels
        weights = [_feather(f, zone) for f in foot] if smooth else None

        # 2 · the mosaic, block by block
        prof = srcs[0].profile.copy()
        out_dtype = in_dtype if (integer or categorical) else np.dtype("float32")
        nodata = srcs[0].nodata if srcs[0].nodata is not None else (0 if integer else np.nan)
        prof.update(driver="GTiff", crs=dst_crs, transform=dst_tr, width=width, height=height, count=nb, dtype=out_dtype.name,
                    nodata=nodata, compress="deflate", tiled=True, blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER")
        prof.pop("photometric", None)
        lo, hi = (np.iinfo(out_dtype).min, np.iinfo(out_dtype).max) if np.issubdtype(out_dtype, np.integer) else (-np.inf, np.inf)
        out.parent.mkdir(parents=True, exist_ok=True)
        blocks = [(r, c) for r in range(0, height, BLOCK) for c in range(0, width, BLOCK)]
        filled = 0
        cmap = None
        if categorical:
            try:
                cmap = srcs[0].colormap(1)
                prof["photometric"] = "palette"
            except ValueError:
                pass
        with rasterio.open(out, "w", **prof) as dst:
            if cmap:   # before the pixels: GDAL can't change it after
                dst.write_colormap(1, cmap)
            for k, (r0, c0) in enumerate(blocks):
                progress.update(0.05 + 0.93 * k / len(blocks), f"Joining: block {k + 1} of {len(blocks)}")
                win = Window(c0, r0, min(BLOCK, width - c0), min(BLOCK, height - r0))
                wtr, shape = window_transform(win, dst_tr), (int(win.height), int(win.width))
                stack, wts = [], []
                for i, s in enumerate(srcs):
                    a = _warp(s, bands, wtr, dst_crs, shape, rs)
                    if not np.isfinite(a).any():
                        continue
                    g, o = fix[i]
                    a = a * g[:, None, None] + o[:, None, None]
                    stack.append(a)
                    if smooth:   # the overview weight, smoothly enlarged to the block
                        w = np.zeros(shape)
                        reproject(weights[i], w, src_transform=ctr, src_crs=dst_crs, dst_transform=wtr, dst_crs=dst_crs,
                                  resampling=Resampling.bilinear)
                        wts.append(np.where(np.isfinite(a).all(0), np.maximum(w, 1e-4), 0.0))
                res_ = np.full((nb, *shape), np.nan)
                if stack:
                    S = np.stack(stack)   # images × bands × rows × cols
                    ok = np.isfinite(S).all(1)
                    if smooth:
                        W = np.stack(wts)[:, None]
                        tot = W.sum(0)
                        with np.errstate(invalid="ignore", divide="ignore"):
                            res_ = np.where(tot > 0, np.nansum(np.where(np.isfinite(S), S, 0) * W, 0) / tot, np.nan)
                    elif method in ("first", "last"):
                        for a, m in (zip(stack, ok) if method == "last" else zip(stack[::-1], ok[::-1])):
                            res_[:, m] = a[:, m]
                    elif method == "mode":
                        from scipy.stats import mode as _mode
                        Sm = np.where(ok[:, None], S, np.nan)
                        res_ = _mode(Sm, axis=0, nan_policy="omit").mode
                        res_ = np.where(ok.any(0)[None], res_, np.nan)
                    else:
                        Sm = np.where(ok[:, None], S, np.nan)
                        with np.errstate(all="ignore"), warnings.catch_warnings():
                            warnings.simplefilter("ignore", RuntimeWarning)   # all-empty pixels
                            res_ = {"mean": np.nanmean, "median": np.nanmedian, "min": np.nanmin, "max": np.nanmax}[method](Sm, axis=0)
                have = np.isfinite(res_).all(0)
                filled += int(have.sum())
                if np.issubdtype(out_dtype, np.integer):
                    block = np.clip(np.rint(np.nan_to_num(res_, nan=nodata)), lo, hi).astype(out_dtype)
                    if not categorical and nodata == 0:   # real values that round to 0 would read as empty
                        block[:, have & np.all(block == 0, axis=0)] = 1
                else:
                    block = np.where(have[None], res_, nodata).astype(out_dtype)
                dst.write(block, window=win)
            for b in bands:
                dst.set_band_description(b, srcs[0].descriptions[b - 1] or f"Band {b}")
            dst.update_tags(**srcs[0].tags())
            dst.update_tags(mosaic_method=method, mosaic_balance=balance, mosaic_sources=len(srcs))
        progress.update(1, "Done")
        px = abs(dst_tr.a) * abs(dst_tr.e)
        return {"path": str(out), "width": width, "height": height, "bands": nb, "images": len(srcs), "method": method,
                "balance": balance, "crs": dst_crs.to_string(), "res": abs(dst_tr.a),
                "covered_pct": round(100 * filled / (width * height), 1),
                "gains": [[round(float(x), 4) for x in g] for g, _ in fix], "area_km2": round(filled * px / 1e6, 3) if dst_crs.is_projected else None}
    finally:
        for s in srcs:
            s.close()
