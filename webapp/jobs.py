"""Background download jobs with captured logs. Jobs live in memory; files go to ./downloads/<job id>/."""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

DOWNLOAD_DIR = Path.cwd() / "downloads"
_current = threading.local()


@dataclass
class Job:
    id: str
    kind: str
    title: str
    params: dict
    status: str = "queued"  # queued | running | done | error
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    logs: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None

    @property
    def dir(self) -> Path:
        return DOWNLOAD_DIR / self.id

    def files(self) -> list[str]:
        if not self.dir.exists():
            return []
        return sorted(p.name for p in self.dir.iterdir() if p.is_file() and not p.name.endswith(".part"))

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "params": self.params,
                "status": self.status, "created": self.created, "started": self.started,
                "finished": self.finished, "logs": self.logs[-200:], "result": self.result,
                "error": self.error, "files": self.files()}


class _JobLogHandler(logging.Handler):
    """Routes log records emitted on a job's thread into that job's log."""

    def emit(self, record):
        job = getattr(_current, "job", None)
        if job is not None:
            job.logs.append(self.format(record))


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
        _current.job = job
        job.status, job.started = "running", time.time()
        job.dir.mkdir(parents=True, exist_ok=True)
        try:
            job.result = fn(job)
            job.status = "done"
            job.logs.append(f"Finished in {time.time() - job.started:.0f} s")
        except Exception as e:  # report every failure to the UI instead of losing it in the thread
            job.status, job.error = "error", str(e) or type(e).__name__
            job.logs.append(f"ERROR: {job.error}")
            logging.getLogger(__name__).debug(traceback.format_exc())
        finally:
            job.finished = time.time()
            _current.job = None

    def delete(self, job_id: str):
        import shutil

        job = self.jobs.pop(job_id, None)
        if job and job.dir.exists():
            shutil.rmtree(job.dir, ignore_errors=True)
