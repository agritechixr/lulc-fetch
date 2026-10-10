"""SAR flood map refined by machine learning (Analysis ▸ SAR ▸ Flood map: ML refinement).

Self-training on the Flood & water map's own confident pixels: where its confidence is high (water) or low (dry land)
the labels are taken as right, and a model learns from them what water looks like in this scene, from more than the
threshold saw: VV and VH in dB, their difference, local mean and spread (5 × 5 and 11 × 11: texture tells calm water
from radar shadow and smooth fields), the drop since a pre-flood image, and HAND and slope from a DEM (water lies low
and flat). It then decides every pixel, including the uncertain ones the thresholds left open.

Models: LightGBM (fast, the default) or a compact U-Net (TinyUNet, trained in the deep-learning helper process on
256 × 256 patches, the uncertain pixels left out of the loss), which also uses the shapes around each pixel. Checked on
held-out spatial blocks of the confident pixels (accuracy, F1 and IoU of water)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from scipy import ndimage as ndi

from .. import progress

CLASSES = {1: "Dry land", 2: "Water / flood", 3: "Permanent water"}
COLORS = {1: (235, 225, 200, 120), 2: (0, 190, 255, 255), 3: (8, 48, 107, 255)}


def _pols(src) -> tuple[np.ndarray, np.ndarray | None]:
    """VV and VH (dB) from a SAR layer, by band names, else bands 1 and 2."""
    names = [(d or "").upper() for d in src.descriptions]
    iv = next((i for i, n in enumerate(names) if "VV" in n), 0)
    ih = next((i for i, n in enumerate(names) if "VH" in n), 1 if src.count > 1 else None)
    vv = src.read(iv + 1, masked=True).astype("float64").filled(np.nan)
    vh = src.read(ih + 1, masked=True).astype("float64").filled(np.nan) if ih is not None and ih != iv else None
    lin = np.nanmedian(vv) > 0 and np.nanmedian(vv) < 1.5            # linear power (0–1), not dB
    if lin:
        vv = 10 * np.log10(np.maximum(vv, 1e-6))
        vh = 10 * np.log10(np.maximum(vh, 1e-6)) if vh is not None else None
    return vv, vh


def features(sar: str, *, pre: str | None = None, dem: str | None = None) -> tuple[np.ndarray, list[str], dict]:
    from ..hydro.common import open_dem, read_like
    with rasterio.open(sar) as s:
        vv, vh = _pols(s)
        prof = {**s.profile, "driver": "GTiff", "count": 1, "compress": "deflate"}
        prof.pop("photometric", None)
        grid = {"transform": s.transform, "crs": s.crs, "width": s.width, "height": s.height}
    bands, names = [vv], ["VV_dB"]
    if vh is not None:
        bands += [vh, vv - vh]
        names += ["VH_dB", "VV_minus_VH"]
    for b, nm in list(zip(bands[:2], names[:2])):
        f = np.nan_to_num(b, nan=float(np.nanmedian(b)))
        for w in (5, 11):
            m = ndi.uniform_filter(f, w)
            sd = np.sqrt(np.maximum(ndi.uniform_filter(f * f, w) - m * m, 0))
            bands += [m, sd]
            names += [f"{nm}_mean{w}", f"{nm}_std{w}"]
    if pre:
        with rasterio.open(pre) as s2:
            pvv, _ = _pols(s2)
            if pvv.shape != vv.shape:
                from rasterio.warp import Resampling, reproject
                out = np.full(vv.shape, np.nan)
                reproject(pvv, out, src_transform=s2.transform, src_crs=s2.crs, dst_transform=grid["transform"], dst_crs=grid["crs"],
                          resampling=Resampling.bilinear)
                pvv = out
        bands.append(vv - pvv)
        names.append("VV_change_dB")
    if dem:
        import tempfile

        from ..hydro.common import Surface
        from ..hydro.terrain import hand, slope_aspect
        # the DEM on the SAR's grid, then HAND and slope there
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "dem.tif"
            from rasterio.warp import Resampling, reproject
            with rasterio.open(dem) as d:
                a = np.full((grid["height"], grid["width"]), np.nan, "float32")
                reproject(d.read(1, masked=True).astype("float32").filled(np.nan), a, src_transform=d.transform, src_crs=d.crs,
                          dst_transform=grid["transform"], dst_crs=grid["crs"], src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
            with rasterio.open(tmp, "w", **{**prof, "dtype": "float32", "nodata": np.nan}) as d:
                d.write(a, 1)
            g, z = open_dem(tmp)
            s = Surface(g, z)
            area_km2 = g.cell_m2[g.valid].sum() / 1e6
            hnd, _, _ = hand(s, s.streams(max(0.05, min(1.0, area_km2 / 200))), s.filled)
            sl, _, _ = slope_aspect(g, s.filled)
        bands += [hnd.reshape(g.h, g.w), sl]
        names += ["HAND_m", "slope_deg"]
    return np.stack(bands).astype("float32"), names, {"profile": prof, "grid": grid}


def _labels(confidence: str | None, classes: str | None, grid: dict, hi: float, lo: float) -> tuple[np.ndarray, np.ndarray]:
    """(labels: 1 water, 0 dry, -1 unknown; permanent water mask) on the SAR's grid from the flood map."""
    from rasterio.warp import Resampling, reproject

    def onto(path, nearest):
        with rasterio.open(path) as s:
            a = s.read(1, masked=True).astype("float64").filled(np.nan)
            out = np.full((grid["height"], grid["width"]), np.nan)
            reproject(a, out, src_transform=s.transform, src_crs=s.crs, dst_transform=grid["transform"], dst_crs=grid["crs"],
                      src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest if nearest else Resampling.bilinear)
            return out, s.tags()
    lab = np.full((grid["height"], grid["width"]), -1, "int8")
    perm = np.zeros(lab.shape, bool)
    if confidence:
        c, _ = onto(confidence, False)
        lab[c >= hi] = 1
        lab[c <= lo] = 0
    if classes:
        k, tags = onto(classes, True)
        names = json.loads(tags.get("classes", "{}"))
        ids = {v.lower(): int(i) for i, v in names.items()}
        perm = k == ids.get("permanent water", -99)
        if not confidence:
            lab[np.isin(k, [ids.get("flood (new water)", -99), ids.get("water", -99), ids.get("permanent water", -99)])] = 1
            lab[k == ids.get("dry land", -99)] = 0
    if (lab == 1).sum() < 50 or (lab == 0).sum() < 50:
        raise ValueError("The flood map has too few confident water or dry pixels to learn from (at least 50 of each)")
    return lab, perm


def _blocks(shape, px: int = 64) -> np.ndarray:
    r, c = np.indices(shape)
    return (r // px) * 100_000 + (c // px)


def refine(sar: str, out_dir: Path, *, confidence: str | None = None, classes: str | None = None, pre: str | None = None, dem: str | None = None,
           model: str = "lgbm", hi: float = 0.75, lo: float = 0.25, max_samples: int = 200_000, unet_runner=None, epochs: int = 30,
           name: str = "flood_ml") -> dict:
    if not confidence and not classes:
        raise ValueError("Give the Flood & water map's confidence (or classes) layer")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress.update(0.05, "Features from the SAR image")
    X, names, meta = features(sar, pre=pre, dem=dem)
    grid, prof = meta["grid"], meta["profile"]
    nb, h, w = X.shape
    lab, perm = _labels(confidence, classes, grid, hi, lo)
    ok = np.isfinite(X).all(0)
    lab[~ok] = -1
    rng = np.random.default_rng(0)
    blocks = _blocks((h, w))
    ub = np.unique(blocks[lab >= 0])
    test_b = set(rng.choice(ub, max(1, ub.size // 5), replace=False).tolist())
    is_test = np.isin(blocks, list(test_b))
    progress.update(0.2, f"Training {'LightGBM' if model == 'lgbm' else 'TinyUNet'} on {int((lab >= 0).sum()):,} confident pixels")
    if model == "lgbm":
        from lightgbm import LGBMClassifier
        flat = X.reshape(nb, -1).T
        L = lab.ravel()
        tr = np.flatnonzero((L >= 0) & ~is_test.ravel())
        te = np.flatnonzero((L >= 0) & is_test.ravel())
        for part in ("tr", "te"):
            ix = tr if part == "tr" else te
            # balance: as many dry as water pixels (at most max_samples / 2 each)
            w1, w0 = ix[L[ix] == 1], ix[L[ix] == 0]
            k = min(max(len(w1), 1), max_samples // 2)
            ix = np.r_[rng.choice(w1, min(k, len(w1)), replace=False), rng.choice(w0, min(k, len(w0)), replace=False)]
            if part == "tr":
                tr = ix
            else:
                te = ix
        est = LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=63, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=0, verbose=-1)
        est.fit(flat[tr], L[tr])
        progress.update(0.6, "Mapping every pixel")
        prob = np.full(h * w, np.nan, "float32")
        good = np.flatnonzero(ok.ravel())
        for s in range(0, good.size, 1_000_000):
            prob[good[s:s + 1_000_000]] = est.predict_proba(flat[good[s:s + 1_000_000]])[:, 1]
        prob = prob.reshape(h, w)
        imp = est.feature_importances_.astype(float)
        importance = {names[i]: round(float(imp[i] / imp.sum()), 4) for i in np.argsort(-imp)[:8]} if imp.sum() else {}
        test_idx = te
    else:
        if unet_runner is None:
            raise ValueError("The U-Net needs the deep-learning add-on")
        npz = out_dir / "_unet_in.npz"
        np.savez(npz, X=np.nan_to_num(X), lab=np.where(is_test, -1, lab), ok=ok)
        out_npz = out_dir / "_unet_out.npz"
        info = unet_runner(str(npz), str(out_npz), epochs)
        prob = np.load(out_npz)["prob"].astype("float32")
        prob[~ok] = np.nan
        npz.unlink(missing_ok=True)
        out_npz.unlink(missing_ok=True)
        importance = {"unet": info}
        test_idx = np.flatnonzero(((lab >= 0) & is_test).ravel())
    # scores on held-out blocks of confident pixels
    y = lab.ravel()[test_idx]
    p = (prob.ravel()[test_idx] >= 0.5).astype(int)
    tp, fp, fn = int(((p == 1) & (y == 1)).sum()), int(((p == 1) & (y == 0)).sum()), int(((p == 0) & (y == 1)).sum())
    acc = float((p == y).mean()) if y.size else None
    f1 = 2 * tp / max(2 * tp + fp + fn, 1)
    iou = tp / max(tp + fp + fn, 1)
    cls = np.where(np.isfinite(prob), np.where(prob >= 0.5, 2, 1), 0).astype("uint8")
    cls[perm & (cls == 2)] = 3
    unc_before = int((lab == -1)[ok].sum())
    p_path, c_path = out_dir / f"{name}_probability.tif", out_dir / f"{name}_classes.tif"
    with rasterio.open(p_path, "w", **{**prof, "dtype": "float32", "nodata": np.nan}) as d:
        d.write(prob, 1)
        d.set_band_description(1, "Water probability (ML refinement)")
    with rasterio.open(c_path, "w", **{**prof, "dtype": "uint8", "nodata": 0}) as d:
        d.write(cls, 1)
        d.set_band_description(1, "Water / flood (ML refinement)")
        d.write_colormap(1, COLORS)
        d.update_tags(classes=json.dumps({str(k): v for k, v in CLASSES.items()}))
    t = grid["transform"]
    px_m2 = abs(t.a * t.e) * ((111_320 * 110_574) if grid["crs"] and grid["crs"].is_geographic else 1)
    progress.update(1.0, "Done")
    return {"outputs": [str(p_path), str(c_path)], "model": "LightGBM" if model == "lgbm" else "TinyUNet", "features": names,
            "trained_on": {"water": int((lab == 1).sum()), "dry": int((lab == 0).sum())}, "uncertain_resolved": unc_before,
            "water_km2": round(float((cls >= 2).sum()) * px_m2 / 1e6, 4), "held_out": {"accuracy": round(acc, 4) if acc is not None else None,
            "f1_water": round(f1, 4), "iou_water": round(iou, 4), "pixels": int(y.size)}, "importance": importance}


def train_unet(npz: str, out_npz: str, epochs: int = 30, seed: int = 0) -> dict:
    """TinyUNet on 256 × 256 patches of the feature stack; unknown pixels (−1) are left out of the loss. Runs in the
    deep-learning helper process."""
    import torch
    from torch import nn

    from ..lightseg import build_model
    torch.manual_seed(seed)
    d = np.load(npz)
    X, lab = d["X"].astype("float32"), d["lab"].astype("int64")
    nb, h, w = X.shape
    mu = X.reshape(nb, -1).mean(1)[:, None, None]
    sd = X.reshape(nb, -1).std(1)[:, None, None] + 1e-6
    X = (X - mu) / sd
    P = 128 if min(h, w) >= 128 else (min(h, w) // 32) * 32
    if P < 32:
        raise ValueError("The image is too small for the U-Net (at least 32 × 32 pixels): use LightGBM")
    pad_h, pad_w = (-h) % P, (-w) % P
    Xp = np.pad(X, ((0, 0), (0, pad_h), (0, pad_w)))
    Lp = np.pad(lab, ((0, pad_h), (0, pad_w)), constant_values=-1)
    # training crops: random P × P windows with enough confident pixels (many per epoch, so small images train too)
    known = (Lp >= 0).astype("float32")
    frac = ndi.uniform_filter(known, P, mode="constant")
    half = P // 2
    centres = np.argwhere(frac[half:Xp.shape[1] - half + 1, half:Xp.shape[2] - half + 1] > 0.05)
    if not centres.size:
        raise ValueError("No patch has enough confident pixels to learn from")
    w1 = (Lp == 1).sum()
    weight = torch.tensor([1.0, float(np.clip((Lp == 0).sum() / max(w1, 1), 0.2, 5))])
    dev = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    net = build_model("tinyunet", in_channels=nb, num_classes=2).to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss(ignore_index=-1, weight=weight.to(dev))
    rng = np.random.default_rng(seed)
    steps = int(np.clip(centres.shape[0] // (P * 4), 20, 200))
    for ep in range(epochs):
        net.train()
        tot = 0.0
        for _ in range(steps):
            pick = centres[rng.integers(0, centres.shape[0], 8)]
            xb = np.stack([Xp[:, r:r + P, c:c + P] for r, c in pick])
            yb = np.stack([Lp[r:r + P, c:c + P] for r, c in pick])
            if rng.random() < 0.5:
                xb, yb = xb[..., ::-1], yb[..., ::-1]
            if rng.random() < 0.5:
                xb, yb = xb[..., ::-1, :], yb[..., ::-1, :]
            xt = torch.tensor(np.ascontiguousarray(xb)).to(dev)
            yt = torch.tensor(np.ascontiguousarray(yb)).to(dev)
            opt.zero_grad()
            loss = loss_fn(net(xt), yt)
            loss.backward()
            opt.step()
            tot += float(loss.item())
        progress.update(0.1 + 0.8 * (ep + 1) / epochs, f"U-Net epoch {ep + 1} of {epochs}: loss {tot / steps:.3f}")
    net.eval()
    prob = np.zeros(Xp.shape[1:], "float32")
    cnt = np.zeros(Xp.shape[1:], "float32")
    with torch.no_grad():
        for r in range(0, Xp.shape[1] - P + 1, P // 2):
            for c in range(0, Xp.shape[2] - P + 1, P // 2):
                xt = torch.tensor(Xp[None, :, r:r + P, c:c + P]).to(dev)
                pr = torch.softmax(net(xt), 1)[0, 1].cpu().numpy()
                prob[r:r + P, c:c + P] += pr
                cnt[r:r + P, c:c + P] += 1
    prob = (prob / np.maximum(cnt, 1))[:h, :w]
    np.savez(out_npz, prob=prob)
    return {"epochs": epochs, "steps_per_epoch": steps, "device": dev}
