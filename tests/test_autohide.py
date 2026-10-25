"""P12 pure-logic tests: chrome auto-hide countdown (PLAN §13.1.3)."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)


class HideConstant(unittest.TestCase):
    def test_five_seconds(self) -> None:
        self.assertEqual(looksee.HIDE_AFTER_MS, 5000)


class ChromeShouldHide(unittest.TestCase):
    cs = staticmethod(looksee.chrome_should_hide)

    def test_never_when_not_playing(self) -> None:
        self.assertFalse(self.cs(1_000 + 3 * looksee.HIDE_AFTER_MS, 1_000, False))
        self.assertFalse(self.cs(10**12, 0, False))

    def test_just_under_boundary(self) -> None:
        self.assertFalse(self.cs(5_000 + 4_999, 5_000, True))  # 4999 ms idle

    def test_just_over_boundary(self) -> None:
        self.assertTrue(self.cs(5_000 + 5_001, 5_000, True))  # 5001 ms idle

    def test_exactly_at_boundary_hides(self) -> None:
        self.assertTrue(self.cs(5_000 + looksee.HIDE_AFTER_MS, 5_000, True))

    def test_fresh_activity_hides_nothing(self) -> None:
        self.assertFalse(self.cs(9_999, 9_999, True))
        self.assertFalse(self.cs(9_999, 9_998, True))

    def test_clock_delta_backwards_is_no_hide(self) -> None:
        # Monotonic clocks don't go back; the pure helper still refuses.
        self.assertFalse(self.cs(1_000, 1_000 + 2 * looksee.HIDE_AFTER_MS, True))


if __name__ == "__main__":
    unittest.main()
