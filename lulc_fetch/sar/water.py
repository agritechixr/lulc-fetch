"""Water and flood mapping by fuzzy fusion of independent evidence, with a confidence for every pixel.

    water_map(post, …)   0–1 water confidence from:
                           radar (calm water is dark: VV / VH dB below a threshold found in the scene itself, Otsu,
                           where its histogram is bimodal; else −18 / −24 dB),
                           optical where it is clear (the Water mask tool's index: AWEI no-shadow, else NDWI, at 0;
                           or a finished water mask layer),
                           the drop since a pre-flood radar image (≥ 3 dB darker),
                           terrain (water lies low and flat: height above the local low ≲ 10 m, slope ≲ 8°; this also
                           rejects radar shadow on slopes, which looks like water),
                         then classes: dry land, uncertain, permanent water (pre-flood image or JRC Global Surface
                         Water occurrence ≥ 50 %), flood (new water), water (when nothing tells old from new)

Evidence is averaged where it exists (optical only where clear) and the terrain scales it, so every source can be
left out. Water is confidence ≥ 0.5, uncertain 0.35–0.5 (tuned on Sen1Floods11's hand-labelled validation split:
radar + optical 0.72 IoU there, radar alone 0.48, the dataset's own Otsu labels 0.51). The optical evidence is the
Water mask tool's index (AWEI no-shadow, else NDWI / MNDWI) through a steep logistic, weighted 2 against the radar's 1
(best of the forms tried on the same validation chips).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import minimum_filter, uniform_filter

from .. import progress

CLASSES = {0: "No data", 1: "Dry land", 2: "Uncertain", 3: "Permanent water", 4: "Flood (new water)", 5: "Water"}
COLORS = {0: (0, 0, 0, 0), 1: (235, 225, 200, 120), 2: (255, 200, 60, 255), 3: (8, 48, 107, 255), 4: (0, 190, 255, 255), 5: (33, 113, 181, 255)}


def _db(x):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10 * np.log10(np.where(x > 0, x, np.nan))


def below(x, mid, width):
    """Membership 1 well below mid, 0 well above (logistic of width)."""
    with np.errstate(over="ignore", invalid="ignore"):
        return 1 / (1 + np.exp((x - mid) / width))


def otsu(v: np.ndarray, bins: int = 256) -> float:
    h, e = np.histogram(v, bins=bins)
    c = (e[:-1] + e[1:]) / 2
    w0 = np.cumsum(h)
    w1 = w0[-1] - w0
    m0 = np.cumsum(h * c) / np.maximum(w0, 1)
    m1 = (np.sum(h * c) - np.cumsum(h * c)) / np.maximum(w1, 1)
    return float(c[np.argmax(w0 * w1 * (m0 - m1) ** 2)])


def scene_threshold(db: np.ndarray, default: float, lo: float = -32, hi: float = -8, mode: str = "checked") -> tuple[float, str]:
    """The water / land split of a dB image: mode "checked" (Otsu on the values in [lo, hi], accepted when both sides
    have pixels and their means are ≥ 4 dB apart, else the default), "otsu" (Otsu, kept within ±6 dB of the
    default) or "fixed" (the default)."""
    if mode == "fixed":
        return default, "default"
    v = db[np.isfinite(db)]
    v = v[(v > lo) & (v < hi)]
    if v.size < 1000:
        return default, "default (too few pixels)"
    t = otsu(v)
    if mode == "otsu":
        return round(float(np.clip(t, default - 6, default + 6)), 2), "from the image (Otsu)"
    a, b = v[v < t], v[v >= t]
    if a.size < 0.005 * v.size or b.size < 0.2 * v.size or b.mean() - a.mean() < 4 or not (default - 6 <= t <= default + 4):
        return default, "default (no clear water mode in the image)"
    return round(t, 2), "from the image (Otsu)"


def height_above_low(dem: np.ndarray, res_m: float, radius_m: float = 1000) -> np.ndarray:
    """Height above the lowest ground within radius_m: a quick stand-in for HAND (height above nearest drainage)."""
    size = max(3, int(2 * radius_m / res_m) | 1)
    d = np.where(np.isfinite(dem), dem, np.nanmax(dem) if np.isfinite(dem).any() else 0)
    return dem - minimum_filter(d, size)


def slope_deg(dem: np.ndarray, res_m: float) -> np.ndarray:
    gy, gx = np.gradient(np.where(np.isfinite(dem), dem, np.nanmean(dem)), res_m)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def sar_water(pol: dict, thr: dict, width: float = 1.0) -> tuple[np.ndarray, dict]:
    """Radar water membership: VV and VH below their thresholds (averaged where both exist)."""
    ms, used = [], {}
    for k, w in (("VV", 0.6), ("VH", 0.4), ("HH", 0.6), ("HV", 0.4)):
        if k in pol:
            ms.append((below(_db(pol[k]), thr[k], width), w))
            used[k] = thr[k]
    num = sum(np.where(np.isfinite(m), m * w, 0) for m, w in ms)
    den = sum(np.where(np.isfinite(m), w, 0) for m, w in ms)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan), used


def water_map(post: dict, *, pre: dict | None = None, optical: dict | None = None, clear: np.ndarray | None = None,
              dem: np.ndarray | None = None, res_m: float = 10.0, permanent: np.ndarray | None = None, thresholds: dict | None = None,
              hand_m: float = 10.0, slope_max: float = 8.0, drop_db: float = 3.0, sar_quality: np.ndarray | None = None,
              water_cut: float = 0.5, threshold_mode: str = "checked", width: float = 1.0, sar_weight: float = 1.0,
              terrain_on: str = "radar", optical_mask: np.ndarray | None = None, optical_weight: float = 2.0) -> dict:
    """post / pre: {"VV": linear, "VH": linear}; optical: {"B03", "B08", "B11"} reflectance with `clear` (True where
    not cloudy); dem: heights (m); permanent: JRC occurrence (%) or a 0 / 1 mask. Returns arrays and what was used."""
    shape = next(iter(post.values())).shape
    thr, notes = {}, {}
    defaults = {"VV": -18.0, "VH": -24.0, "HH": -18.0, "HV": -26.0}
    for k in post:
        if k in defaults:
            if thresholds and thresholds.get(k) is not None:
                thr[k], notes[k] = float(thresholds[k]), "given"
            else:
                if threshold_mode in ("checked", "otsu", "fixed"):
                    thr[k], notes[k] = scene_threshold(_db(post[k]), defaults[k], mode=threshold_mode)
                else:   # an automatic method of lulc_fetch/thresholds.py (water is dark in radar)
                    from .. import thresholds as TH
                    d = _db(post[k])
                    r = TH.compute(np.where((d > -40) & (d < 5), d, np.nan), threshold_mode, bright=False, default=defaults[k])
                    thr[k] = r["threshold"]
                    notes[k] = r["title"] + (f" ({r['note']})" if r.get("note") else "")
    sar, _ = sar_water(post, thr, width)
    if sar_quality is not None:   # radar layover / shadow: no radar evidence there
        sar = np.where(np.isin(sar_quality, (2, 3)), np.nan, sar)
    terrain = None
    if dem is not None:   # water lies low and flat; dark radar on slopes is shadow
        terrain = below(height_above_low(dem, res_m), hand_m, hand_m / 5) * below(slope_deg(dem, res_m), slope_max, 1.5)
        terrain = np.where(np.isfinite(terrain), terrain, 1)
        if terrain_on == "radar":
            sar = sar * terrain
    ev = [(sar, sar_weight)]
    sources = ["radar"]
    if optical_mask is not None:   # a finished water mask (Water mask tool): 2 water, 1 land, else unknown
        ev.append((np.where(optical_mask == 2, 1.0, np.where(optical_mask == 1, 0.0, np.nan)), optical_weight))
        sources.append("optical water mask")
    elif optical:   # the water-mask engine's best index (AWEI no-shadow, else NDWI / MNDWI) at 0, as a 0–1 membership
        from .. import watermask as WM
        roles = {r: optical.get(b) for r, b in (("blue", "B02"), ("green", "B03"), ("red", "B04"), ("nir", "B08"), ("swir1", "B11"), ("swir2", "B12"))}
        roles = {k: v for k, v in roles.items() if v is not None}
        if "green" in roles and ("nir" in roles or "swir1" in roles):
            name = WM.best_index(set(roles))
            with np.errstate(over="ignore", invalid="ignore"):
                m = 1 / (1 + np.exp(-WM.index(name, roles) / 0.05))
            if clear is not None:
                m = np.where(clear, m, np.nan)
            ev.append((m, optical_weight))   # clear optical counts double: it is the sharper evidence where it exists
            sources.append(f"optical ({WM.INDICES[name][0]})")
    num = sum(np.where(np.isfinite(m), m * w, 0) for m, w in ev)
    den = sum(np.where(np.isfinite(m), w, 0) for m, w in ev)
    agree = sum((np.nan_to_num(m, nan=0) >= 0.5).astype("uint8") for m, _ in ev)
    with np.errstate(invalid="ignore", divide="ignore"):
        conf = np.where(den > 0, num / den, np.nan)
    if terrain is not None:
        if terrain_on == "all":
            conf = conf * terrain
        sources.append("terrain")
    pre_water = None
    change = None
    if pre:
        pw, _ = sar_water(pre, thr)
        pre_water = pw >= 0.5
        k = "VV" if "VV" in post and "VV" in pre else next(iter(set(post) & set(pre)))
        drop = _db(post[k]) - _db(pre[k])
        change = below(drop, -drop_db, 1.0)
        sources.append("change since the pre-flood image")
    perm = None
    if permanent is not None:
        perm = (permanent >= 50) if np.nanmax(permanent) > 1 else permanent > 0
        sources.append("JRC permanent water" if np.nanmax(permanent) > 1 else "permanent water mask")
    valid = np.isfinite(conf)
    cls = np.zeros(shape, "uint8")
    cls[valid] = 1
    cls[valid & (conf >= water_cut - 0.15)] = 2   # (0.5: tuned on Sen1Floods11's validation split, see the module notes)
    water = valid & (conf >= water_cut)
    known_old = np.zeros(shape, bool)
    if pre_water is not None:
        known_old |= pre_water
    if perm is not None:
        known_old |= perm
    if pre_water is not None or perm is not None:
        cls[water & known_old] = 3
        new = water & ~known_old
        if change is not None:   # new water should also have darkened; otherwise uncertain
            cls[new & (change >= 0.5)] = 4
            cls[new & (change < 0.5)] = 2
        else:
            cls[new] = 4
    else:
        cls[water] = 5
    thr = {k: round(float(np.nanmean(v)), 2) if isinstance(v, np.ndarray) else v for k, v in thr.items()}   # (a local method: its mean)
    return {"confidence": conf.astype("float32"), "classes": cls, "agreement": agree, "terrain": terrain, "thresholds": thr,
            "threshold_source": notes, "sources": sources}


# ------------------------------------------------------------------ files in, files out
def _read_onto(path: str, grid: dict, names: list[str] | None = None, nearest: bool = False) -> dict:
    from rasterio.warp import Resampling, reproject
    out = {}
    with rasterio.open(path) as s:
        desc = [(d or f"band{i + 1}") for i, d in enumerate(s.descriptions)]
        for i, d in enumerate(desc):
            if names and d.upper() not in names and not any(d.upper().startswith(n) for n in names):
                continue
            a = np.full(grid["shape"], np.nan, "float32")
            reproject(rasterio.band(s, i + 1), a, dst_transform=grid["transform"], dst_crs=grid["crs"], dst_nodata=np.nan,
                      resampling=Resampling.nearest if nearest else Resampling.bilinear)
            out[d.upper()] = a
    return out


def run(post_path: str, out_dir: Path, *, pre_path: str | None = None, optical_path: str | None = None, mask_path: str | None = None, dem: str | None = "auto",
        permanent: str | None = "auto", cache: Path | None = None, aoi: dict | None = None, name: str = "water", **kw) -> dict:
    """water_map from files on the post-event SAR's grid; dem / permanent 'auto' fetch Copernicus DEM and JRC Global
    Surface Water occurrence for the area (Planetary Computer), None leaves them out, a path uses that layer."""
    from . import analysis as AN
    from . import geometry as G
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress.update(0.02, "Reading the radar image")
    post, _, grid = AN.read_pols(post_path)
    with rasterio.open(post_path) as s:
        grid = {"crs": s.crs, "transform": s.transform, "shape": (s.height, s.width)}
        prof = s.profile.copy()
    res_m = abs(grid["transform"].a) * (111320 if grid["crs"].is_geographic else 1)
    pre = AN.read_pols(pre_path, like=grid)[0] if pre_path else None
    optical, clear = None, None
    if optical_path:
        progress.update(0.15, "Reading the optical image")
        from .. import watermask as WM
        g = WM.guess_bands(optical_path)["bands"]
        raw = _read_onto(optical_path, grid)
        keys = list(raw)
        roles = {r: raw[keys[i - 1]] for r, i in g.items() if 0 < i <= len(keys)}
        if "green" in roles:
            ref = roles["green"]
            if np.nanmedian(ref) > 1.5:
                roles = {k: v / 10000.0 for k, v in roles.items()}
            optical = {b: roles[r] for r, b in (("blue", "B02"), ("green", "B03"), ("red", "B04"), ("nir", "B08"), ("swir1", "B11"), ("swir2", "B12")) if r in roles}
            clear = np.isfinite(optical["B03"])
            if "SCL" in raw:
                from .fusion import SCL_CLOUD
                clear &= ~np.isin(np.nan_to_num(raw["SCL"], nan=0).astype(int), SCL_CLOUD)
    omask = None
    if mask_path:
        omask = next(iter(_read_onto(mask_path, grid, nearest=True).values()))
    demarr, dem_name = None, None
    if dem:
        progress.update(0.3, "DEM heights")
        try:
            demarr, dem_name = G.dem_for(grid["crs"], grid["transform"], grid["shape"], None if dem == "auto" else dem, cache)
        except Exception as e:   # noqa: BLE001 (terrain is optional evidence)
            dem_name = f"not used ({str(e)[:120]})"
    perm, perm_name = None, None
    if permanent:
        progress.update(0.45, "Permanent water")
        try:
            perm, perm_name = (_jrc_occurrence(grid), "JRC Global Surface Water occurrence 1984–2020 (≥ 50 %)") if permanent == "auto" else \
                (next(iter(_read_onto(permanent, grid, nearest=True).values())), Path(permanent).name)
        except Exception as e:   # noqa: BLE001
            perm_name = f"not used ({str(e)[:120]})"
    q = None
    qp = Path(str(post_path).replace("_linear.tif", "_quality.tif").replace("_dB.tif", "_quality.tif"))
    if qp.exists() and qp != Path(post_path):
        q = next(iter(_read_onto(str(qp), grid, nearest=True).values()))
    progress.update(0.6, "Combining the evidence")
    r = water_map(post, pre=pre, optical=optical, clear=clear, dem=demarr, res_m=res_m, permanent=perm, sar_quality=q, optical_mask=omask, **kw)
    if aoi:
        from .pipeline import _clip_mask
        m = _clip_mask(grid, aoi)
        r["confidence"] = np.where(m, r["confidence"], np.nan)
        r["classes"] = np.where(m, r["classes"], 0).astype("uint8")
    base = {k: prof[k] for k in ("driver", "width", "height", "crs", "transform") if k in prof}
    base.update(driver="GTiff", compress="deflate")
    cp, kp = out_dir / f"{name}_confidence.tif", out_dir / f"{name}_classes.tif"
    with rasterio.open(cp, "w", **base, count=1, dtype="float32", nodata=np.nan) as d:
        d.write(r["confidence"], 1)
        d.set_band_description(1, "Water confidence (0–1)")
    with rasterio.open(kp, "w", **base, count=1, dtype="uint8", nodata=0, photometric="palette") as d:
        d.write_colormap(1, COLORS)
        d.write(r["classes"], 1)
        d.set_band_description(1, "Water / flood")
        d.update_tags(classes=json.dumps(CLASSES), sources=", ".join(r["sources"]), thresholds=json.dumps(r["thresholds"]))
    ha = abs(grid["transform"].a * grid["transform"].e) / 1e4 if not grid["crs"].is_geographic else None
    areas = {CLASSES[i]: round(int((r["classes"] == i).sum()) * ha, 2) if ha else int((r["classes"] == i).sum()) for i in range(1, 6) if (r["classes"] == i).any()}
    c = r["confidence"]
    return {"outputs": [str(kp), str(cp)], "areas_ha" if ha else "areas_px": areas, "sources": r["sources"], "thresholds": r["thresholds"],
            "threshold_source": r["threshold_source"], "dem": dem_name, "permanent": perm_name,
            "mean_confidence_water": round(float(np.nanmean(c[c >= 0.65])), 3) if (c >= 0.65).any() else None}


def _jrc_occurrence(grid) -> np.ndarray:
    """JRC Global Surface Water occurrence (% of the time water, 1984–2020) on the grid, from Planetary Computer."""
    import planetary_computer as pc
    import pystac_client
    from rasterio.transform import array_bounds
    from rasterio.warp import Resampling, reproject, transform_bounds
    w, s_, e, n = transform_bounds(grid["crs"], "EPSG:4326", *array_bounds(*grid["shape"], grid["transform"]))
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace, timeout=60)
    out = np.full(grid["shape"], np.nan, "float32")
    for it in cat.search(collections=["jrc-gsw"], bbox=[w, s_, e, n]).items():
        tmp = np.full(grid["shape"], np.nan, "float32")
        with rasterio.open("/vsicurl/" + it.assets["occurrence"].href) as src:
            reproject(rasterio.band(src, 1), tmp, dst_transform=grid["transform"], dst_crs=grid["crs"], dst_nodata=np.nan,
                      src_nodata=255, resampling=Resampling.nearest)
        out = np.where(np.isnan(out), tmp, out)
    return np.nan_to_num(out, nan=0)
