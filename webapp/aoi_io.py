"""Turning user input (uploaded vector files, addresses) into WGS84 GeoJSON."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePath

import requests
from rasterio.crs import CRS
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "lulc-fetch/0.1 (local LULC data tool)"
VECTOR_EXTS = {".zip", ".shp", ".shx", ".dbf", ".prj", ".cpg", ".geojson", ".json", ".kml", ".kmz"}


def _fc(geoms: list[dict], props: list[dict] | None = None) -> dict:
    props = props or [{} for _ in geoms]
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": g, "properties": p} for g, p in zip(geoms, props)]}


# ------------------------------------------------------------------ shapefile

def _read_shapefile(parts: dict[str, bytes]) -> dict:
    import shapefile  # pyshp

    if "shp" not in parts:
        raise ValueError("A shapefile needs at least the .shp file (plus .shx/.dbf/.prj) — "
                         "upload them together or as one .zip")
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]),
                              shx=io.BytesIO(parts["shx"]) if "shx" in parts else None,
                              dbf=io.BytesIO(parts["dbf"]) if "dbf" in parts else None)
    src_crs = CRS.from_wkt(parts["prj"].decode("utf-8", "ignore")) if "prj" in parts else None
    field_names = [f[0] for f in reader.fields[1:]] if "dbf" in parts else []
    geoms, props = [], []
    for i, shp in enumerate(reader.shapes()):
        if shp.shapeType == shapefile.NULL:
            continue
        geom = shp.__geo_interface__
        if src_crs and not src_crs.equals(CRS.from_epsg(4326)):
            geom = transform_geom(src_crs, "EPSG:4326", geom)
        geoms.append(geom)
        if field_names:
            rec = reader.record(i)
            props.append({k: v for k, v in zip(field_names, rec) if isinstance(v, (str, int, float))})
        else:
            props.append({})
    if not geoms:
        raise ValueError("The shapefile has no geometries")
    fc = _fc(geoms, props)
    if not src_crs:
        fc["warning"] = "No .prj file — assumed coordinates are WGS84 longitude/latitude"
    return fc


# ------------------------------------------------------------------ KML

def _kml_coords(text: str) -> list[list[float]]:
    out = []
    for tok in text.split():
        vals = tok.split(",")
        if len(vals) >= 2:
            out.append([float(vals[0]), float(vals[1])])
    return out


def _read_kml(data: bytes) -> dict:
    root = ET.fromstring(data)
    geoms, props = [], []
    for pm in root.iter():
        if not pm.tag.endswith("Placemark"):
            continue
        name = next((el.text for el in pm.iter() if el.tag.endswith("name")), None)
        for el in pm.iter():
            tag = el.tag.split("}")[-1]
            if tag == "Polygon":
                rings = []
                for b in el.iter():
                    btag = b.tag.split("}")[-1]
                    if btag in ("outerBoundaryIs", "innerBoundaryIs"):
                        c = next((x for x in b.iter() if x.tag.endswith("coordinates")), None)
                        if c is not None and c.text:
                            ring = _kml_coords(c.text)
                            rings.insert(0, ring) if btag == "outerBoundaryIs" else rings.append(ring)
                if rings:
                    geoms.append({"type": "Polygon", "coordinates": rings})
                    props.append({"name": name})
            elif tag in ("LineString", "Point"):
                c = next((x for x in el.iter() if x.tag.endswith("coordinates")), None)
                if c is not None and c.text:
                    coords = _kml_coords(c.text)
                    geoms.append({"type": tag, "coordinates": coords if tag == "LineString" else coords[0]})
                    props.append({"name": name})
    if not geoms:
        raise ValueError("No Placemark geometries found in the KML")
    return _fc(geoms, props)


# ------------------------------------------------------------------ GeoJSON

def _read_geojson(data: bytes) -> dict:
    obj = json.loads(data)
    if obj.get("type") == "FeatureCollection":
        fc = obj
    elif obj.get("type") == "Feature":
        fc = _fc([obj["geometry"]], [obj.get("properties") or {}])
    else:
        fc = _fc([obj])
    crs_name = (obj.get("crs") or {}).get("properties", {}).get("name", "")
    if crs_name and "4326" not in crs_name and "CRS84" not in crs_name:
        src = CRS.from_user_input(crs_name.replace("urn:ogc:def:crs:", "").replace("::", ":"))
        for f in fc["features"]:
            f["geometry"] = transform_geom(src, "EPSG:4326", f["geometry"])
    return fc


# ------------------------------------------------------------------ entry point

def parse_upload(files: list[tuple[str, bytes]]) -> dict:
    """Parse one or more uploaded files into a WGS84 FeatureCollection plus a merged AOI geometry."""
    shp_parts: dict[str, bytes] = {}
    fc = None
    for name, data in files:
        ext = PurePath(name).suffix.lower()
        if ext not in VECTOR_EXTS:
            raise ValueError(f"Unsupported file type {ext!r}. Use a zipped shapefile, .shp+.shx+.dbf+.prj, "
                             f"GeoJSON, KML or KMZ.")
        if ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            shp_parts[ext[1:]] = data
        elif ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for member in z.namelist():
                    mext = PurePath(member).suffix.lower()
                    if mext in (".shp", ".shx", ".dbf", ".prj") and not member.startswith("__MACOSX"):
                        shp_parts.setdefault(mext[1:], z.read(member))
                    elif mext in (".geojson", ".json", ".kml") and fc is None:
                        inner = z.read(member)
                        fc = _read_kml(inner) if mext == ".kml" else _read_geojson(inner)
        elif ext == ".kmz":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                kml = next(m for m in z.namelist() if m.lower().endswith(".kml"))
                fc = _read_kml(z.read(kml))
        elif ext == ".kml":
            fc = _read_kml(data)
        else:
            fc = _read_geojson(data)
    if shp_parts:
        fc = _read_shapefile(shp_parts)
    if fc is None:
        raise ValueError("No vector data found in the upload")
    return with_aoi(fc)


def with_aoi(fc: dict) -> dict:
    """Attach a single merged AOI geometry. Points and lines get a small buffer (~500 m)."""
    shapes = [shape(f["geometry"]) for f in fc["features"] if f.get("geometry")]
    merged = unary_union([s if s.geom_type.endswith("Polygon") else s.buffer(0.005) for s in shapes])
    if merged.is_empty:
        raise ValueError("The uploaded geometry is empty")
    fc["aoi"] = mapping(merged)
    return fc


def geocode(query: str, limit: int = 6) -> list[dict]:
    """Address / place search with OpenStreetMap Nominatim (max 1 request per second, per their policy)."""
    r = requests.get(NOMINATIM, params={"q": query, "format": "jsonv2", "limit": limit,
                                        "polygon_geojson": 1, "polygon_threshold": 0.0005},
                     headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    out = []
    for res in r.json():
        s, n, w, e = (float(v) for v in res["boundingbox"])
        geo = res.get("geojson")
        out.append({
            "name": res.get("display_name"), "type": res.get("type"), "category": res.get("category"),
            "lat": float(res["lat"]), "lon": float(res["lon"]), "bbox": [w, s, e, n],
            "boundary": geo if geo and geo.get("type") in ("Polygon", "MultiPolygon") else None,
        })
    return out
