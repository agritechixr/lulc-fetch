"""Satellite embeddings: the Embeddings menu.

An embedding gives every 10 m pixel a vector that sums up a whole year of satellite observations; similar places get
similar vectors, so a few labelled points are enough to classify, cluster or search them. Open, per-pixel datasets that
are read directly over HTTPS, without an account: Google AlphaEarth Foundations (64-D, CC-BY-4.0) and TESSERA (128-D, CC0).

    sources.py      the datasets (SOURCES, shown in the panel), other open embeddings, the HTTP session
    alphaearth.py   reads AlphaEarth (index, blocks, decoding)
    tessera.py      reads TESSERA (tiles, row ranges, scales)
    download.py     area → output grid, size estimate, which years exist, download to GeoTIFF
    explore.py      colour view (PCA) and similar places, for any embedding GeoTIFF
    formats.py      number formats: 8-bit (AlphaEarth coding or scaled per band) ↔ 16 / 32-bit float

Adding a tool to the Embeddings menu: put its code here (a new module), its endpoints in webapp/server.py (section
"Satellite embeddings", /api/emb/...), and its panel: a <section id="tab-<id>"> in webapp/static/index.html and an entry
{ id, menu: "embed", title, icon, subtitle } in TOOLS in webapp/static/app.js.
"""

from .formats import FORMATS, convert, detect
from .download import available, estimate, fetch
from .explore import colour_view, similarity
from .sources import OTHER, SOURCES, YEARS

__all__ = ["SOURCES", "OTHER", "YEARS", "estimate", "available", "fetch", "colour_view", "similarity", "FORMATS", "detect", "convert"]
