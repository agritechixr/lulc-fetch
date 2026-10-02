"""Train classify model + Classify image (deep learning, semantic segmentation). Needs the PyTorch add-on.

Models train from random weights (pretrained = off), so nothing is downloaded."""

import time

import numpy as np
import pytest
import rasterio

from tests.helpers import ok, run, wait

pytestmark = pytest.mark.dl


@pytest.fixture(scope="module")
def dataset(client, data) -> str:
    r = run(client, "/api/patches/make", {"path": data["s2"], "inputs": [{"path": data["s2"], "name": "s2"}],
                                          "ground_truth": {"type": "raster", "path": data["labels"], "band": 1},
                                          "patch_m": [320, 320], "overlap_m": [160, 160], "name": "dl_patches"})
    return r["folder"]


def train(client, dataset, arch, encoder, name, epochs=3, **params):
    return run(client, "/api/dl/train", {"dataset": dataset, "arch": arch, "encoder": encoder, "pretrained": False, "name": name,
                                         "params": {"epochs": epochs, "batch_size": 8, "device": "cpu", "early_stop": False, **params}})


def test_status_and_schema(client):
    st = ok(client.get("/api/dl/status"))
    assert st["available"], st.get("error")
    sc = ok(client.get("/api/dl/schema"))
    assert {"unet", "deeplabv3plus", "segformer", "fcn", "lraspp", "yolo_sem"} <= set(sc["archs"])


def test_datasets_are_listed(client, dataset):
    assert any(d["folder"] == dataset for d in ok(client.get("/api/dl/datasets")))


@pytest.mark.parametrize("arch,encoder", [("unet", "mobilenet_v2"), ("fpn", "resnet18"), ("linknet", "resnet18"), ("lraspp", "mobilenetv3_large")])
def test_train_architectures(client, dataset, home, arch, encoder):
    r = train(client, dataset, arch, encoder, f"t_{arch}")
    c = r["config"]
    assert c["epochs_run"] == 3 and c["val"]["miou"] is not None
    for f in ("best_model.pt", "last_model.pt", "model_config.json", "training_log.csv", "report.html"):
        assert (__import__("pathlib").Path(r["folder"]) / f).is_file(), f


@pytest.mark.slow
def test_unet_learns_and_maps_the_image(client, dataset, data, home):
    r = train(client, dataset, "unet", "resnet18", "unet_good", epochs=25, lr=0.003)
    assert r["config"]["val"]["miou"] > 0.6, r["config"]["val"]
    models = ok(client.get("/api/dl/models"))
    m = next(m for m in models if m["folder"] == r["folder"])
    assert client.get("/api/dl/report", params={"folder": m["folder"]}).status_code == 200
    # Classify image with it
    p = run(client, "/api/dl/predict", {"model": m["folder"], "inputs": [{"path": data["s2"], "name": "s2"}], "name": "unet_map"})
    with rasterio.open(home / p["path"]) as s, rasterio.open(data["folder"] / "labels.tif") as t:
        assert s.count == 2   # class + confidence
        assert (s.read(1) == t.read(1)).mean() > 0.8


def test_resume_training(client, dataset):
    r = train(client, dataset, "unet", "mobilenet_v2", "resumable", epochs=2)
    r2 = run(client, "/api/dl/train", {"dataset": dataset, "arch": "unet", "encoder": "mobilenet_v2", "pretrained": False, "name": "resumable",
                                       "resume": r["folder"], "params": {"epochs": 4, "batch_size": 8, "device": "cpu", "early_stop": False}})
    assert r2["config"]["epochs_run"] == 4


def test_cancel_keeps_the_best_model(client, dataset):
    job = ok(client.post("/api/dl/train", json={"dataset": dataset, "arch": "unet", "encoder": "mobilenet_v2", "pretrained": False, "name": "cancelled",
                                                "params": {"epochs": 200, "batch_size": 8, "device": "cpu", "early_stop": False}}))
    for _ in range(600):
        j = ok(client.get(f"/api/jobs/{job['id']}"))
        if (j.get("live") or {}).get("history"):
            break
        time.sleep(0.2)
    ok(client.post(f"/api/jobs/{job['id']}/cancel"))
    for _ in range(600):
        j = ok(client.get(f"/api/jobs/{job['id']}"))
        if j["status"] in ("done", "cancelled", "error"):
            break
        time.sleep(0.2)
    assert j["status"] in ("done", "cancelled"), j.get("error")
    if j["status"] == "done":
        assert j["result"]["config"]["stopped"] == "stopped by the user"


def test_classify_image_needs_matching_bands(client, dataset, data):
    r = train(client, dataset, "unet", "mobilenet_v2", "bands_check", epochs=1)
    job = ok(client.post("/api/dl/predict", json={"model": r["folder"], "inputs": [{"path": data["aerial"]}], "name": "wrong"}))
    j = None
    for _ in range(600):
        j = ok(client.get(f"/api/jobs/{job['id']}"))
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert j["status"] == "error" and "bands" in j["error"]


@pytest.mark.yolo
def test_yolo26_semantic(client, dataset, data, home):
    r = train(client, dataset, "yolo_sem", "yolo-n", "yolo_sem", epochs=2)
    assert r["config"]["arch"] == "yolo_sem"
    p = run(client, "/api/dl/predict", {"model": r["folder"], "inputs": [{"path": data["s2"]}], "name": "yolo_sem_map"})
    with rasterio.open(home / p["path"]) as s:
        assert set(np.unique(s.read(1))) <= {1, 2, 3}
