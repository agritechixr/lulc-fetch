"""Google AlphaEarth Foundations Satellite Embedding V1, read straight from the public bucket (or its mirror).

Cloud-Optimized GeoTIFFs of 8192 × 8192 pixels per UTM zone, 64 bands (A00–A63), 1024 × 1024 blocks per band, stored
bottom-up (positive pixel height), signed 8-bit: value v means sign(v)·(v/127.5)², −128 = no data. Overviews give 20–160 m.
The file index (aef_index.parquet, 70 MB) is downloaded once and kept slim.
"""

from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .. import progress
from .sources import AEF_BASES, GDAL_HTTP, session

log = logging.getLogger(__name__)


def index(cache_dir: Path) -> "object":
    """The AlphaEarth file index (year, path, bounds, CRS), downloaded once (70 MB) and kept slim (a few MB)."""
    import pandas as pd
    slim = cache_dir / "aef_index_slim.parquet"
    if slim.is_file() and time.time() - slim.stat().st_mtime < 90 * 86400:
        return pd.read_parquet(slim)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache_dir / "aef_index.parquet.part"
    last = None
    for base in AEF_BASES:
        try:
            log.info("Downloading the AlphaEarth file index (70 MB, once)")
            with session().get(f"{base}/aef_index.parquet", stream=True, timeout=60) as r:
                r.raise_for_status()
                total, done = int(r.headers.get("content-length") or 0), 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        progress.update(None, f"AlphaEarth index: {done / 1e6:.0f}{f' of {total / 1e6:.0f}' if total else ''} MB (once)")
            break
        except Exception as e:   # try the mirror
            last = e
    else:
        raise RuntimeError(f"Couldn't download the AlphaEarth index ({last}): check the internet connection")
    cols = ["path", "year", "crs", "wgs84_west", "wgs84_south", "wgs84_east", "wgs84_north"]
    df = pd.read_parquet(tmp, columns=cols)
    df["path"] = df["path"].str.replace("gs://alphaearth_foundations/satellite_embedding/v1/annual/", "", regex=False)
    df.to_parquet(slim, index=False)
    tmp.unlink(missing_ok=True)
    return df


def files(geom: dict, year: int, cache_dir: Path):
    from shapely.geometry import box, shape
    df = index(cache_dir)
    g = shape(geom)
    w, s, e, n = g.bounds
    c = df[(df.year == year) & (df.wgs84_west <= e) & (df.wgs84_east >= w) & (df.wgs84_south <= n) & (df.wgs84_north >= s)]
    return [r for r in c.itertuples() if box(r.wgs84_west, r.wgs84_south, r.wgs84_east, r.wgs84_north).intersects(g)]


def read(rel: str, bounds_in_file, ovr: int | None):
    """The window of one AlphaEarth file covering bounds (in the file's CRS), all 64 bands read in parallel.
    Returns (int8 array bands × rows × cols, its transform, crs) or None when the area misses the file."""
    import rasterio
    from rasterio.windows import Window

    def opener(base):
        kw = {"overview_level": ovr} if ovr is not None else {}
        return rasterio.open(f"/vsicurl/{base}/{rel}", **kw)

    base = AEF_BASES[0]
    with rasterio.Env(**GDAL_HTTP):
        try:
            src = opener(base)
        except Exception:
            base = AEF_BASES[1]
            src = opener(base)
        with src:
            inv = ~src.transform
            xs, ys = (bounds_in_file[0], bounds_in_file[2]), (bounds_in_file[1], bounds_in_file[3])
            cols, rows = zip(*[inv * (x, y) for x in xs for y in ys])
            c0, c1 = max(0, math.floor(min(cols))), min(src.width, math.ceil(max(cols)))
            r0, r1 = max(0, math.floor(min(rows))), min(src.height, math.ceil(max(rows)))
            if c1 <= c0 or r1 <= r0:
                return None
            win = Window(c0, r0, c1 - c0, r1 - r0)
            transform, crs, count = src.window_transform(win), src.crs, src.count

    def band(b):
        with rasterio.Env(**GDAL_HTTP), opener(base) as s:
            return s.read(b, window=win)

    with ThreadPoolExecutor(16) as ex:
        data = np.stack(list(ex.map(band, range(1, count + 1))))
    return data, transform, crs


def dequantize(q: np.ndarray) -> np.ndarray:
    v = q.astype(np.float32)
    out = np.sign(v) * (v / 127.5) ** 2
    out[:, (q == -128).all(axis=0)] = np.nan
    return out

