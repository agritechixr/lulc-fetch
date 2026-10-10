"""Image features: local statistics, GLCM texture, morphology (functions and routes), and the automatic thresholds."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from lulc_fetch import imagefeatures as F
from lulc_fetch import thresholds as TH
from tests.helpers import run

TOOL = "Image features & thresholds"
YY, XX = np.mgrid[0:120, 0:160]


def test_local_stats_known_values():
    rng = np.random.default_rng(0)
    img = np.where(XX < 80, 5.0, 5.0 + rng.normal(0, 2, XX.shape))      # flat left, noisy right
    img[0:3, 0:3] = np.nan
    d = dict(F.local_stats(img, (5, 15), F.STATS))
    assert np.nanmean(d["standard deviation 15×15"][:, 10:60]) < 1e-6 and np.nanmean(d["standard deviation 15×15"][:, 100:150]) == pytest.approx(2, rel=0.1)
    assert np.nanmean(d["mean 5×5"][:, 10:60]) == pytest.approx(5)
    assert np.isnan(d["mean 5×5"][0, 0]) and np.isfinite(d["mean 5×5"][5, 5])            # no-data stays no-data, neighbours ignore it
    assert np.nanmean(d["entropy 15×15"][:, 100:150]) > 3 > np.nanmean(d["entropy 15×15"][:, 10:60])
    r = d["range 5×5"]
    assert np.allclose(r, d["maximum 5×5"] - d["minimum 5×5"], equal_nan=True)
    with pytest.raises(ValueError):
        F.local_stats(img, (3,), ("nope",))


def test_glcm_separates_smooth_and_rough():
    img = np.where(XX < 80, 1.0, ((XX // 2 + YY // 2) % 2).astype(float))
    g = dict(F.glcm(img, window=7, levels=8, features=F.GLCM_FEATURES))
    left, right = (slice(10, 110), slice(10, 70)), (slice(10, 110), slice(90, 150))
    assert np.mean(g["GLCM contrast 7×7"][left]) == 0 and np.mean(g["GLCM contrast 7×7"][right]) > 10
    assert np.mean(g["GLCM homogeneity 7×7"][left]) == 1 > np.mean(g["GLCM homogeneity 7×7"][right])
    assert np.mean(g["GLCM entropy 7×7"][right]) > np.mean(g["GLCM entropy 7×7"][left]) == 0
    assert np.all(g["GLCM energy 7×7"] <= np.sqrt(g["GLCM asm 7×7"]) + 1e-6)   # (mean of √ over directions ≤ √ of the mean)


def test_morphology_operations():
    m = np.zeros((60, 60), np.uint8)
    m[10:40, 10:40] = 1
    m[50, 50] = 1                       # a speck
    m[20:23, 20:23] = 0                 # a hole
    assert F.morphology(m, "opening", size=3)[50, 50] == 0 and F.morphology(m, "closing", size=5)[21, 21] == 1
    assert F.morphology(m, "erosion", size=3).sum() < m.sum() < F.morphology(m, "dilation", size=3).sum()
    assert F.morphology(m, "remove_small", min_px=5)[50, 50] == 0 and F.morphology(m, "fill_holes", min_px=20)[21, 21] == 1
    b = F.morphology(m, "boundary", size=3)
    assert b[10, 25] == 1 and b[25, 25] == 0
    g = np.where(m > 0, 10.0, 0.0)
    assert F.morphology(g, "tophat", size=3, binary=False)[50, 50] == 10                 # a small bright detail
    cls = np.where(XX[:60, :60] < 30, 1, 2).astype(np.uint8)
    cls[5, 5] = 2                                                                          # an odd pixel
    assert F.morphology(cls, "majority", size=3)[5, 5] == 1


def test_thresholds_all_methods():
    rng = np.random.default_rng(1)
    obj = YY < 40
    img = np.where(obj, 0.5, -0.4) + 0.08 * rng.standard_normal(YY.shape)
    for m in TH.GLOBAL + TH.LOCAL + TH.FUZZY:
        r = TH.compute(img, m, default=0.0, tile=32)
        pred = img > r["threshold"] if r.get("membership") is None or r["kind"] != "fuzzy" else r["membership"] >= 0.5
        assert (pred == obj).mean() > 0.93, m
    land = -0.4 + 0.08 * rng.standard_normal(YY.shape)                                    # one class only: the default, not a split
    assert TH.compute(land, "otsu", default=0.0)["threshold"] == 0.0
    dark = np.where(obj, -24.0, -10.0) + rng.normal(0, 1.5, YY.shape)                     # radar: water dark
    r = TH.compute(dark, "kapur", bright=False, default=-18.0, tile=32)
    assert ((dark < r["threshold"]) == obj).mean() > 0.95


def test_feature_routes(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    rng = np.random.default_rng(2)
    prof = dict(driver="GTiff", width=160, height=120, count=2, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1400000, 10, 10))
    with rasterio.open(up / "feat_s1.tif", "w", **prof) as d:
        d.write(np.stack([0.05 * rng.gamma(4.4, 1 / 4.4, (120, 160)), 0.01 * rng.gamma(4.4, 1 / 4.4, (120, 160))]).astype("float32"))
        d.set_band_description(1, "VV")
        d.set_band_description(2, "VH")
        d.update_tags(units="gamma0 linear power")
    r = run(client, "/api/raster/localstats", {"path": "uploads/feat_s1.tif", "windows": [3, 7], "stats": ["mean", "cv"]})
    assert len(r["bands"]) == 8 and r["converted_to_db"] == ["VV", "VH"]
    g = run(client, "/api/raster/glcm", {"path": "uploads/feat_s1.tif", "bands": [1], "features": ["contrast", "entropy"]})
    with rasterio.open(home / g["outputs"][0]) as d:
        assert d.count == 2 and "GLCM entropy" in d.descriptions[1]
    with rasterio.open(up / "mask.tif", "w", **{**prof, "count": 1, "dtype": "uint8"}) as d:
        m = np.full((120, 160), 1, np.uint8)
        m[30:90, 40:120] = 2
        m[5, 5] = 2
        d.write(m, 1)
    mo = run(client, "/api/raster/morphology", {"path": "uploads/mask.tif", "op": "remove_small", "value": 2, "min_px": 10})
    with rasterio.open(home / mo["outputs"][0]) as d:
        a = d.read(1)
        assert a[5, 5] == 1 and a[60, 80] == 2                                           # the speck went back to its neighbours' class
    assert client.post("/api/raster/localstats", json={"path": "uploads/feat_s1.tif", "stats": ["nope"]}).status_code == 400


def test_autocorrelation_edges_multiscale():
    from scipy import ndimage as ndi

    from lulc_fetch import spatialraster as SR
    rng = np.random.default_rng(4)
    clustered = ndi.gaussian_filter(rng.normal(size=(120, 160)), 4)
    noise = rng.normal(size=(120, 160))
    checker = (XX % 2).astype(float)   # alternating columns: 6 of the 8 neighbours differ (a 1-pixel checkerboard is neutral with diagonals)
    a, b, c = SR.autocorrelation(clustered), SR.autocorrelation(noise, local=False), SR.autocorrelation(checker, local=False)
    assert a["moran_i"] > 0.8 and a["moran_p"] < 0.001 and a["geary_c"] < 0.3
    assert abs(b["moran_i"]) < 0.05 and abs(b["geary_c"] - 1) < 0.05
    assert c["moran_i"] < -0.1                                                          # neighbours differ
    hot = np.zeros((120, 160))
    hot[40:60, 50:80] = 5
    r = SR.autocorrelation(hot + 0.3 * rng.normal(size=hot.shape))
    assert (r["lisa"][45:55, 55:75] == 2).mean() > 0.9 and r["gi_star"][50, 65] > 5    # a hot cluster, and its Gi*
    cg = SR.correlogram(clustered, radii=(1, 4, 16, 32))
    assert cg[0]["moran_i"] > cg[-1]["moran_i"] and cg[-1]["moran_i"] < 0.2
    step = np.where(XX < 80, 0.0, 10.0)
    e = dict(SR.edges(step, sigma=1, which=("sobel", "canny", "directional")))
    assert e["Canny σ1"][:, 78:82].sum() >= 100 and e["Canny σ1"][:, :60].sum() == 0    # one vertical edge
    assert abs(np.nanmean(e["Gradient 90°"])) < 1e-9 < np.nanmax(e["Gradient 0°"])     # horizontal change only
    m = dict(SR.multiscale(step, (1, 4), ("smooth", "gradient", "dog", "std")))
    assert set(m) >= {"Gaussian σ1", "Difference of Gaussians σ1–4", "Gradient magnitude σ4"}


def test_slic_and_components():
    from lulc_fetch import segmentation as SG
    rng = np.random.default_rng(1)
    truth = ((XX // 40) + 4 * (YY // 40)).astype(int)
    lab = SG.slic([truth * 1.0 + rng.normal(0, 0.3, truth.shape)], n_segments=48, compactness=0.3, sigma=1)
    pure = np.mean([np.bincount(truth[lab == s]).max() / (lab == s).sum() for s in range(1, lab.max() + 1)])
    assert 30 <= lab.max() <= 80 and pure > 0.95
    from scipy import ndimage as ndi
    assert all(ndi.label(lab == s)[1] == 1 for s in range(1, lab.max() + 1))           # each superpixel one piece
    painted, table = SG.region_means(lab, [truth.astype(float)])
    assert table.shape == (lab.max() + 1, 1) and np.nanmax(np.abs(painted[0] - truth)) < 6
    mask = np.zeros((60, 60), int)
    mask[5:15, 5:15] = 1
    mask[30:40, 30:50] = 1
    mask[50, 50] = 1
    _, regs = SG.components(mask, 8, min_px=2)
    assert sorted(r["pixels"] for r in regs) == [100, 200]


def test_structure_routes(client, home):
    from scipy import ndimage as ndi
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    rng = np.random.default_rng(5)
    prof = dict(driver="GTiff", width=160, height=120, count=1, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1400000, 10, 10))
    with rasterio.open(up / "field.tif", "w", **prof) as d:
        d.write(ndi.gaussian_filter(rng.normal(size=(120, 160)), 3).astype("float32")[None])
    a = run(client, "/api/raster/autocorrelation", {"path": "uploads/field.tif"})
    assert a["moran_i"] > 0.5 and len(a["outputs"]) == 2 and "High-high (hot cluster)" in a["lisa_counts"]
    e = run(client, "/api/raster/edges", {"path": "uploads/field.tif", "which": ["sobel", "laplacian"]})
    assert len(e["bands"]) == 5
    m = run(client, "/api/raster/multiscale", {"path": "uploads/field.tif", "sigmas": [1, 2], "features": ["smooth", "std"]})
    assert len(m["bands"]) == 4
    s = run(client, "/api/raster/slic", {"path": "uploads/field.tif", "n_segments": 50})
    assert 20 < s["segments"] < 120 and any(o.endswith(".geojson") for o in s["outputs"])
    with rasterio.open(up / "objs.tif", "w", **{**prof, "dtype": "uint8"}) as d:
        o = np.zeros((120, 160), np.uint8)
        o[10:20, 10:20] = 2
        o[50:80, 60:100] = 2
        d.write(o[None])
    c = run(client, "/api/raster/components", {"path": "uploads/objs.tif", "value": 2})
    assert c["count"] == 2 and c["total_ha"] == pytest.approx((100 + 1200) * 0.01)
    lab = (ndi.gaussian_filter(rng.normal(size=(120, 160)), 6) > 0).astype(np.uint8) + 1
    with rasterio.open(up / "cls.tif", "w", **{**prof, "dtype": "uint8"}) as d:
        d.write(lab[None])
    with rasterio.open(up / "feat2.tif", "w", **{**prof, "count": 2}) as d:
        d.write(np.stack([lab + rng.normal(0, 0.8, lab.shape), rng.normal(size=lab.shape)]).astype("float32"))
    v = run(client, "/api/raster/spatialcv", {"path": "uploads/feat2.tif", "ground_truth": {"type": "raster", "path": "uploads/cls.tif", "band": 1},
                                              "blocks_m": [200, 400], "per_class": 500})
    assert [t["method"] for t in v["table"]] == ["Random k-fold", "Blocks of 200 m", "Blocks of 400 m"] and v["correlogram"]
