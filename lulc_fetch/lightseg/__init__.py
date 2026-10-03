"""Light segmentation models: eleven real-time semantic segmentation networks with 0.1–1 M parameters, for maps from images
or embeddings with any number of bands.

Every model takes ``in_channels`` (3 for RGB, 13 for Sentinel-2, 64 for AlphaEarth, 128 for TESSERA embeddings: all bands
are used, nothing is reduced first) and works on any image size: ``forward`` pads to the model's stride and crops the
class scores back, so the output is always ``(batch, num_classes, height, width)`` for an input ``(batch, in_channels,
height, width)``. Train on 256 × 256 patches (see ``tiles.py`` to cut larger ones) and predict on any size.

    from lulc_fetch.lightseg import build_model, MODELS
    model = build_model("dabnet", in_channels=64, num_classes=6)     # e.g. AlphaEarth embeddings, 6 land-cover classes
    logits = model(torch.randn(8, 64, 256, 256))                      # → (8, 6, 256, 256)

    common.py   the base class (any size), shared blocks
    enet.py, cgnet.py, dabnet.py, lednet.py, fddwnet.py, leanet.py, lsnet.py, efsnet.py, fpenet.py, adscnet.py, tinyunet.py
    tiles.py    cut image / label arrays into 256 × 256 patches (all bands kept)

Needs PyTorch (the app's deep-learning add-on). These are re-implementations from the papers' descriptions: the
structure follows each paper; parameter counts are within about 15 % of the published ones (see MODELS / README.md).
"""

from __future__ import annotations

MODELS = {   # key: title, file, paper, published parameters (Cityscapes, 19 classes, RGB), one-line description
    "enet": ("ENet", "enet", "Paszke et al., 2016", 0.36, "Encoder–decoder with max-unpooling; the classic lightweight baseline"),
    "cgnet": ("CGNet", "cgnet", "Wu et al., 2018", 0.50, "Context Guided blocks: local + surrounding + global context"),
    "dabnet": ("DABNet", "dabnet", "Li et al., 2019", 0.76, "Depth-wise asymmetric bottlenecks; strong accuracy for its size"),
    "lednet": ("LEDNet", "lednet", "Wang et al., 2019", 0.94, "Split-shuffle factorized units + attention pyramid decoder"),
    "fddwnet": ("FDDWNet", "fddwnet", "Liu et al., 2019", 0.77, "Factorized dilated depthwise residual modules"),
    "leanet": ("LEANet", "leanet", "Zhang et al., 2021", 0.74, "Split-channel units with channel + spatial attention"),
    "lsnet": ("LSNet", "lsnet", "1D-convolution design", 0.62, "Every 3×3 convolution replaced by a 3×1 / 1×3 pair"),
    "efsnet": ("EFSNet", "efsnet", "Hu et al., 2020", 0.17, "Continuous shuffle dilated convolutions; the smallest here"),
    "fpenet": ("FPENet", "fpenet", "Liu & Yin, 2019", 0.40, "Feature pyramid encoding blocks + mutual embedding upsampling"),
    "adscnet": ("ADSCNet", "adscnet", "Wang et al., 2019", 0.51, "Asymmetric depthwise separable units + dense dilated connections"),
    "tinyunet": ("TinyUNet", "tinyunet", "U-Net (Ronneberger et al., 2015), compact", 0.24,
                 "A compact U-Net: encoder–decoder with skip connections at every scale, depthwise-separable convolutions"),
}


def model_class(name: str):
    import importlib
    key = name.lower()
    if key not in MODELS:
        raise ValueError(f"Unknown model {name!r}: choose from {', '.join(MODELS)}")
    title, module, *_ = MODELS[key]
    return getattr(importlib.import_module(f"{__name__}.{module}"), title)


def build_model(name: str, in_channels: int, num_classes: int, **kwargs):
    """A light segmentation model for ``in_channels`` input bands and ``num_classes`` classes."""
    return model_class(name)(in_channels=in_channels, num_classes=num_classes, **kwargs)


def summary(in_channels: int = 3, num_classes: int = 19) -> list[dict]:
    """Parameters of every model for these bands and classes, beside the published figure."""
    from .common import count_params
    out = []
    for key, (title, _, paper, published, about) in MODELS.items():
        n = count_params(build_model(key, in_channels, num_classes))
        out.append({"model": key, "title": title, "paper": paper, "published_M": published, "params_M": round(n / 1e6, 3), "about": about})
    return out
