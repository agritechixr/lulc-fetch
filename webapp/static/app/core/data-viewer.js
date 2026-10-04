  // Data viewer under the map: tables, attribute tables, pictures.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ data viewer (bottom panel): tables, attribute tables, pictures
  const vw = { tabs: [], active: null };
  let vwSeq = 0, rowMarker = null;
  const viewerOpen = () => document.body.classList.contains("viewer-open");
  function setViewer(show) {
    document.body.classList.toggle("viewer-open", show);
    if (show) renderViewer();
    syncMenuChecks();
    mapResized();
  }
  function openTab(tab) {
    let t = vw.tabs.find((x) => x.key === tab.key);
    if (!t) { t = { offset: 0, limit: prefs.get("vw-limit", 100), q: "", sort: null, desc: false, mode: "rows", ...tab }; vw.tabs.push(t); t.data = t.stats = null; }
    else if (tab.mode && tab.mode !== t.mode) t.mode = tab.mode;
    vw.active = t.key;
    if (!viewerOpen()) setViewer(true); else renderViewer();
    return t;
  }
  function closeTab(key, force = false) {
    const i = vw.tabs.findIndex((t) => t.key === key);
    if (i < 0) return;
    const tt = vw.tabs[i];
    if (tt.edit && !force) {
      if (isDirty(tt)) { vw.active = tt.key; renderViewer(); askSaveEdits(tt); return; }
      finishEdit(tt, "discard").catch(() => {});
      return;
    }
    if (tt.edit && force && tt.kind === "attr") { const l = getLayer(tt.layerId); if (l && l._original !== undefined) { l.geojson.features = JSON.parse(l._original); delete l._original; } }
    vw.tabs.splice(i, 1);
    if (vw.active === key) vw.active = vw.tabs[Math.max(0, i - 1)]?.key || null;
    clearRowMarker();
    if (!vw.tabs.length) setViewer(false); else renderViewer();
  }
  const activeTab = () => vw.tabs.find((t) => t.key === vw.active) || null;
  const TAB_IC = { table: "ml", attr: "vector", picture: "image" };

  function renderViewer() {
    $("#viewer-tabs").innerHTML = vw.tabs.map((t) => `<div class="vtab ${t.key === vw.active ? "on" : ""}" data-key="${esc(t.key)}" title="${esc(t.title)}">
      <span class="vtab-ic">${svg(TAB_IC[t.kind])}</span><span class="vtab-t">${esc(t.title)}</span><button class="vtab-x" title="Close">×</button></div>`).join("");
    $$("#viewer-tabs .vtab").forEach((el) => {
      el.onclick = (e) => { if (e.target.closest(".vtab-x")) return closeTab(el.dataset.key); vw.active = el.dataset.key; renderViewer(); };
      el.onauxclick = (e) => { if (e.button === 1) closeTab(el.dataset.key); };
    });
    const t = activeTab(), body = $("#viewer-body");
    if (!t) {
      body.innerHTML = `<div class="vw-empty">${svg("ml")}<b>Data viewer</b><span>Double-click a table in <b>Contents ▸ Tabular data</b> to open it here, right-click a vector layer for its <b>attribute table</b>, or open a picture. Drag the top edge to resize.</span></div>`;
      return;
    }
    if (t.kind === "picture") return renderPictureTab(t, body);
    renderTableTab(t, body);
  }

  // ---- tables (server-side paging for files, client-side for vector attribute tables)
  function renderTableTab(t, body) {
    const isAttr = t.kind === "attr";
    body.innerHTML = `<div class="vt-bar">
        <label class="vt-search" title="Type to search every column. Or filter one column: yield > 4 · crop = rice · id != 3">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>
          <input type="search" placeholder="Search…  or  column > value" value="${esc(t.q)}"></label>
        <div class="seg small vt-mode"><button data-mode="rows" class="${t.mode === "rows" ? "active" : ""}">Rows</button><button data-mode="stats" class="${t.mode === "stats" ? "active" : ""}">Column statistics</button></div>
        <span class="vt-count"></span>
        <span class="vt-pager"><button data-pg="first" title="First page">«</button><button data-pg="prev" title="Previous page">‹</button><button data-pg="next" title="Next page">›</button><button data-pg="last" title="Last page">»</button>
          <select class="vt-limit" title="Rows per page">${[50, 100, 250, 1000].map((n) => `<option ${n === t.limit ? "selected" : ""}>${n}</option>`).join("")}</select></span>
        <span class="grow"></span>
        <button class="btn small vt-edit-btn ${t.edit ? "on" : ""}" data-act="edit" title="Edit: add / calculate / rename / delete fields, edit cells, delete rows">✎ Edit</button>
        ${isAttr ? `<button class="btn small" data-act="zoomlayer">Zoom to layer</button>`
          : `<button class="btn small hidden" data-act="points" title="Add the rows as points on the map (uses the current search)">Show on map</button>
             <button class="btn small" data-act="train" title="Open Train a model with this table">Train a model</button>
             <a class="btn small" href="/api/tables/file?path=${encodeURIComponent(t.path)}" download title="Download the file">⬇</a>`}
      </div>
      <div class="vt-editbar ${t.edit ? "" : "hidden"}">
        <button class="btn small" data-ed="addfield" title="Add a new field (column), empty or calculated">+ Add field</button>
        <button class="btn small" data-ed="calc" title="Calculate values with an expression (new or existing field)">ƒx Field calculator</button>
        ${isAttr ? "" : `<button class="btn small" data-ed="addrow" title="Append an empty row at the end">+ Add row</button>`}
        <button class="btn small danger" data-ed="delrows" disabled title="Delete the ticked rows">Delete selected</button>
        <button class="btn small" data-ed="derive" title="Save the rows matching the current search as a new ${isAttr ? "layer" : "table"}">New ${isAttr ? "layer" : "table"} from filtered rows</button>
        <span class="grow"></span>
        <button class="btn small" data-ed="python" title="Change the ${isAttr ? "attributes" : "table"} with Python (pandas)">🐍 Python</button>
        <span class="vt-pending"><span class="vt-pending-n">No changes yet</span></span>
        <button class="btn small" data-ed="undo" title="Undo the last change">↶ Undo</button>
        <button class="btn small" data-ed="discard" title="Throw away all changes since you started editing">Discard</button>
        <button class="btn small primary" data-ed="save" title="Save the changes: over the original or as a new ${isAttr ? "layer" : "table"}">💾 Save…</button>
        <span class="hint vt-edit-hint" style="margin:0">Double-click a cell to edit · right-click a cell or column for more</span>
      </div>
      <div class="vt-wrap"><div class="vt-content"></div></div>`;
    t.sel ||= new Set(); t.pending ||= new Map();
    $('[data-act="edit"]', body).onclick = () => toggleEdit(t);
    $$("[data-ed]", body).forEach((b) => b.onclick = () => editAction(t, b.dataset.ed));
    updateEditBar(t);
    const input = $(".vt-search input", body);
    let deb = 0;
    input.oninput = () => { clearTimeout(deb); deb = setTimeout(() => { t.q = input.value; t.offset = 0; t.stats = null; loadTab(t); }, 300); };
    $$(".vt-mode [data-mode]", body).forEach((b) => b.onclick = () => { t.mode = b.dataset.mode; renderViewer(); });
    $$(".vt-pager [data-pg]", body).forEach((b) => b.onclick = () => {
      const n = t.data?.filtered ?? 0, last = Math.max(0, Math.floor((n - 1) / t.limit) * t.limit);
      t.offset = { first: 0, prev: Math.max(0, t.offset - t.limit), next: Math.min(last, t.offset + t.limit), last }[b.dataset.pg];
      loadTab(t);
    });
    $(".vt-limit", body).onchange = (e) => { t.limit = +e.target.value; prefs.set("vw-limit", t.limit); t.offset = 0; loadTab(t); };
    $('[data-act="train"]', body)?.addEventListener("click", () => { switchTool("ml"); openMlSub("train"); refreshTrainTables(t.path); });
    $('[data-act="points"]', body)?.addEventListener("click", () => tablePoints(t.item || { name: t.title, path: t.path }, t.q));
    $('[data-act="zoomlayer"]', body)?.addEventListener("click", () => zoomTo(getLayer(t.layerId)));
    body.classList.toggle("stats-mode", t.mode === "stats");
    if ((t.mode === "rows" && t.data) || (t.mode === "stats" && t.stats)) drawTable(t); else loadTab(t);
  }

  async function loadTab(t) {
    const req = ++vwSeq;
    t.req = req;
    const content = vw.active === t.key && $("#viewer-body .vt-content");
    if (content) content.classList.add("loading");
    try {
      if (t.kind === "attr") {
        const l = getLayer(t.layerId);
        if (!l) throw new Error("The layer was removed");
        if (t.mode === "stats") t.stats = attrStats(l, t.q); else t.data = attrPage(l, t);
      } else if (t.mode === "stats") {
        t.stats = await api(`/api/tables/stats?path=${encodeURIComponent(t.path)}`);
      } else {
        t.data = await api(`/api/tables/rows?path=${encodeURIComponent(t.path)}&offset=${t.offset}&limit=${t.limit}&q=${encodeURIComponent(t.q)}${t.sort ? `&sort=${encodeURIComponent(t.sort)}&desc=${t.desc}` : ""}`);
      }
      t.error = null;
    } catch (e) { t.error = e.message; }
    if (t.req !== req || vw.active !== t.key) return;
    drawTable(t);
  }

  const fmtCell = (v, type) => {
    if (v === null || v === undefined || v === "") return '<span class="nul">–</span>';
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(+v.toPrecision(7));
    if (typeof v === "object") return esc(JSON.stringify(v));
    return esc(v);
  };
  const TYPE_TAG = { integer: "123", number: "1.5", text: "abc", boolean: "y/n", date: "date" };

  function drawTable(t) {
    const body = $("#viewer-body"), content = $(".vt-content", body);
    if (!content) return;
    content.classList.remove("loading");
    const count = $(".vt-count", body);
    if (t.error) { content.innerHTML = `<div class="vw-err">⚠ ${esc(t.error)}</div>`; count.textContent = ""; return; }
    if (t.mode === "stats") return drawStats(t, content, count);
    const d = t.data;
    const pts = $('[data-act="points"]', body);
    if (pts) pts.classList.toggle("hidden", !d.lonlat);
    const from = d.filtered ? d.offset + 1 : 0, to = d.offset + d.rows.length;
    count.innerHTML = `<b>${from.toLocaleString()}–${to.toLocaleString()}</b> of ${d.filtered.toLocaleString()} rows${d.filtered !== d.total ? ` <span class="muted">(filtered from ${d.total.toLocaleString()})</span>` : ""} · ${d.columns.length} columns`;
    $$(".vt-pager [data-pg]", body).forEach((b) => b.disabled = ["first", "prev"].includes(b.dataset.pg) ? d.offset === 0 : to >= d.filtered);
    const num = d.types.map((ty) => ty === "integer" || ty === "number");
    const ed = !!t.edit, pend = t.pending || new Map();
    const cell = (rid, i, v) => { const key = `${rid}|${d.columns[i]}`; return pend.has(key) ? { v: pend.get(key), dirty: true } : { v, dirty: false }; };
    content.innerHTML = d.rows.length ? `<table class="vt ${ed ? "editing" : ""}"><thead><tr>${ed ? `<th class="ck"><input type="checkbox" data-ckall title="Select all rows on this page"></th>` : ""}<th class="rn">#</th>${d.columns.map((c, i) =>
      `<th data-col="${esc(c)}" class="${num[i] ? "num" : ""} ${t.sort === c ? "sorted" : ""}" title="${esc(c)} (${d.types[i]}). Click to sort">
        <span class="th-n">${esc(c)}</span><i class="ty">${TYPE_TAG[d.types[i]] || ""}</i><i class="arr">${t.sort === c ? (t.desc ? "▼" : "▲") : "↕"}</i>${ed ? `<button class="th-menu" data-colmenu="${esc(c)}" title="Field options">⋯</button>` : ""}</th>`).join("")}</tr></thead>
      <tbody>${d.rows.map((r, k) => { const rid = d.row_ids[k]; return `<tr data-rid="${rid}" class="${t.selRow === rid ? "sel" : ""} ${t.sel?.has(rid) ? "picked" : ""}">${ed ? `<td class="ck"><input type="checkbox" data-ck ${t.sel?.has(rid) ? "checked" : ""}></td>` : ""}<td class="rn">${rid + 1}</td>${r.map((v, i) => {
        const c = cell(rid, i, v);
        return `<td data-c="${i}" class="${num[i] ? "num" : ""} ${c.dirty ? "dirty" : ""}">${fmtCell(c.v, d.types[i])}</td>`; }).join("")}</tr>`; }).join("")}</tbody></table>`
      : `<div class="vw-empty small"><b>No rows match</b><span>Clear the search or change the filter${ed && t.kind !== "attr" ? ", or add a row" : ""}.</span></div>`;
    if (ed) wireEditing(t, d, content);
    wireTableMenus(t, d, content);   // right-click: column and cell menus (table-menu.js)
    $$("th[data-col]", content).forEach((th) => th.onclick = (e) => {
      if (e.target.closest(".th-menu")) return;
      const c = th.dataset.col;
      if (t.sort !== c) { t.sort = c; t.desc = false; } else if (!t.desc) t.desc = true; else { t.sort = null; t.desc = false; }
      t.offset = 0; loadTab(t);
    });
    $$("tbody tr", content).forEach((tr) => tr.onclick = (e) => {
      if (e.target.closest(".ck, input, .cell-edit")) return;
      t.selRow = +tr.dataset.rid;
      $$("tbody tr", content).forEach((x) => x.classList.toggle("sel", x === tr));
      rowToMap(t, d, +tr.dataset.rid, [...d.rows[[...tr.parentNode.children].indexOf(tr)]]);
    });
    updateEditBar(t);
  }

  function drawStats(t, content, count) {
    const s = t.stats;
    count.innerHTML = `<b>${s.columns.length}</b> columns · ${s.rows.toLocaleString()} rows${t.kind === "attr" && t.q ? " (matching the search)" : ""}`;
    const spark = (h) => {
      const m = Math.max(1, ...h);
      return `<svg viewBox="0 0 ${h.length * 6} 24" class="spark" preserveAspectRatio="none">${h.map((v, i) => `<rect x="${i * 6}" y="${24 - 24 * v / m}" width="5" height="${24 * v / m}"/>`).join("")}</svg>`;
    };
    content.innerHTML = `<table class="vt stats"><thead><tr><th>Column</th><th>Type</th><th class="num">Missing</th><th class="num">Distinct</th><th class="num">Min</th><th class="num">Median</th><th class="num">Mean</th><th class="num">Max</th><th class="num">Std</th><th>Distribution / most frequent</th></tr></thead><tbody>
      ${s.columns.map((c) => `<tr><td><b>${esc(c.name)}</b></td><td><i class="ty">${TYPE_TAG[c.type] || esc(c.type)}</i> ${esc(c.type)}</td>
        <td class="num ${c.missing ? "warn-t" : ""}">${c.missing.toLocaleString()}${c.missing && s.rows ? ` <small>(${(100 * c.missing / s.rows).toFixed(1)}%)</small>` : ""}</td>
        <td class="num">${c.distinct.toLocaleString()}</td>
        ${["min", "median", "mean", "max", "std"].map((k) => `<td class="num">${c[k] == null ? '<span class="nul">–</span>' : fmtCell(c[k])}</td>`).join("")}
        <td>${c.hist ? spark(c.hist) : (c.top || []).slice(0, 5).map(([v, n]) => `<span class="topv">${v == null ? "(empty)" : esc(String(v).slice(0, 24))} <b>${n.toLocaleString()}</b></span>`).join("")}</td></tr>`).join("")}
      </tbody></table>`;
  }

  // vector attribute tables are paged / filtered / sorted in the browser with the same controls
  function attrColumns(l) {
    const feats = l.geojson?.features || [];
    const cols = [...new Set(feats.flatMap((f) => Object.keys(f.properties || {})))].filter((k) => !k.startsWith("_")).slice(0, 300);
    const types = cols.map((c) => {
      const vals = feats.map((f) => f.properties?.[c]).filter((v) => v !== null && v !== undefined && v !== "");
      if (vals.length && vals.every((v) => typeof v === "number")) return vals.every(Number.isInteger) ? "integer" : "number";
      if (vals.length && vals.every((v) => typeof v === "boolean")) return "boolean";
      return "text";
    });
    return { feats, cols, types };
  }
  function attrFilter(feats, cols, types, q) {
    let idx = feats.map((_, i) => i);
    q = (q || "").trim();
    if (!q) return idx;
    const m = q.match(/^\s*([^=<>!]+?)\s*(==|=|!=|>=|<=|>|<)\s*(.+?)\s*$/);
    if (m && cols.includes(m[1])) {
      const [, col, op, raw] = m, numeric = types[cols.indexOf(col)] !== "text";
      const val = numeric ? +raw : raw.replace(/^['"]|['"]$/g, "");
      if (numeric && Number.isNaN(val)) throw new Error(`'${raw}' is not a number`);
      const cmp = { "=": (a, b) => a == b, "==": (a, b) => a == b, "!=": (a, b) => a != b, ">": (a, b) => a > b, "<": (a, b) => a < b, ">=": (a, b) => a >= b, "<=": (a, b) => a <= b }[op];
      return idx.filter((i) => { const v = feats[i].properties?.[col]; return v !== null && v !== undefined && cmp(numeric ? v : String(v), val); });
    }
    const ql = q.toLowerCase();
    return idx.filter((i) => cols.some((c) => { const v = feats[i].properties?.[c]; return v !== null && v !== undefined && String(typeof v === "object" ? JSON.stringify(v) : v).toLowerCase().includes(ql); }));
  }
  function attrPage(l, t) {
    const { feats, cols, types } = attrColumns(l);
    let idx = attrFilter(feats, cols, types, t.q);
    if (t.sort && cols.includes(t.sort)) {
      const c = t.sort, dir = t.desc ? -1 : 1;
      idx = [...idx].sort((a, b) => {
        const va = feats[a].properties?.[c], vb = feats[b].properties?.[c];
        if (va == null || va === "") return 1;
        if (vb == null || vb === "") return -1;
        return (typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb), undefined, { numeric: true })) * dir;
      });
    }
    const sel = idx.slice(t.offset, t.offset + t.limit);
    return { columns: cols, types, rows: sel.map((i) => cols.map((c) => feats[i].properties?.[c] ?? null)), row_ids: sel,
             offset: t.offset, total: feats.length, filtered: idx.length, lonlat: null };
  }
  function attrStats(l, q) {
    const { feats, cols, types } = attrColumns(l);
    const idx = attrFilter(feats, cols, types, q);
    return { rows: idx.length, columns: cols.map((c, j) => {
      const vals = idx.map((i) => feats[i].properties?.[c]);
      const present = vals.filter((v) => v !== null && v !== undefined && v !== "");
      const st = { name: c, type: types[j], missing: vals.length - present.length, distinct: new Set(present.map((v) => typeof v === "object" ? JSON.stringify(v) : v)).size };
      if (types[j] === "integer" || types[j] === "number") {
        const a = present.slice().sort((x, y) => x - y), n = a.length;
        if (n) {
          const mean = a.reduce((s, v) => s + v, 0) / n;
          Object.assign(st, { min: a[0], max: a[n - 1], mean, median: a[Math.floor(n / 2)], std: Math.sqrt(a.reduce((s, v) => s + (v - mean) ** 2, 0) / n) });
          const lo = a[0], w = (a[n - 1] - lo) / 20 || 1;
          st.hist = Array(20).fill(0);
          a.forEach((v) => st.hist[Math.min(19, Math.floor((v - lo) / w))]++);
        }
      } else {
        const cnt = new Map();
        present.forEach((v) => { const k = typeof v === "object" ? JSON.stringify(v) : v; cnt.set(k, (cnt.get(k) || 0) + 1); });
        st.top = [...cnt.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
      }
      return st;
    }) };
  }
  function openAttr(l) {
    if (l.type !== "vector") return;
    openTab({ key: "attr:" + l.id, kind: "attr", title: `${l.name} · attributes`, layerId: l.id });
  }
