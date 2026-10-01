"""Writing vector layers: zipped ESRI Shapefile, KML and GeoJSON."""

from __future__ import annotations

import json
import re
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from rasterio.crs import CRS
from rasterio.enums import WktVersion
from shapely.geometry import mapping, shape
from shapely.geometry.polygon import orient

_FAMILIES = {
    "Polygon": "polygons", "MultiPolygon": "polygons",
    "LineString": "lines", "MultiLineString": "lines",
    "Point": "points", "MultiPoint": "multipoints",
}


def features_from_geojson(fc: dict) -> list[tuple]:
    """GeoJSON FeatureCollection / Feature / geometry -> [(shapely geometry, properties)]."""
    if fc.get("type") == "FeatureCollection":
        feats = fc.get("features", [])
    elif fc.get("type") == "Feature":
        feats = [fc]
    else:
        feats = [{"type": "Feature", "geometry": fc, "properties": {}}]
    out = []
    for f in feats:
        if not f.get("geometry"):
            continue
        g = shape(f["geometry"])
        if g.geom_type == "GeometryCollection":
            out += [(part, dict(f.get("properties") or {})) for part in g.geoms]
        elif not g.is_empty:
            out.append((g, dict(f.get("properties") or {})))
    if not out:
        raise ValueError("The layer has no features to export")
    return out


def clip_features(feats: list[tuple], clip: dict) -> list[tuple]:
    """Intersect features with a clip polygon (both EPSG:4326); features outside it are dropped."""
    area = shape(clip)
    out = []
    for g, p in feats:
        if not g.intersects(area):
            continue
        part = g.intersection(area)
        if not part.is_empty:
            out.append((part, p))
    if not out:
        raise ValueError("No features inside the selected area")
    return out


def _field_names(keys: list[str]) -> dict[str, str]:
    """Shapefile (dBase) field names: max 10 chars, letters/digits/underscore, unique."""
    out, used = {}, set()
    for k in keys:
        base = re.sub(r"[^A-Za-z0-9_]", "_", k)[:10] or "field"
        name, i = base, 1
        while name.upper() in used:
            suffix = str(i)
            name, i = base[:10 - len(suffix)] + suffix, i + 1
        used.add(name.upper())
        out[k] = name
    return out


def _field_type(values: list) -> tuple:
    vals = [v for v in values if v is not None]
    if vals and all(isinstance(v, bool) for v in vals):
        return "L", 1, 0
    if vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return "N", 18, 0
    if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "F", 19, 8
    width = max((len(_as_text(v).encode("utf-8")) for v in vals), default=1)  # dBase widths are bytes
    return "C", min(max(width, 1), 254), 0


def _as_text(v) -> str:
    return v if isinstance(v, str) else json.dumps(v) if isinstance(v, (dict, list)) else str(v)


def _write_family(base: Path, family: str, feats: list[tuple]):
    import shapefile  # pyshp

    shape_type = {"polygons": shapefile.POLYGON, "lines": shapefile.POLYLINE,
                  "points": shapefile.POINT, "multipoints": shapefile.MULTIPOINT}[family]
    keys = list(dict.fromkeys(k for _, p in feats for k in p))
    names = _field_names(keys)
    types = {k: _field_type([p.get(k) for _, p in feats]) for k in keys}
    with shapefile.Writer(str(base), shapeType=shape_type, encoding="utf-8") as w:
        for k in keys:
            w.field(names[k], *types[k])
        if not keys:
            w.field("id", "N", 10, 0)
        for i, (g, props) in enumerate(feats):
            if family == "polygons":
                parts = []
                for poly in getattr(g, "geoms", [g]):
                    poly = orient(poly, sign=-1.0)  # shapefile: outer rings clockwise, holes counter-clockwise
                    parts.append([list(c[:2]) for c in poly.exterior.coords])
                    parts += [[list(c[:2]) for c in ring.coords] for ring in poly.interiors]
                w.poly(parts)
            elif family == "lines":
                w.line([[list(c[:2]) for c in line.coords] for line in getattr(g, "geoms", [g])])
            elif family == "points":
                w.point(g.x, g.y)
            else:
                w.multipoint([[p.x, p.y] for p in g.geoms])
            if keys:
                row = []
                for k in keys:
                    v = props.get(k)
                    if v is not None and types[k][0] == "C":
                        v = _as_text(v).encode("utf-8")[:254].decode("utf-8", "ignore")
                    row.append(v)
                w.record(*row)
            else:
                w.record(i + 1)


def write_shapefile_zip(feats: list[tuple], crs, out_zip: str | Path, name: str = "layer") -> Path:
    """Write [(shapely geometry, properties)] as a zipped shapefile (.shp/.shx/.dbf/.prj/.cpg).

    A shapefile holds one geometry type, so mixed layers produce one shapefile per type in the zip.
    """
    crs = crs if isinstance(crs, CRS) else CRS.from_user_input(crs)
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "layer"
    groups: dict[str, list] = {}
    for g, p in feats:
        fam = _FAMILIES.get(g.geom_type)
        if fam:
            groups.setdefault(fam, []).append((g, p))
    if not groups:
        raise ValueError("No polygon, line or point features to export")
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    prj = crs.to_wkt(version=WktVersion.WKT1_ESRI)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for fam, items in groups.items():
            stem = name if len(groups) == 1 else f"{name}_{fam}"
            base = Path(tmp) / stem
            _write_family(base, fam, items)
            (Path(tmp) / f"{stem}.prj").write_text(prj)
            (Path(tmp) / f"{stem}.cpg").write_text("UTF-8")
            for ext in ("shp", "shx", "dbf", "prj", "cpg"):
                z.write(Path(tmp) / f"{stem}.{ext}", f"{stem}.{ext}")
    return out_zip


def _kml_coords(coords) -> str:
    return " ".join(f"{c[0]},{c[1]}" for c in coords)


def _kml_geom(g) -> str:
    t = g.geom_type
    if t == "Point":
        return f"<Point><coordinates>{g.x},{g.y}</coordinates></Point>"
    if t == "LineString":
        return f"<LineString><coordinates>{_kml_coords(g.coords)}</coordinates></LineString>"
    if t == "Polygon":
        inner = "".join(f"<innerBoundaryIs><LinearRing><coordinates>{_kml_coords(r.coords)}</coordinates>"
                        f"</LinearRing></innerBoundaryIs>" for r in g.interiors)
        return (f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{_kml_coords(g.exterior.coords)}"
                f"</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon>")
    return "<MultiGeometry>" + "".join(_kml_geom(p) for p in g.geoms) + "</MultiGeometry>"


def write_kml(feats: list[tuple], out: str | Path, name: str = "layer") -> Path:
    """Write WGS84 features as KML (Google Earth)."""
    marks = []
    for i, (g, props) in enumerate(feats, start=1):
        label = props.get("name") or props.get("label") or f"{name} {i}"
        data = "".join(f'<Data name="{escape(str(k))}"><value>{escape(_as_text(v))}</value></Data>'
                       for k, v in props.items() if v is not None)
        marks.append(f"<Placemark><name>{escape(str(label))}</name><ExtendedData>{data}</ExtendedData>{_kml_geom(g)}</Placemark>")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
                   f"<name>{escape(name)}</name>{''.join(marks)}</Document></kml>", encoding="utf-8")
    return out


def write_geojson(feats: list[tuple], out: str | Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "geometry": mapping(g), "properties": p} for g, p in feats]}
    out.write_text(json.dumps(fc), encoding="utf-8")
    return out
