"""Classical ML for rasters: train straight from an image and ground truth (raster or vector), then map the image.

Works directly on the raster: the labelled pixels are read from the image (only for this run; nothing is kept as a
table), a model is trained and validated, and the image is classified. Works for any number of bands: RGB (3), multispectral (4–30), hyperspectral (100+),
SAR, and pixel embeddings such as Google AlphaEarth Satellite Embeddings (64 dimensions) or TESSERA (128).
The kind of data is detected so sensible models and preprocessing can be suggested.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import numpy as np
import rasterio

from . import ml, progress
from .tabular import raster_to_table

log = logging.getLogger(__name__)

# models offered by the tool (all scikit-learn compatible; no deep learning), in display order
MODELS = ["rf", "svm", "mlc", "sam", "mindist", "knn", "lgbm", "xgb", "et", "lr", "lda", "nb", "mlp", "hgb"]
KINDS = {
    "rgb": {"title": "RGB image", "models": ["rf", "svm", "knn", "mlc"], "scaling": "auto", "reduce": "none",
            "note": "3 colour bands (e.g. a drone or aerial photo). Few bands, so texture-free pixel classification is limited; "
                    "Random Forest or SVM usually work best."},
    "multispectral": {"title": "Multispectral image", "models": ["rf", "svm", "mlc", "lgbm"], "scaling": "auto", "reduce": "none",
                      "note": "4–30 bands (e.g. Sentinel-2, Landsat). Random Forest and SVM are strong; Maximum Likelihood is the classic choice."},
    "hyperspectral": {"title": "Hyperspectral image", "models": ["svm", "rf", "sam", "lda"], "scaling": "auto", "reduce": "auto",
                      "note": "Many narrow bands. Bands are highly correlated: SVM and Spectral Angle Mapper handle that well. "
                              "For Maximum Likelihood / Linear Discriminant, reduce the bands with PCA first (set automatically)."},
    "embedding": {"title": "Pixel embedding", "models": ["knn", "svm", "lr", "sam"], "scaling": "none", "reduce": "none",
                  "params": {"knn": {"metric": "cosine"}, "svm": {"kernel": "linear"}},
                  "note": "Learned feature vectors (e.g. AlphaEarth 64-D, TESSERA 128-D) where similar places have similar vectors. "
                          "Compare them by angle: k-NN with cosine distance, linear SVM, logistic regression or SAM. Keep scaling off."},
    "sar": {"title": "SAR (radar)", "models": ["rf", "svm", "lgbm", "mlc"], "scaling": "auto", "reduce": "none",
            "note": "Radar backscatter (VV / VH …). Use dB values; Random Forest and SVM work well. Add optical bands with Stack layers for better maps."},
    "other": {"title": "Raster", "models": ["rf", "svm", "mlc"], "scaling": "auto", "reduce": "none", "note": ""},
}
GOOD_FOR = {
    "rf": ["rgb", "multispectral", "hyperspectral", "sar", "embedding"], "svm": ["rgb", "multispectral", "hyperspectral", "sar", "embedding"],
    "mlc": ["multispectral", "sar", "rgb"], "sam": ["hyperspectral", "embedding", "multispectral"], "mindist": ["multispectral", "rgb"],
    "knn": ["embedding", "multispectral", "rgb"], "lgbm": ["multispectral", "hyperspectral", "sar"], "xgb": ["multispectral", "hyperspectral", "sar"],
    "et": ["multispectral", "hyperspectral"], "lr": ["embedding", "hyperspectral"], "lda": ["hyperspectral", "multispectral", "embedding"],
    "nb": ["multispectral"], "mlp": ["hyperspectral", "embedding", "multispectral"], "hgb": ["multispectral", "hyperspectral"],
}


def schema() -> dict:
    return {"models": MODELS, "kinds": KINDS, "good_for": GOOD_FOR}


def inspect_kind(path: str | Path, bands: list[int] | None = None) -> dict:
    """Guess what kind of raster this is (RGB, multispectral, hyperspectral, SAR, embedding) from bands and values."""
    from .analysis import _read, _is_rgb
    with rasterio.open(path) as src:
        idx = bands or list(range(1, src.count + 1))
        names = [(src.descriptions[i - 1] or "") for i in idx]
        dtype = src.dtypes[idx[0] - 1]
        rgb = _is_rgb(src) and len(idx) <= 4
        sample = _read(src, idx[:256], max_px=160)   # small overview: fast even for 200+ bands
    n = len(idx)
    v = sample.reshape(sample.shape[0], -1).T
    v = v[np.isfinite(v).all(axis=1)]
    lo, hi = (float(np.percentile(v, 0.5)), float(np.percentile(v, 99.5))) if len(v) else (0.0, 0.0)
    norm = float(np.median(np.linalg.norm(v, axis=1))) if len(v) else 0.0
    up = [nm.upper() for nm in names]
    emb_names = sum(bool(re.fullmatch(r"(A\d{2}|E?\d{1,3}|EMB.*|EMBEDDING.*|DIM.*|F\d+)", u)) for u in up) > n / 2
    sar = any(u in ("VV", "VH", "HH", "HV", "VVDB", "VHDB") for u in up)
    reason = []
    if rgb or (n == 3 and dtype == "uint8"):
        kind = "rgb"
        reason.append(f"{n} bands of 8-bit colour")
    elif sar and n <= 6:
        kind = "sar"
        reason.append("radar polarisation bands (" + ", ".join(nm for nm in names if nm.upper().startswith(("V", "H"))) + ")")
    elif (n in (64, 128) or emb_names) and (dtype == "int8" or (hi <= 1.5 and lo >= -1.5 and (0.6 < norm < 1.4 or emb_names))):
        kind = "embedding"
        reason.append(f"{n} dimensions" + (" with unit-length vectors (|v| ≈ 1)" if 0.9 < norm < 1.1 else "") + (", 8-bit quantised" if dtype == "int8" else ""))
        if n == 64:
            reason.append("64-D, like Google AlphaEarth Satellite Embedding")
        elif n == 128:
            reason.append("128-D, like TESSERA")
    elif n > 30:
        kind = "hyperspectral"
        reason.append(f"{n} bands")
    elif n >= 4:
        kind = "multispectral"
        reason.append(f"{n} bands" + (" (" + ", ".join(names[:6]) + ("…" if n > 6 else "") + ")" if any(names) else ""))
    else:
        kind = "other"
        reason.append(f"{n} band(s)")
    return {"kind": kind, **KINDS[kind], "bands": n, "dtype": dtype, "range": [lo, hi], "vector_norm": norm, "reason": "; ".join(reason)}


def classify(raster: str | Path, out_dir: str | Path, *, bands: list[int] | None, ground_truth: dict, model: str = "rf",
             params: dict | None = None, common: dict | None = None, tuning: dict | None = None, clip: dict | None = None,
             map_clip: dict | None = None, factor: int = 1, scale: float = 1.0, offset: float = 0.0, per_class: int | None = 3000,
             name: str = "classified", class_colors: dict | None = None, confidence: bool = True, resolution: str = "auto",
             make_map: bool = True) -> dict:
    """Sample labelled pixels → train + validate → classify the image. Returns the training report, map and file paths."""
    t0 = time.time()
    if model not in ml.MODELS or "classification" not in ml.MODELS[model]["tasks"]:
        raise ValueError(f"{model} can't classify")
    if not ground_truth:
        raise ValueError("Choose the ground truth (a class raster, or polygons / points with a class field)")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "classified"
    seed = int((common or {}).get("random_state") or 0)

    progress.update(0.0, "Reading the training pixels")
    kind = inspect_kind(raster, bands)
    table = out_dir / f".{stem}_pixels.parquet"   # the run's working copy of the labelled pixels, deleted after training
    with progress.span(0.0, 0.25):
        raster_to_table(raster, table, bands=bands, clip=clip, factor=factor, scale=scale, offset=offset, ground_truth=ground_truth,
                        labelled_only=True, sampling="stratified" if per_class else "all", per_class=per_class or 5000,
                        xy=True, lonlat=False, fmt="parquet", seed=seed, class_colors=class_colors, preview_rows=0)
    meta = json.loads(Path(str(table) + ".json").read_text())
    counts = meta.get("class_counts") or {}
    if len(counts) < 2:
        raise ValueError(f"The ground truth covers only {len(counts)} class inside the image" + (" / area" if clip else "")
                         + ". Check that it overlaps the image and has at least two classes.")
    small = [k for k, v in counts.items() if v < 10]
    features, target = meta["band_columns"], meta["target"]
    log.info("%s training pixels from %d classes, %d bands (%s)", f"{sum(counts.values()):,}", len(counts), len(features), kind["title"])

    try:
        with progress.span(0.25, 0.72 if make_map else 1.0):
            rep = ml.train(table, out_dir, target=target, features=features, model=model, task="classification",
                           params=params, common=common, tuning=tuning, name=name)
    finally:
        table.unlink(missing_ok=True)
        Path(str(table) + ".json").unlink(missing_ok=True)
    rep["table"] = str(raster)            # trained directly from this image
    rep["ground_truth"] = ground_truth.get("path") or f"vector ({len((ground_truth.get('geojson') or {}).get('features', []))} features)"
    if small:
        rep.setdefault("warnings", []).append(f"Very few training pixels for: {', '.join(small)}. Add samples for a reliable map.")

    result = {"kind": kind, "model": rep, "samples": {"classes": counts, "pixels": int(sum(counts.values())), "bands": len(features)},
              "model_path": rep["path"]}
    if make_map:
        with progress.span(0.72, 1.0):
            pr = ml.predict_raster(rep["path"], raster, out_dir / f"{stem}.tif", band_map=dict(zip(features, meta["band_indices"])),
                                   scale=scale, offset=offset, clip=map_clip, confidence=confidence, resolution=resolution)
        result["map"] = pr
    result["seconds"] = round(time.time() - t0, 1)
    log.info("Raster classification done in %.1f s", result["seconds"])
    return result
