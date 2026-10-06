  // The first-run tour: six short hints, each pointing at a part of the window (the ribbon, the quick access bar, search,
  // Contents, the map, the map tabs). Next / Back (or the arrow keys), Skip (or Esc). It shows once; Help ▸ Tour shows it again.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ tour
  const TOUR = [
    ["#menus", "The ribbon", "Each tab holds its commands: <b>File</b> (save, projects), <b>Insert</b> (new 2D / 3D maps, add data, library, bookmarks), <b>Analysis</b> (every tool), <b>View</b> (basemaps, measure). Double-click a tab to hide the ribbon."],
    [".qat", "Quick access", "<b>Save</b> (Ctrl+S), <b>Save as</b>, <b>Undo</b> (Ctrl+Z) and <b>Redo</b> (Ctrl+Y). Right-click any ribbon button to add it here."],
    ["#cmd-search", "Search everything", "Press <b>Ctrl+K</b> and type what you want, e.g. <i>ndvi</i>, <i>export</i> or <i>3d map</i>, then Enter."],
    ["#contents", "Contents", "The layers of the open map: tick to show, drag to reorder, right-click for options. Drop files on the map to add them."],
    [".mapwrap", "The map", "Click to read a layer's values; right-click for coordinates and tools there. <b>View ▸ Measure</b> gives distances, areas and heights."],
    ["#map-tabs", "Maps", "Each tab is a map with its own Contents. <b>Insert ▸ 2D / 3D</b> adds one; double-click a tab to rename it; Ctrl+C / Ctrl+V copies layers between maps."],
  ];
  const tour = { i: 0, on: false };
  function startTour() {
    toggleMenu(null);
    tour.on = true; tour.i = 0;
    $("#tour").classList.remove("hidden");
    showTourStep();
  }
  function endTour() {
    tour.on = false;
    $("#tour").classList.add("hidden");
    prefs.set("tour-done", true);
  }
  function showTourStep() {
    const [sel, title, text] = TOUR[tour.i], el = $(sel);
    if (!el || !el.getBoundingClientRect().width) { if (tour.i < TOUR.length - 1) { tour.i++; return showTourStep(); } return endTour(); }
    const r = el.getBoundingClientRect(), pad = 6, spot = $("#tour-spot"), card = $("#tour-card");
    Object.assign(spot.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + 2 * pad}px`, height: `${r.height + 2 * pad}px` });
    card.innerHTML = `<div class="tc-step">${tour.i + 1} of ${TOUR.length}</div><h3>${title}</h3><p>${text}</p>
      <div class="tc-acts"><button type="button" class="btn small ghost" data-t="skip">Skip tour</button><span style="flex:1"></span>
        ${tour.i ? `<button type="button" class="btn small" data-t="back">Back</button>` : ""}
        <button type="button" class="btn small primary" data-t="next">${tour.i === TOUR.length - 1 ? "Done" : "Next"}</button></div>`;
    // beside the part: below it if there is room, otherwise to its right, otherwise above
    const cw = 330, ch = card.offsetHeight || 170, vw = innerWidth, vh = innerHeight;
    let x = r.left, y = r.bottom + 14;
    if (y + ch > vh - 10) { x = r.right + 14; y = r.top; }
    if (x + cw > vw - 10) { x = Math.max(10, r.left - cw - 14); }
    if (y + ch > vh - 10) y = Math.max(10, r.top - ch - 14);
    if (r.height > vh * 0.5 && r.width > vw * 0.4) { x = r.left + r.width / 2 - cw / 2; y = r.top + r.height / 2 - ch / 2; }   // a big part: in its middle
    Object.assign(card.style, { left: `${Math.min(Math.max(10, x), vw - cw - 10)}px`, top: `${Math.min(Math.max(10, y), vh - ch - 10)}px` });
    $$("[data-t]", card).forEach((b) => b.onclick = () => tourGo(b.dataset.t));
    $("[data-t=next]", card).focus();
  }
  function tourGo(what) {
    if (what === "skip" || (what === "next" && tour.i === TOUR.length - 1)) return endTour();
    tour.i = Math.max(0, tour.i + (what === "back" ? -1 : 1));
    showTourStep();
  }
  document.addEventListener("keydown", (e) => {
    if (!tour.on) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); endTour(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); tourGo("next"); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); tourGo("back"); }
  }, true);
  addEventListener("resize", () => { if (tour.on) showTourStep(); });
  // the first time: once the start-up dialogs (project, restore) are closed
  function maybeStartTour() {
    if (prefs.get("tour-done", false)) return;
    const tryStart = () => { if ($("dialog[open]")) return setTimeout(tryStart, 700); startTour(); };
    setTimeout(tryStart, 1200);
  }
