"""TESSERA embeddings (University of Cambridge), read straight from Source Cooperative.

Tiles of 0.1° × 0.1° named after their centre (grid_77.55_12.95), each an int8 .npy (rows × cols × 128) with a float32
*_scales.npy (rows × cols): value = int8 × scale. The tile's position comes from its landmask GeoTIFF. Only the rows that
cross the area are downloaded (HTTP range requests).
"""

from __future__ import annotations

import io
import math

import numpy as np

from .sources import GDAL_HTTP, TESSERA_BASE, TESSERA_VERSION, get_range, session


def tiles(geom: dict) -> list[tuple[float, float]]:
    """Centres of the 0.1° tiles an area touches."""
    from shapely.geometry import box, shape
    g = shape(geom)
    w, s, e, n = g.bounds
    out = []
    for i in range(math.floor(w * 10), math.ceil(e * 10)):
        for j in range(math.floor(s * 10), math.ceil(n * 10)):
            lon, lat = round(i / 10 + 0.05, 2), round(j / 10 + 0.05, 2)
            if box(lon - 0.05, lat - 0.05, lon + 0.05, lat + 0.05).intersects(g):
                out.append((lon, lat))
    return out


def name(lon, lat) -> str:
    return f"grid_{lon:.2f}_{lat:.2f}"


def url(lon, lat, year, scales=False) -> str:
    n = name(lon, lat)
    return f"{TESSERA_BASE}/npy/{TESSERA_VERSION}/{year}/{n}/{n}{'_scales' if scales else ''}.npy"


def exists(lon, lat, year) -> bool:
    try:
        return session().head(url(lon, lat, year), timeout=20).status_code == 200
    except Exception:
        return False


def _npy_header(url: str):
    b = get_range(url, 0, 4095, timeout=30)
    f = io.BytesIO(b)
    v = np.lib.format.read_magic(f)
    shape, fortran, dtype = (np.lib.format.read_array_header_1_0 if v == (1, 0) else np.lib.format.read_array_header_2_0)(f)
    if fortran:
        raise RuntimeError("Unexpected TESSERA file layout")
    return shape, dtype, f.tell()


def _npy_rows(url: str, r0: int, r1: int):
    shape, dtype, off = _npy_header(url)
    row = int(np.prod(shape[1:])) * dtype.itemsize
    b = get_range(url, off + r0 * row, off + r1 * row - 1)   # retried in pieces if the connection drops
    return np.frombuffer(b, dtype=dtype).reshape((r1 - r0,) + tuple(shape[1:]))


def read(lon, lat, year, bounds_wgs84):
    """The part of one TESSERA tile inside bounds (lon/lat): (float32 bands × rows × cols, transform, crs) or None."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window
    with rasterio.Env(**GDAL_HTTP), rasterio.open(f"/vsicurl/{TESSERA_BASE}/landmasks/{TESSERA_VERSION}/{name(lon, lat)}.tiff") as lm:
        tf, crs, W, H = lm.transform, lm.crs, lm.width, lm.height
    b = transform_bounds("EPSG:4326", crs, *bounds_wgs84, densify_pts=21)
    inv = ~tf
    cols, rows = zip(*[inv * (x, y) for x in (b[0], b[2]) for y in (b[1], b[3])])
    c0, c1 = max(0, math.floor(min(cols))), min(W, math.ceil(max(cols)))
    r0, r1 = max(0, math.floor(min(rows))), min(H, math.ceil(max(rows)))
    if c1 <= c0 or r1 <= r0:
        return None
    q = _npy_rows(url(lon, lat, year), r0, r1)[:, c0:c1]
    sc = _npy_rows(url(lon, lat, year, scales=True), r0, r1)[:, c0:c1]
    emb = q.astype(np.float32) * (sc[..., None] if sc.ndim == 2 else sc)
    emb[~np.isfinite(sc) if sc.ndim == 2 else ~np.isfinite(sc).all(-1)] = np.nan
    return np.moveaxis(emb, -1, 0), rasterio.windows.transform(Window(c0, r0, c1 - c0, r1 - r0), tf), crs

