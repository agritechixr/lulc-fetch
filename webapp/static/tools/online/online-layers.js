/* Insert ▸ Online & field ▸ Online map layer: WMS, WMTS and XYZ tile services (Bhuvan, NASA GIBS, any address) as layers.
   The server reads a service's GetCapabilities (/api/online/capabilities · lulc_fetch/online_layers.py); the map then
   loads the tiles straight from the service (core/layers.js, layer type "tiles"). Nothing is downloaded. */
(() => {
  "use strict";
  const { tip } = LF.html;
  // services that work without an account; "xyz" ones are added as they are
  const PRESETS = [
    { id: "bhuvan-vec", name: "Bhuvan (ISRO): maps of India", url: "https://bhuvan-vec1.nrsc.gov.in/bhuvan/wms",
      about: "Boundaries, roads, railways, water, land use and thematic maps of India (WMS, about 7,000 layers: search them)" },
    { id: "bhuvan-ras", name: "Bhuvan (ISRO): raster themes", url: "https://bhuvan-ras2.nrsc.gov.in/mapcache",
      about: "Land use / land cover (1:250,000, e.g. LULC250K_2223), floods, DEMs and other raster themes of India (WMS)" },
    { id: "gibs", name: "NASA GIBS: satellite imagery by date", url: "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml",
      about: "Daily MODIS / VIIRS true colour, fires, snow, aerosols, temperature and 1,000+ more global layers (WMTS, choose the date)" },
    { id: "gibs-wms", name: "NASA GIBS (WMS)", url: "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi",
      about: "The same NASA layers as a WMS" },
    { id: "otm", name: "OpenTopoMap", kind: "xyz", url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", max_zoom: 17,
      attribution: "© OpenStreetMap contributors, SRTM · © OpenTopoMap (CC-BY-SA)", about: "Topographic map with contours (XYZ)" },
    { id: "esri-hill", name: "Esri World Hillshade", kind: "xyz", url: "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}",
      max_zoom: 16, attribution: "Esri, USGS, NGA, NASA, CGIAR", about: "Shaded relief of the terrain (XYZ)" },
  ];

  LF.tool({
    id: "online", menu: "online", title: "Online map layer", icon: "online",
    subtitle: "Add map layers from web map services: WMS, WMTS or XYZ tiles, such as Bhuvan's maps of India, NASA GIBS daily satellite imagery, or any address. They are drawn from the service, nothing is downloaded",
    panel: `
      <div class="card">
        <h2>Service ${tip("A WMS or WMTS address (its GetCapabilities is read to list the layers), or an XYZ tile address with {z}, {x} and {y}, e.g. https://tile.example.org/{z}/{x}/{y}.png. The map needs the internet to show these layers.")}</h2>
        <select id="ol-preset"></select>
        <p class="hint" id="ol-preset-about"></p>
        <label>Address<input id="ol-url" class="mono" placeholder="https://…/wms  or  https://…/{z}/{x}/{y}.png" spellcheck="false" autocomplete="off"></label>
        <div class="row tight" style="gap:6px;margin-top:6px"><button class="btn primary" id="ol-connect">Show its layers</button></div>
        <div id="ol-error" class="warn hidden"></div>
      </div>
      <div class="card hidden" id="ol-xyz">
        <h2>Tile layer</h2>
        <div class="grid2">
          <label>Name<input id="ol-xyz-name"></label>
          <label>Most detailed zoom ${tip("The service's highest zoom level (often 18 or 19). Beyond it the map enlarges the last tiles.")}<input id="ol-xyz-max" type="number" min="1" max="22" value="19"></label>
        </div>
        <label>Credit (attribution)<input id="ol-xyz-attr" placeholder="© the data's owner"></label>
        <button class="btn primary" id="ol-xyz-add">Add to map</button>
      </div>
      <div class="card hidden" id="ol-layers-card">
        <h2 id="ol-svc-title">Layers</h2>
        <input type="search" id="ol-q" placeholder="Search the layers: e.g. district, lulc, true color, fire" autocomplete="off">
        <p class="hint" id="ol-count"></p>
        <div id="ol-list" class="ol-list"></div>
      </div>
      <p class="hint">Online layers are drawn under your own layers, or over them when on top of Contents. Layers with dates (NASA GIBS) get a date box in Contents. Printing / exporting the map includes them only when the service allows it (NASA does; Bhuvan doesn't).</p>`,

    setup(LF) {
      const { $, $$, esc, prefs, api, toast, addOnlineLayer, zoomTo } = LF;
      const st = { caps: null, shown: 150 };
      const recent = () => prefs.get("ol-recent", []);
      function fillPresets() {
        const keep = $("#ol-preset").value, r = recent().filter((u) => !PRESETS.some((p) => p.url === u));
        $("#ol-preset").innerHTML = `<option value="">Choose a service, or type an address below…</option>
          <optgroup label="Free services">${PRESETS.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</optgroup>
          ${r.length ? `<optgroup label="Used before">${r.map((u) => `<option value="url:${esc(u)}">${esc(u.replace(/^https?:\/\//, "").slice(0, 70))}</option>`).join("")}</optgroup>` : ""}`;
        if ([...$("#ol-preset").options].some((o) => o.value === keep)) $("#ol-preset").value = keep;
      }
      $("#ol-preset").onchange = () => {
        const v = $("#ol-preset").value, p = PRESETS.find((x) => x.id === v);
        $("#ol-preset-about").textContent = p?.about || "";
        if (p) $("#ol-url").value = p.url;
        else if (v.startsWith("url:")) $("#ol-url").value = v.slice(4);
        if (v) connect();
      };
      $("#ol-url").onkeydown = (e) => { if (e.key === "Enter") connect(); };
      $("#ol-connect").onclick = () => connect();

      async function connect() {
        const url = $("#ol-url").value.trim(), btn = $("#ol-connect"), err = $("#ol-error");
        err.classList.add("hidden");
        if (!url) return toast("Choose a service or type its address", true);
        $("#ol-xyz").classList.add("hidden");
        $("#ol-layers-card").classList.add("hidden");
        if (/\{z\}/.test(url) && /\{x\}/.test(url)) return showXyz(url);
        btn.disabled = true; btn.textContent = "Reading the service…";
        try {
          st.caps = await api(`/api/online/capabilities?url=${encodeURIComponent(url)}`);
          if (st.caps.kind === "xyz") return showXyz(url);
          prefs.set("ol-recent", [url, ...recent().filter((u) => u !== url)].slice(0, 8));
          fillPresets();
          st.shown = 150;
          $("#ol-svc-title").textContent = `${st.caps.title} · ${st.caps.kind.toUpperCase()}`;
          $("#ol-q").value = "";
          $("#ol-layers-card").classList.remove("hidden");
          renderList();
        } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
        finally { btn.disabled = false; btn.textContent = "Show its layers"; }
      }
      function showXyz(url) {
        const p = PRESETS.find((x) => x.url === url);
        $("#ol-xyz-name").value = p?.name || new URL(url.replace(/\{[^}]+\}/g, "0")).hostname;
        $("#ol-xyz-max").value = p?.max_zoom || 19;
        $("#ol-xyz-attr").value = p?.attribution || "";
        $("#ol-xyz").classList.remove("hidden");
      }
      $("#ol-xyz-add").onclick = () => {
        const url = $("#ol-url").value.trim(), name = $("#ol-xyz-name").value.trim() || "Tiles";
        addOnlineLayer({ kind: "xyz", url, max_zoom: +$("#ol-xyz-max").value || 19, attribution: $("#ol-xyz-attr").value.trim(), service: name }, name);
        prefs.set("ol-recent", [url, ...recent().filter((u) => u !== url)].slice(0, 8));
        fillPresets();
        toast(`${name} added (from the service)`);
      };

      function matches(l, words) {
        const t = `${l.title} ${l.name} ${l.abstract}`.toLowerCase();
        return words.every((w) => t.includes(w));
      }
      function renderList() {
        const c = st.caps, words = $("#ol-q").value.toLowerCase().split(/\s+/).filter(Boolean);
        const hits = c.layers.filter((l) => matches(l, words)), show = hits.slice(0, st.shown);
        $("#ol-count").textContent = `${hits.length.toLocaleString()} of ${c.layers.length.toLocaleString()} layers${c.truncated ? " (the first ones of a very large service)" : ""}` +
          (c.skipped ? ` · ${c.skipped} not in Web Mercator tiles, left out` : "");
        $("#ol-list").innerHTML = show.map((l, i) => `<div class="ol-item">
            <div class="ol-text"><b>${esc(l.title)}</b>${l.title !== l.name ? ` <code>${esc(l.name)}</code>` : ""}
              ${l.time ? `<span class="pill c0" title="Has dates: ${esc(l.time.start)} to ${esc(l.time.end)}">dates</span>` : ""}
              ${c.kind === "wms" && !l.mercator ? `<span class="pill c1" title="The service doesn't list Web Mercator (EPSG:3857) for this layer: it may not draw">?</span>` : ""}
              ${l.abstract ? `<small>${esc(l.abstract.slice(0, 180))}${l.abstract.length > 180 ? "…" : ""}</small>` : ""}</div>
            <button class="btn small" data-ol-add="${c.layers.indexOf(l)}">Add</button></div>`).join("") +
          (hits.length > show.length ? `<button class="btn small" style="width:100%" id="ol-more">Show ${Math.min(150, hits.length - show.length)} more</button>` : "") +
          (!hits.length ? `<p class="hint">No layer matches: try other words.</p>` : "");
        $$("[data-ol-add]", $("#ol-list")).forEach((b) => b.onclick = () => add(c.layers[+b.dataset.olAdd]));
        $("#ol-more")?.addEventListener("click", () => { st.shown += 150; renderList(); });
      }
      let qt = 0;
      $("#ol-q").oninput = () => { clearTimeout(qt); qt = setTimeout(() => { st.shown = 150; renderList(); }, 200); };

      function add(l) {
        const c = st.caps;
        const tiles = c.kind === "wms"
          ? { kind: "wms", url: c.url, layer: l.name, style: "", format: c.format, version: c.version }
          : { kind: "wmts", url: l.url, layer: l.name, format: l.format, matrices: l.matrices, min_zoom: l.min_zoom, max_zoom: l.max_zoom };
        Object.assign(tiles, { service: c.title, bbox: l.bbox, legend: l.legend, attribution: l.attribution || c.title,
                               time_info: l.time || null, time: l.time ? (l.time.default || l.time.end || "") : "" });
        const lyr = addOnlineLayer(tiles, l.title);
        const b = l.bbox;
        if (b && b[2] - b[0] < 300) zoomTo(lyr);   // a regional layer: show where it is
        toast(`${l.title} added${tiles.time ? ` (${tiles.time}: change the date in Contents)` : ""}`);
      }

      fillPresets();
      return { open() {} };
    },
  });
})();
