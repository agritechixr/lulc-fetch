"""Fuzzy geospatial analysis: gradual membership (0–1) instead of hard yes / no boundaries.

    membership(values, fn, **params)          a membership function on an array (see FUNCTIONS)
    membership_raster(path, out, fn, …)       a raster band → a 0–1 membership raster
    distance_raster(fc, out, …)               fuzzy distance: metres to the nearest feature of a layer → 0–1 ("near" /
                                              "far" a road, a river …), on a reference raster's grid or a UTM grid
    overlay(layers, out, op, …)               fuzzy overlay / suitability of several layers (each with its own
                                              membership function and weight): AND, OR, product, sum, gamma,
                                              weighted sum, weighted product
    boundary(path, out_dir, …)                uncertainty (1 − |2μ − 1|), α-cut zones and the crisp boundary as
                                              smoothed polygons, and the transition zone
    cmeans(path, out_dir, k, …)               fuzzy c-means classification: a membership band per class, the hard
                                              class and the uncertainty of each pixel (mixed pixels)

Membership functions (as ArcGIS Fuzzy Membership, plus linear, Gaussian by σ and sigmoid):
    linear    a → 0, b → 1 (a > b: decreasing)                    small   1 / (1 + (x / mid)^spread)
    gaussian  exp(−(x − mid)² / 2σ²)                               large   1 / (1 + (x / mid)^−spread)
    near      1 / (1 + spread · (x − mid)²)                        sigmoid 1 / (1 + exp(−slope · (x − mid)))
    trapezoid a → 0 … b → 1 … c → 1 … d → 0
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

from . import progress

FUNCTIONS = ("linear", "small", "large", "gaussian", "near", "sigmoid", "trapezoid", "none")
OPERATORS = ("and", "or", "product", "sum", "gamma", "weighted_sum", "weighted_product")
MAX_PIXELS = 60_000_000
CLASSES = [(0.2, "Very low", "#d7191c"), (0.4, "Low", "#fdae61"), (0.6, "Moderate", "#ffffbf"), (0.8, "High", "#a6d96a"), (1.01, "Very high", "#1a9641")]


# ------------------------------------------------------------------ membership functions
def membership(x: np.ndarray, fn: str, *, a: float | None = None, b: float | None = None, c: float | None = None,
               d: float | None = None, mid: float | None = None, spread: float | None = None, sigma: float | None = None,
               slope: float | None = None) -> np.ndarray:
    """The membership (0–1, NaN where x is NaN) of each value under one function."""
    x = np.asarray(x, dtype="float64")
    with np.errstate(all="ignore"):
        if fn == "none":   # already a membership (0–1)
            m = x
        elif fn == "linear":
            if a is None or b is None or a == b:
                raise ValueError("Linear needs two different values: where membership is 0 (a) and where it is 1 (b)")
            m = (x - a) / (b - a)
        elif fn == "trapezoid":
            if None in (a, b, c, d) or not a <= b <= c <= d or a == d:
                raise ValueError("Trapezoid needs a ≤ b ≤ c ≤ d (0 at a, 1 from b to c, 0 at d)")
            up = np.where(x < b, (x - a) / (b - a) if b > a else 1.0, 1.0)
            down = np.where(x > c, (d - x) / (d - c) if d > c else 1.0, 1.0)
            m = np.minimum(up, down)
        elif fn == "gaussian":
            if mid is None or not sigma or sigma <= 0:
                raise ValueError("Gaussian needs the ideal value (mid) and a spread σ > 0")
            m = np.exp(-((x - mid) ** 2) / (2 * sigma ** 2))
        elif fn == "near":
            if mid is None or not spread or spread <= 0:
                raise ValueError("Near needs the ideal value (mid) and a spread > 0")
            m = 1 / (1 + spread * (x - mid) ** 2)
        elif fn in ("small", "large"):
            if not mid or mid <= 0 or not spread or spread <= 0:
                raise ValueError(f"{fn.title()} needs a midpoint > 0 (membership 0.5 there) and a spread > 0 (steepness, e.g. 5)")
            r = np.where(x > 0, x / mid, np.nan)
            m = 1 / (1 + r ** (spread if fn == "small" else -spread))
            m = np.where(x <= 0, 1.0 if fn == "small" else 0.0, m)   # zero and below: as small as it gets
        elif fn == "sigmoid":
            if mid is None or not slope:
                raise ValueError("Sigmoid needs a midpoint (membership 0.5) and a slope (negative: decreasing)")
            m = 1 / (1 + np.exp(-slope * (x - mid)))
        else:
            raise ValueError(f"Unknown membership function {fn}: {', '.join(FUNCTIONS)}")
    m = np.clip(m, 0, 1)
    return np.where(np.isfinite(x), m, np.nan)


def suggest(stats: dict, fn: str, higher_is_better: bool = True) -> dict:
    """Starting parameters for a function from a layer's values (min, max, mean, p2, p98): what the panel fills in."""
    lo, hi = stats.get("p2", stats.get("min", 0.0)), stats.get("p98", stats.get("max", 1.0))
    mid = (lo + hi) / 2
    span = max(hi - lo, 1e-9)
    if fn == "linear":
        return {"a": lo, "b": hi} if higher_is_better else {"a": hi, "b": lo}
    if fn in ("small", "large"):
        return {"mid": mid if mid > 0 else max(hi / 2, 1e-6), "spread": 5}
    if fn == "gaussian":
        return {"mid": mid, "sigma": span / 4}
    if fn == "near":
        return {"mid": mid, "spread": 16 / span ** 2}
    if fn == "sigmoid":
        return {"mid": mid, "slope": (8 if higher_is_better else -8) / span}
    if fn == "trapezoid":
        return {"a": lo, "b": lo + span / 4, "c": hi - span / 4, "d": hi}
    return {}


def _params(spec: dict) -> dict:
    return {k: float(spec[k]) for k in ("a", "b", "c", "d", "mid", "spread", "sigma", "slope") if spec.get(k) is not None}


def _write(ref, out: Path, data: np.ndarray, desc: str, tags: dict, *, crs=None, transform=None) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    prof = {"driver": "GTiff", "width": data.shape[-1], "height": data.shape[-2], "count": 1 if data.ndim == 2 else data.shape[0],
            "dtype": "float32", "nodata": -9999.0, "compress": "deflate", "tiled": True, "blockxsize": 256, "blockysize": 256,
            "crs": crs if crs is not None else ref.crs, "transform": transform if transform is not None else ref.transform}
    arr = data if data.ndim == 3 else data[None]
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(np.where(np.isfinite(arr), arr, -9999.0).astype("float32"))
        for i in range(arr.shape[0]):
            dst.set_band_description(i + 1, desc if arr.shape[0] == 1 else f"{desc} {i + 1}")
        dst.update_tags(**{k: (json.dumps(v) if not isinstance(v, str) else v) for k, v in tags.items()})
    return str(out)


def _read(path, band: int = 1):
    src = rasterio.open(path)
    if src.width * src.height > MAX_PIXELS:
        src.close()
        raise ValueError(f"{Path(path).name} is too big ({src.width:,} × {src.height:,} pixels): clip or resample it first")
    if not 1 <= band <= src.count:
        src.close()
        raise ValueError(f"{Path(path).name} has {src.count} band(s): there is no band {band}")
    return src, src.read(band, masked=True).astype("float64").filled(np.nan)


def membership_raster(path, out: Path, fn: str, band: int = 1, **params) -> dict:
    src, x = _read(path, band)
    with src:
        m = membership(x, fn, **params)
        p = _write(src, out, m, f"Membership ({fn})", {"fuzzy": "membership", "function": fn, "params": params, "source": Path(path).name})
    v = m[np.isfinite(m)]
    return {"path": p, "function": fn, "params": params, "mean": round(float(v.mean()), 4) if v.size else None,
            "full_pct": round(100 * float((v >= 0.99).mean()), 2) if v.size else None}


# ------------------------------------------------------------------ fuzzy distance
def _metres(src_crs, transform, rows: int):
    """Pixel size in metres (x per row for degrees, y)."""
    rx, ry = abs(transform.a), abs(transform.e)
    if src_crs is not None and src_crs.is_geographic:
        lat = transform.f + transform.e * (np.arange(rows) + 0.5)
        return float(np.mean(rx * 111320.0 * np.cos(np.radians(lat)))), ry * 110574.0
    return rx, ry


def distance_raster(fc: dict, out: Path, *, like: str | None = None, res: float = 30.0, pad_m: float = 2000.0,
                    fn: str = "sigmoid", **params) -> dict:
    """Fuzzy distance to a layer's features: the distance in metres to the nearest one (0 inside polygons / on lines),
    turned into membership by fn (e.g. sigmoid mid 500, slope −0.01: 1 near, 0.5 at 500 m, ~0 beyond 1 km). The grid
    is `like`'s (a raster path), or a UTM grid of `res` metres around the features (padded by pad_m)."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds, transform_geom
    from scipy.ndimage import distance_transform_edt
    feats = [f for f in fc.get("features", []) if f.get("geometry")]
    if not feats:
        raise ValueError("The layer has no features to measure the distance to")
    if like:
        with rasterio.open(like) as r:
            crs, tr, shape = r.crs, r.transform, (r.height, r.width)
    else:
        from shapely.geometry import shape as shp
        from shapely.ops import unary_union
        c = unary_union([shp(f["geometry"]) for f in feats]).centroid
        crs = rasterio.crs.CRS.from_epsg((32600 if c.y >= 0 else 32700) + int((c.x + 180) // 6) % 60 + 1)
        w, s, e, n = transform_bounds("EPSG:4326", crs, *unary_union([shp(f["geometry"]) for f in feats]).bounds)
        w, s, e, n = w - pad_m, s - pad_m, e + pad_m, n + pad_m
        cols, rows = math.ceil((e - w) / res), math.ceil((n - s) / res)
        if cols * rows > MAX_PIXELS:
            raise ValueError(f"The grid would be {cols:,} × {rows:,} pixels: make the pixel size bigger")
        tr, shape = from_origin(w, n, res, res), (rows, cols)
    geoms = [transform_geom("EPSG:4326", crs, f["geometry"]) for f in feats]
    progress.update(0.2, "Distance to the features")
    burned = rasterize(((g, 1) for g in geoms), out_shape=shape, transform=tr, fill=0, all_touched=True, dtype="uint8")
    if not burned.any():
        raise ValueError("None of the features is inside the grid")
    mx, my = _metres(crs, tr, shape[0])
    dist = distance_transform_edt(burned == 0, sampling=(my, mx))
    m = membership(dist, fn, **params)
    progress.update(0.9, "Writing")
    p = _write(None, out, m, f"Fuzzy distance ({fn})", {"fuzzy": "distance", "function": fn, "params": params}, crs=crs, transform=tr)
    dpath = out.with_name(out.stem + "_metres.tif")
    _write(None, dpath, dist, "Distance (m)", {"units": "m"}, crs=crs, transform=tr)
    return {"path": p, "distance_path": str(dpath), "max_distance_m": round(float(dist.max()), 1), "function": fn, "params": params}


# ------------------------------------------------------------------ fuzzy overlay / suitability
def combine(ms: list[np.ndarray], op: str, weights: list[float] | None = None, gamma: float = 0.9) -> np.ndarray:
    """Fuzzy overlay of memberships (same shape). Weights are normalised to sum 1 (weighted operators)."""
    if op not in OPERATORS:
        raise ValueError(f"Operator: {', '.join(OPERATORS)}")
    S = np.stack(ms)
    w = np.asarray(weights if weights else [1.0] * len(ms), float)
    if (w < 0).any() or w.sum() <= 0:
        raise ValueError("Weights must be positive")
    w = w / w.sum()
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-empty pixels
        if op == "and":
            r = np.nanmin(S, 0)
        elif op == "or":
            r = np.nanmax(S, 0)
        elif op == "product":
            r = np.prod(S, 0)
        elif op == "sum":   # algebraic sum: 1 − ∏(1 − μ)
            r = 1 - np.prod(1 - S, 0)
        elif op == "gamma":
            if not 0 <= gamma <= 1:
                raise ValueError("Gamma is between 0 (product) and 1 (sum)")
            r = (1 - np.prod(1 - S, 0)) ** gamma * np.prod(S, 0) ** (1 - gamma)
        elif op == "weighted_sum":
            r = np.tensordot(w, S, axes=1)
        else:   # weighted product (weighted geometric mean): ∏ μᵢ^wᵢ
            r = np.prod(S ** w[:, None, None], 0)
    r = np.where(np.isfinite(S).all(0), r, np.nan)   # where a layer has no data, no answer
    return np.clip(r, 0, 1)


def overlay(layers: list[dict], out: Path, op: str = "gamma", gamma: float = 0.9, classes: bool = True) -> dict:
    """layers: [{path, band, fn, params, weight, name}] — each raster turned into membership by its function (fn
    "none": already 0–1), all on the first layer's grid, then combined by op. Writes the suitability (0–1) and, with
    classes, five suitability classes with their area."""
    if len(layers) < 1:
        raise ValueError("Add at least one layer")
    ref, ms, names = None, [], []
    try:
        for k, L in enumerate(layers):
            progress.update(0.05 + 0.6 * k / len(layers), f"Membership of {Path(L['path']).name}")
            src, x = _read(L["path"], int(L.get("band") or 1))
            with src:
                if ref is None:
                    ref = {"crs": src.crs, "transform": src.transform, "shape": x.shape, "src": rasterio.open(L["path"])}
                elif (src.crs, src.transform, x.shape) != (ref["crs"], ref["transform"], ref["shape"]):   # onto the first grid
                    y = np.full(ref["shape"], np.nan)
                    reproject(x, y, src_transform=src.transform, src_crs=src.crs, dst_transform=ref["transform"], dst_crs=ref["crs"],
                              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
                    x = y
            ms.append(membership(x, L.get("fn", "none"), **_params(L.get("params") or {})))
            names.append(L.get("name") or Path(L["path"]).stem)
        progress.update(0.7, "Combining")
        weights = [float(L.get("weight", 1) or 0) for L in layers]
        r = combine(ms, op, weights, gamma)
        src = ref["src"]
        meta = {"fuzzy": "overlay", "operator": op, "gamma": gamma, "layers": [{"name": n, "fn": L.get("fn"), "weight": w, "params": L.get("params")}
                                                                            for n, L, w in zip(names, layers, weights)]}
        p = _write(src, out, r, "Suitability (0–1)", meta)
        res = {"path": p, "operator": op, "layers": len(layers), "outputs": [p]}
        v = r[np.isfinite(r)]
        res.update(mean=round(float(v.mean()), 4) if v.size else None, max=round(float(v.max()), 4) if v.size else None)
        if classes and v.size:
            mx, my = _metres(src.crs, src.transform, r.shape[0])
            px_ha = mx * my / 1e4
            cls = np.zeros(r.shape, "uint8")
            lo = -0.01
            rows = []
            for i, (hi, name, col) in enumerate(CLASSES, start=1):
                sel = np.isfinite(r) & (r > lo) & (r <= hi) if i > 1 else np.isfinite(r) & (r <= hi)
                cls[sel] = i
                rows.append({"value": i, "class": name, "range": f"{max(lo, 0):.1f}–{min(hi, 1):.1f}", "area_ha": round(float(sel.sum() * px_ha), 3),
                             "pct": round(100 * float(sel.sum()) / v.size, 2), "color": col})
                lo = hi
            cp = out.with_name(out.stem + "_classes.tif")
            prof = src.profile.copy()
            prof.update(driver="GTiff", dtype="uint8", count=1, nodata=0, compress="deflate", photometric="palette")
            with rasterio.open(cp, "w", **prof) as d:
                d.write_colormap(1, {0: (0, 0, 0, 0), **{i: tuple(int(c[k:k + 2], 16) for k in (1, 3, 5)) + (255,) for i, (_, _, c) in enumerate(CLASSES, start=1)}})
                d.write(cls, 1)
                d.update_tags(classes=json.dumps({i: n for i, (_, n, _) in enumerate(CLASSES, start=1)}))
                d.set_band_description(1, "Suitability class")
            res.update(classes=rows, outputs=[str(cp), p])
        progress.update(1, "Done")
        return res
    finally:
        if ref and ref.get("src"):
            ref["src"].close()


# ------------------------------------------------------------------ fuzzy boundaries and uncertainty
def densify(coords: list, step: float) -> list:
    """Points every `step` along a line (the original vertices kept)."""
    pts = np.asarray(coords, float)
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.ceil(np.hypot(*(b - a)) / step)))
        out += [a + (b - a) * k / n for k in range(1, n + 1)]
    return np.asarray(out).tolist()


def chaikin(coords: list, iterations: int = 2, closed: bool = True, step: float | None = None) -> list:
    """Chaikin corner cutting: a smoother line through the same shape (each pass cuts every corner at ¼ and ¾ of its
    segments). With `step` (e.g. the pixel size) the line is first cut into pieces that long, so only the pixel
    staircase is rounded, not the shape: long straight edges keep their corners within about a pixel."""
    pts = np.asarray(densify(coords, step) if step else coords, float)
    for _ in range(iterations):
        if len(pts) < 3:
            break
        p = pts[:-1] if closed and np.allclose(pts[0], pts[-1]) else pts
        q = np.roll(p, -1, axis=0) if closed else p[1:]
        p0 = p if closed else p[:-1]
        a, b = 0.75 * p0 + 0.25 * q, 0.25 * p0 + 0.75 * q
        new = np.empty((len(a) * 2, 2))
        new[0::2], new[1::2] = a, b
        if not closed:
            new = np.vstack([pts[0], new, pts[-1]])
        pts = np.vstack([new, new[:1]]) if closed else new
    return pts.tolist()


def boundary(path, out_dir: Path, *, band: int = 1, alpha: float = 0.5, cuts: list[float] | None = None, low: float = 0.25,
             high: float = 0.75, blur: float = 1.0, smooth: int = 2, min_area_m2: float = 0.0, simplify_m: float = 0.0,
             name: str | None = None) -> dict:
    """From a membership (or probability) raster: the uncertainty map (1 at μ = 0.5, 0 at 0 or 1), the zones above each
    α-cut (nested polygons, e.g. 0.25 / 0.5 / 0.75), the crisp zone at `alpha`, and the transition zone (low ≤ μ < high),
    as polygons smoothed by a Gaussian blur of the membership (blur, pixels) and Chaikin corner cutting (smooth passes)."""
    from rasterio.features import shapes
    from rasterio.warp import transform_geom
    from scipy.ndimage import gaussian_filter
    from shapely.geometry import Polygon, mapping, shape
    src, mu = _read(path, band)
    with src:
        fin = np.isfinite(mu)
        if not fin.any():
            raise ValueError("The raster has no values")
        if np.nanmin(mu) < -0.01 or np.nanmax(mu) > 1.01:
            raise ValueError(f"The values run from {np.nanmin(mu):.3g} to {np.nanmax(mu):.3g}: a membership map is 0–1 (make one with Fuzzy membership)")
        mu = np.clip(mu, 0, 1)
        if blur > 0:   # smooth the field, not the polygons' staircase afterwards: the boundary follows the gradient
            filled = np.where(fin, mu, 0.0)
            wts = gaussian_filter(fin.astype(float), blur)
            with np.errstate(invalid="ignore", divide="ignore"):
                mu_s = np.where(fin, gaussian_filter(filled, blur) / wts, np.nan)
        else:
            mu_s = mu
        unc = 1 - np.abs(2 * mu - 1)
        stem = name or Path(path).stem
        out_dir.mkdir(parents=True, exist_ok=True)
        up = _write(src, out_dir / f"{stem}_uncertainty.tif", np.where(fin, unc, np.nan), "Uncertainty (1 − |2μ − 1|)", {"fuzzy": "uncertainty"})
        mx, my = _metres(src.crs, src.transform, mu.shape[0])
        px_m2 = mx * my
        px = max(abs(src.transform.a), abs(src.transform.e))   # smoothing works at the pixel's scale
        to_ll = (lambda g: transform_geom(src.crs, "EPSG:4326", g)) if src.crs and src.crs.to_epsg() != 4326 else (lambda g: g)
        metre_units = not (src.crs and src.crs.is_geographic)

        # the boundaries are traced on a finer, smoothly interpolated copy of the membership (up to 4× finer): the line
        # follows the α level between pixel centres instead of the pixel staircase, then a light simplify + Chaikin
        from rasterio.transform import Affine
        from scipy.ndimage import zoom
        f_up = int(max(1, min(4, math.sqrt(16e6 / mu.size)))) if smooth or blur else 1
        if f_up > 1:
            mu_f = zoom(np.where(np.isfinite(mu_s), mu_s, -1.0), f_up, order=1)
            fin_f = zoom(fin.astype("uint8"), f_up, order=0).astype(bool)
            mu_f = np.where(fin_f & (mu_f >= 0), mu_f, np.nan)
        else:
            mu_f, fin_f = mu_s, fin
        tr_f = src.transform * Affine.scale(1 / f_up)
        step = px / f_up

        def polys(mask: np.ndarray, props: dict) -> list:
            out = []
            for geom, v in shapes(mask.astype("uint8"), mask=mask, transform=tr_f, connectivity=8):
                if not v:
                    continue
                g = shape(geom)
                if smooth:   # drop the fine grid's stair vertices (half a fine pixel), then round what is left
                    g = g.simplify(step * 0.75, preserve_topology=True)
                    g = shape({"type": "Polygon", "coordinates": [chaikin(list(r.coords), smooth, step=step * 2) for r in [g.exterior, *g.interiors]]}) if g.geom_type == "Polygon" else g
                if not g.is_valid:
                    g = g.buffer(0)
                if simplify_m and metre_units:
                    g = g.simplify(simplify_m, preserve_topology=True)
                area = g.area * (1 if metre_units else px_m2 / (abs(src.transform.a) * abs(src.transform.e)))
                if area < min_area_m2 or g.is_empty:
                    continue
                out.append({"type": "Feature", "geometry": to_ll(mapping(g)), "properties": {**props, "area_ha": round(area / 1e4, 4)}})
            return out

        progress.update(0.3, "Zones")
        cuts = sorted(set([float(c) for c in (cuts or [0.25, 0.5, 0.75])] + [float(alpha)]))
        zones = []
        for c in cuts:
            zones += polys(fin_f & (mu_f >= c), {"alpha": c, "zone": f"μ ≥ {c:g}", "class": f"μ ≥ {c:g}"})
        crisp = [f for f in zones if f["properties"]["alpha"] == alpha]
        progress.update(0.7, "Transition zone")
        trans = polys(fin_f & (mu_f >= low) & (mu_f < high), {"zone": f"transition {low:g}–{high:g}", "class": "Transition zone"})
        files = []
        for nm, feats in ((f"{stem}_zones", zones), (f"{stem}_boundary_{alpha:g}", crisp), (f"{stem}_transition", trans)):
            fp = out_dir / f"{nm}.geojson"
            fp.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
            files.append(str(fp))
        v = mu[fin]
        stats = {"uncertain_pct": round(100 * float(((v >= low) & (v < high)).mean()), 2), "crisp_ha": round(float((mu_s[fin] >= alpha).sum() * px_m2 / 1e4), 3),
                 "transition_ha": round(sum(f["properties"]["area_ha"] for f in trans), 3), "mean_uncertainty": round(float(unc[fin].mean()), 4)}
    progress.update(1, "Done")
    return {"uncertainty": up, "zones": files[0], "boundary": files[1], "transition": files[2], "cuts": cuts, "alpha": alpha, **stats}


# ------------------------------------------------------------------ fuzzy c-means
def _fcm(X: np.ndarray, k: int, m: float, iters: int, tol: float, rng) -> tuple[np.ndarray, np.ndarray]:
    """Fuzzy c-means on rows of X: (centres k × bands, memberships n × k)."""
    U = rng.dirichlet(np.ones(k), size=len(X))
    C = None
    for _ in range(iters):
        Um = U ** m
        C = (Um.T @ X) / Um.sum(0)[:, None]
        D = np.maximum(((X[:, None, :] - C[None]) ** 2).sum(2), 1e-12)
        inv = D ** (-1 / (m - 1))
        Un = inv / inv.sum(1, keepdims=True)
        if np.abs(Un - U).max() < tol:
            U = Un
            break
        U = Un
    return C, U


def cmeans(path, out_dir: Path, k: int = 4, *, bands: list[int] | None = None, m: float = 2.0, iters: int = 150,
           sample: int = 60000, seed: int = 0, name: str | None = None) -> dict:
    """Fuzzy classification: each pixel's membership in k classes (fuzzy c-means on the standardised bands), the hard
    class (largest membership) and the uncertainty (1 − (largest − second largest), the confusion index)."""
    if not 2 <= k <= 12:
        raise ValueError("2 to 12 classes")
    if m <= 1:
        raise ValueError("The fuzziness m must be above 1 (2 is usual)")
    with rasterio.open(path) as src:
        if src.width * src.height > MAX_PIXELS:
            raise ValueError("The raster is too big: clip or resample it first")
        bands = bands or list(range(1, src.count + 1))
        A = np.stack([src.read(b, masked=True).astype("float64").filled(np.nan) for b in bands])
        prof = src.profile.copy()
        crs, tr = src.crs, src.transform
    ok = np.isfinite(A).all(0)
    if ok.sum() < k * 10:
        raise ValueError("Too few pixels with values in every band")
    X = A[:, ok].T
    mean, std = X.mean(0), X.std(0)
    X = (X - mean) / np.where(std > 0, std, 1)
    rng = np.random.default_rng(seed)
    S = X[rng.choice(len(X), min(sample, len(X)), replace=False)]
    progress.update(0.1, "Fuzzy c-means")
    C, _ = _fcm(S, k, m, iters, 1e-5, rng)
    order = np.argsort(C[:, 0])   # classes numbered by their first band (stable from run to run)
    C = C[order]
    progress.update(0.6, "Memberships of every pixel")
    U = np.empty((len(X), k))
    for i in range(0, len(X), 200000):
        D = np.maximum(((X[i:i + 200000, None, :] - C[None]) ** 2).sum(2), 1e-12)
        inv = D ** (-1 / (m - 1))
        U[i:i + 200000] = inv / inv.sum(1, keepdims=True)
    mem = np.full((k, *ok.shape), np.nan)
    mem[:, ok] = U.T
    srt = np.sort(U, 1)
    unc = np.full(ok.shape, np.nan)
    unc[ok] = 1 - (srt[:, -1] - srt[:, -2])
    hard = np.zeros(ok.shape, "uint8")
    hard[ok] = U.argmax(1) + 1
    stem = name or f"{Path(path).stem}_fcm{k}"
    out_dir.mkdir(parents=True, exist_ok=True)
    mp = _write(None, out_dir / f"{stem}_memberships.tif", mem, "Membership class", {"fuzzy": "cmeans", "k": k, "m": m}, crs=crs, transform=tr)
    with rasterio.open(mp, "r+") as d:
        for i in range(k):
            d.set_band_description(i + 1, f"Class {i + 1} membership")
    upath = _write(None, out_dir / f"{stem}_uncertainty.tif", unc, "Uncertainty (confusion index)", {"fuzzy": "uncertainty"}, crs=crs, transform=tr)
    pal = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#393b79", "#637939"]
    hp = out_dir / f"{stem}_classes.tif"
    prof.update(driver="GTiff", dtype="uint8", count=1, nodata=0, compress="deflate", photometric="palette", crs=crs, transform=tr)
    for key in ("blockxsize", "blockysize", "tiled", "interleave"):
        prof.pop(key, None)
    with rasterio.open(hp, "w", **prof) as d:
        d.write_colormap(1, {0: (0, 0, 0, 0), **{i + 1: tuple(int(pal[i][j:j + 2], 16) for j in (1, 3, 5)) + (255,) for i in range(k)}})
        d.write(hard, 1)
        d.update_tags(classes=json.dumps({i + 1: f"Class {i + 1}" for i in range(k)}))
        d.set_band_description(1, "Fuzzy class (largest membership)")
    centres = (C * np.where(std > 0, std, 1) + mean).round(4).tolist()
    progress.update(1, "Done")
    counts = np.bincount(hard[ok], minlength=k + 1)[1:]
    return {"outputs": [str(hp), mp, upath], "k": k, "m": m, "centres": centres, "bands": bands,
            "mixed_pct": round(100 * float((unc[ok] > 0.5).mean()), 2),
            "classes": [{"class": i + 1, "pixels": int(n), "pct": round(100 * float(n) / float(ok.sum()), 2), "color": pal[i]} for i, n in enumerate(counts)]}
