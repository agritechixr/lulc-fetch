"""Network analysis on roads (Analysis ▸ Tools ▸ Spatial analysis ▸ Routing): the quickest route through stops, service
areas (what can be reached within 5, 10, 15 … minutes) and the closest facility (e.g. each village's nearest hospital,
by travel time).

Roads come from OpenStreetMap (downloaded with the Overpass API for the area, kept in a cache) or from a line layer of
your own (a speed field in km/h, or one speed). Travel by car (speeds by road type, or the road's maxspeed; one-way streets
respected), bicycle or on foot. Routing is Dijkstra's (scipy.sparse.csgraph); points snap to the nearest road node."""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import requests
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from . import progress

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
CAR_KMH = {"motorway": 100, "motorway_link": 50, "trunk": 80, "trunk_link": 45, "primary": 60, "primary_link": 40, "secondary": 50,
           "secondary_link": 35, "tertiary": 40, "tertiary_link": 30, "unclassified": 30, "residential": 25, "living_street": 10,
           "service": 15, "track": 15, "road": 30}
NOT_ON_FOOT = {"motorway", "motorway_link", "trunk", "trunk_link"}
FOOT_ONLY = {"footway", "path", "pedestrian", "steps", "cycleway", "bridleway"}
MODES = {"car": "Car", "bike": "Bicycle", "walk": "On foot"}
MAX_KM2 = 2500


def _haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_008.8 * np.arcsin(np.sqrt(a))


def _speed(tags: dict, mode: str, default_kmh: float | None):
    """km/h on this way for this mode, or None where it may not go."""
    hw = tags.get("highway", "road")
    if mode == "walk":
        return None if hw in NOT_ON_FOOT else 4.8
    if mode == "bike":
        return None if hw in NOT_ON_FOOT or hw == "steps" else 15.0
    if hw in FOOT_ONLY:
        return None
    ms = str(tags.get("maxspeed", "")).split(";")[0].strip()
    try:
        v = float(ms.replace("mph", "").strip()) * (1.609 if "mph" in ms else 1)
        if 3 <= v <= 150:
            return v * 0.85   # rarely driven at the limit
    except ValueError:
        pass
    return CAR_KMH.get(hw, default_kmh or 30)


def fetch_osm(bbox: tuple[float, float, float, float], cache_dir: Path, mode: str = "car") -> list[dict]:
    """OSM ways with a highway tag in bbox (west, south, east, north): [{coords: [[lon, lat]…], tags}] (cached)."""
    w, s, e, n = bbox
    km2 = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2)) * (n - s) * 110.57
    if km2 > MAX_KM2:
        raise ValueError(f"The area is {km2:,.0f} km²: roads are downloaded for up to {MAX_KM2:,} km² → choose points closer together or a smaller area")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}".encode()).hexdigest()[:16]
    f = cache_dir / f"osm_roads_{key}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 30 * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    q = (f'[out:json][timeout:180];way["highway"]["area"!="yes"]["highway"!~"^(proposed|construction|abandoned|platform|raceway|bus_stop|elevator|corridor)$"]'
         f'({s},{w},{n},{e});(._;>;);out body;')
    progress.update(0.05, f"Downloading the roads of {km2:,.0f} km² from OpenStreetMap")
    last = None
    for url in OVERPASS:
        try:
            r = requests.post(url, data={"data": q}, timeout=240, headers={"User-Agent": "LULC-Fetch"})
            if r.status_code == 200:
                data = r.json()
                break
            last = f"{url}: HTTP {r.status_code}"
        except requests.RequestException as ex:
            last = f"{url}: {ex}"
    else:
        raise RuntimeError(f"OpenStreetMap (Overpass) did not answer → try again in a minute ({last})")
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in data["elements"] if el["type"] == "node"}
    ways = [{"coords": [nodes[i] for i in el["nodes"] if i in nodes], "tags": el.get("tags", {})}
            for el in data["elements"] if el["type"] == "way"]
    ways = [wy for wy in ways if len(wy["coords"]) >= 2]
    if not ways:
        raise ValueError("OpenStreetMap has no roads in this area")
    f.write_text(json.dumps(ways), encoding="utf-8")
    return ways


def ways_from_layer(fc: dict, speed_field: str | None = None, speed_kmh: float = 30.0) -> list[dict]:
    """A line layer as ways; speed from a field (km/h) or one speed for all."""
    ways = []
    for ft in fc.get("features", []):
        g = ft.get("geometry") or {}
        props = ft.get("properties") or {}
        parts = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", []) if g.get("type") == "MultiLineString" else []
        sp = props.get(speed_field) if speed_field else None
        try:
            sp = float(sp) if sp not in (None, "") else speed_kmh
        except (TypeError, ValueError):
            sp = speed_kmh
        for p in parts:
            if len(p) >= 2:
                ways.append({"coords": [c[:2] for c in p], "tags": {"_kmh": sp}})
    if not ways:
        raise ValueError("The layer has no lines")
    return ways


class Network:
    """A routable graph: nodes (lon, lat), and per directed edge its length (m), time (min) and geometry."""

    def __init__(self, ways: list[dict], mode: str = "car"):
        if mode not in MODES:
            raise ValueError(f"mode: {', '.join(MODES)}")
        ids: dict[tuple, int] = {}
        coords: list[tuple] = []
        src, dst, length, minutes = [], [], [], []

        def nid(c):
            k = (round(c[0], 7), round(c[1], 7))
            if k not in ids:
                ids[k] = len(coords)
                coords.append(k)
            return ids[k]
        for wy in ways:
            t = wy["tags"]
            kmh = t["_kmh"] if "_kmh" in t else _speed(t, mode, None)
            if not kmh:
                continue
            ow = str(t.get("oneway", "")).lower()
            fwd_only = mode == "car" and (ow in ("yes", "true", "1") or t.get("junction") == "roundabout" or t.get("highway") in ("motorway",))
            back_only = mode == "car" and ow == "-1"
            c = wy["coords"]
            a = np.array(c, dtype="float64")
            seg = _haversine(a[:-1, 0], a[:-1, 1], a[1:, 0], a[1:, 1])
            for (p, q), m in zip(zip(c[:-1], c[1:]), seg):
                i, j = nid(p), nid(q)
                if i == j:
                    continue
                mins = m / (kmh * 1000 / 60)
                if not back_only:
                    src.append(i); dst.append(j); length.append(m); minutes.append(mins)
                if not fwd_only:
                    src.append(j); dst.append(i); length.append(m); minutes.append(mins)
        if not src:
            raise ValueError(f"No road in the area can be used {MODES[mode].lower()}")
        self.mode = mode
        self.xy = np.array(coords)
        n = len(coords)
        self.src, self.dst = np.array(src), np.array(dst)
        self.length, self.minutes = np.array(length), np.array(minutes)
        # keep the quickest of parallel edges
        order = np.lexsort((self.minutes, self.dst, self.src))
        keep = np.r_[True, (np.diff(self.src[order]) != 0) | (np.diff(self.dst[order]) != 0)]
        o = order[keep]
        self.src, self.dst, self.length, self.minutes = self.src[o], self.dst[o], self.length[o], self.minutes[o]
        self.G = csr_matrix((self.minutes, (self.src, self.dst)), shape=(n, n))
        lat0 = float(np.mean(self.xy[:, 1]))
        self._k = np.array([111_320 * math.cos(math.radians(lat0)), 110_574])
        self.tree = cKDTree(self.xy * self._k)
        self.edge_len = {(int(a), int(b)): float(m) for a, b, m in zip(self.src, self.dst, self.length)}

    def snap(self, points: list[list[float]]) -> tuple[np.ndarray, np.ndarray]:
        d, i = self.tree.query(np.asarray(points, dtype="float64")[:, :2] * self._k)
        return i, d

    def path(self, pred: np.ndarray, a: int, b: int) -> list[int]:
        out = [b]
        while out[-1] != a:
            p = pred[out[-1]]
            if p < 0:
                return []
            out.append(int(p))
        return out[::-1]

    def line(self, nodes: list[int]) -> list[list[float]]:
        return [[round(float(x), 7), round(float(y), 7)] for x, y in self.xy[nodes]]

    def path_length(self, nodes: list[int]) -> float:
        return sum(self.edge_len.get((a, b), 0.0) for a, b in zip(nodes[:-1], nodes[1:]))


def bbox_of(points: list[list[float]], margin_km: float) -> tuple[float, float, float, float]:
    p = np.asarray(points, dtype="float64")
    lat = float(p[:, 1].mean())
    dx, dy = margin_km / (111.32 * max(math.cos(math.radians(lat)), 0.05)), margin_km / 110.57
    return float(p[:, 0].min() - dx), float(p[:, 1].min() - dy), float(p[:, 0].max() + dx), float(p[:, 1].max() + dy)


def route(net: Network, stops: list[list[float]]) -> dict:
    """The quickest route through the stops in order."""
    if len(stops) < 2:
        raise ValueError("Give at least two stops")
    idx, snapd = net.snap(stops)
    feats, total_m, total_min = [], 0.0, 0.0
    for k, (a, b) in enumerate(zip(idx[:-1], idx[1:])):
        dist, pred = dijkstra(net.G, indices=int(a), return_predecessors=True)
        if not np.isfinite(dist[b]):
            raise ValueError(f"Stop {k + 2} cannot be reached from stop {k + 1} on these roads ({MODES[net.mode].lower()})")
        nodes = net.path(pred, int(a), int(b))
        m = net.path_length(nodes)
        total_m += m
        total_min += float(dist[b])
        feats.append({"type": "Feature", "properties": {"leg": k + 1, "from_stop": k + 1, "to_stop": k + 2, "length_km": round(m / 1000, 3),
                                                        "minutes": round(float(dist[b]), 1)},
                      "geometry": {"type": "LineString", "coordinates": net.line(nodes)}})
    return {"type": "FeatureCollection", "features": feats, "summary": {"length_km": round(total_m / 1000, 3), "minutes": round(total_min, 1),
                                                                         "max_snap_m": round(float(snapd.max()), 1)}}


def _utm_epsg(lon, lat):
    return (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) % 60 + 1


def service_areas(net: Network, origins: list[list[float]], breaks: list[float], *, buffer_m: float = 60.0) -> dict:
    """Polygons of what can be reached from any origin within each break (minutes): the reached roads, widened."""
    from rasterio.warp import transform_geom
    from shapely.geometry import LineString, mapping, shape
    from shapely.ops import unary_union
    if not breaks:
        raise ValueError("Give at least one time (minutes)")
    breaks = sorted(set(float(b) for b in breaks))
    idx, snapd = net.snap(origins)
    t = dijkstra(net.G, indices=np.unique(idx), min_only=True)
    lon0, lat0 = float(np.mean(net.xy[:, 0])), float(np.mean(net.xy[:, 1]))
    utm = f"EPSG:{_utm_epsg(lon0, lat0)}"
    feats = []
    prev = None
    for k, b in enumerate(breaks):
        # edges whose start is reached; the part of the edge reachable in the time left
        reach = t[net.src] <= b
        lines = []
        for s, d, mins in zip(net.src[reach], net.dst[reach], net.minutes[reach]):
            left = b - t[s]
            f = 1.0 if t[d] <= b or mins <= 0 else min(1.0, left / mins)
            p, q = net.xy[s], net.xy[d]
            lines.append(LineString([p, p + (q - p) * f]) if f < 1 else LineString([p, q]))
        if not lines:
            continue
        g = unary_union([shape(transform_geom("EPSG:4326", utm, mapping(unary_union(lines[i:i + 20000])))) for i in range(0, len(lines), 20000)])
        poly = g.buffer(buffer_m, quad_segs=4).buffer(-buffer_m / 3).buffer(buffer_m / 3)
        ring = poly.difference(prev) if prev is not None else poly
        prev = poly
        feats.append({"type": "Feature", "properties": {"minutes": b, "from_minutes": breaks[k - 1] if k else 0, "area_km2": round(poly.area / 1e6, 3)},
                      "geometry": transform_geom(utm, "EPSG:4326", mapping(ring))})
        progress.update(0.6 + 0.4 * (k + 1) / len(breaks), f"Service area {b:g} min")
    return {"type": "FeatureCollection", "features": feats[::-1], "summary": {"breaks": breaks, "max_snap_m": round(float(snapd.max()), 1),
                                                                               "areas_km2": {f["properties"]["minutes"]: f["properties"]["area_km2"] for f in feats}}}


def closest_facility(net: Network, incidents: list[list[float]], facilities: list[list[float]], *, names: list[str] | None = None) -> dict:
    """For each incident, the quickest facility to reach and the route there."""
    fi, _ = net.snap(facilities)
    ii, snapd = net.snap(incidents)
    # from every facility at once, on the reversed graph: the time from each node to its nearest facility
    t, pred, srcs = dijkstra(net.G.T.tocsr(), indices=fi, min_only=True, return_predecessors=True)
    node_to_fac = {int(n): k for k, n in enumerate(fi)}
    feats = []
    for k, a in enumerate(ii):
        a = int(a)
        if not np.isfinite(t[a]):
            feats.append({"type": "Feature", "properties": {"incident": k + 1, "facility": None, "minutes": None, "length_km": None}, "geometry": None})
            continue
        nodes = [a]
        while pred[nodes[-1]] >= 0:
            nodes.append(int(pred[nodes[-1]]))
        fac = node_to_fac.get(nodes[-1], node_to_fac.get(int(srcs[a])))
        m = net.path_length(nodes)
        feats.append({"type": "Feature", "properties": {"incident": k + 1, "facility": (fac + 1) if fac is not None else None,
                                                        "facility_name": names[fac] if names and fac is not None else None,
                                                        "minutes": round(float(t[a]), 1), "length_km": round(m / 1000, 3)},
                      "geometry": {"type": "LineString", "coordinates": net.line(nodes)} if len(nodes) > 1 else None})
    feats = [f for f in feats if f["geometry"]] + [f for f in feats if not f["geometry"]]
    reached = [f["properties"]["minutes"] for f in feats if f["properties"]["minutes"] is not None]
    return {"type": "FeatureCollection", "features": [f for f in feats if f["geometry"]],
            "summary": {"incidents": len(incidents), "reached": len(reached), "unreachable": len(incidents) - len(reached),
                        "mean_minutes": round(float(np.mean(reached)), 1) if reached else None,
                        "max_minutes": round(float(np.max(reached)), 1) if reached else None, "max_snap_m": round(float(snapd.max()), 1)}}
