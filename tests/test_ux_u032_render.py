# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class HelperBoxTest(unittest.TestCase):
    def test_helper_box_no_infinite(self):
        text = web_text()
        self.assertIn("s.helper.hours_left===null?'not known yet':s.helper.hours_left", text)
        self.assertNotIn("s.helper.hours_left===null?'infinite':s.helper.hours_left", text)


if __name__ == "__main__":
    unittest.main()
