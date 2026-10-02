"""Make training data from the real scene: 12 bands + SCL → patches."""

import json
from pathlib import Path

TOOL = "Make training data"


def test_patches(s2_patches, report):
    meta = json.loads((Path(s2_patches["folder"]) / "dataset.json").read_text())
    n = len(list((Path(s2_patches["folder"]) / "images").glob("*.tif")))
    report.metric("patches", n)
    report.metric("patch size (px)", f"{meta['patch_size_px'][0]} × {meta['patch_size_px'][1]}")
    report.table("Classes", ["value", "name", "pixels"], [[c["value"], c.get("name"), c.get("pixels")] for c in meta["classes"]])
    assert n >= 50 and meta["band_count"] == 12 and len(meta["classes"]) == 3
