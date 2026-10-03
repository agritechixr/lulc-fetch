"""Deep-learning semantic segmentation: train on a "Make training data" folder, then map whole images.

PyTorch is an optional add-on (``pip install torch torchvision segmentation-models-pytorch``); nothing here
imports it until it is needed, so the rest of LULC Fetch works without it.

Models: U-Net, U-Net++, DeepLabV3, DeepLabV3+, PSPNet, FPN, LinkNet, SegFormer (segmentation-models-pytorch)
and FCN, LR-ASPP (torchvision), with MobileNetV2 / V3, ResNet or EfficientNet backbones. Any number of input
bands works; ImageNet weights are adapted to the band count.

Training output (one folder)::

    best_model.pt        weights with the best validation score (+ everything needed to use it)
    last_model.pt        weights, optimiser and epoch of the last epoch (to resume training)
    model_config.json    architecture, bands, normalisation, classes, patch size, settings, scores
    training_log.csv     one row per epoch
    report.html          curves, confusion matrix, per-class scores, example predictions
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
import time
from pathlib import Path

import numpy as np
import rasterio

from . import progress

log = logging.getLogger(__name__)

PACKAGES = ["torch", "torchvision", "segmentation-models-pytorch"]

ARCHS = {
    "unet": {"title": "U-Net", "lib": "smp", "cls": "Unet", "accuracy": 4, "speed": 4,
             "desc": "Encoder–decoder with skip connections: sharp boundaries, works well with little data. A good default."},
    "unetpp": {"title": "U-Net++", "lib": "smp", "cls": "UnetPlusPlus", "accuracy": 5, "speed": 2,
               "desc": "U-Net with nested skip connections: often slightly more accurate, slower and heavier."},
    "deeplabv3plus": {"title": "DeepLabV3+", "lib": "smp", "cls": "DeepLabV3Plus", "accuracy": 5, "speed": 3,
                      "desc": "Atrous spatial pyramid pooling + decoder: handles objects of many sizes (fields, urban blocks)."},
    "deeplabv3": {"title": "DeepLabV3", "lib": "smp", "cls": "DeepLabV3", "accuracy": 4, "speed": 3,
                  "desc": "Multi-scale context with atrous convolutions; smoother boundaries than V3+."},
    "pspnet": {"title": "PSPNet", "lib": "smp", "cls": "PSPNet", "accuracy": 3, "speed": 5,
               "desc": "Pyramid pooling of global context: fast, good for large uniform regions, coarser edges."},
    "fpn": {"title": "FPN", "lib": "smp", "cls": "FPN", "accuracy": 4, "speed": 4,
            "desc": "Feature pyramid network: fast multi-scale model, a good alternative to U-Net."},
    "linknet": {"title": "LinkNet", "lib": "smp", "cls": "Linknet", "accuracy": 3, "speed": 5,
                "desc": "Very light encoder–decoder: fastest training, fine for simple maps."},
    "segformer": {"title": "SegFormer", "lib": "smp", "cls": "Segformer", "accuracy": 4, "speed": 3,
                  "desc": "Transformer-style all-MLP decoder: strong global context."},
    "fcn": {"title": "FCN", "lib": "tv", "accuracy": 3, "speed": 3,
            "desc": "Fully convolutional network (torchvision): the classic segmentation baseline.", "encoders": ["resnet50", "resnet101"]},
    "lraspp": {"title": "LR-ASPP", "lib": "tv", "accuracy": 3, "speed": 5,
               "desc": "Lite reduced ASPP on MobileNetV3 (torchvision): tiny and fast, made for mobile devices.",
               "encoders": ["mobilenetv3_large"]},
    "yolo_sem": {"title": "YOLO26 semantic", "lib": "yolo", "accuracy": 4, "speed": 5,
                 "desc": "YOLO26 semantic segmentation (ultralytics): a fast real-time network, pretrained on Cityscapes street scenes. "
                         "Needs the YOLO & SAM add-on. Any number of bands.",
                 "encoders": ["yolo-n", "yolo-s", "yolo-m", "yolo-l", "yolo-x"]},
}
# the light segmentation models (lulc_fetch/lightseg): 0.1–1 M parameters, any number of bands, no separate backbone
from .lightseg import MODELS as _LIGHT   # noqa: E402  (no PyTorch needed to list them)

_SPEED = {"tinyunet": 4, "enet": 5, "lsnet": 5, "lednet": 5, "leanet": 5, "efsnet": 5, "adscnet": 4, "cgnet": 4, "dabnet": 4, "fpenet": 4, "fddwnet": 3}
_ACC = {"tinyunet": 4, "dabnet": 4, "lednet": 4, "leanet": 4, "fddwnet": 4, "cgnet": 4, "fpenet": 3, "adscnet": 3, "lsnet": 3, "enet": 3, "efsnet": 3}
for _k, (_title, _mod, _paper, _mparams, _about) in _LIGHT.items():
    ARCHS[f"light_{_k}"] = {"title": _title, "lib": "light", "accuracy": _ACC[_k], "speed": _SPEED[_k], "encoders": ["builtin"], "params_m": _mparams,
                            "desc": f"{_about}. A light model ({_mparams:g} M parameters, {_paper}): trained from scratch, every band used "
                                    "directly, so it suits embeddings (64 / 128 bands) and small datasets."}

ENCODERS = {
    "builtin": "Built in (light model, no separate backbone)",
    "mobilenet_v2": "MobileNetV2 (fast, 2 M params)",
    "tu-mobilenetv3_large_100": "MobileNetV3 large (fast, 3 M)",
    "tu-mobilenetv3_small_100": "MobileNetV3 small (fastest, 1 M)",
    "resnet18": "ResNet-18 (11 M)",
    "resnet34": "ResNet-34 (21 M)",
    "resnet50": "ResNet-50 (23 M)",
    "efficientnet-b0": "EfficientNet-B0 (4 M)",
    "efficientnet-b2": "EfficientNet-B2 (8 M)",
    "efficientnet-b4": "EfficientNet-B4 (18 M)",
    # torchvision-only backbones
    "resnet101": "ResNet-101 (43 M)",
    "mobilenetv3_large": "MobileNetV3 large",
    # YOLO26 semantic sizes
    "yolo-n": "YOLO26 nano (fastest)", "yolo-s": "YOLO26 small", "yolo-m": "YOLO26 medium", "yolo-l": "YOLO26 large",
    "yolo-x": "YOLO26 extra large (most accurate)",
}
SMP_ENCODERS = [k for k in ENCODERS if k not in ("resnet101", "mobilenetv3_large", "builtin") and not k.startswith("yolo-")]

PARAMS = [  # (name, title, kind, default, extra) — rendered by the UI; "adv" = advanced section
    {"name": "epochs", "title": "Epochs", "kind": "int", "default": 50, "min": 1, "max": 1000,
     "tip": "How many times the model sees every training patch. Early stopping usually ends sooner."},
    {"name": "batch_size", "title": "Batch size", "kind": "int", "default": 8, "min": 1, "max": 256,
     "tip": "Patches per training step. Bigger is faster but needs more memory; it is halved automatically if memory runs out."},
    {"name": "lr", "title": "Learning rate", "kind": "float", "default": 1e-3, "min": 1e-6, "max": 1,
     "tip": "Step size of the optimiser. 1e-3 suits AdamW; use ~1e-2 for SGD. Lower it if the loss jumps around."},
    {"name": "val_share", "title": "Validation (%)", "kind": "int", "default": 20, "min": 5, "max": 50,
     "tip": "Patches held back to check the model after every epoch (early stopping and the best model use this)."},
    {"name": "test_share", "title": "Test (%)", "kind": "int", "default": 0, "min": 0, "max": 40,
     "tip": "Optional patches never used during training: scored once at the end with the best model, for an independent result."},
    {"name": "split", "title": "Split", "kind": "choice", "default": "blocks",
     "choices": [["blocks", "Spatial blocks (honest)"], ["random", "Random patches"]],
     "tip": "Spatial blocks keep neighbouring (overlapping) patches on the same side, so validation pixels are never seen in training. Random is optimistic when patches overlap."},
    {"name": "early_stop", "title": "Early stopping", "kind": "bool", "default": True,
     "tip": "Stop when the validation score hasn't improved for a while, and keep the best epoch."},
    {"name": "patience", "title": "Patience (epochs)", "kind": "int", "default": 10, "min": 1, "max": 200,
     "tip": "Epochs without improvement before stopping."},
    {"name": "monitor", "title": "Watch", "kind": "choice", "default": "val_miou",
     "choices": [["val_miou", "Validation mIoU (higher is better)"], ["val_loss", "Validation loss (lower is better)"]],
     "tip": "The score used for early stopping, the learning-rate plateau schedule and picking the best model."},
    {"name": "min_delta", "title": "Minimum improvement", "kind": "float", "default": 0.001, "min": 0, "max": 1,
     "tip": "A change smaller than this doesn't count as an improvement."},
    {"name": "loss", "title": "Loss", "kind": "choice", "default": "ce_dice", "adv": True,
     "choices": [["ce_dice", "Cross-entropy + Dice (recommended)"], ["wce", "Weighted cross-entropy"], ["ce", "Cross-entropy"],
                 ["dice", "Dice"], ["focal", "Focal"]],
     "tip": "Dice, focal and class weights help rare classes (e.g. water in a mostly urban scene)."},
    {"name": "class_weights", "title": "Class weights", "kind": "choice", "default": "auto", "adv": True,
     "choices": [["auto", "Auto (rarer classes count more)"], ["none", "None"]],
     "tip": "Weights from the training pixel counts (median-frequency balancing, capped at 10×). Used by cross-entropy and focal loss."},
    {"name": "optimizer", "title": "Optimiser", "kind": "choice", "default": "adamw", "adv": True,
     "choices": [["adamw", "AdamW"], ["adam", "Adam"], ["sgd", "SGD + momentum"]], "tip": "AdamW is a robust default."},
    {"name": "weight_decay", "title": "Weight decay", "kind": "float", "default": 1e-4, "min": 0, "max": 1, "adv": True,
     "tip": "Regularisation that keeps weights small (less overfitting)."},
    {"name": "scheduler", "title": "Learning-rate schedule", "kind": "choice", "default": "cosine", "adv": True,
     "choices": [["cosine", "Cosine decay"], ["plateau", "Reduce on plateau"], ["onecycle", "One-cycle"], ["none", "Constant"]],
     "tip": "How the learning rate changes during training."},
    {"name": "augment", "title": "Augmentation", "kind": "multi", "default": ["flip", "rot90"], "adv": True,
     "choices": [["flip", "Flips"], ["rot90", "90° rotations"], ["bright", "Brightness ±10 %"], ["noise", "Small noise"]],
     "tip": "Random changes to training patches so the model generalises better. Flips and rotations are safe for satellite images."},
    {"name": "freeze_epochs", "title": "Freeze backbone for (epochs)", "kind": "int", "default": 0, "min": 0, "max": 100, "adv": True,
     "tip": "Train only the decoder at first (with pretrained weights); helps with very small datasets."},
    {"name": "amp", "title": "Mixed precision", "kind": "choice", "default": "auto", "adv": True,
     "choices": [["auto", "Auto (on for NVIDIA GPUs)"], ["on", "On"], ["off", "Off"]], "tip": "Faster and lighter on NVIDIA GPUs."},
    {"name": "device", "title": "Device", "kind": "choice", "default": "auto", "adv": True,
     "choices": [["auto", "Auto"], ["cuda", "NVIDIA GPU (CUDA)"], ["mps", "Apple GPU (MPS)"], ["cpu", "CPU"]],
     "tip": "Auto uses an NVIDIA GPU, else the Apple GPU, else the CPU."},
    {"name": "seed", "title": "Random seed", "kind": "int", "default": 42, "min": 0, "max": 2 ** 31 - 1, "adv": True,
     "tip": "Same seed + same settings = same split and (nearly) the same model."},
]
DEFAULTS = {p["name"]: p["default"] for p in PARAMS}
IGNORE = 255
MAX_CACHE_BYTES = 2.0e9


# ------------------------------------------------------------------ availability / add-on
def status() -> dict:
    out = {"available": False, "packages": {}, "devices": ["cpu"], "device": "cpu"}
    try:
        import torch
        out["packages"]["torch"] = torch.__version__
        import torchvision
        out["packages"]["torchvision"] = torchvision.__version__
        import segmentation_models_pytorch as smp
        out["packages"]["segmentation-models-pytorch"] = smp.__version__
        if torch.cuda.is_available():
            out["devices"].insert(0, "cuda")
            out["gpu"] = torch.cuda.get_device_name(0)
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            out["devices"].insert(0, "mps")
            out["gpu"] = out.get("gpu") or "Apple GPU (Metal)"
        out["device"] = out["devices"][0]
        out["available"] = True
    except Exception as e:   # not installed, or a broken install
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def schema() -> dict:
    return {"archs": ARCHS, "encoders": ENCODERS, "smp_encoders": SMP_ENCODERS, "params": PARAMS, "defaults": DEFAULTS}


def _device(name: str):
    import torch
    if name == "cuda" or (name == "auto" and torch.cuda.is_available()):
        if not torch.cuda.is_available():
            raise ValueError("No NVIDIA GPU (CUDA) is available on this computer")
        return torch.device("cuda")
    if name == "mps" or (name == "auto" and torch.backends.mps.is_available()):
        if not torch.backends.mps.is_available():
            raise ValueError("The Apple GPU (MPS) isn't available on this computer")
        return torch.device("mps")
    return torch.device("cpu")


# ------------------------------------------------------------------ dataset
def inspect_dataset(folder: str | Path) -> dict:
    """Summary of a "Make training data" folder (dataset.json + images/ + labels/)."""
    folder = Path(folder)
    meta_p = folder / "dataset.json"
    if not meta_p.is_file():
        raise ValueError(f"{folder} has no dataset.json: choose a folder made with Make training data")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    imgs = sorted((folder / (meta.get("images_dir") or "images")).glob("*.tif"))
    lab_dir = folder / (meta.get("labels_dir") or "labels") if meta.get("labels_dir") else None
    labelled = [p for p in imgs if lab_dir and (lab_dir / p.name).is_file()]
    return {"folder": str(folder), "name": meta.get("name") or folder.name, "patches": len(imgs), "labelled": len(labelled),
            "patch_size_px": meta.get("patch_size_px"), "patch_size_m": meta.get("patch_size_m"), "pixel_size": meta.get("pixel_size"),
            "bands": meta.get("bands") or [], "band_count": meta.get("band_count"), "dtype": meta.get("dtype"),
            "classes": meta.get("classes") or [], "crs": meta.get("crs"), "created": meta.get("created"),
            "overlap_px": meta.get("overlap_px"), "stride_px": meta.get("stride_px"),
            "size_mb": round(sum(p.stat().st_size for p in imgs) / 1e6, 1) if imgs else 0}


def _read_index(folder: Path, meta: dict, files: list[str]) -> dict[str, dict]:
    """Per-patch row / col / bounds from patches.csv (falls back to the file name and georeferencing)."""
    out = {}
    idx = folder / "patches.csv"
    if idx.is_file():
        with open(idx, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[r["file"]] = {"row": int(r["row"]), "col": int(r["col"]),
                                  "bounds": [float(r["x_min"]), float(r["y_min"]), float(r["x_max"]), float(r["y_max"])]}
    for name in files:
        if name not in out:
            m = re.search(r"_r(\d+)_c(\d+)\.tif$", name)
            with rasterio.open(folder / (meta.get("images_dir") or "images") / name) as s:
                b = s.bounds
            out[name] = {"row": int(m.group(1)) if m else 0, "col": int(m.group(2)) if m else 0, "bounds": [b.left, b.bottom, b.right, b.top]}
    return out


def _split(files, index, val_share, test_share, method, seed, stride_px, patch_px):
    """Train / val / test lists. Spatial blocks group neighbouring patches; overlapping training patches that touch
    validation or test patches are dropped so no pixel is in two splits."""
    rng = np.random.default_rng(seed)
    n = len(files)
    if method == "random" or n < 10:
        order = rng.permutation(n)
        nv, nt = max(1, round(n * val_share)), round(n * test_share)
        val = {files[i] for i in order[:nv]}
        test = {files[i] for i in order[nv:nv + nt]}
    else:
        rows = np.array([index[f]["row"] for f in files])
        cols = np.array([index[f]["col"] for f in files])
        ov = 1
        if stride_px and patch_px:
            ov = max(1, math.ceil(max(patch_px[0] / max(stride_px[0], 1), patch_px[1] / max(stride_px[1], 1))))
        span = max(rows.max() - rows.min() + 1, cols.max() - cols.min() + 1)
        block = max(ov * 2, int(round(span / 6)) or 1)   # ~36 blocks over the area, never smaller than the overlap
        keys = [(r // block, c // block) for r, c in zip(rows, cols)]
        blocks = sorted(set(keys))
        rng.shuffle(blocks)
        by_block = {}
        for f, k in zip(files, keys):
            by_block.setdefault(k, []).append(f)
        val, test = set(), set()
        for b in blocks:
            if len(val) < n * val_share:
                val.update(by_block[b])
            elif len(test) < n * test_share:
                test.update(by_block[b])
        if not val:
            val.update(by_block[blocks[0]])
    held = val | test
    train = [f for f in files if f not in held]
    # purge: training patches overlapping a held-out patch (same pixels)
    hb = np.array([index[f]["bounds"] for f in held]) if held else np.zeros((0, 4))
    eps = 1e-6
    kept, dropped = [], 0
    for f in train:
        x0, y0, x1, y1 = index[f]["bounds"]
        if len(hb) and np.any((hb[:, 0] < x1 - eps) & (hb[:, 2] > x0 + eps) & (hb[:, 1] < y1 - eps) & (hb[:, 3] > y0 + eps)):
            dropped += 1
        else:
            kept.append(f)
    return kept, [f for f in files if f in val], [f for f in files if f in test], dropped


class _Patches:
    """Image / label patches as normalised float32 arrays (cached in memory when they fit)."""

    def __init__(self, img_dir, lab_dir, files, mean, std, lut, cache):
        self.img_dir, self.lab_dir, self.files = img_dir, lab_dir, files
        self.mean, self.std, self.lut = mean[:, None, None], std[:, None, None], lut
        self.cache = {} if cache else None

    def __len__(self):
        return len(self.files)

    def load(self, i):
        if self.cache is not None and i in self.cache:
            return self.cache[i]
        name = self.files[i]
        with rasterio.open(self.img_dir / name) as s:
            x = s.read(masked=True).astype("float32").filled(np.nan)
        x = (x - self.mean) / self.std
        x[~np.isfinite(x)] = 0.0   # no-data → the band mean
        with rasterio.open(self.lab_dir / name) as s:
            y = self.lut[np.clip(s.read(1).astype("int64"), 0, len(self.lut) - 1)]
        item = (x.astype("float32"), y.astype("uint8"))
        if self.cache is not None:
            self.cache[i] = item
        return item


def _band_stats(img_dir, files, nb, max_files=300, seed=0):
    rng = np.random.default_rng(seed)
    pick = [files[i] for i in rng.permutation(len(files))[:max_files]]
    s1, s2, n = np.zeros(nb), np.zeros(nb), np.zeros(nb)
    for name in pick:
        with rasterio.open(img_dir / name) as s:
            x = s.read(masked=True).astype("float64").filled(np.nan).reshape(nb, -1)
        ok = np.isfinite(x)
        s1 += np.where(ok, x, 0).sum(1)
        s2 += np.where(ok, x * x, 0).sum(1)
        n += ok.sum(1)
    mean = s1 / np.maximum(n, 1)
    std = np.sqrt(np.maximum(s2 / np.maximum(n, 1) - mean ** 2, 0))
    std[std < 1e-6] = 1.0
    return mean.astype("float32"), std.astype("float32")


# ------------------------------------------------------------------ models
def _mps_safe_pools(model):
    """Apple GPUs (MPS) can't pool adaptively to a size that doesn't divide the input (PSPNet's 1/2/3/6 bins on a 16 × 16
    feature map, i.e. 128 px patches): such pools fall back to the CPU for that one step. No weights are involved, so
    saved models are unchanged."""
    import torch.nn as nn
    import torch.nn.functional as F

    class MpsSafeAdaptiveAvgPool2d(nn.AdaptiveAvgPool2d):
        def forward(self, x):
            if x.device.type == "mps":
                o = self.output_size if isinstance(self.output_size, (tuple, list)) else (self.output_size,) * 2
                if any(s and d % s for d, s in zip(x.shape[-2:], o)):
                    return F.adaptive_avg_pool2d(x.cpu(), self.output_size).to(x.device)
            return super().forward(x)

    for mod in list(model.modules()):
        for name, child in list(mod.named_children()):
            if type(child) is nn.AdaptiveAvgPool2d and child.output_size not in (1, (1, 1)):
                setattr(mod, name, MpsSafeAdaptiveAvgPool2d(child.output_size))
    return model


def build_model(arch: str, encoder: str, in_ch: int, n_classes: int, pretrained: bool):
    """The segmentation network. Returns (model, encoder module or None, note about weights)."""
    m, enc, note = _build_model(arch, encoder, in_ch, n_classes, pretrained)
    return _mps_safe_pools(m), enc, note


def _build_model(arch: str, encoder: str, in_ch: int, n_classes: int, pretrained: bool):
    import torch.nn as nn
    a = ARCHS[arch]
    note = ""
    if a["lib"] == "light":
        from . import lightseg
        return lightseg.build_model(arch.removeprefix("light_"), in_ch, n_classes), None, "Light model: trained from scratch (no pretrained weights)."
    if a["lib"] == "smp":
        import segmentation_models_pytorch as smp
        if encoder not in SMP_ENCODERS:
            raise ValueError(f"{ENCODERS.get(encoder, encoder)} can't be used with {a['title']}")
        cls = getattr(smp, a["cls"])
        try:
            m = cls(encoder_name=encoder, encoder_weights="imagenet" if pretrained else None, in_channels=in_ch, classes=n_classes)
        except Exception as e:
            if not pretrained:
                raise
            log.warning("Pretrained %s weights couldn't be loaded (%s); starting from random weights", encoder, e)
            note = f"Pretrained weights couldn't be downloaded ({type(e).__name__}); trained from random weights."
            m = cls(encoder_name=encoder, encoder_weights=None, in_channels=in_ch, classes=n_classes)
        return m, m.encoder, note
    if a["lib"] == "yolo":
        return _yolo_sem(encoder, in_ch, n_classes, pretrained)
    import torchvision.models.segmentation as tvs
    allowed = a["encoders"]
    if encoder not in allowed:
        raise ValueError(f"{a['title']} works with {', '.join(ENCODERS[e] for e in allowed)}")
    kw = {"weights": None, "num_classes": n_classes, "aux_loss": None} if arch == "fcn" else {"weights": None, "num_classes": n_classes}
    builder = {"fcn:resnet50": tvs.fcn_resnet50, "fcn:resnet101": tvs.fcn_resnet101,
               "lraspp:mobilenetv3_large": tvs.lraspp_mobilenet_v3_large}[f"{arch}:{encoder}"]
    try:
        m = builder(weights_backbone="DEFAULT" if pretrained else None, **kw)
    except Exception as e:
        if not pretrained:
            raise
        log.warning("Pretrained backbone weights couldn't be loaded (%s); starting from random weights", e)
        note = f"Pretrained weights couldn't be downloaded ({type(e).__name__}); trained from random weights."
        m = builder(weights_backbone=None, **kw)
    # adapt the first convolution to the number of bands (RGB weights averaged and repeated)
    first = m.backbone.conv1 if arch == "fcn" else m.backbone["0"][0]
    if in_ch != first.in_channels:
        import torch
        new = nn.Conv2d(in_ch, first.out_channels, first.kernel_size, first.stride, first.padding, bias=first.bias is not None)
        with torch.no_grad():
            w = first.weight.mean(1, keepdim=True).repeat(1, in_ch, 1, 1) * (3 / in_ch)
            new.weight.copy_(w)
        if arch == "fcn":
            m.backbone.conv1 = new
        else:
            m.backbone["0"][0] = new
    return _TvWrap(m), m.backbone, note


def _yolo_sem(encoder: str, in_ch: int, n_classes: int, pretrained: bool):
    """YOLO26 semantic segmentation (ultralytics) for any band count; pretrained weights are adapted to the bands."""
    import torch.nn as nn

    from .detect import ultra_weights, ultralytics
    ultralytics()
    from ultralytics.nn.tasks import SemanticSegmentationModel

    if encoder not in ARCHS["yolo_sem"]["encoders"]:
        raise ValueError(f"{ENCODERS.get(encoder, encoder)} can't be used with YOLO26 semantic")
    size = encoder.split("-")[1]
    m = SemanticSegmentationModel(f"yolo26{size}-sem.yaml", ch=in_ch, nc=n_classes, verbose=False)
    note = ""
    if pretrained:
        try:
            src = ultralytics().YOLO(ultra_weights(f"yolo26{size}-sem.pt")).model.float().state_dict()
            dst = m.state_dict()
            k0 = "model.0.conv.weight"
            if k0 in src and src[k0].shape[1] != in_ch:   # first convolution: RGB weights averaged and repeated for every band
                src[k0] = src[k0].mean(1, keepdim=True).repeat(1, in_ch, 1, 1) * (3 / in_ch)
            ok = {k: v for k, v in src.items() if k in dst and v.shape == dst[k].shape}
            m.load_state_dict(ok, strict=False)
            log.info("YOLO26 %s semantic: %d of %d weight tensors pretrained (the class layer is new)", size, len(ok), len(dst))
        except Exception as e:
            log.warning("Pretrained YOLO weights couldn't be loaded (%s); starting from random weights", e)
            note = f"Pretrained weights couldn't be downloaded ({type(e).__name__}); trained from random weights."

    class YoloSem(nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, x):
            out = self.net(x)
            return out[0] if isinstance(out, (tuple, list)) else out   # training also returns an auxiliary output

    return YoloSem(m), None, note


def _tv_wrap_cls():
    import torch.nn as nn

    class TvWrap(nn.Module):   # torchvision models return {"out": …}
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            return self.m(x)["out"]
    return TvWrap


def _TvWrap(m):
    return _tv_wrap_cls()(m)


# ------------------------------------------------------------------ losses and metrics
def _make_loss(kind, weights, n_classes):
    import torch
    import torch.nn.functional as F

    ce_w = weights if kind in ("wce", "focal", "ce_dice") else None

    def dice(logits, y):
        p = logits.softmax(1)
        valid = (y != IGNORE)
        y1 = torch.where(valid, y, torch.zeros_like(y))
        oh = F.one_hot(y1.long(), n_classes).permute(0, 3, 1, 2).float() * valid[:, None]
        p = p * valid[:, None]
        inter = (p * oh).sum((0, 2, 3))
        den = p.sum((0, 2, 3)) + oh.sum((0, 2, 3))
        present = oh.sum((0, 2, 3)) > 0
        d = 1 - (2 * inter + 1) / (den + 1)
        return d[present].mean() if present.any() else d.mean() * 0

    def loss(logits, y):
        yl = y.long()
        if kind == "dice":
            return dice(logits, y)
        if kind == "focal":
            ce = F.cross_entropy(logits, yl, weight=ce_w, ignore_index=IGNORE, reduction="none")
            pt = torch.exp(-F.cross_entropy(logits, yl, ignore_index=IGNORE, reduction="none"))
            valid = yl != IGNORE
            f = ((1 - pt) ** 2 * ce)[valid]
            return f.mean() if f.numel() else logits.sum() * 0
        ce = F.cross_entropy(logits, yl, weight=ce_w, ignore_index=IGNORE)
        if not torch.isfinite(ce):   # a batch without labelled pixels
            ce = logits.sum() * 0
        return ce + dice(logits, y) if kind == "ce_dice" else ce
    return loss


def _metrics(cm: np.ndarray) -> dict:
    cm = cm.astype("float64")
    tp = np.diag(cm)
    gt, pr = cm.sum(1), cm.sum(0)
    union = gt + pr - tp
    iou = np.where(union > 0, tp / np.where(union == 0, 1, union), np.nan)
    prec = np.where(pr > 0, tp / np.where(pr == 0, 1, pr), np.nan)
    rec = np.where(gt > 0, tp / np.where(gt == 0, 1, gt), np.nan)
    f1 = np.where((prec + rec) > 0, 2 * prec * rec / np.where((prec + rec) == 0, 1, prec + rec), np.nan)
    total = cm.sum()
    acc = tp.sum() / total if total else float("nan")
    pe = (gt * pr).sum() / total ** 2 if total else 0
    kappa = (acc - pe) / (1 - pe) if total and pe < 1 else float("nan")
    present = gt > 0
    return {"accuracy": float(acc), "miou": float(np.nanmean(iou[present])) if present.any() else float("nan"),
            "f1_macro": float(np.nanmean(f1[present])) if present.any() else float("nan"), "kappa": float(kappa),
            "iou": iou.tolist(), "precision": prec.tolist(), "recall": rec.tolist(), "f1": f1.tolist(),
            "support": gt.astype(int).tolist(), "confusion": cm.astype(int).tolist()}


def _clean(o):
    """JSON-safe: NaN → None."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        return _clean(o.item())
    return o


# ------------------------------------------------------------------ training
def train(dataset: str | Path, out_dir: str | Path, *, arch: str = "unet", encoder: str = "tu-mobilenetv3_large_100",
          pretrained: bool = True, params: dict | None = None, name: str = "dl_model", resume: str | Path | None = None) -> dict:
    import torch

    t_start = time.time()
    P = {**DEFAULTS, **(params or {})}
    if arch not in ARCHS:
        raise ValueError(f"Unknown architecture {arch}")
    folder = Path(dataset)
    meta = json.loads((folder / "dataset.json").read_text(encoding="utf-8"))
    if not meta.get("labels_dir"):
        raise ValueError("This dataset has no labels (it was made without ground truth), so it can't train a model")
    img_dir, lab_dir = folder / (meta.get("images_dir") or "images"), folder / meta["labels_dir"]
    files = sorted(p.name for p in img_dir.glob("*.tif") if (lab_dir / p.name).is_file())
    if len(files) < 4:
        raise ValueError(f"Only {len(files)} labelled patches: make more (smaller patches, overlap or a bigger area)")
    classes = meta.get("classes") or []
    if not classes:
        raise ValueError("dataset.json lists no classes")
    K = len(classes)
    values = [int(c["value"]) for c in classes]
    lut = np.full(max(values) + 1 if values else 1, IGNORE, "int64")
    for i, v in enumerate(values):
        lut[v] = i
    lut[0] = IGNORE
    nb = int(meta.get("band_count") or len(meta.get("bands") or []))
    patch_px = meta.get("patch_size_px") or [256, 256]

    torch.manual_seed(int(P["seed"]))
    np.random.seed(int(P["seed"]) % 2 ** 32)
    dev = _device(P["device"])
    out = Path(out_dir)
    created_out = not out.exists()
    out.mkdir(parents=True, exist_ok=True)

    # ---- split, normalisation, class weights
    progress.update(0.01, "Splitting patches into training and validation")
    index = _read_index(folder, meta, files)
    tr, va, te, purged = _split(files, index, P["val_share"] / 100, P["test_share"] / 100, P["split"], int(P["seed"]),
                                meta.get("stride_px"), patch_px)
    if len(tr) < 2 or not va:
        raise ValueError(f"Too few patches after the split ({len(tr)} training, {len(va)} validation). "
                         "Lower the validation / test share, use the random split, or make more patches.")
    log.info("Split: %d training, %d validation, %d test patches (%d overlapping training patches dropped)", len(tr), len(va), len(te), purged)
    progress.update(0.02, "Computing band statistics")
    mean, std = _band_stats(img_dir, tr, nb, seed=int(P["seed"]))
    counts = np.zeros(K)
    for fname in tr[:2000]:
        with rasterio.open(lab_dir / fname) as s:
            y = lut[np.clip(s.read(1).astype("int64"), 0, len(lut) - 1)]
        counts += np.bincount(y[y != IGNORE].ravel(), minlength=K)[:K]
    freq = counts / max(counts.sum(), 1)
    cw = None
    if P["loss"] == "wce" or (P["class_weights"] == "auto" and P["loss"] in ("focal", "ce_dice")):
        med = np.median(freq[freq > 0]) if (freq > 0).any() else 1
        w = np.where(freq > 0, med / np.maximum(freq, 1e-12), 0)
        w = np.clip(w, 0.1, 10.0)
        cw = torch.tensor(w / w[freq > 0].mean(), dtype=torch.float32, device=dev)

    est = len(files) * nb * patch_px[0] * patch_px[1] * 4
    cache = est < MAX_CACHE_BYTES
    ds_tr = _Patches(img_dir, lab_dir, tr, mean, std, lut, cache)
    ds_va = _Patches(img_dir, lab_dir, va, mean, std, lut, cache)
    ds_te = _Patches(img_dir, lab_dir, te, mean, std, lut, cache) if te else None

    # ---- model
    progress.update(0.03, f"Building {ARCHS[arch]['title']} ({ENCODERS.get(encoder, encoder)})"
                          + (" · the first time, pretrained weights are downloaded" if pretrained and not resume else ""))
    model, enc, note = build_model(arch, encoder, nb, K, pretrained and not resume)
    model.to(dev)
    opt_kind = P["optimizer"]
    lr, wd = float(P["lr"]), float(P["weight_decay"])
    params_ = [p for p in model.parameters()]
    if opt_kind == "sgd":
        opt = torch.optim.SGD(params_, lr=lr, momentum=0.9, nesterov=True, weight_decay=wd)
    elif opt_kind == "adam":
        opt = torch.optim.Adam(params_, lr=lr, weight_decay=wd)
    else:
        opt = torch.optim.AdamW(params_, lr=lr, weight_decay=wd)
    epochs = int(P["epochs"])
    bs = int(P["batch_size"])
    steps = max(1, math.ceil(len(tr) / bs))
    sched_kind = P["scheduler"]
    if sched_kind == "cosine":
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs, eta_min=lr * 0.01)
    elif sched_kind == "onecycle":
        sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=epochs * steps + 10)
    elif sched_kind == "plateau":
        sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode="max" if P["monitor"] == "val_miou" else "min", factor=0.5,
                                                           patience=max(2, int(P["patience"]) // 3))
    else:
        sched = None
    use_amp = dev.type == "cuda" and P["amp"] != "off" or (P["amp"] == "on" and dev.type in ("cuda", "mps"))
    scaler = torch.amp.GradScaler("cuda") if use_amp and dev.type == "cuda" else None
    loss_fn = _make_loss(P["loss"], cw, K)

    history, start_epoch = [], 0
    best = {"score": None, "epoch": None}
    if resume:
        ck = torch.load(resume, map_location=dev, weights_only=False)
        model.load_state_dict(ck["model"])
        if ck.get("optimizer"):
            try:
                opt.load_state_dict(ck["optimizer"])
            except ValueError:
                log.warning("Optimiser state didn't match; continuing with a fresh optimiser")
        history = ck.get("history") or []
        start_epoch = int(ck.get("epoch", 0))
        best = ck.get("best") or best
        log.info("Resuming from epoch %d", start_epoch)

    config = {
        "format": "lulc-fetch-dl/1", "name": name, "arch": arch, "arch_title": ARCHS[arch]["title"], "encoder": encoder,
        "encoder_title": ENCODERS.get(encoder, encoder), "pretrained": bool(pretrained), "in_channels": nb, "num_classes": K,
        "bands": meta.get("bands") or [], "mean": mean.tolist(), "std": std.tolist(), "dtype": meta.get("dtype"),
        "classes": [{"index": i, "value": c["value"], "original": c.get("original"), "name": c.get("name"), "color": c.get("color")} for i, c in enumerate(classes)],
        "patch_size_px": patch_px, "pixel_size": meta.get("pixel_size"), "crs": meta.get("crs"),
        "dataset": {"folder": str(folder), "name": meta.get("name"), "patches": len(files), "train": len(tr), "val": len(va), "test": len(te),
                    "purged": purged, "split": P["split"], "class_pixels_train": counts.astype(int).tolist()},
        "params": P, "class_weights": None if cw is None else [round(float(v), 3) for v in cw.cpu().numpy()], "device": str(dev),
        "weights_note": note,
    }

    def run_epoch(ds, train_mode, bs_now):
        model.train(train_mode)
        order = np.random.permutation(len(ds)) if train_mode else np.arange(len(ds))
        tot, nbat = 0.0, 0
        cm = torch.zeros(K, K, dtype=torch.int64, device=dev)
        for s in range(0, len(order), bs_now):
            idx = order[s:s + bs_now]
            xs, ys = zip(*(ds.load(int(i)) for i in idx))
            x = torch.from_numpy(np.stack(xs)).to(dev)
            y = torch.from_numpy(np.stack(ys)).to(dev)
            if train_mode:
                x, y = _augment(x, y, P["augment"])
            with torch.set_grad_enabled(train_mode), torch.autocast(dev.type, dtype=torch.float16, enabled=use_amp):
                logits = model(x)
                if logits.shape[-2:] != y.shape[-2:]:
                    logits = torch.nn.functional.interpolate(logits, size=y.shape[-2:], mode="bilinear", align_corners=False)
                loss = loss_fn(logits.float(), y)
            if train_mode:
                opt.zero_grad(set_to_none=True)
                if scaler:
                    scaler.scale(loss).backward()
                    scaler.step(opt)
                    scaler.update()
                else:
                    loss.backward()
                    opt.step()
                if sched_kind == "onecycle":
                    try:
                        sched.step()
                    except ValueError:   # more steps than planned (batch size was lowered): keep the last rate
                        pass
            tot += float(loss.detach())
            nbat += 1
            if True:   # confusion matrix for train and validation scores
                with torch.no_grad():
                    pred = logits.argmax(1)
                    v = y != IGNORE
                    cm += torch.bincount(y[v].long() * K + pred[v], minlength=K * K).reshape(K, K)
            if train_mode:
                progress.update(None, f"Epoch {ep + 1}/{epochs} · batch {s // bs_now + 1}/{math.ceil(len(order) / bs_now)}")
        return tot / max(nbat, 1), cm.cpu().numpy()

    def set_frozen(frozen):
        if enc is None:
            return
        for p in enc.parameters():
            p.requires_grad = not frozen

    stopped, stop_reason, since_best = False, "", 0
    if history and best.get("epoch") is not None:
        since_best = len(history) - best["epoch"]
    t_epochs = time.time()
    ep = start_epoch
    try:
        for ep in range(start_epoch, epochs):
            set_frozen(ep < int(P["freeze_epochs"]) and pretrained)
            te0 = time.time()
            while True:   # retry with a smaller batch if memory runs out
                try:
                    tr_loss, tr_cm = run_epoch(ds_tr, True, bs)
                    break
                except RuntimeError as e:
                    if "out of memory" not in str(e).lower() or bs <= 1:
                        raise
                    bs = max(1, bs // 2)
                    log.warning("Out of memory: batch size lowered to %d", bs)
                    if dev.type == "cuda":
                        torch.cuda.empty_cache()
                    elif dev.type == "mps":
                        torch.mps.empty_cache()
            with torch.no_grad():
                va_loss, va_cm = run_epoch(ds_va, False, max(bs, 4))
            mtr, mva = _metrics(tr_cm), _metrics(va_cm)
            row = {"epoch": ep + 1, "train_loss": tr_loss, "val_loss": va_loss, "train_miou": mtr["miou"], "val_miou": mva["miou"],
                   "train_acc": mtr["accuracy"], "val_acc": mva["accuracy"], "val_f1": mva["f1_macro"],
                   "lr": opt.param_groups[0]["lr"], "seconds": round(time.time() - te0, 1), "batch_size": bs}
            history.append(row)
            score = row[P["monitor"]]
            md = float(P["min_delta"])
            if best["score"] is None:
                better = True
            elif P["monitor"] == "val_miou":
                better = score > best["score"] + md
            else:
                better = score < best["score"] - md
            if better and score is not None and math.isfinite(score):
                best = {"score": score, "epoch": ep + 1, "val": _clean(mva)}
                since_best = 0
                torch.save({"model": model.state_dict(), "config": config}, out / "best_model.pt")
            else:
                since_best += 1
            if sched_kind == "cosine":
                sched.step()
            elif sched_kind == "plateau":
                sched.step(score)
            torch.save({"model": model.state_dict(), "optimizer": opt.state_dict(), "epoch": ep + 1, "history": history,
                        "best": best, "config": config}, out / "last_model.pt")
            _write_log(out / "training_log.csv", history)
            done = ep + 1 - start_epoch
            eta = (time.time() - t_epochs) / done * (epochs - ep - 1)
            log.info("Epoch %d/%d · loss %.4f / %.4f · mIoU %.3f / %.3f · %.0f s", ep + 1, epochs, tr_loss, va_loss,
                     mtr["miou"], mva["miou"], row["seconds"])
            progress.live(_clean({"history": history, "best_epoch": best["epoch"], "monitor": P["monitor"], "epochs": epochs,
                                  "eta_s": round(eta), "batch_size": bs, "device": str(dev)}))
            progress.update((ep + 1) / epochs * 0.96, f"Epoch {ep + 1}/{epochs} · val mIoU {mva['miou']:.3f}")
            if P["early_stop"] and since_best >= int(P["patience"]):
                stopped, stop_reason = True, f"early stopping: no improvement in {P['patience']} epochs"
                log.info("Early stopping at epoch %d (best epoch %s)", ep + 1, best["epoch"])
                break
    except progress.Cancelled:
        stopped, stop_reason = True, "stopped by the user"
        log.info("Training stopped by the user after %d epochs", len(history))
        if not (out / "best_model.pt").exists():
            if created_out:
                import shutil
                shutil.rmtree(out, ignore_errors=True)
            raise

    # ---- final evaluation with the best weights (no progress calls: the user may have cancelled)
    ck = torch.load(out / "best_model.pt", map_location=dev, weights_only=False)
    model.load_state_dict(ck["model"])
    with torch.no_grad():
        _, va_cm = run_epoch_eval(model, ds_va, dev, K, max(bs, 4), use_amp)
        te_m = None
        if ds_te:
            _, te_cm = run_epoch_eval(model, ds_te, dev, K, max(bs, 4), use_amp)
            te_m = _metrics(te_cm)
        examples = _examples(model, ds_te or ds_va, dev, config, use_amp)
    va_m = _metrics(va_cm)
    config.update({
        "trained": time.strftime("%Y-%m-%d %H:%M:%S"), "epochs_run": len(history), "best_epoch": best["epoch"],
        "stopped": stop_reason or None, "seconds": round(max(time.time() - t_start, sum(h.get("seconds", 0) for h in history)), 1), "final_batch_size": bs,
        "val": _clean(va_m), "test": _clean(te_m) if te_m else None,
    })
    torch.save({"model": model.state_dict(), "config": config}, out / "best_model.pt")
    (out / "model_config.json").write_text(json.dumps(_clean(config), indent=1), encoding="utf-8")
    _write_log(out / "training_log.csv", history)
    from .dlreport import write_report
    write_report(out / "report.html", config, history, examples)
    log.info("Model saved to %s (best epoch %s, val mIoU %.3f)", out, best["epoch"], va_m["miou"])
    return _clean({"folder": str(out), "config": config, "history": history, "report": str(out / "report.html")})


def run_epoch_eval(model, ds, dev, K, bs, use_amp):
    import torch
    model.eval()
    cm = torch.zeros(K, K, dtype=torch.int64, device=dev)
    for s in range(0, len(ds), bs):
        xs, ys = zip(*(ds.load(i) for i in range(s, min(len(ds), s + bs))))
        x = torch.from_numpy(np.stack(xs)).to(dev)
        y = torch.from_numpy(np.stack(ys)).to(dev)
        with torch.autocast(dev.type, dtype=torch.float16, enabled=use_amp):
            logits = model(x)
        if logits.shape[-2:] != y.shape[-2:]:
            logits = torch.nn.functional.interpolate(logits.float(), size=y.shape[-2:], mode="bilinear", align_corners=False)
        pred = logits.argmax(1)
        v = y != IGNORE
        cm += torch.bincount(y[v].long() * K + pred[v], minlength=K * K).reshape(K, K)
    return 0.0, cm.cpu().numpy()


def _augment(x, y, kinds):
    import torch
    if not kinds:
        return x, y
    if "flip" in kinds:
        m = torch.rand(x.shape[0], device=x.device) < 0.5
        x[m], y[m] = x[m].flip(-1), y[m].flip(-1)
        m = torch.rand(x.shape[0], device=x.device) < 0.5
        x[m], y[m] = x[m].flip(-2), y[m].flip(-2)
    if "rot90" in kinds and x.shape[-1] == x.shape[-2]:
        k = int(torch.randint(0, 4, (1,)))
        if k:
            x, y = torch.rot90(x, k, (-2, -1)), torch.rot90(y, k, (-2, -1))
    if "bright" in kinds:   # normalised data: shift by ±0.1 std per patch
        x = x + (torch.rand(x.shape[0], 1, 1, 1, device=x.device) - 0.5) * 0.2
    if "noise" in kinds:
        x = x + torch.randn_like(x) * 0.03
    return x.contiguous(), y.contiguous()


def _write_log(path, history):
    if not history:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(history[0].keys()))
        w.writeheader()
        w.writerows(history)


def _rgb_bands(bands: list[str]) -> list[int]:
    names = [str(b).upper() for b in bands]
    for trio in (("B04", "B03", "B02"), ("RED", "GREEN", "BLUE"), ("VV", "VH", "VV")):
        if all(t in names for t in trio):
            return [names.index(t) for t in trio]
    return [0, min(1, len(bands) - 1), min(2, len(bands) - 1)]


def _examples(model, ds, dev, config, use_amp, n=6):
    """A few patches as (image RGB, label, prediction) uint8 arrays for the report."""
    import torch
    if not len(ds):
        return []
    # prefer patches with many classes
    scores = []
    for i in range(min(len(ds), 200)):
        _, y = ds.load(i)
        scores.append((len(np.unique(y[y != IGNORE])), i))
    pick = [i for _, i in sorted(scores, reverse=True)[:n]]
    rgb_idx = _rgb_bands(config["bands"])
    colors = np.array([_hex(c.get("color")) for c in config["classes"]] + [(0, 0, 0)], "uint8")
    out = []
    model.eval()
    for i in pick:
        x, y = ds.load(i)
        with torch.no_grad(), torch.autocast(dev.type, dtype=torch.float16, enabled=use_amp):
            logits = model(torch.from_numpy(x[None]).to(dev))
        if logits.shape[-2:] != y.shape:
            logits = torch.nn.functional.interpolate(logits.float(), size=y.shape, mode="bilinear", align_corners=False)
        pred = logits.argmax(1)[0].cpu().numpy()
        rgb = np.stack([x[b] for b in rgb_idx])
        lo, hi = np.percentile(rgb, [2, 98], axis=(1, 2), keepdims=True)
        rgb = (np.clip((rgb - lo) / np.maximum(hi - lo, 1e-6), 0, 1) * 255).astype("uint8")
        yl = np.where(y == IGNORE, len(colors) - 1, y)
        ok = (y != IGNORE)
        acc = float((pred[ok] == y[ok]).mean()) if ok.any() else None
        out.append({"file": ds.files[i], "rgb": rgb, "label": np.moveaxis(colors[yl], -1, 0), "pred": np.moveaxis(colors[pred], -1, 0), "acc": acc})
    return out


def _hex(c):
    c = (c or "#888888").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (136, 136, 136)


# ------------------------------------------------------------------ using a trained model
def load_config(model_dir: str | Path) -> dict:
    d = Path(model_dir)
    p = d / "model_config.json" if d.is_dir() else d.with_name("model_config.json")
    if not p.is_file():
        raise ValueError(f"{d} isn't a LULC Fetch deep-learning model folder (no model_config.json)")
    return json.loads(p.read_text(encoding="utf-8"))


def predict(model_dir: str | Path, inputs: list[dict], out_path: str | Path, *, clip: dict | None = None, overlap: float = 0.25,
            batch_size: int = 8, device: str = "auto", confidence: bool = True, tile: int | None = None) -> dict:
    """Classify a whole image with sliding windows; overlapping predictions are blended so no seams show."""
    import torch
    from rasterio.windows import Window

    from .analysis import clip_region
    from .patches import open_inputs, read_stack

    t0 = time.time()
    d = Path(model_dir)
    cfg = load_config(d)
    dev = _device(device)
    model, _, _ = build_model(cfg["arch"], cfg["encoder"], cfg["in_channels"], cfg["num_classes"], False)
    ck = torch.load(d / "best_model.pt", map_location=dev, weights_only=False)
    model.load_state_dict(ck["model"])
    model.to(dev).eval()
    K = cfg["num_classes"]
    mean = np.array(cfg["mean"], "float32")[:, None, None]
    std = np.array(cfg["std"], "float32")[:, None, None]
    T = int(tile or max(cfg["patch_size_px"]))
    T = max(32, int(math.ceil(T / 32) * 32))
    ov = int(round(T * min(max(overlap, 0), 0.75)))
    S = T - ov
    values = np.array([int(c["value"]) for c in cfg["classes"]], "int64")
    out_dtype = "uint8" if values.max() < 255 else "uint16"

    ref = rasterio.open(inputs[0]["path"])
    plan_in, band_names, srcs, vrts = open_inputs(inputs, ref)
    try:
        nb = sum(len(p["bands"]) for p in plan_in)
        if nb != cfg["in_channels"]:
            raise ValueError(f"The model needs {cfg['in_channels']} bands ({', '.join(cfg['bands'][:20])}"
                             f"{'…' if len(cfg['bands']) > 20 else ''}), but the selected input has {nb}")
        win, geom = clip_region(ref, clip)
        win = win or Window(0, 0, ref.width, ref.height)
        c0, r0, W, H = int(win.col_off), int(win.row_off), int(win.width), int(win.height)
        transform = ref.window_transform(Window(c0, r0, W, H))
        mask_geom = None
        if geom is not None:
            from rasterio.features import geometry_mask
            mask_geom = geometry_mask([geom], out_shape=(H, W), transform=transform, invert=True)
        # blending weights: highest in the tile centre, never zero
        ramp = np.minimum(np.arange(T) + 1, np.arange(T)[::-1] + 1).astype("float32")
        ramp = np.minimum(ramp / max(ov, 1), 1.0) if ov else np.ones(T, "float32")
        wt = np.maximum(np.outer(ramp, ramp), 0.02).astype("float32")
        cmap = {0: (0, 0, 0, 0)}
        for c in cfg["classes"]:
            if int(c["value"]) < 256:
                cmap[int(c["value"])] = (*_hex(c.get("color")), 255)
        prof = {"driver": "GTiff", "width": W, "height": H, "count": 2 if confidence else 1, "dtype": out_dtype, "crs": ref.crs,
                "transform": transform, "nodata": 0, "compress": "deflate", "tiled": True, "blockxsize": 256, "blockysize": 256,
                "BIGTIFF": "IF_SAFER"}
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ys_ = list(range(0, max(H - ov, 1), S))
        xs_ = list(range(0, max(W - ov, 1), S))
        acc = np.zeros((K, T + S, W), "float32")
        wsum = np.zeros((T + S, W), "float32")
        use_amp = dev.type == "cuda"
        written = 0
        counts = np.zeros(K, "int64")
        with rasterio.open(out_path, "w", **prof) as dst:
            dst.set_band_description(1, "class")
            dst.update_tags(classes=json.dumps({str(int(c["value"])): c.get("name") or str(c["value"]) for c in cfg["classes"]}),
                            model=cfg.get("name", ""), model_arch=f'{cfg.get("arch_title")} · {cfg.get("encoder_title")}')
            if confidence:
                dst.set_band_description(2, "confidence_pct")
            for ri, y in enumerate(ys_):
                progress.update(ri / len(ys_) * 0.98, f"Tile row {ri + 1} of {len(ys_)}")
                tiles, valids = [], []
                for x in xs_:
                    a, v = read_stack(plan_in, Window(c0 + x, r0 + y, T, T), (T, T))
                    a = (a - mean) / std
                    a[~np.isfinite(a)] = 0.0
                    tiles.append(a)
                    valids.append(v)
                probs = []
                for b in range(0, len(tiles), batch_size):
                    xb = torch.from_numpy(np.stack(tiles[b:b + batch_size])).to(dev)
                    with torch.no_grad(), torch.autocast(dev.type, dtype=torch.float16, enabled=use_amp):
                        lg = model(xb)
                    if lg.shape[-2:] != (T, T):
                        lg = torch.nn.functional.interpolate(lg.float(), size=(T, T), mode="bilinear", align_corners=False)
                    probs.append(lg.float().softmax(1).cpu().numpy())
                probs = np.concatenate(probs) if probs else np.zeros((0, K, T, T), "float32")
                for x, p, v in zip(xs_, probs, valids):
                    w_ = min(T, W - x)
                    ww = wt[:, :w_] * v[:, :w_]
                    acc[:, :T, x:x + w_] += p[:, :, :w_] * ww
                    wsum[:T, x:x + w_] += ww
                last = ri == len(ys_) - 1
                n_final = min(H - y, T) if last else S
                n_final = max(0, min(n_final, H - y))
                if n_final:
                    a_ = acc[:, :n_final]
                    w_ = wsum[:n_final]
                    ok = w_ > 0
                    cls = a_.argmax(0)
                    pmax = a_.max(0) / np.where(ok, w_, 1)
                    lab = np.where(ok, values[cls], 0).astype(out_dtype)
                    if mask_geom is not None:
                        lab[~mask_geom[y:y + n_final]] = 0
                    dst.write(lab, 1, window=Window(0, y, W, n_final))
                    if confidence:
                        conf = np.where(lab > 0, np.clip(pmax * 100, 1, 100), 0).astype(out_dtype)
                        dst.write(conf, 2, window=Window(0, y, W, n_final))
                    counts += np.bincount(cls[lab > 0].ravel(), minlength=K)[:K]
                    written += n_final
                # shift the buffers up by one tile step
                acc[:, :T] = acc[:, S:S + T]
                acc[:, T:] = 0
                wsum[:T] = wsum[S:S + T]
                wsum[T:] = 0
            if out_dtype == "uint8":
                dst.write_colormap(1, cmap)
        total = counts.sum()
        return _clean({"path": str(out_path), "width": W, "height": H, "seconds": round(time.time() - t0, 1), "tile": T, "overlap": ov,
                       "device": str(dev), "classes": [{"value": int(c["value"]), "name": c.get("name"), "color": c.get("color"),
                                                         "pct": 100 * float(counts[i]) / total if total else 0} for i, c in enumerate(cfg["classes"])]})
    finally:
        for v in vrts:
            v.close()
        for s in srcs:
            s.close()
        ref.close()
