# Changelog

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
