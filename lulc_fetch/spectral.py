"""Pansharpening and spectral unmixing (Analysis ▸ Tools ▸ Imagery).

Pansharpening: a multispectral image (e.g. Landsat 8/9 bands at 30 m) made as sharp as a panchromatic band (Landsat B8 at
15 m) of the same place. Methods: Gram-Schmidt adaptive (GSA, Aiazzi et al. 2007: the default; keeps colours best),
Brovey (ratio) and IHS (fast intensity substitution). The image is resampled to the pan grid and sharpened in strips.

Linear spectral unmixing: each pixel as a mix of a few pure materials (endmembers: vegetation, soil, water, built-up…),
x ≈ E a. The endmembers come from labelled polygons / points (the mean spectrum of each class) or are found in the image
(ATGP, Ren & Chang 2003). Fractions are fully constrained (≥ 0 and summing to 1: projected gradient on the simplex) or
unconstrained; an RMSE band shows how well the mix explains each pixel. numpy only."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling
from rasterio.windows import Window

from . import progress

MAX_PIXELS = 400_000_000
STRIP = 1024


# ------------------------------------------------------------------ pansharpening
PAN_METHODS = {"gsa": "Gram-Schmidt adaptive (GSA)", "brovey": "Brovey (ratio)", "ihs": "IHS (intensity substitution)"}


def pansharpen(ms_path, pan_path, out: Path, *, bands: list[int] | None = None, method: str = "gsa", pan_band: int = 1) -> dict:
    if method not in PAN_METHODS:
        raise ValueError(f"method: {', '.join(PAN_METHODS)}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(pan_path) as pan, rasterio.open(ms_path) as ms:
        if pan.width * pan.height > MAX_PIXELS:
            raise ValueError("The pan band is too big → clip both rasters to your area first")
        if not ms.crs or not pan.crs:
            raise ValueError("Both rasters need a coordinate system")
        bands = bands or list(range(1, ms.count + 1))
        if any(not 1 <= b <= ms.count for b in bands):
            raise ValueError(f"The image has {ms.count} bands")
        if pan.crs == ms.crs and abs(pan.transform.a) >= abs(ms.transform.a):
            raise ValueError("The pan band's pixels should be smaller than the image's (e.g. Landsat B8 at 15 m, the others at 30 m)")
        vrt = WarpedVRT(ms, crs=pan.crs, transform=pan.transform, width=pan.width, height=pan.height, resampling=Resampling.cubic)
        nb = len(bands)
        # weights and gains from a sample of the whole image (every k-th row and column)
        k = max(1, int(np.sqrt(pan.width * pan.height / 2_000_000)))
        sp = pan.read(pan_band, out_shape=(pan.height // k or 1, pan.width // k or 1), masked=True).astype("float64").filled(np.nan).ravel()
        sm = np.stack([vrt.read(b, out_shape=(pan.height // k or 1, pan.width // k or 1), masked=True).astype("float64").filled(np.nan).ravel() for b in bands])
        ok = np.isfinite(sp) & np.isfinite(sm).all(0)
        if ok.sum() < 100:
            raise ValueError("The image and the pan band hardly overlap")
        sp, sm = sp[ok], sm[:, ok]
        if method == "gsa":   # intensity = the mix of bands that best matches the (smoothed) pan
            A = np.vstack([sm, np.ones(sm.shape[1])]).T
            wts = np.linalg.lstsq(A, sp, rcond=None)[0]
            inten = A @ wts
        else:
            wts = np.r_[np.full(nb, 1 / nb), 0.0]
            inten = sm.mean(0)
        # match the pan's mean and spread to the intensity's
        pa, pb = inten.std() / max(sp.std(), 1e-12), 0.0
        pb = inten.mean() - pa * sp.mean()
        gains = np.array([np.cov(sm[i], inten)[0, 1] / max(inten.var(), 1e-12) for i in range(nb)]) if method == "gsa" else np.ones(nb)
        prof = ms.profile.copy()
        prof.update(driver="GTiff", crs=pan.crs, transform=pan.transform, width=pan.width, height=pan.height, count=nb, dtype="float32",
                    nodata=np.nan, compress="deflate", tiled=True, blockxsize=256, blockysize=256, photometric=None)
        prof.pop("photometric", None)
        with rasterio.open(out, "w", **prof) as dst:
            for r0 in range(0, pan.height, STRIP):
                win = Window(0, r0, pan.width, min(STRIP, pan.height - r0))
                p = pan.read(pan_band, window=win, masked=True).astype("float64").filled(np.nan) * pa + pb
                m = np.stack([vrt.read(b, window=win, masked=True).astype("float64").filled(np.nan) for b in bands])
                inten = np.tensordot(wts[:nb], m, 1) + wts[nb]
                if method == "brovey":
                    with np.errstate(divide="ignore", invalid="ignore"):
                        o = m * (p / np.where(np.abs(inten) > 1e-12, inten, np.nan))
                else:
                    o = m + gains[:, None, None] * (p - inten)[None]
                dst.write(o.astype("float32"))
                progress.update(min(1.0, (r0 + STRIP) / pan.height), f"Sharpening rows {r0:,} to {min(pan.height, r0 + STRIP):,} of {pan.height:,}")
            for i, b in enumerate(bands, 1):
                dst.set_band_description(i, (ms.descriptions[b - 1] or f"Band {b}") + " (sharpened)")
        vrt.close()
        res = (abs(ms.transform.a), abs(pan.transform.a))
    return {"path": str(out), "bands": nb, "method": PAN_METHODS[method], "from_pixel": res[0], "to_pixel": res[1],
            "size": [pan.width, pan.height]}


# ------------------------------------------------------------------ spectral unmixing
def atgp(X: np.ndarray, k: int) -> np.ndarray:
    """k endmember indices of X (pixels × bands): the brightest pixel, then each time the pixel least explained by those
    found so far (Automatic Target Generation Process)."""
    idx = [int(np.argmax((X * X).sum(1)))]
    for _ in range(1, k):
        U = X[idx].T                                   # bands × found
        P = np.eye(X.shape[1]) - U @ np.linalg.pinv(U)
        r = X @ P.T
        idx.append(int(np.argmax((r * r).sum(1))))
    return np.array(idx)


def _project_simplex(V: np.ndarray) -> np.ndarray:
    """Each row of V onto the probability simplex (≥ 0, summing to 1): Duchi et al. 2008, vectorised."""
    n, k = V.shape
    U = -np.sort(-V, axis=1)
    css = np.cumsum(U, axis=1) - 1
    ind = np.arange(1, k + 1)
    cond = U - css / ind > 0
    rho = k - 1 - np.argmax(cond[:, ::-1], axis=1)
    theta = css[np.arange(n), rho] / (rho + 1)
    return np.maximum(V - theta[:, None], 0)


def unmix_pixels(X: np.ndarray, E: np.ndarray, constrained: bool = True, iters: int = 300) -> np.ndarray:
    """Fractions (pixels × k) of X (pixels × bands) for endmembers E (k × bands)."""
    G = E @ E.T
    A0 = np.linalg.lstsq(E.T, X.T, rcond=None)[0].T             # unconstrained least squares
    if not constrained:
        return A0
    A = _project_simplex(A0)
    step = 1.0 / max(np.linalg.eigvalsh(G).max(), 1e-12)
    XE = X @ E.T
    for _ in range(iters):
        A_new = _project_simplex(A - step * (A @ G - XE))
        if np.abs(A_new - A).max() < 1e-6:
            A = A_new
            break
        A = A_new
    return A


def endmembers_from_layer(src, bands: list[int], geojson: dict, field: str) -> tuple[list[str], np.ndarray]:
    """The mean spectrum of each class of `field` inside the layer's polygons (or at its points)."""
    from rasterio import features
    from shapely.geometry import shape

    from .convert import _reproject_all
    groups: dict[str, list] = {}
    for f in geojson.get("features", []):
        v = (f.get("properties") or {}).get(field)
        if v is None or not f.get("geometry"):
            continue
        groups.setdefault(str(v), []).append(shape(f["geometry"]))
    if len(groups) < 2:
        raise ValueError(f"The field “{field}” needs at least two classes (one per material)")
    if len(groups) > 12:
        raise ValueError("Up to 12 materials (classes)")
    names, spectra = [], []
    for name, gs in sorted(groups.items()):
        gs = _reproject_all(gs, "EPSG:4326", src.crs)
        m = features.rasterize([(g.buffer(0) if g.geom_type.endswith("Polygon") else g, 1) for g in gs], out_shape=(src.height, src.width),
                               transform=src.transform, all_touched=True, dtype="uint8") > 0
        rows, cols = np.nonzero(m)
        if not rows.size:
            raise ValueError(f"The class “{name}” has no pixel inside the image")
        if rows.size > 200_000:
            sel = np.random.default_rng(0).choice(rows.size, 200_000, replace=False)
            rows, cols = rows[sel], cols[sel]
        vals = []
        for b in bands:
            a = src.read(b, masked=True).astype("float64").filled(np.nan)
            vals.append(a[rows, cols])
        V = np.array(vals).T
        V = V[np.isfinite(V).all(1)]
        if not len(V):
            raise ValueError(f"The class “{name}” has only pixels without data")
        names.append(name)
        spectra.append(V.mean(0))
    return names, np.array(spectra)


def unmix(path, out: Path, *, bands: list[int] | None = None, n_auto: int = 3, layer: dict | None = None, field: str | None = None,
          constrained: bool = True) -> dict:
    """Fraction rasters (one band per endmember, then RMSE) and a CSV of the endmember spectra."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path) as src:
        if src.width * src.height > 60_000_000:
            raise ValueError("The image is too big → clip it to your area first")
        bands = bands or list(range(1, src.count + 1))
        if len(bands) < 2:
            raise ValueError("Unmixing needs at least two bands")
        if layer:
            if not field:
                raise ValueError("Choose the field that names the material (class) of each shape")
            names, E = endmembers_from_layer(src, bands, layer, field)
        else:
            if not 2 <= n_auto <= min(10, len(bands) + 1):
                raise ValueError(f"Find between 2 and {min(10, len(bands) + 1)} endmembers")
        progress.update(0.1, "Reading the image")
        cube = np.stack([src.read(b, masked=True).astype("float64").filled(np.nan) for b in bands])
        nb, h, w = cube.shape
        X = cube.reshape(nb, -1).T
        ok = np.isfinite(X).all(1)
        if ok.sum() < 10:
            raise ValueError("The image has (almost) no pixels with data in every band")
        if not layer:
            sample = np.flatnonzero(ok)
            if sample.size > 300_000:
                sample = np.random.default_rng(0).choice(sample, 300_000, replace=False)
            idx = sample[atgp(X[sample], n_auto)]
            E = X[idx]
            names = [f"endmember_{i + 1}" for i in range(n_auto)]
        if np.linalg.matrix_rank(E) < len(E) and constrained is False:
            raise ValueError("Two endmembers have (nearly) the same spectrum → merge them or use other classes")
        k = len(E)
        frac = np.full((h * w, k), np.nan, "float32")
        rmse = np.full(h * w, np.nan, "float32")
        good = np.flatnonzero(ok)
        for s in range(0, good.size, 500_000):
            i = good[s:s + 500_000]
            A = unmix_pixels(X[i], E, constrained)
            frac[i] = A
            rmse[i] = np.sqrt(((A @ E - X[i]) ** 2).mean(1))
            progress.update(0.2 + 0.75 * min(1, (s + 500_000) / good.size), f"Unmixing pixels {s:,} of {good.size:,}")
        prof = src.profile.copy()
        prof.update(driver="GTiff", count=k + 1, dtype="float32", nodata=np.nan, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
        prof.pop("photometric", None)
        with rasterio.open(out, "w", **prof) as d:
            for j in range(k):
                d.write(frac[:, j].reshape(h, w), j + 1)
                d.set_band_description(j + 1, f"Fraction: {names[j]}")
            d.write(rmse.reshape(h, w), k + 1)
            d.set_band_description(k + 1, "RMSE (how badly the mix explains the pixel)")
            d.update_tags(endmembers=json.dumps(names))
        csv_path = out.with_suffix(".csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["endmember"] + [src.descriptions[b - 1] or f"band_{b}" for b in bands])
            for n_, e in zip(names, E):
                wr.writerow([n_] + [round(float(v), 6) for v in e])
        mean_frac = {n_: round(float(np.nanmean(frac[:, j])), 4) for j, n_ in enumerate(names)}
    return {"path": str(out), "csv": str(csv_path), "endmembers": names, "mean_fraction": mean_frac,
            "mean_rmse": round(float(np.nanmean(rmse)), 6), "constrained": constrained}
