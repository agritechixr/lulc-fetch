# lulc-fetch

Fetch analysis-ready satellite imagery for land-use / land-cover (LULC) work over any area:

- **Sentinel-2 L2A** (10 m, global, every ~5 days): from AWS Earth Search, Microsoft Planetary Computer or Copernicus Data Space. You get surface reflectance (0–1), cloud masking from the SCL layer, spectral indices, and cloud-free median composites.
- **LULC reference maps** for training and validation: ESA WorldCover 10 m and Esri/Impact Observatory annual LULC 10 m.
- **High-resolution / other imagery** from any Planetary Computer collection: NAIP 0.6 m aerial (USA only), Sentinel-1 radar, Copernicus DEM, and more.

Every layer is warped onto the same pixel grid (UTM, snapped to the resolution). Only the pixels inside your AOI are downloaded, not whole 110 km tiles.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
source .venv/bin/activate        # puts `lulc-fetch` on PATH
```

You don't need an account for the default sources (`earth-search`, `planetary-computer`).

## Web app

```bash
.venv/bin/pip install -e ".[web]"
lulc-fetch-web            # opens http://127.0.0.1:8000
```

1. **Area of interest.** You can:
   - draw a rectangle or polygon on the map
   - type lat/long, as a point plus radius or a bounding box (or pick the point on the map)
   - search an address or place name (OpenStreetMap). You can use the place's boundary, its bounding box, or a radius around it.
   - upload a **shapefile** (.zip, or .shp + .shx + .dbf + .prj; projected CRSs are reprojected), **GeoJSON**, **KML** or **KMZ**
2. **Dates and filters.** Choose the source (Earth Search, Planetary Computer or Copernicus), the date range and the maximum cloud %.
3. **Results.** One card per acquisition date shows a thumbnail, the tile cloud %, and how much of your area the tiles cover. Footprints appear on the map.
   - **Preview area** overlays the actual imagery for your area on the map, with a cloud/shadow mask and *clear % inside your area*.
   - **Metadata** shows the key properties, all assets, all STAC properties and the raw JSON.
   - **Download** offers four options: that date (clipped, cloud-masked bands + indices), a cloud-free composite of the range, an ESA WorldCover / Esri LULC label map on the same grid, or the original full .SAFE product from Copernicus.
4. **Downloads tab.** Jobs run in the background with live logs, a result preview and download links. Files are saved to `./downloads/<job>/`.

**Credentials** (top-right) stores your Copernicus account, Copernicus S3 keys and an optional Planetary Computer key in the OS keychain (macOS Keychain on a Mac). Secrets are never sent back to the browser, and they only go to the provider they belong to. **Test** checks them against the provider.

The server listens on `127.0.0.1` only and rejects requests from other websites. It is a personal, single-user tool. Hosting it for several people would need user accounts and per-user credential storage first.

## Command line

An AOI can be given as `--bbox minlon,minlat,maxlon,maxlat`, `--geojson file.geojson` (the output is masked to the polygon), `--point lon,lat --buffer-km 5`, or `--match existing.tif` (reuses that raster's exact grid).

```bash
# 1. See what's available
lulc-fetch search --bbox 77.56,12.94,77.61,12.99 --start 2026-01-01 --end 2026-03-31 --max-cloud 20

# 2. Single best date. It checks clouds *inside your AOI* on the 5 least-cloudy dates and keeps the clearest.
lulc-fetch scene --bbox 77.56,12.94,77.61,12.99 --start 2026-01-01 --end 2026-03-31 \
    --indices -o out/scene.tif

# 3. Cloud-free median composite. This is the best input for LULC classification, especially in the monsoon.
lulc-fetch composite --geojson aoi.geojson --start 2026-06-01 --end 2026-09-30 \
    --indices -o out/composite.tif

# 4. Reference labels on exactly the same grid, for training a classifier
lulc-fetch labels --match out/composite.tif --product worldcover --year 2021 -o out/labels_wc.tif
lulc-fetch labels --match out/composite.tif --product esri --year 2023 -o out/labels_esri.tif

# 5. Other collections: NAIP 0.6 m (USA), Sentinel-1 VV/VH radar, DEM
lulc-fetch fetch --point -122.335,47.608 --buffer-km 1 --res 0.6 --collection naip --assets image -o out/naip.tif
lulc-fetch fetch --match out/composite.tif --collection sentinel-1-rtc --assets vv,vh \
    --start 2026-07-01 --end 2026-07-31 -o out/s1.tif
lulc-fetch fetch --match out/composite.tif --collection cop-dem-glo-30 --assets data -o out/dem.tif

# 6. Full original .SAFE product from Copernicus (needs a free account)
export CDSE_USERNAME=you@example.com CDSE_PASSWORD=...
lulc-fetch product S2B_MSIL2A_20260211T050839_N0512_R019_T43PGQ_20260211T085923 --out-dir safe/
```

Useful options: `--bands all` or `--bands B02,B03,B04,B08`, `--res 20`, `--crs EPSG:4326`, `--source planetary-computer|cdse`, `--keep-clouds`, `--date YYYY-MM-DD`, `--max-scenes`, `--stat mean`.

### Outputs

- `*.tif`: a float32 GeoTIFF with one band per layer. Band descriptions (`B02`, …, `NDVI`, …) are set, so QGIS and rasterio show names. NaN means no data or cloud. Composites add `clear_obs_count`. The metadata tags record the source, dates and item IDs.
- `*.png`: a true-colour quicklook.
- Label maps: uint8 with an embedded colour table, so they render with the official class colours in QGIS.

Indices (`--indices`): NDVI, NDWI, MNDWI, NDBI, BSI, SAVI.

## Sources

| `--source` | Login | Format | Notes |
|---|---|---|---|
| `earth-search` (default) | none | COG on AWS | fastest; windowed reads |
| `planetary-computer` | none | COG on Azure | URLs signed automatically; same values as Earth Search |
| `cdse` | free S3 keys | JP2 on S3 | official ESA archive. Create keys at https://eodata-s3keysmanager.dataspace.copernicus.eu/, then `export CDSE_S3_ACCESS_KEY=… CDSE_S3_SECRET_KEY=…` |

The tool applies the processing-baseline 04.00 reflectance offset (−1000 DN) per item, so values are consistent across sources and dates. Earth Search items that already have it applied are detected.

## About "high resolution"

Sentinel-2 at **10 m** is the highest-resolution *free, global, frequently updated* optical imagery. Free options that are finer than that:

- **NAIP** 0.6–1 m, USA only (`fetch --collection naip`).
- **Maxar Open Data**: sub-metre, but only around disaster events.
- National programmes, e.g. Bhuvan/NRSC (India), IGN (France) and PDOK (Netherlands), each with their own portals and licences.

Commercial providers (Planet 3 m, Maxar/Airbus <1 m) need paid access.

## Limits

- One request is capped at 60 M pixels (≈77×77 km at 10 m). Tile larger regions or use `--res 20`.
- Composites keep about `n_scenes × H × W × 4` bytes per band group in memory, budgeted at about 1.5 GB.

## License

Apache License 2.0. See [LICENSE](LICENSE).
