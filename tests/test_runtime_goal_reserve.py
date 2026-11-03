# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest
from unittest.mock import patch
from opos.battery import essentials_reserve
from opos.governor import Decision, Mode, Priority, Task
from opos.reserve import WORN_REASON, reserve_level
from opos.runtime import Runtime

TASKS = (
    Task("calls", Priority.ESSENTIAL, 0.5),
    Task("messages", Priority.ESSENTIAL, 0.2),
    Task("alarms", Priority.ESSENTIAL, 0.1),
    Task("email", Priority.USEFUL, 2.0),
    Task("maps", Priority.USEFUL, 4.0),
    Task("photos backup", Priority.OPTIONAL, 6.0),
)

HEALTH = 0.35
TEMP = 30.0


def worn_level():
    return reserve_level(TEMP, HEALTH) + 2


def allow_all(rt):
    rt.governor.decide = lambda state, tasks: Decision(
        Mode.NORMAL, tuple(t.name for t in tasks), (), "router: allow all")


class TestRuntimeGoalReserve(unittest.TestCase):
    def test_worn_near_reserve_no_goal(self):
        rt = Runtime(tasks=TASKS)
        tick = rt.step(0.0, worn_level(), health=HEALTH, temp_c=TEMP)
        self.assertEqual(tick.decision.mode, Mode.CRITICAL)
        self.assertEqual(tick.decision.reason, WORN_REASON)
        for name in ("calls", "messages", "alarms"):
            self.assertIn(name, tick.decision.allowed)
        for name in ("email", "maps", "photos backup"):
            self.assertIn(name, tick.decision.deferred)
            self.assertNotIn(name, tick.decision.allowed)
        self.assertEqual(tick.decision.deferred, ("email", "maps", "photos backup"))

    def test_worn_near_reserve_with_goal(self):
        rt = Runtime(tasks=TASKS)
        tick = rt.step(0.0, worn_level(), health=HEALTH, temp_c=TEMP, goal_hours=8.0)
        self.assertEqual(tick.decision.mode, Mode.CRITICAL)
        self.assertEqual(tick.decision.reason, WORN_REASON)
        for name in ("calls", "messages", "alarms"):
            self.assertIn(name, tick.decision.allowed)
        for name in ("email", "maps", "photos backup"):
            self.assertIn(name, tick.decision.deferred)
        self.assertEqual(tick.decision.deferred, ("email", "maps", "photos backup"))
        self.assertIsInstance(tick.steps, tuple)
        self.assertTrue(tick.steps)
        blob = " ".join(tick.steps)
        for step in (
            "dim screen",
            "short screen timeout",
            "batch sync",
            "slow background polling",
        ):
            self.assertIn(step, blob)
        for name in ("email", "maps", "photos backup"):
            self.assertNotIn(name, blob)

    def test_worn_near_reserve_allow_all_advisor(self):
        rt = Runtime(tasks=TASKS)
        allow_all(rt)
        tick = rt.step(0.0, worn_level(), health=HEALTH, temp_c=TEMP, goal_hours=8.0)
        self.assertEqual(tick.decision.mode, Mode.CRITICAL)
        self.assertEqual(tick.decision.reason, WORN_REASON)
        self.assertEqual(set(tick.decision.allowed), {"calls", "messages", "alarms"})
        for name in ("email", "maps", "photos backup"):
            self.assertIn(name, tick.decision.deferred)
        self.assertEqual(tick.decision.deferred, ("email", "maps", "photos backup"))

    def test_healthy_high_charge_control_unrestricted(self):
        rt = Runtime(tasks=TASKS)
        allow_all(rt)
        tick = rt.step(0.0, 90.0, health=0.9, temp_c=TEMP, goal_hours=8.0)
        self.assertEqual(tick.held, ())  # No reserve or other hard floor.
        # The goal plan's soft pauses are all that remains (the final decision owns them now).
        self.assertEqual(tick.decision.deferred, ("maps", "photos backup"))
        self.assertEqual(set(dict(tick.causes).values()), {"goal"})
        rt = Runtime(tasks=TASKS)
        allow_all(rt)
        tick = rt.step(0.0, 90.0, health=0.9, temp_c=TEMP)  # Without a goal nothing is paused.
        self.assertEqual(set(tick.decision.allowed), {t.name for t in TASKS})
        self.assertEqual(tick.decision.deferred, ())

    def test_worn_near_reserve_bytes_preserved(self):
        rt = Runtime(tasks=TASKS)
        tick = rt.step(0.0, worn_level(), health=HEALTH, temp_c=TEMP)
        self.assertEqual(
            b",".join(n.encode() for n in tick.decision.allowed),
            b"calls,messages,alarms")
        self.assertEqual(tick.decision.deferred, ("email", "maps", "photos backup"))

    def test_goal_hard_floor_without_drain(self):
        floor = essentials_reserve(None)
        for rate in (None, 0.0):
            for level in (0.5, floor):
                with self.subTest(rate=rate, level=level):
                    rt = Runtime(tasks=TASKS)
                    allow_all(rt)
                    rt.estimator.rate = lambda r=rate: r
                    with patch("opos.runtime.rule_decide", return_value=Decision(Mode.NORMAL, tuple(t.name for t in TASKS), (), "router: allow all")):
                        tick = rt.step(0.0, level, charging=False, health=1.0, temp_c=25.0, goal_hours=8.0)
                    self.assertEqual(tick.decision.allowed, ("calls", "messages", "alarms"))
                    self.assertEqual(tick.decision.deferred, ("email", "maps", "photos backup"))

    def test_goal_waives_only_rate_reserve(self):
        cases = ((8.0, 25.0, False), (None, 25.0, False), (8.0, 45.0, False), (8.0, 25.0, True))
        for goal_hours, temp_c, charging in cases:
            with self.subTest(goal_hours=goal_hours, temp_c=temp_c, charging=charging):
                rt = Runtime(tasks=TASKS)
                allow_all(rt)
                rt.estimator.rate = lambda: 100.0
                with patch("opos.runtime.rule_decide", return_value=Decision(Mode.NORMAL, tuple(t.name for t in TASKS), (), "router: allow all")):
                    tick = rt.step(0.0, 90.0, charging=charging, health=1.0, temp_c=temp_c, goal_hours=goal_hours)
                if goal_hours == 8.0 and temp_c == 25.0 and charging is False:
                    # The rate reserve is waived; only the goal plan's soft pauses remain.
                    self.assertEqual(tick.held, ())
                    self.assertEqual(tick.decision.allowed, ("calls", "messages", "alarms", "email"))
                    self.assertEqual(set(dict(tick.causes).values()), {"goal"})
                else:
                    self.assertEqual(dict(tick.causes), dict.fromkeys(("email", "maps", "photos backup"), "reserve"))
                    self.assertEqual(tick.decision.allowed, ("calls", "messages", "alarms"))
                    self.assertEqual(tick.decision.deferred, ("email", "maps", "photos backup"))


if __name__ == "__main__":
    unittest.main()
