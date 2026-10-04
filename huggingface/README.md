# Hugging Face

`space/` is a Gradio Space that diagnoses leaf photos online with LULC Fetch's own code
(`python -m lulc_fetch.agri.publish --space <folder> [--upload]` copies in `lulc_fetch/agri` and uploads it).

**Not deployed:** Hugging Face only hosts Gradio Spaces for Pro accounts, even on free CPU hardware, so it is kept here for
later. LULC Fetch doesn't need it: the app downloads each crop's model from
[ixrbhii/multicrop-disease-models](https://huggingface.co/ixrbhii/multicrop-disease-models) the first time it's needed.

To try the Space on your own computer (`pip install gradio`, plus the app's deep-learning add-on):

```bash
cd huggingface/space && PYTHONPATH=../.. python app.py      # then open http://127.0.0.1:7860
```

## Data library

The app's **Library** menu lists every public dataset of `ixrbhii` and adds its files to the map. The first is
[ixrbhii/indian-shapefiles](https://huggingface.co/datasets/ixrbhii/indian-shapefiles): 307 GeoJSON layers of India (states,
districts, sub-districts, villages, constituencies, city wards, highways, railways …) from
[datta07/INDIAN-SHAPEFILES](https://github.com/datta07/INDIAN-SHAPEFILES), MIT licence. Its local copy is in
`data/hf_datasets/indian-shapefiles/` (not in git). To add a dataset:

```bash
python -m lulc_fetch.library catalog <folder> "Title" "source / licence"
python -m lulc_fetch.library upload  <folder> ixrbhii/<dataset-name>      # resumes if interrupted
```
