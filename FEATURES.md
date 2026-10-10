# LULC Fetch: every feature

LULC Fetch is a free desktop GIS for land use / land cover work with free satellite data. It runs on your own computer
(Mac and Windows) and opens in your web browser. Everything below is free and needs no account unless it says so.

This page lists every feature, grouped by where you find it in the app: **File · Insert · Analysis · History · View ·
Help**, then the map and Contents panel. Features marked *(new)* were added in the latest development version. For
installation see the [README](README.md); for how to use each tool, the [User Guide](docs/USER_GUIDE.md); for every
tool's parameters and every model's settings, the [Tool reference](docs/TOOL_REFERENCE.md).

---

## The app at a glance

- **Ribbon**, as in Word: File, Insert, Analysis, History, View and Help tabs, with commands in groups. You can pin it
  open, collapse it (Ctrl+F1 or double-click a tab), make it **compact** *(new)*, and show or hide **icons next to tool
  names** *(new; hidden by default)*.
- **Contents** (left): your layers, in 2D data, 3D data and Tabular data sections. **Map** (centre): 2D (Leaflet) or 3D
  maps, each in a tab. **Tool panel** (right): the open tool. **Data viewer** (bottom): tables.
- **Search** (Ctrl+K): find any command, tool or layer by typing.
- **Background jobs**: long tasks run in the background, with a progress bar, details, a log and Cancel. Results
  go into Contents by themselves.
- **Undo / Redo** (Ctrl+Z / Ctrl+Y) for changes to layers and maps.
- **Light, dark or system theme.**
- **Works offline** for everything except downloading data.

---

## File

| Group | Features |
|---|---|
| **Save** | Save and Save as (the whole session: maps, layers, styles, views). |
| **Export map** | **Picture** of the map (PNG), and a **Print layout** with title, subtitle, legend, scale bar, north arrow, date and credits. |
| **Project** | New, open and close a **project**: one folder that keeps layers, tables, models, results and the map view together. Show the project folder. |
| **Add data** | GeoTIFF, Shapefile, GeoPackage, GeoJSON, KML, CSV / Excel / Parquet tables, pictures; GeoTIFFs already in the workspace; Copernicus **.SAFE** products (Sentinel-1 and Sentinel-2). |
| **Selected layer** | Export, Properties (style, symbology, labels, transparency), Remove; remove all layers. |
| **Settings** | **Recover last session**; **clean up working files** (shows what takes space); **Credentials** (Copernicus, USGS, NASA Earthdata, AI keys), kept in your system's keychain. |

## Insert

| Group | Features |
|---|---|
| **New map** | Several maps at once, as tabs: **2D** or **3D** (terrain from a DEM, layers draped or extruded by an attribute, ViewCube, standard views). |
| **This map** | Rename, duplicate, close the map. |
| **Layers** | Copy the selected layer(s) or all layers and paste them into another map (Ctrl+C / Ctrl+V). |
| **Library** | Ready-made GIS data, downloaded once: India's states, districts, sub-districts, villages, constituencies, city wards, highways, railways… (300+ layers), plus anything else in the library on Hugging Face. |
| **Bookmarks** | Save map places as tiles with a picture; Ctrl+B bookmarks the current view. |
| **Online map layer** | Layers drawn straight from web map services: **Bhuvan** (ISRO's maps of India and LULC), **NASA GIBS** (daily satellite imagery by date), or any **WMS, WMTS or XYZ** address. |
| **Field collection** | A phone web page that works offline: geotagged points and photos with the crop and a note, sent as one .zip. In the app they become a layer with their photos, and leaf photos can go straight to Diagnose crop disease. |

## Analysis

The Analysis tab has six categories: **Tools, Agri, Embeddings, Forecast, SAR and Hydrology** *(new)*.

### Analysis ▸ Tools

**Imagery**

| Tool | What it does |
|---|---|
| Find imagery | Search, preview and download **Sentinel-2** (10 m) and **Landsat 8–9** (30 m): single dates, cloud-free composites, and land-cover reference maps (ESA WorldCover, Dynamic World, ESRI…). |
| Index analysis | 30 spectral and radar indices (NDVI, EVI, SAVI, NDWI, NDBI, NBR, NDRE, RVI…) or your own formula, with band-order checks. |
| Index time series | NDVI, EVI, NDWI, NDMI, NDRE or SAVI of a point or field in every Sentinel-2 scene of a period, clouds masked: crop calendars, drought, harvest dates. |
| PCA & dimensionality reduction | PCA, Kernel PCA, NMF, ICA and more on any multiband image. |
| Stack layers | Bands of several layers (Sentinel-2, Sentinel-1, DEM, indices…) on one grid. |
| Mosaic / merge rasters | Join tiles or scenes into one image, with smooth blending and colour matching so no seams show. |
| Burn severity (dNBR) | Where land burned between two dates and how badly (USGS classes), with burned hectares. |
| Water mask | Open water from Sentinel-2, Landsat or any multispectral image (AWEI, NDWI, MNDWI…), with clouds, shadow and snow masked and a confidence layer. |
| **Pansharpen** *(new)* | Make a multispectral image as sharp as its panchromatic band (e.g. Landsat 30 m → 15 m with band 8): Gram-Schmidt adaptive, Brovey or IHS. |
| **Spectral unmixing** *(new)* | The fraction of each pixel that is vegetation, soil, water, built-up…: from labelled samples of pure materials, or materials found in the image (ATGP). Fully constrained (0–1, adding to 1), with an error band and the spectra as a table. |

**Training data**

| Tool | What it does |
|---|---|
| Training samples | Draw labelled polygons and points for each class on the map. |
| Raster → table | Any image (multispectral, hyperspectral, SAR) to a CSV / Parquet table, with ground-truth labels. |
| Make training data | Cut large images and their ground truth into image / label patches for deep learning. |

**Classical ML**

| Tool | What it does |
|---|---|
| Classical ML (tabular data) | 16 supervised models (Random Forest, XGBoost, LightGBM, SVM, Maximum Likelihood…) for classification or regression, with tuning, comparison and reports; **unsupervised**: K-means, hierarchical, DBSCAN, HDBSCAN, spectral, Gaussian mixtures. |
| Classical ML for raster | Train straight from an image and its ground truth (polygons, points or a class raster) and map it: SVM, Maximum Likelihood, Random Forest, SAM and more, for RGB, multispectral, hyperspectral or embeddings. |
| Spatial cross-validation | How much a random train / test split overstates a map's accuracy: the same model with random k-fold and with spatial blocks, and a correlogram. |
| Interpolation | A surface from values at points (air-quality stations, rain gauges, soil samples): IDW, kriging, spline, natural neighbour, nearest neighbour, trend surface, TIN, compared by leave-one-out. |

**Deep learning** (optional add-on, installed from the app)

| Tool | What it does |
|---|---|
| Train classify model | Semantic segmentation: U-Net, U-Net++, DeepLabV3+, PSPNet, FPN, SegFormer and more, with early stopping and live curves. |
| Classify image | Map a whole image with a trained model: tiled, seamless, with a confidence layer. |
| Detect object | Vehicles, ships, planes, storage tanks, people… in high-resolution images: YOLO26 (incl. an aerial model), Faster R-CNN, RetinaNet, Mask R-CNN, SAM 2.1. |
| Train detection model | Train YOLO26 / YOLO11 on your own objects (boxes, outlines or rotated boxes) from labelled polygons or points. |

**Vector**

| Tool | What it does |
|---|---|
| Buffer | Grow points, lines or polygons by metres on the ground (or shrink polygons), optionally dissolved. |
| Select by attribute | Features meeting a condition, e.g. `crop == "rice" and area_ha > 2`. |
| Overlay | Intersection, union, difference, symmetric difference, clip. |
| Dissolve | Merge shapes: all into one, or one per value of a field. |
| Geometry tools | Centroids, convex hull, simplify, merge layers, multipart to single part, fishnet grid, random points in polygons. |

**Spatial analysis**

| Tool | What it does |
|---|---|
| Zonal statistics | A raster summarised in each polygon (e.g. mean NDVI per field), or the % of each land-cover class. |
| Select by location | Features that intersect, are inside, contain, are apart from or within a distance of another layer. |
| Spatial join | Each feature gets the attributes of the feature it overlaps, lies in or is nearest to. |
| Count points in polygons | Points per polygon and the sum of a field. |
| Calculate geometry | Area, perimeter, length and centroid as fields, measured on the ground. |
| Join table to layer | Attach a CSV / Excel / Parquet table by a shared field. |
| Spatial statistics | Kernel density, hot spots (Getis-Ord Gi\*), Moran's I, nearest-neighbour analysis. |
| Spatial autocorrelation (raster) | Global Moran's I and Geary's C with significance, and Local Moran's I cluster maps. |
| **Routing (roads)** *(new)* | On OpenStreetMap roads (downloaded for the area, no account) or your own line layer, by car, bicycle or on foot: the **quickest route** through stops, **service areas** (what can be reached in 5, 10, 15… minutes) and the **closest facility** (e.g. each village's nearest hospital by travel time). One-way streets and speed limits are respected. |

**Fuzzy & suitability**

| Tool | What it does |
|---|---|
| **AHP weights & overlay** *(new)* | Compare factors in pairs on Saaty's 1–9 scale; weights from the eigenvector with the **consistency ratio** (and which pair to reconsider), live as you choose; then the weighted overlay of the scored layers into a suitability map. |
| Fuzzy membership | A layer as gradual membership 0–1 (slope, NDVI, rainfall, distance to roads or rivers). |
| Fuzzy overlay (suitability) | Suitability from several layers, each with its own membership function and weight. |
| Fuzzy boundary & uncertainty | Where a boundary is uncertain, nested zones (α-cuts) and a smooth crisp boundary. |
| Fuzzy classification (c-means) | Each pixel's membership in every class, the hard class and an uncertainty map. |

**Image features and objects**

| Tool | What it does |
|---|---|
| Local statistics | Per-pixel mean, median, std, variance, min, max, range, CV, entropy, skewness, gradient, Laplacian over 3×3 … 31×31 windows. |
| Texture (GLCM) | Haralick texture: contrast, dissimilarity, homogeneity, energy, ASM, correlation, entropy, mean, variance. |
| Edges & boundaries | Sobel, Canny, Laplacian of Gaussian, gradients: field boundaries, shorelines, roads. |
| Multi-scale features | Gaussian scale space as one feature stack for classification. |
| Morphology | Erosion, dilation, opening, closing, top-hat…; remove small objects, fill holes. |
| Superpixels (SLIC) | Small homogeneous segments with their mean values, as a raster and polygons. |
| Connected components | One id per object in a mask, with size, area and centre, and polygons. |

**Raster & terrain**

| Tool | What it does |
|---|---|
| Terrain | Slope, aspect and hillshade of a DEM. |
| **Viewshed & line of sight** *(new)* | What can be seen from one or more points (with several, how many of them see each place), or whether A can see B and where the view is blocked, with the earth's curvature and refraction. |
| **LiDAR point cloud** *(new)* | A .las point cloud (.laz with the optional laspy add-on) into a **ground model (DTM)**, **surface (DSM)**, **height of trees and buildings (CHM)**, point density, intensity and **tree tops**. Uses the file's ground class, or finds the ground itself. |
| Contours | Contour lines as a vector layer. |
| Reclassify | Ranges of values to classes. |
| Change detection | Difference and % change between dates, or every from → to land-cover change with its area. |
| Clip raster | Cut to polygons (or keep the outside). |
| Resample / reproject | New pixel size or coordinate system, with 10+ resampling methods. |
| Enhance image | Contrast stretch, equalisation, CLAHE, gamma, denoise, sharpen, upscale ×2 / ×4. |
| Raster calculator | Map algebra over bands of several rasters. |

**Assess, conversion and output**

| Tool | What it does |
|---|---|
| Area statistics | Hectares, km² and % of each class, for the whole map or inside an area. |
| Accuracy assessment | Random points per class labelled on the map, then a confusion matrix, kappa and corrected areas with confidence intervals. |
| Raster to polygon / polyline / point | Class maps to polygons with areas; class boundaries or centrelines of roads and rivers; pixels to points. |
| Vector to raster | Burn a layer into a GeoTIFF: a field's values, presence, a count per cell, a **0 / 1 mask** (grown by a buffer, or inverted) *(new)* or the **distance in metres to the nearest shape** *(new)*. |
| Convert features | Polygons ↔ lines, vertices → points, points → tracks, points every n metres, segments, bounding boxes. |
| Georeference | Put a scanned map, plan, photo, raster or vector layer on the map with control points. |
| Export data | GeoTIFF, PNG, Shapefile, GeoPackage, GeoJSON, KML. |
| Downloads & jobs | Background downloads, logs and output files. |

**Automate**

| Tool | What it does |
|---|---|
| Assistant | Describe the work in a sentence: it plans a workflow of the app's tools, shows it, runs it step by step, checks every result and fixes failed steps; it can explain the results. Free local models (Ollama), free online ones with your own key (Hugging Face, Groq, Gemini, OpenRouter, Mistral, Cerebras, any OpenAI-compatible address), or Claude. |
| Workflows & schedules | Runs from History become reusable workflows (with inputs, a diagram, conditions and alerts), run once, in batch for every layer or polygon, or on a **schedule** (every few hours, daily, weekly). |

### Analysis ▸ Agri (phone and camera photos)

| Tool | What it does |
|---|---|
| Diagnose crop disease | Leaf photos → the crop (42 crops) → its disease (top 3 with confidence; 91–100 % on test photos). Unclear photos get "retake". Geotagged photos become a disease map. |
| Crop disease guide | About 9,000 expert questions and answers on symptoms, treatment, spray schedules and pests, searchable offline. |

### Analysis ▸ Embeddings

| Tool | What it does |
|---|---|
| Download embeddings | Free AI embeddings for any area: Google **AlphaEarth** (64-D) and **TESSERA** (128-D), 10 m, 2017–2025. |
| Explore embeddings | A colour view (PCA) and **places similar** to the ones you click. |
| Train embedding model | One of eleven light segmentation models (0.15–0.95 M parameters) trained on your labels. |
| Classify with embedding model | Map other areas or years with a trained model. |
| Convert embeddings | 8-bit ↔ 16 / 32-bit float, showing how much values change. |

### Analysis ▸ Forecast

| Tool | What it does |
|---|---|
| Get AQI & weather data | Hourly PM2.5, PM10, NO₂, SO₂, CO, O₃, the Indian AQI and weather at your points: 92 past days and the forecast (Open-Meteo). |
| Train forecasting model | Forecast hours, days or months ahead from the past, the calendar, nearby stations and weather: LightGBM, XGBoost, Random Forest…, backtested against simple baselines. |
| Forecast with a model | Run a saved model on new data, with an uncertainty band. |
| **Sentinel-5P air quality** *(new)* | **NO₂, CO, SO₂, formaldehyde, ozone, methane and aerosols** from Sentinel-5P (TROPOMI), averaged over your area and dates on a grid, with a daily series table. Only good-quality pixels; only the part of each overpass over your area is downloaded. |

### Analysis ▸ SAR (Sentinel-1)

| Tool | What it does |
|---|---|
| SAR workflow | From a .SAFE / .zip, a Planetary Computer scene or a SAR layer: it says what is already done and you tick what the data still needs (orbit, noise removal, calibration, speckle, terrain flattening and correction, dB). |
| Find Sentinel-1 scenes | Search Planetary Computer for analysis-ready RTC or GRD over your area. |
| SAR product inspector | What a product is and which processing steps are done, needed or optional. |
| Speckle filter | Refined Lee, Lee, Lee Sigma, Gamma MAP, Frost, boxcar, median. |
| SAR features | VV/VH ratio, RVI, polarisation indices, span, texture. |
| SAR time series & change | Per-pixel statistics and trend over dates; change first → last, with flooding. |
| InSAR & RTC on demand | Interferograms (ground movement, coherence) or RTC processed by ASF HyP3 (free NASA Earthdata account). |
| SAR + optical fusion | Crops or land cover from Sentinel-2 and Sentinel-1 together, comparing optical only, SAR only, early and late fusion. |
| Fill clouds from SAR | Cloudy optical pixels filled from Sentinel-1 (and a same-day coarse image or a clear image of another date). |
| Flood & water map | Water and flooding from radar, with a 0–1 confidence, optionally with an optical water mask as evidence. |
| **Flood map: ML refinement** *(new)* | A cleaner flood map: LightGBM or a compact **U-Net** learns from the flood map's own confident pixels (backscatter, texture, change, HAND, slope) and decides the uncertain ones; checked on held-out blocks. |
| Soil moisture (change detection) | Relative surface soil moisture (0–100 %) per date from a series of one orbit track. |

### Analysis ▸ Hydrology *(new)*

Every hydrology and watershed tool, in five ribbon groups: **Data & DEM, Watersheds, Runoff & erosion, Floods, Water
planning**. All work on a DEM in metres (up to 16 million
cells; a 2000 × 2000 DEM takes about 10 seconds per tool), with numpy and scipy only. A DEM made by DEM preparation is
marked as conditioned, so the other tools use it as it is.

| Tool | What it does |
|---|---|
| **Rainfall data** | Free rainfall without an account: **CHIRPS** totals for your dates or the **mean annual rainfall** over years, on a 0.05° grid (only your area is downloaded); **design storms**: the 1-, 2-, 3- and 5-day rain expected once in 2, 5, 10, 25, 50 and 100 years (Gumbel fit of the ERA5 daily archive, 1940–now) and the daily rain / evaporation / temperature series. |
| **DEM preparation** | Make a DEM drain: **fill no-data holes** (voids filled smoothly from their edges), **align** it to another raster or a pixel size, **burn in** known rivers and canals (with a sloping trough), **breach** pits (cut through roads and embankments across valleys: Lindsay's complete breaching, with a depth limit beyond which it fills instead) and **fill** the remaining pits (priority flood). Also a raster of what changed. |
| **Flow direction & accumulation** | **D8**, **D-infinity** (Tarboton: flow split between two neighbours, the direction as an angle) or **MFD** (Freeman: shared among all lower neighbours): flow direction, accumulation in cells, **contributing area** (km²) and **specific catchment area** (m²/m). |
| **Watersheds** | **Catchments of outlet points** with **pour-point snapping**; points upstream of another give **nested watersheds** with their parent (the **basin hierarchy**). **Sub-watersheds**: one per stream link, each knowing the sub-basin downstream and its order, optionally only inside a point's watershed. **Every basin** above a size. Boundaries as polygons with area, perimeter, compactness, mean height, relief, mean slope, stream length, drainage density, **longest flow path** and **time of concentration**; a basin-id raster and a table. |
| **Drainage network** | Streams by a contributing-area threshold, as **links** between **sources, junctions and outlets**: **Strahler** order and **Shreve** magnitude, length, drop, slope, area, and the **topology** (the link downstream, from / to node). Order, magnitude and link-id rasters, **drainage density**, and a table of the orders (counts, lengths, **bifurcation ratios**: Horton's laws). |
| **Terrain & runoff indicators** | **Slope**, **aspect**, **curvature** (profile, plan, total), **TWI** (wetness), **SPI** (stream power), **HAND** (height above the nearest drainage, and the distance to it along the flow), **depressions** (depth, and each as a polygon with area, deepest point and volume), **flow length** (down to the outlet, up to the divide) and **flow paths** downhill from points. |
| **Morphometry & prioritisation** | Every sub-watershed's linear, areal and relief parameters (bifurcation ratio, drainage density, stream frequency, texture, form factor, elongation, circularity, compactness, relief ratio, ruggedness, **hypsometric integral and curve**…) and its **priority for soil and water conservation** by the compound value. |
| **Soil erosion (RUSLE)** | Yearly soil loss (t/ha/yr) = R · K · LS · C · P: rainfall erosivity from mean annual rainfall, soil erodibility from texture or a raster, LS from the DEM, cover from land cover or NDVI, practice factor; FAO classes, and per watershed the **sediment yield** (delivery ratio). |
| **Rainfall–runoff (SCS-CN)** | How much of a storm runs off: **curve numbers** from a land-cover map (WorldCover, Dynamic World, ESRI or by class names) and the hydrologic soil group (A–D, or a soil raster), or your own CN raster; dry / normal / wet conditions; runoff depth and coefficient; runoff **routed downhill** (volume through each cell); per watershed of the outlets: runoff depth and volume, time of concentration and **peak flow** (SCS unit hydrograph). |
| **Design flood hydrograph** | Flow at a watershed's outlet hour by hour through design storms (SCS Type II or even rain): excess rain by SCS-CN, the **SCS unit hydrograph**, peak flow, time to peak and volume for each storm. |
| **Streamflow modelling** | Daily river flow from rain and evaporation, learned from observed flow: **GR4J** (conceptual model, calibrated automatically), **LightGBM / Random Forest** (machine learning) and an **LSTM** (deep learning), all scored on years they never saw (**NSE, KGE**, bias) against a seasonal baseline. Rain and evaporation from the table or the ERA5 archive. |
| **Flood from HAND** | What floods when rivers rise 1, 2, 5 … m: water **depth and extent at each level** from the height above the nearest drainage, with flooded areas and volumes. A quick first map, not a hydraulic model. |
| **Flood simulation (2D)** | Water spreading over the DEM through time from river inflows (steady or a hydrograph) and / or rain: the **local inertial shallow-water model** of LISFLOOD-FP, with Manning roughness from land cover; **maximum depth, maximum speed, arrival time**, depth maps over time and an exact water balance. |
| **Flood depth (FwDET)** | Water depth inside a mapped flood (the SAR flood map, a water mask or polygons) from a DEM. |
| **Flood impact** | What a flood covers: hectares of each land-cover class, people (a population raster), buildings and km of roads, split by depth when known. |
| **Flood susceptibility** | Where floods are likely, learned from a **flood inventory** (points or polygons, e.g. from the SAR flood map) and predictor layers (HAND, TWI, SPI, slope, curvature, distance to streams, land cover…) with Random Forest or LightGBM, scored by **ROC AUC with spatial-block cross-validation** next to a random split; a probability map, five classes (very low … very high) and the importance of each predictor. |
| **Groundwater potential** | Groundwater potential zones from slope, drainage density, wetness, rainfall, land cover, lineament density, geology and soil, **weighted by AHP**; checked against wells (rank correlation). |
| **Check dams & ponds** | Sites along the streams where a small dam holds the most water for its length, with the pond each makes; or the **area–capacity curve** of a site. |
| **Hydrology in one go** | The quick version: fill, flow, accumulation, streams with Strahler order, watersheds and TWI in one run. |

Also in the Hydrology category, as shortcuts: the SAR **flood & water map** (Otsu and adaptive tile-based thresholds),
its **ML refinement**, **AHP**,
the optical **water mask**, **fuzzy overlay** and **fuzzy boundary** (uncertainty and boundary refinement), **spatial
cross-validation**, **resample** and the **raster calculator**.

## History

- Every run, with its settings, inputs, outputs, log and time; re-run it, open its results, or turn runs into a **workflow**.

## View

| Group | Features |
|---|---|
| **Panels** | Show or hide Contents, the tool panel and the data viewer; reset panel sizes. |
| **Basemap** | Streets, satellite, topographic or none; place labels on top. |
| **Navigate** | Zoom to all layers. |
| **Measure** | Distance and area (on the ground; in 3D along the terrain), height at a point, and an **elevation profile** chart. |
| **Compare** | **Swipe** two layers with a line across the map. **Side by side**: a 2D and a 3D map that move together. **Linked views** *(new)*: 2 to 4 maps side by side, each with its own layers (drag layers or a whole group from Contents onto a view); linked views pan and zoom together and show the mouse position in the others; save and reopen **layouts**. **Time slider** *(new)*: step or play through layers in date order (taken from their names) and save the animation as a **GIF or MP4** with date labels. |
| **Theme** | Match system, light, dark. |
| **Ribbon** | Always show the ribbon, **compact ribbon** *(new)*, **icons next to tool names** *(new)*. |

## Help

- Quick guide, Getting started, Keyboard shortcuts, a guided Tour, About.
- **Error log** of every failed run, and the log file.

---

## Contents panel (layers)

- Add, show / hide, reorder by dragging, set transparency, zoom to, rename, remove.
- **Select several layers** *(new)*: **Ctrl-click** (⌘-click on Mac) adds or removes a layer; **Shift-click** selects
  every layer between the last one clicked and this one. Right-click the selection to zoom to, show, hide, show only,
  copy, move to the top or bottom, group, or remove them all; **Delete** removes them and **Ctrl+C** copies them.
- **Layer groups** *(new)*: put layers in a named group with one checkbox for all of them; collapse, rename, zoom to
  the group, or ungroup. Drop a layer onto a group member to add it to the group.
- **Style**: single colour, categories or graduated classes by attribute, line width and dashes, legends; raster band
  combinations (RGB), stretch and colour ramps.
- **Properties, metadata and coordinate system** of each layer; data without a coordinate system can be placed or
  georeferenced.
- **Attribute table** in the data viewer: sort, filter, edit, field calculator.
- **Tabular data**: CSV, Excel and Parquet tables open in the data viewer; points from a table's coordinates.

## Map

- **Identify**: click the map to read pixel values of rasters and the attributes of shapes.
- **Draw** areas of interest, training samples and clip areas.
- **Right-click menu** on the map: copy the coordinates, what's here (values of every layer), add a point, centre or
  zoom here, find imagery or download embeddings around the point, open the place in Google Maps or OpenStreetMap.
- **3D maps**: terrain from any DEM, draped layers, buildings extruded by an attribute.

## Command line

- `lulc-fetch` downloads Sentinel-2 imagery, composites and land-cover maps for an area without the app.

## Data sources (all free)

Sentinel-2, Sentinel-1, Landsat 8–9 and Sentinel-5P (Microsoft Planetary Computer, Copernicus Data Space, USGS);
ESA WorldCover, Dynamic World and other land-cover maps; Copernicus 30 m DEM; Google AlphaEarth and TESSERA
embeddings; Open-Meteo air quality and weather; OpenStreetMap roads; Bhuvan and NASA GIBS map services; the LULC Fetch
Library and crop-disease models on Hugging Face.
