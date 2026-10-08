"""Coordinate systems: find one, guess one, assign one, convert between them, and place data with control points.

    search(q, near=(lon, lat))       EPSG coordinate systems by name or code (PROJ's own database, shipped with rasterio),
                                     those covering `near` first
    describe(text)                   any EPSG code, WKT or PROJ string → its name, units, EPSG code
    suggest(bounds, near)            likely systems for coordinates whose system is unknown (degrees → WGS 84; metres
                                     that look like UTM → the UTM zone of `near`; India's LCC grid …)
    transform_fc(fc, src, dst)       a GeoJSON layer's coordinates from one system to another
    assign_raster(path, crs, out)    a copy of a raster with its coordinate system set (its pixel grid unchanged)
    control_point_fc(fc, points)     a vector layer placed by control points ({x, y} in its own coordinates ↔ lon, lat):
                                     affine, 2nd-order polynomial or thin-plate spline, as Georeference does for pictures

"Assign" corrects what the numbers mean (the data was read in the wrong system); "convert" (reproject) changes the
numbers to another system and keeps the places where they are.
"""

from __future__ import annotations

import math
import shutil
import sqlite3
from functools import lru_cache
from pathlib import Path

import numpy as np
from rasterio.crs import CRS
from rasterio.warp import transform_geom

WGS84 = "EPSG:4326"
TYPES = ("geographic 2D", "projected")


@lru_cache(maxsize=1)
def _db() -> Path:
    import rasterio
    for p in (Path(rasterio.__file__).parent / "proj_data" / "proj.db",):
        if p.is_file():
            return p
    from rasterio.env import PROJDataFinder   # a system PROJ (source installs)
    for d in (PROJDataFinder().search() or "").split(":"):
        if d and (Path(d) / "proj.db").is_file():
            return Path(d) / "proj.db"
    raise RuntimeError("PROJ's database (proj.db) wasn't found")


def _con():
    return sqlite3.connect(f"file:{_db().as_posix()}?mode=ro", uri=True)


def _extent_sql():
    return ("SELECT e.name, e.west_lon, e.south_lat, e.east_lon, e.north_lat FROM usage u JOIN extent e ON e.auth_name = u.extent_auth_name "
            "AND e.code = u.extent_code WHERE u.object_auth_name = 'EPSG' AND u.object_code = ? AND u.object_table_name = ? LIMIT 1")


def _covers(ext, lon, lat) -> bool:
    if not ext or lon is None:
        return False
    _, w, s, e, n = ext
    inside_lon = w <= lon <= e if w <= e else (lon >= w or lon <= e)   # extents across the antimeridian
    return inside_lon and s <= lat <= n


def search(q: str = "", near: tuple[float, float] | None = None, limit: int = 40) -> list[dict]:
    """EPSG geographic and projected systems matching every word of q (or the code), those covering `near` first."""
    q = (q or "").strip()
    words = [w for w in q.replace("/", " ").replace(",", " ").split() if w]
    code = q.upper().removeprefix("EPSG:").strip()
    with _con() as con:
        sql = ("SELECT code, name, type, table_name FROM crs_view WHERE auth_name = 'EPSG' AND deprecated = 0 AND type IN (?, ?)")
        args: list = list(TYPES)
        if code.isdigit():
            sql += " AND code = ?"
            args.append(code)
        else:
            for w in words:
                sql += " AND name LIKE ?"
                args.append(f"%{w}%")
        rows = con.execute(sql + " LIMIT 3000", args).fetchall()
        out = []
        for c, name, typ, table in rows:
            ext = con.execute(_extent_sql(), (c, table)).fetchone()
            out.append({"crs": f"EPSG:{c}", "code": int(c), "name": name, "kind": "degrees" if typ.startswith("geographic") else "projected",
                        "area": ext[0] if ext else "", "covers": _covers(ext, *(near or (None, None)))})
    common = {4326: 0, 3857: 1, 7755: 2}
    out.sort(key=lambda r: (not r["covers"] if near else False, common.get(r["code"], 9), not r["name"].startswith("WGS 84"),
                            "UTM" not in r["name"], len(r["name"]), r["code"]))
    return out[:limit]


def describe(text: str) -> dict:
    """Any EPSG code, WKT or PROJ string, checked: its name, units and EPSG code (when it has one)."""
    try:
        crs = CRS.from_user_input(text.strip())
    except Exception as e:
        raise ValueError(f"Not a coordinate system: {str(e)[:200]}") from None
    epsg = crs.to_epsg()
    wkt = crs.to_wkt()
    name = wkt.split('"')[1] if '"' in wkt else crs.to_string()
    units = "degrees" if crs.is_geographic else (crs.linear_units or "metre")
    return {"crs": f"EPSG:{epsg}" if epsg else crs.to_wkt(), "epsg": epsg, "name": name, "units": units, "geographic": bool(crs.is_geographic)}


def utm_zone(lon: float, lat: float) -> str:
    return f"EPSG:{(32600 if lat >= 0 else 32700) + min(max(int((lon + 180) // 6) + 1, 1), 60)}"


def suggest(bounds: list[float] | None, near: tuple[float, float] | None = None) -> list[dict]:
    """Likely systems for coordinates [minx, miny, maxx, maxy] in an unknown system, best first, each with why."""
    out: list[dict] = []
    add = lambda crs, why: out.append({**describe(crs), "why": why}) if not any(o["crs"] == crs for o in out) else None   # noqa: E731
    if bounds:
        x0, y0, x1, y1 = bounds
        if -180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90:
            add(WGS84, "the numbers look like longitude / latitude in degrees")
        elif 100_000 <= x0 <= x1 <= 900_000 and -10_000_000 <= y0 <= y1 <= 10_000_000:
            if near:
                add(utm_zone(*near), "the numbers look like UTM metres; this is the zone where the map is")
                zone = int(utm_zone(*near)[-2:])
                for z in (zone - 1, zone + 1):   # the neighbouring zones, in case the map is just across a zone edge
                    if 1 <= z <= 60:
                        add(f"EPSG:{(32600 if near[1] >= 0 else 32700) + z}", "a neighbouring UTM zone")
            else:
                add("EPSG:32643", "the numbers look like UTM metres (zone 43N shown: choose yours)")
        elif 2_000_000 <= x0 <= x1 <= 6_500_000 and 0 <= y0 <= y1 <= 5_000_000:
            add("EPSG:7755", "the numbers look like the India-wide grid (WGS 84 / India NSF LCC, metres)")
        elif abs(x0) <= 20_037_509 and abs(y1) <= 20_048_967:
            add("EPSG:3857", "metres that fit Web Mercator (as web maps use)")
    add(WGS84, "the usual default: longitude / latitude (GPS, Google Maps)")
    if near:
        add(utm_zone(*near), "metres in the UTM zone where the map is")
    return out


def raw_bounds(fc: dict) -> list[float] | None:
    xs, ys = [], []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        elif isinstance(c, (list, tuple)):
            for k in c:
                walk(k)
    for f in fc.get("features", []):
        g = f.get("geometry") or {}
        walk(g.get("coordinates") or [g2.get("coordinates") for g2 in g.get("geometries", [])])
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def transform_fc(fc: dict, src: str, dst: str = WGS84) -> dict:
    """The layer's coordinates from system src to dst (features without a geometry are kept as they are)."""
    s, d = CRS.from_user_input(src), CRS.from_user_input(dst)
    out = fc if s == d else {**fc, "features": [{**f, "geometry": transform_geom(s, d, f["geometry"]) if f.get("geometry") else f.get("geometry")}
                                                for f in fc.get("features", [])]}
    if d.to_epsg() == 4326:
        b = raw_bounds(out)
        if b and not (-180.5 <= b[0] <= b[2] <= 180.5 and -90.5 <= b[1] <= b[3] <= 90.5):
            raise ValueError("In this coordinate system the data falls outside the Earth: it isn't the right one")
    return out


def assign_raster(path, crs: str, out) -> Path:
    """A copy of the raster that says it is in `crs` (the pixels and their grid don't change)."""
    import rasterio
    c = CRS.from_user_input(crs)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path) as src:
        if src.transform.is_identity and not src.gcps[0]:
            raise ValueError("The raster has no pixel grid (no world file or geotransform): place it with control points instead")
        if src.driver == "GTiff":
            shutil.copyfile(path, out)
        else:
            prof = src.profile.copy()
            prof.update(driver="GTiff", compress="deflate")
            with rasterio.open(out, "w", **prof) as d:
                d.write(src.read())
    with rasterio.open(out, "r+") as d:
        if d.gcps[0] and d.transform.is_identity:
            d.gcps = (d.gcps[0], c)
        else:
            d.crs = c
    with rasterio.open(out) as d:   # check it lands on the Earth
        from rasterio.warp import transform_bounds
        try:
            w, s, e, n = transform_bounds(d.crs, WGS84, *d.bounds)
        except Exception:
            w = s = e = n = float("nan")
        if not (-181 <= w <= e <= 181 and -91 <= s <= n <= 91):
            out.unlink(missing_ok=True)
            raise ValueError("In this coordinate system the raster falls outside the Earth: it isn't the right one")
    return out


# ------------------------------------------------------------------ vector layers placed by control points
def control_point_fc(fc: dict, points: list[dict], method: str = "affine") -> dict:
    """Place a layer with control points: each {x, y, lon, lat} pairs a place in the layer's own coordinates (any units,
    any system, even a drawing's) with the same place on the map. The fit is made in the points' UTM zone."""
    from rasterio.warp import transform as warp_transform

    from .georef import METHODS, _terms, utm_of
    if method not in METHODS:
        raise ValueError("Method: affine, poly2 or tps")
    pts = [p for p in points if all(k in p for k in ("x", "y", "lon", "lat"))]
    if len(pts) < METHODS[method]:
        raise ValueError(f"This method needs at least {METHODS[method]} control points (you have {len(pts)})")
    utm = utm_of(pts)
    ex, ey = warp_transform(WGS84, utm, [p["lon"] for p in pts], [p["lat"] for p in pts])
    ex, ey = np.array(ex), np.array(ey)
    sx, sy = np.array([p["x"] for p in pts], float), np.array([p["y"] for p in pts], float)
    if len({(round(x, 9), round(y, 9)) for x, y in zip(sx, sy)}) < len(pts):
        raise ValueError("Two control points are at the same place on the layer: remove one")
    cx0, cy0 = sx.mean(), sy.mean()   # centred, for a well-conditioned fit
    span = max(np.ptp(sx), np.ptp(sy), 1e-9)
    u, v = (sx - cx0) / span, (sy - cy0) / span
    if method == "tps":
        from scipy.interpolate import RBFInterpolator
        fx = RBFInterpolator(np.column_stack([u, v]), ex, kernel="thin_plate_spline")
        fy = RBFInterpolator(np.column_stack([u, v]), ey, kernel="thin_plate_spline")
        to_map = lambda a: (fx(a), fy(a))   # noqa: E731
        res = np.zeros(len(pts))
    else:
        order = 1 if method == "affine" else 2
        A = _terms(u, v, order)
        kx, *_ = np.linalg.lstsq(A, ex, rcond=None)
        ky, *_ = np.linalg.lstsq(A, ey, rcond=None)
        to_map = lambda a: (_terms(a[:, 0], a[:, 1], order) @ kx, _terms(a[:, 0], a[:, 1], order) @ ky)   # noqa: E731
        res = np.hypot(A @ kx - ex, A @ ky - ey)
        if method == "affine" and len(pts) == 3:
            res = np.zeros(3)

    def move_all(g):   # whole geometries at once: one transform call per geometry, not per vertex
        if g is None:
            return None
        if g["type"] == "GeometryCollection":
            return {**g, "geometries": [move_all(x) for x in g["geometries"]]}
        flat = []

        def collect(c):
            if isinstance(c[0], (int, float)):
                flat.append(c[:2])
            else:
                for k in c:
                    collect(k)
        collect(g["coordinates"])
        a = (np.array(flat, float) - [cx0, cy0]) / span
        mx, my = to_map(a)
        lon, lat = warp_transform(utm, WGS84, mx.tolist(), my.tolist())
        it = iter(zip(lon, lat))

        def rebuild(c):
            if isinstance(c[0], (int, float)):
                x, y = next(it)
                return [round(x, 8), round(y, 8)]
            return [rebuild(k) for k in c]
        return {**g, "coordinates": rebuild(g["coordinates"])}

    out = {**fc, "features": [{**f, "geometry": move_all(f.get("geometry"))} for f in fc.get("features", [])]}
    rmse = float(math.sqrt(float(np.mean(res ** 2))))
    return {"geojson": out, "rmse_m": round(rmse, 3), "residuals_m": [round(float(r), 3) for r in res], "points": len(pts), "method": method,
            "worst": int(np.argmax(res)) if len(pts) > METHODS[method] else None}
