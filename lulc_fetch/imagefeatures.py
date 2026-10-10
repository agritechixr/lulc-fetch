"""Image features from a pixel's neighbourhood, and shape operations on masks: for SAR and optical GeoAI.

    local_stats(band, windows, stats)   per pixel, over a w × w window: mean, median, std, variance, min, max, range,
                                        coefficient of variation, entropy, skewness; and edges: gradient (Sobel)
                                        and Laplacian. Texture and land cover live in this local variation,
                                        especially in SAR.
    glcm(band, window, …)               grey-level co-occurrence (Haralick 1973) per pixel, averaged over four
                                        directions: contrast, dissimilarity, homogeneity, energy, ASM, correlation,
                                        entropy, GLCM mean and variance
    morphology(array, op, …)            erosion, dilation, opening, closing, gradient, top-hat, black-hat (binary on
                                        a mask or one class, grey-level on continuous bands); and clean-up: remove
                                        small objects, fill small holes, majority filter (class maps), boundary

Every window statistic is NaN-aware (no-data pixels are left out of their neighbours' windows).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from scipy import ndimage as ndi

from . import progress

STATS = ("mean", "median", "std", "variance", "min", "max", "range", "cv", "entropy", "skewness", "gradient", "laplacian")
STAT_TITLES = {"mean": "mean", "median": "median", "std": "standard deviation", "variance": "variance", "min": "minimum", "max": "maximum",
               "range": "range", "cv": "coefficient of variation", "entropy": "entropy", "skewness": "skewness",
               "gradient": "gradient (Sobel)", "laplacian": "Laplacian"}
GLCM_FEATURES = ("contrast", "dissimilarity", "homogeneity", "energy", "asm", "correlation", "entropy", "mean", "variance")
MORPH = ("erosion", "dilation", "opening", "closing", "gradient", "tophat", "blackhat", "remove_small", "fill_holes", "majority", "boundary")
MORPH_TITLES = {"erosion": "Erosion", "dilation": "Dilation", "opening": "Opening", "closing": "Closing", "gradient": "Morphological gradient",
                "tophat": "Top-hat", "blackhat": "Black-hat", "remove_small": "Remove small objects", "fill_holes": "Fill small holes",
                "majority": "Majority filter", "boundary": "Boundary"}
MAX_PIXELS = 60_000_000


# ------------------------------------------------------------------ local statistics
def _sums(x, ok, w):
    xv = np.where(ok, x, 0.0)
    n = ndi.uniform_filter(ok.astype("float64"), w) * w * w
    s1 = ndi.uniform_filter(xv, w) * w * w
    s2 = ndi.uniform_filter(xv * xv, w) * w * w
    return n, s1, s2, xv


def local_stats(band: np.ndarray, windows=(3, 5, 7, 15), stats=("mean", "std"), *, levels: int = 32) -> list[tuple[str, np.ndarray]]:
    """[(name, array)] for each window and statistic, NaN where the pixel itself has no data."""
    x = np.asarray(band, "float64")
    ok = np.isfinite(x)
    if not ok.any():
        raise ValueError("The band has no data")
    out = []
    lo, hi = np.percentile(x[ok], [1, 99])
    q = np.clip(np.nan_to_num((x - lo) / max(hi - lo, 1e-12) * levels, nan=0).astype("int64"), 0, levels - 1)   # for the entropy
    fill = float(np.median(x[ok]))
    for w in windows:
        w = int(w) | 1
        n, s1, s2, xv = _sums(x, ok, w)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = s1 / n
            var = np.maximum(s2 / n - mean * mean, 0)
        for st in stats:
            if st == "mean":
                a = mean
            elif st == "median":
                a = ndi.median_filter(np.where(ok, x, fill), size=w)
            elif st == "std":
                a = np.sqrt(var)
            elif st == "variance":
                a = var
            elif st in ("min", "max", "range"):
                mn = ndi.minimum_filter(np.where(ok, x, np.inf), size=w)
                mx = ndi.maximum_filter(np.where(ok, x, -np.inf), size=w)
                a = mn if st == "min" else mx if st == "max" else mx - mn
            elif st == "cv":
                with np.errstate(invalid="ignore", divide="ignore"):
                    a = np.sqrt(var) / np.abs(mean)
            elif st == "entropy":   # Shannon entropy (bits) of the window's histogram of `levels` grey levels
                a = np.zeros(x.shape)
                for k in range(levels):
                    with np.errstate(invalid="ignore", divide="ignore"):
                        p = ndi.uniform_filter(((q == k) & ok).astype("float64"), w) * w * w / n
                    a -= np.where(p > 0, p * np.log2(np.where(p > 0, p, 1)), 0)
            elif st == "skewness":
                s3 = ndi.uniform_filter(np.where(ok, x, 0.0) ** 3, w) * w * w
                with np.errstate(invalid="ignore", divide="ignore"):
                    m3 = s3 / n - 3 * mean * var - mean ** 3
                    a = m3 / np.power(var, 1.5)
            elif st == "gradient":
                g = ndi.gaussian_filter(np.where(ok, x, fill), max(0.5, (w - 1) / 6)) if w > 3 else np.where(ok, x, fill)
                a = np.hypot(ndi.sobel(g, 0), ndi.sobel(g, 1)) / 8
            elif st == "laplacian":
                a = ndi.gaussian_laplace(np.where(ok, x, fill), max(0.5, (w - 1) / 6))
            else:
                raise ValueError(f"Statistic: {', '.join(STATS)}")
            out.append((f"{STAT_TITLES[st]} {w}×{w}", np.where(ok, a, np.nan).astype("float32")))
    return out


# ------------------------------------------------------------------ GLCM texture
def quantise(x: np.ndarray, levels: int) -> tuple[np.ndarray, np.ndarray]:
    ok = np.isfinite(x)
    lo, hi = np.percentile(x[ok], [1, 99]) if ok.any() else (0, 1)
    q = np.clip(np.floor((x - lo) / max(hi - lo, 1e-12) * levels), 0, levels - 1)
    return np.where(ok, q, 0).astype("int64"), ok


def _shift(a, dy, dx, fill):
    out = np.full_like(a, fill)
    h, w = a.shape
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    out[yd, xd] = a[ys, xs]
    return out


def glcm(band: np.ndarray, *, window: int = 7, distance: int = 1, levels: int = 16, features=("contrast", "homogeneity", "energy", "correlation", "entropy"),
         directions=((0, 1), (1, 1), (1, 0), (1, -1))) -> list[tuple[str, np.ndarray]]:
    """Per-pixel GLCM features over a window, symmetric and averaged over the directions (0°, 45°, 90°, 135°).
    Features that depend only on the grey-level pair (contrast, dissimilarity, homogeneity, mean, variance,
    correlation) are window means of a per-pair value; energy / ASM / entropy need the window's pair histogram."""
    q, ok = quantise(np.asarray(band, "float64"), levels)
    w = int(window) | 1
    acc = {f: np.zeros(q.shape) for f in features}
    for dy, dx in directions:
        dy, dx = dy * distance, dx * distance
        q2 = _shift(q, dy, dx, 0)
        ok2 = ok & _shift(ok, dy, dx, False)
        n = ndi.uniform_filter(ok2.astype("float64"), w)
        wm = lambda a: ndi.uniform_filter(np.where(ok2, a, 0.0), w) / np.maximum(n, 1e-12)   # noqa: E731 (window mean over valid pairs)
        i, j = q.astype("float64"), q2.astype("float64")
        d = i - j
        if "contrast" in features:
            acc["contrast"] += wm(d * d)
        if "dissimilarity" in features:
            acc["dissimilarity"] += wm(np.abs(d))
        if "homogeneity" in features:
            acc["homogeneity"] += wm(1 / (1 + d * d))
        mu = wm((i + j) / 2)
        if "mean" in features:
            acc["mean"] += mu
        if "variance" in features or "correlation" in features:
            var = np.maximum(wm((i * i + j * j) / 2) - mu * mu, 0)
            if "variance" in features:
                acc["variance"] += var
            if "correlation" in features:
                with np.errstate(invalid="ignore", divide="ignore"):
                    acc["correlation"] += np.where(var > 1e-9, (wm(i * j) - mu * mu) / var, 1.0)
        if {"energy", "asm", "entropy"} & set(features):   # the window's (symmetric) pair histogram, one pair class at a time
            lo_, hi_ = np.minimum(q, q2), np.maximum(q, q2)
            code = lo_ * levels + hi_
            asm = np.zeros(q.shape)
            ent = np.zeros(q.shape)
            for a in range(levels):
                for b in range(a, levels):
                    ind = ok2 & (code == a * levels + b)
                    if not ind.any():
                        continue
                    p = ndi.uniform_filter(ind.astype("float64"), w) / np.maximum(n, 1e-12)
                    pp = p if a == b else p / 2   # off-diagonal pairs count for (a, b) and (b, a) in the symmetric matrix
                    k = 1 if a == b else 2
                    asm += k * pp * pp
                    ent -= k * np.where(pp > 0, pp * np.log2(np.where(pp > 0, pp, 1)), 0)
            if "asm" in features:
                acc["asm"] += asm
            if "energy" in features:
                acc["energy"] += np.sqrt(asm)
            if "entropy" in features:
                acc["entropy"] += ent
    nd = len(directions)
    return [(f"GLCM {f} {w}×{w}", np.where(ok, acc[f] / nd, np.nan).astype("float32")) for f in features]


# ------------------------------------------------------------------ morphology
def structure(shape: str = "disk", size: int = 3) -> np.ndarray:
    r = max(1, int(size) // 2)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    if shape == "square":
        return np.ones((2 * r + 1, 2 * r + 1), bool)
    if shape == "cross":
        return (yy == 0) | (xx == 0)
    return yy * yy + xx * xx <= r * r + r * 0.5


def morphology(a: np.ndarray, op: str, *, size: int = 3, shape: str = "disk", binary: bool | None = None, min_px: int = 50,
               nodata_mask: np.ndarray | None = None) -> np.ndarray:
    """One operation on a 2-D array. binary: True for masks (non-zero = object), False for grey levels; None picks
    binary when the array holds only 0 / 1. The clean-up operations (remove_small, fill_holes, boundary) are binary;
    majority keeps class values."""
    se = structure(shape, size)
    a = np.asarray(a)
    if binary is None:
        binary = set(np.unique(a[np.isfinite(a)] if a.dtype.kind == "f" else a).tolist()) <= {0, 1}
    if op == "majority":   # the most common class in the neighbourhood (smooths a classification)
        vals = np.unique(a[nodata_mask] if nodata_mask is not None else a)
        vals = [v for v in vals if np.isfinite(v)] if a.dtype.kind == "f" else list(vals)
        if len(vals) > 64:
            raise ValueError("The majority filter is for class maps (at most 64 classes)")
        best = np.full(a.shape, -1.0)
        out = a.copy()
        k = se.astype("float64")
        for v in vals:
            c = ndi.convolve((a == v).astype("float64"), k, mode="nearest")
            better = c > best
            out = np.where(better, v, out)
            best = np.maximum(best, c)
        return out
    if binary or op in ("remove_small", "fill_holes", "boundary"):
        m = (a > 0) if a.dtype.kind != "f" else (np.nan_to_num(a) > 0)
        if op == "erosion":
            r = ndi.binary_erosion(m, se)
        elif op == "dilation":
            r = ndi.binary_dilation(m, se)
        elif op == "opening":
            r = ndi.binary_opening(m, se)
        elif op == "closing":
            r = ndi.binary_closing(m, se)
        elif op == "gradient":
            r = ndi.binary_dilation(m, se) & ~ndi.binary_erosion(m, se)
        elif op == "tophat":
            r = m & ~ndi.binary_opening(m, se)
        elif op == "blackhat":
            r = ndi.binary_closing(m, se) & ~m
        elif op == "remove_small":
            lab, n = ndi.label(m)
            sizes = np.bincount(lab.ravel())
            keep = sizes >= min_px
            keep[0] = False
            r = keep[lab]
        elif op == "fill_holes":
            lab, n = ndi.label(~m)
            sizes = np.bincount(lab.ravel())
            edge = np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])
            small = sizes < min_px
            small[0] = False
            small[edge] = False   # the background touching the edge isn't a hole
            r = m | small[lab]
        elif op == "boundary":
            r = m & ~ndi.binary_erosion(m, se)
        else:
            raise ValueError(f"Operation: {', '.join(MORPH)}")
        return r.astype("uint8")
    x = np.asarray(a, "float64")
    fill = np.nanmedian(x)
    x = np.where(np.isfinite(x), x, fill)
    if op == "erosion":
        r = ndi.grey_erosion(x, footprint=se)
    elif op == "dilation":
        r = ndi.grey_dilation(x, footprint=se)
    elif op == "opening":
        r = ndi.grey_opening(x, footprint=se)
    elif op == "closing":
        r = ndi.grey_closing(x, footprint=se)
    elif op == "gradient":
        r = ndi.grey_dilation(x, footprint=se) - ndi.grey_erosion(x, footprint=se)
    elif op == "tophat":
        r = x - ndi.grey_opening(x, footprint=se)
    elif op == "blackhat":
        r = ndi.grey_closing(x, footprint=se) - x
    else:
        raise ValueError(f"Operation: {', '.join(MORPH)}")
    return r


# ------------------------------------------------------------------ files
def _read(path, bands):
    with rasterio.open(path) as s:
        if s.width * s.height > MAX_PIXELS:
            raise ValueError(f"The image is too big ({s.width} × {s.height}): clip it to the area first")
        bands = bands or list(range(1, s.count + 1))
        arr = {b: s.read(b, masked=True).astype("float64").filled(np.nan) for b in bands}
        names = {b: (s.descriptions[b - 1] or f"band {b}") for b in bands}
        prof = s.profile.copy()
        tags = s.tags()
    return arr, names, prof, tags


def _write(out: Path, prof, layers: list[tuple[str, np.ndarray]], tags: dict | None = None, dtype="float32", nodata=np.nan, colormap=None):
    out.parent.mkdir(parents=True, exist_ok=True)
    p = {k: prof[k] for k in ("width", "height", "crs", "transform") if k in prof}
    p.update(driver="GTiff", count=len(layers), dtype=dtype, nodata=nodata, compress="deflate", BIGTIFF="IF_SAFER")
    if p["height"] >= 256 and p["width"] >= 256:
        p.update(tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(out, "w", **p) as d:
        for i, (n, a) in enumerate(layers, 1):
            d.write(a.astype(dtype), i)
            d.set_band_description(i, n[:120])
        if colormap:
            d.write_colormap(1, colormap)
        if tags:
            d.update_tags(**tags)
    return str(out)


def _db_if_sar(x, name, tags) -> tuple[np.ndarray, bool]:
    """Linear SAR power is put in dB first (texture of dB is what classifiers use)."""
    units = (tags.get("units") or "").lower()
    if "linear" in units or (name.upper() in ("VV", "VH", "HH", "HV") and "db" not in units):
        v = x[np.isfinite(x)]
        if v.size and np.nanmedian(v) > 0 and np.nanpercentile(v, 99) < 5:
            with np.errstate(divide="ignore", invalid="ignore"):
                return 10 * np.log10(np.where(x > 0, x, np.nan)), True
    return x, False


def run_local_stats(path, out: Path, *, bands=None, windows=(3, 5, 7, 15), stats=("mean", "std"), db: bool = True) -> dict:
    arr, names, prof, tags = _read(path, bands)
    n_out = len(arr) * len(windows) * len(stats)
    if n_out > 300:
        raise ValueError(f"That's {n_out} output bands: choose fewer bands, windows or statistics (at most 300)")
    layers, k, conv = [], 0, []
    for b, x in arr.items():
        if db:
            x, c = _db_if_sar(x, names[b], tags)
            if c:
                conv.append(names[b])
        progress.update(0.05 + 0.9 * k / len(arr), f"{names[b]}: local statistics")
        k += 1
        layers += [(f"{names[b]} {n}", a) for n, a in local_stats(x, windows, stats)]
    p = _write(Path(out), prof, layers, {"features": "local statistics", "source": Path(path).name})
    return {"path": p, "bands": [n for n, _ in layers], "converted_to_db": conv}


def run_glcm(path, out: Path, *, bands=None, window: int = 7, distance: int = 1, levels: int = 16, features=("contrast", "homogeneity", "energy", "correlation", "entropy"),
             db: bool = True) -> dict:
    arr, names, prof, tags = _read(path, bands)
    if len(arr) * len(features) > 200:
        raise ValueError("Too many output bands (at most 200): choose fewer bands or features")
    if levels > 64:
        raise ValueError("At most 64 grey levels")
    layers, conv = [], []
    for k, (b, x) in enumerate(arr.items()):
        if db:
            x, c = _db_if_sar(x, names[b], tags)
            if c:
                conv.append(names[b])
        progress.update(0.05 + 0.9 * k / len(arr), f"{names[b]}: GLCM texture")
        layers += [(f"{names[b]} {n}", a) for n, a in glcm(x, window=window, distance=distance, levels=levels, features=features)]
    p = _write(Path(out), prof, layers, {"features": "GLCM texture", "source": Path(path).name, "levels": str(levels), "distance": str(distance)})
    return {"path": p, "bands": [n for n, _ in layers], "converted_to_db": conv}


def run_morphology(path, out: Path, *, band: int = 1, op: str = "opening", size: int = 3, shape: str = "disk", value: float | None = None,
                   min_px: int = 50, iterations: int = 1) -> dict:
    """value: a class value to operate on (that class becomes the mask; the result keeps the other classes); else
    the band as it is (binary when 0 / 1, grey levels otherwise)."""
    with rasterio.open(path) as s:
        if s.width * s.height > MAX_PIXELS:
            raise ValueError("The image is too big: clip it to the area first")
        a = s.read(band)
        nodata = s.nodata
        prof = s.profile.copy()
        cmap = None
        try:
            cmap = s.colormap(band)
        except ValueError:
            pass
        tags = s.tags()
        name = s.descriptions[band - 1] or f"band {band}"
    valid = np.ones(a.shape, bool) if nodata is None else (a != nodata) & (~np.isnan(a) if a.dtype.kind == "f" else True)
    if value is not None:   # one class of a class map: operate on its mask, write the class back
        m = (a == value) & valid
        r = m.astype("uint8")
        for _ in range(max(1, iterations)):
            r = morphology(r, op, size=size, shape=shape, binary=True, min_px=min_px)
        res = a.copy()
        if op in ("erosion", "opening", "remove_small", "tophat", "boundary"):   # pixels the class lost: their neighbours' majority
            lost = m & (r == 0)
            if lost.any():
                other = np.where(m, nodata if nodata is not None else 0, a)
                filled = morphology(other, "majority", size=max(3, size), binary=False)
                res = np.where(lost, filled, res)
        gained = (r > 0) & ~m & valid
        res = np.where(gained, value, res)
        if op in ("gradient", "tophat", "blackhat", "boundary"):   # these are masks in their own right
            res = r
            cmap = None
        layers = [(f"{name}: {MORPH_TITLES[op]} of class {value:g}", res)]
        dtype = res.dtype
        nd = nodata if res is not r else 0
    else:
        x = a
        for _ in range(max(1, iterations)):
            x = morphology(x, op, size=size, shape=shape, min_px=min_px, nodata_mask=valid)
        if op not in ("majority",) and x.dtype == np.uint8:
            cmap = None
        layers = [(f"{name}: {MORPH_TITLES[op]}", np.where(valid, x, nodata if nodata is not None else 0) if x.dtype.kind != "f" else np.where(valid, x, np.nan))]
        dtype = layers[0][1].dtype
        nd = nodata if layers[0][1].dtype.kind != "f" else np.nan
    p = _write(Path(out), prof, layers, {k: v for k, v in tags.items() if k == "classes"} | {"morphology": f"{op} {shape} {size}×{size} ×{iterations}"},
               dtype=str(dtype), nodata=nd, colormap=cmap if cmap and str(dtype) == "uint8" else None)
    return {"path": p, "op": MORPH_TITLES[op]}
