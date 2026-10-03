/* Embeddings menu: what its tools share, as LF.emb.
   Server side: webapp/routes/embeddings.py · the science: lulc_fetch/embeddings/ (and lulc_fetch/lightseg/ for the models) */
(() => {
  "use strict";
  let meta = null;
  LF.emb = {
    /** the embedding sources, their years and the storage formats (asked from the server once) */
    async meta() { return meta ||= await LF.api("/api/emb/sources"); },
    /** layers that look like embeddings: rasters with 16 bands or more */
    layers: () => LF.layers.filter((l) => l.type === "raster" && l.path && (l.info?.count || 0) >= 16),
    /** fill a <select> with the embedding layers; returns the chosen layer */
    fill: (sel, empty, opts = {}) => LF.fillLayers(sel, LF.emb.layers(), { label: (l) => `${l.name} · ${l.info.count} bands`, empty: LF.emb.why(empty), ...opts }),
    /** when no layer qualifies, say why the rasters in Contents don't (a colour view is a 3-band picture, not the embedding) */
    why(empty) {
      const few = LF.layers.filter((l) => l.type === "raster" && l.path && (l.info?.count || 0) < 16);
      if (!few.length) return empty;
      const colour = few.find((l) => /_colou?r(\.tiff?)?$/i.test(l.name) || /_colou?r\.tiff?$/i.test(l.path));
      if (colour) return `“${colour.name}” is only the colour picture (${colour.info.count} bands): add the embedding itself, ${colour.name.replace(/_colou?r(\.tiff?)?$/i, "")}.tif (64 or 128 bands)`;
      return `No embedding: ${few.slice(0, 2).map((l) => `${l.name} has ${l.info?.count ?? "?"} band${l.info?.count === 1 ? "" : "s"}`).join(", ")}; an embedding has 16 or more (AlphaEarth 64, TESSERA 128)`;
    },
    /** a file-name-safe default name from a layer name and a suffix */
    name: (layerName, suffix) => `${(layerName || "embedding").replace(/\.(tiff?|vrt)$/i, "")}_${suffix}`,
  };
})();
