  // View ▸ Compare: Swipe and Side by side.
  // Swipe (2D maps): two layers, one each side of a line you drag across the map, e.g. before / after, or NDVI 2023 and
  // 2024. Each sits in a pane of its own, cut at the line (other layers show on both sides).
  // Side by side: the open 2D map on the left, a 3D map on the right; moving one moves the other.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ swipe
  const cmp = { on: false, left: null, right: null, x: 0.5 };
  map.createPane("cmpL").style.zIndex = 405;
  map.createPane("cmpR").style.zIndex = 406;
  function startSwipe() {
    if (is3D()) return toast("Swipe works on 2D maps → open a 2D map tab (or use Side by side for 2D and 3D)", true);
    const cand = layers.filter((l) => l.visible && l.type !== "image");
    if (cand.length < 2) return toast("Swipe compares two layers → add (or show) at least two layers first", true);
    stopSwipe(true);
    cmp.on = true; cmp.x = 0.5;
    setSwipeLayers(cand[1].id, cand[0].id);   // the top layer on the right, the next one on the left
    $("#swipe").classList.remove("hidden");
    status("Drag the line to compare · Esc to stop");
  }
  function setSwipeLayers(leftId, rightId) {
    [cmp.left, cmp.right].forEach((id) => { const l = getLayer(id); if (l) { delete l._pane; buildLeaflet(l); } });
    cmp.left = leftId; cmp.right = rightId;
    [[leftId, "cmpL"], [rightId, "cmpR"]].forEach(([id, pane]) => { const l = getLayer(id); if (l) { l._pane = pane; buildLeaflet(l); } });
    restack();
    renderSwipe();
  }
  function stopSwipe(silent) {
    if (!cmp.on) return;
    cmp.on = false;
    [cmp.left, cmp.right].forEach((id) => { const l = getLayer(id); if (l) { delete l._pane; buildLeaflet(l); } });
    restack();
    cmp.left = cmp.right = null;
    ["cmpL", "cmpR"].forEach((p) => { map.getPane(p).style.clip = ""; });
    $("#swipe").classList.add("hidden");
    if (!silent) status("Swipe closed");
  }
  // cut the panes at the line (in the panes' own pixels)
  function clipSwipe() {
    if (!cmp.on) return;
    const size = map.getSize(), nw = map.containerPointToLayerPoint([0, 0]), se = map.containerPointToLayerPoint(size), cx = nw.x + size.x * cmp.x;
    map.getPane("cmpL").style.clip = `rect(${nw.y}px, ${cx}px, ${se.y}px, ${nw.x}px)`;
    map.getPane("cmpR").style.clip = `rect(${nw.y}px, ${se.x}px, ${se.y}px, ${cx}px)`;
    $("#swipe-line").style.left = `${cmp.x * 100}%`;
  }
  map.on("move zoom zoomend resize viewreset", clipSwipe);
  function renderSwipe() {
    const opts = (sel) => layers.filter((l) => l.type !== "image").map((l) => `<option value="${esc(l.id)}" ${l.id === sel ? "selected" : ""}>${esc(l.name)}</option>`).join("");
    $("#swipe-left").innerHTML = opts(cmp.left);
    $("#swipe-right").innerHTML = opts(cmp.right);
    clipSwipe();
  }
  $("#swipe-left").onchange = (e) => setSwipeLayers(e.target.value, cmp.right === e.target.value ? cmp.left : cmp.right);
  $("#swipe-right").onchange = (e) => setSwipeLayers(cmp.left === e.target.value ? cmp.right : cmp.left, e.target.value);
  $("#swipe-x").onclick = () => stopSwipe();
  $("#swipe-flip").onclick = () => setSwipeLayers(cmp.right, cmp.left);
  // drag the handle (or the line) to move it; the map doesn't pan meanwhile
  $("#swipe-line").addEventListener("pointerdown", (e) => {
    e.preventDefault(); e.stopPropagation();
    const el = e.currentTarget, r = $("#map").getBoundingClientRect();
    el.setPointerCapture(e.pointerId);
    const move = (ev) => { cmp.x = Math.max(0.02, Math.min(0.98, (ev.clientX - r.left) / r.width)); clipSwipe(); };
    const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  });
  L.DomEvent.disableClickPropagation($("#swipe"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && cmp.on && !$("dialog[open]")) stopSwipe(); });
  // a compared layer removed: the swipe ends
  function swipeLayersChanged() { if (cmp.on && (!getLayer(cmp.left) || !getLayer(cmp.right))) stopSwipe(); else if (cmp.on) renderSwipe(); }

  // ------------------------------------------------------------------ side by side (2D left, 3D right, linked)
  const link = { on: false, doc: null, busy: false };
  const mppAt = (z, lat) => 40075016.686 * Math.cos(lat * Math.PI / 180) / 2 ** (z + 8);
  function startSideBySide() {
    const threeD = docs.list.filter((d) => d.kind === "3d"), twoD = docs.list.filter((d) => d.kind === "2d");
    const r = $(`#menus .menu-pop [data-cmd="side-by-side"]`)?.getBoundingClientRect() || { left: 300, bottom: 140 };
    if (!is3D()) {
      showMenu("Show beside this 2D map", [
        ...threeD.map((d) => [`3D map “${d.name}”`, () => linkTo(d)]),
        ["A new 3D map with these layers", () => {
          const stored = layers.filter((l) => l.type !== "image").map(layerState).map(copyOfLayer);
          const d = { id: newMapId(), name: uniqueMapName("3D map"), kind: "3d", view: currentView(), cam: null, origin: null, stored, live: null };
          docs.list.splice(docs.list.indexOf(activeDoc()) + 1, 0, d);
          renderMapTabs(); saveLayers();
          linkTo(d);
        }],
      ], r.left, r.bottom + 4);
    } else {
      if (!twoD.length) return toast("Side by side needs a 2D map too → Insert ▸ 2D", true);
      const me = activeDoc();
      showMenu("Show this 3D map beside", twoD.map((d) => [`2D map “${d.name}”`, () => { switchMap(d.id); linkTo(me); }]), r.left, r.bottom + 4);
    }
  }
  async function linkTo(doc) {
    stopSideBySide(true);
    stopSwipe(true);
    // the 3D map's layers as objects (never drawn in 2D here): their pictures are made for the 3D view
    if (!doc.live) { doc.live = (doc.stored || []).map((s) => ({ visible: true, opacity: 1, ...structuredClone(s), id: s.id || `${s.type}-${Math.random().toString(36).slice(2, 9)}` })); doc.stored = null; }
    link.on = true; link.doc = doc;
    v3.src = doc.live;
    document.body.classList.add("side-by-side");
    setTimeout(() => map.invalidateSize(), 30);
    await show3d(doc);
    renderMapTabs();
    for (const l of doc.live.filter((x) => x.type === "raster" && !x.image && x.path)) {
      try {
        const res = await api("/api/analyze/render", { method: "POST", json: { path: l.path, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0, ...l.render } });
        const { image, bounds, ...legend } = res;
        Object.assign(l, { image, bounds, legend });
        map3dChanged();
      } catch (e) { toast(`${l.name}: ${e.message}`, true); }
    }
    syncFrom2d();
    status(`Side by side: “${activeDoc().name}” and “${doc.name}” move together · View ▸ Side by side again to stop`);
  }
  function stopSideBySide(silent) {
    if (!link.on) return;
    link.on = false;
    hide3d(link.doc);
    v3.src = null;
    link.doc = null;
    document.body.classList.remove("side-by-side");
    setTimeout(() => map.invalidateSize(), 30);
    renderMapTabs();
    if (!silent) status("Side by side closed");
  }
  // moving one moves the other: the centre, and a distance that shows about the same width of land
  function syncFrom2d() {
    if (!link.on || link.busy || !v3.controls) return;
    link.busy = true;
    try {
      const c = map.getCenter(), [mx, my] = toMerc(c.lng, c.lat), [x, y] = toScene(mx, my), t = v3.controls.target;
      const span = map.getSize().y * mppAt(map.getZoom(), c.lat), dist = span / 2 / Math.tan(v3.camera.fov * Math.PI / 360);
      const dir = v3.camera.position.clone().sub(t).normalize();
      t.set(x, y, t.z);
      v3.camera.position.copy(t).addScaledVector(dir, dist);
      v3.controls.update();
    } finally { link.busy = false; }
  }
  function syncFrom3d() {
    if (!link.on || link.busy) return;
    link.busy = true;
    try {
      const t = v3.controls.target, [lon, lat] = fromMerc(...fromScene(t.x, t.y));
      const dist = v3.camera.position.distanceTo(t), span = dist * 2 * Math.tan(v3.camera.fov * Math.PI / 360);
      const z = Math.log2(40075016.686 * Math.cos(lat * Math.PI / 180) * map.getSize().y / span) - 8;
      map.setView([lat, lon], Math.max(map.getMinZoom(), Math.min(map.getMaxZoom() || 19, z)), { animate: false });
    } finally { link.busy = false; }
  }
  map.on("move", () => { if (link.on) syncFrom2d(); });
