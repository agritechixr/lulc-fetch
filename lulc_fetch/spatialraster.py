"""Spatial structure of a raster: autocorrelation, edges and multi-scale features.

    autocorrelation(x, radius, …)   global Moran's I and Geary's C (with z-scores under normality), Local Moran's I
                                    (Anselin 1995: clusters high-high, low-low and outliers high-low, low-high, at a
                                    significance level) and Getis-Ord Gi* (hot / cold spots, z-scores). Neighbours:
                                    the pixels within `radius` (queen contiguity at 1), equal weights.
    edges(x, …)                     Sobel (x, y, magnitude, direction), Canny (smoothing, non-maximum suppression,
                                    double threshold with hysteresis), Laplacian of Gaussian, gradient magnitude and
                                    directional gradients at a scale
    multiscale(x, sigmas, …)        Gaussian scale space: smoothed, gradient magnitude, Laplacian of Gaussian,
                                    difference of Gaussians, local mean and standard deviation at each scale: one stack
                                    for classical ML or deep learning

Moran's I: I = (n / W) · Σᵢ Σⱼ wᵢⱼ (xᵢ − x̄)(xⱼ − x̄) / Σᵢ (xᵢ − x̄)², above its expectation −1/(n − 1) when similar
values cluster. Geary's C is below 1 then. Neighbour sums are convolutions, so whole rasters are fine.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage as ndi
from scipy.stats import norm


def _kernel(radius: int, self_: bool = False) -> np.ndarray:
    r = max(1, int(radius))
    k = np.ones((2 * r + 1, 2 * r + 1))
    if not self_:
        k[r, r] = 0
    return k


def _conv(a, k):
    """Sum over the kernel's footprint (zero outside the image); by FFT for big kernels (a ring of radius 128 is
    257 × 257: directly that would be 66 000 operations per pixel)."""
    if k.size <= 121:
        return ndi.convolve(a, k, mode="constant", cval=0.0)
    from scipy.signal import fftconvolve
    r = fftconvolve(a, k[::-1, ::-1], mode="same")
    r[np.abs(r) < 1e-9 * (np.abs(a).max() * k.sum() + 1e-300)] = 0.0   # FFT round-off on empty areas
    return r


def autocorrelation(x: np.ndarray, radius: int = 1, *, alpha: float = 0.05, local: bool = True) -> dict:
    """Global Moran's I / Geary's C and, with local, the LISA cluster map and Getis-Ord Gi* z-scores."""
    x = np.asarray(x, "float64")
    ok = np.isfinite(x)
    n = int(ok.sum())
    if n < 30:
        raise ValueError("Too few valid pixels")
    mean = x[ok].mean()
    z = np.where(ok, x - mean, 0.0)
    m2 = float((z[ok] ** 2).sum() / n)
    m4 = float((z[ok] ** 4).sum() / n)
    if m2 == 0:
        raise ValueError("The raster is constant: no spatial pattern to measure")
    k = _kernel(radius)
    okf = ok.astype("float64")
    kcount = _conv(okf, k) * okf                 # valid neighbours of each valid pixel
    lag = _conv(z, k)                            # Σⱼ wᵢⱼ zⱼ
    S0 = float(kcount.sum())                     # Σ wᵢⱼ (both directions counted)
    S1 = 2 * S0                                  # ½ Σ (wᵢⱼ + wⱼᵢ)² with symmetric 0/1 weights
    S2 = float(((2 * kcount) ** 2).sum())        # Σᵢ (wᵢ. + w.ᵢ)²
    I = n / S0 * float((z * lag)[ok].sum()) / float((z[ok] ** 2).sum())
    EI = -1 / (n - 1)
    VI = (n * n * S1 - n * S2 + 3 * S0 * S0) / ((n * n - 1) * S0 * S0) - EI * EI   # under normality
    zI = (I - EI) / math.sqrt(VI) if VI > 0 else float("nan")
    # Geary's C: (n − 1) Σ wᵢⱼ (xᵢ − xⱼ)² / (2 W Σ zᵢ²)
    sq = _conv(np.where(ok, x, 0) ** 2, k)
    sx = _conv(np.where(ok, x, 0), k)
    diff2 = np.where(ok, x * x * kcount - 2 * x * sx + sq, 0.0)
    C = (n - 1) * float(diff2[ok].sum()) / (2 * S0 * float((z[ok] ** 2).sum()))
    VC = ((2 * S1 + S2) * (n - 1) - 4 * S0 * S0) / (2 * (n + 1) * S0 * S0)
    zC = (1 - C) / math.sqrt(VC) if VC > 0 else float("nan")
    out = {"n": n, "radius": radius, "moran_i": round(I, 5), "moran_expected": round(EI, 7), "moran_z": round(zI, 2),
           "moran_p": float(2 * norm.sf(abs(zI))) if math.isfinite(zI) else None,
           "geary_c": round(C, 5), "geary_z": round(zC, 2), "geary_p": float(2 * norm.sf(abs(zC))) if math.isfinite(zC) else None}
    if local:
        # Local Moran (Anselin 1995): Iᵢ = zᵢ / m2 · Σⱼ wᵢⱼ zⱼ, z-score under randomisation
        Ii = np.where(ok, z / m2 * lag, np.nan)
        b2 = m4 / (m2 * m2)
        wi = kcount
        wi2 = kcount                                  # Σⱼ wᵢⱼ² = count for 0/1 weights
        wikh = wi * wi - wi2                          # Σ_{k≠h} wᵢₖ wᵢₕ
        EIi = -wi / (n - 1)
        VIi = wi2 * (n - b2) / (n - 1) + wikh * (2 * b2 - n) / ((n - 1) * (n - 2)) - EIi ** 2
        with np.errstate(invalid="ignore", divide="ignore"):
            zi = (Ii - EIi) / np.sqrt(VIi)
        p = 2 * norm.sf(np.abs(np.nan_to_num(zi)))
        sig = ok & (p < alpha) & (wi > 0)
        cl = np.zeros(x.shape, "uint8")
        cl[ok] = 1                                    # not significant
        hi_, lag_hi = z > 0, lag > 0
        cl[sig & hi_ & lag_hi] = 2                    # high-high (hot cluster)
        cl[sig & ~hi_ & ~lag_hi] = 3                  # low-low (cold cluster)
        cl[sig & hi_ & ~lag_hi] = 4                   # high-low (outlier)
        cl[sig & ~hi_ & lag_hi] = 5                   # low-high (outlier)
        # Getis-Ord Gi* (self included): (Σⱼ wᵢⱼ xⱼ − x̄ Σⱼ wᵢⱼ) / (S √((n Σ wᵢⱼ² − (Σ wᵢⱼ)²) / (n − 1)))
        ks = _kernel(radius, self_=True)
        cnt = _conv(okf, ks)
        sxs = _conv(np.where(ok, x, 0), ks)
        S = math.sqrt(float((x[ok] ** 2).sum()) / n - mean * mean)
        with np.errstate(invalid="ignore", divide="ignore"):
            gi = (sxs - mean * cnt) / (S * np.sqrt((n * cnt - cnt * cnt) / (n - 1)))
        gi = np.where(ok, gi, np.nan)
        out.update(local_i=Ii.astype("float32"), local_z=np.where(ok, zi, np.nan).astype("float32"), lisa=cl, gi_star=gi.astype("float32"),
                   lisa_counts={name: int((cl == v).sum()) for v, name in LISA.items() if v})
    return out


LISA = {0: "No data", 1: "Not significant", 2: "High-high (hot cluster)", 3: "Low-low (cold cluster)", 4: "High-low (outlier)", 5: "Low-high (outlier)"}
LISA_COLORS = {0: (0, 0, 0, 0), 1: (230, 230, 230, 120), 2: (215, 25, 28, 255), 3: (44, 123, 182, 255), 4: (253, 174, 97, 255), 5: (171, 217, 233, 255)}


def correlogram(x: np.ndarray, radii=(1, 2, 4, 8, 16, 32), sample_max: int = 4_000_000) -> list[dict]:
    """Moran's I at growing neighbourhood radii (ring-shaped lags: the pixels at that distance): how far similarity
    reaches. The distance where it falls near 0 is a good minimum block size for spatial cross-validation."""
    x = np.asarray(x, "float64")
    if x.size > sample_max:   # a coarser view: the correlogram's shape doesn't need every pixel
        f = int(math.ceil(math.sqrt(x.size / sample_max)))
        x = x[::f, ::f]
    else:
        f = 1
    ok = np.isfinite(x)
    z = np.where(ok, x - x[ok].mean(), 0.0)
    den = float((z[ok] ** 2).sum())
    out = []
    prev = 0
    for r in radii:
        r_ = max(1, int(round(r / f)))
        if r_ <= prev:
            continue
        yy, xx = np.mgrid[-r_:r_ + 1, -r_:r_ + 1]
        d = np.hypot(yy, xx)
        ring = ((d <= r_) & (d > prev)).astype("float64")
        prev = r_
        okf = ok.astype("float64")
        W = float((_conv(okf, ring) * okf).sum())
        if W == 0:
            continue
        I = int(ok.sum()) / W * float((z * _conv(z, ring))[ok].sum()) / den
        out.append({"radius_px": r_ * f, "moran_i": round(I, 4)})
    return out


def edges(x: np.ndarray, *, sigma: float = 1.0, which=("sobel", "canny", "laplacian", "magnitude", "directional"), low: float = 0.1,
          high: float = 0.2) -> list[tuple[str, np.ndarray]]:
    """Edge layers of one band: [(name, array)]. Canny's thresholds are fractions of the strongest gradient."""
    x = np.asarray(x, "float64")
    ok = np.isfinite(x)
    if not ok.any():
        raise ValueError("The band has no data")
    g = ndi.gaussian_filter(np.where(ok, x, np.nanmedian(x[ok])), sigma) if sigma > 0 else np.where(ok, x, np.nanmedian(x[ok]))
    gx, gy = ndi.sobel(g, 1) / 8, ndi.sobel(g, 0) / 8
    mag = np.hypot(gx, gy)
    out = []
    keep = lambda a: np.where(ok, a, np.nan).astype("float32")   # noqa: E731
    if "sobel" in which:
        out += [("Sobel x", keep(gx)), ("Sobel y", keep(gy)), ("Sobel magnitude", keep(mag)), ("Sobel direction (°)", keep(np.degrees(np.arctan2(gy, gx))))]
    if "magnitude" in which and "sobel" not in which:
        out.append((f"Gradient magnitude σ{sigma:g}", keep(mag)))
    if "directional" in which:   # gradients along 0°, 45°, 90°, 135°
        for a in (0, 45, 90, 135):
            t = math.radians(a)
            out.append((f"Gradient {a}°", keep(gx * math.cos(t) + gy * math.sin(t))))
    if "laplacian" in which:
        out.append((f"Laplacian of Gaussian σ{max(sigma, 0.5):g}", keep(ndi.gaussian_laplace(np.where(ok, x, np.nanmedian(x[ok])), max(sigma, 0.5)))))
    if "canny" in which:
        out.append((f"Canny σ{sigma:g}", np.where(ok, canny(mag, gx, gy, low, high), 0).astype("float32")))
    return out


def canny(mag, gx, gy, low=0.1, high=0.2) -> np.ndarray:
    """Non-maximum suppression along the gradient direction, then hysteresis: strong edges (≥ high × max) and the weak
    ones (≥ low × max) connected to them."""
    ang = (np.degrees(np.arctan2(gy, gx)) + 180) % 180
    q = np.zeros(mag.shape, "uint8")
    q[((ang >= 22.5) & (ang < 67.5))] = 1
    q[((ang >= 67.5) & (ang < 112.5))] = 2
    q[((ang >= 112.5) & (ang < 157.5))] = 3
    p = np.pad(mag, 1, mode="edge")
    c = p[1:-1, 1:-1]
    nb = {0: (p[1:-1, 2:], p[1:-1, :-2]), 1: (p[2:, 2:], p[:-2, :-2]), 2: (p[2:, 1:-1], p[:-2, 1:-1]), 3: (p[2:, :-2], p[:-2, 2:])}
    nms = np.zeros_like(mag)
    for k, (a, b) in nb.items():
        sel = (q == k) & (c >= a) & (c >= b)
        nms[sel] = c[sel]
    mx = nms.max() or 1
    strong, weak = nms >= high * mx, nms >= low * mx
    lab, _ = ndi.label(weak, structure=np.ones((3, 3)))
    keep = np.unique(lab[strong])
    keep = keep[keep > 0]
    return np.isin(lab, keep).astype("uint8")


def multiscale(x: np.ndarray, sigmas=(1, 2, 4, 8), features=("smooth", "gradient", "log", "dog", "mean", "std")) -> list[tuple[str, np.ndarray]]:
    """Gaussian scale-space features of one band at each σ (pixels)."""
    x = np.asarray(x, "float64")
    ok = np.isfinite(x)
    f = np.where(ok, x, np.nanmedian(x[ok]))
    out = []
    keep = lambda a: np.where(ok, a, np.nan).astype("float32")   # noqa: E731
    prev = None
    for s in sigmas:
        sm = ndi.gaussian_filter(f, s)
        if "smooth" in features:
            out.append((f"Gaussian σ{s:g}", keep(sm)))
        if "gradient" in features:
            out.append((f"Gradient magnitude σ{s:g}", keep(ndi.gaussian_gradient_magnitude(f, s))))
        if "log" in features:
            out.append((f"Laplacian of Gaussian σ{s:g}", keep(ndi.gaussian_laplace(f, s) * s * s)))   # scale-normalised
        if "dog" in features and prev is not None:
            out.append((f"Difference of Gaussians σ{prev[0]:g}–{s:g}", keep(prev[1] - sm)))
        if "mean" in features or "std" in features:
            w = int(2 * round(3 * s) + 1)
            xv = np.where(ok, x, 0.0)
            n = ndi.uniform_filter(ok.astype("float64"), w)
            with np.errstate(invalid="ignore", divide="ignore"):
                m = ndi.uniform_filter(xv, w) / n
                sd = np.sqrt(np.maximum(ndi.uniform_filter(xv * xv, w) / n - m * m, 0))
            if "mean" in features:
                out.append((f"Local mean {w}×{w}", keep(m)))
            if "std" in features:
                out.append((f"Local std {w}×{w}", keep(sd)))
        prev = (s, sm)
    return out
