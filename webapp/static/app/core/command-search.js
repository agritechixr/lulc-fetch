  // Save / Save as (Ctrl+S / Ctrl+Shift+S, the quick access bar) and the command search (Ctrl+K or Ctrl+F, the box in
  // the menu bar): type "ndvi", "export" or "3d map" and run it. It finds every ribbon command, every tool, the maps,
  // the layers, the bookmarks and, in a 3D map, the standard views; the ones used last come first.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ save / save as
  async function saveNow() {
    if (!inProject()) return openSaveAs();   // the temporary workspace is saved by making it a project
    clearTimeout(proj.saveTimer);
    try {
      const r = await api("/api/project/state", { method: "PUT", json: { state: projectState() } });
      $("#project-saved").textContent = "✓";
      $("#project-saved").title = `Saved ${r.saved}`;
      toast(`Saved “${proj.info.project.name}”`);
    } catch (e) { toast(`Couldn't save: ${e.message}`, true); }
  }
  function openSaveAs() {
    const p = proj.info?.project;
    $("#sa-name").value = p ? `${p.name} copy` : "";
    if (!$("#sa-folder").value) $("#sa-folder").value = prefs.get("pj-parent", "");
    $("#sa-error").classList.add("hidden");
    saPreview();
    $("#dlg-saveas").showModal();
    setTimeout(() => { $("#sa-name").focus(); $("#sa-name").select(); }, 30);
  }
  function saPreview() {
    const n = $("#sa-name").value.trim(), f = $("#sa-folder").value.trim();
    $("#sa-preview").innerHTML = n && f ? `Saved as <code>${esc(f.replace(/[\\/]+$/, ""))}/${esc(n.replace(/[<>:"/\\|?*]+/g, "_"))}</code>` : "";
  }
  $("#sa-name").oninput = $("#sa-folder").oninput = saPreview;
  $("#sa-name").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#sa-save").click(); } });
  $("#sa-browse").onclick = async () => {
    const f = await pickFolder({ title: "Where should the project be saved?", start: $("#sa-folder").value, okLabel: "Save here" });
    if (f) { $("#sa-folder").value = f; saPreview(); }
  };
  $("#sa-save").onclick = (e) => busy(e.currentTarget, "Saving…", async () => {
    const name = $("#sa-name").value.trim(), folder = $("#sa-folder").value.trim(), err = $("#sa-error");
    err.classList.add("hidden");
    if (!name || !folder) { err.textContent = name ? "Choose where to save it (Browse…)" : "Give the project a name"; err.classList.remove("hidden"); return; }
    try {
      const info = await api("/api/project/save-as", { method: "POST", json: { name, folder, state: projectState() } });
      prefs.set("pj-parent", folder);
      $("#dlg-saveas").close();
      await afterSwitch(info, `Saved as “${info.project.name}” in ${info.project.folder}${info.copied ? ` (${info.copied} file${info.copied === 1 ? "" : "s"} copied)` : ""}`);
      if (info.missing?.length) toast(`${info.missing.length} file(s) weren't found and couldn't be copied: ${info.missing.slice(0, 3).join(", ")}`, true);
    } catch (x) { err.textContent = x.message; err.classList.remove("hidden"); }
  });

  // ------------------------------------------------------------------ command search
  const cs = { items: [], shown: [], sel: 0, recent: prefs.get("cmd-recent", []) };
  const TOOL_PLACE = { agri: "Analysis ▸ Agri", embed: "Analysis ▸ Embeddings", forecast: "Analysis ▸ Forecast", library: "Insert ▸ Library" };
  const shortcutOf = (t) => (String(t || "").match(/\((Ctrl[^)]*|Del)\)/) || [])[1] || "";
  function commandList() {
    const out = [], seen = new Set();
    const add = (x) => { if (!seen.has(x.key)) { seen.add(x.key); out.push(x); } };
    // the quick access bar
    [["save", "Save", "Ctrl+S", "save"], ["save-as", "Save as a new project…", "Ctrl+Shift+S", "saveas"], ["undo", "Undo", "Ctrl+Z", null], ["redo", "Redo", "Ctrl+Y", null]]
      .forEach(([c, label, short, ic]) => add({ key: `cmd:${c}`, label: c === "undo" && undoHist.undo.length ? `Undo: ${undoHist.undo.at(-1).label}` : c === "redo" && undoHist.redo.length ? `Redo: ${undoHist.redo.at(-1).label}` : label,
        where: "Quick access", short, icon: ic ? svg(ic) : $(`#qa-${c}`)?.innerHTML || "", run: () => runCmd(c), disabled: (c === "undo" && !undoHist.undo.length) || (c === "redo" && !undoHist.redo.length) }));
    // every ribbon command, with its tab and group
    $$("#menus .menu").forEach((m) => {
      const tab = tabName(m);
      $$("[data-cmd]", m).forEach((b) => {
        const label = b.textContent.replace(/\s+/g, " ").trim();
        if (!label) return;
        const group = b.closest(".rb-group")?.querySelector(".rb-cap")?.textContent || "";
        add({ key: `cmd:${b.dataset.cmd}`, label, where: tab + (group && group !== label ? ` ▸ ${group}` : ""), kw: b.title, short: shortcutOf(b.title) || SHORTCUTS[b.dataset.cmd] || "",
              icon: $(".ic", b)?.innerHTML || "", disabled: b.disabled, run: () => runCmd(b.dataset.cmd) });
      });
    });
    // tools (their explanations are searched too: "ndvi" finds Index analysis)
    TOOLS.forEach((t) => add({ key: `tool:${t.id}`, label: t.title, where: TOOL_PLACE[t.menu] || "Analysis ▸ Tools", kw: t.subtitle, icon: svg(t.icon), run: () => switchTool(t.id) }));
    (state.config?.indices || []).forEach((n) => add({ key: `index:${n}`, label: `${n} (index)`, where: "Analysis ▸ Tools ▸ Index analysis", icon: svg("analyze"), run: () => switchTool("analyze") }));
    add({ key: "about", label: "About LULC Fetch (version)", where: "Help", kw: "version environment", icon: svg("info"), run: () => openAbout() });
    // what is open: maps, layers, bookmarks, 3D views
    docs.list.forEach((d) => add({ key: `map:${d.id}`, label: `Open map “${d.name}”`, where: `Maps · ${d.kind === "3d" ? "3D" : "2D"}`, icon: MAP_IC[d.kind], run: () => switchMap(d.id) }));
    layers.forEach((l) => add({ key: `layer:${l.id}`, label: `Zoom to “${l.name}”`, where: "Contents", icon: svg(l.type === "vector" ? "vector" : "raster"), run: () => { selectLayer(l.id); zoomTo(l); } }));
    bookmarks().forEach((bm) => add({ key: `bm:${bm.id}`, label: `Go to bookmark “${bm.name}”`, where: "Insert ▸ Bookmarks", icon: svg("bookmark"), run: () => goToBookmark(bm.id) }));
    if (is3D()) Object.keys(VIEWS3D).forEach((n) => add({ key: `view3d:${n}`, label: `3D view: ${n}`, where: "3D map", icon: svg("map3d"), run: () => view3d(n) }));
    return out;
  }
  // every word typed must appear; the name counts more than where it is or its explanation; recent ones first
  function scoreOf(x, words, q) {
    const label = x.label.toLowerCase(), hay = `${label} ${String(x.where).toLowerCase()} ${String(x.kw || "").toLowerCase()}`;
    if (!words.every((w) => hay.includes(w))) return -1;
    let s = 0;
    if (label.startsWith(q)) s += 100; else if (label.includes(q)) s += 60;
    words.forEach((w) => { if (label.includes(w)) s += 15; if (new RegExp(`\\b${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(label)) s += 10; });
    const r = cs.recent.indexOf(x.key);
    if (r >= 0) s += 30 - r * 3;
    if (x.disabled) s -= 40;
    return s - label.length / 50;
  }
  function renderCmdResults() {
    const q = $("#cmd-input").value.trim().toLowerCase(), words = q.split(/\s+/).filter(Boolean), box = $("#cmd-results");
    cs.items = commandList();
    cs.shown = q
      ? cs.items.map((x) => [x, scoreOf(x, words, q)]).filter(([, s]) => s >= 0).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([x]) => x)
      : [...cs.recent.map((k) => cs.items.find((x) => x.key === k)).filter(Boolean).slice(0, 6),
         ...["cmd:save", "cmd:new-map:3d", "cmd:add-data", "cmd:measure:distance", "tool:analyze", "cmd:bookmark-view"].map((k) => cs.items.find((x) => x.key === k))]
        .filter((x, i, a) => x && a.indexOf(x) === i).slice(0, 10);
    cs.sel = Math.min(cs.sel, Math.max(0, cs.shown.length - 1));
    box.innerHTML = (q ? "" : `<div class="cr-head">${cs.recent.length ? "Recent and suggested" : "Suggested"}</div>`) +
      (cs.shown.length ? cs.shown.map((x, i) => `<div class="cr-item ${i === cs.sel ? "on" : ""} ${x.disabled ? "off" : ""}" data-i="${i}" role="option" aria-selected="${i === cs.sel}">
          <span class="cr-ic">${x.icon || ""}</span><span class="cr-t"><b>${esc(x.label)}</b><small>${esc(x.where)}${x.disabled ? " · not available now" : ""}</small></span>${x.short ? `<kbd>${esc(x.short)}</kbd>` : ""}</div>`).join("")
        : `<div class="cr-none">Nothing matches “${esc(q)}”</div>`);
    box.classList.remove("hidden");
    $$(".cr-item", box).forEach((el) => {
      el.onmousedown = (e) => e.preventDefault();   // keep the focus in the box
      el.onclick = () => runResult(+el.dataset.i);
      el.onmousemove = () => { if (cs.sel !== +el.dataset.i) { cs.sel = +el.dataset.i; $$(".cr-item", box).forEach((x) => x.classList.toggle("on", x === el)); } };
    });
  }
  function runResult(i) {
    const x = cs.shown[i];
    if (!x) return;
    if (x.disabled) return toast(`“${x.label}” isn't available now`, true);
    cs.recent = [x.key, ...cs.recent.filter((k) => k !== x.key)].slice(0, 8);
    prefs.set("cmd-recent", cs.recent);
    closeSearch();
    x.run();
  }
  function closeSearch() { $("#cmd-results").classList.add("hidden"); $("#cmd-input").value = ""; $("#cmd-input").blur(); cs.sel = 0; }
  function openSearch() { $("#cmd-input").focus(); $("#cmd-input").select(); renderCmdResults(); }
  $("#cmd-input").addEventListener("focus", renderCmdResults);
  $("#cmd-input").addEventListener("input", () => { cs.sel = 0; renderCmdResults(); });
  $("#cmd-input").addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== $("#cmd-input")) $("#cmd-results").classList.add("hidden"); }, 120));
  $("#cmd-input").addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      cs.sel = (cs.sel + (e.key === "ArrowDown" ? 1 : -1) + cs.shown.length) % Math.max(1, cs.shown.length);
      $$("#cmd-results .cr-item").forEach((el, i) => { el.classList.toggle("on", i === cs.sel); if (i === cs.sel) el.scrollIntoView({ block: "nearest" }); });
    } else if (e.key === "Enter") { e.preventDefault(); runResult(cs.sel); }
    else if (e.key === "Escape") { e.preventDefault(); closeSearch(); }
  });
