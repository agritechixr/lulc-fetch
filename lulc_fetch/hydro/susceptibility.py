"""Flood susceptibility (Analysis ▸ Hydrology ▸ Flood susceptibility): where floods are likely, learned from where they
happened (a flood inventory: points or polygons, e.g. from the SAR flood map or field reports) and predictor layers
(HAND, TWI, SPI, slope, curvature, distance to streams, land cover, rainfall …).

Non-flood samples are your own layer, or random cells at least a buffer away from every flood. The model (Random
Forest or LightGBM) is scored with spatial-block cross-validation (whole blocks left out, so neighbouring cells don't
flatter it) next to a random split, by ROC AUC; then trained on all samples and mapped: a 0–1 probability and five
classes (very low … very high), with the importance of each predictor."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

from .. import progress

CLASSES = {1: ("Very low", (26, 150, 65, 255)), 2: ("Low", (166, 217, 106, 255)), 3: ("Moderate", (255, 255, 191, 255)),
           4: ("High", (253, 174, 97, 255)), 5: ("Very high", (215, 25, 28, 255))}


def _stack(paths: list[str]):
    """Every band of every predictor on the first one's grid: (bands × h × w), names, profile."""
    with rasterio.open(paths[0]) as ref:
        prof, h, w = ref.profile.copy(), ref.height, ref.width
        tr, crs = ref.transform, ref.crs
    if h * w > 60_000_000:
        raise ValueError("The predictors are too big → clip them to your area first")
    bands, names = [], []
    for p in paths:
        with rasterio.open(p) as s:
            for b in range(1, s.count + 1):
                a = s.read(b, masked=True).astype("float32").filled(np.nan)
                if (s.transform, s.crs, s.width, s.height) != (tr, crs, w, h):
                    out = np.full((h, w), np.nan, "float32")
                    reproject(a, out, src_transform=s.transform, src_crs=s.crs, dst_transform=tr, dst_crs=crs, src_nodata=np.nan,
                              dst_nodata=np.nan, resampling=Resampling.bilinear)
                    a = out
                bands.append(a)
                names.append(s.descriptions[b - 1] or f"{Path(p).stem}" + (f"_b{b}" if s.count > 1 else ""))
    return np.stack(bands), names, prof, tr, crs


def _mask(geojson: dict, tr, crs, shape) -> np.ndarray:
    from rasterio import features
    from shapely.geometry import shape as sshape

    from ..convert import _reproject_all
    geoms = [sshape(f["geometry"]) for f in geojson.get("features", []) if f.get("geometry")]
    if not geoms:
        return np.zeros(shape, bool)
    geoms = _reproject_all(geoms, "EPSG:4326", crs)
    return features.rasterize(((g, 1) for g in geoms), out_shape=shape, transform=tr, all_touched=True, dtype="uint8") > 0


def run(predictors: list[str], floods: dict, out_dir: Path, *, non_floods: dict | None = None, ratio: float = 1.0, buffer_m: float = 200.0,
        model: str = "rf", block_m: float = 2000.0, folds: int = 5, max_samples: int = 20000, seed: int = 0, name: str = "flood_susceptibility") -> dict:
    from scipy import ndimage as ndi
    from sklearn.metrics import roc_auc_score
    if not predictors:
        raise ValueError("Choose the predictor layers (e.g. HAND, TWI, slope, distance to streams)")
    progress.update(0.05, "Stacking the predictors")
    X3, names, prof, tr, crs = _stack(predictors)
    nb, h, w = X3.shape
    ok = np.isfinite(X3).all(0)
    pos = _mask(floods, tr, crs, (h, w)) & ok
    if pos.sum() < 10:
        raise ValueError("The flood layer covers fewer than 10 cells with data in every predictor")
    rng = np.random.default_rng(seed)
    if non_floods:
        neg = _mask(non_floods, tr, crs, (h, w)) & ok & ~pos
    else:
        px = abs(tr.a) * (111_320 if crs and crs.is_geographic else 1)
        far = ndi.distance_transform_edt(~pos) * px > buffer_m
        neg = far & ok
    pi, ni = np.flatnonzero(pos), np.flatnonzero(neg)
    if ni.size < 10:
        raise ValueError("Not enough non-flood cells → give a non-flood layer or a smaller buffer")
    npos = min(pi.size, max_samples // 2)
    pi = rng.choice(pi, npos, replace=False)
    ni = rng.choice(ni, min(ni.size, int(npos * ratio)), replace=False)
    idx = np.r_[pi, ni]
    y = np.r_[np.ones(pi.size), np.zeros(ni.size)].astype(int)
    X = X3.reshape(nb, -1)[:, idx].T
    rows, cols = np.divmod(idx, w)
    pxm = abs(tr.a) * (111_320 if crs and crs.is_geographic else 1)
    blocks = (rows * pxm // block_m).astype(int) * 100_000 + (cols * pxm // block_m).astype(int)

    def make():
        if model == "lgbm":
            from lightgbm import LGBMClassifier
            return LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31, random_state=seed, verbose=-1, n_jobs=-1)
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(n_estimators=300, min_samples_leaf=2, random_state=seed, n_jobs=-1)

    def cv(groups) -> list[float]:
        from sklearn.model_selection import GroupKFold, StratifiedKFold
        aucs = []
        splits = GroupKFold(n_splits=min(folds, len(np.unique(groups)))).split(X, y, groups) if groups is not None else \
            StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed).split(X, y)
        for tr_i, te_i in splits:
            if len(np.unique(y[te_i])) < 2 or len(np.unique(y[tr_i])) < 2:
                continue
            m = make().fit(X[tr_i], y[tr_i])
            aucs.append(float(roc_auc_score(y[te_i], m.predict_proba(X[te_i])[:, 1])))
        return aucs
    progress.update(0.25, "Spatial-block cross-validation")
    auc_block = cv(blocks) if len(np.unique(blocks)) >= 2 else []
    progress.update(0.45, "Random cross-validation")
    auc_rand = cv(None)
    progress.update(0.6, "Training on all samples")
    m = make().fit(X, y)
    imp = getattr(m, "feature_importances_", np.zeros(nb)).astype(float)
    imp = imp / imp.sum() if imp.sum() > 0 else imp
    progress.update(0.7, "Mapping the probability")
    flat = X3.reshape(nb, -1)
    prob = np.full(h * w, np.nan, "float32")
    good = np.flatnonzero(ok.ravel())
    for s in range(0, good.size, 500_000):
        i = good[s:s + 500_000]
        prob[i] = m.predict_proba(flat[:, i].T)[:, 1]
    cls = np.where(np.isfinite(prob), np.clip(np.floor(np.nan_to_num(prob) * 5) + 1, 1, 5), 0).astype("uint8")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = {**prof, "driver": "GTiff", "count": 1, "dtype": "float32", "nodata": np.nan, "compress": "deflate"}
    p.pop("photometric", None)
    prob_path = out_dir / f"{name}_probability.tif"
    with rasterio.open(prob_path, "w", **p) as d:
        d.write(prob.reshape(h, w), 1)
        d.set_band_description(1, "Flood susceptibility (probability 0–1)")
    cls_path = out_dir / f"{name}_classes.tif"
    import json
    with rasterio.open(cls_path, "w", **{**p, "dtype": "uint8", "nodata": 0}) as d:
        d.write(cls.reshape(h, w), 1)
        d.set_band_description(1, "Flood susceptibility class")
        d.write_colormap(1, {k: c for k, (_, c) in CLASSES.items()})
        d.update_tags(classes=json.dumps({str(k): n for k, (n, _) in CLASSES.items()}))
    table = out_dir / f"{name}_importance.csv"
    order = np.argsort(-imp)
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["predictor", "importance"])
        for i in order:
            wr.writerow([names[i], round(float(imp[i]), 4)])
    cell = abs(tr.a * tr.e) * ((111_320 * 110_574) if crs and crs.is_geographic else 1) / 1e6
    shares = {CLASSES[k][0]: round(100 * float((cls == k).sum()) / max(1, int((cls > 0).sum())), 2) for k in CLASSES}
    progress.update(1.0, "Done")
    return {"outputs": [str(prob_path), str(cls_path)], "csv": str(table), "samples": {"flood": int(pi.size), "non_flood": int(ni.size)},
            "auc_spatial": round(float(np.mean(auc_block)), 3) if auc_block else None, "auc_random": round(float(np.mean(auc_rand)), 3) if auc_rand else None,
            "folds_spatial": len(auc_block), "importance": {names[i]: round(float(imp[i]), 4) for i in order[:12]},
            "class_pct": shares, "high_km2": round(float((cls >= 4).sum()) * cell, 3), "model": "LightGBM" if model == "lgbm" else "Random Forest"}
