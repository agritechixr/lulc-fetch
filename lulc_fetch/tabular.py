"""Raster → table: one row per pixel (at the image's own resolution, or aggregated), one column per band,
plus optional coordinates and an optional ground-truth label as the last column.

Works for any raster: Sentinel-2 / Landsat / multispectral / hyperspectral (any number of bands) / SAR.
Ground truth can be a raster (resampled with nearest neighbour onto the image grid) or vector features
(polygons / points rasterised from an attribute). The image is processed strip by strip and rows are
streamed to CSV or Parquet, so memory stays bounded for large images.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject, transform_geom
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window

from . import progress
from .analysis import clip_region

log = logging.getLogger(__name__)


def _column_names(names: list[str]) -> list[str]:
    """Safe, unique column names from band descriptions."""
    out, seen = [], set()
    for n in names:
        base = re.sub(r"[^A-Za-z0-9_]+", "_", str(n)).strip("_") or "band"
        if base[0].isdigit():
            base = "b" + base
        name, i = base, 2
        while name.lower() in seen:
            name, i = f"{base}_{i}", i + 1
        seen.add(name.lower())
        out.append(name)
    return out


class _GroundTruth:
    """Labels on the output grid, strip by strip. Codes: 0 = unlabelled; class names kept in `names`."""

    def __init__(self, spec: dict, crs, label_name: str):
        self.kind = spec["type"]
        self.label_name = label_name
        self.names: dict[int, str] | None = None   # code -> class name (text attributes)
        self.numeric = True
        if self.kind == "raster":
            self.src = rasterio.open(spec["path"])
            self.band = int(spec.get("band", 1))
            if not 1 <= self.band <= self.src.count:
                raise ValueError(f"The ground-truth raster has no band {self.band}")
            self.numeric = True
            self.nodata = self.src.nodata
        elif self.kind == "vector":
            field = spec.get("field")
            feats = [f for f in spec["geojson"].get("features", []) if f.get("geometry")]
            if not feats:
                raise ValueError("The ground-truth layer has no features")
            raw = [(f.get("properties") or {}).get(field) if field else 1 for f in feats]
            if field and all(v is None for v in raw):
                raise ValueError(f"No feature has a value for the attribute '{field}'")
            numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw if v is not None)
            if numeric:
                values = [float(v) if v is not None else None for v in raw]
            else:  # text classes → codes 1..K, names kept for the label column
                classes = sorted({str(v) for v in raw if v is not None})
                self.names = {i + 1: c for i, c in enumerate(classes)}
                code = {c: i for i, c in self.names.items()}
                values = [code[str(v)] if v is not None else None for v in raw]
            self.numeric = numeric
            self.shapes = [(transform_geom("EPSG:4326", crs, f["geometry"]), v)
                           for f, v in zip(feats, values) if v is not None]
            # one id per feature, so the trainer can keep whole polygons in either train or test
            self.id_shapes = [(g, i + 1) for i, (g, _) in enumerate(self.shapes)]
            if numeric and any(v == 0 for _, v in self.shapes):
                self.zero_label = True   # 0 is a real class: shift internally so 0 can mean "unlabelled"
                self.shapes = [(g, v + 1) for g, v in self.shapes]
            else:
                self.zero_label = False
        else:
            raise ValueError(f"Unknown ground-truth type {self.kind}")

    def strip(self, transform, shape, crs) -> np.ndarray:
        """Label values for one strip: float array, NaN = unlabelled."""
        if self.kind == "raster":
            dst = np.full(shape, np.nan, "float32")
            src_nodata = self.nodata if self.nodata is not None else None
            reproject(rasterio.band(self.src, self.band), dst, dst_transform=transform, dst_crs=crs,
                      resampling=Resampling.nearest, src_nodata=src_nodata, dst_nodata=np.nan)
            if src_nodata is None:   # treat 0 as unlabelled in class maps without nodata (e.g. WorldCover)
                dst[dst == 0] = np.nan
            return dst
        burned = rasterize(self.shapes, out_shape=shape, transform=transform, fill=0, dtype="float64")
        out = burned.astype("float64")
        out[burned == 0] = np.nan
        if self.zero_label:
            out -= 1
        return out

    def strip_ids(self, transform, shape) -> np.ndarray | None:
        """Feature (polygon / point) id per pixel for vector ground truth; 0 = none."""
        if self.kind != "vector":
            return None
        return rasterize(self.id_shapes, out_shape=shape, transform=transform, fill=0, dtype="int32")

    def label_columns(self, codes: np.ndarray) -> list[tuple[str, np.ndarray]]:
        if self.names:  # text classes: name column + numeric code column
            names = np.array([self.names.get(int(c), "") for c in codes], dtype=object)
            return [(f"{self.label_name}_code", codes.astype("int64")), (self.label_name, names)]
        if np.all(np.mod(codes, 1) == 0):
            return [(self.label_name, codes.astype("int64"))]
        return [(self.label_name, codes)]

    def close(self):
        if self.kind == "raster":
            self.src.close()


def raster_to_table(path: str | Path, out_path: str | Path, *, bands: list[int] | None = None,
                    clip: dict | None = None, factor: int = 1, scale: float = 1.0, offset: float = 0.0,
                    ground_truth: dict | None = None, label_name: str = "label", labelled_only: bool = True,
                    sampling: str = "all", sample_size: int = 100_000, per_class: int = 5_000,
                    xy: bool = True, lonlat: bool = True, rowcol: bool = False, drop_nodata: bool = True,
                    fmt: str = "csv", seed: int = 0, rows_per_strip: int = 256, preview_rows: int = 12,
                    sample_ids: bool = True, class_colors: dict | None = None) -> dict:
    """Write the table and return a report (rows, columns, class counts, preview)."""
    t0 = time.time()
    rng = np.random.default_rng(seed)
    label_name = re.sub(r"[^A-Za-z0-9_]+", "_", label_name).strip("_") or "label"
    if sampling == "stratified" and not ground_truth:
        raise ValueError("Stratified sampling needs ground truth")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(path) as src:
        bands = bands or list(range(1, src.count + 1))
        for b in bands:
            if not 1 <= b <= src.count:
                raise ValueError(f"This file has no band {b}")
        band_cols = _column_names([src.descriptions[b - 1] or f"band_{b}" for b in bands])
        win, geom = clip_region(src, clip)
        win = win or Window(0, 0, src.width, src.height)
        f = max(1, int(factor))
        out_w, out_h = max(1, math.ceil(win.width / f)), max(1, math.ceil(win.height / f))
        base = src.window_transform(win)
        transform = base * base.scale(win.width / out_w, win.height / out_h)
        res = (abs(transform.a), abs(transform.e))
        gt = _GroundTruth(ground_truth, src.crs, label_name) if ground_truth else None
        crs_str = src.crs.to_string()
        log.info("Raster → table: %d bands, %d×%d px at %.4g m%s%s", len(bands), out_w, out_h, res[0],
                 f" (aggregated {f}×{f})" if f > 1 else " (native resolution)",
                 f", ground truth from {gt.kind}" if gt else "")

        def strips():
            for r0 in range(0, out_h, rows_per_strip):
                rows = min(rows_per_strip, out_h - r0)
                src_win = Window(win.col_off, win.row_off + r0 * f, win.width, min(rows * f, win.height - r0 * f))
                st = transform * transform.translation(0, r0)
                data = src.read(bands, window=src_win, out_shape=(len(bands), rows, out_w), masked=True,
                                resampling=Resampling.average if f > 1 else Resampling.nearest)
                data = data.astype("float32").filled(np.nan) * scale + offset
                inside = np.ones((rows, out_w), bool)
                if geom is not None:
                    from rasterio.features import geometry_mask
                    inside = geometry_mask([geom], out_shape=(rows, out_w), transform=st, invert=True)
                labels = gt.strip(st, (rows, out_w), src.crs) if gt else None
                yield r0, rows, st, data, inside, labels

        # ---- pass 1 (only when needed): count pixels / classes to set sampling probabilities
        keep_p, class_p = 1.0, None
        if sampling in ("random", "stratified"):
            progress.update(0.0, "Counting pixels for sampling")
            total, counts = 0, {}
            for r0, rows, st, data, inside, labels in strips():
                progress.update(0.3 * (r0 + rows) / out_h, f"Counting pixels ({(r0 + rows) / out_h:.0%})")
                ok = inside & (np.all(np.isfinite(data), axis=0) if drop_nodata else True)
                if gt is not None and (labelled_only or sampling == "stratified"):
                    ok &= np.isfinite(labels)
                total += int(ok.sum())
                if sampling == "stratified":
                    v, c = np.unique(labels[ok], return_counts=True)
                    for vi, ci in zip(v, c):
                        counts[float(vi)] = counts.get(float(vi), 0) + int(ci)
            if sampling == "random":
                keep_p = min(1.0, sample_size / max(total, 1))
            else:
                class_p = {k: min(1.0, per_class / n) for k, n in counts.items()}
            span_start = 0.3
        else:
            span_start = 0.0

        # ---- pass 2: build rows and stream them out
        columns = (["x", "y"] if xy else []) + (["lon", "lat"] if lonlat else []) + (["row", "col"] if rowcol else []) + band_cols
        writer, csv_file, written, preview, class_counts, label_cols = None, None, 0, [], {}, None
        try:
            for r0, rows, st, data, inside, labels in strips():
                progress.update(span_start + (1 - span_start) * r0 / out_h,
                                f"Writing rows ({r0 / out_h:.0%} of the image, {written:,} rows so far)")
                ok = inside.copy()
                if drop_nodata:
                    ok &= np.all(np.isfinite(data), axis=0)
                if gt is not None and labelled_only:
                    ok &= np.isfinite(labels)
                if sampling == "random" and keep_p < 1:
                    ok &= rng.random(ok.shape) < keep_p
                elif sampling == "stratified":
                    lab = np.nan_to_num(labels, nan=-1e30)
                    p = np.zeros(ok.shape)
                    for k, pk in class_p.items():
                        p[lab == k] = pk
                    ok &= rng.random(ok.shape) < p
                rr, cc = np.nonzero(ok)
                if not len(rr):
                    continue
                cols: list[tuple[str, np.ndarray]] = []
                if xy or lonlat:
                    xs, ys = rasterio.transform.xy(st, rr, cc)
                    xs, ys = np.asarray(xs), np.asarray(ys)
                    if xy:
                        cols += [("x", xs), ("y", ys)]
                    if lonlat:
                        lon, lat = warp_transform(src.crs, "EPSG:4326", xs, ys)
                        cols += [("lon", np.asarray(lon)), ("lat", np.asarray(lat))]
                if rowcol:  # pixel position in the full image
                    cols += [("row", (rr * f + r0 * f + win.row_off).astype("int64")),
                             ("col", (cc * f + win.col_off).astype("int64"))]
                cols += [(n, data[i][rr, cc]) for i, n in enumerate(band_cols)]
                if gt is not None and gt.kind == "vector" and sample_ids:
                    ids = gt.strip_ids(st, (rows, out_w))
                    cols.append(("poly_id", ids[rr, cc].astype("int64")))
                if gt is not None:
                    lab_vals = labels[rr, cc]
                    lcols = gt.label_columns(np.nan_to_num(lab_vals, nan=-1))
                    lcols = [(n, np.where(np.isfinite(lab_vals), v, None) if v.dtype == object else
                              (np.where(np.isfinite(lab_vals), v, -1) if v.dtype.kind == "i" else v)) for n, v in lcols]
                    cols += lcols
                    if label_cols is None:
                        label_cols = [n for n, _ in lcols]
                    key = lcols[-1][1]
                    for v, c in zip(*np.unique(key.astype(str), return_counts=True)):
                        class_counts[str(v)] = class_counts.get(str(v), 0) + int(c)
                names = [n for n, _ in cols]
                if fmt == "parquet":
                    import pyarrow as pa
                    import pyarrow.parquet as pq
                    table = pa.table({n: v for n, v in cols})
                    if writer is None:
                        writer = pq.ParquetWriter(out_path, table.schema, compression="zstd")
                    writer.write_table(table)
                else:
                    if csv_file is None:
                        csv_file = open(out_path, "w", encoding="utf-8", newline="")
                        csv_file.write(",".join(names) + "\n")
                    _write_csv(csv_file, cols)
                if len(preview) < preview_rows:
                    take = min(preview_rows - len(preview), len(rr))
                    preview += [[_cell(v[i]) for _, v in cols] for i in range(take)]
                    columns = names
                written += len(rr)
        finally:
            if writer is not None:
                writer.close()
            if csv_file is not None:
                csv_file.close()
            if gt is not None:
                gt.close()

    if written == 0:
        out_path.unlink(missing_ok=True)
        raise ValueError("No rows: the area has no valid pixels" + (" with ground truth" if ground_truth and labelled_only else ""))
    report = {
        "path": str(out_path), "name": out_path.name, "format": fmt, "rows": written, "columns": columns,
        "band_columns": band_cols, "band_indices": bands, "scale": scale, "offset": offset,
        "label_columns": label_cols or [], "target": (label_cols or [None])[-1],
        "group_column": "poly_id" if gt is not None and gt.kind == "vector" and sample_ids else None,
        "class_colors": class_colors or None,
        "class_counts": dict(sorted(class_counts.items(), key=lambda kv: -kv[1])),
        "classes": gt.names if gt and gt.names else None, "crs": crs_str,
        "pixel_size": list(res), "grid": [out_w, out_h], "factor": f, "sampling": sampling,
        "size_mb": out_path.stat().st_size / 1e6, "seconds": round(time.time() - t0, 1), "preview": preview,
        "source": str(path),
    }
    out_path.with_suffix(out_path.suffix + ".json").write_text(json.dumps({k: v for k, v in report.items() if k != "preview"}, indent=1))
    log.info("Wrote %s rows × %d columns to %s (%.1f MB) in %.1f s", f"{written:,}", len(columns), out_path.name,
             report["size_mb"], report["seconds"])
    return report


def _cell(v):
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else float(f"{float(v):.6g}")
    if isinstance(v, (np.integer, int)):
        return int(v)
    return str(v)


def _write_csv(fh, cols):
    """Fast column-wise CSV writing (numbers with 7 significant digits; text quoted when needed)."""
    parts = []
    for _, v in cols:
        if v.dtype == object:
            s = np.array(["" if x is None else ('"' + str(x).replace('"', '""') + '"' if any(ch in str(x) for ch in ',"\n') else str(x)) for x in v], dtype=object)
        elif v.dtype.kind in "iu":
            s = v.astype(str)
        else:
            s = np.char.mod("%.7g", v.astype("float64"))
            s = np.where(np.isfinite(v), s, "")
        parts.append(np.asarray(s, dtype=object))
    for row in zip(*parts):
        fh.write(",".join(row) + "\n")
