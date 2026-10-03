"""Explore an embedding GeoTIFF: a colour view (PCA → RGB) and similar places (cosine similarity to clicked points).
Works on any embedding raster, also ones downloaded elsewhere."""

from __future__ import annotations

import math

import numpy as np


def _read(path: str):
    import rasterio
    with rasterio.open(path) as src:
        return src.read().astype(np.float32), src.profile


def colour_view(path: str, out_path: str, sample: int = 200_000) -> dict:
    """The three main directions of variation (PCA) as red, green and blue: similar places get similar colours."""
    import rasterio
    from sklearn.decomposition import PCA
    data, prof = _read(path)
    d, h, w = data.shape
    flat = data.reshape(d, -1).T
    ok = np.isfinite(flat).all(1)
    if ok.sum() < 10:
        raise ValueError("The layer has too few pixels with values")
    rng = np.random.default_rng(0)
    idx = np.flatnonzero(ok)
    fit = flat[rng.choice(idx, min(sample, idx.size), replace=False)]
    pca = PCA(3, random_state=0).fit(fit)
    rgb = np.zeros((3, h * w), np.uint8)
    comp = pca.transform(flat[ok])
    lo, hi = np.percentile(comp, 2, axis=0), np.percentile(comp, 98, axis=0)
    rgb[:, ok] = (np.clip((comp - lo) / np.maximum(hi - lo, 1e-9), 0, 1) * 254 + 1).T.astype(np.uint8)
    with rasterio.open(out_path, "w", driver="GTiff", width=w, height=h, count=3, dtype="uint8", crs=prof["crs"], transform=prof["transform"],
                       nodata=0, tiled=True, compress="deflate", photometric="RGB") as dst:
        dst.write(rgb.reshape(3, h, w))
        for i, n in enumerate(("PC1 (red)", "PC2 (green)", "PC3 (blue)"), 1):
            dst.set_band_description(i, n)
    return {"path": out_path, "explained": [round(float(v), 3) for v in pca.explained_variance_ratio_]}


def similarity(path: str, points: list[list[float]], out_path: str) -> dict:
    """Cosine similarity of every pixel to the mean vector of the clicked places (lon, lat): 1 = the same, 0 = unrelated."""
    import rasterio
    from rasterio.warp import transform as tr
    data, prof = _read(path)
    d, h, w = data.shape
    xs, ys = tr("EPSG:4326", prof["crs"], [p[0] for p in points], [p[1] for p in points])
    inv = ~prof["transform"]
    refs = []
    for x, y in zip(xs, ys):
        c, r = inv * (x, y)
        r, c = int(math.floor(r)), int(math.floor(c))
        if 0 <= r < h and 0 <= c < w and np.isfinite(data[:, r, c]).all():
            v = data[:, r, c]
            refs.append(v / (np.linalg.norm(v) or 1))
    if not refs:
        raise ValueError("None of the points is on the layer (or they fall on pixels without values)")
    ref = np.mean(refs, axis=0)
    ref /= np.linalg.norm(ref) or 1
    flat = data.reshape(d, -1)
    norm = np.linalg.norm(flat, axis=0)
    sim = (ref @ flat) / np.where(norm > 0, norm, np.nan)
    sim = sim.reshape(h, w).astype(np.float32)
    with rasterio.open(out_path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32", crs=prof["crs"], transform=prof["transform"],
                       nodata=np.nan, tiled=True, compress="deflate", predictor=3) as dst:
        dst.write(sim, 1)
        dst.set_band_description(1, "similarity")
    v = sim[np.isfinite(sim)]
    return {"path": out_path, "points": len(refs), "p50": round(float(np.median(v)), 3), "p95": round(float(np.percentile(v, 95)), 3),
            "share_above_0_9": round(float((v > 0.9).mean() * 100), 2)}
