"""P4 pure-logic tests: config roundtrip, defaults, corruption tolerance."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)


class ConfigDir(unittest.TestCase):
    def test_env_override(self) -> None:
        self.assertEqual(
            looksee.config_path({"LOOKSEE_CONFIG_DIR": "/tmp/x"}),
            "/tmp/x/config.json",
        )

    def test_default_is_user_config(self) -> None:
        self.assertTrue(
            looksee.config_path({}).endswith(
                str(pathlib.Path(".config") / "looksee" / "config.json")
            )
        )


class IntervalContract(unittest.TestCase):
    def test_default_5s(self) -> None:
        self.assertEqual(looksee.CONFIG_DEFAULTS["interval_sec"], 5.0)
        self.assertIs(looksee.CONFIG_DEFAULTS["animations"], False)

    def test_clamp_bounds(self) -> None:
        self.assertEqual(looksee.clamp_interval(0.1), 1.0)
        self.assertEqual(looksee.clamp_interval(61), 60.0)
        self.assertEqual(looksee.clamp_interval(7.5), 7.5)

    def test_garbage_intervals_fall_back(self) -> None:
        for bad in ("x", None, float("nan"), float("inf"), [1]):
            self.assertEqual(looksee.clamp_interval(bad), 5.0)


class ConfigRoundtrip(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(pathlib.Path(self._tmp.name) / "looksee" / "config.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_file_gives_defaults(self) -> None:
        self.assertEqual(looksee.load_config(self.path), dict(looksee.CONFIG_DEFAULTS))

    def test_roundtrip_creates_dirs(self) -> None:
        cfg = {"interval_sec": 2.5, "animations": True}
        self.assertTrue(looksee.save_config(cfg, self.path))
        loaded = looksee.load_config(self.path)
        self.assertEqual(loaded["interval_sec"], 2.5)
        self.assertIs(loaded["animations"], True)

    def test_corrupt_file_gives_defaults(self) -> None:
        pathlib.Path(self.path).parent.mkdir(parents=True)
        pathlib.Path(self.path).write_text("{not json!!")
        self.assertEqual(looksee.load_config(self.path), dict(looksee.CONFIG_DEFAULTS))

    def test_non_dict_json_gives_defaults(self) -> None:
        pathlib.Path(self.path).parent.mkdir(parents=True)
        pathlib.Path(self.path).write_text("[1, 2]")
        self.assertEqual(looksee.load_config(self.path), dict(looksee.CONFIG_DEFAULTS))

    def test_per_key_fallback_bad_types(self) -> None:
        pathlib.Path(self.path).parent.mkdir(parents=True)
        pathlib.Path(self.path).write_text(
            json.dumps({"interval_sec": "wide", "animations": "yes"})
        )
        self.assertEqual(looksee.load_config(self.path), dict(looksee.CONFIG_DEFAULTS))

    def test_per_key_fallback_keeps_good_keys(self) -> None:
        pathlib.Path(self.path).parent.mkdir(parents=True)
        pathlib.Path(self.path).write_text(
            json.dumps({"interval_sec": 12.0, "animations": "yes", "junk": 1})
        )
        cfg = looksee.load_config(self.path)
        self.assertEqual(cfg["interval_sec"], 12.0)
        self.assertIs(cfg["animations"], False)

    def test_bool_is_not_an_interval(self) -> None:
        pathlib.Path(self.path).parent.mkdir(parents=True)
        pathlib.Path(self.path).write_text(json.dumps({"interval_sec": True}))
        self.assertEqual(looksee.load_config(self.path)["interval_sec"], 5.0)

    def test_stored_interval_clamped_on_load(self) -> None:
        pathlib.Path(self.path).parent.mkdir(parents=True)
        pathlib.Path(self.path).write_text(json.dumps({"interval_sec": 5000}))
        self.assertEqual(looksee.load_config(self.path)["interval_sec"], 60.0)


if __name__ == "__main__":
    unittest.main()


class TransitionDecider(unittest.TestCase):
    def test_off_is_instant(self) -> None:
        self.assertEqual(looksee.transition_for(False), (False, 0))

    def test_on_is_220ms_crossfade(self) -> None:
        self.assertEqual(looksee.transition_for(True), (True, 220))

    def test_truthy_falsy_coercion(self) -> None:
        for truthy in (1, "x", [0]):
            self.assertEqual(looksee.transition_for(truthy)[0], True)
        for falsy in (0, "", None):
            self.assertEqual(looksee.transition_for(falsy), (False, 0))
