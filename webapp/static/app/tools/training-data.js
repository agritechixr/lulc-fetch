  // Make training data: image(s) + ground truth → patches for deep learning.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Make training data: image(s) + ground truth → patches for deep learning
  const pt = { on: {}, refId: null, sizeFor: null, previewLayer: null, planTimer: 0 };
  const ptRasters = () => layers.filter((l) => l.type === "raster" && !l.derived && l.path);
  const ptRef = () => getLayer($("#pt-ref").value);
  const ptUnit = (l) => /4326/.test(l?.info?.crs || "") ? "°" : "m";
  function refreshPt() {
    const rasters = ptRasters();
    if (!Object.values(pt.on).some(Boolean) && rasters.length) pt.on[rasters[rasters.length - 1].id] = true;   // start with the oldest image
    $("#pt-layers").innerHTML = rasters.length ? rasters.slice().reverse().map((l) => `<div class="st-layer ${pt.on[l.id] ? "on" : ""}"><label><input type="checkbox" data-ptl="${esc(l.id)}" ${pt.on[l.id] ? "checked" : ""}>${esc(l.name)}
        <small>${l.info?.count ?? "?"} bands · ${l.info ? `${fmt(l.info.res[0], l.info.res[0] < 1 ? 3 : 1)} ${ptUnit(l)}` : ""}</small></label></div>`).join("")
      : '<p class="hint">Add a raster layer (GeoTIFF) to Contents first with + Add data.</p>';
    $$("[data-ptl]").forEach((c) => c.onchange = () => { pt.on[c.dataset.ptl] = c.checked; refreshPt(); ptChanged(); });
    const chosen = rasters.filter((l) => pt.on[l.id]).reverse();
    const ref = $("#pt-ref"), cur = ref.value;
    ref.innerHTML = chosen.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}${l.info ? ` · ${fmt(l.info.res[0], l.info.res[0] < 1 ? 3 : 1)} ${ptUnit(l)}` : ""}</option>`).join("") || `<option value="">Tick an input layer</option>`;
    if (chosen.some((l) => l.id === cur)) ref.value = cur;
    // ground truth: any raster (class map) not used as input, or a vector layer
    const gt = $("#pt-gt"), gcur = gt.value;
    const vecs = layers.filter((x) => x.type === "vector" && x.id !== pt.previewLayer?.id && !x.ptFootprints);
    const others = rasters.filter((x) => !pt.on[x.id]);
    gt.innerHTML = `<option value="">No ground truth (images only)</option>` +
      (others.length ? `<optgroup label="Class raster (GeoTIFF, e.g. a land-cover map)">${others.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</optgroup>` : "") +
      (vecs.length ? `<optgroup label="Polygons / points (shapefile, GeoJSON, training samples)">${vecs.map((x) => `<option value="${esc(x.id)}">${esc(x.name)} (${x.geojson.features.length} features)</option>`).join("")}</optgroup>` : "");
    if ([...gt.options].some((o) => o.value === gcur)) gt.value = gcur;
    renderPtGt();
    ptRefChanged();
  }
  function ptRefChanged() {
    const l = ptRef();
    if (!l?.info) { $("#pt-info").textContent = ""; $("#pt-px").textContent = ""; $("#pt-px-chips").innerHTML = ""; return; }
    const i = l.info, r = i.res[0], u = ptUnit(l);
    $("#pt-info").textContent = `${i.width.toLocaleString()} × ${i.height.toLocaleString()} px · pixel ${fmt(r, r < 1 ? 3 : 1)} ${u} · ${i.crs}`;
    if (pt.sizeFor !== l.id) {   // default: 256 × 256 pixels, smaller (with half overlap) when the image is small
      pt.sizeFor = l.id;
      const side = Math.min(i.width, i.height);
      const px = [256, 128, 64, 32].find((n) => side >= 4 * n) || 32;
      const half = px < 256 && side < 8 * px;   // a small image: overlapping patches give more training examples
      $("#pt-sx").value = +(px * i.res[0]).toPrecision(6); $("#pt-sy").value = +(px * i.res[1]).toPrecision(6);
      $("#pt-ox").value = half ? +(px / 2 * i.res[0]).toPrecision(6) : 0; $("#pt-oy").value = half ? +(px / 2 * i.res[1]).toPrecision(6) : 0;
      if (px < 256) toast(`The image is small (${i.width} × ${i.height} px): patches of ${px} × ${px} px${half ? " with half overlap" : ""} were chosen to get enough of them`);
    }
    $("#pt-px-chips").innerHTML = `<small class="hint" style="margin-right:4px">Pixels:</small>` + [32, 64, 128, 224, 256, 512].map((n) => `<button class="chip" data-ptpx="${n}">${n} × ${n}</button>`).join("");
    $$("[data-ptpx]").forEach((b) => b.onclick = () => { $("#pt-sx").value = +(b.dataset.ptpx * i.res[0]).toPrecision(6); $("#pt-sy").value = +(b.dataset.ptpx * i.res[1]).toPrecision(6); ptChanged(); });
    ptChanged();
  }
  $("#pt-ref").onchange = ptRefChanged;
  function ptSizes() {
    return { patch_m: [+$("#pt-sx").value, +$("#pt-sy").value], overlap_m: [+$("#pt-ox").value || 0, +$("#pt-oy").value || 0] };
  }
  function ptChanged() {
    const l = ptRef();
    if (!l?.info) return;
    const { patch_m, overlap_m } = ptSizes(), [rx, ry] = l.info.res;
    const pw = Math.round(patch_m[0] / rx), ph = Math.round(patch_m[1] / ry), ox = Math.round(overlap_m[0] / rx), oy = Math.round(overlap_m[1] / ry);
    const bad = !(pw >= 4 && ph >= 4) ? "Patches must be at least 4 pixels wide." : (ox >= pw || oy >= ph) ? "The overlap must be smaller than the patch." : "";
    $("#pt-px").innerHTML = bad ? `<span style="color:var(--warn)">${bad}</span>`
      : `Each patch = <b>${pw} × ${ph} px</b>${(ox || oy) ? ` · overlap ${ox} × ${oy} px (step ${pw - ox} × ${ph - oy} px)` : " · no overlap"}` +
        ((Math.abs(pw * rx - patch_m[0]) > rx * 0.01 || Math.abs(ph * ry - patch_m[1]) > ry * 0.01) ? ` · rounded to whole pixels (${fmt(pw * rx, 1)} × ${fmt(ph * ry, 1)} ${ptUnit(l)})` : "");
    clearTimeout(pt.planTimer);
    if (!bad) pt.planTimer = setTimeout(() => ptPlan(false), 400);
  }
  ["#pt-sx", "#pt-sy", "#pt-ox", "#pt-oy"].forEach((id) => $(id).addEventListener("input", ptChanged));
  $("#pt-edge").onchange = ptChanged;
  $("#pt-ov0").onclick = () => { $("#pt-ox").value = 0; $("#pt-oy").value = 0; ptChanged(); };
  $("#pt-ovhalf").onclick = () => { const { patch_m } = ptSizes(); $("#pt-ox").value = +(patch_m[0] / 2).toPrecision(6); $("#pt-oy").value = +(patch_m[1] / 2).toPrecision(6); ptChanged(); };
  async function ptPlan(show) {
    const l = ptRef();
    if (!l) return show && toast("Tick an input layer first", true);
    try {
      const r = await api("/api/patches/plan", { method: "POST", json: { path: l.path, clip: getClip("pt-area"), edge: $("#pt-edge").value, ...ptSizes() } });
      $("#pt-plan").innerHTML = `<b>${r.count.toLocaleString()}</b> patch positions (${r.rows} rows × ${r.cols} columns) before skipping empty ones.` +
        (r.count < 10 ? ` <span style="color:var(--warn)">Too few to train a model well (aim for 20 or more): choose smaller patches or half overlap.</span>` : "");
      if (show) {
        if (pt.previewLayer && getLayer(pt.previewLayer.id)) removeLayer(pt.previewLayer.id);
        pt.previewLayer = addVectorLayer(r.preview, "Patch grid preview", { color: "#f59e0b", weight: 1, fillOpacity: 0.03, zoom: true });
        if (r.preview_every > 1) toast(`Showing every ${r.preview_every}th patch of ${r.count.toLocaleString()}`);
      }
    } catch (e) { $("#pt-plan").innerHTML = `<span style="color:var(--warn)">${esc(e.message)}</span>`; }
  }
  $("#pt-preview").onclick = () => ptPlan(true);

  function renderPtGt() {
    const l = getLayer($("#pt-gt").value);
    $("#pt-gt-raster").classList.toggle("hidden", l?.type !== "raster");
    $("#pt-gt-vector").classList.toggle("hidden", l?.type !== "vector");
    $("#pt-gt-opts").classList.toggle("hidden", !l);
    $("#pt-gt-classes").textContent = l ? "" : "Only image patches will be made (no labels folder).";
    if (l?.type === "raster") {
      $("#pt-gt-band").innerHTML = l.info.bands.map((b) => `<option value="${b.index}">Band ${b.index}${b.description !== `Band ${b.index}` ? " · " + esc(b.description) : ""}</option>`).join("");
      const lg = l.legend?.kind === "classes" ? l.legend.classes : null;
      $("#pt-gt-classes").textContent = lg ? `${lg.length} classes: ${lg.slice(0, 8).map((c) => c.name).join(", ")}${lg.length > 8 ? " …" : ""}` : "Each distinct pixel value is a class (0 / no-data = no label).";
    } else if (l?.type === "vector") {
      const keys = [...new Set(l.geojson.features.flatMap((f) => Object.keys(f.properties || {})))].filter((k) => !k.startsWith("_"));
      const pref = keys.find((k) => /^(class|label|lulc|lc|landcover|type|category|code)$/i.test(k)) || keys[0];
      $("#pt-gt-field").innerHTML = keys.length ? keys.map((k) => `<option ${k === pref ? "selected" : ""}>${esc(k)}</option>`).join("") : `<option value="">(no attributes: every shape = class 1)</option>`;
      ptGtClasses();
      if (!$("#pt-req-labels").dataset.touched) { $("#pt-req-labels").checked = true; $("#pt-minlab-wrap").classList.remove("hidden"); }
    }
  }
  function ptGtClasses() {
    const l = getLayer($("#pt-gt").value), f = $("#pt-gt-field").value;
    if (l?.type !== "vector") return;
    const cnt = new Map();
    l.geojson.features.forEach((ft) => { const v = f ? ft.properties?.[f] : 1; if (v != null && v !== "") cnt.set(String(v), (cnt.get(String(v)) || 0) + 1); });
    const e = [...cnt.entries()].sort((a, b) => b[1] - a[1]);
    $("#pt-gt-classes").innerHTML = !e.length ? `<span style="color:var(--warn)">No values in this attribute.</span>`
      : `${e.length} classes: ${e.slice(0, 10).map(([k, n]) => `${esc(k)} <small>(${n})</small>`).join(", ")}${e.length > 10 ? " …" : ""}`;
  }
  $("#pt-gt").onchange = renderPtGt;
  $("#pt-gt-field").onchange = ptGtClasses;
  $("#pt-req-labels").onchange = () => { $("#pt-req-labels").dataset.touched = "1"; $("#pt-minlab-wrap").classList.toggle("hidden", !$("#pt-req-labels").checked); };
  function ptDest() {
    const f = $("#pt-folder").value.trim(), n = safeName($("#pt-name").value || "training_patches").replace(/\./g, "_");
    const base = f || ((proj.info?.workspace || "") + "/training_data");
    $("#pt-dest").innerHTML = `Creates <code>${esc(base.replace(/\/$/, ""))}/${esc(n)}/</code> with <b>images/</b>, <b>labels/</b>, <b>classes.txt</b>, dataset.json and patches.csv.`;
  }
  $("#pt-folder").addEventListener("input", () => { prefs.set("pt-folder", $("#pt-folder").value); ptDest(); });
  $("#pt-name").addEventListener("input", ptDest);
  $("#pt-browse").onclick = async () => {
    const f = await pickFolder({ title: "Save the training data in…", start: $("#pt-folder").value || prefs.get("save-dir:last", ""), okLabel: "Use this folder" });
    if (f) { $("#pt-folder").value = f; prefs.set("pt-folder", f); ptDest(); }
  };
  $("#pt-folder").value = prefs.get("pt-folder", "");
  ptDest();

  $("#pt-run").onclick = async () => {
    const err = $("#pt-error"); err.classList.add("hidden");
    const ref = ptRef();
    if (!ref) return toast("Tick at least one input layer", true);
    const others = ptRasters().filter((l) => pt.on[l.id] && l.id !== ref.id).reverse();
    const inputs = [ref, ...others].map((l) => ({ path: l.path, name: l.name.replace(/\.(tiff?|vrt|jp2)$/i, "") }));
    const gl = getLayer($("#pt-gt").value);
    const ground_truth = !gl ? null : gl.type === "raster" ? { type: "raster", path: gl.path, band: +$("#pt-gt-band").value || 1 }
      : { type: "vector", geojson: gl.geojson, field: $("#pt-gt-field").value || null };
    const { patch_m, overlap_m } = ptSizes();
    if (!(patch_m[0] > 0 && patch_m[1] > 0)) return toast("Enter the patch size X and Y in metres", true);
    const body = {
      path: ref.path, inputs, ground_truth, clip: getClip("pt-area"), patch_m, overlap_m, edge: $("#pt-edge").value,
      min_valid: (+$("#pt-minvalid").value || 0) / 100, require_labels: !!gl && $("#pt-req-labels").checked, min_labelled: (+$("#pt-minlab").value || 0) / 100,
      remap: $("#pt-remap").checked,
      name: $("#pt-name").value.trim() || "training_patches", folder: $("#pt-folder").value.trim() || null,
      class_colors: gl?.classColors || null,
    };
    const btn = $("#pt-run"); btn.disabled = true; $("#pt-result").classList.add("hidden");
    try {
      const job = await api("/api/patches/make", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "patches", title: `Make training data · ${body.name}` });
      showPtResult(done.result);
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  function showPtResult(r) {
    const box = $("#pt-result"), cls = r.classes || [], total = cls.reduce((a, c) => a + c.pixels, 0);
    const max = Math.max(1, ...cls.map((c) => c.pixels));
    box.innerHTML = `<div class="card rm-head-card">
        <h2 style="margin:0">✓ Training data ready</h2>
        <div class="pca-sum" style="margin-top:4px"><b>${r.count.toLocaleString()}</b> patches · ${r.patch_size_px[0]} × ${r.patch_size_px[1]} px (${fmt(r.patch_size_m[0], 1)} × ${fmt(r.patch_size_m[1], 1)} m) · ${r.band_count} bands (${esc(r.dtype)}) · ${r.size_mb} MB · ${r.seconds} s</div>
        ${r.skipped.too_little_data || r.skipped.too_few_labels ? `<div class="pca-sum">Skipped ${r.skipped.too_little_data} with too little data${r.labels_dir ? `, ${r.skipped.too_few_labels} with too few labels` : ""}</div>` : ""}
        <p class="hint" style="word-break:break-all">${esc(r.folder)}</p>
        ${cls.length ? `<div class="home-label" style="margin-top:10px">Classes (classes.txt) · share of labelled pixels · 0 = no label</div>
        <div class="dist">${cls.map((c) => `<div style="grid-template-columns:auto minmax(0,2.2fr) minmax(0,1.6fr) auto"><span><i class="swatch" style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(c.color || "#999")}"></i> <b>${esc(String(c.value))}</b></span><span title="${esc(c.name)} · original value ${esc(String(c.original))} · in ${c.patches} patches">${esc(c.name)}${String(c.original) !== String(c.value) ? ` <small>(${esc(String(c.original))})</small>` : ""}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * c.pixels / max)}%;background:${esc(c.color || "")}"></span></span><b>${c.pixels && c.pixels < total / 1000 ? "<0.1" : fmt(100 * c.pixels / Math.max(total, 1), 1)}%</b></div>`).join("")}</div>` : `<p class="hint">Images only (no ground truth).</p>`}
        <div class="row tight" style="margin-top:10px;flex-wrap:wrap"><button class="btn" data-pt-reveal>Show in folder</button><button class="btn" data-pt-map>Show patches on the map</button></div>
        <p class="hint">dataset.json describes everything a deep-learning training tool needs. No train / validation split is made: the training tool decides that.</p>
      </div>`;
    box.classList.remove("hidden");
    $("[data-pt-reveal]", box).onclick = () => api("/api/project/reveal", { method: "POST", json: { path: r.folder } }).catch((e) => toast(e.message, true));
    $("[data-pt-map]", box).onclick = () => {
      if (pt.previewLayer && getLayer(pt.previewLayer.id)) removeLayer(pt.previewLayer.id);
      const l = addVectorLayer(r.footprints, `${r.name} patches`, { color: "#2563eb", weight: 1, fillOpacity: 0.08, ptFootprints: true, zoom: true });
      return l;
    };
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
