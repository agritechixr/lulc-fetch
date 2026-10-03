  // “Also save to a folder on my computer” for every tool, and the files a job made.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ "also save to a folder on my computer" (every tool)
  const SAVE_SPOTS = [  // [key, element the option goes before, what is saved]
    ["search", "#dl-go", "the downloaded files"], ["analyze", "#ex-go", "the exported GeoTIFF"], ["pca", "#pca-run", "the result GeoTIFF"],
    ["stack", "#st-run", "the stacked GeoTIFF"], ["raster2table", "#rt-run", "the table"], ["train", "#mt-run", "the model and its evaluation report"],
    ["predict", "#mp-run", "the map"], ["rasterml", "#rm-run", "the classified map and the model (with its evaluation report)"], ["dlpredict", "#dp-run", "the classified map"], ["detect", "#od-run", "the detected objects (GeoJSON)"], ["cluster", "#uc-run", "the table with clusters (and the model)"], ["tsne", "#ut-run", "the table with map coordinates"],
    ...LF.tools.flatMap((t) => t.save || []),
  ];
  function saveToHtml(key, what, label = "Also save to a folder on my computer") {
    const dir = prefs.get(`save-dir:${key}`, key === "train" ? prefs.get("report-dir", "") : "") || prefs.get("save-dir:last", "");
    return `<div class="save-to" data-save="${key}">
      <label class="inline" style="margin:0"><input type="checkbox" data-save-on ${prefs.get(`save-on:${key}`, false) ? "checked" : ""}> ${esc(label)}
        <button type="button" class="tip" data-tip="The result always goes into the project folder (or, without a project, the app's working folder) and appears in Contents. Tick this to also save a copy of ${what} in a folder you choose. Existing files are never overwritten." aria-label="Help">i</button></label>
      <div class="save-to-row ${prefs.get(`save-on:${key}`, false) ? "" : "hidden"}"><input data-save-dir placeholder="Folder, e.g. ~/Documents/LULC results" value="${esc(dir)}" spellcheck="false" autocomplete="off"><button class="btn small" data-browse>Browse…</button></div>
      <div class="save-note hidden"></div></div>`;
  }
  function wireSaveTo(w) {
    const key = w.dataset.save;
    $("[data-save-on]", w).onchange = (e) => { prefs.set(`save-on:${key}`, e.target.checked); $(".save-to-row", w).classList.toggle("hidden", !e.target.checked); };
    $("[data-save-dir]", w).onchange = (e) => { prefs.set(`save-dir:${key}`, e.target.value.trim()); prefs.set("save-dir:last", e.target.value.trim()); };
    $("[data-browse]", w).onclick = async () => {
      const f = await pickFolder({ title: "Save results in…", start: $("[data-save-dir]", w).value });
      if (f) { $("[data-save-dir]", w).value = f; prefs.set(`save-dir:${key}`, f); prefs.set("save-dir:last", f); }
    };
  }
  SAVE_SPOTS.forEach(([key, sel, what]) => {
    const anchor = $(sel);
    if (!anchor) return;
    const foot = anchor.closest(".modal-foot");
    if (foot) {   // dialogs: at the end of the dialog's body, not between its buttons
      const body = foot.parentElement.querySelector(".modal-body");
      body.insertAdjacentHTML("beforeend", saveToHtml(key, what));
      wireSaveTo(body.lastElementChild);
      return;
    }
    const host = key === "train" ? anchor.closest(".row") : anchor;   // train: above the Train / Compare buttons
    host.insertAdjacentHTML("beforebegin", saveToHtml(key, what));
    wireSaveTo(host.previousElementSibling);
  });
  // Export tool: save straight into a folder instead of a browser download
  $("#lx-go").closest(".row").insertAdjacentHTML("beforebegin", saveToHtml("export", "the exported file", "Save to a folder instead of downloading"));
  wireSaveTo($("#lx-go").closest(".row").previousElementSibling);
  const exportFolder = () => { const w = $('[data-save="export"]'); return w && $("[data-save-on]", w).checked ? $("[data-save-dir]", w).value.trim() : ""; };
  const OUT_KEYS = ["path", "output_table", "files", "outputs"];
  function outputPaths(job) {
    const r = job.result || {}, out = [];
    for (const k of OUT_KEYS) {
      const v = r[k];
      (Array.isArray(v) ? v : [v]).forEach((x) => { if (typeof x === "string" && x && !x.startsWith("/api/")) out.push(x); });
    }
    if (!out.length && job.files?.length) job.files.filter((f) => !/\.(part|log)$/i.test(f)).forEach((f) => out.push(`downloads/${job.id}/${f}`));
    return [...new Set(out)];
  }
  async function saveOutputs(key, job) {
    const w = $(`[data-save="${key}"]`);
    if (!w || !$("[data-save-on]", w).checked) return;
    const folder = $("[data-save-dir]", w).value.trim(), note = $(".save-note", w);
    if (!folder) { toast("The result was kept, but no folder was chosen to save a copy in", true); return; }
    const paths = outputPaths(job);
    if (!paths.length) return;
    try {
      const r = await api("/api/files/save", { method: "POST", json: { paths, folder } });
      if (r.saved.length) api(`/api/history/${job.id}/copy`, { method: "POST", json: { folder: r.folder, files: r.saved } }).catch(() => {});
      note.innerHTML = `✓ Saved ${r.saved.length} file${r.saved.length === 1 ? "" : "s"} to <code title="${esc(r.saved.join("\n"))}">${esc(r.folder)}</code> · <a href="#" data-reveal>Show in folder</a>${r.skipped.length ? ` · <span style="color:var(--warn)">${r.skipped.length} skipped</span>` : ""}`;
      note.classList.remove("hidden");
      $("[data-reveal]", note).onclick = (e) => { e.preventDefault(); api("/api/project/reveal", { method: "POST", json: { path: r.saved[0] || r.folder } }).catch((x) => toast(x.message, true)); };
      toast(`Saved a copy in ${r.folder}`);
    } catch (e) { toast(`Couldn't save the copy: ${e.message}`, true); }
  }
