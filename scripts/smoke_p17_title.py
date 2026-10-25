#!/usr/bin/env python3
"""P17 gate driver: in-process title-truncation exercise of the REAL window.

Runs under the only sanctioned headless recipe (PLAN R1):

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p17_title.py

Opens testdata/1.png through the real app path (do_open -> open_path),
then in-process verifies PLAN §17.1: both title-block labels carry
ellipsize=END / max-width-chars=48 / xalign=0.5; a 300-character title
leaves the window's measured MINIMUM width a small constant (pre-fix it
equalled the full text width, 3561 px on this stack); a short title's
natural width is unchanged; a nav update lands and keeps the bound.
Exits 0; any failed invariant exits nonzero with 'P17 SMOKE FAIL'.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

STEP_MS = 300
LONG = "x" * 300
MIN_BOUND = 700  # pre-fix min == full text width (>= 3000 px here)
LBL_MIN_BOUND = 60
LBL_NAT_BOUND = 900  # ~48 chars, not 300
CHECKS: list[str] = []


def fail(label: str, detail: str = "") -> None:
    print(f"P17 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def measure_w(widget) -> tuple[int, int]:
    m = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
    return m.minimum, m.natural


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p17-smoke")
    app = looksee.LookseeApp()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def W():  # fresh window handle every step (created during app.run)
        return app.window

    STATE: dict = {}

    def s_props():
        win = W()
        for name, label in (("title", win._title_label),
                            ("subtitle", win._subtitle_label)):
            check(f"{name}-ellipsize-end",
                  label.get_ellipsize() == Pango.EllipsizeMode.END,
                  str(label.get_ellipsize()))
            check(f"{name}-max-width-chars-48",
                  label.get_max_width_chars() == 48,
                  str(label.get_max_width_chars()))
            check(f"{name}-xalign-0.5", abs(label.get_xalign() - 0.5) < 1e-9,
                  str(label.get_xalign()))
            check(f"{name}-width-chars-unset", label.get_width_chars() == -1,
                  "width-chars would pin the headerbar (PLAN §17.3)")

    def s_short_baseline():
        min0, nat0 = measure_w(W())
        STATE["min0"], STATE["nat0"] = min0, nat0
        check("short-title-min-bounded", min0 <= MIN_BOUND, f"min={min0}")

    def s_short_nat_immune():
        # A/B on the real (fixed) window: for a SHORT title the trio must
        # not change the natural width — no layout regression for names.
        lbl = W()._title_label
        _, nat_fixed = measure_w(lbl)
        lbl.set_ellipsize(Pango.EllipsizeMode.NONE)
        lbl.set_max_width_chars(-1)
        _, nat_naive = measure_w(lbl)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(48)
        check("short-title-natural-unchanged", abs(nat_fixed - nat_naive) <= 2,
              f"fixed={nat_fixed} naive={nat_naive}")

    def s_long_title():
        win = W()
        win._title_label.set_text(LONG)
        min1, _ = measure_w(win)
        check("long-title-min-bounded", min1 <= MIN_BOUND,
              f"min={min1} bound={MIN_BOUND}")  # pre-fix: >= 3000
        check("long-title-min-length-independent", min1 <= STATE["min0"] + 80,
              f"min={min1} short-min={STATE['min0']}")
        lbl_min, lbl_nat = measure_w(win._title_label)
        check("long-title-label-min-ellipsis-room", lbl_min <= LBL_MIN_BOUND,
              f"lbl_min={lbl_min}")
        check("long-title-label-nat-capped", lbl_nat <= LBL_NAT_BOUND,
              f"lbl_nat={lbl_nat}")
        check("long-title-text-intact", win._title_label.get_text() == LONG,
              "truncation is visual only")

    def s_long_title_ab_proof():
        # Same window, same text: dropping ellipsize MUST explode the
        # minimum again — proves the guard measures what the fix guards.
        win = W()
        lbl = win._title_label
        lbl.set_ellipsize(Pango.EllipsizeMode.NONE)
        lbl.set_max_width_chars(-1)
        naive, _ = measure_w(win)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(48)
        check("naive-title-clamps-window", naive > MIN_BOUND, f"naive_min={naive}")

    def s_long_subtitle():
        win = W()
        win._subtitle_label.set_text(LONG)
        min1, _ = measure_w(win)
        check("long-subtitle-min-bounded", min1 <= MIN_BOUND, f"min={min1}")

    def s_nav_open():
        W().open_path(str(ROOT / "testdata" / "2.png"))
        return True  # step fired; landing checked by wait_nav

    deadline = [GLib.get_monotonic_time() / 1e6 + 8.0]

    def wait_nav():
        win = W()
        want = str(ROOT / "testdata" / "2.png")
        if win is not None and win.current_path == want:
            GLib.timeout_add(STEP_MS, s_nav_landed)
            return False
        if GLib.get_monotonic_time() / 1e6 > deadline[0]:
            fail("nav-not-landed", f"current_path={win and win.current_path}")
        GLib.timeout_add(100, wait_nav)
        return False

    def s_nav_landed():
        win = W()
        check("nav-title-lands", win._title_label.get_text() == "2.png",
              win._title_label.get_text())
        min1, _ = measure_w(win)
        check("nav-min-still-bounded", min1 <= MIN_BOUND, f"min={min1}")
        check("nav-ellipsize-still-end",
              win._title_label.get_ellipsize() == Pango.EllipsizeMode.END)
        # nav onto a long name: re-truncates, bound holds
        win._title_label.set_text("y" * 300)
        min2, _ = measure_w(win)
        check("nav-long-retruncates-bounded", min2 <= MIN_BOUND, f"min={min2}")
        GLib.timeout_add(STEP_MS, s_done)
        return False

    def s_done():
        print("P17 SMOKE PASS:", *CHECKS, sep="\n  check: ", flush=True)
        app.quit()
        return False

    steps = [s_props, s_short_baseline, s_short_nat_immune, s_long_title,
             s_long_title_ab_proof, s_long_subtitle, s_nav_open]

    def run_next():
        if not steps:
            GLib.timeout_add(STEP_MS, wait_nav)  # s_nav_open fired async
            return False
        try:
            steps.pop(0)()
        except Exception:  # noqa: BLE001 — surface everything then hard-fail
            import traceback

            traceback.print_exc()
            os._exit(1)
        GLib.timeout_add(STEP_MS, run_next)
        return False

    def wait_open():
        win = W()
        if win is not None and win.current_path is not None:
            GLib.timeout_add(STEP_MS, run_next)
            return False
        GLib.timeout_add(150, wait_open)
        return False

    GLib.timeout_add(300, wait_open)
    return app.run(["looksee", str(ROOT / "testdata" / "1.png")])


if __name__ == "__main__":
    raise SystemExit(main())
