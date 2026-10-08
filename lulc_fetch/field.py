"""Field collection: the .zip that the phone page (webapp/static/field/, on the website at /lulc-fetch/field/) makes.

    field.json       {"app": "LULC Fetch Field", "format": 1, "project", "made", "points", "photos"}  (the first entry)
    points.geojson   a FeatureCollection of points: name, crop, note, time, accuracy_m, altitude_m, photos ["photos/…"]
    photos/…jpg      the photos (made smaller on the phone)

import_zip() unpacks it into a folder, writes each point's position and time into its photos' EXIF (GPS), so every tool
that reads a photo's position (Diagnose crop disease) finds it there, and writes points.geojson with the photos' paths.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

MARKER = "field.json"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_PHOTO_BYTES = 60 * 2**20


def is_field_zip(path_or_bytes) -> bool:
    try:
        src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
        with zipfile.ZipFile(src) as z:
            return MARKER in z.namelist() and json.loads(z.read(MARKER)).get("app") == "LULC Fetch Field"
    except (zipfile.BadZipFile, ValueError, KeyError, OSError):
        return False


def _rational(v: float):
    from PIL.TiffImagePlugin import IFDRational
    return IFDRational(round(v * 1_000_000), 1_000_000)


def add_gps(path: Path, lat: float, lon: float, alt: float | None = None, when: str | None = None) -> bool:
    """Write the position (and time) into a JPEG's EXIF, unless it has a position already. Keeps the JPEG's quality
    tables (quality="keep"). False when the photo isn't a JPEG or can't be read."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            if im.format != "JPEG":
                return False
            ex = im.getexif()
            gps = ex.get_ifd(0x8825)
            if gps and 2 in gps and 4 in gps:
                return True
            dms = lambda v: (_rational(int(abs(v))), _rational(int(abs(v) * 60 % 60)), _rational(abs(v) * 3600 % 60))   # noqa: E731
            g = {0: b"\x02\x03\x00\x00", 1: "N" if lat >= 0 else "S", 2: dms(lat), 3: "E" if lon >= 0 else "W", 4: dms(lon)}
            if alt is not None:
                g.update({5: 0 if alt >= 0 else 1, 6: _rational(abs(alt))})
            ex[0x8825] = g
            if when:
                try:
                    t = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone()   # the phone's local time, as cameras write it
                    stamp = t.strftime("%Y:%m:%d %H:%M:%S")
                    ex[306] = stamp
                    exif_ifd = ex.get_ifd(0x8769)
                    exif_ifd[36867] = stamp
                except ValueError:
                    pass
            tmp = path.with_name(path.name + ".part")
            im.save(tmp, "JPEG", quality="keep", exif=ex, icc_profile=im.info.get("icc_profile"))
        tmp.replace(path)
        return True
    except Exception:
        Path(str(path) + ".part").unlink(missing_ok=True)
        return False


def import_zip(src, dest: Path, rel=lambda p: str(p)) -> dict:
    """Unpack a field .zip (path or bytes) into dest: photos/ (with GPS in their EXIF) and points.geojson, whose features
    get photo (the first) and photos ("; "-separated) as paths given by rel(path)."""
    src = io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile as e:
        raise ValueError("Not a .zip file") from e
    with z:
        names = set(z.namelist())
        if MARKER not in names or "points.geojson" not in names:
            raise ValueError("Not a field collection file (no field.json and points.geojson): make it with LULC Fetch Field")
        meta = json.loads(z.read(MARKER))
        if meta.get("format", 1) > 1:
            raise ValueError("This field file is from a newer version of LULC Fetch Field: update the app")
        fc = json.loads(z.read("points.geojson"))
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "photos").mkdir(exist_ok=True)
        saved: dict[str, Path] = {}
        for info in z.infolist():   # only photos/<name>: nothing else, and never outside the folder
            p = PurePosixPath(info.filename)
            if len(p.parts) != 2 or p.parts[0] != "photos" or p.suffix.lower() not in PHOTO_EXTS or info.file_size > MAX_PHOTO_BYTES:
                continue
            out = dest / "photos" / p.name
            with z.open(info) as fh, open(out, "wb") as o:
                o.write(fh.read())
            saved[info.filename] = out
    feats, photos, located = [], [], 0
    for f in fc.get("features", []):
        g, props = f.get("geometry") or {}, dict(f.get("properties") or {})
        if g.get("type") != "Point" or len(g.get("coordinates") or []) < 2:
            continue
        lon, lat = g["coordinates"][:2]
        alt = g["coordinates"][2] if len(g["coordinates"]) > 2 else props.get("altitude_m")
        mine = [saved[n] for n in props.pop("photos", []) or [] if n in saved]
        for p in mine:
            located += add_gps(p, lat, lon, alt, props.get("time"))
            photos.append({"path": rel(p), "name": p.name, "point": props.get("name")})
        props["photo_count"] = len(mine)
        if mine:
            props["photo"] = rel(mine[0])
            props["photos"] = "; ".join(rel(p) for p in mine)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props})
    if not feats:
        raise ValueError("The field file has no points")
    out_fc = {"type": "FeatureCollection", "features": feats}
    gj = dest / "points.geojson"
    gj.write_text(json.dumps(out_fc, ensure_ascii=False), encoding="utf-8")
    return {"project": meta.get("project") or "", "made": meta.get("made"), "geojson": out_fc, "geojson_path": rel(gj),
            "points": len(feats), "photos": photos, "located_photos": located, "folder": rel(dest)}
