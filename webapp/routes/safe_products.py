"""Copernicus .SAFE products (Sentinel-1 / -2): import, link, open as layers. Shared helpers come from webapp/core.py."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import workspace as ws
from ..core import aoi as _aoi
from ..core import jobs, log

router = APIRouter()


# ------------------------------------------------------------------ Copernicus .SAFE products (Sentinel-1 / -2)

def _product_dirs() -> list[Path]:
    """Where .SAFE products are looked for: the workspace (project) and the app's own data/ folder."""
    dirs = [ws.root() / "data", ws.root(), ws.APP_DIR / "data", ws.APP_DIR]
    out = []
    for d in dirs:
        if d.is_dir() and d.resolve() not in [x.resolve() for x in out]:
            out.append(d)
    return out


def _linked_file() -> Path:
    return ws.CONFIG_DIR / "products.json"


def _linked() -> list[str]:
    """Products opened from where they are (Browse…): remembered across sessions, never copied."""
    import json as _json

    try:
        items = _json.loads(_linked_file().read_text())
    except (OSError, ValueError):
        return []
    return [p for p in items if isinstance(p, str)]


def _save_linked(items: list[str]):
    import json as _json

    try:
        ws.CONFIG_DIR.mkdir(exist_ok=True)
        _linked_file().write_text(_json.dumps(items[-50:], indent=1))
    except OSError as e:
        raise HTTPException(500, f"Can't remember the product: {e}")


def _all_products() -> list[dict]:
    from lulc_fetch.safe import describe, find_products

    out, seen = [], set()
    for p in find_products(*_product_dirs()):
        key = str(Path(p["path"]).resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    for lp in _linked():
        path = Path(lp)
        key = str(path.resolve())
        if key in seen or not path.exists():
            continue
        info = describe(path)
        if info:
            seen.add(key)
            out.append({**info, "linked": True})
    return out


def _product(path: str) -> dict:
    from lulc_fetch.safe import describe, product_name

    target = (ws.root() / path).resolve()
    for p in _all_products():
        if Path(p["path"]).resolve() == target or p["path"] == path:
            return p
    # not in the list (e.g. a .SAFE.zip next to its extracted folder, which the list shows instead), but a real
    # product in a data folder or opened with Browse
    allowed = {d.resolve() for d in _product_dirs()}
    if target.exists() and product_name(target.name) and (target.parent in allowed or str(target) in {str(Path(x).resolve()) for x in _linked()}):
        info = describe(target)
        if info:
            return info
    raise HTTPException(404, "No such product in the data folder")


def _product_out(p: dict) -> dict:
    return {**p, "path": ws.rel(p["path"])}


@router.get("/api/products")
def list_products():
    return {"folder": str(ws.root() / "data"), "products": [_product_out(p) for p in _all_products()]}


# Adding a product: dropped .SAFE folders are copied file by file (only what the readers need) into data/,
# dropped .SAFE.zip files as one file; "Browse…" links a product where it is, without copying.

def _upload_name(filename: str) -> str:
    from lulc_fetch.safe import product_name

    name = product_name(filename)
    if not name:
        raise HTTPException(400, f"“{Path(filename).name}” isn't a Sentinel-1 GRD or Sentinel-2 L1C / L2A product "
                                 "(the name should look like S2B_MSIL2A_… or S1A_IW_GRDH_…)")
    return name


def _upload_rel(rel: str) -> Path:
    parts = [x for x in rel.replace("\\", "/").split("/") if x not in ("", ".")]
    if not parts or any(x == ".." or ":" in x for x in parts):
        raise HTTPException(400, f"Bad file path in the product: {rel}")
    return Path(*parts)


class ProductUploadStart(BaseModel):
    folder: str = Field(max_length=300)                      # the dropped folder's name (…SAFE)
    files: list[dict] = Field(default_factory=list, max_length=20000)   # [{"rel", "size"}]


@router.post("/api/products/upload/start")
def product_upload_start(req: ProductUploadStart):
    """Which of the dropped product's files still have to be copied (files already there with the same size are kept)."""
    name = _upload_name(req.folder)
    final = ws.root() / "data" / f"{name}.SAFE"
    incoming = ws.root() / "data" / ".incoming" / f"{name}.SAFE"
    need = []
    for f in req.files:
        rel = _upload_rel(str(f.get("rel", "")))
        size = int(f.get("size") or 0)
        have = [d / rel for d in (final, incoming) if (d / rel).is_file()]
        if not any(h.stat().st_size == size for h in have):
            need.append(rel.as_posix())
    return {"name": name, "need": need, "exists": final.is_dir()}


@router.put("/api/products/upload/file")
async def product_upload_file(request: Request, folder: str, rel: str):
    name = _upload_name(folder)
    dest = ws.root() / "data" / ".incoming" / f"{name}.SAFE" / _upload_rel(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with open(tmp, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    tmp.replace(dest)
    return {"ok": True}


class ProductUploadFinish(BaseModel):
    folder: str = Field(max_length=300)


@router.post("/api/products/upload/finish")
def product_upload_finish(req: ProductUploadFinish):
    import shutil

    from lulc_fetch.safe import describe

    name = _upload_name(req.folder)
    final = ws.root() / "data" / f"{name}.SAFE"
    incoming = ws.root() / "data" / ".incoming" / f"{name}.SAFE"
    if incoming.is_dir():
        for f in sorted(incoming.rglob("*")):
            if f.is_file() and not f.name.endswith(".part"):
                (final / f.relative_to(incoming)).parent.mkdir(parents=True, exist_ok=True)
                f.replace(final / f.relative_to(incoming))
        shutil.rmtree(incoming, ignore_errors=True)
    if not final.is_dir():
        raise HTTPException(400, "Nothing was copied")
    info = describe(final)
    log.info("Added Sentinel product %s to %s", name, final.parent)
    return _product_out(info)


@router.post("/api/products/upload/zip")
async def product_upload_zip(request: Request, filename: str):
    import zipfile

    from lulc_fetch.safe import describe

    name = _upload_name(filename)
    dest = ws.root() / "data" / f"{name}.SAFE.zip"
    if not (dest.is_file() and request.headers.get("content-length") == str(dest.stat().st_size)):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        try:
            with zipfile.ZipFile(tmp) as z:
                if not any(".SAFE/" in m for m in z.namelist()[:50]):
                    raise HTTPException(400, f"{filename} doesn't contain a .SAFE product folder")
        except zipfile.BadZipFile:
            tmp.unlink(missing_ok=True)
            raise HTTPException(400, f"{filename} isn't a valid zip file (is the download complete?)")
        except HTTPException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
    log.info("Added Sentinel product %s to %s", dest.name, dest.parent)
    return _product_out(describe(dest))


class ProductLinkRequest(BaseModel):
    path: str = Field(max_length=2000)


@router.post("/api/products/link")
def product_link(req: ProductLinkRequest):
    """Open a product where it is on this computer (a .SAFE folder or .SAFE.zip, or a folder holding one)."""
    import os

    from lulc_fetch.safe import describe, find_products, product_name

    p = Path(os.path.expanduser(req.path.strip().strip('"').strip("'")))
    if not p.is_absolute() or not p.exists():
        raise HTTPException(400, f"{p} doesn't exist")
    if product_name(p.name):
        products = [describe(p)]
    elif p.is_dir() and find_products(p):          # a folder that holds products
        products = find_products(p)
    else:
        raise HTTPException(400, f"“{p.name}” isn't a Sentinel-1 GRD or Sentinel-2 L1C / L2A product folder (…SAFE) or zip")
    items = [x for x in _linked() if x not in [q["path"] for q in products]] + [q["path"] for q in products]
    _save_linked(items)
    return {"products": [_product_out({**q, "linked": True}) for q in products]}


@router.delete("/api/products/link")
def product_unlink(path: str):
    target = (ws.root() / path).resolve()
    _save_linked([x for x in _linked() if Path(x).resolve() != target])
    return {"ok": True}


class ProductOpenRequest(BaseModel):
    path: str
    res: float = Field(40, ge=10, le=500)  # Sentinel-1 output pixel size
    aoi: dict | None = None                 # optional clip for Sentinel-1


@router.post("/api/products/open")
def open_product(req: ProductOpenRequest):
    from lulc_fetch import safe

    p = _product(req.path)
    src = Path(p["path"])
    if p["kind"].startswith("S2"):
        out = ws.root() / "imports" / p["name"] / f"{p['name']}_10m.vrt"
        safe.s2_to_vrt(src, out)
        return {"kind": "raster", "path": ws.rel(out),
                "name": f"{p['title']} · {p['date']}"}
    aoi = _aoi(req.aoi) if req.aoi else None
    title = f"Sentinel-1 σ⁰ backscatter {p['date']} · {req.res:g} m{' · clipped' if aoi else ''}"

    def run(job):
        log.info("Processing %s", p["name"])
        out = safe.s1_to_backscatter(src, job.dir / f"S1_{p['date']}_sigma0_dB_{req.res:g}m.tif", res=req.res, aoi=aoi)
        return {"files": [str(out)]}

    return {"kind": "job", "job": jobs.submit("s1", title, {"source": "Sentinel-1 GRD", "res": req.res}, run).to_dict()}
