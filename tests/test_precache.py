"""FTR1 P7 pure-logic tests: keep-set window math, prune plan, budget
accounting (PLAN §11.1).  No display, no widgets — import only."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

P = [f"/f/{n}.png" for n in range(1, 13)]  # 12 sorted absolute paths


class WindowOffsets(unittest.TestCase):
    def test_middle_of_list(self) -> None:
        self.assertEqual(looksee.window_offsets(3, 12), (3, 4, 5, 2))

    def test_front_wraps_to_back(self) -> None:
        self.assertEqual(looksee.window_offsets(0, 12), (0, 1, 2, 11))

    def test_wrap_frontier_at_end(self) -> None:
        self.assertEqual(looksee.window_offsets(11, 12), (11, 0, 1, 10))

    def test_second_to_last(self) -> None:
        self.assertEqual(looksee.window_offsets(10, 12), (10, 11, 0, 9))

    def test_n4_collapses_to_all(self) -> None:
        self.assertEqual(sorted(looksee.window_offsets(3, 4)), [0, 1, 2, 3])
        self.assertEqual(len(looksee.window_offsets(0, 4)), 4)

    def test_n3_n2_n1_collapse(self) -> None:
        self.assertEqual(sorted(looksee.window_offsets(1, 3)), [0, 1, 2])
        self.assertEqual(sorted(looksee.window_offsets(0, 2)), [0, 1])
        self.assertEqual(looksee.window_offsets(0, 1), (0,))

    def test_invalid_index_or_total(self) -> None:
        self.assertEqual(looksee.window_offsets(-1, 12), ())
        self.assertEqual(looksee.window_offsets(12, 12), ())
        self.assertEqual(looksee.window_offsets(0, 0), ())


class WindowPaths(unittest.TestCase):
    def test_roles_and_priority_order(self) -> None:
        roles = looksee.window_paths(P, 4)  # p5 i, p6 +1, p7 +2, p4 -1
        self.assertEqual(
            roles, {P[4]: 0, P[5]: 1, P[6]: 2, P[3]: -1}
        )
        self.assertEqual(list(roles)[0], P[4])  # current first

    def test_wrap_frontier_roles(self) -> None:
        roles = looksee.window_paths(P, 11)
        self.assertEqual(roles, {P[11]: 0, P[0]: 1, P[1]: 2, P[10]: -1})

    def test_n_le_4_collapse_to_all_paths(self) -> None:
        for n in (1, 2, 3, 4):
            small = P[:n]
            roles = looksee.window_paths(small, 0)
            self.assertEqual(set(roles), set(small), f"N={n}")
            self.assertEqual(len(roles), n, f"N={n}")

    def test_invalid_index_yields_empty(self) -> None:
        self.assertEqual(looksee.window_paths(P, -1), {})
        self.assertEqual(looksee.window_paths([], 0), {})

    def test_constants_match_spec(self) -> None:
        self.assertEqual(looksee.PRELOAD_FORWARD, 2)
        self.assertEqual(looksee.PRELOAD_BACK, 1)
        self.assertEqual(looksee.PRELOAD_PIXEL_BUDGET, 200 * 1024 * 1024)


class PrunePlan(unittest.TestCase):
    def test_keep_and_drop_sets(self) -> None:
        cache = {"/a": 1, "/b": 1, "/c": 1}
        self.assertEqual(
            looksee.prune_plan(cache, {"/b"}), ["/a", "/c"]
        )

    def test_all_in_window_drops_nothing(self) -> None:
        self.assertEqual(
            looksee.prune_plan(["/a", "/b"], looksee.window_paths(["/a", "/b"], 0)),
            [],
        )

    def test_empty_window_drops_everything(self) -> None:
        self.assertEqual(sorted(looksee.prune_plan(["/a", "/b"], [])), ["/a", "/b"])

    def test_empty_cache_noop(self) -> None:
        self.assertEqual(looksee.prune_plan([], {"/a"}), [])


class EntryCharge(unittest.TestCase):
    def test_rgba_worst_case(self) -> None:
        self.assertEqual(looksee.entry_charge(100, 50), 100 * 50 * 4)

    def test_zero_dims_charge_zero(self) -> None:
        self.assertEqual(looksee.entry_charge(0, 7), 0)

    def test_budget_math_against_cap(self) -> None:
        cap = looksee.PRELOAD_PIXEL_BUDGET
        big = looksee.entry_charge(5120, 5120)  # 100 MiB exactly
        self.assertEqual(2 * big, cap)  # two such entries sit exactly at cap


class FitCandidate(unittest.TestCase):
    def test_fits_under_cap(self) -> None:
        roles = {"/i": 0, "/p1": 1, "/p2": 2, "/m1": -1}
        charges = {"/i": 100, "/p1": 100}
        evicted, fits = looksee.fit_candidate(roles, charges, "/p2", 100, cap=1000)
        self.assertEqual(evicted, [])
        self.assertTrue(fits)

    def test_furthest_lookahead_evicts_first(self) -> None:
        roles = {"/i": 0, "/p1": 1, "/p2": 2, "/m1": -1}
        charges = {"/i": 100, "/p1": 100, "/p2": 100}
        evicted, fits = looksee.fit_candidate(roles, charges, "/m1", 100, cap=300)
        self.assertEqual(evicted, ["/p2"])  # +2 before +1
        self.assertTrue(fits)

    def test_eviction_order_plus2_then_plus1(self) -> None:
        roles = {"/i": 0, "/p1": 1, "/p2": 2, "/m1": -1}
        charges = {"/i": 100, "/p1": 100, "/p2": 100}
        evicted, fits = looksee.fit_candidate(roles, charges, "/m1", 100, cap=199)
        self.assertEqual(evicted, ["/p2", "/p1"])
        self.assertFalse(fits)  # 200 > 199 even after both look-aheads gone

    def test_current_and_back_never_evicted(self) -> None:
        roles = {"/i": 0, "/p1": 1, "/p2": 2, "/m1": -1}
        charges = {"/i": 100, "/m1": 100}
        evicted, fits = looksee.fit_candidate(roles, charges, "/p1", 100, cap=250)
        self.assertEqual(evicted, [])  # i and i-1 are not eviction victims
        self.assertFalse(fits)  # caller records a shortfall and skips

    def test_stale_keys_dont_count_toward_total(self) -> None:
        # A key outside the window is a prune-drop candidate: it contributes
        # nothing and is never an eviction victim.
        roles = {"/i": 0, "/p1": 1, "/p2": 2, "/m1": -1}
        charges = {"/i": 100, "/stale": 5000}
        evicted, fits = looksee.fit_candidate(roles, charges, "/p1", 100, cap=250)
        self.assertEqual(evicted, [])
        self.assertTrue(fits)

    def test_candidate_replace_drops_its_own_old_charge(self) -> None:
        roles = {"/i": 0, "/p1": 1, "/p2": 2, "/m1": -1}
        charges = {"/i": 100, "/p2": 300}
        evicted, fits = looksee.fit_candidate(roles, charges, "/p2", 100, cap=250)
        self.assertEqual(evicted, [])  # replacing: 100 (i) + 100 (new p2)
        self.assertTrue(fits)

    def test_exact_cap_fits(self) -> None:
        roles = {"/i": 0, "/p1": 1, "/p2": 2, "/m1": -1}
        charges = {"/i": 100}
        evicted, fits = looksee.fit_candidate(roles, charges, "/p1", 200, cap=300)
        self.assertEqual(evicted, [])
        self.assertTrue(fits)  # <= cap is a fit


if __name__ == "__main__":
    unittest.main()
