  // Export data tool and the export dialog: GeoTIFF, PNG, Shapefile, GeoJSON, KML.
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
  function refreshExportLayers(selectId) {
    const sel = $("#lx-layer"), cur = selectId || sel.value || selectedId;
    sel.innerHTML = layers.length ? layers.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("")
      : `<option value="">No layers in Contents</option>`;
    if (cur && getLayer(cur)) sel.value = cur;
  }
  const exportLayer = () => getLayer($("#lx-layer").value);
  function renderExportForm() {
    const l = exportLayer();
    if (!l) { $("#lx-formats").innerHTML = ""; $("#lx-layer-info").textContent = "Choose a layer from Contents."; return; }
    const r = l.render || {};
    let formats;
    if (l.type === "vector") {
      formats = [["shp", "Shapefile (.zip)", "Polygons / lines / points with attributes, WGS 84. Opens in QGIS, ArcGIS."],
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
    const syncShp = () => $("#lx-shp").classList.toggle("hidden", $('input[name="lxf"]:checked')?.value !== "shp");
    $$('input[name="lxf"]').forEach((i) => i.onchange = syncShp);
    syncShp();
    refreshClipPicker("lx-area");
    $("#lx-area").disabled = l.type === "image";
  }
  $("#lx-layer").onchange = renderExportForm;
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
          r = await api("/api/vector/export", { method: "POST", json: { geojson: l.geojson, format: fmtSel, name, clip, folder } });
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
        toast(`Exported ${r.name}${r.features ? ` · ${r.features.toLocaleString()} features` : ""}${r.size_mb ? ` · ${fmt(r.size_mb)} MB` : ""}`);
        status(`Exported ${r.name}`);
      } catch (ex) { err(ex.message); }
    });
  };
