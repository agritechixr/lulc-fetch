/* Agri menu: what its tools share, as LF.agri.
   Server side: webapp/routes/agri.py · the science: lulc_fetch/agri/ (models, knowledge base) */
(() => {
  "use strict";
  let schema = null;
  LF.agri = {
    /** the crops, their models and the knowledge-base sections; fresh = ask again (e.g. the models folder changed) */
    async schema(fresh = false) {
      if (fresh || !schema) schema = await LF.api("/api/agri/schema");
      return schema;
    },
    /** 0.873 → "87 %" (one decimal near 0 and 100 %) */
    pct: (v) => v == null ? "–" : `${LF.fmt(100 * v, v >= 0.995 || v < 0.1 ? 1 : 0)} %`,
  };
})();
