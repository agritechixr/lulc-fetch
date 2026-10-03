"""Embeddings menu (lulc_fetch/embeddings): sources, size estimate, AlphaEarth / TESSERA decoding, colour view, similar places.

The download tests read real AlphaEarth and TESSERA data for a small area of Bengaluru (marker network)."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import webapp.workspace as ws
from lulc_fetch.embeddings import alphaearth, tessera
from tests.helpers import ok, run

BLR = {"type": "Polygon", "coordinates": [[[77.585, 12.965], [77.595, 12.965], [77.595, 12.975], [77.585, 12.975], [77.585, 12.965]]]}


@pytest.fixture(scope="module")
def emb_tif(client):
    """A 64-band embedding image: left half one kind of place, right half another (unit vectors)."""
    p = ws.root() / "uploads" / "testdata" / "embedding64.tif"
    p.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=64), rng.normal(size=64)
    data = np.empty((64, 40, 60), np.float32)
    data[:, :, :30] = a[:, None, None] + rng.normal(0, 0.05, (64, 40, 30))
    data[:, :, 30:] = b[:, None, None] + rng.normal(0, 0.05, (64, 40, 30))
    data /= np.linalg.norm(data, axis=0, keepdims=True)
    with rasterio.open(p, "w", driver="GTiff", width=60, height=40, count=64, dtype="float32", crs="EPSG:32643",
                       transform=from_origin(770000, 1435000, 10, 10)) as dst:
        dst.write(data)
        for i in range(64):
            dst.set_band_description(i + 1, f"A{i:02d}")
    return ws.rel(p)


def lonlat(col, row):
    from rasterio.warp import transform
    x, y = 770000 + 10 * col + 5, 1435000 - 10 * row - 5
    lon, lat = transform("EPSG:32643", "EPSG:4326", [x], [y])
    return [lon[0], lat[0]]


def test_sources(client):
    s = ok(client.get("/api/emb/sources"))
    assert s["sources"]["aef"]["dims"] == 64 and s["sources"]["tessera"]["dims"] == 128
    assert s["sources"]["aef"]["licence"] == "CC-BY-4.0" and s["sources"]["tessera"]["licence"] == "CC0"
    assert s["years"][0] == 2017 and 2025 in s["years"]


def test_estimate(client):
    e = ok(client.post("/api/emb/estimate", json={"clip": BLR, "source": "aef", "res": 10}))
    assert e["crs"] == "EPSG:32643" and 100 < e["width"] < 120 and 100 < e["height"] < 120 and 1 < e["area_km2"] < 1.3
    assert abs(e["output_mb"] - e["width"] * e["height"] * 64 * 4 / 1e6) < 0.2
    e40 = ok(client.post("/api/emb/estimate", json={"clip": BLR, "source": "aef", "res": 40}))
    assert e40["width"] < e["width"] / 3
    assert client.post("/api/emb/fetch", json={"clip": BLR, "source": "tessera", "res": 40}).status_code == 400   # TESSERA: 10 m only
    huge = {"type": "Polygon", "coordinates": [[[70, 10], [75, 10], [75, 15], [70, 15], [70, 10]]]}
    assert client.post("/api/emb/fetch", json={"clip": huge, "source": "aef"}).status_code == 400
    assert client.post("/api/emb/fetch", json={"source": "aef"}).status_code == 422


def test_decoding():
    q = np.array([[[127]], [[-127]], [[0]], [[-128]]], np.int8)
    v = alphaearth.dequantize(q[:3])
    assert abs(v[0, 0, 0] - (127 / 127.5) ** 2) < 1e-6 and abs(v[1, 0, 0] + (127 / 127.5) ** 2) < 1e-6 and v[2, 0, 0] == 0
    nod = alphaearth.dequantize(np.full((64, 1, 1), -128, np.int8))
    assert np.isnan(nod).all()
    assert tessera.tiles(BLR) == [(77.55, 12.95)]
    assert tessera.url(77.55, 12.95, 2024).endswith("/npy/v1/2024/grid_77.55_12.95/grid_77.55_12.95.npy")
    two = {"type": "Polygon", "coordinates": [[[77.58, 12.95], [77.62, 12.95], [77.62, 12.99], [77.58, 12.99], [77.58, 12.95]]]}
    assert tessera.tiles(two) == [(77.55, 12.95), (77.65, 12.95)]   # crosses 77.6° east, not a latitude line
    four = {"type": "Polygon", "coordinates": [[[77.58, 12.95], [77.62, 12.95], [77.62, 13.02], [77.58, 13.02], [77.58, 12.95]]]}
    assert len(tessera.tiles(four)) == 4


def test_colour_view(client, emb_tif):
    r = run(client, "/api/emb/colour", {"path": emb_tif})
    with rasterio.open(ws.root() / r["path"]) as s:
        assert s.count == 3 and s.dtypes[0] == "uint8"
        a = s.read()
    assert (a[:, :, :30].mean(axis=(1, 2)) != a[:, :, 30:].mean(axis=(1, 2))).any()   # the two kinds of place get different colours
    assert r["explained"][0] > 0.5


def test_similar_places(client, emb_tif):
    r = run(client, "/api/emb/similar", {"path": emb_tif, "points": [lonlat(5, 10)], "name": "sim"})
    with rasterio.open(ws.root() / r["path"]) as s:
        sim = s.read(1)
    assert sim[10, 5] > 0.99 and sim[:, :30].mean() > 0.95 and sim[:, 30:].mean() < 0.6
    assert r["points"] == 1
    assert client.post("/api/emb/similar", json={"path": emb_tif, "points": []}).status_code == 400
    bad = run_err(client, {"path": emb_tif, "points": [[0.0, 0.0]]})
    assert "None of the points" in bad


def run_err(client, body):
    from tests.helpers import wait
    job = ok(client.post("/api/emb/similar", json=body))
    import time
    for _ in range(200):
        j = ok(client.get(f"/api/jobs/{job['id']}"))
        if j["status"] in ("done", "error"):
            return j.get("error") or ""
        time.sleep(0.1)
    return ""


@pytest.mark.network
def test_available(client):
    r = run(client, "/api/emb/available", {"clip": BLR})
    assert all(r["aef"][str(y)] >= 1 for y in range(2017, 2026))
    assert r["tessera"]["2024"] == 1 and r["tessera_tiles"] == 1


@pytest.mark.network
@pytest.mark.parametrize("source,dims", [("aef", 64), ("tessera", 128)])
def test_download(client, source, dims, report):
    r = run(client, "/api/emb/fetch", {"clip": BLR, "source": source, "year": 2024, "res": 10, "name": f"{source}_test", "colour": True})
    with rasterio.open(ws.root() / r["path"]) as s:
        assert s.count == dims and s.crs.to_epsg() == 32643 and s.transform.e < 0   # north-up, whatever the source's storage
        a = s.read()
        assert s.descriptions[0] in ("A00", "E000")
    assert r["valid_pct"] > 95 and np.isfinite(a).mean() > 0.6
    if source == "aef":   # unit vectors
        n = np.linalg.norm(a.reshape(dims, -1)[:, np.isfinite(a[0]).ravel()], axis=0)
        assert abs(np.median(n) - 1) < 0.02
    assert r["colour"]["path"].endswith("_colour.tif")
    report.metric(f"{source} seconds (1 km²)", r["seconds"])


# ------------------------------------------------------------------ Convert embeddings (8-bit ↔ float)

@pytest.fixture(scope="module")
def raw_aef(client):
    """A small AlphaEarth tile as Google stores it: int8 codes, −128 = no data, bottom-up (positive pixel height)."""
    from rasterio.transform import Affine
    p = ws.root() / "uploads" / "testdata" / "raw_aef.tif"
    p.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    v = rng.normal(size=(64, 30, 20))
    v /= np.linalg.norm(v, axis=0, keepdims=True)                           # unit vectors, like AlphaEarth
    q = np.clip(np.rint(np.sign(v) * np.sqrt(np.abs(v)) * 127.5), -127, 127).astype(np.int8)
    q[:, 0, 0] = -128                                                        # one pixel without data
    with rasterio.open(p, "w", driver="GTiff", width=20, height=30, count=64, dtype="int8", crs="EPSG:32643", nodata=-128,
                       transform=Affine(10, 0, 770000, 0, 10, 1434000)) as dst:   # origin at the south edge, rows go north
        dst.write(q)
        for i in range(64):
            dst.set_band_description(i + 1, f"A{i:02d}")
    return ws.rel(p), q


def test_convert_detect(client, raw_aef, emb_tif):
    path, _ = raw_aef
    f = ok(client.get("/api/emb/format", params={"path": path}))
    assert f["format"] == "int8-aef" and f["north_up"] is False and f["bands"] == 64 and "int8-aef" not in f["targets"]
    assert ok(client.get("/api/emb/format", params={"path": emb_tif}))["format"] == "float32"
    assert client.post("/api/emb/convert", json={"path": emb_tif, "to": "float32"}).status_code == 400   # already float32
    assert client.post("/api/emb/convert", json={"path": emb_tif, "to": "int4"}).status_code == 422


def test_convert_round_trip(client, raw_aef):
    from lulc_fetch.embeddings import formats as cv
    path, q = raw_aef
    r = run(client, "/api/emb/convert", {"path": path, "to": "float32", "name": "aef_f32"})
    assert r["from"] == "int8-aef" and r["flipped"] and r["max_error"] == 0
    with rasterio.open(ws.root() / r["path"]) as s:
        f, tf = s.read(), s.transform
        assert s.dtypes[0] == "float32" and tf.e < 0 and abs(tf.f - 1434300) < 1e-6   # north-up, same place
    want = cv.decode(q, "int8-aef")[:, ::-1, :]
    assert np.allclose(f, want, equal_nan=True) and np.isnan(f[:, -1, 0]).all()
    back = run(client, "/api/emb/convert", {"path": r["path"], "to": "int8-aef", "name": "aef_back"})
    with rasterio.open(ws.root() / back["path"]) as s:
        assert np.array_equal(s.read(), q[:, ::-1, :])        # Google's codes come back exactly
    sc = run(client, "/api/emb/convert", {"path": r["path"], "to": "int8-scaled", "name": "aef_scaled"})
    assert sc["max_error"] < 0.01 and sc["cosine_min"] > 0.999
    with rasterio.open(ws.root() / sc["path"]) as s:
        assert s.dtypes[0] == "int8" and all(0 < x < 0.02 for x in s.scales)
    h = run(client, "/api/emb/convert", {"path": r["path"], "to": "float16", "name": "aef_f16"})
    assert h["max_error"] < 1e-3 and h["size_out_mb"] <= r["size_out_mb"]


def test_convert_refuses_big_values_for_aef_coding(client, emb_tif, tmp_path):
    from lulc_fetch.embeddings import formats as cv
    big = tmp_path / "big.tif"
    with rasterio.open(ws.root() / emb_tif) as s:
        prof, a = s.profile, s.read() * 10                      # like TESSERA: values beyond ±1
    with rasterio.open(big, "w", **prof) as d:
        d.write(a)
    with pytest.raises(ValueError, match="unit length"):
        cv.convert(big, tmp_path / "x.tif", "int8-aef")
    r = cv.convert(big, tmp_path / "u.tif", "int8-aef", normalise=True)
    assert r["cosine_min"] > 0.999


def test_embedding_layer_keeps_all_bands_and_shows_colour(client, emb_tif, tmp_path):
    """An embedding is recognised, drawn in colour from all its bands (PCA), and exported with all of them."""
    info = ok(client.get("/api/rasters/info", params={"path": emb_tif}))
    assert info["embedding"]["dims"] == 64 and info["count"] == 64
    r = ok(client.post("/api/analyze/render", json={"path": emb_tif, "band_map": {}, "scale": 1, "offset": 0, "pca": True, "stretch": "auto"}))
    assert r["kind"] == "rgb" and "PCA of 64 bands" in r["title"] and r["image"].startswith("data:image/png")
    again = ok(client.post("/api/analyze/render", json={"path": emb_tif, "band_map": {}, "scale": 1, "offset": 0, "pca": True, "stretch": "auto"}))
    assert again["image"] == r["image"]                                    # the same colours every time
    (x0, y1), (x1, y0) = lonlat(5, 5), lonlat(25, 25)
    for clip in (None, {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}):
        e = run(client, "/api/layers/export", {"path": emb_tif, "format": "tif", "name": "emb_export", "band_map": {}, "pca": True, "clip": clip})
        blob = client.get(e["url"]).content
        f = tmp_path / "x.tif"
        f.write_bytes(blob)
        with rasterio.open(f) as s:
            assert s.count == 64                                           # all bands, not the 3 shown


@pytest.mark.dl
def test_train_and_classify_embedding_model(client, emb_tif, home):
    """Embeddings ▸ Train embedding model (light model, all 64 bands) then Classify with embedding model."""
    (x0, y1), (xm, _), (x1, y0) = lonlat(1, 1), lonlat(29, 0), lonlat(58, 38)
    poly = lambda a, b, c, d: {"type": "Polygon", "coordinates": [[[a, b], [c, b], [c, d], [a, d], [a, b]]]}   # noqa: E731
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": poly(x0, y0, xm, y1), "properties": {"class": "left kind"}},
        {"type": "Feature", "geometry": poly(lonlat(31, 0)[0], y0, x1, y1), "properties": {"class": "right kind"}}]}
    body = {"path": emb_tif, "ground_truth": {"type": "vector", "geojson": gj, "field": "class"}, "arch": "light_enet", "patch_px": 32,
            "overlap": 0.5, "params": {"epochs": 3, "batch_size": 4, "device": "cpu"}, "name": "emb_enet"}
    assert client.post("/api/emb/train", json={**body, "arch": "unet"}).status_code == 400          # only the light models here
    r = run(client, "/api/emb/train", body)
    assert r["bands"] == 64 and r["patches"] >= 5 and r["report"] and r["config"]["arch"] == "light_enet"
    with rasterio.open(home / r["map"]) as s:
        assert s.count == 2 and (s.width, s.height) == (60, 40)                   # class + confidence, the whole layer
    m = next(m for m in ok(client.get("/api/dl/models")) if m["folder"] == r["model_folder"])
    assert m["arch_key"] == "light_enet" and m["in_channels"] == 64
    p = run(client, "/api/dl/predict", {"model": m["folder"], "inputs": [{"path": emb_tif, "name": "emb"}], "name": "emb_map2"})
    assert {c["name"] for c in p["classes"]} <= {"left kind", "right kind"}


def test_range_download_survives_dropped_connections():
    """A TESSERA tile is read in pieces; a piece whose connection drops (or comes back short) is asked for again."""
    import http.server
    import threading

    from lulc_fetch.embeddings.sources import get_range
    data = bytes(range(256)) * 400   # 102,400 bytes
    seen = {}

    class Flaky(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            a, b = map(int, self.headers["Range"].split("=")[1].split("-"))
            seen[(a, b)] = seen.get((a, b), 0) + 1
            body = data[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            # the first try of every piece breaks off halfway (as a dropped connection does)
            self.wfile.write(body[: len(body) // 2] if seen[(a, b)] == 1 else body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Flaky)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/tile.npy"
        assert get_range(url, 100, 90_000, chunk=30_000, timeout=5) == data[100:90_001]
        assert len(seen) == 3 and all(n == 2 for n in seen.values())
    finally:
        srv.shutdown()


def test_many_band_file_is_read_in_one_pass(tmp_path, monkeypatch):
    """A pixel-interleaved file with many bands (an embedding) is read once for all bands, averaged over k × k pixels:
    GDAL's own decimated read unpacks every block again for each band (minutes for a 20 km embedding)."""
    import rasterio
    from rasterio.transform import from_origin

    from lulc_fetch import analysis
    a = np.arange(16 * 120 * 90, dtype="float32").reshape(16, 120, 90)
    p = tmp_path / "emb.tif"
    with rasterio.open(p, "w", driver="GTiff", width=90, height=120, count=16, dtype="float32", crs="EPSG:32643",
                       transform=from_origin(0, 0, 10, 10), interleave="pixel", tiled=True, blockxsize=32, blockysize=32) as d:
        d.write(a)
    calls = []
    real = analysis._read_strips
    monkeypatch.setattr(analysis, "_read_strips", lambda *x, **k: calls.append(1) or real(*x, **k))
    analysis._STRIPS.clear()
    with rasterio.open(p) as src:
        got = analysis._read(src, [1, 5], max_px=40)            # k = 3 → 40 × 30
        again = analysis._read(src, list(range(1, 17)), max_px=40)
    assert got.shape == (2, 40, 30) and again.shape == (16, 40, 30)
    assert len(calls) == 1, "the second read should come from the cache"
    want = a[[0, 4]].reshape(2, 40, 3, 30, 3).mean(axis=(2, 4))
    assert np.allclose(got, want)


def test_missing_layer_file_says_so(client):
    r = client.get("/api/rasters/info", params={"path": "uploads/gone_away.tif"})
    assert r.status_code == 404 and "missing" in r.json()["detail"] and "gone_away.tif" in r.json()["detail"]
