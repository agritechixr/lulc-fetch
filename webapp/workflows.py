"""Workflows: a saved chain of tool runs that can be run again on new inputs (Analysis ▸ Tools ▸ Workflows).

A workflow is made from runs in the History: each step is a run's exact request (endpoint and settings). Inside the
settings, the files and areas a run used become the workflow's **inputs** (to change when it is run), unless an earlier
step made that file: then the step takes **that step's output** instead. Years and dates become inputs too.

The settings hold markers the app replaces when it runs a workflow:
  {"$in": "in1"}                               the value of input in1
  {"$step": 0, "ext": ".tif", "nth": 0}        the first .tif that step 0 made

Conditions (optional, per step):
  "when":   {"all": [condition, …], "otherwise": "skip" | "stop"}       run the step only if every condition holds
  "checks": [{"if": condition, "then": "warn" | "alert" | "stop", "message": "…"}, …]   after the step ran
A condition reads a value of an earlier step (or, in checks, of the step itself) and compares it:
  {"of": {"step": 0, "field": "cloud_pct"}, "op": "<", "value": 20}            a number in the step's result
  {"of": {"step": 1, "stat": "mean", "band": 1}, "op": "drop", "value": 0.1}  a statistic of its output file
ops: < <= > >= == != and "drop" / "rise" (fell / grew by at least `value` since the workflow's previous run).
Stats: mean, min, max, empty_pct (raster band), rows (table), features (vector). evaluate() decides them (the app
runs the steps; the server reads the values), and the values of every run are kept for the next one ("drop").

One JSON file per workflow in workflows/ of the app's folder, so they are there in every project.
"""

from __future__ import annotations

import copy
import json
import re
import time
import uuid
from pathlib import Path

from . import history
from . import workspace as ws

# workspace folders whose files are a run's data (an input, or an earlier step's output)
DATA_PATH = re.compile(r"^(uploads|downloads|tables|models|imports|exports|analysis|training_data)/", re.I)
AREA_KEYS = ("clip", "aoi", "geometry", "area")
VALUE_KEYS = ("year", "years", "date", "start", "end", "start_date", "end_date")
# a workflow only runs tools: never the app's own housekeeping (projects, files, credentials, history, add-ons)
BLOCKED = re.compile(r"^/api/(project|fs|files|history|errors|credentials|cache|dl/install|yolo/install|workflows|assistant)", re.I)
RASTER = (".tif", ".tiff", ".vrt", ".img", ".jp2")
TABLE = (".csv", ".tsv", ".parquet", ".xlsx", ".xls")
VECTOR = (".geojson", ".json", ".zip", ".shp", ".kml", ".kmz", ".gpkg")


def folder() -> Path:
    return ws.APP_DIR / "workflows"


def _file(wid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", wid or ""):
        raise ValueError("Not a workflow id")
    return folder() / f"{wid}.json"


def kind_of(path: str) -> str:
    p = path.lower()
    return "raster" if p.endswith(RASTER) else "table" if p.endswith(TABLE) else "vector" if p.endswith(VECTOR) else "file"


def check(wf: dict) -> dict:
    """A workflow as saved: name, description, inputs, steps (endpoint + settings); refuses what it may not run."""
    name = str(wf.get("name") or "").strip()[:120]
    if not name:
        raise ValueError("Give the workflow a name")
    steps = wf.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ValueError("A workflow needs at least one step")
    if len(steps) > 50:
        raise ValueError("A workflow can have up to 50 steps")
    out_steps = []
    for i, s in enumerate(steps):
        ep = str(s.get("endpoint") or "")
        if not ep.startswith("/api/") or BLOCKED.match(ep):
            raise ValueError(f"Step {i + 1} isn't a tool a workflow can run ({ep or 'no endpoint'})")
        if not isinstance(s.get("body"), dict):
            raise ValueError(f"Step {i + 1} has no settings")
        for ref in _refs(s["body"]):
            if "$step" in ref and not (isinstance(ref["$step"], int) and 0 <= ref["$step"] < i):
                raise ValueError(f"Step {i + 1} uses the output of a step that doesn't come before it")
        step = {"title": str(s.get("title") or f"Step {i + 1}")[:200], "kind": str(s.get("kind") or "")[:60], "endpoint": ep, "body": s["body"]}
        if s.get("when"):
            step["when"] = check_when(s["when"], i)
        if s.get("checks"):
            step["checks"] = check_checks(s["checks"], i)
        out_steps.append(step)
    inputs = []
    for p in wf.get("inputs") or []:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", str(p.get("id") or "")):
            raise ValueError("An input has no proper id")
        inputs.append({"id": p["id"], "label": str(p.get("label") or p["id"])[:120], "type": p.get("type") if p.get("type") in ("file", "area", "value") else "value",
                       "kind": str(p.get("kind") or "")[:20], "default": p.get("default")})
    used = {r["$in"] for s in out_steps for r in _refs(s["body"]) if "$in" in r}
    missing = used - {p["id"] for p in inputs}
    if missing:
        raise ValueError(f"The steps use inputs that aren't defined: {', '.join(sorted(missing))}")
    out = {"name": name, "description": str(wf.get("description") or "")[:2000], "inputs": inputs, "steps": out_steps,
           "stop_on_critical": bool(wf.get("stop_on_critical", True))}   # a result that can't be right stops the run
    lay = wf.get("layout")   # where the boxes are in the diagram (the editor's own positions)
    if isinstance(lay, dict):
        out["layout"] = {str(k)[:40]: [float(v[0]), float(v[1])] for k, v in list(lay.items())[:200]
                         if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)}
    return out


# ------------------------------------------------------------------ conditions
OPS = ("<", "<=", ">", ">=", "==", "!=", "drop", "rise")
STATS = ("mean", "min", "max", "empty_pct", "rows", "features")
FIELD = re.compile(r"[A-Za-z0-9_.\[\]-]{1,120}")


def check_condition(c: dict, step: int, own: bool) -> dict:
    """A condition as saved: what it reads (a field of a step's result, or a statistic of its file), op, value."""
    if not isinstance(c, dict):
        raise ValueError(f"Step {step + 1}: a condition must be an object")
    of = c.get("of") or {}
    n = of.get("step")
    if not isinstance(n, int) or not (0 <= n <= step if own else 0 <= n < step):
        raise ValueError(f"Step {step + 1}: a condition must read {'this step or an earlier one' if own else 'an earlier step'}")
    src = {"step": n}
    if of.get("field"):
        if not FIELD.fullmatch(str(of["field"])):
            raise ValueError(f"Step {step + 1}: “{of['field']}” isn't a result field (e.g. cloud_pct or summary.mean_change)")
        src["field"] = str(of["field"])
    elif of.get("stat") in STATS:
        src["stat"] = of["stat"]
        src["band"] = max(1, int(of.get("band") or 1))
    else:
        raise ValueError(f"Step {step + 1}: a condition reads a result field or a statistic ({', '.join(STATS)})")
    if c.get("op") not in OPS:
        raise ValueError(f"Step {step + 1}: the comparison must be one of {' '.join(OPS)}")
    try:
        v = float(c.get("value"))
    except (TypeError, ValueError):
        raise ValueError(f"Step {step + 1}: a condition needs a number to compare with") from None
    if c["op"] in ("drop", "rise") and v < 0:
        raise ValueError(f"Step {step + 1}: “falls by” and “rises by” take a positive number")
    return {"of": src, "op": c["op"], "value": v}


def check_when(w: dict, step: int) -> dict:
    conds = w.get("all") if isinstance(w, dict) else None
    if not conds or not isinstance(conds, list) or len(conds) > 10:
        raise ValueError(f"Step {step + 1}: “run only if” needs 1 to 10 conditions")
    return {"all": [check_condition(c, step, False) for c in conds], "otherwise": w.get("otherwise") if w.get("otherwise") in ("skip", "stop") else "skip"}


def check_checks(lst: list, step: int) -> list:
    if not isinstance(lst, list) or len(lst) > 10:
        raise ValueError(f"Step {step + 1}: up to 10 checks")
    return [{"if": check_condition(c.get("if") or {}, step, True), "then": c.get("then") if c.get("then") in ("warn", "alert", "stop") else "warn",
             "message": str(c.get("message") or "")[:300]} for c in lst]


def key_of(of: dict) -> str:
    return f"{of['step']}:{of['field']}" if of.get("field") else f"{of['step']}:{of['stat']}:{of.get('band', 1)}"


def label_of(of: dict) -> str:
    if of.get("field"):
        return f"step {of['step'] + 1}'s {of['field']}"
    what = {"mean": "mean", "min": "minimum", "max": "maximum", "empty_pct": "% empty", "rows": "rows", "features": "features"}[of["stat"]]
    return f"step {of['step'] + 1}'s {what}" + (f" (band {of['band']})" if of["stat"] in ("mean", "min", "max", "empty_pct") and of.get("band", 1) > 1 else "")


def _field(obj, path: str):
    for k in path.replace("[", ".").replace("]", "").split("."):
        if k == "":
            continue
        if isinstance(obj, list):
            obj = obj[int(k)] if k.lstrip("-").isdigit() and -len(obj) <= int(k) < len(obj) else None
        elif isinstance(obj, dict):
            obj = obj.get(k)
        else:
            return None
    return obj


def value_of(of: dict, steps: dict, describe=None):
    """The value a condition reads, from a step's result ({"result", "outs"}) or its first matching file."""
    st = steps.get(of["step"]) or steps.get(str(of["step"]))
    if st is None:
        return None
    if of.get("field"):
        v = _field(st.get("result") or {}, of["field"])
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None
    if describe is None:
        from .assistant_data import describe
    want = {"rows": "table", "features": "vector"}.get(of["stat"], "raster")
    for path in st.get("outs") or []:
        if kind_of(path) != want and not (want == "vector" and path.lower().endswith(".geojson")):
            continue
        d = describe(path)
        if of["stat"] in ("rows", "features"):
            v = d.get(of["stat"])
            return float(v) if isinstance(v, (int, float)) else None
        b = next((x for x in d.get("bands") or [] if x.get("band") == of.get("band", 1)), None)
        v = (b or {}).get(of["stat"])
        return float(v) if isinstance(v, (int, float)) else None
    return None


def compare(cur: float, op: str, value: float, prev: float | None = None) -> bool | None:
    if op in ("drop", "rise"):
        if prev is None:
            return None   # no earlier run to compare with
        return (prev - cur >= value) if op == "drop" else (cur - prev >= value)
    return {"<": cur < value, "<=": cur <= value, ">": cur > value, ">=": cur >= value, "==": cur == value, "!=": cur != value}[op]


def _num(v: float) -> str:
    return f"{v:.0f}" if abs(v) >= 100 else f"{v:.3g}"


def evaluate(conds: list[dict], steps: dict, previous: dict | None = None, describe=None) -> list[dict]:
    """Each condition decided: {ok: True / False / None (couldn't be decided), value, previous, text}."""
    out = []
    for c in conds:
        of, op, val = c["of"], c["op"], c["value"]
        cur = value_of(of, steps, describe)
        prev = (previous or {}).get(key_of(of))
        name = label_of(of)
        if cur is None:
            out.append({"ok": None, "value": None, "previous": prev, "key": key_of(of), "text": f"{name}: not found in its result"})
            continue
        ok = compare(cur, op, val, prev)
        if op in ("drop", "rise"):
            text = (f"{name} is {_num(cur)}: no earlier run to compare with" if prev is None else
                    f"{name} {'fell' if cur < prev else 'rose' if cur > prev else 'stayed'} {'' if cur == prev else f'from {_num(prev)} '}to {_num(cur)} "
                    f"({'a ' + ('fall' if op == 'drop' else 'rise') + ' of at least ' + _num(val) if ok else 'not a ' + ('fall' if op == 'drop' else 'rise') + ' of ' + _num(val)})")
        else:
            text = f"{name} = {_num(cur)} ({'' if ok else 'not '}{op} {_num(val)})"
        out.append({"ok": ok, "value": cur, "previous": prev, "key": key_of(of), "text": text})
    return out


def _values_file(wid: str) -> Path:
    return folder() / "values" / f"{_file(wid).stem}.json"


def previous_values(wid: str) -> dict:
    try:
        return json.loads(_values_file(wid).read_text(encoding="utf-8")).get("values", {})
    except (OSError, ValueError):
        return {}


def record_values(wid: str, values: dict) -> None:
    """The values this run read, kept for the next run's “falls by / rises by” conditions."""
    if not values:
        return
    f = _values_file(wid)
    f.parent.mkdir(parents=True, exist_ok=True)
    old = previous_values(wid)
    old.update({str(k)[:80]: float(v) for k, v in values.items() if isinstance(v, (int, float))})
    f.write_text(json.dumps({"values": old, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1), encoding="utf-8")


def _refs(node):
    if isinstance(node, dict):
        if "$in" in node or "$step" in node:
            yield node
        else:
            for v in node.values():
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def listing() -> list[dict]:
    out = []
    for f in sorted(folder().glob("*.json")) if folder().is_dir() else []:
        try:
            w = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"id": f.stem, "name": w.get("name"), "description": w.get("description", ""), "steps": len(w.get("steps") or []),
                    "inputs": len(w.get("inputs") or []), "updated": w.get("updated"), "titles": [s.get("title") for s in w.get("steps") or []][:8]})
    return sorted(out, key=lambda w: w.get("updated") or "", reverse=True)


def get(wid: str) -> dict:
    f = _file(wid)
    if not f.is_file():
        raise FileNotFoundError("No such workflow")
    return {"id": wid, **json.loads(f.read_text(encoding="utf-8"))}


def save(wid: str | None, wf: dict) -> dict:
    data = check(wf)
    wid = wid or uuid.uuid4().hex[:10]
    f = _file(wid)
    old = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    data.update(app="LULC Fetch", version=1, created=old.get("created", now), updated=now)
    folder().mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(f)
    return {"id": wid, **data}


def delete(wid: str):
    _file(wid).unlink(missing_ok=True)


# ------------------------------------------------------------------ making a workflow from History runs

def _label(key_path: list, kind: str) -> str:
    keys = [k for k in key_path if isinstance(k, str)]
    k = (keys[-2] if keys and keys[-1] == "path" and len(keys) > 1 else keys[-1] if keys else "input").replace("_", " ")
    nice = {"path": f"Input {kind}" if kind != "file" else "Input file", "clip": "Area", "aoi": "Area of interest", "geometry": "Area",
            "ground truth": "Ground truth", "table": "Table", "model": "Model", "photos": "Photos"}
    return nice.get(k, k[:1].upper() + k[1:])


def from_history(job_ids: list[str], name: str = "") -> dict:
    """A workflow from History runs, in the order they ran: their settings, with files and areas turned into inputs or
    into earlier steps' outputs. Not saved: the app shows it to be named and saved."""
    runs = []
    for jid in job_ids:
        req, entry = history.get_request(jid), history.get(jid)
        if req is None or entry is None:
            raise ValueError("One of the runs can't be repeated (its settings weren't kept), so it can't be part of a workflow")
        if BLOCKED.match(req.get("endpoint") or ""):
            raise ValueError(f"“{entry.get('title')}” isn't a tool a workflow can run")
        runs.append((entry.get("started") or 0, jid, req, entry))
    runs.sort(key=lambda r: r[0])
    inputs, steps, made = [], [], []   # made[i] = absolute paths step i wrote

    def add_input(**p):
        same = next((x for x in inputs if x["type"] == p["type"] and x["default"] == p["default"]), None)
        if same:
            return same["id"]
        p["id"] = f"in{len(inputs) + 1}"
        inputs.append(p)
        return p["id"]

    for _, jid, req, entry in runs:
        root = Path(req.get("workspace") or ws.root())

        def walk(node, kp):
            if isinstance(node, dict):
                is_area = bool(kp) and kp[-1] in AREA_KEYS and ("coordinates" in node or node.get("type") == "FeatureCollection")
                if is_area:   # an area (a polygon, or polygons): an input
                    return {"$in": add_input(type="area", kind="area", label=_label(kp, "area"), default=node)}
                return {k: walk(v, kp + [k]) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v, kp + [i]) for i, v in enumerate(node)]
            if isinstance(node, str) and DATA_PATH.match(node):
                ab = str((root / node).resolve())
                for i in range(len(made) - 1, -1, -1):   # made by an earlier step: that step's output
                    if ab in made[i]:
                        ext = Path(node).suffix.lower()
                        same_ext = [p for p in made[i] if Path(p).suffix.lower() == ext]
                        return {"$step": i, "ext": ext, "nth": same_ext.index(ab)}
                k = kind_of(node)
                return {"$in": add_input(type="file", kind=k, label=_label(kp, k), default=node)}
            if kp and len(kp) == 1 and kp[0] in VALUE_KEYS and isinstance(node, (int, float, str)) and node != "":
                return {"$in": add_input(type="value", kind=kp[0], label=kp[0].replace("_", " ").capitalize(), default=node)}
            return node

        body = walk(copy.deepcopy(req.get("body") or {}), [])
        steps.append({"title": entry.get("title") or req["endpoint"], "kind": entry.get("kind") or "", "endpoint": req["endpoint"], "body": body})
        made.append([str(Path(p).resolve()) for p in entry.get("outputs") or []])
    titles = [s["title"].split(" · ")[0] for s in steps]
    return {"name": name or " → ".join(dict.fromkeys(titles))[:120], "description": "", "inputs": inputs, "steps": steps}


# ------------------------------------------------------------------ schedules
# A workflow can run by itself while the app is open: every few hours, daily or weekly at a time. Runs missed while the
# app was closed run once when it opens. Date inputs can move with the run day ("30 days before"). Kept beside the
# workflows (schedules/schedules.json), so saving a workflow doesn't touch its schedule.
def _sched_file() -> Path:
    return folder() / "schedules" / "schedules.json"


def _sched_all() -> dict:
    try:
        return json.loads(_sched_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sched_write(data: dict) -> None:
    f = _sched_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)


def next_run(s: dict, after: float) -> float:
    """The next time (epoch seconds, local time) the schedule runs after `after`."""
    import datetime as dt
    if s["every"] == "hours":
        return after + 3600 * max(1, int(s.get("hours") or 24))
    hh, mm = (int(x) for x in (s.get("at") or "07:00").split(":"))
    t = dt.datetime.fromtimestamp(after).replace(hour=hh, minute=mm, second=0, microsecond=0)
    if s["every"] == "day":
        if t.timestamp() <= after:
            t += dt.timedelta(days=1)
        return t.timestamp()
    wd = int(s.get("weekday", 0))   # 0 = Monday
    t += dt.timedelta(days=(wd - t.weekday()) % 7)
    if t.timestamp() <= after:
        t += dt.timedelta(days=7)
    return t.timestamp()


def check_schedule(s: dict) -> dict:
    every = s.get("every")
    if every not in ("hours", "day", "week"):
        raise ValueError("every: hours, day or week")
    out = {"every": every, "enabled": bool(s.get("enabled", True)), "notify": s.get("notify") if s.get("notify") in ("always", "fail", "alert") else "always",
           "add_results": bool(s.get("add_results", True)), "catch_up": bool(s.get("catch_up", True))}
    if every == "hours":
        h = int(s.get("hours") or 0)
        if not 1 <= h <= 24 * 30:
            raise ValueError("Every 1 to 720 hours")
        out["hours"] = h
    else:
        at = str(s.get("at") or "")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
            raise ValueError("The time is HH:MM, e.g. 07:30")
        out["at"] = at
        if every == "week":
            wd = int(s.get("weekday", -1))
            if not 0 <= wd <= 6:
                raise ValueError("The weekday is 0 (Monday) to 6 (Sunday)")
            out["weekday"] = wd
    days = {}   # date inputs that move with the run day: input id → days before it
    for k, v in (s.get("relative_dates") or {}).items():
        if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(k)) and str(v).lstrip("-").isdigit() and 0 <= int(v) <= 3650:
            days[str(k)] = int(v)
    out["relative_dates"] = days
    out["inputs"] = {str(k): v for k, v in (s.get("inputs") or {}).items() if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(k)) and isinstance(v, str) and len(v) < 500}
    a = s.get("alert") or None   # alert when a value of the last step's result crosses a limit
    if a:
        if not re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,120}", str(a.get("field") or "")) or a.get("op") not in (">", "<", ">=", "<=") or not isinstance(a.get("value"), (int, float)):
            raise ValueError("The alert needs a result field (e.g. summary.mean_change), > or <, and a number")
        out["alert"] = {"field": a["field"], "op": a["op"], "value": float(a["value"])}
    return out


def set_schedule(wid: str, s: dict) -> dict:
    get(wid)   # the workflow exists
    data, now = _sched_all(), time.time()
    old = data.get(wid, {})
    sch = check_schedule(s)
    sch.update(last_run=old.get("last_run"), runs=old.get("runs", []), next_run=next_run(sch, now))
    data[wid] = sch
    _sched_write(data)
    return {"id": wid, **sch}


def delete_schedule(wid: str) -> None:
    data = _sched_all()
    if data.pop(wid, None) is not None:
        _sched_write(data)


def schedules() -> list[dict]:
    """Every schedule, with its workflow's name and whether it is due now (a missed run is due once)."""
    data, now, out, changed = _sched_all(), time.time(), [], False
    names = {w["id"]: w["name"] for w in listing()}
    for wid, s in list(data.items()):
        if wid not in names:   # the workflow was deleted
            data.pop(wid); changed = True
            continue
        due = s.get("enabled") and (s.get("next_run") or 0) <= now
        if due and not s.get("catch_up", True) and now - s["next_run"] > 3600:   # missed long ago, not caught up: skip to the next one
            s["next_run"] = next_run(s, now); changed = True; due = False
        out.append({"id": wid, "name": names[wid], **s, "due": bool(due)})
    if changed:
        _sched_write(data)
    return sorted(out, key=lambda s: s.get("next_run") or 0)


def mark_ran(wid: str, ok: bool, message: str = "", alert: bool = False) -> dict:
    data = _sched_all()
    s = data.get(wid)
    if not s:
        raise FileNotFoundError("No schedule for this workflow")
    now = time.time()
    s["last_run"] = now
    s["next_run"] = next_run(s, now)
    s["runs"] = ([{"t": now, "ok": bool(ok), "alert": bool(alert), "message": str(message)[:500]}] + s.get("runs", []))[:20]
    _sched_write(data)
    return {"id": wid, **s}


# ------------------------------------------------------------------ cautions before running
def cautions(wf: dict, saved: bool = True) -> list[str]:
    """What may go wrong when the workflow runs, found from its settings alone (shown before running; nothing is
    refused): cloudy imagery allowed, very large areas, a step comparing a layer with itself, conditions that can't
    decide on a first run, outputs nothing uses."""
    out = []
    inputs = {i["id"]: i for i in wf.get("inputs") or []}

    def val(v):
        return inputs[v["$in"]].get("default") if isinstance(v, dict) and "$in" in v and v["$in"] in inputs else v
    for n, s in enumerate(wf.get("steps") or []):
        b, ep, name = s.get("body") or {}, s.get("endpoint", ""), f"Step {n + 1} ({s.get('title', '')})"
        if ep == "/api/jobs" and b.get("kind") in ("scene", "composite"):
            mc = val(b.get("max_cloud", 40))
            gated = any(c["of"].get("field") == "cloud_pct" and c["of"]["step"] == n for later in wf["steps"][n + 1:]
                        for c in (later.get("when") or {}).get("all", []))
            if isinstance(mc, (int, float)) and mc >= 50 and not gated:
                out.append(f"{name} accepts imagery up to {mc:g}% cloudy: add “run only if cloud_pct < 20” to the next step, or lower the cloud limit")
            aoi = val(b.get("aoi"))
            if isinstance(aoi, dict) and aoi.get("type") in ("Polygon", "MultiPolygon"):
                try:
                    from shapely.geometry import shape
                    g = shape(aoi)
                    lat = abs(g.centroid.y)
                    km2 = g.area * 111.32 ** 2 * max(0.05, __import__("math").cos(__import__("math").radians(lat)))
                    if km2 > 5000:
                        out.append(f"{name} downloads about {km2:,.0f} km² at {b.get('res', 10)} m: a large, slow download (clip to the area you need)")
                except Exception:   # noqa: BLE001 (an area that can't be measured is checked when it runs)
                    pass
        pair = [b.get(k) for k in ("before", "after")] if "before" in b and "after" in b else None
        if pair and json.dumps(pair[0], sort_keys=True) == json.dumps(pair[1], sort_keys=True):
            out.append(f"{name} compares the same file with itself: the result will show no change")
        for c in (s.get("when") or {}).get("all", []) + [k["if"] for k in s.get("checks") or []]:
            if c["op"] in ("drop", "rise") and not saved:
                out.append(f"{name}: “{'falls' if c['op'] == 'drop' else 'rises'} by” compares with the previous run: save the workflow, and the first run only records the value")
    used = {r["$step"] for s in wf.get("steps") or [] for r in _refs(s.get("body")) if "$step" in r}
    used |= {c["of"]["step"] for s in wf.get("steps") or [] for c in (s.get("when") or {}).get("all", []) + [k["if"] for k in s.get("checks") or []]}
    last = len(wf.get("steps") or []) - 1
    idle = [n + 1 for n in range(last) if n not in used]
    if idle and last > 0:
        out.append(f"Step{'s' if len(idle) > 1 else ''} {', '.join(map(str, idle))} make{'' if len(idle) > 1 else 's'} files no later step uses "
                   "(fine if you want them; tick “Add every step's results” to see them)")
    return list(dict.fromkeys(out))
