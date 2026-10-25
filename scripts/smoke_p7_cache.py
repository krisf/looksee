#!/usr/bin/env python3
"""P7+P8 gate driver: look-ahead pre-render cache (PLAN §11.2 P7/P8).

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p7_cache.py

Open testdata/1.png under the sanctioned xvfb recipe, let the PRIORITY_LOW
idle pump settle, then step +/- through the folder asserting at every hop:
cache keys == keep-set (i, i+1, i+2, i-1 with wrap), forward/back revisits
consume the SAME cached pixbuf object (no second decode), the dict never
holds a key outside the window at any sample point, and rapid navs neither
grow the cache nor crash.  P8 cleanliness adds the wrap-frontier crossing,
a forced first-visit-cold decode that must merge, folder-shrink churn on a
scratch album copy (rescan drops exactly the deleted keys and re-plans),
sampled 10-step rapid churn, and teardown invalidation on close-request.
Repo-local LOOKSEE_CONFIG_DIR keeps it clean.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["LOOKSEE_CONFIG_DIR"] = str(ROOT / "scratch" / "smoke-p7-config")
shutil.rmtree(os.environ["LOOKSEE_CONFIG_DIR"], ignore_errors=True)

_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

from gi.repository import Adw, GLib  # noqa: E402

STEP_MS = 350
POLL_MS = 150
POLL_BUDGET_S = 4.0
CHECKS: list[str] = []

TESTDATA = [str(ROOT / "testdata" / f"{i}.png") for i in range(1, 13)]

# P8 churn arena: scratch copy of the album — the committed testdata must
# never be mutated (same semantics, repo-clean).
ALBUM = ROOT / "scratch" / "p8-album"
shutil.rmtree(ALBUM, ignore_errors=True)
ALBUM.mkdir(parents=True)
for _src in TESTDATA:
    shutil.copy2(_src, ALBUM / os.path.basename(_src))
A = [str(ALBUM / f"{i}.png") for i in range(1, 13)]


def expect(idx: int, n: int = 12) -> set[str]:
    """Keep-set at *idx* over TESTDATA — independent re-implementation of
    the PLAN §11.1 window rule (i, i+1, i+2, i-1 modulo N), NOT
    looksee.window_paths, so the smoke cross-checks the app."""
    return {TESTDATA[(idx + off) % n] for off in (0, 1, 2, -1)}


def aexpect(idx: int, files: list[str]) -> set[str]:
    """Same keep-set rule over an arbitrary file list (album churn)."""
    return {files[(idx + off) % len(files)] for off in (0, 1, 2, -1)}


def fail(label: str, detail: str = "") -> None:
    print(f"P7 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p7-smoke")
    app = looksee.LookseeApp()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def W():
        return app.window

    def keys() -> set[str]:
        return set(W()._precache)

    def settled(idx: int):
        return lambda: keys() == expect(idx)

    def s_merged_current():
        win = W()
        check(
            "open-merges-current",
            win._precache.get(TESTDATA[0]) is win._pixbuf,
            "cold decode not merged into cache as the i entry",
        )
        check("window-eq-index0", keys() == expect(0), str(sorted(keys())))

    def s_hit_forward():
        win = W()
        cached = win._precache.get(TESTDATA[1])
        check("fwd-entry-cached", cached is not None)
        win.step(1)
        check(
            "fwd-hit-same-pixbuf",
            win._pixbuf is cached,
            "second visit re-decoded instead of consuming the cache",
        )
        check("prune-at-nav-index1", keys() <= expect(1), str(sorted(keys())))

    def s_hit_back():
        win = W()
        cached = win._precache.get(TESTDATA[0])  # the -1 slot of window@1
        check("back-entry-cached", cached is not None)
        win.step(-1)
        check("back-hit-same-pixbuf", win._pixbuf is cached)
        check("prune-at-nav-index0", keys() <= expect(0), str(sorted(keys())))

    def s_wrap():
        win = W()
        win.step(-1)  # 1.png -> 12.png across the wrap frontier
        check("wrap-index", win.index == 11, f"index={win.index}")
        check("prune-at-wrap", keys() <= expect(11), str(sorted(keys())))

    def s_rapid():
        win = W()
        for n in range(10):
            win.step(1)
            check(
                f"rapid-{n}-in-window",
                keys() <= expect(win.index),
                f"index={win.index} keys={sorted(keys())}",
            )

    def s_rapid_settled():
        win = W()
        check("rapid-final-index", win.index == 9, f"index={win.index}")
        check("window-eq-after-rapid", keys() == expect(9), str(sorted(keys())))
        check(
            "pump-quiet-when-settled",
            win._precache_pump_id == 0 and not win._precache_pending,
            f"pump={win._precache_pump_id} pending={win._precache_pending}",
        )

    # ---- P8 cleanliness ------------------------------------------------------

    def s_frontier_fwd():
        win = W()
        win.open_path(TESTDATA[11])  # deterministic land on the wrap head
        check("frontier-land-index", win.index == 11, f"index={win.index}")
        win.step(1)  # 12.png -> 1.png across the wrap frontier
        check("frontier-cross-index", win.index == 0, f"index={win.index}")
        check("frontier-cross-prune", keys() <= expect(0), str(sorted(keys())))

    def s_cold_first_visit():
        win = W()
        victim = TESTDATA[1]  # cached +2 slot; evict it to force a cold hop
        stale = win._precache.pop(victim, None)
        win._precache_charges.pop(victim, None)
        check("cold-victim-removed", stale is not None)
        before = win._pixbuf  # the slide on screen right now
        win.step(1)  # lands on the victim with no cache entry -> cold path
        # FTR3 (P14): the cold decode is ASYNC — the previous slide stays
        # visible (the exact same pixbuf/texture) while it is in flight;
        # nothing decodes synchronously on nav anymore.  Merge checks run at
        # arrival below.
        check("cold-prev-slide-preserved", win._pixbuf is before)
        check("cold-load-really-cold", stale is not before)
        check("cold-nav-prune", keys() <= expect(1), str(sorted(keys())))

    def s_cold_arrived():
        victim = TESTDATA[1]
        win = W()  # settled(1) already implies the arrival (window refilled)
        check("cold-arrival-painted", win._painted_key == victim,
              str(win._painted_key))
        check(
            "cold-merge-as-i-entry",
            win._precache.get(victim) is win._pixbuf,
            "cold decode not merged into the cache",
        )

    def s_shrink_adopt():
        # FTR3 (P15): the folder listing is ASYNC now — open kicks it and a
        # poll waits for the atomic swap-in before the check.
        W().open_path(A[0])

    def p_adopted():
        win = W()
        return win.folder == str(ALBUM) and win.paths == A

    def s_shrink_adopted():
        win = W()
        check(
            "album-adopted",
            p_adopted(),
            f"folder={win.folder} paths={len(win.paths)}",
        )

    def settled_album(idx: int, files: list[str]):
        return lambda: keys() == aexpect(idx, files)

    def s_shrink_delete():
        # Delete 3 of the 12 album copies — all three sit in the current
        # cache window {A1,A2,A3,A12}; the monitor-driven rescan must drop
        # exactly these keys.
        for p in (A[1], A[2], A[11]):
            os.remove(p)
        DELETED.extend((A[1], A[2], A[11]))

    def p_rescanned():
        return len(W().paths) == 9

    def s_shrink_check():
        gone = keys() & set(DELETED)
        check("shrink-drops-exactly-deleted", not gone, str(sorted(gone)))
        names = [os.path.basename(p) for p in W().paths]
        check(
            "shrink-scan-rebuilt",
            names == ["1.png", "4.png", "5.png", "6.png", "7.png",
                      "8.png", "9.png", "10.png", "11.png"],
            str(names),
        )
        check("shrink-current-survives", W().current_path == A[0])

    def s_shrink_refilled():
        win = W()
        remaining = [p for p in A if p not in set(DELETED)]
        check("shrink-window-refilled", keys() == aexpect(0, remaining),
              str(sorted(keys())))
        check("shrink-index-kept", win.index == 0, f"index={win.index}")

    def s_teardown():
        win = W()
        win.emit("close-request")  # the existing teardown hook
        check(
            "teardown-invalidates-cache",
            not win._precache
            and not win._precache_charges
            and not win._precache_pending
            and win._precache_pump_id == 0,
            f"keys={sorted(keys())} pump={win._precache_pump_id}",
        )

    DELETED: list[str] = []
    remaining_final = [p for p in A if p not in {A[1], A[2], A[11]}]

    steps: list = [
        ("poll", settled(0), "pump never filled the start window (i,+1,+2,-1)"),
        s_merged_current,
        s_hit_forward,
        ("poll", settled(1), "pump never filled window at index 1"),
        s_hit_back,
        ("poll", settled(0), "pump never refilled window at index 0"),
        s_wrap,
        ("poll", settled(11), "pump never filled the wrap window"),
        s_rapid,
        ("poll", settled(9), "pump never settled after 10 rapid steps"),
        s_rapid_settled,
        s_frontier_fwd,
        ("poll", settled(0), "pump never refilled after frontier crossing"),
        s_cold_first_visit,
        ("poll", settled(1), "pump never refilled after cold visit"),
        s_shrink_adopt,
        ("poll", p_adopted, "album listing never swapped in"),
        s_shrink_adopted,
        ("poll", settled_album(0, A), "pump never filled the album window"),
        s_shrink_delete,
        ("poll", p_rescanned, "folder-shrink rescan never ran"),
        s_shrink_check,
        ("poll", settled_album(0, remaining_final),
         "cache never re-planned after shrink churn"),
        s_shrink_refilled,
        s_teardown,
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
            print("P7 SMOKE PASS:", *CHECKS, sep="\n  check: ", flush=True)
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
    return app.run(["looksee", TESTDATA[0]])


if __name__ == "__main__":
    raise SystemExit(main())
