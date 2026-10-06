"""Workflows: a saved chain of tool runs that can be run again on new inputs (Analysis ▸ Tools ▸ Workflows).

A workflow is made from runs in the History: each step is a run's exact request (endpoint and settings). Inside the
settings, the files and areas a run used become the workflow's **inputs** (to change when it is run), unless an earlier
step made that file: then the step takes **that step's output** instead. Years and dates become inputs too.

The settings hold markers the app replaces when it runs a workflow:
  {"$in": "in1"}                               the value of input in1
  {"$step": 0, "ext": ".tif", "nth": 0}        the first .tif that step 0 made

One JSON file per workflow in workflows/ of the app's folder, so they are there in every project.
"""

from __future__ import annotations

import copy
import json
import re
import time
import uuid
from pathlib import Path

from . import history
from . import workspace as ws

# workspace folders whose files are a run's data (an input, or an earlier step's output)
DATA_PATH = re.compile(r"^(uploads|downloads|tables|models|imports|exports|analysis|training_data)/", re.I)
AREA_KEYS = ("clip", "aoi", "geometry", "area")
VALUE_KEYS = ("year", "years", "date", "start", "end", "start_date", "end_date")
# a workflow only runs tools: never the app's own housekeeping (projects, files, credentials, history, add-ons)
BLOCKED = re.compile(r"^/api/(project|fs|files|history|errors|credentials|cache|dl/install|yolo/install|workflows)", re.I)
RASTER = (".tif", ".tiff", ".vrt", ".img", ".jp2")
TABLE = (".csv", ".tsv", ".parquet", ".xlsx", ".xls")
VECTOR = (".geojson", ".json", ".zip", ".shp", ".kml", ".kmz", ".gpkg")


def folder() -> Path:
    return ws.APP_DIR / "workflows"


def _file(wid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", wid or ""):
        raise ValueError("Not a workflow id")
    return folder() / f"{wid}.json"


def kind_of(path: str) -> str:
    p = path.lower()
    return "raster" if p.endswith(RASTER) else "table" if p.endswith(TABLE) else "vector" if p.endswith(VECTOR) else "file"


def check(wf: dict) -> dict:
    """A workflow as saved: name, description, inputs, steps (endpoint + settings); refuses what it may not run."""
    name = str(wf.get("name") or "").strip()[:120]
    if not name:
        raise ValueError("Give the workflow a name")
    steps = wf.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ValueError("A workflow needs at least one step")
    if len(steps) > 50:
        raise ValueError("A workflow can have up to 50 steps")
    out_steps = []
    for i, s in enumerate(steps):
        ep = str(s.get("endpoint") or "")
        if not ep.startswith("/api/") or BLOCKED.match(ep):
            raise ValueError(f"Step {i + 1} isn't a tool a workflow can run ({ep or 'no endpoint'})")
        if not isinstance(s.get("body"), dict):
            raise ValueError(f"Step {i + 1} has no settings")
        for ref in _refs(s["body"]):
            if "$step" in ref and not (isinstance(ref["$step"], int) and 0 <= ref["$step"] < i):
                raise ValueError(f"Step {i + 1} uses the output of a step that doesn't come before it")
        out_steps.append({"title": str(s.get("title") or f"Step {i + 1}")[:200], "kind": str(s.get("kind") or "")[:60], "endpoint": ep,
                          "body": s["body"]})
    inputs = []
    for p in wf.get("inputs") or []:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", str(p.get("id") or "")):
            raise ValueError("An input has no proper id")
        inputs.append({"id": p["id"], "label": str(p.get("label") or p["id"])[:120], "type": p.get("type") if p.get("type") in ("file", "area", "value") else "value",
                       "kind": str(p.get("kind") or "")[:20], "default": p.get("default")})
    used = {r["$in"] for s in out_steps for r in _refs(s["body"]) if "$in" in r}
    missing = used - {p["id"] for p in inputs}
    if missing:
        raise ValueError(f"The steps use inputs that aren't defined: {', '.join(sorted(missing))}")
    return {"name": name, "description": str(wf.get("description") or "")[:2000], "inputs": inputs, "steps": out_steps}


def _refs(node):
    if isinstance(node, dict):
        if "$in" in node or "$step" in node:
            yield node
        else:
            for v in node.values():
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def listing() -> list[dict]:
    out = []
    for f in sorted(folder().glob("*.json")) if folder().is_dir() else []:
        try:
            w = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"id": f.stem, "name": w.get("name"), "description": w.get("description", ""), "steps": len(w.get("steps") or []),
                    "inputs": len(w.get("inputs") or []), "updated": w.get("updated"), "titles": [s.get("title") for s in w.get("steps") or []][:8]})
    return sorted(out, key=lambda w: w.get("updated") or "", reverse=True)


def get(wid: str) -> dict:
    f = _file(wid)
    if not f.is_file():
        raise FileNotFoundError("No such workflow")
    return {"id": wid, **json.loads(f.read_text(encoding="utf-8"))}


def save(wid: str | None, wf: dict) -> dict:
    data = check(wf)
    wid = wid or uuid.uuid4().hex[:10]
    f = _file(wid)
    old = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    data.update(app="LULC Fetch", version=1, created=old.get("created", now), updated=now)
    folder().mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(f)
    return {"id": wid, **data}


def delete(wid: str):
    _file(wid).unlink(missing_ok=True)


# ------------------------------------------------------------------ making a workflow from History runs

def _label(key_path: list, kind: str) -> str:
    keys = [k for k in key_path if isinstance(k, str)]
    k = (keys[-2] if keys and keys[-1] == "path" and len(keys) > 1 else keys[-1] if keys else "input").replace("_", " ")
    nice = {"path": f"Input {kind}" if kind != "file" else "Input file", "clip": "Area", "aoi": "Area of interest", "geometry": "Area",
            "ground truth": "Ground truth", "table": "Table", "model": "Model", "photos": "Photos"}
    return nice.get(k, k[:1].upper() + k[1:])


def from_history(job_ids: list[str], name: str = "") -> dict:
    """A workflow from History runs, in the order they ran: their settings, with files and areas turned into inputs or
    into earlier steps' outputs. Not saved: the app shows it to be named and saved."""
    runs = []
    for jid in job_ids:
        req, entry = history.get_request(jid), history.get(jid)
        if req is None or entry is None:
            raise ValueError("One of the runs can't be repeated (its settings weren't kept), so it can't be part of a workflow")
        if BLOCKED.match(req.get("endpoint") or ""):
            raise ValueError(f"“{entry.get('title')}” isn't a tool a workflow can run")
        runs.append((entry.get("started") or 0, jid, req, entry))
    runs.sort(key=lambda r: r[0])
    inputs, steps, made = [], [], []   # made[i] = absolute paths step i wrote

    def add_input(**p):
        same = next((x for x in inputs if x["type"] == p["type"] and x["default"] == p["default"]), None)
        if same:
            return same["id"]
        p["id"] = f"in{len(inputs) + 1}"
        inputs.append(p)
        return p["id"]

    for _, jid, req, entry in runs:
        root = Path(req.get("workspace") or ws.root())

        def walk(node, kp):
            if isinstance(node, dict):
                is_area = bool(kp) and kp[-1] in AREA_KEYS and ("coordinates" in node or node.get("type") == "FeatureCollection")
                if is_area:   # an area (a polygon, or polygons): an input
                    return {"$in": add_input(type="area", kind="area", label=_label(kp, "area"), default=node)}
                return {k: walk(v, kp + [k]) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v, kp + [i]) for i, v in enumerate(node)]
            if isinstance(node, str) and DATA_PATH.match(node):
                ab = str((root / node).resolve())
                for i in range(len(made) - 1, -1, -1):   # made by an earlier step: that step's output
                    if ab in made[i]:
                        ext = Path(node).suffix.lower()
                        same_ext = [p for p in made[i] if Path(p).suffix.lower() == ext]
                        return {"$step": i, "ext": ext, "nth": same_ext.index(ab)}
                k = kind_of(node)
                return {"$in": add_input(type="file", kind=k, label=_label(kp, k), default=node)}
            if kp and len(kp) == 1 and kp[0] in VALUE_KEYS and isinstance(node, (int, float, str)) and node != "":
                return {"$in": add_input(type="value", kind=kp[0], label=kp[0].replace("_", " ").capitalize(), default=node)}
            return node

        body = walk(copy.deepcopy(req.get("body") or {}), [])
        steps.append({"title": entry.get("title") or req["endpoint"], "kind": entry.get("kind") or "", "endpoint": req["endpoint"], "body": body})
        made.append([str(Path(p).resolve()) for p in entry.get("outputs") or []])
    titles = [s["title"].split(" · ")[0] for s in steps]
    return {"name": name or " → ".join(dict.fromkeys(titles))[:120], "description": "", "inputs": inputs, "steps": steps}
