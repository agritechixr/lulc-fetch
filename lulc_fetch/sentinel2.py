"""Sentinel-2 L2A search, single-date scenes, cloud masking and median composites."""

from __future__ import annotations

import logging
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import numpy as np
from pystac import Item
from rasterio.enums import Resampling

from .aoi import Grid
from .raster import read_to_grid
from .sources import Source

log = logging.getLogger(__name__)

# Scene Classification Layer classes treated as unusable:
# 0 no data, 1 saturated/defective, 3 cloud shadow, 8/9 cloud medium/high prob, 10 thin cirrus.
BAD_SCL = (0, 1, 3, 8, 9, 10)
MAX_WORKERS = 16


@dataclass
class Scene:
    """One acquisition date over the AOI; may span several MGRS tiles that get mosaicked."""
    date: date
    items: list[Item]

    @property
    def cloud(self) -> float:
        values = [i.properties.get("eo:cloud_cover") for i in self.items]
        values = [v for v in values if v is not None]
        return float(np.mean(values)) if values else float("nan")

    @property
    def tiles(self) -> list[str]:
        return sorted({_tile(i) for i in self.items})

    @property
    def ids(self) -> list[str]:
        return [i.id for i in self.items]


def _tile(item: Item) -> str:
    p = item.properties
    if "s2:mgrs_tile" in p:
        return p["s2:mgrs_tile"]
    if "grid:code" in p:
        return p["grid:code"].replace("MGRS-", "")
    return item.id.split("_")[1] if "_" in item.id else item.id


def search(source: Source, bbox, start: str, end: str, max_cloud: float | None = None,
           limit: int = 500, intersects: dict | None = None) -> list[Item]:
    """Items over `bbox`, or over the `intersects` GeoJSON geometry when given."""
    client = source.client()
    where = {"intersects": intersects} if intersects else {"bbox": list(bbox)}
    results = client.search(collections=[source.collection], datetime=f"{start}/{end}",
                            max_items=limit, **where, **source.search_kwargs(max_cloud))
    items = list(results.items())
    if max_cloud is not None:  # not every catalog honours the server-side filter
        items = [i for i in items if i.properties.get("eo:cloud_cover", 0) < max_cloud]
    return items


def group_scenes(items: list[Item]) -> list[Scene]:
    """Group items by acquisition date (and drop duplicate reprocessings of the same tile)."""
    by_date: dict[date, dict[str, Item]] = defaultdict(dict)
    for item in items:
        tiles = by_date[item.datetime.date()]
        key = _tile(item)
        # Keep the most recently processed copy when a catalog lists a tile twice.
        if key not in tiles or item.id > tiles[key].id:
            tiles[key] = item
    scenes = [Scene(d, list(t.values())) for d, t in by_date.items()]
    return sorted(scenes, key=lambda s: (np.nan_to_num(s.cloud, nan=101), s.date))


def read_band(source: Source, scene: Scene, band: str, grid: Grid, env: dict) -> np.ndarray:
    """Mosaic one band from every tile in the scene onto the grid.

    Spectral bands come back as surface reflectance (0-1); SCL as raw class codes.
    """
    out = np.full(grid.shape, np.nan, dtype="float32")
    is_scl = band == "SCL"
    for item in scene.items:
        try:
            href = source.href(item, band)
        except KeyError:
            continue
        data = read_to_grid(href, grid, env, src_nodata=0,
                            resampling=Resampling.nearest if is_scl else Resampling.bilinear)
        if not is_scl:
            data = (data + source.boa_offset(item)) / 10000.0
        fill = np.isnan(out) & ~np.isnan(data)
        out[fill] = data[fill]
        if not np.isnan(out).any():
            break
    return out


def clear_mask(source: Source, scene: Scene, grid: Grid, env: dict) -> np.ndarray:
    """True where the pixel has data and is not cloud / shadow / cirrus according to SCL."""
    scl = read_band(source, scene, "SCL", grid, env)
    return ~np.isnan(scl) & ~np.isin(np.nan_to_num(scl, nan=0), BAD_SCL)


def pick_best_scene(source: Source, scenes: list[Scene], grid: Grid, region: np.ndarray | None,
                    candidates: int = 5) -> tuple[Scene, float]:
    """Of the `candidates` least-cloudy scenes, return the one with most clear pixels in the AOI.

    Scene-level cloud cover describes the whole 110 km tile, so it often misjudges a small AOI;
    checking the SCL layer inside the AOI is cheap and much more reliable.
    """
    env = source.gdal_env()
    pool = scenes[:candidates]
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        masks = list(ex.map(lambda s: clear_mask(source, s, grid, env), pool))
    region = region if region is not None else np.ones(grid.shape, bool)
    fractions = [float(m[region].mean()) for m in masks]
    for s, f in zip(pool, fractions):
        log.info("  %s  tiles=%s  scene cloud=%5.1f%%  clear in AOI=%5.1f%%",
                 s.date, ",".join(s.tiles), s.cloud, 100 * f)
    best = int(np.argmax(fractions))
    return pool[best], fractions[best]


def scene_stack(source: Source, scene: Scene, grid: Grid, bands: list[str],
                mask_clouds: bool = True) -> dict[str, np.ndarray]:
    env = source.gdal_env()
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        arrays = dict(zip(bands, ex.map(lambda b: read_band(source, scene, b, grid, env), bands)))
        if mask_clouds:
            clear = clear_mask(source, scene, grid, env)
            for a in arrays.values():
                a[~clear] = np.nan
    return arrays


def composite(source: Source, scenes: list[Scene], grid: Grid, bands: list[str],
              stat: str = "median", mem_budget: float = 1.5e9) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Cloud-masked per-pixel median (or mean) over all scenes.

    Returns the band arrays and a per-pixel count of clear observations. Bands are read in
    groups sized so the scene x band cube stays under `mem_budget` bytes, with every
    (scene, band) read in a group running concurrently.
    """
    env = source.gdal_env()
    reducer = {"median": np.nanmedian, "mean": np.nanmean}[stat]
    cube_bytes = len(scenes) * grid.width * grid.height * 4
    group = int(max(1, min(len(bands), mem_budget // max(cube_bytes, 1))))
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        log.info("Reading cloud masks for %d scenes...", len(scenes))
        masks = np.stack(list(ex.map(lambda s: clear_mask(source, s, grid, env), scenes)))
        count = masks.sum(axis=0).astype("uint16")
        out = {}
        for i in range(0, len(bands), group):
            batch = bands[i:i + group]
            log.info("Compositing %s...", ", ".join(batch))
            futures = {b: [ex.submit(read_band, source, s, b, grid, env) for s in scenes] for b in batch}
            for band, futs in futures.items():
                cube = np.stack([f.result() for f in futs])
                cube[~masks] = np.nan
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels
                    out[band] = reducer(cube, axis=0).astype("float32")
                del cube
    return out, count
