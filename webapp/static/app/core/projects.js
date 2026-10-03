  // Projects: one folder for layers, results and settings.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ projects: one folder for layers, results and settings
  const proj = { info: null, saveTimer: 0, loading: false };
  const inProject = () => !!proj.info?.project;
  function projectState() {
    const c = map.getCenter();
    return { layers: layers.filter((l) => l.type !== "image").map(({ leaflet, image, busy, error, legend, _original, ...rest }) =>
               _original !== undefined ? { ...rest, geojson: { ...rest.geojson, features: JSON.parse(_original) } } : rest),
             items: dataItems, view: { center: [c.lat, c.lng], zoom: map.getZoom() }, basemap: prefs.get("basemap", "streets") };
  }
  function scheduleProjectSave() {
    if (!inProject() || proj.loading) return;
    clearTimeout(proj.saveTimer);
    $("#project-saved").textContent = "•";
    $("#project-saved").title = "Unsaved changes (saving…)";
    proj.saveTimer = setTimeout(async () => {
      try {
        const r = await api("/api/project/state", { method: "PUT", json: { state: projectState() } });
        $("#project-saved").textContent = "✓";
        $("#project-saved").title = `Saved ${r.saved}`;
      } catch (e) { $("#project-saved").textContent = "!"; $("#project-saved").title = "Couldn't save the project: " + e.message; }
    }, 900);
  }
  function renderProjectChip() {
    const p = proj.info?.project;
    $("#project-name").textContent = p ? p.name : "Temporary workspace";
    $("#btn-project").classList.toggle("temp", !p);
    $("#btn-project").title = p ? `Project folder: ${p.folder}\nEverything you make is saved in this folder.` : `No project open: results are kept in the app's working folder (${proj.info?.workspace || ""}) until you clean them up.`;
    $("#btn-project .pc-ic").innerHTML = p ? "📁" : "🗂";
    if (!p) $("#project-saved").textContent = "";
    $("#mi-project-close").disabled = !p;
    document.title = p ? `${p.name} · LULC Fetch` : "LULC Fetch";
  }
  function clearContents() {
    [...layers].forEach((l) => removeLayer(l.id, { silent: true }));
    dataItems.splice(0);
    [...vw.tabs].forEach((t) => closeTab(t.key, true));
    selectedId = null;
    renderContents(); renderItems();
  }
  // apply a project's saved state (or the temporary workspace's browser-stored state)
  function applyState(state) {
    proj.loading = true;
    try {
      clearContents();
      if (state?.view?.center) map.setView(state.view.center, state.view.zoom ?? map.getZoom());
      if (state?.basemap) setBasemap(state.basemap);
      restoreLayers(state ? state.layers || [] : undefined);
      restoreItems(state ? state.items || [] : undefined);
    } finally { proj.loading = false; }
  }
  async function afterSwitch(info, label) {
    proj.info = info;
    renderProjectChip();
    applyState(info.project ? (info.state || {}) : null);
    refreshJobs();
    if (currentTool !== "home") switchTool(currentTool);
    toast(label);
  }
  async function projectCreate(name, folder) {
    const info = await api("/api/project/new", { method: "POST", json: { name, folder } });
    prefs.set("pj-parent", folder);
    await afterSwitch(info, `Project “${info.project.name}” created in ${info.project.folder}`);
  }
  async function projectOpen(folder) {
    const info = await api("/api/project/open", { method: "POST", json: { folder } });
    await afterSwitch(info, `Opened project “${info.project.name}”`);
  }
  async function projectClose() {
    if (!inProject()) return;
    clearTimeout(proj.saveTimer);
    await api("/api/project/state", { method: "PUT", json: { state: projectState() } }).catch(() => {});
    const info = await api("/api/project/close", { method: "POST" });
    await afterSwitch(info, "Project closed. Back to the temporary workspace.");
  }
  async function showProjectDialog() {
    try { proj.info = await api("/api/project"); } catch {}
    $("#pj-show").checked = prefs.get("pj-show", true);
    if (!$("#pj-folder").value) $("#pj-folder").value = prefs.get("pj-parent", "");
    $("#pj-error").classList.add("hidden");
    const rec = proj.info?.recent || [];
    $("#pj-recent").innerHTML = rec.length ? rec.map((r) => `<div class="pj-item ${r.exists ? "" : "missing"}" data-f="${esc(r.folder)}" title="${esc(r.folder)}">
        <span class="pj-ic">📁</span><span class="pj-t"><b>${esc(r.name)}</b><small>${esc(r.folder)}</small><small>${r.exists ? `opened ${esc(r.opened || "")}` : "folder not found"}</small></span>
        <button class="vtab-x" data-forget title="Remove from the list">×</button></div>`).join("")
      : '<p class="hint">No recent projects yet.</p>';
    $$("#pj-recent .pj-item").forEach((el) => {
      el.onclick = async (e) => {
        if (e.target.closest("[data-forget]")) { e.stopPropagation(); await api(`/api/project/recent?folder=${encodeURIComponent(el.dataset.f)}`, { method: "DELETE" }); el.remove(); return; }
        if (el.classList.contains("missing")) return toast("That project folder no longer exists", true);
        try { await projectOpen(el.dataset.f); $("#dlg-project").close(); } catch (err) { toast(err.message, true); }
      };
    });
    updatePjPreview();
    if (!$("#dlg-project").open) $("#dlg-project").showModal();
    setTimeout(() => $("#pj-name").focus(), 50);
  }
  function updatePjPreview() {
    const n = $("#pj-name").value.trim(), f = $("#pj-folder").value.trim();
    $("#pj-preview").innerHTML = n && f ? `Will be created as <code>${esc(f.replace(/\/+$/, ""))}/${esc(n.replace(/[<>:"/\\|?*]+/g, "_"))}</code>` : "A new folder with the project's name is created inside the location.";
  }
  $("#pj-name").oninput = $("#pj-folder").oninput = updatePjPreview;
  $("#pj-browse").onclick = async () => { const f = await pickFolder({ title: "Where should the project folder be created?", start: $("#pj-folder").value, okLabel: "Use this location" }); if (f) { $("#pj-folder").value = f; updatePjPreview(); } };
  $("#pj-create").onclick = (e) => busy(e.currentTarget, "Creating…", async () => {
    const name = $("#pj-name").value.trim(), folder = $("#pj-folder").value.trim();
    const err = $("#pj-error"); err.classList.add("hidden");
    if (!name) { err.textContent = "Give the project a name"; err.classList.remove("hidden"); return; }
    if (!folder) { err.textContent = "Choose where to create it (Browse…)"; err.classList.remove("hidden"); return; }
    try { await projectCreate(name, folder); $("#dlg-project").close(); }
    catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
  });
  $("#pj-name").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#pj-create").click(); } });
  $("#pj-open").onclick = async () => {
    const f = await pickFolder({ title: "Open a project folder", mode: "project" });
    if (!f) return;
    try { await projectOpen(f); $("#dlg-project").close(); } catch (e) { toast(e.message, true); }
  };
  $("#pj-temp").onclick = () => $("#dlg-project").close();
  $("#pj-show").onchange = (e) => prefs.set("pj-show", e.target.checked);
  $("#btn-project").onclick = (e) => {
    e.stopPropagation();   // the page-wide "click outside closes menus" handler would close it at once
    if (!inProject()) return showProjectDialog();
    const r = e.currentTarget.getBoundingClientRect(), p = proj.info.project;
    showMenu(p.folder, [
      ["Show project folder", () => api("/api/project/reveal", { method: "POST", json: {} }).catch((x) => toast(x.message, true))],
      ["Save now", () => { scheduleProjectSave(); }],
      "-",
      ["Open another project…", () => showProjectDialog()],
      ["Close project", () => projectClose().catch((x) => toast(x.message, true))],
      "-",
      ["Clean up working files…", () => openCacheDialog()],
    ], r.left, r.bottom + 4);
  };
  async function initProject() {
    try { proj.info = await api("/api/project"); } catch { proj.info = null; }
    renderProjectChip();
    if (inProject()) applyState(proj.info.state || {});
    else {
      applyState(null);
      let shown = false;
      try { shown = sessionStorage.getItem("pj-asked") === "1"; sessionStorage.setItem("pj-asked", "1"); } catch {}
      if (prefs.get("pj-show", true) && !shown) showProjectDialog();
    }
  }
  map.on("moveend", () => scheduleProjectSave());
