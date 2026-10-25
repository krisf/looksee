#!/usr/bin/env python3
"""P4 gate driver: in-process slideshow + config-persistence exercise.

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p4_slideshow.py

Uses a repo-local LOOKSEE_CONFIG_DIR (scratch/smoke-p4-config) so the smoke
stays sandbox-clean.  Flows: defaults 5.0 with no file on disk; menu SpinButton
writes JSON immediately; toggle (signals fired in code) ticks next() at the
configured interval; toggle off stops the tick; the ticker wraps last -> first.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["LOOKSEE_CONFIG_DIR"] = str(ROOT / "scratch" / "smoke-p4-config")
shutil.rmtree(os.environ["LOOKSEE_CONFIG_DIR"], ignore_errors=True)

_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

from gi.repository import Adw, GLib  # noqa: E402

STEP_MS = 350
POLL_MS = 150
POLL_BUDGET_S = 4.0
CHECKS: list[str] = []


def fail(label: str, detail: str = "") -> None:
    print(f"P4 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p4-smoke")
    app = looksee.LookseeApp()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def W():
        return app.window

    state: dict = {}

    def base() -> str:
        return os.path.basename(W().current_path or "")

    def saved() -> dict:
        with open(looksee.config_path()) as fh:
            return json.load(fh)

    def s_defaults():
        check("opened-first", base() == "1.png", base())
        cfg = looksee.load_config()
        check(
            "defaults-5s-no-file",
            cfg["interval_sec"] == 5.0 and not os.path.exists(looksee.config_path()),
            f"cfg={cfg}",
        )

    def s_menu_writes_json():
        W().interval_spin.set_value(1.0)  # fires value-changed -> save
        check("spin-writes-json-immediately", saved()["interval_sec"] == 1.0,
              str(saved()))

    def s_play():
        W().play_button.set_active(True)  # toggled signal -> start
        check("play-icon-is-pause",
              W().play_button.get_icon_name() == "media-playback-pause-symbolic",
              W().play_button.get_icon_name())
        check("ticker-armed", W()._slide_id != 0)

    def p_advanced():
        return base() != "1.png"

    def s_advanced():
        check("tick-advanced-1s", base() == "2.png", base())

    def s_live_retempo():
        W().interval_spin.set_value(2.5)
        check("retempo-saved", saved()["interval_sec"] == 2.5, str(saved()))

    def s_stop():
        W().play_button.set_active(False)
        check("stop-icon-is-play",
              W().play_button.get_icon_name() == "media-playback-start-symbolic",
              W().play_button.get_icon_name())
        check("ticker-removed", W()._slide_id == 0)
        state["stopped_at"] = base()

    def s_stopped_stays():
        check("stopped-stays", base() == state["stopped_at"],
              f"{state['stopped_at']} -> {base()}")

    def s_wrap():
        W().interval_spin.set_value(1.0)
        W().open_path(str(ROOT / "testdata" / "12.png"))  # last slide
        check("at-last-slide", base() == "12.png", base())
        W().play_button.set_active(True)

    def p_wrapped():
        return base() == "1.png"

    def s_wrap_done():
        check("wrap-last-to-first", base() == "1.png", base())
        W().play_button.set_active(False)
        check("final-ticker-removed", W()._slide_id == 0)

    steps: list = [
        s_defaults, s_menu_writes_json, s_play, ("poll", p_advanced,
                                                 "slideshow never advanced"),
        s_advanced, s_live_retempo, s_stop, ("wait", 1200, "settle"),
        s_stopped_stays, s_wrap, ("poll", p_wrapped, "slideshow never wrapped"),
        s_wrap_done,
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
            print("P4 SMOKE PASS:", *CHECKS, sep="\n  check: ", flush=True)
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
