# SPDX-License-Identifier: GPL-3.0-or-later
"""Clock page: save an alarm, ring from a test clock, snooze, stop, and refuse to ring when the clock is unknown."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from opos import apps, apps_page, clock_page, web
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, page, served_home_page

FIRE = "2026-11-16T07:00:30+00:00"
SNOOZE = "2026-11-16T07:10:30+00:00"


class ClockPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        clock_page.reset()
        self.env = {
            "OPOS_APPS": "clock", "OPOS_STATE_DIR": self.tmp.name, "OPOS_CLOCK_ZONE": "UTC",
            "OPOS_CLOCK_OK": "1", "OPOS_CLOCK_TEST": "1",
        }

    def tearDown(self):
        clock_page.reset()

    def test_the_page_says_what_is_real_and_uses_only_the_token_colours(self):
        html = clock_page.render("tok", "nonce")
        self.assertIn("Clock not set: alarms will not ring until the time is known", html)
        self.assertIn(">Stop<", html)
        self.assertIn(">Snooze 10 min<", html)
        self.assertIn(">Bedside<", html)
        self.assertIn('type="text"', html)
        self.assertIn("24-hour", html)
        self.assertNotIn('type="time"', html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        self.assertIn("Works offline: yes. Sends data: no. Uses: speaker, vibration. Real or practice: real.", apps.by_id("clock").card())

    def test_ring_snooze_stop_and_a_clock_that_is_not_set(self):
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/clock")
            self.assertEqual(status, 200)
            self.assertIn(b"Snooze 10 min", body)
            code, raw = post(server, "/api/clock", {"action": "add", "hour": 7, "minute": 0, "days": [0, 1, 2, 3, 4], "label": "Kitchen"}, token)
            self.assertEqual(code, 200, raw)
            code, raw = post(server, "/api/clock", {"action": "tick", "now": FIRE}, token)
            snap = json.loads(raw)
            self.assertEqual(code, 200)
            self.assertEqual(snap["ringing"]["text"], "Alarm 07:00")
            code, raw = post(server, "/api/clock", {"action": "snooze"}, token)
            self.assertIsNone(json.loads(raw)["ringing"])
            code, raw = post(server, "/api/clock", {"action": "tick", "now": SNOOZE}, token)
            self.assertEqual(json.loads(raw)["ringing"]["text"], "Alarm 07:00")
            code, raw = post(server, "/api/clock", {"action": "stop"}, token)
            snap = json.loads(raw)
            self.assertIsNone(snap["ringing"])
            self.assertTrue(snap["alarms"][0]["enabled"])
        clock_page.reset()
        unknown = dict(self.env, OPOS_CLOCK_OK="0", OPOS_STATE_DIR=self.tmp.name + "-off")
        os.makedirs(unknown["OPOS_STATE_DIR"], exist_ok=True)
        with running(unknown) as server:
            token = server.RequestHandlerClass.token
            post(server, "/api/clock", {"action": "add", "hour": 7, "minute": 0}, token)
            code, raw = post(server, "/api/clock", {"action": "tick", "now": FIRE}, token)
            snap = json.loads(raw)
            self.assertIsNone(snap["ringing"])
            self.assertFalse(snap["clock_ok"])
            self.assertEqual(len(snap["alarms"]), 1)
            self.assertEqual(snap["notices"], [])


    def test_typed_time_must_be_hh_mm(self):
        html = clock_page.render("tok", "nonce")
        self.assertIn(r"/^(?:[01]\d|2[0-3]):[0-5]\d$/", html)
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        match = re.search(r"G\('clock-time'\)\.onchange=function\(\)\{.*?\};", html)
        self.assertIsNotNone(match)
        script = r"""
function pad(n){return (n<10?'0':'')+n}
var hour=7,minute=0,shown=null;
var input={value:'07:00'};
function G(i){return i==='clock-time'?input:null}
function show(){shown=pad(hour)+':'+pad(minute)}
""" + match.group(0) + r"""
function apply(v){
  hour=7;minute=0;shown=null;input.value=v;
  input.onchange();
  return {hour:hour,minute:minute,shown:shown};
}
var rejects=[':', '1:2.5', '7:30', '24:00', '07:60', '', '07:0', '7:05', '25:00', '23:5'];
var accepts=[['07:00',7,0],['00:00',0,0],['23:59',23,59],['09:05',9,5],['12:30',12,30]];
for (var i=0;i<rejects.length;i++){
  var r=apply(rejects[i]);
  if(r.hour!==7||r.minute!==0||r.shown!=='07:00'){
    console.error('accepted '+JSON.stringify(rejects[i])+' -> '+JSON.stringify(r));
    process.exit(1);
  }
}
for (var j=0;j<accepts.length;j++){
  var a=accepts[j], r=apply(a[0]);
  var want=pad(a[1])+':'+pad(a[2]);
  if(r.hour!==a[1]||r.minute!==a[2]||r.shown!==want){
    console.error('bad accept '+JSON.stringify(a[0])+' -> '+JSON.stringify(r));
    process.exit(1);
  }
}
console.log('ok');
"""
        proc = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_tick_is_refused_without_the_test_hook_and_the_main_page_does_not_grow(self):
        before = page()
        env = dict(self.env, OPOS_CLOCK_TEST="0")
        with running(env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/clock", {"action": "tick", "now": FIRE}, token)
        self.assertEqual(code, 403)
        with mock.patch.dict(os.environ, {"OPOS_APPS": "clock"}):
            self.assertEqual(page(), before)
        served = served_home_page({"OPOS_APPS": "clock"})
        self.assertLessEqual(len(served), WAVE_GATE)
        self.assertIn(b"/apps-home.js", served)
        self.assertNotIn(b"Snooze 10 min", served)


if __name__ == "__main__":
    unittest.main()
