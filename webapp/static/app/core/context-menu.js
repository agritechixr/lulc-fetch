  // Right-click menu of layers in Contents.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ context menu
  function showCtx(l, x, y) {
    const isPoly = l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type));
    const items = [
      ["Zoom to layer", () => zoomTo(l)],
      ["Properties…", () => openProps(l)],
      l.type === "raster" && !l.derived && (l.info?.count || 0) >= 2 ? ["Band combination (RGB)…", () => openBandCombo(l)] : null,
      l.type !== "image" ? ["Metadata…", () => openMetadata(l)] : null,
      l.type === "raster" && !l.derived ? ["Compute indices on this layer", () => analyzeLayer(l)] : null,
      l.type === "vector" ? ["Open attribute table", () => openAttr(l)] : null,
      isPoly && l.id !== "aoi" ? ["Use as area of interest", () => useAsAoi(l)] : null,
      "-",
      l.type !== "image" ? ["Save to folder…", () => saveLayerToFolder(l)] : null,
      ["Export / save to computer…", () => openExport(l)],
      "-",
      ["Move to top", () => moveLayer(l.id, 0)],
      ["Move to bottom", () => moveLayer(l.id, layers.length)],
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
