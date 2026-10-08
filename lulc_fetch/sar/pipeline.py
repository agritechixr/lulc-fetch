"""The SAR workflow: the steps a user ticks, run in the right order for what the input is.

    search(aoi, start, end, …)       Sentinel-1 GRD / RTC scenes on Planetary Computer, filtered (polarisation, mode,
                                     orbit direction, relative orbit) so a series is homogeneous
    process(src, out_dir, opts)      a GRD (.SAFE / .zip / Planetary Computer scene), an RTC scene, or a processed SAR
                                     raster → analysis-ready backscatter (linear power, and dB if asked)

GRD chain (each step only if ticked and still needed): product check → precise orbit → border noise → thermal noise
→ calibration (σ⁰ / β⁰ / γ⁰) → speckle filter (radar geometry, after multilooking to the output pixel size) →
Range-Doppler terrain correction with a DEM (or ellipsoid geocoding without) → terrain flattening (γ⁰) → layover /
shadow masks → dB → onto the output grid (reprojection, resampling, alignment) → clip. Steps already done by the data's
producer (RTC, GEE, ASF …) are skipped, never applied twice.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform, transform_bounds, transform_geom

from .. import progress
from . import geometry as G
from . import product as P
from . import radiometry as R
from . import speckle as SP

log = logging.getLogger(__name__)
MAX_PIXELS = 50_000_000
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
    cat = pystac_client.Client.open(P.PC_STAC, modifier=pc.sign_inplace)
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
    scene's footprint)."""
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


def _write(grid, out: Path, bands: dict, tags: dict, dtype="float32", nodata=np.nan) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    prof = {"driver": "GTiff", "width": grid["shape"][1], "height": grid["shape"][0], "count": len(bands), "dtype": dtype, "crs": grid["crs"],
            "transform": grid["transform"], "nodata": nodata, "compress": "deflate", "BIGTIFF": "IF_SAFER"}
    if grid["shape"][0] >= 256 and grid["shape"][1] >= 256:
        prof.update(tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(out, "w", **prof) as d:
        for i, (n, b) in enumerate(bands.items(), start=1):
            d.write(b.astype(dtype), i)
            d.set_band_description(i, n)
        d.update_tags(**{k: (v if isinstance(v, str) else json.dumps(v)) for k, v in tags.items()})
    return str(out)


def _finish(grid, bands: dict, out_dir: Path, stem: str, opts: dict, tags: dict, done: list, extra: dict | None = None) -> dict:
    """Clip, then write linear power (always) and dB (if asked), and the geometry bands (if any)."""
    if opts.get("clip") and opts.get("aoi"):
        m = _clip_mask(grid, opts["aoi"])
        bands = {k: np.where(m, v, np.nan) for k, v in bands.items()}
        extra = {k: np.where(m, v, np.nan if v.dtype.kind == "f" else 0) for k, v in (extra or {}).items()}
        done.append("clip")
    outs = []
    lin = _write(grid, out_dir / f"{stem}_linear.tif", bands, {**tags, "units": tags["units_linear"], "sar_steps": ",".join(done)})
    outs.append(lin)
    if opts.get("db", True):
        with np.errstate(divide="ignore", invalid="ignore"):
            dbb = {f"{k}_dB": 10 * np.log10(np.where(v > 0, v, np.nan)) for k, v in bands.items()}
        outs.insert(0, _write(grid, out_dir / f"{stem}_dB.tif", dbb, {**tags, "units": tags["units_linear"].replace("linear power", "dB"), "sar_steps": ",".join(done + ["db"])}))
    if extra:
        geo = {k: v for k, v in extra.items() if v.dtype.kind == "f"}
        if geo:
            outs.append(_write(grid, out_dir / f"{stem}_angles.tif", geo, {"units": "degrees"}))
        if "layover_shadow" in extra:
            p = out_dir / f"{stem}_layover_shadow.tif"
            prof = {"driver": "GTiff", "width": grid["shape"][1], "height": grid["shape"][0], "count": 1, "dtype": "uint8", "crs": grid["crs"],
                    "transform": grid["transform"], "nodata": 255, "compress": "deflate", "photometric": "palette"}
            with rasterio.open(p, "w", **prof) as d:
                d.write_colormap(1, {0: (220, 220, 220, 60), 1: (230, 85, 13, 255), 2: (49, 54, 149, 255)})
                d.write(extra["layover_shadow"], 1)
                d.update_tags(classes=json.dumps({0: "Fine", 1: "Layover", 2: "Shadow"}))
                d.set_band_description(1, "Layover / shadow")
            outs.append(str(p))
    return {"outputs": outs, "steps_done": done}


# ------------------------------------------------------------------ GRD
def _process_grd(prod: P.Product, out_dir: Path, opts: dict, cache: Path | None) -> dict:
    steps, info = set(opts.get("steps") or []), {}
    g = prod.geometry
    pols = [p for p in (opts.get("pols") or sorted(prod.measurement)) if p in prod.measurement and p in prod.calibration]
    if not pols:
        raise ValueError("The product has none of the chosen polarisations (with calibration files)")
    t0, t1 = g.first_line, g.first_line + (g.lines - 1) * g.line_interval
    done = ["validate"]
    # orbit
    sv, src = g.orbit, "the product's own (restituted / predicted)"
    if "orbit" in steps:
        progress.update(0.03, "Precise orbit (ESA)")
        po = G.precise_orbit(prod.name[:3], t0, t1, cache)
        if po is not None:
            sv, src = po, "precise (POEORB, ESA)"
            done.append("orbit")
        else:
            info["orbit_note"] = "No precise orbit yet (it comes ~20 days after acquisition) or ESA wasn't reachable: the product's orbit was used"
    orbit = G.Orbit(sv, t0, t1, src)
    rd = G.RangeDoppler(g, orbit)
    footprint = (g.grid[:, 3].min(), g.grid[:, 2].min(), g.grid[:, 3].max(), g.grid[:, 2].max())
    grid = output_grid(aoi=opts.get("aoi"), footprint=footprint, crs=opts.get("crs"), res=float(opts.get("res") or 20), like=opts.get("like"))
    res_m = abs(grid["transform"].a) * (111320 if grid["crs"].is_geographic else 1)
    # the part of the radar image the grid needs (from its border, at the scene's mean height), with a margin
    h, w = grid["shape"]
    rr = np.unique(np.r_[np.linspace(0, h - 1, 40).astype(int)])
    lon_b, lat_b = [], []
    for r in rr:
        lo, la = _lonlat(grid, r, r + 1)
        lon_b.append(lo[0, :: max(1, w // 40)]); lat_b.append(la[0, :: max(1, w // 40)])
    lon_b, lat_b = np.concatenate(lon_b), np.concatenate(lat_b)
    line, pix, _, _ = rd.locate(lat_b, lon_b, np.full(lat_b.shape, float(np.mean(g.grid[:, 4]))))
    margin = 300
    r0, r1 = int(max(0, np.floor(line.min()) - margin)), int(min(g.lines, np.ceil(line.max()) + margin))
    c0, c1 = int(max(0, np.floor(pix.min()) - margin)), int(min(g.samples, np.ceil(pix.max()) + margin))
    if r1 <= r0 or c1 <= c0:
        raise ValueError("The area isn't covered by this scene")
    looks = (max(1, round(res_m / g.azimuth_spacing)), max(1, round(res_m / g.range_spacing)))
    kind = "sigma0" if "flatten" in steps else (opts.get("kind") or "sigma0")
    if "calibrate" not in steps:
        kind = "dn"
    else:
        done.append("calibrate")
    if "border" in steps:
        done.append("border")
    if "thermal" in steps:
        done.append("thermal")
    radar = {}
    for k, pol in enumerate(pols):
        progress.update(0.08 + 0.4 * k / len(pols), f"{pol}: reading and calibrating the radar image")
        radar[pol] = R.read_window(prod, pol, (r0, c0, r1 - r0, c1 - c0), looks, thermal="thermal" in steps, border="border" in steps,
                                   kind=kind, progress_cb=lambda f, k=k, pol=pol: progress.update(0.08 + 0.4 * (k + f) / len(pols), f"{pol}: {f:.0%}"))
    enl = float(prod.props.get("sar:looks_equivalent_number") or 4.4) * looks[0] * looks[1]
    if opts.get("speckle") and opts["speckle"].get("method") not in (None, "", "none"):
        sp = opts["speckle"]
        for pol in pols:
            progress.update(0.5, f"{pol}: speckle filter ({sp['method']})")
            radar[pol] = SP.filter_image(radar[pol], sp["method"], int(sp.get("size") or 5), float(sp.get("looks") or enl)).astype("float32")
        done.append("speckle")
        info["speckle"] = f"{sp['method']} {int(sp.get('size') or 5)}×{int(sp.get('size') or 5)}, ENL {enl:.1f}"
    # onto the map grid
    bands = {p: np.full(grid["shape"], np.nan, "float32") for p in pols}
    extra = {}
    use_dem = "terrain" in steps
    want_angles = use_dem and ("flatten" in steps or opts.get("masks"))
    if use_dem:
        progress.update(0.55, "DEM heights")
        dem, dem_name = G.dem_for(grid["crs"], grid["transform"], grid["shape"], opts.get("dem"), cache)
        info["dem"] = dem_name
        slope = G.slopes(dem, (res_m, res_m))   # once for the whole grid (blocks need their neighbours' heights)
        done.append("terrain")
    if want_angles:
        extra = {"incidence": np.full(grid["shape"], np.nan, "float32"), "local_incidence": np.full(grid["shape"], np.nan, "float32"),
                 "layover_shadow": np.full(grid["shape"], 255, "uint8")}
    from scipy.ndimage import map_coordinates
    block = max(16, 2_000_000 // max(w, 1))
    geoid_used = None
    for b0 in range(0, h, block):
        b1 = min(h, b0 + block)
        progress.update(0.6 + 0.35 * b0 / h, f"{'Terrain correction' if use_dem else 'Geocoding'}: rows {b0}–{b1} of {h}")
        lon, lat = _lonlat(grid, b0, b1)
        if use_dem:
            N = G.geoid(lon, lat, cache)
            geoid_used = geoid_used if geoid_used is not None else bool(np.any(N != 0))
            hgt = np.where(np.isfinite(dem[b0:b1]), dem[b0:b1], 0) + N
        else:   # no DEM: the product's own (coarse) heights from its geolocation grid
            from scipy.interpolate import griddata
            hgt = griddata(g.grid[:, [3, 2]], g.grid[:, 4], (lon, lat), method="linear")
            hgt = np.where(np.isfinite(hgt), hgt, float(np.mean(g.grid[:, 4])))
        line, pix, S, Pp = rd.locate(lat, lon, hgt)
        rl = (line - r0 - (looks[0] - 1) / 2) / looks[0]
        rp = (pix - c0 - (looks[1] - 1) / 2) / looks[1]
        inside = (line >= 0) & (line <= g.lines - 1) & (pix >= 0) & (pix <= g.samples - 1)
        if want_angles or "flatten" in steps:
            th, thl, ar = G.angles(lat, lon, S, Pp, (slope[0][b0:b1], slope[1][b0:b1]) if use_dem else None)
        for pol in pols:
            img = radar[pol]
            okm = np.isfinite(img)
            vals = map_coordinates(np.where(okm, img, 0), [rl, rp], order=1, mode="constant", cval=0)
            wts = map_coordinates(okm.astype("float32"), [rl, rp], order=1, mode="constant", cval=0)
            with np.errstate(invalid="ignore", divide="ignore"):
                v = np.where(inside & (wts > 0.5), vals / wts, np.nan)
            if "flatten" in steps:
                with np.errstate(invalid="ignore", divide="ignore"):
                    v = v / np.cos(np.radians(th)) / G.flatten_factor(th, ar)
                v = np.where(G.layover_shadow(th, ar) == 0, v, np.nan)
            bands[pol][b0:b1] = v
        if want_angles:
            extra["incidence"][b0:b1] = np.where(inside, th, np.nan)
            extra["local_incidence"][b0:b1] = np.where(inside, thl, np.nan)
            extra["layover_shadow"][b0:b1] = np.where(inside, G.layover_shadow(th, ar), 255)
    if "flatten" in steps:
        done.append("flatten")
        units = "gamma0 terrain-flattened, linear power"
    else:
        units = {"sigma0": "sigma0", "beta0": "beta0", "gamma0": "gamma0 (ellipsoid)", "dn": "uncalibrated DN²"}[kind] + ", linear power"
    done += ["reproject"] + (["resample"] if opts.get("like") or opts.get("res") else [])
    if not opts.get("masks"):
        extra = {}
    if all(not np.isfinite(b).any() for b in bands.values()):
        raise ValueError("Nothing of the scene falls in the area")
    date = prod.fields.get("start", "")
    date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) >= 8 else ""
    tags = {"sar": "backscatter", "sar_source": f"{prod.name} ({'Planetary Computer' if prod.source == 'pc' else 'file'})", "sar_date": date,
            "units_linear": units, "orbit": orbit.source, "looks": f"{looks[0]}x{looks[1]}", "dem": info.get("dem", "none (ellipsoid geocoding, product heights)"),
            "geoid": "EGM96" if geoid_used else "not applied" if use_dem else "n/a"}
    stem = opts.get("name") or f"{prod.name[:3]}_{date.replace('-', '')}_{prod.fields.get('mode', 'IW')}"
    res = _finish(grid, bands, out_dir, stem, opts, tags, done, extra)
    return {**res, **info, "units": units, "grid": {"crs": grid["crs"].to_string(), "res": round(res_m, 3), "shape": list(grid["shape"])},
            "window": [r0, c0, r1 - r0, c1 - c0], "looks": list(looks), "orbit": orbit.source, "date": date}



# ------------------------------------------------------------------ RTC scenes and processed rasters
def _onto(grid, arrays: dict, src_crs, src_tr) -> dict:
    out = {}
    for k, a in arrays.items():
        b = np.full(grid["shape"], np.nan, "float32")
        reproject(a, b, src_transform=src_tr, src_crs=src_crs, dst_transform=grid["transform"], dst_crs=grid["crs"],
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
        out[k] = b
    return out


def _speckle_map(bands: dict, opts: dict, done: list, info: dict, looks: float) -> dict:
    sp = opts.get("speckle") or {}
    if sp.get("method") in (None, "", "none"):
        return bands
    out = {k: SP.filter_image(v, sp["method"], int(sp.get("size") or 5), float(sp.get("looks") or looks)).astype("float32") for k, v in bands.items()}
    done.append("speckle")
    info["speckle"] = f"{sp['method']} {int(sp.get('size') or 5)}×{int(sp.get('size') or 5)}, ENL {float(sp.get('looks') or looks):.1f}"
    return out


def _process_rtc(item: dict, out_dir: Path, opts: dict) -> dict:
    """A Planetary Computer RTC scene (γ⁰ terrain-flattened, linear, UTM): only the area is read (cloud-optimised
    GeoTIFFs); optional speckle filter, dB, regridding, clip."""
    from rasterio.windows import from_bounds
    a = item["assets"]
    pols = [p for p in (opts.get("pols") or ["VV", "VH", "HH", "HV"]) if p.lower() in a]
    if not pols:
        raise ValueError("The scene has none of the chosen polarisations")
    first = "/vsicurl/" + a[pols[0].lower()]["href"]
    with rasterio.open(first) as s:
        native = {"crs": s.crs, "transform": s.transform, "shape": (s.height, s.width)}
    regrid = bool(opts.get("like") or (opts.get("crs") not in (None, "", "auto")) or opts.get("res"))
    if regrid:
        grid = output_grid(aoi=opts.get("aoi"), footprint=None, crs=opts.get("crs"), res=float(opts.get("res") or 10), like=opts.get("like"))
    elif opts.get("aoi"):
        from shapely.geometry import shape
        b = transform_bounds("EPSG:4326", native["crs"], *shape(opts["aoi"]).bounds)
        win = from_bounds(*b, transform=native["transform"]).round_offsets().round_lengths()
        grid = {"crs": native["crs"], "transform": rasterio.windows.transform(win, native["transform"]), "shape": (int(win.height), int(win.width))}
    else:
        raise ValueError("Choose an area: a whole RTC scene is about 25 000 × 30 000 pixels")
    bands = {}
    for k, pol in enumerate(pols):
        progress.update(0.1 + 0.6 * k / len(pols), f"Reading {pol} for the area")
        b = np.full(grid["shape"], np.nan, "float32")
        with rasterio.open("/vsicurl/" + a[pol.lower()]["href"]) as s:
            reproject(rasterio.band(s, 1), b, dst_transform=grid["transform"], dst_crs=grid["crs"], src_nodata=s.nodata if s.nodata is not None else 0,
                      dst_nodata=np.nan, resampling=Resampling.bilinear if regrid else Resampling.nearest)
        bands[pol] = np.where(b > 0, b, np.nan)
    done = ["validate", "orbit", "border", "thermal", "calibrate", "terrain", "flatten", "reproject"]
    info = {}
    bands = _speckle_map(bands, opts, done, info, float(item["properties"].get("sar:looks_equivalent_number") or 4.4))
    if regrid:
        done.append("resample")
    date = (item["properties"].get("datetime") or item["properties"].get("start_datetime") or "")[:10]
    tags = {"sar": "backscatter", "sar_source": f"{item['id']} (Planetary Computer RTC)", "sar_date": date, "units_linear": "gamma0 terrain-flattened, linear power"}
    stem = opts.get("name") or f"{item['id'][:3]}_{date.replace('-', '')}_RTC"
    res = _finish(grid, bands, out_dir, stem, opts, tags, done)
    return {**res, **info, "units": tags["units_linear"], "date": date, "grid": {"crs": grid["crs"].to_string(), "res": abs(grid["transform"].a), "shape": list(grid["shape"])}}


def _process_raster(path: str, out_dir: Path, opts: dict) -> dict:
    """A SAR raster processed elsewhere (GEE, ASF, this app): speckle filter, regridding, clip, dB; the radar-geometry
    steps are already done (and can't be redone without the original product)."""
    from .analysis import read_pols
    info0 = P.inspect_raster(path)
    if not info0["sar"]:
        raise ValueError(info0["summary"])
    bands, was_db, native = read_pols(path)
    done = [s["step"] for s in info0["steps"] if s["status"] == "done"]
    info = {}
    bands = _speckle_map({k: v.astype("float32") for k, v in bands.items()}, opts, done, info, float((opts.get("speckle") or {}).get("looks") or 4.4))
    if opts.get("like") or opts.get("res") or (opts.get("crs") not in (None, "", "auto")) or opts.get("aoi"):
        grid = output_grid(aoi=opts.get("aoi"), footprint=transform_bounds(native["crs"], "EPSG:4326", *rasterio.transform.array_bounds(*native["shape"], native["transform"])),
                           crs=opts.get("crs"), res=float(opts.get("res") or abs(native["transform"].a)), like=opts.get("like"))
        bands = _onto(grid, bands, native["crs"], native["transform"])
        done += ["resample"]
    else:
        grid = native
    tags = {"sar": "backscatter", "sar_source": f"{Path(path).name} ({info0['source']})", "units_linear": "backscatter, linear power"}
    stem = opts.get("name") or Path(path).stem.replace("_dB", "").replace("_linear", "") + "_sar"
    res = _finish(grid, bands, out_dir, stem, opts, tags, done)
    return {**res, **info, "units": tags["units_linear"], "was_db": was_db}


def process(src, out_dir: Path, opts: dict, cache: Path | None = None) -> dict:
    """Run the ticked steps on a SAR input: a GRD .SAFE / .zip, a Planetary Computer scene (id or item), or a SAR raster.
    opts: steps [names], pols, kind (sigma0 / beta0 / gamma0), speckle {method, size, looks}, dem (path), crs ("auto"
    or EPSG), res (m), like (raster path), aoi (GeoJSON geometry), clip, db, masks, name."""
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
