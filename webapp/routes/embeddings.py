"""Embeddings menu: Download, Train with, Classify with (via the deep-learning tools), Convert and Explore satellite
embeddings (AlphaEarth, TESSERA). The science is in lulc_fetch/embeddings/; this file only checks the request and runs it
as a job. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import core
from .. import workspace as ws

router = APIRouter()

EMB_CACHE = ws.APP_DIR / "embeddings_cache"   # the AlphaEarth file index (shared by all projects)


@router.get("/api/emb/sources")
def emb_sources():
    from lulc_fetch import embeddings as em
    return {"sources": em.SOURCES, "other": em.OTHER, "years": em.YEARS, "formats": em.FORMATS}


class EmbArea(BaseModel):
    clip: dict
    source: str = Field("aef", pattern="^(aef|tessera)$")
    res: float = Field(10, ge=10, le=160)


def _emb_clip(clip: dict) -> dict:
    from shapely.geometry import shape
    g = core.clip(clip)
    if not g:
        raise HTTPException(400, "Choose an area")
    if shape(g).area > 4:   # degrees²: about 200 × 200 km near the equator
        raise HTTPException(400, "The area is too large: choose an area up to about 200 × 200 km")
    return g


@router.post("/api/emb/estimate")
def emb_estimate(req: EmbArea):
    from lulc_fetch import embeddings as em
    return em.estimate(_emb_clip(req.clip), req.source, req.res)


class EmbAvailRequest(BaseModel):
    clip: dict


@router.post("/api/emb/available")
def emb_available(req: EmbAvailRequest):
    from lulc_fetch import embeddings as em
    g = _emb_clip(req.clip)
    return core.jobs.submit("embcheck", "Satellite embeddings · what's available here", {}, lambda job: em.available(g, EMB_CACHE)).to_dict()


class EmbFetchRequest(EmbArea):
    year: int = Field(2024, ge=2017, le=2030)
    name: str = Field("embedding", max_length=80)
    colour: bool = True


@router.post("/api/emb/fetch")
def emb_fetch(req: EmbFetchRequest):
    from lulc_fetch import embeddings as em
    g = _emb_clip(req.clip)
    if req.res not in em.SOURCES[req.source]["resolutions"]:
        raise HTTPException(400, f"{em.SOURCES[req.source]['short']} is available at {', '.join(map(str, em.SOURCES[req.source]['resolutions']))} m")
    est = em.estimate(g, req.source, req.res)
    if est["width"] * est["height"] > 25_000_000:
        raise HTTPException(400, f"The area is too large at {req.res:g} m ({est['width']} × {est['height']} pixels): choose a smaller area"
                                 + (" or a coarser resolution" if req.source == "aef" else ""))
    stem = core.safe_stem(req.name, "embedding")

    def run(job):
        res = em.fetch(g, req.source, req.year, str(job.dir / f"{stem}.tif"), EMB_CACHE, req.res)
        outs = [res["path"]]
        if req.colour:
            core.log.info("Colour view: the three main directions of variation (PCA) as red, green and blue")
            res["colour"] = em.colour_view(res["path"], str(job.dir / f"{stem}_colour.tif"))
            res["colour"]["path"] = ws.rel(res["colour"]["path"])
            outs.append(res["colour"]["path"])
        res["path"] = ws.rel(res["path"])
        res["outputs"] = [res["path"]] + outs[1:]
        return res

    title = f"Satellite embeddings · {em.SOURCES[req.source]['short']} {req.year} · {req.name}"
    return core.jobs.submit("embfetch", title, {"source": req.source, "year": req.year}, run).to_dict()


class EmbLayerRequest(BaseModel):
    path: str
    points: list[list[float]] | None = Field(None, max_length=500)   # [lon, lat] (similar places)
    name: str = Field("similarity", max_length=80)


@router.post("/api/emb/similar")
def emb_similar(req: EmbLayerRequest):
    from lulc_fetch import embeddings as em
    src = core.raster_path(req.path)
    if not req.points:
        raise HTTPException(400, "Click at least one place on the map")
    stem = core.safe_stem(req.name, "similarity")

    def run(job):
        r = em.similarity(str(src), req.points, str(job.dir / f"{stem}.tif"))
        return core.one_output(r)

    return core.jobs.submit("embsimilar", f"Similar places · {req.name}", {}, run).to_dict()


class EmbTrainRequest(BaseModel):
    path: str                                                # the embedding layer: every band is used
    ground_truth: dict                                       # {"type": "vector", "geojson", "field"} | {"type": "raster", "path", "band"}
    clip: dict | None = None
    arch: str = Field("light_dabnet", max_length=40)
    patch_px: int = Field(256, ge=32, le=1024)
    overlap: float = Field(0.25, ge=0, le=0.75)              # share of a patch
    params: dict = Field(default_factory=dict)               # epochs, batch_size, lr, val_share, early_stop, patience, loss, class_weights, …
    name: str = Field("embedding_model", max_length=80)
    map: bool = True                                         # also classify the whole layer (or area) with the trained model
    class_colors: dict[str, str] | None = None


@router.post("/api/emb/train")
def emb_train(req: EmbTrainRequest):
    """Train a light segmentation model on an embedding layer and labels in one go: 256 × 256 patches (all bands), training
    with early stopping and a report, then the class map of the layer."""
    import rasterio

    from lulc_fetch import dl, dlrunner, patches, progress
    core.require_dl()
    if req.arch not in dl.ARCHS or dl.ARCHS[req.arch]["lib"] != "light":
        raise HTTPException(400, f"Choose one of the light models ({', '.join(k for k, a in dl.ARCHS.items() if a['lib'] == 'light')})")
    src = core.raster_path(req.path)
    gt = dict(req.ground_truth)
    if gt.get("type") == "raster":
        gt["path"] = str(core.raster_path(gt["path"]))
    elif gt.get("type") != "vector" or not gt.get("geojson", {}).get("features"):
        raise HTTPException(400, "Choose the labels: a layer of polygons or points with a class field, or a class raster")
    with rasterio.open(src) as s:
        if s.crs is None or s.crs.is_geographic:
            raise HTTPException(400, "The embedding layer needs a projected coordinate system in metres (e.g. UTM), as downloaded")
        res, bands = abs(s.res[0]), s.count
    patch_m = req.patch_px * res
    clip = core.clip(req.clip) if req.clip else None
    stem = core.safe_stem(req.name, "embedding_model")
    ds_parent = core.PATCH_DIR.path
    out = core.DL_MODEL_DIR.path / stem
    i = 2
    while out.exists() and any(out.iterdir()):
        out = core.DL_MODEL_DIR.path / f"{stem}_{i}"
        i += 1
    params = {"early_stop": True, "patience": 10, **req.params}

    def run(job):
        with progress.span(0, 0.1):
            ds_name = f"{stem}_patches_{job.id}"
            d = patches.make([{"path": str(src), "name": src.stem}], ds_parent, name=ds_name, ground_truth=gt, clip=clip,
                             patch_m=[patch_m, patch_m], overlap_m=[patch_m * req.overlap] * 2, edge="pad", min_valid=0.3,
                             require_labels=True, min_labelled=0.001, remap=True, class_colors=req.class_colors)
            if not d.get("count"):
                raise RuntimeError("No patch has labels: the labels don't overlap the embedding layer (or the area)")
            core.log.info("%d patches of %d × %d pixels × %d bands", d["count"], req.patch_px, req.patch_px, bands)
        p = dict(params)
        if "split" not in req.params and d["count"] < 40:   # few patches (a small labelled area): spatial blocks can't fill both sides
            p["split"] = "random"
            core.log.info("Only %d patches: training / validation split at random (spatial blocks need more)", d["count"])
        if d["count"] < 5:
            raise RuntimeError(f"Only {d['count']} patch{'es' if d['count'] != 1 else ''} with labels: label a larger area, use smaller "
                               "patches (e.g. 128) or more overlap")
        with progress.span(0.1, 0.85 if req.map else 1):
            tr = dlrunner.run("train", dataset=d["folder"], out_dir=str(out), arch=req.arch, encoder="builtin", pretrained=False,
                              params=p, name=req.name)
        core.remember_dl("dl_models.json", str(out))
        res = {"model_folder": str(out), "dataset": d["folder"], "patches": d["count"], "bands": bands, "patch_px": req.patch_px,
               "config": tr.get("config", {}), "report": (out / "report.html").is_file(), "outputs": []}
        if req.map:
            with progress.span(0.85, 1):
                pr = dlrunner.run("predict", model_dir=str(out), inputs=[{"path": str(src), "name": src.stem}],
                                  out_path=str(job.dir / f"{stem}_map.tif"), clip=clip, overlap=0.25, batch_size=8, device=params.get("device", "auto"),
                                  confidence=True)
            res["map"] = ws.rel(pr["path"])
            res["outputs"] = [res["map"]]
        return res

    title = f"Train embedding model · {dl.ARCHS[req.arch]['title']} · {req.name}"
    return core.jobs.submit("embtrain", title, {"model": dl.ARCHS[req.arch]["title"], "bands": bands}, run).to_dict()


@router.get("/api/emb/format")
def emb_format(path: str):
    from lulc_fetch import embeddings as em
    return em.detect(core.raster_path(path))


class EmbConvertRequest(BaseModel):
    path: str
    to: str = Field(pattern="^(float32|float16|int8-aef|int8-scaled)$")
    normalise: bool = False
    name: str = Field("embedding", max_length=80)


@router.post("/api/emb/convert")
def emb_convert(req: EmbConvertRequest):
    from lulc_fetch import embeddings as em
    src = core.raster_path(req.path)
    info = em.detect(src)
    if info["format"] is None:
        raise HTTPException(400, info["error"])
    if info["format"] == req.to and not req.normalise:
        raise HTTPException(400, f"The layer is already {em.FORMATS[req.to]['title']}")
    stem = core.safe_stem(req.name, "embedding")

    def run(job):
        r = em.convert(str(src), str(job.dir / f"{stem}.tif"), req.to, req.normalise)
        return core.one_output(r)

    return core.jobs.submit("embconvert", f"Convert embeddings · {em.FORMATS[req.to]['title']} · {req.name}", {"to": req.to}, run).to_dict()


@router.post("/api/emb/colour")
def emb_colour(req: EmbLayerRequest):
    from lulc_fetch import embeddings as em
    src = core.raster_path(req.path)
    stem = core.safe_stem(src.stem, "embedding")

    def run(job):
        r = em.colour_view(str(src), str(job.dir / f"{stem}_colour.tif"))
        return core.one_output(r)

    return core.jobs.submit("embcolour", f"Colour view · {src.stem}", {}, run).to_dict()
