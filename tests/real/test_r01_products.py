"""Your Sentinel products (.SAFE): open the data folder's Sentinel-2 L2A and Sentinel-1 GRD zips."""

import numpy as np
import rasterio

import webapp.workspace as ws
from tests.helpers import ok, wait
from tests.real.metrics import stretch_rgb

TOOL = "Sentinel products (.SAFE)"


def test_sentinel2_opens_as_12_bands(client, s2_vrt, report):
    info = s2_vrt["info"]
    report.metric("bands", info["count"])
    report.metric("size (px)", f"{info['width']} × {info['height']}")
    report.note(f"{s2_vrt['product']['title']} · {s2_vrt['product']['date']} · {s2_vrt['product']['size_mb']:.0f} MB")
    assert info["count"] == 12 and info["width"] == 10980
    assert [b["description"] for b in info["bands"]][:4] == ["B01", "B02", "B03", "B04"]
    assert abs(info["scale"] - 1e-4) < 1e-9   # L2A DN → reflectance


def test_sentinel2_area_quicklook(s2_stack, s2_rgb, report):
    report.image("albano_true_colour", s2_rgb, "Test area: Lake Albano, true colour (B04 B03 B02) from the stack of the product")
    assert s2_rgb.shape[0] > 500 and s2_rgb.std() > 10


def test_sentinel1_backscatter(client, s1_zip, s1_aoi, report):
    linked = ok(client.post("/api/products/link", json={"path": str(s1_zip)}))
    p = linked["products"][0]
    assert p["kind"] == "S1_GRD"
    r = ok(client.post("/api/products/open", json={"path": p["path"], "res": 20, "aoi": s1_aoi}))
    assert r["kind"] == "job"
    res = wait(client, r["job"])["result"]
    out = res["files"][0]
    with rasterio.open(out) as s:
        a = s.read(masked=True)
        report.metric("size (px)", f"{s.width} × {s.height}")
        assert list(s.descriptions) == ["VV", "VH", "VVVH"]
    vv, vh = np.ma.median(a[0]), np.ma.median(a[1])
    report.metric("VV median (dB)", float(vv))
    report.metric("VH median (dB)", float(vh))
    assert -20 < vv < 2 and -28 < vh < -5 and vh < vv   # cross-polarised backscatter is weaker
    rgb = np.stack([a[0].filled(-30), a[1].filled(-30), (a[0] - a[1]).filled(0)])
    report.image("sentinel1_vienna", stretch_rgb(rgb), "Sentinel-1 σ⁰ over Vienna: R = VV, G = VH, B = VV − VH (dB)")


def test_products_are_listed(client, s2_vrt):
    names = [p["name"] for p in ok(client.get("/api/products"))["products"]]
    assert s2_vrt["product"]["name"] in names
