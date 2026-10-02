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
