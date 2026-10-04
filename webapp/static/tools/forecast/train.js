/* Forecast ▸ Train forecasting model: a table with a time column (one or many series, e.g. AQI per station) → a model that
   forecasts the next hours / days / months, checked by backtesting against simple baselines, saved for Forecast ▸ Run.
   Server: /api/forecast/describe, /api/forecast/train · lulc_fetch/forecast.py. */
(() => {
  "use strict";
  const { tip } = LF.html;

  LF.tool({
    id: "fctrain", menu: "forecast", title: "Train forecasting model", icon: "forecast",
    subtitle: "Forecast the next hours, days or months of a value in a table (AQI per station, rainfall, humidity, sales…) from its past, the calendar, nearby stations and other inputs such as weather: LightGBM, XGBoost, Random Forest, linear and more, checked against simple baselines",
    kinds: ["fctrain"],
    panel: `
      <div class="card">
        <h2>Table <span class="req">required</span> ${tip("One row per time (and per station / place when there are several): a date or time column, the value to forecast, and optionally a station column, latitude / longitude and other inputs (weather…). Forecast ▸ Get AQI & weather data makes one; or + Add data (CSV / Excel).")}</h2>
        <select id="ft-table"></select>
        <p class="hint" id="ft-info"></p>
      </div>
      <div class="card">
        <h2>Columns</h2>
        <div class="grid2">
          <label>Date / time <select id="ft-time"></select></label>
          <label>Value to forecast <select id="ft-target"></select></label>
          <label>Station / series ${tip("When the table has several places (stations, districts…), the column that names them: one model learns from all of them together. None = the table is one series.")}<select id="ft-series"></select></label>
          <span></span>
          <label>Latitude ${tip("With the stations' latitude / longitude the model also uses where each one is and the latest values at its nearest stations (3 or more stations), and the forecast can be put on the map.")}<select id="ft-lat"></select></label>
          <label>Longitude<select id="ft-lon"></select></label>
        </div>
        <div class="fc-inputs-head"><b>Other inputs</b> ${tip("Other numbers that help, e.g. temperature, humidity, wind, rain. The model uses their value at each time and one step before. For the future it takes them from the table when it has rows after the last value (e.g. a weather forecast) and otherwise repeats the last cycle. Choose a few that plausibly matter: each one adds training time.")}
          <button type="button" class="link-btn" id="ft-in-none">none</button></div>
        <div id="ft-inputs" class="fc-inputs"></div>
        <p class="hint" id="ft-in-info"></p>
      </div>
      <div class="card">
        <h2>Forecast</h2>
        <div class="grid2">
          <label>Time step ${tip("Automatic = from the gaps between the times in the table. Values within one step are averaged (e.g. 15-minute readings → hourly).")}<select id="ft-freq"><option value="auto">Automatic</option><option value="h">Hourly</option><option value="D">Daily</option><option value="W">Weekly</option><option value="MS">Monthly</option></select></label>
          <label>How far ahead (steps) <input type="number" id="ft-h" min="1" max="5000" placeholder="auto"></label>
          <label>Backtests ${tip("How many times the model is checked: trained on the data before a cut-off and asked to forecast the steps after it. The cut-offs are spread over the last year of the data, so a seasonal value (rain, monsoon AQI) is checked in different seasons. More = a fairer score and a better uncertainty band; each one is a training.")}<select id="ft-bt"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>6</option><option>8</option><option>10</option></select></label>
          <label>How it forecasts ${tip("Step by step: predicts the next step, then uses that prediction as if it were a value for the step after (good for smooth cycles like hourly AQI). Direct: predicts each step ahead straight from what is known now, so errors don't build up (good for values that are often 0, like daily rain). Automatic: backtests the best model both ways and keeps the better.")}<select id="ft-strategy"><option value="auto" selected>Automatic</option><option value="recursive">Step by step</option><option value="direct">Direct</option></select></label>
        </div>
        <p class="hint" id="ft-h-info"></p>
      </div>
      <div class="card">
        <h2>Model</h2>
        <div id="ft-models"></div>
        <div class="fc-set-head"><b>Model settings</b> ${tip("The settings of the chosen model, like a model's hyperparameters. The defaults work well for most data; change one at a time and compare the backtest error. With “Compare all”, choose which model's settings to edit: each model uses its own.")}
          <select id="ft-set-model" class="hidden"></select><button type="button" class="link-btn" id="ft-set-reset">defaults</button></div>
        <div class="grid2" id="ft-settings"></div>
        <p class="hint" id="ft-set-note"></p>
      </div>
      <div class="card">
        <h2>Training ${tip("Options for every model: early stopping (for the boosted trees, like stopping training epochs when the validation error stops improving), how many rows each fit uses, the uncertainty band, the random seed, and automatic tuning.")}</h2>
        <div class="grid2" id="ft-options"></div>
      </div>
      <div class="card">
        <h2>Output</h2>
        <label>Model name<input id="ft-name" maxlength="80" value="forecast"></label>
        ${LF.html.run("ft", "Train and forecast")}
      </div>
      <div id="ft-result" class="hidden"></div>`,

    setup(LF) {
      const { $, $$, esc, fmt, prefs, api, toast, modelPicker, runJob, runButton, openTool, touched, autoName } = LF;
      const st = { schema: null, desc: null, model: prefs.get("ft-model", "auto"), want: null, params: prefs.get("ft-params", {}) || {}, opts: prefs.get("ft-opts", {}) || {}, setModel: null };
      const opt = (v, t, cur) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(t)}</option>`;

      async function open(arg) {
        if (!st.schema) {
          try { st.schema = await LF.fc.schema(); } catch (e) { toast(e.message, true); return; }
          renderModels();
          renderOptions();
        }
        const path = await LF.fc.fillTables($("#ft-table"), arg?.table);
        if (path && (path !== st.desc?.path)) await describe(path);
        else if (!path) { st.desc = null; render(); }
      }
      async function describe(path) {
        $("#ft-info").innerHTML = `<span class="spinner"></span>Reading the table…`;
        try { st.desc = { ...(await api(`/api/forecast/describe?path=${encodeURIComponent(path)}`)), path }; }
        catch (e) { st.desc = null; $("#ft-info").textContent = e.message; return; }
        const g = st.desc.guess;
        st.inputs = new Set((st.desc.inputs || []).filter((k) => /^(temperature|temp|humidity|rh|relative_humidity|wind_speed|wind|rain|rainfall|precipitation)$/i.test(k)));
        render(g);
      }
      function render(g) {
        const d = st.desc;
        if (!d) { ["#ft-time", "#ft-target", "#ft-series", "#ft-lat", "#ft-lon"].forEach((s) => { $(s).innerHTML = ""; }); $("#ft-inputs").innerHTML = ""; $("#ft-info").textContent = ""; return; }
        const cols = d.columns, num = cols.filter((c) => c.type === "number"), cur = (s) => g ? null : $(s).value;
        const times = cols.filter((c) => c.type === "time");
        $("#ft-time").innerHTML = (times.length ? times : cols).map((c) => opt(c.name, c.name, cur("#ft-time") || g?.time)).join("");
        $("#ft-target").innerHTML = num.map((c) => opt(c.name, c.name, cur("#ft-target") || g?.target)).join("") || `<option value="">No number columns</option>`;
        $("#ft-series").innerHTML = opt("", "None (one series)", "") + cols.filter((c) => c.type !== "time" && c.unique > 1 && c.unique < d.rows).map((c) => opt(c.name, `${c.name} · ${c.unique} values`, cur("#ft-series") ?? g?.series)).join("");
        if (g && !g.series) $("#ft-series").value = "";
        ["lat", "lon"].forEach((k) => { $(`#ft-${k}`).innerHTML = opt("", "None", "") + num.map((c) => opt(c.name, c.name, cur(`#ft-${k}`) ?? g?.[k])).join(""); if (g && !g[k]) $(`#ft-${k}`).value = ""; });
        $("#ft-info").innerHTML = `${d.rows.toLocaleString()} rows · ${esc(String(d.start || "").slice(0, 16))} to ${esc(String(d.end || "").slice(0, 16))}${d.freq ? ` · ${st.schema.freqs[d.freq].title.toLowerCase()}` : ""}${d.series_count > 1 ? ` · ${d.series_count} series` : ""}` +
          (d.freq_error ? `<br><span style="color:var(--err)">${esc(d.freq_error)}</span>` : "");
        renderInputs();
        horizonInfo();
        autoName($("#ft-name"), `${$("#ft-target").value || "value"}_forecast`.replace(/[^A-Za-z0-9_-]+/g, "_"));
      }
      function renderInputs() {
        const d = st.desc;
        if (!d) return;
        const used = new Set([$("#ft-target").value, $("#ft-lat").value, $("#ft-lon").value, $("#ft-series").value, $("#ft-time").value]);
        const list = d.columns.filter((c) => c.type === "number" && !used.has(c.name) && c.unique > 1 && !/^(no|sl_?no|s_?no|index|fid|objectid|id)$/i.test(c.name));
        $("#ft-inputs").innerHTML = list.length ? list.map((c) => `<label class="inline"><input type="checkbox" value="${esc(c.name)}" ${st.inputs.has(c.name) ? "checked" : ""}> ${esc(c.name)}</label>`).join("")
          : `<span class="hint">No other number columns.</span>`;
        $$("#ft-inputs input").forEach((i) => i.onchange = () => { i.checked ? st.inputs.add(i.value) : st.inputs.delete(i.value); inputsInfo(); });
        inputsInfo();
      }
      function inputsInfo() {
        const d = st.desc, n = [...st.inputs].filter((k) => d?.columns.some((c) => c.name === k)).length;
        $("#ft-in-info").textContent = !d ? "" : d.future_rows && n ? `The table has ${d.future_rows.toLocaleString()} rows after the last ${$("#ft-target").value}: their inputs are used as known future values (e.g. a weather forecast).`
          : n ? "No rows after the last value: future inputs will repeat the last cycle." : "";
      }
      function horizonInfo() {
        const d = st.desc, f = $("#ft-freq").value === "auto" ? d?.freq : $("#ft-freq").value;
        if (!f) { $("#ft-h-info").textContent = ""; return; }
        const F = st.schema.freqs[f];
        $("#ft-h").placeholder = `auto (${F.horizon})`;
        const h = +$("#ft-h").value || F.horizon;
        const span = f === "h" && h >= 24 ? ` (${fmt(h / 24, h % 24 ? 1 : 0)} days)` : f === "D" && h >= 7 ? ` (${fmt(h / 7, h % 7 ? 1 : 0)} weeks)` : "";
        $("#ft-h-info").textContent = `${LF.fc.unit(F.unit, h)} ahead${span}. Every model is checked by forecasting this far ahead from ${$("#ft-bt").value} cut-off${$("#ft-bt").value === "1" ? "" : "s"} spread over the last year of the data.`;
      }
      // one setting as an input (number or checkbox), with its help
      const field = (p, value, attr) => p.kind === "bool"
        ? `<label class="inline" style="margin-top:6px"><input type="checkbox" ${attr}="${p.name}" ${value ? "checked" : ""}> ${esc(p.title)} ${tip(p.tip)}</label>`
        : `<label>${esc(p.title)} ${tip(p.tip + (p.min != null ? ` (${p.min}–${p.max})` : ""))}<input type="number" ${attr}="${p.name}" value="${value}" min="${p.min}" max="${p.max}" step="${p.kind === "int" ? 1 : "any"}"></label>`;
      function renderSettings() {
        const M = st.schema.models, ml = Object.keys(M).filter((k) => (M[k].params || []).length && M[k].available);
        const sel = $("#ft-set-model");
        sel.classList.toggle("hidden", st.model !== "auto");
        if (st.model === "auto") {
          if (!ml.includes(st.setModel)) st.setModel = ml[0];
          sel.innerHTML = ml.map((k) => `<option value="${k}" ${k === st.setModel ? "selected" : ""}>${esc(M[k].title)}</option>`).join("");
        } else st.setModel = st.model;
        const ps = M[st.setModel]?.params || [];
        $("#ft-settings").innerHTML = ps.map((p) => field(p, st.params[st.setModel]?.[p.name] ?? p.default, "data-fp")).join("");
        $("#ft-set-reset").classList.toggle("hidden", !ps.length);
        const changed = Object.keys(st.params).filter((k) => Object.keys(st.params[k] || {}).length);
        $("#ft-set-note").textContent = !ps.length ? "The baselines have no settings." :
          changed.length ? `Changed: ${changed.map((k) => `${M[k].title} (${Object.entries(st.params[k]).map(([n, v]) => `${(M[k].params.find((x) => x.name === n) || {}).title || n} ${v}`).join(", ")})`).join("; ")}.` : "All settings at their defaults.";
        $$("#ft-settings [data-fp]").forEach((i) => i.onchange = () => {
          const p = ps.find((x) => x.name === i.dataset.fp), v = +i.value;
          const m = (st.params[st.setModel] ||= {});
          if (!Number.isFinite(v) || v === p.default) delete m[p.name]; else m[p.name] = Math.min(p.max, Math.max(p.min, v));
          prefs.set("ft-params", st.params); renderSettings();
        });
      }
      function renderOptions() {
        $("#ft-options").innerHTML = st.schema.options.map((p) => field(p, st.opts[p.name] ?? p.default, "data-fo")).join("");
        $$("#ft-options [data-fo]").forEach((i) => i.onchange = () => {
          st.opts[i.dataset.fo] = i.type === "checkbox" ? i.checked : +i.value;
          prefs.set("ft-opts", st.opts);
          optionsInfo();
        });
        optionsInfo();
      }
      function optionsInfo() {
        const es = !!(st.opts.early_stop ?? false);
        const pat = $("#ft-options [data-fo='patience']")?.closest("label");
        if (pat) pat.classList.toggle("hidden", !es);
      }
      function renderModels() {
        renderSettings();
        const M = st.schema.models;
        if (!M[st.model]?.available) st.model = "auto";
        modelPicker($("#ft-models"), { value: st.model, onChange: (k) => { st.model = k; prefs.set("ft-model", k); renderModels(); },
          items: Object.entries(M).map(([k, m]) => ({ id: k, title: m.title, tip: m.desc, disabled: !m.available, why: m.available ? "" : "Not installed",
            group: k === "auto" ? "" : ["seasonal", "naive"].includes(k) ? "Baselines (always compared)" : "Machine learning",
            badge: k === "auto" ? "recommended" : k === "lightgbm" ? "usually best" : "" })) });
      }
      $("#ft-table").onchange = () => describe($("#ft-table").value);
      ["#ft-target", "#ft-lat", "#ft-lon", "#ft-series", "#ft-time"].forEach((s) => { $(s).onchange = () => { renderInputs(); autoName($("#ft-name"), `${$("#ft-target").value}_forecast`); }; });
      $("#ft-freq").onchange = horizonInfo;
      $("#ft-h").oninput = horizonInfo;
      $("#ft-bt").onchange = horizonInfo;
      $("#ft-in-none").onclick = () => { st.inputs.clear(); renderInputs(); };
      $("#ft-set-model").onchange = () => { st.setModel = $("#ft-set-model").value; renderSettings(); };
      $("#ft-set-reset").onclick = () => { delete st.params[st.setModel]; prefs.set("ft-params", st.params); renderSettings(); };
      touched($("#ft-name"));

      runButton("ft", async () => {
        const d = st.desc;
        if (!d) return toast("Choose a table", true);
        if (!$("#ft-target").value) return toast("Choose the value to forecast", true);
        const lat = $("#ft-lat").value, lon = $("#ft-lon").value;
        const body = { table: d.path, time_col: $("#ft-time").value, target: $("#ft-target").value, series_col: $("#ft-series").value || null,
          lat_col: lat && lon ? lat : null, lon_col: lat && lon ? lon : null, inputs: [...st.inputs].filter((k) => $$("#ft-inputs input").some((i) => i.value === k && i.checked)),
          freq: $("#ft-freq").value, horizon: +$("#ft-h").value || null, model: st.model, backtests: +$("#ft-bt").value, strategy: $("#ft-strategy").value,
          params: st.params, options: st.opts, name: $("#ft-name").value.trim() || "forecast" };
        const r = await runJob("/api/forecast/train", body, { tool: "fctrain", title: `Training a forecast of ${body.target}` });
        const box = LF.fc.show("ft", r, { trained: true });
        $("[data-fc-use]", box).onclick = () => openTool("fcrun", { model: r.path, table: d.path });
      });

      return { open };
    },
  });
})();
