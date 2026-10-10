"""Check dams, ponds and reservoirs (Analysis ▸ Hydrology ▸ Check dams & ponds).

Site finding: along the streams (a range of Strahler orders and contributing areas, gentle bed slope), a dam of a given
height is tried at every stream cell: the water it holds is everything upstream of it, connected to it, below the crest
(the dam's base height + the height); the dam's length is the width of the valley at the crest along the line across
the flow. Sites are ranked by stored volume per metre of dam (the cheapest water), a minimum distance apart.

Area–capacity curve: at a point (snapped to the stream), the flooded area and stored volume for every water level from
the bed up to a height: the elevation–area–capacity table used to size a pond or a reservoir."""

from __future__ import annotations

import csv
from collections import deque
from pathlib import Path

import numpy as np

from .. import hydrology as H
from .. import progress
from .common import Surface, open_dem, polygons, to_lonlat, write_fc

_PERP = {1: ((-1, 0), (1, 0)), 16: ((-1, 0), (1, 0)), 4: ((0, -1), (0, 1)), 64: ((0, -1), (0, 1)),
         2: ((-1, 1), (1, -1)), 32: ((-1, 1), (1, -1)), 8: ((-1, -1), (1, 1)), 128: ((-1, -1), (1, 1))}


def reservoir(s: Surface, site: int, crest: float, up_mask: np.ndarray | None, limit: int = 2_000_000) -> np.ndarray:
    """The cells a dam at `site` with its crest at `crest` m holds water over: connected to the site, below the crest and
    not below the dam's base (the valley downstream of the dam stays dry): an 8-neighbour flood fill."""
    g = s.g
    z = s.filled
    seen = np.zeros((g.h, g.w), bool)
    r0, c0 = divmod(site, g.w)
    base = z[r0, c0]
    q = deque([(r0, c0)])
    seen[r0, c0] = True
    n = 0
    while q:
        r, c = q.popleft()
        n += 1
        if n > limit:
            break
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if (dr or dc) and 0 <= rr < g.h and 0 <= cc < g.w and not seen[rr, cc] and g.valid[rr, cc] and base <= z[rr, cc] < crest \
                        and (up_mask is None or up_mask[rr, cc]):
                    seen[rr, cc] = True
                    q.append((rr, cc))
    return seen


def dam_length(s: Surface, site: int, crest: float) -> float:
    """Length (m) of the dam across the flow at the crest height: from the site both ways until the ground is higher."""
    g = s.g
    r0, c0 = divmod(site, g.w)
    code = int(s.code.ravel()[site])
    if code not in _PERP:
        return float("nan")
    total = 0.0
    for dr, dc in _PERP[code]:
        r, c = r0, c0
        step = float(np.hypot(dr * g.dy, dc * g.dx[r0, 0]))
        while True:
            r, c = r + dr, c + dc
            if not (0 <= r < g.h and 0 <= c < g.w) or not g.valid[r, c]:
                return float("nan")        # the valley side is outside the DEM
            if s.filled[r, c] >= crest:
                break
            total += step
        if total > 5000:
            return float("nan")
    return total + g.cell_m


def sites(dem, out_dir: Path, *, height_m: float = 3.0, stream_km2: float = 0.5, min_order: int = 1, max_order: int = 3,
          max_area_km2: float | None = None, max_slope_pct: float = 5.0, spacing_m: float = 300.0, top: int = 50,
          conditioned: bool = False, name: str | None = None) -> dict:
    out_dir = Path(out_dir)
    stem = name or "dam_sites"
    g, z = open_dem(dem)
    progress.update(0.05, "Flow on the DEM")
    s = Surface(g, z, filled=conditioned)
    stream = s.streams(stream_km2)
    order = H.strahler(s.down, s.levels, stream)
    zf = s.filled.ravel()
    # bed slope over the step downstream
    d = s.down
    bed = np.where(d >= 0, (zf - zf[np.maximum(d, 0)]) / np.maximum(s.step, 1e-6) * 100, np.inf)
    cand = np.flatnonzero(stream & (order >= min_order) & (order <= max_order) & (bed <= max_slope_pct) & (d >= 0))
    if max_area_km2:
        cand = cand[s.area_km2[cand] <= max_area_km2]
    if not cand.size:
        raise ValueError("No stream cell fits (order, slope, area) → widen the limits")
    # try every few cells along the streams (spacing / 3), best first
    stride = max(1, int(spacing_m / g.cell_m / 3))
    cand = cand[np.argsort(-s.area_km2[cand])][::stride][:1500]
    progress.update(0.3, f"Trying {cand.size:,} dam sites")
    rows = []
    for k, i in enumerate(cand):
        crest = zf[i] + height_m
        L = dam_length(s, int(i), crest)
        if not np.isfinite(L) or L <= 0:
            continue
        res = reservoir(s, int(i), crest, None, limit=60_000)
        cells = np.flatnonzero(res.ravel())
        vol = float(((crest - zf[cells]) * g.cell_m2.ravel()[cells]).sum())
        rows.append({"cell": int(i), "volume_m3": vol, "area_ha": float(g.cell_m2.ravel()[cells].sum() / 1e4), "dam_m": L,
                     "m3_per_m": vol / L, "catchment_km2": float(s.area_km2[i]), "order": int(order[i]), "bed_slope_pct": float(bed[i])})
        if k % 200 == 0:
            progress.update(0.3 + 0.6 * k / cand.size, f"Dam site {k:,} of {cand.size:,}")
    if not rows:
        raise ValueError("No site holds water with a dam this high (valleys wider than the DEM?) → try another height")
    rows.sort(key=lambda r: -r["m3_per_m"])
    chosen, taken = [], []
    for r in rows:
        rr, cc = divmod(r["cell"], g.w)
        if all(np.hypot((rr - a) * g.dy, (cc - b) * g.cell_m) >= spacing_m for a, b in taken):
            chosen.append(r)
            taken.append((rr, cc))
        if len(chosen) >= top:
            break
    lab = np.zeros((g.h, g.w), "int32")
    feats = []
    for k, r in enumerate(chosen, 1):
        res = reservoir(s, r["cell"], zf[r["cell"]] + height_m, None, limit=200_000)
        lab[res & (lab == 0)] = k
        lon, lat = to_lonlat(g, [r["cell"] // g.w], [r["cell"] % g.w])
        r.update(rank=k, lon=round(float(lon[0]), 6), lat=round(float(lat[0]), 6))
        feats.append({"type": "Feature", "properties": {"rank": k, "volume_m3": round(r["volume_m3"]), "area_ha": round(r["area_ha"], 2),
                       "dam_length_m": round(r["dam_m"], 1), "m3_per_m_of_dam": round(r["m3_per_m"], 1), "catchment_km2": round(r["catchment_km2"], 3),
                       "stream_order": r["order"], "bed_slope_pct": round(r["bed_slope_pct"], 2), "dam_height_m": height_m},
                      "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]}})
    outs = [write_fc({"features": feats}, out_dir / f"{stem}_sites.geojson")]
    info = {k: {"rank": k, "volume_m3": round(r["volume_m3"])} for k, r in enumerate(chosen, 1)}
    outs.append(write_fc(polygons(g, lab, info, key="site"), out_dir / f"{stem}_reservoirs.geojson"))
    table = out_dir / f"{stem}.csv"
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["rank", "lon", "lat", "volume_m3", "area_ha", "dam_length_m", "m3_per_m_of_dam", "catchment_km2", "stream_order", "bed_slope_pct"])
        for r in chosen:
            wr.writerow([r["rank"], r["lon"], r["lat"], round(r["volume_m3"]), round(r["area_ha"], 2), round(r["dam_m"], 1), round(r["m3_per_m"], 1),
                         round(r["catchment_km2"], 3), r["order"], round(r["bed_slope_pct"], 2)])
    progress.update(1.0, "Done")
    return {"outputs": outs, "csv": str(table), "sites": len(chosen), "tried": int(cand.size),
            "best_volume_m3": round(chosen[0]["volume_m3"]), "total_volume_m3": round(sum(r["volume_m3"] for r in chosen)), "height_m": height_m}


def area_capacity(dem, out_dir: Path, *, point: list[float], max_height_m: float = 10.0, step_m: float = 0.5, snap_m: float = 100.0,
                  conditioned: bool = False, name: str | None = None) -> dict:
    """Elevation – area – capacity at a dam site (snapped to the stream)."""
    out_dir = Path(out_dir)
    stem = name or "area_capacity"
    g, z = open_dem(dem)
    s = Surface(g, z, filled=conditioned)
    site = s.snap([point], snap_m)[0]
    zf = s.filled.ravel()
    base = float(zf[site])
    rows, last = [], None
    levels = np.arange(step_m, max_height_m + 1e-9, step_m)
    for k, h in enumerate(levels):
        res = reservoir(s, site, base + h, None)
        cells = np.flatnonzero(res.ravel())
        area = float(g.cell_m2.ravel()[cells].sum())
        vol = float(((base + h - zf[cells]) * g.cell_m2.ravel()[cells]).sum())
        rows.append({"height_m": round(float(h), 3), "water_level_m": round(base + float(h), 3), "area_ha": round(area / 1e4, 3), "volume_m3": round(vol, 1),
                     "dam_length_m": round(dam_length(s, site, base + h), 1)})
        last = res
        progress.update((k + 1) / len(levels), f"Level {h:g} m")
    table = out_dir / f"{stem}.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    lon, lat = to_lonlat(g, [site // g.w], [site % g.w])
    outs = [write_fc(polygons(g, last.astype("int32"), {1: {"height_m": max_height_m, "volume_m3": rows[-1]["volume_m3"], "area_ha": rows[-1]["area_ha"]}}, key="reservoir"),
                     out_dir / f"{stem}_full.geojson"),
            write_fc({"features": [{"type": "Feature", "properties": {"bed_m": round(base, 2)}, "geometry": {"type": "Point", "coordinates": [float(lon[0]), float(lat[0])]}}]},
                     out_dir / f"{stem}_site.geojson")]
    return {"outputs": outs, "csv": str(table), "bed_m": round(base, 2), "rows": rows, "catchment_km2": round(float(s.area_km2[site]), 3)}
