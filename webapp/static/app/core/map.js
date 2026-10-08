  // The Leaflet map and basemaps.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ map
  const savedView = prefs.get("view", { center: [20.5, 78.9], zoom: 5 });
  const map = L.map("map", { zoomControl: true, zoomSnap: 0, zoomDelta: 0.5, wheelPxPerZoomLevel: 90 }).setView(savedView.center, savedView.zoom);
  map.on("moveend", () => { const c = map.getCenter(); prefs.set("view", { center: [+c.lat.toFixed(5), +c.lng.toFixed(5)], zoom: +map.getZoom().toFixed(2) }); });
  map.createPane("labels").style.zIndex = 650;
  // online map layers (WMS / WMTS / XYZ): under the app's own layers, or over them when on top of Contents (see restack)
  map.createPane("onlineBelow").style.zIndex = 350;
  map.createPane("onlineAbove").style.zIndex = 450;
  map.getPane("labels").style.pointerEvents = "none";
  const BASEMAPS = {
    streets: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" }),
    imagery: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" }),
    topo: L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, attribution: "© OpenStreetMap contributors, SRTM · © OpenTopoMap (CC-BY-SA)" }),
    none: null,
  };
  const placeLabels = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, pane: "labels" });
  let basemap = null;
  function setBasemap(name) {
    if (!(name in BASEMAPS)) name = "streets";
    if (basemap) map.removeLayer(basemap);
    basemap = BASEMAPS[name];
    basemap?.addTo(map);
    map.getContainer().classList.remove("bm-streets", "bm-imagery", "bm-topo", "bm-none");
    map.getContainer().classList.add("bm-" + name);
    prefs.set("basemap", name);
  }
  function setLabels(on) { on ? placeLabels.addTo(map) : map.removeLayer(placeLabels); prefs.set("labels", on); }
  setBasemap(prefs.get("basemap", "streets"));
  setLabels(prefs.get("labels", false));
  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
  // map activity: a small spinner above the scale bar turns while basemap tiles or layers load (after a short delay, so quick
  // redraws don't flicker)
  const mapBusy = (() => {
    const pending = new Set();
    let timer = 0;
    const ctl = L.control({ position: "bottomleft" });
    ctl.onAdd = () => {
      const d = L.DomUtil.create("div", "map-busy");
      d.title = "Loading the map…";
      d.setAttribute("role", "status");
      d.setAttribute("aria-label", "Loading the map");
      return d;
    };
    ctl.addTo(map);
    const el = ctl.getContainer();
    const sync = () => {
      clearTimeout(timer);
      if (pending.size) timer = setTimeout(() => el.classList.add("on"), 150);
      else el.classList.remove("on");
    };
    const api = {
      start: (key) => { pending.add(key); sync(); },
      end: (key) => { pending.delete(key); sync(); },
      watch: (layer) => {   // tile layers: "loading" when tiles are requested, "load" when all visible ones arrived
        layer.on("loading", () => api.start(layer));
        layer.on("load remove", () => api.end(layer));
      },
    };
    return api;
  })();
  [...Object.values(BASEMAPS), placeLabels].filter(Boolean).forEach((l) => {
    mapBusy.watch(l);
    if (map.hasLayer(l) && l.isLoading()) mapBusy.start(l);   // the basemap started loading before this existed
  });
  const geoLayer = L.featureGroup().addTo(map);  // transient hover previews for address results
