"""Projects, the folder browser, saving copies to a folder, and the cache. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs, log
from ..core import report_folder as _report_folder
from ..core import save_into as _save_into
from ..core import unique as _unique
from ..core import vrt_to_tif as _vrt_to_tif

router = APIRouter()


# ------------------------------------------------------------------ projects, folder browser, saving copies, cache

def _abs_folder(raw: str) -> Path:
    import os
    raw = (raw or "").strip().strip('"').strip("'")
    d = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not raw or not d.is_absolute():
        raise HTTPException(400, "Give a full folder path, e.g. /Users/you/Documents or ~/Documents")
    return d


def _project_info(with_state: bool = False) -> dict:
    p = ws.project()
    return {"project": p, "temporary": p is None, "workspace": str(ws.root()), "app_dir": str(ws.APP_DIR),
            "recent": ws.recent(), **({"state": ws.load_state()} if with_state and p else {})}


def _switch_guard():
    busy = [j for j in jobs.list() if j.status in ("queued", "running")]
    if busy:
        raise HTTPException(409, f"Wait for {len(busy)} running job(s) to finish (or cancel them) before switching projects")


@router.get("/api/project")
def project_get():
    return _project_info(with_state=True)


class ProjectNewRequest(BaseModel):
    name: str = Field(max_length=80)
    folder: str = Field(max_length=1000)


@router.post("/api/project/new")
def project_new(req: ProjectNewRequest):
    _switch_guard()
    try:
        folder = ws.create(req.name, _abs_folder(req.folder))
        ws.open_project(folder)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))
    jobs.clear_finished()
    log.info("Project %s created in %s", req.name, folder)
    return _project_info(with_state=True)


class ProjectOpenRequest(BaseModel):
    folder: str = Field(max_length=1000)


@router.post("/api/project/open")
def project_open(req: ProjectOpenRequest):
    _switch_guard()
    try:
        ws.open_project(_abs_folder(req.folder))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))
    jobs.clear_finished()
    log.info("Project opened: %s", ws.root())
    return _project_info(with_state=True)


@router.post("/api/project/close")
def project_close():
    _switch_guard()
    ws.close_project()
    jobs.clear_finished()
    return _project_info()


class ProjectStateRequest(BaseModel):
    state: dict


@router.put("/api/project/state")
def project_state(req: ProjectStateRequest):
    try:
        return {"saved": ws.save_state(req.state)}
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.delete("/api/project/recent")
def project_forget(folder: str):
    ws.forget(folder)
    return {"recent": ws.recent()}


class RevealRequest(BaseModel):
    path: str | None = None


@router.post("/api/project/reveal")
def project_reveal(req: RevealRequest):
    """Show the project folder (or a saved file's folder) in Finder / Explorer / the file manager."""
    import subprocess
    import sys

    target = Path(req.path).expanduser() if req.path else ws.root()
    if not target.is_absolute():
        target = ws.root() / target
    folder = target if target.is_dir() else target.parent
    if not folder.is_dir():
        raise HTTPException(404, "Folder not found")
    cmd = ["open", str(folder)] if sys.platform == "darwin" else ["explorer", str(folder)] if sys.platform.startswith("win") else ["xdg-open", str(folder)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise HTTPException(500, f"Couldn't open the folder: {e}")
    return {"ok": True}


@router.get("/api/fs/list")
def fs_list(path: str = "", products: bool = False):
    """Folders inside a folder, for the folder picker (file contents are never read).

    products=true also lists Sentinel .SAFE.zip files and marks .SAFE product folders."""
    import os

    from lulc_fetch.safe import product_name

    p = _abs_folder(path) if path.strip() else Path.home()
    if not p.is_dir():
        raise HTTPException(404, f"{p} is not a folder")
    dirs = []
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if c.is_dir() and not c.name.startswith(".") and not c.name.endswith((".app", ".photoslibrary")):
                    dirs.append({"name": c.name, "path": str(c), "project": ws.is_project(c),
                                 **({"product": True} if products and product_name(c.name) else {})})
                elif products and c.is_file() and c.suffix.lower() == ".zip" and product_name(c.name):
                    dirs.append({"name": c.name, "path": str(c), "project": False, "product": True, "file": True})
            except OSError:
                continue
            if len(dirs) >= 800:
                break
    except PermissionError:
        raise HTTPException(403, f"No permission to read {p}")
    home = Path.home()
    shortcuts = [("Home", home), ("Desktop", home / "Desktop"), ("Documents", home / "Documents"), ("Downloads", home / "Downloads")]
    if ws.project():
        shortcuts.insert(0, ("This project", ws.root()))
    shortcuts.append(("App folder", ws.APP_DIR))
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs,
            "project": ws.is_project(p), "writable": os.access(p, os.W_OK), "product": bool(products and product_name(p.name)),
            "shortcuts": [{"name": n, "path": str(d)} for n, d in shortcuts if d.is_dir()]}


class MkdirRequest(BaseModel):
    parent: str = Field(max_length=1000)
    name: str = Field(max_length=120)


@router.post("/api/fs/mkdir")
def fs_mkdir(req: MkdirRequest):
    import re

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", req.name).strip(" .")
    if not name:
        raise HTTPException(400, "Give the folder a name")
    d = _abs_folder(req.parent) / name
    try:
        d.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise HTTPException(400, f"{name} already exists")
    except OSError as e:
        raise HTTPException(400, f"Can't create the folder: {e.strerror or e}")
    return {"path": str(d)}








class SaveFilesRequest(BaseModel):
    paths: list[str] = Field(max_length=200)
    folder: str = Field(max_length=1000)


@router.post("/api/files/save")
def files_save(req: SaveFilesRequest):
    """Save copies of workspace files (tool outputs, layers, tables, models) into a folder of the user's choice."""
    folder = _report_folder(req.folder)
    saved, skipped = [], []
    base = ws.root().resolve()
    for raw in req.paths:
        src = (ws.root() / raw).resolve()
        if not src.is_file() or not (src.is_relative_to(base) or src.is_relative_to(ws.APP_DIR)):
            skipped.append({"path": raw, "reason": "not a file in this workspace"})
            continue
        try:
            if src.suffix.lower() == ".vrt":
                saved.append(str(_vrt_to_tif(src, _unique(folder, src.with_suffix(".tif").name))))
            else:
                saved += _save_into(src, folder)
        except OSError as e:
            skipped.append({"path": raw, "reason": str(e)})
    log.info("Saved %d file(s) to %s", len(saved), folder)
    return {"folder": str(folder), "saved": saved, "skipped": skipped}


CACHE_DIRS = ("downloads", "analysis", "uploads", "exports", "imports")


@router.get("/api/cache")
def cache_info():
    """Sizes of the workspace's working folders (the cache of the temporary workspace, or the project's files)."""
    import time as _t

    out = []
    for d in CACHE_DIRS:
        p = ws.root() / d
        size, n, oldest = 0, 0, None
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    st = f.stat()
                    size += st.st_size
                    n += 1
                    oldest = min(oldest or st.st_mtime, st.st_mtime)
        out.append({"folder": d, "size_mb": size / 1e6, "files": n, "oldest_days": None if oldest is None else (_t.time() - oldest) / 86400})
    return {"workspace": str(ws.root()), "temporary": ws.project() is None, "folders": out}


class CacheCleanRequest(BaseModel):
    folders: list[str]
    older_than_days: float = Field(7, ge=0, le=3650)


@router.post("/api/cache/clean")
def cache_clean(req: CacheCleanRequest):
    """Delete working files older than N days (whole job / upload folders, so layers never half-break)."""
    import shutil
    import time as _t

    cutoff = _t.time() - req.older_than_days * 86400
    running = {j.id for j in jobs.list() if j.status in ("queued", "running")}
    freed, removed = 0, 0
    for d in req.folders:
        if d not in CACHE_DIRS:
            continue
        p = ws.root() / d
        if not p.is_dir():
            continue
        for item in p.iterdir():
            if item.name in running:
                continue
            files = [f for f in item.rglob("*") if f.is_file()] if item.is_dir() else [item]
            if not files or max(f.stat().st_mtime for f in files) > cutoff:
                continue
            freed += sum(f.stat().st_size for f in files)
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
            removed += 1
    log.info("Cleaned %d item(s), %.1f MB", removed, freed / 1e6)
    return {"removed": removed, "freed_mb": freed / 1e6}
