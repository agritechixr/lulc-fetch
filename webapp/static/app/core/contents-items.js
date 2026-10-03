  // Contents: tables and pictures (not map layers).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Contents: tables and plain pictures (not map layers)
  const dataItems = [];   // {id, kind: "table" | "picture", name, path, rows, cols, width, height}
  let selectedItem = null;
  const OPEN_IC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 13h18M8 17h8"/></svg>';
  $("#sect-2d .sect-ic").innerHTML = svg("raster");
  $("#sect-tab .sect-ic").innerHTML = svg("ml");
  $$(".sect-head").forEach((b) => {
    const sect = b.closest(".sect");
    sect.classList.toggle("collapsed", prefs.get("sect-" + b.dataset.sect, false));
    b.onclick = () => { sect.classList.toggle("collapsed"); prefs.set("sect-" + b.dataset.sect, sect.classList.contains("collapsed")); };
  });
  function updateSectionCounts() {
    const pics = dataItems.filter((d) => d.kind === "picture").length, tabs = dataItems.filter((d) => d.kind === "table").length;
    $("#count-2d").textContent = layers.length + pics;
    $("#count-tab").textContent = tabs;
    $("#contents-empty").classList.toggle("hidden", layers.length + pics > 0);
    $("#tables-empty").classList.toggle("hidden", tabs > 0);
  }
  function saveItems() {
    if (!inProject()) try { localStorage.setItem("lulc-data", JSON.stringify(dataItems)); } catch {}
    scheduleProjectSave();
  }
  function restoreItems(list) {
    if (list) dataItems.push(...list);
    else try { dataItems.push(...JSON.parse(localStorage.getItem("lulc-data") || "[]")); } catch {}
    renderItems();
  }
  function addItem(def, { open = false } = {}) {
    let it = dataItems.find((d) => d.path === def.path);
    if (it) Object.assign(it, def);
    else { it = { id: `${def.kind}-${Date.now().toString(36)}${(seq++).toString(36)}`, ...def }; dataItems.unshift(it); }
    selectedItem = it.id;
    saveItems(); renderItems();
    if (it.kind === "table" && it.rows == null) {
      api(`/api/tables/rows?path=${encodeURIComponent(it.path)}&limit=1`).then((r) => { it.rows = r.total; it.cols = r.columns.length; saveItems(); renderItems(); }).catch(() => {});
    }
    if (open) openItem(it);
    return it;
  }
  function removeItem(id) {
    const i = dataItems.findIndex((d) => d.id === id);
    if (i < 0) return;
    dataItems.splice(i, 1);
    closeTab("item:" + id);
    saveItems(); renderItems();
  }
  function itemRow(it) {
    const isT = it.kind === "table";
    const sub = isT ? (it.rows != null ? `${it.rows.toLocaleString()} rows × ${it.cols} cols` : "table") : `picture · ${it.width}×${it.height} · not on map`;
    return `<div class="item ${it.id === selectedItem ? "selected" : ""}" data-item="${esc(it.id)}" title="${esc(it.name)}\n${esc(it.path)}">
      <span class="lyr-ic ${isT ? "ic-tab" : "ic-pic"}">${svg(isT ? "ml" : "image")}</span>
      <span class="lyr-name">${esc(it.name)}<small>${esc(sub)}</small></span>
      <button class="lyr-zoom" data-open title="${isT ? "Open in the data viewer under the map" : "View the picture"}">${OPEN_IC}</button>
      <button class="lyr-more" title="Options">⋯</button></div>`;
  }
  function renderItems() {
    $("#table-list").innerHTML = dataItems.filter((d) => d.kind === "table").map(itemRow).join("");
    $("#picture-list").innerHTML = dataItems.filter((d) => d.kind === "picture").map(itemRow).join("");
    $$("#contents .item").forEach((el) => {
      const it = dataItems.find((d) => d.id === el.dataset.item);
      el.onclick = (e) => { if (!e.target.closest("button")) { selectedItem = it.id; $$("#contents .item").forEach((x) => x.classList.toggle("selected", x === el)); } };
      el.ondblclick = (e) => { if (!e.target.closest("button")) openItem(it); };
      el.oncontextmenu = (e) => { e.preventDefault(); itemMenu(it, e.clientX, e.clientY); };
      $("[data-open]", el).onclick = (e) => { e.stopPropagation(); openItem(it); };
      $(".lyr-more", el).onclick = (e) => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); itemMenu(it, r.right, r.bottom); };
    });
    updateSectionCounts();
  }
  function itemMenu(it, x, y) {
    const isT = it.kind === "table";
    showMenu(it.name, isT ? [
      ["Open in data viewer", () => openItem(it)],
      ["Column statistics", () => openItem(it, "stats")],
      ["Show points on map (lon / lat)", () => tablePoints(it)],
      ["Train a model with this table", () => { switchTool("ml"); openMlSub("train"); refreshTrainTables(it.path); }],
      "-",
      ["Save to folder…", () => saveItemToFolder(it)],
      ["Restore previous version", () => restoreTable(it)],
      ["Download", () => { location.href = `/api/tables/file?path=${encodeURIComponent(it.path)}`; }],
      "-",
      ["Remove from Contents", () => removeItem(it.id), "danger"],
    ] : [
      ["View picture", () => openItem(it)],
      ["Place on map (stretch over current view)", () => placePicture(it)],
      "-",
      ["Save to folder…", () => saveItemToFolder(it)],
      ["Download", () => { location.href = `/api/pictures/file?path=${encodeURIComponent(it.path)}`; }],
      "-",
      ["Remove from Contents", () => removeItem(it.id), "danger"],
    ], x, y);
  }
  function openItem(it, mode) {
    selectedItem = it.id;
    if (it.kind === "table") openTab({ key: "item:" + it.id, kind: "table", title: it.name, path: it.path, item: it, ...(mode ? { mode } : {}) });
    else openTab({ key: "item:" + it.id, kind: "picture", title: it.name, path: it.path, item: it });
  }
  async function tablePoints(it, q = "") {
    status(`Loading points from ${it.name}…`, true);
    try {
      const fc = await api(`/api/tables/points?path=${encodeURIComponent(it.path)}&q=${encodeURIComponent(q)}`);
      if (!fc.features.length) return toast("No rows with valid longitude / latitude", true);
      addVectorLayer({ type: "FeatureCollection", features: fc.features }, `${it.name.replace(/\.[^.]+$/, "")} · points`);
      status(`${fc.features.length.toLocaleString()} points added`);
      if (fc.sampled) toast(`Showing a sample of ${fc.features.length.toLocaleString()} of ${fc.total.toLocaleString()} rows`);
    } catch (e) { status(""); toast(e.message, true); }
  }
  async function placePicture(it) {
    const b = map.getBounds();
    if (!confirm(`Stretch "${it.name}" over the current map view? Zoom / pan the map first so the view matches the picture's area.`)) return;
    try {
      const r = await api("/api/pictures/georef", { method: "POST", json: { path: it.path, bounds: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()] } });
      await addRasterFromPath(r.path, { name: it.name.replace(/\.[^.]+$/, "") + " (placed)", zoom: false });
      toast("Placed on the map. It's now a GeoTIFF layer under 2D data.");
    } catch (e) { toast(e.message, true); }
  }
