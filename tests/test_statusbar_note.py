# SPDX-License-Identifier: GPL-3.0-or-later
"""The status bar says what is true (interface plan W1-05): a simulated battery, an unreadable one and a guessed clock are named."""
import unittest
from unittest import mock

from opos import statusbar, web
from opos.hw import live_battery as lb
from opos.web import Phone


def reading(level=60, source="sysfs"):
    return {"level": level, "temp_c": 29.0, "charging": False, "source": source, "health": 1.0, "health_source": "sysfs"}


def live(read):
    return lb.LiveBattery(reader=lambda: read, clock=lambda: 0.0)


class NoteTests(unittest.TestCase):
    def test_the_demo_battery_is_called_simulated(self):
        self.assertEqual(statusbar.note(Phone()), "Simulated battery")

    def test_a_live_battery_that_cannot_be_read_says_so(self):
        self.assertEqual(statusbar.note(Phone(live_battery=live(None))), "Battery not readable")

    def test_a_guessed_clock_says_so(self):
        with mock.patch.object(web, "clock_ok", return_value=False):
            self.assertEqual(statusbar.note(Phone(live_battery=live(reading()))), "Clock not set")

    def test_a_live_battery_and_a_real_clock_have_nothing_to_add(self):
        with mock.patch.object(web, "clock_ok", return_value=True):
            self.assertEqual(statusbar.note(Phone(live_battery=live(reading()))), "")

    def test_the_priority_is_simulated_then_unreadable_then_clock(self):
        with mock.patch.object(web, "clock_ok", return_value=False):
            self.assertEqual(statusbar.note(Phone(live_battery=live(None))), "Battery not readable")
            self.assertEqual(statusbar.note(Phone()), "Simulated battery")

    def test_an_object_without_the_fields_is_not_an_error(self):
        self.assertEqual(statusbar.note(object()), "Simulated battery")


class StateTests(unittest.TestCase):
    def test_state_carries_the_note(self):
        self.assertEqual(Phone().state()["status_note"], "Simulated battery")
        with mock.patch.object(web, "clock_ok", return_value=False):
            s = Phone(live_battery=live(reading())).state()
        self.assertEqual((s["status_note"], s["clock"]), ("Clock not set", "--:--"))

    def test_the_note_is_plain_text_without_the_words_the_quiet_mode_forbids(self):
        for phone in (Phone(), Phone(live_battery=live(None))):
            note = phone.state()["status_note"].lower()
            for word in ("model", "alarm", "opos", "glimmer", " ai"):
                self.assertNotIn(word, " " + note)


if __name__ == "__main__":
    unittest.main()
