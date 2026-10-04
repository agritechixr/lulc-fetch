"""Spatial interpolation: a surface (GeoTIFF) from values measured at points (rain gauges, soil samples, air-quality
stations, wells, spot heights …), with seven methods, and a leave-one-out check of how well each predicts.

    IDW               nearby points count more (weights 1 / distance^power)
    Kriging           ordinary kriging with a fitted semivariogram (spherical / exponential / Gaussian): the best linear
                      unbiased estimate, plus its standard error (band 2)
    Spline            a smooth surface through the points (thin-plate spline, optionally smoothed)
    Natural neighbour Sibson's weights from the Voronoi cells (discrete Sibson on the output grid), inside the points' hull
    Nearest neighbour the value of the closest point (Voronoi / Thiessen polygons): also for classes
    Trend surface     a polynomial of order 1–3 fitted by least squares: the large-scale trend
    TIN               linear interpolation on the Delaunay triangles, inside the points' hull

Distances are computed in metres (the points are projected to their UTM zone); the output is on that UTM grid.
Needs numpy, scipy and rasterio (already used by the app).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import progress

P = lambda name, title, kind, default, tip, **kw: {"name": name, "title": title, "kind": kind, "default": default, "tip": tip, **kw}  # noqa: E731

METHODS = {
    "idw": {"title": "IDW (inverse distance weighting)", "min_points": 2,
            "desc": "Each point's influence falls with distance: nearby points count more. Simple and robust; the surface "
                    "passes through the points and makes 'bull's-eyes' around them.",
            "good": "rainfall, temperature, soil properties",
            "params": [P("power", "Power", "float", 2.0, "How fast influence falls with distance: 1 smooth, 2 usual, 3+ very local.", min=0.5, max=6),
                       P("neighbours", "Points used per cell", "int", 0, "0 = all points; e.g. 12 = only the 12 nearest (faster, more local).", min=0, max=500)]},
    "kriging": {"title": "Kriging (ordinary)", "min_points": 5,
                "desc": "Uses the spatial autocorrelation of the values: a semivariogram fitted to how differences grow "
                        "with distance sets the weights. The best linear unbiased estimate, with its standard error as band 2. "
                        "Needs enough points (10+) to fit the semivariogram well.",
                "good": "groundwater, soil properties, pollution",
                "params": [P("model", "Semivariogram model", "select", "spherical", "The shape of the fitted semivariogram.",
                             options=[["spherical", "Spherical (usual)"], ["exponential", "Exponential"], ["gaussian", "Gaussian (very smooth)"], ["linear", "Linear (no sill)"]]),
                           P("nugget", "Nugget", "select", "auto", "Variation at very short distances (measurement error): fitted, or none.",
                             options=[["auto", "Fitted"], ["zero", "None (exact at the points)"]])]},
    "spline": {"title": "Spline (thin-plate)", "min_points": 3,
               "desc": "A smooth, minimum-curvature surface through (or near) the points. Good for gently varying fields; can "
                       "overshoot beyond the highest / lowest value between distant points.",
               "good": "elevation, terrain, smooth fields",
               "params": [P("smoothing", "Smoothing", "float", 0.0, "0 = through every point; larger = smoother, near the points (e.g. 0.1–10).", min=0, max=1000)]},
    "natural": {"title": "Natural neighbour", "min_points": 3,
                "desc": "Sibson's method: each cell takes the values of its Voronoi neighbours, weighted by how much of their "
                        "area a new point there would take. Local, smooth and never beyond the data range; defined inside "
                        "the points' convex hull.",
                "good": "elevation, rainfall",
                "params": [P("outside", "Outside the points' hull", "select", "empty", "Natural neighbour is defined inside the hull of the points.",
                             options=[["empty", "Leave empty"], ["nearest", "Fill with the nearest value"]])]},
    "nearest": {"title": "Nearest neighbour", "min_points": 1,
                "desc": "Each cell takes the value of the closest point (Thiessen / Voronoi polygons). No averaging, so it also "
                        "works for classes (soil type, land use codes).",
                "good": "categorical data, quick looks",
                "params": []},
    "trend": {"title": "Trend surface", "min_points": 3,
              "desc": "A polynomial surface (plane, quadratic or cubic) fitted to all points by least squares: shows the "
                      "large-scale trend, not local detail. Doesn't pass through the points.",
              "good": "large-scale spatial trends, removing a trend first",
              "params": [P("order", "Order", "select", "1", "1 = a tilted plane; 2 = one bend; 3 = two bends (needs 10+ points).",
                           options=[["1", "1 (plane)"], ["2", "2 (quadratic)"], ["3", "3 (cubic)"]])]},
    "tin": {"title": "TIN (triangulated irregular network)", "min_points": 3,
            "desc": "Delaunay triangles between the points, with values interpolated linearly inside each triangle. Exact "
                    "at the points; defined inside their convex hull; shows the triangles' edges.",
            "good": "DEM, terrain modelling",
            "params": [P("outside", "Outside the points' hull", "select", "empty", "TIN is defined inside the hull of the points.",
                         options=[["empty", "Leave empty"], ["nearest", "Fill with the nearest value"]])]},
}


def schema() -> dict:
    return {"methods": METHODS}


def _params(method: str, params: dict | None) -> dict:
    out = {p["name"]: p["default"] for p in METHODS[method]["params"]}
    out.update({k: v for k, v in (params or {}).items() if k in out and v is not None and v != ""})
    return out


# ------------------------------------------------------------------ the methods (x, y in metres; query points xq, yq)
def _idw(x, y, z, xq, yq, p):
    from scipy.spatial import cKDTree
    power, k = float(p["power"]), int(p["neighbours"] or 0)
    tree = cKDTree(np.c_[x, y])
    k = len(x) if not k or k >= len(x) else k
    out = np.empty(len(xq))
    for s in range(0, len(xq), 200_000):
        d, i = tree.query(np.c_[xq[s:s + 200_000], yq[s:s + 200_000]], k=k)
        d, i = (d[:, None], i[:, None]) if k == 1 else (d, i)
        with np.errstate(divide="ignore"):
            w = 1.0 / np.maximum(d, 1e-9) ** power
        exact = d[:, 0] < 1e-6
        v = (w * z[i]).sum(1) / w.sum(1)
        v[exact] = z[i[exact, 0]]
        out[s:s + 200_000] = v
    return out


def _nearest(x, y, z, xq, yq, p=None):
    from scipy.spatial import cKDTree
    return z[cKDTree(np.c_[x, y]).query(np.c_[xq, yq])[1]]


def _spline(x, y, z, xq, yq, p):
    from scipy.interpolate import RBFInterpolator
    s = (x.mean(), y.mean(), max(np.ptp(x), np.ptp(y), 1.0))   # scaled coordinates keep the system well conditioned
    f = RBFInterpolator(np.c_[(x - s[0]) / s[2], (y - s[1]) / s[2]], z, kernel="thin_plate_spline", smoothing=float(p["smoothing"]))
    out = np.empty(len(xq))
    for a in range(0, len(xq), 100_000):
        out[a:a + 100_000] = f(np.c_[(xq[a:a + 100_000] - s[0]) / s[2], (yq[a:a + 100_000] - s[1]) / s[2]])
    return out


def _trend(x, y, z, xq, yq, p):
    order = int(p["order"])
    n_terms = (order + 1) * (order + 2) // 2
    if len(x) <= n_terms:
        raise ValueError(f"A trend surface of order {order} needs more than {n_terms} points (there are {len(x)}): choose a lower order")
    cx, cy, s = x.mean(), y.mean(), max(np.ptp(x), np.ptp(y), 1.0)
    terms = lambda u, v: np.column_stack([u ** i * v ** j for i in range(order + 1) for j in range(order + 1 - i)])  # noqa: E731
    coef, *_ = np.linalg.lstsq(terms((x - cx) / s, (y - cy) / s), z, rcond=None)
    return terms((xq - cx) / s, (yq - cy) / s) @ coef


def _tin(x, y, z, xq, yq, p):
    from scipy.interpolate import LinearNDInterpolator
    v = LinearNDInterpolator(np.c_[x, y], z)(np.c_[xq, yq])
    if p.get("outside") == "nearest":
        bad = ~np.isfinite(v)
        v[bad] = _nearest(x, y, z, xq[bad], yq[bad])
    return v


# ---- kriging: empirical semivariogram, fitted model, ordinary kriging system
def _vmodel(model, h, nugget, sill, rng):
    h = np.asarray(h, float)
    a = max(rng, 1e-9)
    if model == "spherical":
        g = np.where(h < a, sill * (1.5 * h / a - 0.5 * (h / a) ** 3), sill)
    elif model == "exponential":
        g = sill * (1 - np.exp(-3 * h / a))
    elif model == "gaussian":
        g = sill * (1 - np.exp(-3 * (h / a) ** 2))
    else:   # linear: slope = sill / range
        g = sill * h / a
    return np.where(h > 0, nugget + g, 0.0)


def fit_variogram(x, y, z, model="spherical", nugget="auto") -> dict:
    """Empirical semivariogram (up to half the largest distance, ~10 bins) and the fitted model (nugget, sill, range)."""
    from scipy.optimize import curve_fit
    from scipy.spatial.distance import pdist
    h, g = pdist(np.c_[x, y]), 0.5 * pdist(z[:, None]) ** 2
    maxd = h.max() / 2 if len(h) > 30 else h.max()
    nb = int(np.clip(len(h) // 6, 4, 12))
    edges = np.linspace(0, maxd, nb + 1)
    lag, gam, cnt = [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (h > a) & (h <= b)
        if m.sum():
            lag.append(h[m].mean()); gam.append(g[m].mean()); cnt.append(int(m.sum()))
    lag, gam = np.array(lag), np.array(gam)
    var = float(np.var(z)) or 1.0
    p0 = [0.0 if nugget == "zero" else 0.05 * var, var, maxd / 2]
    try:
        if nugget == "zero":
            f = lambda hh, s, r: _vmodel(model, hh, 0.0, s, r)  # noqa: E731
            (s, r), _ = curve_fit(f, lag, gam, p0=p0[1:], bounds=([1e-12, maxd * 0.02], [var * 10, h.max() * 3]), sigma=1 / np.sqrt(cnt), maxfev=20000)
            n0 = 0.0
        else:
            f = lambda hh, n, s, r: _vmodel(model, hh, n, s, r)  # noqa: E731
            (n0, s, r), _ = curve_fit(f, lag, gam, p0=p0, bounds=([0, 1e-12, maxd * 0.02], [var * 5, var * 10, h.max() * 3]), sigma=1 / np.sqrt(cnt), maxfev=20000)
        fitted = True
    except (RuntimeError, ValueError):
        n0, s, r, fitted = p0[0], var, maxd / 2, False
    return {"model": model, "nugget": float(n0), "sill": float(s), "range": float(r), "fitted": fitted,
            "lags": lag.round(1).tolist(), "gamma": gam.round(4).tolist(), "pairs": cnt}


def _kriging(x, y, z, xq, yq, p, vg=None, variance=False):
    from scipy.spatial.distance import cdist
    vg = vg or fit_variogram(x, y, z, p["model"], p["nugget"])
    gm = lambda h: _vmodel(vg["model"], h, vg["nugget"], vg["sill"], vg["range"])  # noqa: E731
    n = len(x)
    A = np.ones((n + 1, n + 1))
    A[:n, :n] = gm(cdist(np.c_[x, y], np.c_[x, y]))
    A[n, n] = 0.0
    lu = np.linalg.pinv(A) if n > 1500 else None
    est, sd = np.empty(len(xq)), np.empty(len(xq)) if variance else None
    for a in range(0, len(xq), 20_000):
        g0 = gm(cdist(np.c_[x, y], np.c_[xq[a:a + 20_000], yq[a:a + 20_000]]))
        b = np.vstack([g0, np.ones((1, g0.shape[1]))])
        w = lu @ b if lu is not None else np.linalg.solve(A, b)
        est[a:a + 20_000] = w[:n].T @ z
        if variance:
            sd[a:a + 20_000] = np.sqrt(np.maximum((w * b).sum(0), 0))
    return (est, sd) if variance else est


# ---- natural neighbour: discrete Sibson on the output grid (Park et al., 2006)
def _natural_grid(x, y, z, gx, gy, cell, p):
    """Each cell takes the mean of the nearest-point values of all cells within its distance to the nearest point."""
    from scipy.signal import fftconvolve
    from scipy.spatial import Delaunay, cKDTree
    H, W = gx.shape
    d, i = cKDTree(np.c_[x, y]).query(np.c_[gx.ravel(), gy.ravel()])
    near = z[i].reshape(H, W)
    rad = d.reshape(H, W) / cell
    out = np.full((H, W), np.nan)
    # radii grouped on a geometric scale (≤ 64 disks): one FFT convolution per group
    rmax = max(1.0, float(rad.max()))
    levels = np.unique(np.round(np.geomspace(1, rmax + 1, 64) - 1, 2))
    grp = np.clip(np.searchsorted(levels, rad), 0, len(levels) - 1)
    ones = np.ones((H, W))
    for k, r in enumerate(levels):
        m = grp == k
        if not m.any():
            continue
        R = int(math.ceil(r))
        yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
        disk = ((xx ** 2 + yy ** 2) <= r * r + 1e-9).astype(float)
        num = fftconvolve(near, disk, mode="same")
        den = fftconvolve(ones, disk, mode="same")
        out[m] = (num / np.maximum(den, 1e-9))[m]
        progress.update(0.2 + 0.6 * (k + 1) / len(levels), "Natural neighbour weights")
    if p.get("outside") != "nearest" and len(x) >= 3:   # Sibson interpolation is defined inside the convex hull
        try:
            hull = Delaunay(np.c_[x, y])
            out[hull.find_simplex(np.c_[gx.ravel(), gy.ravel()]).reshape(H, W) < 0] = np.nan
        except Exception:   # collinear points: no hull
            pass
    return out


# ------------------------------------------------------------------ running a method on a grid / at points
def predict(method, x, y, z, xq, yq, params=None):
    p = _params(method, params)
    fn = {"idw": _idw, "kriging": _kriging, "spline": _spline, "nearest": _nearest, "trend": _trend, "tin": _tin}.get(method)
    if fn is None:
        raise ValueError(f"{method} works on a grid only")
    return fn(x, y, z, xq, yq, p)


def leave_one_out(method, x, y, z, params=None, cell=None, max_folds=60) -> dict:
    """Predict each point (up to max_folds of them) from the others: RMSE, MAE, bias (mean error) and the predictions."""
    n = len(x)
    if n < METHODS[method]["min_points"] + 1:
        return {"skipped": f"needs at least {METHODS[method]['min_points'] + 1} points"}
    idx = np.arange(n) if n <= max_folds else np.random.default_rng(0).choice(n, max_folds, replace=False)
    pred = np.full(n, np.nan)
    for i in idx:
        keep = np.arange(n) != i
        try:
            if method == "natural":   # grid-based: a small grid around the points
                c = cell or max(np.ptp(x), np.ptp(y)) / 150
                gx, gy = np.meshgrid(np.arange(x.min() - c, x.max() + 2 * c, c), np.arange(y.min() - c, y.max() + 2 * c, c))
                g = _natural_grid(x[keep], y[keep], z[keep], gx, gy, c, _params(method, params))
                pred[i] = g[int(round((y[i] - gy[0, 0]) / c)), int(round((x[i] - gx[0, 0]) / c))]
            else:
                pred[i] = predict(method, x[keep], y[keep], z[keep], x[i:i + 1], y[i:i + 1], params)[0]
        except (ValueError, np.linalg.LinAlgError):
            pass
    ok = np.isfinite(pred)
    if not ok.any():
        return {"skipped": "no point could be predicted from the others (e.g. all outside their hull)"}
    e = pred[ok] - z[ok]
    return {"rmse": float(np.sqrt(np.mean(e ** 2))), "mae": float(np.mean(np.abs(e))), "bias": float(e.mean()), "n": int(ok.sum()),
            "predicted": [None if not np.isfinite(v) else round(float(v), 4) for v in pred]}


def _utm_epsg(lon, lat) -> int:
    return (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1


def interpolate(points: dict, field: str, method: str, out_path: str | Path, *, params: dict | None = None, area: dict | None = None,
                res_m: float | None = None, max_cells: int = 4_000_000) -> dict:
    """Points (GeoJSON in EPSG:4326, a numeric property `field`) → GeoTIFF on a UTM grid, masked to `area` (a GeoJSON polygon /
    multipolygon in EPSG:4326) when given, else the points' extent plus a margin."""
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from rasterio.warp import transform, transform_geom
    if method not in METHODS:
        raise ValueError(f"Unknown method {method}")
    lon, lat, z = [], [], []
    for f in points.get("features", []):
        g, v = f.get("geometry") or {}, (f.get("properties") or {}).get(field)
        if g.get("type") == "Point" and v is not None and v != "":
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                lon.append(g["coordinates"][0]); lat.append(g["coordinates"][1]); z.append(v)
    if not z:
        raise ValueError(f"No points with a number in “{field}”")
    need = METHODS[method]["min_points"]
    if len(z) < need:
        raise ValueError(f"{METHODS[method]['title']} needs at least {need} points with values (there are {len(z)})")
    epsg = _utm_epsg(float(np.mean(lon)), float(np.mean(lat)))
    crs = f"EPSG:{epsg}"
    xs, ys = transform("EPSG:4326", crs, lon, lat)
    x, y, z = np.asarray(xs), np.asarray(ys), np.asarray(z, float)
    # duplicate positions: their values are averaged (they would make the systems singular)
    key = np.round(np.c_[x, y], 2)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    dup = len(uniq) < len(x)
    if dup:
        z = np.bincount(inv.ravel(), z) / np.bincount(inv.ravel())
        x, y = uniq[:, 0], uniq[:, 1]
    area_utm = transform_geom("EPSG:4326", crs, area) if area else None
    if area_utm:
        from shapely.geometry import shape
        w, s, e, n = shape(area_utm).bounds
    else:
        span = max(np.ptp(x), np.ptp(y), 1000.0)
        w, s, e, n = x.min() - 0.1 * span, y.min() - 0.1 * span, x.max() + 0.1 * span, y.max() + 0.1 * span
    if not res_m:   # about 500 cells across, rounded to a tidy size
        raw = max(e - w, n - s) / 500
        res_m = float(min((1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000), key=lambda v: abs(v - raw)))
    W, H = int(math.ceil((e - w) / res_m)), int(math.ceil((n - s) / res_m))
    if W * H > max_cells:
        raise ValueError(f"{W} × {H} cells at {res_m:g} m is too many: choose a coarser resolution")
    tr = from_origin(w, n, res_m, res_m)
    cx = w + (np.arange(W) + 0.5) * res_m
    cy = n - (np.arange(H) + 0.5) * res_m
    gx, gy = np.meshgrid(cx, cy)
    p = _params(method, params)
    progress.update(0.1, f"{METHODS[method]['title']}: {len(z)} points → {W} × {H} cells at {res_m:g} m")
    extra, vg = None, None
    if method == "natural":
        grid = _natural_grid(x, y, z, gx, gy, res_m, p)
    elif method == "kriging":
        vg = fit_variogram(x, y, z, p["model"], p["nugget"])
        est, sd = _kriging(x, y, z, gx.ravel(), gy.ravel(), p, vg, variance=True)
        grid, extra = est.reshape(H, W), sd.reshape(H, W)
    else:
        grid = predict(method, x, y, z, gx.ravel(), gy.ravel(), p).reshape(H, W)
    progress.update(0.85, "Checking each point against the others (leave-one-out)")
    cv = leave_one_out(method, x, y, z, p, cell=res_m * 2 if method == "natural" else None)
    if area_utm:
        outside = geometry_mask([area_utm], out_shape=(H, W), transform=tr)
        grid[outside] = np.nan
        if extra is not None:
            extra[outside] = np.nan
    bands = [grid] + ([extra] if extra is not None else [])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", driver="GTiff", width=W, height=H, count=len(bands), dtype="float32", crs=crs, transform=tr,
                       nodata=np.nan, compress="deflate", tiled=True) as dst:
        for b, a in enumerate(bands, 1):
            dst.write(a.astype("float32"), b)
        dst.set_band_description(1, f"{field} ({method})")
        if extra is not None:
            dst.set_band_description(2, "kriging standard error")
        dst.update_tags(method=METHODS[method]["title"], field=field, points=str(len(z)), params=str(p))
    v = grid[np.isfinite(grid)]
    progress.update(1, "Done")
    return {"path": str(out_path), "method": method, "method_title": METHODS[method]["title"], "field": field, "points": int(len(z)),
            "duplicates_merged": bool(dup), "width": W, "height": H, "res_m": res_m, "crs": crs, "params": p,
            "min": float(v.min()) if v.size else None, "max": float(v.max()) if v.size else None, "mean": float(v.mean()) if v.size else None,
            "data_min": float(z.min()), "data_max": float(z.max()), "cv": cv, "variogram": vg}


def compare(points: dict, field: str, methods: list[str] | None = None, params: dict | None = None) -> list[dict]:
    """Leave-one-out RMSE / MAE / bias of every method (with its default settings, or `params[method]`), best first."""
    from rasterio.warp import transform
    lon, lat, z = [], [], []
    for f in points.get("features", []):
        g, v = f.get("geometry") or {}, (f.get("properties") or {}).get(field)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if g.get("type") == "Point" and math.isfinite(v):
            lon.append(g["coordinates"][0]); lat.append(g["coordinates"][1]); z.append(v)
    if len(z) < 3:
        raise ValueError("Comparing methods needs at least 3 points with values")
    crs = f"EPSG:{_utm_epsg(float(np.mean(lon)), float(np.mean(lat)))}"
    x, y = (np.asarray(a) for a in transform("EPSG:4326", crs, lon, lat))
    z = np.asarray(z, float)
    rows = []
    ms = methods or list(METHODS)
    for k, m in enumerate(ms):
        progress.update(k / len(ms), f"Checking {METHODS[m]['title']}")
        r = leave_one_out(m, x, y, z, (params or {}).get(m))
        rows.append({"method": m, "title": METHODS[m]["title"], **{k2: v for k2, v in r.items() if k2 != "predicted"}})
    # fair ranking: methods checked at (nearly) every point first; a method that could predict only a few points
    # (Natural neighbour / TIN outside the others' hull) is listed after them, with how many it could check
    full = max((r.get("n") or 0) for r in rows) or 1
    for r in rows:
        r["checked_all"] = (r.get("n") or 0) >= 0.8 * full
    rows.sort(key=lambda r: (r.get("rmse") is None, not r["checked_all"], r.get("rmse") or 0))
    return rows
