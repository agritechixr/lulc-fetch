"""Field calculator: evaluate expressions over table columns / vector attributes, vectorised with numpy.

Safe by construction: the expression is parsed with Python's ``ast`` and only whitelisted nodes and functions are
evaluated (no attribute access, no imports, no arbitrary calls).

Syntax
  columns      name   or   [name with spaces]
  geometry     $area (m²)  $area_ha  $area_km2  $perimeter (m)  $length (m)  $x  $y (centroid lon / lat)  $id (row number)
  operators    + - * / // % **   == != < <= > >=   and or not   a if cond else b
  functions    abs round sqrt log log10 exp floor ceil min max clip iif coalesce isnull
               upper lower title strip len concat replace substr contains startswith endswith text number integer
"""

from __future__ import annotations

import ast
import math
import re

import numpy as np

GEOM_VARS = ("area", "area_ha", "area_km2", "perimeter", "length", "x", "y", "id")


class CalcError(ValueError):
    pass


def _is_text(a) -> bool:
    return isinstance(a, str) or (isinstance(a, np.ndarray) and a.dtype.kind in "OUS")


def _num(a):
    if isinstance(a, np.ndarray) and a.dtype.kind in "US":
        a = a.astype(object)
    if isinstance(a, np.ndarray) and a.dtype == object:
        out = np.full(len(a), np.nan)
        for i, v in enumerate(a):
            try:
                out[i] = float(v) if v not in (None, "") else np.nan
            except (TypeError, ValueError):
                pass
        return out
    if isinstance(a, str):
        try:
            return float(a)
        except ValueError:
            return np.nan
    return np.asarray(a, dtype="float64") if isinstance(a, np.ndarray) else a


def _txt(a, n):
    def one(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return ""
        if isinstance(v, (float, np.floating)) and float(v).is_integer():
            return str(int(v))
        if isinstance(v, (bool, np.bool_)):
            return "true" if v else "false"
        return str(v)
    if isinstance(a, np.ndarray):
        return np.array([one(v) for v in a], dtype=object)
    return np.array([one(a)] * n, dtype=object)


def _bool(a, n):
    if isinstance(a, np.ndarray):
        if a.dtype == bool:
            return a
        if a.dtype == object:
            return np.array([bool(v) and v not in ("false", "0", "no") for v in a])
        return np.nan_to_num(a.astype("float64")) != 0
    return np.full(n, bool(a))


def _str_fn(f):
    def g(n, s, *args):
        s = _txt(s, n)
        return np.array([f(v, *[a[i] if isinstance(a, np.ndarray) else a for a in args]) for i, v in enumerate(s)], dtype=object)
    return g


def _substr(v, start, length=None):
    start = int(start) - 1 if start else 0
    return v[start:] if length is None else v[start:start + int(length)]


FUNCS = {
    "abs": lambda n, a: np.abs(_num(a)),
    "round": lambda n, a, d=0: np.round(_num(a), int(d)),
    "sqrt": lambda n, a: np.sqrt(_num(a)),
    "log": lambda n, a: np.log(_num(a)),
    "log10": lambda n, a: np.log10(_num(a)),
    "exp": lambda n, a: np.exp(_num(a)),
    "floor": lambda n, a: np.floor(_num(a)),
    "ceil": lambda n, a: np.ceil(_num(a)),
    "min": lambda n, *a: np.fmin.reduce([np.broadcast_to(_num(x), (n,)) for x in a]),
    "max": lambda n, *a: np.fmax.reduce([np.broadcast_to(_num(x), (n,)) for x in a]),
    "clip": lambda n, a, lo, hi: np.clip(_num(a), _num(lo), _num(hi)),
    "iif": lambda n, c, a, b: _iif(n, c, a, b),
    "coalesce": lambda n, *a: _coalesce(n, a),
    "isnull": lambda n, a: _isnull(a, n),
    "upper": _str_fn(lambda v: v.upper()), "lower": _str_fn(lambda v: v.lower()), "title": _str_fn(lambda v: v.title()),
    "strip": _str_fn(lambda v: v.strip()),
    "len": lambda n, a: np.array([len(v) for v in _txt(a, n)], dtype="float64"),
    "concat": lambda n, *a: np.array(["".join(p) for p in zip(*[_txt(x, n) for x in a])], dtype=object),
    "replace": _str_fn(lambda v, old, new: v.replace(str(old), str(new))),
    "substr": _str_fn(_substr),
    "contains": lambda n, s, sub: np.array([str(sub).lower() in v.lower() for v in _txt(s, n)]),
    "startswith": lambda n, s, sub: np.array([v.startswith(str(sub)) for v in _txt(s, n)]),
    "endswith": lambda n, s, sub: np.array([v.endswith(str(sub)) for v in _txt(s, n)]),
    "text": lambda n, a: _txt(a, n),
    "number": lambda n, a: _num(a) if isinstance(a, np.ndarray) else np.full(n, _num(a)),
    "integer": lambda n, a: np.trunc(_num(a)),
}
FUNC_HELP = {
    "abs": "abs(x)", "round": "round(x, digits)", "sqrt": "sqrt(x)", "log": "log(x)", "log10": "log10(x)", "exp": "exp(x)",
    "floor": "floor(x)", "ceil": "ceil(x)", "min": "min(a, b, …)", "max": "max(a, b, …)", "clip": "clip(x, low, high)",
    "iif": "iif(condition, if_true, if_false)", "coalesce": "coalesce(a, b, …): first non-empty", "isnull": "isnull(x)",
    "upper": "upper(text)", "lower": "lower(text)", "title": "title(text)", "strip": "strip(text)", "len": "len(text)",
    "concat": "concat(a, \" - \", b)", "replace": "replace(text, \"old\", \"new\")", "substr": "substr(text, start, length)",
    "contains": "contains(text, \"part\")", "startswith": "startswith(text, \"A\")", "endswith": "endswith(text, \"z\")",
    "text": "text(x)", "number": "number(x)", "integer": "integer(x)",
}


def _iif(n, c, a, b):
    cond = _bool(c, n)
    if _is_text(a) or _is_text(b) or a is None or b is None:
        A = np.broadcast_to(np.asarray(a, dtype=object), (n,)) if isinstance(a, np.ndarray) else np.full(n, a, dtype=object)
        B = np.broadcast_to(np.asarray(b, dtype=object), (n,)) if isinstance(b, np.ndarray) else np.full(n, b, dtype=object)
        return np.where(cond, A, B).astype(object)
    return np.where(cond, _num(a), _num(b))


def _isnull(a, n):
    if isinstance(a, np.ndarray):
        if a.dtype == object:
            return np.array([v is None or v == "" or (isinstance(v, float) and math.isnan(v)) for v in a])
        return ~np.isfinite(a.astype("float64")) if a.dtype.kind in "fiu" else np.zeros(n, bool)
    return np.full(n, a is None)


def _coalesce(n, args):
    out = np.broadcast_to(args[-1], (n,)).astype(object).copy() if isinstance(args[-1], np.ndarray) else np.full(n, args[-1], dtype=object)
    for a in reversed(args[:-1]):
        arr = np.broadcast_to(a, (n,)) if isinstance(a, np.ndarray) else np.full(n, a, dtype=object)
        ok = ~_isnull(np.asarray(arr), n)
        out[ok] = np.asarray(arr)[ok]
    return out


def _prepare(expr: str):
    """[name] → placeholders, $geom → placeholders. Returns (python expression, {placeholder: column}, geometry vars used)."""
    if not expr or not expr.strip():
        raise CalcError("Type an expression")
    if len(expr) > 2000:
        raise CalcError("The expression is too long")
    cols, geoms = {}, set()

    def col(m):
        key = f"__c{len(cols)}"
        cols[key] = m.group(1)
        return key

    out = re.sub(r"\[([^\]]+)\]", col, expr)

    def geo(m):
        g = m.group(1).lower()
        if g not in GEOM_VARS:
            raise CalcError(f"Unknown geometry value ${m.group(1)}. Use " + ", ".join("$" + v for v in GEOM_VARS))
        geoms.add(g)
        return f"__g_{g}"

    out = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", geo, out)
    out = re.sub(r"(?<![=!<>])=(?!=)", "==", out)       # allow a single = for comparison
    out = re.sub(r"\bAND\b", "and", re.sub(r"\bOR\b", "or", re.sub(r"\bNOT\b", "not", out)))
    out = re.sub(r"<>", "!=", out)
    return out, cols, geoms


def referenced(expr: str, columns: list[str]) -> tuple[set, set]:
    """Columns and geometry values an expression uses (to send only what is needed)."""
    py, cols, geoms = _prepare(expr)
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"Syntax error near position {e.offset}: check brackets, quotes and operators") from None
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used = {cols[n] for n in names if n in cols} | {n for n in names if n in columns}
    return used, geoms


def evaluate(expr: str, data: dict, n: int, geom: dict | None = None):
    """data: {column: numpy array}; geom: {var: numpy array}. Returns (numpy array, type: number | integer | text | boolean)."""
    py, cols, _ = _prepare(expr)
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"Syntax error near position {e.offset}: check brackets, quotes and operators") from None

    def name(node):
        nm = node.id
        if nm in cols:
            if cols[nm] not in data:
                raise CalcError(f"No field called [{cols[nm]}]")
            return data[cols[nm]]
        if nm.startswith("__g_"):
            g = nm[4:]
            if not geom or g not in geom:
                raise CalcError(f"${g} needs a vector layer with geometries")
            return geom[g]
        if nm in data:
            return data[nm]
        low = {"true": True, "false": False, "null": None, "none": None, "pi": math.pi}
        if nm.lower() in low:
            return low[nm.lower()]
        raise CalcError(f"Unknown name '{nm}'. Put field names in square brackets, e.g. [{nm}], and text in quotes")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str, bool)) or node.value is None:
                return node.value
            raise CalcError("Unsupported value")
        if isinstance(node, ast.Name):
            return name(node)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if isinstance(node.op, ast.Not):
                return ~_bool(v, n)
            if isinstance(node.op, ast.USub):
                return -_num(v)
            if isinstance(node.op, ast.UAdd):
                return _num(v)
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add) and (_is_text(a) or _is_text(b)):
                return np.array([x + y for x, y in zip(_txt(a, n), _txt(b, n))], dtype=object)
            a, b = _num(a), _num(b)
            with np.errstate(all="ignore"):
                ops = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide,
                       ast.FloorDiv: np.floor_divide, ast.Mod: np.mod, ast.Pow: np.power}
                for k, f in ops.items():
                    if isinstance(node.op, k):
                        return f(a, b)
        if isinstance(node, ast.BoolOp):
            vals = [_bool(ev(v), n) for v in node.values]
            return np.logical_and.reduce(vals) if isinstance(node.op, ast.And) else np.logical_or.reduce(vals)
        if isinstance(node, ast.Compare):
            left, res = ev(node.left), np.ones(n, bool)
            for op, comp in zip(node.ops, node.comparators):
                right = ev(comp)
                if _is_text(left) or _is_text(right):
                    if isinstance(op, (ast.Eq, ast.NotEq)):
                        l_, r_ = _txt(left, n), _txt(right, n)
                    else:
                        l_, r_ = _num(left), _num(right)
                else:
                    l_, r_ = _num(left), _num(right)
                cmp = {ast.Eq: np.equal, ast.NotEq: np.not_equal, ast.Lt: np.less, ast.LtE: np.less_equal,
                       ast.Gt: np.greater, ast.GtE: np.greater_equal}
                f = next((f for k, f in cmp.items() if isinstance(op, k)), None)
                if f is None:
                    raise CalcError("Unsupported comparison")
                with np.errstate(invalid="ignore"):
                    res = res & np.asarray(f(l_, r_), dtype=bool)
                left = right
            return res
        if isinstance(node, ast.IfExp):
            return FUNCS["iif"](n, ev(node.test), ev(node.body), ev(node.orelse))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id.lower() not in FUNCS or node.keywords:
                fn = getattr(node.func, "id", None)
                if fn is None:
                    raise CalcError("Only plain functions like round(x) are allowed")
                raise CalcError(f"Unknown function '{fn}'. Available: " + ", ".join(sorted(FUNCS)))
            try:
                return FUNCS[node.func.id.lower()](n, *[ev(a) for a in node.args])
            except CalcError:
                raise
            except TypeError:
                raise CalcError(f"Wrong number of arguments: {FUNC_HELP[node.func.id.lower()]}") from None
        raise CalcError(f"'{type(node).__name__}' isn't allowed in expressions")

    out = ev(tree)
    if isinstance(out, np.ndarray) and out.dtype.kind in "US":
        out = out.astype(object)
    if not isinstance(out, np.ndarray):
        out = np.full(n, out, dtype=object if isinstance(out, str) or out is None else None)
    out = np.broadcast_to(out, (n,)).copy() if out.shape != (n,) else out
    if out.dtype == bool:
        return out, "boolean"
    if out.dtype == object:
        if all(v is None or isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) for v in out):
            out = np.array([np.nan if v is None else float(v) for v in out])
        else:
            return out, "text"
    out = out.astype("float64")
    fin = out[np.isfinite(out)]
    return out, ("integer" if len(fin) and np.all(fin == np.round(fin)) and np.all(np.abs(fin) < 2**53) else "number")


def cast(values: np.ndarray, typ: str, n: int):
    """Convert values to number / integer / text / boolean."""
    if typ == "text":
        return _txt(values, n), "text"
    if typ == "boolean":
        return _bool(values, n), "boolean"
    v = _num(values) if isinstance(values, np.ndarray) else np.full(n, _num(values))
    return (np.trunc(v) if typ == "integer" else v.astype("float64")), typ


def geometry_measures(geoms: list[dict | None], needed: set) -> dict:
    """$area / $perimeter / $length (metres, in a local UTM projection), $x / $y (centroid lon / lat), $id."""
    from rasterio.warp import transform_geom
    from shapely.geometry import mapping, shape
    n = len(geoms)
    out = {k: np.full(n, np.nan) for k in needed}
    if "id" in needed:
        out["id"] = np.arange(1, n + 1, dtype="float64")
    want = needed - {"id"}
    if not want:
        return out
    for i, g in enumerate(geoms):
        if not g:
            continue
        try:
            s = shape(g)
            c = s.centroid
            if {"x", "y"} & want:
                if "x" in want:
                    out["x"][i] = c.x
                if "y" in want:
                    out["y"][i] = c.y
            if {"area", "area_ha", "area_km2", "perimeter", "length"} & want:
                zone = int((c.x + 180) // 6) + 1
                epsg = (32600 if c.y >= 0 else 32700) + min(max(zone, 1), 60)
                p = shape(transform_geom("EPSG:4326", f"EPSG:{epsg}", mapping(s)))
                a = p.area if p.geom_type.endswith("Polygon") else 0.0
                for k, v in (("area", a), ("area_ha", a / 1e4), ("area_km2", a / 1e6),
                             ("perimeter", p.boundary.length if p.geom_type.endswith("Polygon") else 0.0),
                             ("length", p.length if "Line" in p.geom_type else (p.boundary.length if p.geom_type.endswith("Polygon") else 0.0))):
                    if k in want:
                        out[k][i] = v
        except Exception:
            continue
    return out
