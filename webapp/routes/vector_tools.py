"""Vector tools (Analysis ▸ Tools ▸ Vector): buffer, select by attribute, overlay (intersection, union, difference,
symmetric difference, clip) and dissolve. Each runs as a background job (progress, History, Workflows, the
Assistant) and writes its result as a GeoJSON file in analysis/. A layer is given as GeoJSON, or as a workspace file
(GeoJSON or a zipped shapefile). Logic in lulc_fetch/geoprocess.py."""

from __future__ import annotations

import json
import re
import uuid

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
    segments: int = Field(16, ge=4, le=128)
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
