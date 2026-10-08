"""Speckle filters for SAR intensity (linear power, not dB), NaN-aware:

    boxcar · median · lee · refined_lee · lee_sigma · frost · gamma_map

`looks` is the equivalent number of looks (ENL) of the image: the speckle's coefficient of variation is 1 / √ENL
(Sentinel-1 GRDH ≈ 4.4, × the looks of any further multilooking). Over-filtering removes real detail: start with a
5 × 5 window. Refined Lee, Gamma MAP and Lee Sigma keep edges best; the median is slightly biased low on speckle
(about −0.3 dB at 4 looks); boxcar smooths most and blurs edges most.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter, uniform_filter

FILTERS = ("boxcar", "median", "lee", "refined_lee", "lee_sigma", "frost", "gamma_map")


def _local(x: np.ndarray, size: int):
    """Local mean and variance over a size × size window, ignoring NaN."""
    ok = np.isfinite(x)
    xv = np.where(ok, x, 0.0)
    n = uniform_filter(ok.astype(float), size, mode="reflect")
    with np.errstate(invalid="ignore", divide="ignore"):
        m = uniform_filter(xv, size, mode="reflect") / n
        m2 = uniform_filter(xv * xv, size, mode="reflect") / n
    return m, np.maximum(m2 - m * m, 0), ok


def _lee(x, m, v, cu2):
    """Lee's minimum-mean-square estimate with local mean m, variance v and speckle variance cu²."""
    with np.errstate(invalid="ignore", divide="ignore"):
        vx = np.maximum((v - m * m * cu2) / (1 + cu2), 0)
        k = np.where(v > 0, vx / v, 0)
    return m + k * (x - m)


def _shift(a, dy, dx):
    """a moved by (dy, dx) with NaN where nothing comes in."""
    out = np.full_like(a, np.nan)
    h, w = a.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def _at(a, oy, ox):
    """The value at offset (oy, ox) from each pixel (NaN beyond the image)."""
    return _shift(a, -oy, -ox)


def refined_lee(x: np.ndarray, cu2: float) -> np.ndarray:
    """Refined Lee (Lee 1981): in a 7 × 7 window the edge direction is the strongest of four gradients of the 3 × 3
    means (0°, 45°, 90°, 135°); the Lee estimate then uses only the half of the window on the pixel's side of that
    edge (the side whose 3 × 3 mean is closer to the pixel's own)."""
    m3, _, ok = _local(x, 3)
    dirs = [(0, 1), (1, -1), (1, 0), (1, 1)]   # unit steps across each candidate edge (the gradient direction)
    grads = np.stack([np.abs(_at(m3, 2 * dy, 2 * dx) - _at(m3, -2 * dy, -2 * dx)) for dy, dx in dirs])
    d = np.nan_to_num(grads, nan=-1).argmax(0)
    yy, xx = np.mgrid[-3:4, -3:4]
    xv = np.where(ok, x, np.nan)
    out = np.full(x.shape, np.nan)
    for k, (dy, dx) in enumerate(dirs):
        sel = d == k
        if not sel.any():
            continue
        plus = np.abs(_at(m3, 2 * dy, 2 * dx) - m3) <= np.abs(_at(m3, -2 * dy, -2 * dx) - m3)   # the pixel belongs to the + side
        for side, use in ((1, plus), (-1, ~plus)):
            s2 = sel & use
            if not s2.any():
                continue
            mask = (yy * dy + xx * dx) * side >= 0   # the half window on that side (and on the edge line)
            sm = np.zeros(x.shape)
            sm2 = np.zeros(x.shape)
            cnt = np.zeros(x.shape)
            for oy, ox in zip(yy[mask], xx[mask]):
                v = _at(xv, int(oy), int(ox))
                f = np.isfinite(v)
                sm += np.where(f, v, 0)
                sm2 += np.where(f, v * v, 0)
                cnt += f
            with np.errstate(invalid="ignore", divide="ignore"):
                m = sm / cnt
                var = np.maximum(sm2 / cnt - m * m, 0)
            out[s2] = _lee(x, m, var, cu2)[s2]
    return np.where(ok, out, np.nan)


def lee_sigma(x: np.ndarray, size: int, cu2: float, conf: float = 0.9) -> np.ndarray:
    """Lee Sigma (after Lee et al. 2009): only the window's pixels inside the speckle's `conf` range around the a-priori
    3 × 3 mean are used (the gamma distribution's quantiles for the image's looks), their mean and variance corrected
    for that truncation, then the Lee estimate; strong point targets (above the 98th percentile) are kept as they are."""
    from scipy import stats
    looks = 1 / cu2
    q_lo, q_hi = stats.gamma.ppf([(1 - conf) / 2, (1 + conf) / 2], looks, scale=1 / looks)
    # mean and variance of unit-mean speckle truncated to [q_lo, q_hi]
    g = stats.gamma(looks, scale=1 / looks)
    zz = np.linspace(q_lo, q_hi, 2001)
    pdf = g.pdf(zz)
    mt = np.trapezoid(zz * pdf, zz) / np.trapezoid(pdf, zz)
    vt = np.trapezoid((zz - mt) ** 2 * pdf, zz) / np.trapezoid(pdf, zz)
    cu2_t = vt / mt ** 2
    m3, _, ok = _local(x, 3)
    lo, hi = m3 * q_lo, m3 * q_hi
    r = size // 2
    sm = np.zeros(x.shape)
    sm2 = np.zeros(x.shape)
    cnt = np.zeros(x.shape)
    xv = np.where(ok, x, np.nan)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            v = _at(xv, dy, dx)
            f = np.isfinite(v) & (v >= lo) & (v <= hi)
            sm += np.where(f, v, 0)
            sm2 += np.where(f, v * v, 0)
            cnt += f
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.where(cnt > 0, sm / cnt, m3)
        var = np.where(cnt > 1, np.maximum(sm2 / np.maximum(cnt, 1) - m * m, 0), 0)
        out = _lee(x / mt, m / mt, var / mt ** 2, cu2_t) * mt   # in the truncated distribution's terms
        out = np.where(np.isfinite(out), out, m3)
    # point targets (Lee et al. 2009): a pixel above the 98th percentile with at least 5 such pixels in its 3 × 3
    # neighbourhood is a real bright object, kept as it is; a lone bright pixel is just speckle
    hot = np.where(ok, x, 0) > np.nanpercentile(x, 98)
    strong = hot & (uniform_filter(hot.astype(float), 3) * 9 >= 5)
    return np.where(ok, np.where(strong, x, out), np.nan)


def frost(x: np.ndarray, size: int, damping: float = 2.0) -> np.ndarray:
    """Frost: a weighted mean whose weights fall exponentially with distance, faster where the image varies more."""
    m, v, ok = _local(x, size)
    with np.errstate(invalid="ignore", divide="ignore"):
        cv2 = np.where(m > 0, v / (m * m), 0)
    r = size // 2
    num = np.zeros_like(x, dtype="float64")
    den = np.zeros_like(num)
    xv = np.where(ok, x, np.nan)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            wgt = np.exp(-damping * cv2 * np.hypot(dy, dx))
            s = _shift(xv, dy, dx)
            f = np.isfinite(s)
            num += np.where(f, wgt * s, 0)
            den += np.where(f, wgt, 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ok, num / den, np.nan)


def gamma_map(x: np.ndarray, size: int, looks: float) -> np.ndarray:
    """Gamma MAP (Lopes et al. 1990): homogeneous areas → the local mean, heterogeneous ones → MAP estimate, point
    targets → kept."""
    m, v, ok = _local(x, size)
    cu = 1 / np.sqrt(looks)
    cmax = np.sqrt(2) * cu
    with np.errstate(invalid="ignore", divide="ignore"):
        ci = np.sqrt(v) / m
        alpha = (1 + cu * cu) / (ci * ci - cu * cu)
        b = alpha - looks - 1
        d = m * m * b * b + 4 * alpha * looks * m * x
        rmap = (b * m + np.sqrt(np.maximum(d, 0))) / (2 * alpha)
    out = np.where(ci <= cu, m, np.where(ci >= cmax, x, rmap))
    return np.where(ok, out, np.nan)


def filter_image(x: np.ndarray, method: str, size: int = 5, looks: float = 4.4) -> np.ndarray:
    """Speckle-filter one band of linear intensity."""
    if method not in FILTERS:
        raise ValueError(f"Speckle filter: {', '.join(FILTERS)}")
    if size < 3 or size % 2 == 0 or size > 15:
        raise ValueError("The window is an odd size from 3 to 15")
    x = np.asarray(x, dtype="float64")
    cu2 = 1 / max(looks, 0.5)
    if method == "boxcar":
        m, _, ok = _local(x, size)
        return np.where(ok, m, np.nan)
    if method == "median":
        ok = np.isfinite(x)
        f = median_filter(np.where(ok, x, np.nanmedian(x)), size)
        return np.where(ok, f, np.nan)
    if method == "lee":
        m, v, ok = _local(x, size)
        return np.where(ok, _lee(x, m, v, cu2), np.nan)
    if method == "refined_lee":
        return refined_lee(x, cu2)
    if method == "lee_sigma":
        return lee_sigma(x, size, cu2)
    if method == "frost":
        return frost(x, size)
    return gamma_map(x, size, looks)


def enl(x: np.ndarray) -> float:
    """The equivalent number of looks of a homogeneous-looking area: mean² / variance (median over 9 × 9 windows)."""
    m, v, _ = _local(np.asarray(x, float), 9)
    with np.errstate(invalid="ignore", divide="ignore"):
        e = m * m / v
    e = e[np.isfinite(e)]
    return float(np.median(e)) if e.size else float("nan")
