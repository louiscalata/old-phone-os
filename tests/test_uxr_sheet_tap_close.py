# SPDX-License-Identifier: GPL-3.0-or-later
import os
import re
import unittest

import opos.web


def _grip_click(src):
    m = re.search(r"\[handleEl,gripEl\]\.forEach\(el=>el\.addEventListener\('click',.*?\)\);", src, re.DOTALL)
    return m.group(0) if m else None


class TestSheetTapClose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = opos.web.page()
        base = os.path.dirname(opos.web.__file__)
        with open(os.path.join(base, "web_static", "js-3.js"), encoding="utf-8") as f:
            cls.js3 = f.read()

    def test_handle_and_grip_get_click(self):
        stmt = _grip_click(self.html)
        self.assertIsNotNone(stmt)
        self.assertIn("sheetOpen", stmt)
        self.assertIn("closeSheet()", stmt)

    def test_click_does_not_open(self):
        stmt = _grip_click(self.html)
        self.assertIsNotNone(stmt)
        self.assertNotIn("openSheet", stmt)

    def test_after_pointerdown_wiring(self):
        wire = "[handleEl,titleEl,timeEl,gripEl].forEach(el=>el.addEventListener('pointerdown',sheetDown));"
        self.assertIn(wire, self.html)
        stmt = _grip_click(self.html)
        self.assertIsNotNone(stmt)
        self.assertGreater(self.html.index(stmt), self.html.index(wire))

    def test_title_and_time_not_tap_close(self):
        self.assertNotIn("titleEl.addEventListener('click'", self.html)
        self.assertNotIn('titleEl.addEventListener("click"', self.html)
        self.assertNotIn("timeEl.addEventListener('click'", self.html)
        self.assertNotIn('timeEl.addEventListener("click"', self.html)
        for m in re.finditer(r"\[[^\]]+\]\.forEach\(.*?addEventListener\(['\"]click['\"]", self.html):
            self.assertNotIn("titleEl", m.group(0))
            self.assertNotIn("timeEl", m.group(0))

    def test_lives_in_js3(self):
        stmt = _grip_click(self.html)
        self.assertIsNotNone(stmt)
        self.assertIn(stmt, self.js3)
