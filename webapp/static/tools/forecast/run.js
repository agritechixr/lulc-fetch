/* Forecast ▸ Forecast with a model: a model from Train forecasting model + a table with newer values (the same columns)
   → the next steps, with the uncertainty band, as a table, a chart and points on the map.
   Server: /api/forecast/models, /api/forecast/run · lulc_fetch/forecast.py. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "fcrun", menu: "forecast", title: "Forecast with a model", icon: "fcrun",
    subtitle: "Use a model from Train forecasting model on newer data (the same columns, e.g. today's AQI and the weather forecast): the next hours / days / months with an uncertainty band, as a table, a chart and points on the map",
    kinds: ["forecast"],
    panel: `
      <div class="card">
        <h2>Model <span class="req">required</span> ${tip("Models made with Forecast ▸ Train forecasting model, newest first, with their backtest error.")}</h2>
        <select id="fr-model"></select>
        <p class="hint" id="fr-model-info"></p>
      </div>
      <div class="card">
        <h2>Table with the latest values <span class="req">required</span> ${tip("The same columns the model was trained on, up to now (e.g. Get AQI & weather data run again today). The forecast starts after its last value. Rows after the last value with the other inputs filled (a weather forecast) are used as known future inputs.")}</h2>
        <select id="fr-table"></select>
        <p class="hint" id="fr-table-info"></p>
      </div>
      <div class="card">
        <h2>Forecast</h2>
        <label>How far ahead (steps) <input type="number" id="fr-h" min="1" max="5000"></label>
        <p class="hint" id="fr-h-info"></p>
        <label>Table name<input id="fr-name" maxlength="80" value="forecast"></label>
        ${LF.html.run("fr", "Forecast")}
      </div>
      <div id="fr-result" class="hidden"></div>`,

    setup(LF) {
      const { $, esc, fmt, api, toast, runJob, runButton, touched, autoName } = LF;
      const st = { models: [] };
      const model = () => st.models.find((m) => m.path === $("#fr-model").value);

      async function open(arg) {
        try { st.models = await api("/api/forecast/models"); } catch (e) { toast(e, true); return; }
        await LF.fc.schema();
        const sel = $("#fr-model"), cur = arg?.model || sel.value;
        sel.innerHTML = st.models.length ? st.models.map((m) => `<option value="${esc(m.path)}">${esc(m.name)} · ${esc(m.cfg.target)} · ${esc(m.model_title)}${m.metrics?.mae != null ? ` · MAE ${fmt(m.metrics.mae, 1)}` : ""}</option>`).join("")
          : `<option value="">No forecasting model yet: train one with Forecast ▸ Train forecasting model</option>`;
        if (st.models.some((m) => m.path === cur)) sel.value = cur;
        const m = model();
        await LF.fc.fillTables($("#fr-table"), arg?.table || (m && st.lastModel !== m.path ? tableOf(m) : null));
        st.lastModel = m?.path;
        renderModel();
      }
      // the model's own training table, when it is still in tables/
      const tableOf = (m) => { const t = String(m.trained?.table || "").replace(/\\/g, "/"), i = t.lastIndexOf("/tables/"); return i >= 0 ? t.slice(i + 1) : null; };
      async function renderModel() {
        const m = model();
        $("#fr-run").disabled = !m || !$("#fr-table").value;
        if (!m) { $("#fr-model-info").textContent = ""; return; }
        const F = (await LF.fc.schema()).freqs[m.freq], c = m.cfg;
        $("#fr-model-info").innerHTML = `${esc(F.title)} ${esc(c.target)}${c.series_col ? ` by ${esc(c.series_col)}` : ""} · trained on ${m.trained.series} series, ${esc(String(m.trained.start).slice(0, 10))} to ${esc(String(m.trained.end).slice(0, 10))}` +
          `${c.inputs.length ? ` · inputs: ${c.inputs.map(esc).join(", ")}` : ""} · checked ${LF.fc.unit(F.unit, m.horizon)} ahead (MAE ${fmt(m.metrics.mae, 2)}${m.metrics.skill != null ? `, ${fmt(100 * m.metrics.skill, 0)} % better than the baseline` : ""}) · ${esc(m.created)}`;
        $("#fr-h").placeholder = m.horizon; if (!$("#fr-h").value) $("#fr-h").value = m.horizon;
        $("#fr-table-info").textContent = `Needs the columns ${[c.time_col, c.target, c.series_col, c.lat_col, c.lon_col, ...c.inputs].filter(Boolean).join(", ")}.`;
        hInfo();
        autoName($("#fr-name"), `${m.name}_${new Date().toISOString().slice(0, 10)}`);
      }
      async function hInfo() {
        const m = model();
        if (!m) return;
        const F = (await LF.fc.schema()).freqs[m.freq], h = +$("#fr-h").value || m.horizon;
        $("#fr-h-info").innerHTML = h > m.horizon ? `<span style="color:var(--warn)">Further than the model was checked (${LF.fc.unit(F.unit, m.horizon)}): the later steps are less reliable and their band too narrow.</span>` : `${LF.fc.unit(F.unit, h)} ahead.`;
      }
      $("#fr-model").onchange = async () => { $("#fr-h").value = ""; const m = model(); if (m) await LF.fc.fillTables($("#fr-table"), tableOf(m)); st.lastModel = m?.path; renderModel(); };
      $("#fr-table").onchange = renderModel;
      $("#fr-h").oninput = hInfo;
      touched($("#fr-name"));

      runButton("fr", async () => {
        const m = model();
        if (!m || !$("#fr-table").value) return toast("Choose the model and the table", true);
        const body = { model: m.path, table: $("#fr-table").value, horizon: +$("#fr-h").value || null, name: $("#fr-name").value.trim() || "forecast" };
        const r = await runJob("/api/forecast/run", body, { tool: "fcrun", title: `Forecasting with ${m.name}` });
        LF.fc.show("fr", r);
      });

      return { open };
    },
  });
})();
