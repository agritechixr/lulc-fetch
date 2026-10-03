"""Agriculture tools: crop disease diagnosis from leaf photos and the crop disease guide.

They come from the Multi-Crop Disease Decision Support System (github.com/agritechixr/multicrop-disease-decision-support):
per-crop ConvNeXt disease models for 42 crops, two crop detectors, and a Q&A knowledge base. This folder holds the code
and the small files (class labels, test results, knowledge base, in data/); the model weights (about 190 MB per crop)
stay in a models folder outside the app, chosen in the tool.

    disease.py      runs the models (in the deep-learning helper process, see lulc_fetch.dlrunner)
    knowledge.py    the crop list and the knowledge base (no PyTorch needed)
    labels.py       friendly crop and disease names
    import_data.py  refreshes data/ from a copy of the disease repository
"""
