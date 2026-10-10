"""Drainage network (Analysis ▸ Hydrology ▸ Drainage network): streams where the contributing area passes a threshold
(D8), cut into links between sources, junctions and outlets.

Per link: an id, the Strahler order (1 at the sources; two of order n meeting make n + 1) and the Shreve magnitude (the
number of sources upstream), length, drop and slope, contributing area, and the topology (from node, to node, the link
downstream). Nodes: sources, junctions (with how many links meet) and outlets. Drainage density = stream length per
area (km / km²) for the whole DEM. Rasters of order, magnitude and link ids; a summary table of the orders (count,
total and mean length: Horton's ratios)."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .. import hydrology as H
from .. import progress
from .common import Grid, Surface, line, open_dem, write, write_fc


def shreve(down: np.ndarray, levels: list[np.ndarray], stream: np.ndarray) -> np.ndarray:
    mag = np.zeros(down.size, "int64")
    for lv in levels:
        s = lv[stream[lv]]
        if not s.size:
            continue
        mag[s] = np.where(mag[s] == 0, 1, mag[s])          # nothing flowed in yet: a source
        d = down[s]
        m = (d >= 0) & stream[np.maximum(d, 0)]
        np.add.at(mag, d[m], mag[s[m]])
    return mag


def links(g: Grid, s: Surface, stream: np.ndarray) -> dict:
    """Stream links: id per stream cell, the cells of each link (head to end), and the nodes."""
    down = s.down
    idx = np.flatnonzero(stream)
    d = down[idx]
    inflow = np.bincount(d[(d >= 0) & stream[np.maximum(d, 0)]], minlength=down.size)
    starts = idx[inflow[idx] != 1]                         # sources (0 inflows) and junctions (2+)
    link_id = np.zeros(down.size, "int64")
    cells: list[list[int]] = []
    for i in starts:
        path = [int(i)]
        link_id[i] = len(cells) + 1
        j = down[i]
        while j >= 0 and stream[j] and inflow[j] == 1:
            path.append(int(j))
            link_id[j] = len(cells) + 1
            j = down[j]
        end_next = int(j) if j >= 0 and stream[j] else -1      # the junction it flows into, or -1 (it leaves the DEM / stream)
        cells.append(path + ([end_next] if end_next >= 0 else []))
    return {"id": link_id, "cells": cells, "inflow": inflow, "starts": starts}


def run(dem, out_dir: Path, *, stream_km2: float = 1.0, outputs=("lines", "nodes", "order", "magnitude", "links"),
        conditioned: bool = False, name: str | None = None) -> dict:
    out_dir = Path(out_dir)
    stem = name or Path(dem).stem
    g, z = open_dem(dem)
    progress.update(0.05, "Flow on the DEM")
    s = Surface(g, z, filled=conditioned)
    stream = s.streams(stream_km2)
    progress.update(0.5, "Stream order")
    order = H.strahler(s.down, s.levels, stream)
    mag = shreve(s.down, s.levels, stream)
    progress.update(0.6, "Links and junctions")
    L = links(g, s, stream)
    zf = s.filled.ravel()
    feats, nodes = [], []
    start_of = {int(c[0]): k + 1 for k, c in enumerate(L["cells"])}
    total_len = 0.0
    for k, cl in enumerate(L["cells"], 1):
        own = [c for c in cl if L["id"][c] == k]               # its cells (without the junction it flows into)
        length = float(s.step[own].sum())
        total_len += length
        end = cl[-1]
        downstream = start_of.get(end) if len(cl) > len(own) else None
        drop = float(zf[own[0]] - zf[end])
        feats.append({"type": "Feature", "properties": {
            "link": k, "strahler": int(order[own[0]]), "shreve": int(mag[own[-1]]), "length_m": round(length, 1),
            "drop_m": round(drop, 2), "slope_pct": round(100 * drop / length, 3) if length > 0 else None,
            "area_km2": round(float(s.area_km2[own[-1]]), 4), "downstream_link": downstream,
            "from_node": "source" if L["inflow"][own[0]] == 0 else "junction", "to_node": "junction" if downstream else "outlet"},
            "geometry": {"type": "LineString", "coordinates": line(g, cl if len(cl) >= 2 else cl * 2)}})
        if k % 2000 == 0:
            progress.update(0.6 + 0.25 * k / len(L["cells"]), f"Links: {k:,} of {len(L['cells']):,}")
    # nodes: sources, junctions, outlets
    for i in np.flatnonzero(stream & (L["inflow"] == 0)):
        nodes.append((int(i), "source", 0))
    for i in np.flatnonzero(stream & (L["inflow"] >= 2)):
        nodes.append((int(i), "junction", int(L["inflow"][i])))
    ends = np.flatnonzero(stream & ((s.down < 0) | ~stream[np.maximum(s.down, 0)]))
    for i in ends:
        nodes.append((int(i), "outlet", int(L["inflow"][i])))
    xs = line(g, [n[0] for n in nodes]) if nodes else []
    node_fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"type": t, "links_in": k, "strahler": int(order[i]),
               "shreve": int(mag[i]), "area_km2": round(float(s.area_km2[i]), 4)}, "geometry": {"type": "Point", "coordinates": xy}}
               for (i, t, k), xy in zip(nodes, xs)]}
    area_km2 = float(g.cell_m2[g.valid].sum() / 1e6)
    outs = []
    if "lines" in outputs:
        outs.append(write_fc({"features": feats}, out_dir / f"{stem}_stream_links.geojson"))
    if "nodes" in outputs:
        outs.append(write_fc(node_fc, out_dir / f"{stem}_stream_nodes.geojson"))
    if "order" in outputs:
        outs.append(write(g, out_dir / f"{stem}_strahler.tif", order, "Strahler stream order (0: not a stream)", dtype="uint8", nodata=0))
    if "magnitude" in outputs:
        outs.append(write(g, out_dir / f"{stem}_shreve.tif", mag, "Shreve magnitude (sources upstream; 0: not a stream)", dtype="int32", nodata=0))
    if "links" in outputs:
        outs.append(write(g, out_dir / f"{stem}_stream_link_id.tif", L["id"], "Stream link id (0: not a stream)", dtype="int32", nodata=0))
    # the orders: Horton's laws (bifurcation and length ratios)
    rows = []
    for o in range(1, int(order.max()) + 1):
        ls = [f["properties"]["length_m"] for f in feats if f["properties"]["strahler"] == o]
        segs = len({f["properties"]["link"] for f in feats if f["properties"]["strahler"] == o})
        rows.append({"order": o, "links": segs, "total_length_km": round(sum(ls) / 1000, 3), "mean_length_km": round(np.mean(ls) / 1000, 3) if ls else 0})
    for a, b in zip(rows[:-1], rows[1:]):
        a["bifurcation_ratio"] = round(a["links"] / b["links"], 2) if b["links"] else None
    table = out_dir / f"{stem}_stream_orders.csv"
    with open(table, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["order", "links", "total_length_km", "mean_length_km", "bifurcation_ratio"])
        wr.writeheader()
        wr.writerows(rows)
    progress.update(1.0, "Done")
    return {"outputs": outs, "csv": str(table), "links": len(feats), "sources": sum(1 for n in nodes if n[1] == "source"),
            "junctions": sum(1 for n in nodes if n[1] == "junction"), "outlets": sum(1 for n in nodes if n[1] == "outlet"),
            "max_strahler": int(order.max()), "max_shreve": int(mag.max()), "stream_length_km": round(total_len / 1000, 3),
            "area_km2": round(area_km2, 3), "drainage_density_km_per_km2": round(total_len / 1000 / max(area_km2, 1e-9), 4), "orders": rows}
