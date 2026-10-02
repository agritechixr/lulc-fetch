"""The workspace: where the app reads and writes its files.

Without a project this is the folder the app was started in (a temporary workspace / cache). When a project
is open, it is the project folder: every tool's output (downloads, analysis results, tables, models…) then lands
inside the project, and the project file (lulc_project.json) remembers Contents, the map view and settings.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

APP_DIR = Path.cwd().resolve()            # where the app was started (holds data/ with your .SAFE products)
PROJECT_FILE = "lulc_project.json"
SUBDIRS = ("downloads", "analysis", "tables", "models", "uploads", "exports", "imports")
CONFIG_DIR = Path.home() / ".lulc-fetch"
_lock = threading.Lock()
_root = APP_DIR
_project: dict | None = None


def root() -> Path:
    return _root


def project() -> dict | None:
    return _project


def rel(p: str | Path) -> str:
    """Path for the browser: relative to the workspace when inside it, otherwise absolute."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(_root.resolve()))
    except ValueError:
        return str(p)


class Dir(os.PathLike):
    """A workspace subfolder that follows the current workspace (e.g. tables/ of the open project)."""

    def __init__(self, name: str):
        self.name = name

    @property
    def path(self) -> Path:
        return _root / self.name

    def __fspath__(self):
        return str(self.path)

    def __truediv__(self, other):
        return self.path / other

    def __getattr__(self, attr):
        return getattr(self.path, attr)

    def __str__(self):
        return str(self.path)

    def __repr__(self):
        return f"Dir({self.path})"


# ------------------------------------------------------------------ projects
def _read(folder: Path) -> dict:
    data = json.loads((folder / PROJECT_FILE).read_text(encoding="utf-8"))
    if data.get("app") != "LULC Fetch":
        raise ValueError("This folder's project file wasn't made by LULC Fetch")
    return data


def is_project(folder: str | Path) -> bool:
    return (Path(folder) / PROJECT_FILE).is_file()


def create(name: str, parent: str | Path) -> Path:
    import re
    name = name.strip()
    if not name:
        raise ValueError("Give the project a name")
    parent = Path(parent).expanduser()
    if not parent.is_absolute():
        raise ValueError("Choose a full folder path for the project")
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")[:80] or "project"
    folder = parent / safe
    if is_project(folder):
        raise ValueError(f"{folder} is already a project. Use Open project instead.")
    if folder.exists() and any(folder.iterdir()):
        raise ValueError(f"{folder} already exists and isn't empty. Choose another name or folder.")
    folder.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    data = {"app": "LULC Fetch", "version": 1, "name": name, "created": now, "modified": now, "state": {}}
    (folder / PROJECT_FILE).write_text(json.dumps(data, indent=1), encoding="utf-8")
    for d in SUBDIRS:
        (folder / d).mkdir(exist_ok=True)
    return folder


def open_project(folder: str | Path) -> dict:
    global _root, _project
    folder = Path(folder).expanduser().resolve()
    if not is_project(folder):
        raise ValueError(f"No LULC Fetch project in {folder} (it has no {PROJECT_FILE})")
    data = _read(folder)
    with _lock:
        _root, _project = folder, {"name": data.get("name", folder.name), "folder": str(folder), "created": data.get("created")}
    _remember(folder, data.get("name", folder.name))
    return data


def close_project():
    global _root, _project
    with _lock:
        _root, _project = APP_DIR, None


def save_state(state: dict):
    if _project is None:
        raise ValueError("No project is open")
    folder = Path(_project["folder"])
    with _lock:
        data = _read(folder)
        data.update(state=state, modified=time.strftime("%Y-%m-%d %H:%M:%S"))
        tmp = folder / (PROJECT_FILE + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(folder / PROJECT_FILE)   # atomic: a crash never leaves a half-written project file
    return data["modified"]


def load_state() -> dict:
    return _read(Path(_project["folder"])).get("state", {}) if _project else {}


# ------------------------------------------------------------------ recent projects (~/.lulc-fetch/recent.json)
def _recent_file() -> Path:
    return CONFIG_DIR / "recent.json"


def recent() -> list[dict]:
    try:
        items = json.loads(_recent_file().read_text())
    except (OSError, ValueError):
        return []
    return [{**r, "exists": is_project(r["folder"])} for r in items if isinstance(r, dict) and "folder" in r][:12]


def _remember(folder: Path, name: str):
    items = [r for r in recent() if Path(r["folder"]).resolve() != folder]
    items.insert(0, {"folder": str(folder), "name": name, "opened": time.strftime("%Y-%m-%d %H:%M")})
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        _recent_file().write_text(json.dumps([{k: v for k, v in r.items() if k != "exists"} for r in items[:12]], indent=1))
    except OSError:
        pass


def forget(folder: str):
    items = [r for r in recent() if r["folder"] != folder]
    try:
        _recent_file().write_text(json.dumps([{k: v for k, v in r.items() if k != "exists"} for r in items], indent=1))
    except OSError:
        pass
