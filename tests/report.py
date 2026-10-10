"""Test-run reports: one folder per run, one sub-folder per tool, and a history of all runs.

    test_reports/
      index.html                      every run, newest first (version, commit, passed / failed, key numbers)
      history.csv                     the same as a table
      2026-10-03_101500_real/
        index.html                    this run: environment, totals, one row per tool
        summary.json
        classical_ml/
          index.html                  tests (passed / failed / skipped, time, error), metrics, tables, images, files
          results.json
          *.png, *.html …             what the tests saved (quicklooks, maps, model reports)

Tests add content with the `report` fixture: report.metric(), report.table(), report.image(), report.file(), report.note().
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import json
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# test module → tool (a module can also set TOOL = "…")
TOOLS = {
    "test_app": "App", "test_find_imagery": "Find imagery", "test_index_analysis": "Index analysis", "test_pca": "PCA & dimensionality reduction",
    "test_stack": "Stack layers", "test_raster_to_table": "Raster → table & data viewer", "test_classical_ml": "Classical ML (tabular data)",
    "test_unsupervised": "Classical ML: unsupervised", "test_raster_ml": "Classical ML for raster", "test_export": "Export data",
    "test_training_data": "Make training data", "test_train_classify_model": "Train classify model & Classify image",
    "test_detection": "Detect object & Train detection model", "test_jobs_and_cli": "Downloads & jobs, command line",
    "test_agri": "Agri: Diagnose crop disease & Crop disease guide",
    "test_embeddings": "Satellite embeddings",
    "test_lightseg": "Light segmentation models",
    "test_online_field_gpkg": "Online layers, GeoPackage & field collection",
    "test_mosaic_burn_spatial": "Mosaic, burn severity & spatial statistics",
    "test_crs": "Coordinate systems & georeferencing",
    "test_workflow_conditions": "Workflows: conditions, checks & diagram",
    "test_fuzzy": "Fuzzy & suitability",
    "test_sar": "SAR (Sentinel-1)",
    "test_watermask": "Water mask",
    "test_imagefeatures": "Image features & thresholds",
    "test_geo_tools": "Hydrology, visibility, LiDAR, spectral & routing",
    "test_hydro": "Hydrology & watersheds",
    "test_hydro2": "Hydrology models, AHP & SAR flood ML",
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower().replace("&", "and").replace("→", "to")).strip("_")[:60]


def _version() -> dict:
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    info = {"app_version": m.group(1) if m else "?", "commit": git("rev-parse", "--short", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "changed_files": len([x for x in git("status", "--porcelain").splitlines() if x.strip()]),
            "python": platform.python_version(), "platform": f"{platform.system()} {platform.release()} {platform.machine()}"}
    import importlib.metadata as md
    for pkg in ("numpy", "rasterio", "scikit-learn", "xgboost", "lightgbm", "torch", "torchvision", "segmentation-models-pytorch", "ultralytics"):
        try:
            info[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            info[pkg] = None
    return info


class ToolReport:
    """What one tool's tests collected during the run."""

    def __init__(self, run: "RunReport", tool: str):
        self.run, self.tool, self.dir = run, tool, run.dir / slug(tool)
        self.tests: dict[str, dict] = {}
        self.metrics: list[dict] = []
        self.tables: list[dict] = []
        self.images: list[dict] = []
        self.files: list[dict] = []
        self.notes: list[str] = []

    def _mk(self):
        self.dir.mkdir(parents=True, exist_ok=True)


class TestReport:
    """The `report` fixture: what a single test adds to its tool's report."""

    def __init__(self, tool: ToolReport, test: str):
        self.tool, self.test = tool, test

    def metric(self, name: str, value, unit: str = "", good: str | None = None):
        """A number worth tracking across versions (e.g. accuracy). good = 'high' or 'low' for colour hints."""
        v = float(value) if isinstance(value, (int, float)) or hasattr(value, "item") else value
        self.tool.metrics.append({"test": self.test, "name": name, "value": v, "unit": unit, "good": good})
        return value

    def table(self, title: str, header: list[str], rows: list[list]):
        self.tool.tables.append({"test": self.test, "title": title, "header": header, "rows": [[_cell(c) for c in r] for r in rows]})

    def note(self, text: str):
        self.tool.notes.append(f"{self.test}: {text}")

    def image(self, name: str, img, caption: str = "") -> Path:
        """Save a picture: a PIL image, an (H, W) / (H, W, 3) uint8 array, or an existing image file."""
        from PIL import Image
        self.tool._mk()
        out = self.tool.dir / f"{slug(name)}.png"
        if isinstance(img, (str, Path)):
            Image.open(img).convert("RGB").save(out)
        elif isinstance(img, Image.Image):
            img.save(out)
        else:
            import numpy as np
            a = np.asarray(img)
            Image.fromarray(a.astype("uint8")).save(out)
        self.tool.images.append({"test": self.test, "file": out.name, "caption": caption or name})
        return out

    def file(self, src: str | Path, name: str | None = None, caption: str = "") -> Path | None:
        """Copy a file (e.g. a model's HTML report) into the tool's folder."""
        src = Path(src)
        if not src.is_file():
            return None
        self.tool._mk()
        out = self.tool.dir / (name or src.name)
        shutil.copy2(src, out)
        self.tool.files.append({"test": self.test, "file": out.name, "caption": caption or out.name})
        return out


def _cell(c):
    if isinstance(c, float):
        return round(c, 4)
    if hasattr(c, "item"):
        return _cell(c.item())
    return c


class RunReport:
    def __init__(self, base: Path, suite: str):
        self.started = time.time()
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.base = base
        self.dir = base / f"{stamp}_{suite}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.suite = suite
        self.tools: dict[str, ToolReport] = {}
        self.env = _version()
        self.extra: dict = {}

    def tool(self, name: str) -> ToolReport:
        if name not in self.tools:
            self.tools[name] = ToolReport(self, name)
        return self.tools[name]

    @staticmethod
    def tool_of(module: str, mod_obj=None) -> str:
        return getattr(mod_obj, "TOOL", None) or TOOLS.get(module) or module.removeprefix("test_").replace("_", " ").capitalize()

    def add_result(self, tool: str, test: str, outcome: str, duration: float, longrepr: str | None):
        t = self.tool(tool).tests.setdefault(test, {"outcome": "passed", "duration": 0.0, "error": None})
        t["duration"] += duration
        # a failure in setup / call / teardown wins over "passed"; skipped only when nothing ran
        if outcome == "failed" or (outcome == "skipped" and t["outcome"] == "passed" and t["duration"] <= duration):
            t["outcome"] = outcome
        if longrepr and outcome != "passed":
            t["error"] = longrepr[-6000:]

    # ---------------------------------------------------------------- writing
    def finish(self, exitstatus: int):
        seconds = round(time.time() - self.started, 1)
        totals = {"passed": 0, "failed": 0, "skipped": 0}
        rows = []
        for name, t in sorted(self.tools.items()):
            if not t.tests:
                continue
            c = {k: sum(1 for x in t.tests.values() if x["outcome"] == k) for k in totals}
            for k in totals:
                totals[k] += c[k]
            t._mk()
            (t.dir / "results.json").write_text(json.dumps({"tool": name, "tests": t.tests, "metrics": t.metrics, "tables": t.tables,
                                                            "images": t.images, "files": t.files, "notes": t.notes}, indent=1, default=str))
            (t.dir / "index.html").write_text(self._tool_html(t, c), encoding="utf-8")
            rows.append({"tool": name, "folder": t.dir.name, **c, "seconds": round(sum(x["duration"] for x in t.tests.values()), 1),
                         "metrics": [m for m in t.metrics if isinstance(m["value"], (int, float))][:8]})
        summary = {"run": self.dir.name, "suite": self.suite, "started": dt.datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
                   "seconds": seconds, "exitstatus": exitstatus, "totals": totals, "env": self.env, "tools": rows, **self.extra}
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
        (self.dir / "index.html").write_text(self._run_html(summary), encoding="utf-8")
        self._history(summary)
        return self.dir

    def _history(self, s: dict):
        hist = self.base / "history.csv"
        new = not hist.exists()
        with open(hist, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["run", "suite", "started", "app_version", "commit", "changed_files", "passed", "failed", "skipped", "seconds", "key_metrics"])
            key = "; ".join(f"{r['tool']}: {m['name']}={m['value']:.4g}" for r in s["tools"] for m in r["metrics"][:2])
            w.writerow([s["run"], s["suite"], s["started"], s["env"]["app_version"], s["env"]["commit"], s["env"]["changed_files"],
                        s["totals"]["passed"], s["totals"]["failed"], s["totals"]["skipped"], s["seconds"], key])
        runs = []
        for p in sorted(self.base.glob("*/summary.json"), reverse=True):
            try:
                runs.append(json.loads(p.read_text()))
            except ValueError:
                pass
        (self.base / "index.html").write_text(_history_html(runs), encoding="utf-8")

    def _run_html(self, s: dict) -> str:
        e = s["env"]
        t = s["totals"]
        rows = "".join(
            f"<tr><td><a href='{html.escape(r['folder'])}/index.html'>{html.escape(r['tool'])}</a></td>{_counts(r)}<td>{r['seconds']} s</td>"
            f"<td>{'<br>'.join(_metric_text(m) for m in r['metrics'][:4])}</td></tr>" for r in s["tools"])
        envrows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in e.items() if v is not None)
        extra = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in s.items()
                        if k not in ("tools", "env", "totals", "run", "suite", "started", "seconds", "exitstatus"))
        status = "passed" if t["failed"] == 0 and s["exitstatus"] in (0, 5) else "failed"
        return _page(f"Test run {s['run']}", f"""
<p class="sub"><a href="../index.html">← all runs</a></p>
<h1>Test run · {html.escape(s['suite'])} <span class="pill {status}">{status}</span></h1>
<p class="sub">{html.escape(s['started'])} · {s['seconds']} s · LULC Fetch {html.escape(e['app_version'])} · commit {html.escape(e['commit'] or '?')}
{f"(+{e['changed_files']} uncommitted changes)" if e['changed_files'] else ''}</p>
<div class="tiles"><div class="tile ok"><b>{t['passed']}</b><span>passed</span></div><div class="tile bad"><b>{t['failed']}</b><span>failed</span></div>
<div class="tile"><b>{t['skipped']}</b><span>skipped</span></div></div>
<h2>Tools</h2><table><tr><th>Tool</th><th>Passed</th><th>Failed</th><th>Skipped</th><th>Time</th><th>Key numbers</th></tr>{rows}</table>
<h2>Environment</h2><table>{envrows}{extra}</table>""")

    def _tool_html(self, t: ToolReport, c: dict) -> str:
        tests = "".join(
            f"<tr class='{x['outcome']}'><td>{html.escape(n)}</td><td><span class='pill {x['outcome']}'>{x['outcome']}</span></td><td>{x['duration']:.1f} s</td>"
            f"<td>{_err(x)}</td></tr>"
            for n, x in t.tests.items())
        metrics = "".join(f"<tr><td>{html.escape(m['test'])}</td><td>{html.escape(m['name'])}</td><td class='num'>{html.escape(_fmt(m['value']))}{html.escape(m['unit'])}</td></tr>"
                          for m in t.metrics)
        tables = "".join(f"<h3>{html.escape(tb['title'])}</h3><table><tr>{''.join(f'<th>{html.escape(str(h))}</th>' for h in tb['header'])}</tr>"
                         + "".join("<tr>" + "".join(f"<td>{html.escape(_fmt(v))}</td>" for v in r) + "</tr>" for r in tb["rows"]) + "</table>"
                         for tb in t.tables)
        imgs = "".join(f"<figure><a href='{html.escape(i['file'])}'><img src='{html.escape(i['file'])}' alt=''></a><figcaption>{html.escape(i['caption'])}</figcaption></figure>"
                       for i in t.images)
        files = "".join(f"<li><a href='{html.escape(f['file'])}'>{html.escape(f['caption'])}</a> <span class='sub'>({html.escape(f['test'])})</span></li>" for f in t.files)
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in t.notes)
        return _page(t.tool, f"""
<p class="sub"><a href="../index.html">← this run</a></p>
<h1>{html.escape(t.tool)}</h1>
<div class="tiles"><div class="tile ok"><b>{c['passed']}</b><span>passed</span></div><div class="tile bad"><b>{c['failed']}</b><span>failed</span></div>
<div class="tile"><b>{c['skipped']}</b><span>skipped</span></div></div>
<h2>Tests</h2><table><tr><th>Test</th><th>Result</th><th>Time</th><th>Message</th></tr>{tests}</table>
{f'<h2>Metrics</h2><table><tr><th>Test</th><th>Metric</th><th>Value</th></tr>{metrics}</table>' if metrics else ''}
{f'<h2>Tables</h2>{tables}' if tables else ''}
{f'<h2>Notes</h2><ul>{notes}</ul>' if notes else ''}
{f'<h2>Images</h2><div class="gallery">{imgs}</div>' if imgs else ''}
{f'<h2>Files</h2><ul>{files}</ul>' if files else ''}""")


def _err(x: dict) -> str:
    if not x["error"]:
        return ""
    if x["outcome"] == "failed":
        return "<details><summary>error</summary><pre>" + html.escape(x["error"]) + "</pre></details>"
    return html.escape(x["error"].strip().splitlines()[-1][:200])   # why it was skipped


def _counts(r):
    return f"<td class='num ok'>{r['passed']}</td><td class='num {'bad' if r['failed'] else ''}'>{r['failed']}</td><td class='num'>{r['skipped']}</td>"


def _metric_text(m: dict) -> str:
    return html.escape(str(m["name"]) + ": " + _fmt(m["value"]) + str(m["unit"]))


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _history_html(runs: list[dict]) -> str:
    rows = []
    for s in runs:
        e, t = s["env"], s["totals"]
        status = "passed" if t["failed"] == 0 else "failed"
        key = "<br>".join(html.escape(r["tool"]) + ": " + _metric_text(m) for r in s["tools"] for m in r["metrics"][:1])
        rows.append(f"<tr><td><a href='{html.escape(s['run'])}/index.html'>{html.escape(s['started'].replace('T', ' '))}</a></td><td>{html.escape(s['suite'])}</td>"
                    f"<td>{html.escape(e['app_version'])}</td><td>{html.escape(e['commit'] or '')}{'*' if e['changed_files'] else ''}</td>"
                    f"<td><span class='pill {status}'>{status}</span></td><td class='num'>{t['passed']}</td><td class='num'>{t['failed']}</td>"
                    f"<td class='num'>{t['skipped']}</td><td class='num'>{s['seconds']:.0f} s</td><td class='small'>{key}</td></tr>")
    return _page("LULC Fetch test runs", f"""<h1>LULC Fetch test runs</h1>
<p class="sub">Every run of the test suite, newest first. * = run with uncommitted changes. The same list is in history.csv.</p>
<table><tr><th>Run</th><th>Suite</th><th>Version</th><th>Commit</th><th>Result</th><th>Passed</th><th>Failed</th><th>Skipped</th><th>Time</th><th>Key numbers</th></tr>
{''.join(rows)}</table>""")


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#f8fafc;--panel:#fff;--text:#0f172a;--muted:#64748b;--border:#e2e8f0;--ok:#15803d;--bad:#b91c1c;--okbg:#dcfce7;--badbg:#fee2e2;--skipbg:#f1f5f9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f172a;--panel:#111827;--text:#e2e8f0;--muted:#94a3b8;--border:#334155;--ok:#4ade80;--bad:#f87171;--okbg:#14532d;--badbg:#7f1d1d;--skipbg:#1e293b}}}}
body{{font:14px/1.5 system-ui,-apple-system,sans-serif;margin:0;background:var(--bg);color:var(--text)}} main{{max-width:1100px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}} h3{{font-size:14px;margin:18px 0 6px}} a{{color:#2563eb}} .sub,.small{{color:var(--muted);font-size:12.5px}}
table{{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden;display:block;overflow-x:auto}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}} th{{color:var(--muted);font-weight:600;font-size:12.5px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}} td.ok{{color:var(--ok)}} td.bad{{color:var(--bad);font-weight:700}}
.pill{{display:inline-block;padding:0 8px;border-radius:999px;font-size:12px;font-weight:600}} .pill.passed{{background:var(--okbg);color:var(--ok)}}
.pill.failed{{background:var(--badbg);color:var(--bad)}} .pill.skipped{{background:var(--skipbg);color:var(--muted)}}
.tiles{{display:flex;gap:10px;margin:14px 0}} .tile{{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:8px 16px;min-width:90px}}
.tile b{{display:block;font-size:22px}} .tile span{{color:var(--muted);font-size:12px}} .tile.ok b{{color:var(--ok)}} .tile.bad b{{color:var(--bad)}}
pre{{white-space:pre-wrap;font-size:12px;max-height:420px;overflow:auto}} .gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}}
figure{{margin:0;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:8px}} figure img{{width:100%;height:auto;display:block;image-rendering:auto}}
figcaption{{color:var(--muted);font-size:12px;margin-top:4px}}
</style></head><body><main>{body}</main></body></html>"""
