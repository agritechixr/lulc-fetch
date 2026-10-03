# LULC Fetch: User Guide

This guide explains every part of LULC Fetch, the land-use / land-cover (LULC) toolkit, in the order you would normally use it: get imagery → prepare it → collect training data → train a model → make and export a map.

**Contents**

1. [Installing and starting](#1-installing-and-starting)
2. [The workspace](#2-the-workspace)
3. [Contents panel: 2D data and tabular data](#3-contents-panel-2d-data-and-tabular-data)
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
14. [Classical ML: Clustering (unsupervised)](#14-classical-ml-clustering-unsupervised)
15. [Classical ML: t-SNE map (unsupervised)](#15-classical-ml-t-sne-map-unsupervised)
16. [Classical ML for raster](#16-classical-ml-for-raster)
17. [Make training data](#17-make-training-data)
18. [Train classify model](#18-train-classify-model)
19. [Classify image](#19-classify-image)
20. [Detect object](#20-detect-object)
21. [Train detection model](#21-train-detection-model)
22. [Agri: Diagnose crop disease](#22-agri-diagnose-crop-disease)
23. [Agri: Crop disease guide](#23-agri-crop-disease-guide)
24. [Satellite embeddings](#24-satellite-embeddings)
25. [Export data](#25-export-data)
26. [Downloads & jobs](#26-downloads--jobs)
27. [Credentials](#27-credentials)
28. [Command-line tool](#28-command-line-tool)
29. [Data sources and band conventions](#29-data-sources-and-band-conventions)
30. [Files and folders](#30-files-and-folders)
31. [Limits and known issues](#31-limits-and-known-issues)
32. [Troubleshooting](#32-troubleshooting)
33. [For developers: adding a tool](#33-for-developers-adding-a-tool)

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

The app runs entirely on your computer. Searching and downloading uses free public catalogues with no login. Accounts are only needed for Copernicus and USGS original-product downloads (see [Credentials](#27-credentials)).

---

### Desktop app (Mac and Windows)
Ready-made apps for Mac (Apple Silicon, macOS 14+) and Windows are on the GitHub **Releases** page; see the README for download and first-start steps. They're built automatically by the *Build desktop apps* workflow whenever a version tag (e.g. `v0.2.0`) is pushed. On Windows, extract the zip and run **LULC Fetch.exe** (build it yourself with `packaging\build_windows.ps1`).

On a Mac, `./packaging/build_mac.sh` builds **`dist/LULC Fetch.app`** and a disk image **`dist/LULC-Fetch.dmg`**. Python and every library are bundled inside (about 370 MB), so the target Mac needs nothing else installed.

**Install:** open the `.dmg` and drag LULC Fetch to Applications.
- An app built on your own Mac opens normally.
- A copy downloaded from elsewhere needs right-click ▸ **Open** the first time, because it isn't signed with an Apple developer ID.

**Start:** double-click the app. It starts the local server, opens LULC Fetch in your default browser, and shows a small window: **Open LULC Fetch** · **Data folder** · **Quit**.
- Closing that window, or pressing Cmd+Q, stops the app.
- Clicking the Dock icon reopens the page.
- Starting it again while it's running just reopens the page.

**Your files** are in `~/Documents/LULC Fetch` (downloads, tables, models, uploads, logs …). Put `.SAFE` products in `~/Documents/LULC Fetch/data`. Projects can live in any folder. The log is `logs/app.log`.

**Offline use:** all tools that work on your own files run without internet: opening products, indices, PCA, stacking, Raster → table, every ML tool, editing, Python, projects and export. These need internet:
- Find imagery and land-cover downloads;
- basemap tiles (offline, choose View ▸ Basemap ▸ No basemap);
- address search.

To use a different data folder, start the app with the `LULC_HOME` environment variable set; `LULC_PORT` changes the port (default 8765).

**Rebuild** after changing the code: `./packaging/build_mac.sh`. The app is built for the Mac it's built on (Apple Silicon or Intel).

## 2. The workspace

The window is laid out like a desktop GIS (QGIS / ArcGIS):

```
┌ File  Tools ▾  View  Help ─────────────────────────── Credentials ┐
│ Contents        ┆                                ┆ Tool panel     │
│ ▾ 2D DATA     3 ┆              map               ┆ (opens when    │
│ ☑ NDVI · scene  ┆                                ┆  you choose a  │
│ ☑ AOI           ┆                                ┆  tool)         │
│ ▣ photo.jpg     ┆┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┆                │
│ ▾ TABULAR DATA 1┆ Data viewer: table · attributes┆                │
│ ▦ samples.csv   ┆ # │ B02 │ B03 │ … │ label        ┆                │
├─────────────────┴────────────────────────────────┴────────────────┤
│ Ready          Lat 12.96501  Lon 77.58500   Scale 1 : 25,000  z 14 │
└────────────────────────────────────────────────────────────────────┘
```

**Resize any panel** by dragging its edge (the dotted lines above): Contents, the tool panel, and the data viewer. Double-click an edge to reset it, or use View ▸ Reset panel sizes.

### Menu bar
| Menu | What's in it |
|---|---|
| **File** | New project · Open project · Close project · Show project folder · Add data from computer · Add GeoTIFF from workspace · Open Sentinel product (.SAFE) · Export / Properties / Remove the selected layer · Remove all layers · Clean up working files · Credentials |
| **Tools ▾** | Find imagery · Index analysis · PCA & dimensionality reduction · Training samples · Stack layers · Raster → table · Classical ML (supervised: ↳ Train a model, ↳ Classify an image · unsupervised: ↳ Clustering, ↳ t-SNE map) · Export data · Downloads & jobs · Satellite embeddings |
| **Agri ▾** | Diagnose crop disease · Crop disease guide · shortcuts to Index analysis (crop health: NDVI, EVI…), Satellite embeddings and Find imagery |
| **View** | Show/hide Contents, Tool panel and Data viewer · Reset panel sizes · Basemap (Streets, Satellite, Topographic, None) · Place labels on top · Zoom to all layers · Theme (system / light / dark) |
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
| Ctrl/⌘ 1 / 2 / 3 | Show / hide Contents / Tool panel / Data viewer |
| Esc | Close menus, stop drawing, close hints |

### Projects
A **project** is a folder that keeps everything for one piece of work together: your Contents (layers, tables, pictures), the map view and basemap, and every result the tools make (downloads, index and PCA results, stacks, tables, models, maps).

- **When the app starts** you choose:
  - **New project:** give a name and a location, using **Browse…** to pick a folder. A folder with the project's name is created there, holding `lulc_project.json` and the working folders.
  - **Open a project:** pick a folder marked *project*.
  - **Recent projects:** click one to reopen it.
  - **Continue without a project:** a *temporary workspace*. Results are kept in the app's own folder, as a cache, until you clean them up.
  - Untick *Show this window when the app starts* to skip it next time. File ▸ New project / Open project brings it back.
- **The chip in the top-right corner** shows the open project; ✓ means everything is saved. Click it to show the project folder in Finder / Explorer, save now, switch to another project, close the project or clean up working files.
- **Autosave:** Contents and the map view are saved into the project about a second after every change. Reopening the project, or simply reloading the page, brings everything back.
- Your Sentinel products in the app's `data/` folder are still listed when a project is open. Switching projects is blocked while a job is running.

### Saving results to your computer
Every result is always kept in the project (or the temporary workspace) and appears in Contents. To also save a copy somewhere else:

- **In every tool:** tick **Also save to a folder on my computer** above the Run button and choose a folder (type it, or use **Browse…**). When the run finishes, a copy of the result is saved there, and the tool shows the path with a *Show in folder* link. The folder is remembered per tool.

  | Tool | What is copied |
  |---|---|
  | Find imagery | the downloaded files |
  | Index analysis export | the exported GeoTIFF |
  | PCA, Stack layers, Classify an image | the result GeoTIFF |
  | Raster → table, Clustering, t-SNE | the table (with its description); clustering also copies the saved model |
  | Train a model | the model with its JSON and HTML evaluation report |
- **Right-click a layer, table or picture ▸ Save to folder…**
  - **Rasters** are saved as GeoTIFF. Index or formula layers, such as an NDVI result, are saved as their computed values. A Sentinel-2 product opened from `.SAFE` is written out as a real GeoTIFF.
  - **Vector layers** are saved as a shapefile (.shp, .shx, .dbf, .prj and .cpg).
  - **Tables and pictures** are copied.
- **Export data** has **Save to a folder instead of downloading**.
- Files are never overwritten: `_2`, `_3` … is added to the name.

**The folder picker** (Browse…) shows shortcuts to the project, Home, Desktop, Documents, Downloads and the app folder. You can type a path and press Enter, go up a folder, create a **New folder**, double-click to open a folder, and click to select one.

**Clean up working files** (File menu, or the project chip) lists the size of the working folders and deletes files older than 1, 7 or 30 days, or all of them. Whole download / result folders are removed together, and running jobs are skipped.

In the temporary workspace, the app remembers your layers, map position, basemap, theme and panel layout in your browser. In a project, they are saved in the project file instead.

---

## 3. Contents panel: 2D data and tabular data

Everything you work with is listed in **Contents**, on the left, in two sections. Click a section header to collapse or expand it; the number shows how many items it holds.

| Section | What's in it |
|---|---|
| **2D data** | Map layers: rasters (GeoTIFF), **RGB images** (aerial / drone photos, georeferenced JPG / PNG), vectors (shapefile, GeoJSON, KML), and plain **pictures** without coordinates |
| **Tabular data** | Tables: CSV, TSV / TXT, **Excel (.xlsx)** and Parquet, including the tables made by Raster → table |

### Adding data
- **+ Add data**, File ▸ Add data, or **drag files onto the map**. Each file goes to the right section automatically:
  - **GeoTIFF**: true-colour (RGB) GeoTIFFs are shown in their natural colours.
  - **JPG / PNG / BMP / GIF / WebP pictures:**
    - With a **world file** (`.jgw`, `.pgw`, `.wld` …), optionally a `.prj`: select the files together. The picture is converted to a GeoTIFF and placed on the map. Without a `.prj`, longitude / latitude (WGS 84) is assumed, and you're told so.
    - Without georeferencing (an ordinary photo): it's listed under 2D data as a *picture*, marked *not on map*, and opens in the data viewer. **Place on map** stretches it over the current map view. Zoom the map to the right area first. It then becomes a GeoTIFF layer.
  - **Shapefile** (`.zip`, or `.shp` + `.shx` + `.dbf` + `.prj` selected together; other projections are converted), **GeoJSON**, **KML / KMZ**.
  - **Tables:** CSV, TSV / TXT (the comma, tab, semicolon or `|` separator is detected), Excel `.xlsx` (first sheet; the first row holds the column names) and Parquet. Tables are stored in `tables/`, so **every tool can use them**, e.g. Train a model.
- **Workspace:** GeoTIFFs and tables already on your computer from earlier downloads, results, imports and command-line output.
- Tools add their results automatically: downloads, index results, PCA, stacks, classified maps, Raster → table tables, your area of interest, scene footprints and previews.

### Tables in Contents
Double-click a table, or click its ▭ button, to open it in the data viewer. Right-click or **⋯** for: Open · Column statistics · **Show points on map** (for tables with `lon` / `lat` columns) · Train a model with this table · Download · Remove from Contents. Removing it from Contents keeps the file.

### Working with layers
| Action | How |
|---|---|
| Show / hide | Tick box |
| Reorder | Drag a layer up or down (top = drawn on top) |
| Zoom to layer | 🔍 button, double-click, or right-click ▸ Zoom to layer |
| Legend + opacity | ▶ arrow on the left of the layer |
| Pixel values | Select a raster layer, then click the map. A popup shows the value, class name or band values. |
| Attribute table | Right-click a vector layer ▸ **Open attribute table**: opens in the data viewer |
| More options | Right-click or **⋯**: Zoom · Properties · **Band combination (RGB)** · **Metadata** · Compute indices · Open attribute table (vector layers) · Use as area of interest (polygon layers) · Export / save to computer · Move to top/bottom · Remove |

### Layer properties
Right-click ▸ **Properties** lets you rename a layer, change its opacity, and choose how a raster is **displayed**:
- a **band combination** (true colour, false colour, SWIR, agriculture, urban, radar RGB)
- an **RGB of any three bands** (e.g. PC2 / PC3 / PC1)
- a **single band** with a colour scale and stretch (standard range, automatic 2–98 %, or custom min/max)

Class maps (e.g. WorldCover, classified maps) are drawn with their own class colours and names.

### Band combination (RGB)
Right-click a raster with two or more bands ▸ **Band combination (RGB)…** to choose which bands are shown as red, green and blue. The map updates as you choose.
- **Presets** are shown when the layer's bands are known, e.g. Sentinel-2, Landsat or a stack with band names:
  - True colour (R · G · B)
  - Colour infrared (NIR · R · G)
  - SWIR · NIR · Red
  - Agriculture (SWIR1 · NIR · Blue)
  - Healthy vegetation (NIR · SWIR1 · Blue)
  - Land / water (NIR · SWIR1 · Red)
  - Urban (SWIR2 · SWIR1 · Red)
  - Geology (SWIR2 · SWIR1 · Blue)
  - Atmospheric penetration
  - Red edge
  - Bathymetric
  - Radar (VV · VH · VV)
- **Choose the bands**: any band for Red, Green and Blue. The same band may be used twice.
- **Contrast**: stretch 2–98 % (default), 1–99 %, min–max, or none (photo colours).
- **Default** goes back to true colour. **Cancel** (or Esc) puts the previous look back; **Done** keeps the new one.

### Metadata
Right-click any layer ▸ **Metadata…**. **Copy as JSON** copies everything.
- **Rasters:**
  - the file, format, size, compression, tiles and overviews;
  - size in pixels, number of bands, data type and no-data value;
  - the coordinate system (name, EPSG, units), pixel size, area covered, and the extent in map units and longitude / latitude;
  - a table of every band: name, the band it is used as (e.g. B08 · NIR), type, no-data, min / max / mean / std / 2 % / 98 % and valid share from a quick overview, and its colour table;
  - the file's tags.
  - Sentinel products also show the satellite, level, date and time, tile, orbit and processing baseline.
- **Vector layers:** the number of features, geometry types, extent, and every attribute field with its type, number of filled and distinct values, and range or examples.

### Data viewer (under the map)
The data viewer opens below the map when you open a table, an attribute table or a picture. Each one gets its own **tab**, and *×* or a middle-click closes it. Drag the viewer's top edge to make it taller or shorter, use ⤢ to maximise it, or press Ctrl/⌘ 3 to show or hide it.

**Tables and attribute tables**
- **Paging:** 50 / 100 / 250 / 1000 rows per page, with « ‹ › ». Large tables stay fast because only one page is loaded at a time.
- **Sort:** click a column header (ascending → descending → off). The small tag shows the column type: `123` whole number, `1.5` decimal, `abc` text.
- **Search:** type to search every column, or filter one column with a comparison: `yield > 4`, `crop = rice`, `class != water`, `area_m2 <= 500`.
- **Rows on the map:** click a row. For a vector layer, the feature is highlighted in yellow and the map zooms to it. For a table with `lon` / `lat` columns, the point is marked. **Show on map** adds all matching rows as a point layer (up to 20,000, sampled).
- **Column statistics:** for every column, the type, missing values, distinct values, min / median / mean / max / standard deviation, and a mini histogram (numbers) or the most frequent values (text).
- **Train a model** opens Classical ML ▸ Train a model with the table selected. ⬇ downloads the file.

**Editing tables and attribute tables:** click **✎ Edit** in the viewer's toolbar. This starts an **edit session**: every change goes to a working copy, and the original table or layer is not touched until you save.

| Action | How |
|---|---|
| Add a field | **+ Add field**: a name, an optional type and an optional expression. Leave the expression empty for an empty field you fill in by hand. |
| Calculate values | **ƒx Field calculator**: create a new field or update an existing one with an expression. Tick *Only the rows matching the current search* to change just the filtered rows. A live preview shows the first results and the type. |
| Python | **🐍 Python**: change the data with pandas (see below) |
| Edit a cell | Double-click it, type, then Enter (moves down), Tab (moves right) or Esc (cancel) |
| Field options | **⋯** on a column header: Calculate values · Rename · Convert to decimal number / whole number / text · Delete field |
| Delete rows / features | Tick the rows (or tick the header box for the whole page), then **Delete selected** |
| Add a row | **+ Add row** (tables) |
| Undo | **↶ Undo**: steps back through the changes of this session |
| Copy the filtered rows | **New table / New layer from filtered rows** saves the rows matching the search as a new table or vector layer |

The edit bar counts the **unsaved changes**.

**Saving: 💾 Save…** (or ✎ Edit again, or closing the tab) opens the *Save changes* dialog. It lists every change made in the session and warns before anything is overwritten. You choose:
- **Overwrite** the existing table or layer.
  - Tables: tools that use the table (Train a model, Clustering …) see the new version. The previous version is kept: right-click the table ▸ **Restore previous version**.
  - Vector layers: the layer in Contents (and in the project) is changed, and this can't be undone afterwards. The original file on your computer, e.g. the shapefile you added, is never changed. Use right-click ▸ *Save to folder…* to write a new file.
- **Save as new**: a new table or layer, with the name you give. The original stays exactly as it was.
- **Discard changes**: the original stays as it was.
- **Keep editing.**

If you don't save, nothing is applied. Reloading the page brings back the original vector layer. An unsaved table session is kept, and offered again (save or discard) the next time you click ✎ Edit.

**Python (🐍):** write Python with the data as a pandas DataFrame called `df`.

```python
df["tons"] = df["yield"] * 2.5                                   # a calculated column
df["class"] = np.where(df["yield"] >= 3, "high", "low")         # groups
df = df[df["crop"] != "rice"]                                    # keep only some rows
df = df.rename(columns={"lon": "longitude"})                    # rename
df["yield"] = df["yield"].fillna(df["yield"].median())          # fill missing values
print(df.groupby("crop")["yield"].agg(["count", "mean"]))       # see results
```

- Available names: `pd`, `np`, `math`, `re` and `datetime`.
- For vector layers, `df["geometry"]` holds the shapes (read-only), with helpers `area_m2(g)`, `area_ha(g)`, `perimeter_m(g)`, `length_m(g)` and `centroid_xy(g)`, measured in the local UTM zone. Rows you drop delete those features.
- **Insert an example…** offers ready-made snippets that use your column names.
- **▶ Test** runs the script and shows the printed output, a summary (rows before → after, columns added / removed) and a preview, without changing anything.
- **Run & apply** changes the working copy. It is one change in the session, so it can be undone, and like everything else it reaches the original only when you save.
- Scripts run on your computer in a separate process. Cancel stops them, and there's a 10-minute limit. Errors show only your script's line and the message.

**Expressions** (field calculator)
- Field names go in square brackets, text in quotes: `[Production] / [Area]`, `iif([Yield] > 2, 'high', 'low')`, `concat([State], ' - ', [District])`, `[crop] = 'rice' and [yield] > 3`.
- Operators: `+ - * / ** %`, comparisons (`=` or `==`, `!=`, `<`, `<=`, `>`, `>=`), `and`, `or`, `not`.
- Functions: `abs round sqrt log log10 exp floor ceil min max clip iif coalesce isnull upper lower title strip len concat replace substr contains startswith endswith text number integer`.
- Vector layers also have geometry values: `$area` (m²), `$area_ha`, `$area_km2`, `$perimeter` (m), `$length` (m), `$x` / `$y` (centroid longitude / latitude) and `$id`.
- Expressions are checked safely: only these operators and functions run.

Renamed or deleted band / label columns are updated in a table's description, so the ML tools keep working. CSV files don't store column types (a number converted to text is read back as a number); use Parquet if types matter.

**Pictures:** scroll to zoom and drag to pan. *Fit* and *1:1* reset the view. **Place on map** works as described above.

---

## 4. Things every tool shares

### Choosing a model or method
Wherever you choose a model or method (Train a model, Clustering, Classical ML for raster, PCA, Deep learning), a dropdown lists one row per option with its accuracy / speed stars. Click **ⓘ** on a row to read what it does; it doesn't select the row. Every setting also has an ⓘ with its explanation.

### Finding a tool
The **Tools** menu and the Start page list the tools in alphabetical order. The Classical ML sub-tools are listed A–Z under *Classical ML (tabular data)*. To read what a tool does, click the 👁 **eye** next to it (click again to hide it), or hover over the eye. The open tool has the same eye next to its title in the tool panel.

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

Sentinel-1 GRD and Sentinel-2 L1C / L2A products from Copernicus can be added in three ways. Each works with an extracted `.SAFE` folder or a `.SAFE.zip`.

- **Drag and drop:** drop the `.SAFE` folder or `.SAFE.zip` anywhere on the window. You can also pick the zip with **+ Add data**.
  - It is copied into the project's `data/` folder.
  - From a folder, only the files LULC Fetch reads are copied (Sentinel-2: metadata and the finest file of each band, about 60 % of the product; Sentinel-1: measurement and annotation files).
  - Files that are already there are skipped.
  - Sentinel-2 then opens right away; for Sentinel-1 the backscatter options appear.
- **Browse… (no copying):** **File ▸ Open Sentinel product (.SAFE) ▸ Browse…**. Choose a `.SAFE` folder or `.SAFE.zip` anywhere on your computer; they are marked *Sentinel product*. Choosing a folder that holds several products opens all of them.
  - The product is read where it is and stays in the list (**remove from list** forgets it; nothing is deleted).
  - This is the quickest way for large products.
- **Data folder:** copy products into `data/` yourself.

All products are listed under **File ▸ Open Sentinel product (.SAFE)** and on the Start page.

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

1. **Training table:** any table from Raster → table (or any CSV / Parquet file in `tables/`).
2. **Target (what to predict):**
   - **Target column** (the dependent variable): preselected as the ground-truth column.
   - **What kind of values does it hold?** Choose **Categories** (classification: land-cover classes, crop types, class codes 1, 2, 3…) or **Numbers on a scale** (regression: biomass, yield, height, an index). A 💡 suggestion explains the guess. For example, text, or only a few distinct whole numbers, means categories. You're warned if your choice doesn't fit the data, such as text treated as numbers.
   - **Class distribution:** shown, with a suggestion to use class balancing if the classes are very unequal.
3. **Input columns:** a table with every column of the file. Beside each column name:
   - **Role:** *Feature* (model input), *Ignore* (left out) or *Target*. Choosing *Target* on a row makes that column the target.
   - **Type:** *Numeric* (a measured value: bands, indices, elevation) or *Categorical* (labels or codes with no order: soil type, zone code, orbit). Categorical columns are **one-hot encoded** automatically. Text columns are always categorical.
   - Each row shows the detected type, the number of distinct values, missing values and example values. Whole-number columns with only a few values are flagged *looks categorical?*.
   - Coordinates, ids (`poly_id`, `sample_id`) and text columns are ignored by default. Use the filter box and the **Bands only / All / None** buttons for quick selection.
   - Text features work for training and evaluation, but an image can't provide them, so such a model can't classify a raster. You're told so. Numeric codes (e.g. a zone band) work fine.
4. **Preprocessing:** how the data is cleaned and transformed before it reaches the model. A **Pipeline** line shows the steps in order, e.g. *Fill missing → Remove constant columns → Clip 1–99% → Yeo-Johnson (skewed) → Robust scale → One-hot → SVM*. **Defaults** resets the card.

   | Option | Choices | When it helps |
   |---|---|---|
   | **Missing values** | *Drop those rows* (default) · *Fill in*: median for numbers, a separate "(missing)" category for categorical columns | Fill in when many rows have a gap. Classify an image then also fills pixels where a band is missing instead of leaving them blank. |
   | **Remove constant columns** | on (default) | Drops columns with the same value in every training row |
   | **Remove near-duplicate columns** | off (default) · \|r\| ≥ 0.99 / 0.95 / 0.90 | Keeps one column of each almost perfectly correlated group (e.g. B8 and B8A). Helps linear models, k-NN and Maximum Likelihood. |
   | **Outliers** | keep (default) · *clip to percentiles* (1 = 1st–99th) | Saturated pixels, cloud or shadow remnants, sensor spikes |
   | **Skewed features** | leave (default) · Yeo-Johnson on skewed columns (\|skew\| > 1) · on all numeric columns | Radar backscatter in linear units, texture, distances. For linear models, SVM, k-NN, MLP, Naive Bayes and Maximum Likelihood; trees don't need it. |
   | **Feature scaling** | *Auto* (default: standard scaling for SVM, SGD, logistic regression, k-NN and MLP; none for trees and probabilistic models) · Standard (z-score) · Min–max (0–1) · Robust (median / IQR) · None | Robust is least affected by outliers |
   | **Reduce bands (PCA)** | no (default) · auto (only above 30 bands) · 10 / 20 / 30 / 50 components | Hyperspectral data or embeddings with Maximum Likelihood, LDA, k-NN, SVM |
   | **Target transform** *(regression only)* | none (default) · log(1 + y) · Yeo-Johnson | Skewed targets such as biomass, yield or counts. Predictions are converted back automatically. |

   Every step is learned from the **training rows only** and refitted inside each cross-validation and tuning fold, so no information leaks from the test split. The steps are saved inside the model, so Classify an image repeats them exactly. The results and the evaluation report list the steps that were applied, and which columns were removed and why.
5. **Model:** a dropdown with one row per model, grouped by family, showing typical accuracy / speed ratings. The ⓘ on each row explains the model.

| Family | Models |
|---|---|
| Trees | **Random Forest** *(recommended)*, Extra Trees, Decision Tree |
| Boosting | XGBoost, LightGBM, Histogram Gradient Boosting |
| Kernel | SVM (RBF, linear or polynomial kernel) |
| Linear | SGD (log-loss or linear SVM), Logistic Regression |
| Probabilistic | Naive Bayes, **Maximum Likelihood** (classic remote-sensing Gaussian classifier), Linear Discriminant |
| Distance | **Minimum Distance** (nearest class mean), **Spectral Angle Mapper** (spectral shape / cosine; hyperspectral and embeddings) |
| Neighbours / Neural | k-Nearest Neighbours (Euclidean, cosine or Manhattan distance), neural network (MLP) |

6. **Parameters:** 2–4 key settings per model, with defaults tuned for pixel data. The rest is under *Advanced*:
   - validation split, test share, class balancing
   - cross-validation folds
   - training-row cap (large tables are sampled down per model), random seed
7. **Hyperparameter tuning** (switch on with *Tune*): the app tries many parameter combinations and keeps the best.
   - **Random search** (tries a set number of random combinations, 20 by default) or **Grid search** (every combination, max 300).
   - **CV folds:** each combination is trained on all folds but one and scored on the one left out. Folds use the **same polygon / spatial-block grouping** as the validation split, and only the training rows, so the test split stays untouched and the result stays honest.
   - **Optimise for:** accuracy, macro F1, balanced accuracy or kappa (classification); R², RMSE or MAE (regression).
   - **Values to try:** comma-separated candidates for each parameter, prefilled with sensible ranges per model, e.g. trees `100, 300, 600` and depth `None, 10, 20, 40`. Untick a parameter to keep its value from *Parameters*. `None` means unlimited.
   - The panel shows the total number of fits (tries × folds). Progress and **Cancel** work during the search.
8. **Train model** (or **Tune & train model**), or **Compare models**: trains every suitable model with default settings on the same split (max 20,000 training rows each) and shows a ranked **leaderboard** (accuracy and kappa, or R² and RMSE, plus time). Click **Use** to select a model, then adjust or tune it and train it fully.

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
- **Tuning** (if used): the best cross-validation score with the chosen parameters, plus a table of the top combinations.
- Categorical columns used, and how many rows were dropped or filled in.

Random Forest, Extra Trees, XGBoost and LightGBM report real progress while training, so **Cancel** works mid-training. Models are saved in `models/` as `.joblib` files (a scikit-learn pipeline), with a JSON report. In the hub you can **Use**, view the **Report**, open the **📊 evaluation report**, download or delete each model.

### Evaluation report (HTML)
Training also writes `models/<name>.evaluation.html`. It is one self-contained page that opens offline in any browser, prints to PDF, and can be emailed. Open it with **📊 Evaluation report ▸ Open** in the results, or download it with **⬇ .html**.

**Save it in your own folder:** type a folder in **Save evaluation report to folder** (next to *Model name*) before training, e.g. `~/Documents/LULC reports` or `/Users/you/Projects/reports`. A copy is saved there after training, and the result shows the full path. The folder is created if needed, existing files are never overwritten (`_2` is added to the name), and the app remembers the folder for next time. For a model you've already trained, use **Save a copy** in the evaluation box, or open its *Report* in the model library. The original always stays in `models/`. Hover over any chart or cell for exact values. Every chart has a one-line *how to read it* note. It works for every model.

| Classification | Regression |
|---|---|
| Score tiles: accuracy, kappa, macro / weighted F1, balanced accuracy, MCC, ROC AUC, log loss, top-2 accuracy, calibration error | Score tiles: R², adjusted R², RMSE, MAE, median error, bias, Pearson r, NRMSE, MAPE |
| Confusion matrix in counts and in row %, with producer's / user's accuracy | Predicted vs true, with the 1:1 line and the best-fit line |
| Per-class precision / recall / F1 chart, plus a table with omission and commission errors | Residuals vs predicted |
| Most-confused class pairs | Residual histogram and Q–Q plot |
| Class distribution: training vs test true vs test predicted | Error and bias by true-value range |
| ROC and precision–recall curves per class* | |
| Confidence histogram (correct vs wrong) and reliability (calibration) diagram* | |

\*Needs class probabilities. These charts are skipped for SVM with *probability* off and for SGD with hinge loss.

Both kinds of report also include feature importance, cross-validation folds, tuning results (if used), the validation split, and all model and data settings.

A compact sample of the test predictions (up to 20,000 rows) is stored inside the model file, so the report can be rebuilt at any time. A saved model can also be **tested on another labelled table**, for example a different area or date, as an independent check:

```bash
python -m lulc_fetch.evaluation models/rf.joblib                                   # rebuild the report
python -m lulc_fetch.evaluation models/rf.joblib --table tables/other_area.csv -o other.html
```

---

## 13. Classical ML: Classify an image

A clustering saved as a model (section 14) works here too: it makes an **unsupervised land-cover map**, one class per cluster.

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

## 14. Classical ML: Clustering (unsupervised)

**Tools ▸ Classical ML ▸ Clustering.** The Classical ML hub has two groups:
- **Supervised learning:** you have labels, and a model learns to predict them (*Train a model*, *Classify an image*).
- **Unsupervised learning:** no labels needed, the tools find structure in the data (*Clustering*, *t-SNE map*).

Clustering splits the rows of a table into groups of similar rows, without any ground truth. Use it to:
- find natural classes in an image (unsupervised land-cover classification);
- segment fields, farms or districts;
- explore a new dataset before labelling it.

1. **Table:** any table in Contents or `tables/`.
2. **Columns to cluster on:** tick **Use** for each column that describes the rows. Tick **Categorical** for codes and categories, which are one-hot encoded; text columns are always categorical. Ids, coordinates and label columns are left out by default. **Compare with a known label** (optional) is only used to check the result, never to build the clusters.
3. **Method**

   | Method | Number of clusters | Good for | Fitted on |
   |---|---|---|---|
   | **K-means** *(recommended)* | you choose k | compact, similar-sized groups; images. Mini-batch K-means is used automatically above 100,000 rows. | up to 1,000,000 rows |
   | **Hierarchical** | you choose k, or a cut height | seeing how groups nest (dendrogram). Linkage: Ward, average, complete, single. | 5,000-row sample |
   | **DBSCAN** | found automatically | any shape; marks outliers as **noise**. *eps* is suggested from the k-distance curve. | 30,000-row sample |
   | **HDBSCAN** | found automatically | groups of different density; no eps to tune | 30,000-row sample |
   | **Spectral clustering** | you choose k | non-round groups (rings, bands, elongated shapes) | 4,000-row sample |
   | **Gaussian mixture** | you choose k | elliptical groups; gives each row a **probability** (`cluster_probability`) | up to 300,000 rows |

   When a method is fitted on a sample, the other rows get the cluster of their nearest clustered rows. K-means and Gaussian mixture use their own rule instead (nearest centre, most likely component).
4. **Settings:** the method's parameters.
   - **Find the best number of clusters:** tries k = 2 … *max* on a sample and uses the best silhouette score. It shows the silhouette curve, plus the elbow (inertia) for K-means or BIC for Gaussian mixture.
   - **Save as a model (to cluster an image):** on by default for tables made from a raster.
5. **Preprocessing:** missing values, outlier clipping, skew transform and **scaling**. Standard scaling is the default, because clustering compares distances between rows.

**Results**
- **Quality:**
  - **Silhouette:** −1 to 1. Above 0.5 means clear clusters, 0.25–0.5 reasonable, below 0.25 overlapping.
  - **Davies-Bouldin:** lower is better.
  - **Calinski-Harabasz:** higher is better.
- **Warnings** explain weak results in plain words: only one cluster found, no clusters (everything noise), mostly noise, or overlapping groups.
- **Cluster sizes**, and a **2D view** (PCA) coloured by cluster. Hover over points; click a legend entry to hide a cluster.
- **Cluster profiles:** the mean of every column per cluster in its original units, coloured by how far it is above (red) or below (blue) the overall average. This is what makes each cluster different. Categorical columns show their most common value.
- **Clusters vs a known label** (if chosen): adjusted Rand index (0 = random, 1 = identical), NMI, homogeneity and completeness, and a cross-table with the most common label per cluster.
- **Dendrogram** (hierarchical) with the cut line. **k-distance curve** (DBSCAN) with the eps used.
- **Outputs:**
  - A new table `tables/<name>_<method>.csv`: the original table plus a **cluster** column. Values are 1 … k, 0 = noise, and empty = row skipped for missing values.
  - From there, **Open table**, make a **t-SNE map of these clusters**, **Train a model on the clusters** (turn the clusters into a supervised classifier), or **Cluster an image** with the saved model through Classify an image.

Example with the included `diabetes_risk.csv` (9 numeric health measurements, compared with `diabetes_risk`):
- K-means with *find best k* chooses k = 2 (silhouette 0.19).
- Gaussian mixture with k = 3 matches the risk groups best (ARI 0.29).
- DBSCAN finds one dense cloud, and HDBSCAN finds no dense groups.

The tool explains these results: this data has no well-separated natural groups.

## 15. Classical ML: t-SNE map (unsupervised)

**Tools ▸ Classical ML ▸ t-SNE map.** t-SNE draws every row as a point on a 2D map, so that rows that are similar in the chosen columns sit close together. Use it to see whether your classes or clusters separate, to spot sub-groups and mislabelled samples, and to check training data before you train a model.

1. **Table** and **columns to map**, chosen the same way as for clustering.
2. **Colour points by:** any column, such as the class label, a `cluster` column or a measurement. You can switch it after the run.
3. **Settings:**
   - **Perplexity** (5–50; larger shows more global structure).
   - **Rows:** a random sample, 5,000 by default; 5,000 rows take about 10–40 s.
   - **Iterations.** Advanced: learning rate (auto), early exaggeration, PCA or random initialisation, Euclidean / Manhattan / cosine distance.
   - With more than 50 columns, PCA reduces them to 50 first.
4. **Preprocessing:** as for clustering (standard scaling by default).

**Results**
- The map: hover a point for its values, and click legend entries to hide groups.
- **Trustworthiness:** how faithfully neighbours are kept, 0–1; above 0.9 is good.
- **KL divergence:** the final error.
- A progress bar driven by t-SNE's own iterations, so Cancel works mid-run.
- The mapped rows are saved as a table with **tsne_1** and **tsne_2** columns.

In t-SNE, the distances between far-apart groups and the sizes of groups don't mean anything. What matters is which points sit together.

## 16. Classical ML for raster

**Tools ▸ Classical ML for raster.** Train a classifier straight from an **image** and its **ground truth**, and get a classified map in one run. There is no table step: the labelled pixels are read from the raster for that run only.

It works with any number of bands:
- **RGB** (3)
- **multispectral** (Sentinel-2, Landsat: 4–30)
- **hyperspectral** (100+)
- **SAR**
- **pixel embeddings**, such as Google **AlphaEarth** Satellite Embeddings (64-D) or **TESSERA** (128-D)

1. **Image:** any raster layer.
   - The tool **detects the kind of data** (RGB, multispectral, hyperspectral, SAR or embedding) from the bands, data type and values. Embeddings are recognised by 64 / 128 dimensions with unit-length vectors, or 8-bit quantised values.
   - It explains what suits that kind of data. **Use recommended settings** picks the model, parameters and preprocessing.
   - **Bands:** use all of them, or type ranges such as `1-100, 120-180`, e.g. to drop noisy water-absorption bands of a hyperspectral image.
   - *Convert to reflectance* applies the layer's scale / offset. The same conversion is used for the map.
2. **Ground truth:**
   - Either polygons / points with a class attribute (shapefile, GeoJSON, KML, or drawn with **Training samples**), or a class raster such as a land-cover map.
   - The classes and the number of polygons per class are shown. You're warned about classes with only one polygon.
   - **Training pixels per class** (balanced): 1,000 to 30,000, or all labelled pixels.
   - Pixel size, and an optional **Area**. *Map the whole image* is on by default; untick it to map only the area.
3. **Model**

   | Model | Notes | Good for |
   |---|---|---|
   | Random Forest | robust default | all kinds |
   | **SVM** | kernel **RBF** (default) or **linear** (fast and strong with many bands / embeddings) or polynomial | all kinds |
   | **Maximum Likelihood** | the classic Gaussian classifier | multispectral, SAR, RGB; hyperspectral after PCA |
   | **Spectral Angle Mapper** | compares spectral *shape* (angle) and ignores brightness and shadow | hyperspectral, embeddings |
   | **Minimum Distance** | nearest class mean; very fast baseline | multispectral, RGB |
   | k-Nearest Neighbours | distance **Euclidean**, **cosine** (embeddings) or Manhattan | embeddings |
   | LightGBM, XGBoost, Extra Trees, Hist. Gradient Boosting | boosted / random trees | multispectral, hyperspectral |
   | Logistic Regression, Linear Discriminant, Naive Bayes, MLP | linear / probabilistic / small neural network | embeddings, hyperspectral |

   Models marked **★ SUGGESTED** suit the detected kind of data.
4. **Settings:**
   - The model's parameters.
   - **Preprocessing:** missing values, outliers, skew, scaling, and **Reduce bands (PCA)**. *Auto* reduces more than 30 bands to the components that keep 99 % of the variance (max 30). It is set automatically for Maximum Likelihood, LDA, Naive Bayes, k-NN, SVM and MLP on hyperspectral data.
   - Optional **quick tuning** (12 combinations, 3-fold grouped CV).
   - Advanced: validation split, class balancing, seed.
5. **Output:**
   - map name and map pixel size;
   - a **confidence layer** (band 2, 0–100 %);
   - **Also save to a folder on my computer**.

**Results**
- The classified map is added to Contents with class colours.
- Training pixels per class, and the full accuracy assessment, as in Train a model:
  - an honest split (by polygon for vector ground truth, spatial blocks for raster ground truth);
  - accuracy, kappa, F1, confusion matrix, per-class scores;
  - the HTML evaluation report.
- The model is saved under **Classical ML ▸ Your models**, so **Classify an image** can apply it to other images or dates with the same bands.

Recommended starting points:
- **Embeddings:** k-NN (cosine) or linear SVM, without scaling.
- **Hyperspectral:** SVM, SAM, or Maximum Likelihood with PCA.
- **Multispectral:** Random Forest, SVM or Maximum Likelihood.
- **RGB:** Random Forest or SVM.

## 17. Make training data

**Tools ▸ Make training data** cuts a large image, and its ground truth for the same area, into small matching patches that a deep-learning model can train on. You don't need to tile anything yourself.

1. **Input layers** (required). Tick one or more rasters, e.g. a Sentinel-2 scene, or Sentinel-2 + Sentinel-1 + a DEM.
   - Every ticked layer goes into every image patch, band by band.
   - **Reference grid**: the layer whose projection and pixel size the patches follow. The other layers are resampled onto it (nearest neighbour for class maps, bilinear for the rest).
2. **Ground truth** (optional). Either:
   - a class raster (GeoTIFF, e.g. a land-cover map), or
   - polygons / points (shapefile, GeoJSON, training samples) with a class attribute.

   With no ground truth, only image patches are made (e.g. for pre-training or prediction).
   - **Number classes 1, 2, 3…** (on by default): labels are stored as 1…K, and `classes.txt` lists the original value / name of each number. Untick it to keep the original values (e.g. WorldCover 10, 20, 30…).
   - **0 always means no label / ignore.**
   - **Skip patches with almost no labels**: useful with sparse polygons (on by default for vector ground truth).
3. **Patch size & overlap.**
   - **Patch width X / height Y** in metres (map units). Default: 256 × 256 pixels of the reference grid (2,560 m for 10 m Sentinel-2). The chips set 64 / 128 / 224 / 256 / 512 px.
   - The size in pixels is shown below the inputs (it is rounded to whole pixels).
   - **Overlap / padding X / Y**: 0 by default (patches touch edge to edge). **Half a patch** makes every place appear in up to 4 patches.
   - **Edge patches**: keep and pad with no-data (label 0) so the whole area is covered, or drop them.
   - **Minimum valid data**: skip patches that are mostly no-data.
   - **Area (ROI)**: only make patches inside a polygon, the map view, or a rectangle / polygon you draw. Pixels outside it become no-data.
   - **Preview patch grid on the map** draws the patch outlines and counts them.
4. **Output.** No train / validation split is made: the training tool decides that.
   - **Save in folder**: **Browse…** or type a path. If you leave it empty, the project's `training_data/` folder is used.
   - **Dataset name**: the folder that is created inside it. An existing, non-empty folder is never overwritten.

**What you get**

```
<folder>/<dataset name>/
  images/<name>_r0000_c0000.tif   image patches: all input bands, georeferenced GeoTIFF, band names kept
  labels/<name>_r0000_c0000.tif   label patches: same file names and grid, uint8 (uint16 if >255 classes), 0 = ignore
  classes.txt                     value · original value · name · colour · pixels · % · number of patches
  dataset.json                    patch size (px and m), overlap, bands, data type, no-data, CRS, classes
  patches.csv                     one row per patch: file, row / col, bounds, valid / labelled share, main class
```

- Float images keep no-data as NaN; integer images keep the source no-data value.
- The result lists the classes and their share. **Show patches on the map** draws the patch outlines. **Show in folder** opens the folder.
- Cancelling a run deletes the half-written dataset folder.

Reading a pair in Python:

```python
import rasterio
img = rasterio.open("images/my_dataset_r0003_c0007.tif").read()     # (bands, H, W)
lab = rasterio.open("labels/my_dataset_r0003_c0007.tif").read(1)    # (H, W), 0 = ignore
```

## 18. Train classify model

**Tools ▸ Train classify model** trains a semantic-segmentation network on a folder made with **Make training data**.

**The add-on.** The deep-learning tools use PyTorch and segmentation-models-pytorch (free, open source). The first time you open one of them, it offers **Install the deep-learning add-on**.
- It is installed once from the internet: about 0.8 GB on Mac.
- On Windows you choose **NVIDIA GPU (CUDA)**, about 3.5 GB, or **CPU only**, about 1.1 GB.
- It goes into the app's data folder (`addons/`), or into the Python environment when running from source. Everything else in LULC Fetch works without it.

**The YOLO & SAM add-on.** YOLO26 / YOLO11 and SAM 2.1 come from **ultralytics** (free, open source, **AGPL-3.0** licence). The tools that use them (YOLO26 semantic here, YOLO and SAM in **Detect object**, and **Train detection model**) offer **Install the YOLO & SAM add-on**: about 0.15 GB, installed once on top of the deep-learning add-on. LULC Fetch itself never ships ultralytics.

1. **Training data.** Pick a dataset; the project's `training_data/` folder and folders used before are listed, and **Browse…** opens any other.
   - The summary shows the number of patches, the patch size, the band count and each class's share.
   - It warns when there are very few patches.
2. **Model.** Choose an architecture from the dropdown (ⓘ on each row explains it) and a backbone.

   | Architecture | Notes |
   |---|---|
   | **U-Net** | Default. Sharp boundaries, works with little data |
   | U-Net++ | A little more accurate, slower |
   | **DeepLabV3+** / DeepLabV3 | Objects of many sizes |
   | **PSPNet** | Fast; large uniform regions |
   | FPN, LinkNet | Fast alternatives |
   | SegFormer | Transformer-style decoder |
   | **FCN** (torchvision) | Classic baseline; ResNet-50 / 101 |
   | **LR-ASPP** (torchvision) | Tiny and fast; MobileNetV3 |
   | **YOLO26 semantic** (ultralytics) | Very fast real-time network; size nano to extra large is chosen under *Backbone*. Pretrained on Cityscapes street scenes. Needs the YOLO & SAM add-on |

   - **Backbones:** MobileNetV2, MobileNetV3 large / small (fast), ResNet-18 / 34 / 50, EfficientNet-B0 / B2 / B4.
   - **ImageNet-pretrained** (on by default) downloads the backbone's weights once, 10–100 MB. They are adapted to any number of bands, so 3, 10, 16 or 64 bands all work. Without internet, training starts from random weights, and the report says so.
3. **Training.**
   - **Epochs** (50), **batch size** (8) and **learning rate** (0.001).
   - **Validation %** (20) and an optional **test %**. The test patches are scored once at the end with the best model.
   - **Split:**
     - **Spatial blocks** (default): neighbouring patches stay together. Training patches that overlap a validation or test patch are dropped, so no pixel is in two splits.
     - **Random:** optimistic when patches overlap.
   - **Early stopping** (on): **patience** (10 epochs), what to **watch** (validation mIoU or loss) and the **minimum improvement**. The best epoch's weights are kept.
   - **Advanced:**
     - **Loss:** cross-entropy + Dice (default), weighted cross-entropy, cross-entropy, Dice or focal. **Class weights** (auto) make rare classes count more.
     - Optimiser (AdamW, Adam, SGD), weight decay, learning-rate schedule (cosine, reduce-on-plateau, one-cycle, constant).
     - Augmentation (flips, 90° rotations, brightness, noise).
     - Freeze the backbone for the first N epochs, mixed precision, device (Auto = NVIDIA GPU → Apple GPU → CPU), seed.
4. **Output.**
   - **Model name** and **Save in folder** (empty = the project's `models/` folder).
   - **Continue training** resumes a model trained earlier from its last epoch. Raise *Epochs* above what it already ran.

**While training**, the panel shows live curves: training and validation loss, and mIoU, with the best epoch marked. Below them is a table of the latest epochs and the time left.
- **Cancel** stops training and keeps the best model so far.
- If memory runs out, the batch size is halved automatically.

**The model folder:**

```
best_model.pt        weights of the best epoch (+ everything needed to use the model)
last_model.pt        weights, optimiser and epoch of the last epoch (Continue training uses it)
model_config.json    architecture, bands, normalisation (per-band mean / std), classes and colours, patch size, settings, scores
training_log.csv     one row per epoch: losses, mIoU, accuracy, learning rate, seconds
report.html          the evaluation report
```

**The HTML report** is one file that opens offline; **Open report** / **Download report**.
- Headline scores: mIoU, pixel accuracy, macro F1, kappa, best epoch.
- Curves: loss, mIoU, accuracy and learning rate, with the best and early-stop epochs marked.
- Confusion matrices: pixel counts and row %, with producer's / user's accuracy.
- IoU per class, plus precision / recall / F1 per class and a per-class table with training pixel counts.
- **Example predictions:** image | ground truth | prediction for six patches.
- All the settings.

The result card also links to **Show in folder** and **Classify image with it**.

## 19. Classify image

**Tools ▸ Classify image** maps a whole image with a trained model.

1. **Model.** Pick a model; **Browse…** adds a model folder from elsewhere. The card shows its architecture, score, the bands it needs, its classes and a link to its report.
2. **Image.** Tick the layer, or several layers to stack them like in Make training data. They must give the same bands in the same order as the training data.
   - ✓ means the band count and names match.
   - ⚠ means the names differ.
   - ✗ means the band count is wrong.
3. **Area & settings.**
   - Area of interest.
   - **Tile overlap:** none, 25 % (default) or 50 %. Overlapping tiles are blended, so no seams show.
   - Batch size and device.
   - **Confidence layer:** band 2 holds how sure the model is (0–100 %).
4. **Output.** A map name, and optionally **Also save to a folder on my computer**.

The map is added to Contents with the class colours and names, and the result card shows the share of each class. The image is processed in strips, so very large scenes (a full Sentinel-2 tile) work with modest memory.

## 20. Detect object

**Tools ▸ Detect object** finds objects in an image: vehicles, ships, planes, storage tanks, people, animals, sports fields… It draws a box, a rotated box or an outline around each one. Pretrained open-source models need no training, and models you train with **Train detection model** (section 21) are listed too. A model's weights are downloaded once, the first time you use it.

> **Which images work?** The objects must be clearly visible: **high-resolution aerial, drone or satellite images with pixels of about 0.1–1 m** (e.g. NAIP, drone orthomosaics). At 10 m (Sentinel-2) a car is smaller than one pixel, so the tool warns you and will find little. For overhead images, start with **YOLO26 aerial (DOTA)**: the COCO models learned from street-level photos and sometimes take roofs or piers for buses or boats. **Largest object** and the minimum score remove such false detections.

1. **Model.** The dropdown groups the models; ⓘ on each row explains it.

   | Model | Classes | Notes |
   |---|---|---|
   | **YOLO26 aerial (DOTA)** | 15 aerial classes: plane, ship, small / large vehicle, storage tank, harbour, bridge, helicopter, roundabout, swimming pool, sports fields | **Rotated boxes**; the best start for satellite and aerial images. YOLO & SAM add-on |
   | **YOLO26** | COCO (80 everyday classes) | Fast, accurate boxes. YOLO & SAM add-on |
   | YOLO26 outlines | COCO | An outline for every object (instance segmentation). YOLO & SAM add-on |
   | Faster R-CNN v2 / Faster R-CNN / Faster R-CNN Mobile, RetinaNet v2, FCOS, SSD300, SSDlite | COCO | torchvision, deep-learning add-on only |
   | Mask R-CNN v2 | COCO | Outlines (torchvision) |
   | **SAM 2.1: segment everything** | none | Outlines every distinct object or region (buildings, fields, trees, ponds…) without naming it; class = `segment`. Slow: several seconds per tile. YOLO & SAM add-on |
   | **Your trained models** | yours | From Train detection model; **Add a trained model…** adds a model folder from elsewhere |

   - **Model size** (YOLO: nano, small, medium, large, extra large; SAM: tiny, small, base, large). Bigger is more accurate and slower. Nano is weak on small objects such as cars at 0.6 m; medium is the default.
2. **Image.** Pick the layer and the bands to use as **red, green and blue**. Sentinel-2-style band names (B04 / B03 / B02) and 8-bit RGB images are set automatically. Choose the same band three times for a greyscale image. **Brightness stretch:** 2–98 % for satellite or 16-bit data, *None (0–255)* for ordinary 8-bit photos. Your own models use the stretch they were trained with, and the panel warns when the bands differ from their training bands.
3. **What to find.** The class list follows the model (COCO, DOTA or your model's classes), with presets such as Vehicles, Ships & harbours or Aircraft. It is hidden for SAM segment-everything.
4. **Area & settings.**
   - Area of interest. Objects whose centre lies outside it are left out.
   - **Minimum score** (default 0.4): lower finds more objects but also more false ones.
   - **Zoom:** enlarges the image before the model sees it. If a car is only 10–20 pixels wide, try 2× or 3×. For your own models, **Auto** zooms the image so objects look as big as in the training images (from the pixel sizes). The panel shows the number of tiles.
   - **Tile overlap** (default 20 %). An object cut by a tile edge is found whole in the neighbouring tile. Objects larger than the overlap are cut into pieces, and the pieces are joined back into one object.
   - **Duplicate overlap (IoU):** same-class boxes that overlap more than this count as one object (non-maximum suppression).
   - **Outlines with SAM** (box models only): Segment Anything 2.1 turns every box into the object's exact outline (tiny, small, base or large).
   - **Largest object (m)** *(optional)*: leaves out detections whose longer side is bigger than this, e.g. 25 m for vehicles. It needs an image in metres (UTM or another projected coordinate system).
   - Batch size and device. On a Mac the Apple GPU is used; if a model can't run on it, the tool falls back to the CPU on its own.
5. **Output.** A layer name, and optionally **Also save to a folder on my computer**.

The objects are added to Contents as a **vector layer**, coloured by class. Each object has its `class` and `score` (0–1), plus `width_m`, `height_m` and `area_m2` when the image is in metres. The result card shows the count per class. The GeoJSON file is in the job's folder, and **Export data** saves the layer as a Shapefile, GeoJSON or KML.

## 21. Train detection model

**Tools ▸ Train detection model** trains **YOLO26** or **YOLO11** (ultralytics) to find your own objects, e.g. a crop's bales, solar panels, boats of a certain type or damaged roofs. It needs the deep-learning add-on and the YOLO & SAM add-on.

1. **Image.** The layer, the bands used as red, green and blue, and the brightness stretch (as in Detect object; the model remembers them).
2. **Labelled objects.** A vector layer from Contents with your objects: **polygons** drawn around them (Training samples, a shapefile or GeoJSON) or **points** on them, and the **class attribute** that names each object's class. Points become square boxes of the **box size** you give (e.g. 5 m for cars). The panel counts the shapes per class.
   - **Label every object** of your classes in the area you train on: an unlabelled object teaches the model that it is background. Use **Area** to train only where you labelled.
   - Classes with fewer than about 20 objects are hard to learn.
3. **Model.**
   - **Boxes** (default): an upright box per object. Fastest; works with rough shapes or points.
   - **Outlines:** the exact outline of every object (instance segmentation). Needs polygons.
   - **Rotated boxes:** a box turned to fit each object; best for ships, planes, vehicles and buildings seen from above. Starts from weights pretrained on DOTA aerial images.
   - **YOLO version** (YOLO26 or YOLO11) and **size** (nano to extra large). **Start from pretrained weights** (on) learns much faster from a few hundred objects.
4. **Tiles.** **Tile size** (640 px by default) is the model's input. **Zoom** enlarges the image first so small objects get bigger: try 2× when objects are under about 15 pixels. The panel shows how many metres a tile covers. **Overlap** (20 %).
5. **Training.** **Epochs** (100), **batch size** (8), **patience** for early stopping (30) and **validation %** (20). Validation tiles are chosen in spatial blocks, so overlapping tiles rarely end up on both sides.
   - **Advanced:** learning rate, optimiser, vertical flips (on: seen from above there is no "up"), random rotation, mosaic augmentation, **tiles without objects** (background examples, 20 % of the tiles with objects), how much of a cut object must be inside a tile to label it (40 %), device and seed.
6. **Output.** **Model name** and **Save in folder** (empty = the project's `models/` folder).

**While training**, the panel shows the training loss, mAP50 and mAP50-95 per epoch, with the best epoch marked, plus precision and recall. **Cancel** stops after the current epoch and keeps the best model.

**The result card** shows the validation **mAP50** (a detection counts when it overlaps the true object by at least 50 %), **mAP50-95** (stricter overlaps: how exact the boxes are), **precision** and **recall**, and AP50 per class with the number of training / validation objects. **Detect objects with it** opens Detect object with the model chosen and Zoom set to Auto.

**The model folder:**

```
best.pt              weights of the best epoch
last.pt              weights of the last epoch
model_config.json    task, classes and colours, bands, stretch, tile size, pixel size, scores
training_log.csv     one row per epoch
report.html          scores, per-class AP, training curves, confusion matrix, PR curves, example predictions
dataset/             the training tiles and YOLO label files (data.yaml): reusable with other YOLO tools
run/, val/           everything ultralytics wrote
```

## 22. Agri: Diagnose crop disease

**Agri ▸ Diagnose crop disease** tells which disease a crop has from **photos of its leaves**. It uses the photo models of the Multi-Crop Disease Decision Support System: one ConvNeXt model per crop for **42 crops**, and two crop detectors that recognise the crop first. It needs the deep-learning add-on (PyTorch).

| Crops | Photo models trained on | Guide (section 23) |
|---|---|---|
| Apple, Banana, Brinjal, Cashew, Cherry, Coconut, Custard Apple, Guava, Mango, Mulberry (varieties, not diseases), Okra, Papaya, Pomegranate, Rose, Strawberry, Watermelon | public datasets (PlantVillage, MangoLeafBD…) | symptoms, treatment and spray schedules, pests |
| Apricot, Betel, Bitter Gourd, Black Pepper, Bottle Gourd, Cassava, Chrysanthemum, Citrus, Coffee, Cucumber, Cucurbit (pumpkin / melon), Fig, Grape, Loquat, Maize, Peach, Pear, Potato, Rice, Ridge Gourd, Snake Gourd, Soybean, Sugarcane, Tea, Tomato, Walnut | LeafNet and field photos | symptoms only |

Test accuracy is 91–100 % per crop (shown in the panel for the crop you choose). These are test photos from the same datasets the models learned from: real field photos score lower.

1. **Disease models.** The models are on Hugging Face ([ixrbhii/multicrop-disease-models](https://huggingface.co/ixrbhii/multicrop-disease-models)). Each one downloads the first time it's needed (about 95 MB per crop, 190 MB for the two crop detectors) into `agri_models/` in the app's folder, is checked against its published checksum, and is kept for next time; after that no internet is needed. **Use a local models folder…** uses your own copy instead (a copy of the disease app with `data/<Crop>/convnext_best.pth` and `master_model/`, e.g. after retraining); one in `~/Desktop/Farmer_ai` or `~/multicrop-disease-decision-support` is found automatically. **Download from Hugging Face instead** switches back. Each crop's model is also published on its own (e.g. `ixrbhii/tomato-disease-convnext`, loadable with `timm.create_model("hf-hub:ixrbhii/tomato-disease-convnext", pretrained=True)`); all of them are in the collection [Multi-crop disease models](https://huggingface.co/collections/ixrbhii/multi-crop-disease-models-42-crops-6ac0a3291f2153fa716e2993).
2. **Leaf photos.** **Add photos…** (or drop them on the box), or **Add a folder…** for a whole field survey (tick *with sub-folders* to look deeper; up to 5,000 photos). JPG, PNG, WebP, BMP or TIFF. iPhone HEIC photos need the `pillow-heif` package: save them as JPG instead. The best photos show **one leaf filling most of the picture**, in daylight and in focus.
3. **Crop.** Click the box and type a few letters: the list shows the matching crops, also by local name (*paddy*, *aloo*, *bhindi*) or by disease (*rust*, *blight* lists the crops that have it). ↑ ↓ and Enter choose, Esc keeps the current crop.
   - **Detect the crop in each photo** (default): the original detector (16 crops, 99.8 % on test photos) decides, unless the added-crops detector (36 crops, 98 %) is at least 80 % sure of one of the added crops. Pepper, Raspberry, Sorghum and Squash leaves are recognised (so they aren't taken for another crop) but have no disease model.
   - **Or choose the crop** when all photos are of one crop: faster, and no crop mix-ups.
   - **Refuse unclear photos** (on): the disease app's photo check. Photos that are too small (under 96 pixels), too dark or blank get **Retake photo** instead of a guess, and so do photos the crop detector is less than 60 % sure about (probably not a leaf of a supported crop) or the disease model less than 45 % sure about. Blurry or very bright photos are refused only when the models are also unsure. Untick it to diagnose every photo anyway; doubtful results are then marked.
4. **Output.** A name for the results, then **Diagnose photos**.

**Results.**
- **Summary:** how many photos were diseased, healthy, to retake or without a model, and a bar per crop and diagnosis.
- **One card per photo:** the crop (with the detector's confidence, and the runner-up when it is close), the diagnosis, the **top 3** with confidence bars, why a photo was refused and how to retake it, and a link to the disease in the **Crop disease guide**. Click the photo to see it full size.
- **Results table** in Contents ▸ Tabular data (opens in the data viewer): one row per photo with `status` (disease / healthy / variety / retake / no_model / error), `crop`, `crop_confidence`, `diagnosis`, `confidence`, the second and third diagnoses, `reason`, `lat`, `lon`, `taken` and the photo's path.
- **Disease map:** photos taken with location on (GPS in the photo's EXIF) become a **point layer**: red diseased, green healthy, orange retake. Click a point for its diagnosis. Export it as a Shapefile, GeoJSON or KML with Export data, or put it over Index analysis results (e.g. NDVI) to compare.

The CSV and GeoJSON are in the job's folder (**Show in folder**). Photo diagnosis supports, but doesn't replace, a local agriculture expert.

## 23. Agri: Crop disease guide

**Agri ▸ Crop disease guide** is the disease app's knowledge base: about **9,000 questions and answers** (also published as the dataset [ixrbhii/crop-disease-qa](https://huggingface.co/datasets/ixrbhii/crop-disease-qa)) by agriculture experts, from extension booklets and datasets. It needs no add-on and no internet.

1. **Crop.** Type a few letters of the crop, its local name (e.g. *paddy*, *dhan* for Rice) or a disease, and pick it from the list. Crops with the full guide (symptoms, treatment, spray schedules, pests) are listed first; the LeafNet crops only describe symptoms.
2. **Diseases & pests.** First the diseases the photo model detects (with their number of answers), then everything else in the knowledge base for that crop (pests, disorders, practices). Click one to read about it.
3. **Search** the crop's answers with any words, e.g. *yellow leaves*, *spray schedule*, *fruit drop*. All words must appear; the best matches come first. Search and a chosen disease work together.

Answers are grouped as **Symptoms & identification**, **Management & treatment**, **Pests** and **Growing the crop**; the chips filter them. Each answer shows the growth stage it applies to. **Symptoms & treatment of …** on a Diagnose crop disease result opens the guide at that disease.

## 24. Satellite embeddings

**Tools ▸ Satellite embeddings** finds, downloads and explores free **AI embeddings** of the Earth. An embedding gives every
10 m pixel a list of numbers (64 or 128) that sums up a whole year of satellite observations: places that look and behave
alike over the year (the same crop, forest type, water, built-up) get alike numbers. So a handful of labelled points is enough
to map crops or land cover, you can search for places like one you click, and clustering works well. No account is needed.

| Embedding | Numbers per pixel | Years | Coverage | By · licence |
|---|---|---|---|---|
| **Google AlphaEarth Foundations** (Satellite Embedding V1) | 64 (unit vectors) | 2017–2025 | All land and coastal waters, every year | Google and Google DeepMind · CC-BY-4.0 |
| **TESSERA** | 128 | 2017–2025 | All land for 2024; other years in many regions | University of Cambridge · CC0 |

Under *Other open embeddings* the panel lists datasets that give one vector per image patch instead of per pixel (Major TOM,
Clay); they can't be downloaded as maps here.

1. **Embedding.** AlphaEarth or TESSERA.
2. **Area** *(required)*: draw a rectangle or polygon, use the current map view, or any polygon layer (your farm, a district).
   **What's available here?** shows, for every year, whether each embedding covers the area (for TESSERA, how many of the
   0.1° tiles covering it have data); click a year to use it.
3. **Year & resolution.** 10 m is full detail. AlphaEarth also comes at 20, 40, 80 or 160 m (averaged vectors from its
   overviews): far less to download, good for large areas. The panel shows the size of the result and roughly how much is
   downloaded. Up to 25 million pixels per download (e.g. 50 × 50 km at 10 m, or a whole district at 40 m).
4. **Download.** The result is a GeoTIFF layer (float32, one band per dimension named `A00`–`A63` or `E000`–`E127`) in the
   area's UTM zone. **Also make a colour view** adds a second layer where the three main directions of variation (PCA) are
   shown as red, green and blue: alike places get alike colours.
5. **Explore an embedding layer** (any embedding GeoTIFF in Contents, also your own AlphaEarth or TESSERA exports):
   - **Find similar places:** click one or more places on the map (e.g. fields of the crop you're looking for), then
     **Find similar places**: a new layer scores every pixel by cosine similarity to the average of the clicked places
     (1 = the same; above about 0.9 is usually the same kind of place).
   - **Colour view of this layer.**
   - **Classify it:** opens Classical ML for raster, which recognises the layer as an embedding and suggests k-NN, SVM,
     logistic regression and Spectral Angle Mapper (cosine distance). Clustering works on it too.

**How the data is read.** AlphaEarth comes from Google's public bucket (free to download since July 2026; Source Cooperative
as a mirror): only the 1024 × 1024-pixel blocks touching the area are read, all 64 dimensions in parallel. Its files are
stored upside down and as 8-bit codes; the tool flips and decodes them (value = sign(v)·(v/127.5)²). The first use downloads
AlphaEarth's file index once (70 MB), kept in `embeddings_cache/`. TESSERA comes from Source Cooperative: only the rows of
each 0.1° tile that cross the area are downloaded, then decoded with the tile's scales and placed with its landmask.

## 25. Export data

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

## 26. Downloads & jobs

**Tools ▸ Downloads & jobs** lists background jobs (downloads, composites, product downloads, Sentinel-1 processing, …). Each job shows:
- its progress, current step and **Cancel**
- a preview image and land-cover class statistics, where relevant
- its output files, with **Add to map** for GeoTIFFs
- its log
- **Delete files**

Downloads that finish while the app is open are added to Contents automatically. Older ones can be added from **Workspace**.

---

## 27. Credentials

Click **Credentials** (top right). Secrets are stored in your **operating-system keychain** (macOS Keychain, Windows Credential Locker, Linux Secret Service). They're never shown again or sent back to the browser, and are only used with the service they belong to. Each entry has a **Test** button.

| Entry | Needed for | Where to get it |
|---|---|---|
| Copernicus Data Space: account | Original Sentinel `.SAFE` product downloads | dataspace.copernicus.eu |
| Copernicus Data Space: S3 keys | The *Copernicus* source in Find imagery | eodata-s3keysmanager.dataspace.copernicus.eu |
| USGS EarthExplorer | Original Landsat bundle downloads | ers.cr.usgs.gov ▸ Access Request ▸ M2M API, then create an *Application Token* (USGS no longer accepts passwords for this) |
| Planetary Computer *(optional)* | Higher rate limits only | planetarycomputer.developer.azure-api.net |

**Security:** the server only accepts connections from your own computer (127.0.0.1) and rejects requests from other websites. It's a single-user tool; hosting it for others would need user accounts first.

---

## 28. Command-line tool

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

## 29. Data sources and band conventions

| Source | Login | Notes |
|---|---|---|
| Earth Search (AWS), default | none | Sentinel-2 L2A Cloud-Optimized GeoTIFFs; fastest |
| Microsoft Planetary Computer | none | Sentinel-2, Landsat C2 L2, WorldCover, Esri LULC, NAIP, Sentinel-1 RTC, DEM |
| Copernicus Data Space | free S3 keys | The official ESA archive (JPEG2000) |
| USGS EarthExplorer | ERS account + M2M token | Original Landsat product bundles |
| Google AlphaEarth Foundations (Google Cloud Storage, Source Cooperative) | none | Satellite embeddings, 64-D, 10 m, 2017–2025 (CC-BY-4.0) |
| TESSERA (Source Cooperative) | none | Satellite embeddings, 128-D, 10 m (CC0) |
| Hugging Face ([ixrbhii/multicrop-disease-models](https://huggingface.co/ixrbhii/multicrop-disease-models)) | none | Crop disease models for Agri ▸ Diagnose crop disease, downloaded once per crop (CC-BY-4.0) |

- **Reflectance offset:** values are made consistent across sources and dates. The −1000 DN offset of Sentinel-2 processing baseline ≥ 04.00 is applied per scene.
- **Landsat band names:** Landsat bands are stored under their Sentinel-2-equivalent names: B02 blue, B03 green, B04 red, B08 NIR, B11 / B12 SWIR. So every index and model works for both satellites. Landsat clouds are masked with the QA_PIXEL flags.
- **Highest free resolution:** Sentinel-2 at **10 m** is the highest-resolution free, global, frequently updated optical imagery. Finer free data exists only regionally, e.g. NAIP 0.6 m (USA) and national programmes such as Bhuvan (India).

---

## 30. Files and folders

With a project open, these folders are inside the project folder (next to `lulc_project.json`). Without a project they are in the app's folder (the temporary workspace). `data/` is always also read from the app's folder. The list of recent projects is stored in `~/.lulc-fetch/recent.json`.

| Folder | Contents |
|---|---|
| `lulc_project.json` | (projects only) the project's name, Contents, map view and settings |
| `data/` | Your Copernicus `.SAFE` products (input) |
| `imports/` | Lightweight VRTs for opened Sentinel-2 products |
| `downloads/` | Job outputs (downloads, maps, stacks…), one folder per job |
| `tables/` | Tables you added, clustering results (with a `cluster` column), t-SNE maps (`tsne_1`, `tsne_2`), (CSV, TSV and Excel are converted to CSV; Parquet is kept) and Raster → table outputs (with a `.json` description) |
| `models/` | Trained models (`.joblib`) with `.json` reports and `.evaluation.html` evaluation reports; deep-learning models as folders (`best_model.pt`, `model_config.json`, `report.html`…) |
| `addons/` | (desktop app) the deep-learning add-on (PyTorch) and the YOLO & SAM add-on (ultralytics), if installed |
| `training_data/` | Datasets made with Make training data when no folder is chosen (images/, labels/, classes.txt, dataset.json…) |
| `embeddings_cache/` | The AlphaEarth file index used by Satellite embeddings (downloaded once) |
| `agri_models/` | Crop disease models downloaded from Hugging Face (shared by all projects) |
| `uploads/`, `analysis/`, `exports/` | Uploaded files (photos added to Diagnose crop disease go to `uploads/photos/`), index exports, other exports |

All of these are excluded from git.

---

## 31. Limits and known issues

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
- **Crop disease models** aren't in the installer: Diagnose crop disease downloads each crop's model from Hugging Face the first time (internet needed once per crop). Mulberry's model tells varieties, not diseases. Coffee's *Cercospora brown eye spot* is usually missed (few training photos). Leaves of crops the detectors don't know can still be taken for a known crop; the photo check catches only part of them.

---

## 32. Troubleshooting

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
| Diagnose crop disease: "Couldn't reach Hugging Face" | The first diagnosis of each crop downloads its model (about 95 MB): connect to the internet once. Models already downloaded (listed in the Disease models card) work offline |
| Diagnose crop disease: "The … model download was damaged" | The download was interrupted or changed on the way: run it again (only that model is downloaded again) |
| Free up the space of downloaded disease models | Delete the `agri_models/` folder in the app's folder; models download again when needed |

---

## 33. For developers: adding a tool

The web app is a FastAPI backend (`webapp/server.py`) with a single-page frontend (`webapp/static/`). Processing code lives in the `lulc_fetch/` package.

1. Add a panel to `webapp/static/index.html`: `<section id="tab-mytool" class="tabpanel hidden">…</section>`.
2. Add an entry to `TOOLS` in `webapp/static/app.js`: `{ id: "mytool", title, icon, subtitle }`. The Tools-menu item and start-page card are generated from it. Add `menu: "agri"` to list it in the Agri menu instead.
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
| `agri/` | Agri menu: `disease.py` (leaf photo → crop → disease, run in the deep-learning helper process), `knowledge.py` (crop list and the guide's search), `labels.py` (crop and disease names), `data/` (each crop's labels, test results and knowledge base). `python -m lulc_fetch.agri.import_data <disease repo folder>` refreshes `data/` after the disease models are retrained, and `python -m lulc_fetch.agri.publish <disease repo folder> <out>` converts the models for Hugging Face (then upload `<out>`), and `python -m lulc_fetch.agri.publish --repos <out> <folder> --upload` updates the 44 one-model repositories and the collection |
