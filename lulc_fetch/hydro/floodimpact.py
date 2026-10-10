"""Flood depth and flood impact (Analysis ▸ Hydrology ▸ Flood depth, Flood impact).

Flood depth, FwDET (Floodwater Depth Estimation Tool, Cohen et al. 2018, 2019): a flood extent (from the SAR flood map,
the water mask or polygons) and a DEM. The water surface at the edge of the flood is the ground height there; each
flooded cell takes the water surface of its nearest edge cell (edge cells next to permanent water or the DEM's border
are left out, as in FwDET 2.0), depth = water surface − ground, smoothed a little and never below 0.

Flood impact: what a flood covers: hectares of each land-cover class (cropland, built-up …), how many buildings
(points or polygons) and km of roads (lines) are flooded, and people (a population raster such as WorldPop or GHS-POP,
counts per cell). With a depth raster, everything is split by depth: < 0.5 m, 0.5–1 m, 1–2 m, > 2 m."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import rasterio
from scipy import ndimage as ndi

from .. import progress
from .common import open_dem, read_like, write, write_fc

DEPTHS = [(0, 0.5, "< 0.5 m"), (0.5, 1, "0.5–1 m"), (1, 2, "1–2 m"), (2, np.inf, "> 2 m")]
FLOOD_CLASSES = (4, 5)          # the SAR flood map: flood (new water), water


def _extent(g, flood: str | None, polygons: dict | None, classes=FLOOD_CLASSES) -> tuple[np.ndarray, np.ndarray]:
    """(flooded, permanent water) on the DEM's grid from a flood raster (classes, or > 0.5 for masks / confidences) or polygons."""
    perm = np.zeros((g.h, g.w), bool)
    if polygons:
        from rasterio import features
        from shapely.geometry import shape

        from ..convert import _reproject_all
        geoms = _reproject_all([shape(f["geometry"]) for f in polygons.get("features", []) if f.get("geometry")], "EPSG:4326", g.crs)
        if not geoms:
            raise ValueError("The flood layer has no shapes")
        fl = features.rasterize(((gm, 1) for gm in geoms), out_shape=(g.h, g.w), transform=g.transform, all_touched=False, dtype="uint8") > 0
        return fl & g.valid, perm
    with rasterio.open(flood) as s:
        tags = s.tags()
    a = read_like(g, flood, resampling="nearest")
    if "classes" in tags:
        names = json.loads(tags["classes"])
        flood_ids = [int(k) for k, v in names.items() if any(w in v.lower() for w in ("flood", "water"))]
        perm_ids = [int(k) for k, v in names.items() if "permanent" in v.lower()]
        if tags.get("water_mask") == "1":               # the Water mask tool: land / water / masked
            flood_ids = [int(k) for k, v in names.items() if v.lower().startswith("water")]
        fl = np.isin(a, [i for i in flood_ids if i not in perm_ids])
        perm = np.isin(a, perm_ids)
    else:
        fl = np.nan_to_num(a) > 0.5
    return fl & g.valid, perm & g.valid


def depth(dem, out_dir: Path, *, flood: str | None = None, polygons: dict | None = None, smooth_px: int = 3, include_permanent: bool = False,
          name: str | None = None) -> dict:
    out_dir = Path(out_dir)
    stem = name or "flood"
    g, z = open_dem(dem)
    progress.update(0.1, "Flood extent on the DEM")
    fl, perm = _extent(g, flood, polygons)
    if include_permanent:
        fl = fl | perm
    if fl.sum() < 4:
        raise ValueError("The flood covers (almost) no cell of the DEM")
    progress.update(0.3, "Water surface from the flood's edge")
    edge = fl & ~ndi.binary_erosion(fl, structure=np.ones((3, 3)), border_value=1)
    # FwDET 2.0: edge cells touching permanent water or the DEM's no-data / border don't show the water surface
    bad = ndi.binary_dilation(perm | ~g.valid, structure=np.ones((3, 3)))
    bad[0, :] = bad[-1, :] = bad[:, 0] = bad[:, -1] = True
    good = edge & ~bad & np.isfinite(z)
    if good.sum() < 1:
        good = edge & np.isfinite(z)
    _, (ri, ci) = ndi.distance_transform_edt(~good, return_indices=True)
    wse = z[ri, ci]
    if smooth_px > 1:   # a focal mean of the water surface (FwDET 2.0's smoothing), over flooded cells only
        num = ndi.uniform_filter(np.where(fl, wse, 0), smooth_px)
        den = ndi.uniform_filter(fl.astype(float), smooth_px)
        wse = np.where(den > 0, num / np.maximum(den, 1e-9), wse)
    d = np.where(fl, np.maximum(wse - z, 0), np.nan)
    cell = g.cell_m2
    rows = [{"depth": lab, "km2": round(float(cell[fl & (d >= lo) & (d < hi)].sum() / 1e6), 4)} for lo, hi, lab in DEPTHS]
    outs = [write(g, out_dir / f"{stem}_depth.tif", np.stack([d, np.where(fl, wse, np.nan)]), ["Water depth (m, FwDET)", "Water surface elevation (m)"])]
    progress.update(1.0, "Done")
    v = d[fl & np.isfinite(d)]
    return {"outputs": outs, "path": outs[0], "flooded_km2": round(float(cell[fl].sum() / 1e6), 4), "mean_depth_m": round(float(v.mean()), 2),
            "max_depth_m": round(float(np.percentile(v, 99)), 2), "volume_m3": round(float(np.nansum(d * cell)), 1), "by_depth": rows}


def people_on(g, population: str) -> np.ndarray:
    """A population raster (people per cell) on another grid, keeping the totals: people per m² resampled, times the
    new cells' area."""
    from rasterio.warp import Resampling, reproject

    from ..raster_ops import _pixel_metres
    with rasterio.open(population) as s:
        a = np.maximum(s.read(1, masked=True).astype("float64").filled(0), 0)
        dx, dy = _pixel_metres(s, s.height)
        dens = a / (np.asarray(dx) * dy)
        out = np.zeros((g.h, g.w))
        reproject(dens, out, src_transform=s.transform, src_crs=s.crs, dst_transform=g.transform, dst_crs=g.crs,
                  resampling=Resampling.average if abs(g.transform.a) > abs(s.transform.a) else Resampling.bilinear)
    return np.nan_to_num(out) * g.cell_m2


def _layer_hits(g, layer: dict, wet: np.ndarray, depth_m: np.ndarray | None):
    """Per feature: flooded or not, length / area flooded, deepest water; for points, polygons and lines."""
    from rasterio import features
    from shapely.geometry import mapping, shape

    from ..convert import _reproject_all
    feats = [f for f in layer.get("features", []) if f.get("geometry")]
    geoms = _reproject_all([shape(f["geometry"]) for f in feats], "EPSG:4326", g.crs)
    out = []
    geo = g.crs.is_geographic
    for f, gm in zip(feats, geoms):
        if gm.is_empty:
            continue
        if gm.geom_type.endswith("Point"):
            pts = [gm] if gm.geom_type == "Point" else list(gm.geoms)
            hit = False
            dmax = 0.0
            for p in pts:
                r, c = rasterio.transform.rowcol(g.transform, p.x, p.y)
                if 0 <= r < g.h and 0 <= c < g.w and wet[r, c]:
                    hit = True
                    dmax = max(dmax, float(depth_m[r, c]) if depth_m is not None and np.isfinite(depth_m[r, c]) else 0)
            out.append((f, gm, hit, 0.0, dmax))
            continue
        m = features.rasterize([(mapping(gm), 1)], out_shape=(g.h, g.w), transform=g.transform, all_touched=True, dtype="uint8") > 0
        hitc = m & wet
        if not hitc.any():
            out.append((f, gm, False, 0.0, 0.0))
            continue
        frac = hitc.sum() / max(m.sum(), 1)
        size = (gm.length if "Line" in gm.geom_type else gm.area)
        if geo:   # degrees → metres (roughly, at the feature's latitude)
            k = 111_320 * np.cos(np.radians(gm.centroid.y))
            size = size * k if "Line" in gm.geom_type else size * k * 110_574
        dmax = float(np.nanmax(depth_m[hitc])) if depth_m is not None else 0.0
        out.append((f, gm, True, float(size * frac), dmax))
    return out


def impact(out_dir: Path, *, flood: str | None = None, polygons: dict | None = None, depth_raster: str | None = None, landcover: str | None = None,
           population: str | None = None, buildings: dict | None = None, roads: dict | None = None, name: str = "flood_impact") -> dict:
    """What the flood covers. The flood (or the depth raster) sets the grid."""
    out_dir = Path(out_dir)
    base = depth_raster or flood
    if not base and polygons:
        if landcover:
            base = landcover
        elif population:
            base = population
        else:
            raise ValueError("With flood polygons, give a land-cover or population raster too (it sets the grid)")
    g, a = open_dem(base)
    progress.update(0.1, "Flood extent")
    if depth_raster:
        dpt = a
        wet = np.nan_to_num(dpt) > 0.01
    else:
        dpt = None
        wet, _ = _extent(g, flood if flood else None, polygons)
    if not wet.any():
        raise ValueError("Nothing is flooded")
    cell = g.cell_m2
    res = {"flooded_km2": round(float(cell[wet].sum() / 1e6), 4)}
    rows = []
    bands = [(lo, hi, lab) for lo, hi, lab in DEPTHS] if dpt is not None else [(None, None, "all")]

    def in_band(lo, hi):
        return wet if lo is None else wet & (dpt >= lo) & (dpt < hi)
    if landcover:
        progress.update(0.3, "Land cover under water")
        lc = read_like(g, landcover, resampling="nearest")
        with rasterio.open(landcover) as s:
            try:
                names = {int(k): v for k, v in json.loads(s.tags().get("classes", "{}")).items()}
            except ValueError:
                names = {}
        cover = {}
        for v in np.unique(lc[wet & np.isfinite(lc)]):
            nm = names.get(int(v), f"class {int(v)}")
            for lo, hi, lab in bands:
                ha = float(cell[in_band(lo, hi) & (lc == v)].sum() / 1e4)
                if ha > 0:
                    rows.append({"what": "land cover", "item": nm, "depth": lab, "amount": round(ha, 2), "unit": "ha"})
            cover[nm] = round(float(cell[wet & (lc == v)].sum() / 1e4), 2)
        res["land_cover_ha"] = dict(sorted(cover.items(), key=lambda kv: -kv[1]))
    if population:
        progress.update(0.5, "People")
        pop = people_on(g, population)
        for lo, hi, lab in bands:
            n = float(pop[in_band(lo, hi)].sum())
            if n > 0:
                rows.append({"what": "people", "item": "population", "depth": lab, "amount": round(n), "unit": "people"})
        res["people"] = int(round(float(pop[wet].sum())))
    outs = []
    for kind, layer, unit in (("buildings", buildings, "count"), ("roads", roads, "km")):
        if not layer:
            continue
        progress.update(0.7, f"{kind.capitalize()}")
        hits = _layer_hits(g, layer, wet, dpt)
        feats = []
        n_hit, km = 0, 0.0
        for f, gm, hit, size, dmax in hits:
            if not hit:
                continue
            n_hit += 1
            km += size / 1000
            feats.append({"type": "Feature", "properties": {**(f.get("properties") or {}), "flooded": True, "flooded_m": round(size, 1),
                                                            "max_depth_m": round(dmax, 2) if dpt is not None else None}, "geometry": f["geometry"]})
        if kind == "buildings":
            res["buildings"] = n_hit
            res["buildings_total"] = len(hits)
            rows.append({"what": "buildings", "item": "flooded", "depth": "all", "amount": n_hit, "unit": "buildings"})
        else:
            res["roads_km"] = round(km, 3)
            rows.append({"what": "roads", "item": "flooded", "depth": "all", "amount": round(km, 3), "unit": "km"})
        if feats:
            outs.append(write_fc({"features": feats}, out_dir / f"{name}_{kind}.geojson"))
    table = out_dir / f"{name}.csv"
    table.parent.mkdir(parents=True, exist_ok=True)
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["what", "item", "depth", "amount", "unit"])
        wr.writeheader()
        wr.writerows(rows)
    progress.update(1.0, "Done")
    return {"outputs": outs, "csv": str(table), **res}
