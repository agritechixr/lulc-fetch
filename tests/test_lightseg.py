"""Light segmentation models (lulc_fetch/lightseg): any number of bands, any image size, trainable, close to the
published sizes; and the 256 × 256 tiling (all bands kept)."""

import numpy as np
import pytest

from lulc_fetch.lightseg import MODELS
from lulc_fetch.lightseg.tiles import tile_array

TOOL = "Light segmentation models"


def test_tile_array_keeps_all_bands_and_covers_the_image():
    img = np.random.rand(128, 600, 300).astype(np.float32)          # e.g. TESSERA: 128 bands
    tiles = tile_array(img)
    assert all(t.shape == (128, 256, 256) for t, _ in tiles)
    cover = np.zeros((600, 300), bool)
    for _, (r, c) in tiles:
        cover[r:r + 256, c:c + 256] = True
    assert cover.all() and tiles[-1][1] == (600 - 256, 300 - 256)    # last patch flush with the edge: nothing lost
    small = tile_array(np.ones((64, 100, 90), np.float32))           # smaller than one patch: padded
    assert len(small) == 1 and small[0][0].shape == (64, 256, 256) and small[0][0][:, 100:, :].sum() == 0
    labels = tile_array(np.ones((600, 300), np.int64))
    assert labels[0][0].shape == (256, 256) and len(labels) == len(tiles)


@pytest.fixture(scope="module")
def checks():
    """Every model checked once, in a separate process: PyTorch must not be loaded next to XGBoost / LightGBM."""
    import json
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "lulc_fetch.lightseg.selftest", "--all"], capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-3000:]
    return {d["model"]: d for d in map(json.loads, r.stdout.splitlines())}


@pytest.mark.dl
@pytest.mark.parametrize("name", list(MODELS))
def test_any_bands_any_size(checks, name):
    """3, 64 and 128 bands at 256 × 256, 300 × 217 and 40 × 40: (1, classes, H, W) out; a wrong band count is refused."""
    c = checks[name]
    assert c["shapes_ok"], c.get("bad")
    assert c["wrong_bands_refused"]


@pytest.mark.dl
@pytest.mark.parametrize("name", list(MODELS))
def test_trains_and_size_close_to_paper(checks, name):
    c = checks[name]
    assert abs(c["params_M"] - c["published_M"]) / c["published_M"] < 0.2, c
    assert c["finite"] and c["loss_last"] < c["loss_first"], c   # learns a rule from two of 64 bands
