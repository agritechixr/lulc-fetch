"""LEANet (Zhang et al., 2021, "LEANet: Lightweight Efficient Attention Network for Real-Time Semantic Segmentation"),
about 0.74 M parameters.

Encoder: downsampling units to 32, 64 and 128 channels with 3, 2 and 8 Split-Channel Attention (SCA) units (the last
eight dilated 1, 2, 5, 9, 2, 5, 9, 17). An SCA unit splits the channels in two: one half goes through factorized
3×1 / 1×3 convolutions, the other through the same dilated; they are joined by a 1×1 convolution, re-weighted by channel
attention (squeeze-excitation) and spatial attention, added to the input and channel-shuffled. Decoder: an attention
pyramid on the 1/8 features (as in LEDNet) fused with the 1/4 features, then upsampled to full size."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import SE, ConvBNAct, DownsamplingUnit, SegModel, channel_shuffle, resize_to


class SpatialAttention(nn.Module):
    def __init__(self, k=7):
        super().__init__()
        self.conv = nn.Conv2d(2, 1, k, padding=k // 2, bias=False)

    def forward(self, x):
        return x * torch.sigmoid(self.conv(torch.cat([x.mean(1, keepdim=True), x.amax(1, keepdim=True)], 1)))


class SCA(nn.Module):
    def __init__(self, c, d=1, drop=0.0):
        super().__init__()
        h = c // 2
        self.local = nn.Sequential(ConvBNAct(h, h, (3, 1), act="relu"), ConvBNAct(h, h, (1, 3), act="relu"))
        self.context = nn.Sequential(ConvBNAct(h, h, (3, 1), d=(d, 1), act="relu"), ConvBNAct(h, h, (1, 3), d=(1, d), act="relu"))
        self.fuse = nn.Sequential(nn.Conv2d(c, c, 1, bias=False), nn.BatchNorm2d(c))
        self.ca, self.sa = SE(c, 8), SpatialAttention()
        self.drop = nn.Dropout2d(drop)

    def forward(self, x):
        a, b = x.chunk(2, 1)
        y = self.fuse(torch.cat([self.local(a), self.context(b)], 1))
        y = self.drop(self.sa(self.ca(y)))
        return channel_shuffle(F.relu(x + y), 2)


class LEANet(SegModel):
    stride = 64

    def __init__(self, in_channels: int = 3, num_classes: int = 2, drop: float = 0.0):
        super().__init__(in_channels, num_classes)
        self.stage1 = nn.Sequential(DownsamplingUnit(in_channels, 32, act="relu"), *[SCA(32, 1, drop) for _ in range(3)])
        self.stage2 = nn.Sequential(DownsamplingUnit(32, 64, act="relu"), *[SCA(64, 1, drop) for _ in range(2)])
        self.stage3 = nn.Sequential(DownsamplingUnit(64, 128, act="relu"), *[SCA(128, d, drop) for d in (1, 2, 5, 9, 2, 5, 9, 17)])
        # attention pyramid (single attention channel), global context, and the 1/4 skip
        self.p1, self.p2 = ConvBNAct(128, 1, 7, 2, act="relu"), ConvBNAct(1, 1, 5, 2, act="relu")
        self.p3 = nn.Sequential(ConvBNAct(1, 1, 3, 2, act="relu"), ConvBNAct(1, 1, 3, act="relu"))
        self.c2, self.c1 = ConvBNAct(1, 1, 5, act="relu"), ConvBNAct(1, 1, 7, act="relu")
        self.mid = ConvBNAct(128, num_classes, 1, act="relu")
        self.glob = nn.Sequential(nn.AdaptiveAvgPool2d(1), ConvBNAct(128, num_classes, 1, act="relu"))
        self.skip = ConvBNAct(64, num_classes, 1, act="relu")

    def body(self, x):
        x2 = self.stage2(self.stage1(x))   # 1/4
        x3 = self.stage3(x2)               # 1/8
        p1 = self.p1(x3)
        p2 = self.p2(p1)
        a = resize_to(self.p3(p2), p2) + self.c2(p2)
        a = resize_to(resize_to(a, p1) + self.c1(p1), x3)
        y = self.mid(x3) * a + resize_to(self.glob(x3), x3)
        return resize_to(y, x2) + self.skip(x2)
