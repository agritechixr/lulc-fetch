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
    assert sorted(ids) == sorted(["embed", "embtrain", "embpredict", "embconvert", "embexplore", "agridisease", "agriguide", "library", "interp", "fcdata", "fctrain", "fcrun", "workflows", "assistant", "vbuffer", "vquery", "voverlay", "vdissolve", "vzonal", "vlocation", "vsjoin", "vgeometry", "vcount", "vtjoin", "rterrain", "rcontours", "rreclass", "rchange", "rclip", "rresample", "renhance", "r2poly", "r2line", "r2point", "rasterize", "vconvert", "areastats", "accuracy", "rcalc", "timeseries", "georef", "vhelpers", "online", "field", "rmosaic", "rburn", "vstats", "fmember", "foverlay", "fboundary", "fcmeans"])
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


def test_workflow_schedules(client):
    """A schedule: its next run (daily at a time, weekly, every few hours), a missed run is due once, the run is recorded."""
    import datetime as dt
    import time as _t

    from webapp import workflows
    wf = {"name": "Weekly NDVI", "inputs": [{"id": "in1", "label": "End date", "type": "value", "default": "2025-01-31"}],
          "steps": [{"title": "Area", "endpoint": "/api/assess/area-stats", "body": {"raster": "uploads/x.tif", "name": {"$in": "in1"}}}]}
    wid = ok(client.put("/api/workflows/new", json={"workflow": wf}))["id"]
    s = ok(client.put(f"/api/workflows/{wid}/schedule", json={"schedule": {"every": "day", "at": "07:30", "relative_dates": {"in1": 0},
                                                                         "alert": {"field": "summary.mean", "op": "<", "value": 0.3}}}))
    nxt = dt.datetime.fromtimestamp(s["next_run"])
    assert (nxt.hour, nxt.minute) == (7, 30) and 0 < s["next_run"] - _t.time() <= 86400 and s["relative_dates"] == {"in1": 0}
    wed = dt.datetime(2026, 10, 7, 12, 0).timestamp()   # a Wednesday, noon
    assert dt.datetime.fromtimestamp(workflows.next_run({"every": "week", "weekday": 0, "at": "06:00"}, wed)).strftime("%a %d %H:%M") == "Mon 12 06:00"
    assert workflows.next_run({"every": "hours", "hours": 6}, wed) == wed + 6 * 3600
    lst = ok(client.get("/api/workflows/schedules"))["schedules"]
    assert [x["id"] for x in lst] == [wid] and not lst[0]["due"] and lst[0]["name"] == "Weekly NDVI"
    data = workflows._sched_all(); data[wid]["next_run"] = _t.time() - 7200; workflows._sched_write(data)   # missed while closed
    assert ok(client.get("/api/workflows/schedules"))["schedules"][0]["due"]
    r = ok(client.post(f"/api/workflows/{wid}/schedule/ran", json={"ok": True, "message": "summary.mean = 0.21 (< 0.3)", "alert": True}))
    assert r["runs"][0]["alert"] and r["next_run"] > _t.time() and not ok(client.get("/api/workflows/schedules"))["schedules"][0]["due"]
    for bad in ({"every": "month"}, {"every": "day", "at": "25:00"}, {"every": "hours", "hours": 0}, {"every": "week", "at": "07:00"},
                {"every": "day", "at": "07:00", "alert": {"field": "x", "op": "~", "value": 1}}):
        assert client.put(f"/api/workflows/{wid}/schedule", json={"schedule": bad}).status_code == 400, bad
    ok(client.delete(f"/api/workflows/{wid}"))
    assert ok(client.get("/api/workflows/schedules"))["schedules"] == []                 # a deleted workflow's schedule goes too


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


def test_assistant_plans_with_an_online_openai_compatible_model(client):
    """A stand-in OpenAI-compatible server (like LM Studio, Groq or Hugging Face's router) on this computer: the plan
    comes back through /chat/completions, a server without JSON mode is asked again without it, models are listed,
    and a refused key says where to fix it."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    plan = {"plan": "A buffer of 100 m.", "questions": [], "remember": [], "workflow": {"name": "Buffer",
            "inputs": [{"id": "in1", "label": "Fields", "type": "file", "default": "uploads/f.geojson"}],
            "steps": [{"title": "Buffer", "endpoint": "/api/vector/buffer", "body": {"layer": {"$in": "in1"}, "distance": 100}}]}}
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            b = _json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            self._send(200, {"data": [{"id": "tiny-model"}, {"id": "other-model"}]})

        def do_POST(self):
            body = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            if body["model"] == "locked":
                return self._send(401, {"error": {"message": "bad key"}})
            if "response_format" in body:
                return self._send(400, {"error": {"message": "response_format is not supported"}})
            self._send(200, {"choices": [{"message": {"content": "Here it is:\n" + _json.dumps(plan)}, "finish_reason": "stop"}]})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/v1"
    try:
        st = ok(client.put("/api/assistant/settings", json={"provider": "api", "api_preset": "custom", "api_base": base, "api_model": "tiny-model"}))
        assert st["ready"] and st["provider"] == "api" and st["model"] == "tiny-model"
        assert ok(client.get("/api/assistant/api-models", params={"preset": "custom", "base": base}))["models"] == ["other-model", "tiny-model"]
        r = ok(client.post("/api/assistant/plan", json={"messages": [{"role": "user", "content": "buffer my fields by 100 m"}],
                                                      "context": {"layers": [{"name": "Fields", "type": "vector", "path": "uploads/f.geojson"}]}}))
        assert r["problems"] == [] and r["workflow"]["steps"][0]["endpoint"] == "/api/vector/buffer"
        assert "response_format" in seen[0] and "response_format" not in seen[1]         # asked again without JSON mode
        assert seen[1]["messages"][0]["role"] == "system" and "schema" in seen[1]["messages"][0]["content"]
        ok(client.put("/api/assistant/settings", json={"provider": "api", "api_preset": "custom", "api_base": base, "api_model": "locked"}))
        bad = client.post("/api/assistant/plan", json={"messages": [{"role": "user", "content": "x"}]})
        assert bad.status_code == 400 and "refused the key" in bad.json()["detail"]
        assert client.put("/api/assistant/settings", json={"provider": "api", "api_preset": "custom", "api_base": "ftp://x", "api_model": "m"}).status_code == 400
        st = ok(client.put("/api/assistant/settings", json={"provider": "api", "api_preset": "groq"}))
        assert st["model"] == "llama-3.3-70b-versatile" and not st["ready"]               # no Groq key: not ready
    finally:
        srv.shutdown()
        client.put("/api/assistant/settings", json={"provider": "ollama"})


def test_assistant_needs_setting_up_first(client, monkeypatch):
    from webapp import assistant
    monkeypatch.setattr(assistant, "status", lambda: {"provider": "ollama", "model": "m", "ready": False})
    r = client.post("/api/assistant/plan", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400 and "isn't set up" in r.json()["detail"]
    assert "/api/analyze/export" in ok(client.get("/api/assistant/catalog"))["text"]


def test_assistant_looks_at_the_data(client, data):
    """GISclaw's schema analysis / observation: bands with value ranges, a table's columns and rows, warnings."""
    from webapp import assistant_data
    r = ok(client.post("/api/assistant/observe", json={"paths": [data["s2"], "uploads/nothing.tif"]}))["files"]
    assert r[0]["kind"] == "raster" and r[0]["bands_total"] >= 3 and "min" in r[0]["bands"][0]
    assert "error" in r[1]
    from webapp import workspace as ws
    t = ws.root() / "tables" / "empty_t.csv"
    t.parent.mkdir(exist_ok=True)
    t.write_text("a,b\n")
    d = assistant_data.describe("tables/empty_t.csv")
    assert d["kind"] == "table" and d["rows"] == 0 and d["warnings"]


def test_assistant_replans_after_a_failure_and_remembers(client, monkeypatch):
    """Plan → Execute → Replan: the done steps are kept, the new ones come after them; the error is remembered and
    shown to later plans as a known pitfall; what the user asks to remember goes into the notes."""
    import json as _json
    from webapp import assistant_data
    rest = {"plan": "Table of the NDVI instead.", "questions": [], "remember": ["My farm is the Fields layer"], "workflow": {"name": "x", "inputs": [],
            "steps": [{"title": "Table", "endpoint": "/api/tables/from-raster", "body": {"path": {"$step": 0, "ext": ".tif", "nth": 0}, "factor": 2}}]}}
    seen = _assistant_ready(monkeypatch, [_json.dumps(rest), _json.dumps(rest)])
    done = [{"title": "NDVI", "endpoint": "/api/analyze/export", "body": {"path": "uploads/x.tif", "band_map": {"B04": 3, "B08": 4}, "indices": ["NDVI"]},
             "observation": [{"kind": "raster", "bands": [{"min": 0.1, "max": 0.8}]}]}]
    failed = {"title": "Clusters", "endpoint": "/api/unsup/cluster", "body": {"table": "x"}, "error": "features: field required", "history": []}
    r = ok(client.post("/api/assistant/continue", json={"messages": [{"role": "user", "content": "NDVI then cluster"}], "context": {},
                                                      "workflow": {}, "done": done, "failed": failed, "conv_id": "test1234"}))
    assert [s["endpoint"] for s in r["workflow"]["steps"]] == ["/api/analyze/export", "/api/tables/from-raster"]   # step [0] kept
    assert "failed with: features: field required" in seen[-1] and "[0] NDVI" in seen[-1]
    assert r["remembered"] == ["My farm is the Fields layer"] and "Fields layer" in assistant_data.notes()
    assert any(p["endpoint"] == "/api/unsup/cluster" for p in assistant_data.pitfalls())
    from webapp import assistant
    prompt = assistant._memory_text("cluster", {})
    assert "Known pitfalls" in prompt and "features: field required" in prompt and "Fields layer" in prompt
    mem = ok(client.get("/api/assistant/memory"))
    assert any(c["id"] == "test1234" for c in mem["conversations"])
    ok(client.put("/api/assistant/memory/notes", json={"notes": ""}))
    ok(client.delete("/api/assistant/memory/pitfalls"))
    assert assistant_data.pitfalls() == [] and assistant_data.notes() == ""


def test_assistant_renumbers_steps_counted_from_one():
    from webapp.assistant import _renumber
    steps = [{"body": {"path": "a"}}, {"body": {"table": {"$step": 1, "ext": ".csv", "nth": 0}}}, {"body": {"x": [{"$step": 2, "ext": ".csv", "nth": 0}]}}]
    _renumber(steps, 0)
    assert steps[1]["body"]["table"]["$step"] == 0 and steps[2]["body"]["x"][0]["$step"] == 1
    ok_steps = [{"body": {}}, {"body": {"t": {"$step": 0}}}]
    _renumber(ok_steps, 0)
    assert ok_steps[1]["body"]["t"]["$step"] == 0       # already right: unchanged


def test_assistant_moves_an_area_to_the_tools_own_name():
    from webapp.assistant import _area_names, _openapi
    body = {"kind": "labels", "clip": {"type": "Polygon", "coordinates": []}}
    _area_names(_openapi(), "/api/jobs", body)
    assert "aoi" in body and "clip" not in body
    body2 = {"path": "x", "aoi": {"type": "Polygon"}}
    _area_names(_openapi(), "/api/analyze/export", body2)
    assert "clip" in body2 and "aoi" not in body2


def _sq(x0, y0, d, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d], [x0, y0]]]}}


def _area_m2(geom):
    from rasterio.warp import transform_geom
    from shapely.geometry import shape
    return shape(transform_geom("EPSG:4326", "EPSG:32643", geom)).area


def test_vector_buffer_query_overlay_dissolve(client):
    """Buffer (metres), select by attribute (also SQL-like), overlay (intersection, union, difference) and dissolve,
    as jobs whose results are GeoJSON files in the workspace."""
    import math

    from tests.helpers import run
    pt = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"id": 1}, "geometry": {"type": "Point", "coordinates": [77.5, 13.0]}}]}
    r = run(client, "/api/vector/buffer", {"layer": pt, "distance": 100, "segments": 64})
    fc = ok(client.get("/api/vector/read", params={"path": r["path"]}))
    assert abs(_area_m2(fc["features"][0]["geometry"]) - math.pi * 100 ** 2) / (math.pi * 100 ** 2) < 0.01   # π r², within 1 %
    fields = {"type": "FeatureCollection", "features": [_sq(77.50, 13.0, 0.01, crop="Rice", area_ha=3), _sq(77.52, 13.0, 0.01, crop="wheat", area_ha=1),
                                                         _sq(77.54, 13.0, 0.01, crop="rice", area_ha=1)]}
    sel = run(client, "/api/vector/query", {"layer": fields, "where": "crop = 'rice' AND area_ha > 2"})
    assert sel["features"] == 1
    assert run(client, "/api/vector/query", {"layer": fields, "where": "crop == rice or crop in ('wheat',)"})["features"] == 3
    assert client.post("/api/vector/query", json={"layer": fields, "where": "__import__('os')"}).status_code == 400
    a = {"type": "FeatureCollection", "features": [_sq(77.50, 13.0, 0.02, name="A")]}
    b = {"type": "FeatureCollection", "features": [_sq(77.51, 13.0, 0.02, name="B")]}
    inter = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/overlay", {"a": a, "b": b, "how": "intersection"})["path"]}))
    assert len(inter["features"]) == 1 and inter["features"][0]["properties"] == {"name": "A", "b_name": "B"}
    ia, aa = _area_m2(inter["features"][0]["geometry"]), _area_m2(a["features"][0]["geometry"])
    assert abs(ia / aa - 0.5) < 0.01                                                                  # half of A overlaps B
    union = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/overlay", {"a": a, "b": b, "how": "union"})["path"]}))
    assert len(union["features"]) == 3 and abs(sum(_area_m2(f["geometry"]) for f in union["features"]) / aa - 1.5) < 0.01
    dflt = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/buffer", {"layer": pt, "distance": 50})["path"]}))
    assert abs(_area_m2(dflt["features"][0]["geometry"]) / (math.pi * 50 ** 2) - 1) < 0.005                # the default circle is round enough
    tiny_and_big = {"type": "FeatureCollection", "features": [_sq(77.50, 13.0, 0.00001, n=1), _sq(77.52, 13.0, 0.001, n=2)]}   # ~1 m and ~110 m
    shrunk = run(client, "/api/vector/buffer", {"layer": tiny_and_big, "distance": -2})
    assert shrunk["features"] == 1                                                                    # the 1 m square vanishes, no crash
    j = ok(client.post("/api/vector/buffer", json={"layer": pt, "distance": -5}))
    from tests.helpers import wait as _w
    with pytest.raises(BaseException, match="only shrinks polygons"):
        _w(client, j)
    diff = run(client, "/api/vector/overlay", {"a": a, "b": b, "how": "difference"})
    assert diff["features"] == 1
    one = run(client, "/api/vector/dissolve", {"layer": fields})
    assert one["features"] == 1
    by = run(client, "/api/vector/dissolve", {"layer": fields, "field": "crop"})
    assert by["features"] == 3                                                                        # Rice, wheat, rice (as written)


def test_vector_batch1_zonal_location_join_geometry_count_table(client, home):
    """Zonal statistics (known pixel values), select by location (and within a distance), spatial join, calculate
    geometry (area in ha), count points in polygons, join a table by a field."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from tests.helpers import run
    rd = home / "uploads"
    rd.mkdir(exist_ok=True)
    # a 10 × 10 raster of 100 m pixels in UTM 43N: left half 1, right half 3 (one class map, one value map)
    with rasterio.open(rd / "zs.tif", "w", driver="GTiff", width=10, height=10, count=1, dtype="float32", crs="EPSG:32643",
                       transform=from_origin(700000, 1500000, 100, 100)) as d:
        a = np.ones((10, 10), "float32"); a[:, 5:] = 3; d.write(a, 1)
    from rasterio.warp import transform_geom
    def poly_utm(x0, y0, x1, y1, **p):
        g = transform_geom("EPSG:32643", "EPSG:4326", {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]})
        return {"type": "Feature", "properties": p, "geometry": g}
    zones = {"type": "FeatureCollection", "features": [poly_utm(700000, 1499000, 700500, 1500000, id="left"),       # all 1s
                                                        poly_utm(700000, 1499000, 701000, 1500000, id="whole")]}    # half 1, half 3
    fc = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/zonal", {"layer": zones, "raster": "uploads/zs.tif"})["path"]}))
    left, whole = (f["properties"] for f in fc["features"])
    assert abs(left["mean"] - 1) < 1e-6 and abs(whole["mean"] - 2) < 0.05 and whole["min"] == 1 and whole["max"] == 3
    assert 45 <= left["count"] <= 55 and 95 <= whole["count"] <= 105
    cat = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/zonal", {"layer": zones, "raster": "uploads/zs.tif", "categorical": True})["path"]}))
    w = cat["features"][1]["properties"]
    assert abs(w["pct_1"] - 50) < 3 and abs(w["pct_3"] - 50) < 3
    # geometry: the left zone is 500 m × 1 km = 50 ha
    geo = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/geometry", {"layer": zones})["path"]}))
    assert abs(geo["features"][0]["properties"]["area_ha"] - 50) < 0.5
    # points: one in the left zone, one 150 m east of the whole zone
    from rasterio.warp import transform as wt
    (x1, x2), (y1, y2) = wt("EPSG:32643", "EPSG:4326", [700200, 701150], [1499500, 1499500])
    pts = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"n": 2}, "geometry": {"type": "Point", "coordinates": [x1, y1]}},
                                                     {"type": "Feature", "properties": {"n": 5}, "geometry": {"type": "Point", "coordinates": [x2, y2]}}]}
    assert run(client, "/api/vector/select-location", {"a": pts, "b": zones, "predicate": "within"})["features"] == 1
    assert run(client, "/api/vector/select-location", {"a": pts, "b": zones, "predicate": "within_distance", "distance": 200})["features"] == 2
    cnt = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/count-points", {"polygons": zones, "points": pts, "sum_field": "n"})["path"]}))
    assert [f["properties"]["point_count"] for f in cnt["features"]] == [1, 1] and cnt["features"][0]["properties"]["sum_n"] == 2
    sj = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/spatial-join", {"a": pts, "b": zones, "how": "nearest"})["path"]}))
    assert sj["features"][1]["properties"]["id"] == "whole" and 140 < sj["features"][1]["properties"]["join_dist_m"] < 160
    (home / "tables").mkdir(exist_ok=True)
    (home / "tables" / "yields.csv").write_text("zone,yield\nLEFT,4.5\nwhole,3.1\n")
    r = run(client, "/api/vector/join-table", {"layer": zones, "table": "tables/yields.csv", "layer_field": "id", "table_field": "zone"})
    jt = ok(client.get("/api/vector/read", params={"path": r["path"]}))
    assert r["unmatched"] == 0 and [f["properties"]["yield"] for f in jt["features"]] == [4.5, 3.1]


def test_raster_tools_terrain_contours_reclassify_change_clip(client, home):
    """Terrain on a plane tilting up to the south-east (slope and aspect known), contours of it, reclassify, change
    between two dates (values and classes), clip to a polygon."""
    import math

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import transform_geom

    from tests.helpers import run
    (home / "uploads").mkdir(exist_ok=True)
    n, px = 50, 10.0
    yy, xx = np.mgrid[0:n, 0:n] * px
    z = (0.1 * xx + 0.1 * yy).astype("float32")             # rises 0.1 m per m east and per m south (rows run south)
    prof = dict(driver="GTiff", width=n, height=n, count=1, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1500000, px, px))
    with rasterio.open(home / "uploads/plane.tif", "w", **prof) as d:
        d.write(z, 1)
    outs = run(client, "/api/raster/terrain", {"dem": "uploads/plane.tif"})["outputs"]
    vals = {}
    for o in outs:
        with rasterio.open(home / o) as s:
            vals[o.split("_")[-1]] = float(np.median(s.read(1)[5:-5, 5:-5]))
    assert abs(vals["slope.tif"] - math.degrees(math.atan(math.hypot(0.1, 0.1)))) < 0.1     # 8.05°
    assert abs(vals["aspect.tif"] - 315) < 1                                                # faces north-west (downhill)
    assert vals["hillshade.tif"] > 200                                                      # lit from the north-west: bright
    c = run(client, "/api/raster/contours", {"dem": "uploads/plane.tif", "interval": 20})
    fc = ok(client.get("/api/vector/read", params={"path": c["path"]}))
    assert c["features"] >= 4 and {f["properties"]["value"] for f in fc["features"]} >= {20.0, 40.0, 60.0}
    r = run(client, "/api/raster/reclassify", {"raster": "uploads/plane.tif", "rules": [{"max": 40, "value": 1, "label": "low"}, {"min": 40, "value": 2, "label": "high"}]})
    info = ok(client.get("/api/rasters/info", params={"path": r["path"]}))
    with rasterio.open(home / r["path"]) as s:
        cls = s.read(1)
        assert set(np.unique(cls)) == {1, 2} and s.tags()["classes"] and s.colormap(1)[2]
    assert cls[0, 0] == 1 and cls[-1, -1] == 2
    with rasterio.open(home / "uploads/plane2.tif", "w", **prof) as d:
        d.write(z + 5, 1)
    ch = run(client, "/api/raster/change", {"before": "uploads/plane.tif", "after": "uploads/plane2.tif"})
    assert abs(ch["summary"]["mean_change"] - 5) < 1e-3 and ch["summary"]["increased_pct"] == 100
    p2 = {**prof, "dtype": "uint8"}
    for nm, k in (("lc_a.tif", 1), ("lc_b.tif", 1)):
        with rasterio.open(home / "uploads" / nm, "w", **p2) as d:
            a = np.full((n, n), k, "uint8")
            if nm == "lc_b.tif":
                a[:, :25] = 2                                                               # half of class 1 became 2
            d.write(a, 1)
    cc = run(client, "/api/raster/change", {"before": "uploads/lc_a.tif", "after": "uploads/lc_b.tif", "categorical": True})
    assert abs(cc["summary"]["changed_pct"] - 50) < 1 and abs(cc["summary"]["changed_ha"] - 12.5) < 0.1   # 1,250 px × 0.01 ha
    assert any(t["from"] == 1 and t["to"] == 2 for t in cc["transitions"]) and cc["csv"].startswith("tables/")
    area = transform_geom("EPSG:32643", "EPSG:4326", {"type": "Polygon", "coordinates": [[[700000, 1499800], [700200, 1499800], [700200, 1500000], [700000, 1500000], [700000, 1499800]]]})
    cl = run(client, "/api/raster/clip", {"raster": "uploads/plane.tif", "area": area})
    with rasterio.open(home / cl["path"]) as s:
        assert 19 <= s.width <= 22 and 19 <= s.height <= 22                                 # 200 m / 10 m
    # resampling: to 20 m (half the pixels each way), ×2 with cubic, a class map keeps its classes, a bad method → 400
    r20 = run(client, "/api/raster/resample", {"raster": "uploads/plane.tif", "res": 20, "method": "average"})
    with rasterio.open(home / r20["path"]) as s:
        assert s.width == 25 and abs(s.res[0] - 20) < 1e-9
    up = run(client, "/api/raster/resample", {"raster": "uploads/plane.tif", "scale": 2, "method": "bicubic"})
    assert up["size"] == [100, 100] and up["method"] == "bicubic"
    lc = run(client, "/api/raster/resample", {"raster": "uploads/lc_b.tif", "res": 30})
    with rasterio.open(home / lc["path"]) as s:
        assert lc["method"] == "mode" and set(np.unique(s.read(1))) <= {1, 2}
    utm = run(client, "/api/raster/resample", {"raster": "uploads/plane.tif", "crs": "EPSG:4326", "method": "lanczos"})
    with rasterio.open(home / utm["path"]) as s:
        assert s.crs.to_epsg() == 4326
    assert client.post("/api/raster/resample", json={"raster": "uploads/plane.tif", "res": 20, "method": "magic"}).status_code == 422
    assert client.post("/api/raster/resample", json={"raster": "uploads/plane.tif"}).status_code == 400
    assert client.post("/api/raster/change", json={"before": "uploads/plane.tif", "after": "uploads/plane2.tif", "resampling": "nope"}).status_code == 422
    ch2 = run(client, "/api/raster/change", {"before": "uploads/plane.tif", "after": "uploads/plane2.tif", "resampling": "cubic"})
    assert abs(ch2["summary"]["mean_change"] - 5) < 1e-3
    # enhancement: stretch to 0–1, CLAHE, sharpen, edges; ×2 upscale; majority keeps a class map's classes
    en = run(client, "/api/raster/enhance", {"raster": "uploads/plane.tif", "steps": [{"op": "stretch"}, {"op": "clahe"}, {"op": "sharpen"}]})
    with rasterio.open(home / en["path"]) as s:
        a = s.read(1)
        assert s.dtypes[0] == "float32" and a.min() >= -0.6 and a.max() <= 1.6 and a.std() > 0.1
    ed = run(client, "/api/raster/enhance", {"raster": "uploads/plane.tif", "steps": [{"op": "sobel"}], "upscale": 2, "upscale_method": "lanczos"})
    with rasterio.open(home / ed["path"]) as s:
        assert s.width == 100 and abs(s.res[0] - 5) < 1e-9
    noisy = np.ones((n, n), "uint8"); noisy[10, 10] = 2; noisy[30:, :] = 2
    with rasterio.open(home / "uploads/lc_noisy.tif", "w", **p2) as d:
        d.write(noisy, 1); d.write_colormap(1, {1: (0, 128, 0, 255), 2: (200, 200, 0, 255)})
    mj = run(client, "/api/raster/enhance", {"raster": "uploads/lc_noisy.tif", "steps": [{"op": "majority", "size": 3}]})
    with rasterio.open(home / mj["path"]) as s:
        m = s.read(1)
        assert s.dtypes[0] == "uint8" and m[10, 10] == 1 and m[40, 5] == 2 and s.colormap(1)[2][:3] == (200, 200, 0)
    assert client.post("/api/raster/enhance", json={"raster": "uploads/plane.tif", "steps": [{"op": "blur_magic"}]}).status_code == 422
    assert client.post("/api/raster/enhance", json={"raster": "uploads/plane.tif", "steps": []}).status_code == 400
    assert len(ok(client.get("/api/raster/resampling-methods"))["methods"]) >= 10


def test_conversion_tools(client, home):
    """Raster → polygons (classes, sieve, dissolve), boundaries, centrelines of a thin line, points; rasterize a text
    field, a count and a grid like a raster; polygons ↔ lines, vertices, points → line, points along, segments, boxes."""
    import json

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from tests.helpers import run
    (home / "uploads").mkdir(exist_ok=True)
    n = 40
    a = np.ones((n, n), "uint8"); a[:, 20:] = 2; a[5, 5] = 2               # two halves, and one stray pixel
    prof = dict(driver="GTiff", width=n, height=n, count=1, dtype="uint8", crs="EPSG:32643", transform=from_origin(700000, 1500000, 10, 10), nodata=0)
    with rasterio.open(home / "uploads/cls.tif", "w", **prof) as d:
        d.write(a, 1); d.update_tags(classes=json.dumps({"1": "crop", "2": "forest"}))
    rd = lambda r: ok(client.get("/api/vector/read", params={"path": r["path"]}))
    poly = rd(run(client, "/api/convert/raster-to-polygon", {"raster": "uploads/cls.tif", "dissolve": True}))
    by = {f["properties"]["value"]: f["properties"] for f in poly["features"]}
    assert set(by) == {1, 2} and by[2]["class"] == "forest" and abs(by[1]["area_ha"] + by[2]["area_ha"] - 16) < 0.01   # 400 m × 400 m
    zn = rd(run(client, "/api/vector/zonal", {"layer": poly, "raster": "uploads/cls.tif", "categorical": True}))
    z1 = next(f["properties"] for f in zn["features"] if f["properties"]["value"] == 1)
    assert z1["pct_crop"] > 99 and z1["majority_class"] == "crop"           # class names, not pct_1
    sieved = rd(run(client, "/api/convert/raster-to-polygon", {"raster": "uploads/cls.tif", "min_area": 300}))
    assert len(sieved["features"]) == 2                                      # the stray pixel merged away
    edges = run(client, "/api/convert/raster-to-polyline", {"raster": "uploads/cls.tif", "min_length": 1})
    assert edges["features"] >= 1
    road = np.zeros((n, n), "uint8"); road[18:23, 3:37] = 1                   # a 5-pixel-wide road, 34 pixels long
    with rasterio.open(home / "uploads/road.tif", "w", **prof) as d:
        d.write(road, 1)
    cl = rd(run(client, "/api/convert/raster-to-polyline", {"raster": "uploads/road.tif", "mode": "centrelines", "min_length": 50}))
    assert len(cl["features"]) == 1 and 250 < cl["features"][0]["properties"]["length_m"] < 360
    pts = rd(run(client, "/api/convert/raster-to-point", {"raster": "uploads/cls.tif", "step": 4}))
    assert len(pts["features"]) == 100 and {f["properties"]["class"] for f in pts["features"]} == {"crop", "forest"}
    from lulc_fetch import convert
    with rasterio.open(home / "uploads/ndvi.tif", "w", **{**prof, "dtype": "float32", "nodata": None}) as d:
        d.write(np.random.default_rng(0).random((n, n), dtype="float32"), 1)
    with pytest.raises(ValueError, match="Reclassify"):
        convert.raster_to_polygons(home / "uploads/ndvi.tif")                # continuous values: classes first
    sq = lambda x, y, d, **p: {"type": "Feature", "properties": p, "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}}
    fields = {"type": "FeatureCollection", "features": [sq(77.50, 13.0, 0.01, crop="rice", yld=4.2), sq(77.52, 13.0, 0.01, crop="maize", yld=3.1)]}
    r = run(client, "/api/convert/rasterize", {"layer": fields, "field": "crop", "res": 20})
    with rasterio.open(home / r["path"]) as s:
        v = s.read(1)
        assert set(np.unique(v)) == {0, 1, 2} and json.loads(s.tags()["classes"]) == {"1": "maize", "2": "rice"} and s.colormap(1)[1]
    r2 = run(client, "/api/convert/rasterize", {"layer": fields, "field": "yld", "res": 50})
    with rasterio.open(home / r2["path"]) as s:
        assert s.dtypes[0] == "float32" and abs(s.read(1).max() - 4.2) < 1e-5
    dots = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"t": i, "car": "A" if i < 3 else "B"}, "geometry": {"type": "Point", "coordinates": [77.5 + 0.001 * i, 13.0]}} for i in range(6)]}
    cnt = run(client, "/api/convert/rasterize", {"layer": dots, "mode": "count", "like": "uploads/cls.tif"})
    assert cnt["size"] == [40, 40]
    with rasterio.open(home / run(client, "/api/convert/rasterize", {"layer": fields, "mode": "count", "res": 5000})["path"]) as s:
        assert s.read(1).sum() == 2                                                              # polygons smaller than a cell count once each
    assert client.post("/api/convert/rasterize", json={"layer": fields, "mode": "value", "res": 10}).status_code == 400
    conv = lambda op, lay, **kw: rd(run(client, "/api/convert/features", {"op": op, "layer": lay, **kw}))
    lines = conv("polygons_to_lines", fields)
    assert lines["features"][0]["geometry"]["type"] in ("LineString", "MultiLineString")
    back = conv("lines_to_polygons", lines)
    assert len(back["features"]) == 2 and back["features"][0]["properties"]["area_ha"] > 100
    assert len(conv("vertices_to_points", fields)["features"]) == 8
    tracks = conv("points_to_lines", dots, group_by="car", order_by="t")
    assert len(tracks["features"]) == 2 and all(f["properties"]["points"] == 3 for f in tracks["features"])
    along = conv("points_along_lines", lines, distance=500)
    assert 18 <= len(along["features"]) <= 22                                # 2 × ~4.4 km of outlines / 500 m, + the ends
    assert len(conv("split_lines", fields)["features"]) == 8
    assert len(conv("bounding_boxes", fields, whole=True)["features"]) == 1


def test_area_statistics_and_accuracy_assessment(client, home):
    """Area per class; stratified points; a confusion matrix and Olofsson's area estimates that we can work out by hand."""
    import json

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from tests.helpers import run
    (home / "uploads").mkdir(exist_ok=True)
    a = np.ones((100, 100), "uint8"); a[:, 75:] = 2                              # 75 % class 1 (crop), 25 % class 2 (forest)
    prof = dict(driver="GTiff", width=100, height=100, count=1, dtype="uint8", crs="EPSG:32643", transform=from_origin(700000, 1500000, 10, 10), nodata=0)
    with rasterio.open(home / "uploads/map.tif", "w", **prof) as d:
        d.write(a, 1); d.update_tags(classes=json.dumps({"1": "crop", "2": "forest"}))
    st = run(client, "/api/assess/area-stats", {"raster": "uploads/map.tif"})
    by = {c["class"]: c for c in st["classes"]}
    assert abs(by["crop"]["area_ha"] - 75) < 1e-6 and abs(by["forest"]["percent"] - 25) < 1e-6 and st["csv"].startswith("tables/")
    smp = run(client, "/api/assess/sample", {"raster": "uploads/map.tif", "per_class": 20, "seed": 3})
    fc = ok(client.get("/api/vector/read", params={"path": smp["path"]}))
    assert len(fc["features"]) == 40 and all(f["properties"]["reference"] == "" for f in fc["features"])
    # label: all forest points right; 4 of the 20 crop points are really forest
    crop = [f["properties"] for f in fc["features"] if f["properties"]["map_class"] == "crop"]
    for p in (f["properties"] for f in fc["features"]):
        p["reference"] = p["map_class"]
    for p in crop[:4]:
        p["reference"] = "forest"
    r = run(client, "/api/assess/accuracy", {"raster": "uploads/map.tif", "points": fc})
    assert r["matrix"] == [[16, 4], [0, 20]] and r["overall_accuracy"] == 0.9
    w = {c["class"]: c for c in r["weighted"]["classes"]}
    # Olofsson: forest share = 0.75 · 4/20 + 0.25 · 20/20 = 0.40 → 40 ha of 100; overall = 0.75 · 0.8 + 0.25 = 0.85
    assert abs(w["forest"]["estimated_area_ha"] - 40) < 1e-6 and abs(r["weighted"]["overall_accuracy"] - 0.85) < 1e-9
    assert w["forest"]["ci95_ha"] > 0 and r["report"].endswith(".html") and (home / r["report"]).read_text().count("Olofsson")
    unlabelled = client.post("/api/assess/accuracy", json={"raster": "uploads/map.tif", "points": fc, "ref_field": "nope"})
    assert unlabelled.status_code == 200   # (the job fails: no such field)


def test_raster_calculator(client, home):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from tests.helpers import run
    (home / "uploads").mkdir(exist_ok=True)
    prof = dict(driver="GTiff", width=20, height=20, count=2, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1500000, 10, 10))
    with rasterio.open(home / "uploads/two.tif", "w", **prof) as d:
        d.write(np.full((20, 20), 0.1, "float32"), 1); d.write(np.full((20, 20), 0.5, "float32"), 2)
    coarse = {**prof, "count": 1, "width": 10, "height": 10, "transform": from_origin(700000, 1500000, 20, 20)}
    with rasterio.open(home / "uploads/coarse.tif", "w", **coarse) as d:
        d.write(np.arange(100, dtype="float32").reshape(10, 10), 1)
    nd = run(client, "/api/raster/calc", {"variables": {"R": {"path": "uploads/two.tif", "band": 1}, "N": {"path": "uploads/two.tif", "band": 2}},
                                          "expression": "(N - R) / (N + R)", "name": "ndvi"})
    with rasterio.open(home / nd["path"]) as s:
        assert abs(float(s.read(1).mean()) - 0.4 / 0.6) < 1e-5
    m = run(client, "/api/raster/calc", {"variables": {"A": {"path": "uploads/two.tif", "band": 2}, "C": {"path": "uploads/coarse.tif"}},
                                         "expression": "(C > 49) and A > 0.3"})
    with rasterio.open(home / m["path"]) as s:
        assert m["boolean"] and s.dtypes[0] == "uint8" and s.shape == (20, 20) and abs(m["summary"]["true_pct"] - 50) < 1
    for bad in ("__import__('os')", "A.real", "open(A)", "Z + 1", "A +"):
        assert client.post("/api/raster/calc", json={"variables": {"A": {"path": "uploads/two.tif"}}, "expression": bad}).status_code == 400, bad


def test_time_series_route(client, monkeypatch):
    from lulc_fetch import timeseries

    from tests.helpers import run
    seen = {}

    def fake(g, start, end, **kw):
        seen.update(g=g, start=start, **kw)
        return {"index": kw["index"], "rows": [{"date": "2025-01-01", "mean": 0.4}, {"date": "2025-01-11", "mean": 0.6}], "points": 2, "scenes": 2, "summary": {}}
    monkeypatch.setattr(timeseries, "series", fake)
    r = run(client, "/api/timeseries", {"geometry": {"type": "Feature", "geometry": {"type": "Point", "coordinates": [76.9, 12.5]}},
                                         "start": "2025-01-01", "end": "2025-03-01", "index": "EVI"})
    assert r["csv"].startswith("tables/") and seen["g"]["type"] == "Point" and seen["index"] == "EVI"
    assert client.post("/api/timeseries", json={"geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "start": "2025-01-01", "end": "2025-02-01"}).status_code == 400
    assert client.post("/api/timeseries", json={"geometry": {"type": "Point", "coordinates": [0, 0]}, "start": "2025-03-01", "end": "2025-02-01"}).status_code == 400


def test_georeference_a_picture(client, home):
    """A picture with no coordinates, 4 control points: placed where they say, with residuals."""
    import io

    import numpy as np
    import rasterio
    from PIL import Image
    from rasterio.warp import transform_bounds

    from tests.helpers import run
    buf = io.BytesIO()
    Image.fromarray((np.indices((200, 300)).sum(0) % 255).astype("uint8")).convert("RGB").save(buf, "PNG")
    up = ok(client.post("/api/georef/upload", files={"file": ("plan.png", buf.getvalue(), "image/png")}))
    assert up["width"] == 300 and up["height"] == 200
    assert client.get("/api/georef/image", params={"path": up["path"]}).status_code == 200
    pts = [{"px": 0, "py": 0, "lon": 77.50, "lat": 13.02}, {"px": 300, "py": 0, "lon": 77.53, "lat": 13.02},
           {"px": 0, "py": 200, "lon": 77.50, "lat": 13.00}, {"px": 300, "py": 200, "lon": 77.53, "lat": 13.00}]
    f = ok(client.post("/api/georef/fit", json={"image": up["path"], "points": pts}))
    assert f["rmse_m"] < 5 and len(f["residuals_m"]) == 4
    assert client.post("/api/georef/fit", json={"image": up["path"], "points": pts, "method": "poly2"}).status_code == 400   # needs 6
    r = run(client, "/api/georef/warp", {"image": up["path"], "points": pts})
    with rasterio.open(home / r["path"]) as s:
        b = transform_bounds(s.crs, "EPSG:4326", *s.bounds)
        assert s.count == 3 and abs(b[0] - 77.50) < 0.001 and abs(b[3] - 13.02) < 0.001
    assert client.post("/api/georef/upload", files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 400
    tif = io.BytesIO()   # a TIFF is shown through a PNG preview (made without Pillow, which the app doesn't bundle)
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="GTiff", width=40, height=30, count=1, dtype="uint16") as d:
            d.write((np.arange(1200).reshape(1, 30, 40) * 7).astype("uint16"))
        tif.write(mf.read())
    up2 = ok(client.post("/api/georef/upload", files={"file": ("scan.tif", tif.getvalue(), "image/tiff")}))
    img = client.get("/api/georef/image", params={"path": up2["path"]})
    assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_vector_geometry_helpers(client):
    """Centroids, convex hull, simplify, merge, explode, fishnet (cells of a known size), random points inside."""
    from shapely.geometry import shape

    from tests.helpers import run
    sq = lambda x, d, **p: {"type": "Feature", "properties": p, "geometry": {"type": "Polygon", "coordinates": [[[x, 13.0], [x + d, 13.0], [x + d, 13.0 + d], [x, 13.0 + d], [x, 13.0]]]}}
    a = {"type": "FeatureCollection", "features": [sq(77.50, 0.01, id=1), sq(77.52, 0.01, id=2)]}
    rd = lambda r: ok(client.get("/api/vector/read", params={"path": r["path"]}))
    c = rd(run(client, "/api/vector/geom-op", {"op": "centroids", "layer": a}))
    assert abs(c["features"][0]["geometry"]["coordinates"][0] - 77.505) < 1e-6
    assert run(client, "/api/vector/geom-op", {"op": "convex_hull", "layer": a, "whole": True})["features"] == 1
    assert run(client, "/api/vector/geom-op", {"op": "merge", "layers": [a, a], "layer_names": ["x", "y"]})["features"] == 4
    multi = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"k": 1}, "geometry": {"type": "MultiPolygon", "coordinates": [sq(77.5, 0.01)["geometry"]["coordinates"], sq(77.52, 0.01)["geometry"]["coordinates"]]}}]}
    assert run(client, "/api/vector/geom-op", {"op": "explode", "layer": multi})["features"] == 2
    net = rd(run(client, "/api/vector/geom-op", {"op": "fishnet", "layer": {"type": "FeatureCollection", "features": [sq(77.5, 0.01)]}, "cell": 250}))
    assert 16 <= len(net["features"]) <= 30                                                    # ~1.1 km square / 250 m
    pts = rd(run(client, "/api/vector/geom-op", {"op": "random_points", "layer": a, "count": 5, "per_feature": True, "seed": 1}))
    assert len(pts["features"]) == 10 and all(any(shape(p["geometry"]).within(shape(f["geometry"])) for f in a["features"]) for p in pts["features"])
    s = rd(run(client, "/api/vector/geom-op", {"op": "simplify", "layer": a, "tolerance": 5}))
    assert len(s["features"]) == 2


def test_no_element_id_is_used_twice(client):
    """Every tool's panel is on one page: two panels using the same element id (e.g. the same "<prefix>-run") would
    make one tool's button run the other."""
    import collections
    import re
    html = client.get("/").text
    srcs = [html] + [client.get(f"/static/{s}").text for s in re.findall(r'<script src="/static/(tools/[^"]+\.js)"', html)]
    ids = collections.Counter(i for s in srcs for i in re.findall(r'id="([A-Za-z][\w-]*)"', s))
    assert not [k for k, v in ids.items() if v > 1], f"ids used twice: {[k for k, v in ids.items() if v > 1]}"


def test_assistant_explains_results_from_their_numbers(client, monkeypatch):
    from webapp import assistant
    seen = {}

    def fake(system, messages, max_tokens=1500):
        seen.update(system=system, text=messages[-1]["content"])
        return "Cropland is 58 % of the area (231 ha)."
    monkeypatch.setattr(assistant, "chat_text", fake)
    steps = [{"title": "Area statistics", "endpoint": "/api/assess/area-stats", "result": {"total_ha": 400.0, "classes": [{"class": "Cropland", "area_ha": 231.4567}] * 30,
              "geometry": {"type": "Polygon"}}, "observation": [{"kind": "table", "rows": 6}]}]
    r = ok(client.post("/api/assistant/explain", json={"request": "how much cropland?", "steps": steps}))
    assert r["text"].startswith("Cropland") and "only the numbers" in seen["system"].lower().replace("use only", "only")
    assert "how much cropland?" in seen["text"] and "231.4567" in seen["text"] and "… 5 more" in seen["text"] and '"geometry"' not in seen["text"]


def test_find_place_then_buffer(client, monkeypatch):
    """Find place: the best match of a name as a point (or its outline), usable by Buffer; nothing found → a clear error."""
    from tests.helpers import run
    from webapp import aoi_io
    hit = {"name": "M Chinnaswamy Stadium, Bengaluru", "type": "stadium", "lat": 12.9788127, "lon": 77.5995775, "bbox": [77.598, 12.977, 77.601, 12.980],
           "boundary": {"type": "Polygon", "coordinates": [[[77.5985, 12.9791], [77.5993, 12.9779], [77.6006, 12.9783], [77.5999, 12.9798], [77.5985, 12.9791]]]}}
    monkeypatch.setattr(aoi_io, "geocode", lambda q, limit=6: [hit] if "chinnaswamy" in q.lower() else [])
    pt = run(client, "/api/vector/place", {"place": "M. Chinnaswamy Stadium, Bengaluru"})
    fc = ok(client.get("/api/vector/read", params={"path": pt["path"]}))
    assert fc["features"][0]["geometry"] == {"type": "Point", "coordinates": [77.5995775, 12.9788127]} and fc["features"][0]["properties"]["type"] == "stadium"
    outline = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/place", {"place": "Chinnaswamy", "outline": True})["path"]}))
    assert outline["features"][0]["geometry"]["type"] == "Polygon"
    zone = ok(client.get("/api/vector/read", params={"path": run(client, "/api/vector/buffer", {"layer": pt["path"], "distance": 20000})["path"]}))
    assert abs(_area_m2(zone["features"][0]["geometry"]) / (3.14159265 * 20000 ** 2) - 1) < 0.01   # a 20 km circle
    j = ok(client.post("/api/vector/place", json={"place": "Nowhere-at-all"}))
    while (s := ok(client.get(f"/api/jobs/{j['id']}")))["status"] not in ("done", "error"):
        pass
    assert s["status"] == "error" and "No place called" in s["error"]


def test_every_loaded_file_is_in_git():
    """Every script, stylesheet and app part index.html loads is tracked by git: a file only on this disk (e.g. hidden by
    a .gitignore rule) works here but is missing from a clone and from the apps built from it."""
    import re
    import shutil
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if not shutil.which("git") or not (root / ".git").exists():
        pytest.skip("not a git checkout")
    html = (root / "webapp/static/index.html").read_text(encoding="utf-8")
    files = {f"webapp/static/{p}" for p in re.findall(r'(?:src|href)="/static/([^"]+)"', html) if p != "app.js"}
    files |= {f"webapp/static/app/{p}" for p in __import__("json").loads((root / "webapp/static/app/parts.json").read_text())["parts"]}
    tracked = set(subprocess.run(["git", "ls-files", "--cached", "webapp/static"], cwd=root, capture_output=True, text=True).stdout.split())
    missing = sorted(f for f in files if f not in tracked)
    assert not missing, f"loaded by the app but not in git (check .gitignore, then git add): {missing}"
