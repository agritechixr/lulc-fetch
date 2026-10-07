"""Raster calculator: an expression over several layers, e.g. (B - A) / (B + A), where(A > 0.3, 1, 0), or
(NDBI_2025 - NDBI_2018) > 0.1. Each variable is a band of a raster; all are put on the first one's grid. The expression
is parsed safely (numbers, variables, arithmetic, comparisons, and / or / not, and a few functions); nothing else runs."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import reproject

from . import progress, resample

MAX_PIXELS = 60_000_000
FUNCS = {
    "where": np.where, "abs": np.abs, "sqrt": np.sqrt, "log": np.log, "log10": np.log10, "exp": np.exp,
    "min": np.minimum, "max": np.maximum, "clip": np.clip, "round": np.round, "floor": np.floor, "ceil": np.ceil,
    "isnan": np.isnan, "nan_to_num": np.nan_to_num, "sin": np.sin, "cos": np.cos, "tan": np.tan, "arctan2": np.arctan2,
}
CONSTS = {"nan": np.nan, "pi": np.pi, "e": np.e}
_BIN = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide, ast.Pow: np.power, ast.Mod: np.mod,
        ast.FloorDiv: np.floor_divide, ast.BitAnd: np.logical_and, ast.BitOr: np.logical_or}
_CMP = {ast.Gt: np.greater, ast.GtE: np.greater_equal, ast.Lt: np.less, ast.LtE: np.less_equal, ast.Eq: np.equal, ast.NotEq: np.not_equal}


def _prepare(expr: str) -> str:
    e = expr.strip()
    for a, b in ((" AND ", " and "), (" OR ", " or "), (" NOT ", " not "), ("^", "**")):
        e = e.replace(a, b)
    return e


def check(expr: str, names: list[str]) -> ast.Expression:
    """The expression parsed and checked: only known variables, functions, numbers and operators."""
    try:
        tree = ast.parse(_prepare(expr), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"The expression can't be read near “{(e.text or '').strip()[:40]}” (column {e.offset})")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Name, ast.Load, ast.Constant,
               ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, ast.IfExp, *_BIN, *_CMP)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError(f"“{type(node).__name__}” isn't allowed in an expression")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in FUNCS):
            raise ValueError(f"Unknown function; use one of: {', '.join(FUNCS)}")
        if isinstance(node, ast.Call) and node.keywords:
            raise ValueError("Functions take values only (no name=value)")
        if isinstance(node, ast.Name) and node.id not in names and node.id not in FUNCS and node.id not in CONSTS:
            raise ValueError(f"Unknown name “{node.id}”: the variables are {', '.join(names) or '(none)'}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("Only numbers can be written in an expression")
    return tree


def _eval(node, env):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env[node.id] if node.id in env else CONSTS[node.id]
    if isinstance(node, ast.BinOp):
        return _BIN[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        return np.logical_not(v) if isinstance(node.op, ast.Not) else (-v if isinstance(node.op, ast.USub) else v)
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        f = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
        out = vals[0]
        for v in vals[1:]:
            out = f(out, v)
        return out
    if isinstance(node, ast.Compare):
        left, out = _eval(node.left, env), None
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            r = _CMP[type(op)](left, right)
            out = r if out is None else np.logical_and(out, r)
            left = right
        return out
    if isinstance(node, ast.IfExp):
        return np.where(_eval(node.test, env), _eval(node.body, env), _eval(node.orelse, env))
    if isinstance(node, ast.Call):
        return FUNCS[node.func.id](*[_eval(a, env) for a in node.args])
    raise ValueError("Unsupported expression")


def calculate(variables: dict[str, dict], expr: str, out: Path, *, resampling: str | None = None, nodata: float = -9999.0) -> dict:
    """variables: {name: {"path": …, "band": 1}}; the first one's grid is the result's. Values without data in any
    variable are without data in the result. True / false results are written as 1 / 0 (8-bit)."""
    if not variables:
        raise ValueError("Add at least one layer as a variable")
    names = list(variables)
    for n in names:
        if not n.isidentifier() or n in FUNCS or n in CONSTS:
            raise ValueError(f"“{n}” can't be a variable name (letters, digits and _; not a function's name)")
    tree = check(expr, names)
    first = variables[names[0]]
    with rasterio.open(first["path"]) as ref:
        if ref.width * ref.height > MAX_PIXELS:
            raise ValueError("The first layer is too big for the calculator → clip it first")
        grid = dict(crs=ref.crs, transform=ref.transform, width=ref.width, height=ref.height)
        prof = ref.profile.copy()
    env, missing = {}, np.zeros((grid["height"], grid["width"]), bool)
    for i, n in enumerate(names):
        v = variables[n]
        with rasterio.open(v["path"]) as src:
            b = int(v.get("band") or 1)
            if not 1 <= b <= src.count:
                raise ValueError(f"{n}: the raster has {src.count} band(s); there is no band {b}")
            a = src.read(b, masked=True).astype("float64").filled(np.nan)
            same = src.crs == grid["crs"] and src.transform == grid["transform"] and (src.width, src.height) == (grid["width"], grid["height"])
            if not same:
                dst = np.full((grid["height"], grid["width"]), np.nan)
                reproject(a, dst, src_transform=src.transform, src_crs=src.crs, dst_transform=grid["transform"], dst_crs=grid["crs"],
                          src_nodata=np.nan, dst_nodata=np.nan, resampling=resample.get(resampling, resample.Resampling.bilinear))
                a = dst
            if v.get("scale"):
                a = a * float(v["scale"])
        env[n] = a
        missing |= ~np.isfinite(a)
        progress.update((i + 1) / (len(names) + 1), f"Read {n}")
    with np.errstate(all="ignore"):
        res = _eval(tree, env)
    res = np.broadcast_to(np.asarray(res), missing.shape)
    boolean = res.dtype == bool
    out.parent.mkdir(parents=True, exist_ok=True)
    prof.update(driver="GTiff", count=1, compress="deflate", tiled=True, blockxsize=256, blockysize=256, **grid)
    if boolean:
        prof.update(dtype="uint8", nodata=255)
        data = np.where(missing, 255, res.astype("uint8"))
    else:
        prof.update(dtype="float32", nodata=nodata)
        r = res.astype("float64")
        data = np.where(missing | ~np.isfinite(r), nodata, r).astype("float32")
    for k in ("photometric",):
        prof.pop(k, None)
    with rasterio.open(out, "w", **prof) as d:
        d.write(data, 1)
        d.set_band_description(1, expr[:200])
        d.update_tags(expression=expr, variables=", ".join(f"{n} = {Path(variables[n]['path']).name} band {variables[n].get('band') or 1}" for n in names))
        if boolean:
            d.write_colormap(1, {0: (230, 230, 230, 255), 1: (220, 38, 38, 255)})
            d.update_tags(classes='{"0": "false", "1": "true"}')
    valid = data[data != prof["nodata"]]
    summary = {"pixels": int(valid.size), "min": float(valid.min()) if valid.size else None, "max": float(valid.max()) if valid.size else None,
               "mean": float(valid.mean()) if valid.size else None}
    if boolean:
        summary["true_pct"] = round(100 * float((valid == 1).mean()), 3) if valid.size else None
    progress.update(1, "Done")
    return {"path": str(out), "boolean": boolean, "summary": summary}
