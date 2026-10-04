"""Library menu: GIS data kept on Hugging Face (datasets of the library accounts, e.g. ixrbhii/indian-shapefiles), listed,
downloaded once into the project's downloads/library/ and added to the map. The science is in lulc_fetch/library.py.
Shared helpers come from webapp/core.py."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import core
from .. import workspace as ws

router = APIRouter()
_cache: dict = {}   # (what, key) → (time, value): Hugging Face listings, kept 10 minutes


def _dest() -> Path:
    return ws.root() / "downloads" / "library"


def _accounts() -> list[str]:
    from lulc_fetch.library import ACCOUNTS
    extra = core.read_settings("library.json").get("accounts", [])
    return list(dict.fromkeys([*ACCOUNTS, *[a for a in extra if isinstance(a, str) and a.strip()]]))


def _cached(key, fn, ttl=600, refresh=False):
    hit = _cache.get(key)
    if hit and not refresh and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


@router.get("/api/library/datasets")
def library_datasets(refresh: bool = False):
    from lulc_fetch import library
    accts = _accounts()
    return {"accounts": accts, "datasets": _cached(("datasets", tuple(accts)), lambda: library.datasets(accts), refresh=refresh)}


@router.get("/api/library/files")
def library_files(repo: str, refresh: bool = False):
    from lulc_fetch import library
    if repo.count("/") != 1 or ".." in repo:
        raise HTTPException(400, "A dataset is owner/name")
    r = _cached(("files", repo), lambda: library.files(repo), refresh=refresh)
    base = _dest() / repo.replace("/", "__")
    return {**r, "files": [{**f, "local": (base / f["path"]).is_file()} for f in r["files"]]}


class LibraryAccounts(BaseModel):
    accounts: list[str] = Field(default_factory=list, max_length=20)


@router.post("/api/library/accounts")
def library_accounts(req: LibraryAccounts):
    """More Hugging Face accounts (or organisations) whose public datasets the Library lists."""
    import re
    bad = [a for a in req.accounts if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", a.strip())]
    if bad:
        raise HTTPException(400, f"Not a Hugging Face account name: {bad[0]}")
    try:
        core.write_settings("library.json", {"accounts": [a.strip() for a in req.accounts]})
    except OSError as e:
        raise HTTPException(500, f"Couldn't save the setting: {e}")
    _cache.clear()
    return {"accounts": _accounts()}


class LibraryFetch(BaseModel):
    repo: str = Field(max_length=200)
    path: str = Field(max_length=1000)
    sha256: str | None = Field(None, max_length=64)
    size: int | None = None


@router.post("/api/library/fetch")
def library_fetch(req: LibraryFetch):
    """Download one file (once) and get it ready for the map: rasters as they are, vectors as GeoJSON, tables into tables/."""
    from lulc_fetch import library
    if req.repo.count("/") != 1 or ".." in req.repo or ".." in req.path.split("/"):
        raise HTTPException(400, "Bad dataset or file name")
    kind = library.kind_of(req.path)
    if kind == "other" and req.path.lower().endswith(".json"):
        kind = "vector"   # listed as a map by the dataset's catalog (GeoJSON saved as .json); checked after download
    if kind == "other":
        raise HTTPException(400, f"{Path(req.path).name} isn't a map layer or a table (GeoJSON, shapefile, KML, GeoTIFF, CSV …)")
    name = Path(req.path).name

    def run(job):
        p = library.fetch(req.repo, req.path, _dest(), req.sha256, req.size)
        out = {"kind": kind, "name": Path(name).stem, "source": f"{req.repo}/{req.path}", "size_mb": round(p.stat().st_size / 1e6, 1)}
        if kind == "raster":
            out["path"] = ws.rel(p)
        elif kind == "table":
            from lulc_fetch.tableview import detect_lonlat, import_table, load
            t = import_table(p, core.TABLE_DIR.path)
            out.update(path=ws.rel(t), file=t.name, lonlat=detect_lonlat(load(t)))
        else:   # a vector: served to the map as GeoJSON (other formats converted once, next to the download)
            gj = p if p.suffix.lower() in (".geojson", ".json") else p.with_suffix(p.suffix + ".geojson")
            if gj != p and not gj.is_file():
                from .. import aoi_io
                parts = [p] + ([p.with_suffix(e) for e in (".shx", ".dbf", ".prj", ".cpg") if p.with_suffix(e).is_file()] if p.suffix.lower() == ".shp" else [])
                fc = aoi_io.parse_upload([(x.name, x.read_bytes()) for x in parts])
                gj.write_text(json.dumps(fc), encoding="utf-8")
            with open(gj, "rb") as fh:
                head = fh.read(4096).decode("utf-8", "ignore")
            if '"features"' not in head and '"FeatureCollection"' not in head and '"type"' not in head:
                raise RuntimeError(f"{name} isn't GeoJSON")
            out.update(path=ws.rel(gj), geojson_mb=round(gj.stat().st_size / 1e6, 1))
        out["outputs"] = [out["path"]]
        return out

    return core.jobs.submit("library", f"Library · {name}", {"dataset": req.repo}, run).to_dict()


@router.get("/api/library/geojson")
def library_geojson(path: str):
    """A downloaded vector as GeoJSON (only files in the library's download folder)."""
    p = (ws.root() / path).resolve()
    if not p.is_relative_to(_dest().resolve()) or p.suffix.lower() not in (".geojson", ".json") or not p.is_file():
        raise HTTPException(404, "No such library file: fetch it again")
    return FileResponse(p, media_type="application/geo+json")
