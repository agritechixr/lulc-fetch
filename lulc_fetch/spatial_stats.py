"""Spatial statistics of point or polygon layers (GeoJSON in EPSG:4326, measured in metres in the local UTM zone):

    kernel_density(fc, out)          a heat map of points (e.g. disease reports), per km², optionally weighted by a field
    hot_spots(fc, field=None)        Getis-Ord Gi*: where high (hot) or low (cold) values cluster more than chance;
                                     without a field, points are counted in a grid of cells first (incidents)
    morans_i(fc, field)              global Moran's I (is the pattern clustered, random or dispersed?) and the local
                                     clusters (LISA: high-high, low-low, and the outliers high-low / low-high)
    nearest_neighbour(fc)            average nearest neighbour (Clark-Evans R): clustered, random or dispersed points

Neighbours: within a distance band (default: the smallest distance that gives every feature a neighbour, as ArcGIS
does) or the k nearest. Polygons and lines are used at their centroids. p-values are two-sided, from the normal
distribution (Gi*, Moran's I); Moran's I and LISA also give permutation pseudo p-values (one-sided, as GeoDa / PySAL).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from shapely.geometry import box, mapping, shape

from . import progress

MAX_FEATURES = 60_000


# ------------------------------------------------------------------ shared
def _utm(lon: float, lat: float) -> str:
    return f"EPSG:{(32700 if lat < 0 else 32600) + int((lon + 180) // 6) % 60 + 1}"


def _features(fc: dict, field: str | None = None, need_values: bool = False):
    """(features, centroids lon/lat, values or None) of the features with a geometry (and a number in field)."""
    feats, xy, vals = [], [], []
    for f in fc.get("features", []):
        g = f.get("geometry")
        if not g:
            continue
        sh = shape(g)
        if sh.is_empty:
            continue
        v = None
        if field:
            v = (f.get("properties") or {}).get(field)
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(v):
                continue
        c = sh if sh.geom_type == "Point" else sh.centroid
        feats.append(f)
        xy.append((c.x, c.y))
        vals.append(v)
    if len(feats) > MAX_FEATURES:
        raise ValueError(f"More than {MAX_FEATURES:,} features: select or aggregate them first")
    if need_values and field is None:
        raise ValueError("Choose a number field")
    return feats, np.array(xy, float).reshape(-1, 2), (np.array(vals, float) if field else None)


def _to_metres(lonlat: np.ndarray):
    from rasterio.warp import transform
    lon0, lat0 = float(np.mean(lonlat[:, 0])), float(np.mean(lonlat[:, 1]))
    crs = _utm(lon0, lat0)
    x, y = transform("EPSG:4326", crs, lonlat[:, 0].tolist(), lonlat[:, 1].tolist())
    return np.column_stack([x, y]), crs


def _neighbours(xy: np.ndarray, distance_m: float | None = None, k: int | None = None, self_too: bool = False):
    """Neighbour lists (indices) for each feature, and the distance band used (None for k nearest)."""
    from scipy.spatial import cKDTree
    n = len(xy)
    tree = cKDTree(xy)
    if k:
        k = min(int(k), n - 1)
        _, idx = tree.query(xy, k + 1)
        lists = [list(r[1:]) for r in idx]
        band = None
    else:
        if distance_m is None:   # every feature gets at least one neighbour
            d, _ = tree.query(xy, 2)
            distance_m = float(d[:, 1].max()) * 1.0001
        lists = [[j for j in r if j != i] for i, r in enumerate(tree.query_ball_point(xy, distance_m))]
        band = distance_m
    if self_too:
        lists = [[i] + lst for i, lst in enumerate(lists)]
    return lists, band


def _norm_p(z):
    from scipy.stats import norm
    return 2 * norm.sf(np.abs(z))


def _pseudo_p(sim: np.ndarray, observed: float) -> float:
    """Permutation pseudo p-value as GeoDa and PySAL: the share of simulated values at least as extreme as the observed
    one, on the side it falls ((k + 1) / (permutations + 1))."""
    larger = int((sim >= observed).sum())
    return (min(larger, len(sim) - larger) + 1) / (len(sim) + 1)


def _fdr_bins(z: np.ndarray, p: np.ndarray, fdr: bool) -> np.ndarray:
    """Confidence bins −3…3 (±3: 99 %, ±2: 95 %, ±1: 90 %), with the false discovery rate correction if asked."""
    bins = np.zeros(len(p), int)
    n = len(p)
    for level, alpha in ((1, 0.10), (2, 0.05), (3, 0.01)):
        if fdr:   # Benjamini-Hochberg: the largest p(k) ≤ α k / n sets the critical p
            ps = np.sort(p)
            ok = ps <= alpha * np.arange(1, n + 1) / n
            crit = ps[ok].max() if ok.any() else -1.0
        else:
            crit = alpha
        bins[p <= crit] = level
    return bins * np.sign(z).astype(int)


def _fc(feats, extra: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": f["geometry"], "properties": {**(f.get("properties") or {}), **e}} for f, e in zip(feats, extra)]}


# ------------------------------------------------------------------ kernel density
def default_bandwidth(xy: np.ndarray, w: np.ndarray | None = None) -> float:
    """The spatial variant of Silverman's rule (as ArcGIS Kernel Density): 0.9 · min(SD, √(1/ln 2) · Dm) · n^−0.2."""
    w = np.ones(len(xy)) if w is None else w
    c = np.average(xy, axis=0, weights=w)
    d = np.hypot(*(xy - c).T)
    sd = math.sqrt(np.average(d ** 2, weights=w))
    dm = float(np.median(d))
    h = 0.9 * min(sd, math.sqrt(1 / math.log(2)) * dm) * max(w.sum(), 1) ** -0.2
    return h if h > 0 else max(sd, 1.0)


def kernel_density(fc: dict, out: str | Path, *, weight_field: str | None = None, bandwidth_m: float | None = None,
                   cell_m: float | None = None, kernel: str = "quartic") -> dict:
    """Density of points per km² (sum of weights per km² with a weight field) as a GeoTIFF in the local UTM zone."""
    import rasterio
    from rasterio.transform import from_origin
    from scipy.signal import fftconvolve
    feats, ll, w = _features(fc, weight_field)
    if len(feats) < 2:
        raise ValueError("Kernel density needs at least 2 points" + (f" with a number in {weight_field}" if weight_field else ""))
    w = np.ones(len(feats)) if w is None else np.clip(w, 0, None)
    xy, crs = _to_metres(ll)
    h = float(bandwidth_m) if bandwidth_m else default_bandwidth(xy, w)
    (x0, y0), (x1, y1) = xy.min(0), xy.max(0)
    span = max(x1 - x0, y1 - y0, h)
    cell = float(cell_m) if cell_m else max(min(x1 - x0, y1 - y0, span) / 250, span / 2500, h / 25, 1.0)
    pad = h + cell
    W, H = math.ceil((x1 - x0 + 2 * pad) / cell), math.ceil((y1 - y0 + 2 * pad) / cell)
    if W * H > 40_000_000:
        raise ValueError(f"The grid would be {W:,} × {H:,} cells: make the cells bigger")
    left, top = x0 - pad, y1 + pad
    counts = np.zeros((H, W))
    ci = np.clip(((xy[:, 0] - left) / cell).astype(int), 0, W - 1)
    ri = np.clip(((top - xy[:, 1]) / cell).astype(int), 0, H - 1)
    np.add.at(counts, (ri, ci), w)
    r = int(math.ceil(h / cell)) if kernel == "quartic" else int(math.ceil(3 * h / cell))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1] * cell
    d2 = (xx ** 2 + yy ** 2) / h ** 2
    if kernel == "quartic":
        k = np.where(d2 < 1, 3 / (math.pi * h * h) * (1 - d2) ** 2, 0.0)
    elif kernel == "gaussian":
        k = np.exp(-d2 / 2) / (2 * math.pi * h * h)
    else:
        raise ValueError("kernel: quartic or gaussian")
    dens = np.clip(fftconvolve(counts, k * cell * cell, mode="same"), 0, None) / (cell * cell) * 1e6   # per km²
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", width=W, height=H, count=1, dtype="float32", crs=crs, nodata=None,
                       transform=from_origin(left, top, cell, cell), compress="deflate") as d:
        d.write(dens.astype("float32"), 1)
        d.set_band_description(1, f"Density per km²{f' ({weight_field})' if weight_field else ''}")
        d.update_tags(units="per km²", bandwidth_m=round(h, 2), kernel=kernel)
    return {"path": str(out), "points": len(feats), "bandwidth_m": round(h, 1), "cell_m": round(cell, 2), "max_per_km2": round(float(dens.max()), 4),
            "crs": crs}


# ------------------------------------------------------------------ hot spots (Getis-Ord Gi*)
HOT_NAMES = {3: "Hot spot (99%)", 2: "Hot spot (95%)", 1: "Hot spot (90%)", 0: "Not significant",
             -1: "Cold spot (90%)", -2: "Cold spot (95%)", -3: "Cold spot (99%)"}
HOT_COLORS = {3: "#b2182b", 2: "#ef8a62", 1: "#fddbc7", 0: "#e0e0e0", -1: "#d1e5f0", -2: "#67a9cf", -3: "#2166ac"}


def count_grid(fc: dict, cell_m: float | None = None) -> tuple[dict, float]:
    """Points (incidents) counted in square cells covering them (cells within their convex hull, plus a cell around),
    as polygons with a `count` field (zeros included: Gi* needs them)."""
    from rasterio.warp import transform_geom
    from shapely.geometry import MultiPoint
    feats, ll, _ = _features(fc)
    if len(feats) < 3:
        raise ValueError("Hot spots of incidents need at least 3 points")
    xy, crs = _to_metres(ll)
    (x0, y0), (x1, y1) = xy.min(0), xy.max(0)
    if not cell_m:
        from scipy.spatial import cKDTree
        d, _ = cKDTree(xy).query(xy, 2)
        nn = float(np.mean(d[:, 1])) or 1.0
        cell_m = max(2 * nn, max(x1 - x0, y1 - y0) / 60, 1.0)   # a few points per cell, at most ~60 cells across
    hull = MultiPoint([tuple(p) for p in xy]).convex_hull.buffer(cell_m)
    nx, ny = int((x1 - x0) // cell_m) + 3, int((y1 - y0) // cell_m) + 3
    if nx * ny > 200_000:
        raise ValueError("Too many cells: make them bigger")
    gx0, gy0 = x0 - cell_m, y0 - cell_m
    cnt = np.zeros((ny, nx), int)
    np.add.at(cnt, (((xy[:, 1] - gy0) // cell_m).astype(int), ((xy[:, 0] - gx0) // cell_m).astype(int)), 1)
    out = []
    for r in range(ny):
        for c in range(nx):
            b = box(gx0 + c * cell_m, gy0 + r * cell_m, gx0 + (c + 1) * cell_m, gy0 + (r + 1) * cell_m)
            if b.intersects(hull):
                out.append({"type": "Feature", "geometry": transform_geom(crs, "EPSG:4326", mapping(b)), "properties": {"count": int(cnt[r, c])}})
    return {"type": "FeatureCollection", "features": out}, cell_m


def hot_spots(fc: dict, field: str | None = None, *, distance_m: float | None = None, k: int | None = None, fdr: bool = True,
              cell_m: float | None = None) -> dict:
    """Getis-Ord Gi* of a number field (or, without one, of point counts per grid cell): z, p and a hot / cold class."""
    grid = None
    if not field:
        fc, grid = count_grid(fc, cell_m)
        field = "count"
    feats, ll, x = _features(fc, field, need_values=True)
    n = len(feats)
    if n < 8:
        raise ValueError(f"Hot spots need at least 8 features with a number (there are {n})")
    if np.ptp(x) == 0:
        raise ValueError(f"Every feature has the same {field}: nothing to compare")
    xy, _ = _to_metres(ll)
    lists, band = _neighbours(xy, distance_m, k, self_too=True)
    xbar, s = x.mean(), math.sqrt((x ** 2).mean() - x.mean() ** 2)
    z = np.empty(n)
    for i, js in enumerate(lists):   # binary weights, the feature itself included (the * of Gi*)
        wsum = len(js)
        num = x[js].sum() - xbar * wsum
        den = s * math.sqrt(max((n * wsum - wsum ** 2) / (n - 1), 1e-12))
        z[i] = num / den
    p = _norm_p(z)
    bins = _fdr_bins(z, p, fdr)
    extra = [{"gi_z": round(float(zi), 4), "gi_p": round(float(pi), 6), "gi_bin": int(b), "class": HOT_NAMES[int(b)], "neighbors": len(js) - 1}
             for zi, pi, b, js in zip(z, p, bins, lists)]
    counts = {HOT_NAMES[b]: int((bins == b).sum()) for b in (3, 2, 1, 0, -1, -2, -3)}
    no_nb = sum(1 for js in lists if len(js) == 1)
    return {"geojson": _fc(feats, extra), "field": field, "features": n, "distance_m": round(band, 1) if band else None, "k": k,
            "grid_cell_m": round(grid, 1) if grid else None, "fdr": fdr, "counts": counts, "no_neighbours": no_nb,
            "classes": [{"name": HOT_NAMES[b], "color": HOT_COLORS[b]} for b in (3, 2, 1, 0, -1, -2, -3) if counts[HOT_NAMES[b]]]}


# ------------------------------------------------------------------ Moran's I (global) and LISA
LISA_NAMES = {"HH": "High-high cluster", "LL": "Low-low cluster", "HL": "High-low outlier", "LH": "Low-high outlier", "": "Not significant"}
LISA_COLORS = {"HH": "#d7191c", "LL": "#2c7bb6", "HL": "#fdae61", "LH": "#abd9e9", "": "#e0e0e0"}


def morans_i(fc: dict, field: str, *, distance_m: float | None = None, k: int | None = None, permutations: int = 999,
             alpha: float = 0.05, seed: int = 0) -> dict:
    """Global Moran's I with row-standardised weights (z and p under normality and by permutation), and the local
    Moran's I of each feature (LISA, conditional permutations) with its cluster type."""
    feats, ll, x = _features(fc, field, need_values=True)
    n = len(feats)
    if n < 8:
        raise ValueError(f"Moran's I needs at least 8 features with a number (there are {n})")
    if np.ptp(x) == 0:
        raise ValueError(f"Every feature has the same {field}: nothing to compare")
    xy, _ = _to_metres(ll)
    lists, band = _neighbours(xy, distance_m, k)
    z = x - x.mean()
    m2 = (z ** 2).sum() / (n - 1)   # local Moran's I as Anselin (1995) and PySAL: (n − 1) zi lag_i / Σz²
    from scipy.sparse import csr_matrix
    has = np.array([len(js) > 0 for js in lists])
    r_ = np.repeat(np.arange(n), [len(js) for js in lists])
    c_ = np.fromiter((j for js in lists for j in js), int, len(r_))
    W = csr_matrix((1.0 / np.repeat([max(len(js), 1) for js in lists], [len(js) for js in lists]), (r_, c_)), shape=(n, n))   # row-standardised
    lag = W @ z   # the mean of each feature's neighbours
    S0 = float(has.sum())
    I = float((z * lag).sum() / (z ** 2).sum() * n / S0)
    # variance under normality, from S1 and S2 of the weights
    S1 = float(((W + W.T).power(2)).sum()) / 2
    S2 = float(((np.asarray(W.sum(1)).ravel() + np.asarray(W.sum(0)).ravel()) ** 2).sum())
    EI = -1 / (n - 1)
    VI = (n * n * S1 - n * S2 + 3 * S0 * S0) / ((n * n - 1) * S0 * S0) - EI ** 2
    zI = (I - EI) / math.sqrt(VI) if VI > 0 else 0.0
    rng = np.random.default_rng(seed)
    progress.update(0.3, "Moran's I: permutations")
    perm = np.empty(permutations)
    for t0 in range(0, permutations, 100):   # 100 random arrangements of the values at a time
        Zp = np.column_stack([rng.permutation(z) for _ in range(min(100, permutations - t0))])
        perm[t0:t0 + Zp.shape[1]] = (Zp * (W @ Zp)).sum(0) / (Zp ** 2).sum(0) * n / S0
    p_perm = _pseudo_p(perm, I)
    # LISA: Ii = (n − 1) zi lag_i / Σz²; conditional permutations: feature i's neighbours drawn from the other values
    progress.update(0.6, "Local Moran's I")
    Ii = z / m2 * lag
    P = min(permutations, 499)
    p_loc = np.ones(n)
    for i, js in enumerate(lists):
        if not js:
            continue
        pick = rng.integers(0, n - 1, size=(P, len(js)))   # indices into the other n − 1 values
        draws = np.delete(z, i)[pick].mean(1)
        p_loc[i] = _pseudo_p(z[i] / m2 * draws, Ii[i])
    quad = np.where(z > 0, np.where(lag > 0, "HH", "HL"), np.where(lag > 0, "LH", "LL"))
    cl = np.where((p_loc <= alpha) & has, quad, "")
    extra = [{"lisa_i": round(float(a), 5), "lisa_p": round(float(b), 4), "lag": round(float(c + x.mean()), 5), "cluster": str(q),
              "class": LISA_NAMES[str(q)]} for a, b, c, q in zip(Ii, p_loc, lag, cl)]
    pattern = "clustered" if p_perm <= 0.05 and I > EI else "dispersed" if p_perm <= 0.05 and I < EI else "random"
    counts = {LISA_NAMES[k_]: int((cl == k_).sum()) for k_ in ("HH", "LL", "HL", "LH", "")}
    return {"geojson": _fc(feats, extra), "field": field, "features": n, "distance_m": round(band, 1) if band else None, "k": k,
            "I": round(I, 5), "expected": round(EI, 5), "variance": round(VI, 7), "z": round(zI, 4), "p_normal": round(float(_norm_p(zI)), 6),
            "p_permutation": round(float(p_perm), 4), "permutations": permutations, "pattern": pattern, "no_neighbours": int((~has).sum()),
            "counts": counts, "classes": [{"name": LISA_NAMES[k_], "color": LISA_COLORS[k_]} for k_ in ("HH", "LL", "HL", "LH", "") if counts[LISA_NAMES[k_]]]}


# ------------------------------------------------------------------ average nearest neighbour
def nearest_neighbour(fc: dict, *, area_m2: float | None = None, area: dict | None = None) -> dict:
    """Clark-Evans: the mean distance to each point's nearest neighbour against that of random points in the study
    area (default: the smallest rectangle around the points, any rotation, as ArcGIS does). R < 1 clustered, > 1 dispersed."""
    from rasterio.warp import transform_geom
    from scipy.spatial import cKDTree
    from shapely.geometry import MultiPoint
    feats, ll, _ = _features(fc)
    n = len(feats)
    if n < 3:
        raise ValueError("Nearest neighbour analysis needs at least 3 points")
    xy, crs = _to_metres(ll)
    d, idx = cKDTree(xy).query(xy, 2)
    nn = d[:, 1]
    if area is not None:
        A = shape(transform_geom("EPSG:4326", crs, area)).area
        how = "the area given"
    elif area_m2:
        A, how = float(area_m2), "the area given"
    else:
        A, how = MultiPoint([tuple(p) for p in xy]).minimum_rotated_rectangle.area, "the smallest rectangle around the points"
    if A <= 0:
        raise ValueError("The points all lie on one line: give a study area")
    do, de = float(nn.mean()), 0.5 / math.sqrt(n / A)
    se = 0.26136 / math.sqrt(n * n / A)
    R, z = do / de, (do - de) / se
    p = float(_norm_p(z))
    pattern = "clustered" if p <= 0.05 and R < 1 else "dispersed" if p <= 0.05 and R > 1 else "random"
    extra = [{"nn_dist_m": round(float(a), 2), "nn_index": int(b[1])} for a, b in zip(nn, idx)]
    return {"geojson": _fc(feats, extra), "features": n, "observed_m": round(do, 2), "expected_m": round(de, 2), "R": round(R, 4),
            "z": round(z, 4), "p": round(p, 6), "pattern": pattern, "area_km2": round(A / 1e6, 4), "area_from": how}
