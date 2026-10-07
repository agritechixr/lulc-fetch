/* Analysis ▸ Tools ▸ Raster & terrain: Terrain (slope, aspect, hillshade), Contours, Reclassify, Change detection and
   Clip raster. Jobs (progress, History, Workflows, the Assistant); results are added to Contents.
   Server: /api/raster/terrain, contours, reclassify, change, clip · lulc_fetch/raster_ops.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const rasterSel = (id, label) => `<label>${label} <select id="${id}"></select></label>`;
  const runRow = (p, label) => `<button class="btn primary" id="${p}-run">${label}</button><p class="hint hidden" id="${p}-error" style="color:var(--err)"></p><div id="${p}-result" class="hidden"></div>`;

  // shared: raster pickers, the Run button as a job, every result into Contents
  function wire(LF, p, endpoint, body, selects, describe) {
    const { $, api, layers, getLayer, fillLayers, runButton, trackJob, addRasterFromPath, addVectorLayer, addItem, showResult, esc } = LF;
    const rasters = () => layers.filter((l) => l.type === "raster" && l.path);
    const fill = (pick) => selects.forEach((s, i) => fillLayers($(`#${s}`), rasters(), { empty: "No raster layer in Contents", pick: i === 0 ? pick : undefined }));
    const v = { raster: (s) => { const l = getLayer($(`#${s}`).value); if (!l) throw new Error("Choose a raster (add one with Insert ▸ Add data)"); return l.path; } };
    runButton(p, async () => {
      const job = await api(endpoint, { method: "POST", json: body(v) });
      const r = (await trackJob(job, { title: job.title })).result;
      const files = [...(r.outputs || []), ...(r.path ? [r.path] : [])];
      for (const f of files) {
        if (/\.tiff?$/i.test(f)) await addRasterFromPath(f, { zoom: false }).catch(() => {});
        else if (/\.geojson$/i.test(f)) { const fc = await api(`/api/vector/read?path=${encodeURIComponent(f)}`); if (fc.features?.length) addVectorLayer(fc, r.name || "result", { path: f, zoom: false }); }
      }
      if (r.csv) addItem({ kind: "table", name: r.csv.split("/").pop(), path: r.csv });
      showResult(p, `${describe ? describe(r) : ""} <span class="hint">${files.length + (r.csv ? 1 : 0)} result${files.length + (r.csv ? 1 : 0) === 1 ? "" : "s"} added to Contents.</span>`);
    });
    return { open(arg) { fill(arg?.layer); }, layersChanged() { fill(); } };
  }

  LF.tool({ id: "rterrain", title: "Terrain: slope, aspect, hillshade", icon: "rterrain", kinds: ["terrain"],
    subtitle: "From a DEM: slope in degrees, aspect (the direction a slope faces, degrees from north) and a shaded relief",
    panel: `<div class="card"><h2>Terrain ${tip("Computed from a DEM (heights in metres). The pixel size is taken in metres, also for DEMs in degrees.")}</h2>
      ${rasterSel("rtr-dem", "DEM")}
      <div class="vz-stats"><label class="check"><input type="checkbox" value="slope" checked> Slope</label><label class="check"><input type="checkbox" value="aspect" checked> Aspect</label><label class="check"><input type="checkbox" value="hillshade" checked> Hillshade</label></div>
      <details><summary class="hint">Hillshade light and exaggeration</summary>
        <label>Sun from (° from north) <input type="number" id="rtr-az" value="315" min="0" max="360"></label>
        <label>Sun height (°) <input type="number" id="rtr-alt" value="45" min="1" max="90"></label>
        <label>Height × <input type="number" id="rtr-z" value="1" min="0.01" step="any"></label></details>
      ${runRow("rtr", "Compute")}</div>`,
    setup: (LF) => wire(LF, "rtr", "/api/raster/terrain", (v) => ({ dem: v.raster("rtr-dem"), products: LF.$$("#tab-rterrain .vz-stats input:checked").map((c) => c.value),
      azimuth: +LF.$("#rtr-az").value, altitude: +LF.$("#rtr-alt").value, z_factor: +LF.$("#rtr-z").value || 1 }), ["rtr-dem"]),
  });

  LF.tool({ id: "rcontours", title: "Contours", icon: "rcontours", kinds: ["contours"],
    subtitle: "Contour lines of a DEM every few metres, as a vector layer with each line's height",
    panel: `<div class="card"><h2>Contours</h2>${rasterSel("rc-dem", "DEM")}
      <label>Every (m) <input type="number" id="rc-int" value="10" min="0.01" step="any"></label>
      <label>Name of the result <input type="text" id="rc-name" value="contours" maxlength="80"></label>
      ${runRow("rc", "Make contours")}</div>`,
    setup: (LF) => wire(LF, "rc", "/api/raster/contours", (v) => ({ dem: v.raster("rc-dem"), interval: +LF.$("#rc-int").value, name: LF.$("#rc-name").value.trim() || "contours" }),
      ["rc-dem"], (r) => `<b>${r.features.toLocaleString()} lines.</b>`),
  });

  LF.tool({ id: "rreclass", title: "Reclassify", icon: "rreclass", kinds: ["reclassify"],
    subtitle: "Turn ranges of values into classes, e.g. NDVI < 0.2 bare, 0.2–0.5 sparse, above 0.5 dense vegetation",
    panel: `<div class="card"><h2>Reclassify ${tip("Each range is from (inclusive) to (exclusive); an empty from or to means no limit. Values in no range become empty.")}</h2>
      ${rasterSel("rr-raster", "Raster")}
      <table class="rr-rules"><thead><tr><th>From</th><th>To</th><th>Class name</th><th></th></tr></thead><tbody id="rr-rules"></tbody></table>
      <div class="row tight" style="gap:6px;margin:4px 0 8px"><button class="btn small" id="rr-add">+ Range</button><button class="btn small ghost" id="rr-ndvi">NDVI example</button><button class="btn small ghost" id="rr-even" title="Equal ranges between the raster's min and max">5 equal ranges</button></div>
      <label>Name of the result <input type="text" id="rr-name" value="classes" maxlength="80"></label>
      ${runRow("rr", "Reclassify")}</div>`,
    setup(LF) {
      const { $, $$, esc, getLayer } = LF;
      const row = (r = {}) => `<tr><td><input type="number" step="any" data-k="min" value="${r.min ?? ""}"></td><td><input type="number" step="any" data-k="max" value="${r.max ?? ""}"></td>
        <td><input type="text" data-k="label" value="${esc(r.label || "")}"></td><td><button class="np-copy" data-del title="Remove">×</button></td></tr>`;
      const set = (rules) => { $("#rr-rules").innerHTML = rules.map(row).join(""); $$("#rr-rules [data-del]").forEach((b) => b.onclick = () => b.closest("tr").remove()); };
      set([{ max: 0.2, label: "Bare / water" }, { min: 0.2, max: 0.5, label: "Sparse vegetation" }, { min: 0.5, label: "Dense vegetation" }]);
      $("#rr-add").onclick = () => { $("#rr-rules").insertAdjacentHTML("beforeend", row()); set(read()); };
      $("#rr-ndvi").onclick = () => set([{ max: 0, label: "Water" }, { min: 0, max: 0.2, label: "Bare" }, { min: 0.2, max: 0.4, label: "Sparse" }, { min: 0.4, max: 0.6, label: "Moderate" }, { min: 0.6, label: "Dense" }]);
      $("#rr-even").onclick = () => {
        const l = getLayer($("#rr-raster").value), st = l?.legend?.stats;
        if (!st) return LF.toast("Choose a raster with values first", true);
        const step = (st.max - st.min) / 5;
        set([0, 1, 2, 3, 4].map((i) => ({ min: i ? +(st.min + i * step).toPrecision(4) : null, max: i < 4 ? +(st.min + (i + 1) * step).toPrecision(4) : null, label: `Class ${i + 1}` })));
      };
      const read = () => $$("#rr-rules tr").map((tr) => {
        const g = (k) => tr.querySelector(`[data-k="${k}"]`).value;
        return { min: g("min") === "" ? null : +g("min"), max: g("max") === "" ? null : +g("max"), label: g("label") };
      });
      return wire(LF, "rr", "/api/raster/reclassify", (v) => {
        const rules = read().map((r, i) => ({ ...r, value: i + 1 }));
        if (!rules.length) throw new Error("Add at least one range");
        return { raster: v.raster("rr-raster"), rules, name: $("#rr-name").value.trim() || "classes" };
      }, ["rr-raster"]);
    },
  });

  LF.tool({ id: "rchange", title: "Change detection", icon: "rchange", kinds: ["change"],
    subtitle: "What changed between two dates: the difference and % change (e.g. NDVI 2023 → 2024), or for land-cover maps every from → to change with its area",
    panel: `<div class="card"><h2>Change detection ${tip("The after raster is put on the before raster's grid. For class maps, the result codes each pixel as from × 100 + to, and a table lists the area of every change.")}</h2>
      ${rasterSel("rx-a", "Before")}${rasterSel("rx-b", "After")}
      <label class="check"><input type="checkbox" id="rx-cat"> Class maps (land cover): from → to</label>
      ${runRow("rx", "Compare")}</div>`,
    setup: (LF) => wire(LF, "rx", "/api/raster/change", (v) => {
      if (LF.$("#rx-a").value === LF.$("#rx-b").value) throw new Error("Choose two different rasters");
      return { before: v.raster("rx-a"), after: v.raster("rx-b"), categorical: LF.$("#rx-cat").checked };
    }, ["rx-a", "rx-b"], (r) => r.summary.changed_pct != null ? `<b>${r.summary.changed_pct}% changed</b> (${r.summary.changed_ha.toLocaleString()} ha); the table lists every change.`
      : `<b>Mean change ${LF.fmt(r.summary.mean_change, 3)}</b>; ${r.summary.increased_pct}% of the pixels went up.`),
  });

  LF.tool({ id: "rclip", title: "Clip raster", icon: "rclip", kinds: ["rclip"],
    subtitle: "Cut a raster to the polygons of a layer (or keep only what is outside them)",
    panel: `<div class="card"><h2>Clip raster</h2>${rasterSel("rp-raster", "Raster")}
      <label>To the polygons of <select id="rp-poly"></select></label>
      <label class="check"><input type="checkbox" id="rp-invert"> Keep the outside instead (mask the polygons)</label>
      <label>Name of the result <input type="text" id="rp-name" value="clipped" maxlength="80"></label>
      ${runRow("rp", "Clip")}</div>`,
    setup(LF) {
      const polys = () => LF.layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)));
      const fillP = () => LF.fillLayers(LF.$("#rp-poly"), polys(), { empty: "No polygon layer in Contents" });
      const hooks = wire(LF, "rp", "/api/raster/clip", (v) => {
        const l = LF.getLayer(LF.$("#rp-poly").value);
        if (!l) throw new Error("Choose a polygon layer");
        return { raster: v.raster("rp-raster"), area: l.geojson, invert: LF.$("#rp-invert").checked, name: LF.$("#rp-name").value.trim() || "clipped" };
      }, ["rp-raster"]);
      return { open(arg) { hooks.open(arg); fillP(); }, layersChanged() { hooks.layersChanged(); fillP(); } };
    },
  });
})();
