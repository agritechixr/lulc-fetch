"""Conversion tools: raster → polygons, polylines (class boundaries or centrelines) and points; vector → raster
(rasterize: a field's values, classes, presence or a count per cell); and feature conversions (polygons → lines,
lines → polygons, vertices → points, points → lines, points along lines, lines → segments, bounding boxes).
Vector results are GeoJSON in EPSG:4326; lengths and areas are measured in metres (UTM)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
import shapely
from rasterio import features
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape

from . import progress
from .geoprocess import _fc, _geoms, _to_metric, _utm

MAX_PIXELS = 40_000_000
MAX_FEATURES = 250_000
PALETTE = ["#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#d73027", "#4575b4", "#91bfdb", "#a6611a", "#7b3294", "#636363", "#e7298a"]


def class_names(src, band: int = 1) -> dict:
    """{value: name} from a raster's "classes" tag (the app's class maps, WorldCover, Esri land cover)."""
    try:
        return {int(k): v for k, v in json.loads(src.tags().get("classes") or "{}").items()}
    except (ValueError, AttributeError):
        return {}


def _metric_crs(src) -> str:
    """A CRS in metres for a raster: its own if projected, else the UTM zone of its centre."""
    if src.crs and not src.crs.is_geographic:
        return src.crs.to_string()
    lon, lat = (src.bounds.left + src.bounds.right) / 2, (src.bounds.bottom + src.bounds.top) / 2
    if src.crs and src.crs.to_epsg() != 4326:
        (lon,), (lat,) = warp_transform(src.crs, "EPSG:4326", [lon], [lat])
    return f"EPSG:{(32600 if lat >= 0 else 32700) + min(max(int((lon + 180) // 6) + 1, 1), 60)}"


def _reproject_all(geoms: list, src, dst) -> list:
    """Many shapely geometries from one CRS to another (in batches: fast)."""
    if not geoms or str(src) == str(dst):
        return list(geoms)
    out = []
    for i in range(0, len(geoms), 5000):
        out += [shape(g) for g in transform_geom(src, dst, [mapping(g) for g in geoms[i:i + 5000]])]
    return out


def _read_band(src, band: int):
    if not 1 <= band <= src.count:
        raise ValueError(f"The raster has {src.count} band(s); there is no band {band}")
    if src.width * src.height > MAX_PIXELS:
        raise ValueError(f"The raster is too big for this ({src.width:,} × {src.height:,} pixels) → clip it to your area first")
    return src.read(band, masked=True)


def _class_array(a, values):
    """The band as whole-number classes (continuous data has to be reclassified first) and the mask of what to keep."""
    valid = ~np.ma.getmaskarray(a)
    data = np.ma.getdata(a)
    if not np.issubdtype(data.dtype, np.integer):
        valid &= np.isfinite(data)
        u = np.unique(data[valid])
        if u.size > 1000 or not np.allclose(u, np.round(u)):
            raise ValueError("The raster has continuous values (e.g. NDVI, heights) → turn them into classes first with "
                             "Reclassify, or use Contours for lines of equal height")
        data = np.where(valid, data, 0)
    data = data.astype("int32")
    if values:
        valid &= np.isin(data, list(values))
    return data, valid


# ------------------------------------------------------------------ raster → polygons
def raster_to_polygons(path, *, band: int = 1, values: list[int] | None = None, min_area: float = 0, simplify: float = 0,
                       dissolve: bool = False, diagonal: bool = False) -> dict:
    """Areas of equal value (classes) as polygons with their value, class name and area. min_area (m²): patches smaller
    than this are merged into their neighbour first (sieve); simplify (m): smoother outlines; dissolve: one
    (multi)polygon per value; diagonal: pixels touching at a corner join."""
    with rasterio.open(path) as src:
        a = _read_band(src, band)
        data, keep = _class_array(a, values)
        names = class_names(src, band)
        mcrs = _metric_crs(src)
        px_m2 = abs(src.transform.a * src.transform.e) if not src.crs.is_geographic else (abs(src.transform.a) * 111320 * math.cos(math.radians((src.bounds.top + src.bounds.bottom) / 2)) * abs(src.transform.e) * 110574)
        if min_area and min_area > px_m2:
            progress.update(0.1, "Merging small patches into their neighbours")
            size = int(math.ceil(min_area / px_m2))
            data = features.sieve(data, size=size, mask=keep, connectivity=8 if diagonal else 4)
            if values:
                keep &= np.isin(data, list(values))
        progress.update(0.25, "Tracing the outlines")
        vals, geoms = [], []
        for g, v in features.shapes(data, mask=keep, transform=src.transform, connectivity=8 if diagonal else 4):
            vals.append(int(v)); geoms.append(shape(g))
            if len(geoms) > MAX_FEATURES * 4:
                raise ValueError("Too many separate patches → set a minimum area, or reclassify into fewer classes")
        if not geoms:
            raise ValueError("No pixels with data (or with the chosen values)")
        gm = _reproject_all(geoms, src.crs, mcrs)
    progress.update(0.6, f"{len(gm):,} polygons")
    if dissolve:
        by = {}
        for v, g in zip(vals, gm):
            by.setdefault(v, []).append(g)
        vals, gm = list(by), [shapely.union_all(gs) for gs in by.values()]
    if simplify and simplify > 0:
        gm = [g.simplify(simplify, preserve_topology=True) for g in gm]
    if len(gm) > MAX_FEATURES:
        raise ValueError(f"{len(gm):,} polygons → set a minimum area, or dissolve by value")
    out = _reproject_all(gm, mcrs, "EPSG:4326")
    items = []
    for i, (v, g, g4) in enumerate(zip(vals, gm, out)):
        p = {"id": i + 1, "value": v, **({"class": names[v]} if v in names else {}), "area_ha": round(g.area / 1e4, 4)}
        if dissolve:
            p["parts"] = len(getattr(g, "geoms", [g]))
        items.append((p, g4))
    progress.update(1, "Done")
    return _fc(items)


# ------------------------------------------------------------------ raster → polylines
def _thin(img: np.ndarray) -> np.ndarray:
    """Zhang–Suen thinning: a mask reduced to lines one pixel wide (numpy, whole-array steps)."""
    img = img.astype(np.uint8).copy()
    for it in range(10_000):
        changed = False
        for step in (0, 1):
            P = np.pad(img, 1)
            p2, p3, p4, p5 = P[:-2, 1:-1], P[:-2, 2:], P[1:-1, 2:], P[2:, 2:]
            p6, p7, p8, p9 = P[2:, 1:-1], P[2:, :-2], P[1:-1, :-2], P[:-2, :-2]
            nb = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            B = sum(n.astype(np.int16) for n in nb[:8])
            A = sum(((nb[i] == 0) & (nb[i + 1] == 1)).astype(np.int16) for i in range(8))
            c = ((p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)) if step == 0 else ((p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0))
            m = (img == 1) & (B >= 2) & (B <= 6) & (A == 1) & c
            if m.any():
                img[m] = 0
                changed = True
        if it % 5 == 0:
            progress.update(min(0.5, 0.1 + it / 200), "Thinning to centrelines")
        if not changed:
            return img
    return img


def _skeleton_lines(sk: np.ndarray, transform) -> list:
    """The pixels of a one-pixel-wide skeleton joined to their neighbours, merged into lines (map units)."""
    r, c = np.nonzero(sk)
    if not r.size:
        return []
    P = np.pad(sk.astype(bool), 1)
    segs = []
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        nb = P[1 + dr:1 + dr + sk.shape[0], 1 + dc:1 + dc + sk.shape[1]] if dc >= 0 else P[1 + dr:1 + dr + sk.shape[0], 0:sk.shape[1]]
        ok = sk.astype(bool) & nb
        if dr == 1 and dc != 0:   # a diagonal only where no corner pixel already joins the two
            side = P[1:1 + sk.shape[0], 1 + dc:1 + dc + sk.shape[1]] if dc > 0 else P[1:1 + sk.shape[0], 0:sk.shape[1]]
            down = P[2:2 + sk.shape[0], 1:1 + sk.shape[1]]
            ok &= ~side & ~down
        rr, cc = np.nonzero(ok)
        segs += [((cc[i], rr[i]), (cc[i] + dc, rr[i] + dr)) for i in range(rr.size)]
    to_xy = lambda col, row: transform * (col + 0.5, row + 0.5)
    lines = shapely.MultiLineString([[to_xy(*a), to_xy(*b)] for a, b in segs]) if segs else None
    if lines is None:
        return []
    merged = shapely.line_merge(lines)
    return list(getattr(merged, "geoms", [merged]))


def raster_to_polylines(path, *, mode: str = "boundaries", band: int = 1, values: list[int] | None = None, simplify: float = 0,
                        min_length: float = 0) -> dict:
    """Lines from a raster. mode 'boundaries': the edges between classes (each edge once); mode 'centrelines': the
    middle lines of thin shapes (roads, rivers, field borders) in a mask: pixels with the chosen values, or any
    non-zero value. simplify (m): fewer vertices; min_length (m): shorter pieces are dropped."""
    if mode not in ("boundaries", "centrelines"):
        raise ValueError("mode: boundaries or centrelines")
    with rasterio.open(path) as src:
        a = _read_band(src, band)
        data, keep = _class_array(a, values)
        mcrs = _metric_crs(src)
        if mode == "boundaries":
            progress.update(0.2, "Tracing the class edges")
            polys = [shape(g) for g, _ in features.shapes(data, mask=keep, transform=src.transform)]
            if not polys:
                raise ValueError("No pixels with data (or with the chosen values)")
            if len(polys) > MAX_FEATURES:
                raise ValueError("Too many patches → reclassify into fewer classes, or clip the raster first")
            progress.update(0.5, f"Joining the edges of {len(polys):,} patches")
            edges = shapely.union_all([p.boundary for p in polys])   # shared edges become one
            merged = shapely.line_merge(edges)
            lines = list(getattr(merged, "geoms", [merged]))
        else:
            mask = keep & ((data != 0) if not values else True)
            if not mask.any():
                raise ValueError("The mask is empty: choose the values of the lines (e.g. the road class)")
            lines = _skeleton_lines(_thin(mask), src.transform)
        lines_m = _reproject_all(lines, src.crs, mcrs)
    progress.update(0.8, f"{len(lines_m):,} lines")
    if simplify and simplify > 0:
        lines_m = [g.simplify(simplify, preserve_topology=False) for g in lines_m]
    keep_i = [i for i, g in enumerate(lines_m) if not g.is_empty and g.length >= (min_length or 0)]
    if len(keep_i) > MAX_FEATURES:
        raise ValueError(f"{len(keep_i):,} lines → set a minimum length, or simplify")
    out = _reproject_all([lines_m[i] for i in keep_i], mcrs, "EPSG:4326")
    progress.update(1, "Done")
    return _fc([({"id": n + 1, "length_m": round(lines_m[i].length, 2)}, g) for n, (i, g) in enumerate(zip(keep_i, out))])


# ------------------------------------------------------------------ raster → points
def raster_to_points(path, *, bands: list[int] | None = None, step: int = 1, max_points: int = MAX_FEATURES) -> dict:
    """A point at the centre of each pixel with data (every `step`-th pixel each way), with the value of each band
    (named by the band descriptions) and the class name of class maps."""
    with rasterio.open(path) as src:
        bl = bands or list(range(1, min(src.count, 50) + 1))
        if any(not 1 <= b <= src.count for b in bl):
            raise ValueError(f"The raster has {src.count} bands")
        step = max(1, int(step))
        if src.width * src.height > MAX_PIXELS * 4:
            raise ValueError("The raster is too big → clip it first")
        arrs = [src.read(b, masked=True)[::step, ::step] for b in bl]
        valid = ~np.ma.getmaskarray(arrs[0])
        for a in arrs:
            d = np.ma.getdata(a)
            if np.issubdtype(d.dtype, np.floating):
                valid &= np.isfinite(d)
        rows, cols = np.nonzero(valid)
        if rows.size > max_points:
            raise ValueError(f"That makes {rows.size:,} points (at most {max_points:,}) → take every {math.ceil(math.sqrt(rows.size / max_points)) * step}th pixel, or clip the raster")
        if not rows.size:
            raise ValueError("No pixels with data")
        xs, ys = rasterio.transform.xy(src.transform, rows * step, cols * step, offset="center")
        xs, ys = np.asarray(xs, float), np.asarray(ys, float)
        if src.crs and src.crs.to_epsg() != 4326:
            xs, ys = warp_transform(src.crs, "EPSG:4326", xs.tolist(), ys.tolist())
        desc = [src.descriptions[b - 1] or f"band_{b}" for b in bl]
        names = class_names(src)
        vals = [np.ma.getdata(a)[rows, cols] for a in arrs]
    feats = []
    for i in range(rows.size):
        p = {"id": i + 1}
        for d, v in zip(desc, vals):
            x = v[i].item()
            p[d] = round(x, 6) if isinstance(x, float) else x
        if names and len(bl) == 1 and int(vals[0][i]) in names:
            p["class"] = names[int(vals[0][i])]
        feats.append({"type": "Feature", "properties": p, "geometry": {"type": "Point", "coordinates": [round(xs[i], 8), round(ys[i], 8)]}})
        if i % 20000 == 0:
            progress.update(i / rows.size, f"{i:,} of {rows.size:,} points")
    return {"type": "FeatureCollection", "features": feats}


# ------------------------------------------------------------------ vector → raster
def rasterize(fc: dict, out: Path, *, field: str | None = None, mode: str = "value", res: float | None = None,
              like: str | Path | None = None, all_touched: bool = False) -> dict:
    """A layer burnt into a raster. mode 'value': a field's numbers (or, for text, one class per distinct text, with
    names and colours); 'presence': 1 where there is a shape; 'count': how many shapes (e.g. points) fall in each
    cell. The grid: like a raster (same CRS, pixels and extent), or `res` metres over the layer in its UTM zone."""
    if mode not in ("value", "presence", "count"):
        raise ValueError("mode: value, presence or count")
    items = _geoms(fc)
    if not items:
        raise ValueError("The layer has no shapes")
    if like:
        with rasterio.open(like) as r:
            crs, transform, w, h = r.crs, r.transform, r.width, r.height
    else:
        if not res or res <= 0:
            raise ValueError("Give the pixel size in metres, or a raster to match")
        crs = _utm(items)
        gm = _reproject_all([g for _, g in items], "EPSG:4326", crs)
        x0, y0, x1, y1 = shapely.union_all(gm).bounds if len(gm) < 5000 else (min(g.bounds[0] for g in gm), min(g.bounds[1] for g in gm), max(g.bounds[2] for g in gm), max(g.bounds[3] for g in gm))
        x0, y0 = math.floor(x0 / res) * res, math.floor(y0 / res) * res
        x1, y1 = math.ceil(x1 / res) * res, math.ceil(y1 / res) * res
        w, h = max(1, int(round((x1 - x0) / res))), max(1, int(round((y1 - y0) / res)))
        transform = rasterio.transform.from_origin(x0, y1, res, res)
    if w * h > MAX_PIXELS:
        raise ValueError(f"The raster would be {w:,} × {h:,} pixels → choose a bigger pixel size")
    geoms = _reproject_all([g for _, g in items], "EPSG:4326", crs)
    progress.update(0.3, f"Burning {len(geoms):,} shapes into {w:,} × {h:,} pixels")
    cmap = names = None
    if mode == "count":
        a = features.rasterize(((g, 1) for g in geoms), out_shape=(h, w), transform=transform, fill=0, all_touched=all_touched,
                               merge_alg=features.MergeAlg.add, dtype="int32")
        dtype, nodata = "int32", None
    elif mode == "presence" or not field:
        a = features.rasterize(((g, 1) for g in geoms), out_shape=(h, w), transform=transform, fill=0, all_touched=all_touched, dtype="uint8")
        dtype, nodata = "uint8", 0
        cmap = {0: (0, 0, 0, 0), 1: (37, 99, 235, 255)}
    else:
        raw = [p.get(field) for p, _ in items]
        if all(v is None for v in raw):
            raise ValueError(f"No feature has a value in {field!r}")
        numeric = all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for v in raw)
        if numeric:
            pairs = [(g, float(v)) for g, v in zip(geoms, raw) if v is not None and math.isfinite(float(v))]
            a = features.rasterize(pairs, out_shape=(h, w), transform=transform, fill=-9999.0, all_touched=all_touched, dtype="float32")
            dtype, nodata = "float32", -9999.0
        else:
            labels = sorted({str(v) for v in raw if v is not None and str(v) != ""})
            if len(labels) > 255:
                raise ValueError(f"{field!r} has {len(labels)} different texts (at most 255 classes)")
            code = {t: i + 1 for i, t in enumerate(labels)}
            pairs = [(g, code[str(v)]) for g, v in zip(geoms, raw) if v is not None and str(v) in code]
            a = features.rasterize(pairs, out_shape=(h, w), transform=transform, fill=0, all_touched=all_touched, dtype="uint8")
            dtype, nodata = "uint8", 0
            names = {c: t for t, c in code.items()}
            cmap = {0: (0, 0, 0, 0), **{c: tuple(int(PALETTE[(c - 1) % len(PALETTE)][k:k + 2], 16) for k in (1, 3, 5)) + (255,) for c in names}}
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", width=w, height=h, count=1, dtype=dtype, crs=crs, transform=transform, nodata=nodata,
                       compress="deflate", tiled=True, blockxsize=256, blockysize=256) as d:
        d.write(a, 1)
        d.set_band_description(1, field if (field and mode == "value") else mode)
        if cmap:
            d.write_colormap(1, cmap)
        if names:
            d.update_tags(classes=json.dumps(names))
    progress.update(1, "Done")
    filled = int((a != (nodata if nodata is not None else 0)).sum()) if mode != "count" else int((a > 0).sum())
    return {"path": str(out), "size": [w, h], "crs": str(crs), "pixels_with_data": filled, **({"classes": names} if names else {})}


# ------------------------------------------------------------------ feature conversions
def polygons_to_lines(fc: dict) -> dict:
    """Each polygon's outline (outer and inner rings) as a line, with the polygon's attributes."""
    out = [(p, g.boundary) for p, g in _geoms(fc) if g.geom_type in ("Polygon", "MultiPolygon")]
    if not out:
        raise ValueError("The layer has no polygons")
    return _fc(out)


def lines_to_polygons(fc: dict) -> dict:
    """The areas enclosed by lines (closed lines, or lines that cross to close an area) as polygons."""
    lines = [g for _, g in _geoms(fc) if "Line" in g.geom_type or "Polygon" in g.geom_type]
    if not lines:
        raise ValueError("The layer has no lines")
    noded = shapely.union_all([g.boundary if "Polygon" in g.geom_type else g for g in lines])   # cut where lines cross
    polys = list(shapely.polygonize(list(getattr(noded, "geoms", [noded]))).geoms)
    if not polys:
        raise ValueError("The lines don't enclose any area (they must be closed, or cross each other)")
    metric, _ = _to_metric([({}, p) for p in polys])
    return _fc([({"id": i + 1, "area_ha": round(gm.area / 1e4, 4)}, p) for i, (p, (_, gm)) in enumerate(zip(polys, metric))])


def _coords(g):
    if g.geom_type == "Point":
        return [g.coords[0]]
    if hasattr(g, "geoms"):
        return [c for part in g.geoms for c in _coords(part)]
    if g.geom_type == "Polygon":
        return [c for ring in [g.exterior, *g.interiors] for c in ring.coords[:-1]]
    return list(g.coords)


def vertices_to_points(fc: dict) -> dict:
    """Every vertex of the shapes as a point, with its shape's attributes and its number along the shape."""
    out = []
    for p, g in _geoms(fc):
        for i, c in enumerate(_coords(g)):
            out.append(({**p, "vertex": i + 1}, shapely.Point(c[:2])))
            if len(out) > MAX_FEATURES:
                raise ValueError(f"More than {MAX_FEATURES:,} vertices → simplify the layer first")
    return _fc(out)


def points_to_lines(fc: dict, *, group_by: str | None = None, order_by: str | None = None, close: bool = False) -> dict:
    """Points joined into lines in the order of a field (e.g. time) or of the layer, one line per value of group_by
    (e.g. one track per vehicle); close: back to the first point (closed rings become polygons' outlines)."""
    pts = [(p, g) for p, g in _geoms(fc) if g.geom_type == "Point"]
    if len(pts) < 2:
        raise ValueError("Points to lines needs two or more points")
    groups = {}
    for i, (p, g) in enumerate(pts):
        groups.setdefault(p.get(group_by) if group_by else None, []).append((p.get(order_by) if order_by else i, i, g))
    out = []
    for key, rows in groups.items():
        rows.sort(key=lambda r: (r[0] is None, r[0] if not isinstance(r[0], (dict, list)) else str(r[0]), r[1]))
        cs = [r[2].coords[0] for r in rows]
        if len(cs) < 2:
            continue
        if close and len(cs) > 2:
            cs.append(cs[0])
        line = shapely.LineString(cs)
        out.append(({**({group_by: key} if group_by else {}), "points": len(rows)}, line))
    metric, _ = _to_metric(out)
    return _fc([({**p, "length_m": round(gm.length, 2)}, g) for (p, g), (_, gm) in zip(out, metric)])


def points_along_lines(fc: dict, distance: float, *, ends: bool = True) -> dict:
    """A point every `distance` metres along each line (or polygon outline), with the line's attributes and the
    distance from its start."""
    if not distance or distance <= 0:
        raise ValueError("Give the distance between points in metres")
    items = [(p, g.boundary if "Polygon" in g.geom_type else g) for p, g in _geoms(fc) if g.geom_type != "Point" and g.geom_type != "MultiPoint"]
    if not items:
        raise ValueError("The layer has no lines or polygons")
    metric, crs = _to_metric(items)
    out = []
    for (p, _), (_, gm) in zip(items, metric):
        for part in getattr(gm, "geoms", [gm]):
            L = part.length
            ds = list(np.arange(0, L, distance)) + ([L] if ends and L % distance > 1e-9 else [])
            if len(out) + len(ds) > MAX_FEATURES:
                raise ValueError(f"More than {MAX_FEATURES:,} points → use a longer distance")
            out += [({**p, "distance_m": round(float(d), 2)}, part.interpolate(d)) for d in ds]
    pts = _reproject_all([g for _, g in out], crs, "EPSG:4326")
    return _fc([(p, g) for (p, _), g in zip(out, pts)])


def split_lines(fc: dict) -> dict:
    """Each line (or polygon outline) cut into its straight segments, with the length of each."""
    out = []
    for p, g in _geoms(fc):
        g = g.boundary if "Polygon" in g.geom_type else g
        for part in getattr(g, "geoms", [g]):
            if part.geom_type != "LineString":
                continue
            cs = list(part.coords)
            out += [({**p, "segment": i + 1}, shapely.LineString([cs[i], cs[i + 1]])) for i in range(len(cs) - 1)]
            if len(out) > MAX_FEATURES:
                raise ValueError(f"More than {MAX_FEATURES:,} segments → simplify the layer first")
    if not out:
        raise ValueError("The layer has no lines or polygons")
    metric, _ = _to_metric(out)
    return _fc([({**p, "length_m": round(gm.length, 2)}, g) for (p, g), (_, gm) in zip(out, metric)])


def bounding_boxes(fc: dict, whole: bool = False) -> dict:
    """The rectangle (north-up) around each shape, or around the whole layer."""
    items = _geoms(fc)
    if not items:
        raise ValueError("The layer has no shapes")
    if whole:
        return _fc([({"features": len(items)}, shapely.box(*shapely.union_all([g for _, g in items]).bounds))])
    return _fc([(p, shapely.box(*g.bounds)) for p, g in items])


FEATURE_OPS = ("polygons_to_lines", "lines_to_polygons", "vertices_to_points", "points_to_lines", "points_along_lines", "split_lines", "bounding_boxes")
