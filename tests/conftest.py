"""Shared setup: a throwaway workspace, the app's API client, and small synthetic data.

Nothing touches your real files: LULC_HOME and the settings folder (~/.lulc-fetch) point into a temporary folder for
the whole session. Run from the project folder:

    .venv/bin/python -m pytest tests                 # everything that works offline
    .venv/bin/python -m pytest tests -m "not dl"     # skip deep learning (fast: about a minute)
    .venv/bin/python -m pytest tests --network       # also the tests that need the internet (imagery search)
    .venv/bin/python -m pytest tests/real --real     # the real-data suite (the data/ folder; see tests/real/README.md)

Every run writes a report (one folder per tool) to test_reports/; open test_reports/index.html.

Deep-learning tests are skipped automatically when the PyTorch add-on (marker dl) or the YOLO & SAM add-on (marker yolo)
isn't installed; tests marked weights download small pretrained weights once (needs internet the first time).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---- an isolated workspace, set before the app is imported (the workspace folder is fixed at import)
_HOME = Path(tempfile.mkdtemp(prefix="lulc_tests_")).resolve()
os.environ["LULC_HOME"] = str(_HOME)
os.environ.setdefault("YOLO_VERBOSE", "False")

import webapp.workspace as ws  # noqa: E402

ws.CONFIG_DIR = _HOME / ".lulc-fetch"   # remembered models / products / recent projects

from tests import _data  # noqa: E402
from tests.report import RunReport, TestReport  # noqa: E402

REAL_DIR = Path(__file__).resolve().parent / "real"
_run: RunReport | None = None


def pytest_addoption(parser):
    parser.addoption("--network", action="store_true", help="also run tests that need the internet (imagery search, geocoding)")
    parser.addoption("--keep", action="store_true", help="keep the temporary workspace after the run (its path is printed)")
    parser.addoption("--real", action="store_true", help="run the real-data suite in tests/real (uses the data folder)")
    parser.addoption("--data", default=str(ROOT / "data"), help="the real-data folder (default: data/ in the project)")
    parser.addoption("--report-dir", default=str(ROOT / "test_reports"), help="where run reports are written (default: test_reports/)")
    parser.addoption("--no-report", action="store_true", help="don't write a run report")
    parser.addoption("--real-size", default=os.environ.get("LULC_REAL_SIZE", "small"), choices=["small", "medium", "large"],
                     help="how much real data the real-data suite uses: small (default), medium, large")


def pytest_configure(config):
    for m, doc in (("network", "needs the internet (skipped unless --network)"),
                   ("dl", "needs the deep-learning add-on (PyTorch)"),
                   ("yolo", "needs the YOLO & SAM add-on (ultralytics)"),
                   ("weights", "downloads pretrained weights the first time (internet)"),
                   ("slow", "takes more than ~10 s")):
        config.addinivalue_line("markers", f"{m}: {doc}")
    config.addinivalue_line("markers", "real: uses the real data folder (run with --real)")


def _have(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def pytest_collection_modifyitems(config, items):
    skip_net = pytest.mark.skip(reason="needs the internet: run with --network")
    skip_dl = pytest.mark.skip(reason="deep-learning add-on (torch, torchvision, segmentation-models-pytorch) not installed")
    skip_yolo = pytest.mark.skip(reason="YOLO & SAM add-on (ultralytics) not installed")
    has_dl = all(_have(m) for m in ("torch", "torchvision", "segmentation_models_pytorch"))
    if not config.getoption("--real"):   # the real-data suite only runs with --real (it isn't even listed otherwise)
        keep = [it for it in items if not Path(str(it.fspath)).is_relative_to(REAL_DIR)]
        if len(keep) != len(items):
            config.hook.pytest_deselected(items=[it for it in items if it not in keep])
            items[:] = keep
    for it in items:
        if "network" in it.keywords and not config.getoption("--network"):
            it.add_marker(skip_net)
        if ("dl" in it.keywords or "yolo" in it.keywords) and not has_dl:
            it.add_marker(skip_dl)
        if "yolo" in it.keywords and not _have("ultralytics"):
            it.add_marker(skip_yolo)


def pytest_sessionstart(session):
    global _run
    cfg = session.config
    base = Path(cfg.getoption("--report-dir")) if not cfg.getoption("--no-report") else _HOME / "_report"
    _run = RunReport(base, "real" if cfg.getoption("--real") else "synthetic")
    if cfg.getoption("--real"):
        _run.extra["data_folder"] = cfg.getoption("--data")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if _run is None or (rep.when == "teardown" and rep.outcome == "passed"):
        return
    if rep.when == "setup" and rep.outcome == "passed":
        return
    tool = RunReport.tool_of(item.module.__name__.rsplit(".", 1)[-1], item.module)
    text = None
    if rep.outcome != "passed":
        lr = rep.longrepr
        text = lr[2] if isinstance(lr, tuple) else str(lr)
    _run.add_result(tool, item.name, rep.outcome, rep.duration, text)


def pytest_sessionfinish(session, exitstatus):
    if _run is not None and _run.tools and not session.config.getoption("--no-report"):
        out = _run.finish(int(exitstatus))
        print(f"\nTest report: {out / 'index.html'}\nAll runs:    {out.parent / 'index.html'}")
    if session.config.getoption("--keep"):
        print(f"\nTest workspace kept: {_HOME}")
    else:
        shutil.rmtree(_HOME, ignore_errors=True)


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="session")
def home() -> Path:
    return _HOME


@pytest.fixture
def report(request) -> TestReport:
    """Add metrics, tables, images and files to this tool's section of the run report."""
    tool = RunReport.tool_of(request.module.__name__.rsplit(".", 1)[-1], request.module)
    return TestReport(_run.tool(tool), request.node.name)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from webapp.server import app
    # the app only answers local requests (anti-CSRF), so call it as 127.0.0.1
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture(scope="session")
def data(client) -> dict:
    """The synthetic files, in the workspace's uploads/ folder (where the app accepts rasters)."""
    d = _data.make_all(ws.root() / "uploads" / "testdata")
    rel = lambda name: f"uploads/testdata/{name}"   # noqa: E731
    d.update(s2=rel("s2.tif"), labels=rel("labels.tif"), aerial=rel("aerial.tif"))
    return d


@pytest.fixture(scope="session")
def s2_info(client, data) -> dict:
    r = client.get("/api/rasters/info", params={"path": data["s2"]})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="session")
def table(client, data) -> dict:
    """A training table made with Raster → table: the 6 bands of s2.tif plus the true class (column "label")."""
    from tests.helpers import run
    r = run(client, "/api/tables/from-raster", {"path": data["s2"], "ground_truth": {"type": "raster", "path": data["labels"], "band": 1},
                                                "name": "s2_table", "sampling": "stratified", "per_class": 300})
    return {"path": r["path"], "features": ["B02", "B03", "B04", "B08", "B11", "B12"], "target": "label", "report": r}
