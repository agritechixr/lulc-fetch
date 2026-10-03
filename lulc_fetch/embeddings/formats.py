"""Convert embeddings between number formats: 8-bit ↔ 16 / 32-bit float, for storage or for training.

    float32      4 bytes per value, exact: what models train on
    float16      2 bytes, about 3 significant digits: half the size, practically lossless for embeddings
    int8-aef     1 byte, AlphaEarth's own coding: q = round(sign(x)·√|x|·127.5), value = sign(q)·(q/127.5)², −128 = no data.
                 Fine near 0, coarser near ±1; for unit-length vectors (values −1…1) such as AlphaEarth's
    int8-scaled  1 byte, linear per band: q = round(x / scale), scale = largest |value| / 127, −128 = no data. Works for any
                 embedding (e.g. TESSERA, values beyond ±1); the scales are stored as the bands' GDAL scale, so GIS software
                 and LULC Fetch read real values

The input format is detected (data type, band scales, the file's tags, AlphaEarth band names A00…). Output is always
north-up (Google's AlphaEarth tiles are stored bottom-up), tiled and compressed, and works in strips so large tiles fit in
memory. Optionally every vector is scaled to unit length (L2), which helps cosine-based methods (k-NN, SAM, similarity).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from .. import progress

log = logging.getLogger(__name__)

FORMATS = {
    "float32": {"title": "32-bit float", "bytes": 4, "dtype": "float32",
                "about": "Exact. What models train on; the easiest to use everywhere."},
    "float16": {"title": "16-bit float", "bytes": 2, "dtype": "float16",
                "about": "Half the size of 32-bit, about 3 significant digits: practically lossless for embeddings. "
                         "Opening it elsewhere needs GDAL 3.11 or newer."},
    "int8-aef": {"title": "8-bit, AlphaEarth coding", "bytes": 1, "dtype": "int8",
                 "about": "A quarter of 32-bit, the same coding as Google's AlphaEarth files (square-root steps: finer near 0). "
                          "For unit-length vectors with values −1…1. For storage: convert back to 32-bit float to train or analyse."},
    "int8-scaled": {"title": "8-bit, scaled per band", "bytes": 1, "dtype": "int8",
                    "about": "A quarter of 32-bit, for any embedding (e.g. TESSERA): even steps of (largest value / 127) per band, "
                             "stored in the file. For storage: convert back to 32-bit float to train or analyse."},
}
NODATA_INT8 = -128
STRIP = 512   # rows per strip


def detect(path: str | Path) -> dict:
    """The number format of an embedding GeoTIFF, its size, and what it can be converted to."""
    import rasterio
    with rasterio.open(path) as src:
        dt, n = src.dtypes[0], src.count
        tags = src.tags()
        scales = src.scales
        names = [d or "" for d in src.descriptions]
        quant = tags.get("embedding_quantization")
        if dt == "int8":
            if quant == "int8-scaled" or any(s != 1 for s in scales):
                fmt = "int8-scaled"
            elif quant == "int8-aef" or src.nodata == NODATA_INT8 or sum(nm.startswith("A") for nm in names) > n / 2 or n == 64:
                fmt = "int8-aef"
            else:
                fmt = "int8-aef"
        elif dt in ("float32", "float64"):
            fmt = "float32"
        elif dt == "float16":
            fmt = "float16"
        else:
            fmt = None
        info = {"format": fmt, "dtype": dt, "bands": n, "width": src.width, "height": src.height,
                "size_mb": round(Path(path).stat().st_size / 1e6, 1), "raw_mb": round(src.width * src.height * n * np.dtype(dt).itemsize / 1e6, 1),
                "north_up": src.transform.e < 0, "embedding": tags.get("embedding_title") or (
                    "AlphaEarth" if fmt == "int8-aef" and n == 64 else None),
                "targets": [k for k in FORMATS if k != fmt]}
        info["estimates"] = {k: round(src.width * src.height * n * f["bytes"] / 1e6, 1) for k, f in FORMATS.items()}
    if fmt is None:
        info["error"] = f"{dt} isn't an embedding number format this tool knows (8-bit or float)"
    return info


# ------------------------------------------------------------------ decoding / encoding (bands × rows × cols)

def decode(a: np.ndarray, fmt: str, scales=None) -> np.ndarray:
    """Real values as float32, NaN = no data."""
    if fmt in ("float32", "float16"):
        return a.astype(np.float32)
    v = a.astype(np.float32)
    nod = (a == NODATA_INT8).all(axis=0)
    if fmt == "int8-aef":
        v = np.sign(v) * (v / 127.5) ** 2
    else:
        v = v * np.asarray(scales, np.float32).reshape(-1, 1, 1)
    v[:, nod] = np.nan
    return v


def encode(v: np.ndarray, fmt: str, scales=None) -> np.ndarray:
    """float32 values (NaN = no data) → the stored numbers of fmt."""
    if fmt == "float32":
        return v.astype(np.float32)
    if fmt == "float16":
        return v.astype(np.float16)
    nod = np.isnan(v).any(axis=0)
    w = np.nan_to_num(v)
    if fmt == "int8-aef":
        q = np.sign(w) * np.sqrt(np.abs(w)) * 127.5
    else:
        q = w / np.asarray(scales, np.float32).reshape(-1, 1, 1)
    q = np.clip(np.rint(q), -127, 127).astype(np.int8)
    q[:, nod] = NODATA_INT8
    return q


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=0, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, v / n, v)


def convert(path: str | Path, out_path: str | Path, to: str, normalise: bool = False) -> dict:
    """Write path in the format `to` (see FORMATS). Returns sizes and, for lossy targets, how much the values changed."""
    import rasterio
    from rasterio.windows import Window

    t0 = time.time()
    if to not in FORMATS:
        raise ValueError(f"Unknown format {to}")
    info = detect(path)
    src_fmt = info["format"]
    if src_fmt is None:
        raise ValueError(info["error"])
    if src_fmt == to and not normalise:
        raise ValueError(f"The layer is already {FORMATS[to]['title']}")
    with rasterio.open(path) as src:
        n, h, w = src.count, src.height, src.width
        src_scales = list(src.scales)
        flip = src.transform.e > 0   # stored bottom-up (Google's AlphaEarth tiles): write north-up
        tf = src.transform if not flip else src.transform * rasterio.Affine.translation(0, h) * rasterio.Affine.scale(1, -1)
        strips = [(r, min(STRIP, h - r)) for r in range(0, h, STRIP)]

        def strip(r, nr):
            a = src.read(window=Window(0, r, w, nr))
            v = decode(a, src_fmt, src_scales)
            return _unit(v) if normalise else v

        out_scales = [1.0] * n
        if to == "int8-scaled":   # pass 1: the largest |value| of each band
            mx = np.zeros(n, np.float64)
            for i, (r, nr) in enumerate(strips):
                progress.update(0.4 * i / len(strips), "Finding each band's range")
                v = strip(r, nr)
                with np.errstate(invalid="ignore"):
                    mx = np.maximum(mx, np.nan_to_num(np.nanmax(np.abs(v), axis=(1, 2)), nan=0))
            out_scales = [float(m / 127) if m > 0 else 1.0 for m in mx]
        if to == "int8-aef":
            v0 = strip(*strips[len(strips) // 2])
            big = np.nanmax(np.abs(v0)) if np.isfinite(v0).any() else 0
            if big > 1.01:
                raise ValueError(f"Values go up to {big:.2f}: the AlphaEarth coding is for values −1…1. Tick “Make every vector "
                                 "unit length” or choose “8-bit, scaled per band”")
        dt = FORMATS[to]["dtype"]
        prof = dict(driver="GTiff", width=w, height=h, count=n, dtype=dt, crs=src.crs, transform=tf, tiled=True, blockxsize=256,
                    blockysize=256, compress="zstd", predictor={"int8": 2, "float32": 3}.get(dt, 1),   # GDAL: no float predictor for float16 interleave="pixel", BIGTIFF="IF_SAFER",
                    nodata=NODATA_INT8 if dt == "int8" else np.nan)
        names = [d or f"E{i:03d}" for i, d in enumerate(src.descriptions)]
        tags = {k: v for k, v in src.tags().items() if not k.startswith("embedding_quantization")}
        rng = np.random.default_rng(0)
        errs, cos = [], []
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out_path, "w", **prof) as dst:
            for i, (r, nr) in enumerate(strips):
                progress.update((0.4 if to == "int8-scaled" else 0) + (0.6 if to == "int8-scaled" else 1) * i / len(strips),
                                f"Converting rows {r + 1}–{r + nr} of {h}")
                v = strip(r, nr)
                q = encode(v, to, out_scales)
                if flip:
                    dst.write(q[:, ::-1, :], window=Window(0, h - r - nr, w, nr))
                else:
                    dst.write(q, window=Window(0, r, w, nr))
                ok = np.isfinite(v).all(axis=0)
                if ok.any():   # how much the values changed, on a sample of pixels
                    idx = np.flatnonzero(ok)
                    pick = rng.choice(idx, min(2000, idx.size), replace=False)
                    a = v.reshape(n, -1)[:, pick]
                    b = decode(q, to, out_scales).reshape(n, -1)[:, pick]
                    errs.append(np.abs(a - b).max(axis=0))
                    na, nb = np.linalg.norm(a, axis=0), np.linalg.norm(b, axis=0)
                    with np.errstate(invalid="ignore", divide="ignore"):
                        cos.append((a * b).sum(0) / (na * nb))
            for i, nm in enumerate(names, 1):
                dst.set_band_description(i, nm)
            if to == "int8-scaled":
                dst.scales = out_scales
                dst.offsets = [0.0] * n
            unit = normalise or tags.get("embedding_unit_length") == "true"
            dst.update_tags(**{**tags, "embedding_quantization": to, "embedding_unit_length": str(unit).lower()})
    e = np.concatenate(errs) if errs else np.array([0.0])
    c = np.concatenate(cos) if cos else np.array([1.0])
    c = c[np.isfinite(c)]
    res = {"from": src_fmt, "to": to, "normalised": normalise, "flipped": bool(flip), "bands": n, "width": w, "height": h,
           "size_in_mb": info["size_mb"], "size_out_mb": round(Path(out_path).stat().st_size / 1e6, 1),
           "max_error": round(float(e.max()), 6), "mean_error": round(float(e.mean()), 6),
           "cosine_min": round(float(c.min()), 6) if c.size else None, "cosine_mean": round(float(c.mean()), 6) if c.size else None,
           "lossless": to in ("float32",) or (to == "float16" and float(e.max()) < 1e-3) or src_fmt == to,
           "seconds": round(time.time() - t0, 1), "path": str(out_path)}
    log.info("%s → %s%s: %.1f → %.1f MB, largest change %.4g, vectors' cosine similarity ≥ %.4f", FORMATS[src_fmt]["title"],
             FORMATS[to]["title"], " (unit length)" if normalise else "", res["size_in_mb"], res["size_out_mb"], res["max_error"],
             res["cosine_min"] or 1)
    progress.update(1, "Done")
    return res
