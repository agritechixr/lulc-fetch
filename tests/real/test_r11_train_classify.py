"""Train classify model + Classify image on real data:
* the Sentinel-2 scene with ESA's scene classification (12 bands),
* the data folder's photo sets (flood, water bodies, forest): RGB photos with masks.

Models start from pretrained weights (ImageNet for U-Net, Cityscapes for YOLO26 semantic), downloaded once."""

import numpy as np
import pytest
import rasterio

import webapp.workspace as ws
from tests.helpers import ok, run
from tests.real.adapters import SEG_COLORS
from tests.real.conftest import CLASS_COLORS, CLASS_NAMES
from tests.real.metrics import colorize, seg_scores, side_by_side

TOOL = "Train classify model & Classify image"
pytestmark = [pytest.mark.dl, pytest.mark.weights]
rows = []


def train(client, dataset, arch, encoder, name, epochs, batch=16):
    return run(client, "/api/dl/train", {"dataset": dataset, "arch": arch, "encoder": encoder, "pretrained": True, "name": name,
                                         "params": {"epochs": epochs, "batch_size": batch, "patience": max(5, epochs // 3)}})


def test_scene_unet(client, s2_patches, s2_stack, scl_labels, s2_rgb, size, report):
    r = train(client, s2_patches["folder"], "unet", "mobilenet_v2", "albano_unet", size["s2_epochs"])
    c = r["config"]
    report.metric("val mIoU", c["val"]["miou"])
    report.metric("val accuracy", c["val"]["accuracy"])
    report.metric("epochs (best)", f"{c['epochs_run']} ({c['best_epoch']})")
    report.file(r["report"], "albano_unet_report.html", "U-Net on Sentinel-2 + SCL: training report")
    p = run(client, "/api/dl/predict", {"model": r["folder"], "inputs": [{"path": s2_stack["path"]}], "name": "albano_unet_map"})
    with rasterio.open(ws.root() / p["path"]) as s:
        pred = s.read(1)
    sc = seg_scores(pred, scl_labels["array"], list(CLASS_NAMES))
    report.metric("whole-area agreement with SCL", sc["accuracy"])
    report.image("albano_unet_map", side_by_side(s2_rgb, colorize(scl_labels["array"], CLASS_COLORS), colorize(pred, CLASS_COLORS), height=320),
                 "U-Net: image | ESA SCL | Classify image result")
    rows.append(["Sentinel-2 / SCL", "U-Net · MobileNetV2", c["val"]["miou"], sc["accuracy"], sc["miou"]])
    assert sc["accuracy"] > 0.75


@pytest.mark.parametrize("arch,encoder", [("unet", "mobilenet_v2"), pytest.param("yolo_sem", "yolo-s", marks=pytest.mark.yolo)])
@pytest.mark.parametrize("name", ["flood", "water", "forest"])
def test_photo_sets(client, seg_sets, size, name, arch, encoder, report):
    if name not in seg_sets:
        pytest.skip(f"{name} set not in the data folder")
    s = seg_sets[name]
    r = train(client, s["dataset"], arch, encoder, f"{name}_{arch}", size["seg_epochs"], batch=8)
    c = r["config"]
    report.metric(f"{name} {arch} val mIoU", c["val"]["miou"])
    report.file(r["report"], f"{name}_{arch}_report.html", f"{name} · {c['arch_title']}: training report")
    # Classify image on the held-out photos (one mosaic), scored photo by photo
    t = s["test"]
    p = run(client, "/api/dl/predict", {"model": r["folder"], "inputs": [{"path": t["rel"]}], "name": f"{name}_{arch}_test", "overlap": 0})
    with rasterio.open(ws.root() / p["path"]) as src:
        pred = src.read(1)
    S = t["size"]
    ious, accs, pics = [], [], []
    for cell in t["cells"]:
        pc = pred[cell["r"] * S:(cell["r"] + 1) * S, cell["c"] * S:(cell["c"] + 1) * S]
        sc = seg_scores(pc, cell["truth"], [1, 2])
        ious.append(sc["iou"][2])
        accs.append(sc["accuracy"])
        if len(pics) < 4:
            cols = {1: SEG_COLORS[0], 2: SEG_COLORS[1]}
            pics.append(side_by_side(cell["rgb"], colorize(cell["truth"], cols), colorize(pc, cols), height=160))
    iou = float(np.nanmean(ious))
    report.metric(f"{name} {arch} test IoU ({s['classes'][1]})", iou)
    report.metric(f"{name} {arch} test pixel accuracy", float(np.mean(accs)))
    report.image(f"{name}_{arch}_examples", np.concatenate([np.pad(x, ((0, 6), (0, max(0, max(q.shape[1] for q in pics) - x.shape[1])), (0, 0)),
                                                                   constant_values=255) for x in pics]),
                 f"{name} · {c['arch_title']}: held-out photo | true mask | prediction")
    rows.append([f"{name} photos ({s['n_train']} train / {len(t['cells'])} test)", c["arch_title"], c["val"]["miou"], float(np.mean(accs)), iou])
    assert np.mean(accs) > 0.6


def test_summary(report):
    report.table("Train classify model on real data", ["data", "model", "val mIoU (training)", "test pixel accuracy", "test IoU / mIoU"], rows)
    assert rows
