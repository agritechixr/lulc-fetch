"""LEDNet (Wang et al., 2019, "LEDNet: A Lightweight Encoder-Decoder Network for Real-Time Semantic Segmentation"),
about 0.94 M parameters.

Encoder: downsampling units (3×3 stride-2 conv ⊕ max pool) to 32, 64 and 128 channels, with 3, 2 and 8 Split-Shuffle
non-bottleneck (SS-nbt) units; the last eight use dilations 1, 2, 5, 9, 2, 5, 9, 17. An SS-nbt unit splits the channels
in two, runs factorized 3×1 / 1×3 convolutions on each half (the second pair dilated), joins them, adds the input and
shuffles the channels. Decoder: an Attention Pyramid Network (APN) — a three-level pyramid (7×7, 5×5, 3×3, each stride 2)
fused top-down, multiplied with a 1×1 projection of the features, plus a global-pooling branch — then upsampling to full
size. As in the authors' code, the pyramid works on a single attention channel."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import ConvBNAct, DownsamplingUnit, SegModel, channel_shuffle, resize_to


class SSnbt(nn.Module):
    def __init__(self, c, d=1, drop=0.0):
        super().__init__()
        h = c // 2

        def branch(first_vertical):
            k1, k2 = ((3, 1), (1, 3)) if first_vertical else ((1, 3), (3, 1))
            dd1, dd2 = ((d, 1), (1, d)) if first_vertical else ((1, d), (d, 1))
            return nn.Sequential(
                nn.Conv2d(h, h, k1, padding=(k1[0] // 2, k1[1] // 2), bias=True), nn.ReLU(inplace=True),
                nn.Conv2d(h, h, k2, padding=(k2[0] // 2, k2[1] // 2), bias=False), nn.BatchNorm2d(h), nn.ReLU(inplace=True),
                nn.Conv2d(h, h, k1, padding=(dd1[0] * (k1[0] // 2), dd1[1] * (k1[1] // 2)), dilation=dd1, bias=True), nn.ReLU(inplace=True),
                nn.Conv2d(h, h, k2, padding=(dd2[0] * (k2[0] // 2), dd2[1] * (k2[1] // 2)), dilation=dd2, bias=False), nn.BatchNorm2d(h),
                nn.Dropout2d(drop))
        self.left, self.right = branch(True), branch(False)

    def forward(self, x):
        a, b = x.chunk(2, 1)
        y = F.relu(x + torch.cat([self.left(a), self.right(b)], 1))
        return channel_shuffle(y, 2)


class APN(nn.Module):
    """Attention Pyramid Network decoder: a per-pixel attention map from a 3-level pyramid, plus a global branch."""

    def __init__(self, c, num_classes):
        super().__init__()
        self.d1 = ConvBNAct(c, 1, 7, 2, act="relu")
        self.d2 = ConvBNAct(1, 1, 5, 2, act="relu")
        self.d3 = nn.Sequential(ConvBNAct(1, 1, 3, 2, act="relu"), ConvBNAct(1, 1, 3, act="relu"))
        self.c2 = ConvBNAct(1, 1, 5, act="relu")
        self.c1 = ConvBNAct(1, 1, 7, act="relu")
        self.mid = ConvBNAct(c, num_classes, 1, act="relu")
        self.glob = nn.Sequential(nn.AdaptiveAvgPool2d(1), ConvBNAct(c, num_classes, 1, act="relu"))

    def forward(self, x):
        p1 = self.d1(x)
        p2 = self.d2(p1)
        p3 = self.d3(p2)
        a = resize_to(p3, p2) + self.c2(p2)
        a = resize_to(a, p1) + self.c1(p1)
        a = resize_to(a, x)
        return self.mid(x) * a + resize_to(self.glob(x), x)


class LEDNet(SegModel):
    stride = 64   # the encoder's 1/8, then the decoder's three stride-2 pyramid levels

    def __init__(self, in_channels: int = 3, num_classes: int = 2, drop: float = 0.0):
        super().__init__(in_channels, num_classes)
        self.encoder = nn.Sequential(
            DownsamplingUnit(in_channels, 32, act="relu"), *[SSnbt(32, 1, drop) for _ in range(3)],
            DownsamplingUnit(32, 64, act="relu"), *[SSnbt(64, 1, drop) for _ in range(2)],
            DownsamplingUnit(64, 128, act="relu"), *[SSnbt(128, d, drop) for d in (1, 2, 5, 9, 2, 5, 9, 17)])
        self.decoder = APN(128, num_classes)

    def body(self, x):
        return self.decoder(self.encoder(x))
