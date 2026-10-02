"""Draw the app icon (a green tile with a globe and a land-cover grid) and build LULC Fetch.icns."""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio

N = 1024
y, x = np.mgrid[0:N, 0:N].astype("float32") + 0.5


def rounded_rect(x0, y0, x1, y1, r):
    dx = np.maximum(np.maximum(x0 + r - x, x - (x1 - r)), 0)
    dy = np.maximum(np.maximum(y0 + r - y, y - (y1 - r)), 0)
    return np.clip(r + 1 - np.hypot(dx, dy), 0, 1) * ((x >= x0) & (x <= x1) & (y >= y0) & (y <= y1))


img = np.zeros((N, N, 4), "float32")
bg = rounded_rect(80, 80, 944, 944, 190)
t = (x + y) / (2 * N)
top, bot = np.array([0.15, 0.55, 0.40]), np.array([0.08, 0.36, 0.27])
img[..., :3] = (top * (1 - t[..., None]) + bot * t[..., None])
img[..., 3] = bg

def paint(mask, rgb, alpha=1.0):
    m = np.clip(mask, 0, 1)[..., None] * alpha
    img[..., :3] = img[..., :3] * (1 - m) + np.array(rgb) * m

cx, cy, R = 470, 470, 300
d = np.hypot(x - cx, y - cy)
globe = np.clip(R - d, 0, 1)
paint(globe, [0.93, 0.98, 0.95], 0.18)
line = lambda dist, w: np.clip(w - np.abs(dist), 0, 1)
paint(line(d - R, 14) , [1, 1, 1])                                   # outline
for k in (-0.55, 0, 0.55):                                             # latitudes
    yy = cy + k * R
    half = np.sqrt(max(R * R - (k * R) ** 2, 0))
    paint(line(y - yy, 9) * (np.abs(x - cx) <= half), [1, 1, 1], 0.9)
for k in (0.45, 1.0):                                                  # meridians (ellipses)
    ex = np.hypot((x - cx) / (k * R), (y - cy) / R)
    paint(line((ex - 1) * R, 9) * globe, [1, 1, 1], 0.9)
paint(line(x - cx, 9) * globe, [1, 1, 1], 0.9)
# land-cover tiles (lower right)
colors = [[0.12, 0.47, 0.71], [0.11, 0.47, 0.22], [0.90, 0.76, 0.16], [0.89, 0.10, 0.11]]
s0, g = 600, 132
for i, c in enumerate(colors):
    r0, c0 = divmod(i, 2)
    x0, y0 = s0 + c0 * (g + 18), s0 + r0 * (g + 18)
    paint(rounded_rect(x0 - 10, y0 - 10, x0 + g + 10, y0 + g + 10, 34), [1, 1, 1])
    paint(rounded_rect(x0, y0, x0 + g, y0 + g, 26), c)
img[..., 3] = np.maximum(img[..., 3] * bg, 0)

out = Path(sys.argv[1] if len(sys.argv) > 1 else "packaging/LULC Fetch.icns")
with tempfile.TemporaryDirectory() as tmp:
    png = Path(tmp) / "icon.png"
    arr = np.moveaxis((np.clip(img, 0, 1) * 255).astype("uint8"), 2, 0)
    with rasterio.open(png, "w", driver="PNG", width=N, height=N, count=4, dtype="uint8") as dst:
        dst.write(arr)
    iconset = Path(tmp) / "LULC.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)], check=True, capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    subprocess.run(["cp", str(png), str(out.with_suffix(".png"))], check=True)
    # Windows .ico: a directory of PNG images (16–256 px)
    import struct
    sizes, blobs = (16, 24, 32, 48, 64, 128, 256), []
    for px in sizes:
        f = Path(tmp) / f"ico_{px}.png"
        subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(f)], check=True, capture_output=True)
        blobs.append(f.read_bytes())
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset, entries = 6 + 16 * len(sizes), b""
    for px, blob in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", px % 256, px % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    out.with_suffix(".ico").write_bytes(head + entries + b"".join(blobs))
print(out)
