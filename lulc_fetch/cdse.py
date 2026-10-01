"""Download complete Sentinel products (.SAFE zip) from the Copernicus Data Space Ecosystem.

Needs a free account at https://dataspace.copernicus.eu/ — export CDSE_USERNAME and CDSE_PASSWORD.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from . import progress

log = logging.getLogger(__name__)

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DOWNLOAD = "https://download.dataspace.copernicus.eu/odata/v1/Products({id})/$value"


def get_token(username: str | None = None, password: str | None = None) -> str:
    username = username or os.environ.get("CDSE_USERNAME")
    password = password or os.environ.get("CDSE_PASSWORD")
    if not username or not password:
        raise RuntimeError("Set CDSE_USERNAME and CDSE_PASSWORD (free account at https://dataspace.copernicus.eu/)")
    r = requests.post(TOKEN_URL, data={"grant_type": "password", "username": username,
                                       "password": password, "client_id": "cdse-public"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"CDSE login failed ({r.status_code}): {r.text[:200]}")
    return r.json()["access_token"]


def product_uuid(name: str) -> str:
    name = name.removesuffix(".zip")
    if not name.endswith(".SAFE") and name.startswith("S"):
        name += ".SAFE"
    r = requests.get(ODATA, params={"$filter": f"Name eq '{name}'"}, timeout=60)
    r.raise_for_status()
    values = r.json().get("value", [])
    if not values:
        raise RuntimeError(f"Product {name} not found in the CDSE catalogue")
    return values[0]["Id"]


def download_product(name: str, out_dir: str | Path = ".", username: str | None = None,
                     password: str | None = None) -> Path:
    uuid = product_uuid(name)
    token = get_token(username, password)
    out = Path(out_dir) / f"{name.removesuffix('.SAFE')}.SAFE.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    url = DOWNLOAD.format(id=uuid)
    # Follow redirects by hand: requests drops the Authorization header on cross-host redirects.
    while True:
        r = session.get(url, allow_redirects=False, stream=True, timeout=120)
        if r.status_code in (301, 302, 303, 307, 308):
            url = r.headers["Location"]
            continue
        r.raise_for_status()
        break
    total = int(r.headers.get("Content-Length", 0))
    done, next_pct = 0, 10
    tmp = out.with_suffix(".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=8 << 20):
            f.write(chunk)
            done += len(chunk)
            progress.update(done / total if total else None, f"Downloading {done / 1e6:,.0f}{f' of {total / 1e6:,.0f}' if total else ''} MB")
            if total and 100 * done / total >= next_pct:
                next_pct += 10
                log.info("  %s: %5.1f%% of %.0f MB", out.name, 100 * done / total, total / 1e6)
    tmp.rename(out)
    return out
