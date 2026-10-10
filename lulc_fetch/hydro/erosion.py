"""Soil erosion, RUSLE (Analysis ▸ Hydrology ▸ Soil erosion): A = R · K · LS · C · P, the average yearly soil loss in
t / ha / year (Renard et al. 1997).

- R, rainfall erosivity (MJ mm / ha / h / year): from mean annual rainfall P (mm, a number or a raster such as Rainfall
  data's CHIRPS mean): Renard & Freimund (1994), R = 0.0483 P^1.61 below 850 mm, else 587.8 − 1.219 P + 0.004105 P²;
  or an R raster of your own.
- K, soil erodibility (t h / MJ / mm): one value, a raster, or from the soil's texture class (typical K of the USDA
  textures, Stone & Hilborn 2000).
- LS, slope length and steepness: from the DEM's specific catchment area (MFD) and slope, Moore & Burch (1986):
  LS = (a / 22.13)^0.4 · (sin β / 0.0896)^1.3.
- C, cover management: from a land-cover map (by its class names, e.g. WorldCover) or from NDVI
  (van der Knijff et al. 2000: C = exp(−2 NDVI / (1 − NDVI))); or a C raster.
- P, support practice: 1 (none), one value (e.g. 0.5 contour farming) or a raster.

Soil loss classes (FAO): very slight < 2, slight 2–5, moderate 5–10, high 10–20, very high 20–40, severe > 40 t/ha/yr.
With outlet points: the mean soil loss of each watershed and its sediment yield with a sediment delivery ratio
(Vanoni 1975: SDR = 0.42 A^−0.125, A in mi² converted from km²)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .. import hydrology as H
from .. import progress
from .common import Surface, open_dem, polygons, read_like, write, write_fc
from .flow import accumulate_multi, receivers_mfd
from .terrain import slope_aspect

K_TEXTURE = {"sand": 0.008, "loamy sand": 0.012, "sandy loam": 0.017, "loam": 0.039, "silt loam": 0.050, "silt": 0.055,
             "sandy clay loam": 0.026, "clay loam": 0.039, "silty clay loam": 0.042, "sandy clay": 0.018, "silty clay": 0.033, "clay": 0.029}
C_COVER = {"tree": 0.003, "forest": 0.003, "mangrove": 0.003, "shrub": 0.04, "grass": 0.05, "rangeland": 0.05, "crop": 0.28, "built": 0.0,
           "urban": 0.0, "bare": 0.45, "snow": 0.0, "water": 0.0, "wetland": 0.0, "flooded": 0.0, "moss": 0.1}
CLASSES = {1: ("Very slight (< 2)", 0, 2, (26, 152, 80, 255)), 2: ("Slight (2–5)", 2, 5, (145, 207, 96, 255)), 3: ("Moderate (5–10)", 5, 10, (217, 239, 139, 255)),
           4: ("High (10–20)", 10, 20, (254, 224, 139, 255)), 5: ("Very high (20–40)", 20, 40, (252, 141, 89, 255)), 6: ("Severe (> 40)", 40, np.inf, (215, 48, 39, 255))}


def r_from_rain(p: np.ndarray) -> np.ndarray:
    return np.where(p < 850, 0.0483 * p ** 1.61, 587.8 - 1.219 * p + 0.004105 * p ** 2)


def c_from_landcover(g, path) -> tuple[np.ndarray, dict]:
    import rasterio

    from .runoff import _cover_of
    lc = read_like(g, path, resampling="nearest")
    with rasterio.open(path) as s:
        try:
            names = {int(k): v for k, v in json.loads(s.tags().get("classes", "{}")).items()}
        except ValueError:
            names = {}
    if not names:
        from .runoff import SCHEMES
        names = SCHEMES["worldcover"]
    c = np.full(lc.shape, np.nan)
    used = {}
    for v, nm in names.items():
        cov = _cover_of(nm) or nm
        if cov in C_COVER:
            c[lc == v] = C_COVER[cov]
            used[str(nm)] = C_COVER[cov]
    if not np.isfinite(c).any():
        raise ValueError("None of the land-cover classes has a C factor → use NDVI or a C raster")
    return c, used


def run(dem, out_dir: Path, *, rain_mm: float | None = None, rain_raster: str | None = None, r_raster: str | None = None,
        k: float | None = None, k_raster: str | None = None, texture: str | None = None, landcover: str | None = None,
        ndvi: str | None = None, c_raster: str | None = None, p: float = 1.0, p_raster: str | None = None,
        points: list[list[float]] | None = None, snap_m: float = 150.0, name: str | None = None) -> dict:
    out_dir = Path(out_dir)
    stem = name or "rusle"
    g, z = open_dem(dem)
    # R
    if r_raster:
        R = read_like(g, r_raster)
    elif rain_raster or rain_mm:
        P = read_like(g, rain_raster) if rain_raster else np.full((g.h, g.w), float(rain_mm))
        R = r_from_rain(P)
    else:
        raise ValueError("Give the mean annual rainfall (mm), a rainfall raster or an R raster")
    # K
    if k_raster:
        K = read_like(g, k_raster)
    elif texture:
        if texture not in K_TEXTURE:
            raise ValueError(f"texture: {', '.join(K_TEXTURE)}")
        K = np.full((g.h, g.w), K_TEXTURE[texture])
    elif k:
        K = np.full((g.h, g.w), float(k))
    else:
        raise ValueError("Give the soil's K factor, its texture or a K raster")
    # C
    c_used = None
    if c_raster:
        C = read_like(g, c_raster)
    elif landcover:
        C, c_used = c_from_landcover(g, landcover)
    elif ndvi:
        nd = np.clip(read_like(g, ndvi), -1, 0.99)
        C = np.clip(np.exp(-2 * nd / (1 - nd)), 0, 1)
    else:
        raise ValueError("Give a land-cover map, an NDVI layer or a C raster")
    Pf = read_like(g, p_raster) if p_raster else np.full((g.h, g.w), float(p))
    # LS from the MFD specific catchment area
    progress.update(0.3, "Slope length and steepness (LS)")
    s = Surface(g, z)
    cell = np.where(g.valid, g.cell_m2, 0).ravel()
    Rr, Wt = receivers_mfd(s.eps, g)
    area = accumulate_multi(Rr, Wt, cell, g.valid.ravel())
    sca = (area / np.sqrt(np.maximum(cell, 1e-9))).reshape(g.h, g.w)
    slope, _, tan = slope_aspect(g, s.filled)
    sinb = np.sin(np.arctan(tan))
    LS = np.minimum((sca / 22.13) ** 0.4 * (sinb / 0.0896) ** 1.3, 100)   # capped: cells on streams are not hillslopes
    progress.update(0.7, "Soil loss")
    A = R * K * LS * C * Pf
    cls = np.zeros((g.h, g.w), "uint8")
    for c, (_, lo, hi, _) in CLASSES.items():
        cls[(A >= lo) & (A < hi)] = c
    cls[~np.isfinite(A)] = 0
    outs = [write(g, out_dir / f"{stem}_soil_loss.tif", A, "Soil loss A (t/ha/yr, RUSLE)"),
            write(g, out_dir / f"{stem}_soil_loss_classes.tif", cls, "Soil loss class (FAO)", dtype="uint8", nodata=0,
                  cmap={c: v[3] for c, v in CLASSES.items()}, tags={"classes": {str(c): v[0] for c, v in CLASSES.items()}}),
            write(g, out_dir / f"{stem}_factors.tif", np.stack([R, K, LS, C, Pf]), ["R rainfall erosivity", "K soil erodibility", "LS slope length & steepness", "C cover", "P practice"])]
    ok = g.valid & np.isfinite(A)
    ha = g.cell_m2 / 1e4
    res = {"outputs": outs, "mean_t_ha_yr": round(float(np.average(A[ok], weights=ha[ok])), 2), "total_t_yr": round(float((A[ok] * ha[ok]).sum()), 1),
           "class_pct": {v[0]: round(100 * float(ha[ok & (cls == c)].sum() / ha[ok].sum()), 2) for c, v in CLASSES.items()},
           "mean_R": round(float(np.nanmean(R[ok])), 1), "mean_K": round(float(np.nanmean(K[ok])), 4), "mean_LS": round(float(np.nanmean(LS[ok])), 2),
           "mean_C": round(float(np.nanmean(C[ok])), 3), "c_from": c_used}
    if points:
        snapped = s.snap(points, snap_m)
        outlets = {}
        for kk, i in enumerate(snapped, 1):
            outlets.setdefault(i, kk)
        lab = H.label_basins(s.down, s.levels, outlets)
        info, flat_A, flat_ha = {}, np.nan_to_num(A).ravel(), ha.ravel()
        for i, kk in outlets.items():
            m = lab == kk
            area_km2 = float(flat_ha[m].sum() / 100)
            gross = float((flat_A[m] * flat_ha[m]).sum())
            sdr = min(1.0, 0.42 * (area_km2 / 2.59) ** -0.125) if area_km2 > 0 else 1.0
            info[kk] = {"area_km2": round(area_km2, 3), "mean_t_ha_yr": round(gross / max(flat_ha[m].sum(), 1e-9), 2), "gross_t_yr": round(gross, 1),
                        "sdr": round(sdr, 3), "sediment_yield_t_yr": round(gross * sdr, 1)}
        outs.append(write_fc(polygons(g, lab.reshape(g.h, g.w), info, key="basin"), out_dir / f"{stem}_watersheds.geojson"))
        res["watersheds"] = [{"basin": kk, **v} for kk, v in sorted(info.items())]
    progress.update(1.0, "Done")
    return res
