  // Right-click on the map: copy the point's coordinates (lat/lon, lon/lat, UTM), what every layer holds there, add a
  // point, and start tools from there (imagery, embeddings, similar places), or open the place in a web map.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ map right-click menu
  /** WGS84 → UTM (zone, hemisphere, easting, northing), the usual series (accurate to well under a metre) */
  function toUtm(lat, lon) {
    const zone = Math.floor((lon + 180) / 6) + 1, a = 6378137, f = 1 / 298.257223563, k0 = 0.9996;
    const e2 = f * (2 - f), ep2 = e2 / (1 - e2), rad = Math.PI / 180;
    const phi = lat * rad, lam = (lon - (zone * 6 - 183)) * rad;
    const N = a / Math.sqrt(1 - e2 * Math.sin(phi) ** 2), T = Math.tan(phi) ** 2, C = ep2 * Math.cos(phi) ** 2, A = Math.cos(phi) * lam;
    const M = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * Math.sin(2 * phi)
      + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * Math.sin(4 * phi) - (35 * e2 ** 3 / 3072) * Math.sin(6 * phi));
    const E = k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T ** 2 + 72 * C - 58 * ep2) * A ** 5 / 120) + 500000;
    let Nn = k0 * (M + N * Math.tan(phi) * (A ** 2 / 2 + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24 + (61 - 58 * T + T ** 2 + 600 * C - 330 * ep2) * A ** 6 / 720));
    if (lat < 0) Nn += 10000000;
    return { zone, hemi: lat < 0 ? "S" : "N", e: E, n: Nn, epsg: (lat < 0 ? 32700 : 32600) + zone };
  }

  // "What's here?": the value(s) of every visible raster layer at the point, in one popup (each list scrolls)
  async function whatsHere(latlng) {
    const rasters = layers.filter((l) => l.type === "raster" && l.visible && l.path);
    if (!rasters.length) return toast("No raster layer is shown here");
    const parts = await Promise.all(rasters.map((l) => pixelValues(l, latlng).then((px) => ({ l, px })).catch((e) => ({ l, err: e.message }))));
    const html = parts.filter((p) => p.px || p.err).map(({ l, px, err }) => {
      if (err) return `<div class="px-layer"><b>${esc(l.name)}</b><div class="small" style="color:var(--err)">${esc(err)}</div></div>`;
      const n = px.r.raw.length;
      return `<div class="px-layer"><b>${esc(l.name)}</b>${px.head}
        ${px.rows.length ? `<details ${parts.length === 1 ? "open" : ""}><summary class="small">${n} band${n === 1 ? "" : "s"}</summary>
          <div class="px-scroll"><table>${px.rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmtv(v)}</td></tr>`).join("")}</table></div></details>` : ""}</div>`;
    }).join("") || `<div class="small">None of the shown layers covers this point.</div>`;
    L.popup({ maxWidth: 340, minWidth: 240, className: "px-popup" }).setLatLng(latlng)
      .setContent(`<div class="pxpop"><div class="small" style="color:#667085">${fmt(latlng.lat, 5)}, ${fmt(latlng.lng, 5)}</div>${html}</div>`).openOn(map);
  }

  // a "Map points" layer in Contents collects the points added from the menu (export it like any vector layer)
  function addMapPoint(latlng) {
    let l = layers.find((x) => x.mapPoints);
    if (!l) l = addVectorLayer({ type: "FeatureCollection", features: [] }, "Map points", { mapPoints: true, color: "#e11d48", zoom: false });
    const n = l.geojson.features.length + 1;
    l.geojson.features.push({ type: "Feature", geometry: { type: "Point", coordinates: [+latlng.lng.toFixed(7), +latlng.lat.toFixed(7)] },
                              properties: { name: `Point ${n}`, lat: +latlng.lat.toFixed(6), lon: +latlng.lng.toFixed(6) } });
    buildLeaflet(l); restack(); renderContents(); saveLayers();
    toast(`Point ${n} added to “${l.name}” (its attribute table lists them; export it from Contents)`);
  }

  function showMapMenu(latlng, x, y) {
    const lat = latlng.lat, lon = latlng.lng, u = toUtm(lat, lon);
    const emb = layers.find((l) => l.type === "raster" && l.visible && l.info?.embedding && l.bounds && L.latLngBounds(l.bounds).contains(latlng));
    const around = (km) => squareAround(lat, lon, km);
    const items = [
      ["Copy coordinates (lat, lon)", () => copyText(`${lat.toFixed(6)}, ${lon.toFixed(6)}`, "Coordinates copied (lat, lon)")],
      ["Copy as lon, lat", () => copyText(`${lon.toFixed(6)}, ${lat.toFixed(6)}`, "Coordinates copied (lon, lat)")],
      [`Copy as UTM ${u.zone}${u.hemi} (EPSG:${u.epsg})`, () => copyText(`${u.e.toFixed(1)}, ${u.n.toFixed(1)}`, `UTM ${u.zone}${u.hemi} copied (easting, northing)`)],
      "-",
      ["What's here? (values of every layer)", () => whatsHere(latlng)],
      ["Add a point here", () => addMapPoint(latlng)],
      ["Centre the map here", () => map.panTo(latlng)],
      ["Zoom in here", () => map.setView(latlng, Math.min(map.getZoom() + 2, map.getMaxZoom()))],
      "-",
      ["Find imagery around here (2 × 2 km)", () => { setAOI(around(1), "1 km around point"); switchTool("search"); }],
      ["Download embeddings around here (2 × 2 km)", () => {
        const c = addClipLayer(around(1), `Area around ${lat.toFixed(4)}, ${lon.toFixed(4)}`);
        openTool("embed", { area: `layer:${c.id}`, year: String(prefs.get("em-year", "2024")) });
      }],
      emb ? [`Find places similar to this one (${emb.name})`, () => openTool("embexplore", { layer: emb.id, point: [lon, lat] })] : null,
      "-",
      ["Open in Google Maps", () => window.open(`https://www.google.com/maps/search/?api=1&query=${lat.toFixed(6)},${lon.toFixed(6)}`, "_blank", "noopener")],
      ["Open in OpenStreetMap", () => window.open(`https://www.openstreetmap.org/?mlat=${lat.toFixed(6)}&mlon=${lon.toFixed(6)}#map=17/${lat.toFixed(6)}/${lon.toFixed(6)}`, "_blank", "noopener")],
    ].filter(Boolean);
    showMenu(`${lat.toFixed(5)}, ${lon.toFixed(5)}`, items, x, y);
  }
  map.on("contextmenu", (e) => {
    if (picking || activeDraw) return;
    e.originalEvent?.preventDefault();
    showMapMenu(e.latlng, e.originalEvent.clientX, e.originalEvent.clientY);
  });
