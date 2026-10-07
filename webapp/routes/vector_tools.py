"""Vector tools (Analysis ▸ Tools ▸ Vector): buffer, select by attribute, overlay (intersection, union, difference,
symmetric difference, clip) and dissolve. Each runs as a background job (progress, History, Workflows, the
Assistant) and writes its result as a GeoJSON file in analysis/. A layer is given as GeoJSON, or as a workspace file
(GeoJSON or a zipped shapefile). Logic in lulc_fetch/geoprocess.py."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs

router = APIRouter()

Layer = str | dict   # a workspace path, or a GeoJSON FeatureCollection / Feature / geometry


def _write(fc: dict, name: str) -> dict:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "layer"
    out = ws.root() / "analysis" / uuid.uuid4().hex[:8] / f"{safe}.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    return {"path": ws.rel(out), "features": len(fc["features"]), "name": safe}


def _layer(src):
    from lulc_fetch.geoprocess import read_layer
    return read_layer(src, ws.root())


class BufferRequest(BaseModel):
    layer: Layer
    distance: float = Field(description="metres; negative shrinks polygons")
    segments: int = Field(64, ge=4, le=128)   # sides of a full circle: 64 is within 0.2 % of its area
    dissolve: bool = False
    name: str = Field("buffer", max_length=80)


@router.post("/api/vector/buffer")
def vector_buffer(req: BufferRequest):
    """Grow each shape by a distance in metres (shrink with a negative one); dissolve merges them into one."""
    from lulc_fetch import geoprocess
    return jobs.submit("vbuffer", f"Buffer {req.distance:g} m", {"distance": req.distance},
                       lambda job: _write(geoprocess.buffer(_layer(req.layer), req.distance, segments=req.segments, dissolve=req.dissolve), req.name)).to_dict()


class QueryRequest(BaseModel):
    layer: Layer
    where: str = Field(min_length=1, max_length=2000, description='e.g. crop == "rice" and area_ha > 2')
    name: str = Field("selection", max_length=80)


@router.post("/api/vector/query")
def vector_query(req: QueryRequest):
    """Select by attribute: the features whose attributes meet a condition, as a new layer."""
    from lulc_fetch import geoprocess
    fc = _layer(req.layer) if isinstance(req.layer, dict) else None
    if fc is not None:   # check the condition now, so a mistake is said at once
        try:
            geoprocess.compile_query(req.where, sorted({k for f in fc.get("features", [])[:500] for k in (f.get("properties") or {})}))
        except ValueError as e:
            raise HTTPException(400, str(e))
    return jobs.submit("vquery", f"Select: {req.where[:60]}", {"where": req.where},
                       lambda job: _write(geoprocess.query(fc or _layer(req.layer), req.where), req.name)).to_dict()


class OverlayRequest(BaseModel):
    a: Layer
    b: Layer
    how: str = Field("intersection", pattern="^(intersection|union|difference|symmetric_difference|clip)$")
    name: str = Field("overlay", max_length=80)


@router.post("/api/vector/overlay")
def vector_overlay(req: OverlayRequest):
    """Overlay two layers: intersection (where both are), union (every piece of both), difference (A without B),
    symmetric difference (in one but not both), clip (A cut to B)."""
    from lulc_fetch import geoprocess
    return jobs.submit("voverlay", f"Overlay ({req.how.replace('_', ' ')})", {"how": req.how},
                       lambda job: _write(geoprocess.overlay(_layer(req.a), _layer(req.b), req.how), req.name)).to_dict()


class DissolveRequest(BaseModel):
    layer: Layer
    field: str | None = Field(None, max_length=200)
    name: str = Field("dissolved", max_length=80)


@router.post("/api/vector/dissolve")
def vector_dissolve(req: DissolveRequest):
    """Merge shapes: all into one, or one per value of a field."""
    from lulc_fetch import geoprocess
    return jobs.submit("vdissolve", f"Dissolve{f' by {req.field}' if req.field else ''}", {"field": req.field},
                       lambda job: _write(geoprocess.dissolve(_layer(req.layer), req.field), req.name)).to_dict()


@router.get("/api/vector/read")
def vector_read(path: str):
    """A vector file of the workspace as GeoJSON (to put a tool's result on the map)."""
    try:
        return _layer(path)
    except ValueError as e:
        raise HTTPException(404, str(e))


class SaveLayer(BaseModel):
    layer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    geojson: dict


@router.post("/api/vector/save")
def vector_save(req: SaveLayer):
    """A map layer kept as a file in the workspace (uploads/layers/<id>.geojson), so tools and the Assistant can use it."""
    if req.geojson.get("type") != "FeatureCollection":
        raise HTTPException(400, "Not a FeatureCollection")
    out = ws.root() / "uploads" / "layers" / f"{req.layer_id}.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(req.geojson, ensure_ascii=False), encoding="utf-8")
    return {"path": ws.rel(out), "features": len(req.geojson.get("features") or [])}


# ------------------------------------------------------------------ batch 1: spatial analysis
class ZonalRequest(BaseModel):
    layer: Layer
    raster: str = Field(max_length=1000)
    band: int = Field(1, ge=1)
    stats: list[str] = Field(default_factory=lambda: ["mean", "min", "max", "std", "count"])
    categorical: bool = False
    name: str = Field("zonal_stats", max_length=80)


@router.post("/api/vector/zonal")
def vector_zonal(req: ZonalRequest):
    """Zonal statistics: each polygon with a raster's values summarised inside it (mean, min, max, std, median, sum,
    count), or for a class raster the % of each class and the majority class."""
    from lulc_fetch import geoprocess

    from ..core import raster_path
    path = raster_path(req.raster)
    bad = [s for s in req.stats if s not in ("mean", "min", "max", "std", "median", "sum", "count")]
    if bad:
        raise HTTPException(400, f"Unknown statistics: {', '.join(bad)}")
    return jobs.submit("vzonal", f"Zonal statistics of {Path(req.raster).name}", {"raster": req.raster},
                       lambda job: _write(geoprocess.zonal_stats(_layer(req.layer), path, band=req.band, stats=req.stats, categorical=req.categorical), req.name)).to_dict()


class LocationRequest(BaseModel):
    a: Layer
    b: Layer
    predicate: str = Field("intersects", pattern="^(intersects|within|contains|disjoint|within_distance)$")
    distance: float = 0
    name: str = Field("selected", max_length=80)


@router.post("/api/vector/select-location")
def vector_select_location(req: LocationRequest):
    """Select by location: the features of A that intersect / are within / contain / are apart from / are within
    a distance (m) of B."""
    from lulc_fetch import geoprocess
    return jobs.submit("vlocation", f"Select by location ({req.predicate.replace('_', ' ')})", {"predicate": req.predicate},
                       lambda job: _write(geoprocess.select_by_location(_layer(req.a), _layer(req.b), req.predicate, req.distance), req.name)).to_dict()


class SpatialJoinRequest(BaseModel):
    a: Layer
    b: Layer
    how: str = Field("intersects", pattern="^(intersects|within|nearest)$")
    max_distance: float | None = None
    name: str = Field("joined", max_length=80)


@router.post("/api/vector/spatial-join")
def vector_spatial_join(req: SpatialJoinRequest):
    """Spatial join: A's features with the attributes of the B feature they overlap most, lie within, or are nearest to."""
    from lulc_fetch import geoprocess
    return jobs.submit("vsjoin", f"Spatial join ({req.how})", {"how": req.how},
                       lambda job: _write(geoprocess.spatial_join(_layer(req.a), _layer(req.b), req.how, req.max_distance), req.name)).to_dict()


class GeometryRequest(BaseModel):
    layer: Layer
    name: str = Field("with_geometry", max_length=80)


@router.post("/api/vector/geometry")
def vector_geometry(req: GeometryRequest):
    """Calculate geometry: area (m², ha), perimeter or length (m), centroid (lon, lat) as fields."""
    from lulc_fetch import geoprocess
    return jobs.submit("vgeometry", "Calculate geometry", {}, lambda job: _write(geoprocess.calculate_geometry(_layer(req.layer)), req.name)).to_dict()


class CountRequest(BaseModel):
    polygons: Layer
    points: Layer
    sum_field: str | None = Field(None, max_length=200)
    name: str = Field("point_counts", max_length=80)


@router.post("/api/vector/count-points")
def vector_count_points(req: CountRequest):
    """Count points in polygons (and the sum of a points' field)."""
    from lulc_fetch import geoprocess
    return jobs.submit("vcount", "Count points in polygons", {"sum_field": req.sum_field},
                       lambda job: _write(geoprocess.count_points(_layer(req.polygons), _layer(req.points), req.sum_field), req.name)).to_dict()


class TableJoinRequest(BaseModel):
    layer: Layer
    table: str = Field(max_length=1000)
    layer_field: str = Field(max_length=200)
    table_field: str = Field(max_length=200)
    name: str = Field("joined_table", max_length=80)


def _read_rows(rel: str) -> list[dict]:
    import pandas as pd
    p = (ws.root() / rel).resolve()
    if not p.is_relative_to(ws.root().resolve()) or not p.is_file():
        raise ValueError(f"The table is missing: {rel}")
    ext = p.suffix.lower()
    df = pd.read_parquet(p) if ext == ".parquet" else pd.read_excel(p) if ext in (".xlsx", ".xls") else pd.read_csv(p, sep="\t" if ext == ".tsv" else ",")
    return json.loads(df.to_json(orient="records", default_handler=str))


@router.post("/api/vector/join-table")
def vector_join_table(req: TableJoinRequest):
    """Join a table (CSV / Excel / Parquet) to a layer by a shared field: its columns become the features' attributes."""
    from lulc_fetch import geoprocess

    def work(job):
        fc, missing = geoprocess.join_table(_layer(req.layer), _read_rows(req.table), req.layer_field, req.table_field)
        return {**_write(fc, req.name), "unmatched": missing}
    return jobs.submit("vtjoin", f"Join {Path(req.table).name} to the layer", {"table": req.table}, work).to_dict()


# ------------------------------------------------------------------ batch 3: geometry helpers
GEOM_OPS = ("centroids", "convex_hull", "simplify", "merge", "explode", "fishnet", "random_points")


class GeomOpRequest(BaseModel):
    op: str = Field(pattern="^(" + "|".join(GEOM_OPS) + ")$")
    layer: Layer | None = None
    layers: list[Layer] = Field(default_factory=list, max_length=20)   # merge: two or more layers
    layer_names: list[str] = Field(default_factory=list, max_length=20)
    inside: bool = False            # centroids: a point surely inside each shape
    whole: bool = False             # convex hull of the whole layer
    tolerance: float = 10           # simplify (m)
    cell: float = 100               # fishnet cell (m)
    clip: bool = True               # fishnet: only inside the area
    count: int = Field(100, ge=1, le=100_000)    # random points
    per_feature: bool = False
    seed: int | None = None
    name: str = Field("", max_length=80)


@router.post("/api/vector/geom-op")
def vector_geom_op(req: GeomOpRequest):
    """Geometry helpers: centroids, convex hull, simplify (m), merge layers, multipart → single parts, a fishnet
    grid of cells (m) over an area, random points inside polygons."""
    from lulc_fetch import geoprocess as g
    if req.op == "merge" and len(req.layers) < 2:
        raise HTTPException(400, "Merge needs two or more layers")
    if req.op != "merge" and req.layer is None:
        raise HTTPException(400, "Choose a layer")

    def work(job):
        lay = (lambda: _layer(req.layer))
        fc = {"centroids": lambda: g.centroids(lay(), req.inside), "convex_hull": lambda: g.convex_hull(lay(), req.whole),
              "simplify": lambda: g.simplify(lay(), req.tolerance), "explode": lambda: g.explode(lay()),
              "merge": lambda: g.merge_layers([(req.layer_names[i] if i < len(req.layer_names) else f"layer {i + 1}", _layer(x)) for i, x in enumerate(req.layers)]),
              "fishnet": lambda: g.fishnet(lay(), req.cell, req.clip), "random_points": lambda: g.random_points(lay(), req.count, req.per_feature, req.seed)}[req.op]()
        return _write(fc, req.name or req.op)
    return jobs.submit("vgeomop", req.op.replace("_", " ").capitalize(), {"op": req.op}, work).to_dict()
