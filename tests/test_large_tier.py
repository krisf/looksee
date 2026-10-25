"""FTR5 P18 pure-logic tests: decode_tier_px boundaries and the tiered
cache-capacity arithmetic (PLAN §19.1.1 / §19.1.3).  No display, no
widgets — import only."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)


class TierFormula(unittest.TestCase):
    def test_two_x_viewport_math(self) -> None:
        self.assertEqual(looksee.decode_tier_px(1190, 790), 2380)
        self.assertEqual(looksee.decode_tier_px(700, 500, 1.0), 1400)

    def test_long_edge_wins_both_orientations(self) -> None:
        self.assertEqual(looksee.decode_tier_px(800, 600), 1600)
        self.assertEqual(looksee.decode_tier_px(600, 800), 1600)

    def test_floor_at_tier_min(self) -> None:
        # 2 * 400 = 800 < 1024 -> floor.
        self.assertEqual(looksee.decode_tier_px(400, 300), looksee.TIER_MIN_PX)
        self.assertEqual(looksee.decode_tier_px(100, 100), 1024)
        # exactly at the floor boundary: 2*512 = 1024.
        self.assertEqual(looksee.decode_tier_px(512, 1), 1024)

    def test_ceil_is_exact(self) -> None:
        # 2 * 512 * 1.001 = 1025.024 -> 1026 (ceil, never round).
        self.assertEqual(looksee.decode_tier_px(512, 1, 1.001), 1026)

    def test_cap_at_texture_px_cap(self) -> None:
        self.assertEqual(looksee.decode_tier_px(3000, 2000), looksee.TEXTURE_PX_CAP)
        # exactly at the cap boundary: 2 * 2048 = 4096.
        self.assertEqual(looksee.decode_tier_px(2048, 100), 4096)
        self.assertEqual(looksee.decode_tier_px(9999, 1), 4096)

    def test_device_scale_scales_the_tier(self) -> None:
        self.assertEqual(looksee.decode_tier_px(1190, 790, 2.0), 4096)  # capped
        self.assertEqual(looksee.decode_tier_px(500, 400, 1.5), 1500)

    def test_zero_viewport_fallback(self) -> None:
        # Window not allocated yet: 1080 fallback, scale does not apply.
        self.assertEqual(looksee.decode_tier_px(0, 0), looksee.TIER_ZERO_PX)
        self.assertEqual(looksee.decode_tier_px(0, 0, 2.0), looksee.TIER_ZERO_PX)

    def test_garbage_viewport_is_zero(self) -> None:
        self.assertEqual(looksee.decode_tier_px(None, None), looksee.TIER_ZERO_PX)
        self.assertEqual(looksee.decode_tier_px(-640, -480), looksee.TIER_ZERO_PX)

    def test_result_is_plain_int(self) -> None:
        for args in ((1190, 790), (0, 0), (400, 300), (9999, 9999, 2.0)):
            self.assertIsInstance(looksee.decode_tier_px(*args), int)


class TieredCacheCapacity(unittest.TestCase):
    def test_tiered_charge_matches_actual_dims(self) -> None:
        # 8000x6000 decoded into a 2380 box -> 2380x1785 (aspect preserved).
        charge = looksee.entry_charge(2380, 1785)
        self.assertEqual(charge, 2380 * 1785 * 4)
        full = looksee.entry_charge(8000, 6000)
        self.assertLess(charge, full / 8)

    def test_six_giants_coexist_under_budget(self) -> None:
        # PLAN §19.2(d): N=6 big tiered slides fit the budget simultaneously.
        charge = looksee.entry_charge(2380, 1785)
        roles = {f"/a/big{n}.jpg": off for n, off in ((0, 0), (1, 1), (2, 2), (3, -1),
                                                      (4, 0), (5, 1))}
        charges = dict.fromkeys(roles, charge)
        self.assertLessEqual(sum(charges.values()), looksee.PRELOAD_PIXEL_BUDGET)
        evicted, fits = looksee.fit_candidate(roles, charges, "/a/new.jpg", charge)
        self.assertTrue(fits)
        self.assertEqual(evicted, [])  # no thrash: nothing has to leave

    def test_full_res_giant_alone_breaches_budget(self) -> None:
        # The FTR5 motivation (§19.0): one full 8000x6000 decode eats >90%
        # of the budget — two can never coexist.
        full = looksee.entry_charge(8000, 6000)
        self.assertGreater(full, looksee.PRELOAD_PIXEL_BUDGET * 9 // 10)
        roles = {"/a/big0.jpg": -1}
        charges = {"/a/big0.jpg": full}
        _evicted, fits = looksee.fit_candidate(roles, charges, "/a/big1.jpg", full)
        self.assertFalse(fits)


class UpgradeAndGrowth(unittest.TestCase):
    """P19 pure helpers (PLAN §19.3 boundaries)."""

    def test_upgrade_ratio_boundary(self):
        self.assertFalse(looksee.upgrade_needed(2499, 2380, 8000))
        self.assertTrue(looksee.upgrade_needed(2499 + 1, 2380, 8000))
        self.assertFalse(looksee.upgrade_needed(2380, 2380, 8000))

    def test_upgrade_requires_more_on_disk(self):
        # Native slide: the file holds nothing beyond the loaded pixels.
        self.assertFalse(looksee.upgrade_needed(9000, 1024, 1024))
        # Only a hair more on disk than the tier already covers.
        self.assertFalse(looksee.upgrade_needed(4000, 2380, 2400))

    def test_upgrade_guards(self):
        self.assertFalse(looksee.upgrade_needed(0, 2380, 8000))
        self.assertFalse(looksee.upgrade_needed(32000, 0, 8000))
        # unknown natural: treated as native-size source (no upgrade)
        self.assertFalse(looksee.upgrade_needed(32000, 2380, 0))

    def test_growth_only_beyond_quarter(self):
        self.assertEqual(looksee.growth_redecode(2380, 2975, 8000), 0)
        self.assertEqual(looksee.growth_redecode(2380, 2976, 8000), 2976)

    def test_growth_never_on_shrink_or_flat(self):
        self.assertEqual(looksee.growth_redecode(2380, 1380, 8000), 0)
        self.assertEqual(looksee.growth_redecode(2380, 2380, 8000), 0)

    def test_growth_native_slide_untouched(self):
        self.assertEqual(looksee.growth_redecode(0, 3020, 8000), 0)
        self.assertEqual(looksee.growth_redecode(2380, 4096, 2380), 0)
        self.assertEqual(looksee.growth_redecode(2380, 4096, 0), 0)

    def test_growth_capped(self):
        self.assertEqual(looksee.growth_redecode(3020, 4500, 8000), 4096)

    def test_constants(self):
        self.assertEqual(looksee.UPGRADE_RATIO, 1.05)
        self.assertEqual(looksee.GROWTH_RATIO, 1.25)
        self.assertEqual(looksee.GROWTH_DEBOUNCE_MS, 300)


if __name__ == "__main__":
    unittest.main()
