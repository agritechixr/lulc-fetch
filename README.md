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
| **Classical ML** | Train 14 models (Random Forest, XGBoost, LightGBM, SVM, Maximum Likelihood…) with honest, spatially independent accuracy assessment, then **classify an image** into a land-cover map |
| **Export data** | Save any layer as GeoTIFF, PNG, Shapefile, GeoJSON or KML, for the whole layer or just an area |

Across the app:
- **Layers:** add GeoTIFF, Shapefile, GeoJSON and KML files, then show, reorder, style, read pixel values and export them.
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
