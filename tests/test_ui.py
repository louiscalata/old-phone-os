# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import os
import sys
from opos.ui import render, ScreenModem, main
from opos.runtime import Runtime

class TestUI(unittest.TestCase):
    def test_render_joins_screen_lines(self):
        tick = Runtime().step(0, 5)
        out = render(tick)
        expected = "\n".join(tick.screen)
        self.assertEqual(out, expected)

    def test_screen_modem_records_events(self):
        modem = ScreenModem()
        events = getattr(modem, "events", getattr(modem, "log", []))
        self.assertIsInstance(events, list)
        before = len(events)
        modem.dial("123")
        modem.hang_up()
        modem.send_sms("to", "msg")
        self.assertIn(("sms", "to", "msg"), getattr(modem, "events", getattr(modem, "log", [])))
        after = len(getattr(modem, "events", getattr(modem, "log", [])))
        self.assertGreater(after, before)

    @unittest.skipUnless(sys.platform.startswith("linux") and not os.environ.get("DISPLAY"), "a window would open")
    def test_main_without_display_does_not_raise(self):
        from opos.ui import main
        main([])

if __name__ == "__main__":
    unittest.main()
