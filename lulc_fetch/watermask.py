"""Water mask from a multispectral image (Sentinel-2, Landsat, or any image with green / NIR / SWIR bands).

    INDICES                                     NDWI (McFeeters 1996), MNDWI (Xu 2006), AWEI no-shadow and shadow
                                                (Feyisa et al. 2014), WI2015 (Fisher et al. 2016): water is above 0
    guess_bands(path)                           which band is blue / green / red / NIR / SWIR1 / SWIR2, from the
                                                band names and the sensor (Sentinel-2 and Landsat 8/9 number them
                                                differently: Landsat B5 is NIR, Sentinel-2 B05 is red edge)
    water_mask(bands, index, …)                 the index, its threshold (from the image's own histogram when it
                                                has a water mode, Otsu; else 0), clouds / shadow / snow masked,
                                                specks below a size removed, and a 0–1 confidence
    run(path, out_dir, …)                       the same from a file: mask, index and confidence GeoTIFFs, hectares

Defaults chosen on Sen1Floods11's hand-labelled validation chips (clear pixels): AWEI no-shadow at 0 was best
(IoU 0.78), then NDWI (0.74), AWEI shadow (0.72), WI2015 (0.65) and MNDWI (0.63, it takes turbid flood water and
flooded vegetation for land); thresholds from the image's histogram (Otsu) did worse than 0 for every index.

The mask is also evidence for the flood map (Analysis ▸ SAR ▸ Flood & water map): where the optical image is clear
it says water or not, and the radar fills in under the clouds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import rasterio

from . import progress

ROLES = ("blue", "green", "red", "nir", "swir1", "swir2")
INDICES = {
    "mndwi": ("MNDWI", ("green", "swir1"), "Modified NDWI (green vs SWIR1, Xu 2006): the usual best; separates water from built-up land"),
    "ndwi": ("NDWI", ("green", "nir"), "NDWI (green vs NIR, McFeeters 1996): for images without SWIR; confuses towns and some soils with water"),
    "awei_nsh": ("AWEI no-shadow", ("green", "nir", "swir1", "swir2"), "Automated Water Extraction Index for areas without much shadow (Feyisa 2014)"),
    "awei_sh": ("AWEI shadow", ("blue", "green", "nir", "swir1", "swir2"), "AWEI for areas with mountain or building shadow (Feyisa 2014)"),
    "wi2015": ("WI2015", ("green", "red", "nir", "swir1", "swir2"), "Water Index 2015 (Fisher et al. 2016), fitted on Landsat over many water types"),
}
SCL_MASK = {3: "cloud shadow", 8: "cloud", 9: "cloud", 10: "cirrus", 11: "snow / ice"}
CLASSES = {0: "No data", 1: "Land", 2: "Water", 3: "Masked (cloud, shadow, snow)"}
COLORS = {0: (0, 0, 0, 0), 1: (235, 225, 200, 90), 2: (33, 113, 181, 255), 3: (200, 200, 200, 160)}

S2 = {"blue": "B02", "green": "B03", "red": "B04", "nir": "B08", "swir1": "B11", "swir2": "B12"}
LANDSAT89 = {"blue": "B2", "green": "B3", "red": "B4", "nir": "B5", "swir1": "B6", "swir2": "B7"}
WORDS = {"blue": ("BLUE",), "green": ("GREEN",), "red": ("RED",), "nir": ("NIR", "NEAR"), "swir1": ("SWIR1", "SWIR_1", "SWIR"), "swir2": ("SWIR2", "SWIR_2")}


def guess_bands(path: str | Path) -> dict:
    """{role: band number (1-based)} from the band names (S2 B02…, Landsat SR_B2…, or words like 'green'), the file
    name / tags (Landsat or Sentinel-2) and, for unnamed 4- / 6-band images, the usual order."""
    with rasterio.open(path) as s:
        names = [(d or "").upper().replace("SR_", "") for d in s.descriptions]
        tags = " ".join(str(v) for v in s.tags().values()).upper() + " " + Path(path).name.upper()
        n = s.count
    landsat = bool(re.search(r"LANDSAT|LC0?8|LC0?9|L8|L9|LT05|LE07", tags))
    out = {}
    for role in ROLES:
        for i, nm in enumerate(names):
            if nm and any(w == nm or nm.startswith(w + " ") for w in WORDS[role]):
                out[role] = i + 1
                break
    table = LANDSAT89 if landsat else S2
    for role in ROLES:
        if role in out:
            continue
        want = table[role]
        for i, nm in enumerate(names):
            m = re.fullmatch(r"B0?(\d{1,2})(A?)", nm)
            if m and f"B{int(m.group(1))}{m.group(2)}" == f"B{int(want[1:].lstrip('0') or 0)}" and not (want == "B08" and m.group(2)):
                out[role] = i + 1
                break
    if not out and not any(names):   # unnamed: the common orders
        if n == 4:
            out = {"blue": 1, "green": 2, "red": 3, "nir": 4}
        elif n == 6:   # blue, green, red, NIR, SWIR1, SWIR2 (Landsat 8 B2–B7 stacks)
            out = dict(zip(ROLES, range(1, 7)))
    return {"bands": out, "sensor": "Landsat 8/9" if landsat else "Sentinel-2 (or named bands)", "count": n, "names": names}


def index(name: str, b: dict) -> np.ndarray:
    g, nir = b.get("green"), b.get("nir")
    with np.errstate(invalid="ignore", divide="ignore"):
        if name == "ndwi":
            return (g - nir) / (g + nir)
        if name == "mndwi":
            return (g - b["swir1"]) / (g + b["swir1"])
        if name == "awei_nsh":
            return 4 * (g - b["swir1"]) - (0.25 * nir + 2.75 * b["swir2"])
        if name == "awei_sh":
            return b["blue"] + 2.5 * g - 1.5 * (nir + b["swir1"]) - 0.25 * b["swir2"]
        if name == "wi2015":
            return 1.7204 + 171 * g + 3 * b["red"] - 70 * nir - 45 * b["swir1"] - 71 * b["swir2"]
    raise ValueError(f"Index: {', '.join(INDICES)}")


def best_index(have: set) -> str:
    for k in ("awei_nsh", "ndwi", "mndwi"):   # (best first on Sen1Floods11's validation chips, see the notes)
        if set(INDICES[k][1]) <= have:
            return k
    raise ValueError("The image needs at least green and NIR bands (green and SWIR for the better indices)")


def otsu(v: np.ndarray, bins: int = 256) -> float:
    h, e = np.histogram(v, bins=bins)
    c = (e[:-1] + e[1:]) / 2
    w0 = np.cumsum(h)
    w1 = w0[-1] - w0
    m0 = np.cumsum(h * c) / np.maximum(w0, 1)
    m1 = (np.sum(h * c) - np.cumsum(h * c)) / np.maximum(w1, 1)
    return float(c[np.argmax(w0 * w1 * (m0 - m1) ** 2)])


def threshold(ix: np.ndarray, mode: str = "zero") -> tuple[float, str]:
    """0, or the image's own split when it has a water mode (Otsu, accepted when between 0.5 % and 90 % of the
    pixels fall on the water side, the two means are well apart and the split is near 0)."""
    if mode == "zero":
        return 0.0, "0 (fixed)"
    v = ix[np.isfinite(ix)]
    if v.size < 1000:
        return 0.0, "0 (too few pixels)"
    lo, hi = np.percentile(v, [0.5, 99.5])
    v = v[(v >= lo) & (v <= hi)]
    t = otsu(v)
    span = hi - lo
    a, b = v[v >= t], v[v < t]
    if not (0.005 * v.size < a.size < 0.9 * v.size) or a.mean() - b.mean() < 0.25 * span or abs(t) > 0.25 * span:
        return 0.0, "0 (no clear water mode in the image)"
    return round(t, 4), "from the image (Otsu)"


def water_mask(b: dict, *, idx: str = "auto", mode: str = "zero", masked: np.ndarray | None = None, min_px: int = 9,
               slope: np.ndarray | None = None, slope_max: float = 10.0, window: int = 51, k: float | None = None) -> dict:
    """b: {role: reflectance 0–1}. masked: True where cloud / shadow / snow. slope (degrees, optional): steep water is
    shadow. Returns the mask classes, index, confidence and what was used."""
    from scipy.ndimage import label
    have = {k for k, v in b.items() if v is not None}
    name = best_index(have) if idx == "auto" else idx
    if not set(INDICES[name][1]) <= have:
        raise ValueError(f"{INDICES[name][0]} needs the {', '.join(INDICES[name][1])} bands")
    ix = index(name, b).astype("float32")
    valid = np.isfinite(ix)
    clear = valid & ~np.asarray(masked, bool) if masked is not None else valid.copy()
    member = None
    if mode in ("zero", "auto"):
        t, src = threshold(np.where(clear, ix, np.nan), mode)
    else:   # one of the automatic methods (global, local or fuzzy): lulc_fetch/thresholds.py
        from . import thresholds as TH
        r = TH.compute(ix, mode, valid=clear, bright=True, window=window, k=k, default=0.0)
        t, member = r["threshold"], r["membership"] if r["kind"] == "fuzzy" else None
        src = f"{r['title']}" + (f", {window} × {window} window" if r["kind"] == "local" else "") + (f": {r['note']}" if r.get("note") else "")
    v = ix[clear]
    scale = max(float(np.subtract(*np.percentile(v, [90, 10]))) / 10, 1e-3) if v.size else 0.05
    with np.errstate(over="ignore", invalid="ignore"):
        conf = (1 / (1 + np.exp(-(ix - t) / scale))).astype("float32") if member is None else member.astype("float32")
    water = clear & (ix > t) if member is None else clear & (member >= 0.5)
    if slope is not None:
        water &= ~(slope > slope_max)
    if min_px > 1 and water.any():   # specks: small groups of water pixels, and small dry holes in water
        lab, n = label(water)
        sizes = np.bincount(lab.ravel())
        small = sizes < min_px
        small[0] = False
        water &= ~small[lab]
        lab, n = label(clear & ~water)
        sizes = np.bincount(lab.ravel())
        small = sizes < min_px
        small[0] = False
        water |= small[lab]
    cls = np.zeros(ix.shape, "uint8")
    cls[valid] = 1
    cls[water] = 2
    if masked is not None:
        cls[valid & masked] = 3
    conf = np.where(clear, conf, np.nan).astype("float32")
    tr = float(np.nanmean(t)) if isinstance(t, np.ndarray) else t
    return {"classes": cls, "index": ix, "confidence": conf, "index_name": name, "threshold": round(tr, 4), "threshold_source": src,
            "threshold_varies": isinstance(t, np.ndarray)}


def _read(path, bands: dict):
    with rasterio.open(path) as s:
        data = {r: s.read(i, masked=True).astype("float32").filled(np.nan) for r, i in bands.items() if i}
        prof = s.profile.copy()
        names = [(d or "").upper() for d in s.descriptions]
        scl = s.read(names.index("SCL") + 1) if "SCL" in names else None
    ref = data.get("green", next(iter(data.values())))
    med = float(np.nanmedian(ref)) if np.isfinite(ref).any() else 0
    if med > 1.5:   # digital numbers: Sentinel-2 L2A (× 10 000), or Landsat Collection 2 (× 0.0000275 − 0.2)
        if med > 5000 and "LANDSAT" in " ".join(names + [Path(path).name.upper()]):
            data = {k: v * 0.0000275 - 0.2 for k, v in data.items()}
        else:
            data = {k: v / 10000.0 for k, v in data.items()}
    return data, prof, scl


def run(path: str, out_dir: Path, *, bands: dict | None = None, idx: str = "auto", mode: str = "zero", cloud: str | None = None,
        scl: bool = True, dem: str | None = None, cache: Path | None = None, min_px: int = 9, slope_max: float = 10.0,
        aoi: dict | None = None, name: str = "water_mask", window: int = 51, k: float | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    g = guess_bands(path)
    bm = {**g["bands"], **{k: int(v) for k, v in (bands or {}).items() if v}}
    if "green" not in bm:
        raise ValueError("Which band is green? Choose the bands (the names didn't say)")
    progress.update(0.05, "Reading the bands")
    data, prof, sclb = _read(path, bm)
    shape = next(iter(data.values())).shape
    grid = {"crs": prof["crs"], "transform": prof["transform"], "shape": shape}
    masked = np.zeros(shape, bool)
    notes = []
    if scl and sclb is not None:
        masked |= np.isin(sclb, list(SCL_MASK))
        notes.append("clouds, shadow and snow from the SCL band")
    if cloud:
        from rasterio.warp import Resampling, reproject
        cm = np.zeros(shape, "float32")
        with rasterio.open(cloud) as c:
            reproject(rasterio.band(c, 1), cm, dst_transform=grid["transform"], dst_crs=grid["crs"], resampling=Resampling.nearest)
        masked |= cm > 0
        notes.append(f"clouds from {Path(cloud).name}")
    slope = None
    if dem:
        progress.update(0.3, "Slopes")
        from .sar import geometry as G
        d, _ = G.dem_for(grid["crs"], grid["transform"], shape, None if dem == "auto" else dem, cache)
        res_m = abs(grid["transform"].a) * (111320 if grid["crs"].is_geographic else 1)
        gy, gx = np.gradient(np.where(np.isfinite(d), d, np.nanmean(d)), res_m)
        slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        notes.append(f"water on slopes over {slope_max:g}° dropped (hill shadow)")
    progress.update(0.5, "Water index")
    r = water_mask(data, idx=idx, mode=mode, masked=masked, min_px=min_px, slope=slope, slope_max=slope_max, window=window, k=k)
    if aoi:
        from .sar.pipeline import _clip_mask
        m = _clip_mask(grid, aoi)
        r["classes"] = np.where(m, r["classes"], 0).astype("uint8")
        r["confidence"] = np.where(m, r["confidence"], np.nan)
        r["index"] = np.where(m, r["index"], np.nan)
    base = {k: prof[k] for k in ("width", "height", "crs", "transform")}
    base.update(driver="GTiff", compress="deflate")
    mp, ip, cp = out_dir / f"{name}.tif", out_dir / f"{name}_{r['index_name']}.tif", out_dir / f"{name}_confidence.tif"
    with rasterio.open(mp, "w", **base, count=1, dtype="uint8", nodata=0, photometric="palette") as d:
        d.write_colormap(1, COLORS)
        d.write(r["classes"], 1)
        d.set_band_description(1, "Water mask")
        d.update_tags(classes=json.dumps(CLASSES), index=INDICES[r["index_name"]][0], threshold=str(r["threshold"]), water_mask="1")
    with rasterio.open(ip, "w", **base, count=1, dtype="float32", nodata=np.nan) as d:
        d.write(r["index"], 1)
        d.set_band_description(1, INDICES[r["index_name"]][0])
    with rasterio.open(cp, "w", **base, count=1, dtype="float32", nodata=np.nan) as d:
        d.write(r["confidence"], 1)
        d.set_band_description(1, "Water confidence (0–1)")
    t = grid["transform"]
    if grid["crs"].is_geographic:   # degrees: the pixel's area at the image's mean latitude
        lat = t.f + t.e * shape[0] / 2
        ha = abs(t.a * t.e) * 111320 * 110574 * np.cos(np.radians(lat)) / 1e4
    else:
        ha = abs(t.a * t.e) / 1e4
    cnt = {CLASSES[i]: int((r["classes"] == i).sum()) for i in (1, 2, 3)}
    return {"outputs": [str(mp), str(ip), str(cp)], "index": INDICES[r["index_name"]][0], "index_key": r["index_name"], "threshold": r["threshold"],
            "threshold_source": r["threshold_source"], "threshold_varies": r["threshold_varies"], "bands": bm, "sensor": g["sensor"], "masking": notes,
            "areas_ha": {k: round(v * ha, 2) for k, v in cnt.items()} if ha else None, "pixels": cnt,
            "water_pct_of_clear": round(100 * cnt["Water"] / max(1, cnt["Water"] + cnt["Land"]), 2)}
