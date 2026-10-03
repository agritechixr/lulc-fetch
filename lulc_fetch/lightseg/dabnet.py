"""DABNet (Li et al., 2019, "DABNet: Depth-wise Asymmetric Bottleneck for Real-time Semantic Segmentation"), about 0.76 M
parameters.

Three 3×3 convolutions (32 channels, the first stride 2) with the downsampled input concatenated; a downsampling block
to 64 channels and 3 Depth-wise Asymmetric Bottleneck (DAB) modules (dilation 2); a downsampling block to 128 channels
and 6 DAB modules (dilations 4, 4, 8, 8, 16, 16). Each stage's output is concatenated with its first features and the
downsampled input; a 1×1 classifier at 1/8 resolution is upsampled to full size. A DAB module halves the channels
(3×3), runs two depthwise branches of 3×1 + 1×3 convolutions (one plain, one dilated), adds them, restores the channels
(1×1) and adds the input."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import BNAct, ConvBNAct, SegModel, resize_to


class DAB(nn.Module):
    def __init__(self, c, d=1):
        super().__init__()
        h = c // 2
        self.pre = BNAct(c)
        self.reduce = ConvBNAct(c, h, 3)
        self.b1 = nn.Sequential(ConvBNAct(h, h, (3, 1), groups=h), ConvBNAct(h, h, (1, 3), groups=h))
        self.b2 = nn.Sequential(ConvBNAct(h, h, (3, 1), d=(d, 1), groups=h), ConvBNAct(h, h, (1, 3), d=(1, d), groups=h))
        self.post = BNAct(h)
        self.expand = nn.Conv2d(h, c, 1, bias=False)

    def forward(self, x):
        y = self.reduce(self.pre(x))
        y = self.post(self.b1(y) + self.b2(y))
        return self.expand(y) + x


class Downsample(nn.Module):
    """3×3 stride-2 convolution, concatenated with a max pool when the channels grow."""

    def __init__(self, cin, cout):
        super().__init__()
        self.pool = cin < cout
        self.conv = nn.Conv2d(cin, cout - cin if self.pool else cout, 3, 2, 1, bias=False)
        self.bn_act = BNAct(cout)

    def forward(self, x):
        y = self.conv(x)
        if self.pool:
            y = torch.cat([y, F.max_pool2d(x, 2, ceil_mode=True)[..., :y.shape[-2], :y.shape[-1]]], 1)
        return self.bn_act(y)


class DABNet(SegModel):
    stride = 8

    def __init__(self, in_channels: int = 3, num_classes: int = 2, blocks1: int = 3, blocks2: int = 6):
        super().__init__(in_channels, num_classes)
        c = in_channels
        self.init = nn.Sequential(ConvBNAct(c, 32, 3, 2), ConvBNAct(32, 32), ConvBNAct(32, 32))
        self.bn1 = BNAct(32 + c)
        self.down2 = Downsample(32 + c, 64)
        self.stage2 = nn.Sequential(*[DAB(64, 2) for _ in range(blocks1)])
        self.bn2 = BNAct(128 + c)
        self.down3 = Downsample(128 + c, 128)
        dil = [4, 4, 8, 8, 16, 16][:blocks2] + [16] * max(0, blocks2 - 6)
        self.stage3 = nn.Sequential(*[DAB(128, d) for d in dil])
        self.bn3 = BNAct(256 + c)
        self.classifier = nn.Conv2d(256 + c, num_classes, 1)

    def body(self, x):
        o1 = self.init(x)
        x1 = self.bn1(torch.cat([o1, resize_to(x, o1, "avg")], 1))
        d2 = self.down2(x1)
        x2 = self.bn2(torch.cat([self.stage2(d2), d2, resize_to(x, d2, "avg")], 1))
        d3 = self.down3(x2)
        x3 = self.bn3(torch.cat([self.stage3(d3), d3, resize_to(x, d3, "avg")], 1))
        return self.classifier(x3)
