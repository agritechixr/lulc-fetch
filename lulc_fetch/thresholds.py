"""Automatic thresholds for splitting an image into two kinds of pixels (water / land, change / no change …).

    GLOBAL   one value for the whole image, from its histogram
             otsu (Otsu 1979: largest between-class variance) · multi_otsu (Otsu with three classes: the split between
             the top two, or the bottom two) · li (Li & Lee 1993: minimum cross entropy) · yen (Yen et al. 1995: maximum
             correlation) · kapur (Kapur et al. 1985: maximum entropy) · triangle (Zack et al. 1977: farthest point from
             the line joining the histogram's peak and its far end) · isodata (Ridler & Calvard 1978: the mean of the two
             class means)
    LOCAL    a value per pixel, from its neighbourhood (window × window): for images whose background changes
             niblack (m + k·s) · sauvola (m·(1 + k·(s/R − 1))) · wolf (Wolf & Jolion 2004) · phansalkar (Phansalkar
             et al. 2011, for low-contrast images); m, s: local mean and standard deviation
    FUZZY    a value plus a 0–1 membership for every pixel
             fcm (fuzzy c-means, two clusters: the threshold is where both memberships are ½) · fuzzy (Huang & Wang
             1995: the split whose fuzzy membership is least ambiguous) · membership (an S-shaped membership rising from
             the background class mean to the object class mean; the threshold is its midpoint)

    compute(image, method, valid=…, bright=True, …) -> {"threshold": float or per-pixel array, "membership": array or
    None, "method", "about"}

`split` (default on): global and fuzzy methods look only at the tiles that clearly hold both classes (split-based
selection, Martinis et al. 2009); an image with no such tile gets `default` (e.g. 0 for a water index, −18 dB for
radar VV). Without it every method splits a one-class histogram in two: on Sen1Floods11's chips, most of which hold
little water, that took the IoU of every method from about 0.78 to 0.2–0.35.

`bright`: the object (water in an index image) is brighter than the background; False for dark objects (water in
radar dB). Local methods come from document scanning, where every window holds some ink; in a window of one class only
(all land) they would still split it. `guard` (default 0.1, on the 0–1 scaled image) keeps the global Otsu threshold
where the neighbourhood's standard deviation is below it.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import uniform_filter

GLOBAL = ("otsu", "multi_otsu", "li", "yen", "kapur", "triangle", "isodata")
LOCAL = ("niblack", "sauvola", "wolf", "phansalkar")
FUZZY = ("fcm", "fuzzy", "membership")
TITLES = {"otsu": "Otsu", "multi_otsu": "Multi-Otsu (3 classes)", "li": "Li (minimum cross entropy)", "yen": "Yen", "kapur": "Kapur (maximum entropy)",
          "triangle": "Triangle", "isodata": "Isodata", "niblack": "Niblack", "sauvola": "Sauvola", "wolf": "Wolf", "phansalkar": "Phansalkar",
          "fcm": "Fuzzy c-means", "fuzzy": "Fuzzy threshold (Huang)", "membership": "Fuzzy membership (S-function)"}


def _hist(v: np.ndarray, bins: int = 256):
    h, e = np.histogram(v, bins=bins)
    return h.astype("float64"), (e[:-1] + e[1:]) / 2


def otsu(v, bins=256):
    h, c = _hist(v, bins)
    w0 = np.cumsum(h)
    w1 = w0[-1] - w0
    s = np.cumsum(h * c)
    m0 = s / np.maximum(w0, 1)
    m1 = (s[-1] - s) / np.maximum(w1, 1)
    return float(c[np.argmax(w0 * w1 * (m0 - m1) ** 2)])


def multi_otsu(v, bins=128, upper=True):
    """Three classes: the two thresholds maximising the between-class variance (exhaustive on the histogram)."""
    h, c = _hist(v, bins)
    p = h / h.sum()
    P = np.cumsum(p)
    S = np.cumsum(p * c)
    mu = S[-1]
    best, t = -1.0, (0, 0)
    for i in range(1, bins - 2):
        w0, m0 = P[i], S[i] / max(P[i], 1e-12)
        j = np.arange(i + 1, bins - 1)
        w1 = P[j] - P[i]
        w2 = 1 - P[j]
        m1 = (S[j] - S[i]) / np.maximum(w1, 1e-12)
        m2 = (mu - S[j]) / np.maximum(w2, 1e-12)
        var = w0 * (m0 - mu) ** 2 + w1 * (m1 - mu) ** 2 + w2 * (m2 - mu) ** 2
        k = int(np.argmax(var))
        if var[k] > best:
            best, t = float(var[k]), (c[i], c[j[k]])
    return float(t[1] if upper else t[0]), (float(t[0]), float(t[1]))


def li(v, tol=None):
    """Li's iterative minimum cross-entropy threshold (values shifted to be positive)."""
    v = np.asarray(v, "float64")
    off = v.min()
    x = v - off + 1e-6
    tol = tol or (x.max() - x.min()) / 512
    t = x.mean()
    for _ in range(200):
        fg, bg = x[x > t], x[x <= t]
        if not fg.size or not bg.size:
            break
        mf, mb = fg.mean(), bg.mean()
        tn = (mb - mf) / (math.log(mb) - math.log(mf)) if mb != mf else t
        if abs(tn - t) < tol:
            t = tn
            break
        t = tn
    return float(t + off - 1e-6)


def yen(v, bins=256):
    h, c = _hist(v, bins)
    p = h / h.sum()
    P1 = np.cumsum(p)
    P1sq = np.cumsum(p ** 2)
    P2sq = np.cumsum((p ** 2)[::-1])[::-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        crit = np.log(((P1sq[:-1] * P2sq[1:]) ** -1) * (P1[:-1] * (1.0 - P1[:-1])) ** 2)
    crit = np.where(np.isfinite(crit), crit, -np.inf)
    return float(c[int(np.argmax(crit))])


def kapur(v, bins=256):
    h, c = _hist(v, bins)
    p = h / h.sum()
    P = np.cumsum(p)
    with np.errstate(divide="ignore", invalid="ignore"):
        plogp = np.where(p > 0, p * np.log(p), 0)
        Hc = np.cumsum(plogp)
        Ht = Hc[-1]
        hb = np.log(P) - Hc / P
        hf = np.log(1 - P) - (Ht - Hc) / (1 - P)
        e = hb + hf
    e = np.where(np.isfinite(e), e, -np.inf)
    return float(c[int(np.argmax(e[:-1]))])


def triangle(v, bins=256):
    h, c = _hist(v, bins)
    pk = int(np.argmax(h))
    nz = np.flatnonzero(h)
    lo, hi = nz[0], nz[-1]
    flip = (pk - lo) < (hi - pk)   # the longer tail decides the side
    if flip:
        h, c = h[::-1], c[::-1]
        pk = len(h) - 1 - pk
        lo = len(h) - 1 - hi
    x = np.arange(lo, pk + 1)
    if x.size < 2:
        return float(c[pk])
    x1, y1, x2, y2 = lo, h[lo], pk, h[pk]
    d = np.abs((y2 - y1) * x - (x2 - x1) * h[x] + x2 * y1 - y2 * x1) / math.hypot(y2 - y1, x2 - x1)
    return float(c[x[int(np.argmax(d))]])


def isodata(v, tol=None):
    v = np.asarray(v, "float64")
    t = v.mean()
    tol = tol or (v.max() - v.min()) / 1024
    for _ in range(200):
        a, b = v[v <= t], v[v > t]
        if not a.size or not b.size:
            break
        tn = (a.mean() + b.mean()) / 2
        if abs(tn - t) < tol:
            return float(tn)
        t = tn
    return float(t)


def fcm(v, m=2.0, iters=100):
    """Two-cluster fuzzy c-means in one dimension: (low centre, high centre)."""
    v = np.asarray(v, "float64")
    c = np.percentile(v, [25, 75])
    for _ in range(iters):
        d = np.abs(v[:, None] - c[None]) + 1e-12
        u = 1 / ((d[:, :, None] / d[:, None, :]) ** (2 / (m - 1))).sum(2)
        cn = (u ** m * v[:, None]).sum(0) / (u ** m).sum(0)
        if np.allclose(cn, c, atol=1e-7 * (v.max() - v.min() + 1e-12)):
            break
        c = cn
    return tuple(sorted(c.tolist()))


def fcm_membership(x, centres, m=2.0, bright=True):
    lo, hi = centres
    d_lo, d_hi = np.abs(x - lo) + 1e-12, np.abs(x - hi) + 1e-12
    u_hi = 1 / (1 + (d_hi / d_lo) ** (2 / (m - 1)))
    u_hi = np.where(x >= hi, 1.0, np.where(x <= lo, 0.0, u_hi))
    return u_hi if bright else 1 - u_hi


def huang(v, bins=256):
    """Huang & Wang's fuzzy thresholding: the split minimising the fuzziness (Shannon) of each pixel's membership to its
    class mean."""
    h, c = _hist(v, bins)
    C = c[-1] - c[0] + 1e-12
    best, bt = np.inf, c[bins // 2]
    S, N, SC = np.cumsum(h * c), np.cumsum(h), (h * c).sum()
    for t in range(1, bins - 1):
        if N[t] == 0 or N[-1] - N[t] == 0:
            continue
        m0, m1 = S[t] / N[t], (SC - S[t]) / (N[-1] - N[t])
        mu = np.where(np.arange(bins) <= t, 1 / (1 + np.abs(c - m0) / C), 1 / (1 + np.abs(c - m1) / C))
        mu = np.clip(mu, 1e-12, 1 - 1e-12)
        e = (h * (-mu * np.log(mu) - (1 - mu) * np.log(1 - mu))).sum()
        if e < best:
            best, bt = e, c[t]
    return float(bt)


def s_membership(x, a, c):
    """Zadeh's S-function: 0 at a, ½ at the midpoint b, 1 at c (a < c)."""
    b = (a + c) / 2
    x = np.asarray(x, "float64")
    out = np.where(x <= a, 0.0, np.where(x <= b, 2 * ((x - a) / (c - a)) ** 2, np.where(x <= c, 1 - 2 * ((x - c) / (c - a)) ** 2, 1.0)))
    return out


def gmm2(v: np.ndarray, iters: int = 40):
    """Two-component 1-D Gaussian mixture by EM (started from Otsu's split): (weights, means, variances), the
    lower-mean component first."""
    t = otsu(v, 64)
    a, b = v[v <= t], v[v > t]
    if not a.size or not b.size:
        return None
    w = np.array([a.size, b.size], "float64") / v.size
    mu = np.array([a.mean(), b.mean()])
    var = np.array([max(a.var(), 1e-9), max(b.var(), 1e-9)])
    for _ in range(iters):
        pdf = w / np.sqrt(2 * np.pi * var) * np.exp(-(v[:, None] - mu) ** 2 / (2 * var))
        r = pdf / np.maximum(pdf.sum(1, keepdims=True), 1e-300)
        nk = r.sum(0)
        if (nk < 1e-6 * v.size).any():
            return None
        w = nk / v.size
        mu = (r * v[:, None]).sum(0) / nk
        var = np.maximum((r * (v[:, None] - mu) ** 2).sum(0) / nk, 1e-9)
    o = np.argsort(mu)
    return w[o], mu[o], var[o]


def bimodal_pixels(img: np.ndarray, ok: np.ndarray, tile: int = 64, min_d: float = 2.0, min_frac: float = 0.1, bright: bool = True,
                   side: float | None = None) -> tuple[np.ndarray, int, int]:
    """Split-based selection (Martinis et al. 2009; Chini et al. 2017, HSBA): the pixels of the tiles that clearly
    hold both classes: a two-Gaussian fit whose components are each ≥ min_frac of the tile and separate by Ashman's
    D ≥ min_d (a single-peak tile fits two overlapping halves, D < 2); and, given `side`, whose object component lies
    beyond it (above for bright objects, below for dark: e.g. above 0 for a water index, below −18 dB for radar VV),
    so that land with two kinds of ground isn't taken for land and water. Returns (values, tiles kept, tiles tried)."""
    h, w = img.shape
    t = max(16, min(tile, h, w))
    keep, tried = [], 0
    rng = np.random.default_rng(0)
    for r in range(0, h - t + 1, t):
        for c in range(0, w - t + 1, t):
            m = ok[r:r + t, c:c + t]
            if m.mean() < 0.5:
                continue
            v = img[r:r + t, c:c + t][m]
            tried += 1
            fit = gmm2(v if v.size <= 2000 else rng.choice(v, 2000, replace=False))
            if fit is None:
                continue
            wt, mu, var = fit
            d = math.sqrt(2) * abs(mu[1] - mu[0]) / math.sqrt(var[0] + var[1])
            obj = mu[1] if bright else mu[0]
            if wt.min() >= min_frac and d >= min_d and (side is None or (obj > side if bright else obj < side)):
                keep.append(v)
    return (np.concatenate(keep) if keep else np.array([])), len(keep), tried


def _local_stats(img, ok, window):
    xv = np.where(ok, img, 0.0)
    n = uniform_filter(ok.astype("float64"), window)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = uniform_filter(xv, window) / n
        s = np.sqrt(np.maximum(uniform_filter(xv * xv, window) / n - m * m, 0))
    return m, s


def compute(image: np.ndarray, method: str = "otsu", *, valid: np.ndarray | None = None, bright: bool = True, window: int = 51,
            k: float | None = None, guard: float | None = 0.1, split: bool = True, default: float | None = None, tile: int = 64,
            sample: int = 400_000, seed: int = 0) -> dict:
    """The threshold of an image (object above it when bright, below when not); local methods give a per-pixel array,
    fuzzy ones also a 0–1 membership of the object class."""
    img = np.asarray(image, "float64")
    ok = np.isfinite(img) & (valid if valid is not None else True)
    v = img[ok]
    if v.size < 100:
        raise ValueError("Too few valid pixels to find a threshold")
    if v.size > sample:   # the histogram doesn't need every pixel
        v = np.random.default_rng(seed).choice(v, sample, replace=False)
    lo, hi = np.percentile(v, [0.1, 99.9])
    v = v[(v >= lo) & (v <= hi)]
    note = ""
    if split and method not in LOCAL:   # a histogram with one class only would be split in two all the same
        sel, kept, tried = bimodal_pixels(img, ok, tile, bright=bright, side=default)
        if sel.size < 200:
            if default is None:
                raise ValueError("No part of the image holds both classes clearly (no bimodal tiles): give a default threshold")
            return {"threshold": float(default), "membership": None, "method": method, "title": TITLES[method], "kind": "default",
                    "note": f"no tile of the image holds both classes clearly (0 of {tried}): the default {default:g}"}
        v = sel[(sel >= lo) & (sel <= hi)]
        note = f"from the {kept} of {tried} tiles holding both classes clearly (two-Gaussian fit, Ashman's D ≥ 2)"
    member = None
    if method == "otsu":
        t = otsu(v)
    elif method == "multi_otsu":
        t, pair = multi_otsu(v, upper=bright)   # the split next to the object class
    elif method == "li":
        t = li(v)
    elif method == "yen":
        t = yen(v)
    elif method == "kapur":
        t = kapur(v)
    elif method == "triangle":
        t = triangle(v)
    elif method == "isodata":
        t = isodata(v)
    elif method == "fcm":
        cl, ch = fcm(v)
        t = (cl + ch) / 2   # (in one dimension with m = 2 both memberships are ½ halfway between the centres)
        member = np.where(ok, fcm_membership(img, (cl, ch), bright=bright), np.nan)
    elif method == "fuzzy":
        t = huang(v)
    elif method == "membership":
        t0 = otsu(v)
        a, c = v[v <= t0].mean(), v[v > t0].mean()   # the two class means
        t = (a + c) / 2
        mem = s_membership(img, a, c)
        member = np.where(ok, mem if bright else 1 - mem, np.nan)
    elif method in LOCAL:
        # the published formulas are for dark objects on a 0–1 image: bright objects are inverted first, and the
        # per-pixel threshold mapped back to the image's own values
        span = (hi - lo) or 1.0
        g = np.where(ok, img, np.nanmedian(v))
        z = np.clip((hi - g) / span if bright else (g - lo) / span, 0, 1)
        mz, sz = _local_stats(z, ok, window)
        if method == "niblack":
            tz = mz - (0.2 if k is None else k) * sz
        elif method == "sauvola":
            tz = mz * (1 + (0.2 if k is None else k) * (sz / 0.5 - 1))
        elif method == "wolf":
            kk = 0.5 if k is None else k
            Mz, Rs = float(np.nanmin(z[ok])), max(float(np.nanmax(sz[ok])), 1e-12)
            tz = (1 - kk) * mz + kk * Mz + kk * (sz / Rs) * (mz - Mz)
        else:   # phansalkar
            tz = mz * (1 + 2.0 * np.exp(-10.0 * mz) + (0.25 if k is None else k) * (sz / 0.5 - 1))
        if guard:   # a flat neighbourhood (one class only) has no edge to find: the image's global threshold there
            sel = bimodal_pixels(img, ok, tile, bright=bright, side=default)[0] if split else v
            gt = otsu(sel) if sel.size >= 200 else (default if default is not None else otsu(v))
            tg = (hi - gt) / span if bright else (gt - lo) / span
            tz = np.where(sz < guard, tg, tz)
        t = np.where(ok, hi - tz * span if bright else lo + tz * span, np.nan)
    else:
        raise ValueError(f"Threshold method: {', '.join(GLOBAL + LOCAL + FUZZY)}")
    if member is None and not isinstance(t, np.ndarray):   # a soft membership around a global threshold
        scale = max((hi - lo) / 40, 1e-9)
        with np.errstate(over="ignore", invalid="ignore"):
            member = 1 / (1 + np.exp(-(img - t) / scale)) if bright else 1 / (1 + np.exp((img - t) / scale))
        member = np.where(ok, member, np.nan)
    return {"threshold": t if isinstance(t, np.ndarray) else float(t), "membership": member, "method": method, "title": TITLES[method],
            "kind": "global" if method in GLOBAL else "local" if method in LOCAL else "fuzzy", "note": note}
