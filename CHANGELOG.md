# Changelog

All versions ship as a hermetic `.deb` built from `packaging/build-deb.sh`.

## 0.2.2
- **Large-image tiering.** Images larger than the display tier decode at
  2x-viewport scale (`decode_tier_px`: floor 1024, cap 4096): an 8000x6000
  JPEG now keeps ~17 MB in memory instead of ~144 MB, so the preload cache
  holds ~9-18 big slides instead of one (FTR5).
- **Deep-zoom quality is not sacrificed:** zooming past tier density
  triggers one background full-resolution decode for the current slide
  (single-flight, cancellable, dropped on navigation), swapped in while the
  upsampled view stays on screen. Window growth >25% re-decodes at the new
  tier (debounced; shrink never re-decodes).
- **Long filenames no longer clamp window size:** the headerbar title and
  subtitle ellipsize (`EllipsizeMode.END`, `max-width-chars 48`), so a
  300-character name no longer forces a huge minimum window width (FTR4).

## 0.2.1
- **Fully async filesystem I/O (FTR3).** Every filesystem call moved off the
  main loop: slide decode (`read_async` + `new_from_stream_async`), folder
  enumeration (`enumerate_children_async` batches), mtime stats
  (`query_info_async`), header probes (`get_file_info_async`). On slow mounts
  (network shares, rclone/restic-style FUSE) the window stays fully usable:
  the previous slide stays visible until the new one decodes, stale paints
  are dropped, and navigating cancels in-flight loads.
- Enforced forever by `tests/test_no_sync_fs.py`, a lint that fails if any
  blocking filesystem call reappears in the GUI path.

## 0.2.0
- **OLED-black skin:** window and headerbar are pixel-off `#000000`;
  popovers and controls keep libadwaita defaults for readability (FTR2).
- **Orientation-filtered slideshow:** All / Landscape / Portrait in the
  slideshow popover, persisted; applies to slideshow playback only (manual
  navigation is never filtered); squares match neither; a filter that
  matches nothing auto-stops instead of spinning.
- **Auto-hiding chrome:** while a slideshow plays and the cursor is idle
  for 5 s the headerbar retracts (`Adw.ToolbarView` reveal API) and the
  photo claims the full window; any pointer motion/click/scroll reveals it
  and re-arms. Slide ticks never reset the idle clock; keyboard navigation
  keeps working while hidden.

## 0.1.1
- **Look-ahead preload cache (FTR1):** keeps decoded pixbufs for
  current + next two + previous one (wrap-around); 200 MB pixel budget with
  furthest-first eviction; single low-priority idle pump; cache is prefill
  only - a miss can never make navigation wrong, only slower.

## 0.1.0
- Initial release: dark single-window GTK4/libadwaita image viewer.
  Folder navigation (buttons, arrows, wheel, wrap-around, natural sort,
  live folder monitoring), zoom (fit / 1:1 / 0.1x-8x, Ctrl+wheel, keys),
  F11 fullscreen, FTR-era slideshow loop with configurable interval
  (default 5 s), optional 220 ms crossfade (off by default - hard cuts),
  `.deb` packaging, config at `~/.config/looksee/config.json`.
