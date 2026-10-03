"""Agriculture tools: crop disease diagnosis from leaf photos and the crop disease guide.

They come from the Multi-Crop Disease Decision Support System (github.com/agritechixr/multicrop-disease-decision-support):
per-crop ConvNeXt disease models for 42 crops, two crop detectors, and a Q&A knowledge base. This folder holds the code
and the small files (class labels, test results, knowledge base, in data/). The model weights are on Hugging Face
(https://huggingface.co/ixrbhii/multicrop-disease-models, about 95 MB per crop): each one downloads the first time it is
needed, or a local copy of the disease repository can be used instead. Every crop's model is also its own timm repository
(e.g. ixrbhii/tomato-disease-convnext), and the knowledge base is the dataset ixrbhii/crop-disease-qa.

    disease.py      runs the models (in the deep-learning helper process, see lulc_fetch.dlrunner)
    knowledge.py    the crop list and the knowledge base (no PyTorch needed)
    labels.py       friendly crop and disease names
    import_data.py  refreshes data/ from a copy of the disease repository
    publish.py      converts the models for Hugging Face and updates the combined and the one-crop repositories
"""
