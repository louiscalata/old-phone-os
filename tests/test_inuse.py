# SPDX-License-Identifier: GPL-3.0-or-later
import json
import threading
import unittest
import urllib.error
import urllib.request

from opos.battery import Sample
from opos.dashboard import Dashboard
from opos.governor import Mode
from opos.home import home_screen
from opos.runtime import DEFAULT_TASKS
from opos.web import Phone, make_server


def make_dash():
    return Dashboard(DEFAULT_TASKS, history_len=60)


def paused_names(out):
    return [entry["name"] for entry in out["paused"]]


def post_api(port, payload, token=None):
    url = "http://127.0.0.1:%d/api" % port
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-OPOS-Token"] = token
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, resp.read()


class InUseTest(unittest.TestCase):
    def test_set_in_use(self):
        dash = make_dash()
        dash.set_in_use("maps")
        self.assertEqual(dash.in_use, "maps")
        dash.set_in_use("")
        self.assertIsNone(dash.in_use)
        dash.set_in_use("maps")
        dash.set_in_use(None)
        self.assertIsNone(dash.in_use)
        with self.assertRaises(ValueError):
            dash.set_in_use("nope")
        with self.assertRaises(ValueError):
            dash.set_in_use(5)

    def test_view_deferred_paused(self):
        dash = make_dash()
        out = dash.view(80, Mode.NORMAL, False, 1.0, 43200.0, False, "rules", deferred=("maps",))
        self.assertIn("maps", paused_names(out))
        out2 = dash.view(80, Mode.NORMAL, False, 1.0, 43200.0, False, "rules", deferred=())
        self.assertNotIn("maps", paused_names(out2))

    def test_in_use_is_decided_by_the_final_decision_not_the_panel(self):
        dash = make_dash()
        dash.set_in_use("maps")
        # The panel shows what the final decision says, even for the app in use (a hard floor may hold it).
        out = dash.view(80, Mode.NORMAL, False, 1.0, 43200.0, False, "rules", deferred=("maps",))
        self.assertIn("maps", paused_names(out))
        self.assertNotIn("maps", out["saving_for"])
        # The runtime is what lets the app in use run over a soft pause.
        phone = Phone()
        phone.dash.set_in_use("maps")
        out = phone.state()["battery"]
        self.assertNotIn("maps", paused_names(out))
        self.assertIn("maps", out["saving_for"])

    def test_http_in_use(self):
        phone = Phone()
        server = make_server(0, phone)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        token = server.RequestHandlerClass.token
        status, _ = post_api(port, {"action": "in_use", "name": "maps"}, token)
        self.assertEqual(status, 200)
        self.assertEqual(phone.dash.in_use, "maps")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post_api(port, {"action": "in_use", "name": "nope"}, token)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(phone.dash.in_use, "maps")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post_api(port, {"action": "in_use", "name": "maps"})
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(phone.dash.in_use, "maps")

    def test_in_use_changes_next_decision(self):
        phone = Phone()
        phone.runtime.governor.advisor = None
        # 40% at full health is SAVER under the battery rules: photos backup is a soft pause.
        phone.sim.level = 40
        phone.sim.health = 1.0
        phone.sim.temp_c = 25.0
        phone.charging = False
        base = phone._tick()
        self.assertEqual(base.decision.allowed, ("calls", "messages", "email", "maps"))
        self.assertEqual(base.decision.deferred, ("photos backup",))
        phone.dash.set_in_use("photos backup")
        for _ in range(2):
            t = phone._tick()
            self.assertEqual(t.decision.allowed, ("calls", "messages", "email", "maps", "photos backup"))
            self.assertEqual(t.decision.deferred, ())
            self.assertEqual(t.screen, home_screen(phone.sim.level, t.decision, visible=phone.visible))
            self.assertIs(t, phone._tick_obj)
            self.assertEqual(t.decision.mode, base.decision.mode)
            self.assertEqual(t.decision.reason, base.decision.reason)
            self.assertEqual(t.budget, base.budget)
            self.assertEqual(t.reserve, base.reserve)
            self.assertEqual(t.hours_left, base.hours_left)
            self.assertEqual(t.steps, base.steps)
        phone.dash.set_in_use("")
        self.assertEqual(phone._tick().decision, base.decision)

    def test_in_use_cannot_override_the_critical_floor(self):
        phone = Phone()
        phone.runtime.governor.advisor = None
        phone.sim.level = 10  # 10% usable: the battery rules' CRITICAL mode, a hard floor
        phone.sim.health = 1.0
        phone.sim.temp_c = 25.0
        base = phone._tick()
        phone.dash.set_in_use("maps")
        tick = phone._tick()
        self.assertEqual(tick.decision, base.decision)
        self.assertEqual(dict(tick.causes)["maps"], "low")

    def test_in_use_promotion_rejected_by_worn_reserve(self):
        phone = Phone()
        phone.runtime.governor.advisor = None
        phone.sim.level = 10
        phone.sim.health = 0.35
        phone.sim.temp_c = 25.0
        phone.charging = False
        base = phone._tick()
        self.assertGreater(phone.sim.level, base.reserve)
        self.assertIn("maps", base.decision.deferred)
        phone.dash.set_in_use("maps")
        for _ in range(2):
            tick = phone._tick()
            self.assertEqual(tick.decision.allowed, ("calls", "messages"))
            self.assertEqual(tick.decision.deferred, ("email", "maps", "photos backup"))
            self.assertEqual(tick.decision.mode, base.decision.mode)
            self.assertEqual(tick.decision.reason, "worn battery: keeping a reserve for calls")
            self.assertEqual(tick.screen, home_screen(phone.sim.level, tick.decision, visible=phone.visible))
            self.assertIs(tick, phone._tick_obj)

    def test_in_use_respects_reserve(self):
        phone = Phone()
        phone.runtime.governor.advisor = None
        phone.sim.health = 1.0
        phone.sim.temp_c = 25.0
        phone.charging = False
        phone.sim.level = 80
        reserve = phone._tick().reserve
        below = reserve - 1.0
        if below < 0.0:
            below = 0.0
        for level in (reserve, below):
            phone.sim.level = level
            phone.dash.set_in_use("maps")
            tick = phone._tick()
            self.assertIn("maps", tick.decision.deferred)
            self.assertNotIn("maps", tick.decision.allowed)
            self.assertIn("calls", tick.decision.allowed)
            self.assertIn("messages", tick.decision.allowed)
        phone.dash.set_in_use("")


    def test_in_use_preserves_reserves(self):
        phone = Phone()
        phone.runtime.governor.advisor = None
        phone.charging = False
        phone.sim.temp_c = 25.0
        phone.sim.level = 5
        phone.sim.health = 0.35
        phone.dash.set_in_use("maps")
        tick = phone._tick()
        self.assertIn("maps", tick.decision.deferred)
        self.assertNotIn("maps", tick.decision.allowed)

        phone = Phone()
        phone.runtime.governor.advisor = None
        phone.charging = False
        phone.sim.temp_c = 25.0
        phone.sim.level = 10
        phone.sim.health = 1.0
        phone.runtime.estimator.add(Sample(0, 30))
        phone.runtime.estimator.add(Sample(3600, 10))
        phone.sim.t_s = 3600
        phone.dash.set_in_use("maps")
        tick = phone._tick()
        self.assertIn("maps", tick.decision.deferred)
        self.assertNotIn("maps", tick.decision.allowed)
        self.assertAlmostEqual(tick.reserve, 20.7, places=1)


if __name__ == "__main__":
    unittest.main()
