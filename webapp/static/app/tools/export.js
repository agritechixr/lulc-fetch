  // Export data tool and the export dialog: GeoTIFF, PNG, Shapefile, GeoPackage (one file, several layers), GeoJSON, KML.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ export dialog
  const safeName = (s) => String(s).replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "layer";
  // Export lives in the "Export data" tool; right-click ▸ Export opens it with that layer selected.
  function openExport(l) {
    if (!l && !layers.length) return toast("Add a layer to Contents first", true);
    switchTool("export");
    refreshExportLayers(l?.id);
    renderExportForm();
  }
  const exportable = () => layers.filter((l) => l.type !== "tiles" && l.type !== "unplaced");   // online layers stay on their service
  function refreshExportLayers(selectId) {
    const sel = $("#lx-layer"), cur = selectId || sel.value || selectedId, list = exportable();
    sel.innerHTML = list.length ? list.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("")
      : `<option value="">No layers in Contents</option>`;
    if (cur && list.some((l) => l.id === cur)) sel.value = cur;
    if ($('input[name="lxf"]:checked')?.value === "gpkg") renderGpkgLayers();
  }
  // GeoPackage: the other vector layers that go into the same file (ticked ones are remembered while the tool is open)
  const gpkgPick = new Set();
  function renderGpkgLayers() {
    const l = exportLayer(), others = layers.filter((x) => x.type === "vector" && x.id !== l?.id && x.geojson?.features?.length);
    const box = $("#lx-gpkg");
    box.innerHTML = others.length ? `<b>Also in this GeoPackage</b> <span class="small"><a href="#" data-gp-all>all</a> · <a href="#" data-gp-none>none</a></span>
      <div class="lx-gpkg-list">${others.map((x) => `<label class="inline"><input type="checkbox" value="${esc(x.id)}" ${gpkgPick.has(x.id) ? "checked" : ""}> ${esc(x.name)}
        <small>${x.geojson.features.length.toLocaleString()} features</small></label>`).join("")}</div>
      <p class="hint" style="margin-top:4px">Each layer becomes a table of the one .gpkg file, with its attributes (WGS 84).</p>`
      : `<p class="hint" style="margin:0">Only this layer: add more vector layers to Contents to save them all in one GeoPackage.</p>`;
    $$("input[type=checkbox]", box).forEach((c) => c.onchange = () => c.checked ? gpkgPick.add(c.value) : gpkgPick.delete(c.value));
    const all = (on) => (e) => { e.preventDefault(); $$("input[type=checkbox]", box).forEach((c) => { c.checked = on; on ? gpkgPick.add(c.value) : gpkgPick.delete(c.value); }); };
    $("[data-gp-all]", box)?.addEventListener("click", all(true));
    $("[data-gp-none]", box)?.addEventListener("click", all(false));
  }
  const exportLayer = () => getLayer($("#lx-layer").value);
  function renderExportForm() {
    const l = exportLayer();
    if (!l) { $("#lx-formats").innerHTML = ""; $("#lx-layer-info").textContent = "Choose a layer from Contents."; return; }
    const r = l.render || {};
    let formats;
    if (l.type === "vector") {
      formats = [["shp", "Shapefile (.zip)", "Polygons / lines / points with attributes, WGS 84. Opens in QGIS, ArcGIS."],
                 ["gpkg", "GeoPackage (.gpkg)", "One file for several layers, with attributes, WGS 84. Opens in QGIS, ArcGIS."],
                 ["geojson", "GeoJSON", "Web-friendly vector format"],
                 ["kml", "KML", "Google Earth"]];
    } else if (l.type === "image") {
      formats = [["png", "PNG image", "The preview picture as shown on the map"]];
    } else {
      const tifDesc = r.index || r.formula ? "Index values as float32, full resolution, original projection"
        : r.rgb ? `All ${l.info?.count || ""} bands of the file (e.g. every component), full resolution`
        : r.composite ? "The 3 displayed bands as surface reflectance, full resolution"
        : `Band ${r.band} with its original values${l.legend?.kind === "classes" ? " and colour table" : ""}`;
      formats = [["tif", "GeoTIFF", tifDesc],
                 ["png", "PNG image", "As displayed (colours), full resolution up to 8192 px"],
                 ["pngw", "PNG + world file (.zip)", "Georeferenced PNG (.pgw + .prj) for QGIS / ArcGIS"],
                 ["shp", "Shapefile (.zip)", r.composite || r.rgb ? "Not available for a colour image. Display a single band first (Properties)." : "Polygons of value classes, with class, range and area attributes"]];
    }
    $("#lx-layer-info").textContent = `${l.type === "vector" ? "Vector" : l.type === "image" ? "Preview image" : "Raster"} · ${displayLabel(l)}${l.path ? " · " + l.path : ""}`;
    const prev = $('input[name="lxf"]:checked')?.value;
    $("#lx-formats").innerHTML = formats.map(([k, t, d]) => `<label class="opt ${k === "shp" && (r.composite || r.rgb) ? "disabled" : ""}">
      <input type="radio" name="lxf" value="${k}"><span><b>${t}</b><small>${esc(d)}</small></span></label>`).join("");
    const keep = formats.find(([k]) => k === prev && !(k === "shp" && (r.composite || r.rgb)));
    $(`input[name="lxf"][value="${keep ? prev : formats[0][0]}"]`).checked = true;
    $("#lx-name").value = safeName(l.name);
    $("#lx-error").classList.add("hidden");
    const classMap = l.legend?.kind === "classes";
    $("#lx-shp-note").textContent = classMap ? "Each map class becomes polygons and keeps its name." :
      "Pixel values are grouped into classes, then neighbouring pixels of the same class are merged into polygons.";
    $("#lx-shp-classes").classList.toggle("hidden", classMap);
    const syncShp = () => {
      const f = $('input[name="lxf"]:checked')?.value;
      $("#lx-shp").classList.toggle("hidden", f !== "shp" || l.type === "vector");
      $("#lx-gpkg").classList.toggle("hidden", f !== "gpkg");
      $("#lx-crs-wrap").classList.toggle("hidden", !(l.type === "vector" && (f === "shp" || f === "gpkg")));   // GeoJSON / KML are WGS 84 by definition
      if (f === "gpkg") renderGpkgLayers();
    };
    $$('input[name="lxf"]').forEach((i) => i.onchange = syncShp);
    syncShp();
    refreshClipPicker("lx-area");
    fillExportCrs();
    $("#lx-area").disabled = l.type === "image";
  }
  $("#lx-layer").onchange = renderExportForm;
  // the coordinate system of an exported vector file: WGS 84, the layer's own (as it was read), UTM where the map is, or any
  let lxPick = null;
  function fillExportCrs(extra = null) {
    const l = exportLayer(), c = map.getCenter(), zone = (c.lat >= 0 ? 32600 : 32700) + Math.min(Math.max(Math.floor((c.lng + 180) / 6) + 1, 1), 60);
    const opts = [["", "WGS 84 (longitude / latitude)"], ...(l?.crs && l.crs.epsg !== 4326 ? [[l.crs.crs, `${l.crs.name} (as the layer was read)`]] : []),
                  [`EPSG:${zone}`, `WGS 84 / UTM zone ${zone % 100}${c.lat >= 0 ? "N" : "S"} (where the map is)`], ...(extra ? [[extra.crs, extra.name]] : []), ["other", "Other… (search)"]];
    const was = $("#lx-crs").value;
    $("#lx-crs").innerHTML = [...new Map(opts.map((o) => [o[0], o])).values()].map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("");
    $("#lx-crs").value = extra ? extra.crs : [...$("#lx-crs").options].some((o) => o.value === was) ? was : "";
    $("#lx-crs").onchange();
  }
  $("#lx-crs").onchange = () => {
    const other = $("#lx-crs").value === "other";
    $("#lx-crs-pick").classList.toggle("hidden", !other);
    if (other && !lxPick) lxPick = crsPicker($("#lx-crs-pick"), {});
  };
  const exportCrsValue = () => { const v = $("#lx-crs").value; return v === "other" ? lxPick?.get()?.crs || null : v || null; };
  /** open Export with a coordinate system chosen (right-click ▸ Coordinate system… ▸ Convert a copy) */
  function exportCrs(c) {
    const f = $('input[name="lxf"][value="gpkg"]');
    if (f) { f.checked = true; f.onchange?.(); }
    fillExportCrs(c);
  }
  $("#lx-method").onchange = () => $("#lx-breaks-wrap").classList.toggle("hidden", $("#lx-method").value !== "custom");
  $("#lx-go").onclick = (e) => {
    const l = exportLayer(), fmtSel = $('input[name="lxf"]:checked')?.value, name = safeName($("#lx-name").value);
    const clip = l?.type === "image" ? null : getClip("lx-area", l);
    const err = (m) => { $("#lx-error").textContent = m; $("#lx-error").classList.remove("hidden"); };
    if (!l || !fmtSel) return;
    busy(e.currentTarget, "Exporting…", async () => {
      try {
        let r;
        if (l.type === "image") { download(l.url, name + ".png"); return; }
        const folder = exportFolder() || null;
        if ($('[data-save="export"] [data-save-on]').checked && !folder) return err("Choose the folder to save in (Browse…), or untick “Save to a folder”.");
        if (l.type === "vector") {
          const more = fmtSel === "gpkg" ? layers.filter((x) => gpkgPick.has(x.id) && x.type === "vector" && x.id !== l.id).reverse()
            .map((x) => ({ name: x.name, geojson: x.geojson })) : [];
          const crs = ["shp", "gpkg"].includes(fmtSel) ? exportCrsValue() : null;
          r = await api("/api/vector/export", { method: "POST", json: { geojson: l.geojson, format: fmtSel, name, clip, folder, layer_name: l.name, more, crs } });
        } else {
          const body = { path: l.path, format: fmtSel, name, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0, ...l.render, clip, folder };
          if (fmtSel === "shp") {
            Object.assign(body, { method: $("#lx-method").value, classes: +$("#lx-classes").value || 5, sieve: +$("#lx-sieve").value || 0 });
            if (body.method === "custom") body.breaks = $("#lx-breaks").value.split(/[,\s]+/).filter(Boolean).map(Number);
            if (body.method === "equal" && l.legend?.kind === "continuous") { body.vmin = l.legend.vmin; body.vmax = l.legend.vmax; }
          }
          const job = await api("/api/layers/export", { method: "POST", json: body });
          try {
            r = (await trackJob(job, { tool: "export", title: `Exporting ${name} (${fmtSel.toUpperCase()})` })).result;
          } catch (ex2) { if (!notCancelled(ex2)) return; throw ex2; }
        }
        if (r.saved) {
          const note = $('[data-save="export"] .save-note');
          note.innerHTML = `✓ Saved ${esc(r.saved.map((p) => p.split(/[\\/]/).pop()).join(", "))} in <code>${esc(r.saved_to)}</code> · <a href="#" data-reveal>Show in folder</a>`;
          note.classList.remove("hidden");
          $("[data-reveal]", note).onclick = (ev) => { ev.preventDefault(); api("/api/project/reveal", { method: "POST", json: { path: r.saved[0] } }).catch((x) => toast(x, true)); };
          toast(`Saved in ${r.saved_to}`);
          return;
        }
        download(r.url, r.name);
        toast(`Exported ${r.name}${r.layers > 1 ? ` · ${r.layers} layers` : ""}${r.features ? ` · ${r.features.toLocaleString()} features` : ""}${r.size_mb ? ` · ${fmt(r.size_mb)} MB` : ""}`);
        status(`Exported ${r.name}`);
      } catch (ex) { err(ex.message); }
    });
  };
