"""PCA & dimensionality reduction (scikit-learn). Shared helpers come from webapp/core.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import clip as _clip
from ..core import jobs
from ..core import raster_path as _raster_path

router = APIRouter()


# ------------------------------------------------------------------ PCA & dimensionality reduction (scikit-learn)

@router.get("/api/pca/schema")
def pca_schema():
    from lulc_fetch import pca

    return pca.schema()


class PcaRequest(BaseModel):
    path: str
    bands: list[int]
    method: str = "pca"
    params: dict = Field(default_factory=dict)
    clip: dict | None = None
    name: str = "image"


@router.post("/api/pca/run")
def pca_run(req: PcaRequest):
    import re

    from lulc_fetch import pca

    src = _raster_path(req.path)
    if req.method not in pca.METHODS:
        raise HTTPException(400, f"Unknown method {req.method}")
    clip = _clip(req.clip)
    title = f"{pca.METHODS[req.method]['title']} · {req.name}"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{req.method}_{req.name}")[:60]

    def run(job):
        report = pca.run(src, job.dir / f"{stem}.tif", bands=req.bands, method=req.method, params=req.params, clip=clip)
        report["path"] = ws.rel(job.dir / f"{stem}.tif")
        return report

    return jobs.submit("pca", title, {"source": pca.METHODS[req.method]["full"]}, run).to_dict()
