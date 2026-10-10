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
  const FEAT_HELP = {
    ratio: "VV minus VH in dB. High on bare soil and water (little volume scattering), lower on vegetation: separates crops from bare fields and follows crop growth.",
    rvi: "Radar vegetation index 4·VH / (VV + VH), from about 0 (bare soil) to 1 (dense vegetation): rises as a crop grows, like an NDVI that works through clouds.",
    ndpi: "(VV − VH) / (VV + VH), between −1 and 1: like the ratio, but bounded; often used as a feature for classification.",
    cross_ratio: "VH / VV in linear power: the inverse of the ratio, rising with vegetation.",
    span: "VV + VH: the total power returned, in dB. Bright for towns and forests, dark for water and smooth surfaces.",
    texture: "How the backscatter varies around each pixel (mean, standard deviation, coefficient of variation, entropy): separates smooth fields, forests and towns that have the same brightness.",
  };
  const STATS = [["mean", "Mean"], ["median", "Median"], ["min", "Minimum"], ["max", "Maximum"], ["std", "Standard deviation"], ["count", "Dates with data"], ["trend", "Trend (dB / year)"]];
  // the ⓘ of each processing step: what it does, and when it matters
  const STEP_HELP = {
    validate: "Reads the product's metadata (mission, mode, polarisations, processor version, orbit) and checks it is a GRD that can be processed. Always done.",
    orbit: "Terrain correction places every pixel from where the satellite was. The orbit inside the product is good to ~1 m; ESA's restituted orbit (RESORB, within hours) and precise orbit (POEORB, after ~20 days) to a few cm. Worth ticking for terrain correction; it needs the internet.",
    border: "Older products (processor before 2.90, until 2018) have noisy, falsely dark strips at the left and right edges of the image. Newer products have them removed already.",
    thermal: "The radar receiver adds its own noise, which makes dark surfaces (calm water, smooth fields, roads), mostly in VH, look brighter than they are and leaves stripes between the sub-swaths. Subtracting it is recommended, especially for water and flood mapping.",
    calibrate: "Turns the image's digital numbers into backscatter (how much of the radar's energy comes back), comparable between dates and sensors. Needed for any measurement.",
    speckle: "Speckle is the grainy salt-and-pepper look of radar. A filter makes fields and water uniform, at the cost of some detail. Useful for mapping and classification; with several dates the multi-temporal filter (below) does better.",
    terrain: "Puts every pixel where it really is on the ground, using a DEM (heights): without it, hills are shifted towards the satellite by hundreds of metres. Needed to overlay radar on maps and other images.",
    flatten: "Slopes facing the satellite look much brighter, slopes facing away darker, whatever is on them. Flattening removes that, so a forest or a field reads the same on any slope. Needed in hilly areas; it gives γ⁰.",
    normalise: "Backscatter falls as the viewing angle grows (from about 30° at one edge of the image to 46° at the other). Normalising makes it as if seen at one angle: needed to compare near and far range, or images from different orbit tracks.",
    db: "Decibels, 10·log10 of the power: the usual scale for looking at radar (water about −20 dB, fields −15 to −10, towns 0 or more). The linear file is kept too: averages and statistics must be taken on it.",
  };
  const runRow = (p, label) => `<button class="btn primary" id="${p}-run">${label}</button><p class="hint hidden" id="${p}-error" style="color:var(--err)"></p><div id="${p}-result" class="hidden"></div>`;
  const sarLayers = (LF) => LF.layers.filter((l) => l.type === "raster" && l.path && (/(^|[^a-z])(vv|vh|hh|hv)([^a-z]|$)/i.test((l.info?.bands || []).map((b) => b.description).join(" ") + " " + l.name) || /sar|rtc|grd|s1[abcd]/i.test(l.name)));
  LF.sar = { picked: [] };   // scenes chosen in "Find Sentinel-1 scenes", for the workflow

  // results into Contents (rasters; the dB ones first so they show)
  async function addOutputs(LF, outs) {
    for (const f of outs || []) if (/\.tiff?$/i.test(f)) await LF.addRasterFromPath(f, { zoom: false }).catch(() => {});
  }

  // ---------------- the SAR workflow
  LF.tool({ id: "sarflow", menu: "sar", title: "SAR workflow", icon: "sar", kinds: ["sar"],
    subtitle: "Sentinel-1 from a .SAFE / .zip, a Planetary Computer scene or a SAR layer: it says what is already done, you tick what you need (orbit, noise removal, calibration, speckle, terrain correction, flattening, angle normalisation, dB, grid, clip, quality layer, features, time series, multi-temporal filter, change)",
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
      <div class="card"><h2>Area &amp; grid</h2>${LF.html.area("sf-area", "Area", "The area to process. Without one the whole scene is processed (about 250 × 170 km, in tiles: it takes a while).")}
        <div class="grid2"><label>Pixel size (m) ${tip("The output pixel. Sentinel-1 GRD is ~20 × 22 m resolution sampled at 10 m: 10 m keeps everything, 20 m is close to the real resolution and has less speckle (the image is multilooked to it), 30 m or more for large areas.")} <input type="number" id="sf-res" value="20" min="1" step="any"></label><label>Coordinate system ${tip("auto: the UTM zone of the area (metres, right for measuring areas). Or any EPSG code, e.g. EPSG:4326 for latitude / longitude, EPSG:32643 for UTM 43 N.")} <input type="text" id="sf-crs" value="auto" placeholder="auto (UTM) or EPSG:32643"></label></div>
        <label>Or align to the grid of ${tip("Use exactly the pixels of another layer (same system, pixel size and extent), e.g. a Sentinel-2 image or a DEM, so the radar can be stacked with it pixel for pixel. Overrides pixel size and system.")} <select id="sf-like"><option value="">(no: pixel size and system above)</option></select></label>
        <label class="check"><input type="checkbox" id="sf-frames" checked> Join frames of one pass ${tip("Sentinel-1 cuts each pass into ~170 km frames. When the area crosses a frame edge, the next frame of the same pass is found and both are joined into one image.")}</label>
        <label class="check"><input type="checkbox" id="sf-quality" checked> Quality layer and metadata ${tip("A layer saying for every pixel: valid, layover, shadow, below the noise floor (weaker than the sensor's noise: unreliable, typical of calm water in VH) or outside the area; and a .json describing the processing (CEOS analysis-ready style).")}</label></div>
      <div class="card"><h2>3 · Analysis (optional) ${tip("Extra layers made from the processed backscatter, all in one file (a band each), for crop mapping, classification and monitoring. Need both polarisations (VV + VH) except texture.")}</h2>
        <div class="sf-ticks">${FEATS.map(([v, t]) => `<label class="check"><input type="checkbox" data-feat="${v}"> ${t} ${tip(FEAT_HELP[v])}</label>`).join("")}</div>
        <div id="sf-multi" class="hidden"><div class="home-label">Several dates ${tip("With two or more inputs (dates of the same track and orbit direction). Statistics are per pixel over all dates, taken on linear power; the change compares the first and last dates.")}</div>
          <label class="check"><input type="checkbox" id="sf-mt"> Multi-temporal speckle filter (Quegan) ${tip("Filters each date with all the others: much less speckle (up to the number of dates × the looks) while each date keeps its own changes and the full resolution. Best for crop monitoring and change. Needs the dates on one grid (same area and pixel size).")}</label>
          <div class="grid2 hidden" id="sf-mt-opts"><label>Window ${tip("The neighbourhood for each date's local mean. 7 × 7 is usual; bigger is smoother. Unlike spatial filters, detail is kept: only the speckle is averaged across the dates.")} <select id="sf-mt-size"><option>5</option><option selected>7</option><option>9</option><option>11</option></select></label></div>
          <div class="sf-ticks">${STATS.map(([v, t]) => `<label class="check"><input type="checkbox" data-stat="${v}"> ${t}</label>`).join("")}</div>
          <label class="check"><input type="checkbox" id="sf-change"> Change first → last date (log-ratio, ±dB classes, new water / flooding) ${tip("The log-ratio (last ÷ first, in dB) per pixel, and classes: decrease, no change, increase, new water (flooding) and water at both dates, with hectares. Best with the same track and the multi-temporal or a speckle filter.")}</label>
          <div class="grid2" id="sf-change-opts"><label>Change threshold (dB) ${tip("How much the backscatter must change to count: 3 dB (half or double the power) is usual; 1.5–2 dB catches subtler changes but more speckle.")} <input type="number" id="sf-thr" value="3" step="0.5"></label><label>Water below (dB) ${tip("Calm open water reflects the radar away and is very dark: below about −18 dB in VV (−24 in VH). Lower it if wet fields are taken for water; raise it if windy water is missed.")} <input type="number" id="sf-water" value="-18" step="0.5"></label></div></div></div>
      <div class="card"><label>Name of the results ${tip("The start of the files' names (e.g. flood_ → flood_dB.tif, flood_linear.tif …). Automatic: the satellite, date and mode, e.g. S1C_20261001_IW.")} <input type="text" id="sf-name" placeholder="automatic" maxlength="80"></label>${runRow("sf", "Run SAR workflow")}</div>`,
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
      $("#sf-mt").onchange = () => $("#sf-mt-opts").classList.toggle("hidden", !$("#sf-mt").checked);
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
          calibrate: `<div class="sf-opt"><label>Backscatter coefficient ${tip("σ⁰ (sigma nought): per unit of ground area, the usual one for land, crops and water. γ⁰ (gamma nought): per area seen by the radar, less dependent on the viewing angle. β⁰ (beta nought): the radar's own brightness, before any ground geometry. Terrain flattening always starts from the right one itself.")} <select id="sf-kind"><option value="sigma0">σ⁰ sigma nought (land, most usual)</option><option value="gamma0">γ⁰ gamma nought (ellipsoid)</option><option value="beta0">β⁰ beta nought (radar brightness)</option></select></label></div>`,
          speckle: `<div class="sf-opt grid2"><label>Filter ${tip("Refined Lee: smooths inside fields but keeps their edges (a good default). Lee / Lee Sigma: adaptive, keep bright points. Gamma MAP: statistically optimal, keeps edges. Frost: weighted by distance. Boxcar: a plain mean, smoothest but blurs edges. Median: removes outliers, slightly darkens.")} <select id="sf-sp">${SPECKLE.slice(1).map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></label><label>Window ${tip("The neighbourhood used, in pixels. 3 × 3 keeps detail but leaves more speckle; 7 × 7 or 9 × 9 is smoother but merges small fields. 5 × 5 is a good start.")} <select id="sf-sp-size"><option>3</option><option selected>5</option><option>7</option><option>9</option></select></label></div>`,
          terrain: `<div class="sf-opt"><label>DEM ${tip("Heights of the ground. Copernicus DEM (30 m, free) is downloaded for the area automatically; or use your own DEM layer (e.g. a drone or LiDAR one) if it is better. Heights are converted to the ellipsoid with the EGM96 geoid.")} <select id="sf-dem"><option value="">Copernicus DEM 30 m (downloaded for the area)</option>${layers.filter((l) => l.type === "raster" && l.path).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("")}</select></label>
            <label class="check"><input type="checkbox" id="sf-masks"> Also the incidence angles (ellipsoid and local) ${tip("An extra file with, for every pixel, the angle the radar looked at the ground: on the ellipsoid (flat earth) and the local one (including the slope). Useful for your own corrections, or for normalising later.")}</label></div>`,
          flatten: `<div class="sf-opt"><label>Method ${tip("Area-based (Small 2011): adds up the ground area each radar pixel really sees, from every DEM cell: the most exact, used by ASF and Planetary Computer, keeps a value in layover. Angular (Vollrath 2020): from each pixel's slope angle alone: faster, close on gentle slopes, masks layover.")} <select id="sf-flat"><option value="area">Area-based (Small 2011): the illuminated area of every DEM cell, best on slopes</option><option value="angular">Angular (Vollrath 2020): from the slope angle, faster</option></select></label>
            <p class="hint" style="margin:2px 0 0">Gives γ⁰ terrain-flattened. Shadow is masked; with area-based, layover keeps a value (flagged in the quality layer). Needs terrain correction.</p></div>`,
          normalise: `<div class="sf-opt grid2"><label>Reference angle (°) ${tip("The angle every pixel is made to look as if seen at. 40° (near the middle of the image) is usual; use the same one for every date you compare.")} <input type="number" id="sf-nref" value="40" min="10" max="60" step="1"></label><label>Exponent ${tip("How strongly backscatter falls with the angle: (cos ref / cos θ)ⁿ. 2 (Lambert's law) fits σ⁰ of most land; γ⁰, already divided by cos θ once, needs about 1. Leave empty for that automatic choice; water and towns don't follow it well.")} <input type="number" id="sf-nexp" placeholder="auto" min="0" max="4" step="0.1"></label></div>
            <p class="hint" style="margin:2px 0 0">σ⁰ × (cos ref / cos θ)ⁿ: n = 2 for σ⁰, 1 for γ⁰ (auto). Uses the local incidence angle with a DEM.</p>`,
          orbit: `<div class="sf-opt hint" style="margin:0">ESA's precise orbit (POEORB, ~5 cm) once published (~20 days after the acquisition), else the restituted one (RESORB, ~10 cm, within hours); the result says which.</div>`,
        };
        box.innerHTML = info.steps.map((s) => {
          const fixed = s.status === "done" || (s.status === "na" && s.note) || s.step === "validate";
          const on = s.status === "needed" || s.step === "db" || s.step === "validate" || s.status === "done";
          const shown = !["reproject", "resample", "clip"].includes(s.step);   // the grid card handles those
          if (!shown) return "";
          return `<div class="sf-step ${s.status}" data-step="${s.step}"><label class="check"><input type="checkbox" data-st="${s.step}" ${on ? "checked" : ""} ${fixed ? "disabled" : ""}>
              <b>${esc(s.title)}</b>${STEP_HELP[s.step] ? " " + tip(STEP_HELP[s.step]) : ""} <span class="sf-pill ${s.status}" title="${esc(ST_TEXT[s.status])}">${ST_ICON[s.status]} ${esc(ST_TEXT[s.status])}</span></label>
            <small>${esc(s.note || s.about)}</small>${opt[s.step] && !fixed ? `<div class="sf-opts ${on ? "" : "hidden"}">${opt[s.step]}</div>` : ""}</div>`;
        }).join("");
        $$("#sf-steps [data-st]").forEach((c) => c.onchange = () => {
          c.closest(".sf-step").querySelector(".sf-opts")?.classList.toggle("hidden", !c.checked);
          if ((c.dataset.st === "flatten" || c.dataset.st === "normalise") && c.checked && !$('#sf-steps [data-st="calibrate"]')?.checked) { const k = $('#sf-steps [data-st="calibrate"]'); if (k && !k.disabled) { k.checked = true; k.onchange(); } }
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
        if (!aoi && !like && src() !== "raster" && !confirm("No area chosen: process the whole scene (about 250 × 170 km)? It is done in tiles and can take an hour or more.")) return;
        const steps = [...ticked, "reproject", ...(aoi ? ["clip"] : [])];
        const body = { sources: s, steps, kind: $("#sf-kind")?.value || "sigma0",
          speckle: ticked.includes("speckle") ? { method: $("#sf-sp").value, size: +$("#sf-sp-size").value } : { method: "none" },
          dem: getLayer($("#sf-dem")?.value)?.path || null, masks: !!$("#sf-masks")?.checked && ticked.includes("terrain"),
          res: +$("#sf-res").value || null, crs: $("#sf-crs").value.trim() || "auto", like: like?.path || null, aoi, clip: !!aoi, db: ticked.includes("db") || !!st.info?.steps.find((x) => x.step === "db" && x.status === "done"),
          features: $$("[data-feat]:checked").map((c) => c.dataset.feat), temporal: s.length > 1 ? $$("[data-stat]:checked").map((c) => c.dataset.stat) : [],
          change: s.length > 1 && $("#sf-change").checked, change_threshold: +$("#sf-thr").value || 3, water_db: +$("#sf-water").value, name: $("#sf-name").value.trim(),
          flatten_method: $("#sf-flat")?.value || "area", normalise_ref: +$("#sf-nref")?.value || 40, normalise_n: $("#sf-nexp")?.value === "" || !$("#sf-nexp") ? null : +$("#sf-nexp").value,
          quality: $("#sf-quality").checked, join_frames: $("#sf-frames").checked, multitemporal: s.length > 1 && $("#sf-mt").checked, mt_size: +$("#sf-mt-size").value };
        const j = await api("/api/sar/process", { method: "POST", json: body });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs);
        const run = r.runs[0] || {};
        showResult("sf", `<b>Done</b>: ${r.runs.length} input${r.runs.length > 1 ? "s" : ""} → ${esc(run.units || "")}${run.grid ? ` · ${run.grid.shape[1]} × ${run.grid.shape[0]} px of ${run.grid.res} m (${esc(run.grid.crs)})` : ""}.
          <ul class="sf-done">${[["Steps", (run.steps_done || []).filter((x) => x !== "validate").join(" → ")], ["DEM", run.dem], ["Orbit", run.orbit_note || run.orbit], ["Note", run.note], ["Speckle", run.speckle], ["Looks", run.looks && run.looks.join(" × ")]].filter(([, v]) => v).map(([k, v]) => `<li><b>${k}</b>: ${esc(v)}</li>`).join("")}</ul>
          ${run.quality && Object.keys(run.quality).length ? `<div class="home-label">Quality</div><div class="dist">${Object.entries(run.quality).map(([k, v]) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span>${esc(k)}</span><b>${v} %</b></div>`).join("")}</div>` : ""}
          ${(r.notes || []).map((n) => `<p class="hint" style="margin:4px 0 0">${esc(n)}</p>`).join("")}
          ${r.multitemporal ? `<p class="hint" style="margin:4px 0 0">Multi-temporal filter over ${r.multitemporal.dates} dates: ${r.multitemporal.enl_gain ? `${r.multitemporal.enl_gain}× the looks` : "done"}.</p>` : ""}
          ${run.tiles > 1 ? `<p class="hint" style="margin:4px 0 0">Processed in ${run.tiles} tiles.</p>` : ""}
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
      <label class="check"><input type="checkbox" id="sr-mt"> Multi-temporal speckle filter (Quegan) first: filtered copies of the dates, added to Contents</label>
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
          change: $("#sr-change").checked, multitemporal: $("#sr-mt").checked, pol: $("#sr-pol").value, threshold_db: +$("#sr-thr").value || 3, water_db: +$("#sr-water").value } });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs);
        showResult("sr", `<b>${ls.length} dates</b>${r.dates ? ` (${r.dates.filter(Boolean).join(", ")})` : ""}.${r.change ? `<div class="dist">${r.change.map((c) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span>${esc(c.class)}</span><b>${c.area_ha != null ? `${LF.fmt(c.area_ha, 1)} ha` : c.pixels}</b></div>`).join("")}</div>` : ""} <span class="hint">Added to Contents.</span>`);
      });
      return { open: fill, layersChanged: fill };
    },
  });

  // ---------------- InSAR & RTC on demand (ASF HyP3, NASA Earthdata login)
  LF.tool({ id: "sarhyp3", menu: "sar", title: "InSAR & RTC on demand (ASF HyP3)", icon: "sar", kinds: [],
    subtitle: "Interferograms (ground movement, coherence) from pairs of Sentinel-1 SLC scenes, or RTC from GRD, processed for you by ASF HyP3: free with a NASA Earthdata login (a monthly allowance)",
    clip: { "hy-area": { what: "search covers" } },
    panel: `<div class="card"><h2>Account ${tip("ASF HyP3 processes Sentinel-1 on NASA's servers, free with an Earthdata login (a monthly allowance of credits). Add the login in Credentials ▸ NASA Earthdata: a token is best (urs.earthdata.nasa.gov ▸ Generate Token).")}</h2><div id="hy-user"><p class="hint">Checking…</p></div></div>
      <div class="card"><h2>Find scenes</h2>${LF.html.area("hy-area", "Area")}
        <div class="grid2"><label>From <input type="date" id="hy-start"></label><label>To <input type="date" id="hy-end"></label></div>
        <div class="grid2"><label>Product <select id="hy-type"><option value="INSAR_GAMMA">InSAR: interferograms from SLC pairs</option><option value="RTC_GAMMA">RTC: terrain-corrected backscatter from GRD</option></select></label>
          <label>Orbit direction <select id="hy-orbit"><option value="">Any</option><option value="ascending">Ascending</option><option value="descending">Descending</option></select></label></div>
        <div class="grid2" id="hy-pairopts"><label>Pair each date with the next <select id="hy-step"><option value="1">1 date (6–12 days: best coherence)</option><option value="2">2 dates</option><option value="3">3 dates</option></select></label>
          <label>At most (days apart) <input type="number" id="hy-maxd" value="48" min="6" max="400"></label></div>
        <button class="btn" id="hy-search">Search ASF</button><p class="hint hidden" id="hy-search-err" style="color:var(--err)"></p><div id="hy-list"></div></div>
      <div class="card"><h2>Options</h2>
        <div data-hy="INSAR_GAMMA"><div class="grid2"><label>Looks (range × azimuth) <select id="hy-looks"><option value="20x4">20 × 4: 80 m pixels (faster, less noise)</option><option value="10x2">10 × 2: 40 m pixels</option></select></label></div>
          <label class="check"><input type="checkbox" id="hy-disp" checked> Displacement maps (line of sight and vertical, metres)</label>
          <label class="check"><input type="checkbox" id="hy-water"> Leave large water bodies out of the phase unwrapping</label></div>
        <div data-hy="RTC_GAMMA" class="hidden"><div class="grid2"><label>Pixel size <select id="hy-res"><option value="30">30 m</option><option value="20">20 m</option><option value="10">10 m</option></select></label>
          <label>Radiometry <select id="hy-rad"><option value="gamma0">γ⁰ (terrain-flattened)</option><option value="sigma0">σ⁰</option></select></label></div>
          <label class="check"><input type="checkbox" id="hy-speckle"> Enhanced Lee speckle filter</label></div>
        <label>Job name <input type="text" id="hy-name" value="lulc-fetch" maxlength="90"></label>
        ${runRow("hy", "Send to HyP3")}</div>
      <div class="card"><div class="row between"><h2 style="margin:0">Jobs</h2><button class="btn small ghost" id="hy-refresh">Refresh</button></div><div id="hy-jobs"><p class="hint">—</p></div></div>`,
    setup(LF) {
      const { $, $$, esc, api, toast, getClip, runButton, trackJob, showResult, addRasterFromPath } = LF;
      const d = new Date(), iso = (x) => x.toISOString().slice(0, 10);
      $("#hy-end").value = iso(d); $("#hy-start").value = iso(new Date(d - 45 * 864e5));
      let found = { scenes: [], pairs: [] }, account = null;
      const type = () => $("#hy-type").value;
      const syncType = () => { $$("[data-hy]").forEach((x) => x.classList.toggle("hidden", x.dataset.hy !== type())); $("#hy-pairopts").classList.toggle("hidden", type() !== "INSAR_GAMMA"); $("#hy-list").innerHTML = ""; };
      $("#hy-type").onchange = syncType;
      async function loadUser() {
        const box = $("#hy-user");
        try {
          account = await api("/api/sar/hyp3/user");
          const cost = account.costs || {};
          if (account.status !== "APPROVED") {
            box.innerHTML = `<p>Logged in as <b>${esc(account.user)}</b>. HyP3 access: <b>${esc(String(account.status || "").toLowerCase().replace("_", " "))}</b>.</p>
              ${account.status === "PENDING" ? `<p class="hint">ASF is reviewing the request (usually within a day).</p>` : `<p class="hint">Ask once for access (free): say briefly what you'll use it for. <a href="${esc(account.access_help)}" target="_blank" rel="noopener">More</a></p>
              <label>What for <textarea id="hy-use" rows="3" placeholder="e.g. Mapping land subsidence and flooding around … for a university project / farm monitoring"></textarea></label>
              <button class="btn small primary" id="hy-ask">Request access</button>`}`;
            $("#hy-ask")?.addEventListener("click", async () => {
              try { await api("/api/sar/hyp3/access", { method: "POST", json: { use_case: $("#hy-use").value.trim() } }); toast("Access requested"); loadUser(); }
              catch (e) { toast(e, true); }
            });
            return;
          }
          box.innerHTML = `<p>Logged in as <b>${esc(account.user)}</b> · <b>${account.credits ?? "?"}</b> credits left${account.credits_per_month ? ` of ${account.credits_per_month} this month` : ""}.</p>
            <p class="hint" style="margin:0">Cost per job: InSAR ${cost.INSAR_GAMMA ?? "?"} · RTC ${cost.RTC_GAMMA ?? "?"} credits.</p>`;
          loadJobs();
        } catch (e) {
          account = null;
          box.innerHTML = `<div class="warn">${esc(e.message)}</div><button class="btn small" id="hy-creds">Open Credentials…</button>
            <p class="hint">No account yet? <a href="https://urs.earthdata.nasa.gov/users/new" target="_blank" rel="noopener">Create a free NASA Earthdata login</a>, then Generate Token there.</p>`;
          $("#hy-creds").onclick = () => document.querySelector('[data-cmd="credentials"]')?.click();
        }
      }
      $("#hy-search").onclick = async () => {
        const err = $("#hy-search-err"); err.classList.add("hidden");
        const aoi = getClip("hy-area");
        if (!aoi) { err.textContent = "Choose the area: draw one or use a polygon layer"; err.classList.remove("hidden"); return; }
        $("#hy-list").innerHTML = `<p class="hint"><span class="spinner"></span> Searching ASF…</p>`;
        try {
          found = await api("/api/sar/asf/search", { method: "POST", json: { aoi, start: $("#hy-start").value, end: $("#hy-end").value, level: type() === "INSAR_GAMMA" ? "SLC" : "GRD_HD",
            orbit: $("#hy-orbit").value || null, step: +$("#hy-step").value, max_days: +$("#hy-maxd").value || 48 } });
        } catch (e) { $("#hy-list").innerHTML = ""; err.textContent = e.message; err.classList.remove("hidden"); return; }
        const rows = type() === "INSAR_GAMMA"
          ? found.pairs.map((p, i) => `<label class="check"><input type="checkbox" data-i="${i}" ${i < 3 ? "checked" : ""}> <b>${esc(p.dates[0])} → ${esc(p.dates[1])}</b> (${p.days} days) · ${esc(p.orbit_direction)} path ${p.path} frame ${p.frame}</label>`)
          : found.scenes.map((s, i) => `<label class="check"><input type="checkbox" data-i="${i}" ${i === found.scenes.length - 1 ? "checked" : ""}> <b>${esc(s.date)}</b> ${esc(s.platform)} · ${esc(s.orbit_direction)} path ${s.path} · ${s.coverage ?? "?"}% of the area</label>`);
        const what = type() === "INSAR_GAMMA" ? `${found.pairs.length} pair${found.pairs.length === 1 ? "" : "s"} from ${found.scenes.length} SLC scenes` : `${found.scenes.length} GRD scene${found.scenes.length === 1 ? "" : "s"}`;
        $("#hy-list").innerHTML = rows.length ? `<div class="row between" style="margin-top:8px"><b>${what}</b><span><a href="#" data-all>all</a> · <a href="#" data-none>none</a></span></div><div class="sf-list">${rows.join("")}</div>
          ${type() === "INSAR_GAMMA" ? `<p class="hint" style="margin:0">Pairs keep one track and frame; shorter gaps keep more coherence (vegetation and wet soil lose it fast).</p>` : ""}`
          : `<p class="hint">Nothing found: widen the dates${type() === "INSAR_GAMMA" ? " (a pair needs two passes of the same track)" : ""}.</p>`;
        $("#hy-list [data-all]")?.addEventListener("click", (e) => { e.preventDefault(); $$("#hy-list [data-i]").forEach((c) => { c.checked = true; }); });
        $("#hy-list [data-none]")?.addEventListener("click", (e) => { e.preventDefault(); $$("#hy-list [data-i]").forEach((c) => { c.checked = false; }); });
      };
      runButton("hy", async () => {
        if (!account) throw new Error("Add your NASA Earthdata login first (Credentials ▸ NASA Earthdata)");
        if (account.status !== "APPROVED") throw new Error("HyP3 access isn't approved yet");
        const sel = $$("#hy-list [data-i]:checked").map((c) => +c.dataset.i);
        if (!sel.length) throw new Error("Search and tick at least one " + (type() === "INSAR_GAMMA" ? "pair" : "scene"));
        const body = { job_type: type(), name: $("#hy-name").value.trim() || "lulc-fetch", looks: $("#hy-looks").value, displacement: $("#hy-disp").checked, water_mask: $("#hy-water").checked,
          resolution: +$("#hy-res").value, radiometry: $("#hy-rad").value, speckle: $("#hy-speckle").checked,
          ...(type() === "INSAR_GAMMA" ? { pairs: sel.map((i) => [found.pairs[i].reference, found.pairs[i].secondary]) } : { granules: sel.map((i) => found.scenes[i].name) }) };
        const cost = (account.costs || {})[type()];
        if (cost != null && !confirm(`Send ${sel.length} job${sel.length > 1 ? "s" : ""} to HyP3: about ${sel.length * cost} credits of your ${account.credits}?`)) return;
        const r = await api("/api/sar/hyp3/submit", { method: "POST", json: body });
        showResult("hy", `<b>${r.jobs.length} job${r.jobs.length > 1 ? "s" : ""} sent.</b> <span class="hint">HyP3 usually takes 20–60 minutes per InSAR pair (a few minutes for RTC): Refresh the jobs below, then Download.</span>`);
        loadUser();
      });
      const STC = { SUCCEEDED: "done", RUNNING: "needed", PENDING: "optional", FAILED: "na" };
      async function loadJobs() {
        const box = $("#hy-jobs");
        if (!account || account.status !== "APPROVED") { box.innerHTML = `<p class="hint">—</p>`; return; }
        box.innerHTML = `<p class="hint"><span class="spinner"></span> Loading…</p>`;
        let js;
        try { js = (await api("/api/sar/hyp3/jobs")).jobs; } catch (e) { box.innerHTML = `<div class="warn">${esc(e.message)}</div>`; return; }
        box.innerHTML = js.length ? js.slice(0, 50).map((j) => `<div class="sf-step ${STC[j.status] || ""}"><span class="sf-pill ${STC[j.status] || ""}">${esc(String(j.status || "").toLowerCase())}</span>
            <b>${j.type === "INSAR_GAMMA" ? "InSAR" : "RTC"}</b> ${esc(j.name || "")} <small>${esc((j.granules || []).map((g) => g.slice(17, 25)).join(" → "))} · sent ${esc((j.submitted || "").slice(0, 16).replace("T", " "))}${j.expires ? ` · kept until ${esc(j.expires.slice(0, 10))}` : ""}</small>
            ${j.status === "SUCCEEDED" ? `<button class="btn small" data-dl="${esc(j.id)}">Download &amp; add to Contents</button>` : ""}</div>`).join("")
          : `<p class="hint">No jobs in the last 30 days.</p>`;
        $$("#hy-jobs [data-dl]").forEach((b) => b.onclick = async () => {
          b.disabled = true;
          try {
            const j = await api("/api/sar/hyp3/download", { method: "POST", json: { job_id: b.dataset.dl } });
            const r = (await trackJob(j, { title: j.title })).result;
            await addOutputs(LF, r.outputs);
            toast(`${r.outputs.length} layer${r.outputs.length === 1 ? "" : "s"} added (${r.folder})`);
          } catch (e) { if (LF.notCancelled(e)) toast(e, true); } finally { b.disabled = false; }
        });
      }
      $("#hy-refresh").onclick = () => loadUser();
      syncType();
      return { open() { loadUser(); } };
    },
  });

  // ---------------- SAR + optical fusion (what radar adds to an optical map)
  LF.tool({ id: "sarfusion", menu: "sar", title: "SAR + optical fusion", icon: "rasterml", kinds: [],
    subtitle: "Map crops or land cover from Sentinel-2 and Sentinel-1 together: compares optical only, SAR only, early fusion (stacked) and late fusion (combined, SAR alone under clouds) on spatial blocks, then maps with the best",
    panel: `<div class="card"><h2>1 · Data ${tip("The optical image sets the grid (pixel size and extent): the SAR layers are put onto it. Process the SAR with the SAR workflow first (terrain-flattened γ⁰; several dates of one track, ideally with the multi-temporal filter).")}</h2>
        <label>Optical image ${tip("A Sentinel-2 (or other optical) image with band names (B02, B03, B04, B08, B11 …) so indices can be computed. Its SCL band, if present, marks clouds.")} <select id="fu-opt"></select></label>
        <div class="home-label">SAR layers ${tip("One date: its VV, VH, ratio and RVI. Several dates: per-date values plus their mean, std, min and max: the change over the season is what separates crops best.")}</div><div id="fu-sars" class="sf-list"></div>
        <label class="check"><input type="checkbox" id="fu-scl" checked> Mask clouds with the optical image's SCL band ${tip("Sentinel-2 L2A's scene classification: cloud shadow, medium / high cloud and cirrus pixels lose their optical values, so only the SAR speaks there.")}</label>
        <label>Or a cloud mask layer ${tip("Any raster where non-zero means cloud (e.g. from s2cloudless or Fmask).")} <select id="fu-cloud"></select></label>
        <label>Embedding to compare (optional) ${tip("E.g. Google AlphaEarth (already a learned blend of optical, radar and more): added as its own column in the comparison, not into the fusion.")} <select id="fu-emb"></select></label></div>
      <div class="card"><h2>2 · Features</h2>
        <div class="sf-ticks">${["NDVI", "EVI", "NDRE", "NDWI", "MNDWI", "NDMI"].map((v) => `<label class="check"><input type="checkbox" data-fi="${v}" checked> ${v}</label>`).join("")}</div>
        <label class="check"><input type="checkbox" id="fu-dates" checked> Each SAR date as its own features ${tip("Off: only the statistics over the dates (fewer features, faster). On: the model also sees when things changed.")}</label>
        <label class="check"><input type="checkbox" id="fu-tex"> SAR texture ${tip("Local variation of VV over 7 × 7 pixels: helps separate forests, orchards and towns.")}</label></div>
      <div class="card"><h2>3 · Ground truth ${tip("Labelled examples: polygons or points with a class attribute (crop, land cover…), or a class raster. Spread them over the area: validation leaves whole blocks out.")}</h2>
        <select id="fu-gt"></select><div id="fu-gt-v" class="hidden"><label>Class attribute <select id="fu-gt-field"></select></label></div>
        <div id="fu-gt-r" class="hidden"><label>Band <input type="number" id="fu-gt-band" value="1" min="1"></label></div></div>
      <div class="card"><h2>4 · Model &amp; validation</h2>
        <div class="grid2"><label>Model ${tip("LightGBM: fast and accurate, handles missing optical values itself (recommended). Random Forest: robust, the classic. XGBoost: similar to LightGBM.")} <select id="fu-model"><option value="lgbm">LightGBM</option><option value="rf">Random Forest</option><option value="xgb">XGBoost</option></select></label>
          <label>Map with ${tip("Best: the fusion (early or late) with the higher F1 in validation. Or force one, e.g. late fusion for a very cloudy image.")} <select id="fu-map"><option value="best">The best fusion</option><option value="early">Early fusion</option><option value="selected">Early fusion, selected features</option><option value="late">Late fusion</option><option value="optical">Optical only</option><option value="sar">SAR only</option></select></label></div>
        <div class="grid3"><label>Block (m) ${tip("Validation leaves whole squares of this size out (and whole polygons), so the score isn't inflated by neighbouring, near-identical pixels. About 5–10 × a field's width; smaller if the ground truth is in a small area.")} <input type="number" id="fu-block" value="1000" min="20" step="100"></label>
          <label>Folds ${tip("How many times the blocks are split into training and test.")} <input type="number" id="fu-folds" value="5" min="2" max="10"></label>
          <label>Pixels / class ${tip("At most this many training pixels per class (sampled evenly).")} <input type="number" id="fu-pc" value="3000" min="50" step="500"></label></div>
        <label class="check"><input type="checkbox" id="fu-select" checked> Feature selection ${tip("Also tries early fusion with only the features that beat a shuffled copy of themselves (a light Boruta), chosen inside each validation fold. Many weak SAR features (e.g. several dates of a clear dry season) can otherwise dilute the strong optical ones.")}</label>
        <label>Name <input type="text" id="fu-name" value="fusion" maxlength="80"></label>${runRow("fu", "Compare & map")}</div>`,
    setup(LF) {
      const { $, $$, esc, api, layers, getLayer, fillLayers, runButton, trackJob, showResult } = LF;
      const isSar = (l) => sarLayers(LF).includes(l);
      const fill = () => {
        const rasters = layers.filter((l) => l.type === "raster" && l.path);
        fillLayers($("#fu-opt"), rasters.filter((l) => !isSar(l)), { empty: "No optical image in Contents" });
        const was = new Set($$("#fu-sars input:checked").map((c) => c.value));
        const ls = sarLayers(LF);
        $("#fu-sars").innerHTML = ls.length ? ls.map((l) => `<label class="check"><input type="checkbox" value="${esc(l.id)}" ${was.has(l.id) || !was.size ? "checked" : ""}> ${esc(l.name)}</label>`).join("")
          : `<p class="hint" style="margin:0">No SAR layer: process Sentinel-1 with the SAR workflow first.</p>`;
        const opts = (sel, first) => { const w = sel.value; sel.innerHTML = `<option value="">${first}</option>` + rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join(""); if (getLayer(w)) sel.value = w; };
        opts($("#fu-cloud"), "(none)"); opts($("#fu-emb"), "(none)");
        const gw = $("#fu-gt").value;
        const gts = layers.filter((l) => (l.type === "vector" && l.geojson?.features?.length) || (l.type === "raster" && l.path));
        $("#fu-gt").innerHTML = `<option value="">Choose…</option>` + gts.map((l) => `<option value="${esc(l.id)}">${l.type === "vector" ? "▢" : "▦"} ${esc(l.name)}</option>`).join("");
        if (getLayer(gw)) $("#fu-gt").value = gw;
        gtChanged();
      };
      function gtChanged() {
        const l = getLayer($("#fu-gt").value);
        $("#fu-gt-v").classList.toggle("hidden", l?.type !== "vector");
        $("#fu-gt-r").classList.toggle("hidden", l?.type !== "raster");
        if (l?.type === "vector") {
          const keys = [...new Set(l.geojson.features.flatMap((f) => Object.keys(f.properties || {})))];
          const w = $("#fu-gt-field").value;
          $("#fu-gt-field").innerHTML = keys.map((k) => `<option>${esc(k)}</option>`).join("") || `<option value="">(no attributes: one class)</option>`;
          const guess = keys.find((k) => /class|crop|type|label|lc|name/i.test(k));
          $("#fu-gt-field").value = keys.includes(w) ? w : (guess || keys[0] || "");
        }
      }
      $("#fu-gt").onchange = gtChanged;
      const pct = (v) => `${(100 * v).toFixed(1)} %`;
      runButton("fu", async () => {
        const o = getLayer($("#fu-opt").value);
        if (!o) throw new Error("Choose the optical image");
        const sars = $$("#fu-sars input:checked").map((c) => getLayer(c.value)?.path).filter(Boolean);
        if (!sars.length) throw new Error("Tick at least one SAR layer");
        const g = getLayer($("#fu-gt").value);
        if (!g) throw new Error("Choose the ground truth");
        const ground_truth = g.type === "raster" ? { type: "raster", path: g.path, band: +$("#fu-gt-band").value || 1 } : { type: "vector", geojson: g.geojson, field: $("#fu-gt-field").value || null };
        const j = await api("/api/sar/fusion", { method: "POST", json: { optical: o.path, sars, ground_truth, cloud: getLayer($("#fu-cloud").value)?.path || null, scl: $("#fu-scl").checked,
          indices: $$("[data-fi]:checked").map((c) => c.dataset.fi), sar_dates: $("#fu-dates").checked, texture: $("#fu-tex").checked, embedding: getLayer($("#fu-emb").value)?.path || null,
          model: $("#fu-model").value, block_m: +$("#fu-block").value || 1000, folds: +$("#fu-folds").value || 5, per_class: +$("#fu-pc").value || 3000, map_with: $("#fu-map").value,
          select: $("#fu-select").checked, name: $("#fu-name").value.trim() || "fusion" } });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs.slice(0, 2));
        const top = Math.max(...r.table.map((t) => t.f1));
        const sets = r.table.map((t) => t.set);
        showResult("fu", `<b>${esc(r.table.find((t) => t.set === r.best).title)}</b> is best: macro F1 ${r.table.find((t) => t.set === r.best).f1.toFixed(3)}${r.gain.vs_optical_f1 != null ? ` (${r.gain.vs_optical_f1 >= 0 ? "+" : ""}${(100 * r.gain.vs_optical_f1).toFixed(1)} points over optical only)` : ""}.
          <table class="kv" style="margin-top:6px"><tr><td></td><td><b>OA</b></td><td><b>κ</b></td><td><b>F1</b></td><td><b>Covers</b></td></tr>
          ${r.table.map((t) => `<tr${t.set === r.best ? ' style="font-weight:600"' : ""}><td>${esc(t.title)}</td><td>${pct(t.oa)}</td><td>${t.kappa.toFixed(3)}</td><td><span style="display:inline-block;height:8px;width:${Math.round(60 * t.f1 / top)}px;background:var(--accent);border-radius:2px;vertical-align:middle"></span> ${t.f1.toFixed(3)}</td><td>${t.coverage} %</td></tr>`).join("")}</table>
          <p class="hint" style="margin:4px 0 0">${r.pixels.toLocaleString()} pixels, ${r.folds}-fold validation on ${r.blocks} blocks of ${r.block_m} m (whole blocks left out). Covers: the share of the test pixels that set could classify (optical alone can't under clouds).</p>
          <div class="home-label">F1 per class</div><table class="kv"><tr><td></td>${sets.map((s) => `<td><b>${esc({ optical: "Opt", sar: "SAR", embedding: "Emb", early: "Early", selected: "Sel.", late: "Late" }[s])}</b></td>`).join("")}</tr>
          ${r.classes.map((c) => `<tr><td>${esc(c)}</td>${sets.map((s) => `<td>${(r.per_class[s]?.[c] ?? 0).toFixed(2)}</td>`).join("")}</tr>`).join("")}</table>
          ${r.selection ? `<p class="hint" style="margin:6px 0 0"><b>Selected features</b>: ${r.selection.kept_optical} of ${r.selection.of.optical} optical and ${r.selection.kept_sar} of ${r.selection.of.sar} SAR kept.
            ${r.selection.dropped.length ? `Dropped: ${esc(r.selection.dropped.slice(0, 12).join(", "))}${r.selection.dropped.length > 12 ? ` and ${r.selection.dropped.length - 12} more` : ""}.` : "None dropped."}</p>` : ""}
          ${(r.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
          ${r.areas ? `<div class="home-label">Area mapped (${esc(r.mapped_with)})</div><div class="dist">${Object.entries(r.areas).map(([k, v]) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span>${esc(k)}</span><b>${v.ha != null ? `${LF.fmt(v.ha, 1)} ha` : v.pixels}</b></div>`).join("")}</div>` : ""}
          <span class="hint">Class map and confidence added to Contents; the stack and a report (.json) are in ${esc(r.report.split("/").slice(0, -1).join("/"))}.</span>`);
      });
      return { open: fill, layersChanged: fill };
    },
  });

  // ---------------- fill cloud gaps from SAR
  LF.tool({ id: "sargapfill", menu: "sar", title: "Fill clouds from SAR", icon: "renhance", kinds: [],
    subtitle: "Fill the cloudy pixels of an optical image from Sentinel-1 (and a same-day coarse image such as MODIS, or a clear image of another date): learned on the image's own clear pixels, no training data needed",
    panel: `<div class="card"><h2>Images ${tip("Everything goes onto the cloudy image's grid. The SAR should be within a few days of it. A same-day coarse optical image (MODIS, Sentinel-3) adds the colour radar can't see.")}</h2>
        <label>Cloudy optical image <select id="gf-opt"></select></label>
        <label>Cloud mask ${tip("Where to fill: non-zero = cloud. Leave on 'the image's SCL band' for Sentinel-2 L2A.")} <select id="gf-mask"></select></label>
        <div class="home-label">SAR ${tip("Sentinel-1 of about the same date, any units (dB, linear or rescaled). Several dates are fine.")}</div><div id="gf-sars" class="sf-list"></div>
        <div class="home-label">Helper images (optional) ${tip("Coarse optical of the same day (MODIS), or a clear image of another date: the model learns how they relate to the cloudy image where it is clear.")}</div><div id="gf-helpers" class="sf-list"></div>
        <label>Clear image to score against (optional) ${tip("If you have the same scene without clouds (e.g. a test dataset), the result is scored where the clouds were: PSNR, SSIM, MAE, spectral angle and NDVI error.")} <select id="gf-truth"></select></label></div>
      <div class="card"><div class="grid2"><label>Model ${tip("LightGBM: accurate and fast (recommended). Random Forest: robust. Linear: a quick baseline.")} <select id="gf-model"><option value="lgbm">LightGBM</option><option value="rf">Random Forest</option><option value="linear">Linear</option></select></label>
        <label>Training pixels ${tip("How many clear pixels to learn from (sampled at random).")} <input type="number" id="gf-samples" value="60000" min="2000" step="10000"></label></div>
        <label class="check"><input type="checkbox" id="gf-res" checked> Correct with the clear surroundings ${tip("The model's error on the clear pixels around each cloud is carried into it, so filled areas join their surroundings without a visible edge.")}</label>
        <label>Name <input type="text" id="gf-name" value="filled" maxlength="80"></label>${runRow("gf", "Fill clouds")}</div>`,
    setup(LF) {
      const { $, $$, esc, api, layers, getLayer, fillLayers, runButton, trackJob, showResult } = LF;
      const fill = () => {
        const rasters = layers.filter((l) => l.type === "raster" && l.path);
        const opt = fillLayers($("#gf-opt"), rasters.filter((l) => !sarLayers(LF).includes(l)), { empty: "No optical image in Contents" });
        const sel = (el, first) => { const w = el.value; el.innerHTML = `<option value="">${first}</option>` + rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join(""); if (getLayer(w)) el.value = w; };
        sel($("#gf-mask"), "The image's SCL band"); sel($("#gf-truth"), "(none)");
        const box = (el, list, all) => { const was = new Set($$(`#${el} input:checked`).map((c) => c.value)); $(`#${el}`).innerHTML = list.length ? list.map((l) => `<label class="check"><input type="checkbox" value="${esc(l.id)}" ${was.has(l.id) || (all && !was.size) ? "checked" : ""}> ${esc(l.name)}</label>`).join("") : `<p class="hint" style="margin:0">None in Contents.</p>`; };
        box("gf-sars", sarLayers(LF), true);
        const taken = new Set([$("#gf-mask").value, $("#gf-truth").value].filter(Boolean));
        box("gf-helpers", rasters.filter((l) => l !== opt && !sarLayers(LF).includes(l) && !taken.has(l.id)), false);
      };
      ["#gf-opt", "#gf-mask", "#gf-truth"].forEach((q) => $(q).addEventListener("change", fill));
      runButton("gf", async () => {
        const o = getLayer($("#gf-opt").value);
        if (!o) throw new Error("Choose the cloudy image");
        const ids = (el) => $$(`#${el} input:checked`).map((c) => getLayer(c.value)?.path).filter(Boolean);
        const sars = ids("gf-sars"), helpers = ids("gf-helpers").filter((p) => p !== o.path);
        if (!sars.length && !helpers.length) throw new Error("Tick at least one SAR layer or helper image");
        const j = await api("/api/sar/gapfill", { method: "POST", json: { optical: o.path, mask: getLayer($("#gf-mask").value)?.path || null, sars, helpers,
          truth: getLayer($("#gf-truth").value)?.path || null, model: $("#gf-model").value, residual: $("#gf-res").checked, samples: +$("#gf-samples").value || 60000, name: $("#gf-name").value.trim() || "filled" } });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs.slice(0, 1));
        const sc = r.score;
        showResult("gf", `<b>Filled ${r.cloud_pct} %</b> of the image from ${r.inputs.sar} SAR and ${r.inputs.helpers} helper image${r.inputs.helpers === 1 ? "" : "s"} (${r.features} features, learned on ${r.trained_on.toLocaleString()} clear pixels).
          ${sc ? `<table class="kv" style="margin-top:6px"><tr><td>PSNR (whole image / where filled)</td><td>${sc.psnr} / ${sc.psnr_gap} dB</td></tr><tr><td>SSIM</td><td>${sc.ssim}</td></tr><tr><td>Mean absolute error (filled)</td><td>${sc.mae}</td></tr>
            <tr><td>Spectral angle (filled)</td><td>${sc.sam_deg}°</td></tr>${sc.ndvi_mae != null ? `<tr><td>NDVI error (filled)</td><td>${sc.ndvi_mae} (R² ${sc.ndvi_r2})</td></tr>` : ""}</table>` : ""}
          ${r.score_note ? `<p class="hint">${esc(r.score_note)}</p>` : ""}<span class="hint">The filled image is in Contents; a mask of the filled pixels is saved beside it.</span>`);
      });
      return { open: fill, layersChanged: fill };
    },
  });

  // ---------------- water & flood map (fused evidence, with confidence)
  LF.tool({ id: "sarwater", menu: "sar", title: "Flood & water map", icon: "sar", kinds: [],
    clip: { "wa-area": { what: "map covers" } },
    subtitle: "Open water and flooding with a confidence for every pixel: radar (dark calm water), the drop since a pre-flood image, optical water index where clear, terrain (low and flat) and permanent water (JRC), combined; no training needed",
    panel: `<div class="card"><h2>Evidence ${tip("Only the radar image is needed; every other source adds evidence where it exists. The radar is the grid of the result.")}</h2>
        <label>Radar during / after the flood ${tip("A processed SAR layer (γ⁰ or σ⁰, VV and VH). Calm water is very dark: its threshold is found in the image itself (Otsu) when the image has enough water, else −18 dB (VV) / −24 dB (VH).")} <select id="wa-post"></select></label>
        <label>Radar before the flood (optional) ${tip("Same orbit track, from a dry date. Water then that is still water is permanent; new water that also got ≥ 3 dB darker is flood.")} <select id="wa-pre"></select></label>
        <label>Optical image (optional) ${tip("Sentinel-2 or Landsat near the date: its water index (AWEI, else NDWI) marks water where it is clear, counted double against the radar; its SCL band masks the clouds.")} <select id="wa-opt"></select></label>
        <label>Or a water mask (optional) ${tip("A mask made with Analysis ▸ Tools ▸ Water mask (its water / land where clear) instead of the optical image.")} <select id="wa-mask"></select></label>
        <div class="grid2"><label>Terrain ${tip("Water lies low and flat: pixels far above the local low ground (≳ 10 m) or on slopes (≳ 8°) are unlikely water. This also removes radar shadow behind hills, which looks like water.")} <select id="wa-dem"><option value="auto">Copernicus DEM (downloaded)</option><option value="">None</option></select></label>
          <label>Permanent water ${tip("JRC Global Surface Water: where water was at least half of the time 1984–2020. Tells rivers and lakes from the flood when there is no pre-flood image.")} <select id="wa-perm"><option value="auto">JRC Global Surface Water</option><option value="">None</option></select></label></div>
        ${LF.html.area("wa-area", "Area", "Optional: the result is cut to it.")}</div>
      <div class="card"><h2>Thresholds (optional) ${tip("Leave empty to find them in the image. Lower them if wet fields or dark tarmac come out as water; raise them for windy water.")}</h2>
        <label>Radar threshold method ${tip("Checked Otsu (default): Otsu when the image has a clear water mode, else −18 / −24 dB. Or any automatic method: it looks only at the tiles that clearly hold land and water (two-Gaussian fit, the dark class below −18 / −24 dB); fixed: always −18 / −24 dB. Radar alone on Sen1Floods11's validation chips (IoU): Triangle 0.49, Li 0.49, checked Otsu 0.48, S-function 0.48, Isodata 0.47, fuzzy c-means 0.47, Kapur 0.45, Yen 0.44; local methods 0.19–0.26.")} <select id="wa-method"><option value="checked">Checked Otsu (recommended)</option><option value="fixed">Fixed (−18 / −24 dB)</option>
        <optgroup label="Global">${[["otsu", "Otsu"], ["multi_otsu", "Multi-Otsu"], ["li", "Li"], ["yen", "Yen"], ["kapur", "Kapur"], ["triangle", "Triangle"], ["isodata", "Isodata"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</optgroup>
        <optgroup label="Local (per pixel)">${[["niblack", "Niblack"], ["sauvola", "Sauvola"], ["wolf", "Wolf"], ["phansalkar", "Phansalkar"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</optgroup>
        <optgroup label="Fuzzy">${[["fcm", "Fuzzy c-means"], ["fuzzy", "Fuzzy threshold (Huang)"], ["membership", "Fuzzy membership (S-function)"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</optgroup></select></label>
        <div class="grid2"><label>VV water below (dB) <input type="number" id="wa-vv" placeholder="auto" step="0.5"></label><label>VH water below (dB) <input type="number" id="wa-vh" placeholder="auto" step="0.5"></label></div>
        <div class="grid3"><label>Above low ground (m) <input type="number" id="wa-hand" value="10" min="1"></label><label>Slope (°) <input type="number" id="wa-slope" value="8" min="1"></label><label>Darker by (dB) ${tip("For flood: how much darker than before.")} <input type="number" id="wa-drop" value="3" step="0.5"></label></div>
        <label>Name <input type="text" id="wa-name" value="water" maxlength="80"></label>${runRow("wa", "Map water")}</div>`,
    setup(LF) {
      const { $, esc, api, layers, getLayer, fillLayers, getClip, runButton, trackJob, showResult } = LF;
      const fill = () => {
        const rasters = layers.filter((l) => l.type === "raster" && l.path);
        const sar = sarLayers(LF);
        fillLayers($("#wa-post"), sar, { empty: "No SAR layer in Contents" });
        const sel = (el, list, first) => { const w = el.value; el.innerHTML = `<option value="">${first}</option>` + list.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join(""); if (getLayer(w)) el.value = w; };
        sel($("#wa-pre"), sar, "(none)");
        sel($("#wa-opt"), rasters.filter((l) => !sar.includes(l)), "(none)");
        sel($("#wa-mask"), rasters.filter((l) => !sar.includes(l)), "(none)");
        for (const [id, first] of [["#wa-dem", "Copernicus DEM (downloaded)"], ["#wa-perm", "JRC Global Surface Water"]]) {
          const el = $(id), w = el.value;
          el.innerHTML = `<option value="auto">${first}</option><option value="">None</option>` + rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
          el.value = [...el.options].some((o) => o.value === w) ? w : "auto";
        }
      };
      const pick = (v) => v === "auto" ? "auto" : v ? getLayer(v)?.path || null : null;
      runButton("wa", async () => {
        const post = getLayer($("#wa-post").value);
        if (!post) throw new Error("Choose the radar image");
        const num = (q) => $(q).value === "" ? null : +$(q).value;
        const j = await api("/api/sar/water", { method: "POST", json: { post: post.path, pre: getLayer($("#wa-pre").value)?.path || null, optical: getLayer($("#wa-opt").value)?.path || null,
          water_mask: getLayer($("#wa-mask").value)?.path || null, dem: pick($("#wa-dem").value), permanent: pick($("#wa-perm").value), aoi: getClip("wa-area"), vv_db: num("#wa-vv"), vh_db: num("#wa-vh"),
          radar_threshold: $("#wa-method").value, hand_m: +$("#wa-hand").value || 10, slope_max: +$("#wa-slope").value || 8, drop_db: +$("#wa-drop").value || 3, name: $("#wa-name").value.trim() || "water" } });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs);
        const areas = r.areas_ha || r.areas_px || {};
        showResult("wa", `<b>Water map</b> from ${esc(r.sources.join(", "))}.
          <div class="dist">${Object.entries(areas).map(([k, v]) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span>${esc(k)}</span><b>${r.areas_ha ? `${LF.fmt(v, 1)} ha` : v}</b></div>`).join("")}</div>
          <p class="hint" style="margin:4px 0 0">Radar thresholds: ${Object.entries(r.thresholds).map(([k, v]) => `${k} ${v} dB (${esc(r.threshold_source[k])})`).join(", ")}${r.dem ? ` · DEM: ${esc(r.dem)}` : ""}${r.permanent ? ` · ${esc(r.permanent)}` : ""}.
          ${r.mean_confidence_water != null ? `Mean confidence of the water: ${r.mean_confidence_water}.` : ""}</p><span class="hint">Classes and confidence (0–1) added to Contents.</span>`);
      });
      return { open: fill, layersChanged: fill };
    },
  });

  // ---------------- soil moisture by change detection
  LF.tool({ id: "sarsoil", menu: "sar", title: "Soil moisture (change detection)", icon: "timeseries", kinds: [],
    clip: { "sm-area": { what: "result covers" } },
    subtitle: "Relative surface soil moisture (0–100 %) for every date of a Sentinel-1 series: each pixel between the driest and wettest it was seen (TU Wien change detection), with water, towns and dense vegetation masked",
    panel: `<div class="card"><h2>Dates ${tip("SAR layers of ONE orbit track (same direction and relative orbit), one per date, processed alike (the SAR workflow). More dates give steadier dry / wet references: a year of data (30+) is best; at least 4.")}</h2><div id="sm-list" class="sf-list"></div>
        <div class="grid2"><label>Polarisation ${tip("VV (or HH) responds most to soil moisture; VH is used only to find dense vegetation.")} <select id="sm-pol"><option>VV</option><option>HH</option></select></label>
          <label>Dry / wet percentiles ${tip("The references: the pixel's 5th and 95th percentile over the series (rather than the min / max, which speckle and outliers would set).")} <input type="text" id="sm-pct" value="5, 95"></label></div>
        <div class="grid3"><label>Min. range (dB) ${tip("Pixels whose backscatter varies less than this over the series (towns, rock, permanently wet or dry ground) are masked: the radar can't follow the soil there.")} <input type="number" id="sm-range" value="3" step="0.5"></label>
          <label>Water below (dB) ${tip("Open water: masked.")} <input type="number" id="sm-water" value="-18" step="0.5"></label>
          <label>Canopy VH−VV above (dB) ${tip("Where VH is close to VV, the radar sees a canopy (forest, tall crops): the soil underneath is masked.")} <input type="number" id="sm-veg" value="-5" step="0.5"></label></div>
        ${LF.html.area("sm-area", "Area", "Optional: the result and the series are cut to it.")}
        <label>Name <input type="text" id="sm-name" value="soil_moisture" maxlength="80"></label>${runRow("sm", "Compute soil moisture")}</div>`,
    setup(LF) {
      const { $, $$, esc, api, getLayer, getClip, runButton, trackJob, showResult } = LF;
      const fill = () => {
        const was = new Set($$("#sm-list input:checked").map((c) => c.value));
        const ls = sarLayers(LF);
        $("#sm-list").innerHTML = ls.length ? ls.map((l) => `<label class="check"><input type="checkbox" value="${esc(l.id)}" ${was.has(l.id) || !was.size ? "checked" : ""}> ${esc(l.name)}</label>`).join("") : `<p class="hint">No SAR layers in Contents.</p>`;
      };
      runButton("sm", async () => {
        const ls = $$("#sm-list input:checked").map((c) => getLayer(c.value)).filter(Boolean);
        if (ls.length < 4) throw new Error("Tick at least 4 dates of one orbit track");
        const [lo, hi] = $("#sm-pct").value.split(/[,\s]+/).map(Number);
        const j = await api("/api/sar/soilmoisture", { method: "POST", json: { rasters: ls.map((l) => l.path), pol: $("#sm-pol").value, lo: lo || 5, hi: hi || 95,
          min_range_db: +$("#sm-range").value || 3, water_db: +$("#sm-water").value, veg_ratio_db: +$("#sm-veg").value, aoi: getClip("sm-area"), name: $("#sm-name").value.trim() || "soil_moisture" } });
        const r = (await trackJob(j, { title: j.title })).result;
        await addOutputs(LF, r.outputs.slice(0, 1).concat(r.outputs.slice(2)));
        const mx = 100;
        showResult("sm", `<b>${r.dates.length} dates</b> · median sensitivity ${r.median_sensitivity_db ?? "?"} dB between dry and wet.
          <div class="home-label">Mean soil moisture of the area (valid pixels)</div>
          <div class="dist">${r.series.map((p) => `<div style="grid-template-columns:90px minmax(0,1fr) auto"><span>${esc(p.date)}</span><span><span style="display:inline-block;height:8px;width:${Math.round(120 * (p.mean ?? 0) / mx)}px;background:var(--accent);border-radius:2px"></span></span><b>${p.mean ?? "–"} %</b></div>`).join("")}</div>
          <div class="home-label">Pixels</div><div class="dist">${Object.entries(r.quality_pct).map(([k, v]) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span>${esc(k)}</span><b>${v} %</b></div>`).join("")}</div>
          ${(r.notes || []).map((n) => `<div class="warn">${esc(n)}</div>`).join("")}<span class="hint">A band per date (0–100 %) and the quality layer are in Contents; the dry / wet references are saved beside them.</span>`);
      });
      return { open: fill, layersChanged: fill };
    },
  });
})();
