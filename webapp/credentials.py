"""Provider credentials kept in the OS keychain (macOS Keychain, Windows Credential Locker,
Secret Service on Linux). Secrets are never sent back to the browser — only whether they are set.

If no keychain backend is available, falls back to ~/.config/lulc-fetch/credentials.json (mode 600).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
SERVICE = "lulc-fetch"

PROVIDERS = {
    "cdse_account": {
        "title": "Copernicus Data Space — account",
        "help": "Your dataspace.copernicus.eu login. Used to download full original products (.SAFE).",
        "signup": "https://dataspace.copernicus.eu/",
        "fields": {"username": "Email / username", "password": "Password"},
        "secret": {"password"},
    },
    "cdse_s3": {
        "title": "Copernicus Data Space — S3 keys",
        "help": "Lets the app read only your area out of CDSE imagery (source = Copernicus).",
        "signup": "https://eodata-s3keysmanager.dataspace.copernicus.eu/",
        "fields": {"access_key": "Access key", "secret_key": "Secret key"},
        "secret": {"secret_key"},
    },
    "usgs": {
        "title": "USGS EarthExplorer (Landsat)",
        "help": "Downloads original Landsat product bundles. Use your ERS username and an M2M application token "
                "(Profile ▸ Access Request ▸ M2M API, then create an Application Token). Not needed to search, "
                "preview or download clipped Landsat data.",
        "signup": "https://ers.cr.usgs.gov/profile/access",
        "fields": {"username": "ERS username", "token": "M2M application token"},
        "secret": {"token"},
    },
    "planetary_computer": {
        "title": "Microsoft Planetary Computer (optional)",
        "help": "Not required. A subscription key only raises rate limits.",
        "signup": "https://planetarycomputer.developer.azure-api.net/",
        "fields": {"subscription_key": "Subscription key"},
        "secret": {"subscription_key"},
    },
}

_FALLBACK = Path.home() / ".config" / "lulc-fetch" / "credentials.json"


def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        kr = keyring.get_keyring()
        return None if isinstance(kr, fail.Keyring) else keyring
    except Exception:
        return None


def backend_name() -> str:
    kr = _keyring()
    return type(kr.get_keyring()).__module__.split(".")[-1] + " keychain" if kr else f"file {_FALLBACK}"


def _read_file() -> dict:
    try:
        return json.loads(_FALLBACK.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_file(data: dict):
    _FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_FALLBACK, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


def get(provider: str, field: str) -> str | None:
    key = f"{provider}.{field}"
    kr = _keyring()
    if kr:
        return kr.get_password(SERVICE, key)
    return _read_file().get(key)


def get_all(provider: str) -> dict[str, str | None]:
    return {f: get(provider, f) for f in PROVIDERS[provider]["fields"]}


def save(provider: str, values: dict[str, str]):
    spec = PROVIDERS[provider]
    kr = _keyring()
    data = None if kr else _read_file()
    for field, value in values.items():
        if field not in spec["fields"] or value is None or value == "":
            continue  # blank = keep the existing value
        if kr:
            kr.set_password(SERVICE, f"{provider}.{field}", value.strip())
        else:
            data[f"{provider}.{field}"] = value.strip()
    if data is not None:
        _write_file(data)


def delete(provider: str):
    kr = _keyring()
    data = None if kr else _read_file()
    for field in PROVIDERS[provider]["fields"]:
        key = f"{provider}.{field}"
        if kr:
            try:
                kr.delete_password(SERVICE, key)
            except Exception:
                pass
        else:
            data.pop(key, None)
    if data is not None:
        _write_file(data)


def status() -> dict:
    """Which fields are set, with non-secret values shown and secrets masked."""
    out = {}
    for provider, spec in PROVIDERS.items():
        values = get_all(provider)
        out[provider] = {
            **{k: spec[k] for k in ("title", "help", "signup")},
            "fields": [{"name": f, "label": label, "secret": f in spec["secret"], "set": bool(values[f]),
                        "display": None if f in spec["secret"] or not values[f] else values[f]}
                       for f, label in spec["fields"].items()],
            "complete": all(values.values()),
        }
    return out


def source_kwargs(source: str) -> dict:
    """Constructor kwargs for lulc_fetch.sources.get_source from stored credentials."""
    if source == "cdse":
        c = get_all("cdse_s3")
        return {"access_key": c["access_key"], "secret_key": c["secret_key"]}
    if source == "planetary-computer":
        return {"subscription_key": get("planetary_computer", "subscription_key")}
    return {}
