  // Classical ML (tabular data): its tabs and overview (tables, models).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ Classical ML (tabular data) hub
  // Sub-tools are registered here; each has a <div id="ml-sub-<id>" class="ml-sub"> in index.html.
  const ML_SUBTOOLS = [  // ← add sub-tools here (and a <div id="ml-sub-<id>" class="ml-sub"> in index.html)
    { id: "train", group: "sup", title: "Train a model", icon: "ml",
      subtitle: "Random Forest, XGBoost, LightGBM, SVM, Maximum Likelihood and more, with accuracy assessment" },
    { id: "predict", group: "sup", title: "Classify an image", icon: "analyze",
      subtitle: "Apply a trained model (or a saved clustering) to a raster to make a land-cover (or value) map" },
    { id: "cluster", group: "unsup", title: "Clustering", icon: "cluster",
      subtitle: "K-means, hierarchical, DBSCAN, HDBSCAN, spectral clustering, Gaussian mixture: find natural groups" },
    { id: "tsne", group: "unsup", title: "t-SNE map", icon: "tsne",
      subtitle: "See your data as a 2D map where similar rows lie together; colour it by label or cluster" },
  ];
  let mlSub = null;
  function openMlSub(id) {
    mlSub = id;
    $("#ml-home").classList.toggle("hidden", !!id);
    $$("#ml-tabs [data-mltab]").forEach((b) => { const on = b.dataset.mltab === (id || ""); b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
    $$(".ml-sub").forEach((d) => d.classList.toggle("hidden", d.id !== `ml-sub-${id}`));
    const st = ML_SUBTOOLS.find((t) => t.id === id);
    $("#tool-sub").textContent = st ? st.title + " · " + st.subtitle : TOOLS.find((t) => t.id === "ml").subtitle;
    $("#tool-eye").title = $("#tool-sub").textContent;
    if (!id) { refreshTables(); refreshModels(); }
    if (id === "train" && mlx.schema) refreshTrainTables();
    if (id === "predict") refreshPredict();
    if (id === "cluster" || id === "tsne") refreshUnsup(id === "cluster" ? "uc" : "ut").catch((e) => toast(e, true));
    document.querySelector(".tool-body").scrollTop = 0;
  }
  function renderMlHub() {
    const card = (t) => toolEntry("tool-card", `data-mlsub="${t.id}"`, t);
    $("#ml-sup").innerHTML = ML_SUBTOOLS.filter((t) => t.group === "sup").sort(byTitle).map(card).join("");
    $("#ml-unsup").innerHTML = ML_SUBTOOLS.filter((t) => t.group === "unsup").sort(byTitle).map(card).join("");
    $(".ml-group-ic.sup").innerHTML = svg("ml");
    $(".ml-group-ic.unsup").innerHTML = svg("cluster");
    wireEntries($("#ml-home"), "[data-mlsub]", (b) => openMlSub(b.dataset.mlsub));
    // the tool's tabs: an overview (tables, models) and one tab per sub-tool, in the order of a typical workflow
    const order = ["train", "predict", "cluster", "tsne"].map((id) => ML_SUBTOOLS.find((t) => t.id === id)).filter(Boolean);
    $("#ml-tabs").innerHTML = [{ id: "", title: "Overview" }, ...order, ...ML_SUBTOOLS.filter((t) => !order.includes(t))]
      .map((t) => `<button type="button" class="chip" role="tab" data-mltab="${t.id}" title="${esc(t.subtitle || "Your tables and models, and what each tool does")}">${esc(t.title)}</button>`).join("");
    $$("#ml-tabs [data-mltab]").forEach((b) => b.onclick = () => openMlSub(b.dataset.mltab || null));
    $("#ml-goto-rt").onclick = (e) => { e.preventDefault(); switchTool("raster2table"); };
  }
  async function refreshTables() {
    let list = [];
    try { list = await api("/api/tables"); } catch { return; }
    $("#ml-tables").innerHTML = list.length ? list.map((t) => `<div class="ws-row"><span>${esc(t.name)}
        <small>${t.rows != null ? `${t.rows.toLocaleString()} rows × ${t.columns.length} columns` : ""}${t.target ? ` · label: ${esc(t.target)}` : ""}${t.source ? ` · from ${esc(t.source)}` : ""} · ${fmt(t.size_mb, t.size_mb < 1 ? 2 : 1)} MB</small></span>
        <span class="row tight"><button class="btn small" data-tprev="${esc(t.path)}">Preview</button><a class="btn small" href="/api/tables/file?path=${encodeURIComponent(t.path)}" download>⬇</a><button class="btn small danger" data-tdel="${esc(t.path)}" title="Delete">×</button></span></div>`).join("")
      : '<p class="hint">No tables yet. Use <b>Raster → table</b> to create one.</p>';
    $$("[data-tprev]").forEach((b) => b.onclick = () => previewTable(b.dataset.tprev));
    $$("[data-tdel]").forEach((b) => b.onclick = async () => {
      if (!confirm("Delete this table file?")) return;
      await api(`/api/tables?path=${encodeURIComponent(b.dataset.tdel)}`, { method: "DELETE" });
      refreshTables();
    });
  }
  function tableHtml(columns, rows, labelCols = []) {
    const isLbl = (c) => labelCols.includes(c);
    return `<table class="data-table"><tr>${columns.map((c) => `<th class="${isLbl(c) ? "lbl" : ""}">${esc(c)}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr>${r.map((v, i) => `<td class="${isLbl(columns[i]) ? "lbl" : ""}">${v == null || v === "" ? "–" : esc(v)}</td>`).join("")}</tr>`).join("")}</table>`;
  }
  function previewTable(path) {  // tables open in the data viewer under the map (paging, sorting, search, statistics)
    addItem({ kind: "table", name: path.split(/[\\/]/).pop(), path }, { open: true });
  }
