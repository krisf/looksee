#!/usr/bin/env python3
"""P3 gate driver: in-process zoom/fullscreen exercise of the REAL window.

Runs under the only sanctioned headless recipe (PLAN R1):

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p3_zoom.py

Opens testdata/1.png through the real app path (do_open -> open_path),
cranks the zoom bounds in-process, verifies the 1:1 scrollbar overflow
condition on the 1024px image (testdata/12.png), drives the key bindings
and the F11 fullscreen toggle, then exits 0.  Any failed invariant exits
nonzero with a 'P3 SMOKE FAIL' line on stderr.
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

from gi.repository import Adw, Gdk, GLib  # noqa: E402 (versions pinned by module)

STEP_MS = 350
CHECKS: list[str] = []


def fail(label: str, detail: str = "") -> None:
    print(f"P3 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def approx(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) < eps


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p3-smoke")
    app = looksee.LookseeApp()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def W():  # fresh window handle every step (created during app.run)
        return app.window

    def s_bounds():
        win = W()
        check("default-fit-mode", win.zoom_mode == "fit", win.zoom_mode)
        for _ in range(80):
            win.zoom_by_step(1)
        check(
            "zoom-max-clamp",
            win.zoom_mode == "scale" and approx(win.scale, looksee.ZOOM_MAX),
            f"scale={win.scale}",
        )
        for _ in range(160):
            win.zoom_by_step(-1)
        check(
            "zoom-min-clamp", approx(win.scale, looksee.ZOOM_MIN),
            f"scale={win.scale}",
        )
        win.set_zoom_fit()

    def s_open_big():
        W().open_path(str(ROOT / "testdata" / "12.png"))  # 1024px square

    def s_one2one():
        W().set_zoom_one_to_one()

    def s_scrollbar():
        win = W()
        adj = win.scroller.get_vadjustment()
        check(
            "1to1-scrollbar",
            adj.get_upper() > adj.get_page_size() + 1,
            f"upper={adj.get_upper()} page={adj.get_page_size()}",
        )
        check("1to1-scale-1.0", approx(win.scale, 1.0), f"scale={win.scale}")

    def s_refit():
        W().set_zoom_fit()

    def s_refit_ok():
        adj = W().scroller.get_vadjustment()
        check(
            "fit-clears-scrollbar",
            adj.get_upper() <= adj.get_page_size() + 1,
            f"upper={adj.get_upper()} page={adj.get_page_size()}",
        )

    def s_wheel():
        win = W()
        win.zoom_by_wheel(-1.0)  # Ctrl+wheel up == zoom in
        check(
            "wheel-zoom-into-scale",
            win.zoom_mode == "scale"
            and looksee.ZOOM_MIN <= win.scale <= looksee.ZOOM_MAX,
            f"mode={win.zoom_mode} scale={win.scale}",
        )

    def s_keys():
        win = W()
        handled = all(
            win._on_key_pressed(None, keyval, 0, 0)
            for keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_minus,
                           Gdk.KEY_1, Gdk.KEY_0)
        )
        check("keys-handled", handled)
        check(
            "key-0-one2one",
            win.zoom_mode == "scale" and approx(win.scale, 1.0),
            f"mode={win.zoom_mode} scale={win.scale}",
        )
        win._on_key_pressed(None, Gdk.KEY_1, 0, 0)
        check("key-1-fit", win.zoom_mode == "fit", win.zoom_mode)

    def s_fullscreen():
        win = W()
        win._on_key_pressed(None, Gdk.KEY_F11, 0, 0)
        check("f11-fullscreen-on", win.is_fullscreen)
        win._on_key_pressed(None, Gdk.KEY_Escape, 0, 0)
        check("esc-exits-fullscreen", not win.is_fullscreen)
        check("chrome-restored", win._toolbar.get_reveal_top_bars())

    steps = [
        s_bounds, s_open_big, s_one2one, s_scrollbar, s_refit, s_refit_ok,
        s_wheel, s_keys, s_fullscreen,
    ]

    deadline = [GLib.get_monotonic_time() / 1e6 + 10.0]

    def wait_open():
        win = W()
        if win is not None and win.current_path is not None:
            GLib.timeout_add(STEP_MS, run_next)
            return False
        if GLib.get_monotonic_time() / 1e6 > deadline[0]:
            fail("window-open", f"win={win}")
        GLib.timeout_add(200, wait_open)
        return False

    def run_next():
        if not steps:
            print("P3 SMOKE PASS:", *CHECKS, sep="\n  check: ", flush=True)
            app.quit()
            return False
        try:
            steps.pop(0)()
        except Exception:  # noqa: BLE001 — surface everything then hard-fail
            import traceback

            traceback.print_exc()
            os._exit(1)
        GLib.timeout_add(STEP_MS, run_next)
        return False

    GLib.timeout_add(300, wait_open)
    return app.run(["looksee", str(ROOT / "testdata" / "1.png")])


if __name__ == "__main__":
    raise SystemExit(main())
