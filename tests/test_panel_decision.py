# SPDX-License-Identifier: GPL-3.0-or-later
"""One final decision: owner overrides first, hard floors last, and every view and the drain read it."""
import itertools
import json
import math
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from opos import web
from opos.governor import PowerState, Priority, Task
from opos.power import time_left_words, total_drain
from opos.runtime import DEFAULT_TASKS, Runtime

NAMES = tuple(t.name for t in DEFAULT_TASKS)
HOT = "Battery rules paused this because the phone is hot."


def screen_paused(state):
    for line in state["screen"]:
        if line.startswith("Paused to save power: "):
            return [n.strip() for n in line[len("Paused to save power: "):].split(", ") if n.strip()]
    return []


class PhoneCase(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch("opos.web.prefs.load", side_effect=OSError),
                        mock.patch("opos.web.assist.free_mb", return_value=4096),
                        mock.patch("opos.web.self_rss_mb", return_value=12)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = 1000.0
        clock = mock.patch.object(web.time, "monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def phone(self, level=60.0, health=0.6, temp_c=30.0, visible=True, goal=None):
        phone = web.Phone(visible=visible)
        phone.sim.level, phone.sim.health, phone.sim.temp_c = level, health, temp_c
        if goal:
            phone.dash.set_goal(goal, phone.clock_s)
        return phone

    def reasons(self, state):
        return {row["name"]: row["reason"] for row in state["battery"]["paused"]}

    def undo(self, phone, name):
        """Press Undo the way the page does, through the API; returns the HTTP status."""
        server = web.make_server(0, phone)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:%d/api" % server.server_address[1],
                data=json.dumps({"action": "undo", "name": name}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-OPOS-Token": server.RequestHandlerClass.token},
                method="POST")
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                exc.close()
                return exc.code
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def measured_rate(self, phone, seconds=5.0, speed=60.0):
        """Percent per hour the simulation drained over one controlled tick."""
        phone.set_sim(True, speed)
        level = phone.sim.level
        self.now += seconds
        phone.tick(seconds)
        return (level - phone.sim.level) / (seconds * speed / 3600)

    def predicted_rate(self, phone, state):
        """Percent per hour for what the screens say runs, as the time-left estimate counts it."""
        return total_drain(DEFAULT_TASKS, paused=state["helper"]["paused"], steps=state["battery"]["steps"], health=phone.sim.health)


class OwnerOverridesThenHardFloors(PhoneCase):
    def test_undo_does_not_survive_heat_and_an_empty_battery(self):
        phone = self.phone()
        self.assertIn("photos backup", phone.state()["helper"]["paused"])
        self.assertEqual(self.undo(phone, "photos backup"), 200)
        self.assertIn("photos backup", phone.state()["helper"]["allowed"])
        phone.set_battery(3)
        phone.set_temp(58)
        state = phone.state()
        self.assertEqual(state["helper"]["mode"], "CRITICAL")
        self.assertEqual(state["helper"]["allowed"], ["calls", "messages"])
        self.assertEqual(state["helper"]["paused"], ["email", "maps", "photos backup"])
        self.assertEqual(state["lock_story"], "Phone hot. Email, maps and photos backup paused.")
        self.assertEqual(self.reasons(state)["photos backup"], HOT)
        # The mode changed, so the Undo is over: back at 60% and 30 C photos backup is paused again.
        phone.set_battery(60)
        phone.set_temp(30)
        self.assertIn("photos backup", phone.state()["helper"]["paused"])
        self.assertEqual(phone.dash.undone, set())

    def test_foreground_app_does_not_run_when_hot(self):
        phone = self.phone(level=90, health=1.0, temp_c=50)
        phone.dash.set_in_use("maps")
        state = phone.state()
        self.assertNotIn("maps", state["helper"]["allowed"])
        self.assertIn("maps", state["helper"]["paused"])
        self.assertEqual(self.reasons(state)["maps"], HOT)
        self.assertNotIn("maps", state["battery"]["saving_for"])

    def test_foreground_app_does_not_run_at_the_critical_floor(self):
        for visible, words in ((True, "because usable battery is low."), (False, "because the battery is low.")):
            with self.subTest(visible=visible):
                phone = self.phone(level=10, health=1.0, visible=visible)
                phone.dash.set_in_use("maps")
                state = phone.state()
                self.assertIn("maps", [row["name"] for row in state["battery"]["paused"]])
                self.assertEqual(self.reasons(state)["maps"], "Battery rules paused this " + words)

    def test_owner_overrides_soft_pauses(self):
        phone = self.phone()  # 60% at 60% health: SAVER, maps and photos backup paused by the router
        phone.dash.set_in_use("maps")
        self.assertEqual(self.undo(phone, "photos backup"), 200)
        state = phone.state()
        self.assertEqual(state["helper"]["paused"], [])
        self.assertEqual(state["battery"]["paused"], [])
        self.assertEqual(state["lock_story"], "Worn battery. Nothing paused.")

    def test_undo_lasts_until_the_mode_changes(self):
        phone = self.phone()
        self.assertEqual(self.undo(phone, "photos backup"), 200)
        self.assertNotIn("photos backup", phone.state()["helper"]["paused"])
        phone.set_battery(65)  # still SAVER (at 55% email pauses too and the router says CRITICAL)
        self.assertEqual(phone.state()["helper"]["mode"], "SAVER")
        self.assertNotIn("photos backup", phone.state()["helper"]["paused"])
        phone.charging = True  # NORMAL while charging
        self.assertEqual(phone.state()["helper"]["mode"], "NORMAL")
        phone.charging = False
        state = phone.state()
        self.assertEqual(state["helper"]["mode"], "SAVER")
        self.assertIn("photos backup", state["helper"]["paused"])
        self.assertIn("photos backup paused", state["battery"]["events"])

    def test_undo_of_a_held_task_has_no_effect_and_no_event(self):
        phone = self.phone(level=100, health=1.0, temp_c=50)
        before = phone.state()
        self.assertEqual(self.undo(phone, "#3"), 200)
        state = phone.state()
        self.assertEqual(phone.dash.undone, set())
        self.assertEqual(state["battery"]["paused"], before["battery"]["paused"])
        self.assertNotIn("maps turned back on", state["battery"]["events"])
        self.assertEqual(self.undo(phone, "#99"), 400)
        with self.assertRaises(ValueError):
            phone.undo("#99")

    def test_worn_reserve_reason_beats_the_foreground_app(self):
        phone = self.phone(level=8, health=0.4)
        phone.dash.set_in_use("maps")
        state = phone.state()
        self.assertEqual(self.reasons(state).get("maps"), "Battery rules paused this to keep a battery reserve for calls.")


class EveryViewReadsTheSameDecision(PhoneCase):
    def test_reported_case_low_worn_battery_with_maps_in_use(self):
        phone = self.phone(level=5, health=0.35)
        phone.dash.set_in_use("maps")
        state = phone.state()
        self.assertEqual(state["helper"]["paused"], ["email", "maps", "photos backup"])
        self.assertEqual([row["name"] for row in state["battery"]["paused"]], ["email", "maps", "photos backup"])
        self.assertEqual(screen_paused(state), ["email", "maps", "photos backup"])
        self.assertEqual(state["lock_story"], "Battery low. 3 tasks paused.")

    CASES = tuple(dict(zip(("level", "health", "temp_c", "charging", "goal", "in_use", "undo"), values))
                  for values in itertools.product((100, 60, 35, 20, 8, 3), (1.0, 0.6, 0.35), (30, 50), (False, True),
                                                  (None, "16:00"), (None, "maps"), (None, "photos backup")))

    def phone_for(self, case):
        phone = self.phone(case["level"], case["health"], case["temp_c"], goal=case["goal"])
        phone.charging = case["charging"]
        phone.dash.set_in_use(case["in_use"])
        phone.state()
        if case["undo"]:
            phone.dash.undo(case["undo"])  # Recorded even when a floor holds it; the decision decides.
        return phone, phone.state()

    def test_home_panel_lock_and_helper_agree(self):
        for case in self.CASES:
            with self.subTest(**case):
                phone, state = self.phone_for(case)
                helper = state["helper"]
                paused = helper["paused"]
                self.assertEqual(sorted(helper["allowed"] + paused), sorted(NAMES))
                self.assertEqual([row["name"] for row in state["battery"]["paused"]], paused)
                self.assertEqual(screen_paused(state), paused)
                self.assertEqual(state["lock_story"], web.lock_story(case["level"], case["health"], case["temp_c"], case["charging"],
                                                                     paused, phone._goal_label(), state["battery"]["goal_met"]))
                for name in paused:
                    self.assertIn(name, helper["why_paused"])
                if case["temp_c"] >= 45 or (not case["charging"] and case["level"] * case["health"] < 15):
                    self.assertEqual(paused, ["email", "maps", "photos backup"])

    def test_drain_reads_the_final_decision(self):
        for case in self.CASES:
            if case["charging"]:
                continue
            with self.subTest(**case):
                phone, state = self.phone_for(case)
                self.assertAlmostEqual(self.measured_rate(phone), self.predicted_rate(phone, state), places=9)


class SimulatedDrainMatchesTimeLeft(PhoneCase):
    def test_ten_percent_full_health_no_goal(self):
        phone = self.phone(level=10, health=1.0)
        state = phone.state()
        self.assertEqual(state["battery"]["time_left"], "Rough estimate: About 52 min left")  # the demo's drain is the model's
        rate = self.measured_rate(phone)
        self.assertAlmostEqual(rate, 11.5, places=9)
        self.assertEqual(time_left_words(10 / rate), "About 52 min left")

    def test_prediction_and_simulated_interval_agree(self):
        cases = (dict(level=60, health=0.6, goal="16:00"), dict(level=42, health=1.0, goal="16:00"),
                 dict(level=8, health=0.4), dict(level=90, health=1.0, temp_c=50), dict(level=35, health=0.6),
                 dict(level=60, health=0.6, undo="maps"), dict(level=60, health=0.6, in_use="maps", goal="16:00"))
        for case in cases:
            with self.subTest(**case):
                case = dict(case)
                undo, in_use = case.pop("undo", None), case.pop("in_use", None)
                phone = self.phone(**case)
                phone.dash.set_in_use(in_use)
                if undo:
                    self.assertEqual(self.undo(phone, undo), 200)
                state = phone.state()
                level, predicted = phone.sim.level, self.predicted_rate(phone, state)
                rate = self.measured_rate(phone)
                self.assertAlmostEqual(rate, predicted, places=9)
                self.assertEqual("Rough estimate: " + time_left_words(level / rate), state["battery"]["time_left"])


class RouterHeatAndGoalInThePhone(PhoneCase):
    def test_heat_applies_while_charging(self):
        phone = self.phone(level=60, health=1.0, temp_c=50)
        phone.charging = True
        state = phone.state()
        self.assertEqual(state["helper"]["allowed"], ["calls", "messages"])
        self.assertEqual(set(self.reasons(state).values()), {HOT})
        self.assertNotEqual(state["battery"]["headline"], "Charging: everything back on")
        self.assertEqual(state["lock_story"], "Charging. Email, maps and photos backup paused.")

    def test_allowed_set_lasts_to_the_goal(self):
        phone = self.phone(level=42, health=1.0, goal="16:00")  # noon: 4 h to go
        state = phone.state()
        self.assertEqual(state["helper"]["allowed"], ["calls", "messages", "email"])
        self.assertTrue(state["battery"]["goal_met"])
        self.assertEqual(self.reasons(state)["maps"], "Battery rules paused this to help the battery last until 4 PM.")


class TickInterval(PhoneCase):
    def test_tick_uses_the_real_seconds_the_page_sends(self):
        phone = self.phone()
        phone.set_sim(True, 1.0)
        for real_s, since, expected in ((2, 10, 2.0), (9, 10, 5.0), (3, 1, 1.0), (0, 4, 0.0)):
            with self.subTest(real_s=real_s, since=since):
                t_s = phone.sim.t_s
                self.now += since
                phone.tick(real_s)
                self.assertAlmostEqual(phone.sim.t_s - t_s, expected)

    def test_bad_real_s_changes_nothing(self):
        phone = self.phone()
        for bad in (-1, math.nan, math.inf, True, "1", None, 10 ** 400):
            with self.subTest(bad=bad):
                snap = (phone.sim.level, phone.sim.t_s, phone.clock_s, phone._last_tick_time)
                self.now += 3
                with self.assertRaises(ValueError):
                    phone.tick(bad)
                self.assertEqual((phone.sim.level, phone.sim.t_s, phone.clock_s, phone._last_tick_time), snap)

    def test_the_sim_drains_at_the_decision_rate(self):
        phone = self.phone(level=80)
        predicted = self.predicted_rate(phone, phone.state())
        with mock.patch.object(phone.sim, "advance", wraps=phone.sim.advance) as advance:
            self.now += 1
            phone.tick(1)
        self.assertEqual(advance.call_args.kwargs.get("per_hour"), predicted)

    def test_a_sim_without_charge_is_an_error_not_a_drain(self):
        phone = self.phone(level=80)
        phone.charging = True
        with mock.patch.object(phone.sim, "charge", side_effect=AttributeError("charge")), \
                mock.patch.object(phone.sim, "advance") as advance:
            self.now += 1
            with self.assertRaises(AttributeError):
                phone.tick(1)
        advance.assert_not_called()

    def test_step_errors_are_not_retried(self):
        phone = self.phone(level=80)
        with mock.patch.object(phone.runtime, "step", side_effect=TypeError("boom")) as step:
            with self.assertRaises(TypeError):
                phone.state()
        self.assertEqual(step.call_count, 1)


class GoalBudgetOwnsTheGoalPauses(unittest.TestCase):
    """The goal pauses are what the cumulative goal budget refuses, for any task list."""
    TASKS = (Task("calls", Priority.ESSENTIAL, 0.5), Task("messages", Priority.ESSENTIAL, 0.3),
             Task("nav", Priority.USEFUL, 5), Task("sync", Priority.OPTIONAL, 1))

    def test_cheap_optional_and_expensive_useful(self):
        # Spare 5.5 %/h: nav (useful) is funded first and fits; sync (optional) then does not.
        tick = Runtime(tasks=self.TASKS).step(0, 46.68, health=1.0, temp_c=25.0, goal_hours=4)
        self.assertEqual(tick.decision.allowed, ("calls", "messages", "nav"))
        self.assertEqual(tick.decision.deferred, ("sync",))
        self.assertEqual(tick.causes, (("sync", "goal"),))

    def test_the_app_in_use_is_funded_first(self):
        tasks = self.TASKS + (Task("news", Priority.OPTIONAL, 2),)
        # 60% at full health with 5 h to the goal leaves 5.83 %/h. The router alone funds nav;
        # with news in use, news is funded first, then nav does not fit and sync does.
        tick = Runtime(tasks=tasks).step(0, 60.0, health=1.0, temp_c=25.0, goal_hours=5, in_use="news")
        self.assertEqual(tick.decision.allowed, ("calls", "messages", "sync", "news"))
        self.assertEqual(tick.causes, (("nav", "goal"),))

    def test_goal_pauses_are_exactly_what_the_budget_refuses(self):
        from opos.router import fund, goal_spare
        lists = (DEFAULT_TASKS, self.TASKS, self.TASKS + (Task("news", Priority.OPTIONAL, 2),),
                 self.TASKS + (Task("cam", Priority.USEFUL, 0.5), Task("feed", Priority.OPTIONAL, 3)))
        for tasks in lists:
            names = [t.name for t in tasks if t.priority != Priority.ESSENTIAL]
            cost = {t.name: t.cost for t in tasks}
            for level, health, goal, in_use in itertools.product((55, 70, 85, 100), (0.6, 1.0), (2, 4, 6, 9), [None] + names):
                with self.subTest(tasks=names, level=level, health=health, goal=goal, in_use=in_use):
                    tick = Runtime(tasks=tasks).step(0, float(level), health=health, temp_c=25.0, goal_hours=goal, in_use=in_use)
                    state = PowerState(float(level), False, health, 25.0, goal)
                    verdicts = fund(state, tasks, (in_use,) if in_use else ())
                    causes = dict(tick.causes)
                    self.assertNotIn("other", causes.values())
                    for name in names:
                        if name == in_use:
                            continue
                        if verdicts[name][0]:
                            self.assertNotEqual(causes.get(name), "goal")
                            if causes.get(name) is None:
                                self.assertIn(name, tick.decision.allowed)
                        else:
                            self.assertIn(name, tick.decision.deferred)
                    spare = goal_spare(state)
                    used = sum(cost[n] for n in tick.decision.allowed if n in names)
                    if in_use is None or cost[in_use] <= spare:
                        self.assertLessEqual(used, max(spare, 0.0) + 1e-9)
                    else:
                        self.assertEqual([n for n in tick.decision.allowed if n in names], [in_use])


class HeldRowsHaveNoUndo(PhoneCase):
    def test_battery_panel_marks_the_rows_a_floor_holds(self):
        hot = self.phone(level=100, health=1.0, temp_c=50).state()["battery"]["paused"]
        self.assertEqual([(row["name"], row["held"]) for row in hot], [("email", True), ("maps", True), ("photos backup", True)])
        soft = self.phone().state()["battery"]["paused"]
        self.assertEqual([(row["name"], row["held"]) for row in soft], [("maps", False), ("photos backup", False)])

    def test_the_page_shows_undo_only_on_rows_it_can_change(self):
        js = web._asset("js-7.js")
        self.assertIn("${p.held?'':`<button aria-label=\"Turn ${esc(p.name||p)} back on\">Undo</button>`}", js)
        self.assertIn("const b=row.querySelector('button');if(b)b.onclick=", js)
        self.assertIn("p.reason||'',!!p.held]", js)  # a row that becomes held is drawn again


class MonotonicPollClock(unittest.TestCase):
    def test_tick_interval_cannot_go_negative_when_the_wall_clock_steps_back(self):
        js = web._asset("js-8.js")
        self.assertIn("pollAt=performance.now();", js)
        self.assertIn("const n=performance.now(),real_s=(n-pollAt)/1000;pollAt=n;", js)
        self.assertNotIn("pollAt=Date.now()", js)


class WornWords(unittest.TestCase):
    def test_one_constant_for_worn_words(self):
        self.assertEqual(web.WORN_WORDS_HEALTH, 0.8)
        self.assertEqual(web.lock_story(50, 0.79, 30, False, ()), "Worn battery. Nothing paused.")
        self.assertEqual(web.lock_story(50, 0.8, 30, False, ()), "Nothing paused.")
        self.assertIn("is worn. It holds 79% of what it did when new", web.why_paused(50, 0.79, 30, False, ()))
        self.assertNotIn("worn", web.why_paused(50, 0.8, 30, False, ()))
        # ... but only a health that was read: a guess of 79% is not called worn
        self.assertEqual(web.lock_story(50, 0.79, 30, False, (), health_known=False), "Nothing paused.")
        self.assertNotIn("worn", web.why_paused(50, 0.79, 30, False, (), health_known=False))


if __name__ == "__main__":
    unittest.main()


class ForegroundDoesNotFreeRulePauses(unittest.TestCase):
    """Funding the app in use first must not re-allow another task the battery rules pause (SAVER)."""

    TASKS = DEFAULT_TASKS[:2] + (
        Task("small", Priority.USEFUL, 3), Task("big", Priority.USEFUL, 8),
        Task("optA", Priority.OPTIONAL, 3), Task("optB", Priority.OPTIONAL, 4))

    def step(self, in_use):
        rt = Runtime(tasks=self.TASKS)
        # Usable charge 40 puts the battery rules in SAVER (optionals paused); the goal leaves
        # 12 %/h spare, which funds optB once optA is funded first as the app in use.
        return rt.step(0, 40.0, health=1.0, temp_c=25.0, goal_hours=40 / 18.17, in_use=in_use)

    def test_second_optional_stays_paused_when_another_is_in_use(self):
        plain = self.step(None).decision
        self.assertIn("optA", plain.deferred)
        self.assertIn("optB", plain.deferred)
        tick = self.step("optA")
        self.assertIn("optA", tick.decision.allowed)  # the owner's own app runs
        self.assertIn("optB", tick.decision.deferred)
        self.assertNotIn("optB", tick.decision.allowed)
