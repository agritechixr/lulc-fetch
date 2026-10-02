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
  async function trackJob(job, { tool = currentTool, title, save } = {}) {
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
      if (save) await saveOutputs(save, j);
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
    samples: '<path d="M4 20l5-12 6 4 5-8"/><circle cx="9" cy="8" r="1.6"/><circle cx="15" cy="12" r="1.6"/><path d="M14 20h7M17.5 16.5v7" opacity=".7"/>',
    stack: '<path d="M12 3l9 4.5-9 4.5-9-4.5z"/><path d="M3 12l9 4.5 9-4.5"/><path d="M3 16.5l9 4.5 9-4.5"/>',
    table: '<path d="M4 6l4-3 4 3 4-3 4 3v6"/><path d="M4 6v6"/><rect x="3" y="14" width="18" height="7" rx="1"/><path d="M3 17.5h18M9 14v7M15 14v7"/>',
    ml: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 4v16"/>',
    pca: '<circle cx="7" cy="16" r="1.4"/><circle cx="11" cy="12" r="1.4"/><circle cx="15" cy="10" r="1.4"/><circle cx="9" cy="17" r="1.4"/><circle cx="17" cy="7" r="1.4"/><path d="M3 21L21 3"/><path d="M8 6l10 10" opacity=".5"/>',
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    raster: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    vector: '<path d="M4 18l5-12 7 4 4 8z"/>',
    rasterml: '<rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1" opacity=".55"/><rect x="3" y="13" width="8" height="8" rx="1" opacity=".55"/><rect x="13" y="13" width="8" height="8" rx="1"/><path d="M5.5 7h3M15.5 17h3"/>',
    cluster: '<circle cx="7" cy="8" r="1.6"/><circle cx="10" cy="6" r="1.6"/><circle cx="9" cy="10.5" r="1.6"/><circle cx="16" cy="15" r="1.6"/><circle cx="18.5" cy="12.5" r="1.6"/><circle cx="15" cy="18" r="1.6"/><circle cx="18" cy="18.5" r="1.6"/><path d="M4.5 4.5a6 6 0 0 1 8 7.5M12 19a6 6 0 0 0 9-8" opacity=".55"/>',
    tsne: '<circle cx="6" cy="7" r="1.5"/><circle cx="8" cy="9.5" r="1.5"/><circle cx="5" cy="11" r="1.5"/><circle cx="16" cy="6" r="1.5"/><circle cx="18" cy="8.5" r="1.5"/><circle cx="12" cy="17" r="1.5"/><circle cx="14.5" cy="18.5" r="1.5"/><circle cx="11" cy="20" r="1.5"/><path d="M3 3v18h18" opacity=".55"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>',
  };
  const svg = (name, w = 1.8) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
  const TOOLS = [
    { id: "search", title: "Find imagery", icon: "search", subtitle: "Search, preview and download Sentinel-2, composites and land-cover labels" },
    { id: "analyze", title: "Index analysis", icon: "analyze", subtitle: "NDVI, SAVI, EVI, NDWI and 21 more indices or your own formula" },
    { id: "pca", title: "PCA & dimensionality reduction", icon: "pca", subtitle: "PCA, Kernel PCA, NMF, ICA and more (scikit-learn) on any multiband image" },
    { id: "samples", title: "Training samples", icon: "samples", subtitle: "Draw labelled polygons and points for each class on the map" },
    { id: "stack", title: "Stack layers", icon: "stack", subtitle: "Combine bands from several layers (S2, S1, DEM, indices…) onto one grid" },
    { id: "raster2table", title: "Raster → table", icon: "table", subtitle: "Turn any image (multispectral, hyperspectral, SAR) into a table, with optional ground-truth labels" },
    { id: "ml", title: "Classical ML (tabular data)", icon: "ml", subtitle: "Machine-learning tools that work on tables" },
    { id: "rasterml", title: "Classical ML for raster", icon: "rasterml", subtitle: "Train SVM, Maximum Likelihood, Random Forest, SAM and more straight from an image and ground truth, and map it: RGB, multispectral, hyperspectral or embeddings" },
    { id: "export", title: "Export data", icon: "export", subtitle: "Save any layer to your computer: GeoTIFF, PNG, Shapefile, GeoJSON, KML" },
    { id: "jobs", title: "Downloads & jobs", icon: "jobs", subtitle: "Background downloads, logs and output files" },
  ];
  let currentTool = "home";

  function buildToolsMenu() {
    $("#tools-menu").innerHTML = TOOLS.map((t) => `<button class="tool-item" data-tool="${t.id}">
        <span class="ic">${svg(t.icon)}</span><span><b>${esc(t.title)}</b><small>${esc(t.subtitle)}</small></span></button>` +
        (t.id === "ml" ? ML_SUBTOOLS.map((st) => `<button class="tool-item sub" data-tool="ml" data-sub="${st.id}"><span><b>${esc(st.title)}</b></span></button>`).join("") : "")).join("") +
      "";
    $$("#tools-menu [data-tool]").forEach((b) => b.onclick = () => { switchTool(b.dataset.tool); if (b.dataset.tool === "ml") openMlSub(b.dataset.sub || null); toggleMenu(null); });
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
    if (tool.id === "ml") { openMlSub(mlSub); }
    if (tool.id === "raster2table") refreshRtInputs();
    if (tool.id === "rasterml") refreshRm();
    if (tool.id === "samples") renderSamples();
    if (tool.id === "stack") refreshStack();
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
    $("#mi-viewer").classList.toggle("on", document.body.classList.contains("viewer-open"));
    if ($("#mi-project-close")) $("#mi-project-close").disabled = !inProject();
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
      case "toggle-viewer": setViewer(!viewerOpen()); break;
      case "project-new": showProjectDialog(); break;
      case "project-open": pickFolder({ title: "Open a project folder", mode: "project" }).then((f) => f && projectOpen(f).catch((e) => toast(e.message, true))); break;
      case "project-close": projectClose().catch((e) => toast(e.message, true)); break;
      case "project-reveal": api("/api/project/reveal", { method: "POST", json: {} }).catch((e) => toast(e.message, true)); break;
      case "clean-cache": openCacheDialog().catch((e) => toast(e.message, true)); break;
      case "reset-layout": resetLayout(); break;
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
    if (currentTool === "samples") renderSamples();
    if (currentTool === "stack") refreshStack();
    if (currentTool === "ml" && mlSub === "predict" && mlx.schema) refreshPredictRasters();
    refreshClipPickers();
    if (currentTool === "export") { refreshExportLayers(); if (!exportLayer()) renderExportForm(); }
  }

  function onLayerRemoved(l) {
    if (vw.tabs.some((t) => t.key === "attr:" + l.id)) closeTab("attr:" + l.id);
    if (l.id === "aoi") { state.aoi = null; $("#aoi-summary").classList.add("hidden"); $("#aoi-warn").classList.add("hidden"); }
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

  // ------------------------------------------------------------------ context menu
  function showCtx(l, x, y) {
    const isPoly = l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type));
    const items = [
      ["Zoom to layer", () => zoomTo(l)],
      ["Properties…", () => openProps(l)],
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
    m.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
    m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
  }
  function hideCtx() { $("#ctx-menu").classList.add("hidden"); }

  // ------------------------------------------------------------------ panel sizes: drag the edges of Contents, Tools and the data viewer
  const LAYOUT = { "w-contents": 290, "w-tools": 400, "h-viewer": 300 };
  const gisEl = $("#gis"), centerEl = $("#center");
  function applySizes() {
    gisEl.style.setProperty("--contents-w", prefs.get("w-contents", LAYOUT["w-contents"]) + "px");
    gisEl.style.setProperty("--tools-w", prefs.get("w-tools", LAYOUT["w-tools"]) + "px");
    centerEl.style.setProperty("--viewer-h", prefs.get("h-viewer", LAYOUT["h-viewer"]) + "px");
  }
  let sizeRaf = 0;
  const mapResized = () => { cancelAnimationFrame(sizeRaf); sizeRaf = requestAnimationFrame(() => map.invalidateSize({ pan: false })); };
  function makeResizer(handle, { axis, key, compute, min, max }) {
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      document.body.classList.add(axis === "x" ? "resizing-x" : "resizing-y");
      handle.classList.add("active");
      if (key === "h-viewer") $("#viewer").classList.remove("maxed");
      const move = (ev) => { prefs.set(key, Math.round(Math.min(max(), Math.max(min, compute(ev))))); applySizes(); mapResized(); };
      const up = () => {
        handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); handle.removeEventListener("pointercancel", up);
        document.body.classList.remove("resizing-x", "resizing-y"); handle.classList.remove("active"); mapResized();
      };
      handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up); handle.addEventListener("pointercancel", up);
    });
    handle.addEventListener("dblclick", () => { prefs.set(key, LAYOUT[key]); $("#viewer").classList.remove("maxed"); applySizes(); mapResized(); });
  }
  makeResizer($("#rz-contents"), { axis: "x", key: "w-contents", min: 200, max: () => Math.min(720, innerWidth * 0.45),
    compute: (e) => e.clientX - gisEl.getBoundingClientRect().left });
  makeResizer($("#rz-tools"), { axis: "x", key: "w-tools", min: 300, max: () => Math.min(1100, innerWidth * 0.6),
    compute: (e) => gisEl.getBoundingClientRect().right - e.clientX });
  makeResizer($("#rz-viewer"), { axis: "y", key: "h-viewer", min: 120, max: () => centerEl.getBoundingClientRect().height - 70,
    compute: (e) => centerEl.getBoundingClientRect().bottom - e.clientY });
  function resetLayout() {
    Object.entries(LAYOUT).forEach(([k, v]) => prefs.set(k, v));
    $("#viewer").classList.remove("maxed");
    applySizes(); mapResized();
  }
  $("#viewer-max").onclick = () => { $("#viewer").classList.toggle("maxed"); mapResized(); };
  applySizes();

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

  // ------------------------------------------------------------------ data viewer (bottom panel): tables, attribute tables, pictures
  const vw = { tabs: [], active: null };
  let vwSeq = 0, rowMarker = null;
  const viewerOpen = () => document.body.classList.contains("viewer-open");
  function setViewer(show) {
    document.body.classList.toggle("viewer-open", show);
    if (show) renderViewer();
    syncMenuChecks();
    mapResized();
  }
  function openTab(tab) {
    let t = vw.tabs.find((x) => x.key === tab.key);
    if (!t) { t = { offset: 0, limit: prefs.get("vw-limit", 100), q: "", sort: null, desc: false, mode: "rows", ...tab }; vw.tabs.push(t); t.data = t.stats = null; }
    else if (tab.mode && tab.mode !== t.mode) t.mode = tab.mode;
    vw.active = t.key;
    if (!viewerOpen()) setViewer(true); else renderViewer();
    return t;
  }
  function closeTab(key, force = false) {
    const i = vw.tabs.findIndex((t) => t.key === key);
    if (i < 0) return;
    const tt = vw.tabs[i];
    if (tt.edit && !force) {
      if (isDirty(tt)) { vw.active = tt.key; renderViewer(); askSaveEdits(tt); return; }
      finishEdit(tt, "discard").catch(() => {});
      return;
    }
    if (tt.edit && force && tt.kind === "attr") { const l = getLayer(tt.layerId); if (l && l._original !== undefined) { l.geojson.features = JSON.parse(l._original); delete l._original; } }
    vw.tabs.splice(i, 1);
    if (vw.active === key) vw.active = vw.tabs[Math.max(0, i - 1)]?.key || null;
    clearRowMarker();
    if (!vw.tabs.length) setViewer(false); else renderViewer();
  }
  const activeTab = () => vw.tabs.find((t) => t.key === vw.active) || null;
  const TAB_IC = { table: "ml", attr: "vector", picture: "image" };

  function renderViewer() {
    $("#viewer-tabs").innerHTML = vw.tabs.map((t) => `<div class="vtab ${t.key === vw.active ? "on" : ""}" data-key="${esc(t.key)}" title="${esc(t.title)}">
      <span class="vtab-ic">${svg(TAB_IC[t.kind])}</span><span class="vtab-t">${esc(t.title)}</span><button class="vtab-x" title="Close">×</button></div>`).join("");
    $$("#viewer-tabs .vtab").forEach((el) => {
      el.onclick = (e) => { if (e.target.closest(".vtab-x")) return closeTab(el.dataset.key); vw.active = el.dataset.key; renderViewer(); };
      el.onauxclick = (e) => { if (e.button === 1) closeTab(el.dataset.key); };
    });
    const t = activeTab(), body = $("#viewer-body");
    if (!t) {
      body.innerHTML = `<div class="vw-empty">${svg("ml")}<b>Data viewer</b><span>Double-click a table in <b>Contents ▸ Tabular data</b> to open it here, right-click a vector layer for its <b>attribute table</b>, or open a picture. Drag the top edge to resize.</span></div>`;
      return;
    }
    if (t.kind === "picture") return renderPictureTab(t, body);
    renderTableTab(t, body);
  }

  // ---- tables (server-side paging for files, client-side for vector attribute tables)
  function renderTableTab(t, body) {
    const isAttr = t.kind === "attr";
    body.innerHTML = `<div class="vt-bar">
        <label class="vt-search" title="Type to search every column. Or filter one column: yield > 4 · crop = rice · id != 3">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>
          <input type="search" placeholder="Search…  or  column > value" value="${esc(t.q)}"></label>
        <div class="seg small vt-mode"><button data-mode="rows" class="${t.mode === "rows" ? "active" : ""}">Rows</button><button data-mode="stats" class="${t.mode === "stats" ? "active" : ""}">Column statistics</button></div>
        <span class="vt-count"></span>
        <span class="vt-pager"><button data-pg="first" title="First page">«</button><button data-pg="prev" title="Previous page">‹</button><button data-pg="next" title="Next page">›</button><button data-pg="last" title="Last page">»</button>
          <select class="vt-limit" title="Rows per page">${[50, 100, 250, 1000].map((n) => `<option ${n === t.limit ? "selected" : ""}>${n}</option>`).join("")}</select></span>
        <span class="grow"></span>
        <button class="btn small vt-edit-btn ${t.edit ? "on" : ""}" data-act="edit" title="Edit: add / calculate / rename / delete fields, edit cells, delete rows">✎ Edit</button>
        ${isAttr ? `<button class="btn small" data-act="zoomlayer">Zoom to layer</button>`
          : `<button class="btn small hidden" data-act="points" title="Add the rows as points on the map (uses the current search)">Show on map</button>
             <button class="btn small" data-act="train" title="Open Train a model with this table">Train a model</button>
             <a class="btn small" href="/api/tables/file?path=${encodeURIComponent(t.path)}" download title="Download the file">⬇</a>`}
      </div>
      <div class="vt-editbar ${t.edit ? "" : "hidden"}">
        <button class="btn small" data-ed="addfield" title="Add a new field (column), empty or calculated">+ Add field</button>
        <button class="btn small" data-ed="calc" title="Calculate values with an expression (new or existing field)">ƒx Field calculator</button>
        ${isAttr ? "" : `<button class="btn small" data-ed="addrow" title="Append an empty row at the end">+ Add row</button>`}
        <button class="btn small danger" data-ed="delrows" disabled title="Delete the ticked rows">Delete selected</button>
        <button class="btn small" data-ed="derive" title="Save the rows matching the current search as a new ${isAttr ? "layer" : "table"}">New ${isAttr ? "layer" : "table"} from filtered rows</button>
        <span class="grow"></span>
        <button class="btn small" data-ed="python" title="Change the ${isAttr ? "attributes" : "table"} with Python (pandas)">🐍 Python</button>
        <span class="vt-pending"><span class="vt-pending-n">No changes yet</span></span>
        <button class="btn small" data-ed="undo" title="Undo the last change">↶ Undo</button>
        <button class="btn small" data-ed="discard" title="Throw away all changes since you started editing">Discard</button>
        <button class="btn small primary" data-ed="save" title="Save the changes: over the original or as a new ${isAttr ? "layer" : "table"}">💾 Save…</button>
        <span class="hint vt-edit-hint" style="margin:0">Double-click a cell to edit · ⋯ on a column for more</span>
      </div>
      <div class="vt-wrap"><div class="vt-content"></div></div>`;
    t.sel ||= new Set(); t.pending ||= new Map();
    $('[data-act="edit"]', body).onclick = () => toggleEdit(t);
    $$("[data-ed]", body).forEach((b) => b.onclick = () => editAction(t, b.dataset.ed));
    updateEditBar(t);
    const input = $(".vt-search input", body);
    let deb = 0;
    input.oninput = () => { clearTimeout(deb); deb = setTimeout(() => { t.q = input.value; t.offset = 0; t.stats = null; loadTab(t); }, 300); };
    $$(".vt-mode [data-mode]", body).forEach((b) => b.onclick = () => { t.mode = b.dataset.mode; renderViewer(); });
    $$(".vt-pager [data-pg]", body).forEach((b) => b.onclick = () => {
      const n = t.data?.filtered ?? 0, last = Math.max(0, Math.floor((n - 1) / t.limit) * t.limit);
      t.offset = { first: 0, prev: Math.max(0, t.offset - t.limit), next: Math.min(last, t.offset + t.limit), last }[b.dataset.pg];
      loadTab(t);
    });
    $(".vt-limit", body).onchange = (e) => { t.limit = +e.target.value; prefs.set("vw-limit", t.limit); t.offset = 0; loadTab(t); };
    $('[data-act="train"]', body)?.addEventListener("click", () => { switchTool("ml"); openMlSub("train"); refreshTrainTables(t.path); });
    $('[data-act="points"]', body)?.addEventListener("click", () => tablePoints(t.item || { name: t.title, path: t.path }, t.q));
    $('[data-act="zoomlayer"]', body)?.addEventListener("click", () => zoomTo(getLayer(t.layerId)));
    body.classList.toggle("stats-mode", t.mode === "stats");
    if ((t.mode === "rows" && t.data) || (t.mode === "stats" && t.stats)) drawTable(t); else loadTab(t);
  }

  async function loadTab(t) {
    const req = ++vwSeq;
    t.req = req;
    const content = vw.active === t.key && $("#viewer-body .vt-content");
    if (content) content.classList.add("loading");
    try {
      if (t.kind === "attr") {
        const l = getLayer(t.layerId);
        if (!l) throw new Error("The layer was removed");
        if (t.mode === "stats") t.stats = attrStats(l, t.q); else t.data = attrPage(l, t);
      } else if (t.mode === "stats") {
        t.stats = await api(`/api/tables/stats?path=${encodeURIComponent(t.path)}`);
      } else {
        t.data = await api(`/api/tables/rows?path=${encodeURIComponent(t.path)}&offset=${t.offset}&limit=${t.limit}&q=${encodeURIComponent(t.q)}${t.sort ? `&sort=${encodeURIComponent(t.sort)}&desc=${t.desc}` : ""}`);
      }
      t.error = null;
    } catch (e) { t.error = e.message; }
    if (t.req !== req || vw.active !== t.key) return;
    drawTable(t);
  }

  const fmtCell = (v, type) => {
    if (v === null || v === undefined || v === "") return '<span class="nul">–</span>';
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(+v.toPrecision(7));
    if (typeof v === "object") return esc(JSON.stringify(v));
    return esc(v);
  };
  const TYPE_TAG = { integer: "123", number: "1.5", text: "abc", boolean: "y/n", date: "date" };

  function drawTable(t) {
    const body = $("#viewer-body"), content = $(".vt-content", body);
    if (!content) return;
    content.classList.remove("loading");
    const count = $(".vt-count", body);
    if (t.error) { content.innerHTML = `<div class="vw-err">⚠ ${esc(t.error)}</div>`; count.textContent = ""; return; }
    if (t.mode === "stats") return drawStats(t, content, count);
    const d = t.data;
    const pts = $('[data-act="points"]', body);
    if (pts) pts.classList.toggle("hidden", !d.lonlat);
    const from = d.filtered ? d.offset + 1 : 0, to = d.offset + d.rows.length;
    count.innerHTML = `<b>${from.toLocaleString()}–${to.toLocaleString()}</b> of ${d.filtered.toLocaleString()} rows${d.filtered !== d.total ? ` <span class="muted">(filtered from ${d.total.toLocaleString()})</span>` : ""} · ${d.columns.length} columns`;
    $$(".vt-pager [data-pg]", body).forEach((b) => b.disabled = ["first", "prev"].includes(b.dataset.pg) ? d.offset === 0 : to >= d.filtered);
    const num = d.types.map((ty) => ty === "integer" || ty === "number");
    const ed = !!t.edit, pend = t.pending || new Map();
    const cell = (rid, i, v) => { const key = `${rid}|${d.columns[i]}`; return pend.has(key) ? { v: pend.get(key), dirty: true } : { v, dirty: false }; };
    content.innerHTML = d.rows.length ? `<table class="vt ${ed ? "editing" : ""}"><thead><tr>${ed ? `<th class="ck"><input type="checkbox" data-ckall title="Select all rows on this page"></th>` : ""}<th class="rn">#</th>${d.columns.map((c, i) =>
      `<th data-col="${esc(c)}" class="${num[i] ? "num" : ""} ${t.sort === c ? "sorted" : ""}" title="${esc(c)} (${d.types[i]}). Click to sort">
        <span class="th-n">${esc(c)}</span><i class="ty">${TYPE_TAG[d.types[i]] || ""}</i><i class="arr">${t.sort === c ? (t.desc ? "▼" : "▲") : "↕"}</i>${ed ? `<button class="th-menu" data-colmenu="${esc(c)}" title="Field options">⋯</button>` : ""}</th>`).join("")}</tr></thead>
      <tbody>${d.rows.map((r, k) => { const rid = d.row_ids[k]; return `<tr data-rid="${rid}" class="${t.selRow === rid ? "sel" : ""} ${t.sel?.has(rid) ? "picked" : ""}">${ed ? `<td class="ck"><input type="checkbox" data-ck ${t.sel?.has(rid) ? "checked" : ""}></td>` : ""}<td class="rn">${rid + 1}</td>${r.map((v, i) => {
        const c = cell(rid, i, v);
        return `<td data-c="${i}" class="${num[i] ? "num" : ""} ${c.dirty ? "dirty" : ""}">${fmtCell(c.v, d.types[i])}</td>`; }).join("")}</tr>`; }).join("")}</tbody></table>`
      : `<div class="vw-empty small"><b>No rows match</b><span>Clear the search or change the filter${ed && t.kind !== "attr" ? ", or add a row" : ""}.</span></div>`;
    if (ed) wireEditing(t, d, content);
    $$("th[data-col]", content).forEach((th) => th.onclick = (e) => {
      if (e.target.closest(".th-menu")) return;
      const c = th.dataset.col;
      if (t.sort !== c) { t.sort = c; t.desc = false; } else if (!t.desc) t.desc = true; else { t.sort = null; t.desc = false; }
      t.offset = 0; loadTab(t);
    });
    $$("tbody tr", content).forEach((tr) => tr.onclick = (e) => {
      if (e.target.closest(".ck, input, .cell-edit")) return;
      t.selRow = +tr.dataset.rid;
      $$("tbody tr", content).forEach((x) => x.classList.toggle("sel", x === tr));
      rowToMap(t, d, +tr.dataset.rid, [...d.rows[[...tr.parentNode.children].indexOf(tr)]]);
    });
    updateEditBar(t);
  }

  function drawStats(t, content, count) {
    const s = t.stats;
    count.innerHTML = `<b>${s.columns.length}</b> columns · ${s.rows.toLocaleString()} rows${t.kind === "attr" && t.q ? " (matching the search)" : ""}`;
    const spark = (h) => {
      const m = Math.max(1, ...h);
      return `<svg viewBox="0 0 ${h.length * 6} 24" class="spark" preserveAspectRatio="none">${h.map((v, i) => `<rect x="${i * 6}" y="${24 - 24 * v / m}" width="5" height="${24 * v / m}"/>`).join("")}</svg>`;
    };
    content.innerHTML = `<table class="vt stats"><thead><tr><th>Column</th><th>Type</th><th class="num">Missing</th><th class="num">Distinct</th><th class="num">Min</th><th class="num">Median</th><th class="num">Mean</th><th class="num">Max</th><th class="num">Std</th><th>Distribution / most frequent</th></tr></thead><tbody>
      ${s.columns.map((c) => `<tr><td><b>${esc(c.name)}</b></td><td><i class="ty">${TYPE_TAG[c.type] || esc(c.type)}</i> ${esc(c.type)}</td>
        <td class="num ${c.missing ? "warn-t" : ""}">${c.missing.toLocaleString()}${c.missing && s.rows ? ` <small>(${(100 * c.missing / s.rows).toFixed(1)}%)</small>` : ""}</td>
        <td class="num">${c.distinct.toLocaleString()}</td>
        ${["min", "median", "mean", "max", "std"].map((k) => `<td class="num">${c[k] == null ? '<span class="nul">–</span>' : fmtCell(c[k])}</td>`).join("")}
        <td>${c.hist ? spark(c.hist) : (c.top || []).slice(0, 5).map(([v, n]) => `<span class="topv">${v == null ? "(empty)" : esc(String(v).slice(0, 24))} <b>${n.toLocaleString()}</b></span>`).join("")}</td></tr>`).join("")}
      </tbody></table>`;
  }

  // vector attribute tables are paged / filtered / sorted in the browser with the same controls
  function attrColumns(l) {
    const feats = l.geojson?.features || [];
    const cols = [...new Set(feats.flatMap((f) => Object.keys(f.properties || {})))].filter((k) => !k.startsWith("_")).slice(0, 300);
    const types = cols.map((c) => {
      const vals = feats.map((f) => f.properties?.[c]).filter((v) => v !== null && v !== undefined && v !== "");
      if (vals.length && vals.every((v) => typeof v === "number")) return vals.every(Number.isInteger) ? "integer" : "number";
      if (vals.length && vals.every((v) => typeof v === "boolean")) return "boolean";
      return "text";
    });
    return { feats, cols, types };
  }
  function attrFilter(feats, cols, types, q) {
    let idx = feats.map((_, i) => i);
    q = (q || "").trim();
    if (!q) return idx;
    const m = q.match(/^\s*([^=<>!]+?)\s*(==|=|!=|>=|<=|>|<)\s*(.+?)\s*$/);
    if (m && cols.includes(m[1])) {
      const [, col, op, raw] = m, numeric = types[cols.indexOf(col)] !== "text";
      const val = numeric ? +raw : raw.replace(/^['"]|['"]$/g, "");
      if (numeric && Number.isNaN(val)) throw new Error(`'${raw}' is not a number`);
      const cmp = { "=": (a, b) => a == b, "==": (a, b) => a == b, "!=": (a, b) => a != b, ">": (a, b) => a > b, "<": (a, b) => a < b, ">=": (a, b) => a >= b, "<=": (a, b) => a <= b }[op];
      return idx.filter((i) => { const v = feats[i].properties?.[col]; return v !== null && v !== undefined && cmp(numeric ? v : String(v), val); });
    }
    const ql = q.toLowerCase();
    return idx.filter((i) => cols.some((c) => { const v = feats[i].properties?.[c]; return v !== null && v !== undefined && String(typeof v === "object" ? JSON.stringify(v) : v).toLowerCase().includes(ql); }));
  }
  function attrPage(l, t) {
    const { feats, cols, types } = attrColumns(l);
    let idx = attrFilter(feats, cols, types, t.q);
    if (t.sort && cols.includes(t.sort)) {
      const c = t.sort, dir = t.desc ? -1 : 1;
      idx = [...idx].sort((a, b) => {
        const va = feats[a].properties?.[c], vb = feats[b].properties?.[c];
        if (va == null || va === "") return 1;
        if (vb == null || vb === "") return -1;
        return (typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb), undefined, { numeric: true })) * dir;
      });
    }
    const sel = idx.slice(t.offset, t.offset + t.limit);
    return { columns: cols, types, rows: sel.map((i) => cols.map((c) => feats[i].properties?.[c] ?? null)), row_ids: sel,
             offset: t.offset, total: feats.length, filtered: idx.length, lonlat: null };
  }
  function attrStats(l, q) {
    const { feats, cols, types } = attrColumns(l);
    const idx = attrFilter(feats, cols, types, q);
    return { rows: idx.length, columns: cols.map((c, j) => {
      const vals = idx.map((i) => feats[i].properties?.[c]);
      const present = vals.filter((v) => v !== null && v !== undefined && v !== "");
      const st = { name: c, type: types[j], missing: vals.length - present.length, distinct: new Set(present.map((v) => typeof v === "object" ? JSON.stringify(v) : v)).size };
      if (types[j] === "integer" || types[j] === "number") {
        const a = present.slice().sort((x, y) => x - y), n = a.length;
        if (n) {
          const mean = a.reduce((s, v) => s + v, 0) / n;
          Object.assign(st, { min: a[0], max: a[n - 1], mean, median: a[Math.floor(n / 2)], std: Math.sqrt(a.reduce((s, v) => s + (v - mean) ** 2, 0) / n) });
          const lo = a[0], w = (a[n - 1] - lo) / 20 || 1;
          st.hist = Array(20).fill(0);
          a.forEach((v) => st.hist[Math.min(19, Math.floor((v - lo) / w))]++);
        }
      } else {
        const cnt = new Map();
        present.forEach((v) => { const k = typeof v === "object" ? JSON.stringify(v) : v; cnt.set(k, (cnt.get(k) || 0) + 1); });
        st.top = [...cnt.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
      }
      return st;
    }) };
  }
  function openAttr(l) {
    if (l.type !== "vector") return;
    openTab({ key: "attr:" + l.id, kind: "attr", title: `${l.name} · attributes`, layerId: l.id });
  }

  // ------------------------------------------------------------------ editing tables and attribute tables
  // Tables (files) are edited on the server (one undo step per change; cell edits are batched until Save).
  // Vector attribute tables are edited in the browser (immediately, with their own undo history).
  const isDirty = (t) => !!(t.pending?.size || t.log?.length);
  async function toggleEdit(t) {
    try {
      if (!t.edit) return await startEdit(t);
      if (isDirty(t)) return await askSaveEdits(t);
      await finishEdit(t, "discard");
    } catch (e) { toast(e.message, true); }
  }
  async function startEdit(t) {
    t.sel = new Set(); t.pending = new Map(); t.hist = [];
    if (t.kind === "attr") {
      const l = editLayer(t);
      l._original = JSON.stringify(l.geojson.features);   // until Save, the stored / project version stays the original
      t.log = [];
    } else {
      const r = await api("/api/tables/edit/start", { method: "POST", json: { path: t.path } });
      t.origPath = t.path; t.path = r.work; t.log = r.log || [];
      t.data = null; t.stats = null;
      if (r.resumed && t.log.length) toast(`Your unsaved changes from ${r.started} were kept: save or discard them`);
    }
    t.edit = true;
    renderViewer();
  }
  // overwrite | new | discard
  async function finishEdit(t, mode, name) {
    if (t.kind === "attr") {
      const l = editLayer(t);
      if (l && l._original !== undefined) {
        if (mode === "discard") l.geojson.features = JSON.parse(l._original);
        else if (mode === "new") {
          const edited = l.geojson.features;
          l.geojson.features = JSON.parse(l._original);
          addVectorLayer({ type: "FeatureCollection", features: edited }, name || `${l.name}_edited`, { zoom: false });
        }
        delete l._original;
        buildLeaflet(l); restack(); renderContents(); saveLayers();
      }
    } else {
      if (mode !== "discard" && t.pending?.size) await tableOps(t, [], `Edited ${t.pending.size} cell(s)`, { silent: true });
      if (mode === "discard") await api("/api/tables/edit/discard", { method: "POST", json: { path: t.path } });
      else {
        const r = await api("/api/tables/edit/save", { method: "POST", json: { path: t.path, mode, name } });
        if (mode === "new") addItem({ kind: "table", name: r.name, path: r.path });
      }
      t.path = t.origPath || t.path;
    }
    t.edit = false; t.log = []; t.hist = []; t.pending = new Map(); t.sel = new Set(); t.data = null; t.stats = null;
    renderViewer();
    if (t.kind !== "attr") {
      const it = dataItems.find((d) => d.path === t.path);
      if (it && t.data) { it.rows = t.data.total; it.cols = t.data.columns.length; saveItems(); renderItems(); }
    }
    toast(mode === "discard" ? "Changes discarded: the original is unchanged" : mode === "new" ? `Saved as a new ${t.kind === "attr" ? "layer" : "table"}` : "Changes saved");
  }
  // the Save dialog: lists the changes and warns before overwriting the existing table / layer
  function askSaveEdits(t) {
    const isAttr = t.kind === "attr", l = isAttr ? editLayer(t) : null;
    const what = isAttr ? l?.name : (t.origPath || t.path).split(/[\\/]/).pop();
    const n = (t.log?.length || 0) + (t.pending?.size ? 1 : 0);
    $("#se-title").textContent = `Save changes to ${what}`;
    $("#se-summary").innerHTML = `<b>${n}</b> change${n === 1 ? "" : "s"} since you started editing:`;
    $("#se-log").innerHTML = [...(t.log || []), ...(t.pending?.size ? [`Edited ${t.pending.size} cell(s) (not yet applied)`] : [])].map((x) => `<li>${esc(x)}</li>`).join("") || "<li>No changes</li>";
    $("#se-warn").innerHTML = isAttr
      ? `⚠ <b>Overwrite</b> applies these changes to the existing layer <b>${esc(what)}</b> in Contents${inProject() ? " and in the project" : ""}. This can't be undone afterwards. The original file on your computer (e.g. the shapefile you added) is not changed: use right-click ▸ <i>Save to folder…</i> to write a new file.`
      : `⚠ <b>Overwrite</b> applies these changes to the existing table <b>${esc(what)}</b>. Tools that use it (Train a model, Clustering …) will see the new version. The previous version is kept: right-click the table ▸ <i>Restore previous version</i>.`;
    $("#se-name").value = `${what.replace(/\.[^.]+$/, "")}_edited`;
    $("#se-new-label").textContent = isAttr ? "Name of the new layer" : "Name of the new table";
    $("#se-overwrite").textContent = `Overwrite ${what}`;
    $("#dlg-save-edits").showModal();
    const go = (mode) => async () => {
      if (mode === "discard" && !confirm("Throw away all changes? The original stays as it was.")) return;
      const name = $("#se-name").value.trim();
      if (mode === "new" && !name) return toast("Give the new one a name", true);
      $("#dlg-save-edits").close();
      try { await finishEdit(t, mode, name); } catch (e) { toast(e.message, true); }
    };
    $("#se-overwrite").onclick = go("overwrite");
    $("#se-new").onclick = go("new");
    $("#se-discard").onclick = go("discard");
  }
  function updateEditBar(t) {
    const body = $("#viewer-body");
    if (!body || vw.active !== t.key) return;
    const del = $('[data-ed="delrows"]', body);
    if (del) { del.disabled = !t.sel?.size; del.textContent = t.sel?.size ? `Delete ${t.sel.size} selected` : "Delete selected"; }
    const n = (t.log?.length || 0) + (t.pending?.size || 0), pend = $(".vt-pending", body);
    if (pend) {
      pend.classList.toggle("dirty", n > 0);
      $(".vt-pending-n", body).innerHTML = n ? `● <b>${n}</b> unsaved change${n === 1 ? "" : "s"}` : "No changes yet";
    }
    $$('[data-ed="save"], [data-ed="discard"]', body).forEach((b) => b.disabled = !n);
    const undo = $('[data-ed="undo"]', body);
    if (undo) undo.disabled = t.kind === "attr" ? !t.hist?.length : !(t.data?.undo || t.pending?.size);
  }
  const editLayer = (t) => getLayer(t.layerId);

  async function editAction(t, act) {
    try {
      if (act === "addfield") return openCalc(t, { mode: "new", title: "Add field" });
      if (act === "calc") return openCalc(t, { mode: t.data?.columns?.length ? "update" : "new" });
      if (act === "addrow") return await tableOps(t, [{ op: "add_row", values: {} }], "Added a row");
      if (act === "delrows") {
        const ids = [...t.sel];
        if (!ids.length || !confirm(`Delete ${ids.length} row(s)? (Undo is available.)`)) return;
        return t.kind === "attr" ? attrDeleteRows(t, ids) : await tableOps(t, [{ op: "delete_rows", rows: ids }], `Deleted ${ids.length} row(s)`);
      }
      if (act === "derive") return await deriveFiltered(t);
      if (act === "undo") return t.kind === "attr" ? attrUndo(t) : await tableUndo(t);
      if (act === "save") return askSaveEdits(t);
      if (act === "discard") { if (confirm("Throw away all changes since you started editing? The original stays as it was.")) await finishEdit(t, "discard"); return; }
      if (act === "python") return openPython(t);
    } catch (e) { toast(e.message, true); }
  }

  // ---- tables on the server
  async function tableOps(t, ops, msg, { silent = false } = {}) {
    const cellsMsg = t.pending?.size ? `Edited ${t.pending.size} cell(s)` : null;
    if (t.pending?.size) {
      const cells = [...t.pending.entries()].map(([k, value]) => { const i = k.indexOf("|"); return { row: +k.slice(0, i), col: k.slice(i + 1), value }; });
      ops = [{ op: "set_cells", cells }, ...ops];
    }
    if (!ops.length) return;
    const r = await api("/api/tables/edit", { method: "POST", json: { path: t.path, ops } });
    t.pending.clear(); t.sel = new Set(); t.data = null; t.stats = null;
    const it = dataItems.find((d) => d.path === t.path);
    if (it) { it.rows = r.rows; it.cols = r.columns.length; saveItems(); renderItems(); }
    const entries = [cellsMsg, ops.length > (cellsMsg ? 1 : 0) ? msg : null].filter(Boolean);
    t.log = [...(t.log || []), ...entries];
    if (entries.length) api("/api/tables/edit/log", { method: "POST", json: { path: t.path, add: entries } }).catch(() => {});
    await loadTab(t);
    if (!silent) toast(msg + (r.notes?.length ? ` · ${r.notes.join(" · ")}` : ""));
  }
  async function tableUndo(t) {
    if (t.pending?.size) { t.pending.clear(); drawTable(t); return toast("Unsaved cell edits discarded"); }
    const r = await api("/api/tables/undo", { method: "POST", json: { path: t.path } });
    t.log = (t.log || []).slice(0, -1);
    api("/api/tables/edit/log", { method: "POST", json: { path: t.path, pop: 1 } }).catch(() => {});
    t.data = null; t.stats = null; t.sel = new Set();
    const it = dataItems.find((d) => d.path === t.path);
    if (it) { it.rows = r.rows; it.cols = r.columns.length; saveItems(); renderItems(); }
    await loadTab(t);
    toast("Undone");
  }
  async function deriveFiltered(t) {
    const n = t.data?.filtered ?? 0;
    if (!t.q) { if (!confirm("No search / filter is active, so all rows will be copied. Continue?")) return; }
    const name = prompt(`Name for the new ${t.kind === "attr" ? "layer" : "table"} (${n.toLocaleString()} rows):`, `${t.title.replace(/ · attributes$/, "").replace(/\.[^.]+$/, "")}_selection`);
    if (!name) return;
    if (t.kind === "attr") {
      const l = editLayer(t), { feats, cols, types } = attrColumns(l);
      const idx = attrFilter(feats, cols, types, t.q);
      addVectorLayer({ type: "FeatureCollection", features: idx.map((i) => JSON.parse(JSON.stringify(feats[i]))) }, name);
      return toast(`New layer “${name}” with ${idx.length} feature(s)`);
    }
    const r = await api("/api/tables/derive", { method: "POST", json: { path: t.path, q: t.q, name } });
    addItem({ kind: "table", name: r.name, path: r.path }, { open: true });
    toast(`New table ${r.name}`);
  }

  // ---- vector attribute tables in the browser
  function attrSnapshot(t, l, label) {
    t.hist ||= [];
    t.hist.push(JSON.stringify(l.geojson.features));
    if (t.hist.length > 12) t.hist.shift();
    if (label) t.log = [...(t.log || []), label];
  }
  function attrChanged(t, l) {
    buildLeaflet(l); restack(); renderContents(); saveLayers();
    t.data = null; t.stats = null; t.sel = new Set();
    loadTab(t);
  }
  function attrUndo(t) {
    const l = editLayer(t);
    if (!l || !t.hist?.length) return;
    l.geojson.features = JSON.parse(t.hist.pop());
    t.log = (t.log || []).slice(0, -1);
    attrChanged(t, l);
    toast("Undone");
  }
  function attrDeleteRows(t, ids) {
    const l = editLayer(t), drop = new Set(ids);
    attrSnapshot(t, l, `Deleted ${ids.length} feature(s)`);
    l.geojson.features = l.geojson.features.filter((_, i) => !drop.has(i));
    attrChanged(t, l);
    toast(`Deleted ${ids.length} feature(s)`);
  }
  function coerceVal(raw, type) {
    if (raw === null || String(raw).trim() === "") return null;
    if (type === "integer" || type === "number") {
      const v = Number(String(raw).replace(/,/g, ""));
      if (!Number.isFinite(v)) throw new Error(`“${raw}” is not a number`);
      return type === "integer" ? Math.round(v) : v;
    }
    if (type === "boolean") return /^(1|true|yes|y)$/i.test(String(raw).trim());
    return String(raw);
  }
  function attrRenameKey(props, oldK, newK) {   // keeps the column order
    const out = {};
    for (const [k, v] of Object.entries(props || {})) out[k === oldK ? newK : k] = v;
    return out;
  }
  function attrFieldOp(t, op) {
    const l = editLayer(t), feats = l.geojson.features;
    attrSnapshot(t, l, op.op === "rename_field" ? `Renamed field ${op.old} → ${op.new}` : op.op === "delete_field" ? `Deleted field ${op.name}` : `Converted ${op.column} to ${op.type}`);
    if (op.op === "rename_field") feats.forEach((f) => { f.properties = attrRenameKey(f.properties, op.old, op.new); });
    else if (op.op === "delete_field") feats.forEach((f) => { if (f.properties) delete f.properties[op.name]; });
    else if (op.op === "cast") feats.forEach((f) => {
      const v = f.properties?.[op.column];
      if (v === undefined) return;
      f.properties[op.column] = op.type === "text" ? (v == null ? null : String(v)) : op.type === "boolean" ? (v == null ? null : /^(1|true|yes|y)$/i.test(String(v)))
        : (v == null || v === "" || !Number.isFinite(Number(v)) ? null : op.type === "integer" ? Math.trunc(Number(v)) : Number(v));
    });
    attrChanged(t, l);
  }

  // ---- inline cell editing, selection, column menus
  function wireEditing(t, d, content) {
    const ckAll = $("[data-ckall]", content);
    const syncAll = () => { if (ckAll) ckAll.checked = d.row_ids.length > 0 && d.row_ids.every((r) => t.sel.has(r)); };
    $$("[data-ck]", content).forEach((cb) => cb.onchange = () => {
      const rid = +cb.closest("tr").dataset.rid;
      cb.checked ? t.sel.add(rid) : t.sel.delete(rid);
      cb.closest("tr").classList.toggle("picked", cb.checked);
      syncAll(); updateEditBar(t);
    });
    if (ckAll) ckAll.onchange = () => {
      d.row_ids.forEach((r) => ckAll.checked ? t.sel.add(r) : t.sel.delete(r));
      $$("[data-ck]", content).forEach((cb) => { cb.checked = ckAll.checked; cb.closest("tr").classList.toggle("picked", ckAll.checked); });
      updateEditBar(t);
    };
    syncAll();
    $$("td[data-c]", content).forEach((td) => td.ondblclick = () => startCellEdit(t, d, td));
    $$("[data-colmenu]", content).forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      const col = b.dataset.colmenu, r = b.getBoundingClientRect(), type = d.types[d.columns.indexOf(col)];
      showMenu(`${col} (${type})`, [
        ["Calculate values…", () => openCalc(t, { mode: "update", column: col })],
        ["Rename…", () => renameField(t, col)],
        "-",
        type !== "number" ? ["Convert to decimal number", () => castField(t, col, "number")] : null,
        type !== "integer" ? ["Convert to whole number", () => castField(t, col, "integer")] : null,
        type !== "text" ? ["Convert to text", () => castField(t, col, "text")] : null,
        "-",
        ["Delete field", () => deleteField(t, col), "danger"],
      ].filter(Boolean), r.left, r.bottom + 2);
    });
  }
  function startCellEdit(t, d, td) {
    if (td.querySelector("input")) return;
    const tr = td.closest("tr"), rid = +tr.dataset.rid, i = +td.dataset.c, col = d.columns[i], type = d.types[i];
    const k = d.row_ids.indexOf(rid), key = `${rid}|${col}`;
    const cur = t.pending?.has(key) ? t.pending.get(key) : d.rows[k][i];
    td.classList.add("cell-edit");
    td.innerHTML = `<input value="${esc(cur ?? "")}" ${type === "integer" || type === "number" ? 'inputmode="decimal"' : ""}>`;
    const inp = $("input", td);
    inp.focus(); inp.select();
    let done = false;
    const finish = (save, move) => {
      if (done) return;
      done = true;
      td.classList.remove("cell-edit");
      const raw = inp.value;
      if (save && String(cur ?? "") !== raw) {
        try {
          const v = coerceVal(raw, type);
          if (t.kind === "attr") {
            const l = editLayer(t);
            attrSnapshot(t, l, `Edited ${col} of feature ${rid + 1}`);
            const f = l.geojson.features[rid];
            f.properties = { ...(f.properties || {}), [col]: v };
            d.rows[k][i] = v;
            buildLeaflet(l); restack(); saveLayers();
          } else {
            t.pending.set(key, raw.trim() === "" ? null : raw);
            td.classList.add("dirty");
          }
        } catch (e) { toast(e.message, true); }
      }
      const shown = t.pending?.has(key) ? t.pending.get(key) : d.rows[k][i];
      td.innerHTML = fmtCell(shown, type);
      updateEditBar(t);
      if (move) {   // Tab / Enter: continue in the next cell
        const next = move === "down" ? tr.nextElementSibling?.querySelector(`td[data-c="${i}"]`) : (td.nextElementSibling || tr.nextElementSibling?.querySelector('td[data-c="0"]'));
        if (next?.dataset.c !== undefined) startCellEdit(t, d, next);
      }
    };
    inp.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true, "down"); }
      else if (e.key === "Tab") { e.preventDefault(); finish(true, "right"); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    };
    inp.onblur = () => finish(true);
  }
  async function renameField(t, col) {
    const nw = prompt(`New name for “${col}”:`, col);
    if (!nw || nw.trim() === col) return;
    if (t.data.columns.includes(nw.trim())) return toast(`There is already a field called ${nw.trim()}`, true);
    try {
      if (t.kind === "attr") { attrFieldOp(t, { op: "rename_field", old: col, new: nw.trim() }); toast(`Renamed to ${nw.trim()}`); }
      else await tableOps(t, [{ op: "rename_field", old: col, new: nw.trim() }], `Renamed field ${col} → ${nw.trim()}`);
      if (t.sort === col) t.sort = null;
    } catch (e) { toast(e.message, true); }
  }
  async function deleteField(t, col) {
    if (!confirm(`Delete the field “${col}”? (Undo is available.)`)) return;
    try {
      if (t.kind === "attr") { attrFieldOp(t, { op: "delete_field", name: col }); toast(`Deleted ${col}`); }
      else await tableOps(t, [{ op: "delete_field", name: col }], `Deleted field ${col}`);
      if (t.sort === col) t.sort = null;
    } catch (e) { toast(e.message, true); }
  }
  async function castField(t, col, type) {
    try {
      if (t.kind === "attr") { attrFieldOp(t, { op: "cast", column: col, type }); toast(`${col} converted`); }
      else await tableOps(t, [{ op: "cast", column: col, type }], `Converted ${col} to ${type}`);
    } catch (e) { toast(e.message, true); }
  }

  async function restoreTable(it) {
    if (!confirm(`Restore the previous version of ${it.name}?\n\nThe version saved before the last change comes back (the current one is replaced).`)) return;
    try {
      const r = await api("/api/tables/restore", { method: "POST", json: { path: it.path } });
      it.rows = r.rows; it.cols = r.columns.length; saveItems(); renderItems();
      const t = vw.tabs.find((x) => x.path === it.path);
      if (t) { t.data = null; t.stats = null; if (vw.active === t.key) loadTab(t); }
      toast("Previous version restored");
    } catch (e) { toast(e.message, true); }
  }

  // ---- Python editor: change the table / attributes with pandas, in a separate process
  const pyx = { t: null };
  function pyExamples(t) {
    const cols = t.data?.columns || [], types = t.data?.types || [];
    const num = cols.find((c, i) => types[i] === "number" || types[i] === "integer") || "value";
    const txt = cols.find((c, i) => types[i] === "text") || "name";
    const q = (c) => JSON.stringify(c);
    const ex = [
      ["Add a calculated column", `df["${num}_x2"] = df[${q(num)}] * 2`],
      ["Classify values into groups", `df["${num}_class"] = np.where(df[${q(num)}] >= df[${q(num)}].median(), "high", "low")`],
      ["Bins / ranges", `df["${num}_bin"] = pd.cut(df[${q(num)}], bins=5).astype(str)`],
      ["Keep only some rows", `df = df[df[${q(num)}] > 0]`],
      ["Remove duplicate rows", `df = df.drop_duplicates()`],
      ["Fill missing values", `df[${q(num)}] = df[${q(num)}].fillna(df[${q(num)}].median())`],
      ["Rename columns", `df = df.rename(columns={${q(num)}: "${num}_new"})`],
      ["Normalise a column to 0–1", `col = ${q(num)}\ndf[col + "_norm"] = (df[col] - df[col].min()) / (df[col].max() - df[col].min())`],
      ["Clean up text", `df[${q(txt)}] = df[${q(txt)}].astype(str).str.strip().str.title()`],
      ["Row-by-row function", `def label(row):\n    if row[${q(num)}] > 10:\n        return "big"\n    return "small"\n\ndf["size"] = df.apply(label, axis=1)`],
      ["Summary statistics (print only)", `print(df.describe(include="all").T)`],
      ["Group statistics (print only)", `print(df.groupby(${q(txt)})[${q(num)}].agg(["count", "mean", "min", "max"]))`],
    ];
    if (t.kind === "attr") ex.unshift(
      ["Area of each polygon (hectares)", `df["area_ha"] = [round(area_ha(g), 3) for g in df["geometry"]]`],
      ["Perimeter / length (metres)", `df["perimeter_m"] = [round(perimeter_m(g), 1) for g in df["geometry"]]`],
      ["Centroid longitude / latitude", `xy = [centroid_xy(g) for g in df["geometry"]]\ndf["lon"] = [p[0] for p in xy]\ndf["lat"] = [p[1] for p in xy]`]);
    return ex;
  }
  function openPython(t) {
    pyx.t = t;
    const isAttr = t.kind === "attr";
    $("#py-vars").innerHTML = `<code>df</code> is the ${isAttr ? "attribute table" : "table"} as a pandas DataFrame (${(t.data?.total ?? 0).toLocaleString()} rows): change it, or assign a new table to <code>df</code>. ` +
      `Available: <code>pd</code>, <code>np</code>, <code>math</code>, <code>re</code>, <code>datetime</code>. Use <code>print(…)</code> to see results.` +
      (isAttr ? ` Geometries: <code>df["geometry"]</code> (shapely, read-only) with <code>area_m2(g)</code>, <code>area_ha(g)</code>, <code>perimeter_m(g)</code>, <code>length_m(g)</code>, <code>centroid_xy(g)</code>. Rows you drop delete those features.` : "");
    const ex = pyExamples(t);
    $("#py-ex").innerHTML = `<option value="">Insert an example…</option>` + ex.map(([k], i) => `<option value="${i}">${esc(k)}</option>`).join("");
    $("#py-ex").onchange = (e) => {
      const it = ex[+e.target.value];
      if (!it) return;
      const ta = $("#py-code");
      ta.value = (ta.value.trim() ? ta.value.replace(/\s*$/, "\n\n") : "") + `# ${it[0]}\n${it[1]}\n`;
      e.target.value = "";
      ta.focus();
    };
    $("#py-code").value = prefs.get("py-code", "") || `# ${ex[0][0]}\n${ex[0][1]}\n`;
    $("#py-out").textContent = ""; $("#py-out").classList.add("hidden");
    $("#py-preview").innerHTML = ""; $("#py-summary").innerHTML = "";
    $("#py-apply").disabled = !t.edit;
    $("#py-apply").title = t.edit ? "" : "Turn on ✎ Edit first";
    $("#dlg-py").showModal();
    setTimeout(() => $("#py-code").focus(), 50);
  }
  $("#py-code").addEventListener("keydown", (e) => {   // Tab indents instead of leaving the editor
    if (e.key !== "Tab") return;
    e.preventDefault();
    const ta = e.target, a = ta.selectionStart, b = ta.selectionEnd;
    ta.value = ta.value.slice(0, a) + "    " + ta.value.slice(b);
    ta.selectionStart = ta.selectionEnd = a + 4;
  });
  async function runPython(apply, btn) {
    const t = pyx.t, code = $("#py-code").value;
    if (!code.trim()) return toast("Write some Python first", true);
    prefs.set("py-code", code);
    const isAttr = t.kind === "attr";
    let body = { code, apply };
    if (isAttr) {
      const l = editLayer(t), { feats, cols } = attrColumns(l);
      body = { code, apply: false, columns: Object.fromEntries(cols.map((c) => [c, feats.map((f) => f.properties?.[c] ?? null)])),
               geometries: feats.map((f) => f.geometry || null), n: feats.length };
    } else {
      if (apply && t.pending?.size) await tableOps(t, [], `Edited ${t.pending.size} cell(s)`, { silent: true });
      body.path = t.path;
    }
    await busy(btn, apply ? "Running…" : "Testing…", async () => {
      let r;
      try {
        const job = await api("/api/python/run", { method: "POST", json: body });
        r = (await trackJob(job, { title: apply ? "Running Python" : "Testing Python" })).result;
      } catch (e) { if (notCancelled(e)) toast(e.message, true); return; }
      const out = $("#py-out");
      out.textContent = r.ok ? (r.output || "(no printed output)") : r.error;
      out.classList.toggle("err", !r.ok);
      out.classList.remove("hidden");
      if (!r.ok) { $("#py-summary").innerHTML = `<span style="color:var(--err)">⚠ The script failed. Nothing was changed.</span>`; $("#py-preview").innerHTML = ""; return; }
      const m = r.meta;
      $("#py-summary").innerHTML = `Rows <b>${m.before.rows.toLocaleString()} → ${m.after.rows.toLocaleString()}</b> · columns ${m.before.columns.length} → ${m.after.columns.length}` +
        (m.added.length ? ` · added <b>${m.added.map(esc).join(", ")}</b>` : "") + (m.removed.length ? ` · removed <b>${m.removed.map(esc).join(", ")}</b>` : "") +
        (apply ? "" : ` · <span class="muted">test only: nothing changed</span>`);
      $("#py-preview").innerHTML = `<div class="load-wrap"><table class="data-table"><tr>${r.preview.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>${r.preview.rows.map((row) => `<tr>${row.map((v) => `<td>${fmtCell(v)}</td>`).join("")}</tr>`).join("")}</table></div>`;
      const lines = code.split("\n").map((ln) => ln.trim()).filter((ln) => ln && !ln.startsWith("#"));
      const label = `Python: ${(lines.find((ln) => /^df(\[|\s*=)/.test(ln)) || lines[0] || "script").slice(0, 70)}${lines.length > 1 ? ` (+${lines.length - 1} line${lines.length > 2 ? "s" : ""})` : ""}`;
      if (!apply) return;
      if (isAttr) {
        const l = editLayer(t), feats = l.geojson.features, res = r.result;
        const newFeats = [], noGeom = res.index.filter((i) => i == null || !feats[i]).length;
        res.index.forEach((fi, k) => {
          if (fi == null || !feats[fi]) return;
          const props = {};
          res.columns.forEach((c, j) => { props[c] = res.rows[k][j]; });
          newFeats.push({ ...feats[fi], properties: props });
        });
        attrSnapshot(t, l, label);
        l.geojson.features = newFeats;
        attrChanged(t, l);
        if (noGeom) toast(`${noGeom} new row(s) have no geometry and were skipped`, true);
      } else {
        t.log = [...(t.log || []), label];
        api("/api/tables/edit/log", { method: "POST", json: { path: t.path, add: [label] } }).catch(() => {});
        t.data = null; t.stats = null; t.sel = new Set();
        await loadTab(t);
      }
      toast("Python applied. Remember to Save.");
    });
  }
  $("#py-test").onclick = (e) => runPython(false, e.currentTarget);
  $("#py-apply").onclick = (e) => runPython(true, e.currentTarget);

  // ---- field calculator dialog (shared by tables and vector layers)
  const fcx = { t: null, fns: null, timer: 0 };
  async function openCalc(t, { mode = "new", column = null, title } = {}) {
    if (!t.data) await loadTab(t);
    fcx.t = t;
    fcx.fns ||= await api("/api/fields/functions").catch(() => ({ functions: {}, geometry: [] }));
    const cols = t.data.columns, isAttr = t.kind === "attr";
    $("#fc-title").textContent = title || "Field calculator";
    $("#fc-name").value = "";
    $("#fc-type").value = "auto";
    $("#fc-col").innerHTML = cols.map((c) => `<option ${c === column ? "selected" : ""}>${esc(c)}</option>`).join("");
    $$('input[name="fct"]').forEach((r) => r.checked = r.value === (cols.length ? mode : "new"));
    $$('input[name="fct"]')[1].disabled = !cols.length;
    const n = t.data.filtered, all = t.data.total;
    $("#fc-only").checked = false;
    $("#fc-only").disabled = !t.q;
    $("#fc-only-n").textContent = t.q ? `${n.toLocaleString()} of ${all.toLocaleString()} rows match “${t.q}”` : "no search / filter active";
    $("#fc-expr").value = mode === "update" && column ? `[${column}]` : "";
    $("#fc-fields").innerHTML = cols.map((c, i) => `<button class="fc-chip" data-ins="[${esc(c)}]" title="${esc(t.data.types[i])}">${esc(c)} <i>${TYPE_TAG[t.data.types[i]] || ""}</i></button>`).join("");
    $("#fc-geom-wrap").classList.toggle("hidden", !isAttr);
    $("#fc-geom").innerHTML = [["$area", "area in m²"], ["$area_ha", "area in hectares"], ["$area_km2", "area in km²"], ["$perimeter", "perimeter in m"],
      ["$length", "line length in m"], ["$x", "centroid longitude"], ["$y", "centroid latitude"], ["$id", "row number"]]
      .map(([g, d]) => `<button class="fc-chip" data-ins="${g}" title="${d}">${g}</button>`).join("");
    $("#fc-funcs").innerHTML = Object.entries(fcx.fns.functions).map(([k, h]) => `<button class="fc-chip" data-ins="${esc(k)}()" title="${esc(h)}">${esc(k)}</button>`).join("");
    $("#fc-examples").innerHTML = (isAttr ? ["round($area_ha, 2)", "$perimeter / 1000", "iif($area_ha > 1, 'large', 'small')"] : [])
      .concat(cols.length >= 2 && t.data.types.filter((x) => x === "number" || x === "integer").length >= 2
        ? [`[${t.data.columns.find((c, i) => t.data.types[i] !== "text")}] * 2`] : [])
      .concat([`upper([${cols.find((c, i) => t.data.types[i] === "text") || cols[0]}])`, `concat([${cols[0]}], " - ", [${cols[cols.length - 1]}])`, `iif([${cols.find((c, i) => t.data.types[i] !== "text") || cols[0]}] > 0, "yes", "no")`])
      .map((e) => `<button class="fc-chip ex" data-ex="${esc(e)}">${esc(e)}</button>`).join("");
    $$("#dlg-calc [data-ins]").forEach((b) => b.onclick = () => insertAtCursor($("#fc-expr"), b.dataset.ins.endsWith("()") ? b.dataset.ins.slice(0, -1) : b.dataset.ins + " "));
    $$("#dlg-calc [data-ex]").forEach((b) => b.onclick = () => { $("#fc-expr").value = b.dataset.ex; calcPreview(); });
    syncCalcTarget();
    $("#dlg-calc").showModal();
    calcPreview();
    setTimeout(() => (mode === "new" ? $("#fc-name") : $("#fc-expr")).focus(), 50);
  }
  function insertAtCursor(ta, text) {
    const a = ta.selectionStart ?? ta.value.length, b = ta.selectionEnd ?? a;
    ta.value = ta.value.slice(0, a) + text + ta.value.slice(b);
    ta.focus(); ta.selectionStart = ta.selectionEnd = a + text.length;
    calcPreview();
  }
  function syncCalcTarget() {
    const isNew = $('input[name="fct"]:checked')?.value === "new";
    $("#fc-name").disabled = !isNew; $("#fc-type").disabled = !isNew; $("#fc-col").disabled = isNew;
    $("#fc-hint").textContent = isNew ? "Leave the expression empty to add an empty field you can fill in by hand." : "";
  }
  $$('input[name="fct"]').forEach((r) => r.onchange = syncCalcTarget);
  $("#fc-expr").oninput = () => calcPreview();
  function attrCalcPayload(l, expr, idx) {
    const { feats, cols } = attrColumns(l);
    const pick = idx || feats.map((_, i) => i);
    const columns = Object.fromEntries(cols.map((c) => [c, pick.map((i) => feats[i].properties?.[c] ?? null)]));
    return { expression: expr, columns, geometries: expr.includes("$") ? pick.map((i) => feats[i].geometry || null) : null, n: pick.length };
  }
  function calcPreview() {
    clearTimeout(fcx.timer);
    fcx.timer = setTimeout(async () => {
      const t = fcx.t, expr = $("#fc-expr").value.trim(), box = $("#fc-preview");
      if (!expr) { box.innerHTML = `<span class="muted">Preview of the first rows appears here.</span>`; return; }
      try {
        let r;
        if (t.kind === "attr") {
          const l = editLayer(t);
          r = await api("/api/fields/calc", { method: "POST", json: attrCalcPayload(l, expr, l.geojson.features.slice(0, 8).map((_, i) => i)) });
        } else r = await api(`/api/tables/calc-preview?path=${encodeURIComponent(t.path)}&expression=${encodeURIComponent(expr)}`);
        box.innerHTML = `<span class="fc-ok">✓ ${esc(r.type)}</span> ${r.values.map((v) => `<code>${v == null ? "–" : esc(typeof v === "number" ? String(+v.toPrecision(8)) : String(v))}</code>`).join(" ")}`;
      } catch (e) { box.innerHTML = `<span style="color:var(--err)">⚠ ${esc(e.message)}</span>`; }
    }, 350);
  }
  $("#fc-apply").onclick = (e) => busy(e.currentTarget, "Calculating…", async () => {
    const t = fcx.t, isNew = $('input[name="fct"]:checked').value === "new";
    const expr = $("#fc-expr").value.trim(), name = isNew ? $("#fc-name").value.trim() : $("#fc-col").value;
    const only = $("#fc-only").checked && t.q;
    if (!name) return toast("Give the new field a name", true);
    if (isNew && t.data.columns.includes(name)) return toast(`There is already a field called ${name}`, true);
    if (!isNew && !expr) return toast("Type an expression", true);
    try {
      if (t.kind === "attr") {
        const l = editLayer(t), { feats, cols, types } = attrColumns(l);
        const idx = only ? attrFilter(feats, cols, types, t.q) : feats.map((_, i) => i);
        let vals = idx.map(() => null), typ = $("#fc-type").value;
        if (expr) { const r = await api("/api/fields/calc", { method: "POST", json: attrCalcPayload(l, expr, idx) }); vals = r.values; if (typ === "auto") typ = r.type; }
        if (typ !== "auto" && expr) vals = vals.map((v) => { try { return coerceVal(v, typ); } catch { return null; } });
        attrSnapshot(t, l, isNew ? `Added field ${name}${expr ? ` = ${expr}` : ""}` : `Calculated ${name} = ${expr}`);
        if (isNew) feats.forEach((f) => { f.properties = { ...(f.properties || {}), [name]: null }; });
        idx.forEach((fi, k) => { feats[fi].properties[name] = vals[k]; });
        attrChanged(t, l);
      } else {
        await tableOps(t, [isNew ? { op: "add_field", name, expression: expr, type: $("#fc-type").value, ...(only ? { q: t.q } : {}) }
                                 : { op: "calc", column: name, expression: expr, ...(only ? { q: t.q } : {}) }],
                       isNew ? `Added field ${name}${expr ? ` = ${expr}` : ""}` : `Calculated ${name} = ${expr}${only ? " (filtered rows)" : ""}`);
      }
      $("#dlg-calc").close();
      if (t.kind === "attr") toast(isNew ? `Field ${name} added` : `${name} updated`);
    } catch (ex) { $("#fc-preview").innerHTML = `<span style="color:var(--err)">⚠ ${esc(ex.message)}</span>`; }
  });

  // clicking a row shows it on the map: the feature (attribute tables) or the lon / lat point (tables)
  function clearRowMarker() { rowMarker?.remove(); rowMarker = null; }
  function rowToMap(t, d, rid, row) {
    clearRowMarker();
    const hl = { color: "#facc15", weight: 4, opacity: 1, fillColor: "#facc15", fillOpacity: 0.18 };
    if (t.kind === "attr") {
      const f = getLayer(t.layerId)?.geojson?.features?.[rid];
      if (!f?.geometry) return;
      rowMarker = L.geoJSON(f, { style: () => hl, pointToLayer: (_, ll) => L.circleMarker(ll, { ...hl, radius: 10 }), interactive: false }).addTo(map);
      const b = rowMarker.getBounds();
      if (b.isValid()) map.fitBounds(b, { padding: [60, 60], maxZoom: Math.max(map.getZoom(), 15) });
    } else if (d.lonlat) {
      const lon = row[d.columns.indexOf(d.lonlat[0])], lat = row[d.columns.indexOf(d.lonlat[1])];
      if (typeof lon !== "number" || typeof lat !== "number" || Math.abs(lat) > 90 || Math.abs(lon) > 180) return;
      rowMarker = L.circleMarker([lat, lon], { ...hl, radius: 10, interactive: false }).addTo(map);
      map.setView([lat, lon], Math.max(map.getZoom(), 14));
    }
  }

  // ---- pictures: zoom (wheel) and pan (drag)
  function renderPictureTab(t, body) {
    const it = t.item;
    body.innerHTML = `<div class="vt-bar">
        <span class="vt-pager"><button data-z="fit" title="Fit to panel">Fit</button><button data-z="1" title="Actual size">1:1</button><button data-z="out" title="Zoom out">−</button><button data-z="in" title="Zoom in">+</button></span>
        <span class="vt-count pic-zoom"></span><span class="muted small">${it.width}×${it.height} px · scroll to zoom, drag to pan</span>
        <span class="grow"></span>
        <button class="btn small primary" data-act="place" title="This picture has no coordinates. Stretch it over the current map view to use it as a layer">Place on map</button>
        <a class="btn small" href="/api/pictures/file?path=${encodeURIComponent(t.path)}" download title="Download">⬇</a></div>
      <div class="pic-stage"><img src="/api/pictures/file?path=${encodeURIComponent(t.path)}" alt="${esc(t.title)}" draggable="false"></div>`;
    const stage = $(".pic-stage", body), img = $("img", stage);
    const apply = () => { img.style.transform = `translate(${t.tx}px, ${t.ty}px) scale(${t.z})`; $(".pic-zoom", body).textContent = `${Math.round(t.z * 100)}%`; };
    const fit = () => {
      const r = stage.getBoundingClientRect(), w = img.naturalWidth || it.width, h = img.naturalHeight || it.height;
      t.z = Math.min(r.width / w, r.height / h, 1) || 1; t.tx = (r.width - w * t.z) / 2; t.ty = (r.height - h * t.z) / 2; apply();
    };
    const zoomAt = (f, cx, cy) => { const nz = Math.min(32, Math.max(0.02, t.z * f)); t.tx = cx - (cx - t.tx) * nz / t.z; t.ty = cy - (cy - t.ty) * nz / t.z; t.z = nz; apply(); };
    if (t.z == null) { if (img.complete) fit(); else img.onload = fit; } else apply();
    stage.onwheel = (e) => { e.preventDefault(); const r = stage.getBoundingClientRect(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top); };
    stage.onpointerdown = (e) => {
      stage.setPointerCapture(e.pointerId); stage.classList.add("panning");
      const sx = e.clientX - t.tx, sy = e.clientY - t.ty;
      stage.onpointermove = (ev) => { t.tx = ev.clientX - sx; t.ty = ev.clientY - sy; apply(); };
      stage.onpointerup = stage.onpointercancel = () => { stage.onpointermove = null; stage.classList.remove("panning"); };
    };
    $$("[data-z]", body).forEach((b) => b.onclick = () => {
      const r = stage.getBoundingClientRect();
      if (b.dataset.z === "fit") fit();
      else if (b.dataset.z === "1") zoomAt(1 / t.z, r.width / 2, r.height / 2);
      else zoomAt(b.dataset.z === "in" ? 1.4 : 1 / 1.4, r.width / 2, r.height / 2);
    });
    $('[data-act="place"]', body).onclick = () => placePicture(it);
  }

  // ------------------------------------------------------------------ folder picker (server-side, so it returns real paths)
  const fp = { path: null, sel: null, mode: "folder", resolve: null };
  const FOLDER_SVG = (isProj) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="${isProj ? "var(--accent)" : "#e3b341"}" fill-opacity="${isProj ? ".9" : ".85"}" stroke="none"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.6l2 2h8.4A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>${isProj ? '<path d="M8 13.5l2.5 2.5L16 11" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' : ""}</svg>`;
  function pickFolder({ title = "Choose a folder", start = "", mode = "folder", okLabel } = {}) {
    fp.mode = mode; fp.sel = null; fp.okLabel = okLabel;
    $("#fp-title").textContent = title;
    $("#fp-newname").value = "";
    $("#dlg-folder").showModal();
    fpLoad(start || prefs.get("fp-last", ""));
    return new Promise((res) => { fp.resolve = res; });
  }
  $("#dlg-folder").addEventListener("close", () => { if (fp.resolve) { fp.resolve(fp.result ?? null); fp.resolve = null; fp.result = null; } });
  async function fpLoad(path) {
    let r;
    try { r = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}`); }
    catch (e) { $("#fp-hint").innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`; if (fp.path) return; r = await api("/api/fs/list"); }
    fp.path = r.path; fp.sel = null; fp.info = r;
    $("#fp-path").value = r.path;
    $("#fp-up").disabled = !r.parent;
    $("#fp-up").onclick = () => r.parent && fpLoad(r.parent);
    $("#fp-shortcuts").innerHTML = r.shortcuts.map((sc) => `<button class="fp-sc ${sc.path === r.path ? "on" : ""}" data-p="${esc(sc.path)}" title="${esc(sc.path)}">${esc(sc.name)}</button>`).join("");
    $$("#fp-shortcuts [data-p]").forEach((b) => b.onclick = () => fpLoad(b.dataset.p));
    $("#fp-list").innerHTML = r.dirs.length ? r.dirs.map((d) => `<div class="fp-item ${d.project ? "is-proj" : ""}" data-p="${esc(d.path)}" title="Double-click to open">
        <span class="fp-ic">${FOLDER_SVG(d.project)}</span><span class="fp-n">${esc(d.name)}</span>${d.project ? '<span class="fp-badge">project</span>' : ""}</div>`).join("")
      : `<p class="hint" style="padding:10px">No sub-folders here.</p>`;
    $$("#fp-list .fp-item").forEach((el) => {
      el.onclick = () => { fp.sel = el.dataset.p; $$("#fp-list .fp-item").forEach((x) => x.classList.toggle("sel", x === el)); fpHint(); };
      el.ondblclick = () => fpLoad(el.dataset.p);
    });
    fpHint();
  }
  function fpTarget() { return fp.sel || fp.path; }
  function fpHint() {
    const t = fpTarget(), isProj = fp.sel ? !!$(`#fp-list .fp-item.sel.is-proj`) : fp.info?.project;
    const ok = $("#fp-ok");
    if (fp.mode === "project") {
      ok.textContent = "Open project"; ok.disabled = !isProj;
      $("#fp-hint").innerHTML = isProj ? `Open <b>${esc(t.split(/[\\/]/).pop())}</b>` : "Choose a folder marked <span class='fp-badge'>project</span>.";
    } else {
      ok.textContent = fp.okLabel || "Select this folder"; ok.disabled = false;
      $("#fp-hint").innerHTML = `<code>${esc(t)}</code>${!fp.sel && fp.info && !fp.info.writable ? ' <span style="color:var(--warn)">(read-only)</span>' : ""}`;
    }
  }
  $("#fp-path").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); fpLoad($("#fp-path").value); } });
  $("#fp-ok").onclick = () => { fp.result = fpTarget(); prefs.set("fp-last", fp.mode === "project" ? fp.path : fp.result); $("#dlg-folder").close(); };
  $("#fp-mkdir").onclick = async () => {
    const name = $("#fp-newname").value.trim();
    if (!name) return toast("Type a name for the new folder", true);
    try { const r = await api("/api/fs/mkdir", { method: "POST", json: { parent: fp.path, name } }); $("#fp-newname").value = ""; await fpLoad(fp.path); fp.sel = r.path; $(`#fp-list [data-p="${CSS.escape(r.path)}"]`)?.classList.add("sel"); fpHint(); }
    catch (e) { toast(e.message, true); }
  };
  $("#fp-newname").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#fp-mkdir").click(); } });

  // ------------------------------------------------------------------ projects: one folder for layers, results and settings
  const proj = { info: null, saveTimer: 0, loading: false };
  const inProject = () => !!proj.info?.project;
  function projectState() {
    const c = map.getCenter();
    return { layers: layers.filter((l) => l.type !== "image").map(({ leaflet, image, busy, error, legend, _original, ...rest }) =>
               _original !== undefined ? { ...rest, geojson: { ...rest.geojson, features: JSON.parse(_original) } } : rest),
             items: dataItems, view: { center: [c.lat, c.lng], zoom: map.getZoom() }, basemap: prefs.get("basemap", "streets") };
  }
  function scheduleProjectSave() {
    if (!inProject() || proj.loading) return;
    clearTimeout(proj.saveTimer);
    $("#project-saved").textContent = "•";
    $("#project-saved").title = "Unsaved changes (saving…)";
    proj.saveTimer = setTimeout(async () => {
      try {
        const r = await api("/api/project/state", { method: "PUT", json: { state: projectState() } });
        $("#project-saved").textContent = "✓";
        $("#project-saved").title = `Saved ${r.saved}`;
      } catch (e) { $("#project-saved").textContent = "!"; $("#project-saved").title = "Couldn't save the project: " + e.message; }
    }, 900);
  }
  function renderProjectChip() {
    const p = proj.info?.project;
    $("#project-name").textContent = p ? p.name : "Temporary workspace";
    $("#btn-project").classList.toggle("temp", !p);
    $("#btn-project").title = p ? `Project folder: ${p.folder}\nEverything you make is saved in this folder.` : `No project open: results are kept in the app's working folder (${proj.info?.workspace || ""}) until you clean them up.`;
    $("#btn-project .pc-ic").innerHTML = p ? "📁" : "🗂";
    if (!p) $("#project-saved").textContent = "";
    $("#mi-project-close").disabled = !p;
    document.title = p ? `${p.name} · LULC Fetch` : "LULC Fetch";
  }
  function clearContents() {
    [...layers].forEach((l) => removeLayer(l.id, { silent: true }));
    dataItems.splice(0);
    [...vw.tabs].forEach((t) => closeTab(t.key, true));
    selectedId = null;
    renderContents(); renderItems();
  }
  // apply a project's saved state (or the temporary workspace's browser-stored state)
  function applyState(state) {
    proj.loading = true;
    try {
      clearContents();
      if (state?.view?.center) map.setView(state.view.center, state.view.zoom ?? map.getZoom());
      if (state?.basemap) setBasemap(state.basemap);
      restoreLayers(state ? state.layers || [] : undefined);
      restoreItems(state ? state.items || [] : undefined);
    } finally { proj.loading = false; }
  }
  async function afterSwitch(info, label) {
    proj.info = info;
    renderProjectChip();
    applyState(info.project ? (info.state || {}) : null);
    refreshJobs();
    if (currentTool !== "home") switchTool(currentTool);
    toast(label);
  }
  async function projectCreate(name, folder) {
    const info = await api("/api/project/new", { method: "POST", json: { name, folder } });
    prefs.set("pj-parent", folder);
    await afterSwitch(info, `Project “${info.project.name}” created in ${info.project.folder}`);
  }
  async function projectOpen(folder) {
    const info = await api("/api/project/open", { method: "POST", json: { folder } });
    await afterSwitch(info, `Opened project “${info.project.name}”`);
  }
  async function projectClose() {
    if (!inProject()) return;
    clearTimeout(proj.saveTimer);
    await api("/api/project/state", { method: "PUT", json: { state: projectState() } }).catch(() => {});
    const info = await api("/api/project/close", { method: "POST" });
    await afterSwitch(info, "Project closed. Back to the temporary workspace.");
  }
  async function showProjectDialog() {
    try { proj.info = await api("/api/project"); } catch {}
    $("#pj-show").checked = prefs.get("pj-show", true);
    if (!$("#pj-folder").value) $("#pj-folder").value = prefs.get("pj-parent", "");
    $("#pj-error").classList.add("hidden");
    const rec = proj.info?.recent || [];
    $("#pj-recent").innerHTML = rec.length ? rec.map((r) => `<div class="pj-item ${r.exists ? "" : "missing"}" data-f="${esc(r.folder)}" title="${esc(r.folder)}">
        <span class="pj-ic">📁</span><span class="pj-t"><b>${esc(r.name)}</b><small>${esc(r.folder)}</small><small>${r.exists ? `opened ${esc(r.opened || "")}` : "folder not found"}</small></span>
        <button class="vtab-x" data-forget title="Remove from the list">×</button></div>`).join("")
      : '<p class="hint">No recent projects yet.</p>';
    $$("#pj-recent .pj-item").forEach((el) => {
      el.onclick = async (e) => {
        if (e.target.closest("[data-forget]")) { e.stopPropagation(); await api(`/api/project/recent?folder=${encodeURIComponent(el.dataset.f)}`, { method: "DELETE" }); el.remove(); return; }
        if (el.classList.contains("missing")) return toast("That project folder no longer exists", true);
        try { await projectOpen(el.dataset.f); $("#dlg-project").close(); } catch (err) { toast(err.message, true); }
      };
    });
    updatePjPreview();
    if (!$("#dlg-project").open) $("#dlg-project").showModal();
    setTimeout(() => $("#pj-name").focus(), 50);
  }
  function updatePjPreview() {
    const n = $("#pj-name").value.trim(), f = $("#pj-folder").value.trim();
    $("#pj-preview").innerHTML = n && f ? `Will be created as <code>${esc(f.replace(/\/+$/, ""))}/${esc(n.replace(/[<>:"/\\|?*]+/g, "_"))}</code>` : "A new folder with the project's name is created inside the location.";
  }
  $("#pj-name").oninput = $("#pj-folder").oninput = updatePjPreview;
  $("#pj-browse").onclick = async () => { const f = await pickFolder({ title: "Where should the project folder be created?", start: $("#pj-folder").value, okLabel: "Use this location" }); if (f) { $("#pj-folder").value = f; updatePjPreview(); } };
  $("#pj-create").onclick = (e) => busy(e.currentTarget, "Creating…", async () => {
    const name = $("#pj-name").value.trim(), folder = $("#pj-folder").value.trim();
    const err = $("#pj-error"); err.classList.add("hidden");
    if (!name) { err.textContent = "Give the project a name"; err.classList.remove("hidden"); return; }
    if (!folder) { err.textContent = "Choose where to create it (Browse…)"; err.classList.remove("hidden"); return; }
    try { await projectCreate(name, folder); $("#dlg-project").close(); }
    catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
  });
  $("#pj-name").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#pj-create").click(); } });
  $("#pj-open").onclick = async () => {
    const f = await pickFolder({ title: "Open a project folder", mode: "project" });
    if (!f) return;
    try { await projectOpen(f); $("#dlg-project").close(); } catch (e) { toast(e.message, true); }
  };
  $("#pj-temp").onclick = () => $("#dlg-project").close();
  $("#pj-show").onchange = (e) => prefs.set("pj-show", e.target.checked);
  $("#btn-project").onclick = (e) => {
    e.stopPropagation();   // the page-wide "click outside closes menus" handler would close it at once
    if (!inProject()) return showProjectDialog();
    const r = e.currentTarget.getBoundingClientRect(), p = proj.info.project;
    showMenu(p.folder, [
      ["Show project folder", () => api("/api/project/reveal", { method: "POST", json: {} }).catch((x) => toast(x.message, true))],
      ["Save now", () => { scheduleProjectSave(); }],
      "-",
      ["Open another project…", () => showProjectDialog()],
      ["Close project", () => projectClose().catch((x) => toast(x.message, true))],
      "-",
      ["Clean up working files…", () => openCacheDialog()],
    ], r.left, r.bottom + 4);
  };
  async function initProject() {
    try { proj.info = await api("/api/project"); } catch { proj.info = null; }
    renderProjectChip();
    if (inProject()) applyState(proj.info.state || {});
    else {
      applyState(null);
      let shown = false;
      try { shown = sessionStorage.getItem("pj-asked") === "1"; sessionStorage.setItem("pj-asked", "1"); } catch {}
      if (prefs.get("pj-show", true) && !shown) showProjectDialog();
    }
  }
  map.on("moveend", () => scheduleProjectSave());

  // ------------------------------------------------------------------ clean up working files (the cache)
  async function openCacheDialog() {
    const r = await api("/api/cache");
    $("#cc-where").innerHTML = r.temporary ? `Temporary workspace: <code>${esc(r.workspace)}</code>. Tool results are kept here until you delete them.`
      : `Project folder: <code>${esc(r.workspace)}</code>. These are the project's working files.`;
    $("#cc-list").innerHTML = r.folders.map((f) => `<label class="ws-row" style="margin:0"><span><input type="checkbox" value="${f.folder}" ${f.files && f.folder !== "imports" ? "checked" : ""} ${f.files ? "" : "disabled"}> <b>${esc(f.folder)}</b>
      <small>${f.files.toLocaleString()} files · ${fmt(f.size_mb, f.size_mb < 10 ? 1 : 0)} MB${f.oldest_days != null ? ` · oldest ${Math.round(f.oldest_days)} day(s)` : ""}</small></span></label>`).join("");
    $("#dlg-cache").showModal();
  }
  $("#cc-go").onclick = (e) => busy(e.currentTarget, "Deleting…", async () => {
    const folders = $$("#cc-list input:checked").map((i) => i.value), days = +$("#cc-age").value;
    if (!folders.length) return toast("Tick at least one folder", true);
    if (!confirm(`Delete ${days ? `files older than ${days} day(s)` : "ALL files"} in ${folders.join(", ")}? This can't be undone.`)) return;
    const r = await api("/api/cache/clean", { method: "POST", json: { folders, older_than_days: days } });
    toast(`Removed ${r.removed} item(s), freed ${fmt(r.freed_mb, 1)} MB`);
    openCacheDialog();
  });

  // ------------------------------------------------------------------ "also save to a folder on my computer" (every tool)
  const SAVE_SPOTS = [  // [key, element the option goes before, what is saved]
    ["search", "#dl-go", "the downloaded files"], ["analyze", "#ex-go", "the exported GeoTIFF"], ["pca", "#pca-run", "the result GeoTIFF"],
    ["stack", "#st-run", "the stacked GeoTIFF"], ["raster2table", "#rt-run", "the table"], ["train", "#mt-run", "the model and its evaluation report"],
    ["predict", "#mp-run", "the map"], ["rasterml", "#rm-run", "the classified map and the model (with its evaluation report)"], ["cluster", "#uc-run", "the table with clusters (and the model)"], ["tsne", "#ut-run", "the table with map coordinates"],
  ];
  function saveToHtml(key, what, label = "Also save to a folder on my computer") {
    const dir = prefs.get(`save-dir:${key}`, key === "train" ? prefs.get("report-dir", "") : "") || prefs.get("save-dir:last", "");
    return `<div class="save-to" data-save="${key}">
      <label class="inline" style="margin:0"><input type="checkbox" data-save-on ${prefs.get(`save-on:${key}`, false) ? "checked" : ""}> ${esc(label)}
        <button type="button" class="tip" data-tip="The result always goes into the project folder (or, without a project, the app's working folder) and appears in Contents. Tick this to also save a copy of ${what} in a folder you choose. Existing files are never overwritten." aria-label="Help">i</button></label>
      <div class="save-to-row ${prefs.get(`save-on:${key}`, false) ? "" : "hidden"}"><input data-save-dir placeholder="Folder, e.g. ~/Documents/LULC results" value="${esc(dir)}" spellcheck="false" autocomplete="off"><button class="btn small" data-browse>Browse…</button></div>
      <div class="save-note hidden"></div></div>`;
  }
  function wireSaveTo(w) {
    const key = w.dataset.save;
    $("[data-save-on]", w).onchange = (e) => { prefs.set(`save-on:${key}`, e.target.checked); $(".save-to-row", w).classList.toggle("hidden", !e.target.checked); };
    $("[data-save-dir]", w).onchange = (e) => { prefs.set(`save-dir:${key}`, e.target.value.trim()); prefs.set("save-dir:last", e.target.value.trim()); };
    $("[data-browse]", w).onclick = async () => {
      const f = await pickFolder({ title: "Save results in…", start: $("[data-save-dir]", w).value });
      if (f) { $("[data-save-dir]", w).value = f; prefs.set(`save-dir:${key}`, f); prefs.set("save-dir:last", f); }
    };
  }
  SAVE_SPOTS.forEach(([key, sel, what]) => {
    const anchor = $(sel);
    if (!anchor) return;
    const foot = anchor.closest(".modal-foot");
    if (foot) {   // dialogs: at the end of the dialog's body, not between its buttons
      const body = foot.parentElement.querySelector(".modal-body");
      body.insertAdjacentHTML("beforeend", saveToHtml(key, what));
      wireSaveTo(body.lastElementChild);
      return;
    }
    const host = key === "train" ? anchor.closest(".row") : anchor;   // train: above the Train / Compare buttons
    host.insertAdjacentHTML("beforebegin", saveToHtml(key, what));
    wireSaveTo(host.previousElementSibling);
  });
  // Export tool: save straight into a folder instead of a browser download
  $("#lx-go").closest(".row").insertAdjacentHTML("beforebegin", saveToHtml("export", "the exported file", "Save to a folder instead of downloading"));
  wireSaveTo($("#lx-go").closest(".row").previousElementSibling);
  const exportFolder = () => { const w = $('[data-save="export"]'); return w && $("[data-save-on]", w).checked ? $("[data-save-dir]", w).value.trim() : ""; };
  const OUT_KEYS = ["path", "output_table", "files", "outputs"];
  function outputPaths(job) {
    const r = job.result || {}, out = [];
    for (const k of OUT_KEYS) {
      const v = r[k];
      (Array.isArray(v) ? v : [v]).forEach((x) => { if (typeof x === "string" && x && !x.startsWith("/api/")) out.push(x); });
    }
    if (!out.length && job.files?.length) job.files.filter((f) => !/\.(part|log)$/i.test(f)).forEach((f) => out.push(`downloads/${job.id}/${f}`));
    return [...new Set(out)];
  }
  async function saveOutputs(key, job) {
    const w = $(`[data-save="${key}"]`);
    if (!w || !$("[data-save-on]", w).checked) return;
    const folder = $("[data-save-dir]", w).value.trim(), note = $(".save-note", w);
    if (!folder) { toast("The result was kept, but no folder was chosen to save a copy in", true); return; }
    const paths = outputPaths(job);
    if (!paths.length) return;
    try {
      const r = await api("/api/files/save", { method: "POST", json: { paths, folder } });
      note.innerHTML = `✓ Saved ${r.saved.length} file${r.saved.length === 1 ? "" : "s"} to <code title="${esc(r.saved.join("\n"))}">${esc(r.folder)}</code> · <a href="#" data-reveal>Show in folder</a>${r.skipped.length ? ` · <span style="color:var(--warn)">${r.skipped.length} skipped</span>` : ""}`;
      note.classList.remove("hidden");
      $("[data-reveal]", note).onclick = (e) => { e.preventDefault(); api("/api/project/reveal", { method: "POST", json: { path: r.saved[0] || r.folder } }).catch((x) => toast(x.message, true)); };
      toast(`Saved a copy in ${r.folder}`);
    } catch (e) { toast(`Couldn't save the copy: ${e.message}`, true); }
  }

  // ------------------------------------------------------------------ Save to folder… (right-click in Contents)
  async function saveLayerToFolder(l) {
    const folder = await pickFolder({ title: `Save “${l.name}” in…`, start: prefs.get("save-dir:last", ""), okLabel: "Save here" });
    if (!folder) return;
    prefs.set("save-dir:last", folder);
    const name = safeName(l.name);
    try {
      let r;
      if (l.type === "vector") {
        r = await api("/api/vector/export", { method: "POST", json: { geojson: l.geojson, format: "shp", name, folder } });
      } else if (l.type === "raster") {
        const asShown = l.render?.index || l.render?.formula;   // index / formula layers are saved as their computed values
        const body = { path: l.path, format: "tif", name, folder, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0, ...(asShown ? l.render : {}) };
        const job = await api("/api/layers/export", { method: "POST", json: body });
        r = (await trackJob(job, { title: `Saving ${l.name}` })).result;
      } else return toast("This layer can't be saved as a file", true);
      toast(`Saved ${r.saved?.map((p) => p.split(/[\\/]/).pop()).join(", ")} in ${r.saved_to}`);
      status(`Saved ${l.name} in ${r.saved_to}`);
    } catch (e) { if (notCancelled(e)) toast(e.message, true); }
  }
  async function saveItemToFolder(it) {
    const folder = await pickFolder({ title: `Save “${it.name}” in…`, start: prefs.get("save-dir:last", ""), okLabel: "Save here" });
    if (!folder) return;
    prefs.set("save-dir:last", folder);
    try {
      const r = await api("/api/files/save", { method: "POST", json: { paths: [it.path], folder } });
      toast(r.saved.length ? `Saved ${r.saved.map((p) => p.split(/[\\/]/).pop()).join(", ")} in ${r.folder}` : `Not saved: ${r.skipped[0]?.reason}`, !r.saved.length);
    } catch (e) { toast(e.message, true); }
  }

  // ------------------------------------------------------------------ add data
  const TABLE_RE = /\.(csv|tsv|txt|parquet|xlsx|xlsm)$/i, PIC_RE = /\.(jpe?g|png|bmp|gif|webp)$/i,
        WORLD_RE = /\.(jgw|jpgw|jpegw|pgw|pngw|bpw|bmpw|gfw|gifw|wld)$/i;
  const stemOf = (n) => n.replace(/(\.aux\.xml|\.[^.]+)$/i, "").toLowerCase();
  async function addFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    const rasters = files.filter((f) => /\.tiff?$/i.test(f.name));
    const tables = files.filter((f) => TABLE_RE.test(f.name));
    const pics = files.filter((f) => PIC_RE.test(f.name));
    // world files / .prj / .aux.xml that belong to a picture (same name, or the only picture)
    const picSide = (pic) => files.filter((f) => (WORLD_RE.test(f.name) || /\.(prj|aux\.xml)$/i.test(f.name)) &&
      (stemOf(f.name) === stemOf(pic.name) || stemOf(f.name) === pic.name.toLowerCase() || (pics.length === 1 && WORLD_RE.test(f.name))));
    const used = new Set([...rasters, ...tables, ...pics, ...pics.flatMap(picSide)]);
    const shpParts = files.filter((f) => !used.has(f) && /\.(shp|shx|dbf|prj|cpg)$/i.test(f.name));
    const others = files.filter((f) => !used.has(f) && !shpParts.includes(f) && !WORLD_RE.test(f.name));
    status(`Adding ${files.length} file${files.length > 1 ? "s" : ""}…`, true);
    for (const f of rasters) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const r = await api("/api/rasters/upload", { method: "POST", body: fd });
        await addRasterFromPath(r.path, { name: f.name });
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    for (const f of pics) {
      try {
        const fd = new FormData();
        [f, ...picSide(f)].forEach((x) => fd.append("files", x));
        const r = await api("/api/pictures/upload", { method: "POST", body: fd });
        if (r.kind === "raster") {
          await addRasterFromPath(r.path, { name: f.name });
          if (r.crs_guessed) toast(`${f.name}: no .prj file, so longitude / latitude (WGS 84) was assumed`);
        } else {
          addItem({ kind: "picture", name: f.name, path: r.path, width: r.width, height: r.height, bands: r.bands }, { open: true });
          toast(`${f.name} has no coordinates (${r.reason}). It opened in the data viewer; use "Place on map" to put it on the map.`);
        }
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    for (const f of tables) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const r = await api("/api/tables/upload", { method: "POST", body: fd });
        addItem({ kind: "table", name: r.name, path: r.path }, { open: true });
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
    const [list, tables] = await Promise.all([api("/api/rasters"), api("/api/tables").catch(() => [])]);
    const groups = {};
    list.forEach((r) => (groups[r.group] ||= []).push(r));
    $("#ws-list").innerHTML = list.length ? Object.entries(groups).map(([g, rs]) => `<div class="ws-group">${esc(g)}</div>` + rs.map((r) => {
      const inMap = layers.some((l) => l.path === r.path && !l.derived);
      return `<div class="ws-row"><span>${esc(r.name)}<small>${esc(r.path)} · ${r.size_mb < 1 ? "<1" : Math.round(r.size_mb)} MB</small></span>
        <button class="btn small ${inMap ? "" : "primary"}" data-add="${esc(r.path)}">${inMap ? "Add again" : "Add"}</button></div>`;
    }).join("")).join("") : '<p class="hint">No GeoTIFFs in the workspace yet.</p>';
    if (tables.length) $("#ws-list").innerHTML += `<div class="ws-group">Tables</div>` + tables.map((t) => {
      const inC = dataItems.some((d) => d.path === t.path);
      return `<div class="ws-row"><span>${esc(t.name)}<small>${esc(t.path)}${t.rows != null ? ` · ${t.rows.toLocaleString()} rows` : ""} · ${t.size_mb < 1 ? "<1" : Math.round(t.size_mb)} MB</small></span>
        <button class="btn small ${inC ? "" : "primary"}" data-addt="${esc(t.path)}">${inC ? "Open" : "Add"}</button></div>`;
    }).join("");
    $$("#ws-list [data-addt]").forEach((b) => b.onclick = () => { addItem({ kind: "table", name: b.dataset.addt.split(/[\\/]/).pop(), path: b.dataset.addt }, { open: true }); b.textContent = "Added ✓"; });
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
        const folder = exportFolder() || null;
        if ($('[data-save="export"] [data-save-on]').checked && !folder) return err("Choose the folder to save in (Browse…), or untick “Save to a folder”.");
        if (l.type === "vector") {
          r = await api("/api/vector/export", { method: "POST", json: { geojson: l.geojson, format: fmtSel, name, clip, folder } });
        } else {
          const body = { path: l.path, format: fmtSel, name, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0, ...l.render, clip, folder };
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
        if (r.saved) {
          const note = $('[data-save="export"] .save-note');
          note.innerHTML = `✓ Saved ${esc(r.saved.map((p) => p.split(/[\\/]/).pop()).join(", "))} in <code>${esc(r.saved_to)}</code> · <a href="#" data-reveal>Show in folder</a>`;
          note.classList.remove("hidden");
          $("[data-reveal]", note).onclick = (ev) => { ev.preventDefault(); api("/api/project/reveal", { method: "POST", json: { path: r.saved[0] } }).catch((x) => toast(x.message, true)); };
          toast(`Saved in ${r.saved_to}`);
          return;
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
    "rt-area": { what: "image is converted", onChange: () => updateRtEstimate() },
    "mp-area": { what: "image is classified", onChange: () => {} },
    "rm-area": { what: "image is used", onChange: () => {} },
    "st-area": { what: "reference extent is used", onChange: () => {} },
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
    if (l.type === "vector") { l.color = $("#lp-color").value; l.leaflet?.setStyle((f) => vecStyle(l, f)); }
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
      <tr><td><kbd>Ctrl/⌘ 3</kbd></td><td>Show / hide the data viewer (tables under the map)</td></tr>
      <tr><td>Drag a panel edge</td><td>Resize Contents, the tool panel or the data viewer (double-click the edge to reset)</td></tr>
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
    else if (mod && e.key === "3") { e.preventDefault(); runCmd("toggle-viewer"); }
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
  function startDraw(Kind, onDone = null, color = null) {
    activeDraw?.disable();
    drawDone = onDone;
    const shape = color ? { color, weight: 2, fillOpacity: 0.3 } : { color: "#dc2626", weight: 2, dashArray: "6 4", fillOpacity: 0.05 };
    activeDraw = new Kind(map, onDone ? { shapeOptions: shape, showArea: false, color, fillColor: color, fillOpacity: 0.8, radius: 6 } : drawOpts);
    activeDraw.enable();
    $("#map-hint").textContent = Kind === L.Draw.Rectangle ? "Drag on the map to draw a rectangle (Esc to stop)"
      : Kind === L.Draw.CircleMarker ? "Click the map to place a point (Esc to stop)" : "Click to add points, click the first point to finish (Esc to stop)";
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
        const done = await trackJob(job, { tool: "search", save: "search" });
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
      // tools that add their own results (PCA, exports, tables, training, classification) are skipped here
      if (j.status !== "done" || addedJobs.has(j.id) || ["pca", "export", "table", "train", "predict", "stack", "compare", "cluster", "tsne", "rasterml", "python"].includes(j.kind)) continue;
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
      const done = await trackJob(job, { tool: "analyze", save: "analyze" });
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
      const j = await trackJob(job, { tool: "pca", save: "pca", title: `${m.title} · ${l.name}` });
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

  // ------------------------------------------------------------------ Classical ML (tabular data) hub
  // Sub-tools are registered here; each has a <div id="ml-sub-<id>" class="ml-sub"> in index.html.
  const ML_SUBTOOLS = [  // ← add sub-tools here (and a <div id="ml-sub-<id>" class="ml-sub"> in index.html)
    { id: "train", group: "sup", title: "Train a model", icon: "ml",
      subtitle: "Random Forest, XGBoost, LightGBM, SVM, Maximum Likelihood and more, with accuracy assessment" },
    { id: "predict", group: "sup", title: "Classify an image", icon: "analyze",
      subtitle: "Apply a trained model (or a saved clustering) to a raster to make a land-cover (or value) map" },
    { id: "cluster", group: "unsup", title: "Clustering", icon: "cluster",
      subtitle: "K-means, hierarchical, DBSCAN, HDBSCAN, spectral clustering, Gaussian mixture: find natural groups" },
    { id: "tsne", group: "unsup", title: "t-SNE map", icon: "tsne",
      subtitle: "See your data as a 2D map where similar rows lie together; colour it by label or cluster" },
  ];
  let mlSub = null;
  function openMlSub(id) {
    mlSub = id;
    $("#ml-home").classList.toggle("hidden", !!id);
    $$(".ml-sub").forEach((d) => d.classList.toggle("hidden", d.id !== `ml-sub-${id}`));
    const st = ML_SUBTOOLS.find((t) => t.id === id);
    $("#tool-sub").textContent = st ? st.title + " · " + st.subtitle : TOOLS.find((t) => t.id === "ml").subtitle;
    if (!id) { refreshTables(); refreshModels(); }
    if (id === "train" && mlx.schema) refreshTrainTables();
    if (id === "predict") refreshPredict();
    if (id === "cluster" || id === "tsne") refreshUnsup(id === "cluster" ? "uc" : "ut").catch((e) => toast(e.message, true));
    document.querySelector(".tool-body").scrollTop = 0;
  }
  function renderMlHub() {
    const card = (t) => `<button class="tool-card" data-mlsub="${t.id}">
      <span class="ic">${svg(t.icon)}</span><span><b>${esc(t.title)}</b><small>${esc(t.subtitle)}</small></span></button>`;
    $("#ml-sup").innerHTML = ML_SUBTOOLS.filter((t) => t.group === "sup").map(card).join("");
    $("#ml-unsup").innerHTML = ML_SUBTOOLS.filter((t) => t.group === "unsup").map(card).join("");
    $(".ml-group-ic.sup").innerHTML = svg("ml");
    $(".ml-group-ic.unsup").innerHTML = svg("cluster");
    $$("#ml-home [data-mlsub]").forEach((b) => b.onclick = () => openMlSub(b.dataset.mlsub));
    $$(".ml-back").forEach((b) => b.onclick = () => openMlSub(null));
    $("#ml-goto-rt").onclick = (e) => { e.preventDefault(); switchTool("raster2table"); };
  }
  async function refreshTables() {
    let list = [];
    try { list = await api("/api/tables"); } catch { return; }
    $("#ml-tables").innerHTML = list.length ? list.map((t) => `<div class="ws-row"><span>${esc(t.name)}
        <small>${t.rows != null ? `${t.rows.toLocaleString()} rows × ${t.columns.length} columns` : ""}${t.target ? ` · label: ${esc(t.target)}` : ""}${t.source ? ` · from ${esc(t.source)}` : ""} · ${fmt(t.size_mb, t.size_mb < 1 ? 2 : 1)} MB</small></span>
        <span class="row tight"><button class="btn small" data-tprev="${esc(t.path)}">Preview</button><a class="btn small" href="/api/tables/file?path=${encodeURIComponent(t.path)}" download>⬇</a><button class="btn small danger" data-tdel="${esc(t.path)}" title="Delete">×</button></span></div>`).join("")
      : '<p class="hint">No tables yet. Use <b>Raster → table</b> to create one.</p>';
    $$("[data-tprev]").forEach((b) => b.onclick = () => previewTable(b.dataset.tprev));
    $$("[data-tdel]").forEach((b) => b.onclick = async () => {
      if (!confirm("Delete this table file?")) return;
      await api(`/api/tables?path=${encodeURIComponent(b.dataset.tdel)}`, { method: "DELETE" });
      refreshTables();
    });
  }
  function tableHtml(columns, rows, labelCols = []) {
    const isLbl = (c) => labelCols.includes(c);
    return `<table class="data-table"><tr>${columns.map((c) => `<th class="${isLbl(c) ? "lbl" : ""}">${esc(c)}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr>${r.map((v, i) => `<td class="${isLbl(columns[i]) ? "lbl" : ""}">${v == null || v === "" ? "–" : esc(v)}</td>`).join("")}</tr>`).join("")}</table>`;
  }
  function previewTable(path) {  // tables open in the data viewer under the map (paging, sorting, search, statistics)
    addItem({ kind: "table", name: path.split(/[\\/]/).pop(), path }, { open: true });
  }


  // ---------------- Raster → table
  const rt = { layer: null };
  function refreshRtInputs() {
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    const sel = $("#rt-input");
    sel.innerHTML = `<option value="">${rasters.length ? "Choose a raster layer…" : "No raster layers yet"}</option>` +
      rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
    if (rt.layer && rasters.includes(rt.layer)) sel.value = rt.layer.id; else { rt.layer = null; renderRt(); }
    // ground truth: any other raster, or any vector layer
    const gtSel = $("#rt-gt"), cur = gtSel.value;
    const vecs = layers.filter((l) => l.type === "vector");
    gtSel.innerHTML = `<option value="">None. Just the image values</option>` +
      (rasters.length ? `<optgroup label="Raster (e.g. land-cover map)">${rasters.filter((l) => l !== rt.layer).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("")}</optgroup>` : "") +
      (vecs.length ? `<optgroup label="Vector (shapefile / GeoJSON / KML)">${vecs.map((l) => `<option value="${esc(l.id)}">${esc(l.name)} (${l.geojson.features.length} features)</option>`).join("")}</optgroup>` : "");
    gtSel.value = [...gtSel.options].some((o) => o.value === cur) ? cur : "";
    renderGt();
  }
  function renderRt() {
    const l = rt.layer;
    if (!l) { $("#rt-bands").innerHTML = ""; $("#rt-info").textContent = ""; updateRtEstimate(); return; }
    const info = l.info;
    $("#rt-info").textContent = `${info.count} bands · ${info.width.toLocaleString()} × ${info.height.toLocaleString()} px · pixel ${fmt(info.res[0], info.res[0] < 1 ? 3 : 1)} ${/4326/.test(info.crs) ? "°" : "m"} · ${info.crs}`;
    $("#rt-bands").innerHTML = info.bands.map((b) => `<label title="Band ${b.index}: ${esc(b.description)}"><input type="checkbox" value="${b.index}" checked>${esc(b.description !== `Band ${b.index}` ? b.description : String(b.index))}</label>`).join("");
    $$("#rt-bands input").forEach((i) => i.onchange = updateRtEstimate);
    const identity = (l.scale ?? 1) === 1 && (l.offset ?? 0) === 0;
    $("#rt-refl").checked = !identity;
    $("#rt-refl").disabled = identity;
    const r0 = info.res[0], unit = /4326/.test(info.crs) ? "°" : " m";
    $("#rt-factor").innerHTML = [1, 2, 4, 8, 16].map((f) => `<option value="${f}">${f === 1 ? `Native (${fmt(r0, r0 < 1 ? 3 : 0)}${unit}): one row per pixel` : `${f}× coarser (${fmt(r0 * f, r0 < 1 ? 3 : 0)}${unit})`}</option>`).join("");
    $("#rt-name").value = safeName(l.name.replace(/\.(tiff?|vrt)$/i, "")).slice(0, 50);
    updateRtEstimate();
  }
  function renderGt() {
    const l = getLayer($("#rt-gt").value);
    $("#rt-gt-raster").classList.toggle("hidden", l?.type !== "raster");
    $("#rt-gt-vector").classList.toggle("hidden", l?.type !== "vector");
    $("#rt-gt-opts").classList.toggle("hidden", !l);
    $("#rt-strat-opt").classList.toggle("disabled", !l);
    if (!l && $('input[name="rts"]:checked').value === "stratified") $('input[name="rts"][value="all"]').checked = true;
    if (l?.type === "raster") {
      $("#rt-gt-band").innerHTML = l.info.bands.map((b) => `<option value="${b.index}">Band ${b.index}${b.description !== `Band ${b.index}` ? " · " + esc(b.description) : ""}</option>`).join("");
    } else if (l?.type === "vector") {
      const keys = [...new Set(l.geojson.features.flatMap((f) => Object.keys(f.properties || {})))];
      const pref = keys.find((k) => /^(class|label|lulc|lc|landcover|type|category|code|id)$/i.test(k)) || keys[0];
      $("#rt-gt-field").innerHTML = keys.length ? keys.map((k) => `<option ${k === pref ? "selected" : ""}>${esc(k)}</option>`).join("")
        : `<option value="">(no attributes: every feature = 1)</option>`;
      showGtClasses();
    }
    updateRtEstimate();
  }
  function showGtClasses() {
    const l = getLayer($("#rt-gt").value), field = $("#rt-gt-field").value;
    if (l?.type !== "vector") return;
    const vals = l.geojson.features.map((f) => (f.properties || {})[field]).filter((v) => v != null);
    const uniq = [...new Set(vals.map(String))];
    $("#rt-gt-classes").textContent = !field ? "" : `${uniq.length} distinct value${uniq.length === 1 ? "" : "s"}: ${uniq.slice(0, 8).join(", ")}${uniq.length > 8 ? " …" : ""}` +
      (vals.every((v) => typeof v === "number") ? " (numeric)" : " (text: a numeric code column is added too)");
  }
  function updateRtEstimate() {
    const l = rt.layer, est = $("#rt-est");
    if (!l) { est.textContent = "Choose an image to see how big the table will be."; return; }
    const f = +$("#rt-factor").value || 1, nb = $$("#rt-bands input:checked").length;
    let px = l.info.width * l.info.height;
    const clip = getClip("rt-area");
    if (clip && !/4326/.test(l.info.crs)) px = Math.min(px, geomArea(clip) / (l.info.res[0] * l.info.res[1]));
    px /= f * f;
    const samp = $('input[name="rts"]:checked').value;
    const gt = !!getLayer($("#rt-gt").value);
    let rows = samp === "random" ? Math.min(px, +$("#rt-n").value || 0) : px;
    const cols = nb + ($("#rt-xy").checked ? 2 : 0) + ($("#rt-ll").checked ? 2 : 0) + ($("#rt-rc").checked ? 2 : 0) + (gt ? 1 : 0);
    const bytes = rows * cols * ($("#rt-fmt").value === "csv" ? 9 : 3.5);
    const note = samp === "stratified" ? `up to ${(+$("#rt-pc").value || 0).toLocaleString()} rows per class` :
      gt && $("#rt-labelled").checked && samp === "all" ? `only labelled pixels (at most ${Math.round(rows).toLocaleString()} rows)` :
      `≈ ${Math.round(rows).toLocaleString()} rows`;
    est.innerHTML = `${note} × ${cols} columns${samp !== "stratified" ? ` · up to ~${bytes > 1e9 ? fmt(bytes / 1e9, 1) + " GB" : Math.max(1, Math.round(bytes / 1e6)) + " MB"}` : ""}` +
      (rows > 5e6 && samp === "all" && !(gt && $("#rt-labelled").checked) ? ` <span style="color:var(--warn)">· large: consider an area, a coarser pixel size, or a random sample</span>` : "");
  }
  $("#rt-input").onchange = () => { rt.layer = getLayer($("#rt-input").value); refreshRtInputs(); renderRt(); if (rt.layer) selectLayer(rt.layer.id); };
  $("#rt-bands-all").onclick = () => { $$("#rt-bands input").forEach((i) => i.checked = true); updateRtEstimate(); };
  $("#rt-bands-none").onclick = () => { $$("#rt-bands input").forEach((i) => i.checked = false); updateRtEstimate(); };
  $("#rt-gt").onchange = renderGt;
  $("#rt-gt-field").onchange = showGtClasses;
  ["#rt-factor", "#rt-fmt", "#rt-n", "#rt-pc", "#rt-xy", "#rt-ll", "#rt-rc", "#rt-labelled"].forEach((s) => $(s).addEventListener("input", updateRtEstimate));
  $$('input[name="rts"]').forEach((r) => r.onchange = updateRtEstimate);

  $("#rt-run").onclick = async () => {
    const l = rt.layer, err = $("#rt-error");
    err.classList.add("hidden");
    if (!l) return toast("Choose an image first (it is required)", true);
    const bands = $$("#rt-bands input:checked").map((i) => +i.value);
    if (!bands.length) return toast("Select at least one band", true);
    const gl = getLayer($("#rt-gt").value);
    let ground_truth = null;
    if (gl?.type === "raster") ground_truth = { type: "raster", path: gl.path, band: +$("#rt-gt-band").value || 1 };
    else if (gl?.type === "vector") ground_truth = { type: "vector", geojson: gl.geojson, field: $("#rt-gt-field").value || null };
    const refl = $("#rt-refl").checked;
    const body = {
      path: l.path, bands, clip: getClip("rt-area"), factor: +$("#rt-factor").value || 1,
      scale: refl ? (l.scale ?? 1) : 1, offset: refl ? (l.offset ?? 0) : 0,
      ground_truth, label_name: $("#rt-label").value || "label", labelled_only: $("#rt-labelled").checked,
      sampling: $('input[name="rts"]:checked').value, sample_size: +$("#rt-n").value || 100000, per_class: +$("#rt-pc").value || 5000,
      xy: $("#rt-xy").checked, lonlat: $("#rt-ll").checked, rowcol: $("#rt-rc").checked, drop_nodata: $("#rt-drop").checked,
      format: $("#rt-fmt").value, name: $("#rt-name").value || "table",
      class_colors: gl?.samples ? gl.classColors : null,
    };
    const btn = $("#rt-run");
    btn.disabled = true;
    $("#rt-result").classList.add("hidden");
    try {
      const job = await api("/api/tables/from-raster", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "raster2table", save: "raster2table", title: `Raster → table · ${body.name}` });
      showRtResult(done.result);
      refreshTables();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  function showRtResult(r) {
    const box = $("#rt-result");
    const counts = Object.entries(r.class_counts || {});
    const max = Math.max(1, ...counts.map(([, n]) => n));
    box.innerHTML = `<div class="pca-sum"><b>${r.rows.toLocaleString()} rows × ${r.columns.length} columns</b> · ${esc(r.name)} · ${fmt(r.size_mb, r.size_mb < 1 ? 2 : 1)} MB · ${r.seconds} s<br>
        Pixel ${fmt(r.pixel_size[0], r.pixel_size[0] < 1 ? 3 : 1)} · ${esc(r.crs)}${r.factor > 1 ? ` · aggregated ${r.factor}×${r.factor}` : ""} · sampling: ${esc(r.sampling)}${r.target ? ` · label column: <b>${esc(r.target)}</b>` : ""}</div>
      ${counts.length ? `<div class="home-label" style="margin-top:12px">Rows per class</div><div class="dist">${counts.slice(0, 20).map(([k, n]) =>
        `<div style="grid-template-columns:1fr 3fr auto"><span>${esc(k)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * n / max)}%"></span></span><b>${n.toLocaleString()}</b></div>`).join("")}</div>` : ""}
      <div class="home-label" style="margin-top:12px">First rows</div>
      <div class="load-wrap">${tableHtml(r.columns, r.preview, r.label_columns || [])}</div>
      <div class="row"><a class="btn small primary" href="/api/tables/file?path=${encodeURIComponent(r.path)}" download>⬇ Download ${esc(r.format.toUpperCase())}</a>
        <button class="btn small" data-tp>Open in data viewer</button></div>
      <p class="hint">Saved as <code>${esc(r.path)}</code>. It's also listed under <a href="#" data-goml>Classical ML ▸ Your tables</a>.</p>`;
    box.classList.remove("hidden");
    $("[data-tp]", box).onclick = () => previewTable(r.path);
    addItem({ kind: "table", name: r.path.split(/[\\/]/).pop(), path: r.path, rows: r.rows, cols: r.columns.length });
    $("[data-goml]", box).onclick = (e) => { e.preventDefault(); switchTool("ml"); openMlSub(null); };
  }

  // ------------------------------------------------------------------ Classical ML: train a model / classify an image
  const mlx = { schema: null, desc: null, model: "rf", family: "All", report: null, cols: {}, tuneMethod: "random" };
  const stars = (n) => "★".repeat(n) + "☆".repeat(3 - n);
  const pct = (v) => v == null ? "–" : `${(v * 100).toFixed(1)}%`;
  const COORDS = ["x", "y", "lon", "lat", "row", "col"];

  function mlField(p, value, scope) {
    let input;
    if (p.type === "bool") input = `<input type="checkbox" data-p="${p.name}" data-scope="${scope}" ${value ? "checked" : ""}>`;
    else if (p.type === "select") input = `<select data-p="${p.name}" data-scope="${scope}">${p.options.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
    else if (p.type === "text") input = `<input type="text" data-p="${p.name}" data-scope="${scope}" value="${esc(value ?? "")}" maxlength="${p.maxlength || 60}">`;
    else input = `<input type="number" data-p="${p.name}" data-scope="${scope}" step="${p.type === "int" ? 1 : "any"}" ${p.min != null ? `min="${p.min}"` : ""} ${p.max != null ? `max="${p.max}"` : ""} value="${value ?? ""}" placeholder="${esc(p.placeholder || "")}">`;
    return `<div class="pca-field"><span>${esc(p.label)}${tipBtn(p.tip)}</span>${input}</div>`;
  }
  const mlCollect = (scope) => {
    const out = {};
    $$(`#mt-params [data-scope="${scope}"], #mt-adv [data-scope="${scope}"], #mt-prep [data-scope="${scope}"]`).forEach((i) => {
      out[i.dataset.p] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : +i.value) : i.value;
    });
    return out;
  };

  // ---------------- train: table, label, features
  async function refreshTrainTables(select) {
    const list = await api("/api/tables").catch(() => []);
    const sel = $("#mt-table"), cur = select || sel.value;
    sel.innerHTML = list.length ? list.map((t) => `<option value="${esc(t.path)}">${esc(t.name)}${t.rows != null ? ` · ${t.rows.toLocaleString()} rows` : ""}</option>`).join("")
      : `<option value="">No tables yet</option>`;
    if (cur && list.some((t) => t.path === cur)) sel.value = cur;
    if (sel.value) await loadTableDesc(sel.value); else { mlx.desc = null; renderTargetAndFeatures(); }
  }
  async function loadTableDesc(path) {
    $("#mt-table-info").innerHTML = `<span class="spinner"></span>Reading table…`;
    try { mlx.desc = await api(`/api/tables/describe?path=${encodeURIComponent(path)}`); }
    catch (e) { mlx.desc = null; toast(e.message, true); }
    renderTargetAndFeatures();
  }
  function renderTargetAndFeatures() {
    const d = mlx.desc;
    if (!d) { $("#mt-target").innerHTML = ""; $("#mt-cols").innerHTML = ""; $("#mt-table-info").innerHTML = `No tables yet? Create one with <a href="#" class="goto-rt"><b>Raster → table</b></a>.`; wireGotoRt(); return; }
    const meta = d.meta || {};
    $("#mt-table-info").innerHTML = `${d.rows.toLocaleString()} rows × ${d.columns.length} columns${meta.source ? ` · from ${esc(String(meta.source).split(/[\\/]/).pop())}` : ""}${meta.crs ? ` · ${esc(meta.crs)}` : ""}`;
    const labelGuess = meta.target && d.columns.some((c) => c.name === meta.target) ? meta.target : d.columns[d.columns.length - 1].name;
    $("#mt-target").innerHTML = d.columns.map((c) => `<option value="${esc(c.name)}" ${c.name === labelGuess ? "selected" : ""}>${esc(c.name)} (${c.type}${c.type !== "number" ? `, ${c.unique} values` : ""})</option>`).join("");
    mlx.cols = {};
    $("#mt-task").value = "auto";
    renderColumns(true);
    renderTargetInfo();
    $("#mt-name").value = `${MODEL_TITLE()}_${(meta.source ? String(meta.source).split(/[\\/]/).pop() : "table").replace(/\.[^.]+$/, "")}`.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60);
  }
  const MODEL_TITLE = () => (mlx.schema?.models[mlx.model]?.title || "model").replace(/[^A-Za-z0-9]+/g, "");
  const ID_COLS = ["poly_id", "sample_id", "fid", "id", "objectid"];
  // default role / type for every column (kept across target changes once the user has edited them)
  function colDefaults(c) {
    const meta = mlx.desc.meta || {}, target = $("#mt-target").value;
    const labelCols = new Set([...(meta.label_columns || []), target]);
    const skip = COORDS.includes(c.name) || labelCols.has(c.name) || ID_COLS.includes(c.name.toLowerCase()) || c.type === "text";
    return { role: skip ? "ignore" : "feature", type: c.suggest || (c.type === "text" ? "categorical" : "numeric") };
  }
  function renderColumns(useDefaults) {
    const d = mlx.desc, target = $("#mt-target").value, bandCols = new Set(d.meta?.band_columns || []);
    const q = ($("#mt-col-filter").value || "").toLowerCase();
    d.columns.forEach((c) => { if (useDefaults || !mlx.cols[c.name]) mlx.cols[c.name] = colDefaults(c); });
    $("#mt-cols").innerHTML = `<tr><th style="text-align:left">Column</th><th>Role</th><th>Type</th></tr>` + d.columns.map((c) => {
      const st = mlx.cols[c.name], isT = c.name === target, text = c.type === "text";
      const info = `${c.type}${c.type !== "number" ? ` · ${c.unique.toLocaleString()} values` : ""}${c.nulls ? ` · <span class="warn-t">${c.nulls.toLocaleString()} missing</span>` : ""}`;
      const ex = (c.examples || []).slice(0, 4).map(String).join(", ");
      const sugg = !isT && st.role === "feature" && c.suggest === "categorical" && st.type === "numeric" && !text
        ? ` <span class="sugg" title="Only ${c.unique} distinct whole numbers: probably codes, not measurements">looks categorical?</span>` : "";
      return `<tr class="${isT ? "is-target" : st.role === "ignore" ? "is-off" : ""}" data-col="${esc(c.name)}" ${q && !c.name.toLowerCase().includes(q) ? 'style="display:none"' : ""}>
        <td class="cn"><b title="${esc(c.name)}">${esc(c.name)}</b>${bandCols.has(c.name) ? ' <span class="band-tag">band</span>' : ""}${sugg}<small title="${esc(ex)}">${info}${ex ? ` · e.g. ${esc(ex.slice(0, 40))}` : ""}</small></td>
        <td><select data-role>${isT ? `<option value="target" selected>🎯 Target</option>` : ""}<option value="feature" ${!isT && st.role === "feature" ? "selected" : ""}>Feature</option><option value="ignore" ${!isT && st.role === "ignore" ? "selected" : ""}>Ignore</option>${isT ? "" : `<option value="target">Target</option>`}</select></td>
        <td><select data-type ${isT ? "disabled" : ""}><option value="numeric" ${st.type === "numeric" ? "selected" : ""} ${text ? "disabled" : ""}>Numeric</option><option value="categorical" ${st.type === "categorical" || text ? "selected" : ""}>Categorical</option></select></td></tr>`;
    }).join("");
    $$("#mt-cols tr[data-col]").forEach((tr) => {
      const name = tr.dataset.col;
      $("[data-role]", tr).onchange = (e) => {
        const v = e.target.value;
        if (v === "target") {
          const old = $("#mt-target").value;
          $("#mt-target").value = name; $("#mt-task").value = "auto";
          restoreOldTarget(old);
          renderColumns(false); renderTargetInfo(); return;
        }
        if (name === $("#mt-target").value) { e.target.value = "target"; return toast("Choose another target column first", true); }
        mlx.cols[name].role = v; tr.className = v === "ignore" ? "is-off" : ""; updateFeatHint();
      };
      $("[data-type]", tr).onchange = (e) => { mlx.cols[name].type = e.target.value; renderColumns(false); if ($("#mt-prep-flow").innerHTML) updatePrepFlow(); };
    });
    updateFeatHint();
  }
  const selFeatures = () => mlx.desc ? mlx.desc.columns.filter((c) => c.name !== $("#mt-target").value && mlx.cols[c.name]?.role === "feature").map((c) => c.name) : [];
  const selCategorical = () => selFeatures().filter((f) => mlx.cols[f].type === "categorical" || mlx.desc.columns.find((c) => c.name === f).type === "text");
  function updateFeatHint() {
    const f = selFeatures(), cats = selCategorical();
    const coords = f.filter((n) => COORDS.includes(n) || ID_COLS.includes(n.toLowerCase()));
    const nulls = f.filter((n) => mlx.desc.columns.find((c) => c.name === n).nulls);
    const textCats = cats.filter((n) => mlx.desc.columns.find((c) => c.name === n).type === "text");
    $("#mt-feats-hint").innerHTML = !f.length ? "Select at least one feature." :
      `<b>${f.length}</b> feature${f.length > 1 ? "s" : ""}${cats.length ? ` (${cats.length} categorical: ${cats.map(esc).join(", ")})` : ""} · ${mlx.desc.columns.length - f.length - 1} ignored` +
      (coords.length ? `<br><span style="color:var(--warn)">${coords.map(esc).join(", ")} included: the model may learn locations rather than spectra.</span>` : "") +
      (nulls.length ? `<br>${nulls.map(esc).join(", ")} ha${nulls.length > 1 ? "ve" : "s"} missing values: those rows are dropped unless <b>Preprocessing ▸ Missing values</b> is set to <b>Fill in</b>.` : "") +
      (textCats.length ? `<br><span style="color:var(--warn)">Text column${textCats.length > 1 ? "s" : ""} ${textCats.map(esc).join(", ")}: fine for evaluation, but an image can't provide ${textCats.length > 1 ? "them" : "it"}, so the model can't classify a raster.</span>` : "");
  }
  // target: categories or numbers?
  function suggestTask(col) {
    if (!col) return ["classification", ""];
    if (col.type === "text") return ["classification", `<b>${esc(col.name)}</b> holds text (${col.unique} values) → categories.`];
    if (col.type === "integer" && col.unique <= 30) return ["classification", `<b>${esc(col.name)}</b> has only ${col.unique} distinct whole numbers (e.g. ${esc((col.examples || []).slice(0, 4).join(", "))}) → probably class codes, so categories.`];
    if (col.type === "integer" && col.unique <= 100) return ["classification", `<b>${esc(col.name)}</b> has ${col.unique} distinct whole numbers. Categories if these are codes; numbers if they are counts or measurements.`];
    return ["regression", `<b>${esc(col.name)}</b> has ${col.unique.toLocaleString()} distinct ${col.type === "integer" ? "whole numbers" : "decimal values"} → a continuous quantity, so numbers.`];
  }
  function detectTask() {
    if ($("#mt-task").value !== "auto") return $("#mt-task").value;
    return suggestTask(mlx.desc?.columns.find((c) => c.name === $("#mt-target").value))[0];
  }
  function renderTargetInfo() {
    const d = mlx.desc, target = $("#mt-target").value, task = detectTask();
    const col = d?.columns.find((c) => c.name === target);
    const [sug, why] = suggestTask(col);
    $$("#mt-task-choice [data-task]").forEach((b) => b.classList.toggle("on", b.dataset.task === task));
    let note = why ? `💡 Suggested: <b>${sug === "classification" ? "Categories" : "Numbers"}</b>. ${why}` : "";
    if (col && task === "regression" && col.type === "text") note = `<span style="color:var(--err)">⚠ ${esc(col.name)} contains text, so it can't be treated as numbers. Choose Categories.</span>`;
    else if (col && task !== sug && $("#mt-task").value !== "auto") note += ` <span style="color:var(--warn)">You chose ${task === "classification" ? "Categories" : "Numbers"}: fine if you know the data.</span>`;
    $("#mt-task-suggest").innerHTML = note;
    const counts = d?.meta?.target === target ? d.meta.class_counts : null;
    let html = "";
    if (task === "classification" && counts) {
      const entries = Object.entries(counts), max = Math.max(...entries.map(([, n]) => n));
      const minN = Math.min(...entries.map(([, n]) => n));
      html += `<div class="dist" style="margin-top:6px">${entries.slice(0, 12).map(([k, n]) => `<div style="grid-template-columns:1fr 3fr auto"><span>${esc(k)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * n / max)}%"></span></span><b>${n.toLocaleString()}</b></div>`).join("")}</div>` +
        (max / Math.max(1, minN) > 5 ? `<p class="hint">Classes are unbalanced (${max.toLocaleString()} vs ${minN.toLocaleString()}). Consider <b>Class balancing → Balanced</b> under Parameters, and tune for <b>Macro F1</b>.</p>` : "");
    }
    if (task === "classification" && col && col.unique > 50) html += `<p class="hint" style="color:var(--warn)">${col.unique} distinct values: is this really a class column? Choose "Numbers on a scale" for continuous values.</p>`;
    $("#mt-target-info").innerHTML = html;
    renderModelCards();
  }
  $("#mt-table").onchange = () => loadTableDesc($("#mt-table").value);
  // the previous target goes back to its normal role (e.g. a numeric column becomes a feature again)
  function restoreOldTarget(old) {
    const c = mlx.desc?.columns.find((x) => x.name === old);
    if (c && mlx.cols[old] && old !== $("#mt-target").value) mlx.cols[old].role = colDefaults(c).role;
    const t = $("#mt-target").value;
    if (mlx.cols[t]) mlx.cols[t].role = "ignore";
    mlx.prevTarget = t;
  }
  $("#mt-target").onfocus = () => { mlx.prevTarget = $("#mt-target").value; };
  $("#mt-target").onchange = () => { $("#mt-task").value = "auto"; restoreOldTarget(mlx.prevTarget); renderColumns(false); renderTargetInfo(); };
  $$("#mt-task-choice [data-task]").forEach((b) => b.onclick = () => { $("#mt-task").value = b.dataset.task; renderTargetInfo(); });
  $("#mt-col-filter").oninput = () => renderColumns(false);
  const bulk = (fn) => { if (!mlx.desc) return; mlx.desc.columns.forEach((c) => { if (c.name !== $("#mt-target").value) mlx.cols[c.name].role = fn(c) ? "feature" : "ignore"; }); renderColumns(false); };
  $("#mt-feats-bands").onclick = () => { const bands = new Set(mlx.desc?.meta?.band_columns || []); bulk((c) => bands.size ? bands.has(c.name) : colDefaults(c).role === "feature" && c.type !== "text"); };
  $("#mt-feats-all").onclick = () => bulk((c) => colDefaults(c).role === "feature");
  $("#mt-feats-none").onclick = () => bulk(() => false);

  // ---------------- train: model cards + parameters
  function renderModelCards() {
    const sc = mlx.schema, task = detectTask();
    const fams = ["All", ...new Set(Object.values(sc.models).map((m) => m.family))];
    $("#mt-family").innerHTML = fams.map((f) => `<button class="${f === mlx.family ? "active" : ""}" data-fam="${esc(f)}">${esc(f)}</button>`).join("");
    $$("#mt-family [data-fam]").forEach((b) => b.onclick = () => { mlx.family = b.dataset.fam; renderModelCards(); });
    const usable = (k, m) => m.tasks.includes(task) && !sc.unavailable.includes(k);
    if (!usable(mlx.model, sc.models[mlx.model])) mlx.model = "rf";
    $("#mt-models").innerHTML = Object.entries(sc.models).filter(([, m]) => mlx.family === "All" || m.family === mlx.family).map(([k, m]) => {
      const ok = usable(k, m);
      const why = !m.tasks.includes(task) ? `Not for ${task}` : sc.unavailable.includes(k) ? "Not installed" : "";
      return `<div role="button" tabindex="${ok ? 0 : -1}" aria-disabled="${!ok}" aria-pressed="${k === mlx.model}" class="model-card ${k === mlx.model ? "on" : ""} ${ok ? "" : "off"}" data-model="${k}" title="${esc(why)}">
        ${m.recommended ? '<span class="rec">RECOMMENDED</span>' : ""}
        <span class="fam">${esc(m.family)}</span>
        <span class="mc-top"><b>${esc(m.title)}</b>${tipBtn(m.tip)}</span>
        <span class="stars"><span>Accuracy <b>${stars(m.accuracy)}</b></span><span>Speed <b>${stars(m.speed)}</b></span></span>
        <small>${esc(why || m.desc)}</small></div>`;
    }).join("");
    $$("#mt-models .model-card").forEach((b) => b.onkeydown = (e) => { if ((e.key === "Enter" || e.key === " ") && !e.target.closest(".tip")) { e.preventDefault(); b.click(); } });
    $$("#mt-models .model-card").forEach((b) => b.onclick = (e) => {
      if (e.target.closest(".tip") || b.classList.contains("off")) return;
      mlx.model = b.dataset.model;
      renderModelCards();
      renderTrainParams(true);
      if ($("#mt-name").value.match(/^[A-Za-z]+_/)) $("#mt-name").value = $("#mt-name").value.replace(/^[A-Za-z]+_/, MODEL_TITLE() + "_");
    });
    renderTrainParams(true);
  }
  function renderTrainParams(keepCommon) {
    const sc = mlx.schema, m = sc.models[mlx.model], task = detectTask();
    const fits = (p) => !p.tasks || p.tasks.includes(task);
    const prevCommon = keepCommon ? mlCollect("common") : {};
    const mp = m.params.filter(fits), cp = sc.common.filter(fits).filter((p) => p.group !== "prep");
    const prepPrev = keepCommon ? mlCollect("common") : {};
    renderPrep(sc.common.filter(fits).filter((p) => p.group === "prep"), prepPrev);
    const val = (p, prev) => (p.name in prev ? prev[p.name] : p.default);
    $("#mt-params").innerHTML = mp.filter((p) => !p.advanced).map((p) => mlField(p, p.default, "model")).join("") +
      cp.filter((p) => !p.advanced).map((p) => mlField(p, val(p, prevCommon), "common")).join("") +
      (mp.filter((p) => !p.advanced).length ? "" : `<p class="hint">${esc(m.title)} has no settings to tune here: it works well as is.</p>`);
    $("#mt-adv").innerHTML = mp.filter((p) => p.advanced).map((p) => mlField(p, p.default, "model")).join("") +
      cp.filter((p) => p.advanced).map((p) => mlField(p, val(p, prevCommon), "common")).join("");
    const mr = $('[data-p="max_train_rows"]');
    if (mr) mr.placeholder = `model default: ${m.max_rows.toLocaleString()}`;
    renderSpace(false);
  }
  $("#mt-reset").onclick = () => renderTrainParams(false);

  // ---------------- train: preprocessing card (missing values → columns → outliers → skew → scaling → target)
  function renderPrep(specs, prev) {
    $("#mt-prep").innerHTML = specs.map((p) => mlField(p, p.name in prev ? prev[p.name] : p.default, "common")).join("");
    $$("#mt-prep [data-scope]").forEach((i) => i.addEventListener("change", updatePrepFlow));
    updatePrepFlow();
  }
  function updatePrepFlow() {
    const v = mlCollect("common"), m = mlx.schema.models[mlx.model];
    const pctRow = $('#mt-prep [data-p="outlier_pct"]')?.closest(".pca-field");
    if (pctRow) pctRow.classList.toggle("hidden", v.outliers !== "clip");
    const scaling = v.scaling === "auto" ? (m.scale ? "standard" : "none") : v.scaling;
    const steps = [
      v.missing === "impute" ? "Fill missing" : "Drop rows with missing",
      v.drop_constant || v.drop_correlated !== "off" ? `Remove ${[v.drop_constant && "constant", v.drop_correlated !== "off" && `|r| ≥ ${v.drop_correlated}`].filter(Boolean).join(" + ")} columns` : null,
      v.outliers === "clip" ? `Clip ${v.outlier_pct ?? 1}–${100 - (v.outlier_pct ?? 1)}%` : null,
      v.skew !== "none" && v.skew ? `Yeo-Johnson (${v.skew === "auto" ? "skewed" : "all"})` : null,
      scaling !== "none" ? { standard: "Standard scale", minmax: "Min–max 0–1", robust: "Robust scale" }[scaling] + (v.scaling === "auto" ? " (auto)" : "") : (v.scaling === "auto" ? "No scaling (not needed)" : "No scaling"),
      selCategorical().length ? `One-hot ${selCategorical().length} categorical` : null,
      v.target_transform && v.target_transform !== "none" ? (v.target_transform === "log" ? "Target log(1+y)" : "Target Yeo-Johnson") : null,
    ].filter(Boolean);
    const warn = scaling === "none" && m.scale ? `<div class="hint" style="color:var(--warn)">${esc(m.title)} works much better with scaled features.</div>` : "";
    $("#mt-prep-flow").innerHTML = `<div class="home-label" style="margin:10px 0 4px">Pipeline</div><div class="flow">${steps.map((t) => `<span>${esc(t)}</span>`).join("<i>→</i>")}<i>→</i><span class="flow-model">${esc(m.title)}</span></div>${warn}`;
  }
  $("#mt-prep-reset").onclick = () => renderPrep(mlx.schema.common.filter((p) => p.group === "prep" && (!p.tasks || p.tasks.includes(detectTask()))), {});

  // ---------------- train: hyperparameter tuning
  const fmtCand = (v) => v === null ? "None" : String(v).replace(/,/g, ";");
  function renderSpace(reset) {
    const sc = mlx.schema, m = sc.models[mlx.model], task = detectTask();
    const prev = {};
    if (!reset) $$("#mt-space [data-sp]").forEach((i) => prev[i.dataset.sp] = { on: $(`[data-spon="${i.dataset.sp}"]`).checked, v: i.value });
    const space = sc.search[mlx.model] || {};
    const specs = m.params.filter((p) => (!p.tasks || p.tasks.includes(task)) && p.name in space);
    $("#mt-space").innerHTML = specs.length ? specs.map((p) => {
      const pv = mlx.spaceModel === mlx.model ? prev[p.name] : null;
      return `<div class="space-row"><label class="inline"><input type="checkbox" data-spon="${p.name}" ${pv ? (pv.on ? "checked" : "") : "checked"}> ${esc(p.label)}${tipBtn(p.tip)}</label>
        <input data-sp="${p.name}" value="${esc(pv ? pv.v : space[p.name].map(fmtCand).join(", "))}"></div>`;
    }).join("") : `<p class="hint">${esc(m.title)} has no settings worth tuning. Turn tuning off, or pick another model.</p>`;
    mlx.spaceModel = mlx.model;
    const metrics = sc.tune_metrics[task] || [];
    const cur = $("#mt-tune-metric").value;
    $("#mt-tune-metric").innerHTML = metrics.map(([v, t]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(t)}</option>`).join("");
    $$("#mt-space input").forEach((i) => i.oninput = i.onchange = tuneEstimate);
    tuneEstimate();
  }
  function collectSpace() {
    const out = {};
    $$("#mt-space [data-sp]").forEach((i) => {
      if (!$(`[data-spon="${i.dataset.sp}"]`).checked) return;
      const vals = i.value.split(",").map((v) => v.trim()).filter(Boolean);
      if (vals.length) out[i.dataset.sp] = vals;
    });
    return out;
  }
  function tuneEstimate() {
    const sp = collectSpace(), grid = Object.values(sp).reduce((a, v) => a * v.length, 1);
    const folds = Math.max(2, +$("#mt-tune-folds").value || 3);
    const tries = mlx.tuneMethod === "grid" ? grid : Math.min(grid, Math.max(1, +$("#mt-tune-iter").value || 20));
    $("#mt-tune-iter").disabled = mlx.tuneMethod === "grid";
    const n = Object.keys(sp).length;
    $("#mt-tune-est").innerHTML = !n ? `<span style="color:var(--warn)">Tick at least one parameter to tune.</span>` :
      `${n} parameter${n > 1 ? "s" : ""} · ${grid.toLocaleString()} possible combinations · <b>${tries.toLocaleString()} tries × ${folds} folds = ${(tries * folds).toLocaleString()} fits</b>, then one final fit` +
      (mlx.tuneMethod === "grid" && grid > 300 ? `<br><span style="color:var(--err)">Too many for grid search (max 300). Use random search or fewer values.</span>` : "") +
      (tries * folds > 150 ? `<br><span style="color:var(--warn)">This may take a while. Lower "Max training rows" (Advanced) to speed it up.</span>` : "");
  }
  $("#mt-tune").onchange = () => { $("#mt-tune-body").classList.toggle("hidden", !$("#mt-tune").checked); $("#mt-run").textContent = $("#mt-tune").checked ? "Tune & train model" : "Train model"; };
  $$("#mt-tune-method [data-m]").forEach((b) => b.onclick = () => { mlx.tuneMethod = b.dataset.m; $$("#mt-tune-method [data-m]").forEach((x) => x.classList.toggle("active", x === b)); tuneEstimate(); });
  $("#mt-tune-iter").oninput = $("#mt-tune-folds").oninput = tuneEstimate;
  $("#mt-tune-reset").onclick = () => renderSpace(true);
  const tuningPayload = () => $("#mt-tune").checked ? { enabled: true, method: mlx.tuneMethod, iter: +$("#mt-tune-iter").value || 20,
    folds: +$("#mt-tune-folds").value || 3, metric: $("#mt-tune-metric").value || "auto", space: collectSpace() } : {};

  function trainInputs() {
    const table = $("#mt-table").value, target = $("#mt-target").value, features = selFeatures();
    if (!table) { toast("Choose a training table first", true); return null; }
    if (!features.length) { toast("Select at least one feature (Role → Feature)", true); return null; }
    return { table, target, features, categorical: selCategorical(), task: detectTask(), common: mlCollect("common") };
  }
  $("#mt-run").onclick = async () => {
    const err = $("#mt-error"); err.classList.add("hidden");
    const inp = trainInputs(); if (!inp) return;
    const tuning = tuningPayload();
    if (tuning.enabled && !Object.keys(tuning.space).length) return toast("Tick at least one parameter to tune", true);
    const btn = $("#mt-run"); btn.disabled = true; $("#mt-compare").disabled = true;
    $("#mt-result").classList.add("hidden");
    try {
      const job = await api("/api/ml/train", { method: "POST", json: { ...inp, model: mlx.model, params: mlCollect("model"), tuning,
        name: $("#mt-name").value || "model" } });
      const done = await trackJob(job, { tool: "ml", save: "train", title: `${tuning.enabled ? "Tuning" : "Training"} ${mlx.schema.models[mlx.model].title}` });
      showTrainResult(done.result, $("#mt-result"));
      refreshModels();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; $("#mt-compare").disabled = false; }
  };
  $("#mt-compare").onclick = async () => {
    const err = $("#mt-error"); err.classList.add("hidden");
    const inp = trainInputs(); if (!inp) return;
    const btn = $("#mt-compare"); btn.disabled = true; $("#mt-run").disabled = true;
    const box = $("#mt-compare-box"); box.classList.add("hidden");
    try {
      const job = await api("/api/ml/compare", { method: "POST", json: inp });
      const done = await trackJob(job, { tool: "ml", title: "Comparing models" });
      showLeaderboard(done.result, box);
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; $("#mt-run").disabled = false; }
  };
  function showLeaderboard(r, box) {
    const cls = r.task === "classification", ok = r.rows.filter((x) => !x.error), best = ok[0];
    const cols = cls ? [["accuracy", "Acc.", pct], ["kappa", "Kappa", (v) => v.toFixed(3)]] : [["r2", "R²", (v) => v.toFixed(3)], ["rmse", "RMSE", fmtv]];
    const top = best ? best[r.metric] : 0;
    box.innerHTML = `<div class="card"><div class="row between"><h2 style="margin:0">🏆 Model comparison</h2><button class="chip" data-close>✕</button></div>
      <p class="hint">Every model trained with default settings on the same train/test split (max ${r.max_rows.toLocaleString()} training rows each), ranked by ${cls ? "kappa" : "R²"}. Pick one, then adjust or tune it and train.</p>
      <table class="data-table lb-table"><tr><th></th><th style="text-align:left">Model</th>${cols.map(([, t]) => `<th>${t}</th>`).join("")}<th></th></tr>
      ${r.rows.map((x, i) => x.error ? `<tr class="is-off"><td></td><td style="text-align:left" colspan="${cols.length + 2}">${esc(x.title)} <small class="hint">failed: ${esc(x.error)}</small></td></tr>` :
        `<tr class="${i === 0 ? "lb-best" : ""}" title="${cls ? `Macro F1 ${pct(x.f1_macro)} · balanced accuracy ${pct(x.balanced_accuracy)}` : `MAE ${fmtv(x.mae)}`}"><td>${i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : i + 1}</td>
          <td style="text-align:left"><b>${esc(x.title)}</b> <small class="hint">${x.seconds}s</small><span class="lb-bar"><span style="width:${Math.max(2, 100 * Math.max(0, x[r.metric]) / Math.max(1e-9, top))}%"></span></span></td>
          ${cols.map(([k, , f]) => `<td>${f(x[k])}</td>`).join("")}<td><button class="btn small ${i === 0 ? "primary" : ""}" data-use="${x.model}">Use</button></td></tr>`).join("")}</table>
      ${ok.length ? splitBadge(ok[0].split) : ""}</div>`;
    box.classList.remove("hidden");
    $("[data-close]", box).onclick = () => box.classList.add("hidden");
    $$("[data-use]", box).forEach((b) => b.onclick = () => {
      mlx.model = b.dataset.use; mlx.family = "All"; renderModelCards();
      $("#mt-name").value = $("#mt-name").value.replace(/^[A-Za-z]+_/, MODEL_TITLE() + "_");
      $("#mt-models").scrollIntoView({ behavior: "smooth", block: "center" });
      toast(`${mlx.schema.models[mlx.model].title} selected. Adjust or tune it, then Train.`);
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------- results dashboard (also used for saved model reports)
  function showTrainResult(r, box) {
    const cls = r.task === "classification";
    const grade = (v) => v >= 0.85 ? "good" : v >= 0.7 ? "ok" : "bad";
    let tiles;
    if (cls) {
      tiles = [["Overall accuracy", r.accuracy, "Share of test pixels classified correctly."],
               ["Kappa", r.kappa, "Agreement beyond chance (0 = random, 1 = perfect). Above 0.8 is excellent, 0.6–0.8 good."],
               ["Macro F1", r.f1_macro, "Average F1 over classes: treats rare classes as important as common ones."],
               ["Balanced acc.", r.balanced_accuracy, "Average recall over classes."]]
        .map(([k, v, t]) => `<div class="metric ${grade(v)}"><b>${k === "Kappa" ? v.toFixed(3) : pct(v)}</b><span>${k} ${tipBtn(t)}</span></div>`).join("");
    } else {
      tiles = `<div class="metric ${grade(r.r2)}"><b>${r.r2.toFixed(3)}</b><span>R² ${tipBtn("Share of the variation explained (1 = perfect, 0 = no better than the mean).")}</span></div>
        <div class="metric"><b>${fmtv(r.rmse)}</b><span>RMSE ${tipBtn("Typical prediction error, in the label's units.")}</span></div>
        <div class="metric"><b>${fmtv(r.mae)}</b><span>MAE ${tipBtn("Average absolute error.")}</span></div>
        <div class="metric"><b>${r.test_rows.toLocaleString()}</b><span>Test rows</span></div>`;
    }
    let cm = "", perClass = "";
    if (cls) {
      const k = r.classes.length, max = Math.max(1, ...r.confusion.flat());
      const rowTot = r.confusion.map((row) => row.reduce((a, b) => a + b, 0));
      const colTot = r.classes.map((_, j) => r.confusion.reduce((a, row) => a + row[j], 0));
      const short = (c) => esc(String(c).slice(0, 9));
      cm = `<div class="home-label" style="margin-top:14px">Confusion matrix ${tipBtn("Rows = true class, columns = predicted class, numbers = test pixels. The diagonal (green) is correct. Off-diagonal cells show which classes get confused. Producer's accuracy = recall (row), user's accuracy = precision (column).")}</div>
        <div class="load-wrap"><table class="cm-table"><tr><th></th>${r.classes.map((c) => `<th title="${esc(c)}">${short(c)}</th>`).join("")}<th title="Producer's accuracy (recall)">PA</th></tr>
        ${r.confusion.map((row, i) => `<tr><th class="rowh" title="${esc(r.classes[i])}">${short(r.classes[i])}</th>${row.map((v, j) => {
          const a = v / max, diag = i === j;
          return `<td style="background:${diag ? `rgba(21,128,61,${0.15 + 0.75 * a})` : v ? `rgba(185,28,28,${0.12 + 0.7 * a})` : "transparent"};color:${a > 0.5 ? "#fff" : "inherit"}">${v || ""}</td>`;
        }).join("")}<td><b>${rowTot[i] ? Math.round(100 * row[i] / rowTot[i]) : "–"}</b></td></tr>`).join("")}
        <tr><th class="rowh" title="User's accuracy (precision)">UA</th>${r.classes.map((_, j) => `<td><b>${colTot[j] ? Math.round(100 * r.confusion[j][j] / colTot[j]) : "–"}</b></td>`).join("")}<td></td></tr></table></div>`;
      perClass = `<details class="an-details"><summary>Per-class scores</summary><table class="data-table"><tr><th style="text-align:left">Class</th><th>Precision (UA)</th><th>Recall (PA)</th><th>F1</th><th>Test px</th></tr>
        ${r.per_class.map((c) => `<tr><td style="text-align:left">${esc(c.class)}</td><td>${pct(c.precision)}</td><td>${pct(c.recall)}</td><td>${pct(c.f1)}</td><td>${c.support.toLocaleString()}</td></tr>`).join("")}</table></details>`;
    } else if (r.scatter) {
      const t = r.scatter.true, p = r.scatter.pred, lo = Math.min(...t, ...p), hi = Math.max(...t, ...p), sc = (v) => 10 + 280 * (v - lo) / ((hi - lo) || 1);
      cm = `<div class="home-label" style="margin-top:14px">Predicted vs true (test sample)</div><svg viewBox="0 0 300 300" class="ev-chart" style="max-width:300px">
        <line x1="10" y1="290" x2="290" y2="10" stroke="var(--muted)" stroke-dasharray="4 3"/>${t.map((v, i) => `<circle cx="${sc(v)}" cy="${300 - sc(p[i])}" r="2" fill="var(--accent)" opacity=".5"/>`).join("")}</svg>`;
    }
    const imp = r.importance ? `<div class="home-label" style="margin-top:14px">Feature importance ${tipBtn("Which inputs the model relies on most. " + r.importance.kind + ".")}</div>
      ${r.importance.features.slice(0, 15).map((f, i) => `<div class="imp-row"><span title="${esc(f)}">${esc(f)}</span><span><span class="imp-bar" style="display:block;width:${Math.max(2, 100 * r.importance.values[i] / r.importance.values[0])}%"></span></span><span>${(r.importance.values[i] * 100).toFixed(1)}%</span></div>`).join("")}` : "";
    const tn = r.tuning, lowBetter = tn?.lower_is_better, fmtS = (v) => tn && ["accuracy", "f1_macro", "balanced_accuracy"].includes(tn.metric) ? pct(v) : fmtv(v);
    const tune = tn ? `<div class="home-label" style="margin-top:14px">Hyperparameter tuning ${tipBtn(`${tn.method === "grid" ? "Grid" : "Random"} search: ${tn.candidates} combinations × ${tn.folds} folds${tn.grouped ? ", folds grouped by polygon / block" : ""}. Scored on training rows only; the test scores above are from the untouched test split.`)}</div>
      <div class="tune-best">Best ${esc(tn.metric.replace("neg_", "").toUpperCase())} in CV: <b>${fmtS(tn.best_score)}</b> with ${Object.entries(tn.best_params).map(([k, v]) => `<code>${esc(k)} = ${esc(fmtCand(v))}</code>`).join(" ")}</div>
      <details class="an-details"><summary>Top ${tn.results.length} of ${tn.candidates} combinations</summary><div class="load-wrap"><table class="data-table"><tr><th>#</th>${Object.keys(tn.best_params).map((k) => `<th>${esc(k)}</th>`).join("")}<th>CV score</th><th>±</th></tr>
      ${tn.results.map((x, i) => `<tr class="${i === 0 ? "lb-best" : ""}"><td>${i + 1}</td>${Object.keys(tn.best_params).map((k) => `<td>${esc(fmtCand(x.params[k]))}</td>`).join("")}<td><b>${fmtS(x.mean)}</b></td><td>${fmtS(x.std)}</td></tr>`).join("")}</table></div>
      ${lowBetter ? '<p class="hint">Lower is better for this metric.</p>' : ""}</details>` : "";
    const cv = r.cv ? `<p class="hint">${r.cv.folds.length}-fold cross-validation ${r.cv.metric}: <b>${r.cv.metric === "accuracy" ? pct(r.cv.mean) : r.cv.mean.toFixed(3)}</b> ± ${r.cv.metric === "accuracy" ? pct(r.cv.std) : r.cv.std.toFixed(3)}</p>` : "";
    box.innerHTML = `<div class="card">
      <div class="row between"><h2 style="margin:0">✓ ${esc(r.model_title)} trained</h2><span class="task-badge">${esc(r.task)}</span></div>
      ${splitBadge(r.split)}
      <div class="pca-sum" style="margin-top:4px">${r.train_rows.toLocaleString()} training rows${r.train_rows_before_sampling > r.train_rows ? ` (sampled from ${r.train_rows_before_sampling.toLocaleString()})` : ""} · ${r.test_rows.toLocaleString()} test rows · ${r.features.length} features · ${r.seconds} s${r.rows_dropped ? ` · ${r.rows_dropped.toLocaleString()} rows with missing values skipped` : ""}${r.rows_imputed ? ` · ${r.rows_imputed.toLocaleString()} rows had missing values filled in` : ""}</div>
      <div class="metric-tiles" style="margin-top:10px">${tiles}</div>
      ${r.warnings?.length ? `<div class="warn">${r.warnings.map(esc).join("<br>")}</div>` : ""}
      ${r.preprocessing ? `<div class="prep-res"><b>Preprocessing</b> ${r.preprocessing.steps.length ? r.preprocessing.steps.map(esc).join(" → ") : "none needed"}${r.categorical?.length ? ` · one-hot: ${r.categorical.map(esc).join(", ")}` : ""}
        ${r.preprocessing.removed?.length ? `<br><b>Removed columns</b> ${r.preprocessing.removed.map((x) => `<span title="${esc(x.reason)}">${esc(x.feature)}</span> <small>(${esc(x.reason)})</small>`).join(", ")}` : ""}</div>`
        : r.categorical?.length ? `<p class="hint">Categorical (one-hot encoded): ${r.categorical.map(esc).join(", ")}</p>` : ""}
      ${tune}${cv}${cm}${perClass}${imp}
      <div class="row" style="flex-wrap:wrap">${cls || r.task === "regression" ? `<button class="btn small primary" data-classify="${esc(r.path)}">${cls ? "Classify an image with this model →" : "Apply to an image →"}</button>` : ""}
        <a class="btn small" href="/api/models/file?path=${encodeURIComponent(r.path)}" download>⬇ Model (.joblib)</a></div>
      <div class="eval-cta"><span>📊</span><span><b>Evaluation report</b><small>Confusion matrices, per-class scores, ${cls ? "ROC and precision–recall curves, confidence and calibration charts" : "predicted-vs-true, residual and Q–Q plots, error by value range"}, feature importance${r.tuning ? ", tuning results" : ""}. One HTML file, opens offline.</small></span>
        <span class="row tight"><a class="btn small primary" href="/api/models/evaluation?path=${encodeURIComponent(r.path)}" target="_blank" rel="noopener">Open</a><a class="btn small" href="/api/models/evaluation?path=${encodeURIComponent(r.path)}&download=true">⬇ .html</a></span>
        <div class="eval-save">${r.evaluation_saved_to ? `<div class="eval-saved">✓ Saved to <code title="${esc(r.evaluation_saved_to)}">${esc(r.evaluation_saved_to)}</code></div>` : ""}
          <div class="row tight"><input data-evdir placeholder="Folder, e.g. ~/Documents/LULC reports" value="${esc(prefs.get("report-dir", ""))}" spellcheck="false" autocomplete="off"><button class="btn small" data-evsave>${r.evaluation_saved_to ? "Save another copy" : "Save a copy"}</button></div></div></div>
      <p class="hint">Saved as <code>${esc(r.path)}</code>.</p></div>`;
    box.classList.remove("hidden");
    $("[data-classify]", box)?.addEventListener("click", () => { openMlSub("predict"); refreshPredict(r.path); });
    $("[data-evsave]", box)?.addEventListener("click", async (e) => {
      const folder = $("[data-evdir]", box).value.trim();
      if (!folder) return toast("Type the folder to save the report in", true);
      await busy(e.currentTarget, "Saving…", async () => {
       try {
        const res = await api("/api/models/evaluation/save", { method: "POST", json: { path: r.path, folder } });
        prefs.set("report-dir", folder);
        let el = $(".eval-saved", box);
        if (!el) { el = document.createElement("div"); el.className = "eval-saved"; $(".eval-save", box).prepend(el); }
        el.innerHTML = `✓ Saved to <code title="${esc(res.saved_to)}">${esc(res.saved_to)}</code>`;
        toast("Evaluation report saved");
       } catch (err) { toast(err.message, true); }
      });
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function splitBadge(sp) {
    if (!sp) return "";
    if (sp.method === "group") return `<div style="margin-top:6px"><span class="split-badge honest">✓ Independent test: by polygon</span> <span class="hint">${sp.groups_test} of ${sp.groups_total} polygons held out for testing</span></div>`;
    if (sp.method === "blocks") return `<div style="margin-top:6px"><span class="split-badge honest">✓ Independent test: spatial blocks</span> <span class="hint">${sp.groups_test} of ${sp.groups_total} blocks (${fmtv(sp.block_size)} map units) held out</span></div>`;
    return `<div style="margin-top:6px"><span class="split-badge optimistic">⚠ Random pixel split</span> <span class="hint">Neighbouring pixels are in both train and test, so accuracy is probably optimistic. Use polygon samples or x/y columns for an honest test.</span></div>`;
  }

  // ---------------- model library (hub)
  async function refreshModels() {
    const list = await api("/api/models").catch(() => []);
    $("#ml-models").innerHTML = list.length ? list.map((m) => `<div class="ws-row"><span>${esc(m.name)}
        <small>${esc(m.model_title || m.model)} · ${esc(m.task || "")} · <span class="model-row-metric">${m.kind === "clustering" ? `${m.n_clusters} clusters${m.silhouette != null ? `, silhouette ${m.silhouette.toFixed(2)}` : ""}` : m.task === "classification" ? `accuracy ${pct(m.accuracy)}, kappa ${m.kappa?.toFixed(2)}` : `R² ${m.r2?.toFixed(3)}`}</span> · ${(m.features || []).length} features</small></span>
        <span class="row tight"><button class="btn small primary" data-mcls="${esc(m.path)}" title="Classify an image">Use</button><button class="btn small" data-mrep="${esc(m.path)}">Report</button>${m.kind === "clustering" ? "" : `<a class="btn small" href="/api/models/evaluation?path=${encodeURIComponent(m.path)}" target="_blank" rel="noopener" title="Full evaluation report with matrices and charts (opens in a new tab)">📊</a>`}<a class="btn small" href="/api/models/file?path=${encodeURIComponent(m.path)}" download>⬇</a><button class="btn small danger" data-mdel="${esc(m.path)}" title="Delete">×</button></span></div>`).join("")
      : '<p class="hint">No models yet. Use <b>Train a model</b>.</p>';
    $$("[data-mcls]").forEach((b) => b.onclick = () => { openMlSub("predict"); refreshPredict(b.dataset.mcls); });
    $$("[data-mrep]").forEach((b) => b.onclick = async () => {
      const r = await api(`/api/models/report?path=${encodeURIComponent(b.dataset.mrep)}`);
      $("#tbl-title").textContent = `${r.kind === "clustering" ? "Clustering" : "Model"} report · ${r.name}`;
      if (r.kind === "clustering") showClusterResult(r, $("#tbl-body")); else showTrainResult(r, $("#tbl-body"));
      $("#dlg-table").showModal();
    });
    $$("[data-mdel]").forEach((b) => b.onclick = async () => { if (confirm("Delete this model?")) { await api(`/api/models?path=${encodeURIComponent(b.dataset.mdel)}`, { method: "DELETE" }); refreshModels(); } });
    return list;
  }

  // ---------------- classify an image
  const colName = (d) => { let n = String(d).replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "band"; return /^\d/.test(n) ? "b" + n : n; };
  let mpModels = [];
  async function refreshPredict(selectModel) {
    mpModels = await api("/api/models").catch(() => []);
    const sel = $("#mp-model"), cur = selectModel || sel.value;
    sel.innerHTML = mpModels.length ? mpModels.map((m) => `<option value="${esc(m.path)}">${esc(m.name)} · ${esc(m.model_title || "")} · ${m.kind === "clustering" ? `${m.n_clusters} clusters (unsupervised)` : m.task === "classification" ? `acc ${pct(m.accuracy)}` : `R² ${m.r2?.toFixed(2)}`}</option>`).join("")
      : `<option value="">No trained models yet</option>`;
    if (cur && mpModels.some((m) => m.path === cur)) sel.value = cur;
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    const rs = $("#mp-raster"), rcur = rs.value;
    rs.innerHTML = rasters.length ? rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("") : `<option value="">No raster layers</option>`;
    const m = mpModel();
    // prefer the layer the model was trained from
    const srcName = m?.source?.source ? String(m.source.source).split(/[\\/]/).pop() : null;
    const pick = rasters.find((l) => l.id === rcur) || rasters.find((l) => srcName && l.path.endsWith(srcName)) || rasters[0];
    if (pick) rs.value = pick.id;
    renderPredictForm();
  }
  const mpModel = () => mpModels.find((m) => m.path === $("#mp-model").value);
  function renderPredictForm() {
    const m = mpModel(), l = getLayer($("#mp-raster").value);
    $("#mp-model-info").innerHTML = m ? `${esc(m.model_title)} · ${esc(m.task)} · features: ${esc((m.features || []).join(", "))}` : "";
    if (!m || !l) { $("#mp-match").innerHTML = ""; return; }
    const bands = l.info.bands;
    const byName = Object.fromEntries(bands.map((b) => [colName(b.description), b.index]));
    const src = m.source || {};
    const rows = m.features.map((f) => {
      let idx = byName[f];
      if (idx == null && src.band_columns && src.band_indices) {
        const i = src.band_columns.indexOf(f);
        if (i >= 0 && src.band_indices[i] <= bands.length) idx = src.band_indices[i];
      }
      return { f, idx };
    });
    $("#mp-match").innerHTML = `<div class="home-label" style="margin-top:10px">Model feature → image band ${tipBtn("Each input the model was trained on must come from the matching band of this image. Bands are matched by name automatically. Check them if the image has a different band order.")}</div>` +
      rows.map(({ f, idx }) => `<div class="match-row"><span title="${esc(f)}">${esc(f)}</span><select data-feat="${esc(f)}"><option value="">— choose —</option>${bands.map((b) => `<option value="${b.index}" ${b.index === idx ? "selected" : ""}>Band ${b.index}${b.description !== "Band " + b.index ? " · " + esc(b.description) : ""}</option>`).join("")}</select><span data-ok="${esc(f)}">${idx ? "✓" : "⚠"}</span></div>`).join("");
    $$("#mp-match select").forEach((s) => s.onchange = () => { $(`[data-ok="${CSS.escape(s.dataset.feat)}"]`).textContent = s.value ? "✓" : "⚠"; });
    const sc = src.scale ?? 1, off = src.offset ?? 0, conv = sc !== 1 || off !== 0;
    $("#mp-refl").checked = conv;
    $("#mp-refl").disabled = !conv;
    $("#mp-refl").dataset.scale = sc; $("#mp-refl").dataset.offset = off;
    $("#mp-refl-val").textContent = conv ? `(× ${sc} ${off < 0 ? "−" : "+"} ${Math.abs(off)})` : "(not needed: the table kept the raw values)";
    $("#mp-conf").disabled = m.task !== "classification" || !m.has_proba;
    $("#mp-conf").checked = !$("#mp-conf").disabled;
    $("#mp-name").value = `${m.name}_map`.slice(0, 70);
  }
  function refreshPredictRasters() {  // keep the user's band choices unless the selected image disappears
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived), rs = $("#mp-raster"), cur = rs.value;
    rs.innerHTML = rasters.length ? rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("") : `<option value="">No raster layers</option>`;
    if (rasters.some((l) => l.id === cur)) rs.value = cur; else renderPredictForm();
  }
  $("#mp-model").onchange = renderPredictForm;
  $("#mp-raster").onchange = renderPredictForm;
  $("#mp-run").onclick = async () => {
    const err = $("#mp-error"); err.classList.add("hidden");
    const m = mpModel(), l = getLayer($("#mp-raster").value);
    if (!m) return toast("Train a model first", true);
    if (!l) return toast("Choose an image to classify", true);
    const band_map = {};
    for (const s of $$("#mp-match select")) { if (!s.value) return toast(`Choose a band for ${s.dataset.feat}`, true); band_map[s.dataset.feat] = +s.value; }
    const conv = $("#mp-refl").checked;
    const btn = $("#mp-run"); btn.disabled = true;
    $("#mp-result").classList.add("hidden");
    try {
      const job = await api("/api/ml/predict", { method: "POST", json: {
        model: m.path, path: l.path, band_map, scale: conv ? +$("#mp-refl").dataset.scale : 1, offset: conv ? +$("#mp-refl").dataset.offset : 0,
        clip: getClip("mp-area"), resolution: $("#mp-res").value, confidence: $("#mp-conf").checked, name: $("#mp-name").value || "classified" } });
      const done = await trackJob(job, { tool: "ml", save: "predict", title: `Classifying with ${m.model_title}` });
      const r = done.result;
      const out = await addRasterFromPath(r.path, { name: $("#mp-name").value || "classified",
        render: m.task === "classification" ? { band: 1, stretch: "fixed" } : { band: 1, stretch: "auto", cmap: "Viridis" } });
      out.open = true; renderContents();
      const box = $("#mp-result");
      box.innerHTML = `<div class="pca-sum" style="margin-top:12px"><b>✓ Map created</b> · ${r.width.toLocaleString()} × ${r.height.toLocaleString()} px${r.factor > 1 ? ` (${r.factor}× coarser)` : ""} · ${r.seconds} s${r.confidence ? " · band 2 = confidence %" : ""}. It's in Contents.</div>` +
        (r.classes ? `<div class="lyr-classes" style="margin-top:8px">${r.classes.map((c) => `<div style="grid-template-columns:12px 1fr auto auto;gap:8px"><i style="background:${esc(c.color)}"></i><span>${esc(c.name)}</span><span>${c.area_km2 != null ? fmt(c.area_km2, c.area_km2 < 10 ? 2 : 1) + " km²" : ""}</span><b>${fmt(c.pct)}%</b></div>`).join("")}</div>` : "") +
        `<div class="row"><button class="btn small" data-mpz>Zoom to map</button><button class="btn small" data-mpx>Export…</button></div>`;
      box.classList.remove("hidden");
      $("[data-mpz]", box).onclick = () => zoomTo(out);
      $("[data-mpx]", box).onclick = () => openExport(out);
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  function wireGotoRt() { $$(".goto-rt").forEach((a) => a.onclick = (e) => { e.preventDefault(); switchTool("raster2table"); }); }

  // ------------------------------------------------------------------ Classical ML: unsupervised (clustering, t-SNE)
  const ux = { schema: null, method: "kmeans", pages: {} };
  const CL_PAL = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#393b79",
                  "#ad494a", "#637939", "#8c6d31", "#843c39", "#7b4173", "#3182bd", "#e6550d", "#31a354", "#756bb1", "#636363"];
  const clColor = (i, n, noise) => noise ? "#9ca3af" : CL_PAL[i % CL_PAL.length];
  const VIRIDIS = ["#440154", "#482878", "#3e4a89", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"];
  function viridis(t) {
    t = Math.min(1, Math.max(0, t)) * (VIRIDIS.length - 1);
    const i = Math.floor(t), f = t - i, a = VIRIDIS[i], b = VIRIDIS[Math.min(i + 1, VIRIDIS.length - 1)];
    const h = (s, k) => parseInt(s.slice(1 + 2 * k, 3 + 2 * k), 16);
    return `rgb(${[0, 1, 2].map((k) => Math.round(h(a, k) + (h(b, k) - h(a, k)) * f)).join(",")})`;
  }

  async function ensureUnsupSchema() { if (!ux.schema) ux.schema = await api("/api/unsup/schema"); return ux.schema; }
  const pageOf = (pre) => (ux.pages[pre] ||= { desc: null, cols: {} });
  const root = (pre) => $(pre === "uc" ? "#ml-sub-cluster" : "#ml-sub-tsne");

  // ---- table + column picker (shared by both pages)
  async function refreshUnsup(pre, selectPath) {
    await ensureUnsupSchema();
    const list = await api("/api/tables").catch(() => []);
    const sel = $(`#${pre}-table`), cur = selectPath || sel.value;
    sel.innerHTML = list.length ? list.map((t) => `<option value="${esc(t.path)}">${esc(t.name)}${t.rows != null ? ` · ${t.rows.toLocaleString()} rows` : ""}</option>`).join("")
      : `<option value="">No tables yet</option>`;
    if (cur && list.some((t) => t.path === cur)) sel.value = cur;
    sel.onchange = () => loadUnsupTable(pre);
    if (pre === "uc") renderMethodCards(); else renderTsneParams(false);
    renderUnsupPrep(pre, false);
    await loadUnsupTable(pre);
  }
  async function loadUnsupTable(pre) {
    const pg = pageOf(pre), path = $(`#${pre}-table`).value;
    pg.desc = null; pg.cols = {};
    if (!path) { $(`.uc-cols`, root(pre)).innerHTML = ""; $(`#${pre}-table-info`).innerHTML = `No tables yet. Add a CSV / Excel file with <b>+ Add data</b> or use <b>Raster → table</b>.`; return; }
    $(`#${pre}-table-info`).innerHTML = `<span class="spinner"></span>Reading table…`;
    try { pg.desc = await api(`/api/tables/describe?path=${encodeURIComponent(path)}`); }
    catch (e) { $(`#${pre}-table-info`).textContent = e.message; return; }
    const d = pg.desc, meta = d.meta || {};
    $(`#${pre}-table-info`).innerHTML = `${d.rows.toLocaleString()} rows × ${d.columns.length} columns${meta.source ? ` · from ${esc(String(meta.source).split(/[\\/]/).pop())}` : ""}`;
    const labelCols = new Set([...(meta.label_columns || []), meta.target].filter(Boolean));
    d.columns.forEach((c) => {
      const skip = COORDS.includes(c.name) || ID_COLS.includes(c.name.toLowerCase()) || labelCols.has(c.name) || c.type === "text" || /(^|_)id$/i.test(c.name);
      pg.cols[c.name] = { use: !skip, cat: c.type === "text" };
    });
    // a label to compare with / colour by: the table's target, else a text column with few values
    // prefer a column that looks like a label (label / class / target / risk / type …), else the last text column with few values
    const cand = d.columns.filter((c) => c.unique >= 2 && c.unique <= 30 && !/(^|_)id$/i.test(c.name) && (c.type === "text" || c.type === "integer"));
    const named = cand.filter((c) => /label|class|target|risk|categor|type|group|crop|landcover|lulc|cluster|outcome|status/i.test(c.name));
    const guess = meta.cluster_column || meta.target || named[named.length - 1]?.name || cand.filter((c) => c.type === "text").pop()?.name || "";
    const opts = (empty) => `<option value="">${empty}</option>` + d.columns.filter((c) => c.unique <= 200 || c.type !== "text").map((c) =>
      `<option value="${esc(c.name)}" ${c.name === guess ? "selected" : ""}>${esc(c.name)} (${c.type}${c.type !== "number" ? `, ${c.unique} values` : ""})</option>`).join("");
    if (pre === "uc") $("#uc-compare").innerHTML = opts("None");
    else $("#ut-color").innerHTML = opts("No colour (or pick after the run)");
    if (pre === "uc") {
      $("#uc-name").value = (path.split(/[\\/]/).pop().replace(/\.[^.]+$/, "") || "clusters").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 50);
      const sm = $('#ml-sub-cluster [data-p="save_model"]');
      if (sm) sm.checked = !!(meta.band_columns && meta.band_columns.length);
    } else $("#ut-name").value = (path.split(/[\\/]/).pop().replace(/\.[^.]+$/, "") || "tsne").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 50);
    renderUnsupCols(pre);
  }
  function renderUnsupCols(pre) {
    const pg = pageOf(pre), d = pg.desc, box = root(pre);
    if (!d) return;
    const q = ($(".uc-col-filter", box).value || "").toLowerCase();
    $(".uc-cols", box).innerHTML = `<tr><th style="text-align:left">Column</th><th>Use</th><th>Categorical</th></tr>` + d.columns.map((c) => {
      const st = pg.cols[c.name], text = c.type === "text";
      const ex = (c.examples || []).slice(0, 4).map(String).join(", ");
      return `<tr class="${st.use ? "" : "is-off"}" data-col="${esc(c.name)}" ${q && !c.name.toLowerCase().includes(q) ? 'style="display:none"' : ""}>
        <td class="cn"><b title="${esc(c.name)}">${esc(c.name)}</b>${!text && c.suggest === "categorical" && st.use && !st.cat ? ' <span class="sugg" title="Few distinct whole numbers: maybe codes, not measurements">codes?</span>' : ""}
          <small title="${esc(ex)}">${c.type}${c.type !== "number" ? ` · ${c.unique.toLocaleString()} values` : ""}${c.nulls ? ` · <span class="warn-t">${c.nulls.toLocaleString()} missing</span>` : ""}${ex ? ` · e.g. ${esc(ex.slice(0, 36))}` : ""}</small></td>
        <td style="text-align:center"><input type="checkbox" data-use ${st.use ? "checked" : ""}></td>
        <td style="text-align:center"><input type="checkbox" data-cat ${st.cat || text ? "checked" : ""} ${text ? "disabled title='Text columns are always categorical'" : ""}></td></tr>`;
    }).join("");
    $$("tr[data-col]", box).forEach((tr) => {
      const st = pg.cols[tr.dataset.col];
      $("[data-use]", tr).onchange = (e) => { st.use = e.target.checked; tr.classList.toggle("is-off", !st.use); unsupColsHint(pre); };
      $("[data-cat]", tr).onchange = (e) => { st.cat = e.target.checked; unsupColsHint(pre); };
    });
    unsupColsHint(pre);
  }
  const unsupFeatures = (pre) => { const pg = pageOf(pre); return pg.desc ? pg.desc.columns.filter((c) => pg.cols[c.name]?.use).map((c) => c.name) : []; };
  const unsupCats = (pre) => { const pg = pageOf(pre); return unsupFeatures(pre).filter((f) => pg.cols[f].cat || pg.desc.columns.find((c) => c.name === f).type === "text"); };
  function unsupColsHint(pre) {
    const f = unsupFeatures(pre), cats = unsupCats(pre), box = root(pre);
    const flagged = f.filter((n) => COORDS.includes(n) || ID_COLS.includes(n.toLowerCase()));
    $(".uc-cols-hint", box).innerHTML = !f.length ? "Tick at least one column." :
      `<b>${f.length}</b> column${f.length > 1 ? "s" : ""}${cats.length ? ` (${cats.length} categorical)` : ""}` +
      (flagged.length ? `<br><span style="color:var(--warn)">${flagged.map(esc).join(", ")}: ids / coordinates usually make meaningless clusters.</span>` : "");
  }
  $$(".uc-col-filter").forEach((i) => i.oninput = () => renderUnsupCols(i.closest(".ml-sub").id === "ml-sub-cluster" ? "uc" : "ut"));
  $$("#ml-sub-cluster [data-colset], #ml-sub-tsne [data-colset]").forEach((b) => b.onclick = () => {
    const pre = b.closest(".ml-sub").id === "ml-sub-cluster" ? "uc" : "ut", pg = pageOf(pre);
    if (!pg.desc) return;
    pg.desc.columns.forEach((c) => {
      const plain = !COORDS.includes(c.name) && !ID_COLS.includes(c.name.toLowerCase()) && !/(^|_)id$/i.test(c.name);
      pg.cols[c.name].use = b.dataset.colset === "none" ? false : b.dataset.colset === "all" ? plain : plain && c.type !== "text";
    });
    renderUnsupCols(pre);
  });

  // ---- settings
  const collectIn = (sel) => {
    const out = {};
    $$(`${sel} [data-p]`).forEach((i) => { out[i.dataset.p] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : +i.value) : i.value; });
    return out;
  };
  function renderUnsupPrep(pre, reset) {
    const prev = reset ? {} : collectIn(`#${pre}-prep`);
    $(`#${pre}-prep`).innerHTML = ux.schema.prep.map((p) => mlField(p, p.name in prev ? prev[p.name] : p.default, "prep")).join("");
    const sync = () => { const r = $(`#${pre}-prep [data-p="outlier_pct"]`)?.closest(".pca-field"); if (r) r.classList.toggle("hidden", $(`#${pre}-prep [data-p="outliers"]`).value !== "clip"); };
    $(`#${pre}-prep [data-p="outliers"]`).addEventListener("change", sync);
    sync();
  }
  function renderMethodCards() {
    const ms = ux.schema.methods;
    $("#uc-methods").innerHTML = Object.entries(ms).map(([k, m]) => `<div role="button" tabindex="0" aria-pressed="${k === ux.method}" class="model-card ${k === ux.method ? "on" : ""}" data-method="${k}">
        ${m.recommended ? '<span class="rec">RECOMMENDED</span>' : ""}<span class="fam">${esc(m.family)}${m.noise ? " · finds noise" : ""}</span>
        <span class="mc-top"><b>${esc(m.title)}</b>${tipBtn(m.tip)}</span>
        <span class="stars"><span>${m.k ? "You choose k" : "Finds k itself"}</span><span>Speed <b>${stars(m.speed)}</b></span></span>
        <small>${esc(m.desc)}</small></div>`).join("");
    $$("#uc-methods .model-card").forEach((b) => {
      b.onkeydown = (e) => { if ((e.key === "Enter" || e.key === " ") && !e.target.closest(".tip")) { e.preventDefault(); b.click(); } };
      b.onclick = (e) => { if (e.target.closest(".tip")) return; ux.method = b.dataset.method; renderMethodCards(); };
    });
    renderClusterParams(true);
  }
  function renderClusterParams(keepOptions) {
    const m = ux.schema.methods[ux.method];
    const optVal = (p) => { const el = $(`#ml-sub-cluster [data-scope="opt"][data-p="${p.name}"]`); return el ? (el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? null : +el.value) : el.value) : p.default; };
    const opts = ux.schema.options.filter((p) => m.k || !["find_k", "k_max"].includes(p.name));
    const vals = Object.fromEntries(opts.map((p) => [p.name, keepOptions ? optVal(p) : p.default]));
    $("#uc-params").innerHTML = m.params.filter((p) => !p.advanced).map((p) => mlField(p, p.default, "method")).join("") +
      opts.filter((p) => !p.advanced).map((p) => mlField(p, vals[p.name], "opt")).join("");
    $("#uc-adv").innerHTML = m.params.filter((p) => p.advanced).map((p) => mlField(p, p.default, "method")).join("") +
      opts.filter((p) => p.advanced).map((p) => mlField(p, vals[p.name], "opt")).join("");
    const mr = $('#ml-sub-cluster [data-p="max_fit_rows"]');
    if (mr) mr.placeholder = `default: ${m.fit_rows.toLocaleString()}`;
    const syncK = () => {
      const fk = $('#ml-sub-cluster [data-p="find_k"]'), km = $('#ml-sub-cluster [data-p="k_max"]')?.closest(".pca-field");
      const nk = $('#ml-sub-cluster [data-scope="method"][data-p="n_clusters"]');
      if (km) km.classList.toggle("hidden", !fk?.checked);
      if (nk) { nk.disabled = !!fk?.checked; nk.title = fk?.checked ? "Chosen automatically (Find the best number of clusters is on)" : ""; }
    };
    $('#ml-sub-cluster [data-p="find_k"]')?.addEventListener("change", syncK);
    syncK();
  }
  $("#uc-reset").onclick = () => { renderClusterParams(false); renderUnsupPrep("uc", true); };
  function renderTsneParams(reset) {
    const prev = reset ? {} : scopeVals("#ml-sub-tsne", "tsne");
    const v = (p) => (p.name in prev ? prev[p.name] : p.default);
    $("#ut-params").innerHTML = ux.schema.tsne.filter((p) => !p.advanced).map((p) => mlField(p, v(p), "tsne")).join("");
    $("#ut-adv").innerHTML = ux.schema.tsne.filter((p) => p.advanced).map((p) => mlField(p, v(p), "tsne")).join("");
  }
  $("#ut-reset").onclick = () => { renderTsneParams(true); renderUnsupPrep("ut", true); };
  const scopeVals = (sel, scope) => {
    const out = {};
    $$(`${sel} [data-scope="${scope}"]`).forEach((i) => { out[i.dataset.p] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : +i.value) : i.value; });
    return out;
  };

  // ---- run
  $("#uc-run").onclick = async () => {
    const err = $("#uc-error"); err.classList.add("hidden");
    const table = $("#uc-table").value, features = unsupFeatures("uc");
    if (!table) return toast("Choose a table first", true);
    if (!features.length) return toast("Tick at least one column to cluster on", true);
    const btn = $("#uc-run"); btn.disabled = true; $("#uc-result").classList.add("hidden");
    try {
      const job = await api("/api/unsup/cluster", { method: "POST", json: {
        table, features, categorical: unsupCats("uc"), method: ux.method, params: scopeVals("#ml-sub-cluster", "method"),
        options: scopeVals("#ml-sub-cluster", "opt"), prep: scopeVals("#uc-prep", "prep"), compare: $("#uc-compare").value || null,
        name: $("#uc-name").value || "clusters" } });
      const done = await trackJob(job, { tool: "ml", save: "cluster", title: `${ux.schema.methods[ux.method].title} clustering` });
      showClusterResult(done.result, $("#uc-result"));
      addItem({ kind: "table", name: done.result.output_table.split(/[\\/]/).pop(), path: done.result.output_table });
      if (done.result.path) refreshModels();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  $("#ut-run").onclick = async () => {
    const err = $("#ut-error"); err.classList.add("hidden");
    const table = $("#ut-table").value, features = unsupFeatures("ut");
    if (!table) return toast("Choose a table first", true);
    if (!features.length) return toast("Tick at least one column to map", true);
    const btn = $("#ut-run"); btn.disabled = true; $("#ut-result").classList.add("hidden");
    try {
      const job = await api("/api/unsup/tsne", { method: "POST", json: {
        table, features, categorical: unsupCats("ut"), params: scopeVals("#ml-sub-tsne", "tsne"), prep: scopeVals("#ut-prep", "prep"),
        color: $("#ut-color").value || null, name: $("#ut-name").value || "tsne" } });
      const done = await trackJob(job, { tool: "ml", save: "tsne", title: "t-SNE map" });
      showTsneResult(done.result, $("#ut-result"));
      addItem({ kind: "table", name: done.result.output_table.split(/[\\/]/).pop(), path: done.result.output_table });
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };

  // ---- canvas scatter plot with hover, legend toggling, categorical or continuous colours
  function scatterPlot(box, { x, y, values, kind, order, colorOf, xlabel = "", ylabel = "", height = 360, tip }) {
    box.innerHTML = `<div class="sc-wrap"><canvas></canvas><div class="sc-tip hidden"></div></div><div class="sc-legend"></div>`;
    const cv = $("canvas", box), tipEl = $(".sc-tip", box), wrap = $(".sc-wrap", box);
    const hidden = new Set();
    const n = x.length;
    let lo = Infinity, hi = -Infinity;
    if (kind === "numeric") values.forEach((v) => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
    const colOf = (v) => kind === "numeric" ? (v == null ? "#9ca3af" : viridis((v - lo) / ((hi - lo) || 1))) : colorOf(v);
    const xmin = Math.min(...x), xmax = Math.max(...x), ymin = Math.min(...y), ymax = Math.max(...y);
    const pad = 28;
    let W = 0, H = height, sx, sy;
    function draw() {
      W = wrap.clientWidth || 400;
      const dpr = window.devicePixelRatio || 1;
      cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
      const g = cv.getContext("2d");
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      const css = getComputedStyle(document.documentElement);
      g.strokeStyle = css.getPropertyValue("--border"); g.lineWidth = 1; g.strokeRect(pad, 6, W - pad - 6, H - pad - 6);
      g.fillStyle = css.getPropertyValue("--muted"); g.font = "11px sans-serif"; g.textAlign = "center";
      g.fillText(xlabel, (W + pad) / 2, H - 8);
      g.save(); g.translate(11, (H - pad) / 2); g.rotate(-Math.PI / 2); g.fillText(ylabel, 0, 0); g.restore();
      sx = (v) => pad + 4 + (v - xmin) / ((xmax - xmin) || 1) * (W - pad - 14);
      sy = (v) => H - pad - 4 - (v - ymin) / ((ymax - ymin) || 1) * (H - pad - 14);
      const r = n > 6000 ? 1.6 : n > 2000 ? 2.1 : 2.8;
      g.globalAlpha = n > 3000 ? 0.6 : 0.75;
      for (let i = 0; i < n; i++) {
        const v = values[i];
        if (kind !== "numeric" && hidden.has(String(v))) continue;
        g.fillStyle = colOf(v);
        g.beginPath(); g.arc(sx(x[i]), sy(y[i]), r, 0, 6.2832); g.fill();
      }
      g.globalAlpha = 1;
    }
    // legend
    const leg = $(".sc-legend", box);
    if (kind === "numeric") {
      leg.innerHTML = `<span class="muted">${fmtv(lo)}</span><span class="sc-grad" style="background:linear-gradient(to right, ${VIRIDIS.join(",")})"></span><span class="muted">${fmtv(hi)}</span>`;
    } else {
      const counts = new Map();
      values.forEach((v) => counts.set(String(v), (counts.get(String(v)) || 0) + 1));
      const keys = order || [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a));
      leg.innerHTML = keys.filter((k) => counts.has(k)).slice(0, 40).map((k) => `<button class="sc-key" data-k="${esc(k)}" title="Click to hide / show"><i style="background:${colorOf(k)}"></i>${esc(k === "" ? "(empty)" : k)} <small>${counts.get(k).toLocaleString()}</small></button>`).join("");
      $$(".sc-key", leg).forEach((b) => b.onclick = () => { const k = b.dataset.k; hidden.has(k) ? hidden.delete(k) : hidden.add(k); b.classList.toggle("off", hidden.has(k)); draw(); });
    }
    // hover: nearest point within 8 px
    cv.onmousemove = (e) => {
      const rc = cv.getBoundingClientRect(), mx = e.clientX - rc.left, my = e.clientY - rc.top;
      let best = -1, bd = 64;
      for (let i = 0; i < n; i++) {
        if (kind !== "numeric" && hidden.has(String(values[i]))) continue;
        const dx = sx(x[i]) - mx, dy = sy(y[i]) - my, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = i; }
      }
      if (best < 0) { tipEl.classList.add("hidden"); return; }
      tipEl.innerHTML = tip ? tip(best) : esc(String(values[best]));
      tipEl.classList.remove("hidden");
      tipEl.style.left = Math.min(mx + 12, W - tipEl.offsetWidth - 4) + "px";
      tipEl.style.top = Math.max(4, my - tipEl.offsetHeight - 8) + "px";
    };
    cv.onmouseleave = () => tipEl.classList.add("hidden");
    draw();
    const ro = new ResizeObserver(() => { if (wrap.clientWidth && Math.abs(wrap.clientWidth - W) > 2) draw(); });
    ro.observe(wrap);
  }

  // ---- small SVG line chart
  function lineChart(xs, series, { xlabel = "", mark = null, h = 170, fmtY = (v) => fmtv(v) } = {}) {
    const W = 340, H = h, L = 44, B = 28, T = 10, R = 10;
    const all = series.flatMap((s) => s.ys.filter((v) => v != null && isFinite(v)));
    if (!all.length) return "";
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...all), y1 = Math.max(...all);
    const X = (v) => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), Y = (v) => H - B - (v - y0) / ((y1 - y0) || 1) * (H - B - T);
    let out = `<svg viewBox="0 0 ${W} ${H}" class="ev-chart lc">`;
    out += `<rect x="${L}" y="${T}" width="${W - L - R}" height="${H - B - T}" fill="none" stroke="var(--border)"/>`;
    [y0, (y0 + y1) / 2, y1].forEach((v) => out += `<text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" class="lc-t">${esc(fmtY(v))}</text>`);
    xs.forEach((v, i) => { if (xs.length <= 16 || i % Math.ceil(xs.length / 12) === 0) out += `<text x="${X(v)}" y="${H - B + 13}" text-anchor="middle" class="lc-t">${esc(fmtv(v))}</text>`; });
    out += `<text x="${(L + W - R) / 2}" y="${H - 3}" text-anchor="middle" class="lc-t">${esc(xlabel)}</text>`;
    if (mark != null) out += `<line x1="${X(mark)}" x2="${X(mark)}" y1="${T}" y2="${H - B}" stroke="var(--accent)" stroke-dasharray="4 3"/>`;
    series.forEach((s) => {
      const pts = xs.map((v, i) => s.ys[i] != null && isFinite(s.ys[i]) ? `${X(v).toFixed(1)},${Y(s.ys[i]).toFixed(1)}` : null).filter(Boolean);
      out += `<polyline points="${pts.join(" ")}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
      if (xs.length <= 40) xs.forEach((v, i) => { if (s.ys[i] != null && isFinite(s.ys[i])) out += `<circle cx="${X(v)}" cy="${Y(s.ys[i])}" r="3" fill="${s.color}"><title>${esc(xlabel)} ${v}: ${esc(fmtY(s.ys[i]))}</title></circle>`; });
    });
    return out + "</svg>";
  }

  // ---- clustering result
  function showClusterResult(r, box) {
    const q = r.quality || {}, k = r.n_clusters, noiseIdx = r.noise ? r.classes.length - 1 : -1;
    const color = (i) => clColor(i, k, i === noiseIdx);
    const grade = (v, good, ok, lowBetter) => v == null ? "" : lowBetter ? (v <= good ? "good" : v <= ok ? "ok" : "bad") : (v >= good ? "good" : v >= ok ? "ok" : "bad");
    const tiles = [
      [`${k}`, "Clusters", "Number of groups found" + (r.k_search ? " (best silhouette)" : ""), ""],
      r.noise ? [`${(100 * r.noise / r.rows_used).toFixed(1)}%`, "Noise", `${r.noise.toLocaleString()} rows belong to no cluster`, ""] : null,
      q.silhouette != null ? [q.silhouette.toFixed(3), "Silhouette", "How well each row fits its own cluster vs the nearest other one: −1 to 1. Above 0.5 clear clusters, 0.25–0.5 reasonable, below 0.25 overlapping.", grade(q.silhouette, 0.5, 0.25)] : null,
      q.davies_bouldin != null ? [q.davies_bouldin.toFixed(2), "Davies-Bouldin", "Average similarity between each cluster and its closest neighbour. Lower is better (0 = perfectly separated).", grade(q.davies_bouldin, 0.7, 1.5, true)] : null,
      q.calinski_harabasz != null ? [fmtv(q.calinski_harabasz), "Calinski-Harabasz", "Between-cluster vs within-cluster spread. Higher is better; compare runs on the same data.", ""] : null,
    ].filter(Boolean).map(([v, l, t, g]) => `<div class="metric ${g}"><b>${v}</b><span>${l} ${tipBtn(t)}</span></div>`).join("");
    const maxSize = Math.max(1, ...r.sizes);
    const sizes = r.classes.map((c, i) => `<div class="imp-row"><span><i class="sw" style="background:${color(i)}"></i>${esc(c)}</span><span><span class="imp-bar" style="display:block;width:${Math.max(1, 100 * r.sizes[i] / maxSize)}%;background:${color(i)}"></span></span><span>${r.sizes[i].toLocaleString()} <small class="muted">${(100 * r.sizes[i] / r.rows_used).toFixed(1)}%</small></span></div>`).join("");
    // profiles heatmap (z-scores), categorical top values
    const P = r.profiles, zc = (z) => z == null ? "transparent" : z > 0 ? `rgba(220,38,38,${Math.min(0.85, Math.abs(z) / 2)})` : `rgba(37,99,235,${Math.min(0.85, Math.abs(z) / 2)})`;
    const prof = P.numeric.length || P.categorical.length ? `<div class="home-label" style="margin-top:14px">Cluster profiles ${tipBtn("Average value of each column per cluster, in the original units. Colour = how far it is from the overall average (red above, blue below, in standard deviations). This tells you what makes each cluster different.")}</div>
      <div class="load-wrap"><table class="cm-table prof"><tr><th></th>${r.classes.map((c, i) => `<th title="${esc(c)}"><i class="sw" style="background:${color(i)}"></i>${esc(c.replace("Cluster ", "C"))}</th>`).join("")}<th class="muted">All</th></tr>
      ${P.numeric.map((f) => `<tr><th class="rowh" title="${esc(f.feature)}">${esc(f.feature)}</th>${f.means.map((v, i) => `<td style="background:${zc(f.z[i])};color:${Math.abs(f.z[i] || 0) > 1.2 ? "#fff" : "inherit"}" title="${esc(r.classes[i])}: mean ${fmtv(v)} (${f.z[i] == null ? "–" : (f.z[i] > 0 ? "+" : "") + f.z[i].toFixed(2)} SD)">${v == null ? "–" : fmtv(v)}</td>`).join("")}<td class="muted">${fmtv(f.overall)}</td></tr>`).join("")}
      ${P.categorical.map((f) => `<tr><th class="rowh" title="${esc(f.feature)}">${esc(f.feature)}</th>${f.top.map((t) => `<td title="${t ? `${esc(t[0])}: ${(100 * t[1]).toFixed(0)}% of the cluster` : ""}">${t ? `${esc(String(t[0]).slice(0, 10))} <small>${(100 * t[1]).toFixed(0)}%</small>` : "–"}</td>`).join("")}<td></td></tr>`).join("")}
      </table></div>` : "";
    // comparison with a known label
    const C = r.comparison;
    const comp = C ? `<div class="home-label" style="margin-top:14px">Clusters vs “${esc(C.column)}” ${tipBtn("How well the clusters match the known label (the label was not used for clustering). Adjusted Rand index: 0 = random, 1 = identical grouping. NMI: shared information, 0–1. Homogeneity: each cluster holds one label. Completeness: each label sits in one cluster.")}</div>
      <div class="metric-tiles small-tiles">${[["ARI", C.ari], ["NMI", C.nmi], ["Homogeneity", C.homogeneity], ["Completeness", C.completeness]].map(([l, v]) => `<div class="metric ${grade(v, 0.6, 0.3)}"><b>${v.toFixed(3)}</b><span>${l}</span></div>`).join("")}</div>
      <div class="load-wrap"><table class="cm-table"><tr><th></th>${C.labels.map((l) => `<th title="${esc(l)}">${esc(String(l).slice(0, 10))}</th>`).join("")}<th>Most common</th></tr>
      ${C.table.map((row, i) => { const tot = row.reduce((a, b) => a + b, 0) || 1; return `<tr><th class="rowh"><i class="sw" style="background:${color(i)}"></i>${esc(r.classes[i])}</th>${row.map((v) => `<td style="background:rgba(31,122,90,${(0.08 + 0.8 * v / tot).toFixed(2)});color:${v / tot > 0.55 ? "#fff" : "inherit"}" title="${v.toLocaleString()} rows (${(100 * v / tot).toFixed(0)}% of the cluster)">${v ? v.toLocaleString() : ""}</td>`).join("")}<td><b>${esc(C.majority[i] ?? "–")}</b></td></tr>`; }).join("")}
      </table></div>` : "";
    const ks = r.k_search ? `<div class="home-label" style="margin-top:14px">Choosing the number of clusters ${tipBtn(`Every k from 2 to ${Math.max(...r.k_search.k)} was tried on ${r.k_search.rows.toLocaleString()} rows. The highest silhouette (dashed line) was used.` + (r.k_search.extra_name ? ` ${r.k_search.extra_name === "inertia" ? "Inertia (within-cluster spread) always falls as k grows; look for the 'elbow' where it flattens." : "BIC: lower is better; balances fit against complexity."}` : ""))}</div>
      <div class="chart-pair"><div>${lineChart(r.k_search.k, [{ ys: r.k_search.silhouette, color: "var(--accent)" }], { xlabel: "k (silhouette)", mark: r.k_search.best_k, fmtY: (v) => v.toFixed(2) })}</div>
      ${r.k_search.extra_name ? `<div>${lineChart(r.k_search.k, [{ ys: r.k_search.extra, color: "#d97706" }], { xlabel: `k (${r.k_search.extra_name})`, mark: r.k_search.best_k })}</div>` : ""}</div>` : "";
    const kd = r.k_distance ? `<div class="home-label" style="margin-top:14px">k-distance curve ${tipBtn("Distance from each row to its k-th nearest neighbour (k = min samples), sorted. The bend (knee) is a good eps: rows to the right of it are sparse (noise). Dashed line: eps used.")}</div>
      ${lineChart(r.k_distance.positions, [{ ys: r.k_distance.distances, color: "var(--accent)" }], { xlabel: `rows sorted by distance · eps used = ${fmtv(r.params.eps)}`, mark: r.k_distance.positions.reduce((b, p, i) => r.k_distance.distances[i] <= r.params.eps ? p : b, 0) })}` : "";
    box.innerHTML = `<div class="card">
      <div class="row between"><h2 style="margin:0">✓ ${esc(r.method_title)}: ${k} cluster${k === 1 ? "" : "s"}</h2><span class="task-badge">unsupervised</span></div>
      <div class="pca-sum" style="margin-top:4px">${r.rows_used.toLocaleString()} rows clustered${r.rows_fitted < r.rows_used ? ` (fitted on ${r.rows_fitted.toLocaleString()}, the rest assigned by ${esc(r.assigned_by)})` : ""}${r.rows_skipped ? ` · ${r.rows_skipped.toLocaleString()} rows skipped (missing values)` : ""} · ${r.features.length} columns · ${r.seconds} s</div>
      ${r.prep_steps?.length ? `<div class="prep-res"><b>Preprocessing</b> ${r.prep_steps.map(esc).join(" → ")}${r.method === "dbscan" ? ` · eps = ${fmtv(r.params.eps)}` : ""}${r.method === "hdbscan" ? ` · min cluster size = ${r.params.min_cluster_size}` : ""}</div>` : ""}
      ${r.warnings?.length ? `<div class="warn">${r.warnings.map(esc).join("<br>")}</div>` : ""}
      <div class="metric-tiles" style="margin-top:10px">${tiles}</div>
      ${ks}
      <div class="home-label" style="margin-top:14px">Cluster sizes</div>${sizes}
      <div class="home-label" style="margin-top:14px">2D view ${tipBtn("The rows projected onto their first two principal components (PCA), coloured by cluster. Overlap here doesn't always mean overlap in all columns. For a clearer map, use t-SNE.")}</div>
      <div class="uc-scatter"></div>
      ${prof}${comp}
      ${r.dendrogram ? `<div class="home-label" style="margin-top:14px">Dendrogram ${tipBtn(`The merge tree of ${r.dendrogram.rows.toLocaleString()} sample rows (last 30 merges). Height = distance at which groups merge. The dashed line is where the tree was cut into ${k} clusters; long vertical lines mean well-separated groups.`)}</div><div class="dendro">${dendroSvg(r.dendrogram)}</div>` : ""}
      ${kd}
      <div class="row" style="flex-wrap:wrap;margin-top:12px">
        <button class="btn small primary" data-open-out>Open table with cluster column</button>
        <button class="btn small" data-tsne-out>t-SNE map of these clusters</button>
        <button class="btn small" data-train-out title="Train a supervised model that learns these clusters">Train a model on the clusters</button>
        ${r.path ? `<button class="btn small" data-classify="${esc(r.path)}">Cluster an image with this →</button>` : ""}
        <a class="btn small" href="/api/tables/file?path=${encodeURIComponent(r.output_table)}" download>⬇ Table</a></div>
      <p class="hint">Saved as <code>${esc(r.output_table)}</code> with a <b>cluster</b> column (1…${k}${r.noise ? ", 0 = noise" : ""}${r.rows_skipped ? ", empty = skipped" : ""})${r.has_probability ? " and cluster_probability" : ""}.${r.path ? ` Model: <code>${esc(r.path)}</code>.` : ""}</p></div>`;
    box.classList.remove("hidden");
    const v = r.view;
    scatterPlot($(".uc-scatter", box), { x: v.x, y: v.y, values: v.cluster.map((c) => r.classes[c]), kind: "categorical", order: r.classes,
      colorOf: (name) => color(r.classes.indexOf(name)), xlabel: `PC1 (${(100 * v.explained[0]).toFixed(0)}%)`, ylabel: `PC2 (${(100 * (v.explained[1] || 0)).toFixed(0)}%)` });
    $("[data-open-out]", box).onclick = () => previewTable(r.output_table);
    $("[data-tsne-out]", box).onclick = async () => {
      openMlSub("tsne");
      await refreshUnsup("ut", r.output_table);
      const pg = pageOf("ut");
      Object.keys(pg.cols).forEach((c) => pg.cols[c].use = r.features.includes(c));
      r.categorical?.forEach((c) => pg.cols[c] && (pg.cols[c].cat = true));
      renderUnsupCols("ut");
      $("#ut-color").value = "cluster";
      toast("Same columns selected, coloured by cluster. Press Make t-SNE map.");
    };
    $("[data-train-out]", box).onclick = () => { switchTool("ml"); openMlSub("train"); refreshTrainTables(r.output_table); };
    $("[data-classify]", box)?.addEventListener("click", () => { openMlSub("predict"); refreshPredict(r.path); });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function dendroSvg(d) {
    const W = 340, H = 200, L = 40, B = 34, T = 8, R = 6;
    const xs = d.icoord.flat(), xmax = Math.max(...xs), xmin = Math.min(...xs), ymax = d.max || Math.max(...d.dcoord.flat());
    const X = (v) => L + (v - xmin) / ((xmax - xmin) || 1) * (W - L - R), Y = (v) => H - B - v / (ymax || 1) * (H - B - T);
    let s = `<svg viewBox="0 0 ${W} ${H}" class="ev-chart lc">`;
    [0, ymax / 2, ymax].forEach((v) => s += `<text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" class="lc-t">${esc(fmtv(v))}</text>`);
    d.icoord.forEach((ic, i) => { const dc = d.dcoord[i]; s += `<polyline points="${ic.map((x, j) => `${X(x).toFixed(1)},${Y(dc[j]).toFixed(1)}`).join(" ")}" fill="none" stroke="var(--text)" stroke-width="1.2" opacity=".8"/>`; });
    if (d.cut) s += `<line x1="${L}" x2="${W - R}" y1="${Y(d.cut)}" y2="${Y(d.cut)}" stroke="#dc2626" stroke-dasharray="5 3"><title>cut height ${fmtv(d.cut)}</title></line>`;
    const step = (W - L - R) / d.leaves.length;
    d.leaves.forEach((lbl, i) => s += `<text transform="translate(${L + step * (i + 0.5)} ${H - B + 8}) rotate(-60)" text-anchor="end" class="lc-t" style="font-size:8px">${esc(lbl)}</text>`);
    return s + "</svg>";
  }

  // ---- t-SNE result
  function showTsneResult(r, box) {
    const keys = Object.keys(r.colors);
    box.innerHTML = `<div class="card">
      <div class="row between"><h2 style="margin:0">✓ t-SNE map</h2><span class="task-badge">unsupervised</span></div>
      <div class="pca-sum" style="margin-top:4px">${r.rows_mapped.toLocaleString()} rows mapped${r.rows_mapped < r.rows_used ? ` (random sample of ${r.rows_used.toLocaleString()})` : ""} · ${r.features.length} columns · perplexity ${fmtv(r.params.perplexity)} · ${r.iterations} iterations · ${r.seconds} s</div>
      ${r.prep_steps?.length ? `<div class="prep-res"><b>Preprocessing</b> ${r.prep_steps.map(esc).join(" → ")}</div>` : ""}
      <div class="metric-tiles small-tiles" style="margin-top:10px">
        <div class="metric ${r.trustworthiness >= 0.9 ? "good" : r.trustworthiness >= 0.8 ? "ok" : "bad"}"><b>${r.trustworthiness.toFixed(3)}</b><span>Trustworthiness ${tipBtn("How well neighbours on the map are also neighbours in the data (0–1). Above 0.9 = the map is faithful.")}</span></div>
        <div class="metric"><b>${r.kl_divergence.toFixed(2)}</b><span>KL divergence ${tipBtn("t-SNE's final error. Lower is better; compare runs on the same data only.")}</span></div></div>
      <label style="margin-top:10px">Colour by <select class="ut-colsel">${keys.map((k) => `<option ${k === r.color ? "selected" : ""}>${esc(k)}</option>`).join("")}<option value="">None</option></select></label>
      <div class="ut-scatter"></div>
      <p class="hint">Distances between far-apart groups and group sizes are not meaningful in t-SNE; what matters is which points sit together. Hover a point for its values.</p>
      <div class="row" style="flex-wrap:wrap"><button class="btn small primary" data-open-out>Open table with map coordinates</button>
        <a class="btn small" href="/api/tables/file?path=${encodeURIComponent(r.output_table)}" download>⬇ Table</a></div>
      <p class="hint">Saved as <code>${esc(r.output_table)}</code> (the mapped rows plus <b>tsne_1</b>, <b>tsne_2</b>).</p></div>`;
    box.classList.remove("hidden");
    const draw = () => {
      const c = $(".ut-colsel", box).value, col = r.colors[c];
      const values = col ? col.values : r.x.map(() => "all rows");
      const kind = col?.kind === "numeric" ? "numeric" : "categorical";
      const cats = kind === "categorical" ? [...new Set(values.map(String))] : [];
      const counts = new Map(); values.forEach((v) => counts.set(String(v), (counts.get(String(v)) || 0) + 1));
      cats.sort((a, b) => counts.get(b) - counts.get(a));
      scatterPlot($(".ut-scatter", box), { x: r.x, y: r.y, values: kind === "numeric" ? values : values.map(String), kind, order: cats,
        colorOf: (v) => (/^noise$|^0$/i.test(v) && c === "cluster") ? "#9ca3af" : CL_PAL[cats.indexOf(String(v)) % CL_PAL.length],
        xlabel: "t-SNE 1", ylabel: "t-SNE 2", height: 440,
        tip: (i) => `<b>row ${r.row_ids[i] + 1}</b>` + keys.slice(0, 8).map((k) => `<div>${esc(k)}: <b>${esc(String(r.colors[k].values[i] ?? "–"))}</b></div>`).join("") });
    };
    $(".ut-colsel", box).onchange = draw;
    draw();
    $("[data-open-out]", box).onclick = () => previewTable(r.output_table);
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------ Classical ML for raster: image + ground truth → model + map
  const rm = { schema: null, layer: null, kind: null, model: "rf", ready: false };
  async function initRasterMl() {
    if (!mlx.schema) mlx.schema = await api("/api/ml/schema");
    if (!rm.schema) rm.schema = await api("/api/rasterml/schema");
    if (!rm.ready) { rm.ready = true; renderRmModels(); renderRmSettings(true); }
  }
  async function refreshRm() {
    try { await initRasterMl(); } catch (e) { return toast(e.message, true); }
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    const sel = $("#rm-input"), cur = rm.layer?.id || sel.value;
    sel.innerHTML = `<option value="">${rasters.length ? "Choose a raster layer…" : "No raster layers yet: add one with + Add data"}</option>` +
      rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)} · ${l.info?.count ?? "?"} bands</option>`).join("");
    if (cur && rasters.some((l) => l.id === cur)) sel.value = cur;
    else if (!rm.layer && rasters.length === 1) sel.value = rasters[0].id;
    const l = getLayer(sel.value);
    if (l !== rm.layer) { rm.layer = l || null; renderRmImage(); }
    // ground truth: any other raster (class map) or vector layer
    const gt = $("#rm-gt"), gcur = gt.value;
    const vecs = layers.filter((x) => x.type === "vector");
    gt.innerHTML = `<option value="">Choose the ground truth…</option>` +
      (vecs.length ? `<optgroup label="Polygons / points (shapefile, GeoJSON, training samples)">${vecs.map((x) => `<option value="${esc(x.id)}">${esc(x.name)} (${x.geojson.features.length} features)</option>`).join("")}</optgroup>` : "") +
      (rasters.filter((x) => x !== rm.layer).length ? `<optgroup label="Class raster (e.g. a land-cover map)">${rasters.filter((x) => x !== rm.layer).map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</optgroup>` : "");
    const auto = vecs.find((x) => x.samples) || (vecs.filter((x) => x.id !== "aoi").length === 1 ? vecs.find((x) => x.id !== "aoi") : null);
    gt.value = [...gt.options].some((o) => o.value === gcur) && gcur ? gcur : (auto ? auto.id : "");
    renderRmGt();
  }
  $("#rm-input").onchange = () => { rm.layer = getLayer($("#rm-input").value); renderRmImage(); refreshRm(); if (rm.layer) selectLayer(rm.layer.id); };

  function renderRmImage() {
    const l = rm.layer;
    $("#rm-kind").classList.add("hidden");
    if (!l) { $("#rm-info").textContent = ""; $("#rm-bands").innerHTML = ""; $("#rm-bands-sum").textContent = "Bands"; return; }
    const info = l.info;
    $("#rm-info").textContent = `${info.count} bands · ${info.width.toLocaleString()} × ${info.height.toLocaleString()} px · pixel ${fmt(info.res[0], info.res[0] < 1 ? 3 : 1)}${/4326/.test(info.crs) ? "°" : " m"} · ${info.crs}`;
    $("#rm-bands").innerHTML = info.bands.map((b) => `<label title="Band ${b.index}: ${esc(b.description)}"><input type="checkbox" value="${b.index}" checked>${esc(b.description !== `Band ${b.index}` ? b.description : String(b.index))}</label>`).join("");
    $$("#rm-bands input").forEach((i) => i.onchange = rmBandsChanged);
    const identity = (l.scale ?? 1) === 1 && (l.offset ?? 0) === 0;
    $("#rm-refl").checked = !identity; $("#rm-refl").disabled = identity;
    const r0 = info.res[0], unit = /4326/.test(info.crs) ? "°" : " m";
    $("#rm-factor").innerHTML = [1, 2, 4, 8].map((f) => `<option value="${f}">${f === 1 ? `Native (${fmt(r0, r0 < 1 ? 3 : 0)}${unit})` : `${f}× coarser (${fmt(r0 * f, r0 < 1 ? 3 : 0)}${unit})`}</option>`).join("");
    $("#rm-name").value = safeName(l.name.replace(/\.(tiff?|vrt)$/i, "")).slice(0, 40) + "_classified";
    $("#rm-bands-wrap").open = info.count <= 16;
    rmBandsChanged();
  }
  const rmBands = () => $$("#rm-bands input:checked").map((i) => +i.value);
  let rmKindTimer = 0;
  function rmBandsChanged() {
    const n = rmBands().length, total = $$("#rm-bands input").length;
    $("#rm-bands-sum").innerHTML = `Bands: <b>${n}</b> of ${total} used`;
    clearTimeout(rmKindTimer);
    rmKindTimer = setTimeout(detectRmKind, 250);
  }
  $("#rm-bands-all").onclick = () => { $$("#rm-bands input").forEach((i) => i.checked = true); rmBandsChanged(); };
  $("#rm-bands-none").onclick = () => { $$("#rm-bands input").forEach((i) => i.checked = false); rmBandsChanged(); };
  $("#rm-range").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const keep = new Set();
    for (const part of $("#rm-range").value.split(",")) {
      const m = part.trim().match(/^(\d+)\s*(?:-|–|to)\s*(\d+)$/) || part.trim().match(/^(\d+)$/);
      if (!m) continue;
      const a = +m[1], b = +(m[2] ?? m[1]);
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) keep.add(i);
    }
    if (!keep.size) return toast("Type band numbers or ranges, e.g. 1-100, 120-180", true);
    $$("#rm-bands input").forEach((i) => i.checked = keep.has(+i.value));
    rmBandsChanged();
  });
  async function detectRmKind() {
    const l = rm.layer, bands = rmBands();
    if (!l || !bands.length) { $("#rm-kind").classList.add("hidden"); return; }
    try { rm.kind = await api(`/api/rasterml/inspect?path=${encodeURIComponent(l.path)}&bands=${bands.length === l.info.count ? "" : bands.join(",")}`); }
    catch (e) { rm.kind = null; return; }
    const k = rm.kind, titles = rm.kind.models.map((m) => mlx.schema.models[m]?.title || m);
    $("#rm-kind").innerHTML = `<div class="rm-kind-head"><span class="rm-kind-tag k-${k.kind}">${esc(k.title)}</span><small>${esc(k.reason)}</small></div>
      <div class="hint" style="margin-top:4px">${esc(k.note)}</div>
      <div class="hint" style="margin-top:4px">Suggested: <b>${titles.map(esc).join(", ")}</b>${k.scaling === "none" ? " · no feature scaling" : ""}${k.reduce === "auto" ? " · PCA band reduction for Maximum Likelihood / LDA" : ""}</div>
      <button class="chip" id="rm-apply" style="margin-top:6px">Use recommended settings</button>`;
    $("#rm-kind").classList.remove("hidden");
    $("#rm-apply").onclick = () => applyRmRecommended(true);
    renderRmModels();
    if (!rm.touched) applyRmRecommended(false);
  }
  function applyRmRecommended(pickModel) {
    const k = rm.kind;
    if (!k) return;
    if (pickModel && k.models[0]) { rm.model = k.models[0]; renderRmModels(); }
    renderRmSettings(true);
    if (pickModel) toast(`Settings for ${k.title.toLowerCase()} applied`);
  }

  function renderRmGt() {
    const l = getLayer($("#rm-gt").value);
    $("#rm-gt-raster").classList.toggle("hidden", l?.type !== "raster");
    $("#rm-gt-vector").classList.toggle("hidden", l?.type !== "vector");
    $("#rm-gt-classes").textContent = "";
    if (l?.type === "raster") {
      $("#rm-gt-band").innerHTML = l.info.bands.map((b) => `<option value="${b.index}">Band ${b.index}${b.description !== `Band ${b.index}` ? " · " + esc(b.description) : ""}</option>`).join("");
      const lg = l.legend?.kind === "classes" ? l.legend.classes : null;
      $("#rm-gt-classes").textContent = lg ? `${lg.length} classes: ${lg.slice(0, 8).map((c) => c.name).join(", ")}${lg.length > 8 ? " …" : ""}` : "Each distinct pixel value is a class.";
    } else if (l?.type === "vector") {
      const keys = [...new Set(l.geojson.features.flatMap((f) => Object.keys(f.properties || {})))].filter((k) => !k.startsWith("_"));
      const pref = keys.find((k) => /^(class|label|lulc|lc|landcover|type|category|code)$/i.test(k)) || keys[0];
      $("#rm-gt-field").innerHTML = keys.length ? keys.map((k) => `<option ${k === pref ? "selected" : ""}>${esc(k)}</option>`).join("") : `<option value="">(no attributes)</option>`;
      rmGtClasses();
    }
  }
  function rmGtClasses() {
    const l = getLayer($("#rm-gt").value), f = $("#rm-gt-field").value;
    if (l?.type !== "vector") return;
    const cnt = new Map();
    l.geojson.features.forEach((ft) => { const v = ft.properties?.[f]; if (v != null && v !== "") cnt.set(String(v), (cnt.get(String(v)) || 0) + 1); });
    const entries = [...cnt.entries()].sort((a, b) => b[1] - a[1]);
    const few = entries.filter(([, n]) => n < 2).map(([k]) => k);
    $("#rm-gt-classes").innerHTML = !entries.length ? `<span style="color:var(--warn)">No values in this attribute.</span>` :
      `${entries.length} classes: ${entries.slice(0, 10).map(([k, n]) => `${esc(k)} <small>(${n})</small>`).join(", ")}${entries.length > 10 ? " …" : ""}` +
      (entries.length < 2 ? `<br><span style="color:var(--warn)">At least two classes are needed.</span>` : "") +
      (few.length ? `<br><span style="color:var(--warn)">Only one polygon / point for: ${few.slice(0, 5).map(esc).join(", ")}. Draw more for an honest accuracy.</span>` : "");
  }
  $("#rm-gt").onchange = renderRmGt;
  $("#rm-gt-field").onchange = rmGtClasses;

  function renderRmModels() {
    const sc = mlx.schema, kind = rm.kind?.kind, rec = rm.kind?.models || [];
    $("#rm-models").innerHTML = rm.schema.models.filter((k) => sc.models[k] && !sc.unavailable.includes(k)).map((k) => {
      const m = sc.models[k], good = (rm.schema.good_for[k] || []).map((g) => rm.schema.kinds[g]?.title.replace(/ image$/, "").replace("Pixel ", "")).join(" · ");
      return `<div role="button" tabindex="0" aria-pressed="${k === rm.model}" class="model-card ${k === rm.model ? "on" : ""}" data-model="${k}">
        ${rec.includes(k) ? `<span class="rec" title="Suggested for this ${esc((rm.kind.title || "").toLowerCase())}">★ SUGGESTED</span>` : ""}
        <span class="fam">${esc(m.family)}</span>
        <span class="mc-top"><b>${esc(m.title)}</b>${tipBtn(m.tip)}</span>
        <span class="stars"><span>Accuracy <b>${stars(m.accuracy)}</b></span><span>Speed <b>${stars(m.speed)}</b></span></span>
        <small>${esc(m.desc)}</small><small class="good-for">Good for: ${esc(good)}</small></div>`;
    }).join("");
    $$("#rm-models .model-card").forEach((b) => {
      b.onkeydown = (e) => { if ((e.key === "Enter" || e.key === " ") && !e.target.closest(".tip")) { e.preventDefault(); b.click(); } };
      b.onclick = (e) => { if (e.target.closest(".tip")) return; rm.model = b.dataset.model; rm.touched = true; renderRmModels(); renderRmSettings(false); };
    });
  }
  // settings: the model's parameters, preprocessing and validation, with recommendations for the detected kind
  function renderRmSettings(useRecommended) {
    const sc = mlx.schema, m = sc.models[rm.model], k = rm.kind;
    const recP = (useRecommended && k?.params?.[rm.model]) || {};
    const fits = (p) => !p.tasks || p.tasks.includes("classification");
    $("#rm-params").innerHTML = m.params.filter(fits).map((p) => mlField(p, p.name in recP ? recP[p.name] : p.default, "model")).join("") ||
      `<p class="hint">${esc(m.title)} has no settings: it works as it is.</p>`;
    const prev = useRecommended ? {} : scopeVals("#rm-prep", "common");
    const recPrep = k ? { scaling: k.scaling, reduce: k.reduce === "auto" && !["mlc", "lda", "nb", "knn", "svm", "mlp"].includes(rm.model) ? "none" : k.reduce } : {};
    const prep = sc.common.filter((p) => p.group === "prep" && fits(p) && !["target_transform", "drop_correlated"].includes(p.name));
    $("#rm-prep").innerHTML = prep.map((p) => mlField(p, p.name in prev ? prev[p.name] : (p.name in recPrep ? recPrep[p.name] : p.default), "common")).join("");
    const sync = () => { const r = $('#rm-prep [data-p="outlier_pct"]')?.closest(".pca-field"); if (r) r.classList.toggle("hidden", $('#rm-prep [data-p="outliers"]').value !== "clip"); };
    $('#rm-prep [data-p="outliers"]')?.addEventListener("change", sync);
    sync();
    const prevAdv = scopeVals("#rm-adv", "common");
    const adv = sc.common.filter((p) => !p.group && fits(p) && !["cv_folds"].includes(p.name));
    $("#rm-adv").innerHTML = adv.map((p) => mlField(p, p.name in prevAdv ? prevAdv[p.name] : p.default, "common")).join("");
    const mr = $('#rm-adv [data-p="max_train_rows"]');
    if (mr) mr.placeholder = `model default: ${m.max_rows.toLocaleString()}`;
  }
  $("#rm-reset").onclick = () => { rm.touched = false; applyRmRecommended(true); };

  $("#rm-run").onclick = async () => {
    const err = $("#rm-error"); err.classList.add("hidden");
    const l = rm.layer, gl = getLayer($("#rm-gt").value), bands = rmBands();
    if (!l) return toast("Choose the image first", true);
    if (!bands.length) return toast("Use at least one band", true);
    if (!gl) return toast("Choose the ground truth (polygons / points or a class raster)", true);
    const ground_truth = gl.type === "raster" ? { type: "raster", path: gl.path, band: +$("#rm-gt-band").value || 1 }
      : { type: "vector", geojson: gl.geojson, field: $("#rm-gt-field").value || null };
    const refl = $("#rm-refl").checked, pc = +$("#rm-pc").value;
    const tuning = $("#rm-tune").checked ? { enabled: true, method: "random", iter: 12, folds: 3, metric: "auto",
      space: Object.fromEntries(Object.entries(mlx.schema.search[rm.model] || {}).map(([k2, v]) => [k2, v.map((x) => x === null ? "None" : String(x).replace(/,/g, ";"))])) } : {};
    if (tuning.enabled && !Object.keys(tuning.space).length) tuning.enabled = false;
    const body = {
      path: l.path, bands: bands.length === l.info.count ? null : bands, ground_truth, model: rm.model,
      params: scopeVals("#rm-params", "model"), common: { ...scopeVals("#rm-prep", "common"), ...scopeVals("#rm-adv", "common") }, tuning,
      clip: getClip("rm-area"), map_whole: $("#rm-map-whole").checked, factor: +$("#rm-factor").value || 1,
      scale: refl ? (l.scale ?? 1) : 1, offset: refl ? (l.offset ?? 0) : 0, per_class: pc || null,
      name: $("#rm-name").value || "classified", class_colors: gl.samples ? gl.classColors : null,
      confidence: $("#rm-conf").checked, resolution: $("#rm-res").value,
    };
    const btn = $("#rm-run"); btn.disabled = true; $("#rm-result").classList.add("hidden");
    try {
      const job = await api("/api/rasterml/run", { method: "POST", json: body });
      const done = await trackJob(job, { tool: "rasterml", save: "rasterml", title: `${mlx.schema.models[rm.model].title} · ${body.name}` });
      const r = done.result;
      if (r.path) await addRasterFromPath(r.path, { name: body.name, zoom: false });
      showRmResult(r);
      refreshModels();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  function showRmResult(r) {
    const box = $("#rm-result"), s = r.samples, k = r.kind;
    const counts = Object.entries(s.classes).sort((a, b) => b[1] - a[1]), max = Math.max(1, ...counts.map(([, n]) => n));
    box.innerHTML = `<div class="card rm-head-card">
        <div class="row between"><h2 style="margin:0">✓ Map ready</h2><span class="rm-kind-tag k-${k.kind}">${esc(k.title)}</span></div>
        <div class="pca-sum" style="margin-top:4px">${s.pixels.toLocaleString()} training pixels from ${counts.length} classes · ${s.bands} bands · ${r.seconds} s total
          ${r.map ? ` · map ${r.map.width.toLocaleString()} × ${r.map.height.toLocaleString()} px${r.map.factor > 1 ? ` (${r.map.factor}× coarser)` : ""}` : ""}</div>
        <div class="home-label" style="margin-top:10px">Training pixels per class</div>
        <div class="dist">${counts.slice(0, 20).map(([c, n]) => `<div style="grid-template-columns:1fr 3fr auto"><span>${esc(c)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * n / max)}%"></span></span><b>${n.toLocaleString()}</b></div>`).join("")}</div>
        <p class="hint">The map was added to Contents${r.map?.confidence ? " (band 2 = confidence %)" : ""}. The model is listed under Classical ML ▸ Your models, so you can apply it to other images with <b>Classify an image</b>.</p>
      </div><div id="rm-train-res"></div>`;
    box.classList.remove("hidden");
    showTrainResult(r.model, $("#rm-train-res"));
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function initMlTrain() {
    mlx.schema = await api("/api/ml/schema");
    wireGotoRt();
  }

  // ------------------------------------------------------------------ Training samples: draw labelled polygons / points
  const TS_PRESETS = {
    lulc: [["Water", "#1f78b4"], ["Trees", "#1b7837"], ["Cropland", "#e6c229"], ["Built-up", "#e31a1c"], ["Bare ground", "#b9a37e"], ["Grass / shrub", "#9acd32"]],
    worldcover: [["Tree cover", "#006400"], ["Shrubland", "#ffbb22"], ["Grassland", "#ffff4c"], ["Cropland", "#f096ff"], ["Built-up", "#fa0000"],
                 ["Bare / sparse vegetation", "#b4b4b4"], ["Snow and ice", "#f0f0f0"], ["Permanent water bodies", "#0064c8"],
                 ["Herbaceous wetland", "#0096a0"], ["Mangroves", "#00cf75"], ["Moss and lichen", "#fae6a0"]],
    water: [["Water", "#1f78b4"], ["Non-water", "#bdbdbd"]],
  };
  const ts = { setId: null, active: null };
  const sampleSets = () => layers.filter((l) => l.type === "vector" && l.samples);
  const tsSet = () => getLayer(ts.setId);
  function tsSyncColors(l) { l.classColors = Object.fromEntries((l.classes || []).map((c) => [c.name, c.color])); }
  function tsNewSet() {
    const n = sampleSets().length + 1;
    const l = addLayer({ type: "vector", samples: true, name: `Training samples ${n}`, classes: [], nextId: 1, color: "#334155", weight: 2, fillOpacity: 0.35,
                         geojson: { type: "FeatureCollection", features: [] } }, { select: true });
    tsSyncColors(l);
    ts.setId = l.id; ts.active = null;
    renderSamples();
    return l;
  }
  function tsRefresh(l) {  // redraw the layer after edits
    tsSyncColors(l);
    buildLeaflet(l);
    restack();
    renderContents();
    saveLayers();
  }
  function renderSamples() {
    const sets = sampleSets();
    if (ts.setId && !getLayer(ts.setId)) ts.setId = null;
    if (!ts.setId && sets.length) ts.setId = sets[0].id;
    $("#ts-set").innerHTML = sets.length ? sets.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("") : `<option value="">No sample set yet. Click + New</option>`;
    if (ts.setId) $("#ts-set").value = ts.setId;
    const l = tsSet();
    $("#ts-classes-card").classList.toggle("disabled-card", !l);
    if (!l) { $("#ts-classes").innerHTML = '<p class="hint">Create a sample set first.</p>'; $("#ts-summary").innerHTML = ""; $("#ts-active").innerHTML = "No sample set"; $("#ts-set-info").textContent = ""; return; }
    const feats = l.geojson.features;
    $("#ts-set-info").textContent = `${feats.length} sample${feats.length === 1 ? "" : "s"} · ${l.classes.length} class${l.classes.length === 1 ? "" : "es"} · listed in Contents`;
    if (!l.classes.some((c) => c.name === ts.active)) ts.active = l.classes[0]?.name || null;
    $("#ts-classes").innerHTML = l.classes.length ? l.classes.map((c, i) => {
      const n = feats.filter((f) => f.properties.class === c.name).length;
      return `<div class="cls-row ${c.name === ts.active ? "on" : ""}"><input type="radio" name="tscls" value="${i}" ${c.name === ts.active ? "checked" : ""} title="Draw this class">
        <input type="color" value="${esc(c.color)}" data-i="${i}" title="Class colour"><input type="text" value="${esc(c.name)}" data-i="${i}" maxlength="40" title="Rename">
        <small>${n} sample${n === 1 ? "" : "s"}</small><button class="x" data-del="${i}" title="Delete class and its samples">×</button></div>`;
    }).join("") : '<p class="hint">No classes yet. Load a preset or add your own below.</p>';
    $$('#ts-classes input[name="tscls"]').forEach((r) => r.onchange = () => { ts.active = l.classes[+r.value].name; renderSamples(); });
    $$('#ts-classes input[type="color"]').forEach((inp) => inp.onchange = () => { l.classes[+inp.dataset.i].color = inp.value; tsRefresh(l); renderSamples(); });
    $$('#ts-classes input[type="text"]').forEach((inp) => inp.onchange = () => {
      const c = l.classes[+inp.dataset.i], nn = inp.value.trim();
      if (!nn || l.classes.some((x) => x !== c && x.name === nn)) { inp.value = c.name; return toast("Class names must be unique and not empty", true); }
      l.geojson.features.forEach((f) => { if (f.properties.class === c.name) f.properties.class = nn; });
      if (ts.active === c.name) ts.active = nn;
      c.name = nn; tsRefresh(l); renderSamples();
    });
    $$("#ts-classes [data-del]").forEach((b) => b.onclick = () => {
      const c = l.classes[+b.dataset.del], n = feats.filter((f) => f.properties.class === c.name).length;
      if (n && !confirm(`Delete class “${c.name}” and its ${n} sample${n === 1 ? "" : "s"}?`)) return;
      l.geojson.features = feats.filter((f) => f.properties.class !== c.name);
      l.classes.splice(+b.dataset.del, 1); tsRefresh(l); renderSamples();
    });
    const ac = l.classes.find((c) => c.name === ts.active);
    $("#ts-active").innerHTML = ac ? `Drawing: <i style="background:${esc(ac.color)}"></i><b>${esc(ac.name)}</b>` : "Add a class to start drawing";
    // summary
    const rows = l.classes.map((c) => {
      const fs = feats.filter((f) => f.properties.class === c.name);
      const polys = fs.filter((f) => /Polygon/.test(f.geometry.type)), pts = fs.length - polys.length;
      const km2 = polys.reduce((a, f) => a + geomArea(f.geometry), 0) / 1e6;
      return { c, polys: polys.length, pts, km2 };
    });
    $("#ts-summary").innerHTML = rows.length ? rows.map((r) => `<div class="ts-sum-row"><i style="background:${esc(r.c.color)}"></i><span>${esc(r.c.name)}</span>
        <span>${r.polys} polygon${r.polys === 1 ? "" : "s"}${r.pts ? ` · ${r.pts} point${r.pts === 1 ? "" : "s"}` : ""}</span><b>${r.km2 ? fmt(r.km2, r.km2 < 1 ? 3 : 2) + " km²" : ""}</b></div>`).join("") : "";
    const weak = rows.filter((r) => r.polys + r.pts < 5).map((r) => r.c.name);
    $("#ts-advice").innerHTML = !rows.length ? "" : weak.length
      ? `Aim for <b>at least 5–10 samples per class</b>, spread across the map. Still low: ${weak.map(esc).join(", ")}. Many small polygons beat a few big ones: they give the model variety and allow an honest, polygon-based accuracy check.`
      : `✓ Every class has at least 5 samples. More, well-spread samples usually improve the map further.`;
  }
  function tsDraw(Kind, btn) {
    const l = tsSet();
    if (!l) return toast("Create a sample set first (+ New)", true);
    const c = l.classes.find((x) => x.name === ts.active);
    if (!c) return toast("Add a class and select it before drawing", true);
    $$(".draw-btns .btn").forEach((b) => b.classList.toggle("drawing", b === btn));
    const done = (geometry) => {
      const cur = l.classes.find((x) => x.name === ts.active) || c;
      l.geojson.features.push({ type: "Feature", geometry, properties: { class: cur.name, class_id: l.classes.indexOf(cur) + 1, sample_id: l.nextId++ } });
      tsRefresh(l);
      renderSamples();
      if ($("#ts-keep").checked) setTimeout(() => tsDraw(Kind, btn), 80);
      else $$(".draw-btns .btn").forEach((b) => b.classList.remove("drawing"));
    };
    startDraw(Kind, done, c.color);
  }
  map.on(L.Draw.Event.DRAWSTOP, () => setTimeout(() => { if (!activeDraw) $$(".draw-btns .btn").forEach((b) => b.classList.remove("drawing")); }, 150));
  $("#ts-new").onclick = () => tsNewSet();
  $("#ts-set").onchange = () => { ts.setId = $("#ts-set").value; ts.active = null; renderSamples(); };
  $("#ts-preset").onchange = (e) => {
    let l = tsSet() || tsNewSet();
    const preset = TS_PRESETS[e.target.value];
    e.target.value = "";
    if (!preset) return;
    preset.forEach(([name, color]) => { if (!l.classes.some((c) => c.name === name)) l.classes.push({ name, color }); });
    tsRefresh(l); renderSamples();
  };
  $("#ts-addclass").onclick = () => {
    const name = $("#ts-newclass").value.trim();
    if (!name) return;
    let l = tsSet() || tsNewSet();
    if (l.classes.some((c) => c.name === name)) return toast("That class already exists", true);
    l.classes.push({ name, color: _PALETTE_JS[l.classes.length % _PALETTE_JS.length] });
    ts.active = name;
    $("#ts-newclass").value = "";
    tsRefresh(l); renderSamples();
  };
  $("#ts-newclass").addEventListener("keydown", (e) => e.key === "Enter" && $("#ts-addclass").click());
  const _PALETTE_JS = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#1f78b4", "#fb9a99", "#6a3d9a"];
  $("#ts-draw-poly").onclick = (e) => tsDraw(L.Draw.Polygon, e.currentTarget);
  $("#ts-draw-rect").onclick = (e) => tsDraw(L.Draw.Rectangle, e.currentTarget);
  $("#ts-draw-pt").onclick = (e) => tsDraw(L.Draw.CircleMarker, e.currentTarget);
  $("#ts-undo").onclick = () => { const l = tsSet(); if (l?.geojson.features.length) { l.geojson.features.pop(); tsRefresh(l); renderSamples(); } };
  $("#ts-satellite").onclick = () => setBasemap("imagery");
  $("#ts-export").onclick = () => { const l = tsSet(); if (!l?.geojson.features.length) return toast("Draw some samples first", true); openExport(l); };
  $("#ts-zoom").onclick = () => { const l = tsSet(); if (!l?.geojson.features.length) return toast("Draw some samples first", true); zoomTo(l); };
  $("#ts-to-table").onclick = () => {
    const l = tsSet();
    if (!l?.geojson.features.length) return toast("Draw some samples first", true);
    switchTool("raster2table");
    refreshRtInputs();
    if (!rt.layer) { const r = layers.find((x) => x.type === "raster" && !x.derived); if (r) { $("#rt-input").value = r.id; rt.layer = r; refreshRtInputs(); renderRt(); } }
    $("#rt-gt").value = l.id;
    renderGt();
    if ([...$("#rt-gt-field").options].some((o) => o.value === "class")) { $("#rt-gt-field").value = "class"; showGtClasses(); }
    $("#rt-label").value = "class";
    $('input[name="rts"][value="all"]').checked = true;
    updateRtEstimate();
    toast("Samples selected as ground truth. Choose the image and create the table.");
  };

  // ------------------------------------------------------------------ Stack layers
  const stSel = {};  // layer id -> { on, bands: Set }
  function refreshStack() {
    const rasters = layers.filter((l) => l.type === "raster");
    $("#st-layers").innerHTML = rasters.length ? rasters.map((l) => {
      const s = stSel[l.id] || (stSel[l.id] = { on: false, bands: new Set((l.info?.bands || []).map((b) => b.index)) });
      const formula = l.derived || l.render?.index || l.render?.formula;
      const bandsHtml = !formula && l.info ? `<div class="bands">${l.info.bands.map((b) => `<label title="${esc(b.description)}"><input type="checkbox" data-sb="${esc(l.id)}" value="${b.index}" ${s.bands.has(b.index) ? "checked" : ""}>${esc(b.description !== "Band " + b.index ? b.description : String(b.index))}</label>`).join("")}</div>` : "";
      return `<div class="st-layer ${s.on ? "on" : ""}"><label><input type="checkbox" data-sl="${esc(l.id)}" ${s.on ? "checked" : ""}>${esc(l.name)}
        <small>${formula ? `1 band (${esc(l.render.index || "formula")})` : `${s.bands.size} of ${l.info?.count || "?"} bands`}</small></label>${s.on ? bandsHtml : ""}</div>`;
    }).join("") : '<p class="hint">Add raster layers to Contents first.</p>';
    $$("[data-sl]").forEach((c) => c.onchange = () => { stSel[c.dataset.sl].on = c.checked; refreshStack(); });
    $$("[data-sb]").forEach((c) => c.onchange = () => { const s = stSel[c.dataset.sb]; c.checked ? s.bands.add(+c.value) : s.bands.delete(+c.value); refreshStack(); });
    const ref = $("#st-ref"), cur = ref.value;
    const chosen = rasters.filter((l) => stSel[l.id]?.on);
    ref.innerHTML = (chosen.length ? chosen : rasters).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}${l.info ? ` · ${fmt(l.info.res[0], l.info.res[0] < 1 ? 3 : 0)} ${/4326/.test(l.info.crs) ? "°" : "m"}` : ""}</option>`).join("");
    if ([...ref.options].some((o) => o.value === cur)) ref.value = cur;
    updateStackEst();
  }
  function updateStackEst() {
    const ref = getLayer($("#st-ref").value);
    if (ref?.info) {
      const r0 = ref.info.res[0], unit = /4326/.test(ref.info.crs) ? "°" : " m", cur = $("#st-factor").value;
      $("#st-factor").innerHTML = [1, 2, 4, 8].map((f) => `<option value="${f}">${f === 1 ? `Native (${fmt(r0, r0 < 1 ? 3 : 0)}${unit})` : `${f}× coarser (${fmt(r0 * f, r0 < 1 ? 3 : 0)}${unit})`}</option>`).join("");
      if (cur) $("#st-factor").value = cur;
    }
    const n = layers.filter((l) => l.type === "raster" && stSel[l.id]?.on).reduce((a, l) => a + ((l.derived || l.render?.index || l.render?.formula) ? 1 : stSel[l.id].bands.size), 0);
    $("#st-est").textContent = n ? `${n} band${n === 1 ? "" : "s"} in the stacked image${ref?.info ? ` · grid of ${ref.info.width.toLocaleString()} × ${ref.info.height.toLocaleString()} px before area / pixel-size options` : ""}` : "Tick at least one layer.";
  }
  $("#st-ref").onchange = updateStackEst;
  $("#st-run").onclick = async () => {
    const err = $("#st-error"); err.classList.add("hidden");
    // oldest layer first (the bottom of Contents), so the base image's bands come first
    const chosen = layers.filter((l) => l.type === "raster" && stSel[l.id]?.on).reverse();
    if (!chosen.length) return toast("Tick at least one layer", true);
    const items = chosen.map((l) => (l.derived || l.render?.index || l.render?.formula)
      ? { path: l.path, name: l.name, index: l.render.index || null, formula: l.render.formula || null, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0 }
      : { path: l.path, name: l.name.replace(/\.(tiff?|vrt)$/i, ""), bands: [...stSel[l.id].bands].sort((a, b) => a - b) });
    if (items.some((it) => it.bands && !it.bands.length)) return toast("A ticked layer has no bands selected", true);
    const ref = getLayer($("#st-ref").value);
    const btn = $("#st-run"); btn.disabled = true;
    $("#st-result").classList.add("hidden");
    try {
      const job = await api("/api/stack", { method: "POST", json: { items, ref: ref.path, clip: getClip("st-area"), factor: +$("#st-factor").value || 1, name: $("#st-name").value || "stack" } });
      const done = await trackJob(job, { tool: "stack", save: "stack", title: "Stacking layers" });
      const r = done.result;
      const out = await addRasterFromPath(r.path, { name: $("#st-name").value || "stack" });
      $("#st-result").innerHTML = `<div class="pca-sum" style="margin-top:10px"><b>✓ ${r.bands.length} bands stacked</b> · ${r.width.toLocaleString()} × ${r.height.toLocaleString()} px · ${esc(r.crs)} · ${r.seconds} s<br>${r.bands.map(esc).join(", ")}</div>
        <div class="row"><button class="btn small primary" data-st2t>Use in Raster → table →</button><button class="btn small" data-stz>Zoom</button></div>`;
      $("#st-result").classList.remove("hidden");
      $("[data-stz]").onclick = () => zoomTo(out);
      $("[data-st2t]").onclick = () => { switchTool("raster2table"); refreshRtInputs(); $("#rt-input").value = out.id; rt.layer = out; refreshRtInputs(); renderRt(); };
    } catch (e) { if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); } }
    finally { btn.disabled = false; }
  };

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
    renderMlHub();
    initMlTrain().catch((e) => toast("Classical ML: " + e.message, true));
    initProject();
    loadCreds().catch(() => {});
    setPane("tools", false);  // the tool panel opens only when a tool is chosen from the Tools menu
    refreshJobs();
  })().catch((e) => toast("Could not reach the server: " + e.message, true));
})();
