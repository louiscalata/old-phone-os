# SPDX-License-Identifier: GPL-3.0-or-later
"""The README demo stays honest through the daily goal, without a server or model."""
import json
import math
import threading
import time
import unittest
import urllib.request
from unittest import mock

import opos.setup.config as setup_config
from opos import muse, web
from opos.advisor import GlimmerAdvisor
from opos.dashboard import Dashboard, _format_goal_12h
from opos.governor import Mode
from opos.runtime import DEFAULT_TASKS

PASSED = "; the 4 PM goal time has passed and calls and messages stay on."
WONT_LAST_4PM = "Battery rules paused this because the battery won't last until 4 PM."


class TestDemoDay(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch("opos.web.prefs.load", side_effect=OSError))
        self.enterContext(mock.patch("opos.web.assist.free_mb", return_value=4096))
        self.now = 0.0
        self.enterContext(mock.patch.object(web.time, "monotonic", lambda: self.now))

    def phone(self, visible=True, goal="16:00"):
        phone = web.Phone(visible=visible)
        phone.dash.set_goal(goal, phone.clock_s)  # As the page and startup set it: at noon.
        return phone

    def step(self, phone, seconds):
        self.now += seconds
        phone.tick(seconds)
        return phone.state()

    def assert_essentials_on(self, state):
        for name in ("calls", "messages"):
            self.assertIn(name, state["helper"]["allowed"])
            self.assertNotIn(name, state["helper"]["paused"])

    def assert_reached_story(self, phone, state):
        battery, helper = state["battery"], state["helper"]
        alive = round(phone.sim.level) > 0  # The empty rule calls and messages use.
        self.assertIs(battery["goal_met"], alive)
        self.assertEqual(battery["goal_note"], "Made it to 4 PM. Calls and messages stay on" if alive else "")
        self.assertEqual(helper["why_paused"].endswith(PASSED), alive)
        self.assertFalse(battery["goal_note"].startswith("Won't last"))
        self.assertNotIn("toward 16:00", helper["why_paused"])
        self.assertNotIn("until 16:00", helper["why_paused"])
        self.assertNotIn("No goal set", battery["why"])
        self.assertIn("Goal time reached", battery["why"])
        self.assertNotIn("16:00", state["lock_story"])
        self.assertNotIn("4 PM", state["lock_story"])
        self.assertEqual(helper["goal"], "16:00")
        self.assertIs(helper["goal_met"], alive)

    def run_demo(self, step_s):
        phone = self.phone()
        phone.set_sim(True, 60)
        state = phone.state()
        previous = (state["helper"]["mode"], tuple(sorted(state["helper"]["paused"])))
        sequence = [(state["clock"], previous)]
        maximum_temp = phone.sim.temp_c
        last_before_goal = None
        reached_states = 0
        self.assert_essentials_on(state)
        while phone.clock_s < 16.5 * 3600:
            state = self.step(phone, step_s)
            self.assert_essentials_on(state)
            if phone.clock_s < 16 * 3600:
                current = (state["helper"]["mode"], tuple(sorted(state["helper"]["paused"])))
                if current != previous:
                    sequence.append((state["clock"], current))
                previous = current
                maximum_temp = max(maximum_temp, phone.sim.temp_c)
                last_before_goal = state
            else:
                self.assert_reached_story(phone, state)
                reached_states += 1
        self.assertGreater(reached_states, 0)
        return last_before_goal, sequence, maximum_temp

    def test_demo_one_second_steps_reaches_goal(self):
        before, _, _ = self.run_demo(1)
        self.assertEqual(before["clock"], "15:59")
        self.assertTrue(before["battery"]["goal_met"])
        self.assertIn("until 16:00", before["helper"]["why_paused"])

    def test_demo_three_second_steps_do_not_flap(self):
        _, sequence, maximum_temp = self.run_demo(3)
        self.assertLessEqual(len(sequence) - 1, 4, f"3 s transition sequence: {sequence!r}")
        self.assertLess(maximum_temp, 45)

    def test_demo_five_second_steps_do_not_flap(self):
        _, sequence, maximum_temp = self.run_demo(5)
        self.assertLessEqual(len(sequence) - 1, 4, f"5 s transition sequence: {sequence!r}")
        self.assertLess(maximum_temp, 45)

    def test_fresh_battery_after_goal_keeps_everything_on(self):
        phone = self.phone()
        phone.clock_s = 16 * 3600 + 60
        phone.sim.level, phone.sim.health = 100, 1.0
        state = phone.state()
        self.assertEqual(state["helper"]["mode"], "NORMAL")
        self.assertEqual(state["helper"]["paused"], [])
        self.assertTrue(state["battery"]["goal_met"])

    def test_dashboard_goal_grace_boundaries(self):
        dash = Dashboard(DEFAULT_TASKS)
        dash.set_goal("16:00")
        self.assertTrue(dash.goal_reached(16 * 3600))
        self.assertIsNone(dash.hours_to_goal(16 * 3600))
        self.assertIsNone(dash.hours_to_goal(23 * 3600 + 59 * 60))
        self.assertFalse(dash.goal_reached(0))  # Exactly eight hours ends the grace window.
        self.assertEqual(dash.hours_to_goal(0), 16)
        self.assertFalse(dash.goal_reached(60))
        self.assertAlmostEqual(dash.hours_to_goal(60), 15.983, places=2)
        self.assertEqual(dash.hours_to_goal(12 * 3600), 4.0)
        self.assertFalse(dash.goal_reached(None))
        self.assertIsNone(dash.hours_to_goal(None))
        dash.set_goal(None)
        self.assertFalse(dash.goal_reached(16 * 3600))
        self.assertFalse(dash.goal_reached(None))
        self.assertIsNone(dash.hours_to_goal(16 * 3600))

    def test_nightly_goal_grace_crosses_midnight(self):
        dash = Dashboard(DEFAULT_TASKS)
        dash.set_goal("22:00")
        self.assertTrue(dash.goal_reached(22 * 3600))
        self.assertTrue(dash.goal_reached(0))
        self.assertIsNone(dash.hours_to_goal(5 * 3600 + 59 * 60))
        self.assertFalse(dash.goal_reached(6 * 3600))
        self.assertEqual(dash.hours_to_goal(6 * 3600), 16)

    def test_usable_percent_is_floored_in_sentence_and_fact(self):
        self.assertIn("so 24% is really about 14%", web.why_paused(24.5, .6, 30, False, ["maps"]))
        phone = web.Phone(visible=True)
        phone.set_battery(24.5)
        helper = phone.state()["helper"]
        self.assertEqual(helper["usable_percent"], 14)
        self.assertIn("so 24% is really about 14%", helper["why_paused"])

    def test_charging_after_goal_has_no_success_note(self):
        phone = self.phone()
        phone.clock_s = 16 * 3600
        phone.charging = True
        state = phone.state()
        self.assertEqual(state["battery"]["goal_note"], "")
        self.assertTrue(state["battery"]["goal_met"])

    def test_charging_empty_battery_after_goal_is_not_success(self):
        for level in (0, 0.4, 0.5):  # All shown as 0%, where calls and messages are refused.
            phone = self.phone()
            phone.clock_s = 16 * 3600
            phone.sim.level = level
            phone.charging = True
            state = phone.state()
            self.assertFalse(state["battery"]["goal_met"], level)
            self.assertFalse(state["helper"]["goal_met"], level)
            self.assertNotIn("goal time has passed", state["helper"]["why_paused"])
            self.assertNotIn("goal time has passed", phone.ask_facts()["why_paused"])

    def test_empty_battery_after_goal_is_not_success(self):
        phone = self.phone()
        phone.clock_s = 16 * 3600
        phone.sim.level = 0
        state = phone.state()
        self.assertEqual(state["battery"]["goal_note"], "")
        self.assertFalse(state["battery"]["goal_met"])
        self.assertFalse(state["helper"]["goal_met"])

    def test_battery_shown_as_zero_after_goal_is_not_success(self):
        for level in (0.4, 0.5):  # 0.5 is a battery slider step; both show 0% and refuse calls.
            with self.subTest(level=level):
                phone = self.phone()
                phone.clock_s = 16 * 3600 + 600
                phone.sim.level = level
                state = phone.state()
                self.assertEqual(state["battery"]["percent"], 0)
                self.assertEqual(state["battery"]["goal_note"], "")
                self.assertFalse(state["battery"]["goal_met"])
                self.assertFalse(state["helper"]["goal_met"])
                phone.call("+15550100")
                self.assertEqual(phone.status, "error: battery empty, charge the phone to call")
        phone = self.phone()
        phone.clock_s = 16 * 3600 + 600
        phone.sim.level = 0.6
        state = phone.state()
        self.assertEqual(state["battery"]["goal_note"], "Made it to 4 PM. Calls and messages stay on")
        self.assertTrue(state["helper"]["goal_met"])

    def test_goal_set_inside_its_grace_window_is_not_reached(self):
        dash = Dashboard(DEFAULT_TASKS)
        dash.set_goal("06:30", 12 * 3600)  # 06:30 entered at noon means tomorrow morning.
        self.assertFalse(dash.goal_reached(12 * 3600))
        self.assertEqual(dash.hours_to_goal(12 * 3600), 18.5)
        view = dash.view(3, Mode.NORMAL, False, 1.0, 12 * 3600, True, "")
        self.assertTrue(view["goal_note"].startswith("Won't last until 6:30 AM, calls and messages until ~"))
        self.assertFalse(view["goal_met"])
        self.assertFalse(dash.goal_reached(15 * 3600))  # Seen before the next 06:30, so that one counts.
        self.assertTrue(dash.goal_reached(6.5 * 3600))
        dash.set_goal("12:00", 12 * 3600)  # Set at its own time: not made either.
        self.assertFalse(dash.goal_reached(12 * 3600))
        self.assertEqual(dash.hours_to_goal(12 * 3600), 24)
        dash.set_goal("16:00", 12 * 3600)  # The README demo goal, set before its time.
        self.assertTrue(dash.goal_reached(16 * 3600))

    def test_owner_message_text_is_never_redacted(self):
        phone = web.Phone(visible=False)
        phone.receive("+15550100", "Can my assistant pick up the new model AI kit?")
        phone.open("+15550100")
        phone.send("+15550100", "The Governor model is fine")
        state = phone.state()
        expected = ["Mom: Can my assistant pick up the new model AI kit?",
                    "Me: The Governor model is fine"]
        self.assertEqual(state["thread"], expected)
        self.assertEqual(state["threads"][0]["last"], "The Governor model is fine")
        self.assertNotIn("helper", state)
        self.assertNotRegex(json.dumps(state["battery"]), r"(?i)\b(?:ai|model|governor|assistant)\b")
        phone.set_visible(True)
        visible = phone.state()
        self.assertEqual(visible["thread"], expected)
        self.assertEqual(visible["threads"], state["threads"])

    def glimmer_phone(self, reply):
        phone = web.Phone(visible=False)
        governor = phone.runtime.governor
        governor.advisor = GlimmerAdvisor(
            governor.advisor, route=muse.glimmer_route({}),
            ask=lambda *args, **kwargs: reply,
            spawn=lambda fn: fn(), clock=lambda: 0)
        phone.set_visible(True)
        phone.state()  # Fill the synchronous stub's cache; the next state applies it.
        return phone, phone.state()

    def test_glimmer_pause_attribution_tracks_undo_and_visibility(self):
        phone, state = self.glimmer_phone('{"defer":["email"]}')
        self.assertEqual(state["helper"]["paused_by_glimmer"], ["email"])
        reasons = {row["name"]: row["reason"] for row in state["battery"]["paused"]}
        self.assertTrue(reasons["email"].startswith("Muse Glimmer "))
        for name in ("maps", "photos backup"):
            self.assertTrue(reasons[name].startswith("Battery rules "))
        phone.dash.undo("email")
        self.assertNotIn("paused_by_glimmer", phone.state()["helper"])
        phone.set_visible(False)
        self.assertNotIn("helper", phone.state())

    def test_empty_glimmer_reply_has_no_pause_attribution(self):
        _, state = self.glimmer_phone('{"defer":[]}')
        self.assertNotIn("paused_by_glimmer", state["helper"])

    def test_plain_phone_has_no_glimmer_pause_attribution(self):
        self.assertNotIn("paused_by_glimmer", web.Phone(visible=True).state()["helper"])

    def test_huge_speed_is_bounded_and_finite(self):
        phone = self.phone()
        phone.set_sim(True, 1e9)
        started = time.perf_counter()
        self.step(phone, 5)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(math.isfinite(phone.sim.level))

    def test_unreachable_goal_pause_reason_states_the_shortfall(self):
        for visible in (False, True):
            with self.subTest(visible=visible):
                phone = self.phone(visible=visible)
                phone.sim.level, phone.sim.health = 20, 1.0
                state = phone.state()
                self.assertFalse(state["battery"]["goal_met"])
                self.assertTrue(state["battery"]["paused"])
                for row in state["battery"]["paused"]:
                    self.assertEqual(row["reason"],
                                     "Battery rules paused this because the battery won't last until 4 PM.")
                    self.assertNotIn("to help the battery last", row["reason"])
                    self.assertNotIn("16:00", row["reason"])

    def test_pause_reason_uses_corrected_goal_after_undo(self):
        phone = self.phone()
        self.assertTrue(phone.state()["battery"]["goal_met"])
        phone.dash.undo("photos backup")
        state = phone.state()
        self.assertFalse(state["battery"]["goal_met"])
        self.assertEqual([(row["name"], row["reason"]) for row in state["battery"]["paused"]],
                         [("maps", "Battery rules paused this because the battery won't last until 4 PM.")])

    def test_planned_pause_reason_follows_missed_goal_after_undo(self):
        # Here the planner, not the goal check, paused maps; Undo still makes the goal missed.
        for visible in (True, False):
            for level, health in ((70, 0.6), (42, 1.0)):
                with self.subTest(visible=visible, level=level, health=health):
                    phone = self.phone(visible=visible)
                    phone.sim.level, phone.sim.health = level, health
                    self.assertTrue(phone.state()["battery"]["goal_met"])
                    phone.dash.undo("photos backup")
                    battery = phone.state()["battery"]
                    self.assertFalse(battery["goal_met"])
                    self.assertEqual([(row["name"], row["reason"]) for row in battery["paused"]],
                                     [("maps", WONT_LAST_4PM)])

    def test_reachable_goal_pause_reasons_use_twelve_hour_time(self):
        state = self.phone().state()
        self.assertTrue(state["battery"]["goal_met"])
        self.assertTrue(state["battery"]["paused"])
        for row in state["battery"]["paused"]:
            self.assertEqual(row["reason"], "Battery rules paused this to help the battery last until 4 PM.")
        self.assertIn("for 4 PM", state["lock_story"])

    def test_goal_after_midnight_says_tomorrow_on_lock_screen_and_rows(self):
        self.assertEqual(_format_goal_12h("06:00 tomorrow"), "6 AM tomorrow")
        cases = ((60, 0.6, False, "Worn battery. 3 tasks paused; 6 AM tomorrow unlikely.",
                  "Battery rules paused this because the battery won't last until 6 AM tomorrow."),
                 (100, 1.0, True, "3 tasks paused for 6 AM tomorrow.",
                  "Battery rules paused this to help the battery last until 6 AM tomorrow."))
        for level, health, met, story, reason in cases:
            with self.subTest(level=level, health=health):
                phone = self.phone(goal="06:00")
                phone.clock_s = 15 * 3600
                phone.sim.level, phone.sim.health = level, health
                state = phone.state()
                self.assertIs(state["battery"]["goal_met"], met)
                self.assertIn("06:00 tomorrow", state["helper"]["why_paused"])
                self.assertEqual(state["lock_story"], story)
                self.assertEqual({row["reason"] for row in state["battery"]["paused"]}, {reason})

    def test_helper_facts_say_the_goal_time_passed(self):
        phone = self.phone()
        self.assertNotIn("goal time has passed", phone.ask_facts()["why_paused"])  # Noon: still ahead.
        phone.clock_s = 21 * 3600
        phone.sim.level = 4.2
        facts = phone.ask_facts()
        self.assertTrue(facts["why_paused"].endswith(PASSED), facts["why_paused"])
        self.assertIs(facts["goal_met"], True)
        phone.charging = True
        self.assertTrue(phone.ask_facts()["why_paused"].endswith(PASSED))
        phone.charging = False
        phone.sim.level = 0.4  # Shown as 0%: calls and messages are refused, so no such clause.
        self.assertNotIn("goal time has passed", phone.ask_facts()["why_paused"])


class TestGoalSetAfterItsTime(unittest.TestCase):
    """A goal entered after its time today means tomorrow, from the page and from saved setup."""

    def setUp(self):
        self.enterContext(mock.patch("opos.web.prefs.load", side_effect=OSError))
        self.enterContext(mock.patch("opos.web.assist.free_mb", return_value=4096))

    def assert_not_made(self, state, words):
        self.assertFalse(state["battery"]["goal_met"])
        self.assertTrue(state["battery"]["goal_note"].startswith(
            f"Won't last until {words}, calls and messages until ~"), state["battery"]["goal_note"])

    def test_saved_goal_before_noon_is_not_made_at_startup(self):
        cfg = setup_config.default_config()
        cfg["goal"] = {"enabled": True, "time": "08:00"}
        captured = {}

        def fake_make_server(port, phone, host="127.0.0.1", token=None):
            captured["phone"] = phone
            return mock.Mock()
        with mock.patch.object(setup_config, "load", return_value=cfg), \
                mock.patch.object(web, "_route_with_glimmer"), \
                mock.patch.object(web, "make_server", side_effect=fake_make_server):
            web.main([])
        phone = captured["phone"]
        self.assertEqual(phone.dash.hours_to_goal(phone.clock_s), 20)
        self.assert_not_made(phone.state(), "8 AM")

    def test_page_goal_before_noon_is_not_made(self):
        phone = web.Phone(visible=True)
        server = web.make_server(0, phone)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/api",
                data=json.dumps({"action": "goal", "time": "06:30"}).encode(),
                headers={"Content-Type": "application/json", "X-OPOS-Token": server.RequestHandlerClass.token})
            with urllib.request.urlopen(req, timeout=10) as resp:
                state = json.load(resp)
        finally:
            server.shutdown()
            server.server_close()
        self.assert_not_made(state, "6:30 AM")
        self.assertFalse(state["helper"]["goal_met"])


if __name__ == "__main__":
    unittest.main()
