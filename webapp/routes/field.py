"""Insert ▸ Field collection: import the .zip of the phone page (points and photos taken in the field) into the workspace's
uploads/field/<survey>_<id>/. The science: lulc_fetch/field.py · the phone page: webapp/static/field/."""

from __future__ import annotations

import shutil
import tempfile
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import core
from .. import workspace as ws

router = APIRouter()
FIELD_URL = "https://agritechixr.github.io/lulc-fetch/field/"


@router.get("/api/field/info")
def field_info():
    return {"url": FIELD_URL}


@router.post("/api/field/import")
def field_import(file: UploadFile = File(...)):
    from lulc_fetch import field
    with tempfile.TemporaryFile() as tmp:
        shutil.copyfileobj(file.file, tmp, length=8 << 20)
        if tmp.tell() > 2 * 2**30:
            raise HTTPException(413, "The file is larger than 2 GB")
        tmp.seek(0)
        if not field.is_field_zip(tmp):
            raise HTTPException(400, "Not a field collection file: make it with LULC Fetch Field (Send to LULC Fetch)")
        tmp.seek(0)
        stem = core.safe_stem((file.filename or "field").rsplit(".", 1)[0], "field")
        dest = ws.root() / "uploads" / "field" / f"{stem}_{uuid.uuid4().hex[:6]}"
        try:
            res = field.import_zip(tmp, dest, ws.rel)
        except ValueError as e:
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(400, str(e))
    res["name"] = res["project"] or stem
    return res
