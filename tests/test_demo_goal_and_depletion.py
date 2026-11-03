# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest import mock
import opos.web as web
import opos.muse as muse
import opos.setup.config as setup_config


class TestDemoGoalAndDepletion(unittest.TestCase):
    def test_main_demo_goal(self):
        with mock.patch.object(setup_config, "load", return_value=None):
            with mock.patch.object(web, "_route_with_glimmer"):
                captured = {}
                def fake_make_server(port, phone, host="127.0.0.1", token=None):
                    captured["phone"] = phone
                    srv = mock.Mock()
                    srv.serve_forever = mock.Mock()
                    return srv
                with mock.patch.object(web, "make_server", side_effect=fake_make_server):
                    web.main([])
                phone = captured["phone"]
                self.assertEqual(phone.dash.goal, "16:00")
                self.assertEqual(phone.dash.hours_to_goal(phone.clock_s), 4.0)

    def test_main_saved_config(self):
        for name, cfg_goal, exp_goal in [
            ("enabled", {"time": "18:30", "enabled": True}, "18:30"),
            ("disabled", {"time": "18:30", "enabled": False}, None),
        ]:
            with self.subTest(name=name):
                cfg = setup_config.default_config()
                cfg["goal"] = cfg_goal
                with mock.patch.object(setup_config, "load", return_value=cfg):
                    with mock.patch.object(web, "_route_with_glimmer"):
                        captured = {}
                        def fake_make_server(port, phone, host="127.0.0.1", token=None):
                            captured["phone"] = phone
                            srv = mock.Mock(); srv.serve_forever = mock.Mock(); return srv
                        with mock.patch.object(web, "make_server", side_effect=fake_make_server):
                            web.main([])
                        self.assertEqual(captured["phone"].dash.goal, exp_goal)
        with mock.patch.object(setup_config, "load", side_effect=setup_config.ConfigError("bad")):
            with mock.patch.object(web, "_route_with_glimmer"):
                captured = {}
                def fake_make_server(port, phone, host="127.0.0.1", token=None):
                    captured["phone"] = phone
                    srv = mock.Mock(); srv.serve_forever = mock.Mock(); return srv
                with mock.patch.object(web, "make_server", side_effect=fake_make_server):
                    web.main([])
                self.assertEqual(captured["phone"].dash.goal, "23:00")

    def test_helper_facts_goal_keys(self):
        with mock.patch.object(web.assist, "free_mb", return_value=4096):
            with mock.patch.object(web.prefs, "load", side_effect=OSError):
                phone = web.Phone(visible=True)
                phone.dash.set_goal("16:00")
                state = phone.state()
                helper = state["helper"]
                self.assertEqual(helper["mode"], "SAVER")
                self.assertIn("email", helper["allowed"])
                self.assertNotIn("email", helper["paused"])
                self.assertEqual(helper["goal"], "16:00")
                self.assertTrue(helper["goal_met"])
                self.assertEqual(helper["time_left"], state["battery"]["time_left"])
                facts = muse.facts(helper)
                for k in ("goal", "goal_met", "time_left"):
                    self.assertIn(k, facts)

    def test_unmet_goal_reaches_why_paused_fact(self):
        # 20% on a worn battery at noon cannot reach 23:00, so the fact must not promise it.
        with mock.patch.object(web.assist, "free_mb", return_value=4096):
            with mock.patch.object(web.prefs, "load", side_effect=OSError):
                phone = web.Phone(visible=True)
                phone.dash.set_goal("23:00")
                phone.set_battery(20)
                state = phone.state()
                helper = state["helper"]
                self.assertIs(helper["goal_met"], False)
                self.assertTrue(helper["paused"])
                why = helper["why_paused"]
                self.assertIn("; to stretch the battery toward 23:00, the phone paused ", why)
                self.assertTrue(why.endswith(", but at this rate the battery shows " + state["battery"]["time_left"].lower() + "."))
                self.assertNotIn("working until", why)

    def test_why_paused_wording(self):
        msg = web.why_paused(30.0, 0.6, 30.0, False, ["maps"], "16:00", goal_met=False, time_left="About 2 h 5 min left")
        self.assertEqual(msg, "The battery is worn (60% health), so 30% charge is about 18% usable and it is not charging; to stretch the battery toward 16:00, the phone paused maps, but at this rate the battery shows about 2 h 5 min left.")
        for status, notice in (("error: battery empty, charge the phone to call", "Charge the phone to call"),
                               ("error: battery empty, charge the phone to send", "Charge the phone to send"),
                               ("call ended: battery empty", "Call ended. Charge the phone")):
            self.assertEqual(web.notice_for(status, False), notice)
            self.assertIsNone(web._NOTICE_FORBIDDEN_RE.search(notice))
        empty = web.why_paused(1.0, 0.6, 30.0, False, ["maps"], "16:00", goal_met=False, time_left="Almost empty")
        self.assertTrue(empty.endswith(", but at this rate the battery shows almost empty."))
        ok = web.why_paused(30.0, 0.6, 30.0, False, ["maps"], "16:00", goal_met=True)
        self.assertIn("to keep calls and messages working until 16:00", ok)
        none_goal = web.why_paused(30.0, 0.6, 30.0, False, ["maps"], None, goal_met=False)
        self.assertIn("to keep calls and messages working, the phone paused maps.", none_goal)
        charging = web.why_paused(30.0, 0.6, 30.0, True, ["maps"], "16:00", goal_met=False, time_left="Charging")
        self.assertTrue(charging.endswith("to stretch the battery toward 16:00, the phone paused maps."))
        self.assertNotIn("last only", charging)

    def test_depletion_call_send(self):
        with mock.patch.object(web.assist, "free_mb", return_value=4096):
            with mock.patch.object(web.prefs, "load", side_effect=OSError):
                phone = web.Phone()
                phone.set_battery(0)
                log_before = list(phone.essentials.log)
                phone.call("5551234")
                self.assertEqual(phone.status, "error: battery empty, charge the phone to call")
                self.assertFalse(phone._in_call)
                phone.send("5551234", "hi")
                self.assertEqual(phone.status, "error: battery empty, charge the phone to send")
                self.assertEqual(list(phone.essentials.log), log_before)
                phone.set_battery(50)
                phone.call("5551234")
                self.assertEqual(phone.status, "in call")

    def test_active_call_ends_at_zero(self):
        with mock.patch.object(web.assist, "free_mb", return_value=4096):
            with mock.patch.object(web.prefs, "load", side_effect=OSError):
                phone = web.Phone()
                phone.set_battery(50)
                phone.call("5551234")
                self.assertTrue(phone._in_call)
                phone.set_battery(0)
                phone.tick(1)
                self.assertFalse(phone._in_call)
                self.assertFalse(phone.essentials.in_call)
                self.assertEqual(phone.status, "call ended: battery empty")
                phone2 = web.Phone()
                phone2.running = True
                phone2.set_battery(50)
                phone2.call("5551234")
                phone2.set_battery(0)
                phone2.tick(1)
                self.assertEqual(phone2.status, "call ended: battery empty")

    def test_charging_keeps_call(self):
        import time as _time
        with mock.patch.object(web.assist, "free_mb", return_value=4096):
            with mock.patch.object(web.prefs, "load", side_effect=OSError):
                phone = web.Phone()
                phone.set_battery(50)
                phone.call("5551234")
                phone.charging = True
                phone.set_battery(0)
                phone._last_tick_time = _time.monotonic() - 1
                phone.tick(1)
                self.assertGreater(phone.sim.level, 0)
                self.assertTrue(phone._in_call)


if __name__ == "__main__":
    unittest.main()
