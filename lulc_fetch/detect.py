"""Object detection with open-source pretrained models, or models trained with "Train detection model".

Model families (all optional add-ons, imported only inside detect(), which runs in a child process: see lulc_fetch.dlrunner):

* torchvision (PyTorch add-on): Faster R-CNN, RetinaNet, FCOS, SSD, SSDlite, Mask R-CNN, pretrained on COCO (80 classes)
* YOLO26 (YOLO & SAM add-on, ultralytics): boxes or outlines pretrained on COCO, and oriented boxes pretrained on DOTA
  (15 aerial classes: planes, ships, vehicles, storage tanks, sports fields, bridges, harbours, …)
* SAM 2.1 (Segment Anything, Meta): outlines every object in the image, without classes; or turns any model's boxes
  into outlines (box prompts)
* your own YOLO models from "Train detection model"

Works on any image: the chosen three bands are stretched to 0–1 and fed to the model as RGB, tile by tile (with
overlap), and detections are merged across tiles. The result is a GeoJSON layer with the class, score and size of
every object. COCO models were trained on everyday photos, so they need high-resolution images (about 0.1–1 m pixels).
Weights are downloaded once, on first use, into the PyTorch cache.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path

import numpy as np
import rasterio

from . import progress

log = logging.getLogger(__name__)

_COCO_DESC = "Pretrained on COCO (80 everyday classes): cars, trucks, buses, boats, planes, people, animals…"
MODELS = {
    # ---- torchvision (PyTorch add-on)
    "fasterrcnn_v2": {"title": "Faster R-CNN v2", "family": "tv", "backbone": "ResNet-50 FPN", "fn": "fasterrcnn_resnet50_fpn_v2", "size": 800, "mb": 167,
                      "accuracy": 5, "speed": 2, "classes": "coco", "desc": "The most accurate torchvision box detector (COCO box mAP 46.7)."},
    "fasterrcnn": {"title": "Faster R-CNN", "family": "tv", "backbone": "ResNet-50 FPN", "fn": "fasterrcnn_resnet50_fpn", "size": 800, "mb": 160,
                   "accuracy": 4, "speed": 3, "classes": "coco", "desc": "The classic two-stage detector (mAP 37.0)."},
    "fasterrcnn_mobile": {"title": "Faster R-CNN Mobile", "family": "tv", "backbone": "MobileNetV3 FPN", "fn": "fasterrcnn_mobilenet_v3_large_fpn", "size": 800,
                          "mb": 74, "accuracy": 3, "speed": 4, "classes": "coco", "desc": "Light Faster R-CNN (mAP 32.8): several times faster on a CPU."},
    "retinanet_v2": {"title": "RetinaNet v2", "family": "tv", "backbone": "ResNet-50 FPN", "fn": "retinanet_resnet50_fpn_v2", "size": 800, "mb": 146,
                     "accuracy": 4, "speed": 3, "classes": "coco", "desc": "One-stage detector with focal loss (mAP 41.5): good with many small objects."},
    "fcos": {"title": "FCOS", "family": "tv", "backbone": "ResNet-50 FPN", "fn": "fcos_resnet50_fpn", "size": 800, "mb": 124,
             "accuracy": 4, "speed": 3, "classes": "coco", "desc": "Anchor-free one-stage detector (mAP 39.2)."},
    "ssd": {"title": "SSD300", "family": "tv", "backbone": "VGG16", "fn": "ssd300_vgg16", "size": 300, "mb": 136,
            "accuracy": 2, "speed": 4, "classes": "coco", "desc": "Single-shot detector on 300 px tiles (mAP 25.1): fast, misses small objects."},
    "ssdlite": {"title": "SSDlite", "family": "tv", "backbone": "MobileNetV3", "fn": "ssdlite320_mobilenet_v3_large", "size": 320, "mb": 13,
                "accuracy": 1, "speed": 5, "classes": "coco", "desc": "Tiny mobile detector (mAP 21.3, 13 MB): fastest, for a quick look."},
    "maskrcnn_v2": {"title": "Mask R-CNN v2", "family": "tv", "backbone": "ResNet-50 FPN", "fn": "maskrcnn_resnet50_fpn_v2", "size": 800, "mb": 177,
                    "accuracy": 5, "speed": 2, "outlines": True, "classes": "coco",
                    "desc": "Finds objects and draws their outline (instance segmentation, mask mAP 41.8) instead of a box."},
    # ---- ultralytics (YOLO & SAM add-on)
    "yolo_detect": {"title": "YOLO26", "family": "yolo", "task": "detect", "weights": "yolo26{s}.pt", "size": 640, "sizes": "nsmlx", "default_size": "m",
                    "accuracy": 5, "speed": 5, "classes": "coco", "desc": "The newest YOLO (ultralytics): fast and accurate boxes. " + _COCO_DESC},
    "yolo_segment": {"title": "YOLO26 outlines", "family": "yolo", "task": "segment", "weights": "yolo26{s}-seg.pt", "size": 640, "sizes": "nsmlx",
                     "default_size": "m", "outlines": True, "accuracy": 5, "speed": 4, "classes": "coco",
                     "desc": "YOLO26 instance segmentation: an outline for every object. " + _COCO_DESC},
    "yolo_obb": {"title": "YOLO26 aerial (DOTA)", "family": "yolo", "task": "obb", "weights": "yolo26{s}-obb.pt", "size": 1024, "sizes": "nsmlx",
                 "default_size": "m", "outlines": True, "accuracy": 5, "speed": 4, "classes": "dota", "aerial": True,
                 "desc": "Oriented (rotated) boxes, pretrained on DOTA aerial images: planes, ships, small and large vehicles, storage tanks, "
                         "harbours, bridges, roundabouts, sports fields, swimming pools, helicopters. The best start for satellite and aerial images."},
    "sam_all": {"title": "SAM 2.1: segment everything", "family": "sam", "weights": "sam2.1_{s}.pt", "size": 1024, "sizes": "tsbl", "default_size": "t",
                "outlines": True, "accuracy": 4, "speed": 1, "classes": None,
                "desc": "Segment Anything (Meta): outlines every distinct object or region (buildings, fields, trees, ponds…) without naming it. "
                        "Slow: several seconds per tile."},
}
SIZE_TITLES = {"n": "nano (fastest)", "s": "small", "m": "medium", "l": "large", "x": "extra large (most accurate)",
               "t": "tiny (fastest)", "b": "base"}
SAM_SIZES = {"t": "SAM 2.1 tiny (fast)", "s": "SAM 2.1 small", "b": "SAM 2.1 base", "l": "SAM 2.1 large (best outlines, slow)"}

# COCO category names in torchvision's label order (index = label id; "N/A" ids are unused). YOLO uses the same names.
COCO = ["__background__", "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
        "fire hydrant", "N/A", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant",
        "bear", "zebra", "giraffe", "N/A", "backpack", "umbrella", "N/A", "N/A", "handbag", "tie", "suitcase", "frisbee", "skis",
        "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
        "N/A", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
        "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant", "bed", "N/A", "dining table", "N/A", "N/A", "toilet",
        "N/A", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
        "N/A", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"]
CLASSES = [c for c in COCO if c not in ("__background__", "N/A")]
DOTA = ["plane", "ship", "storage tank", "baseball diamond", "tennis court", "basketball court", "ground track field", "harbor",
        "bridge", "large vehicle", "small vehicle", "helicopter", "roundabout", "soccer ball field", "swimming pool"]
# classes that show up in overhead imagery; offered first in the UI
AERIAL = ["car", "truck", "bus", "boat", "airplane", "train", "person", "motorcycle", "bicycle", "cow", "sheep", "horse",
          "elephant", "giraffe", "zebra", "bird", "sports ball", "tennis racket", "kite", "umbrella", "bench", "potted plant"]
PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#469990", "#9a6324",
           "#800000", "#808000", "#000075", "#ffe119", "#a9a9a9", "#fabed4", "#dcbeff", "#aaffc3", "#ffd8b1", "#fffac8"]
SEGMENT = "segment"   # the class of SAM "segment everything" outlines


def color(name: str) -> str:
    if name == SEGMENT:
        return "#f59e0b"
    for lst in (AERIAL, DOTA):
        if name in lst:
            return PALETTE[lst.index(name) % len(PALETTE)]
    if name in CLASSES:
        return PALETTE[(len(AERIAL) + CLASSES.index(name)) % len(PALETTE)]
    return PALETTE[sum(map(ord, name)) % len(PALETTE)]


def schema() -> dict:
    return {"models": MODELS, "sizes": SIZE_TITLES, "sam_sizes": SAM_SIZES,
            "classes": {"coco": CLASSES, "dota": DOTA}, "aerial": AERIAL,
            "colors": {c: color(c) for c in CLASSES + DOTA}}


def weights_dir() -> Path:
    """Where ultralytics weights are downloaded (next to the torchvision weights, in the PyTorch cache)."""
    import torch
    d = Path(torch.hub.get_dir()) / "checkpoints" / "ultralytics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ultralytics():
    """Import ultralytics quietly, with its usage analytics off."""
    os.environ.setdefault("YOLO_VERBOSE", "False")
    try:
        import ultralytics as ul
    except ImportError as e:
        raise RuntimeError("YOLO and SAM need the YOLO & SAM add-on (ultralytics). Install it from this tool's panel.") from e
    from ultralytics.utils import SETTINGS
    if SETTINGS.get("sync"):
        SETTINGS.update({"sync": False})
    return ul


def ultra_weights(name: str) -> str:
    """Path of an ultralytics release asset (e.g. yolo26m.pt), downloaded on first use."""
    from ultralytics.utils.downloads import attempt_download_asset
    p = weights_dir() / name
    if not p.is_file():
        log.info("Downloading %s (once)…", name)
    return str(attempt_download_asset(str(p)))


def ultra_device(dev) -> str | int:
    return 0 if dev.type == "cuda" else dev.type


# ------------------------------------------------------------------ runners: one batch of tiles → detections per tile
# Each runner has .size (input px), .outlines (bool) and .run(tiles) where tiles are float32 (3, T, T) arrays in 0–1;
# it returns, per tile, (boxes (N, 4) xyxy, scores (N,), names [N], polygons [N] of [(x, y), …] or None).

def _bgr(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray((a.transpose(1, 2, 0)[..., ::-1] * 255).round().astype("uint8"))


class _TorchvisionRunner:
    def __init__(self, key, dev, score):
        import torchvision.models.detection as tvd
        m = MODELS[key]
        log.info("Loading %s (pretrained on COCO; the weights, about %d MB, are downloaded once)", m["title"], m["mb"])
        self.net = getattr(tvd, m["fn"])(weights="DEFAULT").to(dev).eval()
        for obj in (getattr(self.net, "roi_heads", None), self.net):   # dense scenes have more than 100 objects per tile
            if obj is not None and hasattr(obj, "detections_per_img"):
                obj.detections_per_img = 300
        self.dev, self.score, self.size, self.outlines, self.title = dev, score, m["size"], bool(m.get("outlines")), m["title"]

    def run(self, tiles):
        import torch
        imgs = [torch.from_numpy(a).to(self.dev) for a in tiles]
        try:
            with torch.no_grad():
                outs = self.net(imgs)
        except (RuntimeError, NotImplementedError) as e:
            if self.dev.type != "mps":
                raise
            log.warning("The Apple GPU can't run this model (%s); using the CPU", str(e).split("\n")[0][:160])
            self.dev = torch.device("cpu")
            self.net = self.net.to(self.dev)
            with torch.no_grad():
                outs = self.net([i.cpu() for i in imgs])
        res = []
        for o in outs:
            s = o["scores"].float().cpu().numpy()
            ok = s >= self.score
            b = o["boxes"].float().cpu().numpy()[ok]
            names = [COCO[int(i)] for i in o["labels"].cpu().numpy()[ok]]
            polys = None
            if self.outlines:
                ms = o["masks"][torch.from_numpy(ok)].cpu().numpy()[:, 0] > 0.5
                polys = [(_mask_polygon(m, 0, 0) or [None])[0] if m.any() else None for m in ms]
            res.append((b, s[ok], names, polys))
        return res


class _YoloRunner:
    def __init__(self, weights, dev, score, imgsz, title):
        ul = ultralytics()
        self.model = ul.YOLO(weights)
        self.task = self.model.task
        if self.task not in ("detect", "segment", "obb"):
            raise ValueError(f"{Path(weights).name} is a YOLO '{self.task}' model, not an object detector")
        self.names = self.model.names
        self.dev, self.score, self.size, self.title = dev, score, imgsz, title
        self.outlines = self.task in ("segment", "obb")

    def _predict(self, imgs):
        return self.model.predict(imgs, imgsz=self.size, conf=self.score, iou=0.6, max_det=300, device=ultra_device(self.dev), verbose=False)

    def run(self, tiles):
        import torch
        imgs = [_bgr(a) for a in tiles]
        try:
            outs = self._predict(imgs)
        except (RuntimeError, NotImplementedError) as e:
            if self.dev.type != "mps":
                raise
            log.warning("The Apple GPU can't run this model (%s); using the CPU", str(e).split("\n")[0][:160])
            self.dev = torch.device("cpu")
            outs = self._predict(imgs)
        res = []
        for r in outs:
            if self.task == "obb":
                o = r.obb
                b, s, c = o.xyxy.cpu().numpy(), o.conf.cpu().numpy(), o.cls.cpu().numpy().astype(int)
                polys = [[tuple(map(float, p)) for p in q] + [tuple(map(float, q[0]))] for q in o.xyxyxyxy.cpu().numpy()]
            else:
                b, s, c = r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(), r.boxes.cls.cpu().numpy().astype(int)
                polys = None
                if self.task == "segment" and r.masks is not None:
                    polys = [_ring(xy) for xy in r.masks.xy]
            res.append((b, s, [self.names[int(i)] for i in c], polys))
        return res


class _SamAllRunner:
    def __init__(self, weights, dev, score, title):
        ul = ultralytics()
        self.model = ul.SAM(weights)
        self.dev, self.score, self.size, self.outlines, self.title = dev, score, 1024, True, title

    def run(self, tiles):
        res = []
        for a in tiles:
            r = self.model.predict(_bgr(a), device=ultra_device(self.dev), verbose=False)[0]
            if r.masks is None or r.boxes is None:
                res.append((np.zeros((0, 4)), np.zeros(0), [], []))
                continue
            s = r.boxes.conf.cpu().numpy()
            ok = s >= self.score
            polys = [_ring(xy) for xy, k in zip(r.masks.xy, ok) if k]
            res.append((r.boxes.xyxy.cpu().numpy()[ok], s[ok], [SEGMENT] * int(ok.sum()), polys))
        return res


class _SamRefiner:
    """Turns boxes into outlines with SAM box prompts (one image encoding per tile)."""

    def __init__(self, size, dev):
        ul = ultralytics()
        log.info("Outlines with %s", SAM_SIZES[size])
        self.model = ul.SAM(ultra_weights(f"sam2.1_{size}.pt"))
        self.dev = dev

    def refine(self, tile, boxes):
        if not len(boxes):
            return []
        r = self.model.predict(_bgr(tile), bboxes=[list(map(float, b)) for b in boxes], device=ultra_device(self.dev), verbose=False)[0]
        if r.masks is None:
            return [None] * len(boxes)
        return [_ring(xy) for xy in r.masks.xy]


def _ring(xy) -> list | None:
    """A closed ring of (x, y) tuples from an (N, 2) array, or None when it's too small."""
    pts = [tuple(map(float, p)) for p in np.asarray(xy)]
    if len(pts) < 3:
        return None
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _clean_ring(ring) -> list | None:
    """A valid, simplified outline (pixel units): repaired, largest part only, vertices within 0.75 px."""
    from shapely.geometry import Polygon
    try:
        g = Polygon(ring).buffer(0)
    except (ValueError, TypeError):
        return None
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda q: q.area)
    if g.is_empty or g.geom_type != "Polygon":
        return None
    g = g.simplify(0.75, preserve_topology=True)
    return list(g.exterior.coords) if g.area > 0 else None


def load_custom(model_dir: str | Path) -> dict:
    d = Path(model_dir)
    p = d / "model_config.json"
    if not p.is_file():
        raise ValueError(f"{d} isn't a LULC Fetch detection model folder (no model_config.json)")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    if cfg.get("kind") != "detection":
        raise ValueError(f"{d.name} isn't a detection model")
    return cfg


def _runner(model, size, custom_dir, dev, score, zoom_auto_ratio):
    """(runner, title, class colours, stretch mode or None, native tile px)."""
    if model == "custom":
        cfg = load_custom(custom_dir)
        w = Path(custom_dir) / cfg.get("weights", "best.pt")
        title = f'{cfg.get("name", Path(custom_dir).name)} (your model)'
        r = _YoloRunner(str(w), dev, score, int(cfg["tile_px"]), title)
        return r, title, {c["name"]: c.get("color") or color(c["name"]) for c in cfg["classes"]}, cfg.get("stretch"), cfg
    if model not in MODELS:
        raise ValueError(f"Unknown model {model}")
    m = MODELS[model]
    if m["family"] == "tv":
        return _TorchvisionRunner(model, dev, score), m["title"], {}, None, None
    size = size if size and size in m["sizes"] else m["default_size"]
    title = f'{m["title"]} · {SIZE_TITLES.get(size, size)}' if m["family"] == "yolo" else SAM_SIZES[size]
    weights = ultra_weights(m["weights"].format(s=size))
    if m["family"] == "yolo":
        return _YoloRunner(weights, dev, score, m["size"], title), title, {}, None, None
    return _SamAllRunner(weights, dev, score, title), title, {}, None, None


def _stretch_limits(plan_in, win, mode: str, nb: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-band low / high values used to scale the image to 0–1, from a reduced read of the whole area."""
    if mode == "byte":
        return np.zeros(nb, "float32"), np.full(nb, 255.0, "float32")
    f = max(1, int(math.sqrt(win.width * win.height / 4e6)))
    shape = (max(1, int(win.height // f)), max(1, int(win.width // f)))
    lo, hi = [], []
    for p in plan_in:
        a = p["reader"].read(p["bands"], window=win, out_shape=(len(p["bands"]), *shape), masked=True)
        a = a.astype("float32") * p["scale"] + p["offset"]
        for band in a:
            v = band.compressed()
            v = v[np.isfinite(v)]
            if not v.size:
                lo.append(0.0), hi.append(1.0)
                continue
            q = (v.min(), v.max()) if mode == "minmax" else np.percentile(v, (2, 98))
            lo.append(float(q[0])), hi.append(float(q[1]) if q[1] > q[0] else float(q[0]) + 1.0)
    return np.array(lo, "float32"), np.array(hi, "float32")


def _mask_polygon(mask: np.ndarray, x0: int, y0: int):
    """Largest outline of a binary mask (tile pixels, offset x0, y0) as a list of (col, row) rings, or None."""
    from rasterio.features import shapes
    from shapely.geometry import shape as to_shape
    best = None
    for g, v in shapes(mask.astype("uint8"), mask=mask):
        if v:
            s = to_shape(g)
            if best is None or s.area > best.area:
                best = s
    if best is None:
        return None
    best = best.simplify(0.75)
    ext = [(x + x0, y + y0) for x, y in best.exterior.coords]
    return [ext] if len(ext) >= 4 else None


def _merge_cut(keep: list[int], boxes, labels, outlines, tiles_of, cut, masks_on, tol: float) -> list[int]:
    """Join pieces of one large object found in neighbouring tiles into one detection: same class, and either mostly
    overlapping, or lined up with a piece that continues past the tile edge that cut it (within `tol` pixels). Runs
    before NMS, so a cut piece never suppresses the rest.

    `cut[i]` = (left, top, right, bottom): which sides of box i an inner tile edge cut. `keep` is in descending score
    order; the better-scoring piece absorbs the other."""
    if not any(any(cut[i]) for i in keep):
        return keep
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    b = np.array([boxes[i] for i in keep], "float64")
    lab = np.array([labels[i] for i in keep])
    alive = np.ones(len(keep), bool)
    for k in range(len(keep) - 1, -1, -1):   # weakest first, so chains collapse into the best piece
        i = keep[k]
        cl, ct, cr, cb = cut[i]
        if not (cl or ct or cr or cb):
            continue
        w, h = b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]
        gx = np.minimum(b[:, 2], b[k, 2]) - np.maximum(b[:, 0], b[k, 0])   # overlap (< 0: gap) along x
        gy = np.minimum(b[:, 3], b[k, 3]) - np.maximum(b[:, 1], b[k, 1])
        ix, iy = np.clip(gx, 0, None), np.clip(gy, 0, None)
        ios = ix * iy / np.maximum(np.minimum(w * h, w[k] * h[k]), 1e-6)   # intersection over the smaller box
        rx, ry = ix / np.maximum(np.minimum(w, w[k]), 1e-6), iy / np.maximum(np.minimum(h, h[k]), 1e-6)
        row, col = (gx > -tol) & (ry > 0.6), (gy > -tol) & (rx > 0.6)
        match = ((ios > 0.5) | (cl & row & (b[:, 0] < b[k, 0] - tol / 2)) | (cr & row & (b[:, 2] > b[k, 2] + tol / 2))
                 | (ct & col & (b[:, 1] < b[k, 1] - tol / 2)) | (cb & col & (b[:, 3] > b[k, 3] + tol / 2)))
        cand = np.nonzero(alive & (lab == lab[k]) & match & (np.arange(len(keep)) != k))[0]
        cand = [j for j in cand if tiles_of[keep[j]] != tiles_of[i]]
        if not cand:
            continue
        j, k2 = min(cand[0], k), max(cand[0], k)
        bj, bk = b[j].copy(), b[k2]
        b[j] = [min(bj[0], bk[0]), min(bj[1], bk[1]), max(bj[2], bk[2]), max(bj[3], bk[3])]
        boxes[keep[j]] = b[j].tolist()
        # each side of the joined box is cut only if the piece that reaches furthest on that side was
        cj, ck = cut[keep[j]], cut[keep[k2]]
        cut[keep[j]] = tuple((cj[s] if bj[s] == b[j, s] else False) or (ck[s] if bk[s] == b[j, s] else False) for s in range(4))
        if masks_on and outlines[keep[j]] and outlines[keep[k2]]:
            # mask outlines can self-intersect: repair them (buffer 0) before joining
            u = unary_union([Polygon(outlines[keep[j]][0]).buffer(0), Polygon(outlines[keep[k2]][0]).buffer(0)]).buffer(0.5).buffer(-0.5)
            if u.geom_type == "MultiPolygon":
                u = max(u.geoms, key=lambda g: g.area)
            if u.geom_type == "Polygon" and not u.is_empty:
                outlines[keep[j]] = [list(u.exterior.coords)]
        alive[k2] = False
    return [i for k, i in enumerate(keep) if alive[k]]



def detect(inputs: list[dict], out_path: str | Path, *, model: str = "fasterrcnn_v2", size: str | None = None, custom_dir: str | None = None,
           sam_refine: str | None = None, clip: dict | None = None, classes: list[str] | None = None, score: float = 0.4,
           zoom: float | str = 1.0, overlap: float = 0.2, nms_iou: float = 0.5, stretch: str = "percent", batch_size: int = 2,
           device: str = "auto", max_size_m: float | None = None, max_objects: int = 50000) -> dict:
    """Detect objects over a whole image (or an area) and write them to a GeoJSON file (EPSG:4326)."""
    import torch
    from rasterio.warp import transform as warp_xy, transform_geom
    from rasterio.windows import Window
    from shapely.geometry import Point, shape as to_shape
    from torchvision.ops import batched_nms

    from .analysis import clip_region
    from .dl import _device
    from .patches import open_inputs, read_stack

    t0 = time.time()
    dev = _device(device)
    runner, title, colors, cfg_stretch, cfg = _runner(model, size, custom_dir, dev, score, None)
    wanted = set(classes) if classes else None
    refiner = _SamRefiner(sam_refine, dev) if sam_refine and not runner.outlines else None
    outlines_on = runner.outlines or refiner is not None

    # raw stored values: the stretch below sets the brightness (and "byte" means raw 0–255, whatever the layer's scale)
    inputs = [{**i, "scale": 1.0, "offset": 0.0} for i in inputs]
    ref = rasterio.open(inputs[0]["path"])
    plan_in, band_names, srcs, vrts = open_inputs(inputs, ref)
    try:
        nb = sum(len(p["bands"]) for p in plan_in)
        if nb not in (1, 3):
            raise ValueError(f"Choose 3 bands (red, green, blue) or 1 band, not {nb}")
        win, geom = clip_region(ref, clip)
        win = win or Window(0, 0, ref.width, ref.height)
        c0, r0, W, H = int(win.col_off), int(win.row_off), int(win.width), int(win.height)
        transform = ref.window_transform(Window(c0, r0, W, H))
        crs = ref.crs
        metres = bool(crs and crs.is_projected)
        if zoom == "auto":   # your model: show objects at the size they had in the training images
            zoom = 1.0
            if cfg and cfg.get("pixel_size") and metres:
                zoom = abs(transform.a) / float(cfg["pixel_size"])
                log.info("Zoom %.2f× so objects look as big as in the training images (%.2f m → %.2f m pixels)", zoom, abs(transform.a), cfg["pixel_size"])
        zoom = min(max(float(zoom), 0.25), 8.0)
        # tile size in image pixels: the model sees each tile at its own input size, so smaller tiles = more zoom
        T = max(64, int(round(runner.size / zoom)))
        ov = int(round(T * min(max(overlap, 0), 0.5)))
        S = T - ov
        edge = max(4.0, 0.015 * T)   # a box this close to a tile edge counts as cut by it
        lo, hi = _stretch_limits(plan_in, win, stretch, nb)
        lo, hi = lo[:, None, None], hi[:, None, None]
        poly = None
        if geom is not None:
            from shapely.prepared import prep
            poly = prep(to_shape(geom))
        if metres:
            log.info("Pixel size %.2f m; with zoom %.3g× the model sees objects as if pixels were %.2f m", abs(transform.a), zoom,
                     abs(transform.a) / zoom)

        ys_ = list(range(0, max(H - ov, 1), S))
        xs_ = list(range(0, max(W - ov, 1), S))
        tiles_total = len(ys_) * len(xs_)
        boxes, scores, labels, outlines, tiles_of, cut = [], [], [], [], [], []
        label_ids: dict[str, int] = {}
        done, skipped = 0, 0
        pending = []

        def run_batch(batch):
            for (x, y, a, valid), (b, s, names, polys) in zip(batch, runner.run([a for _, _, a, _ in batch])):
                idx = [i for i, n in enumerate(names) if wanted is None or n in wanted]
                if refiner is not None and idx:
                    refined = refiner.refine(a, b[idx])
                    polys = [None] * len(names)
                    for i, rp in zip(idx, refined):
                        polys[i] = rp
                for i in idx:
                    bx0, by0, bx1, by1 = map(float, b[i])
                    # cut by an inner tile edge: an object that fits in the overlap is found whole in the neighbouring
                    # tile, so the cut piece is dropped; a bigger one is kept and merged with its other piece(s) below
                    sides = (bx0 < edge and x > 0, by0 < edge and y > 0, bx1 > T - edge and x + T < W, by1 > T - edge and y + T < H)
                    if ((sides[0] or sides[2]) and bx1 - bx0 < ov - 4) or ((sides[1] or sides[3]) and by1 - by0 < ov - 4):
                        continue
                    cx, cy = int(min(max((bx0 + bx1) / 2, 0), T - 1)), int(min(max((by0 + by1) / 2, 0), T - 1))
                    if not valid[cy, cx]:
                        continue
                    if poly is not None:
                        gx, gy = transform * (x + (bx0 + bx1) / 2, y + (by0 + by1) / 2)
                        if not poly.contains(Point(gx, gy)):
                            continue
                    boxes.append([bx0 + x, by0 + y, bx1 + x, by1 + y])
                    scores.append(float(s[i]))
                    labels.append(label_ids.setdefault(names[i], len(label_ids)))
                    tiles_of.append((x, y))
                    cut.append(sides)
                    if outlines_on:
                        p = polys[i] if polys is not None else None
                        outlines.append([[(px + x, py + y) for px, py in p]] if p else None)
                    else:
                        outlines.append(None)

        for y in ys_:
            for x in xs_:
                # full-size tiles (padded past the edge): the model rescales every tile, so all must be the same size
                a, v = read_stack(plan_in, Window(c0 + x, r0 + y, T, T), (T, T))
                v[min(T, H - y):, :] = False   # outside the area (the window may reach past it into the image)
                v[:, min(T, W - x):] = False
                if not v.any():
                    skipped += 1
                else:
                    a = np.clip((a - lo) / (hi - lo), 0, 1)
                    a[:, ~v] = 0
                    a = np.nan_to_num(a, nan=0.0)
                    if nb == 1:
                        a = np.repeat(a, 3, axis=0)
                    pending.append((x, y, np.ascontiguousarray(a, dtype="float32"), v))
                done += 1
                if len(pending) >= batch_size:
                    run_batch(pending)
                    pending = []
                    progress.update(done / tiles_total * 0.95, f"Tile {done} of {tiles_total} · {len(boxes):,} objects so far")
                if len(boxes) > max_objects * 3:
                    raise ValueError(f"More than {max_objects * 3:,} detections: raise the score threshold or pick fewer classes")
        if pending:
            run_batch(pending)
        progress.update(0.96, f"Merging {len(boxes):,} detections")

        keep = []
        if boxes:
            order = sorted(range(len(boxes)), key=lambda i: -scores[i])
            alive = _merge_cut(order, boxes, labels, outlines, tiles_of, cut, outlines_on, edge)
            bt, st, lt = torch.tensor([boxes[i] for i in alive]), torch.tensor([scores[i] for i in alive]), torch.tensor([labels[i] for i in alive])
            keep = [alive[k] for k in batched_nms(bt, st, lt, nms_iou).tolist()][:max_objects]

        # pixel boxes / outlines → map coordinates (image CRS) → EPSG:4326
        id_name = {v: k for k, v in label_ids.items()}
        feats, counts, too_big = [], {}, 0
        for i in keep:
            x0, y0, x1, y1 = boxes[i]
            if max_size_m and metres and max((x1 - x0) * abs(transform.a), (y1 - y0) * abs(transform.e)) > max_size_m:
                too_big += 1
                continue
            name = id_name[labels[i]]
            ring = _clean_ring(outlines[i][0]) if outlines_on and outlines[i] else None
            ring = ring or [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
            ring = [transform * (c, r) for c, r in ring]
            g = {"type": "Polygon", "coordinates": [ring]}
            props = {"id": len(feats) + 1, "class": name, "score": round(scores[i], 3)}
            if metres:
                props["width_m"] = round((x1 - x0) * abs(transform.a), 2)
                props["height_m"] = round((y1 - y0) * abs(transform.e), 2)
                props["area_m2"] = round(to_shape(g).area, 1)
            feats.append((g, props))
            counts[name] = counts.get(name, 0) + 1
        if crs and crs.to_epsg() != 4326 and feats:
            if outlines_on:
                feats = [(transform_geom(crs, "EPSG:4326", g), p) for g, p in feats]
            else:   # boxes: one batched transform
                xs, ys = [], []
                for g, _ in feats:
                    for cx, cy in g["coordinates"][0]:
                        xs.append(cx), ys.append(cy)
                lon, lat = warp_xy(crs, "EPSG:4326", xs, ys)
                k = 0
                for g, _ in feats:
                    nring = len(g["coordinates"][0])
                    g["coordinates"] = [[[lon[k + j], lat[k + j]] for j in range(nring)]]
                    k += nring
        fc = {"type": "FeatureCollection",
              "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[round(c[0], 8), round(c[1], 8)] for c in g["coordinates"][0]]]},
                            "properties": p} for g, p in feats]}
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(fc), encoding="utf-8")
        if too_big:
            log.info("%d detections larger than %g m left out", too_big, max_size_m)
        log.info("%d objects found in %d tiles (%d empty tiles skipped)", len(feats), tiles_total, skipped)
        return {"path": str(out_path), "count": len(feats), "tiles": tiles_total, "tile": T, "overlap": ov, "width": W, "height": H,
                "too_big": too_big, "seconds": round(time.time() - t0, 1), "device": str(runner.dev), "model": title,
                "zoom": round(zoom, 3), "outlines": outlines_on,
                "classes": sorted(({"name": k, "count": v, "color": colors.get(k) or color(k)} for k, v in counts.items()), key=lambda c: -c["count"])}
    finally:
        for v in vrts:
            v.close()
        for s in srcs:
            s.close()
        ref.close()
