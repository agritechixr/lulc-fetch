"""Classical ML for raster: image + ground truth → model + map. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import MODEL_DIR, jobs
from ..core import clip as _clip
from ..core import raster_path as _raster_path

router = APIRouter()


# ------------------------------------------------------------------ Classical ML for rasters (image + ground truth → map)

@router.get("/api/rasterml/schema")
def rasterml_schema():
    from lulc_fetch import rasterml

    return rasterml.schema()


@router.get("/api/rasterml/inspect")
def rasterml_inspect(path: str, bands: str = ""):
    from lulc_fetch import rasterml

    idx = [int(b) for b in bands.split(",") if b.strip()] or None
    try:
        return rasterml.inspect_kind(_raster_path(path), idx)
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e))


class RasterMLRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    ground_truth: dict
    model: str = "rf"
    params: dict = Field(default_factory=dict)
    common: dict = Field(default_factory=dict)
    tuning: dict = Field(default_factory=dict)
    clip: dict | None = None
    map_whole: bool = True
    factor: int = Field(1, ge=1, le=64)
    scale: float = 1.0
    offset: float = 0.0
    per_class: int | None = Field(3000, ge=10, le=10_000_000)
    name: str = Field("classified", max_length=80)
    class_colors: dict[str, str] | None = None
    confidence: bool = True
    resolution: str = "auto"


@router.post("/api/rasterml/run")
def rasterml_run(req: RasterMLRequest):
    import json as _json
    import shutil

    from lulc_fetch import ml, rasterml

    src = _raster_path(req.path)
    if req.model not in ml.MODELS:
        raise HTTPException(400, f"Unknown model {req.model}")
    gt = dict(req.ground_truth)
    if gt.get("type") == "raster":
        gt["path"] = str(_raster_path(gt["path"]))
    clip = _clip(req.clip) if req.clip else None

    def run(job):
        res = rasterml.classify(src, job.dir, bands=req.bands, ground_truth=gt, model=req.model, params=req.params, common=req.common,
                                tuning=req.tuning, clip=clip, map_clip=None if req.map_whole else clip, factor=req.factor, scale=req.scale,
                                offset=req.offset, per_class=req.per_class, name=req.name, class_colors=req.class_colors,
                                confidence=req.confidence, resolution=req.resolution)
        rep = res["model"]
        # the model joins Your models (with its evaluation report); no table is kept
        MODEL_DIR.mkdir(exist_ok=True)
        tmp = Path(rep["path"])
        dest, i = MODEL_DIR / tmp.name, 2
        while dest.exists():
            dest = MODEL_DIR / f"{tmp.stem}_{i}.joblib"
            i += 1
        shutil.move(tmp, dest)
        ev = tmp.with_suffix(".evaluation.html")
        if ev.exists():
            shutil.move(ev, dest.with_suffix(".evaluation.html"))
            rep["evaluation_html"] = ws.rel(dest.with_suffix(".evaluation.html"))
        Path(str(tmp) + ".json").unlink(missing_ok=True)
        rep["path"], rep["table"] = ws.rel(dest), src.name
        Path(str(dest) + ".json").write_text(_json.dumps(rep, indent=1, default=str))
        res["model_path"] = rep["path"]
        if res.get("map"):
            res["map"]["path"] = ws.rel(res["map"]["path"])
            res["path"] = res["map"]["path"]          # the map: added to Contents, saved by "also save to a folder"
        res["outputs"] = [res["model_path"]]
        return res

    title = f"Raster classification · {ml.MODELS[req.model]['title']} · {req.name}"
    return jobs.submit("rasterml", title, {"source": src.name}, run).to_dict()
