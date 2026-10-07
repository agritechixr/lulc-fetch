  // The YOLO & SAM add-on (ultralytics), on top of the PyTorch add-on.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ YOLO & SAM add-on (ultralytics), on top of the PyTorch add-on
  function renderYoloAddon(box, why, onReady) {
    const st = dlx.status, ys = st?.yolo || {};
    box.classList.toggle("hidden", !!ys.available);
    if (ys.available) { box.innerHTML = ""; return true; }
    box.innerHTML = `<div class="card dl-addon"><h2>YOLO &amp; SAM add-on needed</h2>
      <p class="hint" style="margin-top:0">${esc(why)} uses <b>ultralytics</b> (YOLO26, YOLO11 and SAM 2.1), free and open source under the
      <b>AGPL-3.0</b> licence (free to use; sharing a modified version requires sharing its source). It is installed once, from the internet
      (about ${fmt((st?.size_mb?.yolo || 150) / 1000, 2)} GB), on top of the deep-learning add-on; LULC Fetch doesn't ship it.</p>
      <div class="pkgs">pip install ultralytics</div>
      <button class="btn primary big-btn" data-yolo-install>Install the YOLO &amp; SAM add-on</button></div>`;
    $("[data-yolo-install]", box).onclick = async (e) => {
      const btn = e.currentTarget; btn.disabled = true;
      try {
        const job = await api("/api/dl/install", { method: "POST", json: { variant: "yolo" } });
        await trackJob(job, { title: "Installing the YOLO & SAM add-on" });
        await dlStatus(true);
        toast("YOLO & SAM add-on installed");
        onReady?.();
      } catch (err) { if (notCancelled(err)) toast(err, true); }
      finally { btn.disabled = false; }
    };
    return false;
  }

  // red / green / blue band pickers for one image (Detect object, Train detection model)
  function rgbDefaults(l) {
    const info = l.info || {}, bm = info.band_map || {}, n = info.count || 1;
    if (info.rgb) return [1, 2, 3];
    if (["B04", "B03", "B02"].every((b) => b in bm)) return ["B04", "B03", "B02"].map((b) => bm[b]);
    if (["red", "green", "blue"].every((b) => b in bm)) return ["red", "green", "blue"].map((b) => bm[b]);
    if (Array.isArray(l.render?.rgb) && l.render.rgb.every((b) => typeof b === "number")) return l.render.rgb;
    return n >= 3 ? [1, 2, 3] : [1, 1, 1];
  }
  function renderRgbPickers(el, l, onChange) {
    const b = rgbDefaults(l), bands = l.info?.bands || [];
    const all = Array.from({ length: l.info?.count || 1 }, (_, i) => `<option value="${i + 1}">${i + 1} · ${esc(bands[i]?.description || `band ${i + 1}`)}</option>`).join("");
    el.innerHTML = ["Red", "Green", "Blue"].map((c, k) => `<label>${c}<select data-rgb="${k}">${all}</select></label>`).join("");
    $$("[data-rgb]", el).forEach((s, k) => { s.value = b[k]; s.onchange = onChange; });
  }
  const rgbChosen = (el) => $$("[data-rgb]", el).map((s) => +s.value);
  const rgbBody = (bands) => new Set(bands).size === 1 ? [bands[0]] : bands;
  const defaultStretch = (l) => l.info?.rgb || l.info?.dtype === "uint8" ? "byte" : "percent";
