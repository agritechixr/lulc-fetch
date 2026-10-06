/* Embeddings ▸ Train embedding model: one of the eleven light segmentation models (lulc_fetch/lightseg) on an embedding layer
   (all bands) and labels, in one go: 256 × 256 patches, training with early stopping and a report, then the class map.
   Needs the deep-learning add-on. Server: /api/emb/train. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "embtrain", menu: "embed", title: "Train embedding model", icon: "dl",
    subtitle: "Train one of eleven light segmentation models (TinyUNet, ENet, DABNet, LEDNet… 0.15–0.95 M parameters) on an embedding layer and your labelled polygons, points or class raster: all bands used, 256 × 256 patches, early stopping, a report, and the class map",
    kinds: ["embtrain"],
    save: [["embtrain", "#et-run", "the class map (the model stays in the project's models folder)"]],
    clip: { "et-area": { what: "layer is used for training and mapped" } },
    panel: `
      ${LF.html.addon()}
      <div class="dl-body">
      <div class="card">
        <h2>Embedding layer <span class="req">required</span> ${tip("Every band (all 64 AlphaEarth or 128 TESSERA dimensions, or any other embedding / image) goes into the model as it is. Run PCA first only if you want fewer. The layer needs a coordinate system in metres, as Download embeddings gives.")}</h2>
        <select id="et-layer"></select>
        <p class="hint" id="et-layer-info"></p>
      </div>
      <div class="card">
        <h2>Labels <span class="req">required</span> ${tip("Where you know the class: polygons or points you drew in Training samples (or a shapefile / GeoJSON) with a class field, or a class raster (one band of class numbers). Only labelled pixels are learned from; draw a few polygons per class, spread over the area.")}</h2>
        <select id="et-gt"></select>
        <label id="et-field-wrap">Class field<select id="et-field"></select></label>
        <p class="hint" id="et-gt-info"></p>
        ${LF.html.area("et-area", "Area", "Optional: train and map only part of the layer.")}
      </div>
      <div class="card">
        <h2>Model</h2>
        <div id="et-models"></div>
        <p class="hint" id="et-model-info"></p>
      </div>
      <div class="card">
        <h2>Training</h2>
        <div class="grid2">
          <label>Patch size ${tip("The embedding is cut into square patches of this many pixels (all bands) for training. 256 × 256 is the default; use 128 or 64 for small labelled areas (more patches), 512 for large ones.")}
            <select id="et-patch"><option value="64">64 × 64</option><option value="128">128 × 128</option><option value="256" selected>256 × 256 (recommended)</option><option value="512">512 × 512</option></select></label>
          <label>Patch overlap<select id="et-overlap"><option value="0">None</option><option value="0.25" selected>25 %</option><option value="0.5">50 % (small areas)</option></select></label>
          <label>Epochs ${tip("Passes over the training patches. Early stopping ends sooner when the validation score stops improving, keeping the best model.")}<input type="number" id="et-epochs" value="60" min="1" max="1000"></label>
          <label>Batch size<input type="number" id="et-batch" value="8" min="1" max="128"></label>
          <label>Learning rate<input type="number" id="et-lr" value="0.003" min="0.00001" max="0.1" step="0.0005"></label>
          <label>Validation %<input type="number" id="et-val" value="20" min="5" max="50"></label>
          <label>Stop after no gain for (epochs)<input type="number" id="et-patience" value="12" min="2" max="200"></label>
          ${LF.html.device("et-device")}
        </div>
        <label class="inline" style="margin-top:8px"><input type="checkbox" id="et-weights" checked> Rarer classes count more ${tip("Class weights: a class with few labelled pixels gets more weight in the loss, so the model doesn't ignore it.")}</label>
        <label class="inline" style="margin-top:4px"><input type="checkbox" id="et-augment" checked> Flip and rotate patches (more variety)</label>
        <p class="hint" id="et-est"></p>
      </div>
      <div class="card">
        <h2>Output</h2>
        <label>Model name<input id="et-name" maxlength="80" value="embedding_model"></label>
        <label class="inline" style="margin-top:8px"><input type="checkbox" id="et-map" checked> Map the layer (or the area) with the trained model</label>
        ${LF.html.run("et", "Train embedding model")}
      </div>
      <div id="et-result" class="hidden"></div>
      </div>`,

    setup(LF) {
      const { $, esc, fmt, prefs, api, toast, layers, getLayer, getClip, refreshClipPicker, renderAddon, modelPicker,
              runJob, runButton, showResult, addRasterFromPath, openTool, fillLayers, touched, autoName, limitDevices } = LF;
      const st = { schema: null, arch: prefs.get("et-arch", "light_dabnet") };
      // labels: polygons / points with a class field, or a one-band class raster
      const labelLayers = () => layers.filter((l) => (l.type === "vector" && l.geojson?.features?.length && l.id !== "aoi" && !l.ptFootprints)
        || (l.type === "raster" && l.path && (l.info?.count || 0) === 1));

      async function open() {
        if (!(await renderAddon($("#tab-embtrain")))) return;
        if (!st.schema) {
          try { st.schema = await api("/api/dl/schema"); } catch (e) { toast(e, true); return; }
          limitDevices($("#et-device"));
        }
        if (!st.schema.archs[st.arch]) st.arch = "light_dabnet";
        renderModels();
        renderLayers();
        refreshClipPicker("et-area");
      }
      function renderModels() {
        const archs = Object.entries(st.schema.archs).filter(([, a]) => a.lib === "light");
        modelPicker($("#et-models"), { value: st.arch, onChange: (k) => { st.arch = k; prefs.set("et-arch", k); renderModels(); setName(); },
          items: archs.map(([k, a]) => ({ id: k, title: a.title, group: "Light segmentation models",
            badge: k === "light_dabnet" ? "recommended" : k === "light_efsnet" ? "smallest" : k === "light_enet" ? "fastest" : "",
            meta: `<span title="Parameters">${a.params_m} M parameters</span>`, tip: a.desc })) });
        $("#et-model-info").textContent = st.schema.archs[st.arch]?.desc || "";
      }
      const setName = () => autoName($("#et-name"), LF.emb.name(getLayer($("#et-layer").value)?.name, st.arch.replace("light_", "")));
      function renderLayers() {
        if (!st.schema) return;
        const l = LF.emb.fill($("#et-layer"), "No embedding layer yet: Embeddings ▸ Download embeddings, or add a GeoTIFF");
        $("#et-layer-info").textContent = l ? `${l.info.count} bands, all used · ${l.info.width?.toLocaleString()} × ${l.info.height?.toLocaleString()} px${l.info.res ? ` · ${fmt(l.info.res[0], 0)} m pixels` : ""}` : "";
        fillLayers($("#et-gt"), labelLayers(), { label: (x) => `${x.name} · ${x.type === "vector" ? `${x.geojson.features.length} shapes` : "class raster"}`,
                                                 empty: "No labels yet: draw them with Tools ▸ Training samples, or add a shapefile / GeoJSON / class raster" });
        labelsChanged();
        setName();
      }
      function labelsChanged() {
        const g = getLayer($("#et-gt").value), f = $("#et-field");
        $("#et-field-wrap").classList.toggle("hidden", !g || g.type !== "vector");
        if (g?.type === "vector") {
          const keys = [...new Set(g.geojson.features.flatMap((x) => Object.keys(x.properties || {})))].filter((k) => !k.startsWith("_"));
          const cur = f.value;
          f.innerHTML = keys.map((k) => `<option>${esc(k)}</option>`).join("");
          f.value = keys.includes(cur) ? cur : keys.find((k) => /^(class|label|name|lulc|landcover|type)$/i.test(k)) || keys[0] || "";
          const vals = new Set(g.geojson.features.map((x) => x.properties?.[f.value]).filter((v) => v !== undefined && v !== null && v !== ""));
          $("#et-gt-info").textContent = `${g.geojson.features.length} shapes · ${vals.size} class${vals.size === 1 ? "" : "es"}${vals.size ? `: ${[...vals].slice(0, 8).join(", ")}${vals.size > 8 ? " …" : ""}` : ""}`;
        } else $("#et-gt-info").textContent = g ? "Band 1 holds the class numbers (0 = no label)." : "";
        estimate();
      }
      // how much ground one patch covers
      function estimate() {
        const l = getLayer($("#et-layer").value), px = +$("#et-patch").value;
        if (!l?.info?.res) { $("#et-est").textContent = ""; return; }
        const km = px * l.info.res[0] / 1000;
        $("#et-est").textContent = `A ${px} × ${px} patch covers ${fmt(km, km < 1 ? 2 : 1)} × ${fmt(km, km < 1 ? 2 : 1)} km here, with all ${l.info.count} bands. Small labelled areas: choose 128 or 64 and 50 % overlap for more patches.`;
      }
      $("#et-layer").onchange = renderLayers;
      $("#et-gt").onchange = labelsChanged;
      $("#et-field").onchange = labelsChanged;
      $("#et-patch").onchange = estimate;
      touched($("#et-name"));

      runButton("et", async () => {
        const l = getLayer($("#et-layer").value), g = getLayer($("#et-gt").value);
        if (!l) return toast("Choose the embedding layer", true);
        if (!g) return toast("Choose the labels", true);
        const gt = g.type === "vector" ? { type: "vector", geojson: g.geojson, field: $("#et-field").value } : { type: "raster", path: g.path, band: 1 };
        const name = $("#et-name").value.trim() || "embedding_model";
        const body = { path: l.path, ground_truth: gt, clip: getClip("et-area"), arch: st.arch, patch_px: +$("#et-patch").value, overlap: +$("#et-overlap").value,
          params: { epochs: +$("#et-epochs").value || 60, batch_size: +$("#et-batch").value || 8, lr: +$("#et-lr").value || 0.003, val_share: +$("#et-val").value || 20,
                    patience: +$("#et-patience").value || 12, early_stop: true, class_weights: $("#et-weights").checked ? "auto" : "none",
                    augment: $("#et-augment").checked ? ["flip", "rot90"] : [], device: $("#et-device").value },
          name, map: $("#et-map").checked, class_colors: g.classColors || null };
        const r = await runJob("/api/emb/train", body, { tool: "embtrain", title: `Training ${st.schema.archs[st.arch].title} · ${name}`, save: "embtrain" });
        if (r.map) await addRasterFromPath(r.map, { name: `${name} map`, zoom: false });
        const c = r.config || {}, v = c.test || c.val || {}, cls = c.classes || [];
        const pct = (x) => x == null ? "–" : `${fmt(100 * x, 1)} %`;
        const box = showResult("et", `<div class="card rm-head-card"><h2 style="margin:0">✓ ${esc(c.arch_title || "Model")} trained</h2>
          <div class="pca-sum">${r.patches} patches of ${r.patch_px} × ${r.patch_px} px × ${r.bands} bands · ${c.epochs_run ?? "?"} epochs (best ${c.best_epoch ?? "?"}) · ${c.test ? "test" : "validation"} scores below</div>
          <div class="metric-tiles" style="margin-top:8px;grid-template-columns:repeat(3,1fr)"><div class="metric"><b>${pct(v.accuracy)}</b><span>Accuracy</span></div><div class="metric"><b>${pct(v.miou)}</b><span>mIoU</span></div>
            <div class="metric"><b>${v.kappa == null ? "–" : fmt(v.kappa, 2)}</b><span>Kappa</span></div></div>
          ${(v.iou || []).length ? `<div class="dist" style="margin-top:8px">${v.iou.map((x, i) => `<div style="grid-template-columns:minmax(0,1.6fr) 2fr auto"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(cls[i]?.color || "#999")}"></i> ${esc(cls[i]?.name ?? i)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(1, 100 * x)}%;background:${esc(cls[i]?.color || "")}"></span></span><b>IoU ${fmt(100 * x, 0)}</b></div>`).join("")}</div>` : ""}
          <div class="row tight" style="margin-top:10px;flex-wrap:wrap;gap:6px">${r.report ? `<a class="btn small" href="/api/dl/report?folder=${encodeURIComponent(r.model_folder)}" target="_blank" rel="noopener">Open the report</a>` : ""}
            <button class="btn small" data-et-use>Classify another layer with it…</button></div>
          ${LF.stillImproving(c)}
          <p class="hint">${r.map ? "The class map was added to Contents (band 2 = confidence). " : ""}The model is in <code>${esc(r.model_folder)}</code>; the patches in <code>${esc(r.dataset)}</code>.</p></div>`);
        $("[data-et-use]", box).onclick = () => openTool("embpredict", { model: r.model_folder });
      });

      return { open, layersChanged: renderLayers, clipChanged: estimate };
    },
  });
})();
