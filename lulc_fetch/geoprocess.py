"""Vector geoprocessing: buffer, select by attribute (a query), overlay (intersection, union, difference, symmetric
difference) and dissolve, on GeoJSON in EPSG:4326 (shapely; distances in metres through the local UTM zone)."""

from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path

import shapely
import shapely.prepared
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
    if geom.is_empty:   # e.g. a polygon shrunk to nothing: nothing to transform
        return geom
    return shape(transform_geom(src, dst, mapping(geom)))


# ------------------------------------------------------------------ buffer
def buffer(fc: dict, distance: float, *, segments: int = 64, dissolve: bool = False) -> dict:
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
    out = [(p, g) for p, g in out if not g.is_empty]   # shapes narrower than twice a negative distance vanish
    if not out:
        raise ValueError(f"Nothing is left: a negative distance only shrinks polygons, and every shape is narrower than {2 * -distance:g} m")
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


# ------------------------------------------------------------------ batch 1: spatial analysis
def _to_metric(items):
    """The items projected to their UTM zone (metres), and that zone."""
    crs = _utm(items)
    return [(p, _project(g, "EPSG:4326", crs)) for p, g in items], crs


PREDICATES = ("intersects", "within", "contains", "disjoint", "within_distance")


def select_by_location(fc_a: dict, fc_b: dict, predicate: str = "intersects", distance: float = 0) -> dict:
    """The features of A that intersect / are within / contain / are apart from (disjoint) / are within `distance`
    metres of any feature of B."""
    if predicate not in PREDICATES:
        raise ValueError(f"predicate: one of {', '.join(PREDICATES)}")
    A, B = _geoms(fc_a), _geoms(fc_b)
    if not A or not B:
        raise ValueError("Both layers need shapes")
    if predicate == "within_distance":
        if not distance or distance <= 0:
            raise ValueError("Give the distance in metres")
        (Bm, crs) = _to_metric(B)
        B = [(p, _project(g.buffer(distance), crs, "EPSG:4326")) for p, g in Bm]
        predicate = "intersects"
    tree = STRtree([g for _, g in B])
    keep = []
    for n, (props, g) in enumerate(A):
        hits = tree.query(g, predicate="intersects")
        if predicate == "disjoint":
            ok = len(hits) == 0
        elif predicate == "intersects":
            ok = len(hits) > 0
        elif predicate == "within":
            ok = any(g.within(B[int(j)][1]) for j in hits)
        else:   # contains
            ok = any(g.contains(B[int(j)][1]) for j in hits)
        if ok:
            keep.append((props, g))
        if n % 500 == 0:
            progress.update(n / len(A), f"Checking {n:,} of {len(A):,}")
    return _fc(keep)


def spatial_join(fc_a: dict, fc_b: dict, how: str = "intersects", max_distance: float | None = None) -> dict:
    """Each feature of A with the attributes of the feature of B it overlaps most (how='intersects'), lies within
    ('within') or is nearest to ('nearest', with join_dist_m; up to max_distance metres when given). A's features
    without a match keep their own attributes only."""
    if how not in ("intersects", "within", "nearest"):
        raise ValueError("how: intersects, within or nearest")
    A, B = _geoms(fc_a), _geoms(fc_b)
    if not A or not B:
        raise ValueError("Both layers need shapes")
    out = []
    if how == "nearest":
        (Am, crs) = _to_metric(A)
        Bm = [(p, _project(g, "EPSG:4326", crs)) for p, g in B]
        tree = STRtree([g for _, g in Bm])
        for (pa, ga), (_, gam) in zip(A, Am):
            j = int(tree.nearest(gam))
            d = gam.distance(Bm[j][1])
            props = dict(pa)
            if max_distance is None or d <= max_distance:
                props = {**_merge_props(pa, Bm[j][0]), "join_dist_m": round(d, 2)}
            out.append((props, ga))
        return _fc(out)
    tree = STRtree([g for _, g in B])
    for pa, ga in A:
        best, best_v = None, 0.0
        for j in tree.query(ga, predicate="intersects"):
            gb = B[int(j)][1]
            if how == "within" and not ga.within(gb):
                continue
            v = ga.intersection(gb).area if ga.area > 0 else 1.0   # polygons: the largest overlap; points / lines: the first
            if best is None or v > best_v:
                best, best_v = int(j), v
        out.append((_merge_props(pa, B[best][0]) if best is not None else dict(pa), ga))
    return _fc(out)


def calculate_geometry(fc: dict) -> dict:
    """Area (m², ha), perimeter or length (m) and the centroid (lon, lat) as fields, measured in metres in the UTM zone."""
    items = _geoms(fc)
    if not items:
        raise ValueError("The layer has no shapes")
    metric, _ = _to_metric(items)
    out = []
    for (props, g), (_, gm) in zip(items, metric):
        p = dict(props)
        if gm.area > 0:
            p.update(area_m2=round(gm.area, 2), area_ha=round(gm.area / 1e4, 4), perimeter_m=round(gm.length, 2))
        elif gm.length > 0:
            p.update(length_m=round(gm.length, 2))
        c = g.centroid
        p.update(centroid_lon=round(c.x, 6), centroid_lat=round(c.y, 6))
        out.append((p, g))
    return _fc(out)


def count_points(fc_polys: dict, fc_points: dict, sum_field: str | None = None) -> dict:
    """Each polygon with the number of points inside it (point_count), and the sum of a points' field when asked."""
    P, Q = _geoms(fc_polys), _geoms(fc_points)
    if not P:
        raise ValueError("The polygon layer has no shapes")
    tree = STRtree([g for _, g in Q]) if Q else None
    out = []
    for props, g in P:
        hits = [int(j) for j in tree.query(g, predicate="intersects")] if tree is not None else []
        p = {**props, "point_count": len(hits)}
        if sum_field:
            vals = []
            for j in hits:
                try:
                    vals.append(float(Q[j][0].get(sum_field)))
                except (TypeError, ValueError):
                    pass
            p[f"sum_{sum_field}"] = round(sum(vals), 6)
        out.append((p, g))
    return _fc(out)


def join_table(fc: dict, rows: list[dict], layer_field: str, table_field: str) -> tuple[dict, int]:
    """The layer with the columns of a table's row whose `table_field` equals the feature's `layer_field` (text
    compared, without case or spaces at the ends). Returns the layer and how many features found no row."""
    def key(v):
        if v is None:
            return None
        try:
            f = float(v)
            return str(int(f)) if f.is_integer() else str(f)   # 7, 7.0 and "7" match
        except (TypeError, ValueError):
            return str(v).strip().lower()
    index = {}
    for r in rows:
        index.setdefault(key(r.get(table_field)), r)
    feats, missing = [], 0
    for f in fc.get("features") or []:
        props = dict(f.get("properties") or {})
        r = index.get(key(props.get(layer_field)))
        if r is None:
            missing += 1
        else:
            for k, v in r.items():
                if k != table_field:
                    props[f"t_{k}" if k in props else k] = v
        feats.append({**f, "properties": props})
    return {"type": "FeatureCollection", "features": feats}, missing


def zonal_stats(fc: dict, raster: str | Path, *, band: int = 1, stats=("mean", "min", "max", "std", "count"), categorical: bool = False,
                prefix: str = "") -> dict:
    """Each polygon with the raster's values summarised inside it: mean, min, max, std, median, sum, count (pixels),
    or, for a class raster (categorical), the % of each class and the majority class. Classes with names (the raster's
    "classes" tag, as WorldCover and the app's class maps have) give fields like pct_tree_cover and majority_class."""
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom
    from rasterio.windows import from_bounds

    items = _geoms(fc)
    if not items:
        raise ValueError("The layer has no shapes")
    out = []
    with rasterio.open(raster) as src:
        if not 1 <= band <= src.count:
            raise ValueError(f"The raster has {src.count} band(s); there is no band {band}")
        if src.crs is None:
            raise ValueError("The raster has no coordinate system")
        names = {}
        if categorical:
            from .convert import class_names
            names = class_names(src)
        slug = lambda c: (re.sub(r"[^a-z0-9]+", "_", names[c].lower()).strip("_") or str(c)) if c in names else str(c)
        for n, (props, g) in enumerate(items):
            p = dict(props)
            gr = shape(transform_geom("EPSG:4326", src.crs, mapping(g)))
            try:
                win = from_bounds(*gr.bounds, transform=src.transform).round_offsets().round_lengths()
                win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
            except Exception:  # noqa: BLE001 — the polygon is outside the raster
                win = None
            vals = np.array([])
            if win is not None and win.width > 0 and win.height > 0:
                a = src.read(band, window=win, masked=True)
                for touched in (gr.area == 0, True):   # a polygon smaller than a pixel: the pixels it touches
                    inside = ~geometry_mask([mapping(gr)], out_shape=a.shape, transform=src.window_transform(win), all_touched=touched)
                    v = a.data[inside & ~np.ma.getmaskarray(a)].astype("float64")
                    vals = v[np.isfinite(v)]
                    if vals.size or touched:
                        break
            if categorical:
                p[f"{prefix}count"] = int(vals.size)
                if vals.size:
                    cls, cnt = np.unique(vals.astype("int64"), return_counts=True)
                    for c, k in zip(cls, cnt):
                        p[f"{prefix}pct_{slug(int(c))}"] = round(100 * k / vals.size, 2)
                    p[f"{prefix}majority"] = int(cls[np.argmax(cnt)])
                    if names:
                        p[f"{prefix}majority_class"] = names.get(int(cls[np.argmax(cnt)]), str(int(cls[np.argmax(cnt)])))
            else:
                f = {"mean": np.mean, "min": np.min, "max": np.max, "std": np.std, "median": np.median, "sum": np.sum}
                for s in stats:
                    if s == "count":
                        p[f"{prefix}count"] = int(vals.size)
                    elif s in f:
                        p[f"{prefix}{s}"] = round(float(f[s](vals)), 6) if vals.size else None
            out.append((p, g))
            if n % 50 == 0:
                progress.update(n / len(items), f"Polygon {n:,} of {len(items):,}")
    return _fc(out)


# ------------------------------------------------------------------ batch 3: geometry helpers
def centroids(fc: dict, inside: bool = False) -> dict:
    """A point per feature: its centroid, or (inside=True) a point surely inside it."""
    return _fc([(p, g.representative_point() if inside else g.centroid) for p, g in _geoms(fc)])


def convex_hull(fc: dict, whole: bool = False) -> dict:
    """The convex hull of each feature, or of the whole layer."""
    items = _geoms(fc)
    if whole:
        return _fc([({"features": len(items)}, shapely.union_all([g for _, g in items]).convex_hull)])
    return _fc([(p, g.convex_hull) for p, g in items])


def simplify(fc: dict, tolerance: float) -> dict:
    """Shapes with fewer vertices: no point moves more than `tolerance` metres (shapes stay valid)."""
    if not tolerance or tolerance <= 0:
        raise ValueError("Give the tolerance in metres")
    items = _geoms(fc)
    metric, crs = _to_metric(items)
    return _fc([(p, _project(gm.simplify(tolerance, preserve_topology=True), crs, "EPSG:4326")) for (p, _), (_, gm) in zip(items, metric)])


def merge_layers(fcs: list[tuple[str, dict]]) -> dict:
    """Several layers as one, each feature with the name of the layer it came from (source_layer)."""
    feats = []
    for name, fc in fcs:
        for f in fc.get("features") or []:
            feats.append({**f, "properties": {**(f.get("properties") or {}), "source_layer": name}})
    return {"type": "FeatureCollection", "features": feats}


def explode(fc: dict) -> dict:
    """Multipart features as one feature per part."""
    out = []
    for p, g in _geoms(fc):
        parts = list(getattr(g, "geoms", [g]))
        out += [({**p, "part": i + 1}, part) for i, part in enumerate(parts)]
    return _fc(out)


def fishnet(area: dict, cell: float, clip: bool = True) -> dict:
    """A grid of square cells of `cell` metres over an area (or a layer's extent); clip: only the cells (parts) inside it."""
    if not cell or cell <= 0:
        raise ValueError("Give the cell size in metres")
    items = _geoms(read_layer(area))
    if not items:
        raise ValueError("No area given")
    metric, crs = _to_metric(items)
    shape_m = shapely.union_all([g for _, g in metric])
    x0, y0, x1, y1 = shape_m.bounds
    nx, ny = math.ceil((x1 - x0) / cell), math.ceil((y1 - y0) / cell)
    if nx * ny > 50_000:
        raise ValueError(f"That makes {nx * ny:,} cells → use bigger cells (at most 50,000)")
    out, n = [], 0
    prepared = shapely.prepared.prep(shape_m) if hasattr(shapely, "prepared") else None
    for r in range(ny):
        for c in range(nx):
            box = shapely.box(x0 + c * cell, y1 - (r + 1) * cell, x0 + (c + 1) * cell, y1 - r * cell)
            if clip:
                if not (prepared.intersects(box) if prepared else box.intersects(shape_m)):
                    continue
                box = box.intersection(shape_m) if not (prepared and prepared.contains(box)) else box
                if box.is_empty:
                    continue
            n += 1
            out.append(({"id": n, "row": r + 1, "col": c + 1}, _project(box, crs, "EPSG:4326")))
    return _fc(out)


def random_points(area: dict, n: int, per_feature: bool = False, seed: int | None = None) -> dict:
    """`n` random points inside the polygons (in all, or n in each polygon with per_feature), e.g. for sampling."""
    import random
    if not 1 <= n <= 100_000:
        raise ValueError("Ask for 1 to 100,000 points")
    rng = random.Random(seed)
    items = [(p, g) for p, g in _geoms(read_layer(area)) if g.area > 0]
    if not items:
        raise ValueError("Random points need polygons")
    groups = [(p, g, n) for p, g in items] if per_feature else [({}, shapely.union_all([g for _, g in items]), n)]
    out, k = [], 0
    for props, g, want in groups:
        x0, y0, x1, y1 = g.bounds
        tries = 0
        prep = shapely.prepared.prep(g) if hasattr(shapely, "prepared") else g
        got = 0
        while got < want and tries < want * 200:
            tries += 1
            pt = shapely.Point(rng.uniform(x0, x1), rng.uniform(y0, y1))
            if prep.contains(pt):
                k += 1; got += 1
                out.append(({**props, "id": k}, pt))
    return _fc(out)
