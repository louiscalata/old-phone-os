# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
from opos.setup.wizard_page import page


class TestWizardTouch(unittest.TestCase):
    def setUp(self):
        self.html = page("a" * 32, "n1")
        s = self.html.find("<style")
        s = self.html.find(">", s) + 1
        e = self.html.find("</style>")
        self.css = self.html[s:e]

    def block(self, sel):
        i = self.css.find(sel)
        if i < 0:
            return None
        j = self.css.find("{", i)
        if j < 0:
            return None
        k = self.css.find("}", j)
        if k < 0:
            return None
        return self.css[j + 1:k]

    def test_option_controls_are_24px(self):
        b = self.block("input[type=checkbox],input[type=radio]")
        self.assertIsNotNone(b)
        self.assertIn("width:24px", b)
        self.assertIn("height:24px", b)
        self.assertNotIn("width:auto", b)

    def test_panel_label_rows_are_at_least_44px(self):
        b = self.block(".panel label")
        self.assertIsNotNone(b)
        self.assertIn("display:flex", b)
        m = re.search(r"min-height:\s*(\d+)px", b)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 44)
        self.assertIn("gap:", b)

    def test_step_change_focuses_heading(self):
        s = self.html.find("function show(i){")
        self.assertNotEqual(s, -1)
        n1 = self.html.find("\nfunction ", s + 1)
        n2 = self.html.find("\nasync function ", s + 1)
        cuts = [x for x in (n1, n2) if x >= 0]
        body = self.html[s:min(cuts)] if cuts else self.html[s:]
        self.assertIn("if(i===6)buildSummary()", body)
        self.assertIn('querySelector("h2")', body)
        self.assertIn("tabIndex=-1", body)
        self.assertIn(".focus()", body)
        self.assertLess(body.find("buildSummary()"), body.find(".focus()"))

    def test_load_does_not_move_focus(self):
        s = self.html.find("async function load(){")
        e = self.html.find("async function changeProfile(")
        self.assertNotEqual(s, -1)
        self.assertNotEqual(e, -1)
        body = self.html[s:e]
        self.assertNotIn("show(", body)
        self.assertNotIn(".focus()", body)


if __name__ == "__main__":
    unittest.main()
