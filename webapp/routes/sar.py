"""Analysis ▸ SAR: Sentinel-1 product inspector, scene search (Planetary Computer), the SAR workflow (preprocessing
steps ticked by the user, from a .SAFE / .zip, a Planetary Computer GRD or RTC scene, or a processed SAR raster),
speckle filtering, features, time series and change. Background jobs writing to analysis/. Logic in lulc_fetch/sar/."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs
from ..core import raster_path as _raster_path

router = APIRouter()
STEPS = r"^(validate|orbit|border|thermal|calibrate|speckle|flatten|terrain|db|reproject|resample|clip)$"


def _cache() -> Path:
    return ws.APP_DIR / "sar_cache"   # the geoid grid and precise orbits, downloaded once


def _out(name: str = "sar") -> Path:
    d = ws.root() / "analysis" / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s or "").strip("_")[:60]


class Source(BaseModel):
    product: str | None = Field(None, max_length=2000)   # a .SAFE / .zip in the data folder (its path from /api/products)
    scene: str | None = Field(None, max_length=200)      # a Planetary Computer scene id (GRD, or …_rtc)
    raster: str | None = Field(None, max_length=2000)    # a processed SAR GeoTIFF in the workspace


def _resolve(src: Source):
    if src.product:
        from .safe_products import _product
        return _product(src.product)["path"]
    if src.scene:
        if not re.fullmatch(r"S1[ABCD]_[A-Z0-9_]+", src.scene):
            raise HTTPException(400, "Not a Sentinel-1 scene id")
        return src.scene
    if src.raster:
        return str(_raster_path(src.raster))
    raise HTTPException(400, "Choose the SAR data: a product, a scene, or a SAR layer")


@router.post("/api/sar/inspect")
def sar_inspect(src: Source):
    """What the data is and the status of every processing step (done / needed / optional / not applicable)."""
    from lulc_fetch.sar import product as P
    s = _resolve(src)
    try:
        return P.inspect_raster(s) if src.raster else P.inspect(s)
    except Exception as e:   # noqa: BLE001 (an unreadable product is reported, not a crash)
        raise HTTPException(400, f"Couldn't read it: {str(e)[:300]}")


class SearchRequest(BaseModel):
    aoi: dict
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    collection: str = Field("rtc", pattern="^(rtc|grd)$")
    pols: str = Field("VV+VH", pattern=r"^(VV\+VH|HH\+HV|VV|HH|)$")
    mode: str = Field("IW", pattern="^(IW|EW|SM)$")
    orbit: str | None = Field(None, pattern="^(ascending|descending)$")
    rel_orbit: int | None = Field(None, ge=1, le=175)


@router.post("/api/sar/search")
def sar_search(req: SearchRequest):
    """Sentinel-1 scenes on Planetary Computer over the area, filtered to a homogeneous series."""
    from lulc_fetch.sar import pipeline
    try:
        scenes = pipeline.search(req.aoi, req.start, req.end, collection=req.collection, pols=req.pols, mode=req.mode, orbit=req.orbit, rel_orbit=req.rel_orbit)
    except Exception as e:   # noqa: BLE001 (the catalogue's answer, said plainly)
        raise HTTPException(502, f"Planetary Computer search failed: {str(e)[:300]}")
    orbits = sorted({(s["orbit_direction"], s["relative_orbit"]) for s in scenes}, key=str)
    return {"scenes": [{k: v for k, v in s.items() if k != "item"} for s in scenes], "orbits": [{"direction": d, "relative": r} for d, r in orbits]}


class Speckle(BaseModel):
    method: str = Field("none", pattern="^(none|boxcar|median|lee|refined_lee|lee_sigma|frost|gamma_map)$")
    size: int = Field(5, ge=3, le=15)
    looks: float | None = Field(None, gt=0, le=1000)


class ProcessRequest(BaseModel):
    sources: list[Source] = Field(min_length=1, max_length=40)   # several: a time series, each processed the same way
    steps: list[str] = Field(default_factory=list, max_length=12)
    pols: list[str] | None = None
    kind: str = Field("sigma0", pattern="^(sigma0|beta0|gamma0)$")
    speckle: Speckle = Field(default_factory=Speckle)
    dem: str | None = None                                        # a DEM in the workspace (else Copernicus DEM, downloaded)
    crs: str | None = Field("auto", max_length=200)
    res: float | None = Field(None, gt=0, le=5000)
    like: str | None = None                                       # align to this raster's grid
    aoi: dict | None = None
    clip: bool = False
    db: bool = True
    masks: bool = False                                           # incidence angles and layover / shadow
    temporal: list[str] = Field(default_factory=list)             # after processing several dates: mean, median, …, trend
    change: bool = False                                          # first → last date: log-ratio, classes, flooding
    change_threshold: float = Field(3.0, gt=0, le=20)
    water_db: float = Field(-18.0, ge=-40, le=0)
    features: list[str] = Field(default_factory=list)             # ratio, cross_ratio, rvi, ndpi, span, texture
    name: str = Field("", max_length=80)


@router.post("/api/sar/process")
def sar_process(req: ProcessRequest):
    """The SAR workflow: every source processed with the ticked steps (skipping what its producer already did), then
    the ticked analysis (features of each, time-series statistics and change across them)."""
    from lulc_fetch import progress
    from lulc_fetch.sar import analysis as AN
    from lulc_fetch.sar import pipeline as PL
    bad = [s for s in req.steps if not re.match(STEPS, s)]
    if bad:
        raise HTTPException(400, f"Unknown steps: {', '.join(bad)}")
    if ("clip" in req.steps or req.clip) and not req.aoi:
        raise HTTPException(400, "Clipping needs an area")
    srcs = [_resolve(s) for s in req.sources]
    opts = {"steps": req.steps, "pols": req.pols, "kind": req.kind, "speckle": req.speckle.model_dump(), "dem": str(_raster_path(req.dem)) if req.dem else None,
            "crs": req.crs, "res": req.res, "like": str(_raster_path(req.like)) if req.like else None, "aoi": req.aoi, "clip": req.clip or "clip" in req.steps,
            "db": req.db, "masks": req.masks}
    if req.change and len(srcs) < 2:
        raise HTTPException(400, "Change detection needs two dates: add a second scene or layer")
    if req.temporal and len(srcs) < 2:
        raise HTTPException(400, "Time-series statistics need at least two dates")

    def work(job):
        out = _out()
        results, outputs = [], []
        for k, s in enumerate(srcs):
            with progress.span(k / len(srcs) * 0.85, (k + 1) / len(srcs) * 0.85):
                nm = _safe(req.name) + (f"_{k + 1}" if len(srcs) > 1 and req.name else "") if req.name else None
                r = PL.process(s, out, {**opts, "name": nm}, _cache())
            results.append(r)
            outputs += r["outputs"]
        lin = [next(o for o in r["outputs"] if o.endswith("_linear.tif")) for r in results]
        extra = {}
        if req.features:
            for p in lin:
                f = AN.features(p, Path(p).with_name(Path(p).stem.replace("_linear", "") + "_features.tif"), req.features)
                outputs.append(f["path"])
        if req.temporal:
            t = AN.temporal(lin, out / f"{_safe(req.name) or 'sar'}_timeseries.tif", req.temporal)
            outputs.append(t["path"])
            extra["dates"] = t["dates"]
        if req.change:
            c = AN.change(lin[0], lin[-1], out, threshold_db=req.change_threshold, water_db=req.water_db)
            outputs += c["outputs"]
            extra.update(change=c["classes"], flood_ha=c["flood_ha"], mean_change_db=c["mean_change_db"])
        summary = [{k: r.get(k) for k in ("steps_done", "units", "date", "orbit", "orbit_note", "dem", "speckle", "grid", "looks")} for r in results]
        return {"outputs": [ws.rel(o) for o in outputs], "runs": summary, **extra}
    title = f"SAR workflow · {len(srcs)} input{'s' if len(srcs) > 1 else ''}" + (f" · {', '.join(s for s in req.steps if s != 'validate')[:60]}" if req.steps else "")
    return jobs.submit("sar", title, {"steps": req.steps}, work).to_dict()


class FeatureRequest(BaseModel):
    raster: str
    features: list[str] = Field(default_factory=lambda: ["ratio", "rvi", "ndpi"])
    texture_band: str = Field("VV", pattern="^(VV|VH|HH|HV)$")
    window: int = Field(7, ge=3, le=31)
    name: str = Field("", max_length=80)


@router.post("/api/sar/features")
def sar_features(req: FeatureRequest):
    from lulc_fetch.sar import analysis as AN
    src = _raster_path(req.raster)
    return jobs.submit("sar", f"SAR features of {src.name}", {}, lambda job: {"outputs": [ws.rel(
        AN.features(src, _out() / f"{_safe(req.name) or src.stem}_features.tif", req.features, req.texture_band, req.window)["path"])]}).to_dict()


class SeriesRequest(BaseModel):
    rasters: list[str] = Field(min_length=2, max_length=200)
    stats: list[str] = Field(default_factory=lambda: ["mean", "median", "min", "max", "std", "count", "trend"])
    change: bool = False
    pol: str = Field("VV", pattern="^(VV|VH|HH|HV)$")
    threshold_db: float = Field(3.0, gt=0, le=20)
    water_db: float = Field(-18.0, ge=-40, le=0)
    name: str = Field("", max_length=80)


@router.post("/api/sar/series")
def sar_series(req: SeriesRequest):
    """Time-series statistics of several dates, and the change from the first to the last (log-ratio, classes, flooding)."""
    from lulc_fetch.sar import analysis as AN
    paths = [str(_raster_path(r)) for r in req.rasters]

    def work(job):
        out, outputs, extra = _out(), [], {}
        if req.stats:
            t = AN.temporal(paths, out / f"{_safe(req.name) or 'sar'}_timeseries.tif", req.stats)
            outputs.append(t["path"])
            extra["dates"] = t["dates"]
        if req.change:
            c = AN.change(paths[0], paths[-1], out, pol=req.pol, threshold_db=req.threshold_db, water_db=req.water_db)
            outputs += c["outputs"]
            extra.update(change=c["classes"], flood_ha=c["flood_ha"], mean_change_db=c["mean_change_db"])
        return {"outputs": [ws.rel(o) for o in outputs], **extra}
    return jobs.submit("sar", f"SAR time series of {len(paths)} dates", {}, work).to_dict()
