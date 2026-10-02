"""Unsupervised learning on tables: clustering and t-SNE maps.

Clustering: K-means, hierarchical (agglomerative), DBSCAN, HDBSCAN, spectral clustering and Gaussian mixture.
Every run writes the table again with a ``cluster`` column (1…k, 0 = noise, empty = row skipped) and reports
cluster quality (silhouette, Calinski-Harabasz, Davies-Bouldin), cluster sizes and profiles, a 2D view,
and agreement with a known label column if one is given.

Methods that can't label new rows themselves (hierarchical, DBSCAN, HDBSCAN, spectral) or that are too slow for
big tables are fitted on a sample; the remaining rows get the cluster of their nearest labelled neighbours.
The same assigner is saved as a model, so a clustering can be applied to an image with *Classify an image*
(classic unsupervised land-cover classification).

t-SNE: a 2D map in which similar rows lie close together, for exploring structure and checking how well classes
or clusters separate.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import re
import sys
import time
import warnings
from pathlib import Path

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin

from . import progress
from .ml import COMMON, P, Clipper, SkewTransformer, _cat_strings, _param, read_table

log = logging.getLogger(__name__)

_K = lambda d=5: P("n_clusters", "Number of clusters", "int", d, "How many groups to split the rows into. Not sure? Tick "
                    "'Find the best number of clusters' below and the app tries 2 … k and picks the best silhouette score.", min=2, max=100)

METHODS = {
    "kmeans": {
        "title": "K-means", "family": "Centroid", "k": True, "noise": False, "recommended": True, "speed": 3, "fit_rows": 1_000_000,
        "desc": "Splits rows into k round groups around centre points. Fast on any size; the classic unsupervised classifier for images.",
        "tip": "Best for compact, similar-sized groups. You choose k. Very large tables automatically use Mini-batch K-means.",
        "params": [_K(), P("n_init", "Restarts", "int", 10, "Runs K-means from different random starts and keeps the best. More = more stable result.", min=1, max=50),
                   P("max_iter", "Max iterations", "int", 300, "Upper limit of refinement steps per run.", min=10, max=5000, advanced=True)]},
    "hierarchical": {
        "title": "Hierarchical", "family": "Hierarchy", "k": True, "noise": False, "speed": 1, "fit_rows": 5000,
        "desc": "Merges the most similar rows step by step into a tree (dendrogram), then cuts it into k groups.",
        "tip": "Shows how groups nest inside each other. Memory grows with rows², so it is fitted on a sample (5,000 rows by default) "
               "and the other rows join their nearest neighbours' cluster.",
        "params": [_K(), P("linkage", "Linkage", "select", "ward", "How the distance between two groups is measured. Ward (recommended) makes compact, "
                            "similar-sized groups. Average / complete are less sensitive to group size; single follows chains.",
                            options=[["ward", "Ward (recommended)"], ["average", "Average"], ["complete", "Complete"], ["single", "Single"]]),
                   P("distance_threshold", "Cut height", "float", None, "Instead of a number of clusters: cut the tree at this distance "
                     "(see the dendrogram). Empty = use the number of clusters.", min=0.000001, max=1e9, advanced=True, placeholder="use number of clusters")]},
    "dbscan": {
        "title": "DBSCAN", "family": "Density", "k": False, "noise": True, "speed": 2, "fit_rows": 30000,
        "desc": "Finds dense regions of any shape and marks isolated rows as noise. You don't choose the number of clusters.",
        "tip": "Good for irregular shapes and outlier detection. Eps (neighbourhood size) is suggested automatically from the "
               "k-distance curve; set it yourself to get more (smaller eps) or fewer (larger eps) clusters.",
        "params": [P("eps", "Neighbourhood size (eps)", "float", None, "Max distance between neighbours, in scaled units. "
                     "Empty = automatic from the knee of the k-distance curve (shown in the results).", min=0.000001, max=1e6, placeholder="auto"),
                   P("min_samples", "Min samples", "int", 10, "Rows needed within eps to form a dense core. Larger = fewer, denser clusters and more noise.", min=2, max=1000)]},
    "hdbscan": {
        "title": "HDBSCAN", "family": "Density", "k": False, "noise": True, "speed": 2, "fit_rows": 30000,
        "desc": "DBSCAN that adapts to clusters of different densities. Usually the easiest density method: no eps to tune.",
        "tip": "Set the smallest group size you care about. Isolated rows become noise.",
        "params": [P("min_cluster_size", "Min cluster size", "int", None, "Smallest group worth calling a cluster. Empty = automatic (0.5 % of rows, at least 10).",
                     min=2, max=1_000_000, placeholder="auto"),
                   P("min_samples", "Min samples", "int", None, "How conservative the clustering is: larger = more rows called noise. Empty = automatic (10, or the min cluster size if smaller).",
                     min=1, max=10000, placeholder="auto", advanced=True),
                   P("cluster_selection_method", "Cluster selection", "select", "eom", "EOM (recommended) prefers a few large stable clusters; leaf gives many small, fine clusters.",
                     options=[["eom", "Excess of mass (recommended)"], ["leaf", "Leaf (fine-grained)"]], advanced=True)]},
    "spectral": {
        "title": "Spectral clustering", "family": "Graph", "k": True, "noise": False, "speed": 1, "fit_rows": 4000,
        "desc": "Builds a similarity graph between rows and splits it. Finds non-round groups (rings, bands) that K-means can't.",
        "tip": "Heavy computation, so it is fitted on a sample (4,000 rows by default) and the other rows join their nearest neighbours' cluster.",
        "params": [_K(), P("affinity", "Similarity graph", "select", "nearest_neighbors", "Nearest neighbours (recommended) connects each row to its closest rows; "
                            "RBF connects all rows weighted by distance.", options=[["nearest_neighbors", "Nearest neighbours (recommended)"], ["rbf", "RBF kernel"]]),
                   P("n_neighbors", "Neighbours", "int", 15, "Rows each row is connected to (nearest-neighbour graph).", min=2, max=200, advanced=True)]},
    "gmm": {
        "title": "Gaussian mixture", "family": "Probabilistic", "k": True, "noise": False, "speed": 2, "fit_rows": 300000, "proba": True,
        "desc": "Soft clustering: each group is a Gaussian (ellipse) and every row gets a probability of belonging to each group.",
        "tip": "Like K-means but allows elongated, tilted and differently sized groups, and gives a confidence per row (cluster_probability column).",
        "params": [_K(), P("covariance_type", "Group shape", "select", "full", "Full (recommended): any ellipse. Diagonal: axis-aligned ellipses. "
                            "Tied: same shape for all. Spherical: round, like K-means.",
                            options=[["full", "Full (recommended)"], ["diag", "Diagonal"], ["tied", "Tied"], ["spherical", "Spherical"]]),
                   P("n_init", "Restarts", "int", 3, "Fits from several starts and keeps the best.", min=1, max=20, advanced=True)]},
}


def _prep_specs() -> list[dict]:
    base = {s["name"]: dict(s) for s in COMMON if s.get("group") == "prep"}
    sc = dict(base["scaling"])
    sc.update(default="standard", options=[o for o in sc["options"] if o[0] != "auto"],
              tip="Clustering and t-SNE measure distances between rows, so features must be on a common scale; otherwise the column "
                  "with the largest numbers dominates. Standard (recommended): mean 0, std 1. Robust: least affected by outliers. "
                  "Min–max: 0 to 1. None: only if all columns already share one unit (e.g. reflectance bands).")
    return [base[n] for n in ("missing", "outliers", "outlier_pct", "skew")] + [sc]


PREP = _prep_specs()
OPTIONS = [
    P("find_k", "Find the best number of clusters", "bool", False, "Tries every k from 2 to the maximum on a sample and uses the one with the best "
      "silhouette score (shown as a chart). For K-means, hierarchical, spectral and Gaussian mixture.", ),
    P("k_max", "Try up to k =", "int", 10, "Largest number of clusters to try.", min=3, max=30),
    P("max_fit_rows", "Rows used to fit", "int", None, "Rows used to build the clusters. Empty = the method's sensible default "
      "(K-means 1,000,000 · Gaussian mixture 300,000 · DBSCAN / HDBSCAN 30,000 · hierarchical 5,000 · spectral 4,000). "
      "All other rows are then assigned to the nearest cluster.", min=100, max=10_000_000, placeholder="method default", advanced=True),
    P("save_model", "Save as a model (to cluster an image)", "bool", False, "Saves the clustering like a trained model, so "
      "Classify an image can apply it to a raster: unsupervised land-cover classification.", ),
    P("random_state", "Random seed", "int", 0, "Makes sampling and clustering reproducible.", min=0, max=2**31 - 1, advanced=True),
]
TSNE_PARAMS = [
    P("perplexity", "Perplexity", "float", 30.0, "Roughly how many neighbours each point pays attention to. 5–50; larger shows more global "
      "structure, smaller more local detail.", min=2, max=200),
    P("max_rows", "Rows (sample)", "int", 5000, "t-SNE is slow on big tables, so a random sample is mapped. 5,000 takes about 10–40 s; 20,000 a few minutes.",
      min=100, max=50000),
    P("max_iter", "Iterations", "int", 1000, "Optimisation steps. 1,000 is usually enough; more if the map still looks blurry.", min=250, max=5000),
    P("learning_rate", "Learning rate", "float", None, "Step size. Empty = automatic (recommended).", min=1, max=10000, placeholder="auto", advanced=True),
    P("early_exaggeration", "Early exaggeration", "float", 12.0, "How strongly clusters are pulled apart at the start. Larger = more space between clusters.",
      min=1, max=100, advanced=True),
    P("init", "Initialisation", "select", "pca", "PCA (recommended) keeps the global layout more faithful and runs reproducibly.",
      options=[["pca", "PCA (recommended)"], ["random", "Random"]], advanced=True),
    P("metric", "Distance", "select", "euclidean", "How similarity between rows is measured.",
      options=[["euclidean", "Euclidean (recommended)"], ["manhattan", "Manhattan"], ["cosine", "Cosine (angle; good for spectra)"]], advanced=True),
    P("random_state", "Random seed", "int", 0, "Makes the sample and the map reproducible.", min=0, max=2**31 - 1, advanced=True),
]


def schema() -> dict:
    return {"methods": METHODS, "prep": PREP, "options": OPTIONS, "tsne": TSNE_PARAMS}


class ClusterAssigner(ClassifierMixin, BaseEstimator):
    """Gives new rows (or pixels) a cluster index 0…K-1, matching the saved class list.

    Wraps the clustering model's own predict (K-means, Gaussian mixture) with a label mapping, or a nearest-neighbour
    classifier trained on the clustered sample (methods that can't predict new rows).
    """

    def __init__(self, model=None, mapping=None, n_classes: int = 0):
        self.model, self.mapping, self.n_classes = model, mapping, n_classes

    def fit(self, X, y=None):
        """Already fitted by cluster(); refitting means fitting a nearest-neighbour classifier on given labels."""
        self.model.fit(X, y)
        return self

    def __sklearn_is_fitted__(self):
        return True

    def predict(self, X):
        p = np.asarray(self.model.predict(X)).astype(int)
        return np.asarray(self.mapping)[p] if self.mapping is not None else p

    @property
    def predict_proba(self):
        if not hasattr(self.model, "predict_proba"):
            raise AttributeError("predict_proba")
        return self._proba

    def _proba(self, X):
        p = self.model.predict_proba(X)
        out = np.zeros((len(p), self.n_classes))
        if self.mapping is not None:
            out[:, np.asarray(self.mapping)[: p.shape[1]]] = p
        else:
            out[:, np.asarray(self.model.classes_).astype(int)] = p
        return out


# ------------------------------------------------------------------ shared: read + preprocess
def _prepare(table_path, features, categorical, pp, extra=()):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import FunctionTransformer, MinMaxScaler, OneHotEncoder, RobustScaler, StandardScaler

    if not features:
        raise ValueError("Select at least one column to use")
    cols = list(dict.fromkeys(list(features) + [c for c in extra if c]))
    data = read_table(table_path, columns=cols)
    n = len(data[cols[0]])
    cat_set = set(categorical or []) | {f for f in features if data[f].dtype == object}
    cat_cols = [f for f in features if f in cat_set]
    num_cols = [f for f in features if f not in cat_set]
    if cat_cols:
        X = np.empty((n, len(features)), dtype=object)
        for i, f in enumerate(features):
            X[:, i] = data[f]
    else:
        X = np.column_stack([data[f] for f in features]).astype("float64")
    num_idx = [features.index(f) for f in num_cols]
    cat_idx = [features.index(f) for f in cat_cols]
    impute = pp["missing"] == "impute"
    ok = np.ones(n, bool)
    if num_cols:
        fin = np.isfinite(X[:, num_idx].astype("float64"))
        ok &= fin.all(axis=1) if not impute else (fin.any(axis=1) | bool(cat_cols))
    if cat_cols and not impute:
        ok &= np.array([all(v not in (None, "") and not (isinstance(v, float) and not np.isfinite(v)) for v in row) for row in X[:, cat_idx]])
    scalers = {"standard": StandardScaler, "minmax": MinMaxScaler, "robust": RobustScaler}
    num_steps = ([("impute", SimpleImputer(strategy="median"))] if impute else []) + \
                ([("clip", Clipper(pp["outlier_pct"]))] if pp["outliers"] == "clip" else []) + \
                ([("skew", SkewTransformer(pp["skew"]))] if pp["skew"] != "none" else []) + \
                ([("scale", scalers[pp["scaling"]]())] if pp["scaling"] != "none" else [])
    transformers = []
    if num_cols:
        transformers.append(("num", Pipeline(num_steps) if num_steps else "passthrough", num_idx))
    if cat_cols:
        transformers.append(("cat", Pipeline([("str", FunctionTransformer(_cat_strings)),
                                              ("onehot", OneHotEncoder(handle_unknown="ignore", max_categories=50, sparse_output=False))]), cat_idx))
    pre = ColumnTransformer(transformers)
    return data, X, ok, pre, num_cols, cat_cols, impute


def _steps(pp, cat_cols, impute) -> list[str]:
    s = []
    if impute:
        s.append("fill missing values")
    if pp["outliers"] == "clip":
        s.append(f"clip outliers to the {pp['outlier_pct']:g}–{100 - pp['outlier_pct']:g} percentiles")
    if pp["skew"] != "none":
        s.append("Yeo-Johnson transform (" + ("skewed columns" if pp["skew"] == "auto" else "all columns") + ")")
    if pp["scaling"] != "none":
        s.append({"standard": "standard scaling", "minmax": "min–max scaling", "robust": "robust scaling"}[pp["scaling"]])
    if cat_cols:
        s.append(f"one-hot encode {len(cat_cols)} categorical column(s)")
    return s


def _transform_chunks(pre, X, idx, chunk=100_000):
    for a in range(0, len(idx), chunk):
        sel = idx[a:a + chunk]
        yield sel, np.asarray(pre.transform(X[sel]), dtype="float64")


# ------------------------------------------------------------------ clustering
def _build(method: str, p: dict, seed: int, n: int):
    from sklearn import cluster, mixture
    if method == "kmeans":
        if n > 100_000:
            return cluster.MiniBatchKMeans(n_clusters=p["n_clusters"], n_init=min(p["n_init"], 5), max_iter=p["max_iter"],
                                           batch_size=4096, random_state=seed)
        return cluster.KMeans(n_clusters=p["n_clusters"], n_init=p["n_init"], max_iter=p["max_iter"], random_state=seed)
    if method == "hierarchical":
        thr = p.get("distance_threshold")
        return cluster.AgglomerativeClustering(n_clusters=None if thr else p["n_clusters"], linkage=p["linkage"], distance_threshold=thr)
    if method == "dbscan":
        return cluster.DBSCAN(eps=p["eps"], min_samples=p["min_samples"], n_jobs=-1)
    if method == "hdbscan":
        return cluster.HDBSCAN(min_cluster_size=p["min_cluster_size"], min_samples=p["min_samples"],
                               cluster_selection_method=p["cluster_selection_method"])
    if method == "spectral":
        return cluster.SpectralClustering(n_clusters=p["n_clusters"], affinity=p["affinity"], n_neighbors=p["n_neighbors"],
                                          assign_labels="cluster_qr", random_state=seed, n_jobs=-1)
    if method == "gmm":
        return mixture.GaussianMixture(n_components=p["n_clusters"], covariance_type=p["covariance_type"], n_init=p["n_init"],
                                       reg_covar=1e-5, random_state=seed)
    raise ValueError(f"Unknown method {method}")


def _k_search(method, Xf, p, k_max, seed) -> dict:
    from sklearn.metrics import silhouette_score
    rng = np.random.default_rng(seed)
    cap = 2000 if method in ("spectral", "hierarchical") else 5000
    sub = Xf[rng.choice(len(Xf), cap, replace=False)] if len(Xf) > cap else Xf
    ks = list(range(2, min(k_max, len(sub) - 1) + 1))
    out = {"k": [], "silhouette": [], "extra": [], "extra_name": {"kmeans": "inertia", "gmm": "BIC"}.get(method)}
    for i, k in enumerate(ks):
        progress.update(0.08 + 0.32 * i / len(ks), f"Finding the best number of clusters: k = {k}")
        est = _build(method, {**p, "n_clusters": k, "distance_threshold": None}, seed, len(sub))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lab = est.fit_predict(sub)
        s = float(silhouette_score(sub, lab)) if len(set(lab)) > 1 else float("nan")
        out["k"].append(k)
        out["silhouette"].append(s)
        out["extra"].append(float(est.inertia_) if method == "kmeans" else float(est.bic(sub)) if method == "gmm" else None)
    valid = [(s, k) for s, k in zip(out["silhouette"], out["k"]) if np.isfinite(s)]
    out["best_k"] = max(valid)[1] if valid else p["n_clusters"]
    out["rows"] = len(sub)
    return out


def _k_distance(Xf, min_samples, seed) -> dict:
    """Sorted distance to each row's k-th neighbour; the knee of the curve is a good DBSCAN eps."""
    from sklearn.neighbors import NearestNeighbors
    d = NearestNeighbors(n_neighbors=min(min_samples, len(Xf) - 1)).fit(Xf).kneighbors(Xf)[0][:, -1]
    d = np.sort(d)
    x = np.linspace(0, 1, len(d))
    y = (d - d[0]) / ((d[-1] - d[0]) or 1)
    knee = int(np.argmax(x - y))
    eps = float(d[knee]) if d[knee] > 0 else float(np.median(d[d > 0])) if (d > 0).any() else 0.5
    keep = np.unique(np.linspace(0, len(d) - 1, min(300, len(d))).round().astype(int))
    return {"distances": d[keep].round(6).tolist(), "positions": keep.tolist(), "n": len(d), "knee": knee, "suggested_eps": eps}


def _dendrogram(Xf, linkage, k, seed) -> dict:
    from scipy.cluster.hierarchy import dendrogram
    from scipy.cluster.hierarchy import linkage as link
    rng = np.random.default_rng(seed)
    sub = Xf[rng.choice(len(Xf), 1500, replace=False)] if len(Xf) > 1500 else Xf
    Z = link(sub, method=linkage)
    dn = dendrogram(Z, truncate_mode="lastp", p=min(30, len(sub) - 1), no_plot=True, count_sort=True)
    h = Z[:, 2]
    cut = float((h[-(k - 1)] + h[-k]) / 2) if k and 1 < k < len(h) else None
    return {"icoord": dn["icoord"], "dcoord": dn["dcoord"], "leaves": dn["ivl"], "cut": cut, "rows": len(sub), "max": float(h.max())}


def _relabel(labels):
    """Clusters renumbered by size (largest first); noise (−1) becomes the last class."""
    uniq = [u for u in np.unique(labels) if u != -1]
    sizes = {u: int((labels == u).sum()) for u in uniq}
    order = sorted(uniq, key=lambda u: -sizes[u])
    k = len(order)
    has_noise = bool((labels == -1).any())
    mapping = {u: i for i, u in enumerate(order)}
    cls = np.array([mapping.get(u, k) for u in labels])
    classes = [f"Cluster {i + 1}" for i in range(k)] + (["Noise"] if has_noise else [])
    return cls, classes, mapping, k, has_noise


def _write_with_column(table_path: Path, table_dir: Path, stem: str, new_cols: dict, meta_extra: dict, rows=None) -> Path:
    """Write the original table (optionally a row subset) plus new columns to tables/ (CSV, or Parquet if the source is)."""
    import pyarrow as pa
    import pyarrow.csv as pcsv

    from .tableview import load
    t = load(table_path)
    if rows is not None:
        t = t.take(pa.array(rows, type=pa.int64()))
    for name, arr in new_cols.items():
        nm = name
        while nm in t.column_names:
            nm += "_2"
        t = t.append_column(nm, arr)
    table_dir.mkdir(parents=True, exist_ok=True)
    ext = ".parquet" if str(table_path).endswith(".parquet") else ".csv"
    out, i = table_dir / f"{stem}{ext}", 2
    while out.exists():
        out = table_dir / f"{stem}_{i}{ext}"
        i += 1
    if ext == ".parquet":
        import pyarrow.parquet as pq
        pq.write_table(t, out)
    else:
        pcsv.write_csv(t, out)
    meta = {}
    side = Path(str(table_path) + ".json")
    if side.exists():
        try:
            meta = json.loads(side.read_text())
        except ValueError:
            pass
    meta.update(meta_extra, rows=t.num_rows, columns=t.column_names, source_table=str(table_path))
    Path(str(out) + ".json").write_text(json.dumps(meta, indent=1, default=str))
    return out


def cluster(table_path, *, features: list[str], categorical: list[str] | None = None, method: str = "kmeans",
            params: dict | None = None, prep: dict | None = None, options: dict | None = None, compare: str | None = None,
            name: str = "clusters", table_dir: str | Path = "tables", model_dir: str | Path | None = None) -> dict:
    import pyarrow as pa
    from sklearn import metrics as mt
    from sklearn.decomposition import PCA
    from sklearn.neighbors import KNeighborsClassifier

    t0 = time.time()
    if method not in METHODS:
        raise ValueError(f"Unknown method {method}")
    m = METHODS[method]
    p = {s["name"]: _param(params or {}, s) for s in m["params"]}
    pp = {s["name"]: _param(prep or {}, s) for s in PREP}
    o = {s["name"]: _param(options or {}, s) for s in OPTIONS}
    seed = o["random_state"]
    table_path = Path(table_path)
    if compare and compare in features:
        compare = None

    progress.update(0.01, "Loading the table")
    data, X, ok, pre, num_cols, cat_cols, impute = _prepare(table_path, features, categorical, pp, extra=[compare])
    n = len(ok)
    idx = np.flatnonzero(ok)
    if len(idx) < 10:
        raise ValueError("Too few usable rows (need at least 10). Check missing values or choose other columns.")
    rng = np.random.default_rng(seed)
    cap = o["max_fit_rows"] or m["fit_rows"]
    fit_idx = np.sort(rng.choice(idx, cap, replace=False)) if len(idx) > cap else idx
    progress.update(0.04, f"Preprocessing {len(fit_idx):,} rows")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        Xf = np.asarray(pre.fit_transform(X[fit_idx]), dtype="float64")

    ksearch = None
    if o["find_k"] and m["k"]:
        ksearch = _k_search(method, Xf, p, o["k_max"], seed)
        p["n_clusters"] = ksearch["best_k"]
        p["distance_threshold"] = None
        log.info("Best number of clusters: %d (silhouette)", ksearch["best_k"])
    kdist = None
    if method == "dbscan":
        progress.update(0.4, "Measuring neighbour distances (k-distance curve)")
        kdist = _k_distance(Xf, p["min_samples"], seed)
        if p["eps"] is None:
            p["eps"] = round(kdist["suggested_eps"], 6)
    if method == "hdbscan":
        if p["min_cluster_size"] is None:
            p["min_cluster_size"] = max(10, int(len(Xf) * 0.005))
        if p["min_samples"] is None:
            p["min_samples"] = min(10, p["min_cluster_size"])
    if m["k"] and p.get("n_clusters", 2) >= len(Xf):
        raise ValueError("More clusters than rows")

    progress.update(0.45, f"Clustering {len(Xf):,} rows with {m['title']}")
    est = _build(method, p, seed, len(Xf))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = np.asarray(est.fit_predict(Xf)).astype(int)
    cls_fit, classes, mapping, k, has_noise = _relabel(raw)
    notes = []
    if k == 0:
        notes.append("No clusters found: every row was marked as noise. The rows are spread fairly evenly, without dense, separated groups. "
                     + ("Try a larger eps or smaller min samples, " if method == "dbscan" else "Try a smaller min cluster size, ")
                     + "or use K-means / Gaussian mixture, which always split the data into k groups.")
    elif k == 1 and not m["k"]:
        notes.append("Only one cluster found: the data forms one dense cloud" + (" plus noise" if has_noise else "") + ". "
                     + ("A smaller eps splits it into more clusters. " if method == "dbscan" else "A smaller min cluster size can split it. ")
                     + "K-means or Gaussian mixture will split it into the number of groups you ask for.")
    log.info("%s found %d cluster(s)%s", m["title"], k, f" and {int((raw == -1).sum()):,} noise rows" if has_noise else "")

    # assigner: the model's own predict where it exists and covers every cluster, otherwise nearest neighbours
    if method in ("kmeans", "gmm") and not has_noise and len(mapping) == getattr(est, "n_clusters", getattr(est, "n_components", -1)):
        mp = np.zeros(max(mapping) + 1, dtype=int)
        for u, i in mapping.items():
            mp[u] = i
        assigner = ClusterAssigner(est, mp.tolist(), len(classes))
        assign_kind = "the model's own rule (nearest centre / most likely component)"
    else:
        kn_idx = rng.choice(len(Xf), 50000, replace=False) if len(Xf) > 50000 else np.arange(len(Xf))
        knn = KNeighborsClassifier(n_neighbors=min(10, len(kn_idx)), weights="distance").fit(Xf[kn_idx], cls_fit[kn_idx])
        assigner = ClusterAssigner(knn, None, len(classes))
        assign_kind = "the cluster of the 10 nearest clustered rows"

    labels = np.full(n, -1, dtype=int)       # −1 = row skipped (missing values)
    labels[fit_idx] = cls_fit
    rest = np.setdiff1d(idx, fit_idx, assume_unique=True)
    probs = np.full(n, np.nan) if m.get("proba") else None
    if len(rest):
        for j, (sel, Xt) in enumerate(_transform_chunks(pre, X, rest)):
            progress.update(0.6 + 0.15 * j * 100_000 / len(rest), f"Assigning the other {len(rest):,} rows to clusters")
            labels[sel] = assigner.predict(Xt)
    if probs is not None:
        for sel, Xt in _transform_chunks(pre, X, idx):
            probs[sel] = assigner.predict_proba(Xt).max(axis=1)

    # ---- quality (on a sample of the fitted rows; noise left out)
    progress.update(0.78, "Measuring cluster quality")
    ms = rng.choice(len(Xf), 10000, replace=False) if len(Xf) > 10000 else np.arange(len(Xf))
    lab, Xs = cls_fit[ms], Xf[ms]
    keep = lab != (k if has_noise else -99)
    quality = {}
    if len(np.unique(lab[keep])) > 1:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            quality = {"silhouette": float(mt.silhouette_score(Xs[keep], lab[keep])),
                       "calinski_harabasz": float(mt.calinski_harabasz_score(Xs[keep], lab[keep])),
                       "davies_bouldin": float(mt.davies_bouldin_score(Xs[keep], lab[keep])), "rows": int(keep.sum())}
    if has_noise and (raw == -1).mean() > 0.5 and k > 0:
        notes.append(f"{(raw == -1).mean():.0%} of the rows are noise: the clusters are only small dense cores. Loosen the settings to include more rows.")
    if quality.get("silhouette") is not None and quality["silhouette"] < 0.25:
        notes.append(f"Weak cluster structure (silhouette {quality['silhouette']:.2f}; above 0.5 is clear, 0.25–0.5 reasonable): the groups overlap, "
                     "so they are a useful split of the data rather than natural, well-separated classes.")
    if method == "kmeans":
        quality["inertia"] = float(est.inertia_)
    if method == "gmm":
        quality["bic"] = float(est.bic(Xf[ms]))

    # ---- sizes and profiles (original units)
    valid_lab = labels[idx]
    sizes = [int((valid_lab == c).sum()) for c in range(len(classes))]
    profiles = {"numeric": [], "categorical": []}
    for f in num_cols:
        v = np.asarray(data[f], dtype="float64")[idx]
        mu, sd = np.nanmean(v), np.nanstd(v) or 1.0
        means = [float(np.nanmean(v[valid_lab == c])) if sizes[c] else None for c in range(len(classes))]
        profiles["numeric"].append({"feature": f, "overall": float(mu), "means": means,
                                    "z": [None if x is None else float((x - mu) / sd) for x in means]})
    for f in cat_cols:
        v = np.asarray(data[f], dtype=object)[idx]
        tops = []
        for c in range(len(classes)):
            vals, cnt = np.unique(np.array([str(x) for x in v[valid_lab == c]]), return_counts=True) if sizes[c] else ([], [])
            tops.append([str(vals[int(np.argmax(cnt))]), float(cnt.max() / cnt.sum())] if len(cnt) else None)
        profiles["categorical"].append({"feature": f, "top": tops})

    # ---- 2D view: PCA of the preprocessed rows
    vs = rng.choice(len(Xf), 4000, replace=False) if len(Xf) > 4000 else np.arange(len(Xf))
    if Xf.shape[1] >= 2:
        pca = PCA(n_components=2, random_state=seed).fit(Xf)
        xy = pca.transform(Xf[vs])
        ev = pca.explained_variance_ratio_.tolist()
    else:
        xy = np.column_stack([Xf[vs, 0], np.zeros(len(vs))])
        ev = [1.0, 0.0]
    view = {"x": xy[:, 0].round(4).tolist(), "y": xy[:, 1].round(4).tolist(), "cluster": cls_fit[vs].tolist(), "explained": ev}

    # ---- agreement with a known label
    comparison = None
    if compare:
        yv = np.asarray(data[compare], dtype=object)[idx]
        ys = np.array([("" if x is None else (str(int(x)) if isinstance(x, float) and np.isfinite(x) and float(x).is_integer() else str(x))) for x in yv])
        good = ys != ""
        if good.sum() > 1:
            yl, cl = ys[good], valid_lab[good]
            h, c_, v = mt.homogeneity_completeness_v_measure(yl, cl)
            vals, cnt = np.unique(yl, return_counts=True)
            top = [str(x) for x in vals[np.argsort(-cnt)][:20]]
            yt = np.where(np.isin(yl, top), yl, "(other)")
            cols = top + (["(other)"] if (yt == "(other)").any() else [])
            ct = [[int(((cl == ci) & (yt == lv)).sum()) for lv in cols] for ci in range(len(classes))]
            comparison = {"column": compare, "ari": float(mt.adjusted_rand_score(yl, cl)), "nmi": float(mt.normalized_mutual_info_score(yl, cl)),
                          "homogeneity": float(h), "completeness": float(c_), "v_measure": float(v), "labels": [str(x) for x in cols],
                          "table": ct, "majority": [cols[int(np.argmax(r))] if sum(r) else None for r in ct]}

    dendro = None
    if method == "hierarchical":
        progress.update(0.84, "Drawing the dendrogram")
        try:
            dendro = _dendrogram(Xf, p["linkage"], k, seed)
        except Exception as e:  # the tree is optional
            log.info("No dendrogram: %s", e)

    # ---- outputs: table with the cluster column (+ model)
    progress.update(0.9, "Saving the table with the cluster column")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "clusters"
    num = np.where(labels < 0, -1, np.where(labels == k, 0, labels + 1))   # 1…k, 0 = noise, empty = skipped
    new_cols = {"cluster": pa.array([None if v < 0 else int(v) for v in num], type=pa.int32())}
    if probs is not None:
        new_cols["cluster_probability"] = pa.array([None if not np.isfinite(v) else round(float(v), 4) for v in probs], type=pa.float64())
    meta_extra = {"clustering": {"method": method, "params": p, "features": features, "classes": classes},
                  "cluster_column": "cluster"}
    side = Path(str(table_path) + ".json")
    if side.exists():
        try:
            meta_extra["label_columns"] = list(dict.fromkeys((json.loads(side.read_text()).get("label_columns") or []) + ["cluster"]))
        except ValueError:
            pass
    else:
        meta_extra["label_columns"] = ["cluster"]
    out_table = _write_with_column(table_path, Path(table_dir), f"{stem}_{method}", new_cols, meta_extra)

    report = {"kind": "clustering", "name": name, "method": method, "model_title": f"{m['title']} ({k} clusters)", "method_title": m["title"],
              "table": str(table_path), "features": features, "categorical": cat_cols, "params": p, "prep_steps": _steps(pp, cat_cols, impute),
              "rows_total": n, "rows_used": int(len(idx)), "rows_skipped": int(n - len(idx)), "rows_fitted": int(len(fit_idx)),
              "assigned_by": assign_kind if len(rest) else None, "n_clusters": k, "noise": sizes[-1] if has_noise else 0,
              "classes": classes, "sizes": sizes, "quality": quality, "profiles": profiles, "view": view, "k_search": ksearch,
              "k_distance": kdist, "dendrogram": dendro, "comparison": comparison, "output_table": str(out_table),
              "has_probability": probs is not None, "warnings": notes}

    if o["save_model"] and model_dir and k == 0:
        notes.append("No model was saved because no clusters were found.")
    if o["save_model"] and model_dir and k > 0:
        import joblib
        from sklearn.pipeline import Pipeline
        meta = json.loads(side.read_text()) if side.exists() else {}
        report.update(task="classification", has_proba=hasattr(assigner, "predict_proba"),
                      source={kk: meta.get(kk) for kk in ("source", "band_columns", "band_indices", "scale", "offset", "crs", "pixel_size")})
        report["source"]["classes"] = classes
        bundle = {"pipeline": Pipeline([("pre", pre), ("model", assigner)]), "classes": classes, "task": "classification",
                  "features": features, "target": "cluster", "categorical": cat_cols, "model": method, "kind": "clustering",
                  "imputes": bool(impute and num_cols), "report": report}
        Path(model_dir).mkdir(parents=True, exist_ok=True)
        mpath, i = Path(model_dir) / f"{stem}_{method}.joblib", 2
        while mpath.exists():
            mpath = Path(model_dir) / f"{stem}_{method}_{i}.joblib"
            i += 1
        report["path"] = str(mpath)
        joblib.dump(bundle, mpath, compress=3)
        Path(str(mpath) + ".json").write_text(json.dumps(report, indent=1, default=str))
    report["seconds"] = round(time.time() - t0, 1)
    log.info("Clustering done in %.1f s · %d clusters%s", report["seconds"], k,
             f" · silhouette {quality['silhouette']:.3f}" if "silhouette" in quality else "")
    return report


# ------------------------------------------------------------------ t-SNE
class _TsneProgress(io.TextIOBase):
    """Reads t-SNE's verbose output to report real progress (and lets Cancel stop it between iterations)."""

    def __init__(self, total: int, orig):
        self.total, self.orig = total, orig

    def write(self, s):
        mt = re.search(r"Iteration (\d+)", s)
        if mt:
            it = int(mt.group(1))
            progress.update(0.15 + 0.75 * min(1.0, it / self.total), f"t-SNE iteration {it:,} of {self.total:,}")
        elif s.strip() and not s.startswith("[t-SNE]"):
            self.orig.write(s)
        return len(s)


def tsne(table_path, *, features: list[str], categorical: list[str] | None = None, params: dict | None = None,
         prep: dict | None = None, color: str | None = None, name: str = "tsne", table_dir: str | Path = "tables") -> dict:
    import pyarrow as pa
    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE, trustworthiness

    t0 = time.time()
    p = {s["name"]: _param(params or {}, s) for s in TSNE_PARAMS}
    pp = {s["name"]: _param(prep or {}, s) for s in PREP}
    seed = p["random_state"]
    table_path = Path(table_path)
    progress.update(0.01, "Loading the table")
    data, X, ok, pre, num_cols, cat_cols, impute = _prepare(table_path, features, categorical, pp, extra=[color])
    idx = np.flatnonzero(ok)
    if len(idx) < 20:
        raise ValueError("Too few usable rows for t-SNE (need at least 20)")
    rng = np.random.default_rng(seed)
    sidx = np.sort(rng.choice(idx, p["max_rows"], replace=False)) if len(idx) > p["max_rows"] else idx
    progress.update(0.05, f"Preprocessing {len(sidx):,} rows")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        Xs = np.asarray(pre.fit_transform(X[sidx]), dtype="float64")
    reduced = None
    if Xs.shape[1] > 50:
        progress.update(0.1, "Reducing to 50 dimensions with PCA first")
        Xs = PCA(n_components=50, random_state=seed).fit_transform(Xs)
        reduced = 50
    perp = min(p["perplexity"], (len(sidx) - 1) / 3)
    est = TSNE(n_components=2, perplexity=perp, max_iter=p["max_iter"], learning_rate=p["learning_rate"] or "auto",
               early_exaggeration=p["early_exaggeration"], init=p["init"], metric=p["metric"], random_state=seed, verbose=2)
    progress.update(0.15, f"Running t-SNE on {len(sidx):,} rows")
    orig = sys.stdout
    with contextlib.redirect_stdout(_TsneProgress(p["max_iter"], orig)), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        emb = est.fit_transform(Xs)
    progress.update(0.92, "Measuring how well neighbours are preserved")
    ti = rng.choice(len(Xs), 2000, replace=False) if len(Xs) > 2000 else np.arange(len(Xs))
    trust = float(trustworthiness(Xs[ti], emb[ti], n_neighbors=min(10, len(ti) // 2 - 1)))

    # colour options: the chosen column, the categorical features, and the numeric features
    colors = {}
    for c in list(dict.fromkeys([color] + cat_cols + num_cols)):
        if not c:
            continue
        v = data[c][sidx]
        if v.dtype == object:
            colors[c] = {"kind": "categorical", "values": ["" if x is None else str(x) for x in v]}
        else:
            vals = np.asarray(v, dtype="float64")
            fin = vals[np.isfinite(vals)]
            integer_like = len(fin) and np.all(fin == np.round(fin)) and len(np.unique(fin)) <= 30
            colors[c] = {"kind": "categorical" if integer_like else "numeric",
                         "values": [None if not np.isfinite(x) else (str(int(x)) if integer_like else round(float(x), 5)) for x in vals]}
    progress.update(0.96, "Saving the table with the map coordinates")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "tsne"
    out = _write_with_column(table_path, Path(table_dir), f"{stem}_tsne",
                             {"tsne_1": pa.array(emb[:, 0].round(5)), "tsne_2": pa.array(emb[:, 1].round(5))},
                             {"tsne": {"features": features, "params": {**p, "perplexity_used": perp}}}, rows=sidx)
    report = {"kind": "tsne", "name": name, "table": str(table_path), "features": features, "categorical": cat_cols,
              "rows_total": len(ok), "rows_used": int(len(idx)), "rows_mapped": int(len(sidx)), "params": {**p, "perplexity": perp},
              "prep_steps": _steps(pp, cat_cols, impute) + ([f"PCA to {reduced} dimensions"] if reduced else []),
              "kl_divergence": float(est.kl_divergence_), "trustworthiness": trust, "iterations": int(est.n_iter_),
              "x": emb[:, 0].round(4).tolist(), "y": emb[:, 1].round(4).tolist(), "row_ids": sidx.tolist(),
              "colors": colors, "color": color if color in colors else (next(iter(colors)) if colors else None),
              "output_table": str(out), "seconds": round(time.time() - t0, 1)}
    log.info("t-SNE of %d rows in %.1f s (trustworthiness %.3f)", len(sidx), report["seconds"], trust)
    return report
