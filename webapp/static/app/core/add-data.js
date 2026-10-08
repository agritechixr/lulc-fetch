  // Add data (Insert ▸ Add data, File ▸ Add data, or drop files): rasters, vectors (a GeoPackage: each of its layers), tables,
  // pictures, and field collection files (.zip from the phone page: points + photos).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ add data
  const TABLE_RE = /\.(csv|tsv|txt|parquet|xlsx|xlsm)$/i, PIC_RE = /\.(jpe?g|png|bmp|gif|webp)$/i,
        WORLD_RE = /\.(jgw|jpgw|jpegw|pgw|pngw|bpw|bmpw|gfw|gifw|wld)$/i;
  const stemOf = (n) => n.replace(/(\.aux\.xml|\.[^.]+)$/i, "").toLowerCase();
  async function addFiles(fileList) {
    let files = [...fileList];
    if (!files.length) return;
    const safeZips = files.filter((f) => /\.zip$/i.test(f.name) && SAFE_NAME_RE.test(f.name));
    if (safeZips.length) {
      files = files.filter((f) => !safeZips.includes(f));
      for (const f of safeZips) await uploadSafeZip(f);
      if (!files.length) return;
    }
    const fieldZips = [];
    for (const f of files) if (/\.zip$/i.test(f.name) && await isFieldZip(f)) fieldZips.push(f);
    if (fieldZips.length) {
      files = files.filter((f) => !fieldZips.includes(f));
      for (const f of fieldZips) await importFieldZip(f).catch((e) => toast(`${f.name}: ${e.message}`, true));
      if (!files.length) return;
    }
    const rasters = files.filter((f) => /\.tiff?$/i.test(f.name));
    const tables = files.filter((f) => TABLE_RE.test(f.name));
    const pics = files.filter((f) => PIC_RE.test(f.name));
    // world files / .prj / .aux.xml that belong to a picture (same name, or the only picture)
    const picSide = (pic) => files.filter((f) => (WORLD_RE.test(f.name) || /\.(prj|aux\.xml)$/i.test(f.name)) &&
      (stemOf(f.name) === stemOf(pic.name) || stemOf(f.name) === pic.name.toLowerCase() || (pics.length === 1 && WORLD_RE.test(f.name))));
    const used = new Set([...rasters, ...tables, ...pics, ...pics.flatMap(picSide)]);
    const shpParts = files.filter((f) => !used.has(f) && /\.(shp|shx|dbf|prj|cpg)$/i.test(f.name));
    const others = files.filter((f) => !used.has(f) && !shpParts.includes(f) && !WORLD_RE.test(f.name));
    status(`Adding ${files.length} file${files.length > 1 ? "s" : ""}…`, true);
    for (const f of rasters) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const r = await api("/api/rasters/upload", { method: "POST", body: fd });
        await addRasterAskCrs(r.path, f.name);   // opens it, or first asks for its coordinate system when it has none
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    for (const f of pics) {
      try {
        const fd = new FormData();
        [f, ...picSide(f)].forEach((x) => fd.append("files", x));
        const r = await api("/api/pictures/upload?ask_crs=true", { method: "POST", body: fd });
        if (r.kind === "needs_crs") await addRasterAskCrs(r.path, f.name, `It has a world file (its pixel grid) but ${r.reason.replace(/^it has a world file but /, "")}.`);
        else if (r.kind === "raster") {
          await addRasterFromPath(r.path, { name: f.name });
          if (r.crs_guessed) toast(`${f.name}: no .prj file, so longitude / latitude (WGS 84) was assumed`);
        } else {
          addItem({ kind: "picture", name: f.name, path: r.path, width: r.width, height: r.height, bands: r.bands }, { open: true });
          toast(`${f.name} has no coordinates (${r.reason}). It opened in the data viewer; use "Place on map" to put it on the map.`);
        }
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    for (const f of tables) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const r = await api("/api/tables/upload", { method: "POST", body: fd });
        const it = addItem({ kind: "table", name: r.name, path: r.path }, { open: true });
        // a table with positions (lat / lon columns): its rows also go on the map as points
        if (r.lonlat) await tablePoints(it, "", r.lonlat);
        else if ((r.columns || []).some((c) => /lat|lon|lng|coord|east|north|^[xy]$/i.test(c))) toast(`${f.name}: added as a table. To show it on the map, right-click it ▸ Show points on map… and choose its position columns (longitude / latitude, or X / Y in any coordinate system)`);
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    for (const group of [...(shpParts.length ? [shpParts] : []), ...others.map((f) => [f])]) {
      try {
        const fd = new FormData();
        group.forEach((f) => fd.append("files", f));
        const fc = await api("/api/aoi/upload?ask_crs=true", { method: "POST", body: fd });
        const name = (group.find((f) => /\.shp$/i.test(f.name)) || group[0]).name.replace(/\.[^.]+$/, "");
        if (fc.layers?.length > 1) {   // a GeoPackage with several layers: one layer each (the file's first on top)
          for (const [i, x] of [...fc.layers].reverse().entries()) {
            if (x.crs_missing) await addVectorAskCrs({ features: x.features, raw_bounds: rawBounds(x.features) }, x.name, `The ${x.crs_missing.replace(/^the /, "")}.`);
            else addVectorLayer({ type: "FeatureCollection", features: x.features }, x.name, { zoom: i === fc.layers.length - 1 });
          }
          toast(`${group[0].name}: ${fc.layers.length} layers added`);
        } else if (fc.crs_missing) await addVectorAskCrs(fc, name, `The ${fc.crs_missing.replace(/^the /, "")}, so its coordinates' system isn't known.`);
        else addVectorLayer({ type: "FeatureCollection", features: fc.features }, name);
        if (fc.warning) toast(`${name}: ${fc.warning}`, true);
      } catch (e) { toast(`${group[0].name}: ${e.message}`, true); }
    }
    status(`Added ${files.length} file${files.length > 1 ? "s" : ""}`);
  }
  $("#add-file").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };

  // ---- field collection files (the phone page's .zip): field.json is its first entry, so its first bytes tell
  async function isFieldZip(f) {
    try {
      const b = new Uint8Array(await f.slice(0, 64).arrayBuffer());
      const n = b[26] | (b[27] << 8);
      return b[0] === 0x50 && b[1] === 0x4b && b[2] === 3 && b[3] === 4 && new TextDecoder().decode(b.slice(30, 30 + n)) === "field.json";
    } catch { return false; }
  }
  /** import a field .zip: its points become a layer (photos linked), and the photos can go to Diagnose crop disease */
  async function importFieldZip(f) {
    status(`Importing ${f.name}…`, true);
    const fd = new FormData();
    fd.append("file", f);
    const r = await api("/api/field/import", { method: "POST", body: fd });
    const l = addVectorLayer(r.geojson, `Field · ${r.name}`, { color: "#ea580c", path: r.geojson_path, field: { photos: r.photos.length } });
    saveLayers();
    status(`Imported ${r.points} point${r.points === 1 ? "" : "s"} and ${r.photos.length} photo${r.photos.length === 1 ? "" : "s"} from ${f.name}`);
    LF.fieldImported?.(r, l);
    return r;
  }

  // drop files anywhere on the window
  let dragDepth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { dragDepth++; $("#drop-overlay").classList.remove("hidden"); } });
  window.addEventListener("dragleave", (e) => { if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").classList.add("hidden"); } });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    $("#drop-overlay").classList.add("hidden");
    if (e.target.closest("#drop, #an-drop, #ad-drop, #fc-drop")) return;  // these upload boxes handle their own drops
    // entries must be read during the event; folders (e.g. a .SAFE product) only show up this way
    const entries = [...(e.dataTransfer.items || [])].map((i) => i.kind === "file" && i.webkitGetAsEntry ? i.webkitGetAsEntry() : null);
    if (entries.some((x) => x?.isDirectory)) addDropped(entries.filter(Boolean)).catch((err) => toast(err, true));
    else addFiles(e.dataTransfer.files);
  });
