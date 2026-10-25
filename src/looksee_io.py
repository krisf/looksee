#!/usr/bin/env python3
"""looksee_io — the pure/logic half of the FTR3 split (PLAN §15.0).

Constants, the natural-sort pipeline (sort_image_paths), config
persistence, FTR1 look-ahead window math, FTR2 orientation/auto-hide
predicates, zoom + transition + subtitle helpers — everything here is
deterministic and gi-free, so tests exercise it without a display.
The GUI (and the async IO engines that drive it) lives in
looksee_ui.py; both halves are re-exported by looksee.py, the entry
point the packaged wrapper and every test driver load."""
from __future__ import annotations

import json
import math
import os
import re

APP_ID = "pro.krisforbes.Looksee"

#: Extensions GdkPixbuf reliably decodes on stock Ubuntu (PLAN §2: HEIC is
#: NOT guaranteed — unknown formats are filtered out of folder scans).
SUPPORTED_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".tif", ".tiff", ".svg", ".svgz", ".avif",
}

# ---------------------------------------------------------------------------
# Pure helpers — unit-tested in tests/ without ever touching the GUI.
# ---------------------------------------------------------------------------

def is_supported(name: str) -> bool:
    """True if *name*'s extension is in SUPPORTED_EXTS (case-insensitive)."""
    return os.path.splitext(name)[1].lower() in SUPPORTED_EXTS


_NUM_RUN = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """Numeric-aware sort key: 'img2' < 'img10' (case-insensitive)."""
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.lower())
        for part in _NUM_RUN.split(name)
        if part != ""
    )


def sort_image_paths(folder: str, entries) -> list[str]:
    """The PURE natural-sort/filter pipeline (FTR3/P15): turns an iterable
    of ``(name, is_file)`` pairs into natural-sorted absolute paths of the
    supported images directly inside *folder* (top level only; dotfiles
    skipped; undecodable formats filtered by extension — PLAN §2 HEIC
    degradation).  The GUI feeds this from `next_files_async` batches
    (_listing_batch); string computation only — ZERO filesystem calls, the
    filesystem side (whoever produces the pairs) is always async."""
    names = sorted(
        (
            name
            for name, is_file in entries
            if is_file and not name.startswith(".") and is_supported(name)
        ),
        key=natural_key,
    )
    return [os.path.join(folder, name) for name in names]


#: Zoom contract (PLAN §4 hop 6): scale stays in [0.1, 8.0].
ZOOM_MIN = 0.1
ZOOM_MAX = 8.0
ZOOM_STEP = 1.25

#: Above this texture edge we stop pre-rescaling the pixbuf (memory guard)
#: and stretch via FILL + size request instead — still exact sizing.
TEXTURE_PX_CAP = 4096


def clamp_scale(value: float) -> float:
    """Zoom factor clamped to the contract range [ZOOM_MIN, ZOOM_MAX]."""
    return max(ZOOM_MIN, min(ZOOM_MAX, float(value)))


def zoom_step(scale: float, direction: int) -> float:
    """Multiply *scale* by ZOOM_STEP**direction, then clamp."""
    return clamp_scale(float(scale) * (ZOOM_STEP ** (1 if direction > 0 else -1)))


# ---------------------------------------------------------------------------
# Config (PLAN §4 hop 8 + §5 contract): JSON at ~/.config/looksee/config.json,
# corruption-tolerant, per-key fallbacks, defaults 5.0 / false.
# LOOKSEE_CONFIG_DIR overrides the directory (test/smoke hook — keeps CI and
# the headless smokes repo-local; the installed app never sets it).
# ---------------------------------------------------------------------------

CONFIG_FILE_NAME = "config.json"
CONFIG_DEFAULTS = {
    "interval_sec": 5.0,
    "animations": False,
    "slide_orientation": "all",
}
INTERVAL_MIN = 1.0
INTERVAL_MAX = 60.0


def config_dir(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    override = env.get("LOOKSEE_CONFIG_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".config", "looksee")


def config_path(env: dict | None = None) -> str:
    return os.path.join(config_dir(env), CONFIG_FILE_NAME)


def clamp_interval(value) -> float:
    """Interval contract: finite float clamped to [1.0, 60.0]."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return CONFIG_DEFAULTS["interval_sec"]
    if not math.isfinite(v):
        return CONFIG_DEFAULTS["interval_sec"]
    return max(INTERVAL_MIN, min(INTERVAL_MAX, v))


def load_config(path: str | None = None) -> dict:
    """Never raises: any problem (missing/corrupt/odd types) degrades to
    defaults, independently per key (PLAN §5)."""
    cfg = dict(CONFIG_DEFAULTS)
    try:
        with open(path if path is not None else config_path()) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    raw_iv = data.get("interval_sec")
    if isinstance(raw_iv, (int, float)) and not isinstance(raw_iv, bool):
        cfg["interval_sec"] = clamp_interval(raw_iv)
    raw_an = data.get("animations")
    if isinstance(raw_an, bool):
        cfg["animations"] = raw_an
    raw_so = data.get("slide_orientation")
    if isinstance(raw_so, str):
        cfg["slide_orientation"] = normalize_orientation(raw_so)
    return cfg


# ---------------------------------------------------------------------------
# Slideshow orientation filter (FTR2 / P11, PLAN §13.1.2). ONE schema diff:
# slide_orientation ∈ {"all","landscape","portrait"}, default "all". The
# filter is a SLIDESHOW-TICK policy only — manual nav (keys, wheel, buttons,
# open) stays unfiltered. Pure helpers; the GUI knows nothing about them.
# ---------------------------------------------------------------------------

ORIENT_VALUES = ("all", "landscape", "portrait")

#: Max NEW dimension probes (possible decodes) per filtered tick. Keeps a
#: 1000-photo album from decoding hundreds of files in one timer fire; the
#: unfinished scan simply resumes on the next tick (no false auto-stop).
ORIENT_PROBE_BUDGET = 8


# ---------------------------------------------------------------------------
# Chrome auto-hide (FTR2 / P12, PLAN §13.1.3): while the slideshow PLAYS and
# the pointer has been idle for HIDE_AFTER_MS, the headerbar retracts via
# Adw.ToolbarView.set_reveal_top_bars(False) — the one and only mechanism.
# ---------------------------------------------------------------------------

HIDE_AFTER_MS = 5000


def chrome_should_hide(now_ms: int, last_activity_ms: int, playing: bool) -> bool:
    """Pure countdown decider: hide only while playing and once
    ``now - last_activity`` has reached the full 5 s.  Stopped/paused
    playback keeps chrome visible forever; a backwards clock delta hides
    nothing."""
    if not playing:
        return False
    return (now_ms - last_activity_ms) >= HIDE_AFTER_MS


def normalize_orientation(value) -> str:
    """Contract coercion: str → strip().lower(); anything unknown degrades
    to 'all' (the plan's unknown-value fallback)."""
    s = str(value).strip().lower()
    return s if s in ORIENT_VALUES else "all"


def img_matches_orientation(w: int, h: int, want: str) -> bool:
    """'all' matches everything; 'landscape' is w > h; 'portrait' is h > w.
    A SQUARE (w == h) matches NEITHER landscape nor portrait. Unknown want
    behaves like 'all' (loaders already guarantee a valid value)."""
    if want == "landscape":
        return w > h
    if want == "portrait":
        return h > w
    return True


def next_matching_index(matchers, start, forward=True, want="all") -> int | None:
    """First index reached from *start* (wrapping through N positions of the
    matchers list) whose matcher is True for *want*; None when nothing
    matches. A matcher is a predicate ``matcher(want) -> bool``. ``want ==
    'all'`` degenerates to the plain ±1 wrap step. A full round trip may
    return *start* itself — the one-match case (place the same slide once);
    this is what stops a single-match slideshow from spinning."""
    n = len(matchers)
    if n <= 0:
        return None
    step = 1 if forward else -1
    if want == "all":
        return (start + step) % n
    for k in range(1, n + 1):
        j = (start + step * k) % n
        if matchers[j](want):
            return j
    return None


ANIM_MS = 220  # PLAN §4 hop 7 / §8 row 8: the one animation is a 220ms crossfade.


def transition_for(animations: bool) -> tuple:
    """Pure duration-decider (PLAN P5 gate): (crossfade?, duration_ms)."""
    if bool(animations):
        return (True, ANIM_MS)
    return (False, 0)


def save_config(cfg: dict, path: str | None = None) -> bool:
    """mkdir -p + write; False on any OS failure (runtime keeps working)."""
    path = path if path is not None else config_path()
    payload = {
        "interval_sec": float(cfg.get("interval_sec", CONFIG_DEFAULTS["interval_sec"])),
        "animations": bool(cfg.get("animations", CONFIG_DEFAULTS["animations"])),
        "slide_orientation": normalize_orientation(
            cfg.get("slide_orientation", CONFIG_DEFAULTS["slide_orientation"])
        ),
    }
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            json.dump(payload, fh)
        return True
    except OSError:
        return False


def wrap_advance(index: int, total: int, delta: int) -> int | None:
    """Wrap-around next (+1) / prev (-1). None when there is nothing to
    advance over (empty list); nothing open yet (index < 0) lands on 0."""
    if total <= 0:
        return None
    if index < 0:
        return 0
    return (index + delta) % total


def format_subtitle(dt, index: int = 0, total: int = 0) -> str:
    """'Oct 21 7:55' mtime line (Loupe-style); '(i/N)' suffix from P2 on."""
    parts = []
    if dt is not None:
        # %-e: day-of-month without leading zero (glibc strftime).
        parts.append(dt.format("%b %-e %H:%M").replace("  ", " ").strip())
    if index and total:
        parts.append(f"({index}/{total})")
    return "  ·  ".join(parts)


# ---------------------------------------------------------------------------
# Look-ahead cache (FTR1, PLAN §11.1): keep-set = current + 2 forward + 1 back,
# bounded by a hard byte budget.  Pure helpers below are unit-tested without
# ever touching the GUI; the window integration lives in LookseeWindow.
# ---------------------------------------------------------------------------

#: Look-ahead depths around the current index (PLAN §11.2 P7).
PRELOAD_FORWARD = 2
PRELOAD_BACK = 1

#: Byte budget for the whole cache (module constant; NO config knob, no schema
#: change — PLAN §11.1).  Entry charge is w*h*4 (RGBA worst case).
PRELOAD_PIXEL_BUDGET = 200 * 1024 * 1024

#: Keep-set scan order = decode priority: current first, then forward, then
#: the single back entry (wrap frontier included via modulo indexing).
_PRELOAD_SCAN = (0, 1, 2, -1)


def window_offsets(index: int, total: int) -> tuple[int, ...]:
    """Keep-set indices: i, i+1, i+2, i-1 (mod N, wrap frontier included),
    deduplicated in priority order.  N <= 4 collapses to every index;
    an invalid index yields the empty tuple."""
    if total <= 0 or not 0 <= index < total:
        return ()
    out: list[int] = []
    for off in _PRELOAD_SCAN:
        idx = (index + off) % total
        if idx not in out:
            out.append(idx)
    return tuple(out)


def window_paths(paths: list[str], index: int) -> dict[str, int]:
    """The keep-set as ``{path: offset}`` for folder *paths* at *index*
    (PLAN §11.1 window rule).  Insertion order = decode priority (current
    first); offsets are the signed window roles 0 / +1 / +2 / -1."""
    total = len(paths)
    if total <= 0 or not 0 <= index < total:
        return {}
    roles: dict[str, int] = {}
    for off in _PRELOAD_SCAN:
        path = paths[(index + off) % total]
        if path not in roles:
            roles[path] = off
    return roles


def prune_plan(cache_keys, keep) -> list[str]:
    """Keys to drop from the cache dict: every key not in the keep-set
    (after EVERY nav/adopt/rescan the cache is pruned to exactly the
    keep-set — PLAN §11.1)."""
    keep_set = set(keep)
    return [k for k in cache_keys if k not in keep_set]


def entry_charge(width: int, height: int) -> int:
    """Per-entry memory charge: w*h*4 bytes (RGBA worst case, PLAN §11.1)."""
    return max(0, int(width)) * max(0, int(height)) * 4


# ---------------------------------------------------------------------------
# FTR5 (PLAN §19.1.1): tiered decode sizing for large images.  The tier is
# the LONG EDGE of the decode box for the current slide: 2x the viewport long
# edge keeps the texture crisp at fit and at 1:1 zoom up to 2x, floored at
# TIER_MIN_PX so a small window never decodes blurry, and capped at
# TEXTURE_PX_CAP because a decode bigger than the biggest texture we ever
# build is pure waste (that is exactly the 144 MB-for-nothing case §19.0
# measured).  A window that has not measured yet (viewport 0 at open) uses
# TIER_ZERO_PX until the first real allocation arrives (P19 re-decodes on
# growth).  Pure + gi-free, deterministic, unit-tested; no config knob.
# ---------------------------------------------------------------------------

#: Viewport multiplier for the tier (crisp at fit AND at 2x zoom).
TIER_MULTIPLIER = 2

#: Tier floor: never decode smaller than this, however small the window.
TIER_MIN_PX = 1024

#: Tier when the viewport measures 0 (window not allocated yet at open).
TIER_ZERO_PX = 1080

#: Deep-zoom upgrade threshold (PLAN §19.3): the full-res slot is worth a
#: decode once the target texture exceeds the tiered pixbuf by this factor.
UPGRADE_RATIO = 1.05

#: Viewport-growth re-decode threshold (PLAN §19.3): re-decode the CURRENT
#: slide only when the new tier exceeds the loaded tier by this factor.
GROWTH_RATIO = 1.25

#: Debounce for the viewport-growth re-decode (ms) — a drag-resize fires
#: notify::default-width/height per pixel; one timer coalesces them.
GROWTH_DEBOUNCE_MS = 300


def upgrade_needed(
    target_long_px: int, loaded_long_px: int, natural_long_px: int
) -> bool:
    """§19.3 full-res upgrade trigger (pure): True when the zoomed target
    texture exceeds the LOADED (tiered) pixbuf by UPGRADE_RATIO **and** the
    file actually holds more detail — natural long edge must exceed it by
    the same margin.  A native-size slide (natural == loaded) can never
    gain from a full re-decode, so the same formula says False there; the
    caller additionally never re-triggers while an upgrade is loaded or
    pending (single-flight)."""
    if loaded_long_px <= 0 or target_long_px <= 0:
        return False
    if natural_long_px <= 0:
        natural_long_px = loaded_long_px
    return (
        target_long_px > UPGRADE_RATIO * loaded_long_px
        and natural_long_px > UPGRADE_RATIO * loaded_long_px
    )


def growth_redecode(old_tier: int, new_tier: int, natural_max: int) -> int:
    """§19.3 viewport-growth plan (pure): return the tier the CURRENT slide
    must be re-decoded at, or 0 for "do nothing".  Re-decode only on
    GROWTH strictly beyond GROWTH_RATIO, only when the image actually has
    more pixels than the current tier (natural long edge > old tier), and
    never past the texture cap."""
    if new_tier <= old_tier:
        return 0  # shrink (or no change): never re-decode
    if old_tier <= 0:
        return 0
    if new_tier <= GROWTH_RATIO * old_tier:
        return 0
    if natural_max <= 0 or natural_max <= old_tier:
        return 0  # native-size slide: a bigger tier buys nothing
    return min(new_tier, TEXTURE_PX_CAP)


def decode_tier_px(viewport_w, viewport_h, device_scale: float = 1.0) -> int:
    """The one tier formula (PLAN §19.1.1):
    ``min(TEXTURE_PX_CAP, max(1024, ceil(2 * max(vw, vh) * scale)))``;
    a zero (or unmeasurable) viewport falls back to 1080."""
    try:
        span = max(int(viewport_w), int(viewport_h))
    except (TypeError, ValueError):
        span = 0
    if span <= 0:
        return TIER_ZERO_PX
    want = math.ceil(TIER_MULTIPLIER * span * max(0.0, float(device_scale)))
    return min(TEXTURE_PX_CAP, max(TIER_MIN_PX, want))


# ---------------------------------------------------------------------------
# OLED black skin (FTR2 / P10, PLAN §13.1.1): ONE CSS provider, pure builder.
# Rules scoped under a single class added to the window + headerbar only —
# popovers, menus, buttons and spins keep libadwaita default colors (they
# never carry the class).  No config key, no knob: the app is simply black.
# ---------------------------------------------------------------------------

BLACK_CSS_CLASS = "looksee-black"


def build_black_css() -> str:
    """The complete OLED-black stylesheet (PLAN §13.1.1): window and
    headerbar painted true #000000 (OLED pixel OFF), headerbar flat (no
    border, no shadow), content widgets transparent so the window black
    shows through."""
    return "\n".join(
        (
            f"window.{BLACK_CSS_CLASS} {{ background-color: #000000; }}",
            f"headerbar.{BLACK_CSS_CLASS} {{",
            "  background-color: #000000;",
            "  background-image: none;",
            "  border-bottom: none;",
            "  box-shadow: none;",
            "  outline: none;",
            "}",
            f".{BLACK_CSS_CLASS} scrolledwindow,",
            f".{BLACK_CSS_CLASS} stack,",
            f".{BLACK_CSS_CLASS} viewport {{",
            "  background-color: transparent;",
            "  background-image: none;",
            "  box-shadow: none;",
            "}",
        )
    )


def fit_candidate(
    roles: dict[str, int],
    charges: dict[str, int],
    candidate: str,
    charge: int,
    cap: int = PRELOAD_PIXEL_BUDGET,
) -> tuple[list[str], bool]:
    """Budget accounting for one candidate insert (pure, PLAN §11.1).

    On breach the FURTHEST of the two look-aheads evicts first (+2 before
    +1); the current (offset 0) and back (-1) entries are never evicted.
    Keys that are not in *roles* are stale (a prune is due) and contribute
    nothing.  Returns ``(evicted_keys, fits)``: when fits is False the
    caller records a shortfall and skips the candidate entirely."""
    total = charge + sum(
        c for k, c in charges.items() if k != candidate and k in roles
    )
    victims = sorted(
        (k for k in charges if k != candidate and roles.get(k, 0) > 0),
        key=lambda k: -roles[k],
    )
    evicted: list[str] = []
    for key in victims:
        if total <= cap:
            break
        total -= charges[key]
        evicted.append(key)
    return evicted, total <= cap


