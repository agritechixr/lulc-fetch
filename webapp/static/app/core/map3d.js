  // 3D maps: the open map's layers as a 3D scene (three.js, loaded the first time a 3D map opens), with the controls of
  // AutoCAD: the viewport controls [–][view][visual style] at the top left, the ViewCube at the top right and the UCS icon
  // (the X / Y / Z axes) at the bottom left. Single-band rasters such as DEMs become surfaces (their values are the
  // heights); other rasters and vector layers are draped on them; the basemap lies under everything.
  // Scene coordinates: metres east (X) and north (Y) of the map's centre, height up (Z).
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ 3D map
  const v3 = { T: null, Orbit: null, loading: null, on: false, doc: null, renderer: null, scene: null, camera: null, controls: null, root: null,
    cube: null, grids: new Map(), textures: new Map(), ground: null, surfaces: [], base: 0, origin: [0, 0], cosLat: 1,
    exag: prefs.get("v3-exag", "auto"), k: 1, style: prefs.get("v3-style", "realistic"), viewName: "SW Isometric", syncTimer: 0, anim: 0, frame: 0, spin: 0,
    nav: prefs.get("v3-nav", true), cubeOn: prefs.get("v3-cube", true), ucsOn: prefs.get("v3-ucs", true), mode: "orbit" };
  // the layers drawn: the open map's, or those of the 3D map shown beside a 2D map (View ▸ Side by side)
  const src3d = () => v3.src || layers;
  const R_EARTH = 6378137, HALF_WORLD = Math.PI * R_EARTH;
  const toMerc = (lon, lat) => [R_EARTH * lon * Math.PI / 180, R_EARTH * Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360))];
  const fromMerc = (x, y) => [x / R_EARTH * 180 / Math.PI, (2 * Math.atan(Math.exp(y / R_EARTH)) - Math.PI / 2) * 180 / Math.PI];
  const toScene = (mx, my) => [(mx - v3.origin[0]) * v3.cosLat, (my - v3.origin[1]) * v3.cosLat];
  const fromScene = (x, y) => [x / v3.cosLat + v3.origin[0], y / v3.cosLat + v3.origin[1]];
  // a layer's box in Web Mercator [left, bottom, right, top]
  const mercBox = (b) => { const [x0, y0] = toMerc(b[0][1], b[0][0]), [x1, y1] = toMerc(b[1][1], b[1][0]); return [x0, y0, x1, y1]; };
  const VIEWS3D = { "Top": [0, -1e-3, 1], "Bottom": [0, -1e-3, -1], "Left": [-1, 0, 0], "Right": [1, 0, 0], "Front": [0, -1, 0], "Back": [0, 1, 0],
    "SW Isometric": [-1, -1, 1], "SE Isometric": [1, -1, 1], "NE Isometric": [1, 1, 1], "NW Isometric": [-1, 1, 1] };
  const STYLES3D = { realistic: "Realistic", shaded: "Shaded", wireframe: "Wireframe" };

  function load3d() {
    v3.loading ||= Promise.all([import("/static/vendor/three/three.module.min.js"), import("/static/vendor/three/OrbitControls.js")])
      .then(([T, oc]) => { v3.T = T; v3.Orbit = oc.OrbitControls; init3d(); })
      .catch((e) => { v3.loading = null; throw e; });
    return v3.loading;
  }

  function init3d() {
    const T = v3.T, el = $("#map3d");
    v3.renderer = new T.WebGLRenderer({ antialias: true });
    v3.renderer.setPixelRatio(Math.min(2, devicePixelRatio || 1));
    v3.renderer.domElement.className = "v3-canvas";
    el.prepend(v3.renderer.domElement);
    v3.scene = new T.Scene();
    v3.camera = new T.PerspectiveCamera(45, 1, 1, 1e7);
    v3.camera.up.set(0, 0, 1);
    v3.controls = new v3.Orbit(v3.camera, v3.renderer.domElement);
    v3.controls.screenSpacePanning = false;   // pans along the ground, like a map
    v3.controls.zoomToCursor = true;
    // the mouse as in AutoCAD: wheel = zoom at the cursor, middle-drag = pan, Shift + middle-drag = orbit, double-click the
    // wheel = zoom extents; also left-drag = orbit and right-drag = pan (Shift swaps them), for trackpads
    v3.controls.mouseButtons = { LEFT: T.MOUSE.ROTATE, MIDDLE: T.MOUSE.PAN, RIGHT: T.MOUSE.PAN };
    let lastMiddle = 0;
    v3.renderer.domElement.addEventListener("pointerdown", (e) => {
      if (e.button !== 1) return;
      e.preventDefault();   // no auto-scroll
      if (e.timeStamp - lastMiddle < 400) { lastMiddle = 0; fit3d(); }
      else lastMiddle = e.timeStamp;
    });
    v3.renderer.domElement.addEventListener("mousedown", (e) => { if (e.button === 1) e.preventDefault(); });
    v3.renderer.domElement.addEventListener("contextmenu", (e) => e.preventDefault());
    // the view's name becomes "Custom" once the user really turns or moves it (a plain click doesn't)
    let userMove = false;
    v3.controls.addEventListener("start", () => { cancelAnimationFrame(v3.anim); userMove = true; });
    v3.controls.addEventListener("end", () => { userMove = false; });
    v3.controls.addEventListener("change", () => {
      if (userMove && v3.viewName !== "Custom") { v3.viewName = "Custom"; vpcLabels(); }
      render3d();
      if (link.on) syncFrom3d();   // side by side: the 2D map follows
    });
    v3.scene.add(new T.AmbientLight(0xffffff, 1.1));
    const sun = new T.DirectionalLight(0xffffff, 2.8);
    sun.position.set(-1, 1, 1.6);   // from the north-west, as in a hillshade
    v3.scene.add(sun);
    v3.root = new T.Group();
    v3.scene.add(v3.root);
    initViewCube();
    new ResizeObserver(resize3d).observe(el);
    resize3d();
    // the cursor's position (and height) in the status bar
    let pending = null;
    v3.renderer.domElement.addEventListener("pointermove", (e) => {
      if (!pending) requestAnimationFrame(() => { pointerCoords(pending); pending = null; });
      pending = e;
    });
    v3.renderer.domElement.addEventListener("pointerleave", () => showCoords(null));
    v3.renderer.domElement.addEventListener("dblclick", (e) => {   // double-click: look at that point
      const hit = pick3d(e);
      if (hit) { v3.controls.target.copy(hit.point); v3.controls.update(); }
    });
  }

  function resize3d() {
    if (!v3.renderer) return;
    const el = $("#map3d"), w = el.clientWidth, h = el.clientHeight;
    if (!w || !h) return;
    v3.renderer.setSize(w, h, false);
    v3.camera.aspect = w / h;
    v3.camera.updateProjectionMatrix();
    render3d();
  }

  // ---- show / hide (a 3D map opened or left)
  async function show3d(doc) {
    v3.on = true; v3.doc = doc;
    $("#map3d").classList.remove("hidden");
    $("#v3-msg").textContent = "Loading the 3D view…";
    try { await load3d(); } catch (e) { $("#v3-msg").textContent = `The 3D view couldn't start: ${e.message}`; return; }
    if (v3.doc !== doc || !v3.on) return;
    if (!doc.origin) {   // the scene's centre: the middle of what the map shows now
      const c = map.getCenter();
      doc.origin = toMerc(c.lng, c.lat);
    }
    v3.origin = doc.origin;
    v3.cosLat = Math.cos(fromMerc(...v3.origin)[1] * Math.PI / 180);
    v3.ground = null;
    resize3d();
    if (doc.cam) setCam(doc.cam);
    else { v3.viewName = "SW Isometric"; fit3d(null, VIEWS3D["SW Isometric"]); }
    vpcLabels();
    build3d();
  }
  function hide3d(doc) {
    if (doc && v3.camera) doc.cam = map3dCam();
    v3.on = false; v3.doc = null;
    cancelAnimationFrame(v3.anim);
    $("#map3d").classList.add("hidden");
  }
  const map3dCam = () => v3.on && v3.camera ? { p: v3.camera.position.toArray(), t: v3.controls.target.toArray(), view: v3.viewName } : null;
  function setCam(c) {
    v3.camera.position.fromArray(c.p);
    v3.controls.target.fromArray(c.t);
    v3.viewName = c.view || "Custom";
    v3.controls.update();
  }
  // the layers changed (added, removed, shown, restyled, reordered): draw the scene again
  function map3dChanged() {
    if (!v3.on || !v3.T) return;
    clearTimeout(v3.syncTimer);
    v3.syncTimer = setTimeout(build3d, 60);
  }

  // ---- heights: single-band rasters are surfaces (unless set to be draped); their grid comes from the server
  const NOT_HEIGHTS = /slope|aspect|hillshade|contour|ndvi|ndwi|evi|savi|index|indices|difference|pct_change|change|from_to|classes|class|cluster|probab|confidence|embedding/i;
  const ELEV_NAME = /\b(dem|dsm|dtm|elev|elevation|height|heights|srtm|alos|aster|nasadem|copdem|terrain|altitude|relief)\b|dem[_-]|_dem/i;
  function isSurface(l) {
    if (l.type !== "raster" || !l.path) return false;
    if (l.view3d) return l.view3d === "surface";
    if ((l.info?.count || 0) !== 1 || l.legend?.kind === "classes" || l.info?.embedding) return false;
    const b = l.info?.bands?.[0], text = `${l.name} ${l.path} ${b?.description || ""}`;   // the file's own values (a DEM's metres), not the display scale
    if (NOT_HEIGHTS.test(text)) return false;   // products made from a DEM or an image are not heights
    return ELEV_NAME.test(text) || (b && b.p98 - b.median > 25);
  }
  // heights are the band's values as stored in the file (metres for a DEM): the display scale (e.g. ×0.0001 guessed for
  // satellite numbers) is not applied
  function grid3d(l) {
    const key = `${l.path}|${l.render?.band || 1}`, g = v3.grids.get(key);
    if (g) return g.values ? g : null;
    v3.grids.set(key, {});
    api(`/api/rasters/grid?path=${encodeURIComponent(l.path)}&band=${l.render?.band || 1}&max_px=320`)
      .then((r) => {
        const bin = atob(r.values), bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        v3.grids.set(key, { ...r, values: new Float32Array(bytes.buffer) });
        map3dChanged();
      })
      .catch((e) => { v3.grids.set(key, { error: e.message }); toast(`${l.name}: ${e.message}`, true); });
    return null;
  }
  // one surface's value at a Web Mercator point (bilinear between its vertices), undefined outside it or on nodata
  function sampleGrid(g, mx, my) {
    const [x0, y0, x1, y1] = g.merc;
    if (mx < x0 || mx > x1 || my < y0 || my > y1) return undefined;
    const fx = (mx - x0) / (x1 - x0) * (g.width - 1), fy = (y1 - my) / (y1 - y0) * (g.height - 1);   // as the surface's vertices
    const c = Math.floor(fx), r = Math.floor(fy), c1 = Math.min(c + 1, g.width - 1), r1 = Math.min(r + 1, g.height - 1), tx = fx - c, ty = fy - r;
    const v = g.values, W = g.width;
    const a = v[r * W + c], b = v[r * W + c1], d = v[r1 * W + c], e = v[r1 * W + c1];
    if ([a, b, d, e].every(Number.isFinite)) return (a * (1 - tx) + b * tx) * (1 - ty) + (d * (1 - tx) + e * tx) * ty;
    return [a, b, d, e].find(Number.isFinite);
  }
  // the height (scene Z) of the topmost surface at a Web Mercator point; 0 where there is none
  function heightAt(mx, my) {
    for (const { g } of v3.surfaces) {
      const h = sampleGrid(g, mx, my);
      if (h !== undefined) return (h - v3.base) * v3.k;
    }
    return 0;
  }

  function texture3d(url) {
    let t = v3.textures.get(url);
    if (!t) {
      t = new v3.T.TextureLoader().load(url, () => render3d());
      t.colorSpace = v3.T.SRGBColorSpace;
      t.anisotropy = 4;
      v3.textures.set(url, t);
      if (v3.textures.size > 40) { const [k, old] = v3.textures.entries().next().value; old.dispose(); v3.textures.delete(k); }
    }
    return t;
  }

  // ---- the scene
  function build3d() {
    if (!v3.on || !v3.T) return;
    const T = v3.T;
    v3.root.children.slice().forEach((o) => {
      v3.root.remove(o);
      o.geometry?.dispose();
      [].concat(o.material || []).forEach((m) => m.dispose());
    });
    const dark = getComputedStyle(document.body).backgroundColor.match(/\d+/g)?.slice(0, 3).reduce((a, b) => a + +b, 0) < 200;
    v3.scene.background = new T.Color(dark ? 0x10151b : 0xdfe7ef);
    const vis = src3d().filter((l) => l.visible);   // index 0 = on top
    if (v3.doc && !v3.doc.originSet && vis.length) moveOrigin();
    v3.surfaces = vis.filter(isSurface).map((l) => ({ l, g: grid3d(l) })).filter((s) => s.g);
    const lows = v3.surfaces.map((s) => s.g.min).filter(Number.isFinite), highs = v3.surfaces.map((s) => s.g.max).filter(Number.isFinite);
    v3.base = lows.length ? Math.min(...lows) : 0;
    v3.k = v3.exag === "auto" ? autoExag(highs.length ? Math.max(...highs) - v3.base : 0) : v3.exag;
    const relief = highs.length ? (Math.max(...highs) - v3.base) * v3.k : 0;
    const loading = vis.filter(isSurface).length - v3.surfaces.length;
    let order = 1;
    // bottom layer first, so later (higher) layers draw over it
    for (const l of vis.slice().reverse()) {
      if (l.type === "raster" && isSurface(l)) {
        const s = v3.surfaces.find((x) => x.l === l);
        if (s) addSurface(l, s.g, order++);
      } else if (l.type === "raster" || l.type === "image") {
        if (l.image || l.url) addDrape(l, order++);
      } else if (l.type === "vector") { if (l.extrude) addExtrusions(l); addVector(l, relief, order++); }
    }
    addGround(relief);
    const surfaces = vis.filter(isSurface).length;
    $("#v3-msg").textContent = loading ? "Reading heights…" : !vis.length ? "This 3D map is empty: add a DEM (a single-band GeoTIFF of heights), imagery or vector layers"
      : !surfaces ? "No height layer: add a DEM to see the land in 3D (other layers lie flat)" : "";
    if (measure.on) drawMeasure3d();   // heights may have changed (exaggeration, a new DEM)
    render3d();
  }
  // Auto heights: flat land is raised so its relief is about a fifth of the surfaces' width (×1 to ×20)
  function autoExag(relief) {
    if (!(relief > 0)) return 1;
    const w = Math.max(...v3.surfaces.map(({ g }) => (g.merc[2] - g.merc[0]) * v3.cosLat)), want = 0.2 * w / relief;
    return [1, 2, 3, 5, 10, 20].reduce((a, b) => Math.abs(Math.log(b / want)) < Math.abs(Math.log(a / want)) ? b : a);
  }
  // surfaces (lit) are drawn where they are; draped layers are pulled towards the eye a little so they show on top of them
  function material3d(l, tex, order, { lit = true } = {}) {
    const T = v3.T;
    const common = { transparent: (l.opacity ?? 1) < 1 || !lit, opacity: l.opacity ?? 1, side: T.DoubleSide,
                     ...(lit ? {} : { polygonOffset: true, polygonOffsetFactor: -order, polygonOffsetUnits: -4 * order }) };
    if (v3.style === "wireframe") return new T.MeshBasicMaterial({ ...common, wireframe: true, color: lit ? 0x2f7d63 : 0x7a8699, transparent: true, opacity: 0.6 * (l.opacity ?? 1) });
    if (v3.style === "shaded" && lit) return new T.MeshLambertMaterial({ ...common, color: 0xd8d2c4 });
    return lit ? new T.MeshLambertMaterial({ ...common, map: tex }) : new T.MeshBasicMaterial({ ...common, map: tex });
  }
  // a grid of vertices over a Web Mercator box: heights from fn(mx, my) (null = no surface there), texture over imgBox
  function gridMesh(box, W, H, fn, imgBox) {
    const T = v3.T, [x0, y0, x1, y1] = box, pos = new Float32Array(W * H * 3), uv = new Float32Array(W * H * 2), ok = new Uint8Array(W * H);
    const [ix0, iy0, ix1, iy1] = imgBox;
    for (let r = 0; r < H; r++) for (let c = 0; c < W; c++) {
      const i = r * W + c, mx = x0 + (c / (W - 1)) * (x1 - x0), my = y1 - (r / (H - 1)) * (y1 - y0), z = fn(mx, my, r, c);
      const [x, y] = toScene(mx, my);
      pos.set([x, y, Number.isFinite(z) ? z : 0], i * 3);
      uv.set([(mx - ix0) / (ix1 - ix0), (my - iy0) / (iy1 - iy0)], i * 2);
      ok[i] = Number.isFinite(z) ? 1 : 0;
    }
    const idx = [];
    for (let r = 0; r < H - 1; r++) for (let c = 0; c < W - 1; c++) {
      const a = r * W + c, b = a + 1, d = a + W, e = d + 1;
      if (ok[a] && ok[b] && ok[d] && ok[e]) idx.push(a, d, b, b, d, e);
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.BufferAttribute(pos, 3));
    geo.setAttribute("uv", new T.BufferAttribute(uv, 2));
    geo.setIndex(W * H > 65535 ? new T.BufferAttribute(new Uint32Array(idx), 1) : new T.BufferAttribute(new Uint16Array(idx), 1));
    geo.computeVertexNormals();
    return geo;
  }
  function addSurface(l, g, order) {
    const imgBox = l.bounds ? mercBox(l.bounds) : g.merc;
    const geo = v3.style === "wireframe"   // a wire every few cells, so it stays readable
      ? gridMesh(g.merc, Math.min(g.width, 81), Math.min(g.height, 81), (mx, my) => { const h = sampleGrid(g, mx, my); return Number.isFinite(h) ? (h - v3.base) * v3.k : NaN; }, imgBox)
      : gridMesh(g.merc, g.width, g.height, (mx, my, r, c) => {
        const v = g.values[r * g.width + c];
        return Number.isFinite(v) ? (v - v3.base) * v3.k : NaN;
      }, imgBox);
    const mesh = new v3.T.Mesh(geo, material3d(l, l.image ? texture3d(l.image) : null, order));
    mesh.renderOrder = order;
    mesh.userData = { layer: l, surface: true };
    v3.root.add(mesh);
  }
  function addDrape(l, order) {
    const b = l.bounds || l.info?.bounds;
    if (!b) return;
    const box = mercBox(b), n = 96, aspect = (box[2] - box[0]) / Math.max(1e-6, box[3] - box[1]);
    const W = Math.max(2, Math.round(aspect >= 1 ? n : n * aspect)), H = Math.max(2, Math.round(aspect >= 1 ? n / aspect : n));
    const geo = gridMesh(box, W, H, (mx, my) => heightAt(mx, my), box);
    const mesh = new v3.T.Mesh(geo, material3d(l, texture3d(l.image || l.url), order, { lit: false }));
    if (v3.style === "wireframe") mesh.material = new v3.T.MeshBasicMaterial({ map: texture3d(l.image || l.url), transparent: true, opacity: l.opacity ?? 1, side: v3.T.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: -order, polygonOffsetUnits: -4 * order });
    mesh.renderOrder = order;
    mesh.userData = { layer: l };
    v3.root.add(mesh);
  }
  // vector layers: lines and outlines follow the ground (split into short pieces), points sit on it
  function addVector(l, relief, order) {
    const T = v3.T, lift = Math.max(1, relief * 0.006), seg = [], pts = [];
    const step = v3.surfaces.length ? Math.max(...v3.surfaces.map(({ g }) => (g.merc[2] - g.merc[0]) / g.width)) : Infinity;
    let budget = 400000;
    const at = (lon, lat) => { const [mx, my] = toMerc(lon, lat), [x, y] = toScene(mx, my); return [x, y, heightAt(mx, my) + lift, mx, my]; };
    const line = (coords) => {
      for (let i = 1; i < coords.length && budget > 0; i++) {
        const [x0, y0] = toMerc(...coords[i - 1]), [x1, y1] = toMerc(...coords[i]);
        const n = Number.isFinite(step) ? Math.min(200, Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / step))) : 1;
        let prev = at(...coords[i - 1]);
        for (let k = 1; k <= n; k++) {
          const mx = x0 + (x1 - x0) * k / n, my = y0 + (y1 - y0) * k / n, [x, y] = toScene(mx, my), cur = [x, y, heightAt(mx, my) + lift];
          seg.push(prev[0], prev[1], prev[2], cur[0], cur[1], cur[2]);
          prev = cur; budget--;
        }
      }
    };
    const walk = (g) => {
      if (!g) return;
      const c = g.coordinates;
      if (g.type === "Point") pts.push(...at(...c).slice(0, 3));
      else if (g.type === "MultiPoint") c.forEach((p) => pts.push(...at(...p).slice(0, 3)));
      else if (g.type === "LineString") line(c);
      else if (g.type === "MultiLineString" || g.type === "Polygon") c.forEach(line);
      else if (g.type === "MultiPolygon") c.forEach((poly) => poly.forEach(line));
      else if (g.type === "GeometryCollection") g.geometries.forEach(walk);
    };
    (l.geojson?.features || []).forEach((f) => walk(f.geometry));
    const color = new T.Color(l.color || "#2563eb");
    if (seg.length) {
      const geo = new T.BufferGeometry();
      geo.setAttribute("position", new T.Float32BufferAttribute(seg, 3));
      const m = new T.LineSegments(geo, new T.LineBasicMaterial({ color, transparent: true, opacity: l.opacity ?? 1 }));
      m.renderOrder = 1000 + order;
      v3.root.add(m);
    }
    if (pts.length) {
      const geo = new T.BufferGeometry();
      geo.setAttribute("position", new T.Float32BufferAttribute(pts, 3));
      const m = new T.Points(geo, new T.PointsMaterial({ color, size: 8, sizeAttenuation: false, transparent: true, opacity: l.opacity ?? 1 }));
      m.renderOrder = 1000 + order;
      v3.root.add(m);
    }
  }
  // the basemap under everything: its tiles over the scene's extent, a little below the lowest ground
  function addGround(relief) {
    const T = v3.T, name = prefs.get("basemap", "streets"), tiles = BASEMAPS[name];
    const box = extent3d(), pad = Math.max(box[2] - box[0], box[3] - box[1]) * 0.35;
    const want = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad];
    if (!tiles) {   // no basemap: a grid on the ground
      const [sx0, sy0] = toScene(want[0], want[1]), [sx1, sy1] = toScene(want[2], want[3]), size = Math.max(sx1 - sx0, sy1 - sy0);
      const g = new T.GridHelper(size, 20, 0x8a94a6, 0xb8c0cc);
      g.rotation.x = Math.PI / 2;
      g.position.set((sx0 + sx1) / 2, (sy0 + sy1) / 2, -Math.max(0.5, relief * 0.003));
      v3.root.add(g);
      return;
    }
    const zMax = tiles.options.maxZoom || 18, span = Math.max(want[2] - want[0], want[3] - want[1]);
    const z = Math.max(0, Math.min(zMax, Math.floor(Math.log2(2048 * 2 * HALF_WORLD / (256 * span)))));
    const n = 2 ** z, size = 2 * HALF_WORLD / n;
    const tx0 = Math.max(0, Math.floor((want[0] + HALF_WORLD) / size)), tx1 = Math.min(n - 1, Math.floor((want[2] + HALF_WORLD) / size));
    const ty0 = Math.max(0, Math.floor((HALF_WORLD - want[3]) / size)), ty1 = Math.min(n - 1, Math.floor((HALF_WORLD - want[1]) / size));
    const key = `${name}|${z}|${tx0}|${tx1}|${ty0}|${ty1}`;
    if (v3.ground?.key !== key) {
      v3.ground?.tex.dispose();
      const cv = document.createElement("canvas");
      cv.width = (tx1 - tx0 + 1) * 256; cv.height = (ty1 - ty0 + 1) * 256;
      const ctx = cv.getContext("2d"), tex = new T.CanvasTexture(cv);
      tex.colorSpace = T.SRGBColorSpace;
      ctx.fillStyle = "#e9e6df"; ctx.fillRect(0, 0, cv.width, cv.height);
      v3.ground = { key, tex, box: [tx0 * size - HALF_WORLD, HALF_WORLD - (ty1 + 1) * size, (tx1 + 1) * size - HALF_WORLD, HALF_WORLD - ty0 * size] };
      for (let ty = ty0; ty <= ty1; ty++) for (let tx = tx0; tx <= tx1; tx++) {
        const img = new Image();
        img.crossOrigin = "anonymous";   // without it the picture couldn't be used in WebGL
        img.onload = () => { ctx.drawImage(img, (tx - tx0) * 256, (ty - ty0) * 256); tex.needsUpdate = true; render3d(); };
        img.src = L.Util.template(tiles._url, { s: "abc"[(tx + ty) % 3], z, x: tx, y: ty, r: "" });
      }
    }
    const [x0, y0, x1, y1] = v3.ground.box, [sx0, sy0] = toScene(x0, y0), [sx1, sy1] = toScene(x1, y1);
    const geo = new T.PlaneGeometry(sx1 - sx0, sy1 - sy0);
    const mat = v3.style === "wireframe" ? new T.MeshBasicMaterial({ map: v3.ground.tex, transparent: true, opacity: 0.35 })
      : new T.MeshBasicMaterial({ map: v3.ground.tex });
    const m = new T.Mesh(geo, mat);
    m.position.set((sx0 + sx1) / 2, (sy0 + sy1) / 2, -Math.max(0.5, relief * 0.003));
    m.renderOrder = 0;
    m.userData = { ground: true };
    v3.root.add(m);
  }
  // the scene's extent (Web Mercator): every visible layer, or what the 2D map showed
  function extent3d() {
    let box = null;
    const add = (b) => { if (!b) return; box = box ? [Math.min(box[0], b[0]), Math.min(box[1], b[1]), Math.max(box[2], b[2]), Math.max(box[3], b[3])] : b; };
    src3d().filter((l) => l.visible).forEach((l) => {
      const b = l.type === "vector" && !l.leaflet ? L.geoJSON(l.geojson).getBounds() : layerBounds(l);   // a map not drawn in 2D
      if (b?.isValid()) add(mercBox([[b.getSouth(), b.getWest()], [b.getNorth(), b.getEast()]]));
    });
    if (!box) { const b = map.getBounds(); add(mercBox([[b.getSouth(), b.getWest()], [b.getNorth(), b.getEast()]])); }
    if (box[2] - box[0] < 50) { box[0] -= 25; box[2] += 25; }
    if (box[3] - box[1] < 50) { box[1] -= 25; box[3] += 25; }
    return box;
  }

  // the first layers of a 3D map set its origin (0, 0, 0) to their centre; the camera keeps looking at the same place
  function moveOrigin() {
    const box = extent3d(), o = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2], [dx, dy] = toScene(...o);
    v3.camera.position.x -= dx; v3.camera.position.y -= dy;
    v3.controls.target.x -= dx; v3.controls.target.y -= dy;
    v3.origin = v3.doc.origin = o;
    v3.cosLat = Math.cos(fromMerc(...o)[1] * Math.PI / 180);
    v3.doc.originSet = true;
    v3.ground = null;
    v3.controls.update();
  }

  // ---- camera: fit an extent, go to a named view (animated)
  function fit3d(box, dir) {
    const T = v3.T;
    box ||= extent3d();
    const [x0, y0] = toScene(box[0], box[1]), [x1, y1] = toScene(box[2], box[3]);
    const zTop = v3.surfaces.length ? Math.max(...v3.surfaces.map(({ g }) => ((g.max ?? 0) - v3.base) * v3.k)) : 0;
    const center = new T.Vector3((x0 + x1) / 2, (y0 + y1) / 2, zTop / 3);
    const radius = Math.max(Math.hypot(x1 - x0, y1 - y0) / 2, zTop / 2, 10);
    const d = new T.Vector3(...(dir || v3.camera.position.clone().sub(v3.controls.target).toArray())).normalize();
    const dist = radius / Math.sin(v3.camera.fov * Math.PI / 360) * 1.05;
    v3.controls.target.copy(center);
    v3.camera.position.copy(center).addScaledVector(d, dist);
    v3.controls.update();
  }
  function zoom3dTo(latLngBounds) {
    if (!v3.on || !v3.T || !latLngBounds?.isValid()) return;
    fit3d(mercBox([[latLngBounds.getSouth(), latLngBounds.getWest()], [latLngBounds.getNorth(), latLngBounds.getEast()]]));
  }
  function view3d(name, dir) {
    const T = v3.T, to = new T.Vector3(...(dir || VIEWS3D[name])).normalize();
    const target = v3.controls.target.clone(), dist = v3.camera.position.distanceTo(target);
    const from = v3.camera.position.clone().sub(target).normalize(), q = new T.Quaternion().setFromUnitVectors(from, to), qi = new T.Quaternion();
    v3.viewName = name || "Custom";
    vpcLabels();
    cancelAnimationFrame(v3.anim);
    const t0 = performance.now(), step = (t) => {
      const k = Math.min(1, (t - t0) / 450), e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
      qi.identity().slerp(q, e);
      v3.camera.position.copy(target).addScaledVector(from.clone().applyQuaternion(qi), dist);
      v3.controls.update();
      if (k < 1) v3.anim = requestAnimationFrame(step);
    };
    v3.anim = requestAnimationFrame(step);
  }

  function render3d() {
    if (!v3.on || !v3.renderer) return;
    const dist = v3.camera.position.distanceTo(v3.controls.target);
    v3.camera.near = Math.max(0.1, dist / 2000);
    v3.camera.far = dist * 60 + 1e5;
    v3.camera.updateProjectionMatrix();
    v3.renderer.render(v3.scene, v3.camera);
    renderViewCube();
    renderUcs();
  }

  // ---- the ViewCube (top right): turns with the view; click a face, edge or corner to look from there
  function labelTexture(text, { bg = "#f4f6f8", fg = "#33415c", w = 128, h = 128, font = 26 } = {}) {
    const cv = document.createElement("canvas");
    cv.width = w; cv.height = h;
    const c = cv.getContext("2d");
    if (bg) { c.fillStyle = bg; c.fillRect(0, 0, w, h); c.strokeStyle = "#9aa6b6"; c.lineWidth = 4; c.strokeRect(2, 2, w - 4, h - 4); }
    c.fillStyle = fg; c.font = `600 ${font}px -apple-system, Segoe UI, sans-serif`; c.textAlign = "center"; c.textBaseline = "middle";
    c.fillText(text, w / 2, h / 2);
    const t = new v3.T.CanvasTexture(cv);
    t.colorSpace = v3.T.SRGBColorSpace;
    return t;
  }
  // The cube is made of 26 hotspots, as in AutoCAD: 6 face centres, 12 edges and 8 corners. Each lights up under the
  // pointer and, clicked, looks from its direction (a corner gives an isometric view, an edge a view between two faces).
  const FACE_NAMES = { "0,0,1": "TOP", "0,0,-1": "BOTTOM", "0,-1,0": "FRONT", "0,1,0": "BACK", "-1,0,0": "LEFT", "1,0,0": "RIGHT" };
  function spotName(d) {
    const name = Object.entries(VIEWS3D).find(([, v]) => v.every((x, i) => Math.round(x) === d[i]))?.[0];
    if (name) return name;
    const parts = [d[2] > 0 ? "Top" : d[2] < 0 ? "Bottom" : "", d[1] < 0 ? "Front" : d[1] > 0 ? "Back" : "", d[0] < 0 ? "Left" : d[0] > 0 ? "Right" : ""];
    return parts.filter(Boolean).join(" · ") + (d.filter(Boolean).length === 3 ? " corner" : " edge");
  }
  function initViewCube() {
    const T = v3.T, box = $("#v3-cube");
    const r = new T.WebGLRenderer({ antialias: true, alpha: true });
    r.setPixelRatio(Math.min(2, devicePixelRatio || 1));
    r.setSize(120, 120);
    box.prepend(r.domElement);
    const scene = new T.Scene(), cam = new T.OrthographicCamera(-1.15, 1.15, 1.15, -1.15, 0.1, 20);
    cam.up.set(0, 0, 1);
    scene.add(new T.AmbientLight(0xffffff, 2.2));
    const light = new T.DirectionalLight(0xffffff, 1.2);
    scene.add(light);
    // the 26 pieces: along each axis a corner strip (0.2), the middle (0.6) and the other corner strip (0.2)
    const SIZE = { "-1": 0.2, 0: 0.6, 1: 0.2 }, POS = { "-1": -0.4, 0: 0, 1: 0.4 }, BASE = 0xeef1f5, HOT = 0x8fd0b5;
    const cube = new T.Group(), pieces = [];
    for (const x of [-1, 0, 1]) for (const y of [-1, 0, 1]) for (const z of [-1, 0, 1]) {
      if (!x && !y && !z) continue;
      const m = new T.Mesh(new T.BoxGeometry(SIZE[x], SIZE[y], SIZE[z]), new T.MeshLambertMaterial({ color: BASE }));
      m.position.set(POS[x], POS[y], POS[z]);
      m.userData.dir = [x, y, z];
      cube.add(m); pieces.push(m);
    }
    // the face names, on the face centres (each a plane just outside the face, upright as seen from there)
    Object.entries(FACE_NAMES).forEach(([k, name]) => {
      const n = k.split(",").map(Number), piece = pieces.find((p) => p.userData.dir.join() === n.join());
      const label = new T.Mesh(new T.PlaneGeometry(0.6, 0.6), new T.MeshBasicMaterial({ map: labelTexture(name, { bg: null, font: name.length > 4 ? 30 : 36 }), transparent: true }));
      label.position.set(...n.map((v) => v * 0.502));
      label.up.set(...(n[2] ? [0, n[2], 0] : [0, 0, 1]));   // TOP reads with north up, the sides with up up
      label.lookAt(...n.map((v) => v * 2));
      label.userData.piece = piece;
      cube.add(label);
    });
    // the cube's outline and the lines between its pieces
    cube.add(new T.LineSegments(new T.EdgesGeometry(new T.BoxGeometry(1, 1, 1)), new T.LineBasicMaterial({ color: 0x8a96a8 })));
    pieces.forEach((p) => {
      const lines = new T.LineSegments(new T.EdgesGeometry(p.geometry), new T.LineBasicMaterial({ color: 0xc9d1dc }));
      lines.position.copy(p.position);
      cube.add(lines);
    });
    scene.add(cube);
    const ring = new T.Mesh(new T.RingGeometry(0.78, 0.9, 48), new T.MeshBasicMaterial({ color: 0x9aa6b6, transparent: true, opacity: 0.55, side: T.DoubleSide }));
    ring.position.z = -0.55;
    scene.add(ring);
    [["N", 0, 1], ["E", 1, 0], ["S", 0, -1], ["W", -1, 0]].forEach(([t, x, y]) => {
      const s = new T.Sprite(new T.SpriteMaterial({ map: labelTexture(t, { bg: null, fg: t === "N" ? "#c0392b" : "#55627a", w: 64, h: 64, font: 40 }) }));
      s.position.set(x * 0.84, y * 0.84, -0.55);
      s.scale.set(0.3, 0.3, 1);
      scene.add(s);
    });
    v3.cube = { r, scene, cam, light };
    const ray = new T.Raycaster(), targets = cube.children.filter((o) => o.isMesh);
    let hot = null;
    const hit = (e) => {
      const rc = r.domElement.getBoundingClientRect();
      ray.setFromCamera(new T.Vector2(((e.clientX - rc.left) / rc.width) * 2 - 1, -((e.clientY - rc.top) / rc.height) * 2 + 1), cam);
      const o = ray.intersectObjects(targets, false)[0]?.object;
      return o ? o.userData.piece || o : null;
    };
    const setHot = (p) => {
      if (p === hot) return;
      hot?.material.color.set(BASE);
      hot = p;
      hot?.material.color.set(HOT);
      r.domElement.style.cursor = p ? "pointer" : "default";
      r.domElement.title = p ? `${spotName(p.userData.dir)}: click to look from here` : "";
      renderViewCube();
    };
    r.domElement.addEventListener("pointermove", (e) => setHot(hit(e)));
    r.domElement.addEventListener("pointerleave", () => setHot(null));
    r.domElement.addEventListener("click", (e) => {
      const p = hit(e);
      if (!p) return;
      const d = p.userData.dir, name = Object.entries(VIEWS3D).find(([, v]) => v.every((x, i) => Math.round(x) === d[i]))?.[0];
      view3d(name || null, d);
    });
    $(".v3-home", box).onclick = () => { v3.viewName = "SW Isometric"; vpcLabels(); fit3d(null, VIEWS3D["SW Isometric"]); };
  }
  function renderViewCube() {
    const c = v3.cube;
    if (!c) return;
    const d = v3.camera.position.clone().sub(v3.controls.target).normalize();
    c.cam.position.copy(d.multiplyScalar(5));
    c.cam.quaternion.copy(v3.camera.quaternion);
    c.light.position.copy(c.cam.position).add(new v3.T.Vector3(0, 0, 3));   // lit from the eye, a little from above
    c.r.render(c.scene, c.cam);
  }

  // ---- the UCS icon (bottom left): the X, Y and Z axes as the view sees them
  function renderUcs() {
    const T = v3.T, q = v3.camera.quaternion.clone().invert();
    const axes = [["X", [1, 0, 0], "#d63c3c"], ["Y", [0, 1, 0], "#2f9e44"], ["Z", [0, 0, 1], "#2f6fd6"]]
      .map(([n, v, col]) => { const p = new T.Vector3(...v).applyQuaternion(q); return { n, col, x: p.x * 30, y: -p.y * 30, z: p.z }; })
      .sort((a, b) => a.z - b.z);
    $("#v3-ucs").innerHTML = `<rect x="-3.5" y="-3.5" width="7" height="7" fill="none" stroke="currentColor" stroke-width="1.4" opacity=".7"/>` +
      axes.map((a) => `<line x1="0" y1="0" x2="${a.x.toFixed(1)}" y2="${a.y.toFixed(1)}" stroke="${a.col}" stroke-width="2.6" stroke-linecap="round"/>
        <text x="${(a.x * 1.28).toFixed(1)}" y="${(a.y * 1.28 + 4).toFixed(1)}" fill="${a.col}" font-size="12" font-weight="700" text-anchor="middle">${a.n}</text>`).join("");
  }

  // ---- the viewport controls (top left): [–] options, [view], [visual style]
  function vpcLabels() {
    $("#v3-view").textContent = `[${v3.viewName}]`;
    $("#v3-style").textContent = `[${STYLES3D[v3.style]}]`;
  }
  function vpcMenu(which, e) {
    e.stopPropagation();
    const r = e.currentTarget.getBoundingClientRect(), x = r.left, y = r.bottom + 4;
    const mark = (on, t) => `${on ? "● " : "    "}${t}`;
    if (which === "view") showMenu("View", Object.keys(VIEWS3D).map((n) => [mark(v3.viewName === n, n), () => view3d(n)]), x, y);
    else if (which === "style") showMenu("Visual style", Object.entries(STYLES3D).map(([k, t]) => [mark(v3.style === k, t), () => { v3.style = k; prefs.set("v3-style", k); vpcLabels(); build3d(); }]), x, y);
    else showMenu("3D view", [
      ["Zoom to all layers", () => fit3d()],
      [mark(v3.cubeOn, "ViewCube"), () => setShown("cube", !v3.cubeOn)],
      [mark(v3.nav, "Navigation bar"), () => setShown("nav", !v3.nav)],
      [mark(v3.ucsOn, "UCS icon"), () => setShown("ucs", !v3.ucsOn)],
      "-",
      ["Reset the view (SW Isometric)", () => { v3.viewName = "SW Isometric"; vpcLabels(); fit3d(null, VIEWS3D["SW Isometric"]); }],
      "-",
      ...["auto", 0.5, 1, 2, 3, 5, 10].map((k) => [mark(v3.exag === k, k === "auto" ? `Heights: auto (now × ${v3.k})` : `Heights × ${k}`),
        () => { v3.exag = k; prefs.set("v3-exag", k); build3d(); }]),
      "-",
      ["Mouse, as in AutoCAD: wheel = zoom · middle-drag = pan · Shift + middle-drag = orbit · double-click the wheel = zoom extents", () => {}],
      ["Also: left-drag = orbit · right-drag (or Shift + left-drag) = pan · double-click a point = look at it", () => {}],
    ], x, y);
  }
  $("#v3-menu").onclick = (e) => vpcMenu("menu", e);
  $("#v3-view").onclick = (e) => vpcMenu("view", e);
  $("#v3-style").onclick = (e) => vpcMenu("style", e);

  // ---- the cursor's coordinates (and the height under it) in the status bar
  function pick3d(e) {
    const T = v3.T, rc = v3.renderer.domElement.getBoundingClientRect();
    const ray = new T.Raycaster();
    ray.setFromCamera(new T.Vector2(((e.clientX - rc.left) / rc.width) * 2 - 1, -((e.clientY - rc.top) / rc.height) * 2 + 1), v3.camera);
    return ray.intersectObjects(v3.root.children.filter((o) => o.isMesh), false)[0] || null;
  }
  function pointerCoords(e) {
    if (!v3.on || !e) return;
    const hit = pick3d(e);
    if (!hit) return showCoords(null);
    const [mx, my] = fromScene(hit.point.x, hit.point.y), [lon, lat] = fromMerc(mx, my);
    showCoords({ lat, lng: lon });
    if (v3.surfaces.length && !hit.object.userData.ground) $("#sb-coords").textContent += `  Z ${fmt(hit.point.z / v3.k + v3.base, 1)} m`;
  }

  // ---- show / hide the ViewCube, the navigation bar and the UCS icon (the [–] menu, as in AutoCAD)
  function setShown(what, on) {
    if (what === "cube") { v3.cubeOn = on; prefs.set("v3-cube", on); }
    if (what === "nav") { v3.nav = on; prefs.set("v3-nav", on); }
    if (what === "ucs") { v3.ucsOn = on; prefs.set("v3-ucs", on); }
    $("#v3-cube").classList.toggle("hidden", !v3.cubeOn);
    $("#v3-wcs").classList.toggle("hidden", !v3.cubeOn);
    $("#v3-nav").classList.toggle("hidden", !v3.nav);
    $("#v3-ucs").classList.toggle("hidden", !v3.ucsOn);
  }
  setShown();

  // ---- the navigation bar: what the left button does (orbit or pan), zoom, continuous orbit, views
  function setNavMode(mode) {
    stopSpin();
    v3.mode = mode;
    if (v3.controls) v3.controls.mouseButtons.LEFT = mode === "pan" ? v3.T.MOUSE.PAN : v3.T.MOUSE.ROTATE;
    $$("#v3-nav [data-nav=pan], #v3-nav [data-nav=orbit]").forEach((b) => b.classList.toggle("on", b.dataset.nav === mode));
  }
  function zoom3dBy(f) {
    if (!v3.controls) return;
    const t = v3.controls.target;
    v3.camera.position.sub(t).multiplyScalar(f).add(t);
    v3.controls.update();
  }
  // continuous orbit: the view keeps turning until a click on the scene (or another tool)
  function startSpin() {
    if (!v3.controls) return;
    stopSpin();
    v3.controls.autoRotate = true;
    v3.controls.autoRotateSpeed = 1.6;
    v3.viewName = "Custom"; vpcLabels();
    const loop = () => { if (!v3.controls.autoRotate || !v3.on) return; v3.controls.update(); v3.spin = requestAnimationFrame(loop); };
    v3.spin = requestAnimationFrame(loop);
    toast("Continuous orbit: click the 3D view to stop");
  }
  function stopSpin() {
    if (v3.controls?.autoRotate) { v3.controls.autoRotate = false; cancelAnimationFrame(v3.spin); }
  }
  $("#map3d").addEventListener("pointerdown", (e) => { if (e.target.classList.contains("v3-canvas")) stopSpin(); }, true);
  $("#v3-nav").addEventListener("click", (e) => {
    const b = e.target.closest("[data-nav]");
    if (!b || !v3.controls) { if (b?.dataset.nav === "close") setShown("nav", false); return; }
    e.stopPropagation();
    const r = b.getBoundingClientRect(), x = r.left - 230, y = r.top;
    switch (b.dataset.nav) {
      case "close": setShown("nav", false); break;
      case "home": stopSpin(); v3.viewName = "SW Isometric"; vpcLabels(); fit3d(null, VIEWS3D["SW Isometric"]); break;
      case "pan": setNavMode("pan"); break;
      case "orbit": setNavMode("orbit"); break;
      case "zoom": stopSpin(); fit3d(); break;
      case "zoom-menu": showMenu("Zoom", [
        ["Zoom extents (everything)", () => fit3d()],
        ["Zoom in", () => zoom3dBy(0.6)],
        ["Zoom out", () => zoom3dBy(1.6)],
        selectedLayer() ? [`Zoom to “${selectedLayer().name}”`, () => zoomTo(selectedLayer())] : null,
      ].filter(Boolean), x, y); break;
      case "orbit-menu": showMenu("Orbit", [
        [`${v3.mode === "orbit" && !v3.controls.autoRotate ? "● " : "    "}Orbit (drag to turn)`, () => setNavMode("orbit")],
        [`${v3.controls.autoRotate ? "● " : "    "}Continuous orbit (keeps turning)`, () => startSpin()],
      ], x, y); break;
      case "views": showMenu("View", Object.keys(VIEWS3D).map((n) => [`${v3.viewName === n ? "● " : "    "}${n}`, () => view3d(n)]), x, y); break;
    }
  });
  // WCS: the coordinate system of the 3D view
  $("#v3-wcs").onclick = (e) => {
    e.stopPropagation();
    const r = e.currentTarget.getBoundingClientRect(), [lon, lat] = fromMerc(...v3.origin);
    showMenu("Coordinate system", [
      ["● WCS (world): X east, Y north, Z up", () => {}],
      [`Origin (0, 0, 0): ${lat.toFixed(5)}, ${lon.toFixed(5)}${v3.surfaces.length ? ` at ${fmt(v3.base, 1)} m` : ""}`, () => copyText(`${lat.toFixed(6)}, ${lon.toFixed(6)}`, "Origin copied (lat, lon)")],
      [`Units: metres · heights × ${v3.k}`, () => {}],
    ], r.left - 200, r.bottom + 4);
  };

  // ---- 3D extrude: polygons raised by an attribute (a building's height, a field's yield) or by a fixed height, standing on
  // the land; lighter to darker with the value. Heights are multiplied by the map's height exaggeration, like the land.
  const polyRings = (g) => !g ? [] : g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates
    : g.type === "GeometryCollection" ? g.geometries.flatMap(polyRings) : [];
  function extrudeValue(l, f) {
    const ex = l.extrude, v = ex.field ? Number(f.properties?.[ex.field]) : Number(ex.height);
    return Number.isFinite(v) && v > 0 ? v * (ex.scale ?? 1) : null;
  }
  function addExtrusions(l) {
    const T = v3.T, feats = (l.geojson?.features || []).slice(0, 8000);
    const vals = feats.map((f) => extrudeValue(l, f)).filter((v) => v != null);
    if (!vals.length) return;
    const lo = Math.min(...vals), hi = Math.max(...vals), base = new T.Color(l.color || "#2563eb"), hsl = base.getHSL({});
    const mats = Array.from({ length: 8 }, (_, i) => new T.MeshLambertMaterial({   // 8 shades, light (low) to dark (high)
      color: new T.Color().setHSL(hsl.h, Math.max(0.35, hsl.s), 0.78 - (i / 7) * 0.42), transparent: (l.opacity ?? 1) < 1, opacity: l.opacity ?? 1 }));
    const xy = ([lon, lat]) => { const [mx, my] = toMerc(lon, lat); return toScene(mx, my); };
    feats.forEach((f) => {
      const v = extrudeValue(l, f);
      if (v == null) return;
      const shade = mats[hi > lo ? Math.round((v - lo) / (hi - lo) * 7) : 7];
      polyRings(f.geometry).forEach((rings) => {
        if (!rings?.[0] || rings[0].length < 4) return;
        const shape = new T.Shape(rings[0].map((c) => new T.Vector2(...xy(c))));
        rings.slice(1).forEach((r) => shape.holes.push(new T.Path(r.map((c) => new T.Vector2(...xy(c))))));
        const ground = Math.min(...rings[0].map((c) => { const [mx, my] = toMerc(...c); return heightAt(mx, my); }));   // stands on the lowest corner
        const geo = new T.ExtrudeGeometry(shape, { depth: v * v3.k, bevelEnabled: false });
        geo.translate(0, 0, ground);
        const m = new T.Mesh(geo, shade);
        m.userData = { layer: l, extruded: true, value: v };
        v3.root.add(m);
      });
    });
  }
  // the dialog: which attribute (numbers only), or a fixed height; times a factor
  function openExtrude(l) {
    const props = (l.geojson?.features || []).slice(0, 500).map((f) => f.properties || {});
    const fields = [...new Set(props.flatMap(Object.keys))].filter((k) => {
      const v = props.map((p) => p[k]).filter((x) => x !== null && x !== undefined && x !== "");
      return v.length && v.filter((x) => Number.isFinite(Number(x))).length / v.length > 0.8;
    });
    const ex = l.extrude || {};
    $("#ex-title").textContent = `Extrude “${l.name}” in 3D`;
    $("#ex-field").innerHTML = fields.map((k) => `<option value="${esc(k)}">${esc(k)}</option>`).join("") + `<option value="">A fixed height</option>`;
    $("#ex-field").value = ex.field !== undefined ? ex.field : fields.find((k) => /height|elev|floors?|levels?|yield|value/i.test(k)) || fields[0] || "";
    $("#ex-height").value = ex.height ?? 10;
    $("#ex-scale").value = ex.scale ?? 1;
    const sync = () => {
      const f = $("#ex-field").value;
      $("#ex-height-row").classList.toggle("hidden", !!f);
      const v = f ? props.map((p) => Number(p[f])).filter(Number.isFinite) : [];
      $("#ex-hint").textContent = f ? (v.length ? `“${f}” goes from ${fmtv(Math.min(...v))} to ${fmtv(Math.max(...v))}: each polygon rises that many metres × the factor (floors? use 3 as the factor).` : "")
        : "Every polygon rises by the same height.";
    };
    $("#ex-field").onchange = sync;
    sync();
    $("#ex-remove").classList.toggle("hidden", !l.extrude);
    $("#ex-remove").onclick = () => { delete l.extrude; saveLayers(); map3dChanged(); $("#dlg-extrude").close(); };
    $("#ex-apply").onclick = () => {
      const field = $("#ex-field").value, scale = +$("#ex-scale").value || 1, height = +$("#ex-height").value || 10;
      l.extrude = field ? { field, scale } : { field: "", height, scale };
      saveLayers(); map3dChanged();
      $("#dlg-extrude").close();
      toast(`“${l.name}” is extruded${field ? ` by “${field}”` : ` ${height} m`}${scale !== 1 ? ` × ${scale}` : ""}`);
    };
    $("#dlg-extrude").showModal();
  }
