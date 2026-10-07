  // Start-up: theme, panels, menus, config, then each tool's own start.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ init
  (async () => {
    applyTheme(prefs.get("theme", "auto"));
    setPane("contents", prefs.get("contents", true));
    buildToolsMenu();
    setRibbonPinned(ribbon.pinned);
    state.config = await api("/api/config");
    $("#mission").innerHTML = Object.entries(state.config.missions).map(([k, m]) => `<option value="${k}">${esc(m.title)}</option>`).join("");
    const fillSources = () => {
      $("#source").innerHTML = mission().sources.map((k) => `<option value="${k}">${esc(state.config.sources[k])}</option>`).join("");
    };
    $("#mission").value = prefs.get("mission", "sentinel-2") in state.config.missions ? prefs.get("mission", "sentinel-2") : "sentinel-2";
    fillSources();
    $("#mission").onchange = () => { prefs.set("mission", $("#mission").value); fillSources(); clearResults(); };
    $("#indices-list").textContent = `(${state.config.indices.join(", ")})`;
    $("#dl-product").innerHTML = Object.entries(state.config.label_products).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    const years = { worldcover: [2021, 2020], esri: [2023, 2022, 2021, 2020, 2019, 2018, 2017] };
    const fillYears = () => $("#dl-year").innerHTML = years[$("#dl-product").value].map((y) => `<option>${y}</option>`).join("");
    $("#dl-product").onchange = fillYears;
    fillYears();
    setRange(90);
    await initAnalyze();
    initPca().catch((e) => toast("PCA tool: " + e.message, true));
    renderMlHub();
    initMlTrain().catch((e) => toast("Classical ML: " + e.message, true));
    initProject();
    maybeStartTour();   // the first time only
    loadCreds().catch(() => {});
    setPane("tools", false);  // the tool panel opens only when a tool is chosen from the Tools menu
    refreshJobs();
  })().catch((e) => toast("Could not reach the server: " + e.message, true));
