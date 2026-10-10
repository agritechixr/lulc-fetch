  // View ▸ Compare ▸ Linked views: 2 to 4 maps side by side over the 2D map, each with layers of its own (drag a layer from
  // Contents onto a view, or pick it there). Linked views (the chain button) pan and zoom together and show where the
  // mouse is in the others; unlink one to move it on its own.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  const lv = { on: false, n: 0, panes: [], busy: false };
  const LV_MAX = 4;
  const LINK_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/></svg>';
  const lvCandidates = () => layers.filter((l) => l.type !== "unplaced" && l.type !== "image" || (l.type === "image" && l.url));

  function startLinkedViews(n, layout = null) {
    if (is3D()) return toast("Linked views work on 2D maps → open a 2D map tab", true);
    stopSwipe(true);
    if (link.on) stopSideBySide(true);
    // a new number of views keeps the views' layers, links and place
    const keep = layout ? layout.panes : lv.on ? lv.panes.map((p) => ({ ids: p.ids, linked: p.linked })) : [];
    const from = lv.on && (lv.panes.find((p) => p.linked) || lv.panes[0]);
    const view = layout?.view ? { center: L.latLng(layout.view.center), zoom: layout.view.zoom }
      : from ? { center: from.map.getCenter(), zoom: from.map.getZoom() } : { center: map.getCenter(), zoom: map.getZoom() };
    if (layout) n = layout.n;
    stopLinkedViews(true, false);
    const shown = lvCandidates().filter((l) => l.visible);
    lv.n = Math.max(2, Math.min(LV_MAX, n || Math.min(LV_MAX, Math.max(2, shown.length))));
    lv.on = true;
    $("#lv").classList.remove("hidden");
    $("#lv-grid").dataset.n = lv.n;
    $$("#lv-n button").forEach((b) => b.classList.toggle("on", +b.dataset.n === lv.n));
    lv.panes = Array.from({ length: lv.n }, (_, i) => makeLvPane(i, keep[i] || { ids: keep.length ? [] : shown[i] ? [shown[i].id] : [], linked: true }, view));
    refreshRibbon();
    status("Linked views: drag layers from Contents onto a view · 🔗 links its pan and zoom · Esc to close");
  }
  function makeLvPane(i, { ids, linked }, view) {
    const el = document.createElement("div");
    el.className = "lv-pane";
    el.innerHTML = `<div class="lv-head"><button type="button" class="lv-link" title="">${LINK_SVG}</button><div class="lv-chips"></div>
      <select class="lv-add" aria-label="Add a layer to this view"></select></div>
      <div class="lv-map"></div><div class="lv-cursor hidden"></div><div class="lv-drop">Drop a layer here (from Contents)</div>`;
    $("#lv-grid").appendChild(el);
    const m = L.map($(".lv-map", el), { zoomControl: true, attributionControl: false, zoomSnap: 0, zoomDelta: 0.5, wheelPxPerZoomLevel: 90 }).setView(view.center, view.zoom);
    const bm = BASEMAPS[prefs.get("basemap", "streets")];
    if (bm) L.tileLayer(bm._url, bm.options).addTo(m);
    const p = { el, map: m, ids: ids.filter((id) => getLayer(id)), linked, drawn: [] };
    m.on("move", () => lvSync(p));
    m.on("mousemove", (e) => lvCursor(p, e.latlng));
    m.on("mouseout", () => lvCursor(p, null));
    $(".lv-link", el).onclick = () => setLvLinked(p, !p.linked);
    $(".lv-add", el).onchange = (e) => { if (e.target.value) addToLvPane(p, e.target.value); e.target.value = ""; };
    // drag a layer from Contents onto the view
    el.addEventListener("dragover", (e) => { const t = [...e.dataTransfer.types]; if (t.includes("text/layer") || t.includes("text/layer-group")) { e.preventDefault(); el.classList.add("drag-over"); } });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drag-over"); });
    el.addEventListener("drop", (e) => {
      el.classList.remove("drag-over");
      const grp = e.dataTransfer.getData("text/layer-group"), id = e.dataTransfer.getData("text/layer");
      if (!id && !grp) return;
      e.preventDefault(); e.stopPropagation();
      // a group brings all its layers, and so does dragging one of several selected layers
      const many = grp ? groupLayers(grp).map((l) => l.id) : isSelected(id) ? selectedLayers().map((l) => l.id) : [id];
      many.forEach((x) => addToLvPane(p, x, false));
      drawLvPane(p);
    });
    setLvLinked(p, linked, false);
    drawLvPane(p);
    setTimeout(() => m.invalidateSize(), 30);
    return p;
  }
  function addToLvPane(p, id, draw = true) {
    if (!p.ids.includes(id)) p.ids.push(id);
    if (draw) drawLvPane(p);
  }
  // the view's layers in the order of Contents (the top one drawn last), and its chips and picker
  function drawLvPane(p) {
    p.drawn.forEach((o) => o.remove());
    p.drawn = [];
    p.ids = p.ids.filter((id) => getLayer(id));
    const list = layers.filter((l) => p.ids.includes(l.id));
    for (const l of [...list].reverse()) {
      let o = null;
      if (l.type === "vector") o = L.geoJSON(l.geojson, { interactive: false, style: (f) => vecStyle(l, f),
        pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, ...vecStyle(l, f), fillOpacity: 0.85 * l.opacity, interactive: false }) });
      else if ((l.type === "image" && l.url) || (l.type === "raster" && l.image)) o = L.imageOverlay(l.url || l.image, l.bounds, { opacity: l.opacity, interactive: false });
      else if (l.type === "tiles") o = tileLeaflet(l, "overlayPane");
      if (o) { o.addTo(p.map); p.drawn.push(o); }
    }
    $(".lv-chips", p.el).innerHTML = list.map((l) => `<span class="lv-chip" title="${esc(l.name)}">${esc(l.name)}<button type="button" data-rm="${esc(l.id)}" title="Take it out of this view">×</button></span>`).join("")
      || `<span class="muted">No layers</span>`;
    $$(".lv-chip [data-rm]", p.el).forEach((b) => b.onclick = () => { p.ids = p.ids.filter((x) => x !== b.dataset.rm); drawLvPane(p); });
    const rest = lvCandidates().filter((l) => !p.ids.includes(l.id));
    $(".lv-add", p.el).innerHTML = `<option value="">+ Layer</option>` + rest.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
    $(".lv-add", p.el).disabled = !rest.length;
    p.el.classList.toggle("empty", !list.length);
  }
  function setLvLinked(p, on, snap = true) {
    p.linked = on;
    const b = $(".lv-link", p.el);
    b.classList.toggle("on", on);
    b.title = on ? "Linked: moves with the other linked views (click to move it on its own)" : "Not linked: click to link it (it jumps to the linked views)";
    if (on && snap) { const o = lv.panes.find((x) => x !== p && x.linked); if (o) { lv.busy = true; try { p.map.setView(o.map.getCenter(), o.map.getZoom(), { animate: false }); } finally { lv.busy = false; } } }
    $("#lv-linkall").classList.toggle("on", lv.panes.length > 0 && lv.panes.every((x) => x.linked));
  }
  function lvSync(src) {
    if (lv.busy || !src.linked) return;
    lv.busy = true;
    try {
      const c = src.map.getCenter(), z = src.map.getZoom();
      lv.panes.forEach((p) => { if (p !== src && p.linked) p.map.setView(c, z, { animate: false }); });
    } finally { lv.busy = false; }
  }
  // the mouse's place shown as a cross in the other linked views
  function lvCursor(src, ll) {
    lv.panes.forEach((p) => {
      const c = $(".lv-cursor", p.el);
      if (p === src || !ll || !src.linked || !p.linked) return c.classList.add("hidden");
      const pt = p.map.latLngToContainerPoint(ll);
      c.style.left = `${pt.x}px`; c.style.top = `${pt.y + $(".lv-map", p.el).offsetTop}px`;
      c.classList.remove("hidden");
    });
  }
  function stopLinkedViews(silent, keepView = true) {
    if (!lv.on) return;
    const first = lv.panes.find((p) => p.linked) || lv.panes[0];
    if (keepView && first) map.setView(first.map.getCenter(), first.map.getZoom(), { animate: false });
    lv.panes.forEach((p) => p.map.remove());
    lv.on = false;
    $("#lv-grid").innerHTML = "";
    $("#lv").classList.add("hidden");
    lv.panes = [];
    refreshRibbon();
    if (!silent) status("Linked views closed");
  }
  // Contents changed (a layer removed, restyled, renamed or added): the views follow
  function linkedViewsChanged() { if (lv.on) lv.panes.forEach(drawLvPane); }

  $$("#lv-n button").forEach((b) => b.onclick = () => startLinkedViews(+b.dataset.n));
  $("#lv-linkall").innerHTML = `${LINK_SVG}<span>Link all</span>`;
  $("#lv-linkall").onclick = () => { const all = !lv.panes.every((p) => p.linked); lv.panes.forEach((p) => setLvLinked(p, all)); };
  $("#lv-x").onclick = () => stopLinkedViews();
  // saved layouts (in this browser): the number of views, each view's layers and link, and the place
  const lvLayouts = () => prefs.get("lv-layouts", []);
  $("#lv-layouts").onclick = (e) => {
    const r = e.currentTarget.getBoundingClientRect(), saved = lvLayouts();
    showMenu("Linked-view layouts", [
      ["Save these views as a layout…", () => {
        const name = prompt("Layout name", `Layout ${saved.length + 1}`)?.trim();
        if (!name) return;
        const p = lv.panes.find((x) => x.linked) || lv.panes[0], c = p.map.getCenter();
        const item = { name, n: lv.n, panes: lv.panes.map((x) => ({ ids: x.ids, linked: x.linked })), view: { center: [c.lat, c.lng], zoom: p.map.getZoom() } };
        prefs.set("lv-layouts", [...saved.filter((s) => s.name !== name), item]);
        status(`Layout “${name}” saved`);
      }],
      ...(saved.length ? ["-"] : []),
      ...saved.map((s) => [`Open “${s.name}” (${s.n} views)`, () => {
        const missing = s.panes.flatMap((p) => p.ids).filter((id) => !getLayer(id)).length;
        startLinkedViews(s.n, s);
        if (missing) toast(`${missing} of the layout's layers are no longer in Contents`, true);
      }]),
      ...(saved.length ? ["-", ...saved.map((s) => [`Delete “${s.name}”`, () => prefs.set("lv-layouts", lvLayouts().filter((x) => x.name !== s.name)), "danger"])] : []),
    ], r.left, r.bottom + 4);
  };
  L.DomEvent.disableClickPropagation($("#lv"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && lv.on && !$("dialog[open]") && !document.activeElement?.matches?.("input, select, textarea")) stopLinkedViews(); });
