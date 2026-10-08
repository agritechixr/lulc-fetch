"""Coordinate systems of layers: find one (by name or EPSG code), guess one for data that has none, assign one (the data
was read in the wrong system), and place a vector layer with control points. Converting a raster to another system is
Resample / reproject (/api/raster/resample); a vector layer is converted when it is exported. Logic in
lulc_fetch/crs_tools.py."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import raster_path as _raster_path

router = APIRouter()


def _near(lon: float | None, lat: float | None):
    return (lon, lat) if lon is not None and lat is not None else None


@router.get("/api/crs/search")
def crs_search(q: str = "", lon: float | None = None, lat: float | None = None, limit: int = 40):
    from lulc_fetch import crs_tools
    return {"results": crs_tools.search(q[:100], _near(lon, lat), max(1, min(limit, 200)))}


@router.get("/api/crs/describe")
def crs_describe(text: str):
    from lulc_fetch import crs_tools
    try:
        return crs_tools.describe(text[:20000])
    except ValueError as e:
        raise HTTPException(400, str(e))


class SuggestRequest(BaseModel):
    bounds: list[float] | None = Field(None, min_length=4, max_length=4)
    lon: float | None = None
    lat: float | None = None


@router.post("/api/crs/suggest")
def crs_suggest(req: SuggestRequest):
    from lulc_fetch import crs_tools
    return {"suggestions": crs_tools.suggest(req.bounds, _near(req.lon, req.lat))}


class TransformRequest(BaseModel):
    geojson: dict
    actual: str = Field(max_length=20000)            # the system the coordinates are really in
    current: str | None = Field(None, max_length=20000)   # the system they were read in (None: raw numbers, no system yet)


@router.post("/api/crs/vector")
def crs_vector(req: TransformRequest):
    """A layer's coordinates read in `actual` (raw numbers; or, when they were read in `current`, those numbers again),
    given back in WGS 84 for the map."""
    from lulc_fetch import crs_tools
    try:
        fc = req.geojson if not req.current else crs_tools.transform_fc(req.geojson, crs_tools.WGS84, req.current)
        out = crs_tools.transform_fc(fc, req.actual, crs_tools.WGS84)
        return {**out, "crs": crs_tools.describe(req.actual)}
    except Exception as e:   # noqa: BLE001 (bad systems, impossible coordinates)
        raise HTTPException(400, str(e)[:300])


@router.get("/api/crs/raster")
def crs_raster(path: str, lon: float | None = None, lat: float | None = None):
    """A raster's coordinate system, or (when it has none) whether it has a pixel grid and the likely systems."""
    import rasterio

    from lulc_fetch import crs_tools
    p = _raster_path(path)
    with rasterio.open(p) as s:
        has_grid = not s.transform.is_identity or bool(s.gcps[0])
        crs = s.crs or (s.gcps[1] if s.gcps[0] else None)
        b = list(s.bounds) if not s.transform.is_identity else None
        if s.gcps[0] and s.transform.is_identity:
            xs, ys = [g.x for g in s.gcps[0]], [g.y for g in s.gcps[0]]
            b = [min(xs), min(ys), max(xs), max(ys)]
    return {"crs": crs_tools.describe(crs.to_wkt()) if crs else None, "has_grid": has_grid, "bounds": b,
            "suggestions": [] if crs else crs_tools.suggest(b if has_grid else None, _near(lon, lat))}


class AssignRequest(BaseModel):
    path: str
    crs: str = Field(max_length=20000)


@router.post("/api/crs/assign-raster")
def crs_assign_raster(req: AssignRequest):
    """A copy of the raster that says it is in `crs` (its pixels stay as they are), next to the original."""
    from lulc_fetch import crs_tools
    p = _raster_path(req.path)
    out = p.with_name(f"{p.stem}_{uuid.uuid4().hex[:4]}.tif") if p.suffix.lower() in (".tif", ".tiff") else p.with_suffix(".tif")
    try:
        crs_tools.assign_raster(p, req.crs, out)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": ws.rel(out), "crs": crs_tools.describe(req.crs)}


class VectorPoint(BaseModel):
    x: float
    y: float
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class VectorGeorefRequest(BaseModel):
    geojson: dict
    points: list[VectorPoint] = Field(min_length=3, max_length=200)
    method: str = Field("affine", pattern="^(affine|poly2|tps)$")


@router.post("/api/georef/vector")
def georef_vector(req: VectorGeorefRequest):
    """A vector layer placed by control points (its own coordinates ↔ the map): the moved layer, RMSE and residuals."""
    from lulc_fetch import crs_tools
    try:
        return crs_tools.control_point_fc(req.geojson, [p.model_dump() for p in req.points], req.method)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/georef/info")
def georef_info(path: str):
    """The size of a picture or raster already in the workspace, to georeference it."""
    import warnings

    import rasterio
    p = (ws.root() / path).resolve()
    if not p.is_relative_to(ws.root().resolve()) or not p.is_file():
        raise HTTPException(404, "The file is missing")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with rasterio.open(p) as s:
            return {"path": ws.rel(p), "width": s.width, "height": s.height, "bands": s.count, "name": Path(p).name}
