  // Save to folder… (right-click a layer in Contents).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Save to folder… (right-click in Contents)
  async function saveLayerToFolder(l) {
    const folder = await pickFolder({ title: `Save “${l.name}” in…`, start: prefs.get("save-dir:last", ""), okLabel: "Save here" });
    if (!folder) return;
    prefs.set("save-dir:last", folder);
    const name = safeName(l.name);
    try {
      let r;
      if (l.type === "vector") {
        r = await api("/api/vector/export", { method: "POST", json: { geojson: l.geojson, format: "shp", name, folder } });
      } else if (l.type === "raster") {
        const asShown = l.render?.index || l.render?.formula;   // index / formula layers are saved as their computed values
        const body = { path: l.path, format: "tif", name, folder, band_map: l.band_map || {}, scale: l.scale ?? 1, offset: l.offset ?? 0, ...(asShown ? l.render : {}) };
        const job = await api("/api/layers/export", { method: "POST", json: body });
        r = (await trackJob(job, { title: `Saving ${l.name}` })).result;
      } else return toast("This layer can't be saved as a file", true);
      toast(`Saved ${r.saved?.map((p) => p.split(/[\\/]/).pop()).join(", ")} in ${r.saved_to}`);
      status(`Saved ${l.name} in ${r.saved_to}`);
    } catch (e) { if (notCancelled(e)) toast(e.message, true); }
  }
  async function saveItemToFolder(it) {
    const folder = await pickFolder({ title: `Save “${it.name}” in…`, start: prefs.get("save-dir:last", ""), okLabel: "Save here" });
    if (!folder) return;
    prefs.set("save-dir:last", folder);
    try {
      const r = await api("/api/files/save", { method: "POST", json: { paths: [it.path], folder } });
      toast(r.saved.length ? `Saved ${r.saved.map((p) => p.split(/[\\/]/).pop()).join(", ")} in ${r.folder}` : `Not saved: ${r.skipped[0]?.reason}`, !r.saved.length);
    } catch (e) { toast(e.message, true); }
  }
