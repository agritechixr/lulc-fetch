"""History (every tool run) and the error log (every failed run). Shared helpers come from webapp/core.py."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs

router = APIRouter()


# ------------------------------------------------------------------ History (every tool run, kept in logs/history.jsonl)

def _running_rows() -> list[dict]:
    return [{"id": j.id, "kind": j.kind, "title": j.title, "status": j.status, "started": j.started or j.created, "finished": None,
             "seconds": round(time.time() - (j.started or j.created), 1), "project": (ws.project() or {}).get("name"), "error": None}
            for j in sorted(jobs.list(), key=lambda j: -j.created) if j.status in ("queued", "running")]


@router.get("/api/history")
def history_list(q: str = "", status: str = "", limit: int = 200):
    from .. import history
    return history.listing(_running_rows(), q=q[:200], status=status, limit=max(1, min(limit, 2000)))


@router.get("/api/history/{job_id}")
def history_get(job_id: str):
    from .. import history
    e = history.get(job_id)
    if e is None:
        j = next((j for j in jobs.list() if j.id == job_id), None)
        if j is None:
            raise HTTPException(404, "Not in the history")
        req = j.request or {}
        e = {"id": j.id, "kind": j.kind, "title": j.title, "status": j.status, "started": j.started or j.created, "finished": None,
             "seconds": round(time.time() - (j.started or j.created), 1), "endpoint": req.get("endpoint"), "params": history.safe(j.params),
             "settings": history.safe(req.get("body") or {}), "inputs": {}, "outputs": [], "summary": {}, "error": None, "log": j.logs[-40:],
             "project": (ws.project() or {}).get("name"), "workspace": str(ws.root())}
    return e


@router.get("/api/history/{job_id}/request")
def history_request(job_id: str):
    """The exact settings a run was started with, to run it again (as they were, or changed)."""
    from .. import history
    r = history.get_request(job_id)
    if r is None:
        raise HTTPException(404, "This run's settings weren't kept, so it can't be repeated")
    return r


@router.delete("/api/history")
def history_clear():
    from .. import history
    history.clear()
    return {"ok": True}


class HistoryCopy(BaseModel):
    folder: str = Field(max_length=2000)
    files: list[str] = Field(default_factory=list, max_length=500)


@router.post("/api/history/{job_id}/copy")
def history_copy(job_id: str, req: HistoryCopy):
    from .. import history
    history.record_copy(job_id, req.folder, req.files)
    return {"ok": True}


# ------------------------------------------------------------------ error log (every failed tool run)

@router.get("/api/errors")
def errors_info():
    from ..jobs import errors_log
    f = errors_log()
    text = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
    return {"path": str(f), "exists": f.is_file(), "entries": text.count("\n==== ") + text.startswith("==== "),
            "size_kb": round(len(text.encode()) / 1000, 1)}


class ErrorReport(BaseModel):
    title: str = Field("", max_length=300)
    error: str = Field("", max_length=4000)
    tool: str = Field("", max_length=60)


@router.post("/api/errors/report")
def errors_report(req: ErrorReport):
    """A failure seen only in the browser (a request that failed outside a background job): recorded like a job's."""
    from ..jobs import Job, record_error
    job = Job("browser", req.tool or "tool", req.title or "A request", {"tool": req.tool})
    job.error = req.error or "Unknown error"
    record_error(job, RuntimeError(job.error))
    return {"ok": True}


@router.get("/api/errors/file")
def errors_file():
    """The error log as plain text (opens in a browser tab)."""
    from fastapi.responses import PlainTextResponse

    from ..jobs import errors_log
    f = errors_log()
    body = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else "No errors recorded yet.\n"
    return PlainTextResponse(f"LULC Fetch error log · {f}\n\n{body}", headers={"Cache-Control": "no-store"})


@router.post("/api/errors/reveal")
def errors_reveal():
    import subprocess
    import sys

    from ..jobs import errors_log
    f = errors_log()
    f.parent.mkdir(parents=True, exist_ok=True)
    target = f if f.is_file() else f.parent
    cmd = (["open", "-R", str(target)] if sys.platform == "darwin" else ["explorer", f"/select,{target}"] if sys.platform.startswith("win")
           else ["xdg-open", str(target.parent)])
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise HTTPException(500, f"Couldn't open the folder: {e}")
    return {"ok": True, "path": str(f)}
