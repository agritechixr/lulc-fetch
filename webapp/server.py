"""Local web UI for lulc-fetch. Run with `lulc-fetch-web` and open http://127.0.0.1:8000."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lulc_fetch.extras import LABEL_PRODUCTS
from lulc_fetch.indices import INDICES
from lulc_fetch.sources import DEFAULT_BANDS, S2_BANDS

from . import credentials
from . import workspace as ws
from .core import source as _source
from .routes import (
    agri,
    classical_ml,
    deep_learning,
    embeddings,
    export,
    find_imagery,
    forecast,
    history,
    interpolation,
    library,
    pca,
    pictures,
    projects,
    raster_ml,
    rasters,
    safe_products,
    stack,
    tables,
    training_data,
    unsupervised,
    workflows,
    assistant,
    vector_tools,
    raster_tools,
    convert_tools,
    analysis_tools,
)
from .routes import jobs as job_routes

log = logging.getLogger("webapp")
STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SOURCE_TITLES = {"earth-search": "Earth Search (AWS) — no login",
                 "planetary-computer": "Microsoft Planetary Computer — no login",
                 "cdse": "Copernicus Data Space (ESA) — needs S3 keys",
                 "landsat-pc": "USGS Landsat via Planetary Computer — no login"}

app = FastAPI(title="lulc-fetch")


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Reject requests from other sites (CSRF) or via DNS-rebinding hostnames."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    origin = request.headers.get("origin")
    origin_host = origin.split("://", 1)[-1].rsplit(":", 1)[0] if origin else None
    if host not in LOCAL_HOSTS or (origin_host and origin_host not in LOCAL_HOSTS):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    if request.method == "POST" and request.url.path.startswith("/api/") and "json" in (request.headers.get("content-type") or ""):
        import json as _json

        from .jobs import REQUEST
        try:   # remembered for the job this request may start: its settings go into History
            REQUEST.set({"endpoint": request.url.path, "body": _json.loads(await request.body() or b"null")})
        except ValueError:
            REQUEST.set(None)
    elif request.method == "POST":
        from .jobs import REQUEST
        REQUEST.set({"endpoint": request.url.path, "body": {}})
    return await call_next(request)


@app.exception_handler(RuntimeError)
@app.exception_handler(ValueError)
async def readable_errors(request: Request, exc: Exception):
    """Library errors (missing credentials, AOI too large, nothing found) carry user-facing messages."""
    return JSONResponse({"detail": str(exc)}, status_code=400)






# ------------------------------------------------------------------ config & credentials

@app.get("/api/config")
def config():
    from lulc_fetch.sources import MISSIONS

    return {"sources": SOURCE_TITLES, "missions": MISSIONS, "bands": S2_BANDS, "default_bands": DEFAULT_BANDS,
            "indices": list(INDICES),
            "label_products": {k: v["title"] for k, v in LABEL_PRODUCTS.items()}}


@app.get("/api/about")
def about(request: Request):
    """The About box (click the logo): version, how the app runs, the computer, library versions, add-ons and folders.
    Nothing heavy is imported: versions come from the installed packages' metadata."""
    import importlib.metadata as md
    import importlib.util
    import os
    import platform
    import subprocess
    import sys

    from lulc_fetch import __version__

    from .core import _dl_status_cache
    from .routes.deep_learning import _yolo_status

    def ver(dist):
        try:
            return md.version(dist)
        except md.PackageNotFoundError:
            return None

    def memory_gb():
        try:
            if sys.platform == "win32":
                import ctypes

                class MS(ctypes.Structure):
                    _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                               ("total", "avail", "ptotal", "pavail", "vtotal", "vavail", "xavail")]
                m = MS(); m.len = ctypes.sizeof(MS)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
                return round(m.total / 2**30, 1)
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
        except (OSError, ValueError, AttributeError):
            return None

    frozen = bool(getattr(sys, "frozen", False))
    git = None
    repo = Path(__file__).resolve().parent.parent
    if not frozen and (repo / ".git").exists():   # running from source: which branch and commit
        def g(*a):
            return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, timeout=3).stdout.strip()
        try:
            git = {"branch": g("rev-parse", "--abbrev-ref", "HEAD"), "commit": g("rev-parse", "--short", "HEAD"),
                   "date": g("log", "-1", "--format=%cs"), "changed": bool(g("status", "--porcelain", "--untracked-files=no"))}
        except (OSError, subprocess.SubprocessError):
            git = None
    os_name = {"darwin": "macOS " + platform.mac_ver()[0], "win32": f"Windows {platform.release()} ({platform.version()})"}.get(sys.platform,
                                                                                                    f"{platform.system()} {platform.release()}")
    import rasterio

    try:
        from . import credentials
        accounts = {k: {"title": v["title"], "connected": v["complete"]} for k, v in credentials.status().items()}
    except Exception:  # noqa: BLE001 — the keychain may be unavailable; the About box still opens
        accounts = {}
    dl = dict(_dl_status_cache) if _dl_status_cache else None
    return {
        "name": "LULC Fetch", "version": __version__,
        "run": {"mode": "desktop app" if frozen else "from source", "frozen": frozen, "git": git,
                "server": f"{request.url.scheme}://{request.url.netloc}", "pid": os.getpid()},
        "system": {"os": os_name, "arch": platform.machine(), "cpus": os.cpu_count(), "memory_gb": memory_gb(),
                   "python": f"{platform.python_version()} ({platform.python_implementation()})", "executable": sys.executable},
        "libraries": {"GDAL": rasterio.__gdal_version__, **{k: ver(d) for k, d in (
            ("rasterio", "rasterio"), ("NumPy", "numpy"), ("Shapely", "shapely"), ("pandas", "pandas"), ("PyArrow", "pyarrow"),
            ("scikit-learn", "scikit-learn"), ("XGBoost", "xgboost"), ("LightGBM", "lightgbm"), ("FastAPI", "fastapi"), ("Uvicorn", "uvicorn"))}},
        "addons": {
            "deep_learning": {"installed": importlib.util.find_spec("torch") is not None, "torch": ver("torch"),
                              "smp": ver("segmentation-models-pytorch"), "device": (dl or {}).get("device"), "devices": (dl or {}).get("devices")},
            "yolo": _yolo_status(),
        },
        "accounts": accounts,
        "folders": {"workspace": str(ws.root()), "data": str(ws.APP_DIR), "settings": str(ws.CONFIG_DIR), "logs": str(ws.root() / "logs")},
    }


@app.get("/api/credentials")
def get_credentials():
    return {"backend": credentials.backend_name(), "providers": credentials.status()}


@app.put("/api/credentials/{provider}")
def put_credentials(provider: str, values: dict[str, str]):
    if provider not in credentials.PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    credentials.save(provider, values)
    return get_credentials()


@app.delete("/api/credentials/{provider}")
def delete_credentials(provider: str):
    if provider not in credentials.PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    credentials.delete(provider)
    return get_credentials()


@app.post("/api/credentials/{provider}/test")
def test_credentials(provider: str):
    try:
        if provider == "cdse_account":
            from lulc_fetch.cdse import get_token
            c = credentials.get_all("cdse_account")
            get_token(c["username"], c["password"])
            return {"ok": True, "message": "Logged in to Copernicus Data Space"}
        if provider == "cdse_s3":
            src = _source("cdse")
            item = next(src.client().search(collections=[src.collection], max_items=1).items())
            href = src.href(item, "SCL")
            import rasterio
            with rasterio.Env(**src.gdal_env()), rasterio.open(href) as ds:
                ds.read(1, window=((0, 16), (0, 16)))
            return {"ok": True, "message": "S3 keys work — read a test file from CDSE"}
        if provider == "usgs":
            from lulc_fetch import usgs as ee
            c = credentials.get_all("usgs")
            ee.logout(ee.login(c["username"], c["token"]))
            return {"ok": True, "message": "Logged in to USGS EarthExplorer (M2M API)"}
        if provider == "planetary_computer":
            return {"ok": True, "message": "Saved (Planetary Computer works without a key; nothing to test)"}
        if provider == "anthropic":   # looks the model up: no tokens are used
            import anthropic

            from .assistant import CLAUDE_MODEL
            m = anthropic.Anthropic(api_key=credentials.get_all("anthropic")["api_key"], max_retries=1, timeout=20).models.retrieve(CLAUDE_MODEL)
            return {"ok": True, "message": f"The key works: {m.display_name} is available to the Assistant"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:300]}
    raise HTTPException(404, "Unknown provider")


# ------------------------------------------------------------------ every tool's endpoints: one file each in webapp/routes/
# (shared helpers in webapp/core.py). Included in this order, the order the endpoints had in one file.

for _r in (find_imagery, job_routes, rasters, safe_products, pca, tables, pictures, classical_ml, raster_ml, training_data,
           deep_learning, history, embeddings, agri, library, interpolation, forecast, unsupervised, stack, export, projects, workflows, assistant, vector_tools, raster_tools, convert_tools, analysis_tools):
    app.include_router(_r.router)


# ------------------------------------------------------------------ static UI

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def app_script() -> str:
    """The app's script: the parts listed in static/app/parts.json, joined in order inside one closure, so every part
    (core/… and one file per tool) sees the others' functions, as when it was one file."""
    import json as _json
    d = STATIC / "app"
    spec = _json.loads((d / "parts.json").read_text(encoding="utf-8"))
    out = [spec["header"], "(() => {", '  "use strict";', ""]
    for name in spec["parts"]:
        out += [f"  // ======== app/{name}", (d / name).read_text(encoding="utf-8").rstrip("\n"), ""]
    return "\n".join(out + ["})();", ""])


@app.get("/static/app.js")
def app_js():
    from fastapi.responses import Response
    return Response(app_script(), media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    import uvicorn

    p = argparse.ArgumentParser(description="lulc-fetch web UI")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  lulc-fetch web UI → {url}\n  Downloads are saved in {ws.root() / 'downloads'}\n")
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
