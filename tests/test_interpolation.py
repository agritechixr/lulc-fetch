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


# ---- checks of each method's mathematics (the same results were compared once with PyKrige for kriging)
@pytest.fixture(scope="module")
def xyz():
    rng = np.random.default_rng(7)
    x, y = rng.random(30) * 20000, rng.random(30) * 20000
    return x, y, 50 + 30 * np.sin(x / 4000) + 20 * np.cos(y / 5000) + rng.normal(0, 1, 30), rng


@pytest.mark.parametrize("method,params", [("idw", {}), ("kriging", {"nugget": "zero"}), ("spline", {"smoothing": 0}), ("nearest", {}), ("tin", {})])
def test_exact_at_the_points(xyz, method, params):
    x, y, z, _ = xyz
    assert np.allclose(it.predict(method, x, y, z, x, y, params), z, atol=1e-6)


def test_a_plane_is_reproduced(xyz):
    x, y, _, rng = xyz
    zl = 10 + 0.002 * x - 0.0015 * y
    xq, yq = rng.random(500) * 20000, rng.random(500) * 20000
    truth = 10 + 0.002 * xq - 0.0015 * yq
    for m, p in (("tin", {}), ("trend", {"order": "1"}), ("spline", {"smoothing": 0})):
        v = it.predict(m, x, y, zl, xq, yq, p)
        k = np.isfinite(v)
        assert np.allclose(v[k], truth[k], atol=1e-6), m
    # natural neighbour (discrete Sibson): exact up to the cell size inside, a little less near the hull edge
    c = 50.0
    gx, gy = np.meshgrid(np.arange(0, 20000 + c, c), np.arange(0, 20000 + c, c))
    g = it._natural_grid(x, y, zl, gx, gy, c, {"outside": "empty"})
    e = np.abs(g - (10 + 0.002 * gx - 0.0015 * gy))
    assert np.nanmean(e) < 0.005 * np.ptp(zl) and np.nanmax(e) < 0.08 * np.ptp(zl)


def test_idw_and_nearest_formulas(xyz):
    x, y, z, rng = xyz
    xq, yq = rng.random(50) * 20000, rng.random(50) * 20000
    d = np.hypot(xq[:, None] - x, yq[:, None] - y)
    w = 1 / d ** 2
    assert np.allclose(it.predict("idw", x, y, z, xq, yq, {"power": 2}), (w * z).sum(1) / w.sum(1))
    assert np.array_equal(it.predict("nearest", x, y, z, xq, yq), z[np.argmin(d, 1)])


def test_gaussian_kriging_stays_stable(xyz):
    x, y, z, rng = xyz
    vg = it.fit_variogram(x, y, z, "gaussian", "auto")
    assert vg["nugget"] >= 0.01 * vg["sill"] - 1e-9
    v = it._kriging(x, y, z, rng.random(2000) * 20000, rng.random(2000) * 20000, {"model": "gaussian", "nugget": "auto"}, vg)
    assert v.min() > z.min() - 0.5 * np.ptp(z) and v.max() < z.max() + 0.5 * np.ptp(z)


def test_many_points_use_a_search_neighbourhood(xyz):
    from scipy.spatial.distance import cdist
    _, _, _, rng = xyz
    n = 700
    x, y = rng.random(n) * 20000, rng.random(n) * 20000
    z = 50 + 30 * np.sin(x / 4000) + 20 * np.cos(y / 5000)
    xq, yq = rng.random(300) * 20000, rng.random(300) * 20000
    vg = it.fit_variogram(x, y, z, "spherical", "auto")
    local = it._kriging(x, y, z, xq, yq, {"model": "spherical", "nugget": "auto"}, vg)
    gm = lambda h: it._vmodel("spherical", h, vg["nugget"], vg["sill"], vg["range"])   # noqa: E731
    A = np.ones((n + 1, n + 1)); A[:n, :n] = gm(cdist(np.c_[x, y], np.c_[x, y])); A[n, n] = 0
    full = np.linalg.solve(A, np.vstack([gm(cdist(np.c_[x, y], np.c_[xq, yq])), np.ones((1, len(xq)))]))[:n].T @ z
    assert np.mean(np.abs(local - full)) < 0.01 * np.ptp(z)
