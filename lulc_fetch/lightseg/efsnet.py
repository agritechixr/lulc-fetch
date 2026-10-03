"""EFSNet (Hu et al., 2020, "Efficient Fast Semantic Segmentation using Continuous Shuffle Dilated Convolutions"),
about 0.17 M parameters: the smallest model here.

An ENet-style initial block (stride-2 3×3 convolution beside a max pool of the input, 16 channels), then Continuous
Shuffle Dilated Convolution (CSDC) modules: a grouped 1×1 convolution, a channel shuffle, a depthwise dilated 3×3
convolution and a grouped 1×1 convolution, added to the input. Encoder: downsample to 64 channels with 4 CSDC, downsample
to 128 with 8 CSDC (dilations 2, 4, 8, 16 twice). Decoder: upsample to 1/4 and add the 1/4 features (2 CSDC), upsample to
1/2 (1 CSDC) and a transposed convolution to the classes at full size."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import ConvBNAct, DownsamplingUnit, SegModel, channel_shuffle, resize_to


class Initial(nn.Module):
    def __init__(self, cin, cout=16):
        super().__init__()
        self.conv = nn.Conv2d(cin, cout - 3, 3, 2, 1, bias=False)
        self.proj = nn.Identity() if cin == 3 else nn.Conv2d(cin, 3, 1, bias=False)   # any number of bands
        self.bn, self.act = nn.BatchNorm2d(cout), nn.PReLU(cout)

    def forward(self, x):
        return self.act(self.bn(torch.cat([self.conv(x), F.max_pool2d(self.proj(x), 2)], 1)))


class CSDC(nn.Module):
    def __init__(self, c, d=1, groups=2, drop=0.0):
        super().__init__()
        m = c // 2
        self.g = groups
        self.reduce = ConvBNAct(c, m, 1, groups=groups)
        self.dw = ConvBNAct(m, m, 3, d=d, groups=m, act=None)
        self.expand = nn.Sequential(nn.Conv2d(m, c, 1, groups=groups, bias=False), nn.BatchNorm2d(c), nn.Dropout2d(drop))
        self.act = nn.PReLU(c)

    def forward(self, x):
        y = channel_shuffle(self.reduce(x), self.g)
        return self.act(x + self.expand(self.dw(y)))


class EFSNet(SegModel):
    stride = 8

    def __init__(self, in_channels: int = 3, num_classes: int = 2, drop: float = 0.0):
        super().__init__(in_channels, num_classes)
        self.initial = Initial(in_channels)
        self.stage2 = nn.Sequential(DownsamplingUnit(16, 64), *[CSDC(64, 1, drop=drop) for _ in range(4)])
        self.stage3 = nn.Sequential(DownsamplingUnit(64, 128), *[CSDC(128, d, drop=drop) for d in (2, 4, 8, 16) * 2])
        self.up2 = ConvBNAct(128, 64, 1)
        self.dec2 = nn.Sequential(CSDC(64), CSDC(64))
        self.up1 = ConvBNAct(64, 16, 1)
        self.dec1 = CSDC(16, groups=2)
        self.head = nn.ConvTranspose2d(16, num_classes, 2, 2)

    def body(self, x):
        x1 = self.initial(x)               # 1/2, 16
        x2 = self.stage2(x1)               # 1/4, 64
        x3 = self.stage3(x2)               # 1/8, 128
        y = self.dec2(resize_to(self.up2(x3), x2) + x2)
        y = self.dec1(resize_to(self.up1(y), x1) + x1)
        return self.head(y)
