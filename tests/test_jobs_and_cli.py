"""Downloads & jobs (list, files, cancel, delete) and the lulc-fetch command line."""

import subprocess
import sys

import pytest

from tests.conftest import ROOT
from tests.helpers import ok, run


def test_jobs_list_files_and_delete(client, data):
    r = run(client, "/api/pca/run", {"path": data["s2"], "bands": [1, 2, 3, 4], "method": "pca", "params": {"n_components": 2}, "name": "job_test"})
    jid = r["path"].split("/")[1]   # downloads/<job id>/file.tif
    listed = ok(client.get("/api/jobs"))
    assert any(j["id"] == jid for j in (listed if isinstance(listed, list) else listed["jobs"]))
    job = ok(client.get(f"/api/jobs/{jid}"))
    assert job["status"] == "done" and job["files"]
    assert client.get(f"/api/jobs/{jid}/files/{job['files'][0]}").status_code == 200
    assert client.get(f"/api/jobs/{jid}/files/..%2F..%2Fsecret").status_code == 404
    ok(client.delete(f"/api/jobs/{jid}"))
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_cache_listing(client):
    assert ok(client.get("/api/cache")) is not None


def test_cli_starts():
    r = subprocess.run([sys.executable, "-m", "lulc_fetch.cli", "--help"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "search" in r.stdout and "composite" in r.stdout


@pytest.mark.network
def test_cli_search():
    r = subprocess.run([sys.executable, "-m", "lulc_fetch.cli", "search", "--bbox", "77.58,12.96,77.61,12.99", "--start", "2024-01-01",
                        "--end", "2024-02-15", "--max-cloud", "30", "--json"], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip()


def test_error_log(client, tmp_path):
    """A failed job is written to logs/errors.log (tool, settings, error, traceback); so is a failure seen in the browser."""
    import time

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    import webapp.workspace as ws
    p = ws.root() / "uploads" / "testdata" / "emb_err.tif"
    p.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(p, "w", driver="GTiff", width=4, height=4, count=16, dtype="float32", crs="EPSG:32643",
                       transform=from_origin(770000, 1435000, 10, 10)) as d:
        d.write(np.ones((16, 4, 4), np.float32))
    job = ok(client.post("/api/emb/similar", json={"path": ws.rel(p), "points": [[0.0, 0.0]], "name": "fails"}))
    for _ in range(100):
        j = ok(client.get(f"/api/jobs/{job['id']}"))
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert j["status"] == "error"
    text = client.get("/api/errors/file").text
    assert f"job {job['id']}" in text and "None of the points" in text and "Traceback" in text and 'File "' in text
    ok(client.post("/api/errors/report", json={"title": "Rendering a layer", "error": "Server unreachable", "tool": "analyze"}))
    info = ok(client.get("/api/errors"))
    assert info["exists"] and info["entries"] >= 2 and info["path"].endswith("errors.log")
    assert "Rendering a layer" in client.get("/api/errors/file").text


def test_history(client, data):
    """Every run goes into History with its inputs, settings (secrets hidden), outputs and time; copies saved later too."""
    r = run(client, "/api/pca/run", {"path": data["s2"], "bands": [1, 2, 3, 4], "method": "pca", "params": {"n_components": 2},
                                     "name": "hist_test", "api_key": "abc"})
    jid = r["path"].split("/")[1]
    rows = ok(client.get("/api/history", params={"q": "hist_test"}))["rows"]
    assert rows and rows[0]["id"] == jid and rows[0]["status"] == "done" and rows[0]["outputs"] >= 1
    e = ok(client.get(f"/api/history/{jid}"))
    assert e["endpoint"] == "/api/pca/run" and e["inputs"]["path"] == data["s2"] and e["settings"]["bands"] == [1, 2, 3, 4]
    assert e["settings"]["api_key"] == "•••" and e["seconds"] >= 0 and any(o.endswith(".tif") for o in e["outputs"])
    ok(client.post(f"/api/history/{jid}/copy", json={"folder": "/somewhere", "files": ["/somewhere/x.tif"]}))
    assert ok(client.get(f"/api/history/{jid}"))["copies"][0]["folder"] == "/somewhere"
    assert client.get("/api/history/nope").status_code == 404
    ok(client.delete("/api/history"))
    assert ok(client.get("/api/history"))["total"] == 0
