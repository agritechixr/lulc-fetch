"""Radiometry of a GRD product, in its own (radar) geometry: digital numbers → backscatter.

    read_window(product, pol, window, looks, …) -> linear backscatter of a window, multilooked

Steps (each optional): border-noise masking (older products: IPF < 2.90), thermal noise removal (the product's range
and azimuth noise vectors: P = DN² − noise), radiometric calibration (σ⁰, β⁰ or γ⁰ LUT: value = P / A²). Calibration
and noise grids are interpolated bilinearly to the pixels, as ESA's S1TBX does.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import numpy as np
import rasterio
from rasterio.windows import Window

CAL_KEY = {"sigma0": "sigmaNought", "beta0": "betaNought", "gamma0": "gamma", "dn": "dn"}


def _grid(lines, pixels_list, values_list):
    """Vectors given at lines (each with its own pixel positions) → a regular [lines × pixels] grid on the first
    vector's pixels."""
    px = pixels_list[0]
    return np.asarray(lines, float), px, np.array([np.interp(px, p, v) for p, v in zip(pixels_list, values_list)])


def calibration_lut(xml: str, kind: str = "sigma0"):
    r = ET.fromstring(xml)
    lines, pxs, vals = [], [], []
    for v in r.iter("calibrationVector"):
        lines.append(int(v.find("line").text))
        pxs.append(np.array(v.find("pixel").text.split(), float))
        vals.append(np.array(v.find(CAL_KEY[kind]).text.split(), float))
    return _grid(lines, pxs, vals)


def noise_luts(xml: str):
    """(range grid, azimuth blocks): range noise at (line, pixel); azimuth blocks [(l0, l1, p0, p1, lines, lut)]
    multiply it (IPF ≥ 2.90; older products have range vectors only)."""
    r = ET.fromstring(xml)
    lines, pxs, vals = [], [], []
    for v in list(r.iter("noiseRangeVector")) or list(r.iter("noiseVector")):
        lines.append(int(v.find("line").text))
        pxs.append(np.array(v.find("pixel").text.split(), float))
        lut = v.find("noiseRangeLut") if v.find("noiseRangeLut") is not None else v.find("noiseLut")
        vals.append(np.array(lut.text.split(), float))
    rng = _grid(lines, pxs, vals)
    az = []
    for v in r.iter("noiseAzimuthVector"):
        g = lambda t, d: int(v.find(t).text) if v.find(t) is not None else d   # noqa: E731
        ls = np.array(v.find("line").text.split(), float)
        lut = np.array(v.find("noiseAzimuthLut").text.split(), float)
        az.append((g("firstAzimuthLine", 0), g("lastAzimuthLine", 10 ** 9), g("firstRangeSample", 0), g("lastRangeSample", 10 ** 9), ls, lut))
    return rng, az


def interp(grid, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """A (lines, pixels, values) grid at rows × cols (bilinear: along the pixels, then between lines)."""
    lines, px, v = grid
    along = np.array([np.interp(cols, px, row) for row in v])
    if len(lines) == 1:
        return np.repeat(along, len(rows), 0)
    i = np.clip(np.searchsorted(lines, rows) - 1, 0, len(lines) - 2)
    w = np.clip((rows - lines[i]) / (lines[i + 1] - lines[i]), 0, 1)[:, None]
    return along[i] * (1 - w) + along[i + 1] * w


def noise_power(rng, az, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    n = interp(rng, rows, cols)
    if az:
        f = np.ones_like(n)
        for l0, l1, p0, p1, ls, lut in az:
            ri = (rows >= l0) & (rows <= l1)
            ci = (cols >= p0) & (cols <= p1)
            if ri.any() and ci.any():
                f[np.ix_(ri, ci)] = np.interp(rows[ri], ls, lut)[:, None]
        n = n * f
    return n


def border_mask(dn: np.ndarray, min_dn: float = 30, width: int = 2000) -> np.ndarray:
    """Old products (IPF < 2.90) have noisy, low-valued strips at the near and far range edges: masked from each edge
    inwards until the row's values reach normal levels."""
    mask = np.ones(dn.shape, bool)
    for r in range(dn.shape[0]):
        row = dn[r]
        good = np.convolve(row > min_dn, np.ones(20), "same") >= 18   # 18 of 20 pixels above the floor
        idx = np.flatnonzero(good)
        if not idx.size:
            mask[r] = False
            continue
        a, b = idx[0], idx[-1]
        mask[r, :min(a, width)] = False
        mask[r, max(b + 1, dn.shape[1] - width):] = False
    return mask


def read_window(prod, pol: str, window: tuple[int, int, int, int], looks: tuple[int, int] = (1, 1), *, thermal: bool = True,
                border: bool = False, kind: str = "sigma0", progress_cb=None, nesz: bool = False):
    """Linear backscatter (σ⁰ / β⁰ / γ⁰, or DN² uncalibrated with kind "dn") of window (row0, col0, rows, cols) of the
    product's image, multilooked by looks (azimuth, range): power averaged, then calibrated. NaN where there is no data.

    After thermal noise removal the darkest surfaces (calm water, smooth sand, tarmac) can come out at or below zero:
    they are kept at 1 % of the noise power (−20 dB under it) instead of zero or negative, so dB stays defined. With
    nesz=True the noise-equivalent sigma zero (the noise power, calibrated alike) comes back too, so pixels weaker than
    the noise can be flagged: (image, nesz)."""
    r0, c0, nr, nc = window
    la, lr = looks
    nr, nc = nr // la * la, nc // lr * lr
    cal = calibration_lut(prod.text(prod.calibration[pol]), "sigma0" if kind == "dn" else kind)
    nz = noise_luts(prod.text(prod.noise[pol])) if thermal and pol in prod.noise else None
    out = np.full((nr // la, nc // lr), np.nan, "float32")
    nout = np.full(out.shape, np.nan, "float32") if nesz else None
    cols = c0 + np.arange(nc // lr) * lr + (lr - 1) / 2
    with rasterio.open(prod.measurement[pol]) as src:
        step = max(la, 2048 // la * la)
        for rr in range(0, nr, step):
            n = min(step, nr - rr)
            dn = src.read(1, window=Window(c0, r0 + rr, nc, n)).astype("float32")
            valid = dn > 0
            if border:
                valid &= border_mask(dn)
            p = (dn.astype("float64") ** 2).reshape(n // la, la, nc // lr, lr).mean(axis=(1, 3))
            ok = valid.reshape(n // la, la, nc // lr, lr).all(axis=(1, 3))
            rows = r0 + rr + np.arange(n // la) * la + (la - 1) / 2
            npow = noise_power(*nz, rows, cols) if nz is not None else None
            if npow is not None:
                p = np.maximum(p - npow, 0.01 * npow)
            if kind != "dn":
                a = interp(cal, rows, cols)
                p = p / a ** 2
                if npow is not None:
                    npow = npow / a ** 2
            p = np.where(ok, p, np.nan)
            out[rr // la: rr // la + n // la] = p
            if nout is not None and npow is not None:
                nout[rr // la: rr // la + n // la] = np.where(ok, npow, np.nan)
            if progress_cb:
                progress_cb((rr + n) / nr)
    return (out, nout) if nesz else out
