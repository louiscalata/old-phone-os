# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.runtime import Runtime


class TestGoalHours(unittest.TestCase):
    def test_runtime_allows_more_with_short_goal(self):
        rt_short = Runtime()
        tick_short = rt_short.step(60, 30, health=0.6, temp_c=30, charging=False, goal_hours=1.0)
        rt_long = Runtime()
        tick_long = rt_long.step(60, 30, health=0.6, temp_c=30, charging=False, goal_hours=40.0)
        allowed_short = tick_short.decision.allowed
        allowed_long = tick_long.decision.allowed
        self.assertGreaterEqual(len(allowed_short), len(allowed_long))
        self.assertIn("calls", allowed_short)
        self.assertIn("messages", allowed_short)
        self.assertIn("calls", allowed_long)
        self.assertIn("messages", allowed_long)

    def test_long_goal_allows_no_more_than_no_goal(self):
        rt_long = Runtime()
        tick_long = rt_long.step(60, 30, health=0.6, temp_c=30, charging=False, goal_hours=40.0)
        rt_none = Runtime()
        tick_none = rt_none.step(60, 30, health=0.6, temp_c=30, charging=False, goal_hours=None)
        allowed_long = tick_long.decision.allowed
        allowed_none = tick_none.decision.allowed
        self.assertGreaterEqual(len(allowed_none), len(allowed_long))
        self.assertIn("calls", allowed_long)
        self.assertIn("messages", allowed_long)
        self.assertIn("calls", allowed_none)
        self.assertIn("messages", allowed_none)


if __name__ == "__main__":
    unittest.main()
