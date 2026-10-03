  // Credentials (Copernicus, USGS).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ credentials
  async function loadCreds() {
    const data = await api("/api/credentials");
    const p = data.providers;
    $("#creds-dot").classList.toggle("ok", Object.values(p).some((x) => x.complete));
    $("#creds-backend").textContent = `Storage: ${data.backend}.`;
    $("#creds-list").innerHTML = Object.entries(p).map(([key, prov]) => `
      <form class="cred" data-p="${key}" autocomplete="off">
        <h4><span>${esc(prov.title)}</span>${prov.complete ? '<span class="status done">saved</span>' : '<span class="status">not set</span>'}</h4>
        <p>${esc(prov.help)} <a href="${esc(prov.signup)}" target="_blank" rel="noopener">Get credentials ↗</a></p>
        ${prov.fields.map((f) => `<label>${esc(f.label)}
          <input name="${f.name}" type="${f.secret ? "password" : "text"}" autocomplete="${f.secret ? "new-password" : "off"}"
            placeholder="${f.set ? (f.secret ? "•••••••• saved. Leave blank to keep it." : esc(f.display)) : ""}"></label>`).join("")}
        <div class="row">
          <button class="btn primary small" type="submit">Save</button>
          <button class="btn small" type="button" data-test>Test</button>
          ${prov.fields.some((f) => f.set) ? '<button class="btn small danger" type="button" data-remove>Remove</button>' : ""}
        </div>
        <div class="msg"></div>
      </form>`).join("");
    $$("#creds-list form").forEach((form) => {
      const provider = form.dataset.p, msg = $(".msg", form);
      form.onsubmit = async (e) => {
        e.preventDefault();
        const values = Object.fromEntries(new FormData(form));
        if (!Object.values(values).some((v) => v)) { msg.className = "msg err"; msg.textContent = "Nothing to save."; return; }
        await api(`/api/credentials/${provider}`, { method: "PUT", json: values });
        toast("Saved to keychain");
        await loadCreds();
      };
      $("[data-test]", form).onclick = (e) => busy(e.currentTarget, "Testing…", async () => {
        const r = await api(`/api/credentials/${provider}/test`, { method: "POST" });
        msg.className = "msg " + (r.ok ? "ok" : "err");
        msg.textContent = (r.ok ? "✓ " : "✗ ") + r.message;
      });
      $("[data-remove]", form)?.addEventListener("click", async () => {
        if (!confirm("Remove these saved credentials?")) return;
        await api(`/api/credentials/${provider}`, { method: "DELETE" });
        await loadCreds();
      });
    });
  }
  $("#btn-creds").onclick = async () => { await loadCreds(); $("#dlg-creds").showModal(); };

  // generic modal close
  $$("dialog").forEach((d) => {
    $$("[data-close]", d).forEach((b) => b.onclick = () => d.close());
    d.addEventListener("click", (e) => e.target === d && d.close());
  });
