# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.demo import run, LogModem
from opos.home import is_quiet

class TestDemo(unittest.TestCase):
    def test_low_battery_default(self):
        lines = run(level=5.0, health=0.6, visible=False)
        self.assertIn("Called +15550100", lines)
        self.assertIn("Sent: On my way", lines)
        self.assertTrue(any("Calls" in l for l in lines))
        self.assertTrue(any("Messages" in l for l in lines))
        self.assertTrue(any(l.startswith("Paused to save power") for l in lines))
        self.assertTrue(is_quiet(lines))
    def test_visible_helper(self):
        lines = run(level=5.0, health=0.6, visible=True)
        self.assertTrue(any(l.startswith("Helper:") for l in lines))
    def test_high_battery_no_pause(self):
        lines = run(level=90, health=1.0, visible=False)
        self.assertFalse(any(l.startswith("Paused to save power") for l in lines))
    def test_log_modem_dial(self):
        modem = LogModem()
        modem.dial("x")
        self.assertIn(("dial","x"), modem.events)

if __name__ == "__main__":
    unittest.main()
