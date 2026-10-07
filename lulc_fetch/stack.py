"""Stack layers: put bands from several rasters (and computed indices) onto one grid as a single GeoTIFF.

Typical use: Sentinel-2 bands + Sentinel-1 VV/VH + NDVI + elevation → one multiband image for
Raster → table and model training. Every source is reprojected/resampled on the fly (WarpedVRT) onto
the reference layer's grid, strip by strip, so memory stays bounded.
"""

from __future__ import annotations

import logging
import math
import re
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling

from . import resample
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from . import progress
from .analysis import _formula, _palette, clip_region
from .indices import evaluate, required_bands

log = logging.getLogger(__name__)


def stack(items: list[dict], ref_path: str | Path, out_path: str | Path, *, clip: dict | None = None,
          factor: int = 1, rows_per_strip: int = 256, resampling: str | None = None) -> dict:
    """items: [{"path", "name", "bands": [ints] | None, "index"/"formula" (+ "band_map", "scale", "offset")}].

    Index / formula items contribute one band each (values computed per pixel). Class maps (paletted bands)
    are resampled with nearest neighbour, everything else bilinearly.
    """
    t0 = time.time()
    if not items:
        raise ValueError("Choose at least one layer to stack")
    with rasterio.open(ref_path) as ref:
        win, geom = clip_region(ref, clip)
        win = win or Window(0, 0, ref.width, ref.height)
        f = max(1, int(factor))
        out_w, out_h = max(1, math.ceil(win.width / f)), max(1, math.ceil(win.height / f))
        base = ref.window_transform(win)
        transform = base * base.scale(win.width / out_w, win.height / out_h)
        crs = ref.crs

    # plan the output bands
    plan, names = [], []
    for it in items:
        with rasterio.open(it["path"]) as src:
            if it.get("index") or it.get("formula"):
                name, expr = _formula(it.get("index"), it.get("formula"))
                need = sorted(required_bands(expr))
                bmap = it.get("band_map") or {}
                missing = [b for b in need if b not in bmap]
                if missing:
                    raise ValueError(f"{it['name']}: band(s) {', '.join(missing)} not assigned")
                plan.append({"kind": "formula", "path": it["path"], "expr": expr, "need": need,
                             "idx": [int(bmap[b]) for b in need], "scale": float(it.get("scale", 1)),
                             "offset": float(it.get("offset", 0)), "resampling": resample.get(resampling, Resampling.bilinear)})
                names.append([name if it.get("index") else re.sub(r"[^A-Za-z0-9_]+", "_", it["name"])[:30]])
            else:
                bands = it.get("bands") or list(range(1, src.count + 1))
                for b in bands:
                    if not 1 <= b <= src.count:
                        raise ValueError(f"{it['name']} has no band {b}")
                classes = all(_palette(src, b) for b in bands)
                plan.append({"kind": "bands", "path": it["path"], "idx": bands,
                             "resampling": Resampling.nearest if classes else resample.get(resampling, Resampling.bilinear)})
                layer = re.sub(r"[^A-Za-z0-9]+", "_", it["name"]).strip("_")[:24] or "layer"
                generic = lambda d: not d or re.fullmatch(r"(band_?\d*|data|image_?\d*)", d, re.I)
                names.append([(layer if len(bands) == 1 else f"{layer}_{b}") if generic(src.descriptions[b - 1])
                              else src.descriptions[b - 1] for b in bands])

    # band names: keep originals (e.g. B04, VV, NDVI) when unique, so band detection and indices still work
    flat = [n for group in names for n in group]
    seen, final = {}, []
    for (it, group) in zip(items, names):
        for n in group:
            nm = n if flat.count(n) == 1 else f"{re.sub(r'[^A-Za-z0-9]+', '_', it['name'])[:20]}_{n}"
            while nm in seen:
                nm += "_2"
            seen[nm] = True
            final.append(nm)
    count = len(final)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile = {"driver": "GTiff", "width": out_w, "height": out_h, "count": count, "dtype": "float32", "crs": crs,
               "transform": transform, "nodata": np.nan, "compress": "deflate", "predictor": 3, "BIGTIFF": "IF_SAFER"}
    if out_w >= 256 and out_h >= 256:
        profile.update(tiled=True, blockxsize=256, blockysize=256)
    log.info("Stacking %d bands from %d layers onto %d×%d px (%s)", count, len(items), out_w, out_h, crs.to_string())

    srcs = [rasterio.open(p["path"]) for p in plan]
    vrts = [WarpedVRT(s, crs=crs, transform=transform, width=out_w, height=out_h, resampling=p["resampling"],
                      src_nodata=s.nodata, nodata=s.nodata) for s, p in zip(srcs, plan)]
    try:
        with rasterio.open(out_path, "w", **profile) as dst:
            for r0 in range(0, out_h, rows_per_strip):
                progress.update(r0 / out_h, f"Stacking ({r0 / out_h:.0%})")
                rows = min(rows_per_strip, out_h - r0)
                w = Window(0, r0, out_w, rows)
                layers = []
                for p, vrt in zip(plan, vrts):
                    data = vrt.read(p["idx"], window=w, masked=True).astype("float32").filled(np.nan)
                    if p["kind"] == "formula":
                        vals = {b: data[i] * p["scale"] + p["offset"] for i, b in enumerate(p["need"])}
                        layers.append(evaluate(p["expr"], vals)[None])
                    else:
                        layers.append(data)
                block = np.concatenate(layers)
                if geom is not None:
                    from rasterio.features import geometry_mask
                    inside = geometry_mask([geom], out_shape=(rows, out_w), transform=dst.window_transform(w), invert=True)
                    block[:, ~inside] = np.nan
                dst.write(block, window=w)
            for i, n in enumerate(final, start=1):
                dst.set_band_description(i, n)
            dst.update_tags(stacked_from=",".join(Path(p["path"]).name for p in plan))
    finally:
        for v in vrts:
            v.close()
        for s in srcs:
            s.close()
    report = {"path": str(out_path), "bands": final, "width": out_w, "height": out_h, "crs": crs.to_string(),
              "pixel_size": [abs(transform.a), abs(transform.e)], "seconds": round(time.time() - t0, 1)}
    log.info("Stacked %d bands in %.1f s", count, report["seconds"])
    return report
