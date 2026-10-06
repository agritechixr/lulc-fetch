/* Insert ▸ Library ▸ Data library: GIS data kept as datasets on Hugging Face (every public dataset of the library accounts,
   e.g. ixrbhii/indian-shapefiles). Search a dataset's files, add any of them to the map: downloaded once into the project's
   downloads/library/. Server: /api/library/datasets, files, fetch, geojson · lulc_fetch/library.py. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "library", menu: "library", title: "Data library", icon: "book",
    subtitle: "Ready-made GIS data from Hugging Face: boundaries of India's states, districts, sub-districts, villages, constituencies and city wards, roads, railways… Search, then add any file to the map (downloaded once). Every dataset uploaded to the library account appears here",
    kinds: ["library"],
    panel: `
      <div class="card">
        <h2>Dataset ${tip("The public datasets of the library's Hugging Face account(s). Anything uploaded there (GeoJSON, shapefiles, KML, GeoTIFF, CSV) shows up here. No account is needed to use them.")}</h2>
        <select id="lib-ds"></select>
        <p class="hint" id="lib-ds-info"></p>
        <div class="row tight" style="gap:6px;flex-wrap:wrap;margin-top:6px">
          <button class="btn small" id="lib-refresh">Refresh</button>
          <button class="btn small ghost" id="lib-accounts">Other accounts…</button>
        </div>
      </div>
      <div class="card">
        <h2>Find</h2>
        <input type="search" id="lib-q" placeholder="e.g. karnataka districts, bengaluru, villages, railways" autocomplete="off">
        <div class="row tight lib-chips" id="lib-kinds"></div>
        <div class="grid2" id="lib-filters"></div>
        <p class="hint" id="lib-count"></p>
      </div>
      <div id="lib-list" class="lib-list"></div>`,

    setup(LF) {
      const { $, $$, esc, fmt, prefs, api, toast, runJob, notCancelled, addRasterFromPath, addVectorLayer, saveLayers, addItem, tablePoints } = LF;
      const st = { datasets: [], repo: prefs.get("lib-repo", ""), files: [], kind: "all", group: "", place: "", shown: 200, busy: new Set() };
      const KINDS = [["all", "All"], ["vector", "Maps"], ["raster", "Rasters"], ["table", "Tables"]];
      const mb = (b) => b >= 1e9 ? `${fmt(b / 1e9, 1)} GB` : b >= 1e6 ? `${fmt(b / 1e6, b < 1e7 ? 1 : 0)} MB` : `${Math.max(1, Math.round(b / 1e3))} kB`;

      async function open() {
        if (!st.datasets.length) await loadDatasets();
      }
      async function loadDatasets(refresh = false) {
        $("#lib-ds-info").textContent = "Loading the library…";
        try {
          const r = await api(`/api/library/datasets${refresh ? "?refresh=true" : ""}`);
          st.datasets = r.datasets; st.accounts = r.accounts;
        } catch (e) { $("#lib-ds-info").innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`; return; }
        const sel = $("#lib-ds");
        sel.innerHTML = st.datasets.length ? st.datasets.map((d) => `<option value="${esc(d.repo)}">${esc(d.title)} · ${esc(d.repo)}</option>`).join("")
          : `<option value="">No public datasets yet on ${esc(st.accounts.join(", "))}</option>`;
        if (st.datasets.some((d) => d.repo === st.repo)) sel.value = st.repo;
        else { const geo = st.datasets.find((d) => /shape|geo|boundar|gis|map/i.test(d.repo)); if (geo) sel.value = geo.repo; }
        await loadFiles(refresh);
      }
      async function loadFiles(refresh = false) {
        st.repo = $("#lib-ds").value; prefs.set("lib-repo", st.repo);
        const d = st.datasets.find((x) => x.repo === st.repo);
        $("#lib-ds-info").innerHTML = d ? `${d.description ? esc(d.description.slice(0, 220)) + " " : ""}${d.license ? `Licence: ${esc(d.license)}. ` : ""}<a href="${esc(d.url)}" target="_blank" rel="noopener">Open on Hugging Face</a>` : "";
        st.files = []; renderList();
        if (!st.repo) return;
        $("#lib-count").textContent = "Loading the files…";
        st.loaded = false;
        try { st.files = (await api(`/api/library/files?repo=${encodeURIComponent(st.repo)}${refresh ? "&refresh=true" : ""}`)).files.filter((f) => f.kind !== "other"); st.loaded = true; }
        catch (e) { $("#lib-count").innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`; return; }
        // filters from the catalog (or the folders): group (e.g. India / States / Metropolitan cities) and place (a state)
        const groups = [...new Set(st.files.map((f) => f.group || f.path.split("/").slice(0, -1)[0] || "").filter(Boolean))].sort();
        st.group = groups.includes(st.group) ? st.group : "";
        $("#lib-filters").innerHTML = groups.length ? `<label>Group<select id="lib-group"><option value="">All</option>${groups.map((g) => `<option>${esc(g)}</option>`).join("")}</select></label>
          <label>Place<select id="lib-place"></select></label>` : "";
        if (groups.length) { $("#lib-group").value = st.group; $("#lib-group").onchange = () => { st.group = $("#lib-group").value; st.place = ""; places(); renderList(); }; places(); }
        renderList();
      }
      const groupOf = (f) => f.group || f.path.split("/").slice(0, -1)[0] || "";
      const placeOf = (f) => f.place || (f.path.split("/").length > 2 ? f.path.split("/")[1] : "");
      function places() {
        const ps = [...new Set(st.files.filter((f) => !st.group || groupOf(f).toLowerCase() === st.group.toLowerCase()).map(placeOf).filter(Boolean))].sort();
        const sel = $("#lib-place");
        sel.innerHTML = `<option value="">All</option>` + ps.map((p) => `<option>${esc(p)}</option>`).join("");
        sel.value = ps.includes(st.place) ? st.place : "";
        sel.disabled = !ps.length;
        sel.onchange = () => { st.place = sel.value; renderList(); };
      }
      $("#lib-kinds").innerHTML = KINDS.map(([k, t]) => `<button class="chip ${k === st.kind ? "active" : ""}" data-lk="${k}">${t}</button>`).join("");
      $$("#lib-kinds [data-lk]").forEach((b) => b.onclick = () => { st.kind = b.dataset.lk; $$("#lib-kinds .chip").forEach((x) => x.classList.toggle("active", x === b)); renderList(); });
      let deb = 0;
      $("#lib-q").oninput = () => { clearTimeout(deb); deb = setTimeout(() => { st.shown = 200; renderList(); }, 200); };

      // every word must appear in the file's path, title, type or place (so "karnataka districts" finds KARNATAKA_DISTRICTS)
      function matches() {
        const words = $("#lib-q").value.toLowerCase().split(/\s+/).filter(Boolean).map((w) => w.replace(/s$/, ""));
        const hits = st.files.filter((f) => (st.kind === "all" || f.kind === st.kind)
          && (!st.group || groupOf(f).toLowerCase() === st.group.toLowerCase()) && (!st.place || placeOf(f) === st.place)
          && words.every((w) => `${f.path} ${f.title || ""} ${f.type || ""}`.toLowerCase().replace(/_/g, " ").includes(w)));
        if (!words.length) return hits;
        // best first: the kind of layer named exactly ("districts" → Districts before District HQs), then whole words in the title
        const sing = (s) => s.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").trim().split(/\s+/).map((x) => x.replace(/s$/, ""));
        const score = (f) => words.reduce((n, w) => n + (sing(f.type || "").join(" ") === w ? 4 : sing(f.type || "").includes(w) ? 2 : 0)
          + (sing(f.title || f.path.split("/").pop()).includes(w) ? 1 : 0), 0);
        return hits.map((f) => [score(f), f]).sort((a, b) => b[0] - a[0]).map(([, f]) => f);
      }
      function renderList() {
        const list = $("#lib-list"), m = matches();
        $("#lib-count").textContent = st.files.length ? `${m.length.toLocaleString()} of ${st.files.length.toLocaleString()} files` : "";
        if (!m.length) {
          list.innerHTML = st.files.length ? `<p class="hint">Nothing matches: try fewer words.</p>`
            : st.repo && st.loaded ? `<p class="hint">This dataset has no maps, rasters or tables (GeoJSON, shapefile, KML, GeoTIFF, CSV …): choose another one.</p>` : "";
          return;
        }
        let last = "";
        list.innerHTML = m.slice(0, st.shown).map((f) => {
          const folder = f.path.split("/").slice(0, -1).join(" / ");
          const head = folder !== last ? `<div class="lib-folder">${esc(folder || "(top)")}</div>` : "";
          last = folder;
          const facts = [f.type, f.features != null ? `${f.features.toLocaleString()} ${f.geometry?.length === 1 ? f.geometry[0].toLowerCase() + (f.features === 1 ? "" : "s") : "features"}` : "",
                         f.rows != null ? `${f.rows.toLocaleString()} rows` : "", f.bands != null ? `${f.bands} bands` : "", mb(f.size)].filter(Boolean).join(" · ");
          return `${head}<div class="lib-row" data-path="${esc(f.path)}">
              <span class="lib-name" title="${esc(f.path)}"><b>${esc(f.title || f.path.split("/").pop())}</b><small>${esc(facts)}${f.fields?.length ? ` · fields: ${esc(f.fields.slice(0, 6).join(", "))}${f.fields.length > 6 ? "…" : ""}` : ""}</small></span>
              <button class="btn small ${f.local ? "" : "primary"}" data-add ${st.busy.has(f.path) ? "disabled" : ""}>${st.busy.has(f.path) ? "Adding…" : f.local ? "✓ Add" : "Add"}</button></div>`;
        }).join("") + (m.length > st.shown ? `<button class="btn" style="width:100%;margin-top:6px" data-more>Show ${Math.min(200, m.length - st.shown)} more</button>` : "");
        $$("#lib-list [data-add]").forEach((b) => b.onclick = () => add(st.files.find((f) => f.path === b.closest(".lib-row").dataset.path)));
        $("#lib-list [data-more]")?.addEventListener("click", () => { st.shown += 200; renderList(); });
      }

      // download (once) and put on the map: GeoJSON / shapefile / KML as a vector layer, GeoTIFF as a raster, CSV as a table
      async function add(f) {
        if (!f || st.busy.has(f.path)) return;
        if (f.size > 60e6 && !confirm(`${f.title || f.path} is ${mb(f.size)}${f.features ? ` (${f.features.toLocaleString()} features)` : ""}. Downloading takes a while and the map may be slow with so many shapes. Add it anyway?`)) return;
        st.busy.add(f.path); renderList();
        try {
          const r = await runJob("/api/library/fetch", { repo: st.repo, path: f.path, sha256: f.sha256 || null, size: f.size || null },
                                 { tool: "library", title: `Library · ${f.title || f.path.split("/").pop()}` });
          const name = f.title || r.name;
          if (r.kind === "raster") await addRasterFromPath(r.path, { name });
          // a table with latitude / longitude also goes on the map, unless the catalog says it is a time series (one row per place and time)
          else if (r.kind === "table") { const it = addItem({ kind: "table", name: r.file, path: r.path }, { open: true }); if (r.lonlat && f.points !== false) await tablePoints(it, "", r.lonlat); }
          else {
            const fc = await api(`/api/library/geojson?path=${encodeURIComponent(r.path)}`);
            const gj = typeof fc === "string" ? JSON.parse(fc) : fc;
            addVectorLayer({ type: "FeatureCollection", features: gj.features || [] }, name, { source: r.source });
            saveLayers();
            toast(`${name}: ${(gj.features || []).length.toLocaleString()} features added`);
          }
          f.local = true;
        } catch (e) { if (notCancelled(e)) toast(`${f.title || f.path}: ${e.message}`, true); }
        finally { st.busy.delete(f.path); renderList(); }
      }

      $("#lib-ds").onchange = () => { st.group = ""; st.place = ""; loadFiles(); };
      $("#lib-refresh").onclick = () => loadDatasets(true);
      $("#lib-accounts").onclick = async () => {
        const cur = (st.accounts || []).filter((a) => a !== "ixrbhii").join(", ");
        const v = prompt("More Hugging Face accounts or organisations whose public datasets the library lists (comma-separated). ixrbhii is always included.", cur);
        if (v === null) return;
        try { await api("/api/library/accounts", { method: "POST", json: { accounts: v.split(",").map((x) => x.trim()).filter(Boolean) } }); await loadDatasets(true); }
        catch (e) { toast(e.message, true); }
      };

      return { open };
    },
  });
})();
