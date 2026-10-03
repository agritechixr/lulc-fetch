  // Clean up working files (the cache).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ clean up working files (the cache)
  async function openCacheDialog() {
    const r = await api("/api/cache");
    $("#cc-where").innerHTML = r.temporary ? `Temporary workspace: <code>${esc(r.workspace)}</code>. Tool results are kept here until you delete them.`
      : `Project folder: <code>${esc(r.workspace)}</code>. These are the project's working files.`;
    $("#cc-list").innerHTML = r.folders.map((f) => `<label class="ws-row" style="margin:0"><span><input type="checkbox" value="${f.folder}" ${f.files && f.folder !== "imports" ? "checked" : ""} ${f.files ? "" : "disabled"}> <b>${esc(f.folder)}</b>
      <small>${f.files.toLocaleString()} files · ${fmt(f.size_mb, f.size_mb < 10 ? 1 : 0)} MB${f.oldest_days != null ? ` · oldest ${Math.round(f.oldest_days)} day(s)` : ""}</small></span></label>`).join("");
    $("#dlg-cache").showModal();
  }
  $("#cc-go").onclick = (e) => busy(e.currentTarget, "Deleting…", async () => {
    const folders = $$("#cc-list input:checked").map((i) => i.value), days = +$("#cc-age").value;
    if (!folders.length) return toast("Tick at least one folder", true);
    if (!confirm(`Delete ${days ? `files older than ${days} day(s)` : "ALL files"} in ${folders.join(", ")}? This can't be undone.`)) return;
    const r = await api("/api/cache/clean", { method: "POST", json: { folders, older_than_days: days } });
    toast(`Removed ${r.removed} item(s), freed ${fmt(r.freed_mb, 1)} MB`);
    openCacheDialog();
  });
