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
      ["Show points on map…", () => tablePoints(it)],
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
  // a table's rows as points on the map: the longitude / latitude columns are found by name and value (lat, lon, lng,
  // longitude, Latitude (deg)…, x / y in degrees), or chosen in a dialog when they aren't
  async function tablePoints(it, q = "", cols = null) {
    status(`Loading points from ${it.name}…`, true);
    try {
      const p = new URLSearchParams({ path: it.path, q });
      if (cols) { p.set("lon", cols[0]); p.set("lat", cols[1]); }
      const fc = await api(`/api/tables/points?${p}`);
      const skipped = fc.total - fc.features.length;
      if (!fc.features.length) { status(""); toast(`No rows of ${it.name} have a valid longitude / latitude${cols ? "" : ": choose the columns"}`, true); return cols ? null : chooseXY(it); }
      const l = addVectorLayer({ type: "FeatureCollection", features: fc.features }, `${it.name.replace(/\.[^.]+$/, "")} · points`, { tableSource: it.path });
      status(`${fc.features.length.toLocaleString()} points added`);
      toast(`${fc.features.length.toLocaleString()} point${fc.features.length === 1 ? "" : "s"} from ${it.name} (${fc.columns[1]} / ${fc.columns[0]})` +
            (fc.sampled ? `: a sample of ${fc.total.toLocaleString()} rows` : skipped > 0 ? `; ${skipped} row${skipped === 1 ? "" : "s"} without a position skipped` : ""));
      return l;
    } catch (e) {
      status("");
      if (/No longitude \/ latitude columns/.test(e.message)) return chooseXY(it);
      toast(e.message, true);
    }
  }
  // choose the longitude / latitude columns of a table (numbers first, the likely ones preselected)
  async function chooseXY(it) {
    let d;
    try { d = await api(`/api/tables/rows?path=${encodeURIComponent(it.path)}&limit=3`); } catch (e) { return toast(e.message, true); }
    const num = d.columns.filter((c, i) => /int|float|number|double/.test(d.types[i] || ""));
    const order = [...num, ...d.columns.filter((c) => !num.includes(c))];
    const guess = (re) => order.find((c) => re.test(c)) || "";
    const opts = order.map((c) => `<option value="${esc(c)}">${esc(c)}${num.includes(c) ? "" : " (text)"}</option>`).join("");
    $("#xy-lon").innerHTML = opts; $("#xy-lat").innerHTML = opts;
    $("#xy-lon").value = d.lonlat?.[0] || guess(/lon|lng|long|^x$/i) || num[0] || order[0];
    $("#xy-lat").value = d.lonlat?.[1] || guess(/lat|^y$/i) || num[1] || order[1] || order[0];
    $("#xy-title").textContent = `Show ${it.name} on the map`;
    const sample = () => {
      const i = d.columns.indexOf($("#xy-lon").value), j = d.columns.indexOf($("#xy-lat").value);
      $("#xy-sample").textContent = d.rows.length ? `First row: longitude ${d.rows[0][i]}, latitude ${d.rows[0][j]}` : "";
    };
    $("#xy-lon").onchange = sample; $("#xy-lat").onchange = sample; sample();
    $("#xy-go").onclick = () => {
      if ($("#xy-lon").value === $("#xy-lat").value) return toast("Longitude and latitude must be different columns", true);
      $("#dlg-xy").close();
      tablePoints(it, "", [$("#xy-lon").value, $("#xy-lat").value]);
    };
    $("#dlg-xy").showModal();
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
