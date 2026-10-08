"""One OpenMP runtime per process on macOS.

PyTorch ships its own libomp.dylib; XGBoost and LightGBM load another one (Homebrew's, or the one bundled with the
app). Two OpenMP runtimes in one process crash (segmentation fault) as soon as the second one runs: e.g. diagnosing a
crop photo (PyTorch) and then training XGBoost in the same session. macOS only lets the library search path be set
when a process starts, so the process is started again once with PyTorch's lib folder first on DYLD_LIBRARY_PATH: every
library asking for libomp.dylib then gets PyTorch's copy, the same one.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_DONE = "LULC_ONE_OPENMP"


def torch_lib_dir() -> Path | None:
    """PyTorch's lib folder when it has its own libomp.dylib (without importing PyTorch)."""
    try:
        spec = importlib.util.find_spec("torch")
    except (ImportError, ValueError):
        return None
    if not spec or not spec.origin:
        return None
    d = Path(spec.origin).parent / "lib"
    return d if (d / "libomp.dylib").exists() else None


def needs_restart() -> bool:
    """Whether ensure_single_runtime would start the process again."""
    if sys.platform != "darwin" or os.environ.get(_DONE):
        return False
    d = torch_lib_dir()
    return d is not None and str(d) not in os.environ.get("DYLD_LIBRARY_PATH", "").split(os.pathsep)


def ensure_single_runtime(argv: list[str]) -> None:
    """On macOS with PyTorch installed, restart this process (argv: the command to run again) with one shared OpenMP.
    Does nothing elsewhere, without PyTorch, or once already done."""
    if sys.platform != "darwin" or os.environ.get(_DONE):
        return
    d = torch_lib_dir()
    if d is None:
        return
    paths = [p for p in os.environ.get("DYLD_LIBRARY_PATH", "").split(os.pathsep) if p]
    os.environ[_DONE] = "1"   # never more than one restart, even if the path doesn't take
    if str(d) in paths:
        return
    os.environ["DYLD_LIBRARY_PATH"] = os.pathsep.join([str(d), *paths])
    for f in (sys.stdout, sys.stderr):   # (None in a windowed app)
        if f:
            f.flush()
    os.execv(sys.executable, argv)
