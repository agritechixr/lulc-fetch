/* Agri ▸ Diagnose crop disease: leaf photos → crop (detected or chosen) → its ConvNeXt model's top 3 diseases. Unclear photos
   are refused; photos with GPS become a point layer. Needs the deep-learning add-on; the models come from Hugging Face or a
   local folder. Server: /api/agri/schema, models, photos/*, diagnose. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "agridisease", menu: "agri", title: "Diagnose crop disease", icon: "leaf",
    subtitle: "Find the disease on leaf photos of 42 crops (apple, mango, rice, tomato, maize…): the crop is recognised, then its ConvNeXt model gives the top 3 diseases. Unclear photos are refused; photos with GPS become a disease map",
    kinds: ["diagnose"],
    panel: `
      ${LF.html.addon()}
      <div class="dl-body">
      <div class="card">
        <h2>Disease models ${tip("The photo models of the Multi-Crop Disease Decision Support System: one ConvNeXt model per crop (42 crops) and two crop detectors. Each one downloads from Hugging Face the first time it's needed (about 95 MB per crop) and is kept, or use your own copy of the disease app (data/<Crop>/convnext_best.pth and master_model/).")}</h2>
        <div id="ad-models"></div>
      </div>
      <div class="card">
        <h2>Leaf photos <span class="req">required</span> ${tip("One leaf per photo, filling most of the picture, in daylight and in focus. JPG, PNG, WebP, BMP or TIFF (HEIC from iPhones needs pillow-heif: save as JPG instead). Photos taken with location on (GPS) are also put on the map.")}</h2>
        <div class="row tight" style="flex-wrap:wrap;gap:6px">
          <button class="btn small" id="ad-add">Add photos…</button>
          <button class="btn small" id="ad-folder">Add a folder…</button>
          <label class="inline" style="margin:0;font-size:12px"><input type="checkbox" id="ad-recursive"> with sub-folders</label>
          <button class="btn small ghost hidden" id="ad-clear" style="margin-left:auto">Clear all</button>
        </div>
        <input type="file" id="ad-file" accept="image/*,.heic,.heif" multiple hidden>
        <div class="ad-drop" id="ad-drop">Drop leaf photos here</div>
        <div class="ad-grid" id="ad-photos"></div>
        <p class="hint" id="ad-photos-hint"></p>
      </div>
      <div class="card">
        <h2>Crop</h2>
        <div id="ad-crop"></div>
        <p class="hint" id="ad-crop-info"></p>
        <label class="inline" style="margin-top:8px"><input type="checkbox" id="ad-strict" checked> Refuse unclear photos ${tip("As in the disease app: photos that are too dark, blank, too small, or not confidently a leaf of a supported crop get 'retake' instead of a guess. Blurry or very bright photos are refused only when the models are unsure. Untick to diagnose every photo anyway (doubtful results are marked).")}</label>
        <div class="grid2">${LF.html.device("ad-device")}</div>
      </div>
      <div class="card">
        <h2>Output</h2>
        <label>Name ${tip("The results table (CSV, one row per photo) and, for photos with a GPS position, a point layer get this name.")}<input id="ad-name" maxlength="80" value="diagnosis"></label>
        ${LF.html.run("ad", "Diagnose photos")}
      </div>
      <div id="ad-result" class="hidden"></div>
      <p class="hint">Photo diagnosis supports, but doesn't replace, a local agriculture expert. Field photos can score lower than the test results shown.</p>
      </div>`,

    setup(LF) {
      const { $, $$, esc, prefs, api, toast, status, map, layers, dataItems, addItem, openItem, addVectorLayer, saveLayers, renderAddon,
              tipBtn, searchPicker, pickFolder, runJob, runButton, showResult, openTool, limitDevices } = LF;
      const pct = LF.agri.pct;
      const st = { schema: null, photos: prefs.get("ad-photos", []), result: null, showAll: false, crop: prefs.get("ad-crop", "auto") };
      const STATUS = { disease: ["Disease", "c2"], healthy: ["Healthy", "c0"], variety: ["Variety", "c0"], retake: ["Retake photo", "c1"], no_model: ["No model", "c1"], error: ["Error", "c2"] };
      const REASON = { too_small: "Photo too small (under 96 pixels): take it closer", too_dark: "Too dark: take it in daylight", too_bright: "Too bright: avoid direct sun glare",
        no_detail: "No leaf to see (blank or plain surface)", blurry: "Blurry: hold still and tap the leaf to focus", not_leaf: "Not confidently a leaf of a supported crop: one leaf, filling the photo",
        low_confidence: "The disease model is unsure: try a closer, sharper photo of the affected part", unreadable: "Couldn't read the photo",
        crop_check: "Check the crop: leaves of related crops look alike, and the crop detectors can mix them up" };
      const COLORS = { disease: "#dc2626", healthy: "#16a34a", variety: "#16a34a", retake: "#d97706", no_model: "#64748b", error: "#64748b" };
      const thumb = (path, size = 160) => `/api/agri/photo?path=${encodeURIComponent(path)}&size=${size}`;
      const cropName = (c) => st.schema?.crops[c]?.name || c;

      async function open() {
        if (!(await renderAddon($("#tab-agridisease")))) return;
        try { st.schema = await LF.agri.schema(true); } catch (e) { toast(e, true); return; }
        limitDevices($("#ad-device"));
        renderModels();
        renderCrops();
        renderPhotos();
      }

      // ---------------- where the models come from: Hugging Face or a local folder
      function renderModels() {
        const m = st.schema.models, n = m.crops.length, total = Object.keys(st.schema.crops).length, d = st.schema.detectors;
        const box = $("#ad-models");
        if (m.source === "hub") {
          const got = m.downloaded.filter((x) => !x.startsWith("detector:")).length;
          box.innerHTML = `<div class="ad-models-ok"><b>✓ All ${total} crops</b> · from <a href="${esc(m.url)}" target="_blank" rel="noopener">Hugging Face</a> ${tipBtn("Each model downloads the first time it's needed (about 95 MB per crop, 190 MB for the two crop detectors) and is kept for next time.")}</div>
            <p class="hint" style="margin-top:4px">${got || m.downloaded.length ? `Downloaded so far: ${got} crop model${got === 1 ? "" : "s"}${m.downloaded.some((x) => x.startsWith("detector:")) ? " and the crop detectors" : ""} (${m.downloaded_mb.toLocaleString()} MB), kept for next time.` : "Nothing downloaded yet: the first diagnosis needs the internet."}
              · <a href="#" data-ad-pick style="white-space:nowrap">use a local models folder…</a></p>`;
        } else if (!m.folder) {
          box.innerHTML = `<div class="warn" style="margin-top:0">Choose the folder that holds the disease models: your copy of the disease app
              (<code>multicrop-disease-decision-support</code>) with <code>data/&lt;Crop&gt;/convnext_best.pth</code> and <code>master_model/</code>.
              Or download them from Hugging Face instead (about 95 MB per crop, each the first time it's needed).</div>
            <div class="row tight" style="margin-top:8px;gap:6px;flex-wrap:wrap"><button class="btn primary" data-ad-hub>Download from Hugging Face</button><button class="btn" data-ad-pick>Choose the models folder…</button></div>`;
        } else {
          const missing = Object.keys(st.schema.crops).filter((c) => !m.crops.includes(c));
          const dets = [m.detectors.includes("original") && `crop detector (${d.original.crops} crops, ${pct(d.original.accuracy)} on test photos)`,
                        m.detectors.includes("new") && `added-crops detector (${d.new.crops} crops, ${pct(d.new.accuracy)})`].filter(Boolean);
          box.innerHTML = `<div class="ad-models-ok"><b>${n === total ? "✓" : "⚠"} ${n} of ${total} crop models</b> · ${dets.length ? `${dets.length} crop detector${dets.length > 1 ? "s" : ""} ${tipBtn(dets.join(" + "))}` : `<span style="color:var(--warn)">no crop detector: choose the crop below</span>`}</div>
            <p class="hint" style="margin-top:4px"><span style="overflow-wrap:anywhere">${esc(m.folder)}</span>${m.chosen ? "" : " (found automatically)"} · <a href="#" data-ad-pick style="white-space:nowrap">change…</a> · <a href="#" data-ad-hub style="white-space:nowrap">download from Hugging Face instead</a></p>
            ${missing.length && n ? `<p class="hint">No model yet for: ${missing.map((c) => esc(cropName(c))).join(", ")}.</p>` : ""}`;
        }
        const setModels = async (json, msg) => {
          try { st.schema.models = await api("/api/agri/models", { method: "POST", json }); renderModels(); renderCrops(); toast(msg); }
          catch (err) { toast(err, true); }
        };
        $$("[data-ad-pick]", box).forEach((b) => b.onclick = async (e) => {
          e.preventDefault();
          const f = await pickFolder({ title: "Choose the disease models folder (the disease app's folder)", start: m.folder || "", okLabel: "Use this folder" });
          if (f) setModels({ folder: f }, "Models folder set");
        });
        $("[data-ad-hub]", box)?.addEventListener("click", (e) => { e.preventDefault(); setModels({ source: "hub" }, "Models will download from Hugging Face when needed"); });
      }

      // ---------------- the crop: detected in each photo, or one for all (type a few letters to find it)
      function renderCrops() {
        const sc = st.schema, have = new Set(sc.models.crops);
        const crops = Object.entries(sc.crops).sort((a, b) => a[1].name.localeCompare(b[1].name));
        const canDetect = sc.models.detectors.length > 0;
        const items = [{ id: "auto", title: "Detect the crop in each photo", sub: canDetect ? "recommended when photos are of several crops" : "the models folder has no crop detector",
                         group: "Automatic", disabled: !canDetect },
          ...crops.map(([k, c]) => ({ id: k, title: c.name, aliases: c.aliases, keywords: c.labels, group: "Or every photo is of one crop",
                                      sub: !have.has(k) ? "no model in the models folder"
                                        : sc.models.source === "hub" && !sc.models.downloaded.includes(k) ? [c.aliases.slice(0, 2).join(", "), "downloads 95 MB once"].filter(Boolean).join(" · ")
                                        : [c.aliases.slice(0, 3).join(", "), `${c.labels.length} classes`].filter(Boolean).join(" · "),
                                      disabled: !have.has(k) }))];
        if (!items.some((it) => it.id === st.crop && !it.disabled)) st.crop = canDetect ? "auto" : (items.find((it) => !it.disabled) || {}).id || "";
        searchPicker($("#ad-crop"), { items, value: st.crop, placeholder: "Type a crop: e.g. tom, paddy, aloo…", empty: "No crop matches",
                                      onChange: (k) => { st.crop = k; cropInfo(); } });
        cropInfo();
      }
      function cropInfo() {
        const sc = st.schema, c = st.crop, info = $("#ad-crop-info");
        if (c === "auto") {
          const d = sc.detectors;
          info.innerHTML = `The crop detectors recognise ${d.original.crops} crops (${pct(d.original.accuracy)} correct on ${d.original.test_images?.toLocaleString()} test photos) and ${d.new.crops} more (${pct(d.new.accuracy)}). ` +
            `${esc(sc.recognised_only.join(", "))} leaves are recognised but not diagnosed. Choose the crop if all photos are of one crop: it's faster and avoids crop mix-ups.`;
        } else if (!sc.crops[c]) {
          info.textContent = "Choose the models folder first.";
        } else {
          const k = sc.crops[c];
          info.innerHTML = `${k.kind === "variety" ? "This model tells <b>varieties</b>, not diseases. " : ""}${k.labels.length} classes: ${k.labels.map(esc).join(", ")}. ` +
            `Test accuracy ${pct(k.accuracy)} on ${k.test_images?.toLocaleString() || "?"} photos.`;
        }
        prefs.set("ad-crop", c);
      }

      // ---------------- the photos (remembered in this browser)
      function savePhotos() { prefs.set("ad-photos", st.photos.slice(0, 3000)); }
      function addPhotos(list) {
        const seen = new Set(st.photos.map((p) => p.path));
        const fresh = list.filter((p) => !seen.has(p.path));
        st.photos.push(...fresh);
        savePhotos();
        renderPhotos();
        return fresh.length;
      }
      function renderPhotos() {
        const grid = $("#ad-photos"), n = st.photos.length, SHOW = 48;
        const done = Object.fromEntries((st.result?.photos || []).map((r) => [r.path, r.status]));
        grid.innerHTML = st.photos.slice(0, SHOW).map((p, i) => `<figure class="ad-thumb" title="${esc(p.name)}">
            <img loading="lazy" src="${thumb(p.path)}" alt="${esc(p.name)}">
            ${done[p.path] ? `<i class="ad-dot" style="background:${COLORS[done[p.path]]}"></i>` : ""}
            <button type="button" class="ad-x" data-ad-rm="${i}" title="Remove from the list" aria-label="Remove ${esc(p.name)}">×</button></figure>`).join("") +
          (n > SHOW ? `<div class="ad-more">+${(n - SHOW).toLocaleString()} more</div>` : "");
        $$("[data-ad-rm]", grid).forEach((b) => b.onclick = () => { st.photos.splice(+b.dataset.adRm, 1); savePhotos(); renderPhotos(); });
        $("#ad-drop").classList.toggle("small", n > 0);
        $("#ad-clear").classList.toggle("hidden", !n);
        $("#ad-photos-hint").textContent = n ? `${n.toLocaleString()} photo${n > 1 ? "s" : ""}` : "";
        $("#ad-run").textContent = n > 1 ? `Diagnose ${n.toLocaleString()} photos` : "Diagnose photo";
      }
      async function uploadPhotos(files) {
        files = [...files].filter((f) => /\.(jpe?g|png|bmp|webp|tiff?|heic|heif)$/i.test(f.name));
        if (!files.length) return toast("Choose photos (JPG, PNG, WebP, BMP or TIFF)", true);
        let added = 0;
        for (let i = 0; i < files.length; i += 20) {
          status(`Adding photos… ${i} of ${files.length}`, true);
          const fd = new FormData();
          files.slice(i, i + 20).forEach((f) => fd.append("files", f));
          try { added += addPhotos((await api("/api/agri/photos/upload", { method: "POST", body: fd })).photos); }
          catch (e) { toast(e, true); break; }
        }
        status(`Added ${added} photo${added === 1 ? "" : "s"}`);
      }
      $("#ad-add").onclick = () => $("#ad-file").click();
      $("#ad-file").onchange = (e) => { uploadPhotos(e.target.files); e.target.value = ""; };
      $("#ad-folder").onclick = async () => {
        const f = await pickFolder({ title: "Choose a folder of leaf photos", start: prefs.get("ad-last-folder", ""), okLabel: "Add its photos" });
        if (!f) return;
        prefs.set("ad-last-folder", f);
        try {
          const r = await api(`/api/agri/photos/folder?path=${encodeURIComponent(f)}&recursive=${$("#ad-recursive").checked}`);
          if (!r.photos.length) return toast(`No photos in ${r.folder}${$("#ad-recursive").checked ? "" : " (tick “with sub-folders” to look deeper)"}`, true);
          const n = addPhotos(r.photos);
          toast(`Added ${n} photo${n === 1 ? "" : "s"}${r.truncated ? " (the first 5,000)" : ""}`);
        } catch (e) { toast(e, true); }
      };
      $("#ad-clear").onclick = () => { st.photos = []; savePhotos(); renderPhotos(); };
      const drop = $("#ad-drop");
      drop.onclick = () => $("#ad-file").click();
      drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
      drop.addEventListener("dragleave", () => drop.classList.remove("over"));
      drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadPhotos(e.dataTransfer.files); });

      // ---------------- diagnose
      runButton("ad", async () => {
        if (!st.photos.length) return toast("Add leaf photos first", true);
        if (!st.schema?.models.folder) return toast("Choose the disease models folder first", true);
        const name = $("#ad-name").value.trim() || "diagnosis";
        if (!st.crop) return toast("Choose the crop", true);
        const body = { photos: st.photos.map((p) => p.path), crop: st.crop, strict: $("#ad-strict").checked, device: $("#ad-device").value, name };
        const r = await runJob("/api/agri/diagnose", body, { tool: "agridisease", title: `Diagnosing ${body.photos.length} photo${body.photos.length > 1 ? "s" : ""}` });
        st.result = { ...r, name };
        st.showAll = false;
        if (r.geojson) {   // photos with GPS: a point layer coloured by status
          const fc = r.geojson;
          fc.features.forEach((f) => f.properties.class = STATUS[f.properties.status]?.[0] || f.properties.status);
          const classes = [...new Set(fc.features.map((f) => f.properties.status))].map((s) => ({ name: STATUS[s]?.[0] || s, color: COLORS[s] || "#64748b" }));
          addVectorLayer(fc, name, { color: classes[0].color, weight: 1.5, fillOpacity: 0.85, path: r.geojson_path, classes,
                                     classColors: Object.fromEntries(classes.map((c) => [c.name, c.color])) });
          saveLayers();
        }
        addItem({ kind: "table", name: `${name}.csv`, path: r.csv });
        renderResult();
        renderPhotos();
      });

      // recount after a photo was diagnosed again (same rules as the server)
      function recount() {
        const r = st.result, sum = {};
        r.counts = Object.fromEntries(["disease", "healthy", "variety", "retake", "no_model", "error"].map((s) => [s, r.photos.filter((p) => p.status === s).length]));
        r.photos.filter((p) => ["disease", "healthy", "variety"].includes(p.status)).forEach((p) => {
          const k = JSON.stringify([p.crop_name, p.diagnosis, p.status]); sum[k] = (sum[k] || 0) + 1; });
        r.summary = Object.entries(sum).map(([k, n]) => { const [crop, diagnosis, status] = JSON.parse(k); return { crop, diagnosis, status, count: n }; })
          .sort((a, b) => b.count - a.count || a.crop.localeCompare(b.crop));
      }
      // a photo's crop was wrong: diagnose it again with the crop the user chose (only that photo)
      async function recrop(i, crop, btn) {
        const p = st.result.photos[i];
        btn.disabled = true; btn.textContent = "Diagnosing…";
        try {
          const r = await runJob("/api/agri/diagnose", { photos: [p.path], crop, strict: $("#ad-strict").checked, device: $("#ad-device").value,
                                                         name: `${st.result.name}_${p.file.replace(/\.[^.]+$/, "")}_as_${crop}` },
                                 { tool: "agridisease", title: `Diagnosing ${p.file} as ${cropName(crop)}` });
          st.result.photos[i] = { ...r.photos[0], rechecked: true };
          recount();
          renderResult(i);
        } catch (e) { btn.disabled = false; btn.textContent = "Diagnose again"; if (LF.notCancelled(e)) toast(e, true); }
      }

      function renderResult(focus = null) {
        const r = st.result, c = r.counts, n = r.photos.length;
        const parts = [c.disease && `<b>${c.disease}</b> diseased`, c.healthy && `<b>${c.healthy}</b> healthy`, c.variety && `<b>${c.variety}</b> variety`,
                       c.retake && `<b>${c.retake}</b> to retake`, c.no_model && `<b>${c.no_model}</b> without a model`, c.error && `<b>${c.error}</b> unreadable`].filter(Boolean);
        const max = Math.max(1, ...r.summary.map((s) => s.count));
        const LIMIT = 30, list = st.showAll ? r.photos : r.photos.slice(0, LIMIT);
        const box = showResult("ad", `<div class="card rm-head-card">
            <h2 style="margin:0">✓ ${n.toLocaleString()} photo${n > 1 ? "s" : ""} checked</h2>
            <div class="pca-sum">${parts.join(" · ")} · ${esc(r.device)} · ${r.seconds} s</div>
            ${r.summary.length ? `<div class="home-label" style="margin-top:10px">Diagnoses</div><div class="dist">${r.summary.map((s) => `<div style="grid-template-columns:minmax(0,2fr) minmax(0,1.3fr) auto">
                <span>${esc(s.crop)} · <b>${esc(s.diagnosis)}</b></span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * s.count / max)}%;background:${COLORS[s.status]}"></span></span><b>${s.count}</b></div>`).join("")}</div>` : ""}
            <div class="row tight" style="margin-top:10px;flex-wrap:wrap;gap:6px">
              <button class="btn small" data-ad-table>Open results table</button>
              ${r.located ? `<button class="btn small" data-ad-zoom>Zoom to the ${r.located} photo${r.located > 1 ? "s" : ""} on the map</button>` : ""}
              <button class="btn small" data-ad-reveal>Show in folder</button>
            </div>
            <p class="hint">${r.located ? `${r.located} of ${n} photos had a GPS position and are on the map (red diseased, green healthy, orange retake). ` : "No photo had a GPS position, so nothing was put on the map. "}The table (${esc(r.csv.split("/").pop())}) has one row per photo with the top 3 diagnoses and confidences.</p>
          </div>
          ${list.map((p, i) => card(p, i)).join("")}
          ${!st.showAll && n > LIMIT ? `<button class="btn" style="width:100%" data-ad-all>Show all ${n.toLocaleString()} photos</button>` : ""}`);
        $("[data-ad-table]", box).onclick = () => { const it = dataItems.find((d) => d.path === r.csv); it ? openItem(it) : addItem({ kind: "table", name: `${r.name}.csv`, path: r.csv }, { open: true }); };
        $("[data-ad-zoom]", box)?.addEventListener("click", () => { const l = layers.find((x) => x.path === r.geojson_path); if (l?.leaflet) map.fitBounds(l.leaflet.getBounds(), { maxZoom: 17, padding: [30, 30] }); });
        $("[data-ad-reveal]", box).onclick = () => api("/api/project/reveal", { method: "POST", json: { path: (r.outputs?.[0] || r.csv).replace(/[\\/][^\\/]+$/, "") } }).catch((e) => toast(e, true));
        $("[data-ad-all]", box)?.addEventListener("click", () => { st.showAll = true; renderResult(); });
        $$("[data-ad-guide]", box).forEach((a) => a.onclick = (e) => { e.preventDefault(); openTool("agriguide", { crop: a.dataset.crop, disease: a.dataset.adGuide }); });
        $$("[data-ad-big]", box).forEach((im) => im.onclick = () => window.open(thumb(im.dataset.adBig, 0), "_blank", "noopener"));
        $$("[data-ad-recrop]", box).forEach((w) => $("button", w).onclick = (e) => recrop(+w.dataset.adRecrop, $("select", w).value, e.currentTarget));
        if (focus != null) $(`[data-ad-card="${focus}"]`, box)?.scrollIntoView({ behavior: "smooth", block: "center" });
        else box.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      // one photo: its status, crop, top 3 diagnoses, why it was refused, a link to the guide
      function card(p, i) {
        const [stText, stCls] = STATUS[p.status] || [p.status, "c1"];
        const diag = ["disease", "healthy", "variety"].includes(p.status);
        const reasons = [p.reason, ...(p.warnings || [])].filter(Boolean).map((x) => REASON[x] || x);
        const top = (p.top || []).map((t, i) => `<div class="ad-bar"><span>${i ? esc(t.name) : `<b>${esc(t.name)}</b>`}</span><span class="rb-track"><span class="rb-fill" style="width:${Math.max(1, 100 * t.conf)}%;${i ? "opacity:.45" : ""}"></span></span><span>${pct(t.conf)}</span></div>`).join("");
        const crop = p.crop_name ? `${esc(p.crop_name)}${p.crop_conf != null ? ` <small>(${pct(p.crop_conf)} sure${p.crop_top?.[1] && p.crop_top[1].conf > 0.1 ? `; or ${esc(p.crop_top[1].name)} ${pct(p.crop_top[1].conf)}` : ""})</small>` : ""}` : "";
        // "Wrong crop?": the likely alternatives first, then every crop that has a model
        const have = new Set(st.schema?.models.crops || []), alts = (p.crop_alternatives || []).filter((a) => have.has(a.crop));
        const others = Object.keys(st.schema?.crops || {}).filter((c) => have.has(c) && c !== p.crop && !alts.some((a) => a.crop === c))
          .sort((a, b) => cropName(a).localeCompare(cropName(b)));
        const recrop = p.crop && p.status !== "error" ? `<div class="ad-recrop" data-ad-recrop="${i}">${p.rechecked ? "✓ Diagnosed again as this crop · " : ""}Wrong crop?
            <select>${alts.length ? `<optgroup label="Likely">${alts.map((a) => `<option value="${esc(a.crop)}">${esc(a.name)}</option>`).join("")}</optgroup>` : ""}
              <optgroup label="${alts.length ? "Other crops" : "Crops"}">${others.map((c) => `<option value="${esc(c)}">${esc(cropName(c))}</option>`).join("")}</optgroup></select>
            <button type="button" class="btn small">Diagnose again</button></div>` : "";
        return `<div class="card ad-card" data-ad-card="${i}">
          <img class="ad-photo" src="${thumb(p.path, 240)}" alt="" data-ad-big="${esc(p.path)}" title="Open the full photo">
          <div class="ad-body">
            <div class="ad-file"><span class="pill ${stCls}">${stText}</span> <span title="${esc(p.path)}">${esc(p.file)}</span>${p.lat != null ? ` <span class="ad-gps" title="${p.lat}, ${p.lon}${p.taken ? ` · ${esc(p.taken)}` : ""}">📍</span>` : ""}</div>
            ${crop ? `<div class="ad-crop">${crop}</div>` : ""}
            ${diag ? `<div class="ad-diag">${esc(p.diagnosis)}</div>` : ""}
            ${top ? `<div class="ad-bars">${top}</div>` : ""}
            ${reasons.length ? `<div class="hint" style="color:var(--warn)">${reasons.map(esc).join(" · ")}</div>` : ""}
            ${p.note && !reasons.length ? `<div class="hint">${esc(p.note)}</div>` : ""}
            ${recrop}
            ${diag && p.status !== "variety" ? `<a href="#" class="small" data-ad-guide="${esc(p.diagnosis)}" data-crop="${esc(p.crop)}">${p.status === "healthy" ? `About ${esc(p.crop_name)} in the guide →` : `Symptoms &amp; treatment of ${esc(p.diagnosis)} →`}</a>` : ""}
          </div></div>`;
      }

      return { open };
    },
  });
})();
