# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class TestDemoPanel(unittest.TestCase):
    def test_controls_id_unique(self):
        text = web_text()
        self.assertEqual(text.count('id="controls"'), 1)
        self.assertEqual(text.count('id="demo-controls"'), 1)
        self.assertIn('<div id="demo-controls"><button id="simbtn">', text)


if __name__ == "__main__":
    unittest.main()
