  // Find imagery: the area of interest (draw, coordinates, place search, file) and geometry helpers used by every tool.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

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
    $("#aoi-emb").classList.remove("hidden");
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
  // the same area (and the year of the dates) in Embeddings ▸ Download embeddings
  $("#aoi-embed").onclick = () => {
    if (!getLayer("aoi")) return toast("Choose an area first", true);
    const y = +(($("#end").value || $("#start").value || "").slice(0, 4)) || new Date().getFullYear() - 1;
    switchTool("embed", { area: "layer:aoi", year: String(Math.min(2025, Math.max(2017, y))) });
  };

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
