/* Analysis ▸ Tools ▸ Fuzzy & suitability: gradual membership (0–1) instead of hard yes / no boundaries.
   Fuzzy membership (of a raster, or of the distance to a vector layer), Fuzzy overlay (suitability), Fuzzy boundary &
   uncertainty, Fuzzy classification (c-means). Each runs as a job; results go to Contents.
   Server: /api/fuzzy/membership, overlay, boundary, cmeans · lulc_fetch/fuzzy.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const runRow = (p, label) => `<button class="btn primary" id="${p}-run">${label}</button><p class="hint hidden" id="${p}-error" style="color:var(--err)"></p><div id="${p}-result" class="hidden"></div>`;

  // ---- membership functions (the same as lulc_fetch/fuzzy.py, for the live curve)
  const FNS = [
    ["large", "Large: higher is better", "0.5 at the midpoint, rising towards 1 above it (NDVI, rainfall, soil depth)", ["mid", "spread"]],
    ["small", "Small: lower is better", "0.5 at the midpoint, falling towards 0 above it (slope, distance, cost)", ["mid", "spread"]],
    ["linear", "Linear", "0 at a, 1 at b, straight between (a above b: decreasing)", ["a", "b"]],
    ["sigmoid", "Sigmoid", "An S-curve: 0.5 at the midpoint; a negative slope falls (e.g. distance: near is good)", ["mid", "slope"]],
    ["gaussian", "Gaussian: near an ideal value", "1 at the ideal value, falling on both sides by the spread σ (soil pH 6.5, temperature 25 °C)", ["mid", "sigma"]],
    ["near", "Near", "1 at the ideal value, falling on both sides (steeper with a bigger spread)", ["mid", "spread"]],
    ["trapezoid", "Trapezoid: a good range", "0 below a, rising to 1 at b, 1 until c, back to 0 at d (a range of good values)", ["a", "b", "c", "d"]],
    ["none", "Already 0–1 (a membership or probability)", "Used as it is", []],
  ];
  const PLABEL = { a: "a", b: "b", c: "c", d: "d", mid: "Midpoint", spread: "Spread", sigma: "σ (spread)", slope: "Slope" };
  function mu(x, fn, p) {
    if (!Number.isFinite(x)) return NaN;
    const c = (v) => Math.min(1, Math.max(0, v));
    switch (fn) {
      case "none": return c(x);
      case "linear": return p.a === p.b ? NaN : c((x - p.a) / (p.b - p.a));
      case "trapezoid": return c(Math.min(x < p.b ? (p.b > p.a ? (x - p.a) / (p.b - p.a) : 1) : 1, x > p.c ? (p.d > p.c ? (p.d - x) / (p.d - p.c) : 1) : 1));
      case "gaussian": return Math.exp(-((x - p.mid) ** 2) / (2 * p.sigma ** 2));
      case "near": return 1 / (1 + p.spread * (x - p.mid) ** 2);
      case "small": return x <= 0 ? 1 : 1 / (1 + (x / p.mid) ** p.spread);
      case "large": return x <= 0 ? 0 : 1 / (1 + (x / p.mid) ** -p.spread);
      case "sigmoid": return 1 / (1 + Math.exp(-p.slope * (x - p.mid)));
    }
    return NaN;
  }
  function suggest(st, fn, up = true) {   // as fuzzy.suggest()
    const lo = st?.p2 ?? st?.min ?? 0, hi = st?.p98 ?? st?.max ?? 1, mid = (lo + hi) / 2, span = Math.max(hi - lo, 1e-9);
    const r = (v) => +Number(v).toPrecision(4);
    return { linear: up ? { a: r(lo), b: r(hi) } : { a: r(hi), b: r(lo) }, small: { mid: r(mid > 0 ? mid : hi / 2 || 1e-6), spread: 5 }, large: { mid: r(mid > 0 ? mid : hi / 2 || 1e-6), spread: 5 },
      gaussian: { mid: r(mid), sigma: r(span / 4) }, near: { mid: r(mid), spread: r(16 / span ** 2) }, sigmoid: { mid: r(mid), slope: r((up ? 8 : -8) / span) },
      trapezoid: { a: r(lo), b: r(lo + span / 4), c: r(hi - span / 4), d: r(hi) }, none: {} }[fn] || {};
  }

  /** a membership-function editor with its curve; getRange() → {lo, hi, stats}; returns { get(): {fn, params}, set(fn, params) } */
  function fnEditor(LF, box, { fn = "large", params = null, getRange, unit = "" } = {}) {
    const { $, $$, esc } = LF;
    let cur = { fn, params: params || {} };
    box.classList.add("fz-fn");
    box.innerHTML = `<label>Membership <select data-f="fn">${FNS.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("")}</select></label>
      <p class="hint" data-f="about" style="margin:2px 0 4px"></p>
      <div class="fz-params" data-f="params"></div>
      <div class="row tight" style="gap:6px;align-items:center"><button type="button" class="btn small ghost" data-f="fill" title="Parameters from the layer's values (2nd–98th percentile)">Fill from the data</button><span class="hint" data-f="range" style="margin:0"></span></div>
      <svg class="fz-curve" viewBox="0 0 260 96" preserveAspectRatio="none"></svg>`;
    const sel = $('[data-f="fn"]', box);
    sel.value = cur.fn;
    const draw = () => {
      const R = getRange?.() || {}, svg = $("svg", box);
      let lo = R.lo, hi = R.hi;
      const p = cur.params, keys = ["a", "b", "c", "d", "mid"].filter((k) => Number.isFinite(p[k]));
      if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) { const v = keys.map((k) => p[k]); lo = Math.min(0, ...v); hi = Math.max(1, ...v) * (cur.fn === "none" ? 1 : 1.5); }
      if (cur.fn === "none") { lo = 0; hi = 1; }
      const pts = Array.from({ length: 81 }, (_, i) => { const x = lo + (hi - lo) * i / 80, y = mu(x, cur.fn, p); return [8 + 244 * i / 80, 86 - 76 * (Number.isFinite(y) ? y : 0)]; });
      svg.innerHTML = `<line x1="8" y1="86" x2="252" y2="86" class="ax"/><line x1="8" y1="10" x2="8" y2="86" class="ax"/><line x1="8" y1="48" x2="252" y2="48" class="half"/>
        <polyline points="${pts.map((q) => q.join(",")).join(" ")}" class="cv"/>
        <text x="10" y="9" class="lb">1</text><text x="10" y="45" class="lb">0.5</text><text x="8" y="96" class="lb">${esc(String(+lo.toPrecision(3)))}${unit}</text><text x="252" y="96" class="lb" text-anchor="end">${esc(String(+hi.toPrecision(3)))}${unit}</text>`;
      $('[data-f="range"]', box).textContent = R.stats ? `Values ${+R.stats.min?.toPrecision?.(3)} to ${+R.stats.max?.toPrecision?.(3)}` : "";
    };
    const renderParams = () => {
      const keys = FNS.find((f) => f[0] === cur.fn)[3];
      $('[data-f="about"]', box).textContent = FNS.find((f) => f[0] === cur.fn)[2];
      $('[data-f="params"]', box).innerHTML = keys.map((k) => `<label>${PLABEL[k]} <input type="number" step="any" data-p="${k}" value="${cur.params[k] ?? ""}"></label>`).join("");
      $$("[data-p]", box).forEach((i) => i.oninput = () => { cur.params[i.dataset.p] = i.value === "" ? undefined : +i.value; draw(); });
      $('[data-f="fill"]', box).classList.toggle("hidden", !keys.length);
      draw();
    };
    sel.onchange = () => { cur = { fn: sel.value, params: suggest(getRange?.()?.stats, sel.value, sel.value !== "small") }; renderParams(); };
    $('[data-f="fill"]', box).onclick = () => { cur.params = suggest(getRange?.()?.stats, cur.fn, cur.fn !== "small"); renderParams(); };
    if (!Object.keys(cur.params).length) cur.params = suggest(getRange?.()?.stats, cur.fn, cur.fn !== "small");
    renderParams();
    return {
      get: () => { const keys = FNS.find((f) => f[0] === cur.fn)[3]; return { fn: cur.fn, params: Object.fromEntries(keys.map((k) => [k, cur.params[k]]).filter(([, v]) => Number.isFinite(v))) }; },
      set(fn, p) { sel.value = fn; cur = { fn, params: { ...p } }; renderParams(); },
      redraw: draw,
    };
  }

  // shared: run as a job, put every result in Contents (rasters, and vector files with their zone colours)
  function job(LF, p, endpoint, body, describe) {
    const { $, api, trackJob, addRasterFromPath, addVectorLayer, showResult, runButton } = LF;
    runButton(p, async () => {
      const j = await api(endpoint, { method: "POST", json: body() });
      const r = (await trackJob(j, { title: j.title })).result;
      const files = r.outputs || [r.path];
      for (const f of files) {
        if (/\.tiff?$/i.test(f)) await addRasterFromPath(f, { zoom: false }).catch(() => {});
        else if (/\.geojson$/i.test(f)) {
          const fc = await api(`/api/vector/read?path=${encodeURIComponent(f)}`);
          if (!fc.features?.length) continue;
          const names = [...new Set(fc.features.map((x) => x.properties?.class).filter(Boolean))];
          const name = f.split("/").pop().replace(/\.geojson$/, "");
          // the crisp boundary: a strong red line; nested zones: yellow → red with α; the transition zone: purple
          const ramp = ["#facc15", "#f97316", "#dc2626", "#7f1d1d", "#450a0a"];
          const crisp = /_boundary_/.test(name);
          const classes = names.map((n, i) => ({ name: n, color: /transition/i.test(n) ? "#9333ea" : crisp ? "#dc2626" : ramp[i % ramp.length] }));
          addVectorLayer(fc, name, { path: f, zoom: false, weight: crisp ? 3 : 2, ...(classes.length ? { classes, classColors: Object.fromEntries(classes.map((c) => [c.name, c.color])), color: classes[0].color, fillOpacity: crisp ? 0.05 : 0.22 } : {}) });
        }
      }
      showResult(p, `${describe(r)} <span class="hint">${files.length} result${files.length === 1 ? "" : "s"} added to Contents.</span>`);
    });
  }
  const rasters = (LF) => LF.layers.filter((l) => l.type === "raster" && l.path);
  const statsOf = (LF, id, band = 1) => { const l = LF.getLayer(id); const s = l?.legend?.stats || l?.info?.bands?.[band - 1]; return s ? { min: s.min, max: s.max, p2: s.p2, p98: s.p98 } : null; };

  // ---------------- 1 · Fuzzy membership (and fuzzy distance)
  LF.tool({ id: "fmember", title: "Fuzzy membership", icon: "fmember", kinds: ["fuzzy"],
    subtitle: "Turn a layer into gradual membership from 0 to 1 instead of a hard yes / no: slope, NDVI or rainfall, or the distance to roads or rivers (fuzzy distance)",
    panel: `<div class="card"><h2>From ${tip("A raster's values (slope, NDVI, rainfall …), or the distance in metres to the features of a vector layer (roads, rivers, villages): within 500 m is not simply yes and 501 m no; membership falls gradually.")}</h2>
      <div class="row tight" style="gap:12px"><label class="check"><input type="radio" name="fm-src" value="raster" checked> A raster's values</label><label class="check"><input type="radio" name="fm-src" value="distance"> Distance to a vector layer</label></div>
      <div id="fm-r"><label>Raster <select id="fm-raster"></select></label><label>Band <input type="number" id="fm-band" min="1" value="1"></label></div>
      <div id="fm-d" class="hidden"><label>Features <select id="fm-layer"></select></label>
        <div class="grid2"><label>On the grid of <select id="fm-like"><option value="">A UTM grid around them</option></select></label><label>Pixel size (m) <input type="number" id="fm-res" value="30" min="1"></label></div></div></div>
      <div class="card"><h2>Membership</h2><div id="fm-fn"></div>
      <label>Name of the result <input type="text" id="fm-name" value="membership" maxlength="80"></label>
      ${runRow("fm", "Make membership")}</div>`,
    setup(LF) {
      const { $, $$, esc, fillLayers, layers, getLayer } = LF;
      const src = () => $('input[name="fm-src"]:checked').value;
      const ed = fnEditor(LF, $("#fm-fn"), { fn: "large", getRange: () => src() === "distance" ? { lo: 0, hi: 2000, stats: null } : { ...(() => { const s = statsOf(LF, $("#fm-raster").value, +$("#fm-band").value || 1); return s ? { lo: s.min, hi: s.max, stats: s } : {}; })() } });
      $$('input[name="fm-src"]').forEach((r) => r.onchange = () => {
        const d = src() === "distance";
        $("#fm-r").classList.toggle("hidden", d); $("#fm-d").classList.toggle("hidden", !d);
        if (d) ed.set("sigmoid", { mid: 500, slope: -0.01 }); else ed.set("large", suggest(statsOf(LF, $("#fm-raster").value), "large"));
        $("#fm-name").value = d ? "near_features" : "membership";
      });
      const fill = (pick) => {
        fillLayers($("#fm-raster"), rasters(LF), { empty: "No raster layer in Contents", pick });
        fillLayers($("#fm-layer"), layers.filter((l) => l.type === "vector" && l.geojson?.features?.length), { empty: "No vector layer in Contents" });
        const was = $("#fm-like").value;
        $("#fm-like").innerHTML = `<option value="">A UTM grid around them</option>` + rasters(LF).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
        if (getLayer(was)) $("#fm-like").value = was;
        ed.redraw();
      };
      $("#fm-raster").addEventListener("change", () => { if (src() === "raster") ed.set(ed.get().fn, suggest(statsOf(LF, $("#fm-raster").value), ed.get().fn, ed.get().fn !== "small")); });
      job(LF, "fm", "/api/fuzzy/membership", () => {
        const f = ed.get(), name = $("#fm-name").value.trim() || "membership";
        if (src() === "distance") {
          const l = getLayer($("#fm-layer").value);
          if (!l) throw new Error("Choose the vector layer to measure the distance to");
          return { layer: l.geojson, like: getLayer($("#fm-like").value)?.path || null, res: +$("#fm-res").value || 30, fn: f.fn, params: f.params, name };
        }
        const l = getLayer($("#fm-raster").value);
        if (!l) throw new Error("Choose a raster (add one with Insert ▸ Add data)");
        return { raster: l.path, band: +$("#fm-band").value || 1, fn: f.fn, params: f.params, name };
      }, (r) => r.max_distance_m != null ? `<b>Fuzzy distance</b>: up to ${LF.fmt(r.max_distance_m, 0)} m from the features (the distance in metres is saved too).`
        : `<b>Membership</b>: mean ${r.mean}, ${r.full_pct}% fully member (≥ 0.99).`);
      return { open(arg) { fill(arg?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ---------------- 2 · Fuzzy overlay / suitability
  const OPS = [["gamma", "Fuzzy gamma (recommended)", "Between product (strict: every layer must be good) and sum (lenient: one good layer is enough); γ = 0.9 is usual"],
    ["weighted_product", "Weighted product", "∏ μᵢ^wᵢ: a weak layer pulls the result down, by its weight"],
    ["weighted_sum", "Weighted sum (weighted overlay)", "Σ wᵢ μᵢ: good layers make up for weak ones"],
    ["and", "AND (minimum)", "As good as the worst layer: every condition must hold"],
    ["or", "OR (maximum)", "As good as the best layer: any condition is enough"],
    ["product", "Product", "μ₁ · μ₂ · …: lower than any layer (strict)"],
    ["sum", "Algebraic sum", "1 − ∏(1 − μᵢ): higher than any layer (lenient)"]];
  LF.tool({ id: "foverlay", title: "Fuzzy overlay (suitability)", icon: "foverlay", kinds: ["fuzzy"],
    subtitle: "Suitability from several layers (slope, soil pH, rainfall, NDVI, distance to water …): each turned into membership with its own function and weight, then combined into a 0–1 map with suitability classes",
    panel: `<div class="card"><h2>Layers ${tip("Each layer gets a membership function (e.g. slope: small is better; NDVI: large is better; soil pH: Gaussian around 6.5; distance to water from Fuzzy membership: already 0–1) and a weight. Layers on other grids are put onto the first layer's grid.")}</h2>
      <div id="fo-layers"></div><button type="button" class="btn small" id="fo-add">+ Layer</button></div>
      <div class="card"><h2>Combine</h2><label>Operator <select id="fo-op">${OPS.map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></label>
      <p class="hint" id="fo-about"></p><label id="fo-g-row">γ (gamma) <input type="number" id="fo-g" value="0.9" min="0" max="1" step="0.05"></label>
      <label class="check"><input type="checkbox" id="fo-cls" checked> Also five suitability classes (very low … very high) with their area</label>
      <label>Name of the result <input type="text" id="fo-name" value="suitability" maxlength="80"></label>
      ${runRow("fo", "Make suitability map")}</div>`,
    setup(LF) {
      const { $, $$, esc, getLayer } = LF;
      const rows = [];
      const addRow = (pick) => {
        const div = document.createElement("div");
        div.className = "fz-row";
        div.innerHTML = `<div class="row between" style="gap:6px;align-items:center"><b>Layer ${rows.length + 1}</b><button type="button" class="np-copy" title="Remove">×</button></div>
          <div class="grid2"><label>Raster <select data-r></select></label><label>Weight <input type="number" data-w value="1" min="0" step="any"></label></div><div data-fn></div>`;
        $("#fo-layers").appendChild(div);
        const row = { div, sel: $("[data-r]", div) };
        const opts = () => { const was = row.sel.value; row.sel.innerHTML = rasters(LF).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("") || `<option value="">No raster layer</option>`; if (getLayer(was)) row.sel.value = was; };
        opts();
        if (pick) row.sel.value = pick;
        else { const used = new Set(rows.map((r) => r.sel.value)); const free = rasters(LF).find((l) => !used.has(l.id)); if (free) row.sel.value = free.id; }
        row.opts = opts;
        const range = () => { const s = statsOf(LF, row.sel.value); return s ? { lo: s.min, hi: s.max, stats: s } : {}; };
        const looksMember = () => { const s = statsOf(LF, row.sel.value); return s && s.min >= -0.001 && s.max <= 1.001 && /member|fuzzy|near|suitab|prob/i.test(getLayer(row.sel.value)?.name || ""); };
        row.ed = fnEditor(LF, $("[data-fn]", div), { fn: looksMember() ? "none" : "large", getRange: range });
        row.sel.onchange = () => row.ed.set(looksMember() ? "none" : row.ed.get().fn, suggest(statsOf(LF, row.sel.value), row.ed.get().fn, row.ed.get().fn !== "small"));
        $(".np-copy", div).onclick = () => { if (rows.length > 1) { div.remove(); rows.splice(rows.indexOf(row), 1); $$("#fo-layers .fz-row b").forEach((b, i) => { b.textContent = `Layer ${i + 1}`; }); } };
        rows.push(row);
      };
      $("#fo-add").onclick = () => addRow();
      const about = () => { const o = OPS.find((x) => x[0] === $("#fo-op").value); $("#fo-about").textContent = o[2]; $("#fo-g-row").classList.toggle("hidden", o[0] !== "gamma"); };
      $("#fo-op").onchange = about; about();
      job(LF, "fo", "/api/fuzzy/overlay", () => {
        const ls = rows.map((r, i) => { const l = getLayer(r.sel.value); if (!l) throw new Error(`Layer ${i + 1}: choose a raster`); const f = r.ed.get();
          return { path: l.path, fn: f.fn, params: f.params, weight: +$("[data-w]", r.div).value || 0, name: l.name }; });
        return { layers: ls, op: $("#fo-op").value, gamma: +$("#fo-g").value, classes: $("#fo-cls").checked, name: $("#fo-name").value.trim() || "suitability" };
      }, (r) => `<b>Suitability</b> (${esc(OPS.find((o) => o[0] === r.operator)?.[1] || r.operator)} of ${r.layers} layers): mean ${r.mean}, best ${r.max}.` +
        (r.classes ? `<div class="dist" style="margin-top:6px">${r.classes.map((c) => `<div style="grid-template-columns:minmax(0,2fr) auto auto;gap:8px"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${c.color};margin-right:5px"></i>${esc(c.class)} <small>${c.range}</small></span><b>${LF.fmt(c.area_ha, 1)} ha</b><span>${c.pct}%</span></div>`).join("")}</div>` : ""));
      return {
        open() { if (!rows.length) { addRow(); if (rasters(LF).length > 1) addRow(); } rows.forEach((r) => { r.opts(); r.ed.redraw(); }); },
        layersChanged() { rows.forEach((r) => r.opts()); },
      };
    },
  });

  // ---------------- 3 · Fuzzy boundary & uncertainty
  LF.tool({ id: "fboundary", title: "Fuzzy boundary & uncertainty", icon: "fboundary", kinds: ["fuzzy"],
    subtitle: "From a membership or probability map: where the boundary is uncertain (the transition zone), nested zones (α-cuts) and a smooth crisp boundary as polygons, plus an uncertainty map",
    panel: `<div class="card"><h2>Membership map ${tip("A 0–1 raster: from Fuzzy membership or overlay, a probability or confidence map, a flood or drought likelihood. Uncertainty is 1 where membership is 0.5 (could be either) and 0 where it is 0 or 1.")}</h2>
      <label>Raster <select id="fb-raster"></select></label>
      <div class="grid2"><label>Crisp boundary at μ = <input type="number" id="fb-alpha" value="0.5" min="0.01" max="0.99" step="0.05"></label>
        <label>Zones (α-cuts) <input type="text" id="fb-cuts" value="0.25, 0.5, 0.75"></label></div>
      <div class="grid2"><label>Transition zone from <input type="number" id="fb-low" value="0.25" min="0" max="1" step="0.05"></label><label>to <input type="number" id="fb-high" value="0.75" min="0" max="1" step="0.05"></label></div>
      <details><summary class="hint">Smoothing</summary>
        <label>Blur the membership first (pixels) ${tip("A Gaussian blur of the membership before the boundary is drawn: the line follows the gradual change, not the pixel staircase.")}<input type="number" id="fb-blur" value="1" min="0" max="20" step="0.5"></label>
        <label>Corner cutting passes (Chaikin) <input type="number" id="fb-smooth" value="2" min="0" max="6"></label>
        <div class="grid2"><label>Drop polygons under (m²) <input type="number" id="fb-min" value="0" min="0"></label><label>Simplify (m) <input type="number" id="fb-simp" value="0" min="0"></label></div></details>
      ${runRow("fb", "Find boundaries")}</div>`,
    setup(LF) {
      const { $, fillLayers, getLayer } = LF;
      job(LF, "fb", "/api/fuzzy/boundary", () => {
        const l = getLayer($("#fb-raster").value);
        if (!l) throw new Error("Choose a membership raster");
        const cuts = $("#fb-cuts").value.split(/[,\s;]+/).filter(Boolean).map(Number);
        if (cuts.some((c) => !(c > 0 && c < 1))) throw new Error("Zones: values between 0 and 1, e.g. 0.25, 0.5, 0.75");
        return { raster: l.path, alpha: +$("#fb-alpha").value, cuts, low: +$("#fb-low").value, high: +$("#fb-high").value, blur: +$("#fb-blur").value,
                 smooth: +$("#fb-smooth").value, min_area_m2: +$("#fb-min").value || 0, simplify_m: +$("#fb-simp").value || 0, name: l.name.replace(/\.tiff?$/i, "") };
      }, (r) => `<b>${r.uncertain_pct}% of the area is uncertain</b> (between ${r.cuts[0]} and ${r.cuts.at(-1)}): a transition zone of ${LF.fmt(r.transition_ha, 1)} ha; ${LF.fmt(r.crisp_ha, 1)} ha above μ = ${r.alpha}. Mean uncertainty ${r.mean_uncertainty}.`);
      const fill = (pick) => fillLayers($("#fb-raster"), rasters(LF), { empty: "No raster layer in Contents", pick });
      return { open(arg) { fill(arg?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ---------------- 4 · Fuzzy classification (c-means)
  LF.tool({ id: "fcmeans", title: "Fuzzy classification (c-means)", icon: "fcmeans", kinds: ["fuzzy"],
    subtitle: "Each pixel's membership in every class instead of one class only (mixed pixels): fuzzy c-means gives a membership map per class, the hard class and an uncertainty map",
    panel: `<div class="card"><h2>Fuzzy classification ${tip("Fuzzy c-means groups the pixels by their band values (standardised) like k-means, but each pixel belongs to every class with a membership that sums to 1. A pixel 0.82 cropland / 0.11 forest is mostly cropland; 0.45 / 0.40 is mixed (high uncertainty).")}</h2>
      <label>Image <select id="fc-raster"></select></label>
      <div class="grid2"><label>Classes <input type="number" id="fc-k" value="4" min="2" max="12"></label>
        <label>Fuzziness m ${tip("1.5 almost hard, 2 usual, 3 very fuzzy")}<input type="number" id="fc-m" value="2" min="1.1" max="5" step="0.1"></label></div>
      <label>Bands (empty = all) <input type="text" id="fc-bands" placeholder="e.g. 2, 3, 4, 8"></label>
      ${runRow("fc", "Classify")}</div>`,
    setup(LF) {
      const { $, esc, fillLayers, getLayer } = LF;
      job(LF, "fc", "/api/fuzzy/cmeans", () => {
        const l = getLayer($("#fc-raster").value);
        if (!l) throw new Error("Choose an image");
        const bt = $("#fc-bands").value.trim(), bands = bt ? bt.split(/[\s,]+/).map(Number) : null;
        if (bands?.some((b) => !Number.isInteger(b) || b < 1)) throw new Error("Bands: numbers from 1, e.g. 2,3,4,8");
        return { raster: l.path, k: +$("#fc-k").value || 4, m: +$("#fc-m").value || 2, bands, name: l.name.replace(/\.tiff?$/i, "") + `_fcm${+$("#fc-k").value || 4}` };
      }, (r) => `<b>${r.k} fuzzy classes</b>; ${r.mixed_pct}% of the pixels are mixed (uncertainty above 0.5).<div class="dist" style="margin-top:6px">${r.classes.map((c) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${c.color};margin-right:5px"></i>Class ${c.class}</span><b>${c.pct}%</b></div>`).join("")}</div>
        <p class="hint">The memberships file has one band per class (Properties ▸ show a band).</p>`);
      const fill = (pick) => fillLayers($("#fc-raster"), rasters(LF), { empty: "No raster layer in Contents", pick });
      return { open(arg) { fill(arg?.layer); }, layersChanged() { fill(); } };
    },
  });
})();
