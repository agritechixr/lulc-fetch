"""PCA & dimensionality reduction on the 12 Sentinel-2 bands of the test area."""

import numpy as np
import pytest
import rasterio

import webapp.workspace as ws
from tests.helpers import run
from tests.real.metrics import stretch_rgb

TOOL = "PCA & dimensionality reduction"


@pytest.mark.parametrize("method", ["pca", "ica", "nmf", "kernel"])
def test_components(client, s2_stack, method, report):
    r = run(client, "/api/pca/run", {"path": s2_stack["path"], "bands": list(range(1, 13)), "method": method,
                                     "params": {"n_components": 3}, "name": "albano"})
    with rasterio.open(ws.root() / r["path"]) as s:
        a = s.read([1, 2, 3])
    report.image(f"{method}_components", stretch_rgb(np.nan_to_num(a)), f"{method.upper()}: components 1–3 as RGB")
    if method == "pca":
        ev = r["explained_variance"]
        report.metric("PC1 explained variance", ev[0])
        report.metric("PC1–3 explained variance", sum(ev[:3]))
        report.table("Explained variance", ["component", "share"], [[f"PC{k + 1}", v] for k, v in enumerate(ev[:6])])
        assert sum(ev[:3]) > 0.8   # 12 correlated bands: 3 components hold most of the information
    assert a.shape[0] == 3
