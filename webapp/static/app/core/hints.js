  // ⓘ hints and compact hints (long explanations behind ⓘ).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ ⓘ hints (hover ~0.5 s, or click to pin)
  let tipTimer = null, tipFor = null;
  function showTip(btn, pinned) {
    const pop = $("#tip-pop");
    pop.innerHTML = esc(btn.dataset.tip);
    pop.classList.remove("hidden");
    pop.classList.toggle("pinned", pinned);
    $$(".tip.pinned").forEach((t) => t !== btn && t.classList.remove("pinned"));
    btn.classList.toggle("pinned", pinned);
    const r = btn.getBoundingClientRect(), pr = pop.getBoundingClientRect();
    let left = r.left + r.width / 2 - pr.width / 2, top = r.bottom + 8;
    if (top + pr.height > innerHeight - 8) top = r.top - pr.height - 8;
    pop.style.left = Math.max(8, Math.min(left, innerWidth - pr.width - 8)) + "px";
    pop.style.top = Math.max(8, top) + "px";
    tipFor = btn;
  }
  function hideTip(force = false) {
    clearTimeout(tipTimer);
    if (!force && tipFor?.classList.contains("pinned")) return;
    $("#tip-pop").classList.add("hidden");
    $$(".tip.pinned").forEach((t) => t.classList.remove("pinned"));
    tipFor = null;
  }
  document.addEventListener("mouseover", (e) => {
    const b = e.target.closest?.(".tip");
    if (!b || tipFor?.classList.contains("pinned")) return;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => showTip(b, false), 450);
  });
  document.addEventListener("mouseout", (e) => { if (e.target.closest?.(".tip")) hideTip(); });
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".tip");
    if (b) { e.preventDefault(); e.stopPropagation(); b.classList.contains("pinned") ? hideTip(true) : showTip(b, true); return; }
    if (!e.target.closest("#tip-pop")) hideTip(true);
  }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(true); });
  const tipBtn = (text) => `<button type="button" class="tip" data-tip="${esc(text)}" aria-label="Help">i</button>`;

  // ------------------------------------------------------------------ compact hints: a clean panel, details behind ⓘ
  // Any hint in the tool panel longer than about a line shows only its first short sentence; the rest is behind an ⓘ (click
  // or hover). Kept as they are: warnings / errors (coloured) and hints with links or buttons. Applies to every tool, also
  // to hints the tools rewrite later (a MutationObserver).
  const HINT_MAX = 95;
  function hintLead(text) {
    const m = text.match(/^(.{20,90}?[.:;!?])(\s|$)/);
    if (m) return m[1].replace(/[:;]$/, "");
    const cut = text.slice(0, 80);
    return cut.slice(0, Math.max(cut.lastIndexOf(" "), 40)).replace(/[,;:·(–-]\s*$/, "") + "…";
  }
  function compactHint(el) {
    if (el.dataset.compactHtml === el.innerHTML || el.classList.contains("keep")) return;
    if (el.querySelector("a, button:not(.hint-tip), input, select, textarea, details, [style*='--warn'], [style*='--err'], .pill")) return;
    if (/var\(--(warn|err)\)/.test(el.getAttribute("style") || "")) return;
    const full = el.textContent.replace(/\s+/g, " ").trim();
    if (full.length <= HINT_MAX) return;
    el.innerHTML = `${esc(hintLead(full))} <button type="button" class="tip hint-tip" data-tip="${esc(full)}" aria-label="More about this" title="More">i</button>`;
    el.dataset.compactHtml = el.innerHTML;
  }
  { const panel = $(".tool-body");
    const run = () => $$(".hint", panel).forEach(compactHint);
    let queued = false;
    new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; run(); }); } })
      .observe(panel, { childList: true, subtree: true, characterData: true });
    run(); }
