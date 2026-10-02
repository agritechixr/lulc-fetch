"""Make training data: image (+ ground truth) → image / label patches for deep learning."""

import json
from pathlib import Path

import pytest

from tests.helpers import ok, run


def make(client, data, name, ground_truth=None, **kw):
    body = {"path": data["s2"], "inputs": [{"path": data["s2"], "name": "s2"}], "ground_truth": ground_truth,
            "patch_m": [320, 320], "overlap_m": [0, 0], "name": name, **kw}
    return run(client, "/api/patches/make", body)


def test_plan(client, data):
    p = ok(client.post("/api/patches/plan", json={"path": data["s2"], "patch_m": [320, 320], "overlap_m": [0, 0]}))
    assert p


@pytest.mark.parametrize("gt", ["raster", "vector", None])
def test_make_patches(client, data, gt):
    ground_truth = {"raster": {"type": "raster", "path": data["labels"], "band": 1},
                    "vector": {"type": "vector", "geojson": data["samples"], "field": "class"}, None: None}[gt]
    r = make(client, data, f"patches_{gt}", ground_truth, overlap_m=[160, 160])
    folder = Path(r["folder"])
    meta = json.loads((folder / "dataset.json").read_text())
    assert meta["patch_size_px"] == [32, 32] and meta["band_count"] == 6
    assert len(list((folder / "images").glob("*.tif"))) >= 9
    if gt:
        assert meta["labels_dir"] and len(meta["classes"]) == 3
    else:
        assert not meta.get("labels_dir")


def test_dataset_is_offered_to_train_classify_model(client, data):
    r = make(client, data, "offered", {"type": "raster", "path": data["labels"], "band": 1})
    info = ok(client.post("/api/dl/dataset", json={"folder": r["folder"]}))
    assert info["labelled"] >= 9 and len(info["classes"]) == 3


def test_existing_folder_is_refused(client, data):
    make(client, data, "twice")
    body = {"path": data["s2"], "inputs": [{"path": data["s2"]}], "patch_m": [320, 320], "name": "twice"}
    assert client.post("/api/patches/make", json=body).status_code == 400
