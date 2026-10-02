"""Unsupervised clustering and t-SNE on the real tables."""

import pytest

from tests.helpers import run
from tests.real.test_r06_classical_ml import DIAB_NUM

TOOL = "Classical ML: unsupervised"


@pytest.mark.parametrize("method,params", [("kmeans", {"n_clusters": 3}), ("gmm", {"n_clusters": 3}), ("hierarchical", {"n_clusters": 3}),
                                           ("hdbscan", {})])
def test_scene_pixels_find_the_classes(client, s2_table, method, params, report):
    r = run(client, "/api/unsup/cluster", {"table": s2_table["path"], "features": s2_table["features"], "method": method, "params": params,
                                           "prep": {"scaling": "standard"}, "compare": "label", "name": f"scl_{method}"})
    c = r["comparison"]
    report.metric(f"{method} ARI vs SCL", c["ari"])
    report.metric(f"{method} clusters", r["n_clusters"])
    if r["quality"].get("silhouette") is not None:
        report.metric(f"{method} silhouette", r["quality"]["silhouette"])
    report.table(f"{method}: clusters × SCL class", ["cluster"] + [str(x) for x in c["labels"]],
                 [[k] + list(row) for k, row in enumerate(c["table"])][:12])
    if method in ("kmeans", "gmm"):
        assert c["ari"] > 0.3, c


def test_diabetes_kmeans(client, tables, report):
    r = run(client, "/api/unsup/cluster", {"table": tables["diabetes_risk"], "features": DIAB_NUM, "method": "kmeans", "params": {"n_clusters": None},
                                           "options": {"auto_k": True}, "compare": "diabetes_risk", "name": "diabetes_k"})
    report.metric("chosen k", r["n_clusters"])
    report.metric("ARI vs diabetes_risk", r["comparison"]["ari"])
    assert r["n_clusters"] >= 2


def test_tsne(client, s2_table, report):
    r = run(client, "/api/unsup/tsne", {"table": s2_table["path"], "features": s2_table["features"], "params": {"perplexity": 30}, "color": "label"})
    report.metric("rows mapped", r.get("rows_used") or r.get("rows"))
    assert r["output_table"]
