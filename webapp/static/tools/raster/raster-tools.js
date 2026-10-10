/* Analysis ▸ Tools ▸ Raster & terrain: Terrain (slope, aspect, hillshade), Contours, Reclassify, Change detection,
   Clip raster, Resample / reproject and Enhance image; and in Imagery: Mosaic / merge rasters, Burn severity (dNBR) and Water mask. Jobs (progress, History, Workflows, the Assistant); results are
   added to Contents. Server: /api/raster/terrain, contours, reclassify, change, clip, resample, enhance, mosaic, burn ·
   lulc_fetch/raster_ops.py, enhance.py, resample.py, mosaic.py, burn.py, watermask.py. */
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
      const files = [...new Set([...(r.outputs || []), ...(r.path ? [r.path] : [])].map((f) => f.replace(/^.*?\/(analysis|downloads|imports|tables)\//, "$1/")))];
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
      <details><summary class="hint">If the grids differ</summary>${LF.html.resampling("rx-method", { auto: "Default (bilinear; classes nearest)", only: ["bilinear", "cubic", "cubic_spline", "lanczos", "average", "nearest"] })}</details>
      ${runRow("rx", "Compare")}</div>`,
    setup: (LF) => wire(LF, "rx", "/api/raster/change", (v) => {
      if (LF.$("#rx-a").value === LF.$("#rx-b").value) throw new Error("Choose two different rasters");
      return { before: v.raster("rx-a"), after: v.raster("rx-b"), categorical: LF.$("#rx-cat").checked, resampling: LF.$("#rx-method").value || null };
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

  LF.tool({ id: "rresample", title: "Resample / reproject", icon: "rresample", kinds: ["resample"],
    subtitle: "Change a raster's pixel size or coordinate system with nearest, bilinear, cubic (bicubic), lanczos, average, mode and more",
    panel: `<div class="card"><h2>Resample / reproject ${tip("Pixel size is in the units of the coordinate system (metres for UTM, degrees for EPSG:4326). A factor of 2 makes pixels twice as small (more of them), 0.5 twice as big.")}</h2>
      ${rasterSel("rs-raster", "Raster")}
      <label>New size by <select id="rs-by"><option value="res">Pixel size</option><option value="scale">Factor</option><option value="none">Keep (only reproject)</option></select></label>
      <label id="rs-res-l">Pixel size <input type="number" id="rs-res" value="10" min="0" step="any"></label>
      <label id="rs-scale-l" class="hidden">Factor <select id="rs-scale"><option value="0.25">× 0.25 (4× bigger pixels)</option><option value="0.5">× 0.5 (2× bigger pixels)</option><option value="2" selected>× 2 (2× smaller pixels)</option><option value="4">× 4 (4× smaller pixels)</option></select></label>
      <label>Coordinate system <input type="text" id="rs-crs" placeholder="Keep, or e.g. EPSG:32643" pattern="EPSG:[0-9]+"></label>
      ${LF.html.resampling("rs-method", { label: "Method", auto: "Auto (classes: nearest / mode; values: bilinear / average)" })}
      <p class="hint" id="rs-about"></p>
      <label>Name of the result <input type="text" id="rs-name" value="resampled" maxlength="80"></label>
      ${runRow("rs", "Resample")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const by = () => { const b = $("#rs-by").value; $("#rs-res-l").classList.toggle("hidden", b !== "res"); $("#rs-scale-l").classList.toggle("hidden", b !== "scale"); };
      $("#rs-by").onchange = by;
      $("#rs-method").onchange = () => { $("#rs-about").textContent = LF.html.RESAMPLING.find(([n]) => n === $("#rs-method").value)?.[2] || ""; };
      return wire(LF, "rs", "/api/raster/resample", (v) => {
        const b = $("#rs-by").value, crs = $("#rs-crs").value.trim().toUpperCase();
        if (crs && !/^EPSG:\d{4,6}$/.test(crs)) throw new Error("Coordinate system: an EPSG code, e.g. EPSG:32643");
        if (b === "none" && !crs) throw new Error("Give a coordinate system, or choose a new pixel size or factor");
        return { raster: v.raster("rs-raster"), res: b === "res" ? +$("#rs-res").value || null : null, scale: b === "scale" ? +$("#rs-scale").value : null,
          crs: crs || null, method: $("#rs-method").value || null, name: $("#rs-name").value.trim() || "resampled" };
      }, ["rs-raster"], (r) => `<b>${r.size[0].toLocaleString()} × ${r.size[1].toLocaleString()} pixels</b> of ${+r.res[0].toPrecision(6)} (${LF.esc(r.method)}).`);
    },
  });

  // the enhancement steps: [op, label, params [[key, label, default, min, max, step]]]
  const STEPS = [
    ["stretch", "Contrast stretch (percent clip)", [["low", "Low %", 2, 0, 50, 0.5], ["high", "High %", 98, 50, 100, 0.5]]],
    ["equalize", "Histogram equalisation", []],
    ["clahe", "CLAHE (adaptive equalisation)", [["tiles", "Tiles", 8, 1, 64, 1], ["clip", "Clip limit", 0.01, 0.001, 1, 0.001]]],
    ["gamma", "Gamma", [["gamma", "Gamma", 1.2, 0.1, 10, 0.1]]],
    ["median", "Denoise: median filter", [["size", "Window", 3, 1, 31, 2]]],
    ["gaussian", "Denoise: gaussian blur", [["sigma", "Sigma", 1, 0.1, 20, 0.1]]],
    ["sharpen", "Sharpen (unsharp mask)", [["sigma", "Sigma", 1.5, 0.1, 20, 0.1], ["amount", "Amount", 1, 0, 10, 0.1]]],
    ["sobel", "Edges: Sobel", []],
    ["laplacian", "Edges: Laplacian", []],
    ["focal_mean", "Focal mean", [["size", "Window", 3, 1, 31, 2]]],
    ["focal_std", "Focal std (texture)", [["size", "Window", 3, 1, 31, 2]]],
    ["focal_min", "Focal minimum", [["size", "Window", 3, 1, 31, 2]]],
    ["focal_max", "Focal maximum", [["size", "Window", 3, 1, 31, 2]]],
    ["majority", "Majority filter (clean class maps)", [["size", "Window", 3, 1, 31, 2]]],
  ];
  const PRESETS = {
    cv: [{ op: "stretch" }, { op: "clahe" }, { op: "sharpen" }],
    denoise: [{ op: "median", size: 3 }, { op: "stretch" }],
    edges: [{ op: "gaussian", sigma: 1 }, { op: "sobel" }],
    texture: [{ op: "focal_std", size: 5 }],
    classes: [{ op: "majority", size: 3 }],
  };

  LF.tool({ id: "renhance", title: "Enhance image", icon: "renhance", kinds: ["enhance"],
    subtitle: "Improve an image for viewing, computer vision or embeddings: contrast stretch, equalisation, CLAHE, gamma, denoise, sharpen, edges, texture, upscale ×2 / ×4 (cubic, lanczos), majority filter for class maps",
    panel: `<div class="card"><h2>Enhance image ${tip("The steps run in order on every band (or the bands you list). Results are float32, except a class map with only majority / median / min / max steps, which keeps its classes and colours.")}</h2>
      ${rasterSel("re-raster", "Image")}
      <label>Bands <input type="text" id="re-bands" placeholder="All, or e.g. 4,3,2"></label>
      <div class="row tight" style="gap:6px;flex-wrap:wrap;margin:4px 0 8px"><span class="hint">Presets:</span>
        <button class="btn small ghost" data-preset="cv" title="Stretch, CLAHE, sharpen: crisp, even contrast for detection, segmentation and embeddings">Computer vision</button>
        <button class="btn small ghost" data-preset="denoise">Denoise</button><button class="btn small ghost" data-preset="edges">Edges</button>
        <button class="btn small ghost" data-preset="texture">Texture</button><button class="btn small ghost" data-preset="classes" title="Removes speckle from a classified map">Clean class map</button></div>
      <div id="re-steps"></div>
      <div class="row tight" style="gap:6px;margin:4px 0 8px"><select id="re-add">${STEPS.map(([op, label]) => `<option value="${op}">${label}</option>`).join("")}</select><button class="btn small" id="re-add-btn">+ Step</button></div>
      <label>Enlarge first <select id="re-up"><option value="1">No</option><option value="2">× 2</option><option value="4">× 4</option></select></label>
      ${LF.html.resampling("re-up-method", { label: "Enlarge with", auto: "Cubic (bicubic)", only: ["nearest", "bilinear", "cubic", "cubic_spline", "lanczos"] })}
      <label>Name of the result <input type="text" id="re-name" value="enhanced" maxlength="80"></label>
      ${runRow("re", "Enhance")}</div>`,
    setup(LF) {
      const { $, $$, esc } = LF;
      let steps = [...PRESETS.cv];
      const render = () => {
        $("#re-steps").innerHTML = steps.length ? steps.map((s, i) => {
          const [, label, params] = STEPS.find(([op]) => op === s.op);
          return `<div class="card re-step" data-i="${i}" style="padding:6px 8px;margin:4px 0"><div class="row tight" style="justify-content:space-between"><b>${i + 1}. ${esc(label)}</b>
            <span><button class="np-copy" data-up title="Earlier">↑</button><button class="np-copy" data-del title="Remove">×</button></span></div>
            ${params.map(([k, pl, d, min, max, st]) => `<label>${pl} <input type="number" data-k="${k}" value="${s[k] ?? d}" min="${min}" max="${max}" step="${st}"></label>`).join("")}</div>`;
        }).join("") : `<p class="hint">No steps: add one, or choose a preset.</p>`;
        $$("#re-steps .re-step").forEach((el) => {
          const i = +el.dataset.i;
          el.querySelector("[data-del]").onclick = () => { steps.splice(i, 1); render(); };
          el.querySelector("[data-up]").onclick = () => { if (i) { [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]]; render(); } };
          el.querySelectorAll("[data-k]").forEach((inp) => inp.onchange = () => { steps[i][inp.dataset.k] = +inp.value; });
        });
      };
      $$("#tab-renhance [data-preset]").forEach((b) => b.onclick = () => { steps = PRESETS[b.dataset.preset].map((s) => ({ ...s })); render(); });
      $("#re-add-btn").onclick = () => { steps.push({ op: $("#re-add").value }); render(); };
      render();
      return wire(LF, "re", "/api/raster/enhance", (v) => {
        const up = +$("#re-up").value, bt = $("#re-bands").value.trim();
        if (!steps.length && up === 1) throw new Error("Add a step, or enlarge the image");
        const bands = bt ? bt.split(/[\s,]+/).filter(Boolean).map(Number) : null;
        if (bands?.some((b) => !Number.isInteger(b) || b < 1)) throw new Error("Bands: numbers from 1, e.g. 4,3,2");
        return { raster: v.raster("re-raster"), steps: steps.map((s) => ({ ...s })), bands, upscale: up,
          upscale_method: $("#re-up-method").value || "cubic", name: $("#re-name").value.trim() || "enhanced" };
      }, ["re-raster"], (r) => `<b>${r.steps.length} step${r.steps.length === 1 ? "" : "s"} on ${r.bands} band${r.bands === 1 ? "" : "s"}</b>${r.upscale > 1 ? `, enlarged × ${r.upscale}` : ""}.`);
    },
  });

  // ---------------- Imagery: Mosaic and Burn severity (dNBR)
  LF.tool({ id: "rmosaic", title: "Mosaic / merge rasters", icon: "rmosaic", kinds: ["mosaic"],
    subtitle: "Join neighbouring tiles or scenes into one image. By default the seams are blended smoothly and the colours matched, so no tile edges show",
    panel: `<div class="card"><h2>Rasters to join ${tip("Rasters with the same bands (e.g. Sentinel-2 tiles or scenes, DEM tiles, class maps). They can be in different coordinate systems: the result uses the first one's (its pixel size too).")}</h2>
      <div class="mo-list" id="mo-list"></div>
      <p class="hint" id="mo-hint">In Contents order: the top one is first. Drag layers in Contents to change it.</p></div>
      <div class="card"><h2>How overlaps are joined</h2>
      <label class="check"><input type="checkbox" id="mo-cat"> These are class maps (land cover…) ${tip("Classes can't be averaged: nearest-neighbour resampling, no blending or colour matching.")}</label>
      <label>Method <select id="mo-method"></select></label>
      <p class="hint" id="mo-about"></p>
      <label class="check" id="mo-bal-row"><input type="checkbox" id="mo-bal" checked> Match colours across overlaps ${tip("Scenes from different dates or light get a gain and offset per band so they match where they overlap, working outwards from the first image. Untick to keep the values exactly as they are.")}</label>
      <details><summary class="hint">More options</summary>
        <label id="mo-zone-row">Blend zone (pixels) ${tip("How wide the smooth fade at each image's edge is. Wider hides differences better; narrower keeps more of each image's own pixels.")}<input type="number" id="mo-zone" value="64" min="1" max="2000"></label>
        <label>Pixel size (map units, e.g. m) <input type="number" id="mo-res" min="0" step="any" placeholder="the first raster's"></label>
        ${LF.html.resampling("mo-resamp", { auto: "Default (bilinear; class maps nearest)", only: ["bilinear", "cubic", "lanczos", "average", "nearest"] })}
      </details>
      <label>Name of the result <input type="text" id="mo-name" value="mosaic" maxlength="80"></label>
      ${runRow("mo", "Join")}</div>`,
    setup(LF) {
      const { $, $$, esc, layers } = LF;
      const IMG = [["blend", "Smooth blend (recommended)", "Each image fades out towards its edges, so overlaps are a smooth mix: no seams or tile edges."],
        ["first", "First on top", "Where images overlap, the first one's pixels; the others only fill gaps. Sharp edges."],
        ["last", "Last on top", "Where images overlap, the last one's pixels."],
        ["median", "Median", "The middle value of all images at each pixel: with 3+ scenes it removes clouds and haze seen in only one."],
        ["mean", "Mean", "The average of all images at each pixel."],
        ["min", "Minimum", "The lowest value (e.g. the darkest: avoids clouds in visible bands)."],
        ["max", "Maximum", "The highest value (e.g. greenest NDVI of several dates)."]];
      const CLS = [["first", "First on top", "Where maps overlap, the first one's classes."], ["last", "Last on top", "Where maps overlap, the last one's classes."],
        ["mode", "Most common class", "The class most of the maps give at each pixel."]];
      const picked = new Set();
      const rasters = () => layers.filter((l) => l.type === "raster" && l.path);
      function list() {
        const rs = rasters();
        if (!picked.size) rs.forEach((l) => picked.add(l.id));
        $("#mo-list").innerHTML = rs.length ? rs.map((l, i) => `<label class="check"><input type="checkbox" value="${esc(l.id)}" ${picked.has(l.id) ? "checked" : ""}> ${i + 1}. ${esc(l.name)}
            <small class="hint">${l.info ? `${l.info.count} band${l.info.count === 1 ? "" : "s"} · ${esc(l.info.crs || "")}` : ""}</small></label>`).join("")
          : `<p class="hint">No raster layer in Contents: add the tiles with Insert ▸ Add data.</p>`;
        $$("#mo-list input").forEach((c) => c.onchange = () => { c.checked ? picked.add(c.value) : picked.delete(c.value); autoCat(); });
        autoCat();
      }
      function autoCat() {   // all chosen are class maps → tick "class maps"
        const ch = rasters().filter((l) => picked.has(l.id));
        if (ch.length && ch.every((l) => l.legend?.kind === "classes") !== $("#mo-cat").checked && !$("#mo-cat").dataset.touched) { $("#mo-cat").checked = !$("#mo-cat").checked; methods(); }
      }
      function methods() {
        const cat = $("#mo-cat").checked, opts = cat ? CLS : IMG, was = $("#mo-method").value;
        $("#mo-method").innerHTML = opts.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("");
        if (opts.some(([v]) => v === was)) $("#mo-method").value = was;
        about();
      }
      function about() {
        const cat = $("#mo-cat").checked, m = $("#mo-method").value;
        $("#mo-about").textContent = (cat ? CLS : IMG).find(([v]) => v === m)?.[2] || "";
        $("#mo-bal-row").classList.toggle("hidden", cat);
        $("#mo-zone-row").classList.toggle("hidden", m !== "blend");
      }
      $("#mo-cat").onchange = () => { $("#mo-cat").dataset.touched = "1"; methods(); };
      $("#mo-method").onchange = about;
      methods();
      const hooks = wire(LF, "mo", "/api/raster/mosaic", () => {
        const chosen = rasters().filter((l) => picked.has(l.id));
        if (chosen.length < 2) throw new Error("Tick at least two rasters to join");
        const cat = $("#mo-cat").checked;
        return { rasters: chosen.map((l) => l.path), method: $("#mo-method").value, categorical: cat, balance: !cat && $("#mo-bal").checked ? "overlap" : "none",
                 blend_px: +$("#mo-zone").value || 64, res: +$("#mo-res").value || null, resampling: $("#mo-resamp").value || null,
                 name: $("#mo-name").value.trim() || "mosaic" };
      }, [], (r) => `<b>${r.images} images joined</b>: ${r.width.toLocaleString()} × ${r.height.toLocaleString()} pixels of ${LF.fmt(r.res, 3)} (${esc(r.crs)}), ${r.covered_pct}% with data.`);
      return { open(arg) { hooks.open(arg); list(); }, layersChanged() { hooks.layersChanged(); list(); } };
    },
  });

  LF.tool({ id: "rburn", title: "Burn severity (dNBR)", icon: "rburn", kinds: ["burn"],
    subtitle: "Where land burned between two dates and how badly: dNBR and its severity classes with burned hectares, e.g. stubble burning after the harvest",
    panel: `<div class="card"><h2>Images ${tip("Two images of the same area (Sentinel-2 or Landsat with their NIR and SWIR2 bands, B08 and B12), one before and one after the burning. Or two NBR rasters made with Index analysis. Cloud-free images work best.")}</h2>
      ${rasterSel("rb-a", "Before (pre-fire)")}${rasterSel("rb-b", "After (post-fire)")}
      <p class="hint">NBR = (NIR − SWIR2) / (NIR + SWIR2) of each date; dNBR = before − after. Burned land loses NIR and gains SWIR, so it has a high dNBR.</p></div>
      <div class="card"><h2>Classes</h2>
      <label class="check"><input type="checkbox" id="rb-stubble" checked> Count only dark, charred land as burned ${tip("A field that was only harvested also loses NBR, but its bare soil stays brighter than ash. With this ticked, a pixel counts as burned only if its NBR after is below the limit (0.1 suits stubble burning; untick for forest fires).")}</label>
      <label id="rb-lim-row">NBR after below <input type="number" id="rb-lim" value="0.1" step="0.01" min="-1" max="1"></label>
      <details><summary class="hint">Class limits</summary>
        <p class="hint">USGS (Key &amp; Benson 2006): below −0.25 high regrowth · −0.1 low regrowth · 0.1 unburned · 0.27 low · 0.44 moderate-low · 0.66 moderate-high · above high severity.</p>
        <label>Your own 6 limits (dNBR, comma-separated) <input type="text" id="rb-breaks" class="mono" placeholder="-0.25, -0.1, 0.1, 0.27, 0.44, 0.66"></label>
      </details>
      <label>Name of the result <input type="text" id="rb-name" placeholder="before_to_after" maxlength="80"></label>
      ${runRow("rb", "Map burn severity")}</div>`,
    setup(LF) {
      const { $, getLayer, esc, fmt } = LF;
      $("#rb-stubble").onchange = () => $("#rb-lim-row").classList.toggle("hidden", !$("#rb-stubble").checked);
      const img = (id) => { const l = getLayer($(`#${id}`).value); return { bands: l?.band_map || {}, scale: l?.scale != null ? [l.scale, l.offset ?? 0] : null }; };
      return wire(LF, "rb", "/api/raster/burn", (v) => {
        if ($("#rb-a").value === $("#rb-b").value) throw new Error("Choose two different images: before and after");
        const bt = $("#rb-breaks").value.trim(), breaks = bt ? bt.split(/[\s,;]+/).filter(Boolean).map(Number) : null;
        if (breaks && (breaks.length !== 6 || breaks.some((x) => !Number.isFinite(x)))) throw new Error("Give 6 numbers for the class limits, or leave it empty");
        const a = img("rb-a"), b = img("rb-b");
        return { before: v.raster("rb-a"), after: v.raster("rb-b"), before_bands: a.bands, after_bands: b.bands, before_scale: a.scale, after_scale: b.scale,
                 breaks, min_post_nbr: $("#rb-stubble").checked ? +$("#rb-lim").value : null, name: $("#rb-name").value.trim() };
      }, ["rb-a", "rb-b"], (r) => `<b>${r.summary.burned_ha.toLocaleString()} ha burned</b> (${r.summary.burned_pct}% of the area).
        <div class="dist" style="margin-top:6px">${r.classes.filter((c) => c.pixels).map((c) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${c.color};margin-right:5px"></i>${esc(c.class)}</span><b>${fmt(c.area_ha, 1)} ha</b></div>`).join("")}</div>`);
    },
  });

  LF.tool({ id: "rwater", title: "Water mask", icon: "rwater", kinds: ["watermask"],
    clip: { "rw-area": { what: "mask covers" } },
    subtitle: "Open water from a multispectral image (Sentinel-2, Landsat …): AWEI, NDWI, MNDWI or WI2015 with clouds, shadow and snow masked, specks removed and a confidence; also evidence for the SAR flood map",
    panel: `<div class="card"><h2>Image ${tip("Any multispectral image with green and NIR bands (SWIR too for the better indices). Sentinel-2 and Landsat 8/9 band names are recognised (they number bands differently); otherwise choose the bands below.")}</h2>
      ${rasterSel("rw-img", "Multispectral image")}
      <details id="rw-bands-box"><summary class="hint" id="rw-bands-sum">Bands</summary><div class="grid3" id="rw-bands"></div></details></div>
      <div class="card"><h2>Water index</h2>
      <label>Index ${tip("Auto: AWEI no-shadow when the image has SWIR1 and SWIR2, else NDWI. On Sen1Floods11's hand-labelled flood chips (clear pixels): AWEI no-shadow 0.79 IoU, NDWI 0.79, AWEI shadow 0.77, WI2015 0.71, MNDWI 0.71. MNDWI misses turbid flood water and flooded vegetation.")} <select id="rw-index"><option value="auto">Auto (best for the bands)</option><option value="awei_nsh">AWEI no-shadow</option><option value="awei_sh">AWEI shadow (mountains, tall buildings)</option><option value="ndwi">NDWI (green, NIR)</option><option value="mndwi">MNDWI (green, SWIR1)</option><option value="wi2015">WI2015</option></select></label>
      <label>Threshold ${tip("0 (water above it) is best on most images. The automatic methods look only at the parts of the image that clearly hold land and water (tiles whose two-Gaussian fit separates, with water above 0; Martinis et al. 2009): where there are none, 0 is used. Global: one value for the image. Local: a value per pixel from its window (for images whose brightness changes across the scene). Fuzzy: a 0–1 membership as the confidence. On Sen1Floods11's validation chips (IoU): 0 → 0.78; Multi-Otsu 0.68, Huang 0.65, Otsu / Isodata / fuzzy c-means / S-function ≈ 0.62, Li 0.58, Triangle 0.57; local methods 0.35–0.48 (made for documents, not landscapes); Yen / Kapur 0.37.")} <select id="rw-thr"><option value="zero">0 (recommended)</option>
        <optgroup label="Global">${[["otsu", "Otsu"], ["multi_otsu", "Multi-Otsu"], ["li", "Li"], ["yen", "Yen"], ["kapur", "Kapur"], ["triangle", "Triangle"], ["isodata", "Isodata"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</optgroup>
        <optgroup label="Local (per pixel)">${[["niblack", "Niblack"], ["sauvola", "Sauvola"], ["wolf", "Wolf"], ["phansalkar", "Phansalkar"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</optgroup>
        <optgroup label="Fuzzy">${[["fcm", "Fuzzy c-means"], ["fuzzy", "Fuzzy threshold (Huang)"], ["membership", "Fuzzy membership (S-function)"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</optgroup></select></label>
      <div class="grid2 hidden" id="rw-local"><label>Window (pixels) <input type="number" id="rw-win" value="51" min="5" step="2"></label><label>k ${tip("Leave empty for each method's usual value (Niblack 0.2, Sauvola 0.2, Wolf 0.5, Phansalkar 0.25).")} <input type="number" id="rw-k" placeholder="usual" step="0.05"></label></div></div>
      <div class="card"><h2>Masking &amp; clean-up</h2>
      <label class="check"><input type="checkbox" id="rw-scl" checked> Clouds, shadow and snow from the image's SCL band ${tip("Sentinel-2 L2A scene classification: cloud shadow, clouds, cirrus and snow are masked (shown grey), not called land or water.")}</label>
      <label>Or a cloud mask layer ${tip("Any raster where non-zero = cloud (s2cloudless, Fmask …).")} <select id="rw-cloud"></select></label>
      <label class="check"><input type="checkbox" id="rw-dem"> Drop water on steep slopes (Copernicus DEM) ${tip("Mountain and cloud shadows can look like water; on slopes steeper than the limit they aren't water. Downloads the DEM for the area.")}</label>
      <div class="grid2"><label>Slope limit (°) <input type="number" id="rw-slope" value="10" min="1"></label><label>Smallest patch (pixels) ${tip("Water patches (and dry holes in water) smaller than this are removed: speckle-like noise.")} <input type="number" id="rw-min" value="9" min="1"></label></div>
      ${LF.html.area("rw-area", "Area", "Optional: the mask is cut to it.")}
      <label>Name <input type="text" id="rw-name" value="water_mask" maxlength="80"></label>
      ${runRow("rw", "Make water mask")}</div>`,
    setup(LF) {
      const { $, $$, api, esc, fmt, getLayer, layers, getClip } = LF;
      const ROLES = [["blue", "Blue"], ["green", "Green"], ["red", "Red"], ["nir", "NIR"], ["swir1", "SWIR 1"], ["swir2", "SWIR 2"]];
      async function guess() {
        const l = getLayer($("#rw-img").value);
        if (!l) { $("#rw-bands").innerHTML = ""; return; }
        let g;
        try { g = await api(`/api/raster/watermask/bands?path=${encodeURIComponent(l.path)}`); } catch { return; }
        const opts = (sel) => `<option value="">–</option>` + Array.from({ length: g.count }, (_, i) => `<option value="${i + 1}" ${sel === i + 1 ? "selected" : ""}>${i + 1}${g.names[i] ? ` · ${esc(g.names[i])}` : ""}</option>`).join("");
        $("#rw-bands").innerHTML = ROLES.map(([k, t]) => `<label>${t} <select data-role="${k}">${opts(g.bands[k])}</select></label>`).join("");
        const found = ROLES.filter(([k]) => g.bands[k]).map(([, t]) => t);
        $("#rw-bands-sum").textContent = `Bands: ${found.length ? found.join(", ") : "not recognised, choose them"} (${g.sensor})`;
        if (!g.bands.green) $("#rw-bands-box").open = true;
      }
      $("#rw-img").addEventListener("change", guess);
      $("#rw-thr").onchange = () => $("#rw-local").classList.toggle("hidden", !["niblack", "sauvola", "wolf", "phansalkar"].includes($("#rw-thr").value));
      const fillCloud = () => { const el = $("#rw-cloud"), w = el.value; el.innerHTML = `<option value="">(none)</option>` + layers.filter((l) => l.type === "raster" && l.path).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join(""); if (getLayer(w)) el.value = w; };
      const hooks = wire(LF, "rw", "/api/raster/watermask", (v) => ({
        path: v.raster("rw-img"), bands: Object.fromEntries($$("#rw-bands [data-role]").filter((s) => s.value).map((s) => [s.dataset.role, +s.value])),
        index: $("#rw-index").value, threshold: $("#rw-thr").value, window: +$("#rw-win").value || 51, k: $("#rw-k").value === "" ? null : +$("#rw-k").value, scl: $("#rw-scl").checked, cloud: getLayer($("#rw-cloud").value)?.path || null,
        dem: $("#rw-dem").checked ? "auto" : null, slope_max: +$("#rw-slope").value || 10, min_px: +$("#rw-min").value || 9, aoi: getClip("rw-area"),
        name: $("#rw-name").value.trim() || "water_mask" }), ["rw-img"], (r) => `<b>${r.areas_ha ? `${fmt(r.areas_ha.Water, 1)} ha of water` : `${r.pixels.Water.toLocaleString()} water pixels`}</b> (${r.water_pct_of_clear} % of the clear area) by ${esc(r.index)} &gt; ${r.threshold_varies ? `a per-pixel threshold (mean ${r.threshold})` : r.threshold} (${esc(r.threshold_source)}).
          ${r.pixels["Masked (cloud, shadow, snow)"] ? `<p class="hint" style="margin:4px 0 0">${r.pixels["Masked (cloud, shadow, snow)"].toLocaleString()} pixels masked (${esc(r.masking.join("; "))}): for those, the SAR flood map can fill in from radar.</p>` : ""}`);
      return { open(a) { hooks.open(a); fillCloud(); guess(); }, layersChanged() { hooks.layersChanged(); fillCloud(); } };
    },
  });

  // ---------------- Image features: local statistics, GLCM texture, morphology
  const bandTicks = (LF, sel, box, checkedAll = true) => {
    const { $, getLayer, esc } = LF;
    const l = getLayer($(`#${sel}`).value);
    const bands = l?.info?.bands || [];
    $(`#${box}`).innerHTML = bands.length > 1 ? `<div class="sf-ticks">${bands.map((b) => `<label class="check"><input type="checkbox" data-b="${b.index}" ${checkedAll || b.index === 1 ? "checked" : ""}> ${b.index}${b.description && b.description !== "Band " + b.index ? ` · ${esc(b.description)}` : ""}</label>`).join("")}</div>` : "";
  };
  const ticked = (LF, box) => LF.$$(`#${box} [data-b]:checked`).map((c) => +c.dataset.b);

  LF.tool({ id: "rlocal", title: "Local statistics", icon: "rlocal", kinds: ["localstats"],
    subtitle: "Per pixel, over its 3×3 … 31×31 neighbourhood: mean, median, std, variance, min, max, range, coefficient of variation, entropy, skewness, edges: features for SAR and optical classification",
    panel: `<div class="card"><h2>Image ${tip("Any raster. Each chosen band gets every chosen statistic for every window: one output band each. Linear SAR power is put in dB first.")}</h2>
      ${rasterSel("rl-img", "Raster")}<div id="rl-bands"></div></div>
      <div class="card"><h2>Windows ${tip("The neighbourhood, in pixels. Small windows keep detail; big ones describe texture and land cover (a 15 × 15 window at 10 m is 150 m).")}</h2>
      <div class="sf-ticks" style="grid-template-columns:repeat(4,1fr)">${[3, 5, 7, 9, 11, 15, 21, 31].map((w) => `<label class="check"><input type="checkbox" data-w="${w}" ${[3, 5, 7, 15].includes(w) ? "checked" : ""}> ${w}×${w}</label>`).join("")}</div></div>
      <div class="card"><h2>Statistics</h2><div class="sf-ticks" style="grid-template-columns:1fr 1fr">${[
        ["mean", "Mean", "μ = (1/N) Σ x over the window: the local level, smoother than the pixel itself."],
        ["median", "Median", "The middle value: like the mean but untouched by a few extreme pixels (speckle)."],
        ["std", "Standard deviation", "How much the window varies: texture. High at edges, in towns and forests; low on water and smooth fields."],
        ["variance", "Variance", "The standard deviation squared."],
        ["min", "Minimum", "The lowest value in the window."], ["max", "Maximum", "The highest value in the window."],
        ["range", "Range", "Maximum − minimum: contrast within the window."],
        ["cv", "Coefficient of variation", "σ / μ: variation relative to the level, the classic SAR heterogeneity measure (speckle alone gives about 1/√looks)."],
        ["entropy", "Entropy", "How disordered the window's values are (bits, from a 32-level histogram): 0 when uniform."],
        ["skewness", "Skewness", "Whether the window has a tail of bright (positive) or dark (negative) pixels."],
        ["gradient", "Gradient (Sobel)", "Edge strength, after smoothing to the window's scale."],
        ["laplacian", "Laplacian", "Second derivative (Laplacian of Gaussian): bright and dark blobs and ridges at the window's scale."]].map(([v, t, h], i) => `<label class="check"><input type="checkbox" data-st="${v}" ${v === "mean" || v === "std" ? "checked" : ""}> ${t} ${tip(h)}</label>`).join("")}</div>
      <label class="check"><input type="checkbox" id="rl-db" checked> SAR in dB ${tip("Linear SAR power (γ⁰ / σ⁰) is converted to dB before the statistics, as classifiers expect.")}</label>
      <label>Name <input type="text" id="rl-name" placeholder="automatic" maxlength="80"></label>${runRow("rl", "Compute")}</div>`,
    setup(LF) {
      const { $, $$ } = LF;
      $("#rl-img").addEventListener("change", () => bandTicks(LF, "rl-img", "rl-bands"));
      const h = wire(LF, "rl", "/api/raster/localstats", (v) => {
        const windows = $$("#tab-rlocal [data-w]:checked").map((c) => +c.dataset.w), stats = $$("#tab-rlocal [data-st]:checked").map((c) => c.dataset.st);
        if (!windows.length || !stats.length) throw new Error("Tick at least one window and one statistic");
        return { path: v.raster("rl-img"), bands: ticked(LF, "rl-bands").length ? ticked(LF, "rl-bands") : null, windows, stats, db: $("#rl-db").checked, name: $("#rl-name").value.trim() };
      }, ["rl-img"], (r) => `<b>${r.bands.length} feature bands</b>${r.converted_to_db.length ? ` (${LF.esc(r.converted_to_db.join(", "))} in dB)` : ""}.`);
      return { open(a) { h.open(a); bandTicks(LF, "rl-img", "rl-bands"); }, layersChanged() { h.layersChanged(); } };
    },
  });

  LF.tool({ id: "rglcm", title: "Texture (GLCM)", icon: "rglcm", kinds: ["glcm"],
    subtitle: "Grey-level co-occurrence texture per pixel (Haralick): contrast, dissimilarity, homogeneity, energy, ASM, correlation, entropy, mean, variance, averaged over four directions",
    panel: `<div class="card"><h2>Image ${tip("Usually SAR (VV, VH in dB) or a single optical band / index. Each band × feature is one output band.")}</h2>
      ${rasterSel("rg-img", "Raster")}<div id="rg-bands"></div></div>
      <div class="card"><h2>GLCM ${tip("For each pixel, how often grey level i sits next to level j in its window (both ways, at the distance, in 4 directions), then statistics of that matrix (Haralick 1973). Values are first put into a few grey levels between the image's 1st and 99th percentiles.")}</h2>
      <div class="grid3"><label>Window ${tip("Bigger windows: steadier texture, coarser detail. 7–11 is usual for Sentinel-1 at 10 m.")} <select id="rg-win">${[5, 7, 9, 11, 15, 21, 31].map((w) => `<option value="${w}" ${w === 7 ? "selected" : ""}>${w}×${w}</option>`).join("")}</select></label>
        <label>Distance ${tip("Pixel pairs this far apart.")} <input type="number" id="rg-dist" value="1" min="1" max="10"></label>
        <label>Grey levels ${tip("16 is a good balance; 32 / 64 show finer texture but make energy and entropy slower and noisier.")} <select id="rg-lev"><option>8</option><option selected>16</option><option>32</option><option>64</option></select></label></div>
      <div class="sf-ticks" style="grid-template-columns:1fr 1fr">${[
        ["contrast", "Contrast", "Σ P(i,j)(i − j)²: large local differences (edges, rough texture)."],
        ["dissimilarity", "Dissimilarity", "Σ P(i,j)|i − j|: like contrast, growing linearly."],
        ["homogeneity", "Homogeneity", "Σ P(i,j)/(1 + (i − j)²): high for smooth, uniform areas (water, bare fields)."],
        ["energy", "Energy", "√ASM: high when few grey-level pairs repeat (orderly texture)."],
        ["asm", "ASM", "Angular second moment Σ P(i,j)²."],
        ["correlation", "Correlation", "How linearly a pixel predicts its neighbour: high for smooth gradients and stripes (row crops)."],
        ["entropy", "Entropy", "−Σ P log P: disorder of the texture (towns, forests high)."],
        ["mean", "GLCM mean", "The mean grey level of the pairs."],
        ["variance", "GLCM variance", "The spread of the pairs' grey levels."]].map(([v, t, h]) => `<label class="check"><input type="checkbox" data-gf="${v}" ${["contrast", "homogeneity", "energy", "correlation", "entropy"].includes(v) ? "checked" : ""}> ${t} ${tip(h)}</label>`).join("")}</div>
      <label class="check"><input type="checkbox" id="rg-db" checked> SAR in dB</label>
      <label>Name <input type="text" id="rg-name" placeholder="automatic" maxlength="80"></label>${runRow("rg", "Compute texture")}</div>`,
    setup(LF) {
      const { $, $$ } = LF;
      $("#rg-img").addEventListener("change", () => bandTicks(LF, "rg-img", "rg-bands"));
      const h = wire(LF, "rg", "/api/raster/glcm", (v) => {
        const features = $$("[data-gf]:checked").map((c) => c.dataset.gf);
        if (!features.length) throw new Error("Tick at least one feature");
        return { path: v.raster("rg-img"), bands: ticked(LF, "rg-bands").length ? ticked(LF, "rg-bands") : null, window: +$("#rg-win").value, distance: +$("#rg-dist").value || 1,
                 levels: +$("#rg-lev").value, features, db: $("#rg-db").checked, name: $("#rg-name").value.trim() };
      }, ["rg-img"], (r) => `<b>${r.bands.length} texture bands</b>${r.converted_to_db.length ? ` (${LF.esc(r.converted_to_db.join(", "))} in dB)` : ""}.`);
      return { open(a) { h.open(a); bandTicks(LF, "rg-img", "rg-bands"); }, layersChanged() { h.layersChanged(); } };
    },
  });

  LF.tool({ id: "rmorph", title: "Morphology", icon: "rmorph", kinds: ["morphology"],
    subtitle: "Shape operations on masks and class maps (after a threshold or a classification): erosion, dilation, opening, closing, gradient, top-hat, black-hat; remove small objects, fill holes, majority filter",
    panel: `<div class="card"><h2>Raster ${tip("A mask (0 / 1), a class map (one class, or all with the majority filter), or a continuous band (grey-level morphology).")}</h2>
      ${rasterSel("rm2-img", "Raster")}
      <div class="grid2"><label>Band <input type="number" id="rm2-band" value="1" min="1"></label>
        <label>Only class value ${tip("For a class map: the class to operate on (e.g. 2 for water in a water mask); the other classes stay. Empty: the band as it is (binary when 0 / 1).")} <input type="number" id="rm2-val" placeholder="(whole band)" step="any"></label></div></div>
      <div class="card"><h2>Operation</h2>
      <label>Operation <select id="rm2-op"><optgroup label="Basic">${[["erosion", "Erosion: shrink objects"], ["dilation", "Dilation: grow objects"], ["opening", "Opening: remove small bits and thin bridges"], ["closing", "Closing: fill small gaps and holes"], ["gradient", "Morphological gradient: the outline"], ["tophat", "Top-hat: small bright details"], ["blackhat", "Black-hat: small dark details"]].map(([v, t]) => `<option value="${v}" ${v === "opening" ? "selected" : ""}>${t}</option>`).join("")}</optgroup>
        <optgroup label="Clean-up">${[["remove_small", "Remove small objects"], ["fill_holes", "Fill small holes"], ["majority", "Majority filter (smooth a class map)"], ["boundary", "Boundary (inner edge)"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</optgroup></select></label>
      <div class="grid3"><label>Shape ${tip("The structuring element: a disk is direction-neutral; a square follows the pixel grid; a cross only looks up / down / left / right.")} <select id="rm2-shape"><option value="disk">Disk</option><option value="square">Square</option><option value="cross">Cross</option></select></label>
        <label>Size (pixels) <input type="number" id="rm2-size" value="3" min="1" max="101"></label><label>Repeat <input type="number" id="rm2-it" value="1" min="1" max="20"></label></div>
      <label id="rm2-min-row" class="hidden">Smaller than (pixels) ${tip("Objects (or holes) with fewer pixels than this are removed (filled).")} <input type="number" id="rm2-min" value="50" min="1"></label>
      <label>Name <input type="text" id="rm2-name" placeholder="automatic" maxlength="80"></label>${runRow("rm2", "Apply")}</div>`,
    setup(LF) {
      const { $ } = LF;
      $("#rm2-op").onchange = () => $("#rm2-min-row").classList.toggle("hidden", !["remove_small", "fill_holes"].includes($("#rm2-op").value));
      return wire(LF, "rm2", "/api/raster/morphology", (v) => ({ path: v.raster("rm2-img"), band: +$("#rm2-band").value || 1, op: $("#rm2-op").value, shape: $("#rm2-shape").value,
        size: +$("#rm2-size").value || 3, iterations: +$("#rm2-it").value || 1, value: $("#rm2-val").value === "" ? null : +$("#rm2-val").value, min_px: +$("#rm2-min").value || 50,
        name: $("#rm2-name").value.trim() }), ["rm2-img"], (r) => `<b>${LF.esc(r.op)}</b> done.`);
    },
  });

  // ---------------- spatial structure: autocorrelation, edges, multi-scale features, superpixels, components, spatial CV
  const bandBox = (id) => `<label>Band <input type="number" id="${id}" value="1" min="1"></label>`;

  LF.tool({ id: "rautocorr", title: "Spatial autocorrelation (raster)", icon: "rautocorr", kinds: ["autocorrelation"],
    subtitle: "Whether similar values cluster in space: global Moran's I and Geary's C with significance, and maps of Local Moran's I clusters (hot, cold, outliers) and Getis-Ord Gi* hot spots",
    panel: `<div class="card"><h2>Raster ${tip("One band of any raster: an index (NDVI), SAR backscatter, a change map, yields … For points or polygons use Spatial statistics instead.")}</h2>
      ${rasterSel("ra-img", "Raster")}<div class="grid2">${bandBox("ra-band")}<label>Neighbours within (pixels) ${tip("1: the 8 surrounding pixels (queen contiguity). Larger radii look at broader patterns.")} <input type="number" id="ra-rad" value="1" min="1" max="25"></label></div>
      <label>Significance level ${tip("Local clusters are shown where p is below this (two-sided). With many pixels some are significant by chance: 0.01 is stricter.")} <select id="ra-alpha"><option value="0.05">0.05</option><option value="0.01">0.01</option><option value="0.001">0.001</option></select></label>
      <p class="hint">Moran's I = (n / W) · Σᵢ Σⱼ wᵢⱼ (xᵢ − x̄)(xⱼ − x̄) / Σᵢ (xᵢ − x̄)²: above −1/(n − 1) when similar values cluster, below when they alternate. Geary's C is below 1 for clustering.</p>
      <label class="check"><input type="checkbox" id="ra-db" checked> SAR in dB</label><label>Name <input type="text" id="ra-name" placeholder="automatic" maxlength="80"></label>${runRow("ra", "Measure")}</div>`,
    setup(LF) {
      const { $, esc } = LF;
      return wire(LF, "ra", "/api/raster/autocorrelation", (v) => ({ path: v.raster("ra-img"), band: +$("#ra-band").value || 1, radius: +$("#ra-rad").value || 1,
        alpha: +$("#ra-alpha").value, db: $("#ra-db").checked, name: $("#ra-name").value.trim() }), ["ra-img"], (r) => {
        const p = (x) => x == null ? "–" : x < 0.001 ? "< 0.001" : x.toFixed(3);
        return `<table class="kv"><tr><td>Moran's I</td><td><b>${r.moran_i}</b> (expected ${r.moran_expected}, z ${r.moran_z}, p ${p(r.moran_p)})</td></tr>
          <tr><td>Geary's C</td><td><b>${r.geary_c}</b> (z ${r.geary_z}, p ${p(r.geary_p)})</td></tr><tr><td>Pixels</td><td>${r.n.toLocaleString()}, neighbours within ${r.radius}</td></tr></table>
          <p class="hint" style="margin:4px 0 0">${r.moran_i > r.moran_expected && r.moran_p < 0.05 ? "Similar values cluster." : r.moran_i < r.moran_expected && r.moran_p < 0.05 ? "Neighbours tend to differ (a checkerboard-like pattern)." : "No significant spatial pattern."}</p>
          <div class="dist">${Object.entries(r.lisa_counts).map(([k, n]) => `<div style="grid-template-columns:minmax(0,2fr) auto"><span>${esc(k)}</span><b>${n.toLocaleString()}</b></div>`).join("")}</div>`;
      });
    },
  });

  LF.tool({ id: "redges", title: "Edges & boundaries", icon: "redges", kinds: ["edges"],
    subtitle: "Sobel (x, y, magnitude, direction), Canny edges, Laplacian of Gaussian, gradient magnitude and directional gradients: field boundaries, shorelines, roads",
    panel: `<div class="card"><h2>Raster</h2>${rasterSel("red-img", "Raster")}<div class="grid2">${bandBox("red-band")}<label>Scale σ (pixels) ${tip("Gaussian smoothing before the derivatives: 1 for fine edges, 2–3 for speckled SAR or noisy images, bigger for broad boundaries.")} <input type="number" id="red-sig" value="1" min="0" step="0.5"></label></div></div>
      <div class="card"><h2>Detectors</h2><div class="sf-ticks">${[["sobel", "Sobel: x, y, magnitude and direction", "First derivatives across and along the image; magnitude = edge strength, direction = which way the value rises."],
        ["canny", "Canny: thin, connected edges (0 / 1)", "Gradient, thinning to one pixel (non-maximum suppression), then strong edges plus the weaker ones connected to them (hysteresis)."],
        ["laplacian", "Laplacian of Gaussian", "Second derivative: zero-crossings at edges; blobs and ridges at the scale σ."],
        ["magnitude", "Gradient magnitude", "Edge strength only (included in Sobel)."],
        ["directional", "Directional gradients 0°, 45°, 90°, 135°", "The derivative along four directions: edges with a given orientation (e.g. field boundaries along a road)."]].map(([v, t, h]) => `<label class="check"><input type="checkbox" data-ed="${v}" ${v === "sobel" || v === "canny" ? "checked" : ""}> ${t} ${tip(h)}</label>`).join("")}</div>
      <div class="grid2"><label>Canny low ${tip("Fractions of the strongest gradient: weak edges above low are kept when they connect to strong ones above high.")} <input type="number" id="red-lo" value="0.1" step="0.05" min="0.01" max="0.95"></label><label>Canny high <input type="number" id="red-hi" value="0.2" step="0.05" min="0.02" max="1"></label></div>
      <label class="check"><input type="checkbox" id="red-db" checked> SAR in dB</label><label>Name <input type="text" id="red-name" placeholder="automatic" maxlength="80"></label>${runRow("red", "Detect edges")}</div>`,
    setup(LF) {
      const { $, $$ } = LF;
      return wire(LF, "red", "/api/raster/edges", (v) => {
        const which = $$("[data-ed]:checked").map((c) => c.dataset.ed);
        if (!which.length) throw new Error("Tick at least one detector");
        return { path: v.raster("red-img"), band: +$("#red-band").value || 1, which, sigma: +$("#red-sig").value, low: +$("#red-lo").value, high: +$("#red-hi").value, db: $("#red-db").checked, name: $("#red-name").value.trim() };
      }, ["red-img"], (r) => `<b>${r.bands.length} edge bands</b>.`);
    },
  });

  LF.tool({ id: "rmulti", title: "Multi-scale features", icon: "rmulti", kinds: ["multiscale"],
    subtitle: "Gaussian scale space at several scales: smoothed image, gradient, Laplacian of Gaussian, difference of Gaussians, local mean and std, as one feature stack for classification",
    panel: `<div class="card"><h2>Raster ${tip("Each chosen band × scale × feature is one output band; stack it with the image for Classical ML or deep learning.")}</h2>${rasterSel("rms-img", "Raster")}<div id="rms-bands"></div></div>
      <div class="card"><h2>Scales σ (pixels) ${tip("The Gaussian's standard deviation. Doubling scales (1, 2, 4, 8) cover fine to coarse structure; at 10 m pixels σ 8 ≈ 80 m.")}</h2>
      <div class="sf-ticks" style="grid-template-columns:repeat(4,1fr)">${[0.5, 1, 2, 4, 8, 16].map((s2) => `<label class="check"><input type="checkbox" data-sg="${s2}" ${[1, 2, 4, 8].includes(s2) ? "checked" : ""}> ${s2}</label>`).join("")}</div>
      <div class="sf-ticks">${[["smooth", "Smoothed (Gaussian)"], ["gradient", "Gradient magnitude"], ["log", "Laplacian of Gaussian (scale-normalised)"], ["dog", "Difference of Gaussians (between scales)"], ["mean", "Local mean"], ["std", "Local standard deviation"]].map(([v, t]) => `<label class="check"><input type="checkbox" data-mf="${v}" ${v !== "mean" ? "checked" : ""}> ${t}</label>`).join("")}</div>
      <label class="check"><input type="checkbox" id="rms-db" checked> SAR in dB</label><label>Name <input type="text" id="rms-name" placeholder="automatic" maxlength="80"></label>${runRow("rms", "Compute")}</div>`,
    setup(LF) {
      const { $, $$ } = LF;
      $("#rms-img").addEventListener("change", () => bandTicks(LF, "rms-img", "rms-bands", false));
      const h = wire(LF, "rms", "/api/raster/multiscale", (v) => {
        const sigmas = $$("[data-sg]:checked").map((c) => +c.dataset.sg), features = $$("[data-mf]:checked").map((c) => c.dataset.mf);
        if (!sigmas.length || !features.length) throw new Error("Tick at least one scale and one feature");
        return { path: v.raster("rms-img"), bands: ticked(LF, "rms-bands").length ? ticked(LF, "rms-bands") : null, sigmas, features, db: $("#rms-db").checked, name: $("#rms-name").value.trim() };
      }, ["rms-img"], (r) => `<b>${r.bands.length} feature bands</b>.`);
      return { open(a) { h.open(a); bandTicks(LF, "rms-img", "rms-bands", false); }, layersChanged() { h.layersChanged(); } };
    },
  });

  LF.tool({ id: "rslic", title: "Superpixels (SLIC)", icon: "rslic", kinds: ["slic"],
    subtitle: "Groups pixels into small homogeneous segments (SLIC) instead of treating each pixel alone: segment ids, boundaries, the mean image per segment and polygons with their means, for object-based classification",
    panel: `<div class="card"><h2>Raster ${tip("Bands to segment on (all by default). SAR speckle averages out within a segment, so segment means classify more cleanly than single pixels.")}</h2>${rasterSel("rsl-img", "Raster")}<div id="rsl-bands"></div></div>
      <div class="card"><h2>SLIC ${tip("Achanta et al. 2012: k-means in position + band values, each centre searching only nearby, so it scales to large images. Bands are standardised first.")}</h2>
      <div class="grid3"><label>Segments (about) ${tip("How many superpixels: area ÷ segments = their typical size. Aim for segments smaller than the objects you map (fields, buildings).")} <input type="number" id="rsl-n" value="1000" min="4"></label>
        <label>Compactness ${tip("Low (0.1–0.3): segments follow the image's edges; high (1–3): squarer, more regular. 0.3 suits most images.")} <input type="number" id="rsl-c" value="0.3" step="0.1" min="0.01"></label>
        <label>Smoothing σ ${tip("Gaussian smoothing before clustering. Empty: 2 for SAR (speckle), 1 otherwise.")} <input type="number" id="rsl-sig" placeholder="auto" step="0.5" min="0"></label></div>
      <label class="check"><input type="checkbox" id="rsl-poly" checked> Also as polygons with the segment means ${tip("A vector layer: one polygon per segment with its pixels, hectares and each band's mean: ready for attribute-based classification.")}</label>
      <label class="check"><input type="checkbox" id="rsl-db" checked> SAR in dB</label><label>Name <input type="text" id="rsl-name" placeholder="automatic" maxlength="80"></label>${runRow("rsl", "Segment")}</div>`,
    setup(LF) {
      const { $ } = LF;
      $("#rsl-img").addEventListener("change", () => bandTicks(LF, "rsl-img", "rsl-bands"));
      const h = wire(LF, "rsl", "/api/raster/slic", (v) => ({ path: v.raster("rsl-img"), bands: ticked(LF, "rsl-bands").length ? ticked(LF, "rsl-bands") : null,
        n_segments: +$("#rsl-n").value || 1000, compactness: +$("#rsl-c").value || 0.3, sigma: $("#rsl-sig").value === "" ? null : +$("#rsl-sig").value, polygons: $("#rsl-poly").checked,
        db: $("#rsl-db").checked, name: $("#rsl-name").value.trim() }), ["rsl-img"], (r) => `<b>${r.segments.toLocaleString()} superpixels</b> (compactness ${r.compactness}, smoothing σ ${r.sigma}).`);
      return { open(a) { h.open(a); bandTicks(LF, "rsl-img", "rsl-bands"); }, layersChanged() { h.layersChanged(); } };
    },
  });

  LF.tool({ id: "rcomp", title: "Connected components", icon: "rcomp", kinds: ["components"],
    subtitle: "Separate objects in a mask or one class (water bodies, fields, buildings): an id per object, a table of their size, area and centre, and polygons",
    panel: `<div class="card"><h2>Mask or class map</h2>${rasterSel("rcp-img", "Raster")}
      <div class="grid2">${bandBox("rcp-band")}<label>Class value ${tip("The class to separate into objects (e.g. 2 for water in a water mask). Empty: every non-zero pixel.")} <input type="number" id="rcp-val" placeholder="(non-zero)" step="any"></label></div>
      <div class="grid2"><label>Connectivity ${tip("8: diagonal neighbours join (a diagonal line is one object). 4: only sides touch.")} <select id="rcp-conn"><option value="8">8 (with diagonals)</option><option value="4">4 (sides only)</option></select></label>
        <label>Smallest object (pixels) <input type="number" id="rcp-min" value="1" min="1"></label></div>
      <label class="check"><input type="checkbox" id="rcp-poly" checked> Also as polygons</label><label>Name <input type="text" id="rcp-name" placeholder="automatic" maxlength="80"></label>${runRow("rcp", "Find objects")}</div>`,
    setup(LF) {
      const { $ } = LF;
      return wire(LF, "rcp", "/api/raster/components", (v) => ({ path: v.raster("rcp-img"), band: +$("#rcp-band").value || 1, value: $("#rcp-val").value === "" ? null : +$("#rcp-val").value,
        connectivity: +$("#rcp-conn").value, min_px: +$("#rcp-min").value || 1, polygons: $("#rcp-poly").checked, name: $("#rcp-name").value.trim() }), ["rcp-img"],
        (r) => `<b>${r.count.toLocaleString()} objects</b>${r.total_ha != null ? `, ${LF.fmt(r.total_ha, 1)} ha in all` : ""}; largest ${r.largest_px.toLocaleString()} px, median ${r.median_px.toLocaleString()} px.`);
    },
  });

  LF.tool({ id: "rspcv", title: "Spatial cross-validation", icon: "rspcv", kinds: ["spatialcv"],
    subtitle: "How much a random train / test split overstates a map's accuracy: the same model scored with random k-fold and with spatial blocks of several sizes, and a correlogram suggesting the block size",
    panel: `<div class="card"><h2>Data ${tip("The image (or feature stack) you classify and its ground truth. Neighbouring pixels are near copies: a random split tests on pixels next to training ones; spatial blocks test on new places.")}</h2>
      ${rasterSel("rv-img", "Image / feature stack")}<div id="rv-bands"></div>
      <label>Ground truth <select id="rv-gt"></select></label><div id="rv-gt-v" class="hidden"><label>Class attribute <select id="rv-field"></select></label></div></div>
      <div class="card"><h2>Validation</h2>
      <div class="grid2"><label>Model <select id="rv-model"><option value="lgbm">LightGBM</option><option value="rf">Random Forest</option><option value="xgb">XGBoost</option></select></label>
        <label>Folds <input type="number" id="rv-folds" value="5" min="2" max="10"></label></div>
      <label>Block sizes (m) ${tip("Squares left out together. Compare: the score falls as blocks grow until they exceed the range of spatial autocorrelation (shown by the correlogram).")} <input type="text" id="rv-blocks" value="250, 500, 1000, 2000, 4000"></label>
      <label>Pixels per class <input type="number" id="rv-pc" value="2000" min="50" step="500"></label>${runRow("rv", "Compare")}</div>`,
    setup(LF) {
      const { $, layers, getLayer, esc } = LF;
      $("#rv-img").addEventListener("change", () => bandTicks(LF, "rv-img", "rv-bands"));
      const fillGt = () => {
        const w = $("#rv-gt").value;
        const gts = layers.filter((l) => (l.type === "vector" && l.geojson?.features?.length) || (l.type === "raster" && l.path));
        $("#rv-gt").innerHTML = `<option value="">Choose…</option>` + gts.map((l) => `<option value="${esc(l.id)}">${l.type === "vector" ? "▢" : "▦"} ${esc(l.name)}</option>`).join("");
        if (getLayer(w)) $("#rv-gt").value = w;
      };
      $("#rv-gt").onchange = () => {
        const l = getLayer($("#rv-gt").value);
        $("#rv-gt-v").classList.toggle("hidden", l?.type !== "vector");
        if (l?.type === "vector") $("#rv-field").innerHTML = [...new Set(l.geojson.features.flatMap((f) => Object.keys(f.properties || {})))].map((k) => `<option>${esc(k)}</option>`).join("");
      };
      const h = wire(LF, "rv", "/api/raster/spatialcv", (v) => {
        const g = getLayer($("#rv-gt").value);
        if (!g) throw new Error("Choose the ground truth");
        const blocks_m = $("#rv-blocks").value.split(/[,\s]+/).map(Number).filter((x) => x > 0);
        return { path: v.raster("rv-img"), bands: ticked(LF, "rv-bands").length ? ticked(LF, "rv-bands") : null, model: $("#rv-model").value, folds: +$("#rv-folds").value || 5,
          blocks_m, per_class: +$("#rv-pc").value || 2000,
          ground_truth: g.type === "raster" ? { type: "raster", path: g.path, band: 1 } : { type: "vector", geojson: g.geojson, field: $("#rv-field").value || null } };
      }, ["rv-img"], (r) => `<table class="kv"><tr><td></td><td><b>OA</b></td><td><b>F1</b></td><td></td></tr>${r.table.map((t) => `<tr><td>${esc(t.method)}</td><td>${t.oa ?? "–"}</td><td>${t.f1 ?? "–"}</td><td class="hint">${esc(t.note || "")}</td></tr>`).join("")}</table>
          ${r.optimism_f1 != null ? `<p style="margin:6px 0 0">Random k-fold overstates macro F1 by <b>${(100 * r.optimism_f1).toFixed(1)} points</b> compared with the largest blocks.</p>` : ""}
          ${r.correlogram ? `<div class="home-label">Correlogram (features' Moran's I by distance)</div><div class="dist">${r.correlogram.map((c) => `<div style="grid-template-columns:80px minmax(0,1fr) auto"><span>${c.distance_m} m</span><span><span style="display:inline-block;height:8px;width:${Math.max(0, Math.round(120 * c.moran_i))}px;background:var(--accent);border-radius:2px"></span></span><b>${c.moran_i}</b></div>`).join("")}</div>
            ${r.suggested_block_m ? `<p class="hint" style="margin:4px 0 0">Similarity fades below 0.1 within about ${r.suggested_block_m / 2} m: use blocks of at least <b>${r.suggested_block_m} m</b>.</p>` : ""}
            ${r.range_note ? `<p class="hint" style="margin:4px 0 0">${esc(r.range_note)}.</p>` : ""}` : ""}`);
      return { open(a) { h.open(a); fillGt(); bandTicks(LF, "rv-img", "rv-bands"); }, layersChanged() { h.layersChanged(); fillGt(); } };
    },
  });
})();
