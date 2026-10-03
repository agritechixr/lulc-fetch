"""ENet (Paszke et al., 2016, "ENet: A Deep Neural Network Architecture for Real-Time Semantic Segmentation").

Initial block (a stride-2 3×3 convolution beside a max pool of the input), then bottleneck stages: 1 (downsample to 64,
4 regular), 2 and 3 (downsample to 128 in stage 2; regular, dilated 2/4/8/16 and asymmetric 5×1·1×5 bottlenecks), and a
decoder of max-unpooling bottlenecks (4: 128→64, 5: 64→16) and a transposed convolution to full size. About 0.36 M
parameters. For any number of input bands, the pooled-input branch of the initial block is projected to 3 channels
(the paper's RGB case is unchanged)."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import SegModel


class Initial(nn.Module):
    def __init__(self, cin, cout=16):
        super().__init__()
        self.conv = nn.Conv2d(cin, cout - 3, 3, 2, 1, bias=False)
        self.proj = nn.Identity() if cin == 3 else nn.Conv2d(cin, 3, 1, bias=False)   # any number of bands
        self.bn, self.act = nn.BatchNorm2d(cout), nn.PReLU(cout)

    def forward(self, x):
        return self.act(self.bn(torch.cat([self.conv(x), F.max_pool2d(self.proj(x), 2)], 1)))


class Bottleneck(nn.Module):
    """Regular / dilated / asymmetric bottleneck: 1×1 reduce → main conv → 1×1 expand, dropout, residual."""

    def __init__(self, c, kind="regular", d=1, drop=0.1, r=4):
        super().__init__()
        m = c // r
        if kind == "asym":
            main = nn.Sequential(nn.Conv2d(m, m, (5, 1), padding=(2, 0), bias=False), nn.Conv2d(m, m, (1, 5), padding=(0, 2), bias=False))
        else:
            main = nn.Conv2d(m, m, 3, padding=d, dilation=d, bias=False)
        self.branch = nn.Sequential(nn.Conv2d(c, m, 1, bias=False), nn.BatchNorm2d(m), nn.PReLU(m), main, nn.BatchNorm2d(m), nn.PReLU(m),
                                    nn.Conv2d(m, c, 1, bias=False), nn.BatchNorm2d(c), nn.Dropout2d(drop))
        self.act = nn.PReLU(c)

    def forward(self, x):
        return self.act(x + self.branch(x))


class Down(nn.Module):
    """Downsampling bottleneck: max pool (indices kept for unpooling) + zero-padded channels, beside a 2×2 stride-2 branch."""

    def __init__(self, cin, cout, drop=0.01, r=4):
        super().__init__()
        m = cin // r
        self.branch = nn.Sequential(nn.Conv2d(cin, m, 2, 2, bias=False), nn.BatchNorm2d(m), nn.PReLU(m), nn.Conv2d(m, m, 3, padding=1, bias=False),
                                    nn.BatchNorm2d(m), nn.PReLU(m), nn.Conv2d(m, cout, 1, bias=False), nn.BatchNorm2d(cout), nn.Dropout2d(drop))
        self.extra = cout - cin
        self.act = nn.PReLU(cout)

    def forward(self, x):
        main, idx = F.max_pool2d(x, 2, return_indices=True)
        if self.extra:
            main = torch.cat([main, main.new_zeros(main.shape[0], self.extra, *main.shape[2:])], 1)
        return self.act(main + self.branch(x)), idx


class Up(nn.Module):
    """Upsampling bottleneck: 1×1 conv + max unpooling (with the encoder's indices) beside a transposed-conv branch."""

    def __init__(self, cin, cout, drop=0.1, r=4):
        super().__init__()
        m = cin // r
        self.main = nn.Sequential(nn.Conv2d(cin, cout, 1, bias=False), nn.BatchNorm2d(cout))
        self.branch = nn.Sequential(nn.Conv2d(cin, m, 1, bias=False), nn.BatchNorm2d(m), nn.PReLU(m),
                                    nn.ConvTranspose2d(m, m, 2, 2, bias=False), nn.BatchNorm2d(m), nn.PReLU(m),
                                    nn.Conv2d(m, cout, 1, bias=False), nn.BatchNorm2d(cout), nn.Dropout2d(drop))
        self.act = nn.PReLU(cout)

    def forward(self, x, idx, size):
        main = F.max_unpool2d(self.main(x), idx, 2, output_size=size)
        return self.act(main + self.branch(x))


class ENet(SegModel):
    stride = 8

    def __init__(self, in_channels: int = 3, num_classes: int = 2):
        super().__init__(in_channels, num_classes)
        self.initial = Initial(in_channels)
        self.down1 = Down(16, 64)
        self.stage1 = nn.Sequential(*[Bottleneck(64, drop=0.01) for _ in range(4)])
        self.down2 = Down(64, 128)
        seq = [("regular", 1), ("dilated", 2), ("asym", 1), ("dilated", 4), ("regular", 1), ("dilated", 8), ("asym", 1), ("dilated", 16)]
        self.stage2 = nn.Sequential(*[Bottleneck(128, k, d) for k, d in seq])
        self.stage3 = nn.Sequential(*[Bottleneck(128, k, d) for k, d in seq])
        self.up4 = Up(128, 64)
        self.stage4 = nn.Sequential(Bottleneck(64), Bottleneck(64))
        self.up5 = Up(64, 16)
        self.stage5 = Bottleneck(16)
        self.full = nn.ConvTranspose2d(16, num_classes, 2, 2)

    def body(self, x):
        x0 = self.initial(x)
        x1, i1 = self.down1(x0)
        x1 = self.stage1(x1)
        x2, i2 = self.down2(x1)
        x2 = self.stage3(self.stage2(x2))
        y = self.stage4(self.up4(x2, i2, x1.shape[-2:]))
        y = self.stage5(self.up5(y, i1, x0.shape[-2:]))
        return self.full(y)
