"""Prepare the disease models for Hugging Face: half-precision .safetensors (about 95 MB per model instead of 190 MB, and
no pickled code) with a config.json each, checked against the original .pth weights.

    python -m lulc_fetch.agri.publish ~/Desktop/Farmer_ai <out folder> [photo folder for the check]

Writes <out>/<Crop>/model.safetensors + config.json, <out>/detectors/{original,new}/…, and <out>/index.json (files, sizes,
SHA-256). Upload <out> to the model repository (HUB_REPO in disease.py) afterwards, e.g. with `hf upload`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from . import disease, knowledge


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


def main(argv: list[str]) -> int:
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
