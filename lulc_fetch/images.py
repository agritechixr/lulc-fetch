"""Ordinary pictures (JPG, PNG, BMP, GIF, WebP) as 2D data.

* Georeferenced pictures (a world file such as .jgw / .pgw / .wld, optionally a .prj, or embedded
  georeferencing) are converted to a GeoTIFF so they behave like any other raster layer.
* Plain photos without georeferencing are kept as pictures: they open in the data viewer, and can be
  placed on the map by stretching them over the current map view (a quick manual georeference).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import ColorInterp
from rasterio.transform import from_bounds

PICTURE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
SIDECAR_EXTS = {".jgw", ".jpgw", ".jpegw", ".pgw", ".pngw", ".bpw", ".bmpw", ".gfw", ".gifw", ".wld", ".prj", ".xml", ".aux"}
MAX_PIXELS = 400_000_000


def _write_geotiff(data: np.ndarray, out: Path, crs, transform, colormap=None, nodata=None) -> Path:
    count = data.shape[0]
    profile = {"driver": "GTiff", "width": data.shape[2], "height": data.shape[1], "count": count, "dtype": data.dtype.name,
               "crs": crs, "transform": transform, "compress": "deflate", "BIGTIFF": "IF_SAFER"}
    if data.dtype == np.uint8 and count in (3, 4):
        profile["photometric"] = "RGB"
    if nodata is not None:
        profile["nodata"] = nodata
    if data.shape[1] >= 256 and data.shape[2] >= 256:
        profile.update(tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data)
        if count >= 3:
            dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue] + ([ColorInterp.alpha] if count == 4 else [])
            for i, n in enumerate(["Red", "Green", "Blue", "Alpha"][:count], start=1):
                dst.set_band_description(i, n)
        if colormap:
            dst.write_colormap(1, colormap)
    return out


def import_picture(path: Path) -> dict:
    """Convert a picture to a GeoTIFF if it is georeferenced, otherwise describe it as a plain picture."""
    path = Path(path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # NotGeoreferencedWarning is expected for photos
        with rasterio.open(path) as src:
            w, h, count = src.width, src.height, src.count
            if w * h > MAX_PIXELS:
                raise ValueError(f"The picture is too large ({w}×{h} px)")
            georef = not src.transform.is_identity or bool(src.gcps[0])
            crs, transform = src.crs, src.transform
            if src.gcps[0] and not crs:
                from rasterio.transform import from_gcps
                crs, transform = src.gcps[1], from_gcps(src.gcps[0])
            prj = next((p for p in path.parent.iterdir() if p.suffix.lower() == ".prj"), None)
            if georef and crs is None and prj is not None:
                crs = CRS.from_wkt(prj.read_text(errors="replace"))
            guessed = False
            if georef and crs is None:
                xs = [transform.c, transform.c + transform.a * w]
                ys = [transform.f, transform.f + transform.e * h]
                if all(-180 <= x <= 180 for x in xs) and all(-90 <= y <= 90 for y in ys):
                    crs, guessed = CRS.from_epsg(4326), True
            if not georef or crs is None:
                return {"kind": "picture", "path": str(path), "width": w, "height": h, "bands": count,
                        "reason": "no coordinate system (add a .prj file)" if georef else "not georeferenced"}
            data = src.read()
            cmap = src.colormap(1) if count == 1 and src.colorinterp[0] == ColorInterp.palette else None
    out = path.with_suffix(".tif")
    _write_geotiff(data, out, crs, transform, cmap)
    return {"kind": "raster", "path": str(out), "crs_guessed": guessed}


def georeference(path: Path, bounds: list[float], out: Path | None = None) -> Path:
    """Stretch a plain picture over [west, south, east, north] (WGS 84) and save it as a GeoTIFF."""
    w_, s, e, n = map(float, bounds)
    if not (-180 <= w_ < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("Invalid map extent")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with rasterio.open(path) as src:
            data = src.read()
            cmap = src.colormap(1) if src.count == 1 and src.colorinterp[0] == ColorInterp.palette else None
    out = Path(out or Path(path).with_name(Path(path).stem + "_placed.tif"))
    return _write_geotiff(data, out, CRS.from_epsg(4326), from_bounds(w_, s, e, n, data.shape[2], data.shape[1]), cmap)
