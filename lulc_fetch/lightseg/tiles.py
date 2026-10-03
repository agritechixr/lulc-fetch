"""Cut images (any number of bands, e.g. 64 or 128 embedding dimensions) and their label maps into 256 × 256 patches for
the light segmentation models. All bands are kept; nothing is reduced (run PCA first only if you want to).

    from lulc_fetch.lightseg.tiles import tile_array, tile_geotiff
    patches = tile_array(image)                       # (bands, H, W) → list of (bands, 256, 256), with their (row, col)
    imgs, labels, origins = tile_geotiff("embedding.tif", "labels.tif", "out_folder")   # writes .npy patches
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

PATCH = 256


def _starts(n: int, size: int, overlap: int) -> list[int]:
    """Patch start positions along one axis: a regular step, plus a last patch flush with the edge (no lost pixels)."""
    if n <= size:
        return [0]
    step = max(1, size - overlap)
    s = list(range(0, n - size + 1, step))
    if s[-1] != n - size:
        s.append(n - size)
    return s


def tile_array(arr: np.ndarray, size: int = PATCH, overlap: int = 0, pad_value: float = 0.0) -> list[tuple[np.ndarray, tuple[int, int]]]:
    """(bands, H, W) or (H, W) → list of (patch, (row, col)); patches are size × size (padded with pad_value when the
    image is smaller than one patch)."""
    two_d = arr.ndim == 2
    a = arr[None] if two_d else arr
    _, h, w = a.shape
    if h < size or w < size:
        a = np.pad(a, ((0, 0), (0, max(0, size - h)), (0, max(0, size - w))), constant_values=pad_value)
    out = []
    for r in _starts(a.shape[1], size, overlap):
        for c in _starts(a.shape[2], size, overlap):
            p = a[:, r:r + size, c:c + size]
            out.append((p[0] if two_d else p, (r, c)))
    return out


def tile_geotiff(image: str | Path, labels: str | Path | None, out_dir: str | Path, size: int = PATCH, overlap: int = 0,
                 min_labelled: float = 0.0, label_nodata: int = 0) -> dict:
    """Cut an image GeoTIFF (all its bands) and, optionally, a label GeoTIFF on the same grid into size × size patches,
    saved as images/<n>.npy (float32, bands × size × size) and labels/<n>.npy (int64). Patches with less than
    min_labelled of their pixels labelled (≠ label_nodata) are skipped."""
    import rasterio

    out = Path(out_dir)
    (out / "images").mkdir(parents=True, exist_ok=True)
    with rasterio.open(image) as src:
        img = src.read().astype(np.float32)
        if src.nodata is not None and not np.isnan(src.nodata):
            img[img == src.nodata] = np.nan
    lab = None
    if labels:
        with rasterio.open(labels) as src:
            lab = src.read(1).astype(np.int64)
        if lab.shape != img.shape[1:]:
            raise ValueError(f"The label raster ({lab.shape}) isn't on the image's grid ({img.shape[1:]})")
        (out / "labels").mkdir(exist_ok=True)
    img = np.nan_to_num(img)
    kept, origins = 0, []
    lab_tiles = dict(((rc, t) for t, rc in tile_array(lab, size, overlap, label_nodata))) if lab is not None else {}
    for patch, rc in tile_array(img, size, overlap):
        if lab is not None:
            lp = lab_tiles[rc]
            if (lp != label_nodata).mean() < min_labelled:
                continue
            np.save(out / "labels" / f"{kept:05d}.npy", lp)
        np.save(out / "images" / f"{kept:05d}.npy", patch)
        origins.append(rc)
        kept += 1
    return {"patches": kept, "size": size, "bands": int(img.shape[0]), "origins": origins, "folder": str(out)}
