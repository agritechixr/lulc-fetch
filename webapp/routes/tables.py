"""Tables: Raster → table, and the tables listing, preview, rows and editing. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import TABLE_DIR, TABLE_EXTS, jobs
from ..core import table_path as _table_path

router = APIRouter()


# ------------------------------------------------------------------ Classical ML: tables (raster → table and future sub-tools)





@router.get("/api/tables")
def list_tables():
    import json as _json

    out = []
    if TABLE_DIR.is_dir():
        for p in sorted(TABLE_DIR.iterdir(), key=lambda p: -p.stat().st_mtime):
            if p.suffix.lower() in TABLE_EXTS:
                meta = {}
                side = p.with_suffix(p.suffix + ".json")
                if side.exists():
                    try:
                        meta = _json.loads(side.read_text())
                    except ValueError:
                        pass
                out.append({"path": ws.rel(p), "name": p.name, "size_mb": p.stat().st_size / 1e6,
                            "modified": p.stat().st_mtime, "rows": meta.get("rows"), "columns": meta.get("columns"),
                            "target": meta.get("target"), "class_counts": meta.get("class_counts"),
                            "source": Path(meta.get("source", "")).name})
    return out


@router.get("/api/tables/preview")
def table_preview(path: str, n: int = 20):
    p = _table_path(path)
    if p.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq
        f = pq.ParquetFile(p)
        t = next(f.iter_batches(batch_size=n)).to_pylist() if f.metadata.num_rows else []
        return {"columns": f.schema_arrow.names, "rows": [list(r.values()) for r in t], "total": f.metadata.num_rows}
    import csv
    with open(p, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        cols = next(rd, [])
        rows = [r for _, r in zip(range(n), rd)]
    return {"columns": cols, "rows": rows}


@router.get("/api/tables/file")
def table_file(path: str):
    p = _table_path(path)
    return FileResponse(p, filename=p.name)


@router.delete("/api/tables")
def delete_table(path: str):
    p = _table_path(path)
    p.unlink(missing_ok=True)
    p.with_suffix(p.suffix + ".json").unlink(missing_ok=True)
    return {"ok": True}


@router.post("/api/tables/upload")
async def upload_table(file: UploadFile = File(...)):
    """Add a CSV / TSV / Parquet / Excel table: it is stored in tables/ so every tool can use it."""
    import shutil
    import tempfile

    from lulc_fetch.tableview import TABLE_IMPORT_EXTS, import_table

    name = Path(file.filename or "table.csv").name
    if Path(name).suffix.lower() not in TABLE_IMPORT_EXTS:
        raise HTTPException(400, "Tables can be CSV, TSV, TXT, Parquet or Excel (.xlsx)")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / name
        with open(src, "wb") as f:
            shutil.copyfileobj(file.file, f, length=8 << 20)
        try:
            out = import_table(src, TABLE_DIR)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Couldn't read {name}: {e}")
    return {"path": ws.rel(out), "name": out.name}


@router.get("/api/tables/rows")
def table_rows(path: str, offset: int = 0, limit: int = 100, q: str = "", sort: str | None = None, desc: bool = False):
    from lulc_fetch import tableview

    try:
        p = _table_path(path)
        return {**tableview.page(p, offset=max(0, offset), limit=max(1, min(limit, 1000)), query=q[:200], sort=sort, desc=desc),
                "undo": tableview.undo_count(p)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class TableEditRequest(BaseModel):
    path: str
    ops: list[dict] = Field(min_length=1, max_length=50)


@router.post("/api/tables/edit")
def table_edit(req: TableEditRequest):
    """Edit a table: add / calculate / rename / convert / delete fields, edit cells, add / delete rows (one undo step)."""
    from lulc_fetch import tableview

    try:
        return tableview.edit(_table_path(req.path), req.ops)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e).strip("'"))


class TablePathRequest(BaseModel):
    path: str


@router.post("/api/tables/undo")
def table_undo(req: TablePathRequest):
    from lulc_fetch import tableview

    try:
        return tableview.undo(_table_path(req.path))
    except ValueError as e:
        raise HTTPException(400, str(e))


class EditStartRequest(BaseModel):
    path: str


@router.post("/api/tables/edit/start")
def table_edit_start(req: EditStartRequest):
    """Start (or resume) an edit session: changes go to a working copy until they are saved."""
    from lulc_fetch import tableview

    p = _table_path(req.path)
    if tableview.is_work(p):
        raise HTTPException(400, "This is already an edit session")
    r = tableview.edit_start(p)
    return {**r, "work": ws.rel(r["work"]), "original": ws.rel(r["original"])}


class EditLogRequest(BaseModel):
    path: str
    add: list[str] = Field(default_factory=list, max_length=50)
    pop: int = Field(0, ge=0, le=50)


@router.post("/api/tables/edit/log")
def table_edit_log(req: EditLogRequest):
    from lulc_fetch import tableview

    p = _table_path(req.path)
    if not tableview.is_work(p):
        raise HTTPException(400, "Not an edit session")
    return {"log": tableview.edit_log(p, [a[:200] for a in req.add], req.pop)}


class EditSaveRequest(BaseModel):
    path: str
    mode: str = "overwrite"   # overwrite | new
    name: str | None = Field(None, max_length=80)


@router.post("/api/tables/edit/save")
def table_edit_save(req: EditSaveRequest):
    from lulc_fetch import tableview

    p = _table_path(req.path)
    if not tableview.is_work(p) or req.mode not in ("overwrite", "new"):
        raise HTTPException(400, "Not an edit session")
    try:
        dest = tableview.edit_save(p, req.mode, req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": ws.rel(dest), "name": dest.name}


@router.post("/api/tables/edit/discard")
def table_edit_discard(req: EditStartRequest):
    from lulc_fetch import tableview

    p = _table_path(req.path)
    try:
        tableview.edit_discard(p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/tables/restore")
def table_restore(req: EditStartRequest):
    """Bring back the version from before the last save over this table."""
    from lulc_fetch import tableview

    try:
        return tableview.restore_previous(_table_path(req.path))
    except ValueError as e:
        raise HTTPException(400, "No previous version is kept for this table" if "undo" in str(e).lower() else str(e))


class PythonRequest(BaseModel):
    path: str | None = None                       # a table (edit session working copy)
    code: str = Field(max_length=100_000)
    apply: bool = False
    columns: dict[str, list] | None = None        # or vector attributes (+ geometries)
    geometries: list[dict | None] | None = None
    n: int = Field(0, ge=0, le=2_000_000)


def _python_result(r: dict, preview: int = 8) -> dict:
    import math

    import pandas as pd
    out = {"ok": r["ok"], "output": r["output"], "error": r["error"]}
    if r["ok"]:
        df = pd.read_parquet(r["out"])
        out["meta"] = r["meta"]
        cols = [c for c in df.columns if c != "__row__"]
        clean = lambda v: None if v is None or (isinstance(v, float) and not math.isfinite(v)) else (v.item() if hasattr(v, "item") else v)
        out["preview"] = {"columns": cols, "rows": [[clean(v) for v in row] for row in df[cols].head(preview).itertuples(index=False)]}
    return out


@router.post("/api/python/run")
def python_run(req: PythonRequest):
    """Run the user's Python on a table (edit session) or on vector attributes, in a separate process (a job)."""
    import shutil
    import tempfile

    import pandas as pd

    from lulc_fetch import pyexec, tableview

    work = _table_path(req.path) if req.path else None
    if work is not None and req.apply and not tableview.is_work(work):
        raise HTTPException(400, "Start editing first: changes go to a working copy until you save them")
    if work is None and req.columns is None:
        raise HTTPException(400, "Nothing to run on")

    def run(job):
        tmp = Path(tempfile.mkdtemp(prefix="lulc_in_"))
        try:
            if work is not None:
                src = tableview.to_parquet_for_python(work, tmp / "in.parquet")
            else:
                data = {"__row__": [float(i) for i in range(req.n)]}
                for c, vals in (req.columns or {}).items():
                    if all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for v in vals):
                        data[c] = [float("nan") if v is None else float(v) for v in vals]
                    elif all(v is None or isinstance(v, bool) for v in vals):
                        data[c] = vals
                    else:
                        data[c] = [None if v is None else v if isinstance(v, str) else str(v) for v in vals]
                if req.geometries is not None:
                    import json as _json
                    data["__geometry__"] = [_json.dumps(g) if g else None for g in req.geometries]
                src = tmp / "in.parquet"
                pd.DataFrame(data).to_parquet(src, index=False)
            r = pyexec.run(src, req.code)
            res = _python_result(r)
            if r["ok"] and work is not None and req.apply:
                res["applied"] = tableview.replace_from_python(work, r["out"])
            elif r["ok"] and work is None:
                df = pd.read_parquet(r["out"])
                cols = [c for c in df.columns if c != "__row__"]
                import math
                clean = lambda v: None if v is None or (isinstance(v, float) and not math.isfinite(v)) else (v.item() if hasattr(v, "item") else v)
                res["result"] = {"columns": cols, "index": [None if not math.isfinite(v) else int(v) for v in df["__row__"].tolist()],
                                 "rows": [[clean(v) for v in row] for row in df[cols].itertuples(index=False)]}
            shutil.rmtree(r.get("dir", ""), ignore_errors=True)
            return res
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return jobs.submit("python", "Python script" + (" (apply)" if req.apply else " (test)"), {"source": "Data viewer"}, run).to_dict()


@router.get("/api/tables/calc-preview")
def table_calc_preview(path: str, expression: str):
    from lulc_fetch import tableview

    try:
        return tableview.calc_preview(_table_path(path), expression)
    except ValueError as e:
        raise HTTPException(400, str(e))


class TableDeriveRequest(BaseModel):
    path: str
    q: str = ""
    name: str = Field("selection", max_length=80)


@router.post("/api/tables/derive")
def table_derive(req: TableDeriveRequest):
    """Save the rows matching the current search / filter as a new table."""
    from lulc_fetch import tableview

    try:
        out = tableview.derive(_table_path(req.path), req.q[:200], TABLE_DIR.path, req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": ws.rel(out), "name": out.name}


class FieldCalcRequest(BaseModel):
    expression: str = Field(max_length=2000)
    columns: dict[str, list] = Field(default_factory=dict)   # attribute values of a vector layer
    geometries: list[dict | None] | None = None
    n: int = Field(ge=0, le=2_000_000)


@router.post("/api/fields/calc")
def field_calc(req: FieldCalcRequest):
    """Field calculator for vector layers (attributes live in the browser): returns the new values."""
    import math

    from lulc_fetch import fieldcalc

    try:
        used, geoms = fieldcalc.referenced(req.expression, list(req.columns))
        data = {}
        for c in used:
            vals = req.columns.get(c)
            if vals is None:
                continue
            if all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for v in vals):
                data[c] = np.array([np.nan if v is None else float(v) for v in vals])
            else:
                data[c] = np.array([None if v is None else v if isinstance(v, str) else str(v) for v in vals], dtype=object)
        g = None
        if geoms:
            if req.geometries is None or len(req.geometries) != req.n:
                raise fieldcalc.CalcError("Geometry values ($area …) only work on vector layers")
            g = fieldcalc.geometry_measures(req.geometries, geoms)
        vals, typ = fieldcalc.evaluate(req.expression, data, req.n, g)
    except fieldcalc.CalcError as e:
        raise HTTPException(400, str(e))
    out = []
    for v in vals.tolist():
        if isinstance(v, float) and not math.isfinite(v):
            out.append(None)
        elif typ == "integer" and isinstance(v, float):
            out.append(int(v))
        else:
            out.append(v)
    return {"values": out, "type": typ}


@router.get("/api/fields/functions")
def field_functions():
    from lulc_fetch import fieldcalc

    return {"functions": fieldcalc.FUNC_HELP, "geometry": list(fieldcalc.GEOM_VARS)}


@router.get("/api/tables/stats")
def table_stats(path: str):
    from lulc_fetch import tableview

    return tableview.stats(_table_path(path))


@router.get("/api/tables/points")
def table_points(path: str, q: str = "", lon: str | None = None, lat: str | None = None):
    from lulc_fetch import tableview

    try:
        return tableview.points(_table_path(path), query=q[:200], lon=lon, lat=lat)
    except ValueError as e:
        raise HTTPException(400, str(e))
