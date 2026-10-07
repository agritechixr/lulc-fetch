  // File ▸ Export: the map as a picture (PNG) or a print layout (title, legend, scale bar, north arrow, date, credits) as a
  // PNG or printed / saved as PDF. A 2D map is drawn again into a picture (basemap tiles, rasters, vectors) at the
  // resolution asked; a 3D map is a snapshot of its view.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ export the view
  const loadImg = (src, cors) => new Promise((ok) => {
    const im = new Image();
    if (cors) im.crossOrigin = "anonymous";   // basemap tiles: without it the picture couldn't be saved
    const t = setTimeout(() => ok(null), 10000);
    im.onload = () => { clearTimeout(t); ok(im); };
    im.onerror = () => { clearTimeout(t); ok(null); };
    im.src = src;
  });
  // the 2D map into a W × H canvas: what the map shows, centred the same, at zoom Z (more pixels: a higher zoom)
  async function draw2d(W, H, { fit = true } = {}) {
    const size = map.getSize(), c = map.getCenter();
    const Z = map.getZoom() + Math.log2(fit ? Math.min(W / size.x, H / size.y) : W / size.x);
    const origin = map.project(c, Z).subtract([W / 2, H / 2]);
    const cv = Object.assign(document.createElement("canvas"), { width: Math.round(W), height: Math.round(H) }), ctx = cv.getContext("2d");
    ctx.fillStyle = getComputedStyle($("#map")).backgroundColor || "#fff";
    ctx.fillRect(0, 0, W, H);
    const P = (ll) => map.project(ll, Z).subtract(origin);
    // basemap (and place labels) tiles
    const tileSets = [basemap, map.hasLayer(placeLabels) ? placeLabels : null].filter(Boolean);
    for (const tl of tileSets) {
      const tz = Math.max(0, Math.min(tl.options.maxZoom || 19, Math.round(Z))), s = 2 ** (Z - tz), ts = 256 * s, n = 2 ** tz;
      const jobs = [];
      for (let ty = Math.floor(origin.y / ts); ty <= Math.floor((origin.y + H) / ts); ty++) {
        if (ty < 0 || ty >= n) continue;
        for (let tx = Math.floor(origin.x / ts); tx <= Math.floor((origin.x + W) / ts); tx++) {
          const url = L.Util.template(tl._url, { s: "abc"[Math.abs(tx + ty) % 3], z: tz, x: ((tx % n) + n) % n, y: ty, r: "" });
          jobs.push(loadImg(url, true).then((im) => im && [im, tx * ts - origin.x, ty * ts - origin.y]));
        }
      }
      (await Promise.all(jobs)).filter(Boolean).forEach(([im, x, y]) => ctx.drawImage(im, x, y, ts + 0.5, ts + 0.5));
      if (tl === basemap) await drawLayers2d(ctx, P);   // the layers go between the basemap and its labels
    }
    if (!tileSets.includes(basemap)) await drawLayers2d(ctx, P);
    return { canvas: cv, Z, center: c };
  }
  async function drawLayers2d(ctx, P) {
    for (const l of layers.slice().reverse().filter((x) => x.visible)) {   // bottom first
      if ((l.type === "raster" && l.image) || (l.type === "image" && l.url)) {
        const im = await loadImg(l.image || l.url, false), b = l.bounds;
        if (!im || !b) continue;
        const a = P(L.latLng(b[1][0], b[0][1])), z = P(L.latLng(b[0][0], b[1][1]));
        ctx.globalAlpha = l.opacity ?? 1;
        ctx.imageSmoothingEnabled = false;   // keep the pixels sharp, as on the map
        ctx.drawImage(im, a.x, a.y, z.x - a.x, z.y - a.y);
        ctx.imageSmoothingEnabled = true;
        ctx.globalAlpha = 1;
      } else if (l.type === "vector") drawVector2d(ctx, P, l);
    }
  }
  function drawVector2d(ctx, P, l) {
    const ring = (coords) => coords.forEach(([x, y], i) => { const p = P(L.latLng(y, x)); i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y); });
    (l.geojson?.features || []).forEach((f) => {
      const st = vecStyle(l, f), g = f.geometry;
      if (!g) return;
      ctx.strokeStyle = st.color; ctx.lineWidth = st.weight; ctx.globalAlpha = st.opacity ?? 1;
      ctx.setLineDash(st.dashArray ? String(st.dashArray).split(/[ ,]+/).map(Number) : []);
      const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
      const lines = g.type === "LineString" ? [g.coordinates] : g.type === "MultiLineString" ? g.coordinates : [];
      const pts = g.type === "Point" ? [g.coordinates] : g.type === "MultiPoint" ? g.coordinates : [];
      polys.forEach((rings) => {
        ctx.beginPath(); rings.forEach((r) => { ring(r); ctx.closePath(); });
        ctx.globalAlpha = st.fillOpacity; ctx.fillStyle = st.fillColor; ctx.fill("evenodd");
        ctx.globalAlpha = st.opacity ?? 1; ctx.stroke();
      });
      lines.forEach((c) => { ctx.beginPath(); ring(c); ctx.stroke(); });
      pts.forEach(([x, y]) => {
        const p = P(L.latLng(y, x));
        ctx.beginPath(); ctx.arc(p.x, p.y, 6, 0, 2 * Math.PI);
        ctx.globalAlpha = 0.85 * (l.opacity ?? 1); ctx.fillStyle = st.fillColor; ctx.fill();
        ctx.globalAlpha = l.opacity ?? 1; ctx.stroke();
      });
    });
    ctx.globalAlpha = 1; ctx.setLineDash([]);
  }
  // a 3D map: its view as it is now (drawn once more, then copied at once)
  function draw3d() {
    render3d();
    const src = v3.renderer.domElement, cv = Object.assign(document.createElement("canvas"), { width: src.width, height: src.height });
    cv.getContext("2d").drawImage(src, 0, 0);
    return { canvas: cv };
  }
  const saveCanvas = (cv, name) => new Promise((ok) => cv.toBlob((b) => { download(URL.createObjectURL(b), name); ok(); }, "image/png"));
  const fileName = (s) => (s || "map").replace(/[<>:"/\\|?*]+/g, "_").trim() || "map";

  // File ▸ Export ▸ Picture: twice the screen's pixels
  async function exportPicture() {
    const name = `${fileName(activeDoc()?.name)}.png`;
    try {
      status("Making the picture…", true);
      const { canvas } = is3D() ? draw3d() : await draw2d(map.getSize().x * 2, map.getSize().y * 2, { fit: false });
      await saveCanvas(canvas, name);
      toast(`Saved the map as ${name} (${canvas.width} × ${canvas.height})`);
    } catch (e) { toast(`Couldn't make the picture: ${e.message}`, true); }
    finally { status("Ready"); }
  }

  // ---- the print layout
  const PAPER = { A4: [297, 210], A3: [420, 297], Letter: [279.4, 215.9] };
  const pr = { busy: 0 };
  function openPrint() {
    if (!$("#pr-title").value) $("#pr-title").value = activeDoc()?.name || "Map";
    $("#dlg-print").showModal();
    makeLayout(true);
  }
  // nice scale-bar length: 1, 2 or 5 × 10^n metres that fits in `max` metres
  const niceLen = (max) => { const p = 10 ** Math.floor(Math.log10(max)); return [5, 2, 1].map((k) => k * p).find((v) => v <= max) || p; };
  async function makeLayout(preview) {
    const seq = ++pr.busy;
    const [pw, ph] = PAPER[$("#pr-paper").value], land = $("#pr-orient").value === "landscape";
    const dpi = preview ? 72 : +$("#pr-dpi").value, mm = dpi / 25.4;
    const W = Math.round((land ? pw : ph) * mm), H = Math.round((land ? ph : pw) * mm), m = 10 * mm;
    const opts = { legend: $("#pr-legend").checked, scale: $("#pr-scale").checked, north: $("#pr-north").checked, date: $("#pr-date").checked };
    const cv = Object.assign(document.createElement("canvas"), { width: W, height: H }), ctx = cv.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, W, H);
    const font = (px, w = 400) => `${w} ${Math.round(px)}px -apple-system, "Segoe UI", Roboto, Arial, sans-serif`;
    // title and subtitle
    const title = $("#pr-title").value.trim(), sub = $("#pr-sub").value.trim();
    ctx.fillStyle = "#111827"; ctx.textBaseline = "top";
    ctx.font = font(7 * mm, 700); ctx.fillText(title, m, m);
    let top = m + (title ? 9 * mm : 0);
    if (sub) { ctx.font = font(4 * mm); ctx.fillStyle = "#4b5563"; ctx.fillText(sub, m, top); top += 6 * mm; }
    top += 2 * mm;
    const legW = opts.legend ? 62 * mm : 0, foot = 7 * mm;
    const fx = m, fy = top, fw = W - 2 * m - (legW ? legW + 5 * mm : 0), fh = H - top - m - foot;
    // the map
    let mapInfo;
    if (is3D()) {
      mapInfo = draw3d();
      const im = mapInfo.canvas, k = Math.max(fw / im.width, fh / im.height);   // fill the frame, cut the edges
      ctx.save(); ctx.beginPath(); ctx.rect(fx, fy, fw, fh); ctx.clip();
      ctx.drawImage(im, fx + (fw - im.width * k) / 2, fy + (fh - im.height * k) / 2, im.width * k, im.height * k);
      ctx.restore();
    } else {
      mapInfo = await draw2d(fw, fh);
      if (seq !== pr.busy) return;
      ctx.drawImage(mapInfo.canvas, fx, fy);
    }
    ctx.strokeStyle = "#374151"; ctx.lineWidth = Math.max(1, 0.3 * mm); ctx.strokeRect(fx, fy, fw, fh);
    // north arrow (in 3D it turns with the view)
    if (opts.north) {
      let ang = 0;
      if (is3D()) { const d = v3.controls.target.clone().sub(v3.camera.position); ang = -Math.atan2(d.x, d.y); }
      const cx = fx + fw - 9 * mm, cy = fy + 11 * mm, r = 6 * mm;
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(ang);
      ctx.fillStyle = "rgba(255,255,255,.85)"; ctx.beginPath(); ctx.arc(0, 0, r * 1.25, 0, 2 * Math.PI); ctx.fill();
      ctx.fillStyle = "#111827"; ctx.beginPath(); ctx.moveTo(0, -r); ctx.lineTo(r * 0.45, r * 0.6); ctx.lineTo(0, r * 0.25); ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#9ca3af"; ctx.beginPath(); ctx.moveTo(0, -r); ctx.lineTo(-r * 0.45, r * 0.6); ctx.lineTo(0, r * 0.25); ctx.closePath(); ctx.fill();
      ctx.rotate(-ang); ctx.fillStyle = "#111827"; ctx.font = font(3.6 * mm, 700); ctx.textAlign = "center"; ctx.textBaseline = "bottom";
      ctx.fillText("N", Math.sin(ang) * r * 1.05, -Math.cos(ang) * r * 1.05 - 0.6 * mm);
      ctx.restore(); ctx.textAlign = "left"; ctx.textBaseline = "top";
    }
    // scale bar (2D only: a 3D view has no single scale)
    if (opts.scale && !is3D()) {
      const mpp = 40075016.686 * Math.cos(mapInfo.center.lat * Math.PI / 180) / (256 * 2 ** mapInfo.Z), len = niceLen(fw * 0.28 * mpp), px = len / mpp;
      const bx = fx + 5 * mm, by = fy + fh - 9 * mm, bh = 1.6 * mm;
      ctx.fillStyle = "rgba(255,255,255,.85)"; ctx.fillRect(bx - 2 * mm, by - 5.5 * mm, px + 4 * mm + 14 * mm, 9.5 * mm);
      for (let i = 0; i < 4; i++) { ctx.fillStyle = i % 2 ? "#fff" : "#111827"; ctx.fillRect(bx + px * i / 4, by, px / 4, bh); }
      ctx.strokeStyle = "#111827"; ctx.lineWidth = Math.max(1, 0.2 * mm); ctx.strokeRect(bx, by, px, bh);
      ctx.fillStyle = "#111827"; ctx.font = font(3 * mm); ctx.textBaseline = "bottom";
      ctx.fillText("0", bx - 0.8 * mm, by - 0.6 * mm);
      ctx.fillText(len >= 1000 ? `${len / 1000} km` : `${len} m`, bx + px - 2 * mm, by - 0.6 * mm);
      ctx.textBaseline = "top";
    }
    // legend: every visible layer, top first
    if (legW) {
      const lx = W - m - legW, ly = fy;
      ctx.fillStyle = "#111827"; ctx.font = font(4.2 * mm, 700); ctx.fillText("Legend", lx, ly);
      let y = ly + 8 * mm;
      const sw = 6 * mm;
      for (const l of layers.filter((x) => x.visible)) {
        if (y > fy + fh - 6 * mm) break;
        const g = l.legend;
        ctx.font = font(3.3 * mm, 600); ctx.fillStyle = "#111827";
        const name = l.name.length > 34 ? `${l.name.slice(0, 33)}…` : l.name;
        if (l.type === "vector") {
          const c = l.color || "#2563eb", isPoly = l.geojson?.features?.some((f) => /Polygon/.test(f.geometry?.type)), isPt = l.geojson?.features?.every((f) => /Point/.test(f.geometry?.type));
          ctx.strokeStyle = c; ctx.fillStyle = c; ctx.lineWidth = Math.max(1, 0.5 * mm);
          if (isPt) { ctx.beginPath(); ctx.arc(lx + sw / 2, y + 2 * mm, 1.6 * mm, 0, 2 * Math.PI); ctx.fill(); }
          else if (isPoly) { ctx.globalAlpha = 0.25; ctx.fillRect(lx, y, sw, 4 * mm); ctx.globalAlpha = 1; ctx.strokeRect(lx, y, sw, 4 * mm); }
          else { ctx.beginPath(); ctx.moveTo(lx, y + 2 * mm); ctx.lineTo(lx + sw, y + 2 * mm); ctx.stroke(); }
          ctx.fillStyle = "#111827"; ctx.fillText(name, lx + sw + 2.5 * mm, y);
          y += 7 * mm;
        } else if (g?.kind === "continuous") {
          ctx.fillText(name, lx, y); y += 5 * mm;
          const grd = ctx.createLinearGradient(lx, 0, lx + legW - 4 * mm, 0);
          g.colors.forEach((c, i) => grd.addColorStop(i / (g.colors.length - 1), c));
          ctx.fillStyle = grd; ctx.fillRect(lx, y, legW - 4 * mm, 3.5 * mm);
          ctx.fillStyle = "#4b5563"; ctx.font = font(2.8 * mm); y += 4.5 * mm;
          ctx.fillText(fmtv(g.vmin), lx, y); ctx.textAlign = "right"; ctx.fillText(fmtv(g.vmax), lx + legW - 4 * mm, y); ctx.textAlign = "left";
          y += 6 * mm;
        } else if (g?.kind === "classes") {
          ctx.fillText(name, lx, y); y += 5.5 * mm;
          ctx.font = font(2.9 * mm);
          for (const c of g.classes.slice(0, 10)) {
            ctx.fillStyle = c.color; ctx.fillRect(lx, y, 4 * mm, 3.2 * mm);
            ctx.fillStyle = "#111827"; ctx.fillText(String(c.name).slice(0, 36), lx + 6 * mm, y - 0.2 * mm);
            y += 4.4 * mm;
          }
          y += 2.5 * mm;
        } else {
          ctx.fillStyle = "#9ca3af"; ctx.fillRect(lx, y, sw, 4 * mm);
          ctx.fillStyle = "#111827"; ctx.fillText(name, lx + sw + 2.5 * mm, y);
          y += 7 * mm;
        }
      }
    }
    // the foot: date, credits
    ctx.fillStyle = "#6b7280"; ctx.font = font(2.8 * mm); ctx.textBaseline = "bottom";
    const credit = (basemap?.options.attribution || "").replace(/<[^>]+>/g, "").replace(/&copy;/g, "©");
    ctx.fillText(`${opts.date ? `${new Date().toLocaleDateString([], { dateStyle: "long" })} · ` : ""}Made with LULC Fetch${credit ? ` · Basemap ${credit}` : ""}`, m, H - m + 1 * mm);
    ctx.textBaseline = "top";
    if (seq !== pr.busy) return;
    if (preview) { const img = $("#pr-preview"); img.src = cv.toDataURL("image/png"); }
    return cv;
  }
  ["#pr-title", "#pr-sub"].forEach((s) => { let t = 0; $(s).addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => makeLayout(true), 350); }); });
  ["#pr-paper", "#pr-orient", "#pr-legend", "#pr-scale", "#pr-north", "#pr-date"].forEach((s) => $(s).addEventListener("change", () => makeLayout(true)));
  $("#pr-png").onclick = (e) => busy(e.currentTarget, "Making…", async () => {
    const cv = await makeLayout(false);
    if (cv) { await saveCanvas(cv, `${fileName($("#pr-title").value)} layout.png`); toast(`Layout saved (${cv.width} × ${cv.height})`); }
  });
  // Print / Save as PDF: the layout in a hidden frame, sized to the paper, and the system's print dialog
  $("#pr-print").onclick = (e) => busy(e.currentTarget, "Preparing…", async () => {
    const cv = await makeLayout(false);
    if (!cv) return;
    const land = $("#pr-orient").value === "landscape", paper = $("#pr-paper").value;
    const f = document.createElement("iframe");
    f.style.cssText = "position:fixed;width:0;height:0;border:0;right:0;bottom:0";
    document.body.append(f);
    const d = f.contentDocument;
    d.open();
    d.write(`<!doctype html><title>${esc($("#pr-title").value)}</title><style>@page{size:${paper} ${land ? "landscape" : "portrait"};margin:0}html,body{margin:0}img{width:100%;height:100vh;object-fit:contain;display:block}</style><img src="${cv.toDataURL("image/png")}">`);
    d.close();
    await new Promise((ok) => { const im = d.querySelector("img"); im.complete ? ok() : im.onload = ok; });
    f.contentWindow.focus();
    f.contentWindow.print();
    setTimeout(() => f.remove(), 60000);
  });
