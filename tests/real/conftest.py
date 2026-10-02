"""Shared inputs for the real-data suite, made once per run from the data folder (see tests/real/README.md).

Run:  .venv/bin/python -m pytest tests/real --real                 (small: about 10–20 minutes)
      .venv/bin/python -m pytest tests/real --real --real-size medium   (more images / epochs)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio

import webapp.workspace as ws
from tests.helpers import ok, run
from tests.real import adapters

# Sentinel-2 test area: 6 × 6 km around Lake Albano (Castelli Romani, Italy): lake, towns, woods, fields; < 1 % cloud
S2_CENTER = (12.665, 41.748)
S2_HALF_DEG = (0.036, 0.027)
# Sentinel-1 test area: 10 × 15 km over Vienna and the Danube
S1_CENTER = (16.40, 48.20)
# SCL (Sentinel-2 scene classification) → test classes
SCL_CLASSES = {4: (1, "vegetation", "#1b7837"), 5: (2, "bare / built-up", "#d6604d"), 6: (3, "water", "#2166ac")}
CLASS_COLORS = {v: c for v, _, c in SCL_CLASSES.values()}
CLASS_NAMES = {v: n for v, n, _ in SCL_CLASSES.values()}

SIZES = {   # how much of the data each run uses
    "small": {"seg_train": 120, "seg_test": 10, "seg_epochs": 8, "s2_epochs": 12, "det_train": 100, "det_test": 25, "det_epochs": 30,
              "tab_models": "all", "yolo_size": "n"},
    "medium": {"seg_train": 400, "seg_test": 30, "seg_epochs": 20, "s2_epochs": 30, "det_train": 225, "det_test": 64, "det_epochs": 40,
               "tab_models": "all", "yolo_size": "s"},
    "large": {"seg_train": 1500, "seg_test": 100, "seg_epochs": 40, "s2_epochs": 60, "det_train": 800, "det_test": 126, "det_epochs": 80,
              "tab_models": "all", "yolo_size": "m"},
}


@pytest.fixture(scope="session")
def size(request) -> dict:
    s = request.config.getoption("--real-size")
    return {"name": s, **SIZES[s]}


@pytest.fixture(scope="session")
def rd(request) -> Path:
    d = Path(request.config.getoption("--data"))
    if not d.is_dir():
        pytest.skip(f"real data folder {d} not found (use --data)")
    return d


def _need(p: Path) -> Path:
    if not p.exists():
        pytest.skip(f"{p} not in the data folder")
    return p


def _box(center, half) -> dict:
    (lon, lat), (dx, dy) = center, half
    return {"type": "Polygon", "coordinates": [[[lon - dx, lat - dy], [lon + dx, lat - dy], [lon + dx, lat + dy], [lon - dx, lat + dy], [lon - dx, lat - dy]]]}


@pytest.fixture(scope="session")
def work() -> Path:
    """Where the test-made inputs go (inside the workspace's uploads/, where the app accepts rasters)."""
    d = ws.root() / "uploads" / "real"
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel(p: Path) -> str:
    return ws.rel(p)


# ------------------------------------------------------------------ Sentinel-2
@pytest.fixture(scope="session")
def s2_zip(rd) -> Path:
    zs = sorted((rd / "sentinal_multispectral").glob("S2*_MSIL2A_*.SAFE.zip")) or sorted((rd / "sentinal_multispectral").glob("S2*.SAFE*"))
    if not zs:
        pytest.skip("no Sentinel-2 product in data/sentinal_multispectral")
    return zs[0]


@pytest.fixture(scope="session")
def s2_aoi() -> dict:
    return _box(S2_CENTER, S2_HALF_DEG)


@pytest.fixture(scope="session")
def s2_vrt(client, s2_zip) -> dict:
    """The product opened with Your Sentinel products (link + open): a 12-band 10 m VRT."""
    linked = ok(client.post("/api/products/link", json={"path": str(s2_zip)}))
    p = linked["products"][0]
    opened = ok(client.post("/api/products/open", json={"path": p["path"]}))
    info = ok(client.get("/api/rasters/info", params={"path": opened["path"]}))
    return {"product": p, "path": opened["path"], "info": info}


@pytest.fixture(scope="session")
def s2_stack(client, s2_vrt, s2_aoi) -> dict:
    """The 12 bands over the test area as one GeoTIFF, made with Stack layers."""
    r = run(client, "/api/stack", {"items": [{"path": s2_vrt["path"], "name": "S2"}], "ref": s2_vrt["path"], "clip": s2_aoi, "name": "albano_s2"})
    info = ok(client.get("/api/rasters/info", params={"path": r["path"]}))
    return {"path": r["path"], "info": info, "abs": ws.root() / r["path"]}


@pytest.fixture(scope="session")
def scl_labels(s2_zip, s2_stack, work) -> dict:
    """ESA's own scene classification (SCL, 20 m) on the stack's 10 m grid: 1 vegetation, 2 bare / built-up, 3 water, 0 other."""
    import zipfile

    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    member = next(n for n in zipfile.ZipFile(s2_zip).namelist() if n.endswith("_SCL_20m.jp2"))
    with rasterio.open(s2_stack["abs"]) as ref, rasterio.open(f"/vsizip/{s2_zip}/{member}") as src, \
            WarpedVRT(src, crs=ref.crs, transform=ref.transform, width=ref.width, height=ref.height, resampling=Resampling.nearest) as v:
        scl = v.read(1)
        prof = {"driver": "GTiff", "width": ref.width, "height": ref.height, "count": 1, "dtype": "uint8", "crs": ref.crs,
                "transform": ref.transform, "nodata": 0}
    lab = np.zeros_like(scl, dtype="uint8")
    for code, (v_, _, _) in SCL_CLASSES.items():
        lab[scl == code] = v_
    out = work / "albano_scl_labels.tif"
    with rasterio.open(out, "w", **prof) as d:
        d.write(lab, 1)
        d.write_colormap(1, {0: (0, 0, 0, 0), **{v: tuple(int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) + (255,) for v, c in CLASS_COLORS.items()}})
    return {"path": rel(out), "abs": out, "array": lab}


@pytest.fixture(scope="session")
def s2_rgb(s2_stack) -> np.ndarray:
    from tests.real.metrics import stretch_rgb
    with rasterio.open(s2_stack["abs"]) as s:
        d = list(s.descriptions)
        return stretch_rgb(s.read([d.index("B04") + 1, d.index("B03") + 1, d.index("B02") + 1]))


# ------------------------------------------------------------------ Sentinel-1
@pytest.fixture(scope="session")
def s1_zip(rd) -> Path:
    zs = sorted((rd / "sentinal_SAR").glob("S1*_GRD*.SAFE.zip")) or sorted((rd / "sentinal_SAR").glob("S1*.SAFE*"))
    if not zs:
        pytest.skip("no Sentinel-1 GRD product in data/sentinal_SAR")
    return zs[0]


@pytest.fixture(scope="session")
def s1_aoi() -> dict:
    return _box(S1_CENTER, (0.07, 0.07))


# ------------------------------------------------------------------ tables
@pytest.fixture(scope="session")
def tables(client, rd) -> dict:
    """The data folder's CSV tables, added with + Add data (table upload)."""
    folder = next((p for p in rd.iterdir() if p.is_dir() and p.name.strip().lower() == "tabular data"), None)
    if folder is None:
        pytest.skip("no 'tabular data' folder")
    out = {}
    for f in sorted(folder.glob("*.csv")):
        with open(f, "rb") as fh:
            up = ok(client.post("/api/tables/upload", files={"file": (f.name, fh, "text/csv")}))
        out[f.stem] = up["path"]
    return out


# ------------------------------------------------------------------ photo datasets (segmentation) and YOLO datasets (detection)
@pytest.fixture(scope="session")
def seg_sets(rd, work, size) -> dict:
    """flood / water / forest: a training folder + held-out test photos, built once."""
    out = {}
    for name in ("flood", "water", "forest"):
        try:
            pairs = adapters.seg_pairs(name, rd)
        except (FileNotFoundError, OSError):
            continue
        if len(pairs) < 20:
            continue
        tr, te = adapters.split(pairs, size["seg_train"], size["seg_test"], seed=1)
        ds = adapters.seg_dataset(name, tr, ws.root() / "training_data" / f"{name}_photos")
        m = adapters.seg_test_mosaic(te, work / f"{name}_test_mosaic.tif")
        out[name] = {"dataset": str(ds), "test": {**m, "rel": rel(m["path"])}, "n_train": len(tr), "classes": adapters.SEG_CLASSES[name]}
    if not out:
        pytest.skip("no segmentation photo sets in data/classify_data")
    return out


@pytest.fixture(scope="session")
def det_sets(rd, work, size) -> dict:
    """Cars Detection and the wind-farm set as train / test mosaics with GeoJSON labels."""
    out = {}
    for key, folder, cell in (("cars", "Cars Detection", 416), ("wind", "object detection1", 640)):
        if not (rd / "detection_data" / folder / "data.yaml").is_file():
            continue
        y = adapters.yolo_set(rd, folder)
        tr, _ = adapters.split(y["train"], size["det_train"], 0, seed=2)
        te_src = y["test"] or y["valid"]
        te, _ = adapters.split(te_src, size["det_test"], 0, seed=3)
        out[key] = {"names": y["names"], "cell": cell,
                    "train": adapters.mosaic(tr, y["names"], work / f"{key}_train_mosaic.tif", cell=cell),
                    "test": adapters.mosaic(te, y["names"], work / f"{key}_test_mosaic.tif", cell=cell, origin=(420000.0, 4700000.0))}
        for part in ("train", "test"):
            out[key][part]["rel"] = rel(out[key][part]["path"])
    if not out:
        pytest.skip("no YOLO detection sets in data/detection_data")
    return out


def save_json(path: Path, obj):
    path.write_text(json.dumps(obj, indent=1, default=str))


@pytest.fixture(scope="session")
def s2_table(client, s2_stack, scl_labels) -> dict:
    """Raster → table: the 12 bands + the SCL class, 2000 pixels per class."""
    r = run(client, "/api/tables/from-raster", {"path": s2_stack["path"], "ground_truth": {"type": "raster", "path": scl_labels["path"], "band": 1},
                                                "sampling": "stratified", "per_class": 2000, "name": "albano_scl_table"})
    return {"path": r["path"], "report": r, "features": r["band_columns"]}


@pytest.fixture(scope="session")
def s2_patches(client, s2_stack, scl_labels) -> dict:
    """Make training data: 64 × 64 px (640 m) patches of the 12 bands with SCL labels, half-overlapping."""
    r = run(client, "/api/patches/make", {"path": s2_stack["path"], "inputs": [{"path": s2_stack["path"], "name": "s2"}],
                                          "ground_truth": {"type": "raster", "path": scl_labels["path"], "band": 1},
                                          "patch_m": [640, 640], "overlap_m": [320, 320], "name": "albano_patches", "min_labelled": 0.3})
    return r
