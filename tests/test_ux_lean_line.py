# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from opos.web import page


# These are source checks, not browser execution.
class SheetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.divs = []
        self.children = []
        self.lean_parents = []
        self.unmatched_closes = 0

    def handle_starttag(self, tag, attrs):
        ident = dict(attrs).get("id")
        if self.divs and self.divs[-1] == "sheet":
            self.children.append(ident)
        if ident == "lean-line":
            self.lean_parents.append(tuple(self.divs))
        if tag == "div":
            self.divs.append(ident)

    def handle_endtag(self, tag):
        if tag == "div":
            if self.divs:
                self.divs.pop()
            else:
                self.unmatched_closes += 1


class LeanLineTests(unittest.TestCase):
    def setUp(self):
        self.body = page()
        self.script = (
            Path(__file__).resolve().parents[1]
            / "src/opos/web_static/js-6.js"
        ).read_text(encoding="utf-8")

    def test_lean_line_once_inside_sheet_after_time_left(self):
        parser = SheetParser()
        parser.feed(self.body)
        self.assertEqual(len(re.findall(r'id="lean-line"', self.body)), 1)
        self.assertEqual(len(parser.lean_parents), 1)
        self.assertEqual(parser.lean_parents[0][-1], "sheet")
        index = parser.children.index("time-left")
        self.assertEqual(parser.children[index + 1:index + 3], ["lean-line", "headline"])
        self.assertRegex(
            self.body,
            r'<div id="time-left"></div>\s*'
            r'<p id="lean-line">Memory figures unavailable on this device</p>\s*'
            r'<div id="headline"></div>',
        )
        self.assertEqual(parser.divs, [])
        self.assertEqual(parser.unmatched_closes, 0)

    def test_script_sets_text_with_strict_finite_checks(self):
        self.assertIn(self.script, self.body)
        self.assertIn("const l=s.lean||{};", self.script)
        self.assertIn("document.getElementById('lean-line').textContent=", self.script)
        self.assertIn("(Number.isFinite(l.opos_mb)&&Number.isFinite(l.free_mb))?", self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_script_wording_and_fallback(self):
        self.assertIn(
            r'`OPOS process uses ${l.opos_mb} MB \u00b7 ${l.free_mb} MB available`'
            ":'Memory figures unavailable on this device'",
            self.script,
        )


if __name__ == "__main__":
    unittest.main()
