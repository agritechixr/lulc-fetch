"""Coordinate systems: data without one (a shapefile without .prj, a GeoTIFF or a world-file picture without a system, a
GeoPackage layer marked undefined, a table in UTM metres) is offered a choice; any layer's system can be found,
corrected (assigned) or converted (exported in another system); vector layers can be placed by control points."""

import io
import json
import sqlite3
import zipfile

import numpy as np
import rasterio
import shapefile
from rasterio.transform import from_origin
from shapely.geometry import Point

from lulc_fetch import crs_tools, gpkg
from tests.helpers import ok

TOOL = "Coordinate systems & georeferencing"


def shp_zip(points, prj: str | None = None) -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POINT)
    w.field("name", "C")
    for i, (x, y) in enumerate(points):
        w.point(x, y)
        w.record(f"p{i}")
    w.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ext, b in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            z.writestr(f"wells.{ext}", b.getvalue())
        if prj:
            z.writestr("wells.prj", prj)
    return buf.getvalue()


UTM = [(700000, 1400000), (705000, 1402000)]   # metres in UTM zone 43N (near Bengaluru)


def test_search_describe_suggest(client):
    r = ok(client.get("/api/crs/search", params={"q": "utm 43", "lon": 77.6, "lat": 12.97}))["results"]
    assert r[0]["crs"] == "EPSG:32643" and r[0]["covers"]
    assert ok(client.get("/api/crs/search", params={"q": "32644"}))["results"][0]["name"] == "WGS 84 / UTM zone 44N"
    assert ok(client.get("/api/crs/describe", params={"text": "+proj=utm +zone=43 +datum=WGS84"}))["epsg"] == 32643
    assert client.get("/api/crs/describe", params={"text": "not a crs"}).status_code == 400
    s = ok(client.post("/api/crs/suggest", json={"bounds": [700000, 1400000, 705000, 1402000], "lon": 77.6, "lat": 12.97}))["suggestions"]
    assert s[0]["crs"] == "EPSG:32643" and "UTM" in s[0]["why"]
    s = ok(client.post("/api/crs/suggest", json={"bounds": [77.5, 12.9, 77.6, 13.0]}))["suggestions"]
    assert s[0]["crs"] == "EPSG:4326"


def test_shapefile_without_prj(client):
    data = shp_zip(UTM)
    old = ok(client.post("/api/aoi/upload", files=[("files", ("wells.zip", data, "application/zip"))]))
    assert "warning" in old and "crs_missing" not in old   # other callers (find imagery) keep the old behaviour
    fc = ok(client.post("/api/aoi/upload", params={"ask_crs": True}, files=[("files", ("wells.zip", data, "application/zip"))]))
    assert fc["crs_missing"] and fc["raw_bounds"] == [700000, 1400000, 705000, 1402000] and "aoi" not in fc
    placed = ok(client.post("/api/crs/vector", json={"geojson": fc, "actual": "EPSG:32643"}))
    lon, lat = placed["features"][0]["geometry"]["coordinates"]
    assert abs(lon - 76.8415) < 1e-3 and abs(lat - 12.6578) < 1e-3 and placed["crs"]["epsg"] == 32643
    assert client.post("/api/crs/vector", json={"geojson": fc, "actual": "EPSG:4326"}).status_code == 400   # metres as degrees: off the Earth
    # corrected later: it was read as zone 43N but is really 44N → the same numbers, 6° further east
    moved = ok(client.post("/api/crs/vector", json={"geojson": placed, "current": "EPSG:32643", "actual": "EPSG:32644"}))
    assert abs(moved["features"][0]["geometry"]["coordinates"][0] - (lon + 6)) < 0.01
    with_prj = ok(client.post("/api/aoi/upload", params={"ask_crs": True}, files=[("files", ("w.zip", shp_zip(UTM, rasterio.crs.CRS.from_epsg(32643).to_wkt()), "application/zip"))]))
    assert "crs_missing" not in with_prj   # a .prj: nothing to ask


def test_geopackage_undefined_srs(tmp_path):
    out = gpkg.write_gpkg([("plots", [(Point(700000, 1400000), {"a": 1})])], tmp_path / "x.gpkg")
    con = sqlite3.connect(out)
    con.execute("UPDATE gpkg_geometry_columns SET srs_id = 0")
    con.execute("UPDATE plots SET geom = ?", (gpkg.geometry_blob(Point(700000, 1400000), 0),))
    con.commit()
    con.close()
    (name, fc), = gpkg.read_gpkg(out, ask_crs=True)
    assert fc["crs_missing"] and fc["features"][0]["geometry"]["coordinates"] == (700000.0, 1400000.0)


def test_raster_without_crs(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    with rasterio.open(up / "nocrs.tif", "w", driver="GTiff", width=20, height=20, count=1, dtype="uint8", crs=None,
                       transform=from_origin(700000, 1400000, 10, 10)) as d:
        d.write(np.full((20, 20), 7, "uint8"), 1)
    info = ok(client.get("/api/crs/raster", params={"path": "uploads/nocrs.tif", "lon": 77.6, "lat": 12.97}))
    assert info["crs"] is None and info["has_grid"] and info["suggestions"][0]["crs"] == "EPSG:32643"
    assert client.post("/api/crs/assign-raster", json={"path": "uploads/nocrs.tif", "crs": "EPSG:4326"}).status_code == 400   # off the Earth
    r = ok(client.post("/api/crs/assign-raster", json={"path": "uploads/nocrs.tif", "crs": "EPSG:32643"}))
    meta = ok(client.get("/api/rasters/info", params={"path": r["path"]}))
    assert meta["crs"] == "EPSG:32643" and abs(meta["bounds"][0][1] - 76.84) < 0.01   # on the map now, the pixels unchanged
    with rasterio.open(home / r["path"]) as d:
        assert d.read(1)[0, 0] == 7
    # a world-file picture without .prj: a GeoTIFF without a system, for the same choice
    import PIL.Image
    buf = io.BytesIO()
    PIL.Image.new("RGB", (30, 20), (90, 140, 60)).save(buf, "PNG")
    pic = ok(client.post("/api/pictures/upload", params={"ask_crs": True}, files=[("files", ("scan.png", buf.getvalue(), "image/png")),
                                                                                  ("files", ("scan.pgw", b"10\n0\n0\n-10\n700005\n1399995\n", "text/plain"))]))
    assert pic["kind"] == "needs_crs" and pic["path"].endswith(".tif")
    assert ok(client.get("/api/crs/raster", params={"path": pic["path"]}))["has_grid"]


def test_table_points_in_utm(client, home):
    (home / "tables").mkdir(exist_ok=True)
    (home / "tables" / "survey_utm.csv").write_text("id,easting,northing\n1,700000,1400000\n2,705000,1402000\n")
    fc = ok(client.get("/api/tables/points", params={"path": "tables/survey_utm.csv", "lon": "easting", "lat": "northing", "crs": "EPSG:32643"}))
    assert len(fc["features"]) == 2 and abs(fc["features"][0]["geometry"]["coordinates"][0] - 76.8415) < 1e-3


def test_export_in_another_crs(client):
    layer = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [75.0, 12.6642]}, "properties": {"n": 1}}]}
    r = ok(client.post("/api/vector/export", json={"geojson": layer, "format": "shp", "name": "wells_utm", "crs": "EPSG:32643"}))
    z = zipfile.ZipFile(io.BytesIO(client.get(r["url"]).content))
    prj = next(n for n in z.namelist() if n.endswith(".prj"))
    assert "UTM" in z.read(prj).decode() and "Zone_43N" in z.read(prj).decode()
    shp = next(n for n in z.namelist() if n.endswith(".shp"))
    x, y = shapefile.Reader(shp=io.BytesIO(z.read(shp)), shx=io.BytesIO(z.read(shp[:-4] + ".shx"))).shape(0).points[0]
    assert abs(x - 500000) < 1 and abs(y - 1400001) < 2
    r = ok(client.post("/api/vector/export", json={"geojson": layer, "format": "gpkg", "name": "wells_utm", "crs": "EPSG:32643"}))
    data = client.get(r["url"]).content
    (_, back), = gpkg.read_gpkg(data)
    assert abs(back["features"][0]["geometry"]["coordinates"][0] - 75.0) < 1e-6   # read back to WGS 84 from UTM


def test_vector_control_points(client):
    """A drawing in millimetres (rotated and scaled) placed by four points; the fit lands every vertex."""
    from rasterio.warp import transform as tr

    def place(x, y):   # the "true" placement of a drawing coordinate
        e = 700000 + (x * np.cos(0.3) - y * np.sin(0.3)) * 2
        n = 1400000 + (x * np.sin(0.3) + y * np.cos(0.3)) * 2
        lo, la = tr("EPSG:32643", "EPSG:4326", [e], [n])
        return lo[0], la[0]
    pts = [{"x": x, "y": y, "lon": place(x, y)[0], "lat": place(x, y)[1]} for x, y in [(0, 0), (500, 0), (0, 400), (500, 400)]]
    drawing = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"plot": "A"},
                                                          "geometry": {"type": "Polygon", "coordinates": [[[100, 100], [250, 100], [250, 300], [100, 100]]]}}]}
    r = ok(client.post("/api/georef/vector", json={"geojson": drawing, "points": pts, "method": "affine"}))
    got = r["geojson"]["features"][0]["geometry"]["coordinates"][0][2]
    want = place(250, 300)
    assert r["rmse_m"] < 0.01 and abs(got[0] - want[0]) < 1e-7 and abs(got[1] - want[1]) < 1e-7
    assert r["geojson"]["features"][0]["properties"] == {"plot": "A"}
    many = [{"x": x, "y": y, "lon": place(x, y)[0], "lat": place(x, y)[1]} for x in (0, 170, 340, 500) for y in (0, 200, 400)]
    tps = ok(client.post("/api/georef/vector", json={"geojson": drawing, "points": many, "method": "tps"}))
    got = tps["geojson"]["features"][0]["geometry"]["coordinates"][0][2]
    assert tps["points"] == 12 and abs(got[0] - want[0]) < 1e-6   # a spline through points of an affine placement is that placement
    bad = client.post("/api/georef/vector", json={"geojson": drawing, "points": pts + pts[:1], "method": "affine"})
    assert bad.status_code == 400 and "same place" in bad.json()["detail"]
    assert client.post("/api/georef/vector", json={"geojson": drawing, "points": pts[:3], "method": "poly2"}).status_code == 400


def test_crs_tools_errors():
    import pytest
    with pytest.raises(ValueError):
        crs_tools.describe("EPSG:99999999")
    assert json.dumps(crs_tools.suggest(None, None))   # no numbers: still the default
