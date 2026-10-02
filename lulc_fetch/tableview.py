"""Tabular data for the web app: import tables (CSV, TSV, Parquet, Excel) and browse them page by page.

The data viewer under the map asks for one page at a time (sorted / filtered on the server), so tables with
millions of rows stay responsive. Loaded tables are cached in memory by path + modification time.
"""

from __future__ import annotations

import math
import re
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

TABLE_IMPORT_EXTS = {".csv", ".tsv", ".txt", ".parquet", ".xlsx", ".xlsm"}
_cache: OrderedDict = OrderedDict()   # (path, mtime) -> pa.Table
_views: OrderedDict = OrderedDict()   # (path, mtime, query, sort, desc) -> row indices
_lock = threading.Lock()
LON_NAMES, LAT_NAMES = ("lon", "longitude", "long", "lng", "x_wgs84"), ("lat", "latitude", "y_wgs84")


def load(path: str | Path) -> pa.Table:
    path = Path(path)
    key = (str(path), path.stat().st_mtime)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq
        t = pq.read_table(path)
    else:
        import pyarrow.csv as pcsv
        t = pcsv.read_csv(path)
    with _lock:
        _cache[key] = t
        while len(_cache) > 3:
            _cache.popitem(last=False)
    return t


def _kind(typ: pa.DataType) -> str:
    if pa.types.is_integer(typ):
        return "integer"
    if pa.types.is_floating(typ) or pa.types.is_decimal(typ):
        return "number"
    if pa.types.is_boolean(typ):
        return "boolean"
    if pa.types.is_temporal(typ):
        return "date"
    return "text"


def _safe(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _view(path: Path, t: pa.Table, query: str, sort: str | None, desc: bool) -> np.ndarray:
    """Row indices after filtering (text search over all columns) and sorting; cached for paging."""
    key = (str(path), path.stat().st_mtime, query, sort, desc)
    with _lock:
        if key in _views:
            _views.move_to_end(key)
            return _views[key]
    idx = np.arange(t.num_rows)
    q = query.strip().lower()
    if q:
        mask = None
        # "column = value" / "column > 3" for exact filters, otherwise a substring search over all columns
        m = re.fullmatch(r"\s*([^=<>!]+?)\s*(=|==|!=|>=|<=|>|<)\s*(.+?)\s*", query)
        if m and m.group(1) in t.column_names:
            col, op, val = t[m.group(1)], m.group(2), m.group(3).strip("'\"")
            if _kind(col.type) in ("integer", "number"):
                try:
                    val = float(val)
                except ValueError:
                    raise ValueError(f"'{m.group(3)}' is not a number")
            else:
                col = pc.cast(col, pa.string())
            fn = {"=": pc.equal, "==": pc.equal, "!=": pc.not_equal, ">": pc.greater, "<": pc.less,
                  ">=": pc.greater_equal, "<=": pc.less_equal}[op]
            mask = pc.fill_null(fn(col, val), False)
        else:
            for name in t.column_names:
                col = t[name]
                s = col if _kind(col.type) == "text" else pc.cast(col, pa.string())
                hit = pc.fill_null(pc.match_substring(pc.utf8_lower(s), q), False)
                mask = hit if mask is None else pc.or_(mask, hit)
        idx = np.flatnonzero(np.asarray(mask.to_numpy(zero_copy_only=False), dtype=bool)) if mask is not None else idx
    if sort and sort in t.column_names and len(idx):
        col = t[sort].take(pa.array(idx))
        order = pc.array_sort_indices(col, order="descending" if desc else "ascending", null_placement="at_end")
        idx = idx[order.to_numpy()]
    with _lock:
        _views[key] = idx
        while len(_views) > 12:
            _views.popitem(last=False)
    return idx


def page(path: str | Path, *, offset: int = 0, limit: int = 100, query: str = "", sort: str | None = None,
         desc: bool = False) -> dict:
    path = Path(path)
    t = load(path)
    idx = _view(path, t, query or "", sort or None, desc)
    sel = idx[offset:offset + limit]
    sub = t.take(pa.array(sel, type=pa.int64())) if len(sel) else t.slice(0, 0)
    cols = sub.to_pydict()
    rows = [[_safe(cols[c][i]) for c in t.column_names] for i in range(len(sel))]
    return {"columns": t.column_names, "types": [_kind(f.type) for f in t.schema], "rows": rows,
            "row_ids": [int(i) for i in sel], "offset": offset, "total": t.num_rows, "filtered": int(len(idx)),
            "lonlat": lonlat_columns(t.column_names)}


def stats(path: str | Path) -> dict:
    """Per-column summary: type, missing, distinct, min / max / mean / std or the most frequent values."""
    t = load(Path(path))
    out = []
    for name, f in zip(t.column_names, t.schema):
        col, kind = t[name], _kind(f.type)
        st = {"name": name, "type": kind, "missing": int(col.null_count), "distinct": int(pc.count_distinct(col).as_py())}
        if kind in ("integer", "number"):
            arr = col.to_numpy(zero_copy_only=False).astype("float64")
            fin = arr[np.isfinite(arr)]
            st["missing"] = int(len(arr) - len(fin))
            if len(fin):
                q = np.percentile(fin, [25, 50, 75])
                st.update(min=float(fin.min()), max=float(fin.max()), mean=float(fin.mean()), std=float(fin.std()),
                          q1=float(q[0]), median=float(q[1]), q3=float(q[2]),
                          hist=np.histogram(fin, bins=20)[0].tolist())
        else:
            vc = pc.value_counts(col).to_pylist()
            vc.sort(key=lambda d: -d["counts"])
            st["top"] = [[_safe(d["values"]), int(d["counts"])] for d in vc[:8]]
        out.append(st)
    return {"rows": t.num_rows, "columns": out}


def lonlat_columns(names) -> list[str] | None:
    low = {n.lower(): n for n in names}
    lon = next((low[n] for n in LON_NAMES if n in low), None)
    lat = next((low[n] for n in LAT_NAMES if n in low), None)
    return [lon, lat] if lon and lat else None


def points(path: str | Path, *, max_points: int = 20000, query: str = "", lon: str | None = None, lat: str | None = None) -> dict:
    """Rows with longitude / latitude columns as a GeoJSON FeatureCollection (sampled to max_points)."""
    path = Path(path)
    t = load(path)
    ll = [lon, lat] if lon and lat else lonlat_columns(t.column_names)
    if not ll:
        raise ValueError("This table has no longitude / latitude columns (e.g. 'lon' and 'lat')")
    idx = _view(path, t, query or "", None, False)
    sampled = len(idx) > max_points
    if sampled:
        idx = np.sort(np.random.default_rng(0).choice(idx, max_points, replace=False))
    sub = t.take(pa.array(idx, type=pa.int64()))
    x = sub[ll[0]].to_numpy(zero_copy_only=False).astype("float64")
    y = sub[ll[1]].to_numpy(zero_copy_only=False).astype("float64")
    keep = [c for c in t.column_names if c not in ll][:12]
    props = sub.select(keep).to_pydict() if keep else {}
    feats = []
    for i in range(len(idx)):
        if not (np.isfinite(x[i]) and np.isfinite(y[i]) and -180 <= x[i] <= 180 and -90 <= y[i] <= 90):
            continue
        p = {c: _safe(props[c][i]) for c in keep}
        p["_row"] = int(idx[i])
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(x[i]), float(y[i])]}, "properties": p})
    return {"type": "FeatureCollection", "features": feats, "sampled": sampled, "total": int(len(idx)), "columns": ll}


def import_table(src: Path, dest_dir: Path, sheet: str | None = None) -> Path:
    """Copy / convert an uploaded table into `dest_dir` as CSV or Parquet (the formats the tools read)."""
    import pyarrow.csv as pcsv
    ext = src.suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", src.stem).strip("_") or "table"
    dest_dir.mkdir(parents=True, exist_ok=True)

    def free(name):
        p, i = dest_dir / name, 2
        while p.exists():
            p = dest_dir / f"{Path(name).stem}_{i}{Path(name).suffix}"
            i += 1
        return p

    if ext == ".parquet":
        import pyarrow.parquet as pq
        pq.ParquetFile(src)  # validates
        out = free(f"{stem}.parquet")
        src.replace(out)
        return out
    if ext in (".csv", ".tsv", ".txt"):
        with open(src, encoding="utf-8-sig", errors="replace") as fh:
            head = fh.readline()
        delim = max([",", "\t", ";", "|"], key=head.count)
        t = pcsv.read_csv(src, parse_options=pcsv.ParseOptions(delimiter=delim))
    elif ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            raise ValueError("The sheet is empty")
        names, seen = [], set()
        for i, h in enumerate(header):
            n = str(h).strip() if h not in (None, "") else f"column_{i + 1}"
            while n in seen:
                n += "_2"
            seen.add(n)
            names.append(n)
        data = [list(r[:len(names)]) + [None] * (len(names) - len(r)) for r in rows if any(v not in (None, "") for v in r)]
        wb.close()
        cols = {}
        for j, n in enumerate(names):
            vals = [r[j] for r in data]
            try:
                cols[n] = pa.array(vals)
            except (pa.ArrowInvalid, pa.ArrowTypeError):
                cols[n] = pa.array([None if v is None else str(v) for v in vals])
        t = pa.table(cols)
    else:
        raise ValueError("Tables can be CSV, TSV, TXT, Parquet or Excel (.xlsx)")
    if t.num_columns < 1 or t.num_rows < 1:
        raise ValueError("The table has no rows")
    out = free(f"{stem}.csv")
    pcsv.write_csv(t, out)
    return out


# ------------------------------------------------------------------ editing (add / calculate / rename / delete fields, edit cells, rows)
HISTORY_KEEP = 15


def _forget(path: Path):
    with _lock:
        for cache in (_cache, _views):
            for k in [k for k in cache if k[0] == str(path)]:
                cache.pop(k, None)


def _col_np(t: pa.Table, name: str) -> np.ndarray:
    col = t[name]
    kind = _kind(col.type)
    if kind in ("integer", "number"):
        return col.to_numpy(zero_copy_only=False).astype("float64")
    if kind == "boolean":
        return np.array(col.to_pylist(), dtype=object)
    return np.array([None if v is None else str(v) if not isinstance(v, str) else v for v in col.to_pylist()], dtype=object)


def _to_arrow(arr: np.ndarray, typ: str) -> pa.Array:
    if typ in ("number", "integer"):
        a = np.asarray(arr, dtype="float64")
        mask = ~np.isfinite(a)
        if typ == "integer":
            return pa.array(np.where(mask, 0, a).astype("int64"), mask=mask, type=pa.int64())
        return pa.array(a, mask=mask, type=pa.float64())
    if typ == "boolean":
        return pa.array([None if v is None else bool(v) for v in arr], type=pa.bool_())
    return pa.array([None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v) for v in arr], type=pa.string())


def _data(t: pa.Table, names) -> dict:
    return {n: _col_np(t, n) for n in names if n in t.column_names}


def _coerce(value, kind: str):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if kind in ("integer", "number"):
        try:
            v = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
        except ValueError:
            raise ValueError(f"'{value}' is not a number")
        return round(v) if kind == "integer" else v
    if kind == "boolean":
        return str(value).strip().lower() in ("1", "true", "yes", "y")
    return str(value)


def _history_dir(path: Path) -> Path:
    return path.parent / ".history" / path.name


def _write(t: pa.Table, path: Path):
    import shutil
    hist = _history_dir(path)
    hist.mkdir(parents=True, exist_ok=True)
    n = max([int(p.stem) for p in hist.glob("*") if p.stem.isdigit()] + [0]) + 1
    shutil.copy2(path, hist / f"{n:05d}{path.suffix}")
    for old in sorted(hist.glob("*"))[:-HISTORY_KEEP]:
        old.unlink(missing_ok=True)
    tmp = path.with_name(path.name + ".writing")
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq
        pq.write_table(t, tmp)
    else:
        import pyarrow.csv as pcsv
        pcsv.write_csv(t, tmp)
    tmp.replace(path)
    _forget(path)


def _sync_sidecar(path: Path, t: pa.Table, renamed: dict | None = None, deleted: set | None = None):
    import json
    side = Path(str(path) + ".json")
    if not side.exists():
        return
    try:
        meta = json.loads(side.read_text())
    except ValueError:
        return
    renamed, deleted = renamed or {}, deleted or set()
    for key in ("band_columns", "label_columns", "columns"):
        if isinstance(meta.get(key), list):
            meta[key] = [renamed.get(c, c) for c in meta[key] if c not in deleted]
    for key in ("target", "group_column", "cluster_column"):
        if meta.get(key) in deleted:
            meta[key] = None
        elif meta.get(key) in renamed:
            meta[key] = renamed[meta[key]]
    meta.update(rows=t.num_rows, columns=t.column_names)
    side.write_text(json.dumps(meta, indent=1, default=str))


def edit(path: str | Path, ops: list[dict]) -> dict:
    """Apply edit operations to a table file (one undo step for the whole batch)."""
    from . import fieldcalc
    path = Path(path)
    t = load(path)
    renamed, deleted, notes = {}, set(), []
    for op in ops:
        kind = op.get("op")
        n = t.num_rows
        if kind == "set_cells":
            by_col: dict[str, list] = {}
            for c in op.get("cells", []):
                by_col.setdefault(c["col"], []).append(c)
            for col, cells in by_col.items():
                if col not in t.column_names:
                    raise ValueError(f"No field called {col}")
                ck = _kind(t.schema.field(col).type)
                ck = "text" if ck == "date" else ck
                arr = _col_np(t, col)
                for c in cells:
                    r = int(c["row"])
                    if not 0 <= r < n:
                        raise ValueError(f"Row {r + 1} doesn't exist")
                    v = _coerce(c.get("value"), ck)
                    arr[r] = (np.nan if v is None else v) if ck in ("integer", "number") else v
                t = t.set_column(t.column_names.index(col), col, _to_arrow(arr, ck))
        elif kind in ("add_field", "calc"):
            col = (op.get("name") or op.get("column") or "").strip()
            if not col:
                raise ValueError("Give the field a name")
            exists = col in t.column_names
            if kind == "add_field" and exists:
                raise ValueError(f"There is already a field called {col}")
            expr = (op.get("expression") or "").strip()
            if expr:
                used, geoms = fieldcalc.referenced(expr, t.column_names)
                if geoms:
                    raise ValueError("Geometry values ($area …) only work on vector layers")
                vals, typ = fieldcalc.evaluate(expr, _data(t, used), n)
            else:
                vals, typ = np.full(n, None, dtype=object), "text"
            want = op.get("type") or "auto"
            if want != "auto":
                vals, typ = fieldcalc.cast(vals, want, n)
            elif exists:   # keep the existing field's type when updating it
                ek = _kind(t.schema.field(col).type)
                if ek in ("integer", "number", "text", "boolean") and ek != typ:
                    vals, typ = fieldcalc.cast(vals, ek, n)
            if op.get("q"):
                idx = _view(path, t, op["q"], None, False)
                base = _col_np(t, col) if exists else np.full(n, np.nan if typ in ("number", "integer") else None, dtype="float64" if typ in ("number", "integer") else object)
                if typ in ("number", "integer"):
                    base = base.astype("float64") if base.dtype != object else fieldcalc.cast(base, "number", n)[0]
                else:
                    base = base.astype(object)
                base[idx] = vals[idx]
                vals = base
            arr = _to_arrow(vals, typ)
            t = t.set_column(t.column_names.index(col), col, arr) if exists else t.append_column(col, arr)
        elif kind == "rename_field":
            old, new = op["old"], (op.get("new") or "").strip()
            if old not in t.column_names:
                raise ValueError(f"No field called {old}")
            if not new or new in t.column_names:
                raise ValueError("Choose a new, unused name")
            t = t.rename_columns([new if c == old else c for c in t.column_names])
            renamed[old] = new
        elif kind == "delete_field":
            if op["name"] not in t.column_names:
                raise ValueError(f"No field called {op['name']}")
            if t.num_columns == 1:
                raise ValueError("A table needs at least one field")
            t = t.drop_columns([op["name"]])
            deleted.add(op["name"])
        elif kind == "cast":
            col = op["column"]
            before = _col_np(t, col)
            vals, typ = fieldcalc.cast(before, op["type"], n)
            had = ~fieldcalc._isnull(before, n)
            now = np.isfinite(vals) if typ in ("number", "integer") else ~fieldcalc._isnull(vals, n)
            bad = int(np.sum(had & ~now))
            t = t.set_column(t.column_names.index(col), col, _to_arrow(vals, typ))
            if bad > 0:
                notes.append(f"{bad} value(s) of {col} couldn't be converted and are now empty")
            if typ == "text" and path.suffix.lower() != ".parquet":
                notes.append("CSV files don't store column types: numbers kept as text are read back as numbers. Use Parquet to keep the type.")
        elif kind == "delete_rows":
            if op.get("q"):
                drop = set(_view(path, t, op["q"], None, False).tolist())
            else:
                drop = {int(r) for r in op.get("rows", [])}
            keep = np.array([i for i in range(n) if i not in drop], dtype=np.int64)
            t = t.take(pa.array(keep))
            notes.append(f"Deleted {n - len(keep):,} row(s)")
        elif kind == "add_row":
            vals = op.get("values") or {}
            row = {}
            for f in t.schema:
                k = _kind(f.type)
                v = _coerce(vals.get(f.name), "text" if k == "date" else k)
                row[f.name] = pa.array([v], type=f.type) if k != "date" else pa.array([None], type=f.type)
            t = pa.concat_tables([t, pa.table(row, schema=t.schema)])
        else:
            raise ValueError(f"Unknown edit {kind}")
        _forget(path)
    _write(t, path)
    _sync_sidecar(path, t, renamed, deleted)
    return {"rows": t.num_rows, "columns": t.column_names, "undo": len(list(_history_dir(path).glob("*"))), "notes": notes}


def undo(path: str | Path) -> dict:
    path = Path(path)
    hist = sorted(_history_dir(path).glob("*"))
    if not hist:
        raise ValueError("Nothing to undo")
    hist[-1].replace(path)
    _forget(path)
    t = load(path)
    _sync_sidecar(path, t)
    return {"rows": t.num_rows, "columns": t.column_names, "undo": len(hist) - 1}


def undo_count(path: str | Path) -> int:
    return len(list(_history_dir(Path(path)).glob("*")))


def calc_preview(path: str | Path, expression: str, limit: int = 8) -> dict:
    from . import fieldcalc
    t = load(Path(path))
    used, geoms = fieldcalc.referenced(expression, t.column_names)
    if geoms:
        raise fieldcalc.CalcError("Geometry values ($area …) only work on vector layers")
    sub = t.slice(0, min(limit, t.num_rows))
    vals, typ = fieldcalc.evaluate(expression, _data(sub, used), sub.num_rows)
    return {"values": [_safe(v.item() if hasattr(v, "item") else v) for v in vals], "type": typ}


def derive(path: str | Path, query: str, out_dir: Path, name: str) -> Path:
    """Save the rows matching a search / filter as a new table."""
    import shutil
    path = Path(path)
    t = load(path)
    idx = _view(path, t, query or "", None, False)
    sub = t.take(pa.array(idx, type=pa.int64()))
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "selection"
    out, i = out_dir / f"{stem}{path.suffix}", 2
    while out.exists():
        out = out_dir / f"{stem}_{i}{path.suffix}"
        i += 1
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq
        pq.write_table(sub, out)
    else:
        import pyarrow.csv as pcsv
        pcsv.write_csv(sub, out)
    side = Path(str(path) + ".json")
    if side.exists():
        shutil.copy2(side, Path(str(out) + ".json"))
        _sync_sidecar(out, sub)
    return out


# ------------------------------------------------------------------ edit sessions: changes go to a working copy until Save
EDIT_DIR = ".edit"


def _session_paths(path: Path):
    work = path.parent / EDIT_DIR / f"{path.stem}__{path.suffix.lstrip('.')}.parquet"
    return work, Path(str(work) + ".session.json")


def _original_of(work: Path) -> Path:
    import json
    sess = json.loads(Path(str(work) + ".session.json").read_text())
    return work.parent.parent / sess["original"]


def is_work(path: str | Path) -> bool:
    return Path(path).parent.name == EDIT_DIR


def edit_start(path: str | Path) -> dict:
    """Open (or resume) an edit session: a Parquet working copy next to the table, in tables/.edit/."""
    import json
    import shutil
    import time as _t

    import pyarrow.parquet as pq
    path = Path(path)
    work, sess = _session_paths(path)
    resumed = work.exists() and sess.exists()
    if not resumed:
        work.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(load(path), work)
        side = Path(str(path) + ".json")
        if side.exists():
            shutil.copy2(side, Path(str(work) + ".json"))
        shutil.rmtree(_history_dir(work), ignore_errors=True)
        sess.write_text(json.dumps({"original": path.name, "started": _t.strftime("%Y-%m-%d %H:%M"), "log": []}))
    _forget(work)
    info = json.loads(sess.read_text())
    return {"work": work, "original": path, "resumed": resumed, "changes": undo_count(work), "log": info.get("log", []),
            "started": info.get("started")}


def edit_log(work: str | Path, entries: list[str] | None = None, pop: int = 0) -> list[str]:
    """The human-readable list of changes in the session (shown in the Save dialog)."""
    import json
    sess = Path(str(work) + ".session.json")
    info = json.loads(sess.read_text())
    log = info.get("log", [])
    if pop:
        log = log[:-pop] if pop < len(log) else []
    log += entries or []
    info["log"] = log[-200:]
    sess.write_text(json.dumps(info))
    return info["log"]


def edit_save(work: str | Path, mode: str = "overwrite", name: str | None = None) -> Path:
    """Write the working copy back: over the original (its previous version is kept for Restore) or as a new table."""
    import json

    import pyarrow.parquet as pq
    work = Path(work)
    orig = _original_of(work)
    t = pq.read_table(work)
    if mode == "overwrite":
        if not orig.exists():
            raise ValueError("The original table no longer exists. Save it as a new table instead.")
        _write(t, orig)           # archives the previous version in .history (Restore previous version)
        dest = orig
    else:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or f"{orig.stem}_edited")).strip("_") or f"{orig.stem}_edited"
        dest, i = orig.parent / f"{stem}{orig.suffix}", 2
        while dest.exists():
            dest = orig.parent / f"{stem}_{i}{orig.suffix}"
            i += 1
        if dest.suffix.lower() == ".parquet":
            pq.write_table(t, dest)
        else:
            import pyarrow.csv as pcsv
            pcsv.write_csv(t, dest)
    wside = Path(str(work) + ".json")
    if wside.exists():
        meta = json.loads(wside.read_text())
        meta.update(rows=t.num_rows, columns=t.column_names)
        Path(str(dest) + ".json").write_text(json.dumps(meta, indent=1, default=str))
    _forget(dest)
    edit_discard(work)
    return dest


def edit_discard(work: str | Path):
    import shutil
    work = Path(work)
    if not is_work(work):
        raise ValueError("Not an edit session")
    for p in (work, Path(str(work) + ".json"), Path(str(work) + ".session.json")):
        p.unlink(missing_ok=True)
    shutil.rmtree(_history_dir(work), ignore_errors=True)
    _forget(work)


def restore_previous(path: str | Path) -> dict:
    """Bring back the version saved before the last overwrite (or edit) of a table."""
    return undo(path)


def to_parquet_for_python(path: str | Path, out: Path) -> Path:
    import pyarrow.parquet as pq
    t = load(Path(path))
    t = t.append_column("__row__", pa.array(np.arange(t.num_rows, dtype="float64")))
    pq.write_table(t, out)
    return out


def replace_from_python(path: str | Path, out_parquet: str | Path) -> dict:
    """Use the Python script's result as the new version of the (working) table: one undo step."""
    import pyarrow.parquet as pq
    path = Path(path)
    old = load(path)
    t = pq.read_table(out_parquet)
    if "__row__" in t.column_names:
        t = t.drop_columns(["__row__"])
    if t.num_columns == 0:
        raise ValueError("The script removed every column")
    _write(t, path)
    deleted = set(old.column_names) - set(t.column_names)
    _sync_sidecar(path, t, None, deleted)
    return {"rows": t.num_rows, "columns": t.column_names, "undo": undo_count(path)}
