/* Agri ▸ Crop disease guide: symptoms, treatment and pests for each crop and disease, from the knowledge base
   (lulc_fetch/agri/data/kb), searchable. Server: /api/agri/schema, /api/agri/guide/diseases, /api/agri/guide/search.
   Other tools open it at a disease with LF.openTool("agriguide", { crop, disease }). */
(() => {
  "use strict";

  LF.tool({
    id: "agriguide", menu: "agri", title: "Crop disease guide", icon: "book",
    subtitle: "Symptoms, treatment and pests for each crop and disease, from a knowledge base of about 9,000 expert questions and answers; searchable",
    panel: `
      <div class="card">
        <label style="margin-top:0">Crop</label>
        <div id="ag-crop"></div>
        <p class="hint" id="ag-crop-info"></p>
        <label>Search this crop <input type="search" id="ag-q" placeholder="e.g. yellow leaves, spray schedule, fruit drop" autocomplete="off"></label>
      </div>
      <div class="card">
        <h2>Diseases &amp; pests</h2>
        <div class="ag-list" id="ag-diseases"></div>
      </div>
      <div id="ag-records"></div>`,

    setup(LF) {
      const { $, $$, esc, fmt, prefs, api, toast, searchPicker } = LF;
      const pct = LF.agri.pct;
      const st = { schema: null, crop: prefs.get("ag-crop", "Mango"), disease: null, section: null, diseases: [], seq: 0, picker: null };

      async function open(arg) {
        if (arg?.crop) {   // opened at a disease (e.g. from a diagnosis)
          st.crop = arg.crop; st.disease = arg.disease === "Healthy" ? null : arg.disease || null; st.section = null; prefs.set("ag-crop", arg.crop);
          $("#ag-q").value = "";
        }
        if (!st.schema) {
          try { st.schema = await LF.agri.schema(); } catch (e) { toast(e, true); return; }
          const crops = Object.entries(st.schema.crops).sort((a, b) => (a[1].limited_kb - b[1].limited_kb) || a[1].name.localeCompare(b[1].name));
          st.picker = searchPicker($("#ag-crop"), { value: st.crop, placeholder: "Type a crop: e.g. man, paddy, bhindi…", empty: "No crop matches",
            items: crops.map(([k, c]) => ({ id: k, title: c.name, aliases: c.aliases, keywords: c.labels,
              group: c.limited_kb ? "Symptoms only" : "Full guide: symptoms, treatment, pests",
              sub: [c.aliases.slice(0, 3).join(", "), `${c.kb_records.toLocaleString()} answers`].filter(Boolean).join(" · ") })),
            onChange: (k) => { st.crop = k; st.disease = null; st.section = null; prefs.set("ag-crop", k); loadCrop(); } });
          let t = 0;
          $("#ag-q").oninput = () => { clearTimeout(t); t = setTimeout(renderRecords, 250); };
        }
        if (!st.schema.crops[st.crop]) st.crop = "Mango";
        st.picker.set(st.crop);
        await loadCrop();
      }

      async function loadCrop() {
        const c = st.schema.crops[st.crop];
        $("#ag-crop-info").innerHTML = `${c.kb_records.toLocaleString()} questions &amp; answers · photo model: ${c.labels.length} classes, ${pct(c.accuracy)} on test photos.` +
          (c.limited_kb ? ` <span style="color:var(--warn)">Symptom descriptions only (from LeafNet): for treatment, ask your local agriculture office.</span>` : "");
        try { st.diseases = (await api(`/api/agri/guide/diseases?crop=${encodeURIComponent(st.crop)}`)).diseases; } catch (e) { toast(e, true); return; }
        renderDiseases();
        renderRecords();
      }

      // the diseases the photo model detects, then the rest of the knowledge base (pests, disorders, practices)
      function renderDiseases() {
        const box = $("#ag-diseases"), inModel = st.diseases.filter((d) => d.in_model), other = st.diseases.filter((d) => !d.in_model && d.records);
        const item = (d) => `<button class="ag-item ${st.disease === d.name ? "on" : ""}" data-ag-d="${esc(d.name)}" title="${d.in_model ? `The photo model detects this${d.f1 != null ? ` (F1 ${fmt(d.f1, 2)} on test photos)` : ""}` : "In the knowledge base only (not detected from photos)"}">
            <span>${d.healthy ? "🌿 " : ""}${esc(d.name)}</span><small>${d.records || ""}</small></button>`;
        box.innerHTML = `<div class="home-label" style="margin-top:0">Detected from photos</div><div class="ag-grid">${inModel.map(item).join("")}</div>
          ${other.length ? `<details ${st.disease && !inModel.some((d) => d.name === st.disease) ? "open" : ""}><summary class="small" style="margin-top:8px">${other.length} more in the knowledge base (pests, disorders, practices)</summary><div class="ag-grid" style="margin-top:6px">${other.map(item).join("")}</div></details>` : ""}
          ${st.disease ? `<div class="row tight" style="margin-top:8px"><button class="btn small ghost" data-ag-all>← All of ${esc(st.schema.crops[st.crop].name)}</button></div>` : ""}`;
        $$("[data-ag-d]", box).forEach((b) => b.onclick = () => { st.disease = st.disease === b.dataset.agD ? null : b.dataset.agD; st.section = null; renderDiseases(); renderRecords(); });
        $("[data-ag-all]", box)?.addEventListener("click", () => { st.disease = null; st.section = null; renderDiseases(); renderRecords(); });
      }

      // the questions & answers of the chosen disease and / or the search, by section
      async function renderRecords() {
        const q = $("#ag-q").value.trim(), box = $("#ag-records"), seq = ++st.seq;
        if (!st.disease && !q) {
          box.innerHTML = `<p class="hint">Pick a disease or pest above, or search, to read its symptoms and how to manage it.</p>`;
          return;
        }
        const params = new URLSearchParams({ crop: st.crop, limit: "300" });
        if (st.disease) params.set("disease", st.disease);
        if (q) params.set("q", q);
        let r;
        try { r = await api(`/api/agri/guide/search?${params}`); } catch (e) { toast(e, true); return; }
        if (seq !== st.seq) return;   // a newer search is on its way
        const S = st.schema.sections, order = ["symptoms", "management", "pests", "growing"].filter((k) => r.sections[k]);
        const sec = order.includes(st.section) ? st.section : null;
        const recs = r.records.filter((x) => !sec || x.section === sec);
        const title = st.disease ? `${esc(st.disease)}${q ? ` · “${esc(q)}”` : ""}` : `“${esc(q)}” in ${esc(st.schema.crops[st.crop].name)}`;
        box.innerHTML = `<div class="card">
            <h2 style="margin-bottom:6px">${title}</h2>
            ${r.total ? `<div class="row tight" style="flex-wrap:wrap;gap:4px;margin-bottom:6px"><button class="chip ${sec ? "" : "active"}" data-ag-s="">All ${r.total}</button>${order.map((k) => `<button class="chip ${sec === k ? "active" : ""}" data-ag-s="${k}">${esc(S[k])} ${r.sections[k]}</button>`).join("")}</div>` : ""}
            ${recs.length ? recs.map((x, i) => `<details class="ag-qa" ${i < 3 ? "open" : ""}><summary>${esc(x.question)}</summary>
                <p>${esc(x.answer)}</p><div class="ag-meta">${[!st.disease && x.disease, x.growth_stage && x.growth_stage.replace(/_/g, " "), x.source].filter(Boolean).map(esc).join(" · ")}</div></details>`).join("")
              : `<p class="hint">Nothing found${q ? `: try other words, or clear the search` : ""}.${st.schema.crops[st.crop].limited_kb ? " This crop's guide only describes symptoms." : ""}</p>`}
            ${r.total > r.records.length ? `<p class="hint">Showing the first ${r.records.length} of ${r.total}: search to narrow down.</p>` : ""}
          </div>`;
        $$("[data-ag-s]", box).forEach((b) => b.onclick = () => { st.section = b.dataset.agS || null; renderRecords(); });
      }

      return { open };
    },
  });
})();
