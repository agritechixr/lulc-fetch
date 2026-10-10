"""Analysis ▸ Hydrology (second part), AHP and SAR flood ML refinement, on synthetic data with known answers: Gumbel
design storms, RUSLE, FwDET flood depth, flood impact, check-dam sites and area–capacity, morphometry, groundwater
potential, AHP weights, GR4J / LightGBM / LSTM streamflow, the SCS design hydrograph, the 2D flood simulation (mass
balance) and the self-trained flood map; CHIRPS and ERA5 with --network."""

import json
import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from tests.helpers import ok, run

TOOL = "Hydrology models, AHP & SAR flood ML"
N = 120


def _tif(path, a, res=30.0, crs="EPSG:32643", nodata=None, tags=None, desc=None):
    a = a[None] if a.ndim == 2 else a
    with rasterio.open(path, "w", driver="GTiff", width=a.shape[2], height=a.shape[1], count=a.shape[0], dtype="float32", crs=crs,
                       transform=from_origin(700000, 1500000, res, res), nodata=nodata) as d:
        d.write(a.astype("float32"))
        if tags:
            d.update_tags(**tags)
        for i, s in enumerate(desc or [], 1):
            d.set_band_description(i, s)
    return path


def _ll(home, rel, row, col):
    from rasterio.warp import transform as wt
    with rasterio.open(home / rel) as s:
        x, y = rasterio.transform.xy(s.transform, row, col)
        lon, lat = wt(s.crs, "EPSG:4326", [x], [y])
    return [lon[0], lat[0]]


def valley(n=N):
    y, x = np.mgrid[0:n, 0:n] * 30.0
    return 0.02 * (n * 30 - y) + 0.08 * np.abs(x - n * 15) + 6 * np.abs(np.sin(y / 1200 * np.pi)) * (np.abs(x - n * 15) > 500) \
        + np.random.default_rng(0).random((n, n)) * 0.5


@pytest.fixture
def up(home):
    (home / "uploads").mkdir(exist_ok=True)
    _tif(home / "uploads/v_dem.tif", valley())
    return home / "uploads"


def test_rainfall_statistics():
    from lulc_fetch.hydro import rainfall as R
    rng = np.random.default_rng(0)
    mx = rng.gumbel(60, 15, 2000)                                  # annual maxima from a known Gumbel
    lv = R.gumbel(mx)
    assert lv[100] == pytest.approx(60 - 15 * math.log(-math.log(0.99)), rel=0.03) and lv[2] < lv[10] < lv[100]
    hrs, steps = R.scs_hyetograph(100, 0.5)
    assert steps.sum() == pytest.approx(100) and hrs[np.argmax(steps)] == pytest.approx(12, abs=0.5)   # Type II peaks at noon
    with pytest.raises(ValueError, match="10 years"):
        R.gumbel(mx[:5])


def test_erosion_rusle(client, home, up):
    from lulc_fetch.hydro import erosion as E
    assert E.r_from_rain(np.array([600.0]))[0] == pytest.approx(0.0483 * 600 ** 1.61)
    assert E.r_from_rain(np.array([1200.0]))[0] == pytest.approx(587.8 - 1.219 * 1200 + 0.004105 * 1200 ** 2)
    lc = np.where(np.arange(N)[None, :] < N // 2, 40, 10) * np.ones((N, 1))                          # crops west, forest east
    _tif(up / "v_lc.tif", lc, tags={"classes": json.dumps({"10": "Tree cover", "40": "Cropland"})})
    r = run(client, "/api/hydro/erosion", {"dem": "uploads/v_dem.tif", "rain_mm": 1000, "texture": "loam", "landcover": "uploads/v_lc.tif",
                                            "points": [_ll(home, "uploads/v_dem.tif", N - 3, N // 2)]})
    assert r["c_from"] == {"Tree cover": 0.003, "Cropland": 0.28} and r["mean_K"] == pytest.approx(0.039)
    with rasterio.open(home / next(p for p in r["outputs"] if p.endswith("_soil_loss.tif"))) as d:
        A = d.read(1)
    with rasterio.open(home / next(p for p in r["outputs"] if p.endswith("_factors.tif"))) as d:
        Rf, K, LS, C, P = d.read()
    assert np.allclose(A, Rf * K * LS * C * P, rtol=1e-4, equal_nan=True)                           # A = R K LS C P
    assert np.nanmean(A[:, :N // 2 - 5]) > 20 * np.nanmean(A[:, N // 2 + 5:])                       # crops erode far more
    w = r["watersheds"][0]
    assert 0 < w["sdr"] < 1 and w["sediment_yield_t_yr"] == pytest.approx(w["gross_t_yr"] * w["sdr"], rel=0.01)


def test_flood_depth_and_impact(client, home, up):
    # a flat-bottomed valley flooded up to 12 m: the depth must be 12 − ground inside
    y, x = np.mgrid[0:N, 0:N] * 30.0
    z = 5 + 0.006 * np.abs(x - N * 15) * 10 / 3                    # V across, flat along
    _tif(up / "fd_dem.tif", z)
    flooded = (z < 12).astype("float32")
    _tif(up / "fd_flood.tif", np.where(flooded > 0, 5, 1), tags={"classes": json.dumps({"1": "Dry land", "5": "Water"})})
    r = run(client, "/api/hydro/flood-depth", {"dem": "uploads/fd_dem.tif", "flood": "uploads/fd_flood.tif", "smooth_px": 1})
    with rasterio.open(home / r["path"]) as d:
        dep = d.read(1)
    inner = (z < 11) & (np.arange(N)[:, None] > 5) & (np.arange(N)[:, None] < N - 5)
    assert np.nanmedian(np.abs(dep[inner] - (12 - z[inner]))) < 0.4 and r["max_depth_m"] == pytest.approx(7, abs=0.6)
    # impact: crops on the west half; 10 people per cell; a building inside and one outside; a road across
    lc = np.where(np.arange(N)[None, :] < N // 2, 40, 50) * np.ones((N, 1))
    _tif(up / "fd_lc.tif", lc, tags={"classes": json.dumps({"40": "Cropland", "50": "Built-up"})})
    _tif(up / "fd_pop.tif", np.full((N, N), 10.0))
    pt = lambda r_, c_: {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": _ll(home, "uploads/fd_dem.tif", r_, c_)}}
    blds = {"type": "FeatureCollection", "features": [pt(60, N // 2), pt(60, 2)]}
    roads = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "NH"}, "geometry": {"type": "LineString",
             "coordinates": [_ll(home, "uploads/fd_dem.tif", 60, 0), _ll(home, "uploads/fd_dem.tif", 60, N - 1)]}}]}
    im = run(client, "/api/hydro/flood-impact", {"depth": r["path"], "landcover": "uploads/fd_lc.tif", "population": "uploads/fd_pop.tif", "buildings": blds, "roads": roads})
    wet = (np.nan_to_num(dep) > 0.01).sum()                                                       # FwDET: 0 m on the flood's edge
    assert im["people"] == pytest.approx(10 * wet, rel=0.02) and im["buildings"] == 1 and im["buildings_total"] == 2
    assert im["roads_km"] == pytest.approx((z[60] < 12).sum() * 0.03, rel=0.15)
    assert set(im["land_cover_ha"]) == {"Cropland", "Built-up"}


def test_storage_and_morphometry(client, home, up):
    s = run(client, "/api/hydro/storage", {"dem": "uploads/v_dem.tif", "op": "sites", "height_m": 3, "stream_km2": 0.3, "max_slope_pct": 10, "top": 5})
    assert 1 <= s["sites"] <= 5 and s["best_volume_m3"] > 0
    sites = json.loads((home / next(p for p in s["outputs"] if p.endswith("_sites.geojson"))).read_text())["features"]
    m3 = [f["properties"]["m3_per_m_of_dam"] for f in sites]
    assert m3 == sorted(m3, reverse=True)                                                           # ranked
    c = run(client, "/api/hydro/storage", {"dem": "uploads/v_dem.tif", "op": "curve", "point": _ll(home, "uploads/v_dem.tif", 80, N // 2), "height_m": 4, "step_m": 1})
    vols = [r_["volume_m3"] for r_ in c["rows"]]
    areas = [r_["area_ha"] for r_ in c["rows"]]
    assert vols == sorted(vols) and areas == sorted(areas) and vols[-1] > 4 * vols[0]
    # exact bounds: one more metre deepens everything already flooded by 1 m, and no cell is deeper than the level
    for k in range(1, len(vols)):
        assert vols[k] - vols[k - 1] >= areas[k - 1] * 1e4 * 0.999
    for k, (v, a) in enumerate(zip(vols, areas), 1):
        assert v <= a * 1e4 * k * 1.001
    m = run(client, "/api/hydro/morphometry", {"dem": "uploads/v_dem.tif", "basin_km2": 0.4, "stream_km2": 0.1})
    assert m["basins"] >= 3
    for r_ in m["rows"]:
        assert 0 <= r_["HI"] <= 1 and 0 < r_["Rc"] <= 1.05 and r_["priority"] in ("High", "Medium", "Low")
    assert {r_["priority_rank"] for r_ in m["rows"]} == set(range(1, m["basins"] + 1))
    assert m["curves_csv"].startswith("tables/")


def test_ahp_and_groundwater(client, home, up):
    # Saaty's classic 3 × 3 example: weights ≈ 0.64, 0.26, 0.10, consistent
    w = ok(client.post("/api/ahp/weights", json={"matrix": [[1, 3, 5], [1 / 3, 1, 3], [1 / 5, 1 / 3, 1]]}))
    assert w["weights"] == pytest.approx([0.637, 0.258, 0.105], abs=0.005) and w["consistent"]
    bad = ok(client.post("/api/ahp/weights", json={"matrix": [[1, 9, 1 / 9], [1 / 9, 1, 9], [9, 1 / 9, 1]]}))
    assert not bad["consistent"] and bad["cr"] > 0.1
    rng = np.random.default_rng(1)
    a = rng.random((N, N)); b = rng.random((N, N))
    _tif(up / "ah_a.tif", a); _tif(up / "ah_b.tif", b)
    o = run(client, "/api/ahp/overlay", {"factors": [{"path": "uploads/ah_a.tif"}, {"path": "uploads/ah_b.tif", "rising": False}], "matrix": [[1, 3], [1 / 3, 1]]})
    assert o["weights"] == pytest.approx([0.75, 0.25], abs=1e-3)
    with rasterio.open(home / o["path"]) as d:
        idx = d.read(1)
    assert np.corrcoef(idx.ravel(), (0.75 * a - 0.25 * b).ravel())[0, 1] > 0.98
    # groundwater: weights by AHP from the usual order; wells whose yield follows the potential are recognised
    body = {"dem": "uploads/v_dem.tif", "stream_km2": 0.3, "radius_m": 500}
    g0 = run(client, "/api/hydro/groundwater", body)
    assert set(g0["factors"]) == {"Slope", "Drainage density", "Wetness (TWI)"} and g0["cr"] < 0.1
    assert abs(sum(g0["zone_pct"].values()) - 100) < 0.5
    with rasterio.open(home / next(p for p in g0["outputs"] if p.endswith("_potential_index.tif"))) as d:
        pot = d.read(1)
    rows, cols = rng.integers(5, N - 5, 40), rng.integers(5, N - 5, 40)
    yld = pot[rows, cols] * 20 + rng.normal(0, 0.2, 40)
    wells = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"yield": float(v)}, "geometry": {"type": "Point",
             "coordinates": _ll(home, "uploads/v_dem.tif", int(r_), int(c_))}} for r_, c_, v in zip(rows, cols, yld)]}
    g = run(client, "/api/hydro/groundwater", {**body, "wells": wells, "wells_field": "yield"})
    assert g["validation"]["wells"] == 40 and g["validation"]["spearman_rho"] > 0.7


def _flow_table(home, n_years=8, with_forcing=True):
    from lulc_fetch.hydro import streamflow as SF
    rng = np.random.default_rng(0)
    n = 365 * n_years
    doy = np.arange(n) % 365
    P = np.where(rng.random(n) < 0.3 + 0.25 * np.sin(2 * np.pi * doy / 365), rng.gamma(0.8, 12, n), 0)
    E = 3 + 2 * np.sin(2 * np.pi * (doy - 80) / 365)
    Q = SF.gr4j(P, E, [350, 0.5, 90, 1.7]) * (1 + 0.05 * rng.standard_normal(n))
    area = 250.0
    import pandas as pd
    df = pd.DataFrame({"date": pd.date_range("2010-01-01", periods=n, freq="D"), "discharge_m3s": Q * area / 86.4})
    if with_forcing:
        df["rain"], df["pet"] = P, E
    (home / "tables").mkdir(exist_ok=True)
    df.to_csv(home / "tables/flow.csv", index=False)
    return area


def test_streamflow_models(client, home):
    area = _flow_table(home)
    r = run(client, "/api/hydro/streamflow", {"table": "tables/flow.csv", "area_km2": area, "models": ["gr4j", "lgbm"]})
    by = {s["model"]: s for s in r["scores"]}
    assert by["GR4J"]["val_nse"] > 0.95 and by["LightGBM"]["val_nse"] > 0.6
    assert by["GR4J"]["val_nse"] > by["Day-of-year mean (baseline)"]["val_nse"] and r["best"] == "GR4J"
    assert r["gr4j_params"]["x1_production_mm"] == pytest.approx(350, rel=0.15) and r["forcing"] == "the table"
    assert r["csv"].startswith("tables/") and r["scores_csv"].startswith("tables/")


@pytest.mark.dl
@pytest.mark.slow
def test_streamflow_lstm(client, home):
    area = _flow_table(home, n_years=6)
    r = run(client, "/api/hydro/streamflow", {"table": "tables/flow.csv", "area_km2": area, "models": ["lstm"]})
    lstm = next(s for s in r["scores"] if s["model"] == "LSTM")
    assert lstm["val_nse"] > 0.5 and r["lstm"]["epochs"] >= 1


def test_design_hydrograph_and_flood_simulation(client, home, up):
    out = _ll(home, "uploads/v_dem.tif", N - 3, N // 2)
    r = run(client, "/api/hydro/hydrograph", {"dem": "uploads/v_dem.tif", "point": out, "storms_mm": [80, 160], "labels": ["10-yr", "100-yr"], "cn": 80, "dt_h": 0.25})
    s10, s100 = r["storms"]
    from lulc_fetch.hydro import runoff as R
    q_mm = float(R.scs_runoff(np.array([160.0]), np.array([80.0]))[0])
    assert s100["runoff_mm"] == pytest.approx(q_mm, rel=0.01)
    assert s100["volume_m3"] == pytest.approx(q_mm / 1000 * r["area_km2"] * 1e6, rel=0.03)       # the unit hydrograph keeps the volume
    assert s100["peak_m3s"] > s10["peak_m3s"] > 0 and 10 < s100["time_to_peak_h"] < 20                # Type II: after noon
    sim = run(client, "/api/hydro/flood-sim", {"dem": "uploads/v_dem.tif", "inflows": [{"lon": _ll(home, "uploads/v_dem.tif", 3, N // 2)[0],
              "lat": _ll(home, "uploads/v_dem.tif", 3, N // 2)[1], "q": [[0, 0], [0.5, 80], [1.5, 0]]}], "rain_mm_h": [[0, 15], [1, 0]], "hours": 3, "snapshots": 3})
    assert abs(sim["mass_error_pct"]) < 0.01 and sim["max_speed_ms"] < 10 and sim["flooded_km2"] > 0
    assert sim["inflow_m3"] == pytest.approx(80 * 3600 * 0.75, rel=0.02)                              # the triangle's area
    with rasterio.open(home / next(p for p in sim["outputs"] if p.endswith("_depth_over_time.tif"))) as d:
        assert d.count == 3
    with rasterio.open(home / next(p for p in sim["outputs"] if p.endswith("_arrival.tif"))) as d:
        arr = d.read(1, masked=True).filled(np.nan)
    assert np.nanmin(arr[:10, N // 2 - 3:N // 2 + 3]) < np.nanmin(arr[-10:, N // 2 - 3:N // 2 + 3])  # upstream gets wet first


def _sar_scene(up, n=160, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n]
    water = (np.hypot(yy - 80, xx - 70) < 35) | ((xx > 120) & (yy > 100))
    vv = np.where(water, -21, -9) + rng.normal(0, 2.2, (n, n))
    vh = np.where(water, -27, -16) + rng.normal(0, 2.2, (n, n))
    _tif(up / "s1_post.tif", np.stack([vv, vh]), res=10, desc=["VV", "VH"])
    # the threshold map's confidence: right but unsure near the edges, and an unsure patch inside the lake
    conf = np.clip(0.5 + (-15 - vv) / 10, 0, 1)
    conf[70:90, 60:80] = 0.5
    _tif(up / "s1_conf.tif", conf, res=10)
    return water


def test_sar_flood_ml(client, home, up):
    water = _sar_scene(up)
    r = run(client, "/api/sar/flood-ml", {"sar": "uploads/s1_post.tif", "confidence": "uploads/s1_conf.tif"})
    with rasterio.open(home / r["outputs"][1]) as d:
        cls = d.read(1)
    pred = cls >= 2
    iou = (pred & water).sum() / (pred | water).sum()
    raw = (np.clip(0.5 + (-15 - rasterio.open(home / "uploads/s1_post.tif").read(1)) / 10, 0, 1) >= 0.5)
    raw_iou = (raw & water).sum() / (raw | water).sum()
    assert iou > 0.85 and iou >= raw_iou - 0.01 and r["held_out"]["iou_water"] > 0.8
    assert pred[72:88, 62:78].mean() > 0.9                                                         # the unsure patch decided as water
    assert "VV_dB_mean11" in r["features"]


@pytest.mark.dl
@pytest.mark.slow
def test_sar_flood_unet(client, home, up):
    water = _sar_scene(up, n=256, seed=1)
    r = run(client, "/api/sar/flood-ml", {"sar": "uploads/s1_post.tif", "confidence": "uploads/s1_conf.tif", "model": "unet", "epochs": 8})
    with rasterio.open(home / r["outputs"][1]) as d:
        pred = d.read(1) >= 2
    assert (pred & water).sum() / (pred | water).sum() > 0.8


@pytest.mark.network
def test_chirps_and_design_storms(client):
    t = run(client, "/api/hydro/rainfall", {"op": "total", "bbox": [77.4, 12.8, 77.8, 13.2], "start": "2024-07-01", "end": "2024-08-03"})
    assert t["files"] == 4 and 50 < t["mean_mm"] < 800                                             # July as one monthly file + 3 days
    s = run(client, "/api/hydro/rainfall", {"op": "storms", "point": [77.6, 13.0], "first_year": 1991, "last_year": 2020})
    assert s["years"] == 30 and s["levels"]["1_day"]["100"] > s["levels"]["1_day"]["2"] > 20 and s["series_csv"].startswith("tables/")
