"""TinyUNet: a compact U-Net (Ronneberger et al., 2015), about 0.3 M parameters.

The U-Net shape, kept whole: an encoder of four stages that halve the size and double the channels (16 → 32 → 64 → 128,
a 256-channel bottleneck at 1/16), and a decoder that doubles the size back up four times, each time joined to the
encoder stage of the same size (skip connections, which keep field edges and thin roads sharp). To make it light, every
3 × 3 convolution after the first is depthwise-separable (a 3 × 3 per channel + a 1 × 1 across channels: about 8× fewer
weights), and upsampling is bilinear instead of transposed convolutions. The first convolution is a full 3 × 3, so all
input bands (e.g. 64 or 128 embedding dimensions) are mixed from the start."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import ConvBNAct, SegModel


class DSConv(nn.Sequential):
    """Depthwise-separable 3 × 3 convolution: depthwise 3 × 3 → pointwise 1 × 1, each with batch norm + ReLU."""

    def __init__(self, cin, cout):
        super().__init__(ConvBNAct(cin, cin, 3, groups=cin, act="relu"), ConvBNAct(cin, cout, 1, act="relu"))


class Double(nn.Sequential):
    """The U-Net block: two 3 × 3 convolutions (here depthwise-separable)."""

    def __init__(self, cin, cout):
        super().__init__(DSConv(cin, cout), DSConv(cout, cout))


class Up(nn.Module):
    """Bilinear ×2, joined with the encoder features of the same size, then a U-Net block."""

    def __init__(self, cin, cskip, cout):
        super().__init__()
        self.block = Double(cin + cskip, cout)

    def forward(self, x, skip):
        x = F.interpolate(x, size=skip.shape[-2:], mode="bilinear", align_corners=False)
        return self.block(torch.cat([x, skip], 1))


class TinyUNet(SegModel):
    stride = 16

    def __init__(self, in_channels: int = 3, num_classes: int = 2, base: int = 16):
        super().__init__(in_channels, num_classes)
        c = [base, base * 2, base * 4, base * 8, base * 16]            # 16, 32, 64, 128, 256
        self.stem = nn.Sequential(ConvBNAct(in_channels, c[0], 3, act="relu"), DSConv(c[0], c[0]))
        self.down = nn.ModuleList([Double(c[i], c[i + 1]) for i in range(4)])
        self.up = nn.ModuleList([Up(c[i + 1], c[i], c[i]) for i in reversed(range(4))])
        self.classifier = nn.Conv2d(c[0], num_classes, 1)

    def body(self, x):
        skips = [self.stem(x)]                                         # full size, 16
        for d in self.down:                                            # 1/2 … 1/16
            skips.append(d(F.max_pool2d(skips[-1], 2)))
        y = skips.pop()
        for u in self.up:
            y = u(y, skips.pop())
        return self.classifier(y)
