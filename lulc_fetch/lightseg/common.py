"""Building blocks shared by the light segmentation models, and the base class that makes every model work with any
number of input bands and any image size."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class SegModel(nn.Module):
    """Base class: ``forward`` pads the image to a multiple of ``stride`` (the model's total downsampling), runs ``body``
    and crops the class scores back to the input size, so any height × width works (256 × 256, 300 × 217, 1024 × 768…).
    Subclasses implement ``body(x) -> logits`` at the padded size (or smaller: they are resized to it)."""

    stride = 8

    def __init__(self, in_channels: int, num_classes: int):
        super().__init__()
        if in_channels < 1 or num_classes < 1:
            raise ValueError("in_channels and num_classes must be at least 1")
        self.in_channels, self.num_classes = in_channels, num_classes

    def body(self, x: torch.Tensor) -> torch.Tensor:
        raise NotImplementedError

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() != 4 or x.shape[1] != self.in_channels:
            raise ValueError(f"{type(self).__name__} expects (batch, {self.in_channels}, height, width); got {tuple(x.shape)}")
        h, w = x.shape[-2:]
        s = self.stride
        ph, pw = (-h) % s, (-w) % s
        if ph or pw:   # reflect where possible (no artificial edges), zeros for tiny images
            mode = "reflect" if h > ph and w > pw else "constant"
            x = F.pad(x, (0, pw, 0, ph), mode=mode)
        y = self.body(x)
        if y.shape[-2:] != x.shape[-2:]:
            y = F.interpolate(y, size=x.shape[-2:], mode="bilinear", align_corners=False)
        return y[..., :h, :w]


class ConvBNAct(nn.Sequential):
    """Convolution → batch norm → activation (PReLU by default, as most of these papers use)."""

    def __init__(self, cin, cout, k=3, s=1, d=1, groups=1, act: str | None = "prelu", bias=False):
        kh, kw = (k, k) if isinstance(k, int) else k
        dh, dw = (d, d) if isinstance(d, int) else d
        pad = ((kh - 1) // 2 * dh, (kw - 1) // 2 * dw)
        layers = [nn.Conv2d(cin, cout, (kh, kw), s, pad, (dh, dw), groups=groups, bias=bias), nn.BatchNorm2d(cout)]
        if act == "prelu":
            layers.append(nn.PReLU(cout))
        elif act == "relu":
            layers.append(nn.ReLU(inplace=True))
        elif act is not None:
            raise ValueError(act)
        super().__init__(*layers)


class BNAct(nn.Sequential):
    def __init__(self, c, act="prelu"):
        super().__init__(nn.BatchNorm2d(c), nn.PReLU(c) if act == "prelu" else nn.ReLU(inplace=True))


class DownsamplingUnit(nn.Module):
    """ENet / ERFNet-style downsampling: a stride-2 3×3 convolution concatenated with a 2×2 max pool of the input (when
    the output has more channels than the input), so no information is thrown away; otherwise just the convolution."""

    def __init__(self, cin, cout, act="prelu"):
        super().__init__()
        self.pool = cout > cin
        self.conv = nn.Conv2d(cin, cout - cin if self.pool else cout, 3, 2, 1, bias=False)
        self.bn_act = BNAct(cout, act)

    def forward(self, x):
        y = self.conv(x)
        if self.pool:
            y = torch.cat([y, F.max_pool2d(x, 2, ceil_mode=True)[..., :y.shape[-2], :y.shape[-1]]], 1)
        return self.bn_act(y)


def channel_shuffle(x: torch.Tensor, groups: int) -> torch.Tensor:
    b, c, h, w = x.shape
    return x.view(b, groups, c // groups, h, w).transpose(1, 2).reshape(b, c, h, w)


def resize_to(x: torch.Tensor, ref: torch.Tensor, mode="bilinear") -> torch.Tensor:
    """x resized to ref's height × width (for skips and input injection at any size)."""
    if x.shape[-2:] == ref.shape[-2:]:
        return x
    if mode == "avg":
        return F.adaptive_avg_pool2d(x, ref.shape[-2:])
    return F.interpolate(x, size=ref.shape[-2:], mode=mode, align_corners=False)


class SE(nn.Module):
    """Squeeze-and-excitation channel attention."""

    def __init__(self, c, r=4):
        super().__init__()
        m = max(4, c // r)
        self.fc = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Conv2d(c, m, 1), nn.ReLU(inplace=True), nn.Conv2d(m, c, 1), nn.Sigmoid())

    def forward(self, x):
        return x * self.fc(x)


def count_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
