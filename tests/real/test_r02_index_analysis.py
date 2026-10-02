"""Index analysis on the real Sentinel-2 area: indices against ESA's scene classification."""

import numpy as np
import pytest
import rasterio

import webapp.workspace as ws
from tests.helpers import ok, run
from tests.real.conftest import CLASS_NAMES, S2_CENTER
from tests.real.metrics import data_url_png

TOOL = "Index analysis"


def spec(st, **kw):
    i = st["info"]
    return {"path": st["path"], "band_map": i["band_map"], "scale": i["scale"], "offset": i["offset"], **kw}


@pytest.mark.parametrize("index", ["NDVI", "NDWI", "NDBI", "EVI", "SAVI", "NBR"])
def test_render(client, s2_stack, index, report):
    r = ok(client.post("/api/analyze/render", json=spec(s2_stack, index=index)))
    report.image(f"{index}_map", data_url_png(r["image"]), f"{index} ({r['formula']})")
    st = r["stats"]
    report.metric(f"{index} mean", st.get("mean", float("nan")))
    assert r["kind"] == "continuous"


def test_indices_agree_with_the_scene_classification(client, s2_stack, scl_labels, report):
    r = run(client, "/api/analyze/export", spec(s2_stack, indices=["NDVI", "NDWI", "NDBI"]))
    with rasterio.open(ws.root() / r["path"]) as s:
        idx = s.read()
    lab = scl_labels["array"]
    rows = []
    means = {}
    for v, name in CLASS_NAMES.items():
        m = lab == v
        means[name] = [float(np.nanmean(idx[k][m])) for k in range(3)]
        rows.append([name, int(m.sum()), *means[name]])
    report.table("Mean index per SCL class", ["SCL class", "pixels", "NDVI", "NDWI", "NDBI"], rows)
    report.metric("NDVI vegetation", means["vegetation"][0])
    report.metric("NDVI water", means["water"][0])
    # early-October scene: "vegetation" also holds dry fields, so its mean NDVI is ~0.45; the order is what must hold
    assert means["vegetation"][0] > 0.35 and means["vegetation"][0] - 0.15 > means["bare / built-up"][0] > means["water"][0] + 0.1
    assert means["water"][1] > 0 > means["vegetation"][1]   # NDWI: water positive


def test_pixel_in_the_lake(client, s2_stack, report):
    lon, lat = S2_CENTER
    p = ok(client.post("/api/analyze/pixel", json=spec(s2_stack, index="NDWI", lon=lon + 0.003, lat=lat)))
    report.metric("NDWI in Lake Albano", p["value"])
    assert p["inside"] and p["value"] > 0   # the lake is very dark: NDWI ~0.07, like the mean of all SCL water pixels


def test_custom_formula_over_the_whole_product(client, s2_vrt, s2_aoi, report):
    v = s2_vrt
    r = ok(client.post("/api/analyze/render", json={"path": v["path"], "band_map": v["info"]["band_map"], "scale": v["info"]["scale"],
                                                    "offset": v["info"]["offset"], "formula": "(B08 - B11) / (B08 + B11)", "clip": s2_aoi}))
    report.image("ndmi_formula", data_url_png(r["image"]), "Custom formula (B08 − B11) / (B08 + B11) straight from the product, clipped")
    assert r["clipped"]
