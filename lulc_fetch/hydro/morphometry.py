"""Watershed morphometry and prioritisation (Analysis ▸ Hydrology ▸ Morphometry & prioritisation).

For every sub-watershed (or the watersheds of outlet points), the classical parameters (Horton 1945, Strahler 1964,
Schumm 1956, Miller 1953):
- linear: stream count and length by Strahler order, mean bifurcation ratio Rb, stream-length ratio;
- areal: area, perimeter, basin length Lb (the longest flow path), form factor Ff = A / Lb², elongation ratio
  Re = 2 √(A/π) / Lb, circularity ratio Rc = 4πA / P², compactness Cc = 0.2821 P / √A, drainage density Dd = ΣL / A,
  stream frequency Fs = ΣN / A, drainage texture T = ΣN / P, length of overland flow Lo = 1 / (2 Dd), constant of
  channel maintenance C = 1 / Dd, infiltration number If = Dd · Fs;
- relief: basin relief H, relief ratio Rh = H / Lb, ruggedness number Rn = H · Dd, hypsometric integral
  HI = (mean − min) / (max − min) (> 0.6 young, 0.35–0.6 mature, < 0.35 old landscape) and the hypsometric curve.

Prioritisation by the compound value (Biswas et al. 1999): sub-watersheds are ranked on the erosion-prone parameters
(higher Rb, Dd, Fs, T, Rn, If, Lo → rank 1) and the shape ones (lower Ff, Re, Rc → rank 1, compact basins shed water
fastest); the mean rank is the compound value; low compound values are high priority for soil and water conservation."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .. import hydrology as H
from .. import progress
from .common import Surface, open_dem, polygons, write, write_fc
from .network import links
from .watershed import basin_stats, delineate, upstream_length

LINEAR = ("Rb", "Dd", "Fs", "T", "Rn", "If", "Lo")          # higher → more erosion → rank 1
SHAPE = ("Ff", "Re", "Rc")                                  # lower → rank 1
PRIORITY = {1: ("High", (215, 48, 39, 255)), 2: ("Medium", (254, 224, 139, 255)), 3: ("Low", (26, 152, 80, 255))}


def _rank(values: np.ndarray, high_first: bool) -> np.ndarray:
    from scipy.stats import rankdata
    v = np.nan_to_num(values, nan=-np.inf if high_first else np.inf)
    return rankdata(-v if high_first else v, method="min")


def run(dem, out_dir: Path, *, mode: str = "subbasins", points: list[list[float]] | None = None, snap_m: float = 150.0,
        stream_km2: float = 0.5, basin_km2: float = 5.0, conditioned: bool = False, name: str | None = None) -> dict:
    """mode 'subbasins': sub-watersheds draining about basin_km2 each (their own streams at stream_km2); 'points': the
    watersheds of outlet points."""
    out_dir = Path(out_dir)
    stem = name or "morphometry"
    g, z = open_dem(dem)
    progress.update(0.05, "Flow on the DEM")
    s = Surface(g, z, filled=conditioned)
    progress.update(0.4, "Sub-watersheds")
    if mode == "subbasins":
        lab, outlets, _, _, _ = delineate(s, mode="subbasins", stream_km2=basin_km2)
    elif mode == "points":
        lab, outlets, _, _, _ = delineate(s, mode="points", points=points, snap_m=snap_m)
    else:
        raise ValueError("mode: subbasins or points")
    stream = s.streams(stream_km2)
    order = H.strahler(s.down, s.levels, stream)
    L = links(g, s, stream)
    up = upstream_length(s)
    info, _ = basin_stats(g, s, lab, outlets, stream, up, {})
    progress.update(0.6, "Morphometric parameters")
    zf = s.filled.ravel()
    rows, curves = [], []
    for i, k in outlets.items():
        if k not in info:
            continue
        m = lab == k
        if m.sum() < 9:                    # a cell or two between junctions: too small to describe
            continue
        A = info[k]["area_km2"]
        P = info[k]["perimeter_km"]
        Lb = max(info[k]["longest_path_km"], 1e-6)
        # streams of this basin: links whose first cell is inside it, by order
        nu, lu = {}, {}
        for li, cl in enumerate(L["cells"], 1):
            own = [c for c in cl if L["id"][c] == li]
            if not m[own[0]]:
                continue
            o = int(order[own[0]])
            nu[o] = nu.get(o, 0) + 1
            lu[o] = lu.get(o, 0.0) + float(s.step[own].sum()) / 1000
        N, Lsum = sum(nu.values()), sum(lu.values())
        orders = sorted(nu)
        rbs = [nu[o] / nu[o + 1] for o in orders[:-1] if nu.get(o + 1)]
        rls = [(lu[o + 1] / nu[o + 1]) / (lu[o] / nu[o]) for o in orders[:-1] if nu.get(o + 1) and lu[o] > 0]
        zb = zf[m]
        zmin, zmax, zmean = float(np.nanmin(zb)), float(np.nanmax(zb)), float(np.nanmean(zb))
        Hk = (zmax - zmin) / 1000
        Dd = Lsum / A if A > 0 else np.nan
        Fs = N / A if A > 0 else np.nan
        r = {"basin": k, "area_km2": A, "perimeter_km": P, "basin_length_km": round(Lb, 3), "max_order": max(orders) if orders else 0,
             "streams": N, "stream_km": round(Lsum, 3), "Rb": round(float(np.mean(rbs)), 3) if rbs else np.nan, "RL": round(float(np.mean(rls)), 3) if rls else np.nan,
             "Ff": round(A / Lb ** 2, 4), "Re": round(2 * np.sqrt(A / np.pi) / Lb, 4), "Rc": round(4 * np.pi * A / P ** 2, 4) if P > 0 else np.nan,
             "Cc": round(0.2821 * P / np.sqrt(A), 4) if A > 0 else np.nan, "Dd": round(Dd, 4), "Fs": round(Fs, 4), "T": round(N / P, 4) if P > 0 else np.nan,
             "Lo": round(1 / (2 * Dd), 4) if Dd and Dd > 0 else np.nan, "C": round(1 / Dd, 4) if Dd and Dd > 0 else np.nan,
             "If": round(Dd * Fs, 4) if np.isfinite(Dd) else np.nan, "relief_m": round(zmax - zmin, 2), "Rh": round(Hk / Lb, 5),
             "Rn": round(Hk * Dd, 4) if np.isfinite(Dd) else np.nan, "HI": round((zmean - zmin) / (zmax - zmin), 4) if zmax > zmin else np.nan,
             "streams_by_order": {str(o): nu[o] for o in orders}}
        rows.append(r)
        # hypsometric curve: share of the area above each relative height
        rel = (zb - zmin) / max(zmax - zmin, 1e-9)
        for h in np.linspace(0, 1, 11):
            curves.append({"basin": k, "relative_height": round(float(h), 2), "relative_area_above": round(float((rel >= h).mean()), 4)})
    if not rows:
        raise ValueError("No watershed to describe")
    # compound value
    if len(rows) >= 2:
        ranks = np.zeros(len(rows))
        for p in LINEAR:
            ranks += _rank(np.array([r[p] for r in rows], float), True)
        for p in SHAPE:
            ranks += _rank(np.array([r[p] for r in rows], float), False)
        cv = ranks / (len(LINEAR) + len(SHAPE))
        q1, q2 = np.percentile(cv, [100 / 3, 200 / 3])
        for r, c in zip(rows, cv):
            r["compound_value"] = round(float(c), 3)
            r["priority"] = "High" if c <= q1 else "Medium" if c <= q2 else "Low"
        order_cv = np.argsort(cv)
        for rank, j in enumerate(order_cv, 1):
            rows[j]["priority_rank"] = rank
    pr = np.zeros((g.h, g.w), "uint8")
    code = {"High": 1, "Medium": 2, "Low": 3}
    lab2 = lab.reshape(g.h, g.w)
    for r in rows:
        if "priority" in r:
            pr[lab2 == r["basin"]] = code[r["priority"]]
    info2 = {r["basin"]: {k: v for k, v in r.items() if k not in ("basin", "streams_by_order")} for r in rows}
    outs = [write_fc(polygons(g, lab2, info2, key="basin"), out_dir / f"{stem}_watersheds.geojson")]
    if pr.any():
        outs.append(write(g, out_dir / f"{stem}_priority.tif", pr, "Priority for conservation (compound value)", dtype="uint8", nodata=0,
                          cmap={k: v[1] for k, v in PRIORITY.items()}, tags={"classes": {str(k): v[0] for k, v in PRIORITY.items()}}))
    cols = ["basin", "priority_rank", "priority", "compound_value", "area_km2", "perimeter_km", "basin_length_km", "max_order", "streams", "stream_km", "Rb", "RL",
            "Dd", "Fs", "T", "Lo", "C", "If", "Ff", "Re", "Rc", "Cc", "relief_m", "Rh", "Rn", "HI"]
    table = out_dir / f"{stem}.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(sorted(rows, key=lambda r: r.get("priority_rank", r["basin"])))
    curve_csv = out_dir / f"{stem}_hypsometric_curves.csv"
    with open(curve_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["basin", "relative_height", "relative_area_above"])
        wr.writeheader()
        wr.writerows(curves)
    progress.update(1.0, "Done")
    clean = lambda v: None if isinstance(v, float) and not np.isfinite(v) else v
    return {"outputs": outs, "csv": str(table), "curves_csv": str(curve_csv), "basins": len(rows),
            "rows": [{k: clean(v) for k, v in r.items() if k != "streams_by_order"} for r in sorted(rows, key=lambda r: r.get("priority_rank", 0))][:30]}
