"""Crop disease diagnosis: a Hugging Face Space.

A leaf photo → the crop (two crop detectors) → its disease (that crop's model), with the photo check, exactly as in
LULC Fetch (Agri ▸ Diagnose crop disease): this Space runs LULC Fetch's own code (lulc_fetch/agri, copied in by
`python -m lulc_fetch.agri.publish --space`). Models download from ixrbhii/multicrop-disease-models the first time each
is needed. Photos are only held in memory / a temporary file while they are diagnosed; nothing is stored.

API (used by LULC Fetch): POST /gradio_api/call/diagnose with {"data": [<base64 JPEG>, <crop key or "auto">, <strict>]}.
"""

import base64
import os
import tempfile
import threading

import gradio as gr

from lulc_fetch.agri import disease, knowledge

MODELS_DIR = os.environ.get("AGRI_MODELS", "/tmp/agri_models")
models = disease.Models(disease.hub_models(MODELS_DIR), "cpu", keep=int(os.environ.get("AGRI_KEEP", "8")))
lock = threading.Lock()   # one photo at a time: the models are shared
TH = knowledge.meta()["thresholds"]
CROPS = sorted(knowledge.crops().items(), key=lambda kv: kv[1]["name"])
CHOICES = [("Detect the crop from the photo", "auto")] + [
    (c["name"] + (f" ({', '.join(c['aliases'][:3])})" if c["aliases"] else ""), k) for k, c in CROPS]


def run(path: str, crop: str = "auto", strict: bool = True) -> dict:
    if crop != "auto" and crop not in knowledge.crops():
        raise gr.Error(f"Unknown crop {crop}")
    with lock:
        r = disease.diagnose_one(models, path, crop, TH, bool(strict))
    for k in ("path", "file"):
        r.pop(k, None)
    return r


def diagnose(image_b64: str, crop: str = "auto", strict: bool = True) -> dict:
    """Diagnose one leaf photo (a base64-encoded JPEG or PNG). Returns the crop, the top-3 diseases with confidence, and the
    photo check's verdict (status: disease / healthy / variety / retake / no_model / error)."""
    data = base64.b64decode(image_b64.split(",")[-1])
    if len(data) > 15_000_000:
        raise gr.Error("Photo too large (over 15 MB): send it at most 2048 pixels wide")
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        f.write(data)
        f.flush()
        return run(f.name, crop, strict)


STATUS = {"disease": "🔴 Disease", "healthy": "🟢 Healthy", "variety": "🟢 Variety", "retake": "🟠 Retake the photo",
          "no_model": "⚪ No model for this crop", "error": "⚪ Couldn't read the photo"}


def ui(path, crop, strict):
    if not path:
        raise gr.Error("Add a leaf photo first")
    r = run(path, crop or "auto", strict)
    top = {t["name"]: t["conf"] for t in r.get("top", [])}
    lines = [f"### {STATUS.get(r['status'], r['status'])}"]
    if r.get("crop_name"):
        lines.append(f"**Crop:** {r['crop_name']}" + (f" ({100 * r['crop_conf']:.0f} % sure)" if r.get("crop_conf") is not None else ""))
    if r.get("diagnosis"):
        lines.append(f"**Diagnosis:** {r['diagnosis']} ({100 * r['conf']:.0f} % sure)")
    reasons = [r["reason"]] if r.get("reason") else []
    reasons += r.get("warnings", [])
    if reasons:
        lines.append("**Photo check:** " + "; ".join(disease.ISSUE_TEXT.get(x, x) for x in reasons) +
                     ". Take one leaf, filling most of the photo, in daylight and in focus.")
    if r.get("note"):
        lines.append(r["note"])
    if r["status"] == "disease":
        found = knowledge.search(r["crop"], disease=r["diagnosis"], limit=50)
        for sec, title in (("symptoms", "Symptoms"), ("management", "Management & treatment")):
            recs = [x for x in found["records"] if x["section"] == sec][:3]
            if recs:
                lines.append(f"#### {title}")
                lines += [f"**{x['question']}**  \n{x['answer']}" for x in recs]
        if knowledge.crops()[r["crop"]]["limited_kb"]:
            lines.append("_This crop's guide only describes symptoms: for treatment, ask your local agriculture office._")
    lines.append("<sub>A diagnosis supports, but doesn't replace, a local agriculture expert.</sub>")
    return top, "\n\n".join(lines)


with gr.Blocks(title="Crop disease diagnosis") as demo:
    gr.Markdown("# 🌿 Crop disease diagnosis from a leaf photo\n"
                "42 crops, from apple and mango to rice, maize, tomato and sugarcane. One leaf per photo, in daylight and in focus. "
                "Models: [collection](https://huggingface.co/collections/ixrbhii/multi-crop-disease-models-42-crops-6ac0a3291f2153fa716e2993) · "
                "answers: [crop-disease-qa](https://huggingface.co/datasets/ixrbhii/crop-disease-qa) · "
                "also in the desktop app [LULC Fetch](https://agritechixr.github.io/lulc-fetch/).")
    with gr.Row():
        with gr.Column():
            photo = gr.Image(type="filepath", label="Leaf photo", sources=["upload", "webcam", "clipboard"])
            crop = gr.Dropdown(CHOICES, value="auto", label="Crop", filterable=True)
            strict = gr.Checkbox(True, label="Refuse unclear photos (recommended)")
            go = gr.Button("Diagnose", variant="primary")
        with gr.Column():
            top = gr.Label(num_top_classes=3, label="Most likely")
            text = gr.Markdown()
    go.click(ui, [photo, crop, strict], [top, text], api_name=False)
    gr.api(diagnose, api_name="diagnose")

demo.queue(default_concurrency_limit=1).launch()
