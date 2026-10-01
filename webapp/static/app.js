/* LULC Fetch — desktop GIS-style web UI. Vanilla JS + Leaflet.
 *
 * Layout: menu bar (File · Tools · View · Help) / Contents (layers, left) / map / tool panel (right) / status bar.
 * Every dataset (downloads, local files, AOI, previews, index results) is a layer in `layers`, which can be
 * shown, reordered, styled, identified (click map) and exported (GeoTIFF / PNG / Shapefile / GeoJSON / KML).
 */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 1) => (n == null || isNaN(n) ? "–" : Number(n).toFixed(d));
  const fmtv = (v) => v == null || isNaN(v) ? "–" : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(3);

  const state = {
    config: null, aoi: null, results: null, sort: "cloud",
    activeScene: null, downloadScene: null, polling: null, catalog: null,
  };

  const prefs = (() => { // per-browser conveniences; everything works without them
    let p = {};
    try { p = JSON.parse(localStorage.getItem("lulc-prefs") || "{}"); } catch {}
    return {
      get: (k, d) => (k in p ? p[k] : d),
      set: (k, v) => { p[k] = v; try { localStorage.setItem("lulc-prefs", JSON.stringify(p)); } catch {} },
    };
  })();

  // ------------------------------------------------------------------ api / ui helpers
  async function api(path, opts = {}) {
    const init = { ...opts, headers: { ...(opts.headers || {}) } };
    if (opts.json !== undefined) {
      init.body = JSON.stringify(opts.json);
      init.headers["Content-Type"] = "application/json";
    }
    const r = await fetch(path, init);
    const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
    if (!r.ok) {
      let msg = body?.detail ?? body;
      if (Array.isArray(msg)) msg = msg.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
      throw new Error(typeof msg === "string" ? msg : r.statusText);
    }
    return body;
  }

  function toast(msg, err = false) {
    const t = document.createElement("div");
    t.className = "toast" + (err ? " err" : "");
    t.textContent = msg;
    $("#toasts").append(t);
    setTimeout(() => t.remove(), err ? 7000 : 3500);
  }

  async function busy(btn, label, fn) {
    const old = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>${label}`;
    try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = old; }
  }

  let msgTimer;
  function status(msg, sticky = false) {
    $("#sb-msg").textContent = msg;
    clearTimeout(msgTimer);
    if (!sticky) msgTimer = setTimeout(() => $("#sb-msg").textContent = "Ready", 5000);
  }

  // ---------------- progress + cancel bar (bottom of the tool panel), one run per tool
  class CancelledError extends Error { constructor() { super("Cancelled"); this.cancelled = true; } }
  const runs = {};  // tool id -> { title, progress (0–1 or null = unknown), message, started, cancel() }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function renderRunBar() {
    const r = runs[currentTool], bar = $("#run-bar");
    bar.classList.toggle("hidden", !r);
    if (!r) return;
    const known = r.progress != null;
    const pct = known ? Math.round(r.progress * 100) : null;
    const secs = (Date.now() - r.started) / 1000;
    let eta = "";
    if (known && r.progress > 0.05 && r.progress < 1) eta = ` · ~${fmtSecs(secs * (1 - r.progress) / r.progress)} left`;
    $("#rb-title").textContent = r.title;
    $("#rb-pct").textContent = known ? `${pct}%` : "";
    $("#rb-fill").classList.toggle("indeterminate", !known);
    $("#rb-fill").style.width = known ? `${Math.max(2, pct)}%` : "";
    $("#rb-msg").textContent = `${r.message || "Working…"} · ${fmtSecs(secs)}${eta}`;
    $("#rb-cancel").disabled = !!r.cancelling;
    $("#rb-cancel").textContent = r.cancelling ? "Cancelling…" : "Cancel";
  }
  const fmtSecs = (s) => s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
  $("#rb-cancel").onclick = () => { const r = runs[currentTool]; if (r && !r.cancelling) { r.cancelling = true; r.message = "Cancelling…"; renderRunBar(); r.cancel(); } };
  setInterval(() => { if (runs[currentTool]) renderRunBar(); }, 1000);  // keep the elapsed time ticking

  // Track a background job until it finishes. Resolves with the finished job; throws CancelledError / Error.
  async function trackJob(job, { tool = currentTool, title } = {}) {
    const run = { title: title || job.title, progress: 0, message: "Starting…", started: Date.now(),
                  cancel: () => api(`/api/jobs/${job.id}/cancel`, { method: "POST" }).catch(() => {}) };
    runs[tool] = run;
    renderRunBar();
    try {
      let j = job;
      while (j.status === "queued" || j.status === "running") {
        await sleep(700);
        j = await api(`/api/jobs/${job.id}`);
        run.progress = j.progress;
        if (!run.cancelling) run.message = (j.message || run.message).replace(/^\d\d:\d\d:\d\d\s+/, "");
        if (runs[tool] === run) renderRunBar();
      }
      if (j.status === "cancelled") throw new CancelledError();
      if (j.status === "error") throw new Error(j.error || "The job failed");
      return j;
    } finally {
      if (runs[tool] === run) { delete runs[tool]; renderRunBar(); }
      refreshJobs();
    }
  }
  // Track a single request (no server-side progress): indeterminate bar; Cancel aborts it.
  async function trackFetch(fn, { tool = currentTool, title, message } = {}) {
    const ctrl = new AbortController();
    const run = { title, progress: null, message: message || "Working…", started: Date.now(), cancel: () => ctrl.abort() };
    runs[tool] = run;
    renderRunBar();
    try {
      return await fn(ctrl.signal);
    } catch (e) {
      if (e.name === "AbortError" || ctrl.signal.aborted) throw new CancelledError();
      throw e;
    } finally {
      if (runs[tool] === run) { delete runs[tool]; renderRunBar(); }
    }
  }
  const notCancelled = (e) => { if (e?.cancelled) { status("Cancelled"); toast("Cancelled"); return false; } return true; };

  function download(url, name) {
    const a = document.createElement("a");
    a.href = url; a.download = name || "";
    document.body.append(a); a.click(); a.remove();
  }

  // ------------------------------------------------------------------ map
  const savedView = prefs.get("view", { center: [20.5, 78.9], zoom: 5 });
  const map = L.map("map", { zoomControl: true, zoomSnap: 0, zoomDelta: 0.5, wheelPxPerZoomLevel: 90 }).setView(savedView.center, savedView.zoom);
  map.on("moveend", () => { const c = map.getCenter(); prefs.set("view", { center: [+c.lat.toFixed(5), +c.lng.toFixed(5)], zoom: +map.getZoom().toFixed(2) }); });
  map.createPane("labels").style.zIndex = 650;
  map.getPane("labels").style.pointerEvents = "none";
  const BASEMAPS = {
    streets: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" }),
    imagery: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" }),
    topo: L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, attribution: "© OpenStreetMap contributors, SRTM · © OpenTopoMap (CC-BY-SA)" }),
    none: null,
  };
  const placeLabels = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, pane: "labels" });
  let basemap = null;
  function setBasemap(name) {
    if (!(name in BASEMAPS)) name = "streets";
    if (basemap) map.removeLayer(basemap);
    basemap = BASEMAPS[name];
    basemap?.addTo(map);
    map.getContainer().classList.remove("bm-streets", "bm-imagery", "bm-topo", "bm-none");
    map.getContainer().classList.add("bm-" + name);
    prefs.set("basemap", name);
  }
  function setLabels(on) { on ? placeLabels.addTo(map) : map.removeLayer(placeLabels); prefs.set("labels", on); }
  setBasemap(prefs.get("basemap", "streets"));
  setLabels(prefs.get("labels", false));
  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
  const geoLayer = L.featureGroup().addTo(map);  // transient hover previews for address results

  // ------------------------------------------------------------------ tools (Tools menu + tool panel)
  // To add a tool: add <section id="tab-<id>" class="tabpanel hidden"> to index.html and an entry here.
  const ICONS = {
    search: '<path d="M4 7l4-4 4 4-4 4z"/><path d="M12 15l4-4 4 4-4 4z"/><path d="M9.5 9.5l5 5"/><path d="M3 21c1.5-3 4-4.5 7-4.5"/>',
    analyze: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17.5l9 5 9-5" opacity=".5"/>',
    jobs: '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 17v3h16v-3"/>',
    export: '<path d="M12 15V3"/><path d="M7 8l5-5 5 5"/><path d="M4 14v6h16v-6"/>',
    pca: '<circle cx="7" cy="16" r="1.4"/><circle cx="11" cy="12" r="1.4"/><circle cx="15" cy="10" r="1.4"/><circle cx="9" cy="17" r="1.4"/><circle cx="17" cy="7" r="1.4"/><path d="M3 21L21 3"/><path d="M8 6l10 10" opacity=".5"/>',
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    raster: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    vector: '<path d="M4 18l5-12 7 4 4 8z"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>',
  };
  const svg = (name, w = 1.8) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
  const TOOLS = [
    { id: "search", title: "Find imagery", icon: "search", subtitle: "Search, preview and download Sentinel-2, composites and land-cover labels" },
    { id: "analyze", title: "Index analysis", icon: "analyze", subtitle: "NDVI, SAVI, EVI, NDWI and 21 more indices or your own formula" },
    { id: "pca", title: "PCA & dimensionality reduction", icon: "pca", subtitle: "PCA, Kernel PCA, NMF, ICA and more (scikit-learn) on any multiband image" },
    { id: "export", title: "Export data", icon: "export", subtitle: "Save any layer to your computer: GeoTIFF, PNG, Shapefile, GeoJSON, KML" },
    { id: "jobs", title: "Downloads & jobs", icon: "jobs", subtitle: "Background downloads, logs and output files" },
  ];
  let currentTool = "home";

  function buildToolsMenu() {
    $("#tools-menu").innerHTML = TOOLS.map((t) => `<button class="tool-item" data-tool="${t.id}">
        <span class="ic">${svg(t.icon)}</span><span><b>${esc(t.title)}</b><small>${esc(t.subtitle)}</small></span></button>`).join("") +
      "";
    $$("#tools-menu [data-tool]").forEach((b) => b.onclick = () => { switchTool(b.dataset.tool); toggleMenu(null); });
    $("#tool-cards").innerHTML = TOOLS.map((t) => `<button class="tool-card" data-tool="${t.id}">
        <span class="ic">${svg(t.icon)}</span><span><b>${esc(t.title)}</b><small>${esc(t.subtitle)}</small></span></button>`).join("");
    $$("#tool-cards [data-tool]").forEach((b) => b.onclick = () => switchTool(b.dataset.tool));
  }

  function switchTool(id) {
    const tool = TOOLS.find((t) => t.id === id) || { id: "home", title: "Start", subtitle: "Choose a tool" };
    currentTool = tool.id;
    $$(".tabpanel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + tool.id));
    $("#tool-title").textContent = tool.title;
    $("#tool-sub").textContent = tool.subtitle;
    $("#active-tool").innerHTML = tool.id === "home" ? "" : `Tool: <b>${esc(tool.title)}</b>`;
    $$("#tools-menu [data-tool]").forEach((b) => b.classList.toggle("on", b.dataset.tool === tool.id));
    document.title = tool.id === "home" ? "LULC Fetch" : `${tool.title} · LULC Fetch`;
    setPane("tools", true);
    renderRunBar();
    if (tool.id === "analyze") refreshAnalyzeInputs();
    if (tool.id === "jobs") refreshJobs();
    if (tool.id === "home") refreshHomeProducts();
    if (tool.id === "export") { refreshExportLayers(); renderExportForm(); }
    if (tool.id === "pca" && pcaState.schema) refreshPcaInputs();
    prefs.set("tool", tool.id);
  }

  // ------------------------------------------------------------------ menus & commands
  let openMenu = null;
  function toggleMenu(m) {
    openMenu?.classList.remove("open");
    openMenu = m;
    if (m) { m.classList.add("open"); syncMenuChecks(); }
  }
  $$("#menus .menu").forEach((m) => {
    const btn = $(".menu-btn", m);
    btn.onclick = (e) => { e.stopPropagation(); toggleMenu(m === openMenu ? null : m); };
    btn.onmouseenter = () => { if (openMenu && openMenu !== m) toggleMenu(m); };
  });
  function syncMenuChecks() {
    $("#mi-contents").classList.toggle("on", !document.body.classList.contains("no-contents"));
    $("#mi-tools").classList.toggle("on", !document.body.classList.contains("no-tools"));
    $("#mi-labels").classList.toggle("on", map.hasLayer(placeLabels));
    const bm = prefs.get("basemap", "streets"), th = prefs.get("theme", "auto");
    $$('[data-group="basemap"]').forEach((b) => b.classList.toggle("on", b.dataset.cmd === "basemap:" + bm));
    $$('[data-group="theme"]').forEach((b) => b.classList.toggle("on", b.dataset.cmd === "theme:" + th));
    const sel = selectedLayer();
    $$('[data-cmd="export-layer"], [data-cmd="remove-layer"], [data-cmd="layer-props"]').forEach((b) => b.disabled = !sel);
    $('[data-cmd="clear-layers"]').disabled = !layers.length;
  }

  function setPane(which, show) {
    const cls = which === "contents" ? "no-contents" : "no-tools";
    document.body.classList.toggle(cls, !show);
    prefs.set(which, show);
    setTimeout(() => map.invalidateSize(), 30);
  }

  function applyTheme(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    prefs.set("theme", t);
  }

  function runCmd(cmd) {
    const [name, arg] = cmd.split(":");
    switch (name) {
      case "add-data": $("#add-file").click(); break;
      case "add-workspace": openWorkspace(); break;
      case "open-safe": openSafeDialog(); break;
      case "export-layer": openExport(selectedLayer()); break;
      case "layer-props": openProps(selectedLayer()); break;
      case "remove-layer": { const l = selectedLayer(); if (l) removeLayer(l.id); break; }
      case "clear-layers": if (layers.length && confirm("Remove all layers from Contents? Files on disk are kept.")) [...layers].forEach((l) => removeLayer(l.id)); break;
      case "credentials": $("#btn-creds").click(); break;
      case "toggle-contents": setPane("contents", document.body.classList.contains("no-contents")); break;
      case "toggle-tools": setPane("tools", document.body.classList.contains("no-tools")); break;
      case "basemap": setBasemap(arg); break;
      case "toggle-labels": setLabels(!map.hasLayer(placeLabels)); break;
      case "zoom-all": zoomAll(); break;
      case "theme": applyTheme(arg); break;
      case "guide": showHelp("guide"); break;
      case "shortcuts": showHelp("shortcuts"); break;
      case "start": switchTool("home"); break;
    }
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-cmd]");
    if (b && !b.disabled) { runCmd(b.dataset.cmd); toggleMenu(null); return; }
    if (!e.target.closest(".menu")) toggleMenu(null);
    if (!e.target.closest("#ctx-menu")) hideCtx();
  });

  // ------------------------------------------------------------------ status bar: coordinates & scale
  let coordFmt = prefs.get("coords", "dd"), lastLatLng = null;
  const dms = (v, pos, neg) => {
    const a = Math.abs(v), d = Math.floor(a), mf = (a - d) * 60, m = Math.floor(mf), s = (mf - m) * 60;
    return `${d}°${String(m).padStart(2, "0")}′${s.toFixed(1).padStart(4, "0")}″${v >= 0 ? pos : neg}`;
  };
  function showCoords(ll) {
    lastLatLng = ll;
    if (!ll) { $("#sb-coords").textContent = "Lat –  Lon –"; return; }
    const lng = L.Util.wrapNum(ll.lng, [-180, 180], true);
    $("#sb-coords").textContent = coordFmt === "dd"
      ? `Lat ${ll.lat.toFixed(5)}  Lon ${lng.toFixed(5)}`
      : `${dms(ll.lat, "N", "S")}  ${dms(lng, "E", "W")}`;
  }
  map.on("mousemove", (e) => showCoords(e.latlng));
  map.getContainer().addEventListener("mouseleave", () => showCoords(null));
  $("#sb-coords").onclick = () => { coordFmt = coordFmt === "dd" ? "dms" : "dd"; prefs.set("coords", coordFmt); showCoords(lastLatLng); };

  const EARTH_CIRC = 40075016.686, DPI = 96, INCH = 0.0254;
  const metersPerPixel = (z, lat) => EARTH_CIRC * Math.cos(lat * Math.PI / 180) / Math.pow(2, z + 8);
  function updateScale() {
    const c = map.getCenter();
    const scale = metersPerPixel(map.getZoom(), c.lat) * DPI / INCH;
    if (document.activeElement !== $("#sb-scale")) $("#sb-scale").value = Math.round(scale).toLocaleString("en-US");
    $("#sb-zoom").textContent = `z ${map.getZoom().toFixed(1)}`;
  }
  function setScale(text) {
    const n = parseFloat(String(text).replace(/[^\d.]/g, ""));
    if (!n || n < 50) { updateScale(); return; }
    const lat = map.getCenter().lat;
    const z = Math.log2(EARTH_CIRC * Math.cos(lat * Math.PI / 180) * DPI / INCH / (n * 256));
    map.setZoom(Math.max(map.getMinZoom(), Math.min(map.getMaxZoom() || 19, z)));
    $("#sb-scale").blur();
  }
  map.on("zoomend moveend", updateScale);
  $("#sb-scale").addEventListener("change", (e) => setScale(e.target.value));
  $("#sb-scale").addEventListener("keydown", (e) => { if (e.key === "Enter") setScale(e.target.value); if (e.key === "Escape") { e.target.blur(); updateScale(); } });
  $("#sb-scale").addEventListener("focus", (e) => e.target.select());
  updateScale();
  $("#sb-jobs").onclick = () => switchTool("jobs");

  // ------------------------------------------------------------------ layers (Contents)
  const layers = [];   // index 0 = drawn on top
  let selectedId = null, seq = 0;
  const PALETTE = ["#2563eb", "#db2777", "#ea580c", "#0891b2", "#7c3aed", "#65a30d", "#dc2626", "#0d9488"];
  let colorIdx = 0;
  const nextColor = () => PALETTE[colorIdx++ % PALETTE.length];
  const selectedLayer = () => layers.find((l) => l.id === selectedId) || null;
  const getLayer = (id) => layers.find((l) => l.id === id) || null;

  function vecStyle(l) {
    return { color: l.color || "#2563eb", weight: l.weight ?? 2, opacity: l.opacity,
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
        style: () => vecStyle(l),
        pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, ...vecStyle(l), fillOpacity: 0.8 * l.opacity, bubblingMouseEvents: false }),
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
    if (l.type === "vector") l.leaflet?.setStyle(vecStyle(l));
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
      renderContents();
      saveLayers();
    }
  }

  function defaultRender(info) {
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
    if (r.rgb) return l.legend?.bands ? "RGB " + l.legend.bands.join("/") : "RGB";
    if (r.index) return r.index;
    if (r.formula) return "formula";
    if (r.band) return l.legend?.kind === "classes" ? "classes" : `band ${r.band}`;
    return "";
  }

  function legendHtml(l) {
    const g = l.legend;
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
    $("#contents-empty").classList.toggle("hidden", layers.length > 0);
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
    refreshAnalyzeInputs();
    if (pcaState.schema) refreshPcaInputs();
    refreshClipPickers();
    if (currentTool === "export") { refreshExportLayers(); if (!exportLayer()) renderExportForm(); }
  }

  function onLayerRemoved(l) {
    if (l.id === "aoi") { state.aoi = null; $("#aoi-summary").classList.add("hidden"); $("#aoi-warn").classList.add("hidden"); }
    if (an.layer?.id === l.id) resetAnalyze();
    if (an.resultId === l.id) { an.resultId = null; an.sel = null; $("#an-result").classList.add("hidden"); renderIndexButtons(); }
  }

  // persistence: layer list survives reloads (preview images are not kept)
  function saveLayers() {
    try {
      const keep = layers.filter((l) => l.type !== "image").map(({ leaflet, image, busy, error, legend, ...rest }) => rest);
      const text = JSON.stringify(keep);
      if (text.length < 4e6) localStorage.setItem("lulc-layers", text);
    } catch {}
  }
  function restoreLayers() {
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem("lulc-layers") || "[]"); } catch {}
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

  // ------------------------------------------------------------------ context menu
  function showCtx(l, x, y) {
    const isPoly = l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type));
    const items = [
      ["Zoom to layer", () => zoomTo(l)],
      ["Properties…", () => openProps(l)],
      l.type === "raster" && !l.derived ? ["Compute indices on this layer", () => analyzeLayer(l)] : null,
      isPoly && l.id !== "aoi" ? ["Use as area of interest", () => useAsAoi(l)] : null,
      "-",
      ["Export / save to computer…", () => openExport(l)],
      "-",
      ["Move to top", () => moveLayer(l.id, 0)],
      ["Move to bottom", () => moveLayer(l.id, layers.length)],
      "-",
      ["Remove", () => removeLayer(l.id), "danger"],
    ].filter(Boolean);
    const m = $("#ctx-menu");
    m.innerHTML = `<div class="ctx-title">${esc(l.name)}</div>` + items.map((it, i) => it === "-" ? "<hr>" : `<button data-i="${i}" class="${it[2] || ""}">${esc(it[0])}</button>`).join("");
    $$("button", m).forEach((b) => b.onclick = (e) => { e.stopPropagation(); hideCtx(); items[+b.dataset.i][1](); });
    m.classList.remove("hidden");
    const r = m.getBoundingClientRect();
    m.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
    m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
  }
  function hideCtx() { $("#ctx-menu").classList.add("hidden"); }

  // ------------------------------------------------------------------ add data
  async function addFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    const rasters = files.filter((f) => /\.tiff?$/i.test(f.name));
    const shpParts = files.filter((f) => /\.(shp|shx|dbf|prj|cpg)$/i.test(f.name));
    const others = files.filter((f) => !rasters.includes(f) && !shpParts.includes(f));
    status(`Adding ${files.length} file${files.length > 1 ? "s" : ""}…`, true);
    for (const f of rasters) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const r = await api("/api/rasters/upload", { method: "POST", body: fd });
        await addRasterFromPath(r.path, { name: f.name });
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    for (const group of [...(shpParts.length ? [shpParts] : []), ...others.map((f) => [f])]) {
      try {
        const fd = new FormData();
        group.forEach((f) => fd.append("files", f));
        const fc = await api("/api/aoi/upload", { method: "POST", body: fd });
        const name = (group.find((f) => /\.shp$/i.test(f.name)) || group[0]).name.replace(/\.[^.]+$/, "");
        addVectorLayer({ type: "FeatureCollection", features: fc.features }, name);
        if (fc.warning) toast(`${name}: ${fc.warning}`, true);
      } catch (e) { toast(`${group[0].name}: ${e.message}`, true); }
    }
    status(`Added ${files.length} file${files.length > 1 ? "s" : ""}`);
  }
  $("#btn-add-data").onclick = () => $("#add-file").click();
  $("#add-file").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
  $("#btn-add-ws").onclick = () => openWorkspace();

  // drop files anywhere on the window
  let dragDepth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { dragDepth++; $("#drop-overlay").classList.remove("hidden"); } });
  window.addEventListener("dragleave", (e) => { if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").classList.add("hidden"); } });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    $("#drop-overlay").classList.add("hidden");
    if (e.target.closest("#drop, #an-drop")) return;  // the AOI upload box handles its own drops
    addFiles(e.dataTransfer.files);
  });

  async function openWorkspace() {
    const list = await api("/api/rasters");
    const groups = {};
    list.forEach((r) => (groups[r.group] ||= []).push(r));
    $("#ws-list").innerHTML = list.length ? Object.entries(groups).map(([g, rs]) => `<div class="ws-group">${esc(g)}</div>` + rs.map((r) => {
      const inMap = layers.some((l) => l.path === r.path && !l.derived);
      return `<div class="ws-row"><span>${esc(r.name)}<small>${esc(r.path)} · ${r.size_mb < 1 ? "<1" : Math.round(r.size_mb)} MB</small></span>
        <button class="btn small ${inMap ? "" : "primary"}" data-add="${esc(r.path)}">${inMap ? "Add again" : "Add"}</button></div>`;
    }).join("")).join("") : '<p class="hint">No GeoTIFFs in the workspace yet.</p>';
    $$("#ws-list [data-add]").forEach((b) => b.onclick = () => busy(b, "Adding…", async () => {
      await addRasterFromPath(b.dataset.add);
      b.textContent = "Added ✓";
    }));
    $("#dlg-ws").showModal();
  }

  // ------------------------------------------------------------------ Copernicus .SAFE products
  // Sentinel-2 opens instantly (a VRT over the original JP2 bands); Sentinel-1 is calibrated and geocoded
  // in a background job whose GeoTIFF is added to Contents when it finishes.
  function safeCard(p, compact = false) {
    const s1 = p.kind === "S1_GRD";
    return `<div class="safe-card" data-path="${esc(p.path)}">
      <div><span class="safe-kind ${s1 ? "s1" : ""}">${s1 ? "SAR" : "OPTICAL"}</span><b>${esc(p.title)}</b>
        <small>${esc(p.date)} · ${esc(p.satellite)} · ${esc(p.detail)} · ${fmt(p.size_mb / 1000, 2)} GB${p.zipped ? " · zip" : ""}</small>
        ${compact ? "" : `<small class="mono">${esc(p.path)}</small>`}</div>
      ${s1 ? `<small>Converted to calibrated backscatter (σ⁰, dB) for ${esc(p.pols.join(" + "))}, geocoded to UTM. No terrain correction.</small>
        <div class="row">
          <label class="inline">Pixel size <select data-res><option value="20">20 m</option><option value="40" selected>40 m</option><option value="80">80 m</option></select></label>
          <label class="inline" title="${state.aoi ? "Only process your area of interest" : "Set an area of interest in Find imagery first"}"><input type="checkbox" data-clip ${state.aoi ? "" : "disabled"}> Clip to area of interest</label>
          <button class="btn small primary" data-open>Create backscatter layer</button>
        </div>`
      : `<small>All 12 bands at 10 m, read directly from the product (nothing is copied).</small>
        <div class="row"><button class="btn small primary" data-open>Open as layer</button></div>`}
    </div>`;
  }
  function wireSafeCards(root, products) {
    $$(".safe-card", root).forEach((card) => {
      const p = products.find((x) => x.path === card.dataset.path);
      $("[data-open]", card).onclick = (e) => busy(e.currentTarget, p.kind === "S1_GRD" ? "Starting…" : "Opening…", async () => {
        try {
          const body = { path: p.path };
          if (p.kind === "S1_GRD") {
            body.res = +$("[data-res]", card).value;
            if ($("[data-clip]", card).checked && state.aoi) body.aoi = state.aoi;
          }
          const r = await api("/api/products/open", { method: "POST", json: body });
          $("#dlg-safe").open && $("#dlg-safe").close();
          if (r.kind === "raster") {
            status(`Opening ${r.name}…`, true);
            await addRasterFromPath(r.path, { name: r.name });
            status(`${r.name} added to Contents`);
          } else {
            switchTool("jobs");
            addedJobs.add(r.job.id);
            prefs.set("addedJobs", [...addedJobs].slice(-200));
            trackJob(r.job, { tool: "jobs" }).then(async (done) => {
              for (const f of done.files.filter((x) => /\.tiff?$/i.test(x))) await addRasterFromPath(`downloads/${done.id}/${f}`, { name: done.title });
              toast(`Added “${done.title}” to Contents`);
            }).catch((e2) => { if (notCancelled(e2)) toast(e2.message, true); });
          }
        } catch (err) { toast(err.message, true); }
      });
    });
  }
  async function openSafeDialog() {
    const r = await api("/api/products");
    $("#safe-folder").textContent = r.folder;
    $("#safe-list").innerHTML = r.products.length ? r.products.map((p) => safeCard(p)).join("")
      : '<p class="hint">No .SAFE products found. Copy Sentinel-1 / Sentinel-2 products (folders or .zip) into the data folder.</p>';
    wireSafeCards($("#safe-list"), r.products);
    $("#dlg-safe").showModal();
  }
  async function refreshHomeProducts() {
    try {
      const r = await api("/api/products");
      $("#home-products").classList.toggle("hidden", !r.products.length);
      $("#home-products-list").innerHTML = r.products.map((p) => safeCard(p, true)).join("");
      wireSafeCards($("#home-products-list"), r.products);
    } catch {}
  }

  // ------------------------------------------------------------------ identify (click map on selected raster)
  let suppressClickUntil = 0;
  map.on("click", (e) => { if (!picking && !activeDraw && Date.now() > suppressClickUntil) identify(e.latlng); });
  async function identify(latlng) {
    const e = { latlng };
    const l = selectedLayer();
    if (!l || l.type !== "raster" || !l.visible) return;
    try {
      const r = await api("/api/analyze/pixel", { method: "POST", json: {
        path: l.path, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0,
        lat: e.latlng.lat, lon: e.latlng.lng, index: l.render?.index, formula: l.render?.formula } });
      if (!r.inside) return;
      let head = "";
      if ("value" in r) head = `<div>${esc(r.name === "custom" ? "Formula" : r.name)}</div><div class="big">${fmtv(r.value)}</div>`;
      else if (l.render?.band) {
        const v = r.raw[l.render.band - 1]?.value;
        const cls = l.legend?.classes?.find((c) => c.value === v);
        head = `<div>${esc(r.raw[l.render.band - 1]?.description || "Band " + l.render.band)}</div><div class="big">${cls ? esc(cls.name) : fmtv(v)}</div>`;
      }
      const refl = Object.entries(r.reflectance || {});
      const extra = r.raw.filter((b) => !Object.values(l.band_map || {}).includes(b.band));
      L.popup({ maxWidth: 280 }).setLatLng(e.latlng).setContent(`<div class="pxpop">
        <div class="small" style="color:#667085"><b>${esc(l.name)}</b></div>${head}
        <div class="small" style="color:#667085">${fmt(e.latlng.lat, 5)}, ${fmt(e.latlng.lng, 5)} · row ${r.row}, col ${r.col}</div>
        <table>${refl.map(([k, v]) => `<tr><td>${k}</td><td>${fmtv(v)}</td></tr>`).join("")}
        ${extra.slice(0, 20).map((b) => `<tr><td>${esc(b.description)}</td><td>${fmtv(b.value)}</td></tr>`).join("")}</table></div>`).openOn(map);
    } catch (err) { toast(err.message, true); }
  }

  // ------------------------------------------------------------------ export dialog
  const safeName = (s) => String(s).replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "layer";
  // Export lives in the "Export data" tool; right-click ▸ Export opens it with that layer selected.
  function openExport(l) {
    if (!l && !layers.length) return toast("Add a layer to Contents first", true);
    switchTool("export");
    refreshExportLayers(l?.id);
    renderExportForm();
  }
  function refreshExportLayers(selectId) {
    const sel = $("#lx-layer"), cur = selectId || sel.value || selectedId;
    sel.innerHTML = layers.length ? layers.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("")
      : `<option value="">No layers in Contents</option>`;
    if (cur && getLayer(cur)) sel.value = cur;
  }
  const exportLayer = () => getLayer($("#lx-layer").value);
  function renderExportForm() {
    const l = exportLayer();
    if (!l) { $("#lx-formats").innerHTML = ""; $("#lx-layer-info").textContent = "Choose a layer from Contents."; return; }
    const r = l.render || {};
    let formats;
    if (l.type === "vector") {
      formats = [["shp", "Shapefile (.zip)", "Polygons / lines / points with attributes, WGS 84. Opens in QGIS, ArcGIS."],
                 ["geojson", "GeoJSON", "Web-friendly vector format"],
                 ["kml", "KML", "Google Earth"]];
    } else if (l.type === "image") {
      formats = [["png", "PNG image", "The preview picture as shown on the map"]];
    } else {
      const tifDesc = r.index || r.formula ? "Index values as float32, full resolution, original projection"
        : r.rgb ? `All ${l.info?.count || ""} bands of the file (e.g. every component), full resolution`
        : r.composite ? "The 3 displayed bands as surface reflectance, full resolution"
        : `Band ${r.band} with its original values${l.legend?.kind === "classes" ? " and colour table" : ""}`;
      formats = [["tif", "GeoTIFF", tifDesc],
                 ["png", "PNG image", "As displayed (colours), full resolution up to 8192 px"],
                 ["pngw", "PNG + world file (.zip)", "Georeferenced PNG (.pgw + .prj) for QGIS / ArcGIS"],
                 ["shp", "Shapefile (.zip)", r.composite || r.rgb ? "Not available for a colour image. Display a single band first (Properties)." : "Polygons of value classes, with class, range and area attributes"]];
    }
    $("#lx-layer-info").textContent = `${l.type === "vector" ? "Vector" : l.type === "image" ? "Preview image" : "Raster"} · ${displayLabel(l)}${l.path ? " · " + l.path : ""}`;
    const prev = $('input[name="lxf"]:checked')?.value;
    $("#lx-formats").innerHTML = formats.map(([k, t, d]) => `<label class="opt ${k === "shp" && (r.composite || r.rgb) ? "disabled" : ""}">
      <input type="radio" name="lxf" value="${k}"><span><b>${t}</b><small>${esc(d)}</small></span></label>`).join("");
    const keep = formats.find(([k]) => k === prev && !(k === "shp" && (r.composite || r.rgb)));
    $(`input[name="lxf"][value="${keep ? prev : formats[0][0]}"]`).checked = true;
    $("#lx-name").value = safeName(l.name);
    $("#lx-error").classList.add("hidden");
    const classMap = l.legend?.kind === "classes";
    $("#lx-shp-note").textContent = classMap ? "Each map class becomes polygons and keeps its name." :
      "Pixel values are grouped into classes, then neighbouring pixels of the same class are merged into polygons.";
    $("#lx-shp-classes").classList.toggle("hidden", classMap);
    const syncShp = () => $("#lx-shp").classList.toggle("hidden", $('input[name="lxf"]:checked')?.value !== "shp");
    $$('input[name="lxf"]').forEach((i) => i.onchange = syncShp);
    syncShp();
    refreshClipPicker("lx-area");
    $("#lx-area").disabled = l.type === "image";
  }
  $("#lx-layer").onchange = renderExportForm;
  $("#lx-method").onchange = () => $("#lx-breaks-wrap").classList.toggle("hidden", $("#lx-method").value !== "custom");
  $("#lx-go").onclick = (e) => {
    const l = exportLayer(), fmtSel = $('input[name="lxf"]:checked')?.value, name = safeName($("#lx-name").value);
    const clip = l?.type === "image" ? null : getClip("lx-area", l);
    const err = (m) => { $("#lx-error").textContent = m; $("#lx-error").classList.remove("hidden"); };
    if (!l || !fmtSel) return;
    busy(e.currentTarget, "Exporting…", async () => {
      try {
        let r;
        if (l.type === "image") { download(l.url, name + ".png"); return; }
        if (l.type === "vector") {
          r = await api("/api/vector/export", { method: "POST", json: { geojson: l.geojson, format: fmtSel, name, clip } });
        } else {
          const body = { path: l.path, format: fmtSel, name, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0, ...l.render, clip };
          if (fmtSel === "shp") {
            Object.assign(body, { method: $("#lx-method").value, classes: +$("#lx-classes").value || 5, sieve: +$("#lx-sieve").value || 0 });
            if (body.method === "custom") body.breaks = $("#lx-breaks").value.split(/[,\s]+/).filter(Boolean).map(Number);
            if (body.method === "equal" && l.legend?.kind === "continuous") { body.vmin = l.legend.vmin; body.vmax = l.legend.vmax; }
          }
          const job = await api("/api/layers/export", { method: "POST", json: body });
          try {
            r = (await trackJob(job, { tool: "export", title: `Exporting ${name} (${fmtSel.toUpperCase()})` })).result;
          } catch (ex2) { if (!notCancelled(ex2)) return; throw ex2; }
        }
        download(r.url, r.name);
        toast(`Exported ${r.name}${r.features ? ` · ${r.features.toLocaleString()} features` : ""}${r.size_mb ? ` · ${fmt(r.size_mb)} MB` : ""}`);
        status(`Exported ${r.name}`);
      } catch (ex) { err(ex.message); }
    });
  };

  // ------------------------------------------------------------------ area picker (clip to an area of interest)
  // Every tool can limit its work to an area: the whole layer, the Find-imagery AOI, the current map view,
  // any polygon layer in Contents, or a rectangle / polygon drawn now (which is added to Contents too).
  const clipPickers = {
    "an-area": { what: "image is analysed", onChange: () => { if (an.sel && !an.sel.composite) compute(); } },
    "lx-area": { what: "layer is exported", own: true, onChange: () => {} },
    "pca-area": { what: "image is used", onChange: () => {} },
  };
  const polygonLayers = () => layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)));
  function refreshClipPicker(id) {
    const sel = $("#" + id), cfg = clipPickers[id];
    if (!sel) return;
    const cur = sel.value;
    const opts = [["none", "Whole " + (id === "lx-area" ? "layer" : "image")]];
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
    $(`#${id}-hint`).innerHTML = !g ? `The whole ${esc(cfg.what)}.`
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

  // ------------------------------------------------------------------ layer properties
  function openProps(l) {
    if (!l) return toast("Select a layer in Contents first", true);
    state.propsLayer = l;
    $("#lp-title").textContent = `Properties · ${l.name}`;
    $("#lp-name").value = l.name;
    $("#lp-opacity").value = Math.round(l.opacity * 100);
    $("#lp-raster").classList.toggle("hidden", l.type !== "raster");
    $("#lp-vector").classList.toggle("hidden", l.type !== "vector");
    const info = [];
    if (l.type === "raster") {
      const r = l.render || {}, bm = l.band_map || {}, c = state.catalog;
      const comps = Object.entries(c.composites).filter(([, v]) => v.bands.every((b) => b in bm));
      let opts = "";
      if (r.index || r.formula) opts += `<optgroup label="Index"><option value="keep">${esc(r.index || "Formula: " + r.formula)}</option></optgroup>`;
      const nb = l.info?.count || 0;
      if (nb >= 3) {
        const combos = [[1, 2, 3], [2, 3, 1], [1, 3, 2], [3, 2, 1]].filter((c) => c.every((b) => b <= nb));
        if (r.rgb && !combos.some((c) => c.join() === r.rgb.join())) combos.unshift(r.rgb);
        const nm = (b) => l.info.bands[b - 1]?.description || "Band " + b;
        opts += `<optgroup label="RGB of bands">${combos.map((c) => `<option value="r:${c.join(",")}">${c.map(nm).map(esc).join(" / ")}</option>`).join("")}</optgroup>`;
      }
      if (comps.length) opts += `<optgroup label="Band combination">${comps.map(([k, v]) => `<option value="c:${k}">${esc(v.title)}</option>`).join("")}</optgroup>`;
      opts += `<optgroup label="Single band">${(l.info?.bands || []).map((b) => `<option value="b:${b.index}">Band ${b.index}${b.description !== "Band " + b.index ? " · " + esc(b.description) : ""}</option>`).join("")}</optgroup>`;
      $("#lp-display").innerHTML = opts;
      $("#lp-display").value = r.index || r.formula ? "keep" : r.composite ? `c:${r.composite}` : r.rgb ? `r:${r.rgb.join(",")}` : `b:${r.band}`;
      $("#lp-cmap").innerHTML = `<option value="">Default</option>` + Object.keys(c.colormaps).map((k) => `<option ${r.cmap === k ? "selected" : ""}>${k}</option>`).join("");
      $("#lp-stretch").value = r.stretch || "fixed";
      $("#lp-vmin").value = l.legend?.vmin != null ? +l.legend.vmin.toFixed(4) : "";
      $("#lp-vmax").value = l.legend?.vmax != null ? +l.legend.vmax.toFixed(4) : "";
      syncPropsUi();
      if (l.info) info.push(["File", l.path], ["Size", `${l.info.width} × ${l.info.height} px · ${l.info.count} bands · ${/\.vrt$/i.test(l.path) ? "virtual (VRT over the original product)" : fmt(l.info.size_mb) + " MB"}`],
        ["CRS", l.info.crs], ["Pixel size", `${fmt(l.info.res[0], 2)} × ${fmt(l.info.res[1], 2)}`], ["Data type", l.info.dtype]);
    } else if (l.type === "vector") {
      $("#lp-color").value = l.color || "#2563eb";
      info.push(["Features", l.geojson.features.length], ["CRS", "EPSG:4326 (WGS 84)"]);
    } else info.push(["Type", "Preview image (PNG)"]);
    $("#lp-info").innerHTML = info.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
    $("#dlg-lprops").showModal();
  }
  function syncPropsUi() {
    const v = $("#lp-display").value;
    const rgbLike = v.startsWith("c:") || v.startsWith("r:");
    $("#lp-style").classList.toggle("hidden", rgbLike);
    $("#lp-range").classList.toggle("hidden", rgbLike || $("#lp-stretch").value !== "custom");
  }
  $("#lp-display").onchange = syncPropsUi;
  $("#lp-stretch").onchange = syncPropsUi;
  $("#lp-apply").onclick = async () => {
    const l = state.propsLayer;
    if (!l) return;
    l.name = $("#lp-name").value.trim() || l.name;
    setOpacity(l, $("#lp-opacity").value / 100);
    if (l.type === "vector") { l.color = $("#lp-color").value; l.leaflet?.setStyle(vecStyle(l)); }
    $("#dlg-lprops").close();
    if (l.type === "raster") {
      const v = $("#lp-display").value, style = { stretch: $("#lp-stretch").value, cmap: $("#lp-cmap").value || null };
      if (style.stretch === "custom") { style.vmin = parseFloat($("#lp-vmin").value); style.vmax = parseFloat($("#lp-vmax").value); }
      if (v.startsWith("c:")) l.render = { composite: v.slice(2) };
      else if (v.startsWith("r:")) l.render = { rgb: v.slice(2).split(",").map(Number) };
      else if (v.startsWith("b:")) l.render = { band: +v.slice(2), ...style };
      else l.render = { index: l.render.index, formula: l.render.formula, ...style };
      try { await renderRaster(l); } catch (e) { toast(e.message, true); }
      if (an.resultId === l.id && l.legend?.kind === "continuous") showResult(l.legend);
    }
    renderContents();
    saveLayers();
  };

  // ------------------------------------------------------------------ help
  function showHelp(which) {
    $("#help-title").textContent = which === "guide" ? "Quick guide" : "Keyboard shortcuts";
    $("#help-body").innerHTML = which === "guide" ? `<div class="help">
      <h4>Workspace</h4><p><b>Contents</b> (left) lists every layer. <b>Tools</b> (menu) opens a tool in the right panel. The <b>status bar</b> shows the cursor position and the map scale. Type a scale such as <code>25000</code> and press Enter to zoom to it.</p>
      <h4>Add data</h4><p>File ▸ Add data, the <b>+ Add data</b> button, or drag files onto the map: GeoTIFF, Shapefile (.zip, or .shp + .shx + .dbf + .prj together), GeoJSON, KML/KMZ. <b>Workspace</b> lists GeoTIFFs already downloaded or produced.</p>
      <h4>Layers</h4><p>Tick to show or hide. Drag to reorder. Double-click to zoom. Right-click (or ⋯) for Properties, Export, Use as area of interest, and Compute indices. Select a raster and click the map to read its pixel values.</p>
      <h4>Export</h4><p>Rasters: GeoTIFF (values), PNG (as displayed), PNG + world file, or Shapefile (value classes → polygons). Vectors: Shapefile, GeoJSON, KML.</p>
      <h4>Your own Sentinel products</h4><p>File ▸ <b>Open Sentinel product (.SAFE)</b> lists products in the <code>data</code> folder. Sentinel-2 opens instantly with all bands. Sentinel-1 GRD is converted to calibrated backscatter (VV, VH in dB) in the background. Then use Index analysis (NDVI… for optical, RVI / CPR for radar).</p>
      <h4>Tools</h4><ul><li><b>Find imagery</b>: area → dates → search → preview → download. Downloads are added as layers when they finish.</li>
      <li><b>Index analysis</b>: pick an input raster, click an index. Each result is a new layer.</li></ul></div>`
      : `<div class="help"><table>
      <tr><td><kbd>Ctrl/⌘ O</kbd></td><td>Add data from computer</td></tr>
      <tr><td><kbd>Ctrl/⌘ E</kbd></td><td>Export selected layer</td></tr>
      <tr><td><kbd>Delete</kbd></td><td>Remove selected layer</td></tr>
      <tr><td><kbd>Ctrl/⌘ 1</kbd></td><td>Show / hide Contents</td></tr>
      <tr><td><kbd>Ctrl/⌘ 2</kbd></td><td>Show / hide tool panel</td></tr>
      <tr><td><kbd>Esc</kbd></td><td>Close menus</td></tr></table></div>`;
    $("#dlg-help").showModal();
  }

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    if (e.key === "Escape") { toggleMenu(null); hideCtx(); }
    if (mod && k === "o") { e.preventDefault(); runCmd("add-data"); }
    else if (mod && k === "e") { e.preventDefault(); runCmd("export-layer"); }
    else if (mod && e.key === "1") { e.preventDefault(); runCmd("toggle-contents"); }
    else if (mod && e.key === "2") { e.preventDefault(); runCmd("toggle-tools"); }
    else if (!typing && !mod && (e.key === "Delete" || e.key === "Backspace") && selectedId && !$("dialog[open]")) { e.preventDefault(); runCmd("remove-layer"); }
  });

  // ------------------------------------------------------------------ Find imagery: area of interest
  const AOI_STYLE = { color: "#1f7a5a", weight: 2.5, fillOpacity: 0.08 };

  // ------------------------------------------------------------------ geometry helpers
  const R = 6378137;
  function ringArea(ring) { // spherical polygon area in m² (same as @mapbox/geojson-area)
    let a = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      const [x1, y1] = ring[i], [x2, y2] = ring[i + 1];
      a += ((x2 - x1) * Math.PI / 180) * (2 + Math.sin(y1 * Math.PI / 180) + Math.sin(y2 * Math.PI / 180));
    }
    return Math.abs(a * R * R / 2);
  }
  function geomArea(g) {
    const poly = (rings) => ringArea(rings[0]) - rings.slice(1).reduce((s, r) => s + ringArea(r), 0);
    if (g.type === "Polygon") return poly(g.coordinates);
    if (g.type === "MultiPolygon") return g.coordinates.reduce((s, p) => s + poly(p), 0);
    return 0;
  }
  function geomBounds(g) {
    const b = [Infinity, Infinity, -Infinity, -Infinity];
    const walk = (c) => typeof c[0] === "number"
      ? (b[0] = Math.min(b[0], c[0]), b[1] = Math.min(b[1], c[1]), b[2] = Math.max(b[2], c[0]), b[3] = Math.max(b[3], c[1]))
      : c.forEach(walk);
    walk(g.coordinates);
    return b;
  }
  const countVerts = (g) => JSON.stringify(g.coordinates).split("],[").length;
  function bboxPolygon(w, s, e, n) {
    return { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] };
  }
  function squareAround(lat, lon, km) {
    const dLat = km / 111.32, dLon = km / (111.32 * Math.max(Math.cos(lat * Math.PI / 180), 1e-6));
    return bboxPolygon(lon - dLon, lat - dLat, lon + dLon, lat + dLat);
  }

  function updateAoiSummary(geometry, label) {
    const km2 = geomArea(geometry) / 1e6;
    const [w, s, e, n] = geomBounds(geometry);
    $("#aoi-area").textContent = `${km2 < 10 ? fmt(km2, 2) : Math.round(km2).toLocaleString()} km²${label ? " · " + label : ""}`;
    $("#aoi-detail").textContent = `${fmt(s, 4)}, ${fmt(w, 4)} → ${fmt(n, 4)}, ${fmt(e, 4)} · ${countVerts(geometry)} vertices`;
    $("#aoi-summary").classList.remove("hidden");
    const warn = $("#aoi-warn");
    if (km2 > 5500) {
      warn.textContent = `This area is large (${Math.round(km2).toLocaleString()} km²). At 10 m one download is limited to ~6,000 km², so use 20–60 m pixels or split the area. Searching still works.`;
      warn.classList.remove("hidden");
    } else warn.classList.add("hidden");
  }

  // The AOI is a regular layer in Contents (id "aoi"); removing that layer clears it.
  function setAOI(geometry, label = "") {
    if (!geometry || !/Polygon/.test(geometry.type)) { toast("The area must be a polygon", true); return; }
    state.aoi = geometry;
    const km2 = geomArea(geometry) / 1e6;
    addLayer({ id: "aoi", type: "vector", name: `Area of interest${label ? " · " + label : ""}`, aoiLabel: label,
      geojson: { type: "FeatureCollection", features: [{ type: "Feature", geometry, properties: { name: "Area of interest", area_km2: +km2.toFixed(3) } }] },
      color: "#1f7a5a", weight: 2.5, fillOpacity: 0.08 }, { select: false });
    zoomTo(getLayer("aoi"));
    updateAoiSummary(geometry, label);
    clearResults();
  }
  function restoreAoi(d) {
    const g = d.geojson?.features?.[0]?.geometry;
    if (g) { state.aoi = g; updateAoiSummary(g, d.aoiLabel || ""); }
  }
  function useAsAoi(l) {
    const polys = l.geojson.features.map((f) => f.geometry).filter((g) => g && /Polygon/.test(g.type));
    if (!polys.length) return toast("This layer has no polygons", true);
    const geometry = polys.length === 1 ? polys[0]
      : { type: "MultiPolygon", coordinates: polys.flatMap((g) => g.type === "Polygon" ? [g.coordinates] : g.coordinates) };
    setAOI(geometry, l.name);
    switchTool("search");
  }
  $("#aoi-zoom").onclick = () => getLayer("aoi") && zoomTo(getLayer("aoi"));
  $("#aoi-clear").onclick = () => removeLayer("aoi");

  // AOI method tabs
  $$("#aoi-tabs button").forEach((b) => b.onclick = () => {
    $$("#aoi-tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $$(".aoi-pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== b.dataset.aoi));
  });

  // Draw
  const drawOpts = { shapeOptions: AOI_STYLE, showArea: false };
  let activeDraw = null;
  let drawDone = null;  // callback for the current drawing; null = it sets the Find-imagery AOI
  function startDraw(Kind, onDone = null) {
    activeDraw?.disable();
    drawDone = onDone;
    activeDraw = new Kind(map, onDone ? { shapeOptions: { color: "#dc2626", weight: 2, dashArray: "6 4", fillOpacity: 0.05 }, showArea: false } : drawOpts);
    activeDraw.enable();
    $("#map-hint").textContent = Kind === L.Draw.Rectangle ? "Drag on the map to draw a rectangle (Esc to cancel)" : "Click to add points, click the first point to finish (Esc to cancel)";
    $("#map-hint").classList.remove("hidden");
  }
  map.on(L.Draw.Event.DRAWSTOP, () => { $("#map-hint").classList.add("hidden"); setTimeout(() => { activeDraw = null; }, 0); });
  $("#draw-rect").onclick = () => startDraw(L.Draw.Rectangle);
  $("#draw-poly").onclick = () => startDraw(L.Draw.Polygon);
  map.on(L.Draw.Event.CREATED, (e) => {
    activeDraw = null;
    suppressClickUntil = Date.now() + 500;  // the mouse-up that finished the drawing is not an identify click
    const g = e.layer.toGeoJSON().geometry, done = drawDone;
    drawDone = null;
    if (done) done(g); else setAOI(g, "drawn");
  });

  // Coordinates
  let coordMode = "point";
  $$("#coord-mode button").forEach((b) => b.onclick = () => {
    coordMode = b.dataset.mode;
    $$("#coord-mode button").forEach((x) => x.classList.toggle("active", x === b));
    $("#coord-point").classList.toggle("hidden", coordMode !== "point");
    $("#coord-bbox").classList.toggle("hidden", coordMode !== "bbox");
  });
  let picking = false;
  $("#pick-point").onclick = () => {
    picking = !picking;
    $("#map-hint").textContent = "Click the map to set the point";
    $("#map-hint").classList.toggle("hidden", !picking);
    map.getContainer().style.cursor = picking ? "crosshair" : "";
  };
  map.on("click", (e) => {
    if (!picking) return;
    picking = false;
    $("#map-hint").classList.add("hidden");
    map.getContainer().style.cursor = "";
    $("#pt-lat").value = e.latlng.lat.toFixed(5);
    $("#pt-lon").value = L.Util.wrapNum(e.latlng.lng, [-180, 180], true).toFixed(5);
    if (coordMode !== "point") $$("#coord-mode button")[0].click();
  });
  $("#apply-coords").onclick = () => {
    if (coordMode === "point") {
      const lat = parseFloat($("#pt-lat").value), lon = parseFloat($("#pt-lon").value), km = parseFloat($("#pt-km").value);
      if ([lat, lon, km].some(isNaN)) return toast("Enter latitude, longitude and radius", true);
      if (Math.abs(lat) > 85 || Math.abs(lon) > 180) return toast("Latitude must be within ±85°, longitude within ±180°", true);
      setAOI(squareAround(lat, lon, km), `${km} km around point`);
    } else {
      const [w, s, e, n] = ["#bb-w", "#bb-s", "#bb-e", "#bb-n"].map((id) => parseFloat($(id).value));
      if ([w, s, e, n].some(isNaN)) return toast("Fill in all four bounding-box values", true);
      if (w >= e || s >= n) return toast("Min must be smaller than max (W < E, S < N)", true);
      setAOI(bboxPolygon(w, s, e, n), "bounding box");
    }
  };

  // Address
  async function geocode() {
    const q = $("#geo-q").value.trim();
    if (!q) return;
    const ul = $("#geo-results");
    await busy($("#geo-go"), "…", async () => {
      try {
        const res = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
        geoLayer.clearLayers();
        ul.innerHTML = res.length ? "" : `<li>No matches for “${esc(q)}”.</li>`;
        res.forEach((r, i) => {
          const li = document.createElement("li");
          li.innerHTML = `<div>${esc(r.name)}</div><div class="meta">${esc(r.category)} · ${esc(r.type)} · ${fmt(r.lat, 4)}, ${fmt(r.lon, 4)}</div>
            <div class="row tight" style="margin-top:6px">
              ${r.boundary ? '<button class="btn small primary" data-a="boundary">Use boundary</button>' : ""}
              <button class="btn small" data-a="point">Use point + radius</button>
              <button class="btn small" data-a="bbox">Use bounding box</button>
            </div>`;
          li.onmouseenter = () => {
            geoLayer.clearLayers();
            const g = r.boundary || bboxPolygon(...r.bbox);
            L.geoJSON(g, { style: { color: "#d97706", weight: 2, fillOpacity: 0.05, dashArray: "4 4" } }).addTo(geoLayer);
          };
          li.querySelector('[data-a="point"]').onclick = () => {
            const km = parseFloat($("#geo-km").value) || 5;
            geoLayer.clearLayers();
            setAOI(squareAround(r.lat, r.lon, km), `${km} km around ${r.name.split(",")[0]}`);
          };
          li.querySelector('[data-a="bbox"]').onclick = () => { geoLayer.clearLayers(); setAOI(bboxPolygon(...r.bbox), r.name.split(",")[0]); };
          li.querySelector('[data-a="boundary"]')?.addEventListener("click", () => { geoLayer.clearLayers(); setAOI(r.boundary, r.name.split(",")[0]); });
          ul.append(li);
          if (i === 0) li.onmouseenter();
        });
        if (res[0]) map.fitBounds([[res[0].bbox[1], res[0].bbox[0]], [res[0].bbox[3], res[0].bbox[2]]], { padding: [40, 40] });
      } catch (e) { toast(e.message, true); }
    });
  }
  $("#geo-go").onclick = geocode;
  $("#geo-q").addEventListener("keydown", (e) => e.key === "Enter" && geocode());

  // Upload
  async function upload(files) {
    if (!files.length) return;
    const fd = new FormData();
    [...files].forEach((f) => fd.append("files", f));
    const info = $("#upload-info");
    info.innerHTML = `<span class="spinner"></span>Reading ${files.length} file(s)…`;
    try {
      const fc = await api("/api/aoi/upload", { method: "POST", body: fd });
      const n = fc.features.length;
      info.innerHTML = `Loaded <b>${n}</b> feature${n === 1 ? "" : "s"} from ${esc([...files].map((f) => f.name).join(", "))}.` +
        (n > 1 ? " They are merged into one area." : "") +
        (fc.warning ? `<div class="warn">${esc(fc.warning)}</div>` : "");
      setAOI(fc.aoi, [...files][0].name);
    } catch (e) { info.textContent = ""; toast(e.message, true); }
  }
  const drop = $("#drop");
  $("#file").onchange = (e) => upload(e.target.files);
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => upload(e.dataTransfer.files));

  // ------------------------------------------------------------------ filters
  const iso = (d) => d.toISOString().slice(0, 10);
  function setRange(days) {
    const end = new Date(), start = new Date(Date.now() - days * 864e5);
    $("#start").value = iso(start);
    $("#end").value = iso(end);
  }
  $$(".presets .chip").forEach((c) => c.onclick = () => setRange(+c.dataset.days));
  $("#cloud").oninput = () => $("#cloud-val").textContent = $("#cloud").value + "%";

  // ------------------------------------------------------------------ search & results
  function clearResults() {
    state.results = null;
    removeLayer("footprints");
    removePreviews();
    $("#results").innerHTML = "";
    $("#results-info").textContent = "Choose an area and dates, then search.";
  }

  $("#btn-search").onclick = () => {
    if (!state.aoi) return toast("Set an area of interest first", true);
    const start = $("#start").value, end = $("#end").value;
    if (!start || !end || start > end) return toast("Choose a valid date range", true);
    busy($("#btn-search"), "Searching…", async () => {
      try {
        const res = await trackFetch((signal) => api("/api/search", { method: "POST", signal, json: {
          source: $("#source").value, aoi: state.aoi, start, end, max_cloud: +$("#cloud").value,
        } }), { tool: "search", title: "Searching the catalog", message: `${$("#mission").selectedOptions[0]?.text || ""}, ${start} → ${end}` });
        state.results = res;
        renderResults();
      } catch (e) { if (notCancelled(e)) toast(e.message, true); }
    });
  };

  $$("#sort button").forEach((b) => b.onclick = () => {
    state.sort = b.dataset.sort;
    $$("#sort button").forEach((x) => x.classList.toggle("active", x === b));
    renderResults();
  });

  const cloudClass = (c) => (c < 10 ? "c0" : c < 40 ? "c1" : "c2");
  const weekday = (d) => new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

  function highlightScene(date) {
    const fp = getLayer("footprints");
    fp?.leaflet?.eachLayer((x) => x.setStyle(date && x.feature.properties.date === date
      ? { color: "#4f46e5", weight: 3, opacity: 1, fillOpacity: 0.08 } : vecStyle(fp)));
  }

  function renderResults() {
    const res = state.results;
    const box = $("#results");
    box.innerHTML = "";
    if (!res) return;
    const scenes = [...res.scenes].sort({
      cloud: (a, b) => a.cloud - b.cloud || b.date.localeCompare(a.date),
      date: (a, b) => b.date.localeCompare(a.date),
      coverage: (a, b) => b.coverage - a.coverage || a.cloud - b.cloud,
    }[state.sort]);
    $("#results-info").textContent = scenes.length
      ? `${scenes.length} acquisition date${scenes.length === 1 ? "" : "s"} · ${res.count} tile${res.count === 1 ? "" : "s"} from ${$("#source").selectedOptions[0].text.split(" —")[0]}`
      : "Nothing found. Try a longer date range or a higher cloud limit.";
    const feats = scenes.flatMap((sc) => sc.items.map((i) => ({ type: "Feature", geometry: i.footprint,
      properties: { date: sc.date, tile: i.tile, cloud_pct: i.cloud != null ? +(+i.cloud).toFixed(2) : null, platform: i.platform, id: i.id } })));
    if (feats.length) {
      addLayer({ id: "footprints", type: "vector", name: `Scene footprints · ${res.count} tiles`,
        geojson: { type: "FeatureCollection", features: feats }, color: "#6366f1", weight: 1, fillOpacity: 0 }, { select: false, below: "aoi" });
    } else removeLayer("footprints");

    scenes.forEach((sc) => {
      const first = sc.items[0];
      const el = document.createElement("div");
      el.className = "scene";
      el.innerHTML = `
        ${first.thumbnail ? `<img class="thumb" loading="lazy" src="${esc(first.thumbnail)}" alt="Thumbnail ${esc(sc.date)}" title="Open full thumbnail">` : `<div class="thumb empty">no preview</div>`}
        <div>
          <h4><span>${esc(weekday(sc.date))}</span></h4>
          <div class="sub">${esc([...new Set(sc.items.map((i) => i.platform).filter(Boolean))].join(", "))} · tile${sc.tiles.length > 1 ? "s" : ""} ${esc(sc.tiles.join(", "))}</div>
          <div class="pills">
            <span class="pill ${cloudClass(sc.cloud)}">☁ ${fmt(sc.cloud)}% tile cloud</span>
            <span class="pill">${fmt(sc.coverage * 100, 0)}% of area covered</span>
          </div>
          <div class="pvstat hidden"></div>
          <div class="actions">
            <button class="btn small" data-a="preview">Preview area</button>
            <button class="btn small" data-a="meta">Metadata</button>
            <button class="btn small primary" data-a="dl">Download</button>
          </div>
        </div>`;
      el.onmouseenter = () => highlightScene(sc.date);
      el.onmouseleave = () => highlightScene(state.activeScene?.date);
      el.querySelector(".thumb")?.addEventListener("click", () => first.thumbnail && window.open(first.thumbnail, "_blank", "noopener"));
      el.querySelector('[data-a="preview"]').onclick = (e) => previewScene(sc, el, e.currentTarget);
      el.querySelector('[data-a="meta"]').onclick = () => showMetadata(sc);
      el.querySelector('[data-a="dl"]').onclick = () => openDownload(sc);
      sc._el = el;
      box.append(el);
    });
  }

  // ------------------------------------------------------------------ preview (added as image layers)
  function removePreviews() {
    layers.filter((l) => /^(preview|clouds)-/.test(l.id)).forEach((l) => removeLayer(l.id, { silent: true }));
    renderContents();
    state.activeScene?._el?.classList.remove("active");
    state.activeScene = null;
  }

  async function previewScene(sc, el, btn) {
    await busy(btn, "Loading…", async () => {
      try {
        const res = await trackFetch((signal) => api("/api/preview", { method: "POST", signal, json: {
          source: state.results.source, item_ids: sc.items.map((i) => i.id), aoi: state.aoi,
        } }), { tool: "search", title: `Preview ${sc.date}`, message: "Reading imagery and cloud mask for your area" });
        removePreviews();
        state.activeScene = sc;
        el.classList.add("active");
        addLayer({ id: `preview-${sc.date}`, type: "image", name: `Preview ${sc.date} · true colour`, url: res.image, bounds: res.bounds },
          { select: false, below: "footprints" });
        addLayer({ id: `clouds-${sc.date}`, type: "image", name: `Cloud / shadow mask ${sc.date}`, url: res.clouds, bounds: res.bounds },
          { select: false, below: "footprints" });
        map.fitBounds(res.bounds, { padding: [30, 30] });
        const s = res.stats;
        const pv = el.querySelector(".pvstat");
        pv.textContent = `Inside your area: ${fmt(s.clear_pct)}% clear · ${fmt(s.cloud_pct)}% cloud · ${fmt(s.shadow_pct)}% shadow · ${res.resolution_m} m preview`;
        pv.classList.remove("hidden");
        status(`Preview ${sc.date}: ${fmt(s.clear_pct)}% clear inside the area`);
      } catch (e) { if (notCancelled(e)) toast(e.message, true); }
    });
  }

  // ------------------------------------------------------------------ metadata
  const KEY_PROPS = [
    ["datetime", "Acquired (UTC)"], ["platform", "Satellite"], ["constellation", "Constellation"], ["instruments", "Instrument"],
    ["eo:cloud_cover", "Cloud cover %"], ["s2:mgrs_tile", "MGRS tile"], ["grid:code", "Grid"], ["proj:epsg", "EPSG"], ["proj:code", "Projection"],
    ["sat:relative_orbit", "Relative orbit"], ["sat:orbit_state", "Orbit direction"], ["view:sun_elevation", "Sun elevation °"],
    ["view:sun_azimuth", "Sun azimuth °"], ["s2:processing_baseline", "Processing baseline"], ["processing:version", "Processing baseline"],
    ["s2:product_uri", "Product"], ["s2:vegetation_percentage", "Vegetation %"], ["s2:water_percentage", "Water %"],
    ["s2:not_vegetated_percentage", "Not vegetated %"], ["s2:cloud_shadow_percentage", "Cloud shadow %"],
    ["s2:snow_ice_percentage", "Snow / ice %"], ["s2:nodata_pixel_percentage", "No-data %"], ["created", "Catalogued"],
  ];
  const showVal = (v) => typeof v === "object" ? esc(JSON.stringify(v)) : esc(v);

  function showMetadata(sc) {
    const dlg = $("#dlg-meta");
    $("#meta-title").textContent = `Metadata · ${sc.date} · ${sc.tiles.join(", ")}`;
    const tabs = $("#meta-items");
    tabs.innerHTML = sc.items.length > 1 ? sc.items.map((it, i) => `<button class="${i ? "" : "active"}" data-i="${i}">${esc(it.tile)}</button>`).join("") : "";
    const render = (it) => {
      const p = it.properties;
      const keyRows = KEY_PROPS.filter(([k]) => p[k] != null).map(([k, label]) => `<tr><td>${esc(label)}</td><td>${showVal(p[k])}</td></tr>`).join("");
      const allRows = Object.keys(p).sort().map((k) => `<tr><td>${esc(k)}</td><td>${showVal(p[k])}</td></tr>`).join("");
      const assets = it.assets.map((a) => `<tr><td>${esc(a.key)}</td><td>${esc(a.title || "")}${a.gsd ? ` · ${a.gsd} m` : ""}<br><small class="mono">${esc((a.type || "").split(";")[0])}</small></td></tr>`).join("");
      $("#meta-body").innerHTML = `
        <div class="meta-top">
          ${it.thumbnail ? `<img src="${esc(it.thumbnail)}" alt="Tile thumbnail">` : "<div></div>"}
          <div>
            <div class="mono small" style="word-break:break-all">${esc(it.id)}</div>
            <table class="kv" style="margin-top:8px">
              <tr><td>Area covered</td><td>${fmt(it.coverage * 100, 1)}%</td></tr>${keyRows}
            </table>
            <div class="row">
              ${it.self_href ? `<a class="btn small" href="${esc(it.self_href)}" target="_blank" rel="noopener">STAC item JSON ↗</a>` : ""}
              <button class="btn small" id="copy-json">Copy all metadata</button>
            </div>
          </div>
        </div>
        <div class="meta-sec">Assets (${it.assets.length})</div><table class="kv">${assets}</table>
        <details><summary class="meta-sec">All properties (${Object.keys(p).length})</summary><table class="kv">${allRows}</table></details>
        <details><summary class="meta-sec">Raw JSON</summary><pre class="json">${esc(JSON.stringify({ id: it.id, geometry: it.footprint, properties: p }, null, 2))}</pre></details>`;
      $("#copy-json").onclick = () => navigator.clipboard.writeText(JSON.stringify({ id: it.id, geometry: it.footprint, properties: p, assets: it.assets }, null, 2)).then(() => toast("Copied"));
    };
    $$("button", tabs).forEach((b) => b.onclick = () => {
      $$("button", tabs).forEach((x) => x.classList.toggle("active", x === b));
      render(sc.items[+b.dataset.i]);
    });
    render(sc.items[0]);
    dlg.showModal();
  }

  // ------------------------------------------------------------------ download dialog
  const mission = () => state.config.missions[$("#mission").value] || state.config.missions["sentinel-2"];
  function bandBoxes(selected) {
    $("#bands").innerHTML = mission().bands.map((b) =>
      `<label><input type="checkbox" value="${b}" ${selected.includes(b) ? "checked" : ""}>${b}</label>`).join("");
    $$("#bands input").forEach((i) => i.onchange = updateEstimate);
    updateEstimate();
  }
  $("#bands-default").onclick = () => bandBoxes(mission().default_bands);
  $("#bands-all").onclick = () => bandBoxes(mission().bands);
  $("#bands-rgb").onclick = () => bandBoxes(["B02", "B03", "B04", "B08"]);

  const dlKind = () => $('input[name="kind"]:checked')?.value;

  function updateDialog() {
    const k = dlKind();
    $("#dl-s2").classList.toggle("hidden", !["scene", "composite"].includes(k));
    $("#dl-mask-wrap").classList.toggle("hidden", k !== "scene");
    $("#dl-comp-opts").classList.toggle("hidden", k !== "composite");
    $("#dl-labels").classList.toggle("hidden", k !== "labels");
    $("#dl-res-wrap").classList.toggle("hidden", k === "product");
    $("#dl-product-wrap").classList.toggle("hidden", k !== "product");
    $("#dl-error").classList.add("hidden");
    updateEstimate();
  }
  $$('input[name="kind"]').forEach((r) => r.onchange = updateDialog);
  $("#dl-res").onchange = updateEstimate;
  $("#dl-indices").onchange = updateEstimate;

  function updateEstimate() {
    if (!state.aoi) return;
    const res = +$("#dl-res").value, km2 = geomArea(state.aoi) / 1e6;
    const [w, s, e, n] = geomBounds(state.aoi);
    const midLat = (s + n) / 2;
    const wpx = Math.ceil(((e - w) * 111320 * Math.cos(midLat * Math.PI / 180)) / res);
    const hpx = Math.ceil(((n - s) * 110574) / res);
    const k = dlKind();
    let nb = 1;
    if (k === "scene" || k === "composite") {
      nb = $$("#bands input:checked").length + ($("#dl-indices").checked ? state.config.indices.length : 0) + (k === "composite" ? 1 : 0);
    }
    const mb = (wpx * hpx * nb * (k === "labels" ? 1 : 4)) / 1e6 * 0.6;
    const tooBig = wpx * hpx > 60e6;
    $("#dl-estimate").innerHTML = `≈ ${wpx.toLocaleString()} × ${hpx.toLocaleString()} px · ${nb} band${nb === 1 ? "" : "s"} · ~${mb < 1 ? "<1" : Math.round(mb)} MB` +
      (tooBig ? ` <span style="color:var(--err)">— too large; choose a coarser pixel size</span>` : "");
  }

  function openDownload(scene = null) {
    if (!state.aoi) return toast("Set an area of interest first", true);
    state.downloadScene = scene;
    const sceneOpt = $('.opt[data-kind="scene"]'), prodOpt = $('.opt[data-kind="product"]');
    sceneOpt.classList.toggle("disabled", !scene);
    prodOpt.classList.toggle("disabled", !scene);
    $("#dl-scene-desc").textContent = scene
      ? `Bands for ${scene.date} (${scene.tiles.join(", ")}), clipped to your area`
      : "Pick a date from the search results first";
    $(`input[name="kind"][value="${scene ? "scene" : "composite"}"]`).checked = true;
    const landsat = $("#mission").value === "landsat";
    $("#dl-product-title").textContent = landsat ? "Original product bundle (EarthExplorer)" : "Original full product (.SAFE)";
    $("#dl-product-desc").textContent = landsat ? "Full Landsat Level-2 scene bundle (.tar, ~1 GB) from USGS EarthExplorer (needs credentials)"
      : "Entire ~110 km tile, ~0.5–1.2 GB, from Copernicus (needs account)";
    if (scene) {
      $("#dl-product-list").innerHTML = scene.items.map((i) => esc(i.product_name)).join("<br>") +
        `<p class="hint">Downloaded from ${landsat ? "USGS EarthExplorer" : "Copernicus Data Space"} with your saved credentials.</p>`;
    }
    if ($("#bands").dataset.mission !== $("#mission").value) {
      bandBoxes(mission().default_bands);
      $("#bands").dataset.mission = $("#mission").value;
      $("#dl-res").value = String(mission().res);
    }
    updateDialog();
    $("#dlg-dl").showModal();
  }
  $("#btn-download-range").onclick = () => openDownload(null);

  $("#dl-go").onclick = () => {
    const k = dlKind(), sc = state.downloadScene;
    const body = {
      kind: k, source: state.results?.source || $("#source").value, aoi: state.aoi,
      start: $("#start").value, end: $("#end").value, max_cloud: +$("#cloud").value,
      res: +$("#dl-res").value, bands: $$("#bands input:checked").map((i) => i.value),
      indices: $("#dl-indices").checked, mask_clouds: $("#dl-mask").checked,
      stat: $("#dl-stat").value, max_scenes: +$("#dl-maxscenes").value,
      product: $("#dl-product").value, year: +$("#dl-year").value,
    };
    if (k === "scene") {
      body.date = sc.date;
      body.start = body.end = sc.date;
      body.max_cloud = 100;
    }
    if (k === "product") {
      body.product_names = sc.items.map((i) => i.product_name);
      body.entity_ids = sc.items.map((i) => i.properties["landsat:scene_id"] || "");
    }
    if (["scene", "composite"].includes(k) && !body.bands.length) return showDlError("Select at least one band");
    busy($("#dl-go"), "Starting…", async () => {
      let job;
      try {
        job = await api("/api/jobs", { method: "POST", json: body });
      } catch (e) { return showDlError(e.message); }
      $("#dlg-dl").close();
      addedJobs.add(job.id);  // added below, as soon as it finishes
      prefs.set("addedJobs", [...addedJobs].slice(-200));
      try {
        const done = await trackJob(job, { tool: "search" });
        const tifs = done.files.filter((f) => /\.tiff?$/i.test(f));
        for (const f of tifs) await addRasterFromPath(`downloads/${done.id}/${f}`, { name: done.title });
        toast(tifs.length ? `Added “${done.title}” to Contents` : `${done.title} finished. Files are in Downloads & jobs.`);
      } catch (e) { if (notCancelled(e)) toast(e.message, true); }
    });
  };
  function showDlError(msg) { const el = $("#dl-error"); el.textContent = msg; el.classList.remove("hidden"); }

  // ------------------------------------------------------------------ downloads & jobs
  const openLogs = new Set();
  const addedJobs = new Set(prefs.get("addedJobs", []));
  let jobsSeen = false;
  async function refreshJobs() {
    let list;
    try { list = await api("/api/jobs"); } catch { return; }
    const running = list.filter((j) => j.status === "running" || j.status === "queued").length;
    $("#sb-jobs").classList.toggle("hidden", !running);
    const live = list.filter((j) => j.status === "running");
    const avg = live.length ? Math.round(100 * live.reduce((a, j) => a + j.progress, 0) / live.length) : 0;
    $("#sb-jobs").innerHTML = running ? `<span class="spinner"></span>${running} job${running > 1 ? "s" : ""} running · ${avg}%` : "";
    // Downloads that finish while the page is open go straight into Contents. Older ones (e.g. from a
    // previous session) are not re-added: they are listed under Workspace. PCA jobs add their own layer.
    if (!jobsSeen) {
      list.filter((j) => j.status === "done").forEach((j) => addedJobs.add(j.id));
      jobsSeen = true;
    }
    for (const j of list) {
      if (j.status !== "done" || addedJobs.has(j.id) || j.kind === "pca" || j.kind === "export") continue;
      addedJobs.add(j.id);
      prefs.set("addedJobs", [...addedJobs].slice(-200));
      j.files.filter((f) => /\.tiff?$/i.test(f)).forEach((f) =>
        addRasterFromPath(`downloads/${j.id}/${f}`, { name: j.title }).then(() => toast(`Added “${j.title}” to Contents`)));
    }
    list = list.filter((j) => j.kind !== "export");  // quick layer exports are not listed here
    $("#jobs-empty").classList.toggle("hidden", list.length > 0);
    $("#jobs").innerHTML = list.map((j) => {
      const elapsed = Math.round(((j.finished || Date.now() / 1000) - (j.started || j.created)));
      const files = j.files.map((f) => `<a href="/api/jobs/${j.id}/files/${encodeURIComponent(f)}" download><span>⬇ ${esc(f)}</span>${/\.tiff?$/i.test(f) ? `<button class="btn small" data-addmap="downloads/${j.id}/${esc(f)}" data-name="${esc(j.title)}">Add to map</button>` : ""}</a>`).join("");
      const png = j.files.find((f) => f.endsWith(".png"));
      const dist = j.result?.distribution ? `<div class="dist">${j.result.distribution.slice(0, 11).map((d) =>
        `<div><i style="background:${esc(d.color)}"></i><span>${esc(d.name)}</span><b>${fmt(d.pct)}%</b></div>`).join("")}</div>` : "";
      const facts = [j.params.grid, j.result?.date && `date ${j.result.date}`, j.result?.dates && `${j.result.dates.length} dates`,
        j.result?.valid_pct != null && `${fmt(j.result.valid_pct)}% valid pixels`].filter(Boolean).join(" · ");
      const showLogs = j.status === "error" || openLogs.has(j.id);
      const live = j.status === "running" || j.status === "queued";
      return `<div class="job">
        <h4><span>${esc(j.title)}</span><span class="status ${j.status}">${j.status === "running" ? '<span class="spinner"></span>' : ""}${j.status}</span></h4>
        <div class="sub">${esc(j.params.source || "")} · ${elapsed}s${facts ? " · " + esc(facts) : ""}</div>
        ${live ? `<div class="rb-track"><div class="rb-fill" style="width:${Math.max(2, Math.round(j.progress * 100))}%"></div></div>
          <div class="sub">${Math.round(j.progress * 100)}% · ${esc((j.message || "Starting…").replace(/^\d\d:\d\d:\d\d\s+/, ""))}</div>` : ""}
        ${j.error ? `<div class="warn">${esc(j.error)}</div>` : ""}
        ${png ? `<img class="result" src="/api/jobs/${j.id}/files/${encodeURIComponent(png)}?t=${j.finished || ""}" alt="Result preview">` : ""}
        ${dist}
        ${files ? `<div class="files">${files}</div>` : ""}
        <details data-job="${j.id}" ${showLogs ? "open" : ""}><summary>Log (${j.logs.length} lines)</summary><pre>${esc(j.logs.join("\n"))}</pre></details>
        <div class="row">${live ? `<button class="btn small danger" data-cancel="${j.id}">Cancel</button>` : `<button class="btn small danger" data-del="${j.id}">Delete files</button>`}</div>
      </div>`;
    }).join("");
    $$("#jobs details").forEach((d) => d.addEventListener("toggle", () => d.open ? openLogs.add(d.dataset.job) : openLogs.delete(d.dataset.job)));
    $$("#jobs pre").forEach((p) => p.scrollTop = p.scrollHeight);
    $$("[data-addmap]").forEach((b) => b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); addRasterFromPath(b.dataset.addmap, { name: b.dataset.name }); });
    $$("[data-del]").forEach((b) => b.onclick = async () => { await api(`/api/jobs/${b.dataset.del}`, { method: "DELETE" }); refreshJobs(); });
    $$("[data-cancel]").forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = "Cancelling…"; await api(`/api/jobs/${b.dataset.cancel}/cancel`, { method: "POST" }); refreshJobs(); });
    clearTimeout(state.polling);
    if (running) state.polling = setTimeout(refreshJobs, 1500);
  }

  // ------------------------------------------------------------------ credentials
  async function loadCreds() {
    const data = await api("/api/credentials");
    const p = data.providers;
    $("#creds-dot").classList.toggle("ok", Object.values(p).some((x) => x.complete));
    $("#creds-backend").textContent = `Storage: ${data.backend}.`;
    $("#creds-list").innerHTML = Object.entries(p).map(([key, prov]) => `
      <form class="cred" data-p="${key}" autocomplete="off">
        <h4><span>${esc(prov.title)}</span>${prov.complete ? '<span class="status done">saved</span>' : '<span class="status">not set</span>'}</h4>
        <p>${esc(prov.help)} <a href="${esc(prov.signup)}" target="_blank" rel="noopener">Get credentials ↗</a></p>
        ${prov.fields.map((f) => `<label>${esc(f.label)}
          <input name="${f.name}" type="${f.secret ? "password" : "text"}" autocomplete="${f.secret ? "new-password" : "off"}"
            placeholder="${f.set ? (f.secret ? "•••••••• saved. Leave blank to keep it." : esc(f.display)) : ""}"></label>`).join("")}
        <div class="row">
          <button class="btn primary small" type="submit">Save</button>
          <button class="btn small" type="button" data-test>Test</button>
          ${prov.fields.some((f) => f.set) ? '<button class="btn small danger" type="button" data-remove>Remove</button>' : ""}
        </div>
        <div class="msg"></div>
      </form>`).join("");
    $$("#creds-list form").forEach((form) => {
      const provider = form.dataset.p, msg = $(".msg", form);
      form.onsubmit = async (e) => {
        e.preventDefault();
        const values = Object.fromEntries(new FormData(form));
        if (!Object.values(values).some((v) => v)) { msg.className = "msg err"; msg.textContent = "Nothing to save."; return; }
        await api(`/api/credentials/${provider}`, { method: "PUT", json: values });
        toast("Saved to keychain");
        await loadCreds();
      };
      $("[data-test]", form).onclick = (e) => busy(e.currentTarget, "Testing…", async () => {
        const r = await api(`/api/credentials/${provider}/test`, { method: "POST" });
        msg.className = "msg " + (r.ok ? "ok" : "err");
        msg.textContent = (r.ok ? "✓ " : "✗ ") + r.message;
      });
      $("[data-remove]", form)?.addEventListener("click", async () => {
        if (!confirm("Remove these saved credentials?")) return;
        await api(`/api/credentials/${provider}`, { method: "DELETE" });
        await loadCreds();
      });
    });
  }
  $("#btn-creds").onclick = async () => { await loadCreds(); $("#dlg-creds").showModal(); };

  // generic modal close
  $$("dialog").forEach((d) => {
    $$("[data-close]", d).forEach((b) => b.onclick = () => d.close());
    d.addEventListener("click", (e) => e.target === d && d.close());
  });

  // ------------------------------------------------------------------ index analysis tool
  const an = { catalog: null, layer: null, info: null, bandMap: {}, cat: "All", sel: null, res: null,
               reqId: 0, resultId: null, userSet: new Set(), pending: null };
  const hasBands = (bands) => bands.every((b) => b in an.bandMap);

  function refreshAnalyzeInputs() {
    const sel = $("#an-input");
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    sel.innerHTML = `<option value="">${rasters.length ? "Choose a raster layer…" : "No raster layers yet"}</option>` +
      rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
    if (an.layer && rasters.includes(an.layer)) sel.value = an.layer.id;
  }
  $("#an-input").onchange = () => { const l = getLayer($("#an-input").value); if (l) openInput(l); else resetAnalyze(); };

  function resetAnalyze() {
    Object.assign(an, { layer: null, info: null, bandMap: {}, sel: null, pending: null, resultId: null });
    $("#an-input").value = "";
    $("#an-info").classList.add("hidden");
    $("#an-result").classList.add("hidden");
    $("#an-setup").classList.add("hidden");
    renderIndexButtons();
  }

  function openInput(l) {
    const info = l.info;
    Object.assign(an, { layer: l, info, bandMap: l.band_map, userSet: new Set(l.userSet || []), sel: null, pending: null, resultId: null });
    $("#an-input").value = l.id;
    $("#an-info").classList.remove("hidden");
    $("#an-name").textContent = l.name;
    $("#an-meta").textContent = `${info.width.toLocaleString()} × ${info.height.toLocaleString()} px · ${fmt(info.res[0], info.res[0] < 1 ? 2 : 1)} ${/^EPSG:4326$/.test(info.crs) ? "°" : "m"} · ${info.crs} · ${info.count} band${info.count > 1 ? "s" : ""} · ${/\.vrt$/i.test(l.path) ? "virtual, reads the original product" : fmt(info.size_mb) + " MB"}`;
    $("#an-scale").value = l.scale ?? info.scale;
    $("#an-offset").value = l.offset ?? info.offset;
    const unchanged = (l.scale ?? info.scale) === info.scale && (l.offset ?? info.offset) === info.offset;
    const match = unchanged ? [info.scale_preset] : Object.entries(an.catalog.scale_presets).find(([, p]) => Math.abs(p.scale - $("#an-scale").value) < 1e-12 && Math.abs(p.offset - $("#an-offset").value) < 1e-12);
    $("#an-scale-preset").value = match ? match[0] : "";
    $("#an-scalereason").textContent = `Detected: ${info.scale_reason}.`;
    renderBandTable();
    updateScaleSummary();
    renderSetup();
    renderIndexButtons();
    $("#an-result").classList.add("hidden");
    selectLayer(l.id);
  }
  function analyzeLayer(l) { switchTool("analyze"); openInput(l); }

  // Band / scale fixes are stored on the input layer itself, so they persist and apply to its display.
  function syncInputLayer() {
    const l = an.layer;
    if (!l) return;
    l.userSet = [...an.userSet];
    l.scale = parseFloat($("#an-scale").value) || 1;
    l.offset = parseFloat($("#an-offset").value) || 0;
    saveLayers();
    if (l.render?.composite) renderRaster(l).catch(() => {});
  }

  function renderBandTable() {
    const info = an.info, names = an.catalog.band_names;
    const byIndex = Object.fromEntries(Object.entries(an.bandMap).map(([k, v]) => [v, k]));
    $("#an-bandtable").innerHTML = info.bands.map((b) => `<tr><td>${b.index} · ${esc(b.description)}</td><td>
      <select data-band="${b.index}"><option value="">— not used —</option>${names.map((n) =>
        `<option ${byIndex[b.index] === n ? "selected" : ""}>${n}</option>`).join("")}</select></td></tr>`).join("");
    $$("#an-bandtable select").forEach((s) => s.onchange = () => {
      const idx = +s.dataset.band;
      Object.keys(an.bandMap).forEach((k) => (an.bandMap[k] === idx || k === s.value) && delete an.bandMap[k]);
      if (s.value) { an.bandMap[s.value] = idx; an.userSet.add(s.value); }
      renderBandTable();
      renderIndexButtons();
      renderSetup();
      syncInputLayer();
      if (an.sel) compute();
    });
    const mapped = Object.keys(an.bandMap).sort();
    $("#an-bandsum").textContent = mapped.length ? mapped.join(" ") : "none mapped. Open to set them.";
    $("#an-bandsrc").textContent = `Detected from ${info.band_map_source}. Change a dropdown if a band is wrong.`;
    $("#an-bands-details").open = !mapped.length;
  }

  function updateScaleSummary() {
    const p = $("#an-scale-preset").selectedOptions[0];
    $("#an-scalesum").textContent = p ? p.textContent : `× ${$("#an-scale").value} + ${$("#an-offset").value}`;
  }

  function renderIndexButtons() {
    const c = an.catalog;
    $("#an-composites").innerHTML = Object.entries(c.composites).map(([k, v]) =>
      `<button class="chip ${an.sel?.composite === k ? "active" : ""}" data-comp="${k}" ${an.info ? "" : "disabled"} style="${an.info && !hasBands(v.bands) ? "border-style:dashed;opacity:.55" : ""}" title="${esc(v.bands.join(", "))}">${esc(v.title)}</button>`).join("");
    $$("#an-composites [data-comp]").forEach((b) => b.onclick = () => select({ composite: b.dataset.comp }));
    $("#an-cats").innerHTML = ["All", ...c.categories].map((k) => `<button class="chip ${an.cat === k ? "active" : ""}" data-cat="${esc(k)}">${esc(k)}</button>`).join("");
    $$("#an-cats [data-cat]").forEach((b) => b.onclick = () => { an.cat = b.dataset.cat; renderIndexButtons(); });
    const list = c.indices.filter((i) => an.cat === "All" || i.category === an.cat);
    $("#an-index-grid").innerHTML = list.map((i) => {
      const ok = an.info && hasBands(i.bands);
      const missing = i.bands.filter((b) => !(b in an.bandMap));
      return `<button class="idx ${an.sel?.index === i.name ? "active" : ""} ${an.info && !ok ? "needs" : ""}" data-idx="${i.name}" ${an.info ? "" : "disabled"}
        title="${esc(i.title)}\n${esc(i.formula)}${ok ? "" : `\nNeeds ${missing.join(", ")}. Click to assign.`}"><b>${esc(i.name)}</b><small>${ok || !an.info ? esc(i.title) : "needs " + missing.join(", ")}</small></button>`;
    }).join("");
    $$("#an-index-grid [data-idx]").forEach((b) => b.onclick = () => select({ index: b.dataset.idx }));
    $("#an-band-chips").innerHTML = Object.keys(an.bandMap).sort().map((b) => `<button class="chip" data-ins="${b}">${b}</button>`).join("");
    $$("#an-band-chips [data-ins]").forEach((b) => b.onclick = () => {
      const f = $("#an-formula"), pos = f.selectionStart ?? f.value.length;
      f.value = f.value.slice(0, pos) + b.dataset.ins + f.value.slice(f.selectionEnd ?? pos);
      f.focus();
      f.selectionStart = f.selectionEnd = pos + b.dataset.ins.length;
    });
  }

  // Choosing an index opens its setup (band assignment + hints) and computes it when every band is assigned.
  function select(sel) {
    $("#an-cmap").value = "";
    an.pending = sel;
    renderSetup();
    const spec = selSpec(sel);
    if (spec.bands.every((b) => b in an.bandMap)) compute(sel);
    else $("#an-setup").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function analyzeBody() {
    return { path: an.layer.path, band_map: an.bandMap, scale: parseFloat($("#an-scale").value) || 1, offset: parseFloat($("#an-offset").value) || 0 };
  }

  // Composites change how the input layer is displayed; indices / formulas create (or update) a result layer.
  // `sel` only becomes the current selection once it renders, so a bad formula never sticks.
  async function compute(sel = an.sel) {
    if (!an.layer || !sel) return;
    const id = ++an.reqId;
    $("#an-busy").classList.remove("hidden");
    try {
      if (sel.composite) {
        an.layer.render = { composite: sel.composite };
        await renderRaster(an.layer);
        if (id !== an.reqId) return;
        an.sel = sel;
        renderIndexButtons();
        if (an.pending === sel) renderSetup();
        $("#an-result").classList.add("hidden");
        status(`${an.layer.name}: showing ${state.catalog.composites[sel.composite].title}`);
        return;
      }
      const key = sel.index || "f:" + sel.formula;
      let out = layers.find((l) => l.derived && l.sourceId === an.layer.id && l.key === key);
      const isNew = !out;
      if (isNew) {
        out = addLayer({ type: "raster", derived: true, sourceId: an.layer.id, key, path: an.layer.path, info: an.layer.info,
                         name: `${sel.index || "Formula"} · ${an.layer.name}`, render: {} });
      }
      const body = analyzeBody();
      Object.assign(out, { band_map: { ...body.band_map }, scale: body.scale, offset: body.offset });
      const clip = getClip("an-area");
      out.render = { index: sel.index || null, formula: sel.formula || null, stretch: $("#an-stretch").value, cmap: $("#an-cmap").value || null, clip };
      out.name = `${sel.index || "Formula"} · ${an.layer.name}${clip ? " · area" : ""}`;
      if (out.render.stretch === "custom") { out.render.vmin = parseFloat($("#an-vmin").value); out.render.vmax = parseFloat($("#an-vmax").value); }
      $("#an-result").classList.remove("hidden");
      let res;
      try {
        res = await trackFetch((signal) => renderRaster(out, { signal }),
          { tool: "analyze", title: `${sel.index || "Formula"} · ${an.layer.name}`, message: clip ? "Computing for the selected area" : "Computing for the whole image" });
      } catch (e) {
        if (isNew) { removeLayer(out.id, { silent: true }); renderContents(); saveLayers(); }
        throw e;
      }
      if (id !== an.reqId) return;
      const clipChanged = JSON.stringify(out.lastClip || null) !== JSON.stringify(clip || null);
      out.lastClip = clip;
      an.sel = sel;
      an.resultId = out.id;
      selectLayer(out.id);
      if (clip && (isNew || clipChanged)) zoomTo(out);
      if (sel.formula) an.lastFormula = sel.formula;
      renderIndexButtons();
      if (an.pending === sel) renderSetup();
      showResult(res);
      status(`${out.name} computed. It's in Contents.`);
    } catch (e) {
      if (id === an.reqId && notCancelled(e)) toast(e.message, true);
    } finally {
      if (id === an.reqId) $("#an-busy").classList.add("hidden");
    }
  }

  function showResult(res) {
    const spec = an.catalog.indices.find((i) => i.name === an.sel.index);
    const isComp = !!an.sel.composite;
    $("#an-title").textContent = isComp ? res.title : spec ? `${spec.name} · ${spec.title}` : "Custom formula";
    $("#an-desc").textContent = isComp ? `Red = ${res.bands[0]}, green = ${res.bands[1]}, blue = ${res.bands[2]} (2–98% stretch).` : spec?.description || "";
    $("#an-formula-show").textContent = isComp ? "" : res.formula;
    $("#an-formula-show").classList.toggle("hidden", isComp);
    $("#an-legend-wrap").classList.toggle("hidden", isComp);
    $("#an-export-one").classList.toggle("hidden", isComp);
    if (isComp) return;
    $("#an-cmap").value = res.cmap;
    $("#an-legend").style.background = `linear-gradient(to right, ${res.colors.join(", ")})`;
    $("#an-lmin").textContent = fmtv(res.vmin);
    $("#an-lmid").textContent = fmtv((res.vmin + res.vmax) / 2);
    $("#an-lmax").textContent = fmtv(res.vmax);
    if ($("#an-stretch").value !== "custom") { $("#an-vmin").value = +res.vmin.toFixed(4); $("#an-vmax").value = +res.vmax.toFixed(4); }
    const h = res.stats.histogram, max = Math.max(...h.counts, 1), n = h.counts.length, bw = 300 / n;
    const colorAt = (t) => { // same interpolation as the server colormap
      const c = res.colors, pos = t * (c.length - 1), lo = Math.min(Math.floor(pos), c.length - 2), f = pos - lo;
      const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      const a = rgb(c[lo]), b = rgb(c[lo + 1]);
      return `rgb(${a.map((v, i) => Math.round(v * (1 - f) + b[i] * f)).join(",")})`;
    };
    $("#an-hist").innerHTML = h.counts.map((cnt, i) => {
      const hh = (cnt / max) * 58;
      return `<rect x="${i * bw + 0.5}" y="${60 - hh}" width="${bw - 1}" height="${hh}" fill="${colorAt((i + 0.5) / n)}"><title>${fmtv(h.edges[i])} – ${fmtv(h.edges[i + 1])}: ${cnt.toLocaleString()} px</title></rect>`;
    }).join("");
    const s = res.stats;
    $("#an-stats").innerHTML = [["Mean", s.mean], ["Median", s.median], ["Std dev", s.std], ["Min", s.min], ["Max", s.max],
      ["Pixels", null, s.count.toLocaleString() + (res.decimated ? "*" : "")]].map(([k, v, txt]) =>
      `<div>${k}<b>${txt ?? fmtv(v)}</b></div>`).join("");
    $("#an-stats").title = res.decimated ? "* Stats are computed on the preview resolution. The GeoTIFF export is full resolution." : "";
  }

  // display options
  $("#an-cmap").onchange = () => compute();
  $("#an-stretch").onchange = () => { $("#an-custom-range").classList.toggle("hidden", $("#an-stretch").value !== "custom"); compute(); };
  let rangeTimer;
  ["#an-vmin", "#an-vmax"].forEach((s) => $(s).oninput = () => { clearTimeout(rangeTimer); rangeTimer = setTimeout(() => compute(), 500); });
  $("#an-scale-preset").onchange = () => {
    const p = an.catalog.scale_presets[$("#an-scale-preset").value];
    if (p) { $("#an-scale").value = p.scale; $("#an-offset").value = p.offset; }
    updateScaleSummary();
    renderSetup();
    syncInputLayer();
    compute();
  };
  ["#an-scale", "#an-offset"].forEach((s) => $(s).onchange = () => {
    const sc = parseFloat($("#an-scale").value), off = parseFloat($("#an-offset").value);
    const match = Object.entries(an.catalog.scale_presets).find(([, p]) => Math.abs(p.scale - sc) < 1e-12 && Math.abs(p.offset - off) < 1e-12);
    $("#an-scale-preset").value = match ? match[0] : "";
    updateScaleSummary();
    renderSetup();
    syncInputLayer();
    compute();
  });
  $("#an-formula-go").onclick = async () => {
    const f = $("#an-formula").value.trim();
    if (!f) return toast("Type a formula first", true);
    const r = await checkFormula();
    if (!r) return toast("Fix the formula first (see the message under it)", true);
    select({ formula: f, bands: r.bands });
  };
  $("#an-formula").addEventListener("keydown", (e) => e.key === "Enter" && $("#an-formula-go").click());

  // export: the result layer through the layer export dialog; several indices as one multi-band GeoTIFF
  $("#an-export-one").onclick = () => { const l = getLayer(an.resultId); if (l) openExport(l); };
  async function doExport(indices, formulas, btn) {
    const job = await api("/api/analyze/export", { method: "POST", json: { ...analyzeBody(), indices, formulas, clip: getClip("an-area") } });
    $("#dlg-export").close();
    try {
      const done = await trackJob(job, { tool: "analyze" });
      const r = done.result;
      download(r.url, r.name);
      toast(`Saved ${r.name} (${r.layers.length} band${r.layers.length > 1 ? "s" : ""})`);
    } catch (e) { if (notCancelled(e)) toast(e.message, true); }
  }
  $("#an-export-many").onclick = () => {
    if (!an.info) return toast("Choose an image first", true);
    $("#ex-list").innerHTML = an.catalog.indices.map((i) => {
      const ok = hasBands(i.bands);
      return `<label title="${esc(i.title)}"><input type="checkbox" value="${i.name}" ${ok ? "" : "disabled"} ${ok && an.sel?.index === i.name ? "checked" : ""}>${esc(i.name)}</label>`;
    }).join("");
    const f = an.lastFormula;  // last formula that computed successfully
    $("#ex-custom-wrap").classList.toggle("hidden", !f);
    $("#ex-custom-f").textContent = f || "";
    $("#ex-custom").checked = !!an.sel?.formula;
    $("#ex-error").classList.add("hidden");
    $("#dlg-export").showModal();
  };
  $("#ex-all").onclick = () => $$("#ex-list input:not(:disabled)").forEach((i) => i.checked = true);
  $("#ex-none").onclick = () => $$("#ex-list input").forEach((i) => i.checked = false);
  $("#ex-go").onclick = async (e) => {
    const indices = $$("#ex-list input:checked").map((i) => i.value);
    const f = an.lastFormula;
    const formulas = $("#ex-custom").checked && f ? [{ name: "custom", formula: f }] : [];
    if (!indices.length && !formulas.length) { $("#ex-error").textContent = "Select at least one index"; $("#ex-error").classList.remove("hidden"); return; }
    try { await doExport(indices, formulas, e.currentTarget); $("#dlg-export").close(); }
    catch (err) { $("#ex-error").textContent = err.message; $("#ex-error").classList.remove("hidden"); }
  };

  // ------------------------------------------------------------------ index setup (band assignment + hints)
  const BAND_RE = /[A-Za-z_][A-Za-z0-9_]*/g;
  const FORMULA_ALIASES = { COASTAL: "B01", BLUE: "B02", GREEN: "B03", RED: "B04", RE1: "B05", RE2: "B06", RE3: "B07",
    REDEDGE: "B05", NIR: "B08", NIR2: "B8A", NARROWNIR: "B8A", WV: "B09", SWIR1: "B11", SWIR2: "B12" };
  function normBand(tok) { // mirror of lulc_fetch.indices.normalize_band
    const up = tok.toUpperCase();
    if (FORMULA_ALIASES[up]) return FORMULA_ALIASES[up];
    const m = up.match(/^B0?(\d{1,2})(A?)$/);
    if (!m) return null;
    const cand = m[1] === "8" && m[2] ? "B8A" : `B${String(+m[1]).padStart(2, "0")}`;
    return an.catalog.band_names.includes(cand) ? cand : null;
  }
  const bandLabel = (b) => an.catalog.band_info[b]?.short || b;
  const refl = (bandIdx) => { // file band median converted to reflectance
    const st = an.info.bands.find((x) => x.index === bandIdx);
    if (!st || st.median == null) return null;
    return st.median * (parseFloat($("#an-scale").value) || 1) + (parseFloat($("#an-offset").value) || 0);
  };
  const prettyFormula = (f) => f.replace(/\*\*/g, "^").replace(/\s*\*\s*/g, " × ").replace(/\s\/\s/g, " ÷ ");

  function selSpec(sel) {
    if (!sel) return null;
    if (sel.index) {
      const s = an.catalog.indices.find((i) => i.name === sel.index);
      return { title: `${s.name} · ${s.title}`, bands: s.bands, formula: s.formula, category: s.category, desc: s.description };
    }
    if (sel.composite) {
      const c = an.catalog.composites[sel.composite];
      return { title: c.title, bands: c.bands, formula: `R = ${c.bands[0]}, G = ${c.bands[1]}, B = ${c.bands[2]}`, composite: true };
    }
    return { title: "Custom formula", bands: sel.bands || [], formula: sel.formula };
  }

  function bandWarnings(bands) {
    const out = [], m = an.bandMap, info = an.info;
    const unconfirmed = bands.filter((b) => !an.userSet?.has(b));
    if (/^guessed/.test(info.band_map_source) && unconfirmed.length) {
      out.push(["warn", `This file has no band names, so the band order was <b>guessed from the band count</b> (${info.count} bands). Check ${unconfirmed.join(", ")} below.`]);
    } else if (/^unknown/.test(info.band_map_source)) {
      out.push(["err", "The bands in this file couldn't be identified. Assign each band below."]);
    }
    const used = {};
    bands.filter((b) => b in m).forEach((b) => (used[m[b]] ||= []).push(b));
    Object.entries(used).filter(([, bs]) => bs.length > 1).forEach(([idx, bs]) =>
      out.push(["err", `File band ${idx} is assigned to <b>${bs.join(" and ")}</b> at the same time. Each should usually be a different band.`]));
    const r = (b) => (b in m ? refl(m[b]) : null);
    const nir = r("B08") ?? r("B8A"), red = r("B04");
    if (bands.some((b) => ["B08", "B8A", "B04"].includes(b)) && nir != null && red != null && nir < red * 0.8) {
      out.push(["warn", `<b>NIR looks darker than Red</b> (median ${fmtv(nir)} vs ${fmtv(red)}). Land with any vegetation is normally brighter in NIR, so NIR and Red may be <b>swapped or assigned to the wrong bands</b>. (This can be normal for scenes that are mostly water or dense city.)`]);
    }
    const s1 = r("B11"), s2 = r("B12");
    if (bands.some((b) => ["B11", "B12"].includes(b)) && s1 != null && s2 != null && s2 > s1 * 1.3) {
      out.push(["warn", `SWIR2 (B12) is brighter than SWIR1 (B11) (${fmtv(s2)} vs ${fmtv(s1)}). That's unusual, so they may be swapped.`]);
    }
    const vals = bands.filter((b) => b in m).map((b) => [b, r(b)]).filter(([, v]) => v != null);
    const high = vals.filter(([, v]) => v > 1.5), low = vals.filter(([, v]) => v > 0 && v < 0.002);
    if (high.length) out.push(["err", `Values don't look like reflectance (median ${high.map(([b, v]) => `${b} = ${fmtv(v)}`).join(", ")}). Change <b>Pixel values</b> in step 1, e.g. to “Sentinel-2 DN (÷10000)” or “8-bit”.`]);
    if (low.length) out.push(["warn", `Values are very small (${low.map(([b, v]) => `${b} = ${fmtv(v)}`).join(", ")}). Check the <b>Pixel values</b> scale in step 1.`]);
    return out;
  }

  function renderSetup() {
    const sel = an.pending, spec = selSpec(sel), card = $("#an-setup");
    if (!spec || !an.info) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    const info = an.catalog.band_info, m = an.bandMap;
    $("#su-title").textContent = spec.title;
    const words = (f) => f.replace(BAND_RE, (t) => { const b = normBand(t); return b ? bandLabel(b) : t; });
    const onFile = (f) => f.replace(BAND_RE, (t) => {
      const b = normBand(t);
      if (!b) return t;
      return b in m ? `Band ${m[b]}` : `<span class="chk-err">[${b}?]</span>`;
    });
    $("#su-f-bands").textContent = prettyFormula(spec.formula);
    $("#su-f-words").textContent = prettyFormula(words(spec.formula));
    $("#su-f-file").innerHTML = prettyFormula(onFile(esc(spec.formula)));

    const options = (cur) => `<option value="">— not assigned —</option>` + an.info.bands.map((b) => {
      const v = refl(b.index);
      const named = b.description !== `Band ${b.index}` ? ` · ${esc(b.description)}` : "";
      return `<option value="${b.index}" ${cur === b.index ? "selected" : ""}>Band ${b.index}${named}${v != null ? ` · median ${fmtv(v)}` : ""}</option>`;
    }).join("");
    $("#su-rows").innerHTML = spec.bands.map((b) => {
      const bi = info[b] || {}, cur = m[b], file = an.info.bands.find((x) => x.index === cur);
      const named = file && normBand(file.description.replace(/\s/g, "")) === b;
      const hint = cur == null
        ? `Not assigned. Which band of your file is <b>${esc(bi.name)}</b> (~${bi.nm} nm)? Elsewhere it is ${esc(bi.equiv)}.`
        : named ? `✓ Band ${cur} is named “${esc(file.description)}”, which matches.`
        : `Band ${cur}${file.description !== `Band ${cur}` ? ` (“${esc(file.description)}”)` : ""} is assumed to be ${esc(bi.short)}. Change it if your file's order is different. Elsewhere this band is ${esc(bi.equiv)}.`;
      return `<div class="su-row ${cur == null ? "missing" : "ok"}">
        <div class="su-band"><b>${b} · ${esc(bi.short || "")}</b><small>${esc(bi.name || "")}<br>${bi.nm ? bi.nm + " nm" : ""}</small></div>
        <select data-role="${b}">${options(cur)}</select>
        <div class="su-hint">${hint}</div></div>`;
    }).join("");
    $$("#su-rows select").forEach((s) => s.onchange = () => {
      if (s.value) an.bandMap[s.dataset.role] = +s.value; else delete an.bandMap[s.dataset.role];
      an.userSet.add(s.dataset.role);
      syncInputLayer();
      renderBandTable();
      renderIndexButtons();
      renderSetup();
      if (spec.bands.every((b) => b in an.bandMap)) compute(an.pending);
    });

    const missing = spec.bands.filter((b) => !(b in m));
    const warns = bandWarnings(spec.bands);
    if (missing.length) warns.unshift(["err", `Assign <b>${missing.join(", ")}</b> to compute this. If your image doesn't have ${missing.length > 1 ? "these bands" : "this band"}, pick one of the suggestions below.`]);
    $("#su-warn").innerHTML = warns.map(([k, t]) => `<div class="warn ${k === "err" ? "err" : ""}">${t}</div>`).join("");
    const go = $("#su-go");
    go.disabled = missing.length > 0;
    go.textContent = missing.length ? `Assign ${missing.join(", ")} first` : (an.sel && JSON.stringify(an.sel) === JSON.stringify(sel) ? "Recompute" : "Compute");

    // suggestions: indices that work with the bands this image has
    const avail = an.catalog.indices.filter((i) => hasBands(i.bands) && i.name !== sel.index);
    let sugg = [];
    if (spec.category) sugg = avail.filter((i) => i.category === spec.category);
    if (missing.length && !sugg.length && spec.category === "Vegetation") sugg = avail.filter((i) => i.category === "RGB only");
    if (!spec.category && !spec.composite) sugg = avail.slice(0, 6);
    $("#su-suggest").innerHTML = sugg.length
      ? `${missing.length ? "Works with your image instead:" : "Related indices for this image:"} ` +
        sugg.slice(0, 8).map((i) => `<button class="chip sugg" data-sugg="${i.name}" title="${esc(i.title)}: ${esc(i.formula)}">${i.name}</button>`).join("")
      : "";
    $$("#su-suggest [data-sugg]").forEach((b) => b.onclick = () => select({ index: b.dataset.sugg }));
  }
  $("#su-go").onclick = () => compute(an.pending);
  $("#su-close").onclick = () => $("#an-setup").classList.add("hidden");

  // custom formula: live check + examples
  let fcTimer;
  async function checkFormula() {
    const f = $("#an-formula").value.trim(), out = $("#an-formula-check");
    if (!f) { out.innerHTML = ""; return null; }
    const r = await api(`/api/formula/check?formula=${encodeURIComponent(f)}`);
    if (!r.ok) { out.innerHTML = `<span class="chk-err">✗ ${esc(r.error)}</span>`; return null; }
    out.innerHTML = "Uses " + r.bands.map((b) => b in an.bandMap
      ? `<span class="chk-ok">${b} (${esc(bandLabel(b))}) → Band ${an.bandMap[b]} ✓</span>`
      : `<span class="chk-err">${b} (${esc(bandLabel(b))}) → not assigned ✗</span>`).join(", ");
    return r;
  }
  $("#an-formula").addEventListener("input", () => { clearTimeout(fcTimer); fcTimer = setTimeout(checkFormula, 350); });
  $("#an-formula-examples").onchange = (e) => {
    const s = an.catalog.indices.find((i) => i.name === e.target.value);
    if (!s) return;
    $("#an-formula").value = s.formula;
    e.target.value = "";
    checkFormula();
    $("#an-formula").focus();
  };

  // ------------------------------------------------------------------ ⓘ hints (hover ~0.5 s, or click to pin)
  let tipTimer = null, tipFor = null;
  function showTip(btn, pinned) {
    const pop = $("#tip-pop");
    pop.innerHTML = esc(btn.dataset.tip);
    pop.classList.remove("hidden");
    pop.classList.toggle("pinned", pinned);
    $$(".tip.pinned").forEach((t) => t !== btn && t.classList.remove("pinned"));
    btn.classList.toggle("pinned", pinned);
    const r = btn.getBoundingClientRect(), pr = pop.getBoundingClientRect();
    let left = r.left + r.width / 2 - pr.width / 2, top = r.bottom + 8;
    if (top + pr.height > innerHeight - 8) top = r.top - pr.height - 8;
    pop.style.left = Math.max(8, Math.min(left, innerWidth - pr.width - 8)) + "px";
    pop.style.top = top + "px";
    tipFor = btn;
  }
  function hideTip(force = false) {
    clearTimeout(tipTimer);
    if (!force && tipFor?.classList.contains("pinned")) return;
    $("#tip-pop").classList.add("hidden");
    $$(".tip.pinned").forEach((t) => t.classList.remove("pinned"));
    tipFor = null;
  }
  document.addEventListener("mouseover", (e) => {
    const b = e.target.closest?.(".tip");
    if (!b || tipFor?.classList.contains("pinned")) return;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => showTip(b, false), 450);
  });
  document.addEventListener("mouseout", (e) => { if (e.target.closest?.(".tip")) hideTip(); });
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".tip");
    if (b) { e.preventDefault(); e.stopPropagation(); b.classList.contains("pinned") ? hideTip(true) : showTip(b, true); return; }
    if (!e.target.closest("#tip-pop")) hideTip(true);
  }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(true); });
  const tipBtn = (text) => `<button type="button" class="tip" data-tip="${esc(text)}" aria-label="Help">i</button>`;

  // ------------------------------------------------------------------ PCA & dimensionality reduction tool
  const pcaState = { schema: null, layer: null, method: "pca", job: null };

  function refreshPcaInputs() {
    const sel = $("#pca-input");
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    sel.innerHTML = `<option value="">${rasters.length ? "Choose a raster layer…" : "No raster layers yet"}</option>` +
      rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
    if (pcaState.layer && rasters.includes(pcaState.layer)) sel.value = pcaState.layer.id;
    else if (pcaState.layer) { pcaState.layer = null; renderPcaBands(); }
  }

  function recommendedBand(b) {
    const name = an.catalog ? normBand(String(b.description).replace(/\s/g, "")) : null;
    return name && !pcaState.schema.atmospheric_bands.includes(name) && name !== "VVVH";
  }
  function renderPcaBands() {
    const l = pcaState.layer, box = $("#pca-bands");
    if (!l) { box.innerHTML = ""; $("#pca-bands-hint").textContent = "Choose an input image first."; return; }
    const bands = l.info.bands, anyKnown = bands.some(recommendedBand);
    box.innerHTML = bands.map((b) => {
      const rec = anyKnown ? recommendedBand(b) : true;
      const label = b.description !== `Band ${b.index}` ? esc(b.description) : `${b.index}`;
      return `<label title="Band ${b.index}: ${esc(b.description)}"><input type="checkbox" value="${b.index}" data-rec="${rec ? 1 : 0}" ${rec ? "checked" : ""}>${label}</label>`;
    }).join("");
    $$("#pca-bands input").forEach((i) => i.onchange = updatePcaBandHint);
    updatePcaBandHint();
  }
  const pcaBands = () => $$("#pca-bands input:checked").map((i) => +i.value);
  function updatePcaBandHint() {
    const n = pcaBands().length;
    $("#pca-bands-hint").textContent = n < 2 ? "Select at least 2 bands." : `${n} bands selected.`;
    const nc = $('[data-p="n_components"]');
    if (nc && pcaState.method !== "kernel") { nc.max = Math.max(1, n); if (+nc.value > n && n >= 1) nc.value = Math.min(3, n); }
  }
  $("#pca-bands-rec").onclick = () => { $$("#pca-bands input").forEach((i) => i.checked = i.dataset.rec === "1"); updatePcaBandHint(); };
  $("#pca-bands-all").onclick = () => { $$("#pca-bands input").forEach((i) => i.checked = true); updatePcaBandHint(); };
  $("#pca-bands-none").onclick = () => { $$("#pca-bands input").forEach((i) => i.checked = false); updatePcaBandHint(); };

  function renderPcaMethods() {
    const ms = pcaState.schema.methods;
    $("#pca-methods").innerHTML = Object.entries(ms).map(([k, m]) => `<label class="opt">
      <input type="radio" name="pcam" value="${k}" ${k === pcaState.method ? "checked" : ""}>
      <span><b>${esc(m.title)}${m.recommended ? '<span class="rec">RECOMMENDED</span>' : ""}${tipBtn(m.tip)}</b><small>${esc(m.desc)}</small></span></label>`).join("");
    $$('input[name="pcam"]').forEach((r) => r.onchange = () => { pcaState.method = r.value; renderPcaParams(true); });
  }

  function pcaField(p, value) {
    let input;
    if (p.type === "bool") input = `<input type="checkbox" data-p="${p.name}" ${value ? "checked" : ""}>`;
    else if (p.type === "select") input = `<select data-p="${p.name}">${p.options.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
    else input = `<input type="number" data-p="${p.name}" step="${p.type === "int" ? 1 : "any"}" ${p.min != null ? `min="${p.min}"` : ""} ${p.max != null ? `max="${p.max}"` : ""}
      value="${value ?? ""}" placeholder="${esc(p.placeholder || "")}">`;
    const off = p.name === "standardize" && ["nmf", "svd"].includes(pcaState.method);
    return `<div class="pca-field ${off ? "disabled" : ""}"><span>${esc(p.label)}${tipBtn(p.tip)}</span>${input}
      ${off ? `<div class="note">Not used by ${esc(pcaState.schema.methods[pcaState.method].title)}: bands are rescaled to 0–1 instead.</div>` : ""}</div>`;
  }
  function renderPcaParams(keepCommon = false) {
    const sc = pcaState.schema, m = sc.methods[pcaState.method];
    const prev = keepCommon ? collectPcaParams(true) : {};
    const all = [...sc.common, ...m.params];
    const val = (p) => (p.name in prev ? prev[p.name] : p.default);
    $("#pca-params").innerHTML = all.filter((p) => !p.advanced).map((p) => pcaField(p, val(p))).join("");
    const adv = all.filter((p) => p.advanced);
    $("#pca-adv").innerHTML = adv.map((p) => pcaField(p, val(p))).join("");
    $("#pca-adv-wrap").classList.toggle("hidden", !adv.length);
    $$('#pca-params [data-p="standardize"], #pca-adv [data-p="standardize"]').forEach((i) => i.disabled = ["nmf", "svd"].includes(pcaState.method));
    updatePcaBandHint();
  }
  function collectPcaParams(raw = false) {
    const out = {};
    $$("#pca-params [data-p], #pca-adv [data-p]").forEach((i) => {
      const v = i.type === "checkbox" ? i.checked : i.value;
      out[i.dataset.p] = raw ? v : (i.type === "number" ? (v === "" ? null : +v) : v);
    });
    return out;
  }
  $("#pca-reset").onclick = () => { renderPcaParams(false); if (pcaState.layer) $("#pca-bands-rec").click(); };
  $("#pca-input").onchange = () => {
    pcaState.layer = getLayer($("#pca-input").value);
    renderPcaBands();
    if (pcaState.layer) selectLayer(pcaState.layer.id);
  };

  $("#pca-run").onclick = async () => {
    const l = pcaState.layer, err = $("#pca-error");
    err.classList.add("hidden");
    if (!l) return toast("Choose an input image first", true);
    const bands = pcaBands();
    if (bands.length < 2) return toast("Select at least 2 bands", true);
    const m = pcaState.schema.methods[pcaState.method];
    const btn = $("#pca-run");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>Running ${esc(m.title)}…`;
    $("#pca-report").classList.add("hidden");
    $("#pca-status").textContent = `Starting ${m.title}…`;
    try {
      const job = await api("/api/pca/run", { method: "POST", json: {
        path: l.path, bands, method: pcaState.method, params: collectPcaParams(), clip: getClip("pca-area"), name: l.name } });
      addedJobs.add(job.id);  // this tool adds the result itself (as an RGB of the components)
      prefs.set("addedJobs", [...addedJobs].slice(-200));
      pcaState.job = job.id;
      $("#pca-status").textContent = "";
      const j = await trackJob(job, { tool: "pca", title: `${m.title} · ${l.name}` });
      const rep = j.result;
      const n = rep.n_components;
      const out = await addRasterFromPath(rep.path, { name: `${m.title} (${n}) · ${l.name}${getClip("pca-area") ? " · area" : ""}`,
        render: n >= 3 ? { rgb: [1, 2, 3] } : { band: 1, stretch: "auto", cmap: "Viridis" } });
      out.pcaReport = rep;
      saveLayers();
      showPcaReport(rep, out);
      $("#pca-status").textContent = `Done in ${rep.seconds} s. The result is in Contents.`;
      refreshJobs();
    } catch (e) {
      $("#pca-status").textContent = e.cancelled ? "Cancelled. Change the parameters and run again." : "";
      if (!e.cancelled) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally {
      btn.disabled = false;
      btn.textContent = "Run analysis";
    }
  };

  function showPcaReport(rep, layer) {
    const box = $("#pca-report");
    const ev = rep.explained_variance || rep.explained_variance_kept;
    let chart = "";
    if (ev) {
      const w = 300, h = 110, n = ev.length, bw = Math.min(46, (w - 20) / n - 6);
      let cum = 0;
      const pts = [];
      const bars = ev.map((v, i) => {
        cum += v;
        const x = 14 + i * ((w - 20) / n), bh = v * (h - 30);
        pts.push(`${x + bw / 2},${h - 18 - cum * (h - 30)}`);
        return `<rect x="${x}" y="${h - 18 - bh}" width="${bw}" height="${bh}" rx="2" fill="var(--accent)" opacity=".85"><title>${rep.prefix}${i + 1}: ${(v * 100).toFixed(1)}%</title></rect>
          <text x="${x + bw / 2}" y="${h - 5}" font-size="9" text-anchor="middle" fill="currentColor">${rep.prefix}${i + 1}</text>
          <text x="${x + bw / 2}" y="${h - 21 - bh}" font-size="9" text-anchor="middle" fill="currentColor">${(v * 100).toFixed(1)}%</text>`;
      }).join("");
      chart = `<div class="home-label" style="margin-top:14px">${rep.explained_variance ? "Explained variance" : "Share of variance among kept components"}
          ${tipBtn(rep.explained_variance ? "How much of the image's total variation each component holds. The line is the running total. If the first 3 components add up to over 95%, they summarise the image well." : "Kernel PCA can't measure the share of total variance, only how the kept components compare with each other.")}</div>
        <svg class="ev-chart" viewBox="0 0 ${w} ${h}" style="color:var(--muted)">${bars}
          ${rep.explained_variance ? `<polyline points="${pts.join(" ")}" fill="none" stroke="var(--text)" stroke-width="1.5" stroke-dasharray="3 2"/>` : ""}</svg>
        ${rep.explained_variance ? `<div class="hint" style="margin-top:0">First ${ev.length} components hold <b>${(ev.reduce((a, b) => a + b, 0) * 100).toFixed(1)}%</b> of the variation.</div>` : ""}`;
    }
    let table = "";
    if (rep.loadings) {
      const maxAbs = Math.max(...rep.loadings.flat().map(Math.abs)) || 1;
      const cell = (v) => {
        const a = Math.min(1, Math.abs(v) / maxAbs), c = v >= 0 ? "31,122,90" : "180,35,24";
        return `<td style="background:rgba(${c},${(a * 0.75).toFixed(2)});color:${a > 0.55 ? "#fff" : "inherit"}">${v.toFixed(2)}</td>`;
      };
      table = `<div class="home-label" style="margin-top:14px">Band loadings ${tipBtn("How strongly each band contributes to each component (green = positive, red = negative). Bands with large values drive that component. E.g. NIR high and Red negative means the component tracks vegetation.")}</div>
        <div class="load-wrap"><table class="load-table"><tr><th></th>${rep.bands.map((b) => `<th>${esc(b)}</th>`).join("")}</tr>
        ${rep.loadings.map((row, i) => `<tr><th>${rep.prefix}${i + 1}</th>${row.map(cell).join("")}</tr>`).join("")}</table></div>`;
    }
    const pcaHint = rep.method === "pca" || rep.method === "incremental"
      ? `<p class="hint">Typical reading for multispectral imagery: <b>${rep.prefix}1</b> ≈ overall brightness, <b>${rep.prefix}2</b> ≈ vegetation vs. soil / built-up, <b>${rep.prefix}3</b> ≈ moisture / water. Check the loadings to confirm.</p>` : "";
    box.innerHTML = `<div class="pca-sum"><b>${esc(rep.title)}</b> · ${rep.n_components} components from ${rep.bands.length} bands
        (${esc(rep.bands.join(", "))})<br>Output ${rep.width.toLocaleString()} × ${rep.height.toLocaleString()} px${rep.factor > 1 ? ` (${rep.factor}× coarser than native)` : " at native resolution"}
        · fitted on ${rep.pixels_fit.toLocaleString()} pixels · ${rep.standardized ? "standardized" : "not standardized"} · ${rep.seconds} s
        ${rep.reconstruction_error != null ? `<br>Reconstruction error: ${rep.reconstruction_error.toFixed(3)}` : ""}</div>
      ${rep.warnings?.length ? `<div class="warn">${rep.warnings.map(esc).join("<br>")}. Try more iterations (Advanced).</div>` : ""}
      ${chart}${table}${pcaHint}
      <div class="row"><button class="btn small" data-pz>Zoom to result</button><button class="btn small" data-px>Export…</button>
        <button class="btn small" data-pp title="Show another combination of components">Change display…</button></div>`;
    box.classList.remove("hidden");
    $("[data-pz]", box).onclick = () => zoomTo(layer);
    $("[data-px]", box).onclick = () => openExport(layer);
    $("[data-pp]", box).onclick = () => openProps(layer);
  }

  async function initPca() {
    pcaState.schema = await api("/api/pca/schema");
    renderPcaMethods();
    renderPcaParams(false);
    renderPcaBands();
  }

  async function initAnalyze() {
    an.catalog = state.catalog = await api("/api/indices");
    $("#an-cmap").innerHTML = `<option value="">Index default</option>` + Object.keys(an.catalog.colormaps).map((k) => `<option>${k}</option>`).join("");
    $("#an-scale-preset").innerHTML = Object.entries(an.catalog.scale_presets).map(([k, p]) => `<option value="${k}">${esc(p.title)}</option>`).join("") +
      `<option value="">Custom</option>`;
    $("#an-formula-examples").innerHTML = `<option value="">Choose a formula to edit…</option>` +
      an.catalog.indices.map((i) => `<option value="${i.name}">${i.name}: ${esc(i.formula)}</option>`).join("");
    renderIndexButtons();
  }

  // ------------------------------------------------------------------ init
  (async () => {
    applyTheme(prefs.get("theme", "auto"));
    setPane("contents", prefs.get("contents", true));
    buildToolsMenu();
    state.config = await api("/api/config");
    $("#mission").innerHTML = Object.entries(state.config.missions).map(([k, m]) => `<option value="${k}">${esc(m.title)}</option>`).join("");
    const fillSources = () => {
      $("#source").innerHTML = mission().sources.map((k) => `<option value="${k}">${esc(state.config.sources[k])}</option>`).join("");
    };
    $("#mission").value = prefs.get("mission", "sentinel-2") in state.config.missions ? prefs.get("mission", "sentinel-2") : "sentinel-2";
    fillSources();
    $("#mission").onchange = () => { prefs.set("mission", $("#mission").value); fillSources(); clearResults(); };
    $("#indices-list").textContent = `(${state.config.indices.join(", ")})`;
    $("#dl-product").innerHTML = Object.entries(state.config.label_products).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    const years = { worldcover: [2021, 2020], esri: [2023, 2022, 2021, 2020, 2019, 2018, 2017] };
    const fillYears = () => $("#dl-year").innerHTML = years[$("#dl-product").value].map((y) => `<option>${y}</option>`).join("");
    $("#dl-product").onchange = fillYears;
    fillYears();
    setRange(90);
    await initAnalyze();
    initPca().catch((e) => toast("PCA tool: " + e.message, true));
    restoreLayers();
    loadCreds().catch(() => {});
    setPane("tools", false);  // the tool panel opens only when a tool is chosen from the Tools menu
    refreshJobs();
  })().catch((e) => toast("Could not reach the server: " + e.message, true));
})();
