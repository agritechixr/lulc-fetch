  // Folder picker (on the server, so it returns real paths).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ folder picker (server-side, so it returns real paths)
  const fp = { path: null, sel: null, mode: "folder", resolve: null };
  const FOLDER_SVG = (isProj) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="${isProj ? "var(--accent)" : "#e3b341"}" fill-opacity="${isProj ? ".9" : ".85"}" stroke="none"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.6l2 2h8.4A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>${isProj ? '<path d="M8 13.5l2.5 2.5L16 11" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' : ""}</svg>`;
  const ZIP_SVG = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#64748b" stroke-width="1.8" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6z"/><path d="M11 5h2M11 8h2M11 11h2M11 14h2v3h-2z"/></svg>`;
  function pickFolder({ title = "Choose a folder", start = "", mode = "folder", okLabel } = {}) {
    fp.mode = mode; fp.sel = null; fp.okLabel = okLabel;
    $("#fp-title").textContent = title;
    $("#fp-newname").value = "";
    $("#dlg-folder").showModal();
    fpLoad(start || prefs.get("fp-last", ""));
    return new Promise((res) => { fp.resolve = res; });
  }
  $("#dlg-folder").addEventListener("close", () => { if (fp.resolve) { fp.resolve(fp.result ?? null); fp.resolve = null; fp.result = null; } });
  async function fpLoad(path) {
    let r;
    const q = fp.mode === "product" ? "&products=true" : "";
    try { r = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}${q}`); }
    catch (e) { $("#fp-hint").innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`; if (fp.path) return; r = await api(`/api/fs/list?path=${q}`); }
    fp.path = r.path; fp.sel = null; fp.info = r;
    $("#fp-path").value = r.path;
    $("#fp-up").disabled = !r.parent;
    $("#fp-up").onclick = () => r.parent && fpLoad(r.parent);
    $("#fp-shortcuts").innerHTML = r.shortcuts.map((sc) => `<button class="fp-sc ${sc.path === r.path ? "on" : ""}" data-p="${esc(sc.path)}" title="${esc(sc.path)}">${esc(sc.name)}</button>`).join("");
    $$("#fp-shortcuts [data-p]").forEach((b) => b.onclick = () => fpLoad(b.dataset.p));
    $("#fp-list").innerHTML = r.dirs.length ? r.dirs.map((d) => `<div class="fp-item ${d.project ? "is-proj" : ""} ${d.product ? "is-product" : ""}" data-p="${esc(d.path)}" ${d.file ? "data-file" : ""} title="${d.product ? "Double-click to open this product" : "Double-click to open"}">
        <span class="fp-ic">${d.file ? ZIP_SVG : FOLDER_SVG(d.project)}</span><span class="fp-n">${esc(d.name)}</span>${d.project ? '<span class="fp-badge">project</span>' : ""}${d.product ? '<span class="fp-badge">Sentinel product</span>' : ""}</div>`).join("")
      : `<p class="hint" style="padding:10px">No sub-folders here.</p>`;
    $$("#fp-list .fp-item").forEach((el) => {
      el.onclick = () => { fp.sel = el.dataset.p; $$("#fp-list .fp-item").forEach((x) => x.classList.toggle("sel", x === el)); fpHint(); };
      el.ondblclick = () => { if (fp.mode === "product" && el.classList.contains("is-product")) { fp.sel = el.dataset.p; $("#fp-ok").click(); } else if (!el.hasAttribute("data-file")) fpLoad(el.dataset.p); };
    });
    fpHint();
  }
  function fpTarget() { return fp.sel || fp.path; }
  function fpHint() {
    const t = fpTarget(), isProj = fp.sel ? !!$(`#fp-list .fp-item.sel.is-proj`) : fp.info?.project;
    const ok = $("#fp-ok");
    if (fp.mode === "product") {
      const isProd = fp.sel ? !!$("#fp-list .fp-item.sel.is-product") : fp.info?.product;
      const holds = !fp.sel && $$("#fp-list .fp-item.is-product").length;
      ok.textContent = isProd ? "Open product" : holds ? `Open all ${holds} products here` : "Open product"; ok.disabled = !isProd && !holds;
      $("#fp-hint").innerHTML = isProd || holds ? `<code>${esc(t)}</code>` : "Choose a <b>.SAFE</b> folder or <b>.SAFE.zip</b> (marked <span class='fp-badge'>Sentinel product</span>).";
    } else if (fp.mode === "project") {
      ok.textContent = "Open project"; ok.disabled = !isProj;
      $("#fp-hint").innerHTML = isProj ? `Open <b>${esc(t.split(/[\\/]/).pop())}</b>` : "Choose a folder marked <span class='fp-badge'>project</span>.";
    } else {
      ok.textContent = fp.okLabel || "Select this folder"; ok.disabled = false;
      $("#fp-hint").innerHTML = `<code>${esc(t)}</code>${!fp.sel && fp.info && !fp.info.writable ? ' <span style="color:var(--warn)">(read-only)</span>' : ""}`;
    }
  }
  $("#fp-path").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); fpLoad($("#fp-path").value); } });
  $("#fp-ok").onclick = () => { fp.result = fpTarget(); prefs.set("fp-last", fp.mode === "project" ? fp.path : fp.result); $("#dlg-folder").close(); };
  $("#fp-mkdir").onclick = async () => {
    const name = $("#fp-newname").value.trim();
    if (!name) return toast("Type a name for the new folder", true);
    try { const r = await api("/api/fs/mkdir", { method: "POST", json: { parent: fp.path, name } }); $("#fp-newname").value = ""; await fpLoad(fp.path); fp.sel = r.path; $(`#fp-list [data-p="${CSS.escape(r.path)}"]`)?.classList.add("sel"); fpHint(); }
    catch (e) { toast(e.message, true); }
  };
  $("#fp-newname").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#fp-mkdir").click(); } });
