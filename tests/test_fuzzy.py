"""Fuzzy & suitability: membership functions, fuzzy distance, fuzzy overlay (every operator), fuzzy boundaries and
uncertainty, fuzzy c-means. Small synthetic layers whose answers are known."""

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from lulc_fetch import fuzzy
from tests.helpers import ok, run

TOOL = "Fuzzy & suitability"
PROF = dict(driver="GTiff", width=200, height=150, count=1, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1400000, 10, 10))
YY, XX = np.mgrid[0:150, 0:200]


def raster(path, a, **kw):
    with rasterio.open(path, "w", **{**PROF, **kw}) as d:
        d.write(a.astype("float32"), 1)
    return path


def test_membership_functions():
    d = np.array([0, 100, 250, 500, 750, 1000, 1500.])
    near_road = fuzzy.membership(d, "sigmoid", mid=500, slope=-0.01)
    assert near_road[3] == pytest.approx(0.5) and near_road[0] > 0.99 and near_road[-1] < 0.01 and np.all(np.diff(near_road) < 0)
    assert fuzzy.membership(d, "gaussian", mid=0, sigma=400)[3] == pytest.approx(np.exp(-500 ** 2 / (2 * 400 ** 2)))
    assert list(fuzzy.membership(np.array([-1, 0, 5, 10]), "linear", a=0, b=10)) == [0, 0, 0.5, 1]
    assert list(fuzzy.membership(np.array([10, 5, 0]), "linear", a=10, b=0)) == [0, 0.5, 1]   # decreasing
    large = fuzzy.membership(np.array([-0.2, 0.5, 0.75]), "large", mid=0.5, spread=5)
    assert large[0] == 0 and large[1] == pytest.approx(0.5) and large[2] > 0.85
    assert fuzzy.membership(np.array([8.0]), "small", mid=8, spread=4)[0] == pytest.approx(0.5)
    assert list(fuzzy.membership(np.array([4, 5.5, 6.5, 8, 9]), "trapezoid", a=5, b=6, c=7, d=9)) == [0, 0.5, 1, 0.5, 0]
    assert np.isnan(fuzzy.membership(np.array([np.nan]), "large", mid=1, spread=5)[0])
    for bad in (dict(fn="linear", a=1, b=1), dict(fn="gaussian", mid=0, sigma=0), dict(fn="large", mid=-1, spread=5), dict(fn="nope")):
        with pytest.raises(ValueError):
            fuzzy.membership([1.0], **bad)


def test_operators():
    a, b = np.array([[0.9, 0.2]]), np.array([[0.8, 0.9]])
    r = {op: fuzzy.combine([a, b], op, [0.6, 0.4], 0.9)[0] for op in fuzzy.OPERATORS}
    assert list(r["and"]) == [0.8, 0.2] and list(r["or"]) == [0.9, 0.9]
    assert r["product"] == pytest.approx([0.72, 0.18]) and r["sum"] == pytest.approx([0.98, 0.92])
    assert r["gamma"][0] == pytest.approx(0.98 ** 0.9 * 0.72 ** 0.1)
    assert r["weighted_sum"] == pytest.approx([0.86, 0.48]) and r["weighted_product"][1] == pytest.approx(0.2 ** 0.6 * 0.9 ** 0.4)
    assert np.isnan(fuzzy.combine([np.array([[np.nan]]), np.array([[0.5]])], "or")[0, 0])   # a layer without data: no answer


def test_overlay_route(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    raster(up / "fz_slope.tif", XX / 10)              # 0–20°: low on the west
    raster(up / "fz_ndvi.tif", 0.1 + YY / 200)        # 0.1–0.85: high in the south
    r = run(client, "/api/fuzzy/overlay", {"layers": [
        {"path": "uploads/fz_slope.tif", "fn": "small", "params": {"mid": 8, "spread": 4}, "weight": 1, "name": "slope"},
        {"path": "uploads/fz_ndvi.tif", "fn": "large", "params": {"mid": 0.4, "spread": 5}, "weight": 1, "name": "NDVI"}], "op": "gamma", "gamma": 0.8})
    with rasterio.open(home / r["path"]) as d:
        s = d.read(1)
        assert 0 <= s.min() and s.max() <= 1 and s[-1, 0] > 0.9 and s[0, -1] < 0.1   # flat and green: suitable; steep and bare: not
        assert json.loads(d.tags()["layers"])[0]["name"] == "slope"
    assert len(r["classes"]) == 5 and abs(sum(c["pct"] for c in r["classes"]) - 100) < 0.1 and r["outputs"][0].endswith("_classes.tif")
    bad = client.post("/api/fuzzy/overlay", json={"layers": [{"path": "uploads/fz_slope.tif", "fn": "linear", "params": {"a": 1, "b": 1}}]})
    assert bad.status_code == 400 and "Linear" in bad.json()["detail"]


def test_fuzzy_distance_and_membership_route(client, home):
    road = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
            "geometry": {"type": "LineString", "coordinates": [[76.85, 12.65], [76.86, 12.66]]}}]}
    r = run(client, "/api/fuzzy/membership", {"layer": road, "fn": "sigmoid", "params": {"mid": 500, "slope": -0.01}, "res": 20, "name": "near_road"})
    with rasterio.open(home / r["path"]) as m, rasterio.open(home / r["distance_path"]) as dist:
        mu, dm = m.read(1), dist.read(1)
        assert dm.min() == 0 and mu.max() > 0.99
        at500 = mu[np.abs(dm - 500) < 15]
        assert at500.size and abs(float(at500.mean()) - 0.5) < 0.05   # half way at 500 m, as asked
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    raster(up / "fz_ph.tif", 4 + XX / 25)   # soil pH 4–12
    g = run(client, "/api/fuzzy/membership", {"raster": "uploads/fz_ph.tif", "fn": "gaussian", "params": {"mid": 6.5, "sigma": 1}})
    with rasterio.open(home / g["path"]) as d:
        row = d.read(1)[0]
        assert abs(XX[0, int(np.argmax(row))] / 25 + 4 - 6.5) < 0.05   # best at pH 6.5


def test_boundary_and_uncertainty(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    raster(up / "fz_flood.tif", 1 / (1 + np.exp((XX - 100) / 12)))   # flood-prone in the west, a gradual edge at column 100
    r = run(client, "/api/fuzzy/boundary", {"raster": "uploads/fz_flood.tif", "alpha": 0.5, "cuts": [0.25, 0.5, 0.75]})
    with rasterio.open(home / r["uncertainty"]) as d:
        u = d.read(1)
        assert u[75, 100] > 0.95 and u[75, 0] < 0.01   # most uncertain on the edge, certain far from it
    zones = ok(client.get("/api/vector/read", params={"path": r["zones"]}))["features"]
    assert sorted({f["properties"]["alpha"] for f in zones}) == [0.25, 0.5, 0.75]
    areas = {f["properties"]["alpha"]: f["properties"]["area_ha"] for f in zones}
    assert areas[0.25] > areas[0.5] > areas[0.75]   # nested: a lower cut covers more
    assert abs(areas[0.5] - 150 * 100 * 0.01) / (150 * 100 * 0.01) < 0.05   # the crisp zone: the 100 western columns (150 ha)
    trans = ok(client.get("/api/vector/read", params={"path": r["transition"]}))["features"]
    assert len(trans) == 1 and trans[0]["properties"]["class"] == "Transition zone" and r["transition_ha"] > 0
    raster(up / "fz_notmember.tif", XX * 3.0)
    assert client.post("/api/fuzzy/boundary", json={"raster": "uploads/fz_notmember.tif"}).status_code == 200   # a job: fails inside
    with pytest.raises(ValueError, match="0–1"):
        fuzzy.boundary(up / "fz_notmember.tif", home / "analysis" / "x")


def test_chaikin_smooths():
    sq = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    sm = fuzzy.chaikin(sq, 2)
    assert len(sm) > len(sq) and sm[0] == sm[-1] and all(0 <= x <= 10 and 0 <= y <= 10 for x, y in sm)
    assert [2.5, 0] in [[round(x, 6), round(y, 6)] for x, y in fuzzy.chaikin(sq, 1)]


def test_cmeans(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    rng = np.random.default_rng(0)
    img = np.zeros((3, 100, 120), "float32")
    for k, (c0, c1) in enumerate([(0, 40), (40, 80), (80, 120)]):
        img[:, :, c0:c1] = np.array([[0.1, 0.3, 0.6][k], [0.5, 0.2, 0.4][k], [0.2, 0.6, 0.1][k]])[:, None, None]
    img += rng.normal(0, 0.02, img.shape)
    img[:, :, 38:42] = (img[:, :, 30:34] + img[:, :, 45:49]) / 2   # a strip of mixed pixels between classes 1 and 2
    with rasterio.open(up / "fz_img.tif", "w", **{**PROF, "count": 3, "width": 120, "height": 100}) as d:
        d.write(img)
    r = run(client, "/api/fuzzy/cmeans", {"raster": "uploads/fz_img.tif", "k": 3})
    assert sorted(round(c["pct"]) for c in r["classes"]) == [33, 33, 33]
    hard, mem, unc = (home / p for p in r["outputs"])   # classes, memberships, uncertainty
    with rasterio.open(mem) as m, rasterio.open(unc) as u:
        mm, uu = m.read(), u.read(1)
        assert m.count == 3 and np.allclose(mm[:, 50, 10].sum(), 1, atol=1e-4)
        assert uu[:, 39:41].mean() > 0.6 and uu[:, 5:30].mean() < 0.2   # the mixed strip is uncertain
    with rasterio.open(hard) as h:
        assert set(np.unique(h.read(1))) == {1, 2, 3} and h.colormap(1)[1]
