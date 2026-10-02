"""Scores and pictures for the real-data reports."""

from __future__ import annotations

import base64
import io

import numpy as np
from rasterio.warp import transform as warp_xy


# ------------------------------------------------------------------ detection
def boxes_from_geojson(fc: dict, crs: str, transform) -> list[dict]:
    """Detected objects (EPSG:4326 polygons) → pixel boxes in an image's grid."""
    inv = ~transform
    out = []
    for f in fc.get("features", []):
        ring = f["geometry"]["coordinates"][0]
        xs, ys = warp_xy("EPSG:4326", crs, [p[0] for p in ring], [p[1] for p in ring])
        px = [inv * (x, y) for x, y in zip(xs, ys)]
        cx, cy = [p[0] for p in px], [p[1] for p in px]
        out.append({"cls": f["properties"]["class"], "score": f["properties"].get("score", 1.0), "box": [min(cx), min(cy), max(cx), max(cy)]})
    return out


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def detection_scores(pred: list[dict], truth: list[dict], class_map: dict | None = None, iou: float = 0.5) -> dict:
    """Precision, recall, F1 and AP50 per class (VOC-style), matching each detection to one true box of the same class.

    class_map maps predicted names to true names (e.g. COCO 'car' → 'Car'); unmapped predictions are ignored."""
    if class_map is not None:
        pred = [dict(p, cls=class_map[p["cls"]]) for p in pred if p["cls"] in class_map]
    classes = sorted({t["cls"] for t in truth})
    per, aps = {}, []
    for c in classes:
        T = [t["box"] for t in truth if t["cls"] == c]
        P = sorted((p for p in pred if p["cls"] == c), key=lambda p: -p["score"])
        used = [False] * len(T)
        tp = []
        for p in P:
            best, j = 0.0, -1
            for k, t in enumerate(T):
                if not used[k]:
                    v = _iou(p["box"], t)
                    if v > best:
                        best, j = v, k
            if best >= iou:
                used[j] = True
                tp.append(1)
            else:
                tp.append(0)
        tp = np.array(tp, float)
        ctp = np.cumsum(tp)
        rec = ctp / max(len(T), 1)
        prec = ctp / np.maximum(np.arange(1, len(tp) + 1), 1)
        ap = 0.0
        if len(tp):   # area under the interpolated precision–recall curve
            mrec = np.concatenate([[0], rec, [1]])
            mpre = np.concatenate([[0], prec, [0]])
            for i in range(len(mpre) - 2, -1, -1):
                mpre[i] = max(mpre[i], mpre[i + 1])
            idx = np.where(mrec[1:] != mrec[:-1])[0]
            ap = float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))
        n_tp = int(tp.sum())
        P_ = n_tp / len(P) if P else 0.0
        R_ = n_tp / len(T) if T else 0.0
        per[c] = {"true": len(T), "found": len(P), "correct": n_tp, "precision": P_, "recall": R_,
                  "f1": 2 * P_ * R_ / (P_ + R_) if P_ + R_ else 0.0, "ap50": ap}
        aps.append(ap)
    tot_t = sum(v["true"] for v in per.values())
    tot_p = sum(v["found"] for v in per.values())
    tot_c = sum(v["correct"] for v in per.values())
    P_ = tot_c / tot_p if tot_p else 0.0
    R_ = tot_c / tot_t if tot_t else 0.0
    return {"per_class": per, "precision": P_, "recall": R_, "f1": 2 * P_ * R_ / (P_ + R_) if P_ + R_ else 0.0,
            "map50": float(np.mean(aps)) if aps else 0.0}


def draw_boxes(rgb: np.ndarray, boxes: list[dict], colors: dict, width: int = 3, scale: float = 1.0):
    from PIL import Image, ImageDraw
    im = Image.fromarray(rgb)
    d = ImageDraw.Draw(im)
    for b in boxes:
        x0, y0, x1, y1 = (v * scale for v in b["box"])
        d.rectangle([x0, y0, x1, y1], outline=colors.get(b["cls"], "#ff00ff"), width=width)
    return im


# ------------------------------------------------------------------ segmentation
def seg_scores(pred: np.ndarray, truth: np.ndarray, classes: list[int]) -> dict:
    """Pixel accuracy, IoU per class and mean IoU (pixels where truth is 0 are ignored)."""
    ok = truth > 0
    p, t = pred[ok], truth[ok]
    out = {"accuracy": float((p == t).mean()) if t.size else float("nan"), "iou": {}}
    for c in classes:
        inter = np.sum((p == c) & (t == c))
        union = np.sum((p == c) | (t == c))
        out["iou"][c] = float(inter / union) if union else float("nan")
    vals = [v for v in out["iou"].values() if v == v]
    out["miou"] = float(np.mean(vals)) if vals else float("nan")
    return out


def colorize(classes: np.ndarray, colors: dict) -> np.ndarray:
    """A class raster → RGB using {value: '#rrggbb'} (0 = black)."""
    out = np.zeros((*classes.shape, 3), "uint8")
    for v, c in colors.items():
        c = c.lstrip("#")
        out[classes == v] = [int(c[i:i + 2], 16) for i in (0, 2, 4)]
    return out


def stretch_rgb(a: np.ndarray) -> np.ndarray:
    """(3, H, W) bands → a 2–98 % stretched uint8 (H, W, 3) picture."""
    a = a.astype("float32")
    lo = np.nanpercentile(a, 2, axis=(1, 2), keepdims=True)
    hi = np.nanpercentile(a, 98, axis=(1, 2), keepdims=True)
    return (np.nan_to_num(np.clip((a - lo) / np.maximum(hi - lo, 1e-6), 0, 1)) * 255).astype("uint8").transpose(1, 2, 0)


def side_by_side(*pics: np.ndarray, height: int = 256, gap: int = 6) -> np.ndarray:
    """Several pictures next to each other, all resized to the same height."""
    from PIL import Image
    ims = []
    for p in pics:
        im = Image.fromarray(p if p.ndim == 3 else np.stack([p] * 3, -1))
        ims.append(im.resize((max(1, round(im.width * height / im.height)), height), Image.Resampling.NEAREST))
    W = sum(i.width for i in ims) + gap * (len(ims) - 1)
    canvas = Image.new("RGB", (W, height), (255, 255, 255))
    x = 0
    for i in ims:
        canvas.paste(i, (x, 0))
        x += i.width + gap
    return np.asarray(canvas)


def data_url_png(url: str) -> np.ndarray:
    """A data:image/png;base64 URL (as the app's render endpoint returns) → RGB array."""
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB"))
