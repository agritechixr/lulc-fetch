"""Analysis ▸ Hydrology on synthetic landscapes with known answers: DEM preparation (holes, breaching, burning), D8 /
D-infinity / MFD flow, the drainage network (Strahler, Shreve, topology), nested watersheds and sub-watersheds, terrain
indicators (curvature, TWI, HAND), SCS-CN runoff, flood from HAND and flood susceptibility."""

import json
import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from tests.helpers import ok, run

TOOL = "Hydrology & watersheds"
N = 160


def _dem(path, z, res=30.0, crs="EPSG:32643"):
    with rasterio.open(path, "w", driver="GTiff", width=z.shape[1], height=z.shape[0], count=1, dtype="float32", crs=crs,
                       transform=from_origin(700000, 1500000, res, res), nodata=-9999) as d:
        d.write(np.where(np.isfinite(z), z, -9999).astype("float32"), 1)
    return path


def valley(n=N, seed=0):
    """A main valley draining south with side valleys, a pit and an embankment across the valley."""
    y, x = np.mgrid[0:n, 0:n] * 30.0
    z = 0.03 * (n * 30 - y) + 0.06 * np.abs(x - n * 15) + 8 * np.abs(np.sin(y / 1500 * np.pi)) * (np.abs(x - n * 15) > 600)
    z = z + np.random.default_rng(seed).random((n, n))
    z[50, n // 2] -= 30
    z[120, n // 2 - 20:n // 2 + 21] += 6
    return z


def _lonlat(home, rel, row, col):
    from rasterio.warp import transform as wt
    with rasterio.open(home / rel) as s:
        x, y = rasterio.transform.xy(s.transform, row, col)
        lon, lat = wt(s.crs, "EPSG:4326", [x], [y])
    return [lon[0], lat[0]]


@pytest.fixture
def dem(home):
    (home / "uploads").mkdir(exist_ok=True)
    _dem(home / "uploads/hv_dem.tif", valley())
    return "uploads/hv_dem.tif"


def test_dem_preparation(client, home, dem):
    from lulc_fetch.hydro import conditioning as C
    z = valley()
    holed = z.copy(); holed[80:84, 40:44] = np.nan                                       # a void inside
    filled, n = C.fill_nodata(holed)
    assert n == 16 and np.isfinite(filled).all() and abs(filled[81, 41] - z[81, 41]) < 5
    br, carved, nfill = C.breach(z)
    from lulc_fetch import hydrology as H
    assert carved > 0 and nfill == 0 and (H.fill_sinks(br)[0] - br).max() < 1e-6        # nothing left undrained
    assert (br <= z + 1e-9).all()                                                        # breaching only cuts
    r = run(client, "/api/hydro/condition", {"dem": dem, "steps": ["fill_nodata", "breach"]})
    assert r["cells_still_in_pits"] == 0 and r["max_cut_m"] > 5 and r["raised_cells"] == 0
    with rasterio.open(home / r["path"]) as d:
        assert d.tags()["hydro_conditioned"] == "1"
    # limited breaching falls back to filling; burning a line lowers it
    lim = run(client, "/api/hydro/condition", {"dem": dem, "steps": ["breach", "fill"], "max_breach_m": 1})
    assert lim["raised_cells"] > 0 and lim["cells_still_in_pits"] == 0
    line = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "LineString",
            "coordinates": [_lonlat(home, dem, 10, 20), _lonlat(home, dem, 150, 20)]}}]}
    b = run(client, "/api/hydro/condition", {"dem": dem, "steps": [], "streams": line, "burn_m": 4})
    assert b["burned_cells"] > 100 and b["max_cut_m"] == pytest.approx(4, abs=0.01)


def test_flow_methods(client, home, dem):
    from lulc_fetch.hydro import flow as F
    from lulc_fetch.hydro.common import Grid
    # D-infinity on tilted planes: the angle of steepest descent (counter-clockwise from east)
    yy, xx = np.mgrid[0:20, 0:20] * 10.0
    g = Grid({}, None, None, 20, 20, np.full((20, 1), 10.0), 10.0, np.ones((20, 20), bool))
    for deg in (0, 30, 45, 90, 200, 300):                                              # a plane falling towards deg
        t = math.radians(deg)
        z = -(math.cos(t) * xx + math.sin(t) * (190 - yy))                              # x east, (190 − y) north
        _, _, ang = F.receivers_dinf(z, g)
        assert np.degrees(ang[10, 10]) == pytest.approx(deg, abs=0.5), deg
    areas = {}
    for m in ("d8", "dinf", "mfd"):
        r = run(client, "/api/hydro/flow", {"dem": dem, "method": m})
        areas[m] = r["largest_area_km2"]
        assert len(r["outputs"]) == (4 if m != "mfd" else 3)
    total = N * N * 900 / 1e6
    assert all(a == pytest.approx(total, rel=0.01) for a in areas.values())             # all the water reaches the outlet
    with rasterio.open(home / next(p for p in run(client, "/api/hydro/flow", {"dem": dem, "method": "mfd", "outputs": ["sca"]})["outputs"])) as d:
        sca = d.read(1)
    assert sca[150, N // 2] > 20 * np.median(sca)                                        # the valley floor gathers water


def test_network_and_watersheds(client, home, dem):
    r = run(client, "/api/hydro/network", {"dem": dem, "stream_km2": 0.3, "outputs": ["lines", "nodes", "order", "magnitude", "links"]})
    assert r["max_shreve"] == r["sources"] and r["outlets"] == 1 and r["max_strahler"] >= 2
    assert r["drainage_density_km_per_km2"] == pytest.approx(r["stream_length_km"] / r["area_km2"], rel=1e-3)
    links = json.loads((home / next(p for p in r["outputs"] if p.endswith("_stream_links.geojson"))).read_text())["features"]
    ids = {f["properties"]["link"] for f in links}
    assert all(f["properties"]["downstream_link"] in ids for f in links if f["properties"]["downstream_link"])  # topology closes
    assert sum(1 for f in links if f["properties"]["downstream_link"] is None) == 1                         # one outlet link
    assert r["csv"].startswith("tables/")
    # two outlets, one upstream of the other: nested watersheds that add up to the lower one's catchment
    pts = [_lonlat(home, dem, N - 3, N // 2), _lonlat(home, dem, 80, N // 2)]
    w = run(client, "/api/hydro/watershed", {"dem": dem, "mode": "points", "points": pts, "snap_m": 90})
    fc = json.loads((home / next(p for p in w["outputs"] if p.endswith("_watersheds.geojson"))).read_text())["features"]
    by = {f["properties"]["basin"]: f["properties"] for f in fc}
    assert by[2]["parent"] == 1 and by[1]["parent"] is None and w["total_area_km2"] == pytest.approx(N * N * 900 / 1e6, rel=0.02)
    assert by[1]["time_of_concentration_min"] > 0 and by[1]["compactness"] >= 1
    sub = run(client, "/api/hydro/watershed", {"dem": dem, "mode": "subbasins", "stream_km2": 1})
    assert sub["basins"] >= 3 and sub["total_area_km2"] == pytest.approx(N * N * 900 / 1e6, rel=0.02)
    inside = run(client, "/api/hydro/watershed", {"dem": dem, "mode": "subbasins", "stream_km2": 1, "within_points": True, "points": [pts[1]], "snap_m": 90})
    assert inside["total_area_km2"] < 0.7 * sub["total_area_km2"]


def test_terrain_indicators(client, home, dem):
    from lulc_fetch.hydro import terrain as T
    from lulc_fetch.hydro.common import Grid
    yy, xx = np.mgrid[-20:21, -20:21] * 10.0
    g = Grid({}, None, None, 41, 41, np.full((41, 1), 10.0), 10.0, np.ones((41, 41), bool))
    prof, plan, total = T.curvatures(g, (xx ** 2 + yy ** 2) / 100)                      # a bowl
    _, _, hill = T.curvatures(g, -(xx ** 2 + yy ** 2) / 100)                             # a hill
    assert total[20, 20] < 0 < hill[20, 20]                                              # ArcGIS's sign: + convex (a hilltop)
    assert np.isfinite(prof).all() and np.isfinite(plan).all()
    pts = [_lonlat(home, dem, 10, 10)]
    r = run(client, "/api/hydro/terrain", {"dem": dem, "products": list(T.PRODUCTS), "points": pts, "stream_km2": 0.5})
    assert r["flow_paths"] == 1 and r["depressions"] >= 1                                # the pit (DEM not conditioned)
    out = {p.split("_")[-1].split(".")[0]: home / p for p in r["outputs"] if p.endswith(".tif")}
    with rasterio.open(out["hand"]) as d:
        hand = d.read(1)
    with rasterio.open(out["twi"]) as d:
        twi = d.read(1)
    assert hand[150, N // 2] < 2 and hand[150, 5] > 20                                   # valley floor vs hillside
    assert twi[150, N // 2] > twi[150, 5] + 3
    path = json.loads((home / next(p for p in r["outputs"] if p.endswith("_flow_paths.geojson"))).read_text())["features"][0]
    assert path["properties"]["drop_m"] > 0 and path["properties"]["length_km"] > 1


def test_runoff_flood_and_susceptibility(client, home, dem):
    from lulc_fetch.hydro import runoff as R
    assert float(R.scs_runoff(np.array([100.0]), np.array([78.0]))[0]) == pytest.approx(46.6, abs=0.1)   # TR-55 by hand
    assert float(R.scs_runoff(np.array([10.0]), np.array([60.0]))[0]) == 0                             # below the initial abstraction
    with rasterio.open(home / dem) as s:
        prof = s.profile
    lc = np.full((N, N), 40, "float32")                                                    # all cropland (WorldCover 40)
    with rasterio.open(home / "uploads/hv_lc.tif", "w", **{**prof, "dtype": "float32"}) as d:
        d.write(lc, 1)
    r = run(client, "/api/hydro/runoff", {"dem": dem, "landcover": "uploads/hv_lc.tif", "soil": "B", "rain_mm": 100,
                                           "points": [_lonlat(home, dem, N - 3, N // 2)], "snap_m": 90})
    assert r["mean_cn"] == 78 and r["mean_runoff_mm"] == pytest.approx(46.6, abs=0.1)
    w = r["watersheds"][0]
    assert w["runoff_m3"] == pytest.approx(46.6 / 1000 * w["area_km2"] * 1e6, rel=0.01) and w["peak_m3s"] > 0
    wet = run(client, "/api/hydro/runoff", {"dem": dem, "landcover": "uploads/hv_lc.tif", "soil": "B", "rain_mm": 100, "condition": "III"})
    assert wet["mean_runoff_mm"] > r["mean_runoff_mm"]
    f = run(client, "/api/hydro/hand-flood", {"dem": dem, "levels_m": [1, 3, 8], "stream_km2": 1})
    a = [lv["flooded_km2"] for lv in f["levels"]]
    assert 0 < a[0] < a[1] < a[2]
    # susceptibility: floods where HAND is low; the model should find it and say so
    t = run(client, "/api/hydro/terrain", {"dem": dem, "products": ["hand", "slope"], "stream_km2": 1})
    hand_p = next(p for p in t["outputs"] if p.endswith("_hand.tif"))
    with rasterio.open(home / hand_p) as d:
        hnd = d.read(1)
    rows, cols = np.nonzero((hnd >= 0) & (hnd < 2))
    sel = np.random.default_rng(0).choice(rows.size, min(150, rows.size), replace=False)
    pts = [_lonlat(home, hand_p, int(rows[i]), int(cols[i])) for i in sel]
    floods = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": p}} for p in pts]}
    s = run(client, "/api/hydro/susceptibility", {"predictors": t["outputs"], "floods": floods, "block_m": 1500, "buffer_m": 300})
    assert s["auc_spatial"] > 0.8 and next(iter(s["importance"])).lower().startswith(("hand", "height"))
    with rasterio.open(home / s["outputs"][1]) as d:
        assert json.loads(d.tags()["classes"])["5"] == "Very high"


def test_hydrology_menu(client):
    html = client.get("/").text
    assert 'data-an="hydro"' in html and 'id="hydro-menu"' in html and "/static/tools/hydro/hydro.js" in html
