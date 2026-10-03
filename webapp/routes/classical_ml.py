"""Classical ML (tabular data): train models, the model library, classify an image. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import MODEL_DIR, jobs, log
from ..core import copy_report as _copy_report
from ..core import report_folder as _report_folder
from ..core import table_path as _table_path

router = APIRouter()


# ------------------------------------------------------------------ Classical ML: train models and apply them





@router.get("/api/ml/schema")
def ml_schema():
    from lulc_fetch import ml

    return ml.schema()


@router.get("/api/tables/describe")
def table_describe(path: str):
    from lulc_fetch import ml

    return ml.describe_table(_table_path(path))


class TrainRequest(BaseModel):
    table: str
    target: str
    features: list[str]
    model: str = "rf"
    task: str = "auto"
    params: dict = Field(default_factory=dict)
    common: dict = Field(default_factory=dict)
    name: str = Field("model", max_length=80)
    categorical: list[str] = Field(default_factory=list)
    tuning: dict = Field(default_factory=dict)
    report_dir: str | None = Field(None, max_length=1000)  # also save the HTML evaluation report in this folder






class CompareRequest(BaseModel):
    table: str
    target: str
    features: list[str]
    task: str = "auto"
    common: dict = Field(default_factory=dict)
    categorical: list[str] = Field(default_factory=list)
    max_rows: int = Field(20000, ge=500, le=200000)


@router.post("/api/ml/compare")
def ml_compare(req: CompareRequest):
    from lulc_fetch import ml

    table = _table_path(req.table)
    if req.task not in ("auto", "classification", "regression"):
        raise HTTPException(400, "Unknown task")

    def run(job):
        return ml.compare(table, target=req.target, features=req.features, task=req.task, common=req.common,
                          categorical=req.categorical, max_rows=req.max_rows)

    return jobs.submit("compare", f"Compare models · {table.name}", {"source": "Classical ML"}, run).to_dict()


@router.post("/api/ml/train")
def ml_train(req: TrainRequest):
    import shutil

    from lulc_fetch import ml

    table = _table_path(req.table)
    if req.model not in ml.MODELS:
        raise HTTPException(400, f"Unknown model {req.model}")
    if req.task not in ("auto", "classification", "regression"):
        raise HTTPException(400, "Unknown task")

    report_dir = _report_folder(req.report_dir) if req.report_dir and req.report_dir.strip() else None

    def run(job):
        rep = ml.train(table, job.dir, target=req.target, features=req.features, model=req.model, task=req.task,
                       params=req.params, common=req.common, name=req.name, categorical=req.categorical,
                       tuning=req.tuning)
        MODEL_DIR.mkdir(exist_ok=True)
        tmp = Path(rep["path"])
        dest, i = MODEL_DIR / tmp.name, 2
        while dest.exists():
            dest = MODEL_DIR / f"{tmp.stem}_{i}.joblib"
            i += 1
        shutil.move(tmp, dest)
        ev_tmp = tmp.with_suffix(".evaluation.html")
        if ev_tmp.exists():
            shutil.move(ev_tmp, dest.with_suffix(".evaluation.html"))
            rep["evaluation_html"] = ws.rel(dest.with_suffix(".evaluation.html"))
            if report_dir is not None:
                try:
                    rep["evaluation_saved_to"] = str(_copy_report(dest.with_suffix(".evaluation.html"), report_dir))
                    log.info("Evaluation report saved to %s", rep["evaluation_saved_to"])
                except OSError as e:  # never lose a trained model because of the copy
                    rep.setdefault("warnings", []).append(f"Couldn't save the report to {report_dir}: {e}")
        rep["path"] = ws.rel(dest)
        rep["table"] = ws.rel(table)
        import json as _json
        Path(str(dest) + ".json").write_text(_json.dumps(rep, indent=1, default=str))
        Path(str(tmp) + ".json").unlink(missing_ok=True)
        return rep

    title = f"Train {ml.MODELS[req.model]['title']} · {req.name}"
    return jobs.submit("train", title, {"source": ml.MODELS[req.model]["title"]}, run).to_dict()
