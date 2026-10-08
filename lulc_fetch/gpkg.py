"""GeoPackage (.gpkg, OGC 12-128r18): many vector layers in one SQLite file. Written and read with Python's own sqlite3,
so no GDAL is needed; QGIS, ArcGIS and GDAL open the files written here.

    write_gpkg([(name, [(shapely geometry, properties)]), …], out)   layers in EPSG:4326 (as the app keeps them)
    read_gpkg(path) -> [(name, FeatureCollection in EPSG:4326)]       every feature table, reprojected when needed

Geometries are GeoPackage binary: the "GP" header (version 0, little endian, xy envelope, SRS id) and the geometry as
2D WKB. A layer whose features mix single and multi parts of one kind (Polygon and MultiPolygon) is stored as the multi
kind; one mixing kinds is declared GEOMETRY.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import shapely
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

APPLICATION_ID = 0x47504B47   # "GPKG"
USER_VERSION = 10300           # GeoPackage 1.3
WGS84_WKT = ('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
             'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,'
             'AUTHORITY["EPSG","9122"]],AXIS["Latitude",NORTH],AXIS["Longitude",EAST],AUTHORITY["EPSG","4326"]]')
_MULTI = {"Point": "MultiPoint", "LineString": "MultiLineString", "Polygon": "MultiPolygon"}
_ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_name(name: str, taken: set) -> str:
    t = re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_")[:60] or "layer"
    if t[0].isdigit() or t.lower().startswith(("gpkg_", "rtree_", "sqlite_")):
        t = "l_" + t
    base, i = t, 2
    while t.lower() in taken:
        t, i = f"{base}_{i}", i + 1
    taken.add(t.lower())
    return t


def _geometry_type(geoms: list[BaseGeometry]) -> tuple[str, bool]:
    """The layer's declared type, and whether single parts must become multi parts to match it."""
    kinds = {g.geom_type for g in geoms}
    if len(kinds) == 1:
        return kinds.pop().upper(), False
    multi = {_MULTI.get(k, k) for k in kinds}
    if len(multi) == 1:
        return multi.pop().upper(), True
    return "GEOMETRY", False


def _as_multi(g: BaseGeometry) -> BaseGeometry:
    return {"Point": shapely.MultiPoint, "LineString": shapely.MultiLineString, "Polygon": shapely.MultiPolygon}[g.geom_type]([g]) \
        if g.geom_type in _MULTI else g


def geometry_blob(g: BaseGeometry, srs_id: int = 4326) -> bytes:
    if g.is_empty:
        return b"GP\x00" + bytes([0b00010001]) + struct.pack("<i", srs_id) + shapely.to_wkb(g, byte_order=1, output_dimension=2)
    minx, miny, maxx, maxy = g.bounds
    head = b"GP\x00" + bytes([0b00000011]) + struct.pack("<i", srs_id) + struct.pack("<4d", minx, maxx, miny, maxy)
    return head + shapely.to_wkb(g, byte_order=1, output_dimension=2, flavor="iso")


def parse_blob(blob: bytes) -> tuple[BaseGeometry | None, int]:
    """GeoPackage binary → (shapely geometry, SRS id); None for an empty or unreadable geometry."""
    if not blob or len(blob) < 8 or blob[:2] != b"GP":
        return None, 0
    flags = blob[3]
    srs = struct.unpack("<i" if flags & 1 else ">i", blob[4:8])[0]
    env = _ENVELOPE_BYTES.get((flags >> 1) & 0b111)
    if env is None or flags & 0b00010000:   # bad envelope code, or the empty flag
        return None, srs
    try:
        g = shapely.from_wkb(bytes(blob[8 + env:]))
    except Exception:
        return None, srs
    return (None if g is None or g.is_empty else g), srs


def _column_types(feats: list[tuple]) -> dict[str, str]:
    """Each attribute's column type: INTEGER, REAL or TEXT (anything else, and lists or objects as JSON)."""
    types = {}
    for _, props in feats:
        for k, v in props.items():
            if v is None:
                types.setdefault(k, None)
                continue
            t = "INTEGER" if isinstance(v, (bool, int)) else "REAL" if isinstance(v, float) else "TEXT"
            old = types.get(k)
            types[k] = t if old in (None, t) else "REAL" if {old, t} == {"INTEGER", "REAL"} else "TEXT"
    return {k: t or "TEXT" for k, t in types.items()}


def _value(v, t: str):
    if v is None:
        return None
    if t == "TEXT":
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
    if t == "INTEGER":
        return int(v)
    return float(v)


def _base_tables(con: sqlite3.Connection):
    con.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    con.execute(f"PRAGMA user_version = {USER_VERSION}")
    con.executescript("""
        CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY, organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
        CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE,
            description TEXT DEFAULT '', last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
        CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
            CONSTRAINT uk_gc_table_name UNIQUE (table_name),
            CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
            CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id));
    """)
    con.executemany("INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)", [
        ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "undefined cartesian coordinate reference system"),
        ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "undefined geographic coordinate reference system"),
        ("WGS 84 geodetic", 4326, "EPSG", 4326, WGS84_WKT, "longitude/latitude coordinates in decimal degrees on the WGS 84 spheroid"),
    ])


def _srs(con: sqlite3.Connection, crs) -> int:
    """The SRS id of a coordinate system in the file (EPSG:4326 is there already; others are added)."""
    from rasterio.crs import CRS
    c = CRS.from_user_input(crs)
    epsg = c.to_epsg()
    if epsg == 4326:
        return 4326
    sid = epsg or 100000
    wkt = c.to_wkt()
    name = wkt.split('"')[1] if '"' in wkt else "custom"
    con.execute("INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
                (name, sid, "EPSG" if epsg else "NONE", epsg or sid, wkt, ""))
    return sid


def write_gpkg(layers: list[tuple[str, list[tuple]]], out: str | Path, crs: str | None = None) -> Path:
    """Write vector layers ([(name, [(shapely geometry, properties)])], EPSG:4326) as one GeoPackage (replacing out).
    crs: store them in another coordinate system (converted from WGS 84)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        _base_tables(con)
        srs_id = _srs(con, crs) if crs else 4326
        if srs_id != 4326:
            from rasterio.warp import transform_geom
            from shapely.geometry import shape
            layers = [(n, [(shape(transform_geom("EPSG:4326", crs, mapping(g))), p) for g, p in feats]) for n, feats in layers]
        taken, names = set(), set()
        for name, feats in layers:
            feats = [(g, p) for g, p in feats if g is not None and not g.is_empty]
            if not feats:
                continue
            table = _table_name(name, taken)
            ident, i = name.strip() or table, 2
            while ident.lower() in names:   # identifiers are unique too
                ident, i = f"{name.strip() or table} ({i})", i + 1
            names.add(ident.lower())
            gtype, promote = _geometry_type([g for g, _ in feats])
            types = _column_types(feats)
            cols, seen = {}, {"fid", "geom"}
            for k in types:   # column names: unique without regard to case, never the id or geometry column
                c, j = (k.strip() or "field")[:60], 2
                base = c
                while c.lower() in seen:
                    c, j = f"{base}_{j}", j + 1
                seen.add(c.lower())
                cols[k] = c
            con.execute(f"CREATE TABLE {_q(table)} (fid INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, geom {gtype}"
                        + "".join(f", {_q(c)} {types[k]}" for k, c in cols.items()) + ")")
            geoms = [_as_multi(g) if promote else g for g, _ in feats]
            rows = [[sqlite3.Binary(geometry_blob(g, srs_id))] + [_value(p.get(k), types[k]) for k in cols] for g, (_, p) in zip(geoms, feats)]
            con.executemany(f"INSERT INTO {_q(table)} (geom{''.join(', ' + _q(c) for c in cols.values())}) "
                            f"VALUES ({', '.join('?' * (len(cols) + 1))})", rows)
            minx, miny, maxx, maxy = shapely.GeometryCollection(geoms).bounds
            con.execute("INSERT INTO gpkg_contents VALUES (?, 'features', ?, '', ?, ?, ?, ?, ?, ?)",
                        (table, ident, _now(), minx, miny, maxx, maxy, srs_id))
            con.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, 0, 0)", (table, gtype, srs_id))
        if not taken:
            raise ValueError("No features to save")
        con.commit()
    finally:
        con.close()
    tmp.replace(out)
    return out


def _srs_crs(con: sqlite3.Connection, srs_id: int):
    """The rasterio CRS of an SRS id, or None for WGS 84 / undefined (taken as longitude / latitude)."""
    from rasterio.crs import CRS
    if srs_id in (0, -1, 4326):
        return None
    row = con.execute("SELECT organization, organization_coordsys_id, definition FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (srs_id,)).fetchone()
    if not row:
        return None
    org, code, definition = row
    try:
        crs = CRS.from_epsg(int(code)) if (org or "").upper() == "EPSG" else CRS.from_wkt(definition)
    except Exception:
        try:
            crs = CRS.from_wkt(definition)
        except Exception as e:
            raise ValueError(f"The GeoPackage uses a coordinate system that can't be read ({org}:{code})") from e
    return None if crs.to_epsg() == 4326 else crs


def read_gpkg(path: str | Path | bytes, ask_crs: bool = False) -> list[tuple[str, dict]]:
    """Every feature table of a GeoPackage as (name, FeatureCollection in EPSG:4326), in the file's order. A table whose
    coordinate system is undefined (SRS 0 / −1) is taken as WGS 84, or with ask_crs left as it is, marked crs_missing."""
    from rasterio.warp import transform_geom

    tmp = None
    if isinstance(path, (bytes, bytearray)):
        tmp = tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False)
        tmp.write(path)
        tmp.close()
        path = tmp.name
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            try:
                tables = con.execute("SELECT c.table_name, COALESCE(c.identifier, c.table_name), g.column_name, g.srs_id FROM gpkg_contents c "
                                     "JOIN gpkg_geometry_columns g ON g.table_name = c.table_name WHERE c.data_type = 'features' "
                                     "ORDER BY c.rowid").fetchall()
            except sqlite3.DatabaseError as e:
                raise ValueError(f"Not a GeoPackage: {e}") from e
            out = []
            for table, ident, gcol, srs_id in tables:
                crs = _srs_crs(con, srs_id)
                # the integer primary key is the feature id, not an attribute (as GDAL and QGIS read it)
                pk = {r[1] for r in con.execute(f"PRAGMA table_info({_q(table)})") if r[5] == 1 and (r[2] or "").upper() == "INTEGER"}
                cur = con.execute(f"SELECT * FROM {_q(table)}")
                names = [d[0] for d in cur.description]
                gi = names.index(gcol)
                feats = []
                for row in cur:
                    g, _ = parse_blob(row[gi])
                    if g is None:
                        continue
                    geom = mapping(g)
                    if crs is not None:
                        geom = transform_geom(crs, "EPSG:4326", geom)
                    props = {k: v for i, (k, v) in enumerate(zip(names, row)) if i != gi and k not in pk and not isinstance(v, (bytes, memoryview))}
                    feats.append({"type": "Feature", "geometry": geom, "properties": props})
                if feats:
                    fc = {"type": "FeatureCollection", "features": feats}
                    if ask_crs and srs_id in (0, -1):
                        fc["crs_missing"] = "the GeoPackage layer's coordinate system is undefined"
                    out.append((ident, fc))
            if not out:
                raise ValueError("The GeoPackage has no vector layers with features (raster tiles aren't read)")
            return out
        finally:
            con.close()
    finally:
        if tmp:
            Path(tmp.name).unlink(missing_ok=True)
