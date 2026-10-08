"""SAR analysis on processed backscatter (GeoTIFFs with VV / VH bands, linear power or dB):

    features(path, out, which)          VV/VH ratio, cross ratio, RVI, normalised difference, span, local texture
    temporal(paths, out, stats)         per-pixel statistics of a stack of dates (mean / median in linear power, then
                                        dB), min, max, std, count, and the trend (dB per year)
    change(before, after, out_dir)      log-ratio (dB), increase / decrease classes (± threshold), and flooding:
                                        new open water (VV after below the water threshold and a drop)
Averages are taken in linear power (the physical quantity), then converted to dB when the input was dB.
"""

from __future__ import annotations

import json
import re
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from scipy.ndimage import uniform_filter

FEATURES = ("ratio", "cross_ratio", "rvi", "ndpi", "span", "texture")


def read_pols(path, like=None) -> tuple[dict, bool, dict]:
    """{"VV": linear power, "VH": …} of a SAR raster (dB bands converted to linear), whether it was dB, and its grid
    (or put onto like's grid)."""
    with rasterio.open(path) as s:
        names = [(d or f"band{i + 1}").upper() for i, d in enumerate(s.descriptions)]
        units = (s.tags().get("units") or "").lower()
        grid = like or {"crs": s.crs, "transform": s.transform, "shape": (s.height, s.width), "profile": s.profile.copy()}
        out, was_db = {}, False
        for i, n in enumerate(names, start=1):
            m = re.fullmatch(r"(VV|VH|HH|HV)(_?(DB|LIN|LINEAR))?", n)
            if not m:
                continue
            a = s.read(i, masked=True).astype("float64").filled(np.nan)
            if like is not None and (s.crs, s.transform, a.shape) != (like["crs"], like["transform"], like["shape"]):
                b = np.full(like["shape"], np.nan)
                reproject(a, b, src_transform=s.transform, src_crs=s.crs, dst_transform=like["transform"], dst_crs=like["crs"],
                          src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
                a = b
            v = a[np.isfinite(a)]
            db = (m.group(3) == "DB") or "db" in units or (v.size and np.nanmedian(v) < 0 and np.nanmin(v) > -80 and m.group(3) not in ("LIN", "LINEAR"))
            if m.group(1) in out:
                continue
            out[m.group(1)] = 10 ** (a / 10) if db else a
            was_db = was_db or bool(db)
    if not out:
        raise ValueError(f"{Path(path).name} has no VV / VH (or HH / HV) bands")
    return out, was_db, grid


def _db(x):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10 * np.log10(np.where(x > 0, x, np.nan))


def _write(grid, out: Path, bands: list, names: list, tags: dict, dtype="float32") -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    prof = {"driver": "GTiff", "width": grid["shape"][1], "height": grid["shape"][0], "count": len(bands), "dtype": dtype, "crs": grid["crs"],
            "transform": grid["transform"], "nodata": np.nan if dtype.startswith("float") else 0, "compress": "deflate", "BIGTIFF": "IF_SAFER"}
    with rasterio.open(out, "w", **prof) as d:
        for i, (b, n) in enumerate(zip(bands, names), start=1):
            d.write(b.astype(dtype), i)
            d.set_band_description(i, n)
        d.update_tags(**{k: (v if isinstance(v, str) else json.dumps(v)) for k, v in tags.items()})
    return str(out)


def features(path, out: Path, which=("ratio", "rvi", "ndpi"), texture_band: str = "VV", window: int = 7) -> dict:
    """Polarimetric features of a dual-pol image, and local texture (mean, std, coefficient of variation, entropy)."""
    p, was_db, grid = read_pols(path)
    co, cr = ("VV", "VH") if "VV" in p else ("HH", "HV")
    if co not in p or cr not in p:
        if set(which) - {"texture"}:
            raise ValueError("Polarisation features need two polarisations (VV + VH, or HH + HV)")
    bands, names = [], []
    a, b = p.get(co), p.get(cr)
    with np.errstate(divide="ignore", invalid="ignore"):
        if "ratio" in which:
            bands.append(_db(a / b)); names.append(f"{co}/{cr} (dB)")          # = co dB − cross dB
        if "cross_ratio" in which:
            bands.append(b / a); names.append(f"{cr}/{co}")
        if "rvi" in which:
            bands.append(4 * b / (a + b)); names.append("RVI")                 # dual-pol radar vegetation index
        if "ndpi" in which:
            bands.append((a - b) / (a + b)); names.append("NDPI")             # normalised difference polarisation index
        if "span" in which:
            bands.append(_db(a + b)); names.append("Span (dB)")
    if "texture" in which:
        x = _db(p.get(texture_band.upper()) if texture_band.upper() in p else a)
        ok = np.isfinite(x)
        xv = np.where(ok, x, 0)
        n = uniform_filter(ok.astype(float), window)
        with np.errstate(invalid="ignore", divide="ignore"):
            m = uniform_filter(xv, window) / n
            sd = np.sqrt(np.maximum(uniform_filter(xv * xv, window) / n - m * m, 0))
            lin = np.where(ok, 10 ** (x / 10), 0)
            ml = uniform_filter(lin, window) / n
            cv = np.sqrt(np.maximum(uniform_filter(lin * lin, window) / n - ml * ml, 0)) / ml
        lo, hi = np.nanpercentile(x, [1, 99])
        q = np.clip(((x - lo) / max(hi - lo, 1e-9) * 16).astype(int), 0, 15)
        ent = np.zeros(x.shape)
        for k in range(16):   # entropy of the window's 16-level histogram
            pk = uniform_filter(((q == k) & ok).astype(float), window) / np.maximum(n, 1e-9)
            ent -= np.where(pk > 0, pk * np.log2(np.where(pk > 0, pk, 1)), 0)
        for arr, nm in ((m, "mean (dB)"), (sd, "std (dB)"), (cv, "coefficient of variation"), (ent, "entropy")):
            bands.append(np.where(ok, arr, np.nan)); names.append(f"{texture_band.upper()} texture {nm}")
    if not bands:
        raise ValueError("Choose at least one feature")
    pth = _write(grid, out, bands, names, {"sar_features": ",".join(which), "source": Path(path).name})
    return {"path": pth, "bands": names}


def _date_of(path) -> datetime | None:
    with rasterio.open(path) as s:
        t = s.tags().get("sar_date") or s.tags().get("date") or ""
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", t or Path(path).name)
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def temporal(paths: list, out: Path, stats=("mean", "median", "min", "max", "std", "count", "trend"), pol: str | None = None) -> dict:
    """Per-pixel statistics of several dates (all put onto the first one's grid), per polarisation."""
    if len(paths) < 2:
        raise ValueError("Give at least two dates")
    first, _, grid = read_pols(paths[0])
    pols = [pol.upper()] if pol else list(first)
    stack = {k: [] for k in pols}
    dates = []
    for pth in paths:
        p, _, _ = read_pols(pth, like=grid)
        for k in pols:
            if k not in p:
                raise ValueError(f"{Path(pth).name} has no {k} band")
            stack[k].append(p[k])
        dates.append(_date_of(pth))
    bands, names = [], []
    yrs = None
    if "trend" in stats:
        if any(d is None for d in dates):
            raise ValueError("The trend needs each image's date (in its name, e.g. …_2026-09-29…, or its tags)")
        yrs = np.array([(d - dates[0]).days / 365.25 for d in dates])
        if np.ptp(yrs) == 0:
            raise ValueError("The trend needs images of different dates")
    for k in pols:
        S = np.stack(stack[k])
        D = _db(S)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)   # all-empty pixels
            for st in stats:
                if st == "mean":
                    bands.append(_db(np.nanmean(S, 0))); names.append(f"{k} mean (dB)")
                elif st == "median":
                    bands.append(_db(np.nanmedian(S, 0))); names.append(f"{k} median (dB)")
                elif st == "min":
                    bands.append(np.nanmin(D, 0)); names.append(f"{k} min (dB)")
                elif st == "max":
                    bands.append(np.nanmax(D, 0)); names.append(f"{k} max (dB)")
                elif st == "std":
                    bands.append(np.nanstd(D, 0)); names.append(f"{k} std (dB)")
                elif st == "count":
                    bands.append(np.isfinite(D).sum(0).astype("float32")); names.append(f"{k} dates with data")
                elif st == "trend":   # least squares slope of dB against years, per pixel (NaN-aware)
                    ok = np.isfinite(D)
                    yv = np.where(ok, yrs[:, None, None], np.nan)
                    ym, dm = np.nanmean(yv, 0), np.nanmean(D, 0)
                    cov = np.nansum((yv - ym) * (D - dm), 0)
                    var = np.nansum((yv - ym) ** 2, 0)
                    bands.append(np.where(ok.sum(0) >= 3, cov / var, np.nan)); names.append(f"{k} trend (dB / year)")
    pth = _write(grid, out, bands, names, {"sar_temporal": ",".join(stats), "dates": json.dumps([d.strftime("%Y-%m-%d") if d else None for d in dates])})
    return {"path": pth, "bands": names, "dates": [d.strftime("%Y-%m-%d") if d else None for d in dates]}


def change(before, after, out_dir: Path, *, pol: str = "VV", threshold_db: float = 3.0, water_db: float = -18.0,
           name: str | None = None) -> dict:
    """Change between two dates: log-ratio 10·log10(after / before) (dB), classes (decrease / no change / increase by
    ±threshold), and new water (flooding): after below water_db and a drop of at least threshold."""
    a, _, grid = read_pols(before)
    b, _, _ = read_pols(after, like=grid)
    k = pol.upper()
    if k not in a or k not in b:
        raise ValueError(f"Both images need a {k} band")
    lr = _db(b[k] / a[k])
    ok = np.isfinite(lr)
    cls = np.zeros(lr.shape, "uint8")
    cls[ok] = 2
    cls[ok & (lr <= -threshold_db)] = 1
    cls[ok & (lr >= threshold_db)] = 3
    db_after, db_before = _db(b[k]), _db(a[k])
    flood = ok & (db_after < water_db) & (lr <= -threshold_db)
    water_before = ok & (db_before < water_db)
    cls[flood] = 4
    cls[water_before & (db_after < water_db)] = 5   # water at both dates (rivers, lakes)
    stem = name or f"{Path(before).stem}_to_{Path(after).stem}"[:70]
    out_dir.mkdir(parents=True, exist_ok=True)
    p1 = _write(grid, out_dir / f"{stem}_{k}_logratio.tif", [lr], [f"{k} change (dB)"], {"sar_change": "log-ratio", "units": "dB"})
    names = {1: f"Decrease (≤ −{threshold_db:g} dB)", 2: "No change", 3: f"Increase (≥ +{threshold_db:g} dB)", 4: "New water (flooding)", 5: "Water at both dates"}
    cols = {1: (215, 48, 39, 255), 2: (230, 230, 230, 255), 3: (69, 117, 180, 255), 4: (0, 200, 255, 255), 5: (8, 48, 107, 255)}
    p2 = out_dir / f"{stem}_{k}_change_classes.tif"
    prof = {"driver": "GTiff", "width": grid["shape"][1], "height": grid["shape"][0], "count": 1, "dtype": "uint8", "crs": grid["crs"],
            "transform": grid["transform"], "nodata": 0, "compress": "deflate", "photometric": "palette"}
    with rasterio.open(p2, "w", **prof) as d:
        d.write_colormap(1, {0: (0, 0, 0, 0), **cols})
        d.write(cls, 1)
        d.update_tags(classes=json.dumps(names))
        d.set_band_description(1, "SAR change")
    tr = grid["transform"]
    px_ha = abs(tr.a * tr.e) / 1e4 if grid["crs"] and not grid["crs"].is_geographic else None
    counts = {names[i]: int((cls == i).sum()) for i in names}
    return {"outputs": [str(p2), p1], "classes": [{"class": n, "pixels": c, "area_ha": round(c * px_ha, 2) if px_ha else None} for n, c in counts.items()],
            "mean_change_db": round(float(np.nanmean(lr)), 3) if ok.any() else None, "flood_ha": round(int(flood.sum()) * px_ha, 2) if px_ha else None}
