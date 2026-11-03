# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
import unittest
from opos.dashboard import Dashboard
from opos.governor import Mode, Task, Priority
import opos.power


class TestDashboardIds(unittest.TestCase):
    def test_01_quiet_mode_and_ids(self):
        tasks = (
            Task("calls", Priority.ESSENTIAL, 0.5),
            Task("messages", Priority.ESSENTIAL, 0.2),
            Task("AI assistant", Priority.OPTIONAL, 6),
            Task("maps", Priority.USEFUL, 4),
        )
        dash = Dashboard(tasks)
        dash.set_goal("23:00")
        result = dash.view(5, Mode.CRITICAL, False, 0.6, 43200, False, "")
        s = json.dumps(result)
        self.assertIsNone(re.search(r"\b(ai|model|governor|assistant)\b", s, re.I))
        for item in result.get("paused", []):
            self.assertRegex(item["id"], r"^#\d+$")

    def test_02_undo_by_id_and_error(self):
        tasks = (
            Task("calls", Priority.ESSENTIAL, 0.5),
            Task("messages", Priority.ESSENTIAL, 0.2),
            Task("AI assistant", Priority.OPTIONAL, 6),
            Task("maps", Priority.USEFUL, 4),
        )
        dash = Dashboard(tasks)
        dash.set_goal("23:00")
        dash.view(5, Mode.CRITICAL, False, 0.6, 43200, False, "")
        paused = dash.view(5, Mode.CRITICAL, False, 0.6, 43200, False, "")["paused"]
        self.assertTrue(paused)
        item = paused[0]
        idx = int(item["id"][1:])
        real_name = tasks[idx].name
        dash.undo(item["id"])
        self.assertIn(real_name, dash.undone)
        result2 = dash.view(5, Mode.CRITICAL, False, 0.6, 43200, False, "")
        ids = [p["id"] for p in result2.get("paused", [])]
        self.assertNotIn(item["id"], ids)
        before = tuple(dash.undone)
        with self.assertRaises(ValueError):
            dash.undo("nonsense")
        self.assertEqual(tuple(dash.undone), before)

    def test_03_undo_by_name(self):
        tasks = (
            Task("calls", Priority.ESSENTIAL, 0.5),
            Task("messages", Priority.ESSENTIAL, 0.2),
            Task("AI assistant", Priority.OPTIONAL, 6),
            Task("maps", Priority.USEFUL, 4),
        )
        dash = Dashboard(tasks)
        dash.set_goal("23:00")
        dash.view(5, Mode.CRITICAL, False, 0.6, 43200, False, "")
        dash.undo("maps")
        self.assertIn("maps", dash.undone)

    def test_04_charging_goal_met(self):
        tasks = (
            Task("calls", Priority.ESSENTIAL, 0.5),
            Task("messages", Priority.ESSENTIAL, 0.2),
            Task("AI assistant", Priority.OPTIONAL, 6),
            Task("maps", Priority.USEFUL, 4),
        )
        dash = Dashboard(tasks)
        dash.set_goal("23:00")
        result = dash.view(10, Mode.NORMAL, True, 0.6, 43200, True, "")
        plan = opos.power.plan(10, 11.0, tasks, 0.6, True)
        self.assertEqual(result["goal_met"], plan.meets_goal)
        self.assertEqual(result["time_left"], "Charging")

    def test_05_no_goal_is_not_met(self):
        tasks = (
            Task("calls", Priority.ESSENTIAL, 0.5),
            Task("messages", Priority.ESSENTIAL, 0.2),
            Task("AI assistant", Priority.OPTIONAL, 6),
            Task("maps", Priority.USEFUL, 4),
        )
        dash = Dashboard(tasks)
        result = dash.view(50, Mode.NORMAL, False, 1.0, 43200, True, "")
        self.assertFalse(result["goal_met"])
        self.assertEqual(result["goal"], "")
        self.assertIn("why", result)

    def test_06_collision_and_paused_names(self):
        tasks_coll = (
            Task("email", Priority.USEFUL, 2),
            Task("#0", Priority.OPTIONAL, 3),
            Task("calls", Priority.ESSENTIAL, 0.5),
        )
        dash_c = Dashboard(tasks_coll)
        dash_c.set_goal("23:00")
        dash_c.undo("#0")
        self.assertIn("email", dash_c.undone)
        self.assertNotIn("#0", dash_c.undone)

        tasks_orig = (
            Task("calls", Priority.ESSENTIAL, 0.5),
            Task("messages", Priority.ESSENTIAL, 0.2),
            Task("AI assistant", Priority.OPTIONAL, 6),
            Task("maps", Priority.USEFUL, 4),
        )
        dash_o = Dashboard(tasks_orig)
        dash_o.set_goal("23:00")
        res = dash_o.view(5, Mode.CRITICAL, False, 0.6, 43200, False, "")
        self.assertIsInstance(dash_o.paused_names, tuple)
        self.assertIn("AI assistant", dash_o.paused_names)
        displayed = [p["name"] for p in res.get("paused", [])]
        self.assertNotIn("AI assistant", displayed)


if __name__ == "__main__":
    unittest.main()
