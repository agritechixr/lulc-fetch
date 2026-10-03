"""The shared core ("universe") of the web app: helpers every tool uses, so each tool's own file stays short.

    from webapp import core
    src = core.raster_path(req.path)                 # a layer path from the browser, checked
    g = core.clip(req.clip)                          # an optional area (GeoJSON polygon), checked
    stem = core.safe_stem(req.name, "embedding")     # a name that is safe as a file name
    return core.jobs.submit("kind", "Title", {}, run).to_dict()   # run a tool in the background (History, logs, ⓘ)

Tools live in webapp/routes/<menu>.py (e.g. routes/embeddings.py, routes/agri.py), each an APIRouter included by
server.py, and import only from here and from lulc_fetch: changing one tool never touches another.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from fastapi import HTTPException
from shapely.geometry import shape

from lulc_fetch.aoi import AOI

from . import workspace as ws
from .jobs import JobManager

log = logging.getLogger("webapp")
jobs = JobManager()   # the one job queue: every tool run goes through it (progress, logs, History, error log)

# ------------------------------------------------------------------ folders of the open project

TABLE_DIR = ws.Dir("tables")              # tables (CSV) the data viewer opens
PATCH_DIR = ws.Dir("training_data")       # deep-learning training patches
DL_MODEL_DIR = ws.Dir("models")           # trained models

RASTER_ROOTS = {"imports": "Imported products", "downloads": "Downloads", "uploads": "Uploaded",
                "analysis": "Analysis results", "output": "CLI output"}
RASTER_EXTS = {".tif", ".tiff", ".vrt"}  # .vrt only as written by the .SAFE importer
UPLOAD_EXTS = {".tif", ".tiff"}  # never accept uploaded VRTs: they can point at arbitrary files


# ------------------------------------------------------------------ checking what the browser sends

def aoi(geometry: dict) -> AOI:
    try:
        geom = shape(geometry)
    except Exception:
        raise HTTPException(400, "Invalid AOI geometry")
    if geom.is_empty or geom.area == 0:
        raise HTTPException(400, "The AOI must be a polygon with some area")
    return AOI(tuple(geom.bounds), [geometry])


def clip(geometry: dict | None) -> dict | None:
    """Validate an optional clip polygon (GeoJSON, EPSG:4326)."""
    if not geometry:
        return None
    aoi(geometry)
    return geometry


def raster_path(rel: str, roots=RASTER_ROOTS) -> Path:
    """Resolve a client-supplied relative path, refusing anything outside the allowed folders."""
    path = (ws.root() / rel).resolve()
    for root in roots:
        base = (ws.root() / root).resolve()
        if path.is_relative_to(base) and path.suffix.lower() in RASTER_EXTS and path.is_file():
            return path
    raise HTTPException(404, "No such GeoTIFF")


def abs_user_folder(folder: str) -> Path:
    """A folder the user typed or picked (~ and $VARS expanded, relative to the project), which must exist."""
    raw = folder.strip().strip('"').strip("'")
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute():
        p = ws.root() / p
    if not p.is_dir():
        raise HTTPException(400, f"{p} isn't a folder")
    return p.resolve()


def safe_stem(name: str, default: str) -> str:
    """A user-given name made safe as a file name: letters, digits, _ and -, at most 60 characters."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name or "").strip("_")[:60] or default


# ------------------------------------------------------------------ files

def unique(folder: Path, name: str) -> Path:
    """folder/name, or name_2, name_3 … when it already exists (never overwrite)."""
    dest, i, stem, suf = folder / name, 2, Path(name).stem, Path(name).suffix
    if name.endswith(".evaluation.html"):
        stem, suf = name[: -len(".evaluation.html")], ".evaluation.html"
    while dest.exists():
        dest = folder / f"{stem}_{i}{suf}"
        i += 1
    return dest


def one_output(res: dict, key: str = "path") -> dict:
    """A job result with one written file: its path made relative to the project and listed as the job's output."""
    res[key] = ws.rel(res[key])
    res["outputs"] = [res[key]]
    return res


# ------------------------------------------------------------------ small settings files (config/<name>.json)

def read_settings(fname: str, default=None):
    try:
        return json.loads((ws.CONFIG_DIR / fname).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def write_settings(fname: str, value) -> None:
    """Save a settings file; raises OSError when it can't be written."""
    ws.CONFIG_DIR.mkdir(exist_ok=True)
    (ws.CONFIG_DIR / fname).write_text(json.dumps(value, indent=1))


def remembered(fname: str) -> list[str]:
    """Folders remembered in a list (e.g. the trained models), newest last."""
    return [x for x in read_settings(fname, []) if isinstance(x, str)]


def remember_dl(fname: str, folder: str, remove: bool = False):
    items = [x for x in remembered(fname) if Path(x).resolve() != Path(folder).resolve()]
    if not remove:
        items.append(str(Path(folder).resolve()))
    try:
        write_settings(fname, items[-100:])
    except OSError:
        pass


# ------------------------------------------------------------------ the deep-learning (PyTorch) add-on

_dl_status_cache: dict = {}


def dl_status(force: bool = False) -> dict:
    """Add-on status without importing PyTorch into this process (see lulc_fetch.dlrunner): the package check is
    done here, the device check once in a helper process."""
    import importlib.metadata as md
    import importlib.util

    if _dl_status_cache and not force:
        return dict(_dl_status_cache)
    pkgs, missing = {}, []
    for mod, dist in (("torch", "torch"), ("torchvision", "torchvision"), ("segmentation_models_pytorch", "segmentation-models-pytorch")):
        if importlib.util.find_spec(mod) is None:
            missing.append(dist)
        else:
            try:
                pkgs[dist] = md.version(dist)
            except md.PackageNotFoundError:
                pkgs[dist] = "?"
    st = {"available": False, "packages": pkgs, "devices": ["cpu"], "device": "cpu"}
    if missing:
        st["error"] = f"ModuleNotFoundError: No module named {missing[0]!r}"
    else:
        from lulc_fetch import dlrunner
        try:
            st = dlrunner.run("status")
        except Exception as e:
            st["error"] = str(e)
    _dl_status_cache.clear()
    _dl_status_cache.update(st)
    return dict(st)


def require_dl() -> None:
    """Stop a tool that needs PyTorch when the add-on isn't installed."""
    if not dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
