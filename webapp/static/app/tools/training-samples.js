  // Training samples: draw labelled polygons / points.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

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
