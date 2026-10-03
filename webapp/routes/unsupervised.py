"""Classical ML: Clustering and t-SNE map. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import MODEL_DIR, TABLE_DIR, jobs
from ..core import clip as _clip
from ..core import copy_report as _copy_report
from ..core import model_path as _model_path
from ..core import raster_path as _raster_path
from ..core import report_folder as _report_folder
from ..core import table_path as _table_path

router = APIRouter()


# ------------------------------------------------------------------ Classical ML: unsupervised (clustering, t-SNE)

@router.get("/api/unsup/schema")
def unsup_schema():
    from lulc_fetch import unsupervised

    return unsupervised.schema()


class ClusterRequest(BaseModel):
    table: str
    features: list[str]
    categorical: list[str] = Field(default_factory=list)
    method: str = "kmeans"
    params: dict = Field(default_factory=dict)
    prep: dict = Field(default_factory=dict)
    options: dict = Field(default_factory=dict)
    compare: str | None = None
    name: str = Field("clusters", max_length=80)


def _rel(path: str | None) -> str | None:
    return ws.rel(path) if path else None


@router.post("/api/unsup/cluster")
def unsup_cluster(req: ClusterRequest):
    from lulc_fetch import unsupervised

    table = _table_path(req.table)
    if req.method not in unsupervised.METHODS:
        raise HTTPException(400, f"Unknown method {req.method}")

    def run(job):
        rep = unsupervised.cluster(table, features=req.features, categorical=req.categorical, method=req.method, params=req.params,
                                   prep=req.prep, options=req.options, compare=req.compare or None, name=req.name,
                                   table_dir=TABLE_DIR, model_dir=MODEL_DIR)
        rep["output_table"], rep["path"], rep["table"] = _rel(rep["output_table"]), _rel(rep.get("path")), _rel(rep["table"])
        if rep["path"]:
            import json as _json
            Path(str(ws.root() / rep["path"]) + ".json").write_text(_json.dumps(rep, indent=1, default=str))
        return rep

    title = f"{unsupervised.METHODS[req.method]['title']} clustering · {table.name}"
    return jobs.submit("cluster", title, {"source": "Classical ML"}, run).to_dict()


class TsneRequest(BaseModel):
    table: str
    features: list[str]
    categorical: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    prep: dict = Field(default_factory=dict)
    color: str | None = None
    name: str = Field("tsne", max_length=80)


@router.post("/api/unsup/tsne")
def unsup_tsne(req: TsneRequest):
    from lulc_fetch import unsupervised

    table = _table_path(req.table)

    def run(job):
        rep = unsupervised.tsne(table, features=req.features, categorical=req.categorical, params=req.params, prep=req.prep,
                                color=req.color or None, name=req.name, table_dir=TABLE_DIR)
        rep["output_table"], rep["table"] = _rel(rep["output_table"]), _rel(rep["table"])
        return rep

    return jobs.submit("tsne", f"t-SNE · {table.name}", {"source": "Classical ML"}, run).to_dict()


@router.get("/api/models")
def list_models():
    import json as _json

    out = []
    if MODEL_DIR.is_dir():
        for p in sorted(MODEL_DIR.glob("*.joblib"), key=lambda p: -p.stat().st_mtime):
            rep = {}
            side = Path(str(p) + ".json")
            if side.exists():
                try:
                    rep = _json.loads(side.read_text())
                except ValueError:
                    pass
            out.append({"path": ws.rel(p), "name": p.stem, "size_mb": p.stat().st_size / 1e6,
                        "modified": p.stat().st_mtime, **{k: rep.get(k) for k in (
                            "model", "model_title", "task", "target", "features", "accuracy", "kappa", "f1_macro", "r2", "rmse",
                            "classes", "table", "train_rows", "has_proba", "source", "kind", "n_clusters", "method_title")},
                        "silhouette": (rep.get("quality") or {}).get("silhouette")})
    return out


@router.get("/api/models/report")
def model_report(path: str):
    import json as _json

    p = _model_path(path)
    return _json.loads(Path(str(p) + ".json").read_text())


@router.get("/api/models/file")
def model_file(path: str):
    p = _model_path(path)
    return FileResponse(p, filename=p.name)


class SaveReportRequest(BaseModel):
    path: str                              # the model (.joblib)
    folder: str = Field(max_length=1000)


@router.post("/api/models/evaluation/save")
def save_model_evaluation(req: SaveReportRequest):
    """Save a copy of a model's HTML evaluation report in a folder of the user's choice."""
    from lulc_fetch import evaluation

    p = _model_path(req.path)
    folder = _report_folder(req.folder)
    html_path = p.with_suffix(".evaluation.html")
    if not html_path.exists():
        try:
            evaluation.report_for_model(p, html_path)
        except ValueError as e:
            raise HTTPException(404, str(e))
    return {"saved_to": str(_copy_report(html_path, folder))}


@router.get("/api/models/evaluation")
def model_evaluation(path: str, download: bool = False, rebuild: bool = False):
    """The model's HTML evaluation report (rebuilt from the predictions stored in the model if missing)."""
    from lulc_fetch import evaluation

    p = _model_path(path)
    html_path = p.with_suffix(".evaluation.html")
    if rebuild or not html_path.exists():
        try:
            evaluation.report_for_model(p, html_path)
        except ValueError as e:
            raise HTTPException(404, str(e))
    return FileResponse(html_path, media_type="text/html", filename=html_path.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


@router.delete("/api/models")
def delete_model(path: str):
    p = _model_path(path)
    p.unlink(missing_ok=True)
    Path(str(p) + ".json").unlink(missing_ok=True)
    p.with_suffix(".evaluation.html").unlink(missing_ok=True)
    return {"ok": True}


class PredictRequest(BaseModel):
    model: str
    path: str
    band_map: dict[str, int]
    scale: float = 1.0
    offset: float = 0.0
    clip: dict | None = None
    resolution: str = "auto"
    confidence: bool = True
    name: str = Field("classified", max_length=80)


@router.post("/api/ml/predict")
def ml_predict(req: PredictRequest):
    import re

    from lulc_fetch import ml

    model = _model_path(req.model)
    raster = _raster_path(req.path)
    clip = _clip(req.clip)
    if req.resolution not in ("auto", "1", "2", "4", "8", "16"):
        raise HTTPException(400, "Unknown resolution")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:80] or "classified"

    def run(job):
        rep = ml.predict_raster(model, raster, job.dir / f"{stem}.tif", band_map=req.band_map, scale=req.scale,
                                offset=req.offset, clip=clip, resolution=req.resolution, confidence=req.confidence)
        rep["path"] = ws.rel(job.dir / f"{stem}.tif")
        return rep

    return jobs.submit("predict", f"Classify · {req.name}", {"source": "Classical ML"}, run).to_dict()
