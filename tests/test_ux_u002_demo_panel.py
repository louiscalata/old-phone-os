# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest

from tests.web_source import web_text


class TestDemoPanel(unittest.TestCase):
    def test_demo_panel_above_sheet(self):
        src = web_text()
        rule = "@media (min-width:1000px){#demo{position:fixed;top:12px;left:calc(50% + 232px);width:260px;margin:0;z-index:80}}"
        self.assertEqual(src.count(rule), 1)
        self.assertNotIn("(min-width:900px)", src)
        m = re.search(r"#sheet\{([^}]*)\}", src)
        self.assertIsNotNone(m)
        z = re.search(r"z-index\s*:\s*(\d+)", m.group(1))
        self.assertIsNotNone(z)
        self.assertLess(int(z.group(1)), 80)


if __name__ == "__main__":
    unittest.main()
