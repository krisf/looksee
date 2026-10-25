"""P6 packaging-contract tests (PLAN §5 shapes, stdlib configparser)."""
from __future__ import annotations

import configparser
import pathlib
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent


class DesktopEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cp = configparser.RawConfigParser()
        cls.cp.optionxform = str  # desktop entries are case-sensitive keys
        cls.cp.read(str(ROOT / "data" / "pro.krisforbes.Looksee.desktop"))
        cls.e = dict(cls.cp["Desktop Entry"])

    def test_core_fields(self) -> None:
        self.assertEqual(self.e["Type"], "Application")
        self.assertEqual(self.e["Name"], "Looksee")
        self.assertEqual(self.e["GenericName"], "Image Viewer")
        self.assertEqual(self.e["Exec"], "looksee %U")
        self.assertEqual(self.e["Icon"], "looksee")
        self.assertEqual(self.e["Categories"], "Graphics;Viewer;")
        self.assertEqual(self.e["Terminal"], "false")

    def test_mime_types_verbatim_loupe_set(self) -> None:
        mimes = self.e["MimeType"].rstrip(";").split(";")
        self.assertTrue(mimes, "MimeType must be non-empty")
        for must in ("image/jpeg", "image/png", "image/webp", "image/avif"):
            self.assertIn(must, mimes)
        system = pathlib.Path("/usr/share/applications/org.gnome.Loupe.desktop")
        if system.exists():  # "reuse its MimeType list verbatim"
            loupe = [
                line.rstrip("\n")
                for line in system.read_text().splitlines()
                if line.startswith("MimeType=")
            ]
            self.assertEqual(
                [f"MimeType={self.e['MimeType']}"], loupe
            )


class ControlFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fields: dict[str, str] = {}
        for line in (ROOT / "packaging" / "DEBIAN" / "control").read_text().splitlines():
            if line and not line.startswith((" ", "\t", ".")):
                k, _, v = line.partition(":")
                cls.fields[k.strip()] = v.strip()

    def test_contract_fields(self) -> None:
        self.assertEqual(self.fields["Package"], "looksee")
        self.assertEqual(self.fields["Version"], "0.2.2")
        self.assertEqual(self.fields["Architecture"], "all")
        self.assertEqual(self.fields["Section"], "graphics")
        self.assertEqual(self.fields["Priority"], "optional")
        self.assertEqual(self.fields["Maintainer"], "Kris Forbes <krisf@users.noreply.github.com>")

    def test_depends(self) -> None:
        deps = self.fields["Depends"]
        for dep in (
            "python3 (>= 3.10)", "python3-gi", "gir1.2-gtk-4.0",
            "gir1.2-adw-1", "libadwaita-1-0",
        ):
            self.assertIn(dep, deps)

    def test_descriptions(self) -> None:
        self.assertTrue(self.fields["Description"])
        raw = (ROOT / "packaging" / "DEBIAN" / "control").read_text()
        self.assertIn("\n .\n", raw)  # has long description
        # 0.1.1 re-mint: FTR1 sentence in the long description (PLAN §11.2 P9).
        self.assertIn("look-ahead cache", raw.lower())
        # 0.2.0 mint: FTR2 sentence (PLAN §13.2 P13) — orientation filter,
        # true-black UI, auto-hiding chrome.
        low = raw.lower()
        self.assertIn("orientation", low)
        self.assertIn("true-black", low)
        self.assertIn("auto-hides", low)
        # 0.2.1 mint: FTR3 sentence (PLAN §15.2 P16) — async storage reads,
        # responsive on very slow mounts.
        self.assertIn("asynchronous", low)
        self.assertIn("slow", low)
        # 0.2.2 mint: FTR5 sentence (PLAN §19.4 P20) — tiered large-image
        # decode + deep-zoom full-res re-decode.
        self.assertIn("large images", low)
        self.assertIn("viewport resolution", low)
        self.assertIn("true resolution", low)


class Wrapper(unittest.TestCase):
    def test_exact_exec_form(self) -> None:
        lines = (
            ROOT / "packaging" / "looksee-wrapper.sh"
        ).read_text().splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertEqual(
            lines[1],
            "exec /usr/bin/python3 /usr/share/looksee/looksee.py \"$@\"",
        )


class IconAndMeta(unittest.TestCase):
    def test_svg_is_valid_scalable_svg(self) -> None:
        root = ET.parse(ROOT / "data" / "looksee.svg").getroot()
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.attrib["viewBox"], "0 0 128 128")

    def test_copyright_template(self) -> None:
        text = (ROOT / "packaging" / "copyright").read_text()
        self.assertIn("GPL-3+", text)
        self.assertIn("Kris Forbes <krisf@users.noreply.github.com>", text)

    def test_build_script_contract(self) -> None:
        script = (ROOT / "packaging" / "build-deb.sh").read_text()
        self.assertIn("dpkg-deb --build --root-owner-group", script)
        self.assertIn("scratch/deb-root", script)
        self.assertIn("buildout/looksee_0.2.2_all.deb", script)
        for path in (
            "$STAGE/usr/bin/looksee",
            # FTR3 split: shim + pure core + UI all ship (PLAN §15.2 P16).
            "usr/share/looksee/looksee.py",
            "usr/share/looksee/looksee_io.py",
            "usr/share/looksee/looksee_ui.py",
            "usr/share/applications",
            "usr/share/icons/hicolor/scalable/apps",
            "usr/share/doc/looksee",
        ):
            self.assertIn(path, script)


if __name__ == "__main__":
    unittest.main()
