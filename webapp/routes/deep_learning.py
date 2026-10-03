"""Deep learning (PyTorch add-on): Train classify model, Classify image, Detect object, Train detection model. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import DL_MODEL_DIR, PATCH_DIR, PatchInput, jobs, log
from ..core import abs_user_folder as _abs_user_folder
from ..core import clip as _clip
from ..core import dl_status as _dl_status
from ..core import raster_path as _raster_path
from ..core import remember_dl as _remember_dl
from ..core import remembered as _remembered
from ..core import report_folder as _report_folder

router = APIRouter()


# ------------------------------------------------------------------ Deep learning (optional PyTorch add-on)



def _addon_dir() -> Path:
    """Where the frozen app installs the deep-learning add-on (pip --target), per Python version."""
    import sys
    return ws.APP_DIR / "addons" / f"py{sys.version_info.major}{sys.version_info.minor}"


@router.get("/api/dl/status")
def dl_status(refresh: bool = False):
    import sys

    st = _dl_status(refresh)
    frozen = bool(getattr(sys, "frozen", False))
    st.update({"platform": sys.platform, "frozen": frozen, "addon_dir": str(_addon_dir()) if frozen else None,
               "can_install": True, "variants": (["cpu", "cuda"] if sys.platform.startswith(("win", "linux")) else ["default"]),
               "size_mb": {"default": 850, "cpu": 1100, "cuda": 3500, "yolo": 150}, "yolo": _yolo_status()})
    return st


def _yolo_status() -> dict:
    """The YOLO & SAM add-on (ultralytics, AGPL-3.0): installed or not, without importing it."""
    import importlib.metadata as md
    import importlib.util

    if importlib.util.find_spec("ultralytics") is None:
        return {"available": False}
    try:
        return {"available": True, "version": md.version("ultralytics")}
    except md.PackageNotFoundError:
        return {"available": True, "version": "?"}


class DlInstallRequest(BaseModel):
    variant: str = Field("default", pattern="^(default|cpu|cuda|yolo)$")


@router.post("/api/dl/install")
def dl_install(req: DlInstallRequest):
    """Install PyTorch + segmentation-models-pytorch (a background job with pip's output in the log)."""
    import importlib
    import subprocess
    import sys

    from lulc_fetch import dl, progress
    frozen = bool(getattr(sys, "frozen", False))
    base = [sys.executable, "--lulc-pip"] if frozen else [sys.executable, "-m", "pip"]
    target = ["--target", str(_addon_dir()), "--upgrade"] if frozen else []
    steps = []
    if req.variant == "yolo":
        if not _dl_status()["available"]:
            raise HTTPException(400, "Install the deep-learning add-on (PyTorch) first")
        if frozen:   # --target ignores what is installed, so keep PyTorch out of the dependencies (it is already there)
            steps.append(["install", "--prefer-binary", *target, "--no-deps", "ultralytics", "ultralytics-thop"])
            steps.append(["install", "--prefer-binary", *target, "opencv-python-headless", "pyyaml", "psutil", "polars", "matplotlib",
                          "cloudpickle", "filelock"])
        else:
            steps.append(["install", "--prefer-binary", "ultralytics"])
    elif req.variant == "cuda":
        steps.append(["install", "--prefer-binary", *target, "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu128"])
        steps.append(["install", "--prefer-binary", *target, "segmentation-models-pytorch"])
    elif req.variant == "cpu" and not sys.platform == "darwin":
        steps.append(["install", "--prefer-binary", *target, "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu"])
        steps.append(["install", "--prefer-binary", *target, "segmentation-models-pytorch"])
    else:
        steps.append(["install", "--prefer-binary", *target, *dl.PACKAGES])

    def run(job):
        if frozen:
            _addon_dir().mkdir(parents=True, exist_ok=True)
        for k, args in enumerate(steps):
            cmd = base + args
            log.info("Running: pip %s", " ".join(args))
            flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=flags)
            try:
                for line in proc.stdout:
                    line = line.rstrip()
                    if not line or line.lstrip().startswith(("━", "|")):
                        continue
                    job.logs.append(line[:300])
                    if line.startswith(("Collecting", "Downloading", "Installing", "Successfully", "Requirement already")):
                        progress.update((k + 0.5) / len(steps), line[:140])
            except progress.Cancelled:
                proc.kill()
                raise
            if proc.wait() != 0:
                raise RuntimeError("pip couldn't install the add-on; see the log above (internet connection?)")
        if frozen and str(_addon_dir()) not in sys.path:
            sys.path.append(str(_addon_dir()))
        importlib.invalidate_caches()
        if req.variant == "yolo":
            ys = _yolo_status()
            if not ys["available"]:
                raise RuntimeError("Installed, but ultralytics can't be found")
            log.info("YOLO & SAM add-on ready: ultralytics %s", ys["version"])
            return {"status": ys, "outputs": []}
        st = _dl_status(force=True)
        if not st["available"]:
            raise RuntimeError(f"Installed, but PyTorch can't be loaded: {st.get('error')}")
        log.info("Deep-learning add-on ready: %s", ", ".join(f"{k} {v}" for k, v in st["packages"].items()))
        return {"status": st, "outputs": []}

    title = "Install the YOLO & SAM add-on" if req.variant == "yolo" else "Install the deep-learning add-on"
    return jobs.submit("dlinstall", title, {"variant": req.variant}, run).to_dict()


@router.get("/api/dl/schema")
def dl_schema():
    from lulc_fetch import dl
    return dl.schema()


@router.get("/api/dl/datasets")
def dl_datasets():
    """Training datasets: the project's training_data/ folder plus folders used before."""
    from lulc_fetch import dl
    seen, out = set(), []
    cands = []
    if PATCH_DIR.path.is_dir():
        cands += [p for p in sorted(PATCH_DIR.path.iterdir()) if (p / "dataset.json").is_file()]
    cands += [Path(x) for x in reversed(_remembered("dl_datasets.json"))]
    for p in cands:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen or not (p / "dataset.json").is_file():
            continue
        seen.add(key)
        try:
            info = dl.inspect_dataset(p)
            out.append({k: info[k] for k in ("folder", "name", "patches", "labelled", "patch_size_px", "band_count", "classes", "created")})
        except (ValueError, OSError):
            continue
    return out


class DlFolderRequest(BaseModel):
    folder: str = Field(max_length=2000)


@router.post("/api/dl/dataset")
def dl_dataset(req: DlFolderRequest):
    from lulc_fetch import dl
    p = _abs_user_folder(req.folder)
    try:
        info = dl.inspect_dataset(p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _remember_dl("dl_datasets.json", str(p))
    return info


def _dl_model_dirs() -> list[Path]:
    cands = []
    if DL_MODEL_DIR.path.is_dir():
        cands += [p for p in DL_MODEL_DIR.path.iterdir() if (p / "model_config.json").is_file()]
    cands += [Path(x) for x in _remembered("dl_models.json")]
    seen, out = set(), []
    for p in cands:
        if (p / "model_config.json").is_file() and (p / "best_model.pt").is_file():
            k = str(p.resolve())
            if k not in seen:
                seen.add(k)
                out.append(p.resolve())
    return out


def _dl_model(folder: str) -> Path:
    p = Path(folder)
    p = (ws.root() / p).resolve() if not p.is_absolute() else p.resolve()
    if p not in _dl_model_dirs():
        raise HTTPException(404, "No such deep-learning model (add it with Browse… first)")
    return p


@router.get("/api/dl/models")
def dl_models():
    import json as _json
    out = []
    for p in _dl_model_dirs():
        try:
            c = _json.loads((p / "model_config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ev = c.get("test") or c.get("val") or {}
        out.append({"folder": str(p), "name": c.get("name") or p.name, "arch": c.get("arch_title"), "arch_key": c.get("arch"), "encoder": c.get("encoder_title"),
                    "bands": c.get("bands"), "in_channels": c.get("in_channels"), "classes": c.get("classes"),
                    "patch_size_px": c.get("patch_size_px"), "pixel_size": c.get("pixel_size"), "trained": c.get("trained"),
                    "miou": ev.get("miou"), "accuracy": ev.get("accuracy"), "epochs_run": c.get("epochs_run"),
                    "best_epoch": c.get("best_epoch"), "has_report": (p / "report.html").is_file(), "in_project": p.is_relative_to(ws.root())})
    out.sort(key=lambda m: m.get("trained") or "", reverse=True)
    return out


@router.post("/api/dl/models/add")
def dl_models_add(req: DlFolderRequest):
    p = _abs_user_folder(req.folder)
    if not ((p / "model_config.json").is_file() and (p / "best_model.pt").is_file()):
        raise HTTPException(400, f"{p} isn't a LULC Fetch deep-learning model folder (needs model_config.json and best_model.pt)")
    _remember_dl("dl_models.json", str(p))
    return {"folder": str(p)}


@router.delete("/api/dl/models")
def dl_models_forget(folder: str):
    p = _dl_model(folder)
    _remember_dl("dl_models.json", str(p), remove=True)
    return {"ok": True, "note": "Removed from the list; the folder was not deleted."}


@router.get("/api/dl/report")
def dl_report(folder: str, download: bool = False):
    p = _dl_model(folder) / "report.html"
    if not p.is_file():
        raise HTTPException(404, "This model has no report")
    return FileResponse(p, media_type="text/html", filename=f"{p.parent.name}_report.html" if download else None,
                        content_disposition_type="attachment" if download else "inline")


class DlTrainRequest(BaseModel):
    dataset: str = Field(max_length=2000)
    arch: str = "unet"
    encoder: str = "tu-mobilenetv3_large_100"
    pretrained: bool = True
    params: dict = Field(default_factory=dict)
    name: str = Field("dl_model", max_length=80)
    folder: str | None = Field(None, max_length=1000)     # where the model folder is created (default: the project's models/)
    resume: str | None = Field(None, max_length=2000)     # a model folder to continue training (its last_model.pt)


@router.post("/api/dl/train")
def dl_train(req: DlTrainRequest):
    import re

    from lulc_fetch import dl, dlrunner
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    if req.arch not in dl.ARCHS:
        raise HTTPException(400, f"Unknown architecture {req.arch}")
    if dl.ARCHS[req.arch]["lib"] == "yolo":
        _need_yolo()
    ds = _abs_user_folder(req.dataset)
    try:
        dl.inspect_dataset(ds)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if req.resume:
        out = _dl_model(req.resume)
        if not (out / "last_model.pt").is_file():
            raise HTTPException(400, "This model has no last_model.pt to continue from")
        resume = out / "last_model.pt"
    else:
        parent = _report_folder(req.folder) if req.folder and req.folder.strip() else DL_MODEL_DIR.path
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "dl_model"
        out, i = parent / stem, 2
        while out.exists() and any(out.iterdir()):
            out = parent / f"{stem}_{i}"
            i += 1
        resume = None
    _remember_dl("dl_datasets.json", str(ds))

    def run(job):
        res = dlrunner.run("train", dataset=str(ds), out_dir=str(out), arch=req.arch, encoder=req.encoder, pretrained=req.pretrained,
                           params=req.params, name=req.name, resume=str(resume) if resume else None)
        _remember_dl("dl_models.json", str(out))
        res["outputs"] = []
        res.pop("history", None)
        return res

    title = f"Deep learning · {dl.ARCHS[req.arch]['title']} · {req.name}"
    return jobs.submit("dltrain", title, {"dataset": ds.name}, run).to_dict()


class DlPredictRequest(BaseModel):
    model: str = Field(max_length=2000)
    inputs: list[PatchInput] = Field(..., min_length=1, max_length=20)
    clip: dict | None = None
    overlap: float = Field(0.25, ge=0, le=0.75)
    batch_size: int = Field(8, ge=1, le=256)
    device: str = Field("auto", pattern="^(auto|cpu|cuda|mps)$")
    confidence: bool = True
    name: str = Field("dl_map", max_length=80)


@router.post("/api/dl/predict")
def dl_predict(req: DlPredictRequest):
    import re

    from lulc_fetch import dlrunner
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    model = _dl_model(req.model)
    from lulc_fetch import dl
    if dl.ARCHS.get(dl.load_config(model).get("arch"), {}).get("lib") == "yolo":
        _need_yolo()
    inputs = [{**i.model_dump(), "path": str(_raster_path(i.path))} for i in req.inputs]
    clip = _clip(req.clip) if req.clip else None
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "dl_map"

    def run(job):
        res = dlrunner.run("predict", model_dir=str(model), inputs=inputs, out_path=str(job.dir / f"{stem}.tif"), clip=clip,
                           overlap=req.overlap, batch_size=req.batch_size, device=req.device, confidence=req.confidence)
        res["path"] = ws.rel(res["path"])
        res["outputs"] = [res["path"]]
        return res

    return jobs.submit("dlpredict", f"Deep learning map · {req.name}", {"model": model.name}, run).to_dict()


@router.get("/api/detect/schema")
def detect_schema():
    from lulc_fetch import detect
    return detect.schema()


class DetectRequest(BaseModel):
    input: PatchInput
    model: str = "fasterrcnn_v2"
    size: str | None = Field(None, pattern="^[nsmlxtb]$")         # YOLO n/s/m/l/x, SAM t/s/b/l
    custom: str | None = Field(None, max_length=2000)             # a model folder from Train detection model (model = "custom")
    sam_refine: str | None = Field(None, pattern="^[tsbl]$")      # turn boxes into outlines with SAM 2.1 of this size
    clip: dict | None = None
    classes: list[str] | None = Field(None, max_length=200)
    score: float = Field(0.4, ge=0.01, le=1)
    zoom: float | str = 1.0                                       # or "auto" for your own models
    overlap: float = Field(0.2, ge=0, le=0.5)
    nms_iou: float = Field(0.5, ge=0.05, le=0.95)
    stretch: str = Field("percent", pattern="^(percent|minmax|byte)$")
    batch_size: int = Field(2, ge=1, le=64)
    device: str = Field("auto", pattern="^(auto|cpu|cuda|mps)$")
    max_size_m: float | None = Field(None, gt=0, le=100000)
    name: str = Field("objects", max_length=80)


def _need_yolo():
    if not _yolo_status()["available"]:
        raise HTTPException(400, "This needs the YOLO & SAM add-on: install it from the tool's panel")


@router.post("/api/detect/run")
def detect_run(req: DetectRequest):
    import json as _json
    import re

    from lulc_fetch import detect, dlrunner
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    custom = None
    if req.model == "custom":
        if not req.custom:
            raise HTTPException(400, "Choose one of your detection models")
        custom = _det_model(req.custom)
        title = custom.name
        _need_yolo()
    elif req.model not in detect.MODELS:
        raise HTTPException(400, f"Unknown model {req.model}")
    else:
        title = detect.MODELS[req.model]["title"]
        if detect.MODELS[req.model]["family"] != "tv":
            _need_yolo()
    if req.sam_refine:
        _need_yolo()
    if isinstance(req.zoom, str):
        if req.zoom != "auto":
            raise HTTPException(400, "Zoom must be a number or auto")
    elif not 0.25 <= req.zoom <= 8:
        raise HTTPException(400, "Zoom must be between 0.25 and 8")
    if req.input.bands is not None and len(req.input.bands) not in (1, 3):
        raise HTTPException(400, "Choose 3 bands (red, green, blue) or 1 band")
    inp = {**req.input.model_dump(), "path": str(_raster_path(req.input.path))}
    clip = _clip(req.clip) if req.clip else None
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "objects"

    def run(job):
        res = dlrunner.run("detect", inputs=[inp], out_path=str(job.dir / f"{stem}.geojson"), model=req.model, size=req.size,
                           custom_dir=str(custom) if custom else None, sam_refine=req.sam_refine, clip=clip, classes=req.classes or None,
                           score=req.score, zoom=req.zoom, overlap=req.overlap, nms_iou=req.nms_iou, stretch=req.stretch,
                           batch_size=req.batch_size, device=req.device, max_size_m=req.max_size_m)
        res["geojson"] = _json.loads(Path(res["path"]).read_text(encoding="utf-8"))
        res["path"] = ws.rel(res["path"])
        res["outputs"] = [res["path"]]
        return res

    return jobs.submit("detect", f"Object detection · {req.name}", {"model": title}, run).to_dict()


# ---------------- detection models (Train detection model)

def _det_model_dirs() -> list[Path]:
    import json as _json
    cands = []
    if DL_MODEL_DIR.path.is_dir():
        cands += [p for p in DL_MODEL_DIR.path.iterdir() if (p / "model_config.json").is_file()]
    cands += [Path(x) for x in _remembered("det_models.json")]
    seen, out = set(), []
    for p in cands:
        if (p / "model_config.json").is_file() and (p / "best.pt").is_file():
            try:
                if _json.loads((p / "model_config.json").read_text(encoding="utf-8")).get("kind") != "detection":
                    continue
            except (OSError, ValueError):
                continue
            k = str(p.resolve())
            if k not in seen:
                seen.add(k)
                out.append(p.resolve())
    return out


def _det_model(folder: str) -> Path:
    p = Path(folder)
    p = (ws.root() / p).resolve() if not p.is_absolute() else p.resolve()
    if p not in _det_model_dirs():
        raise HTTPException(404, "No such detection model (add it with Browse… first)")
    return p


@router.get("/api/det/schema")
def det_schema():
    from lulc_fetch import dettrain
    return dettrain.schema()


@router.get("/api/det/models")
def det_models():
    import json as _json
    out = []
    for p in _det_model_dirs():
        try:
            c = _json.loads((p / "model_config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        v = c.get("val") or {}
        out.append({"folder": str(p), "name": c.get("name") or p.name, "arch": c.get("arch_title"), "task": c.get("task"),
                    "classes": c.get("classes"), "bands": c.get("bands"), "stretch": c.get("stretch"), "tile_px": c.get("tile_px"),
                    "pixel_size": c.get("pixel_size"), "image_pixel_size": c.get("image_pixel_size"), "zoom": c.get("zoom"),
                    "trained": c.get("trained"), "map50": v.get("map50"), "map": v.get("map"), "epochs_run": c.get("epochs_run"),
                    "has_report": (p / "report.html").is_file(), "in_project": p.is_relative_to(ws.root())})
    out.sort(key=lambda m: m.get("trained") or "", reverse=True)
    return out


@router.post("/api/det/models/add")
def det_models_add(req: DlFolderRequest):
    import json as _json
    p = _abs_user_folder(req.folder)
    try:
        ok = (p / "best.pt").is_file() and _json.loads((p / "model_config.json").read_text(encoding="utf-8")).get("kind") == "detection"
    except (OSError, ValueError):
        ok = False
    if not ok:
        raise HTTPException(400, f"{p} isn't a LULC Fetch detection model folder (needs model_config.json and best.pt)")
    _remember_dl("det_models.json", str(p))
    return {"folder": str(p)}


@router.delete("/api/det/models")
def det_models_forget(folder: str):
    p = _det_model(folder)
    _remember_dl("det_models.json", str(p), remove=True)
    return {"ok": True, "note": "Removed from the list; the folder was not deleted."}


@router.get("/api/det/report")
def det_report(folder: str, download: bool = False):
    p = _det_model(folder) / "report.html"
    if not p.is_file():
        raise HTTPException(404, "This model has no report")
    return FileResponse(p, media_type="text/html", filename=f"{p.parent.name}_report.html" if download else None,
                        content_disposition_type="attachment" if download else "inline")


class DetTrainRequest(BaseModel):
    input: PatchInput
    ground_truth: dict                                            # {geojson (EPSG:4326), field, point_size_m}
    task: str = Field("detect", pattern="^(detect|segment|obb)$")
    family: str = Field("yolo26", pattern="^(yolo26|yolo11)$")
    size: str = Field("s", pattern="^[nsmlx]$")
    pretrained: bool = True
    tile_px: int = Field(640, ge=128, le=2048)
    zoom: float = Field(1.0, ge=0.25, le=8)
    overlap: float = Field(0.2, ge=0, le=0.5)
    clip: dict | None = None
    stretch: str = Field("percent", pattern="^(percent|minmax|byte)$")
    params: dict = Field(default_factory=dict)
    class_colors: dict[str, str] | None = None
    name: str = Field("detector", max_length=80)
    folder: str | None = Field(None, max_length=1000)


@router.post("/api/det/train")
def det_train(req: DetTrainRequest):
    import re

    from lulc_fetch import dettrain, dlrunner
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    _need_yolo()
    if req.input.bands is not None and len(req.input.bands) not in (1, 3):
        raise HTTPException(400, "Choose 3 bands (red, green, blue) or 1 band")
    gt = req.ground_truth
    if not isinstance(gt.get("geojson"), dict) or not gt["geojson"].get("features"):
        raise HTTPException(400, "Choose a ground-truth layer with labelled shapes")
    inp = {**req.input.model_dump(), "path": str(_raster_path(req.input.path))}
    clip = _clip(req.clip) if req.clip else None
    parent = _report_folder(req.folder) if req.folder and req.folder.strip() else DL_MODEL_DIR.path
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "detector"
    out = parent / stem
    k = 2
    while out.exists():
        out = parent / f"{stem}_{k}"
        k += 1

    def run(job):
        res = dlrunner.run("train_detector", inputs=[inp], ground_truth={"geojson": gt["geojson"], "field": gt.get("field"),
                           "point_size_m": gt.get("point_size_m")}, out_dir=str(out), name=req.name, task=req.task, family=req.family,
                           size=req.size, pretrained=req.pretrained, tile_px=req.tile_px, zoom=req.zoom, overlap=req.overlap, clip=clip,
                           stretch=req.stretch, params=req.params, class_colors=req.class_colors)
        _remember_dl("det_models.json", str(out))
        res["outputs"] = []
        return res

    title = f"Train detection model · {dettrain.FAMILIES[req.family].split(' ')[0]} · {req.name}"
    return jobs.submit("dettrain", title, {"image": Path(inp["path"]).name}, run).to_dict()
