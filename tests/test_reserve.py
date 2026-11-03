# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.reserve import apply_reserve, shape_burst, should_shutdown, call_possible, reserve_level, WORN_REASON
from opos.governor import Decision, Mode, Priority, Task
from opos.eol import BURST_A, MIN_CALL_A, brownout, r0_for_health

tasks = (
    Task("calls", Priority.ESSENTIAL, 0.5),
    Task("messages", Priority.ESSENTIAL, 0.2),
    Task("email", Priority.USEFUL, 2),
    Task("app in use", Priority.ESSENTIAL, 1.0),
    Task("alarms", Priority.ESSENTIAL, 0.1),
)

class TestReserve(unittest.TestCase):
    def setUp(self):
        self.decision = Decision(Mode.NORMAL, ("calls", "messages", "email", "app in use", "alarms"), (), "x")

    def test_healthy_no_change(self):
        self.assertIs(apply_reserve(self.decision, 5, 30, 0.9, tasks), self.decision)

    def test_worn_high_level_no_change(self):
        self.assertIs(apply_reserve(self.decision, 90, 30, 0.35, tasks), self.decision)

    def test_worn_mid_critical_email_deferred(self):
        level = reserve_level(30, 0.35) + 10
        new_dec = apply_reserve(self.decision, level, 30, 0.35, tasks)
        self.assertEqual(new_dec.mode, Mode.CRITICAL)
        self.assertEqual(new_dec.reason, WORN_REASON)
        self.assertIn("email", new_dec.deferred)
        self.assertIn("app in use", new_dec.allowed)
        self.assertIn("calls", new_dec.allowed)

    def test_worn_near_reserve_allowed_set(self):
        level = reserve_level(30, 0.35) + 2
        new_dec = apply_reserve(self.decision, level, 30, 0.35, tasks)
        self.assertEqual(set(new_dec.allowed), {"calls", "messages", "alarms"})
        self.assertIn("app in use", new_dec.deferred)

    def test_shape_burst(self):
        self.assertEqual(shape_burst(1.0, 10, 30, 0.9), 1.0)
        worn = shape_burst(1.0, 10, 30, 0.35)
        self.assertLess(worn, BURST_A["call"])
        self.assertFalse(brownout(10, worn, 30, r0_for_health(0.35), 0.0))

    def test_should_shutdown(self):
        self.assertTrue(should_shutdown(0.0, 30, 0.35))
        self.assertFalse(should_shutdown(50, 30, 0.35))
        self.assertFalse(should_shutdown(0.0, 30, 0.9))

    def test_call_possible(self):
        self.assertTrue(call_possible(50, 30, 0.35))

    def test_reserve_cold_vs_warm(self):
        self.assertGreater(reserve_level(0, 0.35), reserve_level(30, 0.35))

    def test_outer_band_retains_keep_regardless_of_priority(self):
        from opos.power import KEEP
        tasks_mixed = (
            Task("calls", Priority.USEFUL, 0.5),
            Task("messages", Priority.USEFUL, 0.2),
            Task("alarms", Priority.USEFUL, 0.1),
            Task("email", Priority.USEFUL, 2.0),
            Task("app in use", Priority.ESSENTIAL, 1.0),
        )
        level = reserve_level(30, 0.35) + 10
        decision = Decision(Mode.NORMAL, ("calls", "messages", "email", "app in use", "alarms"), (), "x")
        new_dec = apply_reserve(decision, level, 30, 0.35, tasks_mixed)
        for name in KEEP:
            self.assertIn(name, new_dec.allowed)
        self.assertEqual(set(new_dec.allowed) & set(KEEP), set(KEEP) & set(decision.allowed))
        self.assertEqual(new_dec.mode, Mode.CRITICAL)
        self.assertEqual(new_dec.reason, WORN_REASON)

    def test_outer_band_defers_optional_non_keep_while_retaining_keep(self):
        from opos.power import KEEP
        tasks_mixed = (
            Task("calls", Priority.USEFUL, 0.5),
            Task("messages", Priority.USEFUL, 0.2),
            Task("alarms", Priority.USEFUL, 0.1),
            Task("email", Priority.USEFUL, 2.0),
            Task("sync", Priority.USEFUL, 1.0),
        )
        level = reserve_level(30, 0.35) + 10
        decision = Decision(Mode.NORMAL, ("calls", "messages", "email", "sync", "alarms"), (), "x")
        new_dec = apply_reserve(decision, level, 30, 0.35, tasks_mixed)
        for name in KEEP:
            if name in decision.allowed:
                self.assertIn(name, new_dec.allowed)
        self.assertIn("email", new_dec.deferred)
        self.assertIn("sync", new_dec.deferred)
        self.assertNotIn("email", new_dec.allowed)
        self.assertNotIn("sync", new_dec.allowed)

    def test_outer_band_partition_and_deterministic_order(self):
        from opos.power import KEEP
        tasks_mixed = (
            Task("calls", Priority.USEFUL, 0.5),
            Task("messages", Priority.USEFUL, 0.2),
            Task("email", Priority.USEFUL, 2),
            Task("app in use", Priority.ESSENTIAL, 1.0),
            Task("alarms", Priority.USEFUL, 0.1),
        )
        decision = Decision(Mode.NORMAL, ("email", "calls", "app in use", "messages", "alarms"), ("prev-deferred",), "x")
        level = reserve_level(30, 0.35) + 10
        new_dec = apply_reserve(decision, level, 30, 0.35, tasks_mixed)
        self.assertEqual(set(new_dec.allowed) | set(new_dec.deferred), set(decision.allowed) | set(decision.deferred))
        self.assertEqual(len(new_dec.allowed), len(set(new_dec.allowed)))
        self.assertEqual(len(new_dec.deferred), len(set(new_dec.deferred)))
        self.assertEqual(set(new_dec.allowed) & set(new_dec.deferred), set())
        expected_allowed = tuple(name for name in decision.allowed if name in KEEP or name == "app in use")
        self.assertEqual(new_dec.allowed, expected_allowed)
        removed = [n for n in decision.allowed if n not in new_dec.allowed]
        self.assertEqual(new_dec.deferred, decision.deferred + tuple(removed))
        self.assertEqual(new_dec.mode, Mode.CRITICAL)
        self.assertEqual(new_dec.reason, WORN_REASON)

if __name__ == "__main__":
    unittest.main()
