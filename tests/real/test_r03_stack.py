"""Stack layers on real data: product bands + indices + labels on one grid."""

import rasterio

import webapp.workspace as ws
from tests.helpers import run

TOOL = "Stack layers"


def test_stack_from_the_product(s2_stack, report):
    i = s2_stack["info"]
    report.metric("bands", i["count"])
    report.metric("size (px)", f"{i['width']} × {i['height']}")
    assert i["count"] == 12 and 500 < i["width"] < 700 and abs(i["res"][0] - 10) < 0.01


def test_stack_bands_indices_and_labels_coarser(client, s2_vrt, s2_stack, scl_labels, s2_aoi, report):
    b = s2_vrt["info"]["band_map"]
    items = [{"path": s2_vrt["path"], "name": "s2", "bands": [b["B02"], b["B03"], b["B04"], b["B08"]]},
             {"path": s2_vrt["path"], "name": "ndvi", "index": "NDVI", "band_map": b, "scale": s2_vrt["info"]["scale"]},
             {"path": scl_labels["path"], "name": "scl"}]
    r = run(client, "/api/stack", {"items": items, "ref": s2_stack["path"], "factor": 2, "name": "s2_ndvi_scl_20m"})
    with rasterio.open(ws.root() / r["path"]) as s:
        report.metric("bands", s.count)
        report.metric("pixel size (m)", s.res[0])
        assert s.count == 6 and abs(s.res[0] - 20) < 0.5
