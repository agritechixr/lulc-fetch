  // Stack layers tool.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Stack layers
  const stSel = {};  // layer id -> { on, bands: Set }
  function refreshStack() {
    const rasters = layers.filter((l) => l.type === "raster");
    $("#st-layers").innerHTML = rasters.length ? rasters.map((l) => {
      const s = stSel[l.id] || (stSel[l.id] = { on: false, bands: new Set((l.info?.bands || []).map((b) => b.index)) });
      const formula = l.derived || l.render?.index || l.render?.formula;
      const bandsHtml = !formula && l.info ? `<div class="bands">${l.info.bands.map((b) => `<label title="${esc(b.description)}"><input type="checkbox" data-sb="${esc(l.id)}" value="${b.index}" ${s.bands.has(b.index) ? "checked" : ""}>${esc(b.description !== "Band " + b.index ? b.description : String(b.index))}</label>`).join("")}</div>` : "";
      return `<div class="st-layer ${s.on ? "on" : ""}"><label><input type="checkbox" data-sl="${esc(l.id)}" ${s.on ? "checked" : ""}>${esc(l.name)}
        <small>${formula ? `1 band (${esc(l.render.index || "formula")})` : `${s.bands.size} of ${l.info?.count || "?"} bands`}</small></label>${s.on ? bandsHtml : ""}</div>`;
    }).join("") : '<p class="hint">Add raster layers to Contents first.</p>';
    $$("[data-sl]").forEach((c) => c.onchange = () => { stSel[c.dataset.sl].on = c.checked; refreshStack(); });
    $$("[data-sb]").forEach((c) => c.onchange = () => { const s = stSel[c.dataset.sb]; c.checked ? s.bands.add(+c.value) : s.bands.delete(+c.value); refreshStack(); });
    const ref = $("#st-ref"), cur = ref.value;
    const chosen = rasters.filter((l) => stSel[l.id]?.on);
    ref.innerHTML = (chosen.length ? chosen : rasters).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}${l.info ? ` · ${fmt(l.info.res[0], l.info.res[0] < 1 ? 3 : 0)} ${/4326/.test(l.info.crs) ? "°" : "m"}` : ""}</option>`).join("");
    if ([...ref.options].some((o) => o.value === cur)) ref.value = cur;
    updateStackEst();
  }
  function updateStackEst() {
    const ref = getLayer($("#st-ref").value);
    if (ref?.info) {
      const r0 = ref.info.res[0], unit = /4326/.test(ref.info.crs) ? "°" : " m", cur = $("#st-factor").value;
      $("#st-factor").innerHTML = [1, 2, 4, 8].map((f) => `<option value="${f}">${f === 1 ? `Native (${fmt(r0, r0 < 1 ? 3 : 0)}${unit})` : `${f}× coarser (${fmt(r0 * f, r0 < 1 ? 3 : 0)}${unit})`}</option>`).join("");
      if (cur) $("#st-factor").value = cur;
    }
    const n = layers.filter((l) => l.type === "raster" && stSel[l.id]?.on).reduce((a, l) => a + ((l.derived || l.render?.index || l.render?.formula) ? 1 : stSel[l.id].bands.size), 0);
    $("#st-est").textContent = n ? `${n} band${n === 1 ? "" : "s"} in the stacked image${ref?.info ? ` · grid of ${ref.info.width.toLocaleString()} × ${ref.info.height.toLocaleString()} px before area / pixel-size options` : ""}` : "Tick at least one layer.";
  }
  ($("#st-factor").closest("label") || $("#st-factor")).insertAdjacentHTML("afterend",
    LF.html.resampling("st-method", { auto: "Default (bilinear; class maps nearest)", only: ["bilinear", "cubic", "cubic_spline", "lanczos", "average", "med", "nearest"] }));
  $("#st-ref").onchange = updateStackEst;
  $("#st-run").onclick = async () => {
    const err = $("#st-error"); err.classList.add("hidden");
    // oldest layer first (the bottom of Contents), so the base image's bands come first
    const chosen = layers.filter((l) => l.type === "raster" && stSel[l.id]?.on).reverse();
    if (!chosen.length) return toast("Tick at least one layer", true);
    const items = chosen.map((l) => (l.derived || l.render?.index || l.render?.formula)
      ? { path: l.path, name: l.name, index: l.render.index || null, formula: l.render.formula || null, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0 }
      : { path: l.path, name: l.name.replace(/\.(tiff?|vrt)$/i, ""), bands: [...stSel[l.id].bands].sort((a, b) => a - b) });
    if (items.some((it) => it.bands && !it.bands.length)) return toast("A ticked layer has no bands selected", true);
    const ref = getLayer($("#st-ref").value);
    const btn = $("#st-run"); btn.disabled = true;
    $("#st-result").classList.add("hidden");
    try {
      const job = await api("/api/stack", { method: "POST", json: { items, ref: ref.path, clip: getClip("st-area"), factor: +$("#st-factor").value || 1, resampling: $("#st-method").value || null, name: $("#st-name").value || "stack" } });
      const done = await trackJob(job, { tool: "stack", save: "stack", title: "Stacking layers" });
      const r = done.result;
      const out = await addRasterFromPath(r.path, { name: $("#st-name").value || "stack" });
      $("#st-result").innerHTML = `<div class="pca-sum" style="margin-top:10px"><b>✓ ${r.bands.length} bands stacked</b> · ${r.width.toLocaleString()} × ${r.height.toLocaleString()} px · ${esc(r.crs)} · ${r.seconds} s<br>${r.bands.map(esc).join(", ")}</div>
        <div class="row"><button class="btn small primary" data-st2t>Use in Raster → table →</button><button class="btn small" data-stz>Zoom</button></div>`;
      $("#st-result").classList.remove("hidden");
      $("[data-stz]").onclick = () => zoomTo(out);
      $("[data-st2t]").onclick = () => { switchTool("raster2table"); refreshRtInputs(); $("#rt-input").value = out.id; rt.layer = out; refreshRtInputs(); renderRt(); };
    } catch (e) { if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); } }
    finally { btn.disabled = false; }
  };

  async function initAnalyze() {
    an.catalog = state.catalog = await api("/api/indices");
    $("#an-cmap").innerHTML = `<option value="">Index default</option>` + Object.keys(an.catalog.colormaps).map((k) => `<option>${k}</option>`).join("");
    $("#an-scale-preset").innerHTML = Object.entries(an.catalog.scale_presets).map(([k, p]) => `<option value="${k}">${esc(p.title)}</option>`).join("") +
      `<option value="">Custom</option>`;
    $("#an-formula-examples").innerHTML = `<option value="">Choose a formula to edit…</option>` +
      an.catalog.indices.map((i) => `<option value="${i.name}">${i.name}: ${esc(i.formula)}</option>`).join("");
    renderIndexButtons();
  }
