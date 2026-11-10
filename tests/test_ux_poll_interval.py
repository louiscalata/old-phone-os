# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest import mock

import opos.web as web
from opos.governor import Decision, Mode
from opos.web import poll_ms

MODES = ("NORMAL", "SAVER", "CRITICAL")


class PollIntervalTests(unittest.TestCase):
    def test_poll_normal(self):
        self.assertEqual(poll_ms("NORMAL", None, False), 1000)
        self.assertEqual(poll_ms("NORMAL", 1536, False), 1000)
        self.assertEqual(poll_ms("NORMAL", 8192, False), 1000)
        self.assertIsInstance(poll_ms("NORMAL", None, False), int)

    def test_poll_saver_and_low_ram(self):
        self.assertEqual(poll_ms("SAVER", None, False), 3000)
        self.assertEqual(poll_ms("SAVER", 8192, False), 3000)
        self.assertEqual(poll_ms("NORMAL", 1535, False), 3000)
        self.assertEqual(poll_ms("NORMAL", 0, False), 3000)
        self.assertEqual(poll_ms("NORMAL", 1536, False), 1000)

    def test_poll_critical(self):
        self.assertEqual(poll_ms("CRITICAL", None, False), 5000)
        self.assertEqual(poll_ms("CRITICAL", 8192, False), 5000)
        self.assertEqual(poll_ms("CRITICAL", 1535, False), 5000)
        self.assertEqual(poll_ms("CRITICAL", 0, False), 5000)
        self.assertEqual(poll_ms("NORMAL", None, False, 39.9), 1000)
        self.assertEqual(poll_ms("NORMAL", None, False, 40), 5000)
        self.assertEqual(poll_ms("CRITICAL", None, False, 40), 5000)
        self.assertEqual(poll_ms("NORMAL", None, True, 50), 1000)

    def test_poll_call_overrides(self):
        for mode in MODES:
            for free in (None, 0, 1535, 1536, 8192):
                with self.subTest(mode=mode, free=free):
                    self.assertEqual(poll_ms(mode, free, True), 1000)

    def test_state_exposes_poll_ms(self):
        for visible in (False, True):
            for free in (None, 1535, 1536, 8192):
                with self.subTest(visible=visible, free=free), mock.patch.object(web.assist, "free_mb", return_value=free):
                    phone = web.Phone(visible=visible)
                    for level in (100, 3):
                        phone.set_battery(level)
                        state = phone.state()
                        mode = phone._tick().decision.mode.name
                        self.assertIn("poll_ms", state)
                        self.assertEqual(state["poll_ms"], poll_ms(mode, free, False))
                        self.assertIn("lean", state)
                        if visible:
                            self.assertEqual(state["helper"]["mode"], mode)
                        else:
                            self.assertNotIn("helper", state)
                    phone.call("+1 555 0100")
                    self.assertTrue(phone.state()["in_call"])
                    self.assertEqual(phone.state()["poll_ms"], 1000)
                    phone.hang_up()
                    state = phone.state()
                    self.assertFalse(state["in_call"])
                    mode = phone._tick().decision.mode.name
                    self.assertEqual(state["poll_ms"], poll_ms(mode, free, False))

    def test_state_low_battery_slows_poll(self):
        with mock.patch.object(web.assist, "free_mb", return_value=8192):
            phone = web.Phone(visible=False)
            normal = Decision(Mode.NORMAL, tuple(t.name for t in phone.runtime.tasks), (), "rules")
            phone.set_battery(100)
            with mock.patch.object(phone.runtime.governor, "decide", return_value=normal):
                self.assertEqual(phone._tick().decision.mode.name, "NORMAL")
                self.assertEqual(phone.state()["poll_ms"], 1000)
            phone.set_battery(3)
            self.assertIn(phone.state()["poll_ms"], (3000, 5000))


if __name__ == "__main__":
    unittest.main()
