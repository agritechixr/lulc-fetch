"""Bring the data folder's datasets into the form the app's tools take.

The app works on georeferenced map layers, but two kinds of data in data/ are plain pictures:

* image / mask segmentation sets (flood, water bodies, forest): JPG photos with a black / white mask each
  → a "Make training data"-style folder (images/*.tif, labels/*.tif, dataset.json, patches.csv) that
    Train classify model reads, and held-out photos as GeoTIFFs for Classify image;
* YOLO detection sets (Cars Detection, wind farms): many small pictures + label .txt files
  → one georeferenced mosaic (GeoTIFF, a grid of the pictures) + the labelled boxes as GeoJSON polygons,
    which is what Train detection model and Detect object take.

The pictures get an arbitrary but consistent map position (UTM 33N, fixed pixel size), which doesn't change what the
models see.
"""

from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

CRS = "EPSG:32633"


# ------------------------------------------------------------------ segmentation: image / mask pairs
def seg_pairs(name: str, root: Path) -> list[tuple[Path, Path]]:
    """(image, mask) pairs of one of the data folder's segmentation sets."""
    if name == "flood":
        d = root / "classify_data" / "flood data"
        rows = list(csv.DictReader(open(d / "metadata.csv", encoding="utf-8")))
        return [(d / "Image" / r["Image"], d / "Mask" / r["Mask"]) for r in rows if (d / "Image" / r["Image"]).is_file()]
    if name == "water":
        d = root / "classify_data" / "Water Bodies Dataset"
        return [(p, d / "Masks" / p.name) for p in sorted((d / "Images").glob("*.jpg")) if (d / "Masks" / p.name).is_file()]
    if name == "forest":
        d = root / "classify_data" / "forest data"
        inner = d / "Forest Segmented" / "Forest Segmented"
        rows = list(csv.DictReader(open(d / "meta_data.csv", encoding="utf-8")))
        return [(inner / "images" / r["image"], inner / "masks" / r["mask"]) for r in rows if (inner / "images" / r["image"]).is_file()]
    raise ValueError(name)


SEG_CLASSES = {"flood": ("dry land", "flood water"), "water": ("land", "water"), "forest": ("other", "forest")}
SEG_COLORS = ("#c8b28a", "#1f78b4")


def split(items: list, n_train: int, n_test: int, seed: int = 0) -> tuple[list, list]:
    """A fixed random choice of training and held-out items."""
    rng = random.Random(seed)
    idx = list(range(len(items)))
    rng.shuffle(idx)
    return [items[i] for i in idx[:n_train]], [items[i] for i in idx[n_train:n_train + n_test]]


def _load_pair(img: Path, mask: Path, size: int) -> tuple[np.ndarray, np.ndarray]:
    im = Image.open(img).convert("RGB").resize((size, size), Image.Resampling.BILINEAR)
    m = Image.open(mask).convert("L").resize((size, size), Image.Resampling.NEAREST)
    return np.asarray(im).transpose(2, 0, 1), (np.asarray(m) >= 128).astype("uint8")


def seg_dataset(name: str, pairs: list[tuple[Path, Path]], out: Path, size: int = 256, res: float = 1.0) -> Path:
    """Write a "Make training data" folder: images/*.tif (3 bands) and labels/*.tif (1 = background, 2 = object)."""
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    cols = math.ceil(math.sqrt(len(pairs)))
    gap = size * res * 2   # patches don't overlap, so the spatial split never drops any
    index = []
    for k, (img, mask) in enumerate(pairs):
        a, m = _load_pair(img, mask, size)
        r, c = divmod(k, cols)
        x0, y0 = 500000 + c * gap, 4600000 - r * gap
        tr = from_origin(x0, y0, res, res)
        fname = f"{name}_r{r:04d}_c{c:04d}.tif"
        prof = {"driver": "GTiff", "width": size, "height": size, "crs": CRS, "transform": tr}
        with rasterio.open(out / "images" / fname, "w", count=3, dtype="uint8", **prof) as d:
            d.write(a)
            for i, b in enumerate(("red", "green", "blue"), 1):
                d.set_band_description(i, b)
        with rasterio.open(out / "labels" / fname, "w", count=1, dtype="uint8", nodata=0, **prof) as d:
            d.write(m + 1, 1)
        index.append({"file": fname, "row": r, "col": c, "x_min": x0, "y_min": y0 - size * res, "x_max": x0 + size * res, "y_max": y0})
    with open(out / "patches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(index[0]))
        w.writeheader()
        w.writerows(index)
    names = SEG_CLASSES[name]
    meta = {"name": f"{name}_photos", "images_dir": "images", "labels_dir": "labels", "bands": ["red", "green", "blue"], "band_count": 3,
            "dtype": "uint8", "patch_size_px": [size, size], "patch_size_m": [size * res, size * res], "pixel_size": [res, res], "crs": CRS,
            "stride_px": [size * 2, size * 2], "overlap_px": [0, 0],
            "classes": [{"value": 1, "name": names[0], "color": SEG_COLORS[0]}, {"value": 2, "name": names[1], "color": SEG_COLORS[1]}],
            "created": "test adapter"}
    (out / "dataset.json").write_text(json.dumps(meta, indent=1))
    return out


# ------------------------------------------------------------------ detection: YOLO folders → mosaic + GeoJSON
def _yaml_names(text: str) -> list[str]:
    """Class names from a YOLO data.yaml: `names: ['a', 'b']` or a `names:` block of `0: a` lines."""
    try:
        import yaml
        n = yaml.safe_load(text)["names"]
        return n if isinstance(n, list) else [n[k] for k in sorted(n)]
    except ImportError:
        import ast
        import re
        m = re.search(r"^names:\s*(\[.*?\])", text, re.M | re.S)
        if m:
            return list(ast.literal_eval(m.group(1)))
        return [v.strip().strip("'\"") for v in re.findall(r"^\s+\d+:\s*(.+)$", text.split("names:", 1)[1], re.M)]


def yolo_set(root: Path, name: str) -> dict:
    """Images, labels and class names of a YOLO dataset in the data folder."""
    d = root / "detection_data" / name
    names = _yaml_names((d / "data.yaml").read_text())

    def items(split_name):
        out = []
        for p in sorted((d / split_name / "images").glob("*")):
            lab = d / split_name / "labels" / (p.stem + ".txt")
            if p.suffix.lower() in (".jpg", ".jpeg", ".png") and lab.is_file():
                out.append((p, lab))
        return out
    return {"names": names, "train": items("train"), "valid": items("valid"), "test": items("test")}


def mosaic(items: list[tuple[Path, Path]], names: list[str], out_tif: Path, cell: int = 640, res: float = 0.1, origin=(400000.0, 4700000.0)) -> dict:
    """A grid of pictures (each resized to cell × cell) as one GeoTIFF, and their YOLO boxes as polygons (EPSG:4326)."""
    n = len(items)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    W, H = cols * cell, rows * cell
    tr = from_origin(origin[0], origin[1], res, res)
    img = np.zeros((3, H, W), "uint8")
    boxes, feats = [], []
    for k, (p, lab) in enumerate(items):
        r, c = divmod(k, cols)
        a = np.asarray(Image.open(p).convert("RGB").resize((cell, cell), Image.Resampling.BILINEAR)).transpose(2, 0, 1)
        img[:, r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = a
        for line in lab.read_text().splitlines():
            v = line.split()
            if len(v) != 5:   # YOLO box lines only: class cx cy w h
                continue
            k_, cx, cy, w, h = int(v[0]), *map(float, v[1:])
            x0, y0 = c * cell + (cx - w / 2) * cell, r * cell + (cy - h / 2) * cell
            x1, y1 = x0 + w * cell, y0 + h * cell
            boxes.append({"cls": names[k_], "box": [x0, y0, x1, y1]})
            ring = [tr * (x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]
            feats.append({"type": "Feature", "properties": {"class": names[k_]},
                          "geometry": transform_geom(CRS, "EPSG:4326", {"type": "Polygon", "coordinates": [ring]})})
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_tif, "w", driver="GTiff", width=W, height=H, count=3, dtype="uint8", crs=CRS, transform=tr,
                       tiled=True, compress="deflate") as d:
        d.write(img)
        for i, b in enumerate(("red", "green", "blue"), 1):
            d.set_band_description(i, b)
    return {"path": out_tif, "boxes": boxes, "geojson": {"type": "FeatureCollection", "features": feats}, "transform": tr,
            "size": (W, H), "cell": cell, "n": n}


def seg_test_mosaic(pairs: list[tuple[Path, Path]], out_tif: Path, size: int = 256, res: float = 1.0) -> dict:
    """Held-out photos as one GeoTIFF grid (one Classify image run for all of them) and their true masks (1 / 2)."""
    n = len(pairs)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    img = np.zeros((3, rows * size, cols * size), "uint8")
    cells = []
    for k, (p, m) in enumerate(pairs):
        a, t = _load_pair(p, m, size)
        r, c = divmod(k, cols)
        img[:, r * size:(r + 1) * size, c * size:(c + 1) * size] = a
        cells.append({"r": r, "c": c, "truth": t + 1, "rgb": a.transpose(1, 2, 0), "source": p.name})
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_tif, "w", driver="GTiff", width=cols * size, height=rows * size, count=3, dtype="uint8", crs=CRS,
                       transform=from_origin(700000, 4600000, res, res)) as d:
        d.write(img)
        for i, b in enumerate(("red", "green", "blue"), 1):
            d.set_band_description(i, b)
    return {"path": out_tif, "cells": cells, "size": size}
