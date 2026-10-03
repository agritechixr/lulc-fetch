"""FDDWNet (Liu et al., 2019/2020, "FDDWNet: A Lightweight Convolutional Neural Network for Real-time Semantic
Segmentation"), about 0.8 M parameters.

Built from the Extremely Efficient Residual Module (EERM): a factorized depthwise 3×1 / 1×3 pair and a pointwise 1×1
convolution, then the same with dilation, with a residual. Encoder: downsampling units to 16 and 64 channels, 5 EERM
(64), a downsampling unit to 128 and 16 EERM: dilations 1, 2, 5, 9 twice, then 2, 5, 9, 17 twice. Decoder:
upsample to 1/4, add the 1/4 encoder features, 2 EERM; upsample to 1/2, add the 1/2 features, 2 EERM; a transposed
convolution to the classes at full size."""

from __future__ import annotations

import torch.nn as nn
import torch.nn.functional as F

from .common import DownsamplingUnit, SegModel, resize_to


class EERM(nn.Module):
    def __init__(self, c, d=1, drop=0.0):
        super().__init__()
        self.f = nn.Sequential(
            nn.Conv2d(c, c, (3, 1), padding=(1, 0), groups=c, bias=False), nn.Conv2d(c, c, (1, 3), padding=(0, 1), groups=c, bias=False),
            nn.Conv2d(c, c, 1, bias=False), nn.BatchNorm2d(c), nn.ReLU(inplace=True),
            nn.Conv2d(c, c, (3, 1), padding=(d, 0), dilation=(d, 1), groups=c, bias=False),
            nn.Conv2d(c, c, (1, 3), padding=(0, d), dilation=(1, d), groups=c, bias=False),
            nn.BatchNorm2d(c), nn.ReLU(inplace=True),
            nn.Conv2d(c, c, 1, bias=False), nn.BatchNorm2d(c), nn.Dropout2d(drop))

    def forward(self, x):
        return F.relu(x + self.f(x))


class UpBlock(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.up = nn.Sequential(nn.ConvTranspose2d(cin, cout, 3, 2, 1, output_padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True))

    def forward(self, x, skip):
        return resize_to(self.up(x), skip) + skip


class FDDWNet(SegModel):
    stride = 8

    def __init__(self, in_channels: int = 3, num_classes: int = 2, drop: float = 0.0):
        super().__init__(in_channels, num_classes)
        self.down1 = DownsamplingUnit(in_channels, 16, act="relu")
        self.down2 = DownsamplingUnit(16, 64, act="relu")
        self.enc2 = nn.Sequential(*[EERM(64, 1, drop) for _ in range(5)])
        self.down3 = DownsamplingUnit(64, 128, act="relu")
        self.enc3 = nn.Sequential(*[EERM(128, d, drop) for d in (1, 2, 5, 9) * 2 + (2, 5, 9, 17) * 2])
        self.up2 = UpBlock(128, 64)
        self.dec2 = nn.Sequential(EERM(64), EERM(64))
        self.up1 = UpBlock(64, 16)
        self.dec1 = nn.Sequential(EERM(16), EERM(16))
        self.head = nn.ConvTranspose2d(16, num_classes, 2, 2)

    def body(self, x):
        x1 = self.down1(x)              # 1/2, 16
        x2 = self.enc2(self.down2(x1))  # 1/4, 64
        x3 = self.enc3(self.down3(x2))  # 1/8, 128
        y = self.dec2(self.up2(x3, x2))
        y = self.dec1(self.up1(y, x1))
        return self.head(y)
