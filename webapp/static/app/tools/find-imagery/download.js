  // Find imagery: the download dialog (scenes, composites, land-cover labels, original products).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ download dialog
  const mission = () => state.config.missions[$("#mission").value] || state.config.missions["sentinel-2"];
  function bandBoxes(selected) {
    $("#bands").innerHTML = mission().bands.map((b) =>
      `<label><input type="checkbox" value="${b}" ${selected.includes(b) ? "checked" : ""}>${b}</label>`).join("");
    $$("#bands input").forEach((i) => i.onchange = updateEstimate);
    updateEstimate();
  }
  $("#bands-default").onclick = () => bandBoxes(mission().default_bands);
  $("#bands-all").onclick = () => bandBoxes(mission().bands);
  $("#bands-rgb").onclick = () => bandBoxes(["B02", "B03", "B04", "B08"]);

  const dlKind = () => $('input[name="kind"]:checked')?.value;

  function updateDialog() {
    const k = dlKind();
    $("#dl-s2").classList.toggle("hidden", !["scene", "composite"].includes(k));
    $("#dl-mask-wrap").classList.toggle("hidden", k !== "scene");
    $("#dl-comp-opts").classList.toggle("hidden", k !== "composite");
    $("#dl-labels").classList.toggle("hidden", k !== "labels");
    $("#dl-res-wrap").classList.toggle("hidden", k === "product");
    $("#dl-product-wrap").classList.toggle("hidden", k !== "product");
    $("#dl-error").classList.add("hidden");
    updateEstimate();
  }
  $$('input[name="kind"]').forEach((r) => r.onchange = updateDialog);
  $("#dl-res").onchange = updateEstimate;
  $("#dl-indices").onchange = updateEstimate;

  function updateEstimate() {
    if (!state.aoi) return;
    const res = +$("#dl-res").value, km2 = geomArea(state.aoi) / 1e6;
    const [w, s, e, n] = geomBounds(state.aoi);
    const midLat = (s + n) / 2;
    const wpx = Math.ceil(((e - w) * 111320 * Math.cos(midLat * Math.PI / 180)) / res);
    const hpx = Math.ceil(((n - s) * 110574) / res);
    const k = dlKind();
    let nb = 1;
    if (k === "scene" || k === "composite") {
      nb = $$("#bands input:checked").length + ($("#dl-indices").checked ? state.config.indices.length : 0) + (k === "composite" ? 1 : 0);
    }
    const mb = (wpx * hpx * nb * (k === "labels" ? 1 : 4)) / 1e6 * 0.6;
    const tooBig = wpx * hpx > 60e6;
    $("#dl-estimate").innerHTML = `≈ ${wpx.toLocaleString()} × ${hpx.toLocaleString()} px · ${nb} band${nb === 1 ? "" : "s"} · ~${mb < 1 ? "<1" : Math.round(mb)} MB` +
      (tooBig ? ` <span style="color:var(--err)">— too large; choose a coarser pixel size</span>` : "");
  }

  function openDownload(scene = null) {
    if (!state.aoi) return toast("Set an area of interest first", true);
    state.downloadScene = scene;
    const sceneOpt = $('.opt[data-kind="scene"]'), prodOpt = $('.opt[data-kind="product"]');
    sceneOpt.classList.toggle("disabled", !scene);
    prodOpt.classList.toggle("disabled", !scene);
    $("#dl-scene-desc").textContent = scene
      ? `Bands for ${scene.date} (${scene.tiles.join(", ")}), clipped to your area`
      : "Pick a date from the search results first";
    $(`input[name="kind"][value="${scene ? "scene" : "composite"}"]`).checked = true;
    const landsat = $("#mission").value === "landsat";
    $("#dl-product-title").textContent = landsat ? "Original product bundle (EarthExplorer)" : "Original full product (.SAFE)";
    $("#dl-product-desc").textContent = landsat ? "Full Landsat Level-2 scene bundle (.tar, ~1 GB) from USGS EarthExplorer (needs credentials)"
      : "Entire ~110 km tile, ~0.5–1.2 GB, from Copernicus (needs account)";
    if (scene) {
      $("#dl-product-list").innerHTML = scene.items.map((i) => esc(i.product_name)).join("<br>") +
        `<p class="hint">Downloaded from ${landsat ? "USGS EarthExplorer" : "Copernicus Data Space"} with your saved credentials.</p>`;
    }
    if ($("#bands").dataset.mission !== $("#mission").value) {
      bandBoxes(mission().default_bands);
      $("#bands").dataset.mission = $("#mission").value;
      $("#dl-res").value = String(mission().res);
    }
    updateDialog();
    $("#dlg-dl").showModal();
  }
  $("#btn-download-range").onclick = () => openDownload(null);

  $("#dl-go").onclick = () => {
    const k = dlKind(), sc = state.downloadScene;
    const body = {
      kind: k, source: state.results?.source || $("#source").value, aoi: state.aoi,
      start: $("#start").value, end: $("#end").value, max_cloud: +$("#cloud").value,
      res: +$("#dl-res").value, bands: $$("#bands input:checked").map((i) => i.value),
      indices: $("#dl-indices").checked, mask_clouds: $("#dl-mask").checked,
      stat: $("#dl-stat").value, max_scenes: +$("#dl-maxscenes").value,
      product: $("#dl-product").value, year: +$("#dl-year").value,
    };
    if (k === "scene") {
      body.date = sc.date;
      body.start = body.end = sc.date;
      body.max_cloud = 100;
    }
    if (k === "product") {
      body.product_names = sc.items.map((i) => i.product_name);
      body.entity_ids = sc.items.map((i) => i.properties["landsat:scene_id"] || "");
    }
    if (["scene", "composite"].includes(k) && !body.bands.length) return showDlError("Select at least one band");
    busy($("#dl-go"), "Starting…", async () => {
      let job;
      try {
        job = await api("/api/jobs", { method: "POST", json: body });
      } catch (e) { return showDlError(e.message); }
      $("#dlg-dl").close();
      addedJobs.add(job.id);  // added below, as soon as it finishes
      prefs.set("addedJobs", [...addedJobs].slice(-200));
      try {
        const done = await trackJob(job, { tool: "search", save: "search" });
        const tifs = done.files.filter((f) => /\.tiff?$/i.test(f));
        for (const f of tifs) await addRasterFromPath(`downloads/${done.id}/${f}`, { name: done.title });
        toast(tifs.length ? `Added “${done.title}” to Contents` : `${done.title} finished. Files are in Downloads & jobs.`);
      } catch (e) { if (notCancelled(e)) toast(e, true); }
    });
  };
  function showDlError(msg) { const el = $("#dl-error"); el.textContent = msg; el.classList.remove("hidden"); }
