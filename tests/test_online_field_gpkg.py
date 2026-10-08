"""Online map layers (WMS / WMTS / XYZ capabilities), GeoPackage (write several layers, read them back) and field
collection (the phone page's .zip → points with photos). No network: the services' answers are small documents here."""

import io
import json
import sqlite3
import struct
import zipfile

import pytest
from PIL import Image
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from lulc_fetch import field, gpkg, online_layers
from tests.helpers import ok

TOOL = "Online layers, GeoPackage & field collection"

WMS = b"""<?xml version="1.0"?><!DOCTYPE WMT_MS_Capabilities SYSTEM "x.dtd"[ <!ELEMENT VendorSpecificCapabilities EMPTY>]>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms" xmlns:xlink="http://www.w3.org/1999/xlink">
 <Service><Name>WMS</Name><Title>Test maps</Title></Service>
 <Capability>
  <Request><GetMap><Format>image/jpeg</Format><Format>image/png</Format>
   <DCPType><HTTP><Get><OnlineResource xlink:href="https://maps.example.org/ows?map=x&amp;"/></Get></HTTP></DCPType></GetMap></Request>
  <Layer><Title>Root</Title><CRS>EPSG:4326</CRS><CRS>EPSG:3857</CRS>
   <Layer queryable="1"><Name>states</Name><Title>States of India</Title><Abstract>Boundaries</Abstract>
    <EX_GeographicBoundingBox><westBoundLongitude>68</westBoundLongitude><eastBoundLongitude>97</eastBoundLongitude>
     <southBoundLatitude>6</southBoundLatitude><northBoundLatitude>37</northBoundLatitude></EX_GeographicBoundingBox>
    <Style><Name>default</Name><LegendURL><OnlineResource xlink:href="https://maps.example.org/legend.png"/></LegendURL></Style></Layer>
   <Layer><Name>ndvi</Name><Title>NDVI</Title><Dimension name="time" default="2026-09-01">2026-01-01/2026-09-01/P1M</Dimension></Layer>
  </Layer>
 </Capability>
</WMS_Capabilities>"""

WMTS = b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1" version="1.0.0">
 <ows:ServiceIdentification><ows:Title>Test tiles</ows:Title></ows:ServiceIdentification>
 <Contents>
  <Layer><ows:Title>True colour</ows:Title><ows:Identifier>truecolor</ows:Identifier>
   <Style isDefault="true"><ows:Identifier>default</ows:Identifier></Style><Format>image/jpeg</Format>
   <Dimension><ows:Identifier>Time</ows:Identifier><Default>2026-10-01</Default><Value>2000-02-24/2026-10-01/P1D</Value></Dimension>
   <TileMatrixSetLink><TileMatrixSet>WebMerc</TileMatrixSet></TileMatrixSetLink>
   <ResourceURL format="image/jpeg" resourceType="tile" template="https://t.example.org/truecolor/default/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.jpg"/>
   <ResourceURL format="image/jpeg" resourceType="tile" template="https://t.example.org/truecolor/default/{Time}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.jpg"/>
  </Layer>
  <Layer><ows:Title>Polar only</ows:Title><ows:Identifier>polar</ows:Identifier>
   <TileMatrixSetLink><TileMatrixSet>Polar</TileMatrixSet></TileMatrixSetLink></Layer>
  <TileMatrixSet><ows:Identifier>WebMerc</ows:Identifier><ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>
   <TileMatrix><ows:Identifier>EPSG:3857:0</ows:Identifier><ScaleDenominator>559082264.0287178</ScaleDenominator>
    <TopLeftCorner>-20037508.34278925 20037508.34278925</TopLeftCorner><TileWidth>256</TileWidth><TileHeight>256</TileHeight>
    <MatrixWidth>1</MatrixWidth><MatrixHeight>1</MatrixHeight></TileMatrix>
   <TileMatrix><ows:Identifier>EPSG:3857:1</ows:Identifier><ScaleDenominator>279541132.0143589</ScaleDenominator>
    <TopLeftCorner>-20037508.34278925 20037508.34278925</TopLeftCorner><TileWidth>256</TileWidth><TileHeight>256</TileHeight>
    <MatrixWidth>2</MatrixWidth><MatrixHeight>2</MatrixHeight></TileMatrix>
  </TileMatrixSet>
  <TileMatrixSet><ows:Identifier>Polar</ows:Identifier><ows:SupportedCRS>urn:ogc:def:crs:EPSG::3413</ows:SupportedCRS></TileMatrixSet>
 </Contents>
</Capabilities>"""


# ------------------------------------------------------------------ online map layers

def test_wms_capabilities():
    c = online_layers.parse_wms(online_layers.parse_xml(WMS), "https://maps.example.org/ows?map=x&SERVICE=WMS&REQUEST=GetCapabilities")
    assert c["kind"] == "wms" and c["title"] == "Test maps" and c["version"] == "1.3.0" and c["format"] == "image/png"
    assert c["url"] == "https://maps.example.org/ows?map=x"   # the GetMap address, without the OGC parameters
    states, ndvi = c["layers"]
    assert states["name"] == "states" and states["bbox"] == [68, 6, 97, 37] and states["mercator"] and states["queryable"]
    assert states["legend"] == "https://maps.example.org/legend.png"
    assert ndvi["mercator"] and ndvi["time"] == {"default": "2026-09-01", "start": "2026-01-01", "end": "2026-09-01", "ranges": 1}


def test_wmts_capabilities():
    c = online_layers.parse_wmts(online_layers.parse_xml(WMTS), "https://t.example.org/1.0.0/WMTSCapabilities.xml")
    assert c["kind"] == "wmts" and c["skipped"] == 1   # the polar-only layer can't be drawn on a Web Mercator map
    (l,) = c["layers"]
    assert l["url"] == "https://t.example.org/truecolor/default/{time}/WebMerc/{z}/{y}/{x}.jpg"   # the template with the date
    assert l["matrices"] == {"0": "EPSG:3857:0", "1": "EPSG:3857:1"} and (l["min_zoom"], l["max_zoom"]) == (0, 1)
    assert l["time"]["default"] == "2026-10-01" and l["time"]["start"] == "2000-02-24" and l["format"] == "image/jpeg"


def test_capabilities_errors_and_xyz():
    assert online_layers.capabilities("https://tile.example.org/{z}/{x}/{y}.png")["kind"] == "xyz"
    with pytest.raises(online_layers.ServiceError, match="entities"):
        online_layers.parse_xml(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
    with pytest.raises(online_layers.ServiceError, match="XML"):
        online_layers.parse_xml(b"<html><body>Not found</body>")
    with pytest.raises(online_layers.ServiceError, match="http"):
        online_layers.capabilities("file:///etc/passwd")


def test_capabilities_route(client, monkeypatch):
    asked = []

    def fake(url):
        asked.append(url)
        if "SERVICE=WMTS" in url:
            return b'<?xml version="1.0"?><ExceptionReport><Exception>no WMTS here</Exception></ExceptionReport>'
        return WMS
    monkeypatch.setattr(online_layers, "_fetch", fake)
    r = ok(client.get("/api/online/capabilities", params={"url": "https://maps.example.org/wms"}))
    assert r["kind"] == "wms" and [x["name"] for x in r["layers"]] == ["states", "ndvi"]
    assert "SERVICE=WMS" in asked[0] and "REQUEST=GetCapabilities" in asked[0]   # a WMS address is tried as WMS first
    monkeypatch.setattr(online_layers, "_fetch", lambda url: b"<html>no</html>")
    assert client.get("/api/online/capabilities", params={"url": "https://maps.example.org/wms"}).status_code == 400


# ------------------------------------------------------------------ GeoPackage

SQ = Polygon([(77, 12), (78, 12), (78, 13), (77, 13)])
LAYERS = [("Field points", [(Point(77.5, 12.9), {"name": "P1", "acc": 4.2, "n": 1, "tags": ["a", "b"], "ok": True}),
                            (Point(77.6, 12.95), {"name": "P2", "acc": 12, "n": None})]),
          ("fields", [(SQ, {"area": 1.5}), (MultiPolygon([SQ]), {"area": "big"})]),
          ("roads", [(LineString([(77, 12), (78, 13)]), {"Kind": "track", "kind": "dup"})])]


def test_gpkg_write_read(tmp_path):
    out = gpkg.write_gpkg(LAYERS, tmp_path / "survey.gpkg")
    con = sqlite3.connect(out)
    assert con.execute("PRAGMA application_id").fetchone()[0] == 0x47504B47
    assert con.execute("SELECT table_name, identifier, srs_id FROM gpkg_contents ORDER BY rowid").fetchall() == [
        ("Field_points", "Field points", 4326), ("fields", "fields", 4326), ("roads", "roads", 4326)]
    assert dict(con.execute("SELECT table_name, geometry_type_name FROM gpkg_geometry_columns").fetchall()) == {
        "Field_points": "POINT", "fields": "MULTIPOLYGON", "roads": "LINESTRING"}   # Polygon + MultiPolygon → MULTIPOLYGON
    blob = con.execute("SELECT geom FROM Field_points").fetchone()[0]
    assert blob[:4] == b"GP\x00\x03" and struct.unpack("<i", blob[4:8])[0] == 4326   # little endian, xy envelope
    con.close()
    back = dict(gpkg.read_gpkg(out))
    assert list(back) == ["Field points", "fields", "roads"]
    p1 = back["Field points"]["features"][0]
    assert p1["geometry"]["coordinates"] == (77.5, 12.9) and p1["properties"] == {"name": "P1", "acc": 4.2, "n": 1, "tags": '["a", "b"]', "ok": 1}
    assert back["fields"]["features"][0]["geometry"]["type"] == "MultiPolygon"
    assert set(back["roads"]["features"][0]["properties"]) == {"Kind", "kind_2"}   # column names are case-insensitive


def test_gpkg_reads_other_crs(tmp_path):
    """A layer in UTM (as QGIS may write it) is reprojected to longitude / latitude."""
    out = gpkg.write_gpkg([("utm", [(Point(0, 0), {"v": 1})])], tmp_path / "utm.gpkg")
    con = sqlite3.connect(out)
    con.execute("INSERT INTO gpkg_spatial_ref_sys VALUES ('WGS 84 / UTM 43N', 32643, 'EPSG', 32643, 'x', '')")
    con.execute("UPDATE gpkg_geometry_columns SET srs_id = 32643")
    con.execute("UPDATE utm SET geom = ?", (gpkg.geometry_blob(Point(500000, 1400000), 32643),))
    con.commit()
    con.close()
    (name, fc), = gpkg.read_gpkg(out.read_bytes())
    x, y = fc["features"][0]["geometry"]["coordinates"]
    assert name == "utm" and abs(x - 75) < 1e-6 and abs(y - 12.664) < 1e-3


def test_gpkg_export_and_add_data(client):
    fc = lambda feats: {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": json.loads(json.dumps(g.__geo_interface__)), "properties": p} for g, p in feats]}   # noqa: E731
    r = ok(client.post("/api/vector/export", json={"geojson": fc(LAYERS[0][1]), "format": "gpkg", "name": "survey", "layer_name": "Field points",
                                                   "more": [{"name": n, "geojson": fc(f)} for n, f in LAYERS[1:]]}))
    assert r["name"] == "survey.gpkg" and r["layers"] == 3 and r["features"] == 5
    data = client.get(r["url"]).content
    up = ok(client.post("/api/aoi/upload", files=[("files", ("survey.gpkg", data, "application/geopackage+sqlite3"))]))
    assert [x["name"] for x in up["layers"]] == ["Field points", "fields", "roads"] and len(up["features"]) == 2 and up["aoi"]


# ------------------------------------------------------------------ field collection

def field_zip(points=2, extra=None) -> bytes:
    """A .zip as the phone page makes it: field.json first, points.geojson, photos/."""
    buf = io.BytesIO()
    feats = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("field.json", json.dumps({"app": "LULC Fetch Field", "format": 1, "project": "Village A", "points": points}))
        photos = {}
        for i in range(points):
            names = [f"photos/Point_{i + 1}_{i + 1}.jpg"] if i == 0 else []
            for n in names:
                im = io.BytesIO()
                Image.new("RGB", (320, 240), (40, 140 + i, 60)).save(im, "JPEG", quality=90)
                photos[n] = im.getvalue()
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.5 + i / 100, 12.9, 910.5]},
                          "properties": {"name": f"Point {i + 1}", "crop": "Rice", "note": "brown spots", "time": "2026-10-08T05:30:00Z",
                                         "accuracy_m": 4.5, "altitude_m": 910.5, "photos": names}})
        z.writestr("points.geojson", json.dumps({"type": "FeatureCollection", "features": feats}))
        for n, b in photos.items():
            z.writestr(n, b)
        for n, b in (extra or {}).items():
            z.writestr(n, b)
    return buf.getvalue()


def test_field_import(tmp_path):
    from lulc_fetch.agri.disease import open_photo
    data = field_zip(extra={"photos/../../evil.jpg": b"x", "../outside.jpg": b"x", "photos/sub/deep.jpg": b"x"})
    assert field.is_field_zip(data) and not field.is_field_zip(b"PK not really")
    r = field.import_zip(data, tmp_path / "f", rel=lambda p: p.name)
    assert r["project"] == "Village A" and r["points"] == 2 and len(r["photos"]) == 1 and r["located_photos"] == 1
    p1, p2 = r["geojson"]["features"]
    assert p1["properties"]["photo"] == "Point_1_1.jpg" and p1["properties"]["photo_count"] == 1 and "photos" in p1["properties"]
    assert p2["properties"]["photo_count"] == 0 and "photo" not in p2["properties"]
    assert sorted(x.name for x in (tmp_path / "f").rglob("*") if x.is_file()) == ["Point_1_1.jpg", "points.geojson"]   # nothing else unpacked
    _, info = open_photo(tmp_path / "f" / "photos" / "Point_1_1.jpg")   # the position is in the photo now (as Diagnose reads it)
    assert abs(info["lat"] - 12.9) < 1e-5 and abs(info["lon"] - 77.5) < 1e-5 and info["taken"]


def test_field_import_route(client):
    r = ok(client.post("/api/field/import", files={"file": ("field_Village_A_2026-10-08.zip", field_zip(), "application/zip")}))
    assert r["name"] == "Village A" and r["points"] == 2 and r["photos"][0]["path"].startswith("uploads/field/")
    photo = client.get("/api/agri/photo", params={"path": r["photos"][0]["path"], "size": 64})
    assert photo.status_code == 200   # served to the map popup and Diagnose crop disease
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("points.geojson", "{}")
    assert client.post("/api/field/import", files={"file": ("x.zip", bad.getvalue(), "application/zip")}).status_code == 400
