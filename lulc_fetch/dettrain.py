"""Train an object detector (YOLO, ultralytics) on an image and labelled shapes, for "Detect object".

The image is cut into tiles with overlap; every labelled shape (polygon, or point with a box size) becomes a box, an
outline or a rotated box in the tiles it falls in (YOLO label files). YOLO26 / YOLO11 then learns from pretrained
weights (COCO, or DOTA aerial images for rotated boxes). Runs in a child process (see lulc_fetch.dlrunner).

Output (one folder)::

    best.pt              weights with the best validation score
    last.pt              weights of the last epoch
    model_config.json    task, classes, bands, stretch, tile size, pixel size, scores (read by Detect object)
    training_log.csv     one row per epoch
    report.html          curves, scores per class, confusion matrix, example predictions
    dataset/             the tiles and labels it was trained on (YOLO format, reusable elsewhere)
    run/                 everything ultralytics wrote (plots, args.yaml)
"""

from __future__ import annotations

import base64
import csv
import json
import logging
import math
import os
import random
import shutil
import time
from pathlib import Path

import numpy as np
import rasterio

from . import progress
from .detect import PALETTE, SIZE_TITLES, _stretch_limits, color, ultra_device, ultra_weights, ultralytics

log = logging.getLogger(__name__)

TASKS = {
    "detect": {"title": "Boxes", "suffix": "", "desc": "An upright box around every object. Fastest to train; needs only rough shapes or points."},
    "segment": {"title": "Outlines", "suffix": "-seg", "desc": "The exact outline of every object (instance segmentation). Needs polygons drawn around the objects."},
    "obb": {"title": "Rotated boxes", "suffix": "-obb", "desc": "A box turned to fit each object: best for ships, planes, vehicles and buildings seen from above. "
                                                                 "Starts from weights pretrained on DOTA aerial images."},
}
FAMILIES = {"yolo26": "YOLO26 (newest)", "yolo11": "YOLO11"}

PARAMS = [
    {"name": "epochs", "title": "Epochs", "kind": "int", "default": 100, "min": 1, "max": 2000,
     "tip": "How many times the model sees every training tile. Early stopping usually ends sooner."},
    {"name": "batch", "title": "Batch size", "kind": "int", "default": 8, "min": 1, "max": 256,
     "tip": "Tiles per training step. Bigger is faster but needs more memory."},
    {"name": "patience", "title": "Patience (epochs)", "kind": "int", "default": 30, "min": 1, "max": 1000,
     "tip": "Stop when the validation score hasn't improved for this many epochs (early stopping); the best epoch is kept."},
    {"name": "val_share", "title": "Validation (%)", "kind": "int", "default": 20, "min": 5, "max": 50,
     "tip": "Tiles held back to score the model after every epoch. They are picked in spatial blocks, so neighbouring (overlapping) tiles never end up on both sides."},
    {"name": "lr0", "title": "Learning rate", "kind": "float", "default": 0.01, "min": 1e-6, "max": 1, "adv": True,
     "tip": "Starting learning rate (SGD 0.01, AdamW about 0.001). Lower it if the loss jumps around."},
    {"name": "optimizer", "title": "Optimiser", "kind": "choice", "default": "auto", "adv": True,
     "choices": [["auto", "Auto"], ["SGD", "SGD"], ["AdamW", "AdamW"], ["MuSGD", "MuSGD"]], "tip": "Auto picks one from the dataset size."},
    {"name": "flipud", "title": "Vertical flips", "kind": "float", "default": 0.5, "min": 0, "max": 1, "adv": True,
     "tip": "Chance of flipping a tile upside down. Seen from above there is no 'up', so 0.5 suits satellite and aerial images."},
    {"name": "degrees", "title": "Random rotation (°)", "kind": "float", "default": 0, "min": 0, "max": 180, "adv": True,
     "tip": "Rotates tiles randomly by up to this angle. Useful for objects in any direction (ships, planes)."},
    {"name": "mosaic", "title": "Mosaic", "kind": "float", "default": 1.0, "min": 0, "max": 1, "adv": True,
     "tip": "Chance of combining 4 tiles into one training image: more variety, usually better results."},
    {"name": "empty_share", "title": "Tiles without objects (%)", "kind": "int", "default": 20, "min": 0, "max": 200, "adv": True,
     "tip": "Background tiles (no objects) added as negatives, as a share of the tiles with objects. They teach the model what isn't an object."},
    {"name": "min_visible", "title": "Keep cut objects from (%)", "kind": "int", "default": 40, "min": 1, "max": 100, "adv": True,
     "tip": "An object cut by a tile edge is labelled in that tile only if at least this much of it is inside."},
    {"name": "device", "title": "Device", "kind": "choice", "default": "auto", "adv": True,
     "choices": [["auto", "Auto"], ["cuda", "NVIDIA GPU (CUDA)"], ["mps", "Apple GPU (MPS)"], ["cpu", "CPU"]],
     "tip": "Auto uses an NVIDIA GPU, else the Apple GPU, else the CPU."},
    {"name": "seed", "title": "Random seed", "kind": "int", "default": 0, "min": 0, "max": 2 ** 31 - 1, "adv": True,
     "tip": "Same seed + same settings = same split and (nearly) the same model."},
]
DEFAULTS = {p["name"]: p["default"] for p in PARAMS}


def schema() -> dict:
    return {"tasks": TASKS, "families": FAMILIES, "sizes": {k: SIZE_TITLES[k] for k in "nsmlx"}, "params": PARAMS, "defaults": DEFAULTS}


# ------------------------------------------------------------------ training data: tiles + YOLO label files
def _objects(ground_truth: dict, crs, inv, res: float) -> tuple[list, list[str]]:
    """Labelled shapes in pixel coordinates: [(class name, shapely geometry)], and the class names (sorted)."""
    from rasterio.warp import transform_geom
    from shapely.affinity import affine_transform
    from shapely.geometry import box, shape

    field = ground_truth.get("field")
    half = float(ground_truth.get("point_size_m") or 5) / 2 / res   # half the box size of a point, in pixels
    mat = [inv.a, inv.b, inv.d, inv.e, inv.xoff, inv.yoff]
    objs, skipped = [], 0
    for f in ground_truth["geojson"].get("features", []):
        g = f.get("geometry")
        if not g:
            continue
        name = str((f.get("properties") or {}).get(field) if field else "object")
        if name in ("None", ""):
            skipped += 1
            continue
        geom = affine_transform(shape(transform_geom("EPSG:4326", crs, g)), mat)
        if geom.geom_type in ("Point", "MultiPoint"):
            for p in getattr(geom, "geoms", [geom]):
                objs.append((name, box(p.x - half, p.y - half, p.x + half, p.y + half)))
        elif geom.geom_type in ("Polygon", "MultiPolygon"):
            for p in getattr(geom, "geoms", [geom]):
                p = p.buffer(0)
                if p.area > 0.5:
                    objs.append((name, p))
        else:
            skipped += 1
    if skipped:
        log.info("%d shapes left out (lines, or no class value)", skipped)
    return objs, sorted({n for n, _ in objs})


def _label_line(cls: int, part, x0: float, y0: float, T: int, task: str) -> str | None:
    from shapely.affinity import translate
    p = translate(part, -x0, -y0)
    if task == "detect":
        a, b, c, d = p.bounds
        w, h = c - a, d - b
        if w < 1 or h < 1:
            return None
        return f"{cls} {(a + c) / 2 / T:.6f} {(b + d) / 2 / T:.6f} {w / T:.6f} {h / T:.6f}"
    if task == "obb":
        pts = list(p.minimum_rotated_rectangle.exterior.coords)[:4]
    else:
        if p.geom_type == "MultiPolygon":
            p = max(p.geoms, key=lambda q: q.area)
        pts = list(p.simplify(0.5, preserve_topology=True).exterior.coords)[:-1]
    if len(pts) < 3:
        return None
    return f"{cls} " + " ".join(f"{min(max(x / T, 0), 1):.6f} {min(max(y / T, 0), 1):.6f}" for x, y in pts)


def make_dataset(inputs: list[dict], ground_truth: dict, out: Path, *, task: str = "detect", tile_px: int = 640, zoom: float = 1.0,
                 overlap: float = 0.2, clip: dict | None = None, stretch: str = "percent", val_share: float = 20, empty_share: float = 20,
                 min_visible: float = 40, seed: int = 0) -> dict:
    """Cut the image into tiles (PNG) with YOLO label files, split into train / val by spatial blocks. Returns dataset info.

    With zoom > 1 each tile covers tile_px / zoom image pixels and is enlarged to tile_px (small objects get bigger);
    YOLO labels are relative to the tile, so they don't change."""
    from PIL import Image
    from rasterio.windows import Window
    from shapely.geometry import box
    from shapely.strtree import STRtree

    from .analysis import clip_region
    from .patches import open_inputs, read_stack

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
        res = abs(ref.transform.a)
        objs, names = _objects(ground_truth, ref.crs, ~ref.transform, res)
        if not objs:
            raise ValueError("The ground-truth layer has no polygons or points with a class (check the class attribute)")
        cls_of = {n: i for i, n in enumerate(names)}
        geoms = [g for _, g in objs]
        tree = STRtree(geoms)
        lo, hi = _stretch_limits(plan_in, win, stretch, nb)
        lo, hi = lo[:, None, None], hi[:, None, None]
        area = None
        if geom is not None:
            from shapely.affinity import affine_transform
            from shapely.geometry import shape
            inv = ~ref.transform
            area = affine_transform(shape(geom), [inv.a, inv.b, inv.d, inv.e, inv.xoff, inv.yoff])

        out_px = int(tile_px)
        T = max(32, int(round(out_px / max(zoom, 0.25))))   # tile size in image pixels
        S = max(1, T - int(round(T * min(max(overlap, 0), 0.5))))
        tiles = []   # (ix, iy, x, y, label lines)
        for iy, y in enumerate(range(r0, r0 + max(H - (T - S), 1), S)):
            for ix, x in enumerate(range(c0, c0 + max(W - (T - S), 1), S)):
                tb = box(x, y, x + T, y + T)
                if area is not None and not area.intersects(tb):
                    continue
                lines, counts = [], {}
                for k in tree.query(tb):
                    g = geoms[k]
                    part = g.intersection(tb)
                    if part.is_empty or part.area < 1 or part.area / g.area < min_visible / 100:
                        continue
                    ln = _label_line(cls_of[objs[k][0]], part, x, y, T, task)
                    if ln:
                        lines.append(ln)
                        counts[objs[k][0]] = counts.get(objs[k][0], 0) + 1
                tiles.append((ix, iy, x, y, lines, counts))
        with_obj = [t for t in tiles if t[4]]
        if not with_obj:
            raise ValueError("None of the labelled shapes falls in the image (or the selected area)")
        rnd = random.Random(seed)
        empty = [t for t in tiles if not t[4]]
        rnd.shuffle(empty)
        chosen = with_obj + empty[:int(len(with_obj) * empty_share / 100)]

        # spatial blocks of 2 × 2 tiles (more with big overlaps) go wholly to train or val, so few overlapping tiles leak
        B = max(2, math.ceil(T / S))
        blocks: dict[tuple, list] = {}
        for t in chosen:
            blocks.setdefault((t[0] // B, t[1] // B), []).append(t)
        keys = list(blocks)
        rnd.shuffle(keys)
        n_obj = sum(len(t[4]) for t in chosen)
        val_keys, got = set(), 0
        for k in keys:
            if got >= n_obj * val_share / 100 or len(val_keys) >= len(keys) - 1:
                break
            val_keys.add(k)
            got += sum(len(t[4]) for t in blocks[k])
        if len(chosen) < 4 or not val_keys:
            raise ValueError(f"Only {len(chosen)} tile(s) with objects: label more objects, use a bigger area or smaller tiles")

        for sub in ("images/train", "images/val", "labels/train", "labels/val"):
            (out / sub).mkdir(parents=True, exist_ok=True)
        stats = {"train": {"tiles": 0, "objects": {}}, "val": {"tiles": 0, "objects": {}}}
        for n, t in enumerate(chosen):
            ix, iy, x, y, lines, counts = t
            split = "val" if (ix // B, iy // B) in val_keys else "train"
            a, v = read_stack(plan_in, Window(x, y, T, T), (T, T))
            v[min(T, r0 + H - y):, :] = False
            v[:, min(T, c0 + W - x):] = False
            a = np.nan_to_num(np.clip((a - lo) / (hi - lo), 0, 1), nan=0.0)
            a[:, ~v] = 0
            if nb == 1:
                a = np.repeat(a, 3, axis=0)
            stem = f"tile_{iy:04d}_{ix:04d}"
            im = Image.fromarray((a.transpose(1, 2, 0) * 255).round().astype("uint8"))
            if T != out_px:
                im = im.resize((out_px, out_px), Image.Resampling.BILINEAR if T < out_px else Image.Resampling.LANCZOS)
            im.save(out / "images" / split / f"{stem}.png")
            (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
            s = stats[split]
            s["tiles"] += 1
            for k_, c in counts.items():
                s["objects"][k_] = s["objects"].get(k_, 0) + c
            if n % 20 == 0:
                progress.update(0.08 * n / len(chosen), f"Writing training tiles {n + 1} of {len(chosen)}")
        (out / "data.yaml").write_text(
            f"path: {json.dumps(str(out.resolve()))}\ntrain: images/train\nval: images/val\nnames:\n"
            + "".join(f"  {i}: {json.dumps(n)}\n" for i, n in enumerate(names)), encoding="utf-8")
        log.info("Training data: %d tiles (%d train, %d validation), %d objects, %d classes, %d empty tiles",
                 len(chosen), stats["train"]["tiles"], stats["val"]["tiles"], n_obj, len(names), len(chosen) - len(with_obj))
        crs = ref.crs
        # pixel_size: the size of one model-input pixel on the ground (Detect object zooms other images to match it)
        return {"yaml": str(out / "data.yaml"), "names": names, "stats": stats, "tile_px": out_px, "zoom": zoom,
                "pixel_size": res * T / out_px if crs and crs.is_projected else None, "image_pixel_size": res if crs and crs.is_projected else None,
                "crs": crs.to_string() if crs else None, "bands": band_names, "stretch": stretch,
                "empty_tiles": len(chosen) - len(with_obj), "objects": n_obj}
    finally:
        for v in vrts:
            v.close()
        for s in srcs:
            s.close()
        ref.close()


# ------------------------------------------------------------------ training
def _metric_keys(task: str) -> dict:
    t = "M" if task == "segment" else "B"
    return {"map50": f"metrics/mAP50({t})", "map": f"metrics/mAP50-95({t})", "precision": f"metrics/precision({t})", "recall": f"metrics/recall({t})"}


def train(inputs: list[dict], ground_truth: dict, out_dir: str | Path, *, name: str = "detector", task: str = "detect",
          family: str = "yolo26", size: str = "s", pretrained: bool = True, tile_px: int = 640, zoom: float = 1.0, overlap: float = 0.2,
          clip: dict | None = None, stretch: str = "percent", params: dict | None = None, class_colors: dict | None = None) -> dict:
    import torch

    from .dl import _device
    t_start = time.time()
    if task not in TASKS or family not in FAMILIES or size not in "nsmlx":
        raise ValueError("Unknown model choice")
    P = {**DEFAULTS, **{k: v for k, v in (params or {}).items() if k in DEFAULTS and v is not None}}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ul = ultralytics()
    progress.update(0.0, "Making training tiles")
    ds = make_dataset(inputs, ground_truth, out / "dataset", task=task, tile_px=tile_px, zoom=zoom, overlap=overlap, clip=clip, stretch=stretch,
                      val_share=P["val_share"], empty_share=P["empty_share"], min_visible=P["min_visible"], seed=int(P["seed"]))
    dev = _device(P["device"])
    stem = f"{family}{size}{TASKS[task]['suffix']}"
    if pretrained:
        weights = ultra_weights(f"{stem}.pt")
        log.info("Starting from %s pretrained on %s", stem, "DOTA aerial images" if task == "obb" else "COCO")
    else:
        weights = f"{stem}.yaml"
        log.info("Starting %s from random weights", stem)
    model = ul.YOLO(weights, task=task)
    os.chdir(out)   # this is a child process; ultralytics may write helper files to the working folder

    epochs = int(P["epochs"])
    keys = _metric_keys(task)
    history: list[dict] = []
    state = {"stopped": None, "best": None, "best_epoch": None, "t_epoch": time.time(), "batches": 1}

    def on_train_epoch_start(tr):
        state["t_epoch"] = time.time()
        state["batches"] = max(1, len(tr.train_loader))

    def on_batch_end(tr):
        frac = (tr.epoch + min(1.0, (getattr(tr, "_lulc_b", 0) + 1) / state["batches"])) / epochs
        tr._lulc_b = getattr(tr, "_lulc_b", 0) + 1
        try:
            progress.update(0.1 + 0.85 * frac, f"Epoch {tr.epoch + 1}/{epochs}")
        except progress.Cancelled:
            if not tr.stop:
                log.info("Stopping after this epoch; the best model is kept")
            state["stopped"] = "stopped by the user"
            tr.stop = True

    def on_fit_epoch_end(tr):
        if history and not getattr(tr, "_lulc_b", 0):   # the final validation of the best weights (no training batches since)
            return
        tr._lulc_b = 0
        m = tr.metrics or {}
        tl = tr.tloss   # a tensor of loss parts, or a dict of them, depending on the ultralytics version
        tl = sum(float(v) for v in tl.values()) if isinstance(tl, dict) else float(tl.sum()) if hasattr(tl, "sum") else float(tl or 0)
        vl = sum(float(v) for k, v in m.items() if k.startswith("val/"))
        row = {"epoch": tr.epoch + 1, "train_loss": tl, "val_loss": vl or None,
               **{k: (float(m[mk]) if mk in m else None) for k, mk in keys.items()}, "seconds": round(time.time() - state["t_epoch"], 1)}
        history.append(row)
        fit = row["map"] if row["map"] is not None else 0
        if state["best"] is None or fit > state["best"]:
            state["best"], state["best_epoch"] = fit, row["epoch"]
        done = len(history)
        eta = (time.time() - t_start) / max(done, 1) * max(epochs - done, 0)
        log.info("Epoch %d/%d · loss %.3f · mAP50 %.3f · mAP50-95 %.3f", row["epoch"], epochs, tl, row["map50"] or 0, row["map"] or 0)
        progress.live({"history": history, "best_epoch": state["best_epoch"], "epochs": epochs, "eta_s": round(eta), "batch_size": int(P["batch"]),
                       "device": str(dev), "kind": "detection"})

    model.add_callback("on_train_epoch_start", on_train_epoch_start)
    model.add_callback("on_train_batch_end", on_batch_end)
    model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
    train_args = dict(data=ds["yaml"], epochs=epochs, imgsz=ds["tile_px"], batch=int(P["batch"]), patience=int(P["patience"]),
                      device=ultra_device(dev), project=str(out), name="run", exist_ok=True, workers=0, seed=int(P["seed"]),
                      deterministic=False, lr0=float(P["lr0"]), optimizer=P["optimizer"], flipud=float(P["flipud"]), fliplr=0.5,
                      degrees=float(P["degrees"]), mosaic=float(P["mosaic"]), plots=True, verbose=False, amp=dev.type == "cuda",
                      pretrained=bool(pretrained))
    try:
        model.train(**train_args)
    except torch.OutOfMemoryError as e:
        raise RuntimeError("Out of memory: lower the batch size or the tile size") from e
    run = out / "run"
    best_w = run / "weights" / "best.pt"
    if not best_w.is_file():
        raise RuntimeError("Training ended before the first epoch finished, so there is no model to keep")
    shutil.copy2(best_w, out / "best.pt")
    if (run / "weights" / "last.pt").is_file():
        shutil.copy2(run / "weights" / "last.pt", out / "last.pt")
    if len(history) < epochs and not state["stopped"]:
        state["stopped"] = f"early stopping: no improvement in {int(P['patience'])} epochs"

    # final scores of the best weights on the validation tiles (also writes the confusion matrix and PR curves)
    progress.update(0.97, "Scoring the best model")
    best = ul.YOLO(str(out / "best.pt"))
    vr = best.val(data=ds["yaml"], imgsz=ds["tile_px"], batch=int(P["batch"]), device=ultra_device(dev), project=str(out), name="val",
                  exist_ok=True, plots=True, verbose=False, workers=0)
    mt = vr.seg if task == "segment" else vr.box
    names = ds["names"]
    per_class = []
    ap50 = dict(zip(map(int, vr.ap_class_index), mt.ap50)) if len(vr.ap_class_index) else {}
    ap = dict(zip(map(int, vr.ap_class_index), mt.ap)) if len(vr.ap_class_index) else {}
    for i, n in enumerate(names):
        per_class.append({"name": n, "ap50": float(ap50[i]) if i in ap50 else None, "ap": float(ap[i]) if i in ap else None,
                          "val_objects": ds["stats"]["val"]["objects"].get(n, 0), "train_objects": ds["stats"]["train"]["objects"].get(n, 0)})
    colors = class_colors or {}
    config = {
        "kind": "detection", "framework": "ultralytics", "name": name, "task": task, "task_title": TASKS[task]["title"],
        "family": family, "size": size, "arch_title": f"{FAMILIES[family].split(' ')[0]} {SIZE_TITLES[size].split(' ')[0]} · {TASKS[task]['title'].lower()}",
        "weights": "best.pt", "pretrained": bool(pretrained),
        "classes": [{"value": i, "name": n, "color": colors.get(n) or (color(n) if n != "object" else PALETTE[0])} for i, n in enumerate(names)],
        "bands": ds["bands"], "in_channels": 3, "stretch": ds["stretch"], "tile_px": ds["tile_px"], "overlap": overlap, "zoom": zoom,
        "pixel_size": ds["pixel_size"], "image_pixel_size": ds["image_pixel_size"], "crs": ds["crs"], "dataset": {"stats": ds["stats"], "empty_tiles": ds["empty_tiles"], "objects": ds["objects"]},
        "params": P, "epochs_run": len(history), "best_epoch": state["best_epoch"], "stopped": state["stopped"],
        "val": {"map50": float(mt.map50), "map": float(mt.map), "precision": float(mt.mp), "recall": float(mt.mr), "per_class": per_class},
        "trained": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t_start, 1), "device": str(dev),
    }
    (out / "model_config.json").write_text(json.dumps(config, indent=1), encoding="utf-8")
    with open(out / "training_log.csv", "w", newline="") as f:
        if history:
            w = csv.DictWriter(f, fieldnames=list(history[0]))
            w.writeheader()
            w.writerows(history)
    _report(out, config, history)
    log.info("Model saved in %s · validation mAP50 %.3f · mAP50-95 %.3f", out, config["val"]["map50"], config["val"]["map"])
    return {"folder": str(out), "config": config, "history": history}


# ------------------------------------------------------------------ report
def _img(p: Path) -> str:
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f'<img src="data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}" alt="{p.stem}">'


def _report(out: Path, c: dict, history: list[dict]):
    from html import escape
    pct = lambda v: "–" if v is None else f"{100 * v:.1f}%"   # noqa: E731
    rows = "".join(f"<tr><td><i style='background:{escape(k['color'])}'></i>{escape(k['name'])}</td><td>{pct(pc['ap50'])}</td><td>{pct(pc['ap'])}</td>"
                   f"<td>{pc['train_objects']}</td><td>{pc['val_objects']}</td></tr>" for k, pc in zip(c["classes"], c["val"]["per_class"]))
    # loss and mAP curves
    svg = ""
    if history:
        Wd, Ht, l, r, t, b = 640, 220, 44, 40, 10, 26
        n = max(len(history), 2)
        X = lambda e: l + (e - 1) / (n - 1) * (Wd - l - r)   # noqa: E731
        ls = [h[k] for h in history for k in ("train_loss", "val_loss") if h.get(k) is not None]
        lmax = max(ls + [1e-6]) * 1.05
        YL = lambda v: Ht - b - v / lmax * (Ht - b - t)   # noqa: E731
        YM = lambda v: Ht - b - v * (Ht - b - t)   # noqa: E731
        line = lambda k, Y, col, dash="": ("<polyline fill='none' stroke='%s' stroke-width='2' %s points='%s'/>" % (   # noqa: E731
            col, f"stroke-dasharray='{dash}'" if dash else "", " ".join(f"{X(h['epoch']):.1f},{Y(h[k]):.1f}" for h in history if h.get(k) is not None)))
        grid = "".join(f"<line x1='{l}' x2='{Wd - r}' y1='{YM(f)}' y2='{YM(f)}' stroke='#e5e7eb'/><text x='{Wd - r + 4}' y='{YM(f) + 4}'>{f}</text>"
                       f"<text x='{l - 4}' y='{YM(f) + 4}' text-anchor='end'>{f * lmax:.2f}</text>" for f in (0, .25, .5, .75, 1))
        svg = (f"<svg viewBox='0 0 {Wd} {Ht}' class='chart'>{grid}{line('train_loss', YL, '#2563eb')}{line('val_loss', YL, '#f59e0b')}"
               f"{line('map50', YM, '#16a34a', '5 3')}{line('map', YM, '#7c3aed', '5 3')}</svg>"
               "<p class='legend'><b style='color:#2563eb'>■</b> training loss <b style='color:#f59e0b'>■</b> validation loss (left axis) · "
               "<b style='color:#16a34a'>- -</b> mAP50 <b style='color:#7c3aed'>- -</b> mAP50-95 (right axis)</p>")
    plots = []
    for folder, pats in ((out / "val", ["*confusion_matrix_normalized.png", "*PR_curve.png", "val_batch0_pred.jpg", "val_batch1_pred.jpg"]),
                         (out / "run", ["labels.jpg", "train_batch0.jpg"])):
        for pat in pats:
            for p in sorted(folder.glob(pat))[:2]:
                plots.append(f"<figure>{_img(p)}<figcaption>{escape(p.stem.replace('_', ' '))}</figcaption></figure>")
    v = c["val"]
    ds = c["dataset"]
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(c['name'])} · detection report</title><style>
body{{font:14px/1.5 system-ui,sans-serif;margin:0;background:#f8fafc;color:#0f172a}} main{{max-width:980px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}} .sub{{color:#64748b}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:14px}}
.tile{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px}} .tile b{{font-size:22px;display:block}} .tile span{{color:#64748b;font-size:12px}}
table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e8f0;border-radius:10px}} td,th{{padding:6px 10px;border-bottom:1px solid #e2e8f0;text-align:left}}
td i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}} .chart{{width:100%;background:#fff;border:1px solid #e2e8f0;border-radius:10px}}
.chart text{{font-size:10px;fill:#64748b}} .legend{{color:#64748b;font-size:12px}} figure{{margin:0;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:8px}}
figure img{{width:100%;height:auto;display:block}} figcaption{{color:#64748b;font-size:12px;margin-top:4px}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}
@media (prefers-color-scheme:dark){{body{{background:#0f172a;color:#e2e8f0}} .tile,table,.chart,figure{{background:#111827;border-color:#334155}} td,th{{border-color:#334155}}}}
</style></head><body><main>
<h1>{escape(c['name'])}</h1><div class="sub">{escape(c['arch_title'])} · trained {escape(c['trained'])} · {c['epochs_run']} epochs (best {c['best_epoch']}) · {escape(c['device'])}
{(' · ' + escape(c['stopped'])) if c.get('stopped') else ''}</div>
<div class="tiles"><div class="tile"><b>{pct(v['map50'])}</b><span>mAP50 (validation)</span></div><div class="tile"><b>{pct(v['map'])}</b><span>mAP50-95</span></div>
<div class="tile"><b>{pct(v['precision'])}</b><span>Precision</span></div><div class="tile"><b>{pct(v['recall'])}</b><span>Recall</span></div></div>
<p class="sub">mAP50 = average precision when a predicted box must overlap the true one by at least 50 %; mAP50-95 averages stricter overlaps (50–95 %).
Precision = share of detections that are right; recall = share of the objects that were found.</p>
<h2>Per class</h2><table><tr><th>Class</th><th>AP50</th><th>AP50-95</th><th>Training objects</th><th>Validation objects</th></tr>{rows}</table>
<h2>Training curves</h2>{svg}
<h2>Data</h2><p>{ds['objects']} objects in {ds['stats']['train']['tiles'] + ds['stats']['val']['tiles']} tiles of {c['tile_px']} px
({ds['stats']['train']['tiles']} training, {ds['stats']['val']['tiles']} validation, split in spatial blocks; {ds['empty_tiles']} tiles without objects)
{f"· {c['image_pixel_size']:.2f} m image pixels, zoom {c['zoom']:g}×" if c.get('image_pixel_size') else ''} · bands {escape(', '.join(map(str, c['bands'])))} · stretch {escape(c['stretch'])}</p>
<h2>Plots</h2><div class="grid">{''.join(plots) or '<p class="sub">No plots.</p>'}</div>
</main></body></html>"""
    (out / "report.html").write_text(html, encoding="utf-8")
