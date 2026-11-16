# SPDX-License-Identifier: GPL-3.0-or-later
"""Clock page: save an alarm, ring from a test clock, snooze, stop, and refuse to ring when the clock is unknown."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

from opos import alarm_sound, apps, apps_page, clock_page, web
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
            "OPOS_APPS": "clock",
            "OPOS_STATE_DIR": self.tmp.name,
            "OPOS_CLOCK_ZONE": "UTC",
            "OPOS_CLOCK_OK": "1",
            "OPOS_CLOCK_TEST": "1",
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
        self.assertIn(
            "Works offline: yes. Sends data: no. Uses: screen and a short tone (not checked on a phone). Real or practice: real.",
            apps.by_id("clock").card(),
        )

    def test_the_page_says_the_tone_is_short_unchecked_and_has_no_vibration(self):
        html = clock_page.render("tok", "nonce")
        notice = (
            '<p class="note">An alarm shows on screen and plays six short beeps through paplay, ending within'
            " %d seconds. Stop ends them. The tone has not been checked on a phone. No vibration.</p>"
            % alarm_sound.worst_case_seconds()
        )
        self.assertIn(notice, html)
        self.assertNotIn("No sound or vibration is played yet", html)
        card = apps.by_id("clock").card()
        self.assertIn("Uses: screen and a short tone (not checked on a phone).", card)
        self.assertIn("Real or practice: real.", card)

    def _stub(self, body):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        script = os.path.join(folder, "paplay")
        with open(script, "w", encoding="utf-8") as out:
            out.write("#!/bin/sh\n" + body + "\n")
        os.chmod(script, 0o755)
        log = os.path.join(folder, "log")
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {
                    "PATH": folder + os.pathsep + os.environ.get("PATH", ""),
                    "OPOS_PAPLAY_LOG": log,
                },
            )
        )
        return log

    def test_the_clock_thread_path_plays_the_ramp_through_paplay(self):
        log = self._stub('printf \'%s\\n\' "$*" >> "$OPOS_PAPLAY_LOG"')
        event = Event("ring", "alarm", "a", datetime.fromisoformat(FIRE), "Alarm 07:00")
        clock_page._play([event])
        with open(log, encoding="utf-8") as seen:
            lines = seen.read().splitlines()
        self.assertEqual(len(lines), 6)
        self.assertTrue(lines[0].startswith("--volume %d " % (20 * 655)), lines[0])
        self.assertTrue(lines[-1].startswith("--volume %d " % (70 * 655)), lines[-1])
        self.assertTrue(all(line.endswith("alarm.wav") for line in lines))

    def test_stop_on_the_page_ends_a_ramp_that_is_playing(self):
        log = self._stub('printf \'%s\\n\' "$*" >> "$OPOS_PAPLAY_LOG"\nexec sleep 30')
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {
                    "OPOS_STATE_DIR": self.tmp.name,
                    "OPOS_CLOCK_ZONE": "UTC",
                    "OPOS_CLOCK_OK": "1",
                    "OPOS_CLOCK_TEST": "1",
                },
            )
        )
        clock_page.post({"action": "tick", "now": "2026-11-16T06:00:00+00:00"})
        clock_page.post({"action": "add", "hour": 7, "minute": 0})
        event = Event("ring", "alarm", "a", datetime.fromisoformat(FIRE), "Alarm 07:00")
        clock_page.post({"action": "tick", "now": FIRE})
        ramp = threading.Thread(target=clock_page._play, args=([event],))
        begun = time.monotonic()
        ramp.start()
        for _ in range(200):
            if os.path.exists(log):
                break
            time.sleep(0.01)
        code, snap = clock_page.post({"action": "stop"})
        self.assertEqual(code, 200)
        self.assertIsNone(snap["ringing"])
        ramp.join(5)
        self.assertFalse(ramp.is_alive())
        self.assertLess(time.monotonic() - begun, 5)
        with open(log, encoding="utf-8") as seen:
            self.assertEqual(len(seen.read().splitlines()), 1)
        self.assertIsNone(clock_page._player().proc)

    def test_stop_silences_even_when_nothing_is_ringing(self):
        player = clock_page._player()
        before = player.generation
        with mock.patch.dict(os.environ, {"OPOS_STATE_DIR": self.tmp.name, "OPOS_CLOCK_ZONE": "UTC"}):
            code, _snap = clock_page.post({"action": "stop"})
        self.assertEqual(code, 400)
        self.assertEqual(player.generation, before + 1)

    def test_a_stop_between_the_tick_and_the_ramp_wins(self):
        log = self._stub('printf \'%s\\n\' "$*" >> "$OPOS_PAPLAY_LOG"')
        event = Event("ring", "alarm", "a", datetime.fromisoformat(FIRE), "Alarm 07:00")

        class Book:
            def tick(self, _now, _known):
                clock_page.silence()
                return [event]

        with mock.patch.object(clock_page, "book", return_value=Book()):
            clock_page._cycle()
        self.assertFalse(os.path.exists(log))

    def test_a_locked_phone_shows_the_ring_without_its_label_and_stop_and_snooze_end_the_beep(self):
        from opos import lockpin

        log = self._stub('printf \'%s\\n\' "$*" >> "$OPOS_PAPLAY_LOG"\nexec sleep 30')
        self.addCleanup(lockpin.reset)
        lockpin.reset()
        env = dict(self.env, OPOS_LOCK="1", OPOS_CONFIG_DIR=self.tmp.name)
        event = Event("ring", "alarm", "a", datetime.fromisoformat(FIRE), "Alarm 07:00")

        def beeps():
            if not os.path.exists(log):
                return 0
            with open(log, encoding="utf-8") as seen:
                return len(seen.read().splitlines())

        def ring_then_lock(server, token, now, heard):
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            code, raw = post(server, "/api/clock", {"action": "tick", "now": now}, token)
            self.assertEqual(code, 200, raw)
            self.assertEqual(json.loads(raw)["ringing"]["text"], "Alarm 07:00")
            ramp = threading.Thread(target=clock_page._play, args=([event],))
            ramp.start()
            for _ in range(500):
                if beeps() > heard:
                    break
                time.sleep(0.01)
            self.assertEqual(beeps(), heard + 1)
            code, raw = post(server, "/api/lock", {"action": "lock"}, token)
            self.assertEqual(code, 200, raw)
            status, raw = get(server, "/api/clock", token)
            self.assertEqual(status, 200, raw)
            body = json.loads(raw)
            self.assertIs(body["locked"], True)
            self.assertEqual(body["ringing"], {"text": "Alarm 07:00"})
            self.assertEqual(body["alarms"], [])
            self.assertIsNone(body["timer"])
            self.assertNotIn(b"Pills", raw)
            self.assertNotIn(b"Ana", raw)
            self.assertIsNotNone(clock_page._player().proc)  # the beep is still playing behind the lock
            return ramp

        def answer_while_locked(server, token, action, ramp, heard):
            begun = time.monotonic()
            code, raw = post(server, "/api/clock", {"action": action}, token)
            self.assertEqual(code, 200, raw)
            body = json.loads(raw)
            self.assertIs(body["locked"], True)
            self.assertIsNone(body["ringing"])
            self.assertEqual(body["alarms"], [])
            self.assertNotIn(b"Pills", raw)
            self.assertNotIn(b"Ana", raw)
            ramp.join(alarm_sound.worst_case_seconds())
            self.assertFalse(ramp.is_alive())
            self.assertLess(time.monotonic() - begun, 5)
            self.assertEqual(beeps(), heard)
            self.assertIsNone(clock_page._player().proc)

        with running(env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/lock", {"action": "set", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            code, raw = post(server, "/api/clock", {"action": "tick", "now": "2026-11-16T06:00:00+00:00"}, token)
            self.assertEqual(code, 200, raw)
            code, raw = post(
                server,
                "/api/clock",
                {"action": "add", "hour": 7, "minute": 0, "days": [], "label": "Pills for Ana"},
                token,
            )
            self.assertEqual(code, 200, raw)
            ramp = ring_then_lock(server, token, FIRE, 0)
            answer_while_locked(server, token, "snooze", ramp, 1)
            ramp = ring_then_lock(server, token, SNOOZE, 1)
            answer_while_locked(server, token, "stop", ramp, 2)
            # The ring is over: unlocked, the owner sees their alarm and its label again.
            code, raw = post(server, "/api/lock", {"action": "unlock", "pin": "1234"}, token)
            self.assertEqual(code, 200, raw)
            status, raw = get(server, "/api/clock", token)
            self.assertIn(b"Pills for Ana", raw)
            self.assertIsNone(json.loads(raw)["ringing"])

    def test_ring_snooze_stop_and_a_clock_that_is_not_set(self):
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/clock")
            self.assertEqual(status, 200)
            self.assertIn(b"Snooze 10 min", body)
            code, raw = post(
                server,
                "/api/clock",
                {"action": "add", "hour": 7, "minute": 0, "days": [0, 1, 2, 3, 4], "label": "Kitchen"},
                token,
            )
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
        script = (
            r"""
function pad(n){return (n<10?'0':'')+n}
var hour=7,minute=0,shown=null;
var input={value:'07:00'};
function G(i){return i==='clock-time'?input:null}
function show(){shown=pad(hour)+':'+pad(minute)}
"""
            + match.group(0)
            + r"""
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
        )
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
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {
                    "OPOS_STATE_DIR": tmp.name,
                    "OPOS_CLOCK_ZONE": "UTC",
                    "OPOS_CLOCK_OK": "1",
                    "OPOS_CLOCK_TEST": "1",
                },
            )
        )
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
        self.assertEqual(
            row, {"id": ident, "hour": 7, "minute": 0, "days": [], "label": "Kitchen", "enabled": True, "once": True}
        )
        self.assertEqual(
            snap,
            {
                "ok": True,
                "clock_ok": True,
                "now": "2026-11-16T06:00:00+00:00",
                "alarms": [row],
                "ringing": None,
                "timer": None,
                "notices": [],
                "recovered": "",
                "next": {"id": ident, "text": "Alarm 07:00", "label": "Kitchen", "at": "2026-11-16T07:00:00+00:00"},
            },
        )
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
