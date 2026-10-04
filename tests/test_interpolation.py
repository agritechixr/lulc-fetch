"""Interpolation tool: seven methods on points with a known surface, the leave-one-out check, cutting to an area, and the
API (run + compare)."""

import numpy as np
import pytest
import rasterio

import webapp.workspace as ws
from lulc_fetch import interpolation as it
from tests.helpers import ok, run

TRUE = lambda lo, la: 50 + 30 * np.sin((lo - 77.5) * 30) + 20 * np.cos((la - 12.9) * 25)   # noqa: E731


@pytest.fixture(scope="module")
def pts():
    rng = np.random.default_rng(1)
    lon, lat = 77.5 + rng.random(40) * 0.18, 12.9 + rng.random(40) * 0.18
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(a), float(b)]},
                                                       "properties": {"v": float(TRUE(a, b)), "name": f"p{i}"}} for i, (a, b) in enumerate(zip(lon, lat))]}


@pytest.mark.parametrize("method", list(it.METHODS))
def test_every_method_makes_a_surface(pts, method, tmp_path):
    r = it.interpolate(pts, "v", method, tmp_path / f"{method}.tif")
    with rasterio.open(r["path"]) as s:
        a = s.read(1)
        assert s.crs.to_epsg() == 32643 and s.count == (2 if method == "kriging" else 1)
    assert np.isfinite(a).mean() > (0.4 if method in ("natural", "tin") else 0.99)   # natural / TIN: inside the points' hull
    assert r["cv"]["n"] > 0 and r["cv"]["rmse"] < (20 if method == "trend" else 12), (method, r["cv"])
    if method in ("idw", "nearest", "natural", "tin"):   # never beyond the data range
        assert r["min"] >= r["data_min"] - 1e-6 and r["max"] <= r["data_max"] + 1e-6


def test_smooth_methods_beat_nearest(pts):
    rows = {r["method"]: r for r in it.compare(pts, "v")}
    assert rows["spline"]["rmse"] < rows["nearest"]["rmse"] and rows["kriging"]["rmse"] < rows["idw"]["rmse"]
    assert all(r["checked_all"] for k, r in rows.items() if k not in ("natural", "tin"))


def test_cut_to_an_area_and_too_few_points(pts, tmp_path):
    area = {"type": "Polygon", "coordinates": [[[77.55, 12.95], [77.62, 12.95], [77.62, 13.02], [77.55, 13.02], [77.55, 12.95]]]}
    r = it.interpolate(pts, "v", "idw", tmp_path / "cut.tif", area=area, res_m=100)
    with rasterio.open(r["path"]) as s:
        a = s.read(1)
    assert r["res_m"] == 100 and 0.9 < np.isfinite(a).mean() <= 1.0 and a.shape[0] < 90
    two = {"type": "FeatureCollection", "features": pts["features"][:2]}
    with pytest.raises(ValueError, match="needs at least"):
        it.interpolate(two, "v", "kriging", tmp_path / "k.tif")
    with pytest.raises(ValueError, match="No points with a number"):
        it.interpolate(pts, "name", "idw", tmp_path / "x.tif")


def test_api_run_and_compare(client, pts):
    s = ok(client.get("/api/interp/schema"))
    assert set(s["methods"]) == {"idw", "kriging", "spline", "natural", "nearest", "trend", "tin"}
    r = run(client, "/api/interp/run", {"points": pts, "field": "v", "method": "kriging", "params": {"model": "exponential"}, "name": "test surface"})
    assert (ws.root() / r["path"]).is_file() and r["variogram"]["model"] == "exponential"
    c = run(client, "/api/interp/compare", {"points": pts, "field": "v"})
    assert len(c["rows"]) == 7 and c["rows"][0]["rmse"] is not None
