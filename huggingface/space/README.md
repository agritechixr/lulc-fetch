---
title: Crop Disease Diagnosis
emoji: 🌿
colorFrom: green
colorTo: yellow
sdk: gradio
sdk_version: 6.29.1
python_version: "3.11"
app_file: app.py
pinned: true
license: apache-2.0
short_description: Leaf photo → crop → disease, for 42 crops
models:
- ixrbhii/multicrop-disease-models
datasets:
- ixrbhii/crop-disease-qa
tags:
- agriculture
- plant-disease
---

# Crop disease diagnosis

Upload a leaf photo: the crop is recognised (42 crops) and that crop's model gives the three most likely diseases, with
symptoms and treatment from the [crop disease Q&A](https://huggingface.co/datasets/ixrbhii/crop-disease-qa). Unclear photos
(too dark, blank, blurry, not a leaf) get "retake" instead of a guess.

This Space runs the same code as **Agri ▸ Diagnose crop disease** in [LULC Fetch](https://github.com/agritechixr/lulc-fetch),
which uses it to diagnose photos without downloading any model. Photos are only held while they are diagnosed: nothing is stored.

**API:** `diagnose(image_b64, crop="auto", strict=True)`, see "Use via API" at the bottom of the page, e.g.

```python
from gradio_client import Client
import base64
r = Client("ixrbhii/crop-disease-diagnosis").predict(base64.b64encode(open("leaf.jpg", "rb").read()).decode(), "auto", True, api_name="/diagnose")
print(r["crop_name"], r["diagnosis"], r["conf"])
```

Models: [collection](https://huggingface.co/collections/ixrbhii/multi-crop-disease-models-42-crops-6ac0a3291f2153fa716e2993) (ConvNeXt-Small, CC-BY-4.0).
A diagnosis supports, but doesn't replace, a local agriculture expert.
