"""History of every tool run: what ran, when, how long, with which inputs and settings, and where the results went.

One JSON line per finished run (done / error / cancelled) in logs/history.jsonl in the app's folder, so it covers every
project and survives restarts. Copies saved to a folder afterwards (the tools' "Also save to a folder" option) are added as
extra lines and merged into their run when read. Settings are the request the tool was started with, made safe and small:
passwords and keys are hidden, shapes are summarised (type, bounding box, number of points), long lists are shortened.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from . import workspace as ws

_lock = threading.Lock()
SECRET = re.compile(r"(password|passwd|secret|token|api_?key|access_?key|secret_?key|credential)", re.I)
INPUT_KEYS = ("path", "input", "inputs", "photos", "table", "tables", "dataset", "folder", "layer", "model", "custom", "source",
              "ground_truth", "clip", "geojson", "paths", "product", "products", "aoi", "geometry", "year", "years", "date",
              "start", "end", "collection", "name")


def history_file() -> Path:
    return ws.APP_DIR / "logs" / "history.jsonl"


# ------------------------------------------------------------------ making requests safe and small

def _bbox(coords) -> list[float] | None:
    xs, ys = [], []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)
    walk(coords)
    return [round(min(xs), 5), round(min(ys), 5), round(max(xs), 5), round(max(ys), 5)] if xs else None


def _points(coords) -> int:
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        return 1
    return sum(_points(c) for c in coords) if isinstance(coords, (list, tuple)) else 0


def safe(v, depth: int = 0):
    """A request made safe to keep: secrets hidden, geometries summarised, long things shortened."""
    if depth > 6:
        return "…"
    if isinstance(v, dict):
        t = v.get("type")
        if t in ("Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon") and "coordinates" in v:
            return {"type": t, "bbox_lonlat": _bbox(v["coordinates"]), "points": _points(v["coordinates"])}
        if t == "FeatureCollection":
            fs = v.get("features") or []
            props = sorted({k for f in fs[:200] for k in (f.get("properties") or {})})[:20]
            return {"type": "FeatureCollection", "features": len(fs), "bbox_lonlat": _bbox([f.get("geometry", {}).get("coordinates") for f in fs if f.get("geometry")]),
                    "fields": props}
        if t == "Feature":
            return {"type": "Feature", "geometry": safe(v.get("geometry"), depth + 1)}
        return {k: ("•••" if SECRET.search(k) and v[k] else safe(x, depth + 1)) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        if len(v) > 30:
            return [safe(x, depth + 1) for x in v[:10]] + [f"… {len(v):,} items in all"]
        return [safe(x, depth + 1) for x in v]
    if isinstance(v, str) and len(v) > 400:
        return v[:400] + f"… ({len(v):,} characters)"
    return v


def _abs(p: str) -> str:
    q = Path(p)
    return str(q if q.is_absolute() else ws.root() / q)


def _outputs(job) -> list[str]:
    r, out = job.result or {}, []
    for k in ("path", "output_table", "csv", "geojson_path", "folder", "model_dir", "outputs", "files", "saved"):
        v = r.get(k) if isinstance(r, dict) else None
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and x and not x.startswith(("/api/", "data:", "http")) and len(x) < 1000:
                out.append(_abs(x))
    if not out and job.dir.exists():
        out = [str(f) for f in sorted(job.dir.iterdir()) if f.is_file() and not f.name.endswith((".part", ".log"))][:50]
    return list(dict.fromkeys(out))


def _summary(job) -> dict:
    """Small, readable numbers and words from the result (counts, scores, sizes, seconds…)."""
    r = job.result if isinstance(job.result, dict) else {}
    out = {}
    for k, v in r.items():
        if k in ("path", "outputs", "files", "image", "geojson", "photos", "rgba", "preview", "logs"):
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)) or (isinstance(v, str) and len(v) < 200 and not v.startswith("data:")):
            out[k] = v
        elif isinstance(v, dict) and len(json.dumps(v, default=str)) < 600:
            out[k] = v
        if len(out) >= 40:
            break
    return out


# ------------------------------------------------------------------ write / read

def record(job):
    """Append a finished run (called by the job manager for done, error and cancelled jobs)."""
    req = getattr(job, "request", None) or {}
    body = safe(req.get("body") or {})
    inputs = {k: body[k] for k in INPUT_KEYS if isinstance(body, dict) and k in body and body[k] not in (None, "", [], {})}
    settings = {k: v for k, v in body.items() if k not in inputs} if isinstance(body, dict) else {"request": body}
    proj = ws.project()
    entry = {
        "type": "run", "id": job.id, "kind": job.kind, "title": job.title, "status": job.status, "endpoint": req.get("endpoint"),
        "started": job.started or job.created, "finished": job.finished or time.time(),
        "seconds": round((job.finished or time.time()) - (job.started or job.created), 2),
        "project": proj["name"] if proj else None, "workspace": str(ws.root()),
        "params": safe(job.params), "inputs": inputs, "settings": settings,
        "outputs": _outputs(job) if job.status == "done" else [], "summary": _summary(job) if job.status == "done" else {},
        "error": job.error, "log": [str(x)[:300] for x in job.logs[-40:]],
    }
    _append(entry)


def record_copy(job_id: str, folder: str, files: list[str]):
    _append({"type": "copy", "id": job_id, "time": time.time(), "folder": folder, "files": files[:200]})


def _append(entry: dict):
    f = history_file()
    with _lock:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            if f.is_file() and f.stat().st_size > 20_000_000:   # keep it quick to read: start a new file, keep one old one
                f.replace(f.with_suffix(".old.jsonl"))
            with open(f, "a", encoding="utf-8") as out:
                out.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
        except OSError:
            pass


def _read() -> list[dict]:
    f = history_file()
    if not f.is_file():
        return []
    runs, copies = {}, {}
    with _lock, open(f, encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "copy":
                copies.setdefault(e["id"], []).append({k: e[k] for k in ("time", "folder", "files")})
            else:
                runs[e["id"]] = e
    for jid, c in copies.items():
        if jid in runs:
            runs[jid]["copies"] = c
    return sorted(runs.values(), key=lambda e: e.get("finished") or 0, reverse=True)


def listing(running: list[dict], q: str = "", status: str = "", limit: int = 200) -> dict:
    """Newest first: runs still going (from memory), then finished ones (from the file); short rows only."""
    rows = running + _read()
    if q:
        words = q.lower().split()
        rows = [r for r in rows if all(w in json.dumps({k: r.get(k) for k in ("title", "kind", "inputs", "settings", "outputs", "error", "project")},
                                                       default=str).lower() for w in words)]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    keep = ("id", "kind", "title", "status", "started", "finished", "seconds", "project", "error")
    return {"total": len(rows), "file": str(history_file()),
            "rows": [{**{k: r.get(k) for k in keep}, "outputs": len(r.get("outputs") or []), "copies": len(r.get("copies") or [])}
                     for r in rows[:limit]]}


def get(job_id: str) -> dict | None:
    return next((r for r in _read() if r["id"] == job_id), None)


def clear():
    f = history_file()
    with _lock:
        if f.is_file():
            f.replace(f.with_suffix(".old.jsonl"))
