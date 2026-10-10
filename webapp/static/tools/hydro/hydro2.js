/* Analysis ▸ Hydrology (second part): Rainfall data, Soil erosion (RUSLE), Flood depth, Flood impact, Check dams & ponds,
   Morphometry & prioritisation, Groundwater potential, Streamflow modelling, Design flood hydrograph, Flood simulation 2D;
   Tools ▸ Fuzzy & suitability ▸ AHP weights & overlay; SAR ▸ Flood map: ML refinement.
   Server: webapp/routes/hydro_extra.py · lulc_fetch/hydro/, lulc_fetch/ahp.py, lulc_fetch/sar/floodml.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const { sel, runRow, rasters, pointLayers, lineLayers, fieldsOf, pointsHtml, pointsPicker, job } = LF.geo;
  const nameRow = (p, v = "") => `<label>Name <input type="text" id="${p}-name" placeholder="${v || "automatic"}" maxlength="80"></label>`;
  const fmt = (v, d = 2) => (v == null ? "–" : LF.fmt(v, d));
  const esc = (s) => LF.esc(s);
  const demOf = (LF, id) => { const l = LF.getLayer(LF.$(`#${id}`).value); if (!l) throw new Error("Choose a DEM"); return l.path; };
  const pathOf = (LF, id) => LF.getLayer(LF.$(`#${id}`)?.value)?.path || null;
  const geoOf = (LF, id) => LF.getLayer(LF.$(`#${id}`)?.value)?.geojson || null;
  const opts = (list, none) => (none ? `<option value="">${esc(none)}</option>` : "") + list.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
  const keep = (el, html) => { const was = el.value; el.innerHTML = html; if ([...el.options].some((o) => o.value === was)) el.value = was; };
  const vectors = (LF) => LF.layers.filter((l) => l.type === "vector" && l.geojson?.features?.length);
  const polyLayers = (LF) => LF.layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)));
  // "1: 5, 2: 3" or "1=5, 2=3" → {"1": 5, "2": 3}
  const pairs = (s) => Object.fromEntries(s.split(/[,;\n]+/).map((x) => x.split(/[:=]/).map((y) => y.trim())).filter((x) => x.length === 2 && x[0] !== "" && Number.isFinite(+x[1])).map(([k, v]) => [k, +v]));
  // "12" or "0:0, 1:50, 3:0" → number or [[h, v], …]
  const series = (s) => { s = s.trim(); if (!s) return null; if (!/[:=]/.test(s)) return +s; return s.split(/[,;\n]+/).map((x) => x.split(/[:=]/).map(Number)).filter((x) => x.length === 2 && x.every(Number.isFinite)); };
  const bboxOf = (LF, areaId) => {
    const g = LF.getClip(areaId);
    if (!g) { const b = LF.map.getBounds(); return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => +v.toFixed(5)); }
    const cs = JSON.stringify(g.coordinates).match(/-?\d+(\.\d+)?(e-?\d+)?/g).map(Number), xs = cs.filter((_, i) => i % 2 === 0), ys = cs.filter((_, i) => i % 2 === 1);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  };

  // ------------------------------------------------------------------ Rainfall data
  LF.tool({ id: "hydrain", menu: "hydro", title: "Rainfall data", icon: "hydrain", kinds: ["hydrorain"],
    subtitle: "Free rainfall: CHIRPS totals for your dates or the mean annual rainfall on a grid, and design storms (2- to 100-year rain) with a daily series at a place",
    clip: { "hy-rain-area": { what: "rainfall is read" } },
    panel: `<div class="card"><h2>Rainfall ${tip("CHIRPS 2.0 (0.05°, about 5.5 km, 1981–now, between 50° S and 50° N; recent weeks come late). Design storms from the ERA5 daily archive (1940–now) through Open-Meteo. Free, no account.")}</h2>
      <label>What <select id="rn-op"><option value="total">Total rainfall for dates (CHIRPS grid)</option><option value="annual">Mean annual rainfall over years (CHIRPS grid)</option><option value="storms">Design storms and a daily series at a place</option></select></label>
      <div id="rn-area-box">${LF.html.area("hy-rain-area", "Area", "A polygon layer, or none for the map's current view.")}</div>
      <div id="rn-dates" class="row"><label style="flex:1">From <input type="date" id="rn-start"></label><label style="flex:1">To <input type="date" id="rn-end"></label></div>
      <div id="rn-years" class="row hidden"><label style="flex:1">First year <input type="number" id="rn-y0" value="1991" min="1940"></label><label style="flex:1">Last year <input type="number" id="rn-y1" value="2020" min="1940"></label></div>
      <div id="rn-pt-box" class="hidden">${pointsHtml("rn-pt", "Place", "Click the watershed's centre.")}</div>
      ${nameRow("rn")}${runRow("rn", "Get rainfall")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "rn-pt", { max: 1, color: "#2563eb" });
      const iso = (d) => d.toISOString().slice(0, 10), now = new Date();
      $("#rn-end").value = iso(new Date(now - 45 * 864e5)); $("#rn-start").value = iso(new Date(now - 75 * 864e5));
      $("#rn-y1").value = now.getFullYear() - 1; $("#rn-y0").value = now.getFullYear() - 30;
      const sync = () => { const op = $("#rn-op").value; $("#rn-area-box").classList.toggle("hidden", op === "storms"); $("#rn-dates").classList.toggle("hidden", op !== "total");
        $("#rn-years").classList.toggle("hidden", op === "total"); $("#rn-pt-box").classList.toggle("hidden", op !== "storms"); if (op === "storms") pk.fill(); else pk.hide(); };
      $("#rn-op").onchange = sync;
      job(LF, "rn", "/api/hydro/rainfall", () => {
        const op = $("#rn-op").value;
        return { op, bbox: op === "storms" ? null : bboxOf(LF, "hy-rain-area"), point: op === "storms" ? pk.get("place")[0] : null,
                 start: $("#rn-start").value || null, end: $("#rn-end").value || null, first_year: +$("#rn-y0").value, last_year: +$("#rn-y1").value, name: $("#rn-name").value.trim() };
      }, (r) => r.levels ? `<b>${r.years} years</b>, mean ${fmt(r.mean_annual_mm, 0)} mm/yr. 1-day rain: ${Object.entries(r.levels["1_day"]).map(([T, v]) => `${T}-yr <b>${fmt(v, 0)} mm</b>`).join(", ")}. The 2-, 3- and 5-day values are in the table; feed them to the Design flood hydrograph.`
        : `<b>${fmt(r.mean_mm, 0)} mm</b> on average (${fmt(r.min_mm, 0)}–${fmt(r.max_mm, 0)} mm) from ${r.files} CHIRPS files.`);
      sync();
      return { open() { sync(); }, layersChanged() { if ($("#rn-op").value === "storms") pk.fill(); } };
    },
  });

  // ------------------------------------------------------------------ Soil erosion (RUSLE)
  LF.tool({ id: "hyderosion", menu: "hydro", title: "Soil erosion (RUSLE)", icon: "hyderosion", kinds: ["hydroerosion"],
    subtitle: "Average yearly soil loss in t/ha from rainfall, soil, slope, cover and practice (A = R·K·LS·C·P), FAO classes, and sediment yield per watershed",
    panel: `<div class="card"><h2>DEM ${tip("Slope length and steepness (LS) come from it. Heights in metres.")}</h2>${sel("er-dem", "DEM")}</div>
      <div class="card"><h2>R: rainfall ${tip("From the mean annual rainfall (Renard & Freimund 1994). Rainfall data ▸ Mean annual rainfall makes the raster.")}</h2>
        ${sel("er-rain", "Mean annual rainfall raster")}<label>Or one value (mm / year) <input type="number" id="er-rainv" placeholder="e.g. 1100" min="0" step="any"></label></div>
      <div class="card"><h2>K: soil ${tip("Erodibility of the soil: from its texture, one value (t·h/MJ/mm, often 0.01–0.06) or a raster.")}</h2>
        <label>Texture <select id="er-tex"><option value="">(give a value or raster)</option>${["sand", "loamy sand", "sandy loam", "loam", "silt loam", "silt", "sandy clay loam", "clay loam", "silty clay loam", "sandy clay", "silty clay", "clay"].map((t) => `<option ${t === "loam" ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <label>Or K <input type="number" id="er-k" placeholder="e.g. 0.03" min="0" step="any"></label>${sel("er-kr", "Or a K raster")}</div>
      <div class="card"><h2>C: cover · P: practice</h2>
        <label>C from <select id="er-csrc"><option value="lc">Land-cover map</option><option value="ndvi">NDVI</option><option value="raster">A C raster</option></select></label>${sel("er-c", "Layer")}
        <label>P (1 none; 0.5–0.6 contour farming; 0.1–0.3 terraces) <input type="number" id="er-p" value="1" min="0.01" max="1" step="0.05"></label>
        ${pointsHtml("er-pts", "Outlets for sediment yield (optional)", "")}${nameRow("er", "rusle")}${runRow("er", "Compute soil loss")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "er-pts", { max: 200, color: "#a16207" });
      job(LF, "er", "/api/hydro/erosion", () => {
        const src = $("#er-csrc").value, c = pathOf(LF, "er-c");
        if (!c) throw new Error("Choose the layer for C (land cover, NDVI or a C raster)");
        if (!pathOf(LF, "er-rain") && !(+$("#er-rainv").value > 0)) throw new Error("Give the mean annual rainfall (a raster or one value)");
        let points = []; try { points = pk.get("outlets", 0); } catch { points = []; }
        return { dem: demOf(LF, "er-dem"), rain_raster: pathOf(LF, "er-rain"), rain_mm: +$("#er-rainv").value || null, texture: $("#er-tex").value || null,
                 k: +$("#er-k").value || null, k_raster: pathOf(LF, "er-kr"), landcover: src === "lc" ? c : null, ndvi: src === "ndvi" ? c : null, c_raster: src === "raster" ? c : null,
                 p: +$("#er-p").value || 1, points, name: $("#er-name").value.trim() || "rusle" };
      }, (r) => `<b>${fmt(r.mean_t_ha_yr, 1)} t/ha/yr</b> on average, ${fmt(r.total_t_yr / 1000, 1)} thousand t a year. ${Object.entries(r.class_pct).filter(([, v]) => v > 0).map(([k, v]) => `${esc(k)} ${v}%`).join(" · ")}${(r.watersheds || []).map((w) => `<br>Watershed ${w.basin}: ${fmt(w.mean_t_ha_yr, 1)} t/ha/yr, sediment yield ${fmt(w.sediment_yield_t_yr, 0)} t/yr (SDR ${w.sdr})`).join("")}`);
      const fill = (pick) => {
        LF.fillLayers($("#er-dem"), rasters(LF), { empty: "No raster layer in Contents", pick });
        ["er-rain", "er-kr"].forEach((id) => keep($(`#${id}`), opts(rasters(LF), "(none)")));
        LF.fillLayers($("#er-c"), rasters(LF), { empty: "No raster layer in Contents" }); pk.fill();
      };
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ Flood depth
  LF.tool({ id: "hyddepth", menu: "hydro", title: "Flood depth (FwDET)", icon: "hyddepth", kinds: ["hydrodepth"],
    subtitle: "Water depth inside a flood extent (the SAR flood map, a water mask or polygons) from a DEM: the water surface at the flood's edge carried inwards (FwDET 2.0)",
    panel: `<div class="card"><h2>Flood and ground ${tip("The flood map's classes 'Flood' and 'Water' are used; 'Permanent water' only if ticked. The finer the DEM, the better the depths (a 30 m DEM gives a first estimate).")}</h2>
      ${sel("fd-dem", "DEM")}<label>Flood extent from <select id="fd-src"><option value="raster">A flood map / mask (raster)</option><option value="poly">Polygons</option></select></label>
      ${sel("fd-flood", "Flood layer")}
      <label>Smooth the water surface over (pixels) <input type="number" id="fd-sm" value="3" min="1" max="31" step="2"></label>
      <label class="check"><input type="checkbox" id="fd-perm"> Include permanent water</label>${nameRow("fd", "flood")}${runRow("fd", "Estimate depth")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const fillFlood = () => { const r = $("#fd-src").value === "raster"; LF.fillLayers($("#fd-flood"), r ? rasters(LF) : polyLayers(LF), { empty: r ? "No raster layer" : "No polygon layer" }); };
      $("#fd-src").onchange = fillFlood;
      job(LF, "fd", "/api/hydro/flood-depth", () => {
        const r = $("#fd-src").value === "raster", l = LF.getLayer($("#fd-flood").value);
        if (!l) throw new Error("Choose the flood layer");
        return { dem: demOf(LF, "fd-dem"), flood: r ? l.path : null, polygons: r ? null : l.geojson, smooth_px: +$("#fd-sm").value || 3, include_permanent: $("#fd-perm").checked, name: $("#fd-name").value.trim() || "flood" };
      }, (r) => `<b>${fmt(r.flooded_km2, 3)} km²</b> flooded, <b>${fmt(r.mean_depth_m)} m</b> deep on average (up to ${fmt(r.max_depth_m)} m), ${fmt(r.volume_m3 / 1e6, 3)} million m³. ${r.by_depth.map((d) => `${esc(d.depth)}: ${fmt(d.km2, 3)} km²`).join(" · ")}`);
      const fill = (pick) => { LF.fillLayers($("#fd-dem"), rasters(LF), { empty: "No raster layer in Contents", pick }); fillFlood(); };
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ Flood impact
  LF.tool({ id: "hydimpact", menu: "hydro", title: "Flood impact", icon: "hydimpact", kinds: ["hydroimpact"],
    subtitle: "What a flood covers: hectares of cropland, built-up and other land cover, people (a population raster), buildings and km of roads, by depth when known",
    panel: `<div class="card"><h2>The flood ${tip("A flood map or water mask (raster), flood polygons, or a depth raster (from Flood depth, Flood from HAND or Flood simulation): with depth, everything is split into < 0.5, 0.5–1, 1–2 and > 2 m.")}</h2>
      <label>From <select id="fi-src"><option value="raster">A flood map / mask (raster)</option><option value="depth">A depth raster</option><option value="poly">Polygons</option></select></label>${sel("fi-flood", "Flood layer")}</div>
      <div class="card"><h2>What can be flooded ${tip("Population: counts per cell, e.g. WorldPop or GHS-POP (download from their sites, then Add data). Buildings: points or footprints; roads: lines (e.g. from the Library).")}</h2>
      ${sel("fi-lc", "Land cover")}${sel("fi-pop", "Population")}${sel("fi-bld", "Buildings")}${sel("fi-roads", "Roads")}
      ${nameRow("fi", "flood_impact")}${runRow("fi", "Count the impact")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const fillFlood = () => { const p = $("#fi-src").value === "poly"; LF.fillLayers($("#fi-flood"), p ? polyLayers(LF) : rasters(LF), { empty: p ? "No polygon layer" : "No raster layer" }); };
      $("#fi-src").onchange = fillFlood;
      job(LF, "fi", "/api/hydro/flood-impact", () => {
        const src = $("#fi-src").value, l = LF.getLayer($("#fi-flood").value);
        if (!l) throw new Error("Choose the flood layer");
        return { flood: src === "raster" ? l.path : null, depth: src === "depth" ? l.path : null, polygons: src === "poly" ? l.geojson : null,
                 landcover: pathOf(LF, "fi-lc"), population: pathOf(LF, "fi-pop"), buildings: geoOf(LF, "fi-bld"), roads: geoOf(LF, "fi-roads"), name: $("#fi-name").value.trim() || "flood_impact" };
      }, (r) => `<b>${fmt(r.flooded_km2, 3)} km²</b> flooded.${r.land_cover_ha ? ` ${Object.entries(r.land_cover_ha).slice(0, 5).map(([k, v]) => `${esc(k)} ${fmt(v, 1)} ha`).join(" · ")}.` : ""}${r.people != null ? ` <b>${r.people.toLocaleString()} people</b>.` : ""}${r.buildings != null ? ` <b>${r.buildings.toLocaleString()} of ${r.buildings_total.toLocaleString()} buildings</b>.` : ""}${r.roads_km != null ? ` <b>${fmt(r.roads_km, 2)} km</b> of road.` : ""}`);
      const fill = () => {
        fillFlood();
        ["fi-lc", "fi-pop"].forEach((id) => keep($(`#${id}`), opts(rasters(LF), "(none)")));
        keep($("#fi-bld"), opts(vectors(LF), "(none)")); keep($("#fi-roads"), opts(lineLayers(LF), "(none)"));
      };
      return { open() { fill(); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ Check dams & ponds
  LF.tool({ id: "hydstorage", menu: "hydro", title: "Check dams & ponds", icon: "hydstorage", kinds: ["hydrostorage"],
    subtitle: "Sites along the streams where a small dam holds the most water for its length, with the pond each makes; or the area–capacity curve of a site",
    panel: `<div class="card"><h2>DEM ${tip("A DEM of 10–30 m or finer: the ponds of small dams are only a few cells on coarse DEMs.")}</h2>${sel("ds-dem", "DEM")}
      <label>Find <select id="ds-op"><option value="sites">The best dam sites</option><option value="curve">The area–capacity curve of a site</option></select></label>
      <label><span id="ds-hlabel">Dam height (m)</span> <input type="number" id="ds-h" value="3" min="0.5" step="any"></label>
      <div id="ds-sites"><label>Streams draining at least (km²) <input type="number" id="ds-km2" value="0.5" min="0.001" step="any"></label>
        <div class="row"><label style="flex:1">Stream order from <input type="number" id="ds-o0" value="1" min="1"></label><label style="flex:1">to <input type="number" id="ds-o1" value="3" min="1"></label></div>
        <label>Bed slope at most (%) <input type="number" id="ds-slope" value="5" min="0.1" step="any"></label>
        <label>Sites at least (m) apart <input type="number" id="ds-sp" value="300" min="0" step="any"></label><label>How many sites <input type="number" id="ds-top" value="30" min="1" max="500"></label></div>
      <div id="ds-curve" class="hidden">${pointsHtml("ds-pt", "Dam site", "Click on the stream where the dam would be.")}<label>Every (m) <input type="number" id="ds-step" value="0.5" min="0.05" step="any"></label></div>
      ${nameRow("ds")}${runRow("ds", "Find")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "ds-pt", { max: 1, color: "#0e7490" });
      const sync = () => { const c = $("#ds-op").value === "curve"; $("#ds-sites").classList.toggle("hidden", c); $("#ds-curve").classList.toggle("hidden", !c);
        $("#ds-hlabel").textContent = c ? "Up to a water depth of (m)" : "Dam height (m)"; if (c) pk.fill(); else pk.hide(); };
      $("#ds-op").onchange = sync;
      job(LF, "ds", "/api/hydro/storage", () => {
        const op = $("#ds-op").value;
        return { dem: demOf(LF, "ds-dem"), op, height_m: +$("#ds-h").value || 3, stream_km2: +$("#ds-km2").value || 0.5, min_order: +$("#ds-o0").value || 1, max_order: +$("#ds-o1").value || 3,
                 max_slope_pct: +$("#ds-slope").value || 5, spacing_m: +$("#ds-sp").value || 0, top: +$("#ds-top").value || 30, point: op === "curve" ? pk.get("dam site")[0] : null,
                 step_m: +$("#ds-step").value || 0.5, name: $("#ds-name").value.trim() };
      }, (r) => r.rows ? `Bed at ${fmt(r.bed_m, 1)} m, catchment ${fmt(r.catchment_km2, 2)} km²: ${r.rows.filter((_, i, a) => i === a.length - 1 || i % Math.ceil(a.length / 5) === 0).map((x) => `${x.height_m} m → ${fmt(x.area_ha, 2)} ha, ${fmt(x.volume_m3, 0)} m³`).join(" · ")}`
        : `<b>${r.sites} sites</b> of ${r.tried.toLocaleString()} tried; the best holds <b>${fmt(r.best_volume_m3, 0)} m³</b> behind a ${r.height_m} m dam; ${fmt(r.total_volume_m3, 0)} m³ in all. Ranked by water per metre of dam.`);
      sync();
      return { open(a) { LF.fillLayers($("#ds-dem"), rasters(LF), { empty: "No raster layer in Contents", pick: a?.layer }); sync(); }, layersChanged() { LF.fillLayers($("#ds-dem"), rasters(LF), { empty: "No raster layer in Contents" }); } };
    },
  });

  // ------------------------------------------------------------------ Morphometry & prioritisation
  LF.tool({ id: "hydmorph", menu: "hydro", title: "Morphometry & prioritisation", icon: "hydmorph", kinds: ["hydromorph"],
    subtitle: "Linear, areal and relief parameters of every sub-watershed (Rb, Dd, Fs, Rc, Re, Ff, ruggedness, hypsometric integral…) and their priority for conservation",
    panel: `<div class="card"><h2>DEM ${tip("Sub-watersheds of about the size you give, each described by the classical parameters (Horton, Strahler, Schumm, Miller), then ranked by the compound value: high priority = most erosion-prone.")}</h2>${sel("mo-dem", "DEM")}
      <label>Watersheds <select id="mo-mode"><option value="subbasins">Sub-watersheds of the area</option><option value="points">Watersheds of outlet points</option></select></label>
      <div id="mo-sub"><label>Sub-watersheds of about (km²) <input type="number" id="mo-bkm2" value="5" min="0.01" step="any"></label></div>
      <div id="mo-pts-box" class="hidden">${pointsHtml("mo-pts", "Outlets", "")}</div>
      <label>Streams for the parameters drain at least (km²) <input type="number" id="mo-km2" value="0.5" min="0.001" step="any"></label>
      ${nameRow("mo", "morphometry")}${runRow("mo", "Describe & rank")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "mo-pts", { max: 500, color: "#7c3aed" });
      const sync = () => { const p = $("#mo-mode").value === "points"; $("#mo-sub").classList.toggle("hidden", p); $("#mo-pts-box").classList.toggle("hidden", !p); if (p) pk.fill(); else pk.hide(); };
      $("#mo-mode").onchange = sync;
      job(LF, "mo", "/api/hydro/morphometry", () => ({ dem: demOf(LF, "mo-dem"), mode: $("#mo-mode").value, points: $("#mo-mode").value === "points" ? pk.get("outlets") : [],
        basin_km2: +$("#mo-bkm2").value || 5, stream_km2: +$("#mo-km2").value || 0.5, name: $("#mo-name").value.trim() || "morphometry" }),
      (r) => `<b>${r.basins} watersheds</b> described.${r.rows?.[0]?.priority ? ` Highest priority: ${r.rows.filter((x) => x.priority === "High").slice(0, 6).map((x) => `#${x.basin} (Dd ${fmt(x.Dd, 2)}, Rb ${fmt(x.Rb, 2)}, HI ${fmt(x.HI, 2)})`).join(", ")}.` : ""} The full table and the hypsometric curves are in Contents ▸ Tabular data.`);
      sync();
      return { open(a) { LF.fillLayers($("#mo-dem"), rasters(LF), { empty: "No raster layer in Contents", pick: a?.layer }); sync(); }, layersChanged() { LF.fillLayers($("#mo-dem"), rasters(LF), { empty: "No raster layer in Contents" }); } };
    },
  });

  // ------------------------------------------------------------------ Groundwater potential
  LF.tool({ id: "hydgw", menu: "hydro", title: "Groundwater potential", icon: "hydgw", kinds: ["hydrogw"],
    subtitle: "Groundwater potential zones from slope, drainage density, wetness, rainfall, land cover, lineaments, geology and soil, weighted by AHP, checked against wells",
    panel: `<div class="card"><h2>Layers ${tip("Only the DEM is needed; every other layer adds to the map. Geology and soil: give each class a score from 1 (poor aquifer / tight soil) to 5 (good), e.g. 1: 5, 2: 3, 3: 1.")}</h2>
      ${sel("gw-dem", "DEM")}${sel("gw-rain", "Rainfall raster")}${sel("gw-lc", "Land cover")}${sel("gw-lin", "Lineaments / faults (lines)")}
      ${sel("gw-geo", "Geology raster")}<label>Geology scores <input type="text" id="gw-geos" placeholder="class: score, … e.g. 1: 5, 2: 3"></label>
      ${sel("gw-soil", "Soil raster")}<label>Soil scores <input type="text" id="gw-soils" placeholder="class: score, …"></label>
      <label>Densities within (m) <input type="number" id="gw-rad" value="1000" min="100" step="any"></label></div>
      <div class="card"><h2>Check with wells (optional)</h2>${sel("gw-wells", "Wells (points)")}${sel("gw-wf", "Yield or water level field")}
      ${nameRow("gw", "groundwater")}${runRow("gw", "Map the potential")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const wf = () => keep($("#gw-wf"), fieldsOf(LF.getLayer($("#gw-wells").value)).map((k) => `<option>${esc(k)}</option>`).join("") || `<option value="">(no fields)</option>`);
      $("#gw-wells").addEventListener("change", wf);
      job(LF, "gw", "/api/hydro/groundwater", () => {
        const geo = pathOf(LF, "gw-geo"), soil = pathOf(LF, "gw-soil");
        if (geo && !Object.keys(pairs($("#gw-geos").value)).length) throw new Error("Give a score for each geology class, e.g. 1: 5, 2: 3");
        if (soil && !Object.keys(pairs($("#gw-soils").value)).length) throw new Error("Give a score for each soil class");
        return { dem: demOf(LF, "gw-dem"), rain_raster: pathOf(LF, "gw-rain"), landcover: pathOf(LF, "gw-lc"), lineaments: geoOf(LF, "gw-lin"), geology: geo,
                 geology_scores: geo ? pairs($("#gw-geos").value) : null, soil, soil_scores: soil ? pairs($("#gw-soils").value) : null, radius_m: +$("#gw-rad").value || 1000,
                 wells: geoOf(LF, "gw-wells"), wells_field: $("#gw-wells").value ? $("#gw-wf").value || null : null, name: $("#gw-name").value.trim() || "groundwater" };
      }, (r) => `<b>Weights</b> (AHP${r.cr != null ? `, CR ${r.cr}` : ""}): ${Object.entries(r.factors).map(([k, v]) => `${esc(k)} ${Math.round(v * 100)}%`).join(", ")}. Zones: ${Object.entries(r.zone_pct).map(([k, v]) => `${esc(k)} ${v}%`).join(" · ")}${r.validation ? `<br>Wells (${r.validation.wells}): rank correlation <b>${r.validation.spearman_rho}</b> (p ${r.validation.p_value}); ${Object.entries(r.validation.mean_by_zone).map(([k, v]) => `${esc(k)} ${v}`).join(", ")}` : ""}`);
      const fill = (pick) => {
        LF.fillLayers($("#gw-dem"), rasters(LF), { empty: "No raster layer in Contents", pick });
        ["gw-rain", "gw-lc", "gw-geo", "gw-soil"].forEach((id) => keep($(`#${id}`), opts(rasters(LF), "(none)")));
        keep($("#gw-lin"), opts(lineLayers(LF), "(none)")); keep($("#gw-wells"), opts(pointLayers(LF), "(none)")); wf();
      };
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ Streamflow modelling
  LF.tool({ id: "hydstream", menu: "hydro", title: "Streamflow modelling", icon: "hydstream", kinds: ["hydrostreamflow"],
    subtitle: "Daily river flow from rain and evaporation: GR4J (conceptual), LightGBM / Random Forest (machine learning) and an LSTM (deep learning), calibrated on observed flow and scored on later years",
    panel: `<div class="card"><h2>Observed flow ${tip("A table (Add data: CSV / Excel) with a date column and daily flow. Rain and PET columns are used when present; otherwise they come from the ERA5 archive at the catchment's centre (give the place).")}</h2>
      <label>Table <select id="sf-table"></select></label>
      <div class="row"><label style="flex:1">Date column <input type="text" id="sf-date" placeholder="automatic"></label><label style="flex:1">Flow column <input type="text" id="sf-flow" placeholder="automatic"></label></div>
      <label>Flow in <select id="sf-units"><option value="m3s">m³/s</option><option value="mm">mm/day</option></select></label>
      <label>Catchment area (km²) ${tip("For m³/s ↔ mm/day. Watersheds gives it.")}<input type="number" id="sf-area" min="0" step="any"></label>
      ${pointsHtml("sf-pt", "Catchment centre (for ERA5 rain and evaporation)", "")}</div>
      <div class="card"><h2>Models ${tip("All are calibrated on the first part of the record and scored on the rest (they never saw it): NSE and KGE (1 is perfect; above 0.5 is usually useful), percent bias. The day-of-year mean is the baseline to beat.")}</h2>
      <div class="sf-ticks" style="grid-template-columns:1fr 1fr">${[["gr4j", "GR4J (conceptual)", 1], ["lgbm", "LightGBM (ML)", 1], ["rf", "Random Forest (ML)", 0], ["lstm", "LSTM (deep learning add-on)", 0]].map(([v, t, on]) =>
        `<label class="check"><input type="checkbox" data-sfm="${v}" ${on ? "checked" : ""}> ${t}</label>`).join("")}</div>
      <label>Calibrate on the first (%) <input type="number" id="sf-cal" value="70" min="30" max="90"></label>
      <label class="check"><input type="checkbox" id="sf-past"> ML models may use yesterday's observed flow ${tip("Makes them a one-day-ahead forecast (much more accurate) rather than a simulation from weather alone.")}</label>
      ${nameRow("sf", "streamflow")}${runRow("sf", "Calibrate & compare")}</div>`,
    setup(LF) {
      const { $, $$ } = LF;
      const pk = pointsPicker(LF, "sf-pt", { max: 1, color: "#2563eb" });
      const fillT = () => keep($("#sf-table"), LF.dataItems.filter((d) => d.kind === "table" && d.path).map((d) => `<option value="${esc(d.path)}">${esc(d.name)}</option>`).join("") || `<option value="">No table in Contents</option>`);
      job(LF, "sf", "/api/hydro/streamflow", () => {
        if (!$("#sf-table").value) throw new Error("Add the observed-flow table first (Add data)");
        const models = $$("#tab-hydstream [data-sfm]:checked").map((c) => c.dataset.sfm);
        if (!models.length) throw new Error("Tick at least one model");
        let pt = null; try { pt = pk.get("place", 0)[0] || null; } catch { pt = null; }
        return { table: $("#sf-table").value, date_col: $("#sf-date").value.trim() || null, flow_col: $("#sf-flow").value.trim() || null, units: $("#sf-units").value,
                 area_km2: +$("#sf-area").value || null, point: pt, cal_frac: (+$("#sf-cal").value || 70) / 100, models, past_flow: $("#sf-past").checked, name: $("#sf-name").value.trim() || "streamflow" };
      }, (r) => `<table class="mini"><tr><th>Model</th><th>NSE (validation)</th><th>KGE (validation)</th><th>Bias %</th></tr>${r.scores.map((s) => `<tr><td>${esc(s.model)}</td><td>${fmt(s.val_nse, 3)}</td><td>${fmt(s.val_kge, 3)}</td><td>${fmt(s.val_pbias, 1)}</td></tr>`).join("")}</table>
        Best on the unseen years: <b>${esc(r.best || "–")}</b>. Forcing from ${esc(r.forcing)}; calibrated until ${esc(r.calibration_until)}.${r.gr4j_params ? ` GR4J: ${Object.entries(r.gr4j_params).map(([k, v]) => `${esc(k)} ${v}`).join(", ")}.` : ""}`);
      return { open() { fillT(); pk.fill(); }, layersChanged() { fillT(); pk.fill(); } };
    },
  });

  // ------------------------------------------------------------------ Design flood hydrograph
  LF.tool({ id: "hydhydrograph", menu: "hydro", title: "Design flood hydrograph", icon: "hydhydrograph", kinds: ["hydrohydrograph"],
    subtitle: "Flow at a watershed's outlet hour by hour through design storms (e.g. the 10-, 50- and 100-year rain): SCS-CN excess rain and the SCS unit hydrograph, with peaks and volumes",
    panel: `<div class="card"><h2>Watershed ${tip("The watershed of the outlet, its time of concentration (Kirpich) and curve number. Storm depths from Rainfall data ▸ Design storms.")}</h2>
      ${sel("hg-dem", "DEM")}${pointsHtml("hg-pt", "Outlet", "")}
      <label>Curve number from <select id="hg-cnsrc"><option value="lc">Land cover + soil group</option><option value="cn">One value</option></select></label>
      <div id="hg-lc-row">${sel("hg-lc", "Land cover")}<label>Soil group <select id="hg-soil"><option>A</option><option selected>B</option><option>C</option><option>D</option></select></label></div>
      <label id="hg-cn-row" class="hidden">CN <input type="number" id="hg-cn" value="75" min="1" max="100"></label>
      <label>Ground before the storm <select id="hg-amc"><option value="I">Dry</option><option value="II" selected>Normal</option><option value="III">Wet</option></select></label></div>
      <div class="card"><h2>Storms</h2><label>Rain (mm), one per storm <input type="text" id="hg-storms" value="80, 120, 160"></label>
      <label>Their names <input type="text" id="hg-labels" placeholder="e.g. 10-yr, 50-yr, 100-yr"></label>
      <label>Pattern <select id="hg-pat"><option value="scs2">SCS Type II (24 h)</option><option value="uniform">Even over a duration</option></select></label>
      <label id="hg-dur-row" class="hidden">Duration (h) <input type="number" id="hg-dur" value="6" min="0.1" step="any"></label>
      <label>Time step (h) <input type="number" id="hg-dt" value="0.25" min="0.02" step="any"></label>${nameRow("hg", "design_flood")}${runRow("hg", "Compute hydrographs")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "hg-pt", { max: 1, color: "#1d4ed8" });
      const sync = () => { const cn = $("#hg-cnsrc").value === "cn"; $("#hg-lc-row").classList.toggle("hidden", cn); $("#hg-cn-row").classList.toggle("hidden", !cn); $("#hg-dur-row").classList.toggle("hidden", $("#hg-pat").value !== "uniform"); };
      $("#hg-cnsrc").onchange = sync; $("#hg-pat").onchange = sync;
      job(LF, "hg", "/api/hydro/hydrograph", () => {
        const storms = $("#hg-storms").value.split(/[ ,;]+/).map(Number).filter((n) => n > 0);
        if (!storms.length) throw new Error("Give the storm depths in mm");
        const labels = $("#hg-labels").value.split(/[,;]+/).map((s) => s.trim()).filter(Boolean);
        const cn = $("#hg-cnsrc").value === "cn";
        if (!cn && !pathOf(LF, "hg-lc")) throw new Error("Choose the land-cover map (or one CN)");
        return { dem: demOf(LF, "hg-dem"), point: pk.get("outlet")[0], storms_mm: storms, labels: labels.length === storms.length ? labels : null, cn: cn ? +$("#hg-cn").value : null,
                 landcover: cn ? null : pathOf(LF, "hg-lc"), soil: $("#hg-soil").value, condition: $("#hg-amc").value, pattern: $("#hg-pat").value, duration_h: +$("#hg-dur").value || 6,
                 dt_h: +$("#hg-dt").value || 0.25, name: $("#hg-name").value.trim() || "design_flood" };
      }, (r) => `Watershed ${fmt(r.area_km2)} km², CN ${r.curve_number}, time of concentration ${fmt(r.tc_h)} h.<br>${r.storms.map((s) => `${esc(s.storm)}: ${fmt(s.runoff_mm, 1)} mm runs off → peak <b>${fmt(s.peak_m3s, 1)} m³/s</b> at ${fmt(s.time_to_peak_h, 1)} h, ${fmt(s.volume_m3 / 1e6, 3)} million m³`).join("<br>")}`);
      const fill = (pick) => { LF.fillLayers($("#hg-dem"), rasters(LF), { empty: "No raster layer in Contents", pick }); keep($("#hg-lc"), opts(rasters(LF))); pk.fill(); };
      sync();
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ Flood simulation 2D
  LF.tool({ id: "hydsim", menu: "hydro", title: "Flood simulation (2D)", icon: "hydsim", kinds: ["hydrosim"],
    subtitle: "Water spreading over the DEM through time from river inflows and / or rain (local inertial shallow-water model, as LISFLOOD-FP): maximum depth and speed, arrival time, depth over time",
    panel: `<div class="card"><h2>Terrain ${tip("Up to 1.5 million cells: resample or clip a fine DEM (e.g. 30 m over 30 × 30 km). Run DEM preparation first only if pits are artefacts: real hollows should hold water.")}</h2>${sel("fs-dem", "DEM")}
      <label>Roughness from <select id="fs-nsrc"><option value="one">One Manning's n</option><option value="lc">Land cover</option></select></label>
      <label id="fs-n-row">Manning's n ${tip("0.03 smooth / open water, 0.035 fields, 0.05–0.08 towns or shrubs, 0.1 forest.")}<input type="number" id="fs-n" value="0.035" min="0.005" step="0.005"></label>
      <div id="fs-lc-row" class="hidden">${sel("fs-lc", "Land cover")}</div></div>
      <div class="card"><h2>Water ${tip("Inflow: m³/s at each clicked point, one value for the whole run or a hydrograph 'hour: m³/s', e.g. 0: 0, 2: 300, 8: 0 (from the Design flood hydrograph). Rain: mm/h on every cell, the same way.")}</h2>
      ${pointsHtml("fs-pts", "Inflow points (on the river)", "")}<label>Inflow (m³/s, or hour: m³/s, …) <input type="text" id="fs-q" value="0: 0, 1: 200, 4: 0"></label>
      <label>Rain (mm/h, or hour: mm/h, …) <input type="text" id="fs-rain" placeholder="none"></label>
      <div class="row"><label style="flex:1">Simulate (hours) <input type="number" id="fs-hours" value="6" min="0.1" step="any"></label><label style="flex:1">Depth maps <input type="number" id="fs-snaps" value="6" min="0" max="48"></label></div>
      ${nameRow("fs", "flood_sim")}${runRow("fs", "Run the simulation")}</div>`,
    setup(LF) {
      const { $ } = LF;
      const pk = pointsPicker(LF, "fs-pts", { max: 50, color: "#dc2626" });
      $("#fs-nsrc").onchange = () => { const lc = $("#fs-nsrc").value === "lc"; $("#fs-n-row").classList.toggle("hidden", lc); $("#fs-lc-row").classList.toggle("hidden", !lc); };
      job(LF, "fs", "/api/hydro/flood-sim", () => {
        let pts = []; try { pts = pk.get("inflow points", 0); } catch { pts = []; }
        const q = series($("#fs-q").value), rain = series($("#fs-rain").value);
        if (!pts.length && !rain) throw new Error("Click at least one inflow point, or give rain");
        return { dem: demOf(LF, "fs-dem"), inflows: pts.length && q != null ? pts.map(([lon, lat]) => ({ lon, lat, q })) : [], rain_mm_h: rain, hours: +$("#fs-hours").value || 6,
                 manning: +$("#fs-n").value || 0.035, landcover: $("#fs-nsrc").value === "lc" ? pathOf(LF, "fs-lc") : null, snapshots: +$("#fs-snaps").value, name: $("#fs-name").value.trim() || "flood_sim" };
      }, (r) => `<b>${fmt(r.flooded_km2, 3)} km²</b> flooded, up to <b>${fmt(r.max_depth_m)} m</b> deep and ${fmt(r.max_speed_ms)} m/s, in ${r.steps.toLocaleString()} steps. In: ${fmt((r.inflow_m3 + r.rain_m3) / 1e6, 3)} million m³; still on the land ${fmt(r.stored_m3 / 1e6, 3)}, left the area ${fmt(r.out_m3 / 1e6, 3)} (water balance error ${fmt(r.mass_error_pct, 3)}%).`);
      const fill = (pick) => { LF.fillLayers($("#fs-dem"), rasters(LF), { empty: "No raster layer in Contents", pick }); keep($("#fs-lc"), opts(rasters(LF))); pk.fill(); };
      return { open(a) { fill(a?.layer); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ AHP weights & overlay (Tools ▸ Fuzzy & suitability)
  const SAATY = [[9, "9 × more"], [7, "7 × more"], [5, "5 × more"], [3, "3 × more"], [1, "equal"], [1 / 3, "3 × less"], [1 / 5, "5 × less"], [1 / 7, "7 × less"], [1 / 9, "9 × less"]];
  LF.tool({ id: "ahp", title: "AHP weights & overlay", icon: "ahp", kinds: ["ahp"],
    subtitle: "Weigh factors by comparing them in pairs (Saaty's AHP) with a consistency check, then combine the scored layers into a suitability map (site selection, groundwater, flood risk…)",
    panel: `<div class="card"><h2>Factors ${tip("Tick 2–15 raster layers. For each: does a higher value make a place better (e.g. rainfall) or worse (e.g. slope)? Each is scored 0–1 from its 2nd to 98th percentile.")}</h2>
      <div id="ah-facs"></div></div>
      <div class="card"><h2>Compare in pairs ${tip("How much more important is the factor on the left than the one on the right? 1 equal, 3 moderately, 5 strongly, 7 very strongly, 9 extremely. CR (consistency ratio) should be under 0.10.")}</h2>
      <div id="ah-pairs" class="hint">Tick at least two factors.</div><div id="ah-weights" class="hint"></div>
      <label>Result title <input type="text" id="ah-title" value="Suitability" maxlength="60"></label>${nameRow("ah", "ahp_suitability")}${runRow("ah", "Weigh & combine")}</div>`,
    setup(LF) {
      const { $, $$, api } = LF;
      const chosen = () => $$("#ah-facs [data-f]:checked").map((c) => ({ id: c.dataset.f, rising: $(`#ah-dir-${CSS.escape(c.dataset.f)}`)?.value !== "down" }));
      const matrix = () => {
        const f = chosen(), n = f.length, A = Array.from({ length: n }, () => Array(n).fill(1));
        $$("#ah-pairs select[data-i]").forEach((s) => { const i = +s.dataset.i, j = +s.dataset.j; A[i][j] = +s.value; A[j][i] = 1 / +s.value; });
        return A;
      };
      const showW = async () => {
        const f = chosen();
        if (f.length < 2) { $("#ah-weights").textContent = ""; return; }
        try {
          const r = await api("/api/ahp/weights", { method: "POST", json: { matrix: matrix() } });
          $("#ah-weights").innerHTML = `Weights: ${f.map((x, i) => `<b>${esc(LF.getLayer(x.id)?.name || "?")}</b> ${Math.round(r.weights[i] * 100)}%`).join(", ")} · CR <b style="color:${r.consistent ? "var(--ok)" : "var(--err)"}">${r.cr}</b>${r.consistent ? " (consistent)" : ` → reconsider ${esc(LF.getLayer(f[r.worst_pair[0]].id)?.name)} vs ${esc(LF.getLayer(f[r.worst_pair[1]].id)?.name)} (the weights suggest about ${r.suggested} ×)`}`;
        } catch (e) { $("#ah-weights").textContent = e.message; }
      };
      const pairsUi = () => {
        const f = chosen();
        if (f.length < 2) { $("#ah-pairs").innerHTML = "Tick at least two factors."; showW(); return; }
        let html = "";
        for (let i = 0; i < f.length; i++) for (let j = i + 1; j < f.length; j++) {
          html += `<label class="ah-pair">${esc(LF.getLayer(f[i].id)?.name)} <select data-i="${i}" data-j="${j}">${SAATY.map(([v, t]) => `<option value="${v}" ${v === 1 ? "selected" : ""}>${t}</option>`).join("")}</select> important than ${esc(LF.getLayer(f[j].id)?.name)}</label>`;
        }
        $("#ah-pairs").innerHTML = html;
        $$("#ah-pairs select").forEach((s) => s.onchange = showW);
        showW();
      };
      const fill = () => {
        const was = new Set($$("#ah-facs [data-f]:checked").map((c) => c.dataset.f));
        $("#ah-facs").innerHTML = rasters(LF).map((l) => `<div class="row" style="gap:6px;align-items:center"><label class="check" style="flex:1"><input type="checkbox" data-f="${esc(l.id)}" ${was.has(l.id) ? "checked" : ""}> ${esc(l.name)}</label>
          <select id="ah-dir-${esc(l.id)}" style="width:auto;margin:0"><option value="up">higher = better</option><option value="down">lower = better</option></select></div>`).join("") || `<p class="hint">No raster layer in Contents.</p>`;
        $$("#ah-facs [data-f]").forEach((c) => c.onchange = pairsUi);
        pairsUi();
      };
      job(LF, "ah", "/api/ahp/overlay", () => {
        const f = chosen();
        if (f.length < 2) throw new Error("Tick at least two factors");
        return { factors: f.map((x) => ({ path: LF.getLayer(x.id).path, rising: x.rising, name: LF.getLayer(x.id).name })), matrix: matrix(), title: $("#ah-title").value.trim() || "Suitability", name: $("#ah-name").value.trim() || "ahp_suitability" };
      }, (r) => `Weights ${r.weights.map((w) => `${Math.round(w * 100)}%`).join(", ")}${r.cr != null ? ` · CR ${r.cr}${r.consistent ? "" : " ⚠ above 0.10"}` : ""}. ${Object.entries(r.class_pct).map(([k, v]) => `${esc(k)} ${v}%`).join(" · ")}`);
      return { open() { fill(); }, layersChanged() { fill(); } };
    },
  });

  // ------------------------------------------------------------------ SAR flood map: ML refinement (SAR menu)
  LF.tool({ id: "sarfloodml", menu: "sar", title: "Flood map: ML refinement", icon: "sarfloodml", kinds: ["sarfloodml"],
    subtitle: "A cleaner SAR flood map: a model (LightGBM or a U-Net) learns from the Flood & water map's confident pixels, using backscatter, texture, change and terrain, and decides the uncertain ones",
    panel: `<div class="card"><h2>Inputs ${tip("Run Flood & water map first: its confidence layer gives the training labels (≥ 0.75 water, ≤ 0.25 dry). A DEM adds HAND and slope, a pre-flood image the change: both help a lot.")}</h2>
      ${sel("fm-sar", "SAR image (VV / VH)")}${sel("fm-conf", "Flood map confidence (or classes)")}${sel("fm-pre", "Pre-flood SAR (optional)")}${sel("fm-dem", "DEM (optional)")}
      <label>Model <select id="fm-model"><option value="lgbm">LightGBM (fast)</option><option value="unet">TinyUNet (deep learning add-on; sees shapes)</option></select></label>
      <label id="fm-ep-row" class="hidden">Epochs <input type="number" id="fm-ep" value="30" min="1" max="300"></label>
      ${nameRow("fm", "flood_ml")}${runRow("fm", "Refine")}</div>`,
    setup(LF) {
      const { $ } = LF;
      $("#fm-model").onchange = () => $("#fm-ep-row").classList.toggle("hidden", $("#fm-model").value !== "unet");
      job(LF, "fm", "/api/sar/flood-ml", () => {
        const s = pathOf(LF, "fm-sar"), c = LF.getLayer($("#fm-conf").value);
        if (!s) throw new Error("Choose the SAR image");
        if (!c) throw new Error("Choose the Flood & water map's confidence layer");
        const isClasses = /class/i.test(c.name) || /class/i.test(c.path || "");
        return { sar: s, confidence: isClasses ? null : c.path, classes: isClasses ? c.path : null, pre: pathOf(LF, "fm-pre"), dem: pathOf(LF, "fm-dem"),
                 model: $("#fm-model").value, epochs: +$("#fm-ep").value || 30, name: $("#fm-name").value.trim() || "flood_ml" };
      }, (r) => `<b>${esc(r.model)}</b> trained on ${r.trained_on.water.toLocaleString()} water and ${r.trained_on.dry.toLocaleString()} dry pixels; ${r.uncertain_resolved.toLocaleString()} uncertain pixels decided. Water ${fmt(r.water_km2, 3)} km². On held-out blocks: accuracy <b>${fmt(r.held_out.accuracy, 3)}</b>, IoU of water <b>${fmt(r.held_out.iou_water, 3)}</b>.${r.importance && !r.importance.unet ? ` Most useful: ${Object.keys(r.importance).slice(0, 4).map(esc).join(", ")}.` : ""}`);
      const fill = () => { ["fm-sar", "fm-conf"].forEach((id) => LF.fillLayers($(`#${id}`), rasters(LF), { empty: "No raster layer in Contents" })); ["fm-pre", "fm-dem"].forEach((id) => keep($(`#${id}`), opts(rasters(LF), "(none)"))); };
      return { open() { fill(); }, layersChanged() { fill(); } };
    },
  });
})();
