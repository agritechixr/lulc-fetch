"""The SAR workflow: the steps a user ticks, run in the right order for what the input is.

    search(aoi, start, end, …)       Sentinel-1 GRD / RTC scenes on Planetary Computer, filtered (polarisation, mode,
                                     orbit direction, relative orbit) so a series is homogeneous
    process(src, out_dir, opts)      a GRD (.SAFE / .zip / Planetary Computer scene), an RTC scene, or a processed SAR
                                     raster → analysis-ready backscatter (linear power, and dB if asked)

GRD chain (each step only if ticked and still needed): product check → orbit file (precise, else restituted) →
border noise → thermal noise → calibration (σ⁰ / β⁰ / γ⁰) → speckle filter (radar geometry, after multilooking to the
output pixel size) → Range-Doppler terrain correction with a DEM (or ellipsoid geocoding without) → terrain flattening
(γ⁰: area-based, Small 2011, or angular, Vollrath 2020) → incidence-angle normalisation → dB → onto the output grid
(reprojection, resampling, alignment) → clip. Steps already done by the data's producer (RTC, GEE, ASF …) are skipped,
never applied twice.

The map grid is processed in tiles (TILE × TILE pixels, each with its own window of the radar image and its own DEM),
so a whole scene fits in memory. Every result comes with a quality layer (CEOS analysis-ready data style: valid,
layover, shadow, below the noise floor, outside the area, no data) and a metadata .json.

    join_frames(results, …)          consecutive frames (slices) of one pass, processed onto one grid, joined
    multitemporal(paths, size)       Quegan's multi-temporal speckle filter of several dates (rewrites them)
    neighbours(item, aoi)            the other frames of the same pass that the area needs
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling, reproject, transform, transform_bounds, transform_geom

from .. import progress
from . import geometry as G
from . import product as P
from . import radiometry as R
from . import speckle as SP

log = logging.getLogger(__name__)
MAX_PIXELS = 1_500_000_000   # a whole IW scene at 10 m is ~0.45 billion
TILE = 2048
STEP_NAMES = {k: t for k, t, _ in P.STEPS}


# ------------------------------------------------------------------ finding scenes
def search(aoi: dict, start: str, end: str, *, collection: str = "rtc", pols: str = "VV+VH", mode: str = "IW",
           orbit: str | None = None, rel_orbit: int | None = None, limit: int = 60) -> list[dict]:
    """Planetary Computer Sentinel-1 scenes over aoi (GeoJSON geometry) between start and end (YYYY-MM-DD), homogeneous:
    one polarisation pair, one mode, optionally one orbit direction and relative orbit."""
    import planetary_computer as pc
    import pystac_client
    from shapely.geometry import shape
    col = {"rtc": "sentinel-1-rtc", "grd": "sentinel-1-grd"}[collection]
    cat = pystac_client.Client.open(P.PC_STAC, modifier=pc.sign_inplace, timeout=60)
    q = {"sar:instrument_mode": {"eq": mode}}
    if orbit in ("ascending", "descending"):
        q["sat:orbit_state"] = {"eq": orbit}
    if rel_orbit:
        q["sat:relative_orbit"] = {"eq": int(rel_orbit)}
    items = cat.search(collections=[col], intersects=aoi, datetime=f"{start}/{end}", query=q, max_items=limit * 3).items()
    a = shape(aoi)
    want = set(pols.split("+")) if pols else set()
    out = []
    for it in items:
        pr = it.properties
        if want and not want <= set(pr.get("sar:polarizations", [])):
            continue
        cover = shape(it.geometry).intersection(a).area / a.area if it.geometry and a.area else None
        out.append({"id": it.id, "collection": col, "date": (pr.get("datetime") or pr.get("start_datetime") or "")[:10],
                    "time": (pr.get("datetime") or pr.get("start_datetime") or "")[11:19], "platform": it.id[:3],
                    "orbit_direction": pr.get("sat:orbit_state"), "relative_orbit": pr.get("sat:relative_orbit"),
                    "polarisations": pr.get("sar:polarizations"), "mode": pr.get("sar:instrument_mode"),
                    "coverage": round(100 * cover, 1) if cover is not None else None, "item": it.to_dict()})
        if len(out) >= limit:
            break
    return sorted(out, key=lambda r: r["date"])


# ------------------------------------------------------------------ the output grid
def utm_of(lon: float, lat: float) -> CRS:
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + int((lon + 180) // 6) % 60 + 1)


def output_grid(*, aoi: dict | None, footprint: tuple | None, crs: str | None, res: float, like: str | None) -> dict:
    """The map grid: like's grid (aligned), or `res` metres in crs (UTM of the area when "auto") over the AOI (or the
    scene's footprint, a lon / lat box)."""
    if like:
        with rasterio.open(like) as s:
            return {"crs": s.crs, "transform": s.transform, "shape": (s.height, s.width), "aligned_to": Path(like).name}
    if aoi:
        from shapely.geometry import shape
        b = shape(aoi).bounds
    elif footprint:
        b = footprint
    else:
        raise ValueError("Choose an area (or a layer to align to)")
    lon, lat = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    dst = utm_of(lon, lat) if not crs or crs == "auto" else CRS.from_user_input(crs)
    w, s_, e, n = transform_bounds("EPSG:4326", dst, *b, densify_pts=21)
    if dst.is_geographic and res > 1:   # metres asked in a degree CRS
        res = res / 111320.0
    w, n = math.floor(w / res) * res, math.ceil(n / res) * res
    width, height = max(1, math.ceil((e - w) / res)), max(1, math.ceil((n - s_) / res))
    if width * height > MAX_PIXELS:
        raise ValueError(f"The output would be {width:,} × {height:,} pixels: choose a smaller area or a bigger pixel size")
    return {"crs": dst, "transform": from_origin(w, n, res, res), "shape": (height, width)}


def _sub(grid, r: int, c: int, h: int, w: int) -> dict:
    """The part of grid starting at row r, column c (negative: beyond its edge), h × w pixels."""
    return {"crs": grid["crs"], "transform": grid["transform"] * Affine.translation(c, r), "shape": (h, w)}


def _tiles(shape, size: int | None = None):
    h, w = shape
    size = size or TILE
    for r in range(0, h, size):
        for c in range(0, w, size):
            yield r, c, min(size, h - r), min(size, w - c)


def _lonlat(grid, r0: int, r1: int):
    h, w = grid["shape"]
    tr = grid["transform"]
    cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(r0, r1) + 0.5)
    xs, ys = tr * (cols, rows)
    if grid["crs"].to_epsg() == 4326:
        return xs, ys
    lon, lat = transform(grid["crs"], "EPSG:4326", xs.ravel(), ys.ravel())
    return np.reshape(lon, xs.shape), np.reshape(lat, xs.shape)


def _clip_mask(grid, aoi):
    from rasterio.features import geometry_mask
    return geometry_mask([transform_geom("EPSG:4326", grid["crs"], aoi)], out_shape=grid["shape"], transform=grid["transform"], invert=True)


def _touches(grid, aoi) -> bool:
    """Whether a tile touches the area at all (tiles outside it are skipped)."""
    from shapely.geometry import box, shape
    from rasterio.transform import array_bounds
    return shape(aoi).intersects(box(*transform_bounds(grid["crs"], "EPSG:4326", *array_bounds(*grid["shape"], grid["transform"]), densify_pts=5)))


# ------------------------------------------------------------------ results: written tile by tile, with a quality layer
QUALITY = {0: "No data", 1: "Valid", 2: "Layover", 3: "Shadow", 4: "Below the noise floor", 5: "Outside the area"}
QCOLORS = {0: (0, 0, 0, 0), 1: (210, 210, 210, 40), 2: (230, 85, 13, 255), 3: (49, 54, 149, 255), 4: (160, 90, 200, 255), 5: (0, 0, 0, 0)}


class _Writer:
    """The result files of one input: linear power (always), dB, incidence angles, quality; written a tile at a time."""

    def __init__(self, grid, out_dir: Path, stem: str, pols: list, *, db=True, angles=False, quality=True):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.grid, self.pols, self.stem, self.out_dir = grid, pols, stem, out_dir
        h, w = grid["shape"]
        base = {"driver": "GTiff", "width": w, "height": h, "crs": grid["crs"], "transform": grid["transform"], "compress": "deflate", "BIGTIFF": "IF_SAFER"}
        if h >= 256 and w >= 256:
            base.update(tiled=True, blockxsize=256, blockysize=256)
        f32 = {**base, "dtype": "float32", "nodata": np.nan}
        want = {"linear": (pols, f32), **({"dB": ([f"{p}_dB" for p in pols], f32)} if db else {}),
                **({"angles": (["incidence", "local_incidence"], f32)} if angles else {}),
                **({"quality": (["Quality"], {**base, "dtype": "uint8", "nodata": 0, "photometric": "palette"})} if quality else {})}
        self.paths, self.ds = {}, {}
        for k, (names, prof) in want.items():
            self.paths[k] = out_dir / f"{stem}_{k}.tif"
            d = rasterio.open(self.paths[k], "w", count=len(names), **prof)
            if k == "quality":
                d.write_colormap(1, QCOLORS)
            for i, n in enumerate(names, 1):
                d.set_band_description(i, n)
            self.ds[k] = d
        self.counts = np.zeros(len(QUALITY), "int64")

    def write(self, r: int, c: int, bands: dict, angles: dict | None = None, quality: np.ndarray | None = None):
        from rasterio.windows import Window
        h, w = next(iter(bands.values())).shape
        win = Window(c, r, w, h)
        for i, p in enumerate(self.pols, 1):
            v = bands[p].astype("float32")
            self.ds["linear"].write(v, i, window=win)
            if "dB" in self.ds:
                with np.errstate(divide="ignore", invalid="ignore"):
                    self.ds["dB"].write((10 * np.log10(np.where(v > 0, v, np.nan))).astype("float32"), i, window=win)
        if "angles" in self.ds:
            for i, k in enumerate(("incidence", "local_incidence"), 1):
                self.ds["angles"].write((angles or {}).get(k, np.full((h, w), np.nan, "float32")).astype("float32"), i, window=win)
        if quality is None:
            quality = np.where(np.any([np.isfinite(b) for b in bands.values()], 0), 1, 0).astype("uint8")
        if "quality" in self.ds:
            self.ds["quality"].write(quality.astype("uint8"), 1, window=win)
        self.counts += np.bincount(quality.ravel(), minlength=len(QUALITY))[:len(QUALITY)]

    def close(self, tags: dict, done: list) -> list[str]:
        units = tags.get("units_linear", "")
        for k, d in self.ds.items():
            t = {kk: (v if isinstance(v, str) else json.dumps(v)) for kk, v in tags.items()}
            if k == "linear":
                t.update(units=units, sar_steps=",".join(done))
            elif k == "dB":
                t.update(units=units.replace("linear power", "dB"), sar_steps=",".join(done + ["db"]))
            elif k == "angles":
                t = {"units": "degrees", "sar_date": tags.get("sar_date", "")}
            else:
                t = {"classes": json.dumps(QUALITY), "sar_date": tags.get("sar_date", "")}
            d.update_tags(**t)
            d.close()
        order = [k for k in ("dB", "linear", "angles", "quality") if k in self.paths]
        return [str(self.paths[k]) for k in order]

    def quality_pct(self) -> dict:
        n = int(self.counts[1:].sum())
        return {QUALITY[i]: round(100 * int(self.counts[i]) / n, 2) for i in range(1, len(QUALITY)) if n and self.counts[i]} if n else {}


def _metadata(out_dir: Path, stem: str, meta: dict) -> str:
    """The run's description beside the files (CEOS ARD-style: source, processing, geometry, radiometry, quality)."""
    p = out_dir / f"{stem}_metadata.json"
    p.write_text(json.dumps(meta, indent=2, default=str))
    return str(p)


def _sample(img: np.ndarray, rl: np.ndarray, rp: np.ndarray) -> np.ndarray:
    """img (radar geometry) at fractional (row, column) positions, bilinear, NaN-aware; up to a pixel past the window's
    edge takes the edge's value (where the image ends, e.g. at a frame's last line: see _grd_tile)."""
    from scipy.ndimage import map_coordinates
    ok = np.isfinite(img)
    vals = map_coordinates(np.where(ok, img, 0), [rl, rp], order=1, mode="nearest")
    wts = map_coordinates(ok.astype("float32"), [rl, rp], order=1, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(wts > 0.5, vals / wts, np.nan)


def _splat(rl: np.ndarray, rp: np.ndarray, gp: np.ndarray, cell_area: float, shape) -> np.ndarray:
    """Illuminated area (m², projected perpendicular to the look direction) collected by every radar pixel: each DEM
    cell's area × its gamma projection, from four sub-cells (2× oversampled, so stretched back slopes leave no holes),
    spread bilinearly over the radar pixels around where it lands (Small 2011)."""
    from scipy.ndimage import map_coordinates
    nl, nr = shape
    acc = np.zeros(nl * nr)
    a = np.where(np.isfinite(gp), np.maximum(gp, 0), 0).astype("float64") * cell_area / 4
    yy, xx = np.mgrid[0:rl.shape[0], 0:rl.shape[1]].astype("float32")
    rl0, rp0 = np.nan_to_num(rl, nan=-1e6), np.nan_to_num(rp, nan=-1e6)
    for dy in (-0.25, 0.25):
        for dx in (-0.25, 0.25):
            y = map_coordinates(rl0, [yy + dy, xx + dx], order=1, mode="nearest")
            x = map_coordinates(rp0, [yy + dy, xx + dx], order=1, mode="nearest")
            y0, x0 = np.floor(y), np.floor(x)
            fy, fx = y - y0, x - x0
            for oy, wy in ((0, 1 - fy), (1, fy)):
                for ox, wx in ((0, 1 - fx), (1, fx)):
                    yi, xi = y0 + oy, x0 + ox
                    ok = (yi >= 0) & (yi < nl) & (xi >= 0) & (xi < nr)
                    acc += np.bincount((yi[ok] * nr + xi[ok]).astype("int64"), weights=(a * wy * wx)[ok], minlength=nl * nr)
    return acc.reshape(nl, nr)


# ------------------------------------------------------------------ GRD
def _az_spacing(g) -> np.ndarray:
    """The true ground distance between image lines (m), as a straight line across the swath (by pixel), from the
    product's geolocation grid: about 10.2 m for IW GRDH, where the annotation says 10 m (1.8 % = 0.08 dB in the
    area-based flattening)."""
    G_ = g.grid
    px, d = [], []
    for p in np.unique(G_[:, 1]):
        s = G_[G_[:, 1] == p]
        s = s[np.argsort(s[:, 0])]
        if len(s) < 2:
            continue
        la, lo = np.radians(s[:, 2]), np.radians(s[:, 3])
        a = np.sin(np.diff(la) / 2) ** 2 + np.cos(la[:-1]) * np.cos(la[1:]) * np.sin(np.diff(lo) / 2) ** 2
        dist = 2 * (6371008.8 + s[:-1, 4]) * np.arcsin(np.sqrt(a))
        px += [p] * len(dist)
        d += list(dist / np.diff(s[:, 0]))
    if len(set(px)) < 2:
        return np.array([0.0, g.azimuth_spacing])
    return np.polyfit(px, d, 1)


def _grd_tile(T: dict, tg: dict):
    """One tile of the map grid from the GRD product: its radar window, radiometry, speckle, geometry, flattening,
    normalisation and quality. Returns (bands, angles, quality)."""
    g, rd, pols, (la, lr) = T["g"], T["rd"], T["pols"], T["looks"]
    h, w = tg["shape"]
    nan = lambda: np.full((h, w), np.nan, "float32")   # noqa: E731
    empty = ({p: nan() for p in pols}, {"incidence": nan(), "local_incidence": nan()}, np.zeros((h, w), "uint8"))
    if T["clip_aoi"] and not _touches(tg, T["clip_aoi"]):
        return empty
    m = T["margin"]
    E = _sub(tg, -m, -m, h + 2 * m, w + 2 * m)
    eh, ew = E["shape"]
    # the part of the radar image this tile needs (from points over it, at the scene's mean height), with a margin
    pts = [_lonlat(E, r, r + 1) for r in np.unique(np.linspace(0, eh - 1, 24).astype(int))]
    lon_b = np.concatenate([p[0][0, :: max(1, ew // 24)] for p in pts])
    lat_b = np.concatenate([p[1][0, :: max(1, ew // 24)] for p in pts])
    line, pix, _, _ = rd.locate(lat_b, lon_b, np.full(lat_b.shape, T["mean_h"]))
    mg = 300 + T["sp_size"] * max(la, lr)
    r0, r1 = int(max(0, np.floor(np.nanmin(line)) - mg)), int(min(g.lines, np.ceil(np.nanmax(line)) + mg))
    c0, c1 = int(max(0, np.floor(np.nanmin(pix)) - mg)), int(min(g.samples, np.ceil(np.nanmax(pix)) + mg))
    r0, c0 = r0 // la * la, c0 // lr * lr   # on the scene's own look grid: every tile multilooks the same pixels together
    if r1 - r0 < 2 * la or c1 - c0 < 2 * lr:
        return empty
    radar, nesz = {}, {}
    for k, pol in enumerate(T["pols"]):
        progress.update(0.05 + 0.35 * k / len(pols), f"{pol}: reading and calibrating the radar image")
        radar[pol], nesz[pol] = R.read_window(T["prod"], pol, (r0, c0, r1 - r0, c1 - c0), (la, lr), thermal=T["thermal"], border=T["border"],
                                              kind=T["kind"], nesz=True)
    if not any(np.isfinite(v).any() for v in radar.values()):
        return empty
    sp = T["speckle"]
    if sp:
        for pol in pols:
            progress.update(0.42, f"{pol}: speckle filter ({sp['method']})")
            radar[pol] = SP.filter_image(radar[pol], sp["method"], int(sp.get("size") or 5), float(sp.get("looks") or T["enl"])).astype("float32")
    low = None
    if T["thermal"] and T["kind"] != "dn":
        low = np.zeros(radar[pols[0]].shape, bool)
        for pol in pols:
            if nesz[pol] is not None:
                with np.errstate(invalid="ignore"):
                    low |= radar[pol] < nesz[pol]
    # geometry of every cell of the (expanded) tile
    use_dem, area = T["use_dem"], T["method"] == "area"
    if use_dem:
        progress.update(0.5, "DEM heights")
        dem, T["dem_name"] = G.dem_for(E["crs"], E["transform"], E["shape"], T["dem"], T["cache"])
        slope = G.slopes(dem, (T["res_m"], T["res_m"]))
    L = np.full((eh, ew), np.nan, "float32")
    Pix, TH, THL, AR, GP = (np.full((eh, ew), np.nan, "float32") for _ in range(5))
    block = max(16, 1_000_000 // max(ew, 1))
    for b0 in range(0, eh, block):
        b1 = min(eh, b0 + block)
        progress.update(0.55 + 0.35 * b0 / eh, f"{'Terrain correction' if use_dem else 'Geocoding'}: rows {b0}–{b1} of {eh}")
        lon, lat = _lonlat(E, b0, b1)
        if use_dem:
            N = G.geoid(lon, lat, T["cache"])
            T["geoid"] = T["geoid"] or bool(np.any(N != 0))
            hgt = np.where(np.isfinite(dem[b0:b1]), dem[b0:b1], 0) + N
        else:   # no DEM: the product's own (coarse) heights from its geolocation grid
            from scipy.interpolate import griddata
            hgt = griddata(g.grid[:, [3, 2]], g.grid[:, 4], (lon, lat), method="linear")
            hgt = np.where(np.isfinite(hgt), hgt, T["mean_h"])
        ln_, px_, S, Pp = rd.locate(lat, lon, hgt)
        L[b0:b1], Pix[b0:b1] = ln_, px_
        sl = (slope[0][b0:b1], slope[1][b0:b1]) if use_dem else None
        TH[b0:b1], THL[b0:b1], AR[b0:b1] = G.angles(lat, lon, S, Pp, sl)
        if area:
            GP[b0:b1] = G.gamma_projection(lat, lon, S, Pp, sl)
    rl = (L - r0 - (la - 1) / 2) / la
    rp = (Pix - c0 - (lr - 1) / 2) / lr
    core = (slice(m, m + h), slice(m, m + w))
    rlc, rpc = rl[core], rp[core]
    th, thl, ar = TH[core], THL[core], AR[core]
    # inside the image, and up to one multilooked pixel past its first / last line: consecutive frames of a pass are one
    # line apart, so without this a pixel-wide seam between them would have no data
    inside = (L[core] >= -la) & (L[core] <= g.lines - 1 + la) & (Pix[core] >= 0) & (Pix[core] <= g.samples - 1)
    bands = {pol: np.where(inside, _sample(radar[pol], rlc, rpc), np.nan) for pol in pols}
    ls = G.layover_shadow(th, ar) if use_dem else np.zeros((h, w), "uint8")
    lit = np.ones((h, w), bool)
    if T["method"] == "area":
        progress.update(0.92, "Terrain flattening (illuminated area)")
        Arad = _splat(rl, rp, GP, T["res_m"] ** 2, radar[pols[0]].shape)
        from scipy.ndimage import map_coordinates
        Ag = map_coordinates(Arad, [rlc, rpc], order=1, mode="nearest")
        t = np.radians(th)
        az = np.polyval(T["az_fit"], Pix[core])
        Ab = (az * la) * (g.range_spacing * lr) * np.sin(t)   # the radar pixel's area in the slant plane
        with np.errstate(invalid="ignore", divide="ignore"):
            lit = Ag > 0.05 * Ab / np.tan(t)   # at least 5 % of what flat ground would light
            f = np.where(lit & (ls != 2), Ab / Ag, np.nan)
        bands = {p: v * f for p, v in bands.items()}
    elif T["method"] == "angular":
        with np.errstate(invalid="ignore", divide="ignore"):
            f = np.where(ls == 0, 1 / np.cos(np.radians(th)) / G.flatten_factor(th, ar), np.nan)
        bands = {p: v * f for p, v in bands.items()}
    if T["normalise"]:
        nf = G.normalise_factor(thl if use_dem else th, T["norm_ref"], T["norm_n"])
        bands = {p: v * nf for p, v in bands.items()}
    bands = {p: v.astype("float32") for p, v in bands.items()}
    q = np.zeros((h, w), "uint8")
    q[inside & np.any([np.isfinite(v) for v in bands.values()], 0)] = 1
    if low is not None:
        from scipy.ndimage import map_coordinates
        lowc = map_coordinates(low.astype("float32"), [rlc, rpc], order=0, mode="constant", cval=0) > 0.5
        q[(q == 1) & lowc] = 4
    if use_dem:
        q[inside & (ls == 1)] = 2
        q[inside & ((ls == 2) | ~lit)] = 3
    if T["clip_aoi"]:
        cm = _clip_mask(tg, T["clip_aoi"])
        bands = {p: np.where(cm, v, np.nan) for p, v in bands.items()}
        q[~cm & (q > 0)] = 5
        th, thl = np.where(cm, th, np.nan), np.where(cm, thl, np.nan)
    angles = {"incidence": np.where(inside, th, np.nan), "local_incidence": np.where(inside, thl, np.nan) if use_dem else np.where(inside, th, np.nan)}
    return bands, angles, q


def _process_grd(prod: P.Product, out_dir: Path, opts: dict, cache: Path | None) -> dict:
    steps, info = set(opts.get("steps") or []), {}
    g = prod.geometry
    pols = [p for p in (opts.get("pols") or sorted(prod.measurement)) if p in prod.measurement and p in prod.calibration]
    if not pols:
        raise ValueError("The product has none of the chosen polarisations (with calibration files)")
    t0, t1 = g.first_line, g.first_line + (g.lines - 1) * g.line_interval
    done = ["validate"]
    sv, src = g.orbit, "the product's own (restituted / predicted)"
    if "orbit" in steps:
        progress.update(0.01, "Orbit file (ESA)")
        po = G.precise_orbit(prod.name[:3], t0, t1, cache)
        if po is not None:
            sv, kind_o = po
            src = "precise (POEORB, ESA)" if kind_o == "POEORB" else "restituted (RESORB, ESA)"
            done.append("orbit")
            if kind_o == "RESORB":
                info["orbit_note"] = "Restituted orbit (RESORB, ~10 cm): the precise one (POEORB, ~5 cm) comes ~20 days after acquisition"
        else:
            info["orbit_note"] = "No orbit file from ESA yet (or ESA wasn't reachable): the product's own orbit was used"
    orbit = G.Orbit(sv, t0, t1, src)
    rd = G.RangeDoppler(g, orbit)
    footprint = opts.get("bounds") or (g.grid[:, 3].min(), g.grid[:, 2].min(), g.grid[:, 3].max(), g.grid[:, 2].max())
    grid = output_grid(aoi=opts.get("aoi"), footprint=footprint, crs=opts.get("crs"), res=float(opts.get("res") or 20), like=opts.get("like"))
    res_m = abs(grid["transform"].a) * (111320 if grid["crs"].is_geographic else 1)
    looks = (max(1, round(res_m / g.azimuth_spacing)), max(1, round(res_m / g.range_spacing)))
    use_dem = "terrain" in steps
    method = (opts.get("flatten_method") or "area") if "flatten" in steps else None
    if method and not use_dem:
        raise ValueError("Terrain flattening needs terrain correction (a DEM): tick both")
    if "calibrate" not in steps and (method or "normalise" in steps):
        raise ValueError("Terrain flattening and angle normalisation need calibrated backscatter: tick calibration")
    kind = "dn" if "calibrate" not in steps else "beta0" if method == "area" else "sigma0" if method == "angular" else (opts.get("kind") or "sigma0")
    for st in ("calibrate", "border", "thermal"):
        if st in steps:
            done.append(st)
    sp = opts.get("speckle") or {}
    sp = sp if sp.get("method") not in (None, "", "none") else None
    enl = float(prod.props.get("sar:looks_equivalent_number") or 4.4) * looks[0] * looks[1]
    norm_n = float(opts.get("normalise_n") or (1.0 if method else 2.0))
    T = {"prod": prod, "g": g, "rd": rd, "pols": pols, "looks": looks, "kind": kind, "thermal": "thermal" in steps, "border": "border" in steps,
         "speckle": sp, "sp_size": int(sp.get("size") or 5) if sp else 0, "enl": enl, "use_dem": use_dem, "method": method, "dem": opts.get("dem"),
         "cache": cache, "res_m": res_m, "mean_h": float(np.mean(g.grid[:, 4])), "geoid": False, "dem_name": None,
         "normalise": "normalise" in steps, "norm_ref": float(opts.get("normalise_ref") or 40), "norm_n": norm_n,
         "clip_aoi": opts.get("aoi") if opts.get("clip") else None, "az_fit": _az_spacing(g) if method == "area" else None,
         "margin": (math.ceil(1500 / res_m) + 2) if method == "area" else (1 if use_dem else 0)}
    date = prod.fields.get("start", "")
    date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) >= 8 else ""
    stem = opts.get("name") or f"{prod.name[:3]}_{date.replace('-', '')}_{prod.fields.get('mode', 'IW')}"
    W = _Writer(grid, out_dir, stem, pols, db=opts.get("db", True), angles=bool(opts.get("masks")), quality=opts.get("quality", True))
    tiles = list(_tiles(grid["shape"]))
    try:
        for k, (r, c, th_, tw) in enumerate(tiles):
            with progress.span(0.03 + 0.95 * k / len(tiles), 0.03 + 0.95 * (k + 1) / len(tiles)):
                if len(tiles) > 1:
                    progress.update(0, f"Tile {k + 1} of {len(tiles)}")
                W.write(r, c, *_grd_tile(T, _sub(grid, r, c, th_, tw)))
    except BaseException:
        for d in W.ds.values():
            d.close()
        raise
    if sp:
        done.append("speckle")
        info["speckle"] = f"{sp['method']} {T['sp_size']}×{T['sp_size']}, ENL {enl:.1f}"
    if use_dem:
        done.append("terrain")
        info["dem"] = T["dem_name"]
    if method:
        done.append("flatten")
    if T["normalise"]:
        done.append("normalise")
    done += ["reproject"] + (["resample"] if opts.get("like") or opts.get("res") else [])
    if opts.get("clip") and opts.get("aoi"):
        done.append("clip")
    if not W.counts[1:].any() or not (W.counts[1] + W.counts[2] + W.counts[4]):
        for d in W.ds.values():
            d.close()
        for p in W.paths.values():
            Path(p).unlink(missing_ok=True)
        raise ValueError("Nothing of the scene falls in the area")
    if method == "area":
        units = "gamma0 terrain-flattened (area-based, Small 2011), linear power"
    elif method == "angular":
        units = "gamma0 terrain-flattened (angular, Vollrath 2020), linear power"
    else:
        units = {"sigma0": "sigma0", "beta0": "beta0", "gamma0": "gamma0 (ellipsoid)", "dn": "uncalibrated DN²"}[kind] + ", linear power"
    if T["normalise"]:
        units = units.replace(", linear power", f", normalised to {T['norm_ref']:g}° (cos^{norm_n:g}), linear power")
    tags = {"sar": "backscatter", "sar_source": f"{prod.name} ({'Planetary Computer' if prod.source == 'pc' else 'file'})", "sar_date": date,
            "units_linear": units, "orbit": orbit.source, "looks": f"{looks[0]}x{looks[1]}", "dem": T["dem_name"] or "none (ellipsoid geocoding, product heights)",
            "geoid": "EGM96" if T["geoid"] else "not applied" if use_dem else "n/a", "sar_frame": prod.fields.get("datatake", "")}
    outs = W.close(tags, done)
    q = W.quality_pct()
    meta = {"product": prod.name, "source": tags["sar_source"], "date": date, "mission": prod.name[:3], "mode": prod.fields.get("mode"),
            "polarisations": pols, "orbit": {"file": orbit.source, "direction": g.pass_dir}, "processing": done, "units": units,
            "radiometry": {"calibration": kind, "thermal_noise_removed": "thermal" in steps, "speckle": info.get("speckle", "none"), "equivalent_looks": round(enl, 1),
                           "noise_floor": "pixels weaker than the product's noise-equivalent sigma zero are flagged (quality 4)" if T["thermal"] else "not assessed"},
            "geometry": {"dem": tags["dem"], "geoid": tags["geoid"], "terrain_flattening": method or "none", "normalised_to_deg": T["norm_ref"] if T["normalise"] else None,
                         "crs": grid["crs"].to_string(), "pixel_m": round(res_m, 3), "shape": list(grid["shape"]), "looks": list(looks), "tiles": len(tiles)},
            "quality_pct": q, "quality_classes": QUALITY, "files": [Path(o).name for o in outs]}
    outs.append(_metadata(out_dir, stem, meta))
    return {"outputs": outs, "paths": {k: str(v) for k, v in W.paths.items()}, "steps_done": done, **info, "units": units,
            "grid": {"crs": grid["crs"].to_string(), "res": round(res_m, 3), "shape": list(grid["shape"])}, "looks": list(looks),
            "orbit": orbit.source, "date": date, "quality": q, "tiles": len(tiles), "frame": prod.fields.get("datatake")}


# ------------------------------------------------------------------ RTC scenes and processed rasters
def _onto(grid, arrays: dict, src_crs, src_tr) -> dict:
    out = {}
    for k, a in arrays.items():
        b = np.full(grid["shape"], np.nan, "float32")
        reproject(a, b, src_transform=src_tr, src_crs=src_crs, dst_transform=grid["transform"], dst_crs=grid["crs"],
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
        out[k] = b
    return out


def _speckle(bands: dict, sp: dict | None, looks: float) -> dict:
    if not sp:
        return bands
    return {k: SP.filter_image(v, sp["method"], int(sp.get("size") or 5), float(sp.get("looks") or looks)).astype("float32") for k, v in bands.items()}


def _simple_quality(bands: dict, cm) -> np.ndarray:
    q = np.where(np.any([np.isfinite(v) for v in bands.values()], 0), 1, 0).astype("uint8")
    if cm is not None:
        q[~cm] = 0
    return q


def _process_rtc(item: dict, out_dir: Path, opts: dict) -> dict:
    """A Planetary Computer RTC scene (γ⁰ terrain-flattened, linear, UTM): only the area is read (cloud-optimised
    GeoTIFFs), tile by tile (a whole scene works too); optional speckle filter, dB, regridding, clip."""
    from rasterio.windows import from_bounds
    a = item["assets"]
    pols = [p for p in (opts.get("pols") or ["VV", "VH", "HH", "HV"]) if p.lower() in a]
    if not pols:
        raise ValueError("The scene has none of the chosen polarisations")
    if "normalise" in (opts.get("steps") or []):
        raise ValueError("Planetary Computer RTC scenes have no incidence angles to normalise with (they are γ⁰ flattened already)")
    srcs = {p: rasterio.open("/vsicurl/" + a[p.lower()]["href"]) for p in pols}
    try:
        s0 = srcs[pols[0]]
        native = {"crs": s0.crs, "transform": s0.transform, "shape": (s0.height, s0.width)}
        regrid = bool(opts.get("like") or (opts.get("crs") not in (None, "", "auto")) or opts.get("res") or opts.get("bounds"))
        if regrid:
            grid = output_grid(aoi=opts.get("aoi"), footprint=opts.get("bounds") or item.get("bbox"), crs=opts.get("crs"), res=float(opts.get("res") or 10), like=opts.get("like"))
        elif opts.get("aoi"):
            from shapely.geometry import shape
            b = transform_bounds("EPSG:4326", native["crs"], *shape(opts["aoi"]).bounds)
            win = from_bounds(*b, transform=native["transform"]).round_offsets().round_lengths()
            grid = {"crs": native["crs"], "transform": rasterio.windows.transform(win, native["transform"]), "shape": (int(win.height), int(win.width))}
        else:
            grid = native
        sp = opts.get("speckle") or {}
        sp = sp if sp.get("method") not in (None, "", "none") else None
        m = int(sp.get("size") or 5) if sp else 0
        looks = float(item["properties"].get("sar:looks_equivalent_number") or 4.4)
        date = (item["properties"].get("datetime") or item["properties"].get("start_datetime") or "")[:10]
        stem = opts.get("name") or f"{item['id'][:3]}_{date.replace('-', '')}_RTC"
        clip_aoi = opts.get("aoi") if opts.get("clip") else None
        W = _Writer(grid, out_dir, stem, pols, db=opts.get("db", True), quality=opts.get("quality", True))
        tiles = list(_tiles(grid["shape"]))
        for k, (r, c, h, w) in enumerate(tiles):
            progress.update(0.05 + 0.9 * k / len(tiles), f"Reading the scene for the area{f': tile {k + 1} of {len(tiles)}' if len(tiles) > 1 else ''}")
            tg = _sub(grid, r, c, h, w)
            if clip_aoi and not _touches(tg, clip_aoi):
                W.write(r, c, {p: np.full((h, w), np.nan, "float32") for p in pols}, quality=np.zeros((h, w), "uint8"))
                continue
            E = _sub(tg, -m, -m, h + 2 * m, w + 2 * m)
            bands = {}
            for pol, s in srcs.items():
                b = np.full(E["shape"], np.nan, "float32")
                reproject(rasterio.band(s, 1), b, dst_transform=E["transform"], dst_crs=E["crs"], src_nodata=s.nodata if s.nodata is not None else 0,
                          dst_nodata=np.nan, resampling=Resampling.bilinear if regrid else Resampling.nearest)
                bands[pol] = np.where(b > 0, b, np.nan)
            bands = {p: v[m:m + h, m:m + w] for p, v in _speckle(bands, sp, looks).items()}
            cm = _clip_mask(tg, clip_aoi) if clip_aoi else None
            if cm is not None:
                bands = {p: np.where(cm, v, np.nan) for p, v in bands.items()}
            q = _simple_quality(bands, None)
            if cm is not None:
                q[~cm & ~np.all([np.isnan(v) for v in bands.values()], 0)] = 5
            W.write(r, c, bands, quality=q)
    finally:
        for s in srcs.values():
            s.close()
    done = ["validate", "orbit", "border", "calibrate", "terrain", "flatten", "reproject"]   # (thermal noise: kept by the producer)
    info = {"note": "Planetary Computer RTC keeps the thermal noise: dark VH surfaces read up to ~1 dB too bright"}
    if sp:
        done.append("speckle")
        info["speckle"] = f"{sp['method']} {m}×{m}, ENL {float(sp.get('looks') or looks):.1f}"
    if regrid:
        done.append("resample")
    if clip_aoi:
        done.append("clip")
    tags = {"sar": "backscatter", "sar_source": f"{item['id']} (Planetary Computer RTC)", "sar_date": date, "units_linear": "gamma0 terrain-flattened, linear power",
            "sar_frame": P.NAME.match(item["id"]).group(11) if P.NAME.match(item["id"]) else ""}
    outs = W.close(tags, done)
    q = W.quality_pct()
    outs.append(_metadata(out_dir, stem, {"product": item["id"], "source": tags["sar_source"], "date": date, "processing": done, "units": tags["units_linear"],
                                          "radiometry": {"speckle": info.get("speckle", "none")}, "geometry": {"crs": grid["crs"].to_string(), "pixel_m": abs(grid["transform"].a),
                                          "shape": list(grid["shape"])}, "quality_pct": q, "quality_classes": QUALITY}))
    return {"outputs": outs, "paths": {k: str(v) for k, v in W.paths.items()}, "steps_done": done, **info, "units": tags["units_linear"], "date": date,
            "grid": {"crs": grid["crs"].to_string(), "res": abs(grid["transform"].a), "shape": list(grid["shape"])}, "quality": q, "tiles": len(tiles)}


def _angle_band(path: str):
    """A processed raster's incidence-angle band (GEE's 'angle', or this app's local / ellipsoid incidence), if any."""
    with rasterio.open(path) as s:
        names = [(d or "").lower() for d in s.descriptions]
        for want in ("local_incidence", "incidence", "angle", "incidence_angle"):
            if want in names:
                return s.read(names.index(want) + 1, masked=True).astype("float64").filled(np.nan), want
    side = Path(path).with_name(Path(path).name.replace("_linear", "_angles").replace("_dB", "_angles"))
    if side != Path(path) and side.exists():
        with rasterio.open(side) as s:
            return s.read(2 if s.count > 1 else 1, masked=True).astype("float64").filled(np.nan), side.name
    return None, None


def _process_raster(path: str, out_dir: Path, opts: dict) -> dict:
    """A SAR raster processed elsewhere (GEE, ASF, this app): speckle filter, angle normalisation (with its angle
    band), regridding, clip, dB; the radar-geometry steps are already done (and can't be redone without the product)."""
    from .analysis import read_pols
    info0 = P.inspect_raster(path)
    if not info0["sar"]:
        raise ValueError(info0["summary"])
    bands, was_db, native = read_pols(path)
    done = [s["step"] for s in info0["steps"] if s["status"] == "done"]
    info = {}
    sp = opts.get("speckle") or {}
    sp = sp if sp.get("method") not in (None, "", "none") else None
    bands = _speckle({k: v.astype("float32") for k, v in bands.items()}, sp, float((sp or {}).get("looks") or 4.4))
    if sp:
        done.append("speckle")
        info["speckle"] = f"{sp['method']} {int(sp.get('size') or 5)}×{int(sp.get('size') or 5)}, ENL {float(sp.get('looks') or 4.4):.1f}"
    units = "backscatter, linear power"
    if "normalise" in (opts.get("steps") or []) and "normalise" not in done:
        ang, where = _angle_band(path)
        if ang is None:
            raise ValueError("Angle normalisation needs the incidence angles: a band named 'angle' (GEE) or incidence, or this app's _angles.tif beside it")
        gamma = "gamma0" in (info0.get("tags", {}).get("units", "") or "").lower() or "flatten" in done
        ref, n = float(opts.get("normalise_ref") or 40), float(opts.get("normalise_n") or (1.0 if gamma else 2.0))
        nf = G.normalise_factor(ang, ref, n)
        bands = {k: (v * nf).astype("float32") for k, v in bands.items()}
        done.append("normalise")
        units = f"backscatter, normalised to {ref:g}° (cos^{n:g}, angles from {where}), linear power"
    if opts.get("like") or opts.get("res") or (opts.get("crs") not in (None, "", "auto")) or opts.get("aoi"):
        grid = output_grid(aoi=opts.get("aoi"), footprint=transform_bounds(native["crs"], "EPSG:4326", *rasterio.transform.array_bounds(*native["shape"], native["transform"])),
                           crs=opts.get("crs"), res=float(opts.get("res") or abs(native["transform"].a)), like=opts.get("like"))
        bands = _onto(grid, bands, native["crs"], native["transform"])
        done += ["resample"]
    else:
        grid = native
    cm = None
    if opts.get("clip") and opts.get("aoi"):
        cm = _clip_mask(grid, opts["aoi"])
        bands = {k: np.where(cm, v, np.nan) for k, v in bands.items()}
        done.append("clip")
    stem = opts.get("name") or Path(path).stem.replace("_dB", "").replace("_linear", "") + "_sar"
    W = _Writer(grid, out_dir, stem, list(bands), db=opts.get("db", True), quality=opts.get("quality", True))
    q = _simple_quality(bands, None)
    if cm is not None:
        q[~cm] = 0
    W.write(0, 0, bands, quality=q)
    tags = {"sar": "backscatter", "sar_source": f"{Path(path).name} ({info0['source']})", "units_linear": units,
            **({"sar_date": info0["tags"]["sar_date"]} if info0.get("tags", {}).get("sar_date") else {})}
    outs = W.close(tags, done)
    return {"outputs": outs, "paths": {k: str(v) for k, v in W.paths.items()}, "steps_done": done, **info, "units": units, "was_db": was_db,
            "grid": {"crs": grid["crs"].to_string(), "res": abs(grid["transform"].a), "shape": list(grid["shape"])}, "quality": W.quality_pct()}


# ------------------------------------------------------------------ frames of one pass, and several dates
def neighbours(item: dict, aoi: dict) -> list[dict]:
    """The other frames (slices) of the same pass (platform, absolute orbit, within 3 minutes) that cover parts of the
    area the scene doesn't: Sentinel-1 cuts each pass into ~170 km frames."""
    import datetime as dt

    import planetary_computer as pc
    import pystac_client
    from shapely.geometry import shape
    a, g = shape(aoi), shape(item["geometry"])
    if g.contains(a):
        return []
    pr = item["properties"]
    t = dt.datetime.fromisoformat((pr.get("datetime") or pr.get("start_datetime")).replace("Z", "+00:00"))
    q = {"sat:absolute_orbit": {"eq": pr["sat:absolute_orbit"]}} if pr.get("sat:absolute_orbit") else {}
    cat = pystac_client.Client.open(P.PC_STAC, modifier=pc.sign_inplace, timeout=60)
    win = f"{(t - dt.timedelta(minutes=3)).isoformat()}/{(t + dt.timedelta(minutes=3)).isoformat()}"
    found = [it.to_dict() for it in cat.search(collections=[item.get("collection") or "sentinel-1-grd"], intersects=aoi, datetime=win, query=q).items()]
    return [f for f in found if f["id"] != item["id"] and f["id"][:3] == item["id"][:3]]


def join_frames(results: list[dict], out_dir: Path, stem: str) -> dict:
    """Frames of one pass, processed onto the same grid, joined into one set of files (each pixel from the first
    frame that has it; the dB recomputed). The frames' own files are removed."""
    keys = [k for k in ("linear", "angles", "quality") if all(k in r.get("paths", {}) for r in results)]
    first = results[0]["paths"]
    with rasterio.open(first["linear"]) as s:
        grid = {"crs": s.crs, "transform": s.transform, "shape": (s.height, s.width)}
        pols, tags = list(s.descriptions), s.tags()
    for r in results[1:]:
        with rasterio.open(r["paths"]["linear"]) as s:
            if (s.height, s.width) != grid["shape"] or s.transform != grid["transform"]:
                raise ValueError("The frames aren't on one grid")
    W = _Writer(grid, out_dir, stem, pols, db="dB" in first, angles="angles" in keys, quality="quality" in keys)
    from rasterio.windows import Window
    for r0, c0, h, w in _tiles(grid["shape"]):
        win = Window(c0, r0, w, h)
        bands, angles, q, taken = None, None, None, None
        for r in results:   # each pixel from the first frame with data there; flags (shadow …) only where none has data
            with rasterio.open(r["paths"]["linear"]) as s:
                b = {p: s.read(i + 1, window=win) for i, p in enumerate(pols)}
            have = np.any([np.isfinite(v) for v in b.values()], 0)
            qq = None
            if "quality" in keys:
                with rasterio.open(r["paths"]["quality"]) as s:
                    qq = s.read(1, window=win)
            aa = None
            if "angles" in keys:
                with rasterio.open(r["paths"]["angles"]) as s:
                    aa = {"incidence": s.read(1, window=win), "local_incidence": s.read(2, window=win)}
            if bands is None:
                bands, angles, q, taken = b, aa, qq, have
                continue
            new = have & ~taken
            bands = {p: np.where(new, b[p], bands[p]) for p in pols}
            if aa is not None:
                angles = {k: np.where(new | (~taken & np.isnan(angles[k])), aa[k], angles[k]) for k in angles}
            if qq is not None:
                flag_only = ~taken & ~have & (q == 0) & (qq > 0)
                q = np.where(new | flag_only, qq, q)
            taken = taken | have
        W.write(r0, c0, bands, angles, q)
    done = (tags.get("sar_steps") or "").split(",")
    tags = {k: v for k, v in tags.items() if k not in ("units", "sar_steps")}
    tags["units_linear"] = results[0]["units"]
    tags["sar_frames"] = json.dumps([r.get("frame") for r in results])
    outs = W.close(tags, [d for d in done if d] + ["join_frames"])
    for r in results:
        for o in r["outputs"]:
            Path(o).unlink(missing_ok=True)
    meta = {"frames": [r.get("frame") for r in results], "joined_from": len(results), "processing": done + ["join_frames"], "units": results[0]["units"],
            "quality_pct": W.quality_pct(), "quality_classes": QUALITY}
    outs.append(_metadata(out_dir, stem, meta))
    return {**results[0], "outputs": outs, "paths": {k: str(v) for k, v in W.paths.items()}, "quality": W.quality_pct(),
            "steps_done": results[0]["steps_done"] + ["join_frames"], "frames": len(results)}


def multitemporal(paths: list[str], size: int = 7, spatial: str = "boxcar") -> dict:
    """Quegan's multi-temporal speckle filter over several dates' linear files (one grid), tile by tile; each file is
    rewritten filtered, with its dB file (beside it) remade. Returns the ENL gain."""
    import os

    from rasterio.windows import Window
    if len(paths) < 2:
        raise ValueError("The multi-temporal filter needs at least two dates")
    with rasterio.open(paths[0]) as s:
        shape, tr = (s.height, s.width), s.transform
        pols = list(s.descriptions)
    for p in paths[1:]:
        with rasterio.open(p) as s:
            if (s.height, s.width) != shape or s.transform != tr or list(s.descriptions) != pols:
                raise ValueError("The multi-temporal filter needs all dates on one grid with the same polarisations: give them the same area and pixel size")
    tmp = [Path(p).with_suffix(".mtf.tif") for p in paths]
    srcs = [rasterio.open(p) for p in paths]
    outs = [rasterio.open(t, "w", **s.profile) for t, s in zip(tmp, srcs)]
    m = size
    gain = []
    try:
        for d, s in zip(outs, srcs):
            for i in range(1, s.count + 1):
                d.set_band_description(i, s.descriptions[i - 1])
            d.update_tags(**s.tags())
        for k, (r, c, h, w) in enumerate(list(_tiles(shape))):
            progress.update(k / max(1, len(list(_tiles(shape)))), "Multi-temporal speckle filter")
            rr0, cc0 = max(0, r - m), max(0, c - m)
            rr1, cc1 = min(shape[0], r + h + m), min(shape[1], c + w + m)
            win = Window(cc0, rr0, cc1 - cc0, rr1 - rr0)
            core = (slice(r - rr0, r - rr0 + h), slice(c - cc0, c - cc0 + w))
            for i in range(1, len(pols) + 1):
                st = np.stack([s.read(i, window=win).astype("float64") for s in srcs])
                f = SP.quegan(st, size, spatial)
                if k == 0 and i == 1:
                    gain.append(round(SP.enl(f[0][core]) / max(SP.enl(st[0][core]), 1e-9), 2))
                for d, x in zip(outs, f):
                    d.write(x[core].astype("float32"), i, window=Window(c, r, w, h))
    finally:
        for x in srcs + outs:
            x.close()
    for p, t in zip(paths, tmp):
        with rasterio.open(t, "r+") as d:
            d.update_tags(sar_steps=(d.tags().get("sar_steps", "") + ",speckle_mt").strip(","), sar_speckle_mt=f"Quegan {size}x{size} over {len(paths)} dates")
        os.replace(t, p)
        db = Path(p).with_name(Path(p).name.replace("_linear.tif", "_dB.tif"))
        if db.exists() and db != Path(p):
            with rasterio.open(p) as s, rasterio.open(db, "r+") as d:
                for i in range(1, s.count + 1):
                    v = s.read(i)
                    with np.errstate(divide="ignore", invalid="ignore"):
                        d.write((10 * np.log10(np.where(v > 0, v, np.nan))).astype("float32"), i)
                d.update_tags(sar_steps=(d.tags().get("sar_steps", "") + ",speckle_mt").strip(","))
    return {"dates": len(paths), "window": size, "enl_gain": gain[0] if gain else None}


def process(src, out_dir: Path, opts: dict, cache: Path | None = None) -> dict:
    """Run the ticked steps on a SAR input: a GRD .SAFE / .zip, a Planetary Computer scene (id or item), or a SAR raster.
    opts: steps [names], pols, kind (sigma0 / beta0 / gamma0), speckle {method, size, looks}, flatten_method (area /
    angular), normalise_ref (°), normalise_n, dem (path), crs ("auto" or EPSG), res (m), like (raster path), aoi
    (GeoJSON geometry), bounds (lon / lat box when there is no area), clip, db, masks (angles), quality, name."""
    out_dir = Path(out_dir)
    if isinstance(src, (str, Path)) and str(src).lower().endswith((".tif", ".tiff")):
        r = _process_raster(str(src), out_dir, opts)
    else:
        item = None
        if isinstance(src, dict) or (isinstance(src, str) and src.startswith("S1") and not Path(src).exists()):
            item = P.pc_item(src)
            if item.get("collection") == "sentinel-1-rtc":
                r = _process_rtc(item, out_dir, opts)
                progress.update(1, "Done")
                return r
        prod = P.open_product(item or src)
        if prod.fields.get("type") not in (None, "GRD"):
            raise ValueError(P.inspect(prod)["summary"])
        r = _process_grd(prod, out_dir, opts, cache)
    progress.update(1, "Done")
    return r
