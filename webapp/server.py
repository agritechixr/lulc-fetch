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


def _product(path: str) -> dict:
    from lulc_fetch.safe import find_products

    target = (ws.root() / path).resolve()
    for p in find_products(*_product_dirs()):
        if Path(p["path"]).resolve() == target or p["path"] == path:
            return p
    raise HTTPException(404, "No such product in the data folder")


@app.get("/api/products")
def list_products():
    from lulc_fetch.safe import find_products

    out, seen = [], set()
    for p in find_products(*_product_dirs()):
        key = str(Path(p["path"]).resolve())
        if key not in seen:
            seen.add(key)
            out.append({**p, "path": ws.rel(p["path"])})
    return {"folder": str(ws.APP_DIR / "data"), "products": out}


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
        return tableview.page(_table_path(path), offset=max(0, offset), limit=max(1, min(limit, 1000)), query=q[:200],
                              sort=sort, desc=desc)
    except ValueError as e:
        raise HTTPException(400, str(e))


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
            "formula": req.formula, "composite": req.composite, "band": req.band, "rgb": req.rgb, "stretch": req.stretch,
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
def fs_list(path: str = ""):
    """Folders inside a folder, for the folder picker (folders only; file contents are never read)."""
    import os

    p = _abs_folder(path) if path.strip() else Path.home()
    if not p.is_dir():
        raise HTTPException(404, f"{p} is not a folder")
    dirs = []
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if c.is_dir() and not c.name.startswith(".") and not c.name.endswith((".app", ".photoslibrary")):
                    dirs.append({"name": c.name, "path": str(c), "project": ws.is_project(c)})
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
            "project": ws.is_project(p), "writable": os.access(p, os.W_OK),
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
