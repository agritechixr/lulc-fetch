# LULC Fetch tests

Automatic tests for every tool, on small synthetic data made fresh for each run (a few seconds to generate, nothing
to download). Run them before every release to check that each tool still works end to end, through the same API the
app's screens use.

## Run

From the project folder:

```bash
.venv/bin/pip install -e ".[web,test]"          # once: pytest + the app
.venv/bin/python -m pytest                        # everything that works offline (about 2 minutes)
.venv/bin/python -m pytest -m "not dl"            # quick: skip deep learning (about 30 seconds)
.venv/bin/python -m pytest --network              # also the tests that need the internet
.venv/bin/python -m pytest tests/test_pca.py      # one tool
.venv/bin/python -m pytest -k "rf or kmeans"      # tests whose name matches
.venv/bin/python -m pytest -x --keep              # stop at the first failure and keep its workspace to look at
```

**Real data:** `tests/real/` runs the same tools on the real data in `data/` (Sentinel-2 and Sentinel-1 products,
tables, photo and detection datasets) and scores them against real ground truth. It takes 15–25 minutes, so it only
runs with `--real`: see [real/README.md](real/README.md).

```bash
.venv/bin/python -m pytest tests/real --real
```

## Reports

Every run writes a report to `test_reports/` (not committed to git):

```
test_reports/
  index.html                       all runs, newest first: version, commit, passed / failed, key numbers
  history.csv                      the same as a table
  2026-10-03_101500_real/          one run
    index.html                     totals, environment (versions of the app and libraries), one row per tool
    classical_ml_tabular_data/     one folder per tool
      index.html                   each test (passed / failed / skipped, time, error), metrics, tables, pictures, files
      results.json
      *.png, *_evaluation.html …
```

Open `test_reports/index.html` in a browser to compare versions. Options: `--report-dir PATH`, `--no-report`.

The tests never touch your own files: they work in a temporary workspace (`LULC_HOME`) with its own settings folder,
and delete it at the end (unless `--keep`, which prints where it is).

## What is tested

| File | Tool | Checks |
|---|---|---|
| `test_app.py` | the app | starts; every tool is in the Tools list and has a panel; requests from other websites are refused; raster upload, info, metadata, delete; paths outside the workspace refused; projects (new, state, close); folder browser |
| `test_find_imagery.py` | Find imagery | AOI from a GeoJSON file; *(--network)* Sentinel-2 search, geocoding |
| `test_index_analysis.py` | Index analysis | 6 indices, composites, single band, RGB, formulas, area clip; NDVI values right over water / vegetation / built-up; GeoTIFF export of several indices |
| `test_pca.py` | PCA & dimensionality reduction | all 7 methods; area clip |
| `test_stack.py` | Stack layers | bands + index + class raster on one grid; coarser pixels; area clip |
| `test_raster_to_table.py` | Raster → table, data viewer | class raster and polygon ground truth; CSV and Parquet; random sampling; rows, sort, stats, points; search → new table; edit sessions (add field, rename, undo, save); Python scripts (and their errors); field calculator incl. `$area`; table upload |
| `test_classical_ml.py` | Classical ML (tabular data) | **all 16 models** train above 85 % accuracy; tuning + cross-validation; regression; report saved to a folder; model comparison; Your models; classified map matches the truth (> 95 %) |
| `test_unsupervised.py` | Classical ML: unsupervised | all 6 clustering methods (the 3 zones are found: ARI > 0.9); automatic k; t-SNE |
| `test_raster_ml.py` | Classical ML for raster | 7 models from polygons (map accuracy > 90 %); class raster + area; class names and colours kept |
| `test_export.py` | Export data | GeoTIFF, PNG, PNG + world file, Shapefile (incl. class breaks, area clip); save into a folder; vectors as Shapefile, GeoJSON, KML |
| `test_training_data.py` | Make training data | plan; patches with raster, polygon or no ground truth; offered to Train classify model; existing folder refused |
| `test_train_classify_model.py` | Train classify model, Classify image | U-Net, FPN, LinkNet, LR-ASPP; a U-Net that learns (mIoU > 0.6) and maps the image (> 80 %); resume; Cancel keeps the best model; wrong bands refused; YOLO26 semantic |
| `test_detection.py` | Detect object, Train detection model | model list; bad requests refused; torchvision SSDlite; YOLO26 boxes, outlines, aerial (DOTA); SAM outlines and segment everything; training boxes / outlines / rotated boxes from polygons, and boxes from points; the trained model in Detect object with automatic zoom; labels outside the image refused |
| `test_jobs_and_cli.py` | Downloads & jobs, command line | job list, files, delete, path traversal refused; cache; `lulc-fetch --help`; *(--network)* `lulc-fetch search` |

*Training samples* is drawn in the browser; its layers are tested where they are used (ground truth, vector export).

## Markers

| Marker | Meaning | When it is skipped |
|---|---|---|
| `dl` | needs the deep-learning add-on (PyTorch) | automatically, if PyTorch isn't installed |
| `yolo` | needs the YOLO & SAM add-on (ultralytics) | automatically, if ultralytics isn't installed |
| `weights` | downloads small pretrained weights the first time (SSDlite 13 MB, YOLO26 nano ~6 MB, SAM 2.1 tiny 75 MB) | never; deselect with `-m "not weights"` when offline |
| `slow` | takes more than ~10 s | never; deselect with `-m "not slow"` |
| `network` | needs the internet | unless `--network` |

Deep-learning models train from random weights for a few epochs, so those tests check that everything runs and is
saved correctly, not how good the model gets (except the one marked `slow`).

## The test data (`_data.py`)

* `s2.tif`: 96 × 96 px, 10 m, UTM 43N, 6 Sentinel-2 bands. Water, vegetation and built-up in three vertical zones with
  typical spectra and a little noise, so classifiers and clustering have a known right answer.
* `labels.tif`: the true classes (1 water, 2 vegetation, 3 built-up).
* `samples`: 6 labelled polygons (2 per class), like Training samples.
* `aerial.tif`: 320 × 320 px, 0.25 m RGB "car park" with 40 bright cars; `cars`: their outlines (class `car`).

## Adding a test

Use the `client`, `data`, `s2_info` and `table` fixtures from `conftest.py`; `tests.helpers.run(client, url, body)`
starts a background job and returns its result, and `ok(response)` checks a response. Set `LULC_TEST_JOB_TIMEOUT`
(seconds, default 900) for slow machines.
