"""Relative surface soil moisture from a Sentinel-1 time series by change detection (after Wagner et al. 1999, TU Wien;
the method behind Copernicus' SSM product):

    m(t) = (σ⁰(t) − σ⁰_dry) / (σ⁰_wet − σ⁰_dry) × 100 %

per pixel, σ⁰ in dB from one orbit track, σ⁰_dry / σ⁰_wet its low / high percentile over the series (the driest and
wettest the ground was seen). Bare and lightly vegetated soil responds by 3–6 dB between dry and wet; the index is
masked where it can't be trusted: open water, little dynamic range (towns, rock, stable surfaces) and dense
vegetation (the radar sees the canopy, not the soil). It is relative (0 = the driest, 100 = the wettest in the
series), not m³/m³; more dates (a year, 30 or more) give steadier dry / wet references.

Checked on 16 dates of the 2025 monsoon (Sentinel-1 RTC, near Gurgaon): the area's mean follows ERA5-Land's 0–7 cm
soil moisture (Pearson r 0.63) and the previous three days' rain (Spearman 0.91). The canopy mask (VH − VV above
−5 dB) was set there too: median −5.1 dB under trees, −6.3 dB over cropland.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import rasterio

from .. import progress
from . import analysis as AN

QUALITY = {0: "Valid", 1: "Open water", 2: "Little sensitivity (town, rock, stable)", 3: "Dense vegetation", 4: "Too few dates"}


def _db(x):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10 * np.log10(np.where(x > 0, x, np.nan))


def change_detection(stack_db: np.ndarray, *, cross_db: np.ndarray | None = None, lo: float = 5, hi: float = 95,
                     min_range_db: float = 3.0, water_db: float = -18.0, veg_ratio_db: float = -5.0, min_dates: int = 4):
    """stack_db: dates × rows × cols of co-polarised (VV) dB; cross_db: the same of VH (for the vegetation mask).
    Returns (soil moisture % per date, dry reference, wet reference, sensitivity dB, quality)."""
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        n = np.isfinite(stack_db).sum(0)
        dry = np.nanpercentile(stack_db, lo, axis=0)
        wet = np.nanpercentile(stack_db, hi, axis=0)
        sens = wet - dry
        sm = np.clip((stack_db - dry) / sens, 0, 1) * 100
        mean = np.nanmean(stack_db, 0)
        q = np.zeros(mean.shape, "uint8")
        q[sens < min_range_db] = 2
        if cross_db is not None:
            q[(np.nanmean(cross_db, 0) - mean) > veg_ratio_db] = 3   # VH close to VV: volume scattering of a canopy
        q[mean < water_db] = 1
        q[n < min_dates] = 4
    sm = np.where(q[None] == 0, sm, np.nan).astype("float32")
    return sm, dry.astype("float32"), wet.astype("float32"), sens.astype("float32"), q


def run(paths: list[str], out_dir: Path, *, pol: str = "VV", lo: float = 5, hi: float = 95, min_range_db: float = 3.0,
        water_db: float = -18.0, veg_ratio_db: float = -5.0, aoi: dict | None = None, name: str = "soil_moisture") -> dict:
    """From SAR layers of one track (several dates) to relative soil moisture per date, with the references and a
    quality layer; the area's mean per date (valid pixels) as a series."""
    if len(paths) < 4:
        raise ValueError("Soil moisture by change detection needs at least 4 dates of one orbit track (30 or more is better)")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    first, _, grid = AN.read_pols(paths[0])
    with rasterio.open(paths[0]) as s:
        grid = {"crs": s.crs, "transform": s.transform, "shape": (s.height, s.width)}
        prof = s.profile.copy()
    co = pol.upper()
    cr = {"VV": "VH", "HH": "HV"}.get(co)
    rows, cross, dates = [], [], []
    for k, p in enumerate(paths):
        progress.update(0.05 + 0.5 * k / len(paths), f"Reading date {k + 1} of {len(paths)}")
        d, _, _ = AN.read_pols(p, like=grid)
        if co not in d:
            raise ValueError(f"{Path(p).name} has no {co} band")
        rows.append(_db(d[co]))
        cross.append(_db(d[cr]) if cr in d else None)
        dates.append(AN._date_of(p))
    order = sorted(range(len(paths)), key=lambda i: (dates[i] is None, dates[i]))
    rows, cross, dates = [rows[i] for i in order], [cross[i] for i in order], [dates[i] for i in order]
    if any(d is None for d in dates):
        raise ValueError("Each layer needs its date (in its name, e.g. …_2026-09-29…, or its tags)")
    if len({d for d in dates}) < len(dates):
        raise ValueError("Two layers have the same date: give one per date")
    S = np.stack(rows)
    C = np.stack(cross) if all(c is not None for c in cross) else None
    progress.update(0.6, "Change detection")
    sm, dry, wet, sens, q = change_detection(S, cross_db=C, lo=lo, hi=hi, min_range_db=min_range_db, water_db=water_db, veg_ratio_db=veg_ratio_db)
    if aoi:
        from .pipeline import _clip_mask
        m = _clip_mask(grid, aoi)
        sm = np.where(m[None], sm, np.nan)
        q = np.where(m, q, 255).astype("uint8")
    base = {k: prof[k] for k in ("width", "height", "crs", "transform")}
    base.update(driver="GTiff", compress="deflate")
    if grid["shape"][0] >= 256 and grid["shape"][1] >= 256:
        base.update(tiled=True, blockxsize=256, blockysize=256)
    ds = [d.strftime("%Y-%m-%d") for d in dates]
    p1 = out_dir / f"{name}.tif"
    with rasterio.open(p1, "w", **base, count=len(ds), dtype="float32", nodata=np.nan) as d:
        d.write(sm)
        for i, dt in enumerate(ds, 1):
            d.set_band_description(i, f"Soil moisture {dt} (%)")
        d.update_tags(dates=json.dumps(ds), units="relative soil moisture, % (0 = driest, 100 = wettest of the series)", method="change detection (Wagner 1999)")
    p2 = out_dir / f"{name}_references.tif"
    with rasterio.open(p2, "w", **base, count=3, dtype="float32", nodata=np.nan) as d:
        for i, (a, n) in enumerate(((dry, f"Dry reference {co} (dB, p{lo:g})"), (wet, f"Wet reference {co} (dB, p{hi:g})"), (sens, "Sensitivity (dB)")), 1):
            d.write(a, i)
            d.set_band_description(i, n)
    p3 = out_dir / f"{name}_quality.tif"
    with rasterio.open(p3, "w", **base, count=1, dtype="uint8", nodata=255, photometric="palette") as d:
        d.write_colormap(1, {0: (120, 200, 120, 80), 1: (33, 113, 181, 255), 2: (150, 150, 150, 255), 3: (0, 100, 0, 255), 4: (255, 255, 255, 0)})
        d.write(q, 1)
        d.update_tags(classes=json.dumps(QUALITY))
        d.set_band_description(1, "Soil-moisture quality")
    inside = q != 255
    qc = {QUALITY[i]: round(100 * float((q[inside] == i).mean()), 1) for i in QUALITY if (q[inside] == i).any()}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        series = [{"date": dt, "mean": round(float(np.nanmean(sm[i])), 1) if np.isfinite(sm[i]).any() else None,
                   "p25": round(float(np.nanpercentile(sm[i], 25)), 1) if np.isfinite(sm[i]).any() else None,
                   "p75": round(float(np.nanpercentile(sm[i], 75)), 1) if np.isfinite(sm[i]).any() else None} for i, dt in enumerate(ds)]
        med_sens = float(np.nanmedian(sens[q == 0])) if (q == 0).any() else None
    notes = []
    if len(ds) < 10:
        notes.append(f"Only {len(ds)} dates: the dry and wet references may not be the real extremes (30 or more, over a year, is better)")
    return {"outputs": [str(p1), str(p2), str(p3)], "dates": ds, "series": series, "quality_pct": qc,
            "median_sensitivity_db": round(med_sens, 2) if med_sens is not None else None, "notes": notes}
