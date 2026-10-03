  // Layer properties: band combination, stretch, colours.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ layer properties
  // ------------------------------------------------------------------ band combination (any 3 bands as RGB)
  const BAND_NICE = { B01: "Coastal", B02: "Blue", B03: "Green", B04: "Red", B05: "Red edge 1", B06: "Red edge 2", B07: "Red edge 3",
    B08: "NIR", B8A: "NIR narrow", B09: "Water vapour", B10: "Cirrus", B11: "SWIR 1", B12: "SWIR 2", VV: "VV", VH: "VH", HH: "HH", HV: "HV" };
  const BAND_PRESETS = [
    ["True colour", ["B04", "B03", "B02"], "As the eye sees it"],
    ["Colour infrared (NIR · R · G)", ["B08", "B04", "B03"], "Vegetation in red"],
    ["SWIR · NIR · Red", ["B12", "B08", "B04"], "Moisture, burn scars, bare soil"],
    ["Agriculture (SWIR1 · NIR · Blue)", ["B11", "B08", "B02"], "Crops bright green"],
    ["Healthy vegetation (NIR · SWIR1 · Blue)", ["B08", "B11", "B02"], "Vegetation vigour"],
    ["Land / water (NIR · SWIR1 · Red)", ["B08", "B11", "B04"], "Clear water / land edges"],
    ["Urban (SWIR2 · SWIR1 · Red)", ["B12", "B11", "B04"], "Built-up areas stand out"],
    ["Geology (SWIR2 · SWIR1 · Blue)", ["B12", "B11", "B02"], "Rock and soil types"],
    ["Atmospheric penetration", ["B12", "B11", "B8A"], "Sees through haze / smoke"],
    ["Red edge (RE3 · RE2 · RE1)", ["B07", "B06", "B05"], "Vegetation stress"],
    ["Bathymetric (R · G · Coastal)", ["B04", "B03", "B01"], "Shallow water"],
    ["Radar (VV · VH · VV)", ["VV", "VH", "VV"], "Sentinel-1 backscatter"],
  ];
  const bc = { layer: null, before: null, timer: 0 };
  function bcBandLabel(l, i) {
    const d = l.info.bands[i - 1]?.description || `Band ${i}`;
    const mapped = Object.entries(l.band_map || {}).find(([, v]) => v === i)?.[0];
    const nice = BAND_NICE[mapped] || BAND_NICE[d];
    return `${i} · ${d}${nice && nice !== d ? ` (${nice})` : mapped && mapped !== d ? ` (${mapped})` : ""}`;
  }
  // the three file bands the layer currently shows (composite names mapped to band numbers)
  function currentRgb(l) {
    const r = l.render || {}, bm = l.band_map || {}, n = l.info.count;
    if (r.rgb) return r.rgb;
    const names = r.composite && state.catalog?.composites[r.composite]?.bands;
    if (names && names.every((b) => b in bm)) return names.map((b) => bm[b]);
    for (const [, bands] of BAND_PRESETS) if (bands.every((b) => b in bm)) return bands.map((b) => bm[b]);
    return [1, Math.min(2, n), Math.min(3, n)];
  }
  function openBandCombo(l) {
    bc.layer = l; bc.before = JSON.parse(JSON.stringify(l.render || {}));
    $("#bc-title").textContent = `Band combination · ${l.name}`;
    const opts = l.info.bands.map((b) => `<option value="${b.index}">${esc(bcBandLabel(l, b.index))}</option>`).join("");
    ["#bc-r", "#bc-g", "#bc-b"].forEach((id) => $(id).innerHTML = opts);
    const [r, g, b] = currentRgb(l);
    $("#bc-r").value = r; $("#bc-g").value = g; $("#bc-b").value = b;
    $("#bc-stretch").value = l.render?.rgb && ["none", "p1", "minmax"].includes(l.render.stretch) ? l.render.stretch : "auto";
    const bm = l.band_map || {};
    const avail = BAND_PRESETS.filter(([, bands]) => bands.every((x) => x in bm));
    // many bands (an embedding, hyperspectral): all of them can be shown at once, as their three main directions (PCA)
    const pcaBtn = l.info.count >= 4 ? `<button class="bc-preset ${l.render?.pca ? "on" : ""}" data-pca title="Uses every band">
        <b>All ${l.info.count} bands: colour view</b><small>Their three main directions of variation (PCA) as red, green, blue · alike places get alike colours</small></button>` : "";
    $("#bc-presets").innerHTML = pcaBtn + (avail.length ? avail.map(([title, bands, note], k) => `<button class="bc-preset" data-k="${k}" title="${esc(bands.join(" · "))}">
        <b>${esc(title)}</b><small>${esc(bands.join(" · "))} · ${esc(note)}</small></button>`).join("")
      : pcaBtn ? "" : `<p class="hint" style="margin:0">No named bands, so no presets: choose the bands below (e.g. NIR, Red, Green).</p>`) +
      (l.info.count > 3 ? `<p class="hint" style="margin:6px 0 0">The file keeps all ${l.info.count} bands (tools use them all); this only changes what the map shows.</p>` : "");
    $("#bc-presets [data-pca]")?.addEventListener("click", () => {
      l.render = { pca: true, stretch: "auto" };
      bcMarkPreset();
      renderRaster(l).catch((e) => toast(e.message, true));
    });
    $$("#bc-presets .bc-preset[data-k]").forEach((btn) => btn.onclick = () => {
      const bands = avail[+btn.dataset.k][1].map((x) => bm[x]);
      $("#bc-r").value = bands[0]; $("#bc-g").value = bands[1]; $("#bc-b").value = bands[2];
      bcApply();
    });
    bcMarkPreset(avail);
    $("#dlg-bands").showModal();
  }
  function bcMarkPreset(avail) {
    const bm = bc.layer.band_map || {}, cur = [+$("#bc-r").value, +$("#bc-g").value, +$("#bc-b").value].join();
    avail = avail || BAND_PRESETS.filter(([, bands]) => bands.every((x) => x in bm));
    $$("#bc-presets .bc-preset[data-k]").forEach((btn) => btn.classList.toggle("on", !bc.layer.render?.pca && avail[+btn.dataset.k][1].map((x) => bm[x]).join() === cur));
    $("#bc-presets [data-pca]")?.classList.toggle("on", !!bc.layer.render?.pca);
    const nm = (id) => $(id).selectedOptions[0]?.textContent.replace(/^\d+ · /, "") || "";
    $("#bc-hint").textContent = `Red ← ${nm("#bc-r")} · Green ← ${nm("#bc-g")} · Blue ← ${nm("#bc-b")}`;
  }
  function bcApply() {
    const l = bc.layer;
    if (!l) return;
    l.render = { rgb: [+$("#bc-r").value, +$("#bc-g").value, +$("#bc-b").value], stretch: $("#bc-stretch").value };
    bcMarkPreset();
    clearTimeout(bc.timer);
    bc.timer = setTimeout(() => renderRaster(l).catch((e) => toast(e.message, true)), 150);
  }
  ["#bc-r", "#bc-g", "#bc-b", "#bc-stretch"].forEach((id) => $(id).onchange = bcApply);
  $("#bc-reset").onclick = () => {
    const l = bc.layer;
    l.render = defaultRender(l.info);
    const [r, g, b] = currentRgb(l);
    $("#bc-r").value = r; $("#bc-g").value = g; $("#bc-b").value = b;
    $("#bc-stretch").value = l.render.stretch === "none" ? "none" : "auto";
    bcMarkPreset();
    renderRaster(l).catch((e) => toast(e.message, true));
  };
  $("#bc-ok").onclick = () => { bc.before = null; $("#dlg-bands").close(); };
  $("#bc-cancel").onclick = () => $("#dlg-bands").close();
  $("#dlg-bands").addEventListener("close", () => {   // Cancel / × / Esc: put the previous look back
    if (bc.before && bc.layer) { bc.layer.render = bc.before; renderRaster(bc.layer).catch(() => {}); }
    bc.before = null;
  });
