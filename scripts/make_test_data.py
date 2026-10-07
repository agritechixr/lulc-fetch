"""Make a test data kit: free, open data for six 2 × 2 km sites, one folder each, with a README.txt that lists the
files and which LULC Fetch tools to try on them. Nothing needs an account.

    .venv/bin/python scripts/make_test_data.py [OUT_DIR] [--only 01,03] [--force]

Sources: Sentinel-2 L2A (Earth Search, AWS), Sentinel-1 RTC, NAIP and the Copernicus DEM (Microsoft Planetary
Computer), ESA WorldCover (AWS), Google AlphaEarth embeddings (Source Cooperative), OpenStreetMap (Overpass API and
tiles) and air quality from Open-Meteo (CAMS). Each file is downloaded once; run again to fill in what failed.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import math
import random
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UA = {"User-Agent": "LULC-Fetch-test-data/1.0 (https://github.com/agritechixr/lulc-fetch)"}
WORLDCOVER = {10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland", 50: "Built-up", 60: "Bare / sparse vegetation",
              70: "Snow and ice", 80: "Permanent water bodies", 90: "Herbaceous wetland", 95: "Mangroves", 100: "Moss and lichen"}
S2_BANDS = "B02,B03,B04,B05,B06,B07,B08,B8A,B11,B12"

SITES = {
    "01_nandi_hills_terrain": dict(lon=77.6835, lat=13.3702, title="Nandi Hills, Karnataka, India",
        about="A granite hill rising about 600 m above the plain, with forest, scrub, farms and villages around it."),
    "02_bengaluru_hebbal_urban": dict(lon=77.5920, lat=13.0450, title="Hebbal, Bengaluru, India",
        about="Dense city: Hebbal lake, the flyover and ring road, apartment blocks, parks and many OpenStreetMap features."),
    "03_mandya_farmland": dict(lon=76.8500, lat=12.6500, title="Farmland north of Mandya, Karnataka, India",
        about="Irrigated farmland (paddy, sugarcane, coconut groves) along the canals: about half cropland and a third tree cover in "
              "WorldCover. Two seasons of Sentinel-2, Sentinel-1 radar and AlphaEarth embeddings."),
    "04_bengaluru_airport_change": dict(lon=77.7070, lat=13.1990, title="Kempegowda International Airport, Bengaluru, India",
        about="The airport's second runway and Terminal 2 were built between 2018 and 2024: a clear case for change detection."),
    "05_kaveri_river": dict(lon=76.6880, lat=12.4180, title="Kaveri river at Srirangapatna, Karnataka, India",
        about="A river with islands, channels and irrigated fields: water, centrelines and river banks."),
    "06_seattle_naip_aerial": dict(lon=-122.3330, lat=47.6050, title="Downtown Seattle, Washington, USA",
        about="0.6 m NAIP aerial photos (red, green, blue, near-infrared): cars, buildings, roads and the waterfront for object detection."),
}

LICENCES = {
    "s2": "Sentinel-2 L2A: Copernicus Sentinel data (free, full and open), via Element 84 Earth Search on AWS.",
    "s1": "Sentinel-1 RTC: Copernicus Sentinel data, terrain-corrected by Microsoft Planetary Computer (free).",
    "dem": "Copernicus DEM GLO-30: © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the EU and ESA (free licence).",
    "wc": "ESA WorldCover 10 m 2020 / 2021: © ESA WorldCover project, CC BY 4.0.",
    "osm": "OpenStreetMap data and map tiles: © OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright).",
    "aef": "Google AlphaEarth Foundations satellite embeddings: CC BY 4.0 (Google / Source Cooperative).",
    "naip": "NAIP aerial imagery: USDA Farm Production and Conservation, public domain, via Microsoft Planetary Computer.",
    "aq": "Air quality: Open-Meteo (CC BY 4.0), based on Copernicus Atmosphere Monitoring Service (CAMS) data.",
}


def bbox(site: dict, half_km: float = 1.0) -> tuple[float, float, float, float]:
    dlat = half_km / 110.574
    dlon = half_km / (111.320 * math.cos(math.radians(site["lat"])))
    return site["lon"] - dlon, site["lat"] - dlat, site["lon"] + dlon, site["lat"] + dlat


def cli(*args: str) -> None:
    from lulc_fetch import cli as c
    c.main(list(args))


class Kit:
    def __init__(self, out: Path, force: bool):
        self.out, self.force, self.log = out, force, []

    def step(self, folder: Path, name: str, what: str, fn) -> bool:
        """Make one file (skipped when it exists); a failure is logged and the rest goes on."""
        target = folder / name
        if target.exists() and not self.force:
            print(f"  ✓ {name} (already there)")
            return True
        print(f"  … {name}: {what}", flush=True)
        t0 = time.time()
        try:
            fn(target)
            print(f"  ✓ {name} ({time.time() - t0:.0f} s)", flush=True)
            return True
        except (Exception, SystemExit) as e:  # noqa: BLE001 — one failed download mustn't stop the kit
            self.log.append(f"{folder.name}/{name}: {e}")
            print(f"  ✗ {name}: {e}", flush=True)
            traceback.print_exc(limit=2)
            with contextlib.suppress(OSError):
                target.unlink()
            return False


# ------------------------------------------------------------------ downloads
def s2(site, start, end, max_cloud=30):
    def f(t):
        cli("composite", "--point", f"{site['lon']},{site['lat']}", "--buffer-km", "1", "--start", start, "--end", end,
            "--max-cloud", str(max_cloud), "--max-scenes", "12", "--bands", S2_BANDS, "--indices", "-o", str(t))
    return f


def labels(match: Path, year: int):
    return lambda t: cli("labels", "--match", str(match), "--product", "worldcover", "--year", str(year), "-o", str(t))


def pc(site, collection, assets, res, start=None, end=None, nearest=False):
    def f(t):
        args = ["fetch", "--point", f"{site['lon']},{site['lat']}", "--buffer-km", "1", "--res", str(res), "--collection", collection,
                "--assets", assets, "--no-preview", "-o", str(t)]
        if start:
            args += ["--start", start, "--end", end]
        if nearest:
            args.append("--nearest")
        cli(*args)
    return f


def naip(site):
    def f(t):
        tmp = t.with_name("_naip_float.tif")
        pc(site, "naip", "image", 0.6, "2019-01-01", "2025-12-31")(tmp)
        import rasterio
        with rasterio.open(tmp) as s:   # NAIP is 8-bit: stored as such (a quarter of the float file)
            a = s.read()
            prof = s.profile.copy()
            prof.update(dtype="uint8", nodata=0, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
            with rasterio.open(t, "w", **prof) as d:
                d.write(np.nan_to_num(np.clip(a, 0, 255), nan=0).astype("uint8"))
                d.descriptions = ("Red", "Green", "Blue", "NIR")[:s.count]
                d.update_tags(**s.tags())
        tmp.unlink()
    return f


def embeddings(site, year=2024):
    def f(t):
        from lulc_fetch import embeddings as em
        b = bbox(site)
        geom = {"type": "Polygon", "coordinates": [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]}
        em.fetch(geom, "aef", year, str(t), ROOT / "embeddings_cache", 10)
    return f


def overpass(site):
    """OpenStreetMap features of the box as GeoJSON layers: buildings, roads, water, land use, points of interest."""
    def f(t):
        s, w, n, e = bbox(site)[1], bbox(site)[0], bbox(site)[3], bbox(site)[2]
        q = f"""[out:json][timeout:120];(way["building"]({s},{w},{n},{e});way["highway"]({s},{w},{n},{e});
            way["natural"="water"]({s},{w},{n},{e});way["waterway"]({s},{w},{n},{e});way["landuse"]({s},{w},{n},{e});
            way["leisure"="park"]({s},{w},{n},{e});node["amenity"]({s},{w},{n},{e});node["shop"]({s},{w},{n},{e}););out geom;"""
        mirrors = ("https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
                   "https://overpass.kumi.systems/api/interpreter")
        els, errs = None, []
        for attempt in range(3):   # the public servers are often busy: each mirror, three rounds, waiting longer each time
            for url in mirrors:
                try:
                    r = requests.post(url, data={"data": q}, headers=UA, timeout=120)
                    r.raise_for_status()
                    els = r.json()["elements"]
                    break
                except (requests.RequestException, ValueError) as ex:
                    errs.append(f"{url.split('/')[2]}: {str(ex)[:80]}")
            if els is not None:
                break
            time.sleep(30 * (attempt + 1))
        if els is None:
            raise RuntimeError("Overpass didn't answer → run the script again later. " + " | ".join(errs[-3:]))
        layers = {k: [] for k in ("buildings", "roads", "water", "landuse", "points_of_interest")}
        for el in els:
            tags = el.get("tags", {})
            props = {k: v for k, v in tags.items() if k in ("name", "building", "highway", "natural", "waterway", "landuse", "leisure", "amenity", "shop", "levels", "building:levels", "lanes", "surface", "oneway")}
            props["osm_id"] = el["id"]
            if el["type"] == "node":
                if "amenity" in tags or "shop" in tags:
                    layers["points_of_interest"].append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]}})
                continue
            pts = [[p["lon"], p["lat"]] for p in el.get("geometry") or []]
            if len(pts) < 2:
                continue
            closed = pts[0] == pts[-1] and len(pts) >= 4
            if "building" in tags and closed:
                layers["buildings"].append({"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [pts]}})
            elif "highway" in tags:
                layers["roads"].append({"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": pts}})
            elif "waterway" in tags:
                layers["water"].append({"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": pts}})
            elif tags.get("natural") == "water" and closed:
                layers["water"].append({"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [pts]}})
            elif ("landuse" in tags or "leisure" in tags) and closed:
                layers["landuse"].append({"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [pts]}})
        t.mkdir(parents=True, exist_ok=True)
        for k, feats in layers.items():
            if feats:
                (t / f"osm_{k}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
        (t / "counts.json").write_text(json.dumps({k: len(v) for k, v in layers.items()}, indent=1), encoding="utf-8")
    return f


def air_quality(site):
    """Hourly PM2.5, PM10 and NO₂ for the past 7 days at a 5 × 5 grid of points 5 km apart (interpolation, forecasting)."""
    def f(t):
        pts = [(site["lat"] + (i - 2) * 0.045, site["lon"] + (j - 2) * 0.046) for i in range(5) for j in range(5)]
        r = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", headers=UA, timeout=120, params={
            "latitude": ",".join(f"{a:.4f}" for a, _ in pts), "longitude": ",".join(f"{b:.4f}" for _, b in pts),
            "hourly": "pm2_5,pm10,nitrogen_dioxide", "past_days": 7, "forecast_days": 1, "timezone": "Asia/Kolkata"})
        r.raise_for_status()
        js = r.json()
        js = js if isinstance(js, list) else [js]
        with open(t, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["station", "latitude", "longitude", "time", "pm2_5", "pm10", "no2"])
            for k, (p, d) in enumerate(zip(pts, js)):
                h = d["hourly"]
                for i, tm in enumerate(h["time"]):
                    w.writerow([f"P{k + 1:02d}", f"{p[0]:.4f}", f"{p[1]:.4f}", tm, h["pm2_5"][i], h["pm10"][i], h["nitrogen_dioxide"][i]])
    return f


def scanned_map(site, zoom=15):
    """An OpenStreetMap map picture with no coordinates (like a scanned paper map), for Georeference."""
    def f(t):
        from PIL import Image
        n = 2 ** zoom
        x0 = int((site["lon"] + 180) / 360 * n)
        y0 = int((1 - math.asinh(math.tan(math.radians(site["lat"]))) / math.pi) / 2 * n)
        img = Image.new("RGB", (3 * 256, 3 * 256))
        for dx in range(3):
            for dy in range(3):
                r = requests.get(f"https://tile.openstreetmap.org/{zoom}/{x0 - 1 + dx}/{y0 - 1 + dy}.png", headers=UA, timeout=60)
                r.raise_for_status()
                img.paste(Image.open(io.BytesIO(r.content)).convert("RGB"), (dx * 256, dy * 256))
                time.sleep(0.3)
        img.save(t)
        lon = lambda x: x / n * 360 - 180
        lat = lambda y: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
        t.with_suffix(".corners.txt").write_text(
            "The true corners of scanned_map.png (to check your georeferencing; don't load this into the tool):\n"
            f"  top-left     lon {lon(x0 - 1):.6f}  lat {lat(y0 - 1):.6f}\n  bottom-right lon {lon(x0 + 2):.6f}  lat {lat(y0 + 2):.6f}\n"
            "Web Mercator tiles (EPSG:3857), zoom 15. © OpenStreetMap contributors.\n", encoding="utf-8")
    return f


def sample_points(classes_tif: Path, per_class: int, seed: int, field="class"):
    """Stratified random points from a WorldCover map: per_class points of each class, with the class name."""
    def f(t):
        import rasterio
        from rasterio.warp import transform as wt
        with rasterio.open(classes_tif) as s:
            a = s.read(1)
            rng = np.random.default_rng(seed)
            feats = []
            for v in np.unique(a):
                if int(v) not in WORLDCOVER:
                    continue
                rr, cc = np.nonzero(a == v)
                if rr.size < 30:
                    continue
                pick = rng.choice(rr.size, min(per_class, rr.size), replace=False)
                xs, ys = rasterio.transform.xy(s.transform, rr[pick], cc[pick])
                lons, lats = wt(s.crs, "EPSG:4326", list(np.atleast_1d(xs)), list(np.atleast_1d(ys)))
                feats += [{"type": "Feature", "properties": {field: WORLDCOVER[int(v)], "code": int(v)},
                           "geometry": {"type": "Point", "coordinates": [round(x, 7), round(y, 7)]}} for x, y in zip(lons, lats)]
        t.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return f


def gps_track(roads: Path):
    """A made-up GPS track: points every ~25 m along the longest road, one per 5 seconds (Points → lines)."""
    def f(t):
        from shapely.geometry import shape
        fc = json.loads(roads.read_text())
        line = max((shape(x["geometry"]) for x in fc["features"] if x["geometry"]["type"] == "LineString"), key=lambda g: g.length)
        step = 25 / 111_000
        n = max(2, int(line.length / step))
        t0 = time.mktime((2025, 3, 1, 8, 0, 0, 0, 0, -1))
        feats = [{"type": "Feature", "properties": {"vehicle": "bus_1", "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0 + 5 * i)), "seq": i + 1},
                  "geometry": {"type": "Point", "coordinates": list(line.interpolate(i / (n - 1), normalized=True).coords[0])}} for i in range(n)]
        random.Random(1).shuffle(feats)   # stored out of order: Points → lines puts them in time order
        t.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return f


def study_area(site):
    def f(t):
        b = bbox(site)
        t.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": site["title"]},
            "geometry": {"type": "Polygon", "coordinates": [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]}}]}), encoding="utf-8")
    return f


# ------------------------------------------------------------------ the sites
def build(kit: Kit, key: str):
    site, d = SITES[key], kit.out / key
    d.mkdir(parents=True, exist_ok=True)
    print(f"\n{key}: {site['title']}", flush=True)
    st = kit.step
    st(d, "study_area.geojson", "the 2 × 2 km box", study_area(site))
    if key == "01_nandi_hills_terrain":
        st(d, "sentinel2_2025_jan-mar.tif", "Sentinel-2 composite", s2(site, "2025-01-01", "2025-03-31"))
        st(d, "dem_copernicus_30m.tif", "Copernicus DEM", pc(site, "cop-dem-glo-30", "data", 30))
        if (d / "sentinel2_2025_jan-mar.tif").exists():
            st(d, "worldcover_2021.tif", "ESA WorldCover 2021", labels(d / "sentinel2_2025_jan-mar.tif", 2021))
    elif key == "02_bengaluru_hebbal_urban":
        st(d, "sentinel2_2025_jan-mar.tif", "Sentinel-2 composite", s2(site, "2025-01-01", "2025-03-31"))
        if (d / "sentinel2_2025_jan-mar.tif").exists():
            st(d, "worldcover_2020.tif", "ESA WorldCover 2020", labels(d / "sentinel2_2025_jan-mar.tif", 2020))
            st(d, "worldcover_2021.tif", "ESA WorldCover 2021", labels(d / "sentinel2_2025_jan-mar.tif", 2021))
        st(d, "osm", "OpenStreetMap features", overpass(site))
        if (d / "osm" / "osm_roads.geojson").exists():
            st(d, "gps_track_points.geojson", "a GPS track", gps_track(d / "osm" / "osm_roads.geojson"))
        st(d, "air_quality_hourly_25_points.csv", "air quality, 25 points", air_quality(site))
        st(d, "scanned_map.png", "a map picture without coordinates", scanned_map(site))
    elif key == "03_mandya_farmland":
        st(d, "sentinel2_2024_kharif_sep-nov.tif", "Sentinel-2, monsoon season", s2(site, "2024-09-15", "2024-11-30", 60))
        st(d, "sentinel2_2025_rabi_jan-mar.tif", "Sentinel-2, dry season", s2(site, "2025-01-15", "2025-03-31"))
        st(d, "sentinel1_vv_vh_2025_jan-feb.tif", "Sentinel-1 radar", pc(site, "sentinel-1-rtc", "vv,vh", 10, "2025-01-01", "2025-02-28"))
        st(d, "alphaearth_embedding_2024.tif", "AlphaEarth embeddings 2024 (64 bands)", embeddings(site, 2024))
        if (d / "sentinel2_2025_rabi_jan-mar.tif").exists():
            st(d, "worldcover_2021.tif", "ESA WorldCover 2021", labels(d / "sentinel2_2025_rabi_jan-mar.tif", 2021))
        if (d / "worldcover_2021.tif").exists():
            st(d, "training_points.geojson", "training points (30 per class)", sample_points(d / "worldcover_2021.tif", 30, 1))
            st(d, "validation_points.geojson", "validation points (20 per class)", sample_points(d / "worldcover_2021.tif", 20, 2, field="reference"))
        st(d, "osm", "OpenStreetMap features", overpass(site))
    elif key == "04_bengaluru_airport_change":
        st(d, "sentinel2_2018_jan-mar.tif", "Sentinel-2 2018", s2(site, "2018-01-01", "2018-03-31"))
        st(d, "sentinel2_2025_jan-mar.tif", "Sentinel-2 2025", s2(site, "2025-01-01", "2025-03-31"))
        if (d / "sentinel2_2025_jan-mar.tif").exists():
            st(d, "worldcover_2020.tif", "ESA WorldCover 2020", labels(d / "sentinel2_2025_jan-mar.tif", 2020))
            st(d, "worldcover_2021.tif", "ESA WorldCover 2021", labels(d / "sentinel2_2025_jan-mar.tif", 2021))
    elif key == "05_kaveri_river":
        st(d, "sentinel2_2025_jan-mar.tif", "Sentinel-2 composite", s2(site, "2025-01-01", "2025-03-31"))
        st(d, "dem_copernicus_30m.tif", "Copernicus DEM", pc(site, "cop-dem-glo-30", "data", 30))
        if (d / "sentinel2_2025_jan-mar.tif").exists():
            st(d, "worldcover_2021.tif", "ESA WorldCover 2021", labels(d / "sentinel2_2025_jan-mar.tif", 2021))
        st(d, "osm", "OpenStreetMap features", overpass(site))
    elif key == "06_seattle_naip_aerial":
        st(d, "naip_0.6m_rgbn.tif", "NAIP aerial photo, 0.6 m", naip(site))
        st(d, "osm", "OpenStreetMap features", overpass(site))
    (d / "README.txt").write_text(readme(key, d), encoding="utf-8")


def files_of(d: Path) -> list[str]:
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.name != "README.txt" and not p.name.endswith(".aux.xml"):
            out.append(f"  {str(p.relative_to(d)):<44} {p.stat().st_size / 1e6:8.2f} MB{describe(p)}")
    return out


def describe(p: Path) -> str:
    if p.suffix == ".tif":
        try:
            import rasterio
            with rasterio.open(p) as s:
                return f"   {s.width} × {s.height} px, {s.count} band(s), {abs(s.res[0]):g} m, {s.crs}"
        except Exception:  # noqa: BLE001
            return ""
    if p.suffix == ".geojson":
        try:
            fc = json.loads(p.read_text())
            kinds = sorted({f["geometry"]["type"] for f in fc["features"]})
            return f"   {len(fc['features'])} features ({', '.join(kinds)})"
        except Exception:  # noqa: BLE001
            return ""
    return ""


TOOLS = {
    "01_nandi_hills_terrain": """
TOOLS TO TRY HERE
  Raster & terrain ▸ Terrain           dem_copernicus_30m.tif → slope, aspect, hillshade
  Raster & terrain ▸ Contours          dem_copernicus_30m.tif, every 20 or 50 m
  Insert ▸ 3D map                      add the DEM (a surface), then drape sentinel2_*.tif or worldcover_2021.tif on it
  Measure ▸ Elevation profile          draw a line across the hill over the DEM
  Raster & terrain ▸ Reclassify        the DEM into height bands (e.g. < 1000, 1000–1300, > 1300 m)
  Raster & terrain ▸ Resample          the DEM from 30 m to 10 m (cubic spline) to match Sentinel-2
  Stack layers                         Sentinel-2 + the DEM (+ slope) onto one grid
  Index analysis                       NDVI, NDWI, BSI on sentinel2_2025_jan-mar.tif
  Enhance image                        Sentinel-2 bands 3,2,1 with the "Computer vision" preset; compare with Swipe
  Zonal statistics                     mean slope / height inside the classes of worldcover_2021.tif (Raster to polygon first)
  Assistant                            "make a hillshade and contours every 50 m of the DEM"
""",
    "02_bengaluru_hebbal_urban": """
TOOLS TO TRY HERE
  Vector ▸ Buffer                      osm/osm_roads.geojson by 20 m; osm/osm_water.geojson (Hebbal lake) by 100 m
  Vector ▸ Select by attribute         osm_buildings: building == "apartments"; osm_roads: highway in ("primary", "trunk")
  Vector ▸ Overlay                     buildings × the lake buffer (intersection, difference)
  Vector ▸ Dissolve                    osm_landuse by landuse
  Spatial analysis ▸ Select by location  points of interest within 300 m of the lake
  Spatial analysis ▸ Spatial join      points of interest ← the land-use polygon they are in
  Spatial analysis ▸ Count points      points of interest per fishnet cell (Geometry tools ▸ Fishnet, 250 m)
  Spatial analysis ▸ Calculate geometry  area of every building in m²
  Spatial analysis ▸ Zonal statistics  % of each WorldCover class per fishnet cell (tick "classes")
  Conversion ▸ Vector to raster        osm_buildings → building footprint raster (presence), on sentinel2_*.tif's grid
  Conversion ▸ Convert features        gps_track_points.geojson → Points → lines, order by time
  Conversion ▸ Convert features        osm_buildings → polygons → lines, vertices → points, bounding boxes
  Raster & terrain ▸ Change detection  worldcover_2020.tif → worldcover_2021.tif (tick class maps)
  Interpolation                        air_quality_hourly_25_points.csv (one hour) → PM2.5 surface (IDW, kriging)
  Forecast ▸ Train forecasting model   air_quality_hourly_25_points.csv: time = time, series = station, target = pm2_5
  Georeference                         scanned_map.png: click 4+ road crossings on the basemap (check with scanned_map.corners.txt)
  Style by attribute                   osm_buildings coloured by building type; osm_roads by highway
""",
    "03_mandya_farmland": """
TOOLS TO TRY HERE
  Classical ML for raster              sentinel2_2025_rabi_jan-mar.tif + training_points.geojson (field: class) → land-cover map
  Accuracy assessment                  the map + validation_points.geojson (field: reference) → confusion matrix, kappa, areas
  Area statistics                      hectares per class of the classified map or worldcover_2021.tif
  Raster to polygon                    the classified map → field polygons (minimum area 2000 m², dissolve off)
  Raster → table → Classical ML (tabular)  pixels with labels from training_points
  Embeddings ▸ Classify / Explore      alphaearth_embedding_2024.tif (64 bands) with training_points; find similar fields
  PCA                                  alphaearth_embedding_2024.tif → 3 components
  Index analysis                       NDVI of both Sentinel-2 seasons; SAR indices (RVI, VV dB) on sentinel1_vv_vh_*.tif
  Raster calculator                    NDVI_rabi − NDVI_kharif (crop calendar difference)
  Change detection                     NDVI kharif → NDVI rabi (values)
  Stack layers                         Sentinel-2 + Sentinel-1 (+ embeddings) for one classifier
  Make training data → Train classify model  sentinel2 + worldcover_2021.tif as labels (64 × 64 patches)
  NDVI time series                     click a field: NDVI through 2024–2025 from the free Sentinel-2 catalogue
""",
    "04_bengaluru_airport_change": """
TOOLS TO TRY HERE
  Raster & terrain ▸ Change detection  NDVI of sentinel2_2018 → NDVI of sentinel2_2025 (new runway and terminal: big drops)
  Change detection (classes)           worldcover_2020.tif → worldcover_2021.tif: from → to table with hectares
  Swipe / Side by side                 sentinel2_2018 vs sentinel2_2025 (bands 4,3,2)
  Index analysis                       NDBI (built-up) of both years
  Raster calculator                    NDBI_2025 − NDBI_2018 > 0.1 → new built-up mask
  Raster to polygon                    that mask → polygons of new construction, with hectares
  Resample / reproject                 both years to 20 m (average) or to EPSG:4326
  Enhance image                        2018 image: CLAHE, then Detect object or classify
""",
    "05_kaveri_river": """
TOOLS TO TRY HERE
  Index analysis                       MNDWI / NDWI of sentinel2_2025_jan-mar.tif → water
  Reclassify                           MNDWI > 0 → water (1)
  Conversion ▸ Raster to polyline      the water mask, "Centrelines" → the river's centre line(s)
  Conversion ▸ Raster to polygon       the water mask → river and island polygons with hectares
  Conversion ▸ Raster to polyline      worldcover_2021.tif, "Boundaries" → class edges
  Vector ▸ Buffer                      osm/osm_water.geojson (the river) by 100 m → riparian zone
  Zonal statistics                     % of each land-cover class inside the riparian buffer
  Terrain / Contours                   dem_copernicus_30m.tif (the valley)
  Points along lines                   the river line every 200 m → sampling points
""",
    "06_seattle_naip_aerial": """
TOOLS TO TRY HERE
  Detect object                        naip_0.6m_rgbn.tif, bands 1,2,3: YOLO26 aerial (DOTA) → cars, ships, planes; SAM 2.1 → outlines
  Train detection model                draw boxes or points on cars, then train YOLO
  Enhance image                        CLAHE + sharpen before detection; Enlarge ×2 (lanczos) for small cars
  Resample / reproject                 0.6 m → 2 m (average) to compare with satellite pixels
  Index analysis                       NDVI (NAIP has a near-infrared band 4): trees and parks
  Conversion ▸ Vector to raster        osm/osm_buildings.geojson on the NAIP grid → building labels
  Make training data                   NAIP + that building raster → image / label patches (256 × 256)
  Raster to polygon                    a reclassified NDVI (> 0.3) → green-space polygons
  Overlay / Select by location         buildings within 50 m of the roads
""",
}


def readme(key: str, d: Path) -> str:
    site = SITES[key]
    b = bbox(site)
    srcs = {"01": "s2 dem wc", "02": "s2 wc osm aq", "03": "s2 s1 aef wc osm", "04": "s2 wc", "05": "s2 dem wc osm", "06": "naip osm"}[key[:2]]
    return (f"{site['title']}  ·  {key}\n{'=' * 78}\n{site['about']}\n\n"
            f"Area: 2 × 2 km around lon {site['lon']}, lat {site['lat']}  (box {b[0]:.5f}, {b[1]:.5f}, {b[2]:.5f}, {b[3]:.5f})\n"
            f"Rasters are in the area's UTM zone unless said otherwise. Add any file with Insert ▸ Add data (or drag it onto the map).\n\n"
            f"FILES\n" + "\n".join(files_of(d) or ["  (nothing downloaded yet: run the script again)"]) + "\n" + TOOLS[key] +
            "\nSOURCES AND LICENCES\n" + "\n".join(f"  {LICENCES[s]}" for s in srcs.split()) +
            "\n\nMade by scripts/make_test_data.py of LULC Fetch; run it again to update.\n")


TOP = """LULC Fetch test data
====================
Free, open data for six 2 × 2 km sites, to try every tool of LULC Fetch. Each folder has a README.txt with its files
and the tools to try on them; TOOLS_CHECKLIST.txt lists every tool and where to test it.

  01_nandi_hills_terrain        DEM, Sentinel-2, WorldCover       terrain, contours, 3D, resample, enhance
  02_bengaluru_hebbal_urban     Sentinel-2, WorldCover 2020/21, OpenStreetMap, air quality, a map picture
                                vector & spatial analysis, conversion, interpolation, forecasting, georeference
  03_mandya_farmland            Sentinel-2 (2 seasons), Sentinel-1, AlphaEarth, training & validation points
                                classification, accuracy, embeddings, deep learning, time series
  04_bengaluru_airport_change   Sentinel-2 2018 and 2025, WorldCover 2020/21    change detection
  05_kaveri_river               Sentinel-2, DEM, WorldCover, OSM river           water, centrelines, buffers
  06_seattle_naip_aerial        NAIP 0.6 m aerial photo, OSM                     object detection, training data

Remake or update:  .venv/bin/python scripts/make_test_data.py "{out}"
"""

CHECKLIST = """Every tool, and the folder to test it in
=========================================
Imagery & preparation
  Find imagery (download)          any folder's study_area.geojson as the area      ·  Index analysis       01, 03, 04, 05
  PCA                              03 alphaearth_embedding_2024.tif                 ·  Stack layers         01, 03
  Training samples                 03 (draw on sentinel2)                           ·  Raster → table       03
  Make training data               03, 06
Classical ML & deep learning
  Classical ML for raster          03  ·  Classical ML (tabular)  03  ·  Clustering & t-SNE  03
  Train classify model / Classify image   03 (WorldCover labels), 06 (building labels)
  Detect object / Train detection model   06
Embeddings
  Download / Explore / Convert / Train / Classify embeddings   03 alphaearth_embedding_2024.tif
Vector & spatial analysis
  Buffer, Select by attribute, Overlay, Dissolve               02, 05
  Zonal statistics, Select by location, Spatial join, Count points, Calculate geometry, Join table   02
  Geometry tools (centroids, hull, simplify, merge, explode, fishnet, random points)                02
Raster & terrain
  Terrain, Contours                01, 05      ·  Reclassify        01, 05      ·  Change detection   02, 04
  Clip raster                      any folder with study_area.geojson or OSM polygons
  Resample / reproject             01, 04, 06  ·  Enhance image     01, 04, 06
Conversion
  Raster to polygon                03, 04, 05  ·  Raster to polyline  05 (centrelines), any WorldCover (boundaries)
  Raster to point                  01 worldcover_2021.tif                     ·  Vector to raster    02, 06
  Convert features                 02 (gps_track_points, osm_buildings), 05 (river)
Maps & more
  3D map, Elevation profile        01, 05      ·  Swipe, Side by side   04     ·  Georeference   02 scanned_map.png
  Interpolation                    02 air_quality_hourly_25_points.csv        ·  Forecast       02 (same file)
  Style by attribute               02 OSM layers  ·  Area statistics  03, 04  ·  Accuracy assessment  03
  Raster calculator                03, 04         ·  NDVI time series  03     ·  Assistant, Workflows  any folder
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", default=str(Path.home() / "Desktop" / "LULC_Fetch_test_data"))
    ap.add_argument("--only", help="comma list of site numbers, e.g. 01,03")
    ap.add_argument("--force", action="store_true", help="download again files that exist")
    a = ap.parse_args()
    import logging
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    for noisy in ("rasterio", "urllib3", "pystac_client", "botocore", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    kit = Kit(Path(a.out), a.force)
    kit.out.mkdir(parents=True, exist_ok=True)
    keys = [k for k in SITES if not a.only or k[:2] in a.only.split(",")]
    for k in keys:
        build(kit, k)
    (kit.out / "README.txt").write_text(TOP.format(out=kit.out), encoding="utf-8")
    (kit.out / "TOOLS_CHECKLIST.txt").write_text(CHECKLIST, encoding="utf-8")
    print("\nDone." if not kit.log else "\nSome downloads failed (run again to retry):\n  " + "\n  ".join(kit.log))


if __name__ == "__main__":
    main()
