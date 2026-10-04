/* Tools ▸ Interpolation: a surface from values measured at points (AQI stations, rain gauges, soil samples, wells, spot
   heights …): IDW, kriging, spline, natural neighbour, nearest neighbour, trend surface or TIN, cut to an area, with a
   leave-one-out check of each method. Server: /api/interp/schema, run, compare · lulc_fetch/interpolation.py. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "interp", title: "Interpolation", icon: "interp",
    subtitle: "Make a continuous surface (GeoTIFF) from values measured at points, e.g. air quality at monitoring stations, rainfall at gauges, soil samples: IDW, kriging, spline, natural neighbour, nearest neighbour, trend surface or TIN, cut to an area, with a check of how well each method predicts",
    kinds: ["interp", "interpcompare"],
    save: [["interp", "#ip-run", "the interpolated GeoTIFF"]],
    clip: { "ip-area": { what: "area around the points is mapped" } },
    panel: `
      <div class="card">
        <h2>Points <span class="req">required</span> ${tip("A layer of points with a number to interpolate: add a CSV with latitude / longitude columns (+ Add data puts its rows on the map), a shapefile / GeoJSON of points, or the Library's data.")}</h2>
        <select id="ip-layer"></select>
        <label>Value to interpolate<select id="ip-field"></select></label>
        <p class="hint" id="ip-info"></p>
      </div>
      <div class="card">
        <h2>Method</h2>
        <div id="ip-methods"></div>
        <div class="grid2" id="ip-params"></div>
        <div class="row tight" style="margin-top:8px;gap:4px;align-items:center"><button class="btn small" id="ip-compare">Compare all methods</button>${tip("Each point is predicted from the others (leave-one-out) with every method: lower RMSE / MAE is better, bias near 0 is better. Methods that work only inside the points' hull can check fewer points, so they're listed after the rest. Click a row to use that method.")}</div>
        <div id="ip-compare-box"></div>
      </div>
      <div class="card">
        <h2>Area &amp; grid</h2>
        ${LF.html.area("ip-area", "Area", "The surface covers this area and is cut to it (e.g. a city or district boundary from the Library). Whole extent = the points' extent plus a margin.")}
        <div class="grid2">
          <label>Cell size (m) ${tip("Empty = automatic (about 500 cells across). Smaller cells = a finer map, more time.")}<input type="number" id="ip-res" min="1" step="any" placeholder="auto"></label>
          <label>Colours<select id="ip-cmap"><option value="auto">Automatic</option><option value="AQI">AQI (CPCB categories)</option><option value="Spectral">Spectral</option><option value="Viridis">Viridis</option><option value="YlOrRd">Yellow → red</option><option value="RdYlGn">Red → green</option><option value="Blues">Blues</option><option value="Magma">Magma</option></select></label>
        </div>
      </div>
      <div class="card">
        <h2>Output</h2>
        <label>Layer name<input id="ip-name" maxlength="80" value="surface"></label>
        ${LF.html.run("ip", "Interpolate")}
      </div>
      <div id="ip-result" class="hidden"></div>`,

    setup(LF) {
      const { $, $$, esc, fmt, prefs, api, toast, layers, getLayer, getClip, refreshClipPicker, modelPicker, tipBtn, runJob, runButton,
              showResult, notCancelled, addRasterFromPath, moveLayer, fillLayers, touched, autoName } = LF;
      const st = { schema: null, method: prefs.get("ip-method", "idw"), params: {} };
      const pointLayers = () => layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => f.geometry?.type === "Point"));
      const isNum = (v) => v !== null && v !== "" && v !== undefined && Number.isFinite(+v);

      async function open() {
        if (!st.schema) {
          try { st.schema = await api("/api/interp/schema"); } catch (e) { toast(e.message, true); return; }
          renderMethods();
        }
        renderLayers();
        refreshClipPicker("ip-area");
      }
      function renderLayers() {
        const l = fillLayers($("#ip-layer"), pointLayers(), { label: (x) => `${x.name} · ${x.geojson.features.length} points`,
          empty: "No point layer yet: + Add data (a CSV with lat / lon, or points), or the Library" });
        const f = $("#ip-field"), cur = f.value;
        const feats = l?.geojson.features.filter((x) => x.geometry?.type === "Point") || [];
        const keys = [...new Set(feats.flatMap((x) => Object.keys(x.properties || {})))].filter((k) => !k.startsWith("_") && feats.some((x) => isNum(x.properties?.[k])));
        const skip = /^(lat|lon|lng|long|latitude|longitude|x|y|no|id|fid|objectid)$/i;
        f.innerHTML = keys.length ? keys.map((k) => `<option>${esc(k)}</option>`).join("") : `<option value="">No number fields</option>`;
        f.value = keys.includes(cur) ? cur : keys.find((k) => /aqi|value|rain|temp|pm|elev|height|depth|conc/i.test(k)) || keys.find((k) => !skip.test(k)) || keys[0] || "";
        st.onlyIds = keys.length > 0 && keys.every((k) => skip.test(k));
        fieldInfo();
      }
      function fieldInfo() {
        const l = getLayer($("#ip-layer").value), k = $("#ip-field").value;
        const v = (l?.geojson.features || []).map((x) => x.properties?.[k]).filter(isNum).map(Number);
        $("#ip-info").innerHTML = st.onlyIds ? `<span style="color:var(--warn)">This layer has no measured values, only IDs / coordinates (${esc(k)}): choose a layer with values, e.g. AQI readings</span>`
          : v.length ? `${v.length} points · ${fmt(Math.min(...v), 1)} to ${fmt(Math.max(...v), 1)} · mean ${fmt(v.reduce((a, b) => a + b, 0) / v.length, 1)}` +
            (v.length < 10 ? ` ${tipBtn("Few points: every method is only a rough guess between them; Kriging needs 10+ to fit its model well.")}` : "") : "";
        if (/aqi/i.test(k) && $("#ip-cmap").value === "auto") $("#ip-cmap").value = "AQI";
        autoName($("#ip-name"), `${k || "value"}_${st.method}`);
        $("#ip-compare-box").innerHTML = "";
      }
      function renderMethods() {
        const M = st.schema.methods;
        if (!M[st.method]) st.method = "idw";
        modelPicker($("#ip-methods"), { value: st.method, onChange: (k) => { st.method = k; prefs.set("ip-method", k); renderMethods(); fieldInfo(); },
          items: Object.entries(M).map(([k, m]) => ({ id: k, title: m.title.replace(/ \(.*\)$/, ""), badge: k === "idw" ? "simple" : k === "kriging" ? "statistical" : "",
            tip: `${m.desc} Good for: ${m.good}.` })) });
        const m = M[st.method];
        $("#ip-params").innerHTML = m.params.map((p) => {
          const v = st.params[`${st.method}.${p.name}`] ?? p.default;
          const input = p.kind === "select" ? `<select data-ipp="${p.name}">${p.options.map(([o, t]) => `<option value="${esc(o)}" ${String(o) === String(v) ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`
            : `<input type="number" data-ipp="${p.name}" value="${esc(v)}" ${p.min != null ? `min="${p.min}"` : ""} ${p.max != null ? `max="${p.max}"` : ""} step="any">`;
          return `<label>${esc(p.title)} ${tipBtn(p.tip)}${input}</label>`;
        }).join("");
        $$("#ip-params [data-ipp]").forEach((i) => i.onchange = () => { st.params[`${st.method}.${i.dataset.ipp}`] = i.type === "number" ? +i.value : i.value; });
      }
      const params = () => Object.fromEntries((st.schema?.methods[st.method].params || []).map((p) => {
        const v = st.params[`${st.method}.${p.name}`] ?? p.default;
        return [p.name, p.kind === "select" ? v : +v];
      }));
      const pointsBody = () => {
        const l = getLayer($("#ip-layer").value), k = $("#ip-field").value;
        if (!l || !k) throw new Error("Choose a point layer and a number field");
        return { points: { type: "FeatureCollection", features: l.geojson.features.filter((f) => f.geometry?.type === "Point" && isNum(f.properties?.[k])) }, field: k, layer: l };
      };
      $("#ip-layer").onchange = renderLayers;
      $("#ip-field").onchange = fieldInfo;
      touched($("#ip-name"));

      // leave-one-out: each point predicted from the others, every method with its default settings
      $("#ip-compare").onclick = async () => {
        let b;
        try { b = pointsBody(); } catch (e) { return toast(e.message, true); }
        const btn = $("#ip-compare"); btn.disabled = true; $("#ip-compare-box").innerHTML = `<p class="hint">Checking every method…</p>`;
        try {
          const r = await runJob("/api/interp/compare", { points: b.points, field: b.field }, { tool: "interp", title: "Comparing interpolation methods" });
          const best = r.rows.find((x) => x.rmse != null && x.checked_all);
          $("#ip-compare-box").innerHTML = `<table class="ip-cmp"><thead><tr><th>Method</th><th>RMSE</th><th>MAE</th><th>Bias</th><th>Checked</th></tr></thead><tbody>${r.rows.map((x) =>
            `<tr class="${x === best ? "best" : ""} ${x.checked_all === false ? "partial" : ""}" data-ipm="${esc(x.method)}"><td>${esc(x.title.replace(/ \(.*\)/, ""))}</td>${x.rmse == null ? `<td colspan="4" class="hint">${esc(x.skipped || "")}</td>`
              : `<td>${fmt(x.rmse, 2)}</td><td>${fmt(x.mae, 2)}</td><td>${fmt(x.bias, 2)}</td><td>${x.n}</td>`}</tr>`).join("")}</tbody></table>
`;
          $$("#ip-compare-box [data-ipm]").forEach((tr) => tr.onclick = () => { st.method = tr.dataset.ipm; prefs.set("ip-method", st.method); renderMethods(); fieldInfo(); });
        } catch (e) { $("#ip-compare-box").innerHTML = ""; if (notCancelled(e)) toast(e.message, true); }
        finally { btn.disabled = false; }
      };

      runButton("ip", async () => {
        const b = pointsBody();
        const M = st.schema.methods[st.method], name = $("#ip-name").value.trim() || "surface";
        const r = await runJob("/api/interp/run", { points: b.points, field: b.field, method: st.method, params: params(), area: getClip("ip-area"),
                                                   res_m: +$("#ip-res").value || null, name }, { tool: "interp", title: `${M.title} · ${b.field}`, save: "interp" });
        let cmap = $("#ip-cmap").value;
        if (cmap === "auto") cmap = /aqi/i.test(b.field) ? "AQI" : "Spectral";
        const render = cmap === "AQI" ? { band: 1, cmap, stretch: "custom", vmin: 0, vmax: 500 } : { band: 1, cmap, stretch: "custom", vmin: r.min, vmax: r.max };
        await addRasterFromPath(r.path, { name, render, zoom: true });
        moveLayer(b.layer.id, 0);   // the points stay visible on top of the surface
        const cv = r.cv || {}, vg = r.variogram;
        showResult("ip", `<div class="card rm-head-card"><h2 style="margin:0">✓ ${esc(r.method_title)}</h2>
          <div class="pca-sum">${r.points} points → ${r.width.toLocaleString()} × ${r.height.toLocaleString()} cells of ${fmt(r.res_m, 0)} m (${esc(r.crs)}) · surface ${fmt(r.min, 1)} to ${fmt(r.max, 1)} (points ${fmt(r.data_min, 1)} to ${fmt(r.data_max, 1)})${r.duplicates_merged ? " · points at the same place averaged" : ""}</div>
          ${cv.rmse != null ? `<div class="metric-tiles" style="margin-top:8px;grid-template-columns:repeat(3,1fr)"><div class="metric"><b>${fmt(cv.rmse, 2)}</b><span>RMSE ${tipBtn(`Leave-one-out: each point predicted from the others (checked at ${cv.n} of ${r.points} points). The typical error of a prediction, in the value's units.`)}</span></div>
            <div class="metric"><b>${fmt(cv.mae, 2)}</b><span>MAE</span></div><div class="metric"><b>${fmt(cv.bias, 2)}</b><span>Bias</span></div></div>
` : `<p class="hint">${esc(cv.skipped || "")}</p>`}
          ${vg ? `<p class="hint">Semivariogram ${tipBtn(`${vg.model}, nugget ${fmt(vg.nugget, 2)}, sill ${fmt(vg.sill, 2)}, range ${fmt(vg.range / 1000, 1)} km${vg.fitted ? "" : " (not fitted: too few points, so defaults were used)"}. Band 2 of the layer is the kriging standard error.`)}</p>` : ""}
          ${cmap === "AQI" ? `<div class="ip-aqi">${[["Good", "0–50", "#009966"], ["Satisfactory", "51–100", "#9ccc3c"], ["Moderate", "101–200", "#ffde33"], ["Poor", "201–300", "#ff9933"], ["Very poor", "301–400", "#e53935"], ["Severe", "401–500", "#7e0023"]].map(([t, rng, c]) => `<span><i style="background:${c}"></i>${t} <small>${rng}</small></span>`).join("")}</div>` : ""}
          </div>`);
      });

      return { open, layersChanged: renderLayers };
    },
  });
})();
