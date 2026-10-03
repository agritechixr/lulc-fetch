"""Background download jobs with captured logs. Jobs live in memory; files go to ./downloads/<job id>/."""

from __future__ import annotations

import logging
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import workspace as ws
from .workspace import Dir

DOWNLOAD_DIR = Dir("downloads")   # follows the open project


def errors_log() -> Path:
    """Every failed job is recorded here (in the app's folder, whichever project is open)."""
    return ws.APP_DIR / "logs" / "errors.log"


def record_error(job: "Job", exc: BaseException):
    """Append a readable entry for a failed job to errors.log: when, which tool, its settings, the error, the last steps and the
    traceback (also the deep-learning helper process's), so the failure can be looked into later."""
    import json
    import platform
    import sys

    try:
        from lulc_fetch import __version__ as version
    except Exception:
        version = "?"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip() if exc.__traceback__ else "(seen in the browser: no server traceback)"
    child = getattr(exc, "child_trace", None)
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"==== {when} · {job.title} ({job.kind}, job {job.id}) " + "=" * 20,
             f"LULC Fetch {version} · Python {platform.python_version()} · {platform.system()} {platform.release()} {platform.machine()}"
             f"{' · desktop app' if getattr(sys, 'frozen', False) else ''}",
             f"Workspace: {ws.root()}",
             f"Settings: {json.dumps(job.params, default=str, ensure_ascii=False)}",
             f"Ran for: {time.time() - (job.started or time.time()):.1f} s",
             f"ERROR: {job.error}",
             "Last steps:", *[f"  {x}" for x in job.logs[-40:-1]],
             "Traceback:", *[f"  {x}" for x in tb.splitlines()]]
    if child:
        lines += ["Traceback in the deep-learning helper process:", *[f"  {x}" for x in child.rstrip().splitlines()]]
    try:
        f = errors_log()
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.is_file() and f.stat().st_size > 5_000_000:   # keep it readable: start a new file, keep one old one
            f.replace(f.with_suffix(".old.log"))
        with open(f, "a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n\n")
    except OSError:
        pass
_current = threading.local()


@dataclass
class Job:
    id: str
    kind: str
    title: str
    params: dict
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0   # 0–1, reported by the work through lulc_fetch.progress
    message: str = ""
    cancel_requested: bool = False
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    logs: list[str] = field(default_factory=list)
    result: dict | None = None
    live: dict | None = None   # live data published by the work (lulc_fetch.progress.live), e.g. training curves
    error: str | None = None
    base: Path = field(default_factory=lambda: DOWNLOAD_DIR.path)   # fixed when the job is created

    @property
    def dir(self) -> Path:
        return self.base / self.id

    def files(self) -> list[str]:
        if not self.dir.exists():
            return []
        return sorted(p.name for p in self.dir.iterdir() if p.is_file() and not p.name.endswith(".part"))

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "params": self.params,
                "status": self.status, "progress": round(self.progress, 4), "message": self.message,
                "created": self.created, "started": self.started,
                "finished": self.finished, "logs": self.logs[-200:], "result": self.result, "live": self.live,
                "error": self.error, "files": self.files()}


class _JobLogHandler(logging.Handler):
    """Routes log records emitted on a job's thread into that job's log (and its current step)."""

    def emit(self, record):
        job = getattr(_current, "job", None)
        if job is not None:
            job.logs.append(self.format(record))
            job.message = record.getMessage()[:160]


class JobManager:
    def __init__(self, workers: int = 2):
        self.jobs: dict[str, Job] = {}
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix="job")
        handler = _JobLogHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        for name in ("lulc_fetch", "webapp"):
            lg = logging.getLogger(name)
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)

    def submit(self, kind: str, title: str, params: dict, fn: Callable[[Job], dict]) -> Job:
        job = Job(uuid.uuid4().hex[:10], kind, title, params)
        self.jobs[job.id] = job
        self.pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn):
        from lulc_fetch import progress

        _current.job = job
        if job.cancel_requested:  # cancelled while still queued
            job.status, job.finished = "cancelled", time.time()
            return
        job.status, job.started = "running", time.time()
        job.dir.mkdir(parents=True, exist_ok=True)

        def on_progress(fraction, message):
            if job.cancel_requested:
                raise progress.Cancelled()
            if fraction is not None:
                job.progress = max(job.progress, fraction)  # never jump backwards
            if message and message != job.message:
                job.message = message
                # every step also goes into the job's log (shown under ⓘ): a message that only changes its numbers
                # ("file 3 of 9", "40 of 95 MB") updates its line instead of adding one
                key = re.sub(r"\d+(?:[.,]\d+)?", "#", message)
                line = f"{time.strftime('%H:%M:%S')}  {message}"
                if step["key"] == key and step["i"] == len(job.logs) - 1:
                    job.logs[-1] = line
                else:
                    job.logs.append(line)
                    step.update(key=key, i=len(job.logs) - 1)

        step = {"key": None, "i": -1}
        progress.set_handler(on_progress)
        progress.set_live_handler(lambda data: setattr(job, "live", data))
        try:
            job.result = fn(job)
            job.status, job.progress = "done", 1.0
            job.logs.append(f"Finished in {time.time() - job.started:.0f} s")
        except progress.Cancelled:
            import shutil

            job.status = "cancelled"
            job.logs.append("Cancelled by the user. Partial files were removed.")
            shutil.rmtree(job.dir, ignore_errors=True)
        except Exception as e:  # report every failure to the UI instead of losing it in the thread
            job.status, job.error = "error", str(e) or type(e).__name__
            job.logs.append(f"ERROR: {job.error}")
            logging.getLogger(__name__).debug(traceback.format_exc())
            record_error(job, e)
        finally:
            progress.set_handler(None)
            progress.set_live_handler(None)
            job.finished = time.time()
            _current.job = None

    def list(self) -> list[Job]:
        return list(self.jobs.values())

    def clear_finished(self):
        """Forget finished jobs (their files stay on disk), e.g. when another project is opened."""
        for jid in [j.id for j in self.jobs.values() if j.status in ("done", "error", "cancelled")]:
            self.jobs.pop(jid, None)

    def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancel_requested = True
            job.message = "Cancelling…"
            if job.status == "queued":
                job.status = "cancelled"
        return job

    def delete(self, job_id: str):
        import shutil

        job = self.jobs.pop(job_id, None)
        if job and job.dir.exists():
            shutil.rmtree(job.dir, ignore_errors=True)
