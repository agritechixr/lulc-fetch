"""Online map layers: WMS, WMTS and XYZ tile services (Bhuvan, NASA GIBS, any custom address) as map layers.

The map (Leaflet, Web Mercator) draws the tiles itself, straight from the service; this module only reads a service's
GetCapabilities document so its layers can be listed and chosen:

    capabilities(url) -> {"kind": "wms" | "wmts" | "xyz", "title", "layers": [...]}

WMS layers are drawn in EPSG:3857 (every WMS server can reproject). WMTS layers are offered only in a tile matrix set
that matches Web Mercator's (GoogleMapsCompatible): each one becomes a tile address with {z} {x} {y} (and {time} for
layers with a time dimension), plus the tile matrix names when they aren't the zoom levels themselves.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_BYTES = 60 * 2**20          # Bhuvan's WMS lists ~7,000 layers in 11 MB
MAX_LAYERS = 20000
WEB_MERCATOR = {"3857", "900913", "3785", "102100", "102113"}
Z0_SCALE = 559082264.0287178    # scale denominator of zoom 0 in Web Mercator (256-pixel tiles, 0.28 mm pixels)
HALF = 20037508.342789244

W = "http://www.opengis.net/wmts/1.0"
OWS = "http://www.opengis.net/ows/1.1"
XLINK = "{http://www.w3.org/1999/xlink}href"


class ServiceError(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(el, name: str):
    return [c for c in el if _local(c.tag) == name]


def _kid(el, name: str):
    return next((c for c in el if _local(c.tag) == name), None) if el is not None else None


def _text(el, name: str) -> str:
    c = _kid(el, name)
    return (c.text or "").strip() if c is not None and c.text else ""


def _title(svc, url: str) -> str:
    """The service's title, or its host when the title is missing or a server's placeholder ("no title set…")."""
    t = _text(svc, "Title")
    return t if t and not re.search(r"no title|untitled|^(ogc:)?wms$|^wmts$", t, re.I) else urlsplit(url).netloc


def is_xyz(url: str) -> bool:
    return all(k in url for k in ("{z}", "{x}")) and ("{y}" in url or "{-y}" in url)


def _with_query(url: str, **params) -> str:
    """url with these query parameters set (case-insensitively replacing any it has)."""
    u = urlsplit(url)
    keep = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in {p.lower() for p in params}]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(keep + list(params.items())), u.fragment))


def base_url(url: str) -> str:
    """The service address without its OGC request parameters (what GetMap / GetTile requests are added to)."""
    u = urlsplit(url)
    ogc = {"service", "request", "version", "acceptversions"}
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in ogc]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ""))


def _fetch(url: str) -> bytes:
    import requests
    if urlsplit(url).scheme not in ("http", "https"):
        raise ServiceError("Give a web address (http:// or https://)")
    try:
        with requests.get(url, timeout=(15, 90), stream=True, headers={"User-Agent": "LULC-Fetch"}) as r:
            r.raise_for_status()
            data = b""
            for chunk in r.iter_content(1 << 20):
                data += chunk
                if len(data) > MAX_BYTES:
                    raise ServiceError("The service's description is larger than 60 MB")
            return data
    except requests.RequestException as e:
        raise ServiceError(f"Couldn't reach the service: {e}") from e


def parse_xml(data: bytes):
    head = data[:4096].lstrip()
    if not head.startswith(b"<"):
        raise ServiceError("The address didn't return a capabilities document (XML): check it is a WMS or WMTS address")
    if b"<!ENTITY" in data[:20000]:   # entity declarations aren't needed by any capabilities document (and can be abused)
        raise ServiceError("The service's description declares XML entities: not read, for safety")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise ServiceError(f"The service's description isn't valid XML: {e}") from e


def capabilities(url: str) -> dict:
    """The layers of a WMS / WMTS service (its GetCapabilities), or an XYZ address as it is."""
    url = url.strip()
    if is_xyz(url):
        return {"kind": "xyz", "url": url, "title": urlsplit(url).netloc, "layers": []}
    lower = url.lower()
    asked = dict((k.lower(), v) for k, v in parse_qsl(urlsplit(url).query))
    tries = []
    if asked.get("request", "").lower() == "getcapabilities" or lower.endswith(".xml"):
        tries.append(url)
    else:
        wmts = _with_query(url, SERVICE="WMTS", REQUEST="GetCapabilities")
        wms = _with_query(url, SERVICE="WMS", REQUEST="GetCapabilities")
        tries += [wmts, wms] if "wmts" in lower else [wms, wmts]
    last = None
    for t in tries:
        try:
            root = parse_xml(_fetch(t))
        except ServiceError as e:
            last = e
            continue
        name = _local(root.tag)
        if name in ("WMS_Capabilities", "WMT_MS_Capabilities"):
            return parse_wms(root, url)
        if name == "Capabilities" and root.tag.startswith("{" + W):
            return parse_wmts(root, url)
        if name in ("ServiceExceptionReport", "ExceptionReport"):
            last = ServiceError("The service answered with an error: " + " ".join(root.itertext()).strip()[:300])
            continue
        last = ServiceError(f"Not a WMS or WMTS service (it answered with <{name}>)")
    raise last or ServiceError("Not a WMS or WMTS service")


def time_info(default: str, values: list[str]) -> dict:
    """A time dimension, short: its default, first and last dates (values are dates or start/end/period ranges)."""
    parts = [x for v in values for x in v.split("/")[:2] if re.match(r"\d{4}-\d\d", x)]
    day = lambda x: x[:10]   # noqa: E731
    return {"default": day(default) if re.match(r"\d{4}-\d\d-\d\d", default or "") else (default or ""),
            "start": day(min(parts)) if parts else "", "end": day(max(parts)) if parts else "", "ranges": len(values)}


# ------------------------------------------------------------------ WMS

def parse_wms(root, url: str) -> dict:
    version = root.get("version", "1.3.0")
    cap = _kid(root, "Capability")
    if cap is None:
        raise ServiceError("The WMS description has no Capability section")
    getmap = _kid(_kid(cap, "Request"), "GetMap")
    formats = [f.text.strip() for f in _kids(getmap, "Format") if f.text] if getmap is not None else []
    href = None
    if getmap is not None:
        for el in getmap.iter():
            if _local(el.tag) == "OnlineResource" and el.get(XLINK):
                href = el.get(XLINK)
                break
    endpoint = base_url(href) if href and urlsplit(href).scheme in ("http", "https") else base_url(url)
    svc = _kid(root, "Service")
    out = []

    def walk(el, crs: set, inherited_time, attribution):
        crs = set(crs)
        for c in _kids(el, "CRS") + _kids(el, "SRS"):   # WMS 1.1 may list several CRSs in one SRS element
            crs |= set((c.text or "").upper().split())
        time = inherited_time
        for d in _kids(el, "Dimension") + _kids(el, "Extent"):
            if (d.get("name") or "").lower() == "time":
                time = time_info(d.get("default") or "", re.split(r"\s*,\s*", (d.text or "").strip()))
        attr = _text(_kid(el, "Attribution"), "Title") or attribution
        name = _text(el, "Name")
        if name and len(out) < MAX_LAYERS:
            legend = None
            for st in _kids(el, "Style"):
                lg = _kid(st, "LegendURL")
                res = _kid(lg, "OnlineResource")
                if res is not None and res.get(XLINK):
                    legend = res.get(XLINK)
                    break
            out.append({"name": name, "title": _text(el, "Title") or name, "abstract": _text(el, "Abstract")[:600],
                        "bbox": _wms_bbox(el), "legend": legend, "time": time, "attribution": attr,
                        "mercator": any(c.split(":")[-1] in WEB_MERCATOR for c in crs),
                        "queryable": el.get("queryable") == "1"})
        for child in _kids(el, "Layer"):
            walk(child, crs, time, attr)

    for top in _kids(cap, "Layer"):
        walk(top, set(), None, "")
    if not out:
        raise ServiceError("The WMS service has no layers that can be drawn")
    fmt = next((f for f in ("image/png", "image/png8", "image/jpeg") if f in formats), formats[0] if formats else "image/png")
    return {"kind": "wms", "url": endpoint, "version": "1.3.0" if version.startswith("1.3") else "1.1.1",
            "title": _title(svc, url), "format": fmt, "formats": formats[:20],
            "layers": out, "truncated": len(out) >= MAX_LAYERS}


def _wms_bbox(el) -> list[float] | None:
    b = _kid(el, "EX_GeographicBoundingBox")
    if b is not None:
        try:
            v = [float(_text(b, k)) for k in ("westBoundLongitude", "southBoundLatitude", "eastBoundLongitude", "northBoundLatitude")]
            return v if v[0] < v[2] and v[1] < v[3] else None
        except ValueError:
            return None
    b = _kid(el, "LatLonBoundingBox")
    if b is not None:
        try:
            v = [float(b.get(k)) for k in ("minx", "miny", "maxx", "maxy")]
            return v if v[0] < v[2] and v[1] < v[3] else None
        except (TypeError, ValueError):
            return None
    return None


# ------------------------------------------------------------------ WMTS

def mercator_zooms(tms) -> dict[int, str] | None:
    """{zoom: tile matrix identifier} when the tile matrix set is Web Mercator's own grid (256-pixel tiles), else None."""
    crs = _text(tms, "SupportedCRS")
    if not any(re.search(rf"(^|\D){c}$", crs) for c in WEB_MERCATOR):
        return None
    zooms = {}
    for tm in _kids(tms, "TileMatrix"):
        try:
            scale = float(_text(tm, "ScaleDenominator"))
            x, y = (float(v) for v in _text(tm, "TopLeftCorner").split()[:2])
            size = int(_text(tm, "TileWidth") or 256)
        except ValueError:
            return None
        if size != 256 or abs(x + HALF) > 1 or abs(y - HALF) > 1:
            continue
        z = round(math.log2(Z0_SCALE / scale))
        if z < 0 or abs(Z0_SCALE / 2**z - scale) / scale > 0.01:
            continue
        zooms[z] = _text(tm, "Identifier")
    return zooms or None


def parse_wmts(root, url: str) -> dict:
    contents = _kid(root, "Contents")
    if contents is None:
        raise ServiceError("The WMTS description has no Contents section")
    sets = {}
    for tms in _kids(contents, "TileMatrixSet"):
        z = mercator_zooms(tms)
        if z:
            sets[_text(tms, "Identifier")] = z
    gettile = None
    om = _kid(root, "OperationsMetadata")
    for op in (_kids(om, "Operation") if om is not None else []):
        if op.get("name") == "GetTile":
            for el in op.iter():
                if _local(el.tag) == "Get" and el.get(XLINK):
                    gettile = el.get(XLINK)
                    break
    svc = _kid(root, "ServiceIdentification")
    out, skipped = [], 0
    for lay in _kids(contents, "Layer"):
        ident = _text(lay, "Identifier")
        links = [_text(lk, "TileMatrixSet") for lk in _kids(lay, "TileMatrixSetLink")]
        tms = next((s for s in links if s in sets), None)
        if not ident or not tms:
            skipped += 1
            continue
        styles = _kids(lay, "Style")
        style = next((s for s in styles if s.get("isDefault") == "true"), styles[0] if styles else None)
        style_id = _text(style, "Identifier") if style is not None else "default"
        legend = None
        if style is not None:
            lg = _kid(style, "LegendURL")
            legend = lg.get(XLINK) if lg is not None else None
        formats = [f.text.strip() for f in _kids(lay, "Format") if f.text]
        fmt = next((f for f in ("image/png", "image/jpeg") if f in formats), formats[0] if formats else "image/png")
        time, dims = None, {}
        for d in _kids(lay, "Dimension"):
            did = _text(d, "Identifier")
            default = _text(d, "Default")
            if did.lower() == "time":
                time = time_info(default, [v.text.strip() for v in _kids(d, "Value") if v.text])
            elif did:
                dims[did] = default
        tiles = [r.get("template") for r in _kids(lay, "ResourceURL") if r.get("resourceType") == "tile" and r.get("template")]
        tiles = sorted(tiles, key=lambda t: ("{" + fmt.split("/")[-1] not in t and fmt.split("/")[-1] not in t[-6:],
                                             ("{time}" in t.lower()) != bool(time)))   # the format asked, with a date if it has one
        template = tiles[0] if tiles else None
        if template:
            t = template.replace("{TileMatrixSet}", tms).replace("{Style}", style_id).replace("{style}", style_id)
            for k, v in dims.items():
                t = t.replace("{" + k + "}", v)
            t = re.sub(r"\{time\}", "{time}", t, flags=re.I)
            t = t.replace("{TileMatrix}", "{z}").replace("{TileRow}", "{y}").replace("{TileCol}", "{x}")
        elif gettile:
            params = {"SERVICE": "WMTS", "REQUEST": "GetTile", "VERSION": "1.0.0", "LAYER": ident, "STYLE": style_id,
                      "TILEMATRIXSET": tms, "FORMAT": fmt, **dims}
            t = _with_query(base_url(gettile), **params) + "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}" + ("&TIME={time}" if time else "")
        else:
            skipped += 1
            continue
        zooms = sets[tms]
        plain = all(str(z) == m for z, m in zooms.items())
        bbox = None
        wb = _kid(lay, "WGS84BoundingBox")
        if wb is not None:
            try:
                lo, hi = [float(v) for v in _text(wb, "LowerCorner").split()], [float(v) for v in _text(wb, "UpperCorner").split()]
                bbox = [lo[0], lo[1], hi[0], hi[1]]
            except ValueError:
                pass
        out.append({"name": ident, "title": _text(lay, "Title") or ident, "abstract": _text(lay, "Abstract")[:600], "url": t,
                    "format": fmt, "bbox": bbox, "legend": legend, "time": time, "min_zoom": min(zooms), "max_zoom": max(zooms),
                    "matrices": None if plain else {str(z): m for z, m in zooms.items()}})
        if len(out) >= MAX_LAYERS:
            break
    if not out:
        raise ServiceError("The WMTS service has no layers in Web Mercator (GoogleMapsCompatible) tiles, which the map needs"
                           + (": try its WMS address instead" if skipped else ""))
    return {"kind": "wmts", "url": base_url(url), "title": _title(svc, url), "layers": out,
            "skipped": skipped, "truncated": len(out) >= MAX_LAYERS}
