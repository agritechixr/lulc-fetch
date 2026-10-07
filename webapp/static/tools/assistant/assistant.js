/* Analysis ▸ Tools ▸ Assistant: say what you want done; it plans it with the app's tools, you check the plan, then it
   runs it step by step, looking at what each step made (GISclaw's Plan → Execute → Replan, arXiv 2603.26845): a step
   that fails, or makes something empty, is fixed (its settings) or the rest is replanned; a new plan for the rest is
   shown to you first unless you let it fix on its own. Errors are kept so later plans avoid them.
   Its memory (OpenClaw): notes it keeps (yours, and what you ask it to remember), known pitfalls, past conversations;
   your saved workflows are its skills. Free first: a local model through Ollama; or Claude with your own key.
   Server: /api/assistant/* · webapp/assistant.py, assistant_data.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const EXAMPLES = ["NDVI of my image inside the field layer, as a table", "A cloud-free Sentinel-2 mosaic of what the map shows, for last month",
    "The 2021 land-cover map of my area of interest", "Remember: my farm is the Fields layer"];
  const MAX_FIX_PER_STEP = 3, MAX_REPLANS = 2;   // as in GISclaw: a few corrections per step, two replans per task

  LF.tool({
    id: "assistant", title: "Assistant", icon: "assistant",
    subtitle: "Say what you want done; it plans it with the app's tools from your data, you check the plan, then it runs it step by step and fixes what goes wrong. A free local model (Ollama) or Claude with your own key",
    panel: `
      <div class="card as-model">
        <div class="row between" style="gap:8px;align-items:center"><span id="as-model-line" class="hint" style="margin:0"></span>
          <span class="row tight" style="gap:4px"><button class="btn small ghost" id="as-mem-btn" title="Its notes, known pitfalls and past conversations">Memory</button><button class="btn small ghost" id="as-set-btn">Settings</button></span></div>
        <div id="as-settings" class="hidden">
          <label class="check"><input type="radio" name="as-prov" value="ollama"> Local model (free, offline, on this computer)</label>
          <div id="as-local" class="as-sub"></div>
          <label class="check"><input type="radio" name="as-prov" value="claude"> Claude, with your own Anthropic key (paid per use, by you)</label>
          <div id="as-cloud" class="as-sub"></div>
          <label class="check" style="margin-top:6px"><input type="checkbox" id="as-auto"> When a step fails, change the rest of the plan without asking me ${tip("A failed step is always fixed and run again on its own (up to 3 times). A new plan for the remaining steps is shown to you first, unless this is ticked.")}</label>
          <div class="row tight" style="gap:6px;margin-top:8px"><button class="btn small primary" id="as-save">Use this</button></div>
        </div>
        <div id="as-memory" class="hidden"></div>
      </div>
      <div id="as-chat" class="as-chat"></div>
      <div class="card as-ask">
        <textarea id="as-input" rows="3" maxlength="4000" placeholder="What do you want done? e.g. NDVI of my image inside my fields, as a table"></textarea>
        <div class="row between" style="gap:6px;margin-top:6px;align-items:center">
          <span class="hint" style="margin:0">Looks at your Contents and the map view · Ctrl+Enter sends ${tip("It sees the open map's layers and tables (their bands, value ranges, columns and a few rows) and what the map shows, plus its notes. It only plans: nothing runs until you press Run.")}</span>
          <span class="row tight" style="gap:6px"><button class="btn small ghost" id="as-new" title="Start a new conversation">New</button><button class="btn small primary" id="as-send">Plan it</button></span>
        </div>
        <div class="as-examples" id="as-examples"></div>
      </div>`,

    setup(LF) {
      const { $, $$, esc, api, toast, prefs, layers, dataItems, map, trackJob, notCancelled, openTool } = LF;
      const st = { status: null, convo: [], turns: [], busy: false, ctrl: null, conv: null, running: false, stop: false, approve: null };
      const newConv = () => `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
      const logEvent = (event) => st.conv && api("/api/assistant/log", { method: "POST", json: { conv_id: st.conv, event } }).catch(() => {});
      $("#as-auto").checked = prefs.get("as-auto", false);
      $("#as-auto").onchange = (e) => prefs.set("as-auto", e.target.checked);

      // ---- set up: which model
      async function refresh() {
        try { st.status = await api("/api/assistant/status"); } catch (e) { $("#as-model-line").textContent = e.message; return; }
        const s = st.status;
        $("#as-model-line").innerHTML = s.ready
          ? `${s.provider === "claude" ? "Claude" : "Local model"} <b>${esc(s.model)}</b>${s.provider === "ollama" ? " · free, on this computer" : " · your key"}${s.provider === "ollama" && s.model !== s.recommended ? ` · <span title="Small models plan simple requests well; for more steps download ${esc(s.recommended)} in Settings">small model: check plans closely</span>` : ""}`
          : `<span class="err-text">Not set up yet</span> · choose a model in Settings`;
        $$("[name=as-prov]").forEach((r) => { r.checked = r.value === s.provider; });
        const o = s.ollama;
        $("#as-local").innerHTML = !o.running
          ? `<p class="hint">Ollama isn't running. Install it (free) from <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com</a>, start it, then <a href="#" id="as-recheck">check again</a>.</p>`
          : `<label>Model <select id="as-model">${o.models.map((m) => `<option ${m === s.model ? "selected" : ""}>${esc(m)}</option>`).join("") || `<option value="">No model downloaded yet</option>`}</select></label>
             ${o.models.includes(s.recommended) ? "" : `<button class="btn small" id="as-pull">Download ${esc(s.recommended)} (recommended, about 4.7 GB)</button>`}
             <p class="hint">Runs on this computer: free and offline. Bigger models plan better (${esc(s.recommended)} needs about 8 GB of memory).</p>`;
        $("#as-cloud").innerHTML = s.claude.key_set
          ? `<p class="hint">Key set (Credentials ▸ Anthropic). Model: ${esc(s.claude.model)}. Each plan is a paid request to Anthropic, on your account.</p>`
          : `<p class="hint">No key yet. <a href="#" id="as-key">Add your Anthropic API key in Credentials</a> (kept in the system keychain).</p>`;
        $("#as-recheck")?.addEventListener("click", (e) => { e.preventDefault(); refresh(); });
        $("#as-key")?.addEventListener("click", (e) => { e.preventDefault(); $("#btn-creds").click(); });
        $("#as-pull")?.addEventListener("click", pullModel);
        if (!s.ready) $("#as-settings").classList.remove("hidden");
      }
      async function pullModel() {
        const m = st.status.recommended;
        try {
          const job = await api("/api/assistant/pull", { method: "POST", json: { model: m } });
          await trackJob(job, { tool: "assistant", title: `Download ${m}` });
          await api("/api/assistant/settings", { method: "PUT", json: { provider: "ollama", model: m } });
          toast(`${m} is ready`);
          refresh();
        } catch (e) { if (notCancelled(e)) toast(e, true); }
      }
      $("#as-set-btn").onclick = () => { $("#as-memory").classList.add("hidden"); $("#as-settings").classList.toggle("hidden"); };
      $("#as-save").onclick = async () => {
        const provider = $$("[name=as-prov]").find((r) => r.checked)?.value || "ollama";
        try {
          st.status = await api("/api/assistant/settings", { method: "PUT", json: { provider, model: provider === "ollama" ? $("#as-model")?.value || "" : "" } });
          await refresh();
          if (st.status.ready) $("#as-settings").classList.add("hidden");
        } catch (e) { toast(e, true); }
      };

      // ---- memory (OpenClaw): notes, known pitfalls (GISclaw's error memory), past conversations
      async function showMemory() {
        const box = $("#as-memory");
        let m;
        try { m = await api("/api/assistant/memory"); } catch (e) { return toast(e, true); }
        box.innerHTML = `<div class="as-sec">Notes ${tip("What the Assistant keeps in mind in every conversation: write anything (your farm, your usual settings), or say “remember …” in a request.")}</div>
          <textarea id="as-notes" rows="4" placeholder="e.g. - My farm is the Fields layer\n- Use a 20% cloud limit">${esc(m.notes)}</textarea>
          <div class="row tight" style="gap:6px;margin:4px 0 8px"><button class="btn small" id="as-notes-save">Save notes</button></div>
          <div class="as-sec">Known pitfalls (${m.pitfalls.length}) ${tip("Errors of the tools in earlier runs, with the fix when one worked: shown to the model so it avoids them.")}</div>
          ${m.pitfalls.length ? `<ul class="as-list as-pits">${m.pitfalls.slice(0, 12).map((p) => `<li><small>${esc(p.endpoint.replace("/api/", ""))}</small> ${esc(p.error.slice(0, 120))}${p.fix ? ` <i>→ ${esc(p.fix.slice(0, 80))}</i>` : ""}</li>`).join("")}</ul>
            <button class="btn small ghost" id="as-pits-clear">Forget them</button>` : `<p class="hint">None yet.</p>`}
          <div class="as-sec" style="margin-top:10px">Conversations</div>
          ${m.conversations.length ? `<div class="as-convs">${m.conversations.slice(0, 12).map((c) => `<div class="as-conv"><a href="#" data-open="${esc(c.id)}">${esc(c.title || "(untitled)")}</a>
              <small>${esc(new Date(c.updated * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }))}</small><button class="np-copy" data-del="${esc(c.id)}" title="Delete">×</button></div>`).join("")}</div>`
            : `<p class="hint">None yet.</p>`}
          <p class="hint">Kept on this computer only: <code>${esc(m.folder)}</code></p>`;
        $("#as-notes-save").onclick = async () => { try { await api("/api/assistant/memory/notes", { method: "PUT", json: { notes: $("#as-notes").value } }); toast("Notes saved"); } catch (e) { toast(e, true); } };
        $("#as-pits-clear")?.addEventListener("click", async () => { await api("/api/assistant/memory/pitfalls", { method: "DELETE" }).catch(() => {}); showMemory(); });
        $$("[data-open]", box).forEach((a) => a.onclick = (e) => { e.preventDefault(); openConversation(a.dataset.open); });
        $$("[data-del]", box).forEach((b) => b.onclick = async () => { await api(`/api/assistant/conversations/${b.dataset.del}`, { method: "DELETE" }).catch(() => {}); showMemory(); });
      }
      $("#as-mem-btn").onclick = () => {
        $("#as-settings").classList.add("hidden");
        const box = $("#as-memory");
        box.classList.toggle("hidden");
        if (!box.classList.contains("hidden")) showMemory();
      };
      async function openConversation(id) {
        if (st.running || st.busy) return toast("Wait for the Assistant to finish first", true);
        let r;
        try { r = await api(`/api/assistant/conversations/${id}`); } catch (e) { return toast(e, true); }
        st.conv = id; st.convo = []; st.turns = [];
        r.events.forEach((ev) => {
          if (ev.role === "user") { st.convo.push({ role: "user", content: ev.text }); st.turns.push({ role: "user", text: ev.text }); }
          else if (ev.role === "assistant") { st.convo.push({ role: "assistant", content: JSON.stringify({ plan: ev.plan, questions: ev.questions }) }); st.turns.push({ role: "assistant", ...ev }); }
          else if (ev.role === "event" && ev.kind === "finished") st.turns.push({ role: "note", text: ev.text });
        });
        $("#as-memory").classList.add("hidden");
        render();
      }

      // ---- what it may know: the open map's layers and tables (names, files, areas) and the view; the server looks inside the files
      const round = (c) => Array.isArray(c) ? c.map(round) : typeof c === "number" ? +c.toFixed(5) : c;
      function areaOf(l) {   // a polygon layer as one area (its outline when it has few points; else its box)
        const polys = l.geojson.features.map((f) => f.geometry).filter((g) => g && /Polygon/.test(g.type));
        if (!polys.length) return null;
        const g = polys.length === 1 ? polys[0] : { type: "MultiPolygon", coordinates: polys.flatMap((p) => p.type === "Polygon" ? [p.coordinates] : p.coordinates) };
        if (JSON.stringify(g).length < 3000) return { type: g.type, coordinates: round(g.coordinates) };
        const b = l.leaflet?.getBounds?.();
        return b && { type: "Polygon", coordinates: [round([[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()], [b.getEast(), b.getNorth()], [b.getWest(), b.getNorth()], [b.getWest(), b.getSouth()]])] };
      }
      // vector layers are kept as files first (uploads/layers/<id>.geojson): the tools read layers from files, and the
      // server can then look at their fields and values
      const saved = {};
      async function saveVectors() {
        for (const l of layers.filter((x) => x.type === "vector" && x.geojson?.features?.length)) {
          if (l.path) continue;
          const key = `${l.geojson.features.length}:${JSON.stringify(l.geojson).length}`;
          if (saved[l.id]?.key === key) continue;
          try { saved[l.id] = { key, path: (await api("/api/vector/save", { method: "POST", json: { layer_id: l.id, geojson: l.geojson } })).path }; } catch {}
        }
      }
      function context() {
        const b = map.getBounds();
        return {
          layers: layers.filter((l) => l.type !== "image").map((l) => l.type === "raster"
            ? { name: l.name, type: "raster", path: l.path, bands: l.info?.count, band_map: l.band_map, scale: l.scale, offset: l.offset, crs: l.info?.crs,
                bounds: l.bounds && round(l.bounds), classes: l.legend?.kind === "classes" ? l.legend.classes.slice(0, 12).map((c) => c.name) : undefined }
            : { name: l.name, type: "vector", path: l.path || saved[l.id]?.path, features: l.geojson?.features?.length, geometry: [...new Set((l.geojson?.features || []).map((f) => f.geometry?.type))].join(", "),
                fields: Object.keys(l.geojson?.features?.[0]?.properties || {}).slice(0, 20), area: l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)) ? areaOf(l) : undefined }),
          tables: dataItems.filter((d) => d.kind === "table").map((d) => ({ name: d.name, path: d.path })),
          map_view: { type: "Polygon", coordinates: [round([[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()], [b.getEast(), b.getNorth()], [b.getWest(), b.getNorth()], [b.getWest(), b.getSouth()]])] },
        };
      }

      // ---- the conversation
      const shortVal = (v) => typeof v === "string" ? v.split("/").pop() : v?.type ? `${v.type} area` : JSON.stringify(v);
      const settingsLine = (body) => Object.entries(body || {}).filter(([, v]) => !(v && typeof v === "object" && ("$in" in v || "$step" in v || (v.type && v.coordinates))))
        .slice(0, 6).map(([k, v]) => `${k}: ${esc(JSON.stringify(v).slice(0, 40))}`).join(" · ");
      // what a step made, in a few words: "raster 3 bands, 0.12–0.81, 2% empty" / "table, 1,227 rows, 6 columns"
      const obsLine = (files) => (files || []).map((f) => f.error ? `${f.path.split("/").pop()}: ${f.error}`
        : f.kind === "raster" ? `${f.path.split("/").pop()}: ${f.bands_total} band${f.bands_total === 1 ? "" : "s"}${f.bands?.[0]?.min != null ? `, ${f.bands[0].min} – ${f.bands[0].max}` : ""}${f.bands?.[0]?.empty_pct ? `, ${f.bands[0].empty_pct}% empty` : ""}`
        : f.kind === "table" ? `${f.path.split("/").pop()}: ${f.rows?.toLocaleString?.() ?? "?"} rows, ${Object.keys(f.columns || {}).length} columns`
        : f.kind === "vector" ? `${f.path.split("/").pop()}: ${f.features} features` : f.path.split("/").pop()).join(" · ");
      function planCard(t, n) {
        const run = t.run;
        return `<div class="as-msg them"><p>${esc(t.plan || "")}</p>
          ${t.remembered?.length ? `<p class="hint">Remembered: ${t.remembered.map(esc).join("; ")}</p>` : ""}
          ${t.questions?.length ? `<div class="as-q"><b>Before planning:</b><ul>${t.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul><span class="hint">Answer below.</span></div>` : ""}
          ${t.workflow?.steps?.length ? `<div class="as-wf">
            ${t.workflow.inputs.length ? `<div class="as-sec">Works on</div><ul class="as-list">${t.workflow.inputs.map((i) => `<li><b>${esc(i.label)}</b> <small>${esc(i.type)}</small> ${esc(String(shortVal(i.default)).slice(0, 60))}</li>`).join("")}</ul>` : ""}
            <div class="as-sec">Steps</div><ol class="as-list">${t.workflow.steps.map((s, i) => {
              const r = run?.steps?.[i] || {};
              return `<li class="as-step ${r.state || ""}"><b>${esc(s.title)}</b> <small>${esc(s.endpoint.replace("/api/", ""))}</small> <span class="as-mark">${r.state === "done" ? "✓" : r.state === "run" ? '<span class="spinner"></span>' : r.state === "fix" ? "↻" : r.state === "fail" ? "✗" : ""}</span>
                <div class="hint" style="margin:0">${r.obs ? esc(r.obs) : settingsLine(s.body)}</div>${r.note ? `<div class="as-note">${esc(r.note)}</div>` : ""}</li>`;
            }).join("")}</ol>
            ${t.problems?.length ? `<div class="warn">The plan may be wrong: ${esc(t.problems.join("; "))}. Check it in Workflows before running.</div>` : ""}
            ${run?.ask ? `<div class="as-approve"><b>New plan for the rest:</b> ${esc(run.ask.plan)}<ol class="as-list">${run.ask.steps.map((s) => `<li>${esc(s.title)} <small>${esc(s.endpoint.replace("/api/", ""))}</small></li>`).join("")}</ol>
                <div class="row tight" style="gap:6px"><button class="btn small primary" data-approve="yes">Continue with it</button><button class="btn small ghost" data-approve="no">Stop</button></div></div>` : ""}
            ${run?.summary ? `<div class="as-done">${esc(run.summary)}</div>` : ""}
            <div class="row tight" style="gap:6px;margin-top:8px;flex-wrap:wrap">
              ${run?.active ? `<button class="btn small ghost" data-stop>Stop</button>`
                : `<button class="btn small primary" data-run="${n}" ${t.problems?.length ? "disabled title='Fix the plan first (Review)'" : ""}>${run ? "Run again" : "Run"}</button>
                   <button class="btn small" data-review="${n}" title="Open it in Workflows: change inputs, steps or settings, save it">${run?.summary ? "Save as workflow…" : "Review &amp; edit…"}</button>`}
            </div></div>` : ""}
          <small class="hint">${esc(t.model || "")}${t.seconds ? ` · ${t.seconds} s` : ""}${t.fixes ? ` · fixed itself ${t.fixes}×` : ""}</small></div>`;
      }
      function render() {
        $("#as-chat").innerHTML = st.turns.map((t, n) => t.role === "user" ? `<div class="as-msg me">${esc(t.text)}</div>`
          : t.role === "note" ? `<div class="as-msg them as-plain">${esc(t.text)}</div>`
          : t.error ? `<div class="as-msg them err">${esc(t.error)}</div>` : planCard(t, n)).join("") +
          (st.busy ? `<div class="as-msg them busy"><span class="spinner"></span> ${esc(st.busy === true ? "Planning…" : st.busy)} <button class="btn small ghost" id="as-stop">Stop</button></div>` : "");
        $$("[data-run]").forEach((b) => b.onclick = () => execute(st.turns[+b.dataset.run]));
        $$("[data-review]").forEach((b) => b.onclick = () => {
          const t = st.turns[+b.dataset.review];
          openTool("workflows", { plan: { workflow: t.run?.workflow || t.workflow, note: "The Assistant's plan: check it, run it, or name and save it" } });
        });
        $$("[data-stop]").forEach((b) => b.onclick = () => { st.stop = true; st.approve?.(false); toast("Stopping after the current step"); });
        $$("[data-approve]").forEach((b) => b.onclick = () => st.approve?.(b.dataset.approve === "yes"));
        $("#as-stop")?.addEventListener("click", () => st.ctrl?.abort());
        $("#as-chat").lastElementChild?.scrollIntoView({ block: "nearest" });
        $("#as-examples").innerHTML = st.turns.length ? "" : EXAMPLES.map((x) => `<button class="as-chip" type="button">${esc(x)}</button>`).join("");
        $$("#as-examples .as-chip").forEach((b) => b.onclick = () => { $("#as-input").value = b.textContent; $("#as-input").focus(); });
      }
      async function send() {
        const text = $("#as-input").value.trim();
        if (!text || st.busy || st.running) return;
        if (!st.status?.ready) { $("#as-settings").classList.remove("hidden"); return toast("Choose a model first (Settings)", true); }
        st.conv ||= newConv();
        st.convo.push({ role: "user", content: text });
        st.turns.push({ role: "user", text });
        $("#as-input").value = "";
        st.busy = true; st.ctrl = new AbortController(); render();
        try {
          await saveVectors();
          const r = await api("/api/assistant/plan", { method: "POST", json: { messages: st.convo, context: context(), conv_id: st.conv }, signal: st.ctrl.signal });
          st.convo.push({ role: "assistant", content: r.reply });
          st.turns.push({ role: "assistant", ...r });
          if (r.remembered?.length) toast(`Remembered: ${r.remembered.join("; ")}`);
        } catch (e) {
          st.convo.pop();   // not answered: it can be sent again
          st.turns.push({ role: "assistant", error: e.name === "AbortError" ? "Stopped." : e.message });
        } finally { st.busy = false; st.ctrl = null; render(); }
      }

      // ---- running a plan: Plan → Execute → Observe → (fix | replan) → …
      const failing = (files) => (files || []).flatMap((f) => [...(f.warnings || []).filter((w) => /empty|no rows|no features/.test(w)), ...(f.error ? [f.error] : [])]);
      const changedKeys = (a, b) => [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])].filter((k) => JSON.stringify(a?.[k]) !== JSON.stringify(b?.[k]));
      async function execute(turn) {
        if (st.running) return;
        st.running = true; st.stop = false;
        const wf = JSON.parse(JSON.stringify(turn.workflow));
        const run = turn.run = { active: true, workflow: wf, steps: wf.steps.map(() => ({})) };
        const ctx = { inputs: {}, outputs: [] }, done = [], results = [], history = [];
        wf.inputs.forEach((inp) => { ctx.inputs[inp.id] = inp.default; });   // the plan's own values (a path, an area, a year)
        let replans = 0;
        render();
        logEvent({ role: "event", kind: "run", plan: turn.plan });
        try {
          for (let i = 0; i < wf.steps.length; i++) {
            let fixes = 0, lastErr = null;
            while (true) {
              if (st.stop) throw Object.assign(new Error("Stopped"), { cancelled: true });
              const s = wf.steps[i], rs = run.steps[i];
              rs.state = "run"; render();
              let problem = null;
              try {
                const r = await LF.wf.runStep(s, ctx, i, wf.steps.length, wf.name, "assistant");
                const obs = (await api("/api/assistant/observe", { method: "POST", json: { paths: r.outs } }).catch(() => ({ files: [] }))).files;
                const bad = failing(obs);
                if (bad.length) problem = { warnings: bad, obs };
                else {
                  ctx.outputs[i] = r.outs; results.push(r);
                  Object.assign(rs, { state: "done", obs: obsLine(obs) || "done" });
                  done.push({ title: s.title, endpoint: s.endpoint, body: s.body, observation: obs });
                  if (lastErr) api("/api/assistant/memory/lesson", { method: "POST", json: { endpoint: s.endpoint, error: lastErr.error, body: s.body,
                    fix: `changed ${changedKeys(lastErr.body, s.body).join(", ") || "the step"}` } }).catch(() => {});
                  logEvent({ role: "event", kind: "step", index: i, title: s.title, ok: true, observation: rs.obs });
                  render();
                  break;
                }
              } catch (e) {
                if (e?.cancelled) throw e;
                problem = { error: e.message };
              }
              // something went wrong: the model fixes this step (or replans the rest), knowing what failed so far
              history.push({ index: i, endpoint: s.endpoint, error: problem.error || problem.warnings.join("; ") });
              lastErr = { error: history.at(-1).error, body: s.body };
              logEvent({ role: "event", kind: "step", index: i, title: s.title, ok: false, error: lastErr.error });
              if (fixes >= MAX_FIX_PER_STEP) { Object.assign(rs, { state: "fail", note: lastErr.error }); throw new Error(`Step ${i + 1} (${s.title}) still fails after ${fixes} fixes: ${lastErr.error}`); }
              fixes++;
              Object.assign(rs, { state: "fix", note: `${problem.error ? "Failed" : "Made something wrong"}: ${lastErr.error.slice(0, 200)} → fixing…` });
              st.busy = "Fixing the plan…"; render();
              let r;
              try {
                r = await api("/api/assistant/continue", { method: "POST", json: { messages: st.convo, context: context(), workflow: wf, done, conv_id: st.conv,
                  failed: { title: s.title, endpoint: s.endpoint, body: s.body, error: problem.error || "", warnings: problem.warnings || [], history } } });
              } finally { st.busy = false; }
              if (r.problems?.length) { Object.assign(rs, { state: "fail", note: `Couldn't fix it: ${r.problems.join("; ")}` }); throw new Error(`Step ${i + 1} couldn't be fixed`); }
              const rest = r.workflow.steps.slice(i);
              const sameShape = rest.length === wf.steps.length - i && rest.every((x, k) => x.endpoint === wf.steps[i + k].endpoint);
              if (!sameShape) {   // a different plan for the rest: shown first (unless the user lets it change plans on its own)
                if (replans >= MAX_REPLANS) throw new Error("The plan had to change too often → try a simpler request");
                replans++;
                if (!prefs.get("as-auto", false)) {
                  run.ask = { plan: r.plan, steps: rest };
                  render();
                  const yes = await new Promise((ok) => { st.approve = ok; });
                  st.approve = null; run.ask = null;
                  if (!yes) throw Object.assign(new Error("Stopped"), { cancelled: true });
                }
              }
              // the new steps from i on; inputs it added
              wf.steps.splice(i, wf.steps.length - i, ...rest);
              r.workflow.inputs.filter((x) => !wf.inputs.some((y) => y.id === x.id)).forEach((x) => { wf.inputs.push(x); ctx.inputs[x.id] = x.default; });
              run.steps = wf.steps.map((_, k) => run.steps[k] && k < i ? run.steps[k] : k === i ? { state: "fix", note: `${sameShape ? "Fixed" : "Replanned"}: ${r.plan.slice(0, 200)}` } : {});
              render();
            }
          }
          const added = await LF.wf.addResults(results, false);
          run.summary = `Done: ${wf.steps.length} step${wf.steps.length === 1 ? "" : "s"}, ${added} result${added === 1 ? "" : "s"} added to Contents.${history.length ? ` It fixed ${history.length} problem${history.length === 1 ? "" : "s"} on the way.` : ""}`;
          toast("The Assistant's plan finished");
          logEvent({ role: "event", kind: "finished", text: run.summary });
        } catch (e) {
          run.summary = e?.cancelled ? "Stopped." : `Stopped: ${e.message}`;
          if (!e?.cancelled) toast(e, true);
          logEvent({ role: "event", kind: "finished", text: run.summary });
        } finally {
          run.active = false; st.running = false; st.busy = false; run.ask = null;
          render();
        }
      }

      $("#as-send").onclick = send;
      $("#as-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
      $("#as-new").onclick = () => { if (st.running) return toast("Wait for the plan to finish (or stop it)", true); st.convo = []; st.turns = []; st.conv = null; render(); $("#as-input").focus(); };
      render();
      return { open() { refresh(); setTimeout(() => $("#as-input").focus(), 50); } };
    },
  });
})();
