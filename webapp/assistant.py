"""The Assistant (Analysis ▸ Tools ▸ Assistant): say what you want done; it plans it as a workflow of the app's own
tools, you check the plan, then the app runs it like any workflow (webapp/workflows.py).

Free first: a local model through Ollama (runs on this computer, offline). Optionally Claude, with the user's own
Anthropic API key (kept in the keychain like the other accounts, see webapp/credentials.py).

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
}


# ------------------------------------------------------------------ settings
def _settings_file() -> Path:
    return ws.CONFIG_DIR / "assistant.json"


def settings() -> dict:
    try:
        s = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    return {"provider": s.get("provider") if s.get("provider") in ("ollama", "claude") else "ollama",
            "model": s.get("model") or "", "ollama_url": s.get("ollama_url") or OLLAMA_URL}


def save_settings(provider: str, model: str = "", ollama_url: str = "") -> dict:
    if provider not in ("ollama", "claude"):
        raise ValueError("Unknown provider")
    s = {**settings(), "provider": provider, "model": model.strip()[:100]}
    if ollama_url:
        if not ollama_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("The Ollama address must be on this computer (http://127.0.0.1:11434)")
        s["ollama_url"] = ollama_url.rstrip("/")
    ws.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _settings_file().write_text(json.dumps(s, indent=1), encoding="utf-8")
    return s


def _claude_key() -> str:
    from . import credentials
    return (credentials.get_all("anthropic") or {}).get("api_key") or ""


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
    model = s["model"] or (RECOMMENDED_LOCAL if RECOMMENDED_LOCAL in ollama["models"] else (ollama["models"][0] if ollama["models"] else RECOMMENDED_LOCAL))
    ready = (s["provider"] == "ollama" and ollama["running"] and model in ollama["models"]) or (s["provider"] == "claude" and claude["key_set"] and sdk)
    return {"provider": s["provider"], "model": model if s["provider"] == "ollama" else CLAUDE_MODEL, "ready": ready,
            "ollama": ollama, "claude": claude, "recommended": RECOMMENDED_LOCAL}


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
- Indices (NDVI, NDWI, EVI, SAVI…) of an image: /api/analyze/export. A map of classes from labelled data:
  /api/rasterml/run. Embeddings (AlphaEarth, TESSERA): /api/emb/fetch. Files in the data library: /api/library/fetch.
- An area of the user's (a polygon layer, the map view) goes into aoi / clip as an input of type "area".
- Vector layers (points, lines, polygons) are used with their path from the data list (an input of type "file"):
  a zone around them → /api/vector/buffer; features matching a condition on their fields → /api/vector/query (use the
  field names and values seen in the data); where two layers overlap, or their union / difference → /api/vector/overlay;
  merging shapes → /api/vector/dissolve. A vector file made by an earlier step: {"$step": n, "ext": ".geojson", "nth": 0}."""


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
        raise ValueError("The Assistant isn't set up yet → choose a model in its settings (a free local one, or Claude with your key)")
    local = st["provider"] == "ollama"
    first_request = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    system, t0 = system_prompt(context, text_fields=not local, memory=_memory_text(str(first_request), context)), time.time()
    known = {str(x.get("path")) for k in ("layers", "tables") for x in context.get(k) or [] if isinstance(x, dict) and x.get("path")}
    msgs = [{"role": m["role"], "content": str(m["content"])[:20000]} for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    if not msgs or msgs[0]["role"] != "user":
        raise ValueError("Say what you want done")
    ask = (lambda m: _ask_claude(system, m)) if st["provider"] == "claude" else (lambda m: _ask_ollama(st["model"], system, m))
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
