# LULC Fetch

A land-use / land-cover (LULC) toolkit for free satellite data. It runs on your own computer as a **desktop-GIS-style web app** (layers, tools, map), with a **command-line tool** for downloads.

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
| **Classical ML: supervised** | Train 14 models (Random Forest, XGBoost, LightGBM, SVM, Maximum Likelihood…) for classification or regression: choose the target and each column's role and type (numeric / categorical), preprocess (missing values, outlier clipping, skew transforms, scaling, removing redundant columns), tune hyperparameters with cross-validation, compare all models on a leaderboard, get honest spatially independent accuracy and an **HTML evaluation report** (confusion matrices, ROC / PR curves, residual plots…), then **classify an image** into a land-cover map |
| **Classical ML: unsupervised** | **Clustering** with K-means, hierarchical (dendrogram), DBSCAN, HDBSCAN, spectral clustering and Gaussian mixture: automatic choice of k, quality scores, cluster profiles, comparison with known labels, and **unsupervised classification of images**. **t-SNE maps** to see how classes or clusters separate. |
| **Export data** | Save any layer as GeoTIFF, PNG, Shapefile, GeoJSON or KML, for the whole layer or just an area |

Across the app:
- **Projects:** one folder per project keeps your layers, results, tables, models and map view; it autosaves and reopens where you left off. You can also work without a project in a temporary workspace.
- **Save anywhere:** every tool can also save its result to a folder you choose, and right-click ▸ **Save to folder…** works on any layer, table or picture. A built-in folder picker helps.
- **Contents in two sections:** *2D data* (GeoTIFF, RGB photos / georeferenced JPG & PNG, Shapefile, GeoJSON, KML) and *Tabular data* (CSV, TSV, Excel, Parquet).
- **Data viewer under the map:** open tables and vector attribute tables with paging, sorting, search / filters, column statistics and rows linked to the map. Pictures can also be opened there.
- **Resizable panels:** drag the edges of Contents, the tool panel and the data viewer.
- **Area picker:** every tool can work on just an area of your choice.
- **Progress and Cancel** for every long task.
- **ⓘ hints** on every option.
- **Credentials** for Copernicus and USGS EarthExplorer, kept in your OS keychain.

## Install and run

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
