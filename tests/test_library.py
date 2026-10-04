"""Library menu: GIS data on Hugging Face, listed and added to the map. A local server stands in for Hugging Face
(the same API paths: /api/datasets, /api/datasets/<repo>/tree/main, /datasets/<repo>/resolve/main/<file>)."""

import hashlib
import http.server
import json
import threading
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from tests.helpers import ok, run

GJ = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"ward": "A"}, "geometry": {"type": "Polygon", "coordinates": [[[77.5, 12.9], [77.6, 12.9], [77.6, 13.0], [77.5, 12.9]]]}},
    {"type": "Feature", "properties": {"ward": "B"}, "geometry": {"type": "Point", "coordinates": [77.55, 12.95]}}]}


def _shapefile_parts():
    import io

    import shapefile
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POINT)
    w.field("name", "C")
    w.point(77.59, 12.97); w.record("City Railway Station")
    w.close()
    prj = b'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
    return {"stations.shp": shp.getvalue(), "stations.shx": shx.getvalue(), "stations.dbf": dbf.getvalue(), "stations.prj": prj}


@pytest.fixture(scope="module")
def fake_hub():
    files = {"CITIES/BENGALURU.geojson": json.dumps(GJ).encode(), "STATES/KARNATAKA/stations.csv": b"name,lat,lon\nA,12.97,77.59\nB,13.01,77.62\n",
             "notes.json": b'{"answer": "not a map"}', "catalog.json": json.dumps({"files": [
                 {"path": "CITIES/BENGALURU.geojson", "title": "Bengaluru", "type": "City wards", "features": 2, "kind": "vector"}]}).encode(),
             **{f"STATES/KARNATAKA/{k}": v for k, v in _shapefile_parts().items()}}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/api/datasets":
                assert parse_qs(u.query)["author"] == ["ixrbhii"]
                return self.send(200, json.dumps([{"id": "ixrbhii/test-maps", "cardData": {"pretty_name": "Test maps", "license": "mit"},
                                                   "lastModified": "2026-10-04T00:00:00Z", "description": "Maps for tests"}]).encode())
            if u.path == "/api/datasets/ixrbhii/test-maps/tree/main":
                return self.send(200, json.dumps([{"type": "file", "path": p, "size": len(b),
                                                   **({"lfs": {"oid": hashlib.sha256(b).hexdigest()}} if p.endswith(".geojson") else {})}
                                                  for p, b in files.items()]).encode())
            pre = "/datasets/ixrbhii/test-maps/resolve/main/"
            if u.path.startswith(pre) and unquote(u.path[len(pre):]) in files:
                return self.send(200, files[unquote(u.path[len(pre):])], "application/octet-stream")
            self.send(404, b"{}")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def hub(fake_hub, monkeypatch):
    from lulc_fetch import library
    from webapp.routes import library as routes
    monkeypatch.setattr(library, "HF", fake_hub)
    routes._cache.clear()
    return fake_hub


def test_lists_datasets_and_files(client, hub):
    d = ok(client.get("/api/library/datasets"))
    assert [x["repo"] for x in d["datasets"]] == ["ixrbhii/test-maps"] and d["datasets"][0]["title"] == "Test maps"
    f = ok(client.get("/api/library/files", params={"repo": "ixrbhii/test-maps"}))
    by = {x["path"]: x for x in f["files"]}
    assert by["CITIES/BENGALURU.geojson"]["title"] == "Bengaluru" and by["CITIES/BENGALURU.geojson"]["kind"] == "vector"
    assert by["STATES/KARNATAKA/stations.csv"]["kind"] == "table"
    assert by["notes.json"]["kind"] == "other"                        # a plain .json isn't taken for a map
    assert "STATES/KARNATAKA/stations.shx" not in by and by["STATES/KARNATAKA/stations.shp"]["kind"] == "vector"   # one entry per shapefile


def test_fetch_geojson_shapefile_and_csv(client, hub):
    f = {x["path"]: x for x in ok(client.get("/api/library/files", params={"repo": "ixrbhii/test-maps"}))["files"]}
    g = f["CITIES/BENGALURU.geojson"]
    r = run(client, "/api/library/fetch", {"repo": "ixrbhii/test-maps", "path": g["path"], "sha256": g["sha256"], "size": g["size"]})
    fc = ok(client.get("/api/library/geojson", params={"path": r["path"]}))
    assert r["kind"] == "vector" and len(fc["features"]) == 2
    assert ok(client.get("/api/library/files", params={"repo": "ixrbhii/test-maps"}))["files"][0]["local"] in (True, False)
    s = run(client, "/api/library/fetch", {"repo": "ixrbhii/test-maps", "path": "STATES/KARNATAKA/stations.shp"})
    sfc = ok(client.get("/api/library/geojson", params={"path": s["path"]}))
    assert sfc["features"][0]["properties"]["name"] == "City Railway Station"
    assert [round(c, 2) for c in sfc["features"][0]["geometry"]["coordinates"]] == [77.59, 12.97]
    t = run(client, "/api/library/fetch", {"repo": "ixrbhii/test-maps", "path": "STATES/KARNATAKA/stations.csv"})
    assert t["kind"] == "table" and t["lonlat"] == ["lon", "lat"]


def test_damaged_download_is_refused(client, hub):
    j = ok(client.post("/api/library/fetch", json={"repo": "ixrbhii/test-maps", "path": "CITIES/BENGALURU.geojson", "sha256": "0" * 64, "size": 1}))   # size differs from the copy on disk: fetched again
    from tests.helpers import wait
    with pytest.raises(BaseException, match="damaged"):
        wait(client, j)


def test_only_library_files_are_served(client):
    assert client.get("/api/library/geojson", params={"path": "../../etc/passwd"}).status_code == 404
    assert client.post("/api/library/fetch", json={"repo": "ixrbhii/test-maps", "path": "../x.geojson"}).status_code == 400
