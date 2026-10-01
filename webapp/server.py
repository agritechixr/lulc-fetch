"""Local web UI for lulc-fetch. Run with `lulc-fetch-web` and open http://127.0.0.1:8000."""

from __future__ import annotations

import argparse
import base64
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.warp import transform_bounds
from shapely.geometry import shape
from shapely.ops import unary_union

from lulc_fetch import sentinel2 as s2
from lulc_fetch.aoi import AOI, aoi_mask, make_grid
from lulc_fetch.extras import LABEL_PRODUCTS
from lulc_fetch.indices import INDICES
from lulc_fetch.pipeline import export_composite, export_labels, export_scene, search_geometry
from lulc_fetch.raster import read_to_grid
from lulc_fetch.sources import DEFAULT_BANDS, S2_BANDS, SOURCES, get_source

from . import aoi_io, credentials
from .jobs import JobManager

log = logging.getLogger("webapp")
STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SOURCE_TITLES = {"earth-search": "Earth Search (AWS) — no login",
                 "planetary-computer": "Microsoft Planetary Computer — no login",
                 "cdse": "Copernicus Data Space (ESA) — needs S3 keys"}

app = FastAPI(title="lulc-fetch")
jobs = JobManager()


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Reject requests from other sites (CSRF) or via DNS-rebinding hostnames."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    origin = request.headers.get("origin")
    origin_host = origin.split("://", 1)[-1].rsplit(":", 1)[0] if origin else None
    if host not in LOCAL_HOSTS or (origin_host and origin_host not in LOCAL_HOSTS):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


@app.exception_handler(RuntimeError)
@app.exception_handler(ValueError)
async def readable_errors(request: Request, exc: Exception):
    """Library errors (missing credentials, AOI too large, nothing found) carry user-facing messages."""
    return JSONResponse({"detail": str(exc)}, status_code=400)


def _aoi(geometry: dict) -> AOI:
    try:
        geom = shape(geometry)
    except Exception:
        raise HTTPException(400, "Invalid AOI geometry")
    if geom.is_empty or geom.area == 0:
        raise HTTPException(400, "The AOI must be a polygon with some area")
    return AOI(tuple(geom.bounds), [geometry])


def _source(name: str):
    if name not in SOURCES:
        raise HTTPException(400, f"Unknown source {name}")
    return get_source(name, **credentials.source_kwargs(name))


def _clip(geometry: dict | None) -> dict | None:
    """Validate an optional clip polygon (GeoJSON, EPSG:4326)."""
    if not geometry:
        return None
    _aoi(geometry)
    return geometry


def _png_data_url(rgba: np.ndarray) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with MemoryFile() as mem:
            with mem.open(driver="PNG", width=rgba.shape[2], height=rgba.shape[1],
                          count=4, dtype="uint8") as dst:
                dst.write(rgba)
            return "data:image/png;base64," + base64.b64encode(mem.read()).decode()


# ------------------------------------------------------------------ config & credentials

@app.get("/api/config")
def config():
    return {"sources": SOURCE_TITLES, "bands": S2_BANDS, "default_bands": DEFAULT_BANDS,
            "indices": list(INDICES),
            "label_products": {k: v["title"] for k, v in LABEL_PRODUCTS.items()}}


@app.get("/api/credentials")
def get_credentials():
    return {"backend": credentials.backend_name(), "providers": credentials.status()}


@app.put("/api/credentials/{provider}")
def put_credentials(provider: str, values: dict[str, str]):
    if provider not in credentials.PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    credentials.save(provider, values)
    return get_credentials()


@app.delete("/api/credentials/{provider}")
def delete_credentials(provider: str):
    if provider not in credentials.PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    credentials.delete(provider)
    return get_credentials()


@app.post("/api/credentials/{provider}/test")
def test_credentials(provider: str):
    try:
        if provider == "cdse_account":
            from lulc_fetch.cdse import get_token
            c = credentials.get_all("cdse_account")
            get_token(c["username"], c["password"])
            return {"ok": True, "message": "Logged in to Copernicus Data Space"}
        if provider == "cdse_s3":
            src = _source("cdse")
            item = next(src.client().search(collections=[src.collection], max_items=1).items())
            href = src.href(item, "SCL")
            import rasterio
            with rasterio.Env(**src.gdal_env()), rasterio.open(href) as ds:
                ds.read(1, window=((0, 16), (0, 16)))
            return {"ok": True, "message": "S3 keys work — read a test file from CDSE"}
        if provider == "planetary_computer":
            return {"ok": True, "message": "Saved (Planetary Computer works without a key; nothing to test)"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:300]}
    raise HTTPException(404, "Unknown provider")


# ------------------------------------------------------------------ AOI helpers

@app.get("/api/geocode")
def geocode(q: str):
    if len(q.strip()) < 2:
        raise HTTPException(400, "Type at least 2 characters")
    try:
        return aoi_io.geocode(q.strip())
    except Exception as e:
        raise HTTPException(502, f"Geocoding failed: {e}")


@app.post("/api/aoi/upload")
async def upload_aoi(files: list[UploadFile] = File(...)):
    payload = [(f.filename or "upload", await f.read()) for f in files]
    if sum(len(d) for _, d in payload) > 50 * 2**20:
        raise HTTPException(413, "Upload is larger than 50 MB")
    try:
        return aoi_io.parse_upload(payload)
    except Exception as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ search, metadata, preview

class SearchRequest(BaseModel):
    source: str = "earth-search"
    aoi: dict
    start: str
    end: str
    max_cloud: float = Field(30, ge=0, le=100)
    limit: int = Field(200, ge=1, le=1000)


def _item_json(item, aoi_geom):
    footprint = shape(item.geometry) if item.geometry else None
    coverage = footprint.intersection(aoi_geom).area / aoi_geom.area if footprint else None
    thumb = next((item.assets[k].href for k in ("thumbnail", "rendered_preview") if k in item.assets), None)
    props = item.properties
    return {
        "id": item.id, "datetime": props.get("datetime"), "cloud": props.get("eo:cloud_cover"),
        "tile": s2._tile(item), "platform": props.get("platform"), "coverage": coverage,
        "thumbnail": thumb, "footprint": item.geometry, "properties": props,
        "product_name": props.get("s2:product_uri") or item.id,
        "self_href": item.get_self_href(),
        "assets": [{"key": k, "title": a.title, "type": a.media_type, "href": a.href,
                    "gsd": a.extra_fields.get("gsd")} for k, a in item.assets.items()],
    }


@app.post("/api/search")
def search(req: SearchRequest):
    aoi = _aoi(req.aoi)
    src = _source(req.source)
    try:
        items = s2.search(src, aoi.bbox, req.start, req.end, req.max_cloud, req.limit,
                          intersects=search_geometry(aoi))
    except Exception as e:
        raise HTTPException(502, f"Catalog search failed: {e}")
    aoi_geom = shape(req.aoi)
    scenes = []
    for sc in s2.group_scenes(items):
        union = unary_union([shape(i.geometry) for i in sc.items if i.geometry])
        scenes.append({"date": str(sc.date), "cloud": sc.cloud, "tiles": sc.tiles,
                       "coverage": union.intersection(aoi_geom).area / aoi_geom.area,
                       # best-covering tile first, so its thumbnail represents the scene
                       "items": sorted((_item_json(i, aoi_geom) for i in sc.items),
                                       key=lambda it: -(it["coverage"] or 0))})
    return {"source": src.name, "count": len(items), "scenes": scenes}


class PreviewRequest(BaseModel):
    source: str
    item_ids: list[str]
    aoi: dict
    max_px: int = Field(900, ge=128, le=2048)


@app.post("/api/preview")
def preview(req: PreviewRequest):
    """True-colour image of the AOI from the chosen items, plus an SCL cloud overlay and stats."""
    aoi = _aoi(req.aoi)
    src = _source(req.source)
    items = list(src.client().search(collections=[src.collection], ids=req.item_ids).items())
    if not items:
        raise HTTPException(404, "Items not found")
    l, b, r, t = transform_bounds("EPSG:4326", "EPSG:3857", *aoi.bbox)
    res = max(10.0, max(r - l, t - b) / req.max_px)
    grid = make_grid(aoi, res, "EPSG:3857")  # Web Mercator, so the overlay lines up exactly in Leaflet
    env = src.gdal_env()
    scene = s2.Scene(items[0].datetime.date(), items)

    def visual():
        out = None
        for item in items:
            if src.visual_asset not in item.assets:
                continue
            data = read_to_grid(item.assets[src.visual_asset].href, grid, env, indexes=[1, 2, 3],
                                resampling=Resampling.bilinear, src_nodata=0)
            if out is None:
                out = data
            else:
                fill = np.isnan(out) & ~np.isnan(data)
                out[fill] = data[fill]
        return out

    try:
        with ThreadPoolExecutor(2) as ex:
            f_rgb = ex.submit(visual)
            f_scl = ex.submit(s2.read_band, src, scene, "SCL", grid, env)
            rgb, scl = f_rgb.result(), f_scl.result()
    except Exception as e:
        raise HTTPException(502, f"Could not read imagery: {e}")
    if rgb is None:
        raise HTTPException(404, "These items have no true-colour asset")

    region = aoi_mask(aoi, grid)
    region = region if region is not None else np.ones(grid.shape, bool)
    has_data = np.all(np.isfinite(rgb), axis=0) & region
    scl_i = np.nan_to_num(scl, nan=0).astype("uint8")
    cloud = np.isin(scl_i, (8, 9, 10)) & region
    shadow = (scl_i == 3) & region

    image = np.zeros((4, *grid.shape), "uint8")
    image[:3] = np.clip(np.nan_to_num(rgb, nan=0) * 1.15, 0, 255).astype("uint8")  # TCI is a bit dark
    image[3] = has_data * 255
    overlay = np.zeros((4, *grid.shape), "uint8")
    overlay[:, cloud] = np.array([255, 235, 59, 150], "uint8")[:, None]   # clouds: yellow
    overlay[:, shadow] = np.array([156, 39, 176, 150], "uint8")[:, None]  # shadows: purple

    n = max(int(region.sum()), 1)
    west, south, east, north = grid.bbox_lonlat()
    return {
        "image": _png_data_url(image), "clouds": _png_data_url(overlay),
        "bounds": [[south, west], [north, east]], "resolution_m": round(res, 1),
        "stats": {"data_pct": 100 * has_data.sum() / n, "cloud_pct": 100 * cloud.sum() / n,
                  "shadow_pct": 100 * shadow.sum() / n,
                  "clear_pct": 100 * (has_data & ~cloud & ~shadow & ~np.isin(scl_i, (0, 1))).sum() / n},
    }


# ------------------------------------------------------------------ jobs

class JobRequest(BaseModel):
    kind: str  # scene | composite | labels | product
    source: str = "earth-search"
    aoi: dict | None = None
    start: str | None = None
    end: str | None = None
    max_cloud: float = 40
    date: str | None = None
    bands: list[str] = Field(default_factory=lambda: list(DEFAULT_BANDS))
    indices: bool = True
    res: float = Field(10, ge=0.5, le=1000)
    mask_clouds: bool = True
    max_scenes: int = Field(20, ge=1, le=100)
    stat: str = "median"
    product: str = "worldcover"
    year: int = 2021
    product_names: list[str] = Field(default_factory=list)


@app.post("/api/jobs")
def create_job(req: JobRequest):
    bad = [b for b in req.bands if b not in S2_BANDS]
    if bad:
        raise HTTPException(400, f"Unknown bands {bad}")

    if req.kind == "product":
        if not req.product_names:
            raise HTTPException(400, "No products selected")
        acct = credentials.get_all("cdse_account")
        if not all(acct.values()):
            raise HTTPException(400, "Add your Copernicus Data Space account under Credentials first")

        def run(job):
            from lulc_fetch.cdse import download_product
            paths = []
            for name in req.product_names:
                log.info("Downloading %s from Copernicus Data Space...", name)
                paths.append(str(download_product(name, job.dir, acct["username"], acct["password"])))
            return {"files": paths}

        title = f"Original product: {', '.join(req.product_names)[:80]}"
        return jobs.submit("product", title, req.model_dump(exclude={"aoi"}), run).to_dict()

    if not req.aoi:
        raise HTTPException(400, "Draw or choose an area first")
    aoi = _aoi(req.aoi)
    grid = make_grid(aoi, req.res)
    if req.kind in ("scene", "composite"):
        if not (req.start and req.end):
            raise HTTPException(400, "Start and end dates are required")
        src = _source(req.source)
        if req.source == "cdse":
            src.gdal_env()  # fail now, not in the background, when S3 keys are missing

    if req.kind == "scene":
        name = f"S2_{req.date or req.start + '_' + req.end}"
        title = f"Sentinel-2 {'scene ' + req.date if req.date else 'best scene ' + req.start + ' → ' + req.end}"

        def run(job):
            return export_scene(src, aoi, grid, req.start, req.end, job.dir / f"{name}.tif",
                                bands=req.bands, max_cloud=req.max_cloud, date=req.date,
                                candidates=1 if req.date else 5, mask_clouds=req.mask_clouds,
                                indices=req.indices)
    elif req.kind == "composite":
        title = f"Sentinel-2 {req.stat} composite {req.start} → {req.end}"

        def run(job):
            return export_composite(src, aoi, grid, req.start, req.end,
                                    job.dir / f"S2_{req.stat}_{req.start}_{req.end}.tif",
                                    bands=req.bands, max_cloud=req.max_cloud, max_scenes=req.max_scenes,
                                    stat=req.stat, indices=req.indices)
    elif req.kind == "labels":
        if req.product not in LABEL_PRODUCTS:
            raise HTTPException(400, "Unknown label product")
        title = f"{LABEL_PRODUCTS[req.product]['title'].split(' (')[0]} {req.year}"

        def run(job):
            return export_labels(req.product, req.year, aoi, grid, job.dir / f"{req.product}_{req.year}.tif")
    else:
        raise HTTPException(400, f"Unknown job kind {req.kind}")

    log.info("Output grid: %dx%d px at %g m", grid.width, grid.height, grid.res)
    params = req.model_dump(exclude={"aoi"}) | {"grid": f"{grid.width}x{grid.height} px @ {grid.res:g} m"}
    return jobs.submit(req.kind, title, params, run).to_dict()


@app.get("/api/jobs")
def list_jobs():
    return [j.to_dict() for j in sorted(jobs.jobs.values(), key=lambda j: -j.created)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs.jobs:
        raise HTTPException(404, "No such job")
    return jobs.jobs[job_id].to_dict()


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    jobs.delete(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/files/{name}")
def job_file(job_id: str, name: str):
    job = jobs.jobs.get(job_id)
    if not job or name not in job.files():  # also blocks path traversal
        raise HTTPException(404, "No such file")
    return FileResponse(job.dir / name, filename=name)



# ------------------------------------------------------------------ analyze existing GeoTIFFs

RASTER_ROOTS = {"imports": "Imported products", "downloads": "Downloads", "uploads": "Uploaded",
                "analysis": "Analysis results", "output": "CLI output"}
RASTER_EXTS = {".tif", ".tiff", ".vrt"}  # .vrt only as written by the .SAFE importer
UPLOAD_EXTS = {".tif", ".tiff"}  # never accept uploaded VRTs: they can point at arbitrary files


def _raster_path(rel: str, roots=RASTER_ROOTS) -> Path:
    """Resolve a client-supplied relative path, refusing anything outside the allowed folders."""
    path = (Path.cwd() / rel).resolve()
    for root in roots:
        base = (Path.cwd() / root).resolve()
        if path.is_relative_to(base) and path.suffix.lower() in RASTER_EXTS and path.is_file():
            return path
    raise HTTPException(404, "No such GeoTIFF")


@app.get("/api/indices")
def index_catalog():
    from lulc_fetch.analysis import COMPOSITES, SCALE_PRESETS
    from lulc_fetch.indices import BAND_INFO, BAND_NAMES, CATALOG, CATEGORIES, COLORMAPS, required_bands

    return {
        "categories": CATEGORIES,
        "indices": [{"name": i.name, "title": i.title, "formula": i.formula, "category": i.category,
                     "description": i.description, "vmin": i.vmin, "vmax": i.vmax, "cmap": i.cmap,
                     "bands": sorted(required_bands(i.formula))} for i in CATALOG.values()],
        "composites": {k: {"title": t, "bands": list(b)} for k, (t, b) in COMPOSITES.items()},
        "colormaps": COLORMAPS,
        "scale_presets": {k: {"title": t, "scale": sc, "offset": off} for k, (t, sc, off) in SCALE_PRESETS.items()},
        "band_names": list(BAND_NAMES),
        "band_info": {k: {"name": n, "short": sh, "nm": nm, "equiv": eq} for k, (n, sh, nm, eq) in BAND_INFO.items()},
    }


@app.get("/api/formula/check")
def formula_check(formula: str):
    """Validate a custom formula and list the bands it needs (for live feedback while typing)."""
    from lulc_fetch.indices import parse_formula

    try:
        _, bands = parse_formula(formula)
        return {"ok": True, "bands": sorted(bands), "formula": formula.replace("^", "**")}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/rasters")
def list_rasters():
    out = []
    for root, label in RASTER_ROOTS.items():
        base = Path.cwd() / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*"), key=lambda p: -p.stat().st_mtime):
            if p.suffix.lower() in RASTER_EXTS and p.is_file() and len(p.relative_to(base).parts) <= 3:
                out.append({"path": str(p.relative_to(Path.cwd())), "name": p.name, "group": label,
                            "size_mb": p.stat().st_size / 1e6, "modified": p.stat().st_mtime,
                            "deletable": root in ("uploads", "analysis")})
    return out


@app.post("/api/rasters/upload")
async def upload_raster(file: UploadFile = File(...)):
    import shutil
    import uuid

    name = Path(file.filename or "image.tif").name
    if Path(name).suffix.lower() not in UPLOAD_EXTS:
        raise HTTPException(400, "Upload a GeoTIFF (.tif / .tiff)")
    dest = Path.cwd() / "uploads" / uuid.uuid4().hex[:8] / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f, length=8 << 20)
    try:
        from lulc_fetch.analysis import inspect
        inspect(dest)
    except Exception as e:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise HTTPException(400, f"Not a usable GeoTIFF: {e}")
    return {"path": str(dest.relative_to(Path.cwd()))}


@app.delete("/api/rasters")
def delete_raster(path: str):
    import shutil

    p = _raster_path(path, roots=("uploads", "analysis"))
    shutil.rmtree(p.parent, ignore_errors=True)
    return {"ok": True}


@app.get("/api/rasters/info")
def raster_info(path: str):
    from lulc_fetch.analysis import inspect

    info = inspect(_raster_path(path))
    info["path"] = path
    return info


@app.get("/api/rasters/file")
def raster_file(path: str):
    p = _raster_path(path)
    return FileResponse(p, filename=p.name)


class AnalyzeBase(BaseModel):
    path: str
    band_map: dict[str, int]
    scale: float = 1.0
    offset: float = 0.0
    index: str | None = None
    formula: str | None = Field(None, max_length=500)


class RenderRequest(AnalyzeBase):
    composite: str | None = None
    band: int | None = None
    clip: dict | None = None
    stretch: str = "fixed"  # fixed | auto | custom
    vmin: float | None = None
    vmax: float | None = None
    cmap: str | None = None


@app.post("/api/analyze/render")
def analyze_render(req: RenderRequest):
    from lulc_fetch.analysis import render

    res = render(_raster_path(req.path), band_map=req.band_map, scale=req.scale, offset=req.offset,
                 index=req.index, formula=req.formula, composite=req.composite, band=req.band, clip=_clip(req.clip),
                 stretch=req.stretch,
                 vmin=req.vmin, vmax=req.vmax, cmap=req.cmap)
    res["image"] = _png_data_url(res.pop("rgba"))
    return res


class PixelRequest(AnalyzeBase):
    lat: float
    lon: float


@app.post("/api/analyze/pixel")
def analyze_pixel(req: PixelRequest):
    from lulc_fetch.analysis import pixel

    return pixel(_raster_path(req.path), req.lon, req.lat, band_map=req.band_map, scale=req.scale,
                 offset=req.offset, index=req.index, formula=req.formula)


class ExportRequest(BaseModel):
    path: str
    band_map: dict[str, int]
    scale: float = 1.0
    offset: float = 0.0
    indices: list[str] = Field(default_factory=list)
    formulas: list[dict[str, str]] = Field(default_factory=list)  # [{"name", "formula"}]
    clip: dict | None = None


@app.post("/api/analyze/export")
def analyze_export(req: ExportRequest):
    import re
    import uuid

    from lulc_fetch.analysis import export
    from lulc_fetch.indices import CATALOG

    src = _raster_path(req.path)
    items = []
    for name in req.indices:
        if name not in CATALOG:
            raise HTTPException(400, f"Unknown index {name}")
        items.append((name, CATALOG[name].formula))
    for f in req.formulas:
        label = re.sub(r"[^A-Za-z0-9_-]", "", f.get("name") or "") or "custom"
        items.append((label, f.get("formula", "")))
    suffix = "_".join(n for n, _ in items)[:60]
    out = Path.cwd() / "analysis" / uuid.uuid4().hex[:8] / f"{src.stem}_{suffix}.tif"
    export(src, out, items, band_map=req.band_map, scale=req.scale, offset=req.offset, clip=_clip(req.clip))
    rel = str(out.relative_to(Path.cwd()))
    from urllib.parse import quote
    return {"path": rel, "name": out.name, "url": f"/api/rasters/file?path={quote(rel)}",
            "layers": [n for n, _ in items]}


# ------------------------------------------------------------------ Copernicus .SAFE products (Sentinel-1 / -2)

PRODUCT_DIRS = ("data", ".")


def _product(path: str) -> dict:
    from lulc_fetch.safe import find_products

    for p in find_products(*[Path.cwd() / d for d in PRODUCT_DIRS]):
        if Path(p["path"]).resolve() == (Path.cwd() / path).resolve() or p["path"] == path:
            return p
    raise HTTPException(404, "No such product in the data folder")


@app.get("/api/products")
def list_products():
    from lulc_fetch.safe import find_products

    out = []
    for p in find_products(*[Path.cwd() / d for d in PRODUCT_DIRS]):
        rel = str(Path(p["path"]).resolve().relative_to(Path.cwd().resolve()))
        out.append({**p, "path": rel})
    return {"folder": str(Path.cwd() / "data"), "products": out}


class ProductOpenRequest(BaseModel):
    path: str
    res: float = Field(40, ge=10, le=500)  # Sentinel-1 output pixel size
    aoi: dict | None = None                 # optional clip for Sentinel-1


@app.post("/api/products/open")
def open_product(req: ProductOpenRequest):
    from lulc_fetch import safe

    p = _product(req.path)
    src = Path.cwd() / req.path
    if p["kind"].startswith("S2"):
        out = Path.cwd() / "imports" / p["name"] / f"{p['name']}_10m.vrt"
        safe.s2_to_vrt(src, out)
        return {"kind": "raster", "path": str(out.relative_to(Path.cwd())),
                "name": f"{p['title']} · {p['date']}"}
    aoi = _aoi(req.aoi) if req.aoi else None
    title = f"Sentinel-1 σ⁰ backscatter {p['date']} · {req.res:g} m{' · clipped' if aoi else ''}"

    def run(job):
        log.info("Processing %s", p["name"])
        out = safe.s1_to_backscatter(src, job.dir / f"S1_{p['date']}_sigma0_dB_{req.res:g}m.tif", res=req.res, aoi=aoi)
        return {"files": [str(out)]}

    return {"kind": "job", "job": jobs.submit("s1", title, {"source": "Sentinel-1 GRD", "res": req.res}, run).to_dict()}


# ------------------------------------------------------------------ layer export (GeoTIFF / PNG / shapefile / KML)

EXPORT_DIR = Path.cwd() / "exports"


def _export_target(name: str, ext: str) -> Path:
    import re
    import uuid

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_.")[:80] or "layer"
    return EXPORT_DIR / uuid.uuid4().hex[:8] / f"{safe}.{ext}"


def _export_url(path: Path) -> dict:
    return {"url": f"/api/exports/{path.parent.name}/{path.name}", "name": path.name,
            "size_mb": path.stat().st_size / 1e6}


class LayerExportRequest(BaseModel):
    path: str
    format: str  # tif | png | pngw | shp
    name: str = "layer"
    band_map: dict[str, int] = Field(default_factory=dict)
    scale: float = 1.0
    offset: float = 0.0
    index: str | None = None
    formula: str | None = Field(None, max_length=500)
    composite: str | None = None
    band: int | None = None
    stretch: str = "fixed"
    vmin: float | None = None
    vmax: float | None = None
    cmap: str | None = None
    method: str = "equal"  # shapefile classes: equal | quantile | custom
    classes: int = Field(5, ge=2, le=20)
    breaks: list[float] | None = None
    sieve: int = Field(8, ge=0, le=10000)
    clip: dict | None = None


@app.post("/api/layers/export")
def export_layer(req: LayerExportRequest):
    from urllib.parse import quote

    from lulc_fetch import analysis

    src = _raster_path(req.path)
    spec = {"band_map": req.band_map, "scale": req.scale, "offset": req.offset, "index": req.index,
            "formula": req.formula, "composite": req.composite, "band": req.band, "stretch": req.stretch,
            "vmin": req.vmin, "vmax": req.vmax, "cmap": req.cmap, "clip": _clip(req.clip)}
    plain = not (req.index or req.formula or req.composite or req.band) and not req.clip
    if req.format == "tif":
        if plain:  # the layer is the file itself
            return {"url": f"/api/rasters/file?path={quote(req.path)}", "name": src.name,
                    "size_mb": src.stat().st_size / 1e6}
        out = analysis.export_layer_tif(src, _export_target(req.name, "tif"), **spec)
    elif req.format in ("png", "pngw"):
        if plain:
            raise HTTPException(400, "Choose how to display the layer first")
        out = analysis.export_png(src, _export_target(req.name, "png"), world_file=req.format == "pngw", **spec)
    elif req.format == "shp":
        out, n = analysis.polygonize(src, _export_target(req.name, "zip"), name=req.name, method=req.method,
                                     classes=req.classes, breaks=req.breaks, sieve_px=req.sieve, **spec)
        return {**_export_url(out), "features": n}
    else:
        raise HTTPException(400, f"Unknown format {req.format}")
    return _export_url(out)


class VectorExportRequest(BaseModel):
    geojson: dict
    format: str  # shp | geojson | kml
    name: str = "layer"
    clip: dict | None = None


@app.post("/api/vector/export")
def export_vector(req: VectorExportRequest):
    from lulc_fetch import vector_io

    feats = vector_io.features_from_geojson(req.geojson)
    if req.clip:
        feats = vector_io.clip_features(feats, _clip(req.clip))
    if req.format == "shp":
        out = vector_io.write_shapefile_zip(feats, "EPSG:4326", _export_target(req.name, "zip"), req.name)
    elif req.format == "kml":
        out = vector_io.write_kml(feats, _export_target(req.name, "kml"), req.name)
    elif req.format == "geojson":
        out = vector_io.write_geojson(feats, _export_target(req.name, "geojson"))
    else:
        raise HTTPException(400, f"Unknown format {req.format}")
    return {**_export_url(out), "features": len(feats)}


@app.get("/api/exports/{export_id}/{name}")
def export_file(export_id: str, name: str):
    path = (EXPORT_DIR / export_id / name).resolve()
    if not path.is_relative_to(EXPORT_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, "No such export")
    return FileResponse(path, filename=path.name)


# ------------------------------------------------------------------ static UI

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    import uvicorn

    p = argparse.ArgumentParser(description="lulc-fetch web UI")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  lulc-fetch web UI → {url}\n  Downloads are saved in {Path.cwd() / 'downloads'}\n")
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
