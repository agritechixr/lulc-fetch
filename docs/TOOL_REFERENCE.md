# LULC Fetch: tool and model reference

Every tool, where it is in the app, what it does, the server endpoint(s) it runs and **every parameter** (type, default, allowed values), then every model with its settings. Generated from the code by `scripts/make_tool_reference.py` (run it again after changing a tool), so it matches the app exactly. Parameters are those of the API (what the panels, the Assistant and Workflows send); the panels show them with plain names. For how to use the tools see the [User Guide](USER_GUIDE.md); for an overview, [FEATURES.md](../FEATURES.md).

Types: *text*, *number*, *integer*, *yes / no*, *list*, *object* (e.g. a GeoJSON layer). Paths are relative to the workspace (e.g. `downloads/…/image.tif`, `uploads/dem.tif`); points are `[longitude, latitude]`.

## Contents

- Analysis ▸ Tools: Imagery, Training data, Classical ML, Deep learning, Vector, Spatial analysis, Fuzzy & suitability, Image features, Objects, Raster & terrain, Assess, Conversion, Output, Automate
- Analysis ▸ Agri
- Analysis ▸ Embeddings
- Analysis ▸ Forecast
- Analysis ▸ SAR
- Analysis ▸ Hydrology
- Insert ▸ Library
- Insert
- [Models and their parameters](#models-and-their-parameters)
- [Other endpoints](#other-endpoints)

## Analysis ▸ Tools

### Imagery

#### Find imagery

Search, preview and download Sentinel-2, composites and land-cover labels

*Tool id* `search`.

`POST /api/aoi/upload`: Vector files → WGS 84 GeoJSON. ask_crs (Add data): data without a coordinate system comes back as it is, marked

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `ask_crs` (query) | yes / no | `false` |  |

`GET /api/geocode`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `q` (query) | text | **required** |  |

`POST /api/jobs`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `kind` | text | **required** |  |
| `source` | text | `"earth-search"` |  |
| `aoi` | object (optional) | none |  |
| `start` | text (optional) | none |  |
| `end` | text (optional) | none |  |
| `max_cloud` | number | `40` |  |
| `date` | text (optional) | none |  |
| `bands` | list of text | none |  |
| `indices` | yes / no | `true` |  |
| `res` | number | `10` | ≥ 0.5; ≤ 1000 |
| `mask_clouds` | yes / no | `true` |  |
| `max_scenes` | integer | `20` | ≥ 1; ≤ 100 |
| `stat` | text | `"median"` |  |
| `product` | text | `"worldcover"` |  |
| `year` | integer | `2021` |  |
| `product_names` | list of text | none |  |
| `entity_ids` | list of text | none |  |
| `resampling` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |

`GET /api/jobs`

`POST /api/preview`: True-colour image of the AOI from the chosen items, plus a cloud/shadow overlay and stats.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `source` | text | **required** |  |
| `item_ids` | list of text | **required** |  |
| `aoi` | object | **required** |  |
| `max_px` | integer | `900` | ≥ 128; ≤ 2048 |

`POST /api/search`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `source` | text | `"earth-search"` |  |
| `aoi` | object | **required** |  |
| `start` | text | **required** |  |
| `end` | text | **required** |  |
| `max_cloud` | number | `30` | ≥ 0; ≤ 100 |
| `limit` | integer | `200` | ≥ 1; ≤ 1000 |

#### Index analysis

NDVI, SAVI, EVI, NDWI and 21 more indices or your own formula

*Tool id* `analyze`.

`POST /api/analyze/export`: Several indices as one GeoTIFF, as a background job (progress + cancel).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band_map` | object (integer values) | **required** |  |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |
| `indices` | list of text | none |  |
| `formulas` | list of object (text values) | none |  |
| `clip` | object (optional) | none |  |

`GET /api/formula/check`: Validate a custom formula and list the bands it needs (for live feedback while typing).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `formula` (query) | text | **required** |  |

#### Index time series

NDVI (or EVI, NDWI, NDMI, NDRE, SAVI) of a point or field in every Sentinel-2 scene of a period, from the free catalogue, clouds masked: crop calendars, droughts, harvest dates

*Tool id* `timeseries`.

`POST /api/timeseries`: The mean of an index (NDVI, EVI, NDWI…) of a point or field in every Sentinel-2 scene of a date range, from the

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `geometry` | object | **required** |  |
| `start` | text | **required** | pattern `^\d{4}-\d{2}-\d{2}$` |
| `end` | text | **required** | pattern `^\d{4}-\d{2}-\d{2}$` |
| `index` | text | `"NDVI"` | one of `NDVI`, `EVI`, `NDWI`, `NDMI`, `NDRE`, `SAVI` |
| `max_cloud` | number | `80` | ≥ 0; ≤ 100 |
| `buffer_m` | number | `15` | ≥ 5; ≤ 500 |
| `source` | text | `"earth-search"` | one of `earth-search`, `planetary-computer` |
| `name` | text | `"time_series"` | up to 80 characters |

#### PCA & dimensionality reduction

PCA, Kernel PCA, NMF, ICA and more (scikit-learn) on any multiband image

*Tool id* `pca`.

`POST /api/pca/run`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer | **required** |  |
| `method` | text | `"pca"` |  |
| `params` | object | none |  |
| `clip` | object (optional) | none |  |
| `name` | text | `"image"` |  |

`GET /api/pca/schema`

#### Stack layers

Combine bands from several layers (S2, S1, DEM, indices…) onto one grid

*Tool id* `stack`.

`GET /api/indices`

`POST /api/stack`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `items` | list of StackItem | **required** |  |
| `ref` | text | **required** |  |
| `clip` | object (optional) | none |  |
| `factor` | integer | `1` | ≥ 1; ≤ 64 |
| `name` | text | `"stack"` | up to 80 characters |
| `resampling` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |

*StackItem*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `name` | text | `"layer"` |  |
| `bands` | list of integer (optional) | none |  |
| `index` | text (optional) | none |  |
| `formula` | text (optional) | none | up to 500 characters |
| `band_map` | object (integer values) | none |  |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |

#### Mosaic / merge rasters

Join neighbouring tiles or scenes into one image. By default the seams are blended smoothly and the colours matched, so no tile edges show

*Tool id* `rmosaic`.

`POST /api/raster/mosaic`: Join neighbouring tiles or scenes into one image: smooth blended seams and colour balance by default, or first /

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `rasters` | list of text | **required** | 2–200 items |
| `method` | text | `"blend"` | one of `blend`, `first`, `last`, `mean`, `median`, `min`, `max`, `mode` |
| `balance` | text | `"overlap"` | one of `overlap`, `none` |
| `categorical` | yes / no | `false` |  |
| `blend_px` | integer | `64` | ≥ 1; ≤ 2000 |
| `res` | number (optional) | none | > 0 |
| `crs` | text (optional) | none | up to 200 characters |
| `resampling` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |
| `name` | text | `"mosaic"` | up to 80 characters |

#### Burn severity (dNBR)

Where land burned between two dates and how badly: dNBR and its severity classes with burned hectares, e.g. stubble burning after the harvest

*Tool id* `rburn`.

`POST /api/raster/burn`: Burn severity: dNBR (NBR before − NBR after) and its USGS severity classes, with the burned area. Logic in

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `before` | text | **required** |  |
| `after` | text | **required** |  |
| `before_bands` | object (integer values) | none |  |
| `after_bands` | object (integer values) | none |  |
| `before_scale` | list of number (optional) | none | 2–2 items |
| `after_scale` | list of number (optional) | none | 2–2 items |
| `breaks` | list of number (optional) | none | 6–6 items |
| `min_post_nbr` | number (optional) | none | ≥ -1; ≤ 1 |
| `resampling` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |
| `name` | text | `""` | up to 80 characters |

#### Water mask

Open water from a multispectral image (Sentinel-2, Landsat …): AWEI, NDWI, MNDWI or WI2015 with clouds, shadow and snow masked, specks removed and a confidence; also evidence for the SAR flood map

*Tool id* `rwater`.

`POST /api/raster/watermask`: Water mask of a multispectral image (logic in lulc_fetch/watermask.py).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | object (integer values) | none |  |
| `index` | text | `"auto"` | one of `auto`, `mndwi`, `ndwi`, `awei_nsh`, `awei_sh`, `wi2015` |
| `threshold` | text | `"zero"` | one of `zero`, `auto`, `otsu`, `multi_otsu`, `li`, `yen`, `kapur`, `triangle`, `isodata`, `niblack`, `sauvola`, `wolf`, `phansalkar`, `fcm`, `fuzzy`, `membership` |
| `window` | integer | `51` | ≥ 5; ≤ 1001 |
| `k` | number (optional) | none | ≥ -2; ≤ 2 |
| `cloud` | text (optional) | none |  |
| `scl` | yes / no | `true` |  |
| `dem` | text (optional) | none |  |
| `min_px` | integer | `9` | ≥ 1; ≤ 100000 |
| `slope_max` | number | `10` | ≥ 1; ≤ 60 |
| `aoi` | object (optional) | none |  |
| `name` | text | `"water_mask"` | up to 80 characters |

`GET /api/raster/watermask/bands`: Which band is blue / green / red / NIR / SWIR1 / SWIR2 (from the names and the sensor), for the band pickers.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### Pansharpen

Make a multispectral image as sharp as its panchromatic band (e.g. Landsat 8/9 from 30 m to 15 m with band 8): Gram-Schmidt, Brovey or IHS

*Tool id* `rpansharp`.

`POST /api/raster/pansharpen`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `image` | text | **required** |  |
| `pan` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `pan_band` | integer | `1` | ≥ 1 |
| `method` | text | `"gsa"` | one of `gsa`, `brovey`, `ihs` |
| `name` | text | `""` | up to 80 characters |

#### Spectral unmixing

How much of each pixel is vegetation, soil, water, built-up…: fraction maps from labelled samples of pure materials or found in the image

*Tool id* `runmix`.

`POST /api/raster/unmix`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `n_auto` | integer | `3` | ≥ 2; ≤ 10 |
| `layer` | object (optional) | none |  |
| `field` | text (optional) | none |  |
| `constrained` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

### Training data

#### Training samples

Draw labelled polygons and points for each class on the map

*Tool id* `samples`.

Runs in the browser (or through endpoints shared with other tools).

#### Raster → table

Turn any image (multispectral, hyperspectral, SAR) into a table, with optional ground-truth labels

*Tool id* `raster2table`.

`GET /api/tables/file`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`POST /api/tables/from-raster`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `clip` | object (optional) | none |  |
| `factor` | integer | `1` | ≥ 1; ≤ 64 |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |
| `ground_truth` | object (optional) | none |  |
| `label_name` | text | `"label"` | up to 40 characters |
| `labelled_only` | yes / no | `true` |  |
| `sampling` | text | `"all"` |  |
| `sample_size` | integer | `100000` | ≥ 10; ≤ 5e+07 |
| `per_class` | integer | `5000` | ≥ 1; ≤ 1e+07 |
| `xy` | yes / no | `true` |  |
| `lonlat` | yes / no | `true` |  |
| `rowcol` | yes / no | `false` |  |
| `drop_nodata` | yes / no | `true` |  |
| `format` | text | `"csv"` |  |
| `name` | text | `"table"` |  |
| `class_colors` | object (text values) (optional) | none |  |

#### Make training data

Cut large images and their ground truth into image / label patches for deep-learning training

*Tool id* `patches`.

`POST /api/patches/make`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `clip` | object (optional) | none |  |
| `patch_m` | list of number | **required** | 2–2 items |
| `overlap_m` | list of number | none | 2–2 items |
| `edge` | text | `"pad"` | one of `pad`, `drop` |
| `resampling` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |
| `inputs` | list of PatchInput | **required** | 1–20 items |
| `ground_truth` | object (optional) | none |  |
| `name` | text | `"training_patches"` | up to 80 characters |
| `folder` | text (optional) | none | up to 1000 characters |
| `min_valid` | number | `0.5` | ≥ 0; ≤ 1 |
| `require_labels` | yes / no | `false` |  |
| `min_labelled` | number | `0.01` | ≥ 0; ≤ 1 |
| `remap` | yes / no | `true` |  |
| `class_colors` | object (text values) (optional) | none |  |

*PatchInput*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `name` | text (optional) | none | up to 120 characters |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |

`POST /api/patches/plan`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `clip` | object (optional) | none |  |
| `patch_m` | list of number | **required** | 2–2 items |
| `overlap_m` | list of number | none | 2–2 items |
| `edge` | text | `"pad"` | one of `pad`, `drop` |

`POST /api/project/reveal`: Show the project folder (or a saved file's folder) in Finder / Explorer / the file manager.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text (optional) | none |  |

### Classical ML

#### Classical ML (tabular data)

Machine-learning tools that work on tables

*Tool id* `ml`.

`POST /api/ml/compare`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `table` | text | **required** |  |
| `target` | text | **required** |  |
| `features` | list of text | **required** |  |
| `task` | text | `"auto"` |  |
| `common` | object | none |  |
| `categorical` | list of text | none |  |
| `max_rows` | integer | `20000` | ≥ 500; ≤ 200000 |

`POST /api/ml/predict`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `model` | text | **required** |  |
| `path` | text | **required** |  |
| `band_map` | object (integer values) | **required** |  |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |
| `clip` | object (optional) | none |  |
| `resolution` | text | `"auto"` |  |
| `confidence` | yes / no | `true` |  |
| `name` | text | `"classified"` | up to 80 characters |

`POST /api/ml/train`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `table` | text | **required** |  |
| `target` | text | **required** |  |
| `features` | list of text | **required** |  |
| `model` | text | `"rf"` |  |
| `task` | text | `"auto"` |  |
| `params` | object | none |  |
| `common` | object | none |  |
| `name` | text | `"model"` | up to 80 characters |
| `categorical` | list of text | none |  |
| `tuning` | object | none |  |
| `report_dir` | text (optional) | none | up to 1000 characters |

`GET /api/models`

`GET /api/models/evaluation`: The model's HTML evaluation report (rebuilt from the predictions stored in the model if missing).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `download` (query) | yes / no | `false` |  |
| `rebuild` (query) | yes / no | `false` |  |

`POST /api/models/evaluation/save`: Save a copy of a model's HTML evaluation report in a folder of the user's choice.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `folder` | text | **required** | up to 1000 characters |

`GET /api/models/file`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`GET /api/models/report`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`GET /api/tables`

`GET /api/tables/describe`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`POST /api/unsup/cluster`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `table` | text | **required** |  |
| `features` | list of text | **required** |  |
| `categorical` | list of text | none |  |
| `method` | text | `"kmeans"` |  |
| `params` | object | none |  |
| `prep` | object | none |  |
| `options` | object | none |  |
| `compare` | text (optional) | none |  |
| `name` | text | `"clusters"` | up to 80 characters |

`GET /api/unsup/schema`

`POST /api/unsup/tsne`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `table` | text | **required** |  |
| `features` | list of text | **required** |  |
| `categorical` | list of text | none |  |
| `params` | object | none |  |
| `prep` | object | none |  |
| `color` | text (optional) | none |  |
| `name` | text | `"tsne"` | up to 80 characters |

#### Classical ML for raster

Train SVM, Maximum Likelihood, Random Forest, SAM and more straight from an image and ground truth, and map it: RGB, multispectral, hyperspectral or embeddings

*Tool id* `rasterml`.

`GET /api/ml/schema`

`GET /api/rasterml/inspect`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `bands` (query) | text | `""` |  |

`POST /api/rasterml/run`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `ground_truth` | object | **required** |  |
| `model` | text | `"rf"` |  |
| `params` | object | none |  |
| `common` | object | none |  |
| `tuning` | object | none |  |
| `clip` | object (optional) | none |  |
| `map_whole` | yes / no | `true` |  |
| `factor` | integer | `1` | ≥ 1; ≤ 64 |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |
| `per_class` | integer (optional) | `3000` | ≥ 10; ≤ 1e+07 |
| `name` | text | `"classified"` | up to 80 characters |
| `class_colors` | object (text values) (optional) | none |  |
| `confidence` | yes / no | `true` |  |
| `resolution` | text | `"auto"` |  |

`GET /api/rasterml/schema`

#### Spatial cross-validation

How much a random train / test split overstates a map's accuracy: the same model scored with random k-fold and with spatial blocks of several sizes, and a correlogram suggesting the block size

*Tool id* `rspcv`.

`POST /api/raster/spatialcv`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `ground_truth` | object | **required** |  |
| `model` | text | `"lgbm"` | one of `lgbm`, `rf`, `xgb` |
| `blocks_m` | list of number | none | 1–10 items |
| `folds` | integer | `5` | ≥ 2; ≤ 10 |
| `per_class` | integer | `2000` | ≥ 50; ≤ 50000 |

#### Interpolation

Make a continuous surface (GeoTIFF) from values measured at points, e.g. air quality at monitoring stations, rainfall at gauges, soil samples: IDW, kriging, spline, natural neighbour, nearest neighbour, trend surface or TIN, cut to an area, with a check of how well each method predicts

*Tool id* `interp`.

`POST /api/interp/compare`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `points` | object | **required** |  |
| `field` | text | **required** | up to 200 characters |

`POST /api/interp/run`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `points` | object | **required** |  |
| `field` | text | **required** | up to 200 characters |
| `method` | text | `"idw"` | one of `idw`, `kriging`, `spline`, `natural`, `nearest`, `trend`, `tin` |
| `params` | object | none |  |
| `area` | object (optional) | none |  |
| `res_m` | number (optional) | none | > 0; ≤ 100000 |
| `name` | text | `"surface"` | up to 80 characters |

`GET /api/interp/schema`

### Deep learning

#### Train classify model

Train U-Net, DeepLabV3+, PSPNet, FCN, SegFormer and more (MobileNetV2/V3, ResNet, EfficientNet backbones) on your Make-training-data patches, with early stopping and an HTML report

*Tool id* `dltrain`.

`POST /api/dl/dataset`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` | text | **required** | up to 2000 characters |

`GET /api/dl/datasets`: Training datasets: the project's training_data/ folder plus folders used before.

`POST /api/dl/install`: Install PyTorch + segmentation-models-pytorch (a background job with pip's output in the log).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `variant` | text | `"default"` | one of `default`, `cpu`, `cuda`, `yolo` |

`GET /api/dl/models`

`POST /api/dl/models/add`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` | text | **required** | up to 2000 characters |

`POST /api/dl/predict`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `model` | text | **required** | up to 2000 characters |
| `inputs` | list of PatchInput | **required** | 1–20 items |
| `clip` | object (optional) | none |  |
| `overlap` | number | `0.25` | ≥ 0; ≤ 0.75 |
| `batch_size` | integer | `8` | ≥ 1; ≤ 256 |
| `device` | text | `"auto"` | one of `auto`, `cpu`, `cuda`, `mps` |
| `confidence` | yes / no | `true` |  |
| `name` | text | `"dl_map"` | up to 80 characters |

*PatchInput*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `name` | text (optional) | none | up to 120 characters |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |

`GET /api/dl/report`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` (query) | text | **required** |  |
| `download` (query) | yes / no | `false` |  |

`GET /api/dl/schema`

`GET /api/dl/status`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `refresh` (query) | yes / no | `false` |  |

`POST /api/dl/train`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dataset` | text | **required** | up to 2000 characters |
| `arch` | text | `"unet"` |  |
| `encoder` | text | `"tu-mobilenetv3_large_100"` |  |
| `pretrained` | yes / no | `true` |  |
| `params` | object | none |  |
| `name` | text | `"dl_model"` | up to 80 characters |
| `folder` | text (optional) | none | up to 1000 characters |
| `resume` | text (optional) | none | up to 2000 characters |

#### Classify image

Map a whole image with a model from Train classify model (deep learning, tiled, seamless), with a confidence layer

*Tool id* `dlpredict`.

#### Train detection model

Train YOLO26 / YOLO11 to find your own objects (boxes, outlines or rotated boxes) from an image and labelled polygons or points, with early stopping, live curves and an HTML report

*Tool id* `traindet`.

`GET /api/det/report`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` (query) | text | **required** |  |
| `download` (query) | yes / no | `false` |  |

`GET /api/det/schema`

`POST /api/det/train`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `input` | PatchInput | **required** |  |
| `ground_truth` | object | **required** |  |
| `task` | text | `"detect"` | one of `detect`, `segment`, `obb` |
| `family` | text | `"yolo26"` | one of `yolo26`, `yolo11` |
| `size` | text | `"s"` | pattern `^[nsmlx]$` |
| `pretrained` | yes / no | `true` |  |
| `tile_px` | integer | `640` | ≥ 128; ≤ 2048 |
| `zoom` | number | `1.0` | ≥ 0.25; ≤ 8 |
| `overlap` | number | `0.2` | ≥ 0; ≤ 0.5 |
| `clip` | object (optional) | none |  |
| `stretch` | text | `"percent"` | one of `percent`, `minmax`, `byte` |
| `params` | object | none |  |
| `class_colors` | object (text values) (optional) | none |  |
| `name` | text | `"detector"` | up to 80 characters |
| `folder` | text (optional) | none | up to 1000 characters |

*PatchInput*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `name` | text (optional) | none | up to 120 characters |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |

#### Detect object

Find vehicles, ships, planes, people, storage tanks and more in high-resolution images: YOLO26 (incl. aerial DOTA model), Faster R-CNN, RetinaNet, Mask R-CNN, SAM 2.1 segment-everything, or your own trained models. Boxes or outlines as a vector layer

*Tool id* `detect`.

`GET /api/det/models`

`POST /api/det/models/add`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` | text | **required** | up to 2000 characters |

`POST /api/detect/run`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `input` | PatchInput | **required** |  |
| `model` | text | `"fasterrcnn_v2"` |  |
| `size` | text (optional) | none | pattern `^[nsmlxtb]$` |
| `custom` | text (optional) | none | up to 2000 characters |
| `sam_refine` | text (optional) | none | pattern `^[tsbl]$` |
| `clip` | object (optional) | none |  |
| `classes` | list of text (optional) | none | 0–200 items |
| `score` | number | `0.4` | ≥ 0.01; ≤ 1 |
| `zoom` | number or text | `1.0` |  |
| `overlap` | number | `0.2` | ≥ 0; ≤ 0.5 |
| `nms_iou` | number | `0.5` | ≥ 0.05; ≤ 0.95 |
| `stretch` | text | `"percent"` | one of `percent`, `minmax`, `byte` |
| `batch_size` | integer | `2` | ≥ 1; ≤ 64 |
| `device` | text | `"auto"` | one of `auto`, `cpu`, `cuda`, `mps` |
| `max_size_m` | number (optional) | none | > 0; ≤ 100000 |
| `name` | text | `"objects"` | up to 80 characters |

*PatchInput*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `name` | text (optional) | none | up to 120 characters |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |

`GET /api/detect/schema`

### Vector

#### Buffer

Grow points, lines or polygons by a distance in metres (or shrink polygons with a negative one); optionally merge the result into one shape

*Tool id* `vbuffer`.

`POST /api/vector/buffer`: Grow each shape by a distance in metres (shrink with a negative one); dissolve merges them into one.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layer` | text or object | **required** |  |
| `distance` | number | **required** |  |
| `segments` | integer | `64` | ≥ 4; ≤ 128 |
| `dissolve` | yes / no | `false` |  |
| `name` | text | `"buffer"` | up to 80 characters |

#### Select by attribute

The features whose attributes meet a condition, as a new layer: crop == "rice" and area_ha > 2 · name in ("A", "B") · contains(name, "farm")

*Tool id* `vquery`.

`POST /api/vector/query`: Select by attribute: the features whose attributes meet a condition, as a new layer.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layer` | text or object | **required** |  |
| `where` | text | **required** | up to 2000 characters |
| `name` | text | `"selection"` | up to 80 characters |

#### Overlay

Two polygon layers together: intersection (where both are), union (every piece of both), difference (A without B), symmetric difference, clip (A cut to B)

*Tool id* `voverlay`.

`POST /api/vector/overlay`: Overlay two layers: intersection (where both are), union (every piece of both), difference (A without B),

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `a` | text or object | **required** |  |
| `b` | text or object | **required** |  |
| `how` | text | `"intersection"` | one of `intersection`, `union`, `difference`, `symmetric_difference`, `clip` |
| `name` | text | `"overlay"` | up to 80 characters |

#### Dissolve

Merge shapes: all into one, or one shape per value of a field (e.g. one per crop)

*Tool id* `vdissolve`.

`POST /api/vector/dissolve`: Merge shapes: all into one, or one per value of a field.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layer` | text or object | **required** |  |
| `field` | text (optional) | none | up to 200 characters |
| `name` | text | `"dissolved"` | up to 80 characters |

#### Geometry tools

Centroids, convex hull, simplify, merge layers, multipart to single parts, a fishnet grid, random points inside polygons

*Tool id* `vhelpers`.

`POST /api/vector/geom-op`: Geometry helpers: centroids, convex hull, simplify (m), merge layers, multipart → single parts, a fishnet

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `op` | text | **required** | one of `centroids`, `convex_hull`, `simplify`, `merge`, `explode`, `fishnet`, `random_points` |
| `layer` | text or object (optional) | none |  |
| `layers` | list of text or object | none | 0–20 items |
| `layer_names` | list of text | none | 0–20 items |
| `inside` | yes / no | `false` |  |
| `whole` | yes / no | `false` |  |
| `tolerance` | number | `10` |  |
| `cell` | number | `100` |  |
| `clip` | yes / no | `true` |  |
| `count` | integer | `100` | ≥ 1; ≤ 100000 |
| `per_feature` | yes / no | `false` |  |
| `seed` | integer (optional) | none |  |
| `name` | text | `""` | up to 80 characters |

### Spatial analysis

#### Zonal statistics

A raster's values summarised inside each polygon: mean, min, max… (e.g. mean NDVI of each field), or the % of each class of a land-cover map

*Tool id* `vzonal`.

`POST /api/vector/zonal`: Zonal statistics: each polygon with a raster's values summarised inside it (mean, min, max, std, median, sum,

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layer` | text or object | **required** |  |
| `raster` | text | **required** | up to 1000 characters |
| `band` | integer | `1` | ≥ 1 |
| `stats` | list of text | none |  |
| `categorical` | yes / no | `false` |  |
| `name` | text | `"zonal_stats"` | up to 80 characters |

#### Select by location

The features of a layer that intersect, are inside, contain, are apart from, or are within a distance of another layer (e.g. wells within 500 m of the river)

*Tool id* `vlocation`.

`POST /api/vector/select-location`: Select by location: the features of A that intersect / are within / contain / are apart from / are within

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `a` | text or object | **required** |  |
| `b` | text or object | **required** |  |
| `predicate` | text | `"intersects"` | one of `intersects`, `within`, `contains`, `disjoint`, `within_distance` |
| `distance` | number | `0` |  |
| `name` | text | `"selected"` | up to 80 characters |

#### Spatial join

Give each feature the attributes of the feature of another layer it overlaps, lies in, or is nearest to (e.g. each field gets its district's name)

*Tool id* `vsjoin`.

`POST /api/vector/spatial-join`: Spatial join: A's features with the attributes of the B feature they overlap most, lie within, or are nearest to.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `a` | text or object | **required** |  |
| `b` | text or object | **required** |  |
| `how` | text | `"intersects"` | one of `intersects`, `within`, `nearest` |
| `max_distance` | number (optional) | none |  |
| `name` | text | `"joined"` | up to 80 characters |

#### Count points in polygons

How many points fall in each polygon (e.g. wells per village), and the sum of a points' field

*Tool id* `vcount`.

`POST /api/vector/count-points`: Count points in polygons (and the sum of a points' field).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `polygons` | text or object | **required** |  |
| `points` | text or object | **required** |  |
| `sum_field` | text (optional) | none | up to 200 characters |
| `name` | text | `"point_counts"` | up to 80 characters |

#### Calculate geometry

Add area (m², hectares), perimeter or length (m) and the centroid (lon, lat) as fields, measured on the ground

*Tool id* `vgeometry`.

`POST /api/vector/geometry`: Calculate geometry: area (m², ha), perimeter or length (m), centroid (lon, lat) as fields.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layer` | text or object | **required** |  |
| `name` | text | `"with_geometry"` | up to 80 characters |

#### Join table to layer

Attach a table (CSV, Excel, Parquet) to a layer by a shared field, e.g. a yield table by field ID

*Tool id* `vtjoin`.

`POST /api/vector/join-table`: Join a table (CSV / Excel / Parquet) to a layer by a shared field: its columns become the features' attributes.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layer` | text or object | **required** |  |
| `table` | text | **required** | up to 1000 characters |
| `layer_field` | text | **required** | up to 200 characters |
| `table_field` | text | **required** | up to 200 characters |
| `name` | text | `"joined_table"` | up to 80 characters |

#### Spatial statistics

Kernel density (a heat map of points such as disease reports), hot spots (Getis-Ord Gi*), Moran's I and nearest-neighbour analysis

*Tool id* `vstats`.

`GET /api/vector/read`: A vector file of the workspace as GeoJSON (to put a tool's result on the map).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`POST /api/vector/spatial-stats`: Kernel density (a heat map raster), hot spots (Getis-Ord Gi*), Moran's I (global, and local clusters) and average

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `method` | text | **required** | one of `density`, `hotspots`, `moran`, `nearest` |
| `layer` | text or object | **required** |  |
| `field` | text (optional) | none | up to 200 characters |
| `distance` | number (optional) | none | > 0 |
| `k` | integer (optional) | none | ≥ 1; ≤ 100 |
| `fdr` | yes / no | `true` |  |
| `bandwidth` | number (optional) | none | > 0 |
| `cell` | number (optional) | none | > 0 |
| `kernel` | text | `"quartic"` | one of `quartic`, `gaussian` |
| `permutations` | integer | `999` | ≥ 99; ≤ 9999 |
| `area` | text or object (optional) | none |  |
| `name` | text | `""` | up to 80 characters |

#### Spatial autocorrelation (raster)

Whether similar values cluster in space: global Moran's I and Geary's C with significance, and maps of Local Moran's I clusters (hot, cold, outliers) and Getis-Ord Gi* hot spots

*Tool id* `rautocorr`.

`POST /api/raster/autocorrelation`: Global Moran's I / Geary's C of a band, and maps of Local Moran's I clusters (LISA) and Getis-Ord Gi* z-scores.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `radius` | integer | `1` | ≥ 1; ≤ 25 |
| `alpha` | number | `0.05` | > 0; < 0.5 |
| `db` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

#### Routing (roads)

On OpenStreetMap roads or your own: the quickest route through stops, what can be reached in 5, 10, 15 minutes, or each place's nearest hospital / market by travel time

*Tool id* `vroute`.

`POST /api/network/routing`: The quickest route through stops, service areas (minutes) or each incident's closest facility, on OpenStreetMap

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `op` | text | **required** | one of `route`, `service`, `closest` |
| `mode` | text | `"car"` | one of `car`, `bike`, `walk` |
| `points` | list of list of number | **required** | 1–5000 items |
| `facilities` | list of list of number | none | 0–5000 items |
| `facility_names` | list of text (optional) | none |  |
| `breaks` | list of number | none | 0–12 items |
| `roads` | object (optional) | none |  |
| `speed_field` | text (optional) | none |  |
| `speed_kmh` | number | `30` | > 0; ≤ 200 |
| `margin_km` | number | `3` | ≥ 0; ≤ 100 |
| `name` | text | `""` | up to 80 characters |

### Fuzzy & suitability

#### AHP weights & overlay

Weigh factors by comparing them in pairs (Saaty's AHP) with a consistency check, then combine the scored layers into a suitability map (site selection, groundwater, flood risk…)

*Tool id* `ahp`.

`POST /api/ahp/overlay`: AHP weights (from the matrix, or given) and the weighted overlay of the scored factors.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `factors` | list of AhpFactor | **required** | 2–15 items |
| `matrix` | list of list of number (optional) | none |  |
| `weights` | list of number (optional) | none |  |
| `title` | text | `"Suitability"` | up to 60 characters |
| `name` | text | `"ahp_suitability"` | up to 80 characters |

*AhpFactor*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `name` | text (optional) | none |  |
| `rising` | yes / no | `true` |  |
| `scores` | object (number values) (optional) | none |  |

`POST /api/ahp/weights`: Weights and consistency of a pairwise comparison matrix (instant, not a job).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `matrix` | list of list of number | **required** |  |

#### Fuzzy membership

Turn a layer into gradual membership from 0 to 1 instead of a hard yes / no: slope, NDVI or rainfall, or the distance to roads or rivers (fuzzy distance)

*Tool id* `fmember`.

`POST /api/fuzzy/membership`: A 0–1 membership raster: of a raster's values, or (fuzzy distance) of the distance in metres to a layer.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text (optional) | none |  |
| `band` | integer | `1` | ≥ 1 |
| `layer` | text or object (optional) | none |  |
| `like` | text (optional) | none |  |
| `res` | number | `30` | > 0; ≤ 5000 |
| `fn` | text | **required** | one of `linear`, `small`, `large`, `gaussian`, `near`, `sigmoid`, `trapezoid`, `none` |
| `params` | Params | none |  |
| `name` | text | `"membership"` | up to 80 characters |

*Params*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `a` | number (optional) | none |  |
| `b` | number (optional) | none |  |
| `c` | number (optional) | none |  |
| `d` | number (optional) | none |  |
| `mid` | number (optional) | none |  |
| `spread` | number (optional) | none |  |
| `sigma` | number (optional) | none |  |
| `slope` | number (optional) | none |  |

#### Fuzzy overlay (suitability)

Suitability from several layers (slope, soil pH, rainfall, NDVI, distance to water …): each turned into membership with its own function and weight, then combined into a 0–1 map with suitability classes

*Tool id* `foverlay`.

`POST /api/fuzzy/overlay`: Fuzzy overlay / suitability: every layer turned into membership by its own function, then combined (AND, OR,

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layers` | list of OverlayLayer | **required** | 1–20 items |
| `op` | text | `"gamma"` | one of `and`, `or`, `product`, `sum`, `gamma`, `weighted_sum`, `weighted_product` |
| `gamma` | number | `0.9` | ≥ 0; ≤ 1 |
| `classes` | yes / no | `true` |  |
| `name` | text | `"suitability"` | up to 80 characters |

*OverlayLayer*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `fn` | text | `"none"` | one of `linear`, `small`, `large`, `gaussian`, `near`, `sigmoid`, `trapezoid`, `none` |
| `params` | Params | none |  |
| `weight` | number | `1` | ≥ 0 |
| `name` | text | `""` | up to 120 characters |

*Params*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `a` | number (optional) | none |  |
| `b` | number (optional) | none |  |
| `c` | number (optional) | none |  |
| `d` | number (optional) | none |  |
| `mid` | number (optional) | none |  |
| `spread` | number (optional) | none |  |
| `sigma` | number (optional) | none |  |
| `slope` | number (optional) | none |  |

#### Fuzzy boundary & uncertainty

From a membership or probability map: where the boundary is uncertain (the transition zone), nested zones (α-cuts) and a smooth crisp boundary as polygons, plus an uncertainty map

*Tool id* `fboundary`.

`POST /api/fuzzy/boundary`: From a membership or probability map: the uncertainty map, α-cut zones, the crisp boundary and the transition

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `alpha` | number | `0.5` | > 0; < 1 |
| `cuts` | list of number | none | 0–9 items |
| `low` | number | `0.25` | ≥ 0; ≤ 1 |
| `high` | number | `0.75` | ≥ 0; ≤ 1 |
| `blur` | number | `1.0` | ≥ 0; ≤ 20 |
| `smooth` | integer | `2` | ≥ 0; ≤ 6 |
| `min_area_m2` | number | `0` | ≥ 0 |
| `simplify_m` | number | `0` | ≥ 0 |
| `name` | text | `""` | up to 80 characters |

#### Fuzzy classification (c-means)

Each pixel's membership in every class instead of one class only (mixed pixels): fuzzy c-means gives a membership map per class, the hard class and an uncertainty map

*Tool id* `fcmeans`.

`POST /api/fuzzy/cmeans`: Fuzzy classification (fuzzy c-means): a membership band per class, the hard class and the uncertainty.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `k` | integer | `4` | ≥ 2; ≤ 12 |
| `bands` | list of integer (optional) | none |  |
| `m` | number | `2.0` | > 1; ≤ 5 |
| `name` | text | `""` | up to 80 characters |

### Image features

#### Local statistics

Per pixel, over its 3×3 … 31×31 neighbourhood: mean, median, std, variance, min, max, range, coefficient of variation, entropy, skewness, edges: features for SAR and optical classification

*Tool id* `rlocal`.

`POST /api/raster/localstats`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `windows` | list of integer | none | 1–8 items |
| `stats` | list of text | none | 1–12 items |
| `db` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

#### Texture (GLCM)

Grey-level co-occurrence texture per pixel (Haralick): contrast, dissimilarity, homogeneity, energy, ASM, correlation, entropy, mean, variance, averaged over four directions

*Tool id* `rglcm`.

`POST /api/raster/glcm`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `window` | integer | `7` | ≥ 3; ≤ 63 |
| `distance` | integer | `1` | ≥ 1; ≤ 10 |
| `levels` | integer | `16` | ≥ 4; ≤ 64 |
| `features` | list of text | none | 1–9 items |
| `db` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

#### Edges & boundaries

Sobel (x, y, magnitude, direction), Canny edges, Laplacian of Gaussian, gradient magnitude and directional gradients: field boundaries, shorelines, roads

*Tool id* `redges`.

`POST /api/raster/edges`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `which` | list of text | none | 1–… items |
| `sigma` | number | `1.0` | ≥ 0; ≤ 20 |
| `low` | number | `0.1` | > 0; < 1 |
| `high` | number | `0.2` | > 0; ≤ 1 |
| `db` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

#### Multi-scale features

Gaussian scale space at several scales: smoothed image, gradient, Laplacian of Gaussian, difference of Gaussians, local mean and std, as one feature stack for classification

*Tool id* `rmulti`.

`POST /api/raster/multiscale`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `sigmas` | list of number | none | 1–8 items |
| `features` | list of text | none | 1–… items |
| `db` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

#### Morphology

Shape operations on masks and class maps (after a threshold or a classification): erosion, dilation, opening, closing, gradient, top-hat, black-hat; remove small objects, fill holes, majority filter

*Tool id* `rmorph`.

`POST /api/raster/morphology`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `op` | text | `"opening"` | one of `erosion`, `dilation`, `opening`, `closing`, `gradient`, `tophat`, `blackhat`, `remove_small`, `fill_holes`, `majority`, `boundary` |
| `size` | integer | `3` | ≥ 1; ≤ 101 |
| `shape` | text | `"disk"` | one of `disk`, `square`, `cross` |
| `value` | number (optional) | none |  |
| `min_px` | integer | `50` | ≥ 1; ≤ 1e+07 |
| `iterations` | integer | `1` | ≥ 1; ≤ 20 |
| `name` | text | `""` | up to 80 characters |

### Objects

#### Superpixels (SLIC)

Groups pixels into small homogeneous segments (SLIC) instead of treating each pixel alone: segment ids, boundaries, the mean image per segment and polygons with their means, for object-based classification

*Tool id* `rslic`.

`POST /api/raster/slic`: SLIC superpixels: segment ids, their boundaries, the mean image per segment and (optionally) polygons with means.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bands` | list of integer (optional) | none |  |
| `n_segments` | integer | `1000` | ≥ 4; ≤ 200000 |
| `compactness` | number | `0.3` | > 0; ≤ 100 |
| `sigma` | number (optional) | none | ≥ 0; ≤ 10 |
| `polygons` | yes / no | `true` |  |
| `db` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

#### Connected components

Separate objects in a mask or one class (water bodies, fields, buildings): an id per object, a table of their size, area and centre, and polygons

*Tool id* `rcomp`.

`POST /api/raster/components`: Connected regions of a mask or one class: an id per region, a table (pixels, hectares, centroid) and polygons.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `value` | number (optional) | none |  |
| `connectivity` | integer | `8` |  |
| `min_px` | integer | `1` | ≥ 1; ≤ 1e+07 |
| `polygons` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

### Raster & terrain

#### Terrain: slope, aspect, hillshade

From a DEM: slope in degrees, aspect (the direction a slope faces, degrees from north) and a shaded relief

*Tool id* `rterrain`.

`POST /api/raster/terrain`: Slope (degrees), aspect (degrees from north) and hillshade of a DEM.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `products` | list of text | none |  |
| `azimuth` | number | `315` | ≥ 0; ≤ 360 |
| `altitude` | number | `45` | ≥ 1; ≤ 90 |
| `z_factor` | number | `1.0` | > 0 |

#### Viewshed & line of sight

What can be seen from one or more points on a DEM (and by how many), or whether A can see B, with the earth's curvature

*Tool id* `rview`.

`POST /api/raster/line-of-sight`: Can A see B? The line split into seen / hidden parts and the profile between them.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `a` | list of number | **required** |  |
| `b` | list of number | **required** |  |
| `observer_h` | number | `1.7` | ≥ 0; ≤ 10000 |
| `target_h` | number | `0.0` | ≥ 0; ≤ 10000 |
| `curvature` | yes / no | `true` |  |
| `name` | text | `"line_of_sight"` | up to 80 characters |

`POST /api/raster/viewshed`: What can be seen from the observers: 1 / 0 for one, how many see each cell for several.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `observers` | list of list of number | **required** | 1–200 items |
| `observer_h` | number | `1.7` | ≥ 0; ≤ 10000 |
| `target_h` | number | `0.0` | ≥ 0; ≤ 10000 |
| `max_dist_m` | number (optional) | none | > 0; ≤ 500000 |
| `curvature` | yes / no | `true` |  |
| `name` | text | `"viewshed"` | up to 80 characters |

#### LiDAR point cloud

A .las / .laz point cloud into a ground model (DTM), surface (DSM), height of trees and buildings (CHM), point density and tree tops

*Tool id* `rlidar`.

`GET /api/lidar/files`: The .las / .laz files in a folder (and its subfolders, one level down).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` (query) | text | **required** |  |

`POST /api/lidar/grid`: A point cloud gridded into a ground model (DTM), surface (DSM), height above ground (CHM), density, intensity; tree tops.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `res` | number | `1.0` | > 0.05; ≤ 1000 |
| `products` | list of text | none |  |
| `crs` | text (optional) | none |  |
| `ground_window_m` | number | `40` | ≥ 2; ≤ 500 |
| `tree_min_h` | number | `2.0` | ≥ 0; ≤ 200 |
| `tree_window_m` | number | `5.0` | ≥ 1; ≤ 100 |
| `drop_noise` | yes / no | `true` |  |
| `name` | text | `""` | up to 80 characters |

`GET /api/lidar/info`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### Contours

Contour lines of a DEM every few metres, as a vector layer with each line's height

*Tool id* `rcontours`.

`POST /api/raster/contours`: Contour lines every `interval` (e.g. 10 m) of a DEM, as a vector layer.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `interval` | number | **required** | > 0 |
| `base` | number | `0` |  |
| `band` | integer | `1` | ≥ 1 |
| `name` | text | `"contours"` | up to 80 characters |

#### Reclassify

Turn ranges of values into classes, e.g. NDVI < 0.2 bare, 0.2–0.5 sparse, above 0.5 dense vegetation

*Tool id* `rreclass`.

`POST /api/raster/reclassify`: Ranges of values become classes, e.g. NDVI < 0.2 → 1 (bare), 0.2–0.5 → 2 (sparse), ≥ 0.5 → 3 (dense).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `rules` | list of ReclassRule | **required** | 1–50 items |
| `band` | integer | `1` | ≥ 1 |
| `name` | text | `"classes"` | up to 80 characters |

*ReclassRule*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `min` | number (optional) | none |  |
| `max` | number (optional) | none |  |
| `value` | integer | **required** | ≥ 1; ≤ 255 |
| `label` | text | `""` | up to 80 characters |

#### Change detection

What changed between two dates: the difference and % change (e.g. NDVI 2023 → 2024), or for land-cover maps every from → to change with its area

*Tool id* `rchange`.

`POST /api/raster/change`: Change detection between two dates: the difference and % change, or for class maps from → to with the area of

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `before` | text | **required** |  |
| `after` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `categorical` | yes / no | `false` |  |
| `resampling` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |

#### Clip raster

Cut a raster to the polygons of a layer (or keep only what is outside them)

*Tool id* `rclip`.

`POST /api/raster/clip`: Cut a raster to polygons (outside becomes nodata; crop shrinks it to them; invert keeps the outside).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `area` | object | **required** |  |
| `crop` | yes / no | `true` |  |
| `invert` | yes / no | `false` |  |
| `name` | text | `"clipped"` | up to 80 characters |

#### Resample / reproject

Change a raster's pixel size or coordinate system with nearest, bilinear, cubic (bicubic), lanczos, average, mode and more

*Tool id* `rresample`.

`POST /api/raster/resample`: A raster on a new pixel size, scale or CRS, with a chosen method (nearest, bilinear, cubic, lanczos, average,

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `res` | number (optional) | none | > 0 |
| `scale` | number (optional) | none | > 0; ≤ 16 |
| `crs` | text (optional) | none | pattern `^EPSG:\d{4,6}$` |
| `method` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |
| `name` | text | `"resampled"` | up to 80 characters |

#### Enhance image

Improve an image for viewing, computer vision or embeddings: contrast stretch, equalisation, CLAHE, gamma, denoise, sharpen, edges, texture, upscale ×2 / ×4 (cubic, lanczos), majority filter for class maps

*Tool id* `renhance`.

`POST /api/raster/enhance`: Image enhancement for computer vision and embeddings: contrast stretch, histogram equalisation, CLAHE, gamma,

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `steps` | list of EnhanceStep | none | 0–12 items |
| `bands` | list of integer (optional) | none |  |
| `upscale` | integer | `1` | ≥ 1; ≤ 4 |
| `upscale_method` | text | `"cubic"` | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |
| `name` | text | `"enhanced"` | up to 80 characters |

*EnhanceStep*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `op` | text | **required** | one of `stretch`, `equalize`, `clahe`, `gamma`, `median`, `gaussian`, `sharpen`, `sobel`, `laplacian`, `focal_mean`, `focal_std`, `focal_min`, `focal_max`, `majority` |
| `size` | integer | `3` | ≥ 1; ≤ 31 |
| `sigma` | number | `1.0` | > 0; ≤ 20 |
| `amount` | number | `1.0` | ≥ 0; ≤ 10 |
| `gamma` | number | `1.2` | > 0; ≤ 10 |
| `low` | number | `2` | ≥ 0; ≤ 50 |
| `high` | number | `98` | ≥ 50; ≤ 100 |
| `tiles` | integer | `8` | ≥ 1; ≤ 64 |
| `clip` | number | `0.01` | > 0; ≤ 1 |

#### Raster calculator

Map algebra over bands of several rasters: (B − A) / (B + A), where(A > 0.3, 1, 0), NDVI 2025 − NDVI 2018 > 0.1 …

*Tool id* `rcalc`.

`POST /api/raster/calc`: Raster calculator: an expression over bands of several rasters (A, B, …), e.g. (B - A) / (B + A),

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `variables` | object (CalcVar values) | **required** |  |
| `expression` | text | **required** | up to 1000 characters |
| `resampling` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |
| `name` | text | `"calc"` | up to 80 characters |

*CalcVar*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `scale` | number (optional) | none |  |

### Assess

#### Area statistics

Hectares, km² and % of each class of a land-cover or classified map, for the whole map or inside an area, as a table

*Tool id* `areastats`.

`POST /api/assess/area-stats`: Hectares, km² and % of each class of a class map (inside an area, if given), as a table.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `area` | text or object (optional) | none |  |
| `name` | text | `"area_statistics"` | up to 80 characters |

#### Accuracy assessment

How good is a classified map? Random points per class, label them by looking at imagery, then a confusion matrix, kappa and corrected class areas with confidence intervals (Olofsson et al.)

*Tool id* `accuracy`.

`POST /api/assess/accuracy`: Confusion matrix, overall / user's / producer's accuracy, kappa, and area estimates with 95 % confidence

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `points` | text or object | **required** |  |
| `ref_field` | text | `"reference"` | up to 200 characters |
| `band` | integer | `1` | ≥ 1 |
| `area` | text or object (optional) | none |  |
| `name` | text | `"accuracy"` | up to 80 characters |

`GET /api/assess/report`: An HTML report made by a tool (in analysis/), to open in a new tab.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`POST /api/assess/sample`: Stratified random points by map class, each with the map's class and an empty `reference` field to label.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `per_class` | integer | `50` | ≥ 1; ≤ 5000 |
| `total` | integer (optional) | none | ≥ 2; ≤ 50000 |
| `min_per_class` | integer | `20` | ≥ 0; ≤ 5000 |
| `area` | text or object (optional) | none |  |
| `seed` | integer (optional) | none |  |
| `name` | text | `"accuracy_points"` | up to 80 characters |

### Conversion

#### Raster to polygon

Areas of equal value of a class raster (land cover, a classified image, reclassified NDVI) as polygons with their class and area in hectares

*Tool id* `r2poly`.

`POST /api/convert/raster-to-polygon`: Areas of equal value (classes) of a raster as polygons with value, class name and area (ha).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `band` | integer | `1` | ≥ 1 |
| `values` | list of integer (optional) | none | 0–255 items |
| `min_area` | number | `0` | ≥ 0 |
| `simplify` | number | `0` | ≥ 0 |
| `dissolve` | yes / no | `false` |  |
| `diagonal` | yes / no | `false` |  |
| `name` | text | `"polygons"` | up to 80 characters |

#### Raster to polyline

Lines from a raster: the boundaries between classes, or the centrelines of thin features (roads, rivers, canals, field bunds) in a mask

*Tool id* `r2line`.

`POST /api/convert/raster-to-polyline`: Lines from a raster: the boundaries between classes, or the centrelines of thin shapes (roads, rivers) in a mask.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `mode` | text | `"boundaries"` | one of `boundaries`, `centrelines` |
| `band` | integer | `1` | ≥ 1 |
| `values` | list of integer (optional) | none | 0–255 items |
| `simplify` | number | `0` | ≥ 0 |
| `min_length` | number | `0` | ≥ 0 |
| `name` | text | `"lines"` | up to 80 characters |

#### Raster to point

A point at the centre of each pixel (or every n-th pixel) with the values of its bands, e.g. sample points for training or a table

*Tool id* `r2point`.

`POST /api/convert/raster-to-point`: A point at the centre of each pixel with data (or every n-th), with the value of each band.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `bands` | list of integer (optional) | none | 0–50 items |
| `step` | integer | `1` | ≥ 1; ≤ 1000 |
| `name` | text | `"points"` | up to 80 characters |

#### Vector to raster

Burn a layer into a GeoTIFF (rasterize): a field's values (text becomes classes with names), presence, how many points fall in each cell, a 0 / 1 mask (buffered, inverted) or the distance to the nearest shape

*Tool id* `rasterize`.

`POST /api/convert/rasterize`: A vector layer burnt into a GeoTIFF: a field's values (text → classes with names), presence, a count of shapes

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layer` | text or object | **required** |  |
| `mode` | text | `"value"` | one of `value`, `presence`, `count`, `mask`, `distance` |
| `field` | text (optional) | none | up to 200 characters |
| `res` | number (optional) | none | > 0 |
| `like` | text (optional) | none |  |
| `all_touched` | yes / no | `false` |  |
| `buffer_m` | number | `0` | ≥ 0; ≤ 1e+06 |
| `invert` | yes / no | `false` |  |
| `max_distance_m` | number (optional) | none | > 0 |
| `name` | text | `"rasterized"` | up to 80 characters |

#### Convert features

Change the geometry type: polygons ↔ lines, vertices → points, points → lines (e.g. GPS tracks), points every n metres along lines, lines → segments, bounding boxes

*Tool id* `vconvert`.

`POST /api/convert/features`: Feature conversions: polygons → lines, lines → polygons, vertices → points, points → lines (ordered, grouped),

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `op` | text | **required** | one of `polygons_to_lines`, `lines_to_polygons`, `vertices_to_points`, `points_to_lines`, `points_along_lines`, `split_lines`, `bounding_boxes` |
| `layer` | text or object | **required** |  |
| `group_by` | text (optional) | none | up to 200 characters |
| `order_by` | text (optional) | none | up to 200 characters |
| `close` | yes / no | `false` |  |
| `distance` | number | `100` | > 0 |
| `whole` | yes / no | `false` |  |
| `name` | text | `""` | up to 80 characters |

#### Georeference

Put a scanned map, a plan, a photo, a raster or a vector layer without coordinates (or in the wrong place) on the map: click a place on it, then the same place on the map, 3 or more times

*Tool id* `georef`.

`POST /api/georef/fit`: How well the control points fit (residual of each, RMSE), before warping.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `image` | text | **required** |  |
| `points` | list of GeorefPoint | **required** | 3–200 items |
| `method` | text | `"affine"` | one of `affine`, `poly2`, `tps` |
| `res` | number (optional) | none | > 0 |
| `name` | text | `"georeferenced"` | up to 80 characters |

*GeorefPoint*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `px` | number | **required** |  |
| `py` | number | **required** |  |
| `lon` | number | **required** | ≥ -180; ≤ 180 |
| `lat` | number | **required** | ≥ -90; ≤ 90 |

`GET /api/georef/image`: The picture itself, to show and click on (PNG / JPG as they are; others as a PNG preview).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`GET /api/georef/info`: The size of a picture or raster already in the workspace, to georeference it.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`POST /api/georef/upload`: A picture to georeference (PNG, JPG, TIFF), kept in uploads/georef/.

`POST /api/georef/vector`: A vector layer placed by control points (its own coordinates ↔ the map): the moved layer, RMSE and residuals.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `geojson` | object | **required** |  |
| `points` | list of VectorPoint | **required** | 3–200 items |
| `method` | text | `"affine"` | one of `affine`, `poly2`, `tps` |

*VectorPoint*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `x` | number | **required** |  |
| `y` | number | **required** |  |
| `lon` | number | **required** | ≥ -180; ≤ 180 |
| `lat` | number | **required** | ≥ -90; ≤ 90 |

`POST /api/georef/warp`: The picture as a GeoTIFF placed by the control points.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `image` | text | **required** |  |
| `points` | list of GeorefPoint | **required** | 3–200 items |
| `method` | text | `"affine"` | one of `affine`, `poly2`, `tps` |
| `res` | number (optional) | none | > 0 |
| `name` | text | `"georeferenced"` | up to 80 characters |

*GeorefPoint*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `px` | number | **required** |  |
| `py` | number | **required** |  |
| `lon` | number | **required** | ≥ -180; ≤ 180 |
| `lat` | number | **required** | ≥ -90; ≤ 90 |

### Output

#### Export data

Save any layer to your computer: GeoTIFF, PNG, Shapefile, GeoPackage (several layers in one file), GeoJSON, KML

*Tool id* `export`.

`POST /api/layers/export`: Runs as a background job (progress + cancel). Returns the job; its result holds the download URL.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `format` | text | **required** |  |
| `name` | text | `"layer"` |  |
| `band_map` | object (integer values) | none |  |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |
| `index` | text (optional) | none |  |
| `formula` | text (optional) | none | up to 500 characters |
| `composite` | text (optional) | none |  |
| `band` | integer (optional) | none |  |
| `rgb` | list of integer (optional) | none |  |
| `pca` | yes / no | `false` |  |
| `stretch` | text | `"fixed"` |  |
| `vmin` | number (optional) | none |  |
| `vmax` | number (optional) | none |  |
| `cmap` | text (optional) | none |  |
| `method` | text | `"equal"` |  |
| `classes` | integer | `5` | ≥ 2; ≤ 20 |
| `breaks` | list of number (optional) | none |  |
| `sieve` | integer | `8` | ≥ 0; ≤ 10000 |
| `clip` | object (optional) | none |  |
| `folder` | text (optional) | none | up to 1000 characters |

`POST /api/vector/export`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `geojson` | object | **required** |  |
| `format` | text | **required** |  |
| `name` | text | `"layer"` |  |
| `clip` | object (optional) | none |  |
| `folder` | text (optional) | none | up to 1000 characters |
| `layer_name` | text (optional) | none | up to 200 characters |
| `more` | list of VectorLayer | none | 0–200 items |
| `crs` | text (optional) | none | up to 20000 characters |

*VectorLayer*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `name` | text | `"layer"` | up to 200 characters |
| `geojson` | object | **required** |  |

#### Downloads & jobs

Background downloads, logs and output files

*Tool id* `jobs`.

### Automate

#### Assistant

Say what you want done; it plans it with the app's tools from your data, you check the plan, then it runs it step by step and fixes what goes wrong. A free local model (Ollama), a free online model (Hugging Face, Groq, Gemini…) or Claude, with your own key

*Tool id* `assistant`.

`GET /api/assistant/api-models`: The models an online service offers (with the saved key).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `preset` (query) | text | **required** |  |
| `base` (query) | text | `""` |  |

`POST /api/assistant/continue`: Replan after a step failed or made something wrong: the steps still to run (the done ones are kept).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `messages` | list of object | **required** | 1–60 items |
| `context` | object | none |  |
| `conv_id` | text | `""` | up to 40 characters |
| `workflow` | object | **required** |  |
| `done` | list of object | none | 0–50 items |
| `failed` | object (optional) | none |  |

`POST /api/assistant/explain`: A few sentences on what a run's results mean, from its steps' results and files (only their numbers).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `request` | text | **required** | up to 4000 characters |
| `steps` | list of object | **required** | 1–40 items |

`POST /api/assistant/log`: An event of a conversation (a step done or failed, a run finished) for its transcript.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `conv_id` | text | **required** | up to 40 characters |
| `event` | object | **required** |  |

`GET /api/assistant/memory`

`POST /api/assistant/memory/lesson`: A step that failed, then worked after a fix: kept so later plans avoid the error.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `endpoint` | text | **required** | up to 200 characters |
| `error` | text | **required** | up to 2000 characters |
| `fix` | text | `""` | up to 2000 characters |
| `body` | object | none |  |

`POST /api/assistant/plan`: The request (and the conversation so far) planned as a workflow of the app's tools; nothing runs.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `messages` | list of object | **required** | 1–60 items |
| `context` | object | none |  |
| `conv_id` | text | `""` | up to 40 characters |

`POST /api/assistant/pull`: Download a local model into Ollama, as a background job (progress, Cancel).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `model` | text | **required** | pattern `^[A-Za-z0-9._:/-]+$`; up to 100 characters |

`GET /api/assistant/status`

`POST /api/vector/save`: A map layer kept as a file in the workspace (uploads/layers/<id>.geojson), so tools and the Assistant can use it.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `layer_id` | text | **required** | pattern `^[A-Za-z0-9_-]{1,80}$` |
| `geojson` | object | **required** |  |

#### Workflows

Run a chain of tools again on new data: make a workflow from runs in the History (e.g. download → NDVI → export), then give it new inputs, once or for each layer or polygon

*Tool id* `workflows`.

`POST /api/agri/diagnose`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `photos` | list of text | **required** | 1–5000 items |
| `crop` | text | `"auto"` | up to 40 characters |
| `strict` | yes / no | `true` |  |
| `device` | text | `"auto"` | one of `auto`, `cpu`, `cuda`, `mps` |
| `name` | text | `"diagnosis"` | up to 80 characters |

`GET /api/history`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `q` (query) | text | `""` |  |
| `status` (query) | text | `""` |  |
| `limit` (query) | integer | `200` |  |

`POST /api/sar/process`: The SAR workflow: every source processed with the ticked steps (skipping what its producer already did), then

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `sources` | list of Source | **required** | 1–40 items |
| `steps` | list of text | none | 0–13 items |
| `pols` | list of text (optional) | none |  |
| `kind` | text | `"sigma0"` | one of `sigma0`, `beta0`, `gamma0` |
| `speckle` | Speckle | none |  |
| `dem` | text (optional) | none |  |
| `crs` | text (optional) | `"auto"` | up to 200 characters |
| `res` | number (optional) | none | > 0; ≤ 5000 |
| `like` | text (optional) | none |  |
| `aoi` | object (optional) | none |  |
| `clip` | yes / no | `false` |  |
| `db` | yes / no | `true` |  |
| `masks` | yes / no | `false` |  |
| `quality` | yes / no | `true` |  |
| `flatten_method` | text | `"area"` | one of `area`, `angular` |
| `normalise_ref` | number | `40.0` | ≥ 10; ≤ 60 |
| `normalise_n` | number (optional) | none | ≥ 0; ≤ 4 |
| `join_frames` | yes / no | `true` |  |
| `multitemporal` | yes / no | `false` |  |
| `mt_size` | integer | `7` | ≥ 3; ≤ 15 |
| `temporal` | list of text | none |  |
| `change` | yes / no | `false` |  |
| `change_threshold` | number | `3.0` | > 0; ≤ 20 |
| `water_db` | number | `-18.0` | ≥ -40; ≤ 0 |
| `features` | list of text | none |  |
| `name` | text | `""` | up to 80 characters |

*Source*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `product` | text (optional) | none | up to 2000 characters |
| `scene` | text (optional) | none | up to 200 characters |
| `raster` | text (optional) | none | up to 2000 characters |

*Speckle*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `method` | text | `"none"` | one of `none`, `boxcar`, `median`, `lee`, `refined_lee`, `lee_sigma`, `frost`, `gamma_map` |
| `size` | integer | `5` | ≥ 3; ≤ 15 |
| `looks` | number (optional) | none | > 0; ≤ 1000 |

`POST /api/sar/series`: Time-series statistics of several dates, and the change from the first to the last (log-ratio, classes, flooding).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `rasters` | list of text | **required** | 2–200 items |
| `stats` | list of text | none |  |
| `change` | yes / no | `false` |  |
| `pol` | text | `"VV"` | one of `VV`, `VH`, `HH`, `HV` |
| `threshold_db` | number | `3.0` | > 0; ≤ 20 |
| `water_db` | number | `-18.0` | ≥ -40; ≤ 0 |
| `multitemporal` | yes / no | `false` |  |
| `mt_size` | integer | `7` | ≥ 3; ≤ 15 |
| `name` | text | `""` | up to 80 characters |

`GET /api/workflows`

`POST /api/workflows/cautions`: What may go wrong when the workflow runs (from its settings alone): shown before running.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `workflow` | object | **required** |  |
| `saved` | yes / no | `true` |  |

`GET /api/workflows/{wid}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `wid` (path) | text | **required** |  |

`POST /api/workflows/evaluate`: Decide a step's conditions from the results so far: each one true, false or undecided, with why.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `conditions` | list of object | **required** | 0–20 items |
| `steps` | object | none |  |
| `wid` | text (optional) | none | up to 64 characters |
| `step` | integer | `0` | ≥ 0; ≤ 49 |
| `own` | yes / no | `false` |  |

`POST /api/workflows/from-history`: A workflow made from History runs (not saved yet: the app shows it to name and save).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_ids` | list of text | **required** | 1–50 items |
| `name` | text | `""` |  |

`POST /api/workflows/observe`: The built-in checks of what a step made: critical problems (an empty image, impossible index values, no rows…)

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `paths` | list of text | **required** | 0–40 items |
| `result` | object (optional) | none |  |

`GET /api/workflows/schedules`: Every schedule, its next run, and which are due now (the app runs those).

## Analysis ▸ Agri

### Agri

#### Diagnose crop disease

Find the disease on leaf photos of 43 crops (apple, mango, rice, tomato, maize, wheat…): the crop is recognised, then its model gives the top 3 diseases, with a second opinion for maize, rice, potato and sugarcane. Unclear photos are refused; photos with GPS become a disease map

*Tool id* `agridisease`.

`POST /api/agri/models`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` | text (optional) | none | up to 2000 characters |
| `source` | text (optional) | none | pattern `^hub$` |

`GET /api/agri/photo`: A photo as a JPEG, upright and at most size pixels (thumbnails in the tool, the full photo with size=0).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `size` (query) | integer | `256` |  |

`GET /api/agri/photos/folder`: The photos in a folder (and its sub-folders when recursive), up to 5,000.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `recursive` (query) | yes / no | `false` |  |

`POST /api/agri/photos/upload`: Photos added from the computer: kept in the workspace's uploads/photos/<batch>/.

#### Crop disease guide

Symptoms, treatment and pests for each crop and disease, from a knowledge base of about 9,000 expert questions and answers; searchable

*Tool id* `agriguide`.

`GET /api/agri/guide/diseases`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `crop` (query) | text | **required** |  |

`GET /api/agri/guide/search`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `crop` (query) | text | **required** |  |
| `disease` (query) | text (optional) | none |  |
| `q` (query) | text (optional) | none |  |
| `section` (query) | text (optional) | none |  |
| `limit` (query) | integer | `200` |  |

## Analysis ▸ Embeddings

### Embeddings

#### Classify with embedding model

Map any embedding layer (another area or year) with a model from Train embedding model, with a confidence band

*Tool id* `embpredict`.

#### Convert embeddings

…

*Tool id* `embconvert`.

`POST /api/emb/convert`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `to` | text | **required** | one of `float32`, `float16`, `int8-aef`, `int8-scaled` |
| `normalise` | yes / no | `false` |  |
| `name` | text | `"embedding"` | up to 80 characters |

`GET /api/emb/format`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### Download embeddings

Free, open AI embeddings for any area: Google AlphaEarth (64-D) and TESSERA (128-D), 10 m, 2017–2025. See which years exist for your area and download them as a GeoTIFF, with a colour view

*Tool id* `embed`.

`POST /api/emb/available`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `clip` | object | **required** |  |

`POST /api/emb/estimate`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `clip` | object | **required** |  |
| `source` | text | `"aef"` | one of `aef`, `tessera` |
| `res` | number | `10` | ≥ 10; ≤ 160 |

`POST /api/emb/fetch`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `clip` | object | **required** |  |
| `source` | text | `"aef"` | one of `aef`, `tessera` |
| `res` | number | `10` | ≥ 10; ≤ 160 |
| `year` | integer | `2024` | ≥ 2017; ≤ 2030 |
| `name` | text | `"embedding"` | up to 80 characters |
| `colour` | yes / no | `true` |  |
| `resampling` | text (optional) | none | one of `nearest`, `bilinear`, `cubic`, `bicubic`, `cubic_spline`, `lanczos`, `average`, `mode`, `min`, `max`, `med`, `q1`, `q3` |

#### Explore embeddings

Find places similar to the ones you click (cosine similarity), make a colour view, or classify any embedding layer

*Tool id* `embexplore`.

`POST /api/emb/colour`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `points` | list of list of number (optional) | none | 0–500 items |
| `name` | text | `"similarity"` | up to 80 characters |

`POST /api/emb/similar`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `points` | list of list of number (optional) | none | 0–500 items |
| `name` | text | `"similarity"` | up to 80 characters |

#### Train embedding model

Train one of eleven light segmentation models (TinyUNet, ENet, DABNet, LEDNet… 0.15–0.95 M parameters) on an embedding layer and your labelled polygons, points or class raster: all bands used, 256 × 256 patches, early stopping, a report, and the class map

*Tool id* `embtrain`.

`POST /api/emb/train`: Train a light segmentation model on an embedding layer and labels in one go: 256 × 256 patches (all bands), training

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `ground_truth` | object | **required** |  |
| `clip` | object (optional) | none |  |
| `arch` | text | `"light_dabnet"` | up to 40 characters |
| `patch_px` | integer | `256` | ≥ 32; ≤ 1024 |
| `overlap` | number | `0.25` | ≥ 0; ≤ 0.75 |
| `params` | object | none |  |
| `name` | text | `"embedding_model"` | up to 80 characters |
| `map` | yes / no | `true` |  |
| `class_colors` | object (text values) (optional) | none |  |

## Analysis ▸ Forecast

### Forecast

#### Get AQI & weather data

Hourly air quality (PM2.5, PM10, NO2, SO2, CO, O3 and the Indian AQI) and weather (temperature, humidity, wind, rain, cloud, sunshine) at your points: up to 92 past days plus the weather forecast for the next days. Free, from Open-Meteo (CAMS and ECMWF models)

*Tool id* `fcdata`.

`POST /api/forecast/getdata`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `points` | list of object | **required** | 1–500 items |
| `past_days` | integer | `60` | ≥ 1; ≤ 92 |
| `forecast_days` | integer | `7` | ≥ 0; ≤ 16 |
| `air` | yes / no | `true` |  |
| `weather` | yes / no | `true` |  |
| `name` | text | `"aqi_weather"` | up to 80 characters |

#### Forecast with a model

Use a model from Train forecasting model on newer data (the same columns, e.g. today's AQI and the weather forecast): the next hours / days / months with an uncertainty band, as a table, a chart and points on the map

*Tool id* `fcrun`.

`GET /api/forecast/models`

`POST /api/forecast/run`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `model` | text | **required** |  |
| `table` | text | **required** |  |
| `horizon` | integer (optional) | none | ≥ 1; ≤ 5000 |
| `name` | text | `"forecast"` | up to 80 characters |

#### Train forecasting model

Forecast the next hours, days or months of a value in a table (AQI per station, rainfall, humidity, sales…) from its past, the calendar, nearby stations and other inputs such as weather: LightGBM, XGBoost, Random Forest, linear and more, checked against simple baselines

*Tool id* `fctrain`.

`GET /api/forecast/describe`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

`POST /api/forecast/train`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `table` | text | **required** |  |
| `time_col` | text | **required** | up to 200 characters |
| `target` | text | **required** | up to 200 characters |
| `series_col` | text (optional) | none | up to 200 characters |
| `lat_col` | text (optional) | none | up to 200 characters |
| `lon_col` | text (optional) | none | up to 200 characters |
| `inputs` | list of text | none | 0–100 items |
| `freq` | text | `"auto"` | one of `auto`, `h`, `D`, `W`, `MS` |
| `horizon` | integer (optional) | none | ≥ 1; ≤ 5000 |
| `model` | text | `"auto"` | one of `auto`, `lightgbm`, `xgboost`, `hgb`, `rf`, `linear`, `seasonal`, `naive` |
| `backtests` | integer | `3` | ≥ 1; ≤ 10 |
| `future_inputs` | text | `"auto"` | one of `auto`, `known`, `repeat` |
| `strategy` | text | `"auto"` | one of `auto`, `recursive`, `direct` |
| `params` | object | none |  |
| `options` | object | none |  |
| `name` | text | `"forecast"` | up to 80 characters |

#### Sentinel-5P air quality

NO₂, CO, SO₂, formaldehyde, ozone, methane and aerosols from Sentinel-5P (TROPOMI), averaged over your area and dates, with a daily series

*Tool id* `s5p`.

`POST /api/s5p/average`: The mean of a Sentinel-5P product over an area and period (good-quality pixels), with a daily series.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `product` | text | `"no2"` |  |
| `bbox` | list of number | **required** | 4–4 items |
| `start` | text | **required** | pattern `^\d{4}-\d{2}-\d{2}$` |
| `end` | text | **required** | pattern `^\d{4}-\d{2}-\d{2}$` |
| `res` | number | `0.05` | ≥ 0.01; ≤ 1 |
| `name` | text | `""` | up to 80 characters |

`GET /api/s5p/products`

## Analysis ▸ SAR

### SAR

#### Flood map: ML refinement

A cleaner SAR flood map: a model (LightGBM or a U-Net) learns from the Flood & water map's confident pixels, using backscatter, texture, change and terrain, and decides the uncertain ones

*Tool id* `sarfloodml`.

`POST /api/sar/flood-ml`: The SAR flood map refined by a model trained on its own confident pixels (LightGBM or a U-Net).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `sar` | text | **required** |  |
| `confidence` | text (optional) | none |  |
| `classes` | text (optional) | none |  |
| `pre` | text (optional) | none |  |
| `dem` | text (optional) | none |  |
| `model` | text | `"lgbm"` | one of `lgbm`, `unet` |
| `hi` | number | `0.75` | > 0.5; < 1 |
| `lo` | number | `0.25` | > 0; < 0.5 |
| `epochs` | integer | `30` | ≥ 1; ≤ 300 |
| `name` | text | `"flood_ml"` | up to 80 characters |

#### SAR workflow

Sentinel-1 from a .SAFE / .zip, a Planetary Computer scene or a SAR layer: it says what is already done, you tick what you need (orbit, noise removal, calibration, speckle, terrain correction, flattening, angle normalisation, dB, grid, clip, quality layer, features, time series, multi-temporal filter, change)

*Tool id* `sarflow`.

`GET /api/products`

`POST /api/sar/inspect`: What the data is and the status of every processing step (done / needed / optional / not applicable).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `product` | text (optional) | none | up to 2000 characters |
| `scene` | text (optional) | none | up to 200 characters |
| `raster` | text (optional) | none | up to 2000 characters |

#### Find Sentinel-1 scenes

Search Planetary Computer (free) for Sentinel-1 over your area: analysis-ready RTC or GRD to process yourself, filtered to one polarisation, mode, orbit direction and track so a time series is consistent

*Tool id* `sarsearch`.

`POST /api/sar/search`: Sentinel-1 scenes on Planetary Computer over the area, filtered to a homogeneous series.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `aoi` | object | **required** |  |
| `start` | text | **required** | pattern `^\d{4}-\d{2}-\d{2}$` |
| `end` | text | **required** | pattern `^\d{4}-\d{2}-\d{2}$` |
| `collection` | text | `"rtc"` | one of `rtc`, `grd` |
| `pols` | text | `"VV+VH"` | one of `VV\+VH`, `HH\+HV`, `VV`, `HH`, `` |
| `mode` | text | `"IW"` | one of `IW`, `EW`, `SM` |
| `orbit` | text (optional) | none | one of `ascending`, `descending` |
| `rel_orbit` | integer (optional) | none | ≥ 1; ≤ 175 |

#### SAR product inspector

What a Sentinel-1 product or SAR raster is (mission, product type, mode, polarisation, orbit, processing) and which processing steps are done, needed or optional

*Tool id* `sarinspect`.

#### Speckle filter

Reduce the speckle of a SAR layer: Refined Lee, Lee, Lee Sigma, Gamma MAP, Frost, boxcar or median (on linear power, whatever the layer's units)

*Tool id* `sarspeckle`.

#### SAR features

From a dual-pol SAR layer: VV/VH ratio, RVI (radar vegetation index), polarisation indices, span and texture (for crop mapping and classification)

*Tool id* `sarfeatures`.

`POST /api/sar/features`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `raster` | text | **required** |  |
| `features` | list of text | none |  |
| `texture_band` | text | `"VV"` | one of `VV`, `VH`, `HH`, `HV` |
| `window` | integer | `7` | ≥ 3; ≤ 31 |
| `name` | text | `""` | up to 80 characters |

#### SAR time series & change

Several dates of SAR: per-pixel mean, median, min, max, std and trend; and the change from the first to the last date, with flooding (new open water)

*Tool id* `sarseries`.

#### InSAR & RTC on demand (ASF HyP3)

Interferograms (ground movement, coherence) from pairs of Sentinel-1 SLC scenes, or RTC from GRD, processed for you by ASF HyP3: free with a NASA Earthdata login (a monthly allowance)

*Tool id* `sarhyp3`.

`POST /api/sar/asf/search`: Sentinel-1 scenes in ASF's catalogue (no login), and for SLC the InSAR pairs they make.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `aoi` | object | **required** |  |
| `start` | text | **required** | pattern `^\d{4}-\d{2}-\d{2}$` |
| `end` | text | **required** | pattern `^\d{4}-\d{2}-\d{2}$` |
| `level` | text | `"SLC"` | one of `SLC`, `GRD_HD` |
| `orbit` | text (optional) | none | one of `ascending`, `descending` |
| `path` | integer (optional) | none | ≥ 1; ≤ 175 |
| `step` | integer | `1` | ≥ 1; ≤ 4 |
| `max_days` | integer | `48` | ≥ 6; ≤ 400 |

`POST /api/sar/hyp3/access`: Ask ASF for HyP3 access (once per Earthdata account; usually approved quickly).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `use_case` | text | **required** | up to 5000 characters |
| `access_code` | text (optional) | none | up to 100 characters |

`POST /api/sar/hyp3/download`: Download a finished job's product into the workspace (imports/hyp3/…): its GeoTIFFs go to Contents.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_id` | text | **required** | pattern `^[0-9a-f-]{36}$` |

`GET /api/sar/hyp3/jobs`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `name` (query) | text (optional) | none |  |
| `days` (query) | integer | `30` |  |

`POST /api/sar/hyp3/submit`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_type` | text | **required** | one of `INSAR_GAMMA`, `RTC_GAMMA` |
| `pairs` | list of list of text | none | 0–100 items |
| `granules` | list of text | none | 0–100 items |
| `name` | text | `"lulc-fetch"` | up to 90 characters |
| `looks` | text | `"20x4"` | one of `20x4`, `10x2` |
| `displacement` | yes / no | `true` |  |
| `water_mask` | yes / no | `false` |  |
| `resolution` | integer | `30` | ≥ 10; ≤ 30 |
| `radiometry` | text | `"gamma0"` | one of `gamma0`, `sigma0` |
| `speckle` | yes / no | `false` |  |
| `validate_only` | yes / no | `false` |  |

`GET /api/sar/hyp3/user`: Who is logged in, whether HyP3 access is approved, and the credits left this month.

#### SAR + optical fusion

Map crops or land cover from Sentinel-2 and Sentinel-1 together: compares optical only, SAR only, early fusion (stacked) and late fusion (combined, SAR alone under clouds) on spatial blocks, then maps with the best

*Tool id* `sarfusion`.

`POST /api/sar/fusion`: Stack optical + SAR on the optical grid, then compare optical only, SAR only, early and late fusion on spatial

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `optical` | text | **required** |  |
| `sars` | list of text | **required** | 1–60 items |
| `ground_truth` | object | **required** |  |
| `cloud` | text (optional) | none |  |
| `scl` | yes / no | `true` |  |
| `indices` | list of text | none | 0–20 items |
| `sar_dates` | yes / no | `true` |  |
| `texture` | yes / no | `false` |  |
| `embedding` | text (optional) | none |  |
| `model` | text | `"lgbm"` | one of `lgbm`, `rf`, `xgb` |
| `block_m` | number | `1000` | ≥ 20; ≤ 50000 |
| `folds` | integer | `5` | ≥ 2; ≤ 10 |
| `per_class` | integer | `3000` | ≥ 50; ≤ 50000 |
| `map_with` | text | `"best"` | one of `best`, `early`, `selected`, `late`, `optical`, `sar`, `embedding` |
| `select` | yes / no | `true` |  |
| `class_colors` | object (optional) | none |  |
| `name` | text | `"fusion"` | up to 80 characters |

#### Fill clouds from SAR

Fill the cloudy pixels of an optical image from Sentinel-1 (and a same-day coarse image such as MODIS, or a clear image of another date): learned on the image's own clear pixels, no training data needed

*Tool id* `sargapfill`.

`POST /api/sar/gapfill`: Fill the cloudy pixels of an optical image from SAR (and helper images), learned on its own clear pixels.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `optical` | text | **required** |  |
| `mask` | text (optional) | none |  |
| `sars` | list of text | none | 0–12 items |
| `helpers` | list of text | none | 0–6 items |
| `truth` | text (optional) | none |  |
| `model` | text | `"lgbm"` | one of `lgbm`, `rf`, `linear` |
| `residual` | yes / no | `true` |  |
| `samples` | integer | `60000` | ≥ 2000; ≤ 500000 |
| `name` | text | `"filled"` | up to 80 characters |

#### Flood & water map

Open water and flooding with a confidence for every pixel: radar (dark calm water), the drop since a pre-flood image, optical water index where clear, terrain (low and flat) and permanent water (JRC), combined; no training needed

*Tool id* `sarwater`.

`POST /api/sar/water`: Water and flood map with a 0–1 confidence, from radar, optical, the change since a pre-flood image, terrain and

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `post` | text | **required** |  |
| `pre` | text (optional) | none |  |
| `optical` | text (optional) | none |  |
| `water_mask` | text (optional) | none |  |
| `dem` | text (optional) | `"auto"` |  |
| `permanent` | text (optional) | `"auto"` |  |
| `aoi` | object (optional) | none |  |
| `vv_db` | number (optional) | none | ≥ -35; ≤ -5 |
| `vh_db` | number (optional) | none | ≥ -40; ≤ -10 |
| `radar_threshold` | text | `"checked"` | one of `checked`, `fixed`, `otsu`, `multi_otsu`, `li`, `yen`, `kapur`, `triangle`, `isodata`, `niblack`, `sauvola`, `wolf`, `phansalkar`, `fcm`, `fuzzy`, `membership` |
| `hand_m` | number | `10` | ≥ 1; ≤ 100 |
| `slope_max` | number | `8` | ≥ 1; ≤ 45 |
| `drop_db` | number | `3` | ≥ 0.5; ≤ 15 |
| `name` | text | `"water"` | up to 80 characters |

#### Soil moisture (change detection)

Relative surface soil moisture (0–100 %) for every date of a Sentinel-1 series: each pixel between the driest and wettest it was seen (TU Wien change detection), with water, towns and dense vegetation masked

*Tool id* `sarsoil`.

`POST /api/sar/soilmoisture`: Relative surface soil moisture (0–100 %) per date from a Sentinel-1 time series (change detection).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `rasters` | list of text | **required** | 4–400 items |
| `pol` | text | `"VV"` | one of `VV`, `HH` |
| `lo` | number | `5` | ≥ 0; ≤ 40 |
| `hi` | number | `95` | ≥ 60; ≤ 100 |
| `min_range_db` | number | `3.0` | ≥ 0.5; ≤ 15 |
| `water_db` | number | `-18.0` | ≥ -35; ≤ -8 |
| `veg_ratio_db` | number | `-5.0` | ≥ -15; ≤ 0 |
| `aoi` | object (optional) | none |  |
| `name` | text | `"soil_moisture"` | up to 80 characters |

## Analysis ▸ Hydrology

### Data & DEM

#### Rainfall data

Free rainfall: CHIRPS totals for your dates or the mean annual rainfall on a grid, and design storms (2- to 100-year rain) with a daily series at a place

*Tool id* `hydrain`.

`POST /api/hydro/rainfall`: CHIRPS rainfall total for dates or mean annual rainfall over years (a grid), or design storms at a point (ERA5).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `op` | text | **required** | one of `total`, `annual`, `storms` |
| `bbox` | list of number (optional) | none | 4–4 items |
| `point` | list of number (optional) | none |  |
| `start` | text (optional) | none | pattern `^\d{4}-\d{2}-\d{2}$` |
| `end` | text (optional) | none | pattern `^\d{4}-\d{2}-\d{2}$` |
| `first_year` | integer | `1991` | ≥ 1940; ≤ 2100 |
| `last_year` | integer | `2020` | ≥ 1940; ≤ 2100 |
| `name` | text | `""` | up to 80 characters |

#### DEM preparation

Make a DEM drain: fill no-data holes, align it to another raster, burn known rivers in, breach roads and embankments across valleys, fill the remaining pits

*Tool id* `hydprep`.

`POST /api/hydro/condition`: A DEM made to drain: fill no-data holes, align, burn streams in, breach and / or fill pits.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `steps` | list of text | none |  |
| `like` | text (optional) | none |  |
| `res` | number (optional) | none | > 0 |
| `streams` | object (optional) | none |  |
| `burn_m` | number | `5.0` | ≥ 0; ≤ 100 |
| `burn_buffer_m` | number | `0.0` | ≥ 0; ≤ 5000 |
| `max_breach_m` | number (optional) | none | > 0 |
| `max_hole_cells` | integer | `2000` | ≥ 1; ≤ 1e+07 |
| `name` | text | `""` | up to 80 characters |

#### Flow direction & accumulation

Where water flows from each cell (D8, D-infinity or multiple flow direction) and how much area drains through it: cells, km² and specific catchment area

*Tool id* `hydflow`.

`POST /api/hydro/flow`: Flow direction and accumulation by D8, D-infinity or MFD: cells, contributing area (km²), specific catchment area.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `method` | text | `"d8"` | one of `d8`, `dinf`, `mfd` |
| `outputs` | list of text | none |  |
| `mfd_p` | number | `1.1` | > 0; ≤ 20 |
| `conditioned` | yes / no (optional) | none |  |
| `name` | text | `""` | up to 80 characters |

#### Hydrology in one go

From a DEM: fill sinks, flow direction and accumulation, streams with their Strahler order, watersheds of points (or every basin) and the wetness index

*Tool id* `rhydro`.

`POST /api/raster/hydrology`: Fill sinks, D8 flow direction, flow accumulation (km²), streams with Strahler order (raster and lines), watersheds

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `products` | list of text | none |  |
| `stream_km2` | number | `1.0` | > 0; ≤ 1e+06 |
| `points` | list of list of number | none | 0–500 items |
| `snap_m` | number | `150` | ≥ 0; ≤ 10000 |
| `min_basin_km2` | number (optional) | none | > 0 |
| `name` | text | `""` | up to 80 characters |

### Watersheds

#### Watersheds

Catchments of outlet points (snapped to the river, nested with their parents), sub-watersheds of every stream link, or every basin: boundaries, statistics, longest flow paths

*Tool id* `hydwshed`.

`POST /api/hydro/watershed`: Watersheds of outlet points (snapped, nested with their parents), sub-watersheds of every stream link, or every basin;

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `mode` | text | `"points"` | one of `points`, `subbasins`, `all` |
| `points` | list of list of number | none | 0–1000 items |
| `snap_m` | number | `150` | ≥ 0; ≤ 20000 |
| `stream_km2` | number | `1.0` | > 0; ≤ 1e+06 |
| `min_basin_km2` | number (optional) | none | > 0 |
| `within_points` | yes / no | `false` |  |
| `conditioned` | yes / no (optional) | none |  |
| `name` | text | `""` | up to 80 characters |

#### Drainage network

Streams by a contributing-area threshold, as links between sources and junctions: Strahler and Shreve order, length, slope, topology, drainage density and Horton's ratios

*Tool id* `hydnet`.

`POST /api/hydro/network`: Streams by a contributing-area threshold: links with Strahler and Shreve order and topology, nodes, drainage density.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `stream_km2` | number | `1.0` | > 0; ≤ 1e+06 |
| `outputs` | list of text | none |  |
| `conditioned` | yes / no (optional) | none |  |
| `name` | text | `""` | up to 80 characters |

#### Morphometry & prioritisation

Linear, areal and relief parameters of every sub-watershed (Rb, Dd, Fs, Rc, Re, Ff, ruggedness, hypsometric integral…) and their priority for conservation

*Tool id* `hydmorph`.

`POST /api/hydro/morphometry`: Morphometric parameters of sub-watersheds, hypsometric curves and conservation priority (compound value).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `mode` | text | `"subbasins"` | one of `subbasins`, `points` |
| `points` | list of list of number | none | 0–500 items |
| `snap_m` | number | `150` | ≥ 0 |
| `stream_km2` | number | `0.5` | > 0 |
| `basin_km2` | number | `5.0` | > 0 |
| `name` | text | `"morphometry"` | up to 80 characters |

#### Terrain & runoff indicators

Slope, aspect, curvature, wetness index (TWI), stream power index (SPI), height above the nearest drainage (HAND), depressions, flow length and flow paths

*Tool id* `hydterrain`.

`POST /api/hydro/terrain`: Slope, aspect, curvature, TWI, SPI, HAND, depressions, flow length and flow paths.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `products` | list of text | none |  |
| `flow` | text | `"mfd"` | one of `mfd`, `dinf`, `d8` |
| `stream_km2` | number | `1.0` | > 0; ≤ 1e+06 |
| `points` | list of list of number | none | 0–1000 items |
| `min_depression_m` | number | `0.1` | ≥ 0 |
| `conditioned` | yes / no (optional) | none |  |
| `name` | text | `""` | up to 80 characters |

### Runoff & erosion

#### Rainfall–runoff (SCS-CN)

How much of a storm runs off: SCS curve numbers from land cover and soil, runoff depth, routed volumes, and per watershed the runoff, volume, time of concentration and peak flow

*Tool id* `hydrunoff`.

`POST /api/hydro/runoff`: SCS curve-number runoff of a storm; routed volumes; per watershed of the outlets: runoff, volume and peak flow.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `rain_mm` | number (optional) | none | > 0; ≤ 5000 |
| `rain_raster` | text (optional) | none |  |
| `landcover` | text (optional) | none |  |
| `cn_raster` | text (optional) | none |  |
| `soil` | text | `"B"` | pattern `^[ABCDabcd]$` |
| `soil_raster` | text (optional) | none |  |
| `scheme` | text | `"auto"` | one of `auto`, `worldcover`, `dynamicworld`, `esri` |
| `custom` | object (list of number values) (optional) | none |  |
| `condition` | text | `"II"` | one of `I`, `II`, `III` |
| `lam` | number | `0.2` | ≥ 0.01; ≤ 0.3 |
| `points` | list of list of number | none | 0–500 items |
| `snap_m` | number | `150` | ≥ 0; ≤ 20000 |
| `duration_h` | number | `24` | > 0; ≤ 240 |
| `name` | text | `"runoff"` | up to 80 characters |

#### Design flood hydrograph

Flow at a watershed's outlet hour by hour through design storms (e.g. the 10-, 50- and 100-year rain): SCS-CN excess rain and the SCS unit hydrograph, with peaks and volumes

*Tool id* `hydhydrograph`.

`POST /api/hydro/hydrograph`: Flow at a watershed outlet through design storms: SCS-CN excess rain and the SCS unit hydrograph.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `point` | list of number | **required** |  |
| `storms_mm` | list of number | **required** | 1–12 items |
| `labels` | list of text (optional) | none |  |
| `cn` | number (optional) | none | > 0; ≤ 100 |
| `landcover` | text (optional) | none |  |
| `soil` | text | `"B"` | pattern `^[ABCDabcd]$` |
| `condition` | text | `"II"` | one of `I`, `II`, `III` |
| `pattern` | text | `"scs2"` | one of `scs2`, `uniform` |
| `duration_h` | number | `24` | > 0; ≤ 240 |
| `dt_h` | number | `0.25` | > 0.01; ≤ 6 |
| `tc_h` | number (optional) | none | > 0 |
| `snap_m` | number | `150` | ≥ 0 |
| `name` | text | `"design_flood"` | up to 80 characters |

#### Streamflow modelling

Daily river flow from rain and evaporation: GR4J (conceptual), LightGBM / Random Forest (machine learning) and an LSTM (deep learning), calibrated on observed flow and scored on later years

*Tool id* `hydstream`.

`POST /api/hydro/streamflow`: Daily streamflow models (GR4J, LightGBM / Random Forest, LSTM) calibrated on observed flow and scored on later years.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `table` | text | **required** |  |
| `date_col` | text (optional) | none |  |
| `flow_col` | text (optional) | none |  |
| `units` | text | `"m3s"` | one of `m3s`, `mm` |
| `area_km2` | number (optional) | none | > 0 |
| `point` | list of number (optional) | none |  |
| `cal_frac` | number | `0.7` | ≥ 0.3; ≤ 0.9 |
| `models` | list of text | none |  |
| `past_flow` | yes / no | `false` |  |
| `name` | text | `"streamflow"` | up to 80 characters |

#### Soil erosion (RUSLE)

Average yearly soil loss in t/ha from rainfall, soil, slope, cover and practice (A = R·K·LS·C·P), FAO classes, and sediment yield per watershed

*Tool id* `hyderosion`.

`POST /api/hydro/erosion`: RUSLE soil loss (t/ha/yr) and FAO classes; per watershed of the points: soil loss and sediment yield.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `rain_mm` | number (optional) | none | > 0; ≤ 20000 |
| `rain_raster` | text (optional) | none |  |
| `r_raster` | text (optional) | none |  |
| `k` | number (optional) | none | > 0; ≤ 1 |
| `k_raster` | text (optional) | none |  |
| `texture` | text (optional) | none |  |
| `landcover` | text (optional) | none |  |
| `ndvi` | text (optional) | none |  |
| `c_raster` | text (optional) | none |  |
| `p` | number | `1.0` | > 0; ≤ 1 |
| `p_raster` | text (optional) | none |  |
| `points` | list of list of number | none | 0–200 items |
| `snap_m` | number | `150` | ≥ 0 |
| `name` | text | `"rusle"` | up to 80 characters |

### Floods

#### Flood from HAND

What floods when rivers rise 1, 2, 5 … m: depth and extent at each water level from the height above the nearest drainage, with flooded areas and volumes

*Tool id* `hydflood`.

`POST /api/hydro/hand-flood`: Flooded extent and depth at water levels above the streams (HAND).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `levels_m` | list of number | none | 1–20 items |
| `stream_km2` | number | `1.0` | > 0; ≤ 1e+06 |
| `max_dist_m` | number (optional) | none | > 0 |
| `conditioned` | yes / no (optional) | none |  |
| `name` | text | `""` | up to 80 characters |

#### Flood simulation (2D)

Water spreading over the DEM through time from river inflows and / or rain (local inertial shallow-water model, as LISFLOOD-FP): maximum depth and speed, arrival time, depth over time

*Tool id* `hydsim`.

`POST /api/hydro/flood-sim`: 2D flood simulation (local inertial, as LISFLOOD-FP) from inflows and / or rain.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `inflows` | list of Inflow | none | 0–50 items |
| `rain_mm_h` | number or list of list of number (optional) | none |  |
| `hours` | number | `6` | > 0; ≤ 240 |
| `manning` | number | `0.035` | > 0; ≤ 1 |
| `landcover` | text (optional) | none |  |
| `start_depth` | text (optional) | none |  |
| `snapshots` | integer | `6` | ≥ 0; ≤ 48 |
| `name` | text | `"flood_sim"` | up to 80 characters |

*Inflow*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `lon` | number | **required** |  |
| `lat` | number | **required** |  |
| `q` | number or list of list of number | **required** |  |

#### Flood depth (FwDET)

Water depth inside a flood extent (the SAR flood map, a water mask or polygons) from a DEM: the water surface at the flood's edge carried inwards (FwDET 2.0)

*Tool id* `hyddepth`.

`POST /api/hydro/flood-depth`: Water depth of a flood extent on a DEM (FwDET 2.0).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `flood` | text (optional) | none |  |
| `polygons` | object (optional) | none |  |
| `smooth_px` | integer | `3` | ≥ 1; ≤ 31 |
| `include_permanent` | yes / no | `false` |  |
| `name` | text | `"flood"` | up to 80 characters |

#### Flood impact

What a flood covers: hectares of cropland, built-up and other land cover, people (a population raster), buildings and km of roads, by depth when known

*Tool id* `hydimpact`.

`POST /api/hydro/flood-impact`: Land cover, people, buildings and roads under a flood (split by depth when a depth raster is given).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `flood` | text (optional) | none |  |
| `polygons` | object (optional) | none |  |
| `depth` | text (optional) | none |  |
| `landcover` | text (optional) | none |  |
| `population` | text (optional) | none |  |
| `buildings` | object (optional) | none |  |
| `roads` | object (optional) | none |  |
| `name` | text | `"flood_impact"` | up to 80 characters |

#### Flood susceptibility

Where floods are likely, learned from where they happened and predictors (HAND, TWI, slope, curvature, distance to streams, land cover…), checked with spatial cross-validation

*Tool id* `hydsusc`.

`POST /api/hydro/susceptibility`: Flood susceptibility from a flood inventory and predictor layers, scored with spatial-block cross-validation.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `predictors` | list of text | **required** | 1–40 items |
| `floods` | object | **required** |  |
| `non_floods` | object (optional) | none |  |
| `ratio` | number | `1.0` | > 0; ≤ 10 |
| `buffer_m` | number | `200` | ≥ 0; ≤ 100000 |
| `model` | text | `"rf"` | one of `rf`, `lgbm` |
| `block_m` | number | `2000` | > 0; ≤ 200000 |
| `folds` | integer | `5` | ≥ 2; ≤ 10 |
| `name` | text | `"flood_susceptibility"` | up to 80 characters |

### Water planning

#### Groundwater potential

Groundwater potential zones from slope, drainage density, wetness, rainfall, land cover, lineaments, geology and soil, weighted by AHP, checked against wells

*Tool id* `hydgw`.

`POST /api/hydro/groundwater`: Groundwater potential zones from thematic layers weighted by AHP; checked against wells when given.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `rain_raster` | text (optional) | none |  |
| `landcover` | text (optional) | none |  |
| `lineaments` | object (optional) | none |  |
| `geology` | text (optional) | none |  |
| `geology_scores` | object (number values) (optional) | none |  |
| `soil` | text (optional) | none |  |
| `soil_scores` | object (number values) (optional) | none |  |
| `stream_km2` | number | `0.5` | > 0 |
| `radius_m` | number | `1000` | > 0; ≤ 50000 |
| `weights` | object (number values) (optional) | none |  |
| `wells` | object (optional) | none |  |
| `wells_field` | text (optional) | none |  |
| `name` | text | `"groundwater"` | up to 80 characters |

#### Check dams & ponds

Sites along the streams where a small dam holds the most water for its length, with the pond each makes; or the area–capacity curve of a site

*Tool id* `hydstorage`.

`POST /api/hydro/storage`: Check dam / pond sites ranked by water held per metre of dam, or the area–capacity curve of a site.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `dem` | text | **required** |  |
| `op` | text | `"sites"` | one of `sites`, `curve` |
| `height_m` | number | `3.0` | > 0; ≤ 200 |
| `stream_km2` | number | `0.5` | > 0 |
| `min_order` | integer | `1` | ≥ 1; ≤ 12 |
| `max_order` | integer | `3` | ≥ 1; ≤ 12 |
| `max_area_km2` | number (optional) | none | > 0 |
| `max_slope_pct` | number | `5.0` | > 0; ≤ 100 |
| `spacing_m` | number | `300` | ≥ 0 |
| `top` | integer | `50` | ≥ 1; ≤ 500 |
| `point` | list of number (optional) | none |  |
| `step_m` | number | `0.5` | > 0; ≤ 50 |
| `snap_m` | number | `100` | ≥ 0 |
| `name` | text | `""` | up to 80 characters |

## Insert ▸ Library

### Library

#### Data library

Ready-made GIS data from Hugging Face: boundaries of India's states, districts, sub-districts, villages, constituencies and city wards, roads, railways… Search, then add any file to the map (downloaded once). Every dataset uploaded to the library account appears here

*Tool id* `library`.

`POST /api/library/accounts`: More Hugging Face accounts (or organisations) whose public datasets the Library lists.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `accounts` | list of text | none | 0–20 items |

`GET /api/library/datasets`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `refresh` (query) | yes / no | `false` |  |

`POST /api/library/fetch`: Download one file (once) and get it ready for the map: rasters as they are, vectors as GeoJSON, tables into tables/.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `repo` | text | **required** | up to 200 characters |
| `path` | text | **required** | up to 1000 characters |
| `sha256` | text (optional) | none | up to 64 characters |
| `size` | integer (optional) | none |  |

`GET /api/library/files`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `repo` (query) | text | **required** |  |
| `refresh` (query) | yes / no | `false` |  |

`GET /api/library/geojson`: A downloaded vector as GeoJSON (only files in the library's download folder).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

## Insert

### Online map layer & field collection

#### Field collection

Take geotagged points and photos with your phone in the field, even offline, then bring them in here as a points layer with photos: send leaf photos straight to Diagnose crop disease

*Tool id* `field`.

`GET /api/field/info`

#### Online map layer

Add map layers from web map services: WMS, WMTS or XYZ tiles, such as Bhuvan's maps of India, NASA GIBS daily satellite imagery, or any address. They are drawn from the service, nothing is downloaded

*Tool id* `online`.

`GET /api/online/capabilities`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `url` (query) | text | **required** |  |

## Models and their parameters

### Classical ML (tables and rasters) — `lulc_fetch/ml.py`

Used by Classical ML (tabular data) and Classical ML for raster. Settings shared by every model:

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `split` (Validation split) | select | `"auto"` | `auto`, `group`, `blocks`, `random` | How test pixels are kept apart from training pixels. Neighbouring pixels look almost identical, so a random pixel split tests the model on near-copies of its training data and overstates accuracy. 'By polygon' keeps every training polygon entirely in train or in test. 'Spatial blocks' splits the map into squares. Auto picks the most honest option the table allows. |
| `test_size` (Test split) | float | `0.25` | 0.05 – 0.5 | Share of rows kept aside to measure accuracy honestly (never used for training). 0.2–0.3 is standard. |
| `class_weight` (Class balancing) | select | `"none"` | `none`, `balanced` | 'Balanced' gives rare classes more weight, which improves their accuracy (and macro F1) when classes have very different sizes. Not needed if you used stratified sampling. |
| `cv_folds` (Cross-validation folds) | int | `0` | 0 – 10 | Extra check: train k times on different parts of the training data and report the spread. 0 = off (faster). 5 is common. |
| `max_train_rows` (Max training rows) | int | `null` | 100 – 10000000 | Large tables are randomly sampled down (keeping class proportions) to this many training rows. Empty = the model's sensible default. |
| `block_size` (Block size (map units)) | float | `null` | 1e-06 – 10000000.0 | Side of the squares used by 'Spatial blocks', e.g. metres for UTM. Empty = automatic (about 64 blocks over the area). Use blocks larger than your typical field. |
| `random_state` (Random seed) | int | `0` | 0 – 2147483647 | Makes the split and training reproducible. |
| `missing` (Missing values) | select | `"drop"` | `drop`, `impute` | Rows where a feature has no value: drop them (safest), or fill them in (median for numbers, a separate '(missing)' category for categorical columns) so no rows are lost. With 'Fill in', Classify an image also fills pixels where a band is missing. |
| `drop_constant` (Remove constant columns) | bool | `true` |  | Drop feature columns that have the same value in every training row: they carry no information. |
| `drop_correlated` (Remove near-duplicate columns) | select | `"off"` | `off`, `0.99`, `0.95`, `0.9` | If two numeric features are almost perfectly correlated (e.g. B8 and B8A, or an index and its source bands), keep only one. Makes linear models, k-NN and Maximum Likelihood more stable. Tree models rarely need it. |
| `outliers` (Outliers) | select | `"none"` | `none`, `clip` | Clip extreme values (saturated pixels, cloud or shadow remnants, sensor spikes) to percentiles of the training data, so a few odd pixels can't distort scaling or linear / distance-based models. |
| `outlier_pct` (Clip percentile) | float | `1.0` | 0.1 – 10 | Values below this percentile and above (100 − this) are clipped. 1 = clip to the 1st–99th percentile; 0.5 is gentler. |
| `skew` (Skewed features) | select | `"none"` | `none`, `auto`, `all` | Make strongly skewed columns (e.g. radar backscatter in linear units, texture, distances) more symmetric with a Yeo-Johnson power transform. Helps linear models, SVM, k-NN, MLP, Naive Bayes and Maximum Likelihood; trees don't care. |
| `scaling` (Feature scaling) | select | `"auto"` | `auto`, `standard`, `minmax`, `robust`, `none` | Put features on a common scale. Auto = standard scaling for the models that need it (SVM, SGD, logistic, k-NN, MLP) and none for trees. Standard: mean 0, std 1. Min–max: 0 to 1. Robust: median / interquartile range, least affected by outliers. |
| `reduce` (Reduce bands (PCA)) | select | `"none"` | `none`, `auto`, `10`, `20`, `30`, `50` | Combine many correlated bands into fewer principal components before the model. Needed for Maximum Likelihood / Linear Discriminant on hyperspectral data (more bands than training pixels per class makes their statistics unstable), and speeds up SVM and k-NN. Auto: only when there are more than 30 bands, keeping 99 % of the variance (max 30 components). |
| `target_transform` (Target transform) | select | `"none"` | `none`, `log`, `yeo-johnson` | For a skewed target (biomass, yield, counts): train on log(1 + y) or a Yeo-Johnson transform, then convert predictions back. Often lowers the error for skewed values. |

#### Random Forest (`rf`)

Trees · classification, regression. Many decision trees voting together. Accurate, robust to noise, needs no feature scaling. The standard for land-cover mapping.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_estimators` (Number of trees) | int | `300` | 10 – 3000 | More trees = more stable results, slower training. 200–500 is plenty for most tables. |
| `max_depth` (Max tree depth) | int | `null` | 1 – 200 | Limit how deep each tree grows. Empty = unlimited (best for large tables). Set 10–20 if it overfits small tables. |
| `min_samples_leaf` (Min samples per leaf) | int | `1` | 1 – 1000 | Smallest group of pixels a leaf may hold. Raise to 3–10 for smoother, less noisy maps. |
| `max_features` (Bands tried per split) | select | `"sqrt"` | `sqrt`, `log2`, `0.5`, `1.0` | How many bands each split considers. √(bands) is the classic choice; more = stronger trees but more alike. |

#### Extra Trees (`et`)

Trees · classification, regression. Like Random Forest but with random split points: faster and often smoother maps.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_estimators` (Number of trees) | int | `300` | 10 – 3000 | More trees = more stable, slower. |
| `max_depth` (Max tree depth) | int | `null` | 1 – 200 | Empty = unlimited. |
| `min_samples_leaf` (Min samples per leaf) | int | `1` | 1 – 1000 | Raise for smoother maps. |

#### XGBoost (`xgb`)

Boosting · classification, regression. Gradient-boosted trees, often the most accurate on tabular data. Trees are built one after another, each fixing earlier mistakes.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_estimators` (Boosting rounds) | int | `400` | 10 – 5000 | Number of trees added one after another. |
| `learning_rate` (Learning rate) | float | `0.1` | 0.001 – 1 | How much each tree corrects the previous ones. Smaller = more careful, needs more rounds. |
| `max_depth` (Max tree depth) | int | `6` | 1 – 20 | Depth of each tree. 4–8 works well. |
| `subsample` (Row subsample) | float | `0.8` | 0.1 – 1 | Share of rows used per tree. Below 1 reduces overfitting. |
| `colsample_bytree` (Band subsample) | float | `0.8` | 0.1 – 1 | Share of bands used per tree. |
| `reg_lambda` (L2 regularisation) | float | `1.0` | 0 – 100 | Penalty on large leaf values. Raise to reduce overfitting. |

#### LightGBM (`lgbm`)

Boosting · classification, regression. Very fast gradient boosting with leaf-wise trees. Great for large tables (hundreds of thousands of pixels).

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_estimators` (Boosting rounds) | int | `400` | 10 – 5000 | Number of trees. |
| `learning_rate` (Learning rate) | float | `0.05` | 0.001 – 1 | Smaller = more careful, needs more rounds. |
| `num_leaves` (Leaves per tree) | int | `31` | 2 – 1024 | Main complexity control. 15–63 is typical. |
| `subsample` (Row subsample) | float | `0.8` | 0.1 – 1 | Share of rows used per tree. |
| `colsample_bytree` (Band subsample) | float | `0.8` | 0.1 – 1 | Share of bands used per tree. |

#### Hist. Gradient Boosting (`hgb`)

Boosting · classification, regression. scikit-learn's fast gradient boosting (LightGBM-style). Stops early by itself when it stops improving.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `max_iter` (Max boosting rounds) | int | `300` | 10 – 5000 | Upper limit. Early stopping usually stops sooner. |
| `learning_rate` (Learning rate) | float | `0.1` | 0.001 – 1 | Step size of each round. |
| `max_leaf_nodes` (Leaves per tree) | int | `31` | 2 – 1024 | Complexity of each tree. |
| `l2_regularization` (L2 regularisation) | float | `0.0` | 0 – 100 | Raise to reduce overfitting. |

#### Decision Tree (`dt`)

Trees · classification, regression. A single tree of yes/no rules. Easy to understand, but usually less accurate than forests.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `max_depth` (Max tree depth) | int | `15` | 1 – 100 | Deeper = more detailed rules but more overfitting. |
| `min_samples_leaf` (Min samples per leaf) | int | `5` | 1 – 1000 | Larger = simpler, smoother tree. |
| `criterion` (Split criterion) | select | `"gini"` | `gini`, `entropy`, `log_loss` | How split quality is measured. Results are usually similar. |

#### SVM (`svm`)

Kernel · classification, regression. Support Vector Machine. Very accurate on small, clean training sets; slow on large ones. RBF kernel by default, linear for many bands / embeddings.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `C` (C (regularisation)) | float | `10.0` | 0.001 – 10000 | Higher = fits training data more tightly (risk of overfitting). 1–100 is typical. |
| `kernel` (Kernel) | select | `"rbf"` | `rbf`, `linear`, `poly` | RBF (recommended) draws curved class boundaries. Linear is faster and works as well or better with many bands (hyperspectral) or embeddings (64–128 dimensions). Polynomial is in between. |
| `gamma` (Gamma) | select | `"scale"` | `scale`, `auto` | Kernel width (RBF / polynomial). 'scale' adapts to the data and is a good default. |
| `probability` (Estimate probabilities) | bool | `false` |  | Needed for a confidence map when classifying an image, but makes training ~5× slower. |

#### SGD (linear) (`sgd`)

Linear · classification, regression. Linear model trained with stochastic gradient descent. Extremely fast on huge tables; best when classes are fairly separable.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `loss` (Loss) | select | `"log_loss"` | `log_loss`, `hinge`, `modified_huber` | log loss gives class probabilities (confidence map); hinge is a linear SVM. |
| `alpha` (Regularisation (alpha)) | float | `0.0001` | 1e-08 – 1 | Higher = simpler model. |
| `max_iter` (Max epochs) | int | `1000` | 5 – 100000 | Passes over the data. |

#### Logistic Regression (`lr`)

Linear · classification. Linear classifier with calibrated probabilities. A solid, interpretable baseline.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `C` (C (inverse regularisation)) | float | `1.0` | 0.0001 – 10000 | Higher = less regularisation. |
| `max_iter` (Max iterations) | int | `1000` | 50 – 100000 | Raise if it doesn't converge. |

#### Naive Bayes (`nb`)

Probabilistic · classification. Gaussian Naive Bayes: assumes bands are independent within each class. Instant to train; a quick baseline.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `var_smoothing` (Variance smoothing) | float | `1e-09` | 0 – 1 | Added to variances for numerical stability. |

#### Maximum Likelihood (`mlc`)

Probabilistic · classification. The classic remote-sensing classifier: each class is a multivariate Gaussian with its own covariance (Gaussian MLC / QDA).

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `priors` (Class priors) | select | `"equal"` | `equal`, `data` | Equal priors is the traditional MLC choice. 'From data' favours classes with more training pixels. |
| `reg_param` (Covariance regularisation) | float | `0.001` | 0 – 1 | Small value that stabilises covariance matrices of correlated bands. |

#### Minimum Distance (`mindist`)

Distance · classification. Assigns each pixel to the class whose average spectrum is closest. The simplest classic remote-sensing classifier.

*(no settings)*

#### Spectral Angle Mapper (`sam`)

Distance · classification. Compares the shape (angle) of each pixel's spectrum with each class's mean spectrum, ignoring brightness.

*(no settings)*

#### Linear Discriminant (`lda`)

Probabilistic · classification. Gaussian classes with one shared covariance. Simpler than Maximum Likelihood and good with few training pixels.

*(no settings)*

#### k-Nearest Neighbours (`knn`)

Neighbours · classification, regression. Labels a pixel like its most similar training pixels. Simple and non-linear; slow to apply to big images.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_neighbors` (Neighbours (k)) | int | `7` | 1 – 200 | How many similar pixels vote. 5–15 is typical. |
| `weights` (Vote weights) | select | `"distance"` | `distance`, `uniform` | Closer neighbours count more with 'distance'. |
| `metric` (Distance) | select | `"euclidean"` | `euclidean`, `cosine`, `manhattan` | How similar two pixels are. Cosine (angle) is best for embeddings (AlphaEarth, TESSERA) and spectral shapes; Manhattan is robust with many bands. |

#### Neural network (MLP) (`mlp`)

Neural · classification, regression. A small fully connected neural network. Captures complex patterns; benefits from more training data.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `hidden_layer_sizes` (Hidden layers) | text | `"128,64"` |  | Neurons per layer, comma-separated. '128,64' = two layers. |
| `alpha` (L2 regularisation) | float | `0.0001` | 0 – 10 | Higher = simpler model. |
| `max_iter` (Max epochs) | int | `300` | 10 – 5000 | Training stops earlier if it stops improving. |

### Classical ML for raster: which models for which data — `lulc_fetch/rasterml.py`

| Data | Models offered first | Note |
|---|---|---|
| RGB image | rf, svm, knn, mlc | 3 colour bands (e.g. a drone or aerial photo). Few bands, so texture-free pixel classification is limited; Random Forest or SVM usually work best. |
| Multispectral image | rf, svm, mlc, lgbm | 4–30 bands (e.g. Sentinel-2, Landsat). Random Forest and SVM are strong; Maximum Likelihood is the classic choice. |
| Hyperspectral image | svm, rf, sam, lda | Many narrow bands. Bands are highly correlated: SVM and Spectral Angle Mapper handle that well. For Maximum Likelihood / Linear Discriminant, reduce the bands with PCA first (set automatically). |
| Pixel embedding | knn, svm, lr, sam | Learned feature vectors (e.g. AlphaEarth 64-D, TESSERA 128-D) where similar places have similar vectors. Compare them by angle: k-NN with cosine distance, linear SVM, logistic regression or SAM. Keep scaling off. |
| SAR (radar) | rf, svm, lgbm, mlc | Radar backscatter (VV / VH …). Use dB values; Random Forest and SVM work well. Add optical bands with Stack layers for better maps. |
| Raster | rf, svm, mlc |  |

All raster models: `rf`, `svm`, `mlc`, `sam`, `mindist`, `knn`, `lgbm`, `xgb`, `et`, `lr`, `lda`, `nb`, `mlp`, `hgb` (settings as in the table models above; `mlc` maximum likelihood, `sam` spectral angle mapper, `mindist` minimum distance).

### Unsupervised (clustering) — `lulc_fetch/unsupervised.py`

#### K-means (`kmeans`)

Splits rows into k round groups around centre points. Fast on any size; the classic unsupervised classifier for images.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_clusters` (Number of clusters) | int | `5` | 2 – 100 | How many groups to split the rows into. Not sure? Tick 'Find the best number of clusters' below and the app tries 2 … k and picks the best silhouette score. |
| `n_init` (Restarts) | int | `10` | 1 – 50 | Runs K-means from different random starts and keeps the best. More = more stable result. |
| `max_iter` (Max iterations) | int | `300` | 10 – 5000 | Upper limit of refinement steps per run. |

#### Hierarchical (`hierarchical`)

Merges the most similar rows step by step into a tree (dendrogram), then cuts it into k groups.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_clusters` (Number of clusters) | int | `5` | 2 – 100 | How many groups to split the rows into. Not sure? Tick 'Find the best number of clusters' below and the app tries 2 … k and picks the best silhouette score. |
| `linkage` (Linkage) | select | `"ward"` | `ward`, `average`, `complete`, `single` | How the distance between two groups is measured. Ward (recommended) makes compact, similar-sized groups. Average / complete are less sensitive to group size; single follows chains. |
| `distance_threshold` (Cut height) | float | `null` | 1e-06 – 1000000000.0 | Instead of a number of clusters: cut the tree at this distance (see the dendrogram). Empty = use the number of clusters. |

#### DBSCAN (`dbscan`)

Finds dense regions of any shape and marks isolated rows as noise. You don't choose the number of clusters.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `eps` (Neighbourhood size (eps)) | float | `null` | 1e-06 – 1000000.0 | Max distance between neighbours, in scaled units. Empty = automatic from the knee of the k-distance curve (shown in the results). |
| `min_samples` (Min samples) | int | `10` | 2 – 1000 | Rows needed within eps to form a dense core. Larger = fewer, denser clusters and more noise. |

#### HDBSCAN (`hdbscan`)

DBSCAN that adapts to clusters of different densities. Usually the easiest density method: no eps to tune.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `min_cluster_size` (Min cluster size) | int | `null` | 2 – 1000000 | Smallest group worth calling a cluster. Empty = automatic (0.5 % of rows, at least 10). |
| `min_samples` (Min samples) | int | `null` | 1 – 10000 | How conservative the clustering is: larger = more rows called noise. Empty = automatic (10, or the min cluster size if smaller). |
| `cluster_selection_method` (Cluster selection) | select | `"eom"` | `eom`, `leaf` | EOM (recommended) prefers a few large stable clusters; leaf gives many small, fine clusters. |

#### Spectral clustering (`spectral`)

Builds a similarity graph between rows and splits it. Finds non-round groups (rings, bands) that K-means can't.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_clusters` (Number of clusters) | int | `5` | 2 – 100 | How many groups to split the rows into. Not sure? Tick 'Find the best number of clusters' below and the app tries 2 … k and picks the best silhouette score. |
| `affinity` (Similarity graph) | select | `"nearest_neighbors"` | `nearest_neighbors`, `rbf` | Nearest neighbours (recommended) connects each row to its closest rows; RBF connects all rows weighted by distance. |
| `n_neighbors` (Neighbours) | int | `15` | 2 – 200 | Rows each row is connected to (nearest-neighbour graph). |

#### Gaussian mixture (`gmm`)

Soft clustering: each group is a Gaussian (ellipse) and every row gets a probability of belonging to each group.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_clusters` (Number of clusters) | int | `5` | 2 – 100 | How many groups to split the rows into. Not sure? Tick 'Find the best number of clusters' below and the app tries 2 … k and picks the best silhouette score. |
| `covariance_type` (Group shape) | select | `"full"` | `full`, `diag`, `tied`, `spherical` | Full (recommended): any ellipse. Diagonal: axis-aligned ellipses. Tied: same shape for all. Spherical: round, like K-means. |
| `n_init` (Restarts) | int | `3` | 1 – 20 | Fits from several starts and keeps the best. |

Options for every method:

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `find_k` (Find the best number of clusters) | bool | `false` |  | Tries every k from 2 to the maximum on a sample and uses the one with the best silhouette score (shown as a chart). For K-means, hierarchical, spectral and Gaussian mixture. |
| `k_max` (Try up to k =) | int | `10` | 3 – 30 | Largest number of clusters to try. |
| `max_fit_rows` (Rows used to fit) | int | `null` | 100 – 10000000 | Rows used to build the clusters. Empty = the method's sensible default (K-means 1,000,000 · Gaussian mixture 300,000 · DBSCAN / HDBSCAN 30,000 · hierarchical 5,000 · spectral 4,000). All other rows are then assigned to the nearest cluster. |
| `save_model` (Save as a model (to cluster an image)) | bool | `false` |  | Saves the clustering like a trained model, so Classify an image can apply it to a raster: unsupervised land-cover classification. |
| `random_state` (Random seed) | int | `0` | 0 – 2147483647 | Makes sampling and clustering reproducible. |

### PCA and dimensionality reduction — `lulc_fetch/pca.py`

Shared:

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_components` (Number of components) | int | `3` | 1 – 30 | How many new bands to create. 3 is a good start: they are shown as a colour image (component 1 = red, 2 = green, 3 = blue) and usually hold almost all the information of a multispectral image. |
| `standardize` (Standardize bands) | bool | `true` |  | Rescale every band to mean 0 and standard deviation 1 before the analysis, so bands with larger numbers (e.g. NIR) don't dominate. Recommended when bands have different ranges. |
| `resolution` (Processing resolution) | select | `"auto"` | `auto`, `1`, `2`, `4`, `8` | Pixel size of the output. Auto keeps full detail for small areas and coarsens very large images (e.g. a whole Sentinel-2 tile) so it finishes quickly. Pick an area to analyse for full detail. |
| `sample_size` (Pixels used to fit the model) | int | `100000` | 1000 – 2000000 | The model learns from this many randomly chosen pixels, then is applied to the whole image. 100,000 gives the same result as using every pixel for practical purposes, in a fraction of the time. |
| `random_state` (Random seed) | int | `0` | 0 – 2147483647 | Fixes the random pixel sample (and random initialisation of some methods) so running again gives exactly the same result. |

#### Principal Component Analysis (`pca`)

The standard choice. Finds uncorrelated directions of maximum variance. Removes band redundancy and highlights the main patterns (brightness, vegetation, moisture).

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `whiten` (Whiten) | bool | `false` |  | Scale each component to unit variance. Useful as input for some classifiers, but it hides how important each component is. Usually leave off for visual analysis. |
| `svd_solver` (Solver) | select | `"auto"` | `auto`, `full`, `randomized`, `covariance_eigh` | Numerical method. Auto picks the best one for the data size. All give the same answer for imagery with a handful of bands. |

#### Incremental PCA (`incremental`)

Same result as PCA, but learns from every pixel in small batches instead of a sample. For very large images when you want all pixels to count.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `batch_size` (Batch size (pixels)) | int | `50000` | 1000 – 1000000 | Pixels processed per step. Larger is faster but uses more memory. |
| `whiten` (Whiten) | bool | `false` |  | Scale each component to unit variance. Usually leave off. |

#### Kernel PCA (`kernel`)

Non-linear PCA. Can separate classes that linear PCA mixes up. Slow: fitted on a small sample, and best used on a selected area.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `kernel` (Kernel) | select | `"rbf"` | `rbf`, `poly`, `sigmoid`, `cosine`, `linear` | The similarity function. RBF works well for most data. Polynomial and sigmoid need tuning. Linear gives ordinary PCA. |
| `gamma` (Gamma) | float | `null` | 0 | How local the RBF / polynomial / sigmoid kernel is. Larger = more detail, but noisier. Leave empty for the default 1 / number of bands (with standardized bands). |
| `degree` (Degree (polynomial)) | int | `3` | 2 – 6 | Only used with the polynomial kernel. |
| `fit_sample` (Pixels used to fit) | int | `1500` | 200 – 5000 | Kernel PCA compares every pixel with every sample pixel, so this is kept small. 1,000–2,000 is a good balance between quality and speed. |

#### Non-negative Matrix Factorization (`nmf`)

Splits pixels into additive, non-negative parts, similar to spectral unmixing (e.g. vegetation / soil / water). Components are easy to interpret.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `init` (Initialisation) | select | `"nndsvda"` | `nndsvda`, `nndsvd`, `random` | Starting point of the optimisation. NNDSVDa is deterministic and converges well. |
| `beta_loss` (Loss) | select | `"frobenius"` | `frobenius`, `kullback-leibler` | How the reconstruction error is measured. Frobenius is standard. |
| `max_iter` (Max iterations) | int | `400` | 50 – 5000 | Raise if you get a convergence warning. |

#### Independent Component Analysis (`ica`)

Finds statistically independent signals rather than uncorrelated ones. Can isolate specific features such as haze, shadows or a single land-cover type.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `fun` (Contrast function) | select | `"logcosh"` | `logcosh`, `exp`, `cube` | Measures non-Gaussianity. logcosh is a good general-purpose choice. |
| `algorithm` (Algorithm) | select | `"parallel"` | `parallel`, `deflation` | Parallel estimates all components at once (usually faster). Deflation estimates them one by one. |
| `max_iter` (Max iterations) | int | `400` | 50 – 5000 | Raise if you get a convergence warning. |

#### Truncated Singular Value Decomposition (`svd`)

Like PCA but without removing the mean, so component 1 keeps overall brightness. Fast.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `algorithm` (Algorithm) | select | `"randomized"` | `randomized`, `arpack` | Randomized is fast and accurate for imagery. |
| `n_iter` (Iterations) | int | `5` | 1 – 50 | Only for the randomized algorithm. More iterations = more accurate. |

#### Factor Analysis (`fa`)

Models bands as a few hidden factors plus band-specific noise. The varimax option gives factors that are easier to interpret.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `rotation` (Rotation) | select | `"varimax"` | `varimax`, `quartimax`, `none` | Rotates the factors to make them easier to interpret. Varimax is the usual choice. |
| `fit_sample` (Pixels used to fit) | int | `20000` | 1000 – 200000 | Factor Analysis is iterative and slower than PCA. 20,000 random pixels converge in a few seconds and give stable factors. |
| `max_iter` (Max iterations) | int | `3000` | 100 – 10000 | Raise if you get a convergence warning. |

### Forecasting models — `lulc_fetch/forecast.py`

#### Compare all and keep the best (`auto`)

Backtests every model below and keeps the one with the lowest error (the baselines are always compared).

*(no settings)*

#### LightGBM (`lightgbm`)

Gradient-boosted trees, fast and usually the most accurate on tables: the common choice for station AQI, humidity or rainfall forecasting. Handles missing values.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_estimators` (Trees (boosting rounds)) | int | `500` | 10 – 5000 | How many trees are added one after another (boosting rounds, like epochs). More = can learn more detail, slower; with early stopping this is the most it may use. |
| `learning_rate` (Learning rate) | float | `0.03` | 0.001 – 1.0 | How much each new tree corrects the previous ones. Smaller = more careful, needs more trees. |
| `num_leaves` (Leaves per tree) | int | `31` | 2 – 1024 | How complex each tree can be. More = more detail, more risk of learning noise. |
| `max_depth` (Max depth (-1 = no limit)) | int | `-1` | -1 – 64 | Limits how deep each tree grows. |
| `min_child_samples` (Min rows per leaf) | int | `20` | 1 – 5000 | A leaf needs at least this many rows: larger = smoother, less over-fitting. |
| `subsample` (Row sample per tree) | float | `0.8` | 0.1 – 1.0 | Share of the rows each tree sees (random): below 1 adds variety and reduces over-fitting. |
| `colsample_bytree` (Input sample per tree) | float | `0.8` | 0.1 – 1.0 | Share of the inputs each tree may use: below 1 adds variety. |
| `reg_lambda` (L2 regularisation) | float | `0.0` | 0.0 – 100.0 | Penalty on large leaf values: higher = more cautious. |

#### XGBoost (`xgboost`)

Gradient-boosted trees like LightGBM, a little slower; often as accurate.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_estimators` (Trees (boosting rounds)) | int | `500` | 10 – 5000 | How many trees are added one after another (boosting rounds, like epochs). More = can learn more detail, slower; with early stopping this is the most it may use. |
| `learning_rate` (Learning rate) | float | `0.03` | 0.001 – 1.0 | How much each new tree corrects the previous ones. Smaller = more careful, needs more trees. |
| `max_depth` (Max depth) | int | `6` | 1 – 20 | How deep each tree grows: deeper = more detail, more risk of learning noise. |
| `min_child_weight` (Min weight per leaf) | float | `1.0` | 0.0 – 100.0 | Larger = smoother trees. |
| `subsample` (Row sample per tree) | float | `0.8` | 0.1 – 1.0 | Share of the rows each tree sees (random): below 1 adds variety and reduces over-fitting. |
| `colsample_bytree` (Input sample per tree) | float | `0.8` | 0.1 – 1.0 | Share of the inputs each tree may use: below 1 adds variety. |
| `reg_lambda` (L2 regularisation) | float | `1.0` | 0.0 – 100.0 | Penalty on large leaf values. |

#### Gradient boosting (scikit-learn) (`hgb`)

scikit-learn's histogram gradient boosting: like LightGBM, always available.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `max_iter` (Trees (boosting rounds)) | int | `400` | 10 – 5000 | How many trees are added one after another (boosting rounds, like epochs). More = can learn more detail, slower; with early stopping this is the most it may use. |
| `learning_rate` (Learning rate) | float | `0.05` | 0.001 – 1.0 | How much each new tree corrects the previous ones. Smaller = more careful, needs more trees. |
| `max_leaf_nodes` (Leaves per tree) | int | `31` | 2 – 1024 | How complex each tree can be. |
| `max_depth` (Max depth (0 = no limit)) | int | `0` | 0 – 64 | Limits how deep each tree grows. |
| `min_samples_leaf` (Min rows per leaf) | int | `20` | 1 – 5000 | Larger = smoother, less over-fitting. |
| `l2_regularization` (L2 regularisation) | float | `0.0` | 0.0 – 100.0 | Penalty on large leaf values. |

#### Random Forest (`rf`)

Many decision trees averaged: robust, little tuning, smooth forecasts; can't go beyond the values it has seen.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `n_estimators` (Trees) | int | `200` | 10 – 2000 | How many trees are averaged: more = steadier, slower (no over-fitting from more trees). |
| `max_depth` (Max depth (0 = no limit)) | int | `0` | 0 – 100 | Limits how deep each tree grows. |
| `min_samples_leaf` (Min rows per leaf) | int | `3` | 1 – 1000 | Larger = smoother forecasts. |
| `max_features` (Inputs per split (share)) | float | `0.5` | 0.05 – 1.0 | Share of the inputs each split may choose from. |

#### Linear (ridge) regression (`linear`)

A weighted sum of the features: simple, fast, explainable; misses non-linear effects.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `alpha` (Regularisation (alpha)) | float | `1.0` | 0.0 – 10000.0 | Shrinks the weights: higher = simpler, steadier model. |

#### Same time last cycle (baseline) (`seasonal`)

The value one cycle earlier (24 hours, 7 days, 12 months…): a baseline every model should beat.

*(no settings)*

#### Last value (baseline) (`naive`)

The last known value, held: the simplest baseline.

*(no settings)*

Training options for every model:

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `fit_rows` (Rows used to fit each model) | int | `150000` | 1000 – 2000000 | Training rows used by each fit (the most recent features of a random sample of series and times). More = slower, sometimes better. |
| `early_stop` (Early stopping (boosted trees)) | bool | `false` |  | LightGBM, XGBoost and gradient boosting stop adding trees when the error on the latest 15 % of the training period stops improving; the forecast then uses the best number of trees. |
| `patience` (Stop after no gain for (rounds)) | int | `50` | 5 – 1000 | Early stopping waits this many trees without improvement. |
| `band` (Uncertainty band (%)) | int | `80` | 50 – 99 | Share of the backtest errors the band around the forecast should contain. |
| `seed` (Random seed) | int | `0` | 0 – 999999 | Same seed + same data = the same model. |
| `tune` (Tune the best model (trials)) | int | `0` | 0 – 60 | Tries this many random settings for the best model, each checked with the same backtests, and keeps the best (0 = off). Each trial costs one training per backtest. |

### Interpolation methods — `lulc_fetch/interpolation.py`

#### IDW (inverse distance weighting) (`idw`)

Each point's influence falls with distance: nearby points count more. Simple and robust; the surface passes through the points and makes 'bull's-eyes' around them. Good for: rainfall, temperature, soil properties.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `power` (Power) | float | `2.0` | 0.5 – 6 | How fast influence falls with distance: 1 smooth, 2 usual, 3+ very local. |
| `neighbours` (Points used per cell) | int | `0` | 0 – 500 | 0 = all points; e.g. 12 = only the 12 nearest (faster, more local). |

#### Kriging (ordinary) (`kriging`)

Uses the spatial autocorrelation of the values: a semivariogram fitted to how differences grow with distance sets the weights. The best linear unbiased estimate, with its standard error as band 2. Needs enough points (10+) to fit the semivariogram well. With more than 500 points each cell uses its 48 nearest (a search neighbourhood); a Gaussian model always gets a small nugget (1 % of the sill) to stay stable. Good for: groundwater, soil properties, pollution.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `model` (Semivariogram model) | select | `"spherical"` | `spherical`, `exponential`, `gaussian`, `linear` | The shape of the fitted semivariogram. |
| `nugget` (Nugget) | select | `"auto"` | `auto`, `zero` | Variation at very short distances (measurement error): fitted, or none. |

#### Spline (thin-plate) (`spline`)

A smooth, minimum-curvature surface through (or near) the points. Good for gently varying fields; can overshoot beyond the highest / lowest value between distant points. Good for: elevation, terrain, smooth fields.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `smoothing` (Smoothing) | float | `0.0` | 0 – 1000 | 0 = through every point; larger = smoother, near the points (e.g. 0.1–10). |

#### Natural neighbour (`natural`)

Sibson's method: each cell takes the values of its Voronoi neighbours, weighted by how much of their area a new point there would take. Local, smooth and never beyond the data range; defined inside the points' convex hull. Computed on the output grid (discrete Sibson): very close to exact inside, a little less so within about a kilometre of the outermost points. Good for: elevation, rainfall.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `outside` (Outside the points' hull) | select | `"empty"` | `empty`, `nearest` | Natural neighbour is defined inside the hull of the points. |

#### Nearest neighbour (`nearest`)

Each cell takes the value of the closest point (Thiessen / Voronoi polygons). No averaging, so it also works for classes (soil type, land use codes). Good for: categorical data, quick looks.

*(no settings)*

#### Trend surface (`trend`)

A polynomial surface (plane, quadratic or cubic) fitted to all points by least squares: shows the large-scale trend, not local detail. Doesn't pass through the points. Good for: large-scale spatial trends, removing a trend first.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `order` (Order) | select | `"1"` | `1`, `2`, `3` | 1 = a tilted plane; 2 = one bend; 3 = two bends (needs 10+ points). |

#### TIN (triangulated irregular network) (`tin`)

Delaunay triangles between the points, with values interpolated linearly inside each triangle. Exact at the points; defined inside their convex hull; shows the triangles' edges. Good for: DEM, terrain modelling.

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `outside` (Outside the points' hull) | select | `"empty"` | `empty`, `nearest` | TIN is defined inside the hull of the points. |

### Deep learning: segmentation (Train classify model) — `lulc_fetch/dl.py`

| Architecture | Key | Accuracy (1–5) | Speed (1–5) | What it is |
|---|---|---|---|---|
| U-Net | `unet` | 4 | 4 | Encoder–decoder with skip connections: sharp boundaries, works well with little data. A good default. |
| U-Net++ | `unetpp` | 5 | 2 | U-Net with nested skip connections: often slightly more accurate, slower and heavier. |
| DeepLabV3+ | `deeplabv3plus` | 5 | 3 | Atrous spatial pyramid pooling + decoder: handles objects of many sizes (fields, urban blocks). |
| DeepLabV3 | `deeplabv3` | 4 | 3 | Multi-scale context with atrous convolutions; smoother boundaries than V3+. |
| PSPNet | `pspnet` | 3 | 5 | Pyramid pooling of global context: fast, good for large uniform regions, coarser edges. |
| FPN | `fpn` | 4 | 4 | Feature pyramid network: fast multi-scale model, a good alternative to U-Net. |
| LinkNet | `linknet` | 3 | 5 | Very light encoder–decoder: fastest training, fine for simple maps. |
| SegFormer | `segformer` | 4 | 3 | Transformer-style all-MLP decoder: strong global context. |
| FCN | `fcn` | 3 | 3 | Fully convolutional network (torchvision): the classic segmentation baseline. |
| LR-ASPP | `lraspp` | 3 | 5 | Lite reduced ASPP on MobileNetV3 (torchvision): tiny and fast, made for mobile devices. |
| YOLO26 semantic | `yolo_sem` | 4 | 5 | YOLO26 semantic segmentation (ultralytics): a fast real-time network, pretrained on Cityscapes street scenes. Needs the YOLO & SAM add-on. Any number of bands. |
| ENet | `light_enet` | 3 | 5 | Encoder–decoder with max-unpooling; the classic lightweight baseline. A light model (0.36 M parameters, Paszke et al., 2016): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| CGNet | `light_cgnet` | 4 | 4 | Context Guided blocks: local + surrounding + global context. A light model (0.5 M parameters, Wu et al., 2018): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| DABNet | `light_dabnet` | 4 | 4 | Depth-wise asymmetric bottlenecks; strong accuracy for its size. A light model (0.76 M parameters, Li et al., 2019): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| LEDNet | `light_lednet` | 4 | 5 | Split-shuffle factorized units + attention pyramid decoder. A light model (0.94 M parameters, Wang et al., 2019): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| FDDWNet | `light_fddwnet` | 4 | 3 | Factorized dilated depthwise residual modules. A light model (0.77 M parameters, Liu et al., 2019): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| LEANet | `light_leanet` | 4 | 5 | Split-channel units with channel + spatial attention. A light model (0.74 M parameters, Zhang et al., 2021): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| LSNet | `light_lsnet` | 3 | 5 | Every 3×3 convolution replaced by a 3×1 / 1×3 pair. A light model (0.62 M parameters, 1D-convolution design): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| EFSNet | `light_efsnet` | 3 | 5 | Continuous shuffle dilated convolutions; the smallest here. A light model (0.17 M parameters, Hu et al., 2020): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| FPENet | `light_fpenet` | 3 | 4 | Feature pyramid encoding blocks + mutual embedding upsampling. A light model (0.4 M parameters, Liu & Yin, 2019): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| ADSCNet | `light_adscnet` | 3 | 4 | Asymmetric depthwise separable units + dense dilated connections. A light model (0.51 M parameters, Wang et al., 2019): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |
| TinyUNet | `light_tinyunet` | 4 | 4 | A compact U-Net: encoder–decoder with skip connections at every scale, depthwise-separable convolutions. A light model (0.24 M parameters, U-Net (Ronneberger et al., 2015), compact): trained from scratch, every band used directly, so it suits embeddings (64 / 128 bands) and small datasets. |

Encoders (backbones): `builtin`, `mobilenet_v2`, `tu-mobilenetv3_large_100`, `tu-mobilenetv3_small_100`, `resnet18`, `resnet34`, `resnet50`, `efficientnet-b0`, `efficientnet-b2`, `efficientnet-b4`, `resnet101`, `mobilenetv3_large`, `yolo-n`, `yolo-s`, `yolo-m`, `yolo-l`, `yolo-x`

Training settings:

| Setting | Type | Default | Range / options | What it does |
|---|---|---|---|---|
| `epochs` (Epochs) | int | `50` | 1 – 1000 | How many times the model sees every training patch. Early stopping usually ends sooner. |
| `batch_size` (Batch size) | int | `8` | 1 – 256 | Patches per training step. Bigger is faster but needs more memory; it is halved automatically if memory runs out. |
| `lr` (Learning rate) | float | `0.001` | 1e-06 – 1 | Step size of the optimiser. 1e-3 suits AdamW; use ~1e-2 for SGD. Lower it if the loss jumps around. |
| `val_share` (Validation (%)) | int | `20` | 5 – 50 | Patches held back to check the model after every epoch (early stopping and the best model use this). |
| `test_share` (Test (%)) | int | `0` | 0 – 40 | Optional patches never used during training: scored once at the end with the best model, for an independent result. |
| `split` (Split) | choice | `"blocks"` |  | Spatial blocks keep neighbouring (overlapping) patches on the same side, so validation pixels are never seen in training. Random is optimistic when patches overlap. |
| `early_stop` (Early stopping) | bool | `true` |  | Stop when the validation score hasn't improved for a while, and keep the best epoch. |
| `patience` (Patience (epochs)) | int | `10` | 1 – 200 | Epochs without improvement before stopping. |
| `monitor` (Watch) | choice | `"val_miou"` |  | The score used for early stopping, the learning-rate plateau schedule and picking the best model. |
| `min_delta` (Minimum improvement) | float | `0.001` | 0 – 1 | A change smaller than this doesn't count as an improvement. |
| `loss` (Loss) | choice | `"ce_dice"` |  | Dice, focal and class weights help rare classes (e.g. water in a mostly urban scene). |
| `class_weights` (Class weights) | choice | `"auto"` |  | Weights from the training pixel counts (median-frequency balancing, capped at 10×). Used by cross-entropy and focal loss. |
| `optimizer` (Optimiser) | choice | `"adamw"` |  | AdamW is a robust default. |
| `weight_decay` (Weight decay) | float | `0.0001` | 0 – 1 | Regularisation that keeps weights small (less overfitting). |
| `scheduler` (Learning-rate schedule) | choice | `"cosine"` |  | How the learning rate changes during training. |
| `augment` (Augmentation) | multi | `["flip", "rot90"]` |  | Random changes to training patches so the model generalises better. Flips and rotations are safe for satellite images. |
| `freeze_epochs` (Freeze backbone for (epochs)) | int | `0` | 0 – 100 | Train only the decoder at first (with pretrained weights); helps with very small datasets. |
| `amp` (Mixed precision) | choice | `"auto"` |  | Faster and lighter on NVIDIA GPUs. |
| `device` (Device) | choice | `"auto"` |  | Auto uses an NVIDIA GPU, else the Apple GPU, else the CPU. |
| `seed` (Random seed) | int | `42` | 0 – 2147483647 | Same seed + same settings = same split and (nearly) the same model. |

### Light segmentation networks — `lulc_fetch/lightseg/`

| Network | Key | Paper | Parameters (M, Cityscapes) | What it is |
|---|---|---|---|---|
| ENet | `enet` | Paszke et al., 2016 | 0.36 | Encoder–decoder with max-unpooling; the classic lightweight baseline |
| CGNet | `cgnet` | Wu et al., 2018 | 0.5 | Context Guided blocks: local + surrounding + global context |
| DABNet | `dabnet` | Li et al., 2019 | 0.76 | Depth-wise asymmetric bottlenecks; strong accuracy for its size |
| LEDNet | `lednet` | Wang et al., 2019 | 0.94 | Split-shuffle factorized units + attention pyramid decoder |
| FDDWNet | `fddwnet` | Liu et al., 2019 | 0.77 | Factorized dilated depthwise residual modules |
| LEANet | `leanet` | Zhang et al., 2021 | 0.74 | Split-channel units with channel + spatial attention |
| LSNet | `lsnet` | 1D-convolution design | 0.62 | Every 3×3 convolution replaced by a 3×1 / 1×3 pair |
| EFSNet | `efsnet` | Hu et al., 2020 | 0.17 | Continuous shuffle dilated convolutions; the smallest here |
| FPENet | `fpenet` | Liu & Yin, 2019 | 0.4 | Feature pyramid encoding blocks + mutual embedding upsampling |
| ADSCNet | `adscnet` | Wang et al., 2019 | 0.51 | Asymmetric depthwise separable units + dense dilated connections |
| TinyUNet | `tinyunet` | U-Net (Ronneberger et al., 2015), compact | 0.24 | A compact U-Net: encoder–decoder with skip connections at every scale, depthwise-separable convolutions |

### Object detection models — `lulc_fetch/detect.py`

| Model | Key | Backbone | Input size | Accuracy (1–5) | Speed (1–5) | Note |
|---|---|---|---|---|---|---|
| Faster R-CNN v2 | `fasterrcnn_v2` | ResNet-50 FPN | 800 | 5 | 2 | The most accurate torchvision box detector (COCO box mAP 46.7). |
| Faster R-CNN | `fasterrcnn` | ResNet-50 FPN | 800 | 4 | 3 | The classic two-stage detector (mAP 37.0). |
| Faster R-CNN Mobile | `fasterrcnn_mobile` | MobileNetV3 FPN | 800 | 3 | 4 | Light Faster R-CNN (mAP 32.8): several times faster on a CPU. |
| RetinaNet v2 | `retinanet_v2` | ResNet-50 FPN | 800 | 4 | 3 | One-stage detector with focal loss (mAP 41.5): good with many small objects. |
| FCOS | `fcos` | ResNet-50 FPN | 800 | 4 | 3 | Anchor-free one-stage detector (mAP 39.2). |
| SSD300 | `ssd` | VGG16 | 300 | 2 | 4 | Single-shot detector on 300 px tiles (mAP 25.1): fast, misses small objects. |
| SSDlite | `ssdlite` | MobileNetV3 | 320 | 1 | 5 | Tiny mobile detector (mAP 21.3, 13 MB): fastest, for a quick look. |
| Mask R-CNN v2 | `maskrcnn_v2` | ResNet-50 FPN | 800 | 5 | 2 | Finds objects and draws their outline (instance segmentation, mask mAP 41.8) instead of a box. |
| YOLO26 | `yolo_detect` |  | 640 | 5 | 5 | The newest YOLO (ultralytics): fast and accurate boxes. Pretrained on COCO (80 everyday classes): cars, trucks, buses, boats, planes, people, animals… |
| YOLO26 outlines | `yolo_segment` |  | 640 | 5 | 4 | YOLO26 instance segmentation: an outline for every object. Pretrained on COCO (80 everyday classes): cars, trucks, buses, boats, planes, people, animals… |
| YOLO26 aerial (DOTA) | `yolo_obb` |  | 1024 | 5 | 4 | Oriented (rotated) boxes, pretrained on DOTA aerial images: planes, ships, small and large vehicles, storage tanks, harbours, bridges, roundabouts, sports fields, swimming pools, helicopters. The best start for satellite and aerial images. |
| SAM 2.1: segment everything | `sam_all` |  | 1024 | 4 | 1 | Segment Anything (Meta): outlines every distinct object or region (buildings, fields, trees, ponds…) without naming it. Slow: several seconds per tile. |

### Hydrology and SAR models

#### GR4J daily rainfall–runoff model — `lulc_fetch/hydro/streamflow.py`

Four parameters, calibrated on the KGE of the calibration years (differential evolution, population 6 × 4, up to 25
generations, Sobol start; then a Nelder–Mead polish), after a warm-up of up to 365 days:

| Parameter | Meaning | Range searched |
|---|---|---|
| X1 | production store capacity (mm) | 1 – 2500 |
| X2 | groundwater exchange (mm/day) | -10 – 5 |
| X3 | routing store capacity (mm) | 1 – 600 |
| X4 | unit hydrograph time base (days) | 0.5 – 6 |

#### Streamflow machine learning
LightGBM: 600 trees, learning rate 0.03, 31 leaves, min 20 rows per leaf, row and column subsampling 0.8; Random
Forest: 300 trees, min 3 rows per leaf. Target: log(1 + flow). Inputs: rain on the last 0–7 days, rain sums over 3, 7,
14, 30, 60, 90 and 180 days, evaporation sums over 7 and 30 days, the antecedent precipitation index (k = 0.9), mean
temperature, day of year (sine, cosine); optionally yesterday's observed flow.

#### Streamflow LSTM (deep learning add-on)
One LSTM layer of 48 units over the last 120 days of rain, evaporation, temperature and season, dropout 0.2, a linear
head; Adam (learning rate 0.002), batches of 256, up to 40 epochs with early stopping (8 epochs of patience) on 10 %
of the calibration days; inputs standardised on the calibration period, target log(1 + flow).

#### Scores used
NSE (Nash–Sutcliffe), KGE (Kling–Gupta), percent bias, RMSE; baseline: the day-of-year mean of the calibration years.

#### SAR flood map ML refinement — `lulc_fetch/sar/floodml.py`
Labels: flood-map confidence ≥ 0.75 water, ≤ 0.25 dry (the rest is decided by the model). Features: VV, VH (dB),
VV − VH, mean and standard deviation over 5 × 5 and 11 × 11 of VV and VH, VV change since a pre-flood image, HAND and
slope from a DEM. LightGBM: 300 trees, learning rate 0.05, 63 leaves, subsampling 0.8, balanced water / dry samples
(up to 200,000). TinyUNet: 128 × 128 crops, batches of 8, AdamW (0.002, weight decay 1e-4), class-weighted cross
entropy with the uncertain pixels ignored, flips; scored on held-out 64 × 64-pixel blocks.

#### Flood susceptibility — `lulc_fetch/hydro/susceptibility.py`
Random Forest (300 trees, min 2 per leaf) or LightGBM (300 trees, learning rate 0.05, 31 leaves); non-flood samples
at least the buffer away from floods (1 per flood sample by default); ROC AUC by spatial-block (GroupKFold) and random
(stratified) 5-fold cross-validation; classes by probability: < 0.2 very low … ≥ 0.8 very high.

#### 2D flood simulation — `lulc_fetch/hydro/flood2d.py`
Local inertial shallow-water equations (Bates et al. 2010) with semi-implicit Manning friction, Courant number
α = 0.7 (with the flow speed), time steps up to 30 s, flows capped at Froude 1, outflows limited to the water a cell
holds (exact mass balance), normal-depth outflow at the DEM's edges, up to 1,500,000 cells. Manning's n by
land cover:

| Cover | Manning's n |
|---|---|
| tree | 0.1 |
| forest | 0.1 |
| mangrove | 0.12 |
| shrub | 0.07 |
| grass | 0.035 |
| rangeland | 0.04 |
| crop | 0.04 |
| built | 0.08 |
| urban | 0.08 |
| bare | 0.025 |
| snow | 0.02 |
| water | 0.03 |
| wetland | 0.06 |
| flooded | 0.06 |
| moss | 0.03 |

#### SCS curve numbers (Rainfall–runoff, Design flood hydrograph) — `lulc_fetch/hydro/runoff.py`

| Cover | A | B | C | D |
|---|---|---|---|---|
| tree | 30 | 55 | 70 | 77 |
| forest | 30 | 55 | 70 | 77 |
| shrub | 35 | 56 | 70 | 77 |
| grass | 39 | 61 | 74 | 80 |
| rangeland | 49 | 69 | 79 | 84 |
| crop | 67 | 78 | 85 | 89 |
| built | 89 | 92 | 94 | 95 |
| urban | 89 | 92 | 94 | 95 |
| bare | 77 | 86 | 91 | 94 |
| snow | 98 | 98 | 98 | 98 |
| water | 100 | 100 | 100 | 100 |
| wetland | 85 | 85 | 85 | 85 |
| flooded | 85 | 85 | 85 | 85 |
| mangrove | 80 | 85 | 88 | 90 |
| moss | 49 | 69 | 79 | 84 |
| cloud | – | – | – | – |

Antecedent moisture: CN I = 4.2 CN / (10 − 0.058 CN), CN III = 23 CN / (10 + 0.13 CN); initial abstraction λ = 0.2
(0.05 optional). SCS dimensionless unit hydrograph with lag 0.6 tc and peak 0.208 A / Tp; storm pattern SCS Type II.

#### RUSLE factors — `lulc_fetch/hydro/erosion.py`

| Soil texture | K (t·h/MJ/mm) |
|---|---|
| sand | 0.008 |
| loamy sand | 0.012 |
| sandy loam | 0.017 |
| loam | 0.039 |
| silt loam | 0.05 |
| silt | 0.055 |
| sandy clay loam | 0.026 |
| clay loam | 0.039 |
| silty clay loam | 0.042 |
| sandy clay | 0.018 |
| silty clay | 0.033 |
| clay | 0.029 |

| Cover | C |
|---|---|
| tree | 0.003 |
| forest | 0.003 |
| mangrove | 0.003 |
| shrub | 0.04 |
| grass | 0.05 |
| rangeland | 0.05 |
| crop | 0.28 |
| built | 0.0 |
| urban | 0.0 |
| bare | 0.45 |
| snow | 0.0 |
| water | 0.0 |
| wetland | 0.0 |
| flooded | 0.0 |
| moss | 0.1 |

#### Groundwater potential — `lulc_fetch/hydro/groundwater.py`
Factor order for the AHP weights (most important first): Rainfall, Geology, Lineament density, Drainage density, Slope, Land cover, Soil, Wetness (TWI).
Land-cover scores (1 poor … 5 good):

| Cover | Score |
|---|---|
| water | 5 |
| wetland | 5 |
| flooded | 5 |
| mangrove | 4 |
| tree | 4 |
| forest | 4 |
| crop | 4 |
| grass | 3 |
| rangeland | 3 |
| shrub | 3 |
| moss | 2 |
| bare | 2 |
| snow | 1 |
| built | 1 |
| urban | 1 |

#### AHP — `lulc_fetch/ahp.py`
Saaty's random index RI by the number of factors: 1: 0.0, 2: 0.0, 3: 0.58, 4: 0.9, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49, 11: 1.51, 12: 1.48, 13: 1.56, 14: 1.57, 15: 1.59. Consistent when
CR = CI / RI < 0.10.

#### Design storms — `lulc_fetch/hydro/rainfall.py`
Gumbel (EV1) by the method of moments on the annual maxima; return periods 2, 5, 10, 25, 50, 100 years;
1-, 2-, 3- and 5-day totals.

## Other endpoints

Used by the app itself (Contents, History, projects, files…), Workflows and the Assistant.

#### `GET /api/about`: The About box (click the logo): version, how the app runs, the computer, library versions, add-ons and folders.

#### `GET /api/agri/schema`

#### `POST /api/analyze/pixel`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band_map` | object (integer values) | **required** |  |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |
| `index` | text (optional) | none |  |
| `formula` | text (optional) | none | up to 500 characters |
| `lat` | number | **required** |  |
| `lon` | number | **required** |  |

#### `POST /api/analyze/render`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `band_map` | object (integer values) | **required** |  |
| `scale` | number | `1.0` |  |
| `offset` | number | `0.0` |  |
| `index` | text (optional) | none |  |
| `formula` | text (optional) | none | up to 500 characters |
| `composite` | text (optional) | none |  |
| `band` | integer (optional) | none |  |
| `rgb` | list of integer (optional) | none |  |
| `pca` | yes / no | `false` |  |
| `clip` | object (optional) | none |  |
| `stretch` | text | `"fixed"` |  |
| `vmin` | number (optional) | none |  |
| `vmax` | number (optional) | none |  |
| `cmap` | text (optional) | none |  |

#### `GET /api/assistant/catalog`: The tools the Assistant may plan with, as it is told about them.

#### `GET /api/assistant/conversations/{conv_id}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `conv_id` (path) | text | **required** |  |

#### `DELETE /api/assistant/conversations/{conv_id}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `conv_id` (path) | text | **required** |  |

#### `PUT /api/assistant/memory/notes`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `notes` | text | `""` | up to 20000 characters |

#### `DELETE /api/assistant/memory/pitfalls`

#### `POST /api/assistant/observe`: What a step made, looked at (bands and value ranges, rows and columns), with warnings when it looks wrong.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `paths` | list of text | **required** | 0–50 items |

#### `PUT /api/assistant/settings`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `provider` | text | **required** | one of `ollama`, `api`, `claude` |
| `model` | text | `""` | up to 100 characters |
| `ollama_url` | text | `""` | up to 200 characters |
| `api_preset` | text | `""` | up to 40 characters |
| `api_model` | text | `""` | up to 200 characters |
| `api_base` | text | `""` | up to 300 characters |

#### `GET /api/cache`: Sizes of the workspace's working folders (the cache of the temporary workspace, or the project's files).

#### `POST /api/cache/clean`: Delete working files older than N days (whole job / upload folders, so layers never half-break).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folders` | list of text | **required** |  |
| `older_than_days` | number | `7` | ≥ 0; ≤ 3650 |

#### `GET /api/config`

#### `GET /api/credentials`

#### `PUT /api/credentials/{provider}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `provider` (path) | text | **required** |  |

#### `DELETE /api/credentials/{provider}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `provider` (path) | text | **required** |  |

#### `POST /api/credentials/{provider}/test`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `provider` (path) | text | **required** |  |

#### `POST /api/crs/assign-raster`: A copy of the raster that says it is in `crs` (its pixels stay as they are), next to the original.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `crs` | text | **required** | up to 20000 characters |

#### `GET /api/crs/describe`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `text` (query) | text | **required** |  |

#### `GET /api/crs/raster`: A raster's coordinate system, or (when it has none) whether it has a pixel grid and the likely systems.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `lon` (query) | number (optional) | none |  |
| `lat` (query) | number (optional) | none |  |

#### `GET /api/crs/search`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `q` (query) | text | `""` |  |
| `lon` (query) | number (optional) | none |  |
| `lat` (query) | number (optional) | none |  |
| `limit` (query) | integer | `40` |  |

#### `POST /api/crs/suggest`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `bounds` | list of number (optional) | none | 4–4 items |
| `lon` | number (optional) | none |  |
| `lat` | number (optional) | none |  |

#### `POST /api/crs/vector`: A layer's coordinates read in `actual` (raw numbers; or, when they were read in `current`, those numbers again),

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `geojson` | object | **required** |  |
| `actual` | text | **required** | up to 20000 characters |
| `current` | text (optional) | none | up to 20000 characters |

#### `DELETE /api/det/models`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` (query) | text | **required** |  |

#### `DELETE /api/dl/models`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` (query) | text | **required** |  |

#### `GET /api/emb/sources`

#### `GET /api/errors`

#### `GET /api/errors/file`: The error log as plain text (opens in a browser tab).

#### `POST /api/errors/report`: A failure seen only in the browser (a request that failed outside a background job): recorded like a job's.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `title` | text | `""` | up to 300 characters |
| `error` | text | `""` | up to 4000 characters |
| `tool` | text | `""` | up to 60 characters |

#### `POST /api/errors/reveal`

#### `GET /api/exports/{export_id}/{name}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `export_id` (path) | text | **required** |  |
| `name` (path) | text | **required** |  |

#### `POST /api/field/import`

#### `POST /api/fields/calc`: Field calculator for vector layers (attributes live in the browser): returns the new values.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `expression` | text | **required** | up to 2000 characters |
| `columns` | object (list of any values) | none |  |
| `geometries` | list of object (optional) (optional) | none |  |
| `n` | integer | **required** | ≥ 0; ≤ 2e+06 |

#### `GET /api/fields/functions`

#### `POST /api/files/save`: Save copies of workspace files (tool outputs, layers, tables, models) into a folder of the user's choice.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `paths` | list of text | **required** | 0–200 items |
| `folder` | text | **required** | up to 1000 characters |

#### `GET /api/forecast/schema`

#### `GET /api/fs/list`: Folders inside a folder, for the folder picker (file contents are never read).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | `""` |  |
| `products` (query) | yes / no | `false` |  |

#### `POST /api/fs/mkdir`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `parent` | text | **required** | up to 1000 characters |
| `name` | text | **required** | up to 120 characters |

#### `GET /api/fuzzy/preview`: The membership curve over [lo, hi] (for the panel's chart).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `fn` (query) | text | **required** |  |
| `lo` (query) | number | **required** |  |
| `hi` (query) | number | **required** |  |
| `a` (query) | number (optional) | none |  |
| `b` (query) | number (optional) | none |  |
| `c` (query) | number (optional) | none |  |
| `d` (query) | number (optional) | none |  |
| `mid` (query) | number (optional) | none |  |
| `spread` (query) | number (optional) | none |  |
| `sigma` (query) | number (optional) | none |  |
| `slope` (query) | number (optional) | none |  |

#### `POST /api/fuzzy/suggest`: Starting parameters for a function from a layer's value statistics (min, max, p2, p98).

#### `DELETE /api/history`

#### `GET /api/history/{job_id}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_id` (path) | text | **required** |  |

#### `POST /api/history/{job_id}/copy`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` | text | **required** | up to 2000 characters |
| `files` | list of text | none | 0–500 items |

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_id` (path) | text | **required** |  |

#### `GET /api/history/{job_id}/request`: The exact settings a run was started with, to run it again (as they were, or changed).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_id` (path) | text | **required** |  |

#### `GET /api/jobs/{job_id}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_id` (path) | text | **required** |  |

#### `DELETE /api/jobs/{job_id}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_id` (path) | text | **required** |  |

#### `POST /api/jobs/{job_id}/cancel`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_id` (path) | text | **required** |  |

#### `GET /api/jobs/{job_id}/files/{name}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `job_id` (path) | text | **required** |  |
| `name` (path) | text | **required** |  |

#### `DELETE /api/models`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `GET /api/pictures/file`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `POST /api/pictures/georef`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `bounds` | list of number | **required** | 4–4 items |

#### `POST /api/pictures/upload`: A picture plus optional world file (.jgw / .pgw / .wld) and .prj. Georeferenced → GeoTIFF layer.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `ask_crs` (query) | yes / no | `false` |  |

#### `POST /api/products/link`: Open a product where it is on this computer (a .SAFE folder or .SAFE.zip, or a folder holding one).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** | up to 2000 characters |

#### `DELETE /api/products/link`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `POST /api/products/open`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `res` | number | `40` | ≥ 10; ≤ 500 |
| `aoi` | object (optional) | none |  |

#### `PUT /api/products/upload/file`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` (query) | text | **required** |  |
| `rel` (query) | text | **required** |  |

#### `POST /api/products/upload/finish`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` | text | **required** | up to 300 characters |

#### `POST /api/products/upload/start`: Which of the dropped product's files still have to be copied (files already there with the same size are kept).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` | text | **required** | up to 300 characters |
| `files` | list of object | none | 0–20000 items |

#### `POST /api/products/upload/zip`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `filename` (query) | text | **required** |  |

#### `GET /api/project`

#### `POST /api/project/close`

#### `POST /api/project/new`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `name` | text | **required** | up to 80 characters |
| `folder` | text | **required** | up to 1000 characters |

#### `POST /api/project/open`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` | text | **required** | up to 1000 characters |

#### `DELETE /api/project/recent`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `folder` (query) | text | **required** |  |

#### `POST /api/project/save-as`: File ▸ Save as: a new project folder with the maps and layers open now. The files they use (downloads, results,

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `name` | text | **required** | up to 200 characters |
| `folder` | text | **required** | up to 1000 characters |
| `state` | object | **required** |  |

#### `PUT /api/project/state`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `state` | object | **required** |  |

#### `POST /api/python/run`: Run the user's Python on a table (edit session) or on vector attributes, in a separate process (a job).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text (optional) | none |  |
| `code` | text | **required** | up to 100000 characters |
| `apply` | yes / no | `false` |  |
| `columns` | object (list of any values) (optional) | none |  |
| `geometries` | list of object (optional) (optional) | none |  |
| `n` | integer | `0` | ≥ 0; ≤ 2e+06 |

#### `GET /api/raster/resampling-methods`: The resampling methods tools accept, with what each is for.

#### `GET /api/rasters`

#### `DELETE /api/rasters`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `GET /api/rasters/file`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `GET /api/rasters/grid`: A band's values on a Web Mercator grid (3D maps: a DEM's heights).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `band` (query) | integer | `1` |  |
| `scale` (query) | number | `1.0` |  |
| `offset` (query) | number | `0.0` |  |
| `max_px` (query) | integer | `300` |  |

#### `GET /api/rasters/info`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `GET /api/rasters/metadata`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `POST /api/rasters/profile`: Heights (a band's values) along a line: View ▸ Measure ▸ Profile.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `coords` | list of list of number | **required** | 2–5000 items |
| `band` | integer | `1` |  |
| `samples` | integer | `256` |  |

#### `POST /api/rasters/upload`

#### `DELETE /api/tables`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `GET /api/tables/calc-preview`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `expression` (query) | text | **required** |  |

#### `POST /api/tables/derive`: Save the rows matching the current search / filter as a new table.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `q` | text | `""` |  |
| `name` | text | `"selection"` | up to 80 characters |

#### `POST /api/tables/edit`: Edit a table: add / calculate / rename / convert / delete fields, edit cells, add / delete rows (one undo step).

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `ops` | list of object | **required** | 1–50 items |

#### `POST /api/tables/edit/discard`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |

#### `POST /api/tables/edit/log`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `add` | list of text | none | 0–50 items |
| `pop` | integer | `0` | ≥ 0; ≤ 50 |

#### `POST /api/tables/edit/save`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |
| `mode` | text | `"overwrite"` |  |
| `name` | text (optional) | none | up to 80 characters |

#### `POST /api/tables/edit/start`: Start (or resume) an edit session: changes go to a working copy until they are saved.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |

#### `GET /api/tables/points`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `q` (query) | text | `""` |  |
| `lon` (query) | text (optional) | none |  |
| `lat` (query) | text (optional) | none |  |
| `crs` (query) | text (optional) | none |  |

#### `GET /api/tables/preview`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `n` (query) | integer | `20` |  |

#### `POST /api/tables/restore`: Bring back the version from before the last save over this table.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |

#### `GET /api/tables/rows`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |
| `offset` (query) | integer | `0` |  |
| `limit` (query) | integer | `100` |  |
| `q` (query) | text | `""` |  |
| `sort` (query) | text (optional) | none |  |
| `desc` (query) | yes / no | `false` |  |

#### `GET /api/tables/stats`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` (query) | text | **required** |  |

#### `POST /api/tables/undo`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `path` | text | **required** |  |

#### `POST /api/tables/upload`: Add a CSV / TSV / Parquet / Excel table: it is stored in tables/ so every tool can use it.

#### `POST /api/vector/place`: Find a place by name (OpenStreetMap search; needs the internet): a point layer, or the place's outline.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `place` | text | **required** | up to 300 characters |
| `outline` | yes / no | `false` |  |
| `name` | text | `""` | up to 80 characters |

#### `POST /api/view/animation`: The time slider's layers as an animated GIF or an MP4 video, each frame placed by its bounds and labelled.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `frames` | list of Frame | **required** | 2–300 items |
| `fps` | number | `1.0` | ≥ 0.1; ≤ 30 |
| `format` | text | `"gif"` | one of `gif`, `mp4` |
| `width` | integer | `900` | ≥ 200; ≤ 2000 |
| `name` | text | `"animation"` | up to 80 characters |

*Frame*:

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `image` | text | **required** | up to 60000000 characters |
| `bounds` | list of list of number | **required** |  |
| `label` | text | `""` | up to 120 characters |

#### `PUT /api/workflows/{wid}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `workflow` | object | **required** |  |

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `wid` (path) | text | **required** |  |

#### `DELETE /api/workflows/{wid}`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `wid` (path) | text | **required** |  |

#### `PUT /api/workflows/{wid}/schedule`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `schedule` | object | **required** |  |

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `wid` (path) | text | **required** |  |

#### `DELETE /api/workflows/{wid}/schedule`

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `wid` (path) | text | **required** |  |

#### `POST /api/workflows/{wid}/schedule/ran`: A scheduled run finished (or failed): its next run is set.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `ok` | yes / no | **required** |  |
| `message` | text | `""` | up to 2000 characters |
| `alert` | yes / no | `false` |  |

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `wid` (path) | text | **required** |  |

#### `POST /api/workflows/{wid}/values`: Keep the values a run read, for the next run's “falls by / rises by” conditions.

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `values` | object | none |  |

| Parameter | Type | Default | Allowed |
|---|---|---|---|
| `wid` (path) | text | **required** |  |

