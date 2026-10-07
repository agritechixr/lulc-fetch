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
          <h2>Steps</h2>
          <ol id="wf-steps" class="wf-steps"></ol>
          <label class="check" style="margin-top:6px"><input type="checkbox" id="wf-all-results"> Add every step's results to Contents (not only the last step's)</label>
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
        renderInputs(); renderSteps(); renderSchedule();
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
      function renderSteps(state = {}) {
        const wf = st.wf;
        $("#wf-steps").innerHTML = wf.steps.map((s, i) => {
          const uses = [...JSON.stringify(s.body).matchAll(/"\$step":(\d+)/g)].map((m) => +m[1] + 1), ins = [...JSON.stringify(s.body).matchAll(/"\$in":"(\w+)"/g)].map((m) => wf.inputs.find((x) => x.id === m[1])?.label).filter(Boolean);
          const mark = state[i] || "";
          return `<li class="wf-step ${mark}"><div class="row between" style="gap:6px"><b>${st.editing ? `<input type="text" data-stitle="${i}" value="${esc(s.title)}" maxlength="200">` : esc(s.title)}</b>
              <span class="wf-mark">${mark === "done" ? "✓" : mark === "run" ? '<span class="spinner"></span>' : mark === "fail" ? "✗" : ""}</span></div>
            <small>${[...new Set(ins)].map((x) => `uses ${esc(x)}`).concat([...new Set(uses)].map((n) => `uses step ${n}'s result`)).join(" · ") || "no inputs"}</small>
            ${st.editing ? `<div class="row tight" style="gap:4px;margin-top:4px"><button type="button" class="btn small ghost" data-sjson="${i}">Settings…</button>${wf.steps.length > 1 ? `<button type="button" class="btn small ghost" data-sdel="${i}">Remove</button>` : ""}</div>
              <textarea class="wf-json hidden" data-sbody="${i}" spellcheck="false"></textarea>` : ""}</li>`;
        }).join("");
        $$("[data-stitle]").forEach((el) => el.oninput = () => { wf.steps[+el.dataset.stitle].title = el.value; st.dirty = true; });
        $$("[data-sdel]").forEach((b) => b.onclick = () => {
          const i = +b.dataset.sdel;
          if (JSON.stringify(wf.steps.slice(i + 1)).includes(`"$step":${i}`)) return toast("A later step uses this step's result → remove that one first", true);
          wf.steps.splice(i, 1);
          wf.steps.forEach((s) => { s.body = JSON.parse(JSON.stringify(s.body).replace(/"\$step":(\d+)/g, (m, n) => `"$step":${+n > i ? +n - 1 : +n}`)); });
          st.dirty = true; renderSteps();
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
      async function runOnce(chosen, label, wf = st.wf) {
        const ctx = { inputs: {}, outputs: [] }, marks = {}, shown = () => wf === st.wf;
        const renderSteps_ = (m) => { if (shown()) renderSteps(m); };
        wf.inputs.forEach((inp) => { ctx.inputs[inp.id] = inputValue(inp, chosen[inp.id]); });
        const results = [];
        for (let i = 0; i < wf.steps.length; i++) {
          const s = wf.steps[i];
          marks[i] = "run"; renderSteps_(marks);
          status(`${label}Step ${i + 1} of ${wf.steps.length}: ${s.title}`, true);
          try {
            const r = await runStep(s, ctx, i, wf.steps.length, `${label}${wf.name}`);
            results.push(r);
            const outs = r.outs;
            ctx.outputs[i] = outs;
            marks[i] = "done"; renderSteps_(marks);
          } catch (e) {
            marks[i] = "fail"; renderSteps_(marks);
            if (e?.cancelled) throw e;
            throw Object.assign(new Error(`Step ${i + 1} (${s.title}) failed: ${e.message}`), { detail: e.detail });
          }
        }
        return results;
      }
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
          for (let k = 0; k < runs.length; k++) {
            const [ch, label] = runs[k];
            try {
              const results = await runOnce(ch, runs.length > 1 ? `${k + 1}/${runs.length} ${label}` : "");
              added += await addResults(results, $("#wf-all-results").checked);
              ok++;
            } catch (e) {
              if (e?.cancelled) { notCancelled(e); break; }
              failed.push([label, e]);
              if (runs.length === 1) throw e;
            }
          }
          const secs = Math.round((Date.now() - t0) / 1000);
          box.innerHTML = `<h2>Finished</h2><p>${ok} run${ok === 1 ? "" : "s"} of “${esc(wf.name)}” in ${secs < 90 ? `${secs} s` : `${Math.round(secs / 60)} min`}; ${added} result${added === 1 ? "" : "s"} added to Contents.</p>
            ${failed.length ? `<p class="err-text">${failed.length} failed:</p><ul>${failed.map(([l, e]) => `<li>${esc(l)}${esc(e.message)}</li>`).join("")}</ul>` : ""}
            <p class="hint">Each step is in the History, with its settings and files.</p>`;
          box.classList.remove("hidden");
          toast(failed.length ? `Workflow finished with ${failed.length} failure(s)` : `Workflow “${wf.name}” finished`, !!failed.length);
        } catch (e) {
          box.innerHTML = `<h2>Stopped</h2><p class="err-text">${esc(e.message)}</p>`;
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
            const results = await runOnce(chosen, "Scheduled · ", wf);
            ok = true;
            const added = s.add_results ? await addResults(results, false) : 0;
            const last = results[results.length - 1]?.result;
            if (s.alert) {
              const v = Number(valueAt(last || {}, s.alert.field));
              const hit = Number.isFinite(v) && { "<": v < s.alert.value, ">": v > s.alert.value, "<=": v <= s.alert.value, ">=": v >= s.alert.value }[s.alert.op];
              if (hit) { alert = true; msg = `${s.alert.field} = ${fmtNum(v)} (${s.alert.op} ${s.alert.value})`; }
              else msg = Number.isFinite(v) ? `${s.alert.field} = ${fmtNum(v)}` : `${s.alert.field} not in the result`;
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
      async function save() {
        const wf = { ...st.wf, name: $("#wf-ename").value.trim() || st.wf.name, description: $("#wf-edesc").value.trim() };
        if ($$("#wf-steps textarea.bad").length) return toast("A step's settings aren't valid JSON → fix or close them first", true);
        try {
          const r = await api(`/api/workflows/${st.id ? encodeURIComponent(st.id) : "new"}`, { method: "PUT", json: { workflow: wf } });
          st.id = r.id; st.wf = r; st.editing = false; st.dirty = false;
          await loadList(r.id);
          renderDetail();
          toast(`Workflow “${r.name}” saved`);
        } catch (e) { toast(e, true); }
      }
      $("#wf-new").onclick = pickRuns;
      $("#wf-edit").onclick = () => { st.editing = true; renderDetail(); };
      $("#wf-cancel-edit").onclick = () => openWorkflow(st.id);
      $("#wf-save").onclick = save;
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
      // the Assistant hands its plans here: shown (not saved) to review, edit, save or run
      LF.wf = {
        runStep, addResults, inputValue,   // for the Assistant, which runs a plan step by step and looks at each result
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
