# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
import opos.dashboard
import opos.governor
import opos.runtime

class TestGoalNote(unittest.TestCase):
    def setUp(self):
        self.d = opos.dashboard.Dashboard(opos.runtime.DEFAULT_TASKS)
        self.d.set_goal("")

    def test_no_goal(self):
        v = self.d.view(3, opos.governor.Mode.NORMAL, False, 1.0, 12*3600, True, "")
        self.assertEqual(v["goal_note"], "")

    def test_goal_unreachable_note_and_met_false(self):
        self.d.set_goal("23:00")
        v = self.d.view(3, opos.governor.Mode.NORMAL, False, 1.0, 12*3600, True, "")
        note = v["goal_note"]
        self.assertRegex(note, r"^Won't last until 11 PM, calls and messages until ~\d{1,2}:\d{2} (AM|PM)$")
        self.assertFalse(v["goal_met"])

    def test_goal_reachable(self):
        self.d.set_goal("23:00")
        v = self.d.view(100, opos.governor.Mode.NORMAL, False, 1.0, 22*3600, True, "")
        self.assertEqual(v["goal_note"], "")
        self.assertTrue(v["goal_met"])

    def test_charging_suppresses_note(self):
        self.d.set_goal("23:00")
        v = self.d.view(3, opos.governor.Mode.NORMAL, True, 1.0, 12*3600, True, "")
        self.assertEqual(v["goal_note"], "")

    def test_none_clock_suppresses_note(self):
        self.d.set_goal("23:00")
        v = self.d.view(3, opos.governor.Mode.NORMAL, False, 1.0, None, True, "")
        self.assertEqual(v["goal_note"], "")

    def test_visible_false_still_shows_note_no_banned_words(self):
        self.d.set_goal("23:00")
        v = self.d.view(3, opos.governor.Mode.NORMAL, False, 1.0, 12*3600, False, "")
        note = v["goal_note"]
        self.assertRegex(note, r"^Won't last until 11 PM, calls and messages until ~\d{1,2}:\d{2} (AM|PM)$")
        lowered = note.lower()
        for w in ("ai", "model", "governor", "assistant"):
            self.assertNotIn(w, lowered)

    def test_goal_06_30_spoken(self):
        self.d.set_goal("06:30")
        v = self.d.view(3, opos.governor.Mode.NORMAL, False, 1.0, 12*3600, True, "")
        note = v["goal_note"]
        self.assertTrue(note.startswith("Won't last until 6:30 AM, calls and messages until ~"))

    def test_end_time_plausible(self):
        self.d.set_goal("23:00")
        v = self.d.view(3, opos.governor.Mode.NORMAL, False, 1.0, 12*3600, True, "")
        note = v["goal_note"]
        m = re.search(r"~(\d{1,2}):(\d{2}) (AM|PM)", note)
        self.assertIsNotNone(m)
        h = int(m.group(1)); mi = int(m.group(2)); ap = m.group(3)
        mins = h % 12 * 60 + mi
        if ap == "PM":
            mins += 12 * 60
        self.assertGreater(mins, 720)
        self.assertLess(mins, 1440)
