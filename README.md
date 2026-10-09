# LULC Fetch

A land-use / land-cover (LULC) toolkit for free satellite data. It runs on your own computer as a **desktop-GIS-style web app** (layers, tools, map), with a **command-line tool** for downloads.

⬇️ **Download the latest version (0.0.3 beta):** [Mac (Apple Silicon)](https://github.com/agritechixr/lulc-fetch/releases/latest/download/LULC-Fetch-macOS-AppleSilicon.dmg) · [Windows](https://github.com/agritechixr/lulc-fetch/releases/latest/download/LULC-Fetch-Windows.zip): ready-to-run apps, no Python needed ([install steps](#download-mac-and-windows-no-installation-of-python-needed)).

🌐 **Website:** [agritechixr.github.io/lulc-fetch](https://agritechixr.github.io/lulc-fetch/) · **What's new:** [3D maps, the Assistant and the GIS tools](https://agritechixr.github.io/lulc-fetch/gis.html)

📖 **Full explanation of every tool: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)**

## What it can do

| Tool | What it does |
|---|---|
| **Find imagery** | Search and download **Sentinel-2** (10 m) and **Landsat 8–9** (30 m) for any area: single dates, cloud-free composites, ESA WorldCover / Esri land-cover maps, or original products |
| **Open Sentinel products** | Open your own Copernicus `.SAFE` files: Sentinel-2 with all bands, Sentinel-1 radar as calibrated VV/VH backscatter |
| **Index analysis** | 30 spectral and radar indices (NDVI, EVI, NDWI, NDBI, NBR, RVI…) or your own formula, with band-order checks |
| **PCA & dimensionality reduction** | PCA, Kernel PCA, NMF, ICA and more (scikit-learn) |
| **Training samples** | Draw labelled polygons and points for each class on the map |
| **Stack layers** | Combine Sentinel-2, Sentinel-1, elevation and index layers into one image |
| **Mosaic / merge rasters** | Join neighbouring tiles or scenes into one image. By default a **smooth blend** (each image fades out towards its edges) with **colours matched across overlaps**, so no seams or tile edges show; or first / last on top, median (removes clouds seen in one scene), mean, min, max. Class maps: first, last or most common class. Images in different coordinate systems are reprojected onto the first one's grid. |
| **Burn severity (dNBR)** | Two dates (Sentinel-2 or Landsat, or two NBR rasters): dNBR = NBR before − after, classed by the USGS severity scale (Key & Benson 2006) with the burned hectares per class. For **stubble burning**, only dark, charred land counts as burned, so fields that were just harvested aren't. |
| **Raster → table** | Turn any image into a CSV / Parquet table, with ground-truth labels as the last column |
| **Classical ML for raster** | Train straight from an image and its ground truth (polygons, points or a class raster) and get a classified map in one step. No table needed. SVM (RBF / linear), Maximum Likelihood, Spectral Angle Mapper, Minimum Distance, Random Forest, k-NN (cosine) and more, for **RGB, multispectral, hyperspectral (100+ bands) and pixel embeddings** (AlphaEarth, TESSERA). The kind of data is detected, with suggested settings. |
| **Make training data** | Cut a large image (one or several stacked layers) and its ground truth (class raster, shapefile or GeoJSON) into matching **image / label patches for deep learning**. Patch size and overlap in metres, ROI, edge padding, `classes.txt` and `dataset.json`. |
| **Train classify model** | Semantic segmentation on your Make-training-data patches: **U-Net, U-Net++, DeepLabV3 / V3+, PSPNet, FPN, LinkNet, SegFormer, FCN, LR-ASPP** with **MobileNetV2 / V3, ResNet or EfficientNet** backbones (ImageNet-pretrained, any number of bands), or **YOLO26 semantic** (ultralytics). Epochs, batch size, learning rate, early stopping, spatial-block validation split, losses for rare classes (Dice, focal, class weights), augmentation, live training curves, Cancel keeps the best model, resume. Writes the model plus an **HTML report** (curves, confusion matrix, per-class IoU / F1, example predictions). Runs on the Apple GPU, an NVIDIA GPU or the CPU. |
| **Classify image** | Map a whole image with a model from Train classify model: seamless tiled prediction with blended overlaps, area of interest, confidence layer. |
| **Detect object** | Find vehicles, ships, planes, storage tanks, people and more in high-resolution aerial, drone or satellite images. Pretrained, open source: **YOLO26** (COCO), **YOLO26 aerial** (DOTA, rotated boxes), YOLO26 outlines, torchvision **Faster R-CNN, RetinaNet, FCOS, SSD, Mask R-CNN**, **SAM 2.1** segment-everything, or **your own models**. Optional SAM outlines for any box model. Tiled with overlap, so objects cut by tile edges are joined back; zoom (automatic for your models); class, score and size filters. The result is a vector layer with class, score and size. |
| **Train detection model** | Train **YOLO26 / YOLO11** on an image and your labelled polygons or points: boxes, outlines or rotated boxes, pretrained start (COCO / DOTA), tiles with zoom, spatial-block validation, early stopping, live mAP curves, Cancel keeps the best model, **HTML report** (per-class AP, confusion matrix, PR curves, example predictions). |
| **Classical ML: supervised** | Train 16 models (Random Forest, XGBoost, LightGBM, SVM, Maximum Likelihood…) for classification or regression: choose the target and each column's role and type (numeric / categorical), preprocess (missing values, outlier clipping, skew transforms, scaling, removing redundant columns), tune hyperparameters with cross-validation, compare all models on a leaderboard, get honest spatially independent accuracy and an **HTML evaluation report** (confusion matrices, ROC / PR curves, residual plots…), then **classify an image** into a land-cover map |
| **Classical ML: unsupervised** | **Clustering** with K-means, hierarchical (dendrogram), DBSCAN, HDBSCAN, spectral clustering and Gaussian mixture: automatic choice of k, quality scores, cluster profiles, comparison with known labels, and **unsupervised classification of images**. **t-SNE maps** to see how classes or clusters separate. |
| **Export data** | Save any layer as GeoTIFF, PNG, Shapefile, **GeoPackage** (several vector layers in one .gpkg), GeoJSON or KML, for the whole layer or just an area. Add data also reads GeoPackages, every layer of them. |
| **Insert ▸ Online map layer** | Layers from web map services, drawn straight from the service: **Bhuvan** (ISRO's maps of India and LULC), **NASA GIBS** (daily satellite imagery, by date) or any **WMS, WMTS or XYZ** address. |
| **Insert ▸ Field collection** | A phone page that works offline (at [agritechixr.github.io/lulc-fetch/field](https://agritechixr.github.io/lulc-fetch/field/)): geotagged points and photos with the crop and a note, sent as one .zip. In the app the points become a layer with their photos, and leaf photos can go straight to Diagnose crop disease. |
| **Analysis ▸ Embeddings**: Download, Train, Classify, Convert and Explore embeddings | Free AI embeddings for any area, no account: **Google AlphaEarth** (64-D) and **TESSERA** (128-D), 10 m, 2017–2025. See which years exist for your area, download them as a GeoTIFF (only the needed parts are read), get a colour view (PCA), **find places similar** to the ones you click, then classify or cluster them. **Train embedding model** trains one of eleven light segmentation models (TinyUNet, ENet, DABNet, LEDNet…, 0.15–0.95 M parameters, all bands, 256 × 256 patches) on your labels and maps the layer; **Classify with embedding model** maps other areas or years. **Convert embeddings** turns 8-bit (AlphaEarth coding or scaled per band) into 16 / 32-bit float and back, showing how much the values change |
| **Insert ▸ Library**: ready-made GIS data | India's states, districts, sub-districts, villages, constituencies, city wards, highways, railways… (307 layers) and anything else uploaded to the library on Hugging Face: search and add to the map, downloaded once |
| **Interpolation**: IDW, kriging, spline, natural neighbour, nearest neighbour, trend surface, TIN | A surface from values at points (air-quality stations, rain gauges, soil samples), cut to any boundary, with a leave-one-out comparison of the methods and India's AQI colours |

**Analysis ▸ Agri** (from the Multi-Crop Disease Decision Support System):

| Tool | What it does |
|---|---|
| **Diagnose crop disease** | Leaf photos → the crop (two ConvNeXt crop detectors, 42 crops) → its disease (one ConvNeXt model per crop, top 3 with confidence; 91–100 % on test photos). Add single photos or a whole survey folder. Unclear or non-leaf photos get "retake" instead of a guess. A results table, and **geotagged photos become a disease map** (point layer). Needs the deep-learning add-on; the models ([Hugging Face](https://huggingface.co/ixrbhii/multicrop-disease-models)) download by themselves the first time, about 95 MB per crop. Each crop's model is also its own timm repository ([collection](https://huggingface.co/collections/ixrbhii/multi-crop-disease-models-42-crops-6ac0a3291f2153fa716e2993)). |
| **Crop disease guide** | About 9,000 expert questions and answers: symptoms, treatment, spray schedules and pests per crop and disease, searchable; offline. Diagnosis results link straight to their disease. Also a dataset: [ixrbhii/crop-disease-qa](https://huggingface.co/datasets/ixrbhii/crop-disease-qa). |

**Analysis ▸ Forecast:**

| Tool | What it does |
|---|---|
| **Get AQI & weather data** | Hourly air quality (PM2.5, PM10, NO2, SO2, CO, O3 and the Indian AQI) and weather (temperature, humidity, wind, rain, cloud, sunshine) at your points: up to 92 past days plus the weather forecast for the next days. Free, from Open-Meteo (CAMS and ECMWF models). |
| **Train forecasting model** | Forecast the next hours, days or months of a value in a table (AQI per station, rainfall, humidity, sales…) from its past, the calendar, nearby stations and inputs such as weather: LightGBM, XGBoost, Random Forest, linear and more, **backtested** against simple baselines (last value, same time one cycle ago) with a warning when the model isn't better. |
| **Forecast with a model** | Run a saved model on newer data (e.g. today's AQI and the weather forecast): the next hours / days / months with an uncertainty band, as a table, a chart and points on the map. |

**GIS tools & Assistant** (new in 0.0.3; Analysis ▸ Tools, with screenshots on the [What's new page](https://agritechixr.github.io/lulc-fetch/gis.html)):

| Tool | What it does |
|---|---|
| **Assistant** | Describe the work in a sentence: it looks at your layers, plans a workflow of the app's tools, shows it to you, runs it step by step, checks each result (empty or impossible results, mostly cloudy imagery count as failures to fix; other warnings are shown) and fixes or re-plans failed steps. It can add conditions when asked (“only if it's under 20 % cloudy”, “alert me if NDVI falls by 0.1”), and lists cautions before you run a plan. **Explain the results** sums up what came out from the results' own numbers. Models: free local ones through **Ollama** (qwen2.5 7B, one-click download), free online ones with your own key (**Hugging Face, Groq, Google Gemini, OpenRouter, Mistral, Cerebras**, or any OpenAI-compatible address such as LM Studio), or **Claude**. Keys stay in your OS keychain; only your request and a description of your layers (names, bands, fields, extents) are sent, never the pixels. |
| **Workflows & schedules** | Pick runs in the History and they become a workflow: their files, areas and years become its inputs, and where one run used another's result the workflow links the steps the same way. Run it again on other data, once or **for every layer or every polygon** of a layer (batch); edit steps, export and import workflows as files; the Assistant can save its plans as workflows too. **Schedules** run a workflow by themselves every few hours, daily at a time or weekly on a day, while the app is open (a run missed while it was closed runs once when it opens); date inputs can move with the run day (e.g. the last 30 days), and an **alert** shows when a result value crosses a limit. **Conditions** on any step: run it only if a value allows it (e.g. the download's cloud cover below 20 %: otherwise it is skipped, with the steps that need it, or the run stops), and after it runs **warn, alert or stop** when a value crosses a limit or **falls / rises by an amount since the previous run** (e.g. mean NDVI falls by 0.1). Every step's result is also checked for what can't be right (an empty image, an index outside −1…1, a table without rows, mostly cloudy imagery), and a **Before you run** list warns about risky settings. A **diagram** shows the workflow as boxes and arrows (data and conditions, live while it runs): drag, rewire, add conditions, reorder. |
| **Buffer** | Grow points, lines or polygons by a distance in metres measured on the ground (a negative distance shrinks polygons; shapes that shrink to nothing are dropped), each feature keeping its fields plus `buffer_m`, or **dissolved** into one shape. |
| **Vector & spatial analysis** | Select by attribute, Select by location, Overlay (intersection, union, difference, clip), Dissolve, Spatial join, Zonal statistics, Count points in polygons, Calculate geometry, Join table to layer, **Spatial statistics** (kernel density heat maps of points such as disease reports, Getis-Ord Gi* hot spots, Moran's I with local clusters, average nearest neighbour); centroids, convex hull, simplify, merge, multipart to single parts, fishnet grid, random points. Measured in metres on the ground. |
| **Fuzzy & suitability** | Gradual membership (0–1) instead of hard yes / no boundaries. **Fuzzy membership** of a raster (large, small, linear, Gaussian, near, sigmoid, trapezoid, with a live curve) or of the **distance to a vector layer** (near a road: 1 at the road, 0.5 at 500 m, ~0 beyond 1 km). **Fuzzy overlay** of several layers, each with its own function and weight (fuzzy gamma, weighted sum or product, AND, OR, product, sum): a 0–1 **suitability map** and five classes with hectares. **Fuzzy boundary & uncertainty**: an uncertainty map, nested α-cut zones, the crisp boundary and the transition zone as smooth polygons. **Fuzzy classification** (fuzzy c-means): a membership map per class, the hard class and the uncertainty of mixed pixels. |
| **SAR (Sentinel-1)** | Analysis ▸ SAR. **SAR workflow**: pick a GRD product (.SAFE / .zip), Planetary Computer scenes (GRD, or RTC already analysis-ready) or SAR rasters from GEE / ASF; it reads what the data is and **pre-ticks only what is still needed** (steps already done are greyed and never applied twice), and each ticked step asks for what it needs: orbit file (ESA precise POEORB, else restituted RESORB within hours), border and thermal noise removal, calibration to σ⁰ / β⁰ / γ⁰, speckle filtering (Refined Lee, Lee, Lee Sigma, Gamma MAP, Frost, boxcar, median), Range-Doppler terrain correction with Copernicus DEM 30 m (+ EGM96 geoid), terrain flattening to γ⁰ (**area-based, Small 2011**, or angular, Vollrath 2020), **incidence-angle normalisation**, linear and dB, auto UTM, pixel size or align to a layer, clip. **Frames of one pass are found and joined** when the area crosses a frame edge; **whole scenes** are processed in tiles. Every result has a **quality layer** (valid, layover, shadow, below the noise floor, outside the area) and a metadata .json (CEOS analysis-ready style). Several dates: **multi-temporal speckle filter (Quegan)**, mean / median / std / trend, change with **flooding**. Also VV/VH ratio, RVI, NDPI, span, texture. Checked against Planetary Computer's RTC (within 0.1 dB with the same noise handling). **InSAR & RTC on demand (ASF HyP3)**: interferograms (unwrapped phase, coherence, line-of-sight and vertical displacement) from SLC pairs, or GAMMA RTC, processed free by ASF with a NASA Earthdata login; the app finds the scenes and pairs, sends the jobs, and downloads the results into Contents. Also on their own: **Find Sentinel-1 scenes**, **SAR product inspector**, **Speckle filter**, **SAR features**, **SAR time series & change**. |
| **Raster & terrain** | Terrain (slope, aspect, hillshade), Contours, Reclassify, Change detection (difference or land-cover from → to with areas), Clip raster, **Raster calculator** (map algebra over bands of several rasters), Resample / reproject, **Enhance image** (stretch, CLAHE, denoise, sharpen, edges, focal statistics, majority filter). |
| **Conversion** | **Raster to polygon**: class maps to polygons with the class name and area in ha; one or more classes only, a minimum area that merges small patches into their neighbours first, smoothing, dissolve per class. **Raster to polyline**: the boundaries between classes, or the **centrelines** of roads and rivers, with a minimum length. **Raster to point**: a point per pixel (or every n-th) with each band's value. **Vector to raster**: a field's numbers, text as named classes with colours, presence, or a **count** of shapes per cell; a pixel size in metres or another raster's grid. **Convert features**: polygons ↔ lines, vertices → points, points → lines (grouped and ordered by fields, e.g. GPS tracks), points every n metres along lines, lines → segments, bounding boxes. |
| **Accuracy assessment** | Stratified random points per class, labelled on the map (keys 1–9, the map's class hidden), then the confusion matrix, overall / user's / producer's accuracy, F1, kappa and **class areas estimated from the sample with 95 % confidence intervals** (Olofsson et al. 2014), with an HTML report. **Area statistics**: hectares, km² and % of each class. |
| **Index time series** | NDVI, EVI, NDWI, NDMI, NDRE or SAVI of a point or field in every Sentinel-2 scene of a period, read from the free catalogue (only the area's pixels), clouds masked: a chart and a table. |
| **Georeference** | Put a scanned map, plan, photo, raster or **vector layer** (a drawing, a plan, local survey coordinates) on the map with control points: affine, 2nd-order polynomial or thin-plate spline, with each point's residual and the RMSE. |
| **Coordinate systems** | Data without one (a shapefile without .prj, a GeoTIFF or world-file picture without a system, a GeoPackage layer marked undefined) asks what to do: WGS 84, a system you choose (search all EPSG systems by name or code, with suggestions from its numbers, e.g. UTM 43N for metres near Bengaluru), control points, or keep it without one for now. **Right-click ▸ Coordinate system…** corrects any layer's system later or converts a copy to another (rasters reprojected; vectors exported as Shapefile / GeoPackage in that system). Tables with X / Y in metres (UTM …) go on the map too. |

Also new on the map: **3D maps** (DEMs as surfaces, extruded polygons, ViewCube), **style by attribute** (categories or graduated colours with a legend), measure distance / area / height with elevation profiles, Swipe and side-by-side maps, print layouts, undo / redo and Ctrl+K search. **Test data kit:** `scripts/make_test_data.py` downloads free data for six 2 × 2 km sites with a list of tools to try on each.

Across the app:
- **Projects:** one folder per project keeps your layers, results, tables, models and map view; it autosaves and reopens where you left off. You can also work without a project in a temporary workspace.
- **Save anywhere:** every tool can also save its result to a folder you choose, and right-click ▸ **Save to folder…** works on any layer, table or picture. A built-in folder picker helps.
- **Contents in two sections:** *2D data* (GeoTIFF, RGB photos / georeferenced JPG & PNG, Shapefile, GeoJSON, KML) and *Tabular data* (CSV, TSV, Excel, Parquet).
- **Data viewer under the map:** open tables and vector attribute tables with paging, sorting, search / filters, column statistics and rows linked to the map. Pictures can also be opened there.
- **Edit tables and attribute tables** in safe edit sessions (nothing changes until you save; overwrite with a warning, or save as new):
  - add, rename, convert and delete fields;
  - a **field calculator** with expressions and geometry values ($area, $perimeter, $x / $y…);
  - **Python (pandas)** scripts with test runs;
  - inline cell editing, deleting / adding rows, undo, restoring the previous version, and new tables or layers from filtered rows.
- **Resizable panels:** drag the edges of Contents, the tool panel and the data viewer.
- **Area picker:** every tool can work on just an area of your choice.
- **History menu:** every tool run with its time taken, input data, settings and where the results (models, maps, exports) went; searchable, kept across restarts. **Run again** repeats a run, or runs it with changed settings.
- **Progress and Cancel** for every long task, with the time taken; **ⓘ** shows every step in a resizable box. A failed run stays on screen with the reason, and every failure is recorded in `logs/errors.log` (**Help ▸ Error log**).
- **ⓘ hints** on every option.
- **Credentials** for Copernicus and USGS EarthExplorer, kept in your OS keychain.

## Download (Mac and Windows: no installation of Python needed)

### Step 1: download the file for your computer

1. Open the **[Releases page](https://github.com/agritechixr/lulc-fetch/releases/latest)**. You can also find it on this repository's main page: in the right-hand column, click **Releases** (or the version number under it, e.g. *v0.0.1-beta*).
2. Scroll down to **Assets** at the bottom of the newest release. Click **Assets** if the list is folded.
3. Click the file for your computer. Your browser saves it in your **Downloads** folder.

| Your computer | Click this file |
|---|---|
| Mac with Apple Silicon (M1, M2, M3, M4), macOS 14 Sonoma or newer | **[LULC-Fetch-macOS-AppleSilicon.dmg](https://github.com/agritechixr/lulc-fetch/releases/latest/download/LULC-Fetch-macOS-AppleSilicon.dmg)** |
| Windows 10 / 11 (64-bit) | **[LULC-Fetch-Windows.zip](https://github.com/agritechixr/lulc-fetch/releases/latest/download/LULC-Fetch-Windows.zip)** |

Not sure which Mac you have? Apple menu ▸ **About This Mac**: "Chip: Apple M…" means Apple Silicon. Macs with an Intel processor aren't supported by the ready-made app; they can [run from source](#run-from-source-for-development).

The files are large (about 170–200 MB), because Python and all libraries are inside. Ignore the "Source code (zip / tar.gz)" files: those are for developers.

### Step 2: install and open

**Mac**
1. In your **Downloads** folder, double-click **LULC-Fetch-macOS-…dmg**. A window opens showing LULC Fetch and an Applications folder: drag **LULC Fetch** onto **Applications**. You can then eject the disk image (the eject button next to *LULC Fetch* in Finder's sidebar).
2. The first time, double-click it. macOS says it can't check the app, because it isn't signed with an Apple developer ID yet. Go to **System Settings ▸ Privacy & Security**, scroll down, and click **Open Anyway** next to LULC Fetch. Alternatively, run this once in Terminal:
   `xattr -dr com.apple.quarantine "/Applications/LULC Fetch.app"`
3. From then on, just double-click it.

**Windows**
1. In your **Downloads** folder, right-click **LULC-Fetch-Windows.zip** ▸ **Extract All…** ▸ **Extract**. The app only runs from the extracted folder, not from inside the zip.
2. Open the extracted folder and double-click **LULC Fetch.exe**. If Windows shows "Windows protected your PC", click **More info ▸ Run anyway**. That happens once, because the app isn't signed yet.
3. Optional: right-click `LULC Fetch.exe` ▸ *Show more options* ▸ **Send to ▸ Desktop (create shortcut)**.

**Using the app**
- LULC Fetch opens in your web browser at **http://127.0.0.1:8765** (another free port if that one is busy; it only runs on your computer). If the page is closed, click **Open LULC Fetch** in the small window or open that address. The small window lets you reopen it, open your data folder, or quit. Closing that window stops the app.
- Everything runs on your own computer. The internet is only used to find and download satellite images, for background maps, for address search, and to download a crop's disease model from Hugging Face the first time it's needed. All analysis and machine learning work offline.
- Your files are kept in **Documents ▸ LULC Fetch**. Put Copernicus `.SAFE` products in its `data` folder. Projects can live in any folder.
- **Deep-learning tools:** the first time you open one, it offers to install the free **PyTorch add-on** (about 0.8 GB on Mac; on Windows about 1.1 GB for CPU only or 3.5 GB with NVIDIA GPU support). It is downloaded once into the data folder; everything else works without it.

## Build the apps yourself

- **Mac:** `./packaging/build_mac.sh` → `dist/LULC Fetch.app` and `dist/LULC-Fetch.dmg`. Needs the source setup below and `brew install libomp`.
- **Windows:** in PowerShell, `.\packaging\build_windows.ps1` → `dist\LULC Fetch\LULC Fetch.exe` and `dist\LULC-Fetch-Windows.zip`.
- **GitHub builds them automatically:** push a version tag (`git tag v0.2.0 && git push origin v0.2.0`), and the *Build desktop apps* workflow builds the Mac (Apple Silicon) and Windows versions, tests them, and publishes them on a Release. You can also run it by hand from the **Actions** tab.

## Run from source (for development)

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[web]"
.venv/bin/lulc-fetch-web          # opens http://127.0.0.1:8000
```

On macOS, also run `brew install libomp` (needed by XGBoost and LightGBM).

Deep-learning tools: `.venv/bin/pip install -e ".[web,dl]"` (or click *Install the deep-learning add-on* in the app). YOLO and SAM: `.venv/bin/pip install -e ".[yolo]"` (or *Install the YOLO & SAM add-on*); ultralytics is AGPL-3.0 and is never bundled with LULC Fetch. For an NVIDIA GPU on Windows / Linux, install PyTorch from [pytorch.org](https://pytorch.org/get-started/locally/) first.

No account is needed to search and download imagery. Accounts are only needed for original Copernicus or USGS product downloads.

Code layout: one file per tool on each level, `lulc_fetch/` (science), `webapp/routes/<tool>.py` (server) and `webapp/static/app/tools/` or `webapp/static/tools/<menu>/` (browser); what every tool shares is in `webapp/core.py` and `webapp/static/app/core/` / `webapp/static/tools/lf.js`. See the [User Guide, section 35](docs/USER_GUIDE.md#35-for-developers-adding-a-tool) to add a tool.

## Tests

`tests/` checks every tool end to end on small synthetic data (about 2 minutes; 30 seconds without deep learning):

```bash
.venv/bin/pip install -e ".[web,test]"
.venv/bin/python -m pytest              # or: -m "not dl" for the quick run, --network to include imagery search
```

`tests/real/` runs every tool on the real data in `data/` and scores it against real ground truth (`pytest tests/real --real`,
15–25 minutes). Each run writes a report with one folder per tool to `test_reports/` (open `test_reports/index.html`).
See [tests/README.md](tests/README.md) for what is covered.

## Typical workflow

**Find imagery** (or open a `.SAFE` product) → **Training samples** → **Stack layers** (optional) → **Raster → table** → **Train a model** → **Classify an image** → **Export**

Deep learning: **Make training data** (image + ground truth → patches) → **Train classify model** → **Classify image** → **Export**

Object detection: high-resolution image → **Detect object** (pretrained) → **Export** (Shapefile / GeoJSON / KML), or label your objects (**Training samples**) → **Train detection model** → **Detect object**

## Command line

```bash
.venv/bin/lulc-fetch composite --bbox 77.56,12.94,77.61,12.99 --start 2026-01-01 --end 2026-03-31 -o out/composite.tif
.venv/bin/lulc-fetch --help
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
