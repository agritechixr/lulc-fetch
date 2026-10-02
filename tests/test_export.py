"""Export data: rasters as GeoTIFF / PNG / PNG + world file / Shapefile, vectors as Shapefile / GeoJSON / KML."""

import json
import zipfile

import pytest
import rasterio

from tests.helpers import ok, run


def fetch(client, url) -> bytes:
    r = client.get(url)
    assert r.status_code == 200, url
    return r.content


@pytest.mark.parametrize("fmt", ["tif", "png", "pngw", "shp"])
def test_export_index(client, s2_info, fmt, tmp_path):
    r = run(client, "/api/layers/export", {"path": s2_info["path"], "format": fmt, "name": "ndvi", "band_map": s2_info["band_map"],
                                           "scale": s2_info["scale"], "index": "NDVI"})
    blob = fetch(client, r["url"])
    out = tmp_path / r["name"]
    out.write_bytes(blob)
    if fmt == "tif":
        with rasterio.open(out) as s:
            assert s.count == 1 and s.width == 96
    elif fmt == "shp":
        assert r["features"] > 0
        assert {n.rsplit(".", 1)[-1] for n in zipfile.ZipFile(out).namelist()} >= {"shp", "shx", "dbf", "prj"}
    elif fmt == "pngw":   # PNG + world file, zipped together
        names = zipfile.ZipFile(out).namelist()
        assert any(n.endswith(".png") for n in names) and any(n.endswith((".pgw", ".pngw", ".wld")) for n in names), names
    else:
        assert blob[:8] == b"\x89PNG\r\n\x1a\n"


def test_export_whole_file_and_composite(client, data, s2_info):
    r = run(client, "/api/layers/export", {"path": data["s2"], "format": "tif", "name": "plain"})
    assert fetch(client, r["url"])
    r = run(client, "/api/layers/export", {"path": data["s2"], "format": "png", "name": "rgb", "band_map": s2_info["band_map"],
                                           "scale": s2_info["scale"], "composite": "true"})
    assert fetch(client, r["url"])[:4] == b"\x89PNG"


def test_export_class_map_to_shapefile_clipped(client, data):
    r = run(client, "/api/layers/export", {"path": data["labels"], "format": "shp", "name": "classes", "band": 1, "method": "custom",
                                           "breaks": [0.5, 1.5, 2.5, 3.5], "clip": data["aoi"]})
    assert r["features"] >= 3


def test_export_into_a_folder(client, s2_info, tmp_path):
    run(client, "/api/layers/export", {"path": s2_info["path"], "format": "tif", "name": "saved", "band_map": s2_info["band_map"],
                                       "scale": s2_info["scale"], "index": "NDWI", "folder": str(tmp_path)})
    assert list(tmp_path.glob("*.tif"))


@pytest.mark.parametrize("fmt", ["shp", "geojson", "kml"])
def test_export_vector(client, data, fmt, tmp_path):
    r = ok(client.post("/api/vector/export", json={"geojson": data["samples"], "format": fmt, "name": "samples"}))
    assert r["features"] == 6
    blob = fetch(client, r["url"])
    if fmt == "geojson":
        assert len(json.loads(blob)["features"]) == 6
    elif fmt == "kml":
        assert b"<kml" in blob and b"Placemark" in blob


def test_export_vector_clipped_into_folder(client, data, tmp_path):
    r = ok(client.post("/api/vector/export", json={"geojson": data["samples"], "format": "geojson", "name": "clipped", "clip": data["aoi"],
                                                   "folder": str(tmp_path)}))
    assert 0 < r["features"] <= 6 and list(tmp_path.glob("*.geojson"))
