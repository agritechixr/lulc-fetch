"""Export data on real layers: index GeoTIFF / PNG, class map → Shapefile, a product band straight from the VRT."""

import zipfile

import pytest

from tests.helpers import run

TOOL = "Export data"


def get(client, url) -> bytes:
    r = client.get(url)
    assert r.status_code == 200
    return r.content


@pytest.mark.parametrize("fmt", ["tif", "png", "pngw"])
def test_ndvi(client, s2_stack, fmt, report, tmp_path):
    i = s2_stack["info"]
    r = run(client, "/api/layers/export", {"path": s2_stack["path"], "format": fmt, "name": "albano_ndvi", "band_map": i["band_map"],
                                           "scale": i["scale"], "offset": i["offset"], "index": "NDVI"})
    blob = get(client, r["url"])
    report.metric(f"{fmt} size (MB)", len(blob) / 1e6)
    if fmt == "png":
        report.image("ndvi_png_export", __import__("io").BytesIO(blob) and _save(tmp_path, blob), "NDVI exported as PNG")


def _save(tmp_path, blob):
    p = tmp_path / "x.png"
    p.write_bytes(blob)
    return p


def test_scene_classes_to_shapefile(client, scl_labels, report, tmp_path):
    r = run(client, "/api/layers/export", {"path": scl_labels["path"], "format": "shp", "name": "albano_scl", "band": 1, "method": "custom",
                                           "breaks": [0.5, 1.5, 2.5, 3.5], "sieve": 20})
    blob = get(client, r["url"])
    (tmp_path / "s.zip").write_bytes(blob)
    report.metric("polygons", r["features"])
    assert r["features"] > 10 and any(n.endswith(".shp") for n in zipfile.ZipFile(tmp_path / "s.zip").namelist())


def test_product_band_saved_to_a_folder(client, s2_vrt, s2_aoi, report, tmp_path):
    r = run(client, "/api/layers/export", {"path": s2_vrt["path"], "format": "tif", "name": "b08_clip", "band": s2_vrt["info"]["band_map"]["B08"],
                                           "clip": s2_aoi, "folder": str(tmp_path)})
    saved = list(tmp_path.glob("*.tif"))
    report.metric("saved files", len(saved))
    assert saved
