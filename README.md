# lulc-fetch

A toolkit for land-use / land-cover (LULC) work with free satellite data. It has a **desktop GIS-style web app** and a **command-line tool**:

- **Find and download Sentinel-2 L2A** (10 m, every ~5 days) from AWS Earth Search, Microsoft Planetary Computer or Copernicus Data Space, and **Landsat 8–9 Collection 2 L2** (30 m), with original Landsat bundles from USGS EarthExplorer:
  - surface reflectance, cloud masking, cloud-free median composites
  - only the pixels inside your area of interest are downloaded
- **Open your own Copernicus products** (`.SAFE`): Sentinel-2 L1C/L2A with all bands, and Sentinel-1 GRD converted to calibrated radar backscatter (VV/VH, dB).
- **30 spectral and radar indices** (NDVI, SAVI, EVI, NDWI, NDBI, NBR, RVI…) or your own formula, on any GeoTIFF. Band-order checks catch mislabelled files.
- **PCA & dimensionality reduction** with scikit-learn: PCA, Incremental PCA, Kernel PCA, NMF, FastICA, Truncated SVD, Factor Analysis.
- **LULC reference maps** for training and validation: ESA WorldCover 10 m and Esri/Impact Observatory annual LULC 10 m.
- **Other collections** from Planetary Computer: NAIP 0.6 m aerial (USA), Sentinel-1 RTC, Copernicus DEM, and more.
- **Export any layer** as GeoTIFF, PNG (optionally georeferenced), Shapefile, GeoJSON or KML, for the whole layer or just an area you choose.

## Install and run

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[web]"     # use -e . for the command-line tool only
.venv/bin/lulc-fetch-web              # web app → http://127.0.0.1:8000
.venv/bin/lulc-fetch --help           # command-line tool
```

Requires Python 3.10+. You don't need an account for the default sources (`earth-search`, `planetary-computer`). A Copernicus account is only needed for the `cdse` source and for full-product downloads.

## Web app

A desktop GIS-style workspace, laid out like QGIS or ArcGIS:

```
┌ File  Tools ▾  View  Help ─────────────────────────────── Credentials ┐
│ Contents        │                                    │ Tool panel     │
│ ☑ NDVI · scene  │               map                  │ (the tool you  │
│ ☑ AOI           │                                    │  picked in     │
│ ☑ S2 scene      │                                    │  Tools)        │
├─────────────────┴────────────────────────────────────┴────────────────┤
│ Ready              Lat 12.96501  Lon 77.58500   Scale 1 : 25,000  z 14 │
└────────────────────────────────────────────────────────────────────────┘
```

- **Menu bar**
  - **File:** add data, add from the workspace, open a Sentinel product (.SAFE), export / properties / remove the selected layer, credentials.
  - **Tools ▾:** Find imagery, Index analysis, PCA & dimensionality reduction, Export data, Downloads & jobs. The right-hand panel only opens when you pick a tool; × closes it.
  - **View:** show or hide the panels, choose the basemap (streets, satellite, topographic, none), place labels, zoom to all layers, theme.
  - **Help:** getting started, quick guide, keyboard shortcuts (<kbd>Ctrl/⌘ O</kbd> add data, <kbd>Ctrl/⌘ E</kbd> export, <kbd>Del</kbd> remove layer, <kbd>Ctrl/⌘ 1</kbd> / <kbd>2</kbd> toggle panels).
- **Contents (left)** holds every layer:
  - **local files you add:** GeoTIFF, Shapefile (.zip, or .shp + .shx + .dbf + .prj; projected CRSs are reprojected), GeoJSON, KML/KMZ. Use **+ Add data**, File ▸ Add data, or drag them onto the map.
  - GeoTIFFs already in the workspace (**Workspace** button)
  - **finished downloads**, which are added automatically
  - your area of interest, scene footprints, previews and index results

  Tick a layer to show or hide it and drag to reorder. Use **🔍 / double-click / right-click ▸ Zoom to layer** to zoom to it. Right-click (or ⋯) also has Properties, **Export / save to computer**, Use as area of interest, Compute indices, Move, and Remove. Select a raster and click the map to read its pixel values. The layer list and map view are restored when you reopen the page.
- **Area picker (every tool):** Index analysis and Export can be limited to an area: the whole layer, the Find-imagery area of interest, any polygon layer in Contents, the current map view, or a rectangle / polygon you draw (which is added to Contents as a "Clip area" layer).
  - Only that window of the image is read, so area-limited results are computed at full resolution and much faster.
  - Stats and histograms describe only that area.
  - Exports are cropped to it, with pixels outside the polygon set to no-data. Vector exports keep only the features inside it.
- **Export any layer** (Tools ▸ Export data, or right-click ▸ Export / save to computer):
  - **Rasters:** **GeoTIFF** (index values / band / composite, original projection), **PNG** (as displayed), **PNG + world file** (.pgw + .prj, georeferenced), or **Shapefile**. For a shapefile, the values are grouped into classes (equal intervals, quantiles or your own breaks), small patches are merged, and the result is converted to polygons with class, range and area attributes. Class maps such as WorldCover keep their class names.
  - **Vectors:** **Shapefile**, **GeoJSON** or **KML**.
- **Progress and Cancel:** whatever a tool is running (search, preview, download, composite, index, PCA, export, Sentinel-1 processing) shows a bar at the bottom of the tool panel. It gives the % complete, the current step, elapsed time and an estimate of the time left, plus a **Cancel** button. Cancelled jobs stop at the next checkpoint and their partial files are deleted, so you can fix a parameter and run again. Downloads & jobs shows the same progress and Cancel for every job.
- **Status bar (bottom):** the cursor's lat/long (click it to switch decimal degrees / DMS), a **map scale box** (pick 1:500 … 1:10,000,000 or type any scale and press Enter), the zoom level, and running downloads.

### Find imagery
1. **Area of interest.** Draw it, type lat/long (point + radius or bounding box), search an address (OpenStreetMap), or upload a shapefile / GeoJSON / KML. Any polygon layer can also become the area (right-click ▸ Use as area of interest).
2. **Satellite, dates and filters:**
   - **Satellite:** Sentinel-2 L2A (10 m) or **Landsat 8–9 Collection 2 L2** (30 m).
   - **Source:** Earth Search, Planetary Computer or Copernicus for Sentinel-2; the USGS Landsat copy on Planetary Computer for Landsat.
   - Date range and maximum cloud %.

   Landsat bands are stored under their Sentinel-2-equivalent names (B02 blue, B03 green, B04 red, B08 NIR, B11/B12 SWIR), so every index works. Clouds are masked with the QA_PIXEL flags.
3. **Results:**
   - One card per acquisition date shows a thumbnail, the tile cloud %, and how much of your area it covers. Footprints become a layer.
   - **Preview area** adds the real imagery of your area, plus a cloud/shadow mask, as layers.
   - **Metadata** shows the scene's full STAC metadata.
   - **Download** offers that date, a cloud-free composite, ESA WorldCover / Esri land-cover labels, or the original product: a Copernicus .SAFE for Sentinel-2, or a USGS EarthExplorer bundle for Landsat. Progress shows at the bottom of the tool, and the result is added to Contents when it's done.

### Your own Sentinel products (.SAFE)
Copy Copernicus products (extracted `.SAFE` folders or `.SAFE.zip` files) into the `data/` folder. They then appear under **File ▸ Open Sentinel product (.SAFE)** and on **Help ▸ Getting started**:

- **Sentinel-2 L1C / L2A:** opens instantly as one layer with all 12 bands at 10 m. It's a VRT that reads the original JPEG2000 files, so nothing is copied. Band names and the reflectance scale/offset (incl. the −0.1 offset of processing baseline ≥ 04.00) are taken from the product metadata, so every optical index works right away.
- **Sentinel-1 GRD (IW/EW, dual-pol):** converted in the background to a GeoTIFF with VV, VH and VV−VH. The steps are radiometric calibration to σ⁰ using the product's calibration LUT, multilooking (power averaged over n×n pixels), dB conversion, and geocoding from the product's ground control points to UTM at 20/40/80 m, optionally clipped to your area of interest. There is no terrain correction, so expect geometric shifts in steep mountains.

  Radar layers open as a **Radar RGB** (R = VV, G = VH, B = VV/VH). The **Radar (SAR)** index group adds **RVI** (radar vegetation index), **CPR** (VH − VV), **NDPI**, and VV / VH in dB.

Example with the sample products in `data/`:

| Product | Opened as | Time |
|---|---|---|
| `S2C_MSIL2A_20261001…_T33TUG` (central Italy, 1.2 GB) | 10980 × 10980 px, 12 bands | about 9 s to open, 4–5 s per map render |
| `S1C_IW_GRDH_1SDV_20261001…` (Austria, 1.8 GB) | σ⁰ VV/VH at 40 m, 7314 × 5309 px | about 12 s |

### Index analysis
Pick an input raster layer, then click an index. **Each result is a new layer**, which you can style, identify and export.

| Category | Indices |
|---|---|
| Vegetation | NDVI, SAVI, MSAVI, OSAVI, EVI, EVI2, GNDVI, NDRE, CIre, ARVI |
| RGB only | VARI, GLI, ExG |
| Water / moisture | NDWI, MNDWI, AWEI, NDMI, NDCI |
| Built-up / soil | NDBI, UI, BSI, NDTI |
| Fire | NBR, NBR2 |
| Snow | NDSI |
| Radar (Sentinel-1) | RVI, CPR (VH − VV), NDPI, VV dB, VH dB |

- **Area to analyse:** the whole image or any area from the area picker. Results then cover only that area, at full resolution.
- **Band combinations** (true colour, false colour, SWIR, agriculture, urban, radar RGB) change how the input layer is displayed.
- **Custom formula:** e.g. `(NIR - SWIR1) / (NIR + SWIR1)`. You can use band names (`B08` or `NIR`), numbers, `+ - * / ^` and `sqrt abs log exp min max`. Formulas are parsed and checked, never executed as code.
- **Index setup panel:** shows the formula with band numbers, in plain words, and as it will run on your file (`Band 4 − Band 1`). Each needed band has a dropdown to pick the matching band from your file.
  - **Warnings** flag likely mistakes: a guessed band order, NIR darker than Red, swapped SWIR bands, the same band used twice, and values that don't look like reflectance.
  - **Suggestions** list indices that work with the bands your image has.
- **Automatic detection:** band mapping is detected from band names (Sentinel-2, Landsat `SR_B*`, NAIP) or the band count. Pixel values are converted to reflectance (S2 ×10000, Landsat C2, 8-bit). Both can be changed in the panel.

### PCA & dimensionality reduction
Turns many correlated bands into a few new ones (components) that hold most of the information. It's useful for visual interpretation, change detection and as classifier input.

1. **Input image and area:** any raster layer. Optionally use the area picker; small areas run at full resolution in seconds.
2. **Bands:** *Recommended* leaves out atmospheric bands (B01, B09) and derived bands such as indices or VV−VH.
3. **Method** (scikit-learn), with **PCA as the recommended default**:

| Method | Use it for |
|---|---|
| **PCA** (default) | Removing band redundancy. PC1 ≈ brightness, PC2 ≈ vegetation vs. soil, PC3 ≈ moisture. |
| Incremental PCA | The same result, but learns from *every* pixel in batches. For huge images. |
| Kernel PCA | Non-linear patterns (RBF / poly / sigmoid / cosine kernel). Slow, so best on a selected area. |
| NMF | Additive, non-negative parts, similar to spectral unmixing (vegetation / soil / water). |
| FastICA | Statistically independent signals: can isolate haze, shadow or a single cover type. |
| Truncated SVD | PCA without centring: keeps absolute brightness. |
| Factor Analysis | Shared factors plus per-band noise. Varimax makes factors easy to read. |

4. **Parameters:** the defaults suit satellite imagery (3 components, standardized bands, automatic resolution, a 100,000-pixel fitting sample). Method-specific and rarely needed options are under *Advanced*, and **Reset to defaults** restores everything. Every parameter has an **ⓘ hint**: hover for a moment or click it.

The result is a layer with one band per component, shown as an RGB of components 1–3. Properties can switch to other combinations. The report shows explained variance (bar chart and running total) and a band-loadings heatmap. GeoTIFF export contains all components.

How it scales: the model is fitted on a random pixel sample (Incremental PCA uses every pixel) and applied strip by strip, so memory stays bounded. On the sample Sentinel-2 tile, PCA takes about 4 s for a 550 km² area at 10 m and about 36 s for the whole tile.

### Credentials and security
**Credentials** stores, in the OS keychain (macOS Keychain on a Mac):
- your Copernicus account and Copernicus S3 keys
- your **USGS EarthExplorer** username and **M2M application token**, which USGS requires instead of a password. Get one at ers.cr.usgs.gov ▸ Access Request ▸ M2M API, then create an Application Token.
- an optional Planetary Computer key

Each entry has a **Test** button. Secrets are never sent back to the browser, and they only go to the provider they belong to.

The server listens on `127.0.0.1` only and rejects requests from other websites. It is a personal, single-user tool. Hosting it for several people would need user accounts and per-user credential storage first.

### Adding a tool
1. Add a panel to `webapp/static/index.html`: `<section id="tab-mytool" class="tabpanel hidden">…</section>`.
2. Add an entry to the `TOOLS` list in `webapp/static/app.js`: `{ id: "mytool", title, icon, subtitle }`. The Tools-menu item and the start-page card are generated from it.
3. Put server endpoints in `webapp/server.py` and processing code in `lulc_fetch/`. Add results to Contents with `addRasterFromPath(path)` or `addVectorLayer(geojson, name)`, so they get layer styling and export for free.

### Project layout

```
lulc_fetch/          core library (also used by the CLI)
  sentinel2.py       STAC search, cloud masking, scenes, median composites
  sources.py         Earth Search / Planetary Computer / Copernicus catalogs
  pipeline.py        scene / composite / label exports
  pca.py             PCA / Kernel PCA / NMF / ICA / SVD / FA (scikit-learn) with parameter schema
  indices.py         index catalog + safe formula evaluator
  analysis.py        rendering, band detection, clipping, GeoTIFF / PNG / shapefile export
  safe.py            Sentinel-2 .SAFE → VRT, Sentinel-1 GRD → calibrated σ⁰ GeoTIFF
  usgs.py            USGS EarthExplorer M2M login and Landsat bundle download
  progress.py        progress reporting + cooperative cancellation for long tasks
  vector_io.py       shapefile / KML / GeoJSON writers
  extras.py, cdse.py WorldCover / Esri labels, other collections, Copernicus product download
webapp/
  server.py          FastAPI backend (localhost only)
  credentials.py     OS-keychain credential storage
  jobs.py, aoi_io.py background jobs; shapefile / KML / geocoding input
  static/            the web page (index.html, app.js, style.css)
```

Working folders, all git-ignored: `data/` (your .SAFE products), `imports/` (VRTs), `downloads/` (job output), `uploads/`, `analysis/`, `exports/`.

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

- One download request is capped at 60 M pixels (≈77×77 km at 10 m). Tile larger regions or use `--res 20`.
- Composites keep about `n_scenes × H × W × 4` bytes per band group in memory, budgeted at about 1.5 GB.
- Map previews of large rasters are drawn at reduced resolution (≈1400 px). Their stats are computed on that preview unless you pick an area. GeoTIFF exports are always full resolution.
- Raster → shapefile works on at most 2000 px on the long side (pick an area for full detail). PNG export is capped at 8192 px.
- Sentinel-1 backscatter is geocoded on the ellipsoid without terrain correction, and thermal noise is not removed.
- PCA on very large images is processed at reduced resolution (Auto keeps it under 25 M pixels; Kernel PCA under 2 M). Pick an area for full detail.
- Landsat's official cloud mask (CFMask) sometimes flags bright city roofs as cloud. You'll see it as yellow speckle in previews.
- Cancelling takes effect at the next checkpoint, usually within a second or two. A single long step, such as fitting Kernel PCA, finishes before it stops.
- The EarthExplorer bundle download follows the USGS M2M API but needs an account with M2M access. It has not been tested here without such an account.
- The web app is a single-user tool for your own computer. It is not designed to be hosted for others.

## License

Apache License 2.0. See [LICENSE](LICENSE).
