"""Find imagery: the area of interest, catalogue search, scene metadata and previews. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from shapely.geometry import shape
from shapely.ops import unary_union

from lulc_fetch import sentinel2 as s2
from lulc_fetch.aoi import aoi_mask, make_grid
from lulc_fetch.pipeline import search_geometry
from lulc_fetch.raster import read_to_grid

from .. import aoi_io
from ..core import aoi as _aoi
from ..core import png_data_url as _png_data_url
from ..core import source as _source

router = APIRouter()


# ------------------------------------------------------------------ AOI helpers

@router.get("/api/geocode")
def geocode(q: str):
    if len(q.strip()) < 2:
        raise HTTPException(400, "Type at least 2 characters")
    try:
        return aoi_io.geocode(q.strip())
    except Exception as e:
        raise HTTPException(502, f"Geocoding failed: {e}")


@router.post("/api/aoi/upload")
async def upload_aoi(files: list[UploadFile] = File(...), ask_crs: bool = False):
    """Vector files → WGS 84 GeoJSON. ask_crs (Add data): data without a coordinate system comes back as it is, marked
    crs_missing, for the user to choose its system."""
    payload = [(f.filename or "upload", await f.read()) for f in files]
    if sum(len(d) for _, d in payload) > 50 * 2**20:
        raise HTTPException(413, "Upload is larger than 50 MB")
    try:
        return aoi_io.parse_upload(payload, ask_crs=ask_crs)
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


@router.post("/api/search")
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


@router.post("/api/preview")
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
