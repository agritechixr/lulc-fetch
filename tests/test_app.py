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
    assert sorted(ids) == sorted(["embed", "embtrain", "embpredict", "embconvert", "embexplore", "agridisease", "agriguide", "library", "interp", "fcdata", "fctrain", "fcrun", "workflows", "assistant"])
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


def _fake_run(jid, endpoint, body, outputs, started):
    """A finished run in the History, as the job manager records it (its request kept for Run again)."""
    import json as _json

    from webapp import history
    from webapp import workspace as ws
    history._append({"type": "run", "id": jid, "kind": "test", "title": f"Run {jid}", "status": "done", "endpoint": endpoint,
                     "started": started, "finished": started + 1, "outputs": [str(ws.root() / o) for o in outputs]})
    history.requests_dir().mkdir(parents=True, exist_ok=True)
    (history.requests_dir() / f"{jid}.json").write_text(_json.dumps({"endpoint": endpoint, "body": body, "workspace": str(ws.root())}))


def test_workflow_from_history_links_steps_and_makes_inputs(client):
    area = {"type": "Polygon", "coordinates": [[[77.4, 12.9], [77.5, 12.9], [77.5, 13.0], [77.4, 12.9]]]}
    _fake_run("wfa1", "/api/jobs", {"kind": "labels", "aoi": area, "year": 2021}, ["downloads/wfa1/landcover.tif"], 1000)
    _fake_run("wfa2", "/api/analyze/export", {"path": "downloads/wfa1/landcover.tif", "indices": ["NDVI"], "clip": area}, ["analysis/x/out.tif"], 2000)
    wf = ok(client.post("/api/workflows/from-history", json={"job_ids": ["wfa2", "wfa1"]}))
    assert [s["endpoint"] for s in wf["steps"]] == ["/api/jobs", "/api/analyze/export"]   # in the order they ran
    assert wf["steps"][1]["body"]["path"] == {"$step": 0, "ext": ".tif", "nth": 0}           # the first step's output
    kinds = {i["type"] for i in wf["inputs"]}
    assert kinds == {"area", "value"} and len(wf["inputs"]) == 2                              # one area (used twice), the year
    assert wf["steps"][0]["body"]["aoi"] == wf["steps"][1]["body"]["clip"]
    saved = ok(client.put("/api/workflows/new", json={"workflow": {**wf, "name": "Land cover → NDVI"}}))
    listed = ok(client.get("/api/workflows"))["workflows"]
    assert any(w["id"] == saved["id"] and w["steps"] == 2 for w in listed)
    assert ok(client.get(f"/api/workflows/{saved['id']}"))["name"] == "Land cover → NDVI"
    ok(client.delete(f"/api/workflows/{saved['id']}"))
    assert client.get(f"/api/workflows/{saved['id']}").status_code == 404


def test_workflow_refuses_housekeeping_and_bad_links(client):
    bad = {"name": "x", "inputs": [], "steps": [{"endpoint": "/api/project/close", "body": {"a": 1}}]}
    assert client.put("/api/workflows/new", json={"workflow": bad}).status_code == 400
    loop = {"name": "x", "inputs": [], "steps": [{"endpoint": "/api/jobs", "body": {"path": {"$step": 0, "ext": ".tif", "nth": 0}}}]}
    assert client.put("/api/workflows/new", json={"workflow": loop}).status_code == 400
    missing = {"name": "x", "inputs": [], "steps": [{"endpoint": "/api/jobs", "body": {"path": {"$in": "in9"}}}]}
    assert client.put("/api/workflows/new", json={"workflow": missing}).status_code == 400


def _assistant_ready(monkeypatch, answers):
    """The Assistant with a stand-in local model that gives these answers in turn (and records what it was told)."""
    from webapp import assistant
    seen = []
    monkeypatch.setattr(assistant, "status", lambda: {"provider": "ollama", "model": "test-model", "ready": True})
    monkeypatch.setattr(assistant, "_ask_ollama", lambda model, system, msgs: (seen.append(msgs[-1]["content"]), answers.pop(0))[1])
    return seen


def test_assistant_plans_a_checked_workflow_and_fixes_its_mistakes(client, monkeypatch):
    import json as _json
    area = {"type": "Polygon", "coordinates": [[[77.4, 12.9], [77.5, 12.9], [77.5, 13.0], [77.4, 12.9]]]}
    bad = {"plan": "NDVI", "questions": [], "workflow": {"name": "NDVI", "inputs": [{"id": "in1", "label": "Image", "type": "file", "default": "uploads/x.tif"}],
           "steps": [{"title": "NDVI", "endpoint": "/api/analyze/export", "body": {"path": {"$in": "in1"}, "colour": "red"}}]}}
    good = {"plan": "NDVI inside the area, then a table.", "questions": [], "workflow": {"name": "NDVI table",
            "inputs": [{"id": "in1", "label": "Image", "type": "file", "default": "uploads/x.tif"}, {"id": "in2", "label": "Area", "type": "area", "default": area}],
            "steps": [{"title": "NDVI", "endpoint": "/api/analyze/export", "body": {"path": {"$in": "in1"}, "band_map": {"B04": 3, "B08": 4}, "indices": ["NDVI"], "clip": {"$in": "in2"}}},
                      {"title": "Table", "endpoint": "/api/tables/from-raster", "body": {"path": {"$step": 0, "ext": ".tif", "nth": 0}}}]}}
    seen = _assistant_ready(monkeypatch, [_json.dumps(bad), _json.dumps(good)])
    r = ok(client.post("/api/assistant/plan", json={"messages": [{"role": "user", "content": "NDVI table"}],
                                                  "context": {"layers": [{"name": "X", "type": "raster", "path": "uploads/x.tif"}]}}))
    assert r["fixes"] == 1 and r["problems"] == []
    assert "band_map" in seen[-1] and "colour" in seen[-1]      # the problems went back to the model
    assert [s["endpoint"] for s in r["workflow"]["steps"]] == ["/api/analyze/export", "/api/tables/from-raster"]
    assert {i["type"] for i in r["workflow"]["inputs"]} == {"file", "area"}


def test_assistant_flags_invented_files_and_wrong_input_kinds(client, monkeypatch):
    import json as _json
    wrong = {"plan": "x", "questions": [], "workflow": {"name": "x", "inputs": [
        {"id": "in1", "label": "Area", "type": "area", "default": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
        {"id": "in2", "label": "Map", "type": "file", "default": "uploads/made_up.tif"}],
        "steps": [{"title": "s", "endpoint": "/api/tables/from-raster", "body": {"path": {"$in": "in1"}}}]}}
    _assistant_ready(monkeypatch, [_json.dumps(wrong)] * 3)
    r = ok(client.post("/api/assistant/plan", json={"messages": [{"role": "user", "content": "x"}], "context": {"layers": []}}))
    text = " ".join(r["problems"])
    assert "made_up.tif" in text and "not the area in1" in text and r["fixes"] == 2


def test_assistant_needs_setting_up_first(client, monkeypatch):
    from webapp import assistant
    monkeypatch.setattr(assistant, "status", lambda: {"provider": "ollama", "model": "m", "ready": False})
    r = client.post("/api/assistant/plan", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400 and "isn't set up" in r.json()["detail"]
    assert "/api/analyze/export" in ok(client.get("/api/assistant/catalog"))["text"]
