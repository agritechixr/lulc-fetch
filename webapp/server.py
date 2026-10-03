"""Local web UI for lulc-fetch. Run with `lulc-fetch-web` and open http://127.0.0.1:8000."""

from __future__ import annotations

import argparse
import base64
import logging
import time
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
from . import workspace as ws
from .jobs import JobManager

log = logging.getLogger("webapp")
STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SOURCE_TITLES = {"earth-search": "Earth Search (AWS) — no login",
                 "planetary-computer": "Microsoft Planetary Computer — no login",
                 "cdse": "Copernicus Data Space (ESA) — needs S3 keys",
                 "landsat-pc": "USGS Landsat via Planetary Computer — no login"}

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
    if request.method == "POST" and request.url.path.startswith("/api/") and "json" in (request.headers.get("content-type") or ""):
        import json as _json

        from .jobs import REQUEST
        try:   # remembered for the job this request may start: its settings go into History
            REQUEST.set({"endpoint": request.url.path, "body": _json.loads(await request.body() or b"null")})
        except ValueError:
            REQUEST.set(None)
    elif request.method == "POST":
        from .jobs import REQUEST
        REQUEST.set({"endpoint": request.url.path, "body": {}})
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
    from lulc_fetch.sources import MISSIONS

    return {"sources": SOURCE_TITLES, "missions": MISSIONS, "bands": S2_BANDS, "default_bands": DEFAULT_BANDS,
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
        if provider == "usgs":
            from lulc_fetch import usgs as ee
            c = credentials.get_all("usgs")
            ee.logout(ee.login(c["username"], c["token"]))
            return {"ok": True, "message": "Logged in to USGS EarthExplorer (M2M API)"}
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
    """True-colour image of the AOI from the chosen items, plus a cloud/shadow overlay and stats."""
    aoi = _aoi(req.aoi)
    src = _source(req.source)
    items = list(src.client().search(collections=[src.collection], ids=req.item_ids).items())
    if not items:
        raise HTTPException(404, "Items not found")
    l, b, r, t = transform_bounds("EPSG:4326", "EPSG:3857", *aoi.bbox)
    res = max(float(src.native_res), max(r - l, t - b) / req.max_px)
    grid = make_grid(aoi, res, "EPSG:3857")  # Web Mercator, so the overlay lines up exactly in Leaflet
    env = src.gdal_env()
    scene = s2.Scene(items[0].datetime.date(), items)

    def visual():
        if not src.visual_asset:  # build true colour from reflectance (0–0.3 → 0–255)
            rgb = [s2.read_band(src, scene, b, grid, env) for b in ("B04", "B03", "B02")]
            # gamma 0.6 brightens dark land without blowing out clouds (÷1.15 undoes the TCI boost below)
            return np.clip((np.clip(np.stack(rgb), 0, None) / 0.3) ** 0.6 * 255 / 1.15, 0, 255)
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
            f_scl = ex.submit(s2.read_band, src, scene, src.mask_band, grid, env)
            rgb, scl = f_rgb.result(), f_scl.result()
    except Exception as e:
        raise HTTPException(502, f"Could not read imagery: {e}")
    if rgb is None:
        raise HTTPException(404, "These items have no true-colour asset")

    region = aoi_mask(aoi, grid)
    region = region if region is not None else np.ones(grid.shape, bool)
    has_data = np.all(np.isfinite(rgb), axis=0) & region
    cloud, shadow, nodata = src.mask_classes(scl)
    cloud, shadow = cloud & region, shadow & region

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
                  "clear_pct": 100 * (has_data & ~cloud & ~shadow & ~nodata).sum() / n},
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
    entity_ids: list[str] = Field(default_factory=list)  # Landsat scene ids for EarthExplorer


@app.post("/api/jobs")
def create_job(req: JobRequest):
    bad = [b for b in req.bands if b not in S2_BANDS]
    if bad:
        raise HTTPException(400, f"Unknown bands {bad}")

    if req.kind == "product" and req.source == "landsat-pc":
        if not req.product_names:
            raise HTTPException(400, "No products selected")
        usgs = credentials.get_all("usgs")
        if not all(usgs.values()):
            raise HTTPException(400, "Add your USGS EarthExplorer username and M2M token under Credentials first")

        def run(job):
            from lulc_fetch import progress, usgs as ee
            paths = []
            for i, name in enumerate(req.product_names):
                ent = req.entity_ids[i] if i < len(req.entity_ids) else None
                log.info("Downloading %s from USGS EarthExplorer...", name)
                with progress.span(i / len(req.product_names), (i + 1) / len(req.product_names)):
                    paths.append(str(ee.download_product(name, ent, job.dir, usgs["username"], usgs["token"])))
            return {"files": paths}

        title = f"Landsat product (EarthExplorer): {', '.join(req.product_names)[:80]}"
        return jobs.submit("product", title, req.model_dump(exclude={"aoi"}), run).to_dict()

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
        sat, short = ("Landsat", "LS") if src.mission == "landsat" else ("Sentinel-2", "S2")
        if req.source == "cdse":
            src.gdal_env()  # fail now, not in the background, when S3 keys are missing

    if req.kind == "scene":
        name = f"{short}_{req.date or req.start + '_' + req.end}"
        title = f"{sat} {'scene ' + req.date if req.date else 'best scene ' + req.start + ' → ' + req.end}"

        def run(job):
            return export_scene(src, aoi, grid, req.start, req.end, job.dir / f"{name}.tif",
                                bands=req.bands, max_cloud=req.max_cloud, date=req.date,
                                candidates=1 if req.date else 5, mask_clouds=req.mask_clouds,
                                indices=req.indices)
    elif req.kind == "composite":
        title = f"{sat} {req.stat} composite {req.start} → {req.end}"

        def run(job):
            return export_composite(src, aoi, grid, req.start, req.end,
                                    job.dir / f"{short}_{req.stat}_{req.start}_{req.end}.tif",
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


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = jobs.cancel(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job.to_dict()


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
    path = (ws.root() / rel).resolve()
    for root in roots:
        base = (ws.root() / root).resolve()
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
        base = ws.root() / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*"), key=lambda p: -p.stat().st_mtime):
            if p.suffix.lower() in RASTER_EXTS and p.is_file() and len(p.relative_to(base).parts) <= 3:
                out.append({"path": ws.rel(p), "name": p.name, "group": label,
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
    dest = ws.root() / "uploads" / uuid.uuid4().hex[:8] / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f, length=8 << 20)
    try:
        from lulc_fetch.analysis import inspect
        inspect(dest)
    except Exception as e:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise HTTPException(400, f"Not a usable GeoTIFF: {e}")
    return {"path": ws.rel(dest)}


@app.delete("/api/rasters")
def delete_raster(path: str):
    import shutil

    p = _raster_path(path, roots=("uploads", "analysis"))
    shutil.rmtree(p.parent, ignore_errors=True)
    return {"ok": True}


@app.get("/api/rasters/metadata")
def raster_metadata(path: str):
    from lulc_fetch.analysis import metadata

    try:
        return metadata(_raster_path(path))
    except rasterio_errors() as e:
        raise HTTPException(400, f"Can't read the file: {e}")


def rasterio_errors():
    import rasterio.errors
    return (rasterio.errors.RasterioError, OSError, ValueError)


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
    rgb: list[int] | None = None
    pca: bool = False        # colour view: the three main directions of variation of all bands (embeddings)
    clip: dict | None = None
    stretch: str = "fixed"  # fixed | auto | custom
    vmin: float | None = None
    vmax: float | None = None
    cmap: str | None = None


@app.post("/api/analyze/render")
def analyze_render(req: RenderRequest):
    from lulc_fetch.analysis import render

    res = render(_raster_path(req.path), band_map=req.band_map, scale=req.scale, offset=req.offset,
                 index=req.index, formula=req.formula, composite=req.composite, band=req.band, rgb=req.rgb, clip=_clip(req.clip),
                 stretch=req.stretch,
                 vmin=req.vmin, vmax=req.vmax, cmap=req.cmap, pca=req.pca)
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
def analyze_export_job(req: ExportRequest):
    """Several indices as one GeoTIFF, as a background job (progress + cancel)."""
    _raster_path(req.path)
    _clip(req.clip)
    return jobs.submit("export", f"Export {len(req.indices) + len(req.formulas)} indices", {"format": "tif"},
                       lambda job: analyze_export(req)).to_dict()


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
    out = ws.root() / "analysis" / uuid.uuid4().hex[:8] / f"{src.stem}_{suffix}.tif"
    export(src, out, items, band_map=req.band_map, scale=req.scale, offset=req.offset, clip=_clip(req.clip))
    rel = ws.rel(out)
    from urllib.parse import quote
    return {"path": rel, "name": out.name, "url": f"/api/rasters/file?path={quote(rel)}",
            "layers": [n for n, _ in items]}


# ------------------------------------------------------------------ Copernicus .SAFE products (Sentinel-1 / -2)

def _product_dirs() -> list[Path]:
    """Where .SAFE products are looked for: the workspace (project) and the app's own data/ folder."""
    dirs = [ws.root() / "data", ws.root(), ws.APP_DIR / "data", ws.APP_DIR]
    out = []
    for d in dirs:
        if d.is_dir() and d.resolve() not in [x.resolve() for x in out]:
            out.append(d)
    return out


def _linked_file() -> Path:
    return ws.CONFIG_DIR / "products.json"


def _linked() -> list[str]:
    """Products opened from where they are (Browse…): remembered across sessions, never copied."""
    import json as _json

    try:
        items = _json.loads(_linked_file().read_text())
    except (OSError, ValueError):
        return []
    return [p for p in items if isinstance(p, str)]


def _save_linked(items: list[str]):
    import json as _json

    try:
        ws.CONFIG_DIR.mkdir(exist_ok=True)
        _linked_file().write_text(_json.dumps(items[-50:], indent=1))
    except OSError as e:
        raise HTTPException(500, f"Can't remember the product: {e}")


def _all_products() -> list[dict]:
    from lulc_fetch.safe import describe, find_products

    out, seen = [], set()
    for p in find_products(*_product_dirs()):
        key = str(Path(p["path"]).resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    for lp in _linked():
        path = Path(lp)
        key = str(path.resolve())
        if key in seen or not path.exists():
            continue
        info = describe(path)
        if info:
            seen.add(key)
            out.append({**info, "linked": True})
    return out


def _product(path: str) -> dict:
    from lulc_fetch.safe import describe, product_name

    target = (ws.root() / path).resolve()
    for p in _all_products():
        if Path(p["path"]).resolve() == target or p["path"] == path:
            return p
    # not in the list (e.g. a .SAFE.zip next to its extracted folder, which the list shows instead), but a real
    # product in a data folder or opened with Browse
    allowed = {d.resolve() for d in _product_dirs()}
    if target.exists() and product_name(target.name) and (target.parent in allowed or str(target) in {str(Path(x).resolve()) for x in _linked()}):
        info = describe(target)
        if info:
            return info
    raise HTTPException(404, "No such product in the data folder")


def _product_out(p: dict) -> dict:
    return {**p, "path": ws.rel(p["path"])}


@app.get("/api/products")
def list_products():
    return {"folder": str(ws.root() / "data"), "products": [_product_out(p) for p in _all_products()]}


# Adding a product: dropped .SAFE folders are copied file by file (only what the readers need) into data/,
# dropped .SAFE.zip files as one file; "Browse…" links a product where it is, without copying.

def _upload_name(filename: str) -> str:
    from lulc_fetch.safe import product_name

    name = product_name(filename)
    if not name:
        raise HTTPException(400, f"“{Path(filename).name}” isn't a Sentinel-1 GRD or Sentinel-2 L1C / L2A product "
                                 "(the name should look like S2B_MSIL2A_… or S1A_IW_GRDH_…)")
    return name


def _upload_rel(rel: str) -> Path:
    parts = [x for x in rel.replace("\\", "/").split("/") if x not in ("", ".")]
    if not parts or any(x == ".." or ":" in x for x in parts):
        raise HTTPException(400, f"Bad file path in the product: {rel}")
    return Path(*parts)


class ProductUploadStart(BaseModel):
    folder: str = Field(max_length=300)                      # the dropped folder's name (…SAFE)
    files: list[dict] = Field(default_factory=list, max_length=20000)   # [{"rel", "size"}]


@app.post("/api/products/upload/start")
def product_upload_start(req: ProductUploadStart):
    """Which of the dropped product's files still have to be copied (files already there with the same size are kept)."""
    name = _upload_name(req.folder)
    final = ws.root() / "data" / f"{name}.SAFE"
    incoming = ws.root() / "data" / ".incoming" / f"{name}.SAFE"
    need = []
    for f in req.files:
        rel = _upload_rel(str(f.get("rel", "")))
        size = int(f.get("size") or 0)
        have = [d / rel for d in (final, incoming) if (d / rel).is_file()]
        if not any(h.stat().st_size == size for h in have):
            need.append(rel.as_posix())
    return {"name": name, "need": need, "exists": final.is_dir()}


@app.put("/api/products/upload/file")
async def product_upload_file(request: Request, folder: str, rel: str):
    name = _upload_name(folder)
    dest = ws.root() / "data" / ".incoming" / f"{name}.SAFE" / _upload_rel(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with open(tmp, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    tmp.replace(dest)
    return {"ok": True}


class ProductUploadFinish(BaseModel):
    folder: str = Field(max_length=300)


@app.post("/api/products/upload/finish")
def product_upload_finish(req: ProductUploadFinish):
    import shutil

    from lulc_fetch.safe import describe

    name = _upload_name(req.folder)
    final = ws.root() / "data" / f"{name}.SAFE"
    incoming = ws.root() / "data" / ".incoming" / f"{name}.SAFE"
    if incoming.is_dir():
        for f in sorted(incoming.rglob("*")):
            if f.is_file() and not f.name.endswith(".part"):
                (final / f.relative_to(incoming)).parent.mkdir(parents=True, exist_ok=True)
                f.replace(final / f.relative_to(incoming))
        shutil.rmtree(incoming, ignore_errors=True)
    if not final.is_dir():
        raise HTTPException(400, "Nothing was copied")
    info = describe(final)
    log.info("Added Sentinel product %s to %s", name, final.parent)
    return _product_out(info)


@app.post("/api/products/upload/zip")
async def product_upload_zip(request: Request, filename: str):
    import zipfile

    from lulc_fetch.safe import describe

    name = _upload_name(filename)
    dest = ws.root() / "data" / f"{name}.SAFE.zip"
    if not (dest.is_file() and request.headers.get("content-length") == str(dest.stat().st_size)):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        try:
            with zipfile.ZipFile(tmp) as z:
                if not any(".SAFE/" in m for m in z.namelist()[:50]):
                    raise HTTPException(400, f"{filename} doesn't contain a .SAFE product folder")
        except zipfile.BadZipFile:
            tmp.unlink(missing_ok=True)
            raise HTTPException(400, f"{filename} isn't a valid zip file (is the download complete?)")
        except HTTPException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
    log.info("Added Sentinel product %s to %s", dest.name, dest.parent)
    return _product_out(describe(dest))


class ProductLinkRequest(BaseModel):
    path: str = Field(max_length=2000)


@app.post("/api/products/link")
def product_link(req: ProductLinkRequest):
    """Open a product where it is on this computer (a .SAFE folder or .SAFE.zip, or a folder holding one)."""
    import os

    from lulc_fetch.safe import describe, find_products, product_name

    p = Path(os.path.expanduser(req.path.strip().strip('"').strip("'")))
    if not p.is_absolute() or not p.exists():
        raise HTTPException(400, f"{p} doesn't exist")
    if product_name(p.name):
        products = [describe(p)]
    elif p.is_dir() and find_products(p):          # a folder that holds products
        products = find_products(p)
    else:
        raise HTTPException(400, f"“{p.name}” isn't a Sentinel-1 GRD or Sentinel-2 L1C / L2A product folder (…SAFE) or zip")
    items = [x for x in _linked() if x not in [q["path"] for q in products]] + [q["path"] for q in products]
    _save_linked(items)
    return {"products": [_product_out({**q, "linked": True}) for q in products]}


@app.delete("/api/products/link")
def product_unlink(path: str):
    target = (ws.root() / path).resolve()
    _save_linked([x for x in _linked() if Path(x).resolve() != target])
    return {"ok": True}


class ProductOpenRequest(BaseModel):
    path: str
    res: float = Field(40, ge=10, le=500)  # Sentinel-1 output pixel size
    aoi: dict | None = None                 # optional clip for Sentinel-1


@app.post("/api/products/open")
def open_product(req: ProductOpenRequest):
    from lulc_fetch import safe

    p = _product(req.path)
    src = Path(p["path"])
    if p["kind"].startswith("S2"):
        out = ws.root() / "imports" / p["name"] / f"{p['name']}_10m.vrt"
        safe.s2_to_vrt(src, out)
        return {"kind": "raster", "path": ws.rel(out),
                "name": f"{p['title']} · {p['date']}"}
    aoi = _aoi(req.aoi) if req.aoi else None
    title = f"Sentinel-1 σ⁰ backscatter {p['date']} · {req.res:g} m{' · clipped' if aoi else ''}"

    def run(job):
        log.info("Processing %s", p["name"])
        out = safe.s1_to_backscatter(src, job.dir / f"S1_{p['date']}_sigma0_dB_{req.res:g}m.tif", res=req.res, aoi=aoi)
        return {"files": [str(out)]}

    return {"kind": "job", "job": jobs.submit("s1", title, {"source": "Sentinel-1 GRD", "res": req.res}, run).to_dict()}


# ------------------------------------------------------------------ PCA & dimensionality reduction (scikit-learn)

@app.get("/api/pca/schema")
def pca_schema():
    from lulc_fetch import pca

    return pca.schema()


class PcaRequest(BaseModel):
    path: str
    bands: list[int]
    method: str = "pca"
    params: dict = Field(default_factory=dict)
    clip: dict | None = None
    name: str = "image"


@app.post("/api/pca/run")
def pca_run(req: PcaRequest):
    import re

    from lulc_fetch import pca

    src = _raster_path(req.path)
    if req.method not in pca.METHODS:
        raise HTTPException(400, f"Unknown method {req.method}")
    clip = _clip(req.clip)
    title = f"{pca.METHODS[req.method]['title']} · {req.name}"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{req.method}_{req.name}")[:60]

    def run(job):
        report = pca.run(src, job.dir / f"{stem}.tif", bands=req.bands, method=req.method, params=req.params, clip=clip)
        report["path"] = ws.rel(job.dir / f"{stem}.tif")
        return report

    return jobs.submit("pca", title, {"source": pca.METHODS[req.method]["full"]}, run).to_dict()


# ------------------------------------------------------------------ Classical ML: tables (raster → table and future sub-tools)

TABLE_DIR = ws.Dir("tables")
TABLE_EXTS = {".csv", ".parquet"}


def _table_path(rel: str) -> Path:
    p = (ws.root() / rel).resolve()
    if not p.is_relative_to(TABLE_DIR.resolve()) or p.suffix.lower() not in TABLE_EXTS or not p.is_file():
        raise HTTPException(404, "No such table")
    return p


@app.get("/api/tables")
def list_tables():
    import json as _json

    out = []
    if TABLE_DIR.is_dir():
        for p in sorted(TABLE_DIR.iterdir(), key=lambda p: -p.stat().st_mtime):
            if p.suffix.lower() in TABLE_EXTS:
                meta = {}
                side = p.with_suffix(p.suffix + ".json")
                if side.exists():
                    try:
                        meta = _json.loads(side.read_text())
                    except ValueError:
                        pass
                out.append({"path": ws.rel(p), "name": p.name, "size_mb": p.stat().st_size / 1e6,
                            "modified": p.stat().st_mtime, "rows": meta.get("rows"), "columns": meta.get("columns"),
                            "target": meta.get("target"), "class_counts": meta.get("class_counts"),
                            "source": Path(meta.get("source", "")).name})
    return out


@app.get("/api/tables/preview")
def table_preview(path: str, n: int = 20):
    p = _table_path(path)
    if p.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq
        f = pq.ParquetFile(p)
        t = next(f.iter_batches(batch_size=n)).to_pylist() if f.metadata.num_rows else []
        return {"columns": f.schema_arrow.names, "rows": [list(r.values()) for r in t], "total": f.metadata.num_rows}
    import csv
    with open(p, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        cols = next(rd, [])
        rows = [r for _, r in zip(range(n), rd)]
    return {"columns": cols, "rows": rows}


@app.get("/api/tables/file")
def table_file(path: str):
    p = _table_path(path)
    return FileResponse(p, filename=p.name)


@app.delete("/api/tables")
def delete_table(path: str):
    p = _table_path(path)
    p.unlink(missing_ok=True)
    p.with_suffix(p.suffix + ".json").unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/tables/upload")
async def upload_table(file: UploadFile = File(...)):
    """Add a CSV / TSV / Parquet / Excel table: it is stored in tables/ so every tool can use it."""
    import shutil
    import tempfile

    from lulc_fetch.tableview import TABLE_IMPORT_EXTS, import_table

    name = Path(file.filename or "table.csv").name
    if Path(name).suffix.lower() not in TABLE_IMPORT_EXTS:
        raise HTTPException(400, "Tables can be CSV, TSV, TXT, Parquet or Excel (.xlsx)")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / name
        with open(src, "wb") as f:
            shutil.copyfileobj(file.file, f, length=8 << 20)
        try:
            out = import_table(src, TABLE_DIR)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Couldn't read {name}: {e}")
    return {"path": ws.rel(out), "name": out.name}


@app.get("/api/tables/rows")
def table_rows(path: str, offset: int = 0, limit: int = 100, q: str = "", sort: str | None = None, desc: bool = False):
    from lulc_fetch import tableview

    try:
        p = _table_path(path)
        return {**tableview.page(p, offset=max(0, offset), limit=max(1, min(limit, 1000)), query=q[:200], sort=sort, desc=desc),
                "undo": tableview.undo_count(p)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class TableEditRequest(BaseModel):
    path: str
    ops: list[dict] = Field(min_length=1, max_length=50)


@app.post("/api/tables/edit")
def table_edit(req: TableEditRequest):
    """Edit a table: add / calculate / rename / convert / delete fields, edit cells, add / delete rows (one undo step)."""
    from lulc_fetch import tableview

    try:
        return tableview.edit(_table_path(req.path), req.ops)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e).strip("'"))


class TablePathRequest(BaseModel):
    path: str


@app.post("/api/tables/undo")
def table_undo(req: TablePathRequest):
    from lulc_fetch import tableview

    try:
        return tableview.undo(_table_path(req.path))
    except ValueError as e:
        raise HTTPException(400, str(e))


class EditStartRequest(BaseModel):
    path: str


@app.post("/api/tables/edit/start")
def table_edit_start(req: EditStartRequest):
    """Start (or resume) an edit session: changes go to a working copy until they are saved."""
    from lulc_fetch import tableview

    p = _table_path(req.path)
    if tableview.is_work(p):
        raise HTTPException(400, "This is already an edit session")
    r = tableview.edit_start(p)
    return {**r, "work": ws.rel(r["work"]), "original": ws.rel(r["original"])}


class EditLogRequest(BaseModel):
    path: str
    add: list[str] = Field(default_factory=list, max_length=50)
    pop: int = Field(0, ge=0, le=50)


@app.post("/api/tables/edit/log")
def table_edit_log(req: EditLogRequest):
    from lulc_fetch import tableview

    p = _table_path(req.path)
    if not tableview.is_work(p):
        raise HTTPException(400, "Not an edit session")
    return {"log": tableview.edit_log(p, [a[:200] for a in req.add], req.pop)}


class EditSaveRequest(BaseModel):
    path: str
    mode: str = "overwrite"   # overwrite | new
    name: str | None = Field(None, max_length=80)


@app.post("/api/tables/edit/save")
def table_edit_save(req: EditSaveRequest):
    from lulc_fetch import tableview

    p = _table_path(req.path)
    if not tableview.is_work(p) or req.mode not in ("overwrite", "new"):
        raise HTTPException(400, "Not an edit session")
    try:
        dest = tableview.edit_save(p, req.mode, req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": ws.rel(dest), "name": dest.name}


@app.post("/api/tables/edit/discard")
def table_edit_discard(req: EditStartRequest):
    from lulc_fetch import tableview

    p = _table_path(req.path)
    try:
        tableview.edit_discard(p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/tables/restore")
def table_restore(req: EditStartRequest):
    """Bring back the version from before the last save over this table."""
    from lulc_fetch import tableview

    try:
        return tableview.restore_previous(_table_path(req.path))
    except ValueError as e:
        raise HTTPException(400, "No previous version is kept for this table" if "undo" in str(e).lower() else str(e))


class PythonRequest(BaseModel):
    path: str | None = None                       # a table (edit session working copy)
    code: str = Field(max_length=100_000)
    apply: bool = False
    columns: dict[str, list] | None = None        # or vector attributes (+ geometries)
    geometries: list[dict | None] | None = None
    n: int = Field(0, ge=0, le=2_000_000)


def _python_result(r: dict, preview: int = 8) -> dict:
    import math

    import pandas as pd
    out = {"ok": r["ok"], "output": r["output"], "error": r["error"]}
    if r["ok"]:
        df = pd.read_parquet(r["out"])
        out["meta"] = r["meta"]
        cols = [c for c in df.columns if c != "__row__"]
        clean = lambda v: None if v is None or (isinstance(v, float) and not math.isfinite(v)) else (v.item() if hasattr(v, "item") else v)
        out["preview"] = {"columns": cols, "rows": [[clean(v) for v in row] for row in df[cols].head(preview).itertuples(index=False)]}
    return out


@app.post("/api/python/run")
def python_run(req: PythonRequest):
    """Run the user's Python on a table (edit session) or on vector attributes, in a separate process (a job)."""
    import shutil
    import tempfile

    import pandas as pd

    from lulc_fetch import pyexec, tableview

    work = _table_path(req.path) if req.path else None
    if work is not None and req.apply and not tableview.is_work(work):
        raise HTTPException(400, "Start editing first: changes go to a working copy until you save them")
    if work is None and req.columns is None:
        raise HTTPException(400, "Nothing to run on")

    def run(job):
        tmp = Path(tempfile.mkdtemp(prefix="lulc_in_"))
        try:
            if work is not None:
                src = tableview.to_parquet_for_python(work, tmp / "in.parquet")
            else:
                data = {"__row__": [float(i) for i in range(req.n)]}
                for c, vals in (req.columns or {}).items():
                    if all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for v in vals):
                        data[c] = [float("nan") if v is None else float(v) for v in vals]
                    elif all(v is None or isinstance(v, bool) for v in vals):
                        data[c] = vals
                    else:
                        data[c] = [None if v is None else v if isinstance(v, str) else str(v) for v in vals]
                if req.geometries is not None:
                    import json as _json
                    data["__geometry__"] = [_json.dumps(g) if g else None for g in req.geometries]
                src = tmp / "in.parquet"
                pd.DataFrame(data).to_parquet(src, index=False)
            r = pyexec.run(src, req.code)
            res = _python_result(r)
            if r["ok"] and work is not None and req.apply:
                res["applied"] = tableview.replace_from_python(work, r["out"])
            elif r["ok"] and work is None:
                df = pd.read_parquet(r["out"])
                cols = [c for c in df.columns if c != "__row__"]
                import math
                clean = lambda v: None if v is None or (isinstance(v, float) and not math.isfinite(v)) else (v.item() if hasattr(v, "item") else v)
                res["result"] = {"columns": cols, "index": [None if not math.isfinite(v) else int(v) for v in df["__row__"].tolist()],
                                 "rows": [[clean(v) for v in row] for row in df[cols].itertuples(index=False)]}
            shutil.rmtree(r.get("dir", ""), ignore_errors=True)
            return res
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return jobs.submit("python", "Python script" + (" (apply)" if req.apply else " (test)"), {"source": "Data viewer"}, run).to_dict()


@app.get("/api/tables/calc-preview")
def table_calc_preview(path: str, expression: str):
    from lulc_fetch import tableview

    try:
        return tableview.calc_preview(_table_path(path), expression)
    except ValueError as e:
        raise HTTPException(400, str(e))


class TableDeriveRequest(BaseModel):
    path: str
    q: str = ""
    name: str = Field("selection", max_length=80)


@app.post("/api/tables/derive")
def table_derive(req: TableDeriveRequest):
    """Save the rows matching the current search / filter as a new table."""
    from lulc_fetch import tableview

    try:
        out = tableview.derive(_table_path(req.path), req.q[:200], TABLE_DIR.path, req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": ws.rel(out), "name": out.name}


class FieldCalcRequest(BaseModel):
    expression: str = Field(max_length=2000)
    columns: dict[str, list] = Field(default_factory=dict)   # attribute values of a vector layer
    geometries: list[dict | None] | None = None
    n: int = Field(ge=0, le=2_000_000)


@app.post("/api/fields/calc")
def field_calc(req: FieldCalcRequest):
    """Field calculator for vector layers (attributes live in the browser): returns the new values."""
    import math

    from lulc_fetch import fieldcalc

    try:
        used, geoms = fieldcalc.referenced(req.expression, list(req.columns))
        data = {}
        for c in used:
            vals = req.columns.get(c)
            if vals is None:
                continue
            if all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for v in vals):
                data[c] = np.array([np.nan if v is None else float(v) for v in vals])
            else:
                data[c] = np.array([None if v is None else v if isinstance(v, str) else str(v) for v in vals], dtype=object)
        g = None
        if geoms:
            if req.geometries is None or len(req.geometries) != req.n:
                raise fieldcalc.CalcError("Geometry values ($area …) only work on vector layers")
            g = fieldcalc.geometry_measures(req.geometries, geoms)
        vals, typ = fieldcalc.evaluate(req.expression, data, req.n, g)
    except fieldcalc.CalcError as e:
        raise HTTPException(400, str(e))
    out = []
    for v in vals.tolist():
        if isinstance(v, float) and not math.isfinite(v):
            out.append(None)
        elif typ == "integer" and isinstance(v, float):
            out.append(int(v))
        else:
            out.append(v)
    return {"values": out, "type": typ}


@app.get("/api/fields/functions")
def field_functions():
    from lulc_fetch import fieldcalc

    return {"functions": fieldcalc.FUNC_HELP, "geometry": list(fieldcalc.GEOM_VARS)}


@app.get("/api/tables/stats")
def table_stats(path: str):
    from lulc_fetch import tableview

    return tableview.stats(_table_path(path))


@app.get("/api/tables/points")
def table_points(path: str, q: str = "", lon: str | None = None, lat: str | None = None):
    from lulc_fetch import tableview

    try:
        return tableview.points(_table_path(path), query=q[:200], lon=lon, lat=lat)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ pictures (JPG / PNG …)

def _picture_path(rel: str) -> Path:
    from lulc_fetch.images import PICTURE_EXTS

    p = (ws.root() / rel).resolve()
    if not p.is_relative_to((ws.root() / "uploads").resolve()) or p.suffix.lower() not in PICTURE_EXTS or not p.is_file():
        raise HTTPException(404, "No such picture")
    return p


@app.post("/api/pictures/upload")
async def upload_picture(files: list[UploadFile] = File(...)):
    """A picture plus optional world file (.jgw / .pgw / .wld) and .prj. Georeferenced → GeoTIFF layer."""
    import shutil
    import uuid

    from lulc_fetch.images import PICTURE_EXTS, SIDECAR_EXTS, import_picture

    pics = [f for f in files if Path(f.filename or "").suffix.lower() in PICTURE_EXTS]
    if len(pics) != 1:
        raise HTTPException(400, "Upload one picture (JPG, PNG, BMP, GIF or WebP) with its optional world file")
    pic_name = Path(pics[0].filename).name
    dest_dir = ws.root() / "uploads" / uuid.uuid4().hex[:8]
    dest_dir.mkdir(parents=True)
    stem = Path(pic_name).stem
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if f is not pics[0] and ext not in SIDECAR_EXTS:
            continue
        # sidecars get the picture's name so GDAL finds them (photo.jgw, photo.prj, photo.png.aux.xml)
        name = pic_name if f is pics[0] else (f"{pic_name}.aux.xml" if (f.filename or "").lower().endswith(".aux.xml") else stem + ext)
        with open(dest_dir / name, "wb") as out:
            shutil.copyfileobj(f.file, out, length=8 << 20)
    try:
        res = import_picture(dest_dir / pic_name)
    except Exception as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise HTTPException(400, f"Couldn't read {pic_name}: {e}")
    res["path"] = ws.rel(Path(res["path"]))
    res["name"] = pic_name
    return res


@app.get("/api/pictures/file")
def picture_file(path: str):
    p = _picture_path(path)
    return FileResponse(p, filename=p.name, content_disposition_type="inline")


class GeorefRequest(BaseModel):
    path: str
    bounds: list[float] = Field(min_length=4, max_length=4)  # west, south, east, north


@app.post("/api/pictures/georef")
def picture_georef(req: GeorefRequest):
    from lulc_fetch.images import georeference

    p = _picture_path(req.path)
    try:
        out = georeference(p, req.bounds)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": ws.rel(out)}


class RasterTableRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    clip: dict | None = None
    factor: int = Field(1, ge=1, le=64)
    scale: float = 1.0
    offset: float = 0.0
    ground_truth: dict | None = None  # {"type": "raster", "path", "band"} | {"type": "vector", "geojson", "field"}
    label_name: str = Field("label", max_length=40)
    labelled_only: bool = True
    sampling: str = "all"  # all | random | stratified
    sample_size: int = Field(100_000, ge=10, le=50_000_000)
    per_class: int = Field(5_000, ge=1, le=10_000_000)
    xy: bool = True
    lonlat: bool = True
    rowcol: bool = False
    drop_nodata: bool = True
    format: str = "csv"
    name: str = "table"
    class_colors: dict[str, str] | None = None


@app.post("/api/tables/from-raster")
def raster_to_table_job(req: RasterTableRequest):
    import re
    import shutil

    from lulc_fetch.tabular import raster_to_table

    src = _raster_path(req.path)
    if req.format not in ("csv", "parquet"):
        raise HTTPException(400, "Format must be csv or parquet")
    if req.sampling not in ("all", "random", "stratified"):
        raise HTTPException(400, "Unknown sampling")
    gt = dict(req.ground_truth) if req.ground_truth else None
    if gt and gt.get("type") == "raster":
        gt["path"] = str(_raster_path(gt.get("path", "")))
    elif gt and gt.get("type") == "vector":
        if not isinstance(gt.get("geojson"), dict):
            raise HTTPException(400, "Vector ground truth needs GeoJSON")
    elif gt:
        raise HTTPException(400, "Ground truth must be a raster or vector layer")
    clip = _clip(req.clip)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:80] or "table"

    def run(job):
        tmp = job.dir / f"{stem}.{req.format}"
        rep = raster_to_table(src, tmp, bands=req.bands, clip=clip, factor=req.factor, scale=req.scale, offset=req.offset,
                              ground_truth=gt, label_name=req.label_name, labelled_only=req.labelled_only,
                              sampling=req.sampling, sample_size=req.sample_size, per_class=req.per_class,
                              xy=req.xy, lonlat=req.lonlat, rowcol=req.rowcol, drop_nodata=req.drop_nodata, fmt=req.format,
                              class_colors=req.class_colors)
        TABLE_DIR.mkdir(exist_ok=True)
        dest = TABLE_DIR / tmp.name
        i = 2
        while dest.exists():  # never overwrite an earlier table
            dest = TABLE_DIR / f"{stem}_{i}.{req.format}"
            i += 1
        shutil.move(tmp, dest)
        shutil.move(str(tmp) + ".json", str(dest) + ".json")
        rep.update(path=ws.rel(dest), name=dest.name)
        import json as _json
        Path(str(dest) + ".json").write_text(_json.dumps({k: v for k, v in rep.items() if k != "preview"}, indent=1))
        return rep

    return jobs.submit("table", f"Raster → table · {req.name}", {"source": "Raster → table"}, run).to_dict()


# ------------------------------------------------------------------ Classical ML: train models and apply them

MODEL_DIR = ws.Dir("models")


def _model_path(rel: str) -> Path:
    p = (ws.root() / rel).resolve()
    if not p.is_relative_to(MODEL_DIR.resolve()) or p.suffix != ".joblib" or not p.is_file():
        raise HTTPException(404, "No such model")
    return p


@app.get("/api/ml/schema")
def ml_schema():
    from lulc_fetch import ml

    return ml.schema()


@app.get("/api/tables/describe")
def table_describe(path: str):
    from lulc_fetch import ml

    return ml.describe_table(_table_path(path))


class TrainRequest(BaseModel):
    table: str
    target: str
    features: list[str]
    model: str = "rf"
    task: str = "auto"
    params: dict = Field(default_factory=dict)
    common: dict = Field(default_factory=dict)
    name: str = Field("model", max_length=80)
    categorical: list[str] = Field(default_factory=list)
    tuning: dict = Field(default_factory=dict)
    report_dir: str | None = Field(None, max_length=1000)  # also save the HTML evaluation report in this folder


def _report_folder(folder: str) -> Path:
    """Validate a folder typed by the user for saving evaluation reports (created if missing)."""
    import os
    import tempfile

    raw = folder.strip().strip('"').strip("'")
    d = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not raw or not d.is_absolute():
        raise HTTPException(400, "Give a full folder path, e.g. /Users/you/Documents/reports or ~/Documents/reports")
    if d.exists() and not d.is_dir():
        raise HTTPException(400, f"{d} is a file, not a folder")
    try:
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=d, prefix=".lulc_write_test_"):
            pass
    except OSError as e:
        raise HTTPException(400, f"Can't write to {d}: {e.strerror or e}")
    return d


def _copy_report(html: Path, folder: Path) -> Path:
    """Copy an evaluation report into `folder` without overwriting an existing file."""
    import shutil

    dest, i = folder / html.name, 2
    while dest.exists():
        dest = folder / f"{html.name.removesuffix('.evaluation.html')}_{i}.evaluation.html"
        i += 1
    shutil.copy2(html, dest)
    return dest


class CompareRequest(BaseModel):
    table: str
    target: str
    features: list[str]
    task: str = "auto"
    common: dict = Field(default_factory=dict)
    categorical: list[str] = Field(default_factory=list)
    max_rows: int = Field(20000, ge=500, le=200000)


@app.post("/api/ml/compare")
def ml_compare(req: CompareRequest):
    from lulc_fetch import ml

    table = _table_path(req.table)
    if req.task not in ("auto", "classification", "regression"):
        raise HTTPException(400, "Unknown task")

    def run(job):
        return ml.compare(table, target=req.target, features=req.features, task=req.task, common=req.common,
                          categorical=req.categorical, max_rows=req.max_rows)

    return jobs.submit("compare", f"Compare models · {table.name}", {"source": "Classical ML"}, run).to_dict()


@app.post("/api/ml/train")
def ml_train(req: TrainRequest):
    import shutil

    from lulc_fetch import ml

    table = _table_path(req.table)
    if req.model not in ml.MODELS:
        raise HTTPException(400, f"Unknown model {req.model}")
    if req.task not in ("auto", "classification", "regression"):
        raise HTTPException(400, "Unknown task")

    report_dir = _report_folder(req.report_dir) if req.report_dir and req.report_dir.strip() else None

    def run(job):
        rep = ml.train(table, job.dir, target=req.target, features=req.features, model=req.model, task=req.task,
                       params=req.params, common=req.common, name=req.name, categorical=req.categorical,
                       tuning=req.tuning)
        MODEL_DIR.mkdir(exist_ok=True)
        tmp = Path(rep["path"])
        dest, i = MODEL_DIR / tmp.name, 2
        while dest.exists():
            dest = MODEL_DIR / f"{tmp.stem}_{i}.joblib"
            i += 1
        shutil.move(tmp, dest)
        ev_tmp = tmp.with_suffix(".evaluation.html")
        if ev_tmp.exists():
            shutil.move(ev_tmp, dest.with_suffix(".evaluation.html"))
            rep["evaluation_html"] = ws.rel(dest.with_suffix(".evaluation.html"))
            if report_dir is not None:
                try:
                    rep["evaluation_saved_to"] = str(_copy_report(dest.with_suffix(".evaluation.html"), report_dir))
                    log.info("Evaluation report saved to %s", rep["evaluation_saved_to"])
                except OSError as e:  # never lose a trained model because of the copy
                    rep.setdefault("warnings", []).append(f"Couldn't save the report to {report_dir}: {e}")
        rep["path"] = ws.rel(dest)
        rep["table"] = ws.rel(table)
        import json as _json
        Path(str(dest) + ".json").write_text(_json.dumps(rep, indent=1, default=str))
        Path(str(tmp) + ".json").unlink(missing_ok=True)
        return rep

    title = f"Train {ml.MODELS[req.model]['title']} · {req.name}"
    return jobs.submit("train", title, {"source": ml.MODELS[req.model]["title"]}, run).to_dict()


# ------------------------------------------------------------------ Classical ML for rasters (image + ground truth → map)

@app.get("/api/rasterml/schema")
def rasterml_schema():
    from lulc_fetch import rasterml

    return rasterml.schema()


@app.get("/api/rasterml/inspect")
def rasterml_inspect(path: str, bands: str = ""):
    from lulc_fetch import rasterml

    idx = [int(b) for b in bands.split(",") if b.strip()] or None
    try:
        return rasterml.inspect_kind(_raster_path(path), idx)
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e))


class RasterMLRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    ground_truth: dict
    model: str = "rf"
    params: dict = Field(default_factory=dict)
    common: dict = Field(default_factory=dict)
    tuning: dict = Field(default_factory=dict)
    clip: dict | None = None
    map_whole: bool = True
    factor: int = Field(1, ge=1, le=64)
    scale: float = 1.0
    offset: float = 0.0
    per_class: int | None = Field(3000, ge=10, le=10_000_000)
    name: str = Field("classified", max_length=80)
    class_colors: dict[str, str] | None = None
    confidence: bool = True
    resolution: str = "auto"


@app.post("/api/rasterml/run")
def rasterml_run(req: RasterMLRequest):
    import json as _json
    import shutil

    from lulc_fetch import ml, rasterml

    src = _raster_path(req.path)
    if req.model not in ml.MODELS:
        raise HTTPException(400, f"Unknown model {req.model}")
    gt = dict(req.ground_truth)
    if gt.get("type") == "raster":
        gt["path"] = str(_raster_path(gt["path"]))
    clip = _clip(req.clip) if req.clip else None

    def run(job):
        res = rasterml.classify(src, job.dir, bands=req.bands, ground_truth=gt, model=req.model, params=req.params, common=req.common,
                                tuning=req.tuning, clip=clip, map_clip=None if req.map_whole else clip, factor=req.factor, scale=req.scale,
                                offset=req.offset, per_class=req.per_class, name=req.name, class_colors=req.class_colors,
                                confidence=req.confidence, resolution=req.resolution)
        rep = res["model"]
        # the model joins Your models (with its evaluation report); no table is kept
        MODEL_DIR.mkdir(exist_ok=True)
        tmp = Path(rep["path"])
        dest, i = MODEL_DIR / tmp.name, 2
        while dest.exists():
            dest = MODEL_DIR / f"{tmp.stem}_{i}.joblib"
            i += 1
        shutil.move(tmp, dest)
        ev = tmp.with_suffix(".evaluation.html")
        if ev.exists():
            shutil.move(ev, dest.with_suffix(".evaluation.html"))
            rep["evaluation_html"] = ws.rel(dest.with_suffix(".evaluation.html"))
        Path(str(tmp) + ".json").unlink(missing_ok=True)
        rep["path"], rep["table"] = ws.rel(dest), src.name
        Path(str(dest) + ".json").write_text(_json.dumps(rep, indent=1, default=str))
        res["model_path"] = rep["path"]
        if res.get("map"):
            res["map"]["path"] = ws.rel(res["map"]["path"])
            res["path"] = res["map"]["path"]          # the map: added to Contents, saved by "also save to a folder"
        res["outputs"] = [res["model_path"]]
        return res

    title = f"Raster classification · {ml.MODELS[req.model]['title']} · {req.name}"
    return jobs.submit("rasterml", title, {"source": src.name}, run).to_dict()


# ------------------------------------------------------------------ Make training data (deep-learning patches)

PATCH_DIR = ws.Dir("training_data")


class PatchInput(BaseModel):
    path: str
    bands: list[int] | None = None
    name: str | None = Field(None, max_length=120)
    scale: float = 1.0
    offset: float = 0.0


class PatchPlanRequest(BaseModel):
    path: str
    clip: dict | None = None
    patch_m: list[float] = Field(..., min_length=2, max_length=2)
    overlap_m: list[float] = Field(default_factory=lambda: [0, 0], min_length=2, max_length=2)
    edge: str = Field("pad", pattern="^(pad|drop)$")


class PatchRequest(PatchPlanRequest):
    inputs: list[PatchInput] = Field(..., min_length=1, max_length=20)
    ground_truth: dict | None = None
    name: str = Field("training_patches", max_length=80)
    folder: str | None = Field(None, max_length=1000)   # where the dataset folder is created (default: the project)
    min_valid: float = Field(0.5, ge=0, le=1)
    require_labels: bool = False
    min_labelled: float = Field(0.01, ge=0, le=1)
    remap: bool = True
    class_colors: dict[str, str] | None = None


def _check_patch_sizes(req: PatchPlanRequest):
    if min(req.patch_m) <= 0:
        raise HTTPException(400, "The patch size must be above 0")
    if min(req.overlap_m) < 0:
        raise HTTPException(400, "The overlap can't be negative")


@app.post("/api/patches/plan")
def patches_plan(req: PatchPlanRequest):
    from lulc_fetch import patches

    _check_patch_sizes(req)
    try:
        return patches.plan(_raster_path(req.path), clip=_clip(req.clip) if req.clip else None, patch_m=req.patch_m,
                            overlap_m=req.overlap_m, edge=req.edge)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/patches/make")
def patches_make(req: PatchRequest):
    from lulc_fetch import patches

    _check_patch_sizes(req)
    inputs = [{**i.model_dump(), "path": str(_raster_path(i.path))} for i in req.inputs]
    gt = dict(req.ground_truth) if req.ground_truth else None
    if gt and gt.get("type") == "raster":
        gt["path"] = str(_raster_path(gt["path"]))
    clip = _clip(req.clip) if req.clip else None
    parent = _report_folder(req.folder) if req.folder and req.folder.strip() else PATCH_DIR.path
    out = patches.dataset_folder(parent, req.name)
    if out.exists() and any(out.iterdir()):
        raise HTTPException(400, f"{out} already exists and isn't empty. Choose another name or folder.")

    def run(job):
        res = patches.make(inputs, parent, name=req.name, ground_truth=gt, clip=clip, patch_m=req.patch_m, overlap_m=req.overlap_m,
                           edge=req.edge, min_valid=req.min_valid, require_labels=req.require_labels, min_labelled=req.min_labelled,
                           remap=req.remap, class_colors=req.class_colors)
        res["outputs"] = []
        if res.get("labels_dir"):
            _remember_dl("dl_datasets.json", res["folder"])   # offered by Train classify model
        return res

    title = f"Make training data · {req.name}"
    return jobs.submit("patches", title, {"source": Path(inputs[0]["path"]).name}, run).to_dict()


# ------------------------------------------------------------------ Deep learning (optional PyTorch add-on)

DL_MODEL_DIR = ws.Dir("models")


def _addon_dir() -> Path:
    """Where the frozen app installs the deep-learning add-on (pip --target), per Python version."""
    import sys
    return ws.APP_DIR / "addons" / f"py{sys.version_info.major}{sys.version_info.minor}"


def _remembered(fname: str) -> list[str]:
    import json as _json
    try:
        return [x for x in _json.loads((ws.CONFIG_DIR / fname).read_text()) if isinstance(x, str)]
    except (OSError, ValueError):
        return []


def _remember_dl(fname: str, folder: str, remove: bool = False):
    import json as _json
    items = [x for x in _remembered(fname) if Path(x).resolve() != Path(folder).resolve()]
    if not remove:
        items.append(str(Path(folder).resolve()))
    try:
        ws.CONFIG_DIR.mkdir(exist_ok=True)
        (ws.CONFIG_DIR / fname).write_text(_json.dumps(items[-100:], indent=1))
    except OSError:
        pass


def _abs_user_folder(folder: str) -> Path:
    import os
    raw = folder.strip().strip('"').strip("'")
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute():
        p = ws.root() / p
    if not p.is_dir():
        raise HTTPException(400, f"{p} isn't a folder")
    return p.resolve()


_dl_status_cache: dict = {}


def _dl_status(force: bool = False) -> dict:
    """Add-on status without importing PyTorch into this process (see lulc_fetch.dlrunner): the package check is
    done here, the device check once in a helper process."""
    import importlib.metadata as md
    import importlib.util

    if _dl_status_cache and not force:
        return dict(_dl_status_cache)
    pkgs, missing = {}, []
    for mod, dist in (("torch", "torch"), ("torchvision", "torchvision"), ("segmentation_models_pytorch", "segmentation-models-pytorch")):
        if importlib.util.find_spec(mod) is None:
            missing.append(dist)
        else:
            try:
                pkgs[dist] = md.version(dist)
            except md.PackageNotFoundError:
                pkgs[dist] = "?"
    st = {"available": False, "packages": pkgs, "devices": ["cpu"], "device": "cpu"}
    if missing:
        st["error"] = f"ModuleNotFoundError: No module named {missing[0]!r}"
    else:
        from lulc_fetch import dlrunner
        try:
            st = dlrunner.run("status")
        except Exception as e:
            st["error"] = str(e)
    _dl_status_cache.clear()
    _dl_status_cache.update(st)
    return dict(st)


@app.get("/api/dl/status")
def dl_status(refresh: bool = False):
    import sys

    st = _dl_status(refresh)
    frozen = bool(getattr(sys, "frozen", False))
    st.update({"platform": sys.platform, "frozen": frozen, "addon_dir": str(_addon_dir()) if frozen else None,
               "can_install": True, "variants": (["cpu", "cuda"] if sys.platform.startswith(("win", "linux")) else ["default"]),
               "size_mb": {"default": 850, "cpu": 1100, "cuda": 3500, "yolo": 150}, "yolo": _yolo_status()})
    return st


def _yolo_status() -> dict:
    """The YOLO & SAM add-on (ultralytics, AGPL-3.0): installed or not, without importing it."""
    import importlib.metadata as md
    import importlib.util

    if importlib.util.find_spec("ultralytics") is None:
        return {"available": False}
    try:
        return {"available": True, "version": md.version("ultralytics")}
    except md.PackageNotFoundError:
        return {"available": True, "version": "?"}


class DlInstallRequest(BaseModel):
    variant: str = Field("default", pattern="^(default|cpu|cuda|yolo)$")


@app.post("/api/dl/install")
def dl_install(req: DlInstallRequest):
    """Install PyTorch + segmentation-models-pytorch (a background job with pip's output in the log)."""
    import importlib
    import subprocess
    import sys

    from lulc_fetch import dl, progress
    frozen = bool(getattr(sys, "frozen", False))
    base = [sys.executable, "--lulc-pip"] if frozen else [sys.executable, "-m", "pip"]
    target = ["--target", str(_addon_dir()), "--upgrade"] if frozen else []
    steps = []
    if req.variant == "yolo":
        if not _dl_status()["available"]:
            raise HTTPException(400, "Install the deep-learning add-on (PyTorch) first")
        if frozen:   # --target ignores what is installed, so keep PyTorch out of the dependencies (it is already there)
            steps.append(["install", "--prefer-binary", *target, "--no-deps", "ultralytics", "ultralytics-thop"])
            steps.append(["install", "--prefer-binary", *target, "opencv-python-headless", "pyyaml", "psutil", "polars", "matplotlib",
                          "cloudpickle", "filelock"])
        else:
            steps.append(["install", "--prefer-binary", "ultralytics"])
    elif req.variant == "cuda":
        steps.append(["install", "--prefer-binary", *target, "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu128"])
        steps.append(["install", "--prefer-binary", *target, "segmentation-models-pytorch"])
    elif req.variant == "cpu" and not sys.platform == "darwin":
        steps.append(["install", "--prefer-binary", *target, "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu"])
        steps.append(["install", "--prefer-binary", *target, "segmentation-models-pytorch"])
    else:
        steps.append(["install", "--prefer-binary", *target, *dl.PACKAGES])

    def run(job):
        if frozen:
            _addon_dir().mkdir(parents=True, exist_ok=True)
        for k, args in enumerate(steps):
            cmd = base + args
            log.info("Running: pip %s", " ".join(args))
            flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=flags)
            try:
                for line in proc.stdout:
                    line = line.rstrip()
                    if not line or line.lstrip().startswith(("━", "|")):
                        continue
                    job.logs.append(line[:300])
                    if line.startswith(("Collecting", "Downloading", "Installing", "Successfully", "Requirement already")):
                        progress.update((k + 0.5) / len(steps), line[:140])
            except progress.Cancelled:
                proc.kill()
                raise
            if proc.wait() != 0:
                raise RuntimeError("pip couldn't install the add-on; see the log above (internet connection?)")
        if frozen and str(_addon_dir()) not in sys.path:
            sys.path.append(str(_addon_dir()))
        importlib.invalidate_caches()
        if req.variant == "yolo":
            ys = _yolo_status()
            if not ys["available"]:
                raise RuntimeError("Installed, but ultralytics can't be found")
            log.info("YOLO & SAM add-on ready: ultralytics %s", ys["version"])
            return {"status": ys, "outputs": []}
        st = _dl_status(force=True)
        if not st["available"]:
            raise RuntimeError(f"Installed, but PyTorch can't be loaded: {st.get('error')}")
        log.info("Deep-learning add-on ready: %s", ", ".join(f"{k} {v}" for k, v in st["packages"].items()))
        return {"status": st, "outputs": []}

    title = "Install the YOLO & SAM add-on" if req.variant == "yolo" else "Install the deep-learning add-on"
    return jobs.submit("dlinstall", title, {"variant": req.variant}, run).to_dict()


@app.get("/api/dl/schema")
def dl_schema():
    from lulc_fetch import dl
    return dl.schema()


@app.get("/api/dl/datasets")
def dl_datasets():
    """Training datasets: the project's training_data/ folder plus folders used before."""
    from lulc_fetch import dl
    seen, out = set(), []
    cands = []
    if PATCH_DIR.path.is_dir():
        cands += [p for p in sorted(PATCH_DIR.path.iterdir()) if (p / "dataset.json").is_file()]
    cands += [Path(x) for x in reversed(_remembered("dl_datasets.json"))]
    for p in cands:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen or not (p / "dataset.json").is_file():
            continue
        seen.add(key)
        try:
            info = dl.inspect_dataset(p)
            out.append({k: info[k] for k in ("folder", "name", "patches", "labelled", "patch_size_px", "band_count", "classes", "created")})
        except (ValueError, OSError):
            continue
    return out


class DlFolderRequest(BaseModel):
    folder: str = Field(max_length=2000)


@app.post("/api/dl/dataset")
def dl_dataset(req: DlFolderRequest):
    from lulc_fetch import dl
    p = _abs_user_folder(req.folder)
    try:
        info = dl.inspect_dataset(p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _remember_dl("dl_datasets.json", str(p))
    return info


def _dl_model_dirs() -> list[Path]:
    cands = []
    if DL_MODEL_DIR.path.is_dir():
        cands += [p for p in DL_MODEL_DIR.path.iterdir() if (p / "model_config.json").is_file()]
    cands += [Path(x) for x in _remembered("dl_models.json")]
    seen, out = set(), []
    for p in cands:
        if (p / "model_config.json").is_file() and (p / "best_model.pt").is_file():
            k = str(p.resolve())
            if k not in seen:
                seen.add(k)
                out.append(p.resolve())
    return out


def _dl_model(folder: str) -> Path:
    p = Path(folder)
    p = (ws.root() / p).resolve() if not p.is_absolute() else p.resolve()
    if p not in _dl_model_dirs():
        raise HTTPException(404, "No such deep-learning model (add it with Browse… first)")
    return p


@app.get("/api/dl/models")
def dl_models():
    import json as _json
    out = []
    for p in _dl_model_dirs():
        try:
            c = _json.loads((p / "model_config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ev = c.get("test") or c.get("val") or {}
        out.append({"folder": str(p), "name": c.get("name") or p.name, "arch": c.get("arch_title"), "encoder": c.get("encoder_title"),
                    "bands": c.get("bands"), "in_channels": c.get("in_channels"), "classes": c.get("classes"),
                    "patch_size_px": c.get("patch_size_px"), "pixel_size": c.get("pixel_size"), "trained": c.get("trained"),
                    "miou": ev.get("miou"), "accuracy": ev.get("accuracy"), "epochs_run": c.get("epochs_run"),
                    "best_epoch": c.get("best_epoch"), "has_report": (p / "report.html").is_file(), "in_project": p.is_relative_to(ws.root())})
    out.sort(key=lambda m: m.get("trained") or "", reverse=True)
    return out


@app.post("/api/dl/models/add")
def dl_models_add(req: DlFolderRequest):
    p = _abs_user_folder(req.folder)
    if not ((p / "model_config.json").is_file() and (p / "best_model.pt").is_file()):
        raise HTTPException(400, f"{p} isn't a LULC Fetch deep-learning model folder (needs model_config.json and best_model.pt)")
    _remember_dl("dl_models.json", str(p))
    return {"folder": str(p)}


@app.delete("/api/dl/models")
def dl_models_forget(folder: str):
    p = _dl_model(folder)
    _remember_dl("dl_models.json", str(p), remove=True)
    return {"ok": True, "note": "Removed from the list; the folder was not deleted."}


@app.get("/api/dl/report")
def dl_report(folder: str, download: bool = False):
    p = _dl_model(folder) / "report.html"
    if not p.is_file():
        raise HTTPException(404, "This model has no report")
    return FileResponse(p, media_type="text/html", filename=f"{p.parent.name}_report.html" if download else None,
                        content_disposition_type="attachment" if download else "inline")


class DlTrainRequest(BaseModel):
    dataset: str = Field(max_length=2000)
    arch: str = "unet"
    encoder: str = "tu-mobilenetv3_large_100"
    pretrained: bool = True
    params: dict = Field(default_factory=dict)
    name: str = Field("dl_model", max_length=80)
    folder: str | None = Field(None, max_length=1000)     # where the model folder is created (default: the project's models/)
    resume: str | None = Field(None, max_length=2000)     # a model folder to continue training (its last_model.pt)


@app.post("/api/dl/train")
def dl_train(req: DlTrainRequest):
    import re

    from lulc_fetch import dl, dlrunner
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    if req.arch not in dl.ARCHS:
        raise HTTPException(400, f"Unknown architecture {req.arch}")
    if dl.ARCHS[req.arch]["lib"] == "yolo":
        _need_yolo()
    ds = _abs_user_folder(req.dataset)
    try:
        dl.inspect_dataset(ds)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if req.resume:
        out = _dl_model(req.resume)
        if not (out / "last_model.pt").is_file():
            raise HTTPException(400, "This model has no last_model.pt to continue from")
        resume = out / "last_model.pt"
    else:
        parent = _report_folder(req.folder) if req.folder and req.folder.strip() else DL_MODEL_DIR.path
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "dl_model"
        out, i = parent / stem, 2
        while out.exists() and any(out.iterdir()):
            out = parent / f"{stem}_{i}"
            i += 1
        resume = None
    _remember_dl("dl_datasets.json", str(ds))

    def run(job):
        res = dlrunner.run("train", dataset=str(ds), out_dir=str(out), arch=req.arch, encoder=req.encoder, pretrained=req.pretrained,
                           params=req.params, name=req.name, resume=str(resume) if resume else None)
        _remember_dl("dl_models.json", str(out))
        res["outputs"] = []
        res.pop("history", None)
        return res

    title = f"Deep learning · {dl.ARCHS[req.arch]['title']} · {req.name}"
    return jobs.submit("dltrain", title, {"dataset": ds.name}, run).to_dict()


class DlPredictRequest(BaseModel):
    model: str = Field(max_length=2000)
    inputs: list[PatchInput] = Field(..., min_length=1, max_length=20)
    clip: dict | None = None
    overlap: float = Field(0.25, ge=0, le=0.75)
    batch_size: int = Field(8, ge=1, le=256)
    device: str = Field("auto", pattern="^(auto|cpu|cuda|mps)$")
    confidence: bool = True
    name: str = Field("dl_map", max_length=80)


@app.post("/api/dl/predict")
def dl_predict(req: DlPredictRequest):
    import re

    from lulc_fetch import dlrunner
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    model = _dl_model(req.model)
    from lulc_fetch import dl
    if dl.ARCHS.get(dl.load_config(model).get("arch"), {}).get("lib") == "yolo":
        _need_yolo()
    inputs = [{**i.model_dump(), "path": str(_raster_path(i.path))} for i in req.inputs]
    clip = _clip(req.clip) if req.clip else None
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "dl_map"

    def run(job):
        res = dlrunner.run("predict", model_dir=str(model), inputs=inputs, out_path=str(job.dir / f"{stem}.tif"), clip=clip,
                           overlap=req.overlap, batch_size=req.batch_size, device=req.device, confidence=req.confidence)
        res["path"] = ws.rel(res["path"])
        res["outputs"] = [res["path"]]
        return res

    return jobs.submit("dlpredict", f"Deep learning map · {req.name}", {"model": model.name}, run).to_dict()


@app.get("/api/detect/schema")
def detect_schema():
    from lulc_fetch import detect
    return detect.schema()


class DetectRequest(BaseModel):
    input: PatchInput
    model: str = "fasterrcnn_v2"
    size: str | None = Field(None, pattern="^[nsmlxtb]$")         # YOLO n/s/m/l/x, SAM t/s/b/l
    custom: str | None = Field(None, max_length=2000)             # a model folder from Train detection model (model = "custom")
    sam_refine: str | None = Field(None, pattern="^[tsbl]$")      # turn boxes into outlines with SAM 2.1 of this size
    clip: dict | None = None
    classes: list[str] | None = Field(None, max_length=200)
    score: float = Field(0.4, ge=0.01, le=1)
    zoom: float | str = 1.0                                       # or "auto" for your own models
    overlap: float = Field(0.2, ge=0, le=0.5)
    nms_iou: float = Field(0.5, ge=0.05, le=0.95)
    stretch: str = Field("percent", pattern="^(percent|minmax|byte)$")
    batch_size: int = Field(2, ge=1, le=64)
    device: str = Field("auto", pattern="^(auto|cpu|cuda|mps)$")
    max_size_m: float | None = Field(None, gt=0, le=100000)
    name: str = Field("objects", max_length=80)


def _need_yolo():
    if not _yolo_status()["available"]:
        raise HTTPException(400, "This needs the YOLO & SAM add-on: install it from the tool's panel")


@app.post("/api/detect/run")
def detect_run(req: DetectRequest):
    import json as _json
    import re

    from lulc_fetch import detect, dlrunner
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    custom = None
    if req.model == "custom":
        if not req.custom:
            raise HTTPException(400, "Choose one of your detection models")
        custom = _det_model(req.custom)
        title = custom.name
        _need_yolo()
    elif req.model not in detect.MODELS:
        raise HTTPException(400, f"Unknown model {req.model}")
    else:
        title = detect.MODELS[req.model]["title"]
        if detect.MODELS[req.model]["family"] != "tv":
            _need_yolo()
    if req.sam_refine:
        _need_yolo()
    if isinstance(req.zoom, str):
        if req.zoom != "auto":
            raise HTTPException(400, "Zoom must be a number or auto")
    elif not 0.25 <= req.zoom <= 8:
        raise HTTPException(400, "Zoom must be between 0.25 and 8")
    if req.input.bands is not None and len(req.input.bands) not in (1, 3):
        raise HTTPException(400, "Choose 3 bands (red, green, blue) or 1 band")
    inp = {**req.input.model_dump(), "path": str(_raster_path(req.input.path))}
    clip = _clip(req.clip) if req.clip else None
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "objects"

    def run(job):
        res = dlrunner.run("detect", inputs=[inp], out_path=str(job.dir / f"{stem}.geojson"), model=req.model, size=req.size,
                           custom_dir=str(custom) if custom else None, sam_refine=req.sam_refine, clip=clip, classes=req.classes or None,
                           score=req.score, zoom=req.zoom, overlap=req.overlap, nms_iou=req.nms_iou, stretch=req.stretch,
                           batch_size=req.batch_size, device=req.device, max_size_m=req.max_size_m)
        res["geojson"] = _json.loads(Path(res["path"]).read_text(encoding="utf-8"))
        res["path"] = ws.rel(res["path"])
        res["outputs"] = [res["path"]]
        return res

    return jobs.submit("detect", f"Object detection · {req.name}", {"model": title}, run).to_dict()


# ---------------- detection models (Train detection model)

def _det_model_dirs() -> list[Path]:
    import json as _json
    cands = []
    if DL_MODEL_DIR.path.is_dir():
        cands += [p for p in DL_MODEL_DIR.path.iterdir() if (p / "model_config.json").is_file()]
    cands += [Path(x) for x in _remembered("det_models.json")]
    seen, out = set(), []
    for p in cands:
        if (p / "model_config.json").is_file() and (p / "best.pt").is_file():
            try:
                if _json.loads((p / "model_config.json").read_text(encoding="utf-8")).get("kind") != "detection":
                    continue
            except (OSError, ValueError):
                continue
            k = str(p.resolve())
            if k not in seen:
                seen.add(k)
                out.append(p.resolve())
    return out


def _det_model(folder: str) -> Path:
    p = Path(folder)
    p = (ws.root() / p).resolve() if not p.is_absolute() else p.resolve()
    if p not in _det_model_dirs():
        raise HTTPException(404, "No such detection model (add it with Browse… first)")
    return p


@app.get("/api/det/schema")
def det_schema():
    from lulc_fetch import dettrain
    return dettrain.schema()


@app.get("/api/det/models")
def det_models():
    import json as _json
    out = []
    for p in _det_model_dirs():
        try:
            c = _json.loads((p / "model_config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        v = c.get("val") or {}
        out.append({"folder": str(p), "name": c.get("name") or p.name, "arch": c.get("arch_title"), "task": c.get("task"),
                    "classes": c.get("classes"), "bands": c.get("bands"), "stretch": c.get("stretch"), "tile_px": c.get("tile_px"),
                    "pixel_size": c.get("pixel_size"), "image_pixel_size": c.get("image_pixel_size"), "zoom": c.get("zoom"),
                    "trained": c.get("trained"), "map50": v.get("map50"), "map": v.get("map"), "epochs_run": c.get("epochs_run"),
                    "has_report": (p / "report.html").is_file(), "in_project": p.is_relative_to(ws.root())})
    out.sort(key=lambda m: m.get("trained") or "", reverse=True)
    return out


@app.post("/api/det/models/add")
def det_models_add(req: DlFolderRequest):
    import json as _json
    p = _abs_user_folder(req.folder)
    try:
        ok = (p / "best.pt").is_file() and _json.loads((p / "model_config.json").read_text(encoding="utf-8")).get("kind") == "detection"
    except (OSError, ValueError):
        ok = False
    if not ok:
        raise HTTPException(400, f"{p} isn't a LULC Fetch detection model folder (needs model_config.json and best.pt)")
    _remember_dl("det_models.json", str(p))
    return {"folder": str(p)}


@app.delete("/api/det/models")
def det_models_forget(folder: str):
    p = _det_model(folder)
    _remember_dl("det_models.json", str(p), remove=True)
    return {"ok": True, "note": "Removed from the list; the folder was not deleted."}


@app.get("/api/det/report")
def det_report(folder: str, download: bool = False):
    p = _det_model(folder) / "report.html"
    if not p.is_file():
        raise HTTPException(404, "This model has no report")
    return FileResponse(p, media_type="text/html", filename=f"{p.parent.name}_report.html" if download else None,
                        content_disposition_type="attachment" if download else "inline")


class DetTrainRequest(BaseModel):
    input: PatchInput
    ground_truth: dict                                            # {geojson (EPSG:4326), field, point_size_m}
    task: str = Field("detect", pattern="^(detect|segment|obb)$")
    family: str = Field("yolo26", pattern="^(yolo26|yolo11)$")
    size: str = Field("s", pattern="^[nsmlx]$")
    pretrained: bool = True
    tile_px: int = Field(640, ge=128, le=2048)
    zoom: float = Field(1.0, ge=0.25, le=8)
    overlap: float = Field(0.2, ge=0, le=0.5)
    clip: dict | None = None
    stretch: str = Field("percent", pattern="^(percent|minmax|byte)$")
    params: dict = Field(default_factory=dict)
    class_colors: dict[str, str] | None = None
    name: str = Field("detector", max_length=80)
    folder: str | None = Field(None, max_length=1000)


@app.post("/api/det/train")
def det_train(req: DetTrainRequest):
    import re

    from lulc_fetch import dettrain, dlrunner
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    _need_yolo()
    if req.input.bands is not None and len(req.input.bands) not in (1, 3):
        raise HTTPException(400, "Choose 3 bands (red, green, blue) or 1 band")
    gt = req.ground_truth
    if not isinstance(gt.get("geojson"), dict) or not gt["geojson"].get("features"):
        raise HTTPException(400, "Choose a ground-truth layer with labelled shapes")
    inp = {**req.input.model_dump(), "path": str(_raster_path(req.input.path))}
    clip = _clip(req.clip) if req.clip else None
    parent = _report_folder(req.folder) if req.folder and req.folder.strip() else DL_MODEL_DIR.path
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "detector"
    out = parent / stem
    k = 2
    while out.exists():
        out = parent / f"{stem}_{k}"
        k += 1

    def run(job):
        res = dlrunner.run("train_detector", inputs=[inp], ground_truth={"geojson": gt["geojson"], "field": gt.get("field"),
                           "point_size_m": gt.get("point_size_m")}, out_dir=str(out), name=req.name, task=req.task, family=req.family,
                           size=req.size, pretrained=req.pretrained, tile_px=req.tile_px, zoom=req.zoom, overlap=req.overlap, clip=clip,
                           stretch=req.stretch, params=req.params, class_colors=req.class_colors)
        _remember_dl("det_models.json", str(out))
        res["outputs"] = []
        return res

    title = f"Train detection model · {dettrain.FAMILIES[req.family].split(' ')[0]} · {req.name}"
    return jobs.submit("dettrain", title, {"image": Path(inp["path"]).name}, run).to_dict()


# ------------------------------------------------------------------ History (every tool run, kept in logs/history.jsonl)

def _running_rows() -> list[dict]:
    return [{"id": j.id, "kind": j.kind, "title": j.title, "status": j.status, "started": j.started or j.created, "finished": None,
             "seconds": round(time.time() - (j.started or j.created), 1), "project": (ws.project() or {}).get("name"), "error": None}
            for j in sorted(jobs.list(), key=lambda j: -j.created) if j.status in ("queued", "running")]


@app.get("/api/history")
def history_list(q: str = "", status: str = "", limit: int = 200):
    from . import history
    return history.listing(_running_rows(), q=q[:200], status=status, limit=max(1, min(limit, 2000)))


@app.get("/api/history/{job_id}")
def history_get(job_id: str):
    from . import history
    e = history.get(job_id)
    if e is None:
        j = next((j for j in jobs.list() if j.id == job_id), None)
        if j is None:
            raise HTTPException(404, "Not in the history")
        req = j.request or {}
        e = {"id": j.id, "kind": j.kind, "title": j.title, "status": j.status, "started": j.started or j.created, "finished": None,
             "seconds": round(time.time() - (j.started or j.created), 1), "endpoint": req.get("endpoint"), "params": history.safe(j.params),
             "settings": history.safe(req.get("body") or {}), "inputs": {}, "outputs": [], "summary": {}, "error": None, "log": j.logs[-40:],
             "project": (ws.project() or {}).get("name"), "workspace": str(ws.root())}
    return e


@app.get("/api/history/{job_id}/request")
def history_request(job_id: str):
    """The exact settings a run was started with, to run it again (as they were, or changed)."""
    from . import history
    r = history.get_request(job_id)
    if r is None:
        raise HTTPException(404, "This run's settings weren't kept, so it can't be repeated")
    return r


@app.delete("/api/history")
def history_clear():
    from . import history
    history.clear()
    return {"ok": True}


class HistoryCopy(BaseModel):
    folder: str = Field(max_length=2000)
    files: list[str] = Field(default_factory=list, max_length=500)


@app.post("/api/history/{job_id}/copy")
def history_copy(job_id: str, req: HistoryCopy):
    from . import history
    history.record_copy(job_id, req.folder, req.files)
    return {"ok": True}


# ------------------------------------------------------------------ error log (every failed tool run)

@app.get("/api/errors")
def errors_info():
    from .jobs import errors_log
    f = errors_log()
    text = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
    return {"path": str(f), "exists": f.is_file(), "entries": text.count("\n==== ") + text.startswith("==== "),
            "size_kb": round(len(text.encode()) / 1000, 1)}


class ErrorReport(BaseModel):
    title: str = Field("", max_length=300)
    error: str = Field("", max_length=4000)
    tool: str = Field("", max_length=60)


@app.post("/api/errors/report")
def errors_report(req: ErrorReport):
    """A failure seen only in the browser (a request that failed outside a background job): recorded like a job's."""
    from .jobs import Job, record_error
    job = Job("browser", req.tool or "tool", req.title or "A request", {"tool": req.tool})
    job.error = req.error or "Unknown error"
    record_error(job, RuntimeError(job.error))
    return {"ok": True}


@app.get("/api/errors/file")
def errors_file():
    """The error log as plain text (opens in a browser tab)."""
    from fastapi.responses import PlainTextResponse

    from .jobs import errors_log
    f = errors_log()
    body = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else "No errors recorded yet.\n"
    return PlainTextResponse(f"LULC Fetch error log · {f}\n\n{body}", headers={"Cache-Control": "no-store"})


@app.post("/api/errors/reveal")
def errors_reveal():
    import subprocess
    import sys

    from .jobs import errors_log
    f = errors_log()
    f.parent.mkdir(parents=True, exist_ok=True)
    target = f if f.is_file() else f.parent
    cmd = (["open", "-R", str(target)] if sys.platform == "darwin" else ["explorer", f"/select,{target}"] if sys.platform.startswith("win")
           else ["xdg-open", str(target.parent)])
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise HTTPException(500, f"Couldn't open the folder: {e}")
    return {"ok": True, "path": str(f)}


# ------------------------------------------------------------------ Satellite embeddings (AlphaEarth, TESSERA)

EMB_CACHE = ws.APP_DIR / "embeddings_cache"   # the AlphaEarth file index (shared by all projects)


@app.get("/api/emb/sources")
def emb_sources():
    from lulc_fetch import embeddings as em
    return {"sources": em.SOURCES, "other": em.OTHER, "years": em.YEARS, "formats": em.FORMATS}


class EmbArea(BaseModel):
    clip: dict
    source: str = Field("aef", pattern="^(aef|tessera)$")
    res: float = Field(10, ge=10, le=160)


def _emb_clip(clip: dict) -> dict:
    from shapely.geometry import shape
    g = _clip(clip)
    if not g:
        raise HTTPException(400, "Choose an area")
    if shape(g).area > 4:   # degrees²: about 200 × 200 km near the equator
        raise HTTPException(400, "The area is too large: choose an area up to about 200 × 200 km")
    return g


@app.post("/api/emb/estimate")
def emb_estimate(req: EmbArea):
    from lulc_fetch import embeddings as em
    return em.estimate(_emb_clip(req.clip), req.source, req.res)


class EmbAvailRequest(BaseModel):
    clip: dict


@app.post("/api/emb/available")
def emb_available(req: EmbAvailRequest):
    from lulc_fetch import embeddings as em
    g = _emb_clip(req.clip)
    return jobs.submit("embcheck", "Satellite embeddings · what's available here", {}, lambda job: em.available(g, EMB_CACHE)).to_dict()


class EmbFetchRequest(EmbArea):
    year: int = Field(2024, ge=2017, le=2030)
    name: str = Field("embedding", max_length=80)
    colour: bool = True


@app.post("/api/emb/fetch")
def emb_fetch(req: EmbFetchRequest):
    import re

    from lulc_fetch import embeddings as em
    g = _emb_clip(req.clip)
    if req.res not in em.SOURCES[req.source]["resolutions"]:
        raise HTTPException(400, f"{em.SOURCES[req.source]['short']} is available at {', '.join(map(str, em.SOURCES[req.source]['resolutions']))} m")
    est = em.estimate(g, req.source, req.res)
    if est["width"] * est["height"] > 25_000_000:
        raise HTTPException(400, f"The area is too large at {req.res:g} m ({est['width']} × {est['height']} pixels): choose a smaller area"
                                 + (" or a coarser resolution" if req.source == "aef" else ""))
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "embedding"

    def run(job):
        res = em.fetch(g, req.source, req.year, str(job.dir / f"{stem}.tif"), EMB_CACHE, req.res)
        outs = [res["path"]]
        if req.colour:
            log.info("Colour view: the three main directions of variation (PCA) as red, green and blue")
            res["colour"] = em.colour_view(res["path"], str(job.dir / f"{stem}_colour.tif"))
            res["colour"]["path"] = ws.rel(res["colour"]["path"])
            outs.append(res["colour"]["path"])
        res["path"] = ws.rel(res["path"])
        res["outputs"] = [res["path"]] + outs[1:]
        return res

    title = f"Satellite embeddings · {em.SOURCES[req.source]['short']} {req.year} · {req.name}"
    return jobs.submit("embfetch", title, {"source": req.source, "year": req.year}, run).to_dict()


class EmbLayerRequest(BaseModel):
    path: str
    points: list[list[float]] | None = Field(None, max_length=500)   # [lon, lat] (similar places)
    name: str = Field("similarity", max_length=80)


@app.post("/api/emb/similar")
def emb_similar(req: EmbLayerRequest):
    import re

    from lulc_fetch import embeddings as em
    src = _raster_path(req.path)
    if not req.points:
        raise HTTPException(400, "Click at least one place on the map")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "similarity"

    def run(job):
        r = em.similarity(str(src), req.points, str(job.dir / f"{stem}.tif"))
        r["path"] = ws.rel(r["path"])
        r["outputs"] = [r["path"]]
        return r

    return jobs.submit("embsimilar", f"Similar places · {req.name}", {}, run).to_dict()


@app.get("/api/emb/format")
def emb_format(path: str):
    from lulc_fetch import embeddings as em
    return em.detect(_raster_path(path))


class EmbConvertRequest(BaseModel):
    path: str
    to: str = Field(pattern="^(float32|float16|int8-aef|int8-scaled)$")
    normalise: bool = False
    name: str = Field("embedding", max_length=80)


@app.post("/api/emb/convert")
def emb_convert(req: EmbConvertRequest):
    import re

    from lulc_fetch import embeddings as em
    src = _raster_path(req.path)
    info = em.detect(src)
    if info["format"] is None:
        raise HTTPException(400, info["error"])
    if info["format"] == req.to and not req.normalise:
        raise HTTPException(400, f"The layer is already {em.FORMATS[req.to]['title']}")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "embedding"

    def run(job):
        r = em.convert(str(src), str(job.dir / f"{stem}.tif"), req.to, req.normalise)
        r["path"] = ws.rel(r["path"])
        r["outputs"] = [r["path"]]
        return r

    return jobs.submit("embconvert", f"Convert embeddings · {em.FORMATS[req.to]['title']} · {req.name}", {"to": req.to}, run).to_dict()


@app.post("/api/emb/colour")
def emb_colour(req: EmbLayerRequest):
    import re

    from lulc_fetch import embeddings as em
    src = _raster_path(req.path)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", src.stem).strip("_")[:60] or "embedding"

    def run(job):
        r = em.colour_view(str(src), str(job.dir / f"{stem}_colour.tif"))
        r["path"] = ws.rel(r["path"])
        r["outputs"] = [r["path"]]
        return r

    return jobs.submit("embcolour", f"Colour view · {src.stem}", {}, run).to_dict()


# ------------------------------------------------------------------ Agri: crop disease diagnosis and the crop disease guide

# where a copy of the disease repository (with its model weights) is often kept, tried when no models folder is chosen yet
_AGRI_GUESSES = ("Desktop/Farmer_ai", "Farmer_ai", "Desktop/multicrop-disease-decision-support", "multicrop-disease-decision-support",
                 "Documents/Farmer_ai", "Documents/multicrop-disease-decision-support")


def _agri_settings() -> dict:
    import json as _json
    try:
        return _json.loads((ws.CONFIG_DIR / "agri.json").read_text())
    except (OSError, ValueError):
        return {}


AGRI_HUB_DIR = ws.APP_DIR / "agri_models"   # models downloaded from Hugging Face (shared by all projects)


def _agri_models() -> dict:
    """Where the models come from: a chosen local folder, one found in a usual place, or else Hugging Face (downloaded into
    AGRI_HUB_DIR when first needed)."""
    from lulc_fetch.agri.disease import find_models, hub_models

    st = _agri_settings()
    saved = st.get("models")
    if st.get("source") != "hub":
        if saved and Path(saved).is_dir():
            return {**find_models(saved), "chosen": True, "source": "folder"}
        if not saved:
            for g in _AGRI_GUESSES:
                f = find_models(Path.home() / g)
                if f["crops"] or f["detectors"]:
                    return {**f, "chosen": False, "source": "folder"}
    return {**hub_models(AGRI_HUB_DIR), "chosen": st.get("source") == "hub", "source": "hub"}


def _agri_status() -> dict:
    from lulc_fetch.agri.disease import HUB_REPO, find_models
    m = _agri_models()
    out = {"source": m["source"], "folder": m["folder"], "chosen": m["chosen"], "crops": sorted(m["crops"]), "detectors": sorted(m["detectors"])}
    if m["source"] == "hub":
        have = find_models(AGRI_HUB_DIR)
        out.update(repo=HUB_REPO, url=f"https://huggingface.co/{HUB_REPO}", downloaded=sorted(have["crops"]) + [f"detector:{k}" for k in have["detectors"]],
                   downloaded_mb=round(sum(Path(p).stat().st_size for p in [*have["crops"].values(), *have["detectors"].values()]) / 1e6))
    return out


@app.get("/api/agri/schema")
def agri_schema():
    from lulc_fetch.agri import knowledge
    return {**knowledge.schema(), "models": _agri_status()}


class AgriModelsRequest(BaseModel):
    folder: str | None = Field(None, max_length=2000)   # a local models folder…
    source: str | None = Field(None, pattern="^hub$")    # …or "hub": download from Hugging Face


@app.post("/api/agri/models")
def agri_set_models(req: AgriModelsRequest):
    import json as _json

    from lulc_fetch.agri.disease import find_models
    if req.source == "hub":
        settings = {**_agri_settings(), "source": "hub"}
    elif req.folder:
        folder = _abs_user_folder(req.folder)
        f = find_models(folder)
        if not f["crops"] and not f["detectors"]:
            raise HTTPException(400, f"No disease models in {folder}: choose the disease app's folder (with data/<Crop>/convnext_best.pth "
                                     "and master_model/)")
        settings = {**_agri_settings(), "models": str(folder), "source": "folder"}
    else:
        raise HTTPException(400, "Give a models folder, or source = hub")
    try:
        ws.CONFIG_DIR.mkdir(exist_ok=True)
        (ws.CONFIG_DIR / "agri.json").write_text(_json.dumps(settings, indent=1))
    except OSError as e:
        raise HTTPException(500, f"Couldn't save the setting: {e}")
    return _agri_status()


def _agri_photo(path: str) -> Path:
    from lulc_fetch.agri.disease import PHOTO_EXTS
    p = Path(path)
    p = (p if p.is_absolute() else ws.root() / p).resolve()
    if p.suffix.lower() not in PHOTO_EXTS or not p.is_file():
        raise HTTPException(404, f"No such photo: {path}")
    return p


@app.post("/api/agri/photos/upload")
async def agri_upload_photos(files: list[UploadFile] = File(...)):
    """Photos added from the computer: kept in the workspace's uploads/photos/<batch>/."""
    import shutil
    import uuid

    from lulc_fetch.agri.disease import PHOTO_EXTS
    dest = ws.root() / "uploads" / "photos" / uuid.uuid4().hex[:8]
    out, skipped = [], []
    for f in files:
        name = Path(f.filename or "").name
        if Path(name).suffix.lower() not in PHOTO_EXTS:
            skipped.append(name)
            continue
        dest.mkdir(parents=True, exist_ok=True)
        p = _unique(dest, name)
        with open(p, "wb") as fh:
            shutil.copyfileobj(f.file, fh, length=8 << 20)
        out.append({"path": str(p), "name": p.name})
    return {"photos": out, "skipped": skipped}


@app.get("/api/agri/photos/folder")
def agri_folder_photos(path: str, recursive: bool = False):
    """The photos in a folder (and its sub-folders when recursive), up to 5,000."""
    from lulc_fetch.agri.disease import PHOTO_EXTS
    folder = _abs_user_folder(path)
    it = folder.rglob("*") if recursive else folder.iterdir()
    out = []
    for p in it:
        if p.suffix.lower() in PHOTO_EXTS and p.is_file() and not p.name.startswith("."):
            out.append({"path": str(p), "name": str(p.relative_to(folder))})
            if len(out) >= 5000:
                break
    out.sort(key=lambda x: x["name"].lower())
    return {"folder": str(folder), "photos": out, "truncated": len(out) >= 5000}


@app.get("/api/agri/photo")
def agri_photo(path: str, size: int = 256):
    """A photo as a JPEG, upright and at most size pixels (thumbnails in the tool, the full photo with size=0)."""
    import io

    from fastapi.responses import Response

    from lulc_fetch.agri.disease import open_photo
    p = _agri_photo(path)
    try:
        img, _ = open_photo(p)
    except Exception as e:
        raise HTTPException(400, f"Couldn't read {p.name}: {e}")
    if size > 0:
        img.thumbnail((min(size, 2048), min(size, 2048)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


class DiagnoseRequest(BaseModel):
    photos: list[str] = Field(min_length=1, max_length=5000)
    crop: str = Field("auto", max_length=40)
    strict: bool = True                        # refuse unclear photos (the disease app's photo check)
    device: str = Field("auto", pattern="^(auto|cpu|cuda|mps)$")
    name: str = Field("diagnosis", max_length=80)


@app.post("/api/agri/diagnose")
def agri_diagnose(req: DiagnoseRequest):
    import json as _json
    import re
    import shutil

    from lulc_fetch import dlrunner
    from lulc_fetch.agri import knowledge
    if not _dl_status()["available"]:
        raise HTTPException(400, "The deep-learning add-on isn't installed yet")
    m = _agri_models()
    if not (m["crops"] or m["detectors"]):
        raise HTTPException(400, "Choose the disease models folder first")
    if req.crop != "auto":
        if req.crop not in knowledge.crops():
            raise HTTPException(400, f"Unknown crop {req.crop}")
        if req.crop not in m["crops"]:
            raise HTTPException(400, f"The models folder has no {knowledge.crops()[req.crop]['name']} model")
    elif not m["detectors"]:
        raise HTTPException(400, "The models folder has no crop detector (master_model/): choose the crop")
    from lulc_fetch.agri.disease import PHOTO_EXTS
    photos = [str(Path(p) if Path(p).is_absolute() else ws.root() / p) for p in req.photos]
    if any(Path(p).suffix.lower() not in PHOTO_EXTS for p in photos):
        raise HTTPException(400, "Only photos (JPG, PNG, WebP, BMP, TIFF, HEIC) can be diagnosed")
    if not any(Path(p).is_file() for p in photos):   # a missing photo among others is reported in its row
        raise HTTPException(404, "None of the photos exist any more: add them again")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:60] or "diagnosis"

    def run(job):
        res = dlrunner.run("diagnose", photos=photos, models_dir=m["folder"], out_dir=str(job.dir), name=stem, crop=req.crop,
                           strict=req.strict, device=req.device, hub=m["source"] == "hub")
        res["geojson"] = _json.loads(Path(res["geojson_path"]).read_text(encoding="utf-8")) if res["geojson_path"] else None
        TABLE_DIR.mkdir(exist_ok=True)   # the data viewer opens tables from tables/ (never overwrite an earlier one)
        table = _unique(TABLE_DIR.path, Path(res["csv"]).name)
        shutil.copyfile(res["csv"], table)
        res["outputs"] = [ws.rel(res["csv"])] + ([ws.rel(res["geojson_path"])] if res["geojson_path"] else [])
        res["csv"] = ws.rel(table)
        res["geojson_path"] = ws.rel(res["geojson_path"]) if res["geojson_path"] else None
        return res

    crop = "crop detected" if req.crop == "auto" else knowledge.crops()[req.crop]["name"]
    return jobs.submit("diagnose", f"Crop disease · {len(photos)} photo{'s' if len(photos) > 1 else ''} · {req.name}",
                       {"crop": crop}, run).to_dict()


@app.get("/api/agri/guide/diseases")
def agri_guide_diseases(crop: str):
    from lulc_fetch.agri import knowledge
    if crop not in knowledge.crops():
        raise HTTPException(404, f"Unknown crop {crop}")
    return {"crop": crop, "diseases": knowledge.diseases(crop)}


@app.get("/api/agri/guide/search")
def agri_guide_search(crop: str, disease: str | None = None, q: str | None = None, section: str | None = None, limit: int = 200):
    from lulc_fetch.agri import knowledge
    if crop not in knowledge.crops():
        raise HTTPException(404, f"Unknown crop {crop}")
    return knowledge.search(crop, disease=disease or None, q=(q or "")[:200], section=section or None, limit=max(1, min(limit, 1000)))


# ------------------------------------------------------------------ Classical ML: unsupervised (clustering, t-SNE)

@app.get("/api/unsup/schema")
def unsup_schema():
    from lulc_fetch import unsupervised

    return unsupervised.schema()


class ClusterRequest(BaseModel):
    table: str
    features: list[str]
    categorical: list[str] = Field(default_factory=list)
    method: str = "kmeans"
    params: dict = Field(default_factory=dict)
    prep: dict = Field(default_factory=dict)
    options: dict = Field(default_factory=dict)
    compare: str | None = None
    name: str = Field("clusters", max_length=80)


def _rel(path: str | None) -> str | None:
    return ws.rel(path) if path else None


@app.post("/api/unsup/cluster")
def unsup_cluster(req: ClusterRequest):
    from lulc_fetch import unsupervised

    table = _table_path(req.table)
    if req.method not in unsupervised.METHODS:
        raise HTTPException(400, f"Unknown method {req.method}")

    def run(job):
        rep = unsupervised.cluster(table, features=req.features, categorical=req.categorical, method=req.method, params=req.params,
                                   prep=req.prep, options=req.options, compare=req.compare or None, name=req.name,
                                   table_dir=TABLE_DIR, model_dir=MODEL_DIR)
        rep["output_table"], rep["path"], rep["table"] = _rel(rep["output_table"]), _rel(rep.get("path")), _rel(rep["table"])
        if rep["path"]:
            import json as _json
            Path(str(ws.root() / rep["path"]) + ".json").write_text(_json.dumps(rep, indent=1, default=str))
        return rep

    title = f"{unsupervised.METHODS[req.method]['title']} clustering · {table.name}"
    return jobs.submit("cluster", title, {"source": "Classical ML"}, run).to_dict()


class TsneRequest(BaseModel):
    table: str
    features: list[str]
    categorical: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    prep: dict = Field(default_factory=dict)
    color: str | None = None
    name: str = Field("tsne", max_length=80)


@app.post("/api/unsup/tsne")
def unsup_tsne(req: TsneRequest):
    from lulc_fetch import unsupervised

    table = _table_path(req.table)

    def run(job):
        rep = unsupervised.tsne(table, features=req.features, categorical=req.categorical, params=req.params, prep=req.prep,
                                color=req.color or None, name=req.name, table_dir=TABLE_DIR)
        rep["output_table"], rep["table"] = _rel(rep["output_table"]), _rel(rep["table"])
        return rep

    return jobs.submit("tsne", f"t-SNE · {table.name}", {"source": "Classical ML"}, run).to_dict()


@app.get("/api/models")
def list_models():
    import json as _json

    out = []
    if MODEL_DIR.is_dir():
        for p in sorted(MODEL_DIR.glob("*.joblib"), key=lambda p: -p.stat().st_mtime):
            rep = {}
            side = Path(str(p) + ".json")
            if side.exists():
                try:
                    rep = _json.loads(side.read_text())
                except ValueError:
                    pass
            out.append({"path": ws.rel(p), "name": p.stem, "size_mb": p.stat().st_size / 1e6,
                        "modified": p.stat().st_mtime, **{k: rep.get(k) for k in (
                            "model", "model_title", "task", "target", "features", "accuracy", "kappa", "f1_macro", "r2", "rmse",
                            "classes", "table", "train_rows", "has_proba", "source", "kind", "n_clusters", "method_title")},
                        "silhouette": (rep.get("quality") or {}).get("silhouette")})
    return out


@app.get("/api/models/report")
def model_report(path: str):
    import json as _json

    p = _model_path(path)
    return _json.loads(Path(str(p) + ".json").read_text())


@app.get("/api/models/file")
def model_file(path: str):
    p = _model_path(path)
    return FileResponse(p, filename=p.name)


class SaveReportRequest(BaseModel):
    path: str                              # the model (.joblib)
    folder: str = Field(max_length=1000)


@app.post("/api/models/evaluation/save")
def save_model_evaluation(req: SaveReportRequest):
    """Save a copy of a model's HTML evaluation report in a folder of the user's choice."""
    from lulc_fetch import evaluation

    p = _model_path(req.path)
    folder = _report_folder(req.folder)
    html_path = p.with_suffix(".evaluation.html")
    if not html_path.exists():
        try:
            evaluation.report_for_model(p, html_path)
        except ValueError as e:
            raise HTTPException(404, str(e))
    return {"saved_to": str(_copy_report(html_path, folder))}


@app.get("/api/models/evaluation")
def model_evaluation(path: str, download: bool = False, rebuild: bool = False):
    """The model's HTML evaluation report (rebuilt from the predictions stored in the model if missing)."""
    from lulc_fetch import evaluation

    p = _model_path(path)
    html_path = p.with_suffix(".evaluation.html")
    if rebuild or not html_path.exists():
        try:
            evaluation.report_for_model(p, html_path)
        except ValueError as e:
            raise HTTPException(404, str(e))
    return FileResponse(html_path, media_type="text/html", filename=html_path.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


@app.delete("/api/models")
def delete_model(path: str):
    p = _model_path(path)
    p.unlink(missing_ok=True)
    Path(str(p) + ".json").unlink(missing_ok=True)
    p.with_suffix(".evaluation.html").unlink(missing_ok=True)
    return {"ok": True}


class PredictRequest(BaseModel):
    model: str
    path: str
    band_map: dict[str, int]
    scale: float = 1.0
    offset: float = 0.0
    clip: dict | None = None
    resolution: str = "auto"
    confidence: bool = True
    name: str = Field("classified", max_length=80)


@app.post("/api/ml/predict")
def ml_predict(req: PredictRequest):
    import re

    from lulc_fetch import ml

    model = _model_path(req.model)
    raster = _raster_path(req.path)
    clip = _clip(req.clip)
    if req.resolution not in ("auto", "1", "2", "4", "8", "16"):
        raise HTTPException(400, "Unknown resolution")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:80] or "classified"

    def run(job):
        rep = ml.predict_raster(model, raster, job.dir / f"{stem}.tif", band_map=req.band_map, scale=req.scale,
                                offset=req.offset, clip=clip, resolution=req.resolution, confidence=req.confidence)
        rep["path"] = ws.rel(job.dir / f"{stem}.tif")
        return rep

    return jobs.submit("predict", f"Classify · {req.name}", {"source": "Classical ML"}, run).to_dict()


# ------------------------------------------------------------------ stack layers onto one grid

class StackItem(BaseModel):
    path: str
    name: str = "layer"
    bands: list[int] | None = None
    index: str | None = None
    formula: str | None = Field(None, max_length=500)
    band_map: dict[str, int] = Field(default_factory=dict)
    scale: float = 1.0
    offset: float = 0.0


class StackRequest(BaseModel):
    items: list[StackItem]
    ref: str
    clip: dict | None = None
    factor: int = Field(1, ge=1, le=64)
    name: str = Field("stack", max_length=80)


@app.post("/api/stack")
def stack_job(req: StackRequest):
    import re

    from lulc_fetch.stack import stack

    if not req.items:
        raise HTTPException(400, "Choose at least one layer")
    items = [{**it.model_dump(), "path": str(_raster_path(it.path))} for it in req.items]
    ref = _raster_path(req.ref)
    clip = _clip(req.clip)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_")[:80] or "stack"

    def run(job):
        rep = stack(items, ref, job.dir / f"{stem}.tif", clip=clip, factor=req.factor)
        rep["path"] = ws.rel(job.dir / f"{stem}.tif")
        return rep

    return jobs.submit("stack", f"Stack layers · {req.name}", {"source": "Stack layers"}, run).to_dict()


# ------------------------------------------------------------------ layer export (GeoTIFF / PNG / shapefile / KML)

EXPORT_DIR = ws.Dir("exports")


def _export_target(name: str, ext: str, job=None) -> Path:
    """Where an export is written: the job's own folder (removed automatically if cancelled) or exports/."""
    import re
    import uuid

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_.")[:80] or "layer"
    return (job.dir if job else EXPORT_DIR / uuid.uuid4().hex[:8]) / f"{safe}.{ext}"


def _export_url(path: Path, job=None) -> dict:
    url = f"/api/jobs/{job.id}/files/{path.name}" if job else f"/api/exports/{path.parent.name}/{path.name}"
    return {"url": url, "name": path.name, "size_mb": path.stat().st_size / 1e6}


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
    rgb: list[int] | None = None
    pca: bool = False
    stretch: str = "fixed"
    vmin: float | None = None
    vmax: float | None = None
    cmap: str | None = None
    method: str = "equal"  # shapefile classes: equal | quantile | custom
    classes: int = Field(5, ge=2, le=20)
    breaks: list[float] | None = None
    sieve: int = Field(8, ge=0, le=10000)
    clip: dict | None = None
    folder: str | None = Field(None, max_length=1000)   # save into this folder of the user's computer


@app.post("/api/layers/export")
def export_layer(req: LayerExportRequest):
    """Runs as a background job (progress + cancel). Returns the job; its result holds the download URL."""
    from urllib.parse import quote

    src = _raster_path(req.path)
    folder = _report_folder(req.folder) if req.folder and req.folder.strip() else None
    plain = not (req.index or req.formula or req.composite or req.band or req.rgb) and not req.clip
    if folder is not None or (src.suffix.lower() == ".vrt" and plain and req.format == "tif"):
        if req.format in ("png", "pngw") and plain:
            raise HTTPException(400, "Choose how to display the layer first")
        _clip(req.clip)
        return jobs.submit("export", f"Save {req.name} ({req.format.upper()})", {"format": req.format},
                           lambda job: _export_to_folder(req, job, folder)).to_dict()
    if (plain or (req.rgb and not req.clip)) and req.format == "tif":  # the file itself: no work needed
        result = {"url": f"/api/rasters/file?path={quote(req.path)}", "name": src.name, "size_mb": src.stat().st_size / 1e6}
        job = jobs.submit("export", f"Export {req.name}", {"format": req.format}, lambda job: result)
        return job.to_dict()
    if req.format in ("png", "pngw") and plain:
        raise HTTPException(400, "Choose how to display the layer first")
    if req.format not in ("tif", "png", "pngw", "shp"):
        raise HTTPException(400, f"Unknown format {req.format}")
    _clip(req.clip)
    return jobs.submit("export", f"Export {req.name} ({req.format.upper()})", {"format": req.format},
                       lambda job: _export_layer_now(req, job)).to_dict()


def _export_layer_now(req: "LayerExportRequest", job) -> dict:
    from urllib.parse import quote

    from lulc_fetch import analysis

    src = _raster_path(req.path)
    spec = {"band_map": req.band_map, "scale": req.scale, "offset": req.offset, "index": req.index,
            "formula": req.formula, "composite": req.composite, "band": req.band, "rgb": req.rgb, "pca": req.pca, "stretch": req.stretch,
            "vmin": req.vmin, "vmax": req.vmax, "cmap": req.cmap, "clip": _clip(req.clip)}
    plain = not (req.index or req.formula or req.composite or req.band or req.rgb) and not req.clip
    if req.rgb and not req.clip and req.format == "tif":
        plain = True  # an RGB view of a file exports the file itself (all bands, e.g. every component)
    if req.format == "tif":
        if plain:  # the layer is the file itself
            return {"url": f"/api/rasters/file?path={quote(req.path)}", "name": src.name,
                    "size_mb": src.stat().st_size / 1e6}
        out = analysis.export_layer_tif(src, _export_target(req.name, "tif", job), **spec)
    elif req.format in ("png", "pngw"):
        if plain:
            raise HTTPException(400, "Choose how to display the layer first")
        out = analysis.export_png(src, _export_target(req.name, "png", job), world_file=req.format == "pngw", **spec)
    elif req.format == "shp":
        out, n = analysis.polygonize(src, _export_target(req.name, "zip", job), name=req.name, method=req.method,
                                     classes=req.classes, breaks=req.breaks, sieve_px=req.sieve, **spec)
        return {**_export_url(out, job), "features": n}
    else:
        raise HTTPException(400, f"Unknown format {req.format}")
    return _export_url(out, job)


def _export_to_folder(req: "LayerExportRequest", job, folder: Path | None) -> dict:
    """Export into the job folder, then (optionally) save into the user's folder. Plain VRTs are written as GeoTIFF."""
    src = _raster_path(req.path)
    plain = not (req.index or req.formula or req.composite or req.band or req.rgb) and not req.clip
    if req.rgb and not req.clip and req.format == "tif":
        plain = True
    if plain and req.format == "tif":
        out = _vrt_to_tif(src, _export_target(req.name, "tif", job)) if src.suffix.lower() == ".vrt" else src
        res = {**(_export_url(out, job) if out != src else {"url": f"/api/rasters/file?path={req.path}", "name": src.name,
                                                             "size_mb": src.stat().st_size / 1e6})}
    else:
        res = _export_layer_now(req, job)
        out = job.dir / res["name"]
    if folder is not None:
        res["saved"] = _save_into(out, folder)
        res["saved_to"] = str(folder)
    return res


class VectorExportRequest(BaseModel):
    geojson: dict
    format: str  # shp | geojson | kml
    name: str = "layer"
    clip: dict | None = None
    folder: str | None = Field(None, max_length=1000)


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
    res = {**_export_url(out), "features": len(feats)}
    if req.folder and req.folder.strip():
        res["saved"] = _save_into(out, _report_folder(req.folder))
        res["saved_to"] = str(_report_folder(req.folder))
    return res


@app.get("/api/exports/{export_id}/{name}")
def export_file(export_id: str, name: str):
    path = (EXPORT_DIR / export_id / name).resolve()
    if not path.is_relative_to(EXPORT_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, "No such export")
    return FileResponse(path, filename=path.name)


# ------------------------------------------------------------------ projects, folder browser, saving copies, cache

def _abs_folder(raw: str) -> Path:
    import os
    raw = (raw or "").strip().strip('"').strip("'")
    d = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not raw or not d.is_absolute():
        raise HTTPException(400, "Give a full folder path, e.g. /Users/you/Documents or ~/Documents")
    return d


def _project_info(with_state: bool = False) -> dict:
    p = ws.project()
    return {"project": p, "temporary": p is None, "workspace": str(ws.root()), "app_dir": str(ws.APP_DIR),
            "recent": ws.recent(), **({"state": ws.load_state()} if with_state and p else {})}


def _switch_guard():
    busy = [j for j in jobs.list() if j.status in ("queued", "running")]
    if busy:
        raise HTTPException(409, f"Wait for {len(busy)} running job(s) to finish (or cancel them) before switching projects")


@app.get("/api/project")
def project_get():
    return _project_info(with_state=True)


class ProjectNewRequest(BaseModel):
    name: str = Field(max_length=80)
    folder: str = Field(max_length=1000)


@app.post("/api/project/new")
def project_new(req: ProjectNewRequest):
    _switch_guard()
    try:
        folder = ws.create(req.name, _abs_folder(req.folder))
        ws.open_project(folder)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))
    jobs.clear_finished()
    log.info("Project %s created in %s", req.name, folder)
    return _project_info(with_state=True)


class ProjectOpenRequest(BaseModel):
    folder: str = Field(max_length=1000)


@app.post("/api/project/open")
def project_open(req: ProjectOpenRequest):
    _switch_guard()
    try:
        ws.open_project(_abs_folder(req.folder))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))
    jobs.clear_finished()
    log.info("Project opened: %s", ws.root())
    return _project_info(with_state=True)


@app.post("/api/project/close")
def project_close():
    _switch_guard()
    ws.close_project()
    jobs.clear_finished()
    return _project_info()


class ProjectStateRequest(BaseModel):
    state: dict


@app.put("/api/project/state")
def project_state(req: ProjectStateRequest):
    try:
        return {"saved": ws.save_state(req.state)}
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.delete("/api/project/recent")
def project_forget(folder: str):
    ws.forget(folder)
    return {"recent": ws.recent()}


class RevealRequest(BaseModel):
    path: str | None = None


@app.post("/api/project/reveal")
def project_reveal(req: RevealRequest):
    """Show the project folder (or a saved file's folder) in Finder / Explorer / the file manager."""
    import subprocess
    import sys

    target = Path(req.path).expanduser() if req.path else ws.root()
    if not target.is_absolute():
        target = ws.root() / target
    folder = target if target.is_dir() else target.parent
    if not folder.is_dir():
        raise HTTPException(404, "Folder not found")
    cmd = ["open", str(folder)] if sys.platform == "darwin" else ["explorer", str(folder)] if sys.platform.startswith("win") else ["xdg-open", str(folder)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise HTTPException(500, f"Couldn't open the folder: {e}")
    return {"ok": True}


@app.get("/api/fs/list")
def fs_list(path: str = "", products: bool = False):
    """Folders inside a folder, for the folder picker (file contents are never read).

    products=true also lists Sentinel .SAFE.zip files and marks .SAFE product folders."""
    import os

    from lulc_fetch.safe import product_name

    p = _abs_folder(path) if path.strip() else Path.home()
    if not p.is_dir():
        raise HTTPException(404, f"{p} is not a folder")
    dirs = []
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if c.is_dir() and not c.name.startswith(".") and not c.name.endswith((".app", ".photoslibrary")):
                    dirs.append({"name": c.name, "path": str(c), "project": ws.is_project(c),
                                 **({"product": True} if products and product_name(c.name) else {})})
                elif products and c.is_file() and c.suffix.lower() == ".zip" and product_name(c.name):
                    dirs.append({"name": c.name, "path": str(c), "project": False, "product": True, "file": True})
            except OSError:
                continue
            if len(dirs) >= 800:
                break
    except PermissionError:
        raise HTTPException(403, f"No permission to read {p}")
    home = Path.home()
    shortcuts = [("Home", home), ("Desktop", home / "Desktop"), ("Documents", home / "Documents"), ("Downloads", home / "Downloads")]
    if ws.project():
        shortcuts.insert(0, ("This project", ws.root()))
    shortcuts.append(("App folder", ws.APP_DIR))
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs,
            "project": ws.is_project(p), "writable": os.access(p, os.W_OK), "product": bool(products and product_name(p.name)),
            "shortcuts": [{"name": n, "path": str(d)} for n, d in shortcuts if d.is_dir()]}


class MkdirRequest(BaseModel):
    parent: str = Field(max_length=1000)
    name: str = Field(max_length=120)


@app.post("/api/fs/mkdir")
def fs_mkdir(req: MkdirRequest):
    import re

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", req.name).strip(" .")
    if not name:
        raise HTTPException(400, "Give the folder a name")
    d = _abs_folder(req.parent) / name
    try:
        d.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise HTTPException(400, f"{name} already exists")
    except OSError as e:
        raise HTTPException(400, f"Can't create the folder: {e.strerror or e}")
    return {"path": str(d)}


def _unique(folder: Path, name: str) -> Path:
    dest, i, stem, suf = folder / name, 2, Path(name).stem, Path(name).suffix
    if name.endswith(".evaluation.html"):
        stem, suf = name[: -len(".evaluation.html")], ".evaluation.html"
    while dest.exists():
        dest = folder / f"{stem}_{i}{suf}"
        i += 1
    return dest


def _companions(src: Path) -> list[Path]:
    """Files that belong with src: shapefile parts, sidecar descriptions, world files, model reports."""
    out = []
    if src.suffix.lower() == ".shp":
        out += [src.with_suffix(e) for e in (".shx", ".dbf", ".prj", ".cpg")]
    out += [Path(str(src) + ".json"), Path(str(src) + ".aux.xml"), src.with_suffix(".pgw"), src.with_suffix(".tfw")]
    if src.suffix == ".joblib":
        out.append(src.with_suffix(".evaluation.html"))
    return [p for p in out if p.is_file()]


def _save_into(src: Path, folder: Path) -> list[str]:
    """Copy a workspace file (and its companions) into folder without overwriting. Zipped shapefiles are unpacked."""
    import shutil
    import zipfile

    saved = []
    if src.suffix.lower() == ".zip" and zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            members = [m for m in z.namelist() if not m.endswith("/") and "/" not in m.strip("/") and ".." not in m]
            if any(m.lower().endswith(".shp") for m in members):
                stems = {Path(m).stem for m in members}
                rename = {}
                for st in stems:
                    new, i = st, 2
                    while any((folder / f"{new}{Path(m).suffix}").exists() for m in members if Path(m).stem == st):
                        new = f"{st}_{i}"
                        i += 1
                    rename[st] = new
                for m in members:
                    dest = folder / f"{rename[Path(m).stem]}{Path(m).suffix}"
                    with z.open(m) as fin, open(dest, "wb") as fout:
                        shutil.copyfileobj(fin, fout)
                    saved.append(str(dest))
                return saved
    dest = _unique(folder, src.name)
    shutil.copy2(src, dest)
    saved.append(str(dest))
    for c in _companions(src):
        name = dest.name + c.name[len(src.name):] if c.name.startswith(src.name) else dest.stem + c.name[len(src.stem):]
        if c.suffix == ".html" and src.suffix == ".joblib":
            name = dest.with_suffix("").name + ".evaluation.html"
        shutil.copy2(c, folder / name)
        saved.append(str(folder / name))
    return saved


def _vrt_to_tif(src: Path, out: Path) -> Path:
    """A .SAFE import (a VRT that points at the original product) written as a real GeoTIFF."""
    import rasterio
    from rasterio.shutil import copy as rio_copy

    with rasterio.open(src) as ds:
        big = ds.width * ds.height * ds.count > 2**31
    rio_copy(src, out, driver="GTiff", compress="deflate", tiled=True, blockxsize=512, blockysize=512,
             BIGTIFF="YES" if big else "IF_SAFER", predictor=2)
    return out


class SaveFilesRequest(BaseModel):
    paths: list[str] = Field(max_length=200)
    folder: str = Field(max_length=1000)


@app.post("/api/files/save")
def files_save(req: SaveFilesRequest):
    """Save copies of workspace files (tool outputs, layers, tables, models) into a folder of the user's choice."""
    folder = _report_folder(req.folder)
    saved, skipped = [], []
    base = ws.root().resolve()
    for raw in req.paths:
        src = (ws.root() / raw).resolve()
        if not src.is_file() or not (src.is_relative_to(base) or src.is_relative_to(ws.APP_DIR)):
            skipped.append({"path": raw, "reason": "not a file in this workspace"})
            continue
        try:
            if src.suffix.lower() == ".vrt":
                saved.append(str(_vrt_to_tif(src, _unique(folder, src.with_suffix(".tif").name))))
            else:
                saved += _save_into(src, folder)
        except OSError as e:
            skipped.append({"path": raw, "reason": str(e)})
    log.info("Saved %d file(s) to %s", len(saved), folder)
    return {"folder": str(folder), "saved": saved, "skipped": skipped}


CACHE_DIRS = ("downloads", "analysis", "uploads", "exports", "imports")


@app.get("/api/cache")
def cache_info():
    """Sizes of the workspace's working folders (the cache of the temporary workspace, or the project's files)."""
    import time as _t

    out = []
    for d in CACHE_DIRS:
        p = ws.root() / d
        size, n, oldest = 0, 0, None
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    st = f.stat()
                    size += st.st_size
                    n += 1
                    oldest = min(oldest or st.st_mtime, st.st_mtime)
        out.append({"folder": d, "size_mb": size / 1e6, "files": n, "oldest_days": None if oldest is None else (_t.time() - oldest) / 86400})
    return {"workspace": str(ws.root()), "temporary": ws.project() is None, "folders": out}


class CacheCleanRequest(BaseModel):
    folders: list[str]
    older_than_days: float = Field(7, ge=0, le=3650)


@app.post("/api/cache/clean")
def cache_clean(req: CacheCleanRequest):
    """Delete working files older than N days (whole job / upload folders, so layers never half-break)."""
    import shutil
    import time as _t

    cutoff = _t.time() - req.older_than_days * 86400
    running = {j.id for j in jobs.list() if j.status in ("queued", "running")}
    freed, removed = 0, 0
    for d in req.folders:
        if d not in CACHE_DIRS:
            continue
        p = ws.root() / d
        if not p.is_dir():
            continue
        for item in p.iterdir():
            if item.name in running:
                continue
            files = [f for f in item.rglob("*") if f.is_file()] if item.is_dir() else [item]
            if not files or max(f.stat().st_mtime for f in files) > cutoff:
                continue
            freed += sum(f.stat().st_size for f in files)
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
            removed += 1
    log.info("Cleaned %d item(s), %.1f MB", removed, freed / 1e6)
    return {"removed": removed, "freed_mb": freed / 1e6}


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
    print(f"\n  lulc-fetch web UI → {url}\n  Downloads are saved in {ws.root() / 'downloads'}\n")
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
