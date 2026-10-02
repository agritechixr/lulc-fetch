"""Classical machine learning on tables (e.g. from Raster → table): train, evaluate, save, and apply to rasters.

Models: Random Forest, Extra Trees, XGBoost, LightGBM, Histogram Gradient Boosting, Decision Tree,
SVM, SGD, Logistic Regression, Naive Bayes, Maximum Likelihood (Gaussian / QDA), LDA, k-NN and MLP.
Each model has tuned defaults for pixel data; the schema below also drives the web UI (labels, hints).
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import warnings
from pathlib import Path

import numpy as np

from . import progress

log = logging.getLogger(__name__)
COORD_COLUMNS = {"x", "y", "lon", "lat", "row", "col"}
ID_COLUMNS = {"poly_id", "sample_id", "fid", "id"}

# ------------------------------------------------------------------ model catalogue (drives the UI)
# family: grouping in the UI. speed / accuracy: 1–3 stars for typical pixel tables.
P = lambda name, label, type_, default, tip, **kw: {"name": name, "label": label, "type": type_, "default": default, "tip": tip, **kw}

MODELS = {
    "rf": {"title": "Random Forest", "family": "Trees", "recommended": True, "tasks": ["classification", "regression"],
           "speed": 3, "accuracy": 3, "scale": False, "max_rows": 300000,
           "desc": "Many decision trees voting together. Accurate, robust to noise, needs no feature scaling. The standard for land-cover mapping.",
           "tip": "Each tree learns from a random part of the data and a random subset of bands, and the forest averages them. It rarely overfits, handles any number of bands and gives feature importances. Start here.",
           "params": [
               P("n_estimators", "Number of trees", "int", 300, "More trees = more stable results, slower training. 200–500 is plenty for most tables.", min=10, max=3000),
               P("max_depth", "Max tree depth", "int", None, "Limit how deep each tree grows. Empty = unlimited (best for large tables). Set 10–20 if it overfits small tables.", min=1, max=200, placeholder="unlimited"),
               P("min_samples_leaf", "Min samples per leaf", "int", 1, "Smallest group of pixels a leaf may hold. Raise to 3–10 for smoother, less noisy maps.", min=1, max=1000, advanced=True),
               P("max_features", "Bands tried per split", "select", "sqrt", "How many bands each split considers. √(bands) is the classic choice; more = stronger trees but more alike.",
                 options=[["sqrt", "√ bands (recommended)"], ["log2", "log₂ bands"], ["0.5", "50% of bands"], ["1.0", "All bands"]], advanced=True),
           ]},
    "et": {"title": "Extra Trees", "family": "Trees", "tasks": ["classification", "regression"], "speed": 3, "accuracy": 3, "scale": False, "max_rows": 300000,
           "desc": "Like Random Forest but with random split points: faster and often smoother maps.",
           "tip": "Extremely Randomized Trees choose split thresholds at random, which reduces variance and trains faster than Random Forest. Accuracy is usually very similar.",
           "params": [
               P("n_estimators", "Number of trees", "int", 300, "More trees = more stable, slower.", min=10, max=3000),
               P("max_depth", "Max tree depth", "int", None, "Empty = unlimited.", min=1, max=200, placeholder="unlimited"),
               P("min_samples_leaf", "Min samples per leaf", "int", 1, "Raise for smoother maps.", min=1, max=1000, advanced=True),
           ]},
    "xgb": {"title": "XGBoost", "family": "Boosting", "tasks": ["classification", "regression"], "speed": 2, "accuracy": 3, "scale": False, "max_rows": 500000,
            "desc": "Gradient-boosted trees, often the most accurate on tabular data. Trees are built one after another, each fixing earlier mistakes.",
            "tip": "Strong and widely used in competitions. The defaults suit most pixel tables. Lower the learning rate with more trees for a little extra accuracy.",
            "params": [
                P("n_estimators", "Boosting rounds", "int", 400, "Number of trees added one after another.", min=10, max=5000),
                P("learning_rate", "Learning rate", "float", 0.1, "How much each tree corrects the previous ones. Smaller = more careful, needs more rounds.", min=0.001, max=1),
                P("max_depth", "Max tree depth", "int", 6, "Depth of each tree. 4–8 works well.", min=1, max=20),
                P("subsample", "Row subsample", "float", 0.8, "Share of rows used per tree. Below 1 reduces overfitting.", min=0.1, max=1, advanced=True),
                P("colsample_bytree", "Band subsample", "float", 0.8, "Share of bands used per tree.", min=0.1, max=1, advanced=True),
                P("reg_lambda", "L2 regularisation", "float", 1.0, "Penalty on large leaf values. Raise to reduce overfitting.", min=0, max=100, advanced=True),
            ]},
    "lgbm": {"title": "LightGBM", "family": "Boosting", "tasks": ["classification", "regression"], "speed": 3, "accuracy": 3, "scale": False, "max_rows": 1000000,
             "desc": "Very fast gradient boosting with leaf-wise trees. Great for large tables (hundreds of thousands of pixels).",
             "tip": "Similar accuracy to XGBoost, usually faster on big tables. 'Leaves per tree' is its main complexity setting.",
             "params": [
                 P("n_estimators", "Boosting rounds", "int", 400, "Number of trees.", min=10, max=5000),
                 P("learning_rate", "Learning rate", "float", 0.05, "Smaller = more careful, needs more rounds.", min=0.001, max=1),
                 P("num_leaves", "Leaves per tree", "int", 31, "Main complexity control. 15–63 is typical.", min=2, max=1024),
                 P("subsample", "Row subsample", "float", 0.8, "Share of rows used per tree.", min=0.1, max=1, advanced=True),
                 P("colsample_bytree", "Band subsample", "float", 0.8, "Share of bands used per tree.", min=0.1, max=1, advanced=True),
             ]},
    "hgb": {"title": "Hist. Gradient Boosting", "family": "Boosting", "tasks": ["classification", "regression"], "speed": 3, "accuracy": 3, "scale": False, "max_rows": 1000000,
            "desc": "scikit-learn's fast gradient boosting (LightGBM-style). Stops early by itself when it stops improving.",
            "tip": "Bins values into histograms for speed and uses early stopping on a validation split, so the number of iterations is a ceiling, not a target.",
            "params": [
                P("max_iter", "Max boosting rounds", "int", 300, "Upper limit. Early stopping usually stops sooner.", min=10, max=5000),
                P("learning_rate", "Learning rate", "float", 0.1, "Step size of each round.", min=0.001, max=1),
                P("max_leaf_nodes", "Leaves per tree", "int", 31, "Complexity of each tree.", min=2, max=1024, advanced=True),
                P("l2_regularization", "L2 regularisation", "float", 0.0, "Raise to reduce overfitting.", min=0, max=100, advanced=True),
            ]},
    "dt": {"title": "Decision Tree", "family": "Trees", "tasks": ["classification", "regression"], "speed": 3, "accuracy": 1, "scale": False, "max_rows": 1000000,
           "desc": "A single tree of yes/no rules. Easy to understand, but usually less accurate than forests.",
           "tip": "Good for explaining which band thresholds separate classes. Limit the depth to avoid memorising noise.",
           "params": [
               P("max_depth", "Max tree depth", "int", 15, "Deeper = more detailed rules but more overfitting.", min=1, max=100),
               P("min_samples_leaf", "Min samples per leaf", "int", 5, "Larger = simpler, smoother tree.", min=1, max=1000),
               P("criterion", "Split criterion", "select", "gini", "How split quality is measured. Results are usually similar.",
                 options=[["gini", "Gini"], ["entropy", "Entropy"], ["log_loss", "Log loss"]], advanced=True, tasks=["classification"]),
           ]},
    "svm": {"title": "SVM (RBF kernel)", "family": "Kernel", "tasks": ["classification", "regression"], "speed": 1, "accuracy": 3, "scale": True, "max_rows": 20000,
            "desc": "Support Vector Machine with a radial kernel. Very accurate on small, clean training sets; slow on large ones.",
            "tip": "Training time grows roughly with the square of the rows, so the training set is capped at 20,000 rows by default. Bands are standardized automatically.",
            "params": [
                P("C", "C (regularisation)", "float", 10.0, "Higher = fits training data more tightly (risk of overfitting). 1–100 is typical.", min=0.001, max=10000),
                P("gamma", "Gamma", "select", "scale", "Kernel width. 'scale' adapts to the data and is a good default.", options=[["scale", "scale (recommended)"], ["auto", "auto"]]),
                P("probability", "Estimate probabilities", "bool", False, "Needed for a confidence map when classifying an image, but makes training ~5× slower.", advanced=True, tasks=["classification"]),
            ]},
    "sgd": {"title": "SGD (linear)", "family": "Linear", "tasks": ["classification", "regression"], "speed": 3, "accuracy": 2, "scale": True, "max_rows": 2000000,
            "desc": "Linear model trained with stochastic gradient descent. Extremely fast on huge tables; best when classes are fairly separable.",
            "tip": "With 'hinge' loss it is a linear SVM; with 'log loss' it is logistic regression and gives probabilities. Bands are standardized automatically.",
            "params": [
                P("loss", "Loss", "select", "log_loss", "log loss gives class probabilities (confidence map); hinge is a linear SVM.",
                  options=[["log_loss", "Log loss (logistic), recommended"], ["hinge", "Hinge (linear SVM)"], ["modified_huber", "Modified Huber"]], tasks=["classification"]),
                P("alpha", "Regularisation (alpha)", "float", 0.0001, "Higher = simpler model.", min=1e-8, max=1),
                P("max_iter", "Max epochs", "int", 1000, "Passes over the data.", min=5, max=100000, advanced=True),
            ]},
    "lr": {"title": "Logistic Regression", "family": "Linear", "tasks": ["classification"], "speed": 3, "accuracy": 2, "scale": True, "max_rows": 500000,
           "desc": "Linear classifier with calibrated probabilities. A solid, interpretable baseline.",
           "tip": "Learns one linear boundary per class. Fast and stable; less accurate than trees when classes overlap in complex ways.",
           "params": [
               P("C", "C (inverse regularisation)", "float", 1.0, "Higher = less regularisation.", min=0.0001, max=10000),
               P("max_iter", "Max iterations", "int", 1000, "Raise if it doesn't converge.", min=50, max=100000, advanced=True),
           ]},
    "nb": {"title": "Naive Bayes", "family": "Probabilistic", "tasks": ["classification"], "speed": 3, "accuracy": 1, "scale": False, "max_rows": 5000000,
           "desc": "Gaussian Naive Bayes: assumes bands are independent within each class. Instant to train; a quick baseline.",
           "tip": "Very fast and needs little data, but the independence assumption is rarely true for correlated spectral bands, so accuracy is usually modest.",
           "params": [
               P("var_smoothing", "Variance smoothing", "float", 1e-9, "Added to variances for numerical stability.", min=0, max=1, advanced=True),
           ]},
    "mlc": {"title": "Maximum Likelihood", "family": "Probabilistic", "tasks": ["classification"], "speed": 3, "accuracy": 2, "scale": False, "max_rows": 5000000,
            "desc": "The classic remote-sensing classifier: each class is a multivariate Gaussian with its own covariance (Gaussian MLC / QDA).",
            "tip": "Assumes each class's pixel values follow a normal distribution and assigns each pixel to the most likely class. Needs more training pixels per class than bands.",
            "params": [
                P("priors", "Class priors", "select", "equal", "Equal priors is the traditional MLC choice. 'From data' favours classes with more training pixels.",
                  options=[["equal", "Equal (classic MLC)"], ["data", "From training data"]]),
                P("reg_param", "Covariance regularisation", "float", 0.001, "Small value that stabilises covariance matrices of correlated bands.", min=0, max=1, advanced=True),
            ]},
    "lda": {"title": "Linear Discriminant", "family": "Probabilistic", "tasks": ["classification"], "speed": 3, "accuracy": 2, "scale": False, "max_rows": 5000000,
            "desc": "Gaussian classes with one shared covariance. Simpler than Maximum Likelihood and good with few training pixels.",
            "tip": "Linear class boundaries. Robust when some classes have little training data.",
            "params": []},
    "knn": {"title": "k-Nearest Neighbours", "family": "Neighbours", "tasks": ["classification", "regression"], "speed": 2, "accuracy": 2, "scale": True, "max_rows": 50000,
            "desc": "Labels a pixel like its most similar training pixels. Simple and non-linear; slow to apply to big images.",
            "tip": "No real training: it stores the training pixels and compares every new pixel with them. Bands are standardized automatically.",
            "params": [
                P("n_neighbors", "Neighbours (k)", "int", 7, "How many similar pixels vote. 5–15 is typical.", min=1, max=200),
                P("weights", "Vote weights", "select", "distance", "Closer neighbours count more with 'distance'.", options=[["distance", "By distance"], ["uniform", "Equal"]]),
            ]},
    "mlp": {"title": "Neural network (MLP)", "family": "Neural", "tasks": ["classification", "regression"], "speed": 2, "accuracy": 3, "scale": True, "max_rows": 300000,
            "desc": "A small fully connected neural network. Captures complex patterns; benefits from more training data.",
            "tip": "Multi-layer perceptron with early stopping. Bands are standardized automatically. Results vary slightly with the random seed.",
            "params": [
                P("hidden_layer_sizes", "Hidden layers", "text", "128,64", "Neurons per layer, comma-separated. '128,64' = two layers.", maxlength=40),
                P("alpha", "L2 regularisation", "float", 0.0001, "Higher = simpler model.", min=0, max=10, advanced=True),
                P("max_iter", "Max epochs", "int", 300, "Training stops earlier if it stops improving.", min=10, max=5000, advanced=True),
            ]},
}

COMMON = [
    P("split", "Validation split", "select", "auto",
      "How test pixels are kept apart from training pixels. Neighbouring pixels look almost identical, so a random pixel split "
      "tests the model on near-copies of its training data and overstates accuracy. 'By polygon' keeps every training polygon "
      "entirely in train or in test. 'Spatial blocks' splits the map into squares. Auto picks the most honest option the table allows.",
      options=[["auto", "Auto (most honest available)"], ["group", "By polygon (whole samples)"], ["blocks", "Spatial blocks"],
               ["random", "Random pixels (optimistic)"]]),
    P("test_size", "Test split", "float", 0.25, "Share of rows kept aside to measure accuracy honestly (never used for training). 0.2–0.3 is standard.", min=0.05, max=0.5),
    P("class_weight", "Class balancing", "select", "none", "'Balanced' gives rare classes more weight, which improves their accuracy (and macro F1) when classes have very different sizes. Not needed if you used stratified sampling.",
      options=[["none", "None"], ["balanced", "Balanced"]], tasks=["classification"]),
    P("cv_folds", "Cross-validation folds", "int", 0, "Extra check: train k times on different parts of the training data and report the spread. 0 = off (faster). 5 is common.", min=0, max=10, advanced=True),
    P("missing", "Missing values", "select", "drop", "What to do with rows where a feature has no value: drop them (safest), or fill them in (median for numbers, a '(missing)' category for categories) so no rows are lost.",
      options=[["drop", "Drop those rows"], ["impute", "Fill in (median / most frequent)"]], advanced=True),
    P("max_train_rows", "Max training rows", "int", None, "Large tables are randomly sampled down (keeping class proportions) to this many training rows. Empty = the model's sensible default.", min=100, max=10000000, advanced=True, placeholder="model default"),
    P("scaling", "Feature scaling", "select", "auto", "Standardize bands (mean 0, std 1). Auto applies it only to models that need it (SVM, SGD, logistic, k-NN, MLP).",
      options=[["auto", "Auto (recommended)"], ["standard", "Always"], ["none", "Never"]], advanced=True),
    P("block_size", "Block size (map units)", "float", None, "Side of the squares used by 'Spatial blocks', e.g. metres for UTM. Empty = automatic (about 64 blocks over the area). Use blocks larger than your typical field.",
      min=0.000001, max=1e7, advanced=True, placeholder="auto"),
    P("random_state", "Random seed", "int", 0, "Makes the split and training reproducible.", min=0, max=2**31 - 1, advanced=True),
]


# Default hyperparameter search ranges (candidate values) per model, shown and editable in the UI.
SEARCH = {
    "rf": {"n_estimators": [100, 300, 600], "max_depth": [None, 10, 20, 40], "min_samples_leaf": [1, 2, 5], "max_features": ["sqrt", "0.5", "1.0"]},
    "et": {"n_estimators": [100, 300, 600], "max_depth": [None, 10, 20], "min_samples_leaf": [1, 2, 5]},
    "xgb": {"n_estimators": [200, 400, 800], "learning_rate": [0.03, 0.1, 0.3], "max_depth": [4, 6, 8], "subsample": [0.7, 0.9, 1.0], "colsample_bytree": [0.7, 0.9, 1.0]},
    "lgbm": {"n_estimators": [200, 400, 800], "learning_rate": [0.02, 0.05, 0.1], "num_leaves": [15, 31, 63], "subsample": [0.7, 0.9, 1.0]},
    "hgb": {"max_iter": [200, 400], "learning_rate": [0.05, 0.1, 0.2], "max_leaf_nodes": [15, 31, 63], "l2_regularization": [0.0, 0.1, 1.0]},
    "dt": {"max_depth": [5, 10, 15, 25, None], "min_samples_leaf": [1, 5, 10, 20]},
    "svm": {"C": [1, 10, 100, 1000], "gamma": ["scale", "auto"]},
    "sgd": {"alpha": [1e-5, 1e-4, 1e-3, 1e-2]},
    "lr": {"C": [0.01, 0.1, 1, 10, 100]},
    "nb": {"var_smoothing": [1e-11, 1e-9, 1e-7, 1e-5]},
    "mlc": {"reg_param": [0.0, 0.001, 0.01, 0.1]},
    "lda": {},
    "knn": {"n_neighbors": [3, 5, 7, 11, 15, 25], "weights": ["distance", "uniform"]},
    "mlp": {"hidden_layer_sizes": ["64", "128;64", "256;128"], "alpha": [1e-5, 1e-4, 1e-3]},
}
TUNE_METRICS = {
    "classification": [["auto", "Overall accuracy (default)"], ["f1_macro", "Macro F1 (rare classes matter as much)"],
                       ["balanced_accuracy", "Balanced accuracy"], ["kappa", "Kappa"]],
    "regression": [["auto", "R² (default)"], ["neg_rmse", "RMSE (lower is better)"], ["neg_mae", "MAE (lower is better)"]],
}


def available() -> dict:
    ok = {}
    for mod in ("xgboost", "lightgbm"):
        try:
            __import__(mod)
            ok[mod] = True
        except Exception:  # ImportError or a missing OpenMP runtime
            ok[mod] = False
    return {"xgb": ok["xgboost"], "lgbm": ok["lightgbm"]}


def schema() -> dict:
    av = available()
    return {"models": MODELS, "common": COMMON, "search": SEARCH, "tune_metrics": TUNE_METRICS,
            "unavailable": [k for k, v in av.items() if not v]}


# ------------------------------------------------------------------ data

def read_table(path: str | Path, columns: list[str] | None = None):
    """Load a CSV / Parquet table as {column: numpy array} (strings stay as object arrays)."""
    import pyarrow.compute as pc

    path = Path(path)
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq
        t = pq.read_table(path, columns=columns)
    else:
        import pyarrow.csv as pcsv
        t = pcsv.read_csv(path, convert_options=pcsv.ConvertOptions(include_columns=columns) if columns else None)
    out = {}
    for name in t.column_names:
        col = t.column(name)
        if col.type in ("string", "large_string") or str(col.type).startswith(("string", "large_string", "dictionary")):
            out[name] = np.array(pc.cast(col, "string").to_pylist(), dtype=object)
        else:
            out[name] = col.to_numpy(zero_copy_only=False).astype("float64") if str(col.type) != "bool" else col.to_numpy(zero_copy_only=False)
    return out


def describe_table(path: str | Path) -> dict:
    """Columns with type and number of distinct values, plus the table's sidecar metadata."""
    data = read_table(path)
    n = len(next(iter(data.values()))) if data else 0
    cols = []
    for name, arr in data.items():
        text = arr.dtype == object
        sample = arr[: min(n, 200000)]
        uniq = len(set(sample.tolist())) if text else len(np.unique(sample[np.isfinite(sample)]))
        integer = not text and np.all(np.mod(sample[np.isfinite(sample)], 1) == 0)
        vals = list(dict.fromkeys(sample.tolist()))[:6] if text else np.unique(sample[np.isfinite(sample)])[:6].tolist()
        typ = "text" if text else ("integer" if integer else "number")
        cols.append({"name": name, "type": typ, "unique": int(uniq), "examples": [str(v) if text else (int(v) if float(v).is_integer() else round(float(v), 6)) for v in vals],
                     "suggest": "categorical" if text or (integer and uniq <= 20) else "numeric",
                     "nulls": int(np.sum([v is None or v == "" for v in sample])) if text else int(np.sum(~np.isfinite(sample)))})
    meta = {}
    side = Path(str(path) + ".json")
    if side.exists():
        try:
            meta = json.loads(side.read_text())
        except ValueError:
            pass
    return {"rows": n, "columns": cols, "meta": meta}


def _header(path) -> list[str]:
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh), [])


def _detect_task(y: np.ndarray) -> str:
    if y.dtype == object:
        return "classification"
    finite = y[np.isfinite(y)]
    if np.all(np.mod(finite, 1) == 0) and len(np.unique(finite)) <= 100:
        return "classification"
    return "regression"


# ------------------------------------------------------------------ model building

def _param(params: dict, spec: dict):
    v = params.get(spec["name"], spec["default"])
    if v in ("", None):
        return spec["default"]
    t = spec["type"]
    if t == "int":
        v = int(v)
    elif t == "float":
        v = float(v)
    elif t == "bool":
        v = bool(v)
    elif t == "select" and v not in [o[0] for o in spec["options"]]:
        raise ValueError(f"Invalid value for {spec['label']}: {v}")
    if t in ("int", "float") and v is not None:
        if "min" in spec and v < spec["min"]:
            raise ValueError(f"{spec['label']} must be at least {spec['min']}")
        if "max" in spec and v > spec["max"]:
            raise ValueError(f"{spec['label']} must be at most {spec['max']}")
    return v


def _build(model_id: str, task: str, p: dict, seed: int, class_weight):
    cls = task == "classification"
    cw = class_weight if cls else None
    if model_id in ("rf", "et"):
        from sklearn import ensemble as e
        Est = {("rf", True): e.RandomForestClassifier, ("rf", False): e.RandomForestRegressor,
               ("et", True): e.ExtraTreesClassifier, ("et", False): e.ExtraTreesRegressor}[(model_id, cls)]
        mf = p.get("max_features", "sqrt")
        mf = float(mf) if mf not in ("sqrt", "log2") else mf
        kw = dict(n_estimators=p["n_estimators"], max_depth=p["max_depth"], min_samples_leaf=p["min_samples_leaf"],
                  n_jobs=-1, random_state=seed, warm_start=True)
        if model_id == "rf":
            kw["max_features"] = mf if cls or mf != "sqrt" else 1.0
        if cls:
            kw["class_weight"] = cw
        return Est(**kw)
    if model_id == "xgb":
        import xgboost as xgb
        Est = xgb.XGBClassifier if cls else xgb.XGBRegressor
        return Est(n_estimators=p["n_estimators"], learning_rate=p["learning_rate"], max_depth=p["max_depth"],
                   subsample=p["subsample"], colsample_bytree=p["colsample_bytree"], reg_lambda=p["reg_lambda"],
                   tree_method="hist", n_jobs=-1, random_state=seed)
    if model_id == "lgbm":
        import lightgbm as lgb
        Est = lgb.LGBMClassifier if cls else lgb.LGBMRegressor
        kw = dict(n_estimators=p["n_estimators"], learning_rate=p["learning_rate"], num_leaves=p["num_leaves"],
                  subsample=p["subsample"], subsample_freq=1, colsample_bytree=p["colsample_bytree"],
                  n_jobs=-1, random_state=seed, verbose=-1)
        if cls:
            kw["class_weight"] = cw
        return Est(**kw)
    if model_id == "hgb":
        from sklearn import ensemble as e
        Est = e.HistGradientBoostingClassifier if cls else e.HistGradientBoostingRegressor
        kw = dict(max_iter=p["max_iter"], learning_rate=p["learning_rate"], max_leaf_nodes=p["max_leaf_nodes"],
                  l2_regularization=p["l2_regularization"], early_stopping="auto", random_state=seed)
        if cls:
            kw["class_weight"] = cw
        return Est(**kw)
    if model_id == "dt":
        from sklearn import tree
        if cls:
            return tree.DecisionTreeClassifier(max_depth=p["max_depth"], min_samples_leaf=p["min_samples_leaf"],
                                               criterion=p.get("criterion", "gini"), class_weight=cw, random_state=seed)
        return tree.DecisionTreeRegressor(max_depth=p["max_depth"], min_samples_leaf=p["min_samples_leaf"], random_state=seed)
    if model_id == "svm":
        from sklearn import svm
        if cls:
            return svm.SVC(C=p["C"], gamma=p["gamma"], kernel="rbf", probability=p.get("probability", False),
                           class_weight=cw, random_state=seed, cache_size=500)
        return svm.SVR(C=p["C"], gamma=p["gamma"], kernel="rbf", cache_size=500)
    if model_id == "sgd":
        from sklearn import linear_model as lm
        if cls:
            return lm.SGDClassifier(loss=p.get("loss", "log_loss"), alpha=p["alpha"], max_iter=p["max_iter"],
                                    early_stopping=True, class_weight=cw, random_state=seed, n_jobs=-1)
        return lm.SGDRegressor(alpha=p["alpha"], max_iter=p["max_iter"], early_stopping=True, random_state=seed)
    if model_id == "lr":
        from sklearn.linear_model import LogisticRegression
        return LogisticRegression(C=p["C"], max_iter=p["max_iter"], class_weight=cw)
    if model_id == "nb":
        from sklearn.naive_bayes import GaussianNB
        return GaussianNB(var_smoothing=p["var_smoothing"])
    if model_id == "mlc":
        from sklearn.discriminant_analysis import QuadraticDiscriminantAnalysis
        return QuadraticDiscriminantAnalysis(reg_param=p["reg_param"])  # priors set at fit time
    if model_id == "lda":
        from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
        return LinearDiscriminantAnalysis()
    if model_id == "knn":
        from sklearn import neighbors as nb
        Est = nb.KNeighborsClassifier if cls else nb.KNeighborsRegressor
        return Est(n_neighbors=p["n_neighbors"], weights=p["weights"], n_jobs=-1)
    if model_id == "mlp":
        from sklearn import neural_network as nn
        try:
            layers = tuple(int(v) for v in str(p["hidden_layer_sizes"]).replace(" ", "").split(",") if v)
        except ValueError:
            raise ValueError("Hidden layers must be numbers separated by commas, e.g. 128,64") from None
        if not layers or any(v < 1 or v > 4096 for v in layers):
            raise ValueError("Hidden layers must be between 1 and 4096 neurons each")
        Est = nn.MLPClassifier if cls else nn.MLPRegressor
        return Est(hidden_layer_sizes=layers, alpha=p["alpha"], max_iter=p["max_iter"], early_stopping=True, random_state=seed)
    raise ValueError(f"Unknown model {model_id}")


def _fit_with_progress(model_id: str, est, X, y, sample_weight, task: str):
    """Fit, reporting real progress where the library allows it (and letting Cancel stop training)."""
    if model_id in ("rf", "et"):
        total = est.n_estimators
        steps = sorted({max(1, round(total * k / 10)) for k in range(1, 11)})
        for n in steps:
            est.set_params(n_estimators=n)
            est.fit(X, y, sample_weight=sample_weight)
            progress.update(n / total, f"Growing trees ({n}/{total})")
        return est
    if model_id == "xgb":
        import xgboost as xgb

        class _Progress(xgb.callback.TrainingCallback):
            def after_iteration(self, model, epoch, evals_log):
                if epoch % 5 == 0:
                    progress.update((epoch + 1) / est.n_estimators, f"Boosting round {epoch + 1}/{est.n_estimators}")
                return False

        est.set_params(callbacks=[_Progress()])
        est.fit(X, y, sample_weight=sample_weight)
        est.set_params(callbacks=None)
        return est
    if model_id == "lgbm":
        def cb(env):
            if env.iteration % 5 == 0:
                progress.update((env.iteration + 1) / env.end_iteration, f"Boosting round {env.iteration + 1}/{env.end_iteration}")
        est.fit(X, y, sample_weight=sample_weight, callbacks=[cb])
        return est
    progress.update(0.05, "Training")
    if model_id == "mlc" and task == "classification" and getattr(est, "_equal_priors", False):
        k = len(np.unique(y))
        est.set_params(priors=np.full(k, 1.0 / k))
    if sample_weight is not None and model_id in ("hgb", "xgb", "nb"):
        est.fit(X, y, sample_weight=sample_weight)
    else:
        est.fit(X, y)
    return est


# ------------------------------------------------------------------ training

def _cat_strings(X):
    """Categorical columns → strings (whole numbers lose their '.0', so codes from a raster match the table)."""
    def one(v):
        if v is None:
            return "(missing)"
        if isinstance(v, (float, np.floating)):
            if not np.isfinite(v):
                return "(missing)"
            return str(int(v)) if float(v).is_integer() else str(v)
        s = str(v)
        return s if s != "" else "(missing)"
    X = np.asarray(X, dtype=object)
    return np.vectorize(one, otypes=[object])(X) if X.size else X


def _scorer(metric: str, task: str):
    from sklearn.metrics import cohen_kappa_score, get_scorer, make_scorer
    if metric == "auto":
        metric = "accuracy" if task == "classification" else "r2"
    names = {"neg_rmse": "neg_root_mean_squared_error", "neg_mae": "neg_mean_absolute_error"}
    if metric == "kappa":
        return metric, make_scorer(cohen_kappa_score)
    clf_metrics = {"accuracy", "f1_macro", "balanced_accuracy", "kappa"}
    if (task == "classification") != (metric in clf_metrics):
        raise ValueError(f"'{metric}' is not a {task} metric")
    return metric, get_scorer(names.get(metric, metric))


def _parse_space(model: str, task: str, space: dict) -> dict:
    """Candidate values typed into the UI (strings) → typed parameter lists, validated against the schema."""
    specs = {s["name"]: s for s in MODELS[model]["params"] if task in s.get("tasks", [task])}
    out = {}
    for name, values in (space or {}).items():
        if name not in specs:
            raise ValueError(f"'{name}' can't be tuned for {MODELS[model]['title']}")
        spec, typed = specs[name], []
        vals = values if isinstance(values, list) else str(values).split(",")
        for v in vals:
            v = str(v).strip()
            if v == "":
                continue
            if v.lower() in ("none", "unlimited", "null") and (spec["default"] is None or name == "max_depth"):
                typed.append(None)
                continue
            if spec["type"] == "text":
                typed.append(v.replace(";", ","))  # e.g. hidden layers "128;64"
                continue
            typed.append(_param({name: v}, spec))
        if typed:
            out[name] = list(dict.fromkeys(typed))
    return out


def train(table_path: str | Path, out_dir: str | Path | None, *, target: str, features: list[str], model: str = "rf",
          task: str = "auto", params: dict | None = None, common: dict | None = None, name: str = "model",
          categorical: list[str] | None = None, tuning: dict | None = None, save: bool = True) -> dict:
    """Train a model on a table, evaluate it on a held-out test split, and save it (joblib + JSON report).

    categorical: feature columns to one-hot encode (text columns always are). tuning: {"enabled", "method",
    "iter", "folds", "metric", "space": {param: [values]}}: hyperparameter search with cross-validation that
    respects the same polygon / block grouping as the test split.
    """
    import joblib
    from sklearn.base import clone
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import FunctionTransformer, LabelEncoder, OneHotEncoder, StandardScaler

    t0 = time.time()
    if model not in MODELS:
        raise ValueError(f"Unknown model {model}")
    m = MODELS[model]
    if model in available() and not available()[model]:
        raise ValueError(f"{m['title']} is not installed on this computer")
    if not features:
        raise ValueError("Select at least one feature column")
    if target in features:
        raise ValueError("The target column can't also be a feature")
    params, common, tuning = params or {}, common or {}, tuning or {}

    progress.update(0.0, "Loading the table")
    meta = {}
    side = Path(str(table_path) + ".json")
    if side.exists():
        try:
            meta = json.loads(side.read_text())
        except ValueError:
            pass
    c = {s["name"]: _param(common, s) for s in COMMON}
    import pyarrow.parquet as _pq
    available_cols = (_pq.ParquetFile(table_path).schema_arrow.names if str(table_path).endswith(".parquet")
                      else _header(table_path))
    missing_cols = [f for f in features + [target] if f not in available_cols]
    if missing_cols:
        raise ValueError(f"Column(s) not in the table: {', '.join(missing_cols)}")
    group_col = meta.get("group_column") or next((g for g in ("poly_id", "sample_id") if g in available_cols), None)
    split = c["split"]
    if split == "auto":
        split = "group" if group_col in available_cols else ("blocks" if {"x", "y"} <= set(available_cols) else "random")
    if split == "group" and group_col not in available_cols:
        raise ValueError("'By polygon' needs a polygon id column (made by Raster → table from vector ground truth)")
    if split == "blocks" and not {"x", "y"} <= set(available_cols):
        raise ValueError("'Spatial blocks' needs x and y columns (tick 'Map x / y' in Raster → table)")
    extra = [group_col] if split == "group" else (["x", "y"] if split == "blocks" else [])
    data = read_table(table_path, columns=list(dict.fromkeys(features + [target] + extra)))

    # ---- columns: numeric vs categorical
    cat_set = set(categorical or []) | {f for f in features if data[f].dtype == object}
    cat_cols = [f for f in features if f in cat_set]
    num_cols = [f for f in features if f not in cat_set]
    impute = c["missing"] == "impute"
    if cat_cols:
        X = np.empty((len(data[target]), len(features)), dtype=object)
        for i, f in enumerate(features):
            X[:, i] = data[f]
    else:
        X = np.column_stack([data[f] for f in features]).astype("float64")
    num_idx = [features.index(f) for f in num_cols]
    cat_idx = [features.index(f) for f in cat_cols]

    y_raw = data[target]
    task = _detect_task(y_raw) if task == "auto" else task
    if task not in m["tasks"]:
        raise ValueError(f"{m['title']} can't do {task}")
    if task == "regression" and y_raw.dtype == object:
        raise ValueError(f"The target '{target}' contains text, so it can't be numeric. Choose 'Categories (classification)'.")
    p = {s["name"]: _param(params, s) for s in m["params"] if task in s.get("tasks", [task])}
    seed = c["random_state"]

    # ---- missing values: the target must be known; features are dropped or filled in
    ok = np.array([v not in (None, "") for v in y_raw]) if y_raw.dtype == object else np.isfinite(y_raw)
    num_nan = 0
    if num_cols:
        Xn = X[:, num_idx].astype("float64")
        finite = np.isfinite(Xn)
        num_nan = int((~finite.all(axis=1) & ok).sum())
        if not impute:
            ok &= finite.all(axis=1)
    if cat_cols and not impute:
        ok &= np.array([all(v not in (None, "") and not (isinstance(v, float) and not np.isfinite(v)) for v in row) for row in X[:, cat_idx]])
    dropped = int((~ok).sum())
    X, y_raw = X[ok], y_raw[ok]
    groups, block = None, None
    if split == "group":
        groups = data[group_col][ok]
        groups = np.asarray(groups, dtype=object if groups.dtype == object else "float64")
    elif split == "blocks":
        gx, gy = data["x"][ok], data["y"][ok]
        block = float(c["block_size"] or max(gx.max() - gx.min(), gy.max() - gy.min()) / 8 or 1.0)
        groups = np.floor((gx - gx.min()) / block) * 1e6 + np.floor((gy - gy.min()) / block)
    if len(X) < 20:
        raise ValueError("Too few usable rows to train (need at least 20)")

    if task == "classification":
        y_labels = np.array([str(v) if y_raw.dtype == object else (int(v) if float(v).is_integer() else v) for v in y_raw], dtype=object)
        encoder = LabelEncoder().fit(y_labels.astype(str))
        y = encoder.transform(y_labels.astype(str))
        classes = list(encoder.classes_)
        counts = np.bincount(y, minlength=len(classes))
        if len(classes) < 2:
            raise ValueError("The target has only one class")
    else:
        y = y_raw.astype("float64")
        classes, counts = None, None

    # ---- honest train / test split
    split_info = {"method": split, "group_column": group_col if split == "group" else None, "block_size": block}
    split_warnings = []
    g_tr = None
    if groups is not None:
        from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold
        n_groups = len(np.unique(groups.astype(str)))
        n_splits = max(2, round(1 / c["test_size"]))
        if n_groups < max(4, n_splits):
            split_warnings.append(f"Only {n_groups} polygons/blocks: too few for a grouped split, so random pixels were used. "
                                  "Draw more, smaller training polygons for an honest accuracy.")
            groups, split = None, "random"
            split_info["method"] = "random"
        else:
            gkey = np.unique(groups.astype(str), return_inverse=True)[1]
            if task == "classification" and counts.min() >= 2:
                tr_idx, te_idx = next(StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed).split(X, y, gkey))
            else:
                tr_idx, te_idx = next(GroupShuffleSplit(n_splits=1, test_size=c["test_size"], random_state=seed).split(X, y, gkey))
            X_tr, X_te, y_tr, y_te = X[tr_idx], X[te_idx], y[tr_idx], y[te_idx]
            g_tr = gkey[tr_idx]
            split_info.update(groups_total=int(n_groups), groups_train=int(len(np.unique(gkey[tr_idx]))),
                              groups_test=int(len(np.unique(gkey[te_idx]))))
            if task == "classification":
                missing = [classes[k] for k in range(len(classes)) if not np.any(y_te == k)]
                if missing:
                    split_warnings.append(f"No test pixels for: {', '.join(map(str, missing))} (too few polygons of "
                                          f"{'that class' if len(missing) == 1 else 'those classes'}). Draw more samples for a full assessment.")
    if groups is None:
        stratify = y if task == "classification" and counts.min() >= 2 else None
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=c["test_size"], random_state=seed, stratify=stratify)
    cap = c["max_train_rows"] or m["max_rows"]
    sampled_from = len(X_tr)
    if len(X_tr) > cap:
        strat = y_tr if task == "classification" and np.bincount(y_tr).min() >= 2 else None
        keep = train_test_split(np.arange(len(X_tr)), train_size=cap, random_state=seed, stratify=strat)[0]
        X_tr, y_tr = X_tr[keep], y_tr[keep]
        g_tr = g_tr[keep] if g_tr is not None else None
        log.info("Training set sampled from %s to %s rows", f"{sampled_from:,}", f"{cap:,}")

    # ---- preprocessing: impute, scale numeric; one-hot encode categorical (part of the saved model)
    scale = c["scaling"] == "standard" or (c["scaling"] == "auto" and m["scale"])
    transformers = []
    num_steps = ([("impute", SimpleImputer(strategy="median"))] if impute and num_nan else []) + ([("scale", StandardScaler())] if scale else [])
    if num_cols and (num_steps or cat_cols):
        transformers.append(("num", Pipeline(num_steps) if num_steps else "passthrough", num_idx))
    if cat_cols:
        transformers.append(("cat", Pipeline([("str", FunctionTransformer(_cat_strings)),
                                              ("onehot", OneHotEncoder(handle_unknown="ignore", max_categories=50, sparse_output=False))]), cat_idx))
    pre = ColumnTransformer(transformers) if transformers else (Pipeline(num_steps) if num_steps else None)

    cw = c["class_weight"] if task == "classification" else "none"

    def fit_one(params_i, Xa, ya, progress_fit=False):
        sw = None
        if cw == "balanced" and model in ("xgb", "hgb", "nb"):
            from sklearn.utils.class_weight import compute_sample_weight
            sw = compute_sample_weight("balanced", ya)
        est_i = _build(model, task, params_i, seed, "balanced" if cw == "balanced" else None)
        if model == "mlc":
            est_i._equal_priors = params_i["priors"] == "equal"
        if progress_fit:
            return _fit_with_progress(model, est_i, Xa, ya, sw, task)
        if model in ("rf", "et"):
            est_i.set_params(warm_start=False)
        if model == "mlc" and est_i._equal_priors:
            k = len(np.unique(ya))
            est_i.set_params(priors=np.full(k, 1.0 / k))
        return est_i.fit(Xa, ya, sample_weight=sw) if sw is not None else est_i.fit(Xa, ya)

    def cv_splits(folds, Xa, ya, ga):
        from sklearn.model_selection import GroupKFold, KFold, StratifiedGroupKFold, StratifiedKFold
        if ga is not None and len(np.unique(ga)) >= folds:
            sp = StratifiedGroupKFold(folds, shuffle=True, random_state=seed) if task == "classification" else GroupKFold(folds)
            return list(sp.split(Xa, ya, ga)), True
        sp = StratifiedKFold(folds, shuffle=True, random_state=seed) if task == "classification" and np.bincount(ya).min() >= folds \
            else KFold(folds, shuffle=True, random_state=seed)
        return list(sp.split(Xa, ya)), False

    # ---- hyperparameter tuning (optional)
    tune_report = None
    fit_span = (0.05, 0.8)
    if tuning.get("enabled"):
        from sklearn.model_selection import ParameterGrid, ParameterSampler
        space = _parse_space(model, task, tuning.get("space") or {})
        if not space:
            raise ValueError("Add at least one parameter with candidate values to tune")
        folds = int(tuning.get("folds") or 3)
        if not 2 <= folds <= 10:
            raise ValueError("Cross-validation folds must be between 2 and 10")
        metric, scorer = _scorer(tuning.get("metric") or "auto", task)
        grid_size = int(np.prod([len(v) for v in space.values()]))
        if tuning.get("method") == "grid":
            if grid_size > 300:
                raise ValueError(f"The grid has {grid_size} combinations. Use random search or fewer values (max 300).")
            cands = list(ParameterGrid(space))
        else:
            n_iter = max(1, min(int(tuning.get("iter") or 20), grid_size, 500))
            cands = list(ParameterSampler(space, n_iter=n_iter, random_state=seed))
        splits, grouped = cv_splits(folds, X_tr, y_tr, g_tr)
        results, total, done = [], len(cands) * folds, 0
        log.info("Tuning %s: %d candidates × %d folds (%s)", m["title"], len(cands), folds, "grouped" if grouped else "random folds")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for i, cand in enumerate(cands):
                scores = []
                for k, (a, b) in enumerate(splits):
                    progress.update(0.02 + 0.6 * done / total, f"Tuning: try {i + 1}/{len(cands)}, fold {k + 1}/{folds}")
                    pk = clone(pre) if pre is not None else None
                    Xa = pk.fit_transform(X_tr[a]) if pk is not None else X_tr[a]
                    Xb = pk.transform(X_tr[b]) if pk is not None else X_tr[b]
                    est_k = fit_one({**p, **cand}, Xa, y_tr[a])
                    scores.append(float(scorer(est_k, Xb, y_tr[b])))
                    done += 1
                results.append({"params": {k2: (None if v is None else v) for k2, v in cand.items()},
                                "mean": float(np.mean(scores)), "std": float(np.std(scores))})
        results.sort(key=lambda r: -r["mean"])
        best = results[0]
        p = {**p, **best["params"]}
        neg = metric.startswith("neg_")
        tune_report = {"method": tuning.get("method") or "random", "metric": metric, "folds": folds, "grouped": grouped,
                       "candidates": len(cands), "grid_size": grid_size, "best_params": best["params"],
                       "best_score": -best["mean"] if neg else best["mean"], "lower_is_better": neg,
                       "results": [{**r, "mean": -r["mean"] if neg else r["mean"]} for r in results[:15]]}
        log.info("Best %s = %.4f with %s", metric, tune_report["best_score"], best["params"])
        fit_span = (0.62, 0.85)

    Xs_tr = pre.fit_transform(X_tr) if pre is not None else X_tr
    Xs_te = pre.transform(X_te) if pre is not None else X_te
    log.info("Training %s (%s) on %s rows × %d features", m["title"], task, f"{len(X_tr):,}", len(features))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with progress.span(*fit_span):
            est = fit_one(p, Xs_tr, y_tr, progress_fit=True)
        warn_msgs = list(dict.fromkeys(str(w.message).split("\n")[0][:160] for w in caught
                                       if w.category.__name__ in ("ConvergenceWarning", "UserWarning")))[:3]
    pipe = Pipeline([("pre", pre), ("model", est)]) if pre is not None else est

    cv = None
    if c["cv_folds"] >= 2:
        progress.update(0.86, f"{c['cv_folds']}-fold cross-validation")
        splits, _ = cv_splits(c["cv_folds"], X_tr, y_tr, g_tr)
        scores = []
        for a, b in splits:
            pk = clone(pre) if pre is not None else None
            Xa = pk.fit_transform(X_tr[a]) if pk is not None else X_tr[a]
            Xb = pk.transform(X_tr[b]) if pk is not None else X_tr[b]
            e_k = fit_one(p, Xa, y_tr[a])
            pr_k = e_k.predict(Xb)
            from sklearn.metrics import accuracy_score, r2_score
            scores.append(accuracy_score(y_tr[b], pr_k) if task == "classification" else r2_score(y_tr[b], pr_k))
        cv = {"metric": "accuracy" if task == "classification" else "r2", "mean": float(np.mean(scores)),
              "std": float(np.std(scores)), "folds": [float(s) for s in scores]}

    progress.update(0.9, "Evaluating on the test split")
    pred = est.predict(Xs_te)
    warnings.filterwarnings("ignore", message="y_pred contains classes not in y_true")
    report = {"name": name, "model": model, "model_title": m["title"], "task": task, "target": target, "features": features,
              "categorical": cat_cols, "text_columns": [f for f in cat_cols if data[f].dtype == object], "table": str(table_path), "rows_total": int(ok.sum()), "rows_dropped": dropped,
              "missing": c["missing"], "rows_imputed": num_nan if impute else 0, "train_rows": len(X_tr),
              "train_rows_before_sampling": sampled_from, "test_rows": len(X_te), "scaled": bool(scale),
              "params": p, "common": c, "warnings": split_warnings + warn_msgs, "cv": cv, "split": split_info, "tuning": tune_report}
    from sklearn import metrics as mt
    if task == "classification":
        labels = list(range(len(classes)))
        pr, rc, f1, sup = mt.precision_recall_fscore_support(y_te, pred, labels=labels, zero_division=0)
        report.update(
            classes=classes, class_counts={cl: int(n) for cl, n in zip(classes, counts)},
            accuracy=float(mt.accuracy_score(y_te, pred)), kappa=float(mt.cohen_kappa_score(y_te, pred)),
            balanced_accuracy=float(mt.balanced_accuracy_score(y_te, pred)),
            f1_macro=float(mt.f1_score(y_te, pred, average="macro", zero_division=0)),
            f1_weighted=float(mt.f1_score(y_te, pred, average="weighted", zero_division=0)),
            per_class=[{"class": cl, "precision": float(a), "recall": float(b), "f1": float(f), "support": int(s)}
                       for cl, a, b, f, s in zip(classes, pr, rc, f1, sup)],
            confusion=mt.confusion_matrix(y_te, pred, labels=labels).tolist(),
            has_proba=hasattr(est, "predict_proba") and (model != "svm" or p.get("probability")) and not (model == "sgd" and p.get("loss") == "hinge"),
        )
    else:
        idx = np.random.default_rng(seed).choice(len(y_te), size=min(600, len(y_te)), replace=False)
        report.update(r2=float(mt.r2_score(y_te, pred)), rmse=float(math.sqrt(mt.mean_squared_error(y_te, pred))),
                      mae=float(mt.mean_absolute_error(y_te, pred)),
                      scatter={"true": y_te[idx].round(5).tolist(), "pred": np.asarray(pred)[idx].round(5).tolist()})

    # ---- feature importance per original column (one-hot columns are summed back to their source column)
    imp, kind = None, None
    if hasattr(est, "feature_importances_"):
        raw = np.asarray(est.feature_importances_, dtype="float64")
        owners = list(num_cols) if cat_cols else list(features)
        if cat_cols:
            oh = pre.named_transformers_["cat"].named_steps["onehot"]
            for name_out in oh.get_feature_names_out(cat_cols):
                owners.append(max((cc for cc in cat_cols if name_out.startswith(cc + "_")), key=len, default=cat_cols[0]))
            if not num_cols or len(owners) != len(raw):  # safety: fall back to transformed order
                owners = owners[:len(raw)]
        imp = np.array([raw[[i for i, o in enumerate(owners) if o == f]].sum() for f in features])
        kind = "built-in (impurity / gain)"
    elif len(features) > 1:
        progress.update(0.93, "Measuring feature importance")
        from sklearn.inspection import permutation_importance
        n = min(3000, len(X_te))
        sel = np.random.default_rng(seed).choice(len(X_te), n, replace=False)
        r = permutation_importance(pipe, X_te[sel], np.asarray(y_te)[sel], n_repeats=3, random_state=seed, n_jobs=1)
        imp, kind = np.clip(r.importances_mean, 0, None), "permutation (drop in score when a feature is shuffled)"
    if imp is not None and imp.sum() > 0:
        imp = imp / imp.sum()
        order = np.argsort(-imp)
        report["importance"] = {"kind": kind, "features": [features[i] for i in order], "values": [float(imp[i]) for i in order]}

    report["source"] = {k: meta.get(k) for k in ("source", "band_columns", "band_indices", "scale", "offset", "crs",
                                                 "pixel_size", "classes", "class_colors")}
    report["seconds"] = round(time.time() - t0, 1)
    # test predictions for the HTML evaluation report (probabilities aligned to the class order)
    proba_te = None
    if task == "classification" and report["has_proba"]:
        try:
            pr_raw = est.predict_proba(Xs_te)
            proba_te = np.zeros((len(Xs_te), len(classes)), dtype="float32")
            proba_te[:, np.asarray(getattr(est, "classes_", np.arange(pr_raw.shape[1]))).astype(int)] = pr_raw
        except Exception as e:  # probabilities are optional for the report
            log.info("No probabilities for the report: %s", e)
    from . import evaluation
    ev = evaluation.compact_eval(y_te, pred, proba_te, np.bincount(y_tr, minlength=len(classes)) if classes else None, seed=seed)
    if not save:
        return report
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "model"
    bundle = {"pipeline": pipe, "classes": classes, "task": task, "features": features, "target": target,
              "categorical": cat_cols, "model": model, "report": report, "evaluation": ev}
    path = out_dir / f"{stem}.joblib"
    joblib.dump(bundle, path, compress=3)
    report["path"] = str(path)
    progress.update(0.97, "Writing the evaluation report")
    eval_path = out_dir / f"{stem}.evaluation.html"
    try:
        evaluation.write_report(report, {**ev, "y_true": y_te, "y_pred": pred, "proba": proba_te}, eval_path)
        report["evaluation_html"] = str(eval_path)
    except Exception as e:  # never lose a trained model because of a report problem
        log.warning("Couldn't write the evaluation report: %s", e)
    Path(str(path) + ".json").write_text(json.dumps(report, indent=1, default=str))
    log.info("%s trained in %.1f s · %s", m["title"], report["seconds"],
             f"accuracy {report['accuracy']:.3f}, kappa {report['kappa']:.3f}" if task == "classification" else f"R² {report['r2']:.3f}")
    return report


def compare(table_path: str | Path, *, target: str, features: list[str], task: str = "auto", common: dict | None = None,
            categorical: list[str] | None = None, models: list[str] | None = None, max_rows: int = 20000) -> dict:
    """Train several models with default settings on the same split and rank them (quick leaderboard)."""
    common = dict(common or {})
    common["cv_folds"] = 0
    av = available()
    if task == "auto":
        data = read_table(table_path, columns=[target])
        task = _detect_task(data[target])
    todo = [k for k, mm in MODELS.items() if task in mm["tasks"] and av.get(k, True) and (models is None or k in models)]
    if not todo:
        raise ValueError("No suitable models to compare")
    rows = []
    for i, k in enumerate(todo):
        progress.update(i / len(todo), f"Comparing models: {MODELS[k]['title']} ({i + 1}/{len(todo)})")
        cmn = {**common, "max_train_rows": min(int(common.get("max_train_rows") or MODELS[k]["max_rows"]), max_rows)}
        try:
            with progress.span(i / len(todo), (i + 1) / len(todo)):
                r = train(table_path, None, target=target, features=features, model=k, task=task, common=cmn,
                          categorical=categorical, save=False)
            rows.append({"model": k, "title": MODELS[k]["title"], "seconds": r["seconds"], "train_rows": r["train_rows"],
                         **({"accuracy": r["accuracy"], "kappa": r["kappa"], "f1_macro": r["f1_macro"],
                             "balanced_accuracy": r["balanced_accuracy"]} if task == "classification"
                            else {"r2": r["r2"], "rmse": r["rmse"], "mae": r["mae"]}), "split": r["split"]})
        except progress.Cancelled:
            raise
        except Exception as e:  # one model failing shouldn't stop the comparison
            rows.append({"model": k, "title": MODELS[k]["title"], "error": str(e)[:200]})
    key = "kappa" if task == "classification" else "r2"
    rows.sort(key=lambda r: -(r.get(key) if r.get(key) is not None else -1e9))
    return {"task": task, "metric": key, "rows": rows, "max_rows": max_rows}


# ------------------------------------------------------------------ applying a model to a raster

_WORLDCOVER = {10: ("Tree cover", "#006400"), 20: ("Shrubland", "#ffbb22"), 30: ("Grassland", "#ffff4c"), 40: ("Cropland", "#f096ff"),
               50: ("Built-up", "#fa0000"), 60: ("Bare / sparse vegetation", "#b4b4b4"), 70: ("Snow and ice", "#f0f0f0"),
               80: ("Permanent water bodies", "#0064c8"), 90: ("Herbaceous wetland", "#0096a0"), 95: ("Mangroves", "#00cf75"),
               100: ("Moss and lichen", "#fae6a0")}
_PALETTE = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#1f78b4", "#b2df8a", "#fb9a99",
            "#cab2d6", "#ff7f00", "#6a3d9a", "#b15928", "#a6cee3", "#33a02c", "#fdbf6f", "#e31a1c", "#999999", "#17becf"]


def _class_codes(classes: list, class_colors: dict | None = None) -> tuple[list[int], dict[int, str], dict[int, tuple]]:
    """Raster codes, names and colours for the model's classes (numeric labels keep their values; colours
    chosen when drawing training samples are kept)."""
    hexrgb = lambda h: (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16), 255)
    try:
        nums = [int(float(c)) for c in classes]
        numeric = all(float(c).is_integer() for c in classes) and all(0 <= v <= 65534 for v in nums)
    except ValueError:
        numeric = False
    if numeric:
        codes = nums
        if set(codes) <= set(_WORLDCOVER):
            names = {v: _WORLDCOVER[v][0] for v in codes}
            colors = {v: hexrgb(_WORLDCOVER[v][1]) for v in codes}
            return codes, names, colors
        names = {v: f"Class {v}" for v in codes}
    else:
        codes = list(range(1, len(classes) + 1))
        names = dict(zip(codes, [str(c) for c in classes]))
    colors = {v: hexrgb(_PALETTE[i % len(_PALETTE)]) for i, v in enumerate(codes)}
    for v, nm in names.items():
        if class_colors and isinstance(class_colors.get(nm), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", class_colors[nm]):
            colors[v] = hexrgb(class_colors[nm])
    return codes, names, colors


def predict_raster(model_path: str | Path, raster_path: str | Path, out_path: str | Path, *, band_map: dict[str, int],
                   scale: float = 1.0, offset: float = 0.0, clip: dict | None = None, resolution: str = "auto",
                   confidence: bool = True, max_pixels: int = 25_000_000, rows_per_strip: int = 256) -> dict:
    """Apply a trained model to a raster: class map (+ confidence %) or regression values, as a GeoTIFF."""
    import joblib
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import geometry_mask
    from rasterio.windows import Window

    from .analysis import clip_region

    t0 = time.time()
    progress.update(0.0, "Loading the model")
    bundle = joblib.load(model_path)
    pipe, features, task = bundle["pipeline"], bundle["features"], bundle["task"]
    text_cats = [f for f in bundle.get("categorical") or [] if f in ((bundle["report"].get("text_columns")) or [])]
    if text_cats:
        raise ValueError(f"This model uses text column(s) {', '.join(text_cats)}, which an image can't provide. "
                         "Retrain without them to classify an image.")
    missing = [f for f in features if f not in band_map]
    if missing:
        raise ValueError(f"Choose a raster band for: {', '.join(missing)}")
    cls = task == "classification"
    with rasterio.open(raster_path) as src:
        idx = [int(band_map[f]) for f in features]
        if not all(1 <= b <= src.count for b in idx):
            raise ValueError("A selected band doesn't exist in this raster")
        win, geom = clip_region(src, clip)
        win = win or Window(0, 0, src.width, src.height)
        f = max(1, math.ceil(math.sqrt(win.width * win.height / max_pixels))) if resolution == "auto" else int(resolution)
        out_w, out_h = max(1, math.ceil(win.width / f)), max(1, math.ceil(win.height / f))
        base = src.window_transform(win)
        transform = base * base.scale(win.width / out_w, win.height / out_h)
        if cls:
            codes, names, colors = _class_codes(bundle["classes"], (bundle["report"].get("source") or {}).get("class_colors"))
            dtype = "uint8" if max(codes) <= 254 else "uint16"
            nodata = 255 if dtype == "uint8" else 65535
            lut = np.array(codes)
            has_proba = confidence and bundle["report"].get("has_proba") and hasattr(pipe, "predict_proba")
            count = 2 if has_proba else 1
        else:
            dtype, nodata, count, has_proba = "float32", np.nan, 1, False
        profile = {"driver": "GTiff", "width": out_w, "height": out_h, "count": count, "dtype": dtype, "crs": src.crs,
                   "transform": transform, "nodata": nodata, "compress": "deflate", "BIGTIFF": "IF_SAFER"}
        if out_w >= 256 and out_h >= 256:
            profile.update(tiled=True, blockxsize=256, blockysize=256)
        log.info("Applying %s to %d×%d px%s", bundle["report"]["model_title"], out_w, out_h, f" ({f}× coarser than native)" if f > 1 else "")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tally = np.zeros(len(codes), "int64") if cls else None
        with rasterio.open(out_path, "w", **profile) as dst:
            for r0 in range(0, out_h, rows_per_strip):
                progress.update(0.02 + 0.96 * r0 / out_h, f"Classifying ({r0 / out_h:.0%} of the image)" if cls else f"Predicting ({r0 / out_h:.0%})")
                rows = min(rows_per_strip, out_h - r0)
                src_win = Window(win.col_off, win.row_off + r0 * f, win.width, min(rows * f, win.height - r0 * f))
                data = src.read(idx, window=src_win, out_shape=(len(idx), rows, out_w), masked=True,
                                resampling=Resampling.average if f > 1 else Resampling.nearest)
                data = data.astype("float64").filled(np.nan) * scale + offset
                X = data.reshape(len(idx), -1).T
                ok = np.all(np.isfinite(X), axis=1)
                if geom is not None:
                    st = transform * transform.translation(0, r0)
                    ok &= geometry_mask([geom], out_shape=(rows, out_w), transform=st, invert=True).ravel()
                band1 = np.full(X.shape[0], nodata, dtype)
                band2 = np.full(X.shape[0], nodata, dtype) if has_proba else None
                if ok.any():
                    sel = np.flatnonzero(ok)
                    for s in range(0, len(sel), 200_000):
                        part = sel[s:s + 200_000]
                        if cls:
                            if has_proba:
                                proba = pipe.predict_proba(X[part])
                                k = proba.argmax(axis=1)
                                band2[part] = np.round(proba.max(axis=1) * 100).astype(dtype)
                            else:
                                k = np.asarray(pipe.predict(X[part])).astype(int)
                            band1[part] = lut[k]
                            tally += np.bincount(k, minlength=len(codes))
                        else:
                            band1[part] = pipe.predict(X[part]).astype("float32")
                dst.write(band1.reshape(rows, out_w), 1, window=Window(0, r0, out_w, rows))
                if has_proba:
                    dst.write(band2.reshape(rows, out_w), 2, window=Window(0, r0, out_w, rows))
            dst.set_band_description(1, bundle["target"] if not cls else "class")
            if cls:
                dst.write_colormap(1, colors)
                if has_proba:
                    dst.set_band_description(2, "confidence_pct")
                dst.update_tags(classes=json.dumps({str(k): v for k, v in names.items()}), model=bundle["report"]["model_title"],
                                units="class code")
            else:
                dst.update_tags(model=bundle["report"]["model_title"], units=bundle["target"])
        px_area = abs(transform.a * transform.e) if src.crs.is_projected else None
    report = {"path": str(out_path), "task": task, "width": out_w, "height": out_h, "factor": f,
              "confidence": bool(has_proba), "seconds": round(time.time() - t0, 1)}
    if cls:
        total = max(int(tally.sum()), 1)
        report["classes"] = [{"code": int(cde), "name": names[cde], "color": "#%02x%02x%02x" % colors[cde][:3],
                              "pixels": int(n), "pct": 100 * int(n) / total,
                              "area_km2": (int(n) * px_area / 1e6) if px_area else None}
                             for cde, n in sorted(zip(codes, tally), key=lambda t: -t[1])]
    log.info("Done in %.1f s", report["seconds"])
    return report
