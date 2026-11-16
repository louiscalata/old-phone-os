# SPDX-License-Identifier: GPL-3.0-or-later
"""Clock page: save an alarm, ring from a test clock, snooze, stop, and refuse to ring when the clock is unknown."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from opos import apps, apps_page, clock_page, web
from opos.alarms import Event
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
        self.assertIn("Clock not set: alarms will not show until the time is known", html)
        self.assertIn(">Stop<", html)
        self.assertIn(">Snooze 10 min<", html)
        self.assertIn(">Bedside<", html)
        self.assertIn('type="text"', html)
        self.assertIn("24-hour", html)
        self.assertNotIn('type="time"', html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        self.assertIn("Works offline: yes. Sends data: no. Uses: screen only (no sound yet). Real or practice: real.", apps.by_id("clock").card())

    def test_production_alarm_without_a_player_discloses_screen_only_behavior(self):
        event = Event("ring", "alarm", "a", datetime.fromisoformat(FIRE), "Alarm 07:00")
        with self.assertRaisesRegex(RuntimeError, "no player"):
            clock_page._play([event])
        html = clock_page.render("tok", "nonce")
        notice = '<p class="note">Alarms show on screen only. No sound or vibration is played yet.</p>'
        self.assertIn(notice, html)
        card = apps.by_id("clock").card()
        self.assertIn("Uses: screen only (no sound yet).", card)
        self.assertIn("Real or practice: real.", card)

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

    def test_an_unknown_clock_check_does_not_count_as_known(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPOS_CLOCK_OK", None)
            with mock.patch("opos.web.clock_ok", side_effect=RuntimeError("boom")):
                self.assertFalse(clock_page.clock_known())


class ClockSnapshot(unittest.TestCase):
    """Exercise the API consumer directly, without a server or sound thread."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {
            "OPOS_STATE_DIR": tmp.name, "OPOS_CLOCK_ZONE": "UTC",
            "OPOS_CLOCK_OK": "1", "OPOS_CLOCK_TEST": "1",
        }))
        clock_page.reset()
        self.addCleanup(clock_page.reset)
        self.send("tick", now="2026-11-16T06:00:00+00:00")

    def send(self, action, **data):
        code, snap = clock_page.post(dict(data, action=action))
        self.assertEqual(code, 200, snap)
        return snap

    def test_one_shot_snapshot_ring_next_tick_snooze_stop_and_reload(self):
        snap = self.send("add", hour=7, minute=0, label="Kitchen")
        row = snap["alarms"][0]
        ident = row["id"]
        self.assertEqual(row, {"id": ident, "hour": 7, "minute": 0, "days": [], "label": "Kitchen", "enabled": True, "once": True})
        self.assertEqual(snap, {
            "ok": True, "clock_ok": True, "now": "2026-11-16T06:00:00+00:00", "alarms": [row],
            "ringing": None, "timer": None, "notices": [], "recovered": "",
            "next": {"id": ident, "text": "Alarm 07:00", "label": "Kitchen", "at": "2026-11-16T07:00:00+00:00"},
        })
        snap = self.send("tick", now=FIRE)
        ringing = {"id": ident, "label": "Kitchen", "text": "Alarm 07:00"}
        self.assertEqual(snap["ringing"], ringing)
        self.assertTrue(snap["alarms"][0]["enabled"])
        self.assertEqual(snap["next"]["at"], "2026-11-17T07:00:00+00:00")
        snap = self.send("tick", now="2026-11-16T07:01:00+00:00")
        self.assertEqual(snap["ringing"], ringing)
        self.assertFalse(snap["alarms"][0]["enabled"])
        self.assertIsNone(snap["next"])
        snap = self.send("snooze")
        self.assertIsNone(snap["ringing"])
        self.assertTrue(snap["alarms"][0]["enabled"])
        self.assertEqual(snap["next"]["at"], "2026-11-16T07:11:00+00:00")
        clock_page.reset()
        with mock.patch.object(clock_page, "current_now", return_value=datetime.fromisoformat(snap["now"])):
            self.assertEqual(clock_page.snapshot(), snap)
        self.send("tick", now="2026-11-16T07:11:00+00:00")
        snap = self.send("stop", id=ident)
        self.assertIsNone(snap["ringing"])
        self.assertFalse(snap["alarms"][0]["enabled"])
        self.assertIsNone(snap["next"])

    def test_unknown_clock_snapshot_and_missed_restart(self):
        self.send("add", hour=7, minute=0)
        before = clock_page.book().last_seen
        with mock.patch.dict(os.environ, {"OPOS_CLOCK_OK": "0"}):
            snap = self.send("tick", now="2026-11-16T07:11:00+00:00")
        self.assertFalse(snap["clock_ok"])
        self.assertIsNone(snap["next"])
        self.assertIsNone(snap["ringing"])
        self.assertEqual(snap["notices"], [])
        self.assertEqual(clock_page.book().last_seen, before)
        snap = self.send("tick", now="2026-11-16T07:11:00+00:00")
        self.assertIsNone(snap["ringing"])
        self.assertFalse(snap["alarms"][0]["enabled"])
        self.assertEqual(snap["notices"], ["Missed alarm 07:00"])

    def test_toggle_off_removes_upcoming_snooze_and_does_not_ring_later(self):
        ident = self.send("add", hour=7, minute=0)["alarms"][0]["id"]
        self.send("tick", now=FIRE)
        self.send("snooze", id=ident)
        snap = self.send("toggle", id=ident, enabled=False)
        self.assertIsNone(snap["next"])
        self.assertIsNone(snap["ringing"])
        snap = self.send("tick", now=SNOOZE)
        self.assertIsNone(snap["ringing"])
        self.assertIsNone(snap["next"])
        self.assertFalse(snap["alarms"][0]["enabled"])

    def test_snooze_of_an_alarm_that_never_rang_is_a_bad_request(self):
        before = self.send("add", hour=7, minute=0)
        code, result = clock_page.post({"action": "snooze", "id": before["alarms"][0]["id"]})
        self.assertEqual((code, result), (400, {"error": "id"}))
        self.assertEqual(clock_page.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
