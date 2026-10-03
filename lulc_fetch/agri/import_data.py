"""Refresh lulc_fetch/agri/data/ from a copy of the Multi-Crop Disease Decision Support repository.

    python -m lulc_fetch.agri.import_data ~/Desktop/Farmer_ai

Copies, per crop: the model's class labels, its test results and the Q&A knowledge base; plus the two crop detectors'
class lists. Model weights are not copied: they are published on Hugging Face (see publish.py) and downloaded by the app.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from .labels import CROP_ALIASES, LIMITED_KB, RECOGNISED_ONLY, VARIETY_MODELS, crop_display, label_display

OUT = Path(__file__).resolve().parent / "data"
KB_FIELDS = ("id", "disease", "vision_label", "category", "subcategory", "growth_stage", "question", "answer", "retrieval_text", "source")


def class_names(folder: Path, txt_names=("class_names.txt",), csv_names=("class_mapping.csv",)) -> list[str]:
    """The labels in model output order: class_names.txt first (as the disease app does), else the mapping CSV."""
    for n in txt_names:
        if (folder / n).is_file():
            names = [x.strip() for x in (folder / n).read_text(encoding="utf-8").splitlines() if x.strip()]
            if names:
                return names
    for n in csv_names:
        if (folder / n).is_file():
            with open(folder / n, encoding="utf-8") as f:
                rows = list(csv.reader(f))
            head, rows = rows[0], [r for r in rows[1:] if len(r) >= 2]
            idx = next((i for i, h in enumerate(head) if h.strip().lower() in ("index", "class_index", "idx", "id")), 1)
            return [r[1 - idx].strip() for r in sorted(rows, key=lambda r: int(r[idx]))]
    return []


def report(folder: Path, txt="convnext_report.txt", js="convnext_report.json") -> dict:
    """Test accuracy, macro-F1, test images and per-class recall / F1 from either report format."""
    if (folder / js).is_file():
        d = json.loads((folder / js).read_text(encoding="utf-8"))
        per = {k: {"recall": round(v["recall"], 4), "f1": round(v["f1-score"], 4), "n": int(v["support"])}
               for k, v in (d.get("per_class") or {}).items() if isinstance(v, dict) and "recall" in v}
        return {"accuracy": round(d["test_acc"], 4), "macro_f1": round(d["test_macro_f1"], 4), "test_images": d.get("test_images"),
                "source": d.get("source"), "per_class": per}
    if (folder / txt).is_file():
        t = (folder / txt).read_text(encoding="utf-8")
        acc = re.search(r"Test Accuracy:\s*([\d.]+)%", t)
        per, macro, n = {}, None, None
        for line in t.splitlines():
            m = re.match(r"\s*(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s*$", line)
            if not m:
                continue
            name = m.group(1).strip()
            if name == "macro avg":
                macro = float(m.group(4))
            elif name not in ("weighted avg",):
                per[name] = {"recall": float(m.group(3)), "f1": float(m.group(4)), "n": int(m.group(5))}
        m2 = re.search(r"^\s*accuracy\s+([\d.]+)\s+(\d+)\s*$", t, re.M)
        if m2:
            n = int(m2.group(2))
        return {"accuracy": round(float(acc.group(1)) / 100, 4) if acc else None, "macro_f1": macro, "test_images": n, "per_class": per}
    return {}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    src = Path(argv[0]).expanduser().resolve()
    data = src / "data"
    if not data.is_dir():
        print(f"{src} doesn't look like the disease repository (no data/ folder)")
        return 1
    (OUT / "kb").mkdir(parents=True, exist_ok=True)
    crops, n_records = {}, 0
    for d in sorted(p for p in data.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        classes = class_names(d)
        if not classes:
            continue
        kb = []
        for f in sorted((d / "text").glob("*_clean_data.json")):
            kb += [{k: r.get(k) for k in KB_FIELDS if r.get(k) not in (None, "", "null")} for r in json.loads(f.read_text(encoding="utf-8"))]
        rep = report(d)
        crops[d.name] = {
            "name": crop_display(d.name), "aliases": CROP_ALIASES.get(d.name, []),
            "kind": "variety" if d.name in VARIETY_MODELS else "disease", "limited_kb": d.name in LIMITED_KB,
            "classes": classes, "labels": [label_display(d.name, c) for c in classes],
            "accuracy": rep.get("accuracy"), "macro_f1": rep.get("macro_f1"), "test_images": rep.get("test_images"),
            "per_class": {k: v for k, v in rep.get("per_class", {}).items() if k in classes}, "kb_records": len(kb),
        }
        (OUT / "kb" / f"{d.name}.json").write_text(json.dumps(kb, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        n_records += len(kb)
    master, new = src / "master_model", src / "master_model" / "new_crop_detector"
    detectors = {
        "original": {"classes": class_names(master, ("class_names.txt", "crop_names.txt"), ("class_mapping.csv", "master_class_mapping.csv", "crop_mapping.csv")),
                     **{k: v for k, v in report(master, "crop_classifier_report.txt").items() if k != "per_class"}},
        "new": {"classes": class_names(new), **{k: v for k, v in report(new).items() if k != "per_class"}},
    }
    meta = {"source": "https://github.com/agritechixr/multicrop-disease-decision-support", "crops": crops, "detectors": detectors,
            "recognised_only": sorted(RECOGNISED_ONLY),
            # the disease app's photo gate (vision_model.py): calibrated on the models' test sets
            "thresholds": {"crop": 0.60, "disease": 0.45, "new_crop_route": 0.80, "soft_accept_crop": 0.90, "soft_accept_disease": 0.70,
                           "sharpness": 45},
            "new_detector_reference": ["Apple", "Mango", "Cashew", "Cherry", "Strawberry", "Rose"]}
    (OUT / "crops.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(crops)} crops, {n_records:,} knowledge-base records → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
