  // Classical ML: Train a model (model, preprocessing, tuning, results) and Classify an image.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Classical ML: train a model / classify an image
  const mlx = { schema: null, desc: null, model: "rf", family: "All", report: null, cols: {}, tuneMethod: "random" };
  const pct = (v) => v == null ? "–" : `${(v * 100).toFixed(1)}%`;
  const COORDS = ["x", "y", "lon", "lat", "row", "col"];

  function mlField(p, value, scope) {
    let input;
    if (p.type === "bool") input = `<input type="checkbox" data-p="${p.name}" data-scope="${scope}" ${value ? "checked" : ""}>`;
    else if (p.type === "select") input = `<select data-p="${p.name}" data-scope="${scope}">${p.options.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
    else if (p.type === "text") input = `<input type="text" data-p="${p.name}" data-scope="${scope}" value="${esc(value ?? "")}" maxlength="${p.maxlength || 60}">`;
    else input = `<input type="number" data-p="${p.name}" data-scope="${scope}" step="${p.type === "int" ? 1 : "any"}" ${p.min != null ? `min="${p.min}"` : ""} ${p.max != null ? `max="${p.max}"` : ""} value="${value ?? ""}" placeholder="${esc(p.placeholder || "")}">`;
    return `<div class="pca-field"><span>${esc(p.label)}${tipBtn(p.tip)}</span>${input}</div>`;
  }
  const mlCollect = (scope) => {
    const out = {};
    $$(`#mt-params [data-scope="${scope}"], #mt-adv [data-scope="${scope}"], #mt-prep [data-scope="${scope}"]`).forEach((i) => {
      out[i.dataset.p] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : +i.value) : i.value;
    });
    return out;
  };

  // ---------------- train: table, label, features
  async function refreshTrainTables(select) {
    const list = await api("/api/tables").catch(() => []);
    const sel = $("#mt-table"), cur = select || sel.value;
    sel.innerHTML = list.length ? list.map((t) => `<option value="${esc(t.path)}">${esc(t.name)}${t.rows != null ? ` · ${t.rows.toLocaleString()} rows` : ""}</option>`).join("")
      : `<option value="">No tables yet</option>`;
    if (cur && list.some((t) => t.path === cur)) sel.value = cur;
    if (sel.value) await loadTableDesc(sel.value); else { mlx.desc = null; renderTargetAndFeatures(); }
  }
  async function loadTableDesc(path) {
    $("#mt-table-info").innerHTML = `<span class="spinner"></span>Reading table…`;
    try { mlx.desc = await api(`/api/tables/describe?path=${encodeURIComponent(path)}`); }
    catch (e) { mlx.desc = null; toast(e.message, true); }
    renderTargetAndFeatures();
  }
  function renderTargetAndFeatures() {
    const d = mlx.desc;
    if (!d) { $("#mt-target").innerHTML = ""; $("#mt-cols").innerHTML = ""; $("#mt-table-info").innerHTML = `No tables yet? Create one with <a href="#" class="goto-rt"><b>Raster → table</b></a>.`; wireGotoRt(); return; }
    const meta = d.meta || {};
    $("#mt-table-info").innerHTML = `${d.rows.toLocaleString()} rows × ${d.columns.length} columns${meta.source ? ` · from ${esc(String(meta.source).split(/[\\/]/).pop())}` : ""}${meta.crs ? ` · ${esc(meta.crs)}` : ""}`;
    const labelGuess = meta.target && d.columns.some((c) => c.name === meta.target) ? meta.target : d.columns[d.columns.length - 1].name;
    $("#mt-target").innerHTML = d.columns.map((c) => `<option value="${esc(c.name)}" ${c.name === labelGuess ? "selected" : ""}>${esc(c.name)} (${c.type}${c.type !== "number" ? `, ${c.unique} values` : ""})</option>`).join("");
    mlx.cols = {};
    $("#mt-task").value = "auto";
    renderColumns(true);
    renderTargetInfo();
    $("#mt-name").value = `${MODEL_TITLE()}_${(meta.source ? String(meta.source).split(/[\\/]/).pop() : "table").replace(/\.[^.]+$/, "")}`.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60);
  }
  const MODEL_TITLE = () => (mlx.schema?.models[mlx.model]?.title || "model").replace(/[^A-Za-z0-9]+/g, "");
  const ID_COLS = ["poly_id", "sample_id", "fid", "id", "objectid"];
  // default role / type for every column (kept across target changes once the user has edited them)
  function colDefaults(c) {
    const meta = mlx.desc.meta || {}, target = $("#mt-target").value;
    const labelCols = new Set([...(meta.label_columns || []), target]);
    const skip = COORDS.includes(c.name) || labelCols.has(c.name) || ID_COLS.includes(c.name.toLowerCase()) || /(^|_)id$/i.test(c.name) || c.type === "text";
    return { role: skip ? "ignore" : "feature", type: c.suggest || (c.type === "text" ? "categorical" : "numeric") };
  }
  function renderColumns(useDefaults) {
    const d = mlx.desc, target = $("#mt-target").value, bandCols = new Set(d.meta?.band_columns || []);
    const q = ($("#mt-col-filter").value || "").toLowerCase();
    d.columns.forEach((c) => { if (useDefaults || !mlx.cols[c.name]) mlx.cols[c.name] = colDefaults(c); });
    $("#mt-cols").innerHTML = `<tr><th style="text-align:left">Column</th><th>Role</th><th>Type</th></tr>` + d.columns.map((c) => {
      const st = mlx.cols[c.name], isT = c.name === target, text = c.type === "text";
      const info = `${c.type}${c.type !== "number" ? ` · ${c.unique.toLocaleString()} values` : ""}${c.nulls ? ` · <span class="warn-t">${c.nulls.toLocaleString()} missing</span>` : ""}`;
      const ex = (c.examples || []).slice(0, 4).map(String).join(", ");
      const sugg = !isT && st.role === "feature" && c.suggest === "categorical" && st.type === "numeric" && !text
        ? ` <span class="sugg" title="Only ${c.unique} distinct whole numbers: probably codes, not measurements">looks categorical?</span>` : "";
      return `<tr class="${isT ? "is-target" : st.role === "ignore" ? "is-off" : ""}" data-col="${esc(c.name)}" ${q && !c.name.toLowerCase().includes(q) ? 'style="display:none"' : ""}>
        <td class="cn"><b title="${esc(c.name)}">${esc(c.name)}</b>${bandCols.has(c.name) ? ' <span class="band-tag">band</span>' : ""}${sugg}<small title="${esc(ex)}">${info}${ex ? ` · e.g. ${esc(ex.slice(0, 40))}` : ""}</small></td>
        <td><select data-role>${isT ? `<option value="target" selected>🎯 Target</option>` : ""}<option value="feature" ${!isT && st.role === "feature" ? "selected" : ""}>Feature</option><option value="ignore" ${!isT && st.role === "ignore" ? "selected" : ""}>Ignore</option>${isT ? "" : `<option value="target">Target</option>`}</select></td>
        <td><select data-type ${isT ? "disabled" : ""}><option value="numeric" ${st.type === "numeric" ? "selected" : ""} ${text ? "disabled" : ""}>Numeric</option><option value="categorical" ${st.type === "categorical" || text ? "selected" : ""}>Categorical</option></select></td></tr>`;
    }).join("");
    $$("#mt-cols tr[data-col]").forEach((tr) => {
      const name = tr.dataset.col;
      $("[data-role]", tr).onchange = (e) => {
        const v = e.target.value;
        if (v === "target") {
          const old = $("#mt-target").value;
          $("#mt-target").value = name; $("#mt-task").value = "auto";
          restoreOldTarget(old);
          renderColumns(false); renderTargetInfo(); return;
        }
        if (name === $("#mt-target").value) { e.target.value = "target"; return toast("Choose another target column first", true); }
        mlx.cols[name].role = v; tr.className = v === "ignore" ? "is-off" : ""; updateFeatHint();
      };
      $("[data-type]", tr).onchange = (e) => { mlx.cols[name].type = e.target.value; renderColumns(false); if ($("#mt-prep-flow").innerHTML) updatePrepFlow(); };
    });
    updateFeatHint();
  }
  const selFeatures = () => mlx.desc ? mlx.desc.columns.filter((c) => c.name !== $("#mt-target").value && mlx.cols[c.name]?.role === "feature").map((c) => c.name) : [];
  const selCategorical = () => selFeatures().filter((f) => mlx.cols[f].type === "categorical" || mlx.desc.columns.find((c) => c.name === f).type === "text");
  function updateFeatHint() {
    const f = selFeatures(), cats = selCategorical();
    const coords = f.filter((n) => COORDS.includes(n) || ID_COLS.includes(n.toLowerCase()));
    const nulls = f.filter((n) => mlx.desc.columns.find((c) => c.name === n).nulls);
    const textCats = cats.filter((n) => mlx.desc.columns.find((c) => c.name === n).type === "text");
    $("#mt-feats-hint").innerHTML = !f.length ? "Select at least one feature." :
      `<b>${f.length}</b> feature${f.length > 1 ? "s" : ""}${cats.length ? ` (${cats.length} categorical: ${cats.map(esc).join(", ")})` : ""} · ${mlx.desc.columns.length - f.length - 1} ignored` +
      (coords.length ? `<br><span style="color:var(--warn)">${coords.map(esc).join(", ")} included: the model may learn locations rather than spectra.</span>` : "") +
      (nulls.length ? `<br>${nulls.map(esc).join(", ")} ha${nulls.length > 1 ? "ve" : "s"} missing values: those rows are dropped unless <b>Preprocessing ▸ Missing values</b> is set to <b>Fill in</b>.` : "") +
      (textCats.length ? `<br><span style="color:var(--warn)">Text column${textCats.length > 1 ? "s" : ""} ${textCats.map(esc).join(", ")}: fine for evaluation, but an image can't provide ${textCats.length > 1 ? "them" : "it"}, so the model can't classify a raster.</span>` : "");
  }
  // target: categories or numbers?
  function suggestTask(col) {
    if (!col) return ["classification", ""];
    if (col.type === "text") return ["classification", `<b>${esc(col.name)}</b> holds text (${col.unique} values) → categories.`];
    if (col.type === "integer" && col.unique <= 30) return ["classification", `<b>${esc(col.name)}</b> has only ${col.unique} distinct whole numbers (e.g. ${esc((col.examples || []).slice(0, 4).join(", "))}) → probably class codes, so categories.`];
    if (col.type === "integer" && col.unique <= 100) return ["classification", `<b>${esc(col.name)}</b> has ${col.unique} distinct whole numbers. Categories if these are codes; numbers if they are counts or measurements.`];
    return ["regression", `<b>${esc(col.name)}</b> has ${col.unique.toLocaleString()} distinct ${col.type === "integer" ? "whole numbers" : "decimal values"} → a continuous quantity, so numbers.`];
  }
  function detectTask() {
    if ($("#mt-task").value !== "auto") return $("#mt-task").value;
    return suggestTask(mlx.desc?.columns.find((c) => c.name === $("#mt-target").value))[0];
  }
  function renderTargetInfo() {
    const d = mlx.desc, target = $("#mt-target").value, task = detectTask();
    const col = d?.columns.find((c) => c.name === target);
    const [sug, why] = suggestTask(col);
    $$("#mt-task-choice [data-task]").forEach((b) => b.classList.toggle("on", b.dataset.task === task));
    let note = why ? `💡 Suggested: <b>${sug === "classification" ? "Categories" : "Numbers"}</b>. ${why}` : "";
    if (col && task === "regression" && col.type === "text") note = `<span style="color:var(--err)">⚠ ${esc(col.name)} contains text, so it can't be treated as numbers. Choose Categories.</span>`;
    else if (col && task !== sug && $("#mt-task").value !== "auto") note += ` <span style="color:var(--warn)">You chose ${task === "classification" ? "Categories" : "Numbers"}: fine if you know the data.</span>`;
    $("#mt-task-suggest").innerHTML = note;
    const counts = d?.meta?.target === target ? d.meta.class_counts : null;
    let html = "";
    if (task === "classification" && counts) {
      const entries = Object.entries(counts), max = Math.max(...entries.map(([, n]) => n));
      const minN = Math.min(...entries.map(([, n]) => n));
      html += `<div class="dist" style="margin-top:6px">${entries.slice(0, 12).map(([k, n]) => `<div style="grid-template-columns:1fr 3fr auto"><span>${esc(k)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(2, 100 * n / max)}%"></span></span><b>${n.toLocaleString()}</b></div>`).join("")}</div>` +
        (max / Math.max(1, minN) > 5 ? `<p class="hint">Classes are unbalanced (${max.toLocaleString()} vs ${minN.toLocaleString()}). Consider <b>Class balancing → Balanced</b> under Parameters, and tune for <b>Macro F1</b>.</p>` : "");
    }
    if (task === "classification" && col && col.unique > 50) html += `<p class="hint" style="color:var(--warn)">${col.unique} distinct values: is this really a class column? Choose "Numbers on a scale" for continuous values.</p>`;
    $("#mt-target-info").innerHTML = html;
    renderModelCards();
  }
  $("#mt-table").onchange = () => loadTableDesc($("#mt-table").value);
  // the previous target goes back to its normal role (e.g. a numeric column becomes a feature again)
  function restoreOldTarget(old) {
    const c = mlx.desc?.columns.find((x) => x.name === old);
    if (c && mlx.cols[old] && old !== $("#mt-target").value) mlx.cols[old].role = colDefaults(c).role;
    const t = $("#mt-target").value;
    if (mlx.cols[t]) mlx.cols[t].role = "ignore";
    mlx.prevTarget = t;
  }
  $("#mt-target").onfocus = () => { mlx.prevTarget = $("#mt-target").value; };
  $("#mt-target").onchange = () => { $("#mt-task").value = "auto"; restoreOldTarget(mlx.prevTarget); renderColumns(false); renderTargetInfo(); };
  $$("#mt-task-choice [data-task]").forEach((b) => b.onclick = () => { $("#mt-task").value = b.dataset.task; renderTargetInfo(); });
  $("#mt-col-filter").oninput = () => renderColumns(false);
  const bulk = (fn) => { if (!mlx.desc) return; mlx.desc.columns.forEach((c) => { if (c.name !== $("#mt-target").value) mlx.cols[c.name].role = fn(c) ? "feature" : "ignore"; }); renderColumns(false); };
  $("#mt-feats-bands").onclick = () => { const bands = new Set(mlx.desc?.meta?.band_columns || []); bulk((c) => bands.size ? bands.has(c.name) : colDefaults(c).role === "feature" && c.type !== "text"); };
  $("#mt-feats-all").onclick = () => bulk((c) => colDefaults(c).role === "feature");
  $("#mt-feats-none").onclick = () => bulk(() => false);

  // ---------------- train: model cards + parameters
  function renderModelCards() {
    const sc = mlx.schema, task = detectTask();
    $("#mt-family").classList.add("hidden");   // families are groups inside the dropdown
    const usable = (k, m) => m.tasks.includes(task) && !sc.unavailable.includes(k);
    if (!usable(mlx.model, sc.models[mlx.model])) mlx.model = "rf";
    const fams = [...new Set(Object.values(sc.models).map((m) => m.family))];
    const items = fams.flatMap((f) => Object.entries(sc.models).filter(([, m]) => m.family === f).map(([k, m]) => {
      const why = !m.tasks.includes(task) ? `Not for ${task}` : sc.unavailable.includes(k) ? "Not installed" : "";
      return { id: k, title: m.title, group: f, badge: m.recommended ? "recommended" : "", tip: descTip(m.desc, m.tip), disabled: !usable(k, m), why };
    }));
    modelPicker($("#mt-models"), { items, value: mlx.model, onChange: (k) => {
      mlx.model = k;
      renderModelCards();
      if ($("#mt-name").value.match(/^[A-Za-z]+_/)) $("#mt-name").value = $("#mt-name").value.replace(/^[A-Za-z]+_/, MODEL_TITLE() + "_");
    } });
    renderTrainParams(true);
  }
  function renderTrainParams(keepCommon) {
    const sc = mlx.schema, m = sc.models[mlx.model], task = detectTask();
    const fits = (p) => !p.tasks || p.tasks.includes(task);
    const prevCommon = keepCommon ? mlCollect("common") : {};
    const mp = m.params.filter(fits), cp = sc.common.filter(fits).filter((p) => p.group !== "prep");
    const prepPrev = keepCommon ? mlCollect("common") : {};
    renderPrep(sc.common.filter(fits).filter((p) => p.group === "prep"), prepPrev);
    const val = (p, prev) => (p.name in prev ? prev[p.name] : p.default);
    $("#mt-params").innerHTML = mp.filter((p) => !p.advanced).map((p) => mlField(p, p.default, "model")).join("") +
      cp.filter((p) => !p.advanced).map((p) => mlField(p, val(p, prevCommon), "common")).join("") +
      (mp.filter((p) => !p.advanced).length ? "" : `<p class="hint">${esc(m.title)} has no settings to tune here: it works well as is.</p>`);
    $("#mt-adv").innerHTML = mp.filter((p) => p.advanced).map((p) => mlField(p, p.default, "model")).join("") +
      cp.filter((p) => p.advanced).map((p) => mlField(p, val(p, prevCommon), "common")).join("");
    const mr = $('[data-p="max_train_rows"]');
    if (mr) mr.placeholder = `model default: ${m.max_rows.toLocaleString()}`;
    renderSpace(false);
  }
  $("#mt-reset").onclick = () => renderTrainParams(false);

  // ---------------- train: preprocessing card (missing values → columns → outliers → skew → scaling → target)
  function renderPrep(specs, prev) {
    $("#mt-prep").innerHTML = specs.map((p) => mlField(p, p.name in prev ? prev[p.name] : p.default, "common")).join("");
    $$("#mt-prep [data-scope]").forEach((i) => i.addEventListener("change", updatePrepFlow));
    updatePrepFlow();
  }
  function updatePrepFlow() {
    const v = mlCollect("common"), m = mlx.schema.models[mlx.model];
    const pctRow = $('#mt-prep [data-p="outlier_pct"]')?.closest(".pca-field");
    if (pctRow) pctRow.classList.toggle("hidden", v.outliers !== "clip");
    const scaling = v.scaling === "auto" ? (m.scale ? "standard" : "none") : v.scaling;
    const steps = [
      v.missing === "impute" ? "Fill missing" : "Drop rows with missing",
      v.drop_constant || v.drop_correlated !== "off" ? `Remove ${[v.drop_constant && "constant", v.drop_correlated !== "off" && `|r| ≥ ${v.drop_correlated}`].filter(Boolean).join(" + ")} columns` : null,
      v.outliers === "clip" ? `Clip ${v.outlier_pct ?? 1}–${100 - (v.outlier_pct ?? 1)}%` : null,
      v.skew !== "none" && v.skew ? `Yeo-Johnson (${v.skew === "auto" ? "skewed" : "all"})` : null,
      scaling !== "none" ? { standard: "Standard scale", minmax: "Min–max 0–1", robust: "Robust scale" }[scaling] + (v.scaling === "auto" ? " (auto)" : "") : (v.scaling === "auto" ? "No scaling (not needed)" : "No scaling"),
      selCategorical().length ? `One-hot ${selCategorical().length} categorical` : null,
      v.target_transform && v.target_transform !== "none" ? (v.target_transform === "log" ? "Target log(1+y)" : "Target Yeo-Johnson") : null,
    ].filter(Boolean);
    const warn = scaling === "none" && m.scale ? `<div class="hint" style="color:var(--warn)">${esc(m.title)} works much better with scaled features.</div>` : "";
    $("#mt-prep-flow").innerHTML = `<div class="home-label" style="margin:10px 0 4px">Pipeline</div><div class="flow">${steps.map((t) => `<span>${esc(t)}</span>`).join("<i>→</i>")}<i>→</i><span class="flow-model">${esc(m.title)}</span></div>${warn}`;
  }
  $("#mt-prep-reset").onclick = () => renderPrep(mlx.schema.common.filter((p) => p.group === "prep" && (!p.tasks || p.tasks.includes(detectTask()))), {});

  // ---------------- train: hyperparameter tuning
  const fmtCand = (v) => v === null ? "None" : String(v).replace(/,/g, ";");
  function renderSpace(reset) {
    const sc = mlx.schema, m = sc.models[mlx.model], task = detectTask();
    const prev = {};
    if (!reset) $$("#mt-space [data-sp]").forEach((i) => prev[i.dataset.sp] = { on: $(`[data-spon="${i.dataset.sp}"]`).checked, v: i.value });
    const space = sc.search[mlx.model] || {};
    const specs = m.params.filter((p) => (!p.tasks || p.tasks.includes(task)) && p.name in space);
    $("#mt-space").innerHTML = specs.length ? specs.map((p) => {
      const pv = mlx.spaceModel === mlx.model ? prev[p.name] : null;
      return `<div class="space-row"><label class="inline"><input type="checkbox" data-spon="${p.name}" ${pv ? (pv.on ? "checked" : "") : "checked"}> ${esc(p.label)}${tipBtn(p.tip)}</label>
        <input data-sp="${p.name}" value="${esc(pv ? pv.v : space[p.name].map(fmtCand).join(", "))}"></div>`;
    }).join("") : `<p class="hint">${esc(m.title)} has no settings worth tuning. Turn tuning off, or pick another model.</p>`;
    mlx.spaceModel = mlx.model;
    const metrics = sc.tune_metrics[task] || [];
    const cur = $("#mt-tune-metric").value;
    $("#mt-tune-metric").innerHTML = metrics.map(([v, t]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(t)}</option>`).join("");
    $$("#mt-space input").forEach((i) => i.oninput = i.onchange = tuneEstimate);
    tuneEstimate();
  }
  function collectSpace() {
    const out = {};
    $$("#mt-space [data-sp]").forEach((i) => {
      if (!$(`[data-spon="${i.dataset.sp}"]`).checked) return;
      const vals = i.value.split(",").map((v) => v.trim()).filter(Boolean);
      if (vals.length) out[i.dataset.sp] = vals;
    });
    return out;
  }
  function tuneEstimate() {
    const sp = collectSpace(), grid = Object.values(sp).reduce((a, v) => a * v.length, 1);
    const folds = Math.max(2, +$("#mt-tune-folds").value || 3);
    const tries = mlx.tuneMethod === "grid" ? grid : Math.min(grid, Math.max(1, +$("#mt-tune-iter").value || 20));
    $("#mt-tune-iter").disabled = mlx.tuneMethod === "grid";
    const n = Object.keys(sp).length;
    $("#mt-tune-est").innerHTML = !n ? `<span style="color:var(--warn)">Tick at least one parameter to tune.</span>` :
      `${n} parameter${n > 1 ? "s" : ""} · ${grid.toLocaleString()} possible combinations · <b>${tries.toLocaleString()} tries × ${folds} folds = ${(tries * folds).toLocaleString()} fits</b>, then one final fit` +
      (mlx.tuneMethod === "grid" && grid > 300 ? `<br><span style="color:var(--err)">Too many for grid search (max 300). Use random search or fewer values.</span>` : "") +
      (tries * folds > 150 ? `<br><span style="color:var(--warn)">This may take a while. Lower "Max training rows" (Advanced) to speed it up.</span>` : "");
  }
  $("#mt-tune").onchange = () => { $("#mt-tune-body").classList.toggle("hidden", !$("#mt-tune").checked); $("#mt-run").textContent = $("#mt-tune").checked ? "Tune & train model" : "Train model"; };
  $$("#mt-tune-method [data-m]").forEach((b) => b.onclick = () => { mlx.tuneMethod = b.dataset.m; $$("#mt-tune-method [data-m]").forEach((x) => x.classList.toggle("active", x === b)); tuneEstimate(); });
  $("#mt-tune-iter").oninput = $("#mt-tune-folds").oninput = tuneEstimate;
  $("#mt-tune-reset").onclick = () => renderSpace(true);
  const tuningPayload = () => $("#mt-tune").checked ? { enabled: true, method: mlx.tuneMethod, iter: +$("#mt-tune-iter").value || 20,
    folds: +$("#mt-tune-folds").value || 3, metric: $("#mt-tune-metric").value || "auto", space: collectSpace() } : {};

  function trainInputs() {
    const table = $("#mt-table").value, target = $("#mt-target").value, features = selFeatures();
    if (!table) { toast("Choose a training table first", true); return null; }
    if (!features.length) { toast("Select at least one feature (Role → Feature)", true); return null; }
    return { table, target, features, categorical: selCategorical(), task: detectTask(), common: mlCollect("common") };
  }
  $("#mt-run").onclick = async () => {
    const err = $("#mt-error"); err.classList.add("hidden");
    const inp = trainInputs(); if (!inp) return;
    const tuning = tuningPayload();
    if (tuning.enabled && !Object.keys(tuning.space).length) return toast("Tick at least one parameter to tune", true);
    const btn = $("#mt-run"); btn.disabled = true; $("#mt-compare").disabled = true;
    $("#mt-result").classList.add("hidden");
    try {
      const job = await api("/api/ml/train", { method: "POST", json: { ...inp, model: mlx.model, params: mlCollect("model"), tuning,
        name: $("#mt-name").value || "model" } });
      const done = await trackJob(job, { tool: "ml", save: "train", title: `${tuning.enabled ? "Tuning" : "Training"} ${mlx.schema.models[mlx.model].title}` });
      showTrainResult(done.result, $("#mt-result"));
      refreshModels();
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; $("#mt-compare").disabled = false; }
  };
  $("#mt-compare").onclick = async () => {
    const err = $("#mt-error"); err.classList.add("hidden");
    const inp = trainInputs(); if (!inp) return;
    const btn = $("#mt-compare"); btn.disabled = true; $("#mt-run").disabled = true;
    const box = $("#mt-compare-box"); box.classList.add("hidden");
    try {
      const job = await api("/api/ml/compare", { method: "POST", json: inp });
      const done = await trackJob(job, { tool: "ml", title: "Comparing models" });
      showLeaderboard(done.result, box);
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; $("#mt-run").disabled = false; }
  };
  function showLeaderboard(r, box) {
    const cls = r.task === "classification", ok = r.rows.filter((x) => !x.error), best = ok[0];
    const cols = cls ? [["accuracy", "Acc.", pct], ["kappa", "Kappa", (v) => v.toFixed(3)]] : [["r2", "R²", (v) => v.toFixed(3)], ["rmse", "RMSE", fmtv]];
    const top = best ? best[r.metric] : 0;
    box.innerHTML = `<div class="card"><div class="row between"><h2 style="margin:0">🏆 Model comparison</h2><button class="chip" data-close>✕</button></div>
      <p class="hint">Every model trained with default settings on the same train/test split (max ${r.max_rows.toLocaleString()} training rows each), ranked by ${cls ? "kappa" : "R²"}. Pick one, then adjust or tune it and train.</p>
      <table class="data-table lb-table"><tr><th></th><th style="text-align:left">Model</th>${cols.map(([, t]) => `<th>${t}</th>`).join("")}<th></th></tr>
      ${r.rows.map((x, i) => x.error ? `<tr class="is-off"><td></td><td style="text-align:left" colspan="${cols.length + 2}">${esc(x.title)} <small class="hint">failed: ${esc(x.error)}</small></td></tr>` :
        `<tr class="${i === 0 ? "lb-best" : ""}" title="${cls ? `Macro F1 ${pct(x.f1_macro)} · balanced accuracy ${pct(x.balanced_accuracy)}` : `MAE ${fmtv(x.mae)}`}"><td>${i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : i + 1}</td>
          <td style="text-align:left"><b>${esc(x.title)}</b> <small class="hint">${x.seconds}s</small><span class="lb-bar"><span style="width:${Math.max(2, 100 * Math.max(0, x[r.metric]) / Math.max(1e-9, top))}%"></span></span></td>
          ${cols.map(([k, , f]) => `<td>${f(x[k])}</td>`).join("")}<td><button class="btn small ${i === 0 ? "primary" : ""}" data-use="${x.model}">Use</button></td></tr>`).join("")}</table>
      ${ok.length ? splitBadge(ok[0].split) : ""}</div>`;
    box.classList.remove("hidden");
    $("[data-close]", box).onclick = () => box.classList.add("hidden");
    $$("[data-use]", box).forEach((b) => b.onclick = () => {
      mlx.model = b.dataset.use; mlx.family = "All"; renderModelCards();
      $("#mt-name").value = $("#mt-name").value.replace(/^[A-Za-z]+_/, MODEL_TITLE() + "_");
      $("#mt-models").scrollIntoView({ behavior: "smooth", block: "center" });
      toast(`${mlx.schema.models[mlx.model].title} selected. Adjust or tune it, then Train.`);
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------- results dashboard (also used for saved model reports)
  function showTrainResult(r, box) {
    const cls = r.task === "classification";
    const grade = (v) => v >= 0.85 ? "good" : v >= 0.7 ? "ok" : "bad";
    let tiles;
    if (cls) {
      tiles = [["Overall accuracy", r.accuracy, "Share of test pixels classified correctly."],
               ["Kappa", r.kappa, "Agreement beyond chance (0 = random, 1 = perfect). Above 0.8 is excellent, 0.6–0.8 good."],
               ["Macro F1", r.f1_macro, "Average F1 over classes: treats rare classes as important as common ones."],
               ["Balanced acc.", r.balanced_accuracy, "Average recall over classes."]]
        .map(([k, v, t]) => `<div class="metric ${grade(v)}"><b>${k === "Kappa" ? v.toFixed(3) : pct(v)}</b><span>${k} ${tipBtn(t)}</span></div>`).join("");
    } else {
      tiles = `<div class="metric ${grade(r.r2)}"><b>${r.r2.toFixed(3)}</b><span>R² ${tipBtn("Share of the variation explained (1 = perfect, 0 = no better than the mean).")}</span></div>
        <div class="metric"><b>${fmtv(r.rmse)}</b><span>RMSE ${tipBtn("Typical prediction error, in the label's units.")}</span></div>
        <div class="metric"><b>${fmtv(r.mae)}</b><span>MAE ${tipBtn("Average absolute error.")}</span></div>
        <div class="metric"><b>${r.test_rows.toLocaleString()}</b><span>Test rows</span></div>`;
    }
    let cm = "", perClass = "";
    if (cls) {
      const k = r.classes.length, max = Math.max(1, ...r.confusion.flat());
      const rowTot = r.confusion.map((row) => row.reduce((a, b) => a + b, 0));
      const colTot = r.classes.map((_, j) => r.confusion.reduce((a, row) => a + row[j], 0));
      const short = (c) => esc(String(c).slice(0, 9));
      cm = `<div class="home-label" style="margin-top:14px">Confusion matrix ${tipBtn("Rows = true class, columns = predicted class, numbers = test pixels. The diagonal (green) is correct. Off-diagonal cells show which classes get confused. Producer's accuracy = recall (row), user's accuracy = precision (column).")}</div>
        <div class="load-wrap"><table class="cm-table"><tr><th></th>${r.classes.map((c) => `<th title="${esc(c)}">${short(c)}</th>`).join("")}<th title="Producer's accuracy (recall)">PA</th></tr>
        ${r.confusion.map((row, i) => `<tr><th class="rowh" title="${esc(r.classes[i])}">${short(r.classes[i])}</th>${row.map((v, j) => {
          const a = v / max, diag = i === j;
          return `<td style="background:${diag ? `rgba(21,128,61,${0.15 + 0.75 * a})` : v ? `rgba(185,28,28,${0.12 + 0.7 * a})` : "transparent"};color:${a > 0.5 ? "#fff" : "inherit"}">${v || ""}</td>`;
        }).join("")}<td><b>${rowTot[i] ? Math.round(100 * row[i] / rowTot[i]) : "–"}</b></td></tr>`).join("")}
        <tr><th class="rowh" title="User's accuracy (precision)">UA</th>${r.classes.map((_, j) => `<td><b>${colTot[j] ? Math.round(100 * r.confusion[j][j] / colTot[j]) : "–"}</b></td>`).join("")}<td></td></tr></table></div>`;
      perClass = `<details class="an-details"><summary>Per-class scores</summary><table class="data-table"><tr><th style="text-align:left">Class</th><th>Precision (UA)</th><th>Recall (PA)</th><th>F1</th><th>Test px</th></tr>
        ${r.per_class.map((c) => `<tr><td style="text-align:left">${esc(c.class)}</td><td>${pct(c.precision)}</td><td>${pct(c.recall)}</td><td>${pct(c.f1)}</td><td>${c.support.toLocaleString()}</td></tr>`).join("")}</table></details>`;
    } else if (r.scatter) {
      const t = r.scatter.true, p = r.scatter.pred, lo = Math.min(...t, ...p), hi = Math.max(...t, ...p), sc = (v) => 10 + 280 * (v - lo) / ((hi - lo) || 1);
      cm = `<div class="home-label" style="margin-top:14px">Predicted vs true (test sample)</div><svg viewBox="0 0 300 300" class="ev-chart" style="max-width:300px">
        <line x1="10" y1="290" x2="290" y2="10" stroke="var(--muted)" stroke-dasharray="4 3"/>${t.map((v, i) => `<circle cx="${sc(v)}" cy="${300 - sc(p[i])}" r="2" fill="var(--accent)" opacity=".5"/>`).join("")}</svg>`;
    }
    const imp = r.importance ? `<div class="home-label" style="margin-top:14px">Feature importance ${tipBtn("Which inputs the model relies on most. " + r.importance.kind + ".")}</div>
      ${r.importance.features.slice(0, 15).map((f, i) => `<div class="imp-row"><span title="${esc(f)}">${esc(f)}</span><span><span class="imp-bar" style="display:block;width:${Math.max(2, 100 * r.importance.values[i] / r.importance.values[0])}%"></span></span><span>${(r.importance.values[i] * 100).toFixed(1)}%</span></div>`).join("")}` : "";
    const tn = r.tuning, lowBetter = tn?.lower_is_better, fmtS = (v) => tn && ["accuracy", "f1_macro", "balanced_accuracy"].includes(tn.metric) ? pct(v) : fmtv(v);
    const tune = tn ? `<div class="home-label" style="margin-top:14px">Hyperparameter tuning ${tipBtn(`${tn.method === "grid" ? "Grid" : "Random"} search: ${tn.candidates} combinations × ${tn.folds} folds${tn.grouped ? ", folds grouped by polygon / block" : ""}. Scored on training rows only; the test scores above are from the untouched test split.`)}</div>
      <div class="tune-best">Best ${esc(tn.metric.replace("neg_", "").toUpperCase())} in CV: <b>${fmtS(tn.best_score)}</b> with ${Object.entries(tn.best_params).map(([k, v]) => `<code>${esc(k)} = ${esc(fmtCand(v))}</code>`).join(" ")}</div>
      <details class="an-details"><summary>Top ${tn.results.length} of ${tn.candidates} combinations</summary><div class="load-wrap"><table class="data-table"><tr><th>#</th>${Object.keys(tn.best_params).map((k) => `<th>${esc(k)}</th>`).join("")}<th>CV score</th><th>±</th></tr>
      ${tn.results.map((x, i) => `<tr class="${i === 0 ? "lb-best" : ""}"><td>${i + 1}</td>${Object.keys(tn.best_params).map((k) => `<td>${esc(fmtCand(x.params[k]))}</td>`).join("")}<td><b>${fmtS(x.mean)}</b></td><td>${fmtS(x.std)}</td></tr>`).join("")}</table></div>
      ${lowBetter ? '<p class="hint">Lower is better for this metric.</p>' : ""}</details>` : "";
    const cv = r.cv ? `<p class="hint">${r.cv.folds.length}-fold cross-validation ${r.cv.metric}: <b>${r.cv.metric === "accuracy" ? pct(r.cv.mean) : r.cv.mean.toFixed(3)}</b> ± ${r.cv.metric === "accuracy" ? pct(r.cv.std) : r.cv.std.toFixed(3)}</p>` : "";
    box.innerHTML = `<div class="card">
      <div class="row between"><h2 style="margin:0">✓ ${esc(r.model_title)} trained</h2><span class="task-badge">${esc(r.task)}</span></div>
      ${splitBadge(r.split)}
      <div class="pca-sum" style="margin-top:4px">${r.train_rows.toLocaleString()} training rows${r.train_rows_before_sampling > r.train_rows ? ` (sampled from ${r.train_rows_before_sampling.toLocaleString()})` : ""} · ${r.test_rows.toLocaleString()} test rows · ${r.features.length} features · ${r.seconds} s${r.rows_dropped ? ` · ${r.rows_dropped.toLocaleString()} rows with missing values skipped` : ""}${r.rows_imputed ? ` · ${r.rows_imputed.toLocaleString()} rows had missing values filled in` : ""}</div>
      <div class="metric-tiles" style="margin-top:10px">${tiles}</div>
      ${r.warnings?.length ? `<div class="warn">${r.warnings.map(esc).join("<br>")}</div>` : ""}
      ${r.preprocessing ? `<div class="prep-res"><b>Preprocessing</b> ${r.preprocessing.steps.length ? r.preprocessing.steps.map(esc).join(" → ") : "none needed"}${r.categorical?.length ? ` · one-hot: ${r.categorical.map(esc).join(", ")}` : ""}
        ${r.preprocessing.removed?.length ? `<br><b>Removed columns</b> ${r.preprocessing.removed.map((x) => `<span title="${esc(x.reason)}">${esc(x.feature)}</span> <small>(${esc(x.reason)})</small>`).join(", ")}` : ""}</div>`
        : r.categorical?.length ? `<p class="hint">Categorical (one-hot encoded): ${r.categorical.map(esc).join(", ")}</p>` : ""}
      ${tune}${cv}${cm}${perClass}${imp}
      <div class="row" style="flex-wrap:wrap">${cls || r.task === "regression" ? `<button class="btn small primary" data-classify="${esc(r.path)}">${cls ? "Classify an image with this model →" : "Apply to an image →"}</button>` : ""}
        <a class="btn small" href="/api/models/file?path=${encodeURIComponent(r.path)}" download>⬇ Model (.joblib)</a></div>
      <div class="eval-cta"><span>📊</span><span><b>Evaluation report</b><small>Confusion matrices, per-class scores, ${cls ? "ROC and precision–recall curves, confidence and calibration charts" : "predicted-vs-true, residual and Q–Q plots, error by value range"}, feature importance${r.tuning ? ", tuning results" : ""}. One HTML file, opens offline.</small></span>
        <span class="row tight"><a class="btn small primary" href="/api/models/evaluation?path=${encodeURIComponent(r.path)}" target="_blank" rel="noopener">Open</a><a class="btn small" href="/api/models/evaluation?path=${encodeURIComponent(r.path)}&download=true">⬇ .html</a></span>
        <div class="eval-save">${r.evaluation_saved_to ? `<div class="eval-saved">✓ Saved to <code title="${esc(r.evaluation_saved_to)}">${esc(r.evaluation_saved_to)}</code></div>` : ""}
          <div class="row tight"><input data-evdir placeholder="Folder, e.g. ~/Documents/LULC reports" value="${esc(prefs.get("report-dir", ""))}" spellcheck="false" autocomplete="off"><button class="btn small" data-evsave>${r.evaluation_saved_to ? "Save another copy" : "Save a copy"}</button></div></div></div>
      <p class="hint">Saved as <code>${esc(r.path)}</code>.</p></div>`;
    box.classList.remove("hidden");
    $("[data-classify]", box)?.addEventListener("click", () => { openMlSub("predict"); refreshPredict(r.path); });
    $("[data-evsave]", box)?.addEventListener("click", async (e) => {
      const folder = $("[data-evdir]", box).value.trim();
      if (!folder) return toast("Type the folder to save the report in", true);
      await busy(e.currentTarget, "Saving…", async () => {
       try {
        const res = await api("/api/models/evaluation/save", { method: "POST", json: { path: r.path, folder } });
        prefs.set("report-dir", folder);
        let el = $(".eval-saved", box);
        if (!el) { el = document.createElement("div"); el.className = "eval-saved"; $(".eval-save", box).prepend(el); }
        el.innerHTML = `✓ Saved to <code title="${esc(res.saved_to)}">${esc(res.saved_to)}</code>`;
        toast("Evaluation report saved");
       } catch (err) { toast(err.message, true); }
      });
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function splitBadge(sp) {
    if (!sp) return "";
    if (sp.method === "group") return `<div style="margin-top:6px"><span class="split-badge honest">✓ Independent test: by polygon</span> <span class="hint">${sp.groups_test} of ${sp.groups_total} polygons held out for testing</span></div>`;
    if (sp.method === "blocks") return `<div style="margin-top:6px"><span class="split-badge honest">✓ Independent test: spatial blocks</span> <span class="hint">${sp.groups_test} of ${sp.groups_total} blocks (${fmtv(sp.block_size)} map units) held out</span></div>`;
    return `<div style="margin-top:6px"><span class="split-badge optimistic">⚠ Random pixel split</span> <span class="hint">Neighbouring pixels are in both train and test, so accuracy is probably optimistic. Use polygon samples or x/y columns for an honest test.</span></div>`;
  }

  // ---------------- model library (hub)
  async function refreshModels() {
    const list = await api("/api/models").catch(() => []);
    $("#ml-models").innerHTML = list.length ? list.map((m) => `<div class="ws-row"><span>${esc(m.name)}
        <small>${esc(m.model_title || m.model)} · ${esc(m.task || "")} · <span class="model-row-metric">${m.kind === "clustering" ? `${m.n_clusters} clusters${m.silhouette != null ? `, silhouette ${m.silhouette.toFixed(2)}` : ""}` : m.task === "classification" ? `accuracy ${pct(m.accuracy)}, kappa ${m.kappa?.toFixed(2)}` : `R² ${m.r2?.toFixed(3)}`}</span> · ${(m.features || []).length} features</small></span>
        <span class="row tight"><button class="btn small primary" data-mcls="${esc(m.path)}" title="Classify an image">Use</button><button class="btn small" data-mrep="${esc(m.path)}">Report</button>${m.kind === "clustering" ? "" : `<a class="btn small" href="/api/models/evaluation?path=${encodeURIComponent(m.path)}" target="_blank" rel="noopener" title="Full evaluation report with matrices and charts (opens in a new tab)">📊</a>`}<a class="btn small" href="/api/models/file?path=${encodeURIComponent(m.path)}" download>⬇</a><button class="btn small danger" data-mdel="${esc(m.path)}" title="Delete">×</button></span></div>`).join("")
      : '<p class="hint">No models yet. Use <b>Train a model</b>.</p>';
    $$("[data-mcls]").forEach((b) => b.onclick = () => { openMlSub("predict"); refreshPredict(b.dataset.mcls); });
    $$("[data-mrep]").forEach((b) => b.onclick = async () => {
      const r = await api(`/api/models/report?path=${encodeURIComponent(b.dataset.mrep)}`);
      $("#tbl-title").textContent = `${r.kind === "clustering" ? "Clustering" : "Model"} report · ${r.name}`;
      if (r.kind === "clustering") showClusterResult(r, $("#tbl-body")); else showTrainResult(r, $("#tbl-body"));
      $("#dlg-table").showModal();
    });
    $$("[data-mdel]").forEach((b) => b.onclick = async () => { if (confirm("Delete this model?")) { await api(`/api/models?path=${encodeURIComponent(b.dataset.mdel)}`, { method: "DELETE" }); refreshModels(); } });
    return list;
  }

  // ---------------- classify an image
  const colName = (d) => { let n = String(d).replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "band"; return /^\d/.test(n) ? "b" + n : n; };
  let mpModels = [];
  async function refreshPredict(selectModel) {
    mpModels = await api("/api/models").catch(() => []);
    const sel = $("#mp-model"), cur = selectModel || sel.value;
    sel.innerHTML = mpModels.length ? mpModels.map((m) => `<option value="${esc(m.path)}">${esc(m.name)} · ${esc(m.model_title || "")} · ${m.kind === "clustering" ? `${m.n_clusters} clusters (unsupervised)` : m.task === "classification" ? `acc ${pct(m.accuracy)}` : `R² ${m.r2?.toFixed(2)}`}</option>`).join("")
      : `<option value="">No trained models yet</option>`;
    if (cur && mpModels.some((m) => m.path === cur)) sel.value = cur;
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived);
    const rs = $("#mp-raster"), rcur = rs.value;
    rs.innerHTML = rasters.length ? rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("") : `<option value="">No raster layers</option>`;
    const m = mpModel();
    // prefer the layer the model was trained from
    const srcName = m?.source?.source ? String(m.source.source).split(/[\\/]/).pop() : null;
    const pick = rasters.find((l) => l.id === rcur) || rasters.find((l) => srcName && l.path.endsWith(srcName)) || rasters[0];
    if (pick) rs.value = pick.id;
    renderPredictForm();
  }
  const mpModel = () => mpModels.find((m) => m.path === $("#mp-model").value);
  function renderPredictForm() {
    const m = mpModel(), l = getLayer($("#mp-raster").value);
    $("#mp-model-info").innerHTML = m ? `${esc(m.model_title)} · ${esc(m.task)} · features: ${esc((m.features || []).join(", "))}` : "";
    if (!m || !l) { $("#mp-match").innerHTML = ""; return; }
    const bands = l.info.bands;
    const byName = Object.fromEntries(bands.map((b) => [colName(b.description), b.index]));
    const src = m.source || {};
    const rows = m.features.map((f) => {
      let idx = byName[f];
      if (idx == null && src.band_columns && src.band_indices) {
        const i = src.band_columns.indexOf(f);
        if (i >= 0 && src.band_indices[i] <= bands.length) idx = src.band_indices[i];
      }
      return { f, idx };
    });
    $("#mp-match").innerHTML = `<div class="home-label" style="margin-top:10px">Model feature → image band ${tipBtn("Each input the model was trained on must come from the matching band of this image. Bands are matched by name automatically. Check them if the image has a different band order.")}</div>` +
      rows.map(({ f, idx }) => `<div class="match-row"><span title="${esc(f)}">${esc(f)}</span><select data-feat="${esc(f)}"><option value="">— choose —</option>${bands.map((b) => `<option value="${b.index}" ${b.index === idx ? "selected" : ""}>Band ${b.index}${b.description !== "Band " + b.index ? " · " + esc(b.description) : ""}</option>`).join("")}</select><span data-ok="${esc(f)}">${idx ? "✓" : "⚠"}</span></div>`).join("");
    $$("#mp-match select").forEach((s) => s.onchange = () => { $(`[data-ok="${CSS.escape(s.dataset.feat)}"]`).textContent = s.value ? "✓" : "⚠"; });
    const sc = src.scale ?? 1, off = src.offset ?? 0, conv = sc !== 1 || off !== 0;
    $("#mp-refl").checked = conv;
    $("#mp-refl").disabled = !conv;
    $("#mp-refl").dataset.scale = sc; $("#mp-refl").dataset.offset = off;
    $("#mp-refl-val").textContent = conv ? `(× ${sc} ${off < 0 ? "−" : "+"} ${Math.abs(off)})` : "(not needed: the table kept the raw values)";
    $("#mp-conf").disabled = m.task !== "classification" || !m.has_proba;
    $("#mp-conf").checked = !$("#mp-conf").disabled;
    $("#mp-name").value = `${m.name}_map`.slice(0, 70);
  }
  function refreshPredictRasters() {  // keep the user's band choices unless the selected image disappears
    const rasters = layers.filter((l) => l.type === "raster" && !l.derived), rs = $("#mp-raster"), cur = rs.value;
    rs.innerHTML = rasters.length ? rasters.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("") : `<option value="">No raster layers</option>`;
    if (rasters.some((l) => l.id === cur)) rs.value = cur; else renderPredictForm();
  }
  $("#mp-model").onchange = renderPredictForm;
  $("#mp-raster").onchange = renderPredictForm;
  $("#mp-run").onclick = async () => {
    const err = $("#mp-error"); err.classList.add("hidden");
    const m = mpModel(), l = getLayer($("#mp-raster").value);
    if (!m) return toast("Train a model first", true);
    if (!l) return toast("Choose an image to classify", true);
    const band_map = {};
    for (const s of $$("#mp-match select")) { if (!s.value) return toast(`Choose a band for ${s.dataset.feat}`, true); band_map[s.dataset.feat] = +s.value; }
    const conv = $("#mp-refl").checked;
    const btn = $("#mp-run"); btn.disabled = true;
    $("#mp-result").classList.add("hidden");
    try {
      const job = await api("/api/ml/predict", { method: "POST", json: {
        model: m.path, path: l.path, band_map, scale: conv ? +$("#mp-refl").dataset.scale : 1, offset: conv ? +$("#mp-refl").dataset.offset : 0,
        clip: getClip("mp-area"), resolution: $("#mp-res").value, confidence: $("#mp-conf").checked, name: $("#mp-name").value || "classified" } });
      const done = await trackJob(job, { tool: "ml", save: "predict", title: `Classifying with ${m.model_title}` });
      const r = done.result;
      const out = await addRasterFromPath(r.path, { name: $("#mp-name").value || "classified",
        render: m.task === "classification" ? { band: 1, stretch: "fixed" } : { band: 1, stretch: "auto", cmap: "Viridis" } });
      out.open = true; renderContents();
      const box = $("#mp-result");
      box.innerHTML = `<div class="pca-sum" style="margin-top:12px"><b>✓ Map created</b> · ${r.width.toLocaleString()} × ${r.height.toLocaleString()} px${r.factor > 1 ? ` (${r.factor}× coarser)` : ""} · ${r.seconds} s${r.confidence ? " · band 2 = confidence %" : ""}. It's in Contents.</div>` +
        (r.classes ? `<div class="lyr-classes" style="margin-top:8px">${r.classes.map((c) => `<div style="grid-template-columns:12px 1fr auto auto;gap:8px"><i style="background:${esc(c.color)}"></i><span>${esc(c.name)}</span><span>${c.area_km2 != null ? fmt(c.area_km2, c.area_km2 < 10 ? 2 : 1) + " km²" : ""}</span><b>${fmt(c.pct)}%</b></div>`).join("")}</div>` : "") +
        `<div class="row"><button class="btn small" data-mpz>Zoom to map</button><button class="btn small" data-mpx>Export…</button></div>`;
      box.classList.remove("hidden");
      $("[data-mpz]", box).onclick = () => zoomTo(out);
      $("[data-mpx]", box).onclick = () => openExport(out);
    } catch (e) {
      if (notCancelled(e)) { err.textContent = e.message; err.classList.remove("hidden"); }
    } finally { btn.disabled = false; }
  };
  function wireGotoRt() { $$(".goto-rt").forEach((a) => a.onclick = (e) => { e.preventDefault(); switchTool("raster2table"); }); }
