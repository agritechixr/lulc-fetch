/* Analysis ▸ Tools ▸ Vector: Buffer, Select by attribute, Overlay (intersection, union, difference, symmetric
   difference, clip) and Dissolve, on the vector layers in Contents. Each runs as a job (progress, History, Workflows,
   the Assistant); its result, a GeoJSON file in analysis/, is added to Contents.
   Server: /api/vector/buffer, query, overlay, dissolve, read · lulc_fetch/geoprocess.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const layerSel = (id, label) => `<label>${label} <select id="${id}"></select></label>`;
  const nameRow = (id, val) => `<label>Name of the result <input type="text" id="${id}" value="${val}" maxlength="80"></label>`;

  LF.tool({ id: "vbuffer", title: "Buffer", icon: "vbuffer", kinds: ["vbuffer"],
    subtitle: "Grow points, lines or polygons by a distance in metres (or shrink polygons with a negative one); optionally merge the result into one shape",
    panel: `<div class="card"><h2>Buffer ${tip("A zone of the given distance around each shape (measured in metres on the ground). A negative distance shrinks polygons.")}</h2>
      ${layerSel("vb-layer", "Layer")}
      <label>Distance (m) <input type="number" id="vb-dist" value="100" step="any"></label>
      <label class="check"><input type="checkbox" id="vb-diss"> Merge the zones into one shape (dissolve)</label>
      ${nameRow("vb-name", "buffer")}
      <button class="btn primary" id="vb-run">Buffer</button><p class="hint hidden" id="vb-error" style="color:var(--err)"></p><div id="vb-result" class="hidden"></div></div>`,
    setup: (LF) => wire(LF, "vb", "/api/vector/buffer", (v) => ({ layer: v.layer("vb-layer"), distance: +LF.$("#vb-dist").value, dissolve: LF.$("#vb-diss").checked })),
  });

  LF.tool({ id: "vquery", title: "Select by attribute", icon: "vquery", kinds: ["vquery"],
    subtitle: "The features whose attributes meet a condition, as a new layer: crop == \"rice\" and area_ha > 2 · name in (\"A\", \"B\") · contains(name, \"farm\")",
    panel: `<div class="card"><h2>Select by attribute ${tip("Write a condition on the layer's fields: == != < <= > >=, and / or / not, in (…), contains(field, \"text\"), startswith(…). SQL style works too: crop = 'rice' AND area > 2. Text compares without case.")}</h2>
      ${layerSel("vq-layer", "Layer")}
      <div class="vq-fields" id="vq-fields"></div>
      <label>Condition <textarea id="vq-where" rows="2" placeholder='e.g. crop == "rice" and area_ha > 2'></textarea></label>
      ${nameRow("vq-name", "selection")}
      <button class="btn primary" id="vq-run">Select</button><p class="hint hidden" id="vq-error" style="color:var(--err)"></p><div id="vq-result" class="hidden"></div></div>`,
    setup(LF) {
      const hooks = wire(LF, "vq", "/api/vector/query", (v) => ({ layer: v.layer("vq-layer"), where: LF.$("#vq-where").value.trim() }));
      const fieldsOf = () => {   // the fields of the chosen layer, as buttons that write themselves into the condition
        const l = LF.getLayer(LF.$("#vq-layer").value), props = (l?.geojson?.features || []).slice(0, 200).map((f) => f.properties || {});
        const keys = [...new Set(props.flatMap(Object.keys))].slice(0, 30);
        LF.$("#vq-fields").innerHTML = keys.length ? `<span class="hint" style="margin:0">Fields: </span>${keys.map((k) => `<button type="button" class="as-chip" data-f="${LF.esc(k)}">${LF.esc(k)}</button>`).join("")}` : "";
        LF.$$("#vq-fields [data-f]").forEach((b) => b.onclick = () => { const ta = LF.$("#vq-where"); ta.value = `${ta.value}${ta.value && !/\s$/.test(ta.value) ? " " : ""}${b.dataset.f} `; ta.focus(); });
      };
      LF.$("#vq-layer").addEventListener("change", fieldsOf);
      return { ...hooks, open(arg) { hooks.open(arg); fieldsOf(); }, layersChanged() { hooks.layersChanged(); fieldsOf(); } };
    },
  });

  LF.tool({ id: "voverlay", title: "Overlay", icon: "voverlay", kinds: ["voverlay"],
    subtitle: "Two polygon layers together: intersection (where both are), union (every piece of both), difference (A without B), symmetric difference, clip (A cut to B)",
    panel: `<div class="card"><h2>Overlay ${tip("Intersection keeps where A and B overlap, with the attributes of both. Union keeps every piece of both. Difference: A without B. Symmetric difference: in one but not both. Clip: A cut to B, A's attributes only.")}</h2>
      ${layerSel("vo-a", "Layer A")}${layerSel("vo-b", "Layer B")}
      <label>Overlay <select id="vo-how"><option value="intersection">Intersection (A and B)</option><option value="union">Union (A or B, in pieces)</option>
        <option value="difference">Difference (A without B)</option><option value="symmetric_difference">Symmetric difference (only one of them)</option><option value="clip">Clip (A cut to B)</option></select></label>
      ${nameRow("vo-name", "overlay")}
      <button class="btn primary" id="vo-run">Overlay</button><p class="hint hidden" id="vo-error" style="color:var(--err)"></p><div id="vo-result" class="hidden"></div></div>`,
    setup: (LF) => wire(LF, "vo", "/api/vector/overlay", (v) => {
      if (LF.$("#vo-a").value === LF.$("#vo-b").value) throw new Error("Choose two different layers");
      return { a: v.layer("vo-a"), b: v.layer("vo-b"), how: LF.$("#vo-how").value };
    }, ["vo-a", "vo-b"]),
  });

  LF.tool({ id: "vdissolve", title: "Dissolve", icon: "vdissolve", kinds: ["vdissolve"],
    subtitle: "Merge shapes: all into one, or one shape per value of a field (e.g. one per crop)",
    panel: `<div class="card"><h2>Dissolve</h2>
      ${layerSel("vd-layer", "Layer")}
      <label>One shape per value of <select id="vd-field"><option value="">(all into one)</option></select></label>
      ${nameRow("vd-name", "dissolved")}
      <button class="btn primary" id="vd-run">Dissolve</button><p class="hint hidden" id="vd-error" style="color:var(--err)"></p><div id="vd-result" class="hidden"></div></div>`,
    setup(LF) {
      const hooks = wire(LF, "vd", "/api/vector/dissolve", (v) => ({ layer: v.layer("vd-layer"), field: LF.$("#vd-field").value || null }));
      const fields = () => {
        const l = LF.getLayer(LF.$("#vd-layer").value), keys = [...new Set((l?.geojson?.features || []).slice(0, 200).flatMap((f) => Object.keys(f.properties || {})))];
        const was = LF.$("#vd-field").value;   // kept when Contents changes (e.g. the result was just added)
        LF.$("#vd-field").innerHTML = `<option value="">(all into one)</option>` + keys.map((k) => `<option>${LF.esc(k)}</option>`).join("");
        if (keys.includes(was)) LF.$("#vd-field").value = was;
      };
      LF.$("#vd-layer").addEventListener("change", fields);
      return { ...hooks, open(arg) { hooks.open(arg); fields(); }, layersChanged() { hooks.layersChanged(); fields(); } };
    },
  });

  // the shared wiring: layer pickers, the Run button (a job), the result added to Contents
  function wire(LF, p, endpoint, body, selects = [`${p}-layer`]) {
    const { $, api, layers, getLayer, fillLayers, runButton, trackJob, addVectorLayer, showResult, esc } = LF;
    const vectors = () => layers.filter((l) => l.type === "vector" && l.geojson?.features?.length);
    const fill = (pick) => selects.forEach((s, i) => fillLayers($(`#${s}`), vectors(), { empty: "No vector layer in Contents", pick: i === 0 ? pick : undefined }));
    const v = { layer: (s) => { const l = getLayer($(`#${s}`).value); if (!l) throw new Error("Choose a vector layer (add one with Insert ▸ Add data)"); return l.geojson; } };
    runButton(p, async () => {
      const job = await api(endpoint, { method: "POST", json: { ...body(v), name: $(`#${p}-name`).value.trim() || "result" } });
      const done = await trackJob(job, { title: job.title });
      const r = done.result, fc = await api(`/api/vector/read?path=${encodeURIComponent(r.path)}`);
      if (fc.features?.length) addVectorLayer(fc, r.name, { path: r.path });
      showResult(p, `<b>${r.features.toLocaleString()} feature${r.features === 1 ? "" : "s"}</b> in “${esc(r.name)}”, added to Contents${r.features ? "" : " (nothing matched)"}. <span class="hint">Saved as <code>${esc(r.path)}</code></span>`);
    });
    return { open(arg) { fill(arg?.layer); }, layersChanged() { fill(); } };
  }
})();
