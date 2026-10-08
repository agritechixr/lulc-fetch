"""Raster and terrain tools (Analysis ▸ Tools ▸ Raster & terrain): slope / aspect / hillshade, contours, reclassify,
change detection, clip / mask by polygons; and from Imagery: mosaic (join tiles / scenes) and burn severity (dNBR).
Background jobs (progress, History, Workflows, the Assistant) writing to analysis/. Logic in lulc_fetch/raster_ops.py,
mosaic.py, burn.py."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs
from ..core import raster_path as _raster_path

router = APIRouter()


def _out_dir() -> Path:
    d = ws.root() / "analysis" / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "result"


class TerrainRequest(BaseModel):
    dem: str
    products: list[str] = Field(default_factory=lambda: ["slope", "aspect", "hillshade"])
    azimuth: float = Field(315, ge=0, le=360)
    altitude: float = Field(45, ge=1, le=90)
    z_factor: float = Field(1.0, gt=0)


@router.post("/api/raster/terrain")
def raster_terrain(req: TerrainRequest):
    """Slope (degrees), aspect (degrees from north) and hillshade of a DEM."""
    from lulc_fetch import raster_ops
    p = _raster_path(req.dem)
    if not req.products or set(req.products) - {"slope", "aspect", "hillshade"}:
        raise HTTPException(400, "products: slope, aspect, hillshade")
    return jobs.submit("terrain", f"Terrain of {p.name}", {"products": req.products}, lambda job: {
        "outputs": [ws.rel(x) for x in raster_ops.terrain(p, _out_dir(), products=req.products, azimuth=req.azimuth, altitude=req.altitude, z_factor=req.z_factor)]}).to_dict()


class ContourRequest(BaseModel):
    dem: str
    interval: float = Field(gt=0)
    base: float = 0
    band: int = Field(1, ge=1)
    name: str = Field("contours", max_length=80)


@router.post("/api/raster/contours")
def raster_contours(req: ContourRequest):
    """Contour lines every `interval` (e.g. 10 m) of a DEM, as a vector layer."""
    from lulc_fetch import raster_ops
    p = _raster_path(req.dem)

    def work(job):
        fc = raster_ops.contours(p, req.interval, base=req.base, band=req.band)
        out = _out_dir() / f"{_safe(req.name)}.geojson"
        out.write_text(json.dumps(fc), encoding="utf-8")
        return {"path": ws.rel(out), "features": len(fc["features"]), "name": _safe(req.name)}
    return jobs.submit("contours", f"Contours every {req.interval:g} of {p.name}", {"interval": req.interval}, work).to_dict()


class ReclassRule(BaseModel):
    min: float | None = None
    max: float | None = None
    value: int = Field(ge=1, le=255)
    label: str = Field("", max_length=80)


class ReclassRequest(BaseModel):
    raster: str
    rules: list[ReclassRule] = Field(min_length=1, max_length=50)
    band: int = Field(1, ge=1)
    name: str = Field("classes", max_length=80)


@router.post("/api/raster/reclassify")
def raster_reclassify(req: ReclassRequest):
    """Ranges of values become classes, e.g. NDVI < 0.2 → 1 (bare), 0.2–0.5 → 2 (sparse), ≥ 0.5 → 3 (dense)."""
    from lulc_fetch import raster_ops
    p = _raster_path(req.raster)
    return jobs.submit("reclassify", f"Reclassify {p.name}", {"classes": len(req.rules)}, lambda job: {
        "path": ws.rel(raster_ops.reclassify(p, _out_dir() / f"{_safe(req.name)}.tif", [r.model_dump() for r in req.rules], band=req.band))}).to_dict()


class ChangeRequest(BaseModel):
    before: str
    after: str
    band: int = Field(1, ge=1)
    categorical: bool = False
    resampling: str | None = Field(None, pattern=r"^(nearest|bilinear|cubic|bicubic|cubic_spline|lanczos|average|mode|min|max|med|q1|q3)$")  # the after raster onto the before's grid (classes: nearest)


@router.post("/api/raster/change")
def raster_change(req: ChangeRequest):
    """Change detection between two dates: the difference and % change, or for class maps from → to with the area of
    each change (a table)."""
    from lulc_fetch import raster_ops
    a, b = _raster_path(req.before), _raster_path(req.after)

    def work(job):
        r = raster_ops.change(a, b, _out_dir(), band=req.band, categorical=req.categorical, resampling=req.resampling)
        out = {"outputs": [ws.rel(x) for x in r["paths"]], "summary": r["summary"]}
        if r.get("transitions"):
            import csv
            t = ws.root() / "tables" / f"{_safe(Path(r['paths'][0]).stem)}_{uuid.uuid4().hex[:4]}.csv"
            t.parent.mkdir(parents=True, exist_ok=True)
            with open(t, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["from", "to", "pixels", "area_ha", "changed"])
                w.writeheader(); w.writerows(r["transitions"])
            out.update(csv=ws.rel(t), transitions=r["transitions"][:30])
        return out
    return jobs.submit("change", f"Change {a.name} → {b.name}", {"categorical": req.categorical}, work).to_dict()


_METHOD = r"^(nearest|bilinear|cubic|bicubic|cubic_spline|lanczos|average|mode|min|max|med|q1|q3)$"


@router.get("/api/raster/resampling-methods")
def resampling_methods():
    """The resampling methods tools accept, with what each is for."""
    from lulc_fetch import resample
    return {"methods": resample.describe()}


class ResampleRequest(BaseModel):
    raster: str
    res: float | None = Field(None, gt=0)          # pixel size in the target CRS's units (metres for UTM)
    scale: float | None = Field(None, gt=0, le=16)  # 2 = pixels twice as small, 0.5 = twice as big
    crs: str | None = Field(None, pattern=r"^EPSG:\d{4,6}$")
    method: str | None = Field(None, pattern=_METHOD)
    name: str = Field("resampled", max_length=80)


@router.post("/api/raster/resample")
def raster_resample(req: ResampleRequest):
    """A raster on a new pixel size, scale or CRS, with a chosen method (nearest, bilinear, cubic, lanczos, average,
    mode…; by default nearest / mode for class maps, bilinear / average for values)."""
    from lulc_fetch import enhance
    p = _raster_path(req.raster)
    if not (req.res or req.scale or req.crs):
        raise HTTPException(400, "Give a pixel size, a scale factor or a coordinate system")
    return jobs.submit("resample", f"Resample {p.name}", {"method": req.method or "auto"}, lambda job: {
        **(r := enhance.resample_raster(p, _out_dir() / f"{_safe(req.name)}.tif", res=req.res, scale=req.scale, crs=req.crs, method=req.method)),
        "path": ws.rel(r["path"])}).to_dict()


class EnhanceStep(BaseModel):
    op: str = Field(pattern=r"^(stretch|equalize|clahe|gamma|median|gaussian|sharpen|sobel|laplacian|focal_mean|focal_std|focal_min|focal_max|majority)$")
    size: int = Field(3, ge=1, le=31)
    sigma: float = Field(1.0, gt=0, le=20)
    amount: float = Field(1.0, ge=0, le=10)
    gamma: float = Field(1.2, gt=0, le=10)
    low: float = Field(2, ge=0, le=50)
    high: float = Field(98, ge=50, le=100)
    tiles: int = Field(8, ge=1, le=64)
    clip: float = Field(0.01, gt=0, le=1)


class EnhanceRequest(BaseModel):
    raster: str
    steps: list[EnhanceStep] = Field(default_factory=list, max_length=12)
    bands: list[int] | None = None
    upscale: int = Field(1, ge=1, le=4)
    upscale_method: str = Field("cubic", pattern=_METHOD)
    name: str = Field("enhanced", max_length=80)


@router.post("/api/raster/enhance")
def raster_enhance(req: EnhanceRequest):
    """Image enhancement for computer vision and embeddings: contrast stretch, histogram equalisation, CLAHE, gamma,
    denoise (median, gaussian), sharpen, edges (sobel, laplacian), focal statistics, majority filter for class maps,
    and upscaling ×2 / ×4 with cubic or lanczos."""
    from lulc_fetch import enhance
    p = _raster_path(req.raster)
    if not req.steps and req.upscale == 1:
        raise HTTPException(400, "Choose at least one step")
    if req.upscale not in (1, 2, 4):
        raise HTTPException(400, "upscale: 1, 2 or 4")
    return jobs.submit("enhance", f"Enhance {p.name}", {"steps": [s.op for s in req.steps], "upscale": req.upscale}, lambda job: {
        **(r := enhance.enhance(p, _out_dir() / f"{_safe(req.name)}.tif", [s.model_dump() for s in req.steps], bands=req.bands,
                                upscale=req.upscale, upscale_method=req.upscale_method)),
        "path": ws.rel(r["path"])}).to_dict()


class ClipRasterRequest(BaseModel):
    raster: str
    area: dict
    crop: bool = True
    invert: bool = False
    name: str = Field("clipped", max_length=80)


@router.post("/api/raster/clip")
def raster_clip(req: ClipRasterRequest):
    """Cut a raster to polygons (outside becomes nodata; crop shrinks it to them; invert keeps the outside)."""
    from lulc_fetch import raster_ops
    p = _raster_path(req.raster)
    return jobs.submit("rclip", f"Clip {p.name}", {"invert": req.invert}, lambda job: {
        "path": ws.rel(raster_ops.clip_raster(p, _out_dir() / f"{_safe(req.name)}.tif", req.area, crop=req.crop, invert=req.invert))}).to_dict()


class MosaicRequest(BaseModel):
    rasters: list[str] = Field(min_length=2, max_length=200)   # in order: with first / last on top, the order counts
    method: str = Field("blend", pattern=r"^(blend|first|last|mean|median|min|max|mode)$")
    balance: str = Field("overlap", pattern=r"^(overlap|none)$")   # colour balance across overlaps
    categorical: bool = False                                     # class maps: nearest, no blending (first / last / mode)
    blend_px: int = Field(64, ge=1, le=2000)                      # width of the smooth seam, in output pixels
    res: float | None = Field(None, gt=0)                         # output pixel size (default: the first raster's)
    crs: str | None = Field(None, max_length=200)                 # output CRS (default: the first raster's)
    resampling: str | None = Field(None, pattern=_METHOD)
    name: str = Field("mosaic", max_length=80)


@router.post("/api/raster/mosaic")
def raster_mosaic(req: MosaicRequest):
    """Join neighbouring tiles or scenes into one image: smooth blended seams and colour balance by default, or first /
    last on top, mean, median, min, max (mode for class maps). Logic in lulc_fetch/mosaic.py."""
    from lulc_fetch import mosaic
    paths = [_raster_path(r) for r in req.rasters]
    if len(set(paths)) < 2:
        raise HTTPException(400, "Choose at least two different rasters")

    def work(job):
        r = mosaic.mosaic(paths, _out_dir() / f"{_safe(req.name)}.tif", method=req.method, balance=req.balance, categorical=req.categorical,
                          blend_px=req.blend_px, res=req.res, crs=req.crs, resampling=req.resampling)
        return {**r, "path": ws.rel(r["path"])}
    return jobs.submit("mosaic", f"Mosaic of {len(paths)} rasters ({req.method})", {"method": req.method, "images": len(paths)}, work).to_dict()


class BurnRequest(BaseModel):
    before: str
    after: str
    before_bands: dict[str, int] = Field(default_factory=dict)   # band map (B08, B12 …) of each image; empty: detected
    after_bands: dict[str, int] = Field(default_factory=dict)
    before_scale: list[float] | None = Field(None, min_length=2, max_length=2)   # [scale, offset] to reflectance; None: detected
    after_scale: list[float] | None = Field(None, min_length=2, max_length=2)
    breaks: list[float] | None = Field(None, min_length=6, max_length=6)   # own dNBR class limits (default USGS)
    min_post_nbr: float | None = Field(None, ge=-1, le=1)                   # burned only where NBR after is below this
    resampling: str | None = Field(None, pattern=_METHOD)
    name: str = Field("", max_length=80)


@router.post("/api/raster/burn")
def raster_burn(req: BurnRequest):
    """Burn severity: dNBR (NBR before − NBR after) and its USGS severity classes, with the burned area. Logic in
    lulc_fetch/burn.py."""
    import csv

    from lulc_fetch import burn
    a, b = _raster_path(req.before), _raster_path(req.after)
    if a == b:
        raise HTTPException(400, "Choose two different images: before and after")

    def work(job):
        r = burn.burn_severity(a, b, _out_dir(), pre_map=req.before_bands or None, post_map=req.after_bands or None,
                               pre_scale=tuple(req.before_scale) if req.before_scale else None,
                               post_scale=tuple(req.after_scale) if req.after_scale else None, breaks=req.breaks,
                               min_post_nbr=req.min_post_nbr, resampling=req.resampling, name=_safe(req.name) if req.name.strip() else None)
        t = ws.root() / "tables" / f"{_safe(Path(r['paths'][0]).stem)}_{uuid.uuid4().hex[:4]}.csv"
        t.parent.mkdir(parents=True, exist_ok=True)
        with open(t, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["value", "class", "pixels", "area_ha", "pct"], extrasaction="ignore")
            w.writeheader()
            w.writerows(r["classes"])
        return {"outputs": [ws.rel(x) for x in r["paths"]], "csv": ws.rel(t), "classes": r["classes"], "summary": r["summary"]}
    return jobs.submit("burn", f"Burn severity {a.name} → {b.name}", {}, work).to_dict()
