"""P0 gate: every committed testdata PNG is a valid, deterministic PNG.

Pure-stdlib validation (R3): walk the chunk stream, decompress IDAT with zlib,
check scanline geometry, and prove a decompress->recompress round-trip is
byte-identical (the generator uses default zlib settings, so it must be).
"""
from __future__ import annotations

import struct
import unittest
import zlib
from pathlib import Path

TESTDATA = Path(__file__).resolve().parent.parent / "testdata"
EXPECTED = {f"{i}.png" for i in range(1, 13)}
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def chunks(data: bytes):
    assert data.startswith(PNG_SIG), "missing PNG signature"
    off = len(PNG_SIG)
    out = []
    while off < len(data):
        (length,) = struct.unpack(">I", data[off : off + 4])
        tag = data[off + 4 : off + 8]
        payload = data[off + 8 : off + 8 + length]
        (crc,) = struct.unpack(">I", data[off + 8 + length : off + 12 + length])
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, f"bad CRC in {tag}"
        out.append((tag, payload))
        off += 12 + length
    return out


class PngGenerated(unittest.TestCase):
    def test_all_twelve_present(self) -> None:
        present = {p.name for p in TESTDATA.glob("*.png")}
        self.assertEqual(present, EXPECTED)

    def test_each_png_roundtrips(self) -> None:
        for i in range(1, 13):
            with self.subTest(png=f"{i}.png"):
                data = (TESTDATA / f"{i}.png").read_bytes()
                ch = chunks(data)
                tags = [t for t, _ in ch]
                self.assertEqual(tags[0], b"IHDR")
                self.assertEqual(tags[-1], b"IEND")
                self.assertIn(b"IDAT", tags)
                w, h, depth, color = struct.unpack(">IIBB", ch[0][1][:10])
                self.assertEqual(depth, 8)
                self.assertEqual(color, 2)  # truecolor RGB
                self.assertLessEqual(64, w)
                self.assertLessEqual(h, 1024)
                self.assertEqual(w, h)
                idat = b"".join(p for t, p in ch if t == b"IDAT")
                raw = zlib.decompress(idat)
                self.assertEqual(len(raw), h * (1 + 3 * w))
                for y in range(h):  # every scanline uses filter 0 (None)
                    self.assertEqual(raw[y * (1 + 3 * w)], 0)
                # Round-trip: decompress -> recompress reproduces stored IDAT.
                self.assertEqual(zlib.compress(raw), idat)

    def test_generator_is_deterministic(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "gen_pngs", TESTDATA / "gen_pngs.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for i, size in enumerate(mod.SIZES, start=1):
            with self.subTest(png=f"{i}.png"):
                self.assertEqual(
                    mod.make_png(size, i), (TESTDATA / f"{i}.png").read_bytes()
                )


if __name__ == "__main__":
    unittest.main()
