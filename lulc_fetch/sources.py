"""STAC catalogs that serve Sentinel-2 L2A, and how to turn their assets into readable paths."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from pystac import Item
from pystac_client import Client

S2_BANDS = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12"]
# 10 m + 20 m bands; the usual feature set for LULC classification.
DEFAULT_BANDS = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]

_EARTH_SEARCH_KEYS = {
    "B01": "coastal", "B02": "blue", "B03": "green", "B04": "red", "B05": "rededge1",
    "B06": "rededge2", "B07": "rededge3", "B08": "nir", "B8A": "nir08", "B09": "nir09",
    "B11": "swir16", "B12": "swir22", "SCL": "scl",
}

# GDAL settings that keep remote COG reads to a few range requests.
BASE_GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff,.jp2,.JP2",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"


class Source:
    name: str
    url: str
    collection = "sentinel-2-l2a"
    visual_asset = "visual"  # 8-bit true-colour (TCI) asset, used for quick previews

    def client(self) -> Client:
        return Client.open(self.url)

    def search_kwargs(self, max_cloud: float | None) -> dict:
        return {"query": {"eo:cloud_cover": {"lt": max_cloud}}} if max_cloud is not None else {}

    def asset_key(self, item: Item, band: str) -> str:
        raise NotImplementedError

    def href(self, item: Item, band: str) -> str:
        return item.assets[self.asset_key(item, band)].href

    def gdal_env(self) -> dict:
        return dict(BASE_GDAL_ENV)

    def boa_offset(self, item: Item) -> int:
        """DN offset to add before dividing by 10000 (processing baseline >= 04.00 adds +1000)."""
        pb = item.properties.get("s2:processing_baseline") or item.properties.get("processing:version")
        try:
            return -1000 if float(pb) >= 4.0 else 0
        except (TypeError, ValueError):
            return -1000 if item.datetime >= datetime(2022, 1, 25, tzinfo=timezone.utc) else 0


class EarthSearch(Source):
    """Element 84 Earth Search on AWS: public COGs, no account needed."""
    name = "earth-search"
    url = "https://earth-search.aws.element84.com/v1"

    def asset_key(self, item, band):
        return _EARTH_SEARCH_KEYS[band]

    def boa_offset(self, item):
        if item.properties.get("earthsearch:boa_offset_applied"):
            return 0
        return super().boa_offset(item)


class PlanetaryComputer(Source):
    """Microsoft Planetary Computer: public COGs, URLs are signed automatically (no key needed)."""
    name = "planetary-computer"
    url = PC_STAC

    def __init__(self, subscription_key: str | None = None):
        if subscription_key:  # optional; raises rate limits
            import planetary_computer
            planetary_computer.settings.set_subscription_key(subscription_key)

    def client(self):
        import planetary_computer
        return Client.open(self.url, modifier=planetary_computer.sign_inplace)

    def asset_key(self, item, band):
        return band


class CopernicusDataSpace(Source):
    """Copernicus Data Space Ecosystem (ESA). Assets are JP2 on S3 and need free S3 keys.

    Generate keys at https://eodata-s3keysmanager.dataspace.copernicus.eu/ and export
    CDSE_S3_ACCESS_KEY and CDSE_S3_SECRET_KEY.
    """
    name = "cdse"
    url = "https://stac.dataspace.copernicus.eu/v1"
    visual_asset = "TCI_10m"

    def __init__(self, access_key: str | None = None, secret_key: str | None = None):
        self.access_key = access_key or os.environ.get("CDSE_S3_ACCESS_KEY")
        self.secret_key = secret_key or os.environ.get("CDSE_S3_SECRET_KEY")

    def search_kwargs(self, max_cloud):
        if max_cloud is None:
            return {}
        return {"filter": {"op": "<", "args": [{"property": "eo:cloud_cover"}, max_cloud]},
                "filter_lang": "cql2-json"}

    def asset_key(self, item, band):
        for res in ("10m", "20m", "60m"):
            key = f"{band}_{res}"
            if key in item.assets:
                return key
        raise KeyError(f"{item.id} has no asset for {band}")

    def href(self, item, band):
        href = super().href(item, band)
        # Hand GDAL a /vsis3/ path directly so rasterio doesn't try to build a boto3 session.
        return href.replace("s3://", "/vsis3/", 1) if href.startswith("s3://") else href

    def gdal_env(self):
        key, secret = self.access_key, self.secret_key
        if not key or not secret:
            raise RuntimeError(
                "The cdse source needs S3 credentials. Create them (free) at "
                "https://eodata-s3keysmanager.dataspace.copernicus.eu/, then add them under Credentials "
                "in the web app (or export CDSE_S3_ACCESS_KEY / CDSE_S3_SECRET_KEY for the CLI). "
                "Or use the Earth Search source, which needs no login."
            )
        return {
            **BASE_GDAL_ENV,
            "AWS_S3_ENDPOINT": "eodata.dataspace.copernicus.eu",
            "AWS_ACCESS_KEY_ID": key,
            "AWS_SECRET_ACCESS_KEY": secret,
            "AWS_VIRTUAL_HOSTING": "FALSE",
            "AWS_HTTPS": "YES",
            "AWS_REGION": "default",
        }


SOURCES = {cls.name: cls for cls in (EarthSearch, PlanetaryComputer, CopernicusDataSpace)}


def get_source(name: str, **credentials) -> Source:
    try:
        return SOURCES[name](**credentials)
    except KeyError:
        raise ValueError(f"Unknown source {name!r}; choose from {', '.join(SOURCES)}") from None
