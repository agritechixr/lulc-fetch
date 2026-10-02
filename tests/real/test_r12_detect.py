"""Detect object with pretrained models on the Cars Detection test pictures (as one mosaic), scored against their labels."""

import pytest

from tests.helpers import run
from tests.real.adapters import CRS
from tests.real.metrics import boxes_from_geojson, detection_scores, draw_boxes, stretch_rgb

TOOL = "Detect object"
pytestmark = [pytest.mark.dl, pytest.mark.weights]
COCO_TO_CARS = {"car": "Car", "bus": "Bus", "truck": "Truck", "motorcycle": "Motorcycle"}   # Ambulance has no COCO class
COLORS = {"Ambulance": "#ff00ff", "Bus": "#1f78b4", "Car": "#e31a1c", "Motorcycle": "#33a02c", "Truck": "#ff7f00"}
rows = []


def score(client, det_sets, report, label, **body):
    t = det_sets["cars"]["test"]
    cell = det_sets["cars"]["cell"]
    r = run(client, "/api/detect/run", {"input": {"path": t["rel"], "bands": [1, 2, 3]}, "stretch": "byte", "score": 0.3, "overlap": 0,
                                        "name": f"cars_{label}", **body})
    pred = boxes_from_geojson(r["geojson"], CRS, t["transform"])
    sc = detection_scores(pred, t["boxes"], COCO_TO_CARS if body.get("model") != "custom" else None)
    rows.append([label, r["count"], sc["precision"], sc["recall"], sc["f1"], sc["map50"], r["seconds"]])
    report.metric(f"{label} mAP50", sc["map50"])
    report.metric(f"{label} F1", sc["f1"])
    report.table(f"{label}: per class (IoU ≥ 0.5)", ["class", "true", "found", "correct", "precision", "recall", "AP50"],
                 [[c, v["true"], v["found"], v["correct"], v["precision"], v["recall"], v["ap50"]] for c, v in sc["per_class"].items()])
    import rasterio
    with rasterio.open(t["path"]) as s:
        n = min(3, s.height // cell) * cell
        rgb = s.read(window=((0, n), (0, n))).transpose(1, 2, 0)
    shown = [dict(b, cls=COCO_TO_CARS.get(b["cls"], b["cls"])) for b in pred if b["box"][2] < n and b["box"][3] < n]
    report.image(f"cars_{label}", draw_boxes(rgb.copy(), shown, COLORS), f"{label}: detections on the first test pictures")
    return sc, r


def test_truth_preview(det_sets, report):
    t = det_sets["cars"]["test"]
    import rasterio
    cell = det_sets["cars"]["cell"]
    with rasterio.open(t["path"]) as s:
        n = min(3, s.height // cell) * cell
        rgb = s.read(window=((0, n), (0, n))).transpose(1, 2, 0)
    report.image("cars_truth", draw_boxes(rgb.copy(), [b for b in t["boxes"] if b["box"][2] < n and b["box"][3] < n], COLORS),
                 "Cars Detection test pictures with their true labels")
    report.metric("test pictures", t["n"])
    report.metric("true objects", len(t["boxes"]))


@pytest.mark.yolo
def test_yolo26_coco(client, det_sets, report):
    cell = det_sets["cars"]["cell"]
    sc, _ = score(client, det_sets, report, "YOLO26 m (COCO)", model="yolo_detect", size="m", zoom=640 / cell)
    assert sc["recall"] > 0.3


def test_faster_rcnn_coco(client, det_sets, report):
    cell = det_sets["cars"]["cell"]
    sc, _ = score(client, det_sets, report, "Faster R-CNN v2 (COCO)", model="fasterrcnn_v2", zoom=800 / cell)
    assert sc["recall"] > 0.3


@pytest.mark.yolo
def test_sam_outlines(client, det_sets, report):
    cell = det_sets["cars"]["cell"]
    _, r = score(client, det_sets, report, "YOLO26 n + SAM 2.1 outlines", model="yolo_detect", size="n", zoom=640 / cell, sam_refine="t")
    assert r["outlines"] or r["count"] == 0


def test_summary(report):
    report.table("Pretrained detectors on Cars Detection (test pictures)", ["model", "objects found", "precision", "recall", "F1", "mAP50", "seconds"], rows)
    report.note("Ambulance has no COCO class, so pretrained models can only miss ambulances (they lower recall).")
    assert rows
