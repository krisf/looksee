#!/usr/bin/env python3
"""P11 gate driver: orientation filter for SLIDESHOW TICKS only (FTR2).

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p8_orientation.py

Scratch albums of NON-SQUARE PNGs are generated (stdlib zlib/struct) under
scratch/p11-album-{mix,sq,one} — repo testdata stays square and untouched.
Flows: default "all" + no config file; radio writes JSON immediately; manual
step stays unfiltered under a landscape filter; filtered tick skips
non-matching slides and wraps; a square-only album auto-stops on zero
matches (button released, slide kept, config untouched); a one-match album
keeps playing in place (no spin); "all" restores plain +1; a second window
loads the persisted filter.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import struct
import sys
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["LOOKSEE_CONFIG_DIR"] = str(ROOT / "scratch" / "smoke-p8-config")
shutil.rmtree(os.environ["LOOKSEE_CONFIG_DIR"], ignore_errors=True)

_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

from gi.repository import Adw, GLib  # noqa: E402

STEP_MS = 350
POLL_MS = 150
POLL_BUDGET_S = 6.0
CHECKS: list[str] = []


def write_png(path: str, w: int, h: int) -> None:
    """Minimal 8-bit RGB PNG (flat color) — sizes are what the filter sees."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes((0x30, 0x80, 0x40)) * w for _ in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(png)


def build_album(name: str, spec: list[tuple[str, int, int]]) -> str:
    folder = str(ROOT / "scratch" / name)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    for fname, w, h in spec:
        write_png(os.path.join(folder, fname), w, h)
    return folder


ALBUM_MIX = build_album("p11-album-mix", [
    ("1.png", 120, 80), ("2.png", 90, 90), ("3.png", 80, 120),
    ("4.png", 100, 60), ("5.png", 60, 60), ("6.png", 60, 100),
])
ALBUM_SQ = build_album("p11-album-sq", [("1.png", 70, 70), ("2.png", 70, 70)])
ALBUM_ONE = build_album("p11-album-one",
                        [("1.png", 120, 80), ("2.png", 90, 90), ("3.png", 90, 90)])


def fail(label: str, detail: str = "") -> None:
    print(f"P8 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p8-smoke")
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
        check("orient-default-all", cfg["slide_orientation"] == "all", str(cfg))
        check("no-config-file-on-defaults",
              not os.path.exists(looksee.config_path()))
        check("all-radio-armed", W()._orient_buttons["all"].get_active())

    def s_manual_unfiltered_under_filter():
        W()._orient_buttons["landscape"].set_active(True)  # toggled -> save
        check("radio-writes-json-immediately",
              saved()["slide_orientation"] == "landscape", str(saved()))
        W().step(1)  # manual nav must ignore the filter: 2.png is a SQUARE
        check("manual-step-unfiltered", base() == "2.png", base())
        W().step(-1)
        check("manual-prev-unfiltered", base() == "1.png", base())

    def s_play_filtered():
        W().interval_spin.set_value(1.0)
        W().open_path(os.path.join(ALBUM_MIX, "1.png"))  # back to first L
        W().play_button.set_active(True)

    def p_skipped_to_4():
        return base() == "4.png"

    def s_skipped():
        check("filtered-tick-skips-S-and-P", base() == "4.png", base())

    def p_wrapped_to_1():
        return base() == "1.png"

    def s_wrapped():
        check("filtered-wrap-4-to-1", base() == "1.png", base())
        W().play_button.set_active(False)
        check("off-after-wrap", W()._slide_id == 0)

    def s_zero_match_stop():
        W().open_path(os.path.join(ALBUM_SQ, "1.png"))
        check("sq-album-open", base() == "1.png", base())
        W().play_button.set_active(True)

    def p_autostopped():
        return not W().play_button.get_active()

    def s_autostopped():
        check("zero-match-auto-stops", W()._slide_id == 0)
        check("stop-keeps-slide", base() == "1.png", base())
        check("stop-keeps-filter-config",
              saved()["slide_orientation"] == "landscape", str(saved()))

    def s_single_match():
        W().open_path(os.path.join(ALBUM_ONE, "1.png"))  # the only L
        W().play_button.set_active(True)

    def s_single_match_held():
        check("one-match-still-playing", W()._slide_id != 0)
        check("one-match-no-spin", base() == "1.png", base())
        W().play_button.set_active(False)

    def s_all_restores_plain():
        W()._orient_buttons["all"].set_active(True)
        check("all-writes-json", saved()["slide_orientation"] == "all",
              str(saved()))
        W().open_path(os.path.join(ALBUM_MIX, "4.png"))  # index 3
        W().play_button.set_active(True)

    def p_plain_next_5():
        return base() == "5.png"  # square: only reachable with the filter off

    def s_plain_next():
        check("all-means-plain-plus-one", base() == "5.png", base())
        W().play_button.set_active(False)

    def s_second_window_reloads():
        W()._orient_buttons["portrait"].set_active(True)  # persist portrait
        check("portrait-persisted", saved()["slide_orientation"] == "portrait",
              str(saved()))
        win2 = looksee.LookseeWindow(application=app)
        try:
            check("reloaded-radio-is-portrait",
                  win2._orient_buttons["portrait"].get_active())
            check("reloaded-no-extra-write",  # radio init must NOT write
                  saved()["slide_orientation"] == "portrait")
        finally:
            win2.destroy()
        state["second_window"] = True

    steps: list = [
        s_defaults, s_manual_unfiltered_under_filter, s_play_filtered,
        ("poll", p_skipped_to_4, "filtered tick never reached 4.png"),
        s_skipped,
        ("poll", p_wrapped_to_1, "filtered wrap never reached 1.png"),
        s_wrapped, s_zero_match_stop,
        ("poll", p_autostopped, "zero-match album never auto-stopped"),
        s_autostopped, s_single_match, ("wait", 2600, "two filtered ticks"),
        s_single_match_held, s_all_restores_plain,
        ("poll", p_plain_next_5, "plain +1 never reached 5.png"),
        s_plain_next, s_second_window_reloads,
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
            print("P8 SMOKE PASS:", *CHECKS, sep="\n  check: ", flush=True)
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
    return app.run(["looksee", os.path.join(ALBUM_MIX, "1.png")])


if __name__ == "__main__":
    raise SystemExit(main())
