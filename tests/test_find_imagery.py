"""Find imagery: catalogue search and geocoding (internet: run with --network), and AOI files (offline)."""

import json

import pytest

from tests.helpers import ok

BENGALURU = {"type": "Polygon", "coordinates": [[[77.58, 12.96], [77.61, 12.96], [77.61, 12.99], [77.58, 12.99], [77.58, 12.96]]]}


def test_aoi_from_a_geojson_file(client, data):
    blob = json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": data["aoi"]}]}).encode()
    r = ok(client.post("/api/aoi/upload", files=[("files", ("area.geojson", blob, "application/geo+json"))]))
    assert r


def test_aoi_rejects_garbage(client):
    assert client.post("/api/aoi/upload", files=[("files", ("area.geojson", b"not json", "application/json"))]).status_code == 400


@pytest.mark.network
def test_search_sentinel2(client):
    r = ok(client.post("/api/search", json={"source": "earth-search", "aoi": BENGALURU, "start": "2024-01-01", "end": "2024-02-15", "max_cloud": 30}))
    assert r["count"] > 0 and r["scenes"], "no Sentinel-2 scenes found"
    for sc in r["scenes"]:   # scenes = one date, its tiles merged
        assert sc["cloud"] is None or sc["cloud"] <= 30
        assert sc["items"] and sc["items"][0]["id"]


@pytest.mark.network
def test_geocode(client):
    r = ok(client.get("/api/geocode", params={"q": "Bengaluru"}))
    assert r
