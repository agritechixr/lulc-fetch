  // Coordinate systems: data without one (a shapefile without .prj, a GeoTIFF or a world-file picture without a system,
  // a GeoPackage layer marked undefined) asks the user: WGS 84, a system they choose, control points, or none. Any layer's
  // system can be changed later (right-click ▸ Coordinate system…): corrected (the data is really in another one) or
  // converted (a copy in another system). Server: /api/crs/* · lulc_fetch/crs_tools.py.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ the coordinate-system dialog
  const crsDlg = document.createElement("dialog");
  crsDlg.className = "modal";
  crsDlg.id = "dlg-crs";
  document.body.appendChild(crsDlg);
  const mapNear = () => { const c = map.getCenter(); return { lon: +c.lng.toFixed(4), lat: +c.lat.toFixed(4) }; };
  const crsLabel = (c) => c ? `${c.name}${c.epsg ? ` (EPSG:${c.epsg})` : ""}` : "none";
  const isDegrees = (b) => !b || (b[0] >= -180 && b[2] <= 180 && b[1] >= -90 && b[3] <= 90);
  /** [minx, miny, maxx, maxy] of features' raw coordinates */
  function rawBounds(features) {
    const b = [Infinity, Infinity, -Infinity, -Infinity];
    const walk = (c) => { if (typeof c?.[0] === "number") { b[0] = Math.min(b[0], c[0]); b[1] = Math.min(b[1], c[1]); b[2] = Math.max(b[2], c[0]); b[3] = Math.max(b[3], c[1]); } else c?.forEach?.(walk); };
    (features || []).forEach((f) => walk(f.geometry?.coordinates));
    return Number.isFinite(b[0]) ? b : null;
  }

  /** a search box with suggestions and results; returns { get(): {crs, name, epsg} | null } */
  function crsPicker(box, { suggestions = [], value = null, onPick } = {}) {
    let chosen = value || (suggestions[0] ? { crs: suggestions[0].crs, name: suggestions[0].name, epsg: suggestions[0].epsg } : null), t = 0;
    box.innerHTML = `<div class="crs-chosen"></div>
      <input type="search" class="crs-q" placeholder="Search: a name or EPSG code, e.g. UTM 43, India, 32644, Kalianpur" autocomplete="off" spellcheck="false">
      <div class="crs-list"></div>
      <details class="crs-custom"><summary class="hint">Paste a definition (WKT, PROJ string or EPSG:…)</summary>
        <textarea rows="3" class="mono" placeholder="+proj=utm +zone=43 +datum=WGS84   or   PROJCS[…]"></textarea>
        <button type="button" class="btn small">Use this</button><span class="hint crs-custom-msg"></span></details>`;
    const showChosen = () => { $(".crs-chosen", box).innerHTML = chosen ? `Chosen: <b>${esc(crsLabel(chosen))}</b>` : `<span class="hint">Nothing chosen yet</span>`; onPick?.(chosen); };
    const row = (r, why) => `<button type="button" class="crs-item ${chosen?.crs === r.crs ? "on" : ""}" data-crs="${esc(r.crs)}" data-name="${esc(r.name)}" data-epsg="${r.epsg || r.code || ""}">
        <b>${esc(r.name)}</b> <code>${esc(r.crs.startsWith("EPSG:") ? r.crs : "custom")}</code>${why ? `<small>${esc(why)}</small>` : r.area ? `<small>${esc(r.area)}${r.covers ? " · covers the map" : ""}</small>` : ""}</button>`;
    const wire = () => $$(".crs-item", box).forEach((b) => b.onclick = () => {
      chosen = { crs: b.dataset.crs, name: b.dataset.name, epsg: +b.dataset.epsg || null };
      $$(".crs-item", box).forEach((x) => x.classList.toggle("on", x === b)); showChosen();
    });
    const list = (results) => {
      const sug = suggestions.map((s) => row(s, s.why)).join("");
      $(".crs-list", box).innerHTML = (sug ? `<div class="home-label">Suggested</div>${sug}` : "") +
        (results?.length ? `<div class="home-label">Found</div>${results.map((r) => row(r)).join("")}` : results ? `<p class="hint">No coordinate system matches.</p>` : "");
      wire();
    };
    $(".crs-q", box).oninput = () => {
      clearTimeout(t);
      const q = $(".crs-q", box).value.trim();
      if (!q) return list(null);
      t = setTimeout(async () => {
        const n = mapNear();
        try { list((await api(`/api/crs/search?q=${encodeURIComponent(q)}&lon=${n.lon}&lat=${n.lat}`)).results); } catch (e) { toast(e, true); }
      }, 200);
    };
    $(".crs-custom button", box).onclick = async () => {
      const text = $(".crs-custom textarea", box).value.trim(), msg = $(".crs-custom-msg", box);
      if (!text) return;
      try { const d = await api(`/api/crs/describe?text=${encodeURIComponent(text)}`); chosen = { crs: d.crs, name: d.name, epsg: d.epsg }; msg.textContent = ` ✓ ${d.name}`; showChosen(); }
      catch (e) { msg.textContent = ` ${e.message}`; }
    };
    list(null); showChosen();
    return { get: () => chosen };
  }

  /** data without a coordinate system: ask what to do. what: "vector" | "raster"; bounds: its raw [minx, miny, maxx, maxy];
   *  resolves { mode: "crs", crs } (WGS 84 or chosen), { mode: "gcp" } or { mode: "none" } */
  async function askCrs({ name, why, what = "vector", bounds = null, hasGrid = true, suggestions = null }) {
    const n = mapNear();
    if (!suggestions) {
      try { suggestions = (await api("/api/crs/suggest", { method: "POST", json: { bounds, ...n } })).suggestions; } catch { suggestions = []; }
    }
    const deg = isDegrees(bounds), canAssign = what === "vector" || hasGrid;
    const firstChoice = !canAssign ? "gcp" : deg ? "wgs84" : "pick";
    return new Promise((resolve) => {
      crsDlg.innerHTML = `<div class="modal-head"><h3>“${esc(name)}” has no coordinate system</h3><button class="x" data-close>×</button></div>
        <div class="modal-body">
          <p class="hint" style="margin-top:0">${esc(why || "Its file doesn't say which coordinate system its numbers are in.")}${bounds ? ` Its numbers run from ${fmtv(bounds[0])}, ${fmtv(bounds[1])} to ${fmtv(bounds[2])}, ${fmtv(bounds[3])}${deg ? " (they could be longitude / latitude)" : " (not degrees: metres or feet of some projection)"}.` : ""}</p>
          <div class="opts">
            <label class="opt ${canAssign ? "" : "disabled"}"><input type="radio" name="crs-mode" value="wgs84" ${canAssign ? "" : "disabled"}><span><b>WGS 84: longitude / latitude</b><small>${deg ? "The usual default (GPS, Google Maps, most web data)." : "⚠ Its numbers aren't degrees: it would land in the wrong place."}</small></span></label>
            <label class="opt ${canAssign ? "" : "disabled"}"><input type="radio" name="crs-mode" value="pick" ${canAssign ? "" : "disabled"}><span><b>Choose its coordinate system</b><small>UTM, India's grids, or any EPSG code or definition.</small></span></label>
            <label class="opt"><input type="radio" name="crs-mode" value="gcp"><span><b>Place it with control points (GCPs)</b><small>Click places on it and the same places on the map${what === "vector" ? ": for drawings, plans or local survey coordinates" : ": for scans, plans or pictures"}.</small></span></label>
            <label class="opt"><input type="radio" name="crs-mode" value="none"><span><b>Keep it without a coordinate system</b><small>${what === "vector" ? "It stays in Contents (⚠) but isn't drawn on the map or used by tools until you give it one (right-click ▸ Coordinate system…)." : "It opens as a picture in the data viewer; you can place it later."}</small></span></label>
          </div>
          <div class="crs-pick hidden" style="margin-top:10px"></div>
        </div>
        <div class="modal-foot"><button class="btn" data-close>Cancel</button><button class="btn primary" data-ok>OK</button></div>`;
      const picker = crsPicker($(".crs-pick", crsDlg), { suggestions: suggestions.filter((s) => s.crs !== "EPSG:4326" || !deg) });
      const sync = () => $(".crs-pick", crsDlg).classList.toggle("hidden", $('input[name="crs-mode"]:checked', crsDlg)?.value !== "pick");
      $$('input[name="crs-mode"]', crsDlg).forEach((r) => r.onchange = sync);
      $(`input[name="crs-mode"][value="${firstChoice}"]`, crsDlg).checked = true;
      sync();
      let done = false;
      const finish = (v) => { if (done) return; done = true; crsDlg.close(); resolve(v); };
      $$("[data-close]", crsDlg).forEach((b) => b.onclick = () => finish({ mode: "none", cancelled: true }));
      crsDlg.onclose = () => finish({ mode: "none", cancelled: true });
      $("[data-ok]", crsDlg).onclick = () => {
        const mode = $('input[name="crs-mode"]:checked', crsDlg).value;
        if (mode === "wgs84") return finish({ mode: "crs", crs: "EPSG:4326" });
        if (mode === "pick") { const c = picker.get(); if (!c) return toast("Choose a coordinate system from the list", true); return finish({ mode: "crs", crs: c.crs }); }
        finish({ mode });
      };
      crsDlg.showModal();
    });
  }

  // ------------------------------------------------------------------ data arriving without a coordinate system
  /** a vector layer whose numbers have no system yet: asks, then adds it placed, unplaced, or for control points */
  async function addVectorAskCrs(fc, name, why, opts = {}) {
    const bounds = fc.raw_bounds || null;
    const a = await askCrs({ name, why, what: "vector", bounds });
    if (a.mode === "crs") {
      try {
        const r = await api("/api/crs/vector", { method: "POST", json: { geojson: { type: "FeatureCollection", features: fc.features }, actual: a.crs } });
        return addVectorLayer({ type: "FeatureCollection", features: r.features }, name, { ...opts, crs: r.crs });
      } catch (e) { toast(`${name}: ${e.message}. It was kept without a coordinate system.`, true); }
    }
    const l = addLayer({ type: "unplaced", name, geojson: { type: "FeatureCollection", features: fc.features }, rawBounds: bounds, color: nextColor(), ...opts }, { zoom: false });
    if (a.mode === "gcp") openTool("georef", { layer: l.id });
    else toast(`${name} is in Contents without a coordinate system (not on the map). Right-click it ▸ Coordinate system… to place it.`);
    return l;
  }
  /** a raster (GeoTIFF in the workspace) without a coordinate system: asks, then opens it placed, for control points or
   *  as a picture */
  async function addRasterAskCrs(path, name, why) {
    const n = mapNear();
    let info;
    try { info = await api(`/api/crs/raster?path=${encodeURIComponent(path)}&lon=${n.lon}&lat=${n.lat}`); } catch (e) { return toast(e, true); }
    if (info.crs) return addRasterFromPath(path, { name });
    const a = await askCrs({ name, why: why || (info.has_grid ? "It has a pixel grid but doesn't say which coordinate system its numbers are in." : "It has no coordinates at all (no pixel grid)."),
                             what: "raster", bounds: info.bounds, hasGrid: info.has_grid, suggestions: info.suggestions });
    if (a.mode === "crs") {
      try {
        const r = await api("/api/crs/assign-raster", { method: "POST", json: { path, crs: a.crs } });
        return addRasterFromPath(r.path, { name });
      } catch (e) { toast(`${name}: ${e.message}`, true); return addRasterAskCrs(path, name, why); }
    }
    if (a.mode === "gcp") return openTool("georef", { path, name });
    try {   // none: as a picture in the data viewer (Place on map / Georeference later)
      const g = await api(`/api/georef/info?path=${encodeURIComponent(path)}`);
      addItem({ kind: "picture", name, path, width: g.width, height: g.height, bands: g.bands }, { open: true });
    } catch (e) { toast(e, true); }
  }

  // ------------------------------------------------------------------ right-click ▸ Coordinate system…
  async function openCrsDialog(l) {
    const vec = l.type === "vector" || l.type === "unplaced";
    let current = l.type === "unplaced" ? null : vec ? (l.crs || { crs: "EPSG:4326", name: "WGS 84", epsg: 4326 }) : null;
    if (l.type === "raster") {
      try { current = (await api(`/api/crs/raster?path=${encodeURIComponent(l.path)}`)).crs; } catch (e) { return toast(e, true); }
    }
    const n = mapNear();
    let suggestions = [];
    try { suggestions = (await api("/api/crs/suggest", { method: "POST", json: { bounds: l.type === "unplaced" ? l.rawBounds : null, ...n } })).suggestions; } catch {}
    crsDlg.innerHTML = `<div class="modal-head"><h3>Coordinate system · ${esc(l.name)}</h3><button class="x" data-close>×</button></div>
      <div class="modal-body">
        <p style="margin-top:0">${l.type === "unplaced" ? "⚠ <b>No coordinate system</b>: the layer isn't on the map yet." : `Now: <b>${esc(crsLabel(current))}</b>${vec && current?.epsg !== 4326 ? " (shown on the map in WGS 84)" : ""}`}</p>
        <div class="opts">
          <label class="opt"><input type="radio" name="crs-act" value="assign" checked><span><b>${l.type === "unplaced" ? "Give it its coordinate system" : "Correct it: the data is really in another system"}</b><small>The numbers stay; what they mean changes (the layer moves on the map). For data that was read in the wrong system.</small></span></label>
          ${l.type === "unplaced" ? "" : `<label class="opt"><input type="radio" name="crs-act" value="convert"><span><b>Convert a copy to another system (reproject)</b><small>${vec ? "Save the layer with its coordinates in another system (Shapefile or GeoPackage): the places stay where they are." : "A new GeoTIFF on a grid in another system: the places stay where they are."}</small></span></label>`}
          <label class="opt"><input type="radio" name="crs-act" value="gcp"><span><b>Place it with control points (GCPs)</b><small>Click places on it and the same places on the map.</small></span></label>
        </div>
        <div class="crs-pick" style="margin-top:10px"></div>
      </div>
      <div class="modal-foot"><button class="btn" data-close>Cancel</button><button class="btn primary" data-ok>Apply</button></div>`;
    const picker = crsPicker($(".crs-pick", crsDlg), { suggestions });
    const sync = () => $(".crs-pick", crsDlg).classList.toggle("hidden", $('input[name="crs-act"]:checked', crsDlg).value === "gcp");
    $$('input[name="crs-act"]', crsDlg).forEach((r) => r.onchange = sync);
    $$("[data-close]", crsDlg).forEach((b) => b.onclick = () => crsDlg.close());
    crsDlg.onclose = null;
    $("[data-ok]", crsDlg).onclick = async (e) => {
      const act = $('input[name="crs-act"]:checked', crsDlg).value, c = picker.get();
      if (act === "gcp") { crsDlg.close(); return openTool("georef", vec ? { layer: l.id } : { path: l.path, name: l.name }); }
      if (!c) return toast("Choose a coordinate system from the list", true);
      busy(e.currentTarget, "Working…", async () => {
        try {
          if (act === "assign" && vec) {
            const r = await api("/api/crs/vector", { method: "POST", json: { geojson: l.geojson, actual: c.crs, current: l.type === "unplaced" ? null : (l.crs?.crs || "EPSG:4326") } });
            historyStep(`Coordinate system of ${l.name}`, () => {
              Object.assign(l, { type: "vector", geojson: { type: "FeatureCollection", features: r.features }, crs: r.crs });
              delete l.rawBounds;
              buildLeaflet(l); restack(); renderContents(); saveLayers();
            });
            zoomTo(l);
            toast(`${l.name} is now read as ${crsLabel(r.crs)}`);
          } else if (act === "assign") {
            const r = await api("/api/crs/assign-raster", { method: "POST", json: { path: l.path, crs: c.crs } });
            const nl = await addRasterFromPath(r.path, { name: l.name, render: l.render, select: true });
            if (nl) { moveLayer(nl.id, layers.indexOf(l)); removeLayer(l.id); }
            toast(`${l.name} is now in ${crsLabel(r.crs)} (a corrected copy: ${r.path})`);
          } else if (vec) {
            crsDlg.close();
            openExport(l);
            exportCrs(c);
            return;
          } else {
            if (!c.epsg) return toast("Converting needs an EPSG coordinate system: search for it by name or code", true);
            const res = await runJob("/api/raster/resample", { raster: l.path, crs: `EPSG:${c.epsg}`, name: `${safeName(l.name.replace(/\.[^.]+$/, ""))}_epsg${c.epsg}` },
                                     { title: `Converting ${l.name} to EPSG:${c.epsg}` });
            await addRasterFromPath(res.path, { zoom: false });
          }
          crsDlg.close();
        } catch (ex) { if (notCancelled(ex)) toast(ex, true); }
      });
    };
    crsDlg.showModal();
  }
