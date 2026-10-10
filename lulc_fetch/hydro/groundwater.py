"""Groundwater potential zones (Analysis ▸ Hydrology ▸ Groundwater potential): the usual GIS–AHP method (e.g. Jha et al.
2010, Machiwal et al. 2011): thematic layers scored for how well water can soak in and be stored, weighted by AHP, summed
into a potential index and five zones (very poor … very good).

Layers (each optional except the DEM):
- from the DEM: slope (gentle → more infiltration), drainage density (low → more infiltration; stream length per km²
  within a moving window), TWI (wet spots);
- rainfall (more → more recharge): a number or a raster, e.g. Rainfall data's CHIRPS mean annual;
- land cover (water bodies, wetlands, forest and crops recharge; built-up and bare don't), by class names;
- lineament density (fractures carry water): from a line layer of lineaments / faults;
- geology and soil: rasters with a score (1 poor … 5 good) for each class.
Weights: AHP from the usual order of importance (rainfall, geology, lineaments, drainage density, slope, land cover,
soil, TWI), or your own. With wells (points with a yield or water-level field): the mean value in each zone and the
rank correlation, to check the map."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .. import ahp
from .. import progress
from .common import Surface, open_dem, read_like, write
from .terrain import slope_aspect

ORDER = ["rainfall", "geology", "lineaments", "drainage", "slope", "landcover", "soil", "twi"]
TITLES = {"rainfall": "Rainfall", "geology": "Geology", "lineaments": "Lineament density", "drainage": "Drainage density", "slope": "Slope",
          "landcover": "Land cover", "soil": "Soil", "twi": "Wetness (TWI)"}
LC_SCORE = {"water": 5, "wetland": 5, "flooded": 5, "mangrove": 4, "tree": 4, "forest": 4, "crop": 4, "grass": 3, "rangeland": 3, "shrub": 3,
            "moss": 2, "bare": 2, "snow": 1, "built": 1, "urban": 1}
ZONES = {1: ("Very poor", (215, 25, 28, 255)), 2: ("Poor", (253, 174, 97, 255)), 3: ("Moderate", (255, 255, 191, 255)),
         4: ("Good", (116, 173, 209, 255)), 5: ("Very good", (44, 123, 182, 255))}


def _density(g, lines_m: np.ndarray, radius_m: float) -> np.ndarray:
    """Length of lines (m per cell) per km² within radius_m."""
    k = max(1, int(round(radius_m / g.cell_m)))
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
    disk = (xx ** 2 + yy ** 2 <= k * k).astype(float)
    tot = ndi.convolve(lines_m, disk, mode="constant")
    area_km2 = disk.sum() * g.cell_m ** 2 / 1e6
    return tot / 1000 / area_km2


def run(dem, out_dir: Path, *, rain_mm: float | None = None, rain_raster: str | None = None, landcover: str | None = None,
        lineaments: dict | None = None, geology: str | None = None, geology_scores: dict | None = None, soil: str | None = None,
        soil_scores: dict | None = None, stream_km2: float = 0.5, radius_m: float = 1000.0, weights: dict | None = None,
        wells: dict | None = None, wells_field: str | None = None, name: str | None = None) -> dict:
    out_dir = Path(out_dir)
    stem = name or "groundwater"
    g, z = open_dem(dem)
    progress.update(0.05, "Terrain layers")
    s = Surface(g, z)
    factors: dict[str, np.ndarray] = {}
    slope, _, tan = slope_aspect(g, s.filled)
    factors["slope"] = 1 - ahp.score_continuous(slope)
    stream = s.streams(stream_km2)
    factors["drainage"] = 1 - ahp.score_continuous(_density(g, np.where(stream, s.step, 0).reshape(g.h, g.w), radius_m))
    sca = (s.area_km2 * 1e6 / np.sqrt(np.maximum(np.where(g.valid, g.cell_m2, 0).ravel(), 1e-9))).reshape(g.h, g.w)
    factors["twi"] = ahp.score_continuous(np.log(sca / np.maximum(tan, 1e-3)))
    if rain_raster:
        factors["rainfall"] = ahp.score_continuous(read_like(g, rain_raster))
    elif rain_mm:
        pass   # one number everywhere scores nothing (it doesn't vary): left out
    if landcover:
        import rasterio

        from .runoff import SCHEMES, _cover_of
        lc = read_like(g, landcover, resampling="nearest")
        with rasterio.open(landcover) as src:
            try:
                names = {int(k): v for k, v in json.loads(src.tags().get("classes", "{}")).items()}
            except ValueError:
                names = {}
        names = names or SCHEMES["worldcover"]
        sc = np.full(lc.shape, np.nan)
        for v, nm in names.items():
            cov = _cover_of(nm) or nm
            if cov in LC_SCORE:
                sc[lc == v] = LC_SCORE[cov] / 5
        factors["landcover"] = sc
    if lineaments:
        from rasterio import features
        from shapely.geometry import shape

        from ..convert import _reproject_all
        geoms = _reproject_all([shape(f["geometry"]) for f in lineaments.get("features", []) if f.get("geometry")], "EPSG:4326", g.crs)
        on = features.rasterize(((gm, 1) for gm in geoms), out_shape=(g.h, g.w), transform=g.transform, all_touched=True, dtype="uint8") > 0
        factors["lineaments"] = ahp.score_continuous(_density(g, np.where(on, g.cell_m, 0.0), radius_m))
    for key, path, scores in (("geology", geology, geology_scores), ("soil", soil, soil_scores)):
        if not path:
            continue
        a = read_like(g, path, resampling="nearest")
        if not scores:
            raise ValueError(f"Give a score (1 poor … 5 good) for each {key} class")
        sc = np.full(a.shape, np.nan)
        for v, s_ in scores.items():
            sc[a == float(v)] = float(s_) / 5
        factors[key] = sc
    keys = [k for k in ORDER if k in factors]
    if weights:
        w = np.array([float(weights.get(k, 0)) for k in keys])
        if w.sum() <= 0:
            raise ValueError("The weights add up to 0")
        w = w / w.sum()
        cons = None
    else:
        ah = ahp.weights(ahp.from_ranks(list(range(1, len(keys) + 1))))
        w = np.array(ah["weights"])
        cons = ah["cr"]
    progress.update(0.7, "Groundwater potential index")
    ok = g.valid.copy()
    idx = np.zeros((g.h, g.w))
    for k, wk in zip(keys, w):
        ok &= np.isfinite(factors[k])
        idx += wk * np.nan_to_num(factors[k])
    idx = np.where(ok, idx, np.nan)
    qs = np.nanpercentile(idx, [20, 40, 60, 80])
    zone = np.where(ok, np.digitize(idx, qs) + 1, 0).astype("uint8")
    outs = [write(g, out_dir / f"{stem}_potential_index.tif", idx, "Groundwater potential index (0–1, AHP weighted)"),
            write(g, out_dir / f"{stem}_zones.tif", zone, "Groundwater potential zone", dtype="uint8", nodata=0,
                  cmap={k: v[1] for k, v in ZONES.items()}, tags={"classes": {str(k): v[0] for k, v in ZONES.items()}}),
            write(g, out_dir / f"{stem}_factor_scores.tif", np.stack([factors[k] for k in keys]), [f"{TITLES[k]} score (0–1)" for k in keys])]
    ha = g.cell_m2 / 1e4
    res = {"outputs": outs, "factors": {TITLES[k]: round(float(x), 4) for k, x in zip(keys, w)}, "cr": cons,
           "zone_pct": {ZONES[k][0]: round(100 * float(ha[zone == k].sum() / ha[ok].sum()), 2) for k in ZONES}}
    if rain_mm and not rain_raster:
        res["note"] = "One rainfall value for the whole area doesn't vary, so it was left out (give a raster to use it)"
    if wells and wells_field:
        import rasterio
        from scipy.stats import spearmanr
        from rasterio.warp import transform as wt
        vals, zs = [], []
        for f in wells.get("features", []):
            gm, v = f.get("geometry") or {}, (f.get("properties") or {}).get(wells_field)
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if gm.get("type") != "Point":
                continue
            x, y = gm["coordinates"][:2]
            if g.crs.to_epsg() != 4326:
                xs, ys = wt("EPSG:4326", g.crs, [x], [y])
                x, y = xs[0], ys[0]
            r, c = rasterio.transform.rowcol(g.transform, x, y)
            if 0 <= r < g.h and 0 <= c < g.w and zone[r, c] > 0:
                vals.append(v)
                zs.append(int(zone[r, c]))
        if len(vals) >= 5:
            rho, pv = spearmanr(zs, vals)
            res["validation"] = {"wells": len(vals), "spearman_rho": round(float(rho), 3), "p_value": round(float(pv), 4),
                                 "mean_by_zone": {ZONES[k][0]: round(float(np.mean([v for v, zz in zip(vals, zs) if zz == k])), 3)
                                                  for k in ZONES if k in zs}}
    table = out_dir / f"{stem}_weights.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["factor", "weight"])
        for k, x in zip(keys, w):
            wr.writerow([TITLES[k], round(float(x), 4)])
    res["csv"] = str(table)
    progress.update(1.0, "Done")
    return res
