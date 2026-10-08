"""Work with an existing multiband GeoTIFF: band detection, index maps, stats, pixel values, export."""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window

from . import progress
from .indices import CATALOG, COLORMAPS, SAR_NAMES, S2_NAMES, evaluate, normalize_band, required_bands
from .sources import DEFAULT_BANDS

COMPOSITES = {
    "true": ("True colour", ("B04", "B03", "B02")),
    "false": ("False colour (vegetation red)", ("B08", "B04", "B03")),
    "swir": ("SWIR (moisture / burn)", ("B12", "B08", "B04")),
    "agri": ("Agriculture", ("B11", "B08", "B02")),
    "urban": ("Urban", ("B12", "B11", "B04")),
    "sar": ("Radar RGB (VV, VH, VV/VH)", ("VV", "VH", "VVVH")),
}

_DESC_ALIASES = {
    "COASTAL": "B01", "BLUE": "B02", "GREEN": "B03", "RED": "B04", "REDEDGE1": "B05", "REDEDGE2": "B06",
    "REDEDGE3": "B07", "NIR": "B08", "NIR08": "B8A", "NIR09": "B09", "SWIR16": "B11", "SWIR1": "B11",
    "SWIR22": "B12", "SWIR2": "B12",
    # Landsat 8/9 Collection 2 surface reflectance
    "SR_B1": "B01", "SR_B2": "B02", "SR_B3": "B03", "SR_B4": "B04", "SR_B5": "B08", "SR_B6": "B11", "SR_B7": "B12",
    # NAIP / 4-band aerial as written by `lulc-fetch fetch`
    "IMAGE_1": "B04", "IMAGE_2": "B03", "IMAGE_3": "B02", "IMAGE_4": "B08",
}

# Band order assumed for files without usable band descriptions, by band count.
_ORDER_BY_COUNT = {
    3: ("B04", "B03", "B02"),                     # RGB
    4: ("B04", "B03", "B02", "B08"),              # RGB + NIR (NAIP, Planet, drones)
    10: tuple(DEFAULT_BANDS),                     # lulc-fetch default stack
    12: S2_NAMES,                                 # Sentinel-2 without B10
    13: ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", None, "B11", "B12"),  # ESA 13-band
}

SCALE_PRESETS = {
    "reflectance": ("Reflectance 0–1", 1.0, 0.0),
    "asis": ("Values as-is (×1), e.g. radar dB", 1.0, 0.0),
    "s2dn": ("Sentinel-2 DN (÷10000)", 1e-4, 0.0),
    "s2dn_offset": ("Sentinel-2 DN, baseline ≥ 04.00 (−1000, ÷10000)", 1e-4, -0.1),
    "landsat": ("Landsat Collection 2 SR (×0.0000275 − 0.2)", 2.75e-5, -0.2),
    "byte": ("8-bit 0–255 (÷255)", 1 / 255, 0.0),
}


def _norm_desc(desc: str | None) -> str | None:
    if not desc:
        return None
    up = desc.strip().upper().replace(" ", "")
    if up in _DESC_ALIASES:
        return _DESC_ALIASES[up]
    if up in SAR_NAMES:
        return up
    if re.fullmatch(r"B0?\d{1,2}A?", up):
        return normalize_band(up)
    return None


def detect_band_map(descriptions: tuple, count: int) -> tuple[dict[str, int], str]:
    """Map Sentinel-2 band names -> 1-based band numbers in the file. Returns (map, how)."""
    found = {}
    for i, d in enumerate(descriptions, start=1):
        name = _norm_desc(d)
        if name and name not in found:
            found[name] = i
    if found:
        return found, "band names"
    order = _ORDER_BY_COUNT.get(count)
    if order:
        return {name: i for i, name in enumerate(order, start=1) if name}, f"guessed from band count ({count})"
    return {}, "unknown — set the bands manually"


def detect_scale(src, band_map: dict[str, int]) -> tuple[str, float, float, str]:
    """Guess how pixel values convert to reflectance. Returns (preset, scale, offset, reason)."""
    tags = src.tags()
    units = (tags.get("units") or "").lower()
    if is_embedding(src):   # embedding values are used as they are (no reflectance scaling), nothing to sample
        return "asis", 1.0, 0.0, "embedding: values used as they are"
    if "reflectance_scale" in tags:  # written by the .SAFE importer
        sc, off = float(tags["reflectance_scale"]), float(tags.get("reflectance_offset", 0))
        preset = next((k for k, (_, s, o) in SCALE_PRESETS.items() if abs(s - sc) < 1e-12 and abs(o - off) < 1e-12), "")
        return preset, sc, off, f"product metadata: reflectance = DN × {sc:g} + {off:g}"
    if "db" in units.split() or units.endswith(" db"):
        return "asis", 1.0, 0.0, "radar backscatter in dB, used as-is"
    if "reflectance" in units:
        return "reflectance", 1.0, 0.0, "file metadata says surface reflectance"
    if any((d or "").upper().startswith("SR_B") for d in src.descriptions):
        return "landsat", 2.75e-5, -0.2, "Landsat SR band names"
    if src.dtypes[0] == "uint8":
        return "byte", 1 / 255, 0.0, "8-bit data"
    idx = list(band_map.values())[:4] or [1]
    sample = _read(src, idx, max_px=256)
    finite = sample[np.isfinite(sample) & (sample != 0)]
    p99 = float(np.percentile(finite, 99)) if finite.size else 1.0
    if p99 <= 1.5:
        return "reflectance", 1.0, 0.0, f"values already 0–1 (99th percentile {p99:.2f})"
    if finite.max() <= 255:
        return "byte", 1 / 255, 0.0, f"values within 0–255 (max {finite.max():.0f}) — assumed 8-bit"
    return "s2dn", 1e-4, 0.0, f"integer-like values (99th percentile {p99:.0f}) — assumed ×10000"


def _read_strips(src, indexes: list[int], max_px: int, window: Window | None) -> np.ndarray:
    """Many bands of a pixel-interleaved file (embeddings: every block holds all 64 / 128 bands), decimated: read in
    strips with all bands at once, so each block is decompressed once, and averaged over k × k pixels. GDAL's own
    decimated read decompresses every block again for each band (minutes instead of seconds for a 2000 × 2000 × 64
    embedding)."""
    import warnings

    from rasterio.windows import Window as W
    c0, r0, w, h = (int(window.col_off), int(window.row_off), int(window.width), int(window.height)) if window else (0, 0, src.width, src.height)
    k = int(np.ceil(max(h, w) / max_px))
    oh, ow = -(-h // k), -(-w // k)
    out = np.empty((len(indexes), oh, ow), np.float32)
    strip = max(k, (src.block_shapes[0][0] // k) * k or k)
    for y in range(0, h, strip):
        sh = min(strip, h - y)
        d = src.read(indexes, window=W(c0, r0 + y, w, sh), masked=True).astype("float32").filled(np.nan)
        ph, pw = -(-sh // k) * k, ow * k
        if (ph, pw) != (sh, w):   # pad the last partial cells with NaN: they average what is there
            d = np.pad(d, ((0, 0), (0, ph - sh), (0, pw - w)), constant_values=np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN cells stay NaN
            out[:, y // k:y // k + ph // k] = np.nanmean(d.reshape(len(indexes), ph // k, k, ow, k), axis=(2, 4))
    return out


_STRIPS: dict = {}   # the last few all-band reads (a redraw, the band statistics, … don't read the file again)


def _many_bands(src) -> bool:
    """A file whose blocks each hold many bands (pixel interleaved, e.g. an embedding): read all bands in one pass."""
    return src.count >= 8 and getattr(src.interleaving, "name", "").lower() == "pixel"


def _read_strips_cached(src, indexes, max_px, window):
    """All bands of the file in one pass (cached), then the bands asked for."""
    import os
    allb = list(range(1, src.count + 1))
    try:
        st = os.stat(src.name)
        key = (src.name, st.st_mtime_ns, st.st_size, max_px, tuple(window.flatten()) if window else None)
    except OSError:
        return _read_strips(src, indexes, max_px, window)
    if key not in _STRIPS:
        while len(_STRIPS) >= 3:
            _STRIPS.pop(next(iter(_STRIPS)))
        _STRIPS[key] = _read_strips(src, allb, max_px, window)
    return _STRIPS[key][[i - 1 for i in indexes]].copy()


def _read(src, indexes: list[int], max_px: int | None = None, window: Window | None = None) -> np.ndarray:
    """Read bands as float32 with NaN nodata, optionally decimated so the longest side <= max_px."""
    h, w = (window.height, window.width) if window else (src.height, src.width)
    out_shape = None
    if max_px and max(h, w) > max_px:
        if _many_bands(src):
            return _read_strips_cached(src, indexes, max_px, window)
        f = max_px / max(h, w)
        out_shape = (len(indexes), max(1, int(h * f)), max(1, int(w * f)))
    data = src.read(indexes, window=window, out_shape=out_shape, masked=True,
                    resampling=Resampling.average if out_shape else Resampling.nearest)
    return data.astype("float32").filled(np.nan)


def _pca3(data: np.ndarray, sample: int = 20000) -> list[np.ndarray]:
    """The three main directions of variation of a (bands, rows, cols) stack: for showing an embedding (or any many-band
    image) in colour. Signs are fixed (largest loading positive) so the colours don't flip between redraws."""
    n, h, w = data.shape
    flat = data.reshape(n, -1)
    ok = np.isfinite(flat).all(axis=0)
    out = [np.full(h * w, np.nan, np.float32) for _ in range(3)]
    if ok.sum() >= 3:
        x = flat[:, ok].T.astype(np.float64)
        rng = np.random.default_rng(0)
        fit = x[rng.choice(len(x), min(sample, len(x)), replace=False)]
        mean = fit.mean(axis=0)
        _, _, vt = np.linalg.svd(fit - mean, full_matrices=False)
        comps = vt[:3]
        for i in range(len(comps)):
            if comps[i][np.abs(comps[i]).argmax()] < 0:
                comps[i] = -comps[i]
        proj = (x - mean) @ comps.T
        for i in range(proj.shape[1]):
            out[i][ok] = proj[:, i]
    return [o.reshape(h, w) for o in out]


def is_embedding(src) -> dict | None:
    """Is this raster a pixel embedding (AlphaEarth, TESSERA or similar)? Cheap: tags, band names, band count."""
    import re as _re
    tags = src.tags()
    names = [(d or "").upper() for d in src.descriptions]
    named = sum(bool(_re.fullmatch(r"(A\d{2}|E\d{2,3}|EMB.*|DIM.*)", nm)) for nm in names)
    if tags.get("embedding") or tags.get("embedding_quantization") or (src.count >= 16 and named > src.count / 2) \
            or (src.count in (64, 128) and src.dtypes[0] in ("int8", "float16", "float32")):
        return {"dims": src.count, "title": tags.get("embedding_title") or ("AlphaEarth" if src.count == 64 else "TESSERA" if src.count == 128 else "Embedding"),
                "first": src.descriptions[0] or "1", "last": src.descriptions[-1] or str(src.count)}
    return None


def inspect(path: str | Path) -> dict:
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError("This GeoTIFF has no coordinate system, so it can't be placed on the map")
        band_map, how = detect_band_map(src.descriptions, src.count)
        preset, scale, offset, reason = detect_scale(src, band_map)
        w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
        return {
            "path": str(path), "name": Path(path).name, "width": src.width, "height": src.height,
            "count": src.count, "dtype": src.dtypes[0], "crs": src.crs.to_string(),
            "res": [abs(src.res[0]), abs(src.res[1])],
            "nodata": None if src.nodata is None or np.isnan(src.nodata) else src.nodata,
            "bounds": [[s, w], [n, e]], "size_mb": Path(path).stat().st_size / 1e6,
            "bands": _band_stats(src),
            "band_map": band_map, "band_map_source": how,
            "scale_preset": preset, "scale": scale, "offset": offset, "scale_reason": reason,
            "tags": {k: v for k, v in src.tags().items() if len(v) < 300},
            "rgb": _is_rgb(src),
            "embedding": is_embedding(src),
        }


def metadata(path: str | Path, max_px: int = 512) -> dict:
    """Everything worth knowing about a raster file, for the Metadata dialog."""
    import re as _re

    path = Path(path)
    with rasterio.open(path) as src:
        band_map, how = detect_band_map(src.descriptions, src.count)
        mapped = {v: k for k, v in band_map.items()}
        w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21) if src.crs else (None,) * 4
        crs = src.crs
        units = None
        if crs:
            try:
                units = crs.linear_units if not crs.is_geographic else "degree"
            except Exception:
                units = None
        bands = []
        stats = _read(src, list(range(1, src.count + 1)), max_px=max_px) if src.count <= 64 else None
        for i in range(1, src.count + 1):
            b = {"index": i, "description": src.descriptions[i - 1] or "", "mapped_as": mapped.get(i),
                 "dtype": src.dtypes[i - 1], "nodata": None if src.nodatavals[i - 1] is None or np.isnan(src.nodatavals[i - 1]) else src.nodatavals[i - 1],
                 "scale": src.scales[i - 1], "offset": src.offsets[i - 1], "unit": src.units[i - 1] or None,
                 "color": src.colorinterp[i - 1].name, "tags": {k: v for k, v in src.tags(i).items() if len(v) < 300}}
            if stats is not None:
                v = stats[i - 1][np.isfinite(stats[i - 1])]
                if v.size:
                    b.update(min=float(v.min()), max=float(v.max()), mean=float(v.mean()), std=float(v.std()),
                             p2=float(np.percentile(v, 2)), p98=float(np.percentile(v, 98)), valid_pct=100 * v.size / stats[i - 1].size)
            try:
                b["has_colormap"] = bool(src.colormap(i))
            except ValueError:
                b["has_colormap"] = False
            bands.append(b)
        prof = src.profile
        out = {
            "file": str(path), "name": path.name, "driver": src.driver, "size_mb": round(path.stat().st_size / 1e6, 3),
            "width": src.width, "height": src.height, "count": src.count, "dtype": src.dtypes[0],
            "nodata": None if src.nodata is None or np.isnan(src.nodata) else src.nodata,
            "compression": src.compression.name if src.compression else None, "interleave": src.interleaving.name if src.interleaving else None,
            "block_size": list(src.block_shapes[0]) if src.block_shapes else None, "tiled": bool(prof.get("tiled")),
            "overviews": src.overviews(1) if src.count else [],
            "crs": crs.to_string() if crs else None, "crs_name": (crs.to_wkt().split('"')[1] if crs and '"' in crs.to_wkt() else None),
            "epsg": crs.to_epsg() if crs else None, "units": units,
            "pixel_size": [abs(src.res[0]), abs(src.res[1])], "origin": [src.transform.c, src.transform.f],
            "bounds": {"left": src.bounds.left, "bottom": src.bounds.bottom, "right": src.bounds.right, "top": src.bounds.top},
            "bounds_lonlat": {"west": w, "south": s, "east": e, "north": n},
            "transform": list(src.transform)[:6],
            "band_map_source": how, "bands": bands,
            "tags": {k: v for k, v in src.tags().items() if len(v) < 2000},
            "stats_note": f"Statistics from a {max_px}-pixel overview" if stats is not None else "Statistics skipped (more than 64 bands)",
        }
        if path.suffix.lower() == ".vrt":
            out["sources"] = len(src.files) - 1
        for dom in ("IMAGE_STRUCTURE",):
            md = src.tags(ns=dom)
            if md:
                out[dom.lower()] = md
    # Sentinel products opened from a .SAFE (imports/<product>/…)
    from .safe import product_name
    for part in path.parts:
        if product_name(part):
            m = _re.match(r"(S2[ABCD])_MSI(L1C|L2A)_(\d{8})T(\d{6})_N(\d{4})_R(\d{3})_T(\w{5})_", part)
            if m:
                sat, lvl, d, t, base, orbit, tile = m.groups()
                out["product"] = {"product": part, "satellite": sat, "level": lvl, "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                                  "time_utc": f"{t[:2]}:{t[2:4]}:{t[4:]}", "tile": tile, "relative_orbit": orbit,
                                  "processing_baseline": f"{base[:2]}.{base[2:]}"}
            else:
                out["product"] = {"product": part}
            break
    return out


def _is_rgb(src) -> bool:
    """A true-colour picture (e.g. a drone / aerial photo or a georeferenced JPG): show it as it is."""
    from rasterio.enums import ColorInterp
    if src.count < 3 or src.dtypes[0] not in ("uint8", "uint16"):
        return False
    ci = list(src.colorinterp[:3])
    if ci == [ColorInterp.red, ColorInterp.green, ColorInterp.blue]:
        return True
    names = [(d or "").lower() for d in src.descriptions[:3]]
    return names == ["red", "green", "blue"] or (src.dtypes[0] == "uint8" and src.count in (3, 4) and not any(names))


def _band_stats(src, max_px: int = 256) -> list[dict]:
    """Per-band raw median / 98th percentile from a small overview. Used as hints when assigning bands.

    Bands are read in parallel (one dataset handle per thread), which matters for JPEG2000 / VRT sources.
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(i):
        with rasterio.open(src.name) as ds:
            return _read(ds, [i], max_px=max_px)[0]

    if _many_bands(src):   # one pass over the file instead of one per band
        sample = list(_read(src, list(range(1, src.count + 1)), max_px=max_px))
    else:
        with ThreadPoolExecutor(min(8, src.count)) as ex:
            sample = list(ex.map(one, range(1, src.count + 1)))
    out = []
    for i, (desc, a) in enumerate(zip(src.descriptions, sample), start=1):
        v = a[np.isfinite(a)]
        out.append({"index": i, "description": desc or f"Band {i}",
                    "median": float(np.median(v)) if v.size else None,
                    "p98": float(np.percentile(v, 98)) if v.size else None})
    return out


def _bands(src, band_map: dict[str, int], needed: set[str], scale: float, offset: float,
           max_px: int | None = None, window: Window | None = None) -> dict[str, np.ndarray]:
    missing = sorted(needed - set(band_map))
    if missing:
        raise ValueError(f"This image has no {', '.join(missing)} band (or it isn't mapped). "
                         f"Check the band mapping.")
    names = sorted(needed)
    data = _read(src, [band_map[n] for n in names], max_px=max_px, window=window)
    # Pixels that are 0 in every band are fill, not data (common in uint images without nodata).
    if src.nodata is None and np.issubdtype(np.dtype(src.dtypes[0]), np.integer):
        data[:, np.all(data == 0, axis=0)] = np.nan
    out = {}
    for i, n in enumerate(names):
        v = data[i] * scale + offset
        # Optical reflectance can't be negative (dark pixels go slightly below 0 after the S2 −0.1 offset),
        # and negatives make normalized differences explode. Radar dB values are left as they are.
        out[n] = np.maximum(v, 0) if n in S2_NAMES else v
    return out


def _formula(index: str | None, formula: str | None) -> tuple[str, str]:
    if index:
        if index not in CATALOG:
            raise ValueError(f"Unknown index {index}")
        return index, CATALOG[index].formula
    if not formula:
        raise ValueError("Choose an index or enter a formula")
    return "custom", formula


def colorize(values: np.ndarray, vmin: float, vmax: float, cmap: str) -> np.ndarray:
    stops = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in COLORMAPS[cmap]], dtype="float32")
    t = np.clip((values - vmin) / max(vmax - vmin, 1e-12), 0, 1)
    pos = np.nan_to_num(t, nan=0) * (len(stops) - 1)
    lo = np.floor(pos).astype(int).clip(0, len(stops) - 2)
    frac = (pos - lo)[..., None]
    rgb = stops[lo] * (1 - frac) + stops[lo + 1] * frac
    rgba = np.zeros((4, *values.shape), "uint8")
    rgba[:3] = np.moveaxis(rgb, -1, 0).astype("uint8")
    rgba[3] = np.isfinite(values) * 255
    return rgba


def _palette(src, band: int):
    """(colormap {value: rgba}, names {value: label}) for a paletted class band, else None."""
    if not np.issubdtype(np.dtype(src.dtypes[band - 1]), np.integer):
        return None
    try:
        cmap = src.colormap(band)
    except ValueError:
        return None
    try:
        names = {int(k): v for k, v in json.loads(src.tags().get("classes", "{}")).items()}
    except (ValueError, AttributeError):
        names = {}
    return cmap, names


def clip_region(src, clip: dict | None):
    """Pixel window covering a clip polygon (GeoJSON, EPSG:4326) and the polygon in the file's CRS.

    Returns (None, None) when there is no clip. Only the window is read, so clipping also makes large
    images faster to process.
    """
    if not clip:
        return None, None
    import math

    from rasterio.warp import transform_geom
    from shapely.geometry import shape

    geom = transform_geom("EPSG:4326", src.crs, clip)
    minx, miny, maxx, maxy = shape(geom).bounds
    inv = ~src.transform
    cols, rows = zip(*[inv * (x, y) for x, y in ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy))])
    c0, c1 = max(0, math.floor(min(cols))), min(src.width, math.ceil(max(cols)))
    r0, r1 = max(0, math.floor(min(rows))), min(src.height, math.ceil(max(rows)))
    if c1 <= c0 or r1 <= r0:
        raise ValueError("The selected area doesn't overlap this layer")
    return Window(c0, r0, c1 - c0, r1 - r0), geom


def _grid_transform(src, win, shape):
    """Affine transform of an array of `shape` read from window `win` (or the whole file)."""
    base = src.window_transform(win) if win is not None else src.transform
    full_w, full_h = (win.width, win.height) if win is not None else (src.width, src.height)
    h, w = shape
    return base * base.scale(full_w / w, full_h / h)


def _mask_outside(arrays, geom, transform):
    """Set pixels outside the clip polygon to NaN (in place)."""
    if geom is None:
        return
    from rasterio.features import geometry_mask

    inside = geometry_mask([geom], out_shape=arrays[0].shape, transform=transform, invert=True)
    for a in arrays:
        a[~inside] = np.nan


def _layer_values(src, spec: dict, max_px: int | None):
    """Single-value-per-pixel array for an index / formula / band layer, limited to the optional clip.

    Returns (values, name, formula, transform of the values grid).
    """
    win, geom = clip_region(src, spec.get("clip"))
    if spec.get("band"):
        b = int(spec["band"])
        if not 1 <= b <= src.count:
            raise ValueError(f"This file has no band {b}")
        values, name, expr = _read(src, [b], max_px=max_px, window=win)[0], src.descriptions[b - 1] or f"Band {b}", None
    else:
        name, expr = _formula(spec.get("index"), spec.get("formula"))
        bands = _bands(src, spec["band_map"], required_bands(expr), spec["scale"], spec["offset"], max_px=max_px, window=win)
        values = evaluate(expr, bands)
    transform = _grid_transform(src, win, values.shape)
    _mask_outside([values], geom, transform)
    return values, name, expr, transform


def _render_native(src, spec: dict, max_px: int | None):
    """Colour image of a layer on the file's own grid (decimated so the long side <= max_px).

    Returns (rgba uint8 [4, h, w], affine transform of that grid, metadata for legends).
    """
    if spec.get("composite") or spec.get("rgb") or spec.get("pca"):
        win, geom = clip_region(src, spec.get("clip"))
        if spec.get("pca"):   # all bands → their three main directions of variation as red, green, blue (embeddings)
            if src.count < 3:
                raise ValueError("A colour view (PCA) needs at least 3 bands")
            data = _read(src, list(range(1, src.count + 1)), max_px=min(max_px, 900 if src.count <= 64 else 640), window=win)
            arrays = _pca3(data)
            names = ["PC1", "PC2", "PC3"]
            title = f"Colour view: PCA of {src.count} bands"
        elif spec.get("rgb"):  # any three file bands, e.g. PC1 / PC2 / PC3
            idx = [int(b) for b in spec["rgb"]][:3]
            if len(idx) != 3 or not all(1 <= b <= src.count for b in idx):
                raise ValueError("RGB display needs three valid band numbers")
            arrays = list(_read(src, idx, max_px=max_px, window=win))
            names = [src.descriptions[b - 1] or f"Band {b}" for b in idx]
            title = "RGB " + " / ".join(names)
        else:
            title, names = COMPOSITES[spec["composite"]]
            bands = _bands(src, spec["band_map"], set(names), spec["scale"], spec["offset"], max_px=max_px, window=win)
            arrays = [bands[n] for n in names]
        transform = _grid_transform(src, win, arrays[0].shape)
        natural = spec.get("rgb") and spec.get("stretch") == "none"  # photo colours: no contrast stretch
        if natural and src.count >= 4 and src.colorinterp[3].name == "alpha":
            alpha = _read(src, [4], max_px=max_px, window=win)[0]
            for a in arrays:
                a[~(alpha > 0)] = np.nan
        _mask_outside(arrays, geom, transform)
        valid = np.all([np.isfinite(a) for a in arrays], axis=0)
        rgba = np.zeros((4, *arrays[0].shape), "uint8")
        for i, a in enumerate(arrays):
            if natural:
                lo, hi = 0, (255 if src.dtypes[0] == "uint8" else float(np.nanmax(a)) if valid.any() else 1)
            else:   # contrast stretch: 2–98 % (default), 1–99 % or min–max of the visible pixels
                pct = {"p1": (1, 99), "minmax": (0, 100)}.get(spec.get("stretch"), (2, 98))
                lo, hi = np.percentile(a[valid], pct) if valid.any() else (0, 1)
            rgba[i] = (np.clip((np.nan_to_num(a, nan=lo) - lo) / max(hi - lo, 1e-9), 0, 1) * 255).astype("uint8")
        rgba[3] = valid * 255
        meta = {"kind": "rgb", "title": title, "bands": list(names)}
    else:
        values, name, expr, transform = _layer_values(src, spec, max_px)
        pal = _palette(src, int(spec["band"])) if spec.get("band") else None
        if pal:
            cmap, names = pal
            ints = np.nan_to_num(values, nan=-1).astype("int64")
            rgba = np.zeros((4, *values.shape), "uint8")
            codes, counts = np.unique(ints[ints >= 0], return_counts=True)
            total = max(int(counts.sum()), 1)
            classes = []
            for code, n in zip(codes, counts):
                color = cmap.get(int(code), (128, 128, 128, 255))
                rgba[:, ints == code] = np.array(color, "uint8")[:, None]
                classes.append({"value": int(code), "name": names.get(int(code), f"Class {int(code)}"),
                                "color": "#%02x%02x%02x" % tuple(color[:3]), "pct": 100 * int(n) / total})
            classes.sort(key=lambda c: -c["pct"])
            meta = {"kind": "classes", "title": name, "classes": classes}
        else:
            idx = CATALOG.get(name)
            stretch, vmin, vmax = spec.get("stretch", "fixed"), spec.get("vmin"), spec.get("vmax")
            finite = values[np.isfinite(values)]
            if stretch == "custom" and vmin is not None and vmax is not None:
                lo, hi = vmin, vmax
            elif stretch == "fixed" and idx:
                lo, hi = idx.vmin, idx.vmax
            else:
                lo, hi = np.percentile(finite, [2, 98]) if finite.size else (0.0, 1.0)
            hi = hi if hi > lo else lo + 1e-6
            cmap = spec.get("cmap") if spec.get("cmap") in COLORMAPS else (idx.cmap if idx else ("Greys" if spec.get("band") else "Viridis"))
            rgba = colorize(values, lo, hi, cmap)
            meta = {"kind": "continuous", "title": name, "formula": expr, "vmin": float(lo), "vmax": float(hi),
                    "cmap": cmap, "colors": COLORMAPS[cmap], "stats": _stats(values, float(lo), float(hi))}
    meta["decimated"] = bool(transform.a > src.transform.a * 1.0001)
    meta["clipped"] = bool(spec.get("clip"))
    return rgba, transform, meta


def _rgba_to_web_mercator(rgba: np.ndarray, src_crs, src_transform, max_px: int):
    """Reproject an RGBA image to EPSG:3857 so a Leaflet image overlay lines up exactly."""
    h, w = rgba.shape[1:]
    left, top = src_transform * (0, 0)
    right, bottom = src_transform * (w, h)
    dst_transform, dw, dh = calculate_default_transform(src_crs, "EPSG:3857", w, h,
                                                        left=min(left, right), bottom=min(top, bottom),
                                                        right=max(left, right), top=max(top, bottom))
    f = min(1.0, max_px / max(dw, dh))
    if f < 1:
        dst_transform = dst_transform * dst_transform.scale(1 / f)
        dw, dh = max(1, int(dw * f)), max(1, int(dh * f))
    out = np.zeros((4, dh, dw), "uint8")
    for i in range(4):
        reproject(rgba[i], out[i], src_transform=src_transform, src_crs=src_crs, dst_transform=dst_transform,
                  dst_crs="EPSG:3857", resampling=Resampling.nearest)
    l_, t_ = dst_transform * (0, 0)
    r_, b_ = dst_transform * (dw, dh)
    w4, s4, e4, n4 = transform_bounds("EPSG:3857", "EPSG:4326", l_, b_, r_, t_)
    return out, [[s4, w4], [n4, e4]]


def _stats(values: np.ndarray, vmin: float, vmax: float, bins: int = 48) -> dict:
    v = values[np.isfinite(values)]
    if not v.size:
        raise ValueError("No valid pixels — the result is empty (check band mapping / scaling)")
    if not vmax - vmin > 1e-6 * max(1.0, abs(vmin), abs(vmax)):   # (about) one value everywhere: a small range around it
        pad = max(abs(vmin) * 1e-3, 1e-6)
        vmin, vmax = vmin - pad, vmax + pad
    counts, edges = np.histogram(np.clip(v, vmin, vmax), bins=bins, range=(vmin, vmax))
    p = np.percentile(v, [2, 25, 50, 75, 98])
    return {"count": int(v.size), "min": float(v.min()), "max": float(v.max()), "mean": float(v.mean()),
            "std": float(v.std()), "p2": float(p[0]), "p25": float(p[1]), "median": float(p[2]),
            "p75": float(p[3]), "p98": float(p[4]),
            "histogram": {"counts": counts.tolist(), "edges": edges.tolist()}}


def _spec(band_map, scale, offset, **kw) -> dict:
    return {"band_map": band_map or {}, "scale": scale, "offset": offset, **kw}


def render(path: str | Path, *, band_map: dict[str, int], scale: float, offset: float,
           index: str | None = None, formula: str | None = None, composite: str | None = None,
           band: int | None = None, rgb: list[int] | None = None, stretch: str = "fixed", vmin: float | None = None,
           vmax: float | None = None, cmap: str | None = None, clip: dict | None = None, max_px: int = 1400, pca: bool = False) -> dict:
    """Map-ready RGBA image (Web Mercator) of a composite / index / formula / single band, plus legend data.
    With `clip` (GeoJSON polygon, EPSG:4326) only that area is read, shown and counted in the stats."""
    spec = _spec(band_map, scale, offset, index=index, formula=formula, composite=composite, band=band, rgb=rgb,
                 stretch=stretch, vmin=vmin, vmax=vmax, cmap=cmap, clip=clip, pca=pca)
    with rasterio.open(path) as src:
        rgba, transform, meta = _render_native(src, spec, max_px)
        merc, bounds = _rgba_to_web_mercator(rgba, src.crs, transform, max_px)
    return {"rgba": merc, "bounds": bounds, **meta}


def elevation_grid(path: str | Path, *, band: int = 1, scale: float = 1.0, offset: float = 0.0, max_px: int = 300) -> dict:
    """One band's values (heights of a DEM) on a Web Mercator grid of at most max_px a side, for 3D maps.
    Row 0 is the north edge, nodata is NaN. Returns the values (float32, little-endian, base64), the grid's Web Mercator
    box [left, bottom, right, top], its bounds as [[south, west], [north, east]] and the range of the values."""
    import base64

    with rasterio.open(path) as src:
        if not 1 <= band <= src.count:
            raise ValueError(f"The file has {src.count} band(s); there is no band {band}")
        if src.crs is None:
            raise ValueError("The file has no coordinate system, so it can't be placed in 3D")
        data = _read(src, [band], max_px * 2)[0] * scale + offset
        h, w = data.shape
        transform = src.transform * src.transform.scale(src.width / w, src.height / h)
        left, top = transform * (0, 0)
        right, bottom = transform * (w, h)
        dst, dw, dh = calculate_default_transform(src.crs, "EPSG:3857", w, h, left=min(left, right), bottom=min(top, bottom),
                                                  right=max(left, right), top=max(top, bottom))
        f = min(1.0, max_px / max(dw, dh))
        if f < 1:
            dst = dst * dst.scale(1 / f)
            dw, dh = max(2, int(dw * f)), max(2, int(dh * f))
        out = np.full((dh, dw), np.nan, "float32")
        reproject(data.astype("float32"), out, src_transform=transform, src_crs=src.crs, dst_transform=dst, dst_crs="EPSG:3857",
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
    l_, t_ = dst * (0, 0)
    r_, b_ = dst * (dw, dh)
    w4, s4, e4, n4 = transform_bounds("EPSG:3857", "EPSG:4326", l_, b_, r_, t_)
    v = out[np.isfinite(out)]
    return {"width": int(dw), "height": int(dh), "merc": [l_, b_, r_, t_], "bounds": [[s4, w4], [n4, e4]],
            "min": float(v.min()) if v.size else None, "max": float(v.max()) if v.size else None,
            "values": base64.b64encode(out.astype("<f4").tobytes()).decode()}


def profile(path: str | Path, coords: list, *, band: int = 1, samples: int = 256) -> dict:
    """One band's values (a DEM's heights) along a line of lon/lat points: `samples` points evenly spaced along it by
    great-circle distance (metres). Values are as stored in the file; nodata or outside the raster is None."""
    import math

    pts = [(float(x), float(y)) for x, y in coords]
    if len(pts) < 2:
        raise ValueError("A profile needs a line of at least 2 points")

    def dist(a, b):
        la1, la2 = math.radians(a[1]), math.radians(b[1])
        h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
        return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))

    seg = [dist(pts[i - 1], pts[i]) for i in range(1, len(pts))]
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    d = np.linspace(0, total, max(2, min(int(samples), 2000)))
    lons = np.interp(d, cum, [p[0] for p in pts])
    lats = np.interp(d, cum, [p[1] for p in pts])
    with rasterio.open(path) as src:
        if not 1 <= band <= src.count:
            raise ValueError(f"The file has {src.count} band(s); there is no band {band}")
        if src.crs is None:
            raise ValueError("The file has no coordinate system")
        xs, ys = warp_transform("EPSG:4326", src.crs, lons.tolist(), lats.tolist())
        vals = []
        for v in src.sample(zip(xs, ys), indexes=band, masked=True):
            vals.append(None if np.ma.is_masked(v[0]) or not np.isfinite(float(v[0])) else float(v[0]))
    return {"length": total, "distance": d.tolist(), "lon": lons.tolist(), "lat": lats.tolist(), "value": vals}


def export_png(path: str | Path, out: str | Path, *, world_file: bool = False, max_px: int = 8192, **spec) -> Path:
    """Write the layer as it is displayed, on the file's own grid. With `world_file`, returns a .zip
    holding the PNG plus .pgw / .prj so GIS software can place it."""
    import zipfile

    from rasterio.enums import WktVersion
    from rasterio.errors import NotGeoreferencedWarning

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    progress.update(0.05, "Rendering the image")
    with rasterio.open(path) as src:
        rgba, t, _ = _render_native(src, _spec(**spec), max_px)
        crs = src.crs
    progress.update(0.8, "Writing PNG")
    png = out.with_suffix(".png")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(png, "w", driver="PNG", width=rgba.shape[2], height=rgba.shape[1], count=4, dtype="uint8") as dst:
            dst.write(rgba)
    for extra in png.parent.glob(png.name + ".aux.xml"):
        extra.unlink()
    if not world_file:
        return png
    # world file: pixel size x, rotation, rotation, pixel size y, centre of the upper-left pixel
    pgw = f"{t.a}\n{t.d}\n{t.b}\n{t.e}\n{t.c + t.a / 2 + t.b / 2}\n{t.f + t.d / 2 + t.e / 2}\n"
    zpath = out.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(png, png.name)
        z.writestr(png.stem + ".pgw", pgw)
        z.writestr(png.stem + ".prj", crs.to_wkt(version=WktVersion.WKT1_ESRI))
    png.unlink()
    return zpath


def export_layer_tif(path: str | Path, out: str | Path, *, rows_per_chunk: int = 512, **spec) -> Path:
    """GeoTIFF of a layer: index/formula values, one band (keeping its colour table), or a composite's 3 bands."""
    spec = _spec(**spec)
    if spec.get("index") or spec.get("formula"):
        name, expr = _formula(spec.get("index"), spec.get("formula"))
        return export(path, out, [(name, expr)], band_map=spec["band_map"], scale=spec["scale"], offset=spec["offset"],
                      clip=spec.get("clip"))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path) as src:
        region, geom = clip_region(src, spec.get("clip"))
        region = region or Window(0, 0, src.width, src.height)
        grid = {"width": region.width, "height": region.height, "transform": src.window_transform(region)}
        if spec.get("band"):
            b = int(spec["band"])
            nodata = src.nodata if src.nodata is not None else (np.nan if np.issubdtype(np.dtype(src.dtypes[b - 1]), np.floating) else 0)
            profile = {**src.profile, **grid, "count": 1, "driver": "GTiff", "compress": "deflate", "nodata": nodata}
            profile.pop("blockxsize", None); profile.pop("blockysize", None); profile.pop("tiled", None)
            with rasterio.open(out, "w", **profile) as dst:
                for r0 in range(0, region.height, rows_per_chunk):
                    progress.update(r0 / region.height, f"Writing band {b} ({r0 / region.height:.0%})")
                    win = Window(0, r0, region.width, min(rows_per_chunk, region.height - r0))
                    data = src.read(b, window=Window(region.col_off, region.row_off + r0, win.width, win.height))
                    if geom is not None:
                        from rasterio.features import geometry_mask
                        inside = geometry_mask([geom], out_shape=data.shape, transform=dst.window_transform(win), invert=True)
                        data[~inside] = nodata
                    dst.write(data, 1, window=win)
                dst.set_band_description(1, src.descriptions[b - 1] or f"Band {b}")
                if _palette(src, b):
                    dst.write_colormap(1, src.colormap(b))
                dst.update_tags(**src.tags())
            return out
        if spec.get("rgb") or spec.get("pca"):   # shown from 3 bands (or their PCA), exported with all of them
            profile = {"driver": "GTiff", **grid, "count": src.count, "dtype": "float32",
                       "crs": src.crs, "nodata": np.nan, "compress": "deflate", "predictor": 3, "BIGTIFF": "IF_SAFER"}
            with rasterio.open(out, "w", **profile) as dst:
                for r0 in range(0, region.height, rows_per_chunk):
                    progress.update(r0 / region.height, f"Writing all bands ({r0 / region.height:.0%})")
                    win = Window(0, r0, region.width, min(rows_per_chunk, region.height - r0))
                    data = src.read(window=Window(region.col_off, region.row_off + r0, win.width, win.height),
                                    masked=True).astype("float32").filled(np.nan)
                    layers = list(data)
                    _mask_outside(layers, geom, dst.window_transform(win))
                    dst.write(np.stack(layers), window=win)
                for i, d in enumerate(src.descriptions, start=1):
                    dst.set_band_description(i, d or f"Band {i}")
                dst.update_tags(**src.tags())
            return out
        if spec.get("composite"):
            title, names = COMPOSITES[spec["composite"]]
            profile = {"driver": "GTiff", **grid, "count": 3, "dtype": "float32",
                       "crs": src.crs, "nodata": np.nan, "compress": "deflate", "predictor": 3}
            with rasterio.open(out, "w", **profile) as dst:
                for r0 in range(0, region.height, rows_per_chunk):
                    progress.update(r0 / region.height, f"Writing {title} ({r0 / region.height:.0%})")
                    win = Window(0, r0, region.width, min(rows_per_chunk, region.height - r0))
                    bands = _bands(src, spec["band_map"], set(names), spec["scale"], spec["offset"],
                                   window=Window(region.col_off, region.row_off + r0, win.width, win.height))
                    arrays = [bands[n].astype("float32") for n in names]
                    _mask_outside(arrays, geom, dst.window_transform(win))
                    for i, a in enumerate(arrays, start=1):
                        dst.write(a, i, window=win)
                for i, n in enumerate(names, start=1):
                    dst.set_band_description(i, n)
                dst.update_tags(composite=title, units="surface reflectance (0-1)")
            return out
    raise ValueError("Nothing to export — choose an index, a band or a band combination")


def classify(values: np.ndarray, method: str = "equal", classes: int = 5, breaks=None,
             vmin: float | None = None, vmax: float | None = None) -> tuple[np.ndarray, list[dict]]:
    """Group continuous values into classes 1..n (0 = no data). Returns (class array, class table)."""
    finite = values[np.isfinite(values)]
    if not finite.size:
        raise ValueError("No valid pixels to classify")
    if method == "custom":
        edges = np.array(sorted(float(b) for b in (breaks or [])))
        if edges.size < 2:
            raise ValueError("Custom breaks need at least two values, e.g. -1, 0, 0.2, 0.5, 1")
    elif method == "quantile":
        edges = np.unique(np.percentile(finite, np.linspace(0, 100, classes + 1)))
    else:
        lo = vmin if vmin is not None else float(np.percentile(finite, 2))
        hi = vmax if vmax is not None else float(np.percentile(finite, 98))
        edges = np.linspace(lo, hi if hi > lo else lo + 1e-6, classes + 1)
    n = len(edges) - 1
    cls = (np.digitize(values, edges[1:-1]) + 1).astype("int32")  # out-of-range values join the end classes
    cls[~np.isfinite(values)] = 0
    f = lambda v: f"{v:.4g}"
    table = [{"value": i + 1, "name": f"{f(edges[i])} – {f(edges[i + 1])}", "min": float(edges[i]),
              "max": float(edges[i + 1])} for i in range(n)]
    return cls, table


def polygonize(path: str | Path, out_zip: str | Path, *, name: str = "layer", method: str = "equal",
               classes: int = 5, breaks=None, sieve_px: int = 8, max_px: int = 2000,
               max_features: int = 150_000, **spec) -> tuple[Path, int]:
    """Convert a layer to polygons (one per connected patch of the same class) and write a zipped shapefile.

    Class maps (e.g. WorldCover) keep their classes; continuous layers (indices) are first grouped into
    `classes` ranges. Large rasters are reduced to `max_px` and patches under `sieve_px` pixels are merged
    into their neighbours, which keeps the polygon count manageable.
    """
    from rasterio import features
    from shapely.geometry import shape

    from .vector_io import write_shapefile_zip

    spec = _spec(**spec)
    if spec.get("composite") or spec.get("rgb"):
        raise ValueError("A colour composite has no single value per pixel. Choose an index or a single band "
                         "to convert to polygons.")
    progress.update(0.05, "Reading values")
    with rasterio.open(path) as src:
        values, title, _, transform = _layer_values(src, spec, max_px)
        pal = _palette(src, int(spec["band"])) if spec.get("band") else None
        crs = src.crs
    progress.update(0.35, "Grouping values into classes")
    if pal:
        _, names = pal
        cls = np.nan_to_num(values, nan=0).astype("int32")
        table = {int(v): {"value": int(v), "name": names.get(int(v), f"Class {int(v)}"), "min": float(v), "max": float(v)}
                 for v in np.unique(cls[cls > 0])}
    else:
        cls, rows = classify(values, method, classes, breaks, spec.get("vmin"), spec.get("vmax"))
        table = {r["value"]: r for r in rows}
    valid = cls > 0
    if sieve_px and sieve_px > 1:
        cls = features.sieve(cls, size=int(sieve_px), mask=valid)
    feats = []
    progress.update(0.5, "Tracing polygons")
    for geom, v in features.shapes(cls, mask=cls > 0, transform=transform):
        if len(feats) % 2000 == 0:
            progress.update(0.5 + 0.4 * min(1, len(feats) / 40000), f"Tracing polygons ({len(feats):,})")
        g = shape(geom)
        row = table.get(int(v), {"value": int(v), "name": f"Class {int(v)}", "min": None, "max": None})
        props = {"class": int(v), "label": row["name"], "min_val": row["min"], "max_val": row["max"]}
        if crs.is_projected:
            props["area_m2"] = round(g.area, 2)
        feats.append((g, props))
        if len(feats) > max_features:
            raise ValueError(f"Too many polygons (> {max_features:,}). Use fewer classes or a larger "
                             f"'merge patches smaller than' value.")
    if not feats:
        raise ValueError("No polygons were produced (the layer has no valid pixels)")
    progress.update(0.92, "Writing the shapefile")
    out = write_shapefile_zip(feats, crs, out_zip, name)
    return out, len(feats)


def pixel(path: str | Path, lon: float, lat: float, *, band_map: dict[str, int], scale: float, offset: float,
          index: str | None = None, formula: str | None = None) -> dict:
    with rasterio.open(path) as src:
        (x,), (y,) = warp_transform("EPSG:4326", src.crs, [lon], [lat])
        row, col = src.index(x, y)
        if not (0 <= row < src.height and 0 <= col < src.width):
            return {"inside": False}
        window = Window(col, row, 1, 1)
        raw = src.read(window=window, masked=True)[:, 0, 0]
        out = {"inside": True, "row": int(row), "col": int(col),
               "raw": [{"band": i + 1, "description": src.descriptions[i] or f"Band {i + 1}",
                        "value": None if np.ma.is_masked(v) else float(v)} for i, v in enumerate(raw)]}
        refl = _bands(src, band_map, set(band_map), scale, offset, window=window) if band_map else {}
        out["reflectance"] = {k: (None if np.isnan(v[0, 0]) else float(v[0, 0])) for k, v in sorted(refl.items())}
        if index or formula:
            name, expr = _formula(index, formula)
            try:
                val = evaluate(expr, refl)[0, 0]
                out["value"] = None if np.isnan(val) else float(val)
            except ValueError:
                out["value"] = None
            out["name"] = name
        return out


def export(path: str | Path, out_path: str | Path, items: list[tuple[str, str]], *, band_map: dict[str, int],
           scale: float, offset: float, clip: dict | None = None, rows_per_chunk: int = 512) -> Path:
    """Write the given (name, formula) layers at full resolution as one float32 GeoTIFF.

    Works in row chunks, so memory stays bounded for large images. With `clip`, the output is cropped
    to the polygon's extent and pixels outside it are NaN.
    """
    if not items:
        raise ValueError("Select at least one index")
    needed = set().union(*(required_bands(f) for _, f in items))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path) as src:
        region, geom = clip_region(src, clip)
        region = region or Window(0, 0, src.width, src.height)
        profile = {"driver": "GTiff", "width": region.width, "height": region.height, "count": len(items),
                   "dtype": "float32", "crs": src.crs, "transform": src.window_transform(region), "nodata": np.nan,
                   "compress": "deflate", "predictor": 3, "BIGTIFF": "IF_SAFER"}
        if region.width >= 256 and region.height >= 256:
            profile.update(tiled=True, blockxsize=256, blockysize=256)
        with rasterio.open(out_path, "w", **profile) as dst:
            for r0 in range(0, region.height, rows_per_chunk):
                progress.update(r0 / region.height, f"Computing {', '.join(n for n, _ in items)[:60]} ({r0 / region.height:.0%})")
                win = Window(0, r0, region.width, min(rows_per_chunk, region.height - r0))
                bands = _bands(src, band_map, needed, scale, offset,
                               window=Window(region.col_off, region.row_off + r0, win.width, win.height))
                layers = [evaluate(formula, bands) for _, formula in items]
                _mask_outside(layers, geom, dst.window_transform(win))
                for i, layer in enumerate(layers, start=1):
                    dst.write(layer, i, window=win)
            for i, (name, formula) in enumerate(items, start=1):
                dst.set_band_description(i, name)
                dst.update_tags(i, formula=formula)
            dst.update_tags(source_image=Path(path).name, scale=scale, offset=offset)
    return out_path
