  // Maps: several 2D and 3D maps (Insert ▸ 2D / 3D), each with its own Contents, as tabs under the ribbon. Double-click a
  // tab to rename it; Ctrl+C / Ctrl+V copies the selected layer from one map to another.
  // All 2D maps share the one Leaflet map: opening a map tab swaps its layers and view in. A 3D map draws its layers in
  // the 3D view on top of it (map3d.js).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ maps (tabs under the ribbon)
  // each map: { id, name, kind: "2d" | "3d", view, cam (3D camera), live (its layers while another map is open),
  //             stored (its saved layers, until it is first opened) }
  const docs = { list: [], active: null, clip: null };
  const activeDoc = () => docs.list.find((d) => d.id === docs.active);
  const is3D = () => activeDoc()?.kind === "3d";
  const newMapId = () => `map-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
  // a layer as saved (no Leaflet object, picture or legend: those are made again when it is opened)
  const layerState = ({ leaflet, image, busy, error, legend, _original, ...rest }) =>
    _original !== undefined ? { ...rest, geojson: { ...rest.geojson, features: JSON.parse(_original) } } : rest;
  const currentView = () => { const c = map.getCenter(); return { center: [+c.lat.toFixed(6), +c.lng.toFixed(6)], zoom: +map.getZoom().toFixed(2) }; };

  // what is saved: every map but the open one keeps its layers here (the open one's are saved as before, in `layers`)
  function mapsState() {
    return { active: docs.active, maps: docs.list.map((d) => ({ id: d.id, name: d.name, kind: d.kind, origin: d.origin || null,
      view: d.id === docs.active ? currentView() : d.view || null, cam: d.id === docs.active && d.kind === "3d" ? map3dCam() || d.cam : d.cam || null,
      layers: d.id === docs.active ? undefined : d.live ? d.live.filter((l) => l.type !== "image").map(layerState) : d.stored || [] })) };
  }
  function saveMaps() {
    if (inProject()) return;
    try { const t = JSON.stringify(mapsState()); if (t.length < 4e6) localStorage.setItem("lulc-maps", t); } catch {}
  }
  // the maps of a project (or of the temporary workspace, from the browser); the open map's layers are restored after this
  function restoreMaps(saved) {
    if (saved === undefined) try { saved = JSON.parse(localStorage.getItem("lulc-maps") || "null"); } catch { saved = null; }
    if (v3.on) hide3d(null);
    docs.list = (saved?.maps?.length ? saved.maps : [{ id: "map-1", name: "Map", kind: "2d" }]).map((m) => ({
      id: m.id || newMapId(), name: m.name || "Map", kind: m.kind === "3d" ? "3d" : "2d", view: m.view || null, cam: m.cam || null,
      origin: m.origin || null, stored: m.layers || null, live: null }));
    docs.active = docs.list.some((d) => d.id === saved?.active) ? saved.active : docs.list[0].id;
    renderMapTabs();
    if (is3D()) show3d(activeDoc());
  }

  // open another map: the open map's layers leave the map (kept as they are), the other map's come in
  function switchMap(id) {
    const cur = activeDoc(), next = docs.list.find((d) => d.id === id);
    if (!next || next === cur) return;
    if (cur) {
      cur.view = currentView();
      if (cur.kind === "3d") hide3d(cur);
      layers.forEach((l) => l.leaflet?.remove());
      cur.live = layers.splice(0);
      [...vw.tabs].filter((t) => t.key.startsWith("attr:")).forEach((t) => closeTab(t.key, true));
    }
    selectedId = null;
    docs.active = next.id;
    if (next.view?.center) map.setView(next.view.center, next.view.zoom ?? map.getZoom(), { animate: false });
    if (next.live) {
      layers.push(...next.live);
      next.live = null;
      layers.forEach((l) => {
        if (l.visible && l.leaflet) l.leaflet.addTo(map);
        if (l.type === "raster" && !l.image && !l.busy) renderRaster(l).catch(() => {});   // it was still drawing when the map was left
      });
      restack();
      renderContents();
    } else {
      restoreLayers(next.stored || []);
      next.stored = null;
    }
    if (next.kind === "3d") show3d(next);
    renderMapTabs();
    saveLayers();
    status(`Map: ${next.name}`);
  }

  function uniqueMapName(base) {
    let n = 1;
    while (docs.list.some((d) => d.name === `${base} ${n}`)) n++;
    return `${base} ${n}`;
  }
  // Insert ▸ 2D / 3D: a new, empty map at the place the open map shows; its name is ready to type
  function newMap(kind, { name, stored = [] } = {}) {
    const d = { id: newMapId(), name: name || uniqueMapName(kind === "3d" ? "3D map" : "Map"), kind, view: currentView(), cam: null,
                origin: null, stored, live: null };
    docs.list.splice(docs.list.indexOf(activeDoc()) + 1, 0, d);
    switchMap(d.id);
    if (!name) editMapName(d.id);
    return d;
  }
  function duplicateMap(id) {
    const src = docs.list.find((d) => d.id === id);
    if (!src) return;
    const list = src.id === docs.active ? layers : src.live || null;
    const stored = (list ? list.filter((l) => l.type !== "image").map(layerState) : src.stored || []).map((l) => copyOfLayer(l));
    newMap(src.kind, { name: `${src.name} (copy)`, stored });
  }
  function closeMap(id) {
    const d = docs.list.find((x) => x.id === id);
    if (!d) return;
    if (docs.list.length === 1) return toast("This is the only map: there has to be one", true);
    const n = d.id === docs.active ? layers.length : (d.live || d.stored || []).length;
    if (n && !confirm(`Close the map “${d.name}” and its ${n} layer${n === 1 ? "" : "s"}? Files on disk are kept.`)) return;
    if (d.id === docs.active) {
      const i = docs.list.indexOf(d);
      switchMap(docs.list[i + 1]?.id || docs.list[i - 1].id);
    }
    (d.live || []).forEach((l) => l.leaflet?.remove());
    docs.list.splice(docs.list.indexOf(d), 1);
    renderMapTabs();
    saveLayers();
  }
  function renameMap(id, name) {
    const d = docs.list.find((x) => x.id === id);
    name = name.trim().slice(0, 80);
    if (d && name) { d.name = name; saveLayers(); }
    renderMapTabs();
  }

  // ---- copy & paste layers between maps (Ctrl+C / Ctrl+V, Insert ▸ Layers, the layer menu)
  const copyOfLayer = (s) => { const c = structuredClone(s); delete c.id; if (s.id === "aoi") c.name = s.name || "Area of interest"; return c; };
  function copyLayers(list) {
    list = list.filter((l) => l.type !== "image");
    if (!list.length) return toast("Select a layer in Contents first", true);
    docs.clip = list.map((l) => structuredClone(layerState(l)));
    toast(list.length === 1 ? `Copied “${list[0].name}”: open another map and press Ctrl+V` : `Copied ${list.length} layers: open another map and press Ctrl+V`);
  }
  function pasteLayers() {
    if (!docs.clip?.length) return toast("Nothing copied yet: select a layer in Contents and press Ctrl+C", true);
    let last = null;
    for (const s of [...docs.clip].reverse()) {   // bottom first, so the order stays the same
      const c = copyOfLayer(s);
      last = addLayer(c, { select: false });
      if (last.type === "raster") renderRaster(last).catch(() => {});
    }
    selectLayer(last.id);
    toast(`Pasted ${docs.clip.length === 1 ? `“${docs.clip[0].name}”` : `${docs.clip.length} layers`} into “${activeDoc().name}”`);
  }

  // ---- the tabs
  const MAP_IC = { "2d": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14" opacity=".55"/></svg>',
                   "3d": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9" opacity=".6"/></svg>' };
  function renderMapTabs() {
    const box = $("#map-tabs");
    box.innerHTML = docs.list.map((d) => `<div class="mt-tab ${d.id === docs.active ? "on" : ""}" data-map="${esc(d.id)}" role="tab" aria-selected="${d.id === docs.active}"
        title="${esc(d.name)} · ${d.kind === "3d" ? "3D" : "2D"} map${d.id === docs.active ? "" : ": click to open"} · double-click to rename">
        <span class="mt-ic ${d.kind}">${MAP_IC[d.kind]}</span><span class="mt-kind">${d.kind === "3d" ? "3D" : "2D"}</span><span class="mt-name">${esc(d.name)}</span>
        <button type="button" class="mt-x" title="Close this map" aria-label="Close ${esc(d.name)}">×</button></div>`).join("") +
      `<button type="button" class="mt-add" id="mt-add" title="New map (2D or 3D)" aria-label="New map">+</button>`;
    $$(".mt-tab", box).forEach((el) => {
      const id = el.dataset.map;
      el.onclick = (e) => { if (!e.target.closest(".mt-x, input")) switchMap(id); };
      el.ondblclick = (e) => { if (!e.target.closest(".mt-x, input")) editMapName(id); };
      el.oncontextmenu = (e) => { e.preventDefault(); showMapTabMenu(id, e.clientX, e.clientY); };
      $(".mt-x", el).onclick = (e) => { e.stopPropagation(); closeMap(id); };
    });
    $("#mt-add").onclick = (e) => {
      e.stopPropagation();
      const r = e.currentTarget.getBoundingClientRect();
      showMenu("New map", [["2D map", () => newMap("2d")], ["3D map", () => newMap("3d")]], r.left, r.bottom + 4);
    };
  }
  function showMapTabMenu(id, x, y) {
    const d = docs.list.find((m) => m.id === id);
    showMenu(d.name, [
      ["Open", () => switchMap(id)],
      ["Rename", () => editMapName(id)],
      ["Duplicate (with its layers)", () => duplicateMap(id)],
      "-",
      ["Copy all its layers", () => copyLayers(id === docs.active ? layers : d.live || (d.stored || []))],
      id === docs.active && docs.clip?.length ? [`Paste ${docs.clip.length === 1 ? "the copied layer" : `${docs.clip.length} copied layers`}`, () => pasteLayers()] : null,
      "-",
      docs.list.length > 1 ? ["Close map", () => closeMap(id), "danger"] : null,
    ].filter(Boolean), x, y);
  }
  // rename in place: Enter (or clicking away) keeps the name, Esc leaves it as it was
  function editMapName(id) {
    const el = $(`#map-tabs [data-map="${CSS.escape(id)}"]`), d = docs.list.find((m) => m.id === id);
    if (!el || !d || $("input", el)) return;
    const label = $(".mt-name", el);
    label.innerHTML = `<input type="text" maxlength="80" aria-label="Map name" value="${esc(d.name)}">`;
    const inp = $("input", label);
    let done = false;
    const finish = (keep) => { if (done) return; done = true; if (keep) renameMap(id, inp.value); else renderMapTabs(); };
    inp.onkeydown = (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    };
    inp.onclick = inp.ondblclick = (e) => e.stopPropagation();
    inp.onblur = () => finish(true);
    inp.focus(); inp.select();
  }
