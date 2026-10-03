  // Area pickers: limit a tool to an area (whole layer, AOI, map view, a polygon layer, or one drawn now).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ area picker (clip to an area of interest)
  // Every tool can limit its work to an area: the whole layer, the Find-imagery AOI, the current map view,
  // any polygon layer in Contents, or a rectangle / polygon drawn now (which is added to Contents too).
  const clipPickers = {
    "an-area": { what: "image is analysed", onChange: () => { if (an.sel && !an.sel.composite) compute(); } },
    "lx-area": { what: "layer is exported", own: true, onChange: () => {} },
    "pca-area": { what: "image is used", onChange: () => {} },
    "rt-area": { what: "image is converted", onChange: () => updateRtEstimate() },
    "mp-area": { what: "image is classified", onChange: () => {} },
    "rm-area": { what: "image is used", onChange: () => {} },
    "pt-area": { what: "image is cut into patches", onChange: () => ptChanged() },
    "dp-area": { what: "image is classified", onChange: () => {} },
    "od-area": { what: "image is searched", onChange: () => odEstimate() },
    "td-area": { what: "image is used for training", onChange: () => {} },
    "st-area": { what: "reference extent is used", onChange: () => {} },
  };
  LF.tools.forEach((t) => Object.entries(t.clip || {}).forEach(([id, c]) => clipPickers[id] = { ...c, onChange: () => PLUGINS[t.id].hooks?.clipChanged?.(id) }));
  const polygonLayers = () => layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)));
  function refreshClipPicker(id) {
    const sel = $("#" + id), cfg = clipPickers[id];
    if (!sel) return;
    const cur = sel.value;
    const opts = [["none", cfg.required ? "Choose an area…" : "Whole " + (id === "lx-area" ? "layer" : "image")]];
    const lx = id === "lx-area" ? exportLayer() : null;
    if (cfg.own && lx?.render?.clip) opts.push(["own", "Same area as the layer (its analysis area)"]);
    const polys = polygonLayers();
    polys.forEach((l) => opts.push([`layer:${l.id}`, (l.id === "aoi" ? "▣ " : "▢ ") + l.name]));
    opts.push(["view", "⌖ Current map view"], ["draw-rect", "✎ Draw a rectangle…"], ["draw-poly", "✎ Draw a polygon…"]);
    sel.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("");
    // a new export layer starts from its own analysis area (if any); otherwise keep the user's choice
    const layerChanged = id === "lx-area" && sel.dataset.layer !== (lx?.id || "");
    sel.dataset.layer = lx?.id || "";
    const valid = !layerChanged && opts.some(([v]) => v === cur) && !cur.startsWith("draw") && cur !== "view";
    sel.value = valid ? cur : (cfg.own && lx?.render?.clip ? "own" : "none");
    updateClipHint(id);
  }
  function refreshClipPickers() { Object.keys(clipPickers).forEach(refreshClipPicker); }
  function updateClipHint(id) {
    const v = $("#" + id).value, cfg = clipPickers[id];
    const g = getClip(id);
    $(`#${id}-hint`).innerHTML = !g ? (cfg.required ? "Choose an area: draw one, use the map view, or a polygon layer." : `The whole ${esc(cfg.what)}.`)
      : `Only the selected area (<b>${fmt(geomArea(g) / 1e6, geomArea(g) < 1e7 ? 2 : 0)} km²</b>) ${esc(cfg.what)}${v.startsWith("layer:") ? ` · <a href="#" data-zoomclip="${esc(v.slice(6))}">zoom to it</a>` : ""}.`;
    $(`#${id}-hint [data-zoomclip]`)?.addEventListener("click", (e) => { e.preventDefault(); zoomTo(getLayer(e.target.dataset.zoomclip)); });
  }
  function getClip(id, forLayer = null) {
    const v = $("#" + id)?.value || "none";
    if (v === "own") return (forLayer || exportLayer())?.render?.clip || null;
    if (!v.startsWith("layer:")) return null;
    const l = getLayer(v.slice(6));
    const polys = (l?.geojson?.features || []).map((f) => f.geometry).filter((g) => g && /Polygon/.test(g.type));
    if (!polys.length) return null;
    return polys.length === 1 ? polys[0] : { type: "MultiPolygon", coordinates: polys.flatMap((g) => g.type === "Polygon" ? [g.coordinates] : g.coordinates) };
  }
  let clipSeq = 0;
  function addClipLayer(geometry, name) {
    const l = addLayer({ type: "vector", name: name || `Clip area ${++clipSeq}`, color: "#dc2626", dash: "6 4", weight: 2, fillOpacity: 0.04,
      geojson: { type: "FeatureCollection", features: [{ type: "Feature", geometry, properties: { name: name || "Clip area", area_km2: +(geomArea(geometry) / 1e6).toFixed(3) } }] } },
      { select: false });
    return l;
  }
  function pickClip(id) {
    const sel = $("#" + id), v = sel.value;
    const finish = (l) => { refreshClipPickers(); sel.value = `layer:${l.id}`; updateClipHint(id); clipPickers[id].onChange(); };
    if (v === "view") {
      const b = map.getBounds();
      finish(addClipLayer(bboxPolygon(b.getWest(), b.getSouth(), b.getEast(), b.getNorth()), "Map view extent"));
    } else if (v === "draw-rect" || v === "draw-poly") {
      sel.value = "none";
      updateClipHint(id);
      startDraw(v === "draw-rect" ? L.Draw.Rectangle : L.Draw.Polygon, (geometry) => finish(addClipLayer(geometry)));
    } else {
      updateClipHint(id);
      clipPickers[id].onChange();
    }
  }
  Object.keys(clipPickers).forEach((id) => $("#" + id).onchange = () => pickClip(id));
