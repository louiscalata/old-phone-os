# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.router import Router
from opos.governor import PowerState, Task, Priority, Mode
from opos import muse
from opos.advisor import GlimmerAdvisor
class TestAdvisor(unittest.TestCase):
    def setUp(self):
        self.base = Router(visible=True)
        self.essential = Task("call", Priority.ESSENTIAL, 1.0)
        self.useful = Task("sync", Priority.USEFUL, 1.0)
        self.optional = Task("video", Priority.OPTIONAL, 1.0)
        self.state_high = PowerState(level=90, charging=False, health=1.0, temp_c=20, goal_hours=None)
        self.state_low = PowerState(level=50, charging=False, health=1.0, temp_c=20, goal_hours=None)
        self.tasks_all = [self.essential, self.useful, self.optional]
        self.tasks_eu = [self.essential, self.useful]
    def test_cached_defer_moves_useful(self):
        adv = GlimmerAdvisor(self.base, route="r", ask=lambda *a, **k: '{"defer":[]}', spawn=lambda fn: None, clock=lambda: 0)
        d = self.base(self.state_high, self.tasks_all)
        m = {t.name: t for t in self.tasks_all}
        noness = [n for n in d.allowed if m[n].priority != Priority.ESSENTIAL]
        key = (round(self.state_high.level / 10), bool(self.state_high.charging), round(self.state_high.health, 1), round(self.state_high.temp_c / 5), self.state_high.goal_hours, tuple(noness))
        adv.cache[key] = (self.useful.name,)
        d2 = adv(self.state_high, self.tasks_all)
        self.assertNotIn(self.useful.name, d2.allowed)
        self.assertIn(self.useful.name, d2.deferred)
        self.assertEqual(d2.reason, "router+glimmer")
        self.assertEqual(d2.mode, Mode.SAVER)
        self.assertEqual(d2.allowed, (self.essential.name, self.optional.name))
    def test_essential_and_unknown_ignored(self):
        def fake_ask(route, prompt, timeout=30, max_tokens=60):
            return '{"defer": ["call", "ghost"]}'
        spawns = []
        adv = GlimmerAdvisor(self.base, route="r", ask=fake_ask, spawn=lambda fn: spawns.append(fn), clock=lambda: 0)
        d0 = self.base(self.state_high, self.tasks_all)
        d1 = adv(self.state_high, self.tasks_all)
        self.assertEqual(d1, d0)
        self.assertEqual(len(spawns), 1)
        spawns[0]()
        d2 = adv(self.state_high, self.tasks_all)
        self.assertEqual(d2.allowed, d0.allowed)
        self.assertEqual(d2.deferred, d0.deferred)
        self.assertIn(self.essential.name, d2.allowed)
    def test_spawn_once_and_apply(self):
        called = []
        def fake_ask(route, prompt, timeout=30, max_tokens=60):
            called.append((route, prompt, timeout, max_tokens))
            return '{"defer": ["sync"]}'
        spawns = []
        adv = GlimmerAdvisor(self.base, route="r", ask=fake_ask, spawn=lambda fn: spawns.append(fn), clock=lambda: 0)
        d0 = self.base(self.state_high, self.tasks_all)
        d1 = adv(self.state_high, self.tasks_all)
        self.assertEqual(d1, d0)
        self.assertEqual(len(spawns), 1)
        self.assertEqual(len(called), 0)
        spawns[0]()
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0][0], "r")
        self.assertEqual(called[0][2], 120)
        self.assertEqual(called[0][3], 60)
        self.assertIn("90", called[0][1])
        d2 = adv(self.state_high, self.tasks_all)
        self.assertNotIn("sync", d2.allowed)
        self.assertIn("sync", d2.deferred)
        self.assertEqual(d2.reason, "router+glimmer")
        adv(self.state_high, self.tasks_all)
        self.assertEqual(len(spawns), 1)
    def test_muse_error_sets_backoff(self):
        spawns = []
        clock_val = [0.0]
        def clock():
            return clock_val[0]
        def fake_ask(route, prompt, timeout=30, max_tokens=60):
            raise muse.MuseError("fail")
        adv = GlimmerAdvisor(self.base, route="r", ask=fake_ask, spawn=lambda fn: spawns.append(fn), clock=clock, backoff=10.0)
        state_a = PowerState(level=90, charging=False, health=1.0, temp_c=20, goal_hours=None)
        state_b = PowerState(level=50, charging=False, health=1.0, temp_c=20, goal_hours=None)
        adv(state_a, self.tasks_eu)
        self.assertEqual(len(spawns), 1)
        spawns[0]()
        self.assertEqual(adv.down_until, 10.0)
        self.assertEqual(len(adv.pending), 0)
        adv(state_b, self.tasks_eu)
        self.assertEqual(len(spawns), 1)
        clock_val[0] = 11.0
        adv(state_b, self.tasks_eu)
        self.assertEqual(len(spawns), 2)
    def test_never_allows_deferred(self):
        adv = GlimmerAdvisor(self.base, route="r", ask=lambda *a, **k: '{"defer":[]}', spawn=lambda fn: None, clock=lambda: 0)
        d0 = self.base(self.state_low, self.tasks_all)
        self.assertIn(self.optional.name, d0.deferred)
        m = {t.name: t for t in self.tasks_all}
        noness = [n for n in d0.allowed if m[n].priority != Priority.ESSENTIAL]
        key = (round(self.state_low.level / 10), bool(self.state_low.charging), round(self.state_low.health, 1), round(self.state_low.temp_c / 5), self.state_low.goal_hours, tuple(noness))
        adv.cache[key] = (self.useful.name,)
        d2 = adv(self.state_low, self.tasks_all)
        self.assertNotIn(self.optional.name, d2.allowed)
        self.assertIn(self.optional.name, d2.deferred)
        self.assertNotIn(self.useful.name, d2.allowed)
        self.assertEqual(d2.mode, Mode.CRITICAL)
    def test_visible_passthrough(self):
        base = Router(visible=False)
        adv = GlimmerAdvisor(base, route="r", ask=lambda *a, **k: '{"defer":[]}', spawn=lambda fn: None, clock=lambda: 0)
        self.assertFalse(adv.visible)
        adv.visible = True
        self.assertTrue(base.visible)
        self.assertTrue(adv.visible)
        base.visible = False
        self.assertFalse(adv.visible)
    def test_invalid_json_ignored(self):
        spawns = []
        def fake_ask(route, prompt, timeout=30, max_tokens=60):
            return "no json here"
        adv = GlimmerAdvisor(self.base, route="r", ask=fake_ask, spawn=lambda fn: spawns.append(fn), clock=lambda: 0, backoff=60.0)
        d0 = self.base(self.state_high, self.tasks_all)
        d1 = adv(self.state_high, self.tasks_all)
        self.assertEqual(d1, d0)
        self.assertEqual(len(spawns), 1)
        spawns[0]()
        self.assertEqual(len(adv.cache), 0)
        self.assertEqual(adv.down_until, 60.0)
        d2 = adv(self.state_high, self.tasks_all)
        self.assertEqual(d2, d0)

    def test_hidden_returns_base_and_no_spawn(self):
        base = Router(visible=False)
        spawns = []
        def fake_ask(*a, **k):
            self.fail("ask should not be called when hidden")
        adv = GlimmerAdvisor(base, route="r", ask=fake_ask, spawn=lambda fn: spawns.append(fn), clock=lambda: 0)
        d0 = base(self.state_high, self.tasks_all)
        d1 = adv(self.state_high, self.tasks_all)
        self.assertEqual(d1, d0)
        self.assertEqual(len(spawns), 0)

    def test_goal_bucket_reuses_advice_and_one_inference_in_flight(self):
        # Regression (lane C): a running simulation changes goal_hours every tick; the advisor must not
        # queue one Glimmer request per tick, and advice is reused within a 0.5 h goal bucket.
        from opos.advisor import goal_bucket
        self.assertIsNone(goal_bucket(None))
        self.assertEqual(goal_bucket(3.21), 3.0)
        self.assertEqual(goal_bucket(3.18), 3.0)
        self.assertEqual(goal_bucket(3.3), 3.5)
        spawns = []
        adv = GlimmerAdvisor(self.base, route="r", ask=lambda *a, **k: '{"defer": ["sync"]}', spawn=lambda fn: spawns.append(fn), clock=lambda: 0)
        tick1 = PowerState(level=90, charging=False, health=1.0, temp_c=20, goal_hours=3.21)
        tick2 = PowerState(level=90, charging=False, health=1.0, temp_c=20, goal_hours=3.18)
        other = PowerState(level=50, charging=False, health=1.0, temp_c=20, goal_hours=2.0)
        adv(tick1, self.tasks_all)
        self.assertEqual(len(spawns), 1)
        adv(tick2, self.tasks_all)
        self.assertEqual(len(spawns), 1, "same goal bucket must not queue a second inference")
        adv(other, self.tasks_all)
        self.assertEqual(len(spawns), 1, "a different key must wait while one inference is in flight")
        spawns[0]()
        self.assertEqual(len(adv.pending), 0)
        d = adv(tick2, self.tasks_all)
        self.assertIn("sync", d.deferred, "completed advice is reused within the bucket")
        self.assertEqual(d.reason, "router+glimmer")
        adv(other, self.tasks_all)
        self.assertEqual(len(spawns), 2, "the next key runs once the previous inference finished")
