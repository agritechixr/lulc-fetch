  // Index analysis tool (NDVI and 24 more, custom formulas) and its band setup.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ index analysis tool
  const an = { catalog: null, layer: null, info: null, bandMap: {}, cat: "All", sel: null, res: null,
               reqId: 0, resultId: null, userSet: new Set(), pending: null };
  const hasBands = (bands) => bands.every((b) => b in an.bandMap);

  function refreshAnalyzeInputs() {
    const sel = $("#an-input");
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    sel.innerHTML = `<option value="">${rasters.length ? "Choose a raster layer…" : "No raster layers yet"}</option>` +
      rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
    if (an.layer && rasters.includes(an.layer)) sel.value = an.layer.id;
  }
  $("#an-input").onchange = () => { const l = getLayer($("#an-input").value); if (l) openInput(l); else resetAnalyze(); };

  function resetAnalyze() {
    Object.assign(an, { layer: null, info: null, bandMap: {}, sel: null, pending: null, resultId: null });
    $("#an-input").value = "";
    $("#an-info").classList.add("hidden");
    $("#an-result").classList.add("hidden");
    $("#an-setup").classList.add("hidden");
    renderIndexButtons();
  }

  function openInput(l) {
    const info = l.info;
    Object.assign(an, { layer: l, info, bandMap: l.band_map, userSet: new Set(l.userSet || []), sel: null, pending: null, resultId: null });
    $("#an-input").value = l.id;
    $("#an-info").classList.remove("hidden");
    $("#an-name").textContent = l.name;
    $("#an-meta").textContent = `${info.width.toLocaleString()} × ${info.height.toLocaleString()} px · ${fmt(info.res[0], info.res[0] < 1 ? 2 : 1)} ${/^EPSG:4326$/.test(info.crs) ? "°" : "m"} · ${info.crs} · ${info.count} band${info.count > 1 ? "s" : ""} · ${/\.vrt$/i.test(l.path) ? "virtual, reads the original product" : fmt(info.size_mb) + " MB"}`;
    $("#an-scale").value = l.scale ?? info.scale;
    $("#an-offset").value = l.offset ?? info.offset;
    const unchanged = (l.scale ?? info.scale) === info.scale && (l.offset ?? info.offset) === info.offset;
    const match = unchanged ? [info.scale_preset] : Object.entries(an.catalog.scale_presets).find(([, p]) => Math.abs(p.scale - $("#an-scale").value) < 1e-12 && Math.abs(p.offset - $("#an-offset").value) < 1e-12);
    $("#an-scale-preset").value = match ? match[0] : "";
    $("#an-scalereason").textContent = `Detected: ${info.scale_reason}.`;
    renderBandTable();
    updateScaleSummary();
    renderSetup();
    renderIndexButtons();
    $("#an-result").classList.add("hidden");
    selectLayer(l.id);
  }
  function analyzeLayer(l) { switchTool("analyze"); openInput(l); }

  // Band / scale fixes are stored on the input layer itself, so they persist and apply to its display.
  function syncInputLayer() {
    const l = an.layer;
    if (!l) return;
    l.userSet = [...an.userSet];
    l.scale = parseFloat($("#an-scale").value) || 1;
    l.offset = parseFloat($("#an-offset").value) || 0;
    saveLayers();
    if (l.render?.composite) renderRaster(l).catch(() => {});
  }

  function renderBandTable() {
    const info = an.info, names = an.catalog.band_names;
    const byIndex = Object.fromEntries(Object.entries(an.bandMap).map(([k, v]) => [v, k]));
    $("#an-bandtable").innerHTML = info.bands.map((b) => `<tr><td>${b.index} · ${esc(b.description)}</td><td>
      <select data-band="${b.index}"><option value="">— not used —</option>${names.map((n) =>
        `<option ${byIndex[b.index] === n ? "selected" : ""}>${n}</option>`).join("")}</select></td></tr>`).join("");
    $$("#an-bandtable select").forEach((s) => s.onchange = () => {
      const idx = +s.dataset.band;
      Object.keys(an.bandMap).forEach((k) => (an.bandMap[k] === idx || k === s.value) && delete an.bandMap[k]);
      if (s.value) { an.bandMap[s.value] = idx; an.userSet.add(s.value); }
      renderBandTable();
      renderIndexButtons();
      renderSetup();
      syncInputLayer();
      if (an.sel) compute();
    });
    const mapped = Object.keys(an.bandMap).sort();
    $("#an-bandsum").textContent = mapped.length ? mapped.join(" ") : "none mapped. Open to set them.";
    $("#an-bandsrc").textContent = `Detected from ${info.band_map_source}. Change a dropdown if a band is wrong.`;
    $("#an-bands-details").open = !mapped.length;
  }

  function updateScaleSummary() {
    const p = $("#an-scale-preset").selectedOptions[0];
    $("#an-scalesum").textContent = p ? p.textContent : `× ${$("#an-scale").value} + ${$("#an-offset").value}`;
  }

  function renderIndexButtons() {
    const c = an.catalog;
    $("#an-composites").innerHTML = Object.entries(c.composites).map(([k, v]) =>
      `<button class="chip ${an.sel?.composite === k ? "active" : ""}" data-comp="${k}" ${an.info ? "" : "disabled"} style="${an.info && !hasBands(v.bands) ? "border-style:dashed;opacity:.55" : ""}" title="${esc(v.bands.join(", "))}">${esc(v.title)}</button>`).join("");
    $$("#an-composites [data-comp]").forEach((b) => b.onclick = () => select({ composite: b.dataset.comp }));
    $("#an-cats").innerHTML = ["All", ...c.categories].map((k) => `<button class="chip ${an.cat === k ? "active" : ""}" data-cat="${esc(k)}">${esc(k)}</button>`).join("");
    $$("#an-cats [data-cat]").forEach((b) => b.onclick = () => { an.cat = b.dataset.cat; renderIndexButtons(); });
    const list = c.indices.filter((i) => an.cat === "All" || i.category === an.cat);
    $("#an-index-grid").innerHTML = list.map((i) => {
      const ok = an.info && hasBands(i.bands);
      const missing = i.bands.filter((b) => !(b in an.bandMap));
      return `<button class="idx ${an.sel?.index === i.name ? "active" : ""} ${an.info && !ok ? "needs" : ""}" data-idx="${i.name}" ${an.info ? "" : "disabled"}
        title="${esc(i.title)}\n${esc(i.formula)}${ok ? "" : `\nNeeds ${missing.join(", ")}. Click to assign.`}"><b>${esc(i.name)}</b><small>${ok || !an.info ? esc(i.title) : "needs " + missing.join(", ")}</small></button>`;
    }).join("");
    $$("#an-index-grid [data-idx]").forEach((b) => b.onclick = () => select({ index: b.dataset.idx }));
    $("#an-band-chips").innerHTML = Object.keys(an.bandMap).sort().map((b) => `<button class="chip" data-ins="${b}">${b}</button>`).join("");
    $$("#an-band-chips [data-ins]").forEach((b) => b.onclick = () => {
      const f = $("#an-formula"), pos = f.selectionStart ?? f.value.length;
      f.value = f.value.slice(0, pos) + b.dataset.ins + f.value.slice(f.selectionEnd ?? pos);
      f.focus();
      f.selectionStart = f.selectionEnd = pos + b.dataset.ins.length;
    });
  }

  // Choosing an index opens its setup (band assignment + hints) and computes it when every band is assigned.
  function select(sel) {
    $("#an-cmap").value = "";
    an.pending = sel;
    renderSetup();
    const spec = selSpec(sel);
    if (spec.bands.every((b) => b in an.bandMap)) compute(sel);
    else $("#an-setup").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function analyzeBody() {
    return { path: an.layer.path, band_map: an.bandMap, scale: parseFloat($("#an-scale").value) || 1, offset: parseFloat($("#an-offset").value) || 0 };
  }

  // Composites change how the input layer is displayed; indices / formulas create (or update) a result layer.
  // `sel` only becomes the current selection once it renders, so a bad formula never sticks.
  async function compute(sel = an.sel) {
    if (!an.layer || !sel) return;
    const id = ++an.reqId;
    $("#an-busy").classList.remove("hidden");
    try {
      if (sel.composite) {
        an.layer.render = { composite: sel.composite };
        await renderRaster(an.layer);
        if (id !== an.reqId) return;
        an.sel = sel;
        renderIndexButtons();
        if (an.pending === sel) renderSetup();
        $("#an-result").classList.add("hidden");
        status(`${an.layer.name}: showing ${state.catalog.composites[sel.composite].title}`);
        return;
      }
      const key = sel.index || "f:" + sel.formula;
      let out = layers.find((l) => l.derived && l.sourceId === an.layer.id && l.key === key);
      const isNew = !out;
      if (isNew) {
        out = addLayer({ type: "raster", derived: true, sourceId: an.layer.id, key, path: an.layer.path, info: an.layer.info,
                         name: `${sel.index || "Formula"} · ${an.layer.name}`, render: {} });
      }
      const body = analyzeBody();
      Object.assign(out, { band_map: { ...body.band_map }, scale: body.scale, offset: body.offset });
      const clip = getClip("an-area");
      out.render = { index: sel.index || null, formula: sel.formula || null, stretch: $("#an-stretch").value, cmap: $("#an-cmap").value || null, clip };
      out.name = `${sel.index || "Formula"} · ${an.layer.name}${clip ? " · area" : ""}`;
      if (out.render.stretch === "custom") { out.render.vmin = parseFloat($("#an-vmin").value); out.render.vmax = parseFloat($("#an-vmax").value); }
      $("#an-result").classList.remove("hidden");
      let res;
      try {
        res = await trackFetch((signal) => renderRaster(out, { signal }),
          { tool: "analyze", title: `${sel.index || "Formula"} · ${an.layer.name}`, message: clip ? "Computing for the selected area" : "Computing for the whole image" });
      } catch (e) {
        if (isNew) { removeLayer(out.id, { silent: true }); renderContents(); saveLayers(); }
        throw e;
      }
      if (id !== an.reqId) return;
      const clipChanged = JSON.stringify(out.lastClip || null) !== JSON.stringify(clip || null);
      out.lastClip = clip;
      an.sel = sel;
      an.resultId = out.id;
      selectLayer(out.id);
      if (clip && (isNew || clipChanged)) zoomTo(out);
      if (sel.formula) an.lastFormula = sel.formula;
      renderIndexButtons();
      if (an.pending === sel) renderSetup();
      showResult(res);
      status(`${out.name} computed. It's in Contents.`);
    } catch (e) {
      if (id === an.reqId && notCancelled(e)) toast(e, true);
    } finally {
      if (id === an.reqId) $("#an-busy").classList.add("hidden");
    }
  }

  function showResult(res) {
    const spec = an.catalog.indices.find((i) => i.name === an.sel.index);
    const isComp = !!an.sel.composite;
    $("#an-title").textContent = isComp ? res.title : spec ? `${spec.name} · ${spec.title}` : "Custom formula";
    $("#an-desc").textContent = isComp ? `Red = ${res.bands[0]}, green = ${res.bands[1]}, blue = ${res.bands[2]} (2–98% stretch).` : spec?.description || "";
    $("#an-formula-show").textContent = isComp ? "" : res.formula;
    $("#an-formula-show").classList.toggle("hidden", isComp);
    $("#an-legend-wrap").classList.toggle("hidden", isComp);
    $("#an-export-one").classList.toggle("hidden", isComp);
    if (isComp) return;
    $("#an-cmap").value = res.cmap;
    $("#an-legend").style.background = `linear-gradient(to right, ${res.colors.join(", ")})`;
    $("#an-lmin").textContent = fmtv(res.vmin);
    $("#an-lmid").textContent = fmtv((res.vmin + res.vmax) / 2);
    $("#an-lmax").textContent = fmtv(res.vmax);
    if ($("#an-stretch").value !== "custom") { $("#an-vmin").value = +res.vmin.toFixed(4); $("#an-vmax").value = +res.vmax.toFixed(4); }
    const h = res.stats.histogram, max = Math.max(...h.counts, 1), n = h.counts.length, bw = 300 / n;
    const colorAt = (t) => { // same interpolation as the server colormap
      const c = res.colors, pos = t * (c.length - 1), lo = Math.min(Math.floor(pos), c.length - 2), f = pos - lo;
      const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      const a = rgb(c[lo]), b = rgb(c[lo + 1]);
      return `rgb(${a.map((v, i) => Math.round(v * (1 - f) + b[i] * f)).join(",")})`;
    };
    $("#an-hist").innerHTML = h.counts.map((cnt, i) => {
      const hh = (cnt / max) * 58;
      return `<rect x="${i * bw + 0.5}" y="${60 - hh}" width="${bw - 1}" height="${hh}" fill="${colorAt((i + 0.5) / n)}"><title>${fmtv(h.edges[i])} – ${fmtv(h.edges[i + 1])}: ${cnt.toLocaleString()} px</title></rect>`;
    }).join("");
    const s = res.stats;
    $("#an-stats").innerHTML = [["Mean", s.mean], ["Median", s.median], ["Std dev", s.std], ["Min", s.min], ["Max", s.max],
      ["Pixels", null, s.count.toLocaleString() + (res.decimated ? "*" : "")]].map(([k, v, txt]) =>
      `<div>${k}<b>${txt ?? fmtv(v)}</b></div>`).join("");
    $("#an-stats").title = res.decimated ? "* Stats are computed on the preview resolution. The GeoTIFF export is full resolution." : "";
  }

  // display options
  $("#an-cmap").onchange = () => compute();
  $("#an-stretch").onchange = () => { $("#an-custom-range").classList.toggle("hidden", $("#an-stretch").value !== "custom"); compute(); };
  let rangeTimer;
  ["#an-vmin", "#an-vmax"].forEach((s) => $(s).oninput = () => { clearTimeout(rangeTimer); rangeTimer = setTimeout(() => compute(), 500); });
  $("#an-scale-preset").onchange = () => {
    const p = an.catalog.scale_presets[$("#an-scale-preset").value];
    if (p) { $("#an-scale").value = p.scale; $("#an-offset").value = p.offset; }
    updateScaleSummary();
    renderSetup();
    syncInputLayer();
    compute();
  };
  ["#an-scale", "#an-offset"].forEach((s) => $(s).onchange = () => {
    const sc = parseFloat($("#an-scale").value), off = parseFloat($("#an-offset").value);
    const match = Object.entries(an.catalog.scale_presets).find(([, p]) => Math.abs(p.scale - sc) < 1e-12 && Math.abs(p.offset - off) < 1e-12);
    $("#an-scale-preset").value = match ? match[0] : "";
    updateScaleSummary();
    renderSetup();
    syncInputLayer();
    compute();
  });
  $("#an-formula-go").onclick = async () => {
    const f = $("#an-formula").value.trim();
    if (!f) return toast("Type a formula first", true);
    const r = await checkFormula();
    if (!r) return toast("Fix the formula first (see the message under it)", true);
    select({ formula: f, bands: r.bands });
  };
  $("#an-formula").addEventListener("keydown", (e) => e.key === "Enter" && $("#an-formula-go").click());

  // export: the result layer through the layer export dialog; several indices as one multi-band GeoTIFF
  $("#an-export-one").onclick = () => { const l = getLayer(an.resultId); if (l) openExport(l); };
  async function doExport(indices, formulas, btn) {
    const job = await api("/api/analyze/export", { method: "POST", json: { ...analyzeBody(), indices, formulas, clip: getClip("an-area") } });
    $("#dlg-export").close();
    try {
      const done = await trackJob(job, { tool: "analyze", save: "analyze" });
      const r = done.result;
      download(r.url, r.name);
      toast(`Saved ${r.name} (${r.layers.length} band${r.layers.length > 1 ? "s" : ""})`);
    } catch (e) { if (notCancelled(e)) toast(e, true); }
  }
  $("#an-export-many").onclick = () => {
    if (!an.info) return toast("Choose an image first", true);
    $("#ex-list").innerHTML = an.catalog.indices.map((i) => {
      const ok = hasBands(i.bands);
      return `<label title="${esc(i.title)}"><input type="checkbox" value="${i.name}" ${ok ? "" : "disabled"} ${ok && an.sel?.index === i.name ? "checked" : ""}>${esc(i.name)}</label>`;
    }).join("");
    const f = an.lastFormula;  // last formula that computed successfully
    $("#ex-custom-wrap").classList.toggle("hidden", !f);
    $("#ex-custom-f").textContent = f || "";
    $("#ex-custom").checked = !!an.sel?.formula;
    $("#ex-error").classList.add("hidden");
    $("#dlg-export").showModal();
  };
  $("#ex-all").onclick = () => $$("#ex-list input:not(:disabled)").forEach((i) => i.checked = true);
  $("#ex-none").onclick = () => $$("#ex-list input").forEach((i) => i.checked = false);
  $("#ex-go").onclick = async (e) => {
    const indices = $$("#ex-list input:checked").map((i) => i.value);
    const f = an.lastFormula;
    const formulas = $("#ex-custom").checked && f ? [{ name: "custom", formula: f }] : [];
    if (!indices.length && !formulas.length) { $("#ex-error").textContent = "Select at least one index"; $("#ex-error").classList.remove("hidden"); return; }
    try { await doExport(indices, formulas, e.currentTarget); $("#dlg-export").close(); }
    catch (err) { $("#ex-error").textContent = err.message; $("#ex-error").classList.remove("hidden"); }
  };

  // ------------------------------------------------------------------ index setup (band assignment + hints)
  const BAND_RE = /[A-Za-z_][A-Za-z0-9_]*/g;
  const FORMULA_ALIASES = { COASTAL: "B01", BLUE: "B02", GREEN: "B03", RED: "B04", RE1: "B05", RE2: "B06", RE3: "B07",
    REDEDGE: "B05", NIR: "B08", NIR2: "B8A", NARROWNIR: "B8A", WV: "B09", SWIR1: "B11", SWIR2: "B12" };
  function normBand(tok) { // mirror of lulc_fetch.indices.normalize_band
    const up = tok.toUpperCase();
    if (FORMULA_ALIASES[up]) return FORMULA_ALIASES[up];
    const m = up.match(/^B0?(\d{1,2})(A?)$/);
    if (!m) return null;
    const cand = m[1] === "8" && m[2] ? "B8A" : `B${String(+m[1]).padStart(2, "0")}`;
    return an.catalog.band_names.includes(cand) ? cand : null;
  }
  const bandLabel = (b) => an.catalog.band_info[b]?.short || b;
  const refl = (bandIdx) => { // file band median converted to reflectance
    const st = an.info.bands.find((x) => x.index === bandIdx);
    if (!st || st.median == null) return null;
    return st.median * (parseFloat($("#an-scale").value) || 1) + (parseFloat($("#an-offset").value) || 0);
  };
  const prettyFormula = (f) => f.replace(/\*\*/g, "^").replace(/\s*\*\s*/g, " × ").replace(/\s\/\s/g, " ÷ ");

  function selSpec(sel) {
    if (!sel) return null;
    if (sel.index) {
      const s = an.catalog.indices.find((i) => i.name === sel.index);
      return { title: `${s.name} · ${s.title}`, bands: s.bands, formula: s.formula, category: s.category, desc: s.description };
    }
    if (sel.composite) {
      const c = an.catalog.composites[sel.composite];
      return { title: c.title, bands: c.bands, formula: `R = ${c.bands[0]}, G = ${c.bands[1]}, B = ${c.bands[2]}`, composite: true };
    }
    return { title: "Custom formula", bands: sel.bands || [], formula: sel.formula };
  }

  function bandWarnings(bands) {
    const out = [], m = an.bandMap, info = an.info;
    const unconfirmed = bands.filter((b) => !an.userSet?.has(b));
    if (/^guessed/.test(info.band_map_source) && unconfirmed.length) {
      out.push(["warn", `This file has no band names, so the band order was <b>guessed from the band count</b> (${info.count} bands). Check ${unconfirmed.join(", ")} below.`]);
    } else if (/^unknown/.test(info.band_map_source)) {
      out.push(["err", "The bands in this file couldn't be identified. Assign each band below."]);
    }
    const used = {};
    bands.filter((b) => b in m).forEach((b) => (used[m[b]] ||= []).push(b));
    Object.entries(used).filter(([, bs]) => bs.length > 1).forEach(([idx, bs]) =>
      out.push(["err", `File band ${idx} is assigned to <b>${bs.join(" and ")}</b> at the same time. Each should usually be a different band.`]));
    const r = (b) => (b in m ? refl(m[b]) : null);
    const nir = r("B08") ?? r("B8A"), red = r("B04");
    if (bands.some((b) => ["B08", "B8A", "B04"].includes(b)) && nir != null && red != null && nir < red * 0.8) {
      out.push(["warn", `<b>NIR looks darker than Red</b> (median ${fmtv(nir)} vs ${fmtv(red)}). Land with any vegetation is normally brighter in NIR, so NIR and Red may be <b>swapped or assigned to the wrong bands</b>. (This can be normal for scenes that are mostly water or dense city.)`]);
    }
    const s1 = r("B11"), s2 = r("B12");
    if (bands.some((b) => ["B11", "B12"].includes(b)) && s1 != null && s2 != null && s2 > s1 * 1.3) {
      out.push(["warn", `SWIR2 (B12) is brighter than SWIR1 (B11) (${fmtv(s2)} vs ${fmtv(s1)}). That's unusual, so they may be swapped.`]);
    }
    const vals = bands.filter((b) => b in m).map((b) => [b, r(b)]).filter(([, v]) => v != null);
    const high = vals.filter(([, v]) => v > 1.5), low = vals.filter(([, v]) => v > 0 && v < 0.002);
    if (high.length) out.push(["err", `Values don't look like reflectance (median ${high.map(([b, v]) => `${b} = ${fmtv(v)}`).join(", ")}). Change <b>Pixel values</b> in step 1, e.g. to “Sentinel-2 DN (÷10000)” or “8-bit”.`]);
    if (low.length) out.push(["warn", `Values are very small (${low.map(([b, v]) => `${b} = ${fmtv(v)}`).join(", ")}). Check the <b>Pixel values</b> scale in step 1.`]);
    return out;
  }

  function renderSetup() {
    const sel = an.pending, spec = selSpec(sel), card = $("#an-setup");
    if (!spec || !an.info) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    const info = an.catalog.band_info, m = an.bandMap;
    $("#su-title").textContent = spec.title;
    const words = (f) => f.replace(BAND_RE, (t) => { const b = normBand(t); return b ? bandLabel(b) : t; });
    const onFile = (f) => f.replace(BAND_RE, (t) => {
      const b = normBand(t);
      if (!b) return t;
      return b in m ? `Band ${m[b]}` : `<span class="chk-err">[${b}?]</span>`;
    });
    $("#su-f-bands").textContent = prettyFormula(spec.formula);
    $("#su-f-words").textContent = prettyFormula(words(spec.formula));
    $("#su-f-file").innerHTML = prettyFormula(onFile(esc(spec.formula)));

    const options = (cur) => `<option value="">— not assigned —</option>` + an.info.bands.map((b) => {
      const v = refl(b.index);
      const named = b.description !== `Band ${b.index}` ? ` · ${esc(b.description)}` : "";
      return `<option value="${b.index}" ${cur === b.index ? "selected" : ""}>Band ${b.index}${named}${v != null ? ` · median ${fmtv(v)}` : ""}</option>`;
    }).join("");
    $("#su-rows").innerHTML = spec.bands.map((b) => {
      const bi = info[b] || {}, cur = m[b], file = an.info.bands.find((x) => x.index === cur);
      const named = file && normBand(file.description.replace(/\s/g, "")) === b;
      const hint = cur == null
        ? `Not assigned. Which band of your file is <b>${esc(bi.name)}</b> (~${bi.nm} nm)? Elsewhere it is ${esc(bi.equiv)}.`
        : named ? `✓ Band ${cur} is named “${esc(file.description)}”, which matches.`
        : `Band ${cur}${file.description !== `Band ${cur}` ? ` (“${esc(file.description)}”)` : ""} is assumed to be ${esc(bi.short)}. Change it if your file's order is different. Elsewhere this band is ${esc(bi.equiv)}.`;
      return `<div class="su-row ${cur == null ? "missing" : "ok"}">
        <div class="su-band"><b>${b} · ${esc(bi.short || "")}</b><small>${esc(bi.name || "")}<br>${bi.nm ? bi.nm + " nm" : ""}</small></div>
        <select data-role="${b}">${options(cur)}</select>
        <div class="su-hint">${hint}</div></div>`;
    }).join("");
    $$("#su-rows select").forEach((s) => s.onchange = () => {
      if (s.value) an.bandMap[s.dataset.role] = +s.value; else delete an.bandMap[s.dataset.role];
      an.userSet.add(s.dataset.role);
      syncInputLayer();
      renderBandTable();
      renderIndexButtons();
      renderSetup();
      if (spec.bands.every((b) => b in an.bandMap)) compute(an.pending);
    });

    const missing = spec.bands.filter((b) => !(b in m));
    const warns = bandWarnings(spec.bands);
    if (missing.length) warns.unshift(["err", `Assign <b>${missing.join(", ")}</b> to compute this. If your image doesn't have ${missing.length > 1 ? "these bands" : "this band"}, pick one of the suggestions below.`]);
    $("#su-warn").innerHTML = warns.map(([k, t]) => `<div class="warn ${k === "err" ? "err" : ""}">${t}</div>`).join("");
    const go = $("#su-go");
    go.disabled = missing.length > 0;
    go.textContent = missing.length ? `Assign ${missing.join(", ")} first` : (an.sel && JSON.stringify(an.sel) === JSON.stringify(sel) ? "Recompute" : "Compute");

    // suggestions: indices that work with the bands this image has
    const avail = an.catalog.indices.filter((i) => hasBands(i.bands) && i.name !== sel.index);
    let sugg = [];
    if (spec.category) sugg = avail.filter((i) => i.category === spec.category);
    if (missing.length && !sugg.length && spec.category === "Vegetation") sugg = avail.filter((i) => i.category === "RGB only");
    if (!spec.category && !spec.composite) sugg = avail.slice(0, 6);
    $("#su-suggest").innerHTML = sugg.length
      ? `${missing.length ? "Works with your image instead:" : "Related indices for this image:"} ` +
        sugg.slice(0, 8).map((i) => `<button class="chip sugg" data-sugg="${i.name}" title="${esc(i.title)}: ${esc(i.formula)}">${i.name}</button>`).join("")
      : "";
    $$("#su-suggest [data-sugg]").forEach((b) => b.onclick = () => select({ index: b.dataset.sugg }));
  }
  $("#su-go").onclick = () => compute(an.pending);
  $("#su-close").onclick = () => $("#an-setup").classList.add("hidden");

  // custom formula: live check + examples
  let fcTimer;
  async function checkFormula() {
    const f = $("#an-formula").value.trim(), out = $("#an-formula-check");
    if (!f) { out.innerHTML = ""; return null; }
    const r = await api(`/api/formula/check?formula=${encodeURIComponent(f)}`);
    if (!r.ok) { out.innerHTML = `<span class="chk-err">✗ ${esc(r.error)}</span>`; return null; }
    out.innerHTML = "Uses " + r.bands.map((b) => b in an.bandMap
      ? `<span class="chk-ok">${b} (${esc(bandLabel(b))}) → Band ${an.bandMap[b]} ✓</span>`
      : `<span class="chk-err">${b} (${esc(bandLabel(b))}) → not assigned ✗</span>`).join(", ");
    return r;
  }
  $("#an-formula").addEventListener("input", () => { clearTimeout(fcTimer); fcTimer = setTimeout(checkFormula, 350); });
  $("#an-formula-examples").onchange = (e) => {
    const s = an.catalog.indices.find((i) => i.name === e.target.value);
    if (!s) return;
    $("#an-formula").value = s.formula;
    e.target.value = "";
    checkFormula();
    $("#an-formula").focus();
  };
