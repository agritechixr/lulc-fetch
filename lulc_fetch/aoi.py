"""Area-of-interest parsing and the output pixel grid every layer is aligned to."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.transform import array_bounds, from_origin
from rasterio.warp import transform_bounds, transform_geom


@dataclass
class AOI:
    bbox: tuple[float, float, float, float]  # lon/lat: minx, miny, maxx, maxy
    geometries: list[dict] | None = None  # GeoJSON geometries in EPSG:4326, used for masking


@dataclass
class Grid:
    crs: CRS
    transform: Affine
    width: int
    height: int

    @property
    def res(self) -> float:
        return self.transform.a

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def bounds(self):
        return array_bounds(self.height, self.width, self.transform)

    def bbox_lonlat(self) -> tuple[float, float, float, float]:
        return transform_bounds(self.crs, "EPSG:4326", *self.bounds, densify_pts=21)


def _parse_floats(text: str, n: int, name: str) -> list[float]:
    parts = [float(p) for p in text.replace(" ", "").split(",") if p]
    if len(parts) != n:
        raise ValueError(f"--{name} expects {n} comma-separated numbers, got {text!r}")
    return parts


def _coords(geom: dict):
    if geom["type"] == "GeometryCollection":
        for g in geom["geometries"]:
            yield from _coords(g)
        return
    stack = [geom["coordinates"]]
    while stack:
        c = stack.pop()
        if isinstance(c[0], (int, float)):
            yield c[0], c[1]
        else:
            stack.extend(c)


def _load_geojson(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    if data.get("type") == "FeatureCollection":
        return [feat["geometry"] for feat in data["features"] if feat.get("geometry")]
    if data.get("type") == "Feature":
        return [data["geometry"]]
    return [data]


def aoi_from_args(bbox: str | None = None, geojson: str | None = None,
                  point: str | None = None, buffer_km: float = 5.0) -> AOI:
    if bbox:
        minx, miny, maxx, maxy = _parse_floats(bbox, 4, "bbox")
        if minx >= maxx or miny >= maxy:
            raise ValueError("--bbox must be minlon,minlat,maxlon,maxlat")
        return AOI((minx, miny, maxx, maxy))
    if geojson:
        geoms = _load_geojson(geojson)
        xs, ys = zip(*(xy for g in geoms for xy in _coords(g)))
        return AOI((min(xs), min(ys), max(xs), max(ys)), geoms)
    if point:
        lon, lat = _parse_floats(point, 2, "point")
        dlat = buffer_km / 111.32
        dlon = buffer_km / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
        return AOI((lon - dlon, lat - dlat, lon + dlon, lat + dlat))
    raise ValueError("Provide an AOI with --bbox, --geojson, --point or --match")


def utm_crs(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def make_grid(aoi: AOI, res: float, crs: str | None = None) -> Grid:
    minx, miny, maxx, maxy = aoi.bbox
    dst_crs = CRS.from_user_input(crs) if crs else utm_crs((minx + maxx) / 2, (miny + maxy) / 2)
    l, b, r, t = transform_bounds("EPSG:4326", dst_crs, *aoi.bbox, densify_pts=21)
    # Snap to the resolution so grids built from the same AOI line up exactly.
    l, b = math.floor(l / res) * res, math.floor(b / res) * res
    r, t = math.ceil(r / res) * res, math.ceil(t / res) * res
    return Grid(dst_crs, from_origin(l, t, res, res), int(round((r - l) / res)), int(round((t - b) / res)))


def grid_from_raster(path: str) -> Grid:
    with rasterio.open(path) as src:
        return Grid(src.crs, src.transform, src.width, src.height)


def aoi_mask(aoi: AOI | None, grid: Grid) -> np.ndarray | None:
    """Boolean array, True inside the AOI polygon(s). None when the AOI is a plain box."""
    if aoi is None or not aoi.geometries:
        return None
    geoms = [transform_geom("EPSG:4326", grid.crs, g) for g in aoi.geometries]
    return geometry_mask(geoms, out_shape=grid.shape, transform=grid.transform, invert=True)
