"""Non-Sentinel-2 layers from Planetary Computer: LULC reference maps, NAIP, Sentinel-1, etc."""

from __future__ import annotations

import logging

import numpy as np
from rasterio.enums import Resampling

from .aoi import Grid
from .raster import read_to_grid
from .sources import BASE_GDAL_ENV, PC_STAC

log = logging.getLogger(__name__)


def _rgb(hex_color: str) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255


LABEL_PRODUCTS = {
    "worldcover": {
        "title": "ESA WorldCover 10 m (2020, 2021)",
        "collection": "esa-worldcover", "asset": "map",
        "classes": {
            10: ("Tree cover", "#006400"), 20: ("Shrubland", "#ffbb22"), 30: ("Grassland", "#ffff4c"),
            40: ("Cropland", "#f096ff"), 50: ("Built-up", "#fa0000"), 60: ("Bare / sparse vegetation", "#b4b4b4"),
            70: ("Snow and ice", "#f0f0f0"), 80: ("Permanent water bodies", "#0064c8"),
            90: ("Herbaceous wetland", "#0096a0"), 95: ("Mangroves", "#00cf75"), 100: ("Moss and lichen", "#fae6a0"),
        },
    },
    "esri": {
        "title": "Esri / Impact Observatory annual LULC 10 m (2017 onwards)",
        "collection": "io-lulc-annual-v02", "asset": "data",
        "classes": {
            1: ("Water", "#1a5bab"), 2: ("Trees", "#358221"), 4: ("Flooded vegetation", "#87d19e"),
            5: ("Crops", "#ffdb5c"), 7: ("Built area", "#ed022a"), 8: ("Bare ground", "#ede9e4"),
            9: ("Snow / ice", "#f2faff"), 10: ("Clouds", "#c8c8c8"), 11: ("Rangeland", "#c6ad8d"),
        },
    },
}


def pc_search(collection: str, bbox, datetime: str | None = None, limit: int = 100, query=None):
    import planetary_computer
    from pystac_client import Client

    client = Client.open(PC_STAC, modifier=planetary_computer.sign_inplace)
    kwargs = {"collections": [collection], "bbox": list(bbox), "max_items": limit}
    if datetime:
        kwargs["datetime"] = datetime
    if query:
        kwargs["query"] = query
    return list(client.search(**kwargs).items())


def mosaic_items(items, asset: str, grid: Grid, resampling: Resampling,
                 nodata: float | None = None, indexes=None) -> np.ndarray:
    """First-valid mosaic of `asset` across items (pass items newest-first to prefer recent data)."""
    out = None
    for item in items:
        if asset not in item.assets:
            continue
        href = item.assets[asset].href
        if indexes is None:
            import rasterio
            with rasterio.Env(**BASE_GDAL_ENV), rasterio.open(href) as src:
                indexes = list(range(1, src.count + 1))
        data = read_to_grid(href, grid, BASE_GDAL_ENV, indexes=indexes,
                            resampling=resampling, src_nodata=nodata)
        if out is None:
            out = data
        else:
            fill = np.isnan(out) & ~np.isnan(data)
            out[fill] = data[fill]
        if not np.isnan(out).any():
            break
    if out is None:
        raise RuntimeError(f"No items with asset {asset!r} cover the AOI")
    return out


def fetch_labels(product: str, year: int, grid: Grid):
    """Reference LULC map resampled (nearest) onto the grid. Returns (uint8 array, product spec)."""
    spec = LABEL_PRODUCTS[product]
    items = pc_search(spec["collection"], grid.bbox_lonlat(), f"{year}-01-01/{year}-12-31")
    if not items:
        raise RuntimeError(f"No {spec['collection']} data for {year} over this AOI. "
                           f"WorldCover has 2020/2021; Esri annual LULC starts in 2017.")
    log.info("Using %d %s item(s): %s", len(items), spec["collection"], ", ".join(i.id for i in items))
    data = mosaic_items(items, spec["asset"], grid, Resampling.nearest, nodata=0, indexes=1)
    return np.nan_to_num(data, nan=0).astype("uint8"), spec


def fetch_collection(collection: str, assets: list[str], grid: Grid, datetime: str | None,
                     resampling: Resampling = Resampling.bilinear, limit: int = 100):
    """Most-recent-first mosaic of the given assets from any Planetary Computer collection."""
    items = pc_search(collection, grid.bbox_lonlat(), datetime, limit=limit)
    if not items:
        raise RuntimeError(f"No {collection} items found for this AOI / date range")
    items.sort(key=lambda i: i.datetime or i.properties.get("start_datetime", ""), reverse=True)
    log.info("Found %d %s item(s); newest %s", len(items), collection, items[0].id)
    layers, names = [], []
    for asset in assets:
        data = mosaic_items(items, asset, grid, resampling)
        layers.append(data)
        names += [asset] if data.shape[0] == 1 else [f"{asset}_{i}" for i in range(1, data.shape[0] + 1)]
    return np.concatenate(layers), names, items
