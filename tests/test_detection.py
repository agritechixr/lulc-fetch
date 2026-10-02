"""Detect object + Train detection model. Needs the PyTorch add-on; YOLO / SAM need the YOLO & SAM add-on.

Tests marked weights download small pretrained weights the first time (SSDlite 13 MB, YOLO26 nano 5–6 MB)."""

import json
from pathlib import Path

import pytest
from shapely.geometry import shape

from tests.helpers import ok, run

pytestmark = pytest.mark.dl


def detect(client, data, **kw):
    body = {"input": {"path": data["aerial"], "bands": [1, 2, 3]}, "stretch": "byte", "score": 0.2, "name": "objects", **kw}
    r = run(client, "/api/detect/run", body)
    fc = r["geojson"]
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == r["count"]
    for f in fc["features"]:
        assert shape(f["geometry"]).is_valid
        assert {"class", "score", "width_m", "height_m", "area_m2"} <= set(f["properties"])
    return r


def test_schema(client):
    s = ok(client.get("/api/detect/schema"))
    assert {"fasterrcnn_v2", "maskrcnn_v2", "ssdlite", "yolo_detect", "yolo_segment", "yolo_obb", "sam_all"} <= set(s["models"])
    assert "car" in s["classes"]["coco"] and "small vehicle" in s["classes"]["dota"]


@pytest.mark.parametrize("body,why", [({"model": "nope"}, "unknown model"), ({"input": {"path": "x.tif", "bands": [1, 2, 3]}}, "missing image"),
                                      ({"zoom": "huge"}, "bad zoom"), ({"input": {"path": "uploads/testdata/aerial.tif", "bands": [1, 2]}}, "two bands")])
def test_bad_requests(client, body, why):
    full = {"input": {"path": "uploads/testdata/aerial.tif", "bands": [1, 2, 3]}, "model": "ssdlite", **body}
    assert client.post("/api/detect/run", json=full).status_code in (400, 404, 422), why


@pytest.mark.weights
def test_torchvision_detector(client, data):
    r = detect(client, data, model="ssdlite", classes=["car", "truck"])
    assert r["tiles"] >= 1 and r["outlines"] is False


@pytest.mark.weights
def test_area_and_size_filter(client, data):
    detect(client, data, model="ssdlite", max_size_m=1.0, clip=None)


@pytest.mark.yolo
@pytest.mark.weights
@pytest.mark.parametrize("model", ["yolo_detect", "yolo_obb", "yolo_segment"])
def test_yolo_pretrained(client, data, model):
    r = detect(client, data, model=model, size="n", zoom=2)
    assert r["model"].startswith("YOLO26")


@pytest.mark.yolo
@pytest.mark.weights
@pytest.mark.slow
def test_sam_outlines_and_segment_everything(client, data):
    r = detect(client, data, model="yolo_detect", size="n", sam_refine="t", zoom=2)
    assert r["outlines"] is True or r["count"] == 0
    r = detect(client, data, model="sam_all", size="t", score=0.8)
    assert all(f["properties"]["class"] == "segment" for f in r["geojson"]["features"])


# ---------------- Train detection model
@pytest.mark.yolo
@pytest.mark.parametrize("task", ["detect", "segment", "obb"])
def test_train_detection_model(client, data, task):
    r = run(client, "/api/det/train", {"input": {"path": data["aerial"], "bands": [1, 2, 3]},
                                       "ground_truth": {"geojson": data["cars"], "field": "class"},
                                       "task": task, "family": "yolo26", "size": "n", "pretrained": False, "tile_px": 128, "overlap": 0.2,
                                       "stretch": "byte", "name": f"cars_{task}", "params": {"epochs": 2, "batch": 4, "device": "cpu"}})
    folder = Path(r["folder"])
    c = r["config"]
    assert c["kind"] == "detection" and c["task"] == task and c["classes"][0]["name"] == "car"
    assert c["epochs_run"] == 2 and c["dataset"]["objects"] > 20
    for f in ("best.pt", "last.pt", "model_config.json", "training_log.csv", "report.html", "dataset/data.yaml"):
        assert (folder / f).is_file(), f
    assert abs(c["pixel_size"] - 0.25) < 1e-6
    # listed for Detect object, with its report
    m = next(m for m in ok(client.get("/api/det/models")) if m["folder"] == str(folder))
    assert client.get("/api/det/report", params={"folder": m["folder"]}).status_code == 200
    # Detect object with it (zoom Auto from the pixel sizes)
    out = detect(client, data, model="custom", custom=m["folder"], zoom="auto", score=0.05)
    assert out["zoom"] == 1.0
    ok(client.delete("/api/det/models", params={"folder": m["folder"]}))


@pytest.mark.yolo
def test_train_detection_from_points(client, data):
    pts = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "car"}, "geometry": {"type": "Point", "coordinates": list(shape(f["geometry"]).centroid.coords[0])}}
        for f in data["cars"]["features"]]}
    r = run(client, "/api/det/train", {"input": {"path": data["aerial"], "bands": [1, 2, 3]},
                                       "ground_truth": {"geojson": pts, "field": "kind", "point_size_m": 4},
                                       "task": "detect", "size": "n", "pretrained": False, "tile_px": 128, "stretch": "byte", "name": "from_points",
                                       "params": {"epochs": 1, "batch": 4, "device": "cpu"}})
    assert r["config"]["dataset"]["objects"] > 20


@pytest.mark.yolo
def test_train_detection_needs_labels_in_the_image(client, data):
    far = json.loads(json.dumps(data["cars"]))
    for f in far["features"]:
        f["geometry"]["coordinates"] = [[[x + 10, y] for x, y in ring] for ring in f["geometry"]["coordinates"]]
    job = ok(client.post("/api/det/train", json={"input": {"path": data["aerial"], "bands": [1, 2, 3]}, "ground_truth": {"geojson": far, "field": "class"},
                                                 "size": "n", "pretrained": False, "tile_px": 128, "name": "nowhere", "params": {"epochs": 1}}))
    from tests.helpers import wait
    with pytest.raises(AssertionError, match="falls in the image"):
        wait(client, job)
