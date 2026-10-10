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


@router.get("/api/raster/watermask/bands")
def watermask_bands(path: str):
    """Which band is blue / green / red / NIR / SWIR1 / SWIR2 (from the names and the sensor), for the band pickers."""
    from lulc_fetch import watermask as WM
    g = WM.guess_bands(_raster_path(path))
    from lulc_fetch import thresholds as TH
    return {**g, "indices": {k: {"title": t, "needs": list(n), "about": a} for k, (t, n, a) in WM.INDICES.items()},
            "thresholds": {"global": list(TH.GLOBAL), "local": list(TH.LOCAL), "fuzzy": list(TH.FUZZY), "titles": TH.TITLES}}


class WaterMaskRequest(BaseModel):
    path: str
    bands: dict[str, int] = Field(default_factory=dict)          # {"green": 3, "nir": 8, …}: overrides the guess
    index: str = Field("auto", pattern="^(auto|mndwi|ndwi|awei_nsh|awei_sh|wi2015)$")
    threshold: str = Field("zero", pattern="^(zero|auto|otsu|multi_otsu|li|yen|kapur|triangle|isodata|niblack|sauvola|wolf|phansalkar|fcm|fuzzy|membership)$")   # 0, or an automatic method (lulc_fetch/thresholds.py)
    window: int = Field(51, ge=5, le=1001)                          # local methods: the neighbourhood (pixels)
    k: float | None = Field(None, ge=-2, le=2)                      # local methods: their k (None: the method's usual)
    cloud: str | None = None                                      # a cloud mask layer (non-zero = cloud)
    scl: bool = True                                              # clouds / shadow / snow from the image's SCL band
    dem: str | None = None                                        # "auto" (Copernicus) or a DEM layer: drop water on steep slopes
    min_px: int = Field(9, ge=1, le=100000)
    slope_max: float = Field(10, ge=1, le=60)
    aoi: dict | None = None
    name: str = Field("water_mask", max_length=80)


@router.post("/api/raster/watermask")
def raster_watermask(req: WaterMaskRequest):
    """Water mask of a multispectral image (logic in lulc_fetch/watermask.py)."""
    from lulc_fetch import watermask as WM
    p = _raster_path(req.path)
    cloud = str(_raster_path(req.cloud)) if req.cloud else None
    dem = req.dem if req.dem in (None, "auto") else str(_raster_path(req.dem))

    def work(job):
        r = WM.run(str(p), _out_dir(), bands=req.bands, idx=req.index, mode=req.threshold, cloud=cloud, scl=req.scl, dem=dem,
                   cache=ws.APP_DIR / "sar_cache", min_px=req.min_px, slope_max=req.slope_max, aoi=req.aoi, name=_safe(req.name) or "water_mask",
                   window=req.window | 1, k=req.k)
        r["outputs"] = [ws.rel(o) for o in r["outputs"]]
        return r
    return jobs.submit("watermask", f"Water mask of {p.name}", {}, work).to_dict()


# ------------------------------------------------------------------ image features: local statistics, GLCM texture, morphology
class LocalStatsRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    windows: list[int] = Field(default_factory=lambda: [3, 5, 7, 15], min_length=1, max_length=8)
    stats: list[str] = Field(default_factory=lambda: ["mean", "std"], min_length=1, max_length=12)
    db: bool = True                                               # linear SAR power → dB first
    name: str = Field("", max_length=80)


@router.post("/api/raster/localstats")
def raster_localstats(req: LocalStatsRequest):
    from lulc_fetch import imagefeatures as F
    p = _raster_path(req.path)
    bad = [s for s in req.stats if s not in F.STATS]
    if bad or any(not 3 <= w <= 101 for w in req.windows):
        raise HTTPException(400, f"Statistics: {', '.join(F.STATS)}; windows 3–101")
    out = _out_dir() / f"{_safe(req.name) or p.stem + '_localstats'}.tif"
    return jobs.submit("localstats", f"Local statistics of {p.name}", {}, lambda job: {
        **(r := F.run_local_stats(str(p), out, bands=req.bands, windows=req.windows, stats=req.stats, db=req.db)), "outputs": [ws.rel(r["path"])]}).to_dict()


class GlcmRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    window: int = Field(7, ge=3, le=63)
    distance: int = Field(1, ge=1, le=10)
    levels: int = Field(16, ge=4, le=64)
    features: list[str] = Field(default_factory=lambda: ["contrast", "homogeneity", "energy", "correlation", "entropy"], min_length=1, max_length=9)
    db: bool = True
    name: str = Field("", max_length=80)


@router.post("/api/raster/glcm")
def raster_glcm(req: GlcmRequest):
    from lulc_fetch import imagefeatures as F
    p = _raster_path(req.path)
    if [f for f in req.features if f not in F.GLCM_FEATURES]:
        raise HTTPException(400, f"GLCM features: {', '.join(F.GLCM_FEATURES)}")
    out = _out_dir() / f"{_safe(req.name) or p.stem + '_glcm'}.tif"
    return jobs.submit("glcm", f"GLCM texture of {p.name}", {}, lambda job: {
        **(r := F.run_glcm(str(p), out, bands=req.bands, window=req.window | 1, distance=req.distance, levels=req.levels, features=req.features, db=req.db)),
        "outputs": [ws.rel(r["path"])]}).to_dict()


class MorphRequest(BaseModel):
    path: str
    band: int = Field(1, ge=1)
    op: str = Field("opening", pattern="^(erosion|dilation|opening|closing|gradient|tophat|blackhat|remove_small|fill_holes|majority|boundary)$")
    size: int = Field(3, ge=1, le=101)
    shape: str = Field("disk", pattern="^(disk|square|cross)$")
    value: float | None = None                                     # one class of a class map (else the band as it is)
    min_px: int = Field(50, ge=1, le=10_000_000)
    iterations: int = Field(1, ge=1, le=20)
    name: str = Field("", max_length=80)


@router.post("/api/raster/morphology")
def raster_morphology(req: MorphRequest):
    from lulc_fetch import imagefeatures as F
    p = _raster_path(req.path)
    out = _out_dir() / f"{_safe(req.name) or p.stem + '_' + req.op}.tif"
    return jobs.submit("morphology", f"{F.MORPH_TITLES[req.op]} of {p.name}", {}, lambda job: {
        **(r := F.run_morphology(str(p), out, band=req.band, op=req.op, size=req.size, shape=req.shape, value=req.value, min_px=req.min_px,
                                 iterations=req.iterations)), "outputs": [ws.rel(r["path"])]}).to_dict()


# ------------------------------------------------------------------ spatial structure: autocorrelation, edges, multi-scale, objects, spatial CV
def _read_band(p, band: int, db: bool = True):
    import numpy as np
    import rasterio

    from lulc_fetch import imagefeatures as F
    with rasterio.open(p) as s:
        if s.width * s.height > F.MAX_PIXELS:
            raise ValueError("The image is too big: clip it to the area first")
        if not 1 <= band <= s.count:
            raise ValueError(f"This raster has no band {band}")
        a = s.read(band, masked=True).astype("float64").filled(np.nan)
        name, prof, tags = s.descriptions[band - 1] or f"band {band}", s.profile.copy(), s.tags()
    if db:
        a, _ = F._db_if_sar(a, name, tags)
    return a, name, prof


class AutocorrRequest(BaseModel):
    path: str
    band: int = Field(1, ge=1)
    radius: int = Field(1, ge=1, le=25)
    alpha: float = Field(0.05, gt=0, lt=0.5)
    db: bool = True
    name: str = Field("", max_length=80)


@router.post("/api/raster/autocorrelation")
def raster_autocorrelation(req: AutocorrRequest):
    """Global Moran's I / Geary's C of a band, and maps of Local Moran's I clusters (LISA) and Getis-Ord Gi* z-scores."""
    from lulc_fetch import imagefeatures as F
    from lulc_fetch import spatialraster as SR
    p = _raster_path(req.path)

    def work(job):
        a, name, prof = _read_band(p, req.band, req.db)
        r = SR.autocorrelation(a, req.radius, alpha=req.alpha)
        stem = _safe(req.name) or f"{p.stem}_b{req.band}"
        lisa = F._write(_out_dir() / f"{stem}_lisa.tif", prof, [(f"{name}: Local Moran clusters", r["lisa"])], {"classes": json.dumps(SR.LISA)},
                        dtype="uint8", nodata=0, colormap=SR.LISA_COLORS)
        gi = F._write(_out_dir() / f"{stem}_gistar.tif", prof, [(f"{name}: Getis-Ord Gi* (z)", r["gi_star"]), (f"{name}: Local Moran's I", r["local_i"]),
                                                               (f"{name}: Local Moran's I (z)", r["local_z"])])
        keys = ("n", "radius", "moran_i", "moran_expected", "moran_z", "moran_p", "geary_c", "geary_z", "geary_p", "lisa_counts")
        return {**{k: r[k] for k in keys}, "band": name, "outputs": [ws.rel(lisa), ws.rel(gi)]}
    return jobs.submit("autocorrelation", f"Spatial autocorrelation of {p.name}", {}, work).to_dict()


class EdgesRequest(BaseModel):
    path: str
    band: int = Field(1, ge=1)
    which: list[str] = Field(default_factory=lambda: ["sobel", "canny"], min_length=1)
    sigma: float = Field(1.0, ge=0, le=20)
    low: float = Field(0.1, gt=0, lt=1)
    high: float = Field(0.2, gt=0, le=1)
    db: bool = True
    name: str = Field("", max_length=80)


@router.post("/api/raster/edges")
def raster_edges(req: EdgesRequest):
    from lulc_fetch import imagefeatures as F
    from lulc_fetch import spatialraster as SR
    p = _raster_path(req.path)
    if set(req.which) - {"sobel", "canny", "laplacian", "magnitude", "directional"}:
        raise HTTPException(400, "Edges: sobel, canny, laplacian, magnitude, directional")
    if req.low >= req.high:
        raise HTTPException(400, "Canny's low threshold must be below the high one")

    def work(job):
        a, name, prof = _read_band(p, req.band, req.db)
        layers = SR.edges(a, sigma=req.sigma, which=req.which, low=req.low, high=req.high)
        out = F._write(_out_dir() / f"{_safe(req.name) or p.stem + '_edges'}.tif", prof, [(f"{name}: {n}", x) for n, x in layers], {"features": "edges"})
        return {"outputs": [ws.rel(out)], "bands": [n for n, _ in layers]}
    return jobs.submit("edges", f"Edges of {p.name}", {}, work).to_dict()


class MultiscaleRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    sigmas: list[float] = Field(default_factory=lambda: [1, 2, 4, 8], min_length=1, max_length=8)
    features: list[str] = Field(default_factory=lambda: ["smooth", "gradient", "log", "dog", "std"], min_length=1)
    db: bool = True
    name: str = Field("", max_length=80)


@router.post("/api/raster/multiscale")
def raster_multiscale(req: MultiscaleRequest):
    import rasterio

    from lulc_fetch import imagefeatures as F
    from lulc_fetch import spatialraster as SR
    p = _raster_path(req.path)
    if set(req.features) - {"smooth", "gradient", "log", "dog", "mean", "std"} or any(not 0.3 <= s <= 64 for s in req.sigmas):
        raise HTTPException(400, "Features: smooth, gradient, log, dog, mean, std; scales 0.3–64 pixels")

    def work(job):
        with rasterio.open(p) as s:
            bl = req.bands or list(range(1, s.count + 1))
        if len(bl) * len(req.sigmas) * len(req.features) > 300:
            raise ValueError("Too many output bands (at most 300): choose fewer bands, scales or features")
        layers, prof = [], None
        for k, b in enumerate(bl):
            from lulc_fetch import progress
            progress.update(k / len(bl), f"Band {b}: scale space")
            a, name, prof = _read_band(p, b, req.db)
            layers += [(f"{name}: {n}", x) for n, x in SR.multiscale(a, sorted(req.sigmas), req.features)]
        out = F._write(_out_dir() / f"{_safe(req.name) or p.stem + '_multiscale'}.tif", prof, layers, {"features": "multi-scale"})
        return {"outputs": [ws.rel(out)], "bands": [n for n, _ in layers]}
    return jobs.submit("multiscale", f"Multi-scale features of {p.name}", {}, work).to_dict()


class SlicRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    n_segments: int = Field(1000, ge=4, le=200000)
    compactness: float = Field(0.3, gt=0, le=100)
    sigma: float | None = Field(None, ge=0, le=10)      # None: 2 for SAR, 1 otherwise
    polygons: bool = True
    db: bool = True
    name: str = Field("", max_length=80)


@router.post("/api/raster/slic")
def raster_slic(req: SlicRequest):
    """SLIC superpixels: segment ids, their boundaries, the mean image per segment and (optionally) polygons with means."""
    import numpy as np
    import rasterio

    from lulc_fetch import imagefeatures as F
    from lulc_fetch import segmentation as SG
    p = _raster_path(req.path)

    def work(job):
        from lulc_fetch import progress
        with rasterio.open(p) as s:
            bl = req.bands or list(range(1, s.count + 1))
            if s.width * s.height > 25_000_000:
                raise ValueError("SLIC here is for up to 25 million pixels: clip the image first")
        arrs, names, prof, sar = [], [], None, False
        for b in bl:
            a, nm, prof = _read_band(p, b, req.db)
            sar = sar or nm.upper() in ("VV", "VH", "HH", "HV")
            arrs.append(a)
            names.append(nm)
        progress.update(0.1, "SLIC superpixels")
        sigma = req.sigma if req.sigma is not None else (2.0 if sar else 1.0)
        lab = SG.slic(arrs, req.n_segments, req.compactness, sigma=sigma)
        progress.update(0.8, "Means and boundaries")
        painted, table = SG.region_means(lab, arrs)
        edge = np.zeros(lab.shape, "uint8")
        edge[:, 1:] |= (lab[:, 1:] != lab[:, :-1]).astype("uint8")
        edge[1:] |= (lab[1:] != lab[:-1]).astype("uint8")
        stem = _safe(req.name) or p.stem + "_slic"
        o1 = F._write(_out_dir() / f"{stem}_segments.tif", prof, [("Superpixel id", lab)], dtype="int32", nodata=0)
        o2 = F._write(_out_dir() / f"{stem}_mean.tif", prof, [(f"{n} (segment mean)", a) for n, a in zip(names, painted)])
        o3 = F._write(_out_dir() / f"{stem}_boundaries.tif", prof, [("Superpixel boundaries", edge)], dtype="uint8", nodata=0,
                      colormap={0: (0, 0, 0, 0), 1: (255, 230, 0, 255)})
        outs = [o2, o3, o1]
        if req.polygons:
            progress.update(0.9, "Polygons")
            t = prof["transform"]
            area = abs(t.a * t.e) if prof.get("crs") and not prof["crs"].is_geographic else None
            fc = SG.polygons(lab, t, prof.get("crs"), table, names, area)
            gp = _out_dir() / f"{stem}_segments.geojson"
            gp.write_text(json.dumps(fc))
            outs.append(str(gp))
        return {"outputs": [ws.rel(o) for o in outs], "segments": int(lab.max()), "sigma": sigma, "compactness": req.compactness}
    return jobs.submit("slic", f"Superpixels of {p.name}", {}, work).to_dict()


class ComponentsRequest(BaseModel):
    path: str
    band: int = Field(1, ge=1)
    value: float | None = None                      # the class to label (else non-zero)
    connectivity: int = 8
    min_px: int = Field(1, ge=1, le=10_000_000)
    polygons: bool = True
    name: str = Field("", max_length=80)


@router.post("/api/raster/components")
def raster_components(req: ComponentsRequest):
    """Connected regions of a mask or one class: an id per region, a table (pixels, hectares, centroid) and polygons."""
    import csv

    import numpy as np
    import rasterio

    from lulc_fetch import imagefeatures as F
    from lulc_fetch import segmentation as SG
    p = _raster_path(req.path)
    if req.connectivity not in (4, 8):
        raise HTTPException(400, "Connectivity: 4 or 8")

    def work(job):
        with rasterio.open(p) as s:
            a = s.read(req.band)
            prof = s.profile.copy()
        m = (a == req.value) if req.value is not None else (np.nan_to_num(a) != 0) & (a != (prof.get("nodata") if prof.get("nodata") is not None else -1e300))
        lab, regs = SG.components(m, req.connectivity, req.min_px)
        t = prof["transform"]
        ha = abs(t.a * t.e) / 1e4 if prof.get("crs") and not prof["crs"].is_geographic else None
        for r in regs:
            x, y = t * (r["col"] + 0.5, r["row"] + 0.5)
            r.update(x=round(x, 3), y=round(y, 3), **({"area_ha": round(r["pixels"] * ha, 4)} if ha else {}))
        stem = _safe(req.name) or p.stem + "_components"
        o1 = F._write(_out_dir() / f"{stem}.tif", prof, [("Component id", lab)], dtype="int32", nodata=0)
        tp = ws.root() / "tables" / f"{stem}.csv"
        tp.parent.mkdir(parents=True, exist_ok=True)
        with open(tp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(regs[0]) if regs else ["id"])
            w.writeheader()
            w.writerows(regs)
        outs = [o1]
        if req.polygons and regs:
            fc = SG.polygons(lab, t, prof.get("crs"), pixel_area=abs(t.a * t.e) if ha else None)
            gp = _out_dir() / f"{stem}.geojson"
            gp.write_text(json.dumps(fc))
            outs.append(str(gp))
        sizes = [r["pixels"] for r in regs]
        return {"outputs": [ws.rel(o) for o in outs], "csv": ws.rel(tp), "count": len(regs),
                "largest_px": max(sizes) if sizes else 0, "median_px": int(np.median(sizes)) if sizes else 0,
                "total_ha": round(sum(sizes) * ha, 2) if ha and sizes else None}
    return jobs.submit("components", f"Connected components of {p.name}", {}, work).to_dict()


class SpatialCvRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    ground_truth: dict
    model: str = Field("lgbm", pattern="^(lgbm|rf|xgb)$")
    blocks_m: list[float] = Field(default_factory=lambda: [250, 500, 1000, 2000, 4000], min_length=1, max_length=10)
    folds: int = Field(5, ge=2, le=10)
    per_class: int = Field(2000, ge=50, le=50000)


@router.post("/api/raster/spatialcv")
def raster_spatialcv(req: SpatialCvRequest):
    from lulc_fetch import spatialcv as CV
    p = _raster_path(req.path)
    gt = dict(req.ground_truth)
    if gt.get("type") == "raster":
        gt["path"] = str(_raster_path(gt["path"]))
    return jobs.submit("spatialcv", f"Spatial cross-validation of {p.name}", {}, lambda job: CV.run(
        str(p), gt, _out_dir(), bands=req.bands, model=req.model, blocks_m=req.blocks_m, folds=req.folds, per_class=req.per_class)).to_dict()
