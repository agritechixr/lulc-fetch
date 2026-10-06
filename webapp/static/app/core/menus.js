  // Menu bar behaviour and commands.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ menus & commands
  // A ribbon, as in Word: clicking a tab shows its commands in a band under the tabs. Pinned (the default), one tab is always
  // shown and the map sits below it; collapsed (double-click a tab, the ⌃ button or Ctrl+F1), a tab's band opens over the map
  // on click and closes after a command.
  let openMenu = null;
  const ribbon = { pinned: prefs.get("ribbon-pinned", true) };
  const tabName = (m) => $(".menu-btn", m).textContent;
  function toggleMenu(m) {
    if (!m && ribbon.pinned) return;   // the pinned ribbon always shows a tab
    openMenu?.classList.remove("open");
    openMenu = m;
    document.body.classList.toggle("ribbon-open", !!m);
    if (m) {
      m.classList.add("open"); prefs.set("ribbon-tab", tabName(m)); syncMenuChecks();
      if ($("#history-menu", m)) refreshHistoryMenu();
      if ($("#bookmarks-menu", m)) renderBookmarks();
    }
  }
  function setRibbonPinned(on) {
    ribbon.pinned = on;
    prefs.set("ribbon-pinned", on);
    document.body.classList.toggle("ribbon-pinned", on);
    $("#rb-pin").title = on ? "Hide the ribbon (Ctrl+F1)" : "Always show the ribbon (Ctrl+F1)";
    $("#rb-pin").setAttribute("aria-label", $("#rb-pin").title);
    if (on) toggleMenu(openMenu || $$("#menus .menu").find((m) => tabName(m) === prefs.get("ribbon-tab", "Analysis")) || $("#analysis-menu"));
    else toggleMenu(null);
    setTimeout(() => map.invalidateSize(), 30);
  }
  // the mouse wheel scrolls a ribbon that is wider than the window sideways
  $$("#menus .menu-pop").forEach((pop) => pop.addEventListener("wheel", (e) => {
    if (pop.scrollWidth > pop.clientWidth && Math.abs(e.deltaY) > Math.abs(e.deltaX)) { pop.scrollLeft += e.deltaY; e.preventDefault(); }
  }, { passive: false }));
  $$("#menus .menu").forEach((m) => {
    const btn = $(".menu-btn", m);
    btn.onclick = (e) => { e.stopPropagation(); toggleMenu(m === openMenu && !ribbon.pinned ? null : m); };
    btn.ondblclick = (e) => { e.stopPropagation(); setRibbonPinned(!ribbon.pinned); if (ribbon.pinned) toggleMenu(m); };
    btn.onmouseenter = () => { if (!ribbon.pinned && openMenu && openMenu !== m) toggleMenu(m); };
  });
  function syncMenuChecks() {
    $("#mi-contents").classList.toggle("on", !document.body.classList.contains("no-contents"));
    $("#mi-tools").classList.toggle("on", !document.body.classList.contains("no-tools"));
    $("#mi-viewer").classList.toggle("on", document.body.classList.contains("viewer-open"));
    $("#mi-ribbon").classList.toggle("on", ribbon.pinned);
    if ($("#mi-project-close")) $("#mi-project-close").disabled = !inProject();
    $("#mi-labels").classList.toggle("on", map.hasLayer(placeLabels));
    const bm = prefs.get("basemap", "streets"), th = prefs.get("theme", "auto");
    $$('[data-group="basemap"]').forEach((b) => b.classList.toggle("on", b.dataset.cmd === "basemap:" + bm));
    $$('[data-group="theme"]').forEach((b) => b.classList.toggle("on", b.dataset.cmd === "theme:" + th));
    const sel = selectedLayer();
    $$('[data-cmd="export-layer"], [data-cmd="remove-layer"], [data-cmd="layer-props"]').forEach((b) => b.disabled = !sel);
    $('[data-cmd="clear-layers"]').disabled = !layers.length;
    $("#mi-map-close").disabled = docs.list.length < 2;
    $("#mi-layer-copy").disabled = !sel;
    $("#mi-layer-copy-all").disabled = !layers.length;
    $("#mi-layer-paste").disabled = !docs.clip?.length;
  }

  function setPane(which, show) {
    const cls = which === "contents" ? "no-contents" : "no-tools";
    document.body.classList.toggle(cls, !show);
    prefs.set(which, show);
    setTimeout(() => map.invalidateSize(), 30);
  }

  function applyTheme(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    prefs.set("theme", t);
    map3dChanged();
  }

  function runCmd(cmd) {
    const [name, arg] = cmd.split(":");
    switch (name) {
      case "add-data": $("#add-file").click(); break;
      case "add-workspace": openWorkspace(); break;
      case "open-safe": openSafeDialog(); break;
      case "export-layer": openExport(selectedLayer()); break;
      case "layer-props": openProps(selectedLayer()); break;
      case "remove-layer": { const l = selectedLayer(); if (l) removeLayer(l.id); break; }
      case "clear-layers": if (layers.length && confirm("Remove all layers from Contents? Files on disk are kept.")) [...layers].forEach((l) => removeLayer(l.id)); break;
      case "credentials": $("#btn-creds").click(); break;
      case "toggle-contents": setPane("contents", document.body.classList.contains("no-contents")); break;
      case "toggle-tools": setPane("tools", document.body.classList.contains("no-tools")); break;
      case "toggle-viewer": setViewer(!viewerOpen()); break;
      case "project-new": showProjectDialog(); break;
      case "project-open": pickFolder({ title: "Open a project folder", mode: "project" }).then((f) => f && projectOpen(f).catch((e) => toast(e.message, true))); break;
      case "project-close": projectClose().catch((e) => toast(e.message, true)); break;
      case "project-reveal": api("/api/project/reveal", { method: "POST", json: {} }).catch((e) => toast(e.message, true)); break;
      case "clean-cache": openCacheDialog().catch((e) => toast(e.message, true)); break;
      case "reset-layout": resetLayout(); break;
      case "basemap": setBasemap(arg); map3dChanged(); break;
      case "toggle-labels": setLabels(!map.hasLayer(placeLabels)); break;
      case "zoom-all": zoomAll(); break;
      case "toggle-ribbon": setRibbonPinned(!ribbon.pinned); break;
      case "new-map": newMap(arg === "3d" ? "3d" : "2d"); break;
      case "map-rename": editMapName(docs.active); break;
      case "map-duplicate": duplicateMap(docs.active); break;
      case "map-close": closeMap(docs.active); break;
      case "layer-copy": copyLayers([selectedLayer()].filter(Boolean)); break;
      case "layer-copy-all": copyLayers(layers); break;
      case "layer-paste": pasteLayers(); break;
      case "bookmark-view": bookmarkView(); break;
      case "theme": applyTheme(arg); break;
      case "guide": showHelp("guide"); break;
      case "shortcuts": showHelp("shortcuts"); break;
      case "about": openAbout(); break;
      case "error-log": window.open("/api/errors/file", "_blank", "noopener"); break;
      case "error-log-reveal": api("/api/errors/reveal", { method: "POST" }).catch((e) => toast(e.message, true)); break;
      case "start": switchTool("home"); break;
    }
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-cmd]");
    if (b && !b.disabled) { runCmd(b.dataset.cmd); toggleMenu(null); return; }
    if (!e.target.closest(".menu")) toggleMenu(null);
    if (!e.target.closest("#ctx-menu")) hideCtx();
  });
