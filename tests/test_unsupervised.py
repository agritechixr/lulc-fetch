"""Classical ML: unsupervised clustering (every method) and t-SNE maps."""

import pytest

from tests.helpers import ok, run

METHODS = {"kmeans": {"n_clusters": 3}, "hierarchical": {"n_clusters": 3}, "dbscan": {}, "hdbscan": {}, "spectral": {"n_clusters": 3},
           "gmm": {"n_clusters": 3}}


def test_schema(client):
    assert set(METHODS) <= set(ok(client.get("/api/unsup/schema"))["methods"])


@pytest.mark.parametrize("method", list(METHODS))
def test_cluster(client, table, home, method):
    r = run(client, "/api/unsup/cluster", {"table": table["path"], "features": table["features"], "method": method,
                                           "params": METHODS[method], "compare": "label", "name": f"c_{method}"})
    assert (home / r["output_table"]).is_file()
    assert r["n_clusters"] >= 2
    if method in ("kmeans", "gmm", "hierarchical", "spectral"):   # three clear zones: the clusters must find them
        assert r["comparison"]["ari"] > 0.9, r["comparison"]


def test_kmeans_finds_k_itself(client, table):
    r = run(client, "/api/unsup/cluster", {"table": table["path"], "features": table["features"], "method": "kmeans", "params": {"n_clusters": None},
                                           "options": {"auto_k": True}, "name": "auto"})
    assert r["n_clusters"] >= 2


def test_tsne(client, table, home):
    r = run(client, "/api/unsup/tsne", {"table": table["path"], "features": table["features"], "params": {"perplexity": 20}, "color": "label"})
    assert (home / r["output_table"]).is_file()
