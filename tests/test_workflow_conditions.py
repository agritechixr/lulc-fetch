"""Workflow conditions ("run only if", checks that warn / alert / stop, "falls by" against the previous run), the
built-in checks of what a step made (critical problems and warnings, shared with the Assistant), the cautions shown
before running, the diagram's layout, and conditions in the Assistant's plans."""

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from tests.helpers import ok
from webapp import workflows

TOOL = "Workflows: conditions, checks & diagram"
AOI = {"type": "Polygon", "coordinates": [[[77.5, 12.9], [77.52, 12.9], [77.52, 12.92], [77.5, 12.92], [77.5, 12.9]]]}


def wf(**step2):
    return {"name": "NDVI if clear", "inputs": [], "steps": [
        {"title": "Download", "endpoint": "/api/jobs", "body": {"kind": "scene", "aoi": AOI, "max_cloud": 70}},
        {"title": "NDVI", "endpoint": "/api/analyze/export", "body": {"path": {"$step": 0, "ext": ".tif", "nth": 0}}, **step2}]}


WHEN = {"all": [{"of": {"step": 0, "field": "cloud_pct"}, "op": "<", "value": 20}], "otherwise": "skip"}
CHECK = [{"if": {"of": {"step": 1, "stat": "mean"}, "op": "drop", "value": 0.1}, "then": "alert", "message": "NDVI fell"}]


def test_conditions_are_checked():
    c = workflows.check(wf(when=WHEN, checks=CHECK, ))
    assert c["steps"][1]["when"]["all"][0]["of"] == {"step": 0, "field": "cloud_pct"} and c["steps"][1]["checks"][0]["then"] == "alert"
    assert c["stop_on_critical"] is True
    for bad, msg in [({"when": {"all": [{"of": {"step": 1, "field": "x"}, "op": "<", "value": 1}]}}, "earlier step"),   # its own result: not yet
                     ({"when": {"all": [{"of": {"step": 0, "field": "x"}, "op": "~", "value": 1}]}}, "comparison"),
                     ({"checks": [{"if": {"of": {"step": 1, "stat": "mean"}, "op": "drop", "value": -0.1}}]}, "positive"),
                     ({"checks": [{"if": {"of": {"step": 1, "stat": "median"}, "op": "<", "value": 1}}]}, "statistic"),
                     ({"when": {"all": [{"of": {"step": 0, "field": "a b"}, "op": "<", "value": 1}]}}, "result field")]:
        with pytest.raises(ValueError, match=msg):
            workflows.check(wf(**bad))


def test_evaluate_fields_stats_and_change(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    with rasterio.open(up / "ndvi_now.tif", "w", driver="GTiff", width=10, height=10, count=1, dtype="float32", crs="EPSG:32643",
                       transform=from_origin(700000, 1400000, 10, 10)) as d:
        d.write(np.full((10, 10), 0.49, "float32"), 1)
    steps = {"0": {"result": {"cloud_pct": 34.0, "summary": {"n": [1, 2]}}, "outs": []}, "1": {"result": {}, "outs": ["uploads/ndvi_now.tif"]}}
    r = ok(client.post("/api/workflows/evaluate", json={"conditions": WHEN["all"], "steps": steps, "step": 1}))["results"]
    assert r[0]["ok"] is False and "34" in r[0]["text"] and r[0]["key"] == "0:cloud_pct"
    r = ok(client.post("/api/workflows/evaluate", json={"conditions": [{"of": {"step": 0, "field": "summary.n[1]"}, "op": "==", "value": 2}], "steps": steps, "step": 1}))["results"]
    assert r[0]["ok"] is True
    # "falls by 0.1": the first run only records; the next compares
    saved = ok(client.put("/api/workflows/new", json={"workflow": wf(checks=CHECK)}))
    first = ok(client.post("/api/workflows/evaluate", json={"conditions": [CHECK[0]["if"]], "steps": steps, "step": 1, "own": True, "wid": saved["id"]}))["results"][0]
    assert first["ok"] is None and abs(first["value"] - 0.49) < 1e-6 and "no earlier run" in first["text"]
    ok(client.post(f"/api/workflows/{saved['id']}/values", json={"values": {"1:mean:1": 0.61}}))
    later = ok(client.post("/api/workflows/evaluate", json={"conditions": [CHECK[0]["if"]], "steps": steps, "step": 1, "own": True, "wid": saved["id"]}))["results"][0]
    assert later["ok"] is True and later["previous"] == 0.61 and "fell from 0.61 to 0.49" in later["text"]
    missing = ok(client.post("/api/workflows/evaluate", json={"conditions": [{"of": {"step": 0, "field": "nope"}, "op": "<", "value": 1}], "steps": steps, "step": 1}))["results"][0]
    assert missing["ok"] is None and "not found" in missing["text"]
    assert client.post("/api/workflows/evaluate", json={"conditions": [{"of": {"step": 3, "field": "x"}, "op": "<", "value": 1}], "steps": {}, "step": 1}).status_code == 400
    client.delete(f"/api/workflows/{saved['id']}")


def test_builtin_checks(client, home):
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    prof = dict(driver="GTiff", width=20, height=20, count=1, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1400000, 10, 10), nodata=-9999)
    with rasterio.open(up / "scene_NDVI.tif", "w", **prof) as d:   # reflectance not scaled: "NDVI" far outside −1…1
        d.write(np.linspace(-300, 4000, 400, dtype="float32").reshape(20, 20), 1)
    with rasterio.open(up / "empty.tif", "w", **prof) as d:
        d.write(np.full((20, 20), -9999, "float32"), 1)
    (home / "tables").mkdir(exist_ok=True)
    (home / "tables" / "none.csv").write_text("a,b\n")
    r = ok(client.post("/api/workflows/observe", json={"paths": ["uploads/scene_NDVI.tif", "uploads/empty.tif", "tables/none.csv"], "result": {"cloud_pct": 85, "valid_pct": 45}}))
    crit = " | ".join(r["critical"])
    assert "NDVI runs from" in crit and "empty.tif is empty" in crit and "no rows" in crit and "85% cloudy" in crit
    assert any("45% of the area" in w for w in r["warnings"])
    good = ok(client.post("/api/workflows/observe", json={"paths": [], "result": {"cloud_pct": 5, "valid_pct": 97}}))
    assert good["critical"] == [] and good["warnings"] == []


def test_cautions(client):
    c = ok(client.post("/api/workflows/cautions", json={"workflow": wf(), "saved": True}))["cautions"]
    assert any("70% cloudy" in x for x in c)
    gated = ok(client.post("/api/workflows/cautions", json={"workflow": wf(when=WHEN), "saved": True}))["cautions"]
    assert not any("cloudy" in x for x in gated)   # the next step already checks the cloud cover
    same = {"name": "x", "inputs": [], "steps": [{"title": "Change", "endpoint": "/api/raster/change", "body": {"before": "uploads/a.tif", "after": "uploads/a.tif"}}]}
    assert any("same file" in x for x in ok(client.post("/api/workflows/cautions", json={"workflow": same}))["cautions"])
    big = {"name": "x", "inputs": [], "steps": [{"title": "Download", "endpoint": "/api/jobs", "body": {"kind": "composite", "max_cloud": 30,
            "aoi": {"type": "Polygon", "coordinates": [[[70, 10], [80, 10], [80, 20], [70, 20], [70, 10]]]}}}]}
    assert any("km²" in x for x in ok(client.post("/api/workflows/cautions", json={"workflow": big}))["cautions"])
    unsaved = ok(client.post("/api/workflows/cautions", json={"workflow": wf(checks=CHECK), "saved": False}))["cautions"]
    assert any("previous run" in x for x in unsaved)


def test_saved_with_conditions_and_layout(client):
    w = {**wf(when=WHEN, checks=CHECK), "layout": {"s:0": [20, 30], "s:1": [300, 30], "bad": "x"}, "stop_on_critical": False}
    saved = ok(client.put("/api/workflows/new", json={"workflow": w}))
    back = ok(client.get(f"/api/workflows/{saved['id']}"))
    assert back["steps"][1]["when"] == {**WHEN, "all": [{**WHEN["all"][0], "value": 20.0}]} and back["steps"][1]["checks"][0]["message"] == "NDVI fell"
    assert back["layout"] == {"s:0": [20.0, 30.0], "s:1": [300.0, 30.0]} and back["stop_on_critical"] is False
    client.delete(f"/api/workflows/{saved['id']}")


def test_assistant_plans_conditions():
    from webapp import assistant
    out = {"plan": "NDVI when clear", "workflow": {"name": "p", "inputs": [], "steps": [
        {"title": "Download", "endpoint": "/api/jobs", "body_json": json.dumps({"kind": "scene", "aoi": AOI}), "conditions_json": ""},
        {"title": "NDVI", "endpoint": "/api/analyze/export", "body_json": json.dumps({"path": {"$step": 0, "ext": ".tif", "nth": 0}, "band_map": {}, "scale": 1}),
         "conditions_json": json.dumps({"when": WHEN, "checks": CHECK})}]}}
    w, probs = assistant._to_workflow(out)
    assert w["steps"][1]["when"]["all"][0]["op"] == "<" and w["steps"][1]["checks"][0]["then"] == "alert"
    assert not [p for p in probs if "condition" in p]
    out["workflow"]["steps"][1]["conditions_json"] = json.dumps({"when": {"all": [{"of": {"step": 5, "field": "x"}, "op": "<", "value": 1}]}})
    _, probs = assistant._to_workflow(out)
    assert any("conditions" in p for p in probs)   # sent back to the model to fix
    assert "conditions_json" in assistant.SCHEMA["properties"]["workflow"]["properties"]["steps"]["items"]["required"]
    assert "when" in assistant._local_schema()["properties"]["workflow"]["properties"]["steps"]["items"]["properties"]
