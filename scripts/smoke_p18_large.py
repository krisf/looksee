#!/usr/bin/env python3
"""P18 gate driver: tiered decode of LARGE images through the REAL window.

Runs under the only sanctioned headless recipe (PLAN R1):

    LOOKSEE_SMOKE_MODE=quit LOOKSEE_SMOKE_SECS=90 \
        scripts/headless-smoke.sh scripts/smoke_p18_large.py

Generates an 8000x6000 multi-MB JPEG in scratch AT TEST TIME (cached
across runs), builds a 6-giants + 1-small album, and verifies PLAN §19.2:
(a) a cold big-image load decodes at the TIER box (not 8000 px), with the
true natural dims recorded; (b) the cache charge equals ACTUAL tiered dims
x 4 and is a fraction of the full-res charge; (c) re-navigating to a cached
big file is a zero-IO HIT on the same pixbuf; (d) 6 tiered giants fit the
look-ahead budget simultaneously (no shortfall, no thrash); (e) images at
or below the tier decode at NATIVE dims.  The async engine is spied at the
class level (decodes recorded, nothing delayed).  Exits 0; any failed
invariant exits 1 with 'P18 SMOKE FAIL'.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import random
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["LOOKSEE_CONFIG_DIR"] = str(ROOT / "scratch" / "smoke-p18-config")
shutil.rmtree(os.environ["LOOKSEE_CONFIG_DIR"], ignore_errors=True)

_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

import gi  # noqa: E402

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

ALBUM = ROOT / "scratch" / "p18-album"
GIANT_W, GIANT_H = 8000, 6000
MIN_BYTES = 8 * 1024 * 1024  # the §19.0 "multi-MB JPEG" class
STEP_MS = 200
POLL_MS = 100
CHECKS: list[str] = []


def fail(label: str, detail: str = "") -> None:
    print(f"P18 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def gen_giant(path: pathlib.Path) -> None:
    """8000x6000 JPEG, unique random 8px-block row per scanline, tuned to
    the multi-MB class.  Cached: reuse only a file with the right dims and
    a big enough size."""
    if path.exists() and path.stat().st_size >= MIN_BYTES:
        try:
            _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(str(path))[:3]
            if (w, h) == (GIANT_W, GIANT_H):
                return
        except Exception:  # noqa: BLE001 — regenerate below
            pass
    rng = random.Random(18)
    block_w = GIANT_W // 8
    rows = []
    for _ in range(GIANT_H):
        rows.append(
            b"".join(
                bytes([rng.randrange(256), rng.randrange(256), rng.randrange(256)]) * 8
                for _ in range(block_w)
            )
        )
    pb = GdkPixbuf.Pixbuf.new_from_data(
        b"".join(rows),
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        GIANT_W,
        GIANT_H,
        GIANT_W * 3,
    )
    for quality in ("80", "90", "97"):
        pb.savev(str(path), "jpeg", ["quality"], [quality])
        if path.stat().st_size >= MIN_BYTES:
            break
    if path.stat().st_size < MIN_BYTES:
        print(f"P18 note: giant JPEG only {path.stat().st_size} bytes", flush=True)


def build_album() -> list[str]:
    shutil.rmtree(ALBUM, ignore_errors=True)
    ALBUM.mkdir(parents=True)
    giant = ALBUM / "giant-src.jpg"
    gen_giant(giant)
    giants = []
    for n in range(1, 7):
        dst = ALBUM / f"big{n}.jpg"
        os.link(giant, dst)  # same bytes, 6 distinct paths, cheap on disk
        giants.append(str(dst))
    small = ALBUM / "small.png"
    shutil.copy(ROOT / "testdata" / "12.png", small)  # exactly 1024x1024
    return giants + [str(small)]


# ---------------------------------------------------------------------------
# Engine spy: record every async decode the window schedules (key, priority,
# tier asked, and on completion the pixbuf dims + probed natural dims).
# ---------------------------------------------------------------------------
DECODES: list[dict] = []
_REAL_ENGINE = looksee.LookseeWindow._load_pixbuf_async


def _spy_engine(self, key, io_prio, cancellable, done, tier=0):
    rec = {"key": key, "prio": io_prio, "tier": tier, "dims": None, "natural": None}
    DECODES.append(rec)

    def wrapped(pb, err, nat=None):
        if pb is not None:
            rec["dims"] = (pb.get_width(), pb.get_height())
        rec["natural"] = nat
        return done(pb, err, nat)

    _REAL_ENGINE(self, key, io_prio, cancellable, wrapped, tier)


def key(path: str) -> str:
    return os.path.abspath(path)


def main() -> int:
    print(f"P18 album: {ALBUM}", flush=True)
    FILES = build_album()
    looksee.LookseeWindow._load_pixbuf_async = _spy_engine

    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p18-smoke")
    app = looksee.LookseeApp()

    def W():
        return app.window

    STATE: dict = {}

    def painted():
        win = W()
        return win is not None and win._painted_key

    def pump_quiet() -> bool:
        win = W()
        return (
            win is not None
            and not win._precache_pending
            and win._precache_decode_cancel is None
        )

    def _poll(pred, why, budget=20.0):
        deadline = GLib.get_monotonic_time() / 1e6 + budget

        def tick():
            try:
                if pred():
                    STATE["after"]()
                    return False
            except Exception:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                os._exit(1)
            if GLib.get_monotonic_time() / 1e6 > deadline:
                fail(f"poll-timeout: {why}")
            GLib.timeout_add(POLL_MS, tick)
            return False

        STATE["after"] = STATE["next"]
        GLib.timeout_add(POLL_MS, tick)

    # -- phases ---------------------------------------------------------------

    def s_boot():
        STATE["next"] = s_cold
        _poll(
            lambda: painted() == key(FILES[0]) and pump_quiet(),
            "boot paint + pump settled",
        )

    def s_cold():
        # Force a COLD load of big2 with the window fully allocated: drop
        # the (boot-tier) cache so the engine must decode at the measured
        # tier.  Heartbeat watches the main loop for the whole decode.
        win = W()
        win._precache.clear()
        win._precache_charges.clear()
        win._precache_pending = []
        DECODES.clear()
        STATE["beats"] = [GLib.get_monotonic_time() / 1e6]
        STATE["max_gap"] = 0.0

        def beat():
            now = GLib.get_monotonic_time() / 1e6
            STATE["max_gap"] = max(STATE["max_gap"], now - STATE["beats"][-1])
            STATE["beats"].append(now)
            return True

        STATE["hb"] = GLib.timeout_add(10, beat)
        win.open_path(FILES[1])
        STATE["next"] = s_cold_checks
        _poll(lambda: painted() == key(FILES[1]), "big2 cold tiered load", 30.0)

    def s_cold_checks():
        GLib.source_remove(STATE["hb"])
        gap = STATE["max_gap"]
        # Platform note (deviation, see DSH-REPORT): this box runs glycin
        # UNSANDBOXED (in-process), so any real decode — tiered or full,
        # async API or not — occupies the main loop (~440 ms for a 48-Mpx
        # giant here; measured on the pre-FTR5 full path too).  The check
        # therefore guards the real regression: NO multi-second freeze /
        # retry storm around a tiered giant load.  (Production desktops
        # run glycin sandboxed out-of-process: no occupancy at all.)
        check(
            "loop-no-freeze-during-tier-decode",
            gap < 1.0,
            f"max loop gap {gap:.3f}s during giant decode",
        )
        win = W()
        scr_w, scr_h = win.scroller.get_width(), win.scroller.get_height()
        scale = win.get_scale_factor()
        tier = looksee.decode_tier_px(scr_w, scr_h, scale)
        check("viewport-measured", scr_w > 0 and scr_h > 0, f"{scr_w}x{scr_h}")
        check("tier-in-band", 1024 <= tier <= 4096, str(tier))
        rec = next(
            (
                r
                for r in DECODES
                if r["key"] == key(FILES[1]) and r["prio"] == GLib.PRIORITY_DEFAULT
            ),
            None,
        )
        if rec is None:
            fail("no-slide-load-recorded", str(DECODES))
        check("a-load-asked-for-tier", rec["tier"] == tier, f"{rec['tier']} vs {tier}")
        check(
            "a-natural-true-dims",
            rec["natural"] == (GIANT_W, GIANT_H),
            str(rec["natural"]),
        )
        want_h = tier * GIANT_H / GIANT_W
        ok = (
            rec["dims"] is not None
            and abs(rec["dims"][0] - tier) <= 1
            and abs(rec["dims"][1] - want_h) <= 1
        )
        check(
            "a-pixbuf-tiered-not-native",
            ok,
            f"dims={rec['dims']} want=({tier},{want_h:.0f})",
        )
        check(
            "a-not-decoded-at-8000",
            rec["dims"] is not None and max(rec["dims"]) < GIANT_W,
            str(rec["dims"]),
        )
        check(
            "natural-state-true-dims",
            win._natural == (GIANT_W, GIANT_H),
            str(win._natural),
        )
        charge = win._precache_charges.get(key(FILES[1]))
        full = looksee.entry_charge(GIANT_W, GIANT_H)
        check(
            "b-charge-actual-dims",
            charge == looksee.entry_charge(*rec["dims"]),
            f"{charge} vs {looksee.entry_charge(*rec['dims'])}",
        )
        check("b-charge-fraction-of-full", charge is not None and charge < full / 8,
              f"{charge} vs full {full}")
        print(
            f"P18 measured: viewport={scr_w}x{scr_h}@{scale} tier={tier} "
            f"dims={rec['dims']} charge={charge / 1e6:.1f}MB "
            f"vs full={full / 1e6:.1f}MB max-gap={gap * 1000:.0f}ms",
            flush=True,
        )
        STATE["rec2"] = rec
        STATE["next"] = s_hit_away
        _poll(pump_quiet, "pump settle after cold load")

    def s_hit_away():
        # big1 is cached (boot pump decoded it); nav away then straight
        # back must HIT the cache with zero engine calls.
        DECODES.clear()
        W().step(-1)  # -> big1 (cached)
        STATE["next"] = s_hit_back
        _poll(lambda: painted() == key(FILES[0]), "nav to cached big1")

    def s_hit_back():
        DECODES.clear()
        W().step(1)  # -> big2 again: pure cache hit
        STATE["next"] = s_hit_checks
        _poll(lambda: painted() == key(FILES[1]), "hit nav back to big2")

    def s_hit_checks():
        win = W()
        # a cache HIT must issue ZERO slide loads (PRIORITY_DEFAULT); the
        # idle pump (PRIORITY_LOW) refilling the keep-set is legitimate.
        slide_loads = [
            r for r in DECODES if r["prio"] == GLib.PRIORITY_DEFAULT
        ]
        check("c-hit-zero-decodes", len(slide_loads) == 0, f"{slide_loads}")
        check(
            "c-hit-same-pixbuf",
            win._pixbuf is win._precache.get(key(FILES[1])),
            "hit must reuse the cached pixbuf object",
        )
        check(
            "c-hit-natural-true-dims",
            win._natural == (GIANT_W, GIANT_H),
            str(win._natural),
        )
        STATE["next"] = s_six
        _poll(pump_quiet, "pump settle after hits")

    def s_six():
        # Walk the remaining giants; every landing must be tiered and the
        # whole tiered keep-set must coexist under the budget.
        STATE["walk"] = list(FILES[2:6])
        STATE["next"] = s_six_next
        s_six_next()

    def s_six_next():
        walk = STATE["walk"]
        if not walk:
            STATE["next"] = s_six_checks
            _poll(pump_quiet, "pump settle after six-giant walk", 30.0)
            return
        path = walk.pop(0)
        win = W()
        DECODES.clear()
        win.open_path(path)
        STATE["next"] = s_six_step_ok
        _poll(lambda: painted() == key(path), f"land {os.path.basename(path)}", 30.0)

    def s_six_step_ok():
        win = W()
        rec = next(
            (
                r
                for r in DECODES
                if r["key"] == key(win.current_path)
                and r["prio"] == GLib.PRIORITY_DEFAULT
            ),
            None,
        )
        if rec is not None:  # cold leg: must have been tiered
            check(
                "d-walk-tiered-or-native",
                rec["dims"] is not None and max(rec["dims"]) < GIANT_W,
                str(rec["dims"]),
            )
        STATE["next"] = s_six_next
        GLib.timeout_add(STEP_MS, s_six_next)

    def s_six_checks():
        win = W()
        check("d-zero-shortfalls", win._precache_shortfalls == 0,
              str(win._precache_shortfalls))
        charges = win._precache_charges
        check(
            "d-cache-under-budget",
            sum(charges.values()) <= looksee.PRELOAD_PIXEL_BUDGET,
            str(sum(charges.values())),
        )
        rec = STATE["rec2"]
        per = looksee.entry_charge(*rec["dims"])
        check(
            "d-six-giants-fit-budget",
            6 * per <= looksee.PRELOAD_PIXEL_BUDGET,
            f"6 x {per} vs {looksee.PRELOAD_PIXEL_BUDGET}",
        )
        STATE["next"] = s_small
        _poll(
            lambda: any(
                r["key"] == key(FILES[6]) and r["dims"] is not None
                for r in DECODES
            )
            or key(FILES[6]) in win._precache,
            "small.png decoded by pump",
            15.0,
        )

    def s_small():
        win = W()
        _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(FILES[6])[:3]
        check("e-fixture-1024", (w, h) == (1024, 1024), f"{(w, h)}")
        hit = win._precache.get(key(FILES[6]))
        check(
            "e-small-cached-native",
            hit is not None and (hit.get_width(), hit.get_height()) == (w, h),
            str(hit and (hit.get_width(), hit.get_height())),
        )
        for r in (r for r in DECODES if r["key"] == key(FILES[6])):
            check(
                "e-small-decoded-native",
                r["dims"] == (w, h),
                f"dims={r['dims']} native={(w, h)} tier={r['tier']}",
            )
        STATE["next"] = s_done
        GLib.timeout_add(STEP_MS, s_done)

    def s_done():
        print("P18 SMOKE PASS:", *CHECKS, sep="\n  check: ", flush=True)
        app.quit()

    STATE["steps"] = [s_boot]  # phases then chain via _poll -> STATE["next"]
    STATE["next"] = s_cold

    def run_next():
        if not STATE["steps"]:
            return False
        try:
            STATE["steps"].pop(0)()
        except Exception:  # noqa: BLE001 — surface everything then hard-fail
            import traceback

            traceback.print_exc()
            os._exit(1)
        return False

    STATE["after"] = run_next

    def wait_open():
        win = W()
        if win is not None and win.current_path is not None:
            run_next()
            return False
        GLib.timeout_add(150, wait_open)
        return False

    GLib.timeout_add(300, wait_open)
    rc = app.run(["looksee", FILES[0]])
    looksee.LookseeWindow._load_pixbuf_async = _REAL_ENGINE
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
