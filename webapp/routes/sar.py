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
STEPS = r"^(validate|orbit|border|thermal|calibrate|speckle|flatten|terrain|normalise|db|reproject|resample|clip)$"


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


def _key(src) -> str | None:
    """The pass a GRD / RTC input belongs to (mission, absolute orbit, datatake): frames of one pass share it."""
    from lulc_fetch.sar import product as P
    name = src if isinstance(src, str) else src.get("id", "")
    m = P.NAME.search(Path(name).name) if isinstance(name, str) else None
    return f"{m.group(1)}_{m.group(10)}_{m.group(11)}_{'rtc' if name.endswith('_rtc') else 'grd'}" if m else None


def _frames(srcs: list, aoi: dict | None, find: bool) -> list[list]:
    """Inputs grouped by pass (frames of one pass are joined; each group is one date). With find and an area, a
    Planetary Computer scene that doesn't cover the whole area brings the pass's other frames that do."""
    from lulc_fetch.sar import pipeline as PL
    from lulc_fetch.sar import product as P
    groups: dict = {}
    for s in srcs:
        k = _key(s) if find and not str(s).lower().endswith((".tif", ".tiff")) else None
        groups.setdefault(k or f"_{len(groups)}", []).append(s)
    if find and aoi:
        for k, grp in list(groups.items()):
            if k.startswith("_") or not all(isinstance(s, str) and s.startswith("S1") for s in grp):
                continue
            try:
                extra = PL.neighbours(P.pc_item(grp[0]), aoi)
            except Exception:   # noqa: BLE001 (the catalogue: the scene alone, then)
                extra = []
            have = set(grp)
            grp += [e["id"] for e in extra if e["id"] not in have]
    return [sorted(g) for g in groups.values()]


def _union_bounds(grp: list) -> tuple:
    from shapely.geometry import shape
    from shapely.ops import unary_union

    from lulc_fetch.sar import product as P
    boxes = []
    for s in grp:
        if isinstance(s, str) and s.startswith("S1") and not Path(s).exists():
            boxes.append(shape(P.pc_item(s)["geometry"]))
        else:
            from shapely.geometry import box
            g = P.open_product(s).geometry.grid
            boxes.append(box(g[:, 3].min(), g[:, 2].min(), g[:, 3].max(), g[:, 2].max()))
    return unary_union(boxes).bounds


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
    steps: list[str] = Field(default_factory=list, max_length=13)
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
    masks: bool = False                                           # incidence angles (ellipsoid and local)
    quality: bool = True                                          # the quality layer (valid, layover, shadow, noise floor …)
    flatten_method: str = Field("area", pattern="^(area|angular)$")   # Small 2011, or Vollrath 2020
    normalise_ref: float = Field(40.0, ge=10, le=60)              # incidence-angle normalisation: reference angle
    normalise_n: float | None = Field(None, ge=0, le=4)           # its exponent (default 2 for σ⁰, 1 for γ⁰)
    join_frames: bool = True                                      # frames of one pass → one image (finding the missing ones)
    multitemporal: bool = False                                   # Quegan's multi-temporal speckle filter across the dates
    mt_size: int = Field(7, ge=3, le=15)
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
            "db": req.db, "masks": req.masks, "quality": req.quality, "flatten_method": req.flatten_method, "normalise_ref": req.normalise_ref,
            "normalise_n": req.normalise_n}
    if req.change and len(srcs) < 2:
        raise HTTPException(400, "Change detection needs two dates: add a second scene or layer")
    if req.temporal and len(srcs) < 2:
        raise HTTPException(400, "Time-series statistics need at least two dates")

    if req.multitemporal and len(srcs) < 2:
        raise HTTPException(400, "The multi-temporal speckle filter needs at least two dates")

    def work(job):
        out = _out()
        groups = _frames(srcs, req.aoi, req.join_frames)
        if req.multitemporal and len(groups) < 2:
            raise ValueError("The multi-temporal speckle filter needs at least two dates (these are frames of one pass)")
        results, outputs, notes = [], [], []
        one_grid = len(groups) > 1 and not req.aoi and not req.like   # several dates without an area: on the first one's grid
        for k, grp in enumerate(groups):
            with progress.span(k / len(groups) * 0.85, (k + 1) / len(groups) * 0.85):
                nm = _safe(req.name) + (f"_{k + 1}" if len(groups) > 1 and req.name else "") if req.name else None
                o = {**opts, "name": nm}
                if one_grid and results:
                    o["like"] = results[0]["paths"]["linear"]
                if len(grp) == 1:
                    r = PL.process(grp[0], out, o, _cache())
                else:   # frames of one pass: each onto one grid, then joined
                    if not req.aoi and not o.get("like"):
                        o["bounds"] = _union_bounds(grp)
                    o["res"] = o.get("res") or 20
                    parts = []
                    for j, f in enumerate(grp):
                        with progress.span(j / len(grp), (j + 1) / len(grp)):
                            try:
                                parts.append(PL.process(f, out, {**o, "name": f"{nm or 'sar'}_frame{j + 1}"}, _cache()))
                            except ValueError as e:
                                if "falls in the area" not in str(e) and "isn't covered" not in str(e):
                                    raise
                    if not parts:
                        raise ValueError("None of the frames covers the area")
                    stem = nm or Path(parts[0]["paths"]["linear"]).name.replace("_frame1_linear.tif", "")
                    r = PL.join_frames(parts, out, stem) if len(parts) > 1 else parts[0]
                    notes.append(f"{r.get('date') or ''}: {len(parts)} frames of one pass joined")
            results.append(r)
            outputs += r["outputs"]
        lin = [r["paths"]["linear"] for r in results]
        extra = {}
        if req.multitemporal:
            with progress.span(0.85, 0.9):
                extra["multitemporal"] = PL.multitemporal(lin, req.mt_size)
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
        summary = [{k: r.get(k) for k in ("steps_done", "units", "date", "orbit", "orbit_note", "note", "dem", "speckle", "grid", "looks", "quality", "tiles", "frames")} for r in results]
        return {"outputs": [ws.rel(o) for o in outputs], "runs": summary, "notes": notes, **extra}
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
    multitemporal: bool = False        # filtered copies of the dates (Quegan), on the first one's grid
    mt_size: int = Field(7, ge=3, le=15)
    name: str = Field("", max_length=80)


@router.post("/api/sar/series")
def sar_series(req: SeriesRequest):
    """Time-series statistics of several dates, and the change from the first to the last (log-ratio, classes, flooding)."""
    from lulc_fetch.sar import analysis as AN
    paths = [str(_raster_path(r)) for r in req.rasters]

    def work(job):
        nonlocal paths
        out, outputs, extra = _out(), [], {}
        if req.multitemporal:
            from lulc_fetch import progress
            from lulc_fetch.sar import pipeline as PL
            copies = []
            for k, p in enumerate(paths):
                with progress.span(k / len(paths) * 0.5, (k + 1) / len(paths) * 0.5):
                    r = PL.process(p, out, {"steps": [], "db": True, "quality": False, "like": paths[0] if k else None,
                                            "name": f"{Path(p).stem.replace('_dB', '').replace('_linear', '')}_mtf"}, None)
                copies.append(r["paths"]["linear"])
            with progress.span(0.5, 0.7):
                extra["multitemporal"] = PL.multitemporal(copies, req.mt_size)
            outputs += [r for c in copies for r in (c.replace("_linear.tif", "_dB.tif"), c) if Path(r).exists()]
            paths = copies
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


# ------------------------------------------------------------------ ASF HyP3: InSAR & RTC on demand (NASA Earthdata login)
def _hyp3():
    from lulc_fetch.sar import hyp3 as H

    from .. import credentials
    c = credentials.get_all("earthdata")
    try:
        return H.Client(token=c.get("token"), username=c.get("username"), password=c.get("password"))
    except H.AuthError as e:
        raise HTTPException(401, str(e))


def _hyp3_call(fn):
    from lulc_fetch.sar import hyp3 as H
    try:
        return fn()
    except H.AuthError as e:
        raise HTTPException(401, str(e))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e)[:500])
    except Exception as e:   # noqa: BLE001 (the network, said plainly)
        raise HTTPException(502, f"ASF HyP3 couldn't be reached: {str(e)[:300]}")


class AsfSearch(BaseModel):
    aoi: dict
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    level: str = Field("SLC", pattern="^(SLC|GRD_HD)$")
    orbit: str | None = Field(None, pattern="^(ascending|descending)$")
    path: int | None = Field(None, ge=1, le=175)
    step: int = Field(1, ge=1, le=4)          # InSAR pairs: each date with the next `step`
    max_days: int = Field(48, ge=6, le=400)


@router.post("/api/sar/asf/search")
def asf_search(req: AsfSearch):
    """Sentinel-1 scenes in ASF's catalogue (no login), and for SLC the InSAR pairs they make."""
    from lulc_fetch.sar import hyp3 as H
    try:
        scenes = H.search(req.aoi, req.start, req.end, level=req.level, orbit=req.orbit, path=req.path)
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, f"ASF search failed: {str(e)[:300]}")
    return {"scenes": scenes, "pairs": H.pairs(scenes, step=req.step, max_days=req.max_days) if req.level == "SLC" else []}


@router.get("/api/sar/hyp3/user")
def hyp3_user():
    """Who is logged in, whether HyP3 access is approved, and the credits left this month."""
    from lulc_fetch.sar import hyp3 as H
    c = _hyp3()
    u = _hyp3_call(c.user)
    costs = _hyp3_call(c.costs)
    return {"user": u.get("user_id"), "status": u.get("application_status"), "credits": u.get("remaining_credits"),
            "credits_per_month": u.get("credits_per_month"), "access_help": H.ACCESS_HELP,
            "costs": {k: v.get("cost") if isinstance(v, dict) else v for k, v in costs.items() if k in H.JOB_TYPES}}


class AccessRequest(BaseModel):
    use_case: str = Field(min_length=20, max_length=5000)
    access_code: str | None = Field(None, max_length=100)


@router.post("/api/sar/hyp3/access")
def hyp3_access(req: AccessRequest):
    """Ask ASF for HyP3 access (once per Earthdata account; usually approved quickly)."""
    c = _hyp3()
    u = _hyp3_call(lambda: c.request_access(req.use_case, req.access_code))
    return {"status": u.get("application_status"), "credits": u.get("remaining_credits")}


class Hyp3Submit(BaseModel):
    job_type: str = Field(pattern="^(INSAR_GAMMA|RTC_GAMMA)$")
    pairs: list[list[str]] = Field(default_factory=list, max_length=100)    # INSAR_GAMMA: [[reference, secondary], …]
    granules: list[str] = Field(default_factory=list, max_length=100)        # RTC_GAMMA: GRD scene names
    name: str = Field("lulc-fetch", min_length=1, max_length=90)
    looks: str = Field("20x4", pattern="^(20x4|10x2)$")
    displacement: bool = True
    water_mask: bool = False
    resolution: int = Field(30, ge=10, le=30)
    radiometry: str = Field("gamma0", pattern="^(gamma0|sigma0)$")
    speckle: bool = False
    validate_only: bool = False


@router.post("/api/sar/hyp3/submit")
def hyp3_submit(req: Hyp3Submit):
    from lulc_fetch.sar import hyp3 as H
    gran = re.compile(r"^S1[ABCD]_IW_(SLC_|GRDH)_[A-Z0-9_]{50,80}$")
    if req.job_type == "INSAR_GAMMA":
        if not req.pairs:
            raise HTTPException(400, "Choose at least one pair of SLC scenes")
        if any(len(p) != 2 for p in req.pairs):
            raise HTTPException(400, "Each pair is two SLC scenes: the reference (earlier) and the secondary (later)")
        bad = [g for p in req.pairs for g in p if not gran.match(g) or "_SLC_" not in g]
        jobs = [H.insar_job(a, b, name=req.name, looks=req.looks, displacement=req.displacement, water_mask=req.water_mask) for a, b in req.pairs]
    else:
        if not req.granules:
            raise HTTPException(400, "Choose at least one GRD scene")
        bad = [g for g in req.granules if not gran.match(g) or "_GRDH_" not in g]
        if req.resolution not in (10, 20, 30):
            raise HTTPException(400, "RTC pixel size: 10, 20 or 30 m")
        jobs = [H.rtc_job(g, name=req.name, resolution=req.resolution, radiometry=req.radiometry, speckle=req.speckle) for g in req.granules]
    if bad:
        raise HTTPException(400, f"Not Sentinel-1 IW {'SLC' if req.job_type == 'INSAR_GAMMA' else 'GRD'} scene names: {', '.join(bad[:3])}")
    c = _hyp3()
    done = _hyp3_call(lambda: c.submit(jobs, validate_only=req.validate_only))
    return {"jobs": [H.summary(j) for j in done] if not req.validate_only else [], "validated": req.validate_only, "count": len(jobs)}


@router.get("/api/sar/hyp3/jobs")
def hyp3_jobs(name: str | None = None, days: int = 30):
    from lulc_fetch.sar import hyp3 as H
    c = _hyp3()
    js = _hyp3_call(lambda: c.jobs(name=name, days=max(1, min(int(days), 180))))
    return {"jobs": [H.summary(j) for j in sorted(js, key=lambda j: j.get("request_time") or "", reverse=True)]}


class Hyp3Download(BaseModel):
    job_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


@router.post("/api/sar/hyp3/download")
def hyp3_download(req: Hyp3Download):
    """Download a finished job's product into the workspace (imports/hyp3/…): its GeoTIFFs go to Contents."""
    from lulc_fetch import progress
    from lulc_fetch.sar import hyp3 as H
    c = _hyp3()
    j = _hyp3_call(lambda: c.job(req.job_id))
    if j.get("status_code") != "SUCCEEDED":
        raise HTTPException(400, f"The job is {str(j.get('status_code', '?')).lower()}: wait until it has succeeded")

    def work(job):
        d = ws.root() / "imports" / "hyp3" / f"{_safe(j.get('name') or 'job')}_{req.job_id[:8]}"
        tifs = H.download(j, d, progress_cb=lambda f: progress.update(f * 0.95, f"Downloading {f:.0%}"))
        order = ("unw_phase", "corr", "los_disp", "vert_disp", "amp", "_VV", "_VH", "_HH", "_HV", "inc_map", "ls_map", "dem")
        tifs.sort(key=lambda p: next((i for i, k in enumerate(order) if k in Path(p).name), 99))
        return {"outputs": [ws.rel(t) for t in tifs], "folder": ws.rel(d), "type": j["job_type"]}
    return jobs.submit("sar", f"HyP3 {j['job_type']} · download", {}, work).to_dict()
