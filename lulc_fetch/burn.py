"""Burn severity (dNBR): what burned between two dates, and how badly, e.g. stubble (crop residue) burning.

    burn_severity(pre, post, out_dir, ...) -> {"paths": [dNBR.tif, severity.tif], "classes": [...], "summary": {...}}

NBR = (NIR − SWIR2) / (NIR + SWIR2) (Sentinel-2 B08 and B12; Landsat 8/9 SR_B5 and SR_B7). Each date is an image with
those bands (multiband imagery, band names or band map given), or an NBR raster already computed (one band).

dNBR = NBR before − NBR after: positive where vegetation or residue burned. The after image is put on the before
image's grid. Severity classes (USGS, Key & Benson 2006, on dNBR):
    < −0.25 high regrowth · −0.25…−0.1 low regrowth · −0.1…0.1 unburned · 0.1…0.27 low · 0.27…0.44 moderate-low ·
    0.44…0.66 moderate-high · ≥ 0.66 high severity
Optional: breaks of your own; and min_post_nbr — only pixels whose NBR after is below it can be burned (a field just
harvested also loses NBR, but its bare soil stays brighter in SWIR than char: for stubble burning a limit near 0.1
keeps harvests out).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

from . import progress, resample
from .analysis import _bands, detect_band_map, detect_scale

MAX_PIXELS = 120_000_000
USGS = [  # (upper limit of dNBR, value, name, colour)
    (-0.25, 1, "High post-fire regrowth", "#1a9850"),
    (-0.1, 2, "Low post-fire regrowth", "#91cf60"),
    (0.1, 3, "Unburned", "#d9d9d9"),
    (0.27, 4, "Low severity", "#fee08b"),
    (0.44, 5, "Moderate-low severity", "#fdae61"),
    (0.66, 6, "Moderate-high severity", "#f46d43"),
    (np.inf, 7, "High severity", "#a50026"),
]
BURNED = {4, 5, 6, 7}


def nbr(path, band_map: dict | None = None, scale: float | None = None, offset: float | None = None, band: int = 1):
    """The NBR of an image (B08 and B12) or the values of a one-band NBR raster: (open dataset, NBR array, how)."""
    src = rasterio.open(path)
    if src.width * src.height > MAX_PIXELS:
        src.close()
        raise ValueError(f"{Path(path).name} is too big ({src.width:,} × {src.height:,} pixels): clip it to your area first")
    bm = band_map or detect_band_map(src.descriptions, src.count)[0]
    if {"B08", "B12"} <= set(bm):
        if scale is None:
            _, scale, offset, _ = detect_scale(src, bm)
        b = _bands(src, bm, {"B08", "B12"}, scale, offset or 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            v = (b["B08"] - b["B12"]) / (b["B08"] + b["B12"])
        return src, np.where(np.isfinite(v), v, np.nan), "computed from NIR (B08) and SWIR2 (B12)"
    if src.count == 1 or band != 1:
        a = src.read(band, masked=True).astype("float64").filled(np.nan)
        fin = a[np.isfinite(a)]
        if fin.size and (np.nanpercentile(fin, 1) < -1.5 or np.nanpercentile(fin, 99) > 1.5):
            src.close()
            raise ValueError(f"{Path(path).name} has one band but its values aren't NBR (−1 to 1): give the image with its NIR and SWIR2 bands")
        return src, a, "an NBR raster"
    src.close()
    raise ValueError(f"{Path(path).name} has no NIR (B08) and SWIR2 (B12) bands (check its band names), and isn't a one-band NBR raster")


def _classes(breaks: list[float] | None):
    if not breaks:
        return USGS
    b = sorted(float(x) for x in breaks)
    if len(b) != 6:
        raise ValueError("Give 6 breaks (between the 7 classes), from regrowth to high severity")
    return [(lim, v, n, c) for lim, (_, v, n, c) in zip(b + [np.inf], USGS)]


def burn_severity(pre, post, out_dir: Path, *, pre_map: dict | None = None, post_map: dict | None = None,
                  pre_scale: tuple | None = None, post_scale: tuple | None = None, breaks: list[float] | None = None,
                  min_post_nbr: float | None = None, resampling: str | None = None, name: str | None = None) -> dict:
    progress.update(0.05, "NBR before")
    sa, a, how_a = nbr(pre, pre_map, *(pre_scale or (None, None)))
    try:
        progress.update(0.35, "NBR after")
        sb, b_raw, how_b = nbr(post, post_map, *(post_scale or (None, None)))
        with sb:
            b = np.full(a.shape, np.nan)
            reproject(b_raw, b, src_transform=sb.transform, src_crs=sb.crs, dst_transform=sa.transform, dst_crs=sa.crs,
                      src_nodata=np.nan, dst_nodata=np.nan, resampling=resample.get(resampling, Resampling.bilinear))
        progress.update(0.65, "dNBR and severity")
        d = a - b
        if min_post_nbr is not None:
            d = np.where(b < min_post_nbr, d, np.minimum(d, 0.0999))   # not dark enough after: at most "unburned"
        classes = _classes(breaks)
        sev = np.zeros(a.shape, "uint8")
        ok = np.isfinite(d)
        lower = -np.inf
        for lim, v, _, _ in classes:
            sev[ok & (d >= lower) & (d < lim)] = v
            lower = lim
        from .raster_ops import _pixel_metres
        dx, dy = _pixel_metres(sa, a.shape[0])
        px_ha = np.broadcast_to(np.asarray(dx * dy, dtype=float), a.shape) / 1e4   # per row for rasters in degrees
        total_ha = float(px_ha[ok].sum())
        rows = []
        for lim, v, n, c in classes:
            m = sev == v
            ha = float(px_ha[m].sum())
            rows.append({"value": v, "class": n, "pixels": int(m.sum()), "area_ha": round(ha, 3),
                         "pct": round(100 * ha / total_ha, 2) if total_ha else 0, "color": c})
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = name or f"{Path(pre).stem}_to_{Path(post).stem}"[:70]
        prof = sa.profile.copy()
        prof.update(driver="GTiff", count=1, dtype="float32", nodata=-9999.0, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
        prof.pop("photometric", None)
        p1 = out_dir / f"{stem}_dNBR.tif"
        with rasterio.open(p1, "w", **prof) as o:
            o.write(np.where(ok, d, -9999.0).astype("float32"), 1)
            o.set_band_description(1, "dNBR")
            o.update_tags(index="dNBR", formula="NBR before − NBR after")
        prof.update(dtype="uint8", nodata=0)
        p2 = out_dir / f"{stem}_burn_severity.tif"
        prof.update(photometric="palette")
        with rasterio.open(p2, "w", **prof) as o:   # the colour table before the pixels (GDAL can't change it after)
            o.write_colormap(1, {0: (0, 0, 0, 0), **{v: tuple(int(c[k:k + 2], 16) for k in (1, 3, 5)) + (255,) for _, v, _, c in classes}})
            o.write(sev, 1)
            o.update_tags(classes=json.dumps({v: n for _, v, n, _ in classes}))
            o.set_band_description(1, "Burn severity")
        burned = [r for r in rows if r["value"] in BURNED]
        progress.update(1, "Done")
        return {"paths": [str(p1), str(p2)], "classes": rows,   # the severity map last: it lands on top of Contents
                "summary": {"burned_ha": round(sum(r["area_ha"] for r in burned), 3), "burned_pct": round(sum(r["pct"] for r in burned), 2),
                            "mean_dnbr": round(float(np.nanmean(d)), 4) if ok.any() else None, "pre": how_a, "post": how_b,
                            "scheme": "custom" if breaks else "USGS (Key & Benson 2006)", "min_post_nbr": min_post_nbr}}
    finally:
        sa.close()
