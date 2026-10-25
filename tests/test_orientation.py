"""P11 pure-logic tests: slide_orientation contract (PLAN §13.1.2).

The ONLY config schema diff in FTR2 is slide_orientation; the filter walk
and the size predicate are pure and tested without any GUI.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)


class OrientationContract(unittest.TestCase):
    def test_default_is_all(self) -> None:
        self.assertEqual(looksee.CONFIG_DEFAULTS["slide_orientation"], "all")

    def test_vocabulary(self) -> None:
        self.assertEqual(looksee.ORIENT_VALUES, ("all", "landscape", "portrait"))

    def test_normalize_case_and_space(self) -> "None":
        self.assertEqual(looksee.normalize_orientation("LANDSCAPE"), "landscape")
        self.assertEqual(looksee.normalize_orientation("  Portrait "), "portrait")
        self.assertEqual(looksee.normalize_orientation("All"), "all")

    def test_normalize_unknown_falls_back_to_all(self) -> None:
        for junk in ("sideways", "", "  ", "landscape-ish", None, 5, True, ["all"]):
            self.assertEqual(looksee.normalize_orientation(junk), "all", junk)


class SizePredicate(unittest.TestCase):
    m = staticmethod(looksee.img_matches_orientation)

    def test_all_matches_everything(self) -> None:
        for w, h in ((2, 1), (1, 2), (1, 1), (0, 0)):
            self.assertTrue(self.m(w, h, "all"), (w, h))

    def test_landscape_strict_gt(self) -> None:
        self.assertTrue(self.m(120, 80, "landscape"))
        self.assertFalse(self.m(80, 120, "landscape"))
        self.assertFalse(self.m(90, 90, "landscape"))  # square: neither

    def test_portrait_strict_gt(self) -> None:
        self.assertTrue(self.m(80, 120, "portrait"))
        self.assertFalse(self.m(120, 80, "portrait"))
        self.assertFalse(self.m(90, 90, "portrait"))  # square: neither

    def test_unprobeable_never_matches(self) -> None:
        self.assertFalse(self.m(0, 0, "landscape"))
        self.assertFalse(self.m(0, 0, "portrait"))


def matchers_of(dims):
    return [(lambda w, h: (lambda want: looksee.img_matches_orientation(w, h, want)))(w, h) for w, h in dims]


class NextMatchingIndex(unittest.TestCase):
    nm = staticmethod(looksee.next_matching_index)

    def test_all_degenerates_to_plain_plus_one(self) -> None:
        ms = matchers_of([(1, 1)] * 4)  # nothing would match L/P — irrelevant
        self.assertEqual(self.nm(ms, 0, True, "all"), 1)
        self.assertEqual(self.nm(ms, 3, True, "all"), 0)  # wraps
        self.assertEqual(self.nm(ms, 0, False, "all"), 3)  # backwards wraps

    def test_empty_list_none(self) -> None:
        self.assertIsNone(self.nm([], 0, True, "all"))
        self.assertIsNone(self.nm([], 0, True, "landscape"))

    def test_landscape_skips_squares_and_portraits(self) -> None:
        # L S P L S P
        ms = matchers_of([(120, 80), (90, 90), (80, 120), (100, 60), (60, 60), (60, 100)])
        self.assertEqual(self.nm(ms, 0, True, "landscape"), 3)
        self.assertEqual(self.nm(ms, 3, True, "landscape"), 0)  # wraps to next L
        self.assertEqual(self.nm(ms, 0, True, "portrait"), 2)
        self.assertEqual(self.nm(ms, 2, True, "portrait"), 5)

    def test_backwards_walk(self) -> None:
        ms = matchers_of([(120, 80), (90, 90), (80, 120), (100, 60)])
        self.assertEqual(self.nm(ms, 3, False, "landscape"), 0)  # 2,1 skip -> 0
        self.assertEqual(self.nm(ms, 0, False, "portrait"), 2)  # wraps back

    def test_square_never_matches_filters(self) -> None:
        ms = matchers_of([(90, 90), (60, 60)])
        self.assertIsNone(self.nm(ms, 0, True, "landscape"))
        self.assertIsNone(self.nm(ms, 1, True, "portrait"))

    def test_nothing_matches_is_none(self) -> None:
        ms = matchers_of([(80, 120), (60, 100), (40, 90)])
        self.assertIsNone(self.nm(ms, 1, True, "landscape"))

    def test_single_match_returns_current_index(self) -> None:
        ms = matchers_of([(120, 80), (90, 90), (80, 120)])
        # Only index 0 is landscape; from 0 the round trip finds 0 again.
        self.assertEqual(self.nm(ms, 0, True, "landscape"), 0)


class ConfigRoundtrip(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "config.json")
        self.addCleanup(self.dir.cleanup)

    def test_missing_file_defaults(self) -> None:
        self.assertEqual(
            looksee.load_config(self.path)["slide_orientation"], "all"
        )

    def test_roundtrip(self) -> None:
        self.assertTrue(
            looksee.save_config(
                {"interval_sec": 2.5, "animations": True, "slide_orientation": "portrait"},
                self.path,
            )
        )
        self.assertEqual(looksee.load_config(self.path)["slide_orientation"], "portrait")

    def test_loader_coerces_uppercase(self) -> None:
        with open(self.path, "w") as fh:
            json.dump({"slide_orientation": "LANDSCAPE"}, fh)
        self.assertEqual(
            looksee.load_config(self.path),
            {
                "interval_sec": 5.0,
                "animations": False,
                "slide_orientation": "landscape",
            },
        )

    def test_loader_unknown_and_bad_types_fall_back_per_key(self) -> None:
        with open(self.path, "w") as fh:
            json.dump(
                {"slide_orientation": "diagonal", "interval_sec": 7.0, "animations": True},
                fh,
            )
        cfg = looksee.load_config(self.path)
        self.assertEqual(cfg["slide_orientation"], "all")
        self.assertEqual(cfg["interval_sec"], 7.0)  # other keys unaffected
        self.assertTrue(cfg["animations"])
        with open(self.path, "w") as fh:
            json.dump({"slide_orientation": 42}, fh)
        self.assertEqual(looksee.load_config(self.path)["slide_orientation"], "all")

    def test_saver_writes_all_three_keys_normalized(self) -> None:
        looksee.save_config(
            {"interval_sec": 3.0, "animations": False, "slide_orientation": "PORTRAIT"},
            self.path,
        )
        with open(self.path) as fh:
            raw = json.load(fh)
        self.assertEqual(
            set(raw), {"interval_sec", "animations", "slide_orientation"}
        )  # exactly the schema
        self.assertEqual(raw["slide_orientation"], "portrait")

    def test_saver_missing_key_defaults_to_all(self) -> None:
        looksee.save_config({"interval_sec": 4.0, "animations": False}, self.path)
        with open(self.path) as fh:
            self.assertEqual(json.load(fh)["slide_orientation"], "all")


if __name__ == "__main__":
    unittest.main()
