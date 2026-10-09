"""SAR (Sentinel-1): speckle filters, terrain flattening and layover / shadow, the processed-raster inspector, SAR
features, time series and change (flooding), and the SAR workflow on a processed raster. Synthetic speckled images
whose answers are known; the GRD chain itself needs a real product (checked by hand against Planetary Computer RTC)."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from lulc_fetch.sar import analysis as AN
from lulc_fetch.sar import geometry as G
from lulc_fetch.sar import product as P
from lulc_fetch.sar import speckle as SP
from tests.helpers import ok, run

TOOL = "SAR (Sentinel-1)"
H, W = 120, 160
RNG = np.random.default_rng(7)


def speckled(mean, looks=4.4):
    return mean * RNG.gamma(looks, 1 / looks, size=np.shape(mean))


def scene(path, vv, vh, *, db=False, date=None):
    bands = [10 * np.log10(vv), 10 * np.log10(vh)] if db else [vv, vh]
    prof = dict(driver="GTiff", width=W, height=H, count=2, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1400000, 20, 20), nodata=np.nan)
    with rasterio.open(path, "w", **prof) as d:
        for i, (b, n) in enumerate(zip(bands, ("VV", "VH")), 1):
            d.write(b.astype("float32"), i)
            d.set_band_description(i, n)
        d.update_tags(units="dB" if db else "sigma0 linear power", **({"sar_date": date} if date else {}))
    return path


def test_speckle_filters_smooth_and_keep_the_mean():
    flat = speckled(np.full((H, W), 0.05))
    assert SP.enl(flat) == pytest.approx(4.4, rel=0.2)
    for m in SP.FILTERS:
        f = SP.filter_image(flat, m, 5, 4.4)
        assert f.shape == flat.shape and np.isfinite(f).all(), m
        assert SP.enl(f) > 3 * SP.enl(flat), f"{m} hardly filters"
        bias = 10 * np.log10(np.mean(f) / 0.05)
        assert abs(bias) < (0.6 if m == "median" else 0.15), f"{m} biased by {bias:.2f} dB"
    # an edge stays sharp with Refined Lee (much less smeared than boxcar)
    step = speckled(np.where(np.arange(W) < W // 2, 0.01, 0.2)[None].repeat(H, 0))
    rl, bx = SP.filter_image(step, "refined_lee", 5), SP.filter_image(step, "boxcar", 5)
    c = W // 2
    assert np.median(rl[:, c - 2]) < np.median(bx[:, c - 2])
    # NaN (no data) stays NaN and doesn't spread
    g = flat.copy(); g[:10] = np.nan
    f = SP.filter_image(g, "lee", 5)
    assert np.isnan(f[:10]).all() and np.isfinite(f[10:]).all()
    with pytest.raises(ValueError):
        SP.filter_image(flat, "lee", 4)


def test_flattening_and_layover_shadow():
    th = np.array([35.0, 35, 35, 35, 35])
    a = np.array([0.0, 10, -10, 40, -60])
    f = G.flatten_factor(th, a)
    assert f[0] == pytest.approx(1) and f[1] > 1 > f[2]                    # slopes facing the sensor are brighter
    assert list(G.layover_shadow(th, a)) == [0, 0, 0, 1, 2]
    dem = np.add.outer(np.zeros(5), np.arange(5) * 10.0)                     # rises 10 m per 10 m pixel eastwards
    sx, sy = G.slopes(dem, (10, 10))
    assert np.allclose(sx, 1) and np.allclose(sy, 0)


def test_inspect_raster_says_what_is_done(tmp_path):
    vv, vh = speckled(np.full((H, W), 0.08)), speckled(np.full((H, W), 0.015))
    lin = P.inspect_raster(scene(tmp_path / "S1_VV_VH_lin.tif", vv, vh))
    st = {s["step"]: s["status"] for s in lin["steps"]}
    assert lin["supported"] and lin["units"] == "linear power" and lin["polarisations"] == ["VH", "VV"]
    assert st["calibrate"] == "done" and st["terrain"] == "done" and st["db"] == "optional" and st["speckle"] == "optional"
    db = P.inspect_raster(scene(tmp_path / "S1_gee.tif", vv, vh, db=True))
    assert db["units"] == "dB" and {s["step"]: s["status"] for s in db["steps"]}["db"] == "done"   # never converted twice
    with rasterio.open(tmp_path / "rgb.tif", "w", driver="GTiff", width=10, height=10, count=1, dtype="uint8") as d:
        d.write(np.full((1, 10, 10), 120, "uint8"))
    assert not P.inspect_raster(tmp_path / "rgb.tif")["supported"]


def test_features_series_and_flooding(tmp_path):
    vv0, vh0 = np.full((H, W), 0.08), np.full((H, W), 0.016)
    a = scene(tmp_path / "s1_2026-08-01.tif", speckled(vv0), speckled(vh0))
    flood = np.zeros((H, W), bool); flood[40:80, 50:110] = True
    vv1 = np.where(flood, 0.002, vv0)
    b = scene(tmp_path / "s1_2026-08-13.tif", speckled(vv1), speckled(np.where(flood, 0.0005, vh0)))
    c = scene(tmp_path / "s1_2026-08-25.tif", speckled(vv0), speckled(vh0), db=True)       # a dB file mixes in fine
    f = AN.features(a, tmp_path / "feat.tif", ["ratio", "rvi", "ndpi", "texture"])
    with rasterio.open(f["path"]) as d:
        names = list(d.descriptions)
        ratio = d.read(names.index("VV/VH (dB)") + 1)
        assert names[1:3] == ["RVI", "NDPI"] and len(names) == 7
    assert np.nanmedian(ratio) == pytest.approx(10 * np.log10(0.08 / 0.016), abs=0.5)
    t = AN.temporal([a, b, c], tmp_path / "ts.tif", ["mean", "count", "trend"])
    assert t["dates"] == ["2026-08-01", "2026-08-13", "2026-08-25"]
    with rasterio.open(t["path"]) as d:
        assert d.count == 6 and np.all(d.read(2) == 3)                                   # VV mean, count, trend; VH …
    ch = AN.change(a, b, tmp_path / "ch", threshold_db=3, water_db=-18)
    with rasterio.open(ch["outputs"][0]) as d:
        cls = d.read(1)
    hit = (cls[flood] == 4).mean()
    assert hit > 0.9 and (cls[~flood] == 4).mean() < 0.02
    assert ch["flood_ha"] == pytest.approx(flood.sum() * 0.04, rel=0.15)


def test_workflow_on_a_processed_raster(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    scene(up / "s1_2026-08-01_VV_VH.tif", speckled(np.full((H, W), 0.08)), speckled(np.full((H, W), 0.016)))
    scene(up / "s1_2026-08-13_VV_VH.tif", speckled(np.full((H, W), 0.02)), speckled(np.full((H, W), 0.016)))
    info = ok(client.post("/api/sar/inspect", json={"raster": "uploads/s1_2026-08-01_VV_VH.tif"}))
    assert info["supported"]
    r = run(client, "/api/sar/process", {"sources": [{"raster": "uploads/s1_2026-08-01_VV_VH.tif"}, {"raster": "uploads/s1_2026-08-13_VV_VH.tif"}],
                                         "steps": ["speckle", "db"], "speckle": {"method": "refined_lee", "size": 5}, "db": True,
                                         "features": ["ratio"], "temporal": ["mean"], "change": True, "name": "t"})
    assert len(r["runs"]) == 2 and "speckle" in r["runs"][0]["steps_done"]
    assert any(o.endswith("_dB.tif") for o in r["outputs"]) and any("change_classes" in o for o in r["outputs"])
    dec = next(c for c in r["change"] if c["class"].startswith("Decrease"))   # −6 dB everywhere
    assert dec["pixels"] > 0.9 * H * W
    for o in r["outputs"]:
        assert (home / o).exists()
    s = run(client, "/api/sar/series", {"rasters": ["uploads/s1_2026-08-01_VV_VH.tif", "uploads/s1_2026-08-13_VV_VH.tif"], "stats": ["mean", "std"], "change": False})
    assert s["dates"] == ["2026-08-01", "2026-08-13"]
    # errors said plainly
    assert client.post("/api/sar/process", json={"sources": [{"scene": "not a scene"}]}).status_code == 400
    assert client.post("/api/sar/process", json={"sources": [{"raster": "uploads/s1_2026-08-01_VV_VH.tif"}], "change": True}).status_code == 400
    assert client.post("/api/sar/process", json={"sources": [{"raster": "uploads/s1_2026-08-01_VV_VH.tif"}], "steps": ["nope"]}).status_code == 400


def test_multitemporal_filter_keeps_changes():
    N = 6
    st = np.stack([speckled(np.full((H, W), 0.05)) for _ in range(N)])
    st[3, :, W // 2:] *= 4                                                       # one date changes on the right half
    f = SP.quegan(st, 7)
    left = (slice(None), slice(0, W // 2 - 8))
    assert SP.enl(f[0][left]) > 0.7 * N * 4.4                                    # ≈ N × the looks
    assert np.mean(f[3][:, W // 2 + 8:]) / np.mean(f[3][left]) == pytest.approx(4, rel=0.1)   # the change stays
    assert np.mean(f[0][:, W // 2 + 8:]) / np.mean(f[0][left]) == pytest.approx(1, rel=0.1)   # and doesn't leak
    with pytest.raises(ValueError):
        SP.quegan(st[:1], 7)


def test_area_flattening_geometry():
    # flat ground: the gamma projection is cos θ, and the illuminated area per radar pixel is its ground area × cos θ
    from lulc_fetch.sar import pipeline as PL
    lat, lon = np.array([[28.0]]), np.array([[76.0]])
    P_ = G.ecef(lat, lon, np.zeros_like(lat))
    up = P_ / np.linalg.norm(P_, axis=-1, keepdims=True)
    east = np.cross([0, 0, 1.0], up); east /= np.linalg.norm(east, axis=-1, keepdims=True)
    S = P_ + 700e3 * (np.cos(np.radians(35)) * up + np.sin(np.radians(35)) * east)   # seen at 35° from the east
    assert G.gamma_projection(lat, lon, S, P_)[0, 0] == pytest.approx(np.cos(np.radians(35)), abs=1e-5)   # (a geocentric "up" here)
    assert G.gamma_projection(lat, lon, S, P_, (np.array([[-0.2]]), np.array([[0.0]])))[0, 0] > np.cos(np.radians(35))   # facing the sensor
    yy, xx = np.mgrid[0:60, 0:60].astype("float32")
    acc = PL._splat(yy / 2, xx / 2, np.full((60, 60), 0.8), 100.0, (30, 30))          # 2 × 2 cells of 100 m² per radar pixel
    assert acc.sum() == pytest.approx(60 * 60 * 100 * 0.8, rel=0.02)
    assert np.allclose(acc[3:-3, 3:-3], 4 * 100 * 0.8, rtol=0.02)                    # no holes, no ripple inside
    assert G.normalise_factor(np.array([40.0, 30.0]), 40, 2)[0] == pytest.approx(1)
    assert G.normalise_factor(np.array([30.0]), 40, 2)[0] == pytest.approx((np.cos(np.radians(40)) / np.cos(np.radians(30))) ** 2)


def test_quality_layer_normalisation_and_frames(tmp_path):
    from lulc_fetch.sar import pipeline as PL
    th = np.linspace(30, 45, W)[None].repeat(H, 0)
    vv = 0.1 * np.cos(np.radians(th)) ** 2                                       # Lambert: brighter at near range
    p = tmp_path / "gee_s1.tif"
    prof = dict(driver="GTiff", width=W, height=H, count=3, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1400000, 20, 20), nodata=np.nan)
    with rasterio.open(p, "w", **prof) as d:
        for i, (b, n) in enumerate(((10 * np.log10(vv), "VV"), (10 * np.log10(vv / 5), "VH"), (th, "angle")), 1):
            d.write(b.astype("float32"), i)
            d.set_band_description(i, n)
        d.update_tags(units="dB")
    assert {s["step"]: s["status"] for s in P.inspect_raster(p)["steps"]}["normalise"] == "optional"
    r = PL.process(str(p), tmp_path / "o", {"steps": ["normalise"], "normalise_ref": 40, "db": True})
    with rasterio.open(r["paths"]["linear"]) as d:
        v = d.read(1)
    assert np.allclose(v, 0.1 * np.cos(np.radians(40)) ** 2, rtol=1e-4)           # every column as if seen at 40°
    with rasterio.open(r["paths"]["quality"]) as d:
        assert np.all(d.read(1) == 1) and "Valid" in d.tags()["classes"]
    # two halves of one image (frames of a pass) join into the whole
    a, b = vv.copy(), vv.copy()
    a[H // 2:], b[:H // 2 - 5] = np.nan, np.nan
    halves = [PL.process(scene(tmp_path / f"f{k}.tif", x, x / 5), tmp_path / "f", {"steps": [], "name": f"f{k}"}) for k, x in enumerate((a, b))]
    j = PL.join_frames(halves, tmp_path / "f", "joined")
    with rasterio.open(j["paths"]["linear"]) as d:
        assert np.isfinite(d.read(1)).all()
    assert not Path(halves[0]["paths"]["linear"]).exists()


def test_workflow_multitemporal_and_hyp3_routes(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    for k in range(4):
        scene(up / f"mt_2026-08-{1 + 9 * k:02d}.tif", speckled(np.full((H, W), 0.05)), speckled(np.full((H, W), 0.01)))
    r = run(client, "/api/sar/process", {"sources": [{"raster": f"uploads/mt_2026-08-{1 + 9 * k:02d}.tif"} for k in range(4)], "steps": ["db"],
                                         "multitemporal": True, "mt_size": 7, "name": "mt"})
    assert r["multitemporal"]["dates"] == 4 and r["multitemporal"]["enl_gain"] > 2.5
    assert sum(o.endswith("_quality.tif") for o in r["outputs"]) == 4
    s = run(client, "/api/sar/series", {"rasters": [f"uploads/mt_2026-08-{1 + 9 * k:02d}.tif" for k in range(4)], "stats": ["mean"], "multitemporal": True})
    assert s["multitemporal"]["enl_gain"] > 2.5 and sum("_mtf" in o for o in s["outputs"]) == 8   # dB + linear of each date
    # HyP3: pairs, and the routes say plainly what's missing
    from lulc_fetch.sar import hyp3 as H3
    sc = [{"name": f"S{d}", "date": f"2026-09-{d:02d}", "path": 27, "frame": 89, "orbit_direction": "ascending"} for d in (5, 17, 29)]
    assert [p["days"] for p in H3.pairs(sc)] == [12, 12] and len(H3.pairs(sc, step=2)) == 3
    assert client.get("/api/sar/hyp3/user").status_code == 401
    bad = client.post("/api/sar/hyp3/submit", json={"job_type": "INSAR_GAMMA", "pairs": [["S1A_nope", "x"]]})
    assert bad.status_code == 400
    assert client.post("/api/sar/hyp3/submit", json={"job_type": "RTC_GAMMA", "granules": []}).status_code == 400
