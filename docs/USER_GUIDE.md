# LULC Fetch: User Guide

This guide explains every part of LULC Fetch, the land-use / land-cover (LULC) toolkit, in the order you would normally use it: get imagery → prepare it → collect training data → train a model → make and export a map.

**Contents**

1. [Installing and starting](#1-installing-and-starting)
2. [The workspace](#2-the-workspace)
3. [Layers (Contents panel)](#3-layers-contents-panel)
4. [Things every tool shares](#4-things-every-tool-shares)
5. [Find imagery](#5-find-imagery)
6. [Your own Sentinel products (.SAFE)](#6-your-own-sentinel-products-safe)
7. [Index analysis](#7-index-analysis)
8. [PCA & dimensionality reduction](#8-pca--dimensionality-reduction)
9. [Training samples](#9-training-samples)
10. [Stack layers](#10-stack-layers)
11. [Raster → table](#11-raster--table)
12. [Classical ML: Train a model](#12-classical-ml-train-a-model)
13. [Classical ML: Classify an image](#13-classical-ml-classify-an-image)
14. [Export data](#14-export-data)
15. [Downloads & jobs](#15-downloads--jobs)
16. [Credentials](#16-credentials)
17. [Command-line tool](#17-command-line-tool)
18. [Data sources and band conventions](#18-data-sources-and-band-conventions)
19. [Files and folders](#19-files-and-folders)
20. [Limits and known issues](#20-limits-and-known-issues)
21. [Troubleshooting](#21-troubleshooting)
22. [For developers: adding a tool](#22-for-developers-adding-a-tool)

---

## 1. Installing and starting

Requirements: Python 3.10 or newer. On macOS, XGBoost and LightGBM also need the OpenMP runtime: `brew install libomp`.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[web]"     # web app + command-line tool
.venv/bin/lulc-fetch-web              # opens http://127.0.0.1:8000 in your browser
```

- Use `pip install -e .` if you only want the command-line tool.
- `lulc-fetch-web --port 8080` runs on another port. `--no-browser` stops it from opening a browser tab.
- Stop the server with **Ctrl+C** in the terminal.

The app runs entirely on your computer. Searching and downloading uses free public catalogues with no login. Accounts are only needed for Copernicus and USGS original-product downloads (see [Credentials](#16-credentials)).

---

## 2. The workspace

The window is laid out like a desktop GIS (QGIS / ArcGIS):

```
┌ File  Tools ▾  View  Help ─────────────────────────── Credentials ┐
│ Contents        │                                │ Tool panel     │
│ ☑ NDVI · scene  │              map               │ (opens when    │
│ ☑ AOI           │                                │  you choose a  │
│ ☑ S2 scene      │                                │  tool)         │
├─────────────────┴────────────────────────────────┴────────────────┤
│ Ready          Lat 12.96501  Lon 77.58500   Scale 1 : 25,000  z 14 │
└────────────────────────────────────────────────────────────────────┘
```

### Menu bar
| Menu | What's in it |
|---|---|
| **File** | Add data from computer · Add GeoTIFF from workspace · Open Sentinel product (.SAFE) · Export / Properties / Remove the selected layer · Remove all layers · Credentials |
| **Tools ▾** | Find imagery · Index analysis · PCA & dimensionality reduction · Training samples · Stack layers · Raster → table · Classical ML (↳ Train a model, ↳ Classify an image) · Export data · Downloads & jobs |
| **View** | Show/hide Contents and Tool panel · Basemap (Streets, Satellite, Topographic, None) · Place labels on top · Zoom to all layers · Theme (system / light / dark) |
| **Help** | Getting started (start page, incl. your Sentinel products) · Quick guide · Keyboard shortcuts |

The **tool panel** on the right only opens when you choose a tool; its **×** closes it again.

### Status bar
- **Lat / Lon** of the cursor. Click it to switch between decimal degrees and degrees-minutes-seconds.
- **Scale box:** pick a scale (1:500 … 1:10,000,000) or type any number, e.g. `25000`, and press Enter.
- **Zoom level** and the display projection (EPSG:3857). Exports always keep each file's own projection.
- **Running jobs**, with their average progress. Click to open Downloads & jobs.

### Keyboard shortcuts
| Keys | Action |
|---|---|
| Ctrl/⌘ O | Add data from computer |
| Ctrl/⌘ E | Export the selected layer |
| Delete | Remove the selected layer |
| Ctrl/⌘ 1 / 2 | Show / hide Contents / Tool panel |
| Esc | Close menus, stop drawing, close hints |

The app remembers your layers, map position, basemap, theme and panel layout between sessions (stored in your browser).

---

## 3. Layers (Contents panel)

Everything you work with is a layer in **Contents**, on the left.

### Adding layers
- **+ Add data**, File ▸ Add data, or **drag files onto the map**. Supported formats:
  - GeoTIFF
  - Shapefile (`.zip`, or `.shp` + `.shx` + `.dbf` + `.prj` selected together; other projections are converted)
  - GeoJSON, KML / KMZ
- **Workspace:** GeoTIFFs already on your computer from earlier downloads, results, imports and command-line output.
- Tools add their results automatically: downloads, index results, PCA, stacks, classified maps, your area of interest, scene footprints and previews.

### Working with layers
| Action | How |
|---|---|
| Show / hide | Tick box |
| Reorder | Drag a layer up or down (top = drawn on top) |
| Zoom to layer | 🔍 button, double-click, or right-click ▸ Zoom to layer |
| Legend + opacity | ▶ arrow on the left of the layer |
| Pixel values | Select a raster layer, then click the map. A popup shows the value, class name or band values. |
| More options | Right-click or **⋯**: Zoom · Properties · Compute indices · Use as area of interest (polygon layers) · Export / save to computer · Move to top/bottom · Remove |

### Layer properties
Right-click ▸ **Properties** lets you rename a layer, change its opacity, and choose how a raster is **displayed**:
- a **band combination** (true colour, false colour, SWIR, agriculture, urban, radar RGB)
- an **RGB of any three bands** (e.g. PC2 / PC3 / PC1)
- a **single band** with a colour scale and stretch (standard range, automatic 2–98 %, or custom min/max)

Class maps (e.g. WorldCover, classified maps) are drawn with their own class colours and names.

---

## 4. Things every tool shares

### ⓘ hints
Every option has a small **ⓘ** icon. Hover over it for half a second to see a plain-language explanation, or click it to keep the hint open.

### Area picker
Most tools can be limited to part of an image. The **Area** option offers:
- the whole layer
- the area of interest from Find imagery
- any polygon layer in Contents (e.g. an uploaded shapefile)
- the current map view
- **draw a rectangle / polygon**, which is added to Contents as a red dashed "Clip area"

Only that window of the image is read, so area-limited work runs **faster and at full resolution**. Statistics then describe only that area, and exports are cropped to it.

### Progress and Cancel
Anything that takes time (searching, previews, downloads, composites, indices, PCA, tables, training, classification, stacking, exports) shows a bar at the bottom of the tool panel. It gives the **% complete**, the current step, elapsed time and an estimate of the time left, plus a **Cancel** button. Cancelling stops the work at the next checkpoint (usually within a second or two) and deletes partial files, so you can change a setting and run again.

---

## 5. Find imagery

**Tools ▸ Find imagery** searches satellite archives and downloads imagery for your area.

### Step 1: Area of interest
Choose one way to define it:
- **Draw** a rectangle or polygon.
- **Lat / Long:** a point plus radius, or a bounding box. "Pick on map" fills in the point.
- **Address:** search a place (OpenStreetMap). Use its boundary, its bounding box, or a radius around it.
- **Upload** a shapefile, GeoJSON, KML or KMZ.

Any polygon layer can also become the area: right-click it ▸ *Use as area of interest*. The area appears in Contents as *Area of interest*, and removing that layer clears it.

### Step 2: Satellite, dates and filters
- **Satellite:**
  - **Sentinel-2 L2A** (10 m, every ~5 days)
  - **Landsat 8–9 Collection 2 L2** (30 m)
- **Source** (Sentinel-2): Earth Search (AWS), Planetary Computer, or Copernicus Data Space (needs S3 keys). Landsat uses the USGS copy on Planetary Computer; no login is needed.
- **Dates:** start and end, or a preset (30 days, 3 months, 1 year).
- **Max cloud cover** (% of the whole scene).

### Step 3: Results
One card per acquisition date shows:
- a thumbnail, the satellite and tile, the scene's cloud %, and how much of your area it covers
- **Preview area:** adds the real imagery of your area, plus a cloud / shadow mask, as layers, and reports *% clear inside your area*
- **Metadata:** the full STAC record (satellite, sun angles, processing baseline, assets, raw JSON)
- **Download**

Sort the cards by cloud, date or coverage. Hovering a card highlights its footprint on the map.

### Download options
| Option | What you get |
|---|---|
| **Single date** | That date's bands clipped to your area, clouds and shadows masked, with optional indices |
| **Cloud-free composite** | Per-pixel median of all clear observations in the date range. The best input for land-cover mapping, especially in cloudy seasons. |
| **LULC reference map** | ESA WorldCover (2020/2021) or Esri annual land cover (2017–2023) on the same grid, for training or validation |
| **Original full product** | The complete Copernicus `.SAFE` (Sentinel-2) or USGS EarthExplorer bundle (Landsat). Needs credentials. |

You can choose the bands, pixel size and indices. An estimate of the image size is shown. The result is added to Contents when it finishes.

---

## 6. Your own Sentinel products (.SAFE)

Copy Copernicus products into the **`data/`** folder, either extracted `.SAFE` folders or `.SAFE.zip` files. They appear under **File ▸ Open Sentinel product (.SAFE)** and on **Help ▸ Getting started**.

- **Sentinel-2 L1C / L2A:** opens instantly as one layer with all 12 bands at 10 m. It's a lightweight VRT that reads the original JPEG2000 files, so nothing is copied. The band names and the reflectance scale/offset (incl. the −0.1 offset of processing baseline ≥ 04.00) come from the product, so all indices work immediately.
- **Sentinel-1 GRD** (IW/EW, dual polarisation): converted in the background into a GeoTIFF with **VV, VH and VV−VH** in dB:
  - radiometric calibration to σ⁰ using the product's calibration table
  - speckle reduction by averaging (multilooking)
  - geocoding to UTM from the product's ground control points, at 20, 40 or 80 m, optionally only for your area of interest

  It opens as a *Radar RGB* (R = VV, G = VH, B = VV/VH). There is no terrain correction, so expect small shifts in steep mountains.

---

## 7. Index analysis

**Tools ▸ Index analysis** computes spectral and radar indices. Each result is a **new layer** you can style, identify, export or use for training.

1. **Input image:** any raster layer. Optionally choose an **area to analyse**.
2. **Click an index:**

| Group | Indices |
|---|---|
| Vegetation | NDVI, SAVI, MSAVI, OSAVI, EVI, EVI2, GNDVI, NDRE, CIre, ARVI |
| RGB only | VARI, GLI, ExG (for drone / phone / RGB imagery) |
| Water / moisture | NDWI, MNDWI, AWEI, NDMI, NDCI |
| Built-up / soil | NDBI, UI, BSI, NDTI |
| Fire | NBR, NBR2 |
| Snow | NDSI |
| Radar (Sentinel-1) | RVI, CPR (VH − VV), NDPI, VV dB, VH dB |

3. **Band combinations** (true colour, false colour, SWIR, agriculture, urban, radar RGB) change how the input image itself is displayed.
4. **Custom formula:** e.g. `(NIR - SWIR1) / (NIR + SWIR1)`.
   - You can use band names (`B08` or `NIR`, `RED`, `SWIR1`…), numbers, `+ − × ÷ ^`, brackets, and `sqrt abs log exp min max`.
   - The formula is checked as you type, and every built-in formula is available as an editable template.
   - Formulas are parsed safely, never executed as code.

### Index setup panel
When you click an index, the panel shows the formula three ways: with band names (`B08`), in words (`NIR − Red`), and as it will run on your file (`Band 4 − Band 1`).
- **Band dropdowns:** each band the formula needs has a dropdown to pick the right band from your file. The band's typical value is shown as a hint.
- **Warnings:** you're warned about likely mistakes:
  - a band order that was only guessed
  - NIR darker than Red (probably swapped)
  - swapped SWIR bands
  - the same band used twice
  - values that don't look like reflectance
- **Suggestions:** if an index needs a band your image doesn't have, indices that do work are suggested.

### Pixel values and scaling
Band names are detected automatically (Sentinel-2, Landsat `SR_B*`, NAIP, radar VV/VH), or guessed from the band count. Values are converted to reflectance automatically: Sentinel-2 ÷10000 (with the baseline offset), Landsat Collection 2, or 8-bit. Both can be changed under *Bands* and *Pixel values* in step 1.

### Result
- a colour scale (standard range for the index, automatic, or custom), a legend and a histogram
- statistics: mean, median, standard deviation, min, max
- **Export layer…**, or **Export several…** for many indices in one multi-band GeoTIFF

---

## 8. PCA & dimensionality reduction

**Tools ▸ PCA & dimensionality reduction** turns many correlated bands into a few new bands (components) that hold most of the information. It's useful for visual interpretation, change detection and as model input.

1. **Input image** and optional **area**.
2. **Bands:** *Recommended* leaves out atmospheric bands (B01 coastal, B09 water vapour) and derived bands such as indices.
3. **Method:**

| Method | Use it for |
|---|---|
| **PCA** *(recommended default)* | Removing band redundancy. PC1 ≈ brightness, PC2 ≈ vegetation vs. soil, PC3 ≈ moisture / water. |
| Incremental PCA | The same result, learned from *every* pixel in batches. For very large images. |
| Kernel PCA | Non-linear patterns (RBF, polynomial, sigmoid, cosine). Slow, so best on a small area. |
| NMF | Additive, non-negative parts, similar to spectral unmixing (vegetation / soil / water) |
| FastICA | Statistically independent signals: can isolate haze, shadows or one cover type |
| Truncated SVD | PCA without removing the mean, so brightness is kept |
| Factor Analysis | Shared factors plus per-band noise. Varimax makes factors easy to read. |

4. **Parameters:**
   - **Defaults:** 3 components, standardized bands, automatic resolution, a 100,000-pixel fitting sample.
   - **Method-specific options** appear when you choose a method. Rarely needed options are under *Advanced*.
   - **Reset to defaults** restores everything.

**Result:** a layer with one band per component, displayed as an RGB of components 1–3 (Properties can show other combinations), plus a report:
- explained variance per component, with a running total
- a band-loadings heatmap showing which bands drive each component

---

## 9. Training samples

**Tools ▸ Training samples** lets you draw your own ground truth on the map.

1. **Sample set:** click **+ New**. A sample set is a vector layer in Contents.
2. **Classes:** load a preset or add your own. Presets:
   - *Land cover* (6 classes)
   - *ESA WorldCover* (11)
   - *Water / non-water*

   Each class has a colour (click the swatch to change it) and a name (click to rename). The radio button selects the class you're drawing.
3. **Draw:** choose **Polygon**, **Rectangle** or **Point**, and draw over areas you're sure about.
   - Switch to the satellite basemap or show an image layer as reference.
   - *Keep drawing* starts the next shape automatically; press **Esc** to stop.
   - **Undo last** removes the last shape.
4. **Your samples:** the number of samples and the area per class. You're advised when a class has fewer than 5 samples.
5. **Next:** **Use these samples in Raster → table** sets up the table tool in one click. You can also export the samples as a shapefile.

**Tips for good samples**
- Draw **many small polygons spread across the map** rather than a few big ones. This gives the model variety and allows an honest accuracy check (see [validation split](#validation-split-honest-accuracy)).
- Aim for at least **5–10 samples per class**, more for classes that vary a lot (e.g. built-up).
- Only label pixels you're confident about; avoid mixed edges.

Your class colours carry through to the final classified map.

---

## 10. Stack layers

**Tools ▸ Stack layers** combines bands from several layers into **one multiband image** on a common grid. For example: Sentinel-2 bands + Sentinel-1 VV/VH + elevation + an NDVI result. Combining sources usually improves land-cover accuracy.

1. **Layers to stack:** tick layers and untick bands you don't need. Index layers (from Index analysis) become one computed band each.
2. **Grid:**
   - **Reference layer:** all layers are resampled onto its projection, pixel size and extent.
   - **Area:** optional.
   - **Pixel size:** native or coarser.

   Class maps use nearest-neighbour resampling, so class codes stay intact; continuous data uses bilinear.
3. **Name** and **Stack layers**.

Band names are kept (B04, VV, NDVI…), so the stack still opens in true colour and works with indices. **Use in Raster → table →** continues the workflow.

---

## 11. Raster → table

**Tools ▸ Raster → table** converts an image into a table (one row per pixel, one column per band), ready for machine learning.

1. **Image (required):** any raster: GeoTIFF, Sentinel-2, Landsat, multispectral, **hyperspectral** (any number of bands), SAR or a stack.
   - Choose the bands that become columns.
   - *Convert to reflectance* applies the layer's scale/offset.
2. **Area and pixel size:** the whole image or an area. Rows follow the image's **own resolution** (one row per pixel), or blocks of 2×–16× pixels can be averaged into one row.
3. **Ground truth (optional), added as the last column:**
   - **Raster**, e.g. a land-cover map: matched pixel by pixel (nearest neighbour).
   - **Vector**, e.g. your training samples, a shapefile, GeoJSON or KML (polygons or points). Choose the attribute holding the class or value.
     - Text classes get a numeric `<label>_code` column plus the name column.
     - A **`poly_id`** column records which polygon each pixel came from (used for honest validation).
   - *Keep only pixels that have a label* (on by default) gives clean training data.
4. **Rows and output:**
   - **Rows:**
     - *All pixels*
     - *Random sample* of N rows
     - *Stratified by class* (up to N per class, for balanced training data)
   - **Columns:** optional map x/y, lon/lat and row/col. *Skip no-data pixels* is on by default.
   - **Format:** CSV (opens anywhere) or Parquet (smaller and faster for big tables).
   - A live **estimate** shows the rows, columns and file size before you run.

**Result:** rows per class, the first rows of the table, and a download button. Tables are saved in `tables/` and listed under **Classical ML ▸ Your tables**.

---

## 12. Classical ML: Train a model

**Tools ▸ Classical ML ▸ Train a model.** The Classical ML hub also lists **your models** and **your tables**.

1. **Training table:** any table from Raster → table.
2. **Label and features:**
   - **Label column:** preselected (the ground truth). The **task** is detected automatically: *classification* for classes, *regression* for continuous values such as biomass. You can override it.
   - **Class distribution:** shown, with a suggestion to use class balancing if the classes are very unequal.
   - **Features:** the bands and indices are preselected. Coordinates and `poly_id` are left out, so the model learns spectra, not locations.
3. **Model:** cards with typical accuracy / speed ratings, filterable by family.

| Family | Models |
|---|---|
| Trees | **Random Forest** *(recommended)*, Extra Trees, Decision Tree |
| Boosting | XGBoost, LightGBM, Histogram Gradient Boosting |
| Kernel | SVM (RBF kernel) |
| Linear | SGD (log-loss or linear SVM), Logistic Regression |
| Probabilistic | Naive Bayes, **Maximum Likelihood** (classic remote-sensing Gaussian classifier), Linear Discriminant |
| Neighbours / Neural | k-Nearest Neighbours, neural network (MLP) |

4. **Parameters:** 2–4 key settings per model, with defaults tuned for pixel data. The rest is under *Advanced*:
   - validation split, test share, class balancing
   - cross-validation folds, feature scaling (automatic for models that need it)
   - training-row cap (large tables are sampled down per model), random seed

### Validation split (honest accuracy)
Neighbouring pixels look almost identical. If test pixels are picked at random, they're near-copies of training pixels and the accuracy is **overstated**. The *Validation split* setting fixes this:

| Split | How it works | Needs |
|---|---|---|
| **By polygon** | Each training polygon goes entirely to train or to test | a `poly_id` column (vector ground truth) |
| **Spatial blocks** | The map is cut into squares; whole squares go to test | x / y columns |
| **Random pixels** | Pixels picked at random. Marked as *optimistic* in the results. | — |

*Auto* (default) picks the most honest option the table allows. Results show a badge such as *✓ Independent test: by polygon (93 of 300 polygons held out)*. You're warned if there are too few polygons, or if a class has no test pixels.

Example from testing: the same Random Forest scored 79.5 % with a random split but 53.9 % by polygon. **Report the honest number.**

### Results
- **Classification:**
  - tiles for **overall accuracy, Kappa, macro F1 and balanced accuracy**
  - a **confusion matrix** with producer's (PA) and user's (UA) accuracy
  - per-class precision, recall and F1
  - **feature importance**
  - optional cross-validation (grouped the same way as the split)
- **Regression:** R², RMSE, MAE and a predicted-vs-true plot.

Random Forest, Extra Trees, XGBoost and LightGBM report real progress while training, so **Cancel** works mid-training. Models are saved in `models/` as `.joblib` files (a scikit-learn pipeline), with a JSON report. In the hub you can **Use**, view the **Report**, download or delete each model.

---

## 13. Classical ML: Classify an image

**Tools ▸ Classical ML ▸ Classify an image**, or **Use** next to a model in the hub.

1. **Model:** pick a trained model. Its accuracy is shown.
2. **Image:** the image to classify: the training image, or another one with the same bands (another date or area).
   - **Bands** are matched to the model's inputs automatically (✓). Check the matches if the band order differs.
   - The same value conversion as the training table (e.g. reflectance) is applied automatically.
3. **Area and output:**
   - **Area:** optional.
   - **Pixel size:** *Auto* keeps native resolution unless the image has more than 25 M pixels.
   - **Map name.**
   - **Confidence band:** optional.

**Result:** a GeoTIFF layer in Contents:
- **band 1 = class**, with colours and names (your sample colours, or WorldCover colours for WorldCover codes)
- **band 2 = confidence %**, when the model gives probabilities

The tool also reports the **area per class** (km² and %).

---

## 14. Export data

**Tools ▸ Export data**, or right-click a layer ▸ **Export / save to computer**.

1. **Layer.**
2. **Area:** the whole layer, the layer's own analysis area, or any area from the area picker.
3. **Format:**

| Layer type | Formats |
|---|---|
| Raster | **GeoTIFF** (index values / band / composite / all bands, original projection) · **PNG** (as displayed) · **PNG + world file** (.zip, georeferenced for GIS) · **Shapefile** (raster values grouped into classes and converted to polygons) |
| Vector | **Shapefile** (.zip) · **GeoJSON** · **KML** |
| Preview image | PNG |

**Raster → Shapefile** groups values into classes (equal intervals, quantiles or your own breaks), merges small patches, and writes polygons with class, value range and area attributes. Class maps (WorldCover, classified maps) keep their class names. It isn't available for colour composites; display a single band first.

Files are saved to your browser's Downloads folder.

---

## 15. Downloads & jobs

**Tools ▸ Downloads & jobs** lists background jobs (downloads, composites, product downloads, Sentinel-1 processing, …). Each job shows:
- its progress, current step and **Cancel**
- a preview image and land-cover class statistics, where relevant
- its output files, with **Add to map** for GeoTIFFs
- its log
- **Delete files**

Downloads that finish while the app is open are added to Contents automatically. Older ones can be added from **Workspace**.

---

## 16. Credentials

Click **Credentials** (top right). Secrets are stored in your **operating-system keychain** (macOS Keychain, Windows Credential Locker, Linux Secret Service). They're never shown again or sent back to the browser, and are only used with the service they belong to. Each entry has a **Test** button.

| Entry | Needed for | Where to get it |
|---|---|---|
| Copernicus Data Space: account | Original Sentinel `.SAFE` product downloads | dataspace.copernicus.eu |
| Copernicus Data Space: S3 keys | The *Copernicus* source in Find imagery | eodata-s3keysmanager.dataspace.copernicus.eu |
| USGS EarthExplorer | Original Landsat bundle downloads | ers.cr.usgs.gov ▸ Access Request ▸ M2M API, then create an *Application Token* (USGS no longer accepts passwords for this) |
| Planetary Computer *(optional)* | Higher rate limits only | planetarycomputer.developer.azure-api.net |

**Security:** the server only accepts connections from your own computer (127.0.0.1) and rejects requests from other websites. It's a single-user tool; hosting it for others would need user accounts first.

---

## 17. Command-line tool

`lulc-fetch` does the downloading parts without the web app. An area can be given as `--bbox minlon,minlat,maxlon,maxlat`, `--geojson file.geojson`, `--point lon,lat --buffer-km 5`, or `--match existing.tif` (reuse a raster's exact grid).

```bash
lulc-fetch search    --bbox 77.56,12.94,77.61,12.99 --start 2026-01-01 --end 2026-03-31 --max-cloud 20
lulc-fetch scene     --bbox 77.56,12.94,77.61,12.99 --start 2026-01-01 --end 2026-03-31 --indices -o out/scene.tif
lulc-fetch composite --geojson aoi.geojson --start 2026-06-01 --end 2026-09-30 --indices -o out/composite.tif
lulc-fetch labels    --match out/composite.tif --product worldcover --year 2021 -o out/labels.tif
lulc-fetch fetch     --match out/composite.tif --collection sentinel-1-rtc --assets vv,vh \
                     --start 2026-07-01 --end 2026-07-31 -o out/s1.tif
lulc-fetch fetch     --point -122.335,47.608 --buffer-km 1 --res 0.6 --collection naip --assets image -o out/naip.tif
lulc-fetch product   S2B_MSIL2A_20260211T050839_N0512_R019_T43PGQ_20260211T085923 --out-dir safe/   # needs CDSE_USERNAME / CDSE_PASSWORD
```

| Command | What it does |
|---|---|
| `search` | List acquisition dates with cloud cover |
| `scene` | The clearest single date, chosen by checking clouds inside your area |
| `composite` | A cloud-free median composite |
| `labels` | WorldCover / Esri land cover on the same grid |
| `fetch` | Any Planetary Computer collection (NAIP, Sentinel-1 RTC, Copernicus DEM, …) |
| `product` | A full Copernicus `.SAFE` product |

Useful options: `--bands all` or `--bands B02,B03,B04,B08`, `--res 20`, `--crs EPSG:4326`, `--source planetary-computer|cdse`, `--keep-clouds`, `--date YYYY-MM-DD`, `--max-scenes`, `--stat mean`.

Outputs are float32 GeoTIFFs with named bands (NaN = no data or cloud), plus a true-colour PNG quicklook. Label maps are uint8 with their official colour table.

---

## 18. Data sources and band conventions

| Source | Login | Notes |
|---|---|---|
| Earth Search (AWS), default | none | Sentinel-2 L2A Cloud-Optimized GeoTIFFs; fastest |
| Microsoft Planetary Computer | none | Sentinel-2, Landsat C2 L2, WorldCover, Esri LULC, NAIP, Sentinel-1 RTC, DEM |
| Copernicus Data Space | free S3 keys | The official ESA archive (JPEG2000) |
| USGS EarthExplorer | ERS account + M2M token | Original Landsat product bundles |

- **Reflectance offset:** values are made consistent across sources and dates. The −1000 DN offset of Sentinel-2 processing baseline ≥ 04.00 is applied per scene.
- **Landsat band names:** Landsat bands are stored under their Sentinel-2-equivalent names: B02 blue, B03 green, B04 red, B08 NIR, B11 / B12 SWIR. So every index and model works for both satellites. Landsat clouds are masked with the QA_PIXEL flags.
- **Highest free resolution:** Sentinel-2 at **10 m** is the highest-resolution free, global, frequently updated optical imagery. Finer free data exists only regionally, e.g. NAIP 0.6 m (USA) and national programmes such as Bhuvan (India).

---

## 19. Files and folders

| Folder | Contents |
|---|---|
| `data/` | Your Copernicus `.SAFE` products (input) |
| `imports/` | Lightweight VRTs for opened Sentinel-2 products |
| `downloads/` | Job outputs (downloads, maps, stacks…), one folder per job |
| `tables/` | Tables from Raster → table, each with a `.json` description |
| `models/` | Trained models (`.joblib`) with `.json` reports |
| `uploads/`, `analysis/`, `exports/` | Uploaded files, index exports, other exports |

All of these are excluded from git.

---

## 20. Limits and known issues

- **Download size:** one download is capped at 60 M pixels (≈77 × 77 km at 10 m). Use a coarser pixel size or split the area.
- **Map previews** of large rasters are drawn at reduced resolution (≈1400 px), and their statistics come from that preview unless you pick an area. **GeoTIFF exports are always full resolution.**
- **Reduced-resolution processing:**
  - Raster → shapefile works on at most 2000 px (pick an area for full detail).
  - PNG export is capped at 8192 px.
  - PCA and classification on very large images run at reduced resolution automatically (*Auto*).
- **Sentinel-1:** processed without terrain correction or thermal-noise removal.
- **Landsat clouds:** Landsat's official cloud mask sometimes marks bright city roofs as cloud (yellow speckle in previews).
- **Cancel** acts at the next checkpoint. A single long step (e.g. fitting Kernel PCA or an SVM) finishes first.
- **Stratified sampling** gives approximately N rows per class, not exactly N.
- **Saved models** (`.joblib`) may not load after a major scikit-learn / XGBoost / LightGBM upgrade. Retrain them if so.
- **Untested downloads:** the USGS EarthExplorer bundle download and the Copernicus S3 source follow the providers' APIs but need real credentials to verify.
- **Job history** is kept in memory: restarting the server clears the job list (files stay on disk).

---

## 21. Troubleshooting

| Problem | Fix |
|---|---|
| XGBoost / LightGBM shown as "Not installed" on macOS | `brew install libomp`, then restart the app |
| A tool says "Choose a raster layer" | Add data first: + Add data, Workspace, Find imagery or a `.SAFE` product |
| An index looks wrong (e.g. NDVI negative over forest) | Open the Index setup panel and check the band dropdowns and warnings, and *Pixel values* in step 1 |
| "Too few polygons" when training | Draw more, smaller training polygons spread over the map |
| A class has no test pixels | That class needs more polygons |
| The table or export is huge | Pick an area, a coarser pixel size, or a random / stratified sample |
| Copernicus / USGS download fails | Check the credentials with **Test**; USGS needs M2M access approved on your account |
| Nothing happens on the map after drawing | Press Esc and try again; make sure a class is selected (Training samples) |

---

## 22. For developers: adding a tool

The web app is a FastAPI backend (`webapp/server.py`) with a single-page frontend (`webapp/static/`). Processing code lives in the `lulc_fetch/` package.

1. Add a panel to `webapp/static/index.html`: `<section id="tab-mytool" class="tabpanel hidden">…</section>`.
2. Add an entry to `TOOLS` in `webapp/static/app.js`: `{ id: "mytool", title, icon, subtitle }`. The Tools-menu item and start-page card are generated from it.
3. Add server endpoints in `webapp/server.py` and processing code in `lulc_fetch/`. Long tasks should run as jobs (`jobs.submit(...)`) and call `lulc_fetch.progress.update(fraction, message)` at checkpoints, so the progress bar and Cancel work.
4. Add results to Contents with `addRasterFromPath(path)` or `addVectorLayer(geojson, name)`, so they get layer styling, identify and export for free. Track jobs in the UI with `trackJob(job, { tool })`.
5. **Classical ML sub-tools:** add `{ id, title, icon, subtitle }` to `ML_SUBTOOLS` in `app.js` and a `<div id="ml-sub-<id>" class="ml-sub hidden">` inside the ML panel. `/api/tables` and `/api/models` list the available tables and models.

| Module | Purpose |
|---|---|
| `sentinel2.py`, `sources.py`, `pipeline.py` | Search, cloud masking, scenes, composites; catalogue sources |
| `indices.py`, `analysis.py` | Index catalogue and formula evaluator; rendering, band detection, clipping, exports |
| `safe.py`, `usgs.py`, `cdse.py` | `.SAFE` readers; EarthExplorer and Copernicus downloads |
| `pca.py`, `ml.py` | PCA family; model catalogue, training, evaluation, classification |
| `tabular.py`, `stack.py` | Raster → table; layer stacking |
| `vector_io.py`, `progress.py`, `extras.py` | Vector writers; progress and cancellation; reference maps and other collections |
