# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import re
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from unittest.mock import patch

import opos.muse as muse
from opos import web
from opos.web import Phone
from src.opos import web as srv


class TestWebMuse(unittest.TestCase):
    @patch("opos.assist.free_mb", return_value=None)
    def test_visible_has_engine(self, _free_mb):
        p = Phone(visible=True)
        s = p.state()
        self.assertIn("helper", s)
        self.assertIn("engine", s["helper"])
        self.assertEqual(s["helper"]["engine"], "Muse Glimmer, on a local server")

    def test_quiet_no_muse_or_engine(self):
        p = Phone(visible=False)
        s = p.state()
        j = json.dumps(s)
        self.assertNotIn("Muse", j)
        self.assertNotIn("engine", j)

    @patch("opos.assist.free_mb", return_value=None)
    def test_explain_patched(self, _free_mb):
        p = Phone(visible=True)
        req = p.explain_request()
        self.assertIsInstance(req, tuple)
        self.assertEqual(len(req), 2)
        self.assertEqual(muse.label(req[1]), "Muse Glimmer, on a local server")

    def test_explain_hidden(self):
        p = Phone(visible=False)
        self.assertIsNone(p.explain_request())

    def test_explain_lock_free(self):
        self.assertIn("explain", web._RATE_ACTIONS)
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        evt = threading.Event()
        def fake(*args, **kwargs):
            evt.wait(timeout=10)
            return "ok"
        result = {}
        def do_post():
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api", data=json.dumps({"action": "explain"}).encode(), headers={"Content-Type": "application/json", "X-OPOS-Token": token}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                result["status"] = r.status
        try:
            with patch.object(muse, "ask", side_effect=fake):
                pt = threading.Thread(target=do_post, daemon=True)
                pt.start()
                time.sleep(0.3)
                t0 = time.monotonic()
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-OPOS-Token": token})
                with urllib.request.urlopen(req, timeout=5) as r:
                    s = r.status
                dt = time.monotonic() - t0
                self.assertEqual(s, 200)
                self.assertLess(dt, 2)
                evt.set()
                pt.join(timeout=5)
                self.assertEqual(result.get("status"), 200)
        finally:
            server.shutdown()
            server.server_close()


class TestWebMuseHelperCall(unittest.TestCase):
    def test_helper_call_page(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                page = r.read().decode()
            for needle in ["async function helperCall", "opos_helper_s", "Thinking... about", "Still thinking...", "helpercancel", "helperwait", "AbortController", "setInterval"]:
                self.assertIn(needle, page)
            self.assertGreaterEqual(page.count("helperCall("), 4)
            self.assertEqual(page.count("Cancelled. Nothing changed on the phone."), 1)
            self.assertIsNone(re.search(r"\b(AI|model|governor|assistant)\b", page, re.I))
        finally:
            server.shutdown()
            server.server_close()


class TestWebMuseHelperReset(unittest.TestCase):
    def test_helper_reset_markup(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                page = r.read().decode()
            for needle in ['id="helperreset"', 'id="helperrestart"', 'function helperReset', 'Ready.']:
                self.assertIn(needle, page)
        finally:
            server.shutdown()
            server.server_close()

    def test_restart_helper_without_invocation_id(self):
        if "INVOCATION_ID" in os.environ:
            del os.environ["INVOCATION_ID"]
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                with unittest.mock.patch.object(web, "_restart_service") as mock_restart:
                    with unittest.mock.patch.object(srv, "_restart_service", mock_restart):
                        req = urllib.request.Request(f"http://127.0.0.1:{port}/api", data=json.dumps({"action": "restart_helper"}).encode(), headers={"Content-Type": "application/json", "X-OPOS-Token": token}, method="POST")
                        with urllib.request.urlopen(req, timeout=5) as r:
                            body = r.read().decode()
                        data = json.loads(body)
                        self.assertEqual(data.get("error"), "Restart works only when OPOS runs as a service.")
                        mock_restart.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()

    def test_restart_helper_with_invocation_id(self):
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        token = server.RequestHandlerClass.token
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            with unittest.mock.patch.dict(os.environ, {"INVOCATION_ID": "test-id-123"}, clear=False):
                with unittest.mock.patch.object(web, "_restart_service") as mock_restart:
                    with unittest.mock.patch.object(srv, "_restart_service", mock_restart):
                        req = urllib.request.Request(f"http://127.0.0.1:{port}/api", data=json.dumps({"action": "restart_helper"}).encode(), headers={"Content-Type": "application/json", "X-OPOS-Token": token}, method="POST")
                        with urllib.request.urlopen(req, timeout=5) as r:
                            body = r.read().decode()
                        data = json.loads(body)
                        self.assertTrue(data.get("ok"))
                        self.assertEqual(data.get("text"), "Restarting the helper...")
                        mock_restart.assert_called_once()
        finally:
            server.shutdown()
            server.server_close()

    def test_restart_helper_missing_token(self):
        if "INVOCATION_ID" in os.environ:
            del os.environ["INVOCATION_ID"]
        phone = Phone(visible=True)
        server = srv.make_server(0, phone)
        port = server.server_address[1]
        thr = threading.Thread(target=server.serve_forever, daemon=True)
        thr.start()
        try:
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                with unittest.mock.patch.object(web, "_restart_service") as mock_restart:
                    with unittest.mock.patch.object(srv, "_restart_service", mock_restart):
                        req = urllib.request.Request(f"http://127.0.0.1:{port}/api", data=json.dumps({"action": "restart_helper"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
                        with self.assertRaises(urllib.error.HTTPError) as cm:
                            urllib.request.urlopen(req, timeout=5)
                        self.assertEqual(cm.exception.code, 403)
                        mock_restart.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()


class TestLaneCoreRegressions(unittest.TestCase):
    WHY_30 = "The battery is worn (60% health), so 30% charge is about 18% usable and it is not charging; to keep calls and messages working, the phone paused email, maps and photos backup."

    @patch("opos.assist.free_mb", return_value=None)
    def test_undo_and_fractional_usable_reach_the_helper_facts(self, _free_mb):
        # Review: Undo left "maps" in why_paused, and 14.7% usable was published as 15 (helper stayed awake).
        p = Phone(visible=True)
        self.assertIn("maps", p.state()["helper"]["paused"])
        p.dash.undo("maps")
        h = p.state()["helper"]
        self.assertNotIn("maps", h["paused"])
        self.assertIn("maps", h["allowed"])
        self.assertNotIn("maps", h["why_paused"])
        p.set_battery(24.5)
        self.assertEqual(p.state()["helper"]["usable_percent"], 14)

    @patch("opos.assist.free_mb", return_value=None)
    def test_demo_starts_in_saver_with_a_helper_that_answers(self, _free_mb):
        # Regression (lane A): at 5% the mode was CRITICAL, the helper rested and a judge never reached the model.
        p = Phone(visible=True)
        s = p.state()
        self.assertEqual(p.sim.level, 60.0)
        self.assertEqual(s["helper"]["mode"], "SAVER")
        self.assertEqual(s["battery"]["percent"], 60)
        for name in ("calls", "messages"):
            self.assertIn(name, s["helper"]["allowed"])
        for name in ("maps", "photos backup"):
            self.assertIn(name, s["helper"]["paused"])
        facts = p.ask_facts()
        self.assertNotEqual(facts.get("mode"), "CRITICAL")
        seen = []
        def fake_ask(route, prompt, **kw):
            seen.append(prompt)
            return "Maps and photos backup rest so calls last."
        with patch.object(muse, "ask", side_effect=fake_ask):
            from opos import assist
            res = assist.answer("What is paused?", env={}, free=None, context=facts)
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(seen), 1)

    def test_helper_facts_include_battery_numbers_and_why_paused(self):
        # Regression (lane B): "Why is email paused?" answered "I do not know" without these facts.
        p = Phone(visible=True)
        p.set_battery(30)
        h = p.state()["helper"]
        self.assertEqual(h["battery_percent"], 30)
        self.assertEqual(h["battery_health_percent"], 60)
        self.assertEqual(h["usable_percent"], 18)
        self.assertEqual(h["temp_c"], 30.0)
        self.assertIs(h["charging"], False)
        self.assertEqual(h["why_paused"], self.WHY_30)
        for word in ("router", "deferred", "mode", "governor"):
            self.assertNotIn(word, h["why_paused"].lower())
        facts = p.ask_facts()
        for key in ("battery_percent", "battery_health_percent", "usable_percent", "temp_c", "charging", "why_paused"):
            self.assertIn(key, facts)
        self.assertEqual(facts["why_paused"], self.WHY_30)
        self.assertNotIn("source", facts)
        self.assertNotIn("engine", facts)

    def test_why_paused_mentions_heat_and_charging(self):
        self.assertEqual(web.why_paused(30.0, 0.6, 30.0, False, ["email", "maps", "photos backup"]), self.WHY_30)
        hot = web.why_paused(30.0, 0.6, 46.0, False, ["email"])
        self.assertIn("hot (46°C)", hot)
        self.assertIn("not charging", hot)
        charging = web.why_paused(30.0, 0.6, 30.0, True, ["photos backup"])
        self.assertIn(" it is charging;", charging)
        self.assertNotIn("not charging", charging)
        self.assertTrue(charging.endswith("the phone paused photos backup."))
        healthy = web.why_paused(80.0, 1.0, 25.0, False, [])
        self.assertTrue(healthy.startswith("The battery is at 80% (100% health, about 80% usable)"))
        self.assertIn("nothing is paused", healthy)
        self.assertEqual(web.why_paused(30.0, 0.6, 30.0, False, ["maps"]).split("paused ")[-1], "maps.")
        goal = web.why_paused(60.0, 0.6, 30.0, False, ["maps"], "23:00")
        self.assertIn("to keep calls and messages working until 23:00, the phone paused maps.", goal)

    def test_hidden_helper_exposes_no_helper_facts(self):
        p = Phone(visible=False)
        p.set_battery(30)
        s = p.state()
        self.assertNotIn("helper", s)
        self.assertEqual(p.ask_facts(), {})
        j = json.dumps(s)
        for needle in ("why_paused", "usable", "worn (", "battery_health"):
            self.assertNotIn(needle, j)

    def test_state_exposes_sim_temperature_and_charging(self):
        # Regression (lane F): the demo page always showed temp 30 and Charging unchecked after a reload.
        p = Phone()
        s = p.state()
        self.assertEqual(s["temp_c"], 30.0)
        self.assertIs(s["charging"], False)
        p.set_temp(41.5)
        p.charging = True
        s = p.state()
        self.assertEqual(s["temp_c"], 41.5)
        self.assertIs(s["charging"], True)
        self.assertEqual(json.loads(json.dumps(s))["charging"], True)

    @patch("opos.assist.free_mb", return_value=None)
    def test_why_and_plan_requests_carry_ordered_routes(self, _free_mb):
        # Regression (lane D): the handler now hands muse.explain / muse.plan the whole route order.
        p = Phone(visible=True)
        facts, routes = p.explain_routes()
        self.assertEqual(facts, p.explain_request()[0])
        self.assertIsInstance(routes, list)
        self.assertEqual(routes[0], p.explain_request()[1])
        self.assertEqual([r.key for r in routes], ["glimmer"])
        pf, steps, proutes = p.plan_routes()
        self.assertEqual(proutes, routes)
        self.assertEqual(steps, p.plan_request()[1])
        self.assertIsNone(Phone(visible=False).explain_routes())
        self.assertIsNone(Phone(visible=False).plan_routes())


if __name__ == "__main__":
    unittest.main()
