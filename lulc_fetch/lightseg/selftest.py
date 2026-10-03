"""Check light segmentation models in a separate process (PyTorch's OpenMP runtime must not meet XGBoost's / LightGBM's in
one process, see lulc_fetch.dlrunner): any number of bands, any size, a short training run, size against the paper.

    python -m lulc_fetch.lightseg.selftest dabnet enet          # prints one JSON line per model
    python -m lulc_fetch.lightseg.selftest --all
"""

from __future__ import annotations

import json
import sys


def check(name: str, bands=(3, 64, 128), sizes=((256, 256), (300, 217), (40, 40))) -> dict:
    import numpy as np
    import torch

    from . import MODELS, build_model
    from .common import count_params

    out = {"model": name, "shapes_ok": True, "wrong_bands_refused": False}
    for b in bands:
        m = build_model(name, in_channels=b, num_classes=5).eval()
        with torch.no_grad():
            for h, w in sizes:
                y = m(torch.randn(1, b, h, w))
                if y.shape != (1, 5, h, w) or not torch.isfinite(y).all():
                    out["shapes_ok"] = False
                    out.setdefault("bad", []).append([b, h, w, list(y.shape)])
        try:
            m(torch.randn(1, b + 1, 64, 64))
        except ValueError:
            out["wrong_bands_refused"] = True
    out["params_M"] = round(count_params(build_model(name, 3, 19)) / 1e6, 3)
    out["published_M"] = MODELS[name][3]
    torch.manual_seed(0)
    m = build_model(name, in_channels=64, num_classes=3)
    x = torch.randn(4, 64, 64, 64)
    y = (x[:, 0] > 0).long() + (x[:, 1] > 1).long()
    opt = torch.optim.Adam(m.parameters(), 0.01)
    losses = []
    for _ in range(15):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(m(x), y)
        loss.backward()
        opt.step()
        losses.append(float(loss))
    out["loss_first"], out["loss_last"] = round(losses[0], 4), round(losses[-1], 4)
    out["finite"] = bool(np.isfinite(losses).all())
    return out


def main(argv: list[str]) -> int:
    from . import MODELS
    names = list(MODELS) if argv == ["--all"] or not argv else argv
    for n in names:
        print(json.dumps(check(n)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
