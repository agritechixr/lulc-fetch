"""Stack layers onto one grid. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import clip as _clip
from ..core import jobs
from ..core import raster_path as _raster_path

router = APIRouter()


# ------------------------------------------------------------------ stack layers onto one grid

class StackItem(BaseModel):
    path: str
    name: str = "layer"
    bands: list[int] | None = None
    index: str | None = None
    formula: str | None = Field(None, max_length=500)
    band_map: dict[str, int] = Field(default_factory=dict)
    scale: float = 1.0
    offset: float = 0.0


class StackRequest(BaseModel):
    items: list[StackItem]
    ref: str
    clip: dict | None = None
    factor: int = Field(1, ge=1, le=64)
    name: str = Field("stack", max_length=80)
    resampling: str | None = Field(None, pattern=r"^(nearest|bilinear|cubic|bicubic|cubic_spline|lanczos|average|mode|min|max|med|q1|q3)$")   # continuous layers onto the grid (classes stay nearest)


@router.post("/api/stack")
def stack_job(req: StackRequest):
    import re

    from lulc_fetch.stack import stack

    if not req.items:
        raise HTTPException(400, "Choose at least one layer")
    items = [{**it.model_dump(), "path": str(_raster_path(it.path))} for it in req.items]
    ref = _raster_path(req.ref)
    clip = _clip(req.clip)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:80] or "stack"

    def run(job):
        rep = stack(items, ref, job.dir / f"{stem}.tif", clip=clip, factor=req.factor, resampling=req.resampling)
        rep["path"] = ws.rel(job.dir / f"{stem}.tif")
        return rep

    return jobs.submit("stack", f"Stack layers · {req.name}", {"source": "Stack layers"}, run).to_dict()
