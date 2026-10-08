"""Export data: GeoTIFF, PNG, Shapefile, GeoPackage, GeoJSON, KML. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import clip as _clip
from ..core import jobs
from ..core import raster_path as _raster_path
from ..core import report_folder as _report_folder
from ..core import save_into as _save_into
from ..core import vrt_to_tif as _vrt_to_tif

router = APIRouter()


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


@router.post("/api/layers/export")
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


class VectorLayer(BaseModel):
    name: str = Field("layer", max_length=200)
    geojson: dict


class VectorExportRequest(BaseModel):
    geojson: dict
    format: str  # shp | gpkg | geojson | kml
    name: str = "layer"
    clip: dict | None = None
    folder: str | None = Field(None, max_length=1000)
    layer_name: str | None = Field(None, max_length=200)   # gpkg: the layer's name inside the file (default: name)
    more: list[VectorLayer] = Field(default_factory=list, max_length=200)   # gpkg: more layers in the same file
    crs: str | None = Field(None, max_length=20000)   # shp / gpkg: the file's coordinate system (default WGS 84)


@router.post("/api/vector/export")
def export_vector(req: VectorExportRequest):
    from lulc_fetch import vector_io

    feats = vector_io.features_from_geojson(req.geojson)
    if req.clip:
        feats = vector_io.clip_features(feats, _clip(req.clip))
    if req.format == "gpkg":   # one file, any number of layers
        from lulc_fetch.gpkg import write_gpkg
        layers = [(req.layer_name or req.name, feats)]
        for m in req.more:
            try:
                f = vector_io.features_from_geojson(m.geojson)
                layers.append((m.name, vector_io.clip_features(f, _clip(req.clip)) if req.clip else f))
            except ValueError:   # empty, or nothing inside the area: left out
                continue
        out = write_gpkg(layers, _export_target(req.name, "gpkg"), crs=req.crs)
        res = {**_export_url(out), "features": sum(len(f) for _, f in layers), "layers": len(layers)}
        if req.folder and req.folder.strip():
            res["saved"] = _save_into(out, _report_folder(req.folder))
            res["saved_to"] = str(_report_folder(req.folder))
        return res
    if req.format == "shp":
        if req.crs:   # converted from WGS 84; the .prj says which system
            from rasterio.warp import transform_geom
            from shapely.geometry import mapping, shape
            feats = [(shape(transform_geom("EPSG:4326", req.crs, mapping(g))), p) for g, p in feats]
        out = vector_io.write_shapefile_zip(feats, req.crs or "EPSG:4326", _export_target(req.name, "zip"), req.name)
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


@router.get("/api/exports/{export_id}/{name}")
def export_file(export_id: str, name: str):
    path = (EXPORT_DIR / export_id / name).resolve()
    if not path.is_relative_to(EXPORT_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, "No such export")
    return FileResponse(path, filename=path.name)
