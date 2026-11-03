# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import time
import json
import urllib.request
import urllib.error
import threading
import re
import opos.web

def _start_server(phone):
    srv = opos.web.make_server(0, phone)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv

class TestWebBattery(unittest.TestCase):
    def test_tone_boundaries(self):
        phone = opos.web.Phone()
        for lvl, tone in [(19, "low"), (20, "normal"), (100, "full")]:
            phone.set_battery(lvl)
            s = phone.state()["battery"]
            self.assertEqual(s["meter"]["tone"], tone)
            self.assertEqual(s["meter"]["width"], lvl)
        phone.charging = True
        phone.set_battery(50)
        self.assertEqual(phone.state()["battery"]["meter"]["tone"], "charging")
        phone.set_battery(100)
        self.assertEqual(phone.state()["battery"]["meter"]["tone"], "full")

    def test_plan_pauses_and_undo_increases_drain(self):
        phone = opos.web.Phone()
        phone.set_battery(30)  # 18% usable: SAVER under the battery rules, so the goal pauses are soft
        phone.dash.set_goal("23:00")
        phone.state()
        self.assertTrue(phone.paused_names())
        level = phone.sim.level
        phone._last_tick_time = time.monotonic() - 5.0
        phone.tick(5)
        drain1 = level - phone.sim.level
        for item in phone.state()["battery"]["paused"]:
            phone.undo(item["id"])
        phone.state()
        self.assertFalse(phone.paused_names())
        level2 = phone.sim.level
        phone._last_tick_time = time.monotonic() - 5.0
        phone.tick(5)
        drain2 = level2 - phone.sim.level
        self.assertGreater(drain2, drain1)

    def test_undo_has_no_effect_under_a_hard_floor(self):
        phone = opos.web.Phone()
        phone.set_battery(15)  # 9% usable: the battery rules' CRITICAL mode
        phone.dash.set_goal("23:00")
        paused = phone.state()["battery"]["paused"]
        self.assertTrue(paused)
        for item in paused:
            phone.undo(item["id"])
        self.assertEqual(phone.dash.undone, set())
        self.assertEqual(phone.state()["battery"]["paused"], paused)

    def test_http_errors_and_success(self):
        phone = opos.web.Phone()
        srv = _start_server(phone)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        base = f"http://127.0.0.1:{port}"
        def post(payload):
            token = srv.RequestHandlerClass.token
            req = urllib.request.Request(base + "/api", data=payload, headers={"Content-Type": "application/json", "X-OPOS-Token": token}, method="POST")
            return urllib.request.urlopen(req)
        try:
            post(json.dumps({"action": "tick", "real_s": -1}).encode())
            self.fail()
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            self.assertEqual(code, 400)
        try:
            post(json.dumps({"action": "undo", "name": "nonsense"}).encode())
            self.fail()
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            self.assertEqual(code, 400)
        resp = post(json.dumps({"action": "tick", "real_s": 1}).encode())
        self.assertEqual(resp.status, 200)
        try:
            post(b'{"action":"tick","real_s":NaN}')
            self.fail()
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            self.assertEqual(code, 400)

    def test_quiet_mode_no_ai_terms(self):
        phone = opos.web.Phone()
        phone.visible = False
        battery_json = json.dumps(phone.state()["battery"])
        self.assertIsNone(re.search(r"\b(ai|model|governor|assistant)\b", battery_json, re.I))

if __name__ == "__main__":
    unittest.main()
