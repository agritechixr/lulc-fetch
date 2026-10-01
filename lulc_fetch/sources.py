"""STAC catalogs that serve Sentinel-2 L2A, and how to turn their assets into readable paths."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import numpy as np
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
    mission = "sentinel-2"
    visual_asset = "visual"  # 8-bit true-colour (TCI) asset for quick previews; None = build from bands
    mask_band = "SCL"        # quality layer used for cloud masking
    native_res = 10

    def to_reflectance(self, item: Item, dn):
        """Raw digital numbers → surface reflectance (0-1)."""
        return (dn + self.boa_offset(item)) / 10000.0

    def mask_classes(self, m):
        """(cloud, shadow, no-data) boolean arrays from the quality layer (Sentinel-2 SCL classes)."""
        m = np.nan_to_num(m, nan=0)
        return np.isin(m, (8, 9, 10)), m == 3, np.isin(m, (0, 1))

    def tile(self, item: Item) -> str:
        p = item.properties
        if "s2:mgrs_tile" in p:
            return p["s2:mgrs_tile"]
        if "grid:code" in p:
            return p["grid:code"].replace("MGRS-", "")
        return item.id.split("_")[1] if "_" in item.id else item.id

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


_LANDSAT_KEYS = {"B01": "coastal", "B02": "blue", "B03": "green", "B04": "red", "B08": "nir08",
                 "B11": "swir16", "B12": "swir22", "QA": "qa_pixel"}
LANDSAT_BANDS = ["B01", "B02", "B03", "B04", "B08", "B11", "B12"]
LANDSAT_DEFAULT_BANDS = ["B02", "B03", "B04", "B08", "B11", "B12"]


class PlanetaryComputerLandsat(PlanetaryComputer):
    """USGS Landsat 8 / 9 Collection 2 Level-2 surface reflectance (30 m), mirrored on Planetary Computer.

    Bands are stored under their Sentinel-2-equivalent names so every index works unchanged:
    B02 = SR_B2 blue, B03 = SR_B3 green, B04 = SR_B4 red, B08 = SR_B5 NIR, B11 = SR_B6 SWIR1, B12 = SR_B7 SWIR2.
    """
    name = "landsat-pc"
    collection = "landsat-c2-l2"
    mission = "landsat"
    visual_asset = None
    mask_band = "QA"
    native_res = 30

    def search_kwargs(self, max_cloud):
        q = {"platform": {"in": ["landsat-8", "landsat-9"]}}
        if max_cloud is not None:
            q["eo:cloud_cover"] = {"lt": max_cloud}
        return {"query": q}

    def asset_key(self, item, band):
        return _LANDSAT_KEYS[band]

    def to_reflectance(self, item, dn):
        return dn * 2.75e-5 - 0.2

    def mask_classes(self, m):
        """QA_PIXEL bit flags: 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow."""
        q = np.nan_to_num(m, nan=1).astype("uint16")
        bit = lambda b: (q >> b) & 1 == 1
        return bit(1) | bit(2) | bit(3), bit(4), bit(0)

    def tile(self, item):
        p = item.properties
        return f"WRS {p.get('landsat:wrs_path', '?')}/{p.get('landsat:wrs_row', '?')}"


SOURCES = {cls.name: cls for cls in (EarthSearch, PlanetaryComputer, CopernicusDataSpace, PlanetaryComputerLandsat)}

MISSIONS = {
    "sentinel-2": {"title": "Sentinel-2 L2A (10 m)", "sources": ["earth-search", "planetary-computer", "cdse"],
                   "bands": S2_BANDS, "default_bands": DEFAULT_BANDS, "res": 10},
    "landsat": {"title": "Landsat 8–9 Collection 2 L2 (30 m)", "sources": ["landsat-pc"],
                "bands": LANDSAT_BANDS, "default_bands": LANDSAT_DEFAULT_BANDS, "res": 30},
}


def get_source(name: str, **credentials) -> Source:
    try:
        return SOURCES[name](**credentials)
    except KeyError:
        raise ValueError(f"Unknown source {name!r}; choose from {', '.join(SOURCES)}") from None
