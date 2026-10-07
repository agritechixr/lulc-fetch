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

import base64
import json
import logging
import os
import re
import warnings
from pathlib import Path

import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, Field
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from shapely.geometry import shape

from lulc_fetch.aoi import AOI
from lulc_fetch.sources import SOURCES, get_source

from . import credentials
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
    inside = any(path.is_relative_to((ws.root() / root).resolve()) for root in roots)
    if inside and path.suffix.lower() in RASTER_EXTS and path.is_file():
        return path
    # say what is wrong, so a missing file isn't mistaken for a bug in the tool
    name = Path(rel).name
    if not inside:
        raise HTTPException(404, f"“{name}” isn't in this project's folders: add it with Insert ▸ Add data (it is copied in)")
    if path.suffix.lower() not in RASTER_EXTS:
        raise HTTPException(404, f"“{name}” isn't a GeoTIFF (.tif / .tiff)")
    raise HTTPException(404, f"The file of this layer is missing: {rel}. It was moved, renamed or deleted (or the project "
                             "folder changed): add it again with Insert ▸ Add data, or remove the layer")


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


# ------------------------------------------------------------------ used by several tools

def source(name: str):
    if name not in SOURCES:
        raise HTTPException(400, f"Unknown source {name}")
    return get_source(name, **credentials.source_kwargs(name))


def png_data_url(rgba: np.ndarray) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with MemoryFile() as mem:
            with mem.open(driver="PNG", width=rgba.shape[2], height=rgba.shape[1],
                          count=4, dtype="uint8") as dst:
                dst.write(rgba)
            return "data:image/png;base64," + base64.b64encode(mem.read()).decode()


TABLE_EXTS = {".csv", ".parquet"}


def table_path(rel: str) -> Path:
    p = (ws.root() / rel).resolve()
    if not p.is_relative_to(TABLE_DIR.resolve()) or p.suffix.lower() not in TABLE_EXTS or not p.is_file():
        raise HTTPException(404, "No such table")
    return p


MODEL_DIR = ws.Dir("models")


def model_path(rel: str) -> Path:
    p = (ws.root() / rel).resolve()
    if not p.is_relative_to(MODEL_DIR.resolve()) or p.suffix != ".joblib" or not p.is_file():
        raise HTTPException(404, "No such model")
    return p


def report_folder(folder: str) -> Path:
    """Validate a folder typed by the user for saving evaluation reports (created if missing)."""
    import os
    import tempfile

    raw = folder.strip().strip('"').strip("'")
    d = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not raw or not d.is_absolute():
        raise HTTPException(400, "Give a full folder path, e.g. /Users/you/Documents/reports or ~/Documents/reports")
    if d.exists() and not d.is_dir():
        raise HTTPException(400, f"{d} is a file, not a folder")
    try:
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=d, prefix=".lulc_write_test_"):
            pass
    except OSError as e:
        raise HTTPException(400, f"Can't write to {d}: {e.strerror or e}")
    return d


def copy_report(html: Path, folder: Path) -> Path:
    """Copy an evaluation report into `folder` without overwriting an existing file."""
    import shutil

    dest, i = folder / html.name, 2
    while dest.exists():
        dest = folder / f"{html.name.removesuffix('.evaluation.html')}_{i}.evaluation.html"
        i += 1
    shutil.copy2(html, dest)
    return dest


class PatchInput(BaseModel):
    path: str
    bands: list[int] | None = None
    name: str | None = Field(None, max_length=120)
    scale: float = 1.0
    offset: float = 0.0


def companions(src: Path) -> list[Path]:
    """Files that belong with src: shapefile parts, sidecar descriptions, world files, model reports."""
    out = []
    if src.suffix.lower() == ".shp":
        out += [src.with_suffix(e) for e in (".shx", ".dbf", ".prj", ".cpg")]
    out += [Path(str(src) + ".json"), Path(str(src) + ".aux.xml"), src.with_suffix(".pgw"), src.with_suffix(".tfw")]
    if src.suffix == ".joblib":
        out.append(src.with_suffix(".evaluation.html"))
    return [p for p in out if p.is_file()]


def save_into(src: Path, folder: Path) -> list[str]:
    """Copy a workspace file (and its companions) into folder without overwriting. Zipped shapefiles are unpacked."""
    import shutil
    import zipfile

    saved = []
    if src.suffix.lower() == ".zip" and zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            members = [m for m in z.namelist() if not m.endswith("/") and "/" not in m.strip("/") and ".." not in m]
            if any(m.lower().endswith(".shp") for m in members):
                stems = {Path(m).stem for m in members}
                rename = {}
                for st in stems:
                    new, i = st, 2
                    while any((folder / f"{new}{Path(m).suffix}").exists() for m in members if Path(m).stem == st):
                        new = f"{st}_{i}"
                        i += 1
                    rename[st] = new
                for m in members:
                    dest = folder / f"{rename[Path(m).stem]}{Path(m).suffix}"
                    with z.open(m) as fin, open(dest, "wb") as fout:
                        shutil.copyfileobj(fin, fout)
                    saved.append(str(dest))
                return saved
    dest = unique(folder, src.name)
    shutil.copy2(src, dest)
    saved.append(str(dest))
    for c in companions(src):
        name = dest.name + c.name[len(src.name):] if c.name.startswith(src.name) else dest.stem + c.name[len(src.stem):]
        if c.suffix == ".html" and src.suffix == ".joblib":
            name = dest.with_suffix("").name + ".evaluation.html"
        shutil.copy2(c, folder / name)
        saved.append(str(folder / name))
    return saved


def vrt_to_tif(src: Path, out: Path) -> Path:
    """A .SAFE import (a VRT that points at the original product) written as a real GeoTIFF."""
    import rasterio
    from rasterio.shutil import copy as rio_copy

    with rasterio.open(src) as ds:
        big = ds.width * ds.height * ds.count > 2**31
    rio_copy(src, out, driver="GTiff", compress="deflate", tiled=True, blockxsize=512, blockysize=512,
             BIGTIFF="YES" if big else "IF_SAFER", predictor=2)
    return out
