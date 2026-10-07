"""Area statistics of a class map, and accuracy assessment of a map with reference points, after Olofsson et al.
(2014), "Good practices for estimating area and assessing accuracy of land change", Remote Sensing of Environment 148:
a stratified random sample by map class, the confusion matrix, overall / user's / producer's accuracy and kappa, and
the area of each class estimated from the sample (not just counted on the map), with 95 % confidence intervals."""

from __future__ import annotations

import math

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_geom

from . import progress
from .convert import MAX_PIXELS, class_names


def _pixel_area_m2(src) -> np.ndarray | float:
    """Pixel area in m²: one number for a projected raster, one per row for a raster in degrees."""
    rx, ry = abs(src.transform.a), abs(src.transform.e)
    if src.crs and src.crs.is_geographic:
        lat = src.transform.f + src.transform.e * (np.arange(src.height) + 0.5)
        return (rx * 111320.0 * np.cos(np.radians(lat)) * ry * 110574.0)[:, None]
    return rx * ry


def _read_classes(src, band: int, area: dict | None):
    if not 1 <= band <= src.count:
        raise ValueError(f"The raster has {src.count} band(s); there is no band {band}")
    if src.width * src.height > MAX_PIXELS * 2:
        raise ValueError("The raster is too big for this → clip it to your area first")
    a = src.read(band, masked=True)
    data = np.ma.getdata(a)
    if not np.issubdtype(data.dtype, np.integer):
        ok = np.isfinite(data) & ~np.ma.getmaskarray(a)
        u = np.unique(data[ok])
        if u.size > 255 or not np.allclose(u, np.round(u)):
            raise ValueError("The raster has continuous values → this needs a class map (land cover, or Reclassify first)")
    valid = ~np.ma.getmaskarray(a)
    if area:
        from rasterio.features import geometry_mask
        geoms = [transform_geom("EPSG:4326", src.crs, g) for g in _area_geoms(area)]
        valid &= ~geometry_mask(geoms, out_shape=(src.height, src.width), transform=src.transform)
    return np.where(valid, data, 0).astype("int64"), valid


def _area_geoms(area: dict) -> list[dict]:
    if area.get("type") == "FeatureCollection":
        return [f["geometry"] for f in area["features"] if f.get("geometry")]
    if area.get("type") == "Feature":
        return [area["geometry"]]
    return [area]


def area_stats(path, *, band: int = 1, area: dict | None = None) -> dict:
    """Pixels, hectares and % of each class (inside an area, if given)."""
    with rasterio.open(path) as src:
        data, valid = _read_classes(src, band, area)
        names = class_names(src, band)
        try:
            cmap = src.colormap(band)
        except ValueError:
            cmap = {}
        px = _pixel_area_m2(src)
        pxa = np.broadcast_to(px, data.shape) if isinstance(px, np.ndarray) else None
    vals = np.unique(data[valid])
    rows, total = [], 0.0
    for v in vals:
        m = valid & (data == v)
        ha = float(pxa[m].sum() if pxa is not None else m.sum() * px) / 1e4
        total += ha
        c = cmap.get(int(v))
        rows.append({"value": int(v), "class": names.get(int(v), str(int(v))), "pixels": int(m.sum()), "area_ha": round(ha, 4),
                     "color": "#%02x%02x%02x" % tuple(c[:3]) if c else None})
    for r in rows:
        r["percent"] = round(100 * r["area_ha"] / total, 3) if total else 0
    rows.sort(key=lambda r: -r["area_ha"])
    return {"classes": rows, "total_ha": round(total, 4), "total_km2": round(total / 100, 4)}


# ------------------------------------------------------------------ sampling
def sample_points(path, *, band: int = 1, per_class: int = 50, min_per_class: int = 0, total: int | None = None,
                  area: dict | None = None, seed: int | None = None) -> dict:
    """Stratified random points for accuracy assessment: per_class points in each map class (or `total` points shared in
    proportion to the classes' areas, with at least min_per_class each). Each point has the map's class and an empty
    `reference` field to fill in by looking at imagery."""
    rng = np.random.default_rng(seed)
    with rasterio.open(path) as src:
        data, valid = _read_classes(src, band, area)
        names = class_names(src, band)
        vals, counts = np.unique(data[valid], return_counts=True)
        if not vals.size:
            raise ValueError("No pixels with data (inside the area)")
        if total:
            share = counts / counts.sum()
            want = {int(v): max(min_per_class, int(round(total * s))) for v, s in zip(vals, share)}
        else:
            want = {int(v): per_class for v in vals}
        feats, k = [], 0
        for v, c in zip(vals, counts):
            rr, cc = np.nonzero(valid & (data == v))
            n = min(want[int(v)], rr.size)
            pick = rng.choice(rr.size, n, replace=False)
            xs, ys = rasterio.transform.xy(src.transform, rr[pick], cc[pick], offset="center")
            xs, ys = np.atleast_1d(xs), np.atleast_1d(ys)
            if src.crs.to_epsg() != 4326:
                xs, ys = warp_transform(src.crs, "EPSG:4326", list(xs), list(ys))
            for x, y in zip(xs, ys):
                k += 1
                feats.append({"type": "Feature", "properties": {"id": k, "map_value": int(v), "map_class": names.get(int(v), str(int(v))), "reference": ""},
                              "geometry": {"type": "Point", "coordinates": [round(float(x), 8), round(float(y), 8)]}})
    order = rng.permutation(len(feats))   # shuffled, so labelling isn't biased by seeing one class after another
    feats = [feats[i] for i in order]
    for i, f in enumerate(feats):
        f["properties"]["id"] = i + 1
    return {"type": "FeatureCollection", "features": feats, "classes": [{"value": int(v), "class": names.get(int(v), str(int(v)))} for v in vals]}


# ------------------------------------------------------------------ assessment
def _match(ref, names_by_value: dict, values_by_name: dict):
    """A reference label (a class name or a class value) as the map's class value; None when empty or unknown."""
    if ref is None or (isinstance(ref, str) and not ref.strip()):
        return None
    if isinstance(ref, (int, float)) and not isinstance(ref, bool) and math.isfinite(ref):
        return int(ref)
    s = str(ref).strip()
    if s.lower() in values_by_name:
        return values_by_name[s.lower()]
    try:
        return int(float(s))
    except ValueError:
        return s   # a class the map doesn't have (kept, so it shows in the matrix)


def assess(path, points: dict, *, ref_field: str = "reference", band: int = 1, area: dict | None = None) -> dict:
    """The map against reference points: confusion matrix (rows = map, columns = reference), overall, user's and
    producer's accuracy, F1, kappa, and Olofsson's area-weighted estimates (accuracy and area per class with 95 % CI),
    using the map's class areas as the strata."""
    feats = [f for f in points.get("features") or [] if (f.get("geometry") or {}).get("type") == "Point"]
    if not feats:
        raise ValueError("The reference layer has no points")
    if not any(ref_field in (f.get("properties") or {}) for f in feats):
        raise ValueError(f"The points have no field “{ref_field}” → choose the field with the true class")
    stats = area_stats(path, band=band, area=area)
    with rasterio.open(path) as src:
        names = class_names(src, band)
        xs = [f["geometry"]["coordinates"][0] for f in feats]
        ys = [f["geometry"]["coordinates"][1] for f in feats]
        if src.crs.to_epsg() != 4326:
            xs, ys = warp_transform("EPSG:4326", src.crs, xs, ys)
        a = src.read(band, masked=True)
        rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)
    values_by_name = {v.lower(): k for k, v in names.items()}
    pairs, skipped = [], {"no_reference": 0, "outside": 0}
    for f, r, c in zip(feats, rows, cols):
        ref = _match((f.get("properties") or {}).get(ref_field), names, values_by_name)
        if ref is None:
            skipped["no_reference"] += 1
            continue
        if not (0 <= r < a.shape[0] and 0 <= c < a.shape[1]) or np.ma.getmaskarray(a)[r, c]:
            skipped["outside"] += 1
            continue
        pairs.append((int(a.data[r, c]), ref))
    if len(pairs) < 2:
        raise ValueError("Fewer than 2 points have both a reference class and a map value → label the points first")
    map_classes = [c["value"] for c in sorted(stats["classes"], key=lambda c: c["value"])]
    extra = sorted({str(r) for _, r in pairs if r not in map_classes and not isinstance(r, int)} | {r for _, r in pairs if isinstance(r, int) and r not in map_classes}, key=str)
    classes = map_classes + [x for x in extra if x not in map_classes]
    idx = {c: i for i, c in enumerate(classes)}
    k = len(classes)
    m = np.zeros((k, k), dtype="int64")
    for mv, rv in pairs:
        if mv in idx and rv in idx:
            m[idx[mv], idx[rv]] += 1
    n = m.sum()
    label = lambda c: names.get(c, str(c)) if isinstance(c, int) else str(c)
    # simple (sample-count) accuracy
    oa = float(np.trace(m) / n)
    pe = float((m.sum(1) * m.sum(0)).sum() / n ** 2)
    kappa = (oa - pe) / (1 - pe) if pe < 1 else 1.0
    per = []
    for i, c in enumerate(classes):
        ni, nj = m[i].sum(), m[:, i].sum()
        ua = m[i, i] / ni if ni else None
        pa = m[i, i] / nj if nj else None
        f1 = 2 * ua * pa / (ua + pa) if ua and pa else (0.0 if ua is not None and pa is not None else None)
        per.append({"value": c, "class": label(c), "map_samples": int(ni), "reference_samples": int(nj),
                    "users_accuracy": None if ua is None else round(float(ua), 4), "producers_accuracy": None if pa is None else round(float(pa), 4),
                    "f1": None if f1 is None else round(float(f1), 4)})
    # Olofsson: map-area weights W_i, estimated cell proportions p_ij = W_i n_ij / n_i.
    area_by_value = {c["value"]: c["area_ha"] for c in stats["classes"]}
    A = stats["total_ha"]
    W = np.array([area_by_value.get(c, 0.0) / A if A else 0.0 for c in classes])
    ni = m.sum(1).astype("float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(ni[:, None] > 0, W[:, None] * m / ni[:, None], 0.0)
    oa_w = float(np.trace(p))
    var_oa = sum(W[i] ** 2 * (m[i, i] / ni[i]) * (1 - m[i, i] / ni[i]) / (ni[i] - 1) for i in range(k) if ni[i] > 1)
    weighted = []
    for j, c in enumerate(classes):
        pj = float(p[:, j].sum())
        var = sum(W[i] ** 2 * (m[i, j] / ni[i]) * (1 - m[i, j] / ni[i]) / (ni[i] - 1) for i in range(k) if ni[i] > 1)
        se = math.sqrt(var)
        ua_w = float(p[j, j] / p[j].sum()) if p[j].sum() > 0 else None
        pa_w = float(p[j, j] / pj) if pj > 0 else None
        weighted.append({"value": c, "class": label(c), "map_area_ha": round(area_by_value.get(c, 0.0), 3),
                         "estimated_area_ha": round(pj * A, 3), "ci95_ha": round(1.96 * se * A, 3),
                         "estimated_percent": round(100 * pj, 3), "users_accuracy": None if ua_w is None else round(ua_w, 4),
                         "producers_accuracy": None if pa_w is None else round(pa_w, 4)})
    progress.update(1, "Done")
    return {"n": int(n), "skipped": skipped, "classes": [label(c) for c in classes], "matrix": m.tolist(),
            "overall_accuracy": round(oa, 4), "kappa": round(float(kappa), 4), "per_class": per,
            "weighted": {"overall_accuracy": round(oa_w, 4), "overall_ci95": round(1.96 * math.sqrt(var_oa), 4), "classes": weighted},
            "total_ha": stats["total_ha"]}


def report_html(r: dict, map_name: str) -> str:
    """A one-file HTML report of an assessment."""
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    pct = lambda v: "–" if v is None else f"{100 * v:.1f} %"
    cls = r["classes"]
    head = "".join(f"<th>{esc(c)}</th>" for c in cls)
    body = "".join(f"<tr><th>{esc(c)}</th>" + "".join(f"<td class='{'d' if i == j else ''}'>{v}</td>" for j, v in enumerate(row)) +
                   f"<td><b>{sum(row)}</b></td></tr>" for i, (c, row) in enumerate(zip(cls, r["matrix"])))
    totals = "".join(f"<td><b>{sum(row[j] for row in r['matrix'])}</b></td>" for j in range(len(cls)))
    per = "".join(f"<tr><td>{esc(p['class'])}</td><td>{pct(p['users_accuracy'])}</td><td>{pct(p['producers_accuracy'])}</td><td>{'–' if p['f1'] is None else format(p['f1'], '.3f')}</td><td>{p['map_samples']}</td></tr>" for p in r["per_class"])
    w = "".join(f"<tr><td>{esc(c['class'])}</td><td>{c['map_area_ha']:,.2f}</td><td><b>{c['estimated_area_ha']:,.2f}</b> ± {c['ci95_ha']:,.2f}</td><td>{c['estimated_percent']:.2f} %</td><td>{pct(c['users_accuracy'])}</td><td>{pct(c['producers_accuracy'])}</td></tr>" for c in r["weighted"]["classes"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Accuracy assessment · {esc(map_name)}</title><style>
body{{font:14px/1.5 system-ui,sans-serif;margin:32px auto;max-width:980px;padding:0 16px;color:#1f2937}}h1{{font-size:22px}}h2{{font-size:17px;margin-top:28px}}
table{{border-collapse:collapse;margin:8px 0}}td,th{{border:1px solid #d1d5db;padding:4px 9px;text-align:right}}th{{background:#f3f4f6}}td.d{{background:#dcfce7;font-weight:600}}
.tiles{{display:flex;gap:12px;flex-wrap:wrap}}.tile{{border:1px solid #d1d5db;border-radius:10px;padding:10px 16px}}.tile b{{font-size:22px;display:block}}p.n{{color:#6b7280}}</style></head><body>
<h1>Accuracy assessment · {esc(map_name)}</h1>
<div class="tiles"><div class="tile"><b>{pct(r['weighted']['overall_accuracy'])}</b>Overall accuracy (area-weighted) ± {100 * r['weighted']['overall_ci95']:.1f}</div>
<div class="tile"><b>{pct(r['overall_accuracy'])}</b>Overall accuracy (sample count)</div><div class="tile"><b>{r['kappa']:.3f}</b>Kappa</div><div class="tile"><b>{r['n']}</b>Points used</div></div>
<p class="n">Skipped: {r['skipped']['no_reference']} points without a reference class, {r['skipped']['outside']} outside the map.</p>
<h2>Confusion matrix (rows: map, columns: reference)</h2><table><tr><th></th>{head}<th>Total</th></tr>{body}<tr><th>Total</th>{totals}<td><b>{r['n']}</b></td></tr></table>
<h2>Per class (sample counts)</h2><table><tr><th>Class</th><th>User's accuracy</th><th>Producer's accuracy</th><th>F1</th><th>Points</th></tr>{per}</table>
<h2>Area estimates (Olofsson et al. 2014)</h2><p class="n">The map's pixel count is biased by its errors; the area estimated from the sample corrects it, with a 95 % confidence interval. Total area {r['total_ha']:,.2f} ha.</p>
<table><tr><th>Class</th><th>Mapped area (ha)</th><th>Estimated area (ha) ± 95 % CI</th><th>Estimated share</th><th>User's acc.</th><th>Producer's acc.</th></tr>{w}</table>
<p class="n">Made with LULC Fetch.</p></body></html>"""


def matrix_csv(r: dict) -> str:
    lines = ["map \\ reference," + ",".join(f'"{c}"' for c in r["classes"])]
    lines += [f'"{c}",' + ",".join(map(str, row)) for c, row in zip(r["classes"], r["matrix"])]
    return "\n".join(lines) + "\n"
