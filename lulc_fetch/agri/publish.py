"""Prepare the disease models for Hugging Face: half-precision .safetensors (about 95 MB per model instead of 190 MB, and
no pickled code) with a config.json each, checked against the original .pth weights.

    python -m lulc_fetch.agri.publish ~/Desktop/Farmer_ai <out folder> [photo folder for the check]

Writes <out>/<Crop>/model.safetensors + config.json, <out>/detectors/{original,new}/…, and <out>/index.json (files, sizes,
SHA-256). Upload <out> to the model repository (HUB_REPO in disease.py) afterwards, e.g. with `hf upload`.

One repository per crop as well (timm format: timm.create_model("hf-hub:<repo>", pretrained=True)), and a collection:

    python -m lulc_fetch.agri.publish --repos <out> <repos folder> [--upload | --cards]
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from . import disease, knowledge
from .labels import match_crop


def _state(path: Path) -> dict:
    import torch
    s = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(s, dict):
        s = s.get("model_state_dict") or s.get("state_dict") or s
    return s


def convert(src: Path, out: Path, photos: list[Path]) -> dict:
    import numpy as np
    import torch
    from safetensors.torch import save_file

    found = disease.find_models(src)
    meta = knowledge.meta()
    jobs = [(k, Path(p), out / k) for k, p in sorted(found["crops"].items())]
    jobs += [(k, Path(p), out / "detectors" / k) for k, p in sorted(found["detectors"].items())]
    imgs = [disease.open_photo(p)[0] for p in photos]
    rng = np.random.default_rng(0)
    from PIL import Image
    imgs += [Image.fromarray(rng.integers(0, 255, (224, 224, 3), dtype=np.uint8)) for _ in range(4)]   # off-distribution too
    index, report = {}, []
    for key, weights, dest in jobs:
        classes = disease._classes(key, weights)
        ref = disease._Model(str(weights), classes, torch.device("cpu"))
        state = {k: v.half().contiguous() if v.is_floating_point() else v.contiguous() for k, v in ref.net.state_dict().items()}
        dest.mkdir(parents=True, exist_ok=True)
        save_file(state, dest / "model.safetensors")
        info = meta["detectors"][key] if key in ("original", "new") else meta["crops"][key]
        cfg = {"arch": ref.arch, "classes": classes, "image_size": 224, "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
               "kind": "crop_detector" if key in ("original", "new") else info.get("kind", "disease"),
               **({"labels": info["labels"]} if "labels" in info else {}),
               **{k: info.get(k) for k in ("accuracy", "macro_f1", "test_images") if info.get(k) is not None}}
        (dest / "config.json").write_text(json.dumps(cfg, indent=1, ensure_ascii=False), encoding="utf-8")
        # the check: the half-precision model must give the same answers
        new = disease._Model(str(dest / "model.safetensors"), classes, torch.device("cpu"))
        same, worst = 0, 0.0
        for im in imgs:
            a, b = ref.predict(im, top_k=len(classes)), new.predict(im, top_k=len(classes))
            same += a[0][0] == b[0][0]
            pa, pb = dict(a), dict(b)
            worst = max(worst, max(abs(pa[c] - pb[c]) for c in classes))
        report.append((key, same, len(imgs), worst))
        print(f"{key:14} {ref.arch:15} top-1 same {same}/{len(imgs)} · largest probability change {worst:.4f}", flush=True)
        for f in ("model.safetensors", "config.json"):
            p = dest / f
            index[p.relative_to(out).as_posix()] = {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    (out / "index.json").write_text(json.dumps({"files": index}, indent=1), encoding="utf-8")
    return {"models": len(jobs), "report": report}


# ------------------------------------------------------------------ one Hugging Face repository per crop (timm format)

OWNER = "ixrbhii"
COMBINED = f"{OWNER}/multicrop-disease-models"
QA_DATASET = f"{OWNER}/crop-disease-qa"
COLLECTION = "https://huggingface.co/collections/ixrbhii/multi-crop-disease-models-42-crops-6ac0a3291f2153fa716e2993"
CROP_NOTES = {   # shown on that crop's page, from the disease app's known limitations
    "Mulberry": "This model tells **varieties**, not diseases: there were no Mulberry disease photos to train on.",
    "Coffee": "*Cercospora brown eye spot* is usually missed: the model caught 1 of 11 test photos of it (few training photos).",
    "Guava": "`YLD` is the dataset's own label, kept as is.",
    "Brinjal": "`MIT/EB Pest Damage` is the dataset's own label, kept as is.",
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().split("(")[0]).strip("-")


def repo_name(key: str) -> str:
    """The repository of one model: <crop>-disease-convnext, or the crop detectors."""
    if key == "original":
        return f"{OWNER}/leaf-crop-detector-16-convnext"
    if key == "new":
        return f"{OWNER}/leaf-crop-detector-36-convnext"
    c = knowledge.crops()[key]
    return f"{OWNER}/{_slug(c['name'])}-{'variety' if c['kind'] == 'variety' else 'disease'}-convnext"


def _timm_config(arch: str, labels: list[str], raw: list[str]) -> dict:
    import tempfile

    import timm
    from timm.models._hub import save_config_for_hf
    net = timm.create_model(arch, pretrained=False, num_classes=len(labels))
    net.pretrained_cfg = {**net.pretrained_cfg, "input_size": (3, 224, 224), "crop_pct": 1.0, "crop_mode": "center",
                          "interpolation": "bilinear", "mean": (0.485, 0.456, 0.406), "std": (0.229, 0.224, 0.225)}
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "config.json"
        save_config_for_hf(net, f, model_config={"label_names": labels, "label_descriptions": dict(zip(labels, raw))})
        cfg = json.loads(f.read_text())
    cfg["pretrained_cfg"].pop("tag", None)   # timm's default tag names another training run
    return cfg


def _card(key: str, cfg: dict, size_mb: float) -> str:
    meta = knowledge.meta()
    det = key in ("original", "new")
    info = meta["detectors"][key] if det else meta["crops"][key]
    repo = repo_name(key)
    labels = cfg["label_names"]
    if det:
        title = f"Leaf crop detector ({len(labels)} crops)"
        what = (f"Recognises which crop a leaf photo shows, among {len(labels)} crops: {', '.join(labels)}. "
                + ("It is the first step before a crop's disease model." if key == "original" else
                   "Trained on LeafNet and field photos; Pepper, Raspberry, Sorghum and Squash are recognised so they aren't taken "
                   "for another crop, but have no disease model. In the disease app it overrides the 16-crop detector only when it is "
                   "at least 80 % sure of a crop outside Apple, Mango, Cashew, Cherry, Strawberry and Rose."))
        data = ("public leaf-disease photo datasets, including PlantVillage (CC0) and MangoLeafBD (CC-BY-4.0)" if key == "original" else
                "[LeafNet](https://huggingface.co/datasets/enalis/LeafNet) (CC-BY-4.0) and the authors' own field photos")
        rows = ""
    else:
        name = info["name"]
        title = f"{name} leaf {'variety' if info['kind'] == 'variety' else 'disease'} classifier"
        what = f"Tells which of {len(labels)} classes a **{name.lower()}** leaf photo shows: {', '.join(labels)}."
        data = ("[LeafNet](https://huggingface.co/datasets/enalis/LeafNet) (CC-BY-4.0) and the authors' own field photos" if info["limited_kb"]
                else "public leaf-disease photo datasets, including PlantVillage (CC0) and MangoLeafBD (CC-BY-4.0)")
        per = info.get("per_class", {})
        rows = "\n".join(f"| {lab} | {100 * per[r]['recall']:.1f}% | {per[r]['f1']:.3f} | {per[r]['n']} |" for lab, r in zip(labels, cfg["label_descriptions"].values()) if r in per)
        rows = f"\n| Class | Recall | F1 | Test photos |\n|---|---|---|---|\n{rows}\n" if rows else ""
    acc = f"**{100 * info['accuracy']:.1f} %** test accuracy" + (f", macro-F1 {info['macro_f1']:.3f}" if info.get("macro_f1") else "") + \
          (f", on {info['test_images']:,} held-out photos" if info.get("test_images") else "")
    note = CROP_NOTES.get(key, "")
    tags = ["agriculture", "plant-disease" if not det else "crop-classification", "convnext", "image-classification", "timm"]
    return f"""---
license: cc-by-4.0
library_name: timm
pipeline_tag: image-classification
tags:
{chr(10).join(f"- {t}" for t in tags)}
---

# {title}

{what}

ConvNeXt-Small (`{cfg['architecture']}`, from `convnext_small.fb_in22k_ft_in1k`), 224 × 224 RGB, {size_mb:.0f} MB (half precision).
{acc}. Test photos come from the same datasets the model learned from, so real field photos score lower.
{(chr(10) + "> " + note + chr(10)) if note else ""}{rows}
## Use

```python
import timm, torch
from PIL import Image

model = timm.create_model("hf-hub:{repo}", pretrained=True).eval()
cfg = timm.data.resolve_data_config({{}}, model=model)
x = timm.data.create_transform(**cfg)(Image.open("leaf.jpg").convert("RGB")).unsqueeze(0)
p = model(x).softmax(-1)[0]
labels = model.pretrained_cfg["label_names"]
for i in p.argsort(descending=True)[:3]:
    print(labels[i], f"{{100 * p[i]:.1f}} %")
```

The training images were resized to 224 × 224 without cropping; timm's transform above (resize + 224 centre crop) gives the
same result for square photos. `label_descriptions` in config.json maps each name to the dataset's original label.
Best results: one leaf filling most of the photo, in daylight and in focus. Below about 45 % confidence, ask for a better photo.

**In an app:** [LULC Fetch](https://github.com/agritechixr/lulc-fetch) (Agri ▸ Diagnose crop disease) recognises the crop first
and then runs the right disease model, with a photo-quality check and a disease map from geotagged photos.

## Related

- All 44 models (2 crop detectors, 42 crops) as separate repositories: [the collection]({COLLECTION})
- All 44 models in one repository: [{COMBINED}](https://huggingface.co/{COMBINED})
- Symptoms, treatment and pests for each disease: [{QA_DATASET}](https://huggingface.co/datasets/{QA_DATASET})

## Training data and licence

Trained on {data}. Released under CC-BY-4.0: please credit "Multi-crop disease models, IXR (agritechixr)" and the
training datasets. A diagnosis supports, but doesn't replace, a local agriculture expert.
"""


def build_repos(hub: Path, out: Path) -> list[tuple[str, Path]]:
    """One folder per model repository (model.safetensors, timm config.json, README.md) from a converted folder (convert())."""
    import shutil
    built = []
    keys = [k for k in knowledge.crops() if (hub / k / "model.safetensors").is_file()] + \
           [k for k in ("original", "new") if (hub / "detectors" / k / "model.safetensors").is_file()]
    for key in keys:
        src = hub / ("detectors/" + key if key in ("original", "new") else key)
        own = json.loads((src / "config.json").read_text(encoding="utf-8"))
        crops = knowledge.crops()
        labels = own.get("labels") or [crops[m]["name"] if (m := match_crop(c, crops)) else c.replace("_", " ") for c in own["classes"]]
        dest = out / repo_name(key).split("/")[1]
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / "model.safetensors", dest / "model.safetensors")
        cfg = _timm_config(own["arch"], labels, own["classes"])
        (dest / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        (dest / "README.md").write_text(_card(key, cfg, (dest / "model.safetensors").stat().st_size / 1e6), encoding="utf-8")
        built.append((repo_name(key), dest))
    return built


def upload_cards(built: list[tuple[str, Path]]):
    """Upload only the README of each repository (after the cards changed)."""
    from huggingface_hub import HfApi
    api = HfApi()
    for repo, folder in built:
        api.upload_file(path_or_fileobj=str(folder / "README.md"), path_in_repo="README.md", repo_id=repo, commit_message="Update model card")
        print("card", repo, flush=True)


def upload_repos(built: list[tuple[str, Path]], collection_title: str = "Multi-crop disease models (42 crops)") -> str:
    """Create / update each repository and gather them, the combined repository and the Q&A dataset in one collection."""
    from huggingface_hub import HfApi
    api = HfApi()
    for repo, folder in built:
        api.create_repo(repo, repo_type="model", exist_ok=True)
        api.upload_folder(folder_path=str(folder), repo_id=repo, repo_type="model", commit_message="Model, timm config and card")
        print("uploaded", repo, flush=True)
    col = next((c for c in api.list_collections(owner=OWNER) if c.title == collection_title), None)
    if col is None:
        col = api.create_collection(collection_title, namespace=OWNER, exists_ok=True,
                                    description="ConvNeXt leaf-photo models: 2 crop detectors and one disease model per crop, with the Q&A knowledge base.")
    have = {i.item_id for i in api.get_collection(col.slug).items}
    for item, kind in [(COMBINED, "model"), (QA_DATASET, "dataset")] + [(r, "model") for r, _ in built]:
        if item not in have:
            api.add_collection_item(col.slug, item_id=item, item_type=kind, exists_ok=True)
    return f"https://huggingface.co/collections/{col.slug}"


def main(argv: list[str]) -> int:
    if argv[:1] == ["--repos"]:   # python -m lulc_fetch.agri.publish --repos <converted folder> <out> [--upload]
        built = build_repos(Path(argv[1]).expanduser(), Path(argv[2]).expanduser())
        print(f"{len(built)} repositories prepared in {argv[2]}")
        if "--upload" in argv:
            print("collection:", upload_repos(built))
        elif "--cards" in argv:   # only the model cards changed
            upload_cards(built)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    photos = sorted(p for p in Path(argv[2]).iterdir() if p.suffix.lower() in disease.PHOTO_EXTS) if len(argv) > 2 else []
    r = convert(Path(argv[0]).expanduser(), Path(argv[1]).expanduser(), photos)
    bad = [x for x in r["report"] if x[1] != x[2] or x[3] > 0.02]
    print(f"{r['models']} models converted; {len(bad)} differ: {bad}" if bad else f"{r['models']} models converted, all give the same answers")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
