# Light segmentation models

Eleven real-time semantic segmentation networks with 0.1–1 million parameters, written in PyTorch for LULC Fetch. In the app
they are **Embeddings ▸ Train embedding model** / **Classify with embedding model**, and the *Light* architectures of
Train classify model (`dl.ARCHS["light_<name>"]`, backbone "builtin").

- **Any number of bands:** `in_channels` can be 3 (RGB), 13 (Sentinel-2), 64 (AlphaEarth), 128 (TESSERA) or anything
  else. All bands go into the network; nothing is reduced first (run PCA yourself first if you want fewer).
- **Any image size:** each model pads the input to its stride and crops the class scores back, so a
  `(batch, bands, H, W)` input always gives `(batch, classes, H, W)`: train on 256 × 256 patches, predict on any size.
- **256 × 256 patches:** `tiles.py` cuts larger images and label maps into 256 × 256 patches (all bands, nothing lost at the
  edges; smaller images are padded).

```python
import torch
from lulc_fetch.lightseg import build_model, MODELS

model = build_model("dabnet", in_channels=64, num_classes=6)   # AlphaEarth embeddings, 6 classes
logits = model(torch.randn(8, 64, 256, 256))                    # (8, 6, 256, 256)

from lulc_fetch.lightseg.tiles import tile_geotiff
tile_geotiff("embedding.tif", "labels.tif", "patches/")         # patches/images/*.npy (64×256×256), patches/labels/*.npy
```

## The models

Parameters for 19 classes (the papers' Cityscapes setting) with 3, 64 and 128 input bands; time for one 256 × 256 patch
with 64 bands on a laptop CPU (Apple M-series, 8 threads).

| Model | Paper | Published | 3 bands | 64 bands | 128 bands | ms / patch | Idea |
|---|---|---|---|---|---|---|---|
| ENet | Paszke et al., 2016 | 0.36 M | 0.354 M | 0.361 M | 0.369 M | 17 | Encoder–decoder with max-unpooling; the classic baseline |
| CGNet | Wu et al., 2018 | 0.50 M | 0.496 M | 0.620 M | 0.749 M | 67 | Context Guided blocks: local + surrounding + global context |
| DABNet | Li et al., 2019 | 0.76 M | 0.757 M | 0.892 M | 1.023 M | 69 | Depth-wise asymmetric bottlenecks |
| LEDNet | Wang et al., 2019 | 0.94 M | 0.920 M | 0.938 M | 0.956 M | 27 | Split-shuffle factorized units + attention pyramid decoder |
| FDDWNet | Liu et al., 2019 | 0.77 M | 0.757 M | 0.766 M | 0.775 M | 263 | Factorized dilated depthwise residual modules |
| LEANet | Zhang et al., 2021 | 0.74 M | 0.675 M | 0.693 M | 0.711 M | 27 | Split-channel units with channel + spatial attention |
| LSNet | 1D-convolution design | 0.62 M | 0.602 M | 0.620 M | 0.639 M | 22 | Every 3×3 convolution as a 3×1 / 1×3 pair |
| EFSNet | Hu et al., 2020 | 0.17 M | 0.148 M | 0.155 M | 0.163 M | 29 | Continuous shuffle dilated convolutions; the smallest |
| FPENet | Liu & Yin, 2019 | 0.40 M | 0.406 M | 0.415 M | 0.424 M | 78 | Feature pyramid encoding + mutual embedding upsampling |
| ADSCNet | Wang et al., 2019 | 0.51 M | 0.522 M | 0.540 M | 0.558 M | 53 | Asymmetric depthwise separable units + dense dilated connections |
| TinyUNet | U-Net (Ronneberger et al., 2015), compact | — | 0.240 M | 0.249 M | 0.258 M | ≈ 25 * | A small U-Net: 4 down / 4 up stages with skip connections at every scale, depthwise-separable convolutions; sharp edges |

* TinyUNet's time was measured later, scaled to the other models' timing (DABNet on the same run); TinyUNet isn't from a single paper (a compact U-Net), so there is no published count.

CGNet and DABNet grow most with many bands because they feed the (downsampled) input into later stages too; the others
only see it in their first layer. ENet and EFSNet project a many-band input to 3 channels in their pooling branch (their
convolution branch sees every band).

## How faithful they are

These are re-implementations from the papers' descriptions (and, for LEDNet's decoder, the authors' published code), not
ports of the original weights; none is pretrained. The structure follows each paper — block types, stages, channel widths,
dilation rates, decoders — and the parameter counts are within about 15 % of the published ones (each file's docstring
describes its architecture). Where a paper leaves details open, the closest standard choice was made: LSNet follows the
1D-convolution design the name usually refers to (several papers use it); LEANet's attention decoder and EFSNet's decoder
are compact versions of the described ones. Measured accuracy will differ from the papers' Cityscapes numbers anyway:
these models will be trained on your own images and embeddings.

## Files

| File | |
|---|---|
| `__init__.py` | `MODELS`, `build_model(name, in_channels, num_classes)`, `summary()` |
| `common.py` | `SegModel` (any size), convolution / attention / downsampling blocks, `count_params` |
| `enet.py` … `adscnet.py` | one model each |
| `tiles.py` | `tile_array`, `tile_geotiff`: 256 × 256 patches, all bands |

Tests: `tests/test_lightseg.py` (every model with 3, 64 and 128 bands at 256 × 256, 300 × 217 and 40 × 40, a short training
run on 64-band data, sizes against the papers, and the tiling). The model checks run in their own process
(`python -m lulc_fetch.lightseg.selftest --all`), because PyTorch must not share a process with XGBoost / LightGBM.
