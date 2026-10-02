"""Raster → table on the real scene, and the data viewer on a 345 000-row table (India crop production)."""

import time

import pandas as pd

import webapp.workspace as ws
from tests.helpers import ok, run, wait

TOOL = "Raster → table & data viewer"


def test_scene_to_table(s2_table, report):
    r = s2_table["report"]
    report.metric("rows", r["rows"])
    report.table("Rows per SCL class", ["class code", "rows"], sorted(r["class_counts"].items()))
    assert len(r["band_columns"]) == 12 and set(r["class_counts"]) == {"1", "2", "3"}


def test_big_table_upload_and_browse(client, tables, report):
    crops = tables["India Agriculture Crop Production"]
    t0 = time.time()
    first = ok(client.get("/api/tables/rows", params={"path": crops, "offset": 0, "limit": 100}))
    report.metric("open first page (s)", time.time() - t0)
    report.metric("rows", first["total"] if "total" in first else len(first["rows"]))
    t0 = time.time()
    srt = ok(client.get("/api/tables/rows", params={"path": crops, "limit": 20, "sort": "Production", "desc": True}))
    report.metric("sort 345k rows (s)", time.time() - t0)
    t0 = time.time()
    rice = ok(client.get("/api/tables/rows", params={"path": crops, "limit": 20, "q": "Crop = Rice"}))
    report.metric("filter Crop = Rice (s)", time.time() - t0)
    report.metric("Rice rows", rice.get("total"))
    st = ok(client.get("/api/tables/stats", params={"path": crops}))
    assert st and srt["rows"] and rice["rows"]
    cols = first["columns"] if "columns" in first else None
    if cols:
        i = cols.index("Crop")
        assert all(r[i] == "Rice" for r in rice["rows"])


def test_derive_edit_and_python(client, tables, report):
    crops = tables["India Agriculture Crop Production"]
    d = ok(client.post("/api/tables/derive", json={"path": crops, "q": "Crop = Rice", "name": "rice"}))
    df = pd.read_csv(ws.root() / d["path"])
    report.metric("rice rows", len(df))
    assert len(df) > 20000 and set(df["Crop"]) == {"Rice"}
    work = ok(client.post("/api/tables/edit/start", json={"path": d["path"]}))["work"]
    ok(client.post("/api/tables/edit", json={"path": work, "ops": [{"op": "add_field", "name": "yield_check", "expression": "[Production] / [Area]"}]}))
    job = ok(client.post("/api/python/run", json={"path": work, "code": "df['log_area'] = np.log1p(df['Area'])\nprint(len(df))", "apply": True}))
    res = wait(client, job)["result"]
    assert res["ok"], res["error"]
    saved = ok(client.post("/api/tables/edit/save", json={"path": work, "mode": "new", "name": "rice_edited"}))
    out = pd.read_csv(ws.root() / saved["path"])
    ok_rows = out["Area"] > 0
    diff = (out.loc[ok_rows, "yield_check"] - out.loc[ok_rows, "Yield"]).abs()
    report.metric("field calculator vs Yield column: max difference", float(diff.max()))
    assert "log_area" in out.columns and diff.max() < 1e-6
