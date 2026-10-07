"""Make training data: deep-learning patches. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core import PATCH_DIR, PatchInput, jobs
from ..core import clip as _clip
from ..core import raster_path as _raster_path
from ..core import remember_dl as _remember_dl
from ..core import report_folder as _report_folder

router = APIRouter()


# ------------------------------------------------------------------ Make training data (deep-learning patches)





class PatchPlanRequest(BaseModel):
    path: str
    clip: dict | None = None
    patch_m: list[float] = Field(..., min_length=2, max_length=2)
    overlap_m: list[float] = Field(default_factory=lambda: [0, 0], min_length=2, max_length=2)
    edge: str = Field("pad", pattern="^(pad|drop)$")


class PatchRequest(PatchPlanRequest):
    resampling: str | None = Field(None, pattern=r"^(nearest|bilinear|cubic|bicubic|cubic_spline|lanczos|average|mode|min|max|med|q1|q3)$")   # images onto the patch grid (labels stay nearest)
    inputs: list[PatchInput] = Field(..., min_length=1, max_length=20)
    ground_truth: dict | None = None
    name: str = Field("training_patches", max_length=80)
    folder: str | None = Field(None, max_length=1000)   # where the dataset folder is created (default: the project)
    min_valid: float = Field(0.5, ge=0, le=1)
    require_labels: bool = False
    min_labelled: float = Field(0.01, ge=0, le=1)
    remap: bool = True
    class_colors: dict[str, str] | None = None


def _check_patch_sizes(req: PatchPlanRequest):
    if min(req.patch_m) <= 0:
        raise HTTPException(400, "The patch size must be above 0")
    if min(req.overlap_m) < 0:
        raise HTTPException(400, "The overlap can't be negative")


@router.post("/api/patches/plan")
def patches_plan(req: PatchPlanRequest):
    from lulc_fetch import patches

    _check_patch_sizes(req)
    try:
        return patches.plan(_raster_path(req.path), clip=_clip(req.clip) if req.clip else None, patch_m=req.patch_m,
                            overlap_m=req.overlap_m, edge=req.edge)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/patches/make")
def patches_make(req: PatchRequest):
    from lulc_fetch import patches

    _check_patch_sizes(req)
    inputs = [{**i.model_dump(), "path": str(_raster_path(i.path))} for i in req.inputs]
    gt = dict(req.ground_truth) if req.ground_truth else None
    if gt and gt.get("type") == "raster":
        gt["path"] = str(_raster_path(gt["path"]))
    clip = _clip(req.clip) if req.clip else None
    parent = _report_folder(req.folder) if req.folder and req.folder.strip() else PATCH_DIR.path
    out = patches.dataset_folder(parent, req.name)
    if out.exists() and any(out.iterdir()):
        raise HTTPException(400, f"{out} already exists and isn't empty. Choose another name or folder.")

    def run(job):
        res = patches.make(inputs, parent, name=req.name, ground_truth=gt, clip=clip, patch_m=req.patch_m, overlap_m=req.overlap_m,
                           edge=req.edge, min_valid=req.min_valid, require_labels=req.require_labels, min_labelled=req.min_labelled,
                           remap=req.remap, class_colors=req.class_colors)
        res["outputs"] = []
        if res.get("labels_dir"):
            _remember_dl("dl_datasets.json", res["folder"])   # offered by Train classify model
        return res

    title = f"Make training data · {req.name}"
    def run_with_resampling(job):
        from lulc_fetch import resample
        with resample.using(req.resampling):
            return run(job)
    return jobs.submit("patches", title, {"source": Path(inputs[0]["path"]).name}, run_with_resampling).to_dict()
