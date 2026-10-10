"""Start the built app (headless) and check that it really works: web UI, map library, ML libraries, the Python
editor's separate process, and the hydrology, flood, streamflow, AHP, routing, SAR ML and animation tools (only with
what the app bundles).   python packaging/smoke_test.py"""

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


def run(path, body, timeout=300):
    job = get(path, body)
    for _ in range(timeout):
        j = get(f"/api/jobs/{job['id']}")
        if j["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(1)
    assert j["status"] == "done", f"{path}: {j.get('error')}"
    return j["result"]


def new_tools(home: Path):
    """The 0.0.4 tools on small synthetic data, inside the packaged app."""
    import base64

    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    up = home / "uploads"
    up.mkdir(exist_ok=True)
    n = 100
    y, x = np.mgrid[0:n, 0:n] * 30.0
    z = (0.02 * (n * 30 - y) + 0.06 * np.abs(x - n * 15) + np.random.default_rng(0).random((n, n))).astype("float32")
    z[40, 50] -= 20
    prof = dict(driver="GTiff", width=n, height=n, count=1, dtype="float32", crs="EPSG:32643", transform=from_origin(700000, 1500000, 30, 30))
    with rasterio.open(up / "dem.tif", "w", **prof) as d:
        d.write(z, 1)
    c = run("/api/hydro/condition", {"dem": "uploads/dem.tif"})
    assert c["cells_still_in_pits"] == 0, c
    dem = c["path"]
    assert run("/api/hydro/network", {"dem": dem, "stream_km2": 0.3})["links"] >= 1
    w = run("/api/hydro/watershed", {"dem": dem, "mode": "all", "stream_km2": 0.3})
    assert w["total_area_km2"] > 8, w
    from rasterio.warp import transform as wt
    xs, ys = wt("EPSG:32643", "EPSG:4326", [700000 + 50 * 30], [1500000 - 3 * 30])
    lon, lat = xs[0], ys[0]
    sim = run("/api/hydro/flood-sim", {"dem": dem, "inflows": [{"lon": lon, "lat": lat, "q": 20}], "hours": 0.5, "snapshots": 1})
    assert abs(sim["mass_error_pct"]) < 0.01, sim
    assert get("/api/ahp/weights", {"matrix": [[1, 3], [1 / 3, 1]]})["weights"] == [0.75, 0.25]
    # streamflow: GR4J on a synthetic record (rain and PET in the table)
    rng = np.random.default_rng(0)
    days = 365 * 4
    rain = np.where(rng.random(days) < 0.35, rng.gamma(0.8, 12, days), 0)
    (home / "tables").mkdir(exist_ok=True)
    import datetime as dt
    q = np.convolve(rain, np.exp(-np.arange(30) / 6) / 6, "full")[:days] * 0.4
    lines = ["date,flow,rain,pet"] + [f"{dt.date(2015, 1, 1) + dt.timedelta(d)},{q[d]:.4f},{rain[d]:.3f},3.5" for d in range(days)]
    (home / "tables" / "flow.csv").write_text("\n".join(lines))
    sf = run("/api/hydro/streamflow", {"table": "tables/flow.csv", "units": "mm", "models": ["gr4j", "lgbm"]})
    assert any(s["model"] == "GR4J" and s["val_nse"] is not None for s in sf["scores"]), sf
    # routing on a road layer
    roads = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "LineString",
             "coordinates": [[77 + 0.01 * j, 13.0] for j in range(5)]}}]}
    assert run("/api/network/routing", {"op": "route", "points": [[77.0, 13.0], [77.04, 13.0]], "roads": roads})["length_km"] > 4
    # SAR flood ML (LightGBM) on a synthetic scene
    water = (np.hypot(*(np.mgrid[0:n, 0:n] - 50)) < 25)
    vv = np.where(water, -21, -9) + rng.normal(0, 2, (n, n))
    vh = np.where(water, -27, -16) + rng.normal(0, 2, (n, n))
    with rasterio.open(up / "s1.tif", "w", **{**prof, "count": 2}) as d:
        d.write(np.stack([vv, vh]).astype("float32"))
        d.set_band_description(1, "VV")
        d.set_band_description(2, "VH")
    with rasterio.open(up / "conf.tif", "w", **prof) as d:
        d.write(np.clip(0.5 + (-15 - vv) / 10, 0, 1).astype("float32"), 1)
    fm = run("/api/sar/flood-ml", {"sar": "uploads/s1.tif", "confidence": "uploads/conf.tif"})
    assert fm["held_out"]["iou_water"] > 0.7, fm
    # the time slider's GIF without Pillow / OpenCV
    def png(rgb):
        a = np.zeros((4, 20, 30), "uint8")
        a[:3] = np.array(rgb, "uint8")[:, None, None]
        a[3] = 255
        with MemoryFile() as mf:
            with mf.open(driver="PNG", width=30, height=20, count=4, dtype="uint8") as d:
                d.write(a)
            return "data:image/png;base64," + base64.b64encode(mf.read()).decode()
    frames = [{"image": png(c), "bounds": [[13.0, 77.0], [13.2, 77.3]], "label": f"2026-0{k + 1}-01"} for k, c in enumerate([(255, 0, 0), (0, 0, 255)])]
    gif = run("/api/view/animation", {"frames": frames, "format": "gif", "width": 300})
    data = get(gif["url"])
    assert data[:6] == b"GIF89a" and len(data) > 500, len(data)


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
        new_tools(home)
        print("Smoke test passed: UI, map library, ML libraries, field calculator, Python runner, agri data, "
              "hydrology, flood simulation, streamflow, AHP, routing, SAR flood ML, GIF export")
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
