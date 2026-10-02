"""Small synthetic test data with known answers, written once per test session.

* s2.tif        96 × 96 px, 10 m, UTM 43N, 6 Sentinel-2-like bands (B02 B03 B04 B08 B11 B12, uint16 DN). Three land-cover
                zones from left to right: water | vegetation | built-up, with a little noise, so every classifier
                should reach high accuracy and every index has a predictable sign.
* labels.tif    the true classes of s2.tif (1 water, 2 vegetation, 3 built-up; 0 = no data)
* samples       polygons inside each zone (EPSG:4326 GeoJSON) with a text "class" attribute: "Training samples"
* aerial.tif    320 × 320 px, 0.25 m, 8-bit RGB "car park": grey asphalt with bright rectangular "cars"
* cars          the car rectangles as labelled polygons (class "car"), for Train detection model
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

CRS = "EPSG:32643"
ORIGIN = (500000.0, 2100000.0)
N = 96               # s2.tif size (px)
RES = 10.0
CLASSES = {1: "water", 2: "vegetation", 3: "built"}
BANDS = ["B02", "B03", "B04", "B08", "B11", "B12"]
# typical surface reflectance (0–1) per class and band
SPECTRA = {
    1: [0.06, 0.05, 0.03, 0.02, 0.01, 0.01],   # water: dark, NIR lowest
    2: [0.03, 0.06, 0.03, 0.40, 0.20, 0.10],   # vegetation: red edge, high NIR
    3: [0.15, 0.16, 0.18, 0.22, 0.28, 0.25],   # built-up: bright, flat, high SWIR
}


def zone(col: np.ndarray) -> np.ndarray:
    """Class of each column: thirds of the image."""
    return np.select([col < N // 3, col < 2 * N // 3], [1, 2], 3)


def _poly_4326(x0, y0, x1, y1, transform) -> dict:
    """A pixel rectangle as a GeoJSON polygon in EPSG:4326."""
    ring = [transform * (x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]
    return transform_geom(CRS, "EPSG:4326", {"type": "Polygon", "coordinates": [ring]})


def make_all(folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)

    # ---- Sentinel-2-like scene + true labels
    tr = from_origin(*ORIGIN, RES, RES)
    cols = np.tile(np.arange(N), (N, 1))
    lab = zone(cols).astype("uint8")
    data = np.zeros((len(BANDS), N, N), "uint16")
    for c, spec in SPECTRA.items():
        m = lab == c
        for b, refl in enumerate(spec):
            vals = refl * (1 + rng.normal(0, 0.06, m.sum()))
            data[b][m] = np.clip(vals * 10000, 1, 60000).astype("uint16")   # DN = reflectance × 10000
    prof = {"driver": "GTiff", "width": N, "height": N, "count": len(BANDS), "dtype": "uint16", "crs": CRS, "transform": tr, "nodata": 0}
    with rasterio.open(folder / "s2.tif", "w", **prof) as d:
        d.write(data)
        for i, b in enumerate(BANDS, 1):
            d.set_band_description(i, b)
    with rasterio.open(folder / "labels.tif", "w", **{**prof, "count": 1, "dtype": "uint8"}) as d:
        d.write(lab, 1)

    # ---- training samples: 2 squares inside each zone
    feats = []
    for c, name in CLASSES.items():
        x_mid = (c - 1) * N // 3 + N // 6
        for y0 in (10, 55):
            feats.append({"type": "Feature", "properties": {"class": name, "code": c},
                          "geometry": _poly_4326(x_mid - 8, y0, x_mid + 8, y0 + 20, tr)})
    samples = {"type": "FeatureCollection", "features": feats}
    (folder / "samples.geojson").write_text(json.dumps(samples))

    # ---- aerial "car park" + car polygons
    A, ares = 320, 0.25
    atr = from_origin(ORIGIN[0] + 5000, ORIGIN[1], ares, ares)
    img = np.full((3, A, A), 90, "uint8") + rng.integers(0, 12, (3, A, A), dtype="uint8")
    cars, taken = [], np.zeros((A, A), bool)
    colors = [(220, 30, 30), (240, 240, 240), (30, 60, 200), (20, 20, 20), (230, 200, 40)]
    while len(cars) < 40:
        h, w = (16, 8) if rng.random() < 0.5 else (8, 16)       # 4 m × 2 m cars
        y, x = int(rng.integers(4, A - h - 4)), int(rng.integers(4, A - w - 4))
        if taken[y - 3:y + h + 3, x - 3:x + w + 3].any():
            continue
        taken[y:y + h, x:x + w] = True
        col = colors[len(cars) % len(colors)]
        for k in range(3):
            img[k, y:y + h, x:x + w] = col[k]
        img[:, y + 2:y + h - 2, x + 2:x + w - 2] //= 2           # a darker "windscreen" so cars aren't flat squares
        cars.append({"type": "Feature", "properties": {"class": "car"}, "geometry": _poly_4326(x, y, x + w, y + h, atr)})
    with rasterio.open(folder / "aerial.tif", "w", driver="GTiff", width=A, height=A, count=3, dtype="uint8", crs=CRS, transform=atr) as d:
        d.write(img)
        for i, b in enumerate(["red", "green", "blue"], 1):
            d.set_band_description(i, b)
    cars_fc = {"type": "FeatureCollection", "features": cars}
    (folder / "cars.geojson").write_text(json.dumps(cars_fc))

    # an AOI polygon over the middle of s2.tif (EPSG:4326), for "area" options
    aoi = _poly_4326(20, 20, 76, 76, tr)
    return {"folder": folder, "samples": samples, "cars": cars_fc, "aoi": aoi}
