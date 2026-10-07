"""The Assistant (Analysis ▸ Tools ▸ Assistant): say what you want done; it plans it as a workflow of the app's own
tools, you check the plan, then the app runs it like any workflow (webapp/workflows.py).

Free first: a local model through Ollama (runs on this computer, offline); or an online model through any
OpenAI-compatible API with a free tier (Hugging Face, Groq, OpenRouter, Gemini, Mistral, Cerebras, or a custom address
such as LM Studio); or Claude. Keys are the user's own, kept in the keychain (see webapp/credentials.py).

The model only writes a plan: the tools it may use come from the app's own API description (their settings and types),
every step is checked against it, and a plan with problems goes back to the model once or twice to be fixed. Nothing
runs until the user presses Run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from . import assistant_data as data
from . import workflows
from . import workspace as ws

OLLAMA_URL = "http://127.0.0.1:11434"
RECOMMENDED_LOCAL = "qwen2.5:7b"   # good at following a JSON schema; about 4.7 GB, runs on 16 GB of memory
CLAUDE_MODEL = "claude-opus-5-5"
PROVIDERS = ("ollama", "api", "claude")

# online models with a free tier, all through the OpenAI-compatible chat API; the model can be changed (List models)
API_PRESETS = {
    "huggingface": {"title": "Hugging Face", "base": "https://router.huggingface.co/v1", "model": "openai/gpt-oss-120b",
                    "signup": "https://huggingface.co/settings/tokens",
                    "about": "Open models (Llama, Qwen, DeepSeek, gpt-oss…) through Hugging Face Inference Providers; a free token "
                             "with a small monthly allowance"},
    "groq": {"title": "Groq", "base": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
             "signup": "https://console.groq.com/keys", "about": "Very fast open models; free tier with daily limits"},
    "openrouter": {"title": "OpenRouter", "base": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3.3-70b-instruct:free",
                   "signup": "https://openrouter.ai/keys", "about": "Many models; those ending in :free cost nothing (daily limits)"},
    "gemini": {"title": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash",
               "signup": "https://aistudio.google.com/apikey", "about": "Gemini Flash models; free tier with daily limits"},
    "mistral": {"title": "Mistral", "base": "https://api.mistral.ai/v1", "model": "mistral-small-latest",
                "signup": "https://console.mistral.ai/api-keys", "about": "Mistral's models; free Experiment plan with limits"},
    "cerebras": {"title": "Cerebras", "base": "https://api.cerebras.ai/v1", "model": "gpt-oss-120b",
                 "signup": "https://cloud.cerebras.ai/", "about": "Very fast open models; free tier with daily limits"},
    "custom": {"title": "Custom address", "base": "http://127.0.0.1:1234/v1", "model": "",
               "signup": "", "about": "Any OpenAI-compatible server: LM Studio, llama.cpp, vLLM on this computer, or another service"},
}

# the tools the assistant may plan with: what each is for (the settings come from the API description)
CATALOG = {
    "/api/jobs": "Find imagery & download (a background job). kind='composite': a cloud-free Sentinel-2 mosaic of `aoi` "
                 "between `start` and `end` (bands, indices=true adds NDVI etc., res in m, max_cloud %). kind='scene': the one "
                 "best scene on `date`. kind='labels': a land-cover map, product='worldcover' (year 2020/2021) or 'esri' "
                 "(2017-2023). Output: GeoTIFF(s).",
    "/api/analyze/export": "Spectral indices of a multiband raster as one GeoTIFF (indices e.g. ['NDVI','NDWI','SAVI','EVI']). "
                           "band_map maps band names to band numbers: use the layer's band_map and scale from the data list. For a "
                           "composite made by an earlier /api/jobs step with the default bands: band_map {\"B02\":1,\"B03\":2,"
                           "\"B04\":3,\"B05\":4,\"B06\":5,\"B07\":6,\"B08\":7,\"B8A\":8,\"B11\":9,\"B12\":10}, scale 0.0001 "
                           "(but /api/jobs with indices=true already writes NDVI and other indices). Optional `clip` area. Output: .tif",
    "/api/tables/from-raster": "Raster → table: the pixels of a raster as a CSV (one row per pixel, x/y, lon/lat), optional "
                               "ground_truth {type:'raster', path, band:1} for a label column; factor>1 takes every n-th pixel. Output: .csv",
    "/api/stack": "Stack bands of several rasters onto one grid. items: [{path, bands:[...]}]; ref: the path whose grid is used. Output: .tif",
    "/api/pca/run": "PCA / dimensionality reduction of a multiband raster (bands: band numbers, method 'pca'). Output: .tif",
    "/api/rasterml/run": "Classify a raster with classical machine learning (model 'rf' random forest, 'svm', 'ml' maximum "
                         "likelihood, 'knn'…) from ground_truth {type:'raster', path, band:1} or {type:'vector', geojson, field}. Output: .tif map",
    "/api/unsup/cluster": "Cluster the rows of a table (k-means etc.) on its numeric `features` (column names). Output: table",
    "/api/interp/run": "Interpolate a surface (method 'idw', 'kriging', 'tps', 'nn', 'tin') from points (a GeoJSON FeatureCollection) "
                       "of a numeric `field`. Output: .tif",
    "/api/layers/export": "Export a raster layer as format 'tif' (values), 'png' or 'shp' (classes as polygons), optional clip. Output: file",
    "/api/emb/fetch": "Download satellite embeddings (source 'aef' AlphaEarth 64 bands, or 'tessera') for an area (`clip`) and year. Output: .tif",
    "/api/library/fetch": "Download a file from the data library (Hugging Face): repo and path inside it. Output: file",
    "/api/vector/buffer": "Buffer: a zone of `distance` metres around each shape of a vector layer (negative shrinks polygons); "
                          "dissolve=true merges them into one. `layer`: the path of a vector layer from the data list. Output: .geojson",
    "/api/vector/query": "Select by attribute: the features of `layer` whose attributes meet `where`, a condition on its fields, e.g. "
                         "crop == \"rice\" and area_ha > 2 · name in (\"A\", \"B\") · contains(name, \"farm\"). Output: .geojson",
    "/api/vector/overlay": "Overlay two vector layers `a` and `b` (paths): how = 'intersection' (where both are), 'union' (every piece of "
                           "both), 'difference' (a without b), 'symmetric_difference', 'clip' (a cut to b). Output: .geojson",
    "/api/vector/dissolve": "Dissolve: merge the shapes of `layer`, all into one, or one per value of `field`. Output: .geojson",
    "/api/vector/zonal": "Zonal statistics: each polygon of `layer` with the values of the raster `raster` (a path) inside it: stats "
                         "['mean','min','max','std','median','sum','count'], or categorical=true for a class raster (land cover): "
                         "% of each class and the majority. Output: .geojson (the polygons with the new fields)",
    "/api/vector/select-location": "Select by location: features of `a` that intersect / are within / contain / are disjoint from / are "
                                   "within `distance` metres (predicate 'within_distance') of `b`. Output: .geojson",
    "/api/vector/spatial-join": "Spatial join: features of `a` with the attributes of the feature of `b` they overlap most "
                                "(how 'intersects'), are inside ('within') or are nearest to ('nearest', adds join_dist_m). Output: .geojson",
    "/api/vector/geometry": "Calculate geometry: adds area_m2, area_ha, perimeter_m (polygons) or length_m (lines) and the centroid "
                            "lon / lat to `layer`. Output: .geojson",
    "/api/vector/count-points": "Count the `points` (a layer) inside each of the `polygons`, and the sum of a points' `sum_field`. Output: .geojson",
    "/api/vector/join-table": "Join a table (`table`: a path of a CSV / Excel / Parquet) to `layer` where the layer's `layer_field` "
                              "equals the table's `table_field`. Output: .geojson",
    "/api/raster/terrain": "From a DEM (`dem`: a path): products ['slope', 'aspect', 'hillshade'] (slope in degrees, aspect in degrees "
                           "from north); leave azimuth, altitude and z_factor out (z_factor stays 1: pixel sizes are "
                           "converted to metres automatically, also for DEMs in degrees). Output: .tif files",
    "/api/raster/contours": "Contour lines of a DEM every `interval` metres. Output: .geojson lines with their `value`",
    "/api/raster/reclassify": "Ranges of values of `raster` become classes: rules [{min, max, value (1-255), label}] (min inclusive, max "
                              "exclusive; null = no limit). Output: .tif with class names",
    "/api/raster/change": "Change between two dates of the same thing: `before` and `after` (paths): the difference and % change, or "
                          "categorical=true for class maps (land cover): from→to map and a table of the area of every change. Output: .tif (+ .csv)",
    "/api/raster/clip": "Cut `raster` to polygons `area` (a GeoJSON area; invert=true keeps the outside). Output: .tif",
    "/api/convert/raster-to-polygon": "Class areas of `raster` (whole-number classes; reclassify continuous data first) as polygons with "
                                      "value, class, area_ha: values [only these], min_area (m², merges smaller patches), simplify (m), "
                                      "dissolve=true (one per value). Output: .geojson",
    "/api/convert/raster-to-polyline": "Lines from `raster`: mode 'boundaries' (edges between classes) or 'centrelines' (thin shapes such "
                                       "as roads / rivers: values = their classes), simplify (m), min_length (m). Output: .geojson",
    "/api/convert/raster-to-point": "A point per pixel of `raster` (every `step`-th) with each band's value. Output: .geojson",
    "/api/convert/rasterize": "Vector `layer` → raster: mode 'value' (`field`: numbers, or text → classes), 'presence' or 'count'; "
                              "`res` metres, or `like` (a raster path) for the same grid; all_touched. Output: .tif",
    "/api/convert/features": "Change geometry type of `layer`: op = 'polygons_to_lines', 'lines_to_polygons', 'vertices_to_points', "
                             "'points_to_lines' (order_by, group_by, close), 'points_along_lines' (distance m), 'split_lines', "
                             "'bounding_boxes' (whole). Output: .geojson",
    "/api/assess/area-stats": "Hectares, km² and % of each class of a class map `raster` (land cover, a classified map), optionally "
                              "inside `area`. Output: .csv table",
    "/api/assess/sample": "Stratified random points for accuracy assessment of a classified `raster`: per_class points per class (or "
                          "`total`). Each has map_class and an empty `reference` for the user to label. Output: .geojson",
    "/api/assess/accuracy": "Accuracy of a classified `raster` against labelled `points` (ref_field = the true class): confusion "
                            "matrix, overall / user's / producer's accuracy, kappa, area estimates with 95 % CI. Output: .html report + .csv",
    "/api/raster/calc": "Raster calculator: `variables` {\"A\": {\"path\": …, \"band\": n}, \"B\": …} and an `expression` such as "
                        "\"(B - A) / (B + A)\", \"where(A > 0.3, 1, 0)\", \"(A - B) > 0.1\" (and / or / not, abs, sqrt, log, min, max). Output: .tif",
    "/api/timeseries": "Index time series of a point or field `geometry` (GeoJSON Point / Polygon) from the free Sentinel-2 catalogue: "
                       "`start`, `end` (YYYY-MM-DD), index NDVI / EVI / NDWI / NDMI / NDRE / SAVI. Output: .csv (date, mean, std…)",
    "/api/raster/resample": "A new pixel size `res` (metres in UTM) or factor `scale` (2 = pixels twice as small) and / or `crs` "
                            "('EPSG:32643') for `raster`; `method` nearest, bilinear, cubic (= bicubic), cubic_spline, lanczos, average, "
                            "mode, med, min, max (default: nearest / mode for class maps, bilinear / average for values). Output: .tif",
    "/api/raster/enhance": "Enhance `raster` for viewing, computer vision or embeddings: steps [{op, …}] in order, op = stretch {low, "
                           "high}, equalize, clahe {tiles, clip}, gamma {gamma}, median {size}, gaussian {sigma}, sharpen {sigma, amount}, "
                           "sobel, laplacian, focal_mean / focal_std / focal_min / focal_max {size}, majority {size} (clean a class map); "
                           "upscale 2 or 4 with upscale_method (cubic, lanczos). Output: .tif",
    "/api/vector/geom-op": "Geometry helpers, op = 'centroids' (inside=true: a point surely inside), 'convex_hull' (whole=true: one for "
                           "the layer), 'simplify' (tolerance m), 'explode' (multipart to single), 'merge' (`layers`: two or more paths), "
                           "'fishnet' (a grid of `cell` m over `layer`), 'random_points' (`count` points inside the polygons of `layer`, "
                           "per_feature=true: in each). Output: .geojson",
}


# ------------------------------------------------------------------ settings
def _settings_file() -> Path:
    return ws.CONFIG_DIR / "assistant.json"


def settings() -> dict:
    try:
        s = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    preset = s.get("api_preset") if s.get("api_preset") in API_PRESETS else "huggingface"
    return {"provider": s.get("provider") if s.get("provider") in PROVIDERS else "ollama",
            "model": s.get("model") or "", "ollama_url": s.get("ollama_url") or OLLAMA_URL,
            "api_preset": preset, "api_model": s.get("api_model") or API_PRESETS[preset]["model"],
            "api_base": s.get("api_base") or API_PRESETS["custom"]["base"]}


def save_settings(provider: str, model: str = "", ollama_url: str = "", api_preset: str = "", api_model: str = "", api_base: str = "") -> dict:
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider")
    s = {**settings(), "provider": provider}
    if provider == "ollama":
        s["model"] = model.strip()[:100]
    if ollama_url:
        if not ollama_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("The Ollama address must be on this computer (http://127.0.0.1:11434)")
        s["ollama_url"] = ollama_url.rstrip("/")
    if provider == "api":
        if api_preset not in API_PRESETS:
            raise ValueError("Choose an online service")
        if api_preset != s.get("api_preset"):
            s["api_model"] = ""   # another service: its own default model, unless one is given
        s["api_preset"] = api_preset
        if api_model.strip():
            s["api_model"] = api_model.strip()[:200]
        if api_preset == "custom":
            base = (api_base or s.get("api_base") or "").strip().rstrip("/")
            if not base.startswith(("http://", "https://")):
                raise ValueError("The custom address must start with http:// or https:// (e.g. http://127.0.0.1:1234/v1)")
            if not s.get("api_model"):
                raise ValueError("Give the model's name for the custom address (List models shows them)")
            s["api_base"] = base
    ws.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _settings_file().write_text(json.dumps(s, indent=1), encoding="utf-8")
    return s


def _claude_key() -> str:
    from . import credentials
    return (credentials.get_all("anthropic") or {}).get("api_key") or ""


def _api_key(preset: str) -> str:
    from . import credentials
    return credentials.get("llm_api", preset) or ""


def _api_target(s: dict | None = None) -> tuple[str, str, str]:
    """The online service's address, model and key."""
    s = s or settings()
    p = s["api_preset"]
    base = s["api_base"] if p == "custom" else API_PRESETS[p]["base"]
    return base, s["api_model"], _api_key(p)


def _api_headers(key: str) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "LULC-Fetch"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def api_models(preset: str, base: str = "") -> list[str]:
    """The models an online service offers (its /models list), so the user can pick one."""
    if preset not in API_PRESETS:
        raise ValueError("Unknown service")
    base = (base.strip().rstrip("/") if preset == "custom" and base else API_PRESETS[preset]["base"] if preset != "custom" else settings()["api_base"])
    r = requests.get(f"{base}/models", headers=_api_headers(_api_key(preset)), timeout=20)
    if r.status_code in (401, 403):
        raise ValueError(f"{API_PRESETS[preset]['title']} refused the key → check it in Credentials (Online models)")
    r.raise_for_status()
    js = r.json()
    items = js.get("data", js) if isinstance(js, dict) else js
    names = sorted({str(m.get("id") or m.get("name") or "").removeprefix("models/") for m in items if isinstance(m, dict)} - {""})
    if preset == "openrouter":   # the free ones first
        names.sort(key=lambda n: (not n.endswith(":free"), n))
    return names[:400]


def status() -> dict:
    """What can plan now: Ollama running and its models; whether a Claude key is set."""
    s = settings()
    ollama = {"url": s["ollama_url"], "running": False, "models": []}
    try:
        r = requests.get(f"{s['ollama_url']}/api/tags", timeout=2)
        r.raise_for_status()
        ollama.update(running=True, models=sorted(m["name"] for m in r.json().get("models", [])))
    except (requests.RequestException, ValueError):
        pass
    try:
        import anthropic  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    claude = {"key_set": bool(_claude_key()), "sdk": sdk, "model": CLAUDE_MODEL}
    base, api_model, key = _api_target(s)
    api = {"preset": s["api_preset"], "model": api_model, "base": base, "key_set": bool(key),
           "presets": {k: {kk: v[kk] for kk in ("title", "model", "signup", "about")} | {"key_set": bool(_api_key(k))} for k, v in API_PRESETS.items()}}
    local_base = base.startswith(("http://127.0.0.1", "http://localhost"))
    model = s["model"] or (RECOMMENDED_LOCAL if RECOMMENDED_LOCAL in ollama["models"] else (ollama["models"][0] if ollama["models"] else RECOMMENDED_LOCAL))
    ready = ((s["provider"] == "ollama" and ollama["running"] and model in ollama["models"]) or (s["provider"] == "claude" and claude["key_set"] and sdk)
             or (s["provider"] == "api" and bool(api_model) and (bool(key) or (s["api_preset"] == "custom" and local_base))))
    shown = {"ollama": model, "claude": CLAUDE_MODEL, "api": api_model}[s["provider"]]
    return {"provider": s["provider"], "model": shown, "ready": ready, "ollama": ollama, "claude": claude, "api": api, "recommended": RECOMMENDED_LOCAL}


def pull(model: str, job=None) -> dict:
    """Download a model into Ollama (a background job: its progress)."""
    from lulc_fetch import progress
    s = settings()
    with requests.post(f"{s['ollama_url']}/api/pull", json={"name": model, "stream": True}, stream=True, timeout=(5, 600)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(d["error"])
            total, done = d.get("total") or 0, d.get("completed") or 0
            progress.update(done / total if total else None, f"{d.get('status', '')}{f' · {done / 1e9:.2f} of {total / 1e9:.2f} GB' if total else ''}")
    return {"model": model}


# ------------------------------------------------------------------ the tools, from the app's API description
def _openapi() -> dict:
    from .server import app
    return app.openapi()


def _schema_of(spec: dict, endpoint: str) -> dict | None:
    try:
        ref = spec["paths"][endpoint]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    except KeyError:
        return None
    return spec["components"]["schemas"][ref.split("/")[-1]]


def _type(spec: dict, s: dict) -> str:
    if "$ref" in s:
        sub = spec["components"]["schemas"][s["$ref"].split("/")[-1]]
        return "{" + ", ".join(f"{k}: {_type(spec, v)}" for k, v in sub.get("properties", {}).items()) + "}"
    if "anyOf" in s:
        return " | ".join(_type(spec, x) for x in s["anyOf"] if x.get("type") != "null")
    if s.get("type") == "array":
        return f"[{_type(spec, s.get('items', {}))}]"
    return s.get("type", "any")


# the handbook (LLM-Find's per-source handbooks, GISclaw's domain knowledge): how the tools fit together
RULES = """How to choose tools:
- Satellite imagery is downloaded with /api/jobs: kind "composite" for a cloud-free mosaic of a period (start, end),
  kind "scene" for one date. Land-cover maps (ESA WorldCover 2020/2021, Esri 2017-2023) are downloaded with /api/jobs
  kind "labels" and product "worldcover" or "esri" and a year. Not with the library and not with embeddings.
- One download per /api/jobs step: two things to download = two steps.
- Tools that work on tables (/api/unsup/cluster: cluster, group) need a table. When the data is a raster, first make a
  table of its pixels with /api/tables/from-raster, then use {"$step": n, "ext": ".csv", "nth": 0} as the table.
  "Group / cluster into N" always needs that /api/unsup/cluster step at the end: a table alone isn't the answer.
- Indices (NDVI, NDWI, EVI, SAVI…) of an image: /api/analyze/export. A map of classes from labelled data:
  /api/rasterml/run. Embeddings (AlphaEarth, TESSERA): /api/emb/fetch. Files in the data library: /api/library/fetch.
- An area of the user's (a polygon layer, the map view) goes into aoi / clip as an input of type "area".
- Vector layers (points, lines, polygons) are used with their path from the data list (an input of type "file"):
  a zone around them → /api/vector/buffer; features matching a condition on their fields → /api/vector/query (use the
  field names and values seen in the data); where two layers overlap, or their union / difference → /api/vector/overlay;
  merging shapes → /api/vector/dissolve. A vector file made by an earlier step: {"$step": n, "ext": ".geojson", "nth": 0}.
- A raster's values per polygon (mean NDVI of each field, % land cover per district) → /api/vector/zonal (for an index,
  make it first with /api/analyze/export and use its .tif). Features near / inside / touching another layer →
  /api/vector/select-location. Attributes of the layer they fall in or are nearest to → /api/vector/spatial-join.
  Area in hectares, length, perimeter → /api/vector/geometry. How many points per polygon → /api/vector/count-points.
  A table's columns added to a layer by a shared key → /api/vector/join-table.
- Slope, aspect or a shaded relief of a DEM → ONE /api/raster/terrain step with all of them in `products`; contour lines → /api/raster/contours; ranges of
  values to classes (e.g. NDVI → low / medium / high) → /api/raster/reclassify; what changed between two dates →
  /api/raster/change (categorical for land-cover maps); a raster cut to an area → /api/raster/clip.
- A different pixel size or coordinate system → /api/raster/resample (cubic or lanczos when enlarging imagery,
  average when shrinking values, nearest / mode for class maps). Better contrast, denoising, sharpening, edges,
  texture or a speckle-free class map → /api/raster/enhance. Downloads, Stack, Make training data, embeddings and
  change detection also take an optional `resampling` method.
- How many hectares (or km², %) of each class of a whole class map → /api/assess/area-stats, not /api/vector/zonal
  (zonal is per polygon of a layer). How accurate a classified map is → /api/assess/sample (points to label), then
  /api/assess/accuracy once labelled. Map algebra over several rasters (differences, masks, thresholds, custom
  indices) → /api/raster/calc.
- How an index (NDVI…) changes or develops over time (a season, a year, "over 2024", month by month) at a point or
  field → ONE /api/timeseries step with the field's area as `geometry` and start / end dates. Don't download images
  for this: /api/timeseries reads the catalogue itself.
- A class raster as polygons → /api/convert/raster-to-polygon (continuous data: /api/raster/reclassify first); its
  class edges or the centrelines of roads / rivers → /api/convert/raster-to-polyline; pixels as points →
  /api/convert/raster-to-point; polygons or points into a raster (labels on an image's grid: like = that image) →
  /api/convert/rasterize; polygons ↔ lines, points → a track, points every n m, boxes → /api/convert/features.
- Centroids, convex hulls, simplifying, merging layers, splitting multipart shapes, a grid of cells, random sample
  points in polygons → /api/vector/geom-op with its op."""


def catalog_text() -> str:
    spec, out = _openapi(), []
    for ep, what in CATALOG.items():
        sch = _schema_of(spec, ep)
        if not sch:
            continue
        req = set(sch.get("required", []))
        fields = []
        for k, v in sch.get("properties", {}).items():
            d = "" if "default" not in v or v["default"] in (None, [], {}) else f" = {json.dumps(v['default'])}"
            fields.append(f"{k}{'' if k in req else '?'}: {_type(spec, v)}{d}")
        out.append(f"POST {ep}\n  {what}\n  settings: {'; '.join(fields)}")
    return "\n".join(out)


# what a tool needs beyond its API description (settings that are optional there but needed for the job asked)
NEEDS = {
    "/api/jobs": lambda b: (["aoi"] + {"composite": ["start", "end"], "scene": ["date"], "labels": ["product", "year"]}.get(b.get("kind"), [])
                            if b.get("kind") in ("composite", "scene", "labels") else ["aoi", "kind (composite, scene or labels)"]),
    "/api/emb/fetch": lambda b: ["clip", "year"],
}


def _check_body(spec: dict, endpoint: str, body: dict, input_types: dict | None = None) -> list[str]:
    """Required settings present, no unknown ones, the plain types right; an input marker stands for its input's value
    (an area where a text path belongs, or a path where an area belongs, is a problem)."""
    sch = _schema_of(spec, endpoint)
    if sch is None:
        return [f"{endpoint} isn't one of the tools"]
    probs, props = [], sch.get("properties", {})
    for k in sch.get("required", []) + (NEEDS[endpoint](body) if endpoint in NEEDS else []):
        if k.split(" ")[0] not in body:
            probs.append(f"{endpoint}: the setting “{k}” is missing")
    for k, v in body.items():
        if k not in props:
            probs.append(f"{endpoint}: there is no setting “{k}” (settings: {', '.join(props)})")
            continue
        kinds = {x.get("type") for x in props[k].get("anyOf", []) if x.get("type") != "null"}
        t = props[k].get("type") or (next(iter(kinds)) if len(kinds) == 1 else None)   # text or object (a layer): either
        if isinstance(v, dict) and "$step" in v:
            if t not in (None, "string"):
                probs.append(f"{endpoint}: “{k}” takes a {t}, not a file made by a step")
            continue
        if isinstance(v, dict) and "$in" in v:
            it = (input_types or {}).get(v["$in"])
            if it == "area" and t == "string":
                probs.append(f"{endpoint}: “{k}” takes a text value (e.g. a file path), not the area {v['$in']}")
            elif it == "file" and t == "object":
                probs.append(f"{endpoint}: “{k}” takes an object (e.g. an area), not the file {v['$in']}")
            continue
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool), "number": isinstance(v, (int, float)) and not isinstance(v, bool),
              "boolean": isinstance(v, bool), "array": isinstance(v, list), "object": isinstance(v, dict)}.get(t, True)
        if not ok and not (v is None and "anyOf" in props[k]):
            probs.append(f"{endpoint}: “{k}” must be a {t}, not {json.dumps(v)[:60]}")
    return probs


# ------------------------------------------------------------------ the plan
# Claude's strict JSON output needs every object spelled out, so a step's settings (different for every tool) come as
# JSON text (body_json); local models get the settings as a real object (body), which small models write far better.
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["plan", "questions", "remember", "workflow"],
    "properties": {
        "plan": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}},
        "remember": {"type": "array", "items": {"type": "string"}},
        "workflow": {"type": "object", "additionalProperties": False, "required": ["name", "inputs", "steps"], "properties": {
            "name": {"type": "string"},
            "inputs": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "label", "type", "default_json"],
                       "properties": {"id": {"type": "string"}, "label": {"type": "string"}, "type": {"type": "string", "enum": ["file", "area", "value"]},
                                      "default_json": {"type": "string"}}}},
            "steps": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["title", "endpoint", "body_json"],
                      "properties": {"title": {"type": "string"}, "endpoint": {"type": "string", "enum": list(CATALOG)}, "body_json": {"type": "string"}}}},
        }},
    },
}


def _local_schema() -> dict:
    sch = json.loads(json.dumps(SCHEMA))
    wf = sch["properties"]["workflow"]["properties"]
    wf["inputs"]["items"]["properties"].pop("default_json")
    wf["inputs"]["items"]["properties"]["default"] = {"type": ["string", "number", "object", "array"]}
    wf["inputs"]["items"]["required"] = ["id", "label", "type", "default"]
    wf["steps"]["items"]["properties"].pop("body_json")
    wf["steps"]["items"]["properties"]["body"] = {"type": "object"}
    wf["steps"]["items"]["required"] = ["title", "endpoint", "body"]
    wf["steps"]["items"].pop("additionalProperties")
    return sch


EXAMPLE_REQUEST = "NDVI of my image Scene X inside the field layer Plots, as a table"
EXAMPLE_WORKFLOW = {
    "name": "NDVI table of the plots",
    "inputs": [{"id": "in1", "label": "Image", "type": "file", "default": "uploads/scene_x.tif"},
               {"id": "in2", "label": "Plots", "type": "area", "default": {"type": "Polygon", "coordinates": [[[77.40, 12.99], [77.42, 12.99], [77.42, 13.0], [77.40, 12.99]]]}}],
    "steps": [{"title": "NDVI", "endpoint": "/api/analyze/export",
               "body": {"path": {"$in": "in1"}, "band_map": {"B02": 1, "B03": 2, "B04": 3, "B08": 4}, "scale": 0.0001, "indices": ["NDVI"], "clip": {"$in": "in2"}}},
              {"title": "NDVI as a table", "endpoint": "/api/tables/from-raster",
               "body": {"path": {"$step": 0, "ext": ".tif", "nth": 0}, "clip": {"$in": "in2"}, "name": "ndvi_plots"}}]}


def _example(text_fields: bool) -> str:
    wf = json.loads(json.dumps(EXAMPLE_WORKFLOW))
    if text_fields:
        for i in wf["inputs"]:
            i["default_json"] = json.dumps(i.pop("default"))
        for st in wf["steps"]:
            st["body_json"] = json.dumps(st.pop("body"))
    return json.dumps({"plan": "Computes NDVI of Scene X inside the plots, then turns it into a table (one row per pixel).", "questions": [], "remember": [], "workflow": wf})


def _memory_text(request: str, context: dict) -> str:
    """What the Assistant knows besides the request: the data looked at, the user's notes, known pitfalls, skills."""
    paths = [x.get("path") for k in ("layers", "tables") for x in context.get(k) or [] if isinstance(x, dict) and x.get("path")]
    parts = []
    details = data.describe_many(paths)
    if details:
        parts.append("The user's files, looked at (bands and value ranges, columns and sample rows):\n" + json.dumps(details, ensure_ascii=False)[:9000])
    notes = data.notes().strip()
    if notes:
        parts.append("Your notes (things the user told you to remember; follow them):\n" + notes[:3000])
    pits = data.pitfalls(list(CATALOG))
    if pits:
        parts.append("Known pitfalls (errors of these tools in earlier runs; avoid them):\n" + "\n".join(
            f"- {r['endpoint']}: {r['error']}" + (f" → fixed by: {r['fix']}" if r.get("fix") else "") for r in pits[:12]))
    sk = data.skills(request)
    if sk:
        parts.append("Workflows the user saved before that look related (skills: reuse their steps and settings when they fit):\n"
                     + "\n".join(json.dumps(x, ensure_ascii=False)[:2500] for x in sk))
    return "\n\n".join(parts)


def system_prompt(context: dict, text_fields: bool = True, memory: str = "") -> str:
    dflt, body = ("default_json (JSON text)", "body_json (JSON text)") if text_fields else ("default", "body (an object)")
    return f"""You plan GIS work for LULC Fetch, a desktop app for satellite imagery, land cover and maps. The user says what they
want; you plan it as a workflow made only of the app's tools below. The app shows your plan to the user, who checks it
and presses Run; you never run anything yourself.

Answer with the JSON object of the given schema, nothing else:
- plan: 1 to 4 short sentences, in the user's language, saying what the workflow will do and what comes out.
- questions: only when something essential is missing that the user's data below doesn't answer (which area, which
  dates, which layer). Then keep the workflow empty (no steps). Otherwise an empty list.
- remember: only when the user asks you to remember something for later (e.g. "my farm is the Fields layer",
  "always use a 20% cloud limit"): each as one short sentence. Otherwise an empty list.
- workflow.name: a short name.
- workflow.inputs: the data the workflow works on, so it can be run again on other data. Each: id ("in1", "in2"…),
  label, type ("file": the path of a raster, table or vector FROM THE DATA LIST; "area": a GeoJSON Polygon or
  MultiPolygon; "value": a year, date or number), {dflt}: its value (a path from the data list, an area's geometry
  from the data list or the map view, a number, a date). Never invent a file: a file that doesn't exist yet is made by
  a step.
- workflow.steps: in order. Each: title, endpoint (one of the tools), {body}: the tool's settings. In the settings
  write {{"$in": "in1"}} where an input's value goes, and {{"$step": 0, "ext": ".tif", "nth": 0}} for a file that an
  earlier step made (0 = the first step; ext: the kind of file it made). Give only settings the tool lists, with the
  right types; leave out those that should keep their default.
- Dates are YYYY-MM-DD. Today is {time.strftime('%Y-%m-%d')}.

An example. Request: “{EXAMPLE_REQUEST}”. Answer:
{_example(text_fields)}

The app's tools:
{catalog_text()}

{RULES}

The user's data (the open map's Contents and view):
{json.dumps(context, ensure_ascii=False, default=str)[:12000]}

{memory}"""


def _ask_ollama(model: str, system: str, messages: list[dict]) -> str:
    s = settings()
    r = requests.post(f"{s['ollama_url']}/api/chat", timeout=(5, 600), json={
        "model": model, "stream": False, "format": _local_schema(),
        "messages": [{"role": "system", "content": system}, *messages],
        "options": {"temperature": 0.1, "num_ctx": 16384},
    })
    if r.status_code == 404:
        raise ValueError(f"The local model “{model}” isn't downloaded → download it in the Assistant's settings")
    r.raise_for_status()
    return r.json()["message"]["content"]


def _ask_claude(system: str, messages: list[dict]) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=_claude_key(), max_retries=2, timeout=300)
    kw = dict(model=CLAUDE_MODEL, max_tokens=16000, system=system, messages=messages,
              output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
              betas=["server-side-fallback-2026-07-01"])
    try:   # a declined request is answered by Anthropic's recommended fallback model instead of failing
        resp = client.beta.messages.create(**kw, fallbacks="default")
    except TypeError:   # an SDK that doesn't know the parameter yet
        resp = client.beta.messages.create(**kw, extra_body={"fallbacks": "default"})
    if resp.stop_reason == "refusal":
        raise ValueError("Claude declined this request → rephrase it, or use the local model")
    if resp.stop_reason == "max_tokens":
        raise ValueError("The plan was too long → ask for less at once (fewer steps or areas)")
    return next((b.text for b in resp.content if b.type == "text"), "")


class ApiError(ValueError):
    """An online service's refusal, as a message that says what to do."""


def ask_api(system: str, messages: list[dict], json_answer: bool = True, max_tokens: int = 8000) -> str:
    """One chat request to an OpenAI-compatible service (Hugging Face, Groq, OpenRouter, Gemini, Mistral, Cerebras,
    LM Studio…). JSON mode is asked for; a service that doesn't know it is asked again without it."""
    base, model, key = _api_target()
    name = API_PRESETS[settings()["api_preset"]]["title"]
    body = {"model": model, "messages": [{"role": "system", "content": system}, *messages], "temperature": 0.1, "max_tokens": max_tokens}
    if json_answer:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(3):
        try:
            r = requests.post(f"{base}/chat/completions", headers=_api_headers(key), json=body, timeout=(10, 300))
        except requests.RequestException as e:
            raise ApiError(f"Couldn't reach {name} → check the internet connection (or the address) and try again ({e.__class__.__name__})")
        if r.status_code == 400 and "response_format" in body and any(w in r.text.lower() for w in ("response_format", "json", "not supported")):
            body.pop("response_format")   # JSON mode isn't offered by this model: the answer is read as text
            continue
        if r.status_code == 429 and attempt < 2:
            time.sleep(float(r.headers.get("retry-after") or 8) if str(r.headers.get("retry-after") or "").replace(".", "").isdigit() else 8)
            continue
        break
    if r.status_code in (401, 403):
        raise ApiError(f"{name} refused the key → check it in Credentials (Online models for the Assistant)")
    if r.status_code == 402:
        raise ApiError(f"{name}: the free allowance of this key is used up → wait for it to renew, or choose another service or model")
    if r.status_code == 404:
        raise ApiError(f"{name} doesn't know the model “{model}” → choose another one (List models)")
    if r.status_code == 429:
        raise ApiError(f"{name} is limiting requests for your key (free-tier limit) → wait a minute, or choose another service")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else msg
        except ValueError:
            msg = r.text[:300]
        raise ApiError(f"{name} answered with an error ({r.status_code}): {str(msg)[:300]}")
    try:
        choice = r.json()["choices"][0]
        text = choice["message"].get("content") or ""
    except (ValueError, KeyError, IndexError):
        raise ApiError(f"{name}'s answer couldn't be read → try again, or choose another model")
    if choice.get("finish_reason") == "length":
        raise ApiError("The plan was too long for the model → ask for less at once (fewer steps or areas)")
    return text


def _parse(text: str) -> dict:
    text = (text or "").strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        text = text[i:j + 1] if i >= 0 < j else text
    return json.loads(text)


def _unwrap(v):
    """A value given as JSON text (or JSON text inside JSON text): the value."""
    for _ in range(2):
        if isinstance(v, str) and v.strip()[:1] in ("{", "[", '"'):
            try:
                v = json.loads(v)
                continue
            except ValueError:
                pass
        break
    return v


AREA_NAMES = ("aoi", "clip", "area", "geometry")


def _area_names(spec: dict, endpoint: str, body: dict) -> None:
    """The tools call their area aoi, clip or area: an area given under another of these names is moved to the one
    this tool has (when it has exactly one and it isn't given already)."""
    props = (_schema_of(spec, endpoint) or {}).get("properties", {})
    mine = [k for k in AREA_NAMES if k in props]
    if len(mine) != 1 or mine[0] in body:
        return
    for k in AREA_NAMES:
        if k in body and k not in props:
            body[mine[0]] = body.pop(k)
            return


def _renumber(steps: list[dict], start: int) -> None:
    """Small models often count steps from 1 in {"$step": n}: when every reference of the new steps points at its own
    step or later, and all of them one lower would be valid, they are moved down by one."""
    refs = []

    def walk(node, i):
        if isinstance(node, dict):
            if "$step" in node and isinstance(node["$step"], int):
                refs.append((i, node))
            else:
                for v in node.values():
                    walk(v, i)
        elif isinstance(node, list):
            for v in node:
                walk(v, i)
    for i in range(start, len(steps)):
        walk(steps[i]["body"], i)
    if refs and any(r["$step"] >= i for i, r in refs) and all(0 <= r["$step"] - 1 < i for i, r in refs):
        for _, r in refs:
            r["$step"] -= 1


def _to_workflow(out: dict, known_files: set | None = None, prefix: list[dict] | None = None) -> tuple[dict, list[str]]:
    """The model's answer as a workflow (settings as objects) and the problems found in it."""
    probs, w = [], out.get("workflow") or {}
    inputs = []
    for i in w.get("inputs") or []:
        default = _unwrap(i["default"] if "default" in i else i.get("default_json"))
        if i.get("type") == "file" and known_files is not None and isinstance(default, str) and default not in known_files:
            probs.append(f"input {i.get('id')}: “{default}” isn't in the user's data → use a listed file, or let a step make it")
        typ = i.get("type") if i.get("type") in ("file", "area", "value") else "value"
        kind = workflows.kind_of(default) if typ == "file" and isinstance(default, str) else "area" if typ == "area" else "value"
        if typ == "area" and not (isinstance(default, dict) and default.get("type") in ("Polygon", "MultiPolygon")):
            probs.append(f"input {i.get('id')}: an area must be a GeoJSON Polygon or MultiPolygon")
        inputs.append({"id": str(i.get("id") or f"in{len(inputs) + 1}"), "label": i.get("label") or "Input", "type": typ, "kind": kind, "default": default})
    pre = [{"title": d["title"], "kind": "", "endpoint": d["endpoint"], "body": d["body"]} for d in prefix or []]
    steps, spec = list(pre), _openapi()
    for n, s in enumerate(w.get("steps") or [], start=len(pre)):
        body = _unwrap(s["body"] if "body" in s else (s.get("body_json") or "{}"))
        if isinstance(body, dict) and len(body) == 1 and next(iter(body)) in ("settings", "body", "params", "parameters") and isinstance(next(iter(body.values())), dict):
            body = next(iter(body.values()))   # the settings wrapped once more: unwrapped
        if isinstance(body, str):
            probs.append(f"step {n + 1}: the settings aren't valid JSON")
            body = {}
        if not isinstance(body, dict):
            probs.append(f"step {n + 1}: body_json must be a JSON object")
            body = {}
        _area_names(spec, s.get("endpoint", ""), body)
        probs += [f"step {n + 1}: {p}" for p in _check_body(spec, s.get("endpoint", ""), body, {i["id"]: i["type"] for i in inputs})]
        steps.append({"title": s.get("title") or s.get("endpoint"), "kind": "", "endpoint": s.get("endpoint", ""), "body": body})
    _renumber(steps, len(pre))
    wf = {"name": w.get("name") or "Assistant workflow", "description": out.get("plan", ""), "inputs": inputs, "steps": steps}
    if prefix and len(steps) == len(pre):
        probs.append("no steps were given for what still has to run")
    if steps:
        try:
            workflows.check(wf)
        except ValueError as e:
            probs.append(str(e))
    return wf, probs


def plan(messages: list[dict], context: dict, conv_id: str = "", prefix: list[dict] | None = None) -> dict:
    """Plan the user's request (the conversation so far: questions answered, plans changed) as a workflow.
    With `prefix` (steps already run), only the steps still to run are asked for, numbered after them."""
    st = status()
    if not st["ready"]:
        raise ValueError("The Assistant isn't set up yet → choose a model in its settings (a free local one, a free online one with your key, or Claude)")
    text_fields = st["provider"] == "claude"   # Claude's strict schema has settings as JSON text; the others give objects
    first_request = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    system, t0 = system_prompt(context, text_fields=text_fields, memory=_memory_text(str(first_request), context)), time.time()
    known = {str(x.get("path")) for k in ("layers", "tables") for x in context.get(k) or [] if isinstance(x, dict) and x.get("path")}
    msgs = [{"role": m["role"], "content": str(m["content"])[:20000]} for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    if not msgs or msgs[0]["role"] != "user":
        raise ValueError("Say what you want done")
    if st["provider"] == "api":   # no schema to enforce online: the schema itself goes into the instructions
        system += "\n\nThe JSON schema of your answer:\n" + json.dumps(_local_schema())
    ask = {"claude": lambda m: _ask_claude(system, m), "api": lambda m: ask_api(system, m),
           "ollama": lambda m: _ask_ollama(st["model"], system, m)}[st["provider"]]
    tries, out, wf, probs = 0, {}, {}, []
    while True:   # a plan with problems goes back to be fixed, twice at most
        text = ask(msgs)
        try:
            out = _parse(text)
            wf, probs = _to_workflow(out, known, prefix)
        except (ValueError, json.JSONDecodeError) as e:
            out, wf, probs = {}, {}, [f"the answer wasn't the JSON asked for ({e})"]
        if not probs and wf.get("steps") is not None and not out.get("questions") and not wf["steps"]:
            probs = ["the workflow has no steps: plan the steps, or ask a question"]
        if not probs or tries >= 2:
            break
        tries += 1
        msgs += [{"role": "assistant", "content": text},
                 {"role": "user", "content": "Your plan has these problems:\n- " + "\n- ".join(probs) + "\nFix them and answer again with the whole JSON."}]
    if not out:
        raise ValueError("The model's answer couldn't be read as a plan → try again, or rephrase the request")
    remembered = data.remember(out.get("remember") or [])
    result = {"plan": out.get("plan", ""), "questions": [q for q in out.get("questions") or [] if q], "workflow": wf, "problems": probs,
              "remembered": remembered, "provider": st["provider"], "model": st["model"], "seconds": round(time.time() - t0, 1), "fixes": tries,
              "reply": json.dumps(out, ensure_ascii=False)}
    if conv_id:
        if not prefix:
            data.log(conv_id, {"role": "user", "text": msgs[-1]["content"] if msgs[-1]["role"] == "user" else first_request})
        data.log(conv_id, {"role": "assistant", **{k: result[k] for k in ("plan", "questions", "workflow", "problems", "remembered", "model", "seconds", "fixes")}})
    return result


def continue_plan(messages: list[dict], context: dict, workflow: dict, done: list[dict], failed: dict | None, conv_id: str = "") -> dict:
    """GISclaw's Replan: steps 0…k-1 ran (with what they made), step k failed or made something wrong. The model gives
    the steps still to run, from k on; the failure is kept in the error memory."""
    k = len(done)
    lines = [f"[{i}] {d['title']} ({d['endpoint']}) → made: {json.dumps(d.get('observation'), ensure_ascii=False)[:1500]}" for i, d in enumerate(done)]
    if failed:
        data.record_error(failed.get("endpoint", ""), failed.get("body") or {}, failed.get("error", ""))
    errors = [f"- step [{e.get('index')}] {e.get('endpoint')}: {e.get('error')}" for e in (failed or {}).get("history", [])]
    msg = ("We are running your plan.\nDone steps (their numbers are what {\"$step\": n} refers to):\n" + ("\n".join(lines) or "(none)") +
           (f"\n\nStep [{k}] “{failed.get('title')}” ({failed.get('endpoint')}) with the settings {json.dumps(failed.get('body'), ensure_ascii=False)[:2500]}\n"
            f"{'failed with: ' + failed.get('error', '') if failed.get('error') else 'made something wrong: ' + '; '.join(failed.get('warnings') or [])}" if failed else "") +
           ("\n\nWhat failed so far in this task (don't repeat it):\n" + "\n".join(errors) if errors else "") +
           f"\n\nGive the steps that still have to run, starting with step [{k}] (fixed, or done another way), and the plan for them."
           " Keep the same inputs (you may add some). For a file made by a done step use {\"$step\": its number, ...}.")
    out = plan([*messages, {"role": "user", "content": msg}], context, conv_id="", prefix=done)
    if conv_id:
        data.log(conv_id, {"role": "event", "kind": "replan", "failed": failed and {k2: failed.get(k2) for k2 in ("title", "endpoint", "error", "warnings")}, "plan": out["plan"]})
    return out


# ------------------------------------------------------------------ explaining what came out
EXPLAIN_SYSTEM = """You explain the results of a GIS workflow that just ran in LULC Fetch, for the person who asked for it.
Write 3 to 6 short sentences in their language, plain text (no headings, no lists unless there are several numbers to
compare). Say what came out and what it means for their question: the key numbers with their units (hectares, %, m,
index values), the biggest or most surprising ones, and one caveat if the data suggest one (many empty pixels, few
points, a low accuracy, clouds). Use ONLY the numbers given below; never invent or estimate others. If the results
can't answer the question, say so and what to try next."""


def chat_text(system: str, messages: list[dict], max_tokens: int = 1500) -> str:
    """A plain-text answer from the chosen model (no plan schema)."""
    st = status()
    if not st["ready"]:
        raise ValueError("The Assistant isn't set up yet → choose a model in its settings")
    if st["provider"] == "api":
        return ask_api(system, messages, json_answer=False, max_tokens=max_tokens).strip()
    if st["provider"] == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=_claude_key(), max_retries=2, timeout=120)
        resp = client.messages.create(model=CLAUDE_MODEL, max_tokens=max_tokens, system=system, messages=messages)
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    s = settings()
    r = requests.post(f"{s['ollama_url']}/api/chat", timeout=(5, 300), json={
        "model": st["model"], "stream": False, "messages": [{"role": "system", "content": system}, *messages],
        "options": {"temperature": 0.2, "num_ctx": 8192}})
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def _trim(v, depth: int = 0):
    """A step's result made small enough for the model: long lists cut, geometry and paths left out."""
    if isinstance(v, dict):
        return {k: _trim(x, depth + 1) for k, x in list(v.items())[:40]
                if k not in ("geometry", "coordinates", "footprints", "preview") and not (isinstance(x, str) and len(x) > 300)}
    if isinstance(v, list):
        return [_trim(x, depth + 1) for x in v[:25]] + ([f"… {len(v) - 25} more"] if len(v) > 25 else [])
    if isinstance(v, float):
        return round(v, 4)
    return v


def explain(request: str, steps: list[dict]) -> str:
    """What came out of a run, in a few sentences: the request, and each step's title, tool, result and what its files
    hold (observations)."""
    facts = [{"step": i + 1, "title": s.get("title"), "tool": s.get("endpoint"), "result": _trim(s.get("result")),
              "files": _trim(s.get("observation"))} for i, s in enumerate(steps)]
    text = json.dumps(facts, ensure_ascii=False, default=str)[:14000]
    return chat_text(EXPLAIN_SYSTEM, [{"role": "user", "content": f"My request was: {request}\n\nWhat the steps made:\n{text}\n\nExplain the results."}])
