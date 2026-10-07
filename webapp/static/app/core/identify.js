  // Identify: click the map to read the selected raster's values.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ identify (click map on selected raster)
  let suppressClickUntil = 0;
  map.on("click", (e) => { if (!picking && !activeDraw && !measure.on && Date.now() > suppressClickUntil) identify(e.latlng); });
  // the values of one raster layer at a point: every band (scrollable), copy buttons, and for an embedding "similar places"
  async function pixelValues(l, latlng) {
    const r = await api("/api/analyze/pixel", { method: "POST", json: {
      path: l.path, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0,
      lat: latlng.lat, lon: latlng.lng, index: l.render?.index, formula: l.render?.formula } });
    if (!r.inside) return null;
    let head = "";
    if ("value" in r) head = `<div>${esc(r.name === "custom" ? "Formula" : r.name)}</div><div class="big">${fmtv(r.value)}</div>`;
    else if (l.render?.band) {
      const v = r.raw[l.render.band - 1]?.value;
      const cls = l.legend?.classes?.find((c) => c.value === v);
      head = `<div>${esc(r.raw[l.render.band - 1]?.description || "Band " + l.render.band)}</div><div class="big">${cls ? esc(cls.name) : fmtv(v)}</div>`;
    }
    const refl = Object.entries(r.reflectance || {});
    const extra = r.raw.filter((b) => !Object.values(l.band_map || {}).includes(b.band));
    const rows = [...refl.map(([k, v]) => [k, v]), ...extra.map((b) => [b.description, b.value])];
    return { r, head, rows };
  }
  async function identify(latlng, l = selectedLayer()) {
    if (!l || l.type !== "raster" || !l.visible) return;
    try {
      const px = await pixelValues(l, latlng);
      if (!px) return;
      const { r, head, rows } = px;
      const n = r.raw.length, emb = !!l.info?.embedding;
      const pop = L.popup({ maxWidth: 320, minWidth: 240, className: "px-popup" }).setLatLng(latlng).setContent(`<div class="pxpop">
        <div class="small" style="color:#667085"><b>${esc(l.name)}</b></div>${head}
        <div class="small" style="color:#667085">${fmt(latlng.lat, 5)}, ${fmt(latlng.lng, 5)} · row ${r.row}, col ${r.col}${n > 1 ? ` · ${n} bands` : ""}</div>
        ${rows.length ? `<div class="px-scroll"><table>${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmtv(v)}</td></tr>`).join("")}</table></div>` : ""}
        <div class="px-actions"><button type="button" data-px-copy>Copy values</button><button type="button" data-px-xy>Copy coordinates</button>
          ${emb ? `<button type="button" data-px-similar>Find similar places</button>` : ""}</div></div>`).openOn(map);
      const el = pop.getElement();
      $("[data-px-copy]", el).onclick = () => copyText(rows.map(([k, v]) => `${k}\t${v ?? ""}`).join("\n"), `${rows.length} values copied (name ⇥ value per line)`);
      $("[data-px-xy]", el).onclick = () => copyText(`${latlng.lat.toFixed(6)}, ${latlng.lng.toFixed(6)}`, "Coordinates copied (lat, lon)");
      $("[data-px-similar]", el)?.addEventListener("click", () => { map.closePopup(); LF.openTool("embexplore", { layer: l.id, point: [latlng.lng, latlng.lat] }); });
    } catch (err) { toast(err, true); }
  }
  /** copy text to the clipboard, with a message (and a fallback for browsers that refuse) */
  async function copyText(text, msg = "Copied") {
    try { await navigator.clipboard.writeText(text); toast(msg); }
    catch { window.prompt("Copy this (Cmd/Ctrl + C):", text); }
  }
