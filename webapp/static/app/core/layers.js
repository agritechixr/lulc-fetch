  // Contents: map layers (add, style, reorder, remove) and what happens when layers change.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ layers (Contents)
  const layers = [];   // index 0 = drawn on top
  let selectedId = null, seq = 0;
  const PALETTE = ["#2563eb", "#db2777", "#ea580c", "#0891b2", "#7c3aed", "#65a30d", "#dc2626", "#0d9488"];
  let colorIdx = 0;
  const nextColor = () => PALETTE[colorIdx++ % PALETTE.length];
  const selectedLayer = () => layers.find((l) => l.id === selectedId) || null;
  const getLayer = (id) => layers.find((l) => l.id === id) || null;

  function vecStyle(l, f) {
    const color = (f && l.classColors?.[f.properties?.class]) || l.color || "#2563eb";
    return { color, fillColor: color, weight: l.weight ?? 2, opacity: l.opacity,
             fillOpacity: (l.fillOpacity ?? 0.12) * l.opacity, dashArray: l.dash || null };
  }

  function featurePopup(f, l) {
    const props = Object.entries(f.properties || {}).filter(([, v]) => v !== null && typeof v !== "object");
    return `<div class="pxpop"><div><b>${esc(l.name)}</b></div><table>${props.slice(0, 25).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${esc(typeof v === "number" ? fmtv(v) : v)}</td></tr>`).join("") || "<tr><td>No attributes</td></tr>"}</table></div>`;
  }

  function buildLeaflet(l) {
    l.leaflet?.remove();
    l.leaflet = null;
    if (l.type === "vector") {
      l.leaflet = L.geoJSON(l.geojson, {
        bubblingMouseEvents: false,
        style: (f) => vecStyle(l, f),
        pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 6, ...vecStyle(l, f), fillOpacity: 0.85 * l.opacity, bubblingMouseEvents: false }),
        // A selected raster wins: clicking on top of a polygon still reads the raster's pixel values.
        onEachFeature: (f, lyr) => lyr.on("click", (e) => {
          if (picking || activeDraw) return;
          const sel = selectedLayer();
          if (sel?.type === "raster" && sel.visible) identify(e.latlng);
          else L.popup({ maxWidth: 300 }).setLatLng(e.latlng).setContent(featurePopup(f, l)).openOn(map);
        }),
      });
    } else if ((l.type === "image" && l.url) || (l.type === "raster" && l.image)) {
      l.leaflet = L.imageOverlay(l.url || l.image, l.bounds, { opacity: l.opacity, interactive: false });
    }
    if (l.leaflet && l.visible) l.leaflet.addTo(map);
  }

  function addLayer(def, { select = true, zoom = false, below = null } = {}) {
    if (def.id) removeLayer(def.id, { silent: true });
    const l = { visible: true, opacity: 1, open: false, ...def, id: def.id || `${def.type}-${Date.now().toString(36)}${(seq++).toString(36)}` };
    const at = below ? layers.findIndex((x) => x.id === below) : -1;
    if (at >= 0) layers.splice(at + 1, 0, l); else layers.unshift(l);
    buildLeaflet(l);
    if (select) selectedId = l.id;
    restack();
    renderContents();
    saveLayers();
    if (zoom) zoomTo(l);
    return l;
  }

  function removeLayer(id, { silent = false } = {}) {
    const i = layers.findIndex((l) => l.id === id);
    if (i < 0) return;
    const [l] = layers.splice(i, 1);
    l.leaflet?.remove();
    if (selectedId === id) selectedId = null;
    if (silent) return;
    onLayerRemoved(l);
    renderContents();
    saveLayers();
  }

  function restack() {
    for (let i = layers.length - 1; i >= 0; i--) {
      const l = layers[i];
      if (l.visible && l.leaflet && map.hasLayer(l.leaflet)) l.leaflet.bringToFront();
    }
  }
  function setVisible(l, v) {
    l.visible = v;
    if (l.leaflet) { if (v) l.leaflet.addTo(map); else l.leaflet.remove(); }
    restack();
    saveLayers();
  }
  function setOpacity(l, o) {
    l.opacity = o;
    if (l.type === "vector") l.leaflet?.setStyle((f) => vecStyle(l, f));
    else l.leaflet?.setOpacity(o);
    saveLayers();
  }
  function layerBounds(l) {
    if (l.type === "vector") return l.leaflet?.getBounds();
    const b = l.bounds || l.info?.bounds;  // info.bounds works even before the layer has finished drawing
    return b ? L.latLngBounds(b) : null;
  }
  function zoomTo(l) {
    if (!l) return;
    const b = layerBounds(l);
    if (b?.isValid()) { map.fitBounds(b, { padding: [30, 30], maxZoom: 17 }); status(`Zoomed to ${l.name}`); }
    else toast(`${l.name} has no extent to zoom to yet`, true);
  }
  function zoomAll() {
    let b = null;
    layers.filter((l) => l.visible).forEach((l) => { const lb = layerBounds(l); if (lb?.isValid()) b = b ? b.extend(lb) : L.latLngBounds(lb.getSouthWest(), lb.getNorthEast()); });
    if (b) map.fitBounds(b, { padding: [30, 30] }); else toast("No visible layers to zoom to");
  }
  function selectLayer(id) {
    selectedId = id;
    $$("#layer-list .layer").forEach((el) => el.classList.toggle("selected", el.dataset.id === id));
  }
  function moveLayer(id, toIndex) {
    const i = layers.findIndex((l) => l.id === id);
    if (i < 0) return;
    const [l] = layers.splice(i, 1);
    layers.splice(Math.max(0, Math.min(layers.length, toIndex)), 0, l);
    restack();
    renderContents();
    saveLayers();
  }

  // raster layers are rendered by the server into a map-ready PNG (display style lives in l.render)
  async function renderRaster(l, { signal } = {}) {
    l.busy = true; l.error = null;
    mapBusy.start(l);
    renderContents();
    try {
      const res = await api("/api/analyze/render", { method: "POST", signal, json: {
        path: l.path, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0, ...l.render } });
      const { image, bounds, ...legend } = res;
      if (!getLayer(l.id)) return res;  // removed while rendering
      l.image = image; l.bounds = bounds; l.legend = legend;
      if (l.leaflet) { l.leaflet.setUrl(image); l.leaflet.setBounds(L.latLngBounds(bounds)); } else buildLeaflet(l);
      restack();
      return res;
    } catch (e) {
      if (e.name !== "AbortError") l.error = e.message;
      throw e;
    } finally {
      l.busy = false;
      mapBusy.end(l);
      renderContents();
      saveLayers();
    }
  }

  function defaultRender(info) {
    if (info.embedding) return { pca: true, stretch: "auto" };   // all bands kept, shown in colour (their 3 main directions)
    if (info.rgb) return { rgb: [1, 2, 3], stretch: "none" };
    const has = (bs) => bs.every((b) => b in info.band_map);
    if (has(["B04", "B03", "B02"])) return { composite: "true" };
    if (has(["VV", "VH", "VVVH"])) return { composite: "sar" };
    return { band: 1, stretch: "fixed" };  // index files named e.g. "NDVI" get that index's colours; class maps their palette
  }

  async function addRasterFromPath(path, { name, render, select = true, zoom = true } = {}) {
    const info = await api(`/api/rasters/info?path=${encodeURIComponent(path)}`);
    const l = addLayer({ type: "raster", name: name || info.name, path, info, band_map: { ...info.band_map },
                         scale: info.scale, offset: info.offset, render: render || defaultRender(info), userSet: [] }, { select });
    try {
      await renderRaster(l);
      if (zoom) zoomTo(l);
    } catch (e) { toast(`${l.name}: ${e.message}`, true); }
    return l;
  }

  function addVectorLayer(fc, name, opts = {}) {
    return addLayer({ type: "vector", name, geojson: fc, color: opts.color || nextColor(), ...opts }, { zoom: opts.zoom ?? true, below: opts.below });
  }

  function displayLabel(l) {
    if (l.type === "vector") {
      const n = l.geojson?.features?.length ?? 0;
      return `${n} feature${n === 1 ? "" : "s"}`;
    }
    if (l.type === "image") return "preview image";
    const r = l.render || {};
    if (r.composite) return state.catalog?.composites[r.composite]?.title || r.composite;
    if (r.pca) return `${l.info?.count || ""} bands · colour view`;
    if (r.rgb) return l.legend?.bands ? "RGB " + l.legend.bands.join("/") : "RGB";
    if (r.index) return r.index;
    if (r.formula) return "formula";
    if (r.band) return l.legend?.kind === "classes" ? "classes" : `band ${r.band}`;
    return "";
  }

  function legendHtml(l) {
    const g = l.legend;
    if (l.type === "vector" && l.classes?.length) {
      return `<div class="lyr-classes">${l.classes.map((c) => `<div><i style="background:${esc(c.color)}"></i><span>${esc(c.name)}</span><span>${l.geojson.features.filter((f) => f.properties.class === c.name).length}</span></div>`).join("")}</div>`;
    }
    if (l.type === "vector") return `<div class="lyr-meta">${displayLabel(l)} · EPSG:4326</div>`;
    if (!g) return "";
    if (g.kind === "continuous") {
      return `<div class="lyr-legend"><div class="legend" style="background:linear-gradient(to right, ${g.colors.join(",")})"></div>
        <div class="row between"><span>${fmtv(g.vmin)}</span><span>${esc(g.title || "")}</span><span>${fmtv(g.vmax)}</span></div></div>`;
    }
    if (g.kind === "classes") {
      return `<div class="lyr-classes">${g.classes.slice(0, 10).map((c) =>
        `<div><i style="background:${esc(c.color)}"></i><span>${esc(c.name)}</span><span>${fmt(c.pct)}%</span></div>`).join("")}</div>`;
    }
    if (g.kind === "rgb") return `<div class="lyr-meta">R = ${g.bands[0]} · G = ${g.bands[1]} · B = ${g.bands[2]}</div>`;
    return "";
  }

  function layerIcon(l) {
    if (l.type === "vector") return `<span class="lyr-ic" style="border-color:${l.color};color:${l.color};background:${l.color}22">${svg("vector", 2.2)}</span>`;
    const g = l.legend;
    if (g?.kind === "continuous") return `<span class="lyr-ic" style="background:linear-gradient(135deg, ${g.colors.join(",")})"></span>`;
    if (g?.kind === "classes") return `<span class="lyr-ic" style="background:conic-gradient(${g.classes.slice(0, 6).map((c, i, a) => `${c.color} ${i / a.length * 100}% ${(i + 1) / a.length * 100}%`).join(",")})"></span>`;
    return `<span class="lyr-ic">${svg(l.type)}</span>`;
  }

  function renderContents() {
    $("#layer-list").innerHTML = layers.map((l) => `
      <div class="layer ${l.id === selectedId ? "selected" : ""} ${l.open ? "open" : ""}" data-id="${esc(l.id)}" draggable="true">
        <div class="lyr-row">
          <button class="lyr-caret" title="Show legend & opacity">▶</button>
          <input type="checkbox" ${l.visible ? "checked" : ""} title="Show / hide">
          ${layerIcon(l)}
          <span class="lyr-name" title="${esc(l.name)}${l.path ? "\n" + esc(l.path) : ""}">${esc(l.name)}<small>${esc(displayLabel(l))}</small></span>
          ${l.busy ? '<span class="spinner"></span>' : ""}
          <button class="lyr-zoom" title="Zoom to layer"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M11 8v6M8 11h6"/></svg></button>
          <button class="lyr-more" title="Layer options">⋯</button>
        </div>
        ${l.error ? `<div class="lyr-err">⚠ ${esc(l.error)}</div>` : ""}
        <div class="lyr-body">
          ${legendHtml(l)}
          <label>Opacity <input type="range" min="0" max="100" value="${Math.round(l.opacity * 100)}" data-op></label>
          ${l.path ? `<div class="lyr-meta">${esc(l.path)}${l.info ? ` · ${esc(l.info.crs)} · ${l.info.width}×${l.info.height}` : ""}</div>` : ""}
        </div>
      </div>`).join("");
    $$("#layer-list .layer").forEach((el) => {
      const l = getLayer(el.dataset.id);
      el.onclick = (e) => { if (!e.target.closest("input, button")) selectLayer(l.id); };
      el.ondblclick = (e) => { if (!e.target.closest("input, button")) zoomTo(l); };
      el.oncontextmenu = (e) => { e.preventDefault(); selectLayer(l.id); showCtx(l, e.clientX, e.clientY); };
      $("input[type=checkbox]", el).onchange = (e) => setVisible(l, e.target.checked);
      $(".lyr-caret", el).onclick = () => { l.open = !l.open; el.classList.toggle("open", l.open); };
      $(".lyr-zoom", el).onclick = (e) => { e.stopPropagation(); selectLayer(l.id); zoomTo(l); };
      $(".lyr-more", el).onclick = (e) => { e.stopPropagation(); selectLayer(l.id); const r = e.currentTarget.getBoundingClientRect(); showCtx(l, r.right, r.bottom); };
      $("[data-op]", el).oninput = (e) => setOpacity(l, e.target.value / 100);
      // drag to reorder
      el.ondragstart = (e) => { if (e.target.closest("input[type=range]")) { e.preventDefault(); return; } e.dataTransfer.setData("text/layer", l.id); el.classList.add("dragging"); };
      el.ondragend = () => el.classList.remove("dragging");
      el.ondragover = (e) => { if ([...e.dataTransfer.types].includes("text/layer")) { e.preventDefault(); el.classList.add("drag-over"); } };
      el.ondragleave = () => el.classList.remove("drag-over");
      el.ondrop = (e) => {
        el.classList.remove("drag-over");
        const id = e.dataTransfer.getData("text/layer");
        if (!id || id === l.id) return;
        e.preventDefault(); e.stopPropagation();
        const target = layers.findIndex((x) => x.id === l.id), from = layers.findIndex((x) => x.id === id);
        moveLayer(id, from < target ? target : target);
      };
    });
    updateSectionCounts();
    refreshAnalyzeInputs();
    if (pcaState.schema) refreshPcaInputs();
    if (currentTool === "raster2table") refreshRtInputs();
    if (currentTool === "rasterml" && rm.ready) refreshRm();
    if (currentTool === "patches") refreshPt();
    if (currentTool === "dlpredict" && dlx.schema) renderDpLayers();
    if (currentTool === "detect" && od.schema) renderOdLayers();
    PLUGINS[currentTool]?.hooks?.layersChanged?.();
    if (currentTool === "traindet" && td.schema) { renderTdLayers(); renderTdGt(); }
    if (currentTool === "samples") renderSamples();
    if (currentTool === "stack") refreshStack();
    if (currentTool === "ml" && mlSub === "predict" && mlx.schema) refreshPredictRasters();
    refreshClipPickers();
    if (currentTool === "export") { refreshExportLayers(); if (!exportLayer()) renderExportForm(); }
  }

  function onLayerRemoved(l) {
    if (vw.tabs.some((t) => t.key === "attr:" + l.id)) closeTab("attr:" + l.id);
    if (l.id === "aoi") { state.aoi = null; $("#aoi-summary").classList.add("hidden"); $("#aoi-warn").classList.add("hidden"); $("#aoi-emb").classList.add("hidden"); }
    if (an.layer?.id === l.id) resetAnalyze();
    if (an.resultId === l.id) { an.resultId = null; an.sel = null; $("#an-result").classList.add("hidden"); renderIndexButtons(); }
  }

  // persistence: layer list survives reloads (preview images are not kept)
  function saveLayers() {
    try {
      if (!inProject()) {
        const keep = layers.filter((l) => l.type !== "image").map(({ leaflet, image, busy, error, legend, _original, ...rest }) =>
          _original !== undefined ? { ...rest, geojson: { ...rest.geojson, features: JSON.parse(_original) } } : rest);
        const text = JSON.stringify(keep);
        if (text.length < 4e6) localStorage.setItem("lulc-layers", text);
      }
    } catch {}
    scheduleProjectSave();
  }
  function restoreLayers(list) {
    let saved = [];
    if (list) saved = [...list];
    else try { saved = JSON.parse(localStorage.getItem("lulc-layers") || "[]"); } catch {}
    for (const d of saved.reverse()) {  // bottom first, so each unshift puts the next one above
      if (d.type === "vector") {
        addLayer(d, { select: false });
        if (d.id === "aoi") restoreAoi(d);
      } else if (d.type === "raster") {
        const l = addLayer(d, { select: false });
        renderRaster(l).catch(() => {});
      }
    }
    selectedId = null;
    renderContents();
  }
