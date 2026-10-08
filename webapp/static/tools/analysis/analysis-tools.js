/* Analysis ▸ Tools: Area statistics, Accuracy assessment (sample, label, assess), Raster calculator, Index time series
   and Georeference. Jobs (progress, History, Workflows, the Assistant); results go to Contents.
   Server: /api/assess/*, /api/raster/calc, /api/timeseries, /api/georef/* · lulc_fetch/accuracy.py, calc.py,
   timeseries.py, georef.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const sel = (id, label) => `<label>${label} <select id="${id}"></select></label>`;
  const runRow = (p, label) => `<button class="btn primary" id="${p}-run">${label}</button><p class="hint hidden" id="${p}-error" style="color:var(--err)"></p><div id="${p}-result" class="hidden"></div>`;
  const nameRow = (p, val) => `<label>Name of the result <input type="text" id="${p}-name" value="${val}" maxlength="80"></label>`;
  const pct = (v) => v == null ? "–" : `${(100 * v).toFixed(1)} %`;
  const rasters = (LF) => LF.layers.filter((l) => l.type === "raster" && l.path);
  const vectors = (LF, only) => LF.layers.filter((l) => l.type === "vector" && l.geojson?.features?.length && (!only || only(l)));
  const keep = (LF, id, list, empty) => { const s = LF.$(`#${id}`), was = s.value; LF.fillLayers(s, list, { empty }); if ([...s.options].some((o) => o.value === was)) s.value = was; };
  const classMaps = (LF) => rasters(LF).filter((l) => l.legend?.kind === "classes" || /int|uint/.test(l.info?.dtype || ""));

  // a small table with bars, for class areas
  const bars = (LF, rows, val, label) => {
    const max = Math.max(...rows.map(val), 1e-9);
    return `<div class="dist">${rows.map((c) => `<div style="grid-template-columns:minmax(0,1.6fr) minmax(0,2fr) auto"><span>${c.color ? `<i class="swatch" style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${LF.esc(c.color)}"></i> ` : ""}${LF.esc(c.class)}</span>
      <span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * val(c) / max)}%;${c.color ? `background:${LF.esc(c.color)}` : ""}"></span></span><b>${label(c)}</b></div>`).join("")}</div>`;
  };

  // ------------------------------------------------------------------ Area statistics
  LF.tool({ id: "areastats", title: "Area statistics", icon: "areastats", kinds: ["areastats"],
    subtitle: "Hectares, km² and % of each class of a land-cover or classified map, for the whole map or inside an area, as a table",
    clip: { "as2-area": { what: "classes are counted" } },
    panel: `<div class="card"><h2>Area statistics ${tip("Counts the pixels of each class and turns them into hectares (pixel sizes in degrees are converted too). For estimates that correct the map's errors, use Accuracy assessment.")}</h2>
      ${sel("as2-raster", "Class map")}<label>Band <input type="number" id="as2-band" value="1" min="1"></label>
      ${LF.html.area("as2-area", "Inside", "Leave as the whole map, or choose an area")}
      ${nameRow("as2", "area_statistics")}${runRow("as2", "Calculate")}</div>`,
    setup(LF) {
      const { $, esc, fmt, getLayer, getClip, runButton, runJob, addItem, showResult } = LF;
      const fill = () => keep(LF, "as2-raster", classMaps(LF).length ? classMaps(LF) : rasters(LF), "No raster layer in Contents");
      runButton("as2", async () => {
        const l = getLayer($("#as2-raster").value);
        if (!l) throw new Error("Choose a class map");
        const r = await runJob("/api/assess/area-stats", { raster: l.path, band: +$("#as2-band").value || 1, area: getClip("as2-area") || null, name: $("#as2-name").value.trim() || "area_statistics" },
                               { tool: "areastats", title: `Area statistics of ${l.name}` });
        addItem({ kind: "table", name: r.csv.split("/").pop(), path: r.csv });
        showResult("as2", `<b>${fmt(r.total_ha, 1)} ha</b> (${fmt(r.total_km2, 2)} km²) in ${r.classes.length} classes${bars(LF, r.classes, (c) => c.area_ha, (c) => `${fmt(c.area_ha, 1)} ha · ${fmt(c.percent, 1)} %`)}<span class="hint">The table was added to Contents.</span>`);
      });
      return { open(arg) { fill(); if (arg?.layer) $("#as2-raster").value = arg.layer; }, layersChanged: fill };
    },
  });

  // ------------------------------------------------------------------ Accuracy assessment
  LF.tool({ id: "accuracy", title: "Accuracy assessment", icon: "accuracy", kinds: ["accsample", "accuracy"],
    subtitle: "How good is a classified map? Random points per class, label them by looking at imagery, then a confusion matrix, kappa and corrected class areas with confidence intervals (Olofsson et al.)",
    clip: { "ac-area": { what: "points are placed" } },
    panel: `<div class="card"><h2>1 · Sample points ${tip("Stratified random sampling: the same number of points in each class of the map (or a total shared by area, with a minimum per class). Each point gets the map's class and an empty “reference” field.")}</h2>
      ${sel("ac-raster", "Classified map")}
      <div class="grid2"><label>Points per class <input type="number" id="ac-per" value="50" min="1" max="5000"></label>
        <label>or a total of <input type="number" id="ac-total" placeholder="(per class)" min="2"></label></div>
      ${LF.html.area("ac-area", "Inside", "Leave as the whole map, or choose an area")}
      <label>Name <input type="text" id="ac-name" value="accuracy_points" maxlength="80"></label>
      ${runRow("ac", "Make points")}</div>
    <div class="card"><h2>2 · Label them ${tip("For each point, look at the imagery under it (a high-resolution basemap, Sentinel-2, a drone photo…) and choose the true class. Keys 1–9 choose a class, ← → move. The map's class is hidden until you choose, so it doesn't sway you.")}</h2>
      ${sel("al-points", "Points")}
      <label>Field with the true class <select id="al-field"></select></label>
      <button class="btn small" id="al-start">Start labelling</button>
      <div id="al-box" class="hidden"></div></div>
    <div class="card"><h2>3 · Assess</h2>
      <p class="hint">The classified map (step 1) against the labelled points.</p>
      ${nameRow("aa", "accuracy")}${runRow("aa", "Assess accuracy")}</div>`,
    setup(LF) {
      const { $, $$, esc, fmt, getLayer, getClip, runButton, runJob, addVectorLayer, addItem, showResult, saveLayers, map, api } = LF;
      const st = { classes: [], i: 0, marker: null, layer: null };
      const fill = () => {
        keep(LF, "ac-raster", classMaps(LF).length ? classMaps(LF) : rasters(LF), "No classified map in Contents");
        keep(LF, "al-points", vectors(LF, (l) => l.geojson.features.every((f) => f.geometry?.type === "Point")), "No point layer yet (step 1)");
        fields();
      };
      const fields = () => {
        const l = getLayer($("#al-points").value), was = $("#al-field").value;
        const ks = [...new Set((l?.geojson?.features || []).slice(0, 200).flatMap((f) => Object.keys(f.properties || {})))];
        $("#al-field").innerHTML = ks.map((k) => `<option>${esc(k)}</option>`).join("") || `<option value="">(no fields)</option>`;
        $("#al-field").value = ks.includes(was) ? was : ks.includes("reference") ? "reference" : ks.find((k) => /class|label|truth|ref/i.test(k)) || ks[0] || "";
      };
      $("#al-points").addEventListener("change", fields);
      runButton("ac", async () => {
        const l = getLayer($("#ac-raster").value);
        if (!l) throw new Error("Choose the classified map");
        const total = +$("#ac-total").value || null;
        const r = await runJob("/api/assess/sample", { raster: l.path, per_class: +$("#ac-per").value || 50, total, area: getClip("ac-area") || null, name: $("#ac-name").value.trim() || "accuracy_points" },
                               { tool: "accuracy", title: `Accuracy points for ${l.name}` });
        const fc = await api(`/api/vector/read?path=${encodeURIComponent(r.path)}`);
        const pts = addVectorLayer(fc, r.name, { path: r.path, color: "#f59e0b", zoom: false, classNames: r.classes.map((c) => c.class) });
        st.classes = r.classes.map((c) => c.class);
        fill(); $("#al-points").value = pts.id; fields(); $("#al-field").value = "reference";
        showResult("ac", `<b>${r.features} points</b> in ${r.classes.length} classes, added to Contents. Now label them (step 2).`);
      });

      // ---- labelling: one point at a time, zoomed in, the map's class hidden
      const feats = () => st.layer?.geojson?.features || [];
      function show() {
        const f = feats()[st.i], field = $("#al-field").value;
        if (!f) return;
        const [lon, lat] = f.geometry.coordinates, done = feats().filter((x) => String(x.properties?.[field] ?? "").trim() !== "").length;
        map.setView([lat, lon], Math.max(map.getZoom(), 17));
        st.marker?.remove();
        st.marker = L.circleMarker([lat, lon], { radius: 9, color: "#f43f5e", weight: 3, fill: false, interactive: false }).addTo(map);
        const cur = String(f.properties?.[field] ?? "");
        $("#al-box").innerHTML = `<div class="row between" style="margin:6px 0"><b>Point ${st.i + 1} of ${feats().length}</b><span class="hint" style="margin:0">${done} labelled</span></div>
          <div class="rb-track" style="margin:0 0 8px"><span class="rb-fill" style="display:block;width:${100 * done / feats().length}%"></span></div>
          <div class="row tight" style="flex-wrap:wrap;gap:6px">${st.classes.map((c, k) => `<button class="btn small ${cur === c ? "primary" : ""}" data-cls="${esc(c)}" title="Key ${k + 1}">${k < 9 ? `<small>${k + 1}</small> ` : ""}${esc(c)}</button>`).join("")}
            <button class="btn small ghost" data-cls="" title="Can't tell: left out of the assessment">Can't tell</button></div>
          <div class="row tight" style="gap:6px;margin-top:8px"><button class="btn small ghost" data-nav="-1">← Back</button><button class="btn small ghost" data-nav="1">Next →</button>
            <button class="btn small ghost" data-nav="next-empty">Next unlabelled</button><button class="btn small ghost" id="al-stop">Done</button></div>
          ${cur ? `<p class="hint">Map says: ${esc(f.properties.map_class ?? "?")}</p>` : ""}`;
        $$("#al-box [data-cls]").forEach((b) => b.onclick = () => label(b.dataset.cls));
        $$("#al-box [data-nav]").forEach((b) => b.onclick = () => nav(b.dataset.nav));
        $("#al-stop").onclick = stop;
      }
      function label(c) {
        const f = feats()[st.i];
        f.properties = { ...f.properties, [$("#al-field").value]: c };
        saveLayers();
        nav("next-empty");
      }
      function nav(d) {
        const n = feats().length, field = $("#al-field").value;
        if (d === "next-empty") {
          for (let k = 1; k <= n; k++) { const j = (st.i + k) % n; if (String(feats()[j].properties?.[field] ?? "").trim() === "") { st.i = j; return show(); } }
          LF.toast("Every point is labelled → step 3: Assess accuracy");
          return show();
        }
        st.i = (st.i + +d + n) % n; show();
      }
      function stop() { st.marker?.remove(); st.marker = null; $("#al-box").classList.add("hidden"); document.removeEventListener("keydown", keys); }
      function keys(e) {
        if ($("#al-box").classList.contains("hidden") || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
        if (/^[1-9]$/.test(e.key) && st.classes[+e.key - 1]) { e.preventDefault(); label(st.classes[+e.key - 1]); }
        else if (e.key === "ArrowRight") { e.preventDefault(); nav(1); } else if (e.key === "ArrowLeft") { e.preventDefault(); nav(-1); }
      }
      $("#al-start").onclick = () => {
        st.layer = getLayer($("#al-points").value);
        if (!st.layer) return LF.toast("Make or choose a point layer first", true);
        const m = getLayer($("#ac-raster").value);
        st.classes = st.layer.classNames || (m?.legend?.classes || []).map((c) => c.name);
        if (!st.classes.length) st.classes = [...new Set(feats().map((f) => f.properties?.map_class).filter(Boolean))];
        if (!st.classes.length) return LF.toast("Choose the classified map in step 1 (its classes are the choices)", true);
        st.i = 0; $("#al-box").classList.remove("hidden");
        document.addEventListener("keydown", keys);
        nav("next-empty");
      };

      runButton("aa", async () => {
        const m = getLayer($("#ac-raster").value), p = getLayer($("#al-points").value);
        if (!m) throw new Error("Choose the classified map (step 1)");
        if (!p) throw new Error("Choose the labelled points (step 2)");
        const r = await runJob("/api/assess/accuracy", { raster: m.path, points: p.geojson, ref_field: $("#al-field").value || "reference", name: $("#aa-name").value.trim() || "accuracy" },
                               { tool: "accuracy", title: `Accuracy of ${m.name}` });
        addItem({ kind: "table", name: r.csv.split("/").pop(), path: r.csv });
        const cls = r.classes, mx = r.matrix;
        const matrix = `<table class="cm"><tr><th title="rows: map · columns: reference">map \\ ref.</th>${cls.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>
          ${mx.map((row, i) => `<tr><th>${esc(cls[i])}</th>${row.map((v, j) => `<td style="${i === j ? "background:var(--accent-2);font-weight:600" : v ? "color:var(--err)" : "color:var(--muted)"}">${v}</td>`).join("")}</tr>`).join("")}</table>`;
        const w = r.weighted.classes;
        showResult("aa", `<div class="pca-sum"><b>${pct(r.weighted.overall_accuracy)}</b> overall accuracy (area-weighted, ± ${(100 * r.weighted.overall_ci95).toFixed(1)}) · ${pct(r.overall_accuracy)} by count · kappa ${fmt(r.kappa, 3)} · ${r.n} points${r.skipped.no_reference ? ` (${r.skipped.no_reference} unlabelled left out)` : ""}</div>
          <div style="overflow:auto;margin:8px 0">${matrix}</div>
          <table class="cm"><tr><th>Class</th><th>User's</th><th>Producer's</th><th title="Area estimated from the sample ± 95 % confidence interval">Estimated ha</th><th>Mapped ha</th></tr>
          ${w.map((c, i) => `<tr><td style="text-align:left">${esc(c.class)}</td><td>${pct(r.per_class[i].users_accuracy)}</td><td>${pct(r.per_class[i].producers_accuracy)}</td><td><b>${fmt(c.estimated_area_ha, 1)}</b> ± ${fmt(c.ci95_ha, 1)}</td><td>${fmt(c.map_area_ha, 1)}</td></tr>`).join("")}</table>
          <div class="row tight" style="gap:6px;margin-top:8px"><button class="btn small" id="aa-report">Open the report</button></div>
          <p class="hint">The table was added to Contents. Areas follow Olofsson et al. (2014): the map's own counts carry its errors; these estimates correct them.</p>`);
        $("#aa-report").onclick = () => window.open(`/api/assess/report?path=${encodeURIComponent(r.report)}`, "_blank", "noopener");
      });
      return { open(arg) { fill(); if (arg?.layer) $("#ac-raster").value = arg.layer; }, layersChanged: fill };
    },
  });

  // ------------------------------------------------------------------ Raster calculator
  const EXAMPLES = [["Normalised difference", "(B - A) / (B + A)"], ["Difference", "B - A"], ["Mask", "where(A > 0.3, 1, 0)"], ["Change > 0.1", "(B - A) > 0.1"], ["Both conditions", "(A > 0.3) and (B < 0.1)"], ["Scale", "A * 0.0001"]];
  LF.tool({ id: "rcalc", title: "Raster calculator", icon: "rcalc", kinds: ["calc"],
    subtitle: "Map algebra over bands of several rasters: (B − A) / (B + A), where(A > 0.3, 1, 0), NDVI 2025 − NDVI 2018 > 0.1 …",
    panel: `<div class="card"><h2>Raster calculator ${tip("Each variable is a band of a raster in Contents. All are put on the first one's grid. Write + − * / ^, comparisons (> < >= <= == !=), and / or / not, where(test, a, b), abs, sqrt, log, exp, min, max, clip, round. A true / false result becomes a 1 / 0 mask.")}</h2>
      <div id="rk-vars"></div><button class="btn small" id="rk-add">+ Variable</button>
      <label>Expression <textarea id="rk-expr" rows="2" placeholder="(B - A) / (B + A)"></textarea></label>
      <div class="row tight" style="flex-wrap:wrap;gap:6px;margin:4px 0 8px">${EXAMPLES.map(([t, e]) => `<button class="as-chip" data-ex="${e}" title="${e}">${t}</button>`).join("")}</div>
      <details><summary class="hint">If the grids differ</summary>${LF.html.resampling("rk-method", { auto: "Default (bilinear)", only: ["nearest", "bilinear", "cubic", "average"] })}</details>
      ${nameRow("rk", "calc")}${runRow("rk", "Calculate")}</div>`,
    setup(LF) {
      const { $, $$, esc, fmt, getLayer, runButton, runJob, addRasterFromPath, showResult } = LF;
      let vars = [{ name: "A" }, { name: "B" }];
      const render = () => {
        const list = rasters(LF);
        $("#rk-vars").innerHTML = vars.map((v, i) => `<div class="row tight" style="gap:6px;align-items:end" data-i="${i}"><label style="flex:0 0 52px">Name <input type="text" data-k="name" value="${esc(v.name)}" maxlength="12"></label>
          <label style="flex:1">Raster <select data-k="layer">${list.slice().reverse().map((l) => `<option value="${esc(l.id)}" ${l.id === v.layer ? "selected" : ""}>${esc(l.name)}</option>`).join("") || `<option value="">No raster in Contents</option>`}</select></label>
          <label style="flex:0 0 64px">Band <input type="number" data-k="band" value="${v.band || 1}" min="1"></label><button class="np-copy" data-del title="Remove">×</button></div>`).join("");
        $$("#rk-vars [data-i]").forEach((row) => {
          const v = vars[+row.dataset.i];
          row.querySelectorAll("[data-k]").forEach((inp) => { if (inp.dataset.k === "layer" && !v.layer) v.layer = inp.value; inp.onchange = () => { v[inp.dataset.k] = inp.dataset.k === "band" ? +inp.value : inp.value.trim(); }; });
          row.querySelector("[data-del]").onclick = () => { vars.splice(+row.dataset.i, 1); render(); };
        });
      };
      $("#rk-add").onclick = () => { vars.push({ name: String.fromCharCode(65 + vars.length) }); render(); };
      $$("#tab-rcalc [data-ex]").forEach((b) => b.onclick = () => { $("#rk-expr").value = b.dataset.ex; });
      runButton("rk", async () => {
        const variables = {};
        for (const v of vars) {
          const l = getLayer(v.layer);
          if (!l) throw new Error(`Choose a raster for ${v.name}`);
          if (!/^[A-Za-z_]\w*$/.test(v.name)) throw new Error(`“${v.name}” can't be a name: letters, digits and _`);
          variables[v.name] = { path: l.path, band: v.band || 1 };
        }
        const expr = $("#rk-expr").value.trim();
        if (!expr) throw new Error("Write an expression, e.g. (B - A) / (B + A)");
        const name = $("#rk-name").value.trim() || "calc";
        const r = await runJob("/api/raster/calc", { variables, expression: expr, resampling: $("#rk-method").value || null, name }, { tool: "rcalc", title: `Calculate ${expr}` });
        await addRasterFromPath(r.path, { name, zoom: false });
        const s = r.summary;
        showResult("rk", r.boolean ? `<b>${fmt(s.true_pct, 1)} %</b> of the pixels are true (1). Added to Contents.` : `<b>${fmt(s.min, 4)} … ${fmt(s.max, 4)}</b>, mean ${fmt(s.mean, 4)}. Added to Contents.`);
      });
      return { open() { render(); }, layersChanged() { render(); } };
    },
  });

  // ------------------------------------------------------------------ Index time series
  function chart(LF, rows, index) {
    const pts = rows.filter((r) => r.mean != null).map((r) => ({ t: Date.parse(r.date), v: r.mean, r }));
    if (!pts.length) return "";
    const W = 560, H = 220, mL = 44, mR = 10, mT = 12, mB = 28;
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1, lo = Math.min(...pts.map((p) => p.v), 0), hi = Math.max(...pts.map((p) => p.v), 0.1);
    const x = (t) => mL + (W - mL - mR) * (t - t0) / Math.max(t1 - t0, 1), y = (v) => mT + (H - mT - mB) * (1 - (v - lo) / (hi - lo || 1));
    const ticks = Array.from({ length: 5 }, (_, i) => lo + (hi - lo) * i / 4);
    const months = []; for (let d = new Date(t0); d <= t1; d.setMonth(d.getMonth() + 1)) months.push(new Date(d.getFullYear(), d.getMonth(), 1).getTime());
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;font-size:10px" role="img" aria-label="${index} over time">
      ${ticks.map((v) => `<line x1="${mL}" x2="${W - mR}" y1="${y(v)}" y2="${y(v)}" stroke="var(--border)"/><text x="${mL - 4}" y="${y(v) + 3}" text-anchor="end" fill="var(--muted)">${v.toFixed(2)}</text>`).join("")}
      ${months.filter((t) => t >= t0).filter((_, i, a) => a.length < 13 || i % 2 === 0).map((t) => `<text x="${x(t)}" y="${H - 8}" text-anchor="middle" fill="var(--muted)">${new Date(t).toLocaleString(undefined, { month: "short" })}${new Date(t).getMonth() === 0 ? ` ${String(new Date(t).getFullYear()).slice(2)}` : ""}</text>`).join("")}
      <polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")}"/>
      ${pts.map((p) => `<circle cx="${x(p.t)}" cy="${y(p.v)}" r="3" fill="var(--accent)"><title>${p.r.date}: ${index} ${p.v.toFixed(3)} ± ${p.r.std?.toFixed(3)} · ${p.r.clear_pct}% clear</title></circle>`).join("")}
    </svg>`;
  }
  LF.tool({ id: "timeseries", title: "Index time series", icon: "timeseries", kinds: ["timeseries"],
    subtitle: "NDVI (or EVI, NDWI, NDMI, NDRE, SAVI) of a point or field in every Sentinel-2 scene of a period, from the free catalogue, clouds masked: crop calendars, droughts, harvest dates",
    panel: `<div class="card"><h2>Index time series ${tip("Only the pixels of your point (a small square around it) or field are read from each scene: no big downloads. Clouds, shadows and missing data are masked with each scene's classification; dates with less than half the area clear are left out.")}</h2>
      <label>Where <select id="ts-where"><option value="point">A point I click on the map</option><option value="layer">A field (polygon or point layer)</option></select></label>
      <div id="ts-point-row"><button class="btn small" id="ts-pick">Click a point on the map</button> <span class="hint" id="ts-pt">No point yet.</span></div>
      <div id="ts-layer-row" class="hidden">${sel("ts-layer", "Layer")}<label>Feature <select id="ts-feat"></select></label></div>
      <div class="grid2"><label>From <input type="date" id="ts-start"></label><label>To <input type="date" id="ts-end"></label></div>
      <div class="grid2"><label>Index <select id="ts-index">${["NDVI", "EVI", "NDWI", "NDMI", "NDRE", "SAVI"].map((i) => `<option>${i}</option>`).join("")}</select></label>
        <label>Scene cloud below (%) <input type="number" id="ts-cloud" value="80" min="0" max="100"></label></div>
      ${nameRow("ts", "time_series")}${runRow("ts", "Make the time series")}</div>`,
    setup(LF) {
      const { $, esc, fmt, map, getLayer, runButton, runJob, addItem, showResult } = LF;
      const st = { pt: null, marker: null };
      const today = new Date(), ago = new Date(today.getTime() - 365 * 864e5);
      $("#ts-start").value = ago.toISOString().slice(0, 10); $("#ts-end").value = today.toISOString().slice(0, 10);
      const where = () => { const p = $("#ts-where").value === "point"; $("#ts-point-row").classList.toggle("hidden", !p); $("#ts-layer-row").classList.toggle("hidden", p); };
      $("#ts-where").onchange = where;
      const fillFeat = () => {
        const l = getLayer($("#ts-layer").value), fs = l?.geojson?.features || [];
        const nameOf = (f, i) => f.properties?.name || f.properties?.id || f.properties?.field_id || `Feature ${i + 1}`;
        $("#ts-feat").innerHTML = fs.slice(0, 500).map((f, i) => `<option value="${i}">${esc(String(nameOf(f, i)))}</option>`).join("") || `<option value="">(none)</option>`;
      };
      const fill = () => { keep(LF, "ts-layer", vectors(LF, (l) => l.geojson.features.some((f) => /Point|Polygon/.test(f.geometry?.type))), "No vector layer in Contents"); fillFeat(); };
      $("#ts-layer").addEventListener("change", fillFeat);
      $("#ts-pick").onclick = () => {
        $("#ts-pt").textContent = "Click the place on the map…";
        map.getContainer().style.cursor = "crosshair";
        map.once("click", (e) => {
          map.getContainer().style.cursor = "";
          st.pt = [+e.latlng.lng.toFixed(6), +e.latlng.lat.toFixed(6)];
          st.marker?.remove(); st.marker = L.circleMarker(e.latlng, { radius: 7, color: "#16a34a", weight: 3, fillOpacity: 0.3 }).addTo(map);
          $("#ts-pt").textContent = `lon ${st.pt[0]}, lat ${st.pt[1]}`;
        });
      };
      runButton("ts", async () => {
        let geometry;
        if ($("#ts-where").value === "point") {
          if (!st.pt) throw new Error("Click a point on the map first");
          geometry = { type: "Point", coordinates: st.pt };
        } else {
          const l = getLayer($("#ts-layer").value), f = l?.geojson?.features?.[+$("#ts-feat").value];
          if (!f) throw new Error("Choose a layer and a feature");
          geometry = f.geometry;
        }
        const index = $("#ts-index").value, name = $("#ts-name").value.trim() || "time_series";
        const r = await runJob("/api/timeseries", { geometry, start: $("#ts-start").value, end: $("#ts-end").value, index, max_cloud: +$("#ts-cloud").value, name },
                               { tool: "timeseries", title: `${index} time series` });
        addItem({ kind: "table", name: r.csv.split("/").pop(), path: r.csv });
        const s = r.summary;
        showResult("ts", `<b>${r.points} clear dates</b> of ${r.scenes} scenes · ${index} ${fmt(s.min, 2)} – ${fmt(s.max, 2)} (mean ${fmt(s.mean, 2)}) · highest ${esc(s.peak_date)}, lowest ${esc(s.low_date)}
          ${chart(LF, r.rows, index)}<span class="hint">The table (one row per date) was added to Contents; hover a dot for its date and value.</span>`);
      });
      where();
      return { open: fill, layersChanged: fill };
    },
  });

  // ------------------------------------------------------------------ Georeference
  LF.tool({ id: "georef", title: "Georeference", icon: "georef", kinds: ["georef"],
    subtitle: "Put a scanned map, a plan, a photo, a raster or a vector layer without coordinates (or in the wrong place) on the map: click a place on it, then the same place on the map, 3 or more times",
    panel: `<div class="card"><h2>Georeference ${tip("Pick recognisable places (road crossings, building corners) spread over the whole picture. Affine needs 3 points (better 4–6); polynomial 6; thin-plate spline 10 (it bends the picture to fit every point exactly). A point with a large residual was probably clicked wrong: remove it.")}</h2>
      <label>Picture <input type="file" id="gr-file" accept=".png,.jpg,.jpeg,.tif,.tiff,.gif,.bmp,.webp"></label>
      <label>Or a layer from Contents <select id="gr-layer"></select></label>
      <p class="hint" id="gr-info">A picture (PNG, JPG, TIFF), a raster, or a vector layer: drawings, plans and local survey coordinates too.</p>
      <button class="btn small" id="gr-add" disabled>Add a control point</button> <span class="hint" id="gr-step"></span>
      <table class="cm" id="gr-table" style="margin:8px 0"></table>
      <label>Method <select id="gr-method"><option value="affine">Affine (3+ points)</option><option value="poly2">Polynomial, 2nd order (6+)</option><option value="tps">Thin-plate spline (10+)</option></select></label>
      <p class="hint" id="gr-fit"></p>
      ${nameRow("gr", "georeferenced")}${runRow("gr", "Georeference")}</div>`,
    setup(LF) {
      const { $, $$, esc, fmt, api, map, toast, runButton, runJob, addRasterFromPath, addVectorLayer, saveLayers, getLayer, layers, showResult } = LF;
      // image: a picture / raster file; vector: a layer drawn into the same window (its own coordinates ↔ the map)
      const st = { image: null, vector: null, pts: [], pending: null, markers: [], win: null, fit: null };
      const win = document.createElement("div");
      win.className = "rb-float hidden"; win.id = "gr-float"; win.style.cssText = "width:520px;height:440px";
      win.innerHTML = `<div class="rb-float-head" id="gr-head" title="Drag to move"><b>Picture: click a place</b><button type="button" class="rb-float-x" id="gr-x" title="Close">×</button></div>
        <div style="position:relative;overflow:auto;flex:1;height:calc(100% - 34px)" id="gr-scroll"><div style="position:relative;display:inline-block" id="gr-stage"><img id="gr-img" alt="" style="display:block;max-width:none;cursor:crosshair"></div></div>`;
      document.body.appendChild(win);
      // beside the map's left edge, over Contents, so the map stays free to click (it can be dragged anywhere)
      st.win = LF.floatWin(win, $("#gr-head"), "gr-float", () => { const m = $("#map").getBoundingClientRect(); return { x: Math.max(8, m.left - 360), y: m.top + 16, w: 460, h: 420 }; });
      $("#gr-x").onclick = () => st.win.hide();
      const img = $("#gr-img");
      const scale = () => img.naturalWidth ? img.clientWidth / img.naturalWidth : 1;
      win.addEventListener("wheel", (e) => {   // zoom the picture with the wheel
        if (!e.target.closest("#gr-stage")) return;
        e.preventDefault();
        const w = Math.min(Math.max(img.clientWidth * (e.deltaY < 0 ? 1.25 : 0.8), 200), img.naturalWidth * 6);
        img.style.width = `${w}px`; drawDots();
      }, { passive: false });
      const drawDots = () => {
        $$("#gr-stage .gr-dot").forEach((d) => d.remove());
        [...st.pts, ...(st.pending ? [st.pending] : [])].forEach((p, i) => {
          const d = document.createElement("span");
          d.className = "gr-dot"; d.textContent = i + 1;
          d.style.cssText = `position:absolute;left:${p.px * scale() - 9}px;top:${p.py * scale() - 9}px;width:18px;height:18px;border-radius:50%;background:${p === st.pending ? "#f59e0b" : "#dc2626"};color:#fff;font:600 11px/18px sans-serif;text-align:center;pointer-events:none;box-shadow:0 0 0 2px #fff`;
          $("#gr-stage").appendChild(d);
        });
      };
      const table = () => {
        const f = st.fit;
        $("#gr-table").innerHTML = st.pts.length ? `<tr><th>#</th><th>Picture (px)</th><th>Map (lon, lat)</th><th title="How far the fitted point is from where you clicked">Residual</th><th></th></tr>` +
          st.pts.map((p, i) => `<tr${f?.worst === i ? ' style="color:var(--err)"' : ""}><td>${i + 1}</td><td>${Math.round(p.px)}, ${Math.round(p.py)}</td><td>${p.lon.toFixed(5)}, ${p.lat.toFixed(5)}</td>
            <td>${f?.residuals_m?.[i] != null ? `${fmt(f.residuals_m[i], 1)} m` : "–"}</td><td><button class="np-copy" data-rm="${i}" title="Remove">×</button></td></tr>`).join("") : "";
        $$("#gr-table [data-rm]").forEach((b) => b.onclick = () => { st.pts.splice(+b.dataset.rm, 1); st.markers.splice(+b.dataset.rm, 1)[0]?.remove(); renumber(); refit(); });
      };
      const renumber = () => st.markers.forEach((m, i) => m.setTooltipContent(String(i + 1)));
      async function refit() {
        st.fit = null; $("#gr-fit").textContent = "";
        if (st.pts.length >= 3) {
          try {
            st.fit = st.vector
              ? await api("/api/georef/vector", { method: "POST", json: { geojson: st.vector.geojson, points: st.pts.map(toSource), method: $("#gr-method").value } })
              : await api("/api/georef/fit", { method: "POST", json: { image: st.image.path, points: st.pts, method: $("#gr-method").value } });
            $("#gr-fit").innerHTML = `RMSE <b>${fmt(st.fit.rmse_m, 1)} m</b>${st.fit.pixel_m ? ` · about ${fmt(st.fit.pixel_m, 2)} m per pixel` : ""}${st.fit.worst != null ? ` · point ${st.fit.worst + 1} fits worst` : ""}`;
          } catch (e) { $("#gr-fit").textContent = e.message; }
        } else $("#gr-fit").textContent = `${3 - st.pts.length} more point${st.pts.length === 2 ? "" : "s"} needed.`;
        table(); drawDots();
      }
      $("#gr-method").onchange = refit;
      const reset = () => { st.pts = []; st.markers.forEach((m) => m.remove()); st.markers = []; st.pending = null; st.fit = null; };
      const showPicture = (src, width) => {
        st.win.show();
        // fitted to the window (the wheel zooms in)
        img.onload = () => { img.style.width = `${Math.min(img.naturalWidth, width || 500, ($("#gr-scroll").clientWidth || 480) - 6)}px`; drawDots(); };
        img.src = src;
        st.win.show(); $("#gr-add").disabled = false; refit();
      };
      // a vector layer drawn as a picture: its own coordinates (raw numbers for one without a coordinate system)
      const CANVAS = 1400;
      function drawVector(l) {
        const feats = l.geojson.features, b = l.rawBounds || rawB(feats);
        if (!b) throw new Error("The layer has no coordinates");
        const w = Math.max(b[2] - b[0], 1e-9), h = Math.max(b[3] - b[1], 1e-9), k = (CANVAS * 0.9) / Math.max(w, h);
        const cw = Math.round(w * k + CANVAS * 0.1), ch = Math.round(h * k + CANVAS * 0.1), ox = CANVAS * 0.05, oy = CANVAS * 0.05;
        const cv = Object.assign(document.createElement("canvas"), { width: cw, height: ch }), ctx = cv.getContext("2d");
        ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, cw, ch);
        // drawn bold: the window shows it at about a third of this size
        ctx.strokeStyle = l.color || "#2563eb"; ctx.fillStyle = (l.color || "#2563eb") + "33"; ctx.lineWidth = 5; ctx.lineJoin = ctx.lineCap = "round";
        const P = (c) => [ox + (c[0] - b[0]) * k, oy + (b[3] - c[1]) * k];
        const ring = (r) => r.forEach((c, i) => { const [x, y] = P(c); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
        const draw = (g) => {
          if (!g) return;
          const t = g.type, c = g.coordinates;
          if (t === "Point" || t === "MultiPoint") (t === "Point" ? [c] : c).forEach((q) => { const [x, y] = P(q); ctx.beginPath(); ctx.arc(x, y, 14, 0, 7); ctx.fillStyle = l.color || "#2563eb"; ctx.fill(); ctx.stroke(); });
          else if (t === "LineString" || t === "MultiLineString") (t === "LineString" ? [c] : c).forEach((ln) => { ctx.beginPath(); ring(ln); ctx.stroke(); });
          else if (t === "Polygon" || t === "MultiPolygon") (t === "Polygon" ? [c] : c).forEach((pg) => { ctx.beginPath(); pg.forEach(ring); ctx.fill("evenodd"); ctx.stroke(); });
          else if (t === "GeometryCollection") g.geometries.forEach(draw);
        };
        feats.forEach((f) => draw(f.geometry));
        st.vector = { id: l.id, geojson: l.geojson, b, k, ox, oy };
        return cv.toDataURL("image/png");
      }
      const rawB = (feats) => { const b = [Infinity, Infinity, -Infinity, -Infinity]; const walk = (c) => typeof c?.[0] === "number" ? (b[0] = Math.min(b[0], c[0]), b[1] = Math.min(b[1], c[1]), b[2] = Math.max(b[2], c[0]), b[3] = Math.max(b[3], c[1])) : c?.forEach?.(walk); feats.forEach((f) => walk(f.geometry?.coordinates)); return Number.isFinite(b[0]) ? b : null; };
      // a clicked pixel of the drawing → the layer's own coordinates
      const toSource = (p) => { const v = st.vector; return { x: v.b[0] + (p.px - v.ox) / v.k, y: v.b[3] - (p.py - v.oy) / v.k, lon: p.lon, lat: p.lat }; };
      async function useLayer(id) {
        const l = getLayer(id);
        if (!l) return;
        reset();
        try {
          if (l.type === "raster" && l.path) {
            const g = await api(`/api/georef/info?path=${encodeURIComponent(l.path)}`);
            st.image = g; st.vector = null;
            $("#gr-info").textContent = `${l.name}: ${g.width} × ${g.height} px, ${g.bands} band(s).`;
            showPicture(`/api/georef/image?path=${encodeURIComponent(g.path)}`);
          } else {
            st.image = null;
            const src = drawVector(l);
            $("#gr-info").textContent = `${l.name}: ${l.geojson.features.length} features${l.type === "unplaced" ? " without a coordinate system" : ""}. Click a recognisable corner or crossing on the drawing, then the same place on the map.`;
            showPicture(src, 500);
          }
          $("#gr-name").value = `${l.name.replace(/\.[^.]+$/, "")}_placed`.replace(/[^A-Za-z0-9_-]+/g, "_");
        } catch (e) { toast(e, true); }
      }
      async function usePath(path, name) {   // a raster or picture without a coordinate system (Add data ▸ Place with control points)
        reset();
        try {
          const g = await api(`/api/georef/info?path=${encodeURIComponent(path)}`);
          st.image = g; st.vector = null;
          $("#gr-info").textContent = `${name || g.name}: ${g.width} × ${g.height} px, ${g.bands} band(s).`;
          $("#gr-name").value = `${(name || g.name).replace(/\.[^.]+$/, "")}_georeferenced`.replace(/[^A-Za-z0-9_-]+/g, "_");
          showPicture(`/api/georef/image?path=${encodeURIComponent(g.path)}`);
        } catch (e) { toast(e, true); }
      }
      const fillLayersSel = () => {
        const sel = $("#gr-layer"), was = sel.value;
        const ls = layers.filter((l) => l.type === "unplaced" || (l.type === "vector" && l.geojson?.features?.length) || (l.type === "raster" && l.path));
        sel.innerHTML = `<option value="">(choose)</option>` + ls.map((l) => `<option value="${esc(l.id)}">${l.type === "unplaced" ? "⚠ " : ""}${esc(l.name)}</option>`).join("");
        if (ls.some((l) => l.id === was)) sel.value = was;
      };
      $("#gr-layer").onchange = () => $("#gr-layer").value && useLayer($("#gr-layer").value);
      $("#gr-file").onchange = async () => {
        const file = $("#gr-file").files[0];
        if (!file) return;
        const fd = new FormData(); fd.append("file", file);
        try {
          const r = await fetch("/api/georef/upload", { method: "POST", body: fd });
          const js = await r.json();
          if (!r.ok) throw new Error(js.detail || "Upload failed");
          reset(); st.image = js; st.vector = null; $("#gr-layer").value = "";
          $("#gr-info").textContent = `${file.name}: ${js.width} × ${js.height} px, ${js.bands} band(s).`;
          showPicture(`/api/georef/image?path=${encodeURIComponent(js.path)}`);
        } catch (e) { toast(e, true); }
      };
      const pickPicture = () => { $("#gr-step").textContent = "1 · Click the place on the picture"; img.dataset.picking = "1"; st.win.show(); };
      $("#gr-add").onclick = pickPicture;
      const onMap = (ev) => {   // the map half of a control point (one listener at a time)
        map.off("click", onMap);
        map.getContainer().style.cursor = "";
        if (!st.pending) return;
        const p = { ...st.pending, lon: +ev.latlng.lng.toFixed(7), lat: +ev.latlng.lat.toFixed(7) };
        st.pending = null; st.pts.push(p);
        st.markers.push(L.circleMarker(ev.latlng, { radius: 6, color: "#dc2626", weight: 2, fillOpacity: 0.8 }).bindTooltip(String(st.pts.length), { permanent: true, direction: "top" }).addTo(map));
        $("#gr-step").textContent = `${st.pts.length} point${st.pts.length === 1 ? "" : "s"}`;
        refit();
      };
      img.addEventListener("click", (e) => {
        if (!img.dataset.picking) return;
        const r = img.getBoundingClientRect(), px = (e.clientX - r.left) / scale(), py = (e.clientY - r.top) / scale();
        if (!Number.isFinite(px) || !Number.isFinite(py) || px < 0 || py < 0 || px > img.naturalWidth || py > img.naturalHeight) return;
        delete img.dataset.picking;
        st.pending = { px: +px.toFixed(2), py: +py.toFixed(2) };
        drawDots();
        $("#gr-step").textContent = "2 · Now click the same place on the map";
        map.getContainer().style.cursor = "crosshair";
        map.off("click", onMap); map.on("click", onMap);
      });
      runButton("gr", async () => {
        if (!st.image && !st.vector) throw new Error("Choose a picture or a layer first");
        st.pts = st.pts.filter((p) => [p.px, p.py, p.lon, p.lat].every(Number.isFinite));
        const need = { affine: 3, poly2: 6, tps: 10 }[$("#gr-method").value];
        if (st.pts.length < need) throw new Error(`Add at least ${need} control points for this method (you have ${st.pts.length})`);
        const name = $("#gr-name").value.trim() || "georeferenced";
        if (st.vector) {   // the layer's features moved by the control points
          const r = await api("/api/georef/vector", { method: "POST", json: { geojson: st.vector.geojson, points: st.pts.map(toSource), method: $("#gr-method").value } });
          const l = getLayer(st.vector.id);
          if (l?.type === "unplaced") {   // it had no coordinate system: it is placed now
            Object.assign(l, { type: "vector", geojson: r.geojson, crs: { crs: "EPSG:4326", name: `placed by ${r.points} control points`, epsg: null } });
            delete l.rawBounds;
            LF.refreshLayer(l);
          } else addVectorLayer(r.geojson, name, { color: l?.color });
          saveLayers();
          st.markers.forEach((m) => m.remove()); st.markers = [];
          return showResult("gr", `<b>Placed</b> with ${r.points} points${r.points > 3 ? `, RMSE ${fmt(r.rmse_m, 1)} m` : ""}. ${l?.type === "vector" && l.id === st.vector.id ? "The layer is on the map now." : "Added to Contents as a new layer."}`);
        }
        const r = await runJob("/api/georef/warp", { image: st.image.path, points: st.pts, method: $("#gr-method").value, name }, { tool: "georef", title: "Georeference" });
        await addRasterFromPath(r.path, { name, render: { rgb: [1, 2, 3], stretch: "none" } }).catch(() => addRasterFromPath(r.path, { name }));
        showResult("gr", `<b>Placed</b> with ${r.points} points, RMSE ${fmt(r.rmse_m, 1)} m · ${r.size[0]} × ${r.size[1]} px of ${fmt(r.res, 2)} m in ${esc(r.crs)}. Added to Contents; check it with Swipe or by changing its opacity.`);
      });
      return {
        open(arg) {
          fillLayersSel();
          if (arg?.layer) { $("#gr-layer").value = arg.layer; useLayer(arg.layer); }
          else if (arg?.path) usePath(arg.path, arg.name);
        },
        layersChanged: fillLayersSel,
      };
    },
  });
})();
