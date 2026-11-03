# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.setup import buttons
class TestButtons(unittest.TestCase):
    def test_steps(self):
        self.assertEqual(set(buttons.PROMPTS), {"welcome", "kiosk", "done"})
    def test_two_line_format(self):
        self.assertTrue(all(len(v.split("\n")) == 2 and v.split("\n")[1].startswith("Buttons: ") for v in buttons.PROMPTS.values()))
    def test_kiosk_has_undo(self):
        self.assertIn("UNDO", buttons.prompt("kiosk"))
    def test_unknown_step_raises(self):
        with self.assertRaises(KeyError):
            buttons.prompt("nope")
