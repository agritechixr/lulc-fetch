  // Downloads & jobs tool: background jobs, their logs and files.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ downloads & jobs
  const openLogs = new Set();
  const addedJobs = new Set(prefs.get("addedJobs", []));
  let jobsSeen = false;
  async function refreshJobs() {
    let list;
    try { list = await api("/api/jobs"); } catch { return; }
    const running = list.filter((j) => j.status === "running" || j.status === "queued").length;
    $("#sb-jobs").classList.toggle("hidden", !running);
    const live = list.filter((j) => j.status === "running");
    const avg = live.length ? Math.round(100 * live.reduce((a, j) => a + j.progress, 0) / live.length) : 0;
    $("#sb-jobs").innerHTML = running ? `<span class="spinner"></span>${running} job${running > 1 ? "s" : ""} running · ${avg}%` : "";
    // Downloads that finish while the page is open go straight into Contents. Older ones (e.g. from a
    // previous session) are not re-added: they are listed under Workspace. PCA jobs add their own layer.
    if (!jobsSeen) {
      list.filter((j) => j.status === "done").forEach((j) => addedJobs.add(j.id));
      jobsSeen = true;
    }
    for (const j of list) {
      // only Find imagery downloads (scenes, composites, land-cover labels, original products) are added here: every other
      // tool adds its own results, with their own colours and names (an allow list, so a new tool never gets a duplicate)
      if (j.status !== "done" || addedJobs.has(j.id) || !["scene", "composite", "labels", "product"].includes(j.kind)) continue;
      addedJobs.add(j.id);
      prefs.set("addedJobs", [...addedJobs].slice(-200));
      j.files.filter((f) => /\.tiff?$/i.test(f)).forEach((f) =>
        addRasterFromPath(`downloads/${j.id}/${f}`, { name: j.title }).then(() => toast(`Added “${j.title}” to Contents`)));
    }
    list = list.filter((j) => j.kind !== "export");  // quick layer exports are not listed here
    $("#jobs-empty").classList.toggle("hidden", list.length > 0);
    $("#jobs").innerHTML = list.map((j) => {
      const elapsed = Math.round(((j.finished || Date.now() / 1000) - (j.started || j.created)));
      const files = j.files.map((f) => `<a href="/api/jobs/${j.id}/files/${encodeURIComponent(f)}" download><span>⬇ ${esc(f)}</span>${/\.tiff?$/i.test(f) ? `<button class="btn small" data-addmap="downloads/${j.id}/${esc(f)}" data-name="${esc(j.title)}">Add to map</button>` : ""}</a>`).join("");
      const png = j.files.find((f) => f.endsWith(".png"));
      const dist = j.result?.distribution ? `<div class="dist">${j.result.distribution.slice(0, 11).map((d) =>
        `<div><i style="background:${esc(d.color)}"></i><span>${esc(d.name)}</span><b>${fmt(d.pct)}%</b></div>`).join("")}</div>` : "";
      const facts = [j.params.grid, j.result?.date && `date ${j.result.date}`, j.result?.dates && `${j.result.dates.length} dates`,
        j.result?.valid_pct != null && `${fmt(j.result.valid_pct)}% valid pixels`].filter(Boolean).join(" · ");
      const showLogs = j.status === "error" || openLogs.has(j.id);
      const live = j.status === "running" || j.status === "queued";
      return `<div class="job">
        <h4><span>${esc(j.title)}</span><span class="status ${j.status}">${j.status === "running" ? '<span class="spinner"></span>' : ""}${j.status}</span></h4>
        <div class="sub">${esc(j.params.source || "")} · ${elapsed}s${facts ? " · " + esc(facts) : ""}</div>
        ${live ? `<div class="rb-track"><div class="rb-fill" style="width:${Math.max(2, Math.round(j.progress * 100))}%"></div></div>
          <div class="sub">${Math.round(j.progress * 100)}% · ${esc((j.message || "Starting…").replace(/^\d\d:\d\d:\d\d\s+/, ""))}</div>` : ""}
        ${j.error ? `<div class="warn">${esc(j.error)}</div>` : ""}
        ${png ? `<img class="result" src="/api/jobs/${j.id}/files/${encodeURIComponent(png)}?t=${j.finished || ""}" alt="Result preview">` : ""}
        ${dist}
        ${files ? `<div class="files">${files}</div>` : ""}
        <details data-job="${j.id}" ${showLogs ? "open" : ""}><summary>Log (${j.logs.length} lines)</summary><pre>${esc(j.logs.join("\n"))}</pre></details>
        <div class="row">${live ? `<button class="btn small danger" data-cancel="${j.id}">Cancel</button>` : `<button class="btn small danger" data-del="${j.id}">Delete files</button>`}</div>
      </div>`;
    }).join("");
    $$("#jobs details").forEach((d) => d.addEventListener("toggle", () => d.open ? openLogs.add(d.dataset.job) : openLogs.delete(d.dataset.job)));
    $$("#jobs pre").forEach((p) => p.scrollTop = p.scrollHeight);
    $$("[data-addmap]").forEach((b) => b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); addRasterFromPath(b.dataset.addmap, { name: b.dataset.name }); });
    $$("[data-del]").forEach((b) => b.onclick = async () => { await api(`/api/jobs/${b.dataset.del}`, { method: "DELETE" }); refreshJobs(); });
    $$("[data-cancel]").forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = "Cancelling…"; await api(`/api/jobs/${b.dataset.cancel}/cancel`, { method: "POST" }); refreshJobs(); });
    clearTimeout(state.polling);
    if (running) state.polling = setTimeout(refreshJobs, 1500);
  }
