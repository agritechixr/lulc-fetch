  // Detect object: pretrained detectors, SAM, or your own models.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Detect object (pretrained detectors, SAM, or your own models)
  const od = { schema: null, model: prefs.get("od-model", null), layerId: null, classes: null, custom: [], classKey: null };
  const OD_PRESETS = {
    coco: [["All", null], ["Vehicles", ["car", "truck", "bus", "motorcycle", "bicycle", "train"]], ["Boats & planes", ["boat", "airplane"]],
           ["People", ["person"]], ["Animals", ["cow", "sheep", "horse", "elephant", "giraffe", "zebra", "bird"]]],
    dota: [["All", null], ["Vehicles", ["small vehicle", "large vehicle"]], ["Ships & harbours", ["ship", "harbor"]], ["Aircraft", ["plane", "helicopter"]],
           ["Infrastructure", ["storage tank", "bridge", "roundabout", "swimming pool"]],
           ["Sports fields", ["baseball diamond", "tennis court", "basketball court", "ground track field", "soccer ball field"]]],
  };
  const odRasters = () => layers.filter((l) => l.type === "raster" && !l.derived && l.path);
  const odLayer = () => getLayer($("#od-layer").value);
  const odCustom = () => od.model.startsWith("custom:") ? od.custom.find((m) => "custom:" + m.folder === od.model) : null;
  const odSpec = () => odCustom() ? { family: "custom", outlines: odCustom().task !== "detect", size: odCustom().tile_px } : od.schema?.models[od.model];
  async function refreshOd() {
    if (!(await renderAddon($("#tab-detect")))) return;
    if (!od.schema) {
      od.schema = await api("/api/detect/schema");
      [...$("#od-device").options].forEach((o) => { if (o.value !== "auto" && !dlx.status.devices.includes(o.value)) o.disabled = true; });
    }
    try { od.custom = await api("/api/det/models"); } catch { od.custom = []; }
    if (!od.model) od.model = dlx.status.yolo?.available ? "yolo_obb" : "fasterrcnn_v2";
    renderOdModels();
    renderOdLayers();
  }
  // the class list depends on the model: COCO, DOTA, your model's classes, or none (SAM)
  function odClassList() {
    const m = odCustom();
    if (m) return { key: "custom:" + m.folder, all: m.classes.map((c) => c.name), first: [], colors: Object.fromEntries(m.classes.map((c) => [c.name, c.color])), presets: [["All", null]] };
    const spec = odSpec(), sc = od.schema;
    if (!spec?.classes) return null;
    const all = sc.classes[spec.classes];
    return { key: spec.classes, all, first: spec.classes === "coco" ? sc.aerial : all, colors: sc.colors, presets: OD_PRESETS[spec.classes] || [["All", null]] };
  }
  function renderOdClasses() {
    const cl = odClassList();
    $("#od-classes-card").classList.toggle("hidden", !cl);
    if (!cl) return;
    if (od.classKey !== cl.key) {   // another class list: restore what was picked for it last time
      od.classKey = cl.key;
      od.classes = prefs.get(`od-classes:${cl.key}`, null);
      if (od.classes) od.classes = od.classes.filter((c) => cl.all.includes(c));
      if (!od.classes?.length) od.classes = null;
      const box = (c) => `<label><input type="checkbox" data-odc="${esc(c)}"><i style="background:${esc(cl.colors[c] || "#999")}"></i>${esc(c)}</label>`;
      $("#od-classes").innerHTML = cl.first.map(box).join("");
      const rest = cl.all.filter((c) => !cl.first.includes(c));
      $("#od-classes-more").innerHTML = rest.map(box).join("");
      $("#od-classes-more").closest("details").classList.toggle("hidden", !rest.length);
      if (!cl.first.length) { $("#od-classes").innerHTML = $("#od-classes-more").innerHTML; $("#od-classes-more").innerHTML = ""; $("#od-classes-more").closest("details").classList.add("hidden"); }
      $("#od-presets").innerHTML = cl.presets.map(([t], i) => `<button class="chip" data-odp="${i}">${esc(t)}</button>`).join("");
      $$("[data-odp]").forEach((b) => b.onclick = () => { od.classes = cl.presets[+b.dataset.odp][1]; syncOdClasses(); });
      $$("[data-odc]").forEach((c) => c.onchange = () => {
        const on = $$("[data-odc]").filter((x) => x.checked).map((x) => x.dataset.odc);
        od.classes = on.length && on.length < cl.all.length ? on : null;
        syncOdClasses();
      });
    }
    syncOdClasses();
  }
  function syncOdClasses() {
    const cl = odClassList();
    if (!cl) return;
    const set = od.classes ? new Set(od.classes) : null;
    $$("[data-odc]").forEach((c) => c.checked = !set || set.has(c.dataset.odc));
    $$("[data-odp]").forEach((b) => { const p = cl.presets[+b.dataset.odp][1]; b.classList.toggle("active", JSON.stringify(p) === JSON.stringify(od.classes)); });
    $("#od-cls-hint").textContent = set ? `Looking for ${od.classes.length} class${od.classes.length > 1 ? "es" : ""}: ${od.classes.join(", ")}.` : `Looking for all ${cl.all.length} classes.`;
    prefs.set(`od-classes:${cl.key}`, od.classes);
  }
  function renderOdModels() {
    const ms = od.schema.models;
    if (!ms[od.model] && !odCustom()) od.model = dlx.status.yolo?.available ? "yolo_obb" : "fasterrcnn_v2";
    const ys = dlx.status?.yolo?.available, groups = { tv: "torchvision · COCO (PyTorch add-on)", yolo: "YOLO26 · ultralytics", sam: "Segment Anything" };
    const needs = (fam) => fam !== "tv" && !ys ? "needs the YOLO & SAM add-on" : "";
    const items = Object.entries(ms).map(([k, m]) => ({ id: k, title: m.title, group: groups[m.family] + (needs(m.family) ? " (add-on not installed)" : ""),
        badge: k === "yolo_obb" ? "aerial" : k === "yolo_detect" ? "recommended" : "", meta: starMeta(m.accuracy, m.speed, 5),
        tip: `${m.desc}${m.backbone ? ` Backbone: ${m.backbone}.` : ""}${m.mb ? ` Download ${m.mb} MB, once.` : ""}` }));
    items.sort((a, b) => (a.group.startsWith("YOLO") ? 0 : a.group.startsWith("torch") ? 1 : 2) - (b.group.startsWith("YOLO") ? 0 : b.group.startsWith("torch") ? 1 : 2));
    od.custom.forEach((m) => items.push({ id: "custom:" + m.folder, title: m.name, group: "Your trained models", badge: m.task === "obb" ? "rotated" : m.task === "segment" ? "outlines" : "",
      meta: m.map50 != null ? `<span title="Validation mAP50">mAP50 <b>${fmt(100 * m.map50, 1)}%</b></span>` : "", tip: `${m.arch || ""} · ${m.classes.length} classes: ${m.classes.map((c) => c.name).join(", ")} · trained ${m.trained || ""}` }));
    modelPicker($("#od-models"), { value: od.model, onChange: (k) => { od.model = k; prefs.set("od-model", k); renderOdModels(); odLayerChanged(true); }, items });
    const m = odCustom(), spec = odSpec(), sc = od.schema;
    // model size (YOLO n–x, SAM t–l)
    const sizes = !m && spec.sizes ? [...spec.sizes] : [];
    $("#od-size-wrap").classList.toggle("hidden", !sizes.length);
    if (sizes.length) {
      const cur = prefs.get(`od-size:${od.model}`, spec.default_size);
      $("#od-size").innerHTML = sizes.map((z) => `<option value="${z}">${esc(spec.family === "sam" ? sc.sam_sizes[z] : `${z} · ${sc.sizes[z]}`)}</option>`).join("");
      $("#od-size").value = sizes.includes(cur) ? cur : spec.default_size;
    }
    $("#od-model-info").innerHTML = m
      ? `Your model · ${esc(m.arch || "")} · ${m.classes.length} classes · tiles of ${m.tile_px} px${m.pixel_size ? ` · learned at ${fmt(m.pixel_size, 2)} m per model pixel (Zoom Auto matches it)` : ""}${m.has_report ? ` · <a href="/api/det/report?folder=${encodeURIComponent(m.folder)}" target="_blank" rel="noopener">report</a>` : ""} · <a href="#" data-od-forget>remove from list</a>`
      : `${esc(spec.desc)}${spec.backbone ? ` ${esc(spec.backbone)} backbone.` : ""} The model looks at tiles of ${spec.size} px.`;
    $("[data-od-forget]", $("#od-model-info"))?.addEventListener("click", async (e) => {
      e.preventDefault();
      await api(`/api/det/models?folder=${encodeURIComponent(m.folder)}`, { method: "DELETE" }).catch((x) => toast(x.message, true));
      od.model = "yolo_obb"; prefs.set("od-model", od.model); refreshOd();
    });
    // SAM outlines: only for box models
    $("#od-sam-wrap").classList.toggle("hidden", !!spec.outlines);
    // auto zoom only for your models
    const zAuto = $("#od-zoom option[value=auto]");
    zAuto.classList.toggle("hidden", !m); zAuto.disabled = !m;
    if (m && !od.zoomTouched) $("#od-zoom").value = m.pixel_size ? "auto" : "1";
    if (!m && $("#od-zoom").value === "auto") $("#od-zoom").value = "1";
    const needAddon = !!m || spec.family !== "tv" || !!$("#od-sam").value;
    if (needAddon) renderYoloAddon($("#od-yolo"), spec.family === "sam" ? "SAM 2.1" : "This model", () => refreshOd());
    else $("#od-yolo").classList.add("hidden");
    renderOdClasses();
    odEstimate();
  }
  $("#od-size").onchange = () => { prefs.set(`od-size:${od.model}`, $("#od-size").value); odEstimate(); };
  $("#od-sam")?.addEventListener("change", odEstimate);
  $("#od-sam").onchange = () => renderOdModels();
  $("#od-zoom").addEventListener("change", () => od.zoomTouched = true);
  $("#od-browse").onclick = async () => {
    const f = await pickFolder({ title: "Choose a detection model folder (made with Train detection model)", start: prefs.get("od-last", ""), okLabel: "Use this model" });
    if (!f) return;
    try { const r = await api("/api/det/models/add", { method: "POST", json: { folder: f } }); prefs.set("od-last", f); od.model = "custom:" + r.folder; prefs.set("od-model", od.model); refreshOd(); }
    catch (e) { toast(e.message, true); }
  };
  function renderOdLayers() {
    const rasters = odRasters(), sel = $("#od-layer"), cur = sel.value || od.layerId;
    sel.innerHTML = rasters.length ? rasters.slice().reverse().map((l) => `<option value="${esc(l.id)}">${esc(l.name)} · ${l.info?.count ?? "?"} bands</option>`).join("")
      : `<option value="">Add a high-resolution image to Contents first (+ Add data)</option>`;
    if (rasters.some((l) => l.id === cur)) sel.value = cur;
    odLayerChanged();
  }
  function odLayerChanged(modelChanged = false) {
    const l = odLayer();
    if (!l) { $("#od-bands").innerHTML = ""; $("#od-check").textContent = ""; return; }
    if (od.layerId !== l.id) {   // a new image: its own default bands and stretch
      od.layerId = l.id;
      renderRgbPickers($("#od-bands"), l, odCheck);
      $("#od-stretch").value = defaultStretch(l);
      if (!$("#od-name").dataset.touched) $("#od-name").value = `${l.name}_objects`.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60);
    }
    const m = odCustom();
    if (m && modelChanged && m.stretch) $("#od-stretch").value = m.stretch;   // your model: the stretch it was trained with
    odCheck();
    odEstimate();
  }
  function odCheck() {
    const l = odLayer(), m = odCustom(), spec = odSpec();
    const b = rgbChosen($("#od-bands"));
    const px = l?.info?.res?.[0];
    const geo = l?.info?.crs && /4326/.test(l.info.crs);
    const msgs = [];
    if (b.length && new Set(b).size === 1) msgs.push("One band in all three colours: the image is used as greyscale.");
    if (m && m.bands && b.length) {
      const names = (l.info?.bands || []).map((x) => x.description);
      const chosen = b.map((i) => names[i - 1]);
      if (chosen.join() !== m.bands.join()) msgs.push(`<span style="color:var(--warn)">⚠ The model was trained on bands ${esc(m.bands.join(", "))}; you chose ${esc(chosen.join(", "))}.</span>`);
    }
    if (px && !geo && px >= 5 && spec?.family !== "sam") msgs.push(`<span style="color:var(--warn)">⚠ ${fmt(px, 1)} m pixels: vehicles, boats, people… are smaller than a pixel here, so detectors will find little. They need about 0.1–1 m pixels (aerial, drone, very-high-resolution satellite).</span>`);
    else if (px && !geo && !m) msgs.push(`${fmt(px, 2)} m pixels${px > 0.6 && spec?.classes === "coco" ? ": small objects like cars are only a few pixels wide, try Zoom 2× or 3×" : ""}.`);
    $("#od-check").innerHTML = msgs.join(" ");
  }
  function odEstimate() {
    const l = odLayer(), spec = odSpec(), m = odCustom();
    if (!l || !spec || !l.info?.width) { $("#od-est").textContent = ""; return; }
    let zoom = $("#od-zoom").value === "auto" ? (m?.pixel_size && l.info.res ? l.info.res[0] / m.pixel_size : 1) : +$("#od-zoom").value;
    const size = m ? m.tile_px : spec.size;
    const T = Math.max(64, Math.round(size / zoom)), S = T - Math.round(T * +$("#od-overlap").value);
    const clip = getClip("od-area");
    let w = l.info.width, h = l.info.height;
    if (clip && l.info.bounds) {   // rough share of the image covered by the area's bounding box
      const [[y0, x0], [y1, x1]] = l.info.bounds, cs = (clip.type === "Polygon" ? clip.coordinates : clip.coordinates.flat()).flat();
      w *= Math.min(1, (Math.max(...cs.map((c) => c[0])) - Math.min(...cs.map((c) => c[0]))) / ((x1 - x0) || 1));
      h *= Math.min(1, (Math.max(...cs.map((c) => c[1])) - Math.min(...cs.map((c) => c[1]))) / ((y1 - y0) || 1));
    }
    const n = Math.max(1, Math.ceil(Math.max(w - (T - S), 1) / S)) * Math.max(1, Math.ceil(Math.max(h - (T - S), 1) / S));
    // rough time: seconds per tile measured on an Apple M-series GPU (CPU only: about 3× longer)
    const sz = $("#od-size").value;
    const perTile = spec.family === "sam" ? { t: 12, s: 19, b: 20, l: 24 }[sz] || 15
      : spec.family === "yolo" ? ({ n: 0.05, s: 0.06, m: 0.1, l: 0.3, x: 0.6 }[sz] || 0.1) * (spec.size > 640 ? 2 : 1)
      : m ? 0.1 : 0.3;
    const refine = $("#od-sam")?.value && spec.family !== "sam" ? { t: 1, s: 1.2, b: 1.5, l: 2 }[$("#od-sam").value] : 0;
    const secs = (n * (perTile + refine) + 3) * (dlx.status?.device === "cpu" ? 3 : 1);
    const time = secs < 60 ? "under a minute" : secs < 3600 ? `about ${Math.round(secs / 60)} min` : `about ${fmt(secs / 3600, 1)} h`;
    $("#od-est").innerHTML = `About ${n.toLocaleString()} tile${n > 1 ? "s" : ""} of ${T} × ${T} image pixels${$("#od-zoom").value === "auto" ? ` (zoom ${fmt(zoom, 2)}×)` : ""} · ` +
      (secs > 180 ? `<span style="color:var(--warn)"><b>${time}</b>${spec.family === "sam" ? ": segment everything is slow; try a smaller area or the tiny size first" : ": try a smaller area or model first"}</span>` : `<b>${time}</b>`) + ".";
  }
  $("#od-layer").onchange = () => odLayerChanged();
  ["#od-zoom", "#od-overlap"].forEach((s) => $(s).addEventListener("change", odEstimate));
  $("#od-name").addEventListener("input", () => $("#od-name").dataset.touched = "1");
  $("#od-run").onclick = async () => {
    const err = $("#od-error"); err.classList.add("hidden");
    const l = odLayer();
    if (!l) return toast("Choose the image to search", true);
    const m = odCustom(), spec = odSpec();
    const name = $("#od-name").value.trim() || "objects";
    const body = { input: { path: l.path, name: l.name, bands: rgbBody(rgbChosen($("#od-bands"))) },
                   model: m ? "custom" : od.model, custom: m ? m.folder : null, size: !m && spec.sizes ? $("#od-size").value : null,
                   sam_refine: !spec.outlines ? ($("#od-sam").value || null) : null,
                   clip: getClip("od-area"), classes: odClassList() ? od.classes : null, score: +$("#od-score").value || 0.4,
                   zoom: $("#od-zoom").value === "auto" ? "auto" : +$("#od-zoom").value,
                   overlap: +$("#od-overlap").value, nms_iou: +$("#od-iou").value || 0.5, stretch: $("#od-stretch").value,
                   batch_size: +$("#od-batch").value || 2, device: $("#od-device").value, max_size_m: +$("#od-maxsize").value || null, name };
    const btn = $("#od-run"); btn.disabled = true; $("#od-result").classList.add("hidden");
    try {
      const job = await api("/api/detect/run", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "detect", title: `Detecting objects with ${m ? m.name : spec.title}`, save: "detect" });
      const r = done.result;
      const box = $("#od-result");
      if (r.count) {
        const vl = addVectorLayer(r.geojson, name, { color: r.classes[0]?.color || "#e6194b", weight: 2, fillOpacity: 0.12, path: r.path });
        vl.classColors = Object.fromEntries(r.classes.map((c) => [c.name, c.color]));
        vl.classes = r.classes.map((c) => ({ name: c.name, color: c.color }));
        renderContents(); saveLayers();
      }
      const max = Math.max(1, ...r.classes.map((c) => c.count));
      box.innerHTML = `<div class="card rm-head-card"><h2 style="margin:0">${r.count ? `✓ ${r.count.toLocaleString()} object${r.count > 1 ? "s" : ""} found` : "No objects found"}</h2>
        <div class="pca-sum">${esc(r.model)} · ${r.tiles.toLocaleString()} tiles of ${r.tile} px (${r.overlap} px overlap${r.zoom !== 1 ? `, zoom ${fmt(r.zoom, 2)}×` : ""}) · ${esc(r.device)} · ${r.seconds} s${r.too_big ? ` · ${r.too_big} larger than ${fmt(body.max_size_m)} m left out` : ""}</div>
        ${r.count ? `<div class="dist" style="margin-top:8px">${r.classes.map((c) => `<div style="grid-template-columns:minmax(0,1.6fr) 2fr auto"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(c.color)}"></i> ${esc(c.name)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(1, 100 * c.count / max)}%;background:${esc(c.color)}"></span></span><b>${c.count.toLocaleString()}</b></div>`).join("")}</div>
        <p class="hint">Added to Contents as a vector layer of ${r.outlines ? "outlines" : "boxes"}: every object has its class, score${r.count && r.geojson.features[0]?.properties.area_m2 != null ? ", width, height and area" : ""} (open its attribute table). Save it with Export data (Shapefile, GeoJSON, KML).</p>`
        : `<p class="hint">Try a lower minimum score, another zoom (higher if objects are small in the image), or other classes. Pretrained models need high-resolution images where objects are clearly visible.</p>`}</div>`;
      box.classList.remove("hidden");
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
