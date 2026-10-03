  // Model picker: a dropdown with one row per model.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ model picker: a dropdown with one row per model
  // items: [{id, title, group?, badge?, meta? (html), tip?, disabled?, why?}]; each row has an ⓘ with the model's description
  function modelPicker(el, { items, value, onChange, placeholder = "Choose…" }) {
    const sel = items.find((i) => i.id === value);
    const row = (it) => `<div role="option" tabindex="-1" class="mp-row ${it.id === value ? "on" : ""} ${it.disabled ? "off" : ""}" data-id="${esc(it.id)}"
        aria-selected="${it.id === value}" aria-disabled="${!!it.disabled}" ${it.why ? `title="${esc(it.why)}"` : ""}>
        <span class="mp-main"><b>${esc(it.title)}</b>${it.badge ? `<span class="mp-badge">${esc(it.badge)}</span>` : ""}${it.disabled && it.why ? `<small class="mp-why">${esc(it.why)}</small>` : ""}</span>
        <span class="mp-meta">${it.meta || ""}</span>${it.tip ? tipBtn(it.tip) : '<span class="mp-notip"></span>'}</div>`;
    let lastGroup = null;
    const rows = items.map((it) => { const h = it.group && it.group !== lastGroup ? `<div class="mp-group">${esc(it.group)}</div>` : ""; lastGroup = it.group || lastGroup; return h + row(it); }).join("");
    el.classList.add("mp");
    el.innerHTML = `<div class="mp-head"><button type="button" class="mp-btn" aria-haspopup="listbox" aria-expanded="false">
        <span class="mp-main">${sel ? `<b>${esc(sel.title)}</b>${sel.badge ? `<span class="mp-badge">${esc(sel.badge)}</span>` : ""}` : `<span class="hint">${esc(placeholder)}</span>`}</span>
        <span class="mp-meta">${sel?.meta || ""}</span><span class="mp-caret">▾</span></button>${sel?.tip ? tipBtn(sel.tip) : ""}</div>
      <div class="mp-list hidden" role="listbox">${rows}</div>`;
    const btn = $(".mp-btn", el), list = $(".mp-list", el);
    const open = (on) => {
      $$(".mp-list:not(.hidden)").forEach((l) => l !== list && l.classList.add("hidden"));
      list.classList.toggle("hidden", !on);
      btn.setAttribute("aria-expanded", on);
      if (on) { const cur = $(".mp-row.on", list) || $(".mp-row:not(.off)", list); cur?.focus(); cur?.scrollIntoView({ block: "nearest" }); }
    };
    const choose = (r) => { if (!r || r.classList.contains("off")) return; open(false); if (r.dataset.id !== value) onChange(r.dataset.id); btn.focus(); };
    btn.onclick = () => open(list.classList.contains("hidden"));
    $$(".mp-row", list).forEach((r) => { r.onclick = () => choose(r); });
    list.onkeydown = (e) => {
      const rs = $$(".mp-row:not(.off)", list), i = rs.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); rs[Math.min(rs.length - 1, i + 1)]?.focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); rs[Math.max(0, i - 1)]?.focus(); }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(document.activeElement.closest(".mp-row")); }
      else if (e.key === "Escape") { e.preventDefault(); open(false); btn.focus(); }
    };
    btn.onkeydown = (e) => { if (e.key === "ArrowDown") { e.preventDefault(); open(true); } };
  }
  document.addEventListener("click", (e) => { if (!e.target.closest(".mp")) $$(".mp-list:not(.hidden)").forEach((l) => { l.classList.add("hidden"); l.previousElementSibling?.querySelector(".mp-btn")?.setAttribute("aria-expanded", "false"); }); });
  const descTip = (...parts) => parts.filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(" ");
