#!/usr/bin/env python3
"""P14 gate driver: async slide load (FTR3, PLAN §15.2 P14).

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p14_asyncload.py

Slow IO is SIMULATED in-process: Gio.File.read_async is wrapped with an
instrumented shim that starts the REAL async op immediately but delivers the
app's callback only after a GLib.timeout delay — exactly the shape of a slow
mount (op runs on GIO's task pool, completion lands later on the loop).
The shim records every call (path, io priority, cancellable, timings).

Asserted flows:
  (a) no event-loop block while a load is pending: a 10 ms heartbeat keeps
      firing (gap < 150 ms, >= 10 beats inside the >= 500 ms pending window)
      while open_path's decode is in flight — a main-loop-blocking decode
      would stall the beats for the whole delay;
  (b) paint-on-arrival: nav updates current_path/title instantly, the old
      slide stays visible (_painted_key unchanged, same visible stack child),
      and the new picture + cache merge + subtitle appear exactly on arrival;
  (c) superseded-completion drop: nav A -> nav B while A pending; A's late
      completion paints NOTHING (guard compares current_path at finish);
  (d) rapid nav: five back-to-back open_paths leave exactly ONE live load
      cancellable — the four older ones are cancelled — no retry storm, and
      the final slide still arrives;
  (e) look-ahead cache hit paints SYNCHRONOUSLY with ZERO IO (no new
      read_async call at all, same pixbuf object, paint in the same tick).
Repo-local LOOKSEE_CONFIG_DIR keeps it clean; album is a scratch copy.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["LOOKSEE_CONFIG_DIR"] = str(ROOT / "scratch" / "smoke-p14-config")
shutil.rmtree(os.environ["LOOKSEE_CONFIG_DIR"], ignore_errors=True)

_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

from gi.repository import Adw, Gio, GLib  # noqa: E402

STEP_MS = 300
POLL_MS = 100
POLL_BUDGET_S = 8.0
CHECKS: list[str] = []

TESTDATA = [str(ROOT / "testdata" / f"{i}.png") for i in range(1, 13)]
ALBUM = ROOT / "scratch" / "p14-album"
shutil.rmtree(ALBUM, ignore_errors=True)
ALBUM.mkdir(parents=True)
for _src in TESTDATA:
    shutil.copy2(_src, ALBUM / os.path.basename(_src))
A = [str(ALBUM / f"{i}.png") for i in range(1, 13)]


def now_ms() -> int:
    return GLib.get_monotonic_time() // 1000


# --- instrumented slow-IO shim ---------------------------------------------
REAL_READ_ASYNC = Gio.File.read_async
SLOW = {"ms": 0}
CALLS: list[dict] = []


def slow_read_async(self, io_priority, cancellable, callback, *user_data):
    rec = {
        "path": self.get_path(),
        "prio": io_priority,
        "issued": now_ms(),
        "delivered": None,
        "cancel": cancellable,
        "delay": SLOW["ms"],
    }
    CALLS.append(rec)

    def inner(src, res):  # real op completed on the task pool
        def deliver():
            rec["delivered"] = now_ms()
            callback(src, res)
            return False

        if rec["delay"] > 0:
            GLib.timeout_add(rec["delay"], deliver)
        else:
            deliver()

    REAL_READ_ASYNC(self, io_priority, cancellable, inner)


Gio.File.read_async = slow_read_async


def fail(label: str, detail: str = "") -> None:
    print(f"P14 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p14-smoke")
    app = looksee.LookseeApp()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def W():
        return app.window

    def key(p: str) -> str:
        return os.path.abspath(p)

    def base() -> str:
        return os.path.basename(W().current_path or "")

    def painted() -> str | None:
        return W()._painted_key

    def visible() -> str | None:
        return W().stack.get_visible_child_name()

    def calls_for(paths: list[str], slide_only: bool = False) -> list[dict]:
        want = {key(p) for p in paths}
        return [
            r
            for r in CALLS
            if r["path"] in want
            and (not slide_only or r["prio"] == GLib.PRIORITY_DEFAULT)
        ]

    def demote(path: str) -> None:
        """Force-cold a window key (same seam the P7 driver uses)."""
        W()._precache.pop(key(path), None)
        W()._precache_charges.pop(key(path), None)

    state: dict = {}

    def s_ready():
        check("initial-async-arrive", painted() == key(A[0]), str(painted()))
        check("initial-title", W()._title_label.get_text() == "1.png")
        check("initial-visible", visible() is not None)

    def s_case_a():
        """Slow nav to a forced-cold slide: loop stays alive; paint waits."""
        demote(A[11])
        state["painted_before"] = painted()
        state["vis_before"] = visible()
        SLOW["ms"] = 700
        state["beats"] = []

        def beat():
            state["beats"].append(now_ms())
            return True

        state["beat_id"] = GLib.timeout_add(10, beat)
        state["t_nav"] = now_ms()
        W().open_path(A[11])
        check("nav-path-instant", W().current_path == A[11], base())
        check("nav-title-instant", W()._title_label.get_text() == "12.png")
        check(
            "nav-old-slide-stays-visible",
            painted() == state["painted_before"] and visible() == state["vis_before"],
            f"{painted()} {visible()}",
        )
        fresh = calls_for([A[11]])
        check(
            "slide-load-issued-async",
            bool(fresh)
            and fresh[-1]["prio"] == GLib.PRIORITY_DEFAULT
            and fresh[-1]["delivered"] is None,
            str(fresh[-3:]),
        )

    def s_case_a_done():
        GLib.source_remove(state["beat_id"])
        t_arr = now_ms()
        beats = [t for t in state["beats"] if state["t_nav"] <= t <= t_arr]
        gaps = [b - a for a, b in zip(beats, beats[1:])] or [0]
        check("pending-window-real", t_arr - state["t_nav"] >= 500,
              f"{t_arr - state['t_nav']}ms")
        check("loop-heartbeat-alive", len(beats) >= 10, str(len(beats)))
        check("loop-never-blocked", max(gaps) < 150, f"max gap {max(gaps)}ms")
        check("arrival-painted", painted() == key(A[11]), str(painted()))
        check(
            "arrival-merges-cache",
            W()._precache.get(key(A[11])) is W()._pixbuf,
            "cold arrival not merged as the i entry",
        )
        check("arrival-visible-swapped", visible() != state["vis_before"])
        sub = W()._subtitle_label.get_text()
        check("arrival-subtitle-index", sub.endswith("(12/12)"), sub)

    def s_case_c_first():
        """Nav A while cold; nav B 250 ms later (A still pending)."""
        demote(A[7])
        demote(A[8])
        SLOW["ms"] = 900
        state["vis_a"] = visible()
        W().open_path(A[7])
        check("c-nav-A-current", W().current_path == A[7], base())
        GLib.timeout_add(250, s_case_c_second)
        return False

    def s_case_c_second():
        a_rec = calls_for([A[7]])[-1]
        check("c-A-still-pending", a_rec["delivered"] is None)
        state["a_cancel"] = a_rec["cancel"]
        W().open_path(A[8])
        check(
            "c-new-nav-cancels-old",
            state["a_cancel"].is_cancelled(),
            "pending load's cancellable not cancelled by new nav",
        )
        return False

    def s_case_c_wait_A_delivered():
        return calls_for([A[7]])[-1]["delivered"] is not None

    def s_case_c_guard():
        """A's completion has DELIVERED while B is still pending: the guard
        must have dropped it — A never painted, old slide (12.png) still up."""
        check("c-A-delivered", calls_for([A[7]])[-1]["delivered"] is not None)
        check(
            "c-stale-completion-dropped",
            painted() == key(A[11]),
            f"painted={painted()} — superseded load painted!",
        )
        check("c-old-slide-still-up", visible() == state["vis_a"])

    def s_case_c_arrive():
        check("c-newer-arrives-painted", painted() == key(A[8]), str(painted()))

    def s_case_d_burst():
        """Five rapid cold navs: exactly one live load, four cancelled."""
        targets = [A[0], A[1], A[2], A[3], A[4]]
        for p in targets:
            demote(p)
        SLOW["ms"] = 900
        state["d_base"] = len(CALLS)
        for p in targets:
            W().open_path(p)
        recs = [
            r
            for r in CALLS[state["d_base"] :]
            if r["path"] in {key(p) for p in targets}
            and r["prio"] == GLib.PRIORITY_DEFAULT
        ]  # burst-window slide loads only (boot's own open predates d_base)
        check("d-no-retry-storm", len(recs) == 5, f"{len(recs)} slide loads issued")
        cancelled = [r["cancel"].is_cancelled() for r in recs]
        check(
            "d-old-loads-cancelled",
            all(cancelled[:-1]) and not cancelled[-1],
            f"cancelled={cancelled}",
        )
        check("d-live-load-is-last", recs[-1]["cancel"] is W()._load_cancel)

    def s_case_d_arrive():
        check("d-final-arrival-painted", painted() == key(A[4]), str(painted()))

    def s_case_e_prep():
        """One-shot: demote the keep-set around 5.png; the pump refills it
        at zero simulated delay."""
        SLOW["ms"] = 0
        for p in (A[5], A[6], A[3]):
            W()._precache.pop(key(p), None)
            W()._precache_charges.pop(key(p), None)
        W()._plan_precache()  # re-queue the demoted keys for the pump

    def e_filled() -> bool:
        """PURE poll predicate (side-effecting preds can never settle)."""
        return all(key(p) in W()._precache for p in (A[5], A[6], A[3]))

    def s_case_e_hit():
        SLOW["ms"] = 900  # any IO on this step would hang the check
        n_before = len(CALLS)
        cached = W()._precache.get(key(A[5]))
        check("e-hit-target-cached", cached is not None)
        W().step(1)
        check("e-zero-io-issued", len(CALLS) == n_before,
              f"{len(CALLS) - n_before} read_async calls")
        check("e-hit-paints-synchronously", painted() == key(A[5]), str(painted()))
        check("e-hit-same-pixbuf", W()._pixbuf is cached)
        SLOW["ms"] = 0

    # (action, predicate): predicate None -> act now; else wait, THEN act.
    steps = [
        (s_ready, lambda: painted() == key(A[0])),
        (s_case_a, None),
        (s_case_a_done, lambda: painted() == key(A[11])),
        (s_case_c_first, None),
        (s_case_c_guard, s_case_c_wait_A_delivered),
        (s_case_c_arrive, lambda: painted() == key(A[8])),
        (s_case_d_burst, None),
        (s_case_d_arrive, lambda: painted() == key(A[4])),
        (s_case_e_prep, None),
        (None, e_filled),
        (s_case_e_hit, None),
    ]

    def run_next():
        try:
            if not steps:
                Gio.File.read_async = REAL_READ_ASYNC
                print(
                    f"P14 SMOKE OK ({len(CHECKS)} checks)",
                    file=sys.stderr,
                    flush=True,
                )
                app.quit()
                return False
            step, pred = steps.pop(0)
            if step is None:  # pure wait entry
                def on_ready():
                    GLib.timeout_add(STEP_MS, run_next)
                    return False

                _poll(pred, on_ready, "wait")
            elif pred is None:
                step()
                GLib.timeout_add(STEP_MS, run_next)
            else:
                def on_arrived():
                    step()
                    GLib.timeout_add(STEP_MS, run_next)
                    return False

                _poll(pred, on_arrived, step.__name__)
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            os._exit(1)
        return False

    def _poll(pred, cont, why):
        deadline = [GLib.get_monotonic_time() / 1e6 + POLL_BUDGET_S]

        def tick():
            if pred():
                cont()
                return False
            if GLib.get_monotonic_time() / 1e6 > deadline[0]:
                fail("poll-timeout", why)
            GLib.timeout_add(POLL_MS, tick)
            return False

        GLib.timeout_add(POLL_MS, tick)

    SLOW["ms"] = 500  # the initial open itself rides the async path
    GLib.timeout_add(300, lambda: (run_next(), False)[1])
    return app.run(["looksee", A[0]])


if __name__ == "__main__":
    raise SystemExit(main())
