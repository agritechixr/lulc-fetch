"""Hydrology, viewshed and line of sight, LiDAR, pansharpening, spectral unmixing, routing and Sentinel-5P: each on small
synthetic data with a known answer, through its endpoint (Sentinel-5P only with --network)."""

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from tests.helpers import ok, run

TOOL = "Hydrology, visibility, LiDAR, spectral & routing"


def _tif(path, a, transform, crs="EPSG:32643", nodata=None):
    a = a[None] if a.ndim == 2 else a   # bands first
    with rasterio.open(path, "w", driver="GTiff", width=a.shape[2], height=a.shape[1], count=a.shape[0], dtype="float32",
                       crs=crs, transform=transform, nodata=nodata) as d:
        d.write(a.astype("float32"))
    return path


def _lonlat(path, row, col):
    from rasterio.warp import transform as wt
    with rasterio.open(path) as s:
        x, y = rasterio.transform.xy(s.transform, row, col)
        lon, lat = wt(s.crs, "EPSG:4326", [x], [y])
    return [lon[0], lat[0]]


def test_hydrology_streams_order_watersheds(client, home):
    from lulc_fetch import hydrology as H
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    n = 120
    y, x = np.mgrid[0:n, 0:n] * 30.0
    z = 0.02 * (n * 30 - y) + 0.05 * np.abs(x - n * 15) + np.random.default_rng(0).random((n, n))   # a valley draining south
    z[40, 60] -= 25                                                                                 # a pit
    dem = _tif(up / "hy_dem.tif", z, from_origin(700000, 1500000, 30, 30))
    filled, eps = H.fill_sinks(z)
    assert filled[40, 60] > z[40, 60] + 20 and (filled >= z - 1e-9).all()
    code, down = H.flow_direction(eps, 30.0, 30.0)
    assert (down.reshape(n, n)[1:-1, 1:-1] >= 0).all()                                              # every inner cell drains
    r = run(client, "/api/raster/hydrology", {"dem": "uploads/hy_dem.tif", "products": list(H.PRODUCTS), "stream_km2": 0.5})
    assert r["largest_area_km2"] == pytest.approx(n * n * 900 / 1e6, rel=0.01)                      # all of it reaches one outlet
    assert r["max_order"] >= 2 and r["stream_lines"] >= 2 and r["basins"] >= 1
    names = {p.split("/")[-1] for p in r["outputs"]}
    assert {"hy_dem_filled.tif", "hy_dem_flowacc.tif", "hy_dem_streams.geojson", "hy_dem_basins.geojson", "hy_dem_twi.tif"} <= names
    fc = json.loads((home / next(p for p in r["outputs"] if p.endswith("_streams.geojson"))).read_text())
    top = max(f["properties"]["order"] for f in fc["features"])
    assert r["max_order"] - 1 <= top <= r["max_order"]                     # a one-cell link at the DEM's edge has no line
    # the watershed of a point half-way down the valley is about the upper part
    w = run(client, "/api/raster/hydrology", {"dem": "uploads/hy_dem.tif", "products": ["basins"], "stream_km2": 0.5,
                                               "points": [_lonlat(dem, 60, 60)], "snap_m": 120})
    assert 0.3 * r["largest_area_km2"] < w["basin_areas_km2"][0] < 0.7 * r["largest_area_km2"]


def test_viewshed_and_line_of_sight(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    z = np.zeros((300, 300)); z[:, 200:203] = 150                                                    # a 150 m wall
    dem = _tif(up / "vw_dem.tif", z, from_origin(700000, 1500000, 10, 10))
    r = run(client, "/api/raster/viewshed", {"dem": "uploads/vw_dem.tif", "observers": [_lonlat(dem, 150, 100)], "observer_h": 2})
    with rasterio.open(home / r["path"]) as d:
        v = d.read(1)
    assert v[150, 120:201].all() and not v[150, 203:].any()                                          # the wall's face seen, behind it hidden
    two = run(client, "/api/raster/viewshed", {"dem": "uploads/vw_dem.tif", "observers": [_lonlat(dem, 150, 100), _lonlat(dem, 150, 250)], "max_dist_m": 1500})
    with rasterio.open(home / two["path"]) as d:
        c = d.read(1)
    assert c[150, 150] == 1 and c[150, 260] == 1 and c[150, 200] == 1 and c[150, 202] == 1           # each side's own observer
    near = run(client, "/api/raster/viewshed", {"dem": "uploads/vw_dem.tif", "observers": [_lonlat(dem, 150, 100), _lonlat(dem, 150, 50)]})
    with rasterio.open(home / near["path"]) as d:
        assert d.read(1)[150, 150] == 2 and d.read(1)[150, 260] == 0
    los = run(client, "/api/raster/line-of-sight", {"dem": "uploads/vw_dem.tif", "a": _lonlat(dem, 150, 100), "b": _lonlat(dem, 150, 280)})
    assert not los["visible"] and los["first_blocked_m"] == pytest.approx(1030, abs=40)
    assert run(client, "/api/raster/line-of-sight", {"dem": "uploads/vw_dem.tif", "a": _lonlat(dem, 150, 100), "b": _lonlat(dem, 150, 180)})["visible"]


def test_lidar_ground_heights_and_trees(client, home, tmp_path):
    from lulc_fetch import lidar as LD
    rng = np.random.default_rng(0)
    n = 300_000
    x, y = 700000 + rng.random(n) * 150, 1500000 + rng.random(n) * 150
    z = 100 + 0.05 * (x - 700000)
    cls = np.full(n, 2)
    roof = (x > 700040) & (x < 700070) & (y > 1500040) & (y < 1500070)
    z[roof] += 9; cls[roof] = 6
    for tx, ty in [(700110, 1500110), (700120, 1500030)]:
        r = np.hypot(x - tx, y - ty); t = (r < 4) & (rng.random(n) < 0.7); z[t] += 14 * (1 - r[t] / 5); cls[t] = 5
    LD.write_las(tmp_path / "c.las", x, y, z, cls=cls, epsg=32643)
    LD.write_las(tmp_path / "u.las", x, y, z, epsg=32643)                                             # unclassified
    files = ok(client.get("/api/lidar/files", params={"folder": str(tmp_path)}))["files"]
    assert {f["name"] for f in files} == {"c.las", "u.las"}
    info = ok(client.get("/api/lidar/info", params={"path": str(tmp_path / "c.las")}))
    assert info["points"] == n and info["crs"] == "EPSG:32643" and info["classes"]["Building"] == int(roof.sum())
    for f in ("c.las", "u.las"):
        r = run(client, "/api/lidar/grid", {"path": str(tmp_path / f), "products": ["dtm", "chm", "trees"], "res": 1})
        with rasterio.open(home / next(p for p in r["outputs"] if p.endswith("_chm.tif"))) as d:
            chm = d.read(1)
        assert np.median(chm[85:105, 45:65]) == pytest.approx(9, abs=0.3) and np.median(chm[10:30, 10:30]) < 0.3
        if f == "c.las":
            assert r["trees"] == 2 and "class 2" in r["ground"]
    import importlib.util
    if importlib.util.find_spec("laspy") is None:   # .laz without laspy: a clear message, not a crash
        (tmp_path / "x.laz").write_bytes((tmp_path / "c.las").read_bytes())
        with pytest.raises(ValueError, match="laspy"):
            next(LD.Cloud(tmp_path / "x.laz").chunks())


def test_pansharpen_and_unmixing(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    rng = np.random.default_rng(0)
    n = 120
    base = np.kron(rng.random((n // 8, n // 8)), np.ones((8, 8)))
    fine = rng.random((n, n))                                                  # detail every band shares (as in real images)
    truth = np.stack([(0.6 * base + 0.4 * fine) * (i + 1) + 0.1 * i for i in range(3)])
    _tif(up / "ps_pan.tif", truth.mean(0), from_origin(700000, 1500000, 15, 15))
    _tif(up / "ps_ms.tif", truth.reshape(3, n // 2, 2, n // 2, 2).mean((2, 4)), from_origin(700000, 1500000, 30, 30))
    plain = np.mean(np.abs(np.kron(truth.reshape(3, n // 2, 2, n // 2, 2).mean((2, 4)), np.ones((1, 2, 2))) - truth))
    for m in ("gsa", "brovey", "ihs"):
        r = run(client, "/api/raster/pansharpen", {"image": "uploads/ps_ms.tif", "pan": "uploads/ps_pan.tif", "method": m})
        with rasterio.open(home / r["path"]) as d:
            assert d.res == (15, 15) and np.nanmean(np.abs(d.read() - truth)) < 0.75 * plain, m
    # unmixing: three materials with known fractions
    E = np.array([[0.05, 0.08, 0.04, 0.5, 0.3], [0.2, 0.25, 0.3, 0.35, 0.4], [0.06, 0.05, 0.03, 0.02, 0.01]])
    A = rng.dirichlet([1, 1, 1], size=80 * 80)
    X = (A @ E + rng.normal(0, 0.002, (6400, 5))).T.reshape(5, 80, 80)
    _tif(up / "um.tif", X, from_origin(77, 13, 1e-4, 1e-4), crs="EPSG:4326")
    pure = [int(np.argmax(A[:, j])) for j in range(3)]
    pts = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"m": nm}, "geometry": {"type": "Point",
           "coordinates": [77 + (i % 80 + .5) * 1e-4, 13 - (i // 80 + .5) * 1e-4]}} for nm, i in zip(["veg", "soil", "water"], pure)]}
    r = run(client, "/api/raster/unmix", {"path": "uploads/um.tif", "layer": pts, "field": "m"})
    assert r["endmembers"] == ["soil", "veg", "water"] and r["csv"].startswith("tables/")
    with rasterio.open(home / r["path"]) as d:
        fr = d.read()
    assert np.mean(np.abs(fr[:3] - A.T.reshape(3, 80, 80)[[1, 0, 2]])) < 0.02 and np.nanmax(np.abs(fr[:3].sum(0) - 1)) < 1e-4
    auto = run(client, "/api/raster/unmix", {"path": "uploads/um.tif", "n_auto": 3})
    assert auto["mean_rmse"] < 0.005


def test_routing_on_a_road_layer(client):
    lines = []
    for i in range(6):   # a grid of streets every 0.01° (about 1.1 km); the 4th row is a fast road
        lines.append({"type": "Feature", "properties": {"kmh": 80 if i == 3 else 20}, "geometry": {"type": "LineString", "coordinates": [[77 + 0.01 * j, 13 + 0.01 * i] for j in range(6)]}})
        lines.append({"type": "Feature", "properties": {"kmh": 20}, "geometry": {"type": "LineString", "coordinates": [[77 + 0.01 * i, 13 + 0.01 * j] for j in range(6)]}})
    roads = {"type": "FeatureCollection", "features": lines}
    r = run(client, "/api/network/routing", {"op": "route", "points": [[77.0, 13.02], [77.05, 13.02]], "roads": roads, "speed_field": "kmh"})
    assert r["minutes"] == pytest.approx(10.7, abs=0.3) and r["roads"] == "your layer"                 # the detour on the fast road
    s = run(client, "/api/network/routing", {"op": "service", "points": [[77.02, 13.03]], "breaks": [3, 6], "roads": roads, "speed_field": "kmh"})
    assert 0 < s["areas_km2"]["3.0"] < s["areas_km2"]["6.0"]
    c = run(client, "/api/network/routing", {"op": "closest", "points": [[77.0, 13.0], [77.05, 13.05]], "facilities": [[77.0, 13.01], [77.05, 13.04]],
                                              "facility_names": ["A", "B"], "roads": roads, "speed_field": "kmh"})
    assert c["reached"] == 2 and c["max_minutes"] == pytest.approx(3.3, abs=0.2)
    assert client.post("/api/network/routing", json={"op": "route", "points": [[77, 13]], "roads": roads}).status_code == 400


@pytest.mark.network
def test_sentinel5p_no2(client, home):
    r = run(client, "/api/s5p/average", {"product": "no2", "bbox": [77.4, 12.8, 77.8, 13.2], "start": "2026-09-01", "end": "2026-09-02"})
    assert r["overpasses"] >= 1 and 1 < r["mean"] < 500 and r["csv"].startswith("tables/")


def test_time_slider_animation(client):
    import base64
    import io

    from PIL import Image
    def png(color):
        b = io.BytesIO()
        Image.new("RGBA", (40, 30), color).save(b, "PNG")
        return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    frames = [{"image": png(c), "bounds": [[13.0, 77.0], [13.3, 77.4]], "label": f"2026-0{k + 1}-01"} for k, c in enumerate([(255, 0, 0, 255), (0, 128, 0, 255), (0, 0, 255, 255)])]
    for fmt in ("gif", "mp4"):
        r = run(client, "/api/view/animation", {"frames": frames, "fps": 2, "format": fmt, "width": 400})
        assert r["frames"] == 3 and r["file"].endswith("." + fmt) and r["size"][0] == 400
        got = client.get(r["url"])
        assert got.status_code == 200 and len(got.content) > 500
    gif = Image.open(io.BytesIO(client.get(run(client, "/api/view/animation", {"frames": frames, "format": "gif"})["url"]).content))
    assert gif.n_frames == 3
