"""Water mask from multispectral images: band guessing (Sentinel-2, Landsat, unnamed), indices, clouds, specks, the
route, and the mask as evidence for the SAR flood map."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from lulc_fetch import watermask as WM
from tests.helpers import run

TOOL = "Water mask"
H, W = 120, 160
YY, XX = np.mgrid[0:H, 0:W]
LAKE = np.hypot(YY - 60, XX - 60) < 30
REFL = {"blue": (0.06, 0.05), "green": (0.08, 0.07), "red": (0.06, 0.08), "nir": (0.03, 0.30), "swir1": (0.02, 0.20), "swir2": (0.01, 0.12)}   # (water, land)


def image(path, names, order=("blue", "green", "red", "nir", "swir1", "swir2"), scale=10000, scl=None, rng=np.random.default_rng(1)):
    bands = [np.where(LAKE, REFL[r][0], REFL[r][1]) * (1 + 0.05 * rng.standard_normal((H, W))) * scale for r in order]
    if scl is not None:
        bands.append(scl.astype("float32"))
    prof = dict(driver="GTiff", width=W, height=H, count=len(bands), dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1400000, 10, 10))
    with rasterio.open(path, "w", **prof) as d:
        for i, b in enumerate(bands, 1):
            d.write(b.astype("float32"), i)
            if names:
                d.set_band_description(i, names[i - 1])
    return path


def test_band_guessing(tmp_path):
    s2 = WM.guess_bands(image(tmp_path / "s2.tif", ["B02", "B03", "B04", "B08", "B11", "B12"]))["bands"]
    assert s2 == {"blue": 1, "green": 2, "red": 3, "nir": 4, "swir1": 5, "swir2": 6}
    l8 = WM.guess_bands(image(tmp_path / "LC09_scene.tif", ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]))   # Landsat B5 = NIR
    assert l8["bands"]["nir"] == 4 and l8["bands"]["swir1"] == 5 and "Landsat" in l8["sensor"]
    assert WM.guess_bands(image(tmp_path / "plain.tif", None))["bands"]["swir2"] == 6                                  # unnamed 6 bands
    words = WM.guess_bands(image(tmp_path / "w.tif", ["Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2"]))["bands"]
    assert words["nir"] == 4 and words["swir2"] == 6


def test_water_mask_indices_clouds_and_specks():
    rng = np.random.default_rng(2)
    b = {r: np.where(LAKE, v[0], v[1]) * (1 + 0.05 * rng.standard_normal((H, W))) for r, v in REFL.items()}
    for idx in WM.INDICES:
        r = WM.water_mask(b, idx=idx)
        assert ((r["classes"] == 2) == LAKE).mean() > 0.97, idx
    assert WM.water_mask(b)["index_name"] == "awei_nsh"
    assert WM.water_mask({k: b[k] for k in ("green", "nir")})["index_name"] == "ndwi"                      # no SWIR
    cloud = XX > 120
    r = WM.water_mask(b, masked=cloud)
    assert (r["classes"][cloud] == 3).all() and np.isnan(r["confidence"][cloud]).all()
    speck = {k: v.copy() for k, v in b.items()}
    for k, v in REFL.items():
        speck[k][100:102, 140:142] = v[0]                                                                     # a 2 × 2 "pond"
    assert WM.water_mask(speck, min_px=9)["classes"][100, 140] == 1 and WM.water_mask(speck, min_px=1)["classes"][100, 140] == 2
    with pytest.raises(ValueError):
        WM.water_mask({"green": b["green"], "nir": b["nir"]}, idx="mndwi")


def test_water_mask_route_and_flood_map(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    scl = np.where(XX > 130, 9, 4)                                                                             # clouds on the right
    image(up / "wm_s2.tif", ["B02", "B03", "B04", "B08", "B11", "B12", "SCL"], scl=scl)
    g = client.get("/api/raster/watermask/bands", params={"path": "uploads/wm_s2.tif"}).json()
    assert g["bands"]["swir2"] == 6 and "awei_nsh" in g["indices"]
    r = run(client, "/api/raster/watermask", {"path": "uploads/wm_s2.tif"})
    assert r["index_key"] == "awei_nsh" and r["areas_ha"]["Water"] == pytest.approx(LAKE.sum() * 0.01, rel=0.05)
    assert r["pixels"]["Masked (cloud, shadow, snow)"] == int((XX > 130).sum())
    with rasterio.open(home / r["outputs"][0]) as d:
        assert d.tags()["water_mask"] == "1" and set(np.unique(d.read(1))) <= {1, 2, 3}
    # the mask as optical evidence in the flood map, with radar that agrees
    from tests.test_sar import scene
    rng = np.random.default_rng(3)
    scene(up / "wm_s1_2026-08-20.tif", np.where(LAKE, 0.002, 0.08)[:H, :W] * rng.gamma(4.4, 1 / 4.4, (H, W)), np.where(LAKE, 0.0004, 0.015) * rng.gamma(4.4, 1 / 4.4, (H, W)))
    f = run(client, "/api/sar/water", {"post": "uploads/wm_s1_2026-08-20.tif", "water_mask": r["outputs"][0], "dem": None, "permanent": None})
    assert "optical water mask" in f["sources"] and f["areas_ha"]["Water"] == pytest.approx(LAKE.sum() * 0.04, rel=0.15)
