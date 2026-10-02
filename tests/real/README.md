# Real-data tests

The same tools as the synthetic suite, run on the real data in `data/`, and scored against real ground truth. Use it
before a release, and compare the numbers with earlier versions in `test_reports/index.html`.

```bash
.venv/bin/python -m pytest tests/real --real                        # small sample: about 15–25 minutes
.venv/bin/python -m pytest tests/real --real --real-size medium     # more pictures and epochs
.venv/bin/python -m pytest tests/real --real -k "raster_ml or tables"   # only some tools
.venv/bin/python -m pytest tests/real --real --data /path/to/data   # another data folder
```

Without `--real` these tests are skipped, so a plain `pytest` stays quick. Deep-learning tests need the PyTorch add-on
(and YOLO / SAM the YOLO & SAM add-on); pretrained weights are downloaded once.

## Data used

| Folder in `data/` | Used for |
|---|---|
| `sentinal_multispectral/S2*_MSIL2A_*.SAFE.zip` | Opened with *Your Sentinel products*; a 6 × 6 km area around **Lake Albano** (Italy) is stacked and used by Index analysis, PCA, Stack layers, Export, Raster → table, Classical ML, Classical ML for raster, Make training data and Train classify model. **Ground truth: the product's own SCL scene classification** (vegetation, bare / built-up, water). |
| `sentinal_SAR/S1*_GRDH_*.SAFE.zip` | Sentinel-1 σ⁰ backscatter (VV, VH) for 10 × 15 km over **Vienna**. (This scene doesn't overlap the Sentinel-2 tile, so the two aren't stacked together.) |
| `tabular data/diabetes_risk.csv` | Classical ML: all 16 models predict `diabetes_risk` (8 categorical + 9 numeric columns); model comparison; clustering. |
| `tabular data/India Agriculture Crop Production.csv` | The data viewer on 345 000 rows (sort, filter, statistics, derive, edit session, Python, field calculator); regression of rice production. |
| `classify_data/` (flood, Water Bodies, forest) | Train classify model (U-Net and YOLO26 semantic) on photos + masks, then Classify image on held-out photos, scored by IoU. |
| `detection_data/` (Cars Detection, wind farms) | Detect object with pretrained YOLO26 / Faster R-CNN (Cars, mapped to COCO classes), and Train detection model, then Detect object on the held-out test pictures, scored by precision, recall and AP50. |

### How plain pictures are fed to map tools

The app's tools take georeferenced layers. `adapters.py` turns the picture datasets into that form, without changing
what the models see:

* photo + mask sets → a *Make training data* folder (GeoTIFF patches + `dataset.json`) for Train classify model, and the
  held-out photos as one GeoTIFF grid for Classify image (scored photo by photo);
* YOLO sets → one GeoTIFF grid of the pictures + the labelled boxes as GeoJSON polygons, which is what Train detection
  model takes; Detect object runs on a grid of the test pictures, with tiles exactly one picture each.

## Sample sizes (`--real-size`)

| | small (default) | medium | large |
|---|---|---|---|
| photos for training / testing (per set) | 120 / 10 | 400 / 30 | 1500 / 100 |
| epochs (photo sets / Sentinel-2) | 8 / 12 | 20 / 30 | 40 / 60 |
| detection pictures train / test | 100 / 25 | 225 / 64 | 800 / 126 |
| detection epochs, YOLO size | 30, nano | 40, small | 80, medium |

The thresholds in the tests are guards against something breaking (e.g. "the best diabetes model beats always-'Low'"),
not targets: the numbers themselves are in the report, to be compared between versions.

## Report

Every run writes `test_reports/<date>_<time>_real/` with one folder per tool: test results, metrics, tables (e.g. all
16 models' scores, per-class detection scores), pictures (maps next to the SCL truth, photos with true and predicted
masks, detections drawn on test pictures) and the models' own HTML reports. `test_reports/index.html` lists every run.
