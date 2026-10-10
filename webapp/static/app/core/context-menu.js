  // Right-click menu of layers in Contents.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ context menu
  function showCtx(l, x, y) {
    const many = selectedLayers();
    if (many.length > 1) return showMenu(`${many.length} layers`, [
      ["Zoom to these layers", () => zoomToLayers(many)],
      ["Show them", () => setVisibleMany(many, (x) => many.includes(x) ? true : null, `Show ${many.length} layers`)],
      ["Hide them", () => setVisibleMany(many, (x) => many.includes(x) ? false : null, `Hide ${many.length} layers`)],
      ["Show only these", () => setVisibleMany(many, (x) => many.includes(x), "Show only the selected layers")],
      "-",
      many.some((x) => x.type !== "image") ? ["Copy (paste into another map)  Ctrl+C", () => copyLayers(many.filter((x) => x.type !== "image"))] : null,
      ["Group these layers…", () => askGroup(many)],
      many.some((x) => x.group) ? ["Take them out of their groups", () => historyStep("Ungroup layers", () => { many.forEach((x) => { delete x.group; }); renderContents(); saveLayers(); })] : null,
      "-",
      ["Move to top", () => moveLayers(many, 0)],
      ["Move to bottom", () => moveLayers(many, layers.length)],
      "-",
      [`Remove ${many.length} layers`, () => removeLayers(many), "danger"],
    ].filter(Boolean), x, y);
    const isPoly = l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type));
    if (l.type === "unplaced") return showMenu(l.name, [
      ["Coordinate system… (place it on the map)", () => openCrsDialog(l)],
      ["Place with control points…", () => openTool("georef", { layer: l.id })],
      "-",
      ["Remove", () => removeLayer(l.id), "danger"],
    ], x, y);
    const items = [
      ["Zoom to layer", () => zoomTo(l)],
      ["Properties…", () => openProps(l)],
      l.type === "raster" && !l.derived && (l.info?.count || 0) >= 2 ? ["Band combination (RGB)…", () => openBandCombo(l)] : null,
      l.type !== "image" && l.type !== "tiles" ? ["Metadata…", () => openMetadata(l)] : null,
      l.type === "vector" || (l.type === "raster" && l.path) ? ["Coordinate system…", () => openCrsDialog(l)] : null,
      l.type === "tiles" && l.tiles.url ? ["Copy the service address", () => copyText(l.tiles.url, "Service address copied")] : null,
      l.type === "raster" && !l.derived ? ["Compute indices on this layer", () => analyzeLayer(l)] : null,
      l.type === "vector" ? ["Open attribute table", () => openAttr(l)] : null,
      l.type === "vector" ? ["Single colour…", () => { openProps(l); $("#lp-sym").value = "single"; syncSymUi(); }] : null,
      l.type === "vector" ? ["Style by attribute…", () => { openProps(l); $("#lp-sym").value = l.symbology?.mode || "categories"; syncSymUi(); }] : null,
      isPoly && l.id !== "aoi" ? ["Use as area of interest", () => useAsAoi(l)] : null,
      is3D() && l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)) ? [l.extrude ? "3D: change the extrusion…" : "3D: extrude by an attribute…", () => openExtrude(l)] : null,
      is3D() && l.type === "raster" && (isSurface(l) || (l.info?.count || 0) === 1) ? [isSurface(l) ? "Move to 2D data (drape it in 3D)" : "Move to 3D data (its values are heights)", () => setLayer3d(l, !isSurface(l))] : null,
      "-",
      l.type !== "image" ? ["Copy (paste into another map)  Ctrl+C", () => copyLayers([l])] : null,
      docs.clip?.length ? ["Paste into this map  Ctrl+V", () => pasteLayers()] : null,
      "-",
      l.type !== "image" && l.type !== "tiles" ? ["Save to folder…", () => saveLayerToFolder(l)] : null,
      l.type !== "tiles" ? ["Export / save to computer…", () => openExport(l)] : null,
      "-",
      ["Move to top", () => moveLayer(l.id, 0)],
      ["Move to bottom", () => moveLayer(l.id, layers.length)],
      l.group ? [`Take out of group “${l.group}”`, () => historyStep(`Take ${l.name} out of its group`, () => { delete l.group; renderContents(); saveLayers(); })] : ["Put in a new group…", () => askGroup([l])],
      "-",
      ["Remove", () => removeLayer(l.id), "danger"],
    ].filter(Boolean);
    showMenu(l.name, items, x, y);
  }
  function showMenu(title, items, x, y) {
    const m = $("#ctx-menu");
    m.innerHTML = `<div class="ctx-title">${esc(title)}</div>` + items.map((it, i) => it === "-" ? "<hr>" : `<button data-i="${i}" class="${it[2] || ""}">${esc(it[0])}</button>`).join("");
    $$("button", m).forEach((b) => b.onclick = (e) => { e.stopPropagation(); hideCtx(); items[+b.dataset.i][1](); });
    m.classList.remove("hidden");
    const r = m.getBoundingClientRect();
    m.scrollTop = 0;
    m.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + "px";
    m.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + "px";
  }
  function hideCtx() { $("#ctx-menu").classList.add("hidden"); }
