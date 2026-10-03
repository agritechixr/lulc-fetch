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
    fill: (sel, empty, opts = {}) => LF.fillLayers(sel, LF.emb.layers(), { label: (l) => `${l.name} · ${l.info.count} bands`, empty, ...opts }),
    /** a file-name-safe default name from a layer name and a suffix */
    name: (layerName, suffix) => `${(layerName || "embedding").replace(/\.(tiff?|vrt)$/i, "")}_${suffix}`,
  };
})();
