"""Make training data: cut large images (and their ground truth) into patches for deep-learning models.

Output folder layout::

    <folder>/<name>/
        images/<name>_r0000_c0000.tif     image patches (all input bands, georeferenced GeoTIFF)
        labels/<name>_r0000_c0000.tif     label patches (same grid; 0 = no label / ignore, 1…K = classes)
        classes.txt                        label value → class name / original value / colour / pixel count
        dataset.json                       everything a training tool needs (bands, patch size, classes…)
        patches.csv                        one row per patch: file, position, bounds, valid / labelled share, main class

No train / validation split is made: the training tool decides that.

Several input layers are stacked onto the first layer's grid (reprojected on the fly). Patch size and overlap
are given in metres (map units) and converted to pixels of that grid.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from . import progress
from .analysis import _palette, clip_region
from .tabular import _GroundTruth, _column_names

WORLDCOVER = {10: ("Tree cover", "#006400"), 20: ("Shrubland", "#ffbb22"), 30: ("Grassland", "#ffff4c"), 40: ("Cropland", "#f096ff"),
              50: ("Built-up", "#fa0000"), 60: ("Bare / sparse vegetation", "#b4b4b4"), 70: ("Snow and ice", "#f0f0f0"),
              80: ("Permanent water bodies", "#0064c8"), 90: ("Herbaceous wetland", "#0096a0"), 95: ("Mangroves", "#00cf75"),
              100: ("Moss and lichen", "#fae6a0")}   # ESA WorldCover classes and colours

log = logging.getLogger(__name__)
MAX_PATCHES = 200_000
PREVIEW_CELLS = 2500
FALLBACK_COLORS = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4", "#46f0f0", "#f032e6",
                   "#bcf60c", "#fabebe", "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000", "#aaffc3"]


def _grid(ref, clip, patch_m, overlap_m, edge):
    """Patch windows on the reference grid covering the area of interest."""
    win, geom = clip_region(ref, clip)
    win = win or Window(0, 0, ref.width, ref.height)
    rx, ry = abs(ref.res[0]), abs(ref.res[1])
    pw, ph = max(4, int(round(patch_m[0] / rx))), max(4, int(round(patch_m[1] / ry)))
    ox, oy = int(round((overlap_m[0] or 0) / rx)), int(round((overlap_m[1] or 0) / ry))
    if ox >= pw or oy >= ph:
        raise ValueError("The overlap must be smaller than the patch size")
    sx, sy = pw - ox, ph - oy
    c0, r0 = int(win.col_off), int(win.row_off)
    w, h = int(math.ceil(win.width)), int(math.ceil(win.height))
    if w < pw or h < ph:
        if edge == "drop":
            raise ValueError(f"The area ({w}×{h} px) is smaller than one patch ({pw}×{ph} px). Use smaller patches or keep edge patches.")
    cols = range(0, max(1, (w - pw) // sx + 1) * sx, sx) if edge == "drop" else range(0, max(w - ox, 1), sx)
    rows = range(0, max(1, (h - ph) // sy + 1) * sy, sy) if edge == "drop" else range(0, max(h - oy, 1), sy)
    cells = [(i, j, Window(c0 + c, r0 + r, pw, ph)) for i, r in enumerate(rows) for j, c in enumerate(cols)]
    return {"windows": cells, "patch_px": [pw, ph], "overlap_px": [ox, oy], "stride_px": [sx, sy], "geom": geom,
            "pixel_size": [rx, ry], "area_px": [w, h], "rows": len(rows), "cols": len(cols)}


def _cell_polygon(ref, win):
    from rasterio.warp import transform_geom
    t = ref.window_transform(win)
    x0, y0 = t * (0, 0)
    x1, y1 = t * (win.width, win.height)
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return transform_geom(ref.crs, "EPSG:4326", {"type": "Polygon", "coordinates": [ring]})


def plan(ref_path, *, clip=None, patch_m=(2560, 2560), overlap_m=(0, 0), edge="pad") -> dict:
    """How many patches a setting makes, and their outlines (for the map preview)."""
    with rasterio.open(ref_path) as ref:
        g = _grid(ref, clip, patch_m, overlap_m, edge)
        n = len(g["windows"])
        step = max(1, math.ceil(n / PREVIEW_CELLS))
        feats = [{"type": "Feature", "geometry": _cell_polygon(ref, w), "properties": {"row": i, "col": j}}
                 for k, (i, j, w) in enumerate(g["windows"]) if k % step == 0]
        unit = "°" if ref.crs and ref.crs.is_geographic else "m"
    return {"count": n, "rows": g["rows"], "cols": g["cols"], "patch_px": g["patch_px"], "overlap_px": g["overlap_px"],
            "stride_px": g["stride_px"], "pixel_size": g["pixel_size"], "unit": unit, "area_px": g["area_px"],
            "preview": {"type": "FeatureCollection", "features": feats}, "preview_every": step}


def _rgba_hex(c):
    return "#%02x%02x%02x" % tuple(int(v) for v in c[:3])


def open_inputs(inputs: list[dict], ref) -> tuple[list[dict], list[str], list, list]:
    """Open input layers on the reference layer's grid (the first input is the reference itself).

    Returns (readers, band names, open files, warped views); the caller closes the last two."""
    band_names, plan_in, srcs, vrts = [], [], [], []
    try:
        for k, it in enumerate(inputs):
            s = rasterio.open(it["path"])
            srcs.append(s)
            bands = it.get("bands") or list(range(1, s.count + 1))
            for b in bands:
                if not 1 <= b <= s.count:
                    raise ValueError(f"{it.get('name') or Path(it['path']).name} has no band {b}")
            classes = all(_palette(s, b) for b in bands)
            if k == 0 and Path(it["path"]).resolve() == Path(ref.name).resolve():
                rd = s
            else:
                rd = WarpedVRT(s, crs=ref.crs, transform=ref.transform, width=ref.width, height=ref.height,
                               resampling=Resampling.nearest if classes else Resampling.bilinear, src_nodata=s.nodata, nodata=s.nodata)
                vrts.append(rd)
            layer = re.sub(r"[^A-Za-z0-9]+", "_", it.get("name") or Path(it["path"]).stem).strip("_")[:24] or f"layer{k + 1}"
            for b in bands:
                d = s.descriptions[b - 1]
                band_names.append(d if d and not re.fullmatch(r"(band_?\d*|data|layer|value|elevation|)", d, re.I) else (layer if len(bands) == 1 else f"{layer}_{b}"))
            plan_in.append({"reader": rd, "bands": bands, "scale": float(it.get("scale", 1) or 1), "offset": float(it.get("offset", 0) or 0),
                            "nodata": s.nodata, "dtype": s.dtypes[0]})
    except BaseException:
        for v in vrts:
            v.close()
        for s in srcs:
            s.close()
        raise
    return plan_in, _column_names(band_names), srcs, vrts


def read_stack(plan_in: list[dict], window: Window, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """All input bands for a window as float32 (bands, h, w) and a valid-pixel mask; parts outside the image are invalid."""
    h, w = shape
    nb = sum(len(p["bands"]) for p in plan_in)
    ref = plan_in[0]["reader"]
    out = np.full((nb, h, w), np.nan, "float32")
    valid = np.zeros((h, w), bool)
    inter = window.intersection(Window(0, 0, ref.width, ref.height)) if window.col_off < ref.width and window.row_off < ref.height \
        and window.col_off + window.width > 0 and window.row_off + window.height > 0 else None
    if inter is None:
        return out, valid
    ci, ri = int(inter.col_off - window.col_off), int(inter.row_off - window.row_off)
    iw, ih = int(inter.width), int(inter.height)
    valid[ri:ri + ih, ci:ci + iw] = True
    b0 = 0
    for p in plan_in:
        data = p["reader"].read(p["bands"], window=inter, masked=True)
        arr = np.ma.filled(data.astype("float32") * p["scale"] + p["offset"], np.nan)
        out[b0:b0 + len(p["bands"]), ri:ri + ih, ci:ci + iw] = arr
        b0 += len(p["bands"])
    valid &= np.isfinite(out).all(axis=0)
    return out, valid


def dataset_folder(parent: str | Path, name: str) -> Path:
    """The folder a dataset called `name` is written to (a file-safe version of the name)."""
    return Path(parent) / (re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "training_patches")


def make(inputs: list[dict], out_parent: str | Path, *, name: str = "training_patches", ground_truth: dict | None = None,
         clip: dict | None = None, patch_m=(2560, 2560), overlap_m=(0, 0), edge: str = "pad", min_valid: float = 0.5,
         require_labels: bool = False, min_labelled: float = 0.01, remap: bool = True,
         class_colors: dict | None = None) -> dict:
    """inputs: [{"path", "bands": [ints] | None, "name", "scale", "offset"}]; the first one defines the grid."""
    t0 = time.time()
    if not inputs:
        raise ValueError("Choose at least one input layer")
    out = dataset_folder(out_parent, name)
    stem = out.name
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already exists and isn't empty. Choose another name or folder.")
    ref = rasterio.open(inputs[0]["path"])
    srcs, vrts, gt = [], [], None
    created = False
    try:
        g = _grid(ref, clip, patch_m, overlap_m, edge)
        cells, (pw, ph) = g["windows"], g["patch_px"]
        if len(cells) > MAX_PATCHES:
            raise ValueError(f"{len(cells):,} patches is too many (max {MAX_PATCHES:,}). Use bigger patches, less overlap or a smaller area.")

        # ---- inputs on the reference grid
        plan_in, band_names, srcs, vrts = open_inputs(inputs, ref)
        scaled = any(p["scale"] != 1 or p["offset"] != 0 for p in plan_in)
        dtypes = {p["dtype"] for p in plan_in}
        out_dtype = dtypes.pop() if len(dtypes) == 1 and not scaled else "float32"
        is_float = np.issubdtype(np.dtype(out_dtype), np.floating)
        nodata_out = float("nan") if is_float else (plan_in[0]["nodata"] if plan_in[0]["nodata"] is not None else 0)

        # ---- ground truth: class values → label ids (0 = no label)
        classes, lut_vals, lut_ids = [], None, None
        if ground_truth:
            gt = _GroundTruth(ground_truth, ref.crs, "label")
            names, colors = {}, {}
            if gt.kind == "raster":
                pal = _palette(gt.src, gt.band)
                if pal:
                    cmap, nm = pal
                    names = {int(k): v for k, v in nm.items()}
                    colors = {int(k): _rgba_hex(v) for k, v in cmap.items()}
                progress.update(0.01, "Finding the classes in the ground truth")
                found = set()
                c0, r0, w, h = cells[0][2].col_off, cells[0][2].row_off, g["area_px"][0], g["area_px"][1]
                for rr in range(0, h, 512):
                    win = Window(c0, r0 + rr, w, min(512, h - rr))
                    lab = gt.strip(ref.window_transform(win), (int(win.height), int(win.width)), ref.crs)
                    found |= {float(v) for v in np.unique(lab[np.isfinite(lab)])}
                values = sorted(found)
                # ESA WorldCover without its legend (e.g. a cut-out): its official class names and colours
                ints = {int(v) for v in values if float(v).is_integer()}
                if not names and ints and ints <= set(WORLDCOVER) and re.search(r"world_?cover", str(ground_truth.get("path", "")), re.I):
                    names = {v: WORLDCOVER[v][0] for v in ints}
                    colors = {v: WORLDCOVER[v][1] for v in ints}
            else:
                if gt.names:
                    values = sorted(gt.names)
                    names = {int(k): v for k, v in gt.names.items()}
                else:
                    values = sorted({float(v) - (1 if gt.zero_label else 0) for _, v in gt.shapes})
                if class_colors:
                    colors = {k: class_colors.get(str(names.get(int(k), k))) for k in values if class_colors.get(str(names.get(int(k), k)))}
            if not values:
                raise ValueError("The ground truth has no classes inside the image / area")
            if len(values) > 65535:
                raise ValueError("Too many different label values (is the ground truth a continuous raster?)")
            for i, v in enumerate(values, start=1):
                iv = int(v) if float(v).is_integer() else v
                classes.append({"value": i if remap else iv, "original": iv, "name": names.get(int(v), str(iv)) if float(v).is_integer() else str(iv),
                                "color": colors.get(int(v)) if float(v).is_integer() else None, "pixels": 0, "patches": 0})
            lut_vals = np.array(values, dtype="float64")
            lut_ids = np.array([c["value"] for c in classes], dtype="int64")
            max_id = int(max(lut_ids.max(), 0))
            label_dtype = "uint8" if max_id < 256 else "uint16"
            if not remap and (lut_vals.min() < 0 or not all(float(v).is_integer() for v in values)):
                raise ValueError("Original label values must be whole numbers ≥ 0 to keep them; tick 'Number classes 1, 2, 3…'")

        # ---- write patches
        out.mkdir(parents=True, exist_ok=True)
        created = True
        (out / "images").mkdir()
        if gt:
            (out / "labels").mkdir()
        rows_csv, kept, skipped_empty, skipped_unlabelled = [], [], 0, 0
        unlabelled_px = 0
        geom = g["geom"]
        cmap_out = None
        if gt:
            cmap_out = {0: (0, 0, 0, 0)}
            for n, c in enumerate(classes):
                c["color"] = c["color"] or FALLBACK_COLORS[n % len(FALLBACK_COLORS)]
                col = c["color"].lstrip("#")
                cmap_out[int(c["value"])] = (int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16), 255)
        class_tag = json.dumps({str(int(c["value"])): c["name"] for c in classes}) if gt else "{}"
        for k, (i, j, win) in enumerate(cells):
            if k % 25 == 0:
                progress.update(0.05 + 0.9 * k / len(cells), f"Patch {k + 1:,} of {len(cells):,}")
            # part of the window inside the image (edge patches are padded)
            inter = win.intersection(Window(0, 0, ref.width, ref.height))
            ci, ri = int(inter.col_off - win.col_off), int(inter.row_off - win.row_off)
            iw, ih = int(inter.width), int(inter.height)
            img = np.full((len(band_names), ph, pw), nodata_out, dtype="float32" if is_float else out_dtype)
            valid = np.zeros((ph, pw), bool)
            valid[ri:ri + ih, ci:ci + iw] = True
            b0 = 0
            for p in plan_in:
                data = p["reader"].read(p["bands"], window=inter, masked=True)
                arr = data.astype("float32") * p["scale"] + p["offset"] if (is_float or scaled) else data
                if is_float:
                    arr = np.ma.filled(arr.astype("float32"), np.nan)
                    valid[ri:ri + ih, ci:ci + iw] &= np.isfinite(arr).all(axis=0)
                else:
                    m = np.ma.getmaskarray(data).any(axis=0)
                    valid[ri:ri + ih, ci:ci + iw] &= ~m
                    arr = np.ma.filled(arr, nodata_out)
                img[b0:b0 + len(p["bands"]), ri:ri + ih, ci:ci + iw] = arr
                b0 += len(p["bands"])
            wt = ref.window_transform(win)
            if geom is not None:
                from rasterio.features import geometry_mask
                inside = geometry_mask([geom], out_shape=(ph, pw), transform=wt, invert=True)
                valid &= inside
            vfrac = float(valid.mean())
            if vfrac < min_valid:
                skipped_empty += 1
                continue
            if is_float:
                img[:, ~valid] = np.nan
            else:
                img[:, ~valid] = nodata_out
            lab_out, lfrac, dominant = None, None, None
            if gt:
                lab = gt.strip(wt, (ph, pw), ref.crs)
                lab[~valid] = np.nan
                ids = np.zeros((ph, pw), dtype="int64")
                fin = np.isfinite(lab)
                if fin.any():
                    pos = np.searchsorted(lut_vals, lab[fin])
                    pos = np.clip(pos, 0, len(lut_vals) - 1)
                    ok = np.isclose(lut_vals[pos], lab[fin])
                    vals = np.where(ok, lut_ids[pos], 0)
                    ids[fin] = vals
                lfrac = float((ids > 0).mean())
                if require_labels and lfrac < min_labelled:
                    skipped_unlabelled += 1
                    continue
                cnt = np.bincount(ids.ravel(), minlength=int(lut_ids.max()) + 1)
                unlabelled_px += int(cnt[0])
                for c in classes:
                    n_c = int(cnt[int(c["value"])]) if int(c["value"]) < len(cnt) else 0
                    c["pixels"] += n_c
                    c["patches"] += int(n_c > 0)
                if cnt[1:].sum():
                    top = int(np.argmax(cnt[1:]) + 1)
                    dominant = next((c["name"] for c in classes if int(c["value"]) == top), top)
                lab_out = ids.astype(label_dtype)
            fname = f"{stem}_r{i:04d}_c{j:04d}.tif"
            prof = {"driver": "GTiff", "width": pw, "height": ph, "count": len(band_names), "dtype": "float32" if is_float else out_dtype,
                    "crs": ref.crs, "transform": wt, "nodata": nodata_out, "compress": "deflate", "predictor": 3 if is_float else 2}
            with rasterio.open(out / "images" / fname, "w", **prof) as dst:
                dst.write(img)
                for b, nm in enumerate(band_names, start=1):
                    dst.set_band_description(b, nm)
            if lab_out is not None:
                extra = {"photometric": "palette"} if label_dtype == "uint8" else {}
                with rasterio.open(out / "labels" / fname, "w", driver="GTiff", width=pw, height=ph, count=1, dtype=label_dtype,
                                   crs=ref.crs, transform=wt, compress="deflate", **extra) as dst:
                    if label_dtype == "uint8":
                        dst.write_colormap(1, cmap_out)   # before the pixels, or GDAL can't set the palette tag
                    dst.set_band_description(1, "label")
                    dst.update_tags(classes=class_tag)
                    dst.write(lab_out, 1)
            x0, y0 = wt * (0, ph)
            x1, y1 = wt * (pw, 0)
            rows_csv.append({"file": fname, "row": i, "col": j, "x_min": round(x0, 3), "y_min": round(y0, 3), "x_max": round(x1, 3),
                             "y_max": round(y1, 3), "valid_fraction": round(vfrac, 4),
                             "labelled_fraction": None if lfrac is None else round(lfrac, 4), "dominant_class": dominant, "win": win})
            kept.append(len(rows_csv) - 1)
        if not rows_csv:
            raise ValueError("No patch passed the filters (valid pixels / labels). Lower the minimum shares or check the area.")

        progress.update(0.97, "Writing classes.txt, dataset.json and the patch index")
        import csv
        with open(out / "patches.csv", "w", newline="", encoding="utf-8") as f:
            cols = ["file", "row", "col", "x_min", "y_min", "x_max", "y_max", "valid_fraction", "labelled_fraction", "dominant_class"]
            wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(rows_csv)
        total_px = unlabelled_px + sum(c["pixels"] for c in classes)
        if gt:
            lines = ["# LULC Fetch · training data classes",
                     f"# Label patches (labels/*.tif) store the 'value' column. 0 = no label / ignore (not a class).",
                     f"# Ground truth: {Path(ground_truth.get('path', '')).name if ground_truth.get('type') == 'raster' else 'vector layer'}"
                     + (f" · field '{ground_truth.get('field')}'" if ground_truth.get("field") else ""),
                     "", "value\toriginal\tname\tcolor\tpixels\tpercent\tpatches",
                     f"0\t-\tno label / ignore\t#000000\t{unlabelled_px}\t{100 * unlabelled_px / max(total_px, 1):.2f}\t-"]
            for c in classes:
                lines.append(f"{c['value']}\t{c['original']}\t{c['name']}\t{c['color'] or ''}\t{c['pixels']}\t{100 * c['pixels'] / max(total_px, 1):.2f}\t{c['patches']}")
            (out / "classes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        meta = {
            "created": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": "LULC Fetch · Make training data", "name": stem,
            "images_dir": "images", "labels_dir": "labels" if gt else None, "patch_size_px": [pw, ph],
            "patch_size_m": [pw * g["pixel_size"][0], ph * g["pixel_size"][1]], "overlap_px": g["overlap_px"], "stride_px": g["stride_px"],
            "pixel_size": g["pixel_size"], "crs": ref.crs.to_string() if ref.crs else None, "edge_patches": edge,
            "bands": band_names, "band_count": len(band_names), "dtype": "float32" if is_float else out_dtype,
            "nodata": None if is_float else nodata_out, "image_nodata": "NaN" if is_float else nodata_out,
            "inputs": [{"file": Path(it["path"]).name, "bands": p["bands"], "scale": p["scale"], "offset": p["offset"]} for it, p in zip(inputs, plan_in)],
            "classes": [{k: v for k, v in c.items()} for c in classes], "ignore_value": 0 if gt else None, "num_classes": len(classes),
            "label_dtype": label_dtype if gt else None, "remapped": bool(remap) if gt else None,
            "count": len(rows_csv), "skipped": {"too_little_data": skipped_empty, "too_few_labels": skipped_unlabelled},
            "filters": {"min_valid_fraction": min_valid, "require_labels": require_labels, "min_labelled_fraction": min_labelled},
            "index": "patches.csv",
        }
        (out / "dataset.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
        # outlines of the written patches (map preview)
        step = max(1, math.ceil(len(rows_csv) / PREVIEW_CELLS))
        footprints = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": _cell_polygon(ref, r["win"]), "properties": {"file": r["file"], "row": r["row"], "col": r["col"], "dominant_class": r["dominant_class"]}}
            for k, r in enumerate(rows_csv) if k % step == 0]}
        report = {**{k: v for k, v in meta.items() if k not in ("inputs",)}, "folder": str(out), "seconds": round(time.time() - t0, 1),
                  "footprints": footprints, "grid_cells": len(cells),
                  "size_mb": round(sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6, 1)}
        log.info("Wrote %d patches (%d×%d px, %d bands) to %s in %.1f s", len(rows_csv), pw, ph, len(band_names), out, report["seconds"])
        return report
    except BaseException:
        if created:
            shutil.rmtree(out, ignore_errors=True)   # cancelled or failed: don't leave half a dataset behind
        raise
    finally:
        for v in vrts:
            v.close()
        for s in srcs:
            s.close()
        ref.close()
        if gt:
            gt.close()
