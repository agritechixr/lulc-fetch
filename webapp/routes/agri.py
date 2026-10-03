"""Agri menu: Diagnose crop disease (photos → disease, with advice) and the Crop disease guide. The science is in
lulc_fetch/agri/; this file only checks the request and runs it as a job. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import core
from .. import workspace as ws

router = APIRouter()

# where a copy of the disease repository (with its model weights) is often kept, tried when no models folder is chosen yet
_AGRI_GUESSES = ("Desktop/Farmer_ai", "Farmer_ai", "Desktop/multicrop-disease-decision-support", "multicrop-disease-decision-support",
                 "Documents/Farmer_ai", "Documents/multicrop-disease-decision-support")


def _agri_settings() -> dict:
    return core.read_settings("agri.json")


AGRI_HUB_DIR = ws.APP_DIR / "agri_models"   # models downloaded from Hugging Face (shared by all projects)


def _agri_models() -> dict:
    """Where the models come from: a chosen local folder, one found in a usual place, or else Hugging Face (downloaded into
    AGRI_HUB_DIR when first needed)."""
    from lulc_fetch.agri.disease import find_models, hub_models

    st = _agri_settings()
    saved = st.get("models")
    if st.get("source") != "hub":
        if saved and Path(saved).is_dir():
            return {**find_models(saved), "chosen": True, "source": "folder"}
        if not saved:
            for g in _AGRI_GUESSES:
                f = find_models(Path.home() / g)
                if f["crops"] or f["detectors"]:
                    return {**f, "chosen": False, "source": "folder"}
    return {**hub_models(AGRI_HUB_DIR), "chosen": st.get("source") == "hub", "source": "hub"}


def _agri_status() -> dict:
    from lulc_fetch.agri.disease import HUB_REPO, find_models
    m = _agri_models()
    out = {"source": m["source"], "folder": m["folder"], "chosen": m["chosen"], "crops": sorted(m["crops"]), "detectors": sorted(m["detectors"])}
    if m["source"] == "hub":
        have = find_models(AGRI_HUB_DIR)
        out.update(repo=HUB_REPO, url=f"https://huggingface.co/{HUB_REPO}", downloaded=sorted(have["crops"]) + [f"detector:{k}" for k in have["detectors"]],
                   downloaded_mb=round(sum(Path(p).stat().st_size for p in [*have["crops"].values(), *have["detectors"].values()]) / 1e6))
    return out


@router.get("/api/agri/schema")
def agri_schema():
    from lulc_fetch.agri import knowledge
    return {**knowledge.schema(), "models": _agri_status()}


class AgriModelsRequest(BaseModel):
    folder: str | None = Field(None, max_length=2000)   # a local models folder…
    source: str | None = Field(None, pattern="^hub$")    # …or "hub": download from Hugging Face


@router.post("/api/agri/models")
def agri_set_models(req: AgriModelsRequest):
    from lulc_fetch.agri.disease import find_models
    if req.source == "hub":
        settings = {**_agri_settings(), "source": "hub"}
    elif req.folder:
        folder = core.abs_user_folder(req.folder)
        f = find_models(folder)
        if not f["crops"] and not f["detectors"]:
            raise HTTPException(400, f"No disease models in {folder}: choose the disease app's folder (with data/<Crop>/convnext_best.pth "
                                     "and master_model/)")
        settings = {**_agri_settings(), "models": str(folder), "source": "folder"}
    else:
        raise HTTPException(400, "Give a models folder, or source = hub")
    try:
        core.write_settings("agri.json", settings)
    except OSError as e:
        raise HTTPException(500, f"Couldn't save the setting: {e}")
    return _agri_status()


def _agri_photo(path: str) -> Path:
    from lulc_fetch.agri.disease import PHOTO_EXTS
    p = Path(path)
    p = (p if p.is_absolute() else ws.root() / p).resolve()
    if p.suffix.lower() not in PHOTO_EXTS or not p.is_file():
        raise HTTPException(404, f"No such photo: {path}")
    return p


@router.post("/api/agri/photos/upload")
async def agri_upload_photos(files: list[UploadFile] = File(...)):
    """Photos added from the computer: kept in the workspace's uploads/photos/<batch>/."""
    import shutil
    import uuid

    from lulc_fetch.agri.disease import PHOTO_EXTS
    dest = ws.root() / "uploads" / "photos" / uuid.uuid4().hex[:8]
    out, skipped = [], []
    for f in files:
        name = Path(f.filename or "").name
        if Path(name).suffix.lower() not in PHOTO_EXTS:
            skipped.append(name)
            continue
        dest.mkdir(parents=True, exist_ok=True)
        p = core.unique(dest, name)
        with open(p, "wb") as fh:
            shutil.copyfileobj(f.file, fh, length=8 << 20)
        out.append({"path": str(p), "name": p.name})
    return {"photos": out, "skipped": skipped}


@router.get("/api/agri/photos/folder")
def agri_folder_photos(path: str, recursive: bool = False):
    """The photos in a folder (and its sub-folders when recursive), up to 5,000."""
    from lulc_fetch.agri.disease import PHOTO_EXTS
    folder = core.abs_user_folder(path)
    it = folder.rglob("*") if recursive else folder.iterdir()
    out = []
    for p in it:
        if p.suffix.lower() in PHOTO_EXTS and p.is_file() and not p.name.startswith("."):
            out.append({"path": str(p), "name": str(p.relative_to(folder))})
            if len(out) >= 5000:
                break
    out.sort(key=lambda x: x["name"].lower())
    return {"folder": str(folder), "photos": out, "truncated": len(out) >= 5000}


@router.get("/api/agri/photo")
def agri_photo(path: str, size: int = 256):
    """A photo as a JPEG, upright and at most size pixels (thumbnails in the tool, the full photo with size=0)."""
    import io

    from fastapi.responses import Response

    from lulc_fetch.agri.disease import open_photo
    p = _agri_photo(path)
    try:
        img, _ = open_photo(p)
    except Exception as e:
        raise HTTPException(400, f"Couldn't read {p.name}: {e}")
    if size > 0:
        img.thumbnail((min(size, 2048), min(size, 2048)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


class DiagnoseRequest(BaseModel):
    photos: list[str] = Field(min_length=1, max_length=5000)
    crop: str = Field("auto", max_length=40)
    strict: bool = True                        # refuse unclear photos (the disease app's photo check)
    device: str = Field("auto", pattern="^(auto|cpu|cuda|mps)$")
    name: str = Field("diagnosis", max_length=80)


@router.post("/api/agri/diagnose")
def agri_diagnose(req: DiagnoseRequest):
    import json as _json
    import shutil

    from lulc_fetch import dlrunner
    from lulc_fetch.agri import knowledge
    core.require_dl()
    m = _agri_models()
    if not (m["crops"] or m["detectors"]):
        raise HTTPException(400, "Choose the disease models folder first")
    if req.crop != "auto":
        if req.crop not in knowledge.crops():
            raise HTTPException(400, f"Unknown crop {req.crop}")
        if req.crop not in m["crops"]:
            raise HTTPException(400, f"The models folder has no {knowledge.crops()[req.crop]['name']} model")
    elif not m["detectors"]:
        raise HTTPException(400, "The models folder has no crop detector (master_model/): choose the crop")
    from lulc_fetch.agri.disease import PHOTO_EXTS
    photos = [str(Path(p) if Path(p).is_absolute() else ws.root() / p) for p in req.photos]
    if any(Path(p).suffix.lower() not in PHOTO_EXTS for p in photos):
        raise HTTPException(400, "Only photos (JPG, PNG, WebP, BMP, TIFF, HEIC) can be diagnosed")
    if not any(Path(p).is_file() for p in photos):   # a missing photo among others is reported in its row
        raise HTTPException(404, "None of the photos exist any more: add them again")
    stem = core.safe_stem(req.name, "diagnosis")

    def run(job):
        res = dlrunner.run("diagnose", photos=photos, models_dir=m["folder"], out_dir=str(job.dir), name=stem, crop=req.crop,
                           strict=req.strict, device=req.device, hub=m["source"] == "hub")
        res["geojson"] = _json.loads(Path(res["geojson_path"]).read_text(encoding="utf-8")) if res["geojson_path"] else None
        core.TABLE_DIR.mkdir(exist_ok=True)   # the data viewer opens tables from tables/ (never overwrite an earlier one)
        table = core.unique(core.TABLE_DIR.path, Path(res["csv"]).name)
        shutil.copyfile(res["csv"], table)
        res["outputs"] = [ws.rel(res["csv"])] + ([ws.rel(res["geojson_path"])] if res["geojson_path"] else [])
        res["csv"] = ws.rel(table)
        res["geojson_path"] = ws.rel(res["geojson_path"]) if res["geojson_path"] else None
        return res

    crop = "crop detected" if req.crop == "auto" else knowledge.crops()[req.crop]["name"]
    return core.jobs.submit("diagnose", f"Crop disease · {len(photos)} photo{'s' if len(photos) > 1 else ''} · {req.name}",
                       {"crop": crop}, run).to_dict()


@router.get("/api/agri/guide/diseases")
def agri_guide_diseases(crop: str):
    from lulc_fetch.agri import knowledge
    if crop not in knowledge.crops():
        raise HTTPException(404, f"Unknown crop {crop}")
    return {"crop": crop, "diseases": knowledge.diseases(crop)}


@router.get("/api/agri/guide/search")
def agri_guide_search(crop: str, disease: str | None = None, q: str | None = None, section: str | None = None, limit: int = 200):
    from lulc_fetch.agri import knowledge
    if crop not in knowledge.crops():
        raise HTTPException(404, f"Unknown crop {crop}")
    return knowledge.search(crop, disease=disease or None, q=(q or "")[:200], section=section or None, limit=max(1, min(limit, 1000)))
