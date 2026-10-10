"""Rainfall–runoff and flood extent (Analysis ▸ Hydrology ▸ Rainfall–runoff, Flood from HAND).

Rainfall–runoff, SCS curve number (USDA NRCS TR-55): the curve number of each cell from its land cover and hydrologic
soil group (A sandy … D clay), or a CN raster of your own; dry / normal / wet antecedent conditions (AMC I, II, III).
For a storm of P mm: S = 25400 / CN − 254, Ia = λ S (λ 0.2, or 0.05 as recent studies suggest), runoff
Q = (P − Ia)² / (P − Ia + S) for P > Ia. With a DEM: the runoff routed downhill (volume in m³ passing each cell), and per
watershed of the outlet points: area, mean CN, runoff depth and volume, time of concentration (Kirpich) and the peak
flow of the SCS triangular unit hydrograph, qp = 0.208 A Q / Tp (m³/s; A km², Q mm, Tp = D / 2 + 0.6 tc hours).

Flood from HAND (Nobre et al. 2016): at a water level h above the streams, every cell whose height above its nearest
drainage is below h is under water, h − HAND deep. Several levels give a depth raster each, an extent polygon each and
the flooded area: a quick first map of where rising rivers spread, without a hydraulic model."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import rasterio

from .. import hydrology as H
from .. import progress
from .common import Surface, open_dem, polygons, read_like, write, write_fc

# curve numbers for hydrologic soil groups A, B, C, D (TR-55, by the nearest cover type)
CN_BY_COVER = {
    "tree": (30, 55, 70, 77), "forest": (30, 55, 70, 77), "shrub": (35, 56, 70, 77), "grass": (39, 61, 74, 80), "rangeland": (49, 69, 79, 84),
    "crop": (67, 78, 85, 89), "built": (89, 92, 94, 95), "urban": (89, 92, 94, 95), "bare": (77, 86, 91, 94), "snow": (98, 98, 98, 98),
    "water": (100, 100, 100, 100), "wetland": (85, 85, 85, 85), "flooded": (85, 85, 85, 85), "mangrove": (80, 85, 88, 90), "moss": (49, 69, 79, 84),
    "cloud": (None,) * 4,
}
# class codes of the land-cover maps the app downloads
SCHEMES = {
    "worldcover": {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare", 70: "snow", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss"},
    "dynamicworld": {0: "water", 1: "tree", 2: "grass", 3: "flooded", 4: "crop", 5: "shrub", 6: "built", 7: "bare", 8: "snow"},
    "esri": {1: "water", 2: "tree", 4: "flooded", 5: "crop", 7: "built", 8: "bare", 9: "snow", 10: "cloud", 11: "rangeland"},
}
HSG = {"A": 0, "B": 1, "C": 2, "D": 3}


def _cover_of(name: str) -> str | None:
    n = name.lower()
    for k in ("mangrove", "wetland", "flooded", "water", "snow", "built", "urban", "crop", "tree", "forest", "shrub", "rangeland", "grass", "bare", "moss", "cloud"):
        if k in n:
            return k
    return None


def cn_table(path, scheme: str = "auto", custom: dict | None = None) -> tuple[dict[int, tuple], dict]:
    """class value → (CN A, B, C, D), from the raster's class names when it has them, else the scheme's codes."""
    with rasterio.open(path) as s:
        tags = s.tags()
    names = {}
    try:
        names = {int(k): v for k, v in json.loads(tags.get("classes", "{}")).items()}
    except (ValueError, TypeError):
        pass
    table, used = {}, {}
    if custom:
        return {int(k): tuple(v) for k, v in custom.items()}, {"source": "your table"}
    if scheme == "auto" and names:
        for v, nm in names.items():
            c = _cover_of(nm)
            if c:
                table[v], used[nm] = CN_BY_COVER[c], c
        if table:
            return table, {"source": "class names", "classes": used}
    sch = SCHEMES.get("worldcover" if scheme == "auto" else scheme)
    if not sch:
        raise ValueError(f"scheme: auto, {', '.join(SCHEMES)}")
    return {v: CN_BY_COVER[c] for v, c in sch.items()}, {"source": scheme if scheme != "auto" else "worldcover codes"}


def amc(cn: np.ndarray, cond: str) -> np.ndarray:
    if cond == "I":
        return 4.2 * cn / (10 - 0.058 * cn)
    if cond == "III":
        return 23 * cn / (10 + 0.13 * cn)
    return cn


def scs_runoff(P: np.ndarray, cn: np.ndarray, lam: float = 0.2) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        S = np.where(cn >= 100, 0.0, 25400 / cn - 254)
        Ia = lam * S
        Q = np.where(P > Ia, (P - Ia) ** 2 / (P - Ia + S), 0.0)
    return np.where(np.isfinite(cn), Q, np.nan)


def runoff(dem, out_dir: Path, *, rain_mm: float | None = None, rain_raster: str | None = None, landcover: str | None = None,
           cn_raster: str | None = None, soil: str = "B", soil_raster: str | None = None, scheme: str = "auto", custom: dict | None = None,
           condition: str = "II", lam: float = 0.2, points: list[list[float]] | None = None, snap_m: float = 150.0,
           duration_h: float = 24.0, name: str | None = None) -> dict:
    """SCS-CN runoff on the DEM's grid (land cover, soil and rain are put on it)."""
    if not landcover and not cn_raster:
        raise ValueError("Give a land-cover map (and the soil group) or a curve-number raster")
    if rain_mm is None and not rain_raster:
        raise ValueError("Give the storm's rainfall (mm)")
    if condition not in ("I", "II", "III"):
        raise ValueError("Antecedent moisture: I (dry), II (normal) or III (wet)")
    out_dir = Path(out_dir)
    stem = name or "runoff"
    g, z = open_dem(dem)
    progress.update(0.05, "Curve numbers")
    meta = {}
    if cn_raster:
        cn = read_like(g, cn_raster)
        meta["source"] = "your curve-number raster"
    else:
        table, meta = cn_table(landcover, scheme, custom)
        lc = read_like(g, landcover, resampling="nearest")
        if soil_raster:
            sg = np.clip(np.rint(read_like(g, soil_raster, resampling="nearest")) - 1, 0, 3)
        else:
            if soil.upper() not in HSG:
                raise ValueError("Soil group: A, B, C or D")
            sg = np.full(lc.shape, HSG[soil.upper()])
        cn = np.full(lc.shape, np.nan)
        for v, cns in table.items():
            m = lc == v
            if m.any() and cns[0] is not None:
                cn[m] = np.asarray(cns, float)[np.nan_to_num(sg[m], nan=1).astype(int)]
        if not np.isfinite(cn).any():
            raise ValueError("None of the land-cover classes has a curve number → choose the map's scheme or give your own table")
    cn = np.clip(amc(cn, condition), 1, 100)
    P = read_like(g, rain_raster) if rain_raster else np.full(cn.shape, float(rain_mm))
    Q = scs_runoff(P, cn, lam)
    with np.errstate(invalid="ignore", divide="ignore"):
        C = np.where(P > 0, Q / P, np.nan)
    cell = np.where(g.valid, g.cell_m2, 0)
    vol = np.nan_to_num(Q) / 1000 * cell                    # m³ from each cell
    outs = [write(g, out_dir / f"{stem}_scs.tif", np.stack([cn, Q, C]), ["Curve number (CN)", f"Runoff depth Q (mm) for the storm", "Runoff coefficient Q / P"])]
    progress.update(0.4, "Routing the runoff")
    s = Surface(g, z)
    routed = H.accumulate(s.down, s.levels, vol.ravel())
    outs.append(write(g, out_dir / f"{stem}_routed_volume.tif", routed, "Runoff volume passing each cell (m³)"))
    ok = g.valid & np.isfinite(Q)
    res = {"outputs": outs, "cn_from": meta.get("source"), "classes": meta.get("classes"), "mean_cn": round(float(np.nanmean(cn[ok])), 1),
           "mean_runoff_mm": round(float(np.nanmean(Q[ok])), 2), "mean_rain_mm": round(float(np.nanmean(P[ok])), 2),
           "runoff_volume_m3": round(float(vol[ok].sum()), 1), "condition": condition}
    if points:
        progress.update(0.7, "Watersheds of the outlets")
        snapped = s.snap(points, snap_m)
        outlets = {}
        for k, i in enumerate(snapped, 1):
            outlets.setdefault(i, k)
        lab = H.label_basins(s.down, s.levels, outlets)
        from .watershed import basin_stats, upstream_length
        info, _ = basin_stats(g, s, lab, outlets, None, upstream_length(s), {})
        cnf, qf, cellf = np.nan_to_num(cn).ravel(), np.nan_to_num(Q).ravel(), cell.ravel()
        rows = []
        for i, k in outlets.items():
            m = lab == k
            A = cellf[m].sum()
            if A <= 0:
                continue
            q_mm = float((qf[m] * cellf[m]).sum() / A)
            tc_h = info[k]["time_of_concentration_min"] / 60
            tp = duration_h / 2 + 0.6 * tc_h
            qp = 0.208 * (A / 1e6) * q_mm / max(tp, 1e-3)
            info[k].update({"mean_cn": round(float((cnf[m] * cellf[m]).sum() / A), 1), "runoff_mm": round(q_mm, 2),
                            "runoff_m3": round(q_mm / 1000 * A, 1), "peak_m3s": round(qp, 3), "time_to_peak_h": round(tp, 2)})
            rows.append({"basin": k, **info[k]})
        outs.append(write_fc(polygons(g, lab.reshape(g.h, g.w), info, key="basin"), out_dir / f"{stem}_watersheds.geojson"))
        table = out_dir / f"{stem}_watersheds.csv"
        cols = ["basin", "area_km2", "mean_cn", "runoff_mm", "runoff_m3", "peak_m3s", "time_to_peak_h", "time_of_concentration_min", "longest_path_km", "mean_slope_deg", "outlet_lon", "outlet_lat"]
        with open(table, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(rows)
        res.update(csv=str(table), watersheds=rows)
    progress.update(1.0, "Done")
    return res


def hand_flood(dem, out_dir: Path, *, levels_m=(1.0, 2.0, 5.0), stream_km2: float = 1.0, max_dist_m: float | None = None,
               conditioned: bool = False, name: str | None = None) -> dict:
    """Flooded extent and depth at each water level (m above the streams), from HAND."""
    from .terrain import hand
    levels_m = sorted({float(h) for h in levels_m if h > 0})
    if not levels_m:
        raise ValueError("Give at least one water level above 0 m")
    out_dir = Path(out_dir)
    stem = name or Path(dem).stem
    g, z = open_dem(dem)
    progress.update(0.05, "Flow on the DEM")
    s = Surface(g, z, filled=conditioned)
    stream = s.streams(stream_km2)
    progress.update(0.5, "Height above the nearest drainage")
    hnd, dist, _ = hand(s, stream, s.filled)
    hnd, dist = hnd.reshape(g.h, g.w), dist.reshape(g.h, g.w)
    near = np.isfinite(hnd) if not max_dist_m else (np.isfinite(hnd) & (dist <= max_dist_m))
    depths, rows = [], []
    lab = np.zeros((g.h, g.w), "int32")
    cell = g.cell_m2
    for k, h in enumerate(levels_m):
        d = np.where(near & (hnd < h), h - hnd, 0.0)
        depths.append(d)
        wet = d > 0
        lab[wet & (lab == 0)] = k + 1                       # the lowest level that floods each cell
        rows.append({"level_m": h, "flooded_km2": round(float(cell[wet].sum() / 1e6), 4), "mean_depth_m": round(float(d[wet].mean()), 3) if wet.any() else 0,
                     "volume_m3": round(float((d * cell).sum()), 1)})
    outs = [write(g, out_dir / f"{stem}_hand_flood_depth.tif", np.stack(depths), [f"Water depth (m) at {h:g} m above the streams" for h in levels_m])]
    # extent polygons: nested, from the highest level down (each level's whole extent)
    info = {k + 1: {"level_m": levels_m[k], "flooded_km2": rows[k]["flooded_km2"]} for k in range(len(levels_m))}
    fc = polygons(g, lab, info, key="first_flooded_at")
    outs.append(write_fc(fc, out_dir / f"{stem}_hand_flood_extent.geojson"))
    outs.append(write(g, out_dir / f"{stem}_hand.tif", hnd, "HAND: height above the nearest drainage (m)"))
    table = out_dir / f"{stem}_hand_flood.csv"
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["level_m", "flooded_km2", "mean_depth_m", "volume_m3"])
        wr.writeheader()
        wr.writerows(rows)
    progress.update(1.0, "Done")
    return {"outputs": outs, "csv": str(table), "levels": rows}
