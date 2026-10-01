/* LULC Fetch web UI — vanilla JS, Leaflet + Leaflet.draw. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 1) => (n == null || isNaN(n) ? "–" : Number(n).toFixed(d));

  const state = {
    config: null, aoi: null, results: null, sort: "cloud",
    activeScene: null, downloadScene: null, polling: null,
  };

  // ------------------------------------------------------------------ api
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

  // ------------------------------------------------------------------ map
  const map = L.map("map", { zoomControl: true }).setView([20.5, 78.9], 5);
  const streets = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap contributors",
  });
  const imagery = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
  });
  const labels = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19 });
  streets.addTo(map);
  const aoiLayer = L.featureGroup().addTo(map);
  const footprintLayer = L.featureGroup().addTo(map);
  const geoLayer = L.featureGroup().addTo(map);
  let previewImage = null, previewClouds = null;
  L.control.layers({ "Streets": streets, "Satellite (Esri)": imagery }, { "Place labels": labels, "Scene footprints": footprintLayer }, { position: "bottomright" }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);

  const AOI_STYLE = { color: "#1f7a5a", weight: 2.5, fillOpacity: 0.08, dashArray: null };

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

  // ------------------------------------------------------------------ AOI
  function setAOI(geometry, label = "") {
    if (!geometry || !/Polygon/.test(geometry.type)) { toast("The area must be a polygon", true); return; }
    state.aoi = geometry;
    aoiLayer.clearLayers();
    L.geoJSON(geometry, { style: AOI_STYLE }).addTo(aoiLayer);
    map.fitBounds(aoiLayer.getBounds(), { padding: [40, 40], maxZoom: 15 });
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
    clearResults();
  }

  function clearAOI() {
    state.aoi = null;
    aoiLayer.clearLayers();
    $("#aoi-summary").classList.add("hidden");
    $("#aoi-warn").classList.add("hidden");
    clearResults();
  }

  $("#aoi-zoom").onclick = () => aoiLayer.getLayers().length && map.fitBounds(aoiLayer.getBounds(), { padding: [40, 40] });
  $("#aoi-clear").onclick = clearAOI;

  // AOI method tabs
  $$("#aoi-tabs button").forEach((b) => b.onclick = () => {
    $$("#aoi-tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $$(".aoi-pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== b.dataset.aoi));
  });

  // Draw
  const drawOpts = { shapeOptions: AOI_STYLE, showArea: false };
  let activeDraw = null;
  function startDraw(Kind) {
    activeDraw?.disable();
    activeDraw = new Kind(map, drawOpts);
    activeDraw.enable();
  }
  $("#draw-rect").onclick = () => startDraw(L.Draw.Rectangle);
  $("#draw-poly").onclick = () => startDraw(L.Draw.Polygon);
  map.on(L.Draw.Event.CREATED, (e) => { activeDraw = null; setAOI(e.layer.toGeoJSON().geometry, "drawn"); });

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
    footprintLayer.clearLayers();
    removePreview();
    $("#results").innerHTML = "";
    $("#results-info").textContent = "Choose an area and dates, then search.";
  }

  $("#btn-search").onclick = () => {
    if (!state.aoi) return toast("Set an area of interest first", true);
    const start = $("#start").value, end = $("#end").value;
    if (!start || !end || start > end) return toast("Choose a valid date range", true);
    busy($("#btn-search"), "Searching…", async () => {
      try {
        const res = await api("/api/search", { method: "POST", json: {
          source: $("#source").value, aoi: state.aoi, start, end, max_cloud: +$("#cloud").value,
        } });
        state.results = res;
        renderResults();
      } catch (e) { toast(e.message, true); }
    });
  };

  $$("#sort button").forEach((b) => b.onclick = () => {
    state.sort = b.dataset.sort;
    $$("#sort button").forEach((x) => x.classList.toggle("active", x === b));
    renderResults();
  });

  const cloudClass = (c) => (c < 10 ? "c0" : c < 40 ? "c1" : "c2");
  const weekday = (d) => new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

  function renderResults() {
    const res = state.results;
    const box = $("#results");
    box.innerHTML = "";
    footprintLayer.clearLayers();
    if (!res) return;
    const scenes = [...res.scenes].sort({
      cloud: (a, b) => a.cloud - b.cloud || b.date.localeCompare(a.date),
      date: (a, b) => b.date.localeCompare(a.date),
      coverage: (a, b) => b.coverage - a.coverage || a.cloud - b.cloud,
    }[state.sort]);
    $("#results-info").textContent = scenes.length
      ? `${scenes.length} acquisition date${scenes.length === 1 ? "" : "s"} · ${res.count} tile${res.count === 1 ? "" : "s"} from ${$("#source").selectedOptions[0].text.split(" —")[0]}`
      : "Nothing found. Try a longer date range or a higher cloud limit.";

    scenes.forEach((sc) => {
      const fp = L.geoJSON({ type: "FeatureCollection", features: sc.items.map((i) => ({ type: "Feature", geometry: i.footprint })) },
        { style: { color: "#6366f1", weight: 1, fillOpacity: 0, opacity: 0.35 } }).addTo(footprintLayer);
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
      el.onmouseenter = () => fp.setStyle({ color: "#6366f1", weight: 2.5, opacity: 1, fillOpacity: 0.06 });
      el.onmouseleave = () => sc !== state.activeScene && fp.setStyle({ weight: 1, opacity: 0.35, fillOpacity: 0 });
      el.querySelector(".thumb")?.addEventListener("click", () => first.thumbnail && window.open(first.thumbnail, "_blank", "noopener"));
      el.querySelector('[data-a="preview"]').onclick = (e) => previewScene(sc, el, e.currentTarget);
      el.querySelector('[data-a="meta"]').onclick = () => showMetadata(sc);
      el.querySelector('[data-a="dl"]').onclick = () => openDownload(sc);
      sc._el = el;
      box.append(el);
    });
  }

  // ------------------------------------------------------------------ preview
  function removePreview() {
    previewImage?.remove(); previewClouds?.remove();
    previewImage = previewClouds = null;
    $("#preview-ctl").classList.add("hidden");
    state.activeScene?._el?.classList.remove("active");
    state.activeScene = null;
  }
  $("#pv-close").onclick = removePreview;
  $("#pv-clouds").onchange = (e) => previewClouds && (e.target.checked ? previewClouds.addTo(map) : previewClouds.remove());
  $("#pv-opacity").oninput = (e) => previewImage?.setOpacity(e.target.value / 100);

  async function previewScene(sc, el, btn) {
    await busy(btn, "Loading…", async () => {
      try {
        const res = await api("/api/preview", { method: "POST", json: {
          source: state.results.source, item_ids: sc.items.map((i) => i.id), aoi: state.aoi,
        } });
        removePreview();
        state.activeScene = sc;
        el.classList.add("active");
        previewImage = L.imageOverlay(res.image, res.bounds, { opacity: $("#pv-opacity").value / 100 }).addTo(map);
        previewClouds = L.imageOverlay(res.clouds, res.bounds);
        if ($("#pv-clouds").checked) previewClouds.addTo(map);
        aoiLayer.bringToFront();
        map.fitBounds(res.bounds, { padding: [30, 30] });
        const s = res.stats;
        $("#pv-title").textContent = `Preview · ${sc.date}`;
        $("#pv-stats").innerHTML = `
          <span>Clear in area</span><b>${fmt(s.clear_pct)}%</b>
          <span style="color:#ca8a04">Cloud</span><b>${fmt(s.cloud_pct)}%</b>
          <span style="color:#9333ea">Shadow</span><b>${fmt(s.shadow_pct)}%</b>
          <span>Has data</span><b>${fmt(s.data_pct)}%</b>
          <span>Preview pixel</span><b>${res.resolution_m} m</b>`;
        $("#preview-ctl").classList.remove("hidden");
        const pv = el.querySelector(".pvstat");
        pv.textContent = `Inside your area: ${fmt(s.clear_pct)}% clear · ${fmt(s.cloud_pct)}% cloud · ${fmt(s.shadow_pct)}% shadow`;
        pv.classList.remove("hidden");
      } catch (e) { toast(e.message, true); }
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
  function bandBoxes(selected) {
    $("#bands").innerHTML = state.config.bands.map((b) =>
      `<label><input type="checkbox" value="${b}" ${selected.includes(b) ? "checked" : ""}>${b}</label>`).join("");
    $$("#bands input").forEach((i) => i.onchange = updateEstimate);
    updateEstimate();
  }
  $("#bands-default").onclick = () => bandBoxes(state.config.default_bands);
  $("#bands-all").onclick = () => bandBoxes(state.config.bands);
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
    if (scene) {
      $("#dl-product-list").innerHTML = scene.items.map((i) => esc(i.product_name)).join("<br>") +
        `<p class="hint">Downloaded from Copernicus Data Space with your saved account.</p>`;
    }
    if (!$("#bands").children.length) bandBoxes(state.config.default_bands);
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
    if (k === "product") body.product_names = sc.items.map((i) => i.product_name);
    if (["scene", "composite"].includes(k) && !body.bands.length) return showDlError("Select at least one band");
    busy($("#dl-go"), "Starting…", async () => {
      try {
        await api("/api/jobs", { method: "POST", json: body });
        $("#dlg-dl").close();
        switchTab("jobs");
        refreshJobs();
      } catch (e) { showDlError(e.message); }
    });
  };
  function showDlError(msg) { const el = $("#dl-error"); el.textContent = msg; el.classList.remove("hidden"); }

  // ------------------------------------------------------------------ jobs
  function switchTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $("#tab-search").classList.toggle("hidden", name !== "search");
    $("#tab-jobs").classList.toggle("hidden", name !== "jobs");
  }
  $$(".tab").forEach((t) => t.onclick = () => switchTab(t.dataset.tab));

  const openLogs = new Set();
  async function refreshJobs() {
    let list;
    try { list = await api("/api/jobs"); } catch { return; }
    const running = list.filter((j) => j.status === "running" || j.status === "queued").length;
    const badge = $("#jobs-badge");
    badge.textContent = running || list.length;
    badge.classList.toggle("hidden", !list.length);
    $("#jobs-empty").classList.toggle("hidden", list.length > 0);
    $("#jobs").innerHTML = list.map((j) => {
      const elapsed = Math.round(((j.finished || Date.now() / 1000) - (j.started || j.created)));
      const files = j.files.map((f) => `<a href="/api/jobs/${j.id}/files/${encodeURIComponent(f)}" download><span>⬇ ${esc(f)}</span></a>`).join("");
      const png = j.files.find((f) => f.endsWith(".png"));
      const dist = j.result?.distribution ? `<div class="dist">${j.result.distribution.slice(0, 11).map((d) =>
        `<div><i style="background:${esc(d.color)}"></i><span>${esc(d.name)}</span><b>${fmt(d.pct)}%</b></div>`).join("")}</div>` : "";
      const facts = [j.params.grid, j.result?.date && `date ${j.result.date}`, j.result?.dates && `${j.result.dates.length} dates`,
        j.result?.valid_pct != null && `${fmt(j.result.valid_pct)}% valid pixels`].filter(Boolean).join(" · ");
      const showLogs = j.status === "running" || j.status === "error" || openLogs.has(j.id);
      return `<div class="job">
        <h4><span>${esc(j.title)}</span><span class="status ${j.status}">${j.status === "running" ? '<span class="spinner"></span>' : ""}${j.status}</span></h4>
        <div class="sub">${esc(j.params.source || "")} · ${elapsed}s${facts ? " · " + esc(facts) : ""}</div>
        ${j.error ? `<div class="warn">${esc(j.error)}</div>` : ""}
        ${png ? `<img class="result" src="/api/jobs/${j.id}/files/${encodeURIComponent(png)}?t=${j.finished || ""}" alt="Result preview">` : ""}
        ${dist}
        ${files ? `<div class="files">${files}</div>` : ""}
        <details data-job="${j.id}" ${showLogs ? "open" : ""}><summary>Log (${j.logs.length} lines)</summary><pre>${esc(j.logs.join("\n"))}</pre></details>
        <div class="row"><button class="btn small danger" data-del="${j.id}">${j.status === "running" ? "Hide" : "Delete files"}</button></div>
      </div>`;
    }).join("");
    $$("#jobs details").forEach((d) => d.addEventListener("toggle", () => d.open ? openLogs.add(d.dataset.job) : openLogs.delete(d.dataset.job)));
    $$("#jobs pre").forEach((p) => p.scrollTop = p.scrollHeight);
    $$("[data-del]").forEach((b) => b.onclick = async () => { await api(`/api/jobs/${b.dataset.del}`, { method: "DELETE" }); refreshJobs(); });
    clearTimeout(state.polling);
    if (running) state.polling = setTimeout(refreshJobs, 1500);
  }

  // ------------------------------------------------------------------ credentials
  async function loadCreds() {
    const data = await api("/api/credentials");
    const p = data.providers;
    $("#creds-dot").classList.toggle("ok", p.cdse_account.complete || p.cdse_s3.complete);
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

  // ------------------------------------------------------------------ init
  (async () => {
    state.config = await api("/api/config");
    $("#source").innerHTML = Object.entries(state.config.sources).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("#indices-list").textContent = `(${state.config.indices.join(", ")})`;
    $("#dl-product").innerHTML = Object.entries(state.config.label_products).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    const years = { worldcover: [2021, 2020], esri: [2023, 2022, 2021, 2020, 2019, 2018, 2017] };
    const fillYears = () => $("#dl-year").innerHTML = years[$("#dl-product").value].map((y) => `<option>${y}</option>`).join("");
    $("#dl-product").onchange = fillYears;
    fillYears();
    setRange(90);
    loadCreds().catch(() => {});
    refreshJobs();
  })().catch((e) => toast("Could not reach the server: " + e.message, true));
})();
