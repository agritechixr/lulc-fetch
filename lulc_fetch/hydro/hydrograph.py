"""Design flood hydrograph (Analysis ▸ Hydrology ▸ Design flood hydrograph): the flow at a watershed's outlet, hour by hour,
during a design storm (e.g. the 25- or 100-year rain from Rainfall data).

1. The watershed of the outlet point: area, time of concentration tc (Kirpich, from the longest flow path) and the
   area-weighted curve number (from land cover + soil group, or one CN).
2. The storm: its depth spread over 24 hours by the SCS Type II pattern (or evenly over a duration).
3. Excess rain, step by step, from the cumulative SCS-CN equation.
4. The SCS dimensionless unit hydrograph (NRCS NEH-630 ch. 16): lag = 0.6 tc, time to peak Tp = Δt / 2 + lag,
   peak of 1 mm of excess qp = 0.208 A / Tp (m³/s; A km², Tp h), convolved with the excess rain.
Several storms (e.g. 2-, 10-, 100-year) give a hydrograph each, with their peaks, times to peak and volumes."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .. import hydrology as H
from .. import progress
from .common import Surface, open_dem, polygons, read_like, write_fc
from .rainfall import scs_hyetograph
from .watershed import basin_stats, upstream_length

# SCS dimensionless unit hydrograph: t / Tp → q / qp
UH_T = np.array([0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2, 2.2, 2.4, 2.6, 2.8, 3, 3.5, 4, 4.5, 5])
UH_Q = np.array([0, .03, .1, .19, .31, .47, .66, .82, .93, .99, 1, .99, .93, .86, .78, .68, .56, .39, .28, .207, .147, .107, .077, .055, .025, .011, .005, 0])


def excess(rain_steps: np.ndarray, cn: float, lam: float = 0.2) -> np.ndarray:
    S = 25400 / cn - 254 if cn < 100 else 0.0
    P = np.cumsum(rain_steps)
    Ia = lam * S
    Q = np.where(P > Ia, (P - Ia) ** 2 / (P - Ia + S), 0.0)
    return np.diff(np.r_[0, Q])


def unit_hydrograph(area_km2: float, tc_h: float, dt_h: float) -> tuple[np.ndarray, float, float]:
    lag = 0.6 * tc_h
    tp = dt_h / 2 + lag
    qp = 0.208 * area_km2 / tp                 # m³/s per mm of excess
    t = np.arange(0, 5 * tp + dt_h, dt_h)
    return np.interp(t / tp, UH_T, UH_Q) * qp, tp, qp


def run(dem, out_dir: Path, *, point: list[float], storms_mm: list[float], labels: list[str] | None = None, cn: float | None = None,
        landcover: str | None = None, soil: str = "B", condition: str = "II", duration_h: float = 24.0, pattern: str = "scs2",
        dt_h: float = 0.25, lam: float = 0.2, snap_m: float = 150.0, tc_h: float | None = None, name: str = "design_flood") -> dict:
    if not storms_mm:
        raise ValueError("Give at least one storm depth (mm)")
    out_dir = Path(out_dir)
    g, z = open_dem(dem)
    progress.update(0.05, "The watershed")
    s = Surface(g, z)
    site = s.snap([point], snap_m)[0]
    lab = H.label_basins(s.down, s.levels, {site: 1})
    info, paths = basin_stats(g, s, lab, {site: 1}, None, upstream_length(s), {})
    A = info[1]["area_km2"]
    tc = tc_h if tc_h else max(info[1]["time_of_concentration_min"] / 60, dt_h)
    m = lab == 1
    if landcover:
        from .runoff import HSG, amc, cn_table
        table, meta = cn_table(landcover)
        lc = read_like(g, landcover, resampling="nearest").ravel()
        cns = np.full(lc.shape, np.nan)
        for v, c4 in table.items():
            if c4[0] is not None:
                cns[lc == v] = c4[HSG[soil.upper()]]
        w = np.where(g.valid, g.cell_m2, 0).ravel()
        mm = m & np.isfinite(cns)
        if not mm.any():
            raise ValueError("No land-cover class in the watershed has a curve number")
        CN = float(np.clip(amc(np.array([(cns[mm] * w[mm]).sum() / w[mm].sum()]), condition)[0], 1, 100))
    elif cn:
        from .runoff import amc
        CN = float(np.clip(amc(np.array([float(cn)]), condition)[0], 1, 100))
    else:
        raise ValueError("Give the curve number or a land-cover map")
    progress.update(0.6, "Hydrographs")
    uh, tp, qp = unit_hydrograph(A, tc, dt_h)
    labels = labels or [f"{d:g} mm" for d in storms_mm]
    curves, rows = [], []
    for depth, lab_s in zip(storms_mm, labels):
        if pattern == "scs2":
            hrs, steps = scs_hyetograph(depth, dt_h)
        else:
            k = max(1, int(round(duration_h / dt_h)))
            steps = np.full(k, depth / k)
        ex = excess(steps, CN, lam)
        q = np.convolve(ex, uh)
        curves.append((lab_s, steps, ex, q))
        ipk = int(np.argmax(q))
        rows.append({"storm": lab_s, "rain_mm": depth, "runoff_mm": round(float(ex.sum()), 2), "peak_m3s": round(float(q.max()), 3),
                     "time_to_peak_h": round(ipk * dt_h + dt_h, 2), "volume_m3": round(float(q.sum() * dt_h * 3600), 0)})
    n = max(len(c[3]) for c in curves)
    series = out_dir / f"{name}_hydrographs.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(series, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["hour"] + [f"rain_mm {c[0]}" for c in curves] + [f"flow_m3s {c[0]}" for c in curves])
        for t in range(n):
            wr.writerow([round((t + 1) * dt_h, 3)] + [round(float(c[1][t]), 3) if t < len(c[1]) else "" for c in curves]
                        + [round(float(c[3][t]), 4) if t < len(c[3]) else 0 for c in curves])
    outs = [write_fc(polygons(g, lab.reshape(g.h, g.w), {1: {**info[1], "curve_number": round(CN, 1)}}, key="basin"), out_dir / f"{name}_watershed.geojson")]
    progress.update(1.0, "Done")
    return {"outputs": outs, "csv": str(series), "area_km2": A, "tc_h": round(tc, 2), "lag_h": round(0.6 * tc, 2), "tp_h": round(tp, 2),
            "unit_peak_m3s_per_mm": round(qp, 4), "curve_number": round(CN, 1), "storms": rows, "dt_h": dt_h}
