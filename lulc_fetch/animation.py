"""Animated GIFs of map layers (View ▸ Compare ▸ Time slider ▸ Save) with only what the desktop app ships: frames are
decoded with GDAL (rasterio), composed and labelled with numpy (a 5 × 7 pixel font), and written by a small GIF89a
encoder (one 256-colour palette, LZW, looping). MP4 needs OpenCV, which comes with the deep-learning add-on."""

from __future__ import annotations

import base64
from pathlib import Path

import numpy as np

# 5 × 7 font: each glyph is 7 rows of 5 bits (lower-case letters are drawn as capitals)
_F = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110", "C": "01110 10001 10000 10000 10000 10001 01110",
    "D": "11110 10001 10001 10001 10001 10001 11110", "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001", "I": "01110 00100 00100 00100 00100 00100 01110",
    "J": "00111 00010 00010 00010 00010 10010 01100", "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001", "O": "01110 10001 10001 10001 10001 10001 01110",
    "P": "11110 10001 10001 11110 10000 10000 10000", "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100", "U": "10001 10001 10001 10001 10001 10001 01110",
    "V": "10001 10001 10001 10001 10001 01010 00100", "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110", "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11111 00010 00100 00010 00001 10001 01110", "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000", "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100", "-": "00000 00000 00000 11111 00000 00000 00000", "_": "00000 00000 00000 00000 00000 00000 11111",
    ".": "00000 00000 00000 00000 00000 01100 01100", ":": "00000 01100 01100 00000 01100 01100 00000", "/": "00001 00010 00010 00100 01000 01000 10000",
    "·": "00000 00000 00000 01100 01100 00000 00000", "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "%": "11001 11010 00010 00100 01000 01011 10011", "+": "00000 00100 00100 11111 00100 00100 00000", " ": "00000 00000 00000 00000 00000 00000 00000",
}
_GLYPHS = {k: np.array([[c == "1" for c in row] for row in v.split()], bool) for k, v in _F.items()}


def text_mask(text: str, scale: int) -> np.ndarray:
    """A bool image of the text (unknown characters as '?'-less blanks)."""
    cols = []
    for ch in text:
        g = _GLYPHS.get(ch.upper(), _GLYPHS[" "])
        cols += [g, np.zeros((7, 1), bool)]
    m = np.hstack(cols) if cols else np.zeros((7, 1), bool)
    return np.kron(m, np.ones((scale, scale), bool))


def decode_png(data_url: str) -> np.ndarray:
    """A PNG data URL (or base64) as an RGBA uint8 array, decoded by GDAL."""
    from rasterio.io import MemoryFile
    raw = base64.b64decode(data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url)
    with MemoryFile(raw) as mf, mf.open() as s:
        a = s.read()
    if a.shape[0] == 1:
        a = np.repeat(a, 3, 0)
    if a.shape[0] == 3:
        a = np.concatenate([a, np.full((1, *a.shape[1:]), 255, a.dtype)])
    if a.shape[0] == 2:   # grey + alpha
        a = np.concatenate([np.repeat(a[:1], 3, 0), a[1:]])
    return np.moveaxis(a[:4], 0, -1).astype("uint8")


def resize(a: np.ndarray, h: int, w: int) -> np.ndarray:
    """Nearest-neighbour resize of an H × W × C image (map pictures keep their crisp classes)."""
    ri = np.minimum((np.arange(h) + 0.5) * a.shape[0] / h, a.shape[0] - 1).astype(int)
    ci = np.minimum((np.arange(w) + 0.5) * a.shape[1] / w, a.shape[1] - 1).astype(int)
    return a[ri][:, ci]


def compose(frames: list[dict], width: int) -> list[np.ndarray]:
    """Each frame {image, bounds [[s, w], [n, e]], label} placed by its bounds on a white canvas, labelled: RGB uint8."""
    import math
    bs = [(min(b[0][0], b[1][0]), min(b[0][1], b[1][1]), max(b[0][0], b[1][0]), max(b[0][1], b[1][1])) for b in (f["bounds"] for f in frames)]
    s, w, n, e = min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)
    kx = math.cos(math.radians((s + n) / 2))
    W = width - width % 2
    H = max(2, int(W * (n - s) / max((e - w) * kx, 1e-12)))
    H -= H % 2
    if H > 4000:
        raise ValueError("The layers' area is too tall for an animation → zoom to a smaller area")
    scale = max(2, W // 300)
    out = []
    for f, (fs, fw, fn, fe) in zip(frames, bs):
        im = decode_png(f["image"]).astype("float32")
        x0, x1 = int((fw - w) / (e - w) * W), int((fe - w) / (e - w) * W)
        y0, y1 = int((n - fn) / (n - s) * H), int((n - fs) / (n - s) * H)
        canvas = np.full((H, W, 3), 255.0, "float32")
        ph, pw = max(1, y1 - y0), max(1, x1 - x0)
        tile = resize(im, ph, pw)
        y0c, x0c = max(0, y0), max(0, x0)
        y1c, x1c = min(H, y0 + ph), min(W, x0 + pw)
        t = tile[y0c - y0:y1c - y0, x0c - x0:x1c - x0]
        al = t[..., 3:4] / 255
        canvas[y0c:y1c, x0c:x1c] = canvas[y0c:y1c, x0c:x1c] * (1 - al) + t[..., :3] * al
        if f.get("label"):
            m = text_mask(f["label"], scale)
            m = m[:, : max(1, W - 4 * scale - 8)]
            th, tw = m.shape
            ty, tx = H - th - 3 * scale - 8, 8
            if ty > 0:
                box = canvas[ty - 2 * scale:ty + th + 2 * scale, tx - 2 * scale + 2 * scale:tx + tw + 4 * scale]
                box *= 0.35                                                      # a dark box behind the text
                region = canvas[ty:ty + th, tx + 2 * scale:tx + 2 * scale + tw]
                region[m[:region.shape[0], :region.shape[1]]] = 255
        out.append(canvas.clip(0, 255).astype("uint8"))
    return out


# ------------------------------------------------------------------ GIF89a
def _palette() -> np.ndarray:
    """6 × 7 × 6 colour cube (252) + 4 greys: 256 colours, fine for maps."""
    r, g, b = np.meshgrid(np.linspace(0, 255, 6), np.linspace(0, 255, 7), np.linspace(0, 255, 6), indexing="ij")
    cube = np.stack([r.ravel(), g.ravel(), b.ravel()], 1)
    greys = np.array([[64, 64, 64], [128, 128, 128], [192, 192, 192], [238, 238, 238]], float)
    return np.vstack([cube, greys]).round().astype("uint8")


PALETTE = _palette()


def quantise(rgb: np.ndarray) -> np.ndarray:
    """Palette indices, ordered-dithered (Bayer 4 × 4) onto the colour cube; near-greys onto the cube or the greys."""
    bayer = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16 - 0.5
    h, w, _ = rgb.shape
    d = np.tile(bayer, (h // 4 + 1, w // 4 + 1))[:h, :w, None]
    x = rgb.astype("float32")
    steps = np.array([5, 6, 5], "float32")
    q = np.clip(np.floor(x / 255 * steps + 0.5 + d * 0.8), 0, steps).astype(int)
    idx = q[..., 0] * 42 + q[..., 1] * 6 + q[..., 2]
    # neutral pixels: the nearest of cube greys and extra greys (less banding on hillshades)
    grey = (x.max(-1) - x.min(-1)) < 10
    if grey.any():
        lv = x[..., 0][grey]
        cand_v = np.array([0, 64, 128, 192, 238, 255], float)
        cand_i = np.array([0, 252, 253, 254, 255, 5 * 42 + 6 * 6 + 5])
        idx[grey] = cand_i[np.abs(lv[:, None] - cand_v[None]).argmin(1)]
    return idx.astype("uint8")


def _lzw(data: bytes, min_size: int = 8) -> bytes:
    clear, eoi = 1 << min_size, (1 << min_size) + 1
    size = min_size + 1
    table = {bytes([i]): i for i in range(clear)}
    nxt = eoi + 1
    out, acc, nbits = bytearray(), 0, 0

    def put(code):
        nonlocal acc, nbits
        acc |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    put(clear)
    w = b""
    for b in data:
        wc = w + bytes([b])
        if wc in table:
            w = wc
            continue
        put(table[w])
        if nxt < 4096:
            table[wc] = nxt
            nxt += 1
            if nxt > (1 << size) and size < 12:
                size += 1
        else:   # the table is full: start again
            put(clear)
            table = {bytes([i]): i for i in range(clear)}
            nxt, size = eoi + 1, min_size + 1
        w = bytes([b])
    if w:
        put(table[w])
    put(eoi)
    if nbits:
        out.append(acc & 0xFF)
    return bytes(out)


def write_gif(frames: list[np.ndarray], path: Path, fps: float = 1.0) -> None:
    h, w = frames[0].shape[:2]
    delay = max(2, int(round(100 / fps)))
    buf = bytearray(b"GIF89a")
    buf += w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([0xF7, 0, 0])
    buf += PALETTE.tobytes()
    buf += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"                    # loop forever
    for f in frames:
        buf += b"\x21\xF9\x04\x04" + delay.to_bytes(2, "little") + b"\x00\x00"   # delay, keep the frame
        buf += b"\x2C\x00\x00\x00\x00" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + b"\x00"
        data = _lzw(quantise(f).tobytes())
        buf += b"\x08"
        for i in range(0, len(data), 255):
            chunk = data[i:i + 255]
            buf += bytes([len(chunk)]) + chunk
        buf += b"\x00"
    buf += b"\x3B"
    Path(path).write_bytes(bytes(buf))


def write_mp4(frames: list[np.ndarray], path: Path, fps: float = 1.0) -> None:
    try:
        import cv2
    except ImportError as e:
        raise RuntimeError("MP4 video needs OpenCV, which comes with the deep-learning add-on → save a GIF instead") from e
    h, w = frames[0].shape[:2]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not vw.isOpened():
        raise RuntimeError("This computer cannot write MP4 video → save a GIF instead")
    for f in frames:
        vw.write(np.ascontiguousarray(f[..., ::-1]))
    vw.release()
