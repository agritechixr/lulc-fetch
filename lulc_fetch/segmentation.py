"""Objects instead of pixels: SLIC superpixels and connected components.

    slic(bands, n_segments, compactness)   SLIC (Achanta et al. 2012): k-means in (row, column, bands) space, each
                                           centre searching only its 2S × 2S neighbourhood (S = grid step), so it runs
                                           in linear time; then each superpixel made one connected piece (fragments
                                           join the neighbour they touch most). Bands are standardised first.
    components(mask, connectivity)         connected regions of a mask (4- or 8-connected) with their size
    region_means(labels, bands)            every band's mean per segment: the object-based image a classifier can
                                           use instead of noisy single pixels (speckle in SAR averages out)
    polygons(labels, transform, …)         the segments as polygons with their mean values (GeoJSON)
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage as ndi


def _standardise(bands: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    X = np.stack([np.asarray(b, "float64") for b in bands], -1)
    ok = np.all(np.isfinite(X), -1)
    for i in range(X.shape[-1]):
        v = X[..., i][ok]
        sd = v.std() or 1.0
        X[..., i] = np.where(ok, (X[..., i] - v.mean()) / sd, 0.0)
    return X, ok


def slic(bands: list[np.ndarray], n_segments: int = 500, compactness: float = 0.3, iters: int = 10, min_size_factor: float = 0.25,
         sigma: float = 0.0) -> np.ndarray:
    """Superpixel labels 1..K (0 where there is no data). compactness: higher = squarer segments, lower = following
    the image more closely, in standardised band units (0.3 suits most rasters; skimage's 10 is for Lab colour, whose
    values span about 100). sigma: Gaussian smoothing (pixels) before clustering: 1–2 for speckled SAR."""
    if sigma > 0:
        sm = []
        for b in bands:
            b = np.asarray(b, "float64")
            ok_ = np.isfinite(b)
            num = ndi.gaussian_filter(np.where(ok_, b, 0.0), sigma)
            den = ndi.gaussian_filter(ok_.astype("float64"), sigma)
            with np.errstate(invalid="ignore", divide="ignore"):
                sm.append(np.where(ok_, num / den, np.nan))
        bands = sm
    X, ok = _standardise(bands)
    h, w, nb = X.shape
    S = max(2, int(math.sqrt(h * w / max(1, n_segments))))
    ys = np.arange(S // 2, h, S)
    xs = np.arange(S // 2, w, S)
    cy, cx = [a.ravel().astype("float64") for a in np.meshgrid(ys, xs, indexing="ij")]
    # move each centre to the lowest gradient in its 3 × 3 (not on an edge)
    gm = sum(ndi.sobel(X[..., i], 0) ** 2 + ndi.sobel(X[..., i], 1) ** 2 for i in range(nb))
    for k in range(cy.size):
        y0, x0 = int(cy[k]), int(cx[k])
        win = gm[max(0, y0 - 1):y0 + 2, max(0, x0 - 1):x0 + 2]
        dy, dx = np.unravel_index(np.argmin(win), win.shape)
        cy[k], cx[k] = max(0, y0 - 1) + dy, max(0, x0 - 1) + dx
    cf = X[cy.astype(int), cx.astype(int)]
    m2 = (compactness / S) ** 2
    lab = np.full((h, w), -1, "int64")
    for _ in range(iters):
        dist = np.full((h, w), np.inf)
        for k in range(cy.size):
            y0, y1 = int(max(0, cy[k] - S)), int(min(h, cy[k] + S + 1))
            x0, x1 = int(max(0, cx[k] - S)), int(min(w, cx[k] + S + 1))
            yy, xx = np.mgrid[y0:y1, x0:x1]
            dc = ((X[y0:y1, x0:x1] - cf[k]) ** 2).sum(-1)
            d = dc + m2 * ((yy - cy[k]) ** 2 + (xx - cx[k]) ** 2)
            better = d < dist[y0:y1, x0:x1]
            dist[y0:y1, x0:x1][better] = d[better]
            lab[y0:y1, x0:x1][better] = k
        # new centres: mean position and features of each cluster
        flat = lab.ravel()
        good = flat >= 0
        cnt = np.bincount(flat[good], minlength=cy.size).astype("float64")
        has = cnt > 0
        yy, xx = np.indices((h, w))
        cy = np.where(has, np.bincount(flat[good], yy.ravel()[good], cy.size) / np.maximum(cnt, 1), cy)
        cx = np.where(has, np.bincount(flat[good], xx.ravel()[good], cy.size) / np.maximum(cnt, 1), cx)
        cf = np.stack([np.where(has, np.bincount(flat[good], X[..., i].ravel()[good], cy.size) / np.maximum(cnt, 1), cf[:, i]) for i in range(nb)], 1)
    return _connect(lab, ok, int(S * S * min_size_factor))


def _connect(lab: np.ndarray, ok: np.ndarray, min_size: int) -> np.ndarray:
    """Each label one connected piece; pieces smaller than min_size join the neighbour they share the longest border
    with. Works inside each label's bounding box, so it stays fast with thousands of segments."""
    h, w = lab.shape
    out = np.zeros(lab.shape, "int64")
    nxt = 1
    shifted = np.where(lab >= 0, lab + 1, 0)
    for v, sl in enumerate(ndi.find_objects(shifted), start=1):
        if sl is None:
            continue
        sub = shifted[sl] == v
        cc, n = ndi.label(sub)
        o = out[sl]
        o[sub] = cc[sub] + nxt - 1
        nxt += n
    sizes = np.bincount(out.ravel())
    objs = ndi.find_objects(out)
    for s_ in np.flatnonzero(sizes < min_size):
        if s_ == 0 or objs[s_ - 1] is None:
            continue
        sl = objs[s_ - 1]
        y0, y1 = max(0, sl[0].start - 1), min(h, sl[0].stop + 1)
        x0, x1 = max(0, sl[1].start - 1), min(w, sl[1].stop + 1)
        sub = out[y0:y1, x0:x1]
        m = sub == s_
        if not m.any():
            continue
        ring = ndi.binary_dilation(m) & ~m
        nb = sub[ring]
        nb = nb[(nb > 0) & (nb != s_)]
        if nb.size:
            vals, c = np.unique(nb, return_counts=True)
            sub[m] = vals[np.argmax(c)]
    out = np.where(ok, out, 0)
    u, inv = np.unique(out, return_inverse=True)
    new = inv.reshape(out.shape)
    if u[0] != 0:
        new = new + 1
    return new.astype("int32")


def components(mask: np.ndarray, connectivity: int = 8, min_px: int = 1) -> tuple[np.ndarray, list[dict]]:
    """Connected regions of a 0 / 1 mask: labels (0 = background) and each region's pixels and centroid (row, col)."""
    st = np.ones((3, 3)) if connectivity == 8 else ndi.generate_binary_structure(2, 1)
    lab, n = ndi.label(np.asarray(mask) > 0, structure=st)
    if n == 0:
        return lab.astype("int32"), []
    sizes = np.bincount(lab.ravel())
    if min_px > 1:
        drop = sizes < min_px
        drop[0] = False
        lab[drop[lab]] = 0
        lab, n = ndi.label(lab > 0, structure=st)
        sizes = np.bincount(lab.ravel())
    cents = ndi.center_of_mass(lab > 0, lab, range(1, n + 1))
    bbox = ndi.find_objects(lab)
    regions = [{"id": i + 1, "pixels": int(sizes[i + 1]), "row": round(float(c[0]), 2), "col": round(float(c[1]), 2),
                "height_px": b[0].stop - b[0].start, "width_px": b[1].stop - b[1].start} for i, (c, b) in enumerate(zip(cents, bbox))]
    return lab.astype("int32"), regions


def region_means(labels: np.ndarray, bands: list[np.ndarray]) -> tuple[list[np.ndarray], np.ndarray]:
    """Each band's per-segment mean, painted back on the segments; and the table of means [segments × bands]."""
    flat = labels.ravel()
    k = int(flat.max()) + 1
    cnt = np.bincount(flat, minlength=k).astype("float64")
    table = np.zeros((k, len(bands)))
    painted = []
    for i, b in enumerate(bands):
        v = np.asarray(b, "float64").ravel()
        ok = np.isfinite(v)
        s = np.bincount(flat[ok], v[ok], k)
        c = np.bincount(flat[ok], minlength=k).astype("float64")
        with np.errstate(invalid="ignore", divide="ignore"):
            m = s / c
        table[:, i] = m
        painted.append(np.where(labels > 0, m[labels], np.nan).astype("float32"))
    return painted, table


def polygons(labels: np.ndarray, transform, crs, table: np.ndarray | None = None, names: list[str] | None = None, pixel_area: float | None = None) -> dict:
    """The segments as a GeoJSON FeatureCollection (EPSG:4326) with id, pixels, area and the bands' means."""
    from rasterio.features import shapes
    from rasterio.warp import transform_geom
    cnt = np.bincount(labels.ravel())
    feats = []
    for geom, v in shapes(labels.astype("int32"), mask=labels > 0, transform=transform, connectivity=4):
        v = int(v)
        props = {"id": v, "pixels": int(cnt[v])}
        if pixel_area:
            props["area_ha"] = round(cnt[v] * pixel_area / 1e4, 4)
        if table is not None:
            for j, n in enumerate(names or []):
                x = table[v, j]
                props[n[:60]] = None if not np.isfinite(x) else round(float(x), 5)
        g = transform_geom(crs, "EPSG:4326", geom) if crs else geom
        feats.append({"type": "Feature", "geometry": g, "properties": props})
    return {"type": "FeatureCollection", "features": feats}
