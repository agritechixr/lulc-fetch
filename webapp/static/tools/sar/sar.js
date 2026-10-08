/* Analysis ▸ SAR: Sentinel-1 radar. The SAR workflow (pick the data, it says what is already done, tick what you need;
   extra inputs appear with the steps that need them), the product inspector, finding scenes on Planetary Computer,
   speckle filtering, SAR features, and time series & change (flooding). Server: /api/sar/* · lulc_fetch/sar/. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const ST_ICON = { done: "✓", needed: "!", optional: "○", na: "–" };
  const ST_TEXT = { done: "already done", needed: "needed", optional: "optional", na: "not applicable" };
  const SPECKLE = [["none", "None"], ["refined_lee", "Refined Lee (keeps edges, recommended)"], ["lee", "Lee"], ["lee_sigma", "Lee Sigma"], ["gamma_map", "Gamma MAP"],
                   ["frost", "Frost"], ["boxcar", "Boxcar (mean: smoothest, blurs edges)"], ["median", "Median (slightly biased low)"]];
  const FEATS = [["ratio", "VV/VH ratio (dB)"], ["rvi", "RVI: radar vegetation index"], ["ndpi", "Normalised difference (VV − VH) / (VV + VH)"],
                 ["cross_ratio", "Cross ratio VH/VV"], ["span", "Span VV + VH (dB)"], ["texture", "Texture: local mean, std, variation, entropy"]];
  const STATS = [["mean", "Mean"], ["median", "Median"], ["min", "Minimum"], ["max", "Maximum"], ["std", "Standard deviation"], ["count", "Dates with data"], ["trend", "Trend (dB / year)"]];
  const runRow = (p, label) => `<button class="btn primary" id="${p}-run">${label}</button><p class="hint hidden" id="${p}-error" style="color:var(--err)"></p><div id="${p}-result" class="hidden"></div>`;
  const sarLayers = (LF) => LF.layers.filter((l) => l.type === "raster" && l.path && (/(^|[^a-z])(vv|vh|hh|hv)([^a-z]|$)/i.test((l.info?.bands || []).map((b) => b.description).join(" ") + " " + l.name) || /sar|rtc|grd|s1[abcd]/i.test(l.name)));
  LF.sar = { picked: [] };   // scenes chosen in "Find Sentinel-1 scenes", for the workflow

  // results into Contents (rasters; the dB ones first so they show)
  async function addOutputs(LF, outs) {
    for (const f of outs || []) if (/\.tiff?$/i.test(f)) await LF.addRasterFromPath(f, { zoom: false }).catch(() => {});
  }

  // ---------------- the SAR workflow
  LF.tool({ id: "sarflow", menu: "sar", title: "SAR workflow", icon: "sar", kinds: ["sar"],
    subtitle: "Sentinel-1 from a .SAFE / .zip, a Planetary Computer scene or a SAR layer: it says what is already done, you tick what you need (noise removal, calibration, speckle, terrain correction, flattening, dB, grid, clip, features, time series, change)",
    clip: { "sf-area": { what: "result is cut to" } },
    panel: `<div class="card"><h2>1 · Data ${tip("A GRD product (.SAFE folder or .zip, added with Insert ▸ Sentinel product), Sentinel-1 scenes from Planetary Computer (GRD to process, or RTC already analysis-ready: Find Sentinel-1 scenes), or SAR rasters already processed (from GEE, ASF or this app). Several dates make a time series.")}</h2>
        <div class="row tight" style="gap:10px;flex-wrap:wrap"><label class="check"><input type="radio" name="sf-src" value="scene" checked> Planetary Computer scenes</label>
          <label class="check"><input type="radio" name="sf-src" value="product"> A product on this computer</label><label class="check"><input type="radio" name="sf-src" value="raster"> SAR layers in Contents</label></div>
        <div data-src="scene"><div id="sf-scenes" class="sf-list"></div><button class="btn small" id="sf-find">Find scenes…</button></div>
        <div data-src="product" class="hidden"><select id="sf-product"></select><button class="btn small ghost" id="sf-add-product">Add a .SAFE product…</button></div>
        <div data-src="raster" class="hidden"><div id="sf-rasters" class="sf-list"></div></div>
        <div id="sf-info" class="sf-info"></div></div>
      <div class="card"><h2>2 · Steps ${tip("Ticked automatically from what the data still needs; greyed steps were already done by the data's producer and are never applied twice. Extra inputs appear under a step when it needs them.")}</h2>
        <div id="sf-steps"></div></div>
      <div class="card"><h2>Area &amp; grid</h2>${LF.html.area("sf-area", "Area", "The area to process (a whole scene is about 250 × 170 km). Draw one, or use a polygon layer.")}
        <div class="grid2"><label>Pixel size (m) <input type="number" id="sf-res" value="20" min="1" step="any"></label><label>Coordinate system <input type="text" id="sf-crs" value="auto" placeholder="auto (UTM) or EPSG:32643"></label></div>
        <label>Or align to the grid of <select id="sf-like"><option value="">(no: pixel size and system above)</option></select></label></div>
      <div class="card"><h2>3 · Analysis (optional)</h2>
        <div class="sf-ticks">${FEATS.map(([v, t]) => `<label class="check"><input type="checkbox" data-feat="${v}"> ${t}</label>`).join("")}</div>
        <div id="sf-multi" class="hidden"><div class="home-label">Several dates</div>
          <div class="sf-ticks">${STATS.map(([v, t]) => `<label class="check"><input type="checkbox" data-stat="${v}"> ${t}</label>`).join("")}</div>
          <label class="check"><input type="checkbox" id="sf-change"> Change first → last date (log-ratio, ±dB classes, new water / flooding)</label>
          <div class="grid2" id="sf-change-opts"><label>Change threshold (dB) <input type="number" id="sf-thr" value="3" step="0.5"></label><label>Water below (dB) <input type="number" id="sf-water" value="-18" step="0.5"></label></div></div></div>
      <div class="card"><label>Name of the results <input type="text" id="sf-name" placeholder="automatic" maxlength="80"></label>${runRow("sf", "Run SAR workflow")}</div>`,
    setup(LF) {
      const { $, $$, esc, api, toast, getLayer, layers, runButton, trackJob, showResult, getClip, openTool, prefs } = LF;
      const st = { info: null, products: [] };
      const src = () => $('input[name="sf-src"]:checked').value;
      const sources = () => src() === "scene" ? LF.sar.picked.map((s) => ({ scene: s.id }))
        : src() === "product" ? ($("#sf-product").value ? [{ product: $("#sf-product").value }] : [])
        : $$("#sf-rasters input:checked").map((c) => ({ raster: getLayer(c.value)?.path })).filter((x) => x.raster);
      function renderScenes() {
        const p = LF.sar.picked;
        $("#sf-scenes").innerHTML = p.length ? p.map((s, i) => `<div class="sf-row"><span><b>${esc(s.date)}</b> ${esc(s.platform)} · ${s.collection.endsWith("rtc") ? "RTC (analysis-ready)" : "GRD"} · ${esc(s.orbit_direction || "")} R${s.relative_orbit ?? "?"}</span><button class="np-copy" data-rm="${i}" title="Remove">×</button></div>`).join("")
          : `<p class="hint" style="margin:0">No scene chosen yet: <b>Find scenes…</b> searches Planetary Computer for your area.</p>`;
        $$("#sf-scenes [data-rm]").forEach((b) => b.onclick = () => { LF.sar.picked.splice(+b.dataset.rm, 1); renderScenes(); check(); });
      }
      async function loadProducts() {
        try { st.products = (await api("/api/products")).products.filter((p) => p.kind?.startsWith("S1")); } catch { st.products = []; }
        $("#sf-product").innerHTML = st.products.length ? st.products.map((p) => `<option value="${esc(p.path)}">${esc(p.title)} · ${esc(p.date)}</option>`).join("") : `<option value="">No Sentinel-1 product added yet</option>`;
      }
      function renderRasters() {
        const was = new Set($$("#sf-rasters input:checked").map((c) => c.value));
        const ls = sarLayers(LF);
        $("#sf-rasters").innerHTML = ls.length ? ls.map((l) => `<label class="check"><input type="checkbox" value="${esc(l.id)}" ${was.has(l.id) ? "checked" : ""}> ${esc(l.name)}</label>`).join("")
          : `<p class="hint" style="margin:0">No SAR raster in Contents (bands named VV / VH, or a name with SAR / RTC / S1).</p>`;
        $$("#sf-rasters input").forEach((c) => c.onchange = check);
        const like = $("#sf-like"), w = like.value;
        like.innerHTML = `<option value="">(no: pixel size and system above)</option>` + layers.filter((l) => l.type === "raster" && l.path).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
        if (getLayer(w)) like.value = w;
      }
      $$('input[name="sf-src"]').forEach((r) => r.onchange = () => { $$("[data-src]").forEach((d) => d.classList.toggle("hidden", d.dataset.src !== src())); check(); });
      $("#sf-find").onclick = () => openTool("sarsearch");
      $("#sf-add-product").onclick = () => document.querySelector('[data-cmd="open-safe"]')?.click();
      $("#sf-product").onchange = check;
      $("#sf-change").onchange = () => $("#sf-change-opts").classList.toggle("hidden", !$("#sf-change").checked);
      $("#sf-change-opts").classList.add("hidden");

      // what the data is → the steps' status and ticks
      let seq = 0;
      async function check() {
        const s = sources(), me = ++seq;
        $("#sf-multi").classList.toggle("hidden", s.length < 2);
        if (!s.length) { $("#sf-info").innerHTML = ""; st.info = null; return renderSteps(); }
        $("#sf-info").innerHTML = `<p class="hint"><span class="spinner"></span> Reading the data… (about 10 s for a Planetary Computer scene)</p>`;
        let info;
        try { info = await api("/api/sar/inspect", { method: "POST", json: s[0] }); } catch (e) { if (me === seq) $("#sf-info").innerHTML = `<div class="warn">${esc(e.message)}</div>`; return; }
        if (me !== seq) return;
        st.info = info;
        const kv = [["Mission", info.mission], ["Product", info.type], ["Mode", info.mode], ["Polarisation", (info.polarisations || []).join(" + ")], ["Orbit", [info.orbit_direction, info.relative_orbit && `relative ${info.relative_orbit}`].filter(Boolean).join(", ")],
                    ["Date", info.date], ["Incidence (mid)", info.incidence_mid && `${info.incidence_mid}°`], ["Processor", info.ipf && `IPF ${info.ipf}`], ["Values", info.units], ["Source", info.source]].filter(([, v]) => v);
        $("#sf-info").innerHTML = `<table class="kv">${kv.map(([k, v]) => `<tr><td>${k}</td><td>${esc(String(v))}</td></tr>`).join("")}</table>
          <p class="${info.supported ? "hint" : "warn"}" style="margin-top:6px">${esc(info.summary || "")}${s.length > 1 ? ` <b>${s.length} inputs</b>: each is processed the same way.` : ""}</p>`;
        renderSteps();
      }
      // the steps: status from the inspector, ticks, and the options each needs
      function renderSteps() {
        const info = st.info, box = $("#sf-steps");
        if (!info) { box.innerHTML = `<p class="hint">Choose the data first.</p>`; return; }
        if (!info.supported) { box.innerHTML = `<p class="hint">This data can't be processed here (see above).</p>`; return; }
        const opt = {
          calibrate: `<div class="sf-opt"><label>Backscatter coefficient <select id="sf-kind"><option value="sigma0">σ⁰ sigma nought (land, most usual)</option><option value="gamma0">γ⁰ gamma nought (ellipsoid)</option><option value="beta0">β⁰ beta nought (radar brightness)</option></select></label></div>`,
          speckle: `<div class="sf-opt grid2"><label>Filter <select id="sf-sp">${SPECKLE.slice(1).map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></label><label>Window <select id="sf-sp-size"><option>3</option><option selected>5</option><option>7</option><option>9</option></select></label></div>`,
          terrain: `<div class="sf-opt"><label>DEM <select id="sf-dem"><option value="">Copernicus DEM 30 m (downloaded for the area)</option>${layers.filter((l) => l.type === "raster" && l.path).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("")}</select></label>
            <label class="check"><input type="checkbox" id="sf-masks"> Also incidence angles and a layover / shadow map</label></div>`,
          flatten: `<div class="sf-opt hint" style="margin:0">Gives γ⁰ terrain-flattened (angular volume model, Vollrath et al. 2020); layover and shadow are masked. Needs terrain correction.</div>`,
          orbit: `<div class="sf-opt hint" style="margin:0">ESA's precise orbit (POEORB) is downloaded when published (~20 days after the acquisition); otherwise the product's own orbit is used and the result says so.</div>`,
        };
        box.innerHTML = info.steps.map((s) => {
          const fixed = s.status === "done" || (s.status === "na" && s.note) || s.step === "validate";
          const on = s.status === "needed" || s.step === "db" || s.step === "validate" || s.status === "done";
          const shown = !["reproject", "resample", "clip"].includes(s.step);   // the grid card handles those
          if (!shown) return "";
          return `<div class="sf-step ${s.status}" data-step="${s.step}"><label class="check"><input type="checkbox" data-st="${s.step}" ${on ? "checked" : ""} ${fixed ? "disabled" : ""}>
              <b>${esc(s.title)}</b> <span class="sf-pill ${s.status}" title="${esc(ST_TEXT[s.status])}">${ST_ICON[s.status]} ${esc(ST_TEXT[s.status])}</span></label>
            <small>${esc(s.note || s.about)}</small>${opt[s.step] && !fixed ? `<div class="sf-opts ${on ? "" : "hidden"}">${opt[s.step]}</div>` : ""}</div>`;
        }).join("");
        $$("#sf-steps [data-st]").forEach((c) => c.onchange = () => {
          c.closest(".sf-step").querySelector(".sf-opts")?.classList.toggle("hidden", !c.checked);
          if (c.dataset.st === "flatten" && c.checked) { const t = $('#sf-steps [data-st="terrain"]'); if (t && !t.disabled && !t.checked) { t.checked = true; t.onchange(); } }
          if (c.dataset.st === "terrain" && !c.checked) { const f = $('#sf-steps [data-st="flatten"]'); if (f && !f.disabled && f.checked) { f.checked = false; f.onchange(); } }
        });
      }
      runButton("sf", async () => {
        const s = sources();
        if (!s.length) throw new Error("Choose the SAR data first");
        if (st.info && !st.info.supported) throw new Error(st.info.summary);
        const ticked = $$("#sf-steps [data-st]:checked:not(:disabled)").map((c) => c.dataset.st);
        const aoi = getClip("sf-area"), like = getLayer($("#sf-like").value);
        if (!aoi && !like && src() !== "raster") throw new Error("Choose the area (a whole scene is too big): draw one or use a polygon layer");
        const steps = [...ticked, "reproject", ...(aoi ? ["clip"] : [])];
        const body = { sources: s, steps, kind: $("#sf-kind")?.value || "sigma0",
          speckle: ticked.includes("speckle") ? { method: $("#sf-sp").value, size: +$("#sf-sp-size").value } : { method: "none" },
          dem: getLayer($("#sf-dem")?.value)?.path || null, masks: !!$("#sf-masks")?.checked && ticked.includes("terrain"),
          res: +$("#sf-res").value || null, crs: $("#sf-crs").value.trim() || "auto", like: like?.path || null, aoi, clip: !!aoi, db: ticked.includes("db") || !!st.info?.steps.find((x) => x.step === "db" && x.status === "done"),
          features: $$("[data-feat]:checked").map((c) => c.dataset.feat), temporal: s.length > 1 ? $$("[data-stat]:checked").map((c) => c.dataset.stat) : [],
          change: s.length > 1 && $("#sf-change").checked, change_threshold: +$("#sf-thr").value || 3, water_db: +$("#sf-water").value, name: $("#sf-name").value.trim() };
        const j = await api("/api/sar/process", { method: "POST", json: body });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs);
        const run = r.runs[0] || {};
        showResult("sf", `<b>Done</b>: ${r.runs.length} input${r.runs.length > 1 ? "s" : ""} → ${esc(run.units || "")}${run.grid ? ` · ${run.grid.shape[1]} × ${run.grid.shape[0]} px of ${run.grid.res} m (${esc(run.grid.crs)})` : ""}.
          <ul class="sf-done">${[["Steps", (run.steps_done || []).filter((x) => x !== "validate").join(" → ")], ["DEM", run.dem], ["Orbit", run.orbit_note || run.orbit], ["Speckle", run.speckle], ["Looks", run.looks && run.looks.join(" × ")]].filter(([, v]) => v).map(([k, v]) => `<li><b>${k}</b>: ${esc(v)}</li>`).join("")}</ul>
          ${r.change ? `<div class="dist">${r.change.map((c) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span>${esc(c.class)}</span><b>${c.area_ha != null ? `${LF.fmt(c.area_ha, 1)} ha` : c.pixels}</b></div>`).join("")}</div>` : ""}
          <span class="hint">${r.outputs.length} file${r.outputs.length === 1 ? "" : "s"} added to Contents (linear power is kept; the dB file is for viewing).</span>`);
      });
      return {
        async open(arg) {
          if (arg?.scenes) { LF.sar.picked = arg.scenes; $('input[name="sf-src"][value="scene"]').checked = true; $$("[data-src]").forEach((d) => d.classList.toggle("hidden", d.dataset.src !== "scene")); }
          if (arg?.layer) { $('input[name="sf-src"][value="raster"]').checked = true; $$("[data-src]").forEach((d) => d.classList.toggle("hidden", d.dataset.src !== "raster")); }
          renderScenes(); await loadProducts(); renderRasters();
          if (arg?.layer) $(`#sf-rasters input[value="${CSS.escape(arg.layer)}"]`)?.click();
          check();
        },
        layersChanged() { renderRasters(); },
      };
    },
  });

  // ---------------- find Sentinel-1 scenes (Planetary Computer)
  LF.tool({ id: "sarsearch", menu: "sar", title: "Find Sentinel-1 scenes", icon: "search", kinds: [],
    subtitle: "Search Planetary Computer (free) for Sentinel-1 over your area: analysis-ready RTC or GRD to process yourself, filtered to one polarisation, mode, orbit direction and track so a time series is consistent",
    clip: { "sq-area": { what: "search covers" } },
    panel: `<div class="card"><h2>Search ${tip("Planetary Computer's Sentinel-1 RTC (γ⁰ terrain-corrected, analysis-ready: nothing to redo) or GRD (to process with the SAR workflow). For a time series keep one orbit direction and one relative orbit: different tracks see the ground from different angles.")}</h2>
      ${LF.html.area("sq-area", "Area")}
      <div class="grid2"><label>From <input type="date" id="sq-start"></label><label>To <input type="date" id="sq-end"></label></div>
      <div class="grid2"><label>Product <select id="sq-col"><option value="rtc">RTC: analysis-ready (recommended)</option><option value="grd">GRD: process it yourself</option></select></label>
        <label>Polarisation <select id="sq-pol"><option>VV+VH</option><option>HH+HV</option><option>VV</option><option>HH</option></select></label></div>
      <div class="grid2"><label>Orbit direction <select id="sq-orbit"><option value="">Any</option><option value="ascending">Ascending</option><option value="descending">Descending</option></select></label>
        <label>Relative orbit <input type="number" id="sq-rel" min="1" max="175" placeholder="any"></label></div>
      ${runRow("sq", "Search")}<div id="sq-list"></div></div>`,
    setup(LF) {
      const { $, $$, esc, api, toast, getClip, openTool, runButton } = LF;
      const d = new Date(), iso = (x) => x.toISOString().slice(0, 10);
      $("#sq-end").value = iso(d); $("#sq-start").value = iso(new Date(d - 60 * 864e5));
      let found = [];
      runButton("sq", async () => {
        const aoi = getClip("sq-area");
        if (!aoi) throw new Error("Choose the area: draw one or use a polygon layer");
        const r = await api("/api/sar/search", { method: "POST", json: { aoi, start: $("#sq-start").value, end: $("#sq-end").value, collection: $("#sq-col").value,
          pols: $("#sq-pol").value, orbit: $("#sq-orbit").value || null, rel_orbit: +$("#sq-rel").value || null } });
        found = r.scenes;
        const tracks = r.orbits.length > 1 ? `<div class="warn" style="margin-top:6px">${r.orbits.length} different tracks (${r.orbits.map((o) => `${o.direction} R${o.relative}`).join(", ")}): for a time series pick one orbit direction and relative orbit.</div>` : "";
        $("#sq-list").innerHTML = found.length ? `${tracks}<div class="row between" style="margin-top:8px"><b>${found.length} scene${found.length > 1 ? "s" : ""}</b><span><a href="#" id="sq-all">all</a> · <a href="#" id="sq-none">none</a></span></div>
          <div class="sf-list">${found.map((s, i) => `<label class="check"><input type="checkbox" data-i="${i}" ${i === found.length - 1 ? "checked" : ""}> <b>${esc(s.date)}</b> ${esc(s.time)} · ${esc(s.platform)} · ${esc(s.orbit_direction || "")} R${s.relative_orbit} · ${(s.polarisations || []).join("+")} · ${s.coverage ?? "?"}% of the area</label>`).join("")}</div>
          <button class="btn primary" id="sq-use" style="margin-top:6px">Use in the SAR workflow</button>` : `<p class="hint">No scenes: widen the dates or relax the filters.</p>`;
        $("#sq-all")?.addEventListener("click", (e) => { e.preventDefault(); $$("#sq-list [data-i]").forEach((c) => { c.checked = true; }); });
        $("#sq-none")?.addEventListener("click", (e) => { e.preventDefault(); $$("#sq-list [data-i]").forEach((c) => { c.checked = false; }); });
        $("#sq-use")?.addEventListener("click", () => {
          const sel = $$("#sq-list [data-i]:checked").map((c) => found[+c.dataset.i]);
          if (!sel.length) return toast("Tick at least one scene", true);
          openTool("sarflow", { scenes: sel });
          setTimeout(() => { const a = $("#sf-area"), b = $("#sq-area"); if (a && b && [...a.options].some((o) => o.value === b.value)) { a.value = b.value; a.dispatchEvent(new Event("change")); } }, 50);
        });
      });
      return { open() {} };
    },
  });

  // ---------------- product inspector
  LF.tool({ id: "sarinspect", menu: "sar", title: "SAR product inspector", icon: "info", kinds: [],
    subtitle: "What a Sentinel-1 product or SAR raster is (mission, product type, mode, polarisation, orbit, processing) and which processing steps are done, needed or optional",
    panel: `<div class="card"><h2>Inspect</h2><div class="row tight" style="gap:10px"><label class="check"><input type="radio" name="si-src" value="product" checked> A product on this computer</label><label class="check"><input type="radio" name="si-src" value="raster"> A SAR layer</label></div>
      <select id="si-product"></select><select id="si-raster" class="hidden"></select>${runRow("si", "Inspect")}</div>`,
    setup(LF) {
      const { $, $$, esc, api, runButton, showResult, getLayer, layers } = LF;
      const fill = async () => {
        let ps = [];
        try { ps = (await api("/api/products")).products.filter((p) => p.kind?.startsWith("S1")); } catch {}
        $("#si-product").innerHTML = ps.length ? ps.map((p) => `<option value="${esc(p.path)}">${esc(p.title)} · ${esc(p.date)}</option>`).join("") : `<option value="">No Sentinel-1 product added (Insert ▸ Sentinel product)</option>`;
        $("#si-raster").innerHTML = layers.filter((l) => l.type === "raster" && l.path).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("") || `<option value="">No raster layer</option>`;
      };
      $$('input[name="si-src"]').forEach((r) => r.onchange = () => { const p = $('input[name="si-src"]:checked').value === "product"; $("#si-product").classList.toggle("hidden", !p); $("#si-raster").classList.toggle("hidden", p); });
      runButton("si", async () => {
        const p = $('input[name="si-src"]:checked').value === "product";
        const body = p ? { product: $("#si-product").value } : { raster: getLayer($("#si-raster").value)?.path };
        if (!body.product && !body.raster) throw new Error("Choose a product or a layer");
        const i = await api("/api/sar/inspect", { method: "POST", json: body });
        const kv = [["Mission", i.mission], ["Product", i.type], ["Mode", i.mode], ["Polarisation", (i.polarisations || []).join(" + ")], ["Orbit", [i.orbit_direction, i.relative_orbit && `relative ${i.relative_orbit}`, i.absolute_orbit && `absolute ${i.absolute_orbit}`].filter(Boolean).join(", ")],
                    ["Date", i.date], ["Size", i.size && `${i.size[0]} × ${i.size[1]} px`], ["Pixel spacing", i.pixel_spacing && `${i.pixel_spacing.join(" × ")} m`], ["Incidence (mid)", i.incidence_mid && `${i.incidence_mid}°`],
                    ["Heading", i.heading != null && `${i.heading}°`], ["Processor", i.ipf && `IPF ${i.ipf}`], ["Values", i.units], ["Coordinate system", i.crs], ["Source", i.source]].filter(([, v]) => v);
        showResult("si", `<table class="kv">${kv.map(([k, v]) => `<tr><td>${k}</td><td>${esc(String(v))}</td></tr>`).join("")}</table><p class="${i.supported ? "hint" : "warn"}">${esc(i.summary || "")}</p>
          <div class="home-label">Processing steps</div><div class="sf-status">${(i.steps || []).map((s) => `<div class="sf-step ${s.status}"><span class="sf-pill ${s.status}">${ST_ICON[s.status]} ${ST_TEXT[s.status]}</span> <b>${esc(s.title)}</b><small>${esc(s.note || s.about)}</small></div>`).join("")}</div>
          ${i.supported ? `<button class="btn small primary" id="si-go">Process it in the SAR workflow</button>` : ""}`);
        $("#si-go")?.addEventListener("click", () => LF.openTool("sarflow", p ? {} : { layer: $("#si-raster").value }));
      });
      return { open: fill, layersChanged: fill };
    },
  });

  // ---------------- speckle filter, features, time series & change (on SAR layers)
  LF.tool({ id: "sarspeckle", menu: "sar", title: "Speckle filter", icon: "renhance", kinds: [],
    subtitle: "Reduce the speckle of a SAR layer: Refined Lee, Lee, Lee Sigma, Gamma MAP, Frost, boxcar or median (on linear power, whatever the layer's units)",
    panel: `<div class="card"><h2>Speckle filter ${tip("Speckle is the grainy noise of radar. Filters trade it against detail: start with Refined Lee 5 × 5. The equivalent number of looks (ENL) of Sentinel-1 GRDH is about 4.4 (more after multilooking or resampling to bigger pixels).")}</h2>
      <label>SAR layer <select id="sk-raster"></select></label><div class="grid2"><label>Filter <select id="sk-m">${SPECKLE.slice(1).map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></label>
      <label>Window <select id="sk-size"><option>3</option><option selected>5</option><option>7</option><option>9</option><option>11</option></select></label></div>
      <label>Looks (ENL) <input type="number" id="sk-looks" placeholder="4.4" min="0.5" step="any"></label>${runRow("sk", "Filter")}</div>`,
    setup(LF) {
      const { $, api, fillLayers, getLayer, runButton, trackJob, showResult } = LF;
      runButton("sk", async () => {
        const l = getLayer($("#sk-raster").value);
        if (!l) throw new Error("Choose a SAR layer");
        const j = await api("/api/sar/process", { method: "POST", json: { sources: [{ raster: l.path }], steps: ["speckle"], speckle: { method: $("#sk-m").value, size: +$("#sk-size").value, looks: +$("#sk-looks").value || null }, db: true, crs: "auto", name: `${l.name.replace(/\.tiff?$/i, "")}_${$("#sk-m").value}` } });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs);
        showResult("sk", `<b>Filtered</b> (${LF.esc(r.runs[0]?.speckle || "")}). <span class="hint">Added to Contents.</span>`);
      });
      const fill = (pick) => fillLayers($("#sk-raster"), sarLayers(LF), { empty: "No SAR layer in Contents", pick });
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  LF.tool({ id: "sarfeatures", menu: "sar", title: "SAR features", icon: "analyze", kinds: [],
    subtitle: "From a dual-pol SAR layer: VV/VH ratio, RVI (radar vegetation index), polarisation indices, span and texture (for crop mapping and classification)",
    panel: `<div class="card"><h2>Features</h2><label>SAR layer <select id="sx-raster"></select></label>
      <div class="sf-ticks">${FEATS.map(([v, t], i) => `<label class="check"><input type="checkbox" data-f="${v}" ${i < 3 ? "checked" : ""}> ${t}</label>`).join("")}</div>
      <div class="grid2"><label>Texture of <select id="sx-tb"><option>VV</option><option>VH</option><option>HH</option><option>HV</option></select></label><label>Window <input type="number" id="sx-win" value="7" min="3" max="31" step="2"></label></div>
      ${runRow("sx", "Compute")}</div>`,
    setup(LF) {
      const { $, $$, api, fillLayers, getLayer, runButton, trackJob, showResult } = LF;
      runButton("sx", async () => {
        const l = getLayer($("#sx-raster").value);
        if (!l) throw new Error("Choose a SAR layer");
        const f = $$("#tab-sarfeatures [data-f]:checked").map((c) => c.dataset.f);
        if (!f.length) throw new Error("Tick at least one feature");
        const j = await api("/api/sar/features", { method: "POST", json: { raster: l.path, features: f, texture_band: $("#sx-tb").value, window: +$("#sx-win").value || 7 } });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs);
        showResult("sx", `<b>${f.length} feature${f.length > 1 ? "s" : ""}</b> in one file (a band each). <span class="hint">Added to Contents.</span>`);
      });
      const fill = (pick) => fillLayers($("#sx-raster"), sarLayers(LF), { empty: "No SAR layer in Contents", pick });
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  LF.tool({ id: "sarseries", menu: "sar", title: "SAR time series & change", icon: "timeseries", kinds: [],
    subtitle: "Several dates of SAR: per-pixel mean, median, min, max, std and trend; and the change from the first to the last date, with flooding (new open water)",
    panel: `<div class="card"><h2>Dates ${tip("SAR layers of the same track (orbit direction and relative orbit), in date order: their dates come from their names or tags. Averages are taken on linear power.")}</h2><div id="sr-list" class="sf-list"></div>
      <div class="sf-ticks">${STATS.map(([v, t], i) => `<label class="check"><input type="checkbox" data-s="${v}" ${i < 5 || v === "trend" ? "checked" : ""}> ${t}</label>`).join("")}</div>
      <label class="check"><input type="checkbox" id="sr-change" checked> Change first → last (log-ratio, classes, new water)</label>
      <div class="grid2"><label>Polarisation <select id="sr-pol"><option>VV</option><option>VH</option><option>HH</option><option>HV</option></select></label><label>Threshold (dB) <input type="number" id="sr-thr" value="3" step="0.5"></label></div>
      <label>Water below (dB) <input type="number" id="sr-water" value="-18" step="0.5"></label>${runRow("sr", "Analyse")}</div>`,
    setup(LF) {
      const { $, $$, esc, api, getLayer, runButton, trackJob, showResult } = LF;
      const fill = () => {
        const was = new Set($$("#sr-list input:checked").map((c) => c.value));
        const ls = sarLayers(LF);
        $("#sr-list").innerHTML = ls.length ? ls.map((l) => `<label class="check"><input type="checkbox" value="${esc(l.id)}" ${was.has(l.id) ? "checked" : ""}> ${esc(l.name)}</label>`).join("") : `<p class="hint">No SAR layers in Contents.</p>`;
      };
      runButton("sr", async () => {
        const ls = $$("#sr-list input:checked").map((c) => getLayer(c.value)).filter(Boolean);
        if (ls.length < 2) throw new Error("Tick at least two dates");
        const j = await api("/api/sar/series", { method: "POST", json: { rasters: ls.map((l) => l.path), stats: $$("#tab-sarseries [data-s]:checked").map((c) => c.dataset.s),
          change: $("#sr-change").checked, pol: $("#sr-pol").value, threshold_db: +$("#sr-thr").value || 3, water_db: +$("#sr-water").value } });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs);
        showResult("sr", `<b>${ls.length} dates</b>${r.dates ? ` (${r.dates.filter(Boolean).join(", ")})` : ""}.${r.change ? `<div class="dist">${r.change.map((c) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span>${esc(c.class)}</span><b>${c.area_ha != null ? `${LF.fmt(c.area_ha, 1)} ha` : c.pixels}</b></div>`).join("")}</div>` : ""} <span class="hint">Added to Contents.</span>`);
      });
      return { open: fill, layersChanged: fill };
    },
  });
})();
