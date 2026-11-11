# SPDX-License-Identifier: GPL-3.0-or-later
"""Newsreader and Inter Tight are separate files, not part of page()."""
import unittest

from opos import fonts, secure
from opos.web import page
from tests.test_uxp30_budget import WAVE_GATE, served_home_page

STATIC = __import__("pathlib").Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"


class Fonts(unittest.TestCase):
    def test_page_does_not_carry_the_font_bytes(self):
        html = page("t" * 43, "n" * 22)
        self.assertNotIn("@font-face", html)
        self.assertNotIn(".woff2", html)
        self.assertNotIn("fonts.css", html)
        self.assertLessEqual(len(served_home_page({}).decode()), WAVE_GATE)

    def test_the_sheet_swaps_and_keeps_a_system_fallback(self):
        css = (STATIC / "fonts.css").read_text(encoding="utf-8")
        self.assertEqual(css.count("font-display:swap;src"), 4)
        self.assertIn("size-adjust:108.05%", css)
        self.assertIn("size-adjust:99.2%", css)
        self.assertIn('local("Liberation Serif")', css)
        self.assertIn('local("Liberation Sans")', css)
        self.assertIn("unicode-range:U+0000-00FF", css)
        jaryo = (STATIC / "jaryo.css").read_text(encoding="utf-8")
        self.assertNotIn("@font-face", jaryo)
        self.assertNotIn("url(", jaryo)
        self.assertIn("Georgia", jaryo)
        self.assertIn("system-ui", jaryo)
        self.assertIn("Newsreader", jaryo)
        self.assertIn("Inter Tight", jaryo)

    def test_the_files_are_latin_woff2_under_the_ofl(self):
        for name in ("newsreader-400-latin.woff2", "newsreader-700-latin.woff2",
                     "inter-tight-400-latin.woff2", "inter-tight-700-latin.woff2"):
            raw = (STATIC / "fonts" / name).read_bytes()
            self.assertTrue(raw.startswith(b"wOF2"), name)
            self.assertLess(len(raw), 40_000, name)
        self.assertIn("The Newsreader Project Authors", (STATIC / "fonts" / "NEWSREADER-OFL.txt").read_text())
        self.assertIn("The Inter Project Authors", (STATIC / "fonts" / "INTERTIGHT-OFL.txt").read_text())
        self.assertIn("SIL Open Font License", (STATIC / "fonts" / "NEWSREADER-OFL.txt").read_text())

    def test_the_page_may_load_its_own_fonts(self):
        self.assertIn("font-src 'self'", secure.csp("abc"))
        body = fonts.inject(b"<head></head>", "nonceOK")
        self.assertIn(b'href="/fonts.css" nonce="nonceOK"', body)
        self.assertIs(fonts.inject(body, "nonceOK"), body)
