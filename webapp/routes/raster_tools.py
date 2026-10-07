"""Raster and terrain tools (Analysis ▸ Tools ▸ Raster & terrain): slope / aspect / hillshade, contours, reclassify,
change detection, clip / mask by polygons. Background jobs (progress, History, Workflows, the Assistant) writing to
analysis/. Logic in lulc_fetch/raster_ops.py."""

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


@router.post("/api/raster/change")
def raster_change(req: ChangeRequest):
    """Change detection between two dates: the difference and % change, or for class maps from → to with the area of
    each change (a table)."""
    from lulc_fetch import raster_ops
    a, b = _raster_path(req.before), _raster_path(req.after)

    def work(job):
        r = raster_ops.change(a, b, _out_dir(), band=req.band, categorical=req.categorical)
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
