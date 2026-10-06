  // Shared basics: $ / $$, escaping, number formats, state, per-browser prefs, api(), toast / status, and the run bar under each tool (progress, cancel, ⓘ run details).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)


  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 1) => (n == null || isNaN(n) ? "–" : Number(n).toFixed(d));
  // tools in their own files (webapp/static/tools/<menu>/<tool>.js, registered with LF.tool): their panels go in first
  const PLUGINS = Object.fromEntries(LF.tools.map((t) => [t.id, t]));
  LF.tools.forEach((t) => $("#tab-jobs").insertAdjacentHTML("beforebegin", `<section id="tab-${t.id}" class="tabpanel hidden">${t.panel || ""}</section>`));
  const fmtv = (v) => v == null || isNaN(v) ? "–" : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(3);

  const state = {
    config: null, aoi: null, results: null, sort: "cloud",
    activeScene: null, downloadScene: null, polling: null, catalog: null,
  };

  const prefs = (() => { // per-browser conveniences; everything works without them
    let p = {};
    try { p = JSON.parse(localStorage.getItem("lulc-prefs") || "{}"); } catch {}
    return {
      get: (k, d) => (k in p ? p[k] : d),
      set: (k, v) => { p[k] = v; try { localStorage.setItem("lulc-prefs", JSON.stringify(p)); } catch {} },
    };
  })();

  // ------------------------------------------------------------------ friendlier errors
  // Technical messages (from Python, GDAL, the network…) become what happened → what to do. The original text is kept as
  // the error's .detail (the Notifications panel shows it under Details).
  const FRIENDLY = [
    [/failed to fetch|networkerror|load failed|econnrefused|connection (refused|reset)/i, "Can't reach the app's server → it may have stopped: close LULC Fetch and start it again"],
    [/no (coordinate system|crs)|crs is (none|missing)|has no crs|no georeferenc|not georeferenced|missing.*geotransform/i, "This file has no coordinate system, so it can't be placed on the map → add it again with its .prj or world file (e.g. .tfw / .pgw), or use a GeoTIFF that has one"],
    [/not recognized as (a|being in a) supported file format|not a supported file format|rasterioioerror|cannot open.*(tif|raster)/i, "This file can't be read as a raster → check it is a GeoTIFF (.tif) that opens in another GIS, then add it again"],
    [/no space left|enospc|disk (is )?full/i, "The disk is full → free some space, or use File ▸ Clean up working files"],
    [/memoryerror|out of memory|unable to allocate|cannot allocate memory/i, "Not enough memory for this → use a smaller area, fewer bands or a coarser resolution"],
    [/cuda.*out of memory|mps.*out of memory/i, "The graphics card ran out of memory → lower the batch size or the patch size and run it again"],
    [/no module named '?(torch|torchvision|segmentation_models_pytorch)/i, "The deep-learning add-on isn't installed → install it from the tool's panel (Train classify model offers it)"],
    [/no module named '?ultralytics/i, "The YOLO & SAM add-on isn't installed → install it from Detect object"],
    [/\b(401|403)\b|unauthori[sz]ed|forbidden|invalid_grant|invalid credentials|authentication failed/i, "The data service refused your account → check your sign-in in Credentials (top right)"],
    [/\b429\b|too many requests|rate limit/i, "The data service is busy (too many requests) → wait a minute and try again"],
    [/timed? ?out|timeout|read timeout|deadline exceeded/i, "It took too long to answer → check the internet connection, try a smaller area, or try again"],
    [/\b50[234]\b|bad gateway|service unavailable|gateway time/i, "The data service is down for the moment → try again in a few minutes"],
    [/getaddrinfo|name or service not known|nodename nor servname|temporary failure in name resolution|no internet/i, "No internet connection → connect and try again (data you already downloaded still works offline)"],
    [/(no such file or directory|filenotfounderror)/i, "The file can't be found → it was moved, renamed or deleted: add it again with Insert ▸ Add data"],
    [/permission denied|permissionerror|operation not permitted/i, "The app isn't allowed to use that file or folder → choose another folder, or check its permissions"],
    [/^internal server error$|traceback \(most recent call last\)/i, "Something went wrong inside the app → try again; the details are in Help ▸ Error log"],
    [/unexpected token|is not valid json|jsondecodeerror/i, "The app got an answer it couldn't read → try again; if it keeps happening see Help ▸ Error log"],
  ];
  function friendlyError(msg) {
    const text = String(msg ?? "").trim() || "Something went wrong";
    if (text.includes(" → ")) return text;   // already says what to do
    const hit = FRIENDLY.find(([re]) => re.test(text));
    return hit ? hit[1] : text;
  }
  // an Error with the friendly text, the technical one as .detail
  function friendlyErr(msg) {
    const e = new Error(friendlyError(msg));
    if (e.message !== String(msg)) e.detail = String(msg);
    return e;
  }

  // ------------------------------------------------------------------ api / ui helpers
  async function api(path, opts = {}) {
    const init = { ...opts, headers: { ...(opts.headers || {}) } };
    if (opts.json !== undefined) {
      init.body = JSON.stringify(opts.json);
      init.headers["Content-Type"] = "application/json";
    }
    let r;
    try { r = await fetch(path, init); }
    catch (e) { if (e.name === "AbortError") throw e; throw friendlyErr(e.message); }   // the server is unreachable
    const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
    if (!r.ok) {
      let msg = body?.detail ?? body;
      if (Array.isArray(msg)) msg = msg.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
      throw friendlyErr(typeof msg === "string" && msg.trim() ? msg : `${r.status} ${r.statusText}`);
    }
    return body;
  }

  // every message is also kept for the Notifications panel (the bell in the status bar); errors say what to do next
  const notes = { list: (() => { try { return JSON.parse(localStorage.getItem("lulc-notes") || "[]"); } catch { return []; } })(), onChange: null };
  function toast(msg, err = false, { detail } = {}) {
    if (msg instanceof Error) { detail = msg.detail; msg = msg.message; }
    if (err) { const f = friendlyError(msg); if (f !== msg) { detail ||= msg; msg = f; } }
    const t = document.createElement("div");
    t.className = "toast" + (err ? " err" : "");
    t.textContent = msg;
    $("#toasts").append(t);
    setTimeout(() => t.remove(), err ? 7000 : 3500);
    notes.list.unshift({ t: Date.now(), msg: String(msg), err: !!err, ...(detail ? { detail: String(detail).slice(0, 2000) } : {}), unread: true });
    notes.list.length = Math.min(notes.list.length, 60);
    try { localStorage.setItem("lulc-notes", JSON.stringify(notes.list)); } catch {}
    notes.onChange?.();
  }

  async function busy(btn, label, fn) {
    const old = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>${label}`;
    try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = old; }
  }

  let msgTimer;
  function status(msg, sticky = false) {
    $("#sb-msg").textContent = msg;
    clearTimeout(msgTimer);
    if (!sticky) msgTimer = setTimeout(() => $("#sb-msg").textContent = "Ready", 5000);
  }

  // ---------------- progress + cancel bar (bottom of the tool panel), one run per tool
  class CancelledError extends Error { constructor() { super("Cancelled"); this.cancelled = true; } }
  const runs = {};  // tool id -> { title, progress (0–1 or null = unknown), message, started, cancel() }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // The run bar under every tool: progress, time taken, ⓘ details (times, every step in a resizable box), and after a
  // failure the reason, Copy details and the error log (every failed run is also written to logs/errors.log).
  function renderRunBar() {
    const r = runs[currentTool], bar = $("#run-bar");
    bar.classList.toggle("hidden", !r);
    if (!r) { showRunFloat(false); return; }
    const st = r.state || "running";
    bar.classList.toggle("failed", st === "failed");
    bar.classList.toggle("done", st === "done");
    const secs = ((r.finished || Date.now()) - r.started) / 1000;
    const known = r.progress != null;
    const pct = st === "done" ? 100 : known ? Math.round(r.progress * 100) : null;
    const eta = st === "running" && known && r.progress > 0.05 && r.progress < 1 ? `~${fmtSecs(secs * (1 - r.progress) / r.progress)} left` : "";
    $("#rb-title").textContent = st === "failed" ? `Failed: ${r.title}` : st === "done" ? `✓ ${r.title}` : r.title;
    $("#rb-time").textContent = fmtClock(secs);
    $("#rb-pct").textContent = st === "failed" ? "" : pct != null ? `${pct}%` : "";
    $("#rb-fill").classList.toggle("indeterminate", st === "running" && !known);
    $("#rb-fill").style.width = pct != null ? `${Math.max(2, pct)}%` : st === "failed" ? "100%" : "";
    $("#rb-msg").textContent = st === "failed" ? r.error : st === "done" ? `Finished in ${fmtSecs(secs)}` : `${r.message || "Working…"}${eta ? ` · ${eta}` : ""}`;
    $("#rb-msg").title = $("#rb-msg").textContent;
    const btn = $("#rb-cancel");
    btn.disabled = st === "running" && !!r.cancelling;
    btn.textContent = st !== "running" ? "Close" : r.cancelling ? "Cancelling…" : "Cancel";
    btn.classList.toggle("danger", st === "running");
    // the details window: open when you chose so (ⓘ), and once by itself when a run fails, unless you closed it for that run
    if (st === "failed" && !r.autoOpened) { r.autoOpened = true; if (!r.userClosed) prefs.set("rb-open", true); }
    const open = prefs.get("rb-open", false) && !r.userClosed;
    showRunFloat(open);
    $("#rb-more").setAttribute("aria-expanded", open);
    if (open) {
      const f = $("#rb-float");
      f.classList.toggle("failed", st === "failed"); f.classList.toggle("done", st === "done");
      $("#rb-float-title").textContent = $("#rb-title").textContent;
      renderRunDetails(r, st, secs, eta, pct);
    }
  }
  // floating window: position and size are remembered; it always stays (partly) on screen
  function showRunFloat(on) {
    const f = $("#rb-float");
    if (!on) { f.classList.add("hidden"); return; }
    if (f.classList.contains("hidden")) {
      f.classList.remove("hidden");
      const box = prefs.get("rb-float", null);
      const map = $("#map").getBoundingClientRect();
      const w = box?.w || 440, h = box?.h || 340;
      f.style.width = `${w}px`; f.style.height = `${h}px`;
      placeRunFloat(box ? box.x : map.right - w - 16, box ? box.y : map.top + 16);
    }
  }
  function placeRunFloat(x, y) {
    const f = $("#rb-float"), w = f.offsetWidth, h = f.offsetHeight;
    x = Math.min(Math.max(x, 8 - w + 120), innerWidth - 120);   // at least 120 px stay visible
    y = Math.min(Math.max(y, 8), innerHeight - 40);
    f.style.left = `${x}px`; f.style.top = `${y}px`;
  }
  const saveRunFloat = () => { const f = $("#rb-float"); if (!f.classList.contains("hidden")) prefs.set("rb-float", { x: f.offsetLeft, y: f.offsetTop, w: f.offsetWidth, h: f.offsetHeight }); };
  { const head = $("#rb-float-head"), f = $("#rb-float");
    head.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button")) return;
      e.preventDefault();
      const dx = e.clientX - f.offsetLeft, dy = e.clientY - f.offsetTop;
      head.setPointerCapture(e.pointerId);
      const move = (ev) => placeRunFloat(ev.clientX - dx, ev.clientY - dy);
      const up = () => { head.removeEventListener("pointermove", move); head.removeEventListener("pointerup", up); saveRunFloat(); };
      head.addEventListener("pointermove", move);
      head.addEventListener("pointerup", up);
    });
    new ResizeObserver(() => saveRunFloat()).observe(f);
    addEventListener("resize", () => { if (!f.classList.contains("hidden")) placeRunFloat(f.offsetLeft, f.offsetTop); });
    $("#rb-float-x").onclick = () => { prefs.set("rb-open", false); const r = runs[currentTool]; if (r) r.userClosed = true; renderRunBar(); };
  }
  function renderRunDetails(r, st, secs, eta, pct) {
    const clock = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    $("#rb-times").innerHTML = `Started <b>${clock(r.started)}</b> · ${st === "running" ? "running for" : "took"} <b>${fmtSecs(secs)}</b>` +
      (st !== "running" ? ` · ended <b>${clock(r.finished)}</b>` : "") + (eta ? ` · <b>${eta}</b>` : "") + (st === "running" && pct != null ? ` · ${pct}% done` : "");
    $("#rb-full").innerHTML = st === "failed" ? `<b style="color:var(--err)">Why it failed:</b> ${esc(r.error)}`
      : st === "done" ? "" : esc(r.message || "Working…");
    const log = $("#rb-log"), lines = r.logs || [];
    if (log.dataset.n !== String(lines.length) || log.dataset.run !== String(r.started)) {
      const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
      log.innerHTML = lines.length ? lines.map((x) => /\bERROR\b|Traceback|Error:/.test(x) ? `<span class="err">${esc(x)}</span>` : esc(x)).join("\n")
        : esc(r.jobId ? "Waiting for the first step…" : "This action has no step log: only its result or error is shown.");
      log.dataset.n = lines.length; log.dataset.run = r.started;
      if (atEnd || log.dataset.fresh !== String(r.started)) { log.scrollTop = log.scrollHeight; log.dataset.fresh = r.started; }
    }
    const acts = $("#rb-actions");
    if (acts.dataset.state !== `${r.started}:${st}`) {
      acts.dataset.state = `${r.started}:${st}`;
      acts.innerHTML = st === "failed" ? `<button class="btn small" data-rb="copy">Copy details</button><button class="btn small" data-rb="log">Open error log</button>
        <button class="btn small ghost" data-rb="reveal">Show error log file</button>` : "";
      $$("[data-rb]", acts).forEach((b) => b.onclick = () => runAction(b.dataset.rb));
    }
  }
  function runDetailsText(r) {
    const secs = ((r.finished || Date.now()) - r.started) / 1000;
    return [`${r.state === "failed" ? "FAILED" : r.state === "done" ? "Finished" : "Running"}: ${r.title}`, r.error ? `Error: ${r.error}` : "",
            `Started ${new Date(r.started).toLocaleString()} · took ${fmtSecs(secs)}`, "", ...(r.logs || [])].filter((x, i) => x || i === 3).join("\n");
  }
  async function runAction(what) {
    const r = runs[currentTool];
    if (what === "copy" && r) {
      try { await navigator.clipboard.writeText(runDetailsText(r)); toast("Details copied"); } catch { toast("Couldn't copy: select the text in the box instead", true); }
    } else if (what === "log") window.open("/api/errors/file", "_blank", "noopener");
    else if (what === "reveal") api("/api/errors/reveal", { method: "POST" }).catch((e) => toast(e, true));
  }
  const fmtSecs = (s) => s < 60 ? `${s < 10 ? s.toFixed(1) : Math.round(s)} s` : s < 3600 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`
    : `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
  const fmtClock = (s) => { s = Math.floor(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = String(s % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
  $("#rb-cancel").onclick = () => {
    const r = runs[currentTool];
    if (!r) return;
    if (r.state) { delete runs[currentTool]; renderRunBar(); return; }   // finished or failed: Close
    if (!r.cancelling) { r.cancelling = true; r.message = "Cancelling…"; renderRunBar(); r.cancel(); }
  };
  $("#rb-more").onclick = () => {   // ⓘ opens / closes the details window
    const r = runs[currentTool], open = prefs.get("rb-open", false) && !r?.userClosed;
    prefs.set("rb-open", !open);
    if (r) r.userClosed = open;
    renderRunBar();
  };
  setInterval(() => { const r = runs[currentTool]; if (r && !r.state) renderRunBar(); }, 1000);  // keep the time ticking
