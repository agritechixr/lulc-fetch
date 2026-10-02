"""HTML report for a deep-learning segmentation model: one self-contained file (inline SVG charts and PNG examples).

Sections: headline scores, training curves (loss, mIoU, accuracy, learning rate) with the best / early-stop epoch,
confusion matrix (counts and row %), per-class IoU / F1 / precision / recall, example predictions
(image | ground truth | prediction), dataset and settings.
"""

from __future__ import annotations

import base64
import datetime as _dt
import io
from pathlib import Path

import numpy as np

from .evaluation import _CSS, _PAL, _Axes, _fmt, _pct, _section, _tile, esc, grouped_bars, hbars, heatmap

_EXTRA_CSS = """
.ex{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}.ex figure{margin:0;background:var(--panel2);border:1px solid var(--border);border-radius:10px;padding:8px}
.ex .trio{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}.ex img{width:100%;image-rendering:pixelated;border-radius:4px;display:block}
.ex .cap{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;font-size:11px;color:var(--muted);text-align:center;margin-top:3px}
.ex figcaption{font-size:11.5px;color:var(--muted);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.leg2{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;margin-bottom:8px}.leg2 i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:4px;vertical-align:-1px}
"""


def _png(arr: np.ndarray) -> str:
    """(3, h, w) uint8 → data URL."""
    from PIL import Image
    im = Image.fromarray(np.moveaxis(arr, 0, -1))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _curve(history, keys, ylabel, best_epoch=None, stop_epoch=None, ylim=None):
    vmax = max((abs(h.get(k) or 0) for h in history for k, _, _ in keys), default=0)
    if 0 < vmax < 0.05:   # tiny values (learning rate): show them scaled so the ticks are readable
        import math
        scale = 10 ** math.floor(math.log10(vmax))
        history = [{**h, **{k: (h[k] / scale if h.get(k) is not None else None) for k, _, _ in keys}} for h in history]
        ylabel = f"{ylabel} (× {scale:g})"
    xs = [h["epoch"] for h in history]
    vals = [v for k, _, _ in keys for v in (h.get(k) for h in history) if v is not None]
    if not xs or not vals:
        return '<p class="note">No data.</p>'
    lo, hi = (ylim if ylim else (min(vals), max(vals)))
    if not ylim:
        pad = (hi - lo) * 0.08 or 0.05
        lo, hi = lo - pad, hi + pad
    ax = _Axes((min(xs), max(max(xs), min(xs) + 1)), (lo, hi), xlabel="Epoch", ylabel=ylabel)
    for k, name, color in keys:
        pts = [(h["epoch"], h[k]) for h in history if h.get(k) is not None]
        if pts:
            ax.line([p[0] for p in pts], [p[1] for p in pts], color, 2, title=name)
            ax.points([p[0] for p in pts], [p[1] for p in pts], color, r=2.2, opacity=0.9,
                      titles=[f"{name} · epoch {e}: {_fmt(v, 4)}" for e, v in pts])
    for e, label, color in ((best_epoch, "best", "#16a34a"), (stop_epoch, "stopped", "#dc2626")):
        if e:
            x = ax.X(e)
            ax.parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{ax.t}" y2="{ax.h - ax.b}" stroke="{color}" stroke-dasharray="4 3" stroke-width="1.3">'
                            f'<title>{label}: epoch {e}</title></line><text x="{x + 3:.1f}" y="{ax.t + 11}" font-size="10.5" fill="{color}">{label}</text>')
    return ax.svg(legend=[(n, c) for _, n, c in keys])


def build_html(config: dict, history: list[dict], examples: list[dict]) -> str:
    classes = [c.get("name") or str(c["value"]) for c in config["classes"]]
    colors = [c.get("color") or _PAL[i % len(_PAL)] for i, c in enumerate(config["classes"])]
    ev_name, ev = ("test", config["test"]) if config.get("test") else ("validation", config["val"])
    P = config.get("params") or {}
    ds = config.get("dataset") or {}
    best_e = config.get("best_epoch")
    stop_e = len(history) if config.get("stopped") else None
    s = []
    tiles = [_tile("mIoU", _pct(ev["miou"]), "Mean intersection-over-union over classes: the main segmentation score.", _grade(ev["miou"], .7, .5)),
             _tile("Pixel accuracy", _pct(ev["accuracy"]), "Share of labelled pixels classified correctly.", _grade(ev["accuracy"], .9, .8)),
             _tile("Macro F1", _pct(ev["f1_macro"]), "Mean F1 over classes; rare classes count as much as common ones.", _grade(ev["f1_macro"], .8, .6)),
             _tile("Kappa", _fmt(ev["kappa"]), "Agreement beyond chance: >0.8 excellent, 0.6–0.8 good."),
             _tile("Best epoch", f"{best_e} / {len(history)}", "The epoch whose weights were kept (best validation score)."),
             _tile("Training time", _dur(config.get("seconds")), f"On {config.get('device')}")]
    if config.get("test") and config.get("val"):
        tiles.append(_tile("Validation mIoU", _pct(config["val"]["miou"]), "Score on the validation patches (used for early stopping)."))
    s.append(_section(f"Scores on the {ev_name} patches", f'<div class="tiles">{"".join(tiles)}</div>'
                      + (f'<div class="warn">{esc(config["weights_note"])}</div>' if config.get("weights_note") else "")
                      + (f'<div class="warn">Training {esc(config["stopped"])} after {len(history)} epochs; the best epoch ({best_e}) was kept.</div>' if config.get("stopped") else ""),
                      wide=True))
    s.append(_section("Loss", _curve(history, [("train_loss", "Training", "#2563eb"), ("val_loss", "Validation", "#f59e0b")], "Loss", best_e, stop_e),
                      "Both should fall. If validation loss rises while training loss keeps falling, the model is overfitting (early stopping catches this)."))
    s.append(_section("mIoU", _curve(history, [("train_miou", "Training", "#2563eb"), ("val_miou", "Validation", "#f59e0b")], "mIoU", best_e, stop_e, (0, 1)),
                      "Mean intersection-over-union per epoch (higher is better)."))
    s.append(_section("Pixel accuracy", _curve(history, [("train_acc", "Training", "#2563eb"), ("val_acc", "Validation", "#f59e0b")], "Accuracy", best_e, stop_e, (0, 1))))
    s.append(_section("Learning rate", _curve(history, [("lr", "Learning rate", "#7c3aed")], "Learning rate", best_e, stop_e),
                      f"Schedule: {esc(P.get('scheduler'))} · optimiser {esc(P.get('optimizer'))}."))
    cm = np.array(ev["confusion"])
    s.append(_section(f"Confusion matrix ({ev_name}, pixels)", heatmap(cm, classes, classes, colors=colors),
                      "Rows = true class, columns = predicted class; the diagonal (green) is correct. PA = producer's accuracy (recall), UA = user's accuracy (precision).", wide=True))
    s.append(_section("Confusion matrix (row %)", heatmap(cm, classes, classes, normalise=True, colors=colors), wide=True))
    iou = [v if v is not None else 0 for v in ev["iou"]]
    s.append(_section("IoU per class", hbars(classes, iou, colors=colors),
                      "Intersection over union for each class. Classes with few training pixels are usually the weakest."))
    s.append(_section("Precision · recall · F1 per class", grouped_bars(classes, [
        ("Precision", [v or 0 for v in ev["precision"]], "#2563eb"), ("Recall", [v or 0 for v in ev["recall"]], "#f59e0b"),
        ("F1", [v or 0 for v in ev["f1"]], "#16a34a")])))
    rows = "".join(f'<tr><td class="l"><i class="sw" style="background:{colors[i]}"></i>{esc(classes[i])}</td><td>{esc(config["classes"][i]["value"])}</td>'
                   f'<td>{_pct(ev["iou"][i])}</td><td>{_pct(ev["f1"][i])}</td><td>{_pct(ev["precision"][i])}</td><td>{_pct(ev["recall"][i])}</td>'
                   f'<td>{int(ev["support"][i]):,}</td><td>{int((ds.get("class_pixels_train") or [0] * len(classes))[i]):,}</td></tr>' for i in range(len(classes)))
    s.append(_section("Per-class table", f'<div class="scroll"><table class="tbl"><tr><th class="l">Class</th><th>Label</th><th>IoU</th><th>F1</th>'
                                         f'<th>Precision</th><th>Recall</th><th>{ev_name.title()} pixels</th><th>Training pixels</th></tr>{rows}</table></div>', wide=True))
    if examples:
        leg = '<div class="leg2">' + "".join(f'<span><i style="background:{colors[i]}"></i>{esc(c)}</span>' for i, c in enumerate(classes)) + '<span><i style="background:#000"></i>no label</span></div>'
        figs = "".join(f'<figure><div class="trio"><img src="{_png(e["rgb"])}" alt="image"><img src="{_png(e["label"])}" alt="ground truth">'
                       f'<img src="{_png(e["pred"])}" alt="prediction"></div><div class="cap"><span>Image</span><span>Ground truth</span><span>Prediction</span></div>'
                       f'<figcaption title="{esc(e["file"])}">{esc(e["file"])}{f" · {_pct(e["acc"])} correct" if e.get("acc") is not None else ""}</figcaption></figure>' for e in examples)
        s.append(_section(f"Example predictions ({ev_name} patches)", leg + f'<div class="ex">{figs}</div>', wide=True))
    kv = [("Architecture", f'{config["arch_title"]} · backbone {config["encoder_title"]} · {"ImageNet-pretrained" if config.get("pretrained") else "random start"}'),
          ("Input", f'{config["in_channels"]} bands: {", ".join(map(str, config["bands"][:30]))}{" …" if len(config["bands"]) > 30 else ""}'),
          ("Patch size", f'{config["patch_size_px"][0]} × {config["patch_size_px"][1]} px'),
          ("Dataset", f'{esc(ds.get("name"))} · {ds.get("patches")} patches → {ds.get("train")} training / {ds.get("val")} validation / {ds.get("test")} test'
                      f'{f" · {ds.get("purged")} overlapping training patches dropped" if ds.get("purged") else ""}'),
          ("Split", "spatial blocks (neighbouring patches kept together)" if ds.get("split") == "blocks" else "random patches"),
          ("Epochs", f'{len(history)} run (max {P.get("epochs")}) · early stopping {"on, patience " + str(P.get("patience")) + " on " + str(P.get("monitor")) if P.get("early_stop") else "off"}'),
          ("Batch size", f'{P.get("batch_size")}{f" (lowered to {config.get("final_batch_size")} for memory)" if config.get("final_batch_size") and config.get("final_batch_size") != P.get("batch_size") else ""}'),
          ("Learning rate", f'{P.get("lr")} · {P.get("optimizer")} · weight decay {P.get("weight_decay")} · {P.get("scheduler")} schedule'),
          ("Loss", f'{P.get("loss")}{" · class weights " + ", ".join(f"{classes[i]} {w}" for i, w in enumerate(config["class_weights"])) if config.get("class_weights") else ""}'),
          ("Augmentation", ", ".join(P.get("augment") or []) or "none"),
          ("Device", f'{config.get("device")} · mixed precision {P.get("amp")}'),
          ("Seed", P.get("seed")), ("Trained", config.get("trained"))]
    s.append(_section("Model, data and settings", '<table class="kv">' + "".join(f"<tr><th>{esc(k)}</th><td>{v if isinstance(v, str) and k in ('Dataset',) else esc(v)}</td></tr>" for k, v in kv) + "</table>", wide=True))
    when = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    split_badge = ('<span class="badge honest">✓ Spatial-block split</span>' if ds.get("split") == "blocks"
                   else '<span class="badge opt">⚠ Random split: scores may be optimistic if patches overlap</span>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(config["name"])} · deep-learning report</title><style>{_CSS}{_EXTRA_CSS}</style></head><body>
<header><div class="toolbar"><button onclick="window.print()">Print / save as PDF</button></div>
<h1>{esc(config["arch_title"])} · {esc(config["name"])}</h1>
<div class="sub"><span class="badge task">semantic segmentation</span>{split_badge} mIoU {_pct(ev["miou"])} · accuracy {_pct(ev["accuracy"])} on the {ev_name} patches · {len(classes)} classes · generated {when}</div></header>
<main>{"".join(s)}</main>
<footer>LULC Fetch deep-learning report · hover over charts and cells for exact values</footer></body></html>"""


def _grade(v, good, ok):
    if v is None:
        return ""
    return "good" if v >= good else "ok" if v >= ok else "bad"


def _dur(sec):
    if sec is None:
        return "–"
    sec = float(sec)
    return f"{sec:.0f} s" if sec < 90 else f"{sec / 60:.1f} min" if sec < 5400 else f"{sec / 3600:.1f} h"


def write_report(path: str | Path, config: dict, history: list[dict], examples: list[dict]) -> Path:
    path = Path(path)
    path.write_text(build_html(config, history, examples), encoding="utf-8")
    return path
