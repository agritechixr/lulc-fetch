"""Open Copernicus .SAFE products (folders or .SAFE.zip).

- Sentinel-2 L1C / L2A: all bands stacked at 10 m as a lightweight VRT that points at the original JP2
  files (nothing is copied), with band names and the reflectance scale/offset recorded.
- Sentinel-1 GRD: calibrated backscatter (sigma0, dB) for VV / VH, multilooked and geocoded from the
  product's ground control points onto a UTM grid, written as a GeoTIFF.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject, transform_bounds

from . import progress
from .aoi import AOI, make_grid, utm_crs

log = logging.getLogger(__name__)

S2_ORDER = ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12")


# ------------------------------------------------------------------ discovery

def _members(path: Path) -> list[str]:
    """GDAL-readable paths of every file in a .SAFE folder or .SAFE.zip."""
    if path.is_dir():
        return [str(p) for p in path.rglob("*") if p.is_file()]
    with zipfile.ZipFile(path) as z:
        return [f"/vsizip/{path}/{m}" for m in z.namelist() if not m.endswith("/")]


def _read_text(member: str) -> str:
    if member.startswith("/vsizip/"):
        zpath, inner = re.match(r"/vsizip/(.+?\.zip)/(.+)", member).groups()
        with zipfile.ZipFile(zpath) as z:
            return z.read(inner).decode("utf-8", "ignore")
    return Path(member).read_text(encoding="utf-8", errors="ignore")


S2_NAME = re.compile(r"(S2[ABCD])_MSI(L1C|L2A)_(\d{8})T\d{6}_N(\d{4})_R(\d{3})_T(\w{5})_")
S1_NAME = re.compile(r"(S1[ABCD])_(IW|EW|SM)_GRD(\w)_1S(DV|DH|SV|SH)_(\d{8})T\d{6}_")


def product_name(filename: str) -> str | None:
    """'<name>' for a Sentinel-1 GRD / Sentinel-2 L1C/L2A product file or folder name (with or without .SAFE / .zip)."""
    name = Path(filename).name
    for suffix in (".zip", ".SAFE"):
        if name.upper().endswith(suffix.upper()):
            name = name[: -len(suffix)]
    return name if S2_NAME.match(name) or S1_NAME.match(name) else None


def describe(path: str | Path) -> dict | None:
    path = Path(path)
    name = path.name.removesuffix(".zip").removesuffix(".SAFE")
    m2 = S2_NAME.match(name)
    m1 = S1_NAME.match(name)
    size = path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    base = {"name": name, "path": str(path), "zipped": path.is_file(), "size_mb": size / 1e6}
    if m2:
        sat, level, d, baseline, orbit, tile = m2.groups()
        return {**base, "kind": f"S2_{level}", "title": f"Sentinel-2 {level} · tile {tile}", "satellite": sat,
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "detail": f"Baseline {baseline[:2]}.{baseline[2:]} · orbit R{orbit}"}
    if m1:
        sat, mode, res, pol, d = m1.groups()
        pols = {"DV": ["VV", "VH"], "DH": ["HH", "HV"], "SV": ["VV"], "SH": ["HH"]}[pol]
        return {**base, "kind": "S1_GRD", "title": f"Sentinel-1 GRD · {mode} · {'/'.join(pols)}", "satellite": sat,
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "detail": f"{mode} mode, {pol} polarisation", "pols": pols}
    return None


def find_products(*roots: str | Path) -> list[dict]:
    """Find .SAFE folders and .SAFE.zip files directly inside the given folders (zip skipped if extracted)."""
    out, seen = [], set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if not (p.name.endswith(".SAFE") and p.is_dir()) and not p.name.endswith(".SAFE.zip"):
                continue
            key = p.name.removesuffix(".zip")
            if key in seen or (p.suffix == ".zip" and (p.parent / key).is_dir()):
                continue
            info = describe(p)
            if info:
                seen.add(key)
                out.append(info)
    return out


# ------------------------------------------------------------------ Sentinel-2 → VRT

def _s2_band_files(members: list[str]) -> dict[str, str]:
    """Pick the finest-resolution JP2 for each band (L2A: R10m > R20m > R60m; L1C: native)."""
    jp2 = [m for m in members if m.lower().endswith(".jp2") and "/IMG_DATA/" in m.replace("\\", "/")]
    files = {}
    for band in S2_ORDER:
        for res in ("10m", "20m", "60m"):
            hit = next((m for m in jp2 if m.endswith(f"_{band}_{res}.jp2")), None)
            if hit:
                files[band] = hit
                break
        else:
            hit = next((m for m in jp2 if m.endswith(f"_{band}.jp2")), None)  # L1C naming
            if hit:
                files[band] = hit
    return files


def s2_to_vrt(product: str | Path, out_path: str | Path) -> Path:
    """Stack all Sentinel-2 bands at 10 m into one VRT (20 m / 60 m bands resampled bilinearly)."""
    product = Path(product)
    members = _members(product)
    files = _s2_band_files(members)
    if "B04" not in files:
        raise ValueError("No Sentinel-2 band files (IMG_DATA/*.jp2) found in this product")
    mtd = next((m for m in members if re.search(r"MTD_MSIL(1C|2A)\.xml$", m)), None)
    quant, offset = 10000.0, 0.0
    if mtd:
        text = _read_text(mtd)
        q = re.search(r"(?:BOA_)?QUANTIFICATION_VALUE[^>]*>([\d.]+)", text)
        quant = float(q.group(1)) if q else quant
        off = re.search(r"(?:BOA|RADIO)_ADD_OFFSET[^>]*>(-?[\d.]+)", text)
        offset = float(off.group(1)) / quant if off else 0.0
    with rasterio.open(files["B04"]) as ref:
        width, height, crs, t = ref.width, ref.height, ref.crs, ref.transform
    bands_xml = []
    for i, band in enumerate([b for b in S2_ORDER if b in files], start=1):
        with rasterio.open(files[band]) as src:
            sw, sh = src.width, src.height
        resampling = "" if (sw, sh) == (width, height) else ' resampling="bilinear"'
        bands_xml.append(f'''  <VRTRasterBand dataType="UInt16" band="{i}">
    <Description>{band}</Description>
    <NoDataValue>0</NoDataValue>
    <SimpleSource{resampling}>
      <SourceFilename relativeToVRT="0">{escape(files[band])}</SourceFilename>
      <SourceBand>1</SourceBand>
      <SrcRect xOff="0" yOff="0" xSize="{sw}" ySize="{sh}"/>
      <DstRect xOff="0" yOff="0" xSize="{width}" ySize="{height}"/>
    </SimpleSource>
  </VRTRasterBand>''')
    vrt = f'''<VRTDataset rasterXSize="{width}" rasterYSize="{height}">
  <SRS>{escape(crs.to_wkt())}</SRS>
  <GeoTransform>{t.c}, {t.a}, {t.b}, {t.f}, {t.d}, {t.e}</GeoTransform>
  <Metadata>
    <MDI key="source_product">{escape(product.name)}</MDI>
    <MDI key="reflectance_scale">{1 / quant}</MDI>
    <MDI key="reflectance_offset">{offset}</MDI>
    <MDI key="units">DN (reflectance = DN x {1 / quant:g} + {offset:g})</MDI>
  </Metadata>
{chr(10).join(bands_xml)}
</VRTDataset>
'''
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(vrt)
    return out_path


# ------------------------------------------------------------------ Sentinel-1 GRD → sigma0 dB GeoTIFF

def _calibration_lut(xml_text: str):
    """(lines, pixels, sigma0 LUT[lines, pixels]) from a calibration annotation file."""
    root = ET.fromstring(xml_text)
    lines, pixels, values = [], None, []
    for vec in root.iter("calibrationVector"):
        lines.append(int(vec.find("line").text))
        px = np.array(vec.find("pixel").text.split(), dtype="float64")
        sig = np.array(vec.find("sigmaNought").text.split(), dtype="float64")
        if pixels is None:
            pixels = px
        values.append(np.interp(pixels, px, sig))  # vectors normally share pixel positions
    return np.array(lines, dtype="float64"), pixels, np.array(values)


def _interp_lut(lines, pixels, lut, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Bilinear interpolation of the calibration grid at full-resolution row/col positions."""
    along = np.array([np.interp(cols, pixels, v) for v in lut])          # [n_lines, n_cols]
    i = np.clip(np.searchsorted(lines, rows) - 1, 0, len(lines) - 2)
    w = np.clip((rows - lines[i]) / (lines[i + 1] - lines[i]), 0, 1)[:, None]
    return along[i] * (1 - w) + along[i + 1] * w


def s1_to_backscatter(product: str | Path, out_path: str | Path, *, res: float = 40, aoi: AOI | None = None,
                      rows_per_strip: int = 1024) -> Path:
    """Calibrated sigma0 (dB) for each polarisation of a GRD product, geocoded to UTM at `res` metres.

    Steps: multilook (average power over n×n pixels, n = res / 10 m), radiometric calibration with the
    sigmaNought LUT, conversion to dB, then geocoding from the product's GCPs (ellipsoid, no terrain
    correction). Writes bands VV, VH (or HH, HV) and their dB difference (e.g. VVVH).
    """
    product = Path(product)
    members = _members(product)
    pols = []
    for pol in ("vv", "vh", "hh", "hv"):
        meas = next((m for m in members if "/measurement/" in m and f"-{pol}-" in m.lower() and m.lower().endswith((".tiff", ".tif"))), None)
        cal = next((m for m in members if "/calibration/calibration-" in m and f"-{pol}-" in m.lower()), None)
        if meas and cal:
            pols.append((pol.upper(), meas, cal))
    if not pols:
        raise ValueError("No Sentinel-1 measurement/calibration files found in this product")
    n = max(1, int(round(res / 10)))

    looks = []
    for k, (pol, meas, cal) in enumerate(pols):
        lines, pixels, lut = _calibration_lut(_read_text(cal))
        with rasterio.open(meas) as src:
            gcps, gcp_crs = src.gcps
            h, w = src.height // n, src.width // n
            out = np.full((h, w), np.nan, "float32")
            cols = np.arange(w) * n + (n - 1) / 2
            log.info("Calibrating %s: %d×%d px → %d×%d (%d×%d looks)", pol, src.width, src.height, w, h, n, n)
            step = max(n, rows_per_strip // n * n)
            for r0 in range(0, h * n, step):
                progress.update(0.8 * (k + r0 / (h * n)) / len(pols), f"Calibrating {pol} ({r0 / (h * n):.0%})")
                nrows = min(step, h * n - r0)
                dn = src.read(1, window=((r0, r0 + nrows), (0, w * n))).astype("float32")
                power = (dn ** 2).reshape(nrows // n, n, w, n).mean(axis=(1, 3))
                valid = (dn.reshape(nrows // n, n, w, n) > 0).all(axis=(1, 3))
                rows = r0 + np.arange(nrows // n) * n + (n - 1) / 2
                a = _interp_lut(lines, pixels, lut, rows, cols)
                with np.errstate(divide="ignore", invalid="ignore"):
                    db = 10 * np.log10(np.maximum(power / a ** 2, 1e-6))
                db[~valid] = np.nan
                out[r0 // n: r0 // n + nrows // n] = db
        looks.append((pol, out))

    # geocode with the GCPs, rescaled to the multilooked pixel grid
    gcps_ml = [GroundControlPoint(row=g.row / n, col=g.col / n, x=g.x, y=g.y, z=g.z) for g in gcps]
    lon = np.mean([g.x for g in gcps]); lat = np.mean([g.y for g in gcps])
    if aoi is not None:
        grid = make_grid(aoi, res)
        dst_crs, dst_transform, dw, dh = grid.crs, grid.transform, grid.width, grid.height
    else:
        dst_crs = utm_crs(lon, lat)
        dst_transform, dw, dh = calculate_default_transform(gcp_crs, dst_crs, w, h, gcps=gcps_ml, resolution=res)
    bands, names = [], []
    for k, (pol, arr) in enumerate(looks):
        progress.update(0.8 + 0.18 * k / len(looks), f"Geocoding {pol}")
        dst = np.full((dh, dw), np.nan, "float32")
        log.info("Geocoding %s to %s at %g m (%d×%d px)", pol, dst_crs.to_string(), res, dw, dh)
        reproject(arr, dst, gcps=gcps_ml, src_crs=gcp_crs, dst_transform=dst_transform, dst_crs=dst_crs,
                  resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
        bands.append(dst)
        names.append(pol)
    if len(bands) == 2:
        bands.append(bands[0] - bands[1])
        names.append(names[0] + names[1])  # e.g. VVVH = VV - VH in dB
    if not np.isfinite(bands[0]).any():
        raise ValueError("The product does not overlap the area of interest")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile = {"driver": "GTiff", "width": dw, "height": dh, "count": len(bands), "dtype": "float32", "crs": dst_crs,
               "transform": dst_transform, "nodata": np.nan, "compress": "deflate", "predictor": 3, "BIGTIFF": "IF_SAFER"}
    if dw >= 256 and dh >= 256:
        profile.update(tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(out_path, "w", **profile) as dst:
        for i, (b, name) in enumerate(zip(bands, names), start=1):
            dst.write(b, i)
            dst.set_band_description(i, name)
        dst.update_tags(units="sigma0 dB", source_product=product.name,
                        processing=f"radiometric calibration (sigma0), {n}x{n} multilook, GCP geocoding "
                                   f"(ellipsoid, no terrain correction), {res:g} m")
    w4, s4, e4, n4 = transform_bounds(dst_crs, "EPSG:4326", *rasterio.transform.array_bounds(dh, dw, dst_transform))
    log.info("Wrote %s (%d×%d px, bounds %.3f,%.3f – %.3f,%.3f)", out_path.name, dw, dh, w4, s4, e4, n4)
    return out_path
