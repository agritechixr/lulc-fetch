  // The Tools / Agri / Embeddings menus, the start page and switchTool(), which opens a tool.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ tools (Tools menu + tool panel)
  // To add a tool: add <section id="tab-<id>" class="tabpanel hidden"> to index.html and an entry here.
  const ICONS = {
    forecast: '<path d="M3 20h18" opacity=".5"/><path d="M4 16l4-5 4 3 3-4"/><path d="M15 10l3-2 3-3" stroke-dasharray="2 2"/><circle cx="15" cy="10" r="1.3" fill="currentColor" stroke="none"/>',
    fcrun: '<path d="M3 20h18" opacity=".5"/><path d="M4 15l4-4 3 2" /><path d="M11 13l3-3 3 1 4-4" stroke-dasharray="2 2"/><path d="M5 4l4 2.5L5 9z" fill="currentColor" stroke="none"/>',
    cloud: '<path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.5 1.5A3.3 3.3 0 0 0 7 18z"/><path d="M8 21v-1M12 21v-1M16 21v-1" opacity=".7"/>',
    interp: '<circle cx="5" cy="17" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="7" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="14" r="1.6" fill="currentColor" stroke="none"/><path d="M3 12c3-5 6-7 9-6s4 5 9 3" opacity=".75"/><path d="M3 20c4-3 8-4 11-3s5 1 7-1" opacity=".45"/>',
    search: '<path d="M4 7l4-4 4 4-4 4z"/><path d="M12 15l4-4 4 4-4 4z"/><path d="M9.5 9.5l5 5"/><path d="M3 21c1.5-3 4-4.5 7-4.5"/>',
    analyze: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17.5l9 5 9-5" opacity=".5"/>',
    jobs: '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 17v3h16v-3"/>',
    export: '<path d="M12 15V3"/><path d="M7 8l5-5 5 5"/><path d="M4 14v6h16v-6"/>',
    samples: '<path d="M4 20l5-12 6 4 5-8"/><circle cx="9" cy="8" r="1.6"/><circle cx="15" cy="12" r="1.6"/><path d="M14 20h7M17.5 16.5v7" opacity=".7"/>',
    stack: '<path d="M12 3l9 4.5-9 4.5-9-4.5z"/><path d="M3 12l9 4.5 9-4.5"/><path d="M3 16.5l9 4.5 9-4.5"/>',
    table: '<path d="M4 6l4-3 4 3 4-3 4 3v6"/><path d="M4 6v6"/><rect x="3" y="14" width="18" height="7" rx="1"/><path d="M3 17.5h18M9 14v7M15 14v7"/>',
    ml: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 4v16"/>',
    pca: '<circle cx="7" cy="16" r="1.4"/><circle cx="11" cy="12" r="1.4"/><circle cx="15" cy="10" r="1.4"/><circle cx="9" cy="17" r="1.4"/><circle cx="17" cy="7" r="1.4"/><path d="M3 21L21 3"/><path d="M8 6l10 10" opacity=".5"/>',
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    raster: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    vector: '<path d="M4 18l5-12 7 4 4 8z"/>',
    rasterml: '<rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1" opacity=".55"/><rect x="3" y="13" width="8" height="8" rx="1" opacity=".55"/><rect x="13" y="13" width="8" height="8" rx="1"/><path d="M5.5 7h3M15.5 17h3"/>',
    cluster: '<circle cx="7" cy="8" r="1.6"/><circle cx="10" cy="6" r="1.6"/><circle cx="9" cy="10.5" r="1.6"/><circle cx="16" cy="15" r="1.6"/><circle cx="18.5" cy="12.5" r="1.6"/><circle cx="15" cy="18" r="1.6"/><circle cx="18" cy="18.5" r="1.6"/><path d="M4.5 4.5a6 6 0 0 1 8 7.5M12 19a6 6 0 0 0 9-8" opacity=".55"/>',
    tsne: '<circle cx="6" cy="7" r="1.5"/><circle cx="8" cy="9.5" r="1.5"/><circle cx="5" cy="11" r="1.5"/><circle cx="16" cy="6" r="1.5"/><circle cx="18" cy="8.5" r="1.5"/><circle cx="12" cy="17" r="1.5"/><circle cx="14.5" cy="18.5" r="1.5"/><circle cx="11" cy="20" r="1.5"/><path d="M3 3v18h18" opacity=".55"/>',
    dl: '<circle cx="5" cy="7" r="1.8"/><circle cx="5" cy="17" r="1.8"/><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/><circle cx="19" cy="12" r="1.8"/><path d="M6.7 7.5l3.6 4M6.7 16.5l3.6-4M6.7 7l3.5-1.5M6.7 17l3.5 1.5M13.8 5.8l3.6 5.3M13.8 18.2l3.6-5.3M13.8 12H17.2" opacity=".6"/>',
    dlmap: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 14l5-4 4 3 4-5 5 4" opacity=".6"/><circle cx="16.5" cy="16.5" r="3.2" fill="currentColor" stroke="none" opacity=".8"/>',
    traindet: '<rect x="3" y="3" width="18" height="18" rx="2" opacity=".45"/><rect x="6" y="6" width="7" height="6" rx=".5"/><path d="M14 19l2.5-2.5L19 19M16.5 16.5V21" opacity=".9"/><path d="M6 15h5M6 17.5h3" opacity=".6"/>',
    detect: '<rect x="3" y="3" width="18" height="18" rx="2" opacity=".45"/><rect x="6" y="7" width="7" height="6" rx=".5"/><rect x="12" y="13" width="6" height="5" rx=".5" fill="currentColor" fill-opacity=".35"/><path d="M6 5.5h3"/>',
    patches: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18" opacity=".55"/><rect x="9" y="9" width="6" height="6" fill="currentColor" stroke="none" opacity=".8"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>',
    embed: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18" opacity=".35"/><circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none"/><path d="M12 9.8V6M14.2 12H18M12 14.2V18M9.8 12H6" opacity=".7"/>',
    convert: '<path d="M4 8h13l-3-3M20 16H7l3 3"/><text x="3.5" y="14.2" font-size="5.5" font-family="sans-serif" fill="currentColor" stroke="none">8</text><text x="14" y="12.6" font-size="5.5" font-family="sans-serif" fill="currentColor" stroke="none">32</text>',
    similar: '<rect x="3" y="3" width="12" height="12" rx="1.5" opacity=".45"/><path d="M3 7h12M3 11h12M7 3v12M11 3v12" opacity=".3"/><circle cx="15.5" cy="15.5" r="4"/><path d="M18.5 18.5L21 21"/>',
    leaf: '<path d="M5 19C5 10 10 5 20 4c-1 10-6 15-15 15z"/><path d="M5 19l8-8" opacity=".7"/><circle cx="14" cy="9.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="10.5" cy="13.5" r="1" fill="currentColor" stroke="none"/>',
    book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/><path d="M12 7.5c-2 .5-3 2-3 4 2 0 3.5-1.5 3-4zM12 7.5c1.5 1 2 2.5 1.5 4.5" opacity=".75"/>',
  };
  const svg = (name, w = 1.8) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
  const TOOLS = [
    { id: "search", title: "Find imagery", icon: "search", subtitle: "Search, preview and download Sentinel-2, composites and land-cover labels" },
    { id: "analyze", title: "Index analysis", icon: "analyze", subtitle: "NDVI, SAVI, EVI, NDWI and 21 more indices or your own formula" },
    { id: "pca", title: "PCA & dimensionality reduction", icon: "pca", subtitle: "PCA, Kernel PCA, NMF, ICA and more (scikit-learn) on any multiband image" },
    { id: "samples", title: "Training samples", icon: "samples", subtitle: "Draw labelled polygons and points for each class on the map" },
    { id: "stack", title: "Stack layers", icon: "stack", subtitle: "Combine bands from several layers (S2, S1, DEM, indices…) onto one grid" },
    { id: "raster2table", title: "Raster → table", icon: "table", subtitle: "Turn any image (multispectral, hyperspectral, SAR) into a table, with optional ground-truth labels" },
    { id: "ml", title: "Classical ML (tabular data)", icon: "ml", subtitle: "Machine-learning tools that work on tables" },
    { id: "rasterml", title: "Classical ML for raster", icon: "rasterml", subtitle: "Train SVM, Maximum Likelihood, Random Forest, SAM and more straight from an image and ground truth, and map it: RGB, multispectral, hyperspectral or embeddings" },
    { id: "dltrain", title: "Train classify model", icon: "dl", subtitle: "Train U-Net, DeepLabV3+, PSPNet, FCN, SegFormer and more (MobileNetV2/V3, ResNet, EfficientNet backbones) on your Make-training-data patches, with early stopping and an HTML report" },
    { id: "dlpredict", title: "Classify image", icon: "dlmap", subtitle: "Map a whole image with a model from Train classify model (deep learning, tiled, seamless), with a confidence layer" },
    { id: "detect", title: "Detect object", icon: "detect", subtitle: "Find vehicles, ships, planes, people, storage tanks and more in high-resolution images: YOLO26 (incl. aerial DOTA model), Faster R-CNN, RetinaNet, Mask R-CNN, SAM 2.1 segment-everything, or your own trained models. Boxes or outlines as a vector layer" },
    { id: "traindet", title: "Train detection model", icon: "traindet", subtitle: "Train YOLO26 / YOLO11 to find your own objects (boxes, outlines or rotated boxes) from an image and labelled polygons or points, with early stopping, live curves and an HTML report" },
    { id: "patches", title: "Make training data", icon: "patches", subtitle: "Cut large images and their ground truth into image / label patches for deep-learning training" },
    { id: "export", title: "Export data", icon: "export", subtitle: "Save any layer to your computer: GeoTIFF, PNG, Shapefile, GeoJSON, KML" },
    { id: "jobs", title: "Downloads & jobs", icon: "jobs", subtitle: "Background downloads, logs and output files" },
  ];
  LF.tools.forEach(({ id, menu, title, icon, subtitle }) => TOOLS.push({ id, menu, title, icon, subtitle }));
  // menus of their own (besides Tools): their tools have menu: "<key>"; shortcuts list tools of other menus there too
  const MENUS = {
    agri: { el: "#agri-menu", label: "Also useful for crops", shortcuts: ["analyze", "embed", "search"],
            subtitles: { analyze: "Crop health and vigour from satellite images: NDVI, EVI, SAVI, NDRE, NDWI and more" } },
    embed: { el: "#embed-menu", label: "Use embeddings with", shortcuts: ["rasterml", "ml", "pca"],
             subtitles: { rasterml: "Map crops or land cover from an embedding layer and a few labelled points (k-NN, SVM, SAM…)",
                          ml: "Cluster an embedding (Raster → table first) without labels, or train on tables",
                          pca: "Reduce the 64 / 128 dimensions to a few components" } },
    forecast: { el: "#forecast-menu", label: "Also useful for forecasts", shortcuts: ["interp", "ml"],
                subtitles: { interp: "Make a map from a forecast at stations (Put on the map, then a surface: kriging, IDW…)",
                             ml: "Predict a value from other columns without time (regression / classification on tables)" } },
    library: { el: "#library-menu", label: "", shortcuts: [] },
  };
  let currentTool = "home";

  // Tools are listed A–Z; each explanation is behind an ⓘ button (click it to show / hide, or hover for a tooltip)
  const byTitle = (a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
  // the ⓘ that shows / hides a tool's explanation (the same circled i as every other hint)
  const INFO_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.6" r="1.1" fill="currentColor" stroke="none"/></svg>';
  function toolEntry(cls, attrs, t, withIcon = true) {
    return `<div role="button" tabindex="0" class="${cls}" ${attrs}>${withIcon ? `<span class="ic">${svg(t.icon)}</span>` : ""}
      <span class="te-text"><b>${esc(t.title)}</b><small class="te-desc">${esc(t.subtitle || "")}</small></span>
      ${t.subtitle ? `<button type="button" class="eye" title="${esc(t.subtitle)}" aria-label="What does ${esc(t.title)} do?" aria-expanded="false">${INFO_SVG}</button>` : ""}</div>`;
  }
  function wireEntries(root, selector, onOpen) {
    $$(selector, root).forEach((el) => {
      el.onclick = (e) => { if (!e.target.closest(".eye")) onOpen(el); };
      el.onkeydown = (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === el) { e.preventDefault(); onOpen(el); } };
      const eye = $(".eye", el);
      if (eye) eye.onclick = (e) => { e.stopPropagation(); const on = el.classList.toggle("show-desc"); eye.setAttribute("aria-expanded", on); };
    });
  }
  function buildToolsMenu() {
    const tools = [...TOOLS].sort(byTitle);   // Classical ML's own tools are tabs inside it (see renderMlHub)
    $("#tools-menu").innerHTML = tools.filter((t) => !t.menu).map((t) => toolEntry("tool-item", `data-tool="${t.id}"`, t)).join("");
    wireEntries($("#tools-menu"), "[data-tool]", (b) => { switchTool(b.dataset.tool); toggleMenu(null); });
    Object.entries(MENUS).forEach(([key, m]) => {
      const el = $(m.el);
      el.innerHTML = TOOLS.filter((t) => t.menu === key).map((t) => toolEntry("tool-item", `data-tool="${t.id}"`, t)).join("") +
        (m.shortcuts.length ? `<hr><div class="menu-label">${esc(m.label)}</div>` : "") +
        m.shortcuts.map((id) => TOOLS.find((t) => t.id === id)).filter(Boolean)
          .map((t) => toolEntry("tool-item", `data-tool="${t.id}"`, m.subtitles?.[t.id] ? { ...t, subtitle: m.subtitles[t.id] } : t)).join("");
      wireEntries(el, "[data-tool]", (b) => { switchTool(b.dataset.tool); toggleMenu(null); });
    });
    $("#tool-cards").innerHTML = tools.map((t) => toolEntry("tool-card", `data-tool="${t.id}"`, t)).join("");
    wireEntries($("#tool-cards"), "[data-tool]", (b) => switchTool(b.dataset.tool));
  }
  // the open tool's explanation, under its title, also behind an ⓘ button
  $("#tool-eye").innerHTML = INFO_SVG;
  const syncToolSub = () => {
    const on = prefs.get("tool-sub", false);
    $("#tool-sub").classList.toggle("hidden", !on);
    $("#tool-eye").setAttribute("aria-expanded", on);
    $("#tool-eye").classList.toggle("on", on);
  };
  $("#tool-eye").onclick = () => { prefs.set("tool-sub", !prefs.get("tool-sub", false)); syncToolSub(); };
  syncToolSub();

  function switchTool(id, arg) {
    const tool = TOOLS.find((t) => t.id === id) || { id: "home", title: "Start", subtitle: "Choose a tool" };
    currentTool = tool.id;
    $$(".tabpanel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + tool.id));
    $("#tool-title").textContent = tool.title;
    $("#tool-sub").textContent = tool.subtitle;
    $("#tool-eye").title = tool.subtitle;
    $("#tool-eye").classList.toggle("hidden", tool.id === "home");
    $("#active-tool").innerHTML = tool.id === "home" ? "" : `Tool: <b>${esc(tool.title)}</b>`;
    $$(["#tools-menu", ...Object.values(MENUS).map((m) => m.el)].map((e) => `${e} [data-tool]`).join(", ")).forEach((b) => b.classList.toggle("on", b.dataset.tool === tool.id));
    document.title = tool.id === "home" ? "LULC Fetch" : `${tool.title} · LULC Fetch`;
    setPane("tools", true);
    renderRunBar();
    if (tool.id === "analyze") refreshAnalyzeInputs();
    if (tool.id === "jobs") refreshJobs();
    if (tool.id === "home") refreshHomeProducts();
    if (tool.id === "export") { refreshExportLayers(); renderExportForm(); }
    if (tool.id === "pca" && pcaState.schema) refreshPcaInputs();
    if (tool.id === "ml") { openMlSub(mlSub); }
    if (tool.id === "raster2table") refreshRtInputs();
    if (tool.id === "rasterml") refreshRm();
    if (tool.id === "patches") refreshPt();
    if (tool.id === "dltrain") refreshDt();
    if (tool.id === "dlpredict") refreshDp();
    if (tool.id === "detect") refreshOd();
    if (tool.id === "traindet") refreshTd();
    PLUGINS[tool.id]?.hooks?.open?.(arg);
    if (tool.id === "samples") renderSamples();
    if (tool.id === "stack") refreshStack();
    prefs.set("tool", tool.id);
  }
