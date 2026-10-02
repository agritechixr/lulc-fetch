"""Launch the Python runner (lulc_fetch.pyrunner) in a separate process, with a time limit and Cancel."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import progress

MAX_SECONDS = 600
MAX_OUTPUT = 20000


def run(in_parquet: Path, code: str, timeout: int = MAX_SECONDS) -> dict:
    """Run `code` on the table in in_parquet. Returns {"ok", "output", "error", "out" (parquet path), "meta"}."""
    if not code.strip():
        raise ValueError("Write some Python first")
    tmp = Path(tempfile.mkdtemp(prefix="lulc_py_"))
    script, out, meta = tmp / "script.py", tmp / "out.parquet", tmp / "meta.json"
    script.write_text(code, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "lulc_fetch.pyrunner", str(in_parquet), str(script), str(out), str(meta)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    t0 = time.time()
    try:
        while proc.poll() is None:
            elapsed = time.time() - t0
            if elapsed > timeout:
                proc.kill()
                raise ValueError(f"The script ran longer than {timeout // 60} minutes and was stopped")
            progress.update(min(0.95, elapsed / 60), f"Running your Python ({elapsed:.0f} s)")   # raises Cancelled on Cancel
            time.sleep(0.2)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    stdout, stderr = proc.communicate()
    output = stdout[-MAX_OUTPUT:] if stdout else ""
    if proc.returncode != 0:
        return {"ok": False, "output": output, "error": (stderr or "The script failed").strip()[-4000:], "dir": str(tmp)}
    return {"ok": True, "output": output, "error": None, "out": str(out), "meta": json.loads(meta.read_text()), "dir": str(tmp)}
