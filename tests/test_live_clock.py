# SPDX-License-Identifier: GPL-3.0-or-later
"""A live phone whose clock nobody set says so and claims no goal or arrival time against it."""
import calendar
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from opos import web
from opos.dashboard import Dashboard
from opos.governor import Mode
from opos.hw import live_battery as lb
from opos.runtime import DEFAULT_TASKS
from opos.web import Phone, clock_ok

NOON = 12 * 3600


def at(*ymdhms):
    return calendar.timegm(ymdhms + (0,) * (6 - len(ymdhms)))


def battery(level):
    reading = {"level": level, "temp_c": 29.0, "charging": False, "source": "sysfs", "health": 1.0, "health_source": "sysfs"}
    return lb.LiveBattery(reader=lambda: reading, clock=lambda: 0.0)


class ClockOkTests(unittest.TestCase):
    def test_the_year_is_the_floor(self):
        self.assertFalse(clock_ok(0))
        self.assertFalse(clock_ok(at(1970, 1, 1, 0, 3)))
        self.assertFalse(clock_ok(at(2025, 12, 31, 23, 59, 59)))
        self.assertTrue(clock_ok(at(2026, 1, 1)))
        self.assertTrue(clock_ok(at(2026, 11, 7, 12)))
        self.assertTrue(clock_ok(at(2031, 6, 1)))

    def test_a_raised_clock_is_a_guess_until_network_time(self):
        with tempfile.TemporaryDirectory() as d:
            guess, synced = os.path.join(d, "clock-floor"), os.path.join(d, "synchronized")
            now = at(2026, 11, 8, 9)
            self.assertTrue(clock_ok(now, guess, synced))
            open(guess, "w").close()
            self.assertFalse(clock_ok(now, guess, synced))
            open(synced, "w").close()
            self.assertTrue(clock_ok(now, guess, synced))

    def test_network_time_does_not_excuse_a_1970_clock(self):
        with tempfile.TemporaryDirectory() as d:
            synced = os.path.join(d, "synchronized")
            open(synced, "w").close()
            self.assertFalse(clock_ok(0, os.path.join(d, "none"), synced))

    def test_the_real_clock_of_this_machine_is_read_by_default(self):
        with mock.patch.object(web.time, "time", return_value=at(1970, 1, 1, 0, 3)):
            self.assertFalse(clock_ok(guess="/nonexistent/g", synced="/nonexistent/s"))
        with mock.patch.object(web.time, "time", return_value=at(2026, 11, 7, 12)):
            self.assertTrue(clock_ok(guess="/nonexistent/g", synced="/nonexistent/s"))


class LivePhoneClockTests(unittest.TestCase):
    def setUp(self):
        self.ok = True
        self.now = NOON
        for p in (mock.patch.object(web, "_local_clock_s", side_effect=lambda: self.now),
                  mock.patch.object(web, "clock_ok", side_effect=lambda *a, **k: self.ok)):
            p.start()
            self.addCleanup(p.stop)

    def phone(self, ok, level=3, goal="23:00", visible=False):
        self.ok = ok
        phone = Phone(visible=visible, live_battery=battery(level))
        phone.dash.set_goal(goal, phone._goal_clock())
        return phone

    def test_a_trusted_clock_gives_the_arrival_time_as_before(self):
        s = self.phone(True).state()
        self.assertIs(s["battery"]["clock_ok"], True)
        self.assertRegex(s["battery"]["goal_note"], r"^Won't last until 11 PM, calls and messages until ~\d{1,2}:\d{2} (AM|PM)$")
        self.assertRegex(s["battery"]["calls_until"], r"^\d{1,2}:\d{2} (AM|PM)$")
        self.assertEqual(s["clock"], "12:00")
        self.assertIn("11 PM", s["lock_story"])

    def test_a_guessed_clock_says_so_and_claims_no_arrival_time(self):
        s = self.phone(False).state()
        b = s["battery"]
        self.assertIs(b["clock_ok"], False)
        self.assertEqual(b["calls_until"], "")
        self.assertEqual(b["goal_note"], "Clock not set, so the goal time is not used")
        self.assertNotIn("11 PM", s["lock_story"])
        self.assertNotIn("unlikely", s["lock_story"])
        self.assertEqual(s["clock"], "--:--")
        self.assertIsNotNone(b["hours_left"])  # a duration needs no clock

    def test_no_goal_no_note(self):
        b = self.phone(False, goal="").state()["battery"]
        self.assertIs(b["clock_ok"], False)
        self.assertEqual(b["goal_note"], "")

    def test_the_helper_hears_no_goal_against_a_guessed_clock(self):
        h = self.phone(False, visible=True).state()["helper"]
        self.assertEqual(h["goal"], "")
        self.assertNotIn("23:00", h["why_paused"])
        h = self.phone(True, visible=True).state()["helper"]
        self.assertEqual(h["goal"], "23:00")
        self.assertIn("23:00", h["why_paused"])

    def test_the_visible_why_does_not_say_no_goal_when_there_is_one(self):
        why = self.phone(False, visible=True).state()["battery"]["why"]
        self.assertTrue(why.endswith("Clock not set"), why)
        self.assertNotIn("No goal set", why)

    def test_the_governor_gets_no_goal_hours_against_a_guessed_clock(self):
        for ok, want_goal in ((False, False), (True, True)):
            phone = self.phone(ok)
            seen = []
            step = phone.runtime.step
            phone.runtime.step = lambda *a, **k: (seen.append(k.get("goal_hours")), step(*a, **k))[1]
            phone.state()
            self.assertTrue(seen)
            self.assertEqual(all(h is not None for h in seen), want_goal, (ok, seen))

    def test_the_goal_comes_back_when_the_clock_is_set(self):
        phone = self.phone(False)
        self.assertEqual(phone.state()["battery"]["calls_until"], "")
        self.ok = True
        b = phone.state()["battery"]
        self.assertTrue(b["calls_until"])
        self.assertTrue(b["goal_note"].startswith("Won't last until 11 PM"))

    def test_a_goal_set_over_the_api_while_the_clock_is_a_guess_counts_as_set_before_its_time(self):
        phone = self.phone(False, goal="")
        # 11:00 is an hour before the guessed noon: read against that guess it would sit inside its own grace window.
        self.assertTrue(web.apply_phone_action(phone, {"action": "goal", "time": "11:00"}))
        self.assertEqual(phone.dash.goal, "11:00")
        self.assertTrue(phone.dash._goal_armed, "the guessed clock was read for the goal")
        b = phone.state()["battery"]
        self.assertEqual(b["calls_until"], "")
        self.assertEqual(b["goal_note"], "Clock not set, so the goal time is not used")

    def test_a_goal_set_over_the_api_while_the_clock_is_trusted_is_read_against_it(self):
        phone = self.phone(True, goal="")
        self.assertTrue(web.apply_phone_action(phone, {"action": "goal", "time": "11:00"}))
        self.assertFalse(phone.dash._goal_armed, "set an hour after its time, it means the next one")

    def test_the_helper_request_uses_no_goal_hours_against_a_guessed_clock(self):
        real = web.muse.is_hard
        for ok in (False, True):
            phone = self.phone(ok, visible=True)
            seen = []
            with mock.patch.object(web.muse, "is_hard", side_effect=lambda goal, left: (seen.append(goal), real(goal, left))[1]):
                phone._helper_request()
            self.assertEqual(len(seen), 1)
            if ok:
                self.assertAlmostEqual(seen[0], 11.0)  # noon to the 23:00 goal
            else:
                self.assertIsNone(seen[0])

    def test_the_goal_is_judged_again_when_the_clock_goes_from_a_guess_to_trusted(self):
        # The goal was set under a guess (as main() and the API do), and the clock is then set to 23:30, an hour and a
        # half after the goal time: the goal must mean the next 22:00, not "Made it".
        phone = self.phone(False, goal="")
        self.assertTrue(web.apply_phone_action(phone, {"action": "goal", "time": "22:00"}))
        self.assertTrue(phone.dash._goal_armed)
        self.assertEqual(phone.state()["battery"]["calls_until"], "")
        self.ok, self.now = True, 23 * 3600 + 30 * 60
        seen = []
        step = phone.runtime.step
        phone.runtime.step = lambda *a, **k: (seen.append(k.get("goal_hours")), step(*a, **k))[1]
        b = phone.state()["battery"]
        self.assertNotIn("Made it", b["goal_note"])
        self.assertRegex(b["goal_note"], r"^Won't last until 10 PM, calls and messages until ~\d{1,2}:\d{2} (AM|PM)$")
        self.assertFalse(b["goal_met"])
        self.assertRegex(b["calls_until"], r"^\d{1,2}:\d{2} (AM|PM)$")
        self.assertTrue(seen)
        self.assertTrue(all(h is not None and abs(h - 22.5) < 1e-6 for h in seen), seen)  # 23:30 to the next 22:00

    def test_a_goal_set_under_a_guess_is_reached_when_the_clock_turns_out_to_be_before_it(self):
        phone = self.phone(False, goal="")
        self.assertTrue(web.apply_phone_action(phone, {"action": "goal", "time": "22:00"}))
        phone.state()
        self.ok, self.now = True, NOON
        self.assertNotIn("Made it", phone.state()["battery"]["goal_note"])
        self.now = 22 * 3600 + 30 * 60  # judged again once, not on every tick: the goal can still be reached
        b = phone.state()["battery"]
        self.assertTrue(b["goal_note"].startswith("Made it to 10 PM"), b["goal_note"])
        self.assertTrue(b["goal_met"])

    def test_a_clock_that_was_trusted_all_along_is_not_judged_again(self):
        phone = self.phone(True, goal="22:00")  # noon, ten hours before the goal: armed
        self.assertTrue(phone.dash._goal_armed)
        self.now = 22 * 3600 + 30 * 60
        self.assertTrue(phone.state()["battery"]["goal_note"].startswith("Made it to 10 PM"))

    def test_the_clock_turning_trusted_with_no_goal_sets_none(self):
        phone = self.phone(False, goal="")
        phone.state()
        self.ok = True
        b = phone.state()["battery"]
        self.assertIs(phone.dash.goal, None)
        self.assertEqual(b["goal_note"], "")
        self.assertEqual(b["goal"], "")

    def test_main_arms_the_saved_goal_against_no_clock_while_the_clock_is_a_guess(self):
        # 11:00 is an hour before the guessed noon: read against that guess it would sit inside its own grace window.
        for ok, armed in ((False, True), (True, False)):
            phone, err = self.run_main(ok, {"enabled": True, "time": "11:00"})
            self.assertEqual(phone.dash.goal, "11:00")
            self.assertIs(phone.dash._goal_armed, armed, ok)
            self.assertEqual("clock is not set" in err, not ok, err)
        phone, _ = self.run_main(False, {"enabled": False, "time": "11:00"})
        self.assertIsNone(phone.dash.goal)

    def run_main(self, ok, goal):
        """main() with a live battery, a fake saved config and a server that returns at once; the Phone it built and stderr."""
        self.ok = ok
        built = []
        server = mock.Mock()
        server.serve_forever.side_effect = lambda: None
        def make(port, phone, **kw):
            built.append(phone)
            return server
        err = io.StringIO()
        live = battery(50)  # built before LiveBattery is replaced below
        with mock.patch.dict(os.environ, {"OPOS_BATTERY": "live", "OPOS_GLIMMER_ROUTING": "0"}), \
                mock.patch.object(lb, "LiveBattery", return_value=live), \
                mock.patch.object(web.setup_config, "load", return_value={"goal": goal}), \
                mock.patch.object(web, "_install_helper_visible", return_value=False), \
                mock.patch.object(web, "make_server", side_effect=make), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            web.main(["--port", "0"])
        self.assertEqual(len(built), 1)
        return built[0], err.getvalue()

    def test_the_clock_is_read_at_each_state(self):
        phone = self.phone(True)
        self.assertTrue(phone.clock_ok)
        self.ok = False
        phone.state()
        self.assertFalse(phone.clock_ok)


class DemoUnchangedTests(unittest.TestCase):
    def test_the_demo_phone_has_its_own_clock_and_no_clock_key(self):
        phone = Phone()
        s = phone.state()
        self.assertTrue(phone.clock_ok)
        self.assertEqual(s["clock"], "12:00")
        self.assertNotIn("clock_ok", s["battery"])
        self.assertIs(phone._goal_clock(), phone.clock_s)

    def test_the_demo_ignores_the_machines_clock(self):
        with mock.patch.object(web, "clock_ok", return_value=False):
            phone = Phone()
            s = phone.state()
        self.assertEqual(s["clock"], "12:00")
        self.assertNotIn("clock_ok", s["battery"])
        self.assertNotEqual(s["battery"]["goal_note"], "Clock not set, so the goal time is not used")


class DashboardWithoutClockTests(unittest.TestCase):
    def view(self, goal):
        d = Dashboard(DEFAULT_TASKS)
        d.set_goal(goal)
        return d.view(50, Mode.NORMAL, False, 1.0, None, True, "reason")

    def test_a_goal_with_no_clock_reads_clock_not_set(self):
        self.assertEqual(self.view("23:00")["why"], "reason; Clock not set")

    def test_no_goal_still_reads_no_goal_set(self):
        self.assertEqual(self.view("")["why"], "reason; No goal set")


if __name__ == "__main__":
    unittest.main()
