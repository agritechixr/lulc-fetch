"""The open embedding datasets: what they are, where they are, and the shared HTTP session.

To add a dataset: describe it in SOURCES (the panel lists it), then add a reader module like alphaearth.py / tessera.py
and connect it in download.py (available + fetch).
"""

from __future__ import annotations

AEF_BASES = ["https://storage.googleapis.com/alphaearth_foundations/satellite_embedding/v1/annual",
             "https://data.source.coop/tge-labs/aef/v1/annual"]          # Google (free since July 2026) · Source Cooperative mirror
TESSERA_BASE = "https://data.source.coop/tessera/tessera"
TESSERA_VERSION = "v1"
YEARS = list(range(2017, 2026))

SOURCES = {
    "aef": {
        "title": "Google AlphaEarth Foundations", "short": "AlphaEarth", "dims": 64, "res": 10, "years": [2017, 2025],
        "by": "Google and Google DeepMind", "licence": "CC-BY-4.0",
        "attribution": "The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind.",
        "coverage": "Global land and coastal waters, every year 2017–2025",
        "about": "Learned from Sentinel-1 and -2, Landsat, elevation, climate and more; one 64-dimensional unit vector per 10 m "
                 "pixel per year. Compare pixels with cosine similarity (dot product).",
        "url": "https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL",
        "resolutions": [10, 20, 40, 80, 160], "band_prefix": "A",
    },
    "tessera": {
        "title": "TESSERA", "short": "TESSERA", "dims": 128, "res": 10, "years": [2017, 2025],
        "by": "University of Cambridge", "licence": "CC0",
        "attribution": "TESSERA embeddings, University of Cambridge (Feng et al., 2025, arXiv:2506.20380).",
        "coverage": "Global land for 2024; other years 2017–2025 in many regions",
        "about": "Learned from a full year of Sentinel-1 and Sentinel-2 time series; one 128-dimensional vector per 10 m pixel "
                 "per year, strong for crops and seasonal land cover.",
        "url": "https://github.com/ucam-eo/geotessera", "resolutions": [10], "band_prefix": "E",
    },
}
OTHER = [   # open embeddings this tool can't download as per-pixel maps (shown for reference)
    {"title": "Major TOM embeddings (ESA Φ-lab)", "what": "One vector per Sentinel-1/-2 image patch (SigLIP, DINOv2, SSL4EO), global, as GeoParquet",
     "url": "https://huggingface.co/Major-TOM"},
    {"title": "Clay foundation model embeddings", "what": "768-D vectors per image chip for some regions (e.g. NAIP over the USA)",
     "url": "https://huggingface.co/made-with-clay"},
]

_HTTP = None


def session():
    global _HTTP
    if _HTTP is None:
        import requests
        from requests.adapters import HTTPAdapter
        _HTTP = requests.Session()
        _HTTP.mount("https://", HTTPAdapter(pool_connections=32, pool_maxsize=64))
        _HTTP.headers["User-Agent"] = "LULC-Fetch"
    return _HTTP


def get_range(url: str, start: int, end: int, timeout: float = 120, tries: int = 5, chunk: int = 16 << 20) -> bytes:
    """Bytes start..end (inclusive) of a file over HTTP, in pieces of at most `chunk` bytes; a piece whose connection
    drops (or comes back short, or hits a server error) is asked for again, up to `tries` times with growing waits."""
    import time

    import requests
    out = bytearray()
    pos = start
    while pos <= end:
        stop = min(end, pos + chunk - 1)
        for attempt in range(tries):
            try:
                r = session().get(url, headers={"Range": f"bytes={pos}-{stop}"}, timeout=timeout)
                if r.status_code >= 500:
                    raise requests.HTTPError(f"server error {r.status_code}")
                r.raise_for_status()
                if len(r.content) != stop - pos + 1:
                    raise requests.ConnectionError(f"short read: {len(r.content)} of {stop - pos + 1} bytes")
                out += r.content
                break
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError, requests.exceptions.ChunkedEncodingError) as e:
                if attempt == tries - 1 or (isinstance(e, requests.HTTPError) and "server error" not in str(e)):
                    raise RuntimeError(f"Download of {url.rsplit('/', 1)[-1]} failed after {attempt + 1} tries: {e}") from e
                time.sleep(2 * (attempt + 1))
        pos = stop + 1
    return bytes(out)


GDAL_HTTP = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tiff,.tif", GDAL_HTTP_MULTIRANGE="YES",
                 GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", GDAL_HTTP_VERSION="2", GDAL_HTTP_MULTIPLEX="YES", GDAL_HTTP_MAX_RETRY="4",
                 GDAL_HTTP_RETRY_DELAY="2", VSI_CACHE="TRUE")

