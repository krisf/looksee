# Working notes for coding agents (and humans)

The short version of "what will cost you a day if you don't know it". Every
rule below is enforced somewhere — by a unit test, a headless smoke, or the
packaging gate — so treat a red test as a signal that the rule is load
bearing, not as an obstacle.

Docstrings and test names cite a pre-build design document
(`PLAN §15.0`, requirement ids `R1`…), the feature ids (`FTR1`…`FTR5`) and
the build phases (`P3`…`P19`). That document is not part of this repository;
read those tokens as historical phase markers. What they refer to *is* here,
and is described below.

## Layout — the FTR3 three-way split

| File | Contains | Needs a display? |
|---|---|---|
| `src/looksee.py` | stable entry point and public surface; bootstraps `sys.path` | yes |
| `src/looksee_io.py` | **pure, `gi`-free logic**: natural sort, config persistence, FTR1 look-ahead window math, FTR2 orientation and auto-hide predicates, zoom / transition / subtitle helpers | no |
| `src/looksee_ui.py` | the window: widgets, the GLib-async IO engines, the FTR1 pre-cache pump, FTR2 filter/auto-hide timers, the P5 menu popover | yes |

The split was authorised once the single file crossed 1500 lines. Keep it:
**new logic goes in `looksee_io.py`**, so it is unit-testable without a
display, and only widget/`Gio` code goes in `looksee_ui.py`. The `.deb`
installs all three modules.

## The rule that keeps the UI unblocked

`tests/test_no_sync_fs.py` lints the source and **fails the build** on any
synchronous storage call in UI paths:

| Forbidden | Why |
|---|---|
| `GdkPixbuf.Pixbuf.new_from_file()` | decodes on the caller's thread — a big JPEG freezes the window |
| `File.get_file_info()` | stat-style blocking probe |
| `File.query_info()` | blocking metadata query (EXIF/orientation probes) |
| `os.listdir` / `os.scandir` | folder enumeration must go through `Gio.File.enumerate_children_async` |

The lint blanks comments and docstrings before matching, so prose may *name*
those calls; there is also `test_async_variants_actually_pass_the_lint`,
which proves the sanctioned async path is not itself a lint violation. If you
believe a synchronous call is genuinely required, the honest fix is a
background `Gio` task plus a cancellable callback — not a lint exception.

## Running things

```
/usr/bin/python3 -m unittest discover tests            # no display needed
scripts/headless-smoke.sh src/looksee.py testdata/1.png
scripts/headless-smoke.sh scripts/smoke_p9_autohide.py
```

- **Use `/usr/bin/python3`.** The interpreter first on `PATH` in many shells
  is a venv with no PyGObject, and the resulting `ImportError` looks exactly
  like an application bug.
- `scripts/headless-smoke.sh` is the **only** sanctioned way to run the GUI
  headlessly (`R1`). It sets `GSK_RENDERER=cairo`, `LIBGL_ALWAYS_SOFTWARE=1`,
  `GDK_BACKEND=x11`, and unsets `WAYLAND_DISPLAY` under `xvfb-run`; without
  that recipe a GTK4 app dies silently under Xvfb and you will debug nothing.
  It has two modes via `LOOKSEE_SMOKE_MODE`: `alive` (default — the app must
  *survive* the whole window and be killed by the watchdog, rc 124/143,
  elapsed ≥ 6 s) and `quit` (the app must exit 0 by itself). "It didn't
  crash" is only meaningful in the right mode.
- `scripts/smoke_p*.py` print a `CHECKS:` block of `P<phase> <name> <value>`
  lines and raise `SystemExit` non-zero on failure. Add a new `smoke_pNN_*.py`
  for behaviour that only exists with a real window.

## Skin discipline

`tests/test_skin.py` pins the monochrome look: exactly one scoped provider,
the class is `looksee-black`, `window.looksee-black` must set
`background-color: #000000` (true OLED black), and the header bar must be
flat — `border-bottom: none`, no shadow. The CSS must **not** contain
popover or generic button rules; restyling a shared widget selector to
"improve" one corner of the UI has broken others before. Install the provider
once per display, scoped to the app class.

## Auto-hide / chrome (`P9`, FTR2)

The predicates are pure and live in `looksee_io.py`; the timers live in the
window. The clock is monotonic and there is an explicit guard for a backwards
clock delta (`test_clock_delta_backwards_is_no_hide`) — a suspend/resume must
not trigger a hide. Slideshow playback, filter changes and pointer motion all
interact with that state machine, so test state transitions at the predicate
level first and only then in a smoke.

## Large images (`FTR5`)

Images above the display tier decode at 2×-viewport scale (`decode_tier_px`:
floor 1024, cap 4096) so an 8000×6000 JPEG costs ~17 MB instead of ~144 MB
and the preload cache can hold several big slides. Deep zoom is not sacrificed:
zooming past tier density triggers **one** background full-resolution decode
for the current slide — single-flight, cancellable, dropped on navigation.
Do not turn that into a per-zoom-step decode; it stamps the queue.

## Test data

`testdata/1.png … 12.png` are **generated**, by `testdata/gen_pngs.py`
(stdlib `zlib` + `struct`, deterministic: same input → byte-identical PNGs,
sizes ramping 64…1024 px). `tests/test_png_generated.py` enforces it. Keep it
that way: real photographs — even your own — do not belong in a public test
fixture, and a fixture you cannot regenerate is a fixture you cannot fix.

## Packaging

`packaging/build-deb.sh` builds a hermetic `.deb` (wrapper script + modules +
`.desktop` + icon); `tests/test_packaging.py` checks the installed layout.
Runtime deps are `python3`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`,
`libadwaita-1-0` — nothing from pip, ever.

## Change discipline

1. Reproduce in a test first; prefer the pure layer.
2. Every fix ships with a gate that fails without it.
3. Never weaken or delete an existing test or smoke to get green. If a gate
   is genuinely wrong, change it in its own commit and say why in the message.
4. Update `CHANGELOG.md` — it is the user-facing record, and it describes
   behaviour in user terms ("an 8000×6000 JPEG now keeps ~17 MB in memory"),
   not in commit terms.
5. Keep the README screenshot filenames stable (`docs/screenshots/*.png`);
   the README leads with them.
