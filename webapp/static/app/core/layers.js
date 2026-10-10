  // Contents: map layers (add, style, reorder, remove) and what happens when layers change.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ layers (Contents)
  const layers = [];   // index 0 = drawn on top
  let selectedId = null, seq = 0;
  const selExtra = new Set();   // with Ctrl / Shift: the other selected layers (selectedId is the one clicked last)
  let selAnchor = null;         // where a Shift-click range starts
  const PALETTE = ["#2563eb", "#db2777", "#ea580c", "#0891b2", "#7c3aed", "#65a30d", "#dc2626", "#0d9488"];
  let colorIdx = 0;
  const nextColor = () => PALETTE[colorIdx++ % PALETTE.length];
  const selectedLayer = () => layers.find((l) => l.id === selectedId) || null;
  const isSelected = (id) => !!id && (id === selectedId || selExtra.has(id));
  const selectedLayers = () => layers.filter((l) => isSelected(l.id));
  const getLayer = (id) => layers.find((l) => l.id === id) || null;

  // ---- style by attribute: a colour per value of a field (categories), or classes of a number field (graduated)
  const RAMPS = {
    Viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"], Greens: ["#edf8e9", "#a1d99b", "#41ab5d", "#006d2c", "#00441b"],
    "Red–yellow–green": ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"], Blues: ["#eff3ff", "#9ecae1", "#4292c6", "#08519c", "#08306b"],
    Reds: ["#fee5d9", "#fcae91", "#fb6a4a", "#cb181d", "#67000d"], Magma: ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"],
    "Blue–red": ["#2166ac", "#92c5de", "#f7f7f7", "#f4a582", "#b2182b"],
  };
  const CAT_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173", "#3182bd", "#e6550d", "#31a354", "#756bb1", "#636363"];
  function rampColor(ramp, x) {   // x in 0…1
    const c = RAMPS[ramp] || RAMPS.Viridis, p = Math.min(Math.max(x, 0), 1) * (c.length - 1), i = Math.min(Math.floor(p), c.length - 2), f = p - i;
    const h = (s) => [1, 3, 5].map((k) => parseInt(s.slice(k, k + 2), 16));
    const a = h(c[i]), b = h(c[i + 1]);
    return "#" + a.map((v, k) => Math.round(v + (b[k] - v) * f).toString(16).padStart(2, "0")).join("");
  }
  function jenks(vals, n) {   // natural breaks (Fisher–Jenks) on up to 1,000 values
    const v = vals.length > 1000 ? vals.filter((_, i) => i % Math.ceil(vals.length / 1000) === 0) : vals, m = v.length;
    if (m <= n) return [...new Set(v)];
    const lower = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0)), varc = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(Infinity));
    for (let j = 1; j <= n; j++) { lower[1][j] = 1; varc[1][j] = 0; }
    for (let l = 2; l <= m; l++) {
      let s1 = 0, s2 = 0, w = 0;
      for (let k = 1; k <= l; k++) {
        const i3 = l - k + 1, val = v[i3 - 1]; s2 += val * val; s1 += val; w++;
        const va = s2 - (s1 * s1) / w;
        if (i3 > 1) for (let j = 2; j <= n; j++) if (varc[l][j] >= va + varc[i3 - 1][j - 1]) { lower[l][j] = i3; varc[l][j] = va + varc[i3 - 1][j - 1]; }
      }
      lower[l][1] = 1; varc[l][1] = s2 - (s1 * s1) / w;
    }
    const br = []; let k = m;
    for (let j = n; j >= 2; j--) { const id = lower[k][j] - 2; br.unshift(v[id]); k = lower[k][j] - 1; }
    return br;
  }
  /** a symbology from a field: categories (a colour per value) or graduated (n classes of a number field) */
  function makeSymbology(l, mode, field, opts = {}) {
    const vals = l.geojson.features.map((f) => f.properties?.[field]);
    if (mode === "categories") {
      const counts = new Map();
      vals.forEach((v) => { const k = v == null || v === "" ? "(empty)" : String(v); counts.set(k, (counts.get(k) || 0) + 1); });
      const keys = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a));
      const top = keys.slice(0, 19), map = {};
      top.forEach((k, i) => { map[k] = opts.ramp && RAMPS[opts.ramp] ? rampColor(opts.ramp, top.length > 1 ? i / (top.length - 1) : 0.5) : CAT_COLORS[i % CAT_COLORS.length]; });
      return { mode, field, ramp: opts.ramp || "", map, other: keys.length > top.length ? "#bdbdbd" : null,
               legend: [...top.map((k) => ({ label: k, color: map[k], n: counts.get(k) })), ...(keys.length > top.length ? [{ label: `Other (${keys.length - top.length} values)`, color: "#bdbdbd", n: keys.slice(19).reduce((a, k) => a + counts.get(k), 0) }] : [])] };
    }
    const nums = vals.map(Number).filter((v, i) => vals[i] !== null && vals[i] !== "" && Number.isFinite(v)).sort((a, b) => a - b);
    if (!nums.length) throw new Error(`“${field}” has no numbers: use Categories`);
    const n = Math.max(2, Math.min(opts.classes || 5, 9)), lo = nums[0], hi = nums[nums.length - 1];
    let breaks = opts.method === "equal" ? Array.from({ length: n - 1 }, (_, i) => lo + (hi - lo) * (i + 1) / n)
      : opts.method === "jenks" ? jenks(nums, n) : Array.from({ length: n - 1 }, (_, i) => nums[Math.floor(nums.length * (i + 1) / n)]);
    breaks = [...new Set(breaks)].sort((a, b) => a - b);
    const colors = Array.from({ length: breaks.length + 1 }, (_, i) => rampColor(opts.ramp || "Viridis", breaks.length ? i / breaks.length : 0.5));
    const edges = [lo, ...breaks, hi];
    return { mode: "graduated", field, ramp: opts.ramp || "Viridis", method: opts.method || "quantile", classes: n, breaks, colors,
             legend: colors.map((c, i) => ({ label: `${fmtv(edges[i])} – ${fmtv(edges[i + 1])}`, color: c, n: nums.filter((v) => (i === 0 || v >= edges[i]) && (i === colors.length - 1 || v < edges[i + 1])).length })) };
  }
  function symColor(sym, f) {
    const v = f?.properties?.[sym.field];
    if (sym.mode === "categories") return sym.map[v == null || v === "" ? "(empty)" : String(v)] || sym.other || "#bdbdbd";
    const x = Number(v);
    if (v == null || v === "" || !Number.isFinite(x)) return "#bdbdbd";
    let i = 0;
    while (i < sym.breaks.length && x >= sym.breaks[i]) i++;
    return sym.colors[i];
  }

  function vecStyle(l, f) {
    const color = (f && l.symbology?.field && symColor(l.symbology, f)) || (f && l.classColors?.[f.properties?.class]) || l.color || "#2563eb";
    return { color, fillColor: color, weight: l.weight ?? 2, opacity: l.opacity,
             fillOpacity: (l.symbology?.field ? (l.fillOpacity ?? 0.6) : (l.fillOpacity ?? 0.12)) * l.opacity, dashArray: l.dash || null };
  }

  function featurePopup(f, l) {
    const p = f.properties || {}, props = Object.entries(p).filter(([k, v]) => v !== null && typeof v !== "object" && k !== "photos");
    // field collection points: their photos (paths in the workspace), click for the full photo
    const photos = String(p.photos || p.photo || "").split(/;\s*/).filter((x) => /\.(jpe?g|png|webp|heic|heif)$/i.test(x));
    const pics = photos.length ? `<div class="pop-photos">${photos.slice(0, 6).map((x) => `<a href="/api/agri/photo?path=${encodeURIComponent(x)}&size=0" target="_blank" rel="noopener">
      <img src="/api/agri/photo?path=${encodeURIComponent(x)}&size=200" alt="" loading="lazy"></a>`).join("")}</div>` : "";
    return `<div class="pxpop"><div><b>${esc(l.name)}</b></div>${pics}<table>${props.slice(0, 25).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${esc(typeof v === "number" ? fmtv(v) : v)}</td></tr>`).join("") || "<tr><td>No attributes</td></tr>"}</table></div>`;
  }

  function buildLeaflet(l) {
    l.leaflet?.remove();
    l.leaflet = null;
    const pane = l._pane ? { pane: l._pane } : {};   // Swipe puts the two compared layers in panes of their own
    if (l.type === "vector") {
      l.leaflet = L.geoJSON(l.geojson, {
        ...pane,
        bubblingMouseEvents: false,
        style: (f) => vecStyle(l, f),
        pointToLayer: (f, ll) => L.circleMarker(ll, { ...pane, radius: 6, ...vecStyle(l, f), fillOpacity: 0.85 * l.opacity, bubblingMouseEvents: false }),
        // A selected raster wins: clicking on top of a polygon still reads the raster's pixel values.
        onEachFeature: (f, lyr) => lyr.on("contextmenu", (e) => {   // the map's right-click menu also on shapes
          L.DomEvent.stop(e); if (picking || activeDraw || measure.on) return;
          e.originalEvent?.preventDefault(); showMapMenu(e.latlng, e.originalEvent.clientX, e.originalEvent.clientY);
        }).on("click", (e) => {
          if (measure.on) return;   // the map's click adds the point
          if (picking || activeDraw) return;
          const sel = selectedLayer();
          if (sel?.type === "raster" && sel.visible) identify(e.latlng);
          else L.popup({ maxWidth: 300 }).setLatLng(e.latlng).setContent(featurePopup(f, l)).openOn(map);
        }),
      });
    } else if ((l.type === "image" && l.url) || (l.type === "raster" && l.image)) {
      l.leaflet = L.imageOverlay(l.url || l.image, l.bounds, { ...pane, opacity: l.opacity, interactive: false });
    } else if (l.type === "tiles") {
      l.leaflet = tileLeaflet(l, l._pane || onlinePane(l));
    }   // "unplaced": vector data without a coordinate system is kept, not drawn (right-click ▸ Coordinate system…)
    if (l.leaflet && l.visible) l.leaflet.addTo(map);
  }

  // ---- online map layers (Insert ▸ Online map layer): l.tiles = { kind: wms | wmts | xyz, url, layer, style, format, version,
  // time, time_info, matrices, min_zoom, max_zoom, bbox, legend, attribution, service }. The map loads the tiles from the service.
  const TemplateTiles = L.TileLayer.extend({   // {z} {x} {y} {-y} {s} and {time}; WMTS tile matrices not named after the zoom
    getTileUrl(c) {
      const z = c.z, m = this.options.matrices, t = this.options.time;   // c.z: also the tiles of a print at another zoom
      const data = { x: c.x, y: c.y, z: m ? m[z] ?? z : z, s: this._getSubdomain(c), r: L.Browser.retina ? "@2x" : "", time: t || "default",
                     "-y": 2 ** z - 1 - c.y };
      return L.Util.template(this._url, { ...this.options, ...data });
    },
  });
  function tileLeaflet(l, pane) {
    const t = l.tiles, base = { pane, opacity: l.opacity, attribution: t.attribution || t.service || "", maxZoom: 22,
                                minNativeZoom: t.min_zoom ?? 0, maxNativeZoom: t.max_zoom ?? 19,
                                bounds: t.bbox ? L.latLngBounds([t.bbox[1], t.bbox[0]], [t.bbox[3], t.bbox[2]]) : undefined };
    if (t.kind === "wms") {
      return L.tileLayer.wms(t.url, { ...base, layers: t.layer, styles: t.style || "", format: t.format || "image/png", transparent: true,
                                      version: t.version || "1.3.0", uppercase: true, ...(t.time ? { time: t.time } : {}) });
    }
    return new TemplateTiles(t.url, { ...base, matrices: t.matrices || null, time: t.time || t.time_info?.default || "" });
  }
  /** the pane of an online layer: over the app's own layers when it is above all of them in Contents, else under them */
  function onlinePane(l) {
    const i = layers.indexOf(l), own = layers.findIndex((x) => x.type !== "tiles");
    return own < 0 || (i >= 0 && i < own) ? "onlineAbove" : "onlineBelow";
  }
  /** a layer changed in place (its type, data or style): drawn again, Contents and Undo updated, zoomed to */
  function refreshLayer(l, { zoom = true } = {}) {
    buildLeaflet(l); restack(); renderContents(); saveLayers();
    if (zoom) zoomTo(l);
  }
  function addOnlineLayer(tiles, name, opts = {}) {
    return addLayer({ type: "tiles", name, tiles, ...opts }, { zoom: false });
  }

  function addLayer(def, { select = true, zoom = false, below = null } = {}) {
    if (def.id) removeLayer(def.id, { silent: true });
    const l = { visible: true, opacity: 1, open: false, ...def, id: def.id || `${def.type}-${Date.now().toString(36)}${(seq++).toString(36)}` };
    const at = below ? layers.findIndex((x) => x.id === below) : -1;
    if (at >= 0) layers.splice(at + 1, 0, l); else layers.unshift(l);
    buildLeaflet(l);
    if (select) { selectedId = l.id; selExtra.clear(); }
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
    selExtra.delete(id);
    if (selectedId === id) selectedId = selExtra.size ? [...selExtra].pop() : null;
    if (selectedId) selExtra.delete(selectedId);
    if (silent) return;
    onLayerRemoved(l);
    renderContents();
    saveLayers();
  }

  function restack() {
    map3dChanged();
    for (const l of layers) if (l.type === "tiles" && !l._pane && l.leaflet && l.leaflet.options.pane !== onlinePane(l)) buildLeaflet(l);
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
    map3dChanged();
    if (l.type === "vector") l.leaflet?.setStyle((f) => vecStyle(l, f));
    else l.leaflet?.setOpacity(o);
    saveLayers();
  }
  function layerBounds(l) {
    if (l.type === "vector") return l.leaflet?.getBounds();
    if (l.type === "tiles") { const b = l.tiles.bbox; return b && b[2] - b[0] < 359 ? L.latLngBounds([b[1], b[0]], [b[3], b[2]]) : null; }
    const b = l.bounds || l.info?.bounds;  // info.bounds works even before the layer has finished drawing
    return b ? L.latLngBounds(b) : null;
  }
  function zoomTo(l) {
    if (!l) return;
    const b = layerBounds(l);
    if (b?.isValid()) { map.fitBounds(b, { padding: [30, 30], maxZoom: 17 }); zoom3dTo(b); status(`Zoomed to ${l.name}`); }
    else toast(`${l.name} has no extent to zoom to yet`, true);
  }
  function zoomToLayers(list, empty = "No visible layers to zoom to") {
    let b = null;
    list.forEach((l) => { const lb = layerBounds(l); if (lb?.isValid()) b = b ? b.extend(lb) : L.latLngBounds(lb.getSouthWest(), lb.getNorthEast()); });
    if (b) { map.fitBounds(b, { padding: [30, 30] }); zoom3dTo(b); } else toast(empty);
  }
  const zoomAll = () => zoomToLayers(layers.filter((l) => l.visible));
  // a click selects one layer; Ctrl / ⌘-click adds or takes one away; Shift-click selects the run from the last clicked
  // (in the order of the list), Ctrl+Shift-click adds that run
  function selectLayer(id, e) {
    const add = e && (e.ctrlKey || e.metaKey), range = e?.shiftKey && selAnchor && getLayer(selAnchor);
    if (range) {
      const ids = $$(".layer-list .layer[data-id]").map((el) => el.dataset.id), a = ids.indexOf(selAnchor), b = ids.indexOf(id);
      if (!add) selExtra.clear();
      ids.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((x) => selExtra.add(x));
      selectedId = id;
    } else if (add) {
      if (isSelected(id)) {
        selExtra.delete(id);
        if (selectedId === id) selectedId = selExtra.size ? [...selExtra].pop() : null;
      } else { if (selectedId) selExtra.add(selectedId); selectedId = id; }
      selAnchor = id;
    } else { selExtra.clear(); selectedId = id; selAnchor = id; }
    if (selectedId) selExtra.delete(selectedId);
    refreshRibbon();
    $$(".layer-list .layer").forEach((el) => el.classList.toggle("selected", isSelected(el.dataset.id)));
    const n = selectedLayers().length;
    if (n > 1) status(`${n} layers selected · right-click for what to do with them all`);
  }
  function clearLayerSelection() { selectedId = null; selExtra.clear(); selAnchor = null; }
  // remove several layers at once (one Undo step)
  function removeLayers(list) {
    if (!list.length) return;
    if (list.length === 1) return removeLayer(list[0].id);
    historyStep(`Remove ${list.length} layers`, () => {
      list.forEach((l) => { removeLayer(l.id, { silent: true }); onLayerRemoved(l); });
      renderContents();
      saveLayers();
    });
    status(`Removed ${list.length} layers`);
  }
  // ---- groups in Contents: l.group names a layer's group; collapsed groups are remembered in this browser
  const collapsedGroups = new Set(prefs.get("collapsed-groups", []));
  const groupLayers = (name) => layers.filter((l) => l.group === name);
  function groupLayersAs(list, name) {
    name = (name || "").trim();
    if (!name || !list.length) return;
    historyStep(`Group ${list.length} layers as “${name}”`, () => {
      const at = Math.min(...list.map((l) => layers.indexOf(l)));
      const rest = layers.filter((l) => !list.includes(l));
      list.forEach((l) => { l.group = name; });
      rest.splice(Math.min(at, rest.length), 0, ...layers.filter((l) => list.includes(l)));   // together, where the topmost was
      layers.splice(0, layers.length, ...rest);
      restack(); renderContents(); saveLayers();
    });
    status(`Grouped ${list.length} layers as “${name}”`);
  }
  function ungroup(name) {
    historyStep(`Ungroup “${name}”`, () => { groupLayers(name).forEach((l) => { delete l.group; }); renderContents(); saveLayers(); });
  }
  function renameGroup(name) {
    const to = prompt("Group name", name)?.trim();
    if (!to || to === name) return;
    historyStep(`Rename group “${name}”`, () => { groupLayers(name).forEach((l) => { l.group = to; }); if (collapsedGroups.delete(name)) collapsedGroups.add(to); renderContents(); saveLayers(); });
  }
  function askGroup(list) {
    const used = new Set(layers.map((l) => l.group).filter(Boolean));
    let n = 1;
    while (used.has(`Group ${n}`)) n++;
    groupLayersAs(list, prompt(`Name of the group for ${list.length} layer${list.length === 1 ? "" : "s"}`, `Group ${n}`));
  }
  function wireGroup(wrap) {
    const name = wrap.dataset.group, head = $(".lyr-grp", wrap), cb = $("input[type=checkbox]", head);
    cb.indeterminate = cb.dataset.ind === "1";
    $(".lyr-caret", head).onclick = (e) => {
      e.stopPropagation();
      if (collapsedGroups.has(name)) collapsedGroups.delete(name); else collapsedGroups.add(name);
      prefs.set("collapsed-groups", [...collapsedGroups]);
      wrap.classList.toggle("collapsed", collapsedGroups.has(name));
    };
    cb.onchange = () => setVisibleMany(groupLayers(name), (x) => x.group === name ? cb.checked : null, `${cb.checked ? "Show" : "Hide"} group “${name}”`);
    const selectAll = () => { const g = groupLayers(name); if (!g.length) return; selectLayer(g[0].id); g.slice(1).forEach((l) => selectLayer(l.id, { ctrlKey: true })); };
    head.onclick = (e) => { if (!e.target.closest("input, button")) selectAll(); };
    head.ondblclick = (e) => { if (!e.target.closest("input, button")) renameGroup(name); };
    const menu = (x, y) => showMenu(`Group “${name}”`, [
      ["Zoom to the group", () => zoomToLayers(groupLayers(name), "The group's layers have no extent yet")],
      ["Show all", () => setVisibleMany(groupLayers(name), (l) => l.group === name ? true : null, `Show group “${name}”`)],
      ["Hide all", () => setVisibleMany(groupLayers(name), (l) => l.group === name ? false : null, `Hide group “${name}”`)],
      ["Rename…", () => renameGroup(name)],
      "-",
      ["Ungroup (keep the layers)", () => ungroup(name)],
      [`Remove the ${groupLayers(name).length} layers`, () => removeLayers(groupLayers(name)), "danger"],
    ], x, y);
    head.oncontextmenu = (e) => { e.preventDefault(); selectAll(); menu(e.clientX, e.clientY); };
    $(".lyr-more", head).onclick = (e) => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); menu(r.right, r.bottom); };
    $(".lyr-zoom", head).onclick = (e) => { e.stopPropagation(); zoomToLayers(groupLayers(name), "The group's layers have no extent yet"); };
    head.ondragstart = (e) => { e.dataTransfer.setData("text/layer-group", name); const g = groupLayers(name); if (g[0]) e.dataTransfer.setData("text/layer", g[0].id); };
  }

  // several layers to the top (0) or the bottom, keeping their order among themselves
  function moveLayers(list, toIndex) {
    historyStep(`Move ${list.length} layers`, () => {
      const rest = layers.filter((l) => !list.includes(l));
      layers.splice(0, layers.length, ...(toIndex === 0 ? [...list, ...rest] : [...rest, ...list]));
      restack(); renderContents(); saveLayers();
    });
  }
  function setVisibleMany(list, on, label) {
    historyStep(label, () => { layers.forEach((l) => { const v = on(l); if (v !== null && v !== l.visible) setVisible(l, v); }); renderContents(); });
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
    saveLayers();   // a new style is an Undo step (the picture that follows belongs to it)
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
      saveLayers({ amend: true });
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

  // files being opened: shown at the top of Contents until their layer appears (reading a large file takes a while)
  const pendingLoads = new Map();
  async function addRasterFromPath(path, { name, render, select = true, zoom = true } = {}) {
    const token = Symbol(path), label = name || path.split(/[\\/]/).pop();
    pendingLoads.set(token, label);
    renderContents();
    let info;
    try { info = await api(`/api/rasters/info?path=${encodeURIComponent(path)}`); }
    catch (e) { toast(`Couldn't open ${label}: ${e.message}`, true); throw e; }
    finally { pendingLoads.delete(token); renderContents(); }
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
    if (l.type === "tiles") return `${l.tiles.kind.toUpperCase()}${l.tiles.time ? ` · ${l.tiles.time}` : ""} · online`;
    if (l.type === "unplaced") return "no coordinate system · not on the map";
    const r = l.render || {};
    if (r.composite) return state.catalog?.composites[r.composite]?.title || r.composite;
    if (r.pca) return `${l.info?.count || ""} bands · colour view`;
    if (r.rgb) return `${l.info?.count > 3 ? `${l.info.count} bands · shown ` : ""}${l.legend?.bands ? "RGB " + l.legend.bands.join("/") : "RGB"}`;
    if (r.index) return r.index;
    if (r.formula) return "formula";
    if (r.band) return l.legend?.kind === "classes" ? "classes" : `band ${r.band}`;
    return "";
  }

  function legendHtml(l) {
    const g = l.legend;
    if (l.type === "vector" && l.symbology?.legend?.length) {
      return `<div class="lyr-meta">${esc(l.symbology.field)}</div><div class="lyr-classes">${l.symbology.legend.map((c) => `<div><i style="background:${esc(c.color)}"></i><span>${esc(c.label)}</span><span>${c.n}</span></div>`).join("")}</div>`;
    }
    if (l.type === "vector" && l.classes?.length) {
      return `<div class="lyr-classes">${l.classes.map((c) => `<div><i style="background:${esc(c.color)}"></i><span>${esc(c.name)}</span><span>${l.geojson.features.filter((f) => f.properties.class === c.name).length}</span></div>`).join("")}</div>`;
    }
    if (l.type === "vector") return `<div class="lyr-meta">${displayLabel(l)} · ${l.crs && l.crs.epsg !== 4326 ? `read as ${esc(l.crs.name)}${l.crs.epsg ? ` (EPSG:${l.crs.epsg})` : ""}, shown in WGS 84` : "EPSG:4326"}</div>`;
    if (l.type === "unplaced") return `<div class="lyr-meta" style="color:var(--warn)">${(l.geojson?.features || []).length} features without a coordinate system: right-click ▸ Coordinate system… to place them.</div>`;
    if (l.type === "tiles") {
      const t = l.tiles, ti = t.time_info;
      return `<div class="lyr-meta">${esc(t.service || "")}${t.layer ? ` · <code>${esc(t.layer)}</code>` : ""}</div>
        ${ti ? `<label class="lyr-time">Date <input type="date" data-tile-time value="${esc(t.time || ti.default || "")}" ${ti.start ? `min="${esc(ti.start)}"` : ""} ${ti.end ? `max="${esc(ti.end)}"` : ""}></label>` : ""}
        ${t.legend ? `<img class="lyr-legend-img" src="${esc(t.legend)}" alt="Legend" loading="lazy" onerror="this.remove()">` : ""}`;
    }
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
    if (l.type === "vector" && l.symbology?.legend?.length) return `<span class="lyr-ic" style="background:conic-gradient(${l.symbology.legend.slice(0, 6).map((c, i, a) => `${c.color} ${i / a.length * 100}% ${(i + 1) / a.length * 100}%`).join(",")})"></span>`;
    if (l.type === "vector") return `<span class="lyr-ic" style="border-color:${l.color};color:${l.color};background:${l.color}22">${svg("vector", 2.2)}</span>`;
    if (l.type === "tiles") return `<span class="lyr-ic">${svg("online")}</span>`;
    if (l.type === "unplaced") return `<span class="lyr-ic" style="color:var(--warn)" title="No coordinate system">⚠</span>`;
    const g = l.legend;
    if (g?.kind === "continuous") return `<span class="lyr-ic" style="background:linear-gradient(135deg, ${g.colors.join(",")})"></span>`;
    if (g?.kind === "classes") return `<span class="lyr-ic" style="background:conic-gradient(${g.classes.slice(0, 6).map((c, i, a) => `${c.color} ${i / a.length * 100}% ${(i + 1) / a.length * 100}%`).join(",")})"></span>`;
    return `<span class="lyr-ic">${svg(l.type)}</span>`;
  }

  function renderContents() {
    // in a 3D map height layers (DEMs) are listed under 3D data, the others under 2D data (one drawing order for both);
    // a 2D map has no 3D data: a DEM there is a flat raster, marked ⚠
    const in3d = (l) => is3D() && isSurface(l);
    const row = (l) => `
      <div class="layer ${isSelected(l.id) ? "selected" : ""} ${l.open ? "open" : ""}" data-id="${esc(l.id)}" draggable="true">
        <div class="lyr-row">
          <button class="lyr-caret" title="Show legend & opacity">▶</button>
          <input type="checkbox" ${l.visible ? "checked" : ""} title="Show / hide">
          ${layerIcon(l)}
          <span class="lyr-name" title="${esc(l.name)}${l.path ? "\n" + esc(l.path) : ""}">${esc(l.name)}<small>${esc(displayLabel(l))}</small></span>
          ${!is3D() && isSurface(l) ? `<span class="lyr-warn" title="${esc(WARN_3D_IN_2D)}" aria-label="3D data in a 2D map">⚠</span>` : ""}
          ${l.busy ? '<span class="spinner"></span>' : ""}
          <button class="lyr-zoom" title="Zoom to layer"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M11 8v6M8 11h6"/></svg></button>
          <button class="lyr-more" title="Layer options">⋯</button>
        </div>
        ${l.busy && !l.image ? `<div class="lyr-note">Loading…${l.info?.count > 3 ? ` ${l.info.count} bands` : ""}${l.info?.size_mb > 150 ? ` · ${fmt(l.info.size_mb, 0)} MB: a large file takes a little while the first time` : ""}</div>` : ""}
        ${l.error ? `<div class="lyr-err">⚠ ${esc(l.error)} <span class="lyr-err-act"><button type="button" class="btn small" data-lyr-retry>Try again</button><button type="button" class="btn small ghost" data-lyr-rm>Remove</button></span></div>` : ""}
        <div class="lyr-body">
          ${legendHtml(l)}
          <label>Opacity <input type="range" min="0" max="100" value="${Math.round(l.opacity * 100)}" data-op></label>
          ${l.path ? `<div class="lyr-meta">${esc(l.path)}${l.info ? ` · ${esc(l.info.crs)} · ${l.info.width}×${l.info.height}` : ""}</div>` : ""}
        </div>
      </div>`;
    // layers of one group (l.group) under a header of their own; the drawing order stays that of the list
    const grouped = (list) => {
      let html = "", open = null;
      for (const l of list) {
        if ((l.group || null) !== open) {
          if (open) html += "</div></div>";
          open = l.group || null;
          if (open) {
            const members = list.filter((x) => x.group === open), shown = members.filter((x) => x.visible).length;
            html += `<div class="lyr-grp-wrap ${collapsedGroups.has(open) ? "collapsed" : ""}" data-group="${esc(open)}">
              <div class="lyr-grp" draggable="true" title="${esc(open)}: drag onto Linked views to show all its layers">
                <button class="lyr-caret" title="Show / hide the group's layers">▶</button>
                <input type="checkbox" ${shown ? "checked" : ""} data-ind="${shown && shown < members.length ? 1 : 0}" title="Show / hide all">
                <span class="lyr-ic grp-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></span>
                <span class="lyr-name">${esc(open)}<small>${members.length} layer${members.length === 1 ? "" : "s"}</small></span>
                <button class="lyr-zoom" title="Zoom to the group"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M11 8v6M8 11h6"/></svg></button>
                <button class="lyr-more" title="Group options">⋯</button></div><div class="lyr-grp-body">`;
          }
        }
        html += row(l);
      }
      return html + (open ? "</div></div>" : "");
    };
    $("#layer-list").innerHTML = [...pendingLoads.values()].map((n) => `<div class="layer pending"><div class="lyr-row"><span class="spinner"></span>
        <span class="lyr-name">${esc(n)}<small>Opening the file…</small></span></div></div>`).join("") + grouped(layers.filter((l) => !in3d(l)));
    $("#layer-list-3d").innerHTML = grouped(layers.filter(in3d));
    $$(".layer-list .lyr-grp-wrap").forEach(wireGroup);
    $("#sect-3d").classList.toggle("hidden", !is3D());
    $$(".layer-list .layer[data-id]").forEach((el) => {
      const l = getLayer(el.dataset.id);
      el.onclick = (e) => { if (!e.target.closest("input, button")) selectLayer(l.id, e); };
      el.onmousedown = (e) => { if (e.shiftKey && !e.target.closest("input, button")) e.preventDefault(); };   // no text selection on Shift-click
      el.ondblclick = (e) => { if (!e.target.closest("input, button")) zoomTo(l); };
      el.oncontextmenu = (e) => {
        e.preventDefault();
        if (!isSelected(l.id) || selectedLayers().length < 2) selectLayer(l.id);   // right-click inside a multi-selection keeps it
        showCtx(l, e.clientX, e.clientY);
      };
      $("input[type=checkbox]", el).onchange = (e) => setVisible(l, e.target.checked);
      $("[data-lyr-retry]", el)?.addEventListener("click", (e) => { e.stopPropagation(); renderRaster(l).catch(() => {}); });
      $("[data-lyr-rm]", el)?.addEventListener("click", (e) => { e.stopPropagation(); removeLayer(l.id); });
      $(".lyr-caret", el).onclick = () => { l.open = !l.open; el.classList.toggle("open", l.open); };
      $(".lyr-zoom", el).onclick = (e) => { e.stopPropagation(); selectLayer(l.id); zoomTo(l); };
      $(".lyr-more", el).onclick = (e) => { e.stopPropagation(); selectLayer(l.id); const r = e.currentTarget.getBoundingClientRect(); showCtx(l, r.right, r.bottom); };
      $("[data-op]", el).oninput = (e) => setOpacity(l, e.target.value / 100);
      $("[data-tile-time]", el)?.addEventListener("change", (e) => { l.tiles.time = e.target.value; buildLeaflet(l); restack(); renderContents(); saveLayers(); });
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
        if (is3D()) setLayer3d(getLayer(id), isSurface(l));   // dropped among the other section's layers: it moves to that section
        const moved = getLayer(id);
        if (moved && (moved.group || null) !== (l.group || null)) { if (l.group) moved.group = l.group; else delete moved.group; }   // and joins its group
        moveLayer(id, from < target ? target : target);
      };
    });
    updateSectionCounts();
    map3dChanged();
    swipeLayersChanged();
    linkedViewsChanged();
    timeSliderChanged();
    refreshRibbon();
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

  // a raster in 3D data (its values are heights: the land in a 3D map) or in 2D data (draped)
  const WARN_3D_IN_2D = "3D data (heights) in a 2D map: it is shown flat here. Open or paste it into a 3D map (Insert ▸ 3D) to see the land in 3D.";
  function setLayer3d(l, on) {
    if (on && !is3D()) return toast("⚠ A 2D map has no 3D data: open a 3D map (Insert ▸ 3D) to use a raster's values as heights", true);
    if (!l || l.type !== "raster" || isSurface(l) === on) return;
    l.view3d = on ? "surface" : "drape";
    renderContents(); saveLayers();
    status(on ? `${l.name} is in 3D data: in a 3D map its values are the heights` : `${l.name} is in 2D data: in a 3D map it is draped`);
  }
  // drop a layer on a section's empty space (or its header) to move it there
  [["#sect-2d", false], ["#sect-3d", true]].forEach(([sel, on]) => {
    const sect = $(sel);
    sect.addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("text/layer")) { e.preventDefault(); sect.classList.add("drag-over"); } });
    sect.addEventListener("dragleave", (e) => { if (!sect.contains(e.relatedTarget)) sect.classList.remove("drag-over"); });
    sect.addEventListener("drop", (e) => {
      sect.classList.remove("drag-over");
      const id = e.dataTransfer.getData("text/layer");
      if (!id) return;
      e.preventDefault();
      const l = getLayer(id);
      if (l?.type === "raster") setLayer3d(l, on);
      else if (l && on) toast("Only rasters can be 3D data (their values become heights)", true);
    });
  });

  function onLayerRemoved(l) {
    if (vw.tabs.some((t) => t.key === "attr:" + l.id)) closeTab("attr:" + l.id);
    if (l.id === "aoi") { state.aoi = null; $("#aoi-summary").classList.add("hidden"); $("#aoi-warn").classList.add("hidden"); $("#aoi-emb").classList.add("hidden"); }
    if (an.layer?.id === l.id) resetAnalyze();
    if (an.resultId === l.id) { an.resultId = null; an.sel = null; $("#an-result").classList.add("hidden"); renderIndexButtons(); }
  }

  // persistence: layer list survives reloads (preview images are not kept)
  function saveLayers(opts) {
    try {
      if (!inProject()) {
        const text = JSON.stringify(layers.filter((l) => l.type !== "image").map(layerState));
        if (text.length < 4e6) localStorage.setItem("lulc-layers", text);
        localStorage.setItem("lulc-saved-at", Date.now());
        saveMaps();
      }
    } catch {}
    scheduleProjectSave();
    historyNote(opts);   // Undo / Redo
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
      } else if (d.type === "tiles" || d.type === "unplaced") addLayer(d, { select: false });
    }
    clearLayerSelection();
    renderContents();
  }
