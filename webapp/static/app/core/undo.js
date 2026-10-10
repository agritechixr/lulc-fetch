  // Undo / Redo (Ctrl+Z / Ctrl+Y, the arrows in the quick access bar): every change to the layers and maps (add, remove,
  // move, rename, show / hide, opacity, style, 2D ↔ 3D data, new / closed / renamed maps, paste) can be taken back.
  // How: every change ends in saveLayers(), which hands a snapshot of the open map (its layer objects and their settings)
  // and the list of maps to historyNote(); the step's name comes from what differs from the last snapshot. Steps made
  // of several changes (paste, remove all, new or closed map) are one step through historyStep().
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ undo / redo
  const undoHist = { undo: [], redo: [], cur: null, quiet: 0, last: null, max: 60 };
  // settings that are changed in place somewhere are copied, so a snapshot keeps them as they were
  const DEEP = ["render", "band_map", "classColors", "classes", "userSet"];
  function propsOf(l) {
    const { leaflet, busy, error, _pane, ...p } = l;
    for (const k of DEEP) if (p[k] && typeof p[k] === "object") p[k] = structuredClone(p[k]);
    return p;
  }
  function snapState() {
    return { active: docs.active, selected: selectedId, docs: docs.list.map((d) => ({ ...d, live: d.live && d.live.slice() })),
             layers: layers.map((l) => ({ ref: l, props: propsOf(l) })) };
  }
  const STYLE_KEYS = ["render", "band_map", "scale", "offset", "color", "weight", "fillOpacity", "dash", "classColors", "classes", "extrude"];
  const q = (n) => `“${n}”`;
  // the name of the change from one snapshot to the next (null: nothing worth a step)
  function diffLabel(a, b) {
    const dn = (s) => s.docs.map((d) => `${d.id}\u0000${d.name}\u0000${d.kind}`).join("\u0001");
    if (dn(a) !== dn(b)) {
      if (b.docs.length > a.docs.length) return `New map ${q(b.docs.find((d) => !a.docs.some((x) => x.id === d.id))?.name || "")}`;
      if (b.docs.length < a.docs.length) return `Close map ${q(a.docs.find((d) => !b.docs.some((x) => x.id === d.id))?.name || "")}`;
      const d = b.docs.find((x, i) => x.name !== a.docs[i].name);
      return d ? `Rename map ${q(d.name)}` : "Change maps";
    }
    if (a.active !== b.active) return null;   // opening another map is not a change
    const ida = a.layers.map((x) => x.ref.id), idb = b.layers.map((x) => x.ref.id);
    const added = b.layers.filter((x) => !ida.includes(x.ref.id)), removed = a.layers.filter((x) => !idb.includes(x.ref.id));
    if (added.length) return added.length === 1 ? `Add ${q(added[0].props.name)}` : `Add ${added.length} layers`;
    if (removed.length) return removed.length === 1 ? `Remove ${q(removed[0].props.name)}` : `Remove ${removed.length} layers`;
    if (ida.join() !== idb.join()) return `Move ${q(b.layers.find((x, i) => x.ref.id !== ida[i]).props.name)}`;
    for (let i = 0; i < a.layers.length; i++) {
      const pa = a.layers[i].props, pb = b.layers[i].props, n = pb.name;
      if (pa.name !== pb.name) return `Rename ${q(pa.name)} to ${q(pb.name)}`;
      if (pa.visible !== pb.visible) return `${pb.visible ? "Show" : "Hide"} ${q(n)}`;
      if (pa.opacity !== pb.opacity) return `Opacity of ${q(n)}`;
      if (pa.view3d !== pb.view3d) return `Move ${q(n)} to ${pb.view3d === "surface" ? "3D" : "2D"} data`;
      if (STYLE_KEYS.some((k) => JSON.stringify(pa[k]) !== JSON.stringify(pb[k]))) return `Style of ${q(n)}`;
      if (pa.geojson !== pb.geojson) return `Edit ${q(n)}`;
    }
    return null;
  }
  function trim(list) { while (list.length > undoHist.max) list.shift(); }
  // called by saveLayers(): amend = the same step goes on (a picture finished drawing, another map was opened)
  function historyNote({ amend = false } = {}) {
    const s = snapState();
    if (!undoHist.cur || amend || undoHist.quiet || proj.loading) { undoHist.cur = s; syncUndoButtons(); return; }
    const label = diffLabel(undoHist.cur, s);
    if (label) {
      const now = performance.now();
      if (undoHist.last?.label === label && now - undoHist.last.t < 900 && /^Opacity|^Style/.test(label)) undoHist.last.t = now;   // one step for a slider drag
      else { undoHist.undo.push({ ...undoHist.cur, label }); trim(undoHist.undo); undoHist.redo = []; undoHist.last = { label, t: now }; }
    }
    undoHist.cur = s;
    syncUndoButtons();
  }
  // several changes as one step (paste, remove all, a new or closed map)
  function historyStep(label, fn) {
    const before = undoHist.cur || snapState();
    undoHist.quiet++;
    try { return fn(); }
    finally {
      undoHist.quiet--;
      undoHist.undo.push({ ...before, label }); trim(undoHist.undo);
      undoHist.redo = []; undoHist.last = null; undoHist.cur = snapState();
      syncUndoButtons();
    }
  }
  function historyReset() { undoHist.undo = []; undoHist.redo = []; undoHist.last = null; undoHist.cur = snapState(); syncUndoButtons(); }

  // put a snapshot back: its maps, the open map's layers (the same objects, with their settings then), the selection
  function applySnap(s) {
    undoHist.quiet++;
    try {
      if (v3.on) hide3d(activeDoc());
      layers.forEach((l) => l.leaflet?.remove());
      [...vw.tabs].filter((t) => t.key.startsWith("attr:") && !s.layers.some((x) => `attr:${x.ref.id}` === t.key)).forEach((t) => closeTab(t.key, true));
      docs.list = s.docs.map((d) => ({ ...d, live: d.live && d.live.slice() }));
      docs.active = s.active;
      layers.splice(0, layers.length, ...s.layers.map(({ ref, props }) => {
        for (const k of Object.keys(ref)) if (!["leaflet", "busy", "error", "_pane"].includes(k) && !(k in props)) delete ref[k];
        return Object.assign(ref, propsOf(props));
      }));
      layers.forEach((l) => buildLeaflet(l));
      const aoi = getLayer("aoi");
      if (aoi && !state.aoi) restoreAoi(aoi);
      restack();
      clearLayerSelection();
      selectedId = layers.some((l) => l.id === s.selected) ? s.selected : null;
      renderContents();
      renderMapTabs();
      if (is3D()) show3d(activeDoc());
      saveLayers();
    } finally { undoHist.quiet--; }
    undoHist.cur = snapState();
    undoHist.last = null;
    syncUndoButtons();
  }
  function undo() {
    const s = undoHist.undo.pop();
    if (!s) return toast("Nothing to undo");
    undoHist.redo.push({ ...snapState(), label: s.label });
    applySnap(s);
    toast(`Undone: ${s.label}`);
  }
  function redo() {
    const s = undoHist.redo.pop();
    if (!s) return toast("Nothing to redo");
    undoHist.undo.push({ ...snapState(), label: s.label });
    applySnap(s);
    toast(`Redone: ${s.label}`);
  }
  // the quick access bar's arrows say what they would undo / redo
  function syncUndoButtons() {
    const u = undoHist.undo.at(-1), r = undoHist.redo.at(-1);
    const bu = $("#qa-undo"), br = $("#qa-redo");
    if (!bu) return;
    bu.disabled = !u; br.disabled = !r;
    bu.title = u ? `Undo: ${u.label} (Ctrl+Z)` : "Nothing to undo (Ctrl+Z)";
    br.title = r ? `Redo: ${r.label} (Ctrl+Y)` : "Nothing to redo (Ctrl+Y)";
  }
