"""USGS EarthExplorer (M2M API): log in and download original Landsat Collection 2 product bundles.

Needs a USGS ERS account with M2M access and an application token (password login was retired):
https://ers.cr.usgs.gov/profile/access → request "Access to EE's Machine-to-Machine API", then create
an application token. Scene search and clipped downloads don't need this; they use the free Landsat
copy on Planetary Computer.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import requests

from . import progress

log = logging.getLogger(__name__)
API = "https://m2m.cr.usgs.gov/api/api/json/stable/"
DATASETS = {"L2SP": "landsat_ot_c2_l2", "L2SR": "landsat_ot_c2_l2", "L1TP": "landsat_ot_c2_l1",
            "L1GT": "landsat_ot_c2_l1", "L1GS": "landsat_ot_c2_l1"}


def _call(endpoint: str, payload: dict, api_key: str | None = None, timeout: int = 120):
    headers = {"X-Auth-Token": api_key} if api_key else {}
    r = requests.post(API + endpoint, json=payload, headers=headers, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError(f"EarthExplorer {endpoint}: HTTP {r.status_code}") from None
    if body.get("errorCode"):
        raise RuntimeError(f"EarthExplorer: {body.get('errorMessage') or body['errorCode']}")
    return body.get("data")


def login(username: str | None, token: str | None) -> str:
    """Exchange username + application token for an API session key."""
    if not username or not token:
        raise RuntimeError("Add your USGS EarthExplorer username and M2M application token under Credentials")
    return _call("login-token", {"username": username, "token": token})


def logout(api_key: str):
    try:
        _call("logout", {}, api_key, timeout=20)
    except Exception:
        pass


def download_product(display_id: str, entity_id: str | None, out_dir: str | Path, username: str, token: str) -> Path:
    """Download the original product bundle (.tar) of a Landsat scene, e.g. LC09_L2SP_144051_20260204_02_T1."""
    level = display_id.split("_")[1] if "_" in display_id else "L2SP"
    dataset = DATASETS.get(level, "landsat_ot_c2_l2")
    progress.update(0.01, "Logging in to EarthExplorer")
    key = login(username, token)
    try:
        if not entity_id:  # look the scene up by its display id
            hits = _call("scene-search", {"datasetName": dataset, "maxResults": 5, "sceneFilter": {
                "metadataFilter": {"filterType": "value", "filterId": "5e81f14ff4f9941c", "value": display_id,
                                   "operand": "="}}}, key)
            res = [r for r in (hits or {}).get("results", []) if r.get("displayId") == display_id]
            if not res:
                raise RuntimeError(f"{display_id} was not found in EarthExplorer ({dataset})")
            entity_id = res[0]["entityId"]
        progress.update(0.03, "Requesting the product bundle")
        options = _call("download-options", {"datasetName": dataset, "entityIds": [entity_id]}, key) or []
        bundles = [o for o in options if o.get("available") and "bundle" in (o.get("productName") or "").lower()]
        if not bundles:
            raise RuntimeError("No downloadable product bundle for this scene. Your account may need M2M download "
                               "access (ers.cr.usgs.gov ▸ Access Request).")
        opt = bundles[0]
        label = f"lulcfetch_{int(time.time())}"
        req = _call("download-request", {"downloads": [{"entityId": entity_id, "productId": opt["id"]}],
                                         "label": label}, key)
        urls = [d["url"] for d in (req or {}).get("availableDownloads", [])]
        for attempt in range(60):  # products may need preparing: poll for up to ~10 minutes
            if urls:
                break
            progress.update(0.04, f"Waiting for USGS to prepare the download ({attempt * 10} s)")
            time.sleep(10)
            ret = _call("download-retrieve", {"label": label}, key) or {}
            urls = [d["url"] for d in ret.get("available", [])]
        if not urls:
            raise RuntimeError("USGS did not make the download available in time. Try again later.")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with requests.get(urls[0], stream=True, timeout=300) as r:
            r.raise_for_status()
            cd = r.headers.get("Content-Disposition", "")
            name = re.search(r'filename="?([^";]+)', cd).group(1) if "filename" in cd else f"{display_id}.tar"
            total = int(r.headers.get("Content-Length", 0))
            out, tmp, done = out_dir / name, out_dir / (name + ".part"), 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=8 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    progress.update(0.05 + 0.95 * done / total if total else None,
                                    f"Downloading {done / 1e6:,.0f}{f' of {total / 1e6:,.0f}' if total else ''} MB")
            tmp.rename(out)
        log.info("Saved %s (%.0f MB)", out.name, out.stat().st_size / 1e6)
        return out
    finally:
        logout(key)
