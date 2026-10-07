/* Analysis ▸ Tools ▸ Conversion: Raster → polygon, Raster → polyline (class boundaries or centrelines), Raster → point,
   Vector → raster (rasterize) and Convert features (polygons ↔ lines, vertices → points, points → lines, points along
   lines, lines → segments, bounding boxes). Jobs (progress, History, Workflows, the Assistant); results go to Contents.
   Server: /api/convert/raster-to-polygon, raster-to-polyline, raster-to-point, rasterize, features · lulc_fetch/convert.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const sel = (id, label) => `<label>${label} <select id="${id}"></select></label>`;
  const runRow = (p, label) => `<button class="btn primary" id="${p}-run">${label}</button><p class="hint hidden" id="${p}-error" style="color:var(--err)"></p><div id="${p}-result" class="hidden"></div>`;
  const nameRow = (p, val) => `<label>Name of the result <input type="text" id="${p}-name" value="${val}" maxlength="80"></label>`;
  const ints = (s) => { const t = s.trim(); if (!t) return null; const v = t.split(/[\s,]+/).filter(Boolean).map(Number); if (v.some((x) => !Number.isInteger(x))) throw new Error("Values: whole numbers, e.g. 1, 3"); return v; };

  // shared: layer pickers (rasters or vectors), the Run button as a job, the result into Contents
  function wire(LF, p, endpoint, body, pickers, describe) {
    const { $, api, layers, getLayer, fillLayers, runButton, trackJob, addRasterFromPath, addVectorLayer, showResult, esc } = LF;
    const kinds = { raster: (l) => l.type === "raster" && l.path, vector: (l) => l.type === "vector" && l.geojson?.features?.length };
    const fill = (pick) => Object.entries(pickers).forEach(([id, kind], i) => {
      const keep = $(`#${id}`).value;
      fillLayers($(`#${id}`), layers.filter(kinds[kind]), { empty: `No ${kind} layer in Contents`, pick: i === 0 ? pick : undefined });
      if (!pick && [...$(`#${id}`).options].some((o) => o.value === keep)) $(`#${id}`).value = keep;
    });
    const v = {
      raster: (id) => { const l = getLayer($(`#${id}`).value); if (!l) throw new Error("Choose a raster (add one with Insert ▸ Add data)"); return l.path; },
      vector: (id) => { const l = getLayer($(`#${id}`).value); if (!l) throw new Error("Choose a vector layer (add one with Insert ▸ Add data)"); return l.geojson; },
    };
    runButton(p, async () => {
      const job = await api(endpoint, { method: "POST", json: { ...body(v), name: $(`#${p}-name`).value.trim() || "result" } });
      const r = (await trackJob(job, { title: job.title })).result;
      if (/\.tiff?$/i.test(r.path)) await addRasterFromPath(r.path, { name: $(`#${p}-name`).value.trim() || undefined });
      else { const fc = await api(`/api/vector/read?path=${encodeURIComponent(r.path)}`); if (fc.features?.length) addVectorLayer(fc, r.name, { path: r.path }); }
      showResult(p, `${describe ? describe(r) : `<b>${r.features.toLocaleString()} feature${r.features === 1 ? "" : "s"}</b>`} <span class="hint">Added to Contents (<code>${esc(r.path)}</code>).</span>`);
    });
    return { open(arg) { fill(arg?.layer); }, layersChanged() { fill(); } };
  }

  LF.tool({ id: "r2poly", title: "Raster to polygon", icon: "r2poly", kinds: ["r2poly"],
    subtitle: "Areas of equal value of a class raster (land cover, a classified image, reclassified NDVI) as polygons with their class and area in hectares",
    panel: `<div class="card"><h2>Raster to polygon ${tip("Each patch of touching pixels with the same value becomes a polygon. Continuous rasters (NDVI, heights) need Reclassify first. A minimum area merges smaller patches into their neighbour before tracing, so the result isn't full of single pixels.")}</h2>
      ${sel("cp-raster", "Raster")}
      <label>Band <input type="number" id="cp-band" value="1" min="1"></label>
      <label>Only these values <input type="text" id="cp-values" placeholder="All, or e.g. 1, 3"></label>
      <label>Minimum area (m²) <input type="number" id="cp-min" value="0" min="0" step="any"></label>
      <label>Smooth outlines by (m) <input type="number" id="cp-simp" value="0" min="0" step="any"></label>
      <label class="check"><input type="checkbox" id="cp-diss"> One (multi)polygon per value (dissolve)</label>
      <label class="check"><input type="checkbox" id="cp-diag"> Pixels touching at a corner belong together</label>
      ${nameRow("cp", "polygons")}${runRow("cp", "Convert")}</div>`,
    setup: (LF) => wire(LF, "cp", "/api/convert/raster-to-polygon", (v) => ({ raster: v.raster("cp-raster"), band: +LF.$("#cp-band").value || 1,
      values: ints(LF.$("#cp-values").value), min_area: +LF.$("#cp-min").value || 0, simplify: +LF.$("#cp-simp").value || 0,
      dissolve: LF.$("#cp-diss").checked, diagonal: LF.$("#cp-diag").checked }), { "cp-raster": "raster" }),
  });

  LF.tool({ id: "r2line", title: "Raster to polyline", icon: "r2line", kinds: ["r2line"],
    subtitle: "Lines from a raster: the boundaries between classes, or the centrelines of thin features (roads, rivers, canals, field bunds) in a mask",
    panel: `<div class="card"><h2>Raster to polyline ${tip("Boundaries: every edge between two classes, once. Centrelines: the shapes (pixels with the chosen values, or any value other than 0) are thinned to one pixel wide and traced as lines, e.g. a road or river class into a network. For lines of equal height use Contours.")}</h2>
      ${sel("cl-raster", "Raster")}
      <label>Lines <select id="cl-mode"><option value="boundaries">Boundaries between classes</option><option value="centrelines">Centrelines of thin shapes</option></select></label>
      <label>Band <input type="number" id="cl-band" value="1" min="1"></label>
      <label>Only these values <input type="text" id="cl-values" placeholder="All (centrelines: all but 0), or e.g. 4"></label>
      <label>Smooth by (m) <input type="number" id="cl-simp" value="0" min="0" step="any"></label>
      <label>Drop lines shorter than (m) <input type="number" id="cl-min" value="0" min="0" step="any"></label>
      ${nameRow("cl", "lines")}${runRow("cl", "Convert")}</div>`,
    setup(LF) {
      LF.$("#cl-mode").onchange = () => { if (LF.$("#cl-mode").value === "centrelines" && !+LF.$("#cl-min").value) LF.$("#cl-min").value = 30; };
      return wire(LF, "cl", "/api/convert/raster-to-polyline", (v) => ({ raster: v.raster("cl-raster"), mode: LF.$("#cl-mode").value, band: +LF.$("#cl-band").value || 1,
        values: ints(LF.$("#cl-values").value), simplify: +LF.$("#cl-simp").value || 0, min_length: +LF.$("#cl-min").value || 0 }), { "cl-raster": "raster" });
    },
  });

  LF.tool({ id: "r2point", title: "Raster to point", icon: "r2point", kinds: ["r2point"],
    subtitle: "A point at the centre of each pixel (or every n-th pixel) with the values of its bands, e.g. sample points for training or a table",
    panel: `<div class="card"><h2>Raster to point ${tip("Pixels without data are skipped. Each point has a field per band (named after the band) and, for class maps, the class name. Up to 250,000 points: take every n-th pixel for big rasters.")}</h2>
      ${sel("ct-raster", "Raster")}
      <label>Bands <input type="text" id="ct-bands" placeholder="All, or e.g. 4,3,2"></label>
      <label>Every n-th pixel <input type="number" id="ct-step" value="1" min="1" max="1000"></label>
      ${nameRow("ct", "points")}${runRow("ct", "Convert")}</div>`,
    setup: (LF) => wire(LF, "ct", "/api/convert/raster-to-point", (v) => ({ raster: v.raster("ct-raster"), bands: ints(LF.$("#ct-bands").value), step: +LF.$("#ct-step").value || 1 }),
      { "ct-raster": "raster" }),
  });

  LF.tool({ id: "rasterize", title: "Vector to raster", icon: "rasterize", kinds: ["rasterize"],
    subtitle: "Burn a layer into a GeoTIFF (rasterize): a field's values (text becomes classes with names), presence, or how many points fall in each cell",
    panel: `<div class="card"><h2>Vector to raster ${tip("Numbers give a float raster; text gives one class per value, with its name and a colour (e.g. crop type for training labels). Presence: 1 where there is a shape. Count: shapes per cell (e.g. points per 100 m). Match a raster to get the very same grid, e.g. labels for an image.")}</h2>
      ${sel("cz-layer", "Layer")}
      <label>Values <select id="cz-mode"><option value="value">A field's values</option><option value="presence">Presence (1 inside the shapes)</option><option value="count">Count of shapes per cell (lines and polygons once, at their centre)</option></select></label>
      <label id="cz-field-row">Field <select id="cz-field"></select></label>
      <label>Grid <select id="cz-grid"><option value="res">Pixel size</option><option value="like">Same as a raster</option></select></label>
      <label id="cz-res-row">Pixel size (m) <input type="number" id="cz-res" value="10" min="0.01" step="any"></label>
      <label id="cz-like-row" class="hidden">Raster <select id="cz-like"></select></label>
      <label class="check"><input type="checkbox" id="cz-touch"> Every pixel a shape touches (not only those whose centre is inside)</label>
      ${nameRow("cz", "rasterized")}${runRow("cz", "Convert")}</div>`,
    setup(LF) {
      const { $, esc, getLayer } = LF;
      const fields = () => {
        const l = getLayer($("#cz-layer").value), was = $("#cz-field").value;
        const keys = [...new Set((l?.geojson?.features || []).slice(0, 200).flatMap((f) => Object.keys(f.properties || {})))];
        $("#cz-field").innerHTML = keys.map((k) => `<option>${esc(k)}</option>`).join("") || `<option value="">(no fields)</option>`;
        if (keys.includes(was)) $("#cz-field").value = was;
      };
      const show = () => { $("#cz-field-row").classList.toggle("hidden", $("#cz-mode").value !== "value");
        $("#cz-res-row").classList.toggle("hidden", $("#cz-grid").value !== "res"); $("#cz-like-row").classList.toggle("hidden", $("#cz-grid").value !== "like"); };
      $("#cz-mode").onchange = show; $("#cz-grid").onchange = show;
      $("#cz-layer").addEventListener("change", fields);
      const hooks = wire(LF, "cz", "/api/convert/rasterize", (v) => {
        const mode = $("#cz-mode").value, grid = $("#cz-grid").value;
        if (mode === "value" && !$("#cz-field").value) throw new Error("The layer has no fields: use presence or count");
        return { layer: v.vector("cz-layer"), mode, field: mode === "value" ? $("#cz-field").value : null, res: grid === "res" ? +$("#cz-res").value : null,
          like: grid === "like" ? v.raster("cz-like") : null, all_touched: $("#cz-touch").checked };
      }, { "cz-layer": "vector", "cz-like": "raster" }, (r) => `<b>${r.size[0].toLocaleString()} × ${r.size[1].toLocaleString()} pixels</b> (${r.pixels_with_data.toLocaleString()} with data)${r.classes ? `, ${Object.keys(r.classes).length} classes` : ""}.`);
      show();
      return { open(arg) { hooks.open(arg); fields(); }, layersChanged() { hooks.layersChanged(); fields(); } };
    },
  });

  const OPS = {
    polygons_to_lines: ["Polygons → lines (outlines)", ""],
    lines_to_polygons: ["Lines → polygons (the areas they enclose)", ""],
    vertices_to_points: ["Vertices → points", ""],
    points_to_lines: ["Points → lines (a track)", `<label>Order by <select id="cf-order"></select></label><label>One line per value of <select id="cf-group"></select></label><label class="check"><input type="checkbox" id="cf-close"> Back to the first point</label>`],
    points_along_lines: ["Points along lines (every n m)", `<label>Every (m) <input type="number" id="cf-dist" value="100" min="0.01" step="any"></label>`],
    split_lines: ["Lines → straight segments", ""],
    bounding_boxes: ["Bounding boxes (rectangles)", `<label class="check"><input type="checkbox" id="cf-whole"> One box around the whole layer</label>`],
  };
  LF.tool({ id: "vconvert", title: "Convert features", icon: "vconvert", kinds: ["vconvert"],
    subtitle: "Change the geometry type: polygons ↔ lines, vertices → points, points → lines (e.g. GPS tracks), points every n metres along lines, lines → segments, bounding boxes",
    panel: `<div class="card"><h2>Convert features</h2>
      <label>Conversion <select id="cf-op">${Object.entries(OPS).map(([k, [t]]) => `<option value="${k}">${t}</option>`).join("")}</select></label>
      ${sel("cf-layer", "Layer")}<div id="cf-params"></div>
      ${nameRow("cf", "")}${runRow("cf", "Convert")}</div>`,
    setup(LF) {
      const { $, esc, getLayer } = LF;
      const fieldOpts = (none) => { const l = getLayer($("#cf-layer").value);
        return `<option value="">${none}</option>` + [...new Set((l?.geojson?.features || []).slice(0, 200).flatMap((f) => Object.keys(f.properties || {})))].map((k) => `<option>${esc(k)}</option>`).join(""); };
      const params = () => {
        const op = $("#cf-op").value;
        $("#cf-params").innerHTML = OPS[op][1];
        if (op === "points_to_lines") { $("#cf-order").innerHTML = fieldOpts("(the layer's order)"); $("#cf-group").innerHTML = fieldOpts("(one line)"); }
        if (!$("#cf-name").dataset.touched) $("#cf-name").value = op;
      };
      $("#cf-name").addEventListener("input", () => { $("#cf-name").dataset.touched = "1"; });
      $("#cf-op").onchange = params;
      $("#cf-layer").addEventListener("change", () => { if ($("#cf-op").value === "points_to_lines") params(); });
      const hooks = wire(LF, "cf", "/api/convert/features", (v) => {
        const op = $("#cf-op").value, b = { op, layer: v.vector("cf-layer") };
        if (op === "points_to_lines") Object.assign(b, { order_by: $("#cf-order").value || null, group_by: $("#cf-group").value || null, close: $("#cf-close").checked });
        if (op === "points_along_lines") b.distance = +$("#cf-dist").value;
        if (op === "bounding_boxes") b.whole = $("#cf-whole").checked;
        return b;
      }, { "cf-layer": "vector" });
      params();
      return { open(arg) { hooks.open(arg); params(); }, layersChanged() { hooks.layersChanged(); } };
    },
  });
})();
