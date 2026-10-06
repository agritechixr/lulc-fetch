  // View ▸ Measure: distance, area and height, on 2D and 3D maps. Click points on the map; double-click (or Enter) to
  // finish, Backspace takes the last point back, Esc stops. In a 3D map a distance is measured along the ground (with
  // the DEM's heights) as well as flat; a height is read from the 3D data. On a 2D map a height comes from a DEM in Contents.
  // The result can be copied or kept as a layer.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ measure
  const measure = { on: false, mode: null, pts: [], done: false, layer: null, hover: null, heights: [], group: null };
  const MEASURE_TITLE = { distance: "Distance", area: "Area", height: "Height", profile: "Profile" };
  const fmtLen = (m) => m >= 1000 ? `${(m / 1000).toLocaleString(undefined, { maximumFractionDigits: m >= 1e5 ? 1 : 3 })} km` : `${m.toLocaleString(undefined, { maximumFractionDigits: 1 })} m`;
  const fmtArea = (a) => a >= 1e6 ? `${(a / 1e6).toLocaleString(undefined, { maximumFractionDigits: 3 })} km² (${(a / 1e4).toLocaleString(undefined, { maximumFractionDigits: 1 })} ha)`
    : a >= 1e4 ? `${(a / 1e4).toLocaleString(undefined, { maximumFractionDigits: 3 })} ha (${Math.round(a).toLocaleString()} m²)` : `${a.toLocaleString(undefined, { maximumFractionDigits: 1 })} m²`;
  // on the ellipsoid's mean sphere: great-circle lengths and the area of a polygon
  const R_MEAN = 6371008.8, rad = (d) => d * Math.PI / 180;
  function geoDist(a, b) {
    const dLat = rad(b.lat - a.lat), dLon = rad(b.lng - a.lng);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
    return 2 * R_MEAN * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function geoArea(pts) {
    let s = 0;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      s += rad(b.lng - a.lng) * (2 + Math.sin(rad(a.lat)) + Math.sin(rad(b.lat)));
    }
    return Math.abs(s * R_MEAN * R_MEAN / 2);
  }
  const pathLength = (pts, closed) => pts.reduce((t, p, i) => i ? t + geoDist(pts[i - 1], p) : t, 0) + (closed && pts.length > 2 ? geoDist(pts.at(-1), pts[0]) : 0);

  function startMeasure(mode) {
    stopMeasure(true);
    Object.assign(measure, { on: true, mode, pts: [], heights: [], done: false });
    if (!is3D()) {
      measure.layer = L.featureGroup().addTo(map);
      map.doubleClickZoom.disable();
      map.getContainer().classList.add("measuring");
    }
    renderMeasure();
    status(mode === "height" ? "Click the map to read heights · Esc to stop" : "Click points · double-click or Enter to finish · Backspace removes the last · Esc to stop");
  }
  function stopMeasure(silent) {
    if (!measure.on) return;
    measure.on = false;
    measure.layer?.remove(); measure.layer = null;
    closeProfile();
    clearMeasure3d();
    map.doubleClickZoom.enable();
    map.getContainer().classList.remove("measuring");
    $("#measure-box").classList.add("hidden");
    if (!silent) status("Measure closed");
  }
  function finishMeasure() {
    const need = measure.mode === "area" ? 3 : 2;
    if (measure.mode === "height" || measure.pts.length < need) return;
    measure.done = true;
    measure.hover = null;
    drawMeasure();
    if (measure.mode === "profile") loadProfile();
  }
  function addMeasurePoint(ll, height) {
    const last = measure.pts.at(-1);   // the two clicks of a double-click (which finishes) give the same point twice
    if (last && !measure.done && (is3D() ? geoDist(last, ll) < 0.5 : map.latLngToContainerPoint(last).distanceTo(map.latLngToContainerPoint(ll)) < 4)) return;
    if (measure.done) { measure.pts = []; measure.heights = []; measure.done = false; closeProfile(); }   // a click after finishing starts a new one
    measure.pts.push(ll);
    measure.heights.push(height ?? null);
    drawMeasure();
    if (measure.mode === "height") readHeight(measure.pts.length - 1);
  }

  // ---- 2D: Leaflet clicks and drawing
  map.on("click", (e) => { if (measure.on && !is3D()) addMeasurePoint(e.latlng); });
  map.on("dblclick", (e) => { if (measure.on && !is3D()) { L.DomEvent.stop(e); finishMeasure(); } });
  map.on("mousemove", (e) => { if (measure.on && !is3D() && !measure.done && measure.mode !== "height" && measure.pts.length) { measure.hover = e.latlng; drawMeasure(); } });
  function drawMeasure() {
    if (is3D()) { drawMeasure3d(); renderMeasure(); return; }
    const g = measure.layer;
    if (!g) return;
    g.clearLayers();
    const pts = measure.hover ? [...measure.pts, measure.hover] : measure.pts, style = { color: "#e11d48", weight: 2.5, dashArray: measure.done ? null : "6 5", interactive: false };
    if (measure.mode === "area" && pts.length >= 3) L.polygon(pts, { ...style, fillOpacity: 0.12 }).addTo(g);
    else if (measure.mode !== "height" && pts.length >= 2) L.polyline(pts, style).addTo(g);
    measure.pts.forEach((p, i) => {
      const m = L.circleMarker(p, { radius: 4.5, color: "#fff", weight: 2, fillColor: "#e11d48", fillOpacity: 1, interactive: false }).addTo(g);
      if (measure.mode === "height") m.bindTooltip(measure.heights[i] == null ? "…" : measure.heights[i] === false ? "no height here" : `${fmt(measure.heights[i], 1)} m`, { permanent: true, direction: "top", offset: [0, -6], className: "measure-tip" });
    });
    renderMeasure();
  }

  // ---- heights: in 3D from the land; in 2D from a DEM in Contents (the file's own values)
  async function readHeight(i) {
    const p = measure.pts[i];
    if (is3D()) { drawMeasure(); return; }
    const dems = layers.filter((l) => l.visible && isSurface(l)), cands = dems.length ? dems : layers.filter((l) => l.visible && l.type === "raster" && (l.info?.count || 0) === 1);
    let h = false, from = null;
    for (const l of cands) {
      try {
        const r = await api("/api/analyze/pixel", { method: "POST", json: { path: l.path, band_map: {}, scale: 1, offset: 0, lat: p.lat, lon: p.lng } });
        const v = r.inside ? r.raw[(l.render?.band || 1) - 1]?.value : null;
        if (v != null) { h = v; from = l.name; break; }
      } catch {}
    }
    if (measure.pts[i] !== p) return;   // cleared meanwhile
    measure.heights[i] = h;
    measure.from = from || (cands.length ? null : "none");
    drawMeasure();
  }

  // ---- the result box (top of the map)
  function measureResult() {
    const pts = measure.pts, m = measure.mode;
    if (m === "height") {
      const hs = measure.heights.filter((h) => typeof h === "number");
      if (!pts.length) return { main: "Click the map to read the height there", sub: is3D() ? "" : layers.some((l) => l.visible && isSurface(l)) ? "" : "Add a DEM (a single-band GeoTIFF of heights) to read heights on a 2D map" };
      const last = measure.heights.at(-1);
      return { main: last == null ? "Reading…" : last === false ? "No height here" : `${fmt(last, 1)} m`,
               sub: [`${pts.at(-1).lat.toFixed(5)}, ${pts.at(-1).lng.toFixed(5)}`, measure.from && measure.from !== "none" && !is3D() ? `from ${measure.from}` : "",
                     hs.length > 1 ? `${hs.length} points · lowest ${fmt(Math.min(...hs), 1)} m · highest ${fmt(Math.max(...hs), 1)} m · difference ${fmt(Math.max(...hs) - Math.min(...hs), 1)} m` : ""].filter(Boolean).join(" · "),
               copy: last != null && last !== false ? `${fmt(last, 2)} m at ${pts.at(-1).lat.toFixed(6)}, ${pts.at(-1).lng.toFixed(6)}` : "" };
    }
    const live = measure.hover && !measure.done ? [...pts, measure.hover] : pts;
    if (live.length < 2) return { main: m === "area" ? "Click the corners of the area" : m === "profile" ? "Draw the line of the profile: click points, double-click to finish" : "Click the start, then each point along the way",
      sub: m === "profile" && !profileSource() ? "Add a DEM (a single-band GeoTIFF of heights) first: the profile is read from it" : "" };
    if (m === "profile" && measure.done && measure.prof) {
      const v = measure.prof.value.filter((x) => x != null);
      if (!v.length) return { main: "No heights along this line", sub: `the line is outside ${measure.prof.name}` };
      return { main: `${fmtLen(measure.prof.length)} · ${fmt(Math.min(...v), 0)} – ${fmt(Math.max(...v), 0)} m`,
               sub: `climb ${fmt(measure.prof.up, 0)} m · descent ${fmt(measure.prof.down, 0)} m · from ${measure.prof.name}`, copy: `${fmtLen(measure.prof.length)}, heights ${fmt(Math.min(...v), 1)}–${fmt(Math.max(...v), 1)} m` };
    }
    if (m === "area") {
      if (live.length < 3) return { main: "Click at least 3 corners", sub: `side ${fmtLen(geoDist(live[0], live[1]))}` };
      const a = is3D() ? area3d(live) : geoArea(live), per = pathLength(live, true);
      return { main: fmtArea(a), sub: `perimeter ${fmtLen(per)} · ${live.length} corners${measure.done ? "" : " · double-click to finish"}`, copy: `${fmtArea(a)}, perimeter ${fmtLen(per)}` };
    }
    const flat = pathLength(live), ground = is3D() ? groundLength3d(live) : null;
    const segs = live.length - 1, lastSeg = geoDist(live.at(-2), live.at(-1));
    return { main: ground ? `${fmtLen(ground.length)} along the ground` : fmtLen(flat),
             sub: [ground ? `${fmtLen(flat)} flat · climb ${fmt(ground.up, 0)} m · descent ${fmt(ground.down, 0)} m` : "", `${segs} segment${segs === 1 ? "" : "s"}`, segs > 1 ? `last ${fmtLen(lastSeg)}` : "",
                   measure.done ? "" : "double-click to finish"].filter(Boolean).join(" · "),
             copy: ground ? `${fmtLen(ground.length)} along the ground (${fmtLen(flat)} flat)` : fmtLen(flat) };
  }
  function renderMeasure() {
    if (!measure.on) return;
    const r = measureResult(), box = $("#measure-box");
    box.innerHTML = `<div class="mb-head"><span class="mb-ic">${svg({ distance: "mdist", area: "marea", height: "mheight", profile: "mprofile" }[measure.mode])}</span>
        <b>${MEASURE_TITLE[measure.mode]}</b>
        <span class="mb-modes">${Object.entries(MEASURE_TITLE).map(([k, t]) => `<button type="button" data-mm="${k}" class="${k === measure.mode ? "on" : ""}" title="Measure ${t.toLowerCase()}">${t}</button>`).join("")}</span>
        <button type="button" class="x" data-mx title="Stop measuring (Esc)">×</button></div>
      <div class="mb-main">${esc(r.main)}</div>${r.sub ? `<div class="mb-sub">${esc(r.sub)}</div>` : ""}
      <div class="mb-acts">${r.copy ? `<button type="button" class="btn small" data-mcopy>Copy</button>` : ""}
        ${measure.done || (measure.mode === "height" && measure.pts.length) ? `<button type="button" class="btn small" data-mkeep title="Add it to Contents as a vector layer">Keep as layer</button>` : ""}
        ${measure.pts.length ? `<button type="button" class="btn small ghost" data-mclear>Clear</button>` : ""}</div>`;
    box.classList.remove("hidden");
    $$("[data-mm]", box).forEach((b) => b.onclick = () => startMeasure(b.dataset.mm));
    $("[data-mx]", box).onclick = () => stopMeasure();
    $("[data-mcopy]", box)?.addEventListener("click", () => copyText(r.copy, "Measurement copied"));
    $("[data-mclear]", box)?.addEventListener("click", () => { measure.pts = []; measure.heights = []; measure.done = false; measure.hover = null; drawMeasure(); });
    $("[data-mkeep]", box)?.addEventListener("click", keepMeasure);
  }
  // the measurement as a vector layer (its length / area / heights as attributes)
  function keepMeasure() {
    const pts = measure.pts, c = (p) => [+p.lng.toFixed(7), +p.lat.toFixed(7)], r = measureResult();
    let features;
    if (measure.mode === "height") features = pts.map((p, i) => ({ type: "Feature", geometry: { type: "Point", coordinates: c(p) }, properties: { height_m: typeof measure.heights[i] === "number" ? +measure.heights[i].toFixed(2) : null } }));
    else if (measure.mode === "area") features = [{ type: "Feature", geometry: { type: "Polygon", coordinates: [[...pts.map(c), c(pts[0])]] },
      properties: { area_m2: Math.round(is3D() ? area3d(pts) : geoArea(pts)), perimeter_m: Math.round(pathLength(pts, true)) } }];
    else if (measure.mode === "profile" && measure.prof) features = [{ type: "Feature", geometry: { type: "LineString", coordinates: pts.map(c) },
      properties: { length_m: Math.round(measure.prof.length), climb_m: Math.round(measure.prof.up), descent_m: Math.round(measure.prof.down),
                    min_m: +Math.min(...measure.prof.value.filter((x) => x != null)).toFixed(1), max_m: +Math.max(...measure.prof.value.filter((x) => x != null)).toFixed(1) } }];
    else features = [{ type: "Feature", geometry: { type: "LineString", coordinates: pts.map(c) },
      properties: { length_m: Math.round(pathLength(pts)), ...(is3D() ? { ground_length_m: Math.round(groundLength3d(pts).length) } : {}) } }];
    addVectorLayer({ type: "FeatureCollection", features }, `Measured ${MEASURE_TITLE[measure.mode].toLowerCase()} · ${r.main}`.slice(0, 80), { color: "#e11d48", zoom: false });
    toast("Kept as a layer in Contents");
  }
  document.addEventListener("keydown", (e) => {
    if (!measure.on || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || $("dialog[open]")) return;
    if (e.key === "Escape") { e.preventDefault(); stopMeasure(); }
    else if (e.key === "Enter") { e.preventDefault(); finishMeasure(); }
    else if (e.key === "Backspace" && measure.pts.length && !measure.done) {
      e.preventDefault(); e.stopImmediatePropagation();   // not "remove the selected layer"
      measure.pts.pop(); measure.heights.pop(); drawMeasure();
    }
  }, true);

  // ---- 3D: clicks on the scene (a click, not a drag), drawing over the land
  function measurePoint3d(e) {
    const hit = pick3d(e);
    if (!hit) return null;
    const [lon, lat] = fromMerc(...fromScene(hit.point.x, hit.point.y));
    return { ll: L.latLng(lat, lon), h: hit.object.userData.ground || !v3.surfaces.length ? null : hit.point.z / v3.k + v3.base };
  }
  let mDown = null;
  $("#map3d").addEventListener("pointerdown", (e) => { if (measure.on && e.button === 0 && e.target.classList.contains("v3-canvas")) mDown = [e.clientX, e.clientY]; }, true);
  $("#map3d").addEventListener("pointerup", (e) => {
    if (!measure.on || !mDown || e.button !== 0) return;
    const moved = Math.hypot(e.clientX - mDown[0], e.clientY - mDown[1]);
    mDown = null;
    if (moved > 4) return;   // that was an orbit or a pan
    const p = measurePoint3d(e);
    if (p) addMeasurePoint(p.ll, measure.mode === "height" ? (p.h ?? false) : p.h);
  }, true);
  $("#map3d").addEventListener("dblclick", (e) => { if (measure.on) { e.stopImmediatePropagation(); finishMeasure(); } }, true);
  // a measured line along the land: its length with the heights, the climb and the descent (heights at true scale)
  function groundLength3d(pts) {
    let length = 0, up = 0, down = 0;
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = toMerc(pts[i - 1].lng, pts[i - 1].lat), [x1, y1] = toMerc(pts[i].lng, pts[i].lat);
      const n = Math.min(400, Math.max(8, Math.ceil(geoDist(pts[i - 1], pts[i]) / 10))), flat = geoDist(pts[i - 1], pts[i]) / n;
      let prev = heightAt(x0, y0) / v3.k;
      for (let k = 1; k <= n; k++) {
        const z = heightAt(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) / v3.k, dz = z - prev;
        length += Math.hypot(flat, dz);
        if (dz > 0) up += dz; else down -= dz;
        prev = z;
      }
    }
    return { length, up, down };
  }
  const area3d = (pts) => geoArea(pts);   // the area seen from above, as in a 2D map
  function clearMeasure3d() {
    if (!measure.group) return;
    measure.group.parent?.remove(measure.group);
    measure.group.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
    measure.group = null;
    render3d();
  }
  function drawMeasure3d() {
    if (!v3.on || !v3.T) return;
    clearMeasure3d();
    if (!measure.on || !measure.pts.length) return;
    const T = v3.T, g = measure.group = new T.Group(), lift = 2;
    const at = (ll) => { const [mx, my] = toMerc(ll.lng, ll.lat), [x, y] = toScene(mx, my); return [x, y, heightAt(mx, my) + lift, mx, my]; };
    const ring = measure.mode === "area" && measure.pts.length > 2 ? [...measure.pts, measure.pts[0]] : measure.pts;
    if (measure.mode !== "height" && ring.length > 1) {
      const line = [];
      for (let i = 1; i < ring.length; i++) {   // follows the land
        const a = at(ring[i - 1]), b = at(ring[i]), n = 64;
        for (let k = 0; k <= n; k++) {
          const mx = a[3] + (b[3] - a[3]) * k / n, my = a[4] + (b[4] - a[4]) * k / n, [x, y] = toScene(mx, my);
          line.push(x, y, heightAt(mx, my) + lift);
        }
      }
      const geo = new T.BufferGeometry();
      geo.setAttribute("position", new T.Float32BufferAttribute(line, 3));
      const l = new T.Line(geo, new T.LineBasicMaterial({ color: 0xe11d48, depthTest: false, transparent: true }));
      l.renderOrder = 5000;
      g.add(l);
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.Float32BufferAttribute(measure.pts.flatMap((p) => at(p).slice(0, 3)), 3));
    const dots = new T.Points(geo, new T.PointsMaterial({ color: 0xe11d48, size: 9, sizeAttenuation: false, depthTest: false, transparent: true }));
    dots.renderOrder = 5001;
    g.add(dots);
    if (measure.mode === "height") measure.pts.forEach((p) => {   // a pin from the ground up
      const [x, y, z] = at(p), h = Math.max(20, v3.camera.position.distanceTo(v3.controls.target) / 25);
      const pg = new T.BufferGeometry();
      pg.setAttribute("position", new T.Float32BufferAttribute([x, y, z, x, y, z + h], 3));
      const pin = new T.Line(pg, new T.LineBasicMaterial({ color: 0xe11d48, depthTest: false, transparent: true }));
      pin.renderOrder = 5000;
      g.add(pin);
    });
    v3.scene.add(g);
    render3d();
  }

  // ---- View ▸ Measure ▸ Profile: the heights along a line, as a chart under the map (from a DEM; values as stored)
  function profileSource() {
    const vis = (v3.src || layers).filter((l) => l.visible && l.type === "raster" && l.path);
    return vis.find(isSurface) || vis.find((l) => (l.info?.count || 0) === 1) || null;
  }
  async function loadProfile() {
    const l = profileSource();
    if (!l) { measure.prof = null; renderMeasure(); return toast("Add a DEM (a single-band GeoTIFF of heights) to see a profile", true); }
    const pts = measure.pts;
    try {
      const r = await api("/api/rasters/profile", { method: "POST", json: { path: l.path, band: l.render?.band || 1, samples: 300, coords: pts.map((p) => [p.lng, p.lat]) } });
      if (measure.pts !== pts || !measure.on) return;
      let up = 0, down = 0, prev = null;
      r.value.forEach((v) => { if (v != null && prev != null) { if (v > prev) up += v - prev; else down += prev - v; } if (v != null) prev = v; });
      measure.prof = { ...r, up, down, name: l.name };
      renderMeasure();
      drawProfile();
    } catch (e) { toast(e, true); }
  }
  function closeProfile() { measure.prof = null; $("#profile-box").classList.add("hidden"); measure.profMark?.remove(); measure.profMark = null; }
  // the chart: height against distance; the pointer on it shows the place on the map
  function drawProfile() {
    const p = measure.prof, box = $("#profile-box");
    const vals = p.value.filter((v) => v != null);
    if (!vals.length) return box.classList.add("hidden");
    const W = 760, H = 170, mL = 52, mR = 14, mT = 12, mB = 26, lo = Math.min(...vals), hi = Math.max(...vals), span = Math.max(1, hi - lo);
    const y0 = lo - span * 0.08, y1 = hi + span * 0.08;
    const X = (d) => mL + (d / p.length) * (W - mL - mR), Y = (v) => mT + (1 - (v - y0) / (y1 - y0)) * (H - mT - mB);
    let line = "", area = "", started = false;
    p.distance.forEach((d, i) => {
      const v = p.value[i];
      if (v == null) { started = false; return; }
      line += `${started ? "L" : "M"}${X(d).toFixed(1)},${Y(v).toFixed(1)}`;
      started = true;
    });
    const firstI = p.value.findIndex((v) => v != null), lastI = p.value.length - 1 - [...p.value].reverse().findIndex((v) => v != null);
    area = `M${X(p.distance[firstI])},${H - mB}` + p.distance.map((d, i) => p.value[i] == null ? "" : `L${X(d).toFixed(1)},${Y(p.value[i]).toFixed(1)}`).join("") + `L${X(p.distance[lastI])},${H - mB}Z`;
    const ticks = (a, b, n) => { const step = 10 ** Math.floor(Math.log10((b - a) / n)), m = [1, 2, 5, 10].find((k) => (b - a) / (k * step) <= n) * step; const out = []; for (let t = Math.ceil(a / m) * m; t <= b; t += m) out.push(+t.toFixed(6)); return out; };
    const yt = ticks(y0, y1, 4), xt = ticks(0, p.length, 6);
    box.innerHTML = `<div class="pf-head"><b>Elevation profile</b><span>${esc(p.name)} · ${fmtLen(p.length)} · ${fmt(lo, 0)}–${fmt(hi, 0)} m · climb ${fmt(p.up, 0)} m · descent ${fmt(p.down, 0)} m</span>
        <button type="button" class="btn small ghost" id="pf-csv">CSV</button><button type="button" class="x" id="pf-x" title="Close">×</button></div>
      <svg viewBox="0 0 ${W} ${H}" class="pf-svg" preserveAspectRatio="none">
        ${yt.map((t) => `<line x1="${mL}" x2="${W - mR}" y1="${Y(t)}" y2="${Y(t)}" class="pf-grid"/><text x="${mL - 6}" y="${Y(t) + 4}" text-anchor="end" class="pf-tick">${fmt(t, 0)}</text>`).join("")}
        ${xt.map((t) => `<text x="${X(t)}" y="${H - 8}" text-anchor="middle" class="pf-tick">${t >= 1000 ? `${+(t / 1000).toFixed(2)} km` : `${fmt(t, 0)} m`}</text>`).join("")}
        <path d="${area}" class="pf-area"/><path d="${line}" class="pf-line"/>
        <line id="pf-cur" class="pf-cur" y1="${mT}" y2="${H - mB}" x1="-10" x2="-10"/><circle id="pf-dot" r="4" class="pf-dot" cx="-10" cy="-10"/>
        <text id="pf-lab" class="pf-lab" x="0" y="${mT + 10}"></text>
        <rect x="${mL}" y="${mT}" width="${W - mL - mR}" height="${H - mT - mB}" fill="transparent" id="pf-hit"/></svg>`;
    box.classList.remove("hidden");
    $("#pf-x").onclick = () => closeProfile();
    $("#pf-csv").onclick = () => {
      const csv = "distance_m,lon,lat,height_m\n" + p.distance.map((d, i) => `${d.toFixed(1)},${p.lon[i].toFixed(6)},${p.lat[i].toFixed(6)},${p.value[i] ?? ""}`).join("\n");
      download(URL.createObjectURL(new Blob([csv], { type: "text/csv" })), "elevation_profile.csv");
    };
    const svgEl = $(".pf-svg", box);
    $("#pf-hit").onmousemove = (e) => {
      const r = svgEl.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * W;
      const d = Math.max(0, Math.min(p.length, (x - mL) / (W - mL - mR) * p.length));
      let i = Math.round(d / p.length * (p.distance.length - 1));
      const v = p.value[i];
      $("#pf-cur").setAttribute("x1", X(p.distance[i])); $("#pf-cur").setAttribute("x2", X(p.distance[i]));
      $("#pf-dot").setAttribute("cx", v == null ? -10 : X(p.distance[i])); $("#pf-dot").setAttribute("cy", v == null ? -10 : Y(v));
      const lab = $("#pf-lab");
      lab.textContent = `${fmtLen(p.distance[i])} · ${v == null ? "no data" : `${fmt(v, 1)} m`}`;
      lab.setAttribute("x", Math.min(W - mR - 4, X(p.distance[i]) + 6)); lab.setAttribute("text-anchor", X(p.distance[i]) > W - 160 ? "end" : "start");
      if (X(p.distance[i]) > W - 160) lab.setAttribute("x", X(p.distance[i]) - 6);
      const ll = L.latLng(p.lat[i], p.lon[i]);   // the same place on the map
      if (!is3D()) {
        if (!measure.profMark) measure.profMark = L.circleMarker(ll, { radius: 6, color: "#fff", weight: 2, fillColor: "#0ea5e9", fillOpacity: 1, interactive: false }).addTo(map);
        else measure.profMark.setLatLng(ll);
      } else showProfilePoint3d(ll);
    };
    $("#pf-hit").onmouseleave = () => { measure.profMark?.remove(); measure.profMark = null; if (is3D()) showProfilePoint3d(null); };
  }
  function showProfilePoint3d(ll) {
    if (!v3.T || !measure.group) return;
    measure.group.children.filter((o) => o.userData.profDot).forEach((o) => { measure.group.remove(o); o.geometry.dispose(); o.material.dispose(); });
    if (ll) {
      const [mx, my] = toMerc(ll.lng, ll.lat), [x, y] = toScene(mx, my), g = new v3.T.BufferGeometry();
      g.setAttribute("position", new v3.T.Float32BufferAttribute([x, y, heightAt(mx, my) + 3], 3));
      const d = new v3.T.Points(g, new v3.T.PointsMaterial({ color: 0x0ea5e9, size: 12, sizeAttenuation: false, depthTest: false, transparent: true }));
      d.renderOrder = 5002; d.userData.profDot = true;
      measure.group.add(d);
    }
    render3d();
  }
