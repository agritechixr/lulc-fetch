  // Help menu.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ help
  function showHelp(which) {
    $("#help-title").textContent = which === "guide" ? "Quick guide" : "Keyboard shortcuts";
    $("#help-body").innerHTML = which === "guide" ? `<div class="help">
      <h4>Workspace</h4><p><b>Contents</b> (left) lists every layer. The <b>ribbon</b> at the top shows each tab's commands (File, Insert, Analysis, History, View, Help); a tool from <b>Analysis</b> opens in the right panel. The <b>status bar</b> shows the cursor position and the map scale. Type a scale such as <code>25000</code> and press Enter to zoom to it.</p>
      <h4>Maps (2D and 3D)</h4><p><b>Insert ▸ 2D / 3D</b> adds a map; each map is a tab under the ribbon with its own Contents. Double-click a tab to rename it. Select a layer and press <kbd>Ctrl+C</kbd>, open another map and press <kbd>Ctrl+V</kbd> to copy it there. In a 3D map a DEM (a single-band GeoTIFF of heights) becomes the land and other layers are draped on it: the mouse works as in AutoCAD (wheel to zoom, middle-drag to pan, Shift + middle-drag to orbit, double-click the wheel to zoom extents), and left-drag also orbits, right-drag pans; the ViewCube (top right) and the view menu (top left) give standard views such as Top or SW Isometric, and the axes (bottom left) show which way X, Y and Z point.</p>
      <h4>Add data</h4><p><b>Insert ▸ Add data</b> (or File ▸ Add data, Ctrl+O), or drag files onto the map: GeoTIFF, Shapefile (.zip, or .shp + .shx + .dbf + .prj together), GeoJSON, KML/KMZ. <b>Insert ▸ GeoTIFF from workspace</b> lists GeoTIFFs already downloaded or produced.</p>
      <h4>Layers</h4><p>Tick to show or hide. Drag to reorder. Double-click to zoom. Right-click (or ⋯) for Properties, Export, Use as area of interest, and Compute indices. Select a raster and click the map to read its pixel values.</p>
      <h4>Export</h4><p>Rasters: GeoTIFF (values), PNG (as displayed), PNG + world file, or Shapefile (value classes → polygons). Vectors: Shapefile, GeoJSON, KML.</p>
      <h4>Your own Sentinel products</h4><p>File ▸ <b>Open Sentinel product (.SAFE)</b> lists products in the <code>data</code> folder. Sentinel-2 opens instantly with all bands. Sentinel-1 GRD is converted to calibrated backscatter (VV, VH in dB) in the background. Then use Index analysis (NDVI… for optical, RVI / CPR for radar).</p>
      <h4>Tools</h4><ul><li><b>Find imagery</b>: area → dates → search → preview → download. Downloads are added as layers when they finish.</li>
      <li><b>Index analysis</b>: pick an input raster, click an index. Each result is a new layer.</li></ul></div>`
      : `<div class="help"><table>
      <tr><td><kbd>Ctrl/⌘ K</kbd> or <kbd>Ctrl/⌘ F</kbd></td><td>Search every command, tool, layer, map and bookmark</td></tr>
      <tr><td><kbd>Ctrl/⌘ S</kbd></td><td>Save (in the temporary workspace: save as a project)</td></tr>
      <tr><td><kbd>Ctrl/⌘ Shift S</kbd></td><td>Save as a new project</td></tr>
      <tr><td><kbd>Ctrl/⌘ Z</kbd></td><td>Undo the last change to layers or maps</td></tr>
      <tr><td><kbd>Ctrl/⌘ Y</kbd> or <kbd>Ctrl/⌘ Shift Z</kbd></td><td>Redo</td></tr>
      <tr><td>Right-click a ribbon button</td><td>Add it to the quick access bar, or see its shortcut</td></tr>
      <tr><td>Measuring</td><td>Double-click or <kbd>Enter</kbd> finishes · <kbd>Backspace</kbd> removes the last point · <kbd>Esc</kbd> stops</td></tr>
      <tr><td><kbd>Ctrl/⌘ O</kbd></td><td>Add data from computer</td></tr>
      <tr><td><kbd>Ctrl/⌘ E</kbd></td><td>Export selected layer</td></tr>
      <tr><td><kbd>Ctrl/⌘ C</kbd></td><td>Copy the selected layer (to paste it into another map)</td></tr>
      <tr><td><kbd>Ctrl/⌘ V</kbd></td><td>Paste the copied layer(s) into the open map</td></tr>
      <tr><td><kbd>Ctrl/⌘ B</kbd></td><td>Bookmark the map's current view (Insert ▸ Bookmarks)</td></tr>
      <tr><td><kbd>Delete</kbd></td><td>Remove selected layer</td></tr>
      <tr><td><kbd>Ctrl/⌘ 1</kbd></td><td>Show / hide Contents</td></tr>
      <tr><td><kbd>Ctrl/⌘ 2</kbd></td><td>Show / hide tool panel</td></tr>
      <tr><td><kbd>Ctrl/⌘ 3</kbd></td><td>Show / hide the data viewer (tables under the map)</td></tr>
      <tr><td>Drag a panel edge</td><td>Resize Contents, the tool panel or the data viewer (double-click the edge to reset)</td></tr>
      <tr><td>3D: wheel · middle-drag · Shift + middle-drag</td><td>Zoom at the cursor · pan · orbit (as in AutoCAD); double-click the wheel to zoom extents</td></tr>
      <tr><td><kbd>Ctrl F1</kbd></td><td>Show / hide the ribbon (or double-click a tab)</td></tr>
      <tr><td><kbd>Esc</kbd></td><td>Close the ribbon when it is hidden</td></tr></table></div>`;
    $("#dlg-help").showModal();
  }

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    if (e.key === "Escape") { toggleMenu(null); hideCtx(); }
    // the usual shortcuts: save, save as, undo, redo, search (Undo in a text box stays the text box's own)
    if (mod && k === "s") { e.preventDefault(); runCmd(e.shiftKey ? "save-as" : "save"); return; }
    if (mod && (k === "k" || k === "f") && !$("dialog[open]")) { e.preventDefault(); openSearch(); return; }
    const textField = document.activeElement?.matches?.("textarea, select, [contenteditable=true], input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=button])");
    if (mod && !textField && !$("dialog[open]") && (k === "z" || k === "y")) { e.preventDefault(); runCmd(k === "y" || e.shiftKey ? "redo" : "undo"); return; }
    if (mod && k === "o") { e.preventDefault(); runCmd("add-data"); }
    else if (mod && k === "e") { e.preventDefault(); runCmd("export-layer"); }
    else if (mod && k === "c" && !typing && !String(getSelection()) && selectedLayer() && !$("dialog[open]")) { e.preventDefault(); runCmd("layer-copy"); }
    else if (mod && k === "v" && !typing && docs.clip?.length && !$("dialog[open]")) { e.preventDefault(); runCmd("layer-paste"); }
    else if (mod && k === "b" && !typing) { e.preventDefault(); runCmd("bookmark-view"); }
    else if (e.ctrlKey && e.key === "F1") { e.preventDefault(); runCmd("toggle-ribbon"); }
    else if (mod && e.key === "1") { e.preventDefault(); runCmd("toggle-contents"); }
    else if (mod && e.key === "2") { e.preventDefault(); runCmd("toggle-tools"); }
    else if (mod && e.key === "3") { e.preventDefault(); runCmd("toggle-viewer"); }
    else if (!typing && !mod && (e.key === "Delete" || e.key === "Backspace") && selectedId && !$("dialog[open]")) { e.preventDefault(); runCmd("remove-layer"); }
  });
