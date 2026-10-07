# Changelog

## 0.0.3 beta (2026-10-07)

A full GIS around the land-cover tools: 2D and 3D maps, an Assistant that plans and runs workflows, and about 35 new
tools. Everything below is new since 0.0.2.

- **Online models for the Assistant**, as well as local Ollama models and Claude: Hugging Face (Inference Providers),
  Groq, OpenRouter (free models), Google Gemini, Mistral, Cerebras, or any OpenAI-compatible address (LM Studio,
  llama.cpp, vLLM). Free tiers with your own key, kept in the system keychain (Credentials ▸ Online models). List models
  asks the service which models it has. **Explain the results**: after a run, the model sums up what came out, from
  the results' own numbers.
- **Accuracy assessment** (Analysis ▸ Tools ▸ Assess): stratified random points per class, labelled one by one on the
  map (keys 1–9, the map's class hidden), then the confusion matrix, overall / user's / producer's accuracy, F1, kappa,
  and the area of each class estimated from the sample with 95 % confidence intervals (Olofsson et al. 2014), with an
  HTML report. **Area statistics**: hectares, km² and % of each class (inside an area).
- **Raster calculator**: expressions over bands of several rasters (A, B, …), aligned to the first one's grid:
  arithmetic, comparisons, and / or / not, where(), abs, sqrt, log, min, max, clip. True / false results become masks.
- **Index time series**: NDVI, EVI, NDWI, NDMI, NDRE or SAVI of a point or a field in every Sentinel-2 scene of a
  period, read straight from the free catalogue (only the area's pixels), clouds masked with each scene's
  classification, as a chart and a table.
- **Georeference**: put a scanned map, plan or photo on the map with control points (picture ↔ map), affine,
  2nd-order polynomial or thin-plate spline, with each point's residual and the RMSE.
- **Style by attribute**: colour a vector layer by a field's values (categories) or by classes of a number field
  (quantiles, equal intervals, natural breaks) with colour ramps, and a legend in Contents.
- **Find place** for the Assistant and Workflows (`/api/vector/place`): a place name (a stadium, school, town,
  address) becomes a point layer, or its outline, from OpenStreetMap, so "a 20 km buffer around M. Chinnaswamy
  Stadium" finds the stadium first instead of guessing coordinates or using the map view. Plans leave out inputs no
  step uses.
- **Single colour…** in a vector layer's right-click menu (one colour for every point, line or polygon). Style by
  attribute no longer suggests id fields such as osm_id, and warns when every feature would get its own colour.
- Contents shows the normal arrow over layers (not the map's pan hand); the closed hand only while a layer is dragged.
- **Scheduled workflows**: a workflow runs by itself every few hours, daily or weekly while the app is open (a missed
  run runs once when it opens); dates can move with the run day; an alert when a result value crosses a limit.
- **Zonal statistics** of class maps names its fields after the classes (pct_tree_cover, majority_class).
- Checked on the test data kit's real layers: **Buffer** circles are round to within 0.2 % of their area (64 sides
  instead of 16), a negative buffer no longer stops with "not a valid geometry" when a small polygon shrinks to
  nothing (it is dropped), and **Vector to raster ▸ count** counts each line or polygon once (at a point inside it), so
  buildings per cell add up to the number of buildings.
- **Test data kit**: `scripts/make_test_data.py` downloads free data for six 2 × 2 km sites (Sentinel-2, Sentinel-1,
  NAIP, Copernicus DEM, WorldCover, AlphaEarth, OpenStreetMap, air quality) with a README per site listing the tools to
  try on it.
- **Conversion tools** (Analysis ▸ Tools ▸ Conversion): Raster to polygon (class, area in ha; minimum area merges
  small patches first, smoothing, dissolve), Raster to polyline (class boundaries, or centrelines of roads / rivers),
  Raster to point (each band's value), Vector to raster (a field's numbers, text as named classes, presence or a count
  per cell; a pixel size or another raster's grid), Convert features (polygons ↔ lines, vertices → points, points →
  lines, points along lines, lines → segments, bounding boxes). Server: `/api/convert/*`.
- **Resampling methods** (nearest, bilinear, cubic / bicubic, cubic spline, lanczos, average, mode, median, min, max,
  quartiles) as an optional setting in Find imagery downloads, Download embeddings, Stack layers, Make training data
  and Change detection; new **Resample / reproject** tool (pixel size, factor or CRS).
- **Enhance image**: contrast stretch, histogram equalisation, CLAHE, gamma, median / gaussian denoise, unsharp
  sharpening, Sobel / Laplacian edges, focal statistics, majority filter for class maps, enlarge ×2 / ×4 with cubic
  or lanczos; presets for computer vision, denoising, edges, texture and cleaning class maps.
- Website: a "What's new" page with screenshots of 3D maps, the Assistant and the GIS tools.
- **Spatial analysis tools:** Zonal statistics (mean / min / max… of a raster per polygon, or % of each class), Select
  by location (intersect, inside, contain, apart, within a distance), Spatial join (overlapping most, inside, nearest
  with the distance), Calculate geometry (area m² / ha, perimeter, length, centroid), Count points in polygons (and a
  sum), Join table to layer (by a shared field).
- **Raster & terrain tools:** Terrain (slope, aspect, hillshade), Contours, Reclassify (ranges to named classes),
  Change detection (difference and % change, or land-cover from → to with areas), Clip raster (or mask).
- **Geometry tools:** centroids, convex hull, simplify, merge layers, multipart to single parts, fishnet grid, random
  points. Every tool runs as a job with History, can be a Workflow step and is in the Assistant's toolbox.
- The Assistant's test set: qwen2.5:7b plans 74% of 9 tasks right (3 runs each), llama3.2 52%.
- **Assistant, after GISclaw and OpenClaw:** it looks at the data first (bands and value ranges, columns and sample
  rows, fields), runs a plan step by step, looks at what each step made, fixes a failed or empty step (3 tries) or
  replans the rest (shown to you first), and keeps the errors as known pitfalls; long-term notes ("remember …"),
  saved conversations, saved workflows as skills; a tool handbook; step numbers counted from 1 and area names fixed
  automatically.
- **Vector tools** (Analysis ▸ Tools ▸ Vector): Buffer (metres), Select by attribute (and / or, SQL-like; also the
  attribute table's Query… button), Overlay (intersection, union, difference, symmetric difference, clip), Dissolve.
  Jobs with History; results as GeoJSON files; usable in Workflows and by the Assistant.
- **Assistant** (Analysis ▸ Tools ▸ Assistant): say what you want done; it plans a workflow of the app's tools from your
  Contents and the map view, you check the plan, then run it (in Workflows) or review and save it. A free local model
  through Ollama (one-click download of the recommended model), or Claude with your own Anthropic key (Credentials).
  Plans are checked against the tools' settings and sent back to the model to fix. Server: `/api/assistant/*`.
- **Workflows** (Analysis ▸ Tools ▸ Workflows, or History ▸ Workflows): make a chain of tools from runs in the History
  (their files, areas and years become inputs; a step uses an earlier step's result where the runs did), then run it on
  other data, once or for each layer or polygon (batch); edit, export and import workflows. Server: `/api/workflows`.
- **Top bar:** the logo (About) and a quick access bar: Save, Save as, Undo, Redo, plus any ribbon command or tool
  (right-click a ribbon button to add it or see its shortcut); **search every command, tool, layer, map and bookmark
  (Ctrl+K / Ctrl+F)**.
- **Undo / Redo (Ctrl+Z / Ctrl+Y)** for layers and maps: add, remove, move, rename, show / hide, opacity, style, 2D ↔ 3D
  data, paste, remove all, new / closed / renamed maps.
- **Save (Ctrl+S) / Save as (Ctrl+Shift+S):** Save as makes a new project folder and copies in the files the layers use
  (`/api/project/save-as`).
- **View ▸ Measure:** distance, area, height and an **elevation profile** chart (`/api/rasters/profile`), on 2D and 3D
  maps (in 3D: along the ground, climb and descent); keep any measurement as a layer.
- **View ▸ Compare:** **Swipe** two layers with a line across the map; **Side by side**: the 2D map and a 3D map moving
  together.
- **File ▸ Export map:** the view as a **picture** (PNG, 2D or 3D) or a **print layout** (title, legend, scale bar, north
  arrow, date, credits) as PNG or printed / PDF.
- **3D maps:** ViewCube with 26 clickable faces, edges and corners; AutoCAD navigation bar (pan, zoom, orbit, continuous
  orbit) and WCS label; **extrude polygons** by an attribute; files dropped on a 3D map go into it.
- **Contents:** 2D data, **3D data** (DEMs; 3D maps only, a DEM in a 2D map is marked ⚠) and tabular data; no Add data /
  Workspace buttons there (they are in Insert and File).
- **Notifications** (the bell in the status bar): the last 60 messages, failures with the technical details.
- **Friendlier errors:** what happened → what to do, for the usual technical messages.
- **Crash recovery:** after an unexpected close, "Restore your last session?"; File ▸ Recover last session.
- **First-run tour** (Help ▸ Tour); the ribbon's ticks and highlights follow every change.
- **Ribbon, as in Word:** the menu bar is now File · Insert · Analysis · History · View · Help, each tab's commands shown
  as icon buttons in groups under the tabs; pinned by default (double-click a tab, the ⌃ button or Ctrl+F1 to hide it).
- **Insert ▸ 2D / 3D: several maps**, each a tab under the ribbon with its own Contents; rename (double-click the tab),
  duplicate, close; copy layers between maps with Ctrl+C / Ctrl+V.
- **3D maps** (three.js, bundled): a DEM becomes the land (auto height exaggeration), imagery and vectors are draped on
  it, the basemap lies under it; AutoCAD's viewport controls (view, visual style: Realistic / Shaded / Wireframe), ViewCube
  and UCS icon; the mouse as in AutoCAD (wheel zoom, middle-drag pan, Shift + middle-drag orbit, double-click the wheel to
  zoom extents); the height under the cursor in the status bar. Server: `/api/rasters/grid`.
- **Insert ▸ Bookmarks:** save the map's view (Ctrl+B), shown as thumbnails; click to go back, rename, remove.
- **Insert ▸ Library** and **Add data** are in the Insert tab (the Library and Bookmarks tabs are gone).
- **About** (click the logo): version, desktop app or source (git branch and commit), computer, Python and library
  versions, add-ons, accounts, folders; Copy details for bug reports; Check for updates.
- **Tools ▸ Interpolation:** IDW, kriging (with standard error), thin-plate spline, natural neighbour, nearest neighbour, trend
  surface and TIN; cut to any boundary; leave-one-out comparison of the methods; India's AQI colour scale.
- **Live AQI** from CPCB's public feed (`lulc_fetch.aqi`), e.g. Bengaluru's stations.
- Downloads & jobs no longer adds a second copy of tools' results to Contents (only Find imagery downloads are added there).
- **Library (now Insert ▸ Library):** ready-made GIS data from Hugging Face. The first dataset, *Indian shapefiles*, has 307 layers of India
  (states, districts, sub-districts, villages, constituencies, city wards, PIN-code areas, highways, railways…); every
  dataset uploaded to the library account appears too. Search, then add to the map; each file is downloaded once.
- **+ Add data:** a CSV / Excel / Parquet table with latitude / longitude columns also goes on the map as points; the
  columns are found by name and value, or chosen in a dialog.
- **Data viewer:** right-click a column (sort, statistics, rename, calculate, convert, delete) or a cell (copy, filter by
  this value, show on the map, edit, delete the row).
- Right-click the map: copy coordinates (lat / lon / UTM), what's here, add a point, start tools there.

## 0.0.2 beta (2026-10-04)

**New menus and tools**
- **Agri menu:** *Diagnose crop disease* (leaf photos of 42 crops: the crop is recognised, then its model gives the top 3 diseases; unclear photos are refused; photos with GPS become a map; a wrong crop can be corrected per photo) and *Crop disease guide* (symptoms, treatment and pests from about 9,000 expert answers). The models download from Hugging Face the first time they're needed.
- **Embeddings menu:** *Download embeddings* (Google AlphaEarth 64-D and TESSERA 128-D, 10 m, 2017–2025, free, any area), *Train embedding model* and *Classify with embedding model* (eleven light segmentation models, including TinyUNet, using every band), *Convert embeddings* (8-bit ↔ 16 / 32-bit float) and *Explore embeddings* (similar places, colour view).
- **Object detection:** *Detect object* (YOLO26, Faster R-CNN, RetinaNet, Mask R-CNN, SAM 2.1 and your own models) and *Train detection model* (YOLO26 / YOLO11: boxes, outlines or rotated boxes).
- **History menu:** every tool run with its inputs, settings and outputs; *Run again* or *Change settings & run*.
- **Right-click the map:** copy coordinates (lat / lon / UTM), *What's here?*, add a point, start Find imagery or Download embeddings there, find similar places, open the place in Google Maps or OpenStreetMap.

**Better everywhere**
- Run details in a floating window (every step, time taken, why a run failed; failures also go to logs/errors.log).
- Large embeddings open in seconds instead of minutes; Contents says when a file is being opened or loaded, and why a layer can't load (with Try again / Remove).
- Clicking a pixel lists every band (it scrolls), with Copy values.
- Cleaner panels: long explanations behind ⓘ, no step numbers, no star ratings; Classical ML's tools are tabs inside it.
- Sizes that fit small images (training patches, detection tiles), a time estimate before detection runs, a note when a model was still improving at its last epoch, and one-click fixes when a search finds nothing or an embedding year has no data.
- "Also save to a folder on my computer" for the new tools.

**Fixes**
- PSPNet now trains on Apple GPUs; SVM, SGD and neural-network regression fit targets of any size; TESSERA downloads survive dropped connections; Index analysis results panel; History ▸ Run again adds every result to Contents.

**Under the hood:** every tool in its own files (server: webapp/routes/, browser: webapp/static/app/ and webapp/static/tools/), with the shared parts in one place; 231 automated tests plus a real-data suite.

## 0.0.1 beta (2026-10-02)

First release: Find imagery (Sentinel-2 / Landsat search, previews, downloads, composites, land-cover labels), Index analysis, PCA, Stack layers, Raster → table, Classical ML (tables and rasters), Make training data, Train classify model and Classify image, Export, projects.
