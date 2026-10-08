/* Analysis ▸ Tools ▸ Workflows: chains of tool runs, made from the History, run again on new inputs (once, or for each
   of several layers or polygons). A step uses an earlier step's result where the original runs did. Each step runs
   through its tool's own endpoint, with the usual progress, Cancel and History; the last step's results go to Contents.
   Server: /api/workflows (list, read, save, delete, from-history) · webapp/workflows.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="6" height="5" rx="1"/><rect x="15" y="9.5" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><path d="M9 5.5h3v13H9M12 12h3"/></svg>';

  LF.tool({
    id: "workflows", title: "Workflows", icon: "workflow",
    subtitle: "Run a chain of tools again on new data: make a workflow from runs in the History (e.g. download → NDVI → export), then give it new inputs, once or for each layer or polygon",
    panel: `
      <div class="card">
        <h2>Your workflows ${tip("A workflow is a chain of tool runs. Make one from runs you already did (the History keeps their settings): the files and areas they used become its inputs, and where a run used an earlier run's result, the workflow does the same. Then run it on other data.")}</h2>
        <div id="wf-list" class="wf-list"></div>
        <div class="row tight" style="gap:6px;flex-wrap:wrap;margin-top:8px">
          <button class="btn small primary" id="wf-new">New from History…</button>
          <button class="btn small" id="wf-import">Import…</button>
          <input type="file" id="wf-file" accept=".json,application/json" hidden>
        </div>
      </div>
      <div id="wf-detail" class="hidden">
        <div class="card">
          <div class="row between" style="align-items:flex-start;gap:8px">
            <h2 id="wf-name" style="margin:0"></h2>
            <div class="row tight" style="gap:4px"><button class="btn small ghost" id="wf-edit">Edit</button><button class="btn small ghost" id="wf-export" title="Save it as a file to share">Export</button><button class="btn small ghost" id="wf-del">Delete</button></div>
          </div>
          <p class="hint" id="wf-desc"></p>
          <div id="wf-edit-box" class="hidden">
            <label>Name <input type="text" id="wf-ename" maxlength="120"></label>
            <label>What it does <textarea id="wf-edesc" rows="2" maxlength="2000" placeholder="e.g. Monthly NDVI of a field from Sentinel-2"></textarea></label>
          </div>
        </div>
        <div class="card">
          <h2>Inputs ${tip("What the workflow works on. Each starts as what the original runs used; choose a layer from Contents (or an area) to run it on other data.")}</h2>
          <div id="wf-inputs"></div>
          <div id="wf-batch-box" class="wf-batch"></div>
        </div>
        <div class="card">
          <div class="row between" style="align-items:center"><h2 style="margin:0">Steps ${tip("Edit ▸ Conditions… on a step: run it only if a value allows it (e.g. cloud cover below 20 %), and after it runs warn, alert or stop when a value crosses a limit or falls by an amount since the last run. Every step's result is also checked for what can't be right (an empty image, an index outside −1…1, a table without rows, mostly cloudy imagery).")}</h2>
            <button class="btn small" id="wf-diagram" title="The workflow as boxes and arrows: rearrange, rewire, add conditions">Diagram</button></div>
          <div id="wf-cautions"></div>
          <ol id="wf-steps" class="wf-steps"></ol>
          <label class="check" style="margin-top:6px"><input type="checkbox" id="wf-all-results"> Add every step's results to Contents (not only the last step's)</label>
          <label class="check"><input type="checkbox" id="wf-stop-crit" checked> Stop when a result can't be right ${tip("An empty image, an index outside −1…1, a table without rows, imagery that is mostly cloud: the next steps would only build on it. Untick to go on and only be warned.")}</label>
        </div>
        <div class="card" id="wf-sched-card">
          <h2>Schedule ${tip("Runs the workflow by itself while LULC Fetch is open, with the inputs chosen above (layers are used through their files). A run missed while the app was closed runs once when it opens. Date inputs can move with the run day, e.g. the last 30 days.")}</h2>
          <label class="check"><input type="checkbox" id="wfs-on"> Run it by itself</label>
          <div id="wfs-opts" class="hidden">
            <div class="grid2"><label>Every <select id="wfs-every"><option value="day">Day</option><option value="week">Week</option><option value="hours">Few hours</option></select></label>
              <label id="wfs-at-row">At <input type="time" id="wfs-at" value="07:00"></label>
              <label id="wfs-hours-row" class="hidden">Hours <input type="number" id="wfs-hours" value="6" min="1" max="720"></label></div>
            <label id="wfs-wd-row" class="hidden">On <select id="wfs-wd">${["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((d, i) => `<option value="${i}">${d}</option>`).join("")}</select></label>
            <div id="wfs-dates"></div>
            <details><summary class="hint">Alert on a result</summary>
              <p class="hint">When a value of the last step's result crosses a limit, e.g. <code>summary.mean_change</code> &lt; -0.1 or <code>summary.changed_pct</code> &gt; 5.</p>
              <div class="grid2"><label>Result field <input type="text" id="wfs-af" placeholder="summary.mean_change"></label>
                <label>Is <span class="row tight" style="gap:4px"><select id="wfs-aop" style="width:auto"><option>&lt;</option><option>&gt;</option><option>&lt;=</option><option>&gt;=</option></select><input type="number" step="any" id="wfs-av"></span></label></div></details>
            <label>Tell me <select id="wfs-notify"><option value="always">After every run</option><option value="fail">Only when it fails or alerts</option></select></label>
            <label class="check"><input type="checkbox" id="wfs-add" checked> Add the results to Contents</label>
            <div class="row tight" style="gap:6px;margin-top:6px"><button class="btn small primary" id="wfs-save">Save schedule</button></div>
          </div>
          <p class="hint" id="wfs-status"></p>
        </div>
        <div class="row tight" style="gap:6px;margin:4px 0 10px">
          <button class="btn primary" id="wf-run">Run workflow</button>
          <button class="btn hidden" id="wf-save">Save changes</button>
          <button class="btn ghost hidden" id="wf-cancel-edit">Cancel</button>
        </div>
        <div id="wf-result" class="card hidden"></div>
      </div>`,

    setup(LF) {
      const { $, $$, esc, api, toast, status, prefs, layers, getLayer, dataItems, trackJob, notCancelled, addRasterFromPath, addVectorLayer, addItem, map } = LF;
      const st = { list: [], wf: null, id: null, dirty: false, editing: false, running: false };
      const diagram = makeDiagram();

      // ---- the list
      async function loadList(selectId) {
        try { st.list = (await api("/api/workflows")).workflows; } catch (e) { toast(e, true); st.list = []; }
        $("#wf-list").innerHTML = st.list.length ? st.list.map((w) => `<div class="wf-item ${w.id === (selectId || st.id) ? "on" : ""}" data-wf="${esc(w.id)}" role="button" tabindex="0">
            <span class="wf-ic">${ICON}</span><span class="wf-t"><b>${esc(w.name)}</b><small>${w.steps} step${w.steps === 1 ? "" : "s"} · ${w.inputs} input${w.inputs === 1 ? "" : "s"}${w.updated ? ` · ${esc(w.updated.slice(0, 10))}` : ""}</small></span></div>`).join("")
          : `<p class="hint" style="margin:2px 0">No workflows yet. <b>New from History…</b> makes one from tools you have run.</p>`;
        $$("#wf-list [data-wf]").forEach((el) => {
          el.onclick = () => openWorkflow(el.dataset.wf);
          el.onkeydown = (e) => { if (e.key === "Enter") openWorkflow(el.dataset.wf); };
        });
      }
      async function openWorkflow(id) {
        if (st.running) return toast("A workflow is running → wait for it, or cancel it", true);
        try { st.wf = await api(`/api/workflows/${encodeURIComponent(id)}`); } catch (e) { return toast(e, true); }
        st.id = id; st.editing = false; st.dirty = false;
        $$("#wf-list [data-wf]").forEach((el) => el.classList.toggle("on", el.dataset.wf === id));
        renderDetail();
      }
      // a workflow not saved yet (made from the History, or imported): it opens in edit mode to be named and saved
      function openUnsaved(wf, note) {
        st.wf = wf; st.id = null; st.editing = true; st.dirty = true;
        $$("#wf-list [data-wf]").forEach((el) => el.classList.remove("on"));
        renderDetail();
        $("#wf-ename").focus(); $("#wf-ename").select();
        if (note) toast(note);
      }

      // ---- the workflow: name, inputs, steps
      function renderDetail() {
        const wf = st.wf;
        $("#wf-detail").classList.remove("hidden");
        $("#wf-result").classList.add("hidden");
        $("#wf-name").textContent = wf.name + (st.id ? "" : " (not saved)");
        $("#wf-desc").textContent = wf.description || "";
        $("#wf-edit-box").classList.toggle("hidden", !st.editing);
        $("#wf-ename").value = wf.name; $("#wf-edesc").value = wf.description || "";
        $("#wf-save").classList.toggle("hidden", !st.editing);
        $("#wf-cancel-edit").classList.toggle("hidden", !st.editing || !st.id);
        $("#wf-edit").classList.toggle("hidden", st.editing);
        $("#wf-del").classList.toggle("hidden", !st.id);
        $("#wf-run").textContent = st.id ? "Run workflow" : "Run (without saving)";
        $("#wf-stop-crit").checked = wf.stop_on_critical !== false;
        renderInputs(); renderSteps(); renderSchedule(); renderCautions();
        diagram.refresh();
      }
      $("#wf-stop-crit").onchange = (e) => { st.wf.stop_on_critical = e.target.checked; if (st.id && !st.editing) save({ quiet: true }); else st.dirty = true; };
      // what may go wrong when it runs, from its settings alone (nothing is refused)
      async function renderCautions() {
        const box = $("#wf-cautions"), wf = st.wf;
        box.innerHTML = "";
        try {
          const c = (await api("/api/workflows/cautions", { method: "POST", json: { workflow: wf, saved: !!st.id } })).cautions;
          if (wf === st.wf && c.length) box.innerHTML = `<div class="warn wf-cautions"><b>Before you run</b><ul>${c.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
        } catch {}
      }
      const baseName = (p) => String(p || "").split(/[\\/]/).pop();
      const polyLayers = () => layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)));
      function fileChoices(inp) {   // Contents entries of the same kind, with a file on disk
        if (inp.kind === "table") return dataItems.filter((d) => d.kind === "table" && d.path).map((d) => [`item:${d.path}`, d.name]);
        if (inp.kind === "vector") return layers.filter((l) => l.type === "vector" && l.geojson?.features?.length).map((l) => [`layer:${l.id}`, l.name]);
        return layers.filter((l) => l.type === "raster" && l.path).map((l) => [`layer:${l.id}`, l.name]);
      }
      function renderInputs() {
        const wf = st.wf, box = $("#wf-inputs"), was = {};
        $$("[data-in]", box).forEach((el) => { was[el.dataset.in] = el.value; });   // keep what was chosen when Contents changes
        if (!wf.inputs.length) { box.innerHTML = `<p class="hint">This workflow has no inputs: it runs the same every time.</p>`; $("#wf-batch-box").innerHTML = ""; return; }
        box.innerHTML = wf.inputs.map((inp) => {
          const label = st.editing ? `<input type="text" class="wf-label" data-label="${esc(inp.id)}" value="${esc(inp.label)}" maxlength="120">` : esc(inp.label);
          if (inp.type === "file") return `<label class="wf-in">${label}<small>${esc(inp.kind)}</small>
            <select data-in="${esc(inp.id)}"><option value="default">As before: ${esc(baseName(inp.default))}</option>
              ${fileChoices(inp).map(([v, n]) => `<option value="${esc(v)}">${esc(n)}</option>`).join("")}</select></label>`;
          if (inp.type === "area") return `<label class="wf-in">${label}<small>area</small>
            <select data-in="${esc(inp.id)}"><option value="default">As before (the saved area)</option><option value="view">What the map shows now</option>
              ${polyLayers().map((l) => `<option value="layer:${esc(l.id)}">${esc(l.name)}</option>`).join("")}</select></label>`;
          return `<label class="wf-in">${label}<small>${esc(inp.kind || "value")}</small><input type="text" data-in="${esc(inp.id)}" value="${esc(Array.isArray(inp.default) ? inp.default.join(", ") : inp.default ?? "")}"></label>`;
        }).join("");
        // batch: the whole workflow once per layer (a file input) or per polygon (an area input)
        const files = wf.inputs.filter((i) => i.type === "file"), areas = wf.inputs.filter((i) => i.type === "area");
        $("#wf-batch-box").innerHTML = files.length || areas.length ? `<label class="check"><input type="checkbox" id="wf-batch"> Run it several times ${tip("Once for each layer you tick (for a file input), or once for each polygon of a layer (for an area input). Every run's results go to Contents.")}</label>
          <div id="wf-batch-opts" class="hidden">
            <label>For each <select id="wf-batch-in">${[...files, ...areas].map((i) => `<option value="${esc(i.id)}">${esc(i.label)}</option>`).join("")}</select></label>
            <div id="wf-batch-list"></div></div>` : "";
        $("#wf-batch")?.addEventListener("change", (e) => { $("#wf-batch-opts").classList.toggle("hidden", !e.target.checked); renderBatchList(); });
        $("#wf-batch-in")?.addEventListener("change", renderBatchList);
        $$("[data-label]", box).forEach((el) => el.oninput = () => { wf.inputs.find((i) => i.id === el.dataset.label).label = el.value; st.dirty = true; });
        $$("[data-in]", box).forEach((el) => { const v = was[el.dataset.in]; if (v != null && (el.tagName !== "SELECT" || [...el.options].some((o) => o.value === v))) el.value = v; });
      }
      function renderBatchList() {
        const inp = st.wf.inputs.find((i) => i.id === $("#wf-batch-in")?.value), box = $("#wf-batch-list");
        if (!inp || !box) return;
        if (inp.type === "area") {
          box.innerHTML = `<label>Polygons of <select id="wf-batch-layer">${polyLayers().map((l) => `<option value="${esc(l.id)}">${esc(l.name)} (${l.geojson.features.length})</option>`).join("") || `<option value="">No polygon layer in Contents</option>`}</select></label>`;
        } else {
          const ch = fileChoices(inp);
          box.innerHTML = ch.length ? `<div class="wf-checks">${ch.map(([v, n]) => `<label class="check"><input type="checkbox" data-batch="${esc(v)}" checked> ${esc(n)}</label>`).join("")}</div>`
            : `<p class="hint">No ${esc(inp.kind)} layer in Contents to run it on.</p>`;
        }
      }
      // ---- conditions: how they read, and their editor
      const OPS = [["<", "is below"], ["<=", "is at most"], [">", "is above"], [">=", "is at least"], ["==", "equals"], ["!=", "is not"], ["drop", "falls by at least"], ["rise", "rises by at least"]];
      const WHATS = [["field", "a value of its result"], ["mean", "its mean"], ["min", "its minimum"], ["max", "its maximum"], ["empty_pct", "its % without data"], ["rows", "its number of rows"], ["features", "its number of features"]];
      const FIELD_HINTS = { "/api/jobs": ["cloud_pct", "valid_pct", "scenes"], "/api/raster/change": ["summary.mean_change", "summary.increased_pct", "summary.changed_pct", "summary.changed_ha"],
        "/api/raster/burn": ["summary.burned_ha", "summary.burned_pct", "summary.mean_dnbr"], "/api/raster/mosaic": ["covered_pct", "area_km2"], "/api/sar/process": ["flood_ha", "mean_change_db"], "/api/sar/series": ["flood_ha", "mean_change_db"],
        "/api/vector/spatial-stats": ["I", "z", "p_permutation", "R", "p", "max_per_km2", "features"], "/api/agri/diagnose": ["counts.disease", "counts.healthy", "counts.retake"],
        "/api/assess/accuracy": ["overall_accuracy", "kappa"] };
      st.lastFields = {};
      const flatNums = (o, pre = "", out = [], depth = 0) => {   // numeric fields of a result, as paths (summary.mean_change)
        if (depth > 3 || !o || typeof o !== "object") return out;
        for (const [k, v] of Object.entries(o).slice(0, 60)) {
          const p = pre ? `${pre}.${k}` : k;
          if (typeof v === "number") out.push(p); else if (v && typeof v === "object" && !Array.isArray(v)) flatNums(v, p, out, depth + 1);
        }
        return out;
      };
      const condText = (c) => {
        const of = c.of, w = of.field ? of.field : (WHATS.find((x) => x[0] === of.stat)?.[1] || of.stat).replace(/^its /, "") + (of.band > 1 ? ` (band ${of.band})` : "");
        return `step ${of.step + 1}'s ${w} ${OPS.find((o) => o[0] === c.op)?.[1] || c.op} ${c.value}`;
      };
      const condSummary = (s) => [
        s.when?.all?.length ? `<div class="wf-cond-line when">⑂ Runs only if ${s.when.all.map((c) => esc(condText(c))).join(" and ")}; otherwise it ${s.when.otherwise === "stop" ? "stops the run" : "is skipped"}</div>` : "",
        ...(s.checks || []).map((k) => `<div class="wf-cond-line check">${k.then === "stop" ? "■ Stops" : k.then === "alert" ? "🔔 Alerts" : "⚠ Warns"} if ${esc(condText(k.if))}${k.message ? ` (“${esc(k.message)}”)` : ""}</div>`),
      ].join("");
      /** the conditions of step i, edited in box; onChange after every change (s.when / s.checks updated) */
      function condEditor(box, wf, i, onChange) {
        const s = wf.steps[i];
        const stepOpts = (max, sel) => wf.steps.slice(0, max).map((x, n) => `<option value="${n}" ${n === sel ? "selected" : ""}>${n + 1}. ${esc(x.title)}</option>`).join("");
        const row = (c, kind, k) => {
          const of = c?.of || { step: kind === "when" ? i - 1 : i, field: "" }, what = of.field != null && !of.stat ? "field" : of.stat;
          return `<div class="wf-cond" data-kind="${kind}" data-k="${k}">
            ${kind === "check" ? `<select data-c="then"><option value="warn" ${c?.then === "warn" ? "selected" : ""}>Warn</option><option value="alert" ${c?.then === "alert" ? "selected" : ""}>Alert</option><option value="stop" ${c?.then === "stop" ? "selected" : ""}>Stop the run</option></select><span>if</span>` : ""}
            <select data-c="step">${stepOpts(kind === "when" ? i : i + 1, of.step)}</select>
            <select data-c="what">${WHATS.map(([v, t]) => `<option value="${v}" ${v === what ? "selected" : ""}>${t}</option>`).join("")}</select>
            <input data-c="field" list="wf-fl-${i}-${of.step}" placeholder="e.g. cloud_pct" value="${esc(of.field || "")}" class="${what === "field" ? "" : "hidden"}">
            <input data-c="band" type="number" min="1" value="${of.band || 1}" title="Band" class="${["mean", "min", "max", "empty_pct"].includes(what) ? "" : "hidden"}" style="width:56px">
            <select data-c="op">${OPS.map(([v, t]) => `<option value="${v}" ${v === (c?.op || "<") ? "selected" : ""}>${t}</option>`).join("")}</select>
            <input data-c="value" type="number" step="any" value="${c?.value ?? ""}" placeholder="number" style="width:84px">
            ${kind === "check" ? `<input data-c="message" placeholder="message (optional)" value="${esc(c?.message || "")}">` : ""}
            <button type="button" class="np-copy" data-c="del" title="Remove">×</button></div>`;
        };
        const lists = wf.steps.slice(0, i + 1).map((x, n) => `<datalist id="wf-fl-${i}-${n}">${[...new Set([...(FIELD_HINTS[x.endpoint] || []), ...(st.lastFields[n] || [])])].map((f) => `<option value="${esc(f)}">`).join("")}</datalist>`).join("");
        box.innerHTML = `${lists}
          <div class="wf-cond-sec"><b>Run this step only if</b>${i === 0 ? `<p class="hint">The first step has no earlier step to check.</p>` : `
            ${(s.when?.all || []).map((c, k) => row(c, "when", k)).join("")}
            <div class="row tight" style="gap:6px;align-items:center"><button type="button" class="btn small ghost" data-add="when">+ Condition</button>
              ${s.when?.all?.length ? `<label class="inline">otherwise <select data-otherwise><option value="skip">skip it (and the steps that need it)</option><option value="stop" ${s.when.otherwise === "stop" ? "selected" : ""}>stop the run</option></select></label>` : ""}</div>`}</div>
          <div class="wf-cond-sec"><b>After it runs</b> <span class="hint">warn, alert or stop when a value crosses a limit or changes since the last run</span>
            ${(s.checks || []).map((c, k) => row(c.if ? { ...c.if, then: c.then, message: c.message } : null, "check", k)).join("")}
            <button type="button" class="btn small ghost" data-add="check">+ Check</button></div>`;
        const read = () => {
          const conds = (kind) => $$(`.wf-cond[data-kind="${kind}"]`, box).map((el) => {
            const g = (k) => $(`[data-c="${k}"]`, el)?.value;
            const what = g("what"), of = { step: +g("step") };
            if (what === "field") of.field = g("field").trim(); else { of.stat = what; of.band = +g("band") || 1; }
            const c = { of, op: g("op"), value: g("value") === "" ? null : +g("value") };
            return kind === "check" ? { if: c, then: g("then"), message: g("message")?.trim() || "" } : c;
          });
          const w = conds("when"), ch = conds("check");
          if (w.length) s.when = { all: w, otherwise: $("[data-otherwise]", box)?.value || "skip" }; else delete s.when;
          if (ch.length) s.checks = ch; else delete s.checks;
          onChange();
        };
        $$("input, select", box).forEach((el) => el.addEventListener("change", () => {
          const r = el.closest(".wf-cond");
          if (r && el.dataset.c === "what") {
            $('[data-c="field"]', r).classList.toggle("hidden", el.value !== "field");
            $('[data-c="band"]', r).classList.toggle("hidden", !["mean", "min", "max", "empty_pct"].includes(el.value));
          }
          if (r && el.dataset.c === "step") $('[data-c="field"]', r).setAttribute("list", `wf-fl-${i}-${el.value}`);
          read();
        }));
        $$('[data-c="del"]', box).forEach((b) => b.onclick = () => { b.closest(".wf-cond").remove(); read(); condEditor(box, wf, i, onChange); });
        $$("[data-add]", box).forEach((b) => b.onclick = () => {
          const blank = { of: { step: b.dataset.add === "when" ? i - 1 : i, field: wf.steps[b.dataset.add === "when" ? i - 1 : i].endpoint === "/api/jobs" ? "cloud_pct" : "" }, op: "<", value: null };
          if (b.dataset.add === "when") s.when = { all: [...(s.when?.all || []), blank], otherwise: s.when?.otherwise || "skip" };
          else s.checks = [...(s.checks || []), { if: { ...blank, of: { step: i, stat: "mean", band: 1 } }, then: "warn", message: "" }];
          onChange(); condEditor(box, wf, i, onChange);
        });
      }
      // steps re-numbered: every reference to step `from` becomes `to` (settings and conditions)
      function renumber(wf, map) {
        wf.steps.forEach((s) => {
          s.body = JSON.parse(JSON.stringify(s.body).replace(/"\$step":(\d+)/g, (m, n) => `"$step":${map(+n)}`));
          (s.when?.all || []).forEach((c) => { c.of.step = map(c.of.step); });
          (s.checks || []).forEach((c) => { c.if.of.step = map(c.if.of.step); });
        });
      }
      const condRefs = (s) => [...(s.when?.all || []).map((c) => c.of.step), ...(s.checks || []).map((c) => c.if.of.step)];
      function removeStep(wf, i) {
        const later = wf.steps.slice(i + 1);
        if (JSON.stringify(later.map((s) => s.body)).includes(`"$step":${i}`) || later.some((s) => condRefs(s).includes(i))) {
          toast("A later step uses this step's result or checks it → change that one first", true); return false;
        }
        wf.steps.splice(i, 1);
        (wf.steps.slice(0, i)).forEach((s) => { s.checks = (s.checks || []).filter((c) => c.if.of.step !== i); if (!s.checks.length) delete s.checks; });
        renumber(wf, (n) => (n > i ? n - 1 : n));
        return true;
      }
      /** step i moved one place (dir −1 / +1), if no step would then use a later one */
      function moveStep(wf, i, dir) {
        const j = i + dir;
        if (j < 0 || j >= wf.steps.length) return false;
        const a = Math.min(i, j), b = a + 1;   // swap a and b: b may not use a
        const usesA = JSON.stringify(wf.steps[b].body).includes(`"$step":${a}`) || condRefs(wf.steps[b]).filter((n) => n === a).length;
        if (usesA) { toast(`Step ${b + 1} uses step ${a + 1}'s result: it can't come before it`, true); return false; }
        [wf.steps[a], wf.steps[b]] = [wf.steps[b], wf.steps[a]];
        renumber(wf, (n) => (n === a ? b : n === b ? a : n));
        return true;
      }
      function renderSteps(state = {}) {
        const wf = st.wf;
        $("#wf-steps").innerHTML = wf.steps.map((s, i) => {
          const uses = [...JSON.stringify(s.body).matchAll(/"\$step":(\d+)/g)].map((m) => +m[1] + 1), ins = [...JSON.stringify(s.body).matchAll(/"\$in":"(\w+)"/g)].map((m) => wf.inputs.find((x) => x.id === m[1])?.label).filter(Boolean);
          const mark = state[i] || "";
          return `<li class="wf-step ${mark}"><div class="row between" style="gap:6px"><b>${st.editing ? `<input type="text" data-stitle="${i}" value="${esc(s.title)}" maxlength="200">` : esc(s.title)}</b>
              <span class="wf-mark">${{ done: "✓", run: '<span class="spinner"></span>', fail: "✗", skip: "⤼", warn: "⚠", bad: "⛔" }[mark] || ""}</span></div>
            <small>${[...new Set(ins)].map((x) => `uses ${esc(x)}`).concat([...new Set(uses)].map((n) => `uses step ${n}'s result`)).join(" · ") || "no inputs"}</small>
            ${condSummary(s)}
            ${st.editing ? `<div class="row tight" style="gap:4px;margin-top:4px"><button type="button" class="btn small ghost" data-sjson="${i}">Settings…</button><button type="button" class="btn small ghost" data-scond="${i}">Conditions…</button>${wf.steps.length > 1 ? `<button type="button" class="btn small ghost" data-sdel="${i}">Remove</button>` : ""}</div>
              <textarea class="wf-json hidden" data-sbody="${i}" spellcheck="false"></textarea><div class="wf-cond-box hidden" data-scondbox="${i}"></div>` : ""}</li>`;
        }).join("");
        $$("[data-stitle]").forEach((el) => el.oninput = () => { wf.steps[+el.dataset.stitle].title = el.value; st.dirty = true; });
        $$("[data-sdel]").forEach((b) => b.onclick = () => { if (removeStep(wf, +b.dataset.sdel)) { st.dirty = true; renderSteps(); renderCautions(); diagram.refresh(); } });
        $$("[data-scond]").forEach((b) => b.onclick = () => {
          const box = $(`[data-scondbox="${b.dataset.scond}"]`);
          box.classList.toggle("hidden");
          if (!box.classList.contains("hidden")) condEditor(box, wf, +b.dataset.scond, () => { st.dirty = true; renderCautions(); diagram.refresh(); });
        });
        $$("[data-sjson]").forEach((b) => b.onclick = () => {
          const ta = $(`[data-sbody="${b.dataset.sjson}"]`);
          ta.classList.toggle("hidden");
          if (!ta.classList.contains("hidden")) ta.value = JSON.stringify(wf.steps[+b.dataset.sjson].body, null, 2);
          ta.oninput = () => { try { wf.steps[+b.dataset.sjson].body = JSON.parse(ta.value); ta.classList.remove("bad"); st.dirty = true; } catch { ta.classList.add("bad"); } };
        });
      }

      // ---- running: the inputs' values, the markers replaced, each step through its tool
      function areaOf(l) {
        const polys = l.geojson.features.map((f) => f.geometry).filter((g) => g && /Polygon/.test(g.type));
        return polys.length === 1 ? polys[0] : { type: "MultiPolygon", coordinates: polys.flatMap((g) => g.type === "Polygon" ? [g.coordinates] : g.coordinates) };
      }
      function inputValue(inp, chosen) {
        if (inp.type === "file") {
          if (!chosen || chosen === "default") return inp.default;
          if (chosen.startsWith("item:")) return chosen.slice(5);
          return getLayer(chosen.slice(6))?.path || inp.default;
        }
        if (inp.type === "area") {
          if (chosen === "view") { const b = map.getBounds(); return { type: "Polygon", coordinates: [[[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()], [b.getEast(), b.getNorth()], [b.getWest(), b.getNorth()], [b.getWest(), b.getSouth()]]] }; }
          if (chosen?.startsWith("layer:")) { const l = getLayer(chosen.slice(6)); if (l) return areaOf(l); }
          if (chosen?.type) return chosen;   // a geometry (a batch run's polygon)
          return inp.default;
        }
        const v = String(chosen ?? "").trim();
        if (Array.isArray(inp.default)) return v.split(/[,\s]+/).filter(Boolean).map((x) => (Number.isFinite(+x) ? +x : x));
        return typeof inp.default === "number" && Number.isFinite(+v) ? +v : v;
      }
      const extOf = (p) => (String(p).match(/\.[A-Za-z0-9]+$/) || [""])[0].toLowerCase();
      function resolve(node, ctx) {
        if (Array.isArray(node)) return node.map((x) => resolve(x, ctx));
        if (node && typeof node === "object") {
          if ("$in" in node) return ctx.inputs[node.$in];
          if ("$step" in node) {
            const same = (ctx.outputs[node.$step] || []).filter((p) => extOf(p) === node.ext), v = same[node.nth] ?? same[0];
            if (!v) throw new Error(`Step ${node.$step + 1} made no ${node.ext || ""} file for the next step → check its settings`);
            return v;
          }
          return Object.fromEntries(Object.entries(node).map(([k, v]) => [k, resolve(v, ctx)]));
        }
        return node;
      }
      // a finished step's files, as the History has them (the same order the workflow was made from), workspace-relative
      async function stepOutputs(jobId) {
        for (let i = 0; i < 6; i++) {
          try {
            const e = await api(`/api/history/${encodeURIComponent(jobId)}`);
            if (e.status === "done") {
              const root = String(e.workspace || "").replace(/[\\/]+$/, "");
              return (e.outputs || []).map((p) => root && p.startsWith(root + "/") ? p.slice(root.length + 1) : root && p.startsWith(root + "\\") ? p.slice(root.length + 1).replace(/\\/g, "/") : p);
            }
          } catch {}
          await new Promise((r) => setTimeout(r, 400));
        }
        return [];
      }
      // one step: its settings with the inputs and earlier results filled in, run through its tool; the files it made
      async function runStep(s, ctx, i, total, title, tool = "workflows") {
        const body = resolve(s.body, ctx);
        const res = await api(s.endpoint, { method: "POST", json: body });
        if (res?.id && res.status) {   // a background job: follow it (progress bar, Cancel, History)
          const done = await trackJob(res, { tool, title: `${title} · ${i + 1}/${total} ${s.title}` });
          return { step: i, jobId: done.id, outs: await stepOutputs(done.id), body, result: done.result };
        }
        return { step: i, body, result: res, outs: ["path", "csv", "output_table", "geojson_path", "outputs"].flatMap((k) => [].concat(res?.[k] || [])).filter((x) => typeof x === "string") };
      }
      // ---- conditions: "run only if" before a step; the built-in checks and the step's own checks after it
      const usesSteps = (s) => [...new Set([...JSON.stringify(s.body).matchAll(/"\$step":(\d+)/g)].map((m) => +m[1]))];
      /** may step i run? {run, stop, notes: [texts]} from its "when" conditions */
      async function gate(s, i, done, wid) {
        if (!s.when?.all?.length) return { run: true, notes: [] };
        const r = (await api("/api/workflows/evaluate", { method: "POST", json: { conditions: s.when.all, steps: done, wid, step: i } })).results;
        const ok = r.every((x) => x.ok === true);
        return { run: ok, stop: !ok && s.when.otherwise === "stop", notes: r.map((x) => `${x.ok === true ? "✓" : x.ok === false ? "✗" : "?"} ${x.text}`), values: r };
      }
      /** after step i ran: what looks wrong in its files (critical / warnings) and its checks (warn / alert / stop) */
      async function afterStep(s, i, done, wid, r) {
        const out = { critical: [], warnings: [], alerts: [], stop: null, values: [], files: [] };
        try {
          const o = await api("/api/workflows/observe", { method: "POST", json: { paths: r.outs.slice(0, 20), result: r.result && typeof r.result === "object" ? r.result : null } });
          out.critical = o.critical; out.warnings = o.warnings; out.files = o.files;
        } catch {}
        if (s.checks?.length) {
          const ev = (await api("/api/workflows/evaluate", { method: "POST", json: { conditions: s.checks.map((c) => c.if), steps: done, wid, step: i, own: true } })).results;
          out.values = ev;
          ev.forEach((x, k) => {
            const c = s.checks[k];
            if (x.ok !== true) return;
            const text = `${c.message ? `${c.message}: ` : ""}${x.text}`;
            if (c.then === "stop") out.stop = text;
            else if (c.then === "alert") out.alerts.push(text);
            else out.warnings.push(text);
          });
        }
        return out;
      }
      async function runOnce(chosen, label, wf = st.wf, wid = wf === st.wf ? st.id : null) {
        const ctx = { inputs: {}, outputs: [] }, marks = {}, shown = () => wf === st.wf;
        const renderSteps_ = (m) => { if (shown()) { renderSteps(m); diagram.refresh(m); } };
        wf.inputs.forEach((inp) => { ctx.inputs[inp.id] = inputValue(inp, chosen[inp.id]); });
        const results = [], notes = [], done = {}, skipped = new Set(), values = {};
        const note = (i, kind, text) => { notes.push({ step: i, kind, text }); };
        results.notes = notes; results.values = values; results.marks = marks;
        for (let i = 0; i < wf.steps.length; i++) {
          const s = wf.steps[i];
          // a step whose input comes from a skipped step is skipped too
          const from = usesSteps(s).find((n) => skipped.has(n));
          if (from != null) { skipped.add(i); marks[i] = "skip"; note(i, "skip", `skipped: it uses step ${from + 1}'s result, which was skipped`); renderSteps_(marks); continue; }
          const g = await gate(s, i, done, wid).catch((e) => ({ run: false, stop: true, notes: [`couldn't check its conditions: ${e.message}`] }));
          (g.values || []).forEach((x) => { if (x.value != null) values[x.key] = x.value; });
          if (!g.run) {
            marks[i] = "skip"; renderSteps_(marks);
            if (g.stop) { note(i, "stop", `stopped here: ${g.notes.join("; ")}`); results.stopped = `Step ${i + 1} (${s.title}): its condition isn't met (${g.notes.join("; ")})`; break; }
            skipped.add(i); note(i, "skip", `skipped: ${g.notes.join("; ")}`);
            continue;
          }
          marks[i] = "run"; renderSteps_(marks);
          status(`${label}Step ${i + 1} of ${wf.steps.length}: ${s.title}`, true);
          let r;
          try {
            r = await runStep(s, ctx, i, wf.steps.length, `${label}${wf.name}`);
          } catch (e) {
            marks[i] = "fail"; renderSteps_(marks);
            if (e?.cancelled) throw e;
            throw Object.assign(new Error(`Step ${i + 1} (${s.title}) failed: ${e.message}`), { detail: e.detail, notes });
          }
          results.push(r);
          ctx.outputs[i] = r.outs;
          done[i] = { result: r.result && typeof r.result === "object" ? r.result : {}, outs: r.outs };
          if (shown()) st.lastFields[i] = flatNums(done[i].result);   // offered when writing conditions
          const a = await afterStep(s, i, done, wid, r);
          a.values.forEach((x) => { if (x.value != null) values[x.key] = x.value; });
          a.critical.forEach((t) => note(i, "critical", t));
          a.warnings.forEach((t) => note(i, "warn", t));
          a.alerts.forEach((t) => note(i, "alert", t));
          marks[i] = a.critical.length || a.alerts.length ? "bad" : a.warnings.length ? "warn" : "done";
          renderSteps_(marks);
          if (a.stop) { note(i, "stop", `stopped by its check: ${a.stop}`); results.stopped = `Step ${i + 1} (${s.title}): ${a.stop}`; break; }
          if (a.critical.length && wf.stop_on_critical !== false && i < wf.steps.length - 1) {
            results.stopped = `Step ${i + 1} (${s.title}) made something that can't be right: ${a.critical.join("; ")}`;
            note(i, "stop", "stopped: the next steps would work on a wrong result (untick “Stop when a result can't be right” to go on)");
            break;
          }
        }
        if (wid && Object.keys(values).length) api(`/api/workflows/${encodeURIComponent(wid)}/values`, { method: "POST", json: { values } }).catch(() => {});
        return results;
      }
      const NOTE_IC = { skip: "⤼", warn: "⚠", critical: "⛔", alert: "🔔", stop: "■" };
      const notesHtml = (notes, wf) => notes.length ? `<ul class="wf-notes">${notes.map((n) => `<li class="wf-note ${n.kind}"><span>${NOTE_IC[n.kind] || "•"}</span>
          <b>Step ${n.step + 1}</b> ${esc(wf.steps[n.step]?.title || "")}: ${esc(n.text)}</li>`).join("")}</ul>` : "";
      // the results into Contents: rasters, tables, the photos' points of a step
      async function addResults(results, all) {
        const keep = all ? results : results.slice(-1);
        let added = 0;
        for (const r of keep) for (const p of r.outs) {
          const name = baseName(p);
          try {
            if (/\.(tiff?)$/i.test(p) && !/_colour\.tif$/i.test(p)) { await addRasterFromPath(p, { name: name.replace(/\.tiff?$/i, ""), zoom: added === 0 }); added++; }
            else if (/^tables\/.+\.(csv|parquet)$/i.test(p)) { addItem({ kind: "table", name, path: p }); added++; }
            else if (/\.geojson$/i.test(p)) {   // a vector result (a tool's file in analysis/, or a download)
              let fc = r.jobId && p.startsWith(`downloads/${r.jobId}/`) ? await api(`/api/jobs/${r.jobId}/files/${encodeURIComponent(name)}`) : await api(`/api/vector/read?path=${encodeURIComponent(p)}`);
              if (typeof fc === "string") fc = JSON.parse(fc);
              if (fc?.features?.length) { addVectorLayer(fc, name.replace(/\.geojson$/i, ""), { path: p }); added++; }
            }
          } catch {}
        }
        return added;
      }
      async function run() {
        if (st.running) return;
        const wf = st.wf, chosen = {};
        $$("#wf-inputs [data-in]").forEach((el) => { chosen[el.dataset.in] = el.value; });
        // the runs: one, or one per ticked layer / per polygon
        let runs = [[chosen, ""]];
        if ($("#wf-batch")?.checked) {
          const bid = $("#wf-batch-in").value, inp = wf.inputs.find((i) => i.id === bid);
          if (inp.type === "area") {
            const l = getLayer($("#wf-batch-layer")?.value);
            if (!l) return toast("Choose a polygon layer for the batch", true);
            const fs = l.geojson.features.filter((f) => /Polygon/.test(f.geometry?.type));
            runs = fs.map((f, k) => [{ ...chosen, [bid]: f.geometry }, `${f.properties?.name || f.properties?.NAME || `${l.name} ${k + 1}`} · `]);
          } else {
            const ticked = $$("#wf-batch-list [data-batch]:checked").map((c) => c.dataset.batch);
            if (!ticked.length) return toast("Tick at least one layer for the batch", true);
            runs = ticked.map((v) => [{ ...chosen, [bid]: v }, `${(v.startsWith("layer:") ? getLayer(v.slice(6))?.name : baseName(v.slice(5))) || ""} · `]);
          }
          if (runs.length > 1 && !confirm(`Run “${wf.name}” ${runs.length} times (${wf.steps.length} step${wf.steps.length === 1 ? "" : "s"} each)?`)) return;
        }
        // vector layers that aren't files yet are saved first (tools read layers from files)
        for (const r of runs) for (const [k, v] of Object.entries(r[0])) {
          const l = typeof v === "string" && v.startsWith("layer:") ? getLayer(v.slice(6)) : null;
          if (l?.type === "vector" && !l.path) {
            try { r[0][k] = `item:${(await api("/api/vector/save", { method: "POST", json: { layer_id: l.id, geojson: l.geojson } })).path}`; }
            catch (e) { return toast(e, true); }
          }
        }
        st.running = true;
        $("#wf-run").disabled = true;
        const t0 = Date.now(), box = $("#wf-result");
        let ok = 0, failed = [], added = 0;
        try {
          const allNotes = [];
          let stopped = 0, alerts = 0;
          for (let k = 0; k < runs.length; k++) {
            const [ch, label] = runs[k];
            try {
              const results = await runOnce(ch, runs.length > 1 ? `${k + 1}/${runs.length} ${label}` : "");
              added += await addResults(results, $("#wf-all-results").checked);
              results.notes.forEach((n) => allNotes.push({ ...n, run: label }));
              alerts += results.notes.filter((n) => n.kind === "alert").length;
              if (results.stopped) { stopped++; failed.push([label, new Error(results.stopped)]); } else ok++;
            } catch (e) {
              if (e?.cancelled) { notCancelled(e); break; }
              (e.notes || []).forEach((n) => allNotes.push({ ...n, run: label }));
              failed.push([label, e]);
              if (runs.length === 1) throw e;
            }
          }
          const secs = Math.round((Date.now() - t0) / 1000), bad = allNotes.filter((n) => ["critical", "alert", "stop"].includes(n.kind)).length;
          box.innerHTML = `<h2>${failed.length ? "Finished with problems" : bad ? "Finished: check the warnings" : "Finished"}</h2>
            <p>${ok} run${ok === 1 ? "" : "s"} of “${esc(wf.name)}” in ${secs < 90 ? `${secs} s` : `${Math.round(secs / 60)} min`}; ${added} result${added === 1 ? "" : "s"} added to Contents.</p>
            ${failed.length ? `<p class="err-text">${failed.length} ${stopped ? "stopped" : "failed"}:</p><ul>${failed.map(([l, e]) => `<li>${esc(l)}${esc(e.message)}</li>`).join("")}</ul>` : ""}
            ${notesHtml(allNotes.map((n) => ({ ...n, text: `${n.run || ""}${n.text}` })), wf)}
            <p class="hint">Each step is in the History, with its settings and files.</p>`;
          box.classList.remove("hidden");
          toast(failed.length ? `Workflow finished with ${failed.length} problem(s)` : alerts ? `🔔 “${wf.name}”: ${alerts} alert(s)` : bad ? `Workflow “${wf.name}” finished with warnings` : `Workflow “${wf.name}” finished`, !!(failed.length || bad));
        } catch (e) {
          box.innerHTML = `<h2>Stopped</h2><p class="err-text">${esc(e.message)}</p>${notesHtml(e.notes || [], wf)}`;
          box.classList.remove("hidden");
          toast(e, true);
        } finally {
          st.running = false;
          $("#wf-run").disabled = false;
          status("Ready");
        }
      }

      // ---- schedules: the card, and the scheduler that runs due workflows while the app is open
      const isDate = (v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);
      const dayStr = (d) => new Date(Date.now() - d * 864e5).toISOString().slice(0, 10);
      const when = (t) => new Date(t * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      function schedUi() {
        const every = $("#wfs-every").value;
        $("#wfs-opts").classList.toggle("hidden", !$("#wfs-on").checked);
        $("#wfs-at-row").classList.toggle("hidden", every === "hours");
        $("#wfs-hours-row").classList.toggle("hidden", every !== "hours");
        $("#wfs-wd-row").classList.toggle("hidden", every !== "week");
      }
      ["#wfs-on", "#wfs-every"].forEach((s) => $(s).addEventListener("change", schedUi));
      async function renderSchedule() {
        $("#wf-sched-card").classList.toggle("hidden", !st.id);
        if (!st.id) return;
        const dates = st.wf.inputs.filter((i) => i.type === "value" && isDate(i.default));
        let s = null;
        try { s = (await api("/api/workflows/schedules")).schedules.find((x) => x.id === st.id) || null; } catch {}
        $("#wfs-on").checked = !!s?.enabled;
        $("#wfs-every").value = s?.every || "day"; $("#wfs-at").value = s?.at || "07:00"; $("#wfs-hours").value = s?.hours || 6; $("#wfs-wd").value = s?.weekday ?? 0;
        $("#wfs-notify").value = s?.notify === "fail" ? "fail" : "always"; $("#wfs-add").checked = s?.add_results ?? true;
        $("#wfs-af").value = s?.alert?.field || ""; $("#wfs-aop").value = s?.alert?.op || "<"; $("#wfs-av").value = s?.alert?.value ?? "";
        $("#wfs-dates").innerHTML = dates.length ? `<p class="hint" style="margin:6px 0 2px">Dates that move with the run day (empty = keep as set above):</p>` + dates.map((i) =>
          `<label>${esc(i.label)} <span class="row tight" style="gap:6px;align-items:center"><input type="number" min="0" max="3650" data-rel="${esc(i.id)}" value="${s?.relative_dates?.[i.id] ?? ""}" style="width:90px"> days before the run</span></label>`).join("") : "";
        const last = s?.runs?.[0];
        $("#wfs-status").innerHTML = s ? (s.enabled ? `Next run: <b>${esc(when(s.next_run))}</b>` : "Paused") + (last ? ` · last ${esc(when(last.t))}: ${last.ok ? (last.alert ? "⚠ alert" : "✓ done") : "✗ failed"}${last.message ? ` (${esc(last.message.slice(0, 120))})` : ""}` : "") : "Not scheduled.";
        schedUi();
      }
      $("#wfs-save").onclick = async () => {
        const chosen = {};
        for (const el of $$("#wf-inputs [data-in]")) {   // layers become their files, so the run doesn't depend on this session's Contents
          let v = el.value;
          if (v.startsWith("layer:")) {
            const l = getLayer(v.slice(6));
            if (l?.path) v = `item:${l.path}`;
            else if (l?.type === "vector") v = `item:${(await api("/api/vector/save", { method: "POST", json: { layer_id: l.id, geojson: l.geojson } })).path}`;
          }
          if (v === "view") { const b = map.getBounds(); v = JSON.stringify({ type: "Polygon", coordinates: [[[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()], [b.getEast(), b.getNorth()], [b.getWest(), b.getNorth()], [b.getWest(), b.getSouth()]]] }); }
          chosen[el.dataset.in] = v;
        }
        const rel = {};
        $$("#wfs-dates [data-rel]").forEach((el) => { if (el.value !== "") rel[el.dataset.rel] = +el.value; });
        const af = $("#wfs-af").value.trim(), av = $("#wfs-av").value;
        const schedule = { enabled: $("#wfs-on").checked, every: $("#wfs-every").value, at: $("#wfs-at").value, hours: +$("#wfs-hours").value, weekday: +$("#wfs-wd").value,
          notify: $("#wfs-notify").value, add_results: $("#wfs-add").checked, relative_dates: rel, inputs: chosen,
          alert: af && av !== "" ? { field: af, op: $("#wfs-aop").value, value: +av } : null };
        try { await api(`/api/workflows/${encodeURIComponent(st.id)}/schedule`, { method: "PUT", json: { schedule } }); toast(schedule.enabled ? "Schedule saved" : "Schedule paused"); renderSchedule(); }
        catch (e) { toast(e, true); }
      };
      $("#wfs-on").addEventListener("change", () => { if (!$("#wfs-on").checked && st.id) $("#wfs-save").click(); });

      const valueAt = (obj, path) => path.replace(/\[(\d+)\]/g, ".$1").split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
      const sched = { busy: false };
      async function runDue() {
        if (sched.busy || st.running) return;
        let list;
        try { list = (await api("/api/workflows/schedules")).schedules.filter((s) => s.due); } catch { return; }
        for (const s of list) {
          sched.busy = true; st.running = true;
          let ok = false, msg = "", alert = false;
          try {
            const wf = await api(`/api/workflows/${encodeURIComponent(s.id)}`), chosen = { ...s.inputs };
            for (const [id, v] of Object.entries(chosen)) if (typeof v === "string" && v.startsWith("{")) { try { chosen[id] = JSON.parse(v); } catch {} }
            for (const [id, d] of Object.entries(s.relative_dates || {})) chosen[id] = dayStr(d);
            toast(`Scheduled run: “${wf.name}” started`);
            const results = await runOnce(chosen, "Scheduled · ", wf, s.id);
            ok = !results.stopped;
            const added = s.add_results ? await addResults(results, false) : 0;
            const last = results[results.length - 1]?.result;
            const flagged = results.notes.filter((n) => ["alert", "critical", "stop"].includes(n.kind));
            if (flagged.length) { alert = true; msg = flagged.map((n) => `step ${n.step + 1}: ${n.text}`).join("; "); }
            if (s.alert) {
              const v = Number(valueAt(last || {}, s.alert.field));
              const hit = Number.isFinite(v) && { "<": v < s.alert.value, ">": v > s.alert.value, "<=": v <= s.alert.value, ">=": v >= s.alert.value }[s.alert.op];
              if (hit) { alert = true; msg = [msg, `${s.alert.field} = ${fmtNum(v)} (${s.alert.op} ${s.alert.value})`].filter(Boolean).join("; "); }
              else if (!msg) msg = Number.isFinite(v) ? `${s.alert.field} = ${fmtNum(v)}` : `${s.alert.field} not in the result`;
            }
            msg = msg || `${results.length} step${results.length === 1 ? "" : "s"}${added ? `, ${added} result${added === 1 ? "" : "s"} added to Contents` : ""}`;
            if (alert) toast(`⚠ Alert from “${wf.name}”: ${msg}`, true);
            else if (s.notify === "always") toast(`Scheduled run of “${wf.name}” finished: ${msg}`);
          } catch (e) {
            msg = e?.message || String(e);
            toast(`Scheduled run of “${s.name}” failed: ${msg}`, true);
          } finally {
            await api(`/api/workflows/${encodeURIComponent(s.id)}/schedule/ran`, { method: "POST", json: { ok, message: msg, alert } }).catch(() => {});
            sched.busy = false; st.running = false; status("Ready");
            if (st.id === s.id) renderSchedule();
          }
        }
      }
      const fmtNum = (v) => Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(3);
      setTimeout(runDue, 20000);           // missed runs: soon after the app opens
      setInterval(runDue, 60000);          // then every minute
      LF.wfSchedule = { runDue };

      // ---- making, saving, sharing
      async function fromHistory(ids) {
        try { openUnsaved(await api("/api/workflows/from-history", { method: "POST", json: { job_ids: ids } }), "Name the workflow, check its inputs and steps, then save it"); }
        catch (e) { toast(e, true); }
      }
      async function pickRuns() {
        let h;
        try { h = await api("/api/history?limit=60&status=done"); } catch (e) { return toast(e, true); }
        const d = $("#dlg-wf-pick");
        $("#wfp-list").innerHTML = h.rows.length ? h.rows.map((r) => `<label class="wfp-row"><input type="checkbox" value="${esc(r.id)}"><span><b>${esc(r.title)}</b><small>${esc(new Date(r.started * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }))}${r.outputs ? ` · ${r.outputs} file${r.outputs === 1 ? "" : "s"}` : ""}</small></span></label>`).join("")
          : `<p class="hint">No finished runs in the History yet: run the tools once, then make the workflow from those runs.</p>`;
        d.showModal();
        $("#wfp-make").onclick = () => {
          const ids = $$("#wfp-list input:checked").map((c) => c.value);
          if (!ids.length) return toast("Tick the runs that make the workflow", true);
          d.close();
          fromHistory(ids);
        };
      }
      async function save({ quiet = false } = {}) {
        const wf = { ...st.wf, name: $("#wf-ename").value.trim() || st.wf.name, description: $("#wf-edesc").value.trim() };
        if ($$("#wf-steps textarea.bad").length) return toast("A step's settings aren't valid JSON → fix or close them first", true);
        try {
          const r = await api(`/api/workflows/${st.id ? encodeURIComponent(st.id) : "new"}`, { method: "PUT", json: { workflow: wf } });
          st.id = r.id; st.wf = r; st.editing = false; st.dirty = false;
          await loadList(r.id);
          renderDetail();
          if (!quiet) toast(`Workflow “${r.name}” saved`);
        } catch (e) { toast(e, true); }
      }
      $("#wf-new").onclick = pickRuns;
      $("#wf-edit").onclick = () => { st.editing = true; renderDetail(); };
      $("#wf-cancel-edit").onclick = () => openWorkflow(st.id);
      $("#wf-save").onclick = () => save();
      $("#wf-diagram").onclick = () => diagram.open();
      $("#wf-run").onclick = run;
      $("#wf-del").onclick = async () => {
        if (!st.id || !confirm(`Delete the workflow “${st.wf.name}”? (Its runs stay in the History.)`)) return;
        try { await api(`/api/workflows/${encodeURIComponent(st.id)}`, { method: "DELETE" }); } catch (e) { return toast(e, true); }
        st.id = null; st.wf = null; $("#wf-detail").classList.add("hidden"); loadList();
      };
      $("#wf-export").onclick = () => {
        const { id, created, updated, ...wf } = st.wf;
        const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([JSON.stringify(wf, null, 1)], { type: "application/json" })),
          download: `${(wf.name || "workflow").replace(/[<>:"/\\|?*]+/g, "_")}.workflow.json` });
        document.body.append(a); a.click(); a.remove();
      };
      $("#wf-import").onclick = () => $("#wf-file").click();
      $("#wf-file").onchange = async (e) => {
        const f = e.target.files[0];
        e.target.value = "";
        if (!f) return;
        try {
          const wf = JSON.parse(await f.text());
          if (!Array.isArray(wf.steps)) throw new Error("This file isn't a workflow");
          openUnsaved({ name: wf.name || f.name, description: wf.description || "", inputs: wf.inputs || [], steps: wf.steps }, "Imported: check its inputs (files are looked for in this workspace), then save it");
        } catch (x) { toast(`Couldn't import it: ${x.message}`, true); }
      };
      // ---- the diagram: inputs and steps as boxes; arrows where data flows (solid) and where a condition reads (dashed)
      function makeDiagram() {
        const dlg = document.createElement("dialog");
        dlg.className = "modal wide wfd-modal";
        dlg.innerHTML = `<div class="modal-head"><h3 id="wfd-title">Workflow diagram</h3><div class="row tight" style="gap:6px;align-items:center">
            <button class="btn small ghost" id="wfd-auto" title="Put the boxes back in columns, in the order the data flows">Arrange</button>
            <button class="btn small primary" id="wfd-save">Save</button><button class="x" data-close aria-label="Close">×</button></div></div>
          <div class="wfd-body"><div class="wfd-canvas" id="wfd-canvas"><svg id="wfd-svg" xmlns="http://www.w3.org/2000/svg"></svg></div><aside class="wfd-side" id="wfd-side"></aside></div>
          <div class="modal-foot" style="justify-content:flex-start"><span class="hint" style="margin:0">Solid arrows: data going into a step · dashed: a condition reads that step · drag a box to move it · click it to edit</span></div>`;
        document.body.appendChild(dlg);
        $$("[data-close]", dlg).forEach((b) => b.onclick = () => dlg.close());
        const W = 210, H = 66, GX = 28, GY = 58;   // boxes flow top to bottom: inputs first, then each step below what it needs
        let sel = null, marks = {}, drag = null, scale = 1;
        const short = (t, n) => (t.length > n ? t.slice(0, n - 1) + "…" : t);
        const toolName = (ep) => ep.replace(/^\/api\//, "").replace(/[/-]/g, " ");   // e.g. "raster burn"
        function auto(wf) {
          const pos = {}, depth = {}, cols = {}, y0 = wf.inputs.length ? 1 : 0;
          wf.inputs.forEach((inp, k) => { pos[`in:${inp.id}`] = [20 + k * (W + GX), 20]; });
          wf.steps.forEach((s, i) => {
            const d = Math.max(0, ...[...usesSteps(s), ...condRefs(s).filter((n) => n < i)].map((n) => (depth[n] ?? 0) + 1));
            depth[i] = d; cols[d] = cols[d] ?? 0;
            pos[`s:${i}`] = [20 + cols[d]++ * (W + GX), 20 + (d + y0) * (H + GY)];
          });
          return pos;
        }
        const posOf = () => ({ ...auto(st.wf), ...(st.wf.layout || {}) });
        // the markers in a step's settings ({"$in"} / {"$step"}) and where they are
        function markers(node, path = [], out = []) {
          if (Array.isArray(node)) node.forEach((v, k) => markers(v, [...path, k], out));
          else if (node && typeof node === "object") {
            if ("$in" in node || "$step" in node) out.push({ path, ref: node });
            else Object.entries(node).forEach(([k, v]) => markers(v, [...path, k], out));
          }
          return out;
        }
        const setAt = (obj, path, v) => { let o = obj; path.slice(0, -1).forEach((k) => { o = o[k]; }); o[path[path.length - 1]] = v; };
        function render() {
          const wf = st.wf;
          if (!wf) return;
          $("#wfd-title").textContent = `Diagram · ${wf.name}`;
          const pos = posOf(), svg = $("#wfd-svg");
          const box = (k) => pos[k] || [20, 20];
          const edge = (a, b, cls, label) => {
            // from the bottom of the source to the top of the target (a condition's arrow enters a little to the right)
            const [x1, y1] = box(a), [x2, y2] = box(b), c = cls.includes("cond");
            const sx = x1 + W / 2 + (c ? 30 : 0), sy = y1 + H, ex = x2 + W / 2 + (c ? 30 : 0), ey = y2, my = (sy + ey) / 2;
            const up = ey < sy + 10;   // a box placed above its source: loop around
            const d = up ? `M${sx},${sy} C${sx},${sy + 60} ${ex},${ey - 60} ${ex},${ey}` : `M${sx},${sy} C${sx},${my} ${ex},${my} ${ex},${ey}`;
            return `<path class="wfd-edge ${cls}" d="${d}" marker-end="url(#wfd-arrow${c ? "-c" : ""})"/>` +
              (label ? `<text class="wfd-elabel ${cls}" x="${(sx + ex) / 2 + (c ? 8 : 6)}" y="${my + 4}" text-anchor="start">${esc(short(label, 40))}</text>` : "");
          };
          let edges = "";
          wf.steps.forEach((s, i) => {
            const ms = markers(s.body);
            [...new Set(ms.filter((m) => "$in" in m.ref).map((m) => m.ref.$in))].forEach((id) => { edges += edge(`in:${id}`, `s:${i}`, "data", ""); });
            [...new Map(ms.filter((m) => "$step" in m.ref).map((m) => [m.ref.$step, m.ref])).values()].forEach((r) => { edges += edge(`s:${r.$step}`, `s:${i}`, "data", r.ext || ""); });
            (s.when?.all || []).forEach((c) => { edges += edge(`s:${c.of.step}`, `s:${i}`, "cond", `if ${condText(c).replace(/^step \d+'s /, "")}`); });
          });
          const node = (k, title, sub, cls, badges) => {
            const [x, y] = box(k);
            return `<g class="wfd-node ${cls} ${sel === k ? "sel" : ""}" data-k="${esc(k)}" transform="translate(${x},${y})">
              <rect width="${W}" height="${H}" rx="9"/><text class="t" x="12" y="25">${esc(short(title, 26))}</text><text class="s" x="12" y="46">${esc(short(sub, 32))}</text>
              ${badges.map((b, n) => `<g class="wfd-badge ${b[0]}" transform="translate(${W - 14 - n * 24},-8)"><circle r="10"/><text text-anchor="middle" y="4">${b[1]}</text><title>${esc(b[2])}</title></g>`).join("")}</g>`;
          };
          let nodes = wf.inputs.map((inp) => node(`in:${inp.id}`, inp.label, `input · ${inp.type === "file" ? inp.kind || "file" : inp.type}`, "input", [])).join("");
          nodes += wf.steps.map((s, i) => {
            const m = marks[i] || "", b = [];
            if (s.when?.all?.length) b.push(["when", "⑂", `Runs only if ${s.when.all.map(condText).join(" and ")}`]);
            if (s.checks?.length) b.push(["check", s.checks.some((c) => c.then === "alert") ? "🔔" : "⚑", s.checks.map((c) => `${c.then}: ${condText(c.if)}`).join("\n")]);
            if (m) b.push([m, { done: "✓", run: "…", fail: "✗", skip: "⤼", warn: "⚠", bad: "⛔" }[m] || "", { done: "Done", run: "Running", fail: "Failed", skip: "Skipped", warn: "Done, with warnings", bad: "Its result can't be right / alert" }[m] || ""]);
            return node(`s:${i}`, `${i + 1}. ${s.title}`, toolName(s.endpoint), `step ${m}`, b);
          }).join("");
          const all = Object.values(pos), w = Math.max(600, ...all.map((p) => p[0] + W + 60)), h = Math.max(300, ...all.map((p) => p[1] + H + 60));
          // fitted to the drawing area (scrolls only when it would get smaller than 60 %)
          const room = ($("#wfd-canvas").clientWidth || w) - 8;
          scale = Math.max(0.6, Math.min(1, room / w));
          svg.setAttribute("width", Math.round(w * scale)); svg.setAttribute("height", Math.round(h * scale)); svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
          svg.innerHTML = `<defs><marker id="wfd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="wfd-arrowhead"/></marker>
            <marker id="wfd-arrow-c" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="wfd-arrowhead cond"/></marker></defs>${edges}${nodes}`;
        }
        const svgEl = $("#wfd-svg", dlg);
        svgEl.addEventListener("pointerdown", (e) => {
          const g = e.target.closest(".wfd-node");
          if (!g) return;
          const k = g.dataset.k, [x, y] = posOf()[k] || [20, 20];
          drag = { k, x, y, cx: e.clientX, cy: e.clientY, moved: false };
          svgEl.setPointerCapture(e.pointerId);
        });
        svgEl.addEventListener("pointermove", (ev) => {
          if (!drag) return;
          const dx = (ev.clientX - drag.cx) / scale, dy = (ev.clientY - drag.cy) / scale;
          if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
          if (!drag.moved) return;
          st.wf.layout = { ...(st.wf.layout || {}), [drag.k]: [Math.max(0, Math.round(drag.x + dx)), Math.max(0, Math.round(drag.y + dy))] };
          st.dirty = true; render();
        });
        const drop = () => { if (!drag) return; const d = drag; drag = null; if (!d.moved) { sel = d.k; render(); side(); } };
        svgEl.addEventListener("pointerup", drop);
        svgEl.addEventListener("pointercancel", () => { drag = null; });
        function changed() { st.dirty = true; render(); renderSteps(); renderCautions(); }
        function side() {
          const wf = st.wf, box = $("#wfd-side");
          if (!sel) { box.innerHTML = `<p class="hint">Click a box to edit it.</p>`; return; }
          if (sel.startsWith("in:")) {
            const inp = wf.inputs.find((x) => `in:${x.id}` === sel);
            if (!inp) { sel = null; return side(); }
            box.innerHTML = `<h4>Input</h4><label>Name <input id="wfd-ilabel" value="${esc(inp.label)}" maxlength="120"></label>
              <p class="hint">${inp.type === "file" ? `A ${esc(inp.kind || "file")} file; as saved: <code>${esc(baseName(inp.default))}</code>` : inp.type === "area" ? "An area (a polygon)" : `A value; as saved: ${esc(JSON.stringify(inp.default))}`}. Choose what it is when running, in the panel.</p>`;
            $("#wfd-ilabel").oninput = (e) => { inp.label = e.target.value; changed(); };
            return;
          }
          const i = +sel.slice(2), s = wf.steps[i];
          if (!s) { sel = null; return side(); }
          const ms = markers(s.body);
          const opts = (ref) => [...wf.inputs.map((inp) => [`in|${inp.id}`, `Input: ${inp.label}`]),
            ...wf.steps.slice(0, i).flatMap((x, n) => [".tif", ".csv", ".geojson"].map((ext) => [`st|${n}|${ext}`, `Step ${n + 1}'s ${ext} result (${x.title})`]))]
            .map(([v, t]) => `<option value="${esc(v)}" ${("$in" in ref ? `in|${ref.$in}` : `st|${ref.$step}|${ref.ext}`) === v ? "selected" : ""}>${esc(t)}</option>`).join("");
          box.innerHTML = `<h4>Step ${i + 1}</h4>
            <label>Title <input id="wfd-title-in" value="${esc(s.title)}" maxlength="200"></label>
            <p class="hint" style="margin-top:0">Tool: <code>${esc(s.endpoint)}</code></p>
            ${ms.length ? `<div class="wfd-sec"><b>Data in</b>${ms.map((m, k) => `<label>${esc(m.path.join(" › "))}<select data-mk="${k}">${opts(m.ref)}</select></label>`).join("")}</div>` : ""}
            <div class="wfd-sec" id="wfd-cond"></div>
            <div class="row tight" style="gap:6px;flex-wrap:wrap;margin-top:8px">
              <button class="btn small ghost" id="wfd-up" ${i === 0 ? "disabled" : ""}>← Earlier</button><button class="btn small ghost" id="wfd-down" ${i === wf.steps.length - 1 ? "disabled" : ""}>Later →</button>
              ${wf.steps.length > 1 ? `<button class="btn small ghost danger" id="wfd-del">Remove step</button>` : ""}</div>`;
          $("#wfd-title-in").oninput = (e) => { s.title = e.target.value; changed(); };
          $$("[data-mk]", box).forEach((el) => el.onchange = () => {
            const m = ms[+el.dataset.mk], [kind, a, ext] = el.value.split("|");
            setAt(s.body, m.path, kind === "in" ? { $in: a } : { $step: +a, ext, nth: 0 });
            changed(); side();
          });
          condEditor($("#wfd-cond"), wf, i, changed);
          const reorder = (dir) => { if (moveStep(wf, i, dir)) { wf.layout = {}; sel = `s:${i + dir}`; changed(); side(); } };
          $("#wfd-up").onclick = () => reorder(-1);
          $("#wfd-down").onclick = () => reorder(1);
          $("#wfd-del")?.addEventListener("click", () => { if (confirm(`Remove step ${i + 1} (${s.title})?`) && removeStep(wf, i)) { wf.layout = {}; sel = null; changed(); side(); } });
        }
        $("#wfd-auto", dlg).onclick = () => { st.wf.layout = {}; st.dirty = true; render(); };
        $("#wfd-save", dlg).onclick = async () => {
          if (!st.id) { toast("Name the workflow and save it in the panel first (Save changes)", true); return; }
          await save();
          sel = sel && st.wf ? sel : null; render(); side();
        };
        return {
          open() { if (!st.wf) return toast("Open a workflow first", true); sel = null; dlg.showModal(); render(); side(); },
          refresh(m) { if (m) marks = { ...m }; else if (m === undefined && !st.running) marks = {}; if (dlg.open) { render(); if (!st.running) side(); } },
        };
      }

      // the Assistant hands its plans here: shown (not saved) to review, edit, save or run
      LF.wf = {
        runStep, addResults, inputValue, gate, afterStep, usesSteps,   // for the Assistant, which runs a plan step by step and looks at each result
        show(wf, note) { openUnsaved(JSON.parse(JSON.stringify(wf)), note); },
        async showAndRun(wf) { openUnsaved(JSON.parse(JSON.stringify(wf))); await run(); },
      };
      return {
        async open(arg) {
          await loadList();
          if (arg?.plan) { openUnsaved(JSON.parse(JSON.stringify(arg.plan.workflow)), arg.plan.note); if (arg.plan.run) run(); }
          else if (arg?.fromHistory?.length) fromHistory(arg.fromHistory);
          else if (st.wf) renderInputs();   // Contents may have changed
        },
        layersChanged() { if (st.wf && !st.running) renderInputs(); },
      };
    },
  });
})();
