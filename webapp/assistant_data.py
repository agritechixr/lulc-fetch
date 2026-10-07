"""What the Assistant knows besides the request (webapp/assistant.py).

From GISclaw (arXiv 2603.26845):
- describe(): look at the data before planning ("schema analysis"): a raster's bands, CRS, resolution and value ranges,
  a table's columns, types and a few rows, a vector's fields and sample values. The same description of a step's
  results is the "observation" the Assistant sees after each step, with warnings when a result looks wrong
  (empty, all nodata, a constant band).
- Error memory: each failed step (tool, settings, error) is kept; the latest per tool are shown to the model as known
  pitfalls, with the fix when one worked.

From OpenClaw:
- Long-term memory: notes.md, the user's notes plus what the Assistant was asked to remember.
- Transcripts: each conversation, one JSON line per event, to reopen later.
- Skills: the user's saved workflows; the ones closest to a request are shown to the model as examples.

Everything is kept in the settings folder (~/.lulc-fetch/assistant), on this computer only.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np

from . import workspace as ws

RASTER = (".tif", ".tiff", ".vrt")
TABLE = (".csv", ".tsv", ".parquet", ".xlsx", ".xls")
VECTOR = (".geojson", ".json")


def folder() -> Path:
    d = ws.CONFIG_DIR / "assistant"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve(rel: str) -> Path | None:
    """A workspace file (never outside the workspace)."""
    if not isinstance(rel, str) or not rel:
        return None
    root = ws.root().resolve()
    p = (root / rel).resolve()
    return p if p.is_relative_to(root) and p.is_file() else None


def _num(v):
    return None if v is None or not np.isfinite(v) else float(f"{v:.4g}")


# ------------------------------------------------------------------ looking at data
def describe(rel: str) -> dict:
    """A short description of a file in the workspace, with warnings when it looks wrong."""
    p = _resolve(rel)
    if p is None:
        return {"path": rel, "error": "not found in the workspace"}
    ext, out = p.suffix.lower(), {"path": rel}
    try:
        if ext in RASTER:
            import rasterio
            from rasterio.enums import Resampling
            with rasterio.open(p) as src:
                f = min(1.0, 256 / max(src.width, src.height))
                shape = (max(1, int(src.height * f)), max(1, int(src.width * f)))
                bands, warns = [], []
                for b in range(1, min(src.count, 12) + 1):
                    a = src.read(b, out_shape=shape, masked=True, resampling=Resampling.nearest).astype("float64")
                    valid = a.compressed()
                    valid = valid[np.isfinite(valid)]
                    d = {"band": b, "name": src.descriptions[b - 1] or f"Band {b}", "empty_pct": round(100 * (1 - valid.size / max(1, a.size)), 1)}
                    if valid.size:
                        d.update(min=_num(valid.min()), max=_num(valid.max()), mean=_num(valid.mean()))
                        if valid.min() == valid.max():
                            warns.append(f"band {b} has one value only ({_num(valid.min())})")
                    else:
                        warns.append(f"band {b} is empty (all nodata)")
                    bands.append(d)
                out.update(kind="raster", size=[src.width, src.height], bands_total=src.count, crs=str(src.crs) if src.crs else None,
                           res=[_num(abs(src.res[0])), _num(abs(src.res[1]))], dtype=src.dtypes[0], nodata=_num(src.nodata) if src.nodata is not None else None,
                           bands=bands)
                if src.crs is None:
                    warns.append("no coordinate system")
                if warns:
                    out["warnings"] = warns
        elif ext in TABLE:
            import pandas as pd
            if ext == ".parquet":
                import pyarrow.parquet as pq
                rows = pq.ParquetFile(p).metadata.num_rows
                df = pd.read_parquet(p).head(500)
            elif ext in (".xlsx", ".xls"):
                df = pd.read_excel(p, nrows=500)
                rows = None
            else:
                df = pd.read_csv(p, nrows=500, sep="\t" if ext == ".tsv" else ",")
                with open(p, "rb") as fh:
                    rows = max(0, sum(1 for _ in fh) - 1)
            out.update(kind="table", rows=rows, columns={c: str(t) for c, t in list(df.dtypes.items())[:40]},
                       sample=json.loads(df.head(3).to_json(orient="records", default_handler=str))[:3])
            if rows == 0 or df.empty:
                out["warnings"] = ["the table has no rows"]
        elif ext in VECTOR and p.stat().st_size < 30_000_000:
            fc = json.loads(p.read_text(encoding="utf-8"))
            feats = fc.get("features") or []
            props = [f.get("properties") or {} for f in feats[:200]]
            fields = sorted({k for pr in props for k in pr})[:30]
            out.update(kind="vector", features=len(feats), geometry=sorted({(f.get("geometry") or {}).get("type") for f in feats[:500]} - {None}),
                       fields={k: [pr.get(k) for pr in props[:3]] for k in fields})
            if not feats:
                out["warnings"] = ["no features"]
        else:
            out.update(kind="file", size_mb=round(p.stat().st_size / 1e6, 2))
    except Exception as e:  # noqa: BLE001 — a file that can't be read is described as such
        out["error"] = f"couldn't read it: {str(e)[:200]}"
    return out


def describe_many(paths: list, limit: int = 8) -> list[dict]:
    seen, out = set(), []
    for rel in paths:
        if not isinstance(rel, str) or rel in seen or re.search(r"_colour\.tif$|preview|\.png$|\.jpg$", rel, re.I):
            continue
        seen.add(rel)
        out.append(describe(rel))
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ memory: notes (long term)
def notes() -> str:
    try:
        return (folder() / "notes.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def save_notes(text: str) -> str:
    text = text.strip()[:20000]
    (folder() / "notes.md").write_text(text + ("\n" if text else ""), encoding="utf-8")
    return text


def remember(items: list[str]) -> list[str]:
    """Add what the Assistant was asked to remember to the notes (not twice)."""
    cur, added = notes(), []
    for it in items[:5]:
        it = " ".join(str(it).split())[:300]
        if it and it.lower() not in cur.lower():
            cur = (cur.rstrip() + "\n" if cur.strip() else "") + f"- {it}"
            added.append(it)
    if added:
        save_notes(cur)
    return added


# ------------------------------------------------------------------ memory: errors (GISclaw's Error Memory, kept)
def _errors_file() -> Path:
    return folder() / "errors.jsonl"


def record_error(endpoint: str, body: dict, error: str, fix: str = "") -> None:
    keys = sorted(k for k in (body or {}) if not isinstance((body or {}).get(k), dict) or "$in" not in body[k])
    e = {"t": time.time(), "endpoint": endpoint, "error": str(error)[:300], "settings": keys[:15], "fix": str(fix)[:300]}
    try:
        with open(_errors_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        lines = _errors_file().read_text(encoding="utf-8").splitlines()
        if len(lines) > 500:   # keep the newest 500
            _errors_file().write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def pitfalls(endpoints: list[str] | None = None, per_tool: int = 3) -> list[dict]:
    """The latest different errors per tool (with their fix when one worked)."""
    try:
        rows = [json.loads(x) for x in _errors_file().read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    out, seen = [], {}
    for r in reversed(rows):
        if endpoints and r.get("endpoint") not in endpoints:
            continue
        key = (r.get("endpoint"), re.sub(r"\d+", "#", r.get("error", ""))[:120])
        n = seen.get(r.get("endpoint"), 0)
        if key in seen or n >= per_tool:
            continue
        seen[key] = 1
        seen[r.get("endpoint")] = n + 1
        out.append(r)
    return out


def clear_pitfalls() -> None:
    _errors_file().unlink(missing_ok=True)


# ------------------------------------------------------------------ memory: transcripts (OpenClaw)
def _tdir() -> Path:
    d = folder() / "transcripts"
    d.mkdir(exist_ok=True)
    return d


def log(conv_id: str, event: dict) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", conv_id or ""):
        return
    try:
        with open(_tdir() / f"{conv_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), **event}, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def conversations(limit: int = 30) -> list[dict]:
    out = []
    for f in sorted(_tdir().glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            first = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
        except (OSError, ValueError, IndexError):
            continue
        out.append({"id": f.stem, "title": str(first.get("text") or first.get("content") or "")[:120], "updated": f.stat().st_mtime})
    return out


def conversation(conv_id: str) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", conv_id or ""):
        raise ValueError("Not a conversation")
    f = _tdir() / f"{conv_id}.jsonl"
    if not f.is_file():
        raise FileNotFoundError("No such conversation")
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]


def delete_conversation(conv_id: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]{4,40}", conv_id or ""):
        (_tdir() / f"{conv_id}.jsonl").unlink(missing_ok=True)


# ------------------------------------------------------------------ skills: the user's saved workflows
def skills(request: str, limit: int = 2) -> list[dict]:
    """The saved workflows whose names and steps share the most words with the request."""
    from . import workflows
    words = {w for w in re.findall(r"[a-z0-9]{3,}", request.lower())}
    scored = []
    for w in workflows.listing():
        text = f"{w.get('name', '')} {w.get('description', '')} {' '.join(t or '' for t in w.get('titles') or [])}".lower()
        score = len(words & set(re.findall(r"[a-z0-9]{3,}", text)))
        if score:
            scored.append((score, w["id"]))
    out = []
    for _, wid in sorted(scored, reverse=True)[:limit]:
        try:
            wf = workflows.get(wid)
        except (FileNotFoundError, ValueError):
            continue
        out.append({"name": wf["name"], "description": wf.get("description", ""),
                    "inputs": [{k: i.get(k) for k in ("id", "label", "type")} for i in wf.get("inputs", [])],
                    "steps": [{"title": s["title"], "endpoint": s["endpoint"], "body": s["body"]} for s in wf["steps"]][:8]})
    return out
