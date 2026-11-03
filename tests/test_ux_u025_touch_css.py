# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest

from tests.web_source import web_text


class SheetGripTest(unittest.TestCase):
    def test_sheet_grip_44px(self):
        text = web_text()
        match = re.search(r'#sheet-grip\{[^}]*\}', text)
        self.assertIsNotNone(match)
        rule = match.group(0)
        self.assertIn("height:44px", rule)
        self.assertNotIn("height:36px", rule)
