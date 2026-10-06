# Changelog

## Unreleased

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
