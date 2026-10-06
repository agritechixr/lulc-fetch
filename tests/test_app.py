"""The app itself: it starts, lists every tool, refuses foreign requests, and handles files and projects."""

import pytest

from tests.helpers import ok

TOOLS = ["Find imagery", "Index analysis", "PCA & dimensionality reduction", "Training samples", "Stack layers", "Raster → table",
         "Classical ML (tabular data)", "Classical ML for raster", "Make training data", "Train classify model", "Classify image",
         "Detect object", "Train detection model", "Export data", "Downloads & jobs"]


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "LULC Fetch" in r.text
    assert "/static/vendor/leaflet/leaflet.min.js" in r.text


@pytest.mark.parametrize("title", TOOLS)
def test_every_tool_is_listed(client, title):
    js = client.get("/static/app.js").text
    assert f'title: "{title}"' in js, f"the tool {title} is missing from the Tools list"


def test_every_tool_has_a_panel(client):
    html = client.get("/").text
    for tab in ("search", "analyze", "pca", "samples", "stack", "raster2table", "ml", "rasterml", "patches", "dltrain",
                "dlpredict", "detect", "traindet", "export", "jobs"):
        assert f'id="tab-{tab}"' in html, f"no panel for {tab}"


def test_foreign_websites_are_refused(client):
    r = client.get("/api/project", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_project_info(client, home):
    p = ok(client.get("/api/project"))
    assert p["temporary"] is True
    assert p["workspace"] == str(home)


def test_index_catalog(client):
    c = ok(client.get("/api/indices"))
    names = {i["name"] for i in c["indices"]}
    assert {"NDVI", "NDWI", "SAVI", "EVI"} <= names
    assert "true" in c["composites"]


@pytest.mark.parametrize("formula,good", [("(B08 - B04) / (B08 + B04)", True), ("B08 ^ 2", True), ("B08 +", False), ("import os", False)])
def test_formula_check(client, formula, good):
    r = ok(client.get("/api/formula/check", params={"formula": formula}))
    assert r["ok"] is good


def test_raster_upload_info_metadata_delete(client, data):
    with open(data["folder"] / "s2.tif", "rb") as f:
        up = ok(client.post("/api/rasters/upload", files={"file": ("my_scene.tif", f, "image/tiff")}))
    path = up["path"]
    info = ok(client.get("/api/rasters/info", params={"path": path}))
    assert info["count"] == 6 and info["width"] == 96
    assert info["band_map"]["B04"] == 3 and info["band_map"]["B08"] == 4
    assert info["crs"] == "EPSG:32643"
    meta = ok(client.get("/api/rasters/metadata", params={"path": path}))
    assert meta
    assert any(r["path"] == path for r in ok(client.get("/api/rasters")))
    assert client.get("/api/rasters/file", params={"path": path}).status_code == 200
    ok(client.delete("/api/rasters", params={"path": path}))
    assert client.get("/api/rasters/info", params={"path": path}).status_code == 404


def test_upload_refuses_other_files(client):
    r = client.post("/api/rasters/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_paths_outside_the_workspace_are_refused(client):
    assert client.get("/api/rasters/info", params={"path": "../../etc/passwd"}).status_code == 404
    assert client.get("/api/tables/preview", params={"path": "../secret.csv"}).status_code == 404


def test_new_project_open_close(client, tmp_path):
    p = ok(client.post("/api/project/new", json={"name": "Test project", "folder": str(tmp_path)}))
    assert p["temporary"] is False and p["project"]["name"] == "Test project"
    ok(client.put("/api/project/state", json={"state": {"layers": [], "view": {"zoom": 3}}}))
    again = ok(client.get("/api/project"))
    assert again["state"]["view"]["zoom"] == 3
    ok(client.post("/api/project/close"))
    assert ok(client.get("/api/project"))["temporary"] is True


def test_folder_browser(client, tmp_path):
    (tmp_path / "sub").mkdir()
    r = ok(client.get("/api/fs/list", params={"path": str(tmp_path)}))
    assert any(e["name"] == "sub" for e in r["dirs"])
    ok(client.post("/api/fs/mkdir", json={"parent": str(tmp_path), "name": "made_by_test"}))
    assert (tmp_path / "made_by_test").is_dir()


def test_tool_files_register_themselves(client):
    """Tools kept in their own files (webapp/static/tools/<menu>/<tool>.js): each is loaded, registers one id with a
    panel, and the shared core (tools/lf.js) comes before them and app.js after them."""
    import re

    html = client.get("/").text
    scripts = re.findall(r'<script src="/static/(tools/[^"]+\.js)"></script>', html)
    assert scripts[0] == "tools/lf.js"
    assert html.index("/static/tools/") < html.index("/static/app.js"), "the tool files must load before app.js"
    ids = []
    for s in scripts:
        js = client.get(f"/static/{s}")
        assert js.status_code == 200, f"{s} isn't served"
        ids += re.findall(r"^  LF\.tool\(\{\s*id: \"(\w+)\"", js.text, re.M)   # (lf.js only shows one in a comment)
        if "LF.tool(" in js.text:
            assert "panel:" in js.text and "setup(LF)" in js.text, f"{s} has no panel or setup"
    assert sorted(ids) == sorted(["embed", "embtrain", "embpredict", "embconvert", "embexplore", "agridisease", "agriguide", "library", "interp", "fcdata", "fctrain", "fcrun"])
    assert len(ids) == len(set(ids)), "a tool id is registered twice"
    for css in re.findall(r'href="/static/(tools/[^"]+\.css)"', html):
        assert client.get(f"/static/{css}").status_code == 200


def test_no_function_is_defined_twice(client):
    """Two top-level functions with the same name in app.js silently replace each other (the later one wins), so one
    tool can break another (it happened once: a shared helper took the name of Index analysis's showResult)."""
    import re
    from collections import Counter

    js = client.get("/static/app.js").text
    names = re.findall(r"^  (?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", js, re.M)
    names += re.findall(r"^  (?:const|let)\s+([A-Za-z_$][\w$]*)\s*=", js, re.M)
    twice = [n for n, k in Counter(names).items() if k > 1]
    assert not twice, f"defined more than once in app.js: {twice}"


def test_app_script_parts():
    """app.js is joined from webapp/static/app/ (parts.json, in order): every part exists, every file there is listed."""
    import json
    from pathlib import Path

    d = Path(__file__).resolve().parent.parent / "webapp" / "static" / "app"
    parts = json.loads((d / "parts.json").read_text(encoding="utf-8"))["parts"]
    assert len(parts) == len(set(parts)), "a part is listed twice"
    on_disk = {str(p.relative_to(d)) for p in d.rglob("*.js")}
    assert set(parts) == on_disk, f"not listed: {on_disk - set(parts)}; missing: {set(parts) - on_disk}"


def test_every_tool_has_its_own_routes_file():
    """The server is one routes file per tool (webapp/routes/), included by server.py."""
    from webapp import server

    paths = set(server.app.openapi()["paths"])
    for p in ("/api/pca/run", "/api/ml/train", "/api/dl/train", "/api/detect/run", "/api/emb/fetch", "/api/agri/diagnose",
              "/api/history", "/api/stack", "/api/patches/make", "/api/rasterml/run", "/api/tables/from-raster"):
        assert p in paths, f"{p} isn't served"


def test_pixel_popup_lists_every_band(client):
    """Clicking a pixel lists all bands (it once stopped at 20, hiding most of a 64-band embedding) in a scrolling list,
    and right-clicking the map opens its menu (copy coordinates, what's here, tools from this point)."""
    js = client.get("/static/app.js").text
    assert "extra.slice(0, 20)" not in js and "px-scroll" in js
    assert 'map.on("contextmenu"' in js and "function toUtm" in js


@pytest.mark.parametrize("cols,want", [
    ({"lat": [12.9, 13.0], "lon": [77.5, 77.6]}, ["lon", "lat"]),
    ({"Latitude (deg)": [12.9, 13.0], "Longitude (deg)": [77.5, 77.6]}, ["Longitude (deg)", "Latitude (deg)"]),
    ({"LAT_DD": [12.9, 13.0], "LNG_DD": [77.5, 77.6]}, ["LNG_DD", "LAT_DD"]),
    ({"x": [77.5, 77.6], "y": [12.9, 13.0]}, ["x", "y"]),
    ({"x": [780000.0, 781000.0], "y": [1435000.0, 1436000.0]}, None),     # metres (UTM), not degrees
    ({"long": ["far", "near"], "lat": [12.9, 13.0]}, None),                  # a text column called "long"
])
def test_lonlat_columns_are_found(cols, want):
    import pyarrow as pa

    from lulc_fetch.tableview import detect_lonlat
    assert detect_lonlat(pa.table(cols)) == want


def test_csv_with_coordinates_becomes_points(client, tmp_path):
    """+ Add data: a CSV's coordinate columns are reported with the upload, and its rows come back as points."""
    from tests.helpers import ok
    p = tmp_path / "stations.csv"
    p.write_text("name,lat,lon,address\nA,12.97,77.59,Bengaluru\nB,,,(no position)\nC,13.01,77.62,Hebbal\n")
    with open(p, "rb") as f:
        r = ok(client.post("/api/tables/upload", files={"file": ("stations.csv", f, "text/csv")}))
    assert r["lonlat"] == ["lon", "lat"] and "address" in r["columns"]
    fc = ok(client.get("/api/tables/points", params={"path": r["path"]}))
    assert len(fc["features"]) == 2 and fc["features"][0]["geometry"]["coordinates"] == [77.59, 12.97]
    assert fc["features"][0]["properties"]["address"] == "Bengaluru"


def test_data_viewer_right_click_menus(client):
    """Right-click in the data viewer: a column menu and a cell menu (filter by value, delete the row …)."""
    js = client.get("/static/app.js").text
    assert "function wireTableMenus" in js and "wireTableMenus(t, d, content)" in js
    for label in ("Show only rows where", "Delete this row", "Delete this field", "Copy the row"):
        assert label in js


def test_filter_by_a_cell_value(client, tmp_path):
    """The filters the cell menu writes ("agency = \\"KSPCB\\"", "lat > 12.9") work on the server."""
    from tests.helpers import ok
    p = tmp_path / "s.csv"
    p.write_text('name,agency,lat\n"City Railway Station",KSPCB,12.97\nBTM Layout,CPCB,12.91\nHebbal,KSPCB,13.03\n')
    with open(p, "rb") as f:
        path = ok(client.post("/api/tables/upload", files={"file": ("s.csv", f, "text/csv")}))["path"]
    rows = lambda q: ok(client.get("/api/tables/rows", params={"path": path, "q": q}))["filtered"]   # noqa: E731
    assert rows('agency = "KSPCB"') == 2 and rows('agency != "KSPCB"') == 1 and rows("lat > 12.95") == 2
    assert rows('name = "City Railway Station"') == 1


def test_raster_grid_for_3d(client, data):
    import base64

    import numpy as np

    g = ok(client.get("/api/rasters/grid", params={"path": data["s2"], "band": 1, "max_px": 64}))
    assert 2 <= g["width"] <= 64 and 2 <= g["height"] <= 64
    values = np.frombuffer(base64.b64decode(g["values"]), "<f4")
    assert values.size == g["width"] * g["height"]
    assert g["min"] <= np.nanmax(values) <= g["max"] + 1e-3
    (s, w), (n, e) = g["bounds"]
    assert s < n and w < e
    assert client.get("/api/rasters/grid", params={"path": data["s2"], "band": 99}).status_code == 400


def test_about(client):
    a = ok(client.get("/api/about"))
    from lulc_fetch import __version__
    assert a["version"] == __version__
    assert a["run"]["mode"] in ("desktop app", "from source")
    assert a["libraries"]["GDAL"] and a["libraries"]["rasterio"]
    assert {"workspace", "settings", "logs"} <= set(a["folders"])
    assert "password" not in str(a["accounts"]).lower()


def test_project_save_as_copies_files(client, data, home, tmp_path):
    """File ▸ Save as: a new project with the layers' files copied in at the same places."""
    state = {"layers": [{"type": "raster", "name": "s2", "path": data["s2"]}], "items": [],
             "maps": {"active": "m1", "maps": [{"id": "m1", "name": "Map", "kind": "2d"}]}}
    try:
        r = ok(client.post("/api/project/save-as", json={"name": "Saved copy", "folder": str(tmp_path), "state": state}))
        folder = tmp_path / "Saved copy"
        assert r["project"]["folder"] == str(folder)
        assert (folder / data["s2"]).is_file() and r["copied"] >= 1
        assert ok(client.get("/api/project"))["project"]["name"] == "Saved copy"
    finally:
        client.post("/api/project/close")


def test_elevation_profile(client, data):
    info = ok(client.get("/api/rasters/info", params={"path": data["s2"]}))
    (s, w), (n, e) = info["bounds"]
    line = [[w + (e - w) * 0.2, s + (n - s) * 0.5], [w + (e - w) * 0.8, s + (n - s) * 0.5]]
    r = ok(client.post("/api/rasters/profile", json={"path": data["s2"], "coords": line, "band": 1, "samples": 50}))
    assert len(r["distance"]) == len(r["value"]) == 50 and r["length"] > 0
    assert sum(v is not None for v in r["value"]) > 40
    assert client.post("/api/rasters/profile", json={"path": data["s2"], "coords": line, "band": 99}).status_code == 400
