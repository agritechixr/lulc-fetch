  // Help menu.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ help
  function showHelp(which) {
    $("#help-title").textContent = which === "guide" ? "Quick guide" : "Keyboard shortcuts";
    $("#help-body").innerHTML = which === "guide" ? `<div class="help">
      <h4>Workspace</h4><p><b>Contents</b> (left) lists every layer. <b>Tools</b> (menu) opens a tool in the right panel. The <b>status bar</b> shows the cursor position and the map scale. Type a scale such as <code>25000</code> and press Enter to zoom to it.</p>
      <h4>Add data</h4><p>File ▸ Add data, the <b>+ Add data</b> button, or drag files onto the map: GeoTIFF, Shapefile (.zip, or .shp + .shx + .dbf + .prj together), GeoJSON, KML/KMZ. <b>Workspace</b> lists GeoTIFFs already downloaded or produced.</p>
      <h4>Layers</h4><p>Tick to show or hide. Drag to reorder. Double-click to zoom. Right-click (or ⋯) for Properties, Export, Use as area of interest, and Compute indices. Select a raster and click the map to read its pixel values.</p>
      <h4>Export</h4><p>Rasters: GeoTIFF (values), PNG (as displayed), PNG + world file, or Shapefile (value classes → polygons). Vectors: Shapefile, GeoJSON, KML.</p>
      <h4>Your own Sentinel products</h4><p>File ▸ <b>Open Sentinel product (.SAFE)</b> lists products in the <code>data</code> folder. Sentinel-2 opens instantly with all bands. Sentinel-1 GRD is converted to calibrated backscatter (VV, VH in dB) in the background. Then use Index analysis (NDVI… for optical, RVI / CPR for radar).</p>
      <h4>Tools</h4><ul><li><b>Find imagery</b>: area → dates → search → preview → download. Downloads are added as layers when they finish.</li>
      <li><b>Index analysis</b>: pick an input raster, click an index. Each result is a new layer.</li></ul></div>`
      : `<div class="help"><table>
      <tr><td><kbd>Ctrl/⌘ O</kbd></td><td>Add data from computer</td></tr>
      <tr><td><kbd>Ctrl/⌘ E</kbd></td><td>Export selected layer</td></tr>
      <tr><td><kbd>Delete</kbd></td><td>Remove selected layer</td></tr>
      <tr><td><kbd>Ctrl/⌘ 1</kbd></td><td>Show / hide Contents</td></tr>
      <tr><td><kbd>Ctrl/⌘ 2</kbd></td><td>Show / hide tool panel</td></tr>
      <tr><td><kbd>Ctrl/⌘ 3</kbd></td><td>Show / hide the data viewer (tables under the map)</td></tr>
      <tr><td>Drag a panel edge</td><td>Resize Contents, the tool panel or the data viewer (double-click the edge to reset)</td></tr>
      <tr><td><kbd>Esc</kbd></td><td>Close menus</td></tr></table></div>`;
    $("#dlg-help").showModal();
  }

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    if (e.key === "Escape") { toggleMenu(null); hideCtx(); }
    if (mod && k === "o") { e.preventDefault(); runCmd("add-data"); }
    else if (mod && k === "e") { e.preventDefault(); runCmd("export-layer"); }
    else if (mod && e.key === "1") { e.preventDefault(); runCmd("toggle-contents"); }
    else if (mod && e.key === "2") { e.preventDefault(); runCmd("toggle-tools"); }
    else if (mod && e.key === "3") { e.preventDefault(); runCmd("toggle-viewer"); }
    else if (!typing && !mod && (e.key === "Delete" || e.key === "Backspace") && selectedId && !$("dialog[open]")) { e.preventDefault(); runCmd("remove-layer"); }
  });
