# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import json
import re
import opos.dashboard
import opos.runtime
import opos.governor
class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.tasks = opos.runtime.DEFAULT_TASKS
        self.dash = opos.dashboard.Dashboard(self.tasks, history_len=5)
    def test_meter_width_clamp(self):
        v = self.dash.view(120, opos.governor.Mode.NORMAL, False, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["width"], 100)
        v = self.dash.view(-5, opos.governor.Mode.NORMAL, False, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["width"], 0)
    def test_meter_tone(self):
        v = self.dash.view(19, opos.governor.Mode.NORMAL, False, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["tone"], "low")
        v = self.dash.view(20, opos.governor.Mode.NORMAL, False, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["tone"], "normal")
        v = self.dash.view(50, opos.governor.Mode.CRITICAL, False, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["tone"], "normal")
        v = self.dash.view(100, opos.governor.Mode.NORMAL, False, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["tone"], "full")
        v = self.dash.view(50, opos.governor.Mode.NORMAL, True, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["tone"], "charging")
        v = self.dash.view(100, opos.governor.Mode.NORMAL, True, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["tone"], "full")
        v = self.dash.view(10, opos.governor.Mode.NORMAL, True, 1.0, 0, True, "")
        self.assertEqual(v["meter"]["tone"], "charging")
    def test_set_goal(self):
        d = self.dash
        d.set_goal("23:00")
        self.assertEqual(d.hours_to_goal(20*3600), 3.0)
        self.assertEqual(d.hours_to_goal(23.5*3600), 23.5)
        d.set_goal("")
        self.assertIsNone(d.goal)
        with self.assertRaises(ValueError):
            d.set_goal("25:00")
        with self.assertRaises(ValueError):
            d.set_goal("abc")
    def test_view_saver_paused(self):
        d = self.dash
        d.set_goal("23:00")
        v = d.view(30, opos.governor.Mode.SAVER, False, 0.6, 12*3600, True, "")
        self.assertTrue(len(v["paused"]) > 0)
        self.assertTrue(v["headline"].startswith("Saving power for calls, messages"))
        names = [p["name"] for p in v["paused"]]
        self.assertNotIn("calls", names)
        self.assertNotIn("messages", names)
    def test_undo(self):
        d = self.dash
        d.set_goal("23:00")
        v1 = d.view(30, opos.governor.Mode.SAVER, False, 0.6, 12*3600, True, "")
        names_before = [p["name"] for p in v1["paused"]]
        self.assertIn("photos backup", names_before)
        d.undo("photos backup")
        v2 = d.view(30, opos.governor.Mode.SAVER, False, 0.6, 12*3600, True, "")
        names_after = [p["name"] for p in v2["paused"]]
        self.assertNotIn("photos backup", names_after)
        self.assertTrue(any("photos backup turned back on" in e for e in d.events))
        with self.assertRaises(ValueError):
            d.undo("nope")
    def test_charging_view(self):
        d = self.dash
        v = d.view(50, opos.governor.Mode.NORMAL, True, 1.0, 0, True, "")
        self.assertEqual(v["time_left"], "Charging")
        self.assertEqual(v["headline"], "Charging: everything back on")
        self.assertEqual(v["paused"], [])
    def test_quiet_view(self):
        d = self.dash
        v_hidden = d.view(30, opos.governor.Mode.SAVER, False, 0.6, 12*3600, False, "reason")
        self.assertNotIn("why", v_hidden)
        s = json.dumps(v_hidden)
        for word in ["AI","model","Governor","assistant"]:
            self.assertIsNone(re.search(r'\b'+re.escape(word)+r'\b', s, re.I))
        v_visible = d.view(30, opos.governor.Mode.SAVER, False, 0.6, 12*3600, True, "reason")
        self.assertIn("why", v_visible)
    def test_record_history_trim(self):
        d = opos.dashboard.Dashboard(self.tasks, history_len=3)
        for i in range(5):
            d.record(i)
        self.assertEqual(len(d.history), 3)
    def test_features_shares_sum(self):
        d = self.dash
        v = d.view(30, opos.governor.Mode.SAVER, False, 0.6, 12*3600, True, "")
        total = sum(f["share"] for f in v["features"])
        self.assertAlmostEqual(total, 100, delta=2)
if __name__ == "__main__":
    unittest.main()
