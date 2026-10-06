  // About (click the logo, or Help ▸ About): the version, how the app runs (desktop app or from source, with its git
  // branch and commit), the computer, library versions, add-ons, accounts, folders; Copy details for a bug report;
  // Check for updates asks GitHub for the latest release.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ About
  const REPO = "agritechixr/lulc-fetch";
  const about = { info: null };
  // "0.0.2b0" → "0.0.2 beta"
  const prettyVersion = (v) => String(v || "").replace(/b(\d+)$/, (_, n) => ` beta${+n ? " " + n : ""}`).replace(/a(\d+)$/, " alpha").replace(/rc(\d+)$/, " RC $1");

  async function openAbout() {
    $("#dlg-about").showModal();
    $("#about-update").textContent = "";
    try {
      about.info = await api("/api/about");
      renderAbout();
    } catch (e) { $("#about-body").innerHTML = `<p class="hint" style="color:var(--err)">${esc(e.message)}</p>`; }
  }
  // the browser side: what the server can't know
  function clientInfo() {
    const ua = navigator.userAgent, b = /Edg\/([\d.]+)/.exec(ua) ? `Edge ${RegExp.$1}` : /Chrome\/([\d.]+)/.exec(ua) ? `Chrome ${RegExp.$1}`
      : /Firefox\/([\d.]+)/.exec(ua) ? `Firefox ${RegExp.$1}` : /Version\/([\d.]+).*Safari/.exec(ua) ? `Safari ${RegExp.$1}` : ua;
    return { browser: b, screen: `${screen.width} × ${screen.height} @${devicePixelRatio}x · window ${innerWidth} × ${innerHeight}`,
             leaflet: L.version, three: v3.T ? `r${v3.T.REVISION}` : "r169 (loads with the first 3D map)",
             project: inProject() ? `${proj.info.project.name} (${proj.info.project.folder})` : "temporary workspace",
             maps: `${docs.list.length} (${docs.list.filter((d) => d.kind === "3d").length} 3D)`, theme: prefs.get("theme", "auto") };
  }
  function renderAbout() {
    const a = about.info, c = clientInfo(), g = a.run.git, dl = a.addons.deep_learning, yolo = a.addons.yolo;
    const rows = (list) => `<table class="about-kv">${list.filter((r) => r && r[1] != null && r[1] !== "").map(([k, v, raw]) =>
      `<tr><td>${esc(k)}</td><td>${raw ? v : esc(v)}</td></tr>`).join("")}</table>`;
    const ok = (on, yes, no) => `<span class="pill ${on ? "c0" : "c1"}">${esc(on ? yes : no)}</span>`;
    const folder = (p) => `<code>${esc(p)}</code> <button type="button" class="btn small ghost" data-reveal="${esc(p)}" title="Show in Finder / Explorer">Show</button>`;
    $("#about-body").innerHTML = `
      <div class="about-hero"><span class="about-logo"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg></span>
        <div><h2>${esc(a.name)} <span class="about-ver">${esc(prettyVersion(a.version))}</span></h2>
          <p class="hint" style="margin:2px 0 0">Satellite imagery, land cover, indices, machine learning and 2D / 3D maps on your own computer · free and open source (Apache-2.0) · by IXR</p>
          <p style="margin:6px 0 0">${ok(true, a.run.mode, "")} ${g ? ok(!g.changed, `${g.branch} · ${g.commit}${g.changed ? " + local changes" : ""}`, `${g.branch} · ${g.commit} + local changes`) : ""}</p></div></div>
      <div class="about-sec">Version</div>${rows([["Version", `${prettyVersion(a.version)} (${a.version})`], ["Runs as", a.run.frozen ? "Desktop app (packaged)" : "From source (Python)"],
        g ? ["Code", `branch ${g.branch} · commit ${g.commit} (${g.date})${g.changed ? " · with changes not yet committed" : ""}`] : null,
        ["Server", `${a.run.server} · process ${a.run.pid}`], ["Project", c.project], ["Maps", c.maps]])}
      <div class="about-sec">Computer</div>${rows([["System", `${a.system.os} · ${a.system.arch}`], ["Processor", a.system.cpus ? `${a.system.cpus} cores` : null],
        ["Memory", a.system.memory_gb ? `${a.system.memory_gb} GB` : null], ["Python", a.system.python], ["Python program", a.system.executable],
        ["Browser", c.browser], ["Screen", c.screen]])}
      <div class="about-sec">Add-ons</div>${rows([
        ["Deep learning (PyTorch)", dl.installed ? `${ok(true, "installed", "")} PyTorch ${esc(dl.torch || "?")}${dl.smp ? ` · segmentation-models ${esc(dl.smp)}` : ""}${dl.device ? ` · runs on ${esc(String(dl.device).toUpperCase())}` : ""}` : ok(false, "", "not installed (Analysis ▸ Tools ▸ Train classify model offers it)"), true],
        ["YOLO & SAM (ultralytics)", yolo?.available ? `${ok(true, "installed", "")} ${esc(yolo.version || "")}` : ok(false, "", "not installed"), true]])}
      ${Object.keys(a.accounts || {}).length ? `<div class="about-sec">Accounts</div>${rows(Object.values(a.accounts).map((x) => [x.title, ok(x.connected, "connected", "not set"), true]))}` : ""}
      <div class="about-sec">Folders</div>${rows([["Workspace", folder(a.folders.workspace), true], ["App data", folder(a.folders.data), true],
        ["Settings", folder(a.folders.settings), true], ["Logs", folder(a.folders.logs), true]])}
      <details class="about-libs"><summary>Libraries</summary>${rows([...Object.entries(a.libraries).map(([k, v]) => [k, v || "not installed"]),
        ["Leaflet (2D maps)", c.leaflet], ["three.js (3D maps)", c.three]])}</details>
      <div class="about-links"><a href="https://agritechixr.github.io/lulc-fetch/" target="_blank" rel="noopener">Website</a>
        <a href="https://github.com/${REPO}" target="_blank" rel="noopener">Source code</a>
        <a href="https://github.com/${REPO}/releases" target="_blank" rel="noopener">Releases</a>
        <a href="https://github.com/${REPO}/blob/main/CHANGELOG.md" target="_blank" rel="noopener">What's new</a>
        <a href="https://github.com/${REPO}/issues" target="_blank" rel="noopener">Report a problem</a>
        <a href="https://github.com/${REPO}/blob/main/LICENSE" target="_blank" rel="noopener">Licence</a></div>`;
    $$("[data-reveal]", $("#about-body")).forEach((b) => b.onclick = () =>
      api("/api/project/reveal", { method: "POST", json: { path: b.dataset.reveal } }).catch((e) => toast(e, true)));
  }
  // the details as plain text, for a bug report
  function aboutText() {
    const a = about.info, c = clientInfo(), g = a.run.git, dl = a.addons.deep_learning;
    return [`${a.name} ${prettyVersion(a.version)} (${a.version}) · ${a.run.mode}${g ? ` · ${g.branch}@${g.commit} ${g.date}${g.changed ? " (local changes)" : ""}` : ""}`,
      `System: ${a.system.os} · ${a.system.arch} · ${a.system.cpus} cores · ${a.system.memory_gb ?? "?"} GB`,
      `Python: ${a.system.python}`, `Browser: ${c.browser} · ${c.screen}`,
      `Libraries: ${Object.entries(a.libraries).map(([k, v]) => `${k} ${v || "-"}`).join(", ")}, Leaflet ${c.leaflet}, three.js ${c.three}`,
      `Deep learning: ${dl.installed ? `PyTorch ${dl.torch}${dl.device ? ` on ${dl.device}` : ""}` : "not installed"} · YOLO: ${a.addons.yolo?.available ? a.addons.yolo.version : "not installed"}`,
      `Maps: ${c.maps} · project: ${inProject() ? "yes" : "temporary workspace"}`].join("\n");
  }
  $("#about-copy").onclick = async () => {
    if (!about.info) return;
    try { await navigator.clipboard.writeText(aboutText()); toast("Copied: paste it into a bug report"); } catch { toast("Couldn't copy", true); }
  };
  // the latest release on GitHub (asked only when the button is pressed)
  $("#about-check").onclick = async (e) => {
    const out = $("#about-update"), btn = e.currentTarget;
    btn.disabled = true; out.textContent = "Checking…";
    try {
      const r = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, { headers: { Accept: "application/vnd.github+json" } });
      if (!r.ok) throw new Error(r.status === 404 ? "no release published yet" : `GitHub answered ${r.status}`);
      const rel = await r.json(), latest = String(rel.tag_name || "").replace(/^v/, "");
      // "v0.0.2-beta" and "0.0.2b0" are the same version
      const norm = (v) => String(v).toLowerCase().replace(/^v/, "").replace(/[-\s]?beta[.\s]?/, "b").replace(/b0?$/, "b").replace(/\s+/g, "");
      const mine = about.info?.version || "";
      out.innerHTML = norm(latest) === norm(mine)
        ? `✓ You have the latest version (${esc(prettyVersion(mine))})`
        : `A newer version is out: <a href="${esc(rel.html_url)}" target="_blank" rel="noopener">${esc(rel.name || latest)}</a>`;
    } catch (x) { out.textContent = `Couldn't check: ${x.message}`; }
    finally { btn.disabled = false; }
  };
  $("#btn-about").onclick = (e) => { e.stopPropagation(); openAbout(); };
  // the version next to the name in the menu bar
  api("/api/about").then((a) => { about.info = a; $("#btn-about").title = `LULC Fetch ${prettyVersion(a.version)}: about this version, the environment, folders`; }).catch(() => {});
