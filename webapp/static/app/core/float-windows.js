  // Floating windows over the map: drag by the title bar, resize from the edges (run details, History).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ floating windows: drag by the title bar, resize from the
  // corner, position and size remembered, always (partly) on screen
  function floatWin(el, head, key, def) {
    const place = (x, y) => {
      x = Math.min(Math.max(x, 8 - el.offsetWidth + 120), innerWidth - 120);
      y = Math.min(Math.max(y, 8), innerHeight - 40);
      el.style.left = `${x}px`; el.style.top = `${y}px`;
    };
    const save = () => { if (!el.classList.contains("hidden")) prefs.set(key, { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight }); };
    head.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button, input, select")) return;
      e.preventDefault();
      const dx = e.clientX - el.offsetLeft, dy = e.clientY - el.offsetTop;
      head.setPointerCapture(e.pointerId);
      const move = (ev) => place(ev.clientX - dx, ev.clientY - dy);
      const up = () => { head.removeEventListener("pointermove", move); head.removeEventListener("pointerup", up); save(); };
      head.addEventListener("pointermove", move);
      head.addEventListener("pointerup", up);
    });
    new ResizeObserver(save).observe(el);
    addEventListener("resize", () => { if (!el.classList.contains("hidden")) place(el.offsetLeft, el.offsetTop); });
    return {
      show() {
        if (!el.classList.contains("hidden")) return;
        el.classList.remove("hidden");
        const box = prefs.get(key, null), d = def();
        el.style.width = `${box?.w || d.w}px`; el.style.height = `${box?.h || d.h}px`;
        place(box ? box.x : d.x, box ? box.y : d.y);
      },
      hide() { el.classList.add("hidden"); },
      get open() { return !el.classList.contains("hidden"); },
    };
  }
