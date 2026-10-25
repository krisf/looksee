"""P17 regression guards: long filenames must not clamp minimum window width
(PLAN §17 FTR4).

Pure widget-level, in-process.  No display is used or needed: label and
window `measure()` runs on the display-less GTK fallback (P17 probe — the
real LookseeWindow constructs and measures fine with no DISPLAY; the only
display-requiring check is a rendered pixel, and this feature is geometry,
not pixels).  setUpModule removes DISPLAY/WAYLAND_DISPLAY for its duration
so these widgets can never silently attach to a live user session; the
Xvfb side of the same contract lives in scripts/smoke_p17_title.py.

Pre-fix measured facts (P17 probe, GTK 4.22): a 300-char title pushed the
window minimum width to 3561 px (Xvfb) / 5286 px (display-less) — i.e. the
minimum WAS the text width.  Post-fix it must stay a small constant.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango  # noqa: E402

LONG_TEXT = "x" * 300

# Environment guard: never build widgets against the operator's live display.
_SAVED_ENV: dict[str, str | None] = {}


def setUpModule() -> None:
    for var in ("DISPLAY", "WAYLAND_DISPLAY"):
        _SAVED_ENV[var] = os.environ.pop(var, None)


def tearDownModule() -> None:
    for var, value in _SAVED_ENV.items():
        if value is not None:
            os.environ[var] = value


def measure_w(widget: Gtk.Widget) -> tuple[int, int]:
    m = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
    return m.minimum, m.natural


class TitleBlockSourceContract(unittest.TestCase):
    """PLAN §17.1/§17.3 as a source lint: the three sizing calls, Pango
    pinned, and width-chars NEVER set anywhere (that would pin the bar)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = (ROOT / "src" / "looksee_ui.py").read_text(encoding="utf-8")

    def test_pango_version_pinned_and_imported(self) -> None:
        self.assertIn('gi.require_version("Pango", "1.0")', self.src)
        self.assertRegex(self.src, r"from gi\.repository import [^\n]*\bPango\b")

    def test_ellipsize_uses_pango_end(self) -> None:
        self.assertIn("set_ellipsize(Pango.EllipsizeMode.END)", self.src)

    def test_max_width_chars_and_xalign_set(self) -> None:
        self.assertIn("set_max_width_chars(48)", self.src)
        self.assertIn("set_xalign(0.5)", self.src)

    def test_width_chars_never_set(self) -> None:
        # §17.3: width-chars would PIN the headerbar — the exact inverse.
        self.assertNotIn("set_width_chars", self.src)
        self.assertNotIn('"width-chars"', self.src)


class LabelEllipsizeMechanism(unittest.TestCase):
    """The mechanism itself, on bare real labels (no display): without
    ellipsize the minimum width IS the text; with the contract trio it is a
    small constant and the natural width is capped at 48 chars."""

    def test_raw_label_minimum_is_the_full_text(self) -> None:
        lbl = Gtk.Label(label=LONG_TEXT)
        minimum, natural = measure_w(lbl)
        self.assertEqual(minimum, natural)  # the pre-fix clamp
        self.assertGreaterEqual(minimum, 1000)

    def test_fixed_label_minimum_is_bounded(self) -> None:
        lbl = Gtk.Label(label=LONG_TEXT)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(48)
        lbl.set_xalign(0.5)
        minimum, natural = measure_w(lbl)
        self.assertLessEqual(minimum, 60)  # ellipsis room, not the text
        self.assertLessEqual(natural, 900)  # ~48 chars, not 300
        self.assertEqual(lbl.get_ellipsize(), Pango.EllipsizeMode.END)
        self.assertEqual(lbl.get_max_width_chars(), 48)


class WindowTitleBlockGuard(unittest.TestCase):
    """The real LookseeWindow, in-process: min width must not depend on
    title length; short titles must not regress; the trio is wired on BOTH
    labels."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.win = looksee.LookseeWindow()  # app-less construct verified P17

    def setUp(self) -> None:
        self.win._title_label.set_text("Looksee")
        self.win._subtitle_label.set_text("")

    def test_both_labels_carry_the_sizing_contract(self) -> None:
        for label in (self.win._title_label, self.win._subtitle_label):
            self.assertEqual(label.get_ellipsize(), Pango.EllipsizeMode.END)
            self.assertEqual(label.get_max_width_chars(), 48)
            self.assertAlmostEqual(label.get_xalign(), 0.5)
            self.assertEqual(label.get_width_chars(), -1)  # never pinned

    def test_short_title_natural_width_unchanged(self) -> None:
        # A/B on the SAME fixed window: for a SHORT title, ellipsize +
        # max-width-chars must not change the natural width (no regression
        # for normal names — the 48-char cap never bites short text).
        self.win._title_label.set_text("1.png")
        _, nat_fixed = measure_w(self.win._title_label)
        self.win._title_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.win._title_label.set_max_width_chars(-1)
        _, nat_naive = measure_w(self.win._title_label)
        self.win._title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.win._title_label.set_max_width_chars(48)
        self.assertLessEqual(abs(nat_fixed - nat_naive), 2)

    def test_long_title_does_not_clamp_window_min_width(self) -> None:
        _, nat0 = measure_w(self.win)  # default title
        min0, _ = measure_w(self.win)
        self.win._title_label.set_text(LONG_TEXT)
        minimum, _ = measure_w(self.win)
        # Pre-fix this measured 3561–5286 px (P17 probe); post-fix the
        # title contributes ellipsis-room, not text width (PLAN §17.1.3:
        # stays within a small bound, e.g. <= 600 px).
        self.assertLessEqual(minimum, 700)
        self.assertLessEqual(minimum, min0 + 80)  # length-independent
        lbl_min, lbl_nat = measure_w(self.win._title_label)
        self.assertLessEqual(lbl_min, 60)
        self.assertLessEqual(lbl_nat, 900)
        # The full text is untouched — truncation is purely visual.
        self.assertEqual(self.win._title_label.get_text(), LONG_TEXT)
        # And min stayed under nat for the default title (sanity).
        self.assertLess(min0, nat0)

    def test_long_subtitle_does_not_clamp_either(self) -> None:
        self.win._subtitle_label.set_text(LONG_TEXT)
        minimum, _ = measure_w(self.win)
        self.assertLessEqual(minimum, 700)
        self.assertLessEqual(measure_w(self.win._subtitle_label)[0], 60)

    def test_nav_update_re_truncates(self) -> None:
        # A nav-style update (title replaced while ellipsized) lands and
        # keeps the bound — the guard the driver also checks live.
        self.win._title_label.set_text(LONG_TEXT)
        min_long, _ = measure_w(self.win)
        self.win._title_label.set_text("photo-with-a-rather-long-name.png")
        min_short, _ = measure_w(self.win)
        self.assertEqual(self.win._title_label.get_text(),
                         "photo-with-a-rather-long-name.png")
        self.assertLessEqual(min_long, 700)
        self.assertLessEqual(min_short, 700)
        self.assertEqual(
            self.win._title_label.get_ellipsize(), Pango.EllipsizeMode.END
        )


if __name__ == "__main__":
    unittest.main()
