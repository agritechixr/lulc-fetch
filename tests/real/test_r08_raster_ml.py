"""Classical ML for raster: the 12-band scene + ESA's scene classification (SCL) → classified map, per model."""

import numpy as np
import pytest
import rasterio

import webapp.workspace as ws
from tests.helpers import run
from tests.real.conftest import CLASS_COLORS, CLASS_NAMES
from tests.real.metrics import colorize, seg_scores, side_by_side

TOOL = "Classical ML for raster"
rows = []


@pytest.mark.parametrize("model", ["rf", "lgbm", "svm", "mlc", "sam", "mindist", "knn"])
def test_classify_the_scene(client, s2_stack, scl_labels, s2_rgb, model, report):
    r = run(client, "/api/rasterml/run", {"path": s2_stack["path"], "ground_truth": {"type": "raster", "path": scl_labels["path"], "band": 1},
                                          "model": model, "per_class": 1500, "name": f"albano_{model}"})
    with rasterio.open(ws.root() / r["path"]) as s:
        pred = s.read(1)
    sc = seg_scores(pred, scl_labels["array"], list(CLASS_NAMES))
    rows.append([model, sc["accuracy"], sc["miou"], *[sc["iou"][c] for c in CLASS_NAMES], r["model"].get("seconds")])
    report.metric(f"{model} agreement with SCL", sc["accuracy"])
    report.metric(f"{model} mIoU", sc["miou"])
    report.image(f"map_{model}", side_by_side(s2_rgb, colorize(scl_labels["array"], CLASS_COLORS), colorize(pred, CLASS_COLORS), height=300),
                 f"{model}: image | ESA SCL | classified map")
    assert sc["accuracy"] > (0.7 if model in ("sam", "mindist") else 0.8)


def test_summary(report):
    report.table("Agreement with ESA's scene classification (all labelled pixels)",
                 ["model", "accuracy", "mIoU", *[f"IoU {n}" for n in CLASS_NAMES.values()], "training s"], rows)
    assert rows
