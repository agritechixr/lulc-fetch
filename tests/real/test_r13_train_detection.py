"""Train detection model on the data folder's YOLO sets (Cars Detection, wind farms), then Detect object with it on the
held-out test pictures, scored against their labels."""

import pytest
import rasterio

from tests.helpers import ok, run
from tests.real.adapters import CRS
from tests.real.metrics import boxes_from_geojson, detection_scores, draw_boxes

TOOL = "Train detection model"
pytestmark = [pytest.mark.dl, pytest.mark.yolo, pytest.mark.weights]
PALETTE = ["#e31a1c", "#1f78b4", "#33a02c", "#ff7f00", "#6a3d9a", "#b15928"]
rows = []


@pytest.mark.parametrize("key", ["cars", "wind"])
def test_train_and_detect(client, det_sets, size, key, report):
    if key not in det_sets:
        pytest.skip(f"{key} set not in the data folder")
    d = det_sets[key]
    cell, tr, te = d["cell"], d["train"], d["test"]
    r = run(client, "/api/det/train", {"input": {"path": tr["rel"], "bands": [1, 2, 3]}, "ground_truth": {"geojson": tr["geojson"], "field": "class"},
                                       "task": "detect", "family": "yolo26", "size": size["yolo_size"], "pretrained": True, "tile_px": cell,
                                       "overlap": 0, "stretch": "byte", "name": f"{key}_detector",
                                       "params": {"epochs": size["det_epochs"], "batch": 16, "patience": 20}})
    c = r["config"]
    report.metric(f"{key} val mAP50 (training)", c["val"]["map50"])
    report.metric(f"{key} val mAP50-95", c["val"]["map"])
    report.file(f"{r['folder']}/report.html", f"{key}_detector_report.html", f"{key}: Train detection model report")
    # Detect object with the new model on the test mosaic (zoom Auto = same pixel size)
    det = run(client, "/api/detect/run", {"input": {"path": te["rel"], "bands": [1, 2, 3]}, "model": "custom", "custom": r["folder"],
                                          "zoom": "auto", "overlap": 0, "stretch": "byte", "score": 0.01, "name": f"{key}_test"})
    # AP50 uses every detection down to 1 % confidence (like ultralytics' own validation); precision / recall what the
    # map shows at the tool's default threshold
    pred = boxes_from_geojson(det["geojson"], CRS, te["transform"])
    sc = detection_scores(pred, te["boxes"])
    at = detection_scores([b for b in pred if b["score"] >= 0.25], te["boxes"])
    report.metric(f"{key} test mAP50", sc["map50"])
    report.metric(f"{key} test precision @0.25", at["precision"])
    report.metric(f"{key} test recall @0.25", at["recall"])
    if not at["precision"] and not at["recall"]:
        report.note(f"{key}: no detection reached 0.25 confidence: the model needs more pictures / epochs (--real-size medium)")
    report.table(f"{key}: test pictures per class (IoU ≥ 0.5)", ["class", "true", "found", "correct", "precision", "recall", "AP50"],
                 [[k, v["true"], v["found"], v["correct"], v["precision"], v["recall"], v["ap50"]] for k, v in sc["per_class"].items()])
    colors = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(d["names"])}
    with rasterio.open(te["path"]) as s:
        n = min(3, s.height // cell) * cell
        rgb = s.read(window=((0, n), (0, n))).transpose(1, 2, 0)
    shown = [b for b in pred if b["score"] >= 0.25 and b["box"][2] < n and b["box"][3] < n]
    report.image(f"{key}_test_detections", draw_boxes(rgb.copy(), shown, colors),
                 f"{key}: Detect object with the trained model on test pictures (confidence ≥ 0.25)")
    rows.append([key, f"{tr['n']} pictures / {len(tr['boxes'])} objects", c["epochs_run"], c["val"]["map50"], at["precision"], at["recall"], sc["map50"]])
    # reference: the same model run directly (ultralytics) on each test picture. Detect object's tiling, stretch and
    # merging must not lose accuracy compared with it.
    ref = _direct(r["folder"], te, cell)
    rs = detection_scores(ref, te["boxes"])
    report.metric(f"{key} reference mAP50 (ultralytics per picture)", rs["map50"])
    joined = sum(1 for b in pred if b["box"][2] - b["box"][0] > cell * 1.05 or b["box"][3] - b["box"][1] > cell * 1.05)
    if joined:
        report.note(f"{key}: {joined} detections span two pictures (seam joining between neighbouring pictures of the test grid)")
    assert c["epochs_run"] >= 1 and det["zoom"] == 1.0
    assert sc["map50"] > 0.05   # a small sample: it must at least have started to learn
    assert sc["map50"] >= 0.85 * rs["map50"] - 0.02, f"Detect object {sc['map50']:.3f} vs the model itself {rs['map50']:.3f}"


_DIRECT = """
import json, sys
import numpy as np, rasterio
from lulc_fetch.detect import ultralytics
folder, path, cell, n = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
m = ultralytics().YOLO(folder + "/best.pt")
with rasterio.open(path) as s:
    img = s.read().transpose(1, 2, 0)
cols = img.shape[1] // cell
out = []
for k in range(n):
    r_, c_ = divmod(k, cols)
    tile = np.ascontiguousarray(img[r_ * cell:(r_ + 1) * cell, c_ * cell:(c_ + 1) * cell, ::-1])
    o = m.predict(tile, imgsz=cell, conf=0.01, verbose=False)[0]
    for b, sc, cl in zip(o.boxes.xyxy.cpu().numpy(), o.boxes.conf.cpu().numpy(), o.boxes.cls.cpu().numpy()):
        out.append({"cls": m.names[int(cl)], "score": float(sc), "box": [float(b[0]) + c_ * cell, float(b[1]) + r_ * cell,
                                                                        float(b[2]) + c_ * cell, float(b[3]) + r_ * cell]})
print("RESULT" + json.dumps(out))
"""


def _direct(folder, te, cell) -> list[dict]:
    """The model run straight through ultralytics on each test picture, in its own process: PyTorch's OpenMP runtime
    and XGBoost / LightGBM's (loaded by earlier tests) can't share one process (the app uses a child process too)."""
    import json
    import subprocess
    import sys

    from tests.conftest import ROOT
    r = subprocess.run([sys.executable, "-c", _DIRECT, str(folder), str(te["path"]), str(cell), str(te["n"])], cwd=ROOT,
                       capture_output=True, text=True, timeout=1800, env={**__import__("os").environ, "YOLO_VERBOSE": "False"})
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line, r.stderr[-3000:]
    return json.loads(line[6:])


def test_summary(report):
    report.table("Train detection model on real data (YOLO26, pretrained on COCO)",
                 ["data", "training data", "epochs", "val mAP50", "test precision @0.25", "test recall @0.25", "test mAP50"], rows)
    assert rows
