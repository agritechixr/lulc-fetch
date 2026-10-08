  // The Tools / Agri / Embeddings menus, the start page and switchTool(), which opens a tool.
  // (part of app.js: the server joins webapp/static/app/parts.json in order, inside one closure)

  // ------------------------------------------------------------------ tools (Tools menu + tool panel)
  // To add a tool: add <section id="tab-<id>" class="tabpanel hidden"> to index.html and an entry here.
  const ICONS = {
    forecast: '<path d="M3 20h18" opacity=".5"/><path d="M4 16l4-5 4 3 3-4"/><path d="M15 10l3-2 3-3" stroke-dasharray="2 2"/><circle cx="15" cy="10" r="1.3" fill="currentColor" stroke="none"/>',
    fcrun: '<path d="M3 20h18" opacity=".5"/><path d="M4 15l4-4 3 2" /><path d="M11 13l3-3 3 1 4-4" stroke-dasharray="2 2"/><path d="M5 4l4 2.5L5 9z" fill="currentColor" stroke="none"/>',
    fmember: '<path d="M3 19h18" opacity=".5"/><path d="M3 18c4 0 5-12 9-12s5 12 9 12"/><path d="M12 6v13" stroke-dasharray="2 2" opacity=".6"/>',
    foverlay: '<path d="M4 15l8 4 8-4"/><path d="M4 11l8 4 8-4" opacity=".75"/><path d="M4 7l8-4 8 4-8 4z" fill="currentColor" fill-opacity=".25"/>',
    fboundary: '<path d="M4 5c5 1 6 6 3 9s1 6 4 6" stroke-width="1.6"/><path d="M8 4c5 1 6 6 3 9s1 6 4 7" stroke-dasharray="2 2" opacity=".55"/><path d="M12 4c5 1 6 6 3 9s1 6 4 7" stroke-dasharray="1 3" opacity=".35"/>',
    fcmeans: '<circle cx="8.5" cy="9" r="5" fill="currentColor" fill-opacity=".15"/><circle cx="15.5" cy="9" r="5" fill="currentColor" fill-opacity=".15"/><circle cx="12" cy="15" r="5" fill="currentColor" fill-opacity=".15"/>',
    rmosaic: '<rect x="3" y="3" width="10" height="10" rx="1"/><rect x="11" y="11" width="10" height="10" rx="1"/><path d="M11 13h2v-2" opacity=".55"/><path d="M6 21h3M3 18v3M21 6V3h-3" opacity=".5"/>',
    rburn: '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.2 0-3.4 2.4-5.4 3.6-8.3.5 1.7 1.4 2.8 2.6 3.3.1-2.8 1.3-5.3 3.4-6.8-.3 3 .9 5 2.3 6.9 1 1.4 1.6 2.9 1.6 4.9 0 3.6-3.1 6.2-7 6.2z"/><path d="M12 21c-1.6 0-2.7-1.1-2.7-2.6 0-1.6 1.5-2.6 2.2-4.2.9 1.3 3.2 2.3 3.2 4.3 0 1.4-1.1 2.5-2.7 2.5z"/>',
    vstats: '<circle cx="6" cy="17" r="1.6"/><circle cx="9.5" cy="13" r="1.6"/><circle cx="7" cy="9" r="1.6"/><circle cx="17.5" cy="7" r="1.6"/><circle cx="16" cy="17.5" r="1.6"/><circle cx="8" cy="12" r="6" stroke-dasharray="2 2" opacity=".6"/>',
    online: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.8 5.2 3.8 8.5s-1.2 6.1-3.8 8.5c-2.6-2.4-3.8-5.2-3.8-8.5s1.2-6.1 3.8-8.5z"/>',
    field: '<rect x="6.5" y="2.5" width="11" height="19" rx="2.2"/><path d="M12 15.5s-3-3-3-5.2a3 3 0 0 1 6 0c0 2.2-3 5.2-3 5.2z"/><circle cx="12" cy="10.3" r=".9" fill="currentColor" stroke="none"/><path d="M10.5 19h3" opacity=".6"/>',
    cloud: '<path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.5 1.5A3.3 3.3 0 0 0 7 18z"/><path d="M8 21v-1M12 21v-1M16 21v-1" opacity=".7"/>',
    interp: '<circle cx="5" cy="17" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="7" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="14" r="1.6" fill="currentColor" stroke="none"/><path d="M3 12c3-5 6-7 9-6s4 5 9 3" opacity=".75"/><path d="M3 20c4-3 8-4 11-3s5 1 7-1" opacity=".45"/>',
    search: '<path d="M4 7l4-4 4 4-4 4z"/><path d="M12 15l4-4 4 4-4 4z"/><path d="M9.5 9.5l5 5"/><path d="M3 21c1.5-3 4-4.5 7-4.5"/>',
    analyze: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17.5l9 5 9-5" opacity=".5"/>',
    jobs: '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 17v3h16v-3"/>',
    export: '<path d="M12 15V3"/><path d="M7 8l5-5 5 5"/><path d="M4 14v6h16v-6"/>',
    samples: '<path d="M4 20l5-12 6 4 5-8"/><circle cx="9" cy="8" r="1.6"/><circle cx="15" cy="12" r="1.6"/><path d="M14 20h7M17.5 16.5v7" opacity=".7"/>',
    stack: '<path d="M12 3l9 4.5-9 4.5-9-4.5z"/><path d="M3 12l9 4.5 9-4.5"/><path d="M3 16.5l9 4.5 9-4.5"/>',
    table: '<path d="M4 6l4-3 4 3 4-3 4 3v6"/><path d="M4 6v6"/><rect x="3" y="14" width="18" height="7" rx="1"/><path d="M3 17.5h18M9 14v7M15 14v7"/>',
    ml: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 4v16"/>',
    pca: '<circle cx="7" cy="16" r="1.4"/><circle cx="11" cy="12" r="1.4"/><circle cx="15" cy="10" r="1.4"/><circle cx="9" cy="17" r="1.4"/><circle cx="17" cy="7" r="1.4"/><path d="M3 21L21 3"/><path d="M8 6l10 10" opacity=".5"/>',
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    raster: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    vector: '<path d="M4 18l5-12 7 4 4 8z"/>',
    rasterml: '<rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1" opacity=".55"/><rect x="3" y="13" width="8" height="8" rx="1" opacity=".55"/><rect x="13" y="13" width="8" height="8" rx="1"/><path d="M5.5 7h3M15.5 17h3"/>',
    cluster: '<circle cx="7" cy="8" r="1.6"/><circle cx="10" cy="6" r="1.6"/><circle cx="9" cy="10.5" r="1.6"/><circle cx="16" cy="15" r="1.6"/><circle cx="18.5" cy="12.5" r="1.6"/><circle cx="15" cy="18" r="1.6"/><circle cx="18" cy="18.5" r="1.6"/><path d="M4.5 4.5a6 6 0 0 1 8 7.5M12 19a6 6 0 0 0 9-8" opacity=".55"/>',
    tsne: '<circle cx="6" cy="7" r="1.5"/><circle cx="8" cy="9.5" r="1.5"/><circle cx="5" cy="11" r="1.5"/><circle cx="16" cy="6" r="1.5"/><circle cx="18" cy="8.5" r="1.5"/><circle cx="12" cy="17" r="1.5"/><circle cx="14.5" cy="18.5" r="1.5"/><circle cx="11" cy="20" r="1.5"/><path d="M3 3v18h18" opacity=".55"/>',
    dl: '<circle cx="5" cy="7" r="1.8"/><circle cx="5" cy="17" r="1.8"/><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/><circle cx="19" cy="12" r="1.8"/><path d="M6.7 7.5l3.6 4M6.7 16.5l3.6-4M6.7 7l3.5-1.5M6.7 17l3.5 1.5M13.8 5.8l3.6 5.3M13.8 18.2l3.6-5.3M13.8 12H17.2" opacity=".6"/>',
    dlmap: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 14l5-4 4 3 4-5 5 4" opacity=".6"/><circle cx="16.5" cy="16.5" r="3.2" fill="currentColor" stroke="none" opacity=".8"/>',
    traindet: '<rect x="3" y="3" width="18" height="18" rx="2" opacity=".45"/><rect x="6" y="6" width="7" height="6" rx=".5"/><path d="M14 19l2.5-2.5L19 19M16.5 16.5V21" opacity=".9"/><path d="M6 15h5M6 17.5h3" opacity=".6"/>',
    detect: '<rect x="3" y="3" width="18" height="18" rx="2" opacity=".45"/><rect x="6" y="7" width="7" height="6" rx=".5"/><rect x="12" y="13" width="6" height="5" rx=".5" fill="currentColor" fill-opacity=".35"/><path d="M6 5.5h3"/>',
    patches: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18" opacity=".55"/><rect x="9" y="9" width="6" height="6" fill="currentColor" stroke="none" opacity=".8"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>',
    embed: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18" opacity=".35"/><circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none"/><path d="M12 9.8V6M14.2 12H18M12 14.2V18M9.8 12H6" opacity=".7"/>',
    convert: '<path d="M4 8h13l-3-3M20 16H7l3 3"/><text x="3.5" y="14.2" font-size="5.5" font-family="sans-serif" fill="currentColor" stroke="none">8</text><text x="14" y="12.6" font-size="5.5" font-family="sans-serif" fill="currentColor" stroke="none">32</text>',
    similar: '<rect x="3" y="3" width="12" height="12" rx="1.5" opacity=".45"/><path d="M3 7h12M3 11h12M7 3v12M11 3v12" opacity=".3"/><circle cx="15.5" cy="15.5" r="4"/><path d="M18.5 18.5L21 21"/>',
    leaf: '<path d="M5 19C5 10 10 5 20 4c-1 10-6 15-15 15z"/><path d="M5 19l8-8" opacity=".7"/><circle cx="14" cy="9.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="10.5" cy="13.5" r="1" fill="currentColor" stroke="none"/>',
    book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/><path d="M12 7.5c-2 .5-3 2-3 4 2 0 3.5-1.5 3-4zM12 7.5c1.5 1 2 2.5 1.5 4.5" opacity=".75"/>',
    // ribbon commands (File, View, Help, History, Bookmarks)
    fnew: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v6M9 14h6"/>',
    fopen: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v1"/><path d="M3 7v11a2 2 0 0 0 2 2h12.5l3.5-8H7.5L5 18"/>',
    fclose: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M10 11l4 4M14 11l-4 4"/>',
    reveal: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    adddata: '<path d="M12 3l9 4.5-9 4.5-9-4.5z" opacity=".55"/><path d="M3 12l9 4.5 3-1.5"/><path d="M18.5 14v7M15 17.5h7"/>',
    safe: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5" opacity=".7"/><circle cx="17" cy="17" r="2.2" fill="currentColor" stroke="none"/>',
    props: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    trash: '<path d="M4 7h16M10 7V4h4v3M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6" opacity=".7"/>',
    trashall: '<path d="M3 7h14M8 7V4h4v3M5 7l1 13h8l1-13"/><path d="M19 10v10M21 12v6" opacity=".6"/>',
    clean: '<path d="M14 3l-4 9"/><path d="M6 12h10l2 9H4z"/><path d="M8 16v5M12 16v5" opacity=".6"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M15 8l2 2"/>',
    layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16" opacity=".6"/><path d="M5.5 12h1.5M17 12h1.5"/>',
    streets: '<path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14" opacity=".55"/>',
    topo: '<path d="M3 19l6-10 4 6 3-4 5 8z"/><path d="M6 15c2-1 4 1 6 0s3-1 5 0" opacity=".55"/>',
    nomap: '<rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="3 2.5"/><path d="M8 8l8 8M16 8l-8 8" opacity=".6"/>',
    zoomall: '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
    guide: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 21a2 2 0 0 1 2-2h13v2z"/><path d="M9 8h6M9 11.5h4" opacity=".7"/>',
    keys: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    errlog: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4"/><circle cx="12" cy="17" r=".9" fill="currentColor" stroke="none"/>',
    bookmark: '<path d="M6 3h12v18l-6-4.5L6 21z"/><path d="M12 7v6M9 10h6" opacity=".75"/>',
    history: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v4h4"/><path d="M12 7v5l3 2"/>',
    sar: '<path d="M4 20l5-9" /><circle cx="9.5" cy="10" r="1.6"/><path d="M12.5 7a5 5 0 0 1 4 4M14 4a8.5 8.5 0 0 1 6 6" /><path d="M3 20h18" opacity=".5"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.6" r="1.1" fill="currentColor" stroke="none"/>',
    save: '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3"/><rect x="7" y="13" width="10" height="6" rx="1"/>',
    saveas: '<path d="M13 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l3 3v5"/><path d="M7 3v5h8V3"/><path d="M7 21v-6h5"/><path d="M15.5 21l.6-2.6 4.6-4.6a1.4 1.4 0 0 1 2 2l-4.6 4.6z"/>',
    mdist: '<path d="M3 17L17 3l4 4L7 21z"/><path d="M7 13l2 2M10 10l2 2M13 7l2 2" opacity=".7"/>',
    marea: '<path d="M4 6l7-3 9 5-3 12-11-2z" stroke-dasharray="3 2"/><circle cx="4" cy="6" r="1.5" fill="currentColor"/><circle cx="20" cy="8" r="1.5" fill="currentColor"/><circle cx="17" cy="20" r="1.5" fill="currentColor"/>',
    mheight: '<path d="M2 20l6-9 4 5 3-4 7 8z"/><path d="M12 3v9" /><path d="M9.5 5.5L12 3l2.5 2.5"/>',
    tour: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
    mprofile: '<path d="M3 20h18" opacity=".5"/><path d="M3 16l4-6 4 3 4-8 6 9"/><circle cx="15" cy="5" r="1.4" fill="currentColor" stroke="none"/>',
    print: '<path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/><circle cx="17.5" cy="12" r=".9" fill="currentColor" stroke="none"/>',
    swipe: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 2v20"/><path d="M8 10l-2 2 2 2M16 10l2 2-2 2" opacity=".7"/>',
    sidebyside: '<rect x="2" y="5" width="9" height="14" rx="1.5"/><path d="M17 5l5 3v8l-5 3-5-3V8z"/><path d="M12 8l5 3 5-3M17 11v8" opacity=".55"/>',
    assistant: '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M18.5 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" opacity=".7"/><path d="M5 16.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z" opacity=".55"/>',
    vbuffer: '<path d="M8 9l5-2 3 5-4 4-4-2z"/><path d="M4.5 9.5l7.5-5 7 6.5-6 8.5-7.5-3.5z" stroke-dasharray="2.5 2" opacity=".7"/>',
    vquery: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 4v16" opacity=".5"/><path d="M12.5 13.5h6M12.5 16.5h4"/>',
    voverlay: '<circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/><path d="M12 7.2a6 6 0 0 1 0 9.6a6 6 0 0 1 0-9.6z" fill="currentColor" fill-opacity=".35" stroke="none"/>',
    vdissolve: '<path d="M4 6h7v6H4zM11 6h8v6h-8zM4 12h15v6H4z" stroke-dasharray="2 2" opacity=".55"/><path d="M4 6h15v12H4z"/>',
    vzonal: '<rect x="3" y="3" width="18" height="18" rx="2" opacity=".45"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18" opacity=".3"/><path d="M6 6l9 2 3 9-10 1z" stroke-width="2"/>',
    vlocation: '<path d="M12 21s-6-5.5-6-10a6 6 0 0 1 12 0c0 4.5-6 10-6 10z"/><circle cx="12" cy="11" r="2"/><circle cx="12" cy="11" r="9" stroke-dasharray="2 2.5" opacity=".5"/>',
    vsjoin: '<rect x="3" y="5" width="8" height="8" rx="1"/><rect x="13" y="11" width="8" height="8" rx="1"/><path d="M11 9h3a2 2 0 0 1 2 2v0" /><path d="M14.5 9.5L16 11l1.5-1.5"/>',
    vgeometry: '<path d="M4 18L9 5l11 4-5 11z"/><path d="M3 21h4M5 19v4" opacity=".6"/><text x="10.5" y="14.5" font-size="6" font-family="sans-serif" fill="currentColor" stroke="none">ha</text>',
    vcount: '<path d="M4 6l7-2 9 4-2 11-12-1z"/><circle cx="9" cy="9" r="1.2" fill="currentColor"/><circle cx="14" cy="12" r="1.2" fill="currentColor"/><circle cx="10" cy="15" r="1.2" fill="currentColor"/>',
    vtjoin: '<rect x="3" y="4" width="8" height="16" rx="1"/><path d="M3 9h8M3 14h8" opacity=".5"/><path d="M14 8l6 2-1 8-5-1z"/><path d="M11 12h3"/>',
    rterrain: '<path d="M2 19l6-10 4 6 3-4 7 8z"/><path d="M8 9l1.5 4M15 11l-1 3" opacity=".55"/><circle cx="18" cy="5" r="1.6"/>',
    rcontours: '<path d="M4 17c3-3 6-2 8-4s4-5 8-5"/><path d="M4 13c2-2 5-2 6-4s3-4 7-4" opacity=".6"/><path d="M5 21c3-2 7-1 10-3s4-3 6-3" opacity=".6"/>',
    rreclass: '<rect x="3" y="3" width="8" height="18" rx="1"/><path d="M3 9h8M3 15h8" opacity=".5"/><path d="M14 6h7M14 12h7M14 18h7"/><path d="M11 12h3" stroke-dasharray="1.5 1.5"/>',
    rchange: '<rect x="2" y="5" width="9" height="14" rx="1.5"/><rect x="13" y="5" width="9" height="14" rx="1.5" opacity=".6"/><path d="M8 12h8M14 10l2 2-2 2"/>',
    rclip: '<rect x="3" y="3" width="18" height="18" rx="2" opacity=".45"/><path d="M7 8l5-3 6 4-1 8-8 2z"/><path d="M3 3l4 5M21 3l-3 6" opacity=".4"/>',
    rresample: '<rect x="3" y="3" width="10" height="10" rx="1"/><path d="M3 8h10M8 3v10" opacity=".55"/><rect x="11" y="11" width="10" height="10" rx="1"/><path d="M11 14.3h10M11 17.6h10M14.3 11v10M17.6 11v10" opacity=".45"/>',
    renhance: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 21L21 3" opacity=".4"/><path d="M5 19L19 5V19z" fill="currentColor" fill-opacity=".35" stroke="none"/><path d="M7 7l1 2 2 1-2 1-1 2-1-2-2-1 2-1z" fill="currentColor" stroke="none"/>',
    r2poly: '<rect x="2" y="3" width="9" height="9" rx="1"/><path d="M2 7.5h9M6.5 3v9" opacity=".45"/><path d="M14 13l4-2 4 4-2 6-6-1z"/><path d="M11 8h4l-1.5-1.5M15 8l-1.5 1.5" opacity=".7"/>',
    r2line: '<rect x="2" y="3" width="9" height="9" rx="1"/><path d="M2 7.5h9M6.5 3v9" opacity=".45"/><path d="M13 21c2-4 3-5 5-5s2-3 4-5"/><path d="M11 8h4l-1.5-1.5M15 8l-1.5 1.5" opacity=".7"/>',
    r2point: '<rect x="2" y="3" width="9" height="9" rx="1"/><path d="M2 7.5h9M6.5 3v9" opacity=".45"/><circle cx="15" cy="15" r="1.3" fill="currentColor"/><circle cx="20" cy="15" r="1.3" fill="currentColor"/><circle cx="15" cy="20" r="1.3" fill="currentColor"/><circle cx="20" cy="20" r="1.3" fill="currentColor"/><path d="M11 8h4l-1.5-1.5M15 8l-1.5 1.5" opacity=".7"/>',
    rasterize: '<path d="M3 9l3-5 5 2-1 5z"/><path d="M10 8h4l-1.5-1.5M14 8l-1.5 1.5" opacity=".7"/><rect x="13" y="12" width="9" height="9" rx="1"/><path d="M13 16.5h9M17.5 12v9" opacity=".45"/><rect x="13" y="12" width="4.5" height="4.5" fill="currentColor" fill-opacity=".4" stroke="none"/>',
    vconvert: '<path d="M3 4l5-1 2 5-4 3-3-2z"/><path d="M14 17c2-3 4-1 7-4" /><path d="M8 14l-2 4h5M15 7h5l-2-2M20 7l-2 2" opacity=".7"/>',
    areastats: '<rect x="3" y="3" width="18" height="18" rx="2" opacity=".45"/><path d="M7 17V12M11 17V8M15 17V10M19 17V6" stroke-width="2.2"/>',
    accuracy: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18" opacity=".45"/><rect x="3" y="3" width="6" height="6" fill="currentColor" fill-opacity=".35" stroke="none"/><path d="M12.5 15l2.2 2.2 4.3-4.7"/>',
    rcalc: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8" /><path d="M8 12h2M8 16h2M14 12h2M14 16h2" stroke-width="2.2"/>',
    timeseries: '<path d="M3 20h18" opacity=".5"/><path d="M4 15c2-6 4-8 6-4s3 5 5 0 3-6 5-4"/><circle cx="10" cy="11" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="11" r="1.2" fill="currentColor" stroke="none"/>',
    georef: '<rect x="3" y="4" width="11" height="9" rx="1"/><path d="M5 11l3-3 2 2 2-2" opacity=".6"/><path d="M17 12a3 3 0 0 1 3 3c0 2.5-3 5.5-3 5.5s-3-3-3-5.5a3 3 0 0 1 3-3z"/><path d="M14 8.5l3 3.5" stroke-dasharray="1.6 1.6"/>',
    vhelpers: '<circle cx="7" cy="7" r="2.2"/><path d="M14 4l6 5-3 7-6-2z"/><path d="M4 14h6v6H4z" stroke-dasharray="2 1.5"/><circle cx="17" cy="19" r="1" fill="currentColor"/><circle cx="20" cy="16" r="1" fill="currentColor"/>',
    workflow: '<rect x="3" y="3" width="6" height="5" rx="1"/><rect x="15" y="9.5" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><path d="M9 5.5h3v13H9M12 12h3"/>',
    map2d: '<path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14" opacity=".55"/>',
    map3d: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9" opacity=".6"/>',
    rename: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4" opacity=".6"/>',
    duplicate: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/><path d="M12 14h4" opacity=".6"/>',
    paste: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1"/><path d="M9 11h6M9 15h4" opacity=".6"/>',
  };
  const svg = (name, w = 1.8) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
  const TOOLS = [
    { id: "search", title: "Find imagery", icon: "search", subtitle: "Search, preview and download Sentinel-2, composites and land-cover labels" },
    { id: "analyze", title: "Index analysis", icon: "analyze", subtitle: "NDVI, SAVI, EVI, NDWI and 21 more indices or your own formula" },
    { id: "pca", title: "PCA & dimensionality reduction", icon: "pca", subtitle: "PCA, Kernel PCA, NMF, ICA and more (scikit-learn) on any multiband image" },
    { id: "samples", title: "Training samples", icon: "samples", subtitle: "Draw labelled polygons and points for each class on the map" },
    { id: "stack", title: "Stack layers", icon: "stack", subtitle: "Combine bands from several layers (S2, S1, DEM, indices…) onto one grid" },
    { id: "raster2table", title: "Raster → table", icon: "table", subtitle: "Turn any image (multispectral, hyperspectral, SAR) into a table, with optional ground-truth labels" },
    { id: "ml", title: "Classical ML (tabular data)", icon: "ml", subtitle: "Machine-learning tools that work on tables" },
    { id: "rasterml", title: "Classical ML for raster", icon: "rasterml", subtitle: "Train SVM, Maximum Likelihood, Random Forest, SAM and more straight from an image and ground truth, and map it: RGB, multispectral, hyperspectral or embeddings" },
    { id: "dltrain", title: "Train classify model", icon: "dl", subtitle: "Train U-Net, DeepLabV3+, PSPNet, FCN, SegFormer and more (MobileNetV2/V3, ResNet, EfficientNet backbones) on your Make-training-data patches, with early stopping and an HTML report" },
    { id: "dlpredict", title: "Classify image", icon: "dlmap", subtitle: "Map a whole image with a model from Train classify model (deep learning, tiled, seamless), with a confidence layer" },
    { id: "detect", title: "Detect object", icon: "detect", subtitle: "Find vehicles, ships, planes, people, storage tanks and more in high-resolution images: YOLO26 (incl. aerial DOTA model), Faster R-CNN, RetinaNet, Mask R-CNN, SAM 2.1 segment-everything, or your own trained models. Boxes or outlines as a vector layer" },
    { id: "traindet", title: "Train detection model", icon: "traindet", subtitle: "Train YOLO26 / YOLO11 to find your own objects (boxes, outlines or rotated boxes) from an image and labelled polygons or points, with early stopping, live curves and an HTML report" },
    { id: "patches", title: "Make training data", icon: "patches", subtitle: "Cut large images and their ground truth into image / label patches for deep-learning training" },
    { id: "export", title: "Export data", icon: "export", subtitle: "Save any layer to your computer: GeoTIFF, PNG, Shapefile, GeoPackage (several layers in one file), GeoJSON, KML" },
    { id: "jobs", title: "Downloads & jobs", icon: "jobs", subtitle: "Background downloads, logs and output files" },
  ];
  LF.tools.forEach(({ id, menu, title, icon, subtitle }) => TOOLS.push({ id, menu, title, icon, subtitle }));
  // menus of their own (besides Tools): their tools have menu: "<key>"; shortcuts list tools of other menus there too
  const MENUS = {
    agri: { el: "#agri-menu", label: "Also useful for crops", shortcuts: ["analyze", "embed", "search"],
            subtitles: { analyze: "Crop health and vigour from satellite images: NDVI, EVI, SAVI, NDRE, NDWI and more" } },
    embed: { el: "#embed-menu", label: "Use embeddings with", shortcuts: ["rasterml", "ml", "pca"],
             subtitles: { rasterml: "Map crops or land cover from an embedding layer and a few labelled points (k-NN, SVM, SAM…)",
                          ml: "Cluster an embedding (Raster → table first) without labels, or train on tables",
                          pca: "Reduce the 64 / 128 dimensions to a few components" } },
    forecast: { el: "#forecast-menu", label: "Also useful for forecasts", shortcuts: ["interp", "ml"],
                subtitles: { interp: "Make a map from a forecast at stations (Put on the map, then a surface: kriging, IDW…)",
                             ml: "Predict a value from other columns without time (regression / classification on tables)" } },
    sar: { el: "#sar-menu", label: "Also useful for SAR", shortcuts: ["rclip", "rmosaic", "rchange", "rasterml"],
           subtitles: { rchange: "Before / after difference of two SAR dates (the SAR tools do it in dB with flood classes)",
                        rasterml: "Classify crops or land cover from SAR features (VV, VH, ratio, RVI, texture) and labelled points" } },
    library: { el: "#library-menu", label: "", shortcuts: [] },
    online: { el: "#online-menu", label: "", shortcuts: [] },
  };
  // the Analysis menu: a category on the left (Tools, Agri, Embeddings, Forecast), its tools on the right
  function showAnalysis(cat) {
    $$("#analysis-menu [data-an]").forEach((b) => { const on = b.dataset.an === cat; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
    $$("#analysis-menu [data-an-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.anPanel !== cat));
    prefs.set("analysis-cat", cat);
  }
  $$("#menus [data-ic]").forEach((s) => { s.innerHTML = svg(s.dataset.ic); });
  $$("#analysis-menu [data-an]").forEach((b) => {
    b.onmouseenter = () => showAnalysis(b.dataset.an);
    b.onfocus = () => showAnalysis(b.dataset.an);
    b.onclick = (e) => { e.stopPropagation(); showAnalysis(b.dataset.an); $("[data-tool]", $(`#analysis-menu [data-an-panel="${b.dataset.an}"]`))?.focus(); };
  });
  showAnalysis(prefs.get("analysis-cat", "tools"));
    let currentTool = "home";

  // Tools are listed A–Z; each explanation is behind an ⓘ button (click it to show / hide, or hover for a tooltip)
  const byTitle = (a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
  // the ⓘ that shows / hides a tool's explanation (the same circled i as every other hint)
  const INFO_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.6" r="1.1" fill="currentColor" stroke="none"/></svg>';
  function toolEntry(cls, attrs, t, withIcon = true) {
    return `<div role="button" tabindex="0" class="${cls}" ${attrs}>${withIcon ? `<span class="ic">${svg(t.icon)}</span>` : ""}
      <span class="te-text"><b>${esc(t.title)}</b><small class="te-desc">${esc(t.subtitle || "")}</small></span>
      ${t.subtitle ? `<button type="button" class="eye" title="${esc(t.subtitle)}" aria-label="What does ${esc(t.title)} do?" aria-expanded="false">${INFO_SVG}</button>` : ""}</div>`;
  }
  function wireEntries(root, selector, onOpen) {
    $$(selector, root).forEach((el) => {
      el.onclick = (e) => { if (!e.target.closest(".eye")) onOpen(el); };
      el.onkeydown = (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === el) { e.preventDefault(); onOpen(el); } };
      const eye = $(".eye", el);
      if (eye) eye.onclick = (e) => { e.stopPropagation(); const on = el.classList.toggle("show-desc"); eye.setAttribute("aria-expanded", on); };
    });
  }
  // the ribbon groups of the Tools category (tools not named here go in "More")
  const TOOL_GROUPS = [["Imagery", ["search", "analyze", "timeseries", "pca", "stack", "rmosaic", "rburn"]], ["Training data", ["samples", "raster2table", "patches"]],
    ["Classical ML", ["ml", "rasterml", "interp"]], ["Deep learning", ["dltrain", "dlpredict", "traindet", "detect"]], ["Vector", ["vbuffer", "vquery", "voverlay", "vdissolve", "vhelpers"]], ["Spatial analysis", ["vzonal", "vlocation", "vsjoin", "vcount", "vgeometry", "vtjoin", "vstats"]], ["Fuzzy & suitability", ["fmember", "foverlay", "fboundary", "fcmeans"]], ["Raster & terrain", ["rterrain", "rcontours", "rreclass", "rchange", "rclip", "rresample", "renhance", "rcalc"]], ["Assess", ["areastats", "accuracy"]], ["Conversion", ["r2poly", "r2line", "r2point", "rasterize", "vconvert", "georef"]], ["Output", ["export", "jobs"]], ["Automate", ["assistant", "workflows"]]];
  // one ribbon group: a few tools as big buttons, more as small ones in columns of three
  function ribbonGroup(caption, tools, big = tools.length <= 3) {
    if (!tools.length) return "";
    const cls = big ? "tool-item rb-tool big" : "tool-item rb-tool";
    return `<div class="rb-group"><div class="rb-items${big ? "" : " rb-tri"}">${tools.map((t) =>
      toolEntry(cls, `data-tool="${t.id}" title="${esc(t.title)}: ${esc(t.subtitle || "")}"`, t)).join("")}</div><div class="rb-cap">${esc(caption)}</div></div>`;
  }
  function buildToolsMenu() {
    const tools = [...TOOLS].sort(byTitle);   // Classical ML's own tools are tabs inside it (see renderMlHub)
    const plain = TOOLS.filter((t) => !t.menu), grouped = TOOL_GROUPS.flatMap(([, ids]) => ids);
    $("#tools-menu").innerHTML = TOOL_GROUPS.map(([cap, ids]) => ribbonGroup(cap, ids.map((id) => plain.find((t) => t.id === id)).filter(Boolean), false)).join("") +
      ribbonGroup("More", plain.filter((t) => !grouped.includes(t.id)).sort(byTitle), false);
    wireEntries($("#tools-menu"), "[data-tool]", (b) => { switchTool(b.dataset.tool); toggleMenu(null); });
    Object.entries(MENUS).forEach(([key, m]) => {
      const el = $(m.el);
      el.innerHTML = ribbonGroup({ agri: "Agri", embed: "Embeddings", forecast: "Forecast", sar: "SAR", library: "Library", online: "Online & field" }[key] || key, TOOLS.filter((t) => t.menu === key)) +
        ribbonGroup(m.label, m.shortcuts.map((id) => TOOLS.find((t) => t.id === id)).filter(Boolean)
          .map((t) => m.subtitles?.[t.id] ? { ...t, subtitle: m.subtitles[t.id] } : t));
      wireEntries(el, "[data-tool]", (b) => { switchTool(b.dataset.tool); toggleMenu(null); });
    });
    $("#tool-cards").innerHTML = tools.map((t) => toolEntry("tool-card", `data-tool="${t.id}"`, t)).join("");
    wireEntries($("#tool-cards"), "[data-tool]", (b) => switchTool(b.dataset.tool));
  }
  // the open tool's explanation, under its title, also behind an ⓘ button
  $("#tool-eye").innerHTML = INFO_SVG;
  const syncToolSub = () => {
    const on = prefs.get("tool-sub", false);
    $("#tool-sub").classList.toggle("hidden", !on);
    $("#tool-eye").setAttribute("aria-expanded", on);
    $("#tool-eye").classList.toggle("on", on);
  };
  $("#tool-eye").onclick = () => { prefs.set("tool-sub", !prefs.get("tool-sub", false)); syncToolSub(); };
  syncToolSub();

  function switchTool(id, arg) {
    const tool = TOOLS.find((t) => t.id === id) || { id: "home", title: "Start", subtitle: "Choose a tool" };
    currentTool = tool.id;
    $$(".tabpanel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + tool.id));
    $("#tool-title").textContent = tool.title;
    $("#tool-sub").textContent = tool.subtitle;
    $("#tool-eye").title = tool.subtitle;
    $("#tool-eye").classList.toggle("hidden", tool.id === "home");
    $("#active-tool").innerHTML = tool.id === "home" ? "" : `Tool: <b>${esc(tool.title)}</b>`;
    $$(["#tools-menu", ...Object.values(MENUS).map((m) => m.el)].map((e) => `${e} [data-tool]`).join(", ")).forEach((b) => b.classList.toggle("on", b.dataset.tool === tool.id));
    document.title = tool.id === "home" ? "LULC Fetch" : `${tool.title} · LULC Fetch`;
    setPane("tools", true);
    renderRunBar();
    if (tool.id === "analyze") refreshAnalyzeInputs();
    if (tool.id === "jobs") refreshJobs();
    if (tool.id === "home") refreshHomeProducts();
    if (tool.id === "export") { refreshExportLayers(); renderExportForm(); }
    if (tool.id === "pca" && pcaState.schema) refreshPcaInputs();
    if (tool.id === "ml") { openMlSub(mlSub); }
    if (tool.id === "raster2table") refreshRtInputs();
    if (tool.id === "rasterml") refreshRm();
    if (tool.id === "patches") refreshPt();
    if (tool.id === "dltrain") refreshDt();
    if (tool.id === "dlpredict") refreshDp();
    if (tool.id === "detect") refreshOd();
    if (tool.id === "traindet") refreshTd();
    PLUGINS[tool.id]?.hooks?.open?.(arg);
    if (tool.id === "samples") renderSamples();
    if (tool.id === "stack") refreshStack();
    prefs.set("tool", tool.id);
  }
