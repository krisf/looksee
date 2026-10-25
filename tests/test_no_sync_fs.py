"""FTR3 static gate (PLAN §15.2 P16): no synchronous filesystem IO in src/.

Blocking a GTK main loop on storage is exactly the hang FTR3 removes
(slow rclone/restic mounts).  This lint proves the source stays clean:
every *storage* read must go through a GLib async variant.  Stdlib only
(`tokenize` + `re`), like the rest of the suite.

Method: read each src/*.py, blank COMMENT and STRING tokens (so prose in
comments/docstrings mentioning a forbidden name can never trip the lint),
then forbid these call-name prefixes, allowing the `_async` variants to
pass naturally (the name must be followed by `(` — `get_file_info_async(`
never matches `get_file_info` + `(`):

    new_from_file(    Pixbuf sync decode            -> read_async engine
    get_file_info(    Pixbuf sync header probe      -> get_file_info_async
    query_info(       Gio.File sync metadata        -> query_info_async
    os.listdir        blocking enumeration          -> enumerate_children_async
    os.scandir        blocking enumeration          -> enumerate_children_async

Config load/save `open()` is deliberately exempt: it touches only the
local dotdir, once, at startup/quit — never album data (PLAN §15.2 P16).
"""
from __future__ import annotations

import io
import pathlib
import re
import tokenize
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Forbidden sync storage calls.  The lookbehind keeps the name at a token
# start (attribute receivers like `Pixbuf.new_from_file(` DO match — that
# is the point; a longer identifier like `my_new_from_file` does not).
# The trailing `\s*\(` means a `..._async` sibling NEVER matches because
# the bare name then is followed by `_`, not by the paren.
FORBIDDEN: list[tuple[str, str]] = [
    ("sync pixbuf decode", r"(?<!\w)new_from_file\s*\("),
    ("sync header probe", r"(?<!\w)get_file_info\s*\("),
    ("sync metadata query", r"(?<!\w)query_info\s*\("),
    ("sync enumeration", r"(?<!\w)os\.listdir\b"),
    ("sync enumeration", r"(?<!\w)os\.scandir\b"),
]


def scrub(source: str) -> str:
    """Blank comment + string-literal token text (length-preserving, so
    line numbers in failures stay true); code tokens pass through."""

    def blank(tok) -> str:
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            return "\n" * tok.string.count("\n") + " " * len(
                tok.string.rsplit("\n", 1)[-1]
            )
        return tok.string

    try:
        return "".join(blank(t) for t in tokenize.generate_tokens(
            io.StringIO(source).readline
        ))
    except (tokenize.TokenError, IndentationError):
        # Unparsable file: py_compile is the gate for that; scan raw so a
        # hidden sync call can never hide behind a tokenizer hiccup.
        return source


class NoSyncFsIo(unittest.TestCase):
    def test_src_has_no_sync_storage_calls(self) -> None:
        files = sorted((ROOT / "src").glob("*.py"))
        self.assertTrue(files, "src/ must contain the app")
        offenses: list[str] = []
        for path in files:
            text = scrub(path.read_text(encoding="utf-8"))
            for label, pattern in FORBIDDEN:
                for m in re.finditer(pattern, text):
                    line = text.count("\n", 0, m.start()) + 1
                    offenses.append(
                        f"{path.name}:{line} {label}: {m.group(0).strip()}"
                    )
        self.assertEqual(offenses, [], "sync FS calls in src:\n" + "\n".join(offenses))

    def test_async_variants_actually_pass_the_lint(self) -> None:
        """The lint must discriminate: async spellings pass, sync fail."""
        code = (
            "x = Pixbuf.new_from_file_async(a)\n"
            "y = Pixbuf.get_file_info_async(a)\n"
            "z = f.query_info_async(a)\n"
            "w = f.enumerate_children_async(a)\n"
        )
        text = scrub(code)
        for _, pattern in FORBIDDEN:
            self.assertIsNone(re.search(pattern, text))
        for sync in (
            "Pixbuf.new_from_file(a)",
            "Pixbuf.get_file_info(a)",
            "f.query_info(a)",
            "os.listdir(d)",
            "os.scandir(d)",
        ):
            text = scrub(sync)
            self.assertTrue(
                any(re.search(p, text) for _, p in FORBIDDEN),
                f"lint must catch: {sync}",
            )

    def test_prose_mention_does_not_trip_the_lint(self) -> None:
        """Docstrings/comments naming a forbidden call are blanked first."""
        code = (
            '"""scan via os.scandir (twin) — not Pixbuf.new_from_file("""\n'
            "# query_info( is the sync trap we removed\n"
        )
        text = scrub(code)
        for _, pattern in FORBIDDEN:
            self.assertIsNone(re.search(pattern, text))


if __name__ == "__main__":
    unittest.main()
