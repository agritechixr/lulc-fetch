/* Forecast ▸ Get AQI & weather data: hourly air quality (CAMS, with the Indian AQI computed from it) and weather (past
   days + the weather forecast for the next days) at the points of a layer, as a table ready for Train forecasting model.
   Free, no key: Open-Meteo. Server: /api/forecast/getdata · lulc_fetch/openmeteo.py. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "fcdata", menu: "forecast", title: "Get AQI & weather data", icon: "cloud",
    subtitle: "Hourly air quality (PM2.5, PM10, NO2, SO2, CO, O3 and the Indian AQI) and weather (temperature, humidity, wind, rain, cloud, sunshine) at your points: up to 92 past days plus the weather forecast for the next days. Free, from Open-Meteo (CAMS and ECMWF models)",
    kinds: ["fcdata"],
    panel: `
      <div class="card">
        <h2>Places <span class="req">required</span> ${tip("A layer of points: monitoring stations, towns, farms… e.g. + Add data with a CSV of latitude / longitude, or the Library. Up to 500 points.")}</h2>
        <select id="fd-layer"></select>
        <label>Name field<select id="fd-field"></select></label>
        <p class="hint" id="fd-info"></p>
      </div>
      <div class="card">
        <h2>Data</h2>
        <label class="inline"><input type="checkbox" id="fd-air" checked> Air quality and the Indian AQI ${tip("CAMS (Copernicus Atmosphere Monitoring Service) global model, about 40 km cells, hourly: PM2.5, PM10, NO2, SO2, CO, O3, dust (µg/m³). The AQI is computed the CPCB way: 24-hour averages for PM / NO2 / SO2 and 8-hour for CO / O3, the highest sub-index, when PM and 3+ pollutants are there. Model values, not station measurements: points closer than the cell size get the same values.")}</label>
        <label class="inline" style="margin-top:4px"><input type="checkbox" id="fd-weather" checked> Weather ${tip("Temperature, humidity, dew point, rain, cloud cover, pressure, wind speed / direction and sunshine, hourly, from the best weather model for the place (ECMWF / national models, about 9–11 km).")}</label>
        <div class="grid2" style="margin-top:8px">
          <label>Past days<select id="fd-past"><option>7</option><option>14</option><option>30</option><option selected>60</option><option>92</option></select></label>
          <label>Weather forecast ${tip("Rows for the next days with the weather forecast (and no AQI yet): a forecasting model uses them as known future inputs.")}<select id="fd-fc"><option value="0">None</option><option value="3">3 days</option><option value="7" selected>7 days</option><option value="16">16 days</option></select></label>
        </div>
      </div>
      <div class="card">
        <h2>Output</h2>
        <label>Table name<input id="fd-name" maxlength="80" value="aqi_weather"></label>
        ${LF.html.run("fd", "Get data")}
        <p class="hint">Data: <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo</a> (CC BY 4.0; free for non-commercial use), CAMS / ECMWF.</p>
      </div>
      <div id="fd-result" class="hidden"></div>`,

    setup(LF) {
      const { $, esc, fmt, layers, getLayer, toast, fillLayers, runJob, runButton, showResult, dataItems, addItem, openItem, openTool, touched, autoName } = LF;
      const pointLayers = () => layers.filter((l) => l.type === "vector" && l.geojson?.features?.some((f) => f.geometry?.type === "Point"));
      const pts = (l) => (l?.geojson.features || []).filter((f) => f.geometry?.type === "Point" && Array.isArray(f.geometry.coordinates));

      function render() {
        const l = fillLayers($("#fd-layer"), pointLayers(), { label: (x) => `${x.name} · ${pts(x).length} points`,
          empty: "No point layer yet: + Add data (a CSV with lat / lon), or the Library" });
        const f = $("#fd-field"), cur = f.value, feats = pts(l);
        const keys = [...new Set(feats.flatMap((x) => Object.keys(x.properties || {})))].filter((k) => !k.startsWith("_"));
        f.innerHTML = opt("", "(numbered)") + keys.map((k) => opt(k, k)).join("");
        f.value = keys.includes(cur) ? cur : keys.find((k) => /^(name|station|site|location|place|city|town|village)$/i.test(k)) || keys.find((k) => /name|station|site/i.test(k)) || "";
        $("#fd-info").textContent = l ? `${feats.length} point${feats.length === 1 ? "" : "s"}${feats.length > 500 ? " (up to 500: the first 500 are used)" : ""}` : "";
        $("#fd-run").disabled = !feats.length;
        if (l) autoName($("#fd-name"), `${l.name.replace(/\.[^.]+$/, "")}_aqi_weather`.replace(/[^A-Za-z0-9_-]+/g, "_"));
      }
      const opt = (v, t) => `<option value="${esc(v)}">${esc(t)}</option>`;
      $("#fd-layer").onchange = render;
      touched($("#fd-name"));

      runButton("fd", async () => {
        const l = getLayer($("#fd-layer").value), k = $("#fd-field").value;
        const feats = pts(l).slice(0, 500);
        if (!feats.length) return toast("Choose a layer of points", true);
        const seen = new Map();
        const points = feats.map((f, i) => {
          let name = String((k && f.properties?.[k]) ?? `point_${i + 1}`).trim() || `point_${i + 1}`;
          const n = (seen.get(name) || 0) + 1; seen.set(name, n);
          if (n > 1) name = `${name} (${n})`;   // two places with one name stay two series
          return { name, lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] };
        });
        const body = { points, past_days: +$("#fd-past").value, forecast_days: +$("#fd-fc").value, air: $("#fd-air").checked, weather: $("#fd-weather").checked,
                       name: $("#fd-name").value.trim() || "aqi_weather" };
        if (!body.air && !body.weather) return toast("Choose air quality, weather or both", true);
        const r = await runJob("/api/forecast/getdata", body, { tool: "fcdata", title: `Getting data for ${points.length} place${points.length > 1 ? "s" : ""}` });
        const it = addItem({ kind: "table", name: r.path.split("/").pop(), path: r.path });
        const shared = body.air && points.length > 1 && r.distinct_air < points.length * 0.6
          ? `<p class="hint fc-warn">The ${points.length} places fall in about ${Math.max(1, Math.round(r.distinct_air))} air-quality model cell${Math.round(r.distinct_air) > 1 ? "s" : ""} (about 40 km each), so many share the same air values; the weather differs more. For station-level detail use measured readings (e.g. CPCB).</p>` : "";
        const box = showResult("fd", `<div class="card rm-head-card"><h2 style="margin:0">✓ ${r.rows.toLocaleString()} rows</h2>
          <div class="pca-sum">${r.places} place${r.places > 1 ? "s" : ""} · hourly ${esc(r.start.replace("T", " "))} to ${esc(r.end.replace("T", " "))}${r.aqi_range ? ` · AQI ${fmt(r.aqi_range[0], 0)}–${fmt(r.aqi_range[1], 0)} (until ${esc(String(r.last_aqi_time).replace("T", " "))})` : ""}</div>
          ${shared}
          <div class="row tight" style="margin-top:10px;flex-wrap:wrap;gap:6px"><button class="btn small" data-fd-open>Open the table</button><button class="btn small" data-fd-train>Train a forecast with it…</button></div>
          <p class="hint">${body.forecast_days ? `Rows after now have the weather forecast and no AQI yet: Train forecasting model uses them as future inputs. ` : ""}In Contents as ${esc(it.name)}.</p></div>`);
        $("[data-fd-open]", box).onclick = () => openItem(dataItems.find((d) => d.path === r.path) || it);
        $("[data-fd-train]", box).onclick = () => openTool("fctrain", { table: r.path });
      });

      return { open: render, layersChanged: render };
    },
  });
})();
