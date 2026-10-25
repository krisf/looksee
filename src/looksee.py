#!/usr/bin/env python3
"""Looksee — a dark GTK4/libadwaita image viewer with folder navigation
and a configurable slideshow (see README.md / PLAN.md).

FTR3 (PLAN §15.0) authorized splitting the single file once it crossed
1500 lines: this module is the stable entry point and PUBLIC SURFACE —
pure logic lives in looksee_io.py, the GUI in looksee_ui.py (sibling
files; the installed layout ships all three).  sys.path is bootstrapped
here so loading THIS file by path (tests, smoke drivers, the packaged
wrapper) keeps working.
Run:  /usr/bin/python3 src/looksee.py [PATH-or-FILE]
"""
from __future__ import annotations

import os
import sys

# Loading by path (importlib spec / `python3 .../looksee.py`) does not
# put src/ on sys.path — the sibling modules below need it there.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from looksee_io import *  # noqa: E402,F401,F403
from looksee_ui import *  # noqa: E402,F401,F403
from looksee_ui import LookseeApp, LookseeWindow, main  # explicit (tooling)

if __name__ == "__main__":
    raise SystemExit(main())
