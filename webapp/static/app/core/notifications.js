  // Notifications (the bell in the status bar): every message the app showed (downloaded, failed, pasted…) is kept, the
  // last 60, also after a reload; failures say what to do, with the technical details under Details. A red count shows
  // what hasn't been seen.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ notifications
  const np = { open: false, errorsOnly: false };
  function syncBell() {
    const unread = notes.list.filter((n) => n.unread), errs = unread.some((n) => n.err), badge = $("#sb-badge");
    badge.textContent = unread.length > 9 ? "9+" : unread.length;
    badge.classList.toggle("hidden", !unread.length);
    badge.classList.toggle("err", errs);
    $("#sb-bell").title = unread.length ? `Notifications: ${unread.length} new${errs ? ", with failures" : ""}` : "Notifications: the last messages";
    if (np.open) renderNotes();
  }
  function renderNotes() {
    const list = notes.list.filter((n) => !np.errorsOnly || n.err), box = $("#np-list");
    $("#np-errors").classList.toggle("on", np.errorsOnly);
    box.innerHTML = list.length ? list.map((n, i) => `<div class="np-item ${n.err ? "err" : ""} ${n.unread ? "new" : ""}">
        <span class="np-ic">${n.err ? "!" : "✓"}</span>
        <div class="np-t"><div>${esc(n.msg)}</div><small>${esc(ago(n.t / 1000))} · ${esc(new Date(n.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</small>
          ${n.detail ? `<details><summary>Details</summary><pre>${esc(n.detail)}</pre></details>` : ""}</div>
        <button type="button" class="np-copy" data-i="${notes.list.indexOf(n)}" title="Copy">⧉</button></div>`).join("")
      : `<div class="hist-empty">${np.errorsOnly ? "Nothing has failed." : "No messages yet."}</div>`;
    $$(".np-copy", box).forEach((b) => b.onclick = () => { const n = notes.list[+b.dataset.i]; copyText(n.detail ? `${n.msg}\n\n${n.detail}` : n.msg, "Copied"); });
  }
  function toggleNotes(show = !np.open) {
    np.open = show;
    $("#notes-pop").classList.toggle("hidden", !show);
    if (show) {
      renderNotes();
      notes.list.forEach((n) => { n.unread = false; });   // seen
      try { localStorage.setItem("lulc-notes", JSON.stringify(notes.list)); } catch {}
      syncBell();
    }
  }
  $("#sb-bell").onclick = (e) => { e.stopPropagation(); toggleNotes(); };
  $("#np-x").onclick = () => toggleNotes(false);
  $("#np-errors").onclick = () => { np.errorsOnly = !np.errorsOnly; renderNotes(); };
  $("#np-clear").onclick = () => { notes.list.length = 0; try { localStorage.removeItem("lulc-notes"); } catch {} renderNotes(); syncBell(); };
  $("#notes-pop").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => { if (np.open) toggleNotes(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && np.open) toggleNotes(false); });
  notes.onChange = syncBell;
  syncBell();
