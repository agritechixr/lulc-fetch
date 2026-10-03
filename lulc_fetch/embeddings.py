"""Open satellite embeddings: find which years exist for an area, download them as a GeoTIFF, and explore them.

An embedding gives every 10 m pixel a vector that sums up a whole year of satellite observations; similar places get
similar vectors, so a few labelled points are enough to classify, cluster or search them. Two open, global, per-pixel
datasets can be read directly over HTTPS, without an account:

- **Google AlphaEarth Foundations** Satellite Embedding V1 (Google and Google DeepMind, CC-BY-4.0): 64 dimensions, unit
  vectors, 2017–2025. Cloud-Optimized GeoTIFFs of 8192 × 8192 pixels per UTM zone, stored bottom-up (positive pixel height),
  signed 8-bit; value v means sign(v)·(v/127.5)², −128 = no data. Index: aef_index.parquet (one row per file).
- **TESSERA** (University of Cambridge, CC0): 128 dimensions, 2017–2025 (not every year everywhere). Tiles of 0.1° × 0.1°
  named after their centre (grid_77.55_12.95), each an int8 .npy (rows × cols × 128) with a float32 *_scales.npy
  (rows × cols); value = int8 × scale. The position comes from the tile's landmask GeoTIFF. Only the rows needed are
  downloaded (HTTP range requests).

The result is a GeoTIFF (float32, one band per dimension: A00–A63 or E000–E127) in the UTM zone of the area, ready for
Classical ML for raster, clustering, PCA, or similarity search.
"""

from __future__ import annotations

import io
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import progress

log = logging.getLogger(__name__)

AEF_BASES = ["https://storage.googleapis.com/alphaearth_foundations/satellite_embedding/v1/annual",
             "https://data.source.coop/tge-labs/aef/v1/annual"]          # Google (free since July 2026) · Source Cooperative mirror
TESSERA_BASE = "https://data.source.coop/tessera/tessera"
TESSERA_VERSION = "v1"
YEARS = list(range(2017, 2026))

SOURCES = {
    "aef": {
        "title": "Google AlphaEarth Foundations", "short": "AlphaEarth", "dims": 64, "res": 10, "years": [2017, 2025],
        "by": "Google and Google DeepMind", "licence": "CC-BY-4.0",
        "attribution": "The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind.",
        "coverage": "Global land and coastal waters, every year 2017–2025",
        "about": "Learned from Sentinel-1 and -2, Landsat, elevation, climate and more; one 64-dimensional unit vector per 10 m "
                 "pixel per year. Compare pixels with cosine similarity (dot product).",
        "url": "https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL",
        "resolutions": [10, 20, 40, 80, 160], "band_prefix": "A",
    },
    "tessera": {
        "title": "TESSERA", "short": "TESSERA", "dims": 128, "res": 10, "years": [2017, 2025],
        "by": "University of Cambridge", "licence": "CC0",
        "attribution": "TESSERA embeddings, University of Cambridge (Feng et al., 2025, arXiv:2506.20380).",
        "coverage": "Global land for 2024; other years 2017–2025 in many regions",
        "about": "Learned from a full year of Sentinel-1 and Sentinel-2 time series; one 128-dimensional vector per 10 m pixel "
                 "per year, strong for crops and seasonal land cover.",
        "url": "https://github.com/ucam-eo/geotessera", "resolutions": [10], "band_prefix": "E",
    },
}
OTHER = [   # open embeddings this tool can't download as per-pixel maps (shown for reference)
    {"title": "Major TOM embeddings (ESA Φ-lab)", "what": "One vector per Sentinel-1/-2 image patch (SigLIP, DINOv2, SSL4EO), global, as GeoParquet",
     "url": "https://huggingface.co/Major-TOM"},
    {"title": "Clay foundation model embeddings", "what": "768-D vectors per image chip for some regions (e.g. NAIP over the USA)",
     "url": "https://huggingface.co/made-with-clay"},
]

_HTTP = None


def _session():
    global _HTTP
    if _HTTP is None:
        import requests
        from requests.adapters import HTTPAdapter
        _HTTP = requests.Session()
        _HTTP.mount("https://", HTTPAdapter(pool_connections=32, pool_maxsize=64))
        _HTTP.headers["User-Agent"] = "LULC-Fetch"
    return _HTTP


# ------------------------------------------------------------------ area → output grid

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


# ------------------------------------------------------------------ AlphaEarth

def _aef_index(cache_dir: Path) -> "object":
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
            with _session().get(f"{base}/aef_index.parquet", stream=True, timeout=60) as r:
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


def _aef_files(geom: dict, year: int, cache_dir: Path):
    from shapely.geometry import box, shape
    df = _aef_index(cache_dir)
    g = shape(geom)
    w, s, e, n = g.bounds
    c = df[(df.year == year) & (df.wgs84_west <= e) & (df.wgs84_east >= w) & (df.wgs84_south <= n) & (df.wgs84_north >= s)]
    return [r for r in c.itertuples() if box(r.wgs84_west, r.wgs84_south, r.wgs84_east, r.wgs84_north).intersects(g)]


GDAL_HTTP = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tiff,.tif", GDAL_HTTP_MULTIRANGE="YES",
                 GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", GDAL_HTTP_VERSION="2", GDAL_HTTP_MULTIPLEX="YES", GDAL_HTTP_MAX_RETRY="4",
                 GDAL_HTTP_RETRY_DELAY="2", VSI_CACHE="TRUE")


def _aef_read(rel: str, bounds_in_file, ovr: int | None):
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


def _dequantize_aef(q: np.ndarray) -> np.ndarray:
    v = q.astype(np.float32)
    out = np.sign(v) * (v / 127.5) ** 2
    out[:, (q == -128).all(axis=0)] = np.nan
    return out


# ------------------------------------------------------------------ TESSERA

def _tess_tiles(geom: dict) -> list[tuple[float, float]]:
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


def _tess_name(lon, lat) -> str:
    return f"grid_{lon:.2f}_{lat:.2f}"


def _tess_url(lon, lat, year, scales=False) -> str:
    n = _tess_name(lon, lat)
    return f"{TESSERA_BASE}/npy/{TESSERA_VERSION}/{year}/{n}/{n}{'_scales' if scales else ''}.npy"


def _tess_exists(lon, lat, year) -> bool:
    try:
        return _session().head(_tess_url(lon, lat, year), timeout=20).status_code == 200
    except Exception:
        return False


def _npy_header(url: str):
    b = _session().get(url, headers={"Range": "bytes=0-4095"}, timeout=30).content
    f = io.BytesIO(b)
    v = np.lib.format.read_magic(f)
    shape, fortran, dtype = (np.lib.format.read_array_header_1_0 if v == (1, 0) else np.lib.format.read_array_header_2_0)(f)
    if fortran:
        raise RuntimeError("Unexpected TESSERA file layout")
    return shape, dtype, f.tell()


def _npy_rows(url: str, r0: int, r1: int):
    shape, dtype, off = _npy_header(url)
    row = int(np.prod(shape[1:])) * dtype.itemsize
    r = _session().get(url, headers={"Range": f"bytes={off + r0 * row}-{off + r1 * row - 1}"}, timeout=180)
    r.raise_for_status()
    return np.frombuffer(r.content, dtype=dtype).reshape((r1 - r0,) + tuple(shape[1:]))


def _tess_read(lon, lat, year, bounds_wgs84):
    """The part of one TESSERA tile inside bounds (lon/lat): (float32 bands × rows × cols, transform, crs) or None."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window
    with rasterio.Env(**GDAL_HTTP), rasterio.open(f"/vsicurl/{TESSERA_BASE}/landmasks/{TESSERA_VERSION}/{_tess_name(lon, lat)}.tiff") as lm:
        tf, crs, W, H = lm.transform, lm.crs, lm.width, lm.height
    b = transform_bounds("EPSG:4326", crs, *bounds_wgs84, densify_pts=21)
    inv = ~tf
    cols, rows = zip(*[inv * (x, y) for x in (b[0], b[2]) for y in (b[1], b[3])])
    c0, c1 = max(0, math.floor(min(cols))), min(W, math.ceil(max(cols)))
    r0, r1 = max(0, math.floor(min(rows))), min(H, math.ceil(max(rows)))
    if c1 <= c0 or r1 <= r0:
        return None
    q = _npy_rows(_tess_url(lon, lat, year), r0, r1)[:, c0:c1]
    sc = _npy_rows(_tess_url(lon, lat, year, scales=True), r0, r1)[:, c0:c1]
    emb = q.astype(np.float32) * (sc[..., None] if sc.ndim == 2 else sc)
    emb[~np.isfinite(sc) if sc.ndim == 2 else ~np.isfinite(sc).all(-1)] = np.nan
    return np.moveaxis(emb, -1, 0), rasterio.windows.transform(Window(c0, r0, c1 - c0, r1 - r0), tf), crs


# ------------------------------------------------------------------ availability

def available(geom: dict, cache_dir: str | Path, sources=("aef", "tessera")) -> dict:
    """Per source and year: how many files / tiles cover the area, and how many exist."""
    out = {}
    if "aef" in sources:
        progress.update(0.05, "AlphaEarth: looking up the index")
        df = _aef_index(Path(cache_dir))
        from shapely.geometry import box, shape
        g = shape(geom)
        w, s, e, n = g.bounds
        c = df[(df.wgs84_west <= e) & (df.wgs84_east >= w) & (df.wgs84_south <= n) & (df.wgs84_north >= s)]
        c = c[[box(r.wgs84_west, r.wgs84_south, r.wgs84_east, r.wgs84_north).intersects(g) for r in c.itertuples()]]
        out["aef"] = {str(y): int((c.year == y).sum()) for y in YEARS}
    if "tessera" in sources:
        tiles = _tess_tiles(geom)
        sample = tiles if len(tiles) <= 25 else [tiles[i * len(tiles) // 25] for i in range(25)]
        jobs = [(t, y) for t in sample for y in YEARS]
        progress.update(0.4, f"TESSERA: checking {len(sample)} tile{'s' if len(sample) > 1 else ''} × {len(YEARS)} years")
        with ThreadPoolExecutor(24) as ex:
            ok = list(ex.map(lambda ty: _tess_exists(ty[0][0], ty[0][1], ty[1]), jobs))
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
        files = _aef_files(geom, year, Path(cache_dir))
        if not files:
            raise RuntimeError(f"AlphaEarth has no {year} data for this area")
        ovr = {10: None, 20: 0, 40: 1, 80: 2, 160: 3}[int(res)]
        for i, f in enumerate(files):
            progress.update(0.05 + 0.85 * i / len(files), f"AlphaEarth {year}: file {i + 1} of {len(files)} (64 layers in parallel)")
            r = _aef_read(f.path, transform_bounds("EPSG:4326", f.crs, *wgs, densify_pts=21), ovr)
            if r:
                parts.append((_dequantize_aef(r[0]), r[1], r[2]))
    else:
        tiles = [t for t in _tess_tiles(geom)]
        progress.update(0.03, f"TESSERA {year}: checking {len(tiles)} tile{'s' if len(tiles) > 1 else ''}")
        with ThreadPoolExecutor(16) as ex:
            ok = list(ex.map(lambda t: _tess_exists(t[0], t[1], year), tiles))
        tiles = [t for t, o in zip(tiles, ok) if o]
        if not tiles:
            raise RuntimeError(f"TESSERA has no {year} data for this area: try another year (2024 covers all land)")
        done = [0]

        def one(t):
            r = _tess_read(t[0], t[1], year, wgs)
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
    Path(str(out_path) + ".json").write_text(json.dumps(meta, indent=1))
    log.info("%s %d: %d × %d pixels at %g m, %d dimensions, %.1f %% of the area covered", spec["short"], year, w, h, res, dims, meta["valid_pct"])
    progress.update(1, "Done")
    return {**meta, "path": str(out_path)}


# ------------------------------------------------------------------ explore: colour view and similarity

def _read(path: str):
    import rasterio
    with rasterio.open(path) as src:
        return src.read().astype(np.float32), src.profile


def colour_view(path: str, out_path: str, sample: int = 200_000) -> dict:
    """The three main directions of variation (PCA) as red, green and blue: similar places get similar colours."""
    import rasterio
    from sklearn.decomposition import PCA
    data, prof = _read(path)
    d, h, w = data.shape
    flat = data.reshape(d, -1).T
    ok = np.isfinite(flat).all(1)
    if ok.sum() < 10:
        raise ValueError("The layer has too few pixels with values")
    rng = np.random.default_rng(0)
    idx = np.flatnonzero(ok)
    fit = flat[rng.choice(idx, min(sample, idx.size), replace=False)]
    pca = PCA(3, random_state=0).fit(fit)
    rgb = np.zeros((3, h * w), np.uint8)
    comp = pca.transform(flat[ok])
    lo, hi = np.percentile(comp, 2, axis=0), np.percentile(comp, 98, axis=0)
    rgb[:, ok] = (np.clip((comp - lo) / np.maximum(hi - lo, 1e-9), 0, 1) * 254 + 1).T.astype(np.uint8)
    with rasterio.open(out_path, "w", driver="GTiff", width=w, height=h, count=3, dtype="uint8", crs=prof["crs"], transform=prof["transform"],
                       nodata=0, tiled=True, compress="deflate", photometric="RGB") as dst:
        dst.write(rgb.reshape(3, h, w))
        for i, n in enumerate(("PC1 (red)", "PC2 (green)", "PC3 (blue)"), 1):
            dst.set_band_description(i, n)
    return {"path": out_path, "explained": [round(float(v), 3) for v in pca.explained_variance_ratio_]}


def similarity(path: str, points: list[list[float]], out_path: str) -> dict:
    """Cosine similarity of every pixel to the mean vector of the clicked places (lon, lat): 1 = the same, 0 = unrelated."""
    import rasterio
    from rasterio.warp import transform as tr
    data, prof = _read(path)
    d, h, w = data.shape
    xs, ys = tr("EPSG:4326", prof["crs"], [p[0] for p in points], [p[1] for p in points])
    inv = ~prof["transform"]
    refs = []
    for x, y in zip(xs, ys):
        c, r = inv * (x, y)
        r, c = int(math.floor(r)), int(math.floor(c))
        if 0 <= r < h and 0 <= c < w and np.isfinite(data[:, r, c]).all():
            v = data[:, r, c]
            refs.append(v / (np.linalg.norm(v) or 1))
    if not refs:
        raise ValueError("None of the points is on the layer (or they fall on pixels without values)")
    ref = np.mean(refs, axis=0)
    ref /= np.linalg.norm(ref) or 1
    flat = data.reshape(d, -1)
    norm = np.linalg.norm(flat, axis=0)
    sim = (ref @ flat) / np.where(norm > 0, norm, np.nan)
    sim = sim.reshape(h, w).astype(np.float32)
    with rasterio.open(out_path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32", crs=prof["crs"], transform=prof["transform"],
                       nodata=np.nan, tiled=True, compress="deflate", predictor=3) as dst:
        dst.write(sim, 1)
        dst.set_band_description(1, "similarity")
    v = sim[np.isfinite(sim)]
    return {"path": out_path, "points": len(refs), "p50": round(float(np.median(v)), 3), "p95": round(float(np.percentile(v, 95)), 3),
            "share_above_0_9": round(float((v > 0.9).mean() * 100), 2)}
