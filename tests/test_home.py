# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.home import home_screen, is_quiet, BANNED
from opos.governor import Mode, Decision

class TestHome(unittest.TestCase):
    def test_home_screen_basic(self):
        d = Decision(Mode.NORMAL, ('calls','messages','email'), (), 'ok')
        lines = home_screen(80, d)
        self.assertEqual(lines[0], "Calls")
        self.assertEqual(lines[1], "Messages")
        self.assertIn("Email", lines)
        self.assertFalse(any("Battery" in l for l in lines))
        self.assertFalse(any("%" in l for l in lines))
    def test_home_screen_deferred(self):
        d = Decision(Mode.SAVER, ('calls','messages'), ('browser','games'), 'low')
        lines = home_screen(15, d)
        self.assertTrue(any(l.startswith("Paused to save power:") for l in lines))
    def test_home_screen_visible(self):
        d = Decision(Mode.NORMAL, ('calls','messages'), (), 'help me')
        lines = home_screen(50, d, visible=True)
        self.assertEqual(lines[-1], "Helper: help me")
    def test_is_quiet_pass(self):
        self.assertTrue(is_quiet(["Calls", "Messages", "Email"]))
        self.assertTrue(is_quiet(["maintain"]))
    def test_is_quiet_fail(self):
        self.assertFalse(is_quiet(["Helper: model"]))
        self.assertFalse(is_quiet(["AI"]))
        self.assertFalse(is_quiet(["Governor"]))
        self.assertFalse(is_quiet(["assistant"]))
    def test_quiet_output_with_model_reason(self):
        d = Decision(Mode.NORMAL, ('calls','messages'), (), 'model')
        lines = home_screen(50, d, visible=False)
        self.assertTrue(is_quiet(lines))
    def test_visible_output_with_model_reason_fails_quiet(self):
        d = Decision(Mode.NORMAL, ('calls','messages'), (), 'model')
        lines = home_screen(50, d, visible=True)
        self.assertFalse(is_quiet(lines))
    def test_home_screen_plain_reason(self):
        cases = {
            'router: allowed 2, deferred 3': "Helper: decided by the battery rules",
            'rules': "Helper: decided by the battery rules",
            'router+glimmer': "Helper: Muse Glimmer paused extra tasks",
        }
        for reason, want in cases.items():
            d = Decision(Mode.SAVER, ('calls','messages'), ('maps',), reason)
            self.assertEqual(home_screen(60, d, visible=True)[-1], want)
            self.assertNotIn("Helper", " ".join(home_screen(60, d)))

if __name__ == "__main__":
    unittest.main()
