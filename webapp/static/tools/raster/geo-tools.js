/* Hydrology, Viewshed & line of sight, LiDAR (Analysis ▸ Tools ▸ Raster & terrain); Pansharpen and Spectral unmixing
   (Imagery); Routing (Spatial analysis); Sentinel-5P air quality (Forecast). Jobs (progress, History, Workflows, the
   Assistant); results are added to Contents. Server: webapp/routes/geo_tools.py · lulc_fetch/hydrology.py, visibility.py,
   lidar.py, spectral.py, routing.py, s5p.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const sel = (id, label) => `<label>${label} <select id="${id}"></select></label>`;
  const runRow = (p, label) => `<button class="btn primary" id="${p}-run">${label}</button><p class="hint hidden" id="${p}-error" style="color:var(--err)"></p><div id="${p}-result" class="hidden"></div>`;
  const rasters = (LF) => LF.layers.filter((l) => l.type === "raster" && l.path);
  const pointLayers = (LF) => LF.layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => /Point/.test(f.geometry?.type)));
  const lineLayers = (LF) => LF.layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => /LineString/.test(f.geometry?.type)));
  const fieldsOf = (l) => [...new Set((l?.geojson?.features || []).slice(0, 300).flatMap((f) => Object.keys(f.properties || {})))];

  // the job's files into Contents (rasters, GeoJSON) and its table
  async function addResults(LF, r, name, vecOpts = {}) {
    const { api, addRasterFromPath, addVectorLayer, addItem } = LF;
    const files = [...new Set([...(r.outputs || []), ...(r.path ? [r.path] : [])])];
    for (const f of files) {
      if (/\.tiff?$/i.test(f)) await addRasterFromPath(f, { zoom: false }).catch(() => {});
      else if (/\.geojson$/i.test(f)) { const fc = await api(`/api/vector/read?path=${encodeURIComponent(f)}`); if (fc.features?.length) addVectorLayer(fc, f.split("/").pop().replace(/\.geojson$/i, "") || name, { path: f, zoom: false, ...vecOpts }); }
    }
    if (r.csv) addItem({ kind: "table", name: r.csv.split("/").pop(), path: r.csv });
    return files.length + (r.csv ? 1 : 0);
  }
  function job(LF, p, endpoint, body, describe) {
    LF.runButton(p, async () => {
      const j = await LF.api(endpoint, { method: "POST", json: body() });
      const r = (await LF.trackJob(j, { title: j.title })).result;
      const n = await addResults(LF, r, p);
      LF.showResult(p, `${describe ? describe(r) : ""} <span class="hint">${n} result${n === 1 ? "" : "s"} added to Contents.</span>`);
    });
  }

  // points: from a point layer in Contents, or clicked on the map (shown as small markers until the tool closes)
  const PTS = {};
  const pointsHtml = (id, label, help) => `<div class="gp-pts" id="${id}"><label>${label} ${help ? tip(help) : ""}<select class="gp-src"></select></label>
      <div class="row gp-click-row"><button type="button" class="btn small gp-click">Click on the map</button><button type="button" class="btn small ghost gp-clear">Clear</button><span class="hint gp-n"></span></div></div>`;
  function pointsPicker(LF, id, { max = 500, color = "#dc2626" } = {}) {
    const { $, esc, map, getLayer, status } = LF;
    const st = PTS[id] = { clicked: [], group: L.layerGroup(), picking: false };
    const root = $(`#${id}`), src = $(".gp-src", root);
    const fill = () => {
      const was = src.value;
      src.innerHTML = `<option value="map">Points I click on the map</option>` + pointLayers(LF).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
      if ([...src.options].some((o) => o.value === was)) src.value = was;
      show();
    };
    const show = () => {
      const m = src.value === "map";
      $(".gp-click-row", root).classList.toggle("hidden", !m);
      $(".gp-n", root).textContent = m ? `${st.clicked.length} point${st.clicked.length === 1 ? "" : "s"}` : "";
      if (m) st.group.addTo(map); else st.group.remove();
    };
    const onClick = (e) => {
      if (st.clicked.length >= max) return status(`Up to ${max} points`);
      st.clicked.push([+e.latlng.lng.toFixed(7), +e.latlng.lat.toFixed(7)]);
      L.circleMarker(e.latlng, { radius: 6, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1 }).bindTooltip(String(st.clicked.length), { permanent: true, direction: "right", className: "gp-tip" }).addTo(st.group);
      show();
      if (max === 1 || st.clicked.length >= max) stop();
    };
    const stop = () => { if (!st.picking) return; st.picking = false; map.off("click", onClick); map.getContainer().style.cursor = ""; $(".gp-click", root).textContent = "Click on the map"; };
    $(".gp-click", root).onclick = () => {
      if (st.picking) return stop();
      st.picking = true; map.on("click", onClick); map.getContainer().style.cursor = "crosshair";
      $(".gp-click", root).textContent = "Done"; status("Click points on the map · Done (or Esc) when finished");
    };
    $(".gp-clear", root).onclick = () => { st.clicked = []; st.group.clearLayers(); show(); };
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") stop(); });
    src.onchange = show;
    return {
      fill, stop,
      layer: () => (src.value === "map" ? null : getLayer(src.value)),
      get(what = "points", atLeast = 1) {
        let pts;
        if (src.value === "map") pts = st.clicked;
        else pts = (getLayer(src.value)?.geojson?.features || []).flatMap((f) => f.geometry?.type === "Point" ? [f.geometry.coordinates] : f.geometry?.type === "MultiPoint" ? f.geometry.coordinates : []).map((c) => [c[0], c[1]]);
        if (pts.length < atLeast) throw new Error(src.value === "map" ? `Click ${atLeast === 1 ? "a point" : `at least ${atLeast} ${what}`} on the map first` : `The layer needs at least ${atLeast} ${what}`);
        if (pts.length > max) throw new Error(`Up to ${max} ${what}`);
        return pts;
      },
      names(field) {
        const l = getLayer(src.value);
        return field && l ? l.geojson.features.filter((f) => /Point/.test(f.geometry?.type)).map((f) => String(f.properties?.[field] ?? "")) : null;
      },
      hide() { stop(); st.group.remove(); },
    };
  }

  // shared with the Hydrology menu's tools (tools/hydro/hydro.js)
  LF.geo = { sel, runRow, rasters, pointLayers, lineLayers, fieldsOf, addResults, job, pointsHtml, pointsPicker };

  // ------------------------------------------------------------------ Hydrology in one go (Analysis ▸ Hydrology)
  LF.tool({ id: "rhydro", menu: "hydro", title: "Hydrology in one go", icon: "rhydro", kinds: ["hydrology"],
    subtitle: "From a DEM: fill sinks, flow direction and accumulation, streams with their Strahler order, watersheds of points (or every basin) and the wetness index",
    panel: `<div class="card"><h2>DEM ${tip("Heights in metres. Up to 16 million cells (e.g. 4000 × 4000): clip or resample bigger DEMs first. Copernicus 30 m works well (Insert ▸ Library).")}</h2>
      ${sel("hq-dem", "DEM")}</div>
      <div class="card"><h2>Results</h2><div class="sf-ticks" style="grid-template-columns:1fr 1fr">${[
        ["filled", "Filled DEM", "Sinks (pits) filled to the height where water spills out: every cell drains to the edge.", 1],
        ["direction", "Flow direction (D8)", "Which of the 8 neighbours each cell drains to (1 E, 2 SE, 4 S … 128 NE, as ArcGIS).", 0],
        ["accumulation", "Flow accumulation", "The area draining through each cell (km²): bright lines are the valleys.", 1],
        ["streams", "Streams (raster and lines)", "Cells draining at least the threshold below, and the network as lines with their order.", 1],
        ["order", "Stream order (Strahler)", "1 at the sources; two streams of order n meeting make n + 1.", 1],
        ["basins", "Watersheds", "The area draining to each outlet point (below), or every basin when there are no points.", 0],
        ["twi", "Wetness index (TWI)", "ln(upslope area / tan slope): high where water gathers (valley floors, wet soils).", 0]].map(([v, t, h, on]) =>
        `<label class="check"><input type="checkbox" data-hp="${v}" ${on ? "checked" : ""}> ${t} ${tip(h)}</label>`).join("")}</div>
      <label>A stream drains at least (km²) ${tip("Lower: more, smaller streams. 1 km² suits 30 m DEMs; 0.1 km² for detailed DEMs of small areas.")}<input type="number" id="hq-km2" value="1" min="0.001" step="any"></label>
      <div id="hq-basin-opts" class="hidden">${pointsHtml("hq-pts", "Outlets (pour points)", "Each point is moved to the biggest flow within the snap distance (so a point beside the river lands on it). No points: every basin of the minimum size.")}
        <label>Snap to the river within (m) <input type="number" id="hq-snap" value="150" min="0" step="any"></label>
        <label>Without points: basins of at least (km²) <input type="number" id="hq-minb" placeholder="10 × the stream size" min="0" step="any"></label></div>
      <label>Name <input type="text" id="hq-name" placeholder="automatic" maxlength="80"></label>${runRow("hq", "Compute")}</div>`,
    setup(LF) {
      const { $, $$, fillLayers } = LF;
      const pk = pointsPicker(LF, "hq-pts", { color: "#2563eb" });
      const syncB = () => $("#hq-basin-opts").classList.toggle("hidden", !$('[data-hp="basins"]').checked);
      $('[data-hp="basins"]').onchange = syncB;
      job(LF, "hq", "/api/raster/hydrology", () => {
        const l = LF.getLayer($("#hq-dem").value);
        if (!l) throw new Error("Choose a DEM (add one with Insert ▸ Add data or the Library)");
        const products = $$("#tab-rhydro [data-hp]:checked").map((c) => c.dataset.hp);
        if (!products.length) throw new Error("Tick at least one result");
        let points = [];
        if (products.includes("basins")) { try { points = pk.get("outlets", 0); } catch { points = []; } }
        return { dem: l.path, products, stream_km2: +$("#hq-km2").value || 1, points, snap_m: +$("#hq-snap").value || 0,
                 min_basin_km2: +$("#hq-minb").value > 0 ? +$("#hq-minb").value : null, name: $("#hq-name").value.trim() };
      }, (r) => `<b>${r.cells.toLocaleString()} cells</b>: ${r.sink_cells_filled.toLocaleString()} filled (up to ${LF.fmt(r.max_fill_m, 2)} m); the largest catchment drains ${LF.fmt(r.largest_area_km2, 2)} km²${r.stream_lines != null ? `; ${r.stream_lines.toLocaleString()} stream links, order up to ${r.max_order}` : ""}${r.basins ? `; ${r.basins} watershed${r.basins === 1 ? "" : "s"}` : ""}.`);
      const fill = (pick) => { fillLayers($("#hq-dem"), rasters(LF), { empty: "No raster layer in Contents", pick }); pk.fill(); };
      syncB();
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ Viewshed & line of sight
  LF.tool({ id: "rview", title: "Viewshed & line of sight", icon: "rview", kinds: ["viewshed", "lineofsight"],
    subtitle: "What can be seen from one or more points on a DEM (and by how many), or whether A can see B, with the earth's curvature",
    panel: `<div class="card"><h2>DEM ${tip("Heights in metres; a DSM (with buildings and trees) gives what is really visible, a DTM the bare land.")}</h2>${sel("vw-dem", "DEM")}
      <label>Analysis <select id="vw-op"><option value="viewshed">Viewshed: what the observers see</option><option value="los">Line of sight: can A see B?</option></select></label></div>
      <div class="card"><div id="vw-vs">${pointsHtml("vw-obs", "Observers", "One observer: 1 seen, 0 not. Several (up to 200, e.g. towers): how many of them see each place.")}
        <label>Look no further than (m) <input type="number" id="vw-max" placeholder="no limit" min="0" step="any"></label></div>
      <div id="vw-los" class="hidden">${pointsHtml("vw-ab", "Points A (observer) and B (target)", "Click A, then B, on the map, or a layer whose first two points are A and B.")}</div>
      <label>Observer's eyes above the ground (m) <input type="number" id="vw-oh" value="1.7" min="0" step="any"></label>
      <label>Target above the ground (m) ${tip("0: the ground itself. E.g. 10 to see where a 10 m mast would be visible.")}<input type="number" id="vw-th" value="0" min="0" step="any"></label>
      <label class="check"><input type="checkbox" id="vw-curv" checked> Earth curvature and refraction ${tip("Far things sink below the horizon (about 5 km for eyes at 1.7 m over flat land). Refraction k = 0.13.")}</label>
      <label>Name <input type="text" id="vw-name" placeholder="automatic" maxlength="80"></label>${runRow("vw", "Compute")}</div>`,
    setup(LF) {
      const { $, fillLayers } = LF;
      const obs = pointsPicker(LF, "vw-obs", { max: 200, color: "#16a34a" }), ab = pointsPicker(LF, "vw-ab", { max: 2, color: "#7c3aed" });
      const op = () => $("#vw-op").value;
      $("#vw-op").onchange = () => { $("#vw-vs").classList.toggle("hidden", op() !== "viewshed"); $("#vw-los").classList.toggle("hidden", op() !== "los"); (op() === "los" ? obs : ab).hide(); (op() === "los" ? ab : obs).fill(); };
      LF.runButton("vw", async () => {
        const l = LF.getLayer($("#vw-dem").value);
        if (!l) throw new Error("Choose a DEM");
        const common = { dem: l.path, observer_h: +$("#vw-oh").value || 0, target_h: +$("#vw-th").value || 0, curvature: $("#vw-curv").checked, name: $("#vw-name").value.trim() || (op() === "los" ? "line_of_sight" : "viewshed") };
        const body = op() === "los" ? (() => { const p = ab.get("points (A and B)", 2); return { ...common, a: p[0], b: p[1] }; })()
          : { ...common, observers: obs.get("observers"), max_dist_m: +$("#vw-max").value > 0 ? +$("#vw-max").value : null };
        const j = await LF.api(op() === "los" ? "/api/raster/line-of-sight" : "/api/raster/viewshed", { method: "POST", json: body });
        const r = (await LF.trackJob(j, { title: j.title })).result;
        await addResults(LF, r, body.name, op() === "los" ? { weight: 4, symbology: { mode: "categories", field: "visible", map: { true: "#16a34a", false: "#dc2626" },
          legend: [{ value: "true", label: "Seen", color: "#16a34a" }, { value: "false", label: "Hidden", color: "#dc2626" }] } } : {});
        LF.showResult("vw", op() === "los"
          ? `<b>${r.visible ? "B is visible from A" : "B is hidden from A"}</b> (${LF.fmt(r.distance_m / 1000, 2)} km)${r.first_blocked_m != null ? `; the view is first blocked ${LF.fmt(r.first_blocked_m / 1000, 2)} km from A` : ""}. Green: seen, red: hidden.`
          : `<b>${LF.fmt(r.visible_km2, 2)} km² visible</b> (${r.visible_pct}% of the DEM) from ${r.observers} observer${r.observers === 1 ? "" : "s"}.`);
      });
      const fill = (pick) => { fillLayers($("#vw-dem"), rasters(LF), { empty: "No raster layer in Contents", pick }); (op() === "los" ? ab : obs).fill(); };
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ LiDAR
  LF.tool({ id: "rlidar", title: "LiDAR point cloud", icon: "rlidar", kinds: ["lidar"],
    subtitle: "A .las / .laz point cloud into a ground model (DTM), surface (DSM), height of trees and buildings (CHM), point density and tree tops",
    panel: `<div class="card"><h2>Point cloud ${tip("LAS 1.0–1.4 is read directly. Compressed .laz needs laspy with LAZ support (pip install \"laspy[lazrs]\") or converting to .las first.")}</h2>
      <div class="row"><button type="button" class="btn small" id="ld-folder">Choose a folder…</button><span class="hint" id="ld-where"></span></div>
      ${sel("ld-file", "File")}<div id="ld-info" class="hint"></div>
      <label>Coordinate system ${tip("Only when the file does not say it, e.g. EPSG:32643 (UTM 43N). It must be in metres.")}<input type="text" id="ld-crs" placeholder="from the file"></label></div>
      <div class="card"><h2>Grids</h2><div class="sf-ticks" style="grid-template-columns:1fr 1fr">${[
        ["dtm", "Ground (DTM)", "The bare earth: ground points (class 2), or found with a progressive filter when the file has no classes.", 1],
        ["dsm", "Surface (DSM)", "The highest point in each cell: tree crowns, roofs.", 1],
        ["chm", "Height above ground (CHM)", "DSM − DTM: tree and building heights.", 1],
        ["density", "Point density", "Points per m²: where the survey is thin.", 0],
        ["intensity", "Intensity", "The mean return strength: roads, paint, water look different.", 0],
        ["trees", "Tree tops (points)", "Local highest points of the CHM, with their height. In classified clouds only on vegetation.", 0]].map(([v, t, h, on]) =>
        `<label class="check"><input type="checkbox" data-lp="${v}" ${on ? "checked" : ""}> ${t} ${tip(h)}</label>`).join("")}</div>
      <label>Cell size (m) <input type="number" id="ld-res" value="1" min="0.1" step="any"></label>
      <details><summary class="hint">Ground filter and tree tops</summary>
        <label>Largest building to remove (m) ${tip("The ground filter's biggest window: wider than the largest building in the area.")}<input type="number" id="ld-gw" value="40" min="2" step="any"></label>
        <label>Trees at least (m) <input type="number" id="ld-tmin" value="2" min="0" step="any"></label>
        <label>Crown width about (m) <input type="number" id="ld-tw" value="5" min="1" step="any"></label>
        <label class="check"><input type="checkbox" id="ld-noise" checked> Leave out noise points (classes 7 and 18)</label></details>
      <label>Name <input type="text" id="ld-name" placeholder="from the file" maxlength="80"></label>${runRow("ld", "Make grids")}</div>`,
    setup(LF) {
      const { $, $$, api, esc, pickFolder, prefs, fmt } = LF;
      const list = async (folder) => {
        $("#ld-where").textContent = folder;
        const r = await api(`/api/lidar/files?folder=${encodeURIComponent(folder)}`);
        $("#ld-file").innerHTML = r.files.map((f) => `<option value="${esc(f.path)}">${esc(f.name)} (${fmt(f.mb, 1)} MB)</option>`).join("") || `<option value="">No .las / .laz file here</option>`;
        prefs.set("ld-folder", folder);
        info();
      };
      const info = async () => {
        const p = $("#ld-file").value;
        $("#ld-info").textContent = "";
        if (!p) return;
        try {
          const r = await api(`/api/lidar/info?path=${encodeURIComponent(p)}`);
          $("#ld-info").innerHTML = `${r.points.toLocaleString()} points · LAS ${r.version} · ${esc(r.crs || "no coordinate system in the file")}${r.density_per_m2 ? ` · ${fmt(r.density_per_m2, 1)} pts/m²` : ""}<br>${Object.entries(r.classes).map(([k, v]) => `${esc(k)}: ${v.toLocaleString()}`).join(" · ")}`;
        } catch (e) { $("#ld-info").innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`; }
      };
      $("#ld-file").onchange = info;
      $("#ld-folder").onclick = async () => { const f = await pickFolder({ title: "The folder with your .las / .laz files", okLabel: "Use this folder" }); if (f) list(f).catch((e) => LF.toast(e, true)); };
      job(LF, "ld", "/api/lidar/grid", () => {
        if (!$("#ld-file").value) throw new Error("Choose a folder with a .las or .laz file");
        const products = $$("#tab-rlidar [data-lp]:checked").map((c) => c.dataset.lp);
        if (!products.length) throw new Error("Tick at least one grid");
        return { path: $("#ld-file").value, products, res: +$("#ld-res").value || 1, crs: $("#ld-crs").value.trim() || null, ground_window_m: +$("#ld-gw").value || 40,
                 tree_min_h: +$("#ld-tmin").value || 2, tree_window_m: +$("#ld-tw").value || 5, drop_noise: $("#ld-noise").checked, name: $("#ld-name").value.trim() };
      }, (r) => `<b>${r.points.toLocaleString()} points → ${r.size[0].toLocaleString()} × ${r.size[1].toLocaleString()} cells</b> of ${r.res} m. Ground from ${esc(r.ground)} (${r.ground_pct}% of cells); tallest ${fmt(r.max_height_m, 1)} m${r.trees != null ? `; ${r.trees.toLocaleString()} tree tops` : ""}.`);
      return { open() { const f = prefs.get("ld-folder", ""); if (f && !$("#ld-file").options.length) list(f).catch(() => {}); } };
    },
  });

  // ------------------------------------------------------------------ Pansharpen
  LF.tool({ id: "rpansharp", title: "Pansharpen", icon: "rpansharp", kinds: ["pansharpen"],
    subtitle: "Make a multispectral image as sharp as its panchromatic band (e.g. Landsat 8/9 from 30 m to 15 m with band 8): Gram-Schmidt, Brovey or IHS",
    panel: `<div class="card"><h2>Images ${tip("The two must cover the same place: the colour bands (e.g. Landsat B2, B3, B4, B5 stacked) and the sharper pan band (Landsat B8). The result has the pan band's pixels.")}</h2>
      ${sel("ps-ms", "Colour (multispectral) image")}<label>Bands ${tip("Empty: all. Pansharpening works best for the bands the pan band covers (Landsat B8: blue to red).")}<input type="text" id="ps-bands" placeholder="All, or e.g. 1,2,3"></label>
      ${sel("ps-pan", "Panchromatic band")}
      <label>Method <select id="ps-method"><option value="gsa">Gram-Schmidt adaptive (keeps colours best)</option><option value="brovey">Brovey (ratio; strong, shifts colours)</option><option value="ihs">IHS (simple and fast)</option></select></label>
      <label>Name <input type="text" id="ps-name" placeholder="automatic" maxlength="80"></label>${runRow("ps", "Sharpen")}</div>`,
    setup(LF) {
      const { $, fillLayers } = LF;
      const ints = (s) => s.split(/[ ,;]+/).map(Number).filter((n) => Number.isInteger(n) && n > 0);
      job(LF, "ps", "/api/raster/pansharpen", () => {
        const ms = LF.getLayer($("#ps-ms").value), pan = LF.getLayer($("#ps-pan").value);
        if (!ms || !pan) throw new Error("Choose the colour image and the pan band");
        if (ms === pan) throw new Error("The pan band must be a different raster");
        return { image: ms.path, pan: pan.path, bands: ints($("#ps-bands").value).length ? ints($("#ps-bands").value) : null, method: $("#ps-method").value, name: $("#ps-name").value.trim() };
      }, (r) => `<b>${r.bands} bands from ${LF.fmt(r.from_pixel, 4)} to ${LF.fmt(r.to_pixel, 4)}</b> per pixel (${LF.esc(r.method)}).`);
      const fill = () => { fillLayers($("#ps-ms"), rasters(LF), { empty: "No raster layer in Contents" }); fillLayers($("#ps-pan"), rasters(LF), { empty: "No raster layer in Contents" }); };
      return { open() { fill(); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ Spectral unmixing
  LF.tool({ id: "runmix", title: "Spectral unmixing", icon: "runmix", kinds: ["unmix"],
    subtitle: "How much of each pixel is vegetation, soil, water, built-up…: fraction maps from labelled samples of pure materials or found in the image",
    panel: `<div class="card"><h2>Image</h2>${sel("um-img", "Multispectral image")}<label>Bands <input type="text" id="um-bands" placeholder="All, or e.g. 2,3,4,8,11,12"></label></div>
      <div class="card"><h2>Pure materials (endmembers) ${tip("Labelled: polygons or points over pure patches (a dense field, bare soil, deep water), with a field naming the material: the mean spectrum of each. Found in the image: the most extreme pixels (ATGP), named endmember_1, 2 …")}</h2>
      <label>From <select id="um-src"><option value="layer">Labelled polygons or points</option><option value="auto">Found in the image</option></select></label>
      <div id="um-layer-row">${sel("um-layer", "Layer")}${sel("um-field", "Material named in")}</div>
      <label id="um-n-row" class="hidden">How many <input type="number" id="um-n" value="3" min="2" max="10"></label>
      <label class="check"><input type="checkbox" id="um-cons" checked> Fractions between 0 and 1 that add up to 1 ${tip("Fully constrained unmixing (the usual choice). Unticked: plain least squares, which can give negative fractions where a material is missing from the list.")}</label>
      <label>Name <input type="text" id="um-name" placeholder="automatic" maxlength="80"></label>${runRow("um", "Unmix")}</div>`,
    setup(LF) {
      const { $, esc, fillLayers } = LF;
      const ints = (s) => s.split(/[ ,;]+/).map(Number).filter((n) => Number.isInteger(n) && n > 0);
      const labelled = () => LF.layers.filter((l) => l.type === "vector" && l.geojson?.features?.length);
      const syncSrc = () => { const a = $("#um-src").value === "auto"; $("#um-layer-row").classList.toggle("hidden", a); $("#um-n-row").classList.toggle("hidden", !a); };
      const fields = () => { const f = fieldsOf(LF.getLayer($("#um-layer").value)); $("#um-field").innerHTML = f.map((k) => `<option>${esc(k)}</option>`).join("") || `<option value="">(no fields)</option>`; };
      $("#um-src").onchange = syncSrc; $("#um-layer").addEventListener("change", fields);
      job(LF, "um", "/api/raster/unmix", () => {
        const l = LF.getLayer($("#um-img").value);
        if (!l) throw new Error("Choose an image");
        const auto = $("#um-src").value === "auto", lay = LF.getLayer($("#um-layer").value);
        if (!auto && !lay) throw new Error("Choose the layer of pure materials (or find them in the image)");
        if (!auto && !$("#um-field").value) throw new Error("The layer needs a field naming the material");
        return { path: l.path, bands: ints($("#um-bands").value).length ? ints($("#um-bands").value) : null, n_auto: +$("#um-n").value || 3,
                 layer: auto ? null : lay.geojson, field: auto ? null : $("#um-field").value, constrained: $("#um-cons").checked, name: $("#um-name").value.trim() };
      }, (r) => `<b>${r.endmembers.length} fraction bands</b> and an RMSE band (mean ${LF.fmt(r.mean_rmse, 4)}). Mean cover: ${Object.entries(r.mean_fraction).map(([k, v]) => `${esc(k)} ${Math.round(v * 100)}%`).join(", ")}.`);
      const fill = (pick) => { fillLayers($("#um-img"), rasters(LF), { empty: "No raster layer in Contents", pick }); fillLayers($("#um-layer"), labelled(), { empty: "No vector layer in Contents" }); fields(); };
      syncSrc();
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ Routing
  LF.tool({ id: "vroute", title: "Routing (roads)", icon: "vroute", kinds: ["routing"],
    subtitle: "On OpenStreetMap roads or your own: the quickest route through stops, what can be reached in 5, 10, 15 minutes, or each place's nearest hospital / market by travel time",
    panel: `<div class="card"><h2>Network ${tip("OpenStreetMap roads are downloaded for the area around your points (up to 2,500 km²) and kept for 30 days. Car: speeds by road type or the posted limit, one-way streets respected; bicycle 15 km/h, on foot 4.8 km/h.")}</h2>
      <label>Analysis <select id="nr-op"><option value="route">Route through stops</option><option value="service">Service areas (reachable in … minutes)</option><option value="closest">Closest facility</option></select></label>
      <label>Travel <select id="nr-mode"><option value="car">By car</option><option value="bike">By bicycle</option><option value="walk">On foot</option></select></label>
      <label>Roads <select id="nr-roads"></select></label>
      <div id="nr-speed-row" class="hidden"><label>Speed (km/h) from <select id="nr-sfield"></select></label><label>Or everywhere (km/h) <input type="number" id="nr-kmh" value="30" min="1" step="any"></label></div></div>
      <div class="card">${pointsHtml("nr-pts", "Stops / origins / places", "Route: the stops in order. Service areas: where the time starts (several: the nearest counts). Closest facility: the places (e.g. villages) to route from.")}
      <div id="nr-breaks-row" class="hidden"><label>Minutes ${tip("Rings of travel time from the origins, e.g. 5, 10, 15.")}<input type="text" id="nr-breaks" value="5, 10, 15"></label></div>
      <div id="nr-fac-row" class="hidden">${pointsHtml("nr-fac", "Facilities", "E.g. hospitals, markets, schools: each place gets the one it reaches first.")}${sel("nr-fname", "Facility names from")}</div>
      <label>Name <input type="text" id="nr-name" placeholder="automatic" maxlength="80"></label>${runRow("nr", "Solve")}</div>`,
    setup(LF) {
      const { $, esc } = LF;
      const pts = pointsPicker(LF, "nr-pts", { max: 5000, color: "#ea580c" }), fac = pointsPicker(LF, "nr-fac", { max: 5000, color: "#0891b2" });
      const op = () => $("#nr-op").value;
      const sync = () => {
        $("#nr-breaks-row").classList.toggle("hidden", op() !== "service"); $("#nr-fac-row").classList.toggle("hidden", op() !== "closest");
        $("#nr-speed-row").classList.toggle("hidden", $("#nr-roads").value === "osm");
        if (op() !== "closest") fac.hide(); else fac.fill();
      };
      const fillRoads = () => {
        const was = $("#nr-roads").value;
        $("#nr-roads").innerHTML = `<option value="osm">OpenStreetMap (downloaded)</option>` + lineLayers(LF).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
        if ([...$("#nr-roads").options].some((o) => o.value === was)) $("#nr-roads").value = was;
        const f = fieldsOf(LF.getLayer($("#nr-roads").value));
        $("#nr-sfield").innerHTML = `<option value="">(one speed)</option>` + f.map((k) => `<option>${esc(k)}</option>`).join("");
        const fl = fac.layer(), ff = fieldsOf(fl);
        $("#nr-fname").innerHTML = `<option value="">(numbered)</option>` + ff.map((k) => `<option>${esc(k)}</option>`).join("");
        sync();
      };
      $("#nr-op").onchange = sync; $("#nr-roads").onchange = fillRoads;
      $(".gp-src", $("#nr-fac")).addEventListener("change", fillRoads);
      job(LF, "nr", "/api/network/routing", () => {
        const roads = $("#nr-roads").value === "osm" ? null : LF.getLayer($("#nr-roads").value)?.geojson;
        const body = { op: op(), mode: $("#nr-mode").value, points: pts.get(op() === "route" ? "stops" : "points", op() === "route" ? 2 : 1), roads,
                       speed_field: roads ? $("#nr-sfield").value || null : null, speed_kmh: +$("#nr-kmh").value || 30, name: $("#nr-name").value.trim() };
        if (op() === "service") body.breaks = $("#nr-breaks").value.split(/[ ,;]+/).map(Number).filter((n) => n > 0);
        if (op() === "closest") { body.facilities = fac.get("facilities"); body.facility_names = fac.names($("#nr-fname").value); }
        return body;
      }, (r) => op() === "route" ? `<b>${LF.fmt(r.length_km, 2)} km, ${LF.fmt(r.minutes, 1)} minutes</b> on ${esc(r.roads)} roads.`
        : op() === "service" ? `<b>Reachable</b>: ${Object.entries(r.areas_km2).map(([m, a]) => `${m} min → ${LF.fmt(a, 2)} km²`).join(", ")} (${esc(r.roads)}).`
        : `<b>${r.reached} of ${r.incidents} places</b> reach a facility: ${LF.fmt(r.mean_minutes, 1)} min on average, ${LF.fmt(r.max_minutes, 1)} at most${r.unreachable ? `; ${r.unreachable} cannot` : ""}.`);
      return { open() { pts.fill(); fillRoads(); }, layersChanged() { pts.fill(); fillRoads(); } };
    },
  });

  // ------------------------------------------------------------------ Sentinel-5P
  LF.tool({ id: "s5p", menu: "forecast", title: "Sentinel-5P air quality", icon: "s5p", kinds: ["s5p"],
    subtitle: "NO₂, CO, SO₂, formaldehyde, ozone, methane and aerosols from Sentinel-5P (TROPOMI), averaged over your area and dates, with a daily series",
    clip: { "s5-area": { what: "average is made" } },
    panel: `<div class="card"><h2>Sentinel-5P ${tip("Free Level-2 data from Microsoft Planetary Computer (no account). Only the part of each overpass over your area is read: about a minute per overpass. Pixels below ESA's recommended quality are left out.")}</h2>
      <label>Gas <select id="s5-prod"></select></label>
      <div class="row"><label style="flex:1">From <input type="date" id="s5-start"></label><label style="flex:1">To <input type="date" id="s5-end"></label></div>
      ${LF.html.area("s5-area", "Area", "A polygon layer, or none for the map's current view.")}
      <label>Cell size (°) ${tip("TROPOMI pixels are about 5.5 × 3.5 km (0.05°). Smaller cells only leave gaps.")}<input type="number" id="s5-res" value="0.05" min="0.01" max="1" step="0.01"></label>
      <label>Name <input type="text" id="s5-name" placeholder="automatic" maxlength="80"></label>${runRow("s5", "Get the average")}</div>`,
    setup(LF) {
      const { $, api, esc, map, getClip } = LF;
      const iso = (d) => d.toISOString().slice(0, 10);
      const now = new Date();
      $("#s5-end").value = iso(new Date(now - 3 * 864e5)); $("#s5-start").value = iso(new Date(now - 17 * 864e5));
      api("/api/s5p/products").then((r) => { $("#s5-prod").innerHTML = r.products.map((p) => `<option value="${esc(p.id)}">${esc(p.title)}${p.unit ? ` (${esc(p.unit)})` : ""}</option>`).join(""); }).catch(() => {});
      const bbox = () => {
        const g = getClip("s5-area");
        if (!g) { const b = map.getBounds(); return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => +v.toFixed(5)); }
        const cs = JSON.stringify(g.coordinates).match(/-?\d+(\.\d+)?(e-?\d+)?/g).map(Number), xs = cs.filter((_, i) => i % 2 === 0), ys = cs.filter((_, i) => i % 2 === 1);
        return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
      };
      job(LF, "s5", "/api/s5p/average", () => {
        if (!$("#s5-start").value || !$("#s5-end").value) throw new Error("Choose the dates");
        return { product: $("#s5-prod").value, bbox: bbox(), start: $("#s5-start").value, end: $("#s5-end").value, res: +$("#s5-res").value || 0.05, name: $("#s5-name").value.trim() };
      }, (r) => `<b>${esc(r.product)}</b>: mean ${LF.fmt(r.mean, 3)} ${esc(r.unit)} (${LF.fmt(r.min, 3)} to ${LF.fmt(r.max, 3)}), from ${r.overpasses} overpasses on ${r.days} days${r.unreadable ? `; ${r.unreadable} could not be read` : ""}. The daily series is in Contents ▸ Tabular data.`);
      return {};
    },
  });
})();
