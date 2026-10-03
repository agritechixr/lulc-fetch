/* Embeddings ▸ Classify with embedding model: map an embedding layer (any area or year) with a model from Train embedding
   model, with a confidence band. Needs the deep-learning add-on. Server: /api/dl/models, /api/dl/predict. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "embpredict", menu: "embed", title: "Classify with embedding model", icon: "dlmap",
    subtitle: "Map any embedding layer (another area or year) with a model from Train embedding model, with a confidence band",
    save: [["embpredict", "#ep-run", "the class map"]],
    clip: { "ep-area": { what: "layer is classified" } },
    panel: `
      ${LF.html.addon()}
      <div class="dl-body">
      <div class="card">
        <h2>Model <span class="req">required</span> ${tip("Models trained with Train embedding model (light models), with their number of bands and validation score. Add a model folder trained elsewhere with “Add a model…”.")}</h2>
        <select id="ep-model"></select>
        <p class="hint" id="ep-model-info"></p>
        <div class="row tight" style="margin-top:6px"><button class="btn small" id="ep-add">Add a model…</button></div>
      </div>
      <div class="card">
        <h2>Embedding layer <span class="req">required</span> ${tip("The layer to map: the same kind of embedding the model was trained on (same number of bands, e.g. AlphaEarth for an AlphaEarth model), for any area or year.")}</h2>
        <select id="ep-layer"></select>
        <p class="hint" id="ep-check"></p>
        ${LF.html.area("ep-area")}
      </div>
      <div class="card">
        <h2>Output</h2>
        <div class="grid2">
          ${LF.html.device("ep-device")}
          <label>Tile overlap<select id="ep-overlap"><option value="0">None</option><option value="0.25" selected>25 % (seamless)</option><option value="0.5">50 %</option></select></label>
        </div>
        <label class="inline" style="margin-top:8px"><input type="checkbox" id="ep-conf" checked> Add a confidence band</label>
        <label>Layer name<input id="ep-name" maxlength="80" value="embedding_map"></label>
        ${LF.html.run("ep", "Classify")}
      </div>
      <div id="ep-result" class="hidden"></div>
      </div>`,

    setup(LF) {
      const { $, esc, fmt, api, toast, getLayer, getClip, refreshClipPicker, renderAddon, pickFolder, runJob, runButton, showResult,
              addRasterFromPath, touched, autoName, limitDevices } = LF;
      const st = { models: [], ready: false };
      const model = () => st.models.find((m) => m.folder === $("#ep-model").value);

      async function open(arg) {
        if (!(await renderAddon($("#tab-embpredict")))) return;
        limitDevices($("#ep-device"));
        try { st.models = (await api("/api/dl/models")).filter((m) => (m.arch_key || "").startsWith("light_")); } catch (e) { toast(e.message, true); return; }
        st.ready = true;
        const sel = $("#ep-model"), cur = arg?.model || sel.value;
        sel.innerHTML = st.models.length ? st.models.map((m) => `<option value="${esc(m.folder)}">${esc(m.name)} · ${esc(m.arch)} · ${m.in_channels} bands${m.miou != null ? ` · mIoU ${fmt(100 * m.miou, 0)} %` : ""}</option>`).join("")
          : `<option value="">No embedding model yet: train one with Embeddings ▸ Train embedding model</option>`;
        if (st.models.some((m) => m.folder === cur)) sel.value = cur;
        refreshClipPicker("ep-area");
        renderLayers();
      }
      // the layers, with the ones that match the model's number of bands first in line
      function renderLayers() {
        if (!st.ready) return;
        const m = model(), sel = $("#ep-layer"), kept = LF.emb.layers().some((l) => l.id === sel.value);
        LF.emb.fill(sel, "No embedding layer yet", { label: (l) => `${l.name} · ${l.info.count} bands${m && l.info.count !== m.in_channels ? " (doesn't match the model)" : ""}` });
        if (!kept) { const ok = LF.emb.layers().reverse().find((l) => m && l.info.count === m.in_channels); if (ok) sel.value = ok.id; }
        const l = getLayer(sel.value);
        $("#ep-model-info").innerHTML = m ? `${esc(m.arch)} · ${m.in_channels} bands · classes: ${(m.classes || []).map((c) => esc(c.name)).join(", ")}${m.has_report ? ` · <a href="/api/dl/report?folder=${encodeURIComponent(m.folder)}" target="_blank" rel="noopener">report</a>` : ""}` : "";
        $("#ep-check").innerHTML = m && l && l.info.count !== m.in_channels ? `<span style="color:var(--err)">The model needs ${m.in_channels} bands; this layer has ${l.info.count}. Use the same kind of embedding it was trained on.</span>` : "";
        if (m) autoName($("#ep-name"), `${m.name}_map`);
        $("#ep-run").disabled = !m || !l || l.info.count !== m.in_channels;
      }
      $("#ep-model").onchange = renderLayers;
      $("#ep-layer").onchange = renderLayers;
      touched($("#ep-name"));
      $("#ep-add").onclick = async () => {
        const f = await pickFolder({ title: "Choose a model folder (made with Train embedding model)", okLabel: "Use this model" });
        if (!f) return;
        try { await api("/api/dl/models/add", { method: "POST", json: { folder: f } }); open({ model: f }); } catch (e) { toast(e.message, true); }
      };

      runButton("ep", async () => {
        const m = model(), l = getLayer($("#ep-layer").value);
        if (!m || !l) return toast("Choose the model and the embedding layer", true);
        const body = { model: m.folder, inputs: [{ path: l.path, name: l.name }], clip: getClip("ep-area"), overlap: +$("#ep-overlap").value, batch_size: 8,
                       device: $("#ep-device").value, confidence: $("#ep-conf").checked, name: $("#ep-name").value.trim() || "embedding_map" };
        const r = await runJob("/api/dl/predict", body, { tool: "embpredict", title: `Classifying with ${m.name}`, save: "embpredict" });
        await addRasterFromPath(r.path, { name: body.name, zoom: false });
        showResult("ep", `<div class="card rm-head-card"><h2 style="margin:0">✓ Map ready</h2>
          <div class="pca-sum">${r.width.toLocaleString()} × ${r.height.toLocaleString()} px · ${esc(r.device)} · ${r.seconds} s</div>
          <div class="dist" style="margin-top:8px">${r.classes.map((c) => `<div style="grid-template-columns:minmax(0,1.6fr) 2fr auto"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(c.color || "#999")}"></i> ${esc(c.name)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(1, c.pct)}%;background:${esc(c.color || "")}"></span></span><b>${fmt(c.pct, 1)}%</b></div>`).join("")}</div>
          <p class="hint">Added to Contents${body.confidence ? " (band 2 = confidence %)" : ""}.</p></div>`);
      });

      return { open, layersChanged: renderLayers };
    },
  });
})();
