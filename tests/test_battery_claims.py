# SPDX-License-Identifier: GPL-3.0-or-later
"""W1-07: the battery claims are true (health nobody read, a one-point graph, a goal nobody set, a modelled time left)."""
import re
import unittest
from pathlib import Path
from unittest import mock

import opos.hw.live_battery as live_battery
from opos import web
from opos.dashboard import ROUGH, Dashboard
from opos.hw.live_battery import LiveBattery
from opos.web import Phone, lock_story, why_paused

STATIC = Path(__file__).resolve().parents[1] / "src/opos/web_static"
NOON = 12 * 3600


def live_phone(level=55.0, health=1.0, source="assumed", charging=False, clock_s=NOON + 2 * 3600 + 600, visible=True):
    """A Duo-like phone: a sysfs reader whose health is `source`, a steerable wall clock and a steerable monotonic clock."""
    box = {"level": level, "t": 0.0, "clock_s": clock_s}
    reader = lambda: {"level": box["level"], "temp_c": None, "charging": charging, "source": "sysfs", "health": health, "health_source": source}
    lb = LiveBattery(reader=reader, clock=lambda: box["t"], refresh_s=0)
    with mock.patch.object(web, "_local_clock_s", lambda: box["clock_s"]), mock.patch.object(web.assist, "free_mb", return_value=4096), \
            mock.patch.object(web.prefs, "load", side_effect=OSError):
        phone = Phone(visible=visible, live_battery=lb)
    phone.box = box
    return phone


def state_of(phone):
    box = getattr(phone, "box", {"clock_s": NOON})
    with mock.patch.object(web, "_local_clock_s", lambda: box["clock_s"]), mock.patch.object(web.assist, "free_mb", return_value=4096), \
            mock.patch.object(web.prefs, "load", side_effect=OSError):
        return phone.state()


class HealthIsOnlySaidWhenRead(unittest.TestCase):
    def test_an_assumed_health_is_never_printed(self):
        phone = live_phone(level=90.0)  # nothing is paused at 90%: the story is just that
        phone.dash.set_goal(None)
        s = state_of(phone)
        self.assertEqual(s["battery_source"]["health_source"], "assumed")
        self.assertEqual(s["lock_story"], "Nothing paused.")
        why = s["helper"]["why_paused"]
        self.assertTrue(why.startswith("The battery is at 90% and it is not charging"), why)
        self.assertNotRegex(why, r"(?i)health|usable|worn")
        self.assertNotIn("battery_health_percent", s["helper"])

    def test_an_assumed_health_never_makes_the_battery_worn(self):
        phone = live_phone(level=55.0, health=0.5)  # a reader that guessed low: still a guess
        s = state_of(phone)
        self.assertNotIn("Worn", s["lock_story"])
        self.assertNotRegex(s["helper"]["why_paused"], r"(?i)worn|health|holds")

    def test_a_measured_worn_health_is_said_plainly(self):
        phone = live_phone(level=55.0, health=0.6, source="charge-counter")
        s = state_of(phone)
        self.assertTrue(s["lock_story"].startswith("Worn battery."), s["lock_story"])
        why = s["helper"]["why_paused"]
        self.assertTrue(why.startswith("The battery is worn. It holds 60% of what it did when new, so 55% is really about 33%"), why)
        self.assertEqual(s["helper"]["battery_health_percent"], 60)

    def test_a_measured_healthy_battery_says_nothing_about_health(self):
        phone = live_phone(level=72.0, health=0.82, source="charge-counter")
        s = state_of(phone)
        self.assertNotRegex(s["helper"]["why_paused"], r"(?i)health|usable|worn")
        self.assertNotIn("Worn", s["lock_story"])

    def test_the_demo_battery_keeps_its_deliberate_sixty_percent_health(self):
        s = Phone(visible=True).state()
        self.assertNotIn("battery_source", s)
        self.assertTrue(s["lock_story"].startswith("Worn battery."))
        self.assertIn("It holds 60% of what it did when new", s["helper"]["why_paused"])

    def test_functions_directly(self):
        self.assertEqual(lock_story(55, 1.0, 30, False, [], None, True, health_known=False), "Nothing paused.")
        self.assertEqual(lock_story(55, 0.5, 30, False, [], None, True, health_known=False), "Nothing paused.")
        self.assertEqual(lock_story(55, 0.5, 30, False, [], None, True), "Worn battery. Nothing paused.")
        self.assertEqual(why_paused(55, 1.0, 30, False, [], health_known=False),
                         "The battery is at 55% and it is not charging; nothing is paused, so everything is on.")
        self.assertEqual(why_paused(55, 0.6, 30, False, [], health_known=False),
                         "The battery is at 55% and it is not charging; nothing is paused, so everything is on.")
        self.assertEqual(why_paused(100, 0.6, 30, False, []),
                         "The battery is worn. It holds 60% of what it did when new, so 100% is really about 60% and it is not charging; nothing is paused, so everything is on.")
        self.assertTrue(why_paused(80, 1.0, 30, False, []).startswith("The battery is at 80% and it is not charging"))


class History(unittest.TestCase):
    def test_a_live_phone_records_a_point_every_minute_it_is_read(self):
        phone = live_phone(level=60.0)
        phone.dash.set_goal(None)
        seen = []
        for i in range(5):  # 50 minutes: inside the hour the graph keeps
            phone.box["t"] += 600
            phone.box["clock_s"] += 600
            phone.box["level"] -= 1.0
            seen.append(state_of(phone)["battery"]["history"])
        self.assertGreater(len(seen[-1]), 1)
        self.assertEqual(seen[-1], sorted(seen[-1], reverse=True), "the level only fell")
        self.assertEqual(seen[-1][0], 60.0)
        self.assertEqual(seen[-1], [60.0, 59.0, 58.0, 57.0, 56.0, 55.0])
        self.assertEqual([len(h) for h in seen], sorted(len(h) for h in seen), "the graph only grows within the hour")

    def test_a_clock_that_goes_back_restarts_the_graph_with_one_sample(self):
        # Midnight, or the clock being set back: the old reset left the same level twice, which the page drew as a flat line.
        phone = live_phone(level=70.0)
        phone.dash.set_goal(None)
        for i in range(3):
            phone.box["t"] += 600
            phone.box["clock_s"] += 600
            phone.box["level"] -= 1.0
            state_of(phone)
        phone.box["clock_s"] -= 5 * 3600
        phone.box["t"] += 600
        self.assertEqual(state_of(phone)["battery"]["history"], [67.0])
        phone.box["t"] += 600
        phone.box["clock_s"] += 600
        phone.box["level"] -= 1.0
        self.assertEqual(state_of(phone)["battery"]["history"], [67.0, 66.0])

    def test_polling_inside_one_minute_adds_nothing(self):
        phone = live_phone(level=60.0)
        a = state_of(phone)["battery"]["history"]
        b = state_of(phone)["battery"]["history"]
        self.assertEqual(a, b)

    def test_the_page_draws_no_line_for_one_point(self):
        js = (STATIC / "js-7.js").read_text(encoding="utf-8")
        self.assertNotIn("0,30 100,30", js)
        self.assertIn("hist.length>1", js)
        a = (STATIC / "phone-a.js").read_text(encoding="utf-8")
        self.assertIn("s.battery.history.length>1", a)
        self.assertNotIn("s.battery.history.length)})", a)


class Goal(unittest.TestCase):
    def run_main(self, live):
        captured = {}

        def fake_make_server(port, phone, host="127.0.0.1", token=None, **kw):
            captured["phone"] = phone
            srv = mock.Mock()
            srv.serve_forever = mock.Mock()
            srv.server_address = ("127.0.0.1", 1)
            return srv

        patches = [mock.patch.object(web.setup_config, "load", return_value=None), mock.patch.object(web, "_route_with_glimmer"),
                   mock.patch.object(web, "make_server", side_effect=fake_make_server), mock.patch.object(web.prefs, "load", side_effect=OSError)]
        if live:
            lb = LiveBattery(reader=lambda: {"level": 55.0, "temp_c": None, "charging": False, "source": "sysfs", "health": 1.0, "health_source": "assumed"},
                             clock=lambda: 0.0, refresh_s=0)
            patches.append(mock.patch.object(live_battery, "LiveBattery", lambda: lb))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        web.main(["--live-battery"] if live else [])
        return captured["phone"]

    def test_a_real_phone_has_no_goal_until_its_owner_picks_one(self):
        phone = self.run_main(live=True)
        self.assertTrue(phone.live)
        self.assertIsNone(phone.dash.goal)
        s = state_of(phone)
        self.assertEqual(s["battery"]["goal"], "")
        self.assertNotRegex(s["lock_story"], r"\bPM\b|\bAM\b|unlikely")
        self.assertNotIn("goal", s["battery"]["goal_note"].lower())

    def test_the_demo_still_starts_with_its_sixteen_hundred_goal(self):
        phone = self.run_main(live=False)
        self.assertFalse(phone.live)
        self.assertEqual(phone.dash.goal, web.DEMO_GOAL)


class TimeLeftLabel(unittest.TestCase):
    def view(self, **kw):
        from opos.runtime import Runtime
        rt = Runtime(visible=True)
        d = Dashboard(rt.tasks)
        tick = rt.step(0, 60.0, health=1.0, goal_hours=None)
        return d.view(60.0, tick.decision.mode, kw.pop("charging", False), 1.0, 43200, True, "", deferred=tick.decision.deferred,
                      steps=tick.steps, drain_per_h=kw.pop("drain_per_h", tick.drain_per_h), **kw)

    def test_a_model_figure_says_rough_estimate(self):
        v = self.view()
        self.assertTrue(v["time_left"].startswith(ROUGH + "About "), v["time_left"])
        self.assertEqual(ROUGH, "Rough estimate: ")

    def test_a_measured_figure_is_plain(self):
        v = self.view(measured=True)
        self.assertTrue(v["time_left"].startswith("About "), v["time_left"])
        self.assertNotIn("estimate", v["time_left"].lower())

    def test_charging_is_not_an_estimate(self):
        self.assertEqual(self.view(charging=True)["time_left"], "Charging")
        self.assertEqual(self.view(charging=True, measured=True)["time_left"], "Charging")

    def test_the_words_that_are_not_a_guess_are_not_labelled(self):
        for hours in (0.0001,):
            drain = 60.0 / hours
            self.assertEqual(self.view(drain_per_h=drain)["time_left"], "Almost empty")

    def test_a_live_phone_labels_until_the_drain_is_measured(self):
        phone = live_phone(level=60.0)
        phone.dash.set_goal(None)
        s = state_of(phone)
        self.assertEqual(s["battery"]["drain"]["basis"], "model")
        self.assertTrue(s["battery"]["time_left"].startswith(ROUGH))
        self.assertTrue(s["helper"]["time_left"].startswith(ROUGH))
        # ... and the helper's sentence still reads well
        mid = web.why_paused(60, 1.0, 30, False, ["maps"], "16:00", goal_met=False, time_left=s["helper"]["time_left"], health_known=False)
        self.assertNotIn("rough estimate", mid)
        self.assertTrue(mid.endswith(", but at this rate the battery shows " + s["helper"]["time_left"][len(ROUGH):].lower() + "."), mid)

    def test_a_trusted_measurement_drops_the_label(self):
        phone = live_phone(level=80.0)
        phone.dash.set_goal(None)
        for i in range(40):  # 40 minutes of steady 0.5 point per 10 minutes: the window (10 min, 0.5 point) fills
            phone.box["t"] += 120.0
            phone.box["clock_s"] += 120.0
            phone.box["level"] -= 0.1
            s = state_of(phone)
        self.assertEqual(s["battery"]["drain"]["basis"], "measured", s["battery"]["drain"])
        self.assertTrue(s["battery"]["time_left"].startswith("About "), s["battery"]["time_left"])
        self.assertFalse(s["helper"]["time_left"].startswith(ROUGH))

    def test_feature_rows_say_estimated(self):
        js = (STATIC / "js-7.js").read_text(encoding="utf-8")
        self.assertIn("% per hour, estimated", js)
        self.assertNotIn("%/h", js)


if __name__ == "__main__":
    unittest.main()
