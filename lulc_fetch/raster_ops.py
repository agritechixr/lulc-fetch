"""Raster and terrain tools: slope, aspect and hillshade of a DEM, contour lines, reclassify, change detection between
two dates, and clip / mask by polygons. Results are GeoTIFFs (contours: GeoJSON lines)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from rasterio.warp import transform as warp_transform

from . import progress

MAX_PIXELS = 40_000_000   # bigger rasters: clip them first


def _read(path, band: int = 1):
    src = rasterio.open(path)
    if not 1 <= band <= src.count:
        src.close()
        raise ValueError(f"The raster has {src.count} band(s); there is no band {band}")
    if src.width * src.height > MAX_PIXELS:
        src.close()
        raise ValueError(f"The raster is too big for this ({src.width:,} × {src.height:,} pixels) → clip it to your area first")
    a = src.read(band, masked=True).astype("float64").filled(np.nan)
    return src, a


def _profile(src, dtype="float32", nodata=-9999.0, count=1):
    p = src.profile.copy()
    p.update(driver="GTiff", dtype=dtype, nodata=nodata, count=count, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    for k in ("photometric",):
        p.pop(k, None)
    return p


def _pixel_metres(src, rows: int):
    """The size of a pixel in metres, per row (a geographic raster's pixels narrow towards the poles)."""
    rx, ry = abs(src.transform.a), abs(src.transform.e)
    if src.crs and src.crs.is_geographic:
        lat = src.transform.f + src.transform.e * (np.arange(rows) + 0.5)
        return (rx * 111320.0 * np.cos(np.radians(lat)))[:, None], ry * 110574.0
    return rx, ry


# ------------------------------------------------------------------ terrain
def terrain(dem, out_dir: Path, *, products=("slope", "aspect", "hillshade"), azimuth: float = 315, altitude: float = 45, z_factor: float = 1.0) -> list[str]:
    """Slope (degrees), aspect (degrees clockwise from north; -1 where flat) and hillshade (0–255) of a DEM."""
    src, z = _read(dem)
    with src:
        z = z * z_factor
        dx, dy = _pixel_metres(src, z.shape[0])
        gy, gx = np.gradient(z)
        dzdx, dzdy = gx / dx, -gy / dy   # rows run south: north is up
        slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
        aspect = (np.degrees(np.arctan2(-dzdx, -dzdy)) + 360) % 360   # the direction the slope faces
        aspect[np.hypot(dzdx, dzdy) < 1e-9] = -1
        out = []
        stem = Path(dem).stem
        if "slope" in products:
            out.append(_write(src, out_dir / f"{stem}_slope.tif", slope, "Slope (degrees)"))
        if "aspect" in products:
            out.append(_write(src, out_dir / f"{stem}_aspect.tif", aspect, "Aspect (degrees from north)"))
        if "hillshade" in products:
            az, alt = math.radians(360 - azimuth + 90), math.radians(altitude)
            sl, asp = np.arctan(np.hypot(dzdx, dzdy)), np.arctan2(-dzdy, -dzdx)   # the usual formula's y runs south
            hs = 255 * (math.sin(alt) * np.cos(sl) + math.cos(alt) * np.sin(sl) * np.cos(az - asp))
            hs = np.clip(hs, 0, 255)
            hs[np.isnan(z)] = 0
            p = _profile(src, "uint8", 0)
            path = out_dir / f"{stem}_hillshade.tif"
            with rasterio.open(path, "w", **p) as d:
                d.write(hs.astype("uint8"), 1)
                d.set_band_description(1, "Hillshade")
            out.append(str(path))
    return out


def _write(src, path: Path, a: np.ndarray, desc: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **_profile(src)) as d:
        d.write(np.where(np.isfinite(a), a, -9999.0).astype("float32"), 1)
        d.set_band_description(1, desc)
    return str(path)


# ------------------------------------------------------------------ contours (marching squares, vectorised)
# the edges each of the 16 cases crosses (0 top, 1 right, 2 bottom, 3 left); saddles split by the centre value
_CASES = {1: [(3, 2)], 2: [(2, 1)], 3: [(3, 1)], 4: [(0, 1)], 6: [(0, 2)], 7: [(3, 0)], 8: [(3, 0)], 9: [(0, 2)], 11: [(0, 1)], 12: [(3, 1)],
          13: [(2, 1)], 14: [(3, 2)]}


def _segments(z: np.ndarray, level: float):
    a, b, c, d = z[:-1, :-1], z[:-1, 1:], z[1:, 1:], z[1:, :-1]   # top-left, top-right, bottom-right, bottom-left
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(c) & np.isfinite(d)
    case = ((a > level) * 8 + (b > level) * 4 + (c > level) * 2 + (d > level) * 1).astype(int)
    case[~ok] = 0
    rows, cols = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = lambda v1, v2: np.clip((level - v1) / (v2 - v1), 0, 1)
        edge = {0: (cols + t(a, b), rows), 1: (cols + 1, rows + t(b, c)), 2: (cols + t(d, c), rows + 1), 3: (cols, rows + t(a, d))}
    centre = (a + b + c + d) / 4
    segs = []
    for k, pairs in list(_CASES.items()) + [(5, None), (10, None)]:
        m = case == k
        if not m.any():
            continue
        if pairs is None:   # saddles: which corners connect depends on the centre
            hi = centre[m] > level
            pairs_hi, pairs_lo = ([(3, 0), (2, 1)], [(3, 2), (0, 1)]) if k == 5 else ([(0, 1), (3, 2)], [(3, 0), (2, 1)])
            for sel, prs in ((hi, pairs_hi), (~hi, pairs_lo)):
                for e1, e2 in prs:
                    segs.append(np.stack([edge[e1][0][m][sel], edge[e1][1][m][sel], edge[e2][0][m][sel], edge[e2][1][m][sel]], 1))
            continue
        for e1, e2 in pairs:
            segs.append(np.stack([edge[e1][0][m], edge[e1][1][m], edge[e2][0][m], edge[e2][1][m]], 1))
    return np.concatenate(segs) if segs else np.zeros((0, 4))


def _join(segs: np.ndarray) -> list[list[tuple]]:
    """Segments joined end to end into lines."""
    key = lambda x, y: (round(float(x), 6), round(float(y), 6))
    ends: dict = {}
    for i, (x1, y1, x2, y2) in enumerate(segs):
        ends.setdefault(key(x1, y1), []).append((i, 0))
        ends.setdefault(key(x2, y2), []).append((i, 1))
    used, lines = np.zeros(len(segs), bool), []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        line = [tuple(segs[i][:2]), tuple(segs[i][2:])]
        for forward in (True, False):
            while True:
                tip = line[-1] if forward else line[0]
                nxt = next(((j, e) for j, e in ends.get(key(*tip), []) if not used[j]), None)
                if nxt is None:
                    break
                j, e = nxt
                used[j] = True
                other = tuple(segs[j][2:]) if e == 0 else tuple(segs[j][:2])
                line.append(other) if forward else line.insert(0, other)
        lines.append(line)
    return lines


def contours(dem, interval: float, *, base: float = 0.0, band: int = 1) -> dict:
    """Contour lines of a DEM (or any raster) every `interval`, as GeoJSON lines in EPSG:4326 with their `value`."""
    if not interval or interval <= 0:
        raise ValueError("Give the contour interval (e.g. 10 for every 10 m)")
    src, z = _read(dem, band)
    with src:
        lo, hi = np.nanmin(z), np.nanmax(z)
        if not np.isfinite(lo):
            raise ValueError("The raster has no values")
        levels = np.arange(math.ceil((lo - base) / interval) * interval + base, hi + 1e-9, interval)
        if len(levels) > 500:
            raise ValueError(f"That makes {len(levels)} contour levels → use a bigger interval")
        feats = []
        for n, lv in enumerate(levels):
            for line in _join(_segments(z, lv)):
                if len(line) < 2:
                    continue
                cols, rows = np.array([p[0] for p in line]) + 0.5, np.array([p[1] for p in line]) + 0.5   # pixel centres
                xs, ys = rasterio.transform.xy(src.transform, rows, cols, offset="ul")
                xs, ys = (np.asarray(xs), np.asarray(ys))
                if src.crs and src.crs.to_epsg() != 4326:
                    xs, ys = warp_transform(src.crs, "EPSG:4326", xs.tolist(), ys.tolist())
                feats.append({"type": "Feature", "properties": {"value": float(lv)},
                              "geometry": {"type": "LineString", "coordinates": [[round(x, 7), round(y, 7)] for x, y in zip(xs, ys)]}})
            progress.update((n + 1) / len(levels), f"Level {lv:g} ({n + 1} of {len(levels)})")
    return {"type": "FeatureCollection", "features": feats}


# ------------------------------------------------------------------ reclassify
PALETTE = ["#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#d73027", "#4575b4", "#91bfdb", "#a6611a", "#7b3294", "#636363", "#e7298a"]


def reclassify(raster, out: Path, rules: list[dict], *, band: int = 1) -> str:
    """Ranges of values become classes: rules [{min, max, value, label}] (min ≤ v < max; the last range includes its
    max). Values in no range become nodata (0). The result has a colour per class and the class names."""
    if not rules:
        raise ValueError("Give at least one range")
    src, a = _read(raster, band)
    with src:
        res = np.zeros(a.shape, "uint8")
        names, cmap = {}, {0: (0, 0, 0, 0)}
        for i, r in enumerate(rules):
            v = int(r.get("value", i + 1))
            if not 1 <= v <= 255:
                raise ValueError("Class values are 1 to 255")
            lo = -np.inf if r.get("min") is None else float(r["min"])
            hi = np.inf if r.get("max") is None else float(r["max"])
            inside = (a >= lo) & ((a < hi) if i < len(rules) - 1 else (a <= hi)) & np.isfinite(a)
            res[inside & (res == 0)] = v
            names[v] = str(r.get("label") or f"{'' if lo == -np.inf else f'{lo:g}'}–{'' if hi == np.inf else f'{hi:g}'}")
            col = PALETTE[i % len(PALETTE)].lstrip("#")
            cmap[v] = tuple(int(col[k:k + 2], 16) for k in (0, 2, 4)) + (255,)
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, "w", **_profile(src, "uint8", 0)) as d:
            d.write(res, 1)
            d.write_colormap(1, cmap)
            d.update_tags(classes=json.dumps(names))
            d.set_band_description(1, "Classes")
    return str(out)


# ------------------------------------------------------------------ change detection
def change(before, after, out_dir: Path, *, band: int = 1, categorical: bool = False) -> dict:
    """What changed from `before` to `after` (the after raster is put on the before's grid). Values: the difference
    (after − before) and the % change. Classes (categorical): a from→to map (from × 100 + to) and a table of the
    area (ha) of every change."""
    sb, a = _read(before, band)
    with sb, rasterio.open(after) as sa:
        b = np.full(a.shape, np.nan)
        reproject(sa.read(band, masked=True).astype("float64").filled(np.nan), b, src_transform=sa.transform, src_crs=sa.crs,
                  dst_transform=sb.transform, dst_crs=sb.crs, src_nodata=np.nan, dst_nodata=np.nan,
                  resampling=Resampling.nearest if categorical else Resampling.bilinear)
        stem = f"{Path(before).stem}_to_{Path(after).stem}"[:80]
        if not categorical:
            d = b - a
            with np.errstate(divide="ignore", invalid="ignore"):
                pct = np.where(np.abs(a) > 1e-12, 100 * d / np.abs(a), np.nan)
            v = d[np.isfinite(d)]
            return {"paths": [_write(sb, out_dir / f"{stem}_difference.tif", d, "After − before"),
                              _write(sb, out_dir / f"{stem}_pct_change.tif", pct, "% change")],
                    "summary": {"mean_change": float(v.mean()) if v.size else None, "pixels": int(v.size),
                                "increased_pct": round(100 * float((v > 0).mean()), 2) if v.size else None}}
        ok = np.isfinite(a) & np.isfinite(b)
        fa, fb = a[ok].astype(int), b[ok].astype(int)
        code = np.zeros(a.shape, "int32")
        code[ok] = fa * 100 + fb
        dx, dy = _pixel_metres(sb, a.shape[0])
        px_ha = float(np.mean(dx * dy)) / 1e4
        pairs, counts = np.unique(np.stack([fa, fb], 1), axis=0, return_counts=True)
        rows = sorted(({"from": int(p[0]), "to": int(p[1]), "pixels": int(n), "area_ha": round(n * px_ha, 3), "changed": bool(p[0] != p[1])}
                       for p, n in zip(pairs, counts)), key=lambda r: -r["pixels"])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{stem}_from_to.tif"
        with rasterio.open(path, "w", **_profile(sb, "int32", 0)) as d:
            d.write(code, 1)
            d.set_band_description(1, "From × 100 + to")
        changed = sum(r["pixels"] for r in rows if r["changed"])
        return {"paths": [str(path)], "transitions": rows[:200],
                "summary": {"changed_pct": round(100 * changed / max(1, int(ok.sum())), 2), "changed_ha": round(changed * px_ha, 3)}}


# ------------------------------------------------------------------ clip / mask by polygons
def clip_raster(raster, out: Path, geometry: dict, *, crop: bool = True, invert: bool = False) -> str:
    """The raster cut to polygons (EPSG:4326 GeoJSON): outside is nodata; crop shrinks it to their box; invert keeps
    the outside instead."""
    from rasterio.mask import mask
    from rasterio.warp import transform_geom
    with rasterio.open(raster) as src:
        if src.crs is None:
            raise ValueError("The raster has no coordinate system")
        geoms = [transform_geom("EPSG:4326", src.crs, g) for g in (
            [f["geometry"] for f in geometry["features"] if f.get("geometry")] if geometry.get("type") == "FeatureCollection" else [geometry])]
        nodata = src.nodata if src.nodata is not None else (0 if np.issubdtype(np.dtype(src.dtypes[0]), np.integer) else -9999.0)
        try:
            data, tr = mask(src, geoms, crop=crop and not invert, invert=invert, nodata=nodata, filled=True)
        except ValueError:
            raise ValueError("The polygons don't overlap the raster") from None
        prof = src.profile.copy()
        prof.update(driver="GTiff", height=data.shape[1], width=data.shape[2], transform=tr, nodata=nodata, compress="deflate")
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, "w", **prof) as d:
            d.write(data)
            for i in range(src.count):
                d.set_band_description(i + 1, src.descriptions[i] or f"Band {i + 1}")
            try:
                d.write_colormap(1, src.colormap(1))
            except ValueError:
                pass
            d.update_tags(**src.tags())
    return str(out)
