"""Analysis ▸ Tools ▸ Fuzzy & suitability: fuzzy membership (of a raster, or of the distance to a vector layer), fuzzy
overlay / suitability, fuzzy boundaries and uncertainty, fuzzy c-means classification. Background jobs writing to
analysis/. Logic in lulc_fetch/fuzzy.py."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import jobs
from ..core import raster_path as _raster_path

router = APIRouter()
FN = r"^(linear|small|large|gaussian|near|sigmoid|trapezoid|none)$"


def _out(name: str, ext: str = ".tif") -> Path:
    d = ws.root() / "analysis" / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', name).strip('_')[:60] or 'fuzzy'}{ext}"


class Params(BaseModel):
    a: float | None = None
    b: float | None = None
    c: float | None = None
    d: float | None = None
    mid: float | None = None
    spread: float | None = None
    sigma: float | None = None
    slope: float | None = None


def _p(p: Params) -> dict:
    return {k: v for k, v in p.model_dump().items() if v is not None}


class MembershipRequest(BaseModel):
    raster: str | None = None                       # a raster's values …
    band: int = Field(1, ge=1)
    layer: str | dict | None = None                 # … or the distance to a vector layer's features (fuzzy distance)
    like: str | None = None                         # fuzzy distance: on this raster's grid (else a UTM grid of res m)
    res: float = Field(30, gt=0, le=5000)
    fn: str = Field(pattern=FN)
    params: Params = Field(default_factory=Params)
    name: str = Field("membership", max_length=80)


@router.post("/api/fuzzy/membership")
def fuzzy_membership(req: MembershipRequest):
    """A 0–1 membership raster: of a raster's values, or (fuzzy distance) of the distance in metres to a layer."""
    from lulc_fetch import fuzzy
    from lulc_fetch.geoprocess import read_layer
    if not (req.raster or req.layer):
        raise HTTPException(400, "Give a raster, or a vector layer for a fuzzy distance")
    try:   # the parameters checked now, not after the job started
        fuzzy.membership([1.0], req.fn, **_p(req.params))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if req.layer is not None:
        fc = read_layer(req.layer, ws.root())
        like = str(_raster_path(req.like)) if req.like else None
        return jobs.submit("fuzzy", f"Fuzzy distance ({req.fn})", {"fn": req.fn}, lambda job: {
            **(r := fuzzy.distance_raster(fc, _out(req.name), like=like, res=req.res, fn=req.fn, **_p(req.params))),
            "path": ws.rel(r["path"]), "distance_path": ws.rel(r["distance_path"]), "outputs": [ws.rel(r["path"])]}).to_dict()
    src = _raster_path(req.raster)
    return jobs.submit("fuzzy", f"Fuzzy membership of {src.name} ({req.fn})", {"fn": req.fn}, lambda job: {
        **(r := fuzzy.membership_raster(src, _out(req.name), req.fn, req.band, **_p(req.params))), "path": ws.rel(r["path"])}).to_dict()


class OverlayLayer(BaseModel):
    path: str
    band: int = Field(1, ge=1)
    fn: str = Field("none", pattern=FN)
    params: Params = Field(default_factory=Params)
    weight: float = Field(1, ge=0)
    name: str = Field("", max_length=120)


class OverlayRequest(BaseModel):
    layers: list[OverlayLayer] = Field(min_length=1, max_length=20)
    op: str = Field("gamma", pattern=r"^(and|or|product|sum|gamma|weighted_sum|weighted_product)$")
    gamma: float = Field(0.9, ge=0, le=1)
    classes: bool = True
    name: str = Field("suitability", max_length=80)


@router.post("/api/fuzzy/overlay")
def fuzzy_overlay(req: OverlayRequest):
    """Fuzzy overlay / suitability: every layer turned into membership by its own function, then combined (AND, OR,
    product, sum, gamma, weighted sum, weighted product) into a 0–1 map, with five suitability classes."""
    from lulc_fetch import fuzzy
    layers = []
    for L in req.layers:
        try:
            fuzzy.membership([1.0], L.fn, **_p(L.params))
        except ValueError as e:
            raise HTTPException(400, f"{L.name or Path(L.path).name}: {e}")
        layers.append({"path": str(_raster_path(L.path)), "band": L.band, "fn": L.fn, "params": _p(L.params), "weight": L.weight, "name": L.name})
    if req.op.startswith("weighted") and sum(L["weight"] for L in layers) <= 0:
        raise HTTPException(400, "Give the layers weights above 0")

    def work(job):
        r = fuzzy.overlay(layers, _out(req.name), req.op, req.gamma, req.classes)
        return {**r, "path": ws.rel(r["path"]), "outputs": [ws.rel(x) for x in r["outputs"]]}
    return jobs.submit("fuzzy", f"Fuzzy overlay of {len(layers)} layers ({req.op})", {"op": req.op}, work).to_dict()


class BoundaryRequest(BaseModel):
    raster: str
    band: int = Field(1, ge=1)
    alpha: float = Field(0.5, gt=0, lt=1)
    cuts: list[float] = Field(default_factory=lambda: [0.25, 0.5, 0.75], max_length=9)
    low: float = Field(0.25, ge=0, le=1)
    high: float = Field(0.75, ge=0, le=1)
    blur: float = Field(1.0, ge=0, le=20)
    smooth: int = Field(2, ge=0, le=6)
    min_area_m2: float = Field(0, ge=0)
    simplify_m: float = Field(0, ge=0)
    name: str = Field("", max_length=80)


@router.post("/api/fuzzy/boundary")
def fuzzy_boundary(req: BoundaryRequest):
    """From a membership or probability map: the uncertainty map, α-cut zones, the crisp boundary and the transition
    zone as smoothed polygons."""
    from lulc_fetch import fuzzy
    if req.low >= req.high:
        raise HTTPException(400, "The transition zone needs low < high")
    src = _raster_path(req.raster)

    def work(job):
        r = fuzzy.boundary(src, _out("b").parent, band=req.band, alpha=req.alpha, cuts=req.cuts, low=req.low, high=req.high, blur=req.blur,
                           smooth=req.smooth, min_area_m2=req.min_area_m2, simplify_m=req.simplify_m,
                           name=re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_") or None)
        files = {k: ws.rel(r[k]) for k in ("uncertainty", "zones", "boundary", "transition")}
        return {**r, **files, "outputs": [files["uncertainty"], files["transition"], files["zones"], files["boundary"]]}
    return jobs.submit("fuzzy", f"Fuzzy boundary of {src.name}", {"alpha": req.alpha}, work).to_dict()


class CmeansRequest(BaseModel):
    raster: str
    k: int = Field(4, ge=2, le=12)
    bands: list[int] | None = None
    m: float = Field(2.0, gt=1, le=5)
    name: str = Field("", max_length=80)


@router.post("/api/fuzzy/cmeans")
def fuzzy_cmeans(req: CmeansRequest):
    """Fuzzy classification (fuzzy c-means): a membership band per class, the hard class and the uncertainty."""
    from lulc_fetch import fuzzy
    src = _raster_path(req.raster)

    def work(job):
        r = fuzzy.cmeans(src, _out("c").parent, req.k, bands=req.bands, m=req.m, name=re.sub(r"[^A-Za-z0-9_-]+", "_", req.name).strip("_") or None)
        return {**r, "outputs": [ws.rel(x) for x in r["outputs"]]}
    return jobs.submit("fuzzy", f"Fuzzy c-means of {src.name} ({req.k} classes)", {"k": req.k}, work).to_dict()


@router.get("/api/fuzzy/preview")
def fuzzy_preview(fn: str, lo: float, hi: float, a: float | None = None, b: float | None = None, c: float | None = None,
                  d: float | None = None, mid: float | None = None, spread: float | None = None, sigma: float | None = None,
                  slope: float | None = None):
    """The membership curve over [lo, hi] (for the panel's chart)."""
    import numpy as np

    from lulc_fetch import fuzzy
    if not hi > lo:
        raise HTTPException(400, "hi must be above lo")
    x = np.linspace(lo, hi, 101)
    try:
        y = fuzzy.membership(x, fn, a=a, b=b, c=c, d=d, mid=mid, spread=spread, sigma=sigma, slope=slope)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"x": x.round(6).tolist(), "y": np.nan_to_num(y).round(4).tolist()}


@router.post("/api/fuzzy/suggest")
def fuzzy_suggest(req: dict):
    """Starting parameters for a function from a layer's value statistics (min, max, p2, p98)."""
    from lulc_fetch import fuzzy
    return fuzzy.suggest(req.get("stats") or {}, str(req.get("fn") or "linear"), bool(req.get("higher_is_better", True)))
