  // View ▸ Compare ▸ Time slider: step or play through layers one at a time (e.g. NDVI by month, SAR dates), in the order
  // of the dates in their names (else of Contents), and save them as an animated GIF or MP4. Uses the layers selected in
  // Contents (two or more), else every layer whose name has a date. Closing it puts the layers' visibility back.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  const tsl = { on: false, frames: [], i: 0, timer: 0, before: null };
  // a date in a layer's name: 2026-03-15, 20260315, 2026_03, 2026-03, or a year
  function layerDate(l) {
    const s = `${l.name} ${l.path || ""}`;
    let m = s.match(/(19|20)(\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)/);
    if (m) return { key: `${m[1]}${m[2]}-${m[3]}-${m[4]}`, label: `${m[1]}${m[2]}-${m[3]}-${m[4]}` };
    m = s.match(/(19|20)(\d{2})[-_.](0[1-9]|1[0-2])(?!\d)/);
    if (m) return { key: `${m[1]}${m[2]}-${m[3]}-00`, label: `${m[1]}${m[2]}-${m[3]}` };
    m = s.match(/(?<!\d)(19|20)(\d{2})(?!\d)/);
    if (m) return { key: `${m[1]}${m[2]}-00-00`, label: `${m[1]}${m[2]}` };
    return null;
  }
  function startTimeSlider() {
    if (is3D()) return toast("The time slider works on 2D maps → open a 2D map tab", true);
    const usable = (l) => l.type !== "unplaced" && l.leaflet !== undefined;
    let list = selectedLayers().filter(usable);
    if (list.length < 2) list = layers.filter((l) => usable(l) && layerDate(l));
    if (list.length < 2) return toast("Select two or more layers in Contents (Ctrl / ⌘-click), or add layers with dates in their names", true);
    stopTimeSlider(true);
    const dated = list.map((l, k) => ({ l, d: layerDate(l), k: layers.length - layers.indexOf(l) }));
    const allDated = dated.every((x) => x.d);
    dated.sort((a, b) => allDated ? a.d.key.localeCompare(b.d.key) : a.k - b.k);   // by date, else bottom of Contents first
    tsl.frames = dated.map(({ l, d }) => ({ id: l.id, label: d ? `${d.label} · ${l.name}` : l.name }));
    tsl.before = new Map(dated.map(({ l }) => [l.id, l.visible]));
    tsl.on = true; tsl.i = 0;
    $("#ts-range").max = tsl.frames.length - 1;
    $("#ts").classList.remove("hidden");
    showFrame(0);
    refreshRibbon();
    status(`Time slider: ${tsl.frames.length} layers${allDated ? ", by date" : ", in the order of Contents"} · ← → step · Space plays · Esc closes`);
  }
  // only the frame's layer shows (the others of the slider are hidden; layers not in it stay as they are)
  function showFrame(i) {
    if (!tsl.on) return;
    tsl.frames = tsl.frames.filter((f) => getLayer(f.id));
    if (tsl.frames.length < 2) return stopTimeSlider();
    tsl.i = (i + tsl.frames.length) % tsl.frames.length;
    tsl.frames.forEach((f, k) => {
      const l = getLayer(f.id);
      if (!l.leaflet) return;
      if (k === tsl.i) { if (!map.hasLayer(l.leaflet)) l.leaflet.addTo(map); }
      else if (map.hasLayer(l.leaflet)) l.leaflet.remove();
    });
    restack();
    $("#ts-range").value = tsl.i;
    $("#ts-label").textContent = tsl.frames[tsl.i].label;
    $("#ts-count").textContent = `${tsl.i + 1} / ${tsl.frames.length}`;
  }
  function playTimeSlider(on = !tsl.timer) {
    clearInterval(tsl.timer); tsl.timer = 0;
    if (on) tsl.timer = setInterval(() => { if (!$("#ts-loop").checked && tsl.i === tsl.frames.length - 1) return playTimeSlider(false); showFrame(tsl.i + 1); }, 1000 / +$("#ts-fps").value);
    $("#ts-play").textContent = tsl.timer ? "❚❚" : "▶";
    $("#ts-play").title = tsl.timer ? "Pause (Space)" : "Play (Space)";
  }
  function stopTimeSlider(silent) {
    if (!tsl.on) return;
    playTimeSlider(false);
    tsl.on = false;
    for (const [id, vis] of tsl.before || []) {
      const l = getLayer(id);
      if (l?.leaflet) { if (vis && l.visible) l.leaflet.addTo(map); else l.leaflet.remove(); }
    }
    restack();
    $("#ts").classList.add("hidden");
    refreshRibbon();
    if (!silent) status("Time slider closed");
  }
  async function saveAnimation(format) {
    const frames = tsl.frames.map((f) => ({ f, l: getLayer(f.id) })).filter(({ l }) => l && (l.image || l.url) && l.bounds);
    if (frames.length < 2) return toast("Saving an animation needs two or more raster layers (shapes and online layers can't be saved in it yet)", true);
    const skipped = tsl.frames.length - frames.length;
    const bounds = (b) => { const ll = L.latLngBounds(b); return [[ll.getSouth(), ll.getWest()], [ll.getNorth(), ll.getEast()]]; };
    try {
      const job = await api("/api/view/animation", { method: "POST", json: { format, fps: +$("#ts-fps").value, width: 900, name: "time_slider",
        frames: frames.map(({ f, l }) => ({ image: l.image || l.url, bounds: bounds(l.bounds), label: $("#ts-labels").checked ? f.label : "" })) } });
      const r = (await trackJob(job, { title: job.title })).result;
      const a = document.createElement("a");
      a.href = r.url; a.download = r.file; document.body.appendChild(a); a.click(); a.remove();
      toast(`Saved ${r.file}: ${r.frames} frames, ${r.size[0]} × ${r.size[1]} px, ${r.mb} MB${skipped ? ` (${skipped} non-raster layers left out)` : ""}`);
    } catch (e) { toast(e, true); }
  }
  $("#ts-range").oninput = (e) => showFrame(+e.target.value);
  $("#ts-prev").onclick = () => showFrame(tsl.i - 1);
  $("#ts-next").onclick = () => showFrame(tsl.i + 1);
  $("#ts-play").onclick = () => playTimeSlider();
  $("#ts-fps").onchange = () => { if (tsl.timer) playTimeSlider(true); };
  $("#ts-x").onclick = () => stopTimeSlider();
  $("#ts-save").onclick = (e) => { const r = e.currentTarget.getBoundingClientRect(); showMenu("Save the animation", [["Animated GIF", () => saveAnimation("gif")], ["MP4 video", () => saveAnimation("mp4")]], r.left, r.top - 90); };
  L.DomEvent.disableClickPropagation($("#ts"));
  L.DomEvent.disableScrollPropagation($("#ts"));
  document.addEventListener("keydown", (e) => {
    if (!tsl.on || $("dialog[open]") || document.activeElement?.matches?.("input:not([type=range]), select, textarea, [contenteditable=true]")) return;
    if (e.key === "Escape") stopTimeSlider();
    else if (e.key === "ArrowRight") { e.preventDefault(); showFrame(tsl.i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); showFrame(tsl.i - 1); }
    else if (e.key === " ") { e.preventDefault(); playTimeSlider(); }
  });
  // Contents changed: a removed layer leaves the slider
  function timeSliderChanged() { if (tsl.on) showFrame(Math.min(tsl.i, tsl.frames.length - 1)); }
