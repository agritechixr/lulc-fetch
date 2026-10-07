"""Resample / reproject a raster, and enhance an image for computer vision (contrast, equalisation, CLAHE, gamma,
denoising, sharpening, edges, focal statistics, upscaling) or clean a class map (majority filter). numpy + scipy +
rasterio only (nothing else is bundled with the desktop app)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject
from scipy import ndimage

from . import progress, resample

MAX_PIXELS = 60_000_000


def _classes(src, band: int = 1) -> bool:
    """A class map: integer values with a colour table or class names (as the app's class rasters have), or a single
    8-bit band with only a few distinct values."""
    if not np.issubdtype(np.dtype(src.dtypes[band - 1]), np.integer):
        return False
    try:
        src.colormap(band)
        return True
    except ValueError:
        pass
    if src.tags().get("classes"):
        return True
    if src.count == 1 and src.dtypes[0] == "uint8":
        f = max(1, max(src.width, src.height) // 512)
        sample = src.read(1, out_shape=(max(1, src.height // f), max(1, src.width // f)))
        return len(np.unique(sample)) <= 32
    return False


# ------------------------------------------------------------------ resample / reproject
def resample_raster(path, out: Path, *, res: float | None = None, scale: float | None = None, crs: str | None = None,
                    method: str | None = None) -> dict:
    """A raster on a new grid: a pixel size (`res`, in the target CRS's units, metres for UTM), or a factor (`scale`:
    2 = pixels twice as small, 0.5 = twice as big), and / or another CRS (e.g. "EPSG:32643"). Method: any of
    lulc_fetch.resample.METHODS (class maps default to nearest / mode, values to bilinear / average)."""
    with rasterio.open(path) as src:
        dst_crs = crs or src.crs
        if dst_crs is None:
            raise ValueError("The raster has no coordinate system")
        classes = _classes(src)
        if res:
            tr, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds, resolution=res)
        else:
            tr, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
            if scale and scale != 1:
                w, h = max(1, round(w * scale)), max(1, round(h * scale))
                tr = tr * tr.scale(1 / scale)
        if w * h * src.count > MAX_PIXELS * 4:
            raise ValueError(f"The result would be {w:,} × {h:,} pixels → choose a coarser pixel size or a smaller factor")
        shrinking = (abs(tr.a) > abs(src.transform.a) * 1.01) if src.crs == dst_crs else False
        name = method or resample.suggest(classes, shrinking)
        m = resample.get(name)
        prof = src.profile.copy()
        prof.update(driver="GTiff", crs=dst_crs, transform=tr, width=w, height=h, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
        if np.issubdtype(np.dtype(src.dtypes[0]), np.integer) and name in ("bilinear", "cubic", "bicubic", "cubic_spline", "lanczos", "average") and not classes:
            prof.update(dtype="float32", nodata=src.nodata if src.nodata is not None else None)   # interpolated values aren't whole numbers
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, "w", **prof) as d:
            for b in range(1, src.count + 1):
                dst = np.zeros((h, w), dtype=prof["dtype"])
                reproject(rasterio.band(src, b), dst, src_transform=src.transform, src_crs=src.crs, dst_transform=tr, dst_crs=dst_crs,
                          resampling=m, src_nodata=src.nodata, dst_nodata=src.nodata)
                d.write(dst, b)
                d.set_band_description(b, src.descriptions[b - 1] or f"Band {b}")
                progress.update(b / src.count, f"Band {b} of {src.count}")
            if classes:
                try:
                    d.write_colormap(1, src.colormap(1))
                except ValueError:
                    pass
            d.update_tags(**src.tags())
    return {"path": str(out), "size": [w, h], "res": [abs(tr.a), abs(tr.e)], "method": name}


# ------------------------------------------------------------------ enhancement
def _stretch(a, lo=2, hi=98):
    v = a[np.isfinite(a)]
    if not v.size:
        return a
    p1, p2 = np.percentile(v, [lo, hi])
    return np.clip((a - p1) / max(p2 - p1, 1e-12), 0, 1)


def _equalize(a, bins: int = 1024):
    v = a[np.isfinite(a)]
    if not v.size:
        return a
    hist, edges = np.histogram(v, bins=bins)
    cdf = np.cumsum(hist).astype("float64")
    cdf /= cdf[-1]
    return np.interp(a, (edges[:-1] + edges[1:]) / 2, cdf)


def _clahe(a, tiles: int = 8, clip: float = 0.01, bins: int = 256):
    """Contrast-limited adaptive histogram equalisation: equalised per tile (histograms clipped at `clip` of the
    tile's pixels, the excess spread evenly), blended bilinearly between tile centres."""
    x = _stretch(a, 0.5, 99.5)
    h, w = x.shape
    ty, tx = max(1, min(tiles, h // 8)), max(1, min(tiles, w // 8))
    ys, xs = np.linspace(0, h, ty + 1).astype(int), np.linspace(0, w, tx + 1).astype(int)
    luts = np.zeros((ty, tx, bins))
    for i in range(ty):
        for j in range(tx):
            t = x[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
            t = t[np.isfinite(t)]
            hist = np.histogram(t, bins=bins, range=(0, 1))[0].astype("float64") if t.size else np.ones(bins)
            limit = max(1.0, clip * hist.sum())
            excess = np.clip(hist - limit, 0, None).sum()
            hist = np.minimum(hist, limit) + excess / bins
            cdf = np.cumsum(hist)
            luts[i, j] = cdf / cdf[-1]
    idx = np.clip((np.nan_to_num(x) * (bins - 1)).astype(int), 0, bins - 1)
    cy, cx = (ys[:-1] + ys[1:]) / 2, (xs[:-1] + xs[1:]) / 2   # tile centres
    fy = np.clip(np.interp(np.arange(h), cy, np.arange(ty)), 0, ty - 1)
    fx = np.clip(np.interp(np.arange(w), cx, np.arange(tx)), 0, tx - 1)
    y0, x0 = np.floor(fy).astype(int), np.floor(fx).astype(int)
    y1, x1 = np.minimum(y0 + 1, ty - 1), np.minimum(x0 + 1, tx - 1)
    wy, wx = (fy - y0)[:, None], (fx - x0)[None, :]
    Y0, Y1, X0, X1 = y0[:, None], y1[:, None], x0[None, :], x1[None, :]
    v = ((luts[Y0, X0, idx] * (1 - wx) + luts[Y0, X1, idx] * wx) * (1 - wy)
         + (luts[Y1, X0, idx] * (1 - wx) + luts[Y1, X1, idx] * wx) * wy)
    return np.where(np.isfinite(x), v, np.nan)


def _filled(a):
    """NaNs filled with the nearest value (so filters don't spread holes); the mask to put them back."""
    bad = ~np.isfinite(a)
    if not bad.any():
        return a, bad
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    return a[tuple(idx)], bad


OPS = ("stretch", "equalize", "clahe", "gamma", "median", "gaussian", "sharpen", "sobel", "laplacian", "focal_mean", "focal_std",
       "focal_min", "focal_max", "majority")


def enhance(path, out: Path, steps: list[dict], *, bands: list[int] | None = None, upscale: int = 1, upscale_method: str = "cubic") -> dict:
    """Image enhancement, steps applied in order to every band chosen (each step: {op, …}):
    stretch {low, high} percentiles · equalize · clahe {tiles, clip} · gamma {gamma} · median {size} · gaussian
    {sigma} · sharpen {sigma, amount} (unsharp mask) · sobel (edge strength) · laplacian (edges) · focal_mean / std /
    min / max {size} · majority {size} (class maps: each pixel takes the most common class around it).
    upscale: 2 or 4 = the image enlarged first with `upscale_method` (cubic, lanczos…)."""
    if not steps and upscale == 1:
        raise ValueError("Choose at least one step")
    for s in steps:
        if s.get("op") not in OPS:
            raise ValueError(f"Unknown step {s.get('op')!r}; one of {', '.join(OPS)}")
    with rasterio.open(path) as src:
        bl = bands or list(range(1, src.count + 1))
        if any(not 1 <= b <= src.count for b in bl):
            raise ValueError(f"The raster has {src.count} bands")
        if src.width * src.height * upscale * upscale > MAX_PIXELS:
            raise ValueError("The image is too big for this → clip it first, or don't enlarge it")
        tr, w, h = src.transform, src.width, src.height
        if upscale > 1:
            w, h, tr = w * upscale, h * upscale, src.transform * src.transform.scale(1 / upscale)
        classes = _classes(src, bl[0])
        out_bands = []
        for n, b in enumerate(bl):
            a = src.read(b, masked=True).astype("float64").filled(np.nan)
            if upscale > 1:
                big = np.full((h, w), np.nan)
                reproject(a, big, src_transform=src.transform, src_crs=src.crs, dst_transform=tr, dst_crs=src.crs,
                          resampling=resample.get(upscale_method), src_nodata=np.nan, dst_nodata=np.nan)
                a = big
            for s in steps:
                op = s["op"]
                if op == "stretch":
                    a = _stretch(a, float(s.get("low", 2)), float(s.get("high", 98)))
                elif op == "equalize":
                    a = _equalize(a)
                elif op == "clahe":
                    a = _clahe(a, int(s.get("tiles", 8)), float(s.get("clip", 0.01)))
                elif op == "gamma":
                    a = np.power(np.clip(_stretch(a, 0, 100) if np.nanmax(a) > 1 else a, 0, None), 1 / max(float(s.get("gamma", 1.2)), 0.05))
                else:
                    f, bad = _filled(a)
                    size = max(1, int(s.get("size", 3)))
                    if op == "median":
                        f = ndimage.median_filter(f, size=size)
                    elif op == "gaussian":
                        f = ndimage.gaussian_filter(f, sigma=float(s.get("sigma", 1)))
                    elif op == "sharpen":   # unsharp mask: the image plus `amount` × (image − blurred)
                        f = f + float(s.get("amount", 1)) * (f - ndimage.gaussian_filter(f, sigma=float(s.get("sigma", 1.5))))
                    elif op == "sobel":
                        f = np.hypot(ndimage.sobel(f, axis=0), ndimage.sobel(f, axis=1))
                    elif op == "laplacian":
                        f = ndimage.laplace(f)
                    elif op == "focal_mean":
                        f = ndimage.uniform_filter(f, size=size)
                    elif op == "focal_std":
                        m = ndimage.uniform_filter(f, size=size)
                        f = np.sqrt(np.clip(ndimage.uniform_filter(f * f, size=size) - m * m, 0, None))
                    elif op == "focal_min":
                        f = ndimage.minimum_filter(f, size=size)
                    elif op == "focal_max":
                        f = ndimage.maximum_filter(f, size=size)
                    elif op == "majority":
                        vals = np.unique(f[np.isfinite(f)]).astype(int)[:64]
                        counts = np.stack([ndimage.uniform_filter((f == v).astype("float32"), size=size) for v in vals])
                        f = vals[np.argmax(counts, axis=0)].astype("float64")
                    a = np.where(bad, np.nan, f)
            out_bands.append(a)
            progress.update((n + 1) / len(bl), f"Band {b}: {len(steps)} step(s)")
        keep_classes = classes and all(s["op"] in ("majority", "focal_min", "focal_max", "median") for s in steps)
        prof = src.profile.copy()
        prof.update(driver="GTiff", count=len(out_bands), width=w, height=h, transform=tr, compress="deflate", tiled=True, blockxsize=256, blockysize=256,
                    **({} if keep_classes else {"dtype": "float32", "nodata": -9999.0}))
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, "w", **prof) as d:
            for i, (b, a) in enumerate(zip(bl, out_bands), start=1):
                if keep_classes:
                    d.write(np.nan_to_num(a, nan=src.nodata or 0).astype(prof["dtype"]), i)
                else:
                    d.write(np.where(np.isfinite(a), a, -9999.0).astype("float32"), i)
                d.set_band_description(i, src.descriptions[b - 1] or f"Band {b}")
            if keep_classes:
                try:
                    d.write_colormap(1, src.colormap(bl[0]))
                except ValueError:
                    pass
                d.update_tags(**src.tags())
    return {"path": str(out), "bands": len(out_bands), "steps": [s["op"] for s in steps], "upscale": upscale}
