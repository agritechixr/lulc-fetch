"""Insert ▸ Online map layer: the layers of a WMS / WMTS service (read from its GetCapabilities on the server, as most
services don't allow a web page to read it). The map draws the tiles itself. The science: lulc_fetch/online_layers.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/api/online/capabilities")
def online_capabilities(url: str):
    from lulc_fetch import online_layers
    if not url.strip() or len(url) > 4000:
        raise HTTPException(400, "Give the service's address")
    try:
        return online_layers.capabilities(url)
    except online_layers.ServiceError as e:
        raise HTTPException(400, str(e))
