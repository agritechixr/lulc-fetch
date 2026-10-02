"""Index analysis: indices, composites and formulas are drawn, read at a pixel and exported."""

import pytest
import rasterio
from rasterio.warp import transform

from tests import _data
from tests.helpers import ok, run


def lonlat(col: int, row: int = 48):
    tr = rasterio.transform.from_origin(*_data.ORIGIN, _data.RES, _data.RES)
    x, y = tr * (col + 0.5, row + 0.5)
    lon, lat = transform(_data.CRS, "EPSG:4326", [x], [y])
    return lon[0], lat[0]


def spec(info, **kw):
    return {"path": info["path"], "band_map": info["band_map"], "scale": info["scale"], "offset": info["offset"], **kw}


@pytest.mark.parametrize("index", ["NDVI", "NDWI", "SAVI", "EVI", "NDBI", "NBR"])
def test_render_index(client, s2_info, index):
    r = ok(client.post("/api/analyze/render", json=spec(s2_info, index=index)))
    assert r["image"].startswith("data:image/png;base64,")
    assert r["kind"] == "continuous" and r["stats"]


@pytest.mark.parametrize("view", [{"composite": "true"}, {"composite": "false"}, {"band": 4, "stretch": "auto"}, {"rgb": [3, 2, 1], "stretch": "auto"},
                                  {"formula": "B08 / B04"}])
def test_render_views(client, s2_info, view):
    r = ok(client.post("/api/analyze/render", json=spec(s2_info, **view)))
    assert r["image"].startswith("data:image/png")


def test_render_clipped_to_an_area(client, s2_info, data):
    r = ok(client.post("/api/analyze/render", json=spec(s2_info, index="NDVI", clip=data["aoi"])))
    assert r["clipped"] is True


@pytest.mark.parametrize("col,low,high", [(16, -1, -0.1), (48, 0.7, 1), (80, -0.2, 0.2)])   # water, vegetation, built-up
def test_ndvi_values_are_right(client, s2_info, col, low, high):
    lon, lat = lonlat(col)
    p = ok(client.post("/api/analyze/pixel", json=spec(s2_info, index="NDVI", lon=lon, lat=lat)))
    assert p["inside"] and low < p["value"] < high, p


def test_pixel_outside_the_image(client, s2_info):
    p = ok(client.post("/api/analyze/pixel", json=spec(s2_info, index="NDVI", lon=10.0, lat=10.0)))
    assert not p["inside"]


def test_bad_formula_is_an_error(client, s2_info):
    assert client.post("/api/analyze/render", json=spec(s2_info, formula="B08 +")).status_code == 400


def test_export_indices(client, s2_info, home):
    r = run(client, "/api/analyze/export", spec(s2_info, indices=["NDVI", "NDWI"], formulas=[{"name": "ratio", "formula": "B08 / B04"}]))
    assert r["layers"] == ["NDVI", "NDWI", "ratio"]
    with rasterio.open(home / r["path"]) as s:
        assert s.count == 3 and s.width == 96
