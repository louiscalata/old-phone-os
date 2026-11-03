# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.power import steps_for_goal
from opos.runtime import Task, Priority, Runtime, DEFAULT_TASKS


class TestReviewFixes(unittest.TestCase):
    def test_steps_for_goal_accepts_oneshot_iterator(self):
        expected = steps_for_goal(80, 8, (Task("x", Priority.USEFUL, 4),), (), 1.0)
        got = steps_for_goal(80, 8, (t for t in (Task("x", Priority.USEFUL, 4),)), (), 1.0)
        self.assertEqual(got, expected)

    def test_router_reason_counts_match_final_allowed_deferred(self):
        tasks = tuple(list(DEFAULT_TASKS) + [Task("alarms", Priority.USEFUL, 0.1)])
        rt = Runtime(tasks=tasks)
        tick = rt.step(0, 3, health=1.0, temp_c=30)
        decision = tick.decision
        self.assertTrue(decision.reason.startswith("router"))
        self.assertEqual(decision.reason, f"router: allowed {len(decision.allowed)}, deferred {len(decision.deferred)}")
        for name in ("calls", "messages", "alarms"):
            self.assertIn(name, decision.allowed)
            self.assertNotIn(name, decision.deferred)


    def test_glimmer_pause_keeps_its_attribution_through_runtime(self):
        from opos.advisor import GlimmerAdvisor
        from opos.governor import Governor
        from opos.home import plain_reason
        from opos.router import Router
        jobs = []
        advisor = GlimmerAdvisor(Router(visible=True), ask=lambda *a, **k: '{"defer":["email"]}', spawn=jobs.append, clock=lambda: 0.0)
        rt = Runtime(visible=True)
        rt.governor = Governor(advisor=advisor)
        first = rt.step(0, 100, charging=True, health=1.0, temp_c=25)
        self.assertIn("email", first.decision.allowed)
        for job in jobs:
            job()
        second = rt.step(60, 100, charging=True, health=1.0, temp_c=25)
        self.assertIn("email", second.decision.deferred)
        self.assertTrue(second.decision.reason.startswith("router+glimmer: allowed"))
        self.assertEqual(plain_reason(second.decision.reason), "Muse Glimmer paused extra tasks")


if __name__ == "__main__":
    unittest.main()
