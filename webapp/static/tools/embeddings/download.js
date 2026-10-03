/* Embeddings ▸ Download embeddings: free AI embeddings (Google AlphaEarth 64-D, TESSERA 128-D) for any area and year,
   as a GeoTIFF with all bands, shown in colour. Server: /api/emb/sources, estimate, available, fetch. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "embed", menu: "embed", title: "Download embeddings", icon: "embed",
    subtitle: "Free, open AI embeddings for any area: Google AlphaEarth (64-D) and TESSERA (128-D), 10 m, 2017–2025. See which years exist for your area and download them as a GeoTIFF, with a colour view",
    kinds: ["embfetch", "embcheck"],
    save: [["embfetch", "#em-run", "the embedding GeoTIFF (and its colour view)"]],
    clip: { "em-area": { what: "area is downloaded", required: true } },
    panel: `
      <div class="card">
        <h2>Embedding ${tip("An embedding gives every 10 m pixel a list of numbers (64 or 128) that sums up a whole year of satellite observations. Similar places get similar numbers, so a few labelled points are enough to map crops or land cover, find places like a chosen one, or cluster an area. These open datasets are free and need no account.")}</h2>
        <div id="em-sources" class="em-sources"></div>
        <details class="em-other"><summary class="small">Other open embeddings</summary><div id="em-other"></div></details>
      </div>
      <div class="card">
        <h2>Area <span class="req">required</span></h2>
        <div class="area-pick" style="border-top:0;margin-top:0;padding-top:0">
          <label>Area ${tip("Embeddings are downloaded only for this area. Draw a rectangle, use the current map view, or any polygon layer (e.g. your farm or district). Up to about 200 × 200 km; at 10 m keep it to a few hundred km².")}<select id="em-area"></select></label>
          <p class="hint" id="em-area-hint">Choose an area.</p>
        </div>
        <button class="btn small" id="em-check" style="margin-top:8px">What's available here?</button>
        <div id="em-avail" class="hidden"></div>
      </div>
      <div class="card">
        <h2>Year &amp; resolution</h2>
        <div class="grid2">
          <label>Year<select id="em-year"></select></label>
          <label>Resolution ${tip("10 m is the full detail. AlphaEarth also comes at 20–160 m (averaged vectors, from its overviews): much less to download, good for large areas.")}<select id="em-res"></select></label>
        </div>
        <p class="hint" id="em-est"></p>
      </div>
      <div class="card">
        <h2>Download</h2>
        <label>Layer name<input id="em-name" maxlength="80" value="embedding"></label>
        <label class="inline" style="margin-top:8px"><input type="checkbox" id="em-colour"> Also save the colour view as a 3-band GeoTIFF ${tip("The downloaded layer always keeps all its bands and is shown in colour on the map from them (their three main directions of variation, PCA, as red, green and blue). Tick this to also save that picture as its own small 3-band file, e.g. for other software or a report.")}</label>
        ${LF.html.run("em", "Download embeddings")}
      </div>
      <div id="em-result" class="hidden"></div>`,

    setup(LF) {
      const { $, $$, esc, fmt, prefs, api, toast, getClip, refreshClipPicker, updateClipHint, tipBtn, runJob, runButton, showResult,
              notCancelled, addRasterFromPath, switchTool, openTool, touched } = LF;
      const st = { meta: null, source: prefs.get("em-source", "aef"), estSeq: 0 };

      async function open(pending) {
        if (!st.meta) {
          try { st.meta = await LF.emb.meta(); } catch (e) { toast(e.message, true); return; }
          $("#em-other").innerHTML = st.meta.other.map((o) => `<p class="hint"><a href="${esc(o.url)}" target="_blank" rel="noopener"><b>${esc(o.title)}</b></a>: ${esc(o.what)}. Not per-pixel maps, so not downloadable here yet.</p>`).join("");
          $("#em-year").innerHTML = st.meta.years.slice().reverse().map((y) => `<option>${y}</option>`).join("");
          $("#em-year").value = prefs.get("em-year", "2024");
        }
        renderSources();
        refreshClipPicker("em-area");
        if (pending) {   // opened from Find imagery: its area and year
          const sel = $("#em-area");
          if ([...sel.options].some((o) => o.value === pending.area)) { sel.value = pending.area; updateClipHint("em-area"); }
          if ([...$("#em-year").options].some((o) => o.value === pending.year)) { $("#em-year").value = pending.year; prefs.set("em-year", pending.year); }
          toast(`Area and year (${pending.year}) taken from Find imagery`);
        }
        estimate();
      }

      function renderSources() {
        const box = $("#em-sources");
        box.innerHTML = Object.entries(st.meta.sources).map(([k, s]) => `<label class="em-src ${st.source === k ? "on" : ""}">
            <input type="radio" name="em-src" value="${k}" ${st.source === k ? "checked" : ""}>
            <span><b>${esc(s.title)}</b> <span class="em-dims">${s.dims}-D · ${s.res} m · ${s.years[0]}–${s.years[1]} · ${esc(s.licence)}</span>
            ${tipBtn(`${s.about} ${s.coverage}. By ${s.by}. Licence ${s.licence}.`)}</span></label>`).join("");
        $$("input[name=em-src]", box).forEach((r) => r.onchange = () => { st.source = r.value; prefs.set("em-source", r.value); renderSources(); estimate(); });
        const s = st.meta.sources[st.source], cur = +($("#em-res").value || 10);
        $("#em-res").innerHTML = s.resolutions.map((r) => `<option value="${r}">${r} m${r === 10 ? " (full detail)" : ""}</option>`).join("");
        $("#em-res").value = s.resolutions.includes(cur) ? cur : 10;
      }

      // the size of the download, as the area or the resolution changes
      async function estimate() {
        const g = getClip("em-area"), seq = ++st.estSeq, out = $("#em-est");
        if (!g || !st.meta) { out.textContent = "Choose an area to see the size."; return; }
        try {
          const r = await api("/api/emb/estimate", { method: "POST", json: { clip: g, source: st.source, res: +$("#em-res").value } });
          if (seq !== st.estSeq) return;
          const big = r.width * r.height > 25e6;
          out.innerHTML = `${fmt(r.area_km2, r.area_km2 < 10 ? 2 : 0)} km² → ${r.width.toLocaleString()} × ${r.height.toLocaleString()} pixels in ${esc(r.crs)}, a ${fmt(r.output_mb, 0)} MB GeoTIFF; about ${fmt(r.download_mb, 0)} MB to download.` +
            (big ? ` <span style="color:var(--err)">Too large: choose a smaller area${st.source === "aef" ? " or a coarser resolution" : ""}.</span>`
                 : r.download_mb > 1500 ? ` <span style="color:var(--warn)">That's a big download: it can take a while.</span>` : "");
        } catch (e) { if (seq === st.estSeq) out.innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`; }
      }
      $("#em-res").onchange = estimate;
      $("#em-year").onchange = () => prefs.set("em-year", $("#em-year").value);

      // which years each source has for the area
      $("#em-check").onclick = async () => {
        const g = getClip("em-area");
        if (!g) return toast("Choose an area first", true);
        const btn = $("#em-check"); btn.disabled = true;
        try {
          const r = await runJob("/api/emb/available", { clip: g }, { tool: "embed", title: "Checking what's available here" });
          const S = st.meta.sources;
          const cell = (n, of) => n ? `<td class="ok">✓${of > 1 ? ` <small>${n}/${of}</small>` : ""}</td>` : `<td class="no">–</td>`;
          $("#em-avail").innerHTML = `<table class="em-table"><thead><tr><th>Year</th><th>${esc(S.aef.short)}</th><th>${esc(S.tessera.short)}</th></tr></thead><tbody>` +
            st.meta.years.slice().reverse().map((y) => `<tr data-y="${y}"><td><a href="#" data-em-year="${y}">${y}</a></td>${cell(r.aef[y] ? 1 : 0, 1)}${cell(r.tessera[y], r.tessera_tiles)}</tr>`).join("") +
            `</tbody></table><p class="hint">${r.tessera_tiles > 1 ? `TESSERA: tiles with data out of ${r.tessera_tiles} covering the area${r.tessera_sampled ? " (estimated from a sample)" : ""}. ` : ""}Click a year to use it.</p>`;
          $("#em-avail").classList.remove("hidden");
          $$("[data-em-year]").forEach((a) => a.onclick = (e) => { e.preventDefault(); $("#em-year").value = a.dataset.emYear; prefs.set("em-year", a.dataset.emYear); });
        } catch (e) { if (notCancelled(e)) toast(e.message, true); }
        finally { btn.disabled = false; }
      };

      touched($("#em-name"));
      runButton("em", async () => {
        const g = getClip("em-area");
        if (!g) return toast("Choose an area first", true);
        const year = +$("#em-year").value, S = st.meta.sources[st.source];
        const name = $("#em-name").dataset.touched ? ($("#em-name").value.trim() || "embedding") : `${S.short}_${year}`;
        const r = await runJob("/api/emb/fetch", { clip: g, source: st.source, year, res: +$("#em-res").value, name, colour: $("#em-colour").checked },
                               { tool: "embed", title: `Downloading ${S.short} ${year}`, save: "embfetch" });
        const lyr = await addRasterFromPath(r.path, { name, render: { pca: true, stretch: "auto" } });   // all bands, shown in colour
        if (r.colour) await addRasterFromPath(r.colour.path, { name: `${name} colour view`, render: { rgb: [1, 2, 3], stretch: "none" } });
        const box = showResult("em", `<div class="card rm-head-card"><h2 style="margin:0">✓ ${esc(S.short)} ${year} downloaded</h2>
          <div class="pca-sum">${r.width.toLocaleString()} × ${r.height.toLocaleString()} pixels at ${r.res} m · ${r.dims} dimensions (${esc(S.band_prefix)}${"0".repeat(r.dims < 100 ? 2 : 3)}…) · ${esc(r.crs)} · ${r.valid_pct} % of the area has data · ${r.size_mb} MB · ${r.seconds} s</div>
          <p class="hint">The layer keeps all ${r.dims} bands (see its Metadata) and is <b>shown</b> in colour from them: their three main directions of variation (PCA) as red, green and blue, so alike places get alike colours. Change it in the layer's Properties (any 3 bands, or one band).${r.colour ? ` The colour view was also saved as its own 3-band GeoTIFF.` : ""}</p>
          <div class="row tight" style="margin-top:8px;flex-wrap:wrap;gap:6px"><button class="btn small primary" data-em-explore>Explore it: find similar places…</button></div>
          <p class="hint">Or use the layer in <a href="#" data-go="rasterml">Classical ML for raster</a> (it is recognised as an embedding: k-NN, SVM, logistic regression and SAM with cosine distance are suggested), or cluster it. ${esc(r.attribution)} Licence ${esc(r.licence)}.</p></div>`);
        $("[data-go]", box).onclick = (e) => { e.preventDefault(); switchTool("rasterml"); };
        $("[data-em-explore]", box).onclick = () => openTool("embexplore", { layer: lyr.id });
      });

      return { open, clipChanged: estimate };
    },
  });
})();
