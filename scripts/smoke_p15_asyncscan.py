#!/usr/bin/env python3
"""P15 gate driver: async folder listing (FTR3, PLAN §15.2 P15).

    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p15_asyncscan.py

Slow enumeration is SIMULATED in-process exactly like the P14 driver
simulated slow reads: the real GLib async op starts immediately but the
app's callback is re-delivered after a GLib.timeout delay — the shape of
a restic/rclone mount answering seconds later.  Shimmed seams:
Gio.File.enumerate_children_async, Gio.FileEnumerator.next_files_async,
Gio.File.query_info_async (mtime subtitle) and
GdkPixbuf.Pixbuf.get_file_info_async (orientation header probe).

Asserted flows:
  (a) boot: the file-vs-dir probe itself is async (do_open never sync-stats);
      a 10 ms heartbeat keeps firing across the pending window (max gap
      < 150 ms) and the first slide still arrives;
  (b) slow folder open: title/chrome update INSTANTLY, the stale listing +
      the visible slide stay untouched while enumeration is in flight, the
      loop never blocks, batches are consumed via next_files_async, and the
      swap-in is atomic (paths, current, subtitle (1/12) all correct in one
      step — no half-listings ever observed);
  (c) superseded listings: two back-to-back open_folders cancel the first
      cycle's cancellable, issue exactly ONE enumerate per cycle (no retry
      storm) and the LAST request wins the listing;
  (d) rename-in-folder (monitor-driven rescan through the same async
      cycle): the current slide keeps painting the exact same pixbuf, the
      listing updates, the index re-anchors, the loop stays responsive;
  (e) subtitle two-beat: the (i/N) part is instant, the mtime part merges
      in when query_info_async answers seconds later;
  (f) orientation probe async: a filtered tick with unknown dimensions
      kicks budget-sized async header probes (budget == ORIENT_PROBE_BUDGET
      == 8, never more), does NOT blindly land on an unconfirmed slide,
      DEFERS the zero-match auto-stop until probes land — then stops with
      the original slide kept.
Repo-local LOOKSEE_CONFIG_DIR keeps it clean; albums live under scratch/.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import struct
import sys
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ["LOOKSEE_CONFIG_DIR"] = str(ROOT / "scratch" / "smoke-p15-config")
shutil.rmtree(os.environ["LOOKSEE_CONFIG_DIR"], ignore_errors=True)

_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)

from gi.repository import Adw, GdkPixbuf, Gio, GLib  # noqa: E402

STEP_MS = 300
POLL_MS = 100
POLL_BUDGET_S = 10.0
CHECKS: list[str] = []

TESTDATA = [str(ROOT / "testdata" / f"{i}.png") for i in range(1, 13)]


def copy_album(name: str) -> tuple[pathlib.Path, list[str]]:
    folder = ROOT / "scratch" / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for src in TESTDATA:
        shutil.copy2(src, folder / os.path.basename(src))
    return folder, [str(folder / f"{i}.png") for i in range(1, 13)]


ALBUM, A = copy_album("p15-album")
ALBUM2, B = copy_album("p15-album2")


def write_png(path: str, w: int, h: int) -> None:
    """Minimal 8-bit RGB PNG (p8 driver's writer — sizes are what the
    orientation filter sees)."""

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


ORIENT = ROOT / "scratch" / "p15-orient"
shutil.rmtree(ORIENT, ignore_errors=True)
ORIENT.mkdir(parents=True)
for _i in range(1, 10):  # NINE squares: 8 unknowns + the known current
    write_png(str(ORIENT / f"{_i}.png"), 70, 70)
SQ = [str(ORIENT / f"{i}.png") for i in range(1, 10)]

# --- instrumented slow-IO shims (P14 pattern: real op starts now, the
# --- app's callback is re-delivered after `delay` ms on the main loop) ------

REAL_ENUM = Gio.File.enumerate_children_async
REAL_NEXT = Gio.FileEnumerator.next_files_async
REAL_QI = Gio.File.query_info_async
REAL_HDR = GdkPixbuf.Pixbuf.get_file_info_async
ENUM_CALLS: list[dict] = []
NEXT_CALLS: list[dict] = []
QI_CALLS: list[dict] = []
HDR_CALLS: list[dict] = []
SLOW = {"enum": 0, "batch": 0, "mtime": 0, "hdr": 0}


def now_ms() -> int:
    return GLib.get_monotonic_time() // 1000


def delayed(rec: dict, callback, src, res) -> None:
    """Re-deliver the app's callback after the recorded delay (0 = at once,
    like a healthy mount)."""
    if rec["delay"] > 0:
        def go():
            rec["delivered"] = now_ms()
            callback(src, res)
            return False

        GLib.timeout_add(rec["delay"], go)
    else:
        rec["delivered"] = now_ms()
        callback(src, res)


def slow_enum(self, attrs, flags, io_priority, cancellable, callback):
    rec = {
        "path": self.get_path(), "attrs": attrs, "prio": io_priority,
        "cancel": cancellable, "issued": now_ms(), "delivered": None,
        "delay": SLOW["enum"],
    }
    ENUM_CALLS.append(rec)
    REAL_ENUM(self, attrs, flags, io_priority, cancellable,
              lambda src, res: delayed(rec, callback, src, res))


def slow_next(self, num_files, io_priority, cancellable, callback):
    rec = {
        "num": num_files, "prio": io_priority, "cancel": cancellable,
        "issued": now_ms(), "delivered": None, "delay": SLOW["batch"],
    }
    NEXT_CALLS.append(rec)
    REAL_NEXT(self, num_files, io_priority, cancellable,
              lambda src, res: delayed(rec, callback, src, res))


def slow_qi(self, attrs, flags, io_priority, cancellable, callback):
    rec = {
        "path": self.get_path(), "attrs": attrs, "prio": io_priority,
        "issued": now_ms(), "delivered": None, "delay": SLOW["mtime"],
    }
    QI_CALLS.append(rec)
    REAL_QI(self, attrs, flags, io_priority, cancellable,
            lambda src, res: delayed(rec, callback, src, res))


def slow_hdr(filename, cancellable, callback):  # static slot
    rec = {
        "path": filename, "issued": now_ms(), "delivered": None,
        "delay": SLOW["hdr"], "cancel": cancellable,
    }
    HDR_CALLS.append(rec)
    REAL_HDR(filename, cancellable,
             lambda src, res: delayed(rec, callback, src, res))


Gio.File.enumerate_children_async = slow_enum
Gio.FileEnumerator.next_files_async = slow_next
Gio.File.query_info_async = slow_qi
GdkPixbuf.Pixbuf.get_file_info_async = slow_hdr

HB: list[int] = []
HB_ID = {"id": 0}


def hb_start() -> None:
    HB.clear()

    def beat():
        HB.append(now_ms())
        return True

    HB_ID["id"] = GLib.timeout_add(10, beat)


def hb_stop() -> list[int]:
    if HB_ID["id"]:
        GLib.source_remove(HB_ID["id"])
        HB_ID["id"] = 0
    return list(HB)


def beat_stats(lo: int, hi: int):
    beats = [t for t in HB if lo <= t <= hi]
    gaps = [b - a for a, b in zip(beats, beats[1:])] or [0]
    return beats, max(gaps)


def fail(label: str, detail: str = "") -> None:
    print(f"P15 SMOKE FAIL: {label} {detail}".rstrip(), file=sys.stderr, flush=True)
    os._exit(1)


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        fail(label, detail)
    CHECKS.append(label)


def restore_real() -> None:
    Gio.File.enumerate_children_async = REAL_ENUM
    Gio.FileEnumerator.next_files_async = REAL_NEXT
    Gio.File.query_info_async = REAL_QI
    GdkPixbuf.Pixbuf.get_file_info_async = REAL_HDR


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee-p15-smoke")
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

    def sub() -> str:
        return W()._subtitle_label.get_text()

    def zero_deliveries(calls: list[dict], from_i: int) -> bool:
        return all(r["delivered"] is None for r in calls[from_i:])

    state: dict = {}

    # ---- (a) boot: file-vs-dir probe is async; loop alive throughout ----

    def s_boot_hb():
        # SLOW was already armed (800 ms) before app.run so the boot
        # open itself rides the slow path.
        state["t0"] = now_ms()
        hb_start()

    def s_boot_check():
        t = now_ms()
        beats, gap = beat_stats(state["t0"], t)
        check("boot-probe-window-real", t - state["t0"] >= 500, f"{t - state['t0']}ms")
        check("boot-loop-never-blocked", len(beats) >= 10 and gap < 150,
              f"beats={len(beats)} gap={gap}")
        check("boot-slide-arrived", painted() == key(A[0]), str(painted()))
        check("boot-title", W()._title_label.get_text() == "1.png")

    # ---- (b) slow folder open: instant chrome, atomic swap --------------

    def p_b_landed() -> bool:
        return W().paths == B and painted() == key(B[0])

    def s_scan_open():
        check("boot-listing-swapped", W().paths == A, f"paths={len(W().paths)}")
        SLOW["enum"] = 400
        SLOW["batch"] = 400
        state["paint_before"] = painted()
        state["n_enum0"] = len(ENUM_CALLS)
        state["n_next0"] = len(NEXT_CALLS)
        state["t0"] = now_ms()
        hb_start()
        W().open_folder(str(ALBUM2))
        check("scan-title-instant",
              W()._title_label.get_text() == ALBUM2.name)
        # The header honestly says "nothing open yet" (current_path cleared),
        # but the VISIBLE slide is the old one — it stays until the first
        # picture of the new folder arrives (P14 contract).
        check("scan-current-cleared", W().current_path is None)
        check("scan-stale-view-kept", painted() == state["paint_before"])
        recs = ENUM_CALLS[state["n_enum0"]:]
        check(
            "scan-issued-async",
            len(recs) == 1
            and recs[-1]["delivered"] is None
            and recs[-1]["prio"] == GLib.PRIORITY_DEFAULT
            and recs[-1]["attrs"] == "standard::name,standard::type",
            str(recs),
        )

    def s_scan_done():
        t = now_ms()
        beats, gap = beat_stats(state["t0"], t)
        check("scan-window-real", t - state["t0"] >= 700, f"{t - state['t0']}ms")
        check("scan-loop-never-blocked", len(beats) >= 10 and gap < 150,
              f"beats={len(beats)} gap={gap}")
        batches = NEXT_CALLS[state["n_next0"]:]
        check("scan-batches-consumed",
              any(r["num"] >= 1 and r["delivered"] is not None for r in batches),
              str(batches))
        check("scan-atomic-paths", W().paths == B, f"paths={len(W().paths)}")
        check("scan-current-first", W().current_path == B[0], base())
        check("scan-slide-first", painted() == key(B[0]), str(painted()))
        check("scan-subtitle-total", sub().endswith("(1/12)"), sub())

    # ---- (c) superseded listings: cancel, no storm, last request wins ---

    def p_a_back() -> bool:
        return W().paths == A

    def s_burst_open():
        SLOW["enum"] = 1200
        SLOW["batch"] = 1200
        state["n_enum0"] = len(ENUM_CALLS)
        W().open_folder(str(ALBUM2))
        state["c1"] = W()._listing_cancel
        GLib.timeout_add(100, lambda: (W().open_folder(str(ALBUM)), False))

    def s_burst_check():
        check("burst-superseded-cycle-cancelled", state["c1"].is_cancelled())
        recs = ENUM_CALLS[state["n_enum0"]:]
        check("burst-one-enumerate-per-cycle", len(recs) == 2, str(len(recs)))
        # A finished cycle RETIRES its cancellable (None) — nothing stays
        # live beyond the swap; the superseded one was cancelled above.
        check("burst-cycle-retired-clean", W()._listing_cancel is None)
        check("burst-last-request-wins", W().paths == A, f"paths={len(W().paths)}")
        check("burst-current-first", W().current_path == A[0], base())

    # ---- (d) rename-in-folder: slow rescan keeps the visible slide ------

    def p_at(idx: int) -> bool:
        return painted() == key(A[idx]) and W().current_path == A[idx]

    def s_rename_nav():
        SLOW["enum"] = SLOW["batch"] = 0
        W().open_path(A[4])

    def p_rescan_wait() -> bool:
        return p_at(4) and W().paths == A

    def s_rename_act():
        SLOW["enum"] = 600
        SLOW["batch"] = 600
        state["pixbuf"] = W()._pixbuf
        state["n_next0"] = len(NEXT_CALLS)
        state["t0"] = now_ms()
        hb_start()
        os.rename(A[2], A[2] + ".bak")  # drops out of the supported set

    def p_renamed() -> bool:
        return W().paths == [p for p in A if p != A[2]]

    def s_rename_check():
        t = now_ms()
        beats, gap = beat_stats(state["t0"], t)
        check("rename-rescan-window-real", t - state["t0"] >= 500,
              f"{t - state['t0']}ms")
        check("rename-rescan-loop-never-blocked",
              len(beats) >= 10 and gap < 150, f"beats={len(beats)} gap={gap}")
        check("rename-rescan-via-async-batches",
              any(r["delivered"] is not None for r in NEXT_CALLS[state["n_next0"]:]))
        check("rename-keeps-current-slide",
              W()._pixbuf is state["pixbuf"] and painted() == key(A[4]))
        check("rename-listing-updated", p_renamed(), f"paths={len(W().paths)}")
        check("rename-index-re-anchored",
              W().index == W().paths.index(A[4]) == 3, str(W().index))
        SLOW["enum"] = SLOW["batch"] = 0
        os.rename(A[2] + ".bak", A[2])  # restore; monitor rescans (SLOW 0)

    def p_restored() -> bool:
        return W().paths == A

    # ---- (e) subtitle two-beat: (i/N) now, date when query_info lands ---

    def s_mtime_nav():
        SLOW["mtime"] = 700
        state["n_qi0"] = len(QI_CALLS)
        W().open_path(A[5])
        check("mtime-index-instant", sub() == "(6/12)", sub())
        recs = [r for r in QI_CALLS[state["n_qi0"]:] if r["path"] == key(A[5])]
        check(
            "mtime-issued-async",
            len(recs) == 1
            and recs[-1]["attrs"] == "time::modified"
            and recs[-1]["prio"] == GLib.PRIORITY_DEFAULT
            and recs[-1]["delivered"] is None,
            str(recs),
        )

    def p_mtime_merged() -> bool:
        return "\u00b7" in sub() and sub().endswith("(6/12)")

    def s_mtime_check():
        check("mtime-merged-on-arrival", p_mtime_merged(), sub())
        SLOW["mtime"] = 0

    # ---- (f) orientation probe: async, deferred stop, exact FTR2 stop ----

    def p_sq_open() -> bool:
        # FTR5 adaptation: the tiered-load pump (PRIORITY_LOW) now also
        # issues get_file_info_async header probes per decode.  The exact
        # == ORIENT_PROBE_BUDGET assert below only stays deterministic if
        # the pump has fully settled BEFORE the n_hdr0 snapshot — wait for
        # it here so the orient phase counts only orient's own probes.
        return (
            W().paths == SQ
            and painted() == key(SQ[0])
            and not W()._precache_pending
            and W()._precache_decode_cancel is None
        )

    def s_orient_play():
        W()._config["interval_sec"] = 0.3  # fast ticks for the smoke
        W()._orient_buttons["landscape"].set_active(True)  # real toggle path
        state["paint_before"] = painted()
        state["n_hdr0"] = len(HDR_CALLS)
        state["t0"] = now_ms()
        hb_start()
        SLOW["hdr"] = 600
        W().play_button.set_active(True)

    def s_orient_early():
        check("orient-no-blind-landing", painted() == state["paint_before"])
        check("orient-autostop-deferred-while-probing",
              W().play_button.get_active())
        probes = HDR_CALLS[state["n_hdr0"]:]
        check(
            "orient-probe-budget-capped",
            len(probes) == looksee.ORIENT_PROBE_BUDGET
            and all(r["delivered"] is None for r in probes),
            f"{len(probes)} probes issued",
        )

    def p_stopped() -> bool:
        return not W().play_button.get_active()

    def s_orient_stop():
        t = now_ms()
        beats, gap = beat_stats(state["t0"], t)
        check("orient-loop-never-blocked", len(beats) >= 15 and gap < 150,
              f"beats={len(beats)} gap={gap}")
        check("orient-probes-landed",
              all(r["delivered"] is not None for r in HDR_CALLS[state["n_hdr0"]:]))
        check("orient-autostopped-after-landing", not W().play_button.get_active())
        check("orient-stop-kept-slide", painted() == state["paint_before"])
        check("orient-filter-preserved",
              W()._config.get("slide_orientation") == "landscape")

    def s_sq_open():
        W().open_folder(str(ORIENT))

    # ---- runner (P14 protocol: (action|None, predicate|None) pairs) ------

    steps = [
        (s_boot_hb, None),
        (s_boot_check, lambda: painted() == key(A[0])),
        (None, lambda: W().paths == A),
        (s_scan_open, None),
        (s_scan_done, p_b_landed),
        (s_burst_open, None),
        (s_burst_check, p_a_back),
        (s_rename_nav, None),
        (None, p_rescan_wait),
        (s_rename_act, None),
        (s_rename_check, p_renamed),
        (None, p_restored),
        (s_mtime_nav, None),
        (s_mtime_check, p_mtime_merged),
        (None, lambda: W().paths == A and painted() == key(A[5])),
        (s_sq_open, None),
        (None, p_sq_open),
        (s_orient_play, None),
        (None, lambda: len(HDR_CALLS) - state["n_hdr0"] >= 8),
        (s_orient_early, None),
        (s_orient_stop, p_stopped),
    ]

    def run_next():
        try:
            if not steps:
                restore_real()
                print(
                    f"P15 SMOKE OK ({len(CHECKS)} checks)",
                    file=sys.stderr,
                    flush=True,
                )
                app.quit()
                return False
            step, pred = steps.pop(0)
            if step is None:  # pure wait entry
                _poll(pred, lambda: GLib.timeout_add(STEP_MS, run_next), "wait")
            elif pred is None:
                step()
                GLib.timeout_add(STEP_MS, run_next)
            else:
                def on_arrived():
                    step()
                    GLib.timeout_add(STEP_MS, run_next)
                    return False

                _poll(pred, on_arrived, getattr(step, "__name__", "step"))
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

    SLOW["enum"] = 800  # boot: do_open's dir-probe AND the boot listing
    SLOW["batch"] = 800  # both ride the slow path from the very first event
    GLib.timeout_add(300, lambda: (run_next(), False)[1])
    return app.run(["looksee", A[0]])


if __name__ == "__main__":
    raise SystemExit(main())
