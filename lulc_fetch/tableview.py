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
