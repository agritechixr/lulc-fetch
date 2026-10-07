/* Analysis ▸ Tools ▸ Assistant: say what you want done ("NDVI of my fields as a table", "a cloud-free mosaic of this view
   for March"); it plans it as a workflow of the app's tools, from your Contents and the map's view. You check the plan
   (its inputs and steps), then run it (in Workflows, with the usual progress and History), review or save it.
   Free first: a local model through Ollama; or Claude with your own key (Credentials ▸ Anthropic).
   Server: /api/assistant/status, settings, pull, plan · webapp/assistant.py. */
(() => {
  "use strict";
  const { tip } = LF.html;
  const EXAMPLES = ["NDVI of my image inside the field layer, as a table", "A cloud-free Sentinel-2 mosaic of what the map shows, for last month",
    "The 2021 land-cover map of my area of interest", "Cluster the rows of my table into 5 groups"];

  LF.tool({
    id: "assistant", title: "Assistant", icon: "assistant",
    subtitle: "Say what you want done; it plans it with the app's tools from your data, you check the plan, then run it. A free local model (Ollama) or Claude with your own key",
    panel: `
      <div class="card as-model">
        <div class="row between" style="gap:8px;align-items:center"><span id="as-model-line" class="hint" style="margin:0"></span>
          <button class="btn small ghost" id="as-set-btn">Settings</button></div>
        <div id="as-settings" class="hidden">
          <label class="check"><input type="radio" name="as-prov" value="ollama"> Local model (free, offline, on this computer)</label>
          <div id="as-local" class="as-sub"></div>
          <label class="check"><input type="radio" name="as-prov" value="claude"> Claude, with your own Anthropic key (paid per use, by you)</label>
          <div id="as-cloud" class="as-sub"></div>
          <div class="row tight" style="gap:6px;margin-top:8px"><button class="btn small primary" id="as-save">Use this</button></div>
        </div>
      </div>
      <div id="as-chat" class="as-chat"></div>
      <div class="card as-ask">
        <textarea id="as-input" rows="3" maxlength="4000" placeholder="What do you want done? e.g. NDVI of my image inside my fields, as a table"></textarea>
        <div class="row between" style="gap:6px;margin-top:6px;align-items:center">
          <span class="hint" style="margin:0">Uses your Contents and the map view · Ctrl+Enter sends ${tip("The Assistant sees the names, files and areas of the open map's layers and tables (not their contents) and what the map shows. It only plans: nothing runs until you press Run.")}</span>
          <span class="row tight" style="gap:6px"><button class="btn small ghost" id="as-new" title="Start a new conversation">New</button><button class="btn small primary" id="as-send">Plan it</button></span>
        </div>
        <div class="as-examples" id="as-examples"></div>
      </div>`,

    setup(LF) {
      const { $, $$, esc, api, toast, layers, dataItems, map, trackJob, notCancelled, openTool } = LF;
      const st = { status: null, convo: [], turns: [], busy: false, ctrl: null };

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
      $("#as-set-btn").onclick = () => $("#as-settings").classList.toggle("hidden");
      $("#as-save").onclick = async () => {
        const provider = $$("[name=as-prov]").find((r) => r.checked)?.value || "ollama";
        try {
          st.status = await api("/api/assistant/settings", { method: "PUT", json: { provider, model: provider === "ollama" ? $("#as-model")?.value || "" : "" } });
          await refresh();
          if (st.status.ready) $("#as-settings").classList.add("hidden");
        } catch (e) { toast(e, true); }
      };

      // ---- what the Assistant may know: the open map's layers and tables (names, files, areas), the view
      const round = (c) => Array.isArray(c) ? c.map(round) : typeof c === "number" ? +c.toFixed(5) : c;
      function areaOf(l) {   // a polygon layer as one area (its outline only when it has few points; else its box)
        const polys = l.geojson.features.map((f) => f.geometry).filter((g) => g && /Polygon/.test(g.type));
        if (!polys.length) return null;
        const g = polys.length === 1 ? polys[0] : { type: "MultiPolygon", coordinates: polys.flatMap((p) => p.type === "Polygon" ? [p.coordinates] : p.coordinates) };
        if (JSON.stringify(g).length < 3000) return { type: g.type, coordinates: round(g.coordinates) };
        const b = l.leaflet?.getBounds?.();
        return b && { type: "Polygon", coordinates: [round([[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()], [b.getEast(), b.getNorth()], [b.getWest(), b.getNorth()], [b.getWest(), b.getSouth()]])] };
      }
      function context() {
        const b = map.getBounds();
        return {
          layers: layers.filter((l) => l.type !== "image").map((l) => l.type === "raster"
            ? { name: l.name, type: "raster", path: l.path, bands: l.info?.count, band_map: l.band_map, scale: l.scale, offset: l.offset, crs: l.info?.crs,
                bounds: l.bounds && round(l.bounds), classes: l.legend?.kind === "classes" ? l.legend.classes.slice(0, 12).map((c) => c.name) : undefined }
            : { name: l.name, type: "vector", path: l.path, features: l.geojson?.features?.length, geometry: [...new Set((l.geojson?.features || []).map((f) => f.geometry?.type))].join(", "),
                fields: Object.keys(l.geojson?.features?.[0]?.properties || {}).slice(0, 20), area: l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)) ? areaOf(l) : undefined }),
          tables: dataItems.filter((d) => d.kind === "table").map((d) => ({ name: d.name, path: d.path, columns: d.columns?.slice?.(0, 30) })),
          map_view: { type: "Polygon", coordinates: [round([[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()], [b.getEast(), b.getNorth()], [b.getWest(), b.getNorth()], [b.getWest(), b.getSouth()]])] },
        };
      }

      // ---- the conversation
      const shortVal = (v) => typeof v === "string" ? v.split("/").pop() : v?.type ? `${v.type} area` : JSON.stringify(v);
      const settingsLine = (body) => Object.entries(body).filter(([, v]) => !(v && typeof v === "object" && ("$in" in v || "$step" in v)) && !(v && typeof v === "object" && v.type && v.coordinates))
        .slice(0, 6).map(([k, v]) => `${k}: ${esc(JSON.stringify(v).slice(0, 40))}`).join(" · ");
      function render() {
        $("#as-chat").innerHTML = st.turns.map((t, n) => t.role === "user" ? `<div class="as-msg me">${esc(t.text)}</div>`
          : t.error ? `<div class="as-msg them err">${esc(t.error)}</div>`
          : `<div class="as-msg them"><p>${esc(t.plan || "")}</p>
              ${t.questions?.length ? `<div class="as-q"><b>Before planning:</b><ul>${t.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul><span class="hint">Answer below.</span></div>` : ""}
              ${t.workflow?.steps?.length ? `<div class="as-wf">
                ${t.workflow.inputs.length ? `<div class="as-sec">Works on</div><ul class="as-list">${t.workflow.inputs.map((i) => `<li><b>${esc(i.label)}</b> <small>${esc(i.type)}</small> ${esc(String(shortVal(i.default)).slice(0, 60))}</li>`).join("")}</ul>` : ""}
                <div class="as-sec">Steps</div><ol class="as-list">${t.workflow.steps.map((s) => `<li><b>${esc(s.title)}</b> <small>${esc(s.endpoint.replace("/api/", ""))}</small><div class="hint" style="margin:0">${settingsLine(s.body)}</div></li>`).join("")}</ol>
                ${t.problems?.length ? `<div class="warn">The plan may be wrong: ${esc(t.problems.join("; "))}. Check it in Workflows before running.</div>` : ""}
                <div class="row tight" style="gap:6px;margin-top:8px;flex-wrap:wrap">
                  <button class="btn small primary" data-run="${n}" ${t.problems?.length ? "disabled title='Fix the plan first (Review)'" : ""}>Run</button>
                  <button class="btn small" data-review="${n}" title="Open it in Workflows: change inputs, steps or settings, save it">Review &amp; edit…</button>
                </div></div>` : ""}
              <small class="hint">${esc(t.model || "")}${t.seconds ? ` · ${t.seconds} s` : ""}${t.fixes ? ` · fixed itself ${t.fixes}×` : ""}</small></div>`).join("") +
          (st.busy ? `<div class="as-msg them busy"><span class="spinner"></span> Planning… <button class="btn small ghost" id="as-stop">Stop</button></div>` : "");
        $$("[data-run]").forEach((b) => b.onclick = () => openTool("workflows", { plan: { workflow: st.turns[+b.dataset.run].workflow, run: true } }));
        $$("[data-review]").forEach((b) => b.onclick = () => openTool("workflows", { plan: { workflow: st.turns[+b.dataset.review].workflow, note: "The Assistant's plan: check it, run it, or name and save it" } }));
        $("#as-stop")?.addEventListener("click", () => st.ctrl?.abort());
        $("#as-chat").lastElementChild?.scrollIntoView({ block: "nearest" });
        $("#as-examples").innerHTML = st.turns.length ? "" : EXAMPLES.map((x) => `<button class="as-chip" type="button">${esc(x)}</button>`).join("");
        $$("#as-examples .as-chip").forEach((b) => b.onclick = () => { $("#as-input").value = b.textContent; $("#as-input").focus(); });
      }
      async function send() {
        const text = $("#as-input").value.trim();
        if (!text || st.busy) return;
        if (!st.status?.ready) { $("#as-settings").classList.remove("hidden"); return toast("Choose a model first (Settings)", true); }
        st.convo.push({ role: "user", content: text });
        st.turns.push({ role: "user", text });
        $("#as-input").value = "";
        st.busy = true; st.ctrl = new AbortController(); render();
        try {
          const r = await api("/api/assistant/plan", { method: "POST", json: { messages: st.convo, context: context() }, signal: st.ctrl.signal });
          st.convo.push({ role: "assistant", content: r.reply });
          st.turns.push({ role: "assistant", ...r });
        } catch (e) {
          st.convo.pop();   // not answered: it can be sent again
          st.turns.push({ role: "assistant", error: e.name === "AbortError" ? "Stopped." : e.message });
        } finally { st.busy = false; st.ctrl = null; render(); }
      }
      $("#as-send").onclick = send;
      $("#as-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
      $("#as-new").onclick = () => { st.convo = []; st.turns = []; render(); $("#as-input").focus(); };
      render();
      return { open() { refresh(); setTimeout(() => $("#as-input").focus(), 50); } };
    },
  });
})();
