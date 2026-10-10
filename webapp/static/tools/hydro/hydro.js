/* Analysis ▸ Hydrology: DEM preparation, Flow direction & accumulation, Watersheds, Drainage network, Terrain & runoff
   indicators, Rainfall–runoff (SCS-CN), Flood from HAND and Flood susceptibility (and "Hydrology in one go", in
   tools/raster/geo-tools.js). Server: webapp/routes/hydro.py · lulc_fetch/hydro/. Uses LF.geo (point picker, results). */
(() => {
  "use strict";
  const { tip } = LF.html;
  const { sel, runRow, rasters, lineLayers, pointsHtml, pointsPicker, job } = LF.geo;
  const demHelp = "Heights in metres. Run DEM preparation first for the cleanest flow; a DEM it made is used as it is (its pits are not filled again). Up to 16 million cells.";
  const ticks = (attr, items) => `<div class="sf-ticks" style="grid-template-columns:1fr 1fr">${items.map(([v, t, h, on]) =>
    `<label class="check"><input type="checkbox" ${attr}="${v}" ${on ? "checked" : ""}> ${t} ${h ? tip(h) : ""}</label>`).join("")}</div>`;
  const picked = (LF, tab, attr) => LF.$$(`#tab-${tab} [${attr}]:checked`).map((c) => c.getAttribute(attr));
  const demOf = (LF, id) => { const l = LF.getLayer(LF.$(`#${id}`).value); if (!l) throw new Error("Choose a DEM (Insert ▸ Library has Copernicus 30 m, or Insert ▸ Add data)"); return l.path; };
  const fillDem = (LF, id, pick) => LF.fillLayers(LF.$(`#${id}`), rasters(LF), { empty: "No raster layer in Contents", pick });
  const km2Row = (id, v = 1) => `<label>A stream drains at least (km²) ${tip("Lower: more, smaller streams. About 1 km² for 30 m DEMs; 0.05–0.2 km² for detailed DEMs of small areas.")}<input type="number" id="${id}" value="${v}" min="0.0001" step="any"></label>`;
  const nameRow = (p) => `<label>Name <input type="text" id="${p}-name" placeholder="automatic" maxlength="80"></label>`;
  const nums = (s) => s.split(/[ ,;]+/).map(Number).filter((n) => Number.isFinite(n) && n > 0);
  const fmt = (v, d = 2) => LF.fmt(v, d);

  // ------------------------------------------------------------------ 1. DEM preparation
  LF.tool({ id: "hydprep", menu: "hydro", title: "DEM preparation", icon: "hydprep", kinds: ["hydrocondition"],
    subtitle: "Make a DEM drain: fill no-data holes, align it to another raster, burn known rivers in, breach roads and embankments across valleys, fill the remaining pits",
    panel: `<div class="card"><h2>DEM ${tip("Steps run in this order: align → fill holes → burn streams → breach → fill. The result is marked as conditioned, so the other Hydrology tools use it as it is.")}</h2>${sel("hp-dem", "DEM")}</div>
      <div class="card"><h2>Steps</h2>${ticks("data-hs", [
        ["fill_nodata", "Fill no-data holes", "Small gaps inside the DEM (voids) filled smoothly from their edges; big gaps and the area outside stay empty.", 1],
        ["breach", "Breach pits", "Cut a channel from each pit down to its outlet (roads, bridges and embankments across a valley are cut through) instead of flooding the valley above them. Lindsay's complete breaching.", 1],
        ["fill", "Fill pits", "Raise every remaining pit to the height where it spills over (priority flood). Alone, or after breaching for what was too deep to cut.", 0]])}
      <label>Fill no-data holes up to (cells) <input type="number" id="hp-holes" value="2000" min="1"></label>
      <label>Breach no deeper than (m) ${tip("Pits that would need a deeper cut are filled instead. Empty: no limit.")}<input type="number" id="hp-maxb" placeholder="no limit" min="0" step="any"></label>
      <details><summary class="hint">Align to another raster or a pixel size</summary>${sel("hp-like", "Same grid as")}<label>Or pixel size (units of the DEM) <input type="number" id="hp-res" placeholder="keep" min="0" step="any"></label></details>
      <details><summary class="hint">Burn in known rivers / canals</summary>${sel("hp-streams", "Line layer")}
        <label>Lower the DEM by (m) <input type="number" id="hp-burn" value="5" min="0" step="any"></label>
        <label>Sloping back up over (m) each side <input type="number" id="hp-buf" value="0" min="0" step="any"></label></details>
      ${nameRow("hp")}${runRow("hp", "Prepare the DEM")}</div>`,
    setup(LF) {
      const { $, esc } = LF;
      job(LF, "hp", "/api/hydro/condition", () => {
        const steps = picked(LF, "hydprep", "data-hs");
        const lines = LF.getLayer($("#hp-streams").value);
        if (!steps.length && !lines && !$("#hp-like").value && !(+$("#hp-res").value > 0)) throw new Error("Tick at least one step");
        return { dem: demOf(LF, "hp-dem"), steps, like: LF.getLayer($("#hp-like").value)?.path || null, res: +$("#hp-res").value > 0 ? +$("#hp-res").value : null,
                 streams: lines?.geojson || null, burn_m: +$("#hp-burn").value || 0, burn_buffer_m: +$("#hp-buf").value || 0,
                 max_breach_m: +$("#hp-maxb").value > 0 ? +$("#hp-maxb").value : null, max_hole_cells: +$("#hp-holes").value || 2000, name: $("#hp-name").value.trim() };
      }, (r) => `<b>Conditioned DEM ready</b>: ${r.lowered_cells.toLocaleString()} cells cut (up to ${fmt(r.max_cut_m)} m), ${r.raised_cells.toLocaleString()} raised (up to ${fmt(r.max_raise_m)} m)${r.holes_filled_cells ? `, ${r.holes_filled_cells.toLocaleString()} hole cells filled` : ""}${r.burned_cells ? `, ${r.burned_cells.toLocaleString()} stream cells burned` : ""}. ${r.cells_still_in_pits ? `⚠ ${r.cells_still_in_pits.toLocaleString()} cells still in pits: tick Fill.` : "Every cell drains."}`);
      const fill = (pick) => {
        fillDem(LF, "hp-dem", pick);
        $("#hp-like").innerHTML = `<option value="">(keep its grid)</option>` + rasters(LF).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
        $("#hp-streams").innerHTML = `<option value="">(none)</option>` + lineLayers(LF).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
      };
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ 2. Flow direction & accumulation
  LF.tool({ id: "hydflow", menu: "hydro", title: "Flow direction & accumulation", icon: "hydflow", kinds: ["hydroflow"],
    subtitle: "Where water flows from each cell (D8, D-infinity or multiple flow direction) and how much area drains through it: cells, km² and specific catchment area",
    panel: `<div class="card"><h2>DEM ${tip(demHelp)}</h2>${sel("hf-dem", "DEM")}
      <label>Method <select id="hf-method"><option value="d8">D8: all to the steepest neighbour (streams, watersheds)</option><option value="dinf">D-infinity: split between two neighbours (Tarboton)</option><option value="mfd">MFD: shared among all lower neighbours (wetness, hillslopes)</option></select></label>
      ${ticks("data-ho", [["direction", "Flow direction", "D8: codes 1 E, 2 SE, 4 S … 128 NE (as ArcGIS). D-infinity: the angle in degrees. MFD has no single direction.", 1],
        ["accumulation", "Flow accumulation (cells)", "", 1], ["area", "Contributing area (km²)", "The area upstream of each cell.", 1],
        ["sca", "Specific catchment area (m²/m)", "Contributing area per metre of contour width: the 'a' of TWI and SPI.", 0]])}
      <label id="hf-p-row" class="hidden">MFD exponent ${tip("1.1 (Freeman): flow spreads; higher values make it converge towards the steepest path.")}<input type="number" id="hf-p" value="1.1" min="0.1" step="any"></label>
      ${nameRow("hf")}${runRow("hf", "Compute")}</div>`,
    setup(LF) {
      const { $ } = LF;
      $("#hf-method").onchange = () => $("#hf-p-row").classList.toggle("hidden", $("#hf-method").value !== "mfd");
      job(LF, "hf", "/api/hydro/flow", () => {
        const outputs = picked(LF, "hydflow", "data-ho");
        if (!outputs.length) throw new Error("Tick at least one result");
        return { dem: demOf(LF, "hf-dem"), method: $("#hf-method").value, outputs, mfd_p: +$("#hf-p").value || 1.1, name: $("#hf-name").value.trim() };
      }, (r) => `<b>${LF.esc(r.method)}</b>: the largest catchment drains ${fmt(r.largest_area_km2)} km² (${r.cells.toLocaleString()} cells${r.sink_cells_filled ? `, ${r.sink_cells_filled.toLocaleString()} in filled pits` : ""}).`);
      return { open(a) { fillDem(LF, "hf-dem", a?.layer); }, layersChanged() { fillDem(LF, "hf-dem"); } };
    },
  });

  // ------------------------------------------------------------------ 3. Watersheds
  LF.tool({ id: "hydwshed", menu: "hydro", title: "Watersheds", icon: "hydwshed", kinds: ["hydrowatershed"],
    subtitle: "Catchments of outlet points (snapped to the river, nested with their parents), sub-watersheds of every stream link, or every basin: boundaries, statistics, longest flow paths",
    panel: `<div class="card"><h2>DEM ${tip(demHelp)}</h2>${sel("hw-dem", "DEM")}
      <label>Delineate <select id="hw-mode"><option value="points">Watersheds of outlet points</option><option value="subbasins">Sub-watersheds (one per stream link)</option><option value="all">Every basin</option></select></label></div>
      <div class="card"><div id="hw-pts-box">${pointsHtml("hw-pts", "Outlets (pour points)", "Click on or near the river. Each point moves to the largest flow within the snap distance. Points upstream of another give nested watersheds, each with its parent.")}
        <label>Snap to the river within (m) <input type="number" id="hw-snap" value="150" min="0" step="any"></label></div>
      <label class="check hidden" id="hw-within-row"><input type="checkbox" id="hw-within"> Only inside the watersheds of points (pick them above)</label>
      <div id="hw-km2-row" class="hidden">${km2Row("hw-km2")}</div>
      <label id="hw-min-row" class="hidden">Basins of at least (km²) <input type="number" id="hw-min" placeholder="10 × the stream size" min="0" step="any"></label>
      ${nameRow("hw")}${runRow("hw", "Delineate")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "hw-pts", { max: 1000, color: "#2563eb" });
      const mode = () => $("#hw-mode").value;
      const sync = () => {
        const m = mode(), pts = m === "points" || (m === "subbasins" && $("#hw-within").checked);
        $("#hw-pts-box").classList.toggle("hidden", !pts); $("#hw-within-row").classList.toggle("hidden", m !== "subbasins");
        $("#hw-km2-row").classList.toggle("hidden", m === "points"); $("#hw-min-row").classList.toggle("hidden", m !== "all");
        if (!pts) pk.hide(); else pk.fill();
      };
      $("#hw-mode").onchange = sync; $("#hw-within").onchange = sync;
      job(LF, "hw", "/api/hydro/watershed", () => {
        const m = mode(), needPts = m === "points" || (m === "subbasins" && $("#hw-within").checked);
        return { dem: demOf(LF, "hw-dem"), mode: m, points: needPts ? pk.get("outlets") : [], snap_m: +$("#hw-snap").value || 0, stream_km2: +$("#hw-km2").value || 1,
                 min_basin_km2: +$("#hw-min").value > 0 ? +$("#hw-min").value : null, within_points: m === "subbasins" && $("#hw-within").checked, name: $("#hw-name").value.trim() };
      }, (r) => `<b>${r.basins} ${r.mode === "subbasins" ? "sub-watersheds" : r.basins === 1 ? "watershed" : "watersheds"}</b>, ${fmt(r.total_area_km2)} km² in all (largest ${fmt(r.largest_km2)} km²)${r.nested ? `; ${r.nested} drain into another (see "parent")` : ""}. Statistics per basin are in the table.`);
      sync();
      return { open(a) { fillDem(LF, "hw-dem", a?.layer); sync(); }, layersChanged() { fillDem(LF, "hw-dem"); pk.fill(); } };
    },
  });

  // ------------------------------------------------------------------ 4. Drainage network
  LF.tool({ id: "hydnet", menu: "hydro", title: "Drainage network", icon: "hydnet", kinds: ["hydronetwork"],
    subtitle: "Streams by a contributing-area threshold, as links between sources and junctions: Strahler and Shreve order, length, slope, topology, drainage density and Horton's ratios",
    panel: `<div class="card"><h2>DEM ${tip(demHelp)}</h2>${sel("hn-dem", "DEM")}${km2Row("hn-km2")}
      ${ticks("data-hn", [["lines", "Stream links (lines)", "Each link with its Strahler order, Shreve magnitude, length, drop, slope, area and the link downstream.", 1],
        ["nodes", "Sources, junctions and outlets (points)", "", 1], ["order", "Strahler order (raster)", "", 1], ["magnitude", "Shreve magnitude (raster)", "The number of sources upstream.", 0],
        ["links", "Link ids (raster)", "Each stream cell's link number: join the line table to it.", 0]])}
      ${nameRow("hn")}${runRow("hn", "Extract")}</div>`,
    setup(LF) {
      const { $ } = LF;
      job(LF, "hn", "/api/hydro/network", () => {
        const outputs = picked(LF, "hydnet", "data-hn");
        if (!outputs.length) throw new Error("Tick at least one result");
        return { dem: demOf(LF, "hn-dem"), stream_km2: +$("#hn-km2").value || 1, outputs, name: $("#hn-name").value.trim() };
      }, (r) => `<b>${r.links.toLocaleString()} links, ${fmt(r.stream_length_km)} km of streams</b>: ${r.sources} sources, ${r.junctions} junctions, ${r.outlets} outlet${r.outlets === 1 ? "" : "s"}; Strahler order up to ${r.max_strahler}, Shreve ${r.max_shreve}. Drainage density <b>${fmt(r.drainage_density_km_per_km2, 3)} km/km²</b> over ${fmt(r.area_km2)} km².`);
      return { open(a) { fillDem(LF, "hn-dem", a?.layer); }, layersChanged() { fillDem(LF, "hn-dem"); } };
    },
  });

  // ------------------------------------------------------------------ 5. Terrain & runoff indicators
  LF.tool({ id: "hydterrain", menu: "hydro", title: "Terrain & runoff indicators", icon: "hydterrain", kinds: ["hydroterrain"],
    subtitle: "Slope, aspect, curvature, wetness index (TWI), stream power index (SPI), height above the nearest drainage (HAND), depressions, flow length and flow paths",
    panel: `<div class="card"><h2>DEM ${tip(demHelp)}</h2>${sel("ht-dem", "DEM")}
      ${ticks("data-ht", [["slope", "Slope", "", 1], ["aspect", "Aspect", "", 0],
        ["curvature", "Curvature", "Profile (along the slope), plan (across it) and total, in 1/100 m as ArcGIS.", 1],
        ["twi", "Wetness index (TWI)", "ln(a / tan β): high where water gathers.", 1], ["spi", "Stream power index (SPI)", "ln(a · tan β): the erosive power of flowing water.", 1],
        ["hand", "HAND", "Height above the nearest drainage, following the flow, and the distance to it. Low HAND floods first.", 1],
        ["depressions", "Depressions", "Their depth, and each as a polygon with area, deepest point and volume (on the DEM as it is: run it on the original, not a filled DEM).", 0],
        ["flowlength", "Flow length", "Down to where water leaves the DEM, and up to the divide.", 0],
        ["flowpaths", "Flow paths from points", "The path water takes downhill from each point.", 0]])}
      <label>Catchment area for TWI / SPI by <select id="ht-flow"><option value="mfd">MFD (usual)</option><option value="dinf">D-infinity</option><option value="d8">D8</option></select></label>
      <div id="ht-km2-row">${km2Row("ht-km2")}</div>
      <div id="ht-pts-box" class="hidden">${pointsHtml("ht-pts", "Start points", "")}</div>
      ${nameRow("ht")}${runRow("ht", "Compute")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "ht-pts", { max: 500, color: "#0891b2" });
      const sync = () => { const p = picked(LF, "hydterrain", "data-ht"); $("#ht-pts-box").classList.toggle("hidden", !p.includes("flowpaths")); $("#ht-km2-row").classList.toggle("hidden", !p.includes("hand"));
        if (p.includes("flowpaths")) pk.fill(); else pk.hide(); };
      LF.$$("#tab-hydterrain [data-ht]").forEach((c) => c.addEventListener("change", sync));
      job(LF, "ht", "/api/hydro/terrain", () => {
        const products = picked(LF, "hydterrain", "data-ht");
        if (!products.length) throw new Error("Tick at least one indicator");
        return { dem: demOf(LF, "ht-dem"), products, flow: $("#ht-flow").value, stream_km2: +$("#ht-km2").value || 1,
                 points: products.includes("flowpaths") ? pk.get("start points") : [], name: $("#ht-name").value.trim() };
      }, (r) => [r.twi_mean != null ? `mean TWI ${fmt(r.twi_mean)}` : "", r.hand_median_m != null ? `median HAND ${fmt(r.hand_median_m, 1)} m` : "",
                 r.depressions != null ? `${r.depressions} depressions holding ${fmt(r.depression_volume_m3 / 1000, 1)} thousand m³` : "", r.flow_paths ? `${r.flow_paths} flow paths` : ""].filter(Boolean).join(" · ") || "<b>Done.</b>");
      sync();
      return { open(a) { fillDem(LF, "ht-dem", a?.layer); sync(); }, layersChanged() { fillDem(LF, "ht-dem"); } };
    },
  });

  // ------------------------------------------------------------------ 6. Rainfall–runoff
  LF.tool({ id: "hydrunoff", menu: "hydro", title: "Rainfall–runoff (SCS-CN)", icon: "hydrunoff", kinds: ["hydrorunoff"],
    subtitle: "How much of a storm runs off: SCS curve numbers from land cover and soil, runoff depth, routed volumes, and per watershed the runoff, volume, time of concentration and peak flow",
    panel: `<div class="card"><h2>Land and soil ${tip("Curve numbers come from the land-cover classes (by their names, e.g. WorldCover / Dynamic World / ESRI from Find imagery or the Library) and the hydrologic soil group: A sand … D clay. Or give a CN raster.")}</h2>
      ${sel("hr-dem", "DEM")}<label>Curve numbers from <select id="hr-src"><option value="lc">A land-cover map</option><option value="cn">A curve-number raster</option></select></label>
      <div id="hr-lc-row">${sel("hr-lc", "Land cover")}<label>Its classes <select id="hr-scheme"><option value="auto">Automatic (class names, else WorldCover codes)</option><option value="worldcover">ESA WorldCover</option><option value="dynamicworld">Dynamic World</option><option value="esri">ESRI land cover</option></select></label>
        <label>Soil group <select id="hr-soil"><option value="A">A: sand, gravel (little runoff)</option><option value="B" selected>B: loam</option><option value="C">C: sandy clay</option><option value="D">D: clay (most runoff)</option><option value="raster">From a raster (1 = A … 4 = D)</option></select></label>
        <div id="hr-soilr-row" class="hidden">${sel("hr-soilr", "Soil group raster")}</div></div>
      <div id="hr-cn-row" class="hidden">${sel("hr-cn", "Curve-number raster")}</div></div>
      <div class="card"><h2>Storm</h2><label>Rainfall (mm) <input type="number" id="hr-rain" value="100" min="0.1" step="any"></label>
      <label>Ground before the storm <select id="hr-amc"><option value="I">Dry (AMC I)</option><option value="II" selected>Normal (AMC II)</option><option value="III">Wet (AMC III)</option></select></label>
      <label>Storm duration (hours) ${tip("For the peak flow of the SCS triangular hydrograph.")}<input type="number" id="hr-dur" value="24" min="0.1" step="any"></label>
      <label>Initial abstraction λ ${tip("Ia = λ S: 0.2 in the original method; 0.05 fits many watersheds better (Hawkins et al. 2002).")}<input type="number" id="hr-lam" value="0.2" min="0.01" max="0.3" step="0.01"></label>
      ${pointsHtml("hr-pts", "Outlets for watershed totals (optional)", "Each outlet's watershed gets its area, mean CN, runoff depth and volume, time of concentration and peak flow.")}
      ${nameRow("hr")}${runRow("hr", "Compute runoff")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "hr-pts", { max: 500, color: "#7c3aed" });
      const sync = () => { const cn = $("#hr-src").value === "cn"; $("#hr-lc-row").classList.toggle("hidden", cn); $("#hr-cn-row").classList.toggle("hidden", !cn);
        $("#hr-soilr-row").classList.toggle("hidden", $("#hr-soil").value !== "raster"); };
      $("#hr-src").onchange = sync; $("#hr-soil").onchange = sync;
      job(LF, "hr", "/api/hydro/runoff", () => {
        const cn = $("#hr-src").value === "cn", path = (id) => LF.getLayer($(`#${id}`).value)?.path || null;
        if (cn && !path("hr-cn")) throw new Error("Choose the curve-number raster");
        if (!cn && !path("hr-lc")) throw new Error("Choose the land-cover map");
        let points = [];
        try { points = pk.get("outlets", 0); } catch { points = []; }
        return { dem: demOf(LF, "hr-dem"), rain_mm: +$("#hr-rain").value, landcover: cn ? null : path("hr-lc"), cn_raster: cn ? path("hr-cn") : null,
                 soil: $("#hr-soil").value === "raster" ? "B" : $("#hr-soil").value, soil_raster: !cn && $("#hr-soil").value === "raster" ? path("hr-soilr") : null,
                 scheme: $("#hr-scheme").value, condition: $("#hr-amc").value, lam: +$("#hr-lam").value || 0.2, duration_h: +$("#hr-dur").value || 24, points, name: $("#hr-name").value.trim() || "runoff" };
      }, (r) => `<b>${fmt(r.mean_runoff_mm, 1)} mm of ${fmt(r.mean_rain_mm, 0)} mm runs off</b> on average (mean CN ${r.mean_cn}, from ${LF.esc(r.cn_from || "")}): ${fmt(r.runoff_volume_m3 / 1e6, 3)} million m³.${(r.watersheds || []).map((w) => `<br>Watershed ${w.basin}: ${fmt(w.area_km2)} km², ${fmt(w.runoff_mm, 1)} mm, peak <b>${fmt(w.peak_m3s, 1)} m³/s</b> after ${fmt(w.time_to_peak_h, 1)} h.`).join("")}`);
      const fill = (pick) => { fillDem(LF, "hr-dem", pick); ["hr-lc", "hr-cn", "hr-soilr"].forEach((id) => LF.fillLayers($(`#${id}`), rasters(LF), { empty: "No raster layer in Contents" })); pk.fill(); };
      sync();
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ 7. Flood from HAND
  LF.tool({ id: "hydflood", menu: "hydro", title: "Flood from HAND", icon: "hydflood", kinds: ["hydrohandflood"],
    subtitle: "What floods when rivers rise 1, 2, 5 … m: depth and extent at each water level from the height above the nearest drainage, with flooded areas and volumes",
    panel: `<div class="card"><h2>DEM ${tip(demHelp + " A quick first map of where rising rivers spread (Nobre et al. 2016), not a hydraulic model: it ignores flow speed and how long the water rises.")}</h2>${sel("hd-dem", "DEM")}
      ${km2Row("hd-km2", 5)}<label>Water levels above the streams (m) <input type="text" id="hd-levels" value="1, 2, 5"></label>
      <label>Only within (m) of a stream ${tip("Leave empty for no limit. Limits floods in flat areas far from any river.")}<input type="number" id="hd-dist" placeholder="no limit" min="0" step="any"></label>
      ${nameRow("hd")}${runRow("hd", "Map the floods")}</div>`,
    setup(LF) {
      const { $ } = LF;
      job(LF, "hd", "/api/hydro/hand-flood", () => {
        const levels = nums($("#hd-levels").value);
        if (!levels.length) throw new Error("Give at least one water level, e.g. 1, 2, 5");
        return { dem: demOf(LF, "hd-dem"), levels_m: levels, stream_km2: +$("#hd-km2").value || 1, max_dist_m: +$("#hd-dist").value > 0 ? +$("#hd-dist").value : null, name: $("#hd-name").value.trim() };
      }, (r) => r.levels.map((l) => `<b>${l.level_m} m</b>: ${fmt(l.flooded_km2, 3)} km² flooded, ${fmt(l.mean_depth_m)} m deep on average`).join("<br>"));
      return { open(a) { fillDem(LF, "hd-dem", a?.layer); }, layersChanged() { fillDem(LF, "hd-dem"); } };
    },
  });

  // ------------------------------------------------------------------ 8. Flood susceptibility
  LF.tool({ id: "hydsusc", menu: "hydro", title: "Flood susceptibility", icon: "hydsusc", kinds: ["hydrosusceptibility"],
    subtitle: "Where floods are likely, learned from where they happened and predictors (HAND, TWI, slope, curvature, distance to streams, land cover…), checked with spatial cross-validation",
    panel: `<div class="card"><h2>Predictors ${tip("Rasters of anything that explains flooding: make HAND, TWI, SPI, slope and curvature with Terrain & runoff indicators, distance to streams with Vector to raster (distance). Every band counts; they are put on the first one's grid.")}</h2>
      <div id="hs-preds" class="sf-ticks" style="grid-template-columns:1fr"></div></div>
      <div class="card"><h2>Flood inventory ${tip("Where floods happened: points or polygons (e.g. the SAR flood map turned into polygons, field reports). Non-flood places: your own layer, or random cells away from every flood.")}</h2>
      ${sel("hs-flood", "Floods (points / polygons)")}${sel("hs-dry", "Places that did not flood")}
      <label>Random non-flood cells at least (m) from a flood <input type="number" id="hs-buf" value="200" min="0" step="any"></label>
      <label>Model <select id="hs-model"><option value="rf">Random Forest</option><option value="lgbm">LightGBM</option></select></label>
      <label>Spatial blocks of (m) ${tip("Cross-validation leaves out whole blocks, so the score is for new places (not neighbours of the training cells). Use at least the size of a flood patch.")}<input type="number" id="hs-block" value="2000" min="1" step="any"></label>
      ${nameRow("hs")}${runRow("hs", "Model susceptibility")}</div>`,
    setup(LF) {
      const { $, $$, esc } = LF;
      const vecs = () => LF.layers.filter((l) => l.type === "vector" && l.geojson?.features?.length);
      const fill = () => {
        const was = new Set($$("#hs-preds input:checked").map((c) => c.value));
        $("#hs-preds").innerHTML = rasters(LF).map((l) => `<label class="check"><input type="checkbox" value="${esc(l.id)}" ${was.has(l.id) ? "checked" : ""}> ${esc(l.name)}</label>`).join("") || `<p class="hint">No raster layer in Contents.</p>`;
        LF.fillLayers($("#hs-flood"), vecs(), { empty: "No vector layer in Contents" });
        $("#hs-dry").innerHTML = `<option value="">(random, away from the floods)</option>` + vecs().map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
      };
      job(LF, "hs", "/api/hydro/susceptibility", () => {
        const preds = $$("#hs-preds input:checked").map((c) => LF.getLayer(c.value)?.path).filter(Boolean);
        if (!preds.length) throw new Error("Tick the predictor layers");
        const fl = LF.getLayer($("#hs-flood").value);
        if (!fl) throw new Error("Choose the flood layer");
        const dry = LF.getLayer($("#hs-dry").value);
        if (dry === fl) throw new Error("The non-flood layer must be another layer");
        return { predictors: preds, floods: fl.geojson, non_floods: dry?.geojson || null, buffer_m: +$("#hs-buf").value || 0, model: $("#hs-model").value,
                 block_m: +$("#hs-block").value || 2000, name: $("#hs-name").value.trim() || "flood_susceptibility" };
      }, (r) => `<b>${LF.esc(r.model)}</b> on ${r.samples.flood} flood and ${r.samples.non_flood} non-flood cells. ROC AUC <b>${r.auc_spatial ?? "–"}</b> on new places (spatial blocks) vs ${r.auc_random ?? "–"} with a random split${r.auc_spatial != null && r.auc_random != null && r.auc_random - r.auc_spatial > 0.05 ? " (the random split flatters it)" : ""}. High or very high: ${fmt(r.high_km2, 2)} km². Most important: ${Object.entries(r.importance).slice(0, 4).map(([k, v]) => `${LF.esc(k)} ${Math.round(v * 100)}%`).join(", ")}.`);
      return { open() { fill(); }, layersChanged() { fill(); } };
    },
  });
})();
