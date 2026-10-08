/* Insert ▸ Online & field ▸ Field collection: the phone page (webapp/static/field/, published on the website, offline once
   opened) takes geotagged points and photos; its .zip comes back here (or is dropped on the map: core/add-data.js). The
   points become a layer with their photos (positions written into the photos too), and the photos can go straight to
   Diagnose crop disease. Server: /api/field/info, /api/field/import · lulc_fetch/field.py. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "field", menu: "online", title: "Field collection", icon: "field",
    subtitle: "Take geotagged points and photos with your phone in the field, even offline, then bring them in here as a points layer with photos: send leaf photos straight to Diagnose crop disease",
    panel: `
      <div class="card">
        <h2>1 · On the phone ${tip("A web page that works like an app: open it once with internet, then it works offline (add it to the home screen). Points and photos stay on the phone until you send them. Phones only give a web page its GPS position over https, so the page is on the LULC Fetch website.")}</h2>
        <p style="margin:0 0 6px">Open this page on the phone (Chrome or Safari), allow location, and add it to the home screen:</p>
        <div class="fc-link"><code id="fc-url"></code></div>
        <div class="row tight" style="gap:6px;flex-wrap:wrap;margin-top:6px">
          <button class="btn small" id="fc-copy">Copy link</button>
          <button class="btn small ghost" id="fc-open">Open here</button>
        </div>
        <ol class="fc-steps">
          <li><b>📷 Point + photo</b>: a leaf, a field, a crop: the position is taken as you shoot. <b>📍 Point</b>: a position only.</li>
          <li>Add the crop and a note to each point if you like.</li>
          <li><b>Send to LULC Fetch</b>: one .zip, shared to this computer (Drive, WhatsApp, email, Bluetooth or a cable).</li>
        </ol>
      </div>
      <div class="card">
        <h2>2 · Bring it in</h2>
        <div class="drop" id="fc-drop" role="button" tabindex="0"><b>Drop the field .zip here</b><span>or click to choose it · it can also be dropped on the map</span></div>
        <input type="file" id="fc-file" accept=".zip,application/zip" hidden multiple>
        <div id="fc-error" class="warn hidden"></div>
      </div>
      <div id="fc-result"></div>`,

    setup(LF) {
      const { $, $$, esc, api, toast, openTool, zoomTo, importFieldZip, getLayer } = LF;
      const st = { url: "https://agritechixr.github.io/lulc-fetch/field/", last: [] };
      api("/api/field/info").then((r) => { st.url = r.url; $("#fc-url").textContent = r.url; }).catch(() => {});
      $("#fc-url").textContent = st.url;
      $("#fc-copy").onclick = () => navigator.clipboard.writeText(st.url).then(() => toast("Link copied: send it to your phone"), () => toast(st.url));
      $("#fc-open").onclick = () => window.open(st.url, "_blank", "noopener");

      async function importFiles(files) {
        const err = $("#fc-error");
        err.classList.add("hidden");
        for (const f of [...files].filter((x) => /\.zip$/i.test(x.name))) {
          try { await importFieldZip(f); }   // shows its result through LF.fieldImported
          catch (e) { err.textContent = `${f.name}: ${e.message}`; err.classList.remove("hidden"); }
        }
      }
      $("#fc-file").onchange = (e) => { importFiles(e.target.files); e.target.value = ""; };
      const drop = $("#fc-drop");
      drop.onclick = () => $("#fc-file").click();
      drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
      drop.addEventListener("dragleave", () => drop.classList.remove("over"));
      drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); importFiles(e.dataTransfer.files); });

      // an import (from here or from Add data): its summary, with the next steps
      LF.fieldImported = (r, layer) => {
        st.last.unshift({ r, id: layer.id });
        openTool("field");
        render();
      };
      function render() {
        const box = $("#fc-result");
        box.innerHTML = st.last.slice(0, 5).map(({ r, id }, i) => {
          const l = getLayer(id), crops = [...new Set(r.geojson.features.map((f) => f.properties.crop).filter(Boolean))];
          return `<div class="card">
            <h2 style="margin:0">✓ ${esc(r.name)}</h2>
            <div class="pca-sum">${r.points} point${r.points === 1 ? "" : "s"} · ${r.photos.length} photo${r.photos.length === 1 ? "" : "s"}${crops.length ? ` · ${crops.map(esc).join(", ")}` : ""}</div>
            ${r.photos.length ? `<div class="fc-thumbs">${r.photos.slice(0, 8).map((p) => `<img src="/api/agri/photo?path=${encodeURIComponent(p.path)}&size=120" alt="" title="${esc(p.point || p.name)}" loading="lazy">`).join("")}</div>` : ""}
            <div class="row tight" style="gap:6px;flex-wrap:wrap;margin-top:8px">
              ${r.photos.length ? `<button class="btn small primary" data-fc-diag="${i}">Diagnose the ${r.photos.length} photo${r.photos.length === 1 ? "" : "s"}</button>` : ""}
              ${l ? `<button class="btn small" data-fc-zoom="${i}">Zoom to the points</button>` : ""}
              <button class="btn small ghost" data-fc-reveal="${i}">Show in folder</button>
            </div>
            <p class="hint">Click a point on the map to see its photos and notes. The photos now carry their position (EXIF), so a diagnosis puts them on the map too. Kept in <code>${esc(r.folder)}</code>.</p>
          </div>`;
        }).join("");
        $$("[data-fc-diag]", box).forEach((b) => b.onclick = () => {
          const { r } = st.last[+b.dataset.fcDiag];
          openTool("agridisease", { photos: r.photos.map((p) => ({ path: p.path, name: p.name })) });
        });
        $$("[data-fc-zoom]", box).forEach((b) => b.onclick = () => { const l = getLayer(st.last[+b.dataset.fcZoom].id); if (l) zoomTo(l); });
        $$("[data-fc-reveal]", box).forEach((b) => b.onclick = () => api("/api/project/reveal", { method: "POST", json: { path: st.last[+b.dataset.fcReveal].r.folder } }).catch((e) => toast(e, true)));
      }
      return { open() {} };
    },
  });
})();
