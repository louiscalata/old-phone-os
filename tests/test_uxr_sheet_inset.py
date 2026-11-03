# SPDX-License-Identifier: GPL-3.0-or-later
import os
import unittest

import opos.web


class TestSheetLonghand(unittest.TestCase):
    def setUp(self):
        self.page = opos.web.page()
        static = os.path.join(os.path.dirname(opos.web.__file__), "web_static")
        with open(os.path.join(static, "css-2.css"), encoding="utf-8") as f:
            self.css = f.read()

    def _props(self, selector):
        start = self.page.find(selector + "{")
        end = self.page.find("}", start)
        body = self.page[start + len(selector) + 1:end]
        props = {}
        for decl in body.split(";"):
            if ":" not in decl:
                continue
            name, value = decl.split(":", 1)
            props[name.strip()] = value.strip()
        return props

    def test_no_inset(self):
        self.assertNotIn("inset:", self.page)
        self.assertNotIn("inset:", self.css)

    def test_scrim_covers_viewport(self):
        props = self._props("#scrim")
        self.assertEqual(props.get("position"), "fixed")
        self.assertEqual(props.get("top"), "0")
        self.assertEqual(props.get("right"), "0")
        self.assertEqual(props.get("bottom"), "0")
        self.assertEqual(props.get("left"), "0")
        self.assertNotIn("inset", props)

    def test_sheet_pinned_top_full_width(self):
        props = self._props("#sheet")
        self.assertEqual(props.get("position"), "fixed")
        self.assertEqual(props.get("top"), "0")
        self.assertEqual(props.get("right"), "0")
        self.assertEqual(props.get("left"), "0")
        self.assertNotIn("bottom", props)
        self.assertNotIn("inset", props)
        self.assertEqual(props.get("height"), "88dvh")
        start = self.page.find("#sheet{")
        rule = self.page[start:self.page.find("}", start) + 1]
        self.assertLess(rule.find("height:88vh"), rule.find("height:88dvh"))
