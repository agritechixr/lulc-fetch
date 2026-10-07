"""Area statistics and accuracy assessment of class maps, the raster calculator, index time series and georeferencing
(Analysis ▸ Tools). Jobs (progress, History, Workflows, the Assistant) writing to analysis/ and tables/. Logic in
lulc_fetch/accuracy.py, calc.py, timeseries.py and georef.py."""

from __future__ import annotations

import csv
import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs
from ..core import raster_path as _raster_path
from .raster_tools import _METHOD, _out_dir, _safe
from .vector_tools import Layer, _layer, _write

router = APIRouter()


def _table(rows: list[dict], name: str, fields: list[str] | None = None) -> str:
    t = ws.root() / "tables" / f"{_safe(name)}_{uuid.uuid4().hex[:4]}.csv"
    t.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with open(t, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    return ws.rel(t)


def _area(a) -> dict | None:
    if a is None:
        return None
    return _layer(a) if isinstance(a, str) else a


# ------------------------------------------------------------------ area statistics
class AreaStatsRequest(BaseModel):
    raster: str
    band: int = Field(1, ge=1)
    area: Layer | None = None
    name: str = Field("area_statistics", max_length=80)


@router.post("/api/assess/area-stats")
def area_stats(req: AreaStatsRequest):
    """Hectares, km² and % of each class of a class map (inside an area, if given), as a table."""
    from lulc_fetch import accuracy
    p = _raster_path(req.raster)

    def work(job):
        r = accuracy.area_stats(p, band=req.band, area=_area(req.area))
        return {**r, "csv": _table(r["classes"], req.name, ["value", "class", "pixels", "area_ha", "percent"])}
    return jobs.submit("areastats", f"Area statistics of {p.name}", {}, work).to_dict()


# ------------------------------------------------------------------ accuracy assessment
class SampleRequest(BaseModel):
    raster: str
    band: int = Field(1, ge=1)
    per_class: int = Field(50, ge=1, le=5000)
    total: int | None = Field(None, ge=2, le=50000)
    min_per_class: int = Field(20, ge=0, le=5000)
    area: Layer | None = None
    seed: int | None = None
    name: str = Field("accuracy_points", max_length=80)


@router.post("/api/assess/sample")
def assess_sample(req: SampleRequest):
    """Stratified random points by map class, each with the map's class and an empty `reference` field to label."""
    from lulc_fetch import accuracy
    p = _raster_path(req.raster)

    def work(job):
        fc = accuracy.sample_points(p, band=req.band, per_class=req.per_class, total=req.total, min_per_class=req.min_per_class,
                                    area=_area(req.area), seed=req.seed)
        classes = fc.pop("classes")
        return {**_write(fc, req.name), "classes": classes}
    return jobs.submit("accsample", f"Accuracy points for {p.name}", {"per_class": req.per_class, "total": req.total}, work).to_dict()


class AssessRequest(BaseModel):
    raster: str
    points: Layer
    ref_field: str = Field("reference", max_length=200)
    band: int = Field(1, ge=1)
    area: Layer | None = None
    name: str = Field("accuracy", max_length=80)


@router.post("/api/assess/accuracy")
def assess_accuracy(req: AssessRequest):
    """Confusion matrix, overall / user's / producer's accuracy, kappa, and area estimates with 95 % confidence
    intervals (Olofsson et al. 2014), with an HTML report and tables."""
    from lulc_fetch import accuracy
    p = _raster_path(req.raster)

    def work(job):
        r = accuracy.assess(p, _layer(req.points), ref_field=req.ref_field, band=req.band, area=_area(req.area))
        d = _out_dir()
        (d / f"{_safe(req.name)}_report.html").write_text(accuracy.report_html(r, p.name), encoding="utf-8")
        (d / f"{_safe(req.name)}_matrix.csv").write_text(accuracy.matrix_csv(r), encoding="utf-8")
        (d / f"{_safe(req.name)}.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
        rows = [{**c, **{f"{k}_simple": pc[k] for k in ("users_accuracy", "producers_accuracy", "f1")}}
                for c, pc in zip(r["weighted"]["classes"], r["per_class"])]
        return {**r, "report": ws.rel(d / f"{_safe(req.name)}_report.html"), "csv": _table(rows, req.name),
                "matrix_csv": ws.rel(d / f"{_safe(req.name)}_matrix.csv")}
    return jobs.submit("accuracy", f"Accuracy of {p.name}", {"ref_field": req.ref_field}, work).to_dict()


# ------------------------------------------------------------------ raster calculator
class CalcVar(BaseModel):
    path: str
    band: int = Field(1, ge=1)
    scale: float | None = None


class CalcRequest(BaseModel):
    variables: dict[str, CalcVar] = Field(min_length=1, max_length=12)
    expression: str = Field(min_length=1, max_length=1000)
    resampling: str | None = Field(None, pattern=_METHOD)
    name: str = Field("calc", max_length=80)


@router.post("/api/raster/calc")
def raster_calc(req: CalcRequest):
    """Raster calculator: an expression over bands of several rasters (A, B, …), e.g. (B - A) / (B + A),
    where(A > 0.3, 1, 0), (A - B) > 0.1; all put on the first raster's grid."""
    from lulc_fetch import calc
    vars_ = {k: {"path": str(_raster_path(v.path)), "band": v.band, "scale": v.scale} for k, v in req.variables.items()}
    try:
        calc.check(req.expression, list(vars_))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return jobs.submit("calc", f"Calculate {req.expression[:60]}", {"expression": req.expression}, lambda job: {
        **(r := calc.calculate(vars_, req.expression, _out_dir() / f"{_safe(req.name)}.tif", resampling=req.resampling)),
        "path": ws.rel(r["path"])}).to_dict()


# ------------------------------------------------------------------ index time series
class TimeSeriesRequest(BaseModel):
    geometry: dict
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    index: str = Field("NDVI", pattern="^(NDVI|EVI|NDWI|NDMI|NDRE|SAVI)$")
    max_cloud: float = Field(80, ge=0, le=100)
    buffer_m: float = Field(15, ge=5, le=500)
    source: str = Field("earth-search", pattern="^(earth-search|planetary-computer)$")
    name: str = Field("time_series", max_length=80)


@router.post("/api/timeseries")
def time_series(req: TimeSeriesRequest):
    """The mean of an index (NDVI, EVI, NDWI…) of a point or field in every Sentinel-2 scene of a date range, from the
    free catalogue (clouds masked), as a table and a chart."""
    from lulc_fetch import timeseries

    from .. import credentials
    g = req.geometry.get("geometry", req.geometry) if req.geometry.get("type") == "Feature" else req.geometry
    if g.get("type") == "FeatureCollection":
        feats = [f for f in g.get("features") or [] if f.get("geometry")]
        if not feats:
            raise HTTPException(400, "Choose a point or a field")
        g = feats[0]["geometry"]
    if g.get("type") not in ("Point", "Polygon", "MultiPolygon"):
        raise HTTPException(400, "Choose a point or a field (polygon)")
    if req.start >= req.end:
        raise HTTPException(400, "The start date must be before the end date")

    def work(job):
        r = timeseries.series(g, req.start, req.end, index=req.index, source=req.source, max_cloud=req.max_cloud,
                              buffer_m=req.buffer_m, **credentials.source_kwargs(req.source))
        r["csv"] = _table(r["rows"], f"{req.name}_{req.index.lower()}", ["date", "mean", "std", "min", "max", "pixels", "clear_pct", "scene_cloud_pct", "tile", "scene"])
        return r
    return jobs.submit("timeseries", f"{req.index} time series {req.start} → {req.end}", {"index": req.index}, work).to_dict()


# ------------------------------------------------------------------ georeference
GEOREF_DIR = "uploads/georef"


@router.post("/api/georef/upload")
async def georef_upload(file: UploadFile = File(...)):
    """A picture to georeference (PNG, JPG, TIFF), kept in uploads/georef/."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file.filename or "picture.png").name)[:80]
    if not re.search(r"\.(png|jpe?g|tiff?|gif|bmp|webp)$", name, re.I):
        raise HTTPException(400, "Choose a picture: PNG, JPG, TIFF, GIF, BMP or WebP")
    d = ws.root() / GEOREF_DIR
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{uuid.uuid4().hex[:6]}_{name}"
    data = await file.read()
    if len(data) > 200_000_000:
        raise HTTPException(400, "The picture is too big (more than 200 MB)")
    out.write_bytes(data)
    import rasterio
    try:
        with rasterio.open(out) as s:
            w, h, n = s.width, s.height, s.count
    except Exception:  # noqa: BLE001
        out.unlink()
        raise HTTPException(400, "The picture couldn't be read")
    return {"path": ws.rel(out), "width": w, "height": h, "bands": n}


def _georef_file(path: str) -> Path:
    p = (ws.root() / path).resolve()
    if not p.is_relative_to((ws.root() / "uploads").resolve()) or not p.is_file():
        raise HTTPException(404, "The picture is missing")
    return p


@router.get("/api/georef/image")
def georef_image(path: str):
    """The picture itself, to show and click on (PNG / JPG as they are; others as a PNG preview)."""
    p = _georef_file(path)
    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return FileResponse(p)
    prev = p.with_suffix(".preview.png")
    if not prev.exists():
        import numpy as np
        import rasterio   # (Pillow isn't in the desktop app: rasterio writes the PNG)
        with rasterio.open(p) as s:
            a = s.read(list(range(1, min(s.count, 3) + 1)))
        a = np.clip(a.astype("float64") / max(float(np.nanpercentile(a, 99.5)), 1e-9) * 255, 0, 255).astype("uint8")
        with rasterio.open(prev, "w", driver="PNG", width=a.shape[2], height=a.shape[1], count=a.shape[0], dtype="uint8") as d:
            d.write(a)
    return FileResponse(prev)


class GeorefPoint(BaseModel):
    px: float
    py: float
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class GeorefRequest(BaseModel):
    image: str
    points: list[GeorefPoint] = Field(min_length=3, max_length=200)
    method: str = Field("affine", pattern="^(affine|poly2|tps)$")
    res: float | None = Field(None, gt=0)
    name: str = Field("georeferenced", max_length=80)


@router.post("/api/georef/fit")
def georef_fit(req: GeorefRequest):
    """How well the control points fit (residual of each, RMSE), before warping."""
    from lulc_fetch import georef
    try:
        return georef.fit([p.model_dump() for p in req.points], req.method)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/georef/warp")
def georef_warp(req: GeorefRequest):
    """The picture as a GeoTIFF placed by the control points."""
    from lulc_fetch import georef
    p = _georef_file(req.image)
    pts = [x.model_dump() for x in req.points]
    try:
        georef.fit(pts, req.method)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return jobs.submit("georef", f"Georeference {p.name}", {"method": req.method, "points": len(pts)}, lambda job: {
        **(r := georef.warp(p, pts, _out_dir() / f"{_safe(req.name)}.tif", method=req.method, res=req.res)), "path": ws.rel(r["path"])}).to_dict()


@router.get("/api/assess/report")
def assess_report(path: str):
    """An HTML report made by a tool (in analysis/), to open in a new tab."""
    p = (ws.root() / path).resolve()
    if not p.is_relative_to((ws.root() / "analysis").resolve()) or p.suffix.lower() != ".html" or not p.is_file():
        raise HTTPException(404, "The report is missing")
    return FileResponse(p, media_type="text/html")
