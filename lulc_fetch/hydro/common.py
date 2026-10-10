"""What the hydrology tools share: a DEM's grid (size, pixel sizes in metres, cell areas), writing rasters and GeoJSON,
lon / lat ↔ row / column, polygons of labelled areas, and the D8 surface every tool starts from."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform

from .. import hydrology as H
from ..raster_ops import _pixel_metres, _profile


@dataclass
class Grid:
    profile: dict
    transform: object
    crs: object
    h: int
    w: int
    dx: np.ndarray          # pixel width in metres, per row (h, 1): geographic rasters narrow towards the poles
    dy: float
    valid: np.ndarray       # (h, w) bool

    @property
    def cell_m2(self) -> np.ndarray:
        return np.broadcast_to(self.dx * self.dy, (self.h, self.w))

    @property
    def cell_m(self) -> float:
        return float(np.sqrt(np.mean(self.dx) * self.dy))


def open_dem(path, band: int = 1) -> tuple[Grid, np.ndarray]:
    """A DEM's grid and heights (float64, NaN where there is none)."""
    src, z = H.read_dem(path, band)
    with src:
        dx, dy = _pixel_metres(src, src.height)
        dx = np.asarray(dx, dtype="float64").reshape(-1, 1) if np.ndim(dx) else np.full((src.height, 1), float(dx))
        g = Grid(_profile(src), src.transform, src.crs, src.height, src.width, dx, float(dy), np.isfinite(z))
    if g.valid.sum() < 9:
        raise ValueError("The DEM has (almost) no heights")
    return g, z


def read_like(g: Grid, path, band: int = 1, resampling: str = "bilinear") -> np.ndarray:
    """Another raster on the DEM's grid (reprojected / resampled when needed)."""
    from rasterio.warp import Resampling, reproject
    with rasterio.open(path) as s:
        out = np.full((g.h, g.w), np.nan, "float64")
        src = s.read(band, masked=True).astype("float64").filled(np.nan)
        reproject(src, out, src_transform=s.transform, src_crs=s.crs, dst_transform=g.transform, dst_crs=g.crs,
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=getattr(Resampling, resampling))
    return out


def write(g: Grid, path: Path, a: np.ndarray, desc: str | list[str], *, dtype="float32", nodata=-9999.0, cmap=None, tags=None) -> str:
    """One raster (2-D) or several bands (3-D) on the DEM's grid; NaN and cells outside the DEM become nodata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    a = a.reshape(-1, g.h, g.w) if a.ndim == 3 or a.size != g.h * g.w else a.reshape(1, g.h, g.w)
    prof = {**g.profile, "dtype": dtype, "nodata": nodata, "count": a.shape[0]}
    with rasterio.open(path, "w", **prof) as d:
        for i, band in enumerate(a, 1):
            if dtype.startswith("float"):
                band = np.where(np.isfinite(band) & g.valid, band, nodata)
            else:
                band = np.where(g.valid, band, nodata)
            d.write(band.astype(dtype), i)
            d.set_band_description(i, desc[i - 1] if isinstance(desc, list) else desc)
        if cmap:
            d.write_colormap(1, cmap)
        if tags:
            d.update_tags(**{k: (json.dumps(v) if not isinstance(v, str) else v) for k, v in tags.items()})
    return str(path)


def write_fc(fc: dict, path: Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": fc["features"]}), encoding="utf-8")
    return str(path)


def to_lonlat(g: Grid, rows, cols) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = rasterio.transform.xy(g.transform, np.atleast_1d(rows), np.atleast_1d(cols))
    xs, ys = np.atleast_1d(xs).astype(float), np.atleast_1d(ys).astype(float)
    if g.crs and g.crs.to_epsg() != 4326:
        xs, ys = warp_transform(g.crs, "EPSG:4326", xs.tolist(), ys.tolist())
    return np.asarray(xs), np.asarray(ys)


def from_lonlat(g: Grid, points: list[list[float]]) -> tuple[np.ndarray, np.ndarray]:
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    xs, ys = (lons, lats) if not g.crs or g.crs.to_epsg() == 4326 else warp_transform("EPSG:4326", g.crs, lons, lats)
    r, c = rasterio.transform.rowcol(g.transform, xs, ys)
    return np.atleast_1d(r), np.atleast_1d(c)


def line(g: Grid, flat_idx) -> list[list[float]]:
    r, c = np.divmod(np.asarray(flat_idx), g.w)
    xs, ys = to_lonlat(g, r, c)
    return [[round(float(x), 7), round(float(y), 7)] for x, y in zip(xs, ys)]


def polygons(g: Grid, lab2d: np.ndarray, info: dict[int, dict], key: str = "id") -> dict:
    """Polygons of each label > 0 (EPSG:4326) with its info."""
    from rasterio import features
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as stransform
    from shapely.ops import unary_union
    parts: dict[int, list] = {}
    for geom, v in features.shapes(lab2d.astype("int32"), mask=lab2d > 0, transform=g.transform):
        parts.setdefault(int(v), []).append(shape(geom))

    def to_ll(x, y, z=None):
        xs, ys = warp_transform(g.crs, "EPSG:4326", list(np.atleast_1d(x)), list(np.atleast_1d(y)))
        return np.asarray(xs), np.asarray(ys)
    feats = []
    for k in sorted(parts):
        geom = unary_union(parts[k])
        if g.crs and g.crs.to_epsg() != 4326:
            geom = stransform(to_ll, geom)
        feats.append({"type": "Feature", "properties": {key: k, **info.get(k, {})}, "geometry": mapping(geom)})
    return {"type": "FeatureCollection", "features": feats}


def step_m(g: Grid, down: np.ndarray) -> np.ndarray:
    """The length (m) of each cell's D8 step to the cell it drains to (half a cell where water leaves the DEM)."""
    i = np.arange(down.size)
    r, c = np.divmod(i, g.w)
    dr = np.where(down >= 0, down // g.w - r, 0)
    dc = np.where(down >= 0, down % g.w - c, 0)
    dx = g.dx.ravel()[r]
    s = np.hypot(dr * g.dy, dc * dx)
    return np.where(down >= 0, s, 0.5 * np.sqrt(dx * g.dy))


class Surface:
    """The D8 view of a DEM every tool needs: filled heights, the ε-raised surface (flats drain), D8 codes, the cell each
    cell drains to, the flow order (levels), contributing area (km²) and step lengths."""

    def __init__(self, g: Grid, z: np.ndarray, *, filled: bool = False):
        self.g, self.z = g, z
        if filled:   # already conditioned: only raise flats by ε so that they drain
            self.filled, self.eps = z, H.fill_sinks(z)[1]
        else:
            self.filled, self.eps = H.fill_sinks(z)
        self.code, self.down = H.flow_direction(self.eps, g.dx, g.dy)
        self.levels = H.flow_levels(self.down, g.valid.ravel())
        self.area_km2 = H.accumulate(self.down, self.levels, np.where(g.valid, g.cell_m2, 0).ravel() / 1e6)
        self.step = step_m(g, self.down)

    def streams(self, km2: float) -> np.ndarray:
        s = (self.area_km2 >= km2) & self.g.valid.ravel()
        if not s.any():
            raise ValueError(f"No cell drains {km2:g} km² → lower the stream threshold (the largest is {self.area_km2.max():.3g} km²)")
        return s

    def snap(self, points: list[list[float]], snap_m: float) -> list[int]:
        """Each point moved to the cell with the largest contributing area within snap_m."""
        g = self.g
        rows, cols = from_lonlat(g, points)
        r_s = max(0, int(round(snap_m / g.cell_m)))
        acc = self.area_km2.reshape(g.h, g.w)
        out = []
        for k, (r, c) in enumerate(zip(rows, cols), 1):
            if not (0 <= r < g.h and 0 <= c < g.w) or not g.valid[r, c]:
                raise ValueError(f"Point {k} is outside the DEM (or on a cell without a height)")
            r0, r1, c0, c1 = max(0, r - r_s), min(g.h, r + r_s + 1), max(0, c - r_s), min(g.w, c + r_s + 1)
            win = np.where(g.valid[r0:r1, c0:c1], acc[r0:r1, c0:c1], -1)
            rr, cc = np.unravel_index(int(np.argmax(win)), win.shape)
            out.append(int((r0 + rr) * g.w + c0 + cc))
        return out
