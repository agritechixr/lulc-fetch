"""Hydrology, viewshed and line of sight, LiDAR (Analysis ▸ Tools ▸ Raster & terrain); pansharpening and spectral unmixing
(Imagery); routing on roads (Spatial analysis); Sentinel-5P air quality (Forecast). Background jobs (progress, History,
Workflows, the Assistant) writing to analysis/. Logic in lulc_fetch/hydrology.py, visibility.py, lidar.py, spectral.py,
routing.py and s5p.py."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import TABLE_DIR, abs_user_folder, jobs, unique
from ..core import raster_path as _raster_path

router = APIRouter()
Point = list[float]


def _out_dir() -> Path:
    d = ws.root() / "analysis" / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60]


def _points(pts: list[Point], what: str, at_least: int = 1) -> list[list[float]]:
    out = []
    for p in pts:
        if len(p) < 2 or not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
            raise HTTPException(400, f"{what}: give longitude, latitude")
        out.append([float(p[0]), float(p[1])])
    if len(out) < at_least:
        raise HTTPException(400, f"Give at least {at_least} {what.lower()}")
    return out


def _table(csv_path: str) -> str:
    """A CSV result copied into tables/ (where the data viewer opens tables)."""
    import shutil
    TABLE_DIR.path.mkdir(parents=True, exist_ok=True)
    t = unique(TABLE_DIR.path, Path(csv_path).name)
    shutil.copyfile(csv_path, t)
    return ws.rel(t)


def _write_fc(fc: dict, name: str) -> str:
    p = _out_dir() / f"{name}.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": fc["features"]}), encoding="utf-8")
    return ws.rel(p)


# ------------------------------------------------------------------ hydrology
class HydrologyRequest(BaseModel):
    dem: str
    products: list[str] = Field(default_factory=lambda: ["filled", "accumulation", "streams", "order"])
    stream_km2: float = Field(1.0, gt=0, le=1e6)          # a cell is on a stream when it drains at least this area
    points: list[Point] = Field(default_factory=list, max_length=500)   # outlets for watersheds ([lon, lat])
    snap_m: float = Field(150, ge=0, le=10_000)
    min_basin_km2: float | None = Field(None, gt=0)
    name: str = Field("", max_length=80)


@router.post("/api/raster/hydrology")
def raster_hydrology(req: HydrologyRequest):
    """Fill sinks, D8 flow direction, flow accumulation (km²), streams with Strahler order (raster and lines), watersheds
    (of outlet points, or every basin) and the topographic wetness index of a DEM."""
    from lulc_fetch import hydrology as H
    p = _raster_path(req.dem)
    if not req.products or set(req.products) - set(H.PRODUCTS):
        raise HTTPException(400, f"products: {', '.join(H.PRODUCTS)}")
    pts = _points(req.points, "Outlet points", 0) if req.points else None

    def work(job):
        r = H.run(p, _out_dir(), products=req.products, stream_km2=req.stream_km2, points=pts, snap_m=req.snap_m,
                  min_basin_km2=req.min_basin_km2, name=_safe(req.name) or p.stem)
        return {"outputs": [ws.rel(x) for x in r["outputs"]], **r["summary"]}
    return jobs.submit("hydrology", f"Hydrology of {p.name}", {"products": req.products}, work).to_dict()


# ------------------------------------------------------------------ visibility
class ViewshedRequest(BaseModel):
    dem: str
    observers: list[Point] = Field(min_length=1, max_length=200)
    observer_h: float = Field(1.7, ge=0, le=10_000)
    target_h: float = Field(0.0, ge=0, le=10_000)
    max_dist_m: float | None = Field(None, gt=0, le=500_000)
    curvature: bool = True
    name: str = Field("viewshed", max_length=80)


@router.post("/api/raster/viewshed")
def raster_viewshed(req: ViewshedRequest):
    """What can be seen from the observers: 1 / 0 for one, how many see each cell for several."""
    from lulc_fetch import visibility as V
    p = _raster_path(req.dem)
    obs = _points(req.observers, "Observers")
    out = _out_dir() / f"{_safe(req.name) or 'viewshed'}.tif"
    return jobs.submit("viewshed", f"Viewshed of {len(obs)} observer{'s' if len(obs) > 1 else ''} on {p.name}", {}, lambda job: {
        **(r := V.viewshed(p, out, obs, observer_h=req.observer_h, target_h=req.target_h, max_dist_m=req.max_dist_m, curvature=req.curvature)),
        "path": ws.rel(r["path"]), "outputs": [ws.rel(r["path"])]}).to_dict()


class SightRequest(BaseModel):
    dem: str
    a: Point
    b: Point
    observer_h: float = Field(1.7, ge=0, le=10_000)
    target_h: float = Field(0.0, ge=0, le=10_000)
    curvature: bool = True
    name: str = Field("line_of_sight", max_length=80)


@router.post("/api/raster/line-of-sight")
def raster_line_of_sight(req: SightRequest):
    """Can A see B? The line split into seen / hidden parts and the profile between them."""
    from lulc_fetch import visibility as V
    p = _raster_path(req.dem)
    a, b = _points([req.a, req.b], "Points", 2)

    def work(job):
        r = V.line_of_sight(p, a, b, observer_h=req.observer_h, target_h=req.target_h, curvature=req.curvature)
        path = _write_fc(r.pop("lines"), _safe(req.name) or "line_of_sight")
        return {**r, "path": path, "outputs": [path], "name": _safe(req.name) or "line_of_sight"}
    return jobs.submit("lineofsight", f"Line of sight on {p.name}", {}, work).to_dict()


# ------------------------------------------------------------------ LiDAR
def _las(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ws.root() / p
    if p.suffix.lower() not in (".las", ".laz") or not p.is_file():
        raise HTTPException(404, f"No LAS / LAZ file at {path}")
    return p


@router.get("/api/lidar/files")
def lidar_files(folder: str):
    """The .las / .laz files in a folder (and its subfolders, one level down)."""
    d = abs_user_folder(folder)
    files = sorted([*d.glob("*.la[sz]"), *d.glob("*.LA[SZ]"), *d.glob("*/*.la[sz]"), *d.glob("*/*.LA[SZ]")])[:500]
    return {"folder": str(d), "files": [{"path": str(f), "name": str(f.relative_to(d)), "mb": round(f.stat().st_size / 1e6, 1)} for f in files]}


@router.get("/api/lidar/info")
def lidar_info(path: str):
    from lulc_fetch import lidar as LD
    try:
        return LD.info(_las(path))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class LidarRequest(BaseModel):
    path: str
    res: float = Field(1.0, gt=0.05, le=1000)
    products: list[str] = Field(default_factory=lambda: ["dtm", "dsm", "chm"])
    crs: str | None = None
    ground_window_m: float = Field(40, ge=2, le=500)
    tree_min_h: float = Field(2.0, ge=0, le=200)
    tree_window_m: float = Field(5.0, ge=1, le=100)
    drop_noise: bool = True
    name: str = Field("", max_length=80)


@router.post("/api/lidar/grid")
def lidar_grid(req: LidarRequest):
    """A point cloud gridded into a ground model (DTM), surface (DSM), height above ground (CHM), density, intensity; tree tops."""
    from lulc_fetch import lidar as LD
    p = _las(req.path)
    if not req.products or set(req.products) - set(LD.PRODUCTS):
        raise HTTPException(400, f"products: {', '.join(LD.PRODUCTS)}")
    return jobs.submit("lidar", f"LiDAR grids of {p.name}", {"products": req.products}, lambda job: {
        **(r := LD.grid(p, _out_dir(), res=req.res, products=req.products, crs=req.crs, ground_window_m=req.ground_window_m,
                        tree_min_h=req.tree_min_h, tree_window_m=req.tree_window_m, drop_noise=req.drop_noise, name=_safe(req.name) or None)),
        "outputs": [ws.rel(x) for x in r["outputs"]], "name": "tree_tops"}).to_dict()


# ------------------------------------------------------------------ pansharpening, unmixing
class PansharpenRequest(BaseModel):
    image: str
    pan: str
    bands: list[int] | None = None
    pan_band: int = Field(1, ge=1)
    method: str = Field("gsa", pattern="^(gsa|brovey|ihs)$")
    name: str = Field("", max_length=80)


@router.post("/api/raster/pansharpen")
def raster_pansharpen(req: PansharpenRequest):
    from lulc_fetch import spectral as SP
    ms, pan = _raster_path(req.image), _raster_path(req.pan)
    out = _out_dir() / f"{_safe(req.name) or ms.stem + '_pansharpened'}.tif"
    return jobs.submit("pansharpen", f"Pansharpen {ms.name}", {"method": req.method}, lambda job: {
        **(r := SP.pansharpen(ms, pan, out, bands=req.bands, method=req.method, pan_band=req.pan_band)), "path": ws.rel(r["path"]), "outputs": [ws.rel(r["path"])]}).to_dict()


class UnmixRequest(BaseModel):
    path: str
    bands: list[int] | None = None
    n_auto: int = Field(3, ge=2, le=10)
    layer: dict | None = None                # polygons / points of pure materials, named by `field`
    field: str | None = None
    constrained: bool = True
    name: str = Field("", max_length=80)


@router.post("/api/raster/unmix")
def raster_unmix(req: UnmixRequest):
    from lulc_fetch import spectral as SP
    p = _raster_path(req.path)
    out = _out_dir() / f"{_safe(req.name) or p.stem + '_fractions'}.tif"
    return jobs.submit("unmix", f"Spectral unmixing of {p.name}", {}, lambda job: {
        **(r := SP.unmix(p, out, bands=req.bands, n_auto=req.n_auto, layer=req.layer, field=req.field, constrained=req.constrained)),
        "path": ws.rel(r["path"]), "csv": _table(r["csv"]), "outputs": [ws.rel(r["path"])]}).to_dict()


# ------------------------------------------------------------------ routing
class RoutingRequest(BaseModel):
    op: str = Field(pattern="^(route|service|closest)$")
    mode: str = Field("car", pattern="^(car|bike|walk)$")
    points: list[Point] = Field(min_length=1, max_length=5000)        # stops / origins / incidents
    facilities: list[Point] = Field(default_factory=list, max_length=5000)
    facility_names: list[str] | None = None
    breaks: list[float] = Field(default_factory=lambda: [5, 10, 15], max_length=12)
    roads: dict | None = None                # a line layer of your own (else OpenStreetMap)
    speed_field: str | None = None
    speed_kmh: float = Field(30, gt=0, le=200)
    margin_km: float = Field(3, ge=0, le=100)
    name: str = Field("", max_length=80)


@router.post("/api/network/routing")
def network_routing(req: RoutingRequest):
    """The quickest route through stops, service areas (minutes) or each incident's closest facility, on OpenStreetMap
    roads or a line layer."""
    from lulc_fetch import routing as R
    pts = _points(req.points, "Points")
    fac = _points(req.facilities, "Facilities") if req.facilities else []
    if req.op == "route" and len(pts) < 2:
        raise HTTPException(400, "A route needs at least two stops")
    if req.op == "closest" and not fac:
        raise HTTPException(400, "Choose the facilities (e.g. hospitals)")
    if req.op == "service" and (not req.breaks or any(not 0 < b <= 600 for b in req.breaks)):
        raise HTTPException(400, "Times: 1 to 600 minutes")
    reach = max(req.breaks) if req.op == "service" else 0
    margin = req.margin_km + (reach / 60 * {"car": 60, "bike": 15, "walk": 5}[req.mode] if req.op == "service" else 0)
    title = {"route": "Route", "service": "Service areas", "closest": "Closest facility"}[req.op]

    def work(job):
        from lulc_fetch import progress
        ways = R.ways_from_layer(req.roads, req.speed_field, req.speed_kmh) if req.roads else \
            R.fetch_osm(R.bbox_of(pts + fac, margin), ws.APP_DIR / "osm_cache", req.mode)
        progress.update(0.4, f"Building the road network ({len(ways):,} roads)")
        net = R.Network(ways, req.mode)
        progress.update(0.5, "Routing")
        fc = R.route(net, pts) if req.op == "route" else R.service_areas(net, pts, req.breaks) if req.op == "service" else \
            R.closest_facility(net, pts, fac, names=req.facility_names)
        name = _safe(req.name) or {"route": "route", "service": "service_areas", "closest": "closest_facility"}[req.op]
        path = _write_fc(fc, name)
        return {**fc["summary"], "path": path, "outputs": [path], "name": name, "roads": "your layer" if req.roads else "OpenStreetMap",
                "nodes": int(len(net.xy))}
    return jobs.submit("routing", f"{title} ({R.MODES[req.mode].lower()})", {"op": req.op}, work).to_dict()


# ------------------------------------------------------------------ Sentinel-5P
class S5PRequest(BaseModel):
    product: str = "no2"
    bbox: list[float] = Field(min_length=4, max_length=4)      # west, south, east, north
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    res: float = Field(0.05, ge=0.01, le=1)
    name: str = Field("", max_length=80)


@router.get("/api/s5p/products")
def s5p_products():
    from lulc_fetch import s5p
    return {"products": [{"id": k, "title": v[1], "unit": v[2], "qa_min": v[4]} for k, v in s5p.PRODUCTS.items()]}


@router.post("/api/s5p/average")
def s5p_average(req: S5PRequest):
    """The mean of a Sentinel-5P product over an area and period (good-quality pixels), with a daily series."""
    from lulc_fetch import s5p
    if req.product not in s5p.PRODUCTS:
        raise HTTPException(400, f"product: {', '.join(s5p.PRODUCTS)}")
    w, s, e, n = req.bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(400, "The area: west, south, east, north in degrees")
    if req.end < req.start:
        raise HTTPException(400, "The end date is before the start")
    out = _out_dir() / f"{_safe(req.name) or 's5p_' + req.product.replace('-', '_') + '_' + req.start + '_' + req.end}.tif"
    return jobs.submit("s5p", f"Sentinel-5P {s5p.PRODUCTS[req.product][1]} {req.start} → {req.end}", {"product": req.product}, lambda job: {
        **(r := s5p.average(tuple(req.bbox), req.start, req.end, req.product, out, res=req.res)),
        "path": ws.rel(r["path"]), "csv": _table(r["csv"]), "outputs": [ws.rel(r["path"])]}).to_dict()


# ------------------------------------------------------------------ View ▸ Time slider ▸ Save animation
class Frame(BaseModel):
    image: str = Field(max_length=60_000_000)      # a PNG data URL as the map shows it
    bounds: list[list[float]]                       # [[south, west], [north, east]]
    label: str = Field("", max_length=120)


class AnimationRequest(BaseModel):
    frames: list[Frame] = Field(min_length=2, max_length=300)
    fps: float = Field(1.0, ge=0.1, le=30)
    format: str = Field("gif", pattern="^(gif|mp4)$")
    width: int = Field(900, ge=200, le=2000)
    name: str = Field("animation", max_length=80)


@router.post("/api/view/animation")
def view_animation(req: AnimationRequest):
    """The time slider's layers as an animated GIF or an MP4 video, each frame placed by its bounds and labelled."""
    import base64
    import io
    import math

    def work(job):
        from PIL import Image, ImageDraw, ImageFont

        from lulc_fetch import progress
        bs = [(min(b[0][0], b[1][0]), min(b[0][1], b[1][1]), max(b[0][0], b[1][0]), max(b[0][1], b[1][1])) for b in (f.bounds for f in req.frames)]
        s, w, n, e = min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)
        kx = math.cos(math.radians((s + n) / 2))
        W = req.width - req.width % 2
        H = max(2, int(W * (n - s) / max((e - w) * kx, 1e-12)))
        H -= H % 2
        if H > 4000:
            raise ValueError("The layers' area is too tall for a video → zoom the frames to a smaller area")
        try:
            font = ImageFont.load_default(size=max(14, W // 40))
        except TypeError:
            font = ImageFont.load_default()
        frames = []
        for k, (f, (fs, fw, fn, fe)) in enumerate(zip(req.frames, bs)):
            data = f.image.split(",", 1)[1] if f.image.startswith("data:") else f.image
            im = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")
            x0, x1 = int((fw - w) / (e - w) * W), int((fe - w) / (e - w) * W)
            y0, y1 = int((n - fn) / (n - s) * H), int((n - fs) / (n - s) * H)
            canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
            canvas.alpha_composite(im.resize((max(1, x1 - x0), max(1, y1 - y0)), Image.LANCZOS), (x0, y0))
            if f.label:
                d = ImageDraw.Draw(canvas)
                tb = d.textbbox((0, 0), f.label, font=font)
                pad = 6
                d.rectangle([8, H - (tb[3] - tb[1]) - 3 * pad - 8, 8 + (tb[2] - tb[0]) + 2 * pad, H - 8], fill=(0, 0, 0, 170))
                d.text((8 + pad, H - (tb[3] - tb[1]) - 2 * pad - 8 - tb[1]), f.label, font=font, fill=(255, 255, 255, 255))
            frames.append(canvas.convert("RGB"))
            progress.update(0.8 * (k + 1) / len(req.frames), f"Frame {k + 1} of {len(req.frames)}")
        name = (_safe(req.name) or "animation") + "." + req.format
        job.dir.mkdir(parents=True, exist_ok=True)
        out = job.dir / name
        if req.format == "gif":
            pal = [fr.convert("P", palette=Image.ADAPTIVE, colors=255) for fr in frames]
            pal[0].save(out, save_all=True, append_images=pal[1:], duration=int(1000 / req.fps), loop=0, optimize=True)
        else:
            import cv2
            import numpy as np
            vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), req.fps, (W, H))
            if not vw.isOpened():
                raise RuntimeError("This computer cannot write MP4 video → save a GIF instead")
            for fr in frames:
                vw.write(cv2.cvtColor(np.asarray(fr), cv2.COLOR_RGB2BGR))
            vw.release()
        return {"file": name, "url": f"/api/jobs/{job.id}/files/{name}", "frames": len(frames), "size": [W, H], "mb": round(out.stat().st_size / 1e6, 2)}
    return jobs.submit("animation", f"Animation of {len(req.frames)} layers ({req.format.upper()})", {}, work).to_dict()
