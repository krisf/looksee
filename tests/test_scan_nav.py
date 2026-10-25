"""P2 pure-logic tests: natural sort, folder scan, wrap-around advance."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)


class NaturalKey(unittest.TestCase):
    def test_numeric_runs_sort_numerically(self) -> None:
        names = ["img10.png", "img2.png", "img1.png", "img100.png"]
        self.assertEqual(
            sorted(names, key=looksee.natural_key),
            ["img1.png", "img2.png", "img10.png", "img100.png"],
        )

    def test_case_insensitive(self) -> None:
        names = ["B.png", "a10.png", "A2.png"]
        self.assertEqual(
            sorted(names, key=looksee.natural_key),
            ["A2.png", "a10.png", "B.png"],
        )

    def test_mixed_text_digit_segments(self) -> None:
        names = ["photo-2-b.jpg", "photo-10-b.jpg", "photo-2-a.jpg"]
        self.assertEqual(
            sorted(names, key=looksee.natural_key),
            ["photo-2-a.jpg", "photo-2-b.jpg", "photo-10-b.jpg"],
        )

    def test_nonimage_names_sort_without_error(self) -> None:
        self.assertLess(
            looksee.natural_key("a"), looksee.natural_key("a2")
        )


class ScanFolder(unittest.TestCase):
    """FTR3 (P15): the SYNC scanner left the app — the GUI enumerates via
    enumerate_children_async and feeds the now-pure `sort_image_paths`
    pipeline with (name, is_file) pairs.  This suite keeps the pipeline's
    filter/sort/abspath contract through a TEST-LOCAL scandir feeder (test
    code may scan synchronously; app code must not — tests/test_no_sync_fs
    guards that line in P16)."""

    @staticmethod
    def scan_folder(folder: str) -> list[str]:
        try:
            entries = [
                (e.name, e.is_file(follow_symlinks=True))
                for e in os.scandir(folder)
            ]
        except OSError:
            return []
        return looksee.sort_image_paths(folder, entries)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        for name in ("10.png", "9.png", "2.png", "notes.txt", ".hidden.png"):
            pathlib.Path(self.dir, name).write_bytes(b"\x89PNG-stub")
        os.mkdir(pathlib.Path(self.dir, "sub"))
        pathlib.Path(self.dir, "sub", "deep.png").write_bytes(b"\x89PNG-stub")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_filters_sorts_and_returns_abs_paths(self) -> None:
        self.assertEqual(
            self.scan_folder(self.dir),
            [
                os.path.join(self.dir, "2.png"),
                os.path.join(self.dir, "9.png"),
                os.path.join(self.dir, "10.png"),
            ],
        )

    def test_missing_dir_returns_empty(self) -> None:
        self.assertEqual(self.scan_folder("/nonexistent/nope"), [])

    def test_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(self.scan_folder(empty), [])

    def test_pure_pipeline_consumes_pairs(self) -> None:
        # Exactly what the async enumerator feeds (name, is_file): dirs,
        # dotfiles and unsupported extensions drop out; natural sort wins.
        self.assertEqual(
            looksee.sort_image_paths(
                "/f",
                [
                    ("b.png", True),
                    ("sub", False),
                    ("a.png", True),
                    (".hidden.png", True),
                    ("notes.txt", True),
                ],
            ),
            ["/f/a.png", "/f/b.png"],
        )


class WrapAdvance(unittest.TestCase):
    def test_forward_wrap(self) -> None:
        self.assertEqual(looksee.wrap_advance(0, 3, 1), 1)
        self.assertEqual(looksee.wrap_advance(2, 3, 1), 0)

    def test_backward_wrap(self) -> None:
        self.assertEqual(looksee.wrap_advance(2, 3, -1), 1)
        self.assertEqual(looksee.wrap_advance(0, 3, -1), 2)

    def test_single_item_stays(self) -> None:
        self.assertEqual(looksee.wrap_advance(0, 1, 1), 0)
        self.assertEqual(looksee.wrap_advance(0, 1, -1), 0)

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(looksee.wrap_advance(0, 0, 1))
        self.assertIsNone(looksee.wrap_advance(-1, 0, -1))

    def test_nothing_open_lands_on_first(self) -> None:
        self.assertEqual(looksee.wrap_advance(-1, 5, -1), 0)


if __name__ == "__main__":
    unittest.main()
