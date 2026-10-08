"""Mosaic / merge rasters, burn severity (dNBR) and spatial statistics (kernel density, Gi* hot spots, Moran's I, nearest
neighbour), through the app's endpoints, on small synthetic data whose answers are known."""

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from lulc_fetch import mosaic, spatial_stats
from tests.helpers import ok, run

TOOL = "Mosaic, burn severity & spatial statistics"
H, W = 300, 500
yy, xx = np.mgrid[0:H, 0:W]
SCENE = np.stack([3000 + 800 * np.sin(xx / 30) + 600 * np.cos(yy / 25), 2500 + 500 * np.sin((xx + yy) / 40), 2000 + 300 * np.cos(xx / 15)])


def tile(path, c0, c1, gain=1.0, off=0.0, crs="EPSG:32643", seed=0):
    rng = np.random.default_rng(seed)
    a = (SCENE[:, :, c0:c1] * gain + off + rng.normal(0, 10, (3, H, c1 - c0))).astype("uint16")
    prof = dict(driver="GTiff", width=c1 - c0, height=H, count=3, dtype="uint16", crs=crs, nodata=0,
                transform=from_origin(500000 + c0 * 10, 1400000, 10, 10))
    with rasterio.open(path, "w", **prof) as d:
        d.write(a)
        for i, b in enumerate(("B04", "B03", "B02")):
            d.set_band_description(i + 1, b)
    return path


def seam(path, col=300):
    with rasterio.open(path) as d:
        a = d.read(1).astype(float)
    return float(np.abs(a[:, col] - a[:, col - 1]).mean())


# ------------------------------------------------------------------ mosaic
def test_mosaic_blends_without_seams(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    tile(up / "mo_west.tif", 0, 300)
    tile(up / "mo_east.tif", 200, 500, gain=1.25, off=300, seed=1)   # another date: brighter
    smooth = run(client, "/api/raster/mosaic", {"rasters": ["uploads/mo_west.tif", "uploads/mo_east.tif"], "name": "joined"})
    hard = run(client, "/api/raster/mosaic", {"rasters": ["uploads/mo_west.tif", "uploads/mo_east.tif"], "method": "first", "balance": "none"})
    assert (smooth["width"], smooth["height"], smooth["covered_pct"]) == (500, 300, 100.0) and smooth["method"] == "blend"
    assert abs(smooth["gains"][1][0] - 0.8) < 0.02   # the brighter scene was matched to the first
    truth = float(np.abs(SCENE[0, :, 300] - SCENE[0, :, 299]).mean())
    assert seam(home / smooth["path"]) < truth + 30 and seam(home / hard["path"]) > 500   # no visible edge where the west tile ends
    with rasterio.open(home / smooth["path"]) as d:
        assert d.count == 3 and d.dtypes[0] == "uint16" and d.descriptions[0] == "B04" and d.crs.to_epsg() == 32643


def test_mosaic_other_crs_and_classes(tmp_path):
    a = tile(tmp_path / "a.tif", 0, 300)
    b = tile(tmp_path / "b_wgs.tif", 0, 300, crs="EPSG:32643")
    with rasterio.open(b) as s:   # the same data in longitude / latitude
        from rasterio.warp import calculate_default_transform, reproject
        t, w, h = calculate_default_transform(s.crs, "EPSG:4326", s.width, s.height, *s.bounds)
        prof = {**s.profile, "crs": "EPSG:4326", "transform": t, "width": w, "height": h}
        with rasterio.open(tmp_path / "b4326.tif", "w", **prof) as d:
            for i in range(1, 4):
                reproject(rasterio.band(s, i), rasterio.band(d, i))
    r = mosaic.mosaic([a, tmp_path / "b4326.tif"], tmp_path / "m.tif", method="median")
    assert r["crs"] == "EPSG:32643" and r["covered_pct"] > 95
    cls = dict(driver="GTiff", width=40, height=40, count=1, dtype="uint8", crs="EPSG:32643", nodata=0)
    for i, (x0, v) in enumerate([(500000, 1), (500200, 2), (500100, 3)]):
        with rasterio.open(tmp_path / f"c{i}.tif", "w", **cls, transform=from_origin(x0, 1400000, 10, 10)) as d:
            d.write(np.full((40, 40), v, "uint8"), 1)
    r = mosaic.mosaic([tmp_path / f"c{i}.tif" for i in range(3)], tmp_path / "cm.tif", method="mode", categorical=True)
    with rasterio.open(r["path"]) as d:
        assert set(np.unique(d.read(1))) <= {0, 1, 2, 3} and d.read(1)[20, 5] == 1
    with pytest.raises(ValueError, match="Class maps"):
        mosaic.mosaic([tmp_path / "c0.tif", tmp_path / "c1.tif"], tmp_path / "x.tif", method="blend", categorical=True)


# ------------------------------------------------------------------ burn severity
def s2(path, nir, swir):
    prof = dict(driver="GTiff", width=60, height=60, count=2, dtype="uint16", crs="EPSG:32643", transform=from_origin(600000, 1500000, 20, 20))
    with rasterio.open(path, "w", **prof) as d:
        d.write(np.stack([nir, swir]).astype("uint16"))
        d.set_band_description(1, "B08")
        d.set_band_description(2, "B12")


def test_burn_severity_stubble(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    nir, swir = np.full((60, 60), 3500.0), np.full((60, 60), 1000.0)   # crop before: NBR 0.56
    s2(up / "burn_pre.tif", nir, swir)
    nir2, swir2 = nir.copy(), swir.copy()
    nir2[:, :20], swir2[:, :20] = 1200, 1800   # burned: NBR −0.2 (dNBR 0.76, high severity)
    nir2[:, 20:40], swir2[:, 20:40] = 2600, 1700   # only harvested: NBR 0.21 (dNBR 0.35 looks "moderate")
    s2(up / "burn_post.tif", nir2, swir2)
    body = {"before": "uploads/burn_pre.tif", "after": "uploads/burn_post.tif"}
    plain = run(client, "/api/raster/burn", body)
    stubble = run(client, "/api/raster/burn", {**body, "min_post_nbr": 0.1})
    ha = 60 * 20 * 0.04   # 20 columns × 60 rows of 20 m pixels
    assert abs(plain["summary"]["burned_ha"] - 2 * ha) < 0.01        # the harvest counted as burned …
    assert abs(stubble["summary"]["burned_ha"] - ha) < 0.01          # … but not when only charred land counts
    high = next(c for c in stubble["classes"] if c["class"] == "High severity")
    assert abs(high["area_ha"] - ha) < 0.01 and stubble["summary"]["pre"].startswith("computed from NIR")
    sev = home / stubble["outputs"][-1]   # [dNBR, severity]
    with rasterio.open(sev) as d:
        assert d.read(1)[30, 5] == 7 and d.read(1)[30, 30] == 3 and json.loads(d.tags()["classes"])["7"] == "High severity"
    assert client.post("/api/raster/burn", json={**body, "breaks": [1, 2]}).status_code == 422


# ------------------------------------------------------------------ spatial statistics
def points(xy, props=None):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
                                                       "properties": (props[i] if props else {})} for i, (x, y) in enumerate(xy)]}


def test_spatial_statistics(client, home):
    rng = np.random.default_rng(3)
    background = np.column_stack([77.5 + rng.uniform(0, 0.2, 120), 12.9 + rng.uniform(0, 0.2, 120)])
    outbreak = np.column_stack([77.55 + rng.normal(0, 0.004, 80), 12.95 + rng.normal(0, 0.004, 80)])   # reports bunched in one village
    reports = points(np.vstack([background, outbreak]))
    d = run(client, "/api/vector/spatial-stats", {"method": "density", "layer": reports})
    assert d["points"] == 200 and d["path"].endswith(".tif") and d["bandwidth_m"] > 0
    with rasterio.open(home / d["path"]) as t:   # the map adds up to the points, and peaks at the outbreak
        dens = t.read(1).astype(float)
        assert abs(dens.sum() * abs(t.transform.a * t.transform.e) / 1e6 - 200) < 6
        r, c = np.unravel_index(dens.argmax(), dens.shape)
        from rasterio.warp import transform as tr
        lon, lat = tr(t.crs, "EPSG:4326", [t.xy(r, c)[0]], [t.xy(r, c)[1]])
        assert abs(lon[0] - 77.55) < 0.01 and abs(lat[0] - 12.95) < 0.01
    h = run(client, "/api/vector/spatial-stats", {"method": "hotspots", "layer": reports})
    fc = ok(client.get("/api/vector/read", params={"path": h["path"]}))
    hot = [f for f in fc["features"] if f["properties"]["gi_bin"] == 3]
    assert h["grid_cell_m"] and hot and all(abs(np.mean([c[0] for c in f["geometry"]["coordinates"][0]]) - 77.55) < 0.02 for f in hot)
    nn = run(client, "/api/vector/spatial-stats", {"method": "nearest", "layer": reports})
    assert nn["pattern"] == "clustered" and nn["R"] < 1 and nn["p"] < 0.001
    grid = points([(77.5 + i * 0.01, 12.9 + j * 0.01) for i in range(12) for j in range(12)])   # evenly spaced
    assert run(client, "/api/vector/spatial-stats", {"method": "nearest", "layer": grid})["pattern"] == "dispersed"
    vals = [{"v": float(10 * np.exp(-((x - 77.55) ** 2 + (y - 12.95) ** 2) / 0.002) + rng.normal(0, 1))} for x, y in background]
    m = run(client, "/api/vector/spatial-stats", {"method": "moran", "layer": points(background, vals), "field": "v", "k": 8})
    assert m["pattern"] == "clustered" and m["I"] > 0.3 and m["p_permutation"] <= 0.01 and m["counts"]["High-high cluster"] > 0
    assert client.post("/api/vector/spatial-stats", json={"method": "moran", "layer": reports}).status_code == 400


def test_gi_star_formula():
    """Gi* by hand for one feature: binary weights within the band, itself included."""
    xy = np.array([[0, 0], [1, 0], [2, 0], [10, 0], [11, 0], [12, 0], [20, 0], [21, 0]], float)
    x = np.array([9, 8, 9, 1, 2, 1, 5, 5], float)
    lists, _ = spatial_stats._neighbours(xy, 1.5, self_too=True)
    n, xbar, s = len(x), x.mean(), x.std()
    js = lists[1]
    want = (x[js].sum() - xbar * len(js)) / (s * np.sqrt((n * len(js) - len(js) ** 2) / (n - 1)))
    fc = points(xy / 1e5 + [77, 12], [{"v": float(v)} for v in x])
    got = spatial_stats.hot_spots(fc, "v", distance_m=1.5 * 1.1055, fdr=False)["geojson"]["features"][1]["properties"]["gi_z"]
    assert abs(got - want) < 0.05
