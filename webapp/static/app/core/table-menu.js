  // Right-click in the data viewer: on a column header (sort, statistics, copy, rename, calculate, convert, add / delete a
  // field) and on a cell (copy, filter by this value, show on the map, edit the cell, delete the row). Actions that change
  // the table start an edit session first, so nothing is saved until Save (and Undo works).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ data viewer right-click menus
  function wireTableMenus(t, d, content) {
    $$("th[data-col]", content).forEach((th) => th.oncontextmenu = (e) => {
      e.preventDefault();
      columnMenu(t, d, th.dataset.col, e.clientX, e.clientY);
    });
    $$("tbody td[data-c]", content).forEach((td) => td.oncontextmenu = (e) => {
      if (td.querySelector("input")) return;   // a cell being edited keeps the browser's menu (copy / paste)
      e.preventDefault();
      cellMenu(t, d, td, e.clientX, e.clientY);
    });
  }

  // start editing if needed (tables are edited on a working copy; nothing is saved until Save)
  async function ensureEdit(t) {
    if (t.edit) return true;
    try { await startEdit(t); } catch (e) { toast(e, true); return false; }
    toast("Editing started: Save keeps the changes (as they are or as a new copy), Undo takes one back");
    return true;
  }
  // set the search box to a filter, e.g. "crop = rice" (the same syntax as typing it)
  function setFilter(t, q) {
    t.q = q; t.offset = 0; t.stats = null;
    const box = $("#viewer-body .vt-search input");
    if (box) box.value = q;
    if (t.mode !== "rows") { t.mode = "rows"; renderViewer(); } else loadTab(t);
  }
  const filterValue = (v) => typeof v === "number" ? String(v) : `"${String(v).replace(/"/g, "")}"`;
  const rowText = (d, row) => d.columns.map((c, i) => `${c}\t${row[i] ?? ""}`).join("\n");

  function columnMenu(t, d, col, x, y) {
    const i = d.columns.indexOf(col), type = d.types[i], num = type === "integer" || type === "number";
    const page = d.rows.map((r) => r[i]);
    const ed = (fn) => async () => { if (await ensureEdit(t)) fn(); };
    showMenu(`${col} · ${type}`, [
      [num ? "Sort smallest first" : "Sort A → Z", () => { t.sort = col; t.desc = false; t.offset = 0; loadTab(t); }],
      [num ? "Sort largest first" : "Sort Z → A", () => { t.sort = col; t.desc = true; t.offset = 0; loadTab(t); }],
      t.sort === col ? ["Remove sorting", () => { t.sort = null; t.desc = false; loadTab(t); }] : null,
      ["Column statistics", () => { t.mode = "stats"; renderViewer(); }],
      t.q ? ["Clear the filter", () => setFilter(t, "")] : null,
      "-",
      ["Copy the column name", () => copyText(col, `Copied “${col}”`)],
      [`Copy the values shown (${page.length})`, () => copyText(page.map((v) => v ?? "").join("\n"), `${page.length} values copied, one per line`)],
      t.kind !== "attr" && !d.lonlat && num ? ["Show points on map…", () => chooseXY(t.item || { name: t.title, path: t.path })] : null,
      "-",
      ["Rename…", ed(() => renameField(t, col))],
      ["Calculate values…", ed(() => openCalc(t, { mode: "update", column: col }))],
      type !== "number" ? ["Convert to decimal number", ed(() => castField(t, col, "number"))] : null,
      type !== "integer" ? ["Convert to whole number", ed(() => castField(t, col, "integer"))] : null,
      type !== "text" ? ["Convert to text", ed(() => castField(t, col, "text"))] : null,
      ["Add a field…", ed(() => openCalc(t, { mode: "new", title: "Add field" }))],
      "-",
      ["Delete this field", ed(() => deleteField(t, col)), "danger"],
    ].filter(Boolean), x, y);
  }

  function cellMenu(t, d, td, x, y) {
    const tr = td.closest("tr"), rid = +tr.dataset.rid, k = d.row_ids.indexOf(rid), i = +td.dataset.c;
    const col = d.columns[i], type = d.types[i], v = d.rows[k][i], row = d.rows[k], num = type === "integer" || type === "number";
    const has = v !== null && v !== undefined && v !== "";
    const show = has ? (typeof v === "number" ? String(+v.toPrecision(7)) : String(v)) : "";
    const short = show.length > 24 ? show.slice(0, 22) + "…" : show;
    const onMap = t.kind === "attr" || d.lonlat;
    const picked = t.sel?.size > 1 && t.sel.has(rid) ? [...t.sel] : [rid];
    const delRows = async () => {
      if (!confirm(`Delete ${picked.length === 1 ? "this row" : `the ${picked.length} selected rows`}? (Undo is available until you save.)`)) return;
      if (!(await ensureEdit(t))) return;
      try {
        if (t.kind === "attr") attrDeleteRows(t, picked);
        else await tableOps(t, [{ op: "delete_rows", rows: picked }], `Deleted ${picked.length} row(s)`);
      } catch (e) { toast(e, true); }
    };
    const editCell = async () => {
      if (!(await ensureEdit(t))) return;
      for (let n = 0; n < 40; n++) {   // the table is redrawn for editing: wait for the cell, then open it
        const cell = $(`#viewer-body tr[data-rid="${rid}"] td[data-c="${i}"]`);
        if (cell && t.data) return startCellEdit(t, t.data, cell);
        await new Promise((r) => setTimeout(r, 100));
      }
      toast("Double-click the cell to edit it");
    };
    showMenu(has ? `${col}: ${short}` : `${col}: (empty)`, [
      has ? ["Copy the value", () => copyText(show, "Value copied")] : null,
      ["Copy the row", () => copyText(rowText(d, row), "Row copied (column ⇥ value per line)")],
      "-",
      has ? [`Show only rows where ${col} = ${short}`, () => setFilter(t, `${col} = ${filterValue(v)}`)] : null,
      has ? [`Hide rows where ${col} = ${short}`, () => setFilter(t, `${col} != ${filterValue(v)}`)] : null,
      has && num ? [`Show rows where ${col} > ${short}`, () => setFilter(t, `${col} > ${v}`)] : null,
      has && num ? [`Show rows where ${col} < ${short}`, () => setFilter(t, `${col} < ${v}`)] : null,
      t.q ? ["Clear the filter", () => setFilter(t, "")] : null,
      onMap ? "-" : null,
      onMap ? ["Show this row on the map", () => { t.selRow = rid; rowToMap(t, d, rid, [...row]); }] : null,
      "-",
      ["Edit this cell", editCell],
      t.kind !== "attr" ? ["Add a row", async () => { if (await ensureEdit(t)) editAction(t, "addrow"); }] : null,
      [picked.length > 1 ? `Delete the ${picked.length} selected rows` : "Delete this row", delRows, "danger"],
    ].filter(Boolean), x, y);
  }
