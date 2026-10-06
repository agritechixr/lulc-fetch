  // Classical ML: Clustering and t-SNE map.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Classical ML: unsupervised (clustering, t-SNE)
  const ux = { schema: null, method: "kmeans", pages: {} };
  const CL_PAL = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#393b79",
                  "#ad494a", "#637939", "#8c6d31", "#843c39", "#7b4173", "#3182bd", "#e6550d", "#31a354", "#756bb1", "#636363"];
  const clColor = (i, n, noise) => noise ? "#9ca3af" : CL_PAL[i % CL_PAL.length];
  const VIRIDIS = ["#440154", "#482878", "#3e4a89", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"];
  function viridis(t) {
    t = Math.min(1, Math.max(0, t)) * (VIRIDIS.length - 1);
    const i = Math.floor(t), f = t - i, a = VIRIDIS[i], b = VIRIDIS[Math.min(i + 1, VIRIDIS.length - 1)];
    const h = (s, k) => parseInt(s.slice(1 + 2 * k, 3 + 2 * k), 16);
    return `rgb(${[0, 1, 2].map((k) => Math.round(h(a, k) + (h(b, k) - h(a, k)) * f)).join(",")})`;
  }

  async function ensureUnsupSchema() { if (!ux.schema) ux.schema = await api("/api/unsup/schema"); return ux.schema; }
  const pageOf = (pre) => (ux.pages[pre] ||= { desc: null, cols: {} });
  const root = (pre) => $(pre === "uc" ? "#ml-sub-cluster" : "#ml-sub-tsne");

  // ---- table + column picker (shared by both pages)
  async function refreshUnsup(pre, selectPath) {
    await ensureUnsupSchema();
    const list = await api("/api/tables").catch(() => []);
    const sel = $(`#${pre}-table`), cur = selectPath || sel.value;
    sel.innerHTML = list.length ? list.map((t) => `<option value="${esc(t.path)}">${esc(t.name)}${t.rows != null ? ` · ${t.rows.toLocaleString()} rows` : ""}</option>`).join("")
      : `<option value="">No tables yet</option>`;
    if (cur && list.some((t) => t.path === cur)) sel.value = cur;
    sel.onchange = () => loadUnsupTable(pre);
    if (pre === "uc") renderMethodCards(); else renderTsneParams(false);
    renderUnsupPrep(pre, false);
    await loadUnsupTable(pre);
  }
  async function loadUnsupTable(pre) {
    const pg = pageOf(pre), path = $(`#${pre}-table`).value;
    pg.desc = null; pg.cols = {};
    if (!path) { $(`.uc-cols`, root(pre)).innerHTML = ""; $(`#${pre}-table-info`).innerHTML = `No tables yet. Add a CSV / Excel file with <b>Insert ▸ Add data</b> or use <b>Raster → table</b>.`; return; }
    $(`#${pre}-table-info`).innerHTML = `<span class="spinner"></span>Reading table…`;
    try { pg.desc = await api(`/api/tables/describe?path=${encodeURIComponent(path)}`); }
    catch (e) { $(`#${pre}-table-info`).textContent = e.message; return; }
    const d = pg.desc, meta = d.meta || {};
    $(`#${pre}-table-info`).innerHTML = `${d.rows.toLocaleString()} rows × ${d.columns.length} columns${meta.source ? ` · from ${esc(String(meta.source).split(/[\\/]/).pop())}` : ""}`;
    const labelCols = new Set([...(meta.label_columns || []), meta.target].filter(Boolean));
    d.columns.forEach((c) => {
      const skip = COORDS.includes(c.name) || ID_COLS.includes(c.name.toLowerCase()) || labelCols.has(c.name) || c.type === "text" || /(^|_)id$/i.test(c.name);
      pg.cols[c.name] = { use: !skip, cat: c.type === "text" };
    });
    // a label to compare with / colour by: the table's target, else a text column with few values
    // prefer a column that looks like a label (label / class / target / risk / type …), else the last text column with few values
    const cand = d.columns.filter((c) => c.unique >= 2 && c.unique <= 30 && !/(^|_)id$/i.test(c.name) && (c.type === "text" || c.type === "integer"));
    const named = cand.filter((c) => /label|class|target|risk|categor|type|group|crop|landcover|lulc|cluster|outcome|status/i.test(c.name));
    const guess = meta.cluster_column || meta.target || named[named.length - 1]?.name || cand.filter((c) => c.type === "text").pop()?.name || "";
    const opts = (empty) => `<option value="">${empty}</option>` + d.columns.filter((c) => c.unique <= 200 || c.type !== "text").map((c) =>
      `<option value="${esc(c.name)}" ${c.name === guess ? "selected" : ""}>${esc(c.name)} (${c.type}${c.type !== "number" ? `, ${c.unique} values` : ""})</option>`).join("");
    if (pre === "uc") $("#uc-compare").innerHTML = opts("None");
    else $("#ut-color").innerHTML = opts("No colour (or pick after the run)");
    if (pre === "uc") {
      $("#uc-name").value = (path.split(/[\\/]/).pop().replace(/\.[^.]+$/, "") || "clusters").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 50);
      const sm = $('#ml-sub-cluster [data-p="save_model"]');
      if (sm) sm.checked = !!(meta.band_columns && meta.band_columns.length);
    } else $("#ut-name").value = (path.split(/[\\/]/).pop().replace(/\.[^.]+$/, "") || "tsne").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 50);
    renderUnsupCols(pre);
  }
  function renderUnsupCols(pre) {
    const pg = pageOf(pre), d = pg.desc, box = root(pre);
    if (!d) return;
    const q = ($(".uc-col-filter", box).value || "").toLowerCase();
    $(".uc-cols", box).innerHTML = `<tr><th style="text-align:left">Column</th><th>Use</th><th>Categorical</th></tr>` + d.columns.map((c) => {
      const st = pg.cols[c.name], text = c.type === "text";
      const ex = (c.examples || []).slice(0, 4).map(String).join(", ");
      return `<tr class="${st.use ? "" : "is-off"}" data-col="${esc(c.name)}" ${q && !c.name.toLowerCase().includes(q) ? 'style="display:none"' : ""}>
        <td class="cn"><b title="${esc(c.name)}">${esc(c.name)}</b>${!text && c.suggest === "categorical" && st.use && !st.cat ? ' <span class="sugg" title="Few distinct whole numbers: maybe codes, not measurements">codes?</span>' : ""}
          <small title="${esc(ex)}">${c.type}${c.type !== "number" ? ` · ${c.unique.toLocaleString()} values` : ""}${c.nulls ? ` · <span class="warn-t">${c.nulls.toLocaleString()} missing</span>` : ""}${ex ? ` · e.g. ${esc(ex.slice(0, 36))}` : ""}</small></td>
        <td style="text-align:center"><input type="checkbox" data-use ${st.use ? "checked" : ""}></td>
        <td style="text-align:center"><input type="checkbox" data-cat ${st.cat || text ? "checked" : ""} ${text ? "disabled title='Text columns are always categorical'" : ""}></td></tr>`;
    }).join("");
    $$("tr[data-col]", box).forEach((tr) => {
      const st = pg.cols[tr.dataset.col];
      $("[data-use]", tr).onchange = (e) => { st.use = e.target.checked; tr.classList.toggle("is-off", !st.use); unsupColsHint(pre); };
      $("[data-cat]", tr).onchange = (e) => { st.cat = e.target.checked; unsupColsHint(pre); };
    });
    unsupColsHint(pre);
  }
  const unsupFeatures = (pre) => { const pg = pageOf(pre); return pg.desc ? pg.desc.columns.filter((c) => pg.cols[c.name]?.use).map((c) => c.name) : []; };
  const unsupCats = (pre) => { const pg = pageOf(pre); return unsupFeatures(pre).filter((f) => pg.cols[f].cat || pg.desc.columns.find((c) => c.name === f).type === "text"); };
  function unsupColsHint(pre) {
    const f = unsupFeatures(pre), cats = unsupCats(pre), box = root(pre);
    const flagged = f.filter((n) => COORDS.includes(n) || ID_COLS.includes(n.toLowerCase()));
    $(".uc-cols-hint", box).innerHTML = !f.length ? "Tick at least one column." :
      `<b>${f.length}</b> column${f.length > 1 ? "s" : ""}${cats.length ? ` (${cats.length} categorical)` : ""}` +
      (flagged.length ? `<br><span style="color:var(--warn)">${flagged.map(esc).join(", ")}: ids / coordinates usually make meaningless clusters.</span>` : "");
  }
  $$(".uc-col-filter").forEach((i) => i.oninput = () => renderUnsupCols(i.closest(".ml-sub").id === "ml-sub-cluster" ? "uc" : "ut"));
  $$("#ml-sub-cluster [data-colset], #ml-sub-tsne [data-colset]").forEach((b) => b.onclick = () => {
    const pre = b.closest(".ml-sub").id === "ml-sub-cluster" ? "uc" : "ut", pg = pageOf(pre);
    if (!pg.desc) return;
    pg.desc.columns.forEach((c) => {
      const plain = !COORDS.includes(c.name) && !ID_COLS.includes(c.name.toLowerCase()) && !/(^|_)id$/i.test(c.name);
      pg.cols[c.name].use = b.dataset.colset === "none" ? false : b.dataset.colset === "all" ? plain : plain && c.type !== "text";
    });
    renderUnsupCols(pre);
  });

  // ---- settings
  const collectIn = (sel) => {
    const out = {};
    $$(`${sel} [data-p]`).forEach((i) => { out[i.dataset.p] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : +i.value) : i.value; });
    return out;
  };
  function renderUnsupPrep(pre, reset) {
    const prev = reset ? {} : collectIn(`#${pre}-prep`);
    $(`#${pre}-prep`).innerHTML = ux.schema.prep.map((p) => mlField(p, p.name in prev ? prev[p.name] : p.default, "prep")).join("");
    const sync = () => { const r = $(`#${pre}-prep [data-p="outlier_pct"]`)?.closest(".pca-field"); if (r) r.classList.toggle("hidden", $(`#${pre}-prep [data-p="outliers"]`).value !== "clip"); };
    $(`#${pre}-prep [data-p="outliers"]`).addEventListener("change", sync);
    sync();
  }
  function renderMethodCards() {
    const ms = ux.schema.methods;
    modelPicker($("#uc-methods"), { value: ux.method, onChange: (k) => { ux.method = k; renderMethodCards(); },
      items: Object.entries(ms).map(([k, m]) => ({ id: k, title: m.title, group: m.family, badge: m.recommended ? "recommended" : "",
        meta: `<span>${m.k ? "you choose k" : "finds k itself"}${m.noise ? " · finds noise" : ""}</span>`,
        tip: descTip(m.desc, m.tip) })) });
    renderClusterParams(true);
  }
  function renderClusterParams(keepOptions) {
    const m = ux.schema.methods[ux.method];
    const optVal = (p) => { const el = $(`#ml-sub-cluster [data-scope="opt"][data-p="${p.name}"]`); return el ? (el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? null : +el.value) : el.value) : p.default; };
    const opts = ux.schema.options.filter((p) => m.k || !["find_k", "k_max"].includes(p.name));
    const vals = Object.fromEntries(opts.map((p) => [p.name, keepOptions ? optVal(p) : p.default]));
    $("#uc-params").innerHTML = m.params.filter((p) => !p.advanced).map((p) => mlField(p, p.default, "method")).join("") +
      opts.filter((p) => !p.advanced).map((p) => mlField(p, vals[p.name], "opt")).join("");
    $("#uc-adv").innerHTML = m.params.filter((p) => p.advanced).map((p) => mlField(p, p.default, "method")).join("") +
      opts.filter((p) => p.advanced).map((p) => mlField(p, vals[p.name], "opt")).join("");
    const mr = $('#ml-sub-cluster [data-p="max_fit_rows"]');
    if (mr) mr.placeholder = `default: ${m.fit_rows.toLocaleString()}`;
    const syncK = () => {
      const fk = $('#ml-sub-cluster [data-p="find_k"]'), km = $('#ml-sub-cluster [data-p="k_max"]')?.closest(".pca-field");
      const nk = $('#ml-sub-cluster [data-scope="method"][data-p="n_clusters"]');
      if (km) km.classList.toggle("hidden", !fk?.checked);
      if (nk) { nk.disabled = !!fk?.checked; nk.title = fk?.checked ? "Chosen automatically (Find the best number of clusters is on)" : ""; }
    };
    $('#ml-sub-cluster [data-p="find_k"]')?.addEventListener("change", syncK);
    syncK();
  }
  $("#uc-reset").onclick = () => { renderClusterParams(false); renderUnsupPrep("uc", true); };
  function renderTsneParams(reset) {
    const prev = reset ? {} : scopeVals("#ml-sub-tsne", "tsne");
    const v = (p) => (p.name in prev ? prev[p.name] : p.default);
    $("#ut-params").innerHTML = ux.schema.tsne.filter((p) => !p.advanced).map((p) => mlField(p, v(p), "tsne")).join("");
    $("#ut-adv").innerHTML = ux.schema.tsne.filter((p) => p.advanced).map((p) => mlField(p, v(p), "tsne")).join("");
  }
  $("#ut-reset").onclick = () => { renderTsneParams(true); renderUnsupPrep("ut", true); };
  const scopeVals = (sel, scope) => {
    const out = {};
    $$(`${sel} [data-scope="${scope}"]`).forEach((i) => { out[i.dataset.p] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : +i.value) : i.value; });
    return out;
  };

  // ---- run
  $("#uc-run").onclick = async () => {
    const err = $("#uc-error"); err.classList.add("hidden");
    const table = $("#uc-table").value, features = unsupFeatures("uc");
    if (!table) return toast("Choose a table first", true);
    if (!features.length) return toast("Tick at least one column to cluster on", true);
    const btn = $("#uc-run"); btn.disabled = true; $("#uc-result").classList.add("hidden");
    try {
      const job = await api("/api/unsup/cluster", { method: "POST", json: {
        table, features, categorical: unsupCats("uc"), method: ux.method, params: scopeVals("#ml-sub-cluster", "method"),
        options: scopeVals("#ml-sub-cluster", "opt"), prep: scopeVals("#uc-prep", "prep"), compare: $("#uc-compare").value || null,
        name: $("#uc-name").value || "clusters" } });
      const done = await trackJob(job, { tool: "ml", save: "cluster", title: `${ux.schema.methods[ux.method].title} clustering` });
      showClusterResult(done.result, $("#uc-result"));
      addItem({ kind: "table", name: done.result.output_table.split(/[\\/]/).pop(), path: done.result.output_table });
      if (done.result.path) refreshModels();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  $("#ut-run").onclick = async () => {
    const err = $("#ut-error"); err.classList.add("hidden");
    const table = $("#ut-table").value, features = unsupFeatures("ut");
    if (!table) return toast("Choose a table first", true);
    if (!features.length) return toast("Tick at least one column to map", true);
    const btn = $("#ut-run"); btn.disabled = true; $("#ut-result").classList.add("hidden");
    try {
      const job = await api("/api/unsup/tsne", { method: "POST", json: {
        table, features, categorical: unsupCats("ut"), params: scopeVals("#ml-sub-tsne", "tsne"), prep: scopeVals("#ut-prep", "prep"),
        color: $("#ut-color").value || null, name: $("#ut-name").value || "tsne" } });
      const done = await trackJob(job, { tool: "ml", save: "tsne", title: "t-SNE map" });
      showTsneResult(done.result, $("#ut-result"));
      addItem({ kind: "table", name: done.result.output_table.split(/[\\/]/).pop(), path: done.result.output_table });
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };

  // ---- canvas scatter plot with hover, legend toggling, categorical or continuous colours
  function scatterPlot(box, { x, y, values, kind, order, colorOf, xlabel = "", ylabel = "", height = 360, tip }) {
    box.innerHTML = `<div class="sc-wrap"><canvas></canvas><div class="sc-tip hidden"></div></div><div class="sc-legend"></div>`;
    const cv = $("canvas", box), tipEl = $(".sc-tip", box), wrap = $(".sc-wrap", box);
    const hidden = new Set();
    const n = x.length;
    let lo = Infinity, hi = -Infinity;
    if (kind === "numeric") values.forEach((v) => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
    const colOf = (v) => kind === "numeric" ? (v == null ? "#9ca3af" : viridis((v - lo) / ((hi - lo) || 1))) : colorOf(v);
    const xmin = Math.min(...x), xmax = Math.max(...x), ymin = Math.min(...y), ymax = Math.max(...y);
    const pad = 28;
    let W = 0, H = height, sx, sy;
    function draw() {
      W = wrap.clientWidth || 400;
      const dpr = window.devicePixelRatio || 1;
      cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
      const g = cv.getContext("2d");
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      const css = getComputedStyle(document.documentElement);
      g.strokeStyle = css.getPropertyValue("--border"); g.lineWidth = 1; g.strokeRect(pad, 6, W - pad - 6, H - pad - 6);
      g.fillStyle = css.getPropertyValue("--muted"); g.font = "11px sans-serif"; g.textAlign = "center";
      g.fillText(xlabel, (W + pad) / 2, H - 8);
      g.save(); g.translate(11, (H - pad) / 2); g.rotate(-Math.PI / 2); g.fillText(ylabel, 0, 0); g.restore();
      sx = (v) => pad + 4 + (v - xmin) / ((xmax - xmin) || 1) * (W - pad - 14);
      sy = (v) => H - pad - 4 - (v - ymin) / ((ymax - ymin) || 1) * (H - pad - 14);
      const r = n > 6000 ? 1.6 : n > 2000 ? 2.1 : 2.8;
      g.globalAlpha = n > 3000 ? 0.6 : 0.75;
      for (let i = 0; i < n; i++) {
        const v = values[i];
        if (kind !== "numeric" && hidden.has(String(v))) continue;
        g.fillStyle = colOf(v);
        g.beginPath(); g.arc(sx(x[i]), sy(y[i]), r, 0, 6.2832); g.fill();
      }
      g.globalAlpha = 1;
    }
    // legend
    const leg = $(".sc-legend", box);
    if (kind === "numeric") {
      leg.innerHTML = `<span class="muted">${fmtv(lo)}</span><span class="sc-grad" style="background:linear-gradient(to right, ${VIRIDIS.join(",")})"></span><span class="muted">${fmtv(hi)}</span>`;
    } else {
      const counts = new Map();
      values.forEach((v) => counts.set(String(v), (counts.get(String(v)) || 0) + 1));
      const keys = order || [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a));
      leg.innerHTML = keys.filter((k) => counts.has(k)).slice(0, 40).map((k) => `<button class="sc-key" data-k="${esc(k)}" title="Click to hide / show"><i style="background:${colorOf(k)}"></i>${esc(k === "" ? "(empty)" : k)} <small>${counts.get(k).toLocaleString()}</small></button>`).join("");
      $$(".sc-key", leg).forEach((b) => b.onclick = () => { const k = b.dataset.k; hidden.has(k) ? hidden.delete(k) : hidden.add(k); b.classList.toggle("off", hidden.has(k)); draw(); });
    }
    // hover: nearest point within 8 px
    cv.onmousemove = (e) => {
      const rc = cv.getBoundingClientRect(), mx = e.clientX - rc.left, my = e.clientY - rc.top;
      let best = -1, bd = 64;
      for (let i = 0; i < n; i++) {
        if (kind !== "numeric" && hidden.has(String(values[i]))) continue;
        const dx = sx(x[i]) - mx, dy = sy(y[i]) - my, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = i; }
      }
      if (best < 0) { tipEl.classList.add("hidden"); return; }
      tipEl.innerHTML = tip ? tip(best) : esc(String(values[best]));
      tipEl.classList.remove("hidden");
      tipEl.style.left = Math.min(mx + 12, W - tipEl.offsetWidth - 4) + "px";
      tipEl.style.top = Math.max(4, my - tipEl.offsetHeight - 8) + "px";
    };
    cv.onmouseleave = () => tipEl.classList.add("hidden");
    draw();
    const ro = new ResizeObserver(() => { if (wrap.clientWidth && Math.abs(wrap.clientWidth - W) > 2) draw(); });
    ro.observe(wrap);
  }

  // ---- small SVG line chart
  function lineChart(xs, series, { xlabel = "", mark = null, h = 170, fmtY = (v) => fmtv(v) } = {}) {
    const W = 340, H = h, L = 44, B = 28, T = 10, R = 10;
    const all = series.flatMap((s) => s.ys.filter((v) => v != null && isFinite(v)));
    if (!all.length) return "";
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...all), y1 = Math.max(...all);
    const X = (v) => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), Y = (v) => H - B - (v - y0) / ((y1 - y0) || 1) * (H - B - T);
    let out = `<svg viewBox="0 0 ${W} ${H}" class="ev-chart lc">`;
    out += `<rect x="${L}" y="${T}" width="${W - L - R}" height="${H - B - T}" fill="none" stroke="var(--border)"/>`;
    [y0, (y0 + y1) / 2, y1].forEach((v) => out += `<text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" class="lc-t">${esc(fmtY(v))}</text>`);
    xs.forEach((v, i) => { if (xs.length <= 16 || i % Math.ceil(xs.length / 12) === 0) out += `<text x="${X(v)}" y="${H - B + 13}" text-anchor="middle" class="lc-t">${esc(fmtv(v))}</text>`; });
    out += `<text x="${(L + W - R) / 2}" y="${H - 3}" text-anchor="middle" class="lc-t">${esc(xlabel)}</text>`;
    if (mark != null) out += `<line x1="${X(mark)}" x2="${X(mark)}" y1="${T}" y2="${H - B}" stroke="var(--accent)" stroke-dasharray="4 3"/>`;
    series.forEach((s) => {
      const pts = xs.map((v, i) => s.ys[i] != null && isFinite(s.ys[i]) ? `${X(v).toFixed(1)},${Y(s.ys[i]).toFixed(1)}` : null).filter(Boolean);
      out += `<polyline points="${pts.join(" ")}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
      if (xs.length <= 40) xs.forEach((v, i) => { if (s.ys[i] != null && isFinite(s.ys[i])) out += `<circle cx="${X(v)}" cy="${Y(s.ys[i])}" r="3" fill="${s.color}"><title>${esc(xlabel)} ${v}: ${esc(fmtY(s.ys[i]))}</title></circle>`; });
    });
    return out + "</svg>";
  }

  // ---- clustering result
  function showClusterResult(r, box) {
    const q = r.quality || {}, k = r.n_clusters, noiseIdx = r.noise ? r.classes.length - 1 : -1;
    const color = (i) => clColor(i, k, i === noiseIdx);
    const grade = (v, good, ok, lowBetter) => v == null ? "" : lowBetter ? (v <= good ? "good" : v <= ok ? "ok" : "bad") : (v >= good ? "good" : v >= ok ? "ok" : "bad");
    const tiles = [
      [`${k}`, "Clusters", "Number of groups found" + (r.k_search ? " (best silhouette)" : ""), ""],
      r.noise ? [`${(100 * r.noise / r.rows_used).toFixed(1)}%`, "Noise", `${r.noise.toLocaleString()} rows belong to no cluster`, ""] : null,
      q.silhouette != null ? [q.silhouette.toFixed(3), "Silhouette", "How well each row fits its own cluster vs the nearest other one: −1 to 1. Above 0.5 clear clusters, 0.25–0.5 reasonable, below 0.25 overlapping.", grade(q.silhouette, 0.5, 0.25)] : null,
      q.davies_bouldin != null ? [q.davies_bouldin.toFixed(2), "Davies-Bouldin", "Average similarity between each cluster and its closest neighbour. Lower is better (0 = perfectly separated).", grade(q.davies_bouldin, 0.7, 1.5, true)] : null,
      q.calinski_harabasz != null ? [fmtv(q.calinski_harabasz), "Calinski-Harabasz", "Between-cluster vs within-cluster spread. Higher is better; compare runs on the same data.", ""] : null,
    ].filter(Boolean).map(([v, l, t, g]) => `<div class="metric ${g}"><b>${v}</b><span>${l} ${tipBtn(t)}</span></div>`).join("");
    const maxSize = Math.max(1, ...r.sizes);
    const sizes = r.classes.map((c, i) => `<div class="imp-row"><span><i class="sw" style="background:${color(i)}"></i>${esc(c)}</span><span><span class="imp-bar" style="display:block;width:${Math.max(1, 100 * r.sizes[i] / maxSize)}%;background:${color(i)}"></span></span><span>${r.sizes[i].toLocaleString()} <small class="muted">${(100 * r.sizes[i] / r.rows_used).toFixed(1)}%</small></span></div>`).join("");
    // profiles heatmap (z-scores), categorical top values
    const P = r.profiles, zc = (z) => z == null ? "transparent" : z > 0 ? `rgba(220,38,38,${Math.min(0.85, Math.abs(z) / 2)})` : `rgba(37,99,235,${Math.min(0.85, Math.abs(z) / 2)})`;
    const prof = P.numeric.length || P.categorical.length ? `<div class="home-label" style="margin-top:14px">Cluster profiles ${tipBtn("Average value of each column per cluster, in the original units. Colour = how far it is from the overall average (red above, blue below, in standard deviations). This tells you what makes each cluster different.")}</div>
      <div class="load-wrap"><table class="cm-table prof"><tr><th></th>${r.classes.map((c, i) => `<th title="${esc(c)}"><i class="sw" style="background:${color(i)}"></i>${esc(c.replace("Cluster ", "C"))}</th>`).join("")}<th class="muted">All</th></tr>
      ${P.numeric.map((f) => `<tr><th class="rowh" title="${esc(f.feature)}">${esc(f.feature)}</th>${f.means.map((v, i) => `<td style="background:${zc(f.z[i])};color:${Math.abs(f.z[i] || 0) > 1.2 ? "#fff" : "inherit"}" title="${esc(r.classes[i])}: mean ${fmtv(v)} (${f.z[i] == null ? "–" : (f.z[i] > 0 ? "+" : "") + f.z[i].toFixed(2)} SD)">${v == null ? "–" : fmtv(v)}</td>`).join("")}<td class="muted">${fmtv(f.overall)}</td></tr>`).join("")}
      ${P.categorical.map((f) => `<tr><th class="rowh" title="${esc(f.feature)}">${esc(f.feature)}</th>${f.top.map((t) => `<td title="${t ? `${esc(t[0])}: ${(100 * t[1]).toFixed(0)}% of the cluster` : ""}">${t ? `${esc(String(t[0]).slice(0, 10))} <small>${(100 * t[1]).toFixed(0)}%</small>` : "–"}</td>`).join("")}<td></td></tr>`).join("")}
      </table></div>` : "";
    // comparison with a known label
    const C = r.comparison;
    const comp = C ? `<div class="home-label" style="margin-top:14px">Clusters vs “${esc(C.column)}” ${tipBtn("How well the clusters match the known label (the label was not used for clustering). Adjusted Rand index: 0 = random, 1 = identical grouping. NMI: shared information, 0–1. Homogeneity: each cluster holds one label. Completeness: each label sits in one cluster.")}</div>
      <div class="metric-tiles small-tiles">${[["ARI", C.ari], ["NMI", C.nmi], ["Homogeneity", C.homogeneity], ["Completeness", C.completeness]].map(([l, v]) => `<div class="metric ${grade(v, 0.6, 0.3)}"><b>${v.toFixed(3)}</b><span>${l}</span></div>`).join("")}</div>
      <div class="load-wrap"><table class="cm-table"><tr><th></th>${C.labels.map((l) => `<th title="${esc(l)}">${esc(String(l).slice(0, 10))}</th>`).join("")}<th>Most common</th></tr>
      ${C.table.map((row, i) => { const tot = row.reduce((a, b) => a + b, 0) || 1; return `<tr><th class="rowh"><i class="sw" style="background:${color(i)}"></i>${esc(r.classes[i])}</th>${row.map((v) => `<td style="background:rgba(31,122,90,${(0.08 + 0.8 * v / tot).toFixed(2)});color:${v / tot > 0.55 ? "#fff" : "inherit"}" title="${v.toLocaleString()} rows (${(100 * v / tot).toFixed(0)}% of the cluster)">${v ? v.toLocaleString() : ""}</td>`).join("")}<td><b>${esc(C.majority[i] ?? "–")}</b></td></tr>`; }).join("")}
      </table></div>` : "";
    const ks = r.k_search ? `<div class="home-label" style="margin-top:14px">Choosing the number of clusters ${tipBtn(`Every k from 2 to ${Math.max(...r.k_search.k)} was tried on ${r.k_search.rows.toLocaleString()} rows. The highest silhouette (dashed line) was used.` + (r.k_search.extra_name ? ` ${r.k_search.extra_name === "inertia" ? "Inertia (within-cluster spread) always falls as k grows; look for the 'elbow' where it flattens." : "BIC: lower is better; balances fit against complexity."}` : ""))}</div>
      <div class="chart-pair"><div>${lineChart(r.k_search.k, [{ ys: r.k_search.silhouette, color: "var(--accent)" }], { xlabel: "k (silhouette)", mark: r.k_search.best_k, fmtY: (v) => v.toFixed(2) })}</div>
      ${r.k_search.extra_name ? `<div>${lineChart(r.k_search.k, [{ ys: r.k_search.extra, color: "#d97706" }], { xlabel: `k (${r.k_search.extra_name})`, mark: r.k_search.best_k })}</div>` : ""}</div>` : "";
    const kd = r.k_distance ? `<div class="home-label" style="margin-top:14px">k-distance curve ${tipBtn("Distance from each row to its k-th nearest neighbour (k = min samples), sorted. The bend (knee) is a good eps: rows to the right of it are sparse (noise). Dashed line: eps used.")}</div>
      ${lineChart(r.k_distance.positions, [{ ys: r.k_distance.distances, color: "var(--accent)" }], { xlabel: `rows sorted by distance · eps used = ${fmtv(r.params.eps)}`, mark: r.k_distance.positions.reduce((b, p, i) => r.k_distance.distances[i] <= r.params.eps ? p : b, 0) })}` : "";
    box.innerHTML = `<div class="card">
      <div class="row between"><h2 style="margin:0">✓ ${esc(r.method_title)}: ${k} cluster${k === 1 ? "" : "s"}</h2><span class="task-badge">unsupervised</span></div>
      <div class="pca-sum" style="margin-top:4px">${r.rows_used.toLocaleString()} rows clustered${r.rows_fitted < r.rows_used ? ` (fitted on ${r.rows_fitted.toLocaleString()}, the rest assigned by ${esc(r.assigned_by)})` : ""}${r.rows_skipped ? ` · ${r.rows_skipped.toLocaleString()} rows skipped (missing values)` : ""} · ${r.features.length} columns · ${r.seconds} s</div>
      ${r.prep_steps?.length ? `<div class="prep-res"><b>Preprocessing</b> ${r.prep_steps.map(esc).join(" → ")}${r.method === "dbscan" ? ` · eps = ${fmtv(r.params.eps)}` : ""}${r.method === "hdbscan" ? ` · min cluster size = ${r.params.min_cluster_size}` : ""}</div>` : ""}
      ${r.warnings?.length ? `<div class="warn">${r.warnings.map(esc).join("<br>")}</div>` : ""}
      <div class="metric-tiles" style="margin-top:10px">${tiles}</div>
      ${ks}
      <div class="home-label" style="margin-top:14px">Cluster sizes</div>${sizes}
      <div class="home-label" style="margin-top:14px">2D view ${tipBtn("The rows projected onto their first two principal components (PCA), coloured by cluster. Overlap here doesn't always mean overlap in all columns. For a clearer map, use t-SNE.")}</div>
      <div class="uc-scatter"></div>
      ${prof}${comp}
      ${r.dendrogram ? `<div class="home-label" style="margin-top:14px">Dendrogram ${tipBtn(`The merge tree of ${r.dendrogram.rows.toLocaleString()} sample rows (last 30 merges). Height = distance at which groups merge. The dashed line is where the tree was cut into ${k} clusters; long vertical lines mean well-separated groups.`)}</div><div class="dendro">${dendroSvg(r.dendrogram)}</div>` : ""}
      ${kd}
      <div class="row" style="flex-wrap:wrap;margin-top:12px">
        <button class="btn small primary" data-open-out>Open table with cluster column</button>
        <button class="btn small" data-tsne-out>t-SNE map of these clusters</button>
        <button class="btn small" data-train-out title="Train a supervised model that learns these clusters">Train a model on the clusters</button>
        ${r.path ? `<button class="btn small" data-classify="${esc(r.path)}">Cluster an image with this →</button>` : ""}
        <a class="btn small" href="/api/tables/file?path=${encodeURIComponent(r.output_table)}" download>⬇ Table</a></div>
      <p class="hint">Saved as <code>${esc(r.output_table)}</code> with a <b>cluster</b> column (1…${k}${r.noise ? ", 0 = noise" : ""}${r.rows_skipped ? ", empty = skipped" : ""})${r.has_probability ? " and cluster_probability" : ""}.${r.path ? ` Model: <code>${esc(r.path)}</code>.` : ""}</p></div>`;
    box.classList.remove("hidden");
    const v = r.view;
    scatterPlot($(".uc-scatter", box), { x: v.x, y: v.y, values: v.cluster.map((c) => r.classes[c]), kind: "categorical", order: r.classes,
      colorOf: (name) => color(r.classes.indexOf(name)), xlabel: `PC1 (${(100 * v.explained[0]).toFixed(0)}%)`, ylabel: `PC2 (${(100 * (v.explained[1] || 0)).toFixed(0)}%)` });
    $("[data-open-out]", box).onclick = () => previewTable(r.output_table);
    $("[data-tsne-out]", box).onclick = async () => {
      openMlSub("tsne");
      await refreshUnsup("ut", r.output_table);
      const pg = pageOf("ut");
      Object.keys(pg.cols).forEach((c) => pg.cols[c].use = r.features.includes(c));
      r.categorical?.forEach((c) => pg.cols[c] && (pg.cols[c].cat = true));
      renderUnsupCols("ut");
      $("#ut-color").value = "cluster";
      toast("Same columns selected, coloured by cluster. Press Make t-SNE map.");
    };
    $("[data-train-out]", box).onclick = () => { switchTool("ml"); openMlSub("train"); refreshTrainTables(r.output_table); };
    $("[data-classify]", box)?.addEventListener("click", () => { openMlSub("predict"); refreshPredict(r.path); });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function dendroSvg(d) {
    const W = 340, H = 200, L = 40, B = 34, T = 8, R = 6;
    const xs = d.icoord.flat(), xmax = Math.max(...xs), xmin = Math.min(...xs), ymax = d.max || Math.max(...d.dcoord.flat());
    const X = (v) => L + (v - xmin) / ((xmax - xmin) || 1) * (W - L - R), Y = (v) => H - B - v / (ymax || 1) * (H - B - T);
    let s = `<svg viewBox="0 0 ${W} ${H}" class="ev-chart lc">`;
    [0, ymax / 2, ymax].forEach((v) => s += `<text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" class="lc-t">${esc(fmtv(v))}</text>`);
    d.icoord.forEach((ic, i) => { const dc = d.dcoord[i]; s += `<polyline points="${ic.map((x, j) => `${X(x).toFixed(1)},${Y(dc[j]).toFixed(1)}`).join(" ")}" fill="none" stroke="var(--text)" stroke-width="1.2" opacity=".8"/>`; });
    if (d.cut) s += `<line x1="${L}" x2="${W - R}" y1="${Y(d.cut)}" y2="${Y(d.cut)}" stroke="#dc2626" stroke-dasharray="5 3"><title>cut height ${fmtv(d.cut)}</title></line>`;
    const step = (W - L - R) / d.leaves.length;
    d.leaves.forEach((lbl, i) => s += `<text transform="translate(${L + step * (i + 0.5)} ${H - B + 8}) rotate(-60)" text-anchor="end" class="lc-t" style="font-size:8px">${esc(lbl)}</text>`);
    return s + "</svg>";
  }

  // ---- t-SNE result
  function showTsneResult(r, box) {
    const keys = Object.keys(r.colors);
    box.innerHTML = `<div class="card">
      <div class="row between"><h2 style="margin:0">✓ t-SNE map</h2><span class="task-badge">unsupervised</span></div>
      <div class="pca-sum" style="margin-top:4px">${r.rows_mapped.toLocaleString()} rows mapped${r.rows_mapped < r.rows_used ? ` (random sample of ${r.rows_used.toLocaleString()})` : ""} · ${r.features.length} columns · perplexity ${fmtv(r.params.perplexity)} · ${r.iterations} iterations · ${r.seconds} s</div>
      ${r.prep_steps?.length ? `<div class="prep-res"><b>Preprocessing</b> ${r.prep_steps.map(esc).join(" → ")}</div>` : ""}
      <div class="metric-tiles small-tiles" style="margin-top:10px">
        <div class="metric ${r.trustworthiness >= 0.9 ? "good" : r.trustworthiness >= 0.8 ? "ok" : "bad"}"><b>${r.trustworthiness.toFixed(3)}</b><span>Trustworthiness ${tipBtn("How well neighbours on the map are also neighbours in the data (0–1). Above 0.9 = the map is faithful.")}</span></div>
        <div class="metric"><b>${r.kl_divergence.toFixed(2)}</b><span>KL divergence ${tipBtn("t-SNE's final error. Lower is better; compare runs on the same data only.")}</span></div></div>
      <label style="margin-top:10px">Colour by <select class="ut-colsel">${keys.map((k) => `<option ${k === r.color ? "selected" : ""}>${esc(k)}</option>`).join("")}<option value="">None</option></select></label>
      <div class="ut-scatter"></div>
      <p class="hint">Distances between far-apart groups and group sizes are not meaningful in t-SNE; what matters is which points sit together. Hover a point for its values.</p>
      <div class="row" style="flex-wrap:wrap"><button class="btn small primary" data-open-out>Open table with map coordinates</button>
        <a class="btn small" href="/api/tables/file?path=${encodeURIComponent(r.output_table)}" download>⬇ Table</a></div>
      <p class="hint">Saved as <code>${esc(r.output_table)}</code> (the mapped rows plus <b>tsne_1</b>, <b>tsne_2</b>).</p></div>`;
    box.classList.remove("hidden");
    const draw = () => {
      const c = $(".ut-colsel", box).value, col = r.colors[c];
      const values = col ? col.values : r.x.map(() => "all rows");
      const kind = col?.kind === "numeric" ? "numeric" : "categorical";
      const cats = kind === "categorical" ? [...new Set(values.map(String))] : [];
      const counts = new Map(); values.forEach((v) => counts.set(String(v), (counts.get(String(v)) || 0) + 1));
      cats.sort((a, b) => counts.get(b) - counts.get(a));
      scatterPlot($(".ut-scatter", box), { x: r.x, y: r.y, values: kind === "numeric" ? values : values.map(String), kind, order: cats,
        colorOf: (v) => (/^noise$|^0$/i.test(v) && c === "cluster") ? "#9ca3af" : CL_PAL[cats.indexOf(String(v)) % CL_PAL.length],
        xlabel: "t-SNE 1", ylabel: "t-SNE 2", height: 440,
        tip: (i) => `<b>row ${r.row_ids[i] + 1}</b>` + keys.slice(0, 8).map((k) => `<div>${esc(k)}: <b>${esc(String(r.colors[k].values[i] ?? "–"))}</b></div>`).join("") });
    };
    $(".ut-colsel", box).onchange = draw;
    draw();
    $("[data-open-out]", box).onclick = () => previewTable(r.output_table);
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
