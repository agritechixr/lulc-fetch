/* Embeddings ▸ Explore embeddings: places similar to the ones clicked (cosine similarity), a colour view as its own GeoTIFF,
   or classify the layer. Server: /api/emb/similar, /api/emb/colour. */
(() => {
  "use strict";

  LF.tool({
    id: "embexplore", menu: "embed", title: "Explore embeddings", icon: "similar",
    subtitle: "Find places similar to the ones you click (cosine similarity), make a colour view, or classify any embedding layer",
    kinds: ["embsimilar", "embcolour"],
    save: [["embexplore", "#em-save-anchor", "the similarity maps and colour views"]],
    panel: `
      <div class="card">
        <h2>Embedding layer</h2>
        <label>Layer<select id="em-layer"></select></label>
        <p class="hint" id="em-layer-hint"></p>
      </div>
      <div class="card">
        <h2>Find similar places</h2>
        <p class="hint" style="margin-top:0">Click one or more places on the map (e.g. a field of the crop you look for): every pixel is scored by how alike it is (cosine similarity, 1 = the same).</p>
        <div class="row tight" style="flex-wrap:wrap;gap:6px;margin-top:6px">
          <button class="btn small" id="em-pick">Click a place on the map…</button>
          <button class="btn small ghost hidden" id="em-pick-clear">Clear places</button>
          <span class="hint" id="em-pick-n" style="margin:0"></span>
        </div>
        <button class="btn primary" id="em-similar" style="margin-top:8px;width:100%">Find similar places</button>
      </div>
      <div class="card">
        <h2>More</h2>
        <div class="row tight" style="flex-wrap:wrap;gap:6px">
          <button class="btn small" id="em-colour-btn" title="The layer is already shown in colour; this saves that picture as its own 3-band GeoTIFF">Save the colour view as a 3-band GeoTIFF</button>
          <button class="btn small" id="em-classify">Classify it (Classical ML for raster)…</button>
        </div>
        <div id="em-explore-error" class="warn err hidden"></div>
      </div>
      <div class="card">
        <h2>Output</h2>
        <p class="hint" style="margin-top:0">New layers: every result is added to Contents, nothing is overwritten.</p>
        <div id="em-save-anchor"></div>
      </div>`,

    setup(LF) {
      const { $, fmt, map, toast, getLayer, runJob, notCancelled, addRasterFromPath, switchTool, startDraw } = LF;
      const st = { points: [], markers: L.featureGroup().addTo(map) };

      function renderLayers(pick) {
        const l = LF.emb.fill($("#em-layer"), "No embedding layer yet: download one above, or add a GeoTIFF", { pick });
        $("#em-layer-hint").textContent = l ? `${l.info.count} dimensions${l.info.res ? ` · ${fmt(l.info.res[0], 0)} m pixels` : ""}. Any embedding GeoTIFF works, also your own AlphaEarth or TESSERA exports.` : "";
        ["#em-similar", "#em-colour-btn", "#em-classify", "#em-pick"].forEach((s) => $(s).disabled = !l);
      }
      $("#em-layer").onchange = () => renderLayers();

      // the places to compare with: clicked on the map, shown as purple dots
      function syncPoints() {
        $("#em-pick-n").textContent = st.points.length ? `${st.points.length} place${st.points.length > 1 ? "s" : ""}` : "";
        $("#em-pick-clear").classList.toggle("hidden", !st.points.length);
      }
      function addPoint(lonlat) {
        st.points.push(lonlat);
        L.circleMarker([lonlat[1], lonlat[0]], { radius: 6, color: "#fff", weight: 2, fillColor: "#7c3aed", fillOpacity: 1, interactive: false }).addTo(st.markers);
        syncPoints();
      }
      $("#em-pick").onclick = () => startDraw(L.Draw.CircleMarker, (g) => addPoint(g.coordinates), "#7c3aed");
      $("#em-pick-clear").onclick = () => { st.points = []; st.markers.clearLayers(); syncPoints(); };

      const showError = (e) => { if (notCancelled(e)) { const err = $("#em-explore-error"); err.textContent = e.message; err.classList.remove("hidden"); } };
      $("#em-similar").onclick = async () => {
        $("#em-explore-error").classList.add("hidden");
        const src = getLayer($("#em-layer").value);
        if (!src?.path) return toast("Choose an embedding layer", true);
        if (!st.points.length) return toast("Click at least one place on the map first", true);
        const btn = $("#em-similar"); btn.disabled = true;
        try {
          const r = await runJob("/api/emb/similar", { path: src.path, points: st.points, name: `${src.name}_similar` },
                                 { tool: "embexplore", title: "Finding similar places", save: "embexplore" });
          await addRasterFromPath(r.path, { name: `Similar to ${st.points.length} place${st.points.length > 1 ? "s" : ""} · ${src.name}`, zoom: false,
                                            render: { band: 1, stretch: "auto", cmap: "Magma" } });
          toast(`Similarity map added: median ${fmt(r.p50, 2)}, top 5 % above ${fmt(r.p95, 2)}${r.points < st.points.length ? ` (${st.points.length - r.points} place(s) outside the layer were ignored)` : ""}`);
        } catch (e) { showError(e); }
        finally { btn.disabled = false; }
      };
      $("#em-colour-btn").onclick = async () => {
        const src = getLayer($("#em-layer").value);
        if (!src) return;
        try {
          const r = await runJob("/api/emb/colour", { path: src.path }, { tool: "embexplore", title: "Making a colour view", save: "embexplore" });
          await addRasterFromPath(r.path, { name: `${src.name} colour view`, zoom: false, render: { rgb: [1, 2, 3], stretch: "none" } });
        } catch (e) { if (notCancelled(e)) toast(e.message, true); }
      };
      $("#em-classify").onclick = () => switchTool("rasterml");

      // opened with { layer, point: [lon, lat] } (right-click on the map, or a pixel's "Find similar places")
      function open(arg) {
        renderLayers(arg?.layer);
        if (arg?.point) { addPoint(arg.point); toast(`Place added (${st.points.length} in all): click “Find similar places”, or add more`); }
      }
      return { open, layersChanged: () => renderLayers() };
    },
  });
})();
