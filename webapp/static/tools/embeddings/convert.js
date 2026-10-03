/* Embeddings ▸ Convert embeddings: change how an embedding is stored, 8-bit (AlphaEarth coding or scaled per band) ↔
   16 / 32-bit float, optionally unit length. Server: /api/emb/format, /api/emb/convert. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "embconvert", menu: "embed", title: "Convert embeddings", icon: "convert",
    subtitle: "Change how an embedding is stored: 8-bit (AlphaEarth coding or scaled per band) ↔ 16 / 32-bit float. Small files to keep, floats to train on; shows how much the values change",
    kinds: ["embconvert"],
    save: [["embconvert", "#ec-run", "the converted GeoTIFF"]],
    panel: `
      <div class="card">
        <h2>Embedding layer <span class="req">required</span></h2>
        <label>Layer<select id="ec-layer"></select></label>
        <p class="hint" id="ec-info"></p>
      </div>
      <div class="card">
        <h2>Convert to ${tip("The numbers keep their meaning; only how precisely, and in how many bytes, they are stored changes. Keep embeddings small as 8-bit or 16-bit, and convert to 32-bit float to train models, cluster or compute similarity. Converting 8-bit to float is exact; float to 8-bit rounds every value (the result shows by how much).")}</h2>
        <div id="ec-formats" class="em-sources"></div>
        <label class="inline" style="margin-top:10px"><input type="checkbox" id="ec-unit"> Make every vector unit length ${tip("Divide each pixel's vector by its length, so all have length 1 (L2 normalisation). Then cosine similarity is a plain dot product and k-NN, SAM and similarity search treat every pixel alike. AlphaEarth vectors are unit length already; TESSERA's are not. Needed to store TESSERA with the AlphaEarth coding.")}</label>
      </div>
      <div class="card">
        <h2>Output</h2>
        <label>Layer name<input id="ec-name" maxlength="80"></label>
        ${LF.html.run("ec", "Convert")}
      </div>
      <div id="ec-result" class="hidden"></div>`,

    setup(LF) {
      const { $, $$, esc, fmt, prefs, api, toast, getLayer, tipBtn, runJob, runButton, showResult, addRasterFromPath, touched, autoName } = LF;
      const st = { meta: null, info: null, to: prefs.get("ec-to", "float32"), seq: 0 };
      const setName = (l) => l && autoName($("#ec-name"), LF.emb.name(l.name, st.to.replace("-", "_")));

      async function open() {
        try { st.meta ||= await LF.emb.meta(); } catch (e) { toast(e.message, true); return; }
        renderLayers();
      }
      function renderLayers() {
        if (!st.meta) return;
        LF.emb.fill($("#ec-layer"), "No embedding layer yet: download one (Embeddings ▸ Download embeddings) or add a GeoTIFF");
        layerChanged();
      }
      // what the chosen layer is now (format, size) and what it can become
      async function layerChanged() {
        const l = getLayer($("#ec-layer").value), seq = ++st.seq;
        $("#ec-run").disabled = !l;
        if (!l) { st.info = null; $("#ec-info").textContent = ""; $("#ec-formats").innerHTML = ""; return; }
        let info;
        try { info = await api(`/api/emb/format?path=${encodeURIComponent(l.path)}`); } catch (e) { $("#ec-info").innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`; return; }
        if (seq !== st.seq) return;
        st.info = info;
        const F = st.meta.formats;
        $("#ec-info").innerHTML = info.format
          ? `Now: <b>${esc(F[info.format].title)}</b>${info.embedding ? ` · ${esc(info.embedding)}` : ""} · ${info.bands} dimensions · ${info.width.toLocaleString()} × ${info.height.toLocaleString()} pixels · ${fmt(info.size_mb, 1)} MB on disk${info.north_up ? "" : " · stored upside down (as Google's AlphaEarth tiles): written north-up"}`
          : `<span style="color:var(--err)">${esc(info.error)}</span>`;
        if (!info.targets.includes(st.to)) st.to = info.targets.includes("float32") ? "float32" : info.targets[0];
        renderFormats();
        setName(l);
      }
      function renderFormats() {
        const F = st.meta.formats, info = st.info, box = $("#ec-formats");
        if (!info?.format) { box.innerHTML = ""; return; }
        box.innerHTML = info.targets.map((k) => `<label class="em-src ${st.to === k ? "on" : ""}"><input type="radio" name="ec-to" value="${k}" ${st.to === k ? "checked" : ""}>
            <span><b>${esc(F[k].title)}</b> <span class="em-dims">${F[k].bytes} byte${F[k].bytes > 1 ? "s" : ""} · ~${fmt(info.estimates[k], info.estimates[k] < 10 ? 1 : 0)} MB</span>
            ${tipBtn(F[k].about)}</span></label>`).join("");
        $$("input[name=ec-to]", box).forEach((r) => r.onchange = () => {
          st.to = r.value; prefs.set("ec-to", r.value); renderFormats();
          setName(getLayer($("#ec-layer").value));
        });
      }
      $("#ec-layer").onchange = () => layerChanged();
      touched($("#ec-name"));

      runButton("ec", async () => {
        const l = getLayer($("#ec-layer").value);
        if (!l || !st.info?.format) return toast("Choose an embedding layer", true);
        const F = st.meta.formats, name = $("#ec-name").value.trim() || "embedding";
        const r = await runJob("/api/emb/convert", { path: l.path, to: st.to, normalise: $("#ec-unit").checked, name },
                               { tool: "embconvert", title: `Converting to ${F[st.to].title}`, save: "embconvert" });
        await addRasterFromPath(r.path, { name, zoom: false, render: { pca: true, stretch: "auto" } });
        const change = r.size_in_mb ? Math.round(100 * (r.size_out_mb / r.size_in_mb - 1)) : 0;
        showResult("ec", `<div class="card rm-head-card"><h2 style="margin:0">✓ ${esc(F[r.from].title)} → ${esc(F[r.to].title)}</h2>
          <div class="pca-sum">${fmt(r.size_in_mb, 1)} MB → <b>${fmt(r.size_out_mb, 1)} MB</b> on disk (${change > 0 ? "+" : ""}${change} %) · ${r.width.toLocaleString()} × ${r.height.toLocaleString()} × ${r.bands} · ${r.seconds} s${r.normalised ? " · unit length" : ""}${r.flipped ? " · turned north-up" : ""}</div>
          <p class="hint">${r.max_error === 0 ? "<b>Exact</b>: every value is the same as before." :
            `Largest change of a value: <b>${r.max_error.toPrecision(2)}</b> (mean ${r.mean_error.toPrecision(2)}). Each pixel's vector still points the same way: cosine similarity to the original ≥ <b>${r.cosine_min.toFixed(5)}</b> (1 = identical)${r.normalised ? ", measured after making the vectors unit length" : ""}.`}
          ${r.to === "float16" ? " To open this file in other software, it needs GDAL 3.11 or newer (QGIS 3.42+)." : ""}${r.to === "int8-scaled" ? " The scale of each band is stored in the file, so the real values are read back." : ""}</p></div>`);
      });

      return { open, layersChanged: renderLayers };
    },
  });
})();
