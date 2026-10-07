  // Editing tables and attribute tables in the data viewer.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ editing tables and attribute tables
  // Tables (files) are edited on the server (one undo step per change; cell edits are batched until Save).
  // Vector attribute tables are edited in the browser (immediately, with their own undo history).
  const isDirty = (t) => !!(t.pending?.size || t.log?.length);
  async function toggleEdit(t) {
    try {
      if (!t.edit) return await startEdit(t);
      if (isDirty(t)) return await askSaveEdits(t);
      await finishEdit(t, "discard");
    } catch (e) { toast(e, true); }
  }
  async function startEdit(t) {
    t.sel = new Set(); t.pending = new Map(); t.hist = [];
    if (t.kind === "attr") {
      const l = editLayer(t);
      l._original = JSON.stringify(l.geojson.features);   // until Save, the stored / project version stays the original
      t.log = [];
    } else {
      const r = await api("/api/tables/edit/start", { method: "POST", json: { path: t.path } });
      t.origPath = t.path; t.path = r.work; t.log = r.log || [];
      t.data = null; t.stats = null;
      if (r.resumed && t.log.length) toast(`Your unsaved changes from ${r.started} were kept: save or discard them`);
    }
    t.edit = true;
    renderViewer();
  }
  // overwrite | new | discard
  async function finishEdit(t, mode, name) {
    if (t.kind === "attr") {
      const l = editLayer(t);
      if (l && l._original !== undefined) {
        if (mode === "discard") l.geojson.features = JSON.parse(l._original);
        else if (mode === "new") {
          const edited = l.geojson.features;
          l.geojson.features = JSON.parse(l._original);
          addVectorLayer({ type: "FeatureCollection", features: edited }, name || `${l.name}_edited`, { zoom: false });
        }
        delete l._original;
        buildLeaflet(l); restack(); renderContents(); saveLayers();
      }
    } else {
      if (mode !== "discard" && t.pending?.size) await tableOps(t, [], `Edited ${t.pending.size} cell(s)`, { silent: true });
      if (mode === "discard") await api("/api/tables/edit/discard", { method: "POST", json: { path: t.path } });
      else {
        const r = await api("/api/tables/edit/save", { method: "POST", json: { path: t.path, mode, name } });
        if (mode === "new") addItem({ kind: "table", name: r.name, path: r.path });
      }
      t.path = t.origPath || t.path;
    }
    t.edit = false; t.log = []; t.hist = []; t.pending = new Map(); t.sel = new Set(); t.data = null; t.stats = null;
    renderViewer();
    if (t.kind !== "attr") {
      const it = dataItems.find((d) => d.path === t.path);
      if (it && t.data) { it.rows = t.data.total; it.cols = t.data.columns.length; saveItems(); renderItems(); }
    }
    toast(mode === "discard" ? "Changes discarded: the original is unchanged" : mode === "new" ? `Saved as a new ${t.kind === "attr" ? "layer" : "table"}` : "Changes saved");
  }
  // the Save dialog: lists the changes and warns before overwriting the existing table / layer
  function askSaveEdits(t) {
    const isAttr = t.kind === "attr", l = isAttr ? editLayer(t) : null;
    const what = isAttr ? l?.name : (t.origPath || t.path).split(/[\\/]/).pop();
    const n = (t.log?.length || 0) + (t.pending?.size ? 1 : 0);
    $("#se-title").textContent = `Save changes to ${what}`;
    $("#se-summary").innerHTML = `<b>${n}</b> change${n === 1 ? "" : "s"} since you started editing:`;
    $("#se-log").innerHTML = [...(t.log || []), ...(t.pending?.size ? [`Edited ${t.pending.size} cell(s) (not yet applied)`] : [])].map((x) => `<li>${esc(x)}</li>`).join("") || "<li>No changes</li>";
    $("#se-warn").innerHTML = isAttr
      ? `⚠ <b>Overwrite</b> applies these changes to the existing layer <b>${esc(what)}</b> in Contents${inProject() ? " and in the project" : ""}. This can't be undone afterwards. The original file on your computer (e.g. the shapefile you added) is not changed: use right-click ▸ <i>Save to folder…</i> to write a new file.`
      : `⚠ <b>Overwrite</b> applies these changes to the existing table <b>${esc(what)}</b>. Tools that use it (Train a model, Clustering …) will see the new version. The previous version is kept: right-click the table ▸ <i>Restore previous version</i>.`;
    $("#se-name").value = `${what.replace(/\.[^.]+$/, "")}_edited`;
    $("#se-new-label").textContent = isAttr ? "Name of the new layer" : "Name of the new table";
    $("#se-overwrite").textContent = `Overwrite ${what}`;
    $("#dlg-save-edits").showModal();
    const go = (mode) => async () => {
      if (mode === "discard" && !confirm("Throw away all changes? The original stays as it was.")) return;
      const name = $("#se-name").value.trim();
      if (mode === "new" && !name) return toast("Give the new one a name", true);
      $("#dlg-save-edits").close();
      try { await finishEdit(t, mode, name); } catch (e) { toast(e, true); }
    };
    $("#se-overwrite").onclick = go("overwrite");
    $("#se-new").onclick = go("new");
    $("#se-discard").onclick = go("discard");
  }
  function updateEditBar(t) {
    const body = $("#viewer-body");
    if (!body || vw.active !== t.key) return;
    const del = $('[data-ed="delrows"]', body);
    if (del) { del.disabled = !t.sel?.size; del.textContent = t.sel?.size ? `Delete ${t.sel.size} selected` : "Delete selected"; }
    const n = (t.log?.length || 0) + (t.pending?.size || 0), pend = $(".vt-pending", body);
    if (pend) {
      pend.classList.toggle("dirty", n > 0);
      $(".vt-pending-n", body).innerHTML = n ? `● <b>${n}</b> unsaved change${n === 1 ? "" : "s"}` : "No changes yet";
    }
    $$('[data-ed="save"], [data-ed="discard"]', body).forEach((b) => b.disabled = !n);
    const undo = $('[data-ed="undo"]', body);
    if (undo) undo.disabled = t.kind === "attr" ? !t.hist?.length : !(t.data?.undo || t.pending?.size);
  }
  const editLayer = (t) => getLayer(t.layerId);

  async function editAction(t, act) {
    try {
      if (act === "addfield") return openCalc(t, { mode: "new", title: "Add field" });
      if (act === "calc") return openCalc(t, { mode: t.data?.columns?.length ? "update" : "new" });
      if (act === "addrow") return await tableOps(t, [{ op: "add_row", values: {} }], "Added a row");
      if (act === "delrows") {
        const ids = [...t.sel];
        if (!ids.length || !confirm(`Delete ${ids.length} row(s)? (Undo is available.)`)) return;
        return t.kind === "attr" ? attrDeleteRows(t, ids) : await tableOps(t, [{ op: "delete_rows", rows: ids }], `Deleted ${ids.length} row(s)`);
      }
      if (act === "derive") return await deriveFiltered(t);
      if (act === "undo") return t.kind === "attr" ? attrUndo(t) : await tableUndo(t);
      if (act === "save") return askSaveEdits(t);
      if (act === "discard") { if (confirm("Throw away all changes since you started editing? The original stays as it was.")) await finishEdit(t, "discard"); return; }
      if (act === "python") return openPython(t);
    } catch (e) { toast(e, true); }
  }

  // ---- tables on the server
  async function tableOps(t, ops, msg, { silent = false } = {}) {
    const cellsMsg = t.pending?.size ? `Edited ${t.pending.size} cell(s)` : null;
    if (t.pending?.size) {
      const cells = [...t.pending.entries()].map(([k, value]) => { const i = k.indexOf("|"); return { row: +k.slice(0, i), col: k.slice(i + 1), value }; });
      ops = [{ op: "set_cells", cells }, ...ops];
    }
    if (!ops.length) return;
    const r = await api("/api/tables/edit", { method: "POST", json: { path: t.path, ops } });
    t.pending.clear(); t.sel = new Set(); t.data = null; t.stats = null;
    const it = dataItems.find((d) => d.path === t.path);
    if (it) { it.rows = r.rows; it.cols = r.columns.length; saveItems(); renderItems(); }
    const entries = [cellsMsg, ops.length > (cellsMsg ? 1 : 0) ? msg : null].filter(Boolean);
    t.log = [...(t.log || []), ...entries];
    if (entries.length) api("/api/tables/edit/log", { method: "POST", json: { path: t.path, add: entries } }).catch(() => {});
    await loadTab(t);
    if (!silent) toast(msg + (r.notes?.length ? ` · ${r.notes.join(" · ")}` : ""));
  }
  async function tableUndo(t) {
    if (t.pending?.size) { t.pending.clear(); drawTable(t); return toast("Unsaved cell edits discarded"); }
    const r = await api("/api/tables/undo", { method: "POST", json: { path: t.path } });
    t.log = (t.log || []).slice(0, -1);
    api("/api/tables/edit/log", { method: "POST", json: { path: t.path, pop: 1 } }).catch(() => {});
    t.data = null; t.stats = null; t.sel = new Set();
    const it = dataItems.find((d) => d.path === t.path);
    if (it) { it.rows = r.rows; it.cols = r.columns.length; saveItems(); renderItems(); }
    await loadTab(t);
    toast("Undone");
  }
  async function deriveFiltered(t) {
    const n = t.data?.filtered ?? 0;
    if (!t.q) { if (!confirm("No search / filter is active, so all rows will be copied. Continue?")) return; }
    const name = prompt(`Name for the new ${t.kind === "attr" ? "layer" : "table"} (${n.toLocaleString()} rows):`, `${t.title.replace(/ · attributes$/, "").replace(/\.[^.]+$/, "")}_selection`);
    if (!name) return;
    if (t.kind === "attr") {
      const l = editLayer(t), { feats, cols, types } = attrColumns(l);
      const idx = attrFilter(feats, cols, types, t.q);
      addVectorLayer({ type: "FeatureCollection", features: idx.map((i) => JSON.parse(JSON.stringify(feats[i]))) }, name);
      return toast(`New layer “${name}” with ${idx.length} feature(s)`);
    }
    const r = await api("/api/tables/derive", { method: "POST", json: { path: t.path, q: t.q, name } });
    addItem({ kind: "table", name: r.name, path: r.path }, { open: true });
    toast(`New table ${r.name}`);
  }

  // ---- vector attribute tables in the browser
  function attrSnapshot(t, l, label) {
    t.hist ||= [];
    t.hist.push(JSON.stringify(l.geojson.features));
    if (t.hist.length > 12) t.hist.shift();
    if (label) t.log = [...(t.log || []), label];
  }
  function attrChanged(t, l) {
    buildLeaflet(l); restack(); renderContents(); saveLayers();
    t.data = null; t.stats = null; t.sel = new Set();
    loadTab(t);
  }
  function attrUndo(t) {
    const l = editLayer(t);
    if (!l || !t.hist?.length) return;
    l.geojson.features = JSON.parse(t.hist.pop());
    t.log = (t.log || []).slice(0, -1);
    attrChanged(t, l);
    toast("Undone");
  }
  function attrDeleteRows(t, ids) {
    const l = editLayer(t), drop = new Set(ids);
    attrSnapshot(t, l, `Deleted ${ids.length} feature(s)`);
    l.geojson.features = l.geojson.features.filter((_, i) => !drop.has(i));
    attrChanged(t, l);
    toast(`Deleted ${ids.length} feature(s)`);
  }
  function coerceVal(raw, type) {
    if (raw === null || String(raw).trim() === "") return null;
    if (type === "integer" || type === "number") {
      const v = Number(String(raw).replace(/,/g, ""));
      if (!Number.isFinite(v)) throw new Error(`“${raw}” is not a number`);
      return type === "integer" ? Math.round(v) : v;
    }
    if (type === "boolean") return /^(1|true|yes|y)$/i.test(String(raw).trim());
    return String(raw);
  }
  function attrRenameKey(props, oldK, newK) {   // keeps the column order
    const out = {};
    for (const [k, v] of Object.entries(props || {})) out[k === oldK ? newK : k] = v;
    return out;
  }
  function attrFieldOp(t, op) {
    const l = editLayer(t), feats = l.geojson.features;
    attrSnapshot(t, l, op.op === "rename_field" ? `Renamed field ${op.old} → ${op.new}` : op.op === "delete_field" ? `Deleted field ${op.name}` : `Converted ${op.column} to ${op.type}`);
    if (op.op === "rename_field") feats.forEach((f) => { f.properties = attrRenameKey(f.properties, op.old, op.new); });
    else if (op.op === "delete_field") feats.forEach((f) => { if (f.properties) delete f.properties[op.name]; });
    else if (op.op === "cast") feats.forEach((f) => {
      const v = f.properties?.[op.column];
      if (v === undefined) return;
      f.properties[op.column] = op.type === "text" ? (v == null ? null : String(v)) : op.type === "boolean" ? (v == null ? null : /^(1|true|yes|y)$/i.test(String(v)))
        : (v == null || v === "" || !Number.isFinite(Number(v)) ? null : op.type === "integer" ? Math.trunc(Number(v)) : Number(v));
    });
    attrChanged(t, l);
  }

  // ---- inline cell editing, selection, column menus
  function wireEditing(t, d, content) {
    const ckAll = $("[data-ckall]", content);
    const syncAll = () => { if (ckAll) ckAll.checked = d.row_ids.length > 0 && d.row_ids.every((r) => t.sel.has(r)); };
    $$("[data-ck]", content).forEach((cb) => cb.onchange = () => {
      const rid = +cb.closest("tr").dataset.rid;
      cb.checked ? t.sel.add(rid) : t.sel.delete(rid);
      cb.closest("tr").classList.toggle("picked", cb.checked);
      syncAll(); updateEditBar(t);
    });
    if (ckAll) ckAll.onchange = () => {
      d.row_ids.forEach((r) => ckAll.checked ? t.sel.add(r) : t.sel.delete(r));
      $$("[data-ck]", content).forEach((cb) => { cb.checked = ckAll.checked; cb.closest("tr").classList.toggle("picked", ckAll.checked); });
      updateEditBar(t);
    };
    syncAll();
    $$("td[data-c]", content).forEach((td) => td.ondblclick = () => startCellEdit(t, d, td));
    $$("[data-colmenu]", content).forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      const col = b.dataset.colmenu, r = b.getBoundingClientRect(), type = d.types[d.columns.indexOf(col)];
      showMenu(`${col} (${type})`, [
        ["Calculate values…", () => openCalc(t, { mode: "update", column: col })],
        ["Rename…", () => renameField(t, col)],
        "-",
        type !== "number" ? ["Convert to decimal number", () => castField(t, col, "number")] : null,
        type !== "integer" ? ["Convert to whole number", () => castField(t, col, "integer")] : null,
        type !== "text" ? ["Convert to text", () => castField(t, col, "text")] : null,
        "-",
        ["Delete field", () => deleteField(t, col), "danger"],
      ].filter(Boolean), r.left, r.bottom + 2);
    });
  }
  function startCellEdit(t, d, td) {
    if (td.querySelector("input")) return;
    const tr = td.closest("tr"), rid = +tr.dataset.rid, i = +td.dataset.c, col = d.columns[i], type = d.types[i];
    const k = d.row_ids.indexOf(rid), key = `${rid}|${col}`;
    const cur = t.pending?.has(key) ? t.pending.get(key) : d.rows[k][i];
    td.classList.add("cell-edit");
    td.innerHTML = `<input value="${esc(cur ?? "")}" ${type === "integer" || type === "number" ? 'inputmode="decimal"' : ""}>`;
    const inp = $("input", td);
    inp.focus(); inp.select();
    let done = false;
    const finish = (save, move) => {
      if (done) return;
      done = true;
      td.classList.remove("cell-edit");
      const raw = inp.value;
      if (save && String(cur ?? "") !== raw) {
        try {
          const v = coerceVal(raw, type);
          if (t.kind === "attr") {
            const l = editLayer(t);
            attrSnapshot(t, l, `Edited ${col} of feature ${rid + 1}`);
            const f = l.geojson.features[rid];
            f.properties = { ...(f.properties || {}), [col]: v };
            d.rows[k][i] = v;
            buildLeaflet(l); restack(); saveLayers();
          } else {
            t.pending.set(key, raw.trim() === "" ? null : raw);
            td.classList.add("dirty");
          }
        } catch (e) { toast(e, true); }
      }
      const shown = t.pending?.has(key) ? t.pending.get(key) : d.rows[k][i];
      td.innerHTML = fmtCell(shown, type);
      updateEditBar(t);
      if (move) {   // Tab / Enter: continue in the next cell
        const next = move === "down" ? tr.nextElementSibling?.querySelector(`td[data-c="${i}"]`) : (td.nextElementSibling || tr.nextElementSibling?.querySelector('td[data-c="0"]'));
        if (next?.dataset.c !== undefined) startCellEdit(t, d, next);
      }
    };
    inp.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true, "down"); }
      else if (e.key === "Tab") { e.preventDefault(); finish(true, "right"); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    };
    inp.onblur = () => finish(true);
  }
  async function renameField(t, col) {
    const nw = prompt(`New name for “${col}”:`, col);
    if (!nw || nw.trim() === col) return;
    if (t.data.columns.includes(nw.trim())) return toast(`There is already a field called ${nw.trim()}`, true);
    try {
      if (t.kind === "attr") { attrFieldOp(t, { op: "rename_field", old: col, new: nw.trim() }); toast(`Renamed to ${nw.trim()}`); }
      else await tableOps(t, [{ op: "rename_field", old: col, new: nw.trim() }], `Renamed field ${col} → ${nw.trim()}`);
      if (t.sort === col) t.sort = null;
    } catch (e) { toast(e, true); }
  }
  async function deleteField(t, col) {
    if (!confirm(`Delete the field “${col}”? (Undo is available.)`)) return;
    try {
      if (t.kind === "attr") { attrFieldOp(t, { op: "delete_field", name: col }); toast(`Deleted ${col}`); }
      else await tableOps(t, [{ op: "delete_field", name: col }], `Deleted field ${col}`);
      if (t.sort === col) t.sort = null;
    } catch (e) { toast(e, true); }
  }
  async function castField(t, col, type) {
    try {
      if (t.kind === "attr") { attrFieldOp(t, { op: "cast", column: col, type }); toast(`${col} converted`); }
      else await tableOps(t, [{ op: "cast", column: col, type }], `Converted ${col} to ${type}`);
    } catch (e) { toast(e, true); }
  }

  async function restoreTable(it) {
    if (!confirm(`Restore the previous version of ${it.name}?\n\nThe version saved before the last change comes back (the current one is replaced).`)) return;
    try {
      const r = await api("/api/tables/restore", { method: "POST", json: { path: it.path } });
      it.rows = r.rows; it.cols = r.columns.length; saveItems(); renderItems();
      const t = vw.tabs.find((x) => x.path === it.path);
      if (t) { t.data = null; t.stats = null; if (vw.active === t.key) loadTab(t); }
      toast("Previous version restored");
    } catch (e) { toast(e, true); }
  }

  // ---- Python editor: change the table / attributes with pandas, in a separate process
  const pyx = { t: null };
  function pyExamples(t) {
    const cols = t.data?.columns || [], types = t.data?.types || [];
    const num = cols.find((c, i) => types[i] === "number" || types[i] === "integer") || "value";
    const txt = cols.find((c, i) => types[i] === "text") || "name";
    const q = (c) => JSON.stringify(c);
    const ex = [
      ["Add a calculated column", `df["${num}_x2"] = df[${q(num)}] * 2`],
      ["Classify values into groups", `df["${num}_class"] = np.where(df[${q(num)}] >= df[${q(num)}].median(), "high", "low")`],
      ["Bins / ranges", `df["${num}_bin"] = pd.cut(df[${q(num)}], bins=5).astype(str)`],
      ["Keep only some rows", `df = df[df[${q(num)}] > 0]`],
      ["Remove duplicate rows", `df = df.drop_duplicates()`],
      ["Fill missing values", `df[${q(num)}] = df[${q(num)}].fillna(df[${q(num)}].median())`],
      ["Rename columns", `df = df.rename(columns={${q(num)}: "${num}_new"})`],
      ["Normalise a column to 0–1", `col = ${q(num)}\ndf[col + "_norm"] = (df[col] - df[col].min()) / (df[col].max() - df[col].min())`],
      ["Clean up text", `df[${q(txt)}] = df[${q(txt)}].astype(str).str.strip().str.title()`],
      ["Row-by-row function", `def label(row):\n    if row[${q(num)}] > 10:\n        return "big"\n    return "small"\n\ndf["size"] = df.apply(label, axis=1)`],
      ["Summary statistics (print only)", `print(df.describe(include="all").T)`],
      ["Group statistics (print only)", `print(df.groupby(${q(txt)})[${q(num)}].agg(["count", "mean", "min", "max"]))`],
    ];
    if (t.kind === "attr") ex.unshift(
      ["Area of each polygon (hectares)", `df["area_ha"] = [round(area_ha(g), 3) for g in df["geometry"]]`],
      ["Perimeter / length (metres)", `df["perimeter_m"] = [round(perimeter_m(g), 1) for g in df["geometry"]]`],
      ["Centroid longitude / latitude", `xy = [centroid_xy(g) for g in df["geometry"]]\ndf["lon"] = [p[0] for p in xy]\ndf["lat"] = [p[1] for p in xy]`]);
    return ex;
  }
  function openPython(t) {
    pyx.t = t;
    const isAttr = t.kind === "attr";
    $("#py-vars").innerHTML = `<code>df</code> is the ${isAttr ? "attribute table" : "table"} as a pandas DataFrame (${(t.data?.total ?? 0).toLocaleString()} rows): change it, or assign a new table to <code>df</code>. ` +
      `Available: <code>pd</code>, <code>np</code>, <code>math</code>, <code>re</code>, <code>datetime</code>. Use <code>print(…)</code> to see results.` +
      (isAttr ? ` Geometries: <code>df["geometry"]</code> (shapely, read-only) with <code>area_m2(g)</code>, <code>area_ha(g)</code>, <code>perimeter_m(g)</code>, <code>length_m(g)</code>, <code>centroid_xy(g)</code>. Rows you drop delete those features.` : "");
    const ex = pyExamples(t);
    $("#py-ex").innerHTML = `<option value="">Insert an example…</option>` + ex.map(([k], i) => `<option value="${i}">${esc(k)}</option>`).join("");
    $("#py-ex").onchange = (e) => {
      const it = ex[+e.target.value];
      if (!it) return;
      const ta = $("#py-code");
      ta.value = (ta.value.trim() ? ta.value.replace(/\s*$/, "\n\n") : "") + `# ${it[0]}\n${it[1]}\n`;
      e.target.value = "";
      ta.focus();
    };
    $("#py-code").value = prefs.get("py-code", "") || `# ${ex[0][0]}\n${ex[0][1]}\n`;
    $("#py-out").textContent = ""; $("#py-out").classList.add("hidden");
    $("#py-preview").innerHTML = ""; $("#py-summary").innerHTML = "";
    $("#py-apply").disabled = !t.edit;
    $("#py-apply").title = t.edit ? "" : "Turn on ✎ Edit first";
    $("#dlg-py").showModal();
    setTimeout(() => $("#py-code").focus(), 50);
  }
  $("#py-code").addEventListener("keydown", (e) => {   // Tab indents instead of leaving the editor
    if (e.key !== "Tab") return;
    e.preventDefault();
    const ta = e.target, a = ta.selectionStart, b = ta.selectionEnd;
    ta.value = ta.value.slice(0, a) + "    " + ta.value.slice(b);
    ta.selectionStart = ta.selectionEnd = a + 4;
  });
  async function runPython(apply, btn) {
    const t = pyx.t, code = $("#py-code").value;
    if (!code.trim()) return toast("Write some Python first", true);
    prefs.set("py-code", code);
    const isAttr = t.kind === "attr";
    let body = { code, apply };
    if (isAttr) {
      const l = editLayer(t), { feats, cols } = attrColumns(l);
      body = { code, apply: false, columns: Object.fromEntries(cols.map((c) => [c, feats.map((f) => f.properties?.[c] ?? null)])),
               geometries: feats.map((f) => f.geometry || null), n: feats.length };
    } else {
      if (apply && t.pending?.size) await tableOps(t, [], `Edited ${t.pending.size} cell(s)`, { silent: true });
      body.path = t.path;
    }
    await busy(btn, apply ? "Running…" : "Testing…", async () => {
      let r;
      try {
        const job = await api("/api/python/run", { method: "POST", json: body });
        r = (await trackJob(job, { title: apply ? "Running Python" : "Testing Python" })).result;
      } catch (e) { if (notCancelled(e)) toast(e, true); return; }
      const out = $("#py-out");
      out.textContent = r.ok ? (r.output || "(no printed output)") : r.error;
      out.classList.toggle("err", !r.ok);
      out.classList.remove("hidden");
      if (!r.ok) { $("#py-summary").innerHTML = `<span style="color:var(--err)">⚠ The script failed. Nothing was changed.</span>`; $("#py-preview").innerHTML = ""; return; }
      const m = r.meta;
      $("#py-summary").innerHTML = `Rows <b>${m.before.rows.toLocaleString()} → ${m.after.rows.toLocaleString()}</b> · columns ${m.before.columns.length} → ${m.after.columns.length}` +
        (m.added.length ? ` · added <b>${m.added.map(esc).join(", ")}</b>` : "") + (m.removed.length ? ` · removed <b>${m.removed.map(esc).join(", ")}</b>` : "") +
        (apply ? "" : ` · <span class="muted">test only: nothing changed</span>`);
      $("#py-preview").innerHTML = `<div class="load-wrap"><table class="data-table"><tr>${r.preview.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>${r.preview.rows.map((row) => `<tr>${row.map((v) => `<td>${fmtCell(v)}</td>`).join("")}</tr>`).join("")}</table></div>`;
      const lines = code.split("\n").map((ln) => ln.trim()).filter((ln) => ln && !ln.startsWith("#"));
      const label = `Python: ${(lines.find((ln) => /^df(\[|\s*=)/.test(ln)) || lines[0] || "script").slice(0, 70)}${lines.length > 1 ? ` (+${lines.length - 1} line${lines.length > 2 ? "s" : ""})` : ""}`;
      if (!apply) return;
      if (isAttr) {
        const l = editLayer(t), feats = l.geojson.features, res = r.result;
        const newFeats = [], noGeom = res.index.filter((i) => i == null || !feats[i]).length;
        res.index.forEach((fi, k) => {
          if (fi == null || !feats[fi]) return;
          const props = {};
          res.columns.forEach((c, j) => { props[c] = res.rows[k][j]; });
          newFeats.push({ ...feats[fi], properties: props });
        });
        attrSnapshot(t, l, label);
        l.geojson.features = newFeats;
        attrChanged(t, l);
        if (noGeom) toast(`${noGeom} new row(s) have no geometry and were skipped`, true);
      } else {
        t.log = [...(t.log || []), label];
        api("/api/tables/edit/log", { method: "POST", json: { path: t.path, add: [label] } }).catch(() => {});
        t.data = null; t.stats = null; t.sel = new Set();
        await loadTab(t);
      }
      toast("Python applied. Remember to Save.");
    });
  }
  $("#py-test").onclick = (e) => runPython(false, e.currentTarget);
  $("#py-apply").onclick = (e) => runPython(true, e.currentTarget);

  // ---- field calculator dialog (shared by tables and vector layers)
  const fcx = { t: null, fns: null, timer: 0 };
  async function openCalc(t, { mode = "new", column = null, title } = {}) {
    if (!t.data) await loadTab(t);
    fcx.t = t;
    fcx.fns ||= await api("/api/fields/functions").catch(() => ({ functions: {}, geometry: [] }));
    const cols = t.data.columns, isAttr = t.kind === "attr";
    $("#fc-title").textContent = title || "Field calculator";
    $("#fc-name").value = "";
    $("#fc-type").value = "auto";
    $("#fc-col").innerHTML = cols.map((c) => `<option ${c === column ? "selected" : ""}>${esc(c)}</option>`).join("");
    $$('input[name="fct"]').forEach((r) => r.checked = r.value === (cols.length ? mode : "new"));
    $$('input[name="fct"]')[1].disabled = !cols.length;
    const n = t.data.filtered, all = t.data.total;
    $("#fc-only").checked = false;
    $("#fc-only").disabled = !t.q;
    $("#fc-only-n").textContent = t.q ? `${n.toLocaleString()} of ${all.toLocaleString()} rows match “${t.q}”` : "no search / filter active";
    $("#fc-expr").value = mode === "update" && column ? `[${column}]` : "";
    $("#fc-fields").innerHTML = cols.map((c, i) => `<button class="fc-chip" data-ins="[${esc(c)}]" title="${esc(t.data.types[i])}">${esc(c)} <i>${TYPE_TAG[t.data.types[i]] || ""}</i></button>`).join("");
    $("#fc-geom-wrap").classList.toggle("hidden", !isAttr);
    $("#fc-geom").innerHTML = [["$area", "area in m²"], ["$area_ha", "area in hectares"], ["$area_km2", "area in km²"], ["$perimeter", "perimeter in m"],
      ["$length", "line length in m"], ["$x", "centroid longitude"], ["$y", "centroid latitude"], ["$id", "row number"]]
      .map(([g, d]) => `<button class="fc-chip" data-ins="${g}" title="${d}">${g}</button>`).join("");
    $("#fc-funcs").innerHTML = Object.entries(fcx.fns.functions).map(([k, h]) => `<button class="fc-chip" data-ins="${esc(k)}()" title="${esc(h)}">${esc(k)}</button>`).join("");
    $("#fc-examples").innerHTML = (isAttr ? ["round($area_ha, 2)", "$perimeter / 1000", "iif($area_ha > 1, 'large', 'small')"] : [])
      .concat(cols.length >= 2 && t.data.types.filter((x) => x === "number" || x === "integer").length >= 2
        ? [`[${t.data.columns.find((c, i) => t.data.types[i] !== "text")}] * 2`] : [])
      .concat([`upper([${cols.find((c, i) => t.data.types[i] === "text") || cols[0]}])`, `concat([${cols[0]}], " - ", [${cols[cols.length - 1]}])`, `iif([${cols.find((c, i) => t.data.types[i] !== "text") || cols[0]}] > 0, "yes", "no")`])
      .map((e) => `<button class="fc-chip ex" data-ex="${esc(e)}">${esc(e)}</button>`).join("");
    $$("#dlg-calc [data-ins]").forEach((b) => b.onclick = () => insertAtCursor($("#fc-expr"), b.dataset.ins.endsWith("()") ? b.dataset.ins.slice(0, -1) : b.dataset.ins + " "));
    $$("#dlg-calc [data-ex]").forEach((b) => b.onclick = () => { $("#fc-expr").value = b.dataset.ex; calcPreview(); });
    syncCalcTarget();
    $("#dlg-calc").showModal();
    calcPreview();
    setTimeout(() => (mode === "new" ? $("#fc-name") : $("#fc-expr")).focus(), 50);
  }
  function insertAtCursor(ta, text) {
    const a = ta.selectionStart ?? ta.value.length, b = ta.selectionEnd ?? a;
    ta.value = ta.value.slice(0, a) + text + ta.value.slice(b);
    ta.focus(); ta.selectionStart = ta.selectionEnd = a + text.length;
    calcPreview();
  }
  function syncCalcTarget() {
    const isNew = $('input[name="fct"]:checked')?.value === "new";
    $("#fc-name").disabled = !isNew; $("#fc-type").disabled = !isNew; $("#fc-col").disabled = isNew;
    $("#fc-hint").textContent = isNew ? "Leave the expression empty to add an empty field you can fill in by hand." : "";
  }
  $$('input[name="fct"]').forEach((r) => r.onchange = syncCalcTarget);
  $("#fc-expr").oninput = () => calcPreview();
  function attrCalcPayload(l, expr, idx) {
    const { feats, cols } = attrColumns(l);
    const pick = idx || feats.map((_, i) => i);
    const columns = Object.fromEntries(cols.map((c) => [c, pick.map((i) => feats[i].properties?.[c] ?? null)]));
    return { expression: expr, columns, geometries: expr.includes("$") ? pick.map((i) => feats[i].geometry || null) : null, n: pick.length };
  }
  function calcPreview() {
    clearTimeout(fcx.timer);
    fcx.timer = setTimeout(async () => {
      const t = fcx.t, expr = $("#fc-expr").value.trim(), box = $("#fc-preview");
      if (!expr) { box.innerHTML = `<span class="muted">Preview of the first rows appears here.</span>`; return; }
      try {
        let r;
        if (t.kind === "attr") {
          const l = editLayer(t);
          r = await api("/api/fields/calc", { method: "POST", json: attrCalcPayload(l, expr, l.geojson.features.slice(0, 8).map((_, i) => i)) });
        } else r = await api(`/api/tables/calc-preview?path=${encodeURIComponent(t.path)}&expression=${encodeURIComponent(expr)}`);
        box.innerHTML = `<span class="fc-ok">✓ ${esc(r.type)}</span> ${r.values.map((v) => `<code>${v == null ? "–" : esc(typeof v === "number" ? String(+v.toPrecision(8)) : String(v))}</code>`).join(" ")}`;
      } catch (e) { box.innerHTML = `<span style="color:var(--err)">⚠ ${esc(e.message)}</span>`; }
    }, 350);
  }
  $("#fc-apply").onclick = (e) => busy(e.currentTarget, "Calculating…", async () => {
    const t = fcx.t, isNew = $('input[name="fct"]:checked').value === "new";
    const expr = $("#fc-expr").value.trim(), name = isNew ? $("#fc-name").value.trim() : $("#fc-col").value;
    const only = $("#fc-only").checked && t.q;
    if (!name) return toast("Give the new field a name", true);
    if (isNew && t.data.columns.includes(name)) return toast(`There is already a field called ${name}`, true);
    if (!isNew && !expr) return toast("Type an expression", true);
    try {
      if (t.kind === "attr") {
        const l = editLayer(t), { feats, cols, types } = attrColumns(l);
        const idx = only ? attrFilter(feats, cols, types, t.q) : feats.map((_, i) => i);
        let vals = idx.map(() => null), typ = $("#fc-type").value;
        if (expr) { const r = await api("/api/fields/calc", { method: "POST", json: attrCalcPayload(l, expr, idx) }); vals = r.values; if (typ === "auto") typ = r.type; }
        if (typ !== "auto" && expr) vals = vals.map((v) => { try { return coerceVal(v, typ); } catch { return null; } });
        attrSnapshot(t, l, isNew ? `Added field ${name}${expr ? ` = ${expr}` : ""}` : `Calculated ${name} = ${expr}`);
        if (isNew) feats.forEach((f) => { f.properties = { ...(f.properties || {}), [name]: null }; });
        idx.forEach((fi, k) => { feats[fi].properties[name] = vals[k]; });
        attrChanged(t, l);
      } else {
        await tableOps(t, [isNew ? { op: "add_field", name, expression: expr, type: $("#fc-type").value, ...(only ? { q: t.q } : {}) }
                                 : { op: "calc", column: name, expression: expr, ...(only ? { q: t.q } : {}) }],
                       isNew ? `Added field ${name}${expr ? ` = ${expr}` : ""}` : `Calculated ${name} = ${expr}${only ? " (filtered rows)" : ""}`);
      }
      $("#dlg-calc").close();
      if (t.kind === "attr") toast(isNew ? `Field ${name} added` : `${name} updated`);
    } catch (ex) { $("#fc-preview").innerHTML = `<span style="color:var(--err)">⚠ ${esc(ex.message)}</span>`; }
  });

  // clicking a row shows it on the map: the feature (attribute tables) or the lon / lat point (tables)
  function clearRowMarker() { rowMarker?.remove(); rowMarker = null; }
  function rowToMap(t, d, rid, row) {
    clearRowMarker();
    const hl = { color: "#facc15", weight: 4, opacity: 1, fillColor: "#facc15", fillOpacity: 0.18 };
    if (t.kind === "attr") {
      const f = getLayer(t.layerId)?.geojson?.features?.[rid];
      if (!f?.geometry) return;
      rowMarker = L.geoJSON(f, { style: () => hl, pointToLayer: (_, ll) => L.circleMarker(ll, { ...hl, radius: 10 }), interactive: false }).addTo(map);
      const b = rowMarker.getBounds();
      if (b.isValid()) map.fitBounds(b, { padding: [60, 60], maxZoom: Math.max(map.getZoom(), 15) });
    } else if (d.lonlat) {
      const lon = row[d.columns.indexOf(d.lonlat[0])], lat = row[d.columns.indexOf(d.lonlat[1])];
      if (typeof lon !== "number" || typeof lat !== "number" || Math.abs(lat) > 90 || Math.abs(lon) > 180) return;
      rowMarker = L.circleMarker([lat, lon], { ...hl, radius: 10, interactive: false }).addTo(map);
      map.setView([lat, lon], Math.max(map.getZoom(), 14));
    }
  }

  // ---- pictures: zoom (wheel) and pan (drag)
  function renderPictureTab(t, body) {
    const it = t.item;
    body.innerHTML = `<div class="vt-bar">
        <span class="vt-pager"><button data-z="fit" title="Fit to panel">Fit</button><button data-z="1" title="Actual size">1:1</button><button data-z="out" title="Zoom out">−</button><button data-z="in" title="Zoom in">+</button></span>
        <span class="vt-count pic-zoom"></span><span class="muted small">${it.width}×${it.height} px · scroll to zoom, drag to pan</span>
        <span class="grow"></span>
        <button class="btn small primary" data-act="place" title="This picture has no coordinates. Stretch it over the current map view to use it as a layer">Place on map</button>
        <a class="btn small" href="/api/pictures/file?path=${encodeURIComponent(t.path)}" download title="Download">⬇</a></div>
      <div class="pic-stage"><img src="/api/pictures/file?path=${encodeURIComponent(t.path)}" alt="${esc(t.title)}" draggable="false"></div>`;
    const stage = $(".pic-stage", body), img = $("img", stage);
    const apply = () => { img.style.transform = `translate(${t.tx}px, ${t.ty}px) scale(${t.z})`; $(".pic-zoom", body).textContent = `${Math.round(t.z * 100)}%`; };
    const fit = () => {
      const r = stage.getBoundingClientRect(), w = img.naturalWidth || it.width, h = img.naturalHeight || it.height;
      t.z = Math.min(r.width / w, r.height / h, 1) || 1; t.tx = (r.width - w * t.z) / 2; t.ty = (r.height - h * t.z) / 2; apply();
    };
    const zoomAt = (f, cx, cy) => { const nz = Math.min(32, Math.max(0.02, t.z * f)); t.tx = cx - (cx - t.tx) * nz / t.z; t.ty = cy - (cy - t.ty) * nz / t.z; t.z = nz; apply(); };
    if (t.z == null) { if (img.complete) fit(); else img.onload = fit; } else apply();
    stage.onwheel = (e) => { e.preventDefault(); const r = stage.getBoundingClientRect(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top); };
    stage.onpointerdown = (e) => {
      stage.setPointerCapture(e.pointerId); stage.classList.add("panning");
      const sx = e.clientX - t.tx, sy = e.clientY - t.ty;
      stage.onpointermove = (ev) => { t.tx = ev.clientX - sx; t.ty = ev.clientY - sy; apply(); };
      stage.onpointerup = stage.onpointercancel = () => { stage.onpointermove = null; stage.classList.remove("panning"); };
    };
    $$("[data-z]", body).forEach((b) => b.onclick = () => {
      const r = stage.getBoundingClientRect();
      if (b.dataset.z === "fit") fit();
      else if (b.dataset.z === "1") zoomAt(1 / t.z, r.width / 2, r.height / 2);
      else zoomAt(b.dataset.z === "in" ? 1.4 : 1 / 1.4, r.width / 2, r.height / 2);
    });
    $('[data-act="place"]', body).onclick = () => placePicture(it);
  }
