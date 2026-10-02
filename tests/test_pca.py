"""PCA & dimensionality reduction: every method makes an image of components."""

import pytest
import rasterio

from tests.helpers import ok, run


def test_schema(client):
    s = ok(client.get("/api/pca/schema"))
    assert {"pca", "kernel", "nmf", "ica"} <= set(s["methods"])


@pytest.mark.parametrize("method", ["pca", "incremental", "kernel", "nmf", "ica", "svd", "fa"])
def test_method(client, data, home, method):
    r = run(client, "/api/pca/run", {"path": data["s2"], "bands": [1, 2, 3, 4, 5, 6], "method": method,
                                     "params": {"n_components": 3}, "name": "test"})
    with rasterio.open(home / r["path"]) as s:
        assert s.width == 96 and s.height == 96 and 1 <= s.count <= 6


def test_pca_explains_the_variance(client, data):
    r = run(client, "/api/pca/run", {"path": data["s2"], "bands": [1, 2, 3, 4, 5, 6], "method": "pca", "params": {"n_components": 3}, "name": "var"})
    text = str(r)
    assert "explained" in text or "variance" in text


def test_pca_in_an_area(client, data, home):
    r = run(client, "/api/pca/run", {"path": data["s2"], "bands": [1, 2, 3, 4], "method": "pca", "params": {"n_components": 2},
                                     "clip": data["aoi"], "name": "area"})
    with rasterio.open(home / r["path"]) as s:
        assert s.width < 96


def test_unknown_method(client, data):
    assert client.post("/api/pca/run", json={"path": data["s2"], "bands": [1, 2], "method": "nope"}).status_code == 400
