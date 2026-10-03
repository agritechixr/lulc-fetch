"""Runs deep-learning training / prediction / object detection in a separate process.

PyTorch ships its own OpenMP runtime, and XGBoost / LightGBM (used by the classical ML tools) load another one;
two OpenMP runtimes in one process can crash it. A separate process also keeps the app alive if training fails
badly, and gives all memory back when training ends.

Protocol: the child prints JSON lines on stdout — {"type": "progress" | "live" | "log" | "result" | "error" | "cancelled"};
other output (e.g. download progress bars) is plain text. The parent asks the child to stop by creating the stop file;
training then finishes like early stopping (the best model is kept).

    python -m lulc_fetch.dlrunner <request.json>          (frozen app: "LULC Fetch" --lulc-dlrunner <request.json>)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import progress

log = logging.getLogger(__name__)


def _emit(obj):
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


class _JsonLog(logging.Handler):
    def emit(self, record):
        try:
            _emit({"type": "log", "msg": record.getMessage()})
        except Exception:
            pass


def cli(argv: list[str]) -> int:
    req = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    stop = Path(req["stop_file"])
    h = _JsonLog()
    for name in ("lulc_fetch",):
        lg = logging.getLogger(name)
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False

    def on_progress(fraction, message):
        if stop.exists():
            raise progress.Cancelled()
        _emit({"type": "progress", "f": fraction, "m": message})

    progress.set_handler(on_progress)
    progress.set_live_handler(lambda data: _emit({"type": "live", "data": data}))
    from . import dl
    try:
        if req["action"] == "train":
            res = dl.train(**req["kwargs"])
        elif req["action"] == "predict":
            res = dl.predict(**req["kwargs"])
        elif req["action"] == "detect":
            from . import detect
            res = detect.detect(**req["kwargs"])
        elif req["action"] == "train_detector":
            from . import dettrain
            res = dettrain.train(**req["kwargs"])
        elif req["action"] == "diagnose":
            from .agri import disease
            res = disease.diagnose(**req["kwargs"])
        elif req["action"] == "status":
            res = dl.status()
        else:
            raise ValueError(f"Unknown action {req['action']}")
        _emit({"type": "result", "result": res})
        return 0
    except progress.Cancelled:
        _emit({"type": "cancelled"})
        return 0
    except Exception as e:
        import traceback
        _emit({"type": "error", "msg": str(e) or type(e).__name__, "trace": traceback.format_exc()[-3000:]})
        return 1


def run(action: str, **kwargs) -> dict:
    """Run dl.<action>(**kwargs) in a child process, relaying progress, live data and logs to the calling job."""
    tmp = Path(tempfile.mkdtemp(prefix="lulc_dl_"))
    stop = tmp / "stop"
    reqf = tmp / "request.json"
    reqf.write_text(json.dumps({"action": action, "kwargs": {k: (str(v) if isinstance(v, Path) else v) for k, v in kwargs.items()},
                                "stop_file": str(stop)}, default=str), encoding="utf-8")
    cmd = [sys.executable, "--lulc-dlrunner", str(reqf)] if getattr(sys, "frozen", False) else [sys.executable, "-m", "lulc_fetch.dlrunner", str(reqf)]
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            bufsize=1, env=env, creationflags=flags)
    result, error, trace, cancelled, stopping = None, None, None, False, False
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if not line.startswith('{"type"'):
                text = line.split("\r")[-1].replace("\x1b[K", "").strip()
                if text and "%|" not in text and "━" not in text:   # skip tqdm / ultralytics progress bars
                    log.info(text[:300])
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            t = msg.get("type")
            if t == "progress":
                try:
                    progress.update(msg.get("f"), msg.get("m"))
                except progress.Cancelled:
                    if not stopping:   # ask the child to stop; it finishes up (training keeps the best model)
                        stopping = True
                        stop.write_text("stop")
                        log.info("Stopping…")
            elif t == "live":
                progress.live(msg["data"])
            elif t == "log":
                log.info(msg["msg"])
            elif t == "result":
                result = msg["result"]
            elif t == "error":
                error, trace = msg["msg"], msg.get("trace")
                log.debug(trace or "")
            elif t == "cancelled":
                cancelled = True
        code = proc.wait()
    except BaseException:
        proc.kill()
        raise
    finally:
        for p in (stop, reqf):
            p.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass
    if cancelled:
        raise progress.Cancelled()
    if error:
        e = RuntimeError(error)
        e.child_trace = trace   # the helper process's traceback, for the error log
        raise e
    if result is None:
        raise RuntimeError(f"The deep-learning process stopped unexpectedly (exit code {code}). See the log; "
                           "lowering the batch size or the patch size can help if memory ran out.")
    return result


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
