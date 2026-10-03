  // Raster → table tool.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ---------------- Raster → table
  const rt = { layer: null };
  function refreshRtInputs() {
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    const sel = $("#rt-input");
    sel.innerHTML = `<option value="">${rasters.length ? "Choose a raster layer…" : "No raster layers yet"}</option>` +
      rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
    if (rt.layer && rasters.includes(rt.layer)) sel.value = rt.layer.id; else { rt.layer = null; renderRt(); }
    // ground truth: any other raster, or any vector layer
    const gtSel = $("#rt-gt"), cur = gtSel.value;
    const vecs = layers.filter((l) => l.type === "vector");
    gtSel.innerHTML = `<option value="">None. Just the image values</option>` +
      (rasters.length ? `<optgroup label="Raster (e.g. land-cover map)">${rasters.filter((l) => l !== rt.layer).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("")}</optgroup>` : "") +
      (vecs.length ? `<optgroup label="Vector (shapefile / GeoJSON / KML)">${vecs.map((l) => `<option value="${esc(l.id)}">${esc(l.name)} (${l.geojson.features.length} features)</option>`).join("")}</optgroup>` : "");
    gtSel.value = [...gtSel.options].some((o) => o.value === cur) ? cur : "";
    renderGt();
  }
  function renderRt() {
    const l = rt.layer;
    if (!l) { $("#rt-bands").innerHTML = ""; $("#rt-info").textContent = ""; updateRtEstimate(); return; }
    const info = l.info;
    $("#rt-info").textContent = `${info.count} bands · ${info.width.toLocaleString()} × ${info.height.toLocaleString()} px · pixel ${fmt(info.res[0], info.res[0] < 1 ? 3 : 1)} ${/4326/.test(info.crs) ? "°" : "m"} · ${info.crs}`;
    $("#rt-bands").innerHTML = info.bands.map((b) => `<label title="Band ${b.index}: ${esc(b.description)}"><input type="checkbox" value="${b.index}" checked>${esc(b.description !== `Band ${b.index}` ? b.description : String(b.index))}</label>`).join("");
    $$("#rt-bands input").forEach((i) => i.onchange = updateRtEstimate);
    const identity = (l.scale ?? 1) === 1 && (l.offset ?? 0) === 0;
    $("#rt-refl").checked = !identity;
    $("#rt-refl").disabled = identity;
    const r0 = info.res[0], unit = /4326/.test(info.crs) ? "°" : " m";
    $("#rt-factor").innerHTML = [1, 2, 4, 8, 16].map((f) => `<option value="${f}">${f === 1 ? `Native (${fmt(r0, r0 < 1 ? 3 : 0)}${unit}): one row per pixel` : `${f}× coarser (${fmt(r0 * f, r0 < 1 ? 3 : 0)}${unit})`}</option>`).join("");
    $("#rt-name").value = safeName(l.name.replace(/\.(tiff?|vrt)$/i, "")).slice(0, 50);
    updateRtEstimate();
  }
  function renderGt() {
    const l = getLayer($("#rt-gt").value);
    $("#rt-gt-raster").classList.toggle("hidden", l?.type !== "raster");
    $("#rt-gt-vector").classList.toggle("hidden", l?.type !== "vector");
    $("#rt-gt-opts").classList.toggle("hidden", !l);
    $("#rt-strat-opt").classList.toggle("disabled", !l);
    if (!l && $('input[name="rts"]:checked').value === "stratified") $('input[name="rts"][value="all"]').checked = true;
    if (l?.type === "raster") {
      $("#rt-gt-band").innerHTML = l.info.bands.map((b) => `<option value="${b.index}">Band ${b.index}${b.description !== `Band ${b.index}` ? " · " + esc(b.description) : ""}</option>`).join("");
    } else if (l?.type === "vector") {
      const keys = [...new Set(l.geojson.features.flatMap((f) => Object.keys(f.properties || {})))];
      const pref = keys.find((k) => /^(class|label|lulc|lc|landcover|type|category|code|id)$/i.test(k)) || keys[0];
      $("#rt-gt-field").innerHTML = keys.length ? keys.map((k) => `<option ${k === pref ? "selected" : ""}>${esc(k)}</option>`).join("")
        : `<option value="">(no attributes: every feature = 1)</option>`;
      showGtClasses();
    }
    updateRtEstimate();
  }
  function showGtClasses() {
    const l = getLayer($("#rt-gt").value), field = $("#rt-gt-field").value;
    if (l?.type !== "vector") return;
    const vals = l.geojson.features.map((f) => (f.properties || {})[field]).filter((v) => v != null);
    const uniq = [...new Set(vals.map(String))];
    $("#rt-gt-classes").textContent = !field ? "" : `${uniq.length} distinct value${uniq.length === 1 ? "" : "s"}: ${uniq.slice(0, 8).join(", ")}${uniq.length > 8 ? " …" : ""}` +
      (vals.every((v) => typeof v === "number") ? " (numeric)" : " (text: a numeric code column is added too)");
  }
  function updateRtEstimate() {
    const l = rt.layer, est = $("#rt-est");
    if (!l) { est.textContent = "Choose an image to see how big the table will be."; return; }
    const f = +$("#rt-factor").value || 1, nb = $$("#rt-bands input:checked").length;
    let px = l.info.width * l.info.height;
    const clip = getClip("rt-area");
    if (clip && !/4326/.test(l.info.crs)) px = Math.min(px, geomArea(clip) / (l.info.res[0] * l.info.res[1]));
    px /= f * f;
    const samp = $('input[name="rts"]:checked').value;
    const gt = !!getLayer($("#rt-gt").value);
    let rows = samp === "random" ? Math.min(px, +$("#rt-n").value || 0) : px;
    const cols = nb + ($("#rt-xy").checked ? 2 : 0) + ($("#rt-ll").checked ? 2 : 0) + ($("#rt-rc").checked ? 2 : 0) + (gt ? 1 : 0);
    const bytes = rows * cols * ($("#rt-fmt").value === "csv" ? 9 : 3.5);
    const note = samp === "stratified" ? `up to ${(+$("#rt-pc").value || 0).toLocaleString()} rows per class` :
      gt && $("#rt-labelled").checked && samp === "all" ? `only labelled pixels (at most ${Math.round(rows).toLocaleString()} rows)` :
      `≈ ${Math.round(rows).toLocaleString()} rows`;
    est.innerHTML = `${note} × ${cols} columns${samp !== "stratified" ? ` · up to ~${bytes > 1e9 ? fmt(bytes / 1e9, 1) + " GB" : Math.max(1, Math.round(bytes / 1e6)) + " MB"}` : ""}` +
      (rows > 5e6 && samp === "all" && !(gt && $("#rt-labelled").checked) ? ` <span style="color:var(--warn)">· large: consider an area, a coarser pixel size, or a random sample</span>` : "");
  }
  $("#rt-input").onchange = () => { rt.layer = getLayer($("#rt-input").value); refreshRtInputs(); renderRt(); if (rt.layer) selectLayer(rt.layer.id); };
  $("#rt-bands-all").onclick = () => { $$("#rt-bands input").forEach((i) => i.checked = true); updateRtEstimate(); };
  $("#rt-bands-none").onclick = () => { $$("#rt-bands input").forEach((i) => i.checked = false); updateRtEstimate(); };
  $("#rt-gt").onchange = renderGt;
  $("#rt-gt-field").onchange = showGtClasses;
  ["#rt-factor", "#rt-fmt", "#rt-n", "#rt-pc", "#rt-xy", "#rt-ll", "#rt-rc", "#rt-labelled"].forEach((s) => $(s).addEventListener("input", updateRtEstimate));
  $$('input[name="rts"]').forEach((r) => r.onchange = updateRtEstimate);

  $("#rt-run").onclick = async () => {
    const l = rt.layer, err = $("#rt-error");
    err.classList.add("hidden");
    if (!l) return toast("Choose an image first (it is required)", true);
    const bands = $$("#rt-bands input:checked").map((i) => +i.value);
    if (!bands.length) return toast("Select at least one band", true);
    const gl = getLayer($("#rt-gt").value);
    let ground_truth = null;
    if (gl?.type === "raster") ground_truth = { type: "raster", path: gl.path, band: +$("#rt-gt-band").value || 1 };
    else if (gl?.type === "vector") ground_truth = { type: "vector", geojson: gl.geojson, field: $("#rt-gt-field").value || null };
    const refl = $("#rt-refl").checked;
    const body = {
      path: l.path, bands, clip: getClip("rt-area"), factor: +$("#rt-factor").value || 1,
      scale: refl ? (l.scale ?? 1) : 1, offset: refl ? (l.offset ?? 0) : 0,
      ground_truth, label_name: $("#rt-label").value || "label", labelled_only: $("#rt-labelled").checked,
      sampling: $('input[name="rts"]:checked').value, sample_size: +$("#rt-n").value || 100000, per_class: +$("#rt-pc").value || 5000,
      xy: $("#rt-xy").checked, lonlat: $("#rt-ll").checked, rowcol: $("#rt-rc").checked, drop_nodata: $("#rt-drop").checked,
      format: $("#rt-fmt").value, name: $("#rt-name").value || "table",
      class_colors: gl?.samples ? gl.classColors : null,
    };
    const btn = $("#rt-run");
    btn.disabled = true;
    $("#rt-result").classList.add("hidden");
    try {
      const job = await api("/api/tables/from-raster", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "raster2table", save: "raster2table", title: `Raster → table · ${body.name}` });
      showRtResult(done.result);
      refreshTables();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  function showRtResult(r) {
    const box = $("#rt-result");
    const counts = Object.entries(r.class_counts || {});
    const max = Math.max(1, ...counts.map(([, n]) => n));
    box.innerHTML = `<div class="pca-sum"><b>${r.rows.toLocaleString()} rows × ${r.columns.length} columns</b> · ${esc(r.name)} · ${fmt(r.size_mb, r.size_mb < 1 ? 2 : 1)} MB · ${r.seconds} s<br>
        Pixel ${fmt(r.pixel_size[0], r.pixel_size[0] < 1 ? 3 : 1)} · ${esc(r.crs)}${r.factor > 1 ? ` · aggregated ${r.factor}×${r.factor}` : ""} · sampling: ${esc(r.sampling)}${r.target ? ` · label column: <b>${esc(r.target)}</b>` : ""}</div>
      ${counts.length ? `<div class="home-label" style="margin-top:12px">Rows per class</div><div class="dist">${counts.slice(0, 20).map(([k, n]) =>
        `<div style="grid-template-columns:1fr 3fr auto"><span>${esc(k)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * n / max)}%"></span></span><b>${n.toLocaleString()}</b></div>`).join("")}</div>` : ""}
      <div class="home-label" style="margin-top:12px">First rows</div>
      <div class="load-wrap">${tableHtml(r.columns, r.preview, r.label_columns || [])}</div>
      <div class="row"><a class="btn small primary" href="/api/tables/file?path=${encodeURIComponent(r.path)}" download>⬇ Download ${esc(r.format.toUpperCase())}</a>
        <button class="btn small" data-tp>Open in data viewer</button></div>
      <p class="hint">Saved as <code>${esc(r.path)}</code>. It's also listed under <a href="#" data-goml>Classical ML ▸ Your tables</a>.</p>`;
    box.classList.remove("hidden");
    $("[data-tp]", box).onclick = () => previewTable(r.path);
    addItem({ kind: "table", name: r.path.split(/[\\/]/).pop(), path: r.path, rows: r.rows, cols: r.columns.length });
    $("[data-goml]", box).onclick = (e) => { e.preventDefault(); switchTool("ml"); openMlSub(null); };
  }
