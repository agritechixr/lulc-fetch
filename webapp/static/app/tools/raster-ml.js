  // Classical ML for raster: image + ground truth → model + map.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Classical ML for raster: image + ground truth → model + map
  const rm = { schema: null, layer: null, kind: null, model: "rf", ready: false };
  async function initRasterMl() {
    if (!mlx.schema) mlx.schema = await api("/api/ml/schema");
    if (!rm.schema) rm.schema = await api("/api/rasterml/schema");
    if (!rm.ready) { rm.ready = true; renderRmModels(); renderRmSettings(true); }
  }
  async function refreshRm() {
    try { await initRasterMl(); } catch (e) { return toast(e, true); }
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    const sel = $("#rm-input"), cur = rm.layer?.id || sel.value;
    sel.innerHTML = `<option value="">${rasters.length ? "Choose a raster layer…" : "No raster layers yet: add one with Insert ▸ Add data"}</option>` +
      rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)} · ${l.info?.count ?? "?"} bands</option>`).join("");
    if (cur && rasters.some((l) => l.id === cur)) sel.value = cur;
    else if (!rm.layer && rasters.length === 1) sel.value = rasters[0].id;
    const l = getLayer(sel.value);
    if (l !== rm.layer) { rm.layer = l || null; renderRmImage(); }
    // ground truth: any other raster (class map) or vector layer
    const gt = $("#rm-gt"), gcur = gt.value;
    const vecs = layers.filter((x) => x.type === "vector");
    gt.innerHTML = `<option value="">Choose the ground truth…</option>` +
      (vecs.length ? `<optgroup label="Polygons / points (shapefile, GeoJSON, training samples)">${vecs.map((x) => `<option value="${esc(x.id)}">${esc(x.name)} (${x.geojson.features.length} features)</option>`).join("")}</optgroup>` : "") +
      (rasters.filter((x) => x !== rm.layer).length ? `<optgroup label="Class raster (e.g. a land-cover map)">${rasters.filter((x) => x !== rm.layer).map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</optgroup>` : "");
    const auto = vecs.find((x) => x.samples) || (vecs.filter((x) => x.id !== "aoi").length === 1 ? vecs.find((x) => x.id !== "aoi") : null);
    gt.value = [...gt.options].some((o) => o.value === gcur) && gcur ? gcur : (auto ? auto.id : "");
    renderRmGt();
  }
  $("#rm-input").onchange = () => { rm.layer = getLayer($("#rm-input").value); renderRmImage(); refreshRm(); if (rm.layer) selectLayer(rm.layer.id); };

  function renderRmImage() {
    const l = rm.layer;
    $("#rm-kind").classList.add("hidden");
    if (!l) { $("#rm-info").textContent = ""; $("#rm-bands").innerHTML = ""; $("#rm-bands-sum").textContent = "Bands"; return; }
    const info = l.info;
    $("#rm-info").textContent = `${info.count} bands · ${info.width.toLocaleString()} × ${info.height.toLocaleString()} px · pixel ${fmt(info.res[0], info.res[0] < 1 ? 3 : 1)}${/4326/.test(info.crs) ? "°" : " m"} · ${info.crs}`;
    $("#rm-bands").innerHTML = info.bands.map((b) => `<label title="Band ${b.index}: ${esc(b.description)}"><input type="checkbox" value="${b.index}" checked>${esc(b.description !== `Band ${b.index}` ? b.description : String(b.index))}</label>`).join("");
    $$("#rm-bands input").forEach((i) => i.onchange = rmBandsChanged);
    const identity = (l.scale ?? 1) === 1 && (l.offset ?? 0) === 0;
    $("#rm-refl").checked = !identity; $("#rm-refl").disabled = identity;
    const r0 = info.res[0], unit = /4326/.test(info.crs) ? "°" : " m";
    $("#rm-factor").innerHTML = [1, 2, 4, 8].map((f) => `<option value="${f}">${f === 1 ? `Native (${fmt(r0, r0 < 1 ? 3 : 0)}${unit})` : `${f}× coarser (${fmt(r0 * f, r0 < 1 ? 3 : 0)}${unit})`}</option>`).join("");
    $("#rm-name").value = safeName(l.name.replace(/\.(tiff?|vrt)$/i, "")).slice(0, 40) + "_classified";
    $("#rm-bands-wrap").open = info.count <= 16;
    rmBandsChanged();
  }
  const rmBands = () => $$("#rm-bands input:checked").map((i) => +i.value);
  let rmKindTimer = 0;
  function rmBandsChanged() {
    const n = rmBands().length, total = $$("#rm-bands input").length;
    $("#rm-bands-sum").innerHTML = `Bands: <b>${n}</b> of ${total} used`;
    clearTimeout(rmKindTimer);
    rmKindTimer = setTimeout(detectRmKind, 250);
  }
  $("#rm-bands-all").onclick = () => { $$("#rm-bands input").forEach((i) => i.checked = true); rmBandsChanged(); };
  $("#rm-bands-none").onclick = () => { $$("#rm-bands input").forEach((i) => i.checked = false); rmBandsChanged(); };
  $("#rm-range").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const keep = new Set();
    for (const part of $("#rm-range").value.split(",")) {
      const m = part.trim().match(/^(\d+)\s*(?:-|–|to)\s*(\d+)$/) || part.trim().match(/^(\d+)$/);
      if (!m) continue;
      const a = +m[1], b = +(m[2] ?? m[1]);
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) keep.add(i);
    }
    if (!keep.size) return toast("Type band numbers or ranges, e.g. 1-100, 120-180", true);
    $$("#rm-bands input").forEach((i) => i.checked = keep.has(+i.value));
    rmBandsChanged();
  });
  async function detectRmKind() {
    const l = rm.layer, bands = rmBands();
    if (!l || !bands.length) { $("#rm-kind").classList.add("hidden"); return; }
    try { rm.kind = await api(`/api/rasterml/inspect?path=${encodeURIComponent(l.path)}&bands=${bands.length === l.info.count ? "" : bands.join(",")}`); }
    catch (e) { rm.kind = null; return; }
    const k = rm.kind, titles = rm.kind.models.map((m) => mlx.schema.models[m]?.title || m);
    $("#rm-kind").innerHTML = `<div class="rm-kind-head"><span class="rm-kind-tag k-${k.kind}">${esc(k.title)}</span><small>${esc(k.reason)}</small></div>
      <div class="hint" style="margin-top:4px">${esc(k.note)}</div>
      <div class="hint" style="margin-top:4px">Suggested: <b>${titles.map(esc).join(", ")}</b>${k.scaling === "none" ? " · no feature scaling" : ""}${k.reduce === "auto" ? " · PCA band reduction for Maximum Likelihood / LDA" : ""}</div>
      <button class="chip" id="rm-apply" style="margin-top:6px">Use recommended settings</button>`;
    $("#rm-kind").classList.remove("hidden");
    $("#rm-apply").onclick = () => applyRmRecommended(true);
    renderRmModels();
    if (!rm.touched) applyRmRecommended(false);
  }
  function applyRmRecommended(pickModel) {
    const k = rm.kind;
    if (!k) return;
    if (pickModel && k.models[0]) { rm.model = k.models[0]; renderRmModels(); }
    renderRmSettings(true);
    if (pickModel) toast(`Settings for ${k.title.toLowerCase()} applied`);
  }

  function renderRmGt() {
    const l = getLayer($("#rm-gt").value);
    $("#rm-gt-raster").classList.toggle("hidden", l?.type !== "raster");
    $("#rm-gt-vector").classList.toggle("hidden", l?.type !== "vector");
    $("#rm-gt-classes").textContent = "";
    if (l?.type === "raster") {
      $("#rm-gt-band").innerHTML = l.info.bands.map((b) => `<option value="${b.index}">Band ${b.index}${b.description !== `Band ${b.index}` ? " · " + esc(b.description) : ""}</option>`).join("");
      const lg = l.legend?.kind === "classes" ? l.legend.classes : null;
      $("#rm-gt-classes").textContent = lg ? `${lg.length} classes: ${lg.slice(0, 8).map((c) => c.name).join(", ")}${lg.length > 8 ? " …" : ""}` : "Each distinct pixel value is a class.";
    } else if (l?.type === "vector") {
      const keys = [...new Set(l.geojson.features.flatMap((f) => Object.keys(f.properties || {})))].filter((k) => !k.startsWith("_"));
      const pref = keys.find((k) => /^(class|label|lulc|lc|landcover|type|category|code)$/i.test(k)) || keys[0];
      $("#rm-gt-field").innerHTML = keys.length ? keys.map((k) => `<option ${k === pref ? "selected" : ""}>${esc(k)}</option>`).join("") : `<option value="">(no attributes)</option>`;
      rmGtClasses();
    }
  }
  function rmGtClasses() {
    const l = getLayer($("#rm-gt").value), f = $("#rm-gt-field").value;
    if (l?.type !== "vector") return;
    const cnt = new Map();
    l.geojson.features.forEach((ft) => { const v = ft.properties?.[f]; if (v != null && v !== "") cnt.set(String(v), (cnt.get(String(v)) || 0) + 1); });
    const entries = [...cnt.entries()].sort((a, b) => b[1] - a[1]);
    const few = entries.filter(([, n]) => n < 2).map(([k]) => k);
    $("#rm-gt-classes").innerHTML = !entries.length ? `<span style="color:var(--warn)">No values in this attribute.</span>` :
      `${entries.length} classes: ${entries.slice(0, 10).map(([k, n]) => `${esc(k)} <small>(${n})</small>`).join(", ")}${entries.length > 10 ? " …" : ""}` +
      (entries.length < 2 ? `<br><span style="color:var(--warn)">At least two classes are needed.</span>` : "") +
      (few.length ? `<br><span style="color:var(--warn)">Only one polygon / point for: ${few.slice(0, 5).map(esc).join(", ")}. Draw more for an honest accuracy.</span>` : "");
  }
  $("#rm-gt").onchange = renderRmGt;
  $("#rm-gt-field").onchange = rmGtClasses;

  function renderRmModels() {
    const sc = mlx.schema, kind = rm.kind?.kind, rec = rm.kind?.models || [];
    const keys = rm.schema.models.filter((k) => sc.models[k] && !sc.unavailable.includes(k));
    keys.sort((a, b) => (rec.includes(b) ? 1 : 0) - (rec.includes(a) ? 1 : 0));   // suggested models first
    modelPicker($("#rm-models"), { value: rm.model, onChange: (k) => { rm.model = k; rm.touched = true; renderRmModels(); renderRmSettings(false); },
      items: keys.map((k) => {
        const m = sc.models[k], good = (rm.schema.good_for[k] || []).map((g) => rm.schema.kinds[g]?.title.replace(/ image$/, "").replace("Pixel ", "")).join(" · ");
        return { id: k, title: m.title, group: rec.length ? (rec.includes(k) ? `Suggested for this ${(rm.kind.title || "image").toLowerCase()}` : "Other models") : m.family,
                 badge: rec.includes(k) ? "suggested" : "", tip: descTip(m.desc, m.tip, good ? `Good for: ${good}.` : "") };
      }) });
  }
  // settings: the model's parameters, preprocessing and validation, with recommendations for the detected kind
  function renderRmSettings(useRecommended) {
    const sc = mlx.schema, m = sc.models[rm.model], k = rm.kind;
    const recP = (useRecommended && k?.params?.[rm.model]) || {};
    const fits = (p) => !p.tasks || p.tasks.includes("classification");
    $("#rm-params").innerHTML = m.params.filter(fits).map((p) => mlField(p, p.name in recP ? recP[p.name] : p.default, "model")).join("") ||
      `<p class="hint">${esc(m.title)} has no settings: it works as it is.</p>`;
    const prev = useRecommended ? {} : scopeVals("#rm-prep", "common");
    const recPrep = k ? { scaling: k.scaling, reduce: k.reduce === "auto" && !["mlc", "lda", "nb", "knn", "svm", "mlp"].includes(rm.model) ? "none" : k.reduce } : {};
    const prep = sc.common.filter((p) => p.group === "prep" && fits(p) && !["target_transform", "drop_correlated"].includes(p.name));
    $("#rm-prep").innerHTML = prep.map((p) => mlField(p, p.name in prev ? prev[p.name] : (p.name in recPrep ? recPrep[p.name] : p.default), "common")).join("");
    const sync = () => { const r = $('#rm-prep [data-p="outlier_pct"]')?.closest(".pca-field"); if (r) r.classList.toggle("hidden", $('#rm-prep [data-p="outliers"]').value !== "clip"); };
    $('#rm-prep [data-p="outliers"]')?.addEventListener("change", sync);
    sync();
    const prevAdv = scopeVals("#rm-adv", "common");
    const adv = sc.common.filter((p) => !p.group && fits(p) && !["cv_folds"].includes(p.name));
    $("#rm-adv").innerHTML = adv.map((p) => mlField(p, p.name in prevAdv ? prevAdv[p.name] : p.default, "common")).join("");
    const mr = $('#rm-adv [data-p="max_train_rows"]');
    if (mr) mr.placeholder = `model default: ${m.max_rows.toLocaleString()}`;
  }
  $("#rm-reset").onclick = () => { rm.touched = false; applyRmRecommended(true); };

  $("#rm-run").onclick = async () => {
    const err = $("#rm-error"); err.classList.add("hidden");
    const l = rm.layer, gl = getLayer($("#rm-gt").value), bands = rmBands();
    if (!l) return toast("Choose the image first", true);
    if (!bands.length) return toast("Use at least one band", true);
    if (!gl) return toast("Choose the ground truth (polygons / points or a class raster)", true);
    const ground_truth = gl.type === "raster" ? { type: "raster", path: gl.path, band: +$("#rm-gt-band").value || 1 }
      : { type: "vector", geojson: gl.geojson, field: $("#rm-gt-field").value || null };
    const refl = $("#rm-refl").checked, pc = +$("#rm-pc").value;
    const tuning = $("#rm-tune").checked ? { enabled: true, method: "random", iter: 12, folds: 3, metric: "auto",
      space: Object.fromEntries(Object.entries(mlx.schema.search[rm.model] || {}).map(([k2, v]) => [k2, v.map((x) => x === null ? "None" : String(x).replace(/,/g, ";"))])) } : {};
    if (tuning.enabled && !Object.keys(tuning.space).length) tuning.enabled = false;
    const body = {
      path: l.path, bands: bands.length === l.info.count ? null : bands, ground_truth, model: rm.model,
      params: scopeVals("#rm-params", "model"), common: { ...scopeVals("#rm-prep", "common"), ...scopeVals("#rm-adv", "common") }, tuning,
      clip: getClip("rm-area"), map_whole: $("#rm-map-whole").checked, factor: +$("#rm-factor").value || 1,
      scale: refl ? (l.scale ?? 1) : 1, offset: refl ? (l.offset ?? 0) : 0, per_class: pc || null,
      name: $("#rm-name").value || "classified", class_colors: gl.samples ? gl.classColors : null,
      confidence: $("#rm-conf").checked, resolution: $("#rm-res").value,
    };
    const btn = $("#rm-run"); btn.disabled = true; $("#rm-result").classList.add("hidden");
    try {
      const job = await api("/api/rasterml/run", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "rasterml", save: "rasterml", title: `${mlx.schema.models[rm.model].title} · ${body.name}` });
      const r = done.result;
      if (r.path) await addRasterFromPath(r.path, { name: body.name, zoom: false });
      showRmResult(r);
      refreshModels();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  function showRmResult(r) {
    const box = $("#rm-result"), s = r.samples, k = r.kind;
    const counts = Object.entries(s.classes).sort((a, b) => b[1] - a[1]), max = Math.max(1, ...counts.map(([, n]) => n));
    box.innerHTML = `<div class="card rm-head-card">
        <div class="row between"><h2 style="margin:0">✓ Map ready</h2><span class="rm-kind-tag k-${k.kind}">${esc(k.title)}</span></div>
        <div class="pca-sum" style="margin-top:4px">${s.pixels.toLocaleString()} training pixels from ${counts.length} classes · ${s.bands} bands · ${r.seconds} s total
          ${r.map ? ` · map ${r.map.width.toLocaleString()} × ${r.map.height.toLocaleString()} px${r.map.factor > 1 ? ` (${r.map.factor}× coarser)` : ""}` : ""}</div>
        <div class="home-label" style="margin-top:10px">Training pixels per class</div>
        <div class="dist">${counts.slice(0, 20).map(([c, n]) => `<div style="grid-template-columns:1fr 3fr auto"><span>${esc(c)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * n / max)}%"></span></span><b>${n.toLocaleString()}</b></div>`).join("")}</div>
        <p class="hint">The map was added to Contents${r.map?.confidence ? " (band 2 = confidence %)" : ""}. The model is listed under Classical ML ▸ Your models, so you can apply it to other images with <b>Classify an image</b>.</p>
      </div><div id="rm-train-res"></div>`;
    box.classList.remove("hidden");
    showTrainResult(r.model, $("#rm-train-res"));
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function initMlTrain() {
    mlx.schema = await api("/api/ml/schema");
    wireGotoRt();
  }
