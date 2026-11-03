# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
from html.parser import HTMLParser

from opos.web import page

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.found = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        el = {"tag": tag, "attrs": attrs, "ancestors": [i for _, i in self.stack if i]}
        self.found.append(el)
        if tag not in VOID:
            self.stack.append((tag, attrs.get("id")))

    def handle_startendtag(self, tag, attrs):
        attrs = dict(attrs)
        self.found.append({"tag": tag, "attrs": attrs, "ancestors": [i for _, i in self.stack if i]})

    def handle_endtag(self, tag):
        for n in range(len(self.stack) - 1, -1, -1):
            if self.stack[n][0] == tag:
                del self.stack[n:]
                break


class SheetMarkupTests(unittest.TestCase):
    def setUp(self):
        self.html = page()
        tree = _Tree()
        tree.feed(self.html)
        tree.close()
        self.found = tree.found

    def by_id(self, el_id):
        return [e for e in self.found if e["attrs"].get("id") == el_id]

    def test_sheet_wrapper_present_once(self):
        sheets = self.by_id("sheet")
        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0]["attrs"].get("role"), "dialog")
        self.assertEqual(sheets[0]["attrs"].get("aria-modal"), "true")

    def test_sheet_children_inside_sheet(self):
        for el_id in ("sheet-handle", "sheet-title", "time-left"):
            els = self.by_id(el_id)
            self.assertEqual(len(els), 1, el_id)
            self.assertIn("sheet", els[0]["ancestors"], el_id)

    def test_scrim_and_meter_outside_sheet(self):
        for el_id in ("scrim", "meter"):
            els = self.by_id(el_id)
            self.assertEqual(len(els), 1, el_id)
            self.assertNotIn("sheet", els[0]["ancestors"], el_id)

    def test_divs_balanced(self):
        self.assertEqual(len(re.findall(r"<div\b", self.html)), len(re.findall(r"</div>", self.html)))


if __name__ == "__main__":
    unittest.main()
