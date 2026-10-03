"""ADSCNet (Wang et al., 2019, "ADSCNet: Asymmetric Depthwise Separable Convolution for Semantic Segmentation in
Real-time"), about 0.5 M parameters.

Built from Asymmetric Depthwise Separable Convolution (ADSC) units: a 1×1 convolution, a depthwise 3×1 and a depthwise
1×3 convolution (optionally dilated) and a 1×1 convolution, with a residual. Encoder: 3×3 stride-2 convolution (32),
ADSC; stride-2 to 64, two ADSC; stride-2 to 128, then a Dense Dilated Convolution Connections (DDCC) module — four ADSC
units with dilations 3, 5, 9 and 13, each fed the concatenation of the input and all previous outputs (reduced
by 1×1 convolutions). Decoder: upsample to 1/4 and add the 1/4 features (ADSC), to 1/2 and add the 1/2 features (ADSC),
and a transposed convolution to the classes at full size."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import ConvBNAct, SegModel, resize_to


class ADSC(nn.Module):
    def __init__(self, c, d=1, drop=0.0, residual=True):
        super().__init__()
        self.residual = residual
        self.f = nn.Sequential(
            ConvBNAct(c, c, 1, act="relu"),
            nn.Conv2d(c, c, (3, 1), padding=(d, 0), dilation=(d, 1), groups=c, bias=False),
            ConvBNAct(c, c, (1, 3), d=(1, d), groups=c, act="relu"),
            nn.Conv2d(c, c, 1, bias=False), nn.BatchNorm2d(c), nn.Dropout2d(drop))

    def forward(self, x):
        y = self.f(x)
        return F.relu(x + y) if self.residual else F.relu(y)


class DDCC(nn.Module):
    """Dense dilated connections: unit k sees the input and every earlier unit's output (1×1-reduced to c)."""

    def __init__(self, c, dilations=(3, 5, 9, 13), drop=0.0):
        super().__init__()
        self.reduce = nn.ModuleList([ConvBNAct(c * (i + 1), c, 1, act="relu") for i in range(len(dilations))])
        self.units = nn.ModuleList([ADSC(c, d, drop) for d in dilations])
        self.out = ConvBNAct(c * (len(dilations) + 1), c, 1, act="relu")

    def forward(self, x):
        feats = [x]
        for red, unit in zip(self.reduce, self.units):
            feats.append(unit(red(torch.cat(feats, 1))))
        return self.out(torch.cat(feats, 1))


class ADSCNet(SegModel):
    stride = 8

    def __init__(self, in_channels: int = 3, num_classes: int = 2, drop: float = 0.0):
        super().__init__(in_channels, num_classes)
        self.stage1 = nn.Sequential(ConvBNAct(in_channels, 32, 3, 2, act="relu"), ADSC(32))
        self.stage2 = nn.Sequential(ConvBNAct(32, 64, 3, 2, act="relu"), ADSC(64), ADSC(64))
        self.stage3 = nn.Sequential(ConvBNAct(64, 128, 3, 2, act="relu"), DDCC(128, drop=drop))
        self.up2 = ConvBNAct(128, 64, 1, act="relu")
        self.dec2 = ADSC(64)
        self.up1 = ConvBNAct(64, 32, 1, act="relu")
        self.dec1 = ADSC(32)
        self.head = nn.ConvTranspose2d(32, num_classes, 2, 2)

    def body(self, x):
        x1 = self.stage1(x)
        x2 = self.stage2(x1)
        x3 = self.stage3(x2)
        y = self.dec2(resize_to(self.up2(x3), x2) + x2)
        y = self.dec1(resize_to(self.up1(y), x1) + x1)
        return self.head(y)
