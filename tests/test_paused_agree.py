# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from opos.web import Phone


def _screen_paused(state):
    for line in state["screen"]:
        if line.startswith("Paused to save power: "):
            rest = line[len("Paused to save power: "):]
            if not rest.strip():
                return set()
            return {p.strip() for p in rest.split(", ") if p.strip()}
    return set()


def _battery_paused(state):
    return {e["name"] for e in state["battery"]["paused"]}


class TestPausedAgree(unittest.TestCase):
    def test_screen_and_battery_agree(self):
        for level in [100, 60, 35, 20, 8, 3]:
            with self.subTest(level=level):
                phone = Phone()
                phone.set_battery(level)
                s = phone.state()
                self.assertEqual(_screen_paused(s), _battery_paused(s))

    def test_level_8_nonempty_without_calls_messages(self):
        phone = Phone()
        phone.set_battery(8)
        s = phone.state()
        paused = _battery_paused(s)
        self.assertTrue(len(paused) > 0)
        self.assertNotIn("calls", paused)
        self.assertNotIn("messages", paused)

    def test_level_100_and_60_no_essential_and_monotonic(self):
        phone100 = Phone()
        phone100.set_battery(100)
        paused100 = _battery_paused(phone100.state())
        phone60 = Phone()
        phone60.set_battery(60)
        paused60 = _battery_paused(phone60.state())
        self.assertNotIn("calls", paused100)
        self.assertNotIn("messages", paused100)
        self.assertNotIn("calls", paused60)
        self.assertNotIn("messages", paused60)
        self.assertTrue(paused60.issuperset(paused100))


if __name__ == "__main__":
    unittest.main()
