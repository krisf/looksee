"""P3 pure-logic tests: zoom bounds + clamp (PLAN §4 hop 6 contract)."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)


class ClampScale(unittest.TestCase):
    def test_contract_bounds(self) -> None:
        self.assertEqual(looksee.ZOOM_MIN, 0.1)
        self.assertEqual(looksee.ZOOM_MAX, 8.0)

    def test_clamps_below_min(self) -> None:
        self.assertEqual(looksee.clamp_scale(0.0), looksee.ZOOM_MIN)
        self.assertEqual(looksee.clamp_scale(-5.0), looksee.ZOOM_MIN)
        self.assertEqual(looksee.clamp_scale(0.09), looksee.ZOOM_MIN)

    def test_clamps_above_max(self) -> None:
        self.assertEqual(looksee.clamp_scale(8.5), looksee.ZOOM_MAX)
        self.assertEqual(looksee.clamp_scale(1e9), looksee.ZOOM_MAX)

    def test_passthrough_in_range(self) -> None:
        for value in (0.1, 0.5, 1.0, 2.5, 8.0):
            self.assertEqual(looksee.clamp_scale(value), value)

    def test_accepts_int_input(self) -> None:
        self.assertEqual(looksee.clamp_scale(1), 1.0)


class ZoomStep(unittest.TestCase):
    def test_step_ratio(self) -> None:
        self.assertAlmostEqual(looksee.zoom_step(1.0, 1), 1.25)
        self.assertAlmostEqual(looksee.zoom_step(1.0, -1), 0.8)

    def test_repeated_up_clamps_at_max(self) -> None:
        scale = 1.0
        for _ in range(200):
            scale = looksee.zoom_step(scale, 1)
        self.assertEqual(scale, looksee.ZOOM_MAX)

    def test_repeated_down_clamps_at_min(self) -> None:
        scale = 1.0
        for _ in range(200):
            scale = looksee.zoom_step(scale, -1)
        self.assertEqual(scale, looksee.ZOOM_MIN)

    def test_never_leaves_bounds_from_any_seed(self) -> None:
        for seed in (0.001, 0.1, 1.0, 7.9, 1000.0):
            scale = seed
            for direction in (1, -1):
                for _ in range(60):
                    scale = looksee.zoom_step(scale, direction)
                    self.assertGreaterEqual(scale, looksee.ZOOM_MIN)
                    self.assertLessEqual(scale, looksee.ZOOM_MAX)

    def test_roundtrip_roughly_idempotent(self) -> None:
        self.assertAlmostEqual(
            looksee.zoom_step(looksee.zoom_step(2.0, 1), -1), 2.0, places=9
        )


if __name__ == "__main__":
    unittest.main()
