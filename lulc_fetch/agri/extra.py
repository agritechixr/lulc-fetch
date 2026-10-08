"""Extra disease models from Hugging Face, made by others, next to the app's own per-crop models.

Each one is listed in data/extra_models.json: its source repository pinned to one revision (with the weights' SHA-256),
licence and credit, image preprocessing, and what each of its labels means (crop + disease, or null for "not a crop
photo"). An extra model:

- is the main model for a crop the app has no model of (e.g. Wheat), and
- gives a second opinion for the crops it shares with the app's own models.

Adding another one: a new entry in extra_models.json (format "timm", or "transformers-vit" for a transformers
ViTForImageClassification, converted to timm here so the app doesn't need the transformers package). Nothing else.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
FILE = "model.safetensors"


@lru_cache(maxsize=1)
def registry() -> dict:
    return json.loads((DATA / "extra_models.json").read_text(encoding="utf-8"))["models"]


def crop_models() -> dict[str, list[str]]:
    """crop → the extra models that know it."""
    out = {}
    for key, m in registry().items():
        for crop, _ in filter(None, m["labels"].values()):
            out.setdefault(crop, [])
            if key not in out[crop]:
                out[crop].append(key)
    return out


def crop_labels(key: str, crop: str) -> list[tuple[str, str]]:
    """One crop's (raw label, disease name) pairs in an extra model, without duplicates of the same disease."""
    out, seen = [], set()
    for raw, v in registry()[key]["labels"].items():
        if v and v[0] == crop and v[1] not in seen:
            seen.add(v[1])
            out.append((raw, v[1]))
    return out


def download_url(key: str) -> str:
    d = registry()[key]["download"]
    return f"https://huggingface.co/{d['repo']}/resolve/{d['revision']}/{d['file']}"


def path(key: str, root: str | Path) -> Path:
    """Where the model is kept in the download folder (agri_models/extra/<key>/model.safetensors)."""
    return Path(root).expanduser() / "extra" / key / FILE


# ------------------------------------------------------------------ weights

def vit_to_timm(state: dict) -> dict:
    """transformers ViTForImageClassification weights → timm VisionTransformer weights (same numbers, other names; the
    separate query / key / value layers become timm's one qkv layer)."""
    import torch

    out = {"cls_token": state["vit.embeddings.cls_token"], "pos_embed": state["vit.embeddings.position_embeddings"],
           "patch_embed.proj.weight": state["vit.embeddings.patch_embeddings.projection.weight"],
           "patch_embed.proj.bias": state["vit.embeddings.patch_embeddings.projection.bias"],
           "norm.weight": state["vit.layernorm.weight"], "norm.bias": state["vit.layernorm.bias"],
           "head.weight": state["classifier.weight"], "head.bias": state["classifier.bias"]}
    depth = 1 + max(int(k.split(".")[3]) for k in state if k.startswith("vit.encoder.layer."))
    for i in range(depth):
        a, b = f"vit.encoder.layer.{i}.", f"blocks.{i}."
        for t in ("weight", "bias"):
            out[b + "attn.qkv." + t] = torch.cat([state[a + f"attention.attention.{n}.{t}"] for n in ("query", "key", "value")])
            out[b + "attn.proj." + t] = state[a + "attention.output.dense." + t]
            out[b + "norm1." + t] = state[a + "layernorm_before." + t]
            out[b + "norm2." + t] = state[a + "layernorm_after." + t]
            out[b + "mlp.fc1." + t] = state[a + "intermediate.dense." + t]
            out[b + "mlp.fc2." + t] = state[a + "output.dense." + t]
    return out


def build(key: str, weights: str | Path):
    """The model as a timm network with its weights loaded (eval mode, on the CPU)."""
    from functools import partial

    import timm
    import torch
    from safetensors.torch import load_file

    m = registry()[key]
    try:
        state = {k: v.float() if v.is_floating_point() else v for k, v in load_file(str(weights)).items()}
    except Exception as e:
        raise RuntimeError(f"Couldn't read {weights}: {e}")
    kw = {}
    if m["format"] == "transformers-vit":
        state = vit_to_timm(state)
        kw["norm_layer"] = partial(torch.nn.LayerNorm, eps=m.get("layer_norm_eps", 1e-12))   # transformers' default, not timm's 1e-6
    elif m["format"] != "timm":
        raise RuntimeError(f"{key}: unknown model format {m['format']}")
    net = timm.create_model(m["arch"], pretrained=False, num_classes=len(m["labels"]), **kw)
    net.load_state_dict(state, strict=True)
    return net.eval()
