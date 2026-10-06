"""Raster layers: info, rendering for the map, index analysis, identify, uploads. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import RASTER_EXTS, RASTER_ROOTS, UPLOAD_EXTS, jobs
from ..core import clip as _clip
from ..core import png_data_url as _png_data_url
from ..core import raster_path as _raster_path

router = APIRouter()


# ------------------------------------------------------------------ analyze existing GeoTIFFs



@router.get("/api/indices")
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


@router.get("/api/formula/check")
def formula_check(formula: str):
    """Validate a custom formula and list the bands it needs (for live feedback while typing)."""
    from lulc_fetch.indices import parse_formula

    try:
        _, bands = parse_formula(formula)
        return {"ok": True, "bands": sorted(bands), "formula": formula.replace("^", "**")}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/rasters")
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


@router.post("/api/rasters/upload")
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


@router.delete("/api/rasters")
def delete_raster(path: str):
    import shutil

    p = _raster_path(path, roots=("uploads", "analysis"))
    shutil.rmtree(p.parent, ignore_errors=True)
    return {"ok": True}


@router.get("/api/rasters/metadata")
def raster_metadata(path: str):
    from lulc_fetch.analysis import metadata

    try:
        return metadata(_raster_path(path))
    except rasterio_errors() as e:
        raise HTTPException(400, f"Can't read the file: {e}")


def rasterio_errors():
    import rasterio.errors
    return (rasterio.errors.RasterioError, OSError, ValueError)


@router.get("/api/rasters/info")
def raster_info(path: str):
    from lulc_fetch.analysis import inspect

    info = inspect(_raster_path(path))
    info["path"] = path
    return info


@router.get("/api/rasters/grid")
def raster_grid(path: str, band: int = 1, scale: float = 1.0, offset: float = 0.0, max_px: int = 300):
    """A band's values on a Web Mercator grid (3D maps: a DEM's heights)."""
    from lulc_fetch.analysis import elevation_grid

    try:
        return elevation_grid(_raster_path(path), band=band, scale=scale, offset=offset, max_px=max(16, min(max_px, 600)))
    except (*rasterio_errors(), ValueError) as e:
        raise HTTPException(400, f"Can't read heights from the file: {e}")


class ProfileRequest(BaseModel):
    path: str
    coords: list[list[float]] = Field(min_length=2, max_length=5000)
    band: int = 1
    samples: int = 256


@router.post("/api/rasters/profile")
def raster_profile(req: ProfileRequest):
    """Heights (a band's values) along a line: View ▸ Measure ▸ Profile."""
    from lulc_fetch.analysis import profile

    try:
        return profile(_raster_path(req.path), req.coords, band=req.band, samples=req.samples)
    except (*rasterio_errors(), ValueError) as e:
        raise HTTPException(400, f"Can't make the profile: {e}")


@router.get("/api/rasters/file")
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


@router.post("/api/analyze/render")
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


@router.post("/api/analyze/pixel")
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


@router.post("/api/analyze/export")
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
