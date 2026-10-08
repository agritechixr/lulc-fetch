"""SAR (Sentinel-1): speckle filters, terrain flattening and layover / shadow, the processed-raster inspector, SAR
features, time series and change (flooding), and the SAR workflow on a processed raster. Synthetic speckled images
whose answers are known; the GRD chain itself needs a real product (checked by hand against Planetary Computer RTC)."""

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
