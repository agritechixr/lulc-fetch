"""2D flood simulation (Analysis ▸ Hydrology ▸ Flood simulation 2D): water spreading over a DEM through time, from river
inflows (a flow in m³/s, steady or a hydrograph) and / or rain on the grid.

The local inertial form of the shallow-water equations (Bates, Horritt & Fewtrell 2010; de Almeida et al. 2012), as in
LISFLOOD-FP: between every two cells the flow is updated from the water-surface slope with Manning friction, then each
cell's depth from what flows in and out. Time steps follow the Courant condition (α = 0.7). Water leaves at the DEM's
edges with the local bed slope. Manning's n: one value, or from land cover.

Outputs: maximum depth, maximum speed, the time water first arrives (hours, deeper than 5 cm), the depth at regular times,
and a water balance (inflow, rain, stored, out of the edges) through the run. A screening model: no buildings,
culverts, bridges or infiltration, and the DEM's pixel sets the detail (resample a fine DEM for big areas)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from .. import progress
from .common import from_lonlat, open_dem, read_like, write

G = 9.81
N_COVER = {"tree": 0.10, "forest": 0.10, "mangrove": 0.12, "shrub": 0.07, "grass": 0.035, "rangeland": 0.04, "crop": 0.04, "built": 0.08,
           "urban": 0.08, "bare": 0.025, "snow": 0.02, "water": 0.03, "wetland": 0.06, "flooded": 0.06, "moss": 0.03}
MAX_CELLS = 1_500_000


def _series(spec, hours: float) -> callable:
    """A steady value or [[hour, value], …] as a function of time (s)."""
    if spec is None:
        return lambda t: 0.0
    if isinstance(spec, (int, float)):
        return lambda t: float(spec)
    arr = np.array(spec, float)
    return lambda t: float(np.interp(t / 3600, arr[:, 0], arr[:, 1], left=arr[0, 1], right=0.0))


def simulate(dem, out_dir: Path, *, inflows: list[dict] | None = None, rain_mm_h=None, hours: float = 6.0, manning: float = 0.035,
             landcover: str | None = None, start_depth: str | None = None, snapshots: int = 6, alpha: float = 0.7, name: str = "flood_sim") -> dict:
    """inflows: [{lon, lat, q: m³/s or [[h, m³/s], …]}]; rain_mm_h: mm/h or [[h, mm/h], …] on every cell."""
    if not inflows and not rain_mm_h:
        raise ValueError("Give an inflow (m³/s at a point) or rain on the grid")
    if not 0 < hours <= 240:
        raise ValueError("Simulate between a few minutes and 240 hours")
    out_dir = Path(out_dir)
    g, z = open_dem(dem)
    if g.h * g.w > MAX_CELLS:
        raise ValueError(f"The DEM has {g.h * g.w:,} cells: the simulation handles up to {MAX_CELLS:,} → clip it or make the pixels bigger (Resample)")
    valid = g.valid
    zb = np.where(valid, z, np.nanmax(z) + 1000.0)          # outside the DEM: a wall
    dx = np.broadcast_to(g.dx, (g.h, g.w)).astype(float)     # cell width (m) per row
    dy = g.dy
    area = dx * dy
    if landcover:
        import rasterio

        from .runoff import SCHEMES, _cover_of
        lc = read_like(g, landcover, resampling="nearest")
        with rasterio.open(landcover) as s:
            try:
                names = {int(k): v for k, v in json.loads(s.tags().get("classes", "{}")).items()}
            except ValueError:
                names = {}
        names = names or SCHEMES["worldcover"]
        n = np.full((g.h, g.w), manning)
        for v, nm in names.items():
            c = _cover_of(nm) or nm
            if c in N_COVER:
                n[lc == v] = N_COVER[c]
    else:
        n = np.full((g.h, g.w), float(manning))
    h = np.zeros((g.h, g.w))
    if start_depth:
        h = np.nan_to_num(np.maximum(read_like(g, start_depth), 0)) * valid
    src = []
    for k, f in enumerate(inflows or [], 1):
        r, c = from_lonlat(g, [[f["lon"], f["lat"]]])
        r, c = int(r[0]), int(c[0])
        if not (0 <= r < g.h and 0 <= c < g.w and valid[r, c]):
            raise ValueError(f"Inflow {k} is outside the DEM")
        src.append((r, c, _series(f["q"], hours)))
    rain = _series(rain_mm_h, hours)
    qx = np.zeros((g.h, g.w - 1))         # flux per metre of face width (m²/s) between (r, c) and (r, c+1)
    qy = np.zeros((g.h - 1, g.w))         # between (r, c) and (r+1, c)
    nx = 0.5 * (n[:, :-1] + n[:, 1:])
    ny = 0.5 * (n[:-1, :] + n[1:, :])
    dxf = 0.5 * (dx[:, :-1] + dx[:, 1:])
    hmax = np.zeros_like(h)
    vmax = np.zeros_like(h)
    arrival = np.full(h.shape, np.nan)
    T = hours * 3600.0
    snap_t = list(np.linspace(T / snapshots, T, snapshots)) if snapshots else []
    snaps, balance = [], []
    t, steps = 0.0, 0
    vin = vrain = vout = 0.0
    next_bal = 0.0
    dxmin = float(min(dx.min(), dy))
    n_edge = float(np.median(n))
    bed = {k: np.maximum(v, 1e-4) for k, v in {
        "l": (zb[:, 1] - zb[:, 0]) / dx[:, 0], "r": (zb[:, -2] - zb[:, -1]) / dx[:, -1],
        "t": (zb[1, :] - zb[0, :]) / dy, "b": (zb[-2, :] - zb[-1, :]) / dy}.items()}
    umax = 0.0
    while t < T - 1e-6:
        eta = zb + h
        hm = float(h.max())
        dt = alpha * dxmin / (math.sqrt(G * max(hm, 0.01)) + umax)          # Courant, with the flow speed
        dt = min(dt, 30.0, T - t)
        if snap_t and t + dt > snap_t[0]:
            dt = max(snap_t[0] - t, 1e-3)
        # momentum at the faces: water-surface slope against Manning friction (semi-implicit), Froude ≤ 1
        hfx = np.maximum(eta[:, :-1], eta[:, 1:]) - np.maximum(zb[:, :-1], zb[:, 1:])
        wx = hfx > 1e-3
        hw = np.where(wx, hfx, 1.0)
        qx = np.where(wx, (qx - G * hw * dt * (eta[:, 1:] - eta[:, :-1]) / dxf) / (1 + G * dt * nx ** 2 * np.abs(qx) / hw ** (7 / 3)), 0.0)
        cap = hw * np.sqrt(G * hw)
        qx = np.clip(qx, -cap, cap) * wx
        hfy = np.maximum(eta[:-1, :], eta[1:, :]) - np.maximum(zb[:-1, :], zb[1:, :])
        wy = hfy > 1e-3
        hw = np.where(wy, hfy, 1.0)
        qy = np.where(wy, (qy - G * hw * dt * (eta[1:, :] - eta[:-1, :]) / dy) / (1 + G * dt * ny ** 2 * np.abs(qy) / hw ** (7 / 3)), 0.0)
        cap = hw * np.sqrt(G * hw)
        qy = np.clip(qy, -cap, cap) * wy
        # out of the DEM's edges: normal depth on the local bed slope
        ql = np.where(h[:, 0] > 1e-3, h[:, 0] ** (5 / 3) * np.sqrt(bed["l"]) / n_edge, 0.0) * valid[:, 0]
        qr = np.where(h[:, -1] > 1e-3, h[:, -1] ** (5 / 3) * np.sqrt(bed["r"]) / n_edge, 0.0) * valid[:, -1]
        qt = np.where(h[0, :] > 1e-3, h[0, :] ** (5 / 3) * np.sqrt(bed["t"]) / n_edge, 0.0) * valid[0, :]
        qb = np.where(h[-1, :] > 1e-3, h[-1, :] ** (5 / 3) * np.sqrt(bed["b"]) / n_edge, 0.0) * valid[-1, :]
        # no cell gives more water than it has: scale each donor's outflows (keeps the mass exact)
        vx, vy = qx * dy * dt, qy * dx[:-1, :] * dt
        out = np.zeros_like(h)
        out[:, :-1] += np.maximum(vx, 0); out[:, 1:] += np.maximum(-vx, 0)
        out[:-1, :] += np.maximum(vy, 0); out[1:, :] += np.maximum(-vy, 0)
        out[:, 0] += ql * dy * dt; out[:, -1] += qr * dy * dt; out[0, :] += qt * dx[0, :] * dt; out[-1, :] += qb * dx[-1, :] * dt
        have = h * area
        k = np.where(out > have, have / np.maximum(out, 1e-12), 1.0)
        vx = np.where(vx > 0, vx * k[:, :-1], vx * k[:, 1:])
        vy = np.where(vy > 0, vy * k[:-1, :], vy * k[1:, :])
        qx = vx / (dy * dt)
        qy = vy / (dx[:-1, :] * dt)
        el, er, et, eb = ql * dy * dt * k[:, 0], qr * dy * dt * k[:, -1], qt * dx[0, :] * dt * k[0, :], qb * dx[-1, :] * dt * k[-1, :]
        dv = np.zeros_like(h)
        dv[:, :-1] -= vx; dv[:, 1:] += vx
        dv[:-1, :] -= vy; dv[1:, :] += vy
        dv[:, 0] -= el; dv[:, -1] -= er; dv[0, :] -= et; dv[-1, :] -= eb
        out_v = float(el.sum() + er.sum() + et.sum() + eb.sum())
        add_rain = rain(t) / 1000 / 3600 * dt * area * valid
        dv += add_rain
        vrain += float(add_rain.sum())
        for r, c, qf in src:
            v = qf(t) * dt
            dv[r, c] += v
            vin += v
        h = np.maximum(h + dv / area, 0.0) * valid
        vout += out_v
        t += dt
        steps += 1
        ux = np.where(hfx > 0.05, np.abs(qx) / np.maximum(hfx, 0.05), 0)
        uy = np.where(hfy > 0.05, np.abs(qy) / np.maximum(hfy, 0.05), 0)
        umax = float(max(ux.max(initial=0), uy.max(initial=0)))
        # records
        hmax = np.maximum(hmax, h)
        uc = np.zeros_like(h)                                            # the fastest face of each cell
        uc[:, :-1] = np.maximum(uc[:, :-1], ux); uc[:, 1:] = np.maximum(uc[:, 1:], ux)
        uc[:-1, :] = np.maximum(uc[:-1, :], uy); uc[1:, :] = np.maximum(uc[1:, :], uy)
        vmax = np.maximum(vmax, uc)
        newly = np.isnan(arrival) & (h > 0.05)
        arrival[newly] = t / 3600
        if snap_t and t >= snap_t[0] - 1e-6:
            snaps.append((snap_t.pop(0) / 3600, h.copy()))
        if t >= next_bal:
            balance.append({"hour": round(t / 3600, 3), "inflow_m3": round(vin, 1), "rain_m3": round(vrain, 1), "stored_m3": round(float((h * area).sum()), 1),
                            "out_m3": round(vout, 1), "flooded_km2": round(float(area[h > 0.05].sum() / 1e6), 4), "max_depth_m": round(float(h.max()), 3)})
            next_bal += T / 100
            progress.update(min(0.95, t / T), f"Simulated {t / 3600:.2f} of {hours:g} h ({steps:,} steps)")
    outs = [write(g, out_dir / f"{name}_max_depth.tif", np.where(hmax > 0.01, hmax, np.nan), "Maximum water depth (m)"),
            write(g, out_dir / f"{name}_max_speed.tif", np.where(hmax > 0.01, vmax, np.nan), "Maximum flow speed (m/s)"),
            write(g, out_dir / f"{name}_arrival.tif", arrival, "Time water arrives, deeper than 5 cm (hours)")]
    if snaps:
        outs.append(write(g, out_dir / f"{name}_depth_over_time.tif", np.stack([np.where(s > 0.01, s, np.nan) for _, s in snaps]),
                          [f"Depth (m) at {hh:g} h" for hh, _ in snaps]))
    table = out_dir / f"{name}_water_balance.csv"
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(balance[0]))
        wr.writeheader()
        wr.writerows(balance)
    stored = float((h * area).sum())
    err = (vin + vrain - stored - vout) / max(vin + vrain, 1e-9)
    progress.update(1.0, "Done")
    return {"outputs": outs, "csv": str(table), "steps": steps, "hours": hours, "inflow_m3": round(vin, 1), "rain_m3": round(vrain, 1),
            "stored_m3": round(stored, 1), "out_m3": round(vout, 1), "mass_error_pct": round(100 * err, 3),
            "max_depth_m": round(float(hmax.max()), 3), "max_speed_ms": round(float(vmax.max()), 3),
            "flooded_km2": round(float(area[hmax > 0.05].sum() / 1e6), 4), "cells": int(valid.sum())}
