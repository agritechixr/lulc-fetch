  // Searchable picker: type letters, the matching items are listed.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ searchable picker: type letters, the matching items are listed
  // items: { id, title, aliases?, keywords?, sub?, group?, disabled? }. Matches the title, the aliases (e.g. local crop names) and,
  // for 3+ letters, the keywords (e.g. disease names); names that start with the letters come first.
  function searchPicker(el, { items, value, onChange, placeholder = "Type to search…", empty = "Nothing matches" }) {
    const norm = (s) => String(s || "").toLowerCase().replace(/[_\W]+/g, " ").trim();
    el.classList.add("cc");
    el.innerHTML = `<div class="cc-box"><input type="text" class="cc-in" role="combobox" aria-expanded="false" aria-autocomplete="list" autocomplete="off" spellcheck="false" placeholder="${esc(placeholder)}">
      <button type="button" class="cc-caret" tabindex="-1" aria-label="Show the whole list">▾</button></div><div class="cc-list hidden" role="listbox"></div>`;
    const inp = $(".cc-in", el), list = $(".cc-list", el);
    let cur = value, shown = [], act = -1;
    const title = () => items.find((i) => i.id === cur)?.title || "";
    const isOpen = () => !list.classList.contains("hidden");
    function matches(q) {
      if (!q) return items.map((it) => ({ it, why: "" }));
      const out = [];
      for (const it of items) {
        let rank = 9, why = "";
        [it.title, it.id, ...(it.aliases || [])].map(norm).forEach((n, k) => {
          const r = n.startsWith(q) ? (k ? 1 : 0) : n.split(" ").some((w) => w.startsWith(q)) ? 2 : n.includes(q) ? 3 : 9;
          if (r < rank) { rank = r; why = k > 1 ? n : ""; }
        });
        if (rank === 9 && q.length >= 3) {
          const kw = (it.keywords || []).find((w) => norm(w).includes(q));
          if (kw) { rank = 4; why = kw; }
        }
        if (rank < 9) out.push({ it, rank, why });
      }
      return out.sort((a, b) => a.rank - b.rank || a.it.title.localeCompare(b.it.title));
    }
    const mark = (text, q) => {
      const i = q ? text.toLowerCase().indexOf(q) : -1;
      return i < 0 ? esc(text) : `${esc(text.slice(0, i))}<mark>${esc(text.slice(i, i + q.length))}</mark>${esc(text.slice(i + q.length))}`;
    };
    function render(raw) {
      const q = norm(raw), m = matches(q);
      shown = m.map((x) => x.it);
      let last = null;
      list.innerHTML = m.length ? m.map(({ it, why }, i) => {
        const g = !q && it.group && it.group !== last ? `<div class="cc-group">${esc(it.group)}</div>` : "";
        last = it.group;
        const sub = why ? `matches “${why}”` : it.sub || "";
        return `${g}<div role="option" class="cc-row ${it.id === cur ? "on" : ""} ${it.disabled ? "off" : ""}" data-i="${i}" aria-selected="${it.id === cur}">
          <span>${mark(it.title, q)}</span>${sub ? `<small>${q ? mark(sub, q) : esc(sub)}</small>` : ""}</div>`;
      }).join("") : `<div class="cc-empty">${esc(empty)}: “${esc(raw.trim())}”</div>`;
      act = q ? shown.findIndex((it) => !it.disabled) : shown.findIndex((it) => it.id === cur);
      highlight();
      $$(".cc-row", list).forEach((r) => r.onmousedown = (e) => { e.preventDefault(); choose(shown[+r.dataset.i]); });
    }
    function highlight() {
      $$(".cc-row", list).forEach((r) => r.classList.toggle("act", +r.dataset.i === act));
      $(".cc-row.act", list)?.scrollIntoView({ block: "nearest" });
    }
    const open = (on) => { list.classList.toggle("hidden", !on); inp.setAttribute("aria-expanded", on); el.classList.toggle("open", on); };
    function choose(it) {
      if (!it || it.disabled) return;
      const changed = it.id !== cur;
      cur = it.id; stop(); inp.blur();
      if (changed) onChange(it.id);
    }
    // while the list is open the box is empty (the current choice shows greyed), so letters always start a new search
    const start = () => { inp.placeholder = title() || placeholder; inp.value = ""; render(""); open(true); };
    const stop = () => { open(false); inp.value = title(); inp.placeholder = placeholder; };
    inp.value = title();
    inp.onfocus = start;
    inp.onmousedown = () => { if (document.activeElement === inp && !isOpen()) start(); };
    inp.oninput = () => { render(inp.value); open(true); };
    inp.onblur = stop;
    inp.onkeydown = (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!isOpen()) { start(); return; }
        let i = act;
        do { i += e.key === "ArrowDown" ? 1 : -1; } while (shown[i]?.disabled);
        if (shown[i]) { act = i; highlight(); }
      } else if (e.key === "Enter") { e.preventDefault(); if (isOpen()) choose(shown[act]); }
      else if (e.key === "Escape" && isOpen()) { e.preventDefault(); stop(); inp.select(); }
    };
    $(".cc-caret", el).onmousedown = (e) => { e.preventDefault(); if (isOpen()) { open(false); inp.blur(); } else inp.focus(); };
    return { set: (id) => { cur = id; inp.value = title(); }, get: () => cur };
  }
