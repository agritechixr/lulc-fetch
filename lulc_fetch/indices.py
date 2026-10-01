"""Spectral indices commonly used as LULC features. Inputs are surface reflectance (0-1)."""

from __future__ import annotations

import numpy as np


def _nd(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b) / (a + b)


INDICES = {
    # name: (required bands, function)
    "NDVI": (("B08", "B04"), lambda b: _nd(b["B08"], b["B04"])),            # vegetation
    "NDWI": (("B03", "B08"), lambda b: _nd(b["B03"], b["B08"])),            # open water (McFeeters)
    "MNDWI": (("B03", "B11"), lambda b: _nd(b["B03"], b["B11"])),           # water, robust to built-up
    "NDBI": (("B11", "B08"), lambda b: _nd(b["B11"], b["B08"])),            # built-up
    "BSI": (("B11", "B04", "B08", "B02"),                                   # bare soil
            lambda b: _nd(b["B11"] + b["B04"], b["B08"] + b["B02"])),
    "SAVI": (("B08", "B04"),                                                # vegetation, sparse cover
             lambda b: 1.5 * (b["B08"] - b["B04"]) / (b["B08"] + b["B04"] + 0.5)),
}


def compute_indices(bands: dict[str, np.ndarray], names=None) -> tuple[list[np.ndarray], list[str]]:
    """Compute every index whose input bands are available. Returns (arrays, names)."""
    out, labels = [], []
    for name in names or INDICES:
        required, fn = INDICES[name]
        if all(r in bands for r in required):
            with np.errstate(divide="ignore", invalid="ignore"):
                out.append(fn(bands).astype("float32"))
            labels.append(name)
    return out, labels
