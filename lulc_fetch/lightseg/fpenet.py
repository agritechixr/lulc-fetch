"""FPENet (Liu & Yin, 2019, "Feature Pyramid Encoding Network for Real-time Semantic Segmentation"), about 0.4 M
parameters.

Built from Feature Pyramid Encoding (FPE) blocks: a 1×1 convolution widens the channels (×t), the result is split into
four groups that go through depthwise 3×3 convolutions with dilations 1, 2, 4 and 8, each group also receiving the
previous one's output (a small pyramid), the groups are joined and a 1×1 convolution projects them back; squeeze-
excitation and a residual follow. Encoder: a 3×3 stride-2 convolution (16), FPE (16, t=1); FPE downsampling to 32 and 2
FPE (t=4); FPE downsampling to 64 and 9 FPE (t=4). Decoder: two Mutual Embedding Upsample (MEU) modules, where the
high-level features weight the low-level ones per channel and the low-level features weight the high-level ones per
pixel, then a 1×1 classifier, upsampled to full size."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import SE, ConvBNAct, SegModel, resize_to


class FPE(nn.Module):
    def __init__(self, cin, cout, t=4, stride=1, dilations=(1, 2, 4, 8)):
        super().__init__()
        w = cin * t
        w -= w % len(dilations)
        g = w // len(dilations)
        self.expand = ConvBNAct(cin, w, 1, s=stride, act="relu")
        self.dw = nn.ModuleList([ConvBNAct(g, g, 3, d=d, groups=g, act="relu") for d in dilations])
        self.project = nn.Sequential(nn.Conv2d(w, cout, 1, bias=False), nn.BatchNorm2d(cout))
        self.se = SE(cout, 4)
        self.short = None if (cin == cout and stride == 1) else nn.Sequential(nn.Conv2d(cin, cout, 1, stride, bias=False), nn.BatchNorm2d(cout))

    def forward(self, x):
        y = self.expand(x)
        parts, prev = [], None
        for chunk, conv in zip(y.chunk(len(self.dw), 1), self.dw):
            prev = conv(chunk if prev is None else chunk + prev)
            parts.append(prev)
        y = self.se(self.project(torch.cat(parts, 1)))
        return F.relu(y + (x if self.short is None else self.short(x)))


class MEU(nn.Module):
    """Mutual Embedding Upsample: channel attention from the high level, spatial attention from the low level."""

    def __init__(self, c_low, c_high, cout):
        super().__init__()
        self.low = ConvBNAct(c_low, cout, 1, act="relu")
        self.high = ConvBNAct(c_high, cout, 1, act="relu")
        self.ca = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Conv2d(cout, cout, 1), nn.Sigmoid())
        self.sa = nn.Sequential(nn.Conv2d(1, 1, 1), nn.Sigmoid())

    def forward(self, low, high):
        low, high = self.low(low), self.high(high)
        sa = self.sa(low.mean(1, keepdim=True))
        high = resize_to(high, low)
        return low * self.ca(high) + high * sa


class FPENet(SegModel):
    stride = 8

    def __init__(self, in_channels: int = 3, num_classes: int = 2):
        super().__init__(in_channels, num_classes)
        self.stem = ConvBNAct(in_channels, 16, 3, 2, act="relu")
        self.stage1 = FPE(16, 16, t=1)
        self.stage2 = nn.Sequential(FPE(16, 32, t=4, stride=2), FPE(32, 32), FPE(32, 32))
        self.stage3 = nn.Sequential(FPE(32, 64, t=4, stride=2), *[FPE(64, 64) for _ in range(9)])
        self.meu1 = MEU(32, 64, 64)
        self.meu2 = MEU(16, 64, 32)
        self.classifier = nn.Conv2d(32, num_classes, 1)

    def body(self, x):
        x1 = self.stage1(self.stem(x))     # 1/2, 16
        x2 = self.stage2(x1)               # 1/4, 32
        x3 = self.stage3(x2)               # 1/8, 64
        y = self.meu1(x2, x3)
        y = self.meu2(x1, y)
        return self.classifier(y)
