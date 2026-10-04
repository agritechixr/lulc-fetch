"""Interpolation tool: a surface (GeoTIFF) from values at points, with IDW, kriging, spline, natural neighbour, nearest
neighbour, trend surface or TIN, and a leave-one-out comparison of the methods. The science is in
lulc_fetch/interpolation.py. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import core

router = APIRouter()


@router.get("/api/interp/schema")
def interp_schema():
    from lulc_fetch import interpolation
    return interpolation.schema()


class InterpPoints(BaseModel):
    points: dict                                  # GeoJSON FeatureCollection of points (EPSG:4326)
    field: str = Field(max_length=200)


class InterpRun(InterpPoints):
    method: str = Field("idw", pattern="^(idw|kriging|spline|natural|nearest|trend|tin)$")
    params: dict = Field(default_factory=dict)
    area: dict | None = None                      # polygon (EPSG:4326): the surface covers and is cut to it
    res_m: float | None = Field(None, gt=0, le=100000)
    name: str = Field("surface", max_length=80)


def _check(points: dict):
    if not points.get("features"):
        raise HTTPException(400, "Choose a layer of points")
    if len(points["features"]) > 20000:
        raise HTTPException(400, "Up to 20,000 points")


@router.post("/api/interp/run")
def interp_run(req: InterpRun):
    from lulc_fetch import interpolation
    _check(req.points)
    area = core.clip(req.area) if req.area else None
    stem = core.safe_stem(req.name, "surface")

    def run(job):
        r = interpolation.interpolate(req.points, req.field, req.method, job.dir / f"{stem}.tif", params=req.params, area=area, res_m=req.res_m)
        return core.one_output(r)

    title = f"Interpolation · {interpolation.METHODS[req.method]['title']} · {req.field}"
    return core.jobs.submit("interp", title, {"method": req.method, "field": req.field}, run).to_dict()


@router.post("/api/interp/compare")
def interp_compare(req: InterpPoints):
    from lulc_fetch import interpolation
    _check(req.points)
    return core.jobs.submit("interpcompare", f"Interpolation · compare methods · {req.field}", {"field": req.field},
                            lambda job: {"rows": interpolation.compare(req.points, req.field)}).to_dict()
