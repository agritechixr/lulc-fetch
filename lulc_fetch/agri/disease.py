"""Crop disease diagnosis from leaf photos, with the Multi-Crop Disease Decision Support models.

Runs in the deep-learning helper process (lulc_fetch.dlrunner, action "diagnose"). For every photo:

1. photo check: too small / dark / blank photos are refused; blurry or very bright ones only when the models are unsure
2. crop: chosen by you, or detected (the original 16-crop detector; the 36-crop detector when it is sure of an added crop)
3. disease: that crop's ConvNeXt model, top 3 with confidence
4. the photo's GPS position and time (EXIF), when it has them

The limits are the disease app's (vision_model.py), calibrated on the models' test sets.

Models folder: a copy of the disease repository (data/<Crop>/convnext_best.pth, master_model/crop_classifier_best.pth,
master_model/new_crop_detector/convnext_best.pth), or the data/ folder itself.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path

from .. import progress
from . import knowledge
from .import_data import class_names
from .labels import RECOGNISED_ONLY, VARIETY_MODELS, label_display, match_crop

log = logging.getLogger(__name__)

# the published models (half-precision .safetensors, see publish.py): downloaded per model the first time it is needed
HUB_REPO = "ixrbhii/multicrop-disease-models"
HUB_URL = f"https://huggingface.co/{HUB_REPO}/resolve/main/"
HUB_FILE = "model.safetensors"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif"}
WEIGHTS = "convnext_best.pth"
HARD_ISSUES = {"too_small", "too_dark", "no_detail"}
ISSUE_TEXT = {
    "too_small": "photo too small (under 96 pixels)",
    "too_dark": "photo too dark",
    "too_bright": "photo too bright",
    "no_detail": "no detail (blank or plain surface)",
    "blurry": "photo blurry",
    "not_leaf": "not confidently a leaf of a supported crop",
    "low_confidence": "the disease model is unsure",
    "unreadable": "couldn't read the photo",
}


# ------------------------------------------------------------------ models folder

def find_models(folder: str | Path) -> dict:
    """Which model files a folder holds: a copy of the disease repository (or its data/ folder), or the published layout
    (<Crop>/model.safetensors, detectors/{original,new}/model.safetensors, as downloaded from Hugging Face)."""
    root = Path(folder).expanduser()
    data = root / "data" if (root / "data").is_dir() else root
    master = next((p for p in (root / "master_model", root.parent / "master_model") if p.is_dir()), None)
    crops = {}
    for name in knowledge.crops():
        for f in (data / name / WEIGHTS, root / name / HUB_FILE):
            if f.is_file():
                crops[name] = str(f)
                break
    det = {}
    for key, pth in (("original", "crop_classifier_best.pth"), ("new", f"new_crop_detector/{WEIGHTS}")):
        for f in ([master / pth] if master else []) + [root / "detectors" / key / HUB_FILE]:
            if f.is_file():
                det[key] = str(f)
                break
    return {"folder": str(root), "crops": crops, "detectors": det}


def hub_models(folder: str | Path) -> dict:
    """Every published model, at the place in the download folder where it is (or will be) kept."""
    root = Path(folder).expanduser()
    return {"folder": str(root), "hub": True, "crops": {k: str(root / k / HUB_FILE) for k in knowledge.crops()},
            "detectors": {k: str(root / "detectors" / k / HUB_FILE) for k in ("original", "new")}}


def _hub_index(root: Path) -> dict:
    """The published file list with sizes and SHA-256 (index.json), fetched once per run."""
    import requests
    f = root / "index.json"
    try:
        r = requests.get(HUB_URL + "index.json", timeout=30)
        r.raise_for_status()
        root.mkdir(parents=True, exist_ok=True)
        f.write_text(r.text, encoding="utf-8")
    except Exception:
        if not f.is_file():
            raise RuntimeError(f"Couldn't reach Hugging Face ({HUB_REPO}) to download the disease models: check the internet connection")
    return json.loads(f.read_text(encoding="utf-8"))["files"]


def download(path: Path, root: Path, name: str, index: dict):
    """Download one published model (config.json + model.safetensors) into root, checking its size and SHA-256."""
    import hashlib

    import requests
    rel_dir = path.parent.relative_to(root).as_posix()
    for fname in ("config.json", HUB_FILE):
        rel = f"{rel_dir}/{fname}"
        want = index.get(rel)
        if not want:
            raise RuntimeError(f"{rel} isn't in the published models ({HUB_REPO})")
        dest = root / rel
        if dest.is_file() and dest.stat().st_size == want["bytes"]:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        h, done, mb = hashlib.sha256(), 0, want["bytes"] / 1e6
        if fname == HUB_FILE:
            log.info("Downloading the %s model from Hugging Face (%.0f MB, once)", name, mb)
        with requests.get(HUB_URL + rel, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(part, "wb") as out:
                for chunk in r.iter_content(1 << 20):
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if fname == HUB_FILE:
                        progress.update(None, f"Downloading the {name} model: {done / 1e6:.0f} of {mb:.0f} MB")
        if done != want["bytes"] or h.hexdigest() != want["sha256"]:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"The {name} model download was damaged (size or checksum): try again")
        part.replace(dest)


def _classes(kind: str, weights: Path) -> list[str]:
    """Labels in model output order: the ones next to the weights (in case the model was retrained), else the app's copy."""
    folder = weights.parent
    if (folder / "config.json").is_file():   # a published model
        names = json.loads((folder / "config.json").read_text(encoding="utf-8")).get("classes")
        if names:
            return names
    if kind == "original":
        names = class_names(folder, ("class_names.txt", "crop_names.txt"), ("class_mapping.csv", "master_class_mapping.csv", "crop_mapping.csv"))
    else:
        names = class_names(folder)
    if names:
        return names
    m = knowledge.meta()
    return m["detectors"][kind]["classes"] if kind in m["detectors"] else m["crops"][kind]["classes"]


# ------------------------------------------------------------------ photo check

def check_quality(img, min_sharpness: float = 45) -> str | None:
    """Cheap checks before any model runs: too_small, too_dark, no_detail (always refused), blurry, too_bright (refused only
    when the models are unsure), or None."""
    import numpy as np

    if min(img.size) < 96:
        return "too_small"
    gray = img.convert("L")
    gray.thumbnail((512, 512))
    a = np.asarray(gray, dtype=np.float32)
    if np.percentile(a, 90) < 45:   # the brightest part (the leaf): leaves on a black background are fine
        return "too_dark"
    if a.mean() > 235:
        return "too_bright"
    if a.std() < 10:
        return "no_detail"
    lap = a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:] - 4 * a[1:-1, 1:-1]
    if lap.var() < min_sharpness:
        return "blurry"
    return None


def soft_ok(issue: str | None, th: dict, crop_conf: float | None = None, disease_conf: float | None = None) -> bool:
    """A soft problem (blurry, too bright) refuses the photo only if the models aren't confident."""
    if not issue:
        return True
    if issue in HARD_ISSUES:
        return False
    return (crop_conf is None or crop_conf >= th["soft_accept_crop"]) and (disease_conf is None or disease_conf >= th["soft_accept_disease"])


# ------------------------------------------------------------------ EXIF: position and time

def _deg(v) -> float:
    d, m, s = (float(x) for x in v)
    return d + m / 60 + s / 3600


def exif_info(img) -> dict:
    out = {}
    try:
        ex = img.getexif()
        gps = ex.get_ifd(0x8825)
        if gps and 2 in gps and 4 in gps:
            lat, lon = _deg(gps[2]), _deg(gps[4])
            lat *= -1 if str(gps.get(1, "N")).upper().startswith("S") else 1
            lon *= -1 if str(gps.get(3, "E")).upper().startswith("W") else 1
            if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0):
                out["lat"], out["lon"] = round(lat, 7), round(lon, 7)
        taken = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        if taken:
            out["taken"] = str(taken).strip().replace(":", "-", 2)
    except Exception:
        pass
    return out


def open_photo(path: str | Path):
    """The photo as RGB (turned upright as the camera recorded), and its EXIF position / time."""
    from PIL import Image, ImageOps

    if Path(path).suffix.lower() in (".heic", ".heif"):
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            raise ValueError("HEIC photos need the pillow-heif package; save them as JPG instead")
    with Image.open(path) as im:
        info = exif_info(im)
        im = ImageOps.exif_transpose(im)
        return im.convert("RGB"), info


# ------------------------------------------------------------------ models

class _Model:
    def __init__(self, weights: str, classes: list[str], device):
        import timm
        import torch

        try:
            if str(weights).endswith(".safetensors"):   # the published models: half precision, computed in full precision
                from safetensors.torch import load_file
                state = {k: v.float() if v.is_floating_point() else v for k, v in load_file(weights).items()}
            else:
                state = torch.load(weights, map_location="cpu", weights_only=True)   # weights only: no code in the file runs
        except Exception as e:
            raise RuntimeError(f"Couldn't read {weights}: {e}")
        if isinstance(state, dict):
            state = state.get("model_state_dict") or state.get("state_dict") or state
        head = next((k for k in ("head.fc.weight", "classifier.weight", "head.weight") if k in state), None)
        if head and state[head].shape[0] != len(classes):   # labels would land on the wrong outputs
            raise RuntimeError(f"{weights}: the model has {state[head].shape[0]} outputs but {len(classes)} labels were found")
        stem = next((v for k, v in state.items() if "stem" in k and k.endswith("weight") and v.dim() == 4), None)
        width = stem.shape[0] if stem is not None else 96
        blocks = max((int(k.split(".")[3]) for k in state if k.startswith("stages.2.blocks.")), default=26)
        guess = {128: "convnext_base", 192: "convnext_large"}.get(width, "convnext_small" if blocks >= 20 else "convnext_tiny")
        for arch in dict.fromkeys([guess, "convnext_small", "convnext_tiny", "convnext_base", "convnext_large"]):
            try:
                net = timm.create_model(arch, pretrained=False, num_classes=len(classes))
                net.load_state_dict(state, strict=True)
                break
            except Exception:
                continue
        else:
            raise RuntimeError(f"{weights}: the weights don't match any ConvNeXt architecture")
        self.net, self.classes, self.device, self.arch = net.to(device).eval(), classes, device, arch

    def predict(self, img, top_k: int = 3) -> list[tuple[str, float]]:
        import numpy as np
        import torch
        from PIL import Image

        a = np.asarray(img.resize((224, 224), Image.BILINEAR), dtype=np.float32) / 255
        a = (a - (0.485, 0.456, 0.406)) / (0.229, 0.224, 0.225)
        x = torch.from_numpy(a.transpose(2, 0, 1).astype(np.float32)).unsqueeze(0).to(self.device)
        with torch.no_grad():
            p = torch.softmax(self.net(x), 1)[0].float().cpu().numpy()
        order = p.argsort()[::-1][:top_k]
        return [(self.classes[i], float(p[i])) for i in order]


class Models:
    """Loads models when first needed and keeps the most recent few crop models (about 200 MB each) in memory."""

    def __init__(self, found: dict, device: str = "auto", keep: int = 4):
        from .. import dl
        self.found, self.keep, self.cache, self.index = found, keep, OrderedDict(), None
        self.device = dl._device(device)

    @staticmethod
    def _title(key: str) -> str:
        return "crop detector" if key == "original" else "added-crops detector" if key == "new" else knowledge.crops()[key]["name"]

    def get(self, key: str) -> _Model:
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        path = self.found["detectors"].get(key) if key in ("original", "new") else self.found["crops"].get(key)
        if not path:
            raise KeyError(key)
        if self.found.get("hub") and not Path(path).is_file():
            root = Path(self.found["folder"])
            if self.index is None:
                self.index = _hub_index(root)
            download(Path(path), root, self._title(key), self.index)
        log.info("Loading the %s model", self._title(key))
        m = _Model(path, _classes(key, Path(path)), self.device)
        self.cache[key] = m
        crop_keys = [k for k in self.cache if k not in ("original", "new")]
        for k in crop_keys[:max(0, len(crop_keys) - self.keep)]:
            del self.cache[k]
        return m

    def identify(self, img, th: dict) -> dict:
        """Which crop: the original detector, unless the added-crops detector is sure of an added crop."""
        crops = knowledge.crops()
        res = None
        if "original" in self.found["detectors"]:
            top = self.get("original").predict(img)
            res = {"detector": "original", "top": top}
        if "new" in self.found["detectors"]:
            top = self.get("new").predict(img)
            ref = set(knowledge.meta()["new_detector_reference"])
            if res is None or (top[0][0] not in ref and top[0][1] >= th["new_crop_route"]):
                res = {"detector": "new", "top": top}
        if res is None:
            raise RuntimeError("The models folder has no crop detector (master_model/): choose the crop instead of Detect")
        res["top"] = [(match_crop(c, crops) or c, p) for c, p in res["top"]]
        return res


# ------------------------------------------------------------------ diagnose

def _crop_name(c: str) -> str:
    return knowledge.crops()[c]["name"] if c in knowledge.crops() else c.replace("_", " ")


def diagnose_one(models: Models, path: str, crop: str, th: dict, strict: bool = True) -> dict:
    r = {"path": path, "file": Path(path).name, "status": "error", "warnings": []}
    try:
        img, info = open_photo(path)
    except Exception as e:
        r.update(reason="unreadable", note=str(e)[:200])
        return r
    r.update(info, width=img.width, height=img.height)
    issue = check_quality(img, th["sharpness"])
    r["quality"] = issue
    if issue in HARD_ISSUES and strict:
        r.update(status="retake", reason=issue)
        return r
    crop_conf = None
    if crop == "auto":
        idt = models.identify(img, th)
        r["crop_top"] = [{"crop": c, "name": _crop_name(c), "conf": round(p, 4)} for c, p in idt["top"]]
        r["detector"] = idt["detector"]
        crop, crop_conf = idt["top"][0]
        r["crop_conf"] = round(crop_conf, 4)
    r["crop"], r["crop_name"] = crop, _crop_name(crop)
    if crop_conf is not None:
        if strict and issue and not soft_ok(issue, th, crop_conf=crop_conf):
            r.update(status="retake", reason=issue)
            return r
        if crop_conf < th["crop"]:
            if strict:
                r.update(status="retake", reason="not_leaf")
                return r
            r["warnings"].append("not_leaf")
    if crop in RECOGNISED_ONLY or crop not in knowledge.crops():
        r.update(status="no_model", note=f"{_crop_name(crop)} leaves are recognised, but there is no disease model for them")
        return r
    if crop not in models.found["crops"]:
        r.update(status="no_model", note=f"The models folder has no {_crop_name(crop)} model (data/{crop}/{WEIGHTS})")
        return r
    top = models.get(crop).predict(img)
    r["top"] = [{"label": lab, "name": label_display(crop, lab), "conf": round(p, 4)} for lab, p in top]
    label, conf = top[0]
    r.update(label=label, diagnosis=label_display(crop, label), conf=round(conf, 4),
             kind="variety" if crop in VARIETY_MODELS else "disease", healthy="healthy" in label.lower())
    refused = conf < th["disease"] and "low_confidence" or (issue and not soft_ok(issue, th, disease_conf=conf) and issue)
    if refused and strict:
        r.update(status="retake", reason=refused)
        return r
    if refused:
        r["warnings"].append(refused)
    elif issue and issue not in r["warnings"]:
        r["warnings"].append(issue)
    r["status"] = "variety" if r["kind"] == "variety" else "healthy" if r["healthy"] else "disease"
    return r


CSV_COLS = ["file", "status", "crop", "crop_confidence", "diagnosis", "confidence", "second", "second_confidence", "third",
            "third_confidence", "reason", "lat", "lon", "taken", "path"]


def _row(r: dict) -> dict:
    top = r.get("top") or []
    return {"file": r["file"], "status": r["status"], "crop": r.get("crop_name", ""), "crop_confidence": r.get("crop_conf", ""),
            "diagnosis": r.get("diagnosis", ""), "confidence": r.get("conf", ""),
            "second": top[1]["name"] if len(top) > 1 else "", "second_confidence": top[1]["conf"] if len(top) > 1 else "",
            "third": top[2]["name"] if len(top) > 2 else "", "third_confidence": top[2]["conf"] if len(top) > 2 else "",
            "reason": "; ".join(ISSUE_TEXT.get(x, x) for x in ([r["reason"]] if r.get("reason") else []) + r.get("warnings", [])) or r.get("note", ""),
            "lat": r.get("lat", ""), "lon": r.get("lon", ""), "taken": r.get("taken", ""), "path": r["path"]}


def diagnose(photos: list[str], models_dir: str, out_dir: str, name: str = "diagnosis", crop: str = "auto", strict: bool = True,
             device: str = "auto", thresholds: dict | None = None, hub: bool = False) -> dict:
    """Diagnose each photo; writes <name>.csv (one row per photo) and <name>.geojson (photos with a GPS position).
    hub=True: models_dir is the download folder, and models missing there are downloaded from Hugging Face when needed."""
    t0 = time.time()
    th = {**knowledge.meta()["thresholds"], **(thresholds or {})}
    found = hub_models(models_dir) if hub else find_models(models_dir)
    if crop != "auto" and crop not in knowledge.crops():
        raise ValueError(f"Unknown crop {crop}")
    if crop == "auto" and not found["detectors"]:
        raise RuntimeError(f"No crop detector in {models_dir} (master_model/crop_classifier_best.pth): choose the crop instead of Detect")
    if crop != "auto" and crop not in found["crops"]:
        raise RuntimeError(f"No {_crop_name(crop)} model in {models_dir} (data/{crop}/{WEIGHTS})")
    models = Models(found, device)
    log.info("Diagnosing %d photo%s on %s · %s", len(photos), "s" if len(photos) != 1 else "", models.device,
             "crop detected from each photo" if crop == "auto" else _crop_name(crop))
    results = []
    for i, p in enumerate(photos):
        progress.update(i / max(len(photos), 1), f"Photo {i + 1} of {len(photos)}: {Path(p).name}")
        try:
            r = diagnose_one(models, p, crop, th, strict)
        except progress.Cancelled:
            raise
        except Exception as e:
            log.warning("%s: %s", Path(p).name, e)
            r = {"path": p, "file": Path(p).name, "status": "error", "warnings": [], "note": str(e)[:300]}
        results.append(r)
        log.info("%s → %s", r["file"], f"{r.get('crop_name')}: {r['diagnosis']} ({100 * r['conf']:.0f} %)" if r["status"] in ("disease", "healthy", "variety")
                 else ISSUE_TEXT.get(r.get("reason"), r.get("note") or r["status"]))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{name}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, CSV_COLS)
        w.writeheader()
        w.writerows(_row(r) for r in results)
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
              "properties": {k: v for k, v in _row(r).items() if k not in ("lat", "lon")}} for r in results if "lat" in r]
    gj_path = None
    if feats:
        gj_path = out / f"{name}.geojson"
        gj_path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    summary = {}
    for r in results:
        if r["status"] in ("disease", "healthy", "variety"):
            k = (r["crop_name"], r["diagnosis"], r["status"])
            summary[k] = summary.get(k, 0) + 1
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("disease", "healthy", "variety", "retake", "no_model", "error")}
    progress.update(1, "Done")
    return {"photos": results, "csv": str(csv_path), "geojson_path": str(gj_path) if gj_path else None, "located": len(feats),
            "counts": counts, "summary": [{"crop": c, "diagnosis": d, "status": s, "count": n} for (c, d, s), n in
                                          sorted(summary.items(), key=lambda x: (-x[1], x[0]))],
            "device": str(models.device), "seconds": round(time.time() - t0, 1)}
