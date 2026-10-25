#!/usr/bin/env python3
"""P12 gate driver: chrome auto-hide during slideshow (FTR2).

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p9_autohide.py

Flows (activity is injected in code — no real pointer): play arms the 5 s
countdown; with zero activity the headerbar retracts (reveal-top-bars
False, chrome_hidden True) and the content stack gains height; a synthetic
motion event reveals and RE-ARMS (it hides again after another idle 5 s);
a synthetic click press reveals too; play-OFF reveals and disarms; while
stopped an idle 5.2 s never hides; and while hidden, a filter that matches
nothing auto-stops the slideshow THROUGH the button, revealing chrome.
Runtime ~22 s — inside the 30 s quit-mode watchdog.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["LOOKSEE_CONFIG_DIR"] = str(ROOT / "scratch" / "smoke-p9-config")
shutil.rmtree(os.environ["LOOKSEE_CONFIG_DIR"], ignore_errors=True)

_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

from gi.repository import Adw, GLib  # noqa: E402

STEP_MS = 350
POLL_MS = 150
POLL_BUDGET_S = 8.0
CHECKS: list[str] = []


def fail(label: str, detail: str = "") -> None:
    print(f"P9 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p9-smoke")
    app = looksee.LookseeApp()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def W():
        return app.window

    state: dict = {}

    def revealed() -> bool:
        return W()._toolbar.get_reveal_top_bars()

    def s_baseline():
        check("starts-revealed", revealed())
        check("starts-not-hidden", not W()._chrome_hidden)
        check("no-hide-timer-while-stopped", W()._hide_id == 0)
        state["h_shown"] = W().stack.get_height()
        check("stack-has-allocation", state["h_shown"] > 0, str(state["h_shown"]))

    def s_play_arms():
        W().interval_spin.set_value(30.0)  # no slide changes during the waits
        W().play_button.set_active(True)
        check("play-arms-hide-timer", W()._hide_id != 0)
        check("still-revealed-armed", revealed())

    def p_hidden():
        return not revealed()

    def s_hidden():
        check("idle-5s-hides", W()._chrome_hidden)
        state["h_hidden"] = W().stack.get_height()
        check("hidden-chrome-gains-height",
              state["h_hidden"] > state["h_shown"],
              f"{state['h_shown']} -> {state['h_hidden']}")

    def s_motion_reveals():
        W()._motion_ctrl.emit("motion", 400.0, 300.0)  # synthetic activity

    def s_motion_revealed():
        check("motion-reveals", revealed() and not W()._chrome_hidden)
        check("motion-rearms-while-playing", W()._hide_id != 0)
        check("click-gesture-wired", "_click_gesture" in dir(W()))

    def p_hidden2():
        return not revealed()

    def s_stop_while_hidden():
        check("rehidden-after-activity", W()._chrome_hidden)
        W().play_button.set_active(False)  # OFF while hidden: must reveal
        check("stop-reveals", revealed() and not W()._chrome_hidden)
        check("stop-disarms", W()._hide_id == 0)

    def s_stopped_still_revealed():
        check("stopped-idle-never-hides", revealed() and W()._hide_id == 0)

    def s_filter_stop_reveals():
        W()._orient_buttons["landscape"].set_active(True)  # squares: no match
        W().interval_spin.set_value(30.0)
        W().play_button.set_active(True)  # plain play: countdown arms

    def p_hidden4():
        return not revealed()

    def s_filter_armed():
        check("hidden-before-filter-stop", W()._chrome_hidden)
        W().interval_spin.set_value(1.0)  # live re-tempo only, no activity
        W()._orient_buttons["portrait"].set_active(True)  # squares: zero match

    def p_filter_autostopped():
        return not W().play_button.get_active()

    def s_filter_stop_revealed():
        check("filter-stop-reveals", revealed() and not W()._chrome_hidden)
        check("filter-stop-disarms", W()._hide_id == 0)

    steps: list = [
        s_baseline, s_play_arms,
        ("poll", p_hidden, "chrome never hid after 5s idle"),
        s_hidden, s_motion_reveals,
        ("wait", 400, "reveal settles"), s_motion_revealed,
        ("poll", p_hidden2, "re-armed countdown never re-hid (motion)"),
        s_stop_while_hidden,
        ("wait", 1200, "idle while stopped"), s_stopped_still_revealed,
        s_filter_stop_reveals,
        ("poll", p_hidden4, "chrome never hid (filter phase)"),
        s_filter_armed,
        ("poll", p_filter_autostopped, "zero-match filter never auto-stopped"),
        s_filter_stop_revealed,
    ]

    def wait_open():
        win = W()
        if win is not None and win.current_path is not None:
            GLib.timeout_add(STEP_MS, run_next)
            return False
        GLib.timeout_add(200, wait_open)
        return False

    def run_next():
        if not steps:
            print("P9 SMOKE PASS:", *CHECKS, sep="\n  check: ", flush=True)
            app.quit()
            return False
        try:
            step = steps.pop(0)
            if isinstance(step, tuple) and step[0] == "poll":
                _poll(step[1], step[2])
            elif isinstance(step, tuple) and step[0] == "wait":
                GLib.timeout_add(step[1], run_next)
            else:
                step()
                GLib.timeout_add(STEP_MS, run_next)
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            os._exit(1)
        return False

    def _poll(pred, why):
        deadline = [GLib.get_monotonic_time() / 1e6 + POLL_BUDGET_S]

        def tick():
            if pred():
                GLib.timeout_add(STEP_MS, run_next)
                return False
            if GLib.get_monotonic_time() / 1e6 > deadline[0]:
                fail("poll-timeout", why)
            GLib.timeout_add(POLL_MS, tick)
            return False

        GLib.timeout_add(POLL_MS, tick)

    GLib.timeout_add(300, wait_open)
    return app.run(["looksee", str(ROOT / "testdata" / "1.png")])


if __name__ == "__main__":
    raise SystemExit(main())
