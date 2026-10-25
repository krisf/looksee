"""P10 pure-logic tests: OLED-black skin builder (PLAN §13.1.1).

No display needed: everything is string contract + a display-less
Gtk.CssProvider parse check (verified head-safe by the P10 probe).
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("looksee", ROOT / "src" / "looksee.py")
looksee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(looksee)


class BlackCssContract(unittest.TestCase):
    def setUp(self) -> None:
        self.css = looksee.build_black_css()

    def test_true_black_present(self) -> None:
        # The OLED requirement: the exact literal #000000 (lowercase).
        self.assertIn("#000000", self.css)

    def test_every_selector_scoped_under_class(self) -> None:
        cls = looksee.BLACK_CSS_CLASS
        self.assertEqual(cls, "looksee-black")
        # Selector text = whatever precedes each '{' (after the previous '}').
        selectors = [
            sel.rsplit("}", 1)[-1].strip() for sel in re.findall(r"([^{}]*)\{", self.css)
        ]
        self.assertTrue(selectors, "css must have selectors")
        for sel in selectors:
            for part in sel.split(","):
                self.assertIn(
                    f".{cls}", part, f"unscoped selector: {part!r}"
                )

    def test_window_and_headerbar_black(self) -> None:
        self.assertRegex(self.css, r"window\.looksee-black\s*\{[^}]*#000000")
        header_block = re.search(
            r"headerbar\.looksee-black\s*\{([^}]*)\}", self.css
        )
        self.assertIsNotNone(header_block)
        self.assertIn("background-color: #000000;", header_block.group(1))

    def test_headerbar_flat_no_border_no_shadow(self) -> None:
        header_block = re.search(
            r"headerbar\.looksee-black\s*\{([^}]*)\}", self.css
        ).group(1)
        self.assertIn("border-bottom: none;", header_block)
        self.assertIn("box-shadow: none;", header_block)

    def test_content_transparent(self) -> None:
        for widget in ("scrolledwindow", "stack", "viewport"):
            self.assertIn(f".looksee-black {widget}", self.css)
        self.assertIn("background-color: transparent;", self.css)

    def test_no_popover_or_button_rules(self) -> None:
        # Popovers/menus/buttons/spins keep libadwaita defaults: they are
        # never targeted by this stylesheet.
        for forbidden in ("popover", "button", "spinbutton", "menu"):
            self.assertNotIn(forbidden, self.css.lower())


class CssProviderParses(unittest.TestCase):
    def test_provider_loads_without_warnings(self) -> None:
        from gi.repository import GLib, Gtk

        messages: list[str] = []

        def capture(_domain, _level, message):
            messages.append(message)
            return False  # not handled: default behaviour preserved elsewhere

        mask = GLib.LogLevelFlags.LEVEL_WARNING | GLib.LogLevelFlags.LEVEL_CRITICAL
        cookie = GLib.log_set_handler("gtk", mask, capture)
        try:
            provider = Gtk.CssProvider()
            provider.load_from_string(looksee.build_black_css())
        finally:
            GLib.log_remove_handler("gtk", cookie)
        self.assertEqual(messages, [], f"gtk logged during css parse: {messages}")


if __name__ == "__main__":
    unittest.main()
