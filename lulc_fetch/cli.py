"""lulc-fetch command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

from . import sentinel2 as s2
from .aoi import aoi_from_args, aoi_mask, grid_from_raster, make_grid
from .indices import INDICES
from .pipeline import check_size, export_composite, export_labels, export_scene
from .raster import write_geotiff, write_rgb_png
from .sources import DEFAULT_BANDS, S2_BANDS, SOURCES, get_source

log = logging.getLogger("lulc_fetch")


# ---------------------------------------------------------------- helpers

def _add_aoi_args(p: argparse.ArgumentParser, res_default: float = 10.0):
    g = p.add_argument_group("area of interest (pick one)")
    g.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat (WGS84)")
    g.add_argument("--geojson", help="GeoJSON file; output is masked to the polygon(s)")
    g.add_argument("--point", help="lon,lat — a square of --buffer-km around it")
    g.add_argument("--buffer-km", type=float, default=5.0, help="half-width for --point (default 5)")
    g.add_argument("--match", help="existing raster whose exact grid (CRS, extent, pixels) to reuse")
    p.add_argument("--res", type=float, default=res_default, help=f"pixel size in metres (default {res_default:g})")
    p.add_argument("--crs", help="output CRS, e.g. EPSG:32643 (default: UTM zone of the AOI)")


def _add_date_args(p: argparse.ArgumentParser, required: bool = True):
    p.add_argument("--start", required=required, help="start date YYYY-MM-DD")
    p.add_argument("--end", required=required, help="end date YYYY-MM-DD")


def _resolve_grid(args):
    if args.match:
        grid = grid_from_raster(args.match)
        aoi = None
    else:
        aoi = aoi_from_args(args.bbox, args.geojson, args.point, args.buffer_km)
        grid = make_grid(aoi, args.res, args.crs)
    check_size(grid)
    log.info("Output grid: %dx%d px, %g m, %s", grid.width, grid.height, grid.res, grid.crs.to_string())
    return aoi, grid


def _parse_bands(text: str | None) -> list[str]:
    if not text:
        return list(DEFAULT_BANDS)
    if text.lower() == "all":
        return list(S2_BANDS)
    bands = [b.strip().upper() for b in text.split(",") if b.strip()]
    bad = [b for b in bands if b not in S2_BANDS]
    if bad:
        raise SystemExit(f"Unknown band(s) {bad}; valid: {', '.join(S2_BANDS)}")
    return bands


def _add_s2_output_args(p):
    p.add_argument("--source", default="earth-search", choices=list(SOURCES),
                   help="catalog to read from (default earth-search: AWS, no login)")
    p.add_argument("--bands", help=f"comma list or 'all' (default {','.join(DEFAULT_BANDS)})")
    p.add_argument("--indices", action="store_true", help=f"append {', '.join(INDICES)}")
    p.add_argument("--no-preview", action="store_true", help="skip the RGB PNG quicklook")
    p.add_argument("-o", "--out", required=True, help="output GeoTIFF path")


# ---------------------------------------------------------------- commands

def cmd_search(args):
    source = get_source(args.source)
    aoi = aoi_from_args(args.bbox, args.geojson, args.point, args.buffer_km)
    items = s2.search(source, aoi.bbox, args.start, args.end, args.max_cloud, args.limit)
    scenes = s2.group_scenes(items)
    if args.json:
        print(json.dumps([{"date": str(s.date), "cloud_cover": round(s.cloud, 2), "tiles": s.tiles,
                           "ids": s.ids} for s in scenes], indent=2))
        return
    print(f"{len(items)} items / {len(scenes)} acquisition dates from {source.name} "
          f"(sorted by scene cloud cover)\n")
    print(f"{'date':<12}{'cloud %':>8}  {'tiles':<14} ids")
    for s in scenes:
        print(f"{str(s.date):<12}{s.cloud:>8.1f}  {','.join(s.tiles):<14} {', '.join(s.ids)}")


def cmd_scene(args):
    source = get_source(args.source)
    aoi, grid = _resolve_grid(args)
    export_scene(source, aoi, grid, args.start, args.end, args.out, bands=_parse_bands(args.bands),
                 max_cloud=args.max_cloud, date=args.date, candidates=args.candidates,
                 mask_clouds=not args.keep_clouds, indices=args.indices, preview=not args.no_preview)


def cmd_composite(args):
    source = get_source(args.source)
    aoi, grid = _resolve_grid(args)
    export_composite(source, aoi, grid, args.start, args.end, args.out, bands=_parse_bands(args.bands),
                     max_cloud=args.max_cloud, max_scenes=args.max_scenes, stat=args.stat,
                     indices=args.indices, preview=not args.no_preview)


def cmd_labels(args):
    aoi, grid = _resolve_grid(args)
    result = export_labels(args.product, args.year, aoi, grid, args.out)
    print(f"\nClass distribution ({result['title']}, {args.year}):")
    for row in result["distribution"]:
        print(f"  {row['code']:>4}  {row['name']:<28} {row['pct']:6.2f}%")


def cmd_fetch(args):
    from rasterio.enums import Resampling

    from .extras import fetch_collection

    aoi, grid = _resolve_grid(args)
    dt = f"{args.start}/{args.end}" if args.start and args.end else None
    resampling = Resampling.nearest if args.nearest else Resampling.bilinear
    data, names, items = fetch_collection(args.collection, args.assets.split(","), grid, dt, resampling)
    region = aoi_mask(aoi, grid)
    if region is not None:
        data[:, ~region] = np.nan
    out = write_geotiff(args.out, data.astype("float32"), grid, descriptions=names,
                        tags={"collection": args.collection, "items": ",".join(i.id for i in items[:20])})
    log.info("Wrote %s  (%d bands: %s)", out, len(names), ", ".join(names))
    if not args.no_preview and data.shape[0] >= 3:
        log.info("Wrote %s", write_rgb_png(Path(args.out).with_suffix(".png"), data[:3]))


def cmd_product(args):
    from .cdse import download_product

    for name in args.names:
        log.info("Downloading %s from Copernicus Data Space...", name)
        log.info("Saved %s", download_product(name, args.out_dir))


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lulc-fetch", description=__doc__ + "\nSee README.md for examples.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("search", help="list Sentinel-2 L2A acquisitions over an AOI")
    g = sp.add_argument_group("area of interest (pick one)")
    g.add_argument("--bbox"); g.add_argument("--geojson"); g.add_argument("--point")
    g.add_argument("--buffer-km", type=float, default=5.0)
    _add_date_args(sp)
    sp.add_argument("--source", default="earth-search", choices=list(SOURCES))
    sp.add_argument("--max-cloud", type=float, default=None, help="max scene cloud cover %%")
    sp.add_argument("--limit", type=int, default=500)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("scene", help="download one cloud-masked Sentinel-2 date, clipped to the AOI")
    _add_aoi_args(sp); _add_date_args(sp); _add_s2_output_args(sp)
    sp.add_argument("--max-cloud", type=float, default=40, help="scene cloud cover %% prefilter (default 40)")
    sp.add_argument("--date", help="use this acquisition date (YYYY-MM-DD) instead of auto-picking")
    sp.add_argument("--candidates", type=int, default=5,
                    help="check AOI-level clouds on this many least-cloudy dates and keep the clearest")
    sp.add_argument("--keep-clouds", action="store_true", help="don't mask clouds / shadows")
    sp.set_defaults(func=cmd_scene)

    sp = sub.add_parser("composite", help="cloud-free median composite over a date range (best for LULC)")
    _add_aoi_args(sp); _add_date_args(sp); _add_s2_output_args(sp)
    sp.add_argument("--max-cloud", type=float, default=60, help="scene cloud cover %% prefilter (default 60)")
    sp.add_argument("--max-scenes", type=int, default=20, help="use at most this many least-cloudy dates")
    sp.add_argument("--stat", choices=["median", "mean"], default="median")
    sp.set_defaults(func=cmd_composite)

    sp = sub.add_parser("labels", help="reference LULC map (ESA WorldCover / Esri) on the same grid")
    _add_aoi_args(sp)
    sp.add_argument("--product", choices=["worldcover", "esri"], default="worldcover")
    sp.add_argument("--year", type=int, default=2021)
    sp.add_argument("-o", "--out", required=True)
    sp.set_defaults(func=cmd_labels)

    sp = sub.add_parser("fetch", help="any Planetary Computer collection, e.g. NAIP 0.6 m or Sentinel-1")
    _add_aoi_args(sp, res_default=10.0)
    _add_date_args(sp, required=False)
    sp.add_argument("--collection", required=True, help="e.g. naip, sentinel-1-rtc, cop-dem-glo-30")
    sp.add_argument("--assets", required=True, help="comma list, e.g. image  or  vv,vh  or  data")
    sp.add_argument("--nearest", action="store_true", help="nearest-neighbour resampling (for class maps)")
    sp.add_argument("--no-preview", action="store_true")
    sp.add_argument("-o", "--out", required=True)
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("product", help="download full .SAFE products from Copernicus Data Space (login)")
    sp.add_argument("names", nargs="+", help="product names, e.g. S2B_MSIL2A_20260211T050839_N0512_R019_T43PGQ_20260211T085923")
    sp.add_argument("--out-dir", default=".")
    sp.set_defaults(func=cmd_product)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s", stream=sys.stderr)
    for noisy in ("rasterio", "urllib3", "pystac_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        args.func(args)
    except (RuntimeError, ValueError) as e:
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    main()
