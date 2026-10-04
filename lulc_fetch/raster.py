"""Reading remote rasters onto a Grid, and writing GeoTIFF / PNG outputs."""

from __future__ import annotations

import time
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning, RasterioIOError
from rasterio.vrt import WarpedVRT

from .aoi import Grid


def read_to_grid(href: str, grid: Grid, env: dict, *, indexes: int | list[int] = 1,
                 resampling: Resampling = Resampling.bilinear,
                 src_nodata: float | None = None, retries: int = 5) -> np.ndarray:
    """Warp a (remote) raster onto `grid`. Returns float32 with NaN where there is no data.

    Only the blocks overlapping the grid are fetched, so a small AOI out of a 110 km tile
    costs a few MB, not the whole file.
    """
    for attempt in range(retries):
        try:
            with rasterio.Env(**env), rasterio.open(href) as src:
                nodata = src_nodata if src_nodata is not None else src.nodata
                with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                               height=grid.height, resampling=resampling,
                               src_nodata=nodata, nodata=nodata) as vrt:
                    data = vrt.read(indexes, masked=True)
            return data.astype("float32").filled(np.nan)
        except RasterioIOError as e:   # a slow or busy server: wait longer each time (3, 6, 12, 24 s)
            if attempt == retries - 1:
                raise RasterioIOError(f"Couldn't read {str(href).split('?')[0].split('/')[-1]} from the imagery server after {retries} tries ({e})") from e
            time.sleep(3 * 2 ** attempt)


def write_geotiff(path: str | Path, data: np.ndarray, grid: Grid, *, descriptions=None,
                  nodata=np.nan, colormap: dict | None = None, tags: dict | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if data.ndim == 2:
        data = data[None]
    floating = np.issubdtype(data.dtype, np.floating)
    profile = {
        "driver": "GTiff", "width": grid.width, "height": grid.height, "count": data.shape[0],
        "dtype": data.dtype.name, "crs": grid.crs, "transform": grid.transform, "nodata": nodata,
        "compress": "deflate", "predictor": 3 if floating else 2, "tiled": True,
        "blockxsize": 256, "blockysize": 256, "BIGTIFF": "IF_SAFER",
    }
    if grid.width < 256 or grid.height < 256:
        profile.update(tiled=False)
        profile.pop("blockxsize")
        profile.pop("blockysize")
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
        for i, desc in enumerate(descriptions or [], start=1):
            dst.set_band_description(i, desc)
        if colormap:
            dst.write_colormap(1, colormap)
        if tags:
            dst.update_tags(**{k: str(v) for k, v in tags.items()})
    return path


def write_rgb_png(path: str | Path, rgb: np.ndarray, lo_pct: float = 2, hi_pct: float = 98) -> Path:
    """Percentile-stretched 8-bit quicklook. `rgb` is (3, H, W) float with NaN nodata."""
    path = Path(path)
    out = np.zeros(rgb.shape, dtype="uint8")
    valid = np.all(np.isfinite(rgb), axis=0)
    if valid.any():
        for i in range(3):
            band = rgb[i]
            lo, hi = np.percentile(band[valid], [lo_pct, hi_pct])
            scaled = np.clip((band - lo) / max(hi - lo, 1e-9) * 255, 0, 255)
            out[i] = np.nan_to_num(scaled, nan=0).astype("uint8")
        out[:, ~valid] = 0
    alpha = (valid * 255).astype("uint8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        _write_png(path, np.concatenate([out, alpha[None]]))
    return path


def _write_png(path: Path, rgba: np.ndarray):
    with rasterio.open(path, "w", driver="PNG", width=rgba.shape[2], height=rgba.shape[1],
                       count=4, dtype="uint8") as dst:
        dst.write(rgba)
