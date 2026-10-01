"""PCA and related dimensionality reduction (scikit-learn) on a multiband raster.

The model is fitted on a random sample of valid pixels (Incremental PCA instead learns from every
pixel in batches), then applied to the image strip by strip so memory stays bounded. Output is a
float32 GeoTIFF with one band per component, plus a report (explained variance, band loadings).
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window

from . import progress
from .analysis import _mask_outside, _read, clip_region

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ parameter schema (drives the UI)
# Each parameter: name, label, type (int | float | select | bool), default, tip, and optional
# options / min / max / advanced. Defaults are the sensible choice for satellite imagery.

COMMON = [
    {"name": "n_components", "label": "Number of components", "type": "int", "default": 3, "min": 1, "max": 30,
     "tip": "How many new bands to create. 3 is a good start: they are shown as a colour image (component 1 = red, "
            "2 = green, 3 = blue) and usually hold almost all the information of a multispectral image."},
    {"name": "standardize", "label": "Standardize bands", "type": "bool", "default": True,
     "tip": "Rescale every band to mean 0 and standard deviation 1 before the analysis, so bands with larger "
            "numbers (e.g. NIR) don't dominate. Recommended when bands have different ranges."},
    {"name": "resolution", "label": "Processing resolution", "type": "select", "default": "auto",
     "options": [["auto", "Auto (up to 25 M pixels)"], ["1", "Native (full detail)"], ["2", "2× coarser"],
                 ["4", "4× coarser"], ["8", "8× coarser"]],
     "tip": "Pixel size of the output. Auto keeps full detail for small areas and coarsens very large images "
            "(e.g. a whole Sentinel-2 tile) so it finishes quickly. Pick an area to analyse for full detail."},
    {"name": "sample_size", "label": "Pixels used to fit the model", "type": "int", "default": 100000,
     "min": 1000, "max": 2000000, "advanced": True,
     "tip": "The model learns from this many randomly chosen pixels, then is applied to the whole image. "
            "100,000 gives the same result as using every pixel for practical purposes, in a fraction of the time."},
    {"name": "random_state", "label": "Random seed", "type": "int", "default": 0, "min": 0, "max": 2**31 - 1,
     "advanced": True,
     "tip": "Fixes the random pixel sample (and random initialisation of some methods) so running again gives "
            "exactly the same result."},
]

METHODS = {
    "pca": {
        "title": "PCA", "full": "Principal Component Analysis", "recommended": True, "prefix": "PC",
        "desc": "The standard choice. Finds uncorrelated directions of maximum variance. Removes band redundancy "
                "and highlights the main patterns (brightness, vegetation, moisture).",
        "tip": "Linear, fast and well understood. Component 1 usually captures overall brightness, component 2 "
               "vegetation vs. soil, component 3 moisture or water. Use this unless you have a specific reason.",
        "params": [
            {"name": "whiten", "label": "Whiten", "type": "bool", "default": False, "advanced": True,
             "tip": "Scale each component to unit variance. Useful as input for some classifiers, but it hides how "
                    "important each component is. Usually leave off for visual analysis."},
            {"name": "svd_solver", "label": "Solver", "type": "select", "default": "auto", "advanced": True,
             "options": [["auto", "Auto"], ["full", "Full (exact)"], ["randomized", "Randomized (fast)"],
                         ["covariance_eigh", "Covariance eigh"]],
             "tip": "Numerical method. Auto picks the best one for the data size. All give the same answer for "
                    "imagery with a handful of bands."},
        ],
    },
    "incremental": {
        "title": "Incremental PCA", "full": "Incremental PCA", "prefix": "PC",
        "desc": "Same result as PCA, but learns from every pixel in small batches instead of a sample. For very "
                "large images when you want all pixels to count.",
        "tip": "Reads the image batch by batch, so memory stays low even for huge rasters. Slower than standard PCA "
               "with sampling, and the result is practically identical.",
        "params": [
            {"name": "batch_size", "label": "Batch size (pixels)", "type": "int", "default": 50000, "min": 1000,
             "max": 1000000, "advanced": True,
             "tip": "Pixels processed per step. Larger is faster but uses more memory."},
            {"name": "whiten", "label": "Whiten", "type": "bool", "default": False, "advanced": True,
             "tip": "Scale each component to unit variance. Usually leave off."},
        ],
    },
    "kernel": {
        "title": "Kernel PCA", "full": "Kernel PCA", "prefix": "KPC",
        "desc": "Non-linear PCA. Can separate classes that linear PCA mixes up. Slow: fitted on a small sample, "
                "and best used on a selected area.",
        "tip": "Maps pixels through a kernel function before PCA, which captures curved, non-linear relationships "
               "between bands. Cost grows quickly with the sample size, so the output is limited to about 2 M pixels.",
        "params": [
            {"name": "kernel", "label": "Kernel", "type": "select", "default": "rbf",
             "options": [["rbf", "RBF (Gaussian), recommended"], ["poly", "Polynomial"], ["sigmoid", "Sigmoid"],
                         ["cosine", "Cosine"], ["linear", "Linear (= ordinary PCA)"]],
             "tip": "The similarity function. RBF works well for most data. Polynomial and sigmoid need tuning. "
                    "Linear gives ordinary PCA."},
            {"name": "gamma", "label": "Gamma", "type": "float", "default": None, "min": 0, "placeholder": "auto (1 / bands)",
             "tip": "How local the RBF / polynomial / sigmoid kernel is. Larger = more detail, but noisier. Leave "
                    "empty for the default 1 / number of bands (with standardized bands)."},
            {"name": "degree", "label": "Degree (polynomial)", "type": "int", "default": 3, "min": 2, "max": 6,
             "advanced": True, "tip": "Only used with the polynomial kernel."},
            {"name": "fit_sample", "label": "Pixels used to fit", "type": "int", "default": 1500, "min": 200,
             "max": 5000,
             "tip": "Kernel PCA compares every pixel with every sample pixel, so this is kept small. 1,000–2,000 "
                    "is a good balance between quality and speed."},
        ],
    },
    "nmf": {
        "title": "NMF", "full": "Non-negative Matrix Factorization", "prefix": "NMF",
        "desc": "Splits pixels into additive, non-negative parts, similar to spectral unmixing (e.g. vegetation / "
                "soil / water). Components are easy to interpret.",
        "tip": "Every pixel is modelled as a positive mix of a few 'end-member' spectra. Bands are rescaled to 0–1 "
               "(standardizing is not used because NMF needs non-negative values).",
        "params": [
            {"name": "init", "label": "Initialisation", "type": "select", "default": "nndsvda",
             "options": [["nndsvda", "NNDSVDa, recommended"], ["nndsvd", "NNDSVD"], ["random", "Random"]],
             "tip": "Starting point of the optimisation. NNDSVDa is deterministic and converges well."},
            {"name": "beta_loss", "label": "Loss", "type": "select", "default": "frobenius", "advanced": True,
             "options": [["frobenius", "Frobenius (squared error)"], ["kullback-leibler", "Kullback-Leibler"]],
             "tip": "How the reconstruction error is measured. Frobenius is standard."},
            {"name": "max_iter", "label": "Max iterations", "type": "int", "default": 400, "min": 50, "max": 5000,
             "advanced": True, "tip": "Raise if you get a convergence warning."},
        ],
    },
    "ica": {
        "title": "FastICA", "full": "Independent Component Analysis", "prefix": "IC",
        "desc": "Finds statistically independent signals rather than uncorrelated ones. Can isolate specific "
                "features such as haze, shadows or a single land-cover type.",
        "tip": "Unlike PCA, components are not ordered by importance, and their sign and scale are arbitrary. "
               "Look at each component on its own.",
        "params": [
            {"name": "fun", "label": "Contrast function", "type": "select", "default": "logcosh",
             "options": [["logcosh", "logcosh, recommended"], ["exp", "exp (robust to outliers)"], ["cube", "cube"]],
             "tip": "Measures non-Gaussianity. logcosh is a good general-purpose choice."},
            {"name": "algorithm", "label": "Algorithm", "type": "select", "default": "parallel", "advanced": True,
             "options": [["parallel", "Parallel"], ["deflation", "Deflation"]],
             "tip": "Parallel estimates all components at once (usually faster). Deflation estimates them one by one."},
            {"name": "max_iter", "label": "Max iterations", "type": "int", "default": 400, "min": 50, "max": 5000,
             "advanced": True, "tip": "Raise if you get a convergence warning."},
        ],
    },
    "svd": {
        "title": "Truncated SVD", "full": "Truncated Singular Value Decomposition", "prefix": "SVD",
        "desc": "Like PCA but without removing the mean, so component 1 keeps overall brightness. Fast.",
        "tip": "Works on the raw (0–1 rescaled) values without centering. Useful when absolute brightness matters.",
        "params": [
            {"name": "algorithm", "label": "Algorithm", "type": "select", "default": "randomized", "advanced": True,
             "options": [["randomized", "Randomized (fast)"], ["arpack", "ARPACK (exact)"]],
             "tip": "Randomized is fast and accurate for imagery."},
            {"name": "n_iter", "label": "Iterations", "type": "int", "default": 5, "min": 1, "max": 50,
             "advanced": True, "tip": "Only for the randomized algorithm. More iterations = more accurate."},
        ],
    },
    "fa": {
        "title": "Factor Analysis", "full": "Factor Analysis", "prefix": "FA",
        "desc": "Models bands as a few hidden factors plus band-specific noise. The varimax option gives "
                "factors that are easier to interpret.",
        "tip": "Similar to PCA, but it separates shared signal from per-band noise. With varimax rotation each "
               "factor tends to load strongly on a few bands only.",
        "params": [
            {"name": "rotation", "label": "Rotation", "type": "select", "default": "varimax",
             "options": [["varimax", "Varimax, recommended"], ["quartimax", "Quartimax"], ["none", "None"]],
             "tip": "Rotates the factors to make them easier to interpret. Varimax is the usual choice."},
            {"name": "fit_sample", "label": "Pixels used to fit", "type": "int", "default": 20000, "min": 1000,
             "max": 200000, "advanced": True,
             "tip": "Factor Analysis is iterative and slower than PCA. 20,000 random pixels converge in a few seconds "
                    "and give stable factors."},
            {"name": "max_iter", "label": "Max iterations", "type": "int", "default": 3000, "min": 100,
             "max": 10000, "advanced": True, "tip": "Raise if you get a convergence warning."},
        ],
    },
}

# Satellite bands that are mostly atmosphere, not surface: left out of the recommended selection.
ATMOSPHERIC = {"B01", "B09", "B10"}


def schema() -> dict:
    return {"common": COMMON, "methods": METHODS, "atmospheric_bands": sorted(ATMOSPHERIC)}


def _param(params: dict, spec: dict):
    v = params.get(spec["name"], spec["default"])
    if v in ("", None):
        return spec["default"]
    if spec["type"] == "int":
        v = int(v)
    elif spec["type"] == "float":
        v = float(v)
    elif spec["type"] == "bool":
        v = bool(v)
    if spec["type"] in ("int", "float") and v is not None:
        if "min" in spec and v < spec["min"]:
            raise ValueError(f"{spec['label']} must be at least {spec['min']}")
        if "max" in spec and v > spec["max"]:
            raise ValueError(f"{spec['label']} must be at most {spec['max']}")
    if spec["type"] == "select" and v not in [o[0] for o in spec["options"]]:
        raise ValueError(f"Invalid value for {spec['label']}: {v}")
    return v


def _build_model(method: str, p: dict, n_components: int, seed: int):
    from sklearn import decomposition as d

    if method == "pca":
        return d.PCA(n_components=n_components, whiten=p["whiten"], svd_solver=p["svd_solver"], random_state=seed)
    if method == "incremental":
        return d.IncrementalPCA(n_components=n_components, whiten=p["whiten"], batch_size=p["batch_size"])
    if method == "kernel":
        return d.KernelPCA(n_components=n_components, kernel=p["kernel"], gamma=p["gamma"], degree=p["degree"],
                           random_state=seed, eigen_solver="auto")
    if method == "nmf":
        return d.NMF(n_components=n_components, init=p["init"], beta_loss=p["beta_loss"], max_iter=p["max_iter"],
                     solver="mu" if p["beta_loss"] != "frobenius" else "cd", random_state=seed)
    if method == "ica":
        return d.FastICA(n_components=n_components, fun=p["fun"], algorithm=p["algorithm"], max_iter=p["max_iter"],
                         whiten="unit-variance", random_state=seed)
    if method == "svd":
        return d.TruncatedSVD(n_components=n_components, algorithm=p["algorithm"], n_iter=p["n_iter"],
                              random_state=seed)
    if method == "fa":
        return d.FactorAnalysis(n_components=n_components, rotation=None if p["rotation"] == "none" else p["rotation"],
                                max_iter=p["max_iter"], random_state=seed)
    raise ValueError(f"Unknown method {method}")


def run(path: str | Path, out_path: str | Path, *, bands: list[int], method: str = "pca", params: dict | None = None,
        clip: dict | None = None, max_pixels: int = 25_000_000, rows_per_strip: int = 512) -> dict:
    """Fit `method` on the selected bands and write the component image. Returns a report dict."""
    import warnings

    from sklearn.exceptions import ConvergenceWarning

    t0 = time.time()
    params = params or {}
    if method not in METHODS:
        raise ValueError(f"Unknown method {method}")
    m = METHODS[method]
    common = {s["name"]: _param(params, s) for s in COMMON}
    p = {s["name"]: _param(params, s) for s in m["params"]}
    if len(bands) < 2:
        raise ValueError("Select at least two bands")
    n_comp = common["n_components"]
    if n_comp > len(bands) and method != "kernel":
        raise ValueError(f"Number of components ({n_comp}) can't exceed the number of bands ({len(bands)})")
    seed = common["random_state"]
    rng = np.random.default_rng(seed)
    standardize = common["standardize"] and method not in ("nmf", "svd")
    warn_msgs = []

    with rasterio.open(path) as src:
        for b in bands:
            if not 1 <= b <= src.count:
                raise ValueError(f"This file has no band {b}")
        names = [src.descriptions[b - 1] or f"Band {b}" for b in bands]
        win, geom = clip_region(src, clip)
        win = win or Window(0, 0, src.width, src.height)

        # output grid: native, or coarsened so the result stays manageable
        limit = 2_000_000 if method == "kernel" else max_pixels
        if common["resolution"] == "auto":
            f = max(1, math.ceil(math.sqrt(win.width * win.height / limit)))
        else:
            f = int(common["resolution"])
            if method == "kernel" and win.width * win.height / f ** 2 > limit:
                f = max(f, math.ceil(math.sqrt(win.width * win.height / limit)))
                warn_msgs.append(f"Kernel PCA output limited to ~{limit / 1e6:.0f} M pixels: processing at {f}× coarser.")
        out_w, out_h = max(1, math.ceil(win.width / f)), max(1, math.ceil(win.height / f))
        base = src.window_transform(win)
        transform = base * base.scale(win.width / out_w, win.height / out_h)
        log.info("%s on %d bands (%s) · output %d×%d px%s", m["title"], len(bands), ", ".join(names), out_w, out_h,
                 f" ({f}× coarser than native)" if f > 1 else " (native resolution)")

        # sample pixels from a quick overview read for fitting and for scaling statistics
        progress.update(0.01, "Reading a sample of the image")
        ov = _read(src, bands, max_px=min(2000, max(out_w, out_h)), window=win)
        ov_t = base * base.scale(win.width / ov.shape[2], win.height / ov.shape[1])
        _mask_outside(list(ov), geom, ov_t)
        flat = ov.reshape(len(bands), -1).T
        flat = flat[np.all(np.isfinite(flat), axis=1)]
        if len(flat) < max(50, n_comp * 5):
            raise ValueError("Too few valid pixels in the selected area")
        lo, hi = np.percentile(flat, 0.5, axis=0), np.percentile(flat, 99.5, axis=0)
        mean, std = flat.mean(axis=0), flat.std(axis=0)
        std[std == 0] = 1

        def prep(x):
            if standardize:
                return (x - mean) / std
            if method in ("nmf", "svd"):  # non-negative 0–1 per band
                return np.clip((x - lo) / np.where(hi > lo, hi - lo, 1), 0, None)
            return x

        model = _build_model(method, p, n_comp, seed)
        n_fit = p.get("fit_sample", common["sample_size"])  # Kernel PCA / Factor Analysis use a smaller sample

        def strips():
            for r0 in range(0, out_h, rows_per_strip):
                rows = min(rows_per_strip, out_h - r0)
                src_win = Window(win.col_off, win.row_off + r0 * f, win.width, min(rows * f, win.height - r0 * f))
                data = src.read(bands, window=src_win, out_shape=(len(bands), rows, out_w), masked=True,
                                resampling=Resampling.average if f > 1 else Resampling.nearest)
                data = data.astype("float32").filled(np.nan)
                st = transform * transform.translation(0, r0)
                _mask_outside(list(data), geom, st)
                yield r0, rows, data

        fit_end = 0.5 if method == "incremental" else 0.2
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            if method == "incremental":
                log.info("Learning from all pixels in batches of %d", p["batch_size"])
                buf = []
                for r0, rows, data in strips():
                    progress.update(0.05 + (fit_end - 0.05) * (r0 + rows) / out_h, f"Learning from all pixels ({(r0 + rows) / out_h:.0%})")
                    x = data.reshape(len(bands), -1).T
                    x = x[np.all(np.isfinite(x), axis=1)]
                    buf.append(prep(x))
                    while sum(len(b) for b in buf) >= max(p["batch_size"], n_comp):
                        allx = np.concatenate(buf)
                        model.partial_fit(allx[:p["batch_size"]])
                        buf = [allx[p["batch_size"]:]]
                rest = np.concatenate(buf) if buf else np.empty((0, len(bands)))
                if len(rest) >= n_comp:
                    model.partial_fit(rest)
                n_used = int(model.n_samples_seen_)
            else:
                sample = flat[rng.choice(len(flat), size=min(n_fit, len(flat)), replace=False)]
                log.info("Fitting %s on %s sampled pixels", m["title"], f"{len(sample):,}")
                progress.update(0.05)
                model.fit(prep(sample))
                progress.update(fit_end)
                n_used = len(sample)
            warn_msgs += [str(w.message).split(".")[0] for w in caught if issubclass(w.category, ConvergenceWarning)]

        # consistent signs: largest absolute loading of each component positive
        comps = getattr(model, "components_", None)
        flip = np.ones(n_comp)
        if comps is not None and method in ("pca", "incremental", "svd", "fa", "ica"):
            flip = np.sign(comps[np.arange(len(comps)), np.abs(comps).argmax(axis=1)])
            flip[flip == 0] = 1

        prefix = m["prefix"]
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        profile = {"driver": "GTiff", "width": out_w, "height": out_h, "count": n_comp, "dtype": "float32",
                   "crs": src.crs, "transform": transform, "nodata": np.nan, "compress": "deflate", "predictor": 3,
                   "BIGTIFF": "IF_SAFER"}
        if out_w >= 256 and out_h >= 256:
            profile.update(tiled=True, blockxsize=256, blockysize=256)
        log.info("Applying the model to the image")
        with rasterio.open(out_path, "w", **profile) as dst:
            for r0, rows, data in strips():
                progress.update(fit_end + (1 - fit_end) * r0 / out_h, f"Applying the model ({r0 / out_h:.0%} of the image)")
                x = data.reshape(len(bands), -1).T
                ok = np.all(np.isfinite(x), axis=1)
                out = np.full((x.shape[0], n_comp), np.nan, "float32")
                if ok.any():
                    for s in range(0, int(ok.sum()), 200_000):  # bounded memory, esp. for Kernel PCA
                        idx = np.flatnonzero(ok)[s:s + 200_000]
                        out[idx] = model.transform(prep(x[idx])) * flip
                dst.write(out.T.reshape(n_comp, rows, out_w), window=Window(0, r0, out_w, rows))
            for i in range(n_comp):
                dst.set_band_description(i + 1, f"{prefix}{i + 1}")

            report = {"method": method, "title": m["full"], "prefix": prefix, "bands": names, "band_indices": bands,
                      "n_components": n_comp, "pixels_fit": n_used, "width": out_w, "height": out_h, "factor": f,
                      "standardized": standardize, "params": {**common, **p}, "warnings": warn_msgs}
            evr = getattr(model, "explained_variance_ratio_", None)
            if evr is not None:
                report["explained_variance"] = [float(v) for v in evr]
            elif method == "kernel" and getattr(model, "eigenvalues_", None) is not None:
                ev = np.asarray(model.eigenvalues_, dtype="float64")
                report["explained_variance_kept"] = [float(v) for v in ev / ev.sum()]
            if comps is not None:
                report["loadings"] = (comps * flip[:, None]).round(4).tolist()
            if method == "nmf":
                report["reconstruction_error"] = float(model.reconstruction_err_)
            report["seconds"] = round(time.time() - t0, 1)
            dst.update_tags(method=m["full"], bands=",".join(names), report=json.dumps(report)[:30000],
                            units=f"{prefix} component scores")
    log.info("Done in %.1f s", report["seconds"])
    return report
