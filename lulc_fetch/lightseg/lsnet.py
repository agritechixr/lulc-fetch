"""LSNet: a lightweight segmentation network built on 1D convolutions, about 0.62 M parameters.

Several papers use this name; this follows the common design the table describes: every 3×3 convolution is replaced by
a 1D pair (3×1 then 1×3), which needs a third fewer weights. A Light 1D block (L1D) splits the channels in two: one half
goes through a 3×1 / 1×3 pair, the other through a dilated pair; they are joined by a 1×1 convolution and added to the
input. Encoder: downsampling units to 32, 64 and 128 channels with 2, 3 and 7 L1D blocks (dilations 1, 2, 4, 8, 16, 4, 8
at 1/8). Decoder: upsample to 1/4 and fuse with the 1/4 features (1D blocks), then to 1/2 and fuse with the 1/2
features, and a 1×1 classifier, upsampled to full size."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import ConvBNAct, DownsamplingUnit, SegModel, resize_to


class L1D(nn.Module):
    def __init__(self, c, d=1, drop=0.0):
        super().__init__()
        h = c // 2
        self.a = nn.Sequential(ConvBNAct(h, h, (3, 1), act="relu"), ConvBNAct(h, h, (1, 3), act="relu"))
        self.b = nn.Sequential(ConvBNAct(h, h, (3, 1), d=(d, 1), act="relu"), ConvBNAct(h, h, (1, 3), d=(1, d), act="relu"))
        self.fuse = nn.Sequential(nn.Conv2d(c, c, 1, bias=False), nn.BatchNorm2d(c), nn.Dropout2d(drop))

    def forward(self, x):
        a, b = x.chunk(2, 1)
        return F.relu(x + self.fuse(torch.cat([self.a(a), self.b(b)], 1)))


class LSNet(SegModel):
    stride = 8

    def __init__(self, in_channels: int = 3, num_classes: int = 2, drop: float = 0.0):
        super().__init__(in_channels, num_classes)
        self.stage1 = nn.Sequential(DownsamplingUnit(in_channels, 32, act="relu"), L1D(32), L1D(32))
        self.stage2 = nn.Sequential(DownsamplingUnit(32, 64, act="relu"), *[L1D(64, 1, drop) for _ in range(3)])
        self.stage3 = nn.Sequential(DownsamplingUnit(64, 128, act="relu"), *[L1D(128, d, drop) for d in (1, 2, 4, 8, 16, 4, 8)])
        self.up2 = ConvBNAct(128, 64, 1, act="relu")
        self.dec2 = L1D(64)
        self.up1 = ConvBNAct(64, 32, 1, act="relu")
        self.dec1 = L1D(32)
        self.classifier = nn.Conv2d(32, num_classes, 1)

    def body(self, x):
        x1 = self.stage1(x)
        x2 = self.stage2(x1)
        x3 = self.stage3(x2)
        y = self.dec2(resize_to(self.up2(x3), x2) + x2)
        y = self.dec1(resize_to(self.up1(y), x1) + x1)
        return self.classifier(y)
