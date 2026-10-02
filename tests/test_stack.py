"""Stack layers: bands, indices and formulas from several layers on one grid."""

import rasterio

from tests.helpers import run


def test_stack_bands_index_and_labels(client, data, s2_info, home):
    items = [{"path": data["s2"], "name": "s2", "bands": [3, 4]},
             {"path": data["s2"], "name": "ndvi", "index": "NDVI", "band_map": s2_info["band_map"], "scale": s2_info["scale"]},
             {"path": data["labels"], "name": "labels", "bands": [1]}]
    r = run(client, "/api/stack", {"items": items, "ref": data["s2"], "name": "stack"})
    with rasterio.open(home / r["path"]) as s:
        assert s.count == 4 and (s.width, s.height) == (96, 96)


def test_stack_coarser_and_clipped(client, data, home):
    r = run(client, "/api/stack", {"items": [{"path": data["s2"], "name": "s2"}], "ref": data["s2"], "factor": 2, "clip": data["aoi"], "name": "small"})
    with rasterio.open(home / r["path"]) as s:
        assert s.width < 48 and abs(s.res[0] - 20) < 1   # about 2 × 10 m (fitted to the area)
