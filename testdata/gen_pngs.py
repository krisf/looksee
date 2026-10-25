#!/usr/bin/env python3
"""Generate the committed testdata PNGs (1.png .. 12.png).

Stdlib-only (zlib + struct), fully deterministic: same input -> byte-identical
PNGs. Sizes ramp 64..1024 px (square, RGB, bit depth 8, no interlace) so the
headless smokes have a range of image dimensions to fit/zoom against.

Run from the repo root:  /usr/bin/python3 testdata/gen_pngs.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

# 12 square sizes, strictly ascending, endpoints 64 and 1024.
SIZES = [64, 96, 128, 192, 256, 320, 384, 448, 512, 640, 768, 1024]

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _pixel(x: int, y: int, size: int, idx: int) -> tuple[int, int, int]:
    """Deterministic gradient + per-file tint + index number stripe pattern."""
    t = x / (size - 1) if size > 1 else 0.0
    s = y / (size - 1) if size > 1 else 0.0
    r = int(255 * t)
    g = int(255 * s)
    b = (32 * idx) & 0xFF  # each file gets its own blue tint so slides differ
    # Draw a simple white frame so edge fitting is visually obvious.
    m = max(1, size // 32)
    if x < m or y < m or x >= size - m or y >= size - m:
        r = g = b = 255
    return r, g, b


def make_png(size: int, idx: int) -> bytes:
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # filter type 0 (None) per scanline
        for x in range(size):
            rows.extend(_pixel(x, y, size, idx))
    raw = bytes(rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    # Default zlib level keeps the decompress->recompress round-trip exact.
    idat = zlib.compress(raw)
    return (
        _PNG_SIG
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


def main() -> None:
    out = Path(__file__).resolve().parent
    for i, size in enumerate(SIZES, start=1):
        path = out / f"{i}.png"
        path.write_bytes(make_png(size, i))
        print(f"wrote {path.name} {size}x{size} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
