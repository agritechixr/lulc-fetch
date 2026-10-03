"""Background jobs: downloads from Find imagery, and the job list, logs, files and cancel for every tool. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from lulc_fetch.aoi import make_grid
from lulc_fetch.extras import LABEL_PRODUCTS
from lulc_fetch.pipeline import export_composite, export_labels, export_scene
from lulc_fetch.sources import DEFAULT_BANDS, S2_BANDS

from .. import credentials
from ..core import aoi as _aoi
from ..core import jobs, log
from ..core import source as _source

router = APIRouter()


# ------------------------------------------------------------------ jobs

class JobRequest(BaseModel):
    kind: str  # scene | composite | labels | product
    source: str = "earth-search"
    aoi: dict | None = None
    start: str | None = None
    end: str | None = None
    max_cloud: float = 40
    date: str | None = None
    bands: list[str] = Field(default_factory=lambda: list(DEFAULT_BANDS))
    indices: bool = True
    res: float = Field(10, ge=0.5, le=1000)
    mask_clouds: bool = True
    max_scenes: int = Field(20, ge=1, le=100)
    stat: str = "median"
    product: str = "worldcover"
    year: int = 2021
    product_names: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)  # Landsat scene ids for EarthExplorer


@router.post("/api/jobs")
def create_job(req: JobRequest):
    bad = [b for b in req.bands if b not in S2_BANDS]
    if bad:
        raise HTTPException(400, f"Unknown bands {bad}")

    if req.kind == "product" and req.source == "landsat-pc":
        if not req.product_names:
            raise HTTPException(400, "No products selected")
        usgs = credentials.get_all("usgs")
        if not all(usgs.values()):
            raise HTTPException(400, "Add your USGS EarthExplorer username and M2M token under Credentials first")

        def run(job):
            from lulc_fetch import progress
            from lulc_fetch import usgs as ee
            paths = []
            for i, name in enumerate(req.product_names):
                ent = req.entity_ids[i] if i < len(req.entity_ids) else None
                log.info("Downloading %s from USGS EarthExplorer...", name)
                with progress.span(i / len(req.product_names), (i + 1) / len(req.product_names)):
                    paths.append(str(ee.download_product(name, ent, job.dir, usgs["username"], usgs["token"])))
            return {"files": paths}

        title = f"Landsat product (EarthExplorer): {', '.join(req.product_names)[:80]}"
        return jobs.submit("product", title, req.model_dump(exclude={"aoi"}), run).to_dict()

    if req.kind == "product":
        if not req.product_names:
            raise HTTPException(400, "No products selected")
        acct = credentials.get_all("cdse_account")
        if not all(acct.values()):
            raise HTTPException(400, "Add your Copernicus Data Space account under Credentials first")

        def run(job):
            from lulc_fetch.cdse import download_product
            paths = []
            for name in req.product_names:
                log.info("Downloading %s from Copernicus Data Space...", name)
                paths.append(str(download_product(name, job.dir, acct["username"], acct["password"])))
            return {"files": paths}

        title = f"Original product: {', '.join(req.product_names)[:80]}"
        return jobs.submit("product", title, req.model_dump(exclude={"aoi"}), run).to_dict()

    if not req.aoi:
        raise HTTPException(400, "Draw or choose an area first")
    aoi = _aoi(req.aoi)
    grid = make_grid(aoi, req.res)
    if req.kind in ("scene", "composite"):
        if not (req.start and req.end):
            raise HTTPException(400, "Start and end dates are required")
        src = _source(req.source)
        sat, short = ("Landsat", "LS") if src.mission == "landsat" else ("Sentinel-2", "S2")
        if req.source == "cdse":
            src.gdal_env()  # fail now, not in the background, when S3 keys are missing

    if req.kind == "scene":
        name = f"{short}_{req.date or req.start + '_' + req.end}"
        title = f"{sat} {'scene ' + req.date if req.date else 'best scene ' + req.start + ' → ' + req.end}"

        def run(job):
            return export_scene(src, aoi, grid, req.start, req.end, job.dir / f"{name}.tif",
                                bands=req.bands, max_cloud=req.max_cloud, date=req.date,
                                candidates=1 if req.date else 5, mask_clouds=req.mask_clouds,
                                indices=req.indices)
    elif req.kind == "composite":
        title = f"{sat} {req.stat} composite {req.start} → {req.end}"

        def run(job):
            return export_composite(src, aoi, grid, req.start, req.end,
                                    job.dir / f"{short}_{req.stat}_{req.start}_{req.end}.tif",
                                    bands=req.bands, max_cloud=req.max_cloud, max_scenes=req.max_scenes,
                                    stat=req.stat, indices=req.indices)
    elif req.kind == "labels":
        if req.product not in LABEL_PRODUCTS:
            raise HTTPException(400, "Unknown label product")
        title = f"{LABEL_PRODUCTS[req.product]['title'].split(' (')[0]} {req.year}"

        def run(job):
            return export_labels(req.product, req.year, aoi, grid, job.dir / f"{req.product}_{req.year}.tif")
    else:
        raise HTTPException(400, f"Unknown job kind {req.kind}")

    log.info("Output grid: %dx%d px at %g m", grid.width, grid.height, grid.res)
    params = req.model_dump(exclude={"aoi"}) | {"grid": f"{grid.width}x{grid.height} px @ {grid.res:g} m"}
    return jobs.submit(req.kind, title, params, run).to_dict()


@router.get("/api/jobs")
def list_jobs():
    return [j.to_dict() for j in sorted(jobs.jobs.values(), key=lambda j: -j.created)]


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs.jobs:
        raise HTTPException(404, "No such job")
    return jobs.jobs[job_id].to_dict()


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = jobs.cancel(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job.to_dict()


@router.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    jobs.delete(job_id)
    return {"ok": True}


@router.get("/api/jobs/{job_id}/files/{name}")
def job_file(job_id: str, name: str):
    job = jobs.jobs.get(job_id)
    if not job or name not in job.files():  # also blocks path traversal
        raise HTTPException(404, "No such file")
    return FileResponse(job.dir / name, filename=name)
