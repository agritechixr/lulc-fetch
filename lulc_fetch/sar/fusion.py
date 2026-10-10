"""SAR + optical fusion for mapping (Sentinel-1 + Sentinel-2): what radar adds to an optical classification.

    build_stack(optical, sars, out, …)     one GeoTIFF on the optical image's grid: optical bands and indices (clouds
                                           masked), SAR features of one or several dates, optionally an embedding;
                                           each band tagged with its group (optical / sar / embedding)
    compare(stack, ground_truth, out_dir)  the same model trained on optical only, SAR only, both stacked (early
                                           fusion) and both combined after classification (late fusion, weighted per
                                           class, SAR alone under clouds), validated on spatial blocks; the best (or
                                           chosen) one maps the area

Why spatial blocks: neighbouring pixels are near copies, so a random train / test split tests on pixels the model has
almost seen and overstates accuracy by 5–15 %. Here whole blocks (and whole ground-truth polygons) are left out.
Early fusion usually wins on clear imagery (SEN12MS: 88 % vs 84 % for late fusion); late fusion holds up better where
clouds hide part of the optical image, because the radar model answers alone there.
"""

from __future__ import annotations

import json
import logging
import re
import time
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from .. import progress
from . import analysis as AN

log = logging.getLogger(__name__)
INDICES = ("NDVI", "EVI", "NDRE", "NDWI", "MNDWI", "NDMI")
SCL_CLOUD = (3, 8, 9, 10)          # Sentinel-2 scene classification: cloud shadow, cloud medium / high, cirrus
SETS = {"optical": "Optical only", "sar": "SAR only", "embedding": "Embedding only", "early": "Early fusion (stacked)",
        "selected": "Early fusion, selected features",
        "late": "Late fusion (combined)"}
PALETTE = [(31, 120, 180), (51, 160, 44), (227, 26, 28), (255, 127, 0), (106, 61, 154), (177, 89, 40), (166, 206, 227),
           (178, 223, 138), (251, 154, 153), (253, 191, 111), (202, 178, 214), (255, 255, 153), (141, 211, 199), (190, 186, 218)]


def _db(x):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10 * np.log10(np.where(x > 0, x, np.nan))


# ------------------------------------------------------------------ the stack
def build_stack(optical: str, sars: list[str], out: Path, *, indices=INDICES, cloud: str | None = None, scl: bool = True,
                sar_dates: bool = True, texture: bool = False, embedding: str | None = None) -> dict:
    """Optical bands + indices, SAR features and an optional embedding on the optical image's grid, in one file."""
    from ..indices import compute_indices, normalize_band
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(optical) as s:
        grid = {"crs": s.crs, "transform": s.transform, "shape": (s.height, s.width)}
        names = [(d or f"band{i + 1}") for i, d in enumerate(s.descriptions)]
        if s.width * s.height > 60_000_000:
            raise ValueError("The optical image is too big for fusion here (60 million pixels): clip it to the area first")
        data = s.read(masked=True).astype("float32").filled(np.nan)
    if not sars:
        raise ValueError("Choose at least one SAR layer")
    bands, groups = [], []
    # optical bands, their clouds and indices
    mask = np.zeros(grid["shape"], bool)
    keep, s2 = [], {}
    for i, n in enumerate(names):
        if n.upper() in ("SCL", "SCENE_CLASSIFICATION"):
            if scl:
                mask |= np.isin(np.nan_to_num(data[i], nan=0).astype(int), SCL_CLOUD)
            continue
        if n.upper() in ("QA60", "CLOUD", "CLOUDS", "MSK_CLDPRB", "CLOUD_MASK"):
            continue
        keep.append(i)
        b = normalize_band(n)
        if b and b not in ("VV", "VH", "VVVH"):
            s2[b] = data[i]
    if not keep:
        raise ValueError("The optical layer has no bands to use")
    if cloud:
        with rasterio.open(cloud) as c:
            from rasterio.warp import Resampling, reproject
            cm = np.zeros(grid["shape"], "float32")
            reproject(rasterio.band(c, 1), cm, dst_transform=grid["transform"], dst_crs=grid["crs"], resampling=Resampling.nearest)
        mask |= cm > 0
    if s2:   # reflectance as 0–1 (Sentinel-2 L2A digital numbers are × 10 000)
        ref = s2.get("B04", next(iter(s2.values())))
        if np.nanmedian(ref) > 1.5:
            s2 = {k: v / 10000.0 for k, v in s2.items()}
            data = data.copy()
            for i in keep:
                if normalize_band(names[i]) in s2:
                    data[i] = data[i] / 10000.0
    for i in keep:
        bands.append(np.where(mask, np.nan, data[i]))
        groups.append(("optical", f"S2 {names[i]}"))
    if indices and s2:
        arrs, labs = compute_indices(s2, list(indices))
        for a, lab in zip(arrs, labs):
            bands.append(np.where(mask, np.nan, a.astype("float32")))
            groups.append(("optical", f"S2 {lab}"))
    # SAR: one date → its backscatter and ratios; several → per-date values and their statistics
    progress.update(0.3, "SAR features")
    per = []
    for k, p in enumerate(sars):
        pol, _, _ = AN.read_pols(p, like=grid)
        per.append((AN._date_of(p), pol))
    per.sort(key=lambda t: (t[0] is None, t[0]))
    pols = [k for k in ("VV", "VH", "HH", "HV") if all(k in p for _, p in per)]
    if not pols:
        raise ValueError("The SAR layers don't share a polarisation (VV / VH …)")
    co, cr = (pols + [None])[:2]
    if len(per) == 1:
        p = per[0][1]
        for k in pols:
            bands.append(_db(p[k]).astype("float32")); groups.append(("sar", f"S1 {k} dB"))
        if cr:
            bands.append((_db(p[co]) - _db(p[cr])).astype("float32")); groups.append(("sar", f"S1 {co}/{cr} dB"))
            with np.errstate(invalid="ignore", divide="ignore"):
                bands.append((4 * p[cr] / (p[co] + p[cr])).astype("float32")); groups.append(("sar", "S1 RVI"))
    else:
        for k in pols:
            S = np.stack([p[k] for _, p in per])
            D = _db(S)
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                for nm, a in (("mean", _db(np.nanmean(S, 0))), ("std", np.nanstd(D, 0)), ("min", np.nanmin(D, 0)), ("max", np.nanmax(D, 0))):
                    bands.append(a.astype("float32")); groups.append(("sar", f"S1 {k} {nm} dB"))
            if sar_dates:
                step = max(1, len(per) // 12)   # at most 12 dates as their own features
                for (d, p) in per[::step]:
                    bands.append(_db(p[k]).astype("float32")); groups.append(("sar", f"S1 {k} {d:%Y-%m-%d} dB" if d else f"S1 {k} date dB"))
        if cr:
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                R = np.stack([_db(p[co]) - _db(p[cr]) for _, p in per])
                bands.append(np.nanmean(R, 0).astype("float32")); groups.append(("sar", f"S1 {co}/{cr} mean dB"))
                rvi = np.stack([4 * p[cr] / (p[co] + p[cr]) for _, p in per])
                bands.append(np.nanmean(rvi, 0).astype("float32")); groups.append(("sar", "S1 RVI mean"))
    if texture:
        from scipy.ndimage import uniform_filter
        x = _db(np.nanmean(np.stack([p[co] for _, p in per]), 0))
        ok = np.isfinite(x)
        xv = np.where(ok, x, 0)
        n = uniform_filter(ok.astype(float), 7)
        with np.errstate(invalid="ignore", divide="ignore"):
            m = uniform_filter(xv, 7) / n
            sd = np.sqrt(np.maximum(uniform_filter(xv * xv, 7) / n - m * m, 0))
        bands.append(np.where(ok, sd, np.nan).astype("float32")); groups.append(("sar", f"S1 {co} texture std dB"))
    if embedding:
        from rasterio.warp import Resampling, reproject
        with rasterio.open(embedding) as e:
            for i in range(1, e.count + 1):
                b = np.full(grid["shape"], np.nan, "float32")
                reproject(rasterio.band(e, i), b, dst_transform=grid["transform"], dst_crs=grid["crs"], dst_nodata=np.nan, resampling=Resampling.bilinear)
                bands.append(b); groups.append(("embedding", f"EMB {e.descriptions[i - 1] or i}"))
    prof = {"driver": "GTiff", "width": grid["shape"][1], "height": grid["shape"][0], "count": len(bands), "dtype": "float32",
            "crs": grid["crs"], "transform": grid["transform"], "nodata": np.nan, "compress": "deflate", "BIGTIFF": "IF_SAFER"}
    if grid["shape"][0] >= 256 and grid["shape"][1] >= 256:
        prof.update(tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(out, "w", **prof) as d:
        for i, (b, (g, n)) in enumerate(zip(bands, groups), 1):
            d.write(b.astype("float32"), i)
            d.set_band_description(i, n)
        d.update_tags(fusion_groups=json.dumps([g for g, _ in groups]), sar_dates=json.dumps([d_.strftime("%Y-%m-%d") if d_ else None for d_, _ in per]),
                      cloud_pct=f"{100 * mask.mean():.2f}")
    count = {g: sum(1 for x, _ in groups if x == g) for g in ("optical", "sar", "embedding")}
    return {"path": str(out), "bands": [n for _, n in groups], "groups": count, "cloud_pct": round(100 * float(mask.mean()), 2),
            "sar_dates": len(per)}


# ------------------------------------------------------------------ comparing and mapping
def _model(name: str, seed: int, n_classes: int):
    if name == "lgbm":
        from lightgbm import LGBMClassifier
        return LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                              class_weight="balanced", random_state=seed, verbose=-1, n_jobs=-1)
    if name == "xgb":
        from xgboost import XGBClassifier
        return XGBClassifier(n_estimators=300, learning_rate=0.08, max_depth=6, subsample=0.8, colsample_bytree=0.8, random_state=seed,
                             n_jobs=-1, tree_method="hist", objective="multi:softprob" if n_classes > 2 else "binary:logistic")
    if name == "rf":
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(n_estimators=300, class_weight="balanced_subsample", min_samples_leaf=2, random_state=seed, n_jobs=-1)
    raise ValueError("Model: lgbm, rf or xgb")


def _fit(name, X, y, seed, k):
    m = _model(name, seed, k)
    if name == "rf":   # (random forests take NaN, but a column that is all NaN in a fold is filled)
        X = np.where(np.isfinite(X), X, np.nanmedian(np.where(np.isfinite(X), X, np.nan), 0) if np.isfinite(X).any() else 0)
        X = np.nan_to_num(X, nan=0.0)
    m.fit(X, y)
    return m


def _proba(name, m, X, k):
    if name == "rf":
        X = np.nan_to_num(X, nan=0.0)
    p = m.predict_proba(X)
    if p.shape[1] < k:   # a class absent from a training fold
        full = np.zeros((len(X), k))
        full[:, m.classes_] = p
        p = full
    return p


def _metrics(y, pred, k, names):
    from sklearn.metrics import cohen_kappa_score, confusion_matrix, f1_score
    if not len(y):
        return None
    f1 = f1_score(y, pred, labels=list(range(k)), average=None, zero_division=0)
    return {"oa": round(float((y == pred).mean()), 4), "kappa": round(float(cohen_kappa_score(y, pred, labels=list(range(k)))), 4),
            "f1": round(float(f1.mean()), 4), "per_class": {names[i]: round(float(f1[i]), 4) for i in range(k)},
            "confusion": confusion_matrix(y, pred, labels=list(range(k))).tolist(), "n": int(len(y))}


def select_features(X: np.ndarray, y: np.ndarray, seed: int = 0, rounds: int = 3) -> tuple[list[int], np.ndarray]:
    """Features that beat chance (a light Boruta): each feature gets a shuffled copy (same values, no link to the
    classes); a LightGBM is trained on both, and a feature is kept when its importance (gain) beats the best shuffled
    copy's in most of `rounds` runs. Weak features (often many SAR dates on a clear image) then stop diluting the
    strong ones. Returns (kept column indices, how often each was kept)."""
    from lightgbm import LGBMClassifier
    rng = np.random.default_rng(seed)
    n, f = X.shape
    take = rng.choice(n, size=min(n, 20000), replace=False)
    Xs, ys = X[take], y[take]
    hits = np.zeros(f)
    for r in range(rounds):
        sh = np.column_stack([rng.permutation(Xs[:, j]) for j in range(f)])
        m = LGBMClassifier(n_estimators=150, learning_rate=0.1, num_leaves=31, colsample_bytree=0.8, subsample=0.8, subsample_freq=1,
                           importance_type="gain", random_state=seed + r, verbose=-1, n_jobs=-1)
        m.fit(np.hstack([Xs, sh]), ys)
        imp = m.feature_importances_
        hits += imp[:f] > imp[f:].max()
    keep = [j for j in range(f) if hits[j] >= (rounds + 1) // 2 + (rounds % 2 == 0)]
    if len(keep) < 3:   # never fewer than three: the strongest ones
        keep = sorted(np.argsort(-hits)[:3].tolist())
    return keep, hits / rounds


def compare(stack: str, ground_truth: dict, out_dir: Path, *, model: str = "lgbm", block_m: float = 1000.0, folds: int = 5,
            per_class: int = 3000, map_with: str = "best", name: str = "fusion", seed: int = 0, make_map: bool = True,
            class_colors: dict | None = None, select: bool = True) -> dict:
    """Optical only, SAR only (and embedding only), early and late fusion, on spatial-block cross-validation; then the
    map of the best one (or map_with: early / late / optical / sar)."""
    import pandas as pd
    from sklearn.model_selection import GroupKFold

    from ..tabular import raster_to_table
    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "fusion"
    with rasterio.open(stack) as s:
        groups = json.loads(s.tags().get("fusion_groups") or "[]")
        cols_all = list(s.descriptions)
        px = abs(s.transform.a) * (111320 if s.crs and s.crs.is_geographic else 1)
    if len(groups) != len(cols_all) or "optical" not in groups or "sar" not in groups:
        raise ValueError("Not a fusion stack (make it with the SAR + optical fusion tool)")
    progress.update(0.02, "Reading the training pixels")
    tab = out_dir / f".{stem}_pixels.parquet"
    with progress.span(0.02, 0.2):
        meta = raster_to_table(stack, tab, ground_truth=ground_truth, labelled_only=True, sampling="stratified", per_class=per_class,
                               xy=True, lonlat=False, drop_nodata=False, fmt="parquet", seed=seed, preview_rows=0)
    try:
        df = pd.read_parquet(tab)
    finally:
        tab.unlink(missing_ok=True)
        Path(str(tab) + ".json").unlink(missing_ok=True)
    fcols = meta["band_columns"]
    target = meta["target"]
    X_all = df[fcols].to_numpy("float64")
    have = np.isfinite(X_all).any(1)
    df, X_all = df[have].reset_index(drop=True), X_all[have]
    labels = df[target].astype(str).to_numpy()
    classes = sorted(set(labels), key=lambda v: (not v.replace(".", "").isdigit(), float(v) if v.replace(".", "").isdigit() else 0, v))
    if len(classes) < 2:
        raise ValueError("The ground truth covers only one class inside the image: it needs at least two")
    code = {c: i for i, c in enumerate(classes)}
    y = np.array([code[v] for v in labels])
    k = len(classes)
    names = [c[:-2] if c.endswith(".0") else c for c in classes]
    gidx = {g: [i for i, x in enumerate(groups) if x == g] for g in ("optical", "sar", "embedding")}
    # spatial groups: blocks of block_m metres; a ground-truth polygon stays whole (in its first pixel's block)
    bx = np.floor(df["x"].to_numpy() / block_m).astype("int64")
    by = np.floor(df["y"].to_numpy() / block_m).astype("int64")
    blk = bx * 1_000_003 + by
    if meta.get("group_column") and meta["group_column"] in df:
        first = pd.Series(blk).groupby(df[meta["group_column"]].to_numpy()).transform("first").to_numpy()
        blk = first
    n_groups = len(np.unique(blk))
    if n_groups < 2:
        raise ValueError(f"All the training pixels fall in one {block_m:g} m block: use a smaller block size (or more spread-out ground truth)")
    nf = int(min(folds, n_groups))
    notes = []
    if nf < folds:
        notes.append(f"Only {n_groups} spatial blocks hold training pixels: {nf}-fold validation instead of {folds}")
    opt_ok = np.isfinite(X_all[:, gidx["optical"]]).any(1)
    sar_ok = np.isfinite(X_all[:, gidx["sar"]]).any(1)
    if (~opt_ok).mean() > 0.02:
        notes.append(f"{100 * (~opt_ok).mean():.0f} % of the training pixels are under clouds (no optical value)")
    sets = ["optical", "sar"] + (["embedding"] if gidx["embedding"] else []) + ["early"] + (["selected"] if select else [])
    cols = {"optical": gidx["optical"], "sar": gidx["sar"], "embedding": gidx["embedding"], "early": gidx["optical"] + gidx["sar"]}
    ok_rows = {"optical": opt_ok, "sar": sar_ok, "embedding": np.isfinite(X_all[:, gidx["embedding"]]).any(1) if gidx["embedding"] else None,
               "early": opt_ok | sar_ok, "selected": opt_ok | sar_ok}
    fold_keep = []   # the selection of each fold, made on its training pixels only (no peeking at the test blocks)
    oof = {s: np.full((len(y), k), np.nan) for s in sets}
    gkf = GroupKFold(n_splits=nf)
    splits = list(gkf.split(X_all, y, blk))
    total = len(sets) * nf
    step = 0
    for s in sets:
        for tr, te in splits:
            progress.update(0.2 + 0.55 * step / total, f"{SETS[s]}: fold {step % nf + 1} of {nf}")
            step += 1
            trm, tem = tr[ok_rows[s][tr]], te[ok_rows[s][te]]
            if len(np.unique(y[trm])) < 2 or not len(tem):
                continue
            cs = cols.get(s)
            if s == "selected":
                kept, _ = select_features(X_all[np.ix_(trm, cols["early"])], y[trm], seed)
                cs = [cols["early"][j] for j in kept]
                fold_keep.append(cs)
            m = _fit(model, X_all[np.ix_(trm, cs)], y[trm], seed, k)
            oof[s][tem] = _proba(model, m, X_all[np.ix_(tem, cs)], k)
    # late fusion: optical and SAR probabilities weighted per class by each model's F1 (SAR alone where optical is missing)
    res = {}
    for s in sets:
        r = ok_rows[s] & np.isfinite(oof[s]).all(1)
        res[s] = _metrics(y[r], oof[s][r].argmax(1), k, names)
        if res[s]:
            res[s]["coverage"] = round(100 * float(r.mean()), 1)
    wo = np.array([res["optical"]["per_class"][n] if res["optical"] else 0 for n in names]) + 1e-3
    ws = np.array([res["sar"]["per_class"][n] if res["sar"] else 0 for n in names]) + 1e-3
    po, ps = oof["optical"], oof["sar"]
    late = np.where(np.isfinite(po).all(1, keepdims=True) & np.isfinite(ps).all(1, keepdims=True), (po * wo + ps * ws) / (wo + ws),
                    np.where(np.isfinite(ps).all(1, keepdims=True), ps, po))
    r = np.isfinite(late).all(1)
    res["late"] = _metrics(y[r], late[r].argmax(1), k, names)
    res["late"]["coverage"] = round(100 * float(r.mean()), 1)
    sets.append("late")
    table = [{"set": s, "title": SETS[s], **{kk: res[s][kk] for kk in ("oa", "kappa", "f1", "n", "coverage")}} for s in sets if res.get(s)]
    fused = [t for t in table if t["set"] in ("early", "late", "selected")]
    best = max(fused, key=lambda t: (t["f1"], t["oa"]))["set"]
    gain = {"vs_optical_f1": round(res[best]["f1"] - res["optical"]["f1"], 4) if res.get("optical") else None,
            "vs_optical_oa": round(res[best]["oa"] - res["optical"]["oa"], 4) if res.get("optical") else None}
    report = {"table": table, "best": best, "gain": gain, "classes": names, "per_class": {s: res[s]["per_class"] for s in sets if res.get(s)},
              "confusion": res[best]["confusion"], "model": model, "folds": nf, "block_m": block_m, "blocks": n_groups,
              "pixels": int(len(y)), "class_counts": {n: int((y == i).sum()) for i, n in enumerate(names)}, "warnings": notes,
              "late_weights": {"optical": dict(zip(names, np.round(wo, 3).tolist())), "sar": dict(zip(names, np.round(ws, 3).tolist()))},
              "pixel_m": round(px, 3)}
    if select:   # the final selection, on all the training pixels (what the map uses), and how stable it was across folds
        kept, freq = select_features(X_all[:, cols["early"]], y, seed)
        cols["selected"] = [cols["early"][j] for j in kept]
        often = {cols_all[c]: round(sum(c in f_ for f_ in fold_keep) / max(1, len(fold_keep)), 2) for c in cols["early"]}
        report["selection"] = {"kept": [cols_all[c] for c in cols["selected"]], "dropped": [cols_all[c] for c in cols["early"] if c not in cols["selected"]],
                               "kept_optical": sum(1 for c in cols["selected"] if c in gidx["optical"]), "kept_sar": sum(1 for c in cols["selected"] if c in gidx["sar"]),
                               "of": {"optical": len(gidx["optical"]), "sar": len(gidx["sar"])}, "folds_kept": often}
    if make_map:
        use = best if map_with == "best" else map_with
        if use not in ("early", "late", "optical", "sar", "embedding", "selected") or (use == "embedding" and not gidx["embedding"]) \
                or (use == "selected" and not select):
            raise ValueError("Map with: best, early, selected, late, optical, sar or embedding")
        with progress.span(0.78, 1.0):
            report["map"] = _map(stack, out_dir, stem, use, model, X_all, y, ok_rows, cols, k, names, seed, wo, ws, class_colors)
        report["mapped_with"] = use
    rp = out_dir / f"{stem}_report.json"
    rp.write_text(json.dumps(report, indent=2))
    report["report"] = str(rp)
    report["seconds"] = round(time.time() - t0, 1)
    return report


def _map(stack, out_dir, stem, use, model, X_all, y, ok_rows, cols, k, names, seed, wo, ws, class_colors):
    """Train on all the training pixels and classify the stack, in strips: class map + confidence."""
    progress.update(0.0, "Training the final model")
    need = ["optical", "sar"] if use == "late" else [use]
    models = {s: _fit(model, X_all[np.ix_(ok_rows[s], cols[s])], y[ok_rows[s]], seed, k) for s in need}
    with rasterio.open(stack) as src:
        h, w = src.height, src.width
        prof = {"driver": "GTiff", "width": w, "height": h, "count": 1, "crs": src.crs, "transform": src.transform, "compress": "deflate",
                "BIGTIFF": "IF_SAFER"}
        if h >= 256 and w >= 256:
            prof.update(tiled=True, blockxsize=256, blockysize=256)
        cp, fp = out_dir / f"{stem}_classes.tif", out_dir / f"{stem}_confidence.tif"
        colors = {}
        for i, n in enumerate(names, 1):
            c = (class_colors or {}).get(n) or (class_colors or {}).get(str(i))
            if isinstance(c, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", c):
                c = tuple(int(c.lstrip("#")[j:j + 2], 16) for j in (0, 2, 4))
            colors[i] = (*(c if isinstance(c, (list, tuple)) and len(c) >= 3 else PALETTE[(i - 1) % len(PALETTE)])[:3], 255)
        with rasterio.open(cp, "w", **prof, dtype="uint8", nodata=0, photometric="palette") as dc, \
                rasterio.open(fp, "w", **prof, dtype="float32", nodata=np.nan) as df_:
            dc.write_colormap(1, {0: (0, 0, 0, 0), **colors})
            rows = max(16, 200_000 // max(w, 1))
            for r0 in range(0, h, rows):
                n = min(rows, h - r0)
                progress.update(0.1 + 0.9 * r0 / h, f"Mapping rows {r0}–{r0 + n} of {h}")
                X = src.read(window=Window(0, r0, w, n)).astype("float64").reshape(src.count, -1).T
                P = {}
                for s in need:
                    Xs = X[:, cols[s]]
                    has = np.isfinite(Xs).any(1)
                    p = np.full((len(X), k), np.nan)
                    if has.any():
                        p[has] = _proba(model, models[s], Xs[has], k)
                    P[s] = p
                if use == "late":
                    po, ps = P["optical"], P["sar"]
                    both = np.isfinite(po).all(1, keepdims=True) & np.isfinite(ps).all(1, keepdims=True)
                    p = np.where(both, (po * wo + ps * ws) / (wo + ws), np.where(np.isfinite(ps).all(1, keepdims=True), ps, po))
                else:
                    p = P[use]
                ok = np.isfinite(p).all(1)
                cls = np.where(ok, np.nan_to_num(p, nan=-1).argmax(1) + 1, 0).astype("uint8")
                conf = np.where(ok, np.nan_to_num(p, nan=0).max(1), np.nan).astype("float32")
                dc.write(cls.reshape(n, w), 1, window=Window(0, r0, w, n))
                df_.write(conf.reshape(n, w), 1, window=Window(0, r0, w, n))
            dc.update_tags(classes=json.dumps({i + 1: nm for i, nm in enumerate(names)}), fusion=use, model=model)
            dc.set_band_description(1, f"Classes ({SETS[use]})")
            df_.set_band_description(1, "Confidence (probability of the chosen class)")
    with rasterio.open(cp) as s:
        a = s.read(1)
        tr = s.transform
        ha = abs(tr.a * tr.e) / 1e4 if s.crs and not s.crs.is_geographic else None
    areas = {nm: {"pixels": int((a == i + 1).sum()), "ha": round(int((a == i + 1).sum()) * ha, 2) if ha else None} for i, nm in enumerate(names)}
    return {"classes_path": str(cp), "confidence_path": str(fp), "areas": areas}
