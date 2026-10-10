"""Spatial cross-validation: how much a random train / test split overstates a map's accuracy, and which block size to
validate with.

    run(raster, ground_truth, …)   samples the labelled pixels once, then scores the same model with random k-fold and
                                   with spatial-block k-fold at several block sizes (whole blocks, and whole ground-truth
                                   polygons, are left out); plus a correlogram of the features (Moran's I by distance)
                                   whose fall below 0.1 suggests the smallest honest block (Roberts et al. 2017: blocks
                                   at least the range of spatial autocorrelation)

Neighbouring pixels are near copies: tested on pixels next to the ones it trained on, a model looks better than it is
on new places. The gap between random and block scores is that optimism.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from . import progress


def _model(name: str, seed: int):
    if name == "lgbm":
        from lightgbm import LGBMClassifier
        return LGBMClassifier(n_estimators=200, learning_rate=0.08, num_leaves=31, class_weight="balanced", random_state=seed, verbose=-1, n_jobs=-1)
    if name == "rf":
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(n_estimators=200, class_weight="balanced_subsample", min_samples_leaf=2, random_state=seed, n_jobs=-1)
    if name == "xgb":
        from xgboost import XGBClassifier
        return XGBClassifier(n_estimators=200, learning_rate=0.1, max_depth=6, random_state=seed, n_jobs=-1, tree_method="hist")
    raise ValueError("Model: lgbm, rf or xgb")


def _score(y, p, k):
    from sklearn.metrics import cohen_kappa_score, f1_score
    return {"oa": round(float((y == p).mean()), 4), "kappa": round(float(cohen_kappa_score(y, p, labels=list(range(k)))), 4),
            "f1": round(float(f1_score(y, p, labels=list(range(k)), average="macro", zero_division=0)), 4)}


def run(raster: str, ground_truth: dict, out_dir: Path, *, bands: list[int] | None = None, model: str = "lgbm", blocks_m=(250, 500, 1000, 2000, 4000),
        folds: int = 5, per_class: int = 2000, seed: int = 0, correlogram: bool = True) -> dict:
    import pandas as pd
    from sklearn.model_selection import GroupKFold, StratifiedKFold

    from .tabular import raster_to_table
    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tab = out_dir / f".spcv_{int(t0)}.parquet"
    progress.update(0.02, "Reading the labelled pixels")
    with progress.span(0.02, 0.25):
        meta = raster_to_table(raster, tab, bands=bands, ground_truth=ground_truth, labelled_only=True, sampling="stratified", per_class=per_class,
                               xy=True, lonlat=False, fmt="parquet", seed=seed, preview_rows=0)
    try:
        df = pd.read_parquet(tab)
    finally:
        tab.unlink(missing_ok=True)
        Path(str(tab) + ".json").unlink(missing_ok=True)
    X = df[meta["band_columns"]].to_numpy("float64")
    lab = df[meta["target"]].astype(str).to_numpy()
    classes = sorted(set(lab))
    if len(classes) < 2:
        raise ValueError("The ground truth covers only one class inside the image")
    y = np.array([classes.index(v) for v in lab])
    k = len(classes)
    xs, ys = df["x"].to_numpy(), df["y"].to_numpy()
    poly = df[meta["group_column"]].to_numpy() if meta.get("group_column") and meta["group_column"] in df else None
    rows = []
    runs = [("random", None)] + [("blocks", float(b)) for b in blocks_m]
    for n, (kind, b) in enumerate(runs):
        progress.update(0.25 + 0.6 * n / len(runs), "Random k-fold" if kind == "random" else f"Blocks of {b:g} m")
        pred = np.full(y.shape, -1)
        if kind == "random":
            splits = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed).split(X, y)
            note = f"{folds} folds"
        else:
            g = (np.floor(xs / b).astype("int64") * 1_000_003 + np.floor(ys / b).astype("int64"))
            if poly is not None:   # a polygon stays whole, in its first pixel's block
                g = pd.Series(g).groupby(poly).transform("first").to_numpy()
            ng = len(np.unique(g))
            if ng < 2:
                rows.append({"method": f"Blocks of {b:g} m", "block_m": b, "note": "all the pixels fall in one block", "oa": None, "kappa": None, "f1": None})
                continue
            nf = min(folds, ng)
            splits = GroupKFold(n_splits=nf).split(X, y, g)
            note = f"{nf} folds on {ng} blocks"
        for tr, te in splits:
            if len(np.unique(y[tr])) < 2:
                continue
            m = _model(model, seed)
            m.fit(X[tr], y[tr])
            pred[te] = m.predict(X[te])
        done = pred >= 0
        rows.append({"method": "Random k-fold" if kind == "random" else f"Blocks of {b:g} m", "block_m": b, "note": note,
                     **_score(y[done], pred[done], k), "tested": int(done.sum())})
    out = {"table": rows, "classes": classes, "pixels": int(len(y)), "model": model, "folds": folds}
    rnd = rows[0]
    blk = [r for r in rows[1:] if r.get("f1") is not None]
    if blk:
        out["optimism_f1"] = round(rnd["f1"] - min(r["f1"] for r in blk), 4)
    if correlogram:
        progress.update(0.9, "Correlogram")
        from .spatialraster import correlogram as cg
        import rasterio
        with rasterio.open(raster) as s:
            res = abs(s.transform.a) * (111320 if s.crs and s.crs.is_geographic else 1)
            bl = bands or list(range(1, s.count + 1))
            f = max(1, int(np.ceil(np.sqrt(s.width * s.height / 2_000_000))))
            curves = []
            hh, ww = max(1, s.height // f), max(1, s.width // f)
            radii = [r for r in (1, 2, 4, 8, 16, 32, 64, 128, 256, 512) if r <= max(2, min(hh, ww) // 4)]   # up to a quarter of the image
            for b in bl[:12]:   # up to 12 bands: the mean curve
                a = s.read(b, out_shape=(hh, ww), masked=True).astype("float64").filled(np.nan)
                if np.isfinite(a).sum() > 100 and np.nanstd(a) > 0:
                    curves.append(cg(a, radii=radii))
        if curves:
            radii = [p["radius_px"] for p in curves[0]]
            mean_i = [round(float(np.mean([c[i]["moran_i"] for c in curves if i < len(c)])), 4) for i in range(len(radii))]
            pts = [{"distance_m": round(r * f * res, 1), "moran_i": m} for r, m in zip(radii, mean_i)]
            out["correlogram"] = pts
            below = next((p["distance_m"] for p in pts if p["moran_i"] < 0.1), None)
            out["suggested_block_m"] = round(2 * below, -1) if below else None   # a block spans the range both ways
            if not below:
                out["range_note"] = (f"The features are still alike ({pts[-1]['moran_i']}) at {pts[-1]['distance_m']:g} m, a quarter of the image: "
                                     "use the largest blocks (or validate on a separate area)")
    out["seconds"] = round(time.time() - t0, 1)
    return out
