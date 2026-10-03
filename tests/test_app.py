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
    assert sorted(ids) == sorted(["embed", "embtrain", "embpredict", "embconvert", "embexplore", "agridisease", "agriguide"])
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
