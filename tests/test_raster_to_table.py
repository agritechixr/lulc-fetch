"""Raster → table, and the data viewer's table tools (rows, stats, search, edit sessions, Python, field calculator)."""

import io

import pandas as pd
import pytest

from tests.helpers import ok, run


def test_table_from_raster_ground_truth(table):
    r = table["report"]
    assert r["columns"][-1] == "label"
    assert set(r["class_counts"]) == {"1", "2", "3"}


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_table_from_polygons(client, data, home, fmt):
    r = run(client, "/api/tables/from-raster", {"path": data["s2"], "ground_truth": {"type": "vector", "geojson": data["samples"], "field": "class"},
                                                "name": f"from_samples_{fmt}", "format": fmt, "rowcol": True})
    df = pd.read_csv(home / r["path"]) if fmt == "csv" else pd.read_parquet(home / r["path"])
    assert set(df["label"].astype(str)) <= {"water", "vegetation", "built", "1", "2", "3"}
    assert len(df) > 100 and {"row", "col", "B08"} <= set(df.columns)


def test_table_random_sample_coarser(client, data, home):
    r = run(client, "/api/tables/from-raster", {"path": data["s2"], "name": "sample", "sampling": "random", "sample_size": 200,
                                                "factor": 2, "labelled_only": False})
    assert 140 <= r["rows"] <= 260 and r["factor"] == 2   # random sampling keeps each pixel with p = 200 / pixels: about 200 rows


def test_table_list_preview_rows_stats(client, table):
    assert any(t["path"] == table["path"] for t in ok(client.get("/api/tables")))
    pv = ok(client.get("/api/tables/preview", params={"path": table["path"], "n": 5}))
    assert len(pv["rows"]) == 5
    rows = ok(client.get("/api/tables/rows", params={"path": table["path"], "offset": 0, "limit": 10, "sort": "B08", "desc": True}))
    assert len(rows["rows"]) == 10
    assert ok(client.get("/api/tables/stats", params={"path": table["path"]}))
    pts = ok(client.get("/api/tables/points", params={"path": table["path"]}))
    assert pts


def test_search_and_derive(client, table, home):
    d = ok(client.post("/api/tables/derive", json={"path": table["path"], "q": "label = 2", "name": "vegetation_only"}))
    df = pd.read_csv(home / d["path"])
    assert len(df) > 0 and set(df["label"]) == {2}


def test_calc_preview(client, table):
    r = ok(client.get("/api/tables/calc-preview", params={"path": table["path"], "expression": "([B08] - [B04]) / ([B08] + [B04])"}))
    assert r


def test_edit_session(client, table, home):
    start = ok(client.post("/api/tables/edit/start", json={"path": table["path"]}))
    work = start["work"]
    ok(client.post("/api/tables/edit", json={"path": work, "ops": [{"op": "add_field", "name": "ndvi", "expression": "([B08] - [B04]) / ([B08] + [B04])"}]}))
    ok(client.post("/api/tables/edit", json={"path": work, "ops": [{"op": "rename_field", "old": "ndvi", "new": "NDVI"}]}))
    ok(client.post("/api/tables/undo", json={"path": work}))
    saved = ok(client.post("/api/tables/edit/save", json={"path": work, "mode": "new", "name": "with_ndvi"}))
    df = pd.read_csv(home / saved["path"])
    assert "ndvi" in df.columns and "NDVI" not in df.columns
    assert df.loc[df["label"] == 2, "ndvi"].mean() > 0.5


def test_python_on_attributes(client):
    job = ok(client.post("/api/python/run", json={"code": "df['b'] = df['a'] * 2\nprint('hello')", "columns": {"a": [1, 2, 3]}, "n": 3}))
    from tests.helpers import wait
    r = wait(client, job)["result"]
    assert r["ok"] and "hello" in r["output"]
    assert r["result"]["columns"] == ["a", "b"] and [row[1] for row in r["result"]["rows"]] == [2, 4, 6]


def test_python_error_is_reported(client):
    from tests.helpers import wait
    r = wait(client, ok(client.post("/api/python/run", json={"code": "1/0", "columns": {"a": [1]}, "n": 1})))["result"]
    assert not r["ok"] and "ZeroDivision" in r["error"]


@pytest.mark.parametrize("expr,expected", [("round([a] * 2, 1)", [2.5, 5.0]), ("upper([s])", ["X", "Y"]), ("[a] > 2", [False, True])])
def test_field_calculator(client, expr, expected):
    r = ok(client.post("/api/fields/calc", json={"expression": expr, "columns": {"a": [1.25, 2.5], "s": ["x", "y"]}, "n": 2}))
    assert r["values"] == expected


def test_field_calculator_geometry(client, data):
    f = data["samples"]["features"][:2]
    r = ok(client.post("/api/fields/calc", json={"expression": "$area", "columns": {}, "geometries": [x["geometry"] for x in f], "n": 2}))
    assert all(v > 30000 for v in r["values"])   # 16 × 20 pixels of 10 m = 32 000 m²


def test_field_calculator_rejects_bad_input(client):
    assert client.post("/api/fields/calc", json={"expression": "__import__('os')", "columns": {}, "n": 1}).status_code == 400
    assert ok(client.get("/api/fields/functions"))["functions"]


def test_upload_csv_and_delete(client):
    csv = io.BytesIO(b"a,b,label\n1,2,x\n3,4,y\n")
    up = ok(client.post("/api/tables/upload", files={"file": ("small.csv", csv, "text/csv")}))
    pv = ok(client.get("/api/tables/preview", params={"path": up["path"]}))
    assert pv["columns"] == ["a", "b", "label"]
    ok(client.delete("/api/tables", params={"path": up["path"]}))
    assert client.get("/api/tables/preview", params={"path": up["path"]}).status_code == 404
