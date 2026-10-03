  // Resizable panels: Contents, the tool panel and the data viewer.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ panel sizes: drag the edges of Contents, Tools and the data viewer
  const LAYOUT = { "w-contents": 290, "w-tools": 400, "h-viewer": 300 };
  const gisEl = $("#gis"), centerEl = $("#center");
  function applySizes() {
    gisEl.style.setProperty("--contents-w", prefs.get("w-contents", LAYOUT["w-contents"]) + "px");
    gisEl.style.setProperty("--tools-w", prefs.get("w-tools", LAYOUT["w-tools"]) + "px");
    centerEl.style.setProperty("--viewer-h", prefs.get("h-viewer", LAYOUT["h-viewer"]) + "px");
  }
  let sizeRaf = 0;
  const mapResized = () => { cancelAnimationFrame(sizeRaf); sizeRaf = requestAnimationFrame(() => map.invalidateSize({ pan: false })); };
  function makeResizer(handle, { axis, key, compute, min, max }) {
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      document.body.classList.add(axis === "x" ? "resizing-x" : "resizing-y");
      handle.classList.add("active");
      if (key === "h-viewer") $("#viewer").classList.remove("maxed");
      const move = (ev) => { prefs.set(key, Math.round(Math.min(max(), Math.max(min, compute(ev))))); applySizes(); mapResized(); };
      const up = () => {
        handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); handle.removeEventListener("pointercancel", up);
        document.body.classList.remove("resizing-x", "resizing-y"); handle.classList.remove("active"); mapResized();
      };
      handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up); handle.addEventListener("pointercancel", up);
    });
    handle.addEventListener("dblclick", () => { prefs.set(key, LAYOUT[key]); $("#viewer").classList.remove("maxed"); applySizes(); mapResized(); });
  }
  makeResizer($("#rz-contents"), { axis: "x", key: "w-contents", min: 200, max: () => Math.min(720, innerWidth * 0.45),
    compute: (e) => e.clientX - gisEl.getBoundingClientRect().left });
  makeResizer($("#rz-tools"), { axis: "x", key: "w-tools", min: 300, max: () => Math.min(1100, innerWidth * 0.6),
    compute: (e) => gisEl.getBoundingClientRect().right - e.clientX });
  makeResizer($("#rz-viewer"), { axis: "y", key: "h-viewer", min: 120, max: () => centerEl.getBoundingClientRect().height - 70,
    compute: (e) => centerEl.getBoundingClientRect().bottom - e.clientY });
  function resetLayout() {
    Object.entries(LAYOUT).forEach(([k, v]) => prefs.set(k, v));
    $("#viewer").classList.remove("maxed");
    applySizes(); mapResized();
  }
  $("#viewer-max").onclick = () => { $("#viewer").classList.toggle("maxed"); mapResized(); };
  applySizes();
