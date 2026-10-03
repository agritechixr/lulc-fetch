  // Identify: click the map to read the selected raster's values.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ identify (click map on selected raster)
  let suppressClickUntil = 0;
  map.on("click", (e) => { if (!picking && !activeDraw && Date.now() > suppressClickUntil) identify(e.latlng); });
  async function identify(latlng) {
    const e = { latlng };
    const l = selectedLayer();
    if (!l || l.type !== "raster" || !l.visible) return;
    try {
      const r = await api("/api/analyze/pixel", { method: "POST", json: {
        path: l.path, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0,
        lat: e.latlng.lat, lon: e.latlng.lng, index: l.render?.index, formula: l.render?.formula } });
      if (!r.inside) return;
      let head = "";
      if ("value" in r) head = `<div>${esc(r.name === "custom" ? "Formula" : r.name)}</div><div class="big">${fmtv(r.value)}</div>`;
      else if (l.render?.band) {
        const v = r.raw[l.render.band - 1]?.value;
        const cls = l.legend?.classes?.find((c) => c.value === v);
        head = `<div>${esc(r.raw[l.render.band - 1]?.description || "Band " + l.render.band)}</div><div class="big">${cls ? esc(cls.name) : fmtv(v)}</div>`;
      }
      const refl = Object.entries(r.reflectance || {});
      const extra = r.raw.filter((b) => !Object.values(l.band_map || {}).includes(b.band));
      L.popup({ maxWidth: 280 }).setLatLng(e.latlng).setContent(`<div class="pxpop">
        <div class="small" style="color:#667085"><b>${esc(l.name)}</b></div>${head}
        <div class="small" style="color:#667085">${fmt(e.latlng.lat, 5)}, ${fmt(e.latlng.lng, 5)} · row ${r.row}, col ${r.col}</div>
        <table>${refl.map(([k, v]) => `<tr><td>${k}</td><td>${fmtv(v)}</td></tr>`).join("")}
        ${extra.slice(0, 20).map((b) => `<tr><td>${esc(b.description)}</td><td>${fmtv(b.value)}</td></tr>`).join("")}</table></div>`).openOn(map);
    } catch (err) { toast(err.message, true); }
  }
