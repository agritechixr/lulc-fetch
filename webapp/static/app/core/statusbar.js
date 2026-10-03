  // Status bar: coordinates and scale.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ status bar: coordinates & scale
  let coordFmt = prefs.get("coords", "dd"), lastLatLng = null;
  const dms = (v, pos, neg) => {
    const a = Math.abs(v), d = Math.floor(a), mf = (a - d) * 60, m = Math.floor(mf), s = (mf - m) * 60;
    return `${d}°${String(m).padStart(2, "0")}′${s.toFixed(1).padStart(4, "0")}″${v >= 0 ? pos : neg}`;
  };
  function showCoords(ll) {
    lastLatLng = ll;
    if (!ll) { $("#sb-coords").textContent = "Lat –  Lon –"; return; }
    const lng = L.Util.wrapNum(ll.lng, [-180, 180], true);
    $("#sb-coords").textContent = coordFmt === "dd"
      ? `Lat ${ll.lat.toFixed(5)}  Lon ${lng.toFixed(5)}`
      : `${dms(ll.lat, "N", "S")}  ${dms(lng, "E", "W")}`;
  }
  map.on("mousemove", (e) => showCoords(e.latlng));
  map.getContainer().addEventListener("mouseleave", () => showCoords(null));
  $("#sb-coords").onclick = () => { coordFmt = coordFmt === "dd" ? "dms" : "dd"; prefs.set("coords", coordFmt); showCoords(lastLatLng); };

  const EARTH_CIRC = 40075016.686, DPI = 96, INCH = 0.0254;
  const metersPerPixel = (z, lat) => EARTH_CIRC * Math.cos(lat * Math.PI / 180) / Math.pow(2, z + 8);
  function updateScale() {
    const c = map.getCenter();
    const scale = metersPerPixel(map.getZoom(), c.lat) * DPI / INCH;
    if (document.activeElement !== $("#sb-scale")) $("#sb-scale").value = Math.round(scale).toLocaleString("en-US");
    $("#sb-zoom").textContent = `z ${map.getZoom().toFixed(1)}`;
  }
  function setScale(text) {
    const n = parseFloat(String(text).replace(/[^\d.]/g, ""));
    if (!n || n < 50) { updateScale(); return; }
    const lat = map.getCenter().lat;
    const z = Math.log2(EARTH_CIRC * Math.cos(lat * Math.PI / 180) * DPI / INCH / (n * 256));
    map.setZoom(Math.max(map.getMinZoom(), Math.min(map.getMaxZoom() || 19, z)));
    $("#sb-scale").blur();
  }
  map.on("zoomend moveend", updateScale);
  $("#sb-scale").addEventListener("change", (e) => setScale(e.target.value));
  $("#sb-scale").addEventListener("keydown", (e) => { if (e.key === "Enter") setScale(e.target.value); if (e.key === "Escape") { e.target.blur(); updateScale(); } });
  $("#sb-scale").addEventListener("focus", (e) => e.target.select());
  updateScale();
  $("#sb-jobs").onclick = () => switchTool("jobs");
