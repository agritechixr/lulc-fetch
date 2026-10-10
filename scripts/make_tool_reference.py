"""Writes docs/TOOL_REFERENCE.md: every tool of the app with where it is, what it does, its API endpoint(s) and every
parameter (type, default, allowed range or values), then every model the tools offer with its own parameters.

It is generated from the code itself, so it can't miss a tool or a parameter:
- the tools (title, menu, ribbon group, description) from their registrations in webapp/static (LF.tool and TOOLS),
- the endpoints and their parameters from the server's API schema (FastAPI / pydantic: the same checks the app runs),
- the models from the catalogues in lulc_fetch (ml.MODELS, forecast.PARAMS, dl.ARCHS, …) and the hydrology modules.

    .venv/bin/python scripts/make_tool_reference.py          (run it again after changing a tool)
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "webapp" / "static"
OUT = ROOT / "docs" / "TOOL_REFERENCE.md"

MENU_PATH = {"agri": "Analysis ▸ Agri", "embed": "Analysis ▸ Embeddings", "forecast": "Analysis ▸ Forecast", "sar": "Analysis ▸ SAR",
             "hydro": "Analysis ▸ Hydrology", "library": "Insert ▸ Library", "online": "Insert"}
CORE_FILES = {"search": ["find-imagery"], "analyze": ["index-analysis.js"], "pca": ["pca.js"], "samples": ["training-samples.js"],
              "stack": ["stack.js"], "raster2table": ["raster-to-table.js"], "ml": ["classical-ml"], "rasterml": ["raster-ml.js"],
              "dltrain": ["deep-learning"], "dlpredict": ["deep-learning"], "detect": ["detect-object.js"], "traindet": ["train-detection.js"],
              "patches": ["training-data.js"], "export": ["export.js"], "jobs": ["jobs.js"]}
API = re.compile(r"/api/[A-Za-z0-9_\-/{}]+")


def js_string(s: str) -> str:
    return s.encode().decode("unicode_escape").encode("latin-1").decode("utf-8") if "\\u" in s else s.replace('\\"', '"')


def tools() -> list[dict]:
    """Every tool: id, title, subtitle, menu and the endpoints its code calls."""
    found: dict[str, dict] = {}
    menu_js = (STATIC / "app/core/tools-menu.js").read_text(encoding="utf-8")
    for m in re.finditer(r'\{ id: "(\w+)", title: "([^"]+)", icon: "[\w-]+", subtitle: "((?:[^"\\]|\\.)*)" \}', menu_js):
        tid = m.group(1)
        eps = set()
        for f in CORE_FILES.get(tid, []):
            p = STATIC / "app/tools" / f
            for file in ([p] if p.is_file() else sorted(p.rglob("*.js"))):
                eps |= set(API.findall(file.read_text(encoding="utf-8")))
        found[tid] = {"id": tid, "title": m.group(2), "subtitle": js_string(m.group(3)), "menu": "", "endpoints": eps, "file": "app/tools"}
    for f in sorted(STATIC.glob("tools/**/*.js")):
        s = f.read_text(encoding="utf-8")
        starts = [m.start() for m in re.finditer(r"LF\.tool\(\{", s)]
        for i, st in enumerate(starts):
            block = s[st:starts[i + 1] if i + 1 < len(starts) else len(s)]
            idm = re.search(r'id: "(\w+)"', block)
            tm = re.search(r'title: "([^"]+)"', block)
            sm = re.search(r'subtitle: "((?:[^"\\]|\\.)*)"', block)
            mm = re.search(r'menu: "(\w+)"', block[:400])
            if not idm or not tm:
                continue
            new = {"id": idm.group(1), "title": tm.group(1), "subtitle": js_string(sm.group(1)) if sm else "",
                   "menu": mm.group(1) if mm else "", "endpoints": set(API.findall(block)), "file": str(f.relative_to(STATIC))}
            old = found.get(new["id"])
            if old:   # registered in two places: keep what each knows
                new["endpoints"] |= old["endpoints"]
                for k in ("subtitle", "menu"):
                    new[k] = new[k] or old[k]
            found[new["id"]] = new
    return list(found.values())


def groups() -> tuple[list, dict]:
    """(Analysis ▸ Tools ribbon groups, other menus' groups)."""
    menu_js = (STATIC / "app/core/tools-menu.js").read_text(encoding="utf-8")
    tg = menu_js.split("const TOOL_GROUPS = ")[1].split(";\n")[0]
    tool_groups = [(g, re.findall(r'"(\w+)"', ids)) for g, ids in re.findall(r'\["([^"]+)", \[([^\]]*)\]\]', tg)]
    hy = menu_js.split('hydro: { el: "#hydro-menu"')[1].split("shortcuts:")[0]
    hydro_groups = [(g, re.findall(r'"(\w+)"', ids)) for g, ids in re.findall(r'\["([^"]+)", \[([^\]]*)\]\]', hy)]
    return tool_groups, {"hydro": hydro_groups}


def schema_tables(app) -> tuple[dict, dict, dict]:
    """The API schema, its components, and {(method, path): description} of every /api endpoint."""
    spec = app.openapi()
    comps = spec.get("components", {}).get("schemas", {})
    routes = {}
    for path, ops in spec["paths"].items():
        if path.startswith("/api/"):
            for meth, op in ops.items():
                routes[(meth.upper(), path)] = (op.get("description") or "").strip()
    return spec, comps, routes


def _type(s: dict, comps: dict) -> str:
    if "$ref" in s:
        return s["$ref"].split("/")[-1]
    if "anyOf" in s:
        ts = [_type(x, comps) for x in s["anyOf"] if x.get("type") != "null"]
        return " or ".join(ts) + (" (optional)" if any(x.get("type") == "null" for x in s["anyOf"]) else "")
    t = s.get("type", "any")
    if t == "array":
        return f"list of {_type(s.get('items', {}), comps)}"
    if t == "object":
        ap = s.get("additionalProperties")
        return f"object ({_type(ap, comps)} values)" if isinstance(ap, dict) and ap else "object"
    return {"number": "number", "integer": "integer", "string": "text", "boolean": "yes / no"}.get(t, t)


def _range(s: dict) -> str:
    bits = []
    for x in [s] + s.get("anyOf", []):
        if "minimum" in x:
            bits.append(f"≥ {x['minimum']:g}")
        if "exclusiveMinimum" in x:
            bits.append(f"> {x['exclusiveMinimum']:g}")
        if "maximum" in x:
            bits.append(f"≤ {x['maximum']:g}")
        if "exclusiveMaximum" in x:
            bits.append(f"< {x['exclusiveMaximum']:g}")
        if "pattern" in x:
            p = x["pattern"]
            alt = re.fullmatch(r"\^\(([^()]+)\)\$", p)
            bits.append("one of " + ", ".join(f"`{a}`" for a in alt.group(1).split("|")) if alt else f"pattern `{p}`")
        if "enum" in x:
            bits.append("one of " + ", ".join(f"`{a}`" for a in x["enum"]))
        if "minItems" in x or "maxItems" in x:
            bits.append(f"{x.get('minItems', 0)}–{x.get('maxItems', '…')} items")
        if "maxLength" in x:
            bits.append(f"up to {x['maxLength']} characters")
    return "; ".join(dict.fromkeys(bits))


def _default(s: dict, required: bool) -> str:
    if required:
        return "**required**"
    if "default" in s:
        d = s["default"]
        return "none" if d is None else f"`{json.dumps(d, ensure_ascii=False)}`"
    return "none"


def params_md(schema_name: str, comps: dict, seen=None) -> str:
    seen = seen or set()
    sch = comps.get(schema_name, {})
    req = set(sch.get("required", []))
    rows = ["| Parameter | Type | Default | Allowed |", "|---|---|---|---|"]
    nested = []
    for k, v in sch.get("properties", {}).items():
        rows.append(f"| `{k}` | {_type(v, comps)} | {_default(v, k in req)} | {_range(v)} |")
        refs = re.findall(r"#/components/schemas/(\w+)", json.dumps(v))
        nested += [r for r in refs if r not in seen and r != schema_name]
    out = "\n".join(rows)
    for r in dict.fromkeys(nested):
        seen.add(r)
        out += f"\n\n*{r}*:\n\n" + params_md(r, comps, seen)
    return out


def endpoint_md(meth: str, path: str, spec: dict, comps: dict, doc: str) -> str:
    op = spec["paths"].get(path, {}).get(meth.lower(), {})
    out = [f"`{meth} {path}`" + (f": {doc.splitlines()[0] if doc else ''}" if doc else "")]
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if body and "$ref" in body:
        out.append(params_md(body["$ref"].split("/")[-1], comps))
    qs = [p for p in op.get("parameters", []) if p.get("in") in ("query", "path")]
    if qs:
        rows = ["| Parameter | Type | Default | Allowed |", "|---|---|---|---|"]
        for p in qs:
            s = p.get("schema", {})
            rows.append(f"| `{p['name']}` ({p['in']}) | {_type(s, comps)} | {_default(s, p.get('required', False))} | {_range(s)} |")
        out.append("\n".join(rows))
    return "\n\n".join(out)


def catalogue_params(params: list) -> str:
    if not params:
        return "*(no settings)*"
    rows = ["| Setting | Type | Default | Range / options | What it does |", "|---|---|---|---|---|"]
    for p in params:
        opts = p.get("options")
        rng = ", ".join(f"`{o[0]}`" if isinstance(o, (list, tuple)) else f"`{o}`" for o in opts) if opts else \
            " – ".join(str(x) for x in (p.get("min"), p.get("max")) if x is not None)
        tip = (p.get("tip") or p.get("help") or "").replace("|", "/").replace("\n", " ")
        rows.append(f"| `{p['name']}` ({p.get('label') or p.get('title', '')}) | {p.get('type') or p.get('kind', '')} | "
                    f"`{json.dumps(p.get('default'), ensure_ascii=False)}` | {rng} | {tip} |")
    return "\n".join(rows)


def models_md() -> str:
    from lulc_fetch import dl, forecast, interpolation, ml, pca, rasterml, unsupervised
    from lulc_fetch import detect as det
    from lulc_fetch.lightseg import MODELS as LIGHT
    out = ["## Models and their parameters", ""]
    out.append("### Classical ML (tables and rasters) — `lulc_fetch/ml.py`\n\nUsed by Classical ML (tabular data) and Classical ML for "
               "raster. Settings shared by every model:\n\n" + catalogue_params(ml.COMMON) + "\n")
    for k, m in ml.MODELS.items():
        out.append(f"#### {m['title']} (`{k}`)\n\n{m.get('family', '')} · {', '.join(m.get('tasks', []))}. {m.get('desc', '')}\n\n" + catalogue_params(m.get("params", [])) + "\n")
    out.append("### Classical ML for raster: which models for which data — `lulc_fetch/rasterml.py`\n\n| Data | Models offered first | Note |\n|---|---|---|")
    for k, v in rasterml.KINDS.items():
        out.append(f"| {v['title']} | {', '.join(v.get('models', []))} | {v.get('note', '')} |")
    out.append(f"\nAll raster models: {', '.join(f'`{m}`' for m in rasterml.MODELS)} (settings as in the table models above; "
               "`mlc` maximum likelihood, `sam` spectral angle mapper, `mindist` minimum distance).\n")
    out.append("### Unsupervised (clustering) — `lulc_fetch/unsupervised.py`\n")
    for k, m in unsupervised.METHODS.items():
        out.append(f"#### {m['title']} (`{k}`)\n\n{m.get('desc', '')}\n\n" + catalogue_params(m.get("params", [])) + "\n")
    out.append("Options for every method:\n\n" + catalogue_params(unsupervised.OPTIONS) + "\n")
    out.append("### PCA and dimensionality reduction — `lulc_fetch/pca.py`\n\nShared:\n\n" + catalogue_params(pca.COMMON) + "\n")
    for k, m in pca.METHODS.items():
        out.append(f"#### {m.get('full') or m['title']} (`{k}`)\n\n{m.get('desc', '')}\n\n" + catalogue_params(m.get("params", [])) + "\n")
    out.append("### Forecasting models — `lulc_fetch/forecast.py`\n")
    for k, m in forecast.MODELS.items():
        out.append(f"#### {m['title']} (`{k}`)\n\n{m.get('desc', '')}\n\n" + catalogue_params(forecast.PARAMS.get(k, [])) + "\n")
    out.append("Training options for every model:\n\n" + catalogue_params(forecast.OPTIONS) + "\n")
    out.append("### Interpolation methods — `lulc_fetch/interpolation.py`\n")
    for k, m in interpolation.METHODS.items():
        out.append(f"#### {m['title']} (`{k}`)\n\n{m.get('desc', '')} Good for: {m.get('good', '–')}.\n\n" + catalogue_params(m.get("params", [])) + "\n")
    out.append("### Deep learning: segmentation (Train classify model) — `lulc_fetch/dl.py`\n\n| Architecture | Key | Accuracy (1–5) | Speed (1–5) | What it is |\n|---|---|---|---|---|")
    for k, a in dl.ARCHS.items():
        out.append(f"| {a['title']} | `{k}` | {a.get('accuracy', '')} | {a.get('speed', '')} | {a.get('desc', '')} |")
    out.append("\nEncoders (backbones): " + ", ".join(f"`{e}`" for e in dl.ENCODERS) + "\n\nTraining settings:\n\n" + catalogue_params(dl.PARAMS) + "\n")
    out.append("### Light segmentation networks — `lulc_fetch/lightseg/`\n\n| Network | Key | Paper | Parameters (M, Cityscapes) | What it is |\n|---|---|---|---|---|")
    for k, (title, _f, paper, mp, desc) in LIGHT.items():
        out.append(f"| {title} | `{k}` | {paper} | {mp} | {desc} |")
    out.append("\n### Object detection models — `lulc_fetch/detect.py`\n\n| Model | Key | Backbone | Input size | Accuracy (1–5) | Speed (1–5) | Note |\n|---|---|---|---|---|---|---|")
    for k, m in det.MODELS.items():
        out.append(f"| {m['title']} | `{k}` | {m.get('backbone', '')} | {m.get('size', '')} | {m.get('accuracy', '')} | {m.get('speed', '')} | {m.get('desc', '')} |")
    out.append("\n" + hydro_models_md())
    return "\n".join(out)


def hydro_models_md() -> str:
    from lulc_fetch import ahp
    from lulc_fetch.hydro import erosion, flood2d, groundwater, rainfall, runoff, streamflow
    from lulc_fetch.sar import floodml
    t = lambda d, a, b: "\n".join([f"| {a} | {b} |", "|---|---|"] + [f"| {k} | {v} |" for k, v in d.items()])
    cn = "\n".join(["| Cover | A | B | C | D |", "|---|---|---|---|---|"] + [f"| {k} | " + " | ".join("–" if x is None else str(x) for x in v) + " |"
                                                                          for k, v in runoff.CN_BY_COVER.items()])
    return f"""### Hydrology and SAR models

#### GR4J daily rainfall–runoff model — `lulc_fetch/hydro/streamflow.py`

Four parameters, calibrated on the KGE of the calibration years (differential evolution, population 6 × 4, up to 25
generations, Sobol start; then a Nelder–Mead polish), after a warm-up of up to 365 days:

| Parameter | Meaning | Range searched |
|---|---|---|
| X1 | production store capacity (mm) | {streamflow.GR4J_BOUNDS[0][0]:g} – {streamflow.GR4J_BOUNDS[0][1]:g} |
| X2 | groundwater exchange (mm/day) | {streamflow.GR4J_BOUNDS[1][0]:g} – {streamflow.GR4J_BOUNDS[1][1]:g} |
| X3 | routing store capacity (mm) | {streamflow.GR4J_BOUNDS[2][0]:g} – {streamflow.GR4J_BOUNDS[2][1]:g} |
| X4 | unit hydrograph time base (days) | {streamflow.GR4J_BOUNDS[3][0]:g} – {streamflow.GR4J_BOUNDS[3][1]:g} |

#### Streamflow machine learning
LightGBM: 600 trees, learning rate 0.03, 31 leaves, min 20 rows per leaf, row and column subsampling 0.8; Random
Forest: 300 trees, min 3 rows per leaf. Target: log(1 + flow). Inputs: rain on the last 0–7 days, rain sums over 3, 7,
14, 30, 60, 90 and 180 days, evaporation sums over 7 and 30 days, the antecedent precipitation index (k = 0.9), mean
temperature, day of year (sine, cosine); optionally yesterday's observed flow.

#### Streamflow LSTM (deep learning add-on)
One LSTM layer of 48 units over the last 120 days of rain, evaporation, temperature and season, dropout 0.2, a linear
head; Adam (learning rate 0.002), batches of 256, up to 40 epochs with early stopping (8 epochs of patience) on 10 %
of the calibration days; inputs standardised on the calibration period, target log(1 + flow).

#### Scores used
NSE (Nash–Sutcliffe), KGE (Kling–Gupta), percent bias, RMSE; baseline: the day-of-year mean of the calibration years.

#### SAR flood map ML refinement — `lulc_fetch/sar/floodml.py`
Labels: flood-map confidence ≥ 0.75 water, ≤ 0.25 dry (the rest is decided by the model). Features: VV, VH (dB),
VV − VH, mean and standard deviation over 5 × 5 and 11 × 11 of VV and VH, VV change since a pre-flood image, HAND and
slope from a DEM. LightGBM: 300 trees, learning rate 0.05, 63 leaves, subsampling 0.8, balanced water / dry samples
(up to 200,000). TinyUNet: 128 × 128 crops, batches of 8, AdamW (0.002, weight decay 1e-4), class-weighted cross
entropy with the uncertain pixels ignored, flips; scored on held-out 64 × 64-pixel blocks.

#### Flood susceptibility — `lulc_fetch/hydro/susceptibility.py`
Random Forest (300 trees, min 2 per leaf) or LightGBM (300 trees, learning rate 0.05, 31 leaves); non-flood samples
at least the buffer away from floods (1 per flood sample by default); ROC AUC by spatial-block (GroupKFold) and random
(stratified) 5-fold cross-validation; classes by probability: < 0.2 very low … ≥ 0.8 very high.

#### 2D flood simulation — `lulc_fetch/hydro/flood2d.py`
Local inertial shallow-water equations (Bates et al. 2010) with semi-implicit Manning friction, Courant number
α = 0.7 (with the flow speed), time steps up to 30 s, flows capped at Froude 1, outflows limited to the water a cell
holds (exact mass balance), normal-depth outflow at the DEM's edges, up to {flood2d.MAX_CELLS:,} cells. Manning's n by
land cover:

{t(flood2d.N_COVER, "Cover", "Manning's n")}

#### SCS curve numbers (Rainfall–runoff, Design flood hydrograph) — `lulc_fetch/hydro/runoff.py`

{cn}

Antecedent moisture: CN I = 4.2 CN / (10 − 0.058 CN), CN III = 23 CN / (10 + 0.13 CN); initial abstraction λ = 0.2
(0.05 optional). SCS dimensionless unit hydrograph with lag 0.6 tc and peak 0.208 A / Tp; storm pattern SCS Type II.

#### RUSLE factors — `lulc_fetch/hydro/erosion.py`

{t(erosion.K_TEXTURE, "Soil texture", "K (t·h/MJ/mm)")}

{t(erosion.C_COVER, "Cover", "C")}

#### Groundwater potential — `lulc_fetch/hydro/groundwater.py`
Factor order for the AHP weights (most important first): {", ".join(groundwater.TITLES[k] for k in groundwater.ORDER)}.
Land-cover scores (1 poor … 5 good):

{t(groundwater.LC_SCORE, "Cover", "Score")}

#### AHP — `lulc_fetch/ahp.py`
Saaty's random index RI by the number of factors: {", ".join(f"{k}: {v}" for k, v in ahp.RI.items())}. Consistent when
CR = CI / RI < 0.10.

#### Design storms — `lulc_fetch/hydro/rainfall.py`
Gumbel (EV1) by the method of moments on the annual maxima; return periods {", ".join(str(x) for x in rainfall.RETURN_PERIODS)} years;
1-, 2-, 3- and 5-day totals.
"""


def main():
    from webapp.server import app
    spec, comps, routes = schema_tables(app)
    tl = tools()
    tool_groups, menu_groups = groups()
    by_id = {t["id"]: t for t in tl}
    used = set()
    lines = ["# LULC Fetch: tool and model reference", "",
             "Every tool, where it is in the app, what it does, the server endpoint(s) it runs and **every parameter** "
             "(type, default, allowed values), then every model with its settings. Generated from the code by "
             "`scripts/make_tool_reference.py` (run it again after changing a tool), so it matches the app exactly. "
             "Parameters are those of the API (what the panels, the Assistant and Workflows send); the panels show them "
             "with plain names. For how to use the tools see the [User Guide](USER_GUIDE.md); for an overview, "
             "[FEATURES.md](../FEATURES.md).", "",
             "Types: *text*, *number*, *integer*, *yes / no*, *list*, *object* (e.g. a GeoJSON layer). Paths are relative to the "
             "workspace (e.g. `downloads/…/image.tif`, `uploads/dem.tif`); points are `[longitude, latitude]`.", ""]

    def section(title: str, ids: list[str]):
        rows = [by_id[i] for i in ids if i in by_id]
        if not rows:
            return
        lines.append(f"### {title}\n")
        for t in rows:
            lines.append(f"#### {t['title']}\n\n{t['subtitle']}\n\n*Tool id* `{t['id']}`.\n")
            eps = sorted(e for e in t["endpoints"] if any(p == e or re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", p), e) for _, p in routes))
            if not eps:
                lines.append("Runs in the browser (or through endpoints shared with other tools).\n")
            for e in eps:
                for meth in ("POST", "GET"):
                    path = next((p for (m, p) in routes if m == meth and (p == e or re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", p), e))), None)
                    if path and (meth, path) not in used:
                        lines.append(endpoint_md(meth, path, spec, comps, routes[(meth, path)]) + "\n")
                        used.add((meth, path))
            lines.append("")
    lines.append("## Contents\n")
    lines.append("- Analysis ▸ Tools: " + ", ".join(g for g, _ in tool_groups))
    for k in ("agri", "embed", "forecast", "sar", "hydro", "library", "online"):
        lines.append(f"- {MENU_PATH[k]}")
    lines.append("- [Models and their parameters](#models-and-their-parameters)\n- [Other endpoints](#other-endpoints)\n")
    lines.append("## Analysis ▸ Tools\n")
    placed = set()
    for g, ids in tool_groups:
        section(g, ids)
        placed |= set(ids)
    more = [t["id"] for t in tl if not t["menu"] and t["id"] not in placed and t["id"] not in ("home",)]
    section("More", more)
    for k in ("agri", "embed", "forecast", "sar", "hydro", "library", "online"):
        lines.append(f"## {MENU_PATH[k]}\n")
        if k in menu_groups:
            for g, ids in menu_groups[k]:
                section(g, ids)
            rest = [t["id"] for t in tl if t["menu"] == k and not any(t["id"] in ids for _, ids in menu_groups[k])]
            section("Other", rest)
        else:
            section({"online": "Online map layer & field collection"}.get(k, MENU_PATH[k].split(" ▸ ")[-1]), [t["id"] for t in tl if t["menu"] == k])
    lines.append(models_md())
    lines.append("\n## Other endpoints\n\nUsed by the app itself (Contents, History, projects, files…), Workflows and the Assistant.\n")
    for (meth, path), doc in sorted(routes.items(), key=lambda kv: kv[0][1]):
        if (meth, path) in used:
            continue
        lines.append("#### " + endpoint_md(meth, path, spec, comps, doc) + "\n")
    text = "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"
    OUT.write_text(text, encoding="utf-8")
    n_tools = len(tl)
    n_params = text.count("\n| `")
    print(f"{OUT.relative_to(ROOT)}: {n_tools} tools, {len(routes)} endpoints, {n_params} parameter rows, {len(text) // 1024} KB")


if __name__ == "__main__":
    main()
