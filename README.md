# LULC Fetch

A land-use / land-cover (LULC) toolkit for free satellite data. It runs on your own computer as a **desktop-GIS-style web app** (layers, tools, map), with a **command-line tool** for downloads.

⬇️ **[Download for Mac or Windows](#download-mac-and-windows-no-installation-of-python-needed)**: ready-to-run apps, no Python needed.

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
| **Raster → table** | Turn any image into a CSV / Parquet table, with ground-truth labels as the last column |
| **Classical ML for raster** | Train straight from an image and its ground truth (polygons, points or a class raster) and get a classified map in one step. No table needed. SVM (RBF / linear), Maximum Likelihood, Spectral Angle Mapper, Minimum Distance, Random Forest, k-NN (cosine) and more, for **RGB, multispectral, hyperspectral (100+ bands) and pixel embeddings** (AlphaEarth, TESSERA). The kind of data is detected, with suggested settings. |
| **Classical ML: supervised** | Train 16 models (Random Forest, XGBoost, LightGBM, SVM, Maximum Likelihood…) for classification or regression: choose the target and each column's role and type (numeric / categorical), preprocess (missing values, outlier clipping, skew transforms, scaling, removing redundant columns), tune hyperparameters with cross-validation, compare all models on a leaderboard, get honest spatially independent accuracy and an **HTML evaluation report** (confusion matrices, ROC / PR curves, residual plots…), then **classify an image** into a land-cover map |
| **Classical ML: unsupervised** | **Clustering** with K-means, hierarchical (dendrogram), DBSCAN, HDBSCAN, spectral clustering and Gaussian mixture: automatic choice of k, quality scores, cluster profiles, comparison with known labels, and **unsupervised classification of images**. **t-SNE maps** to see how classes or clusters separate. |
| **Export data** | Save any layer as GeoTIFF, PNG, Shapefile, GeoJSON or KML, for the whole layer or just an area |

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
- **Progress and Cancel** for every long task.
- **ⓘ hints** on every option.
- **Credentials** for Copernicus and USGS EarthExplorer, kept in your OS keychain.

## Download (Mac and Windows: no installation of Python needed)

### Step 1: download the file for your computer

1. Open the **[Releases page](https://github.com/agritechixr/lulc-fetch/releases/latest)**. You can also find it on this repository's main page: in the right-hand column, click **Releases** (or the version number under it, e.g. *v0.1.0*).
2. Scroll down to **Assets** at the bottom of the newest release. Click **Assets** if the list is folded.
3. Click the file for your computer. Your browser saves it in your **Downloads** folder.

| Your computer | Click this file |
|---|---|
| Mac with Apple Silicon (M1, M2, M3, M4), macOS 14 Sonoma or newer | **LULC-Fetch-macOS-AppleSilicon.dmg** |
| Mac with an Intel processor, macOS 15 Sequoia or newer | **LULC-Fetch-macOS-Intel.dmg** |
| Windows 10 / 11 (64-bit) | **LULC-Fetch-Windows.zip** |

Not sure which Mac you have? Apple menu ▸ **About This Mac**: "Chip: Apple M…" means Apple Silicon, and "Processor: Intel…" means Intel.

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
- LULC Fetch opens in your web browser, and a small window lets you reopen it, open your data folder, or quit. Closing that window stops the app.
- Everything runs on your own computer. The internet is only used to find and download satellite images, for background maps and for address search. All analysis and machine learning work offline.
- Your files are kept in **Documents ▸ LULC Fetch**. Put Copernicus `.SAFE` products in its `data` folder. Projects can live in any folder.

## Build the apps yourself

- **Mac:** `./packaging/build_mac.sh` → `dist/LULC Fetch.app` and `dist/LULC-Fetch.dmg`. Needs the source setup below and `brew install libomp`.
- **Windows:** in PowerShell, `.\packaging\build_windows.ps1` → `dist\LULC Fetch\LULC Fetch.exe` and `dist\LULC-Fetch-Windows.zip`.
- **GitHub builds them automatically:** push a version tag (`git tag v0.2.0 && git push origin v0.2.0`), and the *Build desktop apps* workflow builds both Mac versions and the Windows version, tests them, and publishes them on a Release. You can also run it by hand from the **Actions** tab.

## Run from source (for development)

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[web]"
.venv/bin/lulc-fetch-web          # opens http://127.0.0.1:8000
```

On macOS, also run `brew install libomp` (needed by XGBoost and LightGBM).

No account is needed to search and download imagery. Accounts are only needed for original Copernicus or USGS product downloads.

## Typical workflow

**Find imagery** (or open a `.SAFE` product) → **Training samples** → **Stack layers** (optional) → **Raster → table** → **Train a model** → **Classify an image** → **Export**

## Command line

```bash
.venv/bin/lulc-fetch composite --bbox 77.56,12.94,77.61,12.99 --start 2026-01-01 --end 2026-03-31 -o out/composite.tif
.venv/bin/lulc-fetch --help
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
