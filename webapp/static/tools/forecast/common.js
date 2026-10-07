/* Forecast menu: what its tools share, as LF.fc (tables, the result card with its chart, the forecast as points).
   Server side: webapp/routes/forecast.py · the science: lulc_fetch/forecast.py and lulc_fetch/openmeteo.py */
(() => {
  "use strict";
  let schema = null;
  const UNIT_PLURAL = (u, n) => `${n} ${u}${n === 1 ? "" : "s"}`;

  LF.fc = {
    async schema() { return schema ||= await LF.api("/api/forecast/schema"); },
    /** a model setting's title ("n_estimators" → "Trees (boosting rounds)") */
    paramTitle: (model, name) => schema?.models?.[model]?.params?.find((p) => p.name === name)?.title,
    unit: UNIT_PLURAL,
    /** fill a <select> with the tables in tables/ (newest first); keeps the choice, or picks `want` */
    async fillTables(sel, want) {
      const list = await LF.api("/api/tables").catch(() => []);
      const cur = want || sel.value;
      sel.innerHTML = list.length ? list.map((t) => `<option value="${LF.esc(t.path)}">${LF.esc(t.name)}${t.rows != null ? ` · ${t.rows.toLocaleString()} rows` : ""}</option>`).join("")
        : `<option value="">No tables yet: Forecast ▸ Get AQI & weather data, or Insert ▸ Add data (CSV / Excel)</option>`;
      if (cur && list.some((t) => t.path === cur)) sel.value = cur;
      return sel.value;
    },

    /** an SVG chart of one series: recent history (solid), the forecast (dashed) and its uncertainty band */
    chart(r, i) {
      const s = r.series[i], { fmt, esc } = LF;
      const W = 340, H = 160, L = 36, R = 8, T = 10, B = 26;
      const nh = s.hist.length, nf = s.fc.length, n = nh + nf;
      const vals = [...s.hist, ...s.fc, ...s.lo, ...s.hi].filter((v) => v != null && Number.isFinite(v));
      if (!vals.length) return "";
      let lo = Math.min(...vals), hi = Math.max(...vals);
      if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
      const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
      const X = (k) => L + (W - L - R) * k / Math.max(1, n - 1), Y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
      const line = (arr, off) => arr.map((v, k) => v == null ? null : `${X(k + off).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean).join(" ");
      const band = s.lo.every((v) => v != null) ? `<polygon points="${[...s.hi.map((v, k) => `${X(k + nh).toFixed(1)},${Y(v).toFixed(1)}`), ...s.lo.map((v, k) => `${X(k + nh).toFixed(1)},${Y(v).toFixed(1)}`).reverse()].join(" ")}" fill="var(--accent)" opacity=".16"/>` : "";
      // the history line runs into the forecast's first point
      const join = s.hist[nh - 1] != null ? `<polyline points="${X(nh - 1).toFixed(1)},${Y(s.hist[nh - 1]).toFixed(1)} ${X(nh).toFixed(1)},${Y(s.fc[0]).toFixed(1)}" fill="none" stroke="var(--accent)" stroke-width="1.8" stroke-dasharray="4 3"/>` : "";
      const ticks = [lo + pad, (lo + hi) / 2, hi - pad].map((v) => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--border)" stroke-width=".6"/><text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end">${fmt(v, Math.abs(hi - lo) < 10 ? 1 : 0)}</text>`).join("");
      const lab = (k, t, anchor) => `<text x="${X(k)}" y="${H - 8}" text-anchor="${anchor}">${esc(String(t).replace(/^\d{4}-/, ""))}</text>`;
      return `<svg class="fc-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="History and forecast of ${esc(s.id)}">${ticks}${band}
        <line x1="${X(nh - 0.5)}" x2="${X(nh - 0.5)}" y1="${T}" y2="${H - B}" stroke="var(--muted)" stroke-dasharray="2 3" stroke-width=".8"/>
        <polyline points="${line(s.hist, 0)}" fill="none" stroke="var(--text)" stroke-width="1.4"/>${join}
        <polyline points="${line(s.fc, nh)}" fill="none" stroke="var(--accent)" stroke-width="1.8" stroke-dasharray="4 3"/>
        ${lab(0, r.hist_times[0], "start")}${lab(nh - 0.5, "now", "middle")}${lab(n - 1, r.times[nf - 1], "end")}</svg>`;
    },

    /** the forecast at one step as a points layer (series with latitude / longitude) */
    points(r, step) {
      const k = Math.max(0, Math.min(r.horizon - 1, step - 1));
      return { type: "FeatureCollection", features: r.series.filter((s) => s.lat != null && s.lon != null).map((s) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        properties: { series: s.id, time: r.times[k], forecast: s.fc[k], low: s.lo[k], high: s.hi[k], last_value: s.last } })) };
    },

    /** the result card of a training or a forecast run; returns the box */
    show(p, r, { trained = false } = {}) {
      const { $, $$, esc, fmt, tipBtn, showResult, dataItems, addItem, openItem, addVectorLayer, openTool, prefs } = LF;
      const m = r.meta || {}, met = m.metrics || {}, base = m.baseline;
      const u = (n) => UNIT_PLURAL(r.unit, n);
      const pct = (x) => x == null ? "–" : `${fmt(100 * x, 0)} %`;
      const hasXY = r.series.some((s) => s.lat != null);
      const many = r.series.length > 1;
      const tiles = trained ? `<div class="metric-tiles" style="margin-top:8px;grid-template-columns:repeat(3,1fr)">
          <div class="metric"><b>${fmt(met.mae, 2)}</b><span>MAE ${tipBtn(`Backtest: trained on the data before a cut-off, the model forecast the ${u(r.horizon)} after it (${m.trained?.backtests} cut-offs at the end of the data). MAE is the average error, in ${r.target}'s units; RMSE ${fmt(met.rmse, 2)} weighs big misses more; bias ${fmt(met.bias, 2)} (above 0 = forecasts too high).`)}</span></div>
          <div class="metric"><b>${pct(met.skill)}</b><span>Better than baseline ${tipBtn(base ? `How much lower the error is than the best simple baseline, “${base.title}” (MAE ${fmt(base.mae, 2)}). Above 0 = the model is worth it.` : "No baseline could be checked.")}</span></div>
          <div class="metric"><b>${met.mape == null ? "–" : `${fmt(met.mape, 0)} %`}</b><span>MAPE ${tipBtn("The average error as a percentage of the actual value (not shown when the values are often 0).")}</span></div></div>` : "";
      const cmp = trained && (m.compare || []).length > 1 ? `<details class="fc-more"><summary>All models compared ${tipBtn("Each model was backtested the same way; lower MAE is better. Skill = how much better than the best baseline.")}</summary>
          <table class="ip-cmp"><thead><tr><th>Model</th><th>MAE</th><th>RMSE</th><th>Bias</th><th>Skill</th></tr></thead><tbody>${m.compare.map((c) =>
            `<tr class="${c.key === m.model && [m.strategy, "-", undefined].includes(c.strategy) ? "best" : ""}"><td>${esc(c.title)}</td><td>${fmt(c.mae, 2)}</td><td>${fmt(c.rmse, 2)}</td><td>${fmt(c.bias, 2)}</td><td>${pct(c.skill)}</td></tr>`).join("")}</tbody></table></details>` : "";
      const imp = (r.importance || m.importance || []).filter((x) => x.pct >= 0.5).slice(0, 8);
      const impHtml = imp.length ? `<details class="fc-more"><summary>What the model relies on ${tipBtn("Permutation importance on the last backtest window: how much worse the one-step forecast gets when a group of inputs is shuffled. Shares of the total.")}</summary>
          <div class="dist" style="margin-top:6px">${imp.map((x) => `<div style="grid-template-columns:minmax(0,1.8fr) 2fr auto"><span title="${esc(x.name)}">${esc(x.name)}</span><span class="rb-track" style="margin:0"><span class="rb-fill" style="display:block;width:${Math.max(1, x.pct)}%"></span></span><b>${fmt(x.pct, 0)}%</b></div>`).join("")}</div></details>` : "";
      // the settings the model was trained with, the trees early stopping kept, and the tuning trials
      const S = m.params || {}, O = m.options || {}, title = (k) => (LF.fc.paramTitle?.(m.model, k)) || k;
      const settingsHtml = trained && Object.keys(S).length ? `<details class="fc-more"><summary>Settings used ${tipBtn("The model's settings (hyperparameters) and training options for this run. Change them in the Model and Training cards and train again to compare.")}</summary>
          <table class="ip-cmp"><tbody>${Object.entries(S).map(([k, v]) => `<tr><td>${esc(title(k))}</td><td>${esc(v)}</td></tr>`).join("")}
            ${m.rounds ? `<tr><td>Trees kept by early stopping</td><td><b>${m.rounds}</b></td></tr>` : ""}
            <tr><td>Rows used per fit</td><td>${(O.fit_rows || 150000).toLocaleString()}</td></tr><tr><td>Early stopping</td><td>${O.early_stop ? `on (patience ${O.patience})` : "off"}</td></tr>
            <tr><td>Random seed</td><td>${O.seed ?? 0}</td></tr></tbody></table>
          ${(m.tuning || []).length ? `<p class="hint" style="margin:8px 0 4px">Tuning: ${m.tuning.length - 1 >= 0 ? (O.tune || m.tuning.length - 1) : 0} random settings tried, the best kept (backtest MAE, lower is better):</p>
            <table class="ip-cmp"><thead><tr><th>Trial</th><th>MAE</th><th>Settings</th></tr></thead><tbody>${m.tuning.slice(0, 8).map((x, i) =>
              `<tr class="${i === 0 ? "best" : ""}"><td>${x.trial === 0 ? "yours" : x.trial}</td><td>${fmt(x.mae, 2)}</td><td style="text-align:left;white-space:normal;font-size:11.5px">${esc(Object.entries(x.settings).map(([k, v]) => `${k} ${v}`).join(", "))}</td></tr>`).join("")}</tbody></table>` : ""}</details>` : "";
      const stepInfo = trained && r.step_mae?.length > 1 ? `<p class="hint">The error grows from ${fmt(r.step_mae[0], 1)} one ${r.unit} ahead to ${fmt(r.step_mae[r.step_mae.length - 1], 1)} ${u(r.horizon)} ahead.</p>` : "";
      const box = showResult(p, `<div class="card rm-head-card"><h2 style="margin:0">✓ ${trained ? `${esc(r.model_title)} trained` : "Forecast ready"}</h2>
        <div class="pca-sum">${r.series.length} series · ${u(r.horizon)} ahead from ${esc(r.last_time)} · ${esc(r.model_title)}${r.seconds ? ` · ${r.seconds} s` : ""}</div>
        ${tiles}${stepInfo}
        <div class="row tight" style="margin-top:8px;gap:6px;align-items:center">${many ? `<select class="fc-series" style="flex:1;min-width:0">${r.series.map((s, i) => `<option value="${i}">${esc(s.id)}${s.stale ? " (no recent values)" : ""}</option>`).join("")}</select>` : ""}</div>
        <div class="fc-chart-box"></div>
        <p class="hint fc-legend"><span class="fc-key hist"></span> ${esc(r.target)} · <span class="fc-key fc"></span> forecast · <span class="fc-key band"></span> ${m.band_level || 80} % of the backtest errors were within this band</p>
        ${cmp}${impHtml}${settingsHtml}
        ${(r.warnings || []).map((w) => `<p class="hint fc-warn">${esc(w)}</p>`).join("")}
        <div class="row tight" style="margin-top:10px;flex-wrap:wrap;gap:6px"><button class="btn small" data-fc-table>Open the forecast table</button>
          ${hasXY ? `<span class="fc-step"><select data-fc-step>${r.times.map((t, k) => `<option value="${k + 1}">${esc(t)} (+${k + 1})</option>`).join("")}</select><button class="btn small" data-fc-points>Put on the map</button></span>` : ""}
          ${trained ? `<button class="btn small" data-fc-use>Forecast with it…</button>` : ""}</div>
        <p class="hint">The table (${(r.table_rows || 0).toLocaleString()} rows: series, time, forecast, low, high) is in Contents.${trained ? ` The model is in <code>${esc(r.path)}</code>.` : ""}</p></div>`);
      const draw = () => { $(".fc-chart-box", box).innerHTML = LF.fc.chart(r, +($(".fc-series", box)?.value || 0)); };
      if (many) $(".fc-series", box).onchange = draw;
      draw();
      const item = () => dataItems.find((d) => d.path === r.table);
      if (!item()) addItem({ kind: "table", name: r.table.split("/").pop(), path: r.table });
      $("[data-fc-table]", box).onclick = () => openItem(item() || addItem({ kind: "table", name: r.table.split("/").pop(), path: r.table }));
      if (hasXY) {
        $("[data-fc-step]", box).value = String(Math.min(r.horizon, +prefs.get("fc-step", 1) || 1));
        $("[data-fc-points]", box).onclick = () => {
          const step = +$("[data-fc-step]", box).value;
          prefs.set("fc-step", step);
          const name = `${r.target} forecast ${r.times[step - 1]}`;
          const lay = addVectorLayer(LF.fc.points(r, step), name, { zoom: true });
          LF.toast(`Added “${name}” to the map`);
          let btn = $("[data-fc-map]", box);
          if (!btn) { $("[data-fc-points]", box).insertAdjacentHTML("afterend", `<button class="btn small" data-fc-map title="Tools ▸ Interpolation: a surface from the forecast at the stations">Make a map of it…</button>`); btn = $("[data-fc-map]", box); }
          btn.onclick = () => openTool("interp", { layer: lay.id, field: "forecast" });
        };
      }
      return box;
    },
  };
})();
