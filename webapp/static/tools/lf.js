/* LF: the shared core ("universe") that every tool file uses.

   A tool lives in its own file, webapp/static/tools/<menu>/<tool>.js, and registers itself:

     LF.tool({
       id: "embconvert", menu: "embed", title: "Convert embeddings", icon: "convert", subtitle: "…",
       kinds: ["embconvert"],                        // job kinds it starts (History links them to this tool)
       save: [["embconvert", "#ec-run", "the converted GeoTIFF"]],   // "Also save to a folder" options: key, before, what
       clip: { "ec-area": { what: "layer is converted" } },          // area pickers (<select id="ec-area"> + #ec-area-hint)
       panel: `<div class="card">…</div>`,           // the tool's panel (HTML)
       setup(LF) {                                   // runs once, when the app has started
         const { $, api, toast } = LF;
         …
         return { open(arg) {}, layersChanged() {}, clipChanged(id) {} };   // hooks, all optional
       },
     });

   setup(LF) receives the app's shared helpers (filled in by static/app/core/universe.js):
     $ $$ esc fmt prefs                    DOM lookup, HTML escaping, numbers, per-browser settings
     api toast status                      server calls, messages
     map layers getLayer addRasterFromPath addVectorLayer addOnlineLayer zoomTo saveLayers   the map and Contents
     dataItems addItem openItem            tables and pictures in Contents
     trackJob notCancelled runJob runButton   background jobs (progress, ⓘ details, History, error log)
     switchTool openTool                   open a tool (openTool passes an argument to its open hook)
     getClip refreshClipPicker updateClipHint startDraw   areas and drawing
     fillLayers autoName touched limitDevices showResult   form helpers used by most tools
     renderAddon tipBtn modelPicker searchPicker pickFolder floatWin   widgets
   Data shared by the tools of one menu goes in LF.<menu> (e.g. tools/embeddings/common.js → LF.emb).

   LF.html holds small HTML builders for panels (they run before the app starts, so they use nothing else). */
(() => {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  window.LF = {
    tools: [],
    tool(def) {
      if (!def.id || !def.title) throw new Error("LF.tool needs at least an id and a title");
      this.tools.push(def);
    },
    html: {
      /** the ⓘ button: its text shows on hover or click */
      tip: (text) => `<button type="button" class="tip" data-tip="${esc(text)}" aria-label="Help">i</button>`,
      /** a "Device" select (Auto / Apple GPU / NVIDIA GPU / CPU) */
      device: (id) => `<label>Device<select id="${id}"><option value="auto">Auto</option><option value="mps">Apple GPU (MPS)</option><option value="cuda">NVIDIA GPU (CUDA)</option><option value="cpu">CPU</option></select></label>`,
      /** an area picker: <select id> + its hint; LF.tool({clip}) wires it */
      area: (id, label = "Area", tip = "") => `<div class="area-pick"><label>${label} ${tip ? LF.html.tip(tip) : ""}<select id="${id}"></select></label><p class="hint" id="${id}-hint"></p></div>`,
      /** an optional "Resampling" select (the methods of lulc_fetch/resample.py); value "" = the tool's default.
          opts: { label, auto: text of the default option, only: [names] to offer a subset } */
      resampling: (id, opts = {}) => {
        const all = LF.html.RESAMPLING.filter(([n]) => !opts.only || opts.only.includes(n));
        return `<label>${opts.label || "Resampling"} ${LF.html.tip("How pixel values are computed on the new grid. Nearest keeps exact values (classes, labels); bilinear is smooth; cubic (bicubic) and lanczos are sharper for imagery, good before computer vision or embeddings; average / mode when making pixels bigger.")}
          <select id="${id}"><option value="">${esc(opts.auto || "Default (the usual choice)")}</option>${all.map(([n, t, about]) => `<option value="${n}" title="${esc(about)}">${esc(t)}</option>`).join("")}</select></label>`;
      },
      RESAMPLING: [
        ["nearest", "Nearest neighbour", "Keeps the exact values. For classes, labels and masks."],
        ["bilinear", "Bilinear", "Smooth, from the 4 nearest pixels. For reflectance, NDVI, heights."],
        ["cubic", "Cubic (bicubic)", "From 16 pixels; sharper than bilinear. Good for imagery and enlarging."],
        ["cubic_spline", "Cubic spline", "Very smooth curves; for elevation and smooth surfaces."],
        ["lanczos", "Lanczos", "The sharpest for enlarging imagery; may add slight halos at edges."],
        ["average", "Average", "The mean of the pixels covered. For making pixels bigger (continuous data)."],
        ["mode", "Mode (majority)", "The most common value. For making class-map pixels bigger."],
        ["med", "Median", "The median of the pixels covered: robust to outliers."],
        ["min", "Minimum", "The minimum of the pixels covered."],
        ["max", "Maximum", "The maximum of the pixels covered."],
        ["q1", "First quartile", "The 25th percentile of the pixels covered."],
        ["q3", "Third quartile", "The 75th percentile of the pixels covered."],
      ],
      /** the deep-learning add-on card: the rest of the panel goes in .dl-body (shown once PyTorch is installed) */
      addon: () => `<div class="dl-addon card hidden" data-addon></div>`,
      /** the run button, its error box and the result box: #<p>-run, #<p>-error, #<p>-result (see LF.runButton) */
      run: (p, label) => `<button class="btn primary big-btn" id="${p}-run">${label}</button><div id="${p}-error" class="warn err hidden"></div>`,
    },
  };
})();
