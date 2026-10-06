  // Train detection model (YOLO on labelled polygons / points).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Train detection model (YOLO on labelled polygons / points)
  const td = { schema: null, task: prefs.get("td-task", "detect"), layerId: null };
  const tdLayer = () => getLayer($("#td-layer").value);
  const tdGt = () => getLayer($("#td-gt").value);
  async function refreshTd() {
    if (!(await renderAddon($("#tab-traindet")))) return;
    const ok = renderYoloAddon($("#td-yolo"), "Train detection model", () => refreshTd());
    $("#tab-traindet .td-body").classList.toggle("hidden", !ok);
    if (!ok) return;
    if (!td.schema) {
      td.schema = await api("/api/det/schema");
      const sc = td.schema;
      $("#td-basic").innerHTML = sc.params.filter((p) => !p.adv).map((p) => dlParamField(p, p.default, "td")).join("");
      $("#td-adv").innerHTML = sc.params.filter((p) => p.adv).map((p) => dlParamField(p, p.default, "td")).join("");
      const devSel = $('#td-adv [data-p="device"]');
      [...devSel.options].forEach((o) => { if (o.value !== "auto" && !dlx.status.devices.includes(o.value)) o.disabled = true; });
      $("#td-family").innerHTML = Object.entries(sc.families).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("");
      $("#td-size").innerHTML = Object.entries(sc.sizes).map(([k, t]) => `<option value="${k}">${k} · ${esc(t)}</option>`).join("");
      $("#td-size").value = prefs.get("td-size", "s");
      $("#td-folder").value = prefs.get("td-folder", "");
    }
    renderTdTasks();
    renderTdLayers();
    renderTdGt();
  }
  function renderTdTasks() {
    const ts = td.schema.tasks;
    modelPicker($("#td-tasks"), { value: td.task, onChange: (k) => { td.task = k; prefs.set("td-task", k); renderTdTasks(); renderTdGt(); },
      items: Object.entries(ts).map(([k, t]) => ({ id: k, title: t.title, badge: k === "obb" ? "aerial" : k === "detect" ? "recommended" : "", tip: t.desc })) });
  }
  function renderTdLayers() {
    const rasters = odRasters(), sel = $("#td-layer"), cur = sel.value || td.layerId;
    sel.innerHTML = rasters.length ? rasters.slice().reverse().map((l) => `<option value="${esc(l.id)}">${esc(l.name)} · ${l.info?.count ?? "?"} bands</option>`).join("")
      : `<option value="">Add the image to Contents first (Insert ▸ Add data)</option>`;
    if (rasters.some((l) => l.id === cur)) sel.value = cur;
    tdLayerChanged();
  }
  function tdLayerChanged() {
    const l = tdLayer();
    if (!l) { $("#td-bands").innerHTML = ""; return; }
    if (td.layerId !== l.id) {
      td.layerId = l.id;
      renderRgbPickers($("#td-bands"), l, () => {});
      $("#td-stretch").value = defaultStretch(l);
      if (!$("#td-tile").dataset.touched && l.info?.width) {   // tiles that fit the image: at least 3 × 3 of them
        const side = Math.min(l.info.width, l.info.height) * +$("#td-zoom").value;
        $("#td-tile").value = String([640, 512, 416, 320, 256, 128].find((n) => side >= 3 * n) || 128);
      }
    }
    tdTileHint();
  }
  $("#td-layer").onchange = tdLayerChanged;
  function renderTdGt() {
    const vecs = layers.filter((x) => x.type === "vector" && x.geojson?.features?.some((f) => /Polygon|Point/.test(f.geometry?.type || "")));
    const sel = $("#td-gt"), cur = sel.value;
    sel.innerHTML = vecs.length ? vecs.slice().reverse().map((x) => `<option value="${esc(x.id)}">${esc(x.name)} · ${x.geojson.features.length} shapes</option>`).join("")
      : `<option value="">Draw objects with Training samples, or add a shapefile / GeoJSON</option>`;
    if (vecs.some((x) => x.id === cur)) sel.value = cur;
    const g = tdGt();
    const keys = g ? [...new Set(g.geojson.features.flatMap((f) => Object.keys(f.properties || {})))] : [];
    const fsel = $("#td-field"), fcur = fsel.value;
    const pref = keys.includes(fcur) ? fcur : keys.find((k) => /^(class|label|name|type|category)$/i.test(k)) || keys[0];
    fsel.innerHTML = keys.length ? keys.map((k) => `<option ${k === pref ? "selected" : ""}>${esc(k)}</option>`).join("") : `<option value="">(no attributes: one class, "object")</option>`;
    tdGtInfo();
  }
  function tdGtInfo() {
    const g = tdGt(), f = $("#td-field").value;
    if (!g) { $("#td-gt-info").textContent = ""; $("#td-point-wrap").classList.add("hidden"); return; }
    const feats = g.geojson.features, pts = feats.filter((x) => /Point/.test(x.geometry?.type || "")).length;
    const polys = feats.filter((x) => /Polygon/.test(x.geometry?.type || "")).length;
    $("#td-point-wrap").classList.toggle("hidden", !pts);
    const counts = {};
    feats.forEach((x) => { const v = f ? x.properties?.[f] : "object"; if (v != null && v !== "") counts[v] = (counts[v] || 0) + 1; });
    const cls = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const colors = g.classColors || {};
    $("#td-gt-info").innerHTML = `${polys} polygon${polys === 1 ? "" : "s"}${pts ? `, ${pts} point${pts === 1 ? "" : "s"}` : ""} · ${cls.length} class${cls.length === 1 ? "" : "es"}
      <div class="dl-swatches">${cls.slice(0, 30).map(([k, n]) => `<span><i style="background:${esc(colors[k] || "#999")}"></i>${esc(k)} <small>${n}</small></span>`).join("")}</div>
      ${td.task === "segment" && pts && !polys ? `<span style="color:var(--warn)">⚠ Outlines need polygons drawn around the objects; points only give boxes.</span>` : ""}
      ${cls.some(([, n]) => n < 20) ? `<span class="hint">Classes with fewer than about 20 objects are hard to learn.</span>` : ""}`;
  }
  $("#td-gt").onchange = renderTdGt;
  $("#td-field").onchange = tdGtInfo;
  function tdTileHint() {
    const l = tdLayer(), px = l?.info?.res?.[0], T = +$("#td-tile").value, z = +$("#td-zoom").value;
    const n = l?.info?.width ? Math.ceil(l.info.width * z / T) * Math.ceil(l.info.height * z / T) : null;
    $("#td-tile-hint").innerHTML = (px && !/4326/.test(l.info.crs || "") ? `Each tile covers ${fmt(T / z * px, 0)} × ${fmt(T / z * px, 0)} m of the image (${fmt(px / z, 2)} m per model pixel). ` : "") +
      (n != null ? `About <b>${n}</b> tile${n === 1 ? "" : "s"} in the whole image${n < 4 ? ` <span style="color:var(--warn)">: at least 4 tiles with objects are needed; choose smaller tiles or a higher zoom</span>` : ""}.` : "");
  }
  ["#td-tile", "#td-zoom"].forEach((s) => $(s).addEventListener("change", tdTileHint));
  $("#td-tile").addEventListener("change", () => $("#td-tile").dataset.touched = "1");
  $("#td-size").onchange = () => prefs.set("td-size", $("#td-size").value);
  $("#td-folder").addEventListener("input", () => prefs.set("td-folder", $("#td-folder").value));
  $("#td-browse").onclick = async () => {
    const f = await pickFolder({ title: "Save the model in…", start: $("#td-folder").value || prefs.get("save-dir:last", ""), okLabel: "Use this folder" });
    if (f) { $("#td-folder").value = f; prefs.set("td-folder", f); }
  };
  $("#td-name").addEventListener("input", () => $("#td-name").dataset.touched = "1");
  function tdParams() {
    const out = {};
    $$('#td-basic [data-scope="td"], #td-adv [data-scope="td"]').forEach((i) => {
      out[i.dataset.p] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : +i.value) : i.value;
    });
    Object.keys(out).forEach((k) => out[k] === null && delete out[k]);
    return out;
  }
  $("#td-run").onclick = async () => {
    const err = $("#td-error"); err.classList.add("hidden");
    const l = tdLayer(), g = tdGt();
    if (!l) return toast("Choose the image", true);
    if (!g) return toast("Choose the layer with your labelled objects", true);
    const name = $("#td-name").value.trim() || "detector";
    const body = { input: { path: l.path, name: l.name, bands: rgbBody(rgbChosen($("#td-bands"))) },
                   ground_truth: { geojson: g.geojson, field: $("#td-field").value || null, point_size_m: +$("#td-point").value || 5 },
                   task: td.task, family: $("#td-family").value, size: $("#td-size").value, pretrained: $("#td-pre").checked,
                   tile_px: +$("#td-tile").value, zoom: +$("#td-zoom").value, overlap: +$("#td-overlap").value, clip: getClip("td-area"),
                   stretch: $("#td-stretch").value, params: tdParams(), class_colors: g.classColors || null, name, folder: $("#td-folder").value.trim() || null };
    const btn = $("#td-run"); btn.disabled = true;
    $("#td-result").classList.add("hidden");
    const live = $("#td-live"); live.classList.remove("hidden");
    live.innerHTML = `<div class="dl-live-head"><b>Training…</b><span class="hint">Making training tiles and loading the model</span></div>`;
    try {
      const job = await api("/api/det/train", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "traindet", title: `Training ${td.schema.tasks[td.task].title.toLowerCase()} · ${name}`, onPoll: (j) => j.live && renderTdLive(j.live) });
      if (done.live) renderTdLive(done.live, true);
      showTdResult(done.result);
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  // live curves: loss (left axis) and mAP (0–1, right axis) per epoch
  function renderTdLive(L, final = false) {
    const H = L.history || [];
    if (!H.length) return;
    const W = 460, Ht = 190, l = 38, r = 34, t = 10, b = 24;
    const n = Math.max(L.epochs || H.length, H.length), X = (e) => l + (e - 1) / Math.max(1, n - 1) * (W - l - r);
    const losses = H.map((h) => h.train_loss).filter((v) => v != null && isFinite(v));
    const lmax = Math.max(...losses, 1e-6) * 1.05;
    const YL = (v) => Ht - b - v / lmax * (Ht - b - t), YM = (v) => Ht - b - v * (Ht - b - t);
    const line = (key, Y, color, dash = "") => { const pts = H.filter((h) => h[key] != null).map((h) => `${X(h.epoch).toFixed(1)},${Y(h[key]).toFixed(1)}`).join(" "); return pts ? `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.8" ${dash ? `stroke-dasharray="${dash}"` : ""}/>` : ""; };
    const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => `<line x1="${l}" x2="${W - r}" y1="${YM(f)}" y2="${YM(f)}" class="grid"/><text x="${W - r + 4}" y="${YM(f) + 3}" class="tick">${f}</text><text x="${l - 4}" y="${YM(f) + 3}" class="tick" text-anchor="end">${fmt(f * lmax, 1)}</text>`).join("");
    const be = L.best_epoch ? `<line x1="${X(L.best_epoch)}" x2="${X(L.best_epoch)}" y1="${t}" y2="${Ht - b}" stroke="#16a34a" stroke-dasharray="3 3"/>` : "";
    const xt = [1, Math.ceil(n / 2), n].map((e) => `<text x="${X(e)}" y="${Ht - 8}" class="tick" text-anchor="middle">${e}</text>`).join("");
    const last = H[H.length - 1], best = H.find((h) => h.epoch === L.best_epoch);
    const pct = (v) => v == null ? "–" : fmt(100 * v, 1) + "%";
    const eta = L.eta_s != null && !final ? ` · ~${fmtSecs(L.eta_s)} left` : "";
    $("#td-live").innerHTML = `<div class="dl-live-head"><b>${final ? "Training curves" : `Epoch ${last.epoch} of ${L.epochs}`}</b><span class="hint">${esc(L.device || "")} · batch ${L.batch_size}${eta}</span></div>
      <svg viewBox="0 0 ${W} ${Ht}" class="dl-chart">${grid}${xt}${be}${line("train_loss", YL, "#2563eb")}${line("map50", YM, "#16a34a", "4 3")}${line("map", YM, "#7c3aed", "4 3")}</svg>
      <div class="dl-legend"><span style="color:#2563eb">■ training loss</span><span style="color:#16a34a">■ mAP50</span><span style="color:#7c3aed">■ mAP50-95</span><span class="hint" style="margin:0">loss on the left axis, mAP (validation) on the right · green line = best epoch</span></div>
      <table class="dl-epochs"><tr><th>Epoch</th><th>Loss</th><th>mAP50</th><th>mAP50-95</th><th>Precision</th><th>Recall</th><th>s</th></tr>
        ${H.slice(-6).reverse().map((h) => `<tr class="${h.epoch === L.best_epoch ? "best" : ""}"><td>${h.epoch}${h.epoch === L.best_epoch ? " ★" : ""}</td><td>${fmt(h.train_loss, 2)}</td><td>${pct(h.map50)}</td><td>${pct(h.map)}</td><td>${pct(h.precision)}</td><td>${pct(h.recall)}</td><td>${fmt(h.seconds, 0)}</td></tr>`).join("")}</table>
      ${best && !final ? `<p class="hint" style="margin:6px 0 0">Best so far: epoch ${best.epoch}, mAP50-95 ${pct(best.map)}. <b>Cancel</b> stops after this epoch and keeps the best model.</p>` : ""}`;
  }
  function showTdResult(r) {
    const c = r.config, v = c.val, pct = (x) => x == null ? "–" : fmt(100 * x, 1) + "%";
    const reportUrl = `/api/det/report?folder=${encodeURIComponent(r.folder)}`;
    const box = $("#td-result");
    box.innerHTML = `<div class="card rm-head-card">
        <div class="row between"><h2 style="margin:0">✓ Detection model trained</h2><span class="hint">${esc(c.arch_title)}</span></div>
        <div class="metric-tiles" style="margin-top:8px">
          <div class="metric"><b>${pct(v.map50)}</b><span>mAP50 ${tipBtn("Average precision when a detection must overlap the true object by at least 50 %, on the validation tiles.")}</span></div>
          <div class="metric"><b>${pct(v.map)}</b><span>mAP50-95 ${tipBtn("Average precision over stricter overlaps (50–95 %): how exact the boxes are.")}</span></div>
          <div class="metric"><b>${pct(v.precision)}</b><span>Precision</span></div><div class="metric"><b>${pct(v.recall)}</b><span>Recall</span></div></div>
        ${c.stopped ? `<p class="hint">Training ${esc(c.stopped)} after ${c.epochs_run} epochs; the best epoch (${c.best_epoch}) was kept.</p>` : ""}${stillImproving(c, "small models (n) usually need 30 or more")}
        <div class="dist" style="margin-top:8px">${c.classes.map((k, i) => { const pc = v.per_class[i]; return `<div style="grid-template-columns:minmax(0,1.6fr) 2fr auto"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(k.color || "#999")}"></i> ${esc(k.name)} <small>${pc.train_objects}/${pc.val_objects}</small></span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * (pc.ap50 || 0))}%;background:${esc(k.color || "")}"></span></span><b title="AP50">${pct(pc.ap50)}</b></div>`; }).join("")}</div>
        <p class="hint">Per class: AP50 on the validation tiles (objects for training / validation).</p>
        <p class="hint" style="word-break:break-all">${esc(r.folder)}</p>
        <div class="row tight" style="flex-wrap:wrap"><a class="btn primary" href="${reportUrl}" target="_blank" rel="noopener">Open report</a><a class="btn" href="${reportUrl}&download=true">Download report</a>
          <button class="btn" data-td-reveal>Show in folder</button><button class="btn" data-td-use>Detect objects with it</button></div>
      </div>`;
    box.classList.remove("hidden");
    $("[data-td-reveal]", box).onclick = () => api("/api/project/reveal", { method: "POST", json: { path: r.folder } }).catch((e) => toast(e, true));
    $("[data-td-use]", box).onclick = () => { od.model = "custom:" + r.folder; prefs.set("od-model", od.model); od.zoomTouched = false; switchTool("detect"); };
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
