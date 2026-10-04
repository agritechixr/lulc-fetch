  // The LF “universe”: shared helpers handed to the tool files (static/tools/<menu>/), and their start-up.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ universe: helpers every tool can use (window.LF, see tools/lf.js)
  /** POST a tool's request, follow its job (progress, ⓘ details, History) and return its result */
  async function runJob(endpoint, body, opts = {}) {
    const job = await api(endpoint, { method: "POST", json: body });
    return (await trackJob(job, opts)).result;
  }
  /** the usual Run button #<p>-run: hides the last error (#<p>-error) and result (#<p>-result), is disabled while fn runs,
   *  and shows why it failed (a cancelled run says so instead) */
  function runButton(p, fn) {
    const btn = $(`#${p}-run`);
    btn.onclick = async () => {
      const err = $(`#${p}-error`);
      err?.classList.add("hidden");
      $(`#${p}-result`)?.classList.add("hidden");
      btn.disabled = true;
      try { await fn(); }
      catch (e) { if (notCancelled(e)) { if (err) { err.textContent = e.message; err.classList.remove("hidden"); } else toast(e.message, true); } }
      finally { btn.disabled = false; }
    };
  }
  /** put html in #<p>-result and show it; returns the box */
  function showRunResult(p, html) {
    const box = $(`#${p}-result`);
    box.innerHTML = html;
    box.classList.remove("hidden");
    return box;
  }
  /** fill a <select> with layers (in the order they were added), keeping the choice when it's still there; returns the chosen layer */
  function fillLayers(sel, list, { label = (l) => l.name, empty = "No layer yet", pick } = {}) {
    const cur = pick || sel.value;
    sel.innerHTML = list.length ? list.slice().reverse().map((l) => `<option value="${esc(l.id)}">${esc(label(l))}</option>`).join("")
      : `<option value="">${esc(empty)}</option>`;
    if (list.some((l) => l.id === cur)) sel.value = cur;
    return getLayer(sel.value);
  }
  /** name fields: the tool suggests a name (autoName) until the user types one (touched) */
  const touched = (input) => input.addEventListener("input", () => input.dataset.touched = "1");
  const autoName = (input, name) => { if (!input.dataset.touched) input.value = String(name).replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 70); };
  /** Device selects: only the devices this computer has (known once the deep-learning add-on is checked) */
  function limitDevices(sel) {
    const have = dlx.status?.devices || ["cpu"];
    [...sel.options].forEach((o) => { if (o.value !== "auto" && !have.includes(o.value)) o.disabled = true; });
  }
  /** a training result's note when the model was still getting better at the last epoch (it would gain from more) */
  function stillImproving(c, more = "") {
    if (!c || c.stopped || !c.best_epoch || !c.epochs_run || c.best_epoch < c.epochs_run - 1) return "";
    return `<div class="warn" style="margin-top:8px">Still improving at the last epoch (best: ${c.best_epoch} of ${c.epochs_run}): train with more epochs for a better model${more ? `, ${more}` : ""}.</div>`;
  }
  /** open a tool and hand its open hook an argument (e.g. a layer or a model to start with) */
  const openTool = (id, arg) => switchTool(id, arg);

  Object.assign(LF, {
    $, $$, esc, fmt, prefs, api, toast, status, map, layers, getLayer, addRasterFromPath, addVectorLayer, saveLayers,
    dataItems, addItem, openItem, tablePoints, trackJob, notCancelled, runJob, runButton, showResult: showRunResult, switchTool, openTool,
    getClip, refreshClipPicker, updateClipHint, startDraw, fillLayers, touched, autoName, limitDevices, stillImproving,
    renderAddon, tipBtn, modelPicker, searchPicker, pickFolder, floatWin,
  });
  LF.tools.forEach((t) => {   // each tool wires its panel once; a broken tool doesn't stop the others
    try { t.hooks = t.setup?.(LF) || {}; } catch (e) { t.hooks = {}; console.error(`Tool ${t.id}:`, e); toast(`The ${t.title} tool couldn't start: ${e.message}`, true); }
  });
