"""High-level exports shared by the CLI and the web app."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from . import progress
from . import sentinel2 as s2
from .aoi import AOI, Grid, aoi_mask
from .indices import compute_indices
from .raster import write_geotiff, write_rgb_png
from .sources import Source

log = logging.getLogger(__name__)
MAX_PIXELS = 60_000_000  # ~ 77 x 77 km at 10 m; beyond this, split the AOI


def check_size(grid: Grid):
    if grid.width * grid.height > MAX_PIXELS:
        raise ValueError(f"AOI is {grid.width}x{grid.height} px at {grid.res:g} m — too large for one "
                         f"request. Use a smaller AOI or a coarser resolution.")


def search_geometry(aoi: AOI | None) -> dict | None:
    """Geometry to send to STAC `intersects`: the AOI itself, or its convex hull when it is
    multi-part or has too many vertices for a search request."""
    if not aoi or not aoi.geometries:
        return None
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    geom = unary_union([shape(g) for g in aoi.geometries])
    if len(aoi.geometries) > 1 or len(shapely_coords(geom)) > 500:
        geom = geom.convex_hull
    return mapping(geom)


def shapely_coords(geom) -> list:
    if hasattr(geom, "geoms"):
        return [c for g in geom.geoms for c in shapely_coords(g)]
    if geom.geom_type == "Polygon":
        return list(geom.exterior.coords) + [c for r in geom.interiors for c in r.coords]
    return list(geom.coords)


def write_s2_outputs(out: Path, arrays: dict[str, np.ndarray], grid: Grid, region, *, indices: bool,
                     preview: bool, tags: dict, extra: list[tuple[str, np.ndarray]] = ()) -> dict:
    layers, names = [arrays[b] for b in arrays], list(arrays)
    if indices:
        idx, idx_names = compute_indices(arrays)
        layers += idx
        names += idx_names
    for name, layer in extra:
        layers.append(layer.astype("float32"))
        names.append(name)
    stack = np.stack(layers).astype("float32")
    if region is not None:
        stack[:, ~region] = np.nan
    files = [write_geotiff(out, stack, grid, descriptions=names, tags=tags)]
    log.info("Wrote %s  (%d bands: %s)", out, len(names), ", ".join(names))
    if preview and all(b in arrays for b in ("B04", "B03", "B02")):
        files.append(write_rgb_png(out.with_suffix(".png"), stack[[names.index(b) for b in ("B04", "B03", "B02")]]))
        log.info("Wrote %s  (true-colour preview)", files[-1])
    valid = np.isfinite(stack[0])
    if region is not None:
        valid = valid[region]
    valid_pct = 100 * float(valid.mean())
    log.info("Valid (cloud-free, in-AOI) pixels: %.1f%%", valid_pct)
    return {"files": [str(f) for f in files], "bands": names, "valid_pct": valid_pct}


def export_scene(source: Source, aoi: AOI | None, grid: Grid, start: str, end: str, out: str | Path, *,
                 bands: list[str], max_cloud: float | None = 40, date: str | None = None,
                 candidates: int = 5, mask_clouds: bool = True, indices: bool = False,
                 preview: bool = True) -> dict:
    check_size(grid)
    region = aoi_mask(aoi, grid)
    progress.update(0.01, "Searching the catalog")
    items = s2.search(source, grid.bbox_lonlat(), start, end, max_cloud, intersects=search_geometry(aoi))
    scenes = s2.group_scenes(items)
    if date:
        scenes = [s for s in scenes if str(s.date) == date]
    if not scenes:
        raise ValueError("No scenes found — widen the date range or raise the cloud limit.")
    if date or candidates <= 1:
        scene = scenes[0]
    else:
        log.info("Checking AOI cloud cover for the %d least-cloudy dates...", min(candidates, len(scenes)))
        with progress.span(0.05, 0.3):
            scene, _ = s2.pick_best_scene(source, scenes, grid, region, candidates)
    log.info("Using %s  tiles=%s  ids=%s", scene.date, ",".join(scene.tiles), ", ".join(scene.ids))
    with progress.span(0.3, 0.95):
        arrays = s2.scene_stack(source, scene, grid, bands, mask_clouds=mask_clouds)
    progress.update(0.96, "Writing GeoTIFF")
    tags = {"source": source.name, "date": scene.date, "items": ",".join(scene.ids),
            "units": "surface reflectance (0-1)", "cloud_masked": mask_clouds}
    result = write_s2_outputs(Path(out), arrays, grid, region, indices=indices, preview=preview, tags=tags)
    cloud = scene.cloud   # the catalogue's cloud cover of the scene's tiles (%), for workflow conditions
    return {**result, "date": str(scene.date), "items": scene.ids, "cloud_pct": None if cloud is None or cloud != cloud else round(float(cloud), 1)}


def export_composite(source: Source, aoi: AOI | None, grid: Grid, start: str, end: str, out: str | Path, *,
                     bands: list[str], max_cloud: float | None = 60, max_scenes: int = 20,
                     stat: str = "median", indices: bool = False, preview: bool = True) -> dict:
    check_size(grid)
    region = aoi_mask(aoi, grid)
    progress.update(0.01, "Searching the catalog")
    items = s2.search(source, grid.bbox_lonlat(), start, end, max_cloud, intersects=search_geometry(aoi))
    scenes = s2.group_scenes(items)[:max_scenes]
    if not scenes:
        raise ValueError("No scenes found — widen the date range or raise the cloud limit.")
    dates = sorted(str(s.date) for s in scenes)
    log.info("Compositing %d dates: %s", len(scenes), ", ".join(dates))
    with progress.span(0.03, 0.95):
        arrays, count = s2.composite(source, scenes, grid, bands, stat)
    progress.update(0.96, "Writing GeoTIFF")
    tags = {"source": source.name, "period": f"{start}/{end}", "stat": stat,
            "dates": ",".join(dates), "units": "surface reflectance (0-1)"}
    result = write_s2_outputs(Path(out), arrays, grid, region, indices=indices, preview=preview,
                              tags=tags, extra=[("clear_obs_count", count)])
    clouds = [s.cloud for s in scenes if s.cloud is not None and s.cloud == s.cloud]
    return {**result, "dates": dates, "scenes": len(scenes), "cloud_pct": round(float(sum(clouds) / len(clouds)), 1) if clouds else None}


def export_labels(product: str, year: int, aoi: AOI | None, grid: Grid, out: str | Path) -> dict:
    from .extras import _rgb, fetch_labels

    check_size(grid)
    progress.update(0.05, "Downloading the land-cover map")
    data, spec = fetch_labels(product, year, grid)
    progress.update(0.85, "Writing GeoTIFF")
    region = aoi_mask(aoi, grid)
    if region is not None:
        data[~region] = 0
    colormap = {code: _rgb(color) for code, (_, color) in spec["classes"].items()}
    path = write_geotiff(out, data, grid, nodata=0, colormap=colormap, descriptions=[product],
                         tags={"product": spec["title"], "year": year,
                               "classes": json.dumps({k: v[0] for k, v in spec["classes"].items()})})
    log.info("Wrote %s", path)
    codes, counts = np.unique(data[data > 0], return_counts=True)
    total = int(counts.sum()) or 1
    distribution = [{"code": int(c), "name": spec["classes"].get(int(c), ("?",))[0],
                     "color": spec["classes"].get(int(c), ("", "#888888"))[1], "pct": 100 * int(n) / total}
                    for c, n in sorted(zip(codes, counts), key=lambda x: -x[1])]
    return {"files": [str(path)], "title": spec["title"], "distribution": distribution}
