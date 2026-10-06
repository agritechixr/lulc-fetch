  // History menu and window: every tool run with its settings and outputs; Run again / Change settings & run.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ History: every tool run (logs/history.jsonl)
  const KIND_TOOL = { table: "raster2table", train: "ml", predict: "ml", cluster: "ml", tsne: "ml", compare: "ml", python: "ml", pca: "pca",
    stack: "stack", rasterml: "rasterml", patches: "patches", dltrain: "dltrain", dlinstall: "dltrain", dlpredict: "dlpredict", detect: "detect",
    dettrain: "traindet", export: "export", product: "search" };
  LF.tools.forEach((t) => (t.kinds || []).forEach((k) => KIND_TOOL[k] = t.id));
  const toolOfKind = (k) => TOOLS.find((t) => t.id === (KIND_TOOL[k] || "search"));
  const HIST_STATUS = { done: ["Finished", "c0"], error: ["Failed", "c2"], cancelled: ["Cancelled", "c1"], running: ["Running", "c1"], queued: ["Waiting", "c1"] };
  const ago = (t) => { const s = Date.now() / 1000 - t; return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago`
    : new Date(t * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }); };
  const when = (t) => t ? new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" }) : "–";
  const hist = { win: null, id: null, seq: 0 };
  function histRow(r) {
    const [st] = HIST_STATUS[r.status] || [r.status];
    return `<div class="hist-row" data-hid="${esc(r.id)}" title="${esc(r.title)}"><i class="hd ${esc(r.status)}" title="${esc(st)}"></i>
      <span class="ht"><b>${esc(r.title)}</b><small>${esc(st)} · ${esc(ago(r.started))} · ${fmtSecs(r.seconds || 0)}${r.outputs ? ` · ${r.outputs} file${r.outputs > 1 ? "s" : ""}` : ""}${r.copies ? " · saved to a folder" : ""}${r.project ? ` · ${esc(r.project)}` : ""}</small></span>
      <button type="button" class="rb-info" data-hinfo title="Details: settings, input data, where the results went" aria-label="Details">i</button></div>`;
  }
  async function refreshHistoryMenu() {
    const box = $("#history-menu");
    if (!box.children.length) box.innerHTML = `<div class="hist-empty">Loading…</div>`;
    let h;
    try { h = await api("/api/history?limit=12"); } catch (e) { box.innerHTML = `<div class="hist-empty">${esc(e.message)}</div>`; return; }
    box.innerHTML = `<div class="rb-group"><div class="rb-items"><button data-hall class="rb-big"><span class="ic">${svg("history")}</span>Full history…</button><button data-hwfs class="rb-big" title="Chains of tool runs, run again on new data"><span class="ic">${svg("workflow")}</span>Workflows</button></div><div class="rb-cap">History</div></div>
      <div class="rb-group"><div class="rb-items${h.rows.length ? " rb-tri" : ""}">` +
      (h.rows.length ? h.rows.map(histRow).join("") : `<div class="hist-empty">No runs yet: every tool you run is listed here, with its settings and results.</div>`) +
      `</div><div class="rb-cap">Recent tool runs</div></div>`;
    $$("[data-hid]", box).forEach((row) => row.onclick = () => { toggleMenu(null); openHistory(row.dataset.hid); });
    $("[data-hall]", box).onclick = () => { toggleMenu(null); openHistory(null); };
    $("[data-hwfs]", box).onclick = () => { toggleMenu(null); switchTool("workflows"); };
  }
  function openHistory(id) {
    hist.win ||= floatWin($("#hist-float"), $("#hist-head"), "hist-float", () => { const m = $("#map").getBoundingClientRect(); return { x: m.left + 16, y: m.top + 16, w: 520, h: 560 }; });
    hist.win.show();
    id ? showHistoryDetail(id) : showHistoryList();
  }
  async function showHistoryList() {
    hist.id = null;
    $("#hist-title").textContent = "History";
    $("#hist-back").classList.add("hidden");
    $("#hist-tools").classList.remove("hidden");
    const body = $("#hist-body"), seq = ++hist.seq;
    let h;
    try { h = await api(`/api/history?limit=1000&q=${encodeURIComponent($("#hist-q").value.trim())}&status=${$("#hist-status").value}`); }
    catch (e) { body.innerHTML = `<div class="hist-empty">${esc(e.message)}</div>`; return; }
    if (seq !== hist.seq) return;
    body.innerHTML = `<p class="hint" style="margin:2px 4px 6px">${h.total.toLocaleString()} run${h.total === 1 ? "" : "s"}${h.total > h.rows.length ? ` (newest ${h.rows.length} shown)` : ""} · kept in <code>${esc(h.file)}</code></p>` +
      (h.rows.length ? h.rows.map(histRow).join("") : `<div class="hist-empty">${$("#hist-q").value || $("#hist-status").value ? "Nothing matches." : "No runs yet."}</div>`) +
      (h.total ? `<div class="row tight" style="margin:10px 4px 0"><button class="btn small ghost" data-hclear>Clear history…</button></div>` : "");
    $$("[data-hid]", body).forEach((row) => row.onclick = () => showHistoryDetail(row.dataset.hid));
    $("[data-hclear]", body)?.addEventListener("click", async () => {
      if (!confirm("Clear the history? It is kept as history.old.jsonl next to it, so it can be restored by hand.")) return;
      await api("/api/history", { method: "DELETE" }).catch((e) => toast(e, true));
      showHistoryList();
    });
  }
  { let t = 0;
    $("#hist-q").oninput = () => { clearTimeout(t); t = setTimeout(showHistoryList, 250); };
    $("#hist-status").onchange = showHistoryList;
    $("#hist-back").onclick = () => showHistoryList();
    $("#hist-x").onclick = () => hist.win?.hide(); }
  const isPath = (v) => typeof v === "string" && /^(\/|~|[A-Za-z]:\\|downloads\/|uploads\/|tables\/|models\/|imports\/|exports\/|analysis\/|training_data\/)/.test(v) && v.length < 1000;
  const histVal = (v) => isPath(v) ? `<code>${esc(v)}</code>` : v && typeof v === "object" ? `<code>${esc(JSON.stringify(v))}</code>` : esc(String(v ?? "–"));
  function fileRow(p) {
    const tif = /\.(tiff?|vrt)$/i.test(p);
    return `<div class="hist-file"><code>${esc(p)}</code><button class="btn small" data-hreveal="${esc(p)}" title="Show in Finder / Explorer">Show</button>${tif ? `<button class="btn small" data-hmap="${esc(p)}">Add to map</button>` : ""}</div>`;
  }
  async function showHistoryDetail(id) {
    hist.id = id;
    $("#hist-back").classList.remove("hidden");
    $("#hist-tools").classList.add("hidden");
    $("#hist-title").textContent = "Run details";
    const body = $("#hist-body"), seq = ++hist.seq;
    body.innerHTML = `<div class="hist-empty">Loading…</div>`;
    let e;
    try { e = await api(`/api/history/${encodeURIComponent(id)}`); } catch (x) { body.innerHTML = `<div class="hist-empty">${esc(x.message)}</div>`; return; }
    if (seq !== hist.seq) return;
    const [st, cls] = HIST_STATUS[e.status] || [e.status, "c1"], tool = toolOfKind(e.kind);
    const kv = (rows) => `<table class="hist-kv">${rows.filter((r) => r && r[1] !== undefined && r[1] !== null && r[1] !== "").map(([k, v, raw]) => `<tr><td>${esc(k)}</td><td>${raw ? v : histVal(v)}</td></tr>`).join("")}</table>`;
    const inputs = Object.entries(e.inputs || {}), summ = Object.entries(e.summary || {});
    const paths = inputs.flatMap(([, v]) => (Array.isArray(v) ? v : [v]).filter(isPath));
    body.innerHTML = `<div class="hist-head-card"><h3>${esc(e.title)}<span class="pill ${cls}">${esc(st)}</span></h3>
        <p class="hint" style="margin:2px 0 0">${tool ? `Tool: <a href="#" data-htool="${esc(tool.id)}">${esc(tool.title)}</a> · ` : ""}${esc(e.kind)} · run ${esc(e.id)}</p></div>
      <div class="hist-sec">When</div>${kv([["Started", when(e.started)], ["Finished", e.finished ? when(e.finished) : "still running"], ["Took", fmtSecs(e.seconds || 0)],
        ["Project", e.project || "temporary workspace"], ["Workspace", e.workspace]])}
      <div class="hist-sec">Input data</div>${inputs.length ? kv(inputs.map(([k, v]) => [k, v])) : `<p class="hint" style="margin:2px 4px">None recorded.</p>`}
      ${paths.length ? `<div style="margin-top:4px">${paths.map(fileRow).join("")}</div>` : ""}
      <div class="hist-sec">Settings</div><pre class="hist-json">${esc(JSON.stringify({ ...(e.settings || {}), ...(Object.keys(e.params || {}).length ? { _job: e.params } : {}) }, null, 2))}</pre>
      ${e.endpoint ? `<p class="hint" style="margin:2px 4px">Request: <code>${esc(e.endpoint)}</code></p>` : ""}
      ${summ.length ? `<div class="hist-sec">Results</div>${kv(summ.map(([k, v]) => [k, v]))}` : ""}
      <div class="hist-sec">Output files</div>${(e.outputs || []).length ? e.outputs.map(fileRow).join("") : `<p class="hint" style="margin:2px 4px">${e.status === "done" ? "No files." : "None (the run didn't finish)."}</p>`}
      ${(e.copies || []).length ? `<div class="hist-sec">Copies saved to a folder</div>` + e.copies.map((c) => `<p class="hint" style="margin:4px">${esc(when(c.time))} → <code>${esc(c.folder)}</code></p>` + c.files.map(fileRow).join("")).join("") : ""}
      ${e.error ? `<div class="hist-sec" style="color:var(--err)">Why it failed</div><p style="margin:4px;font-size:12.5px;color:var(--err)">${esc(e.error)}</p>` : ""}
      ${(e.log || []).length ? `<div class="hist-sec">Steps</div><pre class="hist-json">${esc(e.log.join("\n"))}</pre>` : ""}
      <div class="hist-sec">Run again</div>
      ${e.repeatable ? `<div class="row tight" style="margin:4px;gap:6px;flex-wrap:wrap"><button class="btn small primary" data-hrerun>↻ Run again</button><button class="btn small" data-hedit>Change settings &amp; run…</button></div>
        <p class="hint" style="margin:4px">Runs it again with the same settings (or the ones you change); the results are added to Contents as new layers.</p>
        <div class="hidden" data-heditbox><textarea class="hist-edit" spellcheck="false" aria-label="Settings (JSON)"></textarea>
          <div class="row tight" style="margin:6px 0 0;gap:6px"><button class="btn small primary" data-hrunedit>Run with these settings</button><button class="btn small ghost" data-hcancel>Cancel</button><span class="hint" data-hjsonerr style="margin:0;color:var(--err)"></span></div></div>`
        : `<p class="hint" style="margin:4px">${esc(e.repeat_note || "This run can't be repeated from here (it was recorded before Run again existed).")}</p>`}
      <div class="row tight" style="margin:12px 4px 0;gap:6px;flex-wrap:wrap">${e.repeatable && e.status === "done" ? `<button class="btn small" data-hwf title="A workflow that runs this again on other data (Analysis ▸ Tools ▸ Workflows)">Make a workflow…</button>` : ""}<button class="btn small" data-hcopy>Copy as JSON</button>${tool ? `<button class="btn small" data-htool="${esc(tool.id)}">Open ${esc(tool.title)}</button>` : ""}${e.error ? `<button class="btn small ghost" data-herrlog>Error log</button>` : ""}</div>`;
    $$("[data-hreveal]", body).forEach((b) => b.onclick = () => {
      api("/api/project/reveal", { method: "POST", json: { path: b.dataset.hreveal } }).catch((x) => toast(x, true));   // opens its folder
    });
    $$("[data-hmap]", body).forEach((b) => b.onclick = () => addRasterFromPath(b.dataset.hmap).catch((x) => toast(`Can't add it to the map: ${x.message}`, true)));
    $$("[data-htool]", body).forEach((a) => a.onclick = (ev) => { ev.preventDefault(); switchTool(a.dataset.htool); });
    $("[data-hwf]", body)?.addEventListener("click", () => openTool("workflows", { fromHistory: [e.id] }));
    $("[data-hcopy]", body).onclick = async () => { try { await navigator.clipboard.writeText(JSON.stringify(e, null, 2)); toast("Copied"); } catch { toast("Couldn't copy", true); } };
    $("[data-herrlog]", body)?.addEventListener("click", () => window.open("/api/errors/file", "_blank", "noopener"));
    if (e.repeatable) {
      const box = $("[data-heditbox]", body), ta = $(".hist-edit", box);
      $("[data-hrerun]", body).onclick = () => rerunHistory(e);
      $("[data-hedit]", body).onclick = async () => {
        try { const r = await api(`/api/history/${encodeURIComponent(e.id)}/request`); ta.value = JSON.stringify(r.body, null, 2); box.classList.remove("hidden"); ta.focus(); }
        catch (x) { toast(x, true); }
      };
      $("[data-hcancel]", body).onclick = () => box.classList.add("hidden");
      $("[data-hrunedit]", body).onclick = () => {
        let changed;
        try { changed = JSON.parse(ta.value); } catch (x) { $("[data-hjsonerr]", body).textContent = `Not valid JSON: ${x.message}`; return; }
        $("[data-hjsonerr]", body).textContent = "";
        rerunHistory(e, changed);
      };
    }
    body.scrollTop = 0;
  }
  // Run a recorded run again: the same request (or a changed copy) to the same tool; its results are added to Contents
  async function rerunHistory(e, changedBody = null) {
    let req;
    try { req = await api(`/api/history/${encodeURIComponent(e.id)}/request`); } catch (x) { return toast(x, true); }
    if (!req.same_workspace && !confirm(`This run was made in another project or workspace:\n${req.workspace}\n\nIts layers and files are looked up in the one open now, so it may not find them. Run it anyway?`)) return;
    const tool = toolOfKind(e.kind);
    if (tool) switchTool(tool.id);
    try {
      const job = await api(req.endpoint, { method: "POST", json: changedBody || req.body });
      if (!job?.id) { toast("Done"); return; }
      const done = await trackJob(job, { title: `${changedBody ? "Run with changed settings" : "Run again"} · ${e.title}` });
      let added = 0;
      const r = done.result || {};
      for (const p of [...outputPaths(done), r.csv, r.output_table].filter((x, i, a) => typeof x === "string" && a.indexOf(x) === i)) {
        const name = p.split("/").pop();
        if (/\.(tiff?)$/i.test(p) && !/_colour\.tif$/i.test(p)) { try { await addRasterFromPath(p, { name: name.replace(/\.tiff?$/i, ""), zoom: added === 0 }); added++; } catch {} }
        else if (/^tables\/.+\.(csv|parquet)$/i.test(p)) { addItem({ kind: "table", name, path: p }); added++; }
        else if (/\.geojson$/i.test(p) && p.startsWith(`downloads/${done.id}/`)) {   // e.g. the photo points of a diagnosis
          try {
            let fc = await api(`/api/jobs/${done.id}/files/${encodeURIComponent(name)}`);
            if (typeof fc === "string") fc = JSON.parse(fc);
            if (fc?.features?.length) { addVectorLayer(fc, name.replace(/\.geojson$/i, ""), { path: p }); saveLayers(); added++; }
          } catch {}
        }
      }
      toast(`Finished: ${added ? `${added} result${added > 1 ? "s" : ""} added to Contents` : "see History for its files"}`);
      if (hist.win?.open && !hist.id) showHistoryList();
    } catch (x) { if (notCancelled(x)) toast(x, true); }
  }

  function finishRun(tool, run, state, error) {
    run.state = state; run.error = error || null; run.finished = Date.now();
    if (runs[tool] === run) renderRunBar();
    if (state === "done") setTimeout(() => { if (runs[tool] === run) { delete runs[tool]; renderRunBar(); } }, 5000);
  }

  // Track a background job until it finishes. Resolves with the finished job; throws CancelledError / Error.
  async function trackJob(job, { tool = currentTool, title, save, onPoll } = {}) {
    const run = { title: title || job.title, progress: 0, message: "Starting…", started: Date.now(), jobId: job.id, logs: job.logs || [],
                  cancel: () => api(`/api/jobs/${job.id}/cancel`, { method: "POST" }).catch(() => {}) };
    runs[tool] = run;
    renderRunBar();
    try {
      let j = job;
      while (j.status === "queued" || j.status === "running") {
        await sleep(700);
        j = await api(`/api/jobs/${job.id}`);
        run.progress = j.progress;
        run.logs = j.logs || run.logs;
        if (onPoll) try { onPoll(j); } catch {}
        if (!run.cancelling) run.message = (j.message || run.message).replace(/^\d\d:\d\d:\d\d\s+/, "");
        if (runs[tool] === run) renderRunBar();
      }
      if (j.status === "cancelled") throw new CancelledError();
      if (j.status === "error") {   // stays on screen with the reason (also written to logs/errors.log by the server)
        run.logs = j.logs || run.logs;
        const e = friendlyErr(j.error || "The job failed");
        finishRun(tool, run, "failed", e.message);
        throw e;
      }
      if (save) await saveOutputs(save, j);
      finishRun(tool, run, "done");
      return j;
    } catch (e) {
      if (!run.state && !(e instanceof CancelledError)) {   // the request itself failed (server unreachable…)
        finishRun(tool, run, "failed", e.message);
        reportError(run.title, e.message);
      }
      throw e;
    } finally {
      if (runs[tool] === run && !run.state) { delete runs[tool]; renderRunBar(); }
      refreshJobs();
    }
  }
  // failures that never reached a server job (a request that failed) also go into the error log
  function reportError(title, message) {
    api("/api/errors/report", { method: "POST", json: { title: String(title || "").slice(0, 300), error: String(message || "").slice(0, 4000), tool: currentTool } }).catch(() => {});
  }
  // Track a single request (no server-side progress): indeterminate bar; Cancel aborts it.
  async function trackFetch(fn, { tool = currentTool, title, message } = {}) {
    const ctrl = new AbortController();
    const run = { title, progress: null, message: message || "Working…", started: Date.now(), cancel: () => ctrl.abort() };
    runs[tool] = run;
    renderRunBar();
    try {
      const res = await fn(ctrl.signal);
      finishRun(tool, run, "done");
      return res;
    } catch (e) {
      if (e.name === "AbortError" || ctrl.signal.aborted) throw new CancelledError();
      finishRun(tool, run, "failed", e.message);
      reportError(title, e.message);
      throw e;
    } finally {
      if (runs[tool] === run && !run.state) { delete runs[tool]; renderRunBar(); }
    }
  }
  const notCancelled = (e) => { if (e?.cancelled) { status("Cancelled"); toast("Cancelled"); return false; } return true; };

  function download(url, name) {
    const a = document.createElement("a");
    a.href = url; a.download = name || "";
    document.body.append(a); a.click(); a.remove();
  }
