# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest

from opos.web import page


class TestInputLabels(unittest.TestCase):
    def test_inputs_labelled(self):
        rendered = page()
        number_tag = re.search(r"<input[^>]*id=\"number\"[^>]*>", rendered)
        msg_tag = re.search(r"<input[^>]*id=\"msg\"[^>]*>", rendered)
        self.assertIsNotNone(number_tag)
        self.assertIsNotNone(msg_tag)
        number_tag = number_tag.group(0)
        msg_tag = msg_tag.group(0)
        self.assertIn("placeholder=\"Phone number (or tap a contact)\"", number_tag)
        self.assertIn("aria-label=\"Phone number\"", number_tag)
        self.assertIn("inputmode=\"tel\"", number_tag)
        self.assertIn("type=\"text\"", number_tag)
        self.assertIn("placeholder=\"Text message (for Send)\"", msg_tag)
        self.assertIn("aria-label=\"Text message\"", msg_tag)
        self.assertIn("type=\"text\"", msg_tag)
        self.assertNotIn("placeholder=\"number\"", rendered)
        self.assertNotIn("placeholder=\"message\"", rendered)


if __name__ == "__main__":
    unittest.main()
