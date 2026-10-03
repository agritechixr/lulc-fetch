"""CGNet (Wu et al., 2018/2020, "CGNet: A Light-weight Context Guided Network for Semantic Segmentation"), the M=3, N=21
configuration (about 0.5 M parameters).

Stage 1: three 3×3 convolutions (32 channels, the first stride 2). Stages 2 and 3: a downsampling Context Guided (CG)
block then M−1 = 2 (dilation 2) and N−1 = 20 (dilation 4) CG blocks. A CG block joins a local feature (3×3 depthwise)
and its surrounding context (dilated 3×3 depthwise), refines them with a global-context channel attention, and adds a
residual. The downsampled input is injected into stages 2 and 3 (so any number of bands is used directly), and a 1×1
classifier on the concatenated stage-3 features is upsampled to full size."""

from __future__ import annotations

import torch
import torch.nn as nn

from .common import BNAct, ConvBNAct, SegModel, resize_to


class GlobalContext(nn.Module):
    def __init__(self, c, r=16):
        super().__init__()
        m = max(4, c // r)
        self.fc = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Conv2d(c, m, 1), nn.ReLU(inplace=True), nn.Conv2d(m, c, 1), nn.Sigmoid())

    def forward(self, x):
        return x * self.fc(x)


class CGBlock(nn.Module):
    def __init__(self, cin, cout, d=2, down=False, r=16):
        super().__init__()
        self.down = down
        n = cout if down else cout // 2
        self.reduce = ConvBNAct(cin, n, 3, 2) if down else ConvBNAct(cin, n, 1)
        self.loc = nn.Conv2d(n, n, 3, padding=1, groups=n, bias=False)
        self.sur = nn.Conv2d(n, n, 3, padding=d, dilation=d, groups=n, bias=False)
        self.joint = BNAct(2 * n)
        self.fuse = nn.Conv2d(2 * n, cout, 1, bias=False) if down else nn.Identity()
        self.glo = GlobalContext(cout, r)

    def forward(self, x):
        y = self.reduce(x)
        y = self.fuse(self.joint(torch.cat([self.loc(y), self.sur(y)], 1)))
        y = self.glo(y)
        return y if self.down else x + y   # global residual


class CGNet(SegModel):
    stride = 8

    def __init__(self, in_channels: int = 3, num_classes: int = 2, M: int = 3, N: int = 21, drop: float = 0.0):
        super().__init__(in_channels, num_classes)
        c = in_channels
        self.stage1 = nn.Sequential(ConvBNAct(c, 32, 3, 2), ConvBNAct(32, 32), ConvBNAct(32, 32))
        self.b1 = BNAct(32 + c)
        self.down2 = CGBlock(32 + c, 64, d=2, down=True, r=8)
        self.stage2 = nn.Sequential(*[CGBlock(64, 64, d=2, r=8) for _ in range(M - 1)])
        self.b2 = BNAct(128 + c)
        self.down3 = CGBlock(128 + c, 128, d=4, down=True, r=16)
        self.stage3 = nn.Sequential(*[CGBlock(128, 128, d=4, r=16) for _ in range(N - 1)])
        self.b3 = BNAct(256)
        self.classifier = nn.Sequential(nn.Dropout2d(drop), nn.Conv2d(256, num_classes, 1))

    def body(self, x):
        o1 = self.stage1(x)
        x1 = self.b1(torch.cat([o1, resize_to(x, o1, "avg")], 1))           # input injection, 1/2
        d2 = self.down2(x1)
        o2 = self.stage2(d2)
        x2 = self.b2(torch.cat([o2, d2, resize_to(x, o2, "avg")], 1))       # 1/4
        d3 = self.down3(x2)
        o3 = self.stage3(d3)
        x3 = self.b3(torch.cat([d3, o3], 1))                                 # 1/8
        return self.classifier(x3)
