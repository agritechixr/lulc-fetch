"""Resampling methods: how pixel values are computed when a raster changes resolution or grid.

Tools take an optional `resampling` name. Deep inside a job (imagery and embedding downloads, training patches) the
chosen method is set for that job's thread with `using(name)` and read with `pick(default)`, so the functions in
between don't each need a new parameter.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

from rasterio.enums import Resampling

# name → (rasterio method, what it is for)
METHODS = {
    "nearest": (Resampling.nearest, "Nearest neighbour: keeps the exact values. For classes, labels and masks."),
    "bilinear": (Resampling.bilinear, "Bilinear: smooth, from the 4 nearest pixels. For reflectance, NDVI, heights."),
    "cubic": (Resampling.cubic, "Cubic (bicubic): from 16 pixels; sharper than bilinear. Good for imagery and enlarging."),
    "bicubic": (Resampling.cubic, "Same as cubic."),
    "cubic_spline": (Resampling.cubic_spline, "Cubic spline: very smooth curves; for elevation and smooth surfaces."),
    "lanczos": (Resampling.lanczos, "Lanczos: the sharpest for enlarging imagery; may add slight halos at edges."),
    "average": (Resampling.average, "Average: the mean of the pixels covered. For shrinking continuous data."),
    "mode": (Resampling.mode, "Mode: the most common value. For shrinking class maps."),
    "min": (Resampling.min, "Minimum of the pixels covered."),
    "max": (Resampling.max, "Maximum of the pixels covered."),
    "med": (Resampling.med, "Median of the pixels covered: robust to outliers when shrinking."),
    "q1": (Resampling.q1, "First quartile of the pixels covered."),
    "q3": (Resampling.q3, "Third quartile of the pixels covered."),
}
NAMES = tuple(METHODS)
PATTERN = "^(" + "|".join(NAMES) + ")$"

_current: contextvars.ContextVar = contextvars.ContextVar("lulc_resampling", default=None)


def get(name: str | None, default: Resampling = Resampling.bilinear) -> Resampling:
    if not name:
        return default
    if name not in METHODS:
        raise ValueError(f"Unknown resampling method {name!r}; one of {', '.join(NAMES)}")
    return METHODS[name][0]


def suggest(classes: bool, shrinking: bool = False) -> str:
    """The usual choice: classes keep their values (nearest, or mode when shrinking); values are interpolated."""
    if classes:
        return "mode" if shrinking else "nearest"
    return "average" if shrinking else "bilinear"


@contextmanager
def using(name: str | None):
    """Within the block (a job's work), continuous data is resampled with `name` instead of the tool's default."""
    token = _current.set(name or None)
    try:
        yield
    finally:
        _current.reset(token)


def pick(default: Resampling) -> Resampling:
    """The method chosen for this job (using()), else the default. Class data should keep nearest: callers ask only for
    continuous data."""
    name = _current.get()
    return METHODS[name][0] if name in METHODS else default


def describe() -> list[dict]:
    return [{"name": k, "about": v[1]} for k, v in METHODS.items() if k != "bicubic"]
