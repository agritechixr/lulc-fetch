  // The quick access bar, as in Word: Save, Save as, Undo, Redo, plus any ribbon command or tool the user adds. Right-click
  // a ribbon button (or a tool in Analysis) to add it here, remove it, or see its keyboard shortcut; right-click one of
  // the added buttons to move or remove it.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ quick access bar & keyboard shortcuts
  const SHORTCUTS = { "add-data": "Ctrl+O", "export-layer": "Ctrl+E", "remove-layer": "Delete", "toggle-contents": "Ctrl+1", "toggle-tools": "Ctrl+2",
    "toggle-viewer": "Ctrl+3", "bookmark-view": "Ctrl+B", "toggle-ribbon": "Ctrl+F1", save: "Ctrl+S", "save-as": "Ctrl+Shift+S", undo: "Ctrl+Z",
    redo: "Ctrl+Y", "layer-copy": "Ctrl+C", "layer-paste": "Ctrl+V" };
  const qa = { extra: prefs.get("qat-extra", []) };   // [{ key: "cmd:<command>" | "tool:<id>", label }]
  // what a ribbon element is: a command (data-cmd) or a tool (data-tool)
  function qaItemOf(el) {
    if (el.dataset.cmd) return { key: `cmd:${el.dataset.cmd}`, label: el.textContent.replace(/\s+/g, " ").trim() || el.title };
    const t = TOOLS.find((x) => x.id === el.dataset.tool);
    return t ? { key: `tool:${t.id}`, label: t.title } : null;
  }
  const qaShortcut = (key) => key.startsWith("cmd:") ? SHORTCUTS[key.slice(4)] || "" : "";
  function qaIcon(x) {
    if (x.key.startsWith("tool:")) { const t = TOOLS.find((y) => y.id === x.key.slice(5)); if (t) return svg(t.icon); }
    const ic = $(`#menus .menu-pop [data-cmd="${CSS.escape(x.key.slice(4))}"] .ic`)?.innerHTML;
    return ic || `<span class="qa-letter">${esc((x.label || "?").trim()[0].toUpperCase())}</span>`;
  }
  function renderQat() {
    $("#qa-extra").innerHTML = qa.extra.length ? `<span class="qa-sep"></span>` + qa.extra.map((x, i) => {
      const sc = qaShortcut(x.key), cmd = x.key.startsWith("cmd:") ? x.key.slice(4) : "";
      return `<button type="button" class="qa" data-qx="${i}" ${cmd ? `data-qcmd="${esc(cmd)}"` : ""} title="${esc(x.label)}${sc ? ` (${sc})` : ""} · right-click to move or remove" aria-label="${esc(x.label)}">${qaIcon(x)}</button>`;
    }).join("") : "";
    $$("#qa-extra [data-qx]").forEach((b) => {
      const x = qa.extra[+b.dataset.qx];
      b.onclick = (e) => {
        e.stopPropagation();
        if (x.key.startsWith("cmd:")) runCmd(x.key.slice(4)); else switchTool(x.key.slice(5));
      };
      b.oncontextmenu = (e) => {
        e.preventDefault(); e.stopPropagation();
        const i = +b.dataset.qx, sc = qaShortcut(x.key);
        showMenu(x.label, [
          ["Remove from quick access bar", () => removeQa(x.key)],
          i > 0 ? ["Move left", () => moveQa(i, -1)] : null,
          i < qa.extra.length - 1 ? ["Move right", () => moveQa(i, 1)] : null,
          "-",
          [sc ? `Shortcut: ${sc}` : "No keyboard shortcut", () => {}],
        ].filter(Boolean), e.clientX, e.clientY);
      };
    });
    syncQat();
  }
  // added commands look like their ribbon button: greyed when it can't be used, highlighted when it is on
  function syncQat() {
    $$("#qa-extra [data-qcmd]").forEach((b) => {
      const src = $(`#menus .menu-pop [data-cmd="${CSS.escape(b.dataset.qcmd)}"]`);
      b.disabled = !!src?.disabled;
      b.classList.toggle("on", !!src?.classList.contains("on"));
    });
  }
  function saveQa() { prefs.set("qat-extra", qa.extra); renderQat(); }
  function addQa(x) {
    if (qa.extra.some((y) => y.key === x.key)) return;
    if (qa.extra.length >= 12) return toast("The quick access bar is full (12) → remove one first (right-click it)", true);
    qa.extra.push(x); saveQa(); toast(`“${x.label}” added to the quick access bar`);
  }
  function removeQa(key) { qa.extra = qa.extra.filter((y) => y.key !== key); saveQa(); }
  function moveQa(i, d) { const [x] = qa.extra.splice(i, 1); qa.extra.splice(i + d, 0, x); saveQa(); }

  // right-click in the ribbon: a command or a tool (add / remove, its shortcut), or a tab (the ribbon itself)
  $("#menus").addEventListener("contextmenu", (e) => {
    // a disabled button gets no mouse events (its clicks pass through to the group): find it under the pointer
    const el = e.target.closest(".menu-pop [data-cmd], .menu-pop [data-tool]") || $$(".menu-pop [data-cmd]:disabled", e.target.closest(".menu-pop") || undefined)
      .find((b) => { const r = b.getBoundingClientRect(); return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom; });
    const tab = e.target.closest(".menu-btn");
    if (!el && !tab) return;
    e.preventDefault();
    if (tab) return showMenu("Ribbon", [[ribbon.pinned ? "Hide the ribbon (Ctrl+F1)" : "Always show the ribbon (Ctrl+F1)", () => setRibbonPinned(!ribbon.pinned)],
      ["Keyboard shortcuts…", () => showHelp("shortcuts")]], e.clientX, e.clientY);
    const x = qaItemOf(el);
    if (!x) return;
    const has = qa.extra.some((y) => y.key === x.key), sc = qaShortcut(x.key);
    showMenu(x.label, [
      [has ? "Remove from quick access bar" : "Add to quick access bar", () => has ? removeQa(x.key) : addQa(x)],
      "-",
      [sc ? `Shortcut: ${sc}` : "No keyboard shortcut", () => {}],
      ["All keyboard shortcuts…", () => showHelp("shortcuts")],
    ], e.clientX, e.clientY);
  });
  // the fixed quick access buttons tell their shortcut too
  $$(".qat > .qa").forEach((b) => b.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const sc = SHORTCUTS[b.dataset.cmd];
    showMenu(b.getAttribute("aria-label"), [[sc ? `Shortcut: ${sc}` : "No keyboard shortcut", () => {}], ["All keyboard shortcuts…", () => showHelp("shortcuts")]], e.clientX, e.clientY);
  }));
  renderQat();
