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
