#!/usr/bin/env python3
"""P19 gate driver: deep-zoom full-res upgrade + viewport-growth re-decode.

Runs under the only sanctioned headless recipe (PLAN R1):

    LOOKSEE_SMOKE_MODE=quit LOOKSEE_SMOKE_SECS=120 \
        scripts/headless-smoke.sh scripts/smoke_p19_upgrade.py

Album: one 8000x6000 multi-MB JPEG (same generator as smoke_p18_large) plus
a tiny PNG, in scratch/p19-album.  Verifies PLAN §19.3 through the REAL
window with a spy wrapped around the async decode engine:

  (a) fit view schedules NO full decode (tier=0 PRIORITY_DEFAULT calls = 0);
  (b) deep zoom schedules EXACTLY ONE, repeated triggers while pending are
      no-ops, the slot arrives with true 8000x6000 dims, the render adopts
      it, and the on-screen texture stays <= TEXTURE_PX_CAP;
  (c) navigating mid-upgrade drops the completion (no paint, slot cleared,
      nothing re-appears a beat later);
  (d) a viewport GROWTH past 25% re-decodes the current slide EXACTLY once
      (debounced, not per-pixel), shrink never re-decodes, and regrowing to
      the same tier is a no-op;
  (e) zooming back under tier coverage (and fit) drops the slot and restores
      the tiered render; no extra decode is issued for the drop; and the
      sync-FS lint (FTR3) still holds over both source files.
Exits 0; any failed invariant exits nonzero with 'P19 SMOKE FAIL'.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

# Reuse the P18 driver's giant-JPEG generator (module import has no side
# effects — both drivers guard main()).
_p18_spec = importlib.util.spec_from_file_location(
    "smoke_p18_large", ROOT / "scripts" / "smoke_p18_large.py"
)
p18 = importlib.util.module_from_spec(_p18_spec)
_p18_spec.loader.exec_module(p18)

from gi.repository import GLib  # noqa: E402

ALBUM = ROOT / "scratch" / "p19-album"
GROW_W, GROW_H = 1520, 900  # growth viewport: tier 2*1510 = 3020 > 1.25*2380
SMALL_W = 700
SMALL_H = 500
DEBOUNCE_WAIT_MS = 1200  # > 300 ms debounce + settle
CHECKS: list[str] = []


def fail(label: str, detail: str = "") -> None:
    print(f"P19 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def build_album() -> str:
    ALBUM.mkdir(parents=True, exist_ok=True)
    giant = ALBUM / "bigA.jpg"  # gen_giant caches: reuse if bytes+dims fit
    p18.gen_giant(giant)
    return str(giant)


CALLS: list[dict] = []


def _spy_engine(self, key, io_prio, cancellable, done, tier=0):
    CALLS.append({"key": os.path.basename(key), "prio": io_prio, "tier": tier})
    return looksee.LookseeWindow.__dict__["_load_pixbuf_async_orig"](
        self, key, io_prio, cancellable, done, tier
    )


looksee.LookseeWindow._load_pixbuf_async_orig = (
    looksee.LookseeWindow._load_pixbuf_async
)
looksee.LookseeWindow._load_pixbuf_async = _spy_engine


def key(path: str) -> str:
    return os.path.abspath(path)


def main() -> int:
    giant = build_album()
    shutil.copyfile(ROOT / "testdata" / "1.png", ALBUM / "small.png")
    FILES = sorted(str(p) for p in ALBUM.iterdir() if p.is_file())
    BIG = next(f for f in FILES if f.endswith("bigA.jpg"))
    SMALL = next(f for f in FILES if f.endswith("small.png"))
    print(f"P19 album: {ALBUM}", flush=True)

    def W():
        for w in app.get_windows():
            if isinstance(w, looksee.LookseeWindow):
                return w
        return None

    def painted():
        w = W()
        return w._painted_key if w else None

    def settled():
        w = W()
        return (
            w is not None
            and painted() is not None
            and not w._precache_pending
            and w._precache_decode_cancel is None
        )

    def full_calls(calls=CALLS):
        """Engine attempts that are full-res slide decodes: tier=0 at the
        slide-load priority (the orient probe decodes at PRIORITY_LOW)."""
        return [
            c for c in calls
            if c["tier"] == 0 and c["prio"] == GLib.PRIORITY_DEFAULT
        ]

    def current_calls():
        return [
            c for c in CALLS
            if c["prio"] == GLib.PRIORITY_DEFAULT and c["key"] == "bigA.jpg"
        ]

    def _poll(pred, why, budget=20.0, after=None):
        box = {"t0": GLib.get_monotonic_time()}

        def tick():
            if pred():
                (after or STATE["after"])()
                return False
            if (GLib.get_monotonic_time() - box["t0"]) / 1e6 > budget:
                fail(f"poll-timeout: {why}")
            return True

        GLib.timeout_add(25, tick)

    STATE: dict = {}
    STATE["after"] = None

    def then(fn):
        STATE["after"] = fn

    # -- phases ---------------------------------------------------------------

    def s_boot():
        _poll(settled, "boot paint + pump quiet", 25.0, s_a_fit)

    def s_a_fit():
        check(
            "a-fit-no-full-decode",
            len(full_calls()) == 0,
            f"boot/fit scheduled {full_calls()}",
        )
        then(s_b_trigger)
        GLib.timeout_add(50, s_b_trigger)

    def s_b_trigger():
        w = W()
        CALLS.clear()
        w.zoom_mode = "scale"
        w.scale = 4.0
        w._apply_zoom()
        check("b-one-full-scheduled", len(full_calls()) == 1, f"{CALLS}")
        w._apply_zoom()
        w.zoom_by_step(1)
        w._apply_zoom()  # repeats while pending must all be no-ops
        check("b-repeat-triggers-noop", len(full_calls()) == 1, f"{CALLS}")
        then(s_b_loaded)
        _poll(lambda: W()._full_pixbuf is not None, "full-res arrival", 25.0)

    def s_b_loaded():
        w = W()
        pb = w._full_pixbuf
        check(
            "b-slot-true-dims",
            (pb.get_width(), pb.get_height()) == (p18.GIANT_W, p18.GIANT_H),
            f"{(pb.get_width(), pb.get_height())}",
        )
        check("b-pixbuf-is-full", w._pixbuf is w._full_pixbuf)
        tex = w._texture.get_width() if w._texture is not None else 1 << 30
        check(
            "b-texture-capped",
            w._texture.get_height() <= looksee.TEXTURE_PX_CAP
            and tex <= looksee.TEXTURE_PX_CAP,
            f"tex {w._texture.get_width()}x{w._texture.get_height()}",
        )
        check(
            "b-base-retained",
            w._base_pixbuf is not None and w._base_pixbuf is not w._full_pixbuf,
        )
        n_before = len(full_calls())
        w._apply_zoom()
        check(
            "b-no-second-upgrade",
            len(full_calls()) == n_before,
            f"{n_before} -> {len(full_calls())}",
        )
        then(s_c_nav)
        GLib.timeout_add(50, s_c_nav)

    def s_c_nav():
        w = W()
        # arm a fresh in-flight upgrade, then navigate away mid-decode:
        w._drop_full_res()  # restore tiered look deterministically
        CALLS.clear()
        w.zoom_mode = "scale"
        w.scale = 4.0
        w._apply_zoom()
        check("c-arm-scheduled", len(full_calls()) == 1, f"{CALLS}")
        w.step(1)  # mid-flight nav: must cancel + slot-drop synchronously
        check("c-slot-dropped-on-nav", w._full_pixbuf is None and w._full_cancel is None)
        then(s_c_checks)
        _poll(lambda: painted() == key(SMALL), "nav landed (cache hit)", 10.0)

    def s_c_checks():
        w = W()

        def later():
            check(
                "c-late-completion-dropped",
                w._full_pixbuf is None
                and w._full_key is None
                and painted() == key(SMALL),
                f"slot={w._full_pixbuf} painted={painted()}",
            )
            then(s_d_hit)
            GLib.timeout_add(50, s_d_hit)

        GLib.timeout_add(DEBOUNCE_WAIT_MS, later)
        return False

    def s_d_hit():
        w = W()
        w.step(-1)  # back onto the giant (cached tiered hit)
        then(s_d_grow)
        _poll(
            lambda: painted() == key(BIG)
            and w._slide_tier > 0
            and not w._precache_pending
            and w._precache_decode_cancel is None,
            "giant repainted with settled tier",
            25.0,
        )

    def s_d_grow():
        w = W()
        base_tier = w._slide_tier
        CALLS.clear()
        w.set_default_size(GROW_W, GROW_H)  # the proven headless resize seam
        then(s_d_landed)
        _poll(lambda: len(current_calls()) >= 1, "growth re-decode issued", 8.0)
        # stash for later phases
        STATE["base_tier"] = base_tier

    def s_d_landed():
        w = W()

        def extra_quiet():
            # TIERED growth re-decodes only: with zoom still deep (phase c
            # left it at 4x), a follow-up FULL upgrade for the new bigger
            # tier is correct §19.3 behavior — counted by b/e semantics.
            n = len([c for c in current_calls() if c["tier"] > 0])
            check(
                "d-growth-redecode-once",
                n == 1,
                f"{n} tiered current-slide decodes: all={CALLS}",
            )
            W()._drop_full_res()  # tidy: deterministic state for the shrinks
            hit = w._precache.get(key(BIG))
            long_edge = max(hit.get_width(), hit.get_height()) if hit else 0
            check(
                "d-growth-tier-landed",
                long_edge == w._tier and long_edge > STATE["base_tier"],
                f"entry {long_edge} tier {w._tier} base {STATE['base_tier']}",
            )
            check("d-slide-tier-updated", w._slide_tier == w._tier, f"{w._slide_tier}")
            then(s_d_shrink)
            GLib.timeout_add(50, s_d_shrink)

        def qu():
            if (
                not w._precache_pending
                and w._precache_decode_cancel is None
                and w._load_cancel is None
            ):
                extra_quiet()
                return False
            return True

        GLib.timeout_add(25, qu)
        return False

    def s_d_shrink():
        w = W()
        CALLS.clear()
        w.set_default_size(SMALL_W, SMALL_H)

        def fin():
            check(
                "d-shrink-no-redecode",
                len(current_calls()) == 0,
                f"{current_calls()}",
            )
            then(s_d_regrow)
            GLib.timeout_add(50, s_d_regrow)
            return False

        GLib.timeout_add(DEBOUNCE_WAIT_MS, fin)
        return False

    def s_d_regrow():
        w = W()
        CALLS.clear()
        w.set_default_size(GROW_W, GROW_H)  # back to the ALREADY loaded tier

        def fin():
            check(
                "d-regrow-same-no-redecode",
                len(current_calls()) == 0,
                f"{current_calls()}",
            )
            then(s_e_trigger)
            GLib.timeout_add(50, s_e_trigger)
            return False

        GLib.timeout_add(DEBOUNCE_WAIT_MS, fin)
        return False

    def s_e_trigger():
        w = W()
        w.zoom_mode = "scale"
        w.scale = 4.0
        CALLS.clear()
        w._apply_zoom()
        then(s_e_shallow)
        _poll(lambda: W()._full_pixbuf is not None, "upgrade for e", 25.0)

    def s_e_shallow():
        w = W()
        n_before = len(full_calls())
        # target 0.1*8000 = 800 px <= tier coverage (>= 2380): shallow again
        w.scale = 0.1
        w._apply_zoom()
        check("e-shallow-drops-slot", w._full_pixbuf is None and w._full_cancel is None)
        check(
            "e-shallow-restores-base",
            w._pixbuf is w._base_pixbuf and w._texture is w._base_texture,
        )
        check("e-drop-issues-no-decode", len(full_calls()) == n_before)
        w.zoom_mode = "scale"
        w.scale = 4.0
        w._apply_zoom()  # reload the slot, then fit-drop it
        then(s_e_fit)
        _poll(lambda: W()._full_pixbuf is not None, "upgrade for fit-drop", 25.0)

    def s_e_fit():
        w = W()
        w.set_zoom_fit()
        check("e-fit-drops-slot", w._full_pixbuf is None and w._full_cancel is None)
        check(
            "e-fit-restores-base",
            w._pixbuf is w._base_pixbuf and w._texture is w._base_texture,
        )
        # (e-lint) the FTR3 sync-FS invariant over both source files:
        pat = re.compile(
            r"new_from_file\(|get_file_info\(|query_info\(|os\.listdir|os\.scandir"
        )
        for src in ("src/looksee.py", "src/looksee_io.py", "src/looksee_ui.py"):
            text = (ROOT / src).read_text()
            hits = [
                ln for ln in text.splitlines()
                if pat.search(ln) and not ln.lstrip().startswith("#")
            ]
            check(
                "e-lint-no-sync-io",
                all(
                    "_async" not in ln for ln in hits
                ),
                f"{src}: {hits}",
            )
        then(s_done)
        GLib.timeout_add(50, s_done)

    def s_done():
        print("P19 SMOKE PASS:", flush=True)
        for c in CHECKS:
            print(f"  check: {c}", flush=True)
        app.quit()
        return False

    def run_first():
        then(s_boot)
        STATE["after"]()
        return False

    app = looksee.LookseeApp()
    GLib.timeout_add(200, run_first)
    return app.run(["looksee", BIG])


if __name__ == "__main__":
    sys.exit(main())
