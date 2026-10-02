"""Classical ML for raster: image + ground truth (polygons or a class raster) → model + classified map."""

import numpy as np
import pytest
import rasterio

from tests.helpers import ok, run


def accuracy(home, rel, data) -> float:
    with rasterio.open(home / rel) as s, rasterio.open(data["folder"] / "labels.tif") as t:
        return float((s.read(1) == t.read(1)).mean())


def test_schema_and_inspect(client, data):
    s = ok(client.get("/api/rasterml/schema"))
    assert "rf" in s["models"]
    k = ok(client.get("/api/rasterml/inspect", params={"path": data["s2"]}))
    assert k


@pytest.mark.parametrize("model", ["rf", "svm", "mlc", "mindist", "sam", "knn", "lgbm"])
def test_classify_from_polygons(client, data, home, model):
    r = run(client, "/api/rasterml/run", {"path": data["s2"], "ground_truth": {"type": "vector", "geojson": data["samples"], "field": "code"},
                                          "model": model, "name": f"rm_{model}"})
    assert r["map"] and (home / r["model_path"]).is_file()
    assert accuracy(home, r["path"], data) > 0.9, model


def test_classify_from_a_class_raster_in_an_area(client, data, home):
    r = run(client, "/api/rasterml/run", {"path": data["s2"], "ground_truth": {"type": "raster", "path": data["labels"], "band": 1},
                                          "model": "rf", "clip": data["aoi"], "map_whole": True, "per_class": 200, "name": "rm_raster"})
    assert accuracy(home, r["path"], data) > 0.95


def test_text_classes_keep_their_names(client, data, home):
    r = run(client, "/api/rasterml/run", {"path": data["s2"], "ground_truth": {"type": "vector", "geojson": data["samples"], "field": "class"},
                                          "model": "rf", "name": "named", "class_colors": {"water": "#0000ff", "vegetation": "#00aa00", "built": "#ff0000"}})
    with rasterio.open(home / r["path"]) as s:
        cmap = s.colormap(1)
        assert len(set(np.unique(s.read(1))) - {0}) == 3 and cmap
