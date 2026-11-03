# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressions for the Grok phone-state review; no model or server needed."""
import json
import unittest
from unittest import mock

from opos.advisor import GlimmerAdvisor
from opos.web import Phone, why_paused


class TestGrokStateFixes(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch("opos.web.prefs.load", side_effect=OSError))
        self.enterContext(mock.patch("opos.web.assist.free_mb", return_value=4096))

    def phone(self, goal="16:00", visible=True):
        phone = Phone(visible=visible)
        phone.dash.set_goal(goal)
        return phone

    def screen_paused(self, state):
        prefix = "Paused to save power: "
        return {name for line in state["screen"] if line.startswith(prefix)
                for name in line[len(prefix):].split(", ")}

    def add_glimmer(self, phone, names):
        ask = mock.Mock(return_value=json.dumps({"defer": names}))
        phone.runtime.governor.advisor = GlimmerAdvisor(
            phone.runtime.governor.advisor, route=object(), ask=ask,
            spawn=lambda fn: fn())
        phone.state()  # Fill the cache; the next state applies the reply.
        return ask

    def test_a_goal_at_clock_is_reached(self):
        for goal, clock in (("16:00", 16 * 3600), ("12:00", 12 * 3600)):
            with self.subTest(goal=goal):
                phone = self.phone(goal)
                phone.clock_s = clock
                self.assertIsNone(phone.dash.hours_to_goal(clock))
                self.assertTrue(phone.dash.goal_reached(clock))
                state = phone.state()
                self.assertTrue(state["battery"]["goal_met"])
                self.assertTrue(state["helper"]["goal_met"])
                self.assertNotIn("tomorrow", state["helper"]["why_paused"])

    def test_a_past_goal_says_tomorrow(self):
        for goal, clock, hours in (("06:00", 15 * 3600, 15.0),
                                   ("11:00", 20 * 3600, 15.0)):
            with self.subTest(goal=goal, clock=clock):
                phone = self.phone(goal)
                phone.clock_s = clock
                self.assertAlmostEqual(phone.dash.hours_to_goal(clock), hours)
                helper = phone.state()["helper"]
                self.assertEqual(helper["goal"], goal)
                self.assertIn(goal + " tomorrow", helper["why_paused"])
        phone = self.phone("16:00")
        phone.clock_s = 16 * 3600 + 1
        self.assertIsNone(phone.dash.hours_to_goal(phone.clock_s))
        helper = phone.state()["helper"]
        self.assertNotIn("tomorrow", helper["why_paused"])
        self.assertNotIn("until 16:00", helper["why_paused"])

    def test_a_future_goal_stays_today(self):
        phone = self.phone()
        self.assertEqual(phone.dash.hours_to_goal(phone.clock_s), 4)
        helper = phone.state()["helper"]
        self.assertTrue(helper["goal_met"])
        self.assertIn("until 16:00", helper["why_paused"])
        self.assertNotIn("tomorrow", helper["why_paused"])

    def test_b_undo_all_reports_missed_goal(self):
        phone = self.phone()
        before = phone.state()
        self.assertEqual(set(before["helper"]["paused"]), {"maps", "photos backup"})
        self.assertTrue(before["helper"]["goal_met"])
        phone.dash.undo("maps")
        phone.dash.undo("photos backup")
        helper = phone.state()["helper"]
        self.assertEqual(helper["paused"], [])
        self.assertFalse(helper["goal_met"])
        self.assertIn("may not last until 16:00", helper["why_paused"])
        self.assertIn("nothing is paused", helper["why_paused"])
        self.assertIn("at this rate", helper["why_paused"])
        self.assertIn(helper["time_left"].lower(), helper["why_paused"])

    def test_c_undo_updates_home_and_decision(self):
        for visible in (False, True):
            with self.subTest(visible=visible):
                phone = self.phone(visible=visible)
                self.assertEqual(self.screen_paused(phone.state()), {"maps", "photos backup"})
                for name, remaining in (("maps", {"photos backup"}), ("photos backup", set())):
                    phone.dash.undo(name)
                    state = phone.state()
                    self.assertEqual(self.screen_paused(state), remaining)
                    self.assertEqual({p["name"] for p in state["battery"]["paused"]}, remaining)
                    self.assertIn(name.capitalize(), state["screen"])
                    self.assertIn(name, phone._tick_obj.decision.allowed)
                    self.assertNotIn(name, phone._tick_obj.decision.deferred)
                    if visible:
                        self.assertEqual(set(state["helper"]["paused"]), remaining)

    def test_c_undo_glimmer_pause_removes_stale_credit(self):
        phone = self.phone()
        ask = self.add_glimmer(phone, ["email"])
        self.assertIn("Helper: Muse Glimmer paused extra tasks", phone.state()["screen"])
        phone.dash.undo("email")
        state = phone.state()
        self.assertNotIn("Helper: Muse Glimmer paused extra tasks", state["screen"])
        self.assertEqual(self.screen_paused(state), {"maps", "photos backup"})
        self.assertTrue(all(p["reason"].startswith("Battery rules") for p in state["battery"]["paused"]))
        ask.assert_called_once()

    def test_c_remaining_glimmer_pause_keeps_credit(self):
        phone = self.phone()
        phone.charging = True
        ask = self.add_glimmer(phone, ["email", "maps"])
        phone.dash.undo("email")
        state = phone.state()
        self.assertEqual(self.screen_paused(state), {"maps"})
        self.assertIn("Helper: Muse Glimmer paused extra tasks", state["screen"])
        phone.dash.undo("maps")
        state = phone.state()
        self.assertEqual(self.screen_paused(state), set())
        self.assertNotIn("Helper: Muse Glimmer paused extra tasks", state["screen"])
        ask.assert_called_once()

    def test_d_fractional_usable_fact_matches_sentence(self):
        phone = self.phone()
        phone.set_battery(24.5)
        helper = phone.state()["helper"]
        self.assertEqual(helper["usable_percent"], 14)
        self.assertEqual(helper["mode"], "CRITICAL")
        self.assertIn("24% charge is about 14% usable", helper["why_paused"])

    def test_d_direct_sentence_truncates_usable(self):
        self.assertIn("about 14% usable", why_paused(24.5, .6, 30, False, []))

    def test_e_stopped_empty_battery_ends_call_immediately(self):
        phone = self.phone()
        self.assertFalse(phone.running)
        phone.call("5551234")
        self.assertTrue(phone._in_call)
        phone.set_battery(0)
        self.assertFalse(phone._in_call)
        self.assertFalse(phone.essentials.in_call)
        state = phone.state()
        self.assertEqual(state["status"], "call ended: battery empty")
        self.assertEqual(state["notice"], "Call ended. Charge the phone")

    def test_e_charging_empty_setter_keeps_existing_call(self):
        phone = self.phone()
        phone.call("5551234")
        phone.charging = True
        for level in (0, .5):
            with self.subTest(level=level):
                phone.set_battery(level)
                self.assertTrue(phone._in_call)
                self.assertTrue(phone.essentials.in_call)

    def test_f_displayed_empty_refuses_call(self):
        for level in (0, .49, .5):
            with self.subTest(level=level):
                phone = self.phone()
                phone.set_battery(level)
                battery = phone.state()["battery"]
                self.assertEqual(battery["percent"], 0)
                self.assertEqual(battery["meter"]["label"], "Battery 0 percent")
                phone.call("5551234")
                self.assertEqual(phone.status, "error: battery empty, charge the phone to call")
                self.assertFalse(phone._in_call)
                self.assertFalse(phone.essentials.in_call)

    def test_f_displayed_empty_refuses_message(self):
        for level in (0, .49, .5):
            with self.subTest(level=level):
                phone = self.phone()
                phone.set_battery(level)
                phone.send("5551234", "hi")
                self.assertEqual(phone.status, "error: battery empty, charge the phone to send")
                self.assertEqual(phone.essentials.log, [])

    def test_f_displayed_empty_tick_ends_call(self):
        phone = self.phone()
        phone.call("5551234")
        phone.sim.level = .5
        # Keep the exact half-percent boundary rather than draining below it.
        with mock.patch.object(phone.sim, "advance"):
            phone.tick()
        self.assertFalse(phone._in_call)
        self.assertFalse(phone.essentials.in_call)
        self.assertEqual(phone.status, "call ended: battery empty")

    def test_f_displayed_empty_setter_ends_call(self):
        phone = self.phone()
        phone.call("5551234")
        phone.set_battery(.5)
        self.assertFalse(phone._in_call)
        self.assertFalse(phone.essentials.in_call)
        self.assertEqual(phone.status, "call ended: battery empty")

    def test_f_displayed_nonempty_allows_call_and_message(self):
        phone = self.phone()
        phone.set_battery(.51)
        self.assertEqual(phone.state()["battery"]["percent"], 1)
        phone.call("5551234")
        self.assertEqual(phone.status, "in call")
        self.assertTrue(phone._in_call)
        phone.send("5551234", "hi")
        self.assertEqual(phone.status, "sent")
        self.assertEqual(len(phone.essentials.log), 1)


if __name__ == "__main__":
    unittest.main()
