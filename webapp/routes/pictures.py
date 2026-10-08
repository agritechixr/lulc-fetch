"""Pictures (JPG / PNG …) in Contents. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import TABLE_DIR, jobs
from ..core import clip as _clip
from ..core import raster_path as _raster_path

router = APIRouter()


# ------------------------------------------------------------------ pictures (JPG / PNG …)

def _picture_path(rel: str) -> Path:
    from lulc_fetch.images import PICTURE_EXTS

    p = (ws.root() / rel).resolve()
    if not p.is_relative_to((ws.root() / "uploads").resolve()) or p.suffix.lower() not in PICTURE_EXTS or not p.is_file():
        raise HTTPException(404, "No such picture")
    return p


@router.post("/api/pictures/upload")
async def upload_picture(files: list[UploadFile] = File(...), ask_crs: bool = False):
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
        res = import_picture(dest_dir / pic_name, ask_crs=ask_crs)
    except Exception as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise HTTPException(400, f"Couldn't read {pic_name}: {e}")
    res["path"] = ws.rel(Path(res["path"]))
    res["name"] = pic_name
    return res


@router.get("/api/pictures/file")
def picture_file(path: str):
    p = _picture_path(path)
    return FileResponse(p, filename=p.name, content_disposition_type="inline")


class GeorefRequest(BaseModel):
    path: str
    bounds: list[float] = Field(min_length=4, max_length=4)  # west, south, east, north


@router.post("/api/pictures/georef")
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


@router.post("/api/tables/from-raster")
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
