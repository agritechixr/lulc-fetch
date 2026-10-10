"""AHP, the analytic hierarchy process (Saaty 1980) (Analysis ▸ Tools ▸ Fuzzy & suitability ▸ AHP weights & overlay).

Compare the factors in pairs on Saaty's 1–9 scale (1 equal, 3 moderately, 5 strongly, 7 very strongly, 9 extremely more
important; the reciprocal the other way round). The weights are the principal eigenvector of the comparison matrix;
the consistency ratio CR = CI / RI (CI = (λmax − n) / (n − 1), RI Saaty's random index) should be below 0.10, else the
comparisons contradict each other and the tool says which pair disagrees most with the weights.

Weighted overlay: each factor raster scored 0–1 (continuous: from its 2nd to 98th percentile, rising or falling;
classes: a score per class), then Σ wᵢ · scoreᵢ, and five classes (very low … very high) by equal intervals."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

from . import progress

RI = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49, 11: 1.51, 12: 1.48, 13: 1.56, 14: 1.57, 15: 1.59}
CLASSES = {1: ("Very low", (215, 25, 28, 255)), 2: ("Low", (253, 174, 97, 255)), 3: ("Moderate", (255, 255, 191, 255)),
           4: ("High", (166, 217, 106, 255)), 5: ("Very high", (26, 150, 65, 255))}


def weights(matrix) -> dict:
    """Weights, λmax, CI, CR of a pairwise comparison matrix (only the upper triangle is read: the lower one is its
    reciprocal), and the most inconsistent pair."""
    A = np.array(matrix, dtype=float)
    n = A.shape[0]
    if A.shape != (n, n) or n < 2:
        raise ValueError("The comparison matrix must be square, at least 2 × 2")
    if n > 15:
        raise ValueError("Up to 15 factors")
    for i in range(n):
        A[i, i] = 1.0
        for j in range(i + 1, n):
            if not (1 / 9 - 1e-9 <= A[i, j] <= 9 + 1e-9):
                raise ValueError("Comparisons are between 1/9 and 9")
            A[j, i] = 1 / A[i, j]
    vals, vecs = np.linalg.eig(A)
    k = int(np.argmax(vals.real))
    w = np.abs(vecs[:, k].real)
    w = w / w.sum()
    lam = float(vals[k].real)
    ci = (lam - n) / (n - 1) if n > 2 else 0.0
    cr = ci / RI[n] if RI.get(n) else 0.0
    # the pair whose judgement disagrees most with what the weights imply
    ratio = A / np.outer(w, 1 / w)
    dev = np.abs(np.log(ratio))
    np.fill_diagonal(dev, 0)
    i, j = np.unravel_index(int(np.argmax(np.triu(dev))), dev.shape)
    return {"weights": [round(float(x), 4) for x in w], "lambda_max": round(lam, 4), "ci": round(ci, 4), "cr": round(cr, 4),
            "consistent": cr < 0.1, "worst_pair": [int(i), int(j)], "suggested": round(float(w[i] / w[j]), 2)}


def from_ranks(ranks: list[int]) -> list[list[float]]:
    """A pairwise matrix from an importance order (1 = most important): a step of rank = one step on Saaty's scale."""
    n = len(ranks)
    A = np.ones((n, n))
    for i in range(n):
        for j in range(n):
            d = ranks[j] - ranks[i]
            A[i, j] = min(9, 1 + d) if d >= 0 else 1 / min(9, 1 - d)
    return A.tolist()


def score_continuous(a: np.ndarray, rising: bool = True) -> np.ndarray:
    v = a[np.isfinite(a)]
    if not v.size:
        return np.full(a.shape, np.nan)
    lo, hi = np.percentile(v, [2, 98])
    s = np.clip((a - lo) / max(hi - lo, 1e-12), 0, 1)
    return s if rising else 1 - s


def read_on(ref, path: str, band: int = 1, nearest: bool = False) -> np.ndarray:
    with rasterio.open(path) as s:
        a = s.read(band, masked=True).astype("float64").filled(np.nan)
        if (s.crs, s.transform, s.width, s.height) == (ref["crs"], ref["transform"], ref["width"], ref["height"]):
            return a
        out = np.full((ref["height"], ref["width"]), np.nan)
        reproject(a, out, src_transform=s.transform, src_crs=s.crs, dst_transform=ref["transform"], dst_crs=ref["crs"],
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest if nearest else Resampling.bilinear)
        return out


def overlay(factors: list[dict], w: list[float], out: Path, *, title: str = "Suitability") -> dict:
    """factors: [{path, band?, name?, rising: bool} or {path, scores: {class value: 0–1 or 1–5}}]. On the first one's grid."""
    if len(factors) != len(w):
        raise ValueError("One weight per factor")
    with rasterio.open(factors[0]["path"]) as s:
        ref = {"crs": s.crs, "transform": s.transform, "width": s.width, "height": s.height}
        prof = {**s.profile, "driver": "GTiff", "count": 1, "dtype": "float32", "nodata": np.nan, "compress": "deflate"}
        prof.pop("photometric", None)
    total = np.zeros((ref["height"], ref["width"]))
    ok = np.ones_like(total, bool)
    scored = []
    for k, f in enumerate(factors):
        progress.update(0.1 + 0.7 * k / len(factors), f"Factor {k + 1} of {len(factors)}")
        if f.get("scores"):
            a = read_on(ref, f["path"], f.get("band", 1), nearest=True)
            sc = np.full(a.shape, np.nan)
            vals = {float(kk): float(v) for kk, v in f["scores"].items()}
            top = max(vals.values()) if vals else 1
            for v, s_ in vals.items():
                sc[a == v] = s_ / (5.0 if top > 1 else 1.0)
        else:
            sc = score_continuous(read_on(ref, f["path"], f.get("band", 1)), f.get("rising", True))
        ok &= np.isfinite(sc)
        total += w[k] * np.nan_to_num(sc)
        scored.append(sc)
    idx = np.where(ok, total, np.nan)
    cls = np.where(ok, np.clip(np.floor(idx * 5) + 1, 1, 5), 0).astype("uint8")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **prof) as d:
        d.write(idx.astype("float32"), 1)
        d.set_band_description(1, f"{title} index (0–1, AHP weighted)")
    cls_path = out.with_name(out.stem + "_classes.tif")
    with rasterio.open(cls_path, "w", **{**prof, "dtype": "uint8", "nodata": 0}) as d:
        d.write(cls, 1)
        d.set_band_description(1, f"{title} class")
        d.write_colormap(1, {k: c for k, (_, c) in CLASSES.items()})
        d.update_tags(classes=json.dumps({str(k): n for k, (n, _) in CLASSES.items()}))
    pct = {CLASSES[k][0]: round(100 * float((cls == k).sum()) / max(1, int(ok.sum())), 2) for k in CLASSES}
    progress.update(1.0, "Done")
    return {"path": str(out), "classes_path": str(cls_path), "class_pct": pct, "mean_index": round(float(np.nanmean(idx)), 4), "scored": scored, "ok": ok}
