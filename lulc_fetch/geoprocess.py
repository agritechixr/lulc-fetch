"""Vector geoprocessing: buffer, select by attribute (a query), overlay (intersection, union, difference, symmetric
difference) and dissolve, on GeoJSON in EPSG:4326 (shapely; distances in metres through the local UTM zone)."""

from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape
from shapely.strtree import STRtree

from . import progress


# ------------------------------------------------------------------ reading
def read_layer(src, root: Path | None = None) -> dict:
    """A FeatureCollection: given as GeoJSON, or a .geojson / .json / zipped shapefile in the workspace."""
    if isinstance(src, dict):
        if src.get("type") == "FeatureCollection":
            return src
        if src.get("type") == "Feature":
            return {"type": "FeatureCollection", "features": [src]}
        if src.get("type") and "coordinates" in src:
            return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": src}]}
        raise ValueError("Not a vector layer (GeoJSON)")
    if not isinstance(src, str) or not src:
        raise ValueError("No layer given")
    p = (root / src).resolve() if root else Path(src)
    if root and not p.is_relative_to(root.resolve()):
        raise ValueError("The layer's file must be in the workspace")
    if not p.is_file():
        raise ValueError(f"The layer's file is missing: {src}")
    ext = p.suffix.lower()
    if ext in (".geojson", ".json"):
        return read_layer(json.loads(p.read_text(encoding="utf-8")))
    if ext in (".zip", ".shp"):
        import shapefile
        with shapefile.Reader(str(p)) as r:
            fields = [f[0] for f in r.fields[1:]]
            feats = [{"type": "Feature", "properties": dict(zip(fields, rec.record)), "geometry": rec.shape.__geo_interface__} for rec in r.shapeRecords()]
        return {"type": "FeatureCollection", "features": feats}
    raise ValueError(f"Can't read {p.name} as a vector layer (GeoJSON or a zipped shapefile)")


def _geoms(fc: dict) -> list[tuple[dict, object]]:
    """(properties, valid shapely geometry) of each feature that has a geometry."""
    out = []
    for f in fc.get("features") or []:
        g = f.get("geometry")
        if not g:
            continue
        geom = shape(g)
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
        if not geom.is_empty:
            out.append((dict(f.get("properties") or {}), geom))
    return out


def _fc(items) -> dict:
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": p, "geometry": mapping(g)} for p, g in items if g is not None and not g.is_empty]}


def _utm(fc_items) -> str:
    """The UTM zone (EPSG code) of the layers' centre: distances there are in metres."""
    xs = [g.centroid.x for _, g in fc_items[:2000]] or [0.0]
    ys = [g.centroid.y for _, g in fc_items[:2000]] or [0.0]
    lon, lat = sum(xs) / len(xs), sum(ys) / len(ys)
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + min(max(zone, 1), 60)}"


def _project(geom, src: str, dst: str):
    from rasterio.warp import transform_geom
    return shape(transform_geom(src, dst, mapping(geom)))


# ------------------------------------------------------------------ buffer
def buffer(fc: dict, distance: float, *, segments: int = 16, dissolve: bool = False) -> dict:
    """Each feature grown (or, with a negative distance, shrunk) by `distance` metres; dissolve: one shape."""
    if not math.isfinite(distance) or distance == 0:
        raise ValueError("Give a distance in metres (not 0)")
    items = _geoms(fc)
    if not items:
        raise ValueError("The layer has no shapes")
    crs = _utm(items)
    out = []
    for n, (props, g) in enumerate(items):
        b = _project(_project(g, "EPSG:4326", crs).buffer(distance, quad_segs=max(1, segments // 4)), crs, "EPSG:4326")
        out.append(({**props, "buffer_m": distance}, b))
        if n % 200 == 0:
            progress.update(n / len(items), f"Buffering {n:,} of {len(items):,}")
    if dissolve:
        out = [({"buffer_m": distance, "features": len(out)}, shapely.union_all([g for _, g in out]))]
    return _fc(out)


# ------------------------------------------------------------------ select by attribute
_OPS = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.Compare, ast.Eq, ast.NotEq, ast.Lt,
        ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Name, ast.Load, ast.Constant, ast.Tuple, ast.List, ast.BinOp, ast.Add,
        ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Call)
_FUNCS = {"lower": lambda s: str(s).lower(), "upper": lambda s: str(s).upper(), "len": lambda s: len(str(s)), "abs": abs, "round": round,
          "contains": lambda s, t: str(t).lower() in str(s).lower(), "startswith": lambda s, t: str(s).lower().startswith(str(t).lower()),
          "endswith": lambda s, t: str(s).lower().endswith(str(t).lower()), "number": lambda s: float(s)}


def _sqlish(where: str) -> str:
    """Also accept SQL-like writing: AND / OR / NOT, a single =, <>, LIKE '%text%'."""
    w = re.sub(r"\b(AND|OR|NOT|IN)\b", lambda m: m.group(1).lower(), where)
    w = re.sub(r"<>", "!=", w)
    w = re.sub(r"(?<![<>!=])=(?!=)", "==", w)
    w = re.sub(r"(\w+)\s+like\s+'%([^%']*)%'", r"contains(\1, '\2')", w, flags=re.I)
    return w


def compile_query(where: str, fields: list[str]):
    """A safe test of a feature's attributes, e.g.  crop == "rice" and area_ha > 2  ·  name in ("A", "B")  ·
    contains(name, "farm"). Field names ignore case; a word that isn't a field is text (crop == rice)."""
    text = _sqlish(where.strip())
    if not text:
        raise ValueError("Write a condition, e.g.  crop == \"rice\" and area > 2")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"The condition can't be read: {e.msg}") from None
    lower = {f.lower(): f for f in fields}
    for node in ast.walk(tree):
        if not isinstance(node, _OPS):
            raise ValueError(f"Not allowed in a condition: {type(node).__name__}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            raise ValueError(f"Unknown function; use one of {', '.join(_FUNCS)}")

    def ev(n, props):
        if isinstance(n, ast.Expression):
            return ev(n.body, props)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            key = lower.get(n.id.lower())
            return props.get(key) if key else n.id   # not a field: the word itself
        if isinstance(n, (ast.Tuple, ast.List)):
            return [ev(x, props) for x in n.elts]
        if isinstance(n, ast.BoolOp):
            vals = (ev(x, props) for x in n.values)
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand, props)
            return (not v) if isinstance(n.op, ast.Not) else (-v if isinstance(n.op, ast.USub) else v)
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left, props), ev(n.right, props)
            return {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Div: lambda: a / b, ast.Mod: lambda: a % b}[type(n.op)]()
        if isinstance(n, ast.Call):
            return _FUNCS[n.func.id](*[ev(x, props) for x in n.args])
        if isinstance(n, ast.Compare):
            left = ev(n.left, props)
            for op, right_n in zip(n.ops, n.comparators):
                right = ev(right_n, props)
                if not _cmp(op, left, right):
                    return False
                left = right
            return True
        raise ValueError("Not allowed in a condition")

    def test(props: dict) -> bool:
        try:
            return bool(ev(tree, props))
        except (TypeError, ValueError, ZeroDivisionError):
            return False   # a missing or non-numeric value doesn't match
    return test


def _cmp(op, a, b) -> bool:
    if isinstance(op, (ast.In, ast.NotIn)):
        r = (str(a).lower() in [str(x).lower() for x in b]) if isinstance(b, list) else (str(a).lower() in str(b).lower())
        return r if isinstance(op, ast.In) else not r
    if a is None or b is None:
        return isinstance(op, ast.NotEq) and a != b or isinstance(op, ast.Eq) and a == b
    try:   # numbers kept as text still compare as numbers
        a2, b2 = float(a), float(b)
        if not isinstance(a, bool) and not isinstance(b, bool):
            a, b = a2, b2
    except (TypeError, ValueError):
        a, b = str(a).lower(), str(b).lower()
    return {ast.Eq: a == b, ast.NotEq: a != b, ast.Lt: a < b, ast.LtE: a <= b, ast.Gt: a > b, ast.GtE: a >= b}[type(op)]


def query(fc: dict, where: str) -> dict:
    feats = fc.get("features") or []
    fields = sorted({k for f in feats[:500] for k in (f.get("properties") or {})})
    test = compile_query(where, fields)
    return {"type": "FeatureCollection", "features": [f for f in feats if test(f.get("properties") or {})]}


# ------------------------------------------------------------------ overlay
def _keep_like(geom, like):
    """Only the parts of the same kind as the input (polygons stay polygons; slivers of lines / points are dropped)."""
    want = {"Polygon": ("Polygon", "MultiPolygon"), "MultiPolygon": ("Polygon", "MultiPolygon"), "LineString": ("LineString", "MultiLineString"),
            "MultiLineString": ("LineString", "MultiLineString"), "Point": ("Point", "MultiPoint"), "MultiPoint": ("Point", "MultiPoint")}.get(like.geom_type)
    if geom is None or geom.is_empty or not want:
        return geom
    if geom.geom_type in want:
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in want]
    return shapely.union_all(parts) if parts else None


def _merge_props(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[f"b_{k}" if k in out else k] = v
    return out


def overlay(fc_a: dict, fc_b: dict, how: str = "intersection") -> dict:
    """intersection: where both are · union: every piece of both, with the attributes of each · difference: A without B ·
    symmetric_difference: in one but not both · clip: A cut to B (A's attributes only)."""
    if how not in ("intersection", "union", "difference", "symmetric_difference", "clip"):
        raise ValueError("how: intersection, union, difference, symmetric_difference or clip")
    A, B = _geoms(fc_a), _geoms(fc_b)
    if not A or not B:
        raise ValueError("Both layers need shapes")
    tree_b, tree_a = STRtree([g for _, g in B]), STRtree([g for _, g in A])
    out = []

    def inter_pieces():
        for n, (pa, ga) in enumerate(A):
            for j in tree_b.query(ga, predicate="intersects"):
                pb, gb = B[int(j)]
                piece = _keep_like(ga.intersection(gb), ga)
                if piece is not None and not piece.is_empty:
                    yield (_merge_props(pa, pb) if how != "clip" else dict(pa)), piece
            if n % 100 == 0:
                progress.update(0.6 * n / len(A), f"Overlaying {n:,} of {len(A):,}")

    def minus(items, tree, others):
        for p, g in items:
            near = [others[int(j)][1] for j in tree.query(g, predicate="intersects")]
            rest = _keep_like(g.difference(shapely.union_all(near)), g) if near else g
            if rest is not None and not rest.is_empty:
                yield dict(p), rest

    if how in ("intersection", "clip"):
        out = list(inter_pieces())
    elif how == "difference":
        out = list(minus(A, tree_b, B))
    elif how == "symmetric_difference":
        out = list(minus(A, tree_b, B)) + list(minus(B, tree_a, A))
    else:   # union: the shared pieces, then what is only in A, then only in B
        out = list(inter_pieces()) + list(minus(A, tree_b, B)) + list(minus(B, tree_a, A))
    return _fc(out)


def dissolve(fc: dict, field: str | None = None) -> dict:
    """Shapes merged: all into one, or one per value of a field."""
    items = _geoms(fc)
    groups: dict = {}
    for p, g in items:
        groups.setdefault(p.get(field) if field else None, []).append(g)
    return _fc([({field: k, "features": len(v)} if field else {"features": len(v)}, shapely.union_all(v)) for k, v in groups.items()])
