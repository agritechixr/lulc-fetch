"""Model evaluation report: one self-contained .html file with metrics, matrices and charts.

Works for every model trained by :func:`lulc_fetch.ml.train` (classification and regression). Charts are
inline SVG with hover tooltips, so the file opens offline in any browser and can be emailed or archived.

Written automatically after training (``models/<name>.evaluation.html``). It can also be rebuilt from a
saved model, or used to test a saved model on another labelled table (an independent check)::

    python -m lulc_fetch.evaluation models/rf.joblib                  # rebuild the report
    python -m lulc_fetch.evaluation models/rf.joblib --table tables/other_area.csv -o other.html
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import math
from pathlib import Path

import numpy as np

_PAL = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#393b79", "#ad494a", "#637939", "#8c6d31", "#843c39", "#7b4173", "#3182bd", "#e6550d", "#31a354", "#756bb1"]
EVAL_KEEP = 20000  # test rows stored inside the model file so the report can be rebuilt later


def esc(v) -> str:
    return html.escape(str(v), quote=True)


def _fmt(v, digits=3) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    a = abs(v)
    if a != 0 and (a < 1e-3 or a >= 1e5):
        return f"{v:.{digits}g}"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{v:.0f}"


def _pct(v) -> str:
    return "–" if v is None else f"{100 * v:.1f}%"


# ------------------------------------------------------------------ SVG charts (no JavaScript)
class _Axes:
    """Maps data coordinates to an SVG plot area and draws axes, ticks and labels."""

    def __init__(self, xlim, ylim, w=460, h=320, xlabel="", ylabel="", left=56, bottom=42):
        self.w, self.h, self.l, self.b, self.t, self.r = w, h, left, bottom, 14, 14
        self.x0, self.x1 = xlim if xlim[1] > xlim[0] else (xlim[0] - 1, xlim[0] + 1)
        self.y0, self.y1 = ylim if ylim[1] > ylim[0] else (ylim[0] - 1, ylim[0] + 1)
        self.xlabel, self.ylabel, self.parts = xlabel, ylabel, []

    def X(self, v):
        return self.l + (v - self.x0) / (self.x1 - self.x0) * (self.w - self.l - self.r)

    def Y(self, v):
        return self.h - self.b - (v - self.y0) / (self.y1 - self.y0) * (self.h - self.b - self.t)

    @staticmethod
    def ticks(a, b, n=5):
        span = b - a
        step = 10 ** math.floor(math.log10(span / n)) if span > 0 else 1
        for m in (1, 2, 2.5, 5, 10):
            if span / (step * m) <= n:
                step *= m
                break
        start = math.ceil(a / step) * step
        return [start + i * step for i in range(int((b - start) / step + 1e-9) + 1)]

    def frame(self):
        out = []
        for v in self.ticks(self.y0, self.y1):
            y = self.Y(v)
            out.append(f'<line x1="{self.l}" x2="{self.w - self.r}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                       f'<text x="{self.l - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{_fmt(v, 2)}</text>')
        for v in self.ticks(self.x0, self.x1):
            x = self.X(v)
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{self.t}" y2="{self.h - self.b}" class="grid"/>'
                       f'<text x="{x:.1f}" y="{self.h - self.b + 16}" class="tick" text-anchor="middle">{_fmt(v, 2)}</text>')
        out.append(f'<rect x="{self.l}" y="{self.t}" width="{self.w - self.l - self.r}" height="{self.h - self.b - self.t}" class="axis"/>')
        out.append(f'<text x="{(self.l + self.w - self.r) / 2}" y="{self.h - 6}" class="lab" text-anchor="middle">{esc(self.xlabel)}</text>')
        out.append(f'<text transform="translate(14 {(self.t + self.h - self.b) / 2}) rotate(-90)" class="lab" text-anchor="middle">{esc(self.ylabel)}</text>')
        return "".join(out)

    def svg(self, legend=None):
        leg = ""
        if legend:
            leg = '<div class="legend">' + "".join(f'<span><i style="background:{c}"></i>{esc(n)}</span>' for n, c in legend) + "</div>"
        return (f'<svg viewBox="0 0 {self.w} {self.h}" class="chart" role="img">{self.frame()}{"".join(self.parts)}</svg>{leg}')

    def line(self, xs, ys, color, width=2, dash="", title=""):
        pts = " ".join(f"{self.X(x):.1f},{self.Y(y):.1f}" for x, y in zip(xs, ys))
        t = f"<title>{esc(title)}</title>" if title else ""
        self.parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width}" '
                          f'{f"stroke-dasharray={chr(34)}{dash}{chr(34)}" if dash else ""}>{t}</polyline>')

    def diag(self, a, b):
        self.line([a, b], [a, b], "var(--muted)", 1.2, "5 4", "1 : 1 line")

    def points(self, xs, ys, color, r=2.4, opacity=0.45, titles=None):
        for i, (x, y) in enumerate(zip(xs, ys)):
            t = f"<title>{esc(titles[i])}</title>" if titles is not None else ""
            self.parts.append(f'<circle cx="{self.X(x):.1f}" cy="{self.Y(y):.1f}" r="{r}" fill="{color}" opacity="{opacity}">{t}</circle>')

    def bars(self, lefts, rights, heights, color, titles=None, opacity=0.85):
        for i, (a, b, hgt) in enumerate(zip(lefts, rights, heights)):
            x, x2, y = self.X(a), self.X(b), self.Y(hgt)
            t = f"<title>{esc(titles[i])}</title>" if titles is not None else ""
            self.parts.append(f'<rect x="{x + 0.5:.1f}" y="{y:.1f}" width="{max(0.5, x2 - x - 1):.1f}" height="{max(0, self.Y(self.y0) - y):.1f}" '
                              f'fill="{color}" opacity="{opacity}">{t}</rect>')


def hbars(labels, values, color="var(--accent)", fmt=_pct, maxv=None, colors=None) -> str:
    """Horizontal bar list (HTML, wraps long labels nicely)."""
    if not len(values):
        return ""
    maxv = maxv or max(max(values), 1e-12)
    rows = []
    for i, (lab, v) in enumerate(zip(labels, values)):
        c = colors[i] if colors else color
        rows.append(f'<div class="hb"><span title="{esc(lab)}">{esc(lab)}</span><span class="hb-track">'
                    f'<span style="width:{max(0.5, 100 * max(v, 0) / maxv):.1f}%;background:{c}"></span></span><b>{fmt(v)}</b></div>')
    return '<div class="hbars">' + "".join(rows) + "</div>"


def grouped_bars(cats, series, ylim=(0, 1), ylabel="", fmt=_pct) -> str:
    """Vertical grouped bars, e.g. precision / recall / F1 per class."""
    k, m = len(cats), len(series)
    w = max(460, 34 + k * (m * 12 + 14))
    ax = _Axes((0, k), ylim, w=w, h=300, ylabel=ylabel, bottom=64)
    for gi, (name, vals, color) in enumerate(series):
        for ci, v in enumerate(vals):
            a = ci + 0.12 + gi * (0.76 / m)
            ax.bars([a], [a + 0.76 / m], [v], color, [f"{cats[ci]} · {name}: {fmt(v)}"])
    return _relabel_x(ax.svg(legend=[(n, c) for n, _, c in series]), ax, cats)  # class names instead of numeric x ticks


def _relabel_x(svg: str, ax: _Axes, cats) -> str:
    import re
    svg = re.sub(r'<text x="[\d.]+" y="%s" class="tick" text-anchor="middle">[^<]*</text>' % re.escape(str(ax.h - ax.b + 16)), "", svg)
    svg = re.sub(r'<line x1="([\d.]+)" x2="\1" y1="%s" y2="[\d.]+" class="grid"/>' % ax.t, "", svg)
    labs = "".join(f'<text transform="translate({ax.X(i + 0.5):.1f} {ax.h - ax.b + 12}) rotate(-35)" class="tick" text-anchor="end">'
                   f'{esc(str(c)[:14])}<title>{esc(c)}</title></text>' for i, c in enumerate(cats))
    return svg.replace("</svg>", labs + "</svg>", 1)


def histogram(series, bins, xlabel, ylabel="Rows", density=False) -> str:
    """series: [(name, values, color)] drawn as overlapping histograms on shared bins."""
    edges = np.asarray(bins, dtype="float64")
    counts = [np.histogram(v, bins=edges)[0].astype("float64") for _, v, _ in series]
    if density:
        counts = [c / max(1, c.sum()) for c in counts]
    ax = _Axes((edges[0], edges[-1]), (0, max(1e-9, max(c.max() for c in counts)) * 1.08), xlabel=xlabel,
               ylabel="Share of rows" if density else ylabel)
    for (name, _, color), c in zip(series, counts):
        ax.bars(edges[:-1], edges[1:], c, color, [f"{name}: {_fmt(a, 3)} – {_fmt(b, 3)} → {_pct(v) if density else int(v)}"
                                                  for a, b, v in zip(edges[:-1], edges[1:], c)], opacity=0.55)
    return ax.svg(legend=[(n, c) for n, _, c in series] if len(series) > 1 else None)


def heatmap(matrix, rows, cols, *, normalise=False, row_title="True", col_title="Predicted", colors=None) -> str:
    """Confusion matrix as a coloured HTML table (counts or row percentages), with PA / UA margins."""
    mtx = np.asarray(matrix, dtype="float64")
    row_tot, col_tot = mtx.sum(1), mtx.sum(0)
    shown = mtx / np.where(row_tot[:, None] == 0, 1, row_tot[:, None]) if normalise else mtx
    vmax = max(shown.max(), 1e-12)
    sw = lambda i: f'<i class="sw" style="background:{colors[i]}"></i>' if colors else ""
    head = "".join(f'<th title="{esc(c)}">{sw(j)}{esc(str(c)[:12])}</th>' for j, c in enumerate(cols))
    body = []
    for i, r in enumerate(rows):
        cells = []
        for j in range(len(cols)):
            v, a = shown[i, j], shown[i, j] / vmax
            bg = f"rgba(22,128,61,{0.12 + 0.8 * a:.2f})" if i == j else (f"rgba(200,30,30,{0.08 + 0.75 * a:.2f})" if v else "transparent")
            txt = (f"{100 * v:.0f}%" if v >= 0.005 else ("<1%" if v else "")) if normalise else (f"{int(v):,}" if v else "")
            cells.append(f'<td style="background:{bg};color:{"#fff" if a > 0.55 else "inherit"}" '
                         f'title="True {esc(r)} → predicted {esc(cols[j])}: {int(mtx[i, j]):,} rows ({_pct(mtx[i, j] / row_tot[i]) if row_tot[i] else "–"} of the class)">{txt}</td>')
        pa = mtx[i, i] / row_tot[i] if row_tot[i] else None
        body.append(f'<tr><th class="rh" title="{esc(r)}">{sw(i)}{esc(str(r)[:14])}</th>{"".join(cells)}<td class="m">{_pct(pa)}</td>'
                    f'<td class="m n">{int(row_tot[i]):,}</td></tr>')
    ua = "".join(f'<td class="m">{_pct(mtx[j, j] / col_tot[j]) if col_tot[j] else "–"}</td>' for j in range(len(cols)))
    tot = "".join(f'<td class="m n">{int(col_tot[j]):,}</td>' for j in range(len(cols)))
    return (f'<div class="scroll"><table class="cm"><tr><th class="corner">{esc(row_title)} ↓ / {esc(col_title)} →</th>{head}'
            f'<th title="Producer\'s accuracy = recall">PA</th><th>Total</th></tr>{"".join(body)}'
            f'<tr><th class="rh" title="User\'s accuracy = precision">UA</th>{ua}<td></td><td></td></tr>'
            f'<tr><th class="rh">Total</th>{tot}<td></td><td class="m n">{int(mtx.sum()):,}</td></tr></table></div>')


def _thin(xs, ys, n=250):
    if len(xs) <= n:
        return xs, ys
    idx = np.unique(np.linspace(0, len(xs) - 1, n).round().astype(int))
    return np.asarray(xs)[idx], np.asarray(ys)[idx]


# ------------------------------------------------------------------ metric computation
def classification_metrics(y_true, y_pred, proba, classes) -> dict:
    from sklearn import metrics as mt
    k = len(classes)
    labels = list(range(k))
    out = {"n": int(len(y_true)), "accuracy": mt.accuracy_score(y_true, y_pred),
           "kappa": mt.cohen_kappa_score(y_true, y_pred), "balanced_accuracy": mt.balanced_accuracy_score(y_true, y_pred),
           "f1_macro": mt.f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0),
           "f1_weighted": mt.f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0),
           "mcc": mt.matthews_corrcoef(y_true, y_pred),
           "confusion": mt.confusion_matrix(y_true, y_pred, labels=labels)}
    p, r, f, s = mt.precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    out["per_class"] = {"precision": p, "recall": r, "f1": f, "support": s}
    if proba is not None:
        present = [c for c in labels if np.any(y_true == c)]
        pr = np.clip(proba, 1e-12, 1)
        out["log_loss"] = mt.log_loss(y_true, pr / pr.sum(1, keepdims=True), labels=labels)
        conf = proba.max(1)
        out["confidence"] = conf
        out["correct"] = y_pred == y_true
        if k > 2:
            top2 = np.argsort(-proba, axis=1)[:, :2]
            out["top2"] = float(np.mean((top2 == y_true[:, None]).any(1)))
        roc, prc = {}, {}
        for c in present:
            yb = (y_true == c).astype(int)
            if yb.min() == yb.max():
                continue
            fpr, tpr, _ = mt.roc_curve(yb, proba[:, c])
            prec, rec, _ = mt.precision_recall_curve(yb, proba[:, c])
            roc[c] = (fpr, tpr, mt.auc(fpr, tpr))
            prc[c] = (rec, prec, mt.average_precision_score(yb, proba[:, c]), yb.mean())
        out["roc"], out["pr"] = roc, prc
        if roc:
            out["auc_macro"] = float(np.mean([v[2] for v in roc.values()]))
        # reliability: top-class confidence vs observed accuracy
        edges = np.linspace(0, 1, 11)
        bins = np.clip(np.digitize(conf, edges) - 1, 0, 9)
        rel = [(float(conf[bins == b].mean()), float(out["correct"][bins == b].mean()), int((bins == b).sum()))
               for b in range(10) if np.any(bins == b)]
        out["reliability"] = rel
        out["ece"] = float(sum(n * abs(a - c) for c, a, n in rel) / max(1, len(conf)))
    return out


def regression_metrics(y_true, y_pred, n_features: int) -> dict:
    from sklearn import metrics as mt
    y_true, y_pred = np.asarray(y_true, "float64"), np.asarray(y_pred, "float64")
    n, res = len(y_true), y_pred - y_true
    r2 = mt.r2_score(y_true, y_pred)
    out = {"n": n, "r2": r2, "rmse": math.sqrt(mt.mean_squared_error(y_true, y_pred)), "mae": mt.mean_absolute_error(y_true, y_pred),
           "medae": mt.median_absolute_error(y_true, y_pred), "bias": float(res.mean()),
           "explained_variance": mt.explained_variance_score(y_true, y_pred),
           "pearson_r": float(np.corrcoef(y_true, y_pred)[0, 1]) if np.std(y_true) > 0 and np.std(y_pred) > 0 else None,
           "adj_r2": 1 - (1 - r2) * (n - 1) / (n - n_features - 1) if n - n_features - 1 > 0 else None, "residuals": res}
    nz = np.abs(y_true) > 1e-9
    out["mape"] = float(np.mean(np.abs(res[nz] / y_true[nz]))) if nz.mean() > 0.95 else None
    std = np.std(y_true)
    out["nrmse"] = out["rmse"] / std if std > 0 else None
    return out


# ------------------------------------------------------------------ page sections
def _tile(label, value, tip, grade=None):
    return f'<div class="tile {grade or ""}" title="{esc(tip)}"><b>{value}</b><span>{esc(label)}</span></div>'


def _grade(v, good, ok):
    if v is None:
        return ""
    return "good" if v >= good else "ok" if v >= ok else "bad"


def _section(title, body, note="", wide=False):
    return (f'<section class="{"wide" if wide else ""}"><h2>{esc(title)}</h2>'
            f'{f"<p class=note>{note}</p>" if note else ""}{body}</section>')


def _classification_sections(rep, ev, m, classes, colors):
    k = len(classes)
    cols = colors or [_PAL[i % len(_PAL)] for i in range(k)]
    s = []
    tiles = [_tile("Overall accuracy", _pct(m["accuracy"]), "Share of test rows classified correctly.", _grade(m["accuracy"], .85, .7)),
             _tile("Kappa", _fmt(m["kappa"]), "Agreement beyond chance: >0.8 excellent, 0.6–0.8 good, <0.4 poor.", _grade(m["kappa"], .8, .6)),
             _tile("Macro F1", _pct(m["f1_macro"]), "Mean F1 over classes; rare classes count as much as common ones.", _grade(m["f1_macro"], .8, .6)),
             _tile("Balanced accuracy", _pct(m["balanced_accuracy"]), "Mean recall (producer's accuracy) over classes."),
             _tile("Weighted F1", _pct(m["f1_weighted"]), "F1 averaged by class size."),
             _tile("MCC", _fmt(m["mcc"]), "Matthews correlation: −1 to 1, robust to unbalanced classes.")]
    if "auc_macro" in m:
        tiles.append(_tile("ROC AUC (macro)", _fmt(m["auc_macro"]), "How well probabilities rank the right class first (0.5 random, 1 perfect).", _grade(m["auc_macro"], .9, .8)))
    if "log_loss" in m:
        tiles.append(_tile("Log loss", _fmt(m["log_loss"]), "Penalises confident wrong answers (lower is better)."))
    if "top2" in m:
        tiles.append(_tile("Top-2 accuracy", _pct(m["top2"]), "Correct class among the two most likely."))
    if "ece" in m:
        tiles.append(_tile("Calibration error", _pct(m["ece"]), "Expected calibration error: gap between confidence and actual accuracy (lower is better)."))
    s.append(_section("Scores on the test split", f'<div class="tiles">{"".join(tiles)}</div>', wide=True))

    cm = m["confusion"]
    s.append(_section("Confusion matrix (counts)", heatmap(cm, classes, classes, colors=colors),
                      "Rows = true class, columns = predicted class. The diagonal (green) is correct. "
                      "PA = producer's accuracy (recall, omission errors); UA = user's accuracy (precision, commission errors).", wide=True))
    s.append(_section("Confusion matrix (row %)", heatmap(cm, classes, classes, normalise=True, colors=colors),
                      "Each row sums to 100%: where the pixels of each true class ended up.", wide=True))

    pc = m["per_class"]
    s.append(_section("Per-class precision, recall and F1", grouped_bars(
        classes, [("Precision (UA)", pc["precision"], "#3b82f6"), ("Recall (PA)", pc["recall"], "#f59e0b"), ("F1", pc["f1"], "#16a34a")]),
        "Low recall = the class is missed (omission). Low precision = other classes are wrongly labelled as it (commission).", wide=True))
    rows = "".join(f'<tr><td class="l"><i class="sw" style="background:{cols[i]}"></i>{esc(c)}</td><td>{_pct(pc["precision"][i])}</td>'
                   f'<td>{_pct(pc["recall"][i])}</td><td>{_pct(pc["f1"][i])}</td><td>{_pct(1 - pc["recall"][i])}</td>'
                   f'<td>{_pct(1 - pc["precision"][i]) if cm[:, i].sum() else "–"}</td><td>{int(pc["support"][i]):,}</td></tr>'
                   for i, c in enumerate(classes))
    s.append(_section("Per-class table", f'<div class="scroll"><table class="tbl"><tr><th class="l">Class</th><th>Precision (UA)</th><th>Recall (PA)</th>'
                      f'<th>F1</th><th>Omission</th><th>Commission</th><th>Test rows</th></tr>{rows}</table></div>'))

    off = [(int(cm[i, j]), i, j) for i in range(k) for j in range(k) if i != j and cm[i, j]]
    off.sort(reverse=True)
    if off:
        tot = cm.sum(1)
        lst = "".join(f'<tr><td class="l">{esc(classes[i])}</td><td>→</td><td class="l">{esc(classes[j])}</td><td>{n:,}</td>'
                      f'<td>{_pct(n / tot[i])}</td></tr>' for n, i, j in off[:8])
        s.append(_section("Most confused classes", f'<table class="tbl"><tr><th class="l">True</th><th></th><th class="l">Predicted as</th>'
                          f'<th>Rows</th><th>Of the class</th></tr>{lst}</table>',
                          "The biggest mistakes. Spectrally similar classes (e.g. crop vs grass) often need more samples or extra layers (indices, radar, a second date)."))

    dist_tr = ev.get("train_counts")
    true_n, pred_n = cm.sum(1), cm.sum(0)
    series = [("Test: true", true_n / max(1, true_n.sum()), "#64748b"), ("Test: predicted", pred_n / max(1, pred_n.sum()), "#16a34a")]
    if dist_tr is not None and len(dist_tr) == k:
        dt = np.asarray(dist_tr, "float64")
        series.insert(0, ("Training", dt / max(1, dt.sum()), "#3b82f6"))
    s.append(_section("Class distribution", grouped_bars(classes, series, ylim=(0, max(max(v) for _, v, _ in series) * 1.1)),
                      "If 'predicted' is much bigger than 'true' for a class, the model over-predicts it."))

    if "roc" in m and m["roc"]:
        ax = _Axes((0, 1), (0, 1), xlabel="False positive rate", ylabel="True positive rate (recall)")
        ax.diag(0, 1)
        legend = []
        for c, (fpr, tpr, a) in m["roc"].items():
            x, y = _thin(fpr, tpr)
            ax.line(x, y, cols[c], title=f"{classes[c]}: AUC {a:.3f}")
            legend.append((f"{classes[c]} · AUC {a:.3f}", cols[c]))
        s.append(_section("ROC curves (one class vs the rest)", ax.svg(legend),
                          "The closer a curve hugs the top-left corner, the better. The dashed line is a random guess (AUC 0.5)."))
        ax = _Axes((0, 1), (0, 1.02), xlabel="Recall", ylabel="Precision")
        legend = []
        for c, (rec, prec, ap, base) in m["pr"].items():
            x, y = _thin(rec, prec)
            ax.line(x, y, cols[c], title=f"{classes[c]}: AP {ap:.3f} (random = {base:.3f})")
            legend.append((f"{classes[c]} · AP {ap:.3f}", cols[c]))
        s.append(_section("Precision–recall curves", ax.svg(legend),
                          "More informative than ROC for rare classes. AP = average precision; a random model scores the class's share of rows."))
    if "confidence" in m:
        conf, ok = m["confidence"], m["correct"]
        lo = max(0.0, math.floor(conf.min() * 10) / 10)
        s.append(_section("Prediction confidence", histogram([("Correct", conf[ok], "#16a34a"), ("Wrong", conf[~ok], "#dc2626")],
                                                             np.linspace(lo, 1, 21), "Probability of the predicted class", density=True),
                          "A good model is confident when right and unsure when wrong. Wrong answers with high confidence suggest label errors or missing classes."))
        rel = m["reliability"]
        ax = _Axes((0, 1), (0, 1), xlabel="Mean confidence", ylabel="Actual accuracy")
        ax.diag(0, 1)
        ax.line([c for c, _, _ in rel], [a for _, a, _ in rel], "var(--accent)")
        ax.points([c for c, _, _ in rel], [a for _, a, _ in rel], "var(--accent)", r=4, opacity=1,
                  titles=[f"confidence {c:.2f} → accuracy {a:.2f} ({n:,} rows)" for c, a, n in rel])
        s.append(_section("Reliability (calibration) diagram", ax.svg(),
                          f"Points on the dashed line mean the probabilities can be trusted (expected calibration error {_pct(m['ece'])}). "
                          "Below the line = over-confident, above = under-confident."))
    elif ev.get("proba") is None:
        s.append(_section("Probability charts", "<p class=note>ROC, precision–recall, confidence and calibration charts need class probabilities. "
                          "This model doesn't produce them (e.g. SVM with 'probability' off, or SGD with hinge loss).</p>"))
    return s


def _regression_sections(rep, ev, m):
    yt, yp, res = np.asarray(ev["y_true"], "float64"), np.asarray(ev["y_pred"], "float64"), m["residuals"]
    s = []
    tiles = [_tile("R²", _fmt(m["r2"]), "Share of variation explained (1 perfect, 0 = predicting the mean).", _grade(m["r2"], .8, .5)),
             _tile("RMSE", _fmt(m["rmse"]), "Root mean squared error, in the target's units. Punishes big errors."),
             _tile("MAE", _fmt(m["mae"]), "Mean absolute error, in the target's units."),
             _tile("Median abs. error", _fmt(m["medae"]), "Half of the predictions are closer than this."),
             _tile("Bias", _fmt(m["bias"]), "Mean of (predicted − true). Positive = over-predicts on average."),
             _tile("Adjusted R²", _fmt(m["adj_r2"]), "R² corrected for the number of features."),
             _tile("Pearson r", _fmt(m["pearson_r"]), "Linear correlation of predicted and true."),
             _tile("NRMSE", _fmt(m["nrmse"]), "RMSE divided by the standard deviation of the target (<0.5 good).")]
    if m["mape"] is not None:
        tiles.append(_tile("MAPE", _pct(m["mape"]), "Mean absolute percentage error."))
    s.append(_section("Scores on the test split", f'<div class="tiles">{"".join(tiles)}</div>', wide=True))
    rng = np.random.default_rng(0)
    idx = rng.choice(len(yt), size=min(2500, len(yt)), replace=False)
    lo, hi = float(min(yt.min(), yp.min())), float(max(yt.max(), yp.max()))
    pad = (hi - lo) * 0.03 or 1
    ax = _Axes((lo - pad, hi + pad), (lo - pad, hi + pad), xlabel="True value", ylabel="Predicted value")
    ax.diag(lo - pad, hi + pad)
    ax.points(yt[idx], yp[idx], "var(--accent)")
    if np.std(yt) > 0:
        a, b = np.polyfit(yt, yp, 1)
        ax.line([lo, hi], [a * lo + b, a * hi + b], "#dc2626", 1.6, title=f"fit: predicted = {a:.3f} × true + {b:.3g}")
    s.append(_section("Predicted vs true", ax.svg([("1 : 1 line", "var(--muted)"), ("Best-fit line", "#dc2626")]),
                      "Points on the dashed line are perfect. A best-fit line flatter than 1:1 means low values are over-predicted and high values under-predicted (common for tree models)."))
    rlim = float(np.abs(res).max()) or 1
    ax = _Axes((float(yp.min()), float(yp.max())), (-rlim * 1.05, rlim * 1.05), xlabel="Predicted value", ylabel="Residual (predicted − true)")
    ax.line([yp.min(), yp.max()], [0, 0], "var(--muted)", 1.2, "5 4")
    ax.points(yp[idx], res[idx], "var(--accent)")
    s.append(_section("Residuals vs predicted", ax.svg(), "Should look like a shapeless band around zero. A funnel or curve means errors depend on the value."))
    s.append(_section("Residual distribution", histogram([("Residuals", res, "var(--accent)")], np.linspace(-rlim, rlim, 31), "Residual (predicted − true)"),
                      "Ideally centred on zero and symmetric."))
    from statistics import NormalDist
    q = np.sort((res - res.mean()) / (res.std() or 1))
    qi = np.linspace(0, len(q) - 1, min(400, len(q))).round().astype(int)
    theo = np.array([NormalDist().inv_cdf((i + 0.5) / len(q)) for i in qi])
    lim = float(max(abs(theo).max(), abs(q[qi]).max()))
    ax = _Axes((-lim, lim), (-lim, lim), xlabel="Normal quantiles", ylabel="Residual quantiles (standardised)")
    ax.diag(-lim, lim)
    ax.points(theo, q[qi], "var(--accent)", opacity=0.7)
    s.append(_section("Q–Q plot of residuals", ax.svg(), "Points along the line = normally distributed errors. Bent ends = heavy tails (occasional large errors)."))
    edges = np.unique(np.quantile(yt, np.linspace(0, 1, 11)))
    if len(edges) > 2:
        b = np.clip(np.digitize(yt, edges[1:-1]), 0, len(edges) - 2)
        mae = [float(np.abs(res[b == i]).mean()) if np.any(b == i) else 0 for i in range(len(edges) - 1)]
        bias = [float(res[b == i].mean()) if np.any(b == i) else 0 for i in range(len(edges) - 1)]
        labels = [f"{_fmt(edges[i], 3)} – {_fmt(edges[i + 1], 3)}" for i in range(len(edges) - 1)]
        s.append(_section("Error by true-value range", hbars(labels, mae, fmt=lambda v: _fmt(v, 3)) +
                          '<p class="note">Bias per range: ' + ", ".join(f"{l}: <b>{_fmt(v, 3)}</b>" for l, v in zip(labels, bias)) + "</p>",
                          "Mean absolute error for each tenth of the true values. Large errors at the extremes are typical."))
    return s


def _common_sections(rep):
    s = []
    imp = rep.get("importance")
    if imp:
        s.append(_section("Feature importance", hbars(imp["features"][:25], imp["values"][:25]),
                          f"Which inputs the model relies on most ({esc(imp['kind'])}). Low-importance features can often be removed."))
    cv = rep.get("cv")
    if cv:
        lab = [f"Fold {i + 1}" for i in range(len(cv["folds"]))]
        f = _pct if cv["metric"] == "accuracy" else (lambda v: _fmt(v, 3))
        s.append(_section(f"Cross-validation ({len(cv['folds'])} folds)", hbars(lab, cv["folds"], fmt=f, maxv=max(max(cv["folds"]), 1e-9)) +
                          f'<p class="note">Mean {esc(cv["metric"])} <b>{f(cv["mean"])}</b> ± {f(cv["std"])}. A big spread means the score depends a lot on which samples are tested.</p>'))
    tn = rep.get("tuning")
    if tn:
        is_pct = tn["metric"] in ("accuracy", "f1_macro", "balanced_accuracy")
        f = _pct if is_pct else (lambda v: _fmt(v, 4))
        keys = list(tn["best_params"])
        rows = "".join(f'<tr class="{"best" if i == 0 else ""}"><td>{i + 1}</td>' + "".join(f"<td>{esc(r['params'].get(k2))}</td>" for k2 in keys) +
                       f"<td><b>{f(r['mean'])}</b></td><td>± {f(r['std'])}</td></tr>" for i, r in enumerate(tn["results"]))
        means = [r["mean"] for r in tn["results"]]
        s.append(_section("Hyperparameter tuning",
                          f'<p class="note">{"Grid" if tn["method"] == "grid" else "Random"} search: {tn["candidates"]} of {tn["grid_size"]} combinations × '
                          f'{tn["folds"]} folds{", grouped by polygon / block" if tn.get("grouped") else ""}, optimising {esc(tn["metric"])}'
                          f'{" (lower is better)" if tn.get("lower_is_better") else ""}. Best: <b>{f(tn["best_score"])}</b> with '
                          + ", ".join(f"<code>{esc(k2)} = {esc(v)}</code>" for k2, v in tn["best_params"].items()) + ".</p>"
                          + hbars([f"#{i + 1}" for i in range(len(means))], [abs(v) for v in means], fmt=lambda v: f(v),
                                  maxv=max(abs(v) for v in means) or 1)
                          + f'<div class="scroll"><table class="tbl"><tr><th>#</th>{"".join(f"<th>{esc(k2)}</th>" for k2 in keys)}<th>CV score</th><th>Spread</th></tr>{rows}</table></div>',
                          wide=True))
    return s


def _details(rep):
    sp = rep.get("split") or {}
    method = {"group": "By polygon (independent)", "blocks": "Spatial blocks (independent)", "random": "Random pixels (optimistic)"}.get(sp.get("method"), sp.get("method"))
    held = f" · {sp.get('groups_test')} of {sp.get('groups_total')} groups held out" if sp.get("groups_total") else ""
    kv = [("Model", rep.get("model_title")), ("Task", rep.get("task")), ("Target", rep.get("target")),
          ("Table", Path(str(rep.get("table", ""))).name), ("Validation split", f"{method}{held}"),
          ("Training rows", f"{rep.get('train_rows', 0):,}" + (f" (sampled from {rep['train_rows_before_sampling']:,})" if rep.get("train_rows_before_sampling", 0) > rep.get("train_rows", 0) else "")),
          ("Test rows", f"{rep.get('test_rows', 0):,}"), ("Rows skipped (missing values)", f"{rep.get('rows_dropped', 0):,}"),
          ("Rows filled in", f"{rep.get('rows_imputed', 0):,}"), ("Features", ", ".join(rep.get("features", []))),
          ("Categorical (one-hot)", ", ".join(rep.get("categorical") or []) or "none"), ("Feature scaling", "yes" if rep.get("scaled") else "no"),
          ("Training time", f"{rep.get('seconds', '–')} s")]
    params = ", ".join(f"{k} = {v}" for k, v in (rep.get("params") or {}).items())
    kv.append(("Model parameters", params or "defaults"))
    rows = "".join(f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>' for k, v in kv)
    warn = "".join(f"<li>{esc(w)}</li>" for w in rep.get("warnings") or [])
    return _section("Model and data", f'<table class="kv">{rows}</table>' + (f'<div class="warn"><b>Warnings</b><ul>{warn}</ul></div>' if warn else ""), wide=True)


# ------------------------------------------------------------------ page
_CSS = """
:root{--bg:#f4f5f7;--panel:#fff;--panel2:#f8f9fb;--border:#e3e6ea;--text:#1d2330;--muted:#667085;--accent:#1f7a5a;--warn-bg:#fff6e5;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--panel:#171b23;--panel2:#1d222c;--border:#2a303c;--text:#e6e8ec;--muted:#98a2b3;--accent:#3fb68b;--warn-bg:#2d2412;--warn:#f5c56b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
header{background:var(--panel);border-bottom:1px solid var(--border);padding:18px 24px}header h1{margin:0 0 4px;font-size:21px}
header .sub{color:var(--muted);font-size:13px}.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:600;margin-right:6px}
.badge.honest{background:#dcfce7;color:#166534}.badge.opt{background:#fef3c7;color:#92400e}.badge.task{background:var(--panel2);border:1px solid var(--border)}
main{max-width:1200px;margin:0 auto;padding:18px 16px 40px;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:16px}
section{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;min-width:0}section.wide{grid-column:1/-1}
h2{margin:0 0 8px;font-size:15px}.note{color:var(--muted);font-size:12.5px;margin:4px 0 10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.tile{background:var(--panel2);border:1px solid var(--border);border-radius:10px;padding:10px 12px;cursor:help}.tile b{display:block;font-size:22px}
.tile span{color:var(--muted);font-size:12px}.tile.good{border-color:#16a34a}.tile.good b{color:#16a34a}.tile.ok b{color:#ca8a04}.tile.bad{border-color:#dc2626}.tile.bad b{color:#dc2626}
.scroll{overflow-x:auto}.cm{border-collapse:collapse;font-size:12.5px}.cm th,.cm td{border:1px solid var(--border);padding:5px 7px;text-align:center;min-width:44px}
.cm th{background:var(--panel2);font-weight:600;white-space:nowrap}.cm th.rh{text-align:left}.cm .corner{font-size:11px;color:var(--muted)}.cm td.m{background:var(--panel2);font-weight:600}.cm td.n{color:var(--muted);font-weight:400}
.tbl{border-collapse:collapse;width:100%;font-size:12.5px}.tbl th,.tbl td{border-bottom:1px solid var(--border);padding:5px 8px;text-align:right}.tbl th{color:var(--muted);font-weight:600}
.tbl .l{text-align:left;white-space:nowrap}.tbl tr.best td{background:rgba(22,163,74,.12)}code{background:var(--panel2);border:1px solid var(--border);border-radius:5px;padding:0 5px;font-size:12px}
.kv{border-collapse:collapse;width:100%;font-size:13px}.kv th{text-align:left;color:var(--muted);font-weight:500;width:220px;padding:4px 10px 4px 0;vertical-align:top}.kv td{padding:4px 0;word-break:break-word}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.chart{width:100%;max-width:640px;height:auto;display:block}.chart .grid{stroke:var(--border);stroke-width:1}.chart .axis{fill:none;stroke:var(--muted);stroke-width:1}
.chart .tick{fill:var(--muted);font-size:10.5px}.chart .lab{fill:var(--text);font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;margin-top:6px}.legend i{display:inline-block;width:12px;height:3px;margin-right:5px;vertical-align:middle;border-radius:2px}
.hbars{display:flex;flex-direction:column;gap:4px}.hb{display:grid;grid-template-columns:minmax(80px,180px) 1fr 70px;gap:8px;align-items:center;font-size:12.5px}
.hb>span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.hb-track{background:var(--panel2);border-radius:3px;height:12px;overflow:hidden}
.hb-track span{display:block;height:100%;border-radius:3px}.hb b{text-align:right;font-weight:600}
.warn{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:8px 12px;margin-top:10px;font-size:13px}.warn ul{margin:4px 0 0 18px;padding:0}
.toolbar{float:right}.toolbar button{font:inherit;font-size:12.5px;padding:5px 12px;border-radius:7px;border:1px solid var(--border);background:var(--panel2);color:var(--text);cursor:pointer}
footer{color:var(--muted);font-size:12px;text-align:center;padding:0 16px 24px}
@media print{.toolbar{display:none}body{background:#fff}section{break-inside:avoid}}
"""


def build_html(report: dict, ev: dict, *, title_suffix: str = "") -> str:
    """report: the training report (ml.train). ev: {"y_true", "y_pred", "proba" | None, "train_counts" | None}."""
    task = report["task"]
    classes = [str(c) for c in report.get("classes") or []]
    colors = None
    src = report.get("source") or {}
    if task == "classification" and src.get("class_colors"):
        cc = src["class_colors"]
        if all(c in cc for c in classes):
            colors = [cc[c] for c in classes]
    if task == "classification":
        proba = None if ev.get("proba") is None else np.asarray(ev["proba"], "float64")
        m = classification_metrics(np.asarray(ev["y_true"]).astype(int), np.asarray(ev["y_pred"]).astype(int), proba, classes)
        body = _classification_sections(report, ev, m, classes, colors)
        head_metric = f"accuracy {_pct(m['accuracy'])} · kappa {_fmt(m['kappa'])}"
    else:
        m = regression_metrics(ev["y_true"], ev["y_pred"], len(report.get("features", [])))
        body = _regression_sections(report, ev, m)
        head_metric = f"R² {_fmt(m['r2'])} · RMSE {_fmt(m['rmse'])}"
    body += _common_sections(report)
    body.append(_details(report))
    method = (report.get("split") or {}).get("method")
    split = (f'<span class="badge honest">✓ Independent test: {"by polygon" if method == "group" else "spatial blocks"}</span>' if method in ("group", "blocks")
             else '<span class="badge opt">⚠ Random pixel split: scores are probably optimistic</span>' if method == "random"
             else f'<span class="badge task">{esc(method)}</span>' if method else "")
    when = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"{report.get('name', 'model')} · evaluation{title_suffix}"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{_CSS}</style></head><body>
<header><div class="toolbar"><button onclick="window.print()">Print / save as PDF</button></div>
<h1>{esc(report.get('model_title', 'Model'))} · {esc(report.get('name', ''))}{esc(title_suffix)}</h1>
<div class="sub"><span class="badge task">{esc(task)}</span>{split} {esc(head_metric)} on {int(m['n']):,} test rows · target <b>{esc(report.get('target'))}</b> · generated {when}</div></header>
<main>{"".join(body)}</main>
<footer>LULC Fetch evaluation report · hover over charts and cells for exact values</footer></body></html>"""


def write_report(report: dict, ev: dict, out_path: str | Path, **kw) -> Path:
    out_path = Path(out_path)
    out_path.write_text(build_html(report, ev, **kw), encoding="utf-8")
    return out_path


def compact_eval(y_true, y_pred, proba, train_counts=None, keep: int = EVAL_KEEP, seed: int = 0) -> dict:
    """Test predictions to store inside the model file (sampled to `keep` rows) so reports can be rebuilt."""
    n = len(y_true)
    idx = np.sort(np.random.default_rng(seed).choice(n, keep, replace=False)) if n > keep else slice(None)
    return {"y_true": np.asarray(y_true)[idx], "y_pred": np.asarray(y_pred)[idx],
            "proba": None if proba is None else np.asarray(proba, "float32")[idx],
            "train_counts": None if train_counts is None else np.asarray(train_counts).tolist(), "sampled": n > keep, "n_total": n}


# ------------------------------------------------------------------ saved models
def report_for_model(model_path: str | Path, out_path: str | Path | None = None) -> Path:
    """Rebuild the evaluation report of a saved model from the test predictions stored inside it."""
    import joblib
    bundle = joblib.load(model_path)
    ev = bundle.get("evaluation")
    if not ev:
        raise ValueError("This model was saved before evaluation reports existed. Retrain it, or evaluate it on a table with --table.")
    out = Path(out_path or Path(model_path).with_suffix(".evaluation.html"))
    return write_report(bundle["report"], ev, out)


def evaluate_on_table(model_path: str | Path, table_path: str | Path, out_path: str | Path, target: str | None = None) -> Path:
    """Test a saved model on another labelled table (e.g. a different area or date): an independent check."""
    import joblib
    from .ml import read_table
    bundle = joblib.load(model_path)
    rep, pipe, feats = dict(bundle["report"]), bundle["pipeline"], bundle["features"]
    target = target or bundle["target"]
    cats = set(bundle.get("categorical") or [])
    data = read_table(table_path, columns=list(dict.fromkeys(feats + [target])))
    if cats:
        X = np.empty((len(data[target]), len(feats)), dtype=object)
        for i, f in enumerate(feats):
            X[:, i] = data[f]
        num = [i for i, f in enumerate(feats) if f not in cats]
        ok = np.isfinite(X[:, num].astype("float64")).all(1) if num else np.ones(len(X), bool)
    else:
        X = np.column_stack([data[f] for f in feats]).astype("float64")
        ok = np.isfinite(X).all(1)
    y = data[target]
    ok &= np.array([v not in (None, "") for v in y]) if y.dtype == object else np.isfinite(y)
    X, y = X[ok], y[ok]
    if bundle["task"] == "classification":
        classes = [str(c) for c in bundle["classes"]]
        labels = np.array([str(v) if y.dtype == object else str(int(v)) if float(v).is_integer() else str(v) for v in y])
        known = np.isin(labels, classes)
        if not known.any():
            raise ValueError("None of the table's labels match the model's classes")
        X, labels = X[known], labels[known]
        y_true = np.array([classes.index(v) for v in labels])
        model = pipe.steps[-1][1] if hasattr(pipe, "steps") else pipe
        proba = None
        if rep.get("has_proba") and hasattr(pipe, "predict_proba"):
            p = pipe.predict_proba(X)
            proba = np.zeros((len(X), len(classes)))
            proba[:, np.asarray(getattr(model, "classes_", np.arange(p.shape[1]))).astype(int)] = p
            y_pred = proba.argmax(1)
        else:
            y_pred = np.asarray(pipe.predict(X)).astype(int)
        ev = {"y_true": y_true, "y_pred": y_pred, "proba": proba, "train_counts": list((rep.get("class_counts") or {}).values()) or None}
    else:
        ev = {"y_true": y.astype("float64"), "y_pred": np.asarray(pipe.predict(X), "float64"), "proba": None}
    rep.update(table=str(table_path), test_rows=int(len(ev["y_true"])), split={"method": "external table"},
               warnings=[f"Evaluated on a separate table ({Path(table_path).name}); 'training' details refer to the original training run."])
    return write_report(rep, ev, out_path, title_suffix=f" · on {Path(table_path).name}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build an HTML evaluation report for a saved LULC Fetch model.")
    ap.add_argument("model", help="model file (.joblib)")
    ap.add_argument("--table", help="evaluate on this labelled table instead of the stored test split")
    ap.add_argument("--target", help="label column in --table (default: the model's target)")
    ap.add_argument("-o", "--out", help="output .html (default: next to the model)")
    a = ap.parse_args(argv)
    if a.table:
        out = evaluate_on_table(a.model, a.table, a.out or Path(a.model).with_suffix(f".eval_{Path(a.table).stem}.html"), a.target)
    else:
        out = report_for_model(a.model, a.out)
    print(out)


if __name__ == "__main__":
    main()
