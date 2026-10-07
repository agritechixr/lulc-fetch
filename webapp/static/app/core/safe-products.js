  // Sentinel .SAFE products: drop a folder or zip, or link one, and open them as layers.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Sentinel products: drop a .SAFE folder / .SAFE.zip, or link one
  const SAFE_NAME_RE = /^S(2[ABCD]_MSI(L1C|L2A)|1[ABCD]_(IW|EW|SM)_GRD)/i;
  const entryFile = (en) => new Promise((res, rej) => en.file(res, rej));
  async function readDir(dirEntry) {
    const reader = dirEntry.createReader(), out = [];
    for (;;) {
      const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
      if (!batch.length) return out;
      out.push(...batch);
    }
  }
  async function walkFiles(dirEntry, base = "") {   // [{rel, entry}] of every file below a folder
    const out = [];
    for (const en of await readDir(dirEntry)) {
      const rel = base ? `${base}/${en.name}` : en.name;
      if (en.isDirectory) out.push(...await walkFiles(en, rel));
      else out.push({ rel, entry: en });
    }
    return out;
  }
  async function addDropped(entries) {
    const products = [], plain = [];
    const visit = async (en, depth) => {
      if (en.isDirectory && /\.SAFE$/i.test(en.name)) products.push(en);
      else if (en.isDirectory) { if (depth < 3) for (const c of await readDir(en)) await visit(c, depth + 1); }
      else plain.push(await entryFile(en));
    };
    for (const en of entries) await visit(en, 0);
    for (const en of products) await uploadSafeFolder(en);
    if (plain.length) await addFiles(plain);
  }
  // only what LULC Fetch reads: metadata, Sentinel-1 measurement + annotation, Sentinel-2's finest file per band
  function safeFilesNeeded(files) {
    const keep = files.filter(({ rel }) => /(^|\/)(manifest\.safe|MTD_MSIL(1C|2A)\.xml)$/i.test(rel) && rel.split("/").length === 1
      || /^annotation\/.*\.xml$/i.test(rel) || /^measurement\/.*\.tiff?$/i.test(rel));
    const best = {};
    for (const f of files) {
      const m = f.rel.match(/\/IMG_DATA\/.*_(B\d[\dA])(?:_(10|20|60)m)?\.jp2$/i);
      if (!m) continue;
      const res = +(m[2] || 0);
      if (!best[m[1]] || res < best[m[1]].res) best[m[1]] = { res, f };
    }
    return [...keep, ...Object.values(best).map((b) => b.f)];
  }
  async function uploadSafeFolder(dirEntry) {
    const folder = dirEntry.name;
    if (!SAFE_NAME_RE.test(folder)) return toast(`${folder}: not a Sentinel-1 GRD or Sentinel-2 L1C / L2A product`, true);
    const run = { title: `Adding ${folder.replace(/\.SAFE$/i, "")}`, progress: 0, message: "Reading the product folder…", started: Date.now(), cancel: () => { run.cancelled = true; } };
    runs[currentTool] = run; renderRunBar();
    try {
      status(`Reading ${folder}…`, true);
      const files = safeFilesNeeded(await walkFiles(dirEntry));
      if (!files.length) throw new Error(`${folder} has no Sentinel-1 / Sentinel-2 image files`);
      const withFile = await Promise.all(files.map(async (x) => ({ ...x, file: await entryFile(x.entry) })));
      const start = await api("/api/products/upload/start", { method: "POST", json: { folder, files: withFile.map((x) => ({ rel: x.rel, size: x.file.size })) } });
      const need = new Set(start.need), todo = withFile.filter((x) => need.has(x.rel));
      const total = todo.reduce((a, x) => a + x.file.size, 0) || 1;
      let done = 0;
      for (const x of todo) {
        if (run.cancelled) throw Object.assign(new Error("Cancelled"), { cancelled: true });
        run.message = `Copying into the data folder · ${fmt(done / 1e6, 0)} of ${fmt(total / 1e6, 0)} MB · ${x.rel.split("/").pop()}`;
        run.progress = done / total; renderRunBar();
        await api(`/api/products/upload/file?folder=${encodeURIComponent(folder)}&rel=${encodeURIComponent(x.rel)}`, { method: "PUT", body: x.file });
        done += x.file.size;
      }
      const p = await api("/api/products/upload/finish", { method: "POST", json: { folder } });
      delete runs[currentTool]; renderRunBar();
      toast(`${p.title} · ${p.date} added to the data folder${todo.length < withFile.length ? " (already partly there)" : ""}`);
      await openAddedProduct(p);
    } catch (e) {
      if (notCancelled(e)) toast(`${folder}: ${e.message}`, true);
    } finally {
      if (runs[currentTool] === run) { delete runs[currentTool]; renderRunBar(); }
      status("");
    }
  }
  async function uploadSafeZip(f) {
    const run = { title: `Adding ${f.name}`, progress: null, message: `Copying ${fmt(f.size / 1e6, 0)} MB into the data folder…`, started: Date.now(), cancel: () => xhr.abort() };
    const xhr = new XMLHttpRequest();
    runs[currentTool] = run; renderRunBar();
    try {
      const p = await new Promise((res, rej) => {
        xhr.open("POST", `/api/products/upload/zip?filename=${encodeURIComponent(f.name)}`);
        xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) { run.progress = ev.loaded / ev.total; run.message = `Copying into the data folder · ${fmt(ev.loaded / 1e6, 0)} of ${fmt(ev.total / 1e6, 0)} MB`; renderRunBar(); } };
        xhr.onload = () => { let b; try { b = JSON.parse(xhr.responseText); } catch { b = {}; } xhr.status < 300 ? res(b) : rej(new Error(b.detail || xhr.statusText)); };
        xhr.onerror = () => rej(new Error("Upload failed"));
        xhr.onabort = () => rej(Object.assign(new Error("Cancelled"), { cancelled: true }));
        xhr.send(f);
      });
      if (runs[currentTool] === run) { delete runs[currentTool]; renderRunBar(); }
      toast(`${p.title} · ${p.date} added to the data folder`);
      await openAddedProduct(p);
    } catch (e) {
      if (notCancelled(e)) toast(`${f.name}: ${e.message}`, true);
    } finally { if (runs[currentTool] === run) { delete runs[currentTool]; renderRunBar(); } }
  }
  // Sentinel-2 opens straight away; Sentinel-1 shows its options (pixel size, area) first
  async function openAddedProduct(p) {
    refreshHomeProducts();
    if (p.kind === "S1_GRD") return openSafeDialog(p.path);
    status(`Opening ${p.title}…`, true);
    const r = await api("/api/products/open", { method: "POST", json: { path: p.path } });
    await addRasterFromPath(r.path, { name: r.name, zoom: true });
    status(`${r.name} added to Contents`);
  }

  async function openWorkspace() {
    const [list, tables] = await Promise.all([api("/api/rasters"), api("/api/tables").catch(() => [])]);
    const groups = {};
    list.forEach((r) => (groups[r.group] ||= []).push(r));
    $("#ws-list").innerHTML = list.length ? Object.entries(groups).map(([g, rs]) => `<div class="ws-group">${esc(g)}</div>` + rs.map((r) => {
      const inMap = layers.some((l) => l.path === r.path && !l.derived);
      return `<div class="ws-row"><span>${esc(r.name)}<small>${esc(r.path)} · ${r.size_mb < 1 ? "<1" : Math.round(r.size_mb)} MB</small></span>
        <button class="btn small ${inMap ? "" : "primary"}" data-add="${esc(r.path)}">${inMap ? "Add again" : "Add"}</button></div>`;
    }).join("")).join("") : '<p class="hint">No GeoTIFFs in the workspace yet.</p>';
    if (tables.length) $("#ws-list").innerHTML += `<div class="ws-group">Tables</div>` + tables.map((t) => {
      const inC = dataItems.some((d) => d.path === t.path);
      return `<div class="ws-row"><span>${esc(t.name)}<small>${esc(t.path)}${t.rows != null ? ` · ${t.rows.toLocaleString()} rows` : ""} · ${t.size_mb < 1 ? "<1" : Math.round(t.size_mb)} MB</small></span>
        <button class="btn small ${inC ? "" : "primary"}" data-addt="${esc(t.path)}">${inC ? "Open" : "Add"}</button></div>`;
    }).join("");
    $$("#ws-list [data-addt]").forEach((b) => b.onclick = () => { addItem({ kind: "table", name: b.dataset.addt.split(/[\\/]/).pop(), path: b.dataset.addt }, { open: true }); b.textContent = "Added ✓"; });
    $$("#ws-list [data-add]").forEach((b) => b.onclick = () => busy(b, "Adding…", async () => {
      await addRasterFromPath(b.dataset.add);
      b.textContent = "Added ✓";
    }));
    $("#dlg-ws").showModal();
  }

  // ------------------------------------------------------------------ Copernicus .SAFE products
  // Sentinel-2 opens instantly (a VRT over the original JP2 bands); Sentinel-1 is calibrated and geocoded
  // in a background job whose GeoTIFF is added to Contents when it finishes.
  function safeCard(p, compact = false) {
    const s1 = p.kind === "S1_GRD";
    return `<div class="safe-card" data-path="${esc(p.path)}">
      <div><span class="safe-kind ${s1 ? "s1" : ""}">${s1 ? "SAR" : "OPTICAL"}</span><b>${esc(p.title)}</b>
        <small>${esc(p.date)} · ${esc(p.satellite)} · ${esc(p.detail)} · ${fmt(p.size_mb / 1000, 2)} GB${p.zipped ? " · zip" : ""}</small>
        ${compact ? "" : `<small class="mono">${esc(p.path)}${p.linked ? ` · opened from where it is <a href="#" data-unlink="${esc(p.path)}">remove from list</a>` : ""}</small>`}</div>
      ${s1 ? `<small>Converted to calibrated backscatter (σ⁰, dB) for ${esc(p.pols.join(" + "))}, geocoded to UTM. No terrain correction.</small>
        <div class="row">
          <label class="inline">Pixel size <select data-res><option value="20">20 m</option><option value="40" selected>40 m</option><option value="80">80 m</option></select></label>
          <label class="inline" title="${state.aoi ? "Only process your area of interest" : "Set an area of interest in Find imagery first"}"><input type="checkbox" data-clip ${state.aoi ? "" : "disabled"}> Clip to area of interest</label>
          <button class="btn small primary" data-open>Create backscatter layer</button>
        </div>`
      : `<small>All 12 bands at 10 m, read directly from the product (nothing is copied).</small>
        <div class="row"><button class="btn small primary" data-open>Open as layer</button></div>`}
    </div>`;
  }
  function wireSafeCards(root, products) {
    $$(".safe-card", root).forEach((card) => {
      const p = products.find((x) => x.path === card.dataset.path);
      $("[data-open]", card).onclick = (e) => busy(e.currentTarget, p.kind === "S1_GRD" ? "Starting…" : "Opening…", async () => {
        try {
          const body = { path: p.path };
          if (p.kind === "S1_GRD") {
            body.res = +$("[data-res]", card).value;
            if ($("[data-clip]", card).checked && state.aoi) body.aoi = state.aoi;
          }
          const r = await api("/api/products/open", { method: "POST", json: body });
          $("#dlg-safe").open && $("#dlg-safe").close();
          if (r.kind === "raster") {
            status(`Opening ${r.name}…`, true);
            await addRasterFromPath(r.path, { name: r.name });
            status(`${r.name} added to Contents`);
          } else {
            switchTool("jobs");
            addedJobs.add(r.job.id);
            prefs.set("addedJobs", [...addedJobs].slice(-200));
            trackJob(r.job, { tool: "jobs" }).then(async (done) => {
              for (const f of done.files.filter((x) => /\.tiff?$/i.test(x))) await addRasterFromPath(`downloads/${done.id}/${f}`, { name: done.title });
              toast(`Added “${done.title}” to Contents`);
            }).catch((e2) => { if (notCancelled(e2)) toast(e2, true); });
          }
        } catch (err) { toast(err, true); }
      });
    });
  }
  async function openSafeDialog(highlight = null) {
    const r = await api("/api/products");
    $("#safe-folder").textContent = r.folder;
    $("#safe-list").innerHTML = r.products.length ? r.products.map((p) => safeCard(p)).join("")
      : '<p class="hint">No products yet. Drag a <b>.SAFE</b> folder or <b>.SAFE.zip</b> onto the window, or click <b>Browse…</b>.</p>';
    wireSafeCards($("#safe-list"), r.products);
    $$("#safe-list [data-unlink]").forEach((b) => b.onclick = async (e) => {
      e.preventDefault();
      await api(`/api/products/link?path=${encodeURIComponent(b.dataset.unlink)}`, { method: "DELETE" }).catch((e) => toast(e, true));
      openSafeDialog(); refreshHomeProducts();
    });
    if (!$("#dlg-safe").open) $("#dlg-safe").showModal();
    if (highlight) {
      const card = $(`#safe-list .safe-card[data-path="${CSS.escape(highlight)}"]`);
      if (card) { card.classList.add("flash"); card.scrollIntoView({ block: "nearest" }); }
    }
  }
  $("#safe-browse").onclick = async () => {
    const path = await pickFolder({ title: "Open a Sentinel product (.SAFE folder or .SAFE.zip)", mode: "product", start: prefs.get("safe-last", "") });
    if (!path) return;
    try {
      const r = await api("/api/products/link", { method: "POST", json: { path } });
      prefs.set("safe-last", path.replace(/[\\/][^\\/]+$/, ""));
      refreshHomeProducts();
      if (r.products.length === 1 && r.products[0].kind !== "S1_GRD") { $("#dlg-safe").close(); await openAddedProduct(r.products[0]); }
      else openSafeDialog(r.products[0]?.path);
    } catch (e) { toast(e, true); }
  };
  async function refreshHomeProducts() {
    try {
      const r = await api("/api/products");
      $("#home-products").classList.toggle("hidden", !r.products.length);
      $("#home-products-list").innerHTML = r.products.map((p) => safeCard(p, true)).join("");
      wireSafeCards($("#home-products-list"), r.products);
    } catch {}
  }
