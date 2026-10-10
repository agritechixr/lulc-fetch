"""Analysis ▸ Hydrology: DEM preparation, flow direction & accumulation, watersheds, drainage network, terrain & runoff
indicators, rainfall–runoff, flood from HAND and flood susceptibility. Background jobs writing to analysis/; logic in
lulc_fetch/hydro/. (The all-in-one Hydrology tool is /api/raster/hydrology in geo_tools.py.)"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs
from ..core import raster_path as _raster_path
from .geo_tools import Point, _out_dir, _points, _safe, _table

router = APIRouter()


def _conditioned(p, asked: bool | None) -> bool:
    """A DEM made by DEM preparation is already conditioned (its pits are not filled again)."""
    if asked is not None:
        return asked
    import rasterio
    with rasterio.open(p) as s:
        return s.tags().get("hydro_conditioned") == "1"


def _result(r: dict, name: str = "") -> dict:
    out = {**r, "outputs": [ws.rel(x) for x in r.get("outputs", [])]}
    if r.get("path"):
        out["path"] = ws.rel(r["path"])
    if r.get("csv"):
        out["csv"] = _table(r["csv"])
    if name:
        out["name"] = name
    return out


class ConditionRequest(BaseModel):
    dem: str
    steps: list[str] = Field(default_factory=lambda: ["fill_nodata", "breach"])
    like: str | None = None                         # align onto this raster's grid
    res: float | None = Field(None, gt=0)           # or onto this pixel size
    streams: dict | None = None                     # lines to burn in
    burn_m: float = Field(5.0, ge=0, le=100)
    burn_buffer_m: float = Field(0.0, ge=0, le=5000)
    max_breach_m: float | None = Field(None, gt=0)
    max_hole_cells: int = Field(2000, ge=1, le=10_000_000)
    name: str = Field("", max_length=80)


@router.post("/api/hydro/condition")
def hydro_condition(req: ConditionRequest):
    """A DEM made to drain: fill no-data holes, align, burn streams in, breach and / or fill pits."""
    from lulc_fetch.hydro import conditioning as C
    p = _raster_path(req.dem)
    if set(req.steps) - {"fill_nodata", "breach", "fill"}:
        raise HTTPException(400, "steps: fill_nodata, breach, fill")
    like = _raster_path(req.like) if req.like else None
    return jobs.submit("hydrocondition", f"DEM preparation of {p.name}", {"steps": req.steps}, lambda job: _result(
        C.condition(p, _out_dir(), steps=req.steps, like=like, res=req.res, streams=req.streams, burn_m=req.burn_m, burn_buffer_m=req.burn_buffer_m,
                    max_breach_m=req.max_breach_m, max_hole_cells=req.max_hole_cells, name=_safe(req.name) or p.stem))).to_dict()


class FlowRequest(BaseModel):
    dem: str
    method: str = Field("d8", pattern="^(d8|dinf|mfd)$")
    outputs: list[str] = Field(default_factory=lambda: ["direction", "accumulation", "area", "sca"])
    mfd_p: float = Field(1.1, gt=0, le=20)
    conditioned: bool | None = None
    name: str = Field("", max_length=80)


@router.post("/api/hydro/flow")
def hydro_flow(req: FlowRequest):
    """Flow direction and accumulation by D8, D-infinity or MFD: cells, contributing area (km²), specific catchment area."""
    from lulc_fetch.hydro import flow as F
    p = _raster_path(req.dem)
    if not req.outputs or set(req.outputs) - {"direction", "accumulation", "area", "sca"}:
        raise HTTPException(400, "outputs: direction, accumulation, area, sca")
    return jobs.submit("hydroflow", f"Flow ({F.METHODS[req.method]}) of {p.name}", {"method": req.method}, lambda job: _result(
        F.run(p, _out_dir(), method=req.method, outputs=req.outputs, mfd_p=req.mfd_p, conditioned=_conditioned(p, req.conditioned),
              name=_safe(req.name) or p.stem))).to_dict()


class NetworkRequest(BaseModel):
    dem: str
    stream_km2: float = Field(1.0, gt=0, le=1e6)
    outputs: list[str] = Field(default_factory=lambda: ["lines", "nodes", "order", "magnitude"])
    conditioned: bool | None = None
    name: str = Field("", max_length=80)


@router.post("/api/hydro/network")
def hydro_network(req: NetworkRequest):
    """Streams by a contributing-area threshold: links with Strahler and Shreve order and topology, nodes, drainage density."""
    from lulc_fetch.hydro import network as N
    p = _raster_path(req.dem)
    if not req.outputs or set(req.outputs) - {"lines", "nodes", "order", "magnitude", "links"}:
        raise HTTPException(400, "outputs: lines, nodes, order, magnitude, links")
    return jobs.submit("hydronetwork", f"Drainage network of {p.name}", {"stream_km2": req.stream_km2}, lambda job: _result(
        N.run(p, _out_dir(), stream_km2=req.stream_km2, outputs=req.outputs, conditioned=_conditioned(p, req.conditioned), name=_safe(req.name) or p.stem),
        "stream_links")).to_dict()


class WatershedRequest(BaseModel):
    dem: str
    mode: str = Field("points", pattern="^(points|subbasins|all)$")
    points: list[Point] = Field(default_factory=list, max_length=1000)
    snap_m: float = Field(150, ge=0, le=20_000)
    stream_km2: float = Field(1.0, gt=0, le=1e6)
    min_basin_km2: float | None = Field(None, gt=0)
    within_points: bool = False
    conditioned: bool | None = None
    name: str = Field("", max_length=80)


@router.post("/api/hydro/watershed")
def hydro_watershed(req: WatershedRequest):
    """Watersheds of outlet points (snapped, nested with their parents), sub-watersheds of every stream link, or every basin;
    with boundaries, statistics, longest flow paths and the basin hierarchy."""
    from lulc_fetch.hydro import watershed as W
    p = _raster_path(req.dem)
    pts = _points(req.points, "Outlet points", 1) if (req.mode == "points" or req.within_points) else None
    title = {"points": "Watersheds", "subbasins": "Sub-watersheds", "all": "All basins"}[req.mode]
    return jobs.submit("hydrowatershed", f"{title} of {p.name}", {"mode": req.mode}, lambda job: _result(
        W.run(p, _out_dir(), mode=req.mode, points=pts, snap_m=req.snap_m, stream_km2=req.stream_km2, min_basin_km2=req.min_basin_km2,
              within_points=req.within_points, conditioned=_conditioned(p, req.conditioned), name=_safe(req.name) or p.stem), "watersheds")).to_dict()


class TerrainRequest(BaseModel):
    dem: str
    products: list[str] = Field(default_factory=lambda: ["slope", "curvature", "twi", "spi", "hand"])
    flow: str = Field("mfd", pattern="^(mfd|dinf|d8)$")
    stream_km2: float = Field(1.0, gt=0, le=1e6)
    points: list[Point] = Field(default_factory=list, max_length=1000)
    min_depression_m: float = Field(0.1, ge=0)
    conditioned: bool | None = None
    name: str = Field("", max_length=80)


@router.post("/api/hydro/terrain")
def hydro_terrain(req: TerrainRequest):
    """Slope, aspect, curvature, TWI, SPI, HAND, depressions, flow length and flow paths."""
    from lulc_fetch.hydro import terrain as T
    p = _raster_path(req.dem)
    if not req.products or set(req.products) - set(T.PRODUCTS):
        raise HTTPException(400, f"products: {', '.join(T.PRODUCTS)}")
    pts = _points(req.points, "Points", 1) if "flowpaths" in req.products else None
    return jobs.submit("hydroterrain", f"Terrain & runoff indicators of {p.name}", {"products": req.products}, lambda job: _result(
        T.run(p, _out_dir(), products=req.products, flow=req.flow, stream_km2=req.stream_km2, points=pts, min_depression_m=req.min_depression_m,
              conditioned=_conditioned(p, req.conditioned), name=_safe(req.name) or p.stem))).to_dict()


class RunoffRequest(BaseModel):
    dem: str
    rain_mm: float | None = Field(None, gt=0, le=5000)
    rain_raster: str | None = None
    landcover: str | None = None
    cn_raster: str | None = None
    soil: str = Field("B", pattern="^[ABCDabcd]$")
    soil_raster: str | None = None
    scheme: str = Field("auto", pattern="^(auto|worldcover|dynamicworld|esri)$")
    custom: dict[str, list[float]] | None = None
    condition: str = Field("II", pattern="^(I|II|III)$")
    lam: float = Field(0.2, ge=0.01, le=0.3)
    points: list[Point] = Field(default_factory=list, max_length=500)
    snap_m: float = Field(150, ge=0, le=20_000)
    duration_h: float = Field(24, gt=0, le=240)
    name: str = Field("runoff", max_length=80)


@router.post("/api/hydro/runoff")
def hydro_runoff(req: RunoffRequest):
    """SCS curve-number runoff of a storm; routed volumes; per watershed of the outlets: runoff, volume and peak flow."""
    from lulc_fetch.hydro import runoff as R
    p = _raster_path(req.dem)
    paths = {k: _raster_path(v) for k, v in (("landcover", req.landcover), ("cn_raster", req.cn_raster), ("soil_raster", req.soil_raster),
                                              ("rain_raster", req.rain_raster)) if v}
    pts = _points(req.points, "Outlet points", 1) if req.points else None
    return jobs.submit("hydrorunoff", f"Rainfall–runoff (SCS-CN) on {p.name}", {}, lambda job: _result(
        R.runoff(p, _out_dir(), rain_mm=req.rain_mm, **{k: str(v) for k, v in paths.items()}, soil=req.soil.upper(), scheme=req.scheme, custom=req.custom,
                 condition=req.condition, lam=req.lam, points=pts, snap_m=req.snap_m, duration_h=req.duration_h, name=_safe(req.name) or "runoff"),
        "runoff_watersheds")).to_dict()


class HandFloodRequest(BaseModel):
    dem: str
    levels_m: list[float] = Field(default_factory=lambda: [1, 2, 5], min_length=1, max_length=20)
    stream_km2: float = Field(1.0, gt=0, le=1e6)
    max_dist_m: float | None = Field(None, gt=0)
    conditioned: bool | None = None
    name: str = Field("", max_length=80)


@router.post("/api/hydro/hand-flood")
def hydro_hand_flood(req: HandFloodRequest):
    """Flooded extent and depth at water levels above the streams (HAND)."""
    from lulc_fetch.hydro import runoff as R
    p = _raster_path(req.dem)
    return jobs.submit("hydrohandflood", f"Flood from HAND on {p.name}", {"levels_m": req.levels_m}, lambda job: _result(
        R.hand_flood(p, _out_dir(), levels_m=req.levels_m, stream_km2=req.stream_km2, max_dist_m=req.max_dist_m,
                     conditioned=_conditioned(p, req.conditioned), name=_safe(req.name) or p.stem), "flood_extent")).to_dict()


class SusceptibilityRequest(BaseModel):
    predictors: list[str] = Field(min_length=1, max_length=40)
    floods: dict
    non_floods: dict | None = None
    ratio: float = Field(1.0, gt=0, le=10)
    buffer_m: float = Field(200, ge=0, le=100_000)
    model: str = Field("rf", pattern="^(rf|lgbm)$")
    block_m: float = Field(2000, gt=0, le=200_000)
    folds: int = Field(5, ge=2, le=10)
    name: str = Field("flood_susceptibility", max_length=80)


@router.post("/api/hydro/susceptibility")
def hydro_susceptibility(req: SusceptibilityRequest):
    """Flood susceptibility from a flood inventory and predictor layers, scored with spatial-block cross-validation."""
    from lulc_fetch.hydro import susceptibility as S
    preds = [str(_raster_path(x)) for x in req.predictors]
    return jobs.submit("hydrosusceptibility", f"Flood susceptibility from {len(preds)} layers", {}, lambda job: _result(
        S.run(preds, req.floods, _out_dir(), non_floods=req.non_floods, ratio=req.ratio, buffer_m=req.buffer_m, model=req.model, block_m=req.block_m,
              folds=req.folds, name=_safe(req.name) or "flood_susceptibility"))).to_dict()
