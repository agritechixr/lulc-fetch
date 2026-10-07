  // Find imagery: filters, search, results, previews and scene metadata.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ filters
  const iso = (d) => d.toISOString().slice(0, 10);
  function setRange(days) {
    const end = new Date(), start = new Date(Date.now() - days * 864e5);
    $("#start").value = iso(start);
    $("#end").value = iso(end);
  }
  $$(".presets .chip").forEach((c) => c.onclick = () => setRange(+c.dataset.days));
  $("#cloud").oninput = () => $("#cloud-val").textContent = $("#cloud").value + "%";

  // ------------------------------------------------------------------ search & results
  function clearResults() {
    state.results = null;
    removeLayer("footprints");
    removePreviews();
    $("#results").innerHTML = "";
    $("#results-info").textContent = "Choose an area and dates, then search.";
  }

  $("#btn-search").onclick = () => {
    if (!state.aoi) return toast("Set an area of interest first", true);
    const start = $("#start").value, end = $("#end").value;
    if (!start || !end || start > end) return toast("Choose a valid date range", true);
    busy($("#btn-search"), "Searching…", async () => {
      try {
        const res = await trackFetch((signal) => api("/api/search", { method: "POST", signal, json: {
          source: $("#source").value, aoi: state.aoi, start, end, max_cloud: +$("#cloud").value,
        } }), { tool: "search", title: "Searching the catalog", message: `${$("#mission").selectedOptions[0]?.text || ""}, ${start} → ${end}` });
        state.results = res;
        renderResults();
      } catch (e) { if (notCancelled(e)) toast(e, true); }
    });
  };

  $$("#sort button").forEach((b) => b.onclick = () => {
    state.sort = b.dataset.sort;
    $$("#sort button").forEach((x) => x.classList.toggle("active", x === b));
    renderResults();
  });

  const cloudClass = (c) => (c < 10 ? "c0" : c < 40 ? "c1" : "c2");
  const weekday = (d) => new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

  function highlightScene(date) {
    const fp = getLayer("footprints");
    fp?.leaflet?.eachLayer((x) => x.setStyle(date && x.feature.properties.date === date
      ? { color: "#4f46e5", weight: 3, opacity: 1, fillOpacity: 0.08 } : vecStyle(fp)));
  }

  function renderResults() {
    const res = state.results;
    const box = $("#results");
    box.innerHTML = "";
    if (!res) return;
    const scenes = [...res.scenes].sort({
      cloud: (a, b) => a.cloud - b.cloud || b.date.localeCompare(a.date),
      date: (a, b) => b.date.localeCompare(a.date),
      coverage: (a, b) => b.coverage - a.coverage || a.cloud - b.cloud,
    }[state.sort]);
    $("#results-info").textContent = scenes.length
      ? `${scenes.length} acquisition date${scenes.length === 1 ? "" : "s"} · ${res.count} tile${res.count === 1 ? "" : "s"} from ${$("#source").selectedOptions[0].text.split(" —")[0]}`
      : "Nothing found. Try a longer date range or a higher cloud limit.";
    // nothing under the cloud limit (e.g. the monsoon): one click to search again with a higher one
    const cl = +$("#cloud").value;
    if (!scenes.length && cl < 100) {
      const ups = [60, 100].filter((v) => v > cl);
      $("#results-info").innerHTML = `Nothing found with up to ${cl} % cloud. Cloudy seasons (e.g. the monsoon) often have only cloudier scenes:
        ${ups.map((v) => `<button class="btn small" data-cloud-up="${v}">Search up to ${v} % cloud</button>`).join(" ")} or try a longer date range.`;
      $$("[data-cloud-up]").forEach((b) => b.onclick = () => { $("#cloud").value = b.dataset.cloudUp; $("#cloud").oninput(); $("#btn-search").click(); });
    }
    const feats = scenes.flatMap((sc) => sc.items.map((i) => ({ type: "Feature", geometry: i.footprint,
      properties: { date: sc.date, tile: i.tile, cloud_pct: i.cloud != null ? +(+i.cloud).toFixed(2) : null, platform: i.platform, id: i.id } })));
    if (feats.length) {
      addLayer({ id: "footprints", type: "vector", name: `Scene footprints · ${res.count} tiles`,
        geojson: { type: "FeatureCollection", features: feats }, color: "#6366f1", weight: 1, fillOpacity: 0 }, { select: false, below: "aoi" });
    } else removeLayer("footprints");

    scenes.forEach((sc) => {
      const first = sc.items[0];
      const el = document.createElement("div");
      el.className = "scene";
      el.innerHTML = `
        ${first.thumbnail ? `<img class="thumb" loading="lazy" src="${esc(first.thumbnail)}" alt="Thumbnail ${esc(sc.date)}" title="Open full thumbnail">` : `<div class="thumb empty">no preview</div>`}
        <div>
          <h4><span>${esc(weekday(sc.date))}</span></h4>
          <div class="sub">${esc([...new Set(sc.items.map((i) => i.platform).filter(Boolean))].join(", "))} · tile${sc.tiles.length > 1 ? "s" : ""} ${esc(sc.tiles.join(", "))}</div>
          <div class="pills">
            <span class="pill ${cloudClass(sc.cloud)}">☁ ${fmt(sc.cloud)}% tile cloud</span>
            <span class="pill">${fmt(sc.coverage * 100, 0)}% of area covered</span>
          </div>
          <div class="pvstat hidden"></div>
          <div class="actions">
            <button class="btn small" data-a="preview">Preview area</button>
            <button class="btn small" data-a="meta">Metadata</button>
            <button class="btn small primary" data-a="dl">Download</button>
          </div>
        </div>`;
      el.onmouseenter = () => highlightScene(sc.date);
      el.onmouseleave = () => highlightScene(state.activeScene?.date);
      el.querySelector(".thumb")?.addEventListener("click", () => first.thumbnail && window.open(first.thumbnail, "_blank", "noopener"));
      el.querySelector('[data-a="preview"]').onclick = (e) => previewScene(sc, el, e.currentTarget);
      el.querySelector('[data-a="meta"]').onclick = () => showMetadata(sc);
      el.querySelector('[data-a="dl"]').onclick = () => openDownload(sc);
      sc._el = el;
      box.append(el);
    });
  }

  // ------------------------------------------------------------------ preview (added as image layers)
  function removePreviews() {
    layers.filter((l) => /^(preview|clouds)-/.test(l.id)).forEach((l) => removeLayer(l.id, { silent: true }));
    renderContents();
    state.activeScene?._el?.classList.remove("active");
    state.activeScene = null;
  }

  async function previewScene(sc, el, btn) {
    await busy(btn, "Loading…", async () => {
      try {
        const res = await trackFetch((signal) => api("/api/preview", { method: "POST", signal, json: {
          source: state.results.source, item_ids: sc.items.map((i) => i.id), aoi: state.aoi,
        } }), { tool: "search", title: `Preview ${sc.date}`, message: "Reading imagery and cloud mask for your area" });
        removePreviews();
        state.activeScene = sc;
        el.classList.add("active");
        addLayer({ id: `preview-${sc.date}`, type: "image", name: `Preview ${sc.date} · true colour`, url: res.image, bounds: res.bounds },
          { select: false, below: "footprints" });
        addLayer({ id: `clouds-${sc.date}`, type: "image", name: `Cloud / shadow mask ${sc.date}`, url: res.clouds, bounds: res.bounds },
          { select: false, below: "footprints" });
        map.fitBounds(res.bounds, { padding: [30, 30] });
        const s = res.stats;
        const pv = el.querySelector(".pvstat");
        pv.textContent = `Inside your area: ${fmt(s.clear_pct)}% clear · ${fmt(s.cloud_pct)}% cloud · ${fmt(s.shadow_pct)}% shadow · ${res.resolution_m} m preview`;
        pv.classList.remove("hidden");
        status(`Preview ${sc.date}: ${fmt(s.clear_pct)}% clear inside the area`);
      } catch (e) { if (notCancelled(e)) toast(e, true); }
    });
  }

  // ------------------------------------------------------------------ metadata
  const KEY_PROPS = [
    ["datetime", "Acquired (UTC)"], ["platform", "Satellite"], ["constellation", "Constellation"], ["instruments", "Instrument"],
    ["eo:cloud_cover", "Cloud cover %"], ["s2:mgrs_tile", "MGRS tile"], ["grid:code", "Grid"], ["proj:epsg", "EPSG"], ["proj:code", "Projection"],
    ["sat:relative_orbit", "Relative orbit"], ["sat:orbit_state", "Orbit direction"], ["view:sun_elevation", "Sun elevation °"],
    ["view:sun_azimuth", "Sun azimuth °"], ["s2:processing_baseline", "Processing baseline"], ["processing:version", "Processing baseline"],
    ["s2:product_uri", "Product"], ["s2:vegetation_percentage", "Vegetation %"], ["s2:water_percentage", "Water %"],
    ["s2:not_vegetated_percentage", "Not vegetated %"], ["s2:cloud_shadow_percentage", "Cloud shadow %"],
    ["s2:snow_ice_percentage", "Snow / ice %"], ["s2:nodata_pixel_percentage", "No-data %"], ["created", "Catalogued"],
  ];
  const showVal = (v) => typeof v === "object" ? esc(JSON.stringify(v)) : esc(v);

  function showMetadata(sc) {
    const dlg = $("#dlg-meta");
    $("#meta-title").textContent = `Metadata · ${sc.date} · ${sc.tiles.join(", ")}`;
    const tabs = $("#meta-items");
    tabs.innerHTML = sc.items.length > 1 ? sc.items.map((it, i) => `<button class="${i ? "" : "active"}" data-i="${i}">${esc(it.tile)}</button>`).join("") : "";
    const render = (it) => {
      const p = it.properties;
      const keyRows = KEY_PROPS.filter(([k]) => p[k] != null).map(([k, label]) => `<tr><td>${esc(label)}</td><td>${showVal(p[k])}</td></tr>`).join("");
      const allRows = Object.keys(p).sort().map((k) => `<tr><td>${esc(k)}</td><td>${showVal(p[k])}</td></tr>`).join("");
      const assets = it.assets.map((a) => `<tr><td>${esc(a.key)}</td><td>${esc(a.title || "")}${a.gsd ? ` · ${a.gsd} m` : ""}<br><small class="mono">${esc((a.type || "").split(";")[0])}</small></td></tr>`).join("");
      $("#meta-body").innerHTML = `
        <div class="meta-top">
          ${it.thumbnail ? `<img src="${esc(it.thumbnail)}" alt="Tile thumbnail">` : "<div></div>"}
          <div>
            <div class="mono small" style="word-break:break-all">${esc(it.id)}</div>
            <table class="kv" style="margin-top:8px">
              <tr><td>Area covered</td><td>${fmt(it.coverage * 100, 1)}%</td></tr>${keyRows}
            </table>
            <div class="row">
              ${it.self_href ? `<a class="btn small" href="${esc(it.self_href)}" target="_blank" rel="noopener">STAC item JSON ↗</a>` : ""}
              <button class="btn small" id="copy-json">Copy all metadata</button>
            </div>
          </div>
        </div>
        <div class="meta-sec">Assets (${it.assets.length})</div><table class="kv">${assets}</table>
        <details><summary class="meta-sec">All properties (${Object.keys(p).length})</summary><table class="kv">${allRows}</table></details>
        <details><summary class="meta-sec">Raw JSON</summary><pre class="json">${esc(JSON.stringify({ id: it.id, geometry: it.footprint, properties: p }, null, 2))}</pre></details>`;
      $("#copy-json").onclick = () => navigator.clipboard.writeText(JSON.stringify({ id: it.id, geometry: it.footprint, properties: p, assets: it.assets }, null, 2)).then(() => toast("Copied"));
    };
    $$("button", tabs).forEach((b) => b.onclick = () => {
      $$("button", tabs).forEach((x) => x.classList.toggle("active", x === b));
      render(sc.items[+b.dataset.i]);
    });
    render(sc.items[0]);
    dlg.showModal();
  }
