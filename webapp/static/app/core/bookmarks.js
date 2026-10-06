  // Bookmarks (Insert ▸ Bookmarks): save the map's current extent under a name, go back to it later, rename or remove it.
  // Each bookmark is a tile in the ribbon: a small picture of the place (basemap tiles) with the extent outlined, and its name.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ bookmarks (per browser, in prefs)
  const BM_W = 96, BM_H = 56;   // thumbnail size (px)
  const bookmarks = () => prefs.get("bookmarks", []);
  const saveBookmarks = (list) => prefs.set("bookmarks", list);

  function addBookmark() {
    const b = map.getBounds(), list = bookmarks();
    let n = list.length + 1;
    while (list.some((x) => x.name === `Bookmark ${n}`)) n++;
    const bm = { id: Date.now().toString(36), name: `Bookmark ${n}`, created: Date.now(),
      bounds: [[+b.getSouth().toFixed(6), +b.getWest().toFixed(6)], [+b.getNorth().toFixed(6), +b.getEast().toFixed(6)]],
      basemap: prefs.get("basemap", "streets") };
    saveBookmarks([...list, bm]);
    return bm;
  }
  function goToBookmark(id) {
    const bm = bookmarks().find((x) => x.id === id);
    if (bm) map.fitBounds(bm.bounds);
  }
  function renameBookmark(id, name) {
    name = name.trim();
    if (name) saveBookmarks(bookmarks().map((x) => x.id === id ? { ...x, name } : x));
  }
  function removeBookmark(id) { saveBookmarks(bookmarks().filter((x) => x.id !== id)); }

  // the thumbnail: the basemap tiles under the extent at the zoom where it fits, the extent drawn on top
  function bookmarkThumb(bm) {
    const tiles = BASEMAPS[bm.basemap] || BASEMAPS.streets;
    const sw = L.latLng(bm.bounds[0]), ne = L.latLng(bm.bounds[1]);
    const a0 = map.project(sw, 0), b0 = map.project(ne, 0);
    const fit = Math.min((BM_W - 12) / Math.max(Math.abs(b0.x - a0.x), 1e-9), (BM_H - 12) / Math.max(Math.abs(a0.y - b0.y), 1e-9));
    const z = Math.max(0, Math.min(tiles.options.maxZoom || 18, Math.floor(Math.log2(fit))));
    const a = map.project(sw, z), b = map.project(ne, z), c = a.add(b).divideBy(2);
    const ox = c.x - BM_W / 2, oy = c.y - BM_H / 2, n = 2 ** z;
    let imgs = "";
    for (let ty = Math.floor(oy / 256); ty <= Math.floor((oy + BM_H) / 256); ty++) {
      if (ty < 0 || ty >= n) continue;
      for (let tx = Math.floor(ox / 256); tx <= Math.floor((ox + BM_W) / 256); tx++) {
        const url = L.Util.template(tiles._url, { s: "abc"[(tx + ty) % 3], z, x: ((tx % n) + n) % n, y: ty, r: "" });
        imgs += `<img src="${esc(url)}" alt="" loading="lazy" draggable="false" style="left:${Math.round(tx * 256 - ox)}px;top:${Math.round(ty * 256 - oy)}px">`;
      }
    }
    const box = `left:${Math.round(a.x - ox)}px;top:${Math.round(b.y - oy)}px;width:${Math.max(4, Math.round(b.x - a.x))}px;height:${Math.max(4, Math.round(a.y - b.y))}px`;
    return `<span class="bm-thumb">${imgs}<i class="bm-box" style="${box}"></i></span>`;
  }

  function renderBookmarks(editId = null) {
    const box = $("#bookmarks-menu"), list = bookmarks();
    box.innerHTML = `<div class="rb-group"><div class="rb-items bm-grid"><button data-bmadd class="rb-big" title="Bookmark the map's current view (Ctrl+B)"><span class="ic">${svg("bookmark")}</span>Bookmark this view</button>` +
      (list.length ? list.map((bm) => `<div class="bm-item" data-bm="${esc(bm.id)}" title="${esc(bm.name)}: click to go back to this view">
          ${bookmarkThumb(bm)}<span class="bm-name">${esc(bm.name)}</span>
          <span class="bm-acts"><button type="button" data-bmren title="Rename" aria-label="Rename">✎</button><button type="button" data-bmdel title="Remove" aria-label="Remove">×</button></span></div>`).join("")
        : `<div class="hist-empty">Zoom the map to a place, then bookmark it to come back to it later.</div>`) +
      `</div><div class="rb-cap">Bookmarks</div></div>`;
    $("[data-bmadd]", box).onclick = (e) => { e.stopPropagation(); renderBookmarks(addBookmark().id); };
    $$("[data-bm]", box).forEach((el) => {
      const id = el.dataset.bm;
      el.onclick = () => { if (!el.classList.contains("editing")) { goToBookmark(id); toggleMenu(null); } };
      $("[data-bmren]", el).onclick = (e) => { e.stopPropagation(); editBookmarkName(el); };
      $("[data-bmdel]", el).onclick = (e) => {
        e.stopPropagation();
        const bm = bookmarks().find((x) => x.id === id);
        if (bm && confirm(`Remove the bookmark “${bm.name}”?`)) { removeBookmark(id); renderBookmarks(); }
      };
      el.ondblclick = (e) => { e.stopPropagation(); editBookmarkName(el); };
    });
    if (editId) { const el = $(`[data-bm="${CSS.escape(editId)}"]`, box); if (el) { editBookmarkName(el); el.scrollIntoView({ block: "nearest", inline: "nearest" }); } }
  }
  // rename in place: Enter (or clicking away) keeps the name, Esc leaves it as it was
  function editBookmarkName(el) {
    if (el.classList.contains("editing")) return;
    const id = el.dataset.bm, label = $(".bm-name", el), bm = bookmarks().find((x) => x.id === id);
    if (!bm) return;
    el.classList.add("editing");
    label.innerHTML = `<input type="text" maxlength="80" aria-label="Bookmark name" value="${esc(bm.name)}">`;
    const inp = $("input", label);
    let done = false;
    const finish = (keep) => { if (done) return; done = true; if (keep) renameBookmark(id, inp.value); renderBookmarks(); };
    inp.onclick = (e) => e.stopPropagation();
    inp.onkeydown = (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    };
    inp.onblur = () => finish(true);
    inp.focus(); inp.select();
  }
  // Ctrl/⌘ B: bookmark the view and open the menu with its name ready to type
  function bookmarkView() {
    const m = $("#bookmarks-menu").closest(".menu");
    const bm = addBookmark();
    toggleMenu(m);
    renderBookmarks(bm.id);
  }
