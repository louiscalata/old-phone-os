# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class DemoPanelTest(unittest.TestCase):
    def test_temp_label_has_unit(self):
        src = web_text()
        self.assertIn('<label>Temp (°C) <input id="temp"', src)


if __name__ == "__main__":
    unittest.main()
