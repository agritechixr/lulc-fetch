  // PCA & dimensionality reduction tool.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ PCA & dimensionality reduction tool
  const pcaState = { schema: null, layer: null, method: "pca", job: null };

  function refreshPcaInputs() {
    const sel = $("#pca-input");
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    sel.innerHTML = `<option value="">${rasters.length ? "Choose a raster layer…" : "No raster layers yet"}</option>` +
      rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
    if (pcaState.layer && rasters.includes(pcaState.layer)) sel.value = pcaState.layer.id;
    else if (pcaState.layer) { pcaState.layer = null; renderPcaBands(); }
  }

  function recommendedBand(b) {
    const name = an.catalog ? normBand(String(b.description).replace(/\s/g, "")) : null;
    return name && !pcaState.schema.atmospheric_bands.includes(name) && name !== "VVVH";
  }
  function renderPcaBands() {
    const l = pcaState.layer, box = $("#pca-bands");
    if (!l) { box.innerHTML = ""; $("#pca-bands-hint").textContent = "Choose an input image first."; return; }
    const bands = l.info.bands, anyKnown = bands.some(recommendedBand);
    box.innerHTML = bands.map((b) => {
      const rec = anyKnown ? recommendedBand(b) : true;
      const label = b.description !== `Band ${b.index}` ? esc(b.description) : `${b.index}`;
      return `<label title="Band ${b.index}: ${esc(b.description)}"><input type="checkbox" value="${b.index}" data-rec="${rec ? 1 : 0}" ${rec ? "checked" : ""}>${label}</label>`;
    }).join("");
    $$("#pca-bands input").forEach((i) => i.onchange = updatePcaBandHint);
    updatePcaBandHint();
  }
  const pcaBands = () => $$("#pca-bands input:checked").map((i) => +i.value);
  function updatePcaBandHint() {
    const n = pcaBands().length;
    $("#pca-bands-hint").textContent = n < 2 ? "Select at least 2 bands." : `${n} bands selected.`;
    const nc = $('[data-p="n_components"]');
    if (nc && pcaState.method !== "kernel") { nc.max = Math.max(1, n); if (+nc.value > n && n >= 1) nc.value = Math.min(3, n); }
  }
  $("#pca-bands-rec").onclick = () => { $$("#pca-bands input").forEach((i) => i.checked = i.dataset.rec === "1"); updatePcaBandHint(); };
  $("#pca-bands-all").onclick = () => { $$("#pca-bands input").forEach((i) => i.checked = true); updatePcaBandHint(); };
  $("#pca-bands-none").onclick = () => { $$("#pca-bands input").forEach((i) => i.checked = false); updatePcaBandHint(); };

  function renderPcaMethods() {
    const ms = pcaState.schema.methods;
    modelPicker($("#pca-methods"), { value: pcaState.method, onChange: (k) => { pcaState.method = k; renderPcaMethods(); renderPcaParams(true); },
      items: Object.entries(ms).map(([k, m]) => ({ id: k, title: m.title, badge: m.recommended ? "recommended" : "", tip: descTip(m.desc, m.tip) })) });
  }

  function pcaField(p, value) {
    let input;
    if (p.type === "bool") input = `<input type="checkbox" data-p="${p.name}" ${value ? "checked" : ""}>`;
    else if (p.type === "select") input = `<select data-p="${p.name}">${p.options.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
    else input = `<input type="number" data-p="${p.name}" step="${p.type === "int" ? 1 : "any"}" ${p.min != null ? `min="${p.min}"` : ""} ${p.max != null ? `max="${p.max}"` : ""}
      value="${value ?? ""}" placeholder="${esc(p.placeholder || "")}">`;
    const off = p.name === "standardize" && ["nmf", "svd"].includes(pcaState.method);
    return `<div class="pca-field ${off ? "disabled" : ""}"><span>${esc(p.label)}${tipBtn(p.tip)}</span>${input}
      ${off ? `<div class="note">Not used by ${esc(pcaState.schema.methods[pcaState.method].title)}: bands are rescaled to 0–1 instead.</div>` : ""}</div>`;
  }
  function renderPcaParams(keepCommon = false) {
    const sc = pcaState.schema, m = sc.methods[pcaState.method];
    const prev = keepCommon ? collectPcaParams(true) : {};
    const all = [...sc.common, ...m.params];
    const val = (p) => (p.name in prev ? prev[p.name] : p.default);
    $("#pca-params").innerHTML = all.filter((p) => !p.advanced).map((p) => pcaField(p, val(p))).join("");
    const adv = all.filter((p) => p.advanced);
    $("#pca-adv").innerHTML = adv.map((p) => pcaField(p, val(p))).join("");
    $("#pca-adv-wrap").classList.toggle("hidden", !adv.length);
    $$('#pca-params [data-p="standardize"], #pca-adv [data-p="standardize"]').forEach((i) => i.disabled = ["nmf", "svd"].includes(pcaState.method));
    updatePcaBandHint();
  }
  function collectPcaParams(raw = false) {
    const out = {};
    $$("#pca-params [data-p], #pca-adv [data-p]").forEach((i) => {
      const v = i.type === "checkbox" ? i.checked : i.value;
      out[i.dataset.p] = raw ? v : (i.type === "number" ? (v === "" ? null : +v) : v);
    });
    return out;
  }
  $("#pca-reset").onclick = () => { renderPcaParams(false); if (pcaState.layer) $("#pca-bands-rec").click(); };
  $("#pca-input").onchange = () => {
    pcaState.layer = getLayer($("#pca-input").value);
    renderPcaBands();
    if (pcaState.layer) selectLayer(pcaState.layer.id);
  };

  $("#pca-run").onclick = async () => {
    const l = pcaState.layer, err = $("#pca-error");
    err.classList.add("hidden");
    if (!l) return toast("Choose an input image first", true);
    const bands = pcaBands();
    if (bands.length < 2) return toast("Select at least 2 bands", true);
    const m = pcaState.schema.methods[pcaState.method];
    const btn = $("#pca-run");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>Running ${esc(m.title)}…`;
    $("#pca-report").classList.add("hidden");
    $("#pca-status").textContent = `Starting ${m.title}…`;
    try {
      const job = await api("/api/pca/run", { method: "POST", json: {
        path: l.path, bands, method: pcaState.method, params: collectPcaParams(), clip: getClip("pca-area"), name: l.name } });
      addedJobs.add(job.id);  // this tool adds the result itself (as an RGB of the components)
      prefs.set("addedJobs", [...addedJobs].slice(-200));
      pcaState.job = job.id;
      $("#pca-status").textContent = "";
      const j = await trackJob(job, { tool: "pca", save: "pca", title: `${m.title} · ${l.name}` });
      const rep = j.result;
      const n = rep.n_components;
      const out = await addRasterFromPath(rep.path, { name: `${m.title} (${n}) · ${l.name}${getClip("pca-area") ? " · area" : ""}`,
        render: n >= 3 ? { rgb: [1, 2, 3] } : { band: 1, stretch: "auto", cmap: "Viridis" } });
      out.pcaReport = rep;
      saveLayers();
      showPcaReport(rep, out);
      $("#pca-status").textContent = `Done in ${rep.seconds} s. The result is in Contents.`;
      refreshJobs();
    } catch (e) {
      $("#pca-status").textContent = e.cancelled ? "Cancelled. Change the parameters and run again." : "";
      if (!e.cancelled) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally {
      btn.disabled = false;
      btn.textContent = "Run analysis";
    }
  };

  function showPcaReport(rep, layer) {
    const box = $("#pca-report");
    const ev = rep.explained_variance || rep.explained_variance_kept;
    let chart = "";
    if (ev) {
      const w = 300, h = 110, n = ev.length, bw = Math.min(46, (w - 20) / n - 6);
      let cum = 0;
      const pts = [];
      const bars = ev.map((v, i) => {
        cum += v;
        const x = 14 + i * ((w - 20) / n), bh = v * (h - 30);
        pts.push(`${x + bw / 2},${h - 18 - cum * (h - 30)}`);
        return `<rect x="${x}" y="${h - 18 - bh}" width="${bw}" height="${bh}" rx="2" fill="var(--accent)" opacity=".85"><title>${rep.prefix}${i + 1}: ${(v * 100).toFixed(1)}%</title></rect>
          <text x="${x + bw / 2}" y="${h - 5}" font-size="9" text-anchor="middle" fill="currentColor">${rep.prefix}${i + 1}</text>
          <text x="${x + bw / 2}" y="${h - 21 - bh}" font-size="9" text-anchor="middle" fill="currentColor">${(v * 100).toFixed(1)}%</text>`;
      }).join("");
      chart = `<div class="home-label" style="margin-top:14px">${rep.explained_variance ? "Explained variance" : "Share of variance among kept components"}
          ${tipBtn(rep.explained_variance ? "How much of the image's total variation each component holds. The line is the running total. If the first 3 components add up to over 95%, they summarise the image well." : "Kernel PCA can't measure the share of total variance, only how the kept components compare with each other.")}</div>
        <svg class="ev-chart" viewBox="0 0 ${w} ${h}" style="color:var(--muted)">${bars}
          ${rep.explained_variance ? `<polyline points="${pts.join(" ")}" fill="none" stroke="var(--text)" stroke-width="1.5" stroke-dasharray="3 2"/>` : ""}</svg>
        ${rep.explained_variance ? `<div class="hint" style="margin-top:0">First ${ev.length} components hold <b>${(ev.reduce((a, b) => a + b, 0) * 100).toFixed(1)}%</b> of the variation.</div>` : ""}`;
    }
    let table = "";
    if (rep.loadings) {
      const maxAbs = Math.max(...rep.loadings.flat().map(Math.abs)) || 1;
      const cell = (v) => {
        const a = Math.min(1, Math.abs(v) / maxAbs), c = v >= 0 ? "31,122,90" : "180,35,24";
        return `<td style="background:rgba(${c},${(a * 0.75).toFixed(2)});color:${a > 0.55 ? "#fff" : "inherit"}">${v.toFixed(2)}</td>`;
      };
      table = `<div class="home-label" style="margin-top:14px">Band loadings ${tipBtn("How strongly each band contributes to each component (green = positive, red = negative). Bands with large values drive that component. E.g. NIR high and Red negative means the component tracks vegetation.")}</div>
        <div class="load-wrap"><table class="load-table"><tr><th></th>${rep.bands.map((b) => `<th>${esc(b)}</th>`).join("")}</tr>
        ${rep.loadings.map((row, i) => `<tr><th>${rep.prefix}${i + 1}</th>${row.map(cell).join("")}</tr>`).join("")}</table></div>`;
    }
    const pcaHint = rep.method === "pca" || rep.method === "incremental"
      ? `<p class="hint">Typical reading for multispectral imagery: <b>${rep.prefix}1</b> ≈ overall brightness, <b>${rep.prefix}2</b> ≈ vegetation vs. soil / built-up, <b>${rep.prefix}3</b> ≈ moisture / water. Check the loadings to confirm.</p>` : "";
    box.innerHTML = `<div class="pca-sum"><b>${esc(rep.title)}</b> · ${rep.n_components} components from ${rep.bands.length} bands
        (${esc(rep.bands.join(", "))})<br>Output ${rep.width.toLocaleString()} × ${rep.height.toLocaleString()} px${rep.factor > 1 ? ` (${rep.factor}× coarser than native)` : " at native resolution"}
        · fitted on ${rep.pixels_fit.toLocaleString()} pixels · ${rep.standardized ? "standardized" : "not standardized"} · ${rep.seconds} s
        ${rep.reconstruction_error != null ? `<br>Reconstruction error: ${rep.reconstruction_error.toFixed(3)}` : ""}</div>
      ${rep.warnings?.length ? `<div class="warn">${rep.warnings.map(esc).join("<br>")}. Try more iterations (Advanced).</div>` : ""}
      ${chart}${table}${pcaHint}
      <div class="row"><button class="btn small" data-pz>Zoom to result</button><button class="btn small" data-px>Export…</button>
        <button class="btn small" data-pp title="Show another combination of components">Change display…</button></div>`;
    box.classList.remove("hidden");
    $("[data-pz]", box).onclick = () => zoomTo(layer);
    $("[data-px]", box).onclick = () => openExport(layer);
    $("[data-pp]", box).onclick = () => openProps(layer);
  }

  async function initPca() {
    pcaState.schema = await api("/api/pca/schema");
    renderPcaMethods();
    renderPcaParams(false);
    renderPcaBands();
  }
