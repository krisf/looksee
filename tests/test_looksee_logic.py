"""Pure-logic tests for Looksee (no display, no widgets — import only).

src/looksee.py imports gi at module level, but importing GI typelibs needs
no display; everything under test is a plain function.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

from gi.repository import GLib

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)


class FormatSubtitle(unittest.TestCase):
    def test_padded_and_unpadded_day(self) -> None:
        dt21 = GLib.DateTime.new_local(2026, 10, 21, 7, 55, 30)
        self.assertEqual(looksee.format_subtitle(dt21), "Oct 21 07:55")
        dt5 = GLib.DateTime.new_local(2026, 3, 5, 23, 9, 0)
        self.assertEqual(looksee.format_subtitle(dt5), "Mar 5 23:09")

    def test_index_suffix(self) -> None:
        dt = GLib.DateTime.new_local(2026, 1, 9, 1, 2, 3)
        self.assertEqual(
            looksee.format_subtitle(dt, 3, 12), "Jan 9 01:02  ·  (3/12)"
        )

    def test_none_dt(self) -> None:
        self.assertEqual(looksee.format_subtitle(None), "")


class SupportedExts(unittest.TestCase):
    def test_supported(self) -> None:
        for name in ("a.png", "B.JPG", "c.jpeg", "d.webp", "e.GIF", "f.svg"):
            self.assertTrue(looksee.is_supported(name), name)

    def test_unsupported(self) -> None:
        for name in ("notes.txt", "clip.mp4", "noext", ".heic", "a.heif"):
            self.assertFalse(looksee.is_supported(name), name)


class Contracts(unittest.TestCase):
    def test_app_id(self) -> None:
        self.assertEqual(looksee.APP_ID, "pro.krisforbes.Looksee")


if __name__ == "__main__":
    unittest.main()
