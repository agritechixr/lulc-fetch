"""LiDAR point clouds (Analysis ▸ Tools ▸ Raster & terrain ▸ LiDAR): read a .las file (LAS 1.0–1.4, point formats 0–10,
read directly with numpy; .laz needs laspy with lazrs) and grid it into a ground model (DTM), a surface model (DSM),
canopy / object height (CHM = DSM − DTM), point density and mean intensity; tree tops as points.

Ground: the points classified as ground (class 2), or, in unclassified clouds, the lowest point of each cell kept where it
is not much above a morphological opening of the lowest-points surface (a simple progressive filter, Zhang et al. 2003).
Cells without a point take their nearest neighbour's value."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_transform
from scipy import ndimage as ndi

from . import progress

CLASSES = {0: "Created, never classified", 1: "Unclassified", 2: "Ground", 3: "Low vegetation", 4: "Medium vegetation", 5: "High vegetation",
           6: "Building", 7: "Low point (noise)", 8: "Model key point", 9: "Water", 10: "Rail", 11: "Road surface", 12: "Overlap",
           13: "Wire guard", 14: "Wire conductor", 15: "Transmission tower", 16: "Wire connector", 17: "Bridge deck", 18: "High noise"}
PRODUCTS = ("dtm", "dsm", "chm", "density", "intensity", "trees")
MAX_CELLS = 100_000_000
CHUNK = 5_000_000


class Cloud:
    """Points of a LAS / LAZ file: header facts and chunks of x, y, z, intensity, class, return number, number of returns."""

    def __init__(self, path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            hd = f.read(375)
        if hd[:4] != b"LASF":
            raise ValueError("Not a LAS file (it does not start with LASF)")
        self.version = (hd[24], hd[25])
        self.fmt_raw = hd[104]
        self.fmt = self.fmt_raw & 0x3F
        self.compressed = bool(self.fmt_raw & 0xC0) or self.path.suffix.lower() == ".laz"
        self.rec_len = struct.unpack_from("<H", hd, 105)[0]
        self.offset = struct.unpack_from("<I", hd, 96)[0]
        self.header_size = struct.unpack_from("<H", hd, 94)[0]
        self.n_vlr = struct.unpack_from("<I", hd, 100)[0]
        n = struct.unpack_from("<I", hd, 107)[0]
        if self.version >= (1, 4) and len(hd) >= 255:
            n = struct.unpack_from("<Q", hd, 247)[0] or n
        self.count = int(n)
        self.scale = struct.unpack_from("<3d", hd, 131)
        self.off = struct.unpack_from("<3d", hd, 155)
        mx, mnx, my, mny, mz, mnz = struct.unpack_from("<6d", hd, 179)
        self.bounds = (mnx, mny, mx, my)
        self.zrange = (mnz, mz)
        self.crs = self._crs()
        if self.fmt > 10:
            raise ValueError(f"Point format {self.fmt} is not a LAS point format")

    def _crs(self) -> CRS | None:
        """The coordinate system from the VLRs: OGC WKT (record 2112) or GeoTIFF keys (34735: the EPSG code)."""
        with open(self.path, "rb") as f:
            f.seek(self.header_size)
            for _ in range(self.n_vlr):
                h = f.read(54)
                if len(h) < 54:
                    break
                user = h[2:18].rstrip(b"\0").decode("ascii", "ignore")
                rid, ln = struct.unpack_from("<HH", h, 18)
                body = f.read(ln)
                if user == "LASF_Projection" and rid == 2112:
                    try:
                        return CRS.from_wkt(body.rstrip(b"\0").decode("utf-8", "ignore"))
                    except Exception:
                        pass
                if user == "LASF_Projection" and rid == 34735 and len(body) >= 8:
                    keys = struct.unpack_from(f"<{len(body) // 2}H", body)
                    nk = keys[3]
                    for i in range(nk):
                        kid, loc, _, val = keys[4 + 4 * i: 8 + 4 * i]
                        if kid in (3072, 2048) and loc == 0 and 1024 <= val < 32767:
                            try:
                                return CRS.from_epsg(val)
                            except Exception:
                                pass
        return None

    def chunks(self):
        """Dicts of numpy arrays, a few million points at a time."""
        if self.compressed:
            try:
                import laspy
            except ImportError as e:
                raise ValueError("This is a compressed .laz file: install laspy with LAZ support (pip install \"laspy[lazrs]\") "
                                 "or convert it to .las (e.g. with LAStools' las2las or PDAL)") from e
            with laspy.open(self.path) as r:
                for pts in r.chunk_iterator(CHUNK):
                    yield {"x": np.asarray(pts.x), "y": np.asarray(pts.y), "z": np.asarray(pts.z), "i": np.asarray(pts.intensity),
                           "c": np.asarray(pts.classification), "r": np.asarray(pts.return_number), "n": np.asarray(pts.number_of_returns)}
            return
        mm = np.memmap(self.path, dtype=np.uint8, mode="r", offset=self.offset, shape=(self.count * self.rec_len,))
        recs = mm.reshape(self.count, self.rec_len)
        for s in range(0, self.count, CHUNK):
            b = np.ascontiguousarray(recs[s:s + CHUNK])
            xyz = b[:, :12].copy().view("<i4").reshape(-1, 3)
            out = {"x": xyz[:, 0] * self.scale[0] + self.off[0], "y": xyz[:, 1] * self.scale[1] + self.off[1], "z": xyz[:, 2] * self.scale[2] + self.off[2],
                   "i": b[:, 12:14].copy().view("<u2").ravel()}
            if self.fmt <= 5:
                out["r"], out["n"], out["c"] = b[:, 14] & 7, (b[:, 14] >> 3) & 7, b[:, 15] & 31
            else:
                out["r"], out["n"], out["c"] = b[:, 14] & 15, b[:, 14] >> 4, b[:, 16]
            yield out
        del mm


def info(path) -> dict:
    cl = Cloud(path)
    counts = np.zeros(256, "int64")
    first = 0
    for ch in cl.chunks():
        counts += np.bincount(ch["c"].astype("int64"), minlength=256)[:256]
        first += int((ch["r"] <= 1).sum())
    area = max((cl.bounds[2] - cl.bounds[0]) * (cl.bounds[3] - cl.bounds[1]), 1e-9)
    return {"points": cl.count, "version": f"{cl.version[0]}.{cl.version[1]}", "point_format": cl.fmt, "compressed": cl.compressed,
            "crs": cl.crs.to_string() if cl.crs else None, "bounds": cl.bounds, "z_range": cl.zrange,
            "density_per_m2": round(cl.count / area, 3) if cl.crs is None or not cl.crs.is_geographic else None,
            "classes": {CLASSES.get(k, f"Class {k}"): int(v) for k, v in enumerate(counts) if v}, "first_returns": first}


def grid(path, out_dir: Path, *, res: float = 1.0, products=("dtm", "dsm", "chm"), crs: str | None = None,
         ground_window_m: float = 40.0, ground_slope: float = 0.3, tree_min_h: float = 2.0, tree_window_m: float = 5.0,
         drop_noise: bool = True, name: str | None = None) -> dict:
    bad = set(products) - set(PRODUCTS)
    if bad or not products:
        raise ValueError(f"products: {', '.join(PRODUCTS)}")
    cl = Cloud(path)
    the_crs = CRS.from_user_input(crs) if crs else cl.crs
    if the_crs is None:
        raise ValueError("The file does not say its coordinate system → choose it (e.g. EPSG:32643)")
    if the_crs.is_geographic:
        raise ValueError("The points are in degrees → LiDAR grids need a projected coordinate system in metres")
    x0, y0, x1, y1 = cl.bounds
    x0, y1 = np.floor(x0 / res) * res, np.ceil(y1 / res) * res
    w, h = int(np.ceil((x1 - x0) / res)) + 1, int(np.ceil((y1 - y0) / res)) + 1
    if w * h > MAX_CELLS:
        raise ValueError(f"That grid is {w:,} × {h:,} cells → use bigger cells")
    dsm = np.full(h * w, -np.inf)
    low = np.full(h * w, np.inf)
    gmin = np.full(h * w, np.inf)
    dens = np.zeros(h * w, "int64")
    isum = np.zeros(h * w)
    veg = np.zeros(h * w, bool)
    has_ground = has_veg = False
    done = 0
    for ch in cl.chunks():
        keep = np.ones(ch["x"].size, bool)
        if drop_noise:
            keep &= ~np.isin(ch["c"], (7, 18))
        col = ((ch["x"] - x0) / res).astype("int64")
        row = ((y1 - ch["y"]) / res).astype("int64")
        keep &= (col >= 0) & (col < w) & (row >= 0) & (row < h)
        idx, z = row[keep] * w + col[keep], ch["z"][keep]
        np.maximum.at(dsm, idx, z)
        np.minimum.at(low, idx, z)
        np.add.at(dens, idx, 1)
        np.add.at(isum, idx, ch["i"][keep].astype("float64"))
        v = np.isin(ch["c"][keep], (3, 4, 5))
        if v.any():
            has_veg = True
            veg[idx[v]] = True
        g = ch["c"][keep] == 2
        if g.any():
            has_ground = True
            np.minimum.at(gmin, idx[g], z[g])
        done += ch["x"].size
        progress.update(0.7 * done / max(cl.count, 1), f"Gridding points: {done:,} of {cl.count:,}")
    dsm, low, gmin = dsm.reshape(h, w), low.reshape(h, w), gmin.reshape(h, w)
    occupied = np.isfinite(low)
    if not occupied.any():
        raise ValueError("No points fall in the grid")

    def fill_nearest(a, ok):
        if ok.all():
            return a
        _, (ri, ci) = ndi.distance_transform_edt(~ok, return_indices=True)
        return a[ri, ci]

    progress.update(0.75, "Ground model")
    if has_ground:
        ground = np.isfinite(gmin)
        dtm = fill_nearest(np.where(ground, gmin, 0), ground)
        method = "classified ground points (class 2)"
    else:
        # progressive morphological filter (Zhang et al. 2003): openings with growing windows; a cell stays ground while it is
        # not higher above the opened surface than the slope allows over that window
        lowf = fill_nearest(np.where(occupied, low, 0), occupied)
        surface, ground, prev, k = lowf, occupied.copy(), 1, 0
        while True:
            size = 2 * (2 ** k) + 1
            if size * res > ground_window_m * 1.01 and k:
                break
            opened = ndi.grey_opening(surface, size=(size, size))
            dh = min(0.3 + ground_slope * (size - prev) * res, 3.0) if k else 0.3
            ground &= ~(surface - opened > dh)
            surface, prev, k = opened, size, k + 1
            if size * res >= ground_window_m:
                break
        dtm = fill_nearest(np.where(ground, lowf, 0), ground)
        method = f"lowest points with a progressive morphological filter up to {ground_window_m:g} m (no classified ground in the file)"
    dsmf = fill_nearest(np.where(occupied, dsm, 0), occupied)
    chm = np.clip(dsmf - dtm, 0, None)
    transform = from_origin(x0, y1, res, res)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = name or cl.path.stem
    outs = []

    def write(fname, a, desc, dtype="float32", nodata=-9999.0):
        p = out_dir / f"{stem}_{fname}.tif"
        with rasterio.open(p, "w", driver="GTiff", width=w, height=h, count=1, dtype=dtype, crs=the_crs, transform=transform,
                           nodata=nodata, compress="deflate", tiled=True, blockxsize=256, blockysize=256) as d:
            d.write(a.astype(dtype), 1)
            d.set_band_description(1, desc)
        outs.append(str(p))

    if "dtm" in products:
        write("dtm", dtm, "Ground height (DTM, m)")
    if "dsm" in products:
        write("dsm", dsmf, "Surface height (DSM, m)")
    if "chm" in products:
        write("chm", chm, "Height above ground (CHM, m)")
    if "density" in products:
        write("density", dens.reshape(h, w) / (res * res), "Points per m²")
    if "intensity" in products:
        with np.errstate(invalid="ignore", divide="ignore"):
            write("intensity", np.where(dens > 0, isum / np.maximum(dens, 1), np.nan).reshape(h, w), "Mean intensity")
    trees = None
    if "trees" in products:
        progress.update(0.9, "Tree tops")
        size = max(3, int(round(tree_window_m / res)) | 1)
        sm = ndi.gaussian_filter(chm, max(1.0, size / 4))   # one top per crown, not one per bump in it
        peak = (sm == ndi.maximum_filter(sm, size=size)) & (chm >= tree_min_h) & occupied
        if has_veg:   # classified: tops only on vegetation (not on roofs)
            peak &= ndi.binary_dilation(veg.reshape(h, w), iterations=1)
        rows, cols = np.nonzero(peak)
        xs, ys = x0 + (cols + 0.5) * res, y1 - (rows + 0.5) * res
        lon, lat = warp_transform(the_crs, "EPSG:4326", xs.tolist(), ys.tolist()) if len(xs) else ([], [])
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"height_m": round(float(chm[r, c]), 2)},
               "geometry": {"type": "Point", "coordinates": [round(a, 7), round(b, 7)]}} for r, c, a, b in zip(rows, cols, lon, lat)]}
        p = out_dir / f"{stem}_trees.geojson"
        p.write_text(json.dumps(fc), encoding="utf-8")
        outs.append(str(p))
        trees = len(fc["features"])
    progress.update(1.0, "Done")
    return {"outputs": outs, "points": cl.count, "size": [w, h], "res": res, "crs": the_crs.to_string(), "ground": method,
            "ground_pct": round(100 * float(ground.sum()) / max(1, int(occupied.sum())), 1), "max_height_m": round(float(chm.max()), 2),
            "trees": trees}


def write_las(path, x, y, z, *, cls=None, intensity=None, epsg: int | None = None, scale=0.01) -> None:
    """A small LAS 1.2 file (point format 1), e.g. for tests or to save a subset."""
    n = len(x)
    off = (float(np.min(x)), float(np.min(y)), float(np.min(z)))
    vlr = b""
    if epsg:
        keys = [1, 1, 0, 1, 3072, 0, 1, epsg]
        body = struct.pack(f"<{len(keys)}H", *keys)
        vlr = struct.pack("<H16sHH32s", 0, b"LASF_Projection", 34735, len(body), b"GeoKeyDirectory") + body
    hsize, rec = 227, 28
    hd = bytearray(hsize)
    hd[0:4] = b"LASF"
    hd[24], hd[25] = 1, 2
    struct.pack_into("<H", hd, 94, hsize)
    struct.pack_into("<I", hd, 96, hsize + len(vlr))
    struct.pack_into("<I", hd, 100, 1 if vlr else 0)
    hd[104] = 1
    struct.pack_into("<H", hd, 105, rec)
    struct.pack_into("<I", hd, 107, n)
    struct.pack_into("<3d", hd, 131, scale, scale, scale)
    struct.pack_into("<3d", hd, 155, *off)
    struct.pack_into("<6d", hd, 179, float(np.max(x)), float(np.min(x)), float(np.max(y)), float(np.min(y)), float(np.max(z)), float(np.min(z)))
    dt = np.dtype([("x", "<i4"), ("y", "<i4"), ("z", "<i4"), ("i", "<u2"), ("rn", "u1"), ("c", "u1"), ("a", "i1"), ("u", "u1"), ("s", "<u2"), ("t", "<f8")])
    p = np.zeros(n, dt)
    p["x"], p["y"], p["z"] = [np.round((np.asarray(v) - o) / scale).astype("int64") for v, o in zip((x, y, z), off)]
    p["i"] = 0 if intensity is None else intensity
    p["rn"] = 1 | (1 << 3)
    p["c"] = 1 if cls is None else cls
    with open(path, "wb") as f:
        f.write(bytes(hd) + vlr + p.tobytes())
