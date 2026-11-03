# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path

import opos.web


class TestSheetInert(unittest.TestCase):
    def setUp(self):
        self.html = opos.web.page()
        self.js2 = (Path(opos.web.__file__).with_name("web_static") / "js-2.js").read_text(encoding="utf-8")

    def test_helper_inerts_background_only(self):
        start = self.html.find("function setBehind(")
        end = self.html.find("function openSheet(", start)
        snippet = self.html[start:end]
        self.assertIn("#phone>", snippet)
        self.assertIn("inert", snippet)
        self.assertIn(":not(#sheet)", snippet)
        self.assertIn(":not(#scrim)", snippet)
        self.assertIn(":not(#notice)", snippet)
        self.assertNotIn("#demo", snippet)

    def test_open_makes_background_inert(self):
        start = self.html.find("function openSheet(){")
        end = self.html.find("function closeSheet(){", start)
        snippet = self.html[start:end]
        self.assertIn("setBehind(true)", snippet)
        self.assertLess(snippet.find("sheetOpen=true"), snippet.find("setBehind(true)"))

    def test_close_restores_background_before_focus(self):
        start = self.html.find("function closeSheet(){")
        focus = self.html.find("meterEl.focus()", start)
        snippet = self.html[start:focus + len("meterEl.focus()")]
        self.assertIn("setBehind(false)", snippet)
        self.assertLess(snippet.find("setBehind(false)"), snippet.find("meterEl.focus()"))

    def test_lives_in_js2(self):
        self.assertIn("function setBehind(", self.js2)
