"""looksee_ui — the GUI half of the FTR3 split (PLAN §15.0): the
application window (widgets, the GLib-async IO engines of FTR3 — slide
loads, folder listing, orientation probes — plus the FTR1 pre-cache
pump, FTR2 filter/auto-hide timers and the P5 menu popover) and the
Adw.Application.  Constants and pure helpers come from looksee_io;
looksee.py re-exports the public surface of both halves."""
from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")  # FTR4/P17: Pango.EllipsizeMode for the title
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from looksee_io import *  # noqa: E402,F401,F403 — public surface of the pure half

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class LookseeWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(1200, 800)
        self.set_size_request(400, 300)
        # OLED black skin (P10): the class rides ONLY the window + headerbar
        # (PLAN §13.1.1); popovers/menus/buttons never get it.
        self.add_css_class(BLACK_CSS_CLASS)

        # Centered title (filename) + dim subtitle (mtime [+ index]).
        self._title_label = Gtk.Label(label="Looksee")
        self._title_label.add_css_class("title-2")
        self._subtitle_label = Gtk.Label(label="")
        self._subtitle_label.add_css_class("dim-label")
        self._subtitle_label.add_css_class("caption")
        # FTR4 (P17, PLAN §17.1): the title block must never clamp the
        # window's minimum width — both labels truncate with an ellipsis
        # (END), capped at max-width-chars 48, truncation centered like
        # Loupe (xalign 0.5).  NEVER set width-chars: that would pin the
        # headerbar width instead.
        for label in (self._title_label, self._subtitle_label):
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(48)
            label.set_xalign(0.5)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_box.append(self._title_label)
        title_box.append(self._subtitle_label)

        header = Adw.HeaderBar(title_widget=title_box)
        header.add_css_class(BLACK_CSS_CLASS)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        self._toolbar = toolbar

        # Right cluster (PLAN §4): ◀ ▶ (P2) · slideshow (P4) · zoom (P3) ·
        # menu (P5) — one box so visual left→right order is deterministic
        # (single pack_end; P4/P5 append their children here in place).
        self._right_box = Gtk.Box(spacing=6)
        self._nav_box = Gtk.Box(spacing=6)
        self.prev_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.next_button = Gtk.Button(icon_name="go-next-symbolic")
        self.prev_button.set_tooltip_text("Previous image")
        self.next_button.set_tooltip_text("Next image")
        for btn in (self.prev_button, self.next_button):
            btn.set_can_focus(False)
            btn.add_css_class("flat")
        self.prev_button.connect("clicked", lambda _b: self.step(-1))
        self.next_button.connect("clicked", lambda _b: self.step(1))
        self._nav_box.append(self.prev_button)
        self._nav_box.append(self.next_button)
        self._right_box.append(self._nav_box)

        # Slideshow toggle ▶/⏸ (P4) — sits between nav and zoom per spec.
        self.play_button = Gtk.ToggleButton(icon_name="media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Play slideshow")
        self.play_button.set_can_focus(False)
        self.play_button.add_css_class("flat")
        self.play_button.connect("toggled", self._on_play_toggled)
        self._right_box.append(self.play_button)

        # Zoom cluster [− fit +] (P3).
        self._zoom_box = Gtk.Box(spacing=6)
        self.zoom_out_button = Gtk.Button(icon_name="zoom-out-symbolic")
        self.fit_button = Gtk.Button(icon_name="zoom-fit-best-symbolic")
        self.zoom_in_button = Gtk.Button(icon_name="zoom-in-symbolic")
        self.zoom_out_button.set_tooltip_text("Zoom out (-)")
        self.fit_button.set_tooltip_text("Fit to window (1)")
        self.zoom_in_button.set_tooltip_text("Zoom in (+)")
        for btn in (self.zoom_out_button, self.fit_button, self.zoom_in_button):
            btn.set_can_focus(False)
            btn.add_css_class("flat")
        self.zoom_out_button.connect("clicked", lambda _b: self.zoom_by_step(-1))
        self.fit_button.connect("clicked", lambda _b: self.set_zoom_fit())
        self.zoom_in_button.connect("clicked", lambda _b: self.zoom_by_step(1))
        self._zoom_box.append(self.zoom_out_button)
        self._zoom_box.append(self.fit_button)
        self._zoom_box.append(self.zoom_in_button)
        self._right_box.append(self._zoom_box)

        # ⋮ menu (P4): slideshow interval SpinButton, writes JSON immediately
        # (animations switch + about land in P5).
        self._config: dict = load_config()
        self.interval_spin = Gtk.SpinButton.new_with_range(
            INTERVAL_MIN, INTERVAL_MAX, 0.5
        )
        self.interval_spin.set_digits(1)
        self.interval_spin.set_value(self._config["interval_sec"])
        self.interval_spin.set_hexpand(True)
        self.interval_spin.connect(
            "value-changed",
            lambda spin: self.set_interval(spin.get_value()),
        )
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        menu_box.set_margin_top(12)
        menu_box.set_margin_bottom(12)
        menu_box.set_margin_start(12)
        menu_box.set_margin_end(12)
        interval_label = Gtk.Label(label="Slideshow interval (s)")
        interval_label.set_halign(Gtk.Align.START)
        menu_box.append(interval_label)
        menu_box.append(self.interval_spin)
        menu_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        anim_row = Gtk.Box(spacing=12)
        anim_label = Gtk.Label(label="Animations")
        anim_label.set_hexpand(True)
        anim_label.set_halign(Gtk.Align.START)
        self.anim_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.anim_switch.set_active(self._config["animations"])
        self.anim_switch.connect("state-set", self._on_anim_state_set)
        anim_row.append(anim_label)
        anim_row.append(self.anim_switch)
        menu_box.append(anim_row)
        menu_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        # Orientation radio group (FTR2 / P11): filters SLIDESHOW TICKS only,
        # written to config immediately (no Apply), exact three labels of
        # PLAN §13.1.2.
        orient_label = Gtk.Label(label="Slideshow orientation")
        orient_label.set_halign(Gtk.Align.START)
        menu_box.append(orient_label)
        self._orient_buttons: dict[str, Gtk.CheckButton] = {}
        first_radio: Gtk.CheckButton | None = None
        for value, radio_label in (
            ("all", "All images"),
            ("landscape", "Landscape only"),
            ("portrait", "Portrait only"),
        ):
            radio = Gtk.CheckButton(label=radio_label)
            if first_radio is None:
                first_radio = radio
            else:
                radio.set_group(first_radio)
            self._orient_buttons[value] = radio
            menu_box.append(radio)
        # Initial activation BEFORE handler connect: startup must never
        # write config (P4 "defaults, no file on disk" contract).
        self._orient_buttons[
            normalize_orientation(self._config.get("slide_orientation"))
        ].set_active(True)
        for radio in self._orient_buttons.values():
            radio.connect("toggled", self._on_orient_toggled)
        menu_popover = Gtk.Popover()
        menu_popover.set_child(menu_box)
        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_button.set_tooltip_text("Menu")
        self.menu_button.set_can_focus(False)
        self.menu_button.add_css_class("flat")
        self.menu_button.set_popover(menu_popover)
        self._right_box.append(self.menu_button)

        header.pack_end(self._right_box)

        # Content: ScrolledWindow -> Stack (crossfade carrier, P5) -> Picture.
        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        self.stack = Gtk.Stack(transition_duration=0)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.scroller.set_child(self.stack)
        toolbar.set_content(self.scroller)
        self.set_content(toolbar)

        # --- navigation state ------------------------------------------
        self.current_path: str | None = None
        self.folder: str | None = None
        self.paths: list[str] = []
        self.index: int = -1
        self._slide_seq = 0
        self._monitor: Gio.FileMonitor | None = None
        self._rescan_id = 0
        # FTR3 (P15): async folder listing.  ONE cancellable per
        # adopt/rescan cycle — starting a cycle cancels the superseded one;
        # the live self.paths stays valid until the new listing swaps in
        # atomically (_finish_listing -> _apply_listing).
        self._listing_cancel: Gio.Cancellable | None = None
        self._listing_enum: Gio.FileEnumerator | None = None
        self._listing_names: list[tuple[str, bool]] = []
        self._listing_ready = None  # optional continuation(paths)
        self._open_kind_cancel: Gio.Cancellable | None = None
        # P11: per-album dimension probes for the orientation filter
        # (path -> (w, h)); cleared on adopt, pruned on rescan.
        self._orient_dims: dict[str, tuple[int, int]] = {}
        # FTR3 (P15): async probe engine — cycle cancellable (renewed per
        # adopt) + count of probes in flight (concurrency cap, see
        # _orient_matchers).
        self._orient_cancel: Gio.Cancellable | None = None
        self._orient_inflight = 0

        # P12: chrome auto-hide bookkeeping (monotonic-ms activity clock).
        self._last_activity_ms = 0
        self._hide_id = 0
        self._chrome_hidden = False
        # POINTER sources of activity (keyboard deliberately excluded):
        # motion/enter over the window, button press, wheel (in _on_scroll).
        self._motion_ctrl = Gtk.EventControllerMotion()
        self._motion_ctrl.connect("motion", lambda *_a: self._cursor_activity())
        self._motion_ctrl.connect("enter", lambda *_a: self._cursor_activity())
        self.add_controller(self._motion_ctrl)
        self._click_gesture = Gtk.GestureClick()
        self._click_gesture.connect("pressed", lambda *_a: self._cursor_activity())
        self.add_controller(self._click_gesture)

        # --- zoom state (P3) ----------------------------------------------
        # "fit": CONTAIN in the viewport (default); "scale": exact
        # natural*scale rendering, scrollbars when it overflows.
        self.zoom_mode: str = "fit"
        self.scale: float = 1.0
        self._picture: Gtk.Picture | None = None
        self._pixbuf: GdkPixbuf.Pixbuf | None = None
        self._texture: Gdk.Texture | None = None
        self._natural: tuple[int, int] | None = None
        self._zoom_tex_size: tuple[int, int] | None = None
        self._zoom_tex: Gdk.Texture | None = None

        # --- fullscreen state (P3) -----------------------------------------
        self._fullscreen = False

        # --- slideshow state (P4) -------------------------------------------
        self._slide_id = 0

        # --- animation state (P5) ---------------------------------------------
        self._anim = False
        self._prune_id = 0

        # --- look-ahead cache state (FTR1 / P7) --------------------------------
        # _precache maps ABSOLUTE path -> decoded pixbuf; charges mirrors it in
        # bytes; roles is the current keep-set plan (path -> window offset);
        # pending is the decode queue in priority order (no threads, ever).
        self._precache: dict[str, GdkPixbuf.Pixbuf] = {}
        self._precache_charges: dict[str, int] = {}
        self._precache_roles: dict[str, int] = {}
        self._precache_pending: list[str] = []
        self._precache_pump_id = 0
        self._precache_shortfalls = 0

        # --- async slide-load state (FTR3 / P14, PLAN §15.1.A) ----------------
        # The ONE pending slide load: absolute key (completion guard) and its
        # Gio.Cancellable — a new nav ALWAYS cancels the old load.  No threads
        # anywhere: everything runs through the GLib task pool + main loop.
        self._load_key: str | None = None
        self._load_cancel: Gio.Cancellable | None = None
        # Which picture is actually on screen right now (test/observability
        # seam; None = nothing painted yet — the previous slide stays visible
        # until a pending load arrives).
        self._painted_key: str | None = None
        # FTR1 pump decode in flight (FTR3 made the pump's decode SITE async;
        # still at most one decode, still PRIORITY_LOW).
        self._precache_decode_cancel: Gio.Cancellable | None = None

        # --- tiered decode state (FTR5 / P18, PLAN §19.1) ---------------------
        # The decode box (long edge, px) for CURRENT-slide loads: recomputed
        # from the viewport on every _show_current (§19.1.1); TIER_ZERO_PX
        # until the window measures.  _nat_probe_cancel guards the two-beat
        # true-dims correction for tiered cache hits (_probe_natural).
        self._tier = decode_tier_px(0, 0)
        self._nat_probe_cancel: Gio.Cancellable | None = None

        # --- deep-zoom / growth state (FTR5 / P19, PLAN §19.3) ----------------
        # ONE transient full-res slot for the CURRENT slide only — NOT
        # charged to the preload budget (single-flight: at most one upgrade
        # in flight; dropped on nav or when zoom returns to <= 1x of tier
        # coverage).  _base_* remember what the tiered render looked like so
        # the drop restores it; _slide_tier is the long edge the PAINTED
        # pixbuf was decoded at (0 = native: nothing can be gained).
        # _growth_id debounces the viewport-growth re-decode.
        self._full_pixbuf: GdkPixbuf.Pixbuf | None = None
        self._full_cancel: Gio.Cancellable | None = None
        self._full_key: str | None = None
        self._base_pixbuf: GdkPixbuf.Pixbuf | None = None
        self._base_texture: Gdk.Texture | None = None
        self._slide_tier = 0
        self._growth_id = 0

        # Arrow keys navigate (Left/Right; more bindings in P3+).
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        # Plain wheel (no modifiers) navigates; Ctrl+wheel zooms (P3).
        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL
            | Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_scroll)
        self.scroller.add_controller(scroll)

        self._apply_animations()
        self.connect("close-request", self._on_close)

        # FTR5 (P19, §19.3): window size changes surface headlessly through
        # notify::default-width/height (verified on this build — no resize /
        # size-allocate signal fires there).  Debounce them: a drag-resize
        # notifies per pixel, the re-decode must happen once, 300 ms after
        # the last change, and only for a real tier GROWTH.
        self.connect("notify::default-width", self._on_size_hint)
        self.connect("notify::default-height", self._on_size_hint)

    # -- image loading ------------------------------------------------------

    def open_path(self, path: str) -> None:
        """Show *path*; on undecodable files update the header, never crash.
        Adopts the sibling folder so nav works (P2).  FTR3 (PLAN §15.1.A):
        the header/title update synchronously; the picture decodes through
        GLib-async IO in _show_current — a memory hit in _precache paints
        instantly, everything else arrives later while the previous slide
        stays visible.  FTR3 (PLAN §15.1.B): the sibling listing is async
        too — until it lands, the (possibly stale) list stays the working
        list and the swap-in later corrects (i/N) + the keep-set atomically."""
        folder = os.path.dirname(os.path.abspath(path))
        self._adopt_folder(folder)
        if path in self.paths:
            self.index = self.paths.index(path)
        else:
            # Opened file not in the (current) scan (hidden/unsupported
            # ext, or a fresh folder mid-listing): show it alone for now —
            # the listing swap-in restores siblings atomically.
            self.paths, self.index = [], -1
        self._show_current(path)

    def open_any(self, path: str) -> None:
        """CLI/GtkFileLauncher target (FTR3/P15): directory or file?  Even
        this first fork of the road is decided ASYNC — enumerate_children
        answers only for directories — so the app never sync-stats
        (no os.path.isdir) on an incoming path."""
        if self._open_kind_cancel is not None:
            self._open_kind_cancel.cancel()
        cancel = self._open_kind_cancel = Gio.Cancellable()

        def on_probe(src, res):
            if cancel is not self._open_kind_cancel:
                return  # a newer open request owns the window now
            self._open_kind_cancel = None
            try:
                enum = src.enumerate_children_finish(res)
            except GLib.Error:
                self.open_path(path)  # not enumerable == plain file open
                return
            enum.close_async(GLib.PRIORITY_LOW, None, None)
            self.open_folder(path)

        try:
            Gio.File.new_for_path(path).enumerate_children_async(
                "standard::name",
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                cancel,
                on_probe,
            )
        except GLib.Error:
            self.open_path(path)

    def open_folder(self, folder: str) -> None:
        """CLI `looksee DIR` (FTR3/P15): no upfront scan — adopt the folder,
        enumerate it async, and open its first image the moment the listing
        lands.  Until then the window is up with the folder as title and no
        slide: chrome-first, IO-behind (PLAN §15.1.B)."""
        folder = os.path.abspath(folder)
        self._cancel_slide_load()  # header goes blank: nothing may paint after
        self.current_path = None
        self.paths, self.index = [], -1
        self._title_label.set_text(os.path.basename(folder) or folder)
        self._subtitle_label.set_text("")

        def _first(paths: list[str]) -> None:
            if paths and os.path.abspath(self.current_path or "") != paths[0]:
                self.open_path(paths[0])

        self._adopt_folder(folder, on_ready=_first)

    def step(self, delta: int) -> None:
        """Wrap-around next/prev across the folder list (P2)."""
        nxt = wrap_advance(self.index, len(self.paths), delta)
        if nxt is None or nxt == self.index:
            return
        self.index = nxt
        self._show_current(self.paths[nxt])

    def _show_current(self, path: str) -> None:
        """Nav landing point (open/step/tick/rescan).  current_path + title
        update synchronously — never behind IO.  A look-ahead cache hit
        (FTR1) paints right here with ZERO IO; otherwise the pixbuf arrives
        via read_async -> tiered/scale decode (FTR3/P14 + FTR5/P18) and the
        previous slide stays on screen until then.  FTR5: the decode tier is
        recomputed from the CURRENT viewport on every cold load (§19.1.1);
        a tiered cache hit paints provisionally at pixbuf dims and the true
        natural dims correct a beat later via _probe_natural."""
        self.current_path = path
        self._title_label.set_text(os.path.basename(path))
        self._cancel_slide_load()  # a new nav always supersedes the old load
        self._drop_full_res()  # P19: the full-res slot is current-slide only
        key = os.path.abspath(path)
        self._tier = self._viewport_tier()
        hit = self._precache.get(key)
        if hit is not None:
            # Cache hit (FTR1): consume the SAME decoded pixbuf — GdkPixbufs
            # are immutable in practice, so sharing it with _pixbuf is safe.
            known = self._orient_dims.get(key)
            if known is not None and known[0] > 0 and known[1] > 0:
                self._render_slide(key, hit, known)
            else:
                self._render_slide(key, hit)
                if max(hit.get_width(), hit.get_height()) >= self._tier:
                    # The entry may be TIERED (FTR5): pixbuf dims are only
                    # provisional.  Paint stays IO-free; the header probe
                    # lands the true dims a beat later.
                    self._probe_natural(key)
            return
        self._load_key = key
        self._load_cancel = Gio.Cancellable()
        cancel = self._load_cancel
        self._load_pixbuf_async(
            key,
            GLib.PRIORITY_DEFAULT,
            cancel,
            lambda pb, err, nat: self._on_slide_loaded(key, cancel, pb, err, nat),
            self._tier,
        )
        # FTR1: prune to the new window synchronously (the cache never holds
        # a key outside the keep-set between navs).  The pending load is
        # invisible to the budget logic: it merges on arrival, like the old
        # cold decode did.
        self._plan_precache()

    def _viewport_tier(self) -> int:
        """FTR5 (§19.1.1): the decode tier for the viewport as measured
        right now (device scale included; TIER_ZERO_PX until the window has
        a real allocation — the fallback tier is what an unopened window
        would show at 1080p)."""
        return decode_tier_px(
            self.scroller.get_width(),
            self.scroller.get_height(),
            self.get_scale_factor(),
        )

    def _probe_natural(self, key: str) -> None:
        """FTR5 (P18): two-beat natural-dims correction for a cache hit that
        may hold a TIERED pixbuf.  Paint already happened at provisional
        (pixbuf) dims; when the async header probe lands, the TRUE dims
        update _natural/_orient_dims and zoom re-applies (fit scale was
        computed against the smaller box).  Fire-and-forget: one probe in
        flight, superseded probes (nav/close) drop silently — the paint
        itself never waits on IO."""
        if self._nat_probe_cancel is not None:
            self._nat_probe_cancel.cancel()
        cancel = Gio.Cancellable()
        self._nat_probe_cancel = cancel

        def on_header(_src, res):
            if cancel is not self._nat_probe_cancel:
                return  # superseded by a newer nav/probe (or teardown)
            self._nat_probe_cancel = None
            try:
                _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info_finish(res)
            except Exception:  # noqa: BLE001 — glycin quirk: stay undecided
                return
            if w <= 0 or h <= 0:
                return
            self._orient_dims[key] = (w, h)
            if (
                self._painted_key == key
                and self.current_path is not None
                and os.path.abspath(self.current_path) == key
            ):
                self._natural = (w, h)
                # The hit MAY be tiered after all: settle the painted tier
                # (P19 inputs read the true natural dims from here on).
                base = self._base_pixbuf
                if (
                    base is not None
                    and max(w, h) > max(base.get_width(), base.get_height())
                ):
                    self._slide_tier = max(
                        base.get_width(), base.get_height()
                    )
                else:
                    self._slide_tier = 0
                self._zoom_tex_size = None
                self._apply_zoom()

        GdkPixbuf.Pixbuf.get_file_info_async(key, cancel, on_header)

    def _cancel_slide_load(self) -> None:
        """Drop the pending slide load, if any: its completion will surface
        as a CANCELLED error and paint nothing (PLAN §15.1.A)."""
        if self._load_cancel is not None:
            self._load_cancel.cancel()
        self._load_cancel = None
        self._load_key = None

    # -- deep zoom + viewport growth (FTR5 / P19, PLAN §19.3) ------------------

    def _on_size_hint(self, *_arg) -> None:
        """notify::default-width/height: arm the 300 ms growth debounce (a
        drag-resize notifies per pixel — the timer coalesces them)."""
        if self._growth_id:
            GLib.source_remove(self._growth_id)
        self._growth_id = GLib.timeout_add(
            GROWTH_DEBOUNCE_MS, self._check_viewport_growth
        )

    def _check_viewport_growth(self) -> bool:
        """One debounced growth check (§19.3): recompute the tier; a tier
        GROWTH beyond GROWTH_RATIO re-decodes the CURRENT slide at the new
        tier in the background (old paint stays until arrival, completion
        goes through the usual stale-drop guard).  Never on shrink."""
        self._growth_id = 0
        new_tier = self._viewport_tier()
        if new_tier == self._tier:
            return False
        natural = max(self._natural) if self._natural else 0
        redo = growth_redecode(self._slide_tier, new_tier, natural)
        self._tier = new_tier
        if not redo or self.current_path is None:
            return False
        key = os.path.abspath(self.current_path)
        self._cancel_slide_load()
        self._load_key = key
        cancel = Gio.Cancellable()
        self._load_cancel = cancel
        self._load_pixbuf_async(
            key,
            GLib.PRIORITY_DEFAULT,
            cancel,
            lambda pb, err, nat: self._on_slide_loaded(key, cancel, pb, err, nat),
            redo,
        )
        return False

    def _zoom_quality_check(self, tw: int, th: int) -> None:
        """Scale-mode hook at the end of _apply_zoom (§19.3): deep zoom past
        tier coverage starts ONE cancellable full decode of the CURRENT
        slide; zoom back to <= 1x coverage drops the slot.  While the
        upgrade is pending nothing changes — today's upsample stays."""
        base = self._base_pixbuf
        if base is None or self._painted_key is None:
            return
        cover = max(base.get_width(), base.get_height())
        natural = max(self._natural) if self._natural else 0
        action_slot = self._full_pixbuf is not None or self._full_cancel is not None
        if upgrade_needed(max(tw, th), cover, natural) and not action_slot:
            self._start_full_upgrade()
        elif action_slot and max(tw, th) <= cover:
            self._drop_full_res()  # caller is mid-apply: restore is picked up

    def _start_full_upgrade(self) -> None:
        """Single-flight full decode of the current slide (tier=0): the ONE
        full-res slot, not charged to the preload budget.  Nav (or a newer
        trigger) cancels; the completion identity-guards itself."""
        key = self._painted_key
        if key is None:
            return
        cancel = Gio.Cancellable()
        self._full_cancel = cancel
        self._full_key = key

        def on_full(pb, err, _nat):
            if cancel is not self._full_cancel:
                return  # dropped (nav/shrink/teardown): paint nothing
            self._full_cancel = None
            if pb is None or self._painted_key != key:
                self._full_key = None
                return
            self._full_pixbuf = pb
            self._adopt_full()
            self._apply_zoom()

        self._load_pixbuf_async(key, GLib.PRIORITY_DEFAULT, cancel, on_full, 0)

    def _adopt_full(self) -> None:
        """Point the render at the full-res slot: the pixbuf becomes the
        exact-size zoom source (sharp), while the base texture is replaced
        with a TEXTURE_PX_CAP-bounded one — the cap still rules (§19.3)."""
        pb = self._full_pixbuf
        key = self._full_key
        if pb is None or key is None or self._painted_key != key:
            return
        fw, fh = pb.get_width(), pb.get_height()
        if max(fw, fh) > TEXTURE_PX_CAP:
            k = TEXTURE_PX_CAP / max(fw, fh)
            shown = pb.scale_simple(
                max(1, round(fw * k)), max(1, round(fh * k)),
                GdkPixbuf.InterpType.BILINEAR,
            )
        else:
            shown = pb
        self._pixbuf = pb
        self._texture = Gdk.Texture.new_for_pixbuf(shown)
        self._zoom_tex = None
        self._zoom_tex_size = None

    def _drop_full_res(self) -> None:
        """Release the ONE full-res slot (nav, shallow zoom, teardown) and
        restore the tiered look.  Never re-applies zoom itself — callers sit
        inside (or ahead of) an _apply_zoom, so there is no recursion."""
        if self._full_cancel is not None:
            self._full_cancel.cancel()
            self._full_cancel = None
        had = self._full_pixbuf is not None
        self._full_pixbuf = None
        self._full_key = None
        if had and self._base_pixbuf is not None:
            self._pixbuf = self._base_pixbuf
            self._texture = self._base_texture
            self._zoom_tex = None
            self._zoom_tex_size = None

    def _load_pixbuf_async(self, key, io_prio, cancellable, done, tier=0) -> None:
        """The ONE async read+decode engine (FTR3): file.read_async ->
        Pixbuf.new_from_stream_* on Gio's own task pool — the main loop
        never waits on file IO (no threads in app code, PLAN §15.1).
        ``done(pixbuf, None, natural)`` or ``done(None, error, None)`` is
        called exactly once; the stream is always closed asynchronously
        (close on a slow mount must not touch the loop either).

        FTR5 (PLAN §19.1.2): ``tier > 0`` ALSO fires the async header probe
        (``get_file_info_async``) CONCURRENTLY with the read — the probe
        only GATES the decode variant (``new_from_stream_at_scale_async``
        for images proven larger than the tier; plain full decode when
        they fit or the probe fails — §19.1.4: this build's at_scale
        upscales small inputs, so the probe gate is what keeps the
        native-size contract).  The read is still issued IMMEDIATELY when
        the attempt starts, so FTR3's P14 issue-time accounting is
        untouched; total latency is max(probe, read) + decode, never
        probe + read + decode.  The probed dimensions ride back as
        ``done``'s third argument (None = unknown).  ``tier == 0`` is the
        plain full-decode path (FTR2 fallback, P19 upgrade).  Every call
        issues EXACTLY ONE read_async and calls ``done`` exactly once.
        Deviation (introspection-verified): this GdkPixbuf build has no
        ``new_from_stream_at_scale_finish``; an at_scale result is
        consumed through ``new_from_stream_finish`` (one finish for both
        decode variants)."""

        def run_decode(stream, tier_px, natural):
            def on_pixbuf(_src2, res2):
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_stream_finish(res2)
                except GLib.Error as err:
                    stream.close_async(GLib.PRIORITY_LOW, None, None)
                    done(None, err, None)
                    return
                stream.close_async(GLib.PRIORITY_LOW, None, None)
                done(pixbuf, None, natural)

            if tier_px > 0:
                GdkPixbuf.Pixbuf.new_from_stream_at_scale_async(
                    stream, tier_px, tier_px, True, cancellable, on_pixbuf,
                )
            else:
                GdkPixbuf.Pixbuf.new_from_stream_async(
                    stream, cancellable, on_pixbuf,
                )

        if tier <= 0:

            def on_stream_full(src, res):
                try:
                    stream = src.read_finish(res)
                except GLib.Error as err:
                    done(None, err, None)
                    return
                run_decode(stream, 0, None)

            Gio.File.new_for_path(key).read_async(
                io_prio, cancellable, on_stream_full,
            )
            return

        state: dict = {}

        def maybe_start() -> None:
            if "stream" not in state or "probed" not in state:
                return
            nat = state["nat"]
            if nat is not None and max(nat) > tier:
                run_decode(state["stream"], tier, nat)  # tier box
            else:
                run_decode(state["stream"], 0, nat)  # fits/unknown: native

        def on_stream(src, res):
            try:
                stream = src.read_finish(res)
            except GLib.Error as err:
                done(None, err, None)
                return
            state["stream"] = stream
            maybe_start()

        def on_header(src, res):
            natural = None
            try:
                _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info_finish(res)
                if w > 0 and h > 0:
                    natural = (w, h)
            except Exception:  # noqa: BLE001 — glycin header quirk (P15)
                pass
            state["probed"] = True
            state["nat"] = natural
            maybe_start()

        GdkPixbuf.Pixbuf.get_file_info_async(key, cancellable, on_header)
        Gio.File.new_for_path(key).read_async(io_prio, cancellable, on_stream)

    def _on_slide_loaded(self, key, cancel, pixbuf, error, natural=None) -> None:
        """Slide-load completion (P14).  Completions of superseded loads —
        the usual case after rapid navs — are dropped silently, as are
        cancellations (never a bogus 'cannot display' over a healthy slide).
        Then the guard from PLAN §15.1.A: if the user navigated away while
        this load was pending (current_path differs at finish), NOTHING
        paints and NOTHING describes.  A real error on the slide still on
        screen keeps the exact old subtitle.  FTR5: *natural* carries the
        probed TRUE dims of a tiered decode (None = pixbuf is native)."""
        if self._load_cancel is not cancel:
            return  # superseded by a newer nav (or teardown): silent drop
        if error is not None and error.matches(
            Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED
        ):
            return  # cancelled: expected, invisible
        self._load_cancel = None
        self._load_key = None
        if self.current_path is None or os.path.abspath(self.current_path) != key:
            return  # navigated away mid-load: drop the paint silently
        if error is not None:
            self._subtitle_label.set_text(f"cannot display: {error.message}")
            return
        self._render_slide(key, pixbuf, natural)

    def _render_slide(self, key, pixbuf, natural=None) -> None:
        """Paint a ready pixbuf (cache hit or async arrival) — the old
        synchronous _show_current tail, now shared by both routes.
        FTR5: *natural* (when given) are the TRUE dimensions behind a
        possibly tiered pixbuf; _natural always reflects true dims so the
        fit/zoom math, subtitle and orientation filter see the real image."""
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_hexpand(True)
        picture.set_vexpand(True)
        # Zoom bookkeeping for the new slide (P3): keep the source pixbuf so
        # zooming can re-render at exact sizes without touching disk again.
        self._picture = picture
        self._pixbuf = pixbuf
        self._texture = texture
        # P19: remember the tiered look (restore point for the full-res
        # drop) and which tier this PAINT came in at (0 = native: the file
        # holds no more detail than what is on screen).
        self._base_pixbuf = pixbuf
        self._base_texture = texture
        pb_long = max(pixbuf.get_width(), pixbuf.get_height())
        self._slide_tier = (
            pb_long if natural is not None and max(natural) > pb_long else 0
        )
        if self._full_pixbuf is not None and self._full_key == key:
            self._adopt_full()  # growth re-decode landed while full is loaded
        self._natural = natural or (pixbuf.get_width(), pixbuf.get_height())
        # Free fact for the orientation filter (P15): record dims ONLY when
        # they are TRUE dims — probed (natural given) or a native-size decode
        # (pixbuf smaller than the tier could not have been rescaled,
        # §19.1.4).  A tiered pixbuf leaves the entry UNDECIDED here;
        # _probe_natural or the orient engine fills it in.
        if (
            natural is not None
            or max(pixbuf.get_width(), pixbuf.get_height()) < self._tier
        ):
            self._orient_dims.setdefault(key, self._natural)
        self._zoom_tex_size = None
        self._zoom_tex = None
        self._swap_picture(picture)
        self._apply_zoom()
        self._refresh_subtitle()
        self._painted_key = key
        # FTR1: the cold decode merges into the cache as the `i` entry of the
        # new window (hits are idempotent), then every nav routes through this
        # single re-plan point — adopt/open/step/rescan all land here.
        self._cache_put(key, pixbuf)
        self._plan_precache()

    def _refresh_subtitle(self) -> None:
        """Two-beat subtitle (FTR3/P15): the (i/N) part is pure state and
        lands synchronously; the mtime part fills in when
        query_info_async(time::modified) arrives.  A stale query never
        writes over a newer slide's line (_on_mtime guard)."""
        if self.current_path is None:
            return
        total = len(self.paths)
        index = self.index + 1 if 0 <= self.index < total else 0
        path = self.current_path
        self._subtitle_label.set_text(format_subtitle(None, index, total))
        try:
            Gio.File.new_for_path(path).query_info_async(
                "time::modified",
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                None,
                lambda src, res, want=path: self._on_mtime(want, src, res),
            )
        except GLib.Error:
            pass  # old behavior for a hopeless query: no date part

    def _on_mtime(self, want: str, src, res) -> None:
        """mtime arrival (FTR3/P15): merge only while *want* is still the
        displayed slide; an error leaves the (i/N)-only line (the old sync
        helper passed None for exactly this case)."""
        if self.current_path != want:
            return
        try:
            info = src.query_info_finish(res)
            stamp = info.get_attribute_uint64("time::modified")
        except GLib.Error:
            return
        total = len(self.paths)
        index = self.index + 1 if 0 <= self.index < total else 0
        dt = GLib.DateTime.new_from_unix_local(stamp)
        self._subtitle_label.set_text(format_subtitle(dt, index, total))

    def _swap_picture(self, picture: Gtk.Picture) -> None:
        """One visible stack child per slide.  Animations OFF: instant cut,
        stale child pruned immediately (GtkStack measures hidden children —
        P2 finding).  Animations ON: the stale child survives the 220ms
        crossfade, then a coalesced timer prunes it."""
        self._slide_seq += 1
        name = f"slide-{self._slide_seq}"
        self.stack.add_named(picture, name)
        self.stack.set_visible_child_name(name)
        if self._anim:
            if self._prune_id:
                GLib.source_remove(self._prune_id)
            self._prune_id = GLib.timeout_add(ANIM_MS + 120, self._prune_timer)
        else:
            self._prune_except(picture)

    def _prune_timer(self) -> bool:
        self._prune_id = 0
        self._prune_except(self.stack.get_visible_child())
        return False

    def _prune_except(self, keep: Gtk.Widget | None) -> None:
        child = self.stack.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            if child is not keep:
                self.stack.remove(child)
            child = nxt

    # -- look-ahead cache (FTR1 / P7) ----------------------------------------

    def _cache_put(self, path: str, pixbuf: GdkPixbuf.Pixbuf) -> None:
        """Insert/replace one entry under the byte budget, evicting the
        furthest look-ahead first (+2 before +1).  A candidate that still
        breaches the cap is NOT cached: the shortfall is recorded silently
        (PLAN §11.1 — off-screen issues have no crash path)."""
        charge = entry_charge(pixbuf.get_width(), pixbuf.get_height())
        evicted, fits = fit_candidate(
            self._precache_roles, self._precache_charges, path, charge
        )
        for key in evicted:
            self._precache.pop(key, None)
            self._precache_charges.pop(key, None)
        if not fits:
            self._precache_shortfalls += 1
            return
        self._precache[path] = pixbuf
        self._precache_charges[path] = charge

    def _plan_precache(self) -> None:
        """Run after EVERY nav/adopt/rescan: prune the dict to exactly the
        keep-set, re-queue decodes for the window entries missing a pixbuf
        (priority order), and (re)arm the idle pump.  FileMonitor churn can
        only shrink the cache toward the keep-set, never grow it."""
        self._precache_roles = window_paths(self.paths, self.index)
        for key in prune_plan(self._precache, self._precache_roles):
            self._precache.pop(key, None)
            self._precache_charges.pop(key, None)
        self._precache_pending = [
            p for p in self._precache_roles if p not in self._precache
        ]
        self._schedule_pump()

    def _schedule_pump(self) -> None:
        if self._precache_pending and not self._precache_pump_id:
            self._precache_pump_id = GLib.idle_add(
                self._precache_tick, priority=GLib.PRIORITY_LOW
            )

    def _precache_tick(self) -> bool:
        """The idle pump (PRIORITY_LOW): ONE decode in flight at a time,
        main loop only — no threads, no subprocesses.  FTR3: the decode SITE
        is async (header probe -> read_async -> decode), so a slow mount
        can never hold the loop; still one decode at a time, still issued at
        low IO priority.  FTR5: pump decodes use the current tier, so big
        slides cost tier-sized memory, not 144 MB.  The queue is re-checked
        against the CURRENT keep-set at issue time: a queued decode whose key
        fell out of the window (or decoded already, or is exactly the slide
        load in flight) is skipped, never executed.  An off-screen decode
        failure is lost silently: slot freed, no retry (P7 semantics
        preserved at merge time)."""
        self._precache_pump_id = 0
        if self._precache_decode_cancel is not None:
            # Previous pump decode still in flight — its completion re-arms
            # the pump; no busy spin here.
            return False
        while self._precache_pending:
            path = self._precache_pending.pop(0)
            if self._precache_roles.get(path) is None or path in self._precache:
                continue
            if path == self._load_key:
                continue  # the slide load already fetches this one (P14)
            cancel = Gio.Cancellable()
            self._precache_decode_cancel = cancel
            self._load_pixbuf_async(
                path,
                GLib.PRIORITY_LOW,
                cancel,
                lambda pb, err, nat, p=path, c=cancel: self._on_pump_decoded(
                    p, c, pb, err, nat
                ),
                self._tier,
            )
            break
        self._schedule_pump()
        return False

    def _on_pump_decoded(self, path, cancel, pixbuf, error, natural=None) -> None:
        """Pump decode completion: merge-time window re-check (P7 semantics,
        now evaluated at arrival); silent on anything stale, cancelled or
        undecodable.  FTR5: a tiered pump decode also carries the probed
        TRUE dims — merged into _orient_dims (fill-only), which feeds the
        orientation filter for free."""
        if self._precache_decode_cancel is not cancel:
            return  # superseded (teardown): silent
        self._precache_decode_cancel = None
        if (
            error is None
            and pixbuf is not None
            and self._precache_roles.get(path) is not None
            and path not in self._precache
        ):
            if natural is not None:
                self._orient_dims.setdefault(path, natural)
            self._cache_put(path, pixbuf)
        self._schedule_pump()

    # -- zoom (P3) -----------------------------------------------------------

    def _apply_zoom(self) -> None:
        """Render the current picture per zoom_mode/scale.  Recipe verified
        headlessly: an exact-size texture with SCALE_DOWN + can_shrink=False
        measures min==nat==target, so the ScrolledWindow gains scrollbars
        exactly when the image overflows the viewport (1:1 requirement)."""
        pic = self._picture
        if pic is None or self._pixbuf is None:
            return
        if self.zoom_mode == "fit":
            # Fit never exceeds the viewport, hence never tier coverage:
            # a loaded/pending full-res upgrade is pure memory here (P19).
            self._drop_full_res()
            pic.set_paintable(self._texture)
            self._zoom_tex_size = None
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_can_shrink(True)
            pic.set_size_request(-1, -1)
            pic.set_halign(Gtk.Align.FILL)
            pic.set_valign(Gtk.Align.FILL)
            return
        nw, nh = self._natural or (1, 1)
        tw = max(1, round(nw * self.scale))
        th = max(1, round(nh * self.scale))
        # P19: deep zoom beyond tier coverage starts the single-flight
        # full-res upgrade; coming back to <= coverage drops it (the
        # restore lands in _texture/_pixbuf *before* the paint below).
        self._zoom_quality_check(tw, th)
        if max(tw, th) <= TEXTURE_PX_CAP:
            if self._zoom_tex_size != (tw, th):
                scaled = self._pixbuf.scale_simple(
                    tw, th, GdkPixbuf.InterpType.BILINEAR
                )
                self._zoom_tex = Gdk.Texture.new_for_pixbuf(scaled)
                self._zoom_tex_size = (tw, th)
            pic.set_paintable(self._zoom_tex)
            pic.set_content_fit(Gtk.ContentFit.SCALE_DOWN)
            pic.set_can_shrink(False)  # min == natural == exact target size
            pic.set_size_request(-1, -1)
            pic.set_halign(Gtk.Align.CENTER)
            pic.set_valign(Gtk.Align.CENTER)
        else:
            # Oversized upscale: stretch the source texture at the request.
            pic.set_paintable(self._texture)
            self._zoom_tex_size = None
            pic.set_content_fit(Gtk.ContentFit.FILL)
            pic.set_can_shrink(True)
            pic.set_size_request(tw, th)
            pic.set_halign(Gtk.Align.FILL)
            pic.set_valign(Gtk.Align.FILL)

    def _fit_scale(self) -> float:
        """Effective display factor in fit mode (zoom anchor from fit)."""
        if not self._natural:
            return 1.0
        nw, nh = self._natural
        vw = self.scroller.get_width()
        vh = self.scroller.get_height()
        if min(nw, nh, vw, vh) < 1:
            return 1.0
        return clamp_scale(min(vw / nw, vh / nh))

    def zoom_by_step(self, direction: int) -> None:
        """+/− buttons and Ctrl+wheel notch: step from the current anchor."""
        base = self.scale if self.zoom_mode == "scale" else self._fit_scale()
        self.scale = zoom_step(base, direction)
        self.zoom_mode = "scale"
        self._apply_zoom()

    def zoom_by_wheel(self, dy: float, dx: float = 0.0) -> None:
        delta = dy if dy else dx
        if delta:
            self.zoom_by_step(-1 if delta > 0 else 1)

    def set_zoom_fit(self) -> None:
        self.zoom_mode = "fit"
        self._apply_zoom()

    def set_zoom_one_to_one(self) -> None:
        self.zoom_mode = "scale"
        self.scale = 1.0
        self._apply_zoom()

    # -- fullscreen (P3) ------------------------------------------------------

    def toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self.unfullscreen()
            self._fullscreen = False
        else:
            self.fullscreen()
            self._fullscreen = True
        # Loupe-like: chrome hides while fullscreen (libadwaita 1.9 API);
        # P12: the auto-hide state survives a fullscreen round trip.
        self._toolbar.set_reveal_top_bars(
            not self._fullscreen and not self._chrome_hidden
        )

    @property
    def is_fullscreen(self) -> bool:
        return self._fullscreen

    # -- slideshow (P4) --------------------------------------------------------

    def _on_play_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active():
            self.start_slideshow()
        else:
            self.stop_slideshow()

    def start_slideshow(self) -> None:
        """Tick next(loop) every configured interval (PLAN §4 hop 5).
        timeout_add (ms) rather than timeout_add_seconds because the config
        contract allows half-second intervals."""
        self._stop_tick()
        ms = max(100, int(round(self._config["interval_sec"] * 1000)))
        self._slide_id = GLib.timeout_add(ms, self._slide_tick)
        self.play_button.set_icon_name("media-playback-pause-symbolic")
        self.play_button.set_tooltip_text("Pause slideshow")
        # P12: playback starts the hide countdown fresh (the timer rides the
        # activity clock, not the tick clock — ticks never re-arm it).
        self._last_activity_ms = GLib.get_monotonic_time() // 1000
        self._arm_hide_timer()

    # -- chrome auto-hide (FTR2 / P12) ----------------------------------------

    def _cursor_activity(self) -> None:
        """Single activity seam (P12): pointer motion/enter/click/scroll
        stamp the clock; the keyboard deliberately never does.  Reveals
        hidden chrome ALWAYS, and re-arms the hide countdown only while
        playing — stopped/paused chrome can never hide."""
        self._last_activity_ms = GLib.get_monotonic_time() // 1000
        if self._chrome_hidden:
            self._toolbar.set_reveal_top_bars(True)
            self._chrome_hidden = False
        if self._slide_id:
            self._arm_hide_timer()
        else:
            self._disarm_hide_timer()

    def _arm_hide_timer(self) -> None:
        self._disarm_hide_timer()
        self._hide_id = GLib.timeout_add(HIDE_AFTER_MS, self._hide_tick)

    def _disarm_hide_timer(self) -> None:
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = 0

    def _hide_tick(self) -> bool:
        self._hide_id = 0
        if not self._slide_id:
            return False
        if chrome_should_hide(
            GLib.get_monotonic_time() // 1000, self._last_activity_ms, True
        ):
            self._toolbar.set_reveal_top_bars(False)  # the one mechanism
            self._chrome_hidden = True
        else:  # clock raced the one-shot timer: re-arm, don't drop it
            self._arm_hide_timer()
        return False

    def _slide_tick(self) -> bool:
        want = normalize_orientation(self._config.get("slide_orientation"))
        if want != "all" and self.paths:
            # Filtered slideshow tick (FTR2 / P11): pure walk over the wrap
            # window; dimension probes are budgeted per tick so a huge album
            # can never stall the UI on one timer fire.
            budget = [ORIENT_PROBE_BUDGET]
            unprobed = [False]  # budget ran dry mid-scan (not a real miss)
            nxt = next_matching_index(
                self._orient_matchers(budget, unprobed), self.index, True, want
            )
            if nxt is None:
                if unprobed[0]:
                    # Candidates are still probing: the scan is INCONCLUSIVE,
                    # not a zero-match verdict.  Keep the slide, retry next
                    # tick — the auto-stop is deferred while any dimension is
                    # unknown (PLAN §15.1.B), then stops exactly as in FTR2.
                    return True
                # Zero match on FULLY KNOWN dimensions: auto-stop through the
                # toggle handler so the play button re-renders
                # (stop_slideshow direct would leave the button stuck on
                # "pause").  The slide stays untouched.
                self.play_button.set_active(False)
                return False
            if self._orient_dims.get(self.paths[nxt]) is None:
                # Defensive (P15): matchers only return True for KNOWN
                # dimensions, so a tick can never LAND on a still-unprobed
                # candidate; should that invariant ever break, keep the
                # slide and retry next tick instead of jumping blindly.
                return self._slide_id != 0
            if nxt != self.index:
                self.index = nxt
                self._show_current(self.paths[nxt])
            # nxt == self.index: the only match is what's on screen — keep it
            # displayed (place once) instead of re-decoding forever.
            return self._slide_id != 0
        self.step(1)  # wrap_advance already loops end -> start
        return self._slide_id != 0

    # -- orientation filter (FTR2 / P11; async probe FTR3 / P15) --------------

    def _orient_probe(self, path: str) -> None:
        """Fire-and-forget async dimension probe (FTR3/P15): header read via
        GdkPixbuf.Pixbuf.get_file_info_async; on loaders that refuse
        header-only reads (glycin quirk — P11 probe) fall back to a full
        decode through the P14 async engine.  The result merges into
        _orient_dims on arrival; a broken file caches (0, 0) — probed once —
        exactly like the old sync _orient_size.  The tick NEVER waits: until
        a result lands the candidate stays UNDECIDED (_orient_matchers) —
        never wrongly skipped, never blindly landed on."""
        cancel = self._orient_cancel
        if cancel is None or path in self._orient_dims:
            return
        self._orient_inflight += 1

        def on_header(src, res):
            if cancel is not self._orient_cancel:
                return  # album superseded: this probe is not ours anymore
            self._orient_inflight -= 1
            w = h = 0
            try:
                _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info_finish(res)
            except Exception:  # noqa: BLE001 — loader quirk: header unknown
                pass
            if w > 0 and h > 0:
                self._orient_dims[path] = (w, h)
                return
            self._orient_probe_decode(path, cancel)

        GdkPixbuf.Pixbuf.get_file_info_async(path, cancel, on_header)

    def _orient_probe_decode(self, path: str, cancel) -> None:
        """Dims fallback: async full decode via the P14 engine at the
        pump's LOW priority — never blocks the loop, never outranks a
        real slide load."""
        self._orient_inflight += 1

        def on_pixbuf(pb, _err, _nat=None):
            if cancel is not self._orient_cancel:
                return
            self._orient_inflight -= 1
            self._orient_dims[path] = (
                (pb.get_width(), pb.get_height()) if pb is not None else (0, 0)
            )

        self._load_pixbuf_async(path, GLib.PRIORITY_LOW, cancel, on_pixbuf)

    def _orient_matchers(self, budget: list, unprobed: list) -> list:
        """One matcher closure per path (FTR3/P15 rework of the FTR2 scan).
        KNOWN dimensions decide exactly as in FTR2.  An UNKNOWN file is
        never decided blindly: the matcher kicks a fire-and-forget probe
        (budgeted per tick; ORIENT_PROBE_BUDGET doubles as the in-flight
        cap) and leaves the candidate UNDECIDED for this tick — the scan
        flags `unprobed`, which makes the tick inconclusive rather than a
        zero-match verdict (see _slide_tick).  The candidate therefore is
        never wrongly skipped: it stays in the set and every later tick
        re-evaluates it, landing on it the moment its dimensions arrive and
        match.  Consequences, both PLAN §15.1.B: the tick can only LAND on
        a confirmed match (no blind jumps while probes are pending), and
        the zero-match auto-stop is reachable only once every candidate's
        dimensions are known — deferred by pending probes, then exactly as
        in FTR2."""

        def make(path):
            def matcher(want):
                d = self._orient_dims.get(path)
                if d is not None:
                    return img_matches_orientation(d[0], d[1], want)
                if budget[0] > 0 and self._orient_inflight < ORIENT_PROBE_BUDGET:
                    budget[0] -= 1
                    self._orient_probe(path)
                unprobed[0] = True
                return False

            return matcher

        return [make(p) for p in self.paths]

    def _on_orient_toggled(self, btn: Gtk.CheckButton) -> None:
        if not btn.get_active():
            return  # only the newly selected radio of the group applies
        for value, radio in self._orient_buttons.items():
            if radio is btn:
                self.set_orientation(value)
                return

    def set_orientation(self, value: str) -> None:
        """Radio entry point (P11): normalize + persist immediately."""
        self._config["slide_orientation"] = normalize_orientation(value)
        save_config(self._config)

    def stop_slideshow(self) -> None:
        self._stop_tick()
        # P12: chrome is revealed whenever nothing plays, and the countdown
        # dies with the ticker (also covers the filter zero-match stop,
        # which arrives here through the play-button toggle).
        self._disarm_hide_timer()
        if self._chrome_hidden:
            self._toolbar.set_reveal_top_bars(True)
            self._chrome_hidden = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Play slideshow")

    def _stop_tick(self) -> None:
        if self._slide_id:
            GLib.source_remove(self._slide_id)
            self._slide_id = 0

    def set_interval(self, seconds: float) -> None:
        """Menu interval change: persist immediately, re-arm a live ticker."""
        self._config["interval_sec"] = clamp_interval(seconds)
        save_config(self._config)
        if self._slide_id:
            self.start_slideshow()

    # -- animations (P5) --------------------------------------------------------

    def _apply_animations(self) -> None:
        crossfade, ms = transition_for(self._config["animations"])
        self._anim = crossfade
        self.stack.set_transition_type(
            Gtk.StackTransitionType.CROSSFADE
            if crossfade
            else Gtk.StackTransitionType.NONE
        )
        self.stack.set_transition_duration(ms)

    def set_animations(self, enabled: bool) -> None:
        """Menu switch: persist + re-apply the crossfade carrier settings."""
        self._config["animations"] = bool(enabled)
        save_config(self._config)
        self._apply_animations()

    def _on_anim_state_set(self, switch, value: bool) -> bool:
        self.set_animations(value)
        switch.set_state(value)
        return True  # we manage the widget state ourselves

    # -- input ----------------------------------------------------------

    def _on_key_pressed(self, _ctrl, keyval, _keycode, state) -> bool:
        if keyval == Gdk.KEY_Escape and self._fullscreen:
            self.toggle_fullscreen()
            return True
        if state & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SUPER_MASK
        ):
            return False
        if keyval == Gdk.KEY_Left:
            self.step(-1)
            return True
        if keyval == Gdk.KEY_Right:
            self.step(1)
            return True
        if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.zoom_by_step(1)
            return True
        if keyval in (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract):
            self.zoom_by_step(-1)
            return True
        if keyval == Gdk.KEY_1:
            self.set_zoom_fit()
            return True
        if keyval == Gdk.KEY_0:
            self.set_zoom_one_to_one()
            return True
        if keyval == Gdk.KEY_F11:
            self.toggle_fullscreen()
            return True
        return False

    def _on_scroll(self, ctrl, dx, dy) -> bool:
        self._cursor_activity()  # P12: wheel is pointer activity too
        if ctrl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            self.zoom_by_wheel(dy, dx)  # Ctrl+wheel = zoom (P3)
            return True
        delta = dy if dy else dx
        if delta > 0:
            self.step(1)
        elif delta < 0:
            self.step(-1)
        return True

    # -- folder scan + monitor -------------------------------------------

    def _adopt_folder(self, folder: str, on_ready=None) -> None:
        # FTR1: no cache work here — open_path continues into _show_current,
        # whose single _plan_precache point re-prunes the adopted window.
        # FTR3 (P15): adopting means (re)starting the async listing of
        # *folder* + a fresh orientation-probe cycle; nothing here blocks.
        self.folder = folder
        self._orient_dims = {}  # P11: dimension probes are per-album facts
        if self._nat_probe_cancel is not None:
            self._nat_probe_cancel.cancel()  # FTR5: stale-album dims probe
            self._nat_probe_cancel = None
        if self._orient_cancel is not None:
            self._orient_cancel.cancel()
        self._orient_cancel = Gio.Cancellable()
        self._orient_inflight = 0
        self._watch_folder(folder)
        self._start_listing(on_ready)

    # -- async folder listing (FTR3 / P15) -----------------------------------

    def _start_listing(self, on_ready=None) -> None:
        """Kick the async enumeration of self.folder (FTR3/P15):
        enumerate_children_async -> next_files_async batches -> pure
        pipeline -> atomic swap.  A still-running cycle is CANCELLED here
        (one cancellable per adopt/rescan cycle); the current image list
        stays valid until the new one swaps in.  NOFOLLOW off: file types
        resolve through symlinks, matching the old scandir semantics."""
        self._cancel_listing()
        cancel = Gio.Cancellable()
        self._listing_cancel = cancel
        self._listing_ready = on_ready
        self._listing_names = []
        folder = self.folder or ""
        try:
            Gio.File.new_for_path(folder).enumerate_children_async(
                "standard::name,standard::type",
                Gio.FileQueryInfoFlags.NONE,
                GLib.PRIORITY_DEFAULT,
                cancel,
                lambda src, res: self._on_enumerated(cancel, src, res),
            )
        except GLib.Error:
            # Sync kick failure on a dead folder — the empty listing is the
            # honest answer (old scan_folder returned [] for OSError).
            cancel.cancel()
            self._listing_cancel = None
            self._listing_ready = None
            self._apply_listing([])
            if on_ready is not None:
                on_ready(self.paths)

    def _cancel_listing(self) -> None:
        """Abort any in-flight enumeration (supersede or teardown).  The
        dropped enumerator closes via close_async — never a sync close on
        the main loop."""
        self._listing_ready = None
        if self._listing_cancel is not None:
            self._listing_cancel.cancel()
            self._listing_cancel = None
        enum, self._listing_enum = self._listing_enum, None
        if enum is not None:
            enum.close_async(GLib.PRIORITY_LOW, None, None)

    def _on_enumerated(self, cancel, src, res) -> None:
        if cancel is not self._listing_cancel:
            return  # superseded cycle: drop silently (PLAN §15.1.B)
        try:
            self._listing_enum = src.enumerate_children_finish(res)
        except GLib.Error:
            # Unreadable/missing folder == old OSError -> empty listing.
            self._finish_listing([])
            return
        self._listing_next()

    def _listing_next(self) -> None:
        cancel = self._listing_cancel
        self._listing_enum.next_files_async(
            256,
            GLib.PRIORITY_DEFAULT,
            cancel,
            lambda src, res: self._listing_batch(cancel, src, res),
        )

    def _listing_batch(self, cancel, src, res) -> None:
        if cancel is not self._listing_cancel:
            return  # superseded tick of an older cycle
        try:
            infos = src.next_files_finish(res)
        except GLib.Error as err:
            if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                return  # superseded: silent
            self._finish_listing([])  # folder died mid-scan: empty listing
            return
        self._listing_names.extend(
            (
                info.get_name(),  # raw name (standard::name was requested)
                info.get_file_type() == Gio.FileType.REGULAR,
            )
            for info in infos
        )
        if infos:
            self._listing_next()
        else:
            self._finish_listing(self._listing_names)

    def _finish_listing(self, entries) -> None:
        """Atomic swap-in point: the cycle retires its cancellable +
        enumerator, the PURE pipeline orders the batch, and the live state
        updates in one shot (continuation last — it may re-enter here via
        open_path)."""
        folder = self.folder or ""
        self._listing_cancel = None
        on_ready, self._listing_ready = self._listing_ready, None
        enum, self._listing_enum = self._listing_enum, None
        if enum is not None:
            enum.close_async(GLib.PRIORITY_LOW, None, None)
        self._apply_listing(sort_image_paths(folder, entries))
        if on_ready is not None:
            on_ready(self.paths)

    def _apply_listing(self, new_paths: list[str]) -> None:
        """Atomic swap of a fresh scan into the live state (FTR3/P15).  The
        body is the old synchronous _rescan tail verbatim — the current
        slide is never re-decoded just because the listing changed."""
        if new_paths == self.paths:
            return False  # window unchanged: nothing to prune or re-queue
        self.paths = new_paths
        keep = set(new_paths)  # P11: dims cache follows the scan list
        self._orient_dims = {p: d for p, d in self._orient_dims.items() if p in keep}
        if self.current_path in new_paths:
            self.index = new_paths.index(self.current_path)
            self._refresh_subtitle()  # (i/N) updated, image untouched
            # Rescan churn (FTR1): keys whose paths left the scan list drop
            # with the prune — the re-plan covers the shifted keep-set too.
            self._plan_precache()
        elif new_paths:
            self.index = min(max(self.index, 0), len(new_paths) - 1)
            self._show_current(new_paths[self.index])  # re-plans inline
        else:
            self.index = -1
            self._refresh_subtitle()
            self._plan_precache()  # folder emptied: cache collapses to empty

    def _watch_folder(self, folder: str) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        try:
            monitor = Gio.File.new_for_path(folder).monitor_directory(
                Gio.FileMonitorFlags.NONE, None
            )
        except GLib.Error:
            return
        monitor.connect("changed", self._on_folder_changed)
        self._monitor = monitor

    def _on_folder_changed(self, _mon, _file, _other, _ev) -> None:
        # Gio.FileMonitor::changed carries (monitor, file, other_file,
        # event_type) on this GLib — P8 churn smoke caught a stale arity.
        # Coalesce event bursts: rescan once, shortly after the last change.
        if self._rescan_id:
            GLib.source_remove(self._rescan_id)
        self._rescan_id = GLib.timeout_add(250, self._rescan)

    def _rescan(self) -> bool:
        self._rescan_id = 0
        if not self.folder:
            return False
        # FTR3 (P15): the debounced rescan runs through the SAME async
        # listing cycle (supersedes any in-flight enumeration; the swap is
        # atomic — see _apply_listing).  Nothing on this path ever scans
        # synchronously again.
        self._start_listing()
        return False

    def _on_close(self, *_args) -> bool:
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._rescan_id:
            GLib.source_remove(self._rescan_id)
            self._rescan_id = 0
        self._stop_tick()
        if self._prune_id:
            GLib.source_remove(self._prune_id)
            self._prune_id = 0
        # P12 teardown: no hide countdown may outlive the window.
        self._disarm_hide_timer()
        self._chrome_hidden = False
        # FTR1 teardown (PLAN §11.3): closing the app must not leak decoded
        # pixbufs nor leave the idle pump queued.  _on_close (close-request)
        # is the existing teardown hook — no new one needed.
        if self._precache_pump_id:
            GLib.source_remove(self._precache_pump_id)
            self._precache_pump_id = 0
        self._precache_pending = []
        self._precache.clear()
        self._precache_charges.clear()
        # FTR3 teardown (P14): no pending async load may outlive the window —
        # cancel both engines; their completions drop out at the identity
        # guards above without touching (repopulating) anything.
        self._cancel_slide_load()
        if self._precache_decode_cancel is not None:
            self._precache_decode_cancel.cancel()
            self._precache_decode_cancel = None
        # FTR3 teardown (P15): the listing cycle and the orientation probes
        # die with the window too — their completions drop at the identity
        # guards even if already queued.
        self._cancel_listing()
        if self._open_kind_cancel is not None:
            self._open_kind_cancel.cancel()
            self._open_kind_cancel = None
        if self._orient_cancel is not None:
            self._orient_cancel.cancel()
            self._orient_cancel = None
        # FTR5 teardown (P18): the natural-dims correction probe too.
        if self._nat_probe_cancel is not None:
            self._nat_probe_cancel.cancel()
            self._nat_probe_cancel = None
        # FTR5 teardown (P19): the full-res upgrade and the growth timer.
        self._drop_full_res()
        if self._growth_id:
            GLib.source_remove(self._growth_id)
            self._growth_id = 0
        return False


class LookseeApp(Adw.Application):
    def __init__(self):
        # NON_UNIQUE: keeps headless smokes independent of any other session.
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN
            | Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.window: LookseeWindow | None = None
        self._black_css_installed = False
        self._css_provider: Gtk.CssProvider | None = None

    def _install_black_css(self) -> None:
        """One Gtk.CssProvider from build_black_css(), attached ONCE per
        display at startup (PLAN §13.1.1).  APPLICATION priority beats the
        libadwaita theme, so the scoped rules win without a theme override."""
        if self._black_css_installed:
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_string(build_black_css())
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._css_provider = provider  # keep a reference alive (R6)
        self._black_css_installed = True

    def _ensure_window(self) -> LookseeWindow:
        if self.window is None:
            self._install_black_css()
            self.window = LookseeWindow(application=self)
        return self.window

    def do_activate(self) -> None:
        win = self._ensure_window()
        win.present()
        if win.current_path is None:
            # No CLI path -> FileChooser dialog (PLAN §4 hop 1).
            dialog = Gtk.FileDialog(title="Open an image")
            filters = Gtk.FileFilter()
            filters.set_name("Images")
            for ext in sorted(SUPPORTED_EXTS):
                filters.add_pattern(f"*{ext}")
            dialog.set_initial_filter(filters)
            dialog.open(win, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # user cancelled
        if gfile is not None and gfile.get_path():
            self.window.open_path(gfile.get_path())

    def do_open(self, files, n_files, hint) -> None:
        win = self._ensure_window()
        win.present()
        if files:
            # FTR3 (P15): dir-vs-file decided async (open_any) — zero sync
            # filesystem calls on the open path.
            win.open_any(files[0].get_path() or "")


def main() -> int:
    GLib.set_application_name("Looksee")
    GLib.set_prgname("looksee")
    app = LookseeApp()
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK
    )
    # Explicit argv: GApplication would otherwise see the interpreter +
    # script path from /proc/self/cmdline as file arguments.
    return app.run(["looksee", *sys.argv[1:]])


