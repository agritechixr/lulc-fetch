"""Start the built app (headless) and check that it really works: web UI, map library, ML libraries and the
Python editor's separate process.   python packaging/smoke_test.py"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
exe = ROOT / "dist" / ("LULC Fetch.app/Contents/MacOS/LULC Fetch" if sys.platform == "darwin" else "LULC Fetch/LULC Fetch.exe")
PORT = 8799
U = f"http://127.0.0.1:{PORT}"


def get(path, data=None):
    req = urllib.request.Request(U + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        return json.loads(body) if r.headers.get("content-type", "").startswith("application/json") else body


def main():
    home = Path(tempfile.mkdtemp(prefix="lulc_smoke_"))
    env = {**os.environ, "LULC_HOME": str(home), "LULC_PORT": str(PORT)}
    proc = subprocess.Popen([str(exe), "--headless"], env=env, cwd=str(home))
    try:
        for _ in range(120):
            try:
                get("/api/project")
                break
            except Exception:
                time.sleep(1)
        else:
            raise SystemExit("The app didn't start")
        page = get("/").decode()
        assert "/static/vendor/leaflet/leaflet.min.js" in page, "map library not bundled"
        assert len(get("/static/vendor/leaflet/leaflet.min.js")) > 100000
        schema = get("/api/ml/schema")
        assert not schema["unavailable"], f"models not available: {schema['unavailable']}"
        calc = get("/api/fields/calc", {"expression": "round([a] * 2, 1)", "columns": {"a": [1.25, 2.5]}, "n": 2})
        assert calc["values"] == [2.5, 5.0], calc
        job = get("/api/python/run", {"code": "import sklearn, rasterio\ndf['b'] = df['a'] + 1\nprint('python ok')",
                                      "columns": {"a": [1, 2, 3]}, "n": 3})
        for _ in range(120):
            j = get(f"/api/jobs/{job['id']}")
            if j["status"] in ("done", "error"):
                break
            time.sleep(1)
        assert j["status"] == "done" and j["result"]["ok"], j.get("error") or j.get("result")
        assert "python ok" in j["result"]["output"]
        agri = get("/api/agri/schema")   # crop labels and the knowledge base are bundled
        assert len(agri["crops"]) >= 42, f"agri data not bundled: {len(agri['crops'])} crops"
        assert get("/api/agri/guide/search?crop=Mango&q=anthracnose")["total"] > 0
        print("Smoke test passed: UI, map library, ML libraries, field calculator, Python runner, agri data")
    except BaseException:
        log = home / "logs" / "app.log"
        if log.exists():
            print("---- app log ----\n" + log.read_text(errors="replace")[-6000:])
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
