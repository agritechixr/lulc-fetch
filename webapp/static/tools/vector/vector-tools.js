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

  // ---- batch 1: spatial analysis
  LF.tool({ id: "vzonal", title: "Zonal statistics", icon: "vzonal", kinds: ["vzonal"],
    subtitle: "A raster's values summarised inside each polygon: mean, min, max… (e.g. mean NDVI of each field), or the % of each class of a land-cover map",
    panel: `<div class="card"><h2>Zonal statistics ${tip("For each polygon, the raster's pixels inside it are summarised and added as fields. For a class map (land cover), tick “classes” to get the % of each class and the main one.")}</h2>
      ${layerSel("vz-layer", "Polygons")}
      <label>Raster <select id="vz-raster"></select></label>
      <label>Band <input type="number" id="vz-band" value="1" min="1"></label>
      <label class="check"><input type="checkbox" id="vz-cat"> The raster is classes (land cover): % of each class</label>
      <div id="vz-stats" class="vz-stats">${["mean", "min", "max", "std", "median", "sum", "count"].map((k) => `<label class="check"><input type="checkbox" value="${k}" ${["mean", "min", "max", "count"].includes(k) ? "checked" : ""}> ${k}</label>`).join("")}</div>
      ${nameRow("vz-name", "zonal_stats")}
      <button class="btn primary" id="vz-run">Calculate</button><p class="hint hidden" id="vz-error" style="color:var(--err)"></p><div id="vz-result" class="hidden"></div></div>`,
    setup(LF) {
      const rasters = () => LF.layers.filter((l) => l.type === "raster" && l.path);
      const fillR = () => LF.fillLayers(LF.$("#vz-raster"), rasters(), { empty: "No raster layer in Contents" });
      const hooks = wire(LF, "vz", "/api/vector/zonal", (v) => {
        const r = LF.getLayer(LF.$("#vz-raster").value);
        if (!r) throw new Error("Choose a raster (add one with Insert ▸ Add data)");
        return { layer: v.layer("vz-layer"), raster: r.path, band: +LF.$("#vz-band").value || 1, categorical: LF.$("#vz-cat").checked,
                 stats: LF.$$("#vz-stats input:checked").map((c) => c.value) };
      }, ["vz-layer"], (l) => l.geojson.features.some((f) => /Polygon/.test(f.geometry?.type)));
      LF.$("#vz-cat").onchange = (e) => LF.$("#vz-stats").classList.toggle("hidden", e.target.checked);
      LF.$("#vz-raster").addEventListener("change", () => { const r = LF.getLayer(LF.$("#vz-raster").value); if (r?.legend?.kind === "classes") LF.$("#vz-cat").checked = true; LF.$("#vz-cat").onchange({ target: LF.$("#vz-cat") }); });
      return { open(arg) { hooks.open(arg); fillR(); }, layersChanged() { hooks.layersChanged(); fillR(); } };
    },
  });

  LF.tool({ id: "vlocation", title: "Select by location", icon: "vlocation", kinds: ["vlocation"],
    subtitle: "The features of a layer that intersect, are inside, contain, are apart from, or are within a distance of another layer (e.g. wells within 500 m of the river)",
    panel: `<div class="card"><h2>Select by location</h2>
      ${layerSel("vl-a", "Select from")}
      <label>That <select id="vl-pred"><option value="intersects">intersect</option><option value="within">are inside</option><option value="contains">contain</option>
        <option value="within_distance">are within a distance of</option><option value="disjoint">don't touch</option></select></label>
      ${layerSel("vl-b", "The layer")}
      <label id="vl-dist-row" class="hidden">Distance (m) <input type="number" id="vl-dist" value="500" min="0" step="any"></label>
      ${nameRow("vl-name", "selected")}
      <button class="btn primary" id="vl-run">Select</button><p class="hint hidden" id="vl-error" style="color:var(--err)"></p><div id="vl-result" class="hidden"></div></div>`,
    setup(LF) {
      LF.$("#vl-pred").onchange = (e) => LF.$("#vl-dist-row").classList.toggle("hidden", e.target.value !== "within_distance");
      return wire(LF, "vl", "/api/vector/select-location", (v) => {
        if (LF.$("#vl-a").value === LF.$("#vl-b").value) throw new Error("Choose two different layers");
        return { a: v.layer("vl-a"), b: v.layer("vl-b"), predicate: LF.$("#vl-pred").value, distance: +LF.$("#vl-dist").value || 0 };
      }, ["vl-a", "vl-b"]);
    },
  });

  LF.tool({ id: "vsjoin", title: "Spatial join", icon: "vsjoin", kinds: ["vsjoin"],
    subtitle: "Give each feature the attributes of the feature of another layer it overlaps, lies in, or is nearest to (e.g. each field gets its district's name)",
    panel: `<div class="card"><h2>Spatial join ${tip("Polygons get the one they overlap most; points the one they are in. Nearest adds the distance in metres (join_dist_m).")}</h2>
      ${layerSel("vj-a", "Features")}${layerSel("vj-b", "Take attributes from")}
      <label>Match <select id="vj-how"><option value="intersects">Overlapping (the most)</option><option value="within">The one they are inside</option><option value="nearest">The nearest</option></select></label>
      <label id="vj-max-row" class="hidden">Up to (m, empty = any distance) <input type="number" id="vj-max" min="0" step="any"></label>
      ${nameRow("vj-name", "joined")}
      <button class="btn primary" id="vj-run">Join</button><p class="hint hidden" id="vj-error" style="color:var(--err)"></p><div id="vj-result" class="hidden"></div></div>`,
    setup(LF) {
      LF.$("#vj-how").onchange = (e) => LF.$("#vj-max-row").classList.toggle("hidden", e.target.value !== "nearest");
      return wire(LF, "vj", "/api/vector/spatial-join", (v) => {
        if (LF.$("#vj-a").value === LF.$("#vj-b").value) throw new Error("Choose two different layers");
        return { a: v.layer("vj-a"), b: v.layer("vj-b"), how: LF.$("#vj-how").value, max_distance: LF.$("#vj-max").value === "" ? null : +LF.$("#vj-max").value };
      }, ["vj-a", "vj-b"]);
    },
  });

  LF.tool({ id: "vgeometry", title: "Calculate geometry", icon: "vgeometry", kinds: ["vgeometry"],
    subtitle: "Add area (m², hectares), perimeter or length (m) and the centroid (lon, lat) as fields, measured on the ground",
    panel: `<div class="card"><h2>Calculate geometry</h2>${layerSel("vg-layer", "Layer")}${nameRow("vg-name", "with_geometry")}
      <p class="hint">Adds area_m2, area_ha, perimeter_m (polygons) or length_m (lines), and centroid_lon / centroid_lat.</p>
      <button class="btn primary" id="vg-run">Calculate</button><p class="hint hidden" id="vg-error" style="color:var(--err)"></p><div id="vg-result" class="hidden"></div></div>`,
    setup: (LF) => wire(LF, "vg", "/api/vector/geometry", (v) => ({ layer: v.layer("vg-layer") })),
  });

  LF.tool({ id: "vcount", title: "Count points in polygons", icon: "vcount", kinds: ["vcount"],
    subtitle: "How many points fall in each polygon (e.g. wells per village), and the sum of a points' field",
    panel: `<div class="card"><h2>Count points in polygons</h2>
      ${layerSel("vc-poly", "Polygons")}${layerSel("vc-pts", "Points")}
      <label>Also sum the field <select id="vc-sum"><option value="">(none)</option></select></label>
      ${nameRow("vc-name", "point_counts")}
      <button class="btn primary" id="vc-run">Count</button><p class="hint hidden" id="vc-error" style="color:var(--err)"></p><div id="vc-result" class="hidden"></div></div>`,
    setup(LF) {
      const hooks = wire(LF, "vc", "/api/vector/count-points", (v) => ({ polygons: v.layer("vc-poly"), points: v.layer("vc-pts"), sum_field: LF.$("#vc-sum").value || null }), ["vc-poly", "vc-pts"]);
      const fields = () => {
        const l = LF.getLayer(LF.$("#vc-pts").value), was = LF.$("#vc-sum").value;
        const keys = [...new Set((l?.geojson?.features || []).slice(0, 200).flatMap((f) => Object.entries(f.properties || {}).filter(([, x]) => typeof x === "number").map(([k]) => k)))];
        LF.$("#vc-sum").innerHTML = `<option value="">(none)</option>` + keys.map((k) => `<option>${LF.esc(k)}</option>`).join("");
        if (keys.includes(was)) LF.$("#vc-sum").value = was;
      };
      LF.$("#vc-pts").addEventListener("change", fields);
      return { open(arg) { hooks.open(arg); fields(); }, layersChanged() { hooks.layersChanged(); fields(); } };
    },
  });

  LF.tool({ id: "vtjoin", title: "Join table to layer", icon: "vtjoin", kinds: ["vtjoin"],
    subtitle: "Attach a table (CSV, Excel, Parquet) to a layer by a shared field, e.g. a yield table by field ID",
    panel: `<div class="card"><h2>Join table to layer ${tip("Each feature gets the columns of the table row whose key matches its own (text compared without case; 7, 7.0 and “7” match).")}</h2>
      ${layerSel("vt-layer", "Layer")}<label>Layer's key field <select id="vt-lf"></select></label>
      <label>Table <select id="vt-table"></select></label><label>Table's key column <input type="text" id="vt-tf" placeholder="e.g. field_id"></label>
      ${nameRow("vt-name", "joined_table")}
      <button class="btn primary" id="vt-run">Join</button><p class="hint hidden" id="vt-error" style="color:var(--err)"></p><div id="vt-result" class="hidden"></div></div>`,
    setup(LF) {
      const tables = () => LF.dataItems.filter((d) => d.kind === "table" && d.path);
      const fill = () => {
        const was = LF.$("#vt-table").value;
        LF.$("#vt-table").innerHTML = tables().map((d) => `<option value="${LF.esc(d.path)}">${LF.esc(d.name)}</option>`).join("") || `<option value="">No table in Contents</option>`;
        if (tables().some((d) => d.path === was)) LF.$("#vt-table").value = was;
        const l = LF.getLayer(LF.$("#vt-layer").value), lw = LF.$("#vt-lf").value;
        const keys = [...new Set((l?.geojson?.features || []).slice(0, 200).flatMap((f) => Object.keys(f.properties || {})))];
        LF.$("#vt-lf").innerHTML = keys.map((k) => `<option>${LF.esc(k)}</option>`).join("");
        if (keys.includes(lw)) LF.$("#vt-lf").value = lw;
        if (!LF.$("#vt-tf").value && keys.length) LF.$("#vt-tf").value = LF.$("#vt-lf").value;
      };
      const hooks = wire(LF, "vt", "/api/vector/join-table", (v) => {
        if (!LF.$("#vt-table").value) throw new Error("Add a table first (Insert ▸ Add data)");
        return { layer: v.layer("vt-layer"), table: LF.$("#vt-table").value, layer_field: LF.$("#vt-lf").value, table_field: LF.$("#vt-tf").value.trim() || LF.$("#vt-lf").value };
      }, ["vt-layer"], null, (r) => r.unmatched ? ` ${r.unmatched} feature${r.unmatched === 1 ? "" : "s"} found no row.` : "");
      LF.$("#vt-layer").addEventListener("change", fill);
      return { open(arg) { hooks.open(arg); fill(); }, layersChanged() { hooks.layersChanged(); fill(); } };
    },
  });

  // ---- batch 3: geometry helpers, one panel
  const OPS = {
    centroids: ["Centroids (a point per shape)", `<label class="check"><input type="checkbox" id="vh-inside"> A point surely inside each shape (not the centre of gravity)</label>`],
    convex_hull: ["Convex hull", `<label class="check"><input type="checkbox" id="vh-whole"> One hull around the whole layer</label>`],
    simplify: ["Simplify (fewer vertices)", `<label>Tolerance (m) <input type="number" id="vh-tol" value="10" min="0.01" step="any"></label>`],
    explode: ["Multipart to single parts", ""],
    merge: ["Merge layers into one", `<div id="vh-merge" class="wf-checks"></div>`],
    fishnet: ["Fishnet grid over the layer", `<label>Cell size (m) <input type="number" id="vh-cell" value="100" min="1" step="any"></label><label class="check"><input type="checkbox" id="vh-clip" checked> Only the cells inside the shapes</label>`],
    random_points: ["Random points inside polygons", `<label>Points <input type="number" id="vh-count" value="100" min="1" max="100000"></label><label class="check"><input type="checkbox" id="vh-per"> That many in each polygon</label>`],
  };
  LF.tool({ id: "vhelpers", title: "Geometry tools", icon: "vhelpers", kinds: ["vgeomop"],
    subtitle: "Centroids, convex hull, simplify, merge layers, multipart to single parts, a fishnet grid, random points inside polygons",
    panel: `<div class="card"><h2>Geometry tools</h2>
      <label>Operation <select id="vh-op">${Object.entries(OPS).map(([k, [t]]) => `<option value="${k}">${t}</option>`).join("")}</select></label>
      <div id="vh-layer-row">${layerSel("vh-layer", "Layer")}</div>
      <div id="vh-params"></div>
      ${nameRow("vh-name", "")}
      <button class="btn primary" id="vh-run">Run</button><p class="hint hidden" id="vh-error" style="color:var(--err)"></p><div id="vh-result" class="hidden"></div></div>`,
    setup(LF) {
      const { $, $$, esc, layers } = LF;
      const params = () => {
        const op = $("#vh-op").value;
        $("#vh-params").innerHTML = OPS[op][1];
        $("#vh-layer-row").classList.toggle("hidden", op === "merge");
        if (!$("#vh-name").dataset.touched) $("#vh-name").value = op;
        if (op === "merge") $("#vh-merge").innerHTML = layers.filter((l) => l.type === "vector" && l.geojson?.features?.length)
          .map((l) => `<label class="check"><input type="checkbox" value="${esc(l.id)}" checked> ${esc(l.name)}</label>`).join("") || `<p class="hint">No vector layers.</p>`;
      };
      $("#vh-name").addEventListener("input", () => { $("#vh-name").dataset.touched = "1"; });
      $("#vh-op").onchange = params;
      const hooks = wire(LF, "vh", "/api/vector/geom-op", (v) => {
        const op = $("#vh-op").value, b = { op };
        if (op === "merge") {
          const ids = $$("#vh-merge input:checked").map((c) => c.value);
          if (ids.length < 2) throw new Error("Tick two or more layers to merge");
          Object.assign(b, { layers: ids.map((id) => LF.getLayer(id).geojson), layer_names: ids.map((id) => LF.getLayer(id).name) });
        } else b.layer = v.layer("vh-layer");
        if (op === "centroids") b.inside = $("#vh-inside").checked;
        if (op === "convex_hull") b.whole = $("#vh-whole").checked;
        if (op === "simplify") b.tolerance = +$("#vh-tol").value;
        if (op === "fishnet") Object.assign(b, { cell: +$("#vh-cell").value, clip: $("#vh-clip").checked });
        if (op === "random_points") Object.assign(b, { count: +$("#vh-count").value, per_feature: $("#vh-per").checked });
        return b;
      });
      params();
      return { open(arg) { hooks.open(arg); if ($("#vh-op").value === "merge") params(); }, layersChanged() { hooks.layersChanged(); if ($("#vh-op").value === "merge") params(); } };
    },
  });

  // the shared wiring: layer pickers, the Run button (a job), the result added to Contents
  function wire(LF, p, endpoint, body, selects = [`${p}-layer`], only = null, extra = null) {
    const { $, api, layers, getLayer, fillLayers, runButton, trackJob, addVectorLayer, showResult, esc } = LF;
    const vectors = () => layers.filter((l) => l.type === "vector" && l.geojson?.features?.length && (!only || only(l)));
    const fill = (pick) => selects.forEach((s, i) => fillLayers($(`#${s}`), vectors(), { empty: "No vector layer in Contents", pick: i === 0 ? pick : undefined }));
    const v = { layer: (s) => { const l = getLayer($(`#${s}`).value); if (!l) throw new Error("Choose a vector layer (add one with Insert ▸ Add data)"); return l.geojson; } };
    runButton(p, async () => {
      const job = await api(endpoint, { method: "POST", json: { ...body(v), name: $(`#${p}-name`).value.trim() || "result" } });
      const done = await trackJob(job, { title: job.title });
      const r = done.result, fc = await api(`/api/vector/read?path=${encodeURIComponent(r.path)}`);
      if (fc.features?.length) addVectorLayer(fc, r.name, { path: r.path });
      showResult(p, `<b>${r.features.toLocaleString()} feature${r.features === 1 ? "" : "s"}</b> in “${esc(r.name)}”, added to Contents${r.features ? "" : " (nothing matched)"}.${extra ? esc(extra(r)) : ""} <span class="hint">Saved as <code>${esc(r.path)}</code>; open its attribute table to see the new fields.</span>`);
    });
    return { open(arg) { fill(arg?.layer); }, layersChanged() { fill(); } };
  }
})();
