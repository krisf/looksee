# Contributing

Small, boring patches welcome.

## Dev environment

Ubuntu 24.04+ with `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`
(`apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 xvfb` for headless tests).
Run from a checkout:

    /usr/bin/python3 src/looksee.py [PATH]

## Tests

    /usr/bin/python3 -m unittest discover -s tests -q   # pure-logic suite
    LOOKSEE_SMOKE_MODE=quit scripts/headless-smoke.sh scripts/smoke_p3_zoom.py

`scripts/smoke_p*.py` are in-process GUI drivers (they drive the real app and
assert real GTK state under Xvfb). Every phase gate in this repo's history ran
`unittest + py_compile + every smoke` before committing - keep that.

Two standing rules the test-suite enforces mechanically:

- **No synchronous filesystem calls in the GUI path**
  (`tests/test_no_sync_fs.py`). All I/O is GLib-async.
- **Packaging contract pinned** (`tests/test_packaging.py`): control fields,
  installed paths, wrapper behaviour.

## Pull requests

Describe the user-visible delta, paste the test/smoke output, and update
`CHANGELOG.md`.

Before touching the IO paths, the skin, the auto-hide state machine or the
packaging, read [`AGENTS.md`](AGENTS.md) — it records the constraints that are
invisible in the code and the incidents behind them.
