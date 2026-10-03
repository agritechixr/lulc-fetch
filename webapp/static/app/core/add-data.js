  // + Add data: rasters, vectors, tables and pictures from the computer.
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
        await addRasterFromPath(r.path, { name: f.name });
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    for (const f of pics) {
      try {
        const fd = new FormData();
        [f, ...picSide(f)].forEach((x) => fd.append("files", x));
        const r = await api("/api/pictures/upload", { method: "POST", body: fd });
        if (r.kind === "raster") {
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
        addItem({ kind: "table", name: r.name, path: r.path }, { open: true });
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    for (const group of [...(shpParts.length ? [shpParts] : []), ...others.map((f) => [f])]) {
      try {
        const fd = new FormData();
        group.forEach((f) => fd.append("files", f));
        const fc = await api("/api/aoi/upload", { method: "POST", body: fd });
        const name = (group.find((f) => /\.shp$/i.test(f.name)) || group[0]).name.replace(/\.[^.]+$/, "");
        addVectorLayer({ type: "FeatureCollection", features: fc.features }, name);
        if (fc.warning) toast(`${name}: ${fc.warning}`, true);
      } catch (e) { toast(`${group[0].name}: ${e.message}`, true); }
    }
    status(`Added ${files.length} file${files.length > 1 ? "s" : ""}`);
  }
  $("#btn-add-data").onclick = () => $("#add-file").click();
  $("#add-file").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
  $("#btn-add-ws").onclick = () => openWorkspace();

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
    if (e.target.closest("#drop, #an-drop, #ad-drop")) return;  // these upload boxes handle their own drops
    // entries must be read during the event; folders (e.g. a .SAFE product) only show up this way
    const entries = [...(e.dataTransfer.items || [])].map((i) => i.kind === "file" && i.webkitGetAsEntry ? i.webkitGetAsEntry() : null);
    if (entries.some((x) => x?.isDirectory)) addDropped(entries.filter(Boolean)).catch((err) => toast(err.message, true));
    else addFiles(e.dataTransfer.files);
  });
