"""A Sentinel-1 product, wherever it is (a .SAFE folder, a .SAFE.zip, or a scene of Microsoft Planetary Computer read
over the internet), and what it is: mission, product type (RAW / SLC / GRD / RTC …), mode, polarisations, orbit, and
which processing it has had — so the workflow only offers the steps still needed.

    open_product(src) -> Product            src: a path, a Planetary Computer item (dict) or its id
    inspect(src) -> dict                     what it is + every processing step: done / needed / optional / not applicable
    inspect_raster(path) -> dict             the same for a SAR GeoTIFF (from GEE, ASF, Planetary Computer RTC, this app)
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

NAME = re.compile(r"(S1[ABCD])_(IW|EW|SM|WV|S\d)_(RAW|SLC|GRD|OCN|ETA)([FHM_])_(\d)([SA])(DV|DH|SV|SH|HH|VV|HV|VH)_(\d{8}T\d{6})_(\d{8}T\d{6})_(\d{6})_(\w{6})")
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"


def _t(s: str) -> float:
    """An annotation time (UTC, e.g. 2026-09-29T12:54:39.760868) as seconds since the epoch."""
    return datetime.fromisoformat(s.strip().rstrip("Z")).replace(tzinfo=timezone.utc).timestamp()


def _f(root, tag: str, default=None):
    e = next(root.iter(tag), None)
    return float(e.text) if e is not None and e.text else default


def _s(root, tag: str, default=None):
    e = next(root.iter(tag), None)
    return e.text.strip() if e is not None and e.text else default


@dataclass
class Annotation:
    """The geometry and timing of a GRD product (the same for every polarisation)."""
    first_line: float            # azimuth time of line 0 (s)
    line_interval: float         # azimuth time between lines (s)
    lines: int
    samples: int
    range_spacing: float         # ground range pixel spacing (m)
    azimuth_spacing: float
    near_slant_time: float       # two-way slant range time of the first sample (s)
    frequency: float
    heading: float               # platform heading (degrees from north)
    incidence_mid: float
    orbit: np.ndarray            # rows: t, x, y, z, vx, vy, vz (Earth fixed)
    grid: np.ndarray             # rows: line, pixel, lat, lon, height, incidence
    srgr: list                   # [(azimuth time, sr0, [coefficients])]: ground range = Σ cᵢ (R − sr0)ⁱ
    pass_dir: str


def parse_annotation(xml: str) -> Annotation:
    r = ET.fromstring(xml)
    orbit = np.array([[_t(o.find("time").text), *(float(o.find(f"position/{a}").text) for a in "xyz"),
                       *(float(o.find(f"velocity/{a}").text) for a in "xyz")] for o in r.iter("orbit")])
    grid = np.array([[float(g.find(k).text) for k in ("line", "pixel", "latitude", "longitude", "height", "incidenceAngle")]
                     for g in r.iter("geolocationGridPoint")])
    srgr = [(_t(c.find("azimuthTime").text), float(c.find("sr0").text), [float(x) for x in c.find("srgrCoefficients").text.split()])
            for c in r.iter("coordinateConversion") if c.find("srgrCoefficients") is not None]
    ia = r.find(".//imageAnnotation/imageInformation")
    return Annotation(first_line=_t(ia.find("productFirstLineUtcTime").text), line_interval=float(ia.find("azimuthTimeInterval").text),
                      lines=int(ia.find("numberOfLines").text), samples=int(ia.find("numberOfSamples").text),
                      range_spacing=float(ia.find("rangePixelSpacing").text), azimuth_spacing=float(ia.find("azimuthPixelSpacing").text),
                      near_slant_time=float(ia.find("slantRangeTime").text), frequency=_f(r, "radarFrequency", 5.405e9),
                      heading=_f(r, "platformHeading", 0.0), incidence_mid=_f(ia, "incidenceAngleMidSwath", 35.0),
                      orbit=orbit, grid=grid, srgr=srgr, pass_dir=(_s(r, "pass") or "").lower())


@dataclass
class Product:
    """A Sentinel-1 product: its name fields and where its files are (local paths or URLs)."""
    name: str
    source: str                                  # "safe" | "pc"
    measurement: dict = field(default_factory=dict)    # pol → GDAL path (/vsizip/…, /vsicurl/…)
    annotation: dict = field(default_factory=dict)     # pol → path or URL
    calibration: dict = field(default_factory=dict)
    noise: dict = field(default_factory=dict)
    manifest: str | None = None
    props: dict = field(default_factory=dict)          # STAC properties (Planetary Computer)

    @cached_property
    def fields(self) -> dict:
        m = NAME.search(self.name)
        if not m:
            return {}
        sat, mode, typ, res, level, cls, pol, start, stop, orbit, take = m.groups()
        return {"mission": sat, "mode": mode, "type": typ, "resolution": {"F": "full", "H": "high", "M": "medium"}.get(res, ""),
                "level": int(level), "class": cls, "pol_code": pol, "start": start, "absolute_orbit": int(orbit), "datatake": take}

    def text(self, ref: str) -> str:
        if ref.startswith("http"):
            import requests
            r = requests.get(ref, timeout=120)
            r.raise_for_status()
            return r.text
        if ref.startswith("/vsizip/"):
            zpath, inner = re.match(r"/vsizip/(.+?\.zip)/(.+)", ref).groups()
            with zipfile.ZipFile(zpath) as z:
                return z.read(inner).decode("utf-8", "ignore")
        return Path(ref).read_text(encoding="utf-8", errors="ignore")

    @cached_property
    def geometry(self) -> Annotation:
        pol = next(iter(self.annotation))
        return parse_annotation(self.text(self.annotation[pol]))

    @cached_property
    def ipf(self) -> str | None:
        if self.props.get("s1:processing_software"):
            return str(self.props["s1:processing_software"].get("Sentinel-1 IPF", "")) or None
        if self.manifest:
            m = re.search(r'name="Sentinel-1 IPF" version="([\d.]+)"', self.text(self.manifest))
            return m.group(1) if m else None
        return None


def _safe_members(path: Path) -> list[str]:
    if path.is_dir():
        return [str(p) for p in path.rglob("*") if p.is_file()]
    with zipfile.ZipFile(path) as z:
        return [f"/vsizip/{path}/{m}" for m in z.namelist() if not m.endswith("/")]


def _from_safe(path: Path) -> Product:
    members = _safe_members(path)
    p = Product(name=path.name, source="safe")
    for m in members:
        low = m.lower()
        pol = next((x for x in ("vv", "vh", "hh", "hv") if f"-{x}-" in low), None)
        if low.endswith("manifest.safe"):
            p.manifest = m
        if not pol:
            continue
        if "/measurement/" in low and low.endswith((".tiff", ".tif")):
            p.measurement[pol.upper()] = m
        elif "/annotation/calibration/calibration-" in low:
            p.calibration[pol.upper()] = m
        elif "/annotation/calibration/noise-" in low:
            p.noise[pol.upper()] = m
        elif "/annotation/" in low and low.endswith(".xml") and "/calibration/" not in low and "/rfi/" not in low:
            p.annotation[pol.upper()] = m
    if not p.measurement and not NAME.search(path.name):
        raise ValueError(f"{path.name} isn't a Sentinel-1 product (.SAFE folder or .zip)")
    return p


def pc_item(item_or_id) -> dict:
    """A Planetary Computer Sentinel-1 GRD / RTC item (signed) as a dict."""
    import planetary_computer as pc
    import pystac_client
    if isinstance(item_or_id, dict):
        import pystac
        return pc.sign(pystac.Item.from_dict(item_or_id)).to_dict()
    cat = pystac_client.Client.open(PC_STAC, modifier=pc.sign_inplace, timeout=60)
    for col in ("sentinel-1-grd", "sentinel-1-rtc"):
        found = list(cat.search(collections=[col], ids=[item_or_id]).items())
        if found:
            return found[0].to_dict()
    raise ValueError(f"No Sentinel-1 scene {item_or_id} on Planetary Computer")


def _from_pc(item: dict) -> Product:
    a = item["assets"]
    p = Product(name=item["id"], source="pc", props=item.get("properties", {}))
    p.props["collection"] = item.get("collection")
    cal = a.get("schema-calibration-vv") or a.get("schema-calibration-hh")
    base, q = cal["href"].split("?", 1) if cal else ("", "")
    root = base.rsplit("/annotation/", 1)[0]   # the product's folder: its main annotation (annotation/iw-vv.xml) is there too
    for pol in ("vv", "vh", "hh", "hv"):
        if pol in a:
            p.measurement[pol.upper()] = "/vsicurl/" + a[pol]["href"]
        if f"schema-calibration-{pol}" in a:
            p.calibration[pol.upper()] = a[f"schema-calibration-{pol}"]["href"]
            p.noise[pol.upper()] = a[f"schema-noise-{pol}"]["href"]
            p.annotation[pol.upper()] = f"{root}/annotation/iw-{pol}.xml?{q}".replace("/iw-", f"/{(p.props.get('sar:instrument_mode') or 'IW').lower()}-")
    if "safe-manifest" in a:
        p.manifest = a["safe-manifest"]["href"]
    return p


def open_product(src) -> Product:
    if isinstance(src, Product):
        return src
    if isinstance(src, dict):
        return _from_pc(pc_item(src))
    s = str(src)
    if Path(s).exists():
        return _from_safe(Path(s))
    if re.match(r"^S1[ABCD]_", s):
        return _from_pc(pc_item(s))
    raise ValueError(f"Not a Sentinel-1 product or Planetary Computer scene: {s}")


# ------------------------------------------------------------------ what it is, and what it still needs
STEPS = [
    ("validate", "Product check", "Read the product's metadata and check it can be processed"),
    ("orbit", "Precise orbit", "ESA's orbit file for exact positions in terrain correction: precise (POEORB, after ~20 days), else restituted (RESORB, within hours)"),
    ("border", "Border noise removal", "Remove the noisy strips at the image's near and far edges"),
    ("thermal", "Thermal noise removal", "Subtract the receiver's thermal noise (noise vectors of the product)"),
    ("calibrate", "Radiometric calibration", "Digital numbers → backscatter coefficient (σ⁰, β⁰ or γ⁰)"),
    ("speckle", "Speckle filtering", "Reduce speckle (Lee, Refined Lee, Lee Sigma, Frost, Gamma MAP …): optional, it also blurs details"),
    ("flatten", "Terrain flattening", "Radiometric terrain correction: backscatter of slopes made comparable (γ⁰ flattened; area-based, Small 2011, or angular)"),
    ("terrain", "Terrain correction", "Range-Doppler orthorectification with a DEM (Copernicus DEM 30 m)"),
    ("normalise", "Incidence angle normalisation", "Backscatter as if seen at one angle (40°): near and far range, and different orbit tracks, become comparable"),
    ("db", "Convert to dB", "10·log10 of the power (the linear values are kept too)"),
    ("reproject", "Reproject", "Into a projected system (UTM of the area by default)"),
    ("resample", "Resample / align", "A pixel size, or the grid of another layer (pixel-aligned with optical / DEM)"),
    ("clip", "Clip to area", "Only the area of interest"),
]


def _status(done: set, needed: set, optional: set, na: dict) -> list[dict]:
    out = []
    for k, title, about in STEPS:
        st = "done" if k in done else "needed" if k in needed else "optional" if k in optional else "na"
        out.append({"step": k, "title": title, "about": about, "status": st, "note": na.get(k, "")})
    return out


def inspect(src) -> dict:
    """What a Sentinel-1 product is, and the status of every processing step (done / needed / optional / n.a.)."""
    p = open_product(src)
    f = p.fields
    typ = f.get("type") or (p.props.get("sar:product_type"))
    rtc = p.props.get("collection") == "sentinel-1-rtc"
    info = {"name": p.name, "source": {"safe": "file", "pc": "Microsoft Planetary Computer"}[p.source], "mission": f.get("mission") or p.name[:3],
            "type": "RTC" if rtc else typ, "mode": f.get("mode") or p.props.get("sar:instrument_mode"),
            "polarisations": sorted(p.measurement) or p.props.get("sar:polarizations", []), "level": f.get("level"),
            "date": (f.get("start") or p.props.get("start_datetime", ""))[:8], "absolute_orbit": f.get("absolute_orbit") or p.props.get("sat:absolute_orbit"),
            "relative_orbit": p.props.get("sat:relative_orbit"), "orbit_direction": p.props.get("sat:orbit_state"), "resolution": f.get("resolution")}
    if info["date"] and len(info["date"]) == 8:
        info["date"] = f"{info['date'][:4]}-{info['date'][4:6]}-{info['date'][6:]}"
    if f.get("absolute_orbit") and not info["relative_orbit"]:   # S1A: (abs − 73) mod 175 + 1; S1B: (abs − 27) mod 175 + 1; C/D as A
        off = 27 if info["mission"] == "S1B" else 73
        info["relative_orbit"] = (f["absolute_orbit"] - off) % 175 + 1
    done, needed, optional, na = {"validate"}, set(), set(), {}
    if rtc:
        done |= {"orbit", "border", "calibrate", "flatten", "terrain", "reproject"}
        optional |= {"speckle", "db", "resample", "clip"}
        na["thermal"] = ("not removed by the producer: Planetary Computer's RTC keeps the thermal noise (it matches this app's GRD processing without "
                         "noise removal within 0.1 dB), so dark surfaces in VH (water, smooth fields) read up to ~1 dB too bright. It can't be "
                         "removed afterwards: for water and flooding in VH, process the GRD scene of the same date instead")
        na["normalise"] = "RTC scenes have no incidence-angle layer (and γ⁰ flattened varies little with the angle)"
        info.update(units="γ⁰ terrain-flattened, linear power", summary="Analysis-ready (RTC): calibrated, terrain-flattened and terrain-corrected (thermal noise not removed). Those steps will be skipped.")
    elif typ == "GRD":
        try:
            g = p.geometry
            info.update(orbit_direction=info["orbit_direction"] or g.pass_dir, heading=round(g.heading, 2), incidence_mid=round(g.incidence_mid, 2),
                        size=[g.samples, g.lines], pixel_spacing=[g.range_spacing, g.azimuth_spacing])
        except Exception as e:   # noqa: BLE001 (described below)
            info["warning"] = f"The product's annotation couldn't be read: {str(e)[:200]}"
        ipf = p.ipf
        info["ipf"] = ipf
        needed |= {"thermal", "calibrate", "terrain", "reproject"}
        optional |= {"orbit", "speckle", "flatten", "normalise", "db", "resample", "clip"}
        if ipf and tuple(int(x) for x in ipf.split(".")[:2]) >= (2, 90):
            done.add("border")
            na["border"] = f"already done by the processor (IPF {ipf} ≥ 2.90 removes border noise)"
        else:
            needed.add("border")
        orb = p.props.get("s1:orbit_source")
        if orb == "POEORB":
            done.add("orbit")
        else:
            na["orbit"] = f"the product has {'a restituted (RESORB)' if orb == 'RESORB' else 'its predicted / restituted'} orbit: ESA's precise one comes ~20 days after acquisition, a restituted one within hours (used when there is no precise one)"
        info.update(units="digital numbers (not calibrated)", summary="Level-1 GRD: detected, multilooked, in ground range, but not calibrated, noise-corrected or terrain-corrected. Those steps are needed.")
    elif typ == "SLC":
        info["summary"] = ("Single Look Complex (SLC): keeps the phase, for InSAR (interferograms, coherence, ground movement). This app doesn't "
                           "process SLC itself: Analysis ▸ SAR ▸ InSAR & RTC on demand (ASF HyP3) makes the interferogram for you, free with a NASA "
                           "Earthdata login. For backscatter, use the GRD product of the same date.")
        info["supported"] = False
    elif typ == "RAW":
        info["summary"] = "Level-0 raw data: it must be focused into SLC / GRD first (ESA's processor). Use the GRD product of the same date."
        info["supported"] = False
    else:
        info["summary"] = f"{typ or 'This'} product isn't backscatter imagery: use a GRD or RTC product."
        info["supported"] = False
    info.setdefault("supported", True)
    info["steps"] = _status(done, needed, optional, na)
    return info


def inspect_raster(path) -> dict:
    """A SAR GeoTIFF already processed elsewhere (GEE export, ASF / Planetary Computer RTC, this app): its units and what
    has been done, from its tags, band names and values."""
    import rasterio
    with rasterio.open(path) as s:
        tags, desc = s.tags(), [d or "" for d in s.descriptions]
        f = min(1.0, 512 / max(s.width, s.height))
        a = s.read(1, out_shape=(max(1, int(s.height * f)), max(1, int(s.width * f))), masked=True).astype("float64").filled(np.nan)
        crs = s.crs.to_string() if s.crs else None
    v = a[np.isfinite(a)]
    name = Path(path).name
    units = (tags.get("units") or "").lower()
    db = "db" in units or (v.size and np.nanmedian(v) < 0 and np.nanmin(v) > -60)
    pols = [d.upper() for d in desc if re.fullmatch(r"(VV|VH|HH|HV)(_?DB|_?LIN(EAR)?)?", d.upper())] or \
           [x.upper() for x in re.findall(r"(?<![a-z])(vv|vh|hh|hv)(?![a-z])", name.lower())]
    sar = bool(pols) or "sar" in (tags.get("sar") or "") or "sigma0" in units or "gamma0" in units or "backscatter" in units
    src = tags.get("sar_source") or ("Planetary Computer RTC" if "_rtc" in name.lower() else "ASF RTC" if re.search(r"RTC\d{2}", name) else
                                     "this app (.SAFE import)" if "sigma0" in units else "GEE (COPERNICUS/S1_GRD)" if db and "angle" in [d.lower() for d in desc] else "")
    done = {"validate", "orbit", "border", "thermal", "calibrate", "terrain"} if sar else {"validate"}
    if "flatten" in (tags.get("sar_steps") or "") or "gamma0" in units or "rtc" in name.lower():
        done.add("flatten")
    if (tags.get("sar_steps") or ""):
        done |= set(tags["sar_steps"].split(","))
    if db:
        done.add("db")
    if crs and not crs.endswith("4326"):
        done.add("reproject")
    optional = {"speckle", "db", "resample", "clip", "reproject"} - done
    with rasterio.open(path) as s:
        has_angle = any((d or "").lower() in ("angle", "incidence", "local_incidence", "incidence_angle") for d in s.descriptions)
    has_angle = has_angle or Path(str(path).replace("_linear", "_angles").replace("_dB", "_angles")).exists() and "_angles" not in Path(path).name
    if "normalise" not in done:
        if has_angle and sar:
            optional.add("normalise")
    own_orbit = tags.get("orbit") and "POEORB" not in tags["orbit"].upper()   # made here before the precise orbit was out
    if own_orbit:
        done.discard("orbit")
    na = {k: "can't be redone on a processed raster (it needs the original product)" for k in ("orbit", "border", "thermal", "calibrate", "terrain", "flatten") if k not in done}
    if "normalise" not in done and "normalise" not in optional:
        na["normalise"] = "needs the incidence angles: a band named 'angle' (GEE exports it), or this app's _angles.tif beside the file"
    if own_orbit:
        na["orbit"] = "the product's own orbit was used (the precise one wasn't published yet): process the product again ~20 days after its date for it"
    return {"name": name, "source": src or "a SAR raster", "type": "processed raster", "polarisations": sorted(set(p[:2] for p in pols)), "sar": sar,
            "units": "dB" if db else "linear power", "crs": crs, "supported": sar,
            "summary": ("Already calibrated and terrain-corrected" + (" (and in dB)" if db else "") + ": those steps will be skipped. "
                        "Speckle filtering, features, time series and change detection can follow.") if sar else
                       "This raster doesn't look like SAR backscatter (no VV / VH / HH / HV bands or SAR tags).",
            "steps": _status(done, set(), optional, na), "tags": {k: v for k, v in tags.items() if k.startswith("sar") or k == "units"}}
