  // Train classify model and Classify image (PyTorch add-on).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Deep learning (optional PyTorch add-on): train + classify
  const dlx = { status: null, schema: null, arch: "unet", datasets: [], ds: null, models: [], dpOn: {} };
  const stars5 = (n) => "★".repeat(n) + "☆".repeat(5 - n);
  async function dlStatus(force) {
    if (!dlx.status || force) dlx.status = await api("/api/dl/status");
    return dlx.status;
  }
  // the add-on card replaces the tool until PyTorch is installed
  async function renderAddon(panel) {
    const st = await dlStatus();
    const card = $("[data-addon]", panel), body = $(".dl-body", panel);
    card.classList.toggle("hidden", st.available);
    body.classList.toggle("hidden", !st.available);
    if (st.available) return true;
    const win = st.variants.includes("cuda");
    card.innerHTML = `<h2>Deep-learning add-on needed</h2>
      <p class="hint" style="margin-top:0">The deep-learning tools use <b>PyTorch</b> and <b>segmentation-models-pytorch</b> (free, open source). They are installed once, from the internet, into ${st.frozen ? `LULC Fetch's data folder` : "this Python environment"} (${win ? `about ${fmt(st.size_mb.cuda / 1000, 1)} GB for the NVIDIA version, ${fmt(st.size_mb.cpu / 1000, 1)} GB for CPU only` : `about ${fmt(st.size_mb.default / 1000, 1)} GB`}). Everything else in LULC Fetch works without them.</p>
      ${win ? `<label>Version<select data-variant><option value="cuda">NVIDIA GPU (CUDA): fast training, needs an NVIDIA graphics card</option><option value="cpu">CPU only: smaller download, slower training</option></select></label>` : `<p class="hint">On this Mac, training uses the Apple GPU (Metal) automatically.</p>`}
      <div class="pkgs">pip install torch torchvision segmentation-models-pytorch</div>
      <button class="btn primary big-btn" data-install>Install the deep-learning add-on</button>
      ${st.error && !/No module named/.test(st.error) ? `<div class="warn err" style="margin-top:8px">PyTorch is there but couldn't be loaded: ${esc(st.error)}</div>` : ""}`;
    $("[data-install]", card).onclick = async (e) => {
      const btn = e.currentTarget; btn.disabled = true;
      try {
        const job = await api("/api/dl/install", { method: "POST", json: { variant: $("[data-variant]", card)?.value || "default" } });
        await trackJob(job, { title: "Installing the deep-learning add-on" });
        await dlStatus(true);
        toast("Deep-learning add-on installed");
        renderAddon(panel).then((ok) => ok && ({ "tab-dltrain": refreshDt, "tab-detect": refreshOd, "tab-traindet": refreshTd }[panel.id]
          || PLUGINS[panel.id.slice(4)]?.hooks?.open || refreshDp)());
      } catch (err) { if (notCancelled(err)) toast(err.message, true); }
      finally { btn.disabled = false; }
    };
    return false;
  }
  function dlParamField(p, value, scope = "dl") {
    if (p.kind === "multi") {
      const v = new Set(value || []);
      return `<div class="pca-field wide"><span>${esc(p.title)}${tipBtn(p.tip)}</span><div class="chk-row">${p.choices.map(([k, t]) => `<label><input type="checkbox" data-dlmulti="${p.name}" value="${k}" ${v.has(k) ? "checked" : ""}>${esc(t)}</label>`).join("")}</div></div>`;
    }
    return mlField({ ...p, type: p.kind === "choice" ? "select" : p.kind, label: p.title, options: p.choices }, value, scope);
  }
  function dlParams() {
    const out = {};
    $$('#dt-basic [data-scope="dl"], #dt-adv [data-scope="dl"]').forEach((i) => {
      out[i.dataset.p] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : +i.value) : i.value;
    });
    $$("[data-dlmulti]").forEach((i) => { (out[i.dataset.dlmulti] ||= []); if (i.checked) out[i.dataset.dlmulti].push(i.value); });
    Object.keys(out).forEach((k) => out[k] === null && delete out[k]);
    return out;
  }
  async function refreshDt() {
    if (!(await renderAddon($("#tab-dltrain")))) return;
    if (!dlx.schema) {
      dlx.schema = await api("/api/dl/schema");
      const sc = dlx.schema;
      $("#dt-basic").innerHTML = sc.params.filter((p) => !p.adv).map((p) => dlParamField(p, p.default)).join("");
      $("#dt-adv").innerHTML = sc.params.filter((p) => p.adv).map((p) => dlParamField(p, p.default)).join("");
      const syncEs = () => ["patience", "monitor", "min_delta"].forEach((n) => $(`#dt-basic [data-p="${n}"]`)?.closest(".pca-field").classList.toggle("hidden", !$('#dt-basic [data-p="early_stop"]').checked));
      $('#dt-basic [data-p="early_stop"]').addEventListener("change", syncEs);
      syncEs();
      const st = dlx.status;
      const devSel = $('#dt-adv [data-p="device"]');
      [...devSel.options].forEach((o) => { if (o.value !== "auto" && !st.devices.includes(o.value)) o.disabled = true; });
      renderDtArchs();
      $("#dt-folder").value = prefs.get("dt-folder", "");
    }
    try { dlx.datasets = await api("/api/dl/datasets"); } catch { dlx.datasets = []; }
    const sel = $("#dt-ds"), cur = sel.value || prefs.get("dt-ds", "");
    sel.innerHTML = dlx.datasets.length ? dlx.datasets.map((d) => `<option value="${esc(d.folder)}">${esc(d.name)} · ${d.labelled} patches · ${d.patch_size_px?.[0]} px · ${d.band_count} bands · ${d.classes.length} classes</option>`).join("")
      : `<option value="">No training data yet: make it with Make training data, or Browse…</option>`;
    if (dlx.datasets.some((d) => d.folder === cur)) sel.value = cur;
    await dtDatasetChanged();
    await refreshDtResume();
  }
  function renderDtArchs() {
    const sc = dlx.schema;
    modelPicker($("#dt-archs"), { value: dlx.arch, onChange: (k) => { dlx.arch = k; renderDtArchs(); },
      items: Object.entries(sc.archs).map(([k, a]) => ({ id: k, title: a.title, group: { tv: "torchvision", yolo: "YOLO · ultralytics (YOLO & SAM add-on)" }[a.lib] || "segmentation-models-pytorch",
        badge: k === "unet" ? "recommended" : "", meta: starMeta(a.accuracy, a.speed, 5), tip: a.desc })) });
    const a = sc.archs[dlx.arch], encs = a.encoders || sc.smp_encoders, cur = $("#dt-enc").value;
    $("#dt-enc").innerHTML = encs.map((e) => `<option value="${e}">${esc(sc.encoders[e])}</option>`).join("");
    $("#dt-enc").value = encs.includes(cur) ? cur : encs.includes("tu-mobilenetv3_large_100") ? "tu-mobilenetv3_large_100" : encs.includes("yolo-s") ? "yolo-s" : encs[0];
    if (a.lib === "yolo") renderYoloAddon($("#dt-yolo"), "YOLO26 semantic", () => renderDtArchs()); else $("#dt-yolo").classList.add("hidden");
    $("#dt-name").value = $("#dt-name").dataset.touched ? $("#dt-name").value : `${(dlx.ds?.name || "model")}_${dlx.arch}`.slice(0, 60);
  }
  $("#dt-name").addEventListener("input", () => $("#dt-name").dataset.touched = "1");
  async function dtDatasetChanged() {
    const f = $("#dt-ds").value;
    dlx.ds = null;
    if (!f) { $("#dt-ds-info").innerHTML = ""; return; }
    prefs.set("dt-ds", f);
    try { dlx.ds = await api("/api/dl/dataset", { method: "POST", json: { folder: f } }); }
    catch (e) { $("#dt-ds-info").innerHTML = `<span style="color:var(--warn)">${esc(e.message)}</span>`; return; }
    const d = dlx.ds, total = d.classes.reduce((a, c) => a + (c.pixels || 0), 0) || 1;
    $("#dt-ds-info").innerHTML = `${d.labelled.toLocaleString()} labelled patches · ${d.patch_size_px[0]} × ${d.patch_size_px[1]} px${d.pixel_size ? ` (${fmt(d.patch_size_m[0], 0)} m)` : ""} · ${d.band_count} bands · ${d.size_mb} MB
      ${d.labelled < 30 ? `<br><span style="color:var(--warn)">Very few patches: use smaller patches, overlap or a bigger area for a better model.</span>` : ""}
      <div class="dl-swatches">${d.classes.map((c) => `<span title="label ${esc(String(c.value))}"><i style="background:${esc(c.color || "#999")}"></i>${esc(c.name)} <small>${fmt(100 * (c.pixels || 0) / total, 1)}%</small></span>`).join("")}</div>`;
    if (!$("#dt-name").dataset.touched) $("#dt-name").value = `${d.name}_${dlx.arch}`.slice(0, 60);
  }
  $("#dt-ds").onchange = () => { dtDatasetChanged(); refreshDtResume(); };
  $("#dt-ds-browse").onclick = async () => {
    const f = await pickFolder({ title: "Choose a training-data folder (made with Make training data)", start: prefs.get("dt-ds-last", ""), okLabel: "Use this folder" });
    if (!f) return;
    try {
      await api("/api/dl/dataset", { method: "POST", json: { folder: f } });
      prefs.set("dt-ds-last", f); prefs.set("dt-ds", f);
      await refreshDt();
    } catch (e) { toast(e.message, true); }
  };
  async function refreshDtResume() {
    try { dlx.models = await api("/api/dl/models"); } catch { dlx.models = []; }
    const sel = $("#dt-resume"), cur = sel.value;
    sel.innerHTML = `<option value="">No: train a new model</option>` + dlx.models.map((m) => `<option value="${esc(m.folder)}">${esc(m.name)} · ${esc(m.arch)} · ${m.epochs_run} epochs${m.miou != null ? ` · mIoU ${fmt(100 * m.miou, 1)}%` : ""}</option>`).join("");
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  $("#dt-folder").addEventListener("input", () => prefs.set("dt-folder", $("#dt-folder").value));
  $("#dt-browse").onclick = async () => {
    const f = await pickFolder({ title: "Save the model in…", start: $("#dt-folder").value || prefs.get("save-dir:last", ""), okLabel: "Use this folder" });
    if (f) { $("#dt-folder").value = f; prefs.set("dt-folder", f); }
  };
  $("#dt-run").onclick = async () => {
    const err = $("#dt-error"); err.classList.add("hidden");
    if (!dlx.ds) return toast("Choose the training data first", true);
    const body = { dataset: dlx.ds.folder, arch: dlx.arch, encoder: $("#dt-enc").value, pretrained: $("#dt-pre").checked, params: dlParams(),
                   name: $("#dt-name").value.trim() || "dl_model", folder: $("#dt-folder").value.trim() || null, resume: $("#dt-resume").value || null };
    const btn = $("#dt-run"); btn.disabled = true;
    $("#dt-result").classList.add("hidden");
    const live = $("#dt-live"); live.classList.remove("hidden");
    live.innerHTML = `<div class="dl-live-head"><b>Training…</b><span class="hint">Preparing the data and the model</span></div>`;
    try {
      const job = await api("/api/dl/train", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "dltrain", title: `Training ${dlx.schema.archs[dlx.arch].title} · ${body.name}`, onPoll: (j) => j.live && renderDtLive(j.live) });
      if (done.live) renderDtLive(done.live, true);
      showDtResult(done.result);
      refreshDtResume();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  // live curves: loss (left axis) and mIoU (0–1, right axis) per epoch
  function renderDtLive(L, final = false) {
    const H = L.history || [];
    if (!H.length) return;
    const W = 460, Ht = 190, l = 38, r = 34, t = 10, b = 24;
    const n = Math.max(L.epochs || H.length, H.length), X = (e) => l + (e - 1) / Math.max(1, n - 1) * (W - l - r);
    const losses = H.flatMap((h) => [h.train_loss, h.val_loss]).filter((v) => v != null && isFinite(v));
    const lmax = Math.max(...losses, 1e-6) * 1.05, lmin = Math.min(...losses, 0);
    const YL = (v) => Ht - b - (v - lmin) / Math.max(lmax - lmin, 1e-9) * (Ht - b - t), YM = (v) => Ht - b - v * (Ht - b - t);
    const line = (key, Y, color, dash = "") => { const pts = H.filter((h) => h[key] != null).map((h) => `${X(h.epoch).toFixed(1)},${Y(h[key]).toFixed(1)}`).join(" "); return pts ? `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.8" ${dash ? `stroke-dasharray="${dash}"` : ""}/>` : ""; };
    const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => `<line x1="${l}" x2="${W - r}" y1="${YM(f)}" y2="${YM(f)}" class="grid"/><text x="${W - r + 4}" y="${YM(f) + 3}" class="tick">${f}</text><text x="${l - 4}" y="${YM(f) + 3}" class="tick" text-anchor="end">${fmt(lmin + f * (lmax - lmin), 2)}</text>`).join("");
    const be = L.best_epoch ? `<line x1="${X(L.best_epoch)}" x2="${X(L.best_epoch)}" y1="${t}" y2="${Ht - b}" stroke="#16a34a" stroke-dasharray="3 3"/>` : "";
    const xt = [1, Math.ceil(n / 2), n].map((e) => `<text x="${X(e)}" y="${Ht - 8}" class="tick" text-anchor="middle">${e}</text>`).join("");
    const last = H[H.length - 1], bestRow = H.find((h) => h.epoch === L.best_epoch);
    const eta = L.eta_s != null && !final ? ` · ~${fmtSecs(L.eta_s)} left` : "";
    $("#dt-live").innerHTML = `<div class="dl-live-head"><b>${final ? "Training curves" : `Epoch ${last.epoch} of ${L.epochs}`}</b><span class="hint">${esc(L.device || "")} · batch ${L.batch_size}${eta}</span></div>
      <svg viewBox="0 0 ${W} ${Ht}" class="dl-chart">${grid}${xt}${be}${line("train_loss", YL, "#2563eb")}${line("val_loss", YL, "#f59e0b")}${line("train_miou", YM, "#2563eb", "4 3")}${line("val_miou", YM, "#f59e0b", "4 3")}</svg>
      <div class="dl-legend"><span style="color:#2563eb">■ training</span><span style="color:#f59e0b">■ validation</span><span class="hint" style="margin:0">solid = loss (left axis) · dashed = mIoU (right) · green line = best epoch</span></div>
      <table class="dl-epochs"><tr><th>Epoch</th><th>Train loss</th><th>Val loss</th><th>Val mIoU</th><th>Val acc.</th><th>s</th></tr>
        ${H.slice(-6).reverse().map((h) => `<tr class="${h.epoch === L.best_epoch ? "best" : ""}"><td>${h.epoch}${h.epoch === L.best_epoch ? " ★" : ""}</td><td>${fmt(h.train_loss, 3)}</td><td>${fmt(h.val_loss, 3)}</td><td>${h.val_miou != null ? fmt(100 * h.val_miou, 1) + "%" : "–"}</td><td>${h.val_acc != null ? fmt(100 * h.val_acc, 1) + "%" : "–"}</td><td>${fmt(h.seconds, 0)}</td></tr>`).join("")}</table>
      ${bestRow && !final ? `<p class="hint" style="margin:6px 0 0">Best so far: epoch ${bestRow.epoch}, val mIoU ${fmt(100 * bestRow.val_miou, 1)}%. <b>Cancel</b> stops training and keeps the best model.</p>` : ""}`;
  }
  function showDtResult(r) {
    const c = r.config, ev = c.test || c.val, which = c.test ? "test" : "validation";
    const pct = (v) => v == null ? "–" : fmt(100 * v, 1) + "%";
    const reportUrl = `/api/dl/report?folder=${encodeURIComponent(r.folder)}`;
    const box = $("#dt-result");
    box.innerHTML = `<div class="card rm-head-card">
        <div class="row between"><h2 style="margin:0">✓ Model trained</h2><span class="hint">${esc(c.arch_title)} · ${esc(c.encoder_title)}</span></div>
        <div class="metric-tiles" style="margin-top:8px">
          <div class="metric"><b>${pct(ev.miou)}</b><span>mIoU (${which})</span></div><div class="metric"><b>${pct(ev.accuracy)}</b><span>Pixel accuracy</span></div>
          <div class="metric"><b>${pct(ev.f1_macro)}</b><span>Macro F1</span></div><div class="metric"><b>${c.best_epoch} / ${c.epochs_run}</b><span>Best epoch</span></div></div>
        ${c.stopped ? `<p class="hint">Training ${esc(c.stopped)}; the best epoch was kept.</p>` : ""}${stillImproving(c, "or continue this model with <b>Continue training</b>")}
        ${c.weights_note ? `<div class="warn">${esc(c.weights_note)}</div>` : ""}
        <div class="dist" style="margin-top:8px">${c.classes.map((k, i) => `<div style="grid-template-columns:minmax(0,1.6fr) 2fr auto"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(k.color || "#999")}"></i> ${esc(k.name)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * (ev.iou[i] || 0))}%;background:${esc(k.color || "")}"></span></span><b title="IoU">${pct(ev.iou[i])}</b></div>`).join("")}</div>
        <p class="hint" style="word-break:break-all">${esc(r.folder)}</p>
        <div class="row tight" style="flex-wrap:wrap"><a class="btn primary" href="${reportUrl}" target="_blank" rel="noopener">Open report</a><a class="btn" href="${reportUrl}&download=true">Download report</a>
          <button class="btn" data-dt-reveal>Show in folder</button><button class="btn" data-dt-use>Classify image with it</button></div>
      </div>`;
    box.classList.remove("hidden");
    $("[data-dt-reveal]", box).onclick = () => api("/api/project/reveal", { method: "POST", json: { path: r.folder } }).catch((e) => toast(e.message, true));
    $("[data-dt-use]", box).onclick = () => { prefs.set("dp-model", r.folder); switchTool("dlpredict"); };
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------- classify an image with a trained model
  async function refreshDp() {
    if (!(await renderAddon($("#tab-dlpredict")))) return;
    if (!dlx.schema) dlx.schema = await api("/api/dl/schema");
    const st = dlx.status;
    [...$("#dp-device").options].forEach((o) => { if (o.value !== "auto" && !st.devices.includes(o.value)) o.disabled = true; });
    try { dlx.models = await api("/api/dl/models"); } catch { dlx.models = []; }
    const sel = $("#dp-model"), cur = prefs.get("dp-model", "") || sel.value;
    sel.innerHTML = dlx.models.length ? dlx.models.map((m) => `<option value="${esc(m.folder)}">${esc(m.name)} · ${esc(m.arch)}${m.miou != null ? ` · mIoU ${fmt(100 * m.miou, 1)}%` : ""}</option>`).join("")
      : `<option value="">No models yet: train one with Train classify model, or Browse…</option>`;
    if (dlx.models.some((m) => m.folder === cur)) sel.value = cur;
    dpModelChanged();
  }
  const dpModel = () => dlx.models.find((m) => m.folder === $("#dp-model").value);
  function dpModelChanged() {
    const m = dpModel();
    if (m) prefs.set("dp-model", m.folder);
    $("#dp-model-info").innerHTML = !m ? "" : `${esc(m.arch)} · ${esc(m.encoder)} · trained ${esc(m.trained || "")}${m.miou != null ? ` · mIoU ${fmt(100 * m.miou, 1)}%` : ""}
      <br>Needs <b>${m.in_channels} bands</b>: ${esc((m.bands || []).slice(0, 20).join(", "))}${(m.bands || []).length > 20 ? " …" : ""}${m.pixel_size ? ` · trained at ${fmt(m.pixel_size[0], 1)} m pixels` : ""}
      <div class="dl-swatches">${(m.classes || []).map((c) => `<span><i style="background:${esc(c.color || "#999")}"></i>${esc(c.name)}</span>`).join("")}</div>
      ${m.has_report ? `<a href="/api/dl/report?folder=${encodeURIComponent(m.folder)}" target="_blank" rel="noopener">Open its report</a> · ` : ""}<a href="#" data-dp-forget>Remove from list</a>`;
    $("[data-dp-forget]", $("#dp-model-info"))?.addEventListener("click", async (e) => {
      e.preventDefault();
      await api(`/api/dl/models?folder=${encodeURIComponent(m.folder)}`, { method: "DELETE" }).catch((x) => toast(x.message, true));
      prefs.set("dp-model", ""); refreshDp();
    });
    if (m && !$("#dp-name").dataset.touched) $("#dp-name").value = `${m.name}_map`.slice(0, 60);
    if (m && /^YOLO/.test(m.arch || "")) renderYoloAddon($("#dp-yolo"), "This model", () => dpModelChanged()); else $("#dp-yolo").classList.add("hidden");
    renderDpLayers();
  }
  $("#dp-name").addEventListener("input", () => $("#dp-name").dataset.touched = "1");
  $("#dp-model").onchange = dpModelChanged;
  $("#dp-browse").onclick = async () => {
    const f = await pickFolder({ title: "Choose a deep-learning model folder", start: prefs.get("dp-last", ""), okLabel: "Use this model" });
    if (!f) return;
    try { const r = await api("/api/dl/models/add", { method: "POST", json: { folder: f } }); prefs.set("dp-last", f); prefs.set("dp-model", r.folder); refreshDp(); }
    catch (e) { toast(e.message, true); }
  };
  function renderDpLayers() {
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived && l.path), m = dpModel();
    if (!Object.values(dlx.dpOn).some(Boolean) && rasters.length) {
      const fit = m && rasters.find((l) => l.info?.count === m.in_channels);
      dlx.dpOn[(fit || rasters[rasters.length - 1]).id] = true;
    }
    $("#dp-layers").innerHTML = rasters.length ? rasters.slice().reverse().map((l) => `<div class="st-layer ${dlx.dpOn[l.id] ? "on" : ""}"><label><input type="checkbox" data-dpl="${esc(l.id)}" ${dlx.dpOn[l.id] ? "checked" : ""}>${esc(l.name)}
        <small>${l.info?.count ?? "?"} bands</small></label></div>`).join("") : '<p class="hint">Add the image to classify to Contents first (+ Add data).</p>';
    $$("[data-dpl]").forEach((c) => c.onchange = () => { dlx.dpOn[c.dataset.dpl] = c.checked; renderDpLayers(); });
    const chosen = rasters.filter((l) => dlx.dpOn[l.id]).reverse();
    const nb = chosen.reduce((a, l) => a + (l.info?.count || 0), 0);
    const names = chosen.flatMap((l) => (l.info?.bands || []).map((b) => b.description));
    if (!m) { $("#dp-check").textContent = ""; return; }
    const same = m.bands && names.length === m.bands.length && names.every((n, i) => n === m.bands[i]);
    $("#dp-check").innerHTML = !chosen.length ? "" : nb !== m.in_channels
      ? `<span style="color:var(--err)">✗ ${nb} bands selected, the model needs ${m.in_channels}.</span>`
      : same ? `<span style="color:var(--accent)">✓ ${nb} bands, same names and order as the training data.</span>`
      : `<span style="color:var(--warn)">⚠ ${nb} bands, but the band names differ from the training data (${esc(names.slice(0, 6).join(", "))}… vs ${esc(m.bands.slice(0, 6).join(", "))}…). Make sure they are the same bands in the same order.</span>`;
  }
  $("#dp-run").onclick = async () => {
    const err = $("#dp-error"); err.classList.add("hidden");
    const m = dpModel();
    if (!m) return toast("Choose a model first", true);
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived && l.path && dlx.dpOn[l.id]).reverse();
    if (!rasters.length) return toast("Tick the image to classify", true);
    const body = { model: m.folder, inputs: rasters.map((l) => ({ path: l.path, name: l.name })), clip: getClip("dp-area"),
                   overlap: +$("#dp-overlap").value, batch_size: +$("#dp-batch").value || 8, device: $("#dp-device").value,
                   confidence: $("#dp-conf").checked, name: $("#dp-name").value.trim() || "dl_map" };
    const btn = $("#dp-run"); btn.disabled = true; $("#dp-result").classList.add("hidden");
    try {
      const job = await api("/api/dl/predict", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "dlpredict", title: `Classifying with ${m.name}`, save: "dlpredict" });
      const r = done.result;
      await addRasterFromPath(r.path, { name: body.name, zoom: false });
      const box = $("#dp-result");
      box.innerHTML = `<div class="card rm-head-card"><h2 style="margin:0">✓ Map ready</h2>
        <div class="pca-sum">${r.width.toLocaleString()} × ${r.height.toLocaleString()} px · tiles ${r.tile} px with ${r.overlap} px overlap · ${esc(r.device)} · ${r.seconds} s</div>
        <div class="dist" style="margin-top:8px">${r.classes.map((c) => `<div style="grid-template-columns:minmax(0,1.6fr) 2fr auto"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(c.color || "#999")}"></i> ${esc(c.name)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(1, c.pct)}%;background:${esc(c.color || "")}"></span></span><b>${fmt(c.pct, 1)}%</b></div>`).join("")}</div>
        <p class="hint">The map was added to Contents${body.confidence ? " (band 2 = confidence %)" : ""}. Save it with right-click ▸ Save to folder or Export data.</p></div>`;
      box.classList.remove("hidden");
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
