"""The crop list (labels, test results) and the Q&A knowledge base behind the crop disease guide. No PyTorch needed."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .labels import label_display

DATA = Path(__file__).resolve().parent / "data"

# knowledge-base categories (about 40 in the data) → the guide's sections
SECTIONS = {
    "symptoms": ("Symptoms & identification", ("identification", "symptom", "information", "biology", "impact", "epidemiology",
                                               "spread", "predisposition", "disorder", "deficiency", "toxicity")),
    "management": ("Management & treatment", ("disease_management", "ipm", "integrated", "control", "plant_health", "biological")),
    "pests": ("Pests", ("pest_",)),
    "growing": ("Growing the crop", ("cultivation", "cultural", "nutri", "irrigation", "varietal", "weed", "crop_management",
                                     "cropping", "orchard", "harvest", "postharvest", "post_harvest", "technology")),
}


def section_of(category: str | None) -> str:
    c = (category or "").lower()
    for key, (_, words) in SECTIONS.items():
        if any(w in c for w in words):
            return key
    return "growing"


@lru_cache(maxsize=1)
def meta() -> dict:
    return json.loads((DATA / "crops.json").read_text(encoding="utf-8"))


def crops() -> dict:
    return meta()["crops"]


@lru_cache(maxsize=64)
def records(crop: str) -> tuple:
    if crop not in crops():
        raise KeyError(crop)
    f = DATA / "kb" / f"{crop}.json"
    recs = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
    out, seen = [], set()
    for r in recs:   # the knowledge base repeats some questions with near-identical answers: keep the first
        key = (r.get("disease", "").lower(), re.sub(r"\W+", " ", r.get("question", "").lower()).strip())
        if key in seen:
            continue
        seen.add(key)
        out.append({**r, "section": section_of(r.get("category"))})
    return tuple(out)


def schema() -> dict:
    m = meta()
    return {
        "crops": {k: {f: c[f] for f in ("name", "aliases", "kind", "limited_kb", "classes", "labels", "accuracy", "macro_f1", "test_images", "kb_records")}
                  for k, c in m["crops"].items()},
        "detectors": {k: {f: d.get(f) for f in ("accuracy", "macro_f1", "test_images")} | {"crops": len(d["classes"])} for k, d in m["detectors"].items()},
        "recognised_only": m["recognised_only"],
        "thresholds": m["thresholds"],
        "sections": {k: v[0] for k, v in SECTIONS.items()},
        "source": m["source"],
    }


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def diseases(crop: str) -> list[dict]:
    """The crop's diseases / pests: first those the photo model detects (with their linked records), then the others in the
    knowledge base, each with its record count per section."""
    c = crops()[crop]
    recs = records(crop)
    out, by_name = [], {}
    for label, name in zip(c["classes"], c["labels"]):
        out.append({"name": name, "label": label, "in_model": True, "healthy": "healthy" in label.lower(), "sections": {},
                    "f1": c.get("per_class", {}).get(label, {}).get("f1")})
        by_name[_norm(name)] = out[-1]
    for r in recs:
        d = _disease_of(crop, r, by_name)
        if d is None:
            name = r.get("disease") or "General"
            d = by_name.setdefault(_norm(name), {"name": name, "label": None, "in_model": False, "healthy": False, "sections": {}, "f1": None})
            if d not in out:
                out.append(d)
        d["sections"][r["section"]] = d["sections"].get(r["section"], 0) + 1
    for d in out:
        d["records"] = sum(d["sections"].values())
    general = [d for d in out if not d["in_model"] and _norm(d["name"]) in ("general", "")]
    rest = [d for d in out if not d["in_model"] and d not in general]
    rest.sort(key=lambda d: (-d["records"], d["name"].lower()))
    return [d for d in out if d["in_model"]] + rest + general


def _disease_of(crop: str, r: dict, by_name: dict) -> dict | None:
    """A record's model class: its vision_label, or a disease name equal to a class's friendly name."""
    vl = r.get("vision_label")
    if vl:
        hit = by_name.get(_norm(label_display(crop, vl)))
        if hit:
            return hit
    return by_name.get(_norm(r.get("disease", "")))


def search(crop: str, disease: str | None = None, q: str | None = None, section: str | None = None, limit: int = 200) -> dict:
    """Records of one crop, optionally for one disease (its friendly name), one section, and matching words (all words must
    appear; ranked by how often and where)."""
    recs = records(crop)
    if disease:
        c = crops()[crop]
        model = {_norm(name): {"name": name} for name in c["labels"]}
        recs = [r for r in recs if _norm((_disease_of(crop, r, model) or {"name": r.get("disease") or "General"})["name"]) == _norm(disease)]
    if section:
        recs = [r for r in recs if r["section"] == section]
    words = [w for w in _norm(q or "").split() if len(w) > 1]
    if words:
        scored = []
        for r in recs:
            head, body = _norm(f"{r.get('disease', '')} {r.get('question', '')}"), _norm(f"{r.get('answer', '')} {r.get('retrieval_text', '')}")
            if all(w in head or w in body for w in words):
                scored.append((sum(3 * head.count(w) + body.count(w) for w in words), r))
        recs = [r for _, r in sorted(scored, key=lambda x: -x[0])]
    counts = {}
    for r in recs:
        counts[r["section"]] = counts.get(r["section"], 0) + 1
    return {"total": len(recs), "sections": counts, "records": [{k: v for k, v in r.items() if k != "retrieval_text"} for r in recs[:limit]]}
