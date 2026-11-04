# SPDX-License-Identifier: GPL-3.0-or-later
"""Design tests for Old Phone OS Interface: history, home screen, web, goal wiring."""

import re
import threading
import unittest
import urllib.request

from opos.dashboard import Dashboard
from opos.governor import Decision, Mode
from opos.home import home_screen
from opos.runtime import DEFAULT_TASKS
from opos.web import Phone, make_server


def make_dash(history_len=60):
    return Dashboard(DEFAULT_TASKS, history_len=history_len)


def make_view(dash, level):
    return dash.view(
        level, Mode.NORMAL, False, 1.0, 43200.0, False, "rules"
    )


class TestDesign(unittest.TestCase):
    def test_record_one_point_per_minute(self):
        dash = make_dash(history_len=60)
        dash.record(50, clock_s=120.0)
        dash.record(51, clock_s=121.0)
        dash.record(52, clock_s=125.0)
        self.assertEqual(len(dash.history), 1)
        self.assertEqual(dash.history[0], 50)
        dash.record(53, clock_s=181.0)
        self.assertEqual(len(dash.history), 2)
        self.assertEqual(dash.history[-1], 53)

    def test_history_bounded(self):
        dash = make_dash(history_len=10)
        for i in range(30):
            dash.record(float(i))
        hist = make_view(dash, 29.0)["history"]
        self.assertLessEqual(len(hist), 10)

    def test_reset_history(self):
        dash = make_dash(history_len=60)
        dash.record(50, clock_s=60.0)
        dash.reset_history(30.0)
        self.assertEqual(dash.history, [30.0])
        dash.record(31.0, clock_s=61.0)
        self.assertEqual(dash.history, [30.0, 31.0])

    def test_view_history_keeps_measured_levels(self):
        dash = make_dash()
        for _ in range(5):
            dash.record(50.0)
        dash.record(40.0)
        history = make_view(dash, 40.0)["history"]
        self.assertEqual(history, [50.0] * 5 + [40.0])

    def test_set_battery_preserves_recent_history(self):
        phone = Phone()
        phone.set_battery(30)
        hist = phone.state()["battery"]["history"]
        self.assertEqual(hist, [60.0, 30.0])

    def test_home_screen_no_battery(self):
        decision = Decision(Mode.NORMAL, ("calls", "messages"), (), "rules")
        lines = home_screen(50, decision, visible=False)
        self.assertEqual(lines[0], "Calls")
        for line in lines:
            self.assertNotIn("Battery", line)
            self.assertNotIn("%", line)

    def test_server_root_and_demo(self):
        phone = Phone()
        server = make_server(0, phone)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        with urllib.request.urlopen(base + "/") as r:
            self.assertEqual(r.status, 200)
            html = r.read().decode("utf-8")
        with urllib.request.urlopen(base + "/?demo=1") as r:
            self.assertEqual(r.status, 200)
            html2 = r.read().decode("utf-8")
        self.assertEqual(re.sub(r'nonce="[^"]*"', 'nonce="N"', html), re.sub(r'nonce="[^"]*"', 'nonce="N"', html2))
        self.assertIn('id="demo"', html)
        self.assertIn("hidden", html.split('id="demo"')[0][-200:] + html.split('id="demo"')[1][:200])
        self.assertIn('id="sheet-grip"', html)

    def test_goal_hours_wiring(self):
        phone = Phone()
        phone.dash.set_goal("23:00")
        seen = {}
        orig = phone.runtime.step

        def wrapper(*a, **k):
            seen["goal_hours"] = k.get("goal_hours")
            return orig(*a, **k)

        phone.runtime.step = wrapper
        try:
            phone.state()
        finally:
            phone.runtime.step = orig
        self.assertIsInstance(seen.get("goal_hours"), float)
        self.assertGreater(seen["goal_hours"], 0)
        self.assertLess(seen["goal_hours"], 24)

        phone2 = Phone()
        seen2 = {}
        orig2 = phone2.runtime.step

        def wrapper2(*a, **k):
            seen2["goal_hours"] = k.get("goal_hours")
            return orig2(*a, **k)

        phone2.runtime.step = wrapper2
        try:
            phone2.state()
        finally:
            phone2.runtime.step = orig2
        self.assertIsNone(seen2.get("goal_hours"))


if __name__ == "__main__":
    unittest.main()
