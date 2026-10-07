  // Layer metadata window.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ metadata
  let mdData = null;
  const kvTable = (rows) => `<table class="md-kv">${rows.filter((r) => r && r[1] !== undefined && r[1] !== null && r[1] !== "").map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(String(v))}</td></tr>`).join("")}</table>`;
  const crd = (v, d = 3) => v == null ? "" : (+v).toLocaleString(undefined, { maximumFractionDigits: d, useGrouping: false });
  const num = (v, d = 4) => v == null ? "" : Math.abs(v) >= 1e9 || (Math.abs(v) < 1e-3 && v !== 0) ? (+v).toExponential(3) : (+v.toFixed(d)).toLocaleString(undefined, { maximumFractionDigits: d });
  async function openMetadata(l) {
    $("#md-title").textContent = `Metadata · ${l.name}`;
    $("#md-body").innerHTML = `<p class="hint">Reading…</p>`;
    $("#dlg-lmeta").showModal();
    try {
      if (l.type === "raster") {
        const m = mdData = await api(`/api/rasters/metadata?path=${encodeURIComponent(l.path)}`);
        const bb = m.bounds, ll = m.bounds_lonlat, u = m.units === "metre" ? " m" : m.units === "degree" ? "°" : "";
        $("#md-body").innerHTML =
          (m.product ? `<h4>Sentinel product</h4>` + kvTable([["Product", m.product.product], ["Satellite", m.product.satellite], ["Processing level", m.product.level],
            ["Acquired", m.product.date && `${m.product.date} ${m.product.time_utc} UTC`], ["Tile", m.product.tile], ["Relative orbit", m.product.relative_orbit], ["Processing baseline", m.product.processing_baseline]]) : "") +
          `<h4>File</h4>` + kvTable([["Layer name", l.name], ["File", m.file], ["Format", m.driver + (m.driver === "VRT" ? ` (virtual, reads ${m.sources} source files)` : "")],
            ["File size", m.driver === "VRT" ? "" : `${num(m.size_mb, 2)} MB`], ["Compression", m.compression], ["Interleave", m.interleave],
            ["Tiles / blocks", m.block_size && `${m.block_size[1]} × ${m.block_size[0]} px${m.tiled ? " (tiled)" : " (strips)"}`], ["Overviews", m.overviews?.length ? m.overviews.map((o) => o + "×").join(", ") : "none"]]) +
          `<h4>Image</h4>` + kvTable([["Size", `${m.width.toLocaleString()} × ${m.height.toLocaleString()} px · ${m.count} band${m.count > 1 ? "s" : ""}`], ["Data type", m.dtype], ["No-data value", m.nodata ?? "none"],
            ["Band names", l.info?.embedding ? `Embedding: ${l.info.embedding.dims} dimensions (${l.info.embedding.first} … ${l.info.embedding.last}), all kept${l.render?.pca ? "; shown as a colour view (PCA of all bands)" : ""}` : m.band_map_source], ["Scale / offset used", (l.scale ?? 1) !== 1 || (l.offset ?? 0) !== 0 ? `× ${l.scale} + ${l.offset}` : "none (values as stored)"]]) +
          `<h4>Coordinate system &amp; extent</h4>` + kvTable([["CRS", `${m.crs_name || ""}${m.epsg ? ` (EPSG:${m.epsg})` : m.crs ? ` (${m.crs})` : ""}`], ["Units", m.units],
            ["Pixel size", `${crd(m.pixel_size[0], 6)} × ${crd(m.pixel_size[1], 6)}${u}`], ["Area covered", m.units === "metre" ? `${num((bb.right - bb.left) / 1000, 2)} × ${num((bb.top - bb.bottom) / 1000, 2)} km` : ""],
            ["Upper-left corner", `${crd(m.origin[0])}, ${crd(m.origin[1])}`], ["Extent (left, bottom, right, top)", [bb.left, bb.bottom, bb.right, bb.top].map((v) => crd(v)).join(", ")],
            ["Extent (lon / lat)", ll.west != null ? `W ${crd(ll.west, 5)} · S ${crd(ll.south, 5)} · E ${crd(ll.east, 5)} · N ${crd(ll.north, 5)}` : ""]]) +
          `<h4>Bands</h4><p class="hint" style="margin:0 0 6px">${esc(m.stats_note)}; raw stored values.</p><div class="md-tbl-wrap"><table class="md-tbl"><thead><tr><th>#</th><th>Name</th><th>Used as</th><th>Type</th><th>No-data</th><th>Min</th><th>Max</th><th>Mean</th><th>Std</th><th>2 %</th><th>98 %</th><th>Valid</th><th>Colours</th></tr></thead><tbody>` +
          m.bands.map((b) => `<tr><td>${b.index}</td><td>${esc(b.description || "–")}</td><td>${esc(b.mapped_as ? `${b.mapped_as}${BAND_NICE[b.mapped_as] && BAND_NICE[b.mapped_as] !== b.mapped_as ? " · " + BAND_NICE[b.mapped_as] : ""}` : "–")}</td><td>${esc(b.dtype)}</td><td>${esc(String(b.nodata ?? "–"))}</td>` +
            ["min", "max", "mean", "std", "p2", "p98"].map((k) => `<td class="num">${num(b[k])}</td>`).join("") + `<td class="num">${b.valid_pct != null ? num(b.valid_pct, 1) + " %" : ""}</td><td>${b.has_colormap ? "colour table" : esc(b.color === "undefined" ? "–" : b.color.toLowerCase())}</td></tr>`).join("") +
          `</tbody></table></div>` +
          (Object.keys(m.tags || {}).length ? `<h4>Tags</h4>` + kvTable(Object.entries(m.tags)) : "") +
          (m.bands.some((b) => Object.keys(b.tags).length) ? `<h4>Band tags</h4>` + kvTable(m.bands.flatMap((b) => Object.entries(b.tags).map(([k, v]) => [`Band ${b.index} · ${k}`, v]))) : "");
      } else {
        const fs = l.geojson?.features || [];
        const types = {};
        fs.forEach((f) => { const t = f.geometry?.type || "none"; types[t] = (types[t] || 0) + 1; });
        let w = 180, s = 90, e = -180, n = -90;
        const walk = (c) => typeof c[0] === "number" ? (w = Math.min(w, c[0]), e = Math.max(e, c[0]), s = Math.min(s, c[1]), n = Math.max(n, c[1])) : c.forEach(walk);
        fs.forEach((f) => f.geometry?.coordinates && walk(f.geometry.coordinates));
        const keys = [...new Set(fs.flatMap((f) => Object.keys(f.properties || {})))].filter((k) => !k.startsWith("_"));
        const fields = keys.map((k) => {
          const vals = fs.map((f) => f.properties?.[k]).filter((v) => v !== null && v !== undefined && v !== "");
          const nums = vals.filter((v) => typeof v === "number");
          const type = !vals.length ? "empty" : nums.length === vals.length ? (nums.every(Number.isInteger) ? "integer" : "decimal") : vals.every((v) => typeof v === "boolean") ? "true / false" : "text";
          const uniq = new Set(vals.map(String));
          const range = nums.length === vals.length && nums.length ? `${num(Math.min(...nums))} – ${num(Math.max(...nums))}` : [...uniq].slice(0, 4).join(", ") + (uniq.size > 4 ? " …" : "");
          return { k, type, filled: vals.length, uniq: uniq.size, range };
        });
        mdData = { name: l.name, features: fs.length, geometry_types: types, crs: "EPSG:4326", bounds_lonlat: { west: w, south: s, east: e, north: n }, fields };
        $("#md-body").innerHTML = `<h4>Layer</h4>` + kvTable([["Layer name", l.name], ["Features", fs.length.toLocaleString()],
            ["Geometry", Object.entries(types).map(([t, c]) => `${t} (${c})`).join(", ")], ["CRS", "EPSG:4326 (WGS 84, longitude / latitude)"],
            ["Extent (lon / lat)", fs.length ? `W ${crd(w, 5)} · S ${crd(s, 5)} · E ${crd(e, 5)} · N ${crd(n, 5)}` : ""],
            l.samples ? ["Kind", `Training samples · ${(l.classes || []).length} classes`] : null]) +
          `<h4>Fields (${fields.length})</h4>` + (fields.length ? `<div class="md-tbl-wrap"><table class="md-tbl"><thead><tr><th>Field</th><th>Type</th><th>Filled</th><th>Distinct</th><th>Range / examples</th></tr></thead><tbody>` +
            fields.map((f) => `<tr><td>${esc(f.k)}</td><td>${f.type}</td><td class="num">${f.filled.toLocaleString()}</td><td class="num">${f.uniq.toLocaleString()}</td><td>${esc(f.range)}</td></tr>`).join("") + `</tbody></table></div>` : `<p class="hint">No attributes.</p>`);
      }
    } catch (e) { $("#md-body").innerHTML = `<div class="warn err">${esc(e.message)}</div>`; }
  }
  $("#md-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(mdData, null, 1)); toast("Metadata copied"); } catch { toast("Copying isn't allowed here", true); }
  };

  function openProps(l) {
    if (!l) return toast("Select a layer in Contents first", true);
    state.propsLayer = l;
    $("#lp-title").textContent = `Properties · ${l.name}`;
    $("#lp-name").value = l.name;
    $("#lp-opacity").value = Math.round(l.opacity * 100);
    $("#lp-raster").classList.toggle("hidden", l.type !== "raster");
    $("#lp-vector").classList.toggle("hidden", l.type !== "vector");
    const info = [];
    if (l.type === "raster") {
      const r = l.render || {}, bm = l.band_map || {}, c = state.catalog;
      const comps = Object.entries(c.composites).filter(([, v]) => v.bands.every((b) => b in bm));
      let opts = "";
      if (r.index || r.formula) opts += `<optgroup label="Index"><option value="keep">${esc(r.index || "Formula: " + r.formula)}</option></optgroup>`;
      const nb = l.info?.count || 0;
      if (nb >= 3) opts += `<optgroup label="Colour view"><option value="p:">Colour view: PCA of all ${nb} bands${l.info?.embedding ? " (embedding)" : ""}</option></optgroup>`;
      if (nb >= 3) {
        const combos = [[1, 2, 3], [2, 3, 1], [1, 3, 2], [3, 2, 1]].filter((c) => c.every((b) => b <= nb));
        if (r.rgb && !combos.some((c) => c.join() === r.rgb.join())) combos.unshift(r.rgb);
        const nm = (b) => l.info.bands[b - 1]?.description || "Band " + b;
        opts += `<optgroup label="RGB of bands">${combos.map((c) => `<option value="r:${c.join(",")}">${c.map(nm).map(esc).join(" / ")}</option>`).join("")}</optgroup>`;
      }
      if (comps.length) opts += `<optgroup label="Band combination">${comps.map(([k, v]) => `<option value="c:${k}">${esc(v.title)}</option>`).join("")}</optgroup>`;
      opts += `<optgroup label="Single band">${(l.info?.bands || []).map((b) => `<option value="b:${b.index}">Band ${b.index}${b.description !== "Band " + b.index ? " · " + esc(b.description) : ""}</option>`).join("")}</optgroup>`;
      $("#lp-display").innerHTML = opts;
      $("#lp-display").value = r.index || r.formula ? "keep" : r.pca ? "p:" : r.composite ? `c:${r.composite}` : r.rgb ? `r:${r.rgb.join(",")}` : `b:${r.band}`;
      $("#lp-cmap").innerHTML = `<option value="">Default</option>` + Object.keys(c.colormaps).map((k) => `<option ${r.cmap === k ? "selected" : ""}>${k}</option>`).join("");
      $("#lp-stretch").value = r.stretch || "fixed";
      $("#lp-vmin").value = l.legend?.vmin != null ? +l.legend.vmin.toFixed(4) : "";
      $("#lp-vmax").value = l.legend?.vmax != null ? +l.legend.vmax.toFixed(4) : "";
      syncPropsUi();
      if (l.info) info.push(["File", l.path], ["Size", `${l.info.width} × ${l.info.height} px · ${l.info.count} bands · ${/\.vrt$/i.test(l.path) ? "virtual (VRT over the original product)" : fmt(l.info.size_mb) + " MB"}`],
        ["CRS", l.info.crs], ["Pixel size", `${fmt(l.info.res[0], 2)} × ${fmt(l.info.res[1], 2)}`], ["Data type", l.info.dtype]);
    } else if (l.type === "vector") {
      $("#lp-color").value = l.color || "#2563eb";
      const keys = [...new Set(l.geojson.features.slice(0, 500).flatMap((f) => Object.keys(f.properties || {})))];
      $("#lp-field").innerHTML = keys.map((k) => `<option>${esc(k)}</option>`).join("") || `<option value="">(no fields)</option>`;
      $("#lp-sym").value = l.symbology?.mode || "single";
      if (l.symbology?.field && keys.includes(l.symbology.field)) $("#lp-field").value = l.symbology.field;
      else {   // a sensible first field: a class / type text field, else the first number
        const sample = l.geojson.features.slice(0, 200).map((f) => f.properties || {});
        $("#lp-field").value = keys.find((k) => /^(class|type|landuse|building|highway|crop|name|majority_class)$/i.test(k)) || keys.find((k) => sample.some((p) => typeof p[k] === "number")) || keys[0] || "";
      }
      $("#lp-classes").value = l.symbology?.classes || 5;
      $("#lp-method").value = l.symbology?.method || "quantile";
      syncSymUi(l.symbology?.ramp);
      info.push(["Features", l.geojson.features.length], ["CRS", "EPSG:4326 (WGS 84)"]);
    } else info.push(["Type", "Preview image (PNG)"]);
    $("#lp-info").innerHTML = info.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
    $("#dlg-lprops").showModal();
  }
  function syncSymUi(ramp) {
    const l = state.propsLayer, mode = $("#lp-sym").value;
    $("#lp-color-row").classList.toggle("hidden", mode !== "single");
    $("#lp-sym-opts").classList.toggle("hidden", mode === "single");
    $("#lp-classes-row").classList.toggle("hidden", mode !== "graduated");
    $("#lp-method-row").classList.toggle("hidden", mode !== "graduated");
    const cur = ramp ?? $("#lp-ramp").value;
    $("#lp-ramp").innerHTML = (mode === "categories" ? `<option value="">Distinct colours</option>` : "") + Object.keys(RAMPS).map((k) => `<option>${k}</option>`).join("");
    if ([...$("#lp-ramp").options].some((o) => o.value === cur)) $("#lp-ramp").value = cur;
    if (mode === "single" || !l?.geojson || !$("#lp-field").value) { $("#lp-sym-preview").innerHTML = ""; return null; }
    try {
      const sym = makeSymbology(l, mode, $("#lp-field").value, { ramp: $("#lp-ramp").value, classes: +$("#lp-classes").value, method: $("#lp-method").value });
      $("#lp-sym-preview").innerHTML = sym.legend.map((c) => `<div><i style="background:${esc(c.color)}"></i><span>${esc(c.label)}</span><span>${c.n}</span></div>`).join("");
      return sym;
    } catch (e) { $("#lp-sym-preview").innerHTML = `<p class="hint" style="color:var(--warn)">${esc(e.message)}</p>`; return null; }
  }
  ["#lp-sym", "#lp-field", "#lp-ramp", "#lp-classes", "#lp-method"].forEach((s) => $(s).addEventListener("change", () => syncSymUi()));
  function syncPropsUi() {
    const v = $("#lp-display").value;
    const rgbLike = v.startsWith("c:") || v.startsWith("r:") || v === "p:";
    $("#lp-style").classList.toggle("hidden", rgbLike);
    $("#lp-range").classList.toggle("hidden", rgbLike || $("#lp-stretch").value !== "custom");
  }
  $("#lp-display").onchange = syncPropsUi;
  $("#lp-stretch").onchange = syncPropsUi;
  $("#lp-apply").onclick = async () => {
    const l = state.propsLayer;
    if (!l) return;
    l.name = $("#lp-name").value.trim() || l.name;
    setOpacity(l, $("#lp-opacity").value / 100);
    if (l.type === "vector") {
      l.color = $("#lp-color").value;
      if ($("#lp-sym").value === "single") delete l.symbology;
      else { const sym = syncSymUi(); if (sym) l.symbology = sym; }
      l.leaflet?.setStyle((f) => vecStyle(l, f));
      l.leaflet?.eachLayer((m) => m.setStyle && m.feature && m.setStyle(vecStyle(l, m.feature)));
    }
    $("#dlg-lprops").close();
    if (l.type === "raster") {
      const v = $("#lp-display").value, style = { stretch: $("#lp-stretch").value, cmap: $("#lp-cmap").value || null };
      if (style.stretch === "custom") { style.vmin = parseFloat($("#lp-vmin").value); style.vmax = parseFloat($("#lp-vmax").value); }
      if (v === "p:") l.render = { pca: true, stretch: "auto" };
      else if (v.startsWith("c:")) l.render = { composite: v.slice(2) };
      else if (v.startsWith("r:")) l.render = { rgb: v.slice(2).split(",").map(Number) };
      else if (v.startsWith("b:")) l.render = { band: +v.slice(2), ...style };
      else l.render = { index: l.render.index, formula: l.render.formula, ...style };
      try { await renderRaster(l); } catch (e) { toast(e, true); }
      if (an.resultId === l.id && l.legend?.kind === "continuous") showResult(l.legend);
    }
    renderContents();
    saveLayers();
  };
