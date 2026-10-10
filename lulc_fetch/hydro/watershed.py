"""Watershed delineation (Analysis ▸ Hydrology ▸ Watersheds).

- From outlet points: each point is snapped to the largest flow within a distance (pour-point snapping), and gets the
  area that drains to it. Points upstream of another give nested watersheds: each basin is the area between its outlet
  and the outlets above it, with its parent (the basin it drains into): the basin hierarchy.
- Sub-watersheds: the area draining into each stream link (between junctions), optionally only inside the watershed of
  a point; each knows the sub-basin downstream and its stream order.
- Every basin: each outlet at the DEM's edge with at least a minimum area.

Boundaries as polygons with, per basin: area, perimeter, compactness (Gravelius), mean and range of height (relief),
mean slope, stream length and drainage density, the longest flow path and the outlet; plus a raster of basin ids."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .. import hydrology as H
from .. import progress
from .common import Grid, Surface, line, open_dem, polygons, to_lonlat, write, write_fc
from .network import links


def upstream_length(s: Surface) -> np.ndarray:
    """The longest flow path above each cell (m), from the divide down to it."""
    up = np.zeros(s.down.size)
    for lv in s.levels:
        d = s.down[lv]
        m = d >= 0
        np.maximum.at(up, d[m], up[lv[m]] + s.step[lv[m]])
    return up


def longest_path(s: Surface, up: np.ndarray, outlet: int, lab: np.ndarray, k: int) -> list[int]:
    """Back from the outlet up the branch with the longest upstream length (within basin k)."""
    g = s.g
    path = [outlet]
    seen = {outlet}
    while True:
        i = path[-1]
        r, c = divmod(i, g.w)
        best, nxt = -1.0, -1
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if (dr or dc) and 0 <= rr < g.h and 0 <= cc < g.w:
                    j = rr * g.w + cc
                    if s.down[j] == i and lab[j] == k and j not in seen and up[j] + s.step[j] > best:
                        best, nxt = up[j] + s.step[j], j
        if nxt < 0:
            return path[::-1]
        path.append(nxt)
        seen.add(nxt)


def basin_stats(g: Grid, s: Surface, lab: np.ndarray, outlets: dict[int, int], stream: np.ndarray | None, up: np.ndarray,
                parents: dict[int, int | None]) -> tuple[dict[int, dict], list[dict]]:
    """Per basin: area, perimeter, compactness, heights, slope, streams, longest flow path (and its line)."""
    lab2 = lab.reshape(g.h, g.w)
    n = int(lab.max())
    cell = np.where(g.valid, g.cell_m2, 0).ravel()
    area = np.bincount(lab, weights=cell, minlength=n + 1)
    zf = s.filled.ravel()
    zsum = np.bincount(lab, weights=np.nan_to_num(zf), minlength=n + 1)
    cnt = np.bincount(lab, minlength=n + 1)
    gy, gx = np.gradient(s.filled)
    slope = np.degrees(np.arctan(np.hypot(gx / g.dx, gy / g.dy))).ravel()
    ssum = np.bincount(lab, weights=np.nan_to_num(slope), minlength=n + 1)
    zmax = np.full(n + 1, -np.inf); zmin = np.full(n + 1, np.inf)
    np.maximum.at(zmax, lab, np.nan_to_num(zf, nan=-np.inf)); np.minimum.at(zmin, lab, np.nan_to_num(zf, nan=np.inf))
    # perimeter: cell edges between different labels
    per = np.zeros(n + 1)
    a, b = lab2[:, :-1], lab2[:, 1:]
    e = a != b
    np.add.at(per, a[e], g.dy); np.add.at(per, b[e], g.dy)
    a, b = lab2[:-1], lab2[1:]
    e = a != b
    dxr = np.broadcast_to(g.dx[:-1], a.shape)
    np.add.at(per, a[e], dxr[e]); np.add.at(per, b[e], dxr[e])
    for edge in (lab2[0], lab2[-1]):
        np.add.at(per, edge, float(np.mean(g.dx)))
    np.add.at(per, lab2[:, 0], g.dy); np.add.at(per, lab2[:, -1], g.dy)
    slen = np.bincount(lab, weights=np.where(stream, s.step, 0), minlength=n + 1) if stream is not None else np.zeros(n + 1)
    info, paths = {}, []
    for i, k in outlets.items():
        if area[k] <= 0:
            continue
        lon, lat = to_lonlat(g, [i // g.w], [i % g.w])
        A = area[k] / 1e6
        P = per[k] / 1000
        lp = longest_path(s, up, i, lab, k)
        lp_len = float(s.step[lp[:-1]].sum()) if len(lp) > 1 else 0.0     # inside this basin (nested ones stop at the outlets above)
        drop = float(zf[lp[0]] - zf[i]) if len(lp) > 1 else 0.0
        tc = 0.0195 * lp_len ** 0.77 * max(drop / max(lp_len, 1), 1e-4) ** -0.385 if lp_len > 0 else 0.0   # Kirpich (minutes)
        info[k] = {"area_km2": round(A, 4), "perimeter_km": round(P, 3), "compactness": round(0.28 * P / np.sqrt(A), 3) if A > 0 else None,
                   "mean_elev_m": round(zsum[k] / max(cnt[k], 1), 2), "relief_m": round(float(zmax[k] - zmin[k]), 2),
                   "mean_slope_deg": round(ssum[k] / max(cnt[k], 1), 2), "stream_km": round(slen[k] / 1000, 3),
                   "drainage_density": round(slen[k] / 1000 / A, 4) if A > 0 else None, "longest_path_km": round(lp_len / 1000, 3),
                   "time_of_concentration_min": round(tc, 1), "outlet_lon": round(float(lon[0]), 6), "outlet_lat": round(float(lat[0]), 6),
                   "parent": parents.get(k)}
        if len(lp) >= 2:
            paths.append({"type": "Feature", "properties": {"basin": k, "length_km": round(lp_len / 1000, 3), "drop_m": round(drop, 2)},
                          "geometry": {"type": "LineString", "coordinates": line(g, lp)}})
    return info, paths


def delineate(s: Surface, *, mode: str, points=None, snap_m: float = 150.0, stream_km2: float = 1.0, min_basin_km2: float | None = None,
              within_points: bool = False):
    """(labels, outlets {cell: id}, parents {id: parent id}, stream mask, snapped cells) for a mode (see run)."""
    if mode not in ("points", "subbasins", "all"):
        raise ValueError("mode: points, subbasins or all")
    g = s.g
    stream = (s.area_km2 >= stream_km2) & g.valid.ravel()
    parents: dict[int, int | None] = {}
    snapped = []
    if mode == "points" or (mode == "subbasins" and within_points):
        if not points:
            raise ValueError("Give at least one outlet point")
        snapped = s.snap(points, snap_m)
    if mode == "points":
        outlets = {}
        for k, i in enumerate(snapped, 1):
            outlets.setdefault(i, k)                       # two points on one cell: one basin
        lab = H.label_basins(s.down, s.levels, outlets)
        for i, k in outlets.items():                       # the basin each outlet drains into
            d = s.down[i]
            parents[k] = int(lab[d]) if d >= 0 and lab[d] else None
    elif mode == "subbasins":
        if not stream.any():
            raise ValueError(f"No cell drains {stream_km2:g} km² → lower the stream threshold")
        L = links(g, s, stream)
        lab = H.label_basins(s.down, s.levels, {int(i): int(L["id"][i]) for i in np.flatnonzero(stream)})
        start_of = {int(c[0]): k + 1 for k, c in enumerate(L["cells"])}
        outlets = {}
        for k, cl in enumerate(L["cells"], 1):
            own = [c for c in cl if L["id"][c] == k]
            outlets[own[-1]] = k
            parents[k] = start_of.get(cl[-1]) if len(cl) > len(own) else None
        if within_points:
            # only inside the points' watersheds; a link cut by a point ends at its lowest cell inside
            keep = H.label_basins(s.down, s.levels, {i: 1 for i in snapped}) > 0
            lab = np.where(keep, lab, 0)
            idx = np.flatnonzero(lab > 0)
            idx = idx[np.lexsort((s.area_km2[idx], lab[idx]))]          # by label, then contributing area
            last = np.r_[lab[idx][1:] != lab[idx][:-1], True]
            outlets = {int(i): int(lab[i]) for i in idx[last]}
            parents = {k: (p if p in outlets.values() else None) for k, p in parents.items() if k in outlets.values()}
    else:
        mk = min_basin_km2 if min_basin_km2 is not None else 10 * stream_km2
        ends = np.flatnonzero(g.valid.ravel() & (s.down < 0) & (s.area_km2 >= mk))
        ends = ends[np.argsort(-s.area_km2[ends])]
        if not ends.size:
            raise ValueError(f"No basin of {mk:g} km² or more → lower the minimum size")
        outlets = {int(i): k for k, i in enumerate(ends, 1)}
        lab = H.label_basins(s.down, s.levels, outlets)
    if not outlets:
        raise ValueError("No watershed found")
    return lab, outlets, parents, stream, snapped


def run(dem, out_dir: Path, *, mode: str = "points", points: list[list[float]] | None = None, snap_m: float = 150.0,
        stream_km2: float = 1.0, min_basin_km2: float | None = None, within_points: bool = False, conditioned: bool = False,
        name: str | None = None) -> dict:
    """mode 'points' (watersheds of the points, nested), 'subbasins' (one per stream link; within_points: only inside
    the points' watersheds) or 'all' (every basin reaching the DEM's edge)."""
    out_dir = Path(out_dir)
    stem = name or Path(dem).stem
    g, z = open_dem(dem)
    progress.update(0.05, "Flow on the DEM")
    s = Surface(g, z, filled=conditioned)
    progress.update(0.55, "Watersheds")
    lab, outlets, parents, stream, snapped = delineate(s, mode=mode, points=points, snap_m=snap_m, stream_km2=stream_km2,
                                                       min_basin_km2=min_basin_km2, within_points=within_points)
    progress.update(0.7, "Basin statistics")
    up = upstream_length(s)
    info, paths = basin_stats(g, s, lab, outlets, stream, up, parents)
    if mode == "subbasins":
        order = H.strahler(s.down, s.levels, stream)
        for i, k in outlets.items():
            if k in info:
                info[k]["strahler"] = int(order[i])
    progress.update(0.85, "Boundaries")
    fc = polygons(g, lab.reshape(g.h, g.w), info, key="basin")
    outs = [write_fc(fc, out_dir / f"{stem}_{mode}_watersheds.geojson"),
            write(g, out_dir / f"{stem}_{mode}_basin_id.tif", lab, "Basin id (0: none)", dtype="int32", nodata=0)]
    if paths:
        outs.append(write_fc({"features": paths}, out_dir / f"{stem}_{mode}_longest_flow_paths.geojson"))
    if snapped:
        lon, lat = to_lonlat(g, [i // g.w for i in snapped], [i % g.w for i in snapped])
        outs.append(write_fc({"features": [{"type": "Feature", "properties": {"point": k + 1, "area_km2": round(float(s.area_km2[i]), 4)},
                                            "geometry": {"type": "Point", "coordinates": [round(float(x), 7), round(float(y), 7)]}}
                                           for k, (i, x, y) in enumerate(zip(snapped, lon, lat))]}, out_dir / f"{stem}_snapped_outlets.geojson"))
    table = out_dir / f"{stem}_{mode}_watersheds.csv"
    cols = ["basin", "parent", "area_km2", "perimeter_km", "compactness", "mean_elev_m", "relief_m", "mean_slope_deg", "stream_km",
            "drainage_density", "longest_path_km", "time_of_concentration_min", "outlet_lon", "outlet_lat"] + (["strahler"] if mode == "subbasins" else [])
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for k in sorted(info):
            wr.writerow({"basin": k, **info[k]})
    progress.update(1.0, "Done")
    return {"outputs": outs, "csv": str(table), "basins": len(info), "mode": mode,
            "total_area_km2": round(sum(v["area_km2"] for v in info.values()), 4),
            "largest_km2": round(max(v["area_km2"] for v in info.values()), 4), "nested": sum(1 for v in info.values() if v.get("parent"))}
