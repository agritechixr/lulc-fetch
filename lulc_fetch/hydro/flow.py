"""Flow direction and accumulation (Analysis ▸ Hydrology ▸ Flow direction & accumulation) with three ways of routing:

- D8 (O'Callaghan & Mark 1984): all water to the steepest of the 8 neighbours. Streams and watersheds use it.
- D-infinity (Tarboton 1997): the steepest direction on 8 triangular facets, as an angle; water split between the two
  neighbours on either side of it. Less grid-shaped on hillslopes.
- MFD (Freeman 1991, exponent 1.1; Quinn et al. 1991 contour lengths): water shared among every lower neighbour by
  slope. Best for wetness indices and diffuse hillslope flow.

Accumulation counts cells, contributing area (km²) and the specific catchment area (m² per metre of contour width,
used by TWI and SPI). All on the ε-filled DEM so that every cell drains."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .. import hydrology as H
from .. import progress
from .common import Grid, Surface, open_dem, write

N8 = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]   # E SE S SW W NW N NE
METHODS = {"d8": "D8", "dinf": "D-infinity", "mfd": "Multiple flow direction (MFD)"}


def _neighbour(eps: np.ndarray, dr: int, dc: int) -> np.ndarray:
    h, w = eps.shape
    pad = np.pad(eps, 1, constant_values=np.nan)
    return pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]


def receivers_mfd(eps: np.ndarray, g: Grid, p: float = 1.1) -> tuple[np.ndarray, np.ndarray]:
    """(R, W): for each cell its lower neighbours (flat indices, -1 none) and the share of water each gets."""
    h, w = eps.shape
    rows, cols = np.indices((h, w))
    R = np.full((h * w, 8), -1, "int64")
    Wt = np.zeros((h * w, 8))
    for k, (dr, dc) in enumerate(N8):
        nb = _neighbour(eps, dr, dc)
        dist = np.hypot(dr * g.dy, dc * g.dx)
        tan = (eps - nb) / dist
        L = 0.5 if (dr == 0 or dc == 0) else 0.354          # contour length per cell size (Quinn et al.)
        ok = np.isfinite(tan) & (tan > 0)
        Wt[:, k] = np.where(ok, (np.where(ok, tan, 0) * L) ** p, 0).ravel()
        R[:, k] = np.where(ok, (rows + dr) * w + (cols + dc), -1).ravel()
    s = Wt.sum(1, keepdims=True)
    Wt = np.divide(Wt, s, out=np.zeros_like(Wt), where=s > 0)
    return R, Wt


# Tarboton's 8 facets: (cardinal neighbour e1, diagonal neighbour e2), and the angle factors ac, af
_FACETS = [((0, 1), (-1, 1), 0, 1), ((-1, 0), (-1, 1), 1, -1), ((-1, 0), (-1, -1), 1, 1), ((0, -1), (-1, -1), 2, -1),
           ((0, -1), (1, -1), 2, 1), ((1, 0), (1, -1), 3, -1), ((1, 0), (1, 1), 3, 1), ((0, 1), (1, 1), 4, -1)]


def receivers_dinf(eps: np.ndarray, g: Grid) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(R, W, angle): the two neighbours that share each cell's water, their shares, and the flow angle (radians,
    counter-clockwise from east; NaN where water leaves the DEM)."""
    h, w = eps.shape
    rows, cols = np.indices((h, w))
    dx = np.broadcast_to(g.dx, (h, w))
    best = np.full((h, w), 0.0)
    ang = np.full((h, w), np.nan)
    R = np.full((h * w, 2), -1, "int64")
    Wt = np.zeros((h * w, 2))
    r1 = np.zeros((h, w), "int64"); c1 = np.zeros((h, w), "int64"); r2 = np.zeros((h, w), "int64"); c2 = np.zeros((h, w), "int64")
    frac2 = np.zeros((h, w))
    for (a1, a2, ac, af) in _FACETS:
        e1, e2 = _neighbour(eps, *a1), _neighbour(eps, *a2)
        d1 = np.where(a1[0] == 0, dx, g.dy)                  # along the cardinal step
        d2 = np.where(a1[0] == 0, g.dy, dx)                  # across, to the diagonal
        s1 = (eps - e1) / d1
        s2 = (e1 - e2) / d2
        r = np.arctan2(s2, s1)
        rmax = np.arctan2(d2, d1)
        s = np.sqrt(s1 * s1 + s2 * s2)
        lo, hi = r < 0, r > rmax
        r = np.where(lo, 0, np.where(hi, rmax, r))
        s = np.where(lo, s1, np.where(hi, (eps - e2) / np.hypot(d1, d2), s))
        s = np.where(np.isfinite(s), s, -np.inf)
        better = s > best
        best = np.where(better, s, best)
        ang = np.where(better, af * r + ac * math.pi / 2, ang)
        f2 = r / rmax
        frac2 = np.where(better, f2, frac2)
        r1 = np.where(better, rows + a1[0], r1); c1 = np.where(better, cols + a1[1], c1)
        r2 = np.where(better, rows + a2[0], r2); c2 = np.where(better, cols + a2[1], c2)
    has = np.isfinite(ang)
    R[:, 0] = np.where(has & (frac2 < 1), r1 * w + c1, -1).ravel()
    R[:, 1] = np.where(has & (frac2 > 0), r2 * w + c2, -1).ravel()
    Wt[:, 0] = np.where(has, 1 - frac2, 0).ravel()
    Wt[:, 1] = np.where(has, frac2, 0).ravel()
    return R, Wt, np.where(has, np.mod(ang, 2 * math.pi), np.nan)


def accumulate_multi(R: np.ndarray, Wt: np.ndarray, weight: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Accumulation when a cell sends water to several cells: level by level in flow order (vectorised)."""
    n = weight.size
    use = (R >= 0) & (Wt > 0)
    indeg = np.zeros(n, "int64")
    for k in range(R.shape[1]):
        np.add.at(indeg, R[use[:, k], k], 1)
    acc = weight.astype("float64").copy()
    frontier = np.flatnonzero(valid & (indeg == 0))
    it = 0
    while frontier.size:
        touched = []
        for k in range(R.shape[1]):
            m = use[frontier, k]
            src = frontier[m]
            dst = R[src, k]
            np.add.at(acc, dst, acc[src] * Wt[src, k])
            np.subtract.at(indeg, dst, 1)
            touched.append(dst)
        cand = np.unique(np.concatenate(touched)) if touched else np.empty(0, "int64")
        frontier = cand[indeg[cand] == 0]
        it += 1
    return acc


def run(dem, out_dir: Path, *, method: str = "d8", outputs=("direction", "accumulation", "area", "sca"), mfd_p: float = 1.1,
        conditioned: bool = False, name: str | None = None) -> dict:
    if method not in METHODS:
        raise ValueError(f"method: {', '.join(METHODS)}")
    out_dir = Path(out_dir)
    stem = name or Path(dem).stem
    g, z = open_dem(dem)
    progress.update(0.05, "Filling sinks" if not conditioned else "Preparing the conditioned DEM")
    s = Surface(g, z, filled=conditioned)
    valid = g.valid.ravel()
    cell = np.where(g.valid, g.cell_m2, 0).ravel()
    progress.update(0.5, f"{METHODS[method]} flow")
    angle = None
    if method == "d8":
        cells = H.accumulate(s.down, s.levels, valid.astype(float))
        area = s.area_km2 * 1e6
    else:
        if method == "dinf":
            R, Wt, angle = receivers_dinf(s.eps, g)
        else:
            R, Wt = receivers_mfd(s.eps, g, mfd_p)
        progress.update(0.65, "Accumulating")
        cells = accumulate_multi(R, Wt, valid.astype(float), valid)
        area = accumulate_multi(R, Wt, cell, valid)
    width = np.sqrt(cell)                                   # contour width ≈ the cell size
    sca = np.divide(area, width, out=np.zeros_like(area), where=width > 0)
    outs = []
    if "direction" in outputs:
        if method == "d8":
            outs.append(write(g, out_dir / f"{stem}_flowdir_d8.tif", s.code, "Flow direction D8 (1 E, 2 SE, 4 S, 8 SW, 16 W, 32 NW, 64 N, 128 NE; 0 leaves the DEM)", dtype="uint8", nodata=255))
        elif method == "dinf":
            outs.append(write(g, out_dir / f"{stem}_flowdir_dinf.tif", np.degrees(angle), "Flow direction D-infinity (degrees counter-clockwise from east)"))
    if "accumulation" in outputs:
        outs.append(write(g, out_dir / f"{stem}_flowacc_{method}.tif", cells, f"Flow accumulation {METHODS[method]} (cells)"))
    if "area" in outputs:
        outs.append(write(g, out_dir / f"{stem}_contrib_area_{method}.tif", area / 1e6, f"Contributing area {METHODS[method]} (km²)"))
    if "sca" in outputs:
        outs.append(write(g, out_dir / f"{stem}_sca_{method}.tif", sca, f"Specific catchment area {METHODS[method]} (m²/m)"))
    progress.update(1.0, "Done")
    return {"outputs": outs, "method": METHODS[method], "largest_area_km2": round(float(area.max() / 1e6), 4), "cells": int(valid.sum()),
            "sink_cells_filled": int(((s.filled - z) > 1e-9)[g.valid].sum())}
