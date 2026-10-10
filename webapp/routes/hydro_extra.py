"""Analysis ▸ Hydrology (second part): rainfall data, soil erosion (RUSLE), flood depth (FwDET), flood impact, check dams &
ponds, morphometry & prioritisation, groundwater potential, streamflow modelling (GR4J / ML / LSTM), design flood
hydrograph and 2D flood simulation; Fuzzy & suitability ▸ AHP; SAR ▸ flood map ML refinement. Background jobs writing to
analysis/; logic in lulc_fetch/hydro/, lulc_fetch/ahp.py and lulc_fetch/sar/floodml.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import core
from .. import workspace as ws
from ..core import jobs
from ..core import raster_path as _raster_path
from .geo_tools import Point, _out_dir, _points, _safe, _table

router = APIRouter()


def _res(r: dict, name: str = "", extra_tables=()) -> dict:
    out = {k: v for k, v in r.items() if k not in ("scored", "ok")}
    out["outputs"] = [ws.rel(x) for x in r.get("outputs", [])]
    for k in ("path", "classes_path"):
        if r.get(k):
            out[k] = ws.rel(r[k])
            if r[k] not in r.get("outputs", []):
                out["outputs"].append(ws.rel(r[k]))
    if r.get("csv"):
        out["csv"] = _table(r["csv"])
    for k in extra_tables:
        if r.get(k):
            out[k] = _table(r[k])
    if name:
        out["name"] = name
    return out


def _opt(p: str | None):
    return str(_raster_path(p)) if p else None


def _bbox(b: list[float]) -> tuple:
    w, s, e, n = b
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(400, "The area: west, south, east, north in degrees")
    return (w, s, e, n)


# ------------------------------------------------------------------ rainfall
class RainRequest(BaseModel):
    op: str = Field(pattern="^(total|annual|storms)$")
    bbox: list[float] | None = Field(None, min_length=4, max_length=4)
    point: Point | None = None
    start: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    first_year: int = Field(1991, ge=1940, le=2100)
    last_year: int = Field(2020, ge=1940, le=2100)
    name: str = Field("", max_length=80)


@router.post("/api/hydro/rainfall")
def hydro_rainfall(req: RainRequest):
    """CHIRPS rainfall total for dates or mean annual rainfall over years (a grid), or design storms at a point (ERA5)."""
    from lulc_fetch.hydro import rainfall as R
    if req.op in ("total", "annual") and not req.bbox:
        raise HTTPException(400, "Give the area")
    if req.op == "total" and not (req.start and req.end):
        raise HTTPException(400, "Give the start and end dates")
    if req.op == "storms" and not req.point:
        raise HTTPException(400, "Give the place (a point)")
    nm = _safe(req.name)

    def work(job):
        d = _out_dir()
        if req.op == "total":
            return _res(R.chirps_total(_bbox(req.bbox), req.start, req.end, d / f"{nm or 'rain_' + req.start + '_' + req.end}.tif"))
        if req.op == "annual":
            return _res(R.chirps_annual_mean(_bbox(req.bbox), req.first_year, req.last_year, d / f"{nm or f'rain_mean_{req.first_year}_{req.last_year}'}.tif"))
        p = _points([req.point], "Place", 1)[0]
        r = R.design_storms(p[0], p[1], req.first_year, req.last_year, d / f"{nm or 'design_storms'}.csv", d / f"{nm or 'daily_rain'}_series.csv")
        return _res(r, extra_tables=("series_csv",))
    title = {"total": f"Rainfall {req.start} → {req.end} (CHIRPS)", "annual": f"Mean annual rainfall {req.first_year}–{req.last_year} (CHIRPS)",
             "storms": f"Design storms {req.first_year}–{req.last_year}"}[req.op]
    return jobs.submit("hydrorain", title, {"op": req.op}, work).to_dict()


# ------------------------------------------------------------------ soil erosion
class ErosionRequest(BaseModel):
    dem: str
    rain_mm: float | None = Field(None, gt=0, le=20000)
    rain_raster: str | None = None
    r_raster: str | None = None
    k: float | None = Field(None, gt=0, le=1)
    k_raster: str | None = None
    texture: str | None = None
    landcover: str | None = None
    ndvi: str | None = None
    c_raster: str | None = None
    p: float = Field(1.0, gt=0, le=1)
    p_raster: str | None = None
    points: list[Point] = Field(default_factory=list, max_length=200)
    snap_m: float = Field(150, ge=0)
    name: str = Field("rusle", max_length=80)


@router.post("/api/hydro/erosion")
def hydro_erosion(req: ErosionRequest):
    """RUSLE soil loss (t/ha/yr) and FAO classes; per watershed of the points: soil loss and sediment yield."""
    from lulc_fetch.hydro import erosion as E
    p = _raster_path(req.dem)
    pts = _points(req.points, "Outlet points", 1) if req.points else None
    return jobs.submit("hydroerosion", f"Soil erosion (RUSLE) on {p.name}", {}, lambda job: _res(E.run(
        p, _out_dir(), rain_mm=req.rain_mm, rain_raster=_opt(req.rain_raster), r_raster=_opt(req.r_raster), k=req.k, k_raster=_opt(req.k_raster),
        texture=req.texture, landcover=_opt(req.landcover), ndvi=_opt(req.ndvi), c_raster=_opt(req.c_raster), p=req.p, p_raster=_opt(req.p_raster),
        points=pts, snap_m=req.snap_m, name=_safe(req.name) or "rusle"), "rusle_watersheds")).to_dict()


# ------------------------------------------------------------------ flood depth & impact
class DepthRequest(BaseModel):
    dem: str
    flood: str | None = None
    polygons: dict | None = None
    smooth_px: int = Field(3, ge=1, le=31)
    include_permanent: bool = False
    name: str = Field("flood", max_length=80)


@router.post("/api/hydro/flood-depth")
def hydro_flood_depth(req: DepthRequest):
    """Water depth of a flood extent on a DEM (FwDET 2.0)."""
    from lulc_fetch.hydro import floodimpact as FI
    p = _raster_path(req.dem)
    if not req.flood and not req.polygons:
        raise HTTPException(400, "Give the flood extent (a flood map raster or polygons)")
    return jobs.submit("hydrodepth", f"Flood depth on {p.name}", {}, lambda job: _res(FI.depth(
        p, _out_dir(), flood=_opt(req.flood), polygons=req.polygons, smooth_px=req.smooth_px, include_permanent=req.include_permanent,
        name=_safe(req.name) or "flood"))).to_dict()


class ImpactRequest(BaseModel):
    flood: str | None = None
    polygons: dict | None = None
    depth: str | None = None
    landcover: str | None = None
    population: str | None = None
    buildings: dict | None = None
    roads: dict | None = None
    name: str = Field("flood_impact", max_length=80)


@router.post("/api/hydro/flood-impact")
def hydro_flood_impact(req: ImpactRequest):
    """Land cover, people, buildings and roads under a flood (split by depth when a depth raster is given)."""
    from lulc_fetch.hydro import floodimpact as FI
    if not (req.flood or req.polygons or req.depth):
        raise HTTPException(400, "Give the flood (a flood map, polygons or a depth raster)")
    if not (req.landcover or req.population or req.buildings or req.roads):
        raise HTTPException(400, "Give at least one thing that can be flooded: land cover, population, buildings or roads")
    return jobs.submit("hydroimpact", "Flood impact", {}, lambda job: _res(FI.impact(
        _out_dir(), flood=_opt(req.flood), polygons=req.polygons, depth_raster=_opt(req.depth), landcover=_opt(req.landcover),
        population=_opt(req.population), buildings=req.buildings, roads=req.roads, name=_safe(req.name) or "flood_impact"), "flooded")).to_dict()


# ------------------------------------------------------------------ check dams & ponds
class StorageRequest(BaseModel):
    dem: str
    op: str = Field("sites", pattern="^(sites|curve)$")
    height_m: float = Field(3.0, gt=0, le=200)
    stream_km2: float = Field(0.5, gt=0)
    min_order: int = Field(1, ge=1, le=12)
    max_order: int = Field(3, ge=1, le=12)
    max_area_km2: float | None = Field(None, gt=0)
    max_slope_pct: float = Field(5.0, gt=0, le=100)
    spacing_m: float = Field(300, ge=0)
    top: int = Field(50, ge=1, le=500)
    point: Point | None = None
    step_m: float = Field(0.5, gt=0, le=50)
    snap_m: float = Field(100, ge=0)
    name: str = Field("", max_length=80)


@router.post("/api/hydro/storage")
def hydro_storage(req: StorageRequest):
    """Check dam / pond sites ranked by water held per metre of dam, or the area–capacity curve of a site."""
    from lulc_fetch.hydro import storage as ST
    p = _raster_path(req.dem)
    if req.op == "curve" and not req.point:
        raise HTTPException(400, "Give the dam site (a point)")
    if req.op == "sites":
        return jobs.submit("hydrostorage", f"Check dam & pond sites on {p.name}", {}, lambda job: _res(ST.sites(
            p, _out_dir(), height_m=req.height_m, stream_km2=req.stream_km2, min_order=req.min_order, max_order=req.max_order,
            max_area_km2=req.max_area_km2, max_slope_pct=req.max_slope_pct, spacing_m=req.spacing_m, top=req.top, name=_safe(req.name) or None), "dam_sites")).to_dict()
    pt = _points([req.point], "Site", 1)[0]
    return jobs.submit("hydrostorage", f"Area–capacity curve on {p.name}", {}, lambda job: _res(ST.area_capacity(
        p, _out_dir(), point=pt, max_height_m=req.height_m, step_m=req.step_m, snap_m=req.snap_m, name=_safe(req.name) or None), "reservoir")).to_dict()


# ------------------------------------------------------------------ morphometry
class MorphRequest(BaseModel):
    dem: str
    mode: str = Field("subbasins", pattern="^(subbasins|points)$")
    points: list[Point] = Field(default_factory=list, max_length=500)
    snap_m: float = Field(150, ge=0)
    stream_km2: float = Field(0.5, gt=0)
    basin_km2: float = Field(5.0, gt=0)
    name: str = Field("morphometry", max_length=80)


@router.post("/api/hydro/morphometry")
def hydro_morphometry(req: MorphRequest):
    """Morphometric parameters of sub-watersheds, hypsometric curves and conservation priority (compound value)."""
    from lulc_fetch.hydro import morphometry as M
    p = _raster_path(req.dem)
    pts = _points(req.points, "Outlet points", 1) if req.mode == "points" else None
    return jobs.submit("hydromorph", f"Morphometry of {p.name}", {}, lambda job: _res(M.run(
        p, _out_dir(), mode=req.mode, points=pts, snap_m=req.snap_m, stream_km2=req.stream_km2, basin_km2=req.basin_km2,
        name=_safe(req.name) or "morphometry"), "morphometry", extra_tables=("curves_csv",))).to_dict()


# ------------------------------------------------------------------ groundwater
class GroundwaterRequest(BaseModel):
    dem: str
    rain_raster: str | None = None
    landcover: str | None = None
    lineaments: dict | None = None
    geology: str | None = None
    geology_scores: dict[str, float] | None = None
    soil: str | None = None
    soil_scores: dict[str, float] | None = None
    stream_km2: float = Field(0.5, gt=0)
    radius_m: float = Field(1000, gt=0, le=50_000)
    weights: dict[str, float] | None = None
    wells: dict | None = None
    wells_field: str | None = None
    name: str = Field("groundwater", max_length=80)


@router.post("/api/hydro/groundwater")
def hydro_groundwater(req: GroundwaterRequest):
    """Groundwater potential zones from thematic layers weighted by AHP; checked against wells when given."""
    from lulc_fetch.hydro import groundwater as GW
    p = _raster_path(req.dem)
    return jobs.submit("hydrogw", f"Groundwater potential on {p.name}", {}, lambda job: _res(GW.run(
        p, _out_dir(), rain_raster=_opt(req.rain_raster), landcover=_opt(req.landcover), lineaments=req.lineaments, geology=_opt(req.geology),
        geology_scores=req.geology_scores, soil=_opt(req.soil), soil_scores=req.soil_scores, stream_km2=req.stream_km2, radius_m=req.radius_m,
        weights=req.weights, wells=req.wells, wells_field=req.wells_field, name=_safe(req.name) or "groundwater"))).to_dict()


# ------------------------------------------------------------------ streamflow modelling
class StreamflowRequest(BaseModel):
    table: str
    date_col: str | None = None
    flow_col: str | None = None
    units: str = Field("m3s", pattern="^(m3s|mm)$")
    area_km2: float | None = Field(None, gt=0)
    point: Point | None = None
    cal_frac: float = Field(0.7, ge=0.3, le=0.9)
    models: list[str] = Field(default_factory=lambda: ["gr4j", "lgbm"])
    past_flow: bool = False
    name: str = Field("streamflow", max_length=80)


@router.post("/api/hydro/streamflow")
def hydro_streamflow(req: StreamflowRequest):
    """Daily streamflow models (GR4J, LightGBM / Random Forest, LSTM) calibrated on observed flow and scored on later years."""
    from lulc_fetch.hydro import streamflow as SF
    t = core.table_path(req.table)
    if not req.models or set(req.models) - {"gr4j", "lgbm", "rf", "lstm"}:
        raise HTTPException(400, "models: gr4j, lgbm, rf, lstm")
    runner = None
    if "lstm" in req.models:
        core.require_dl()
        from lulc_fetch import dlrunner

        def runner(npz, out_npz):
            return dlrunner.run("call", target="lstm_streamflow", npz=npz, out_npz=out_npz)
    pt = _points([req.point], "Place", 1)[0] if req.point else (None, None)
    return jobs.submit("hydrostreamflow", f"Streamflow models ({', '.join(m.upper() for m in req.models)})", {}, lambda job: _res(SF.run(
        str(t), _out_dir(), area_km2=req.area_km2, lon=pt[0], lat=pt[1], date_col=req.date_col, flow_col=req.flow_col, units=req.units,
        cal_frac=req.cal_frac, models=req.models, past_flow=req.past_flow, lstm_runner=runner, name=_safe(req.name) or "streamflow"),
        extra_tables=("scores_csv",))).to_dict()


# ------------------------------------------------------------------ design flood hydrograph
class HydrographRequest(BaseModel):
    dem: str
    point: Point
    storms_mm: list[float] = Field(min_length=1, max_length=12)
    labels: list[str] | None = None
    cn: float | None = Field(None, gt=0, le=100)
    landcover: str | None = None
    soil: str = Field("B", pattern="^[ABCDabcd]$")
    condition: str = Field("II", pattern="^(I|II|III)$")
    pattern: str = Field("scs2", pattern="^(scs2|uniform)$")
    duration_h: float = Field(24, gt=0, le=240)
    dt_h: float = Field(0.25, gt=0.01, le=6)
    tc_h: float | None = Field(None, gt=0)
    snap_m: float = Field(150, ge=0)
    name: str = Field("design_flood", max_length=80)


@router.post("/api/hydro/hydrograph")
def hydro_hydrograph(req: HydrographRequest):
    """Flow at a watershed outlet through design storms: SCS-CN excess rain and the SCS unit hydrograph."""
    from lulc_fetch.hydro import hydrograph as HG
    p = _raster_path(req.dem)
    pt = _points([req.point], "Outlet", 1)[0]
    return jobs.submit("hydrohydrograph", f"Design flood hydrograph on {p.name}", {}, lambda job: _res(HG.run(
        p, _out_dir(), point=pt, storms_mm=req.storms_mm, labels=req.labels, cn=req.cn, landcover=_opt(req.landcover), soil=req.soil.upper(),
        condition=req.condition, duration_h=req.duration_h, pattern=req.pattern, dt_h=req.dt_h, tc_h=req.tc_h, snap_m=req.snap_m,
        name=_safe(req.name) or "design_flood"), "design_flood_watershed")).to_dict()


# ------------------------------------------------------------------ 2D flood simulation
class Inflow(BaseModel):
    lon: float
    lat: float
    q: float | list[list[float]]


class SimRequest(BaseModel):
    dem: str
    inflows: list[Inflow] = Field(default_factory=list, max_length=50)
    rain_mm_h: float | list[list[float]] | None = None
    hours: float = Field(6, gt=0, le=240)
    manning: float = Field(0.035, gt=0, le=1)
    landcover: str | None = None
    start_depth: str | None = None
    snapshots: int = Field(6, ge=0, le=48)
    name: str = Field("flood_sim", max_length=80)


@router.post("/api/hydro/flood-sim")
def hydro_flood_sim(req: SimRequest):
    """2D flood simulation (local inertial, as LISFLOOD-FP) from inflows and / or rain."""
    from lulc_fetch.hydro import flood2d as F
    p = _raster_path(req.dem)
    return jobs.submit("hydrosim", f"Flood simulation {req.hours:g} h on {p.name}", {}, lambda job: _res(F.simulate(
        p, _out_dir(), inflows=[i.model_dump() for i in req.inflows], rain_mm_h=req.rain_mm_h, hours=req.hours, manning=req.manning,
        landcover=_opt(req.landcover), start_depth=_opt(req.start_depth), snapshots=req.snapshots, name=_safe(req.name) or "flood_sim"))).to_dict()


# ------------------------------------------------------------------ AHP
class AhpWeightsRequest(BaseModel):
    matrix: list[list[float]]


@router.post("/api/ahp/weights")
def ahp_weights(req: AhpWeightsRequest):
    """Weights and consistency of a pairwise comparison matrix (instant, not a job)."""
    from lulc_fetch import ahp
    try:
        return ahp.weights(req.matrix)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class AhpFactor(BaseModel):
    path: str
    band: int = Field(1, ge=1)
    name: str | None = None
    rising: bool = True
    scores: dict[str, float] | None = None


class AhpOverlayRequest(BaseModel):
    factors: list[AhpFactor] = Field(min_length=2, max_length=15)
    matrix: list[list[float]] | None = None
    weights: list[float] | None = None
    title: str = Field("Suitability", max_length=60)
    name: str = Field("ahp_suitability", max_length=80)


@router.post("/api/ahp/overlay")
def ahp_overlay(req: AhpOverlayRequest):
    """AHP weights (from the matrix, or given) and the weighted overlay of the scored factors."""
    from lulc_fetch import ahp
    if not req.matrix and not req.weights:
        raise HTTPException(400, "Give the comparison matrix or the weights")
    facs = [{**f.model_dump(), "path": str(_raster_path(f.path))} for f in req.factors]
    try:
        info = ahp.weights(req.matrix) if req.matrix else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    w = info["weights"] if info else [x / sum(req.weights) for x in req.weights]
    if len(w) != len(facs):
        raise HTTPException(400, "One comparison row / weight per factor")

    def work(job):
        r = ahp.overlay(facs, w, _out_dir() / f"{_safe(req.name) or 'ahp_suitability'}.tif", title=req.title)
        return {**_res(r), "weights": w, **({k: info[k] for k in ("cr", "ci", "lambda_max", "consistent")} if info else {})}
    return jobs.submit("ahp", f"AHP overlay of {len(facs)} factors", {}, work).to_dict()


# ------------------------------------------------------------------ SAR flood ML refinement
class FloodMLRequest(BaseModel):
    sar: str
    confidence: str | None = None
    classes: str | None = None
    pre: str | None = None
    dem: str | None = None
    model: str = Field("lgbm", pattern="^(lgbm|unet)$")
    hi: float = Field(0.75, gt=0.5, lt=1)
    lo: float = Field(0.25, gt=0, lt=0.5)
    epochs: int = Field(30, ge=1, le=300)
    name: str = Field("flood_ml", max_length=80)


@router.post("/api/sar/flood-ml")
def sar_flood_ml(req: FloodMLRequest):
    """The SAR flood map refined by a model trained on its own confident pixels (LightGBM or a U-Net)."""
    from lulc_fetch.sar import floodml as FM
    s = _raster_path(req.sar)
    if not req.confidence and not req.classes:
        raise HTTPException(400, "Give the Flood & water map's confidence or classes layer")
    runner = None
    if req.model == "unet":
        core.require_dl()
        from lulc_fetch import dlrunner

        def runner(npz, out_npz, epochs):
            return dlrunner.run("call", target="unet_flood", npz=npz, out_npz=out_npz, epochs=epochs)
    return jobs.submit("sarfloodml", f"Flood map ML refinement ({'U-Net' if req.model == 'unet' else 'LightGBM'}) of {s.name}", {}, lambda job: _res(FM.refine(
        str(s), _out_dir(), confidence=_opt(req.confidence), classes=_opt(req.classes), pre=_opt(req.pre), dem=_opt(req.dem), model=req.model,
        hi=req.hi, lo=req.lo, unet_runner=runner, epochs=req.epochs, name=_safe(req.name) or "flood_ml"))).to_dict()
