# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos import web


class TestDuoDemoPage(unittest.TestCase):
    def test_demo_page_differs_and_marks_demo(self):
        default = web.page("", "")
        false_page = web.page("", "", demo=False)
        true_page = web.page("", "", demo=True)
        marked = '<div id="demo" data-demo="1">'
        original = '<div id="demo" hidden>'
        self.assertEqual(true_page.count(marked), 1)
        self.assertNotIn(original, true_page)
        self.assertEqual(true_page.replace(marked, original, 1), default)
        self.assertEqual(default, false_page)


if __name__ == "__main__":
    unittest.main()
