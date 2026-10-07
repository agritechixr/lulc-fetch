"""Conversion tools (Analysis ▸ Tools ▸ Conversion): raster → polygons / polylines / points, vector → raster
(rasterize), and feature conversions (polygons → lines, lines → polygons, vertices → points, points → lines, points
along lines, lines → segments, bounding boxes). Background jobs writing to analysis/. Logic in lulc_fetch/convert.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs
from ..core import raster_path as _raster_path
from .raster_tools import _out_dir, _safe
from .vector_tools import Layer, _layer, _write

router = APIRouter()


class RasterToPolygonRequest(BaseModel):
    raster: str
    band: int = Field(1, ge=1)
    values: list[int] | None = Field(None, max_length=255)   # only these classes
    min_area: float = Field(0, ge=0)                          # m²: smaller patches merge into their neighbour
    simplify: float = Field(0, ge=0)                          # m
    dissolve: bool = False
    diagonal: bool = False
    name: str = Field("polygons", max_length=80)


@router.post("/api/convert/raster-to-polygon")
def raster_to_polygon(req: RasterToPolygonRequest):
    """Areas of equal value (classes) of a raster as polygons with value, class name and area (ha)."""
    from lulc_fetch import convert
    p = _raster_path(req.raster)
    return jobs.submit("r2poly", f"Raster → polygons: {p.name}", {"dissolve": req.dissolve}, lambda job: _write(
        convert.raster_to_polygons(p, band=req.band, values=req.values, min_area=req.min_area, simplify=req.simplify,
                                   dissolve=req.dissolve, diagonal=req.diagonal), req.name)).to_dict()


class RasterToLineRequest(BaseModel):
    raster: str
    mode: str = Field("boundaries", pattern="^(boundaries|centrelines)$")
    band: int = Field(1, ge=1)
    values: list[int] | None = Field(None, max_length=255)
    simplify: float = Field(0, ge=0)
    min_length: float = Field(0, ge=0)
    name: str = Field("lines", max_length=80)


@router.post("/api/convert/raster-to-polyline")
def raster_to_polyline(req: RasterToLineRequest):
    """Lines from a raster: the boundaries between classes, or the centrelines of thin shapes (roads, rivers) in a mask."""
    from lulc_fetch import convert
    p = _raster_path(req.raster)
    return jobs.submit("r2line", f"Raster → {req.mode}: {p.name}", {"mode": req.mode}, lambda job: _write(
        convert.raster_to_polylines(p, mode=req.mode, band=req.band, values=req.values, simplify=req.simplify,
                                    min_length=req.min_length), req.name)).to_dict()


class RasterToPointRequest(BaseModel):
    raster: str
    bands: list[int] | None = Field(None, max_length=50)
    step: int = Field(1, ge=1, le=1000)
    name: str = Field("points", max_length=80)


@router.post("/api/convert/raster-to-point")
def raster_to_point(req: RasterToPointRequest):
    """A point at the centre of each pixel with data (or every n-th), with the value of each band."""
    from lulc_fetch import convert
    p = _raster_path(req.raster)
    return jobs.submit("r2point", f"Raster → points: {p.name}", {"step": req.step}, lambda job: _write(
        convert.raster_to_points(p, bands=req.bands, step=req.step), req.name)).to_dict()


class RasterizeRequest(BaseModel):
    layer: Layer
    mode: str = Field("value", pattern="^(value|presence|count)$")
    field: str | None = Field(None, max_length=200)
    res: float | None = Field(None, gt=0)
    like: str | None = None           # a raster whose grid to use
    all_touched: bool = False
    name: str = Field("rasterized", max_length=80)


@router.post("/api/convert/rasterize")
def vector_to_raster(req: RasterizeRequest):
    """A vector layer burnt into a GeoTIFF: a field's values (text → classes with names), presence, or a count of
    shapes per cell; on a pixel size in metres or on another raster's grid."""
    from lulc_fetch import convert
    like = _raster_path(req.like) if req.like else None
    if not like and not req.res:
        raise HTTPException(400, "Give the pixel size in metres, or a raster to match")
    if req.mode == "value" and not req.field:
        raise HTTPException(400, "Choose the field whose values go into the raster (or use presence / count)")

    def work(job):
        r = convert.rasterize(_layer(req.layer), _out_dir() / f"{_safe(req.name)}.tif", field=req.field, mode=req.mode,
                              res=req.res, like=like, all_touched=req.all_touched)
        return {**r, "path": ws.rel(r["path"])}
    return jobs.submit("rasterize", f"Vector → raster ({req.mode})", {"mode": req.mode, "field": req.field}, work).to_dict()


class FeatureConvertRequest(BaseModel):
    op: str = Field(pattern="^(polygons_to_lines|lines_to_polygons|vertices_to_points|points_to_lines|points_along_lines|split_lines|bounding_boxes)$")
    layer: Layer
    group_by: str | None = Field(None, max_length=200)   # points → lines: one line per value
    order_by: str | None = Field(None, max_length=200)   # points → lines: in this order
    close: bool = False
    distance: float = Field(100, gt=0)                   # points along lines (m)
    whole: bool = False                                  # one bounding box for the layer
    name: str = Field("", max_length=80)


@router.post("/api/convert/features")
def convert_features(req: FeatureConvertRequest):
    """Feature conversions: polygons → lines, lines → polygons, vertices → points, points → lines (ordered, grouped),
    points every n metres along lines, lines → straight segments, bounding boxes."""
    from lulc_fetch import convert as c

    def work(job):
        fc = _layer(req.layer)
        out = {"polygons_to_lines": lambda: c.polygons_to_lines(fc), "lines_to_polygons": lambda: c.lines_to_polygons(fc),
               "vertices_to_points": lambda: c.vertices_to_points(fc),
               "points_to_lines": lambda: c.points_to_lines(fc, group_by=req.group_by, order_by=req.order_by, close=req.close),
               "points_along_lines": lambda: c.points_along_lines(fc, req.distance), "split_lines": lambda: c.split_lines(fc),
               "bounding_boxes": lambda: c.bounding_boxes(fc, req.whole)}[req.op]()
        return _write(out, req.name or req.op)
    return jobs.submit("vconvert", req.op.replace("_", " ").capitalize(), {"op": req.op}, work).to_dict()
