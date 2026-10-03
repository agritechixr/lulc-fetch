"""Download embeddings for an area: the output grid, the size estimate, which years exist, and the download itself.

The result is a float32 GeoTIFF (one band per dimension: A00–A63 or E000–E127) in the UTM zone of the area, north-up,
whatever the source's own storage.
"""

from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .. import progress
from . import alphaearth, tessera
from .sources import SOURCES, YEARS

log = logging.getLogger(__name__)


def _utm_crs(lon: float, lat: float) -> str:
    zone = int((lon + 180) // 6) % 60 + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def _grid(geom: dict, res: float):
    """The output grid for an area: its UTM zone, aligned to res metres."""
    from rasterio.transform import from_origin
    from rasterio.warp import transform_geom
    from shapely.geometry import shape

    g = shape(geom)
    c = g.centroid
    crs = _utm_crs(c.x, c.y)
    gu = shape(transform_geom("EPSG:4326", crs, geom))
    x0, y0, x1, y1 = gu.bounds
    x0, y0 = math.floor(x0 / res) * res, math.floor(y0 / res) * res
    x1, y1 = math.ceil(x1 / res) * res, math.ceil(y1 / res) * res
    w, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    return crs, from_origin(x0, y1, res, res), w, h, gu


def estimate(geom: dict, source: str, res: float) -> dict:
    """Size of the result and roughly how much is downloaded."""
    from shapely.geometry import shape
    crs, _, w, h, gu = _grid(geom, res)
    dims = SOURCES[source]["dims"]
    area = gu.area / 1e6
    if source == "aef":   # whole 1024 × 1024 blocks (at the overview level) are read: ~0.6 MB per block and dimension
        block_km = 10.24 * res / 10
        blocks = (math.ceil(math.sqrt(area) / block_km) + 1) ** 2
        dl = blocks * dims * 0.6
    else:                 # whole rows of 0.1° tiles: ~140 kB per row of 128 dims, plus scales
        b = shape(geom).bounds
        tiles_x = max(1, math.ceil((b[2] - b[0]) / 0.1 + 0.5))
        dl = (h * res / 10) * tiles_x * 0.145
    return {"crs": crs, "width": w, "height": h, "area_km2": round(area, 2), "output_mb": round(w * h * dims * 4 / 1e6, 1),
            "download_mb": round(dl)}



# ------------------------------------------------------------------ availability

def available(geom: dict, cache_dir: str | Path, sources=("aef", "tessera")) -> dict:
    """Per source and year: how many files / tiles cover the area, and how many exist."""
    out = {}
    if "aef" in sources:
        progress.update(0.05, "AlphaEarth: looking up the index")
        df = alphaearth.index(Path(cache_dir))
        from shapely.geometry import box, shape
        g = shape(geom)
        w, s, e, n = g.bounds
        c = df[(df.wgs84_west <= e) & (df.wgs84_east >= w) & (df.wgs84_south <= n) & (df.wgs84_north >= s)]
        c = c[[box(r.wgs84_west, r.wgs84_south, r.wgs84_east, r.wgs84_north).intersects(g) for r in c.itertuples()]]
        out["aef"] = {str(y): int((c.year == y).sum()) for y in YEARS}
    if "tessera" in sources:
        tiles = tessera.tiles(geom)
        sample = tiles if len(tiles) <= 25 else [tiles[i * len(tiles) // 25] for i in range(25)]
        jobs = [(t, y) for t in sample for y in YEARS]
        progress.update(0.4, f"TESSERA: checking {len(sample)} tile{'s' if len(sample) > 1 else ''} × {len(YEARS)} years")
        with ThreadPoolExecutor(24) as ex:
            ok = list(ex.map(lambda ty: tessera.exists(ty[0][0], ty[0][1], ty[1]), jobs))
        have = {str(y): sum(o for (t, yy), o in zip(jobs, ok) if yy == y) for y in YEARS}
        out["tessera"] = {y: round(len(tiles) * n / len(sample)) for y, n in have.items()}
        out["tessera_tiles"] = len(tiles)
        out["tessera_sampled"] = len(sample) < len(tiles)
    progress.update(1, "Done")
    return out


# ------------------------------------------------------------------ fetch → GeoTIFF

def fetch(geom: dict, source: str, year: int, out_path: str, cache_dir: str | Path, res: float = 10) -> dict:
    """Download the embeddings of an area for one year into a float32 GeoTIFF in the area's UTM zone."""
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import Resampling, reproject, transform_bounds
    from shapely.geometry import mapping, shape

    t0 = time.time()
    spec = SOURCES[source]
    if res not in spec["resolutions"]:
        raise ValueError(f"{spec['short']} is available at {', '.join(map(str, spec['resolutions']))} m")
    crs, transform, w, h, gu = _grid(geom, res)
    if w * h > 25_000_000:
        raise ValueError(f"The area is too large at {res} m ({w} × {h} pixels): choose a smaller area or a coarser resolution")
    dims = spec["dims"]
    out = np.full((dims, h, w), np.nan, dtype=np.float32)
    wgs = shape(geom).bounds
    parts = []
    if source == "aef":
        files = alphaearth.files(geom, year, Path(cache_dir))
        if not files:
            raise RuntimeError(f"AlphaEarth has no {year} data for this area")
        ovr = {10: None, 20: 0, 40: 1, 80: 2, 160: 3}[int(res)]
        for i, f in enumerate(files):
            progress.update(0.05 + 0.85 * i / len(files), f"AlphaEarth {year}: file {i + 1} of {len(files)} (64 layers in parallel)")
            r = alphaearth.read(f.path, transform_bounds("EPSG:4326", f.crs, *wgs, densify_pts=21), ovr)
            if r:
                parts.append((alphaearth.dequantize(r[0]), r[1], r[2]))
    else:
        tiles = [t for t in tessera.tiles(geom)]
        progress.update(0.03, f"TESSERA {year}: checking {len(tiles)} tile{'s' if len(tiles) > 1 else ''}")
        with ThreadPoolExecutor(16) as ex:
            ok = list(ex.map(lambda t: tessera.exists(t[0], t[1], year), tiles))
        tiles = [t for t, o in zip(tiles, ok) if o]
        if not tiles:
            raise RuntimeError(f"TESSERA has no {year} data for this area: try another year (2024 covers all land)")
        done = [0]

        def one(t):
            r = tessera.read(t[0], t[1], year, wgs)
            done[0] += 1
            progress.update(0.05 + 0.85 * done[0] / len(tiles), f"TESSERA {year}: tile {done[0]} of {len(tiles)}")
            return r
        with ThreadPoolExecutor(4) as ex:
            parts = [r for r in ex.map(one, tiles) if r]
    if not parts:
        raise RuntimeError("No embeddings were found inside the area")
    progress.update(0.92, "Putting the pieces together")
    for data, tf, src_crs in parts:   # each piece onto the output grid (nearest: keeps real vectors)
        tmp = np.full_like(out, np.nan)
        reproject(data, tmp, src_transform=tf, src_crs=src_crs, dst_transform=transform, dst_crs=crs, resampling=Resampling.nearest,
                  src_nodata=np.nan, dst_nodata=np.nan)
        fill = np.isnan(out[0]) & ~np.isnan(tmp[0])
        out[:, fill] = tmp[:, fill]
    outside = geometry_mask([mapping(gu)], out_shape=(h, w), transform=transform)
    out[:, outside] = np.nan
    valid = int((~np.isnan(out[0])).sum())
    if not valid:
        raise RuntimeError("No embeddings were found inside the area")
    names = [f"{spec['band_prefix']}{i:0{2 if dims < 100 else 3}d}" for i in range(dims)]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    prof = dict(driver="GTiff", width=w, height=h, count=dims, dtype="float32", crs=crs, transform=transform, nodata=np.nan,
                tiled=True, blockxsize=256, blockysize=256, compress="deflate", predictor=3, interleave="pixel",
                BIGTIFF="IF_SAFER")
    with rasterio.open(out_path, "w", **prof) as dst:
        dst.write(out)
        for i, n in enumerate(names, 1):
            dst.set_band_description(i, n)
        dst.update_tags(embedding=source, embedding_title=spec["title"], year=str(year), dimensions=str(dims), resolution_m=str(res),
                        licence=spec["licence"], attribution=spec["attribution"])
    meta = {"source": source, "title": spec["title"], "year": year, "res": res, "dims": dims, "width": w, "height": h, "crs": crs,
            "valid_pct": round(100 * valid / max(1, int((~outside).sum())), 1), "pieces": len(parts),
            "size_mb": round(Path(out_path).stat().st_size / 1e6, 1), "seconds": round(time.time() - t0, 1),
            "licence": spec["licence"], "attribution": spec["attribution"]}
    log.info("%s %d: %d × %d pixels at %g m, %d dimensions, %.1f %% of the area covered", spec["short"], year, w, h, res, dims, meta["valid_pct"])
    progress.update(1, "Done")
    return {**meta, "path": str(out_path)}

