# SPDX-License-Identifier: GPL-3.0-or-later;
import unittest
import opos.web

class TestHelperNewlines(unittest.TestCase):
    def test_helper_box_uses_js_newline(self):
        html = opos.web.page()
        self.assertIn("'Helper\\nMode: '", html)
        self.assertIn("'\\nHelper set to: '", html)
        self.assertNotIn("\\\\n", html)
