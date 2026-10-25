#!/usr/bin/env python3
"""P5 gate driver: crossfade carrier under BOTH configs (PLAN §8 row 8).

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p5_anim.py

Default (no config file): instant cut — stack measures NONE/0ms and the
stale child is pruned inside the same swap.  Switch the ⋮ menu Animations
row ON (real state-set signal) -> CROSSFADE/220ms persisted; a slide swap
leaves BOTH children alive during the fade and a later tick prunes the
stale one; switch back OFF restores the hard cut.  Repo-local
LOOKSEE_CONFIG_DIR keeps the smoke sandbox-clean.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["LOOKSEE_CONFIG_DIR"] = str(ROOT / "scratch" / "smoke-p5-config")
shutil.rmtree(os.environ["LOOKSEE_CONFIG_DIR"], ignore_errors=True)

_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

from gi.repository import Adw, GLib, Gtk  # noqa: E402

STEP_MS = 350
POLL_MS = 150
POLL_BUDGET_S = 3.0
CHECKS: list[str] = []


def fail(label: str, detail: str = "") -> None:
    print(f"P5 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p5-smoke")
    app = looksee.LookseeApp()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def W():
        return app.window

    def children() -> int:
        n, c = 0, W().stack.get_first_child()
        while c is not None:
            n += 1
            c = c.get_next_sibling()
        return n

    def saved() -> dict:
        with open(looksee.config_path()) as fh:
            return json.load(fh)

    def s_off_default():
        win = W()
        check(
            "off-default",
            win.stack.get_transition_type() == Gtk.StackTransitionType.NONE
            and win.stack.get_transition_duration() == 0,
            f"{win.stack.get_transition_type()}/{win.stack.get_transition_duration()}",
        )
        check("no-file-on-disk", not os.path.exists(looksee.config_path()))
        win.step(1)
        check("hard-cut-prunes-now", children() == 1, f"children={children()}")

    def s_switch_on():
        win = W()
        win.anim_switch.emit("state-set", True)  # real menu-switch path
        check(
            "crossfade-220",
            win.stack.get_transition_type() == Gtk.StackTransitionType.CROSSFADE
            and win.stack.get_transition_duration() == 220,
            f"{win.stack.get_transition_type()}/{win.stack.get_transition_duration()}",
        )
        check("switch-on-persisted", saved()["animations"] is True, str(saved()))
        check("switch-widget-state", win.anim_switch.get_state())

    def s_fade_step():
        W().step(1)
        check("fade-keeps-both-children", children() == 2, f"children={children()}")

    def p_pruned():
        return children() == 1

    def s_switch_off():
        win = W()
        win.anim_switch.emit("state-set", False)
        check(
            "back-to-instant",
            win.stack.get_transition_type() == Gtk.StackTransitionType.NONE
            and win.stack.get_transition_duration() == 0,
            "still crossfade",
        )
        check("switch-off-persisted", saved()["animations"] is False, str(saved()))
        win.step(1)
        check("hard-cut-again", children() == 1, f"children={children()}")

    def s_reload_sees_config():
        cfg = looksee.load_config()  # "relaunch": fresh read of persisted key
        check("reload-animations-off", cfg["animations"] is False
              and cfg["interval_sec"] == 5.0, str(cfg))

    steps: list = [
        s_off_default, s_switch_on, s_fade_step,
        ("poll", p_pruned, "stale child never pruned after fade"),
        s_switch_off, s_reload_sees_config,
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
            print("P5 SMOKE PASS:", *CHECKS, sep="\n  check: ", flush=True)
            app.quit()
            return False
        try:
            step = steps.pop(0)
            if isinstance(step, tuple) and step[0] == "poll":
                _poll(step[1], step[2])
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
