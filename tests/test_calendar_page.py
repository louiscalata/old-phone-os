# SPDX-License-Identifier: GPL-3.0-or-later
"""Calendar page: save an event, remind from a test clock, and keep the title off the lock line."""
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opos import apps, calendar_page, web
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, page, served_home_page

ROOT = Path(__file__).resolve().parents[1]
WHEN = "2026-11-10T14:50:30+00:00"
LATE = "2026-11-10T15:01:00+00:00"
ICS = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:cafe1\nSUMMARY:Café\nDTSTART:20261110T180000Z\nDTEND:20261110T190000Z\nEND:VEVENT\nEND:VCALENDAR\n"


class CalendarPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        calendar_page.reset()
        self.env = {
            "OPOS_APPS": "calendar", "OPOS_STATE_DIR": self.tmp.name, "OPOS_CLOCK_ZONE": "UTC",
            "OPOS_CLOCK_OK": "1", "OPOS_CALENDAR_TEST": "1",
            "OPOS_CALENDAR_DIR": str(Path(self.tmp.name) / "ics"),
        }

    def tearDown(self):
        calendar_page.reset()

    def test_the_page_is_text_only_and_names_what_it_uses(self):
        html = calendar_page.render("tok", "nonce")
        self.assertIn("Clock not set: reminders will not show until the time is known", html)
        self.assertIn(">Save<", html)
        self.assertIn(">Add event<", html)
        self.assertIn('type="date"', html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        self.assertIn("Works offline: yes. Sends data: no. Uses: none. Real or practice: real.", apps.by_id("calendar").card())
        self.assertEqual(apps.by_id("calendar").priority, "USEFUL")

    def test_a_reminder_hands_off_without_writing_an_alarm_or_putting_the_title_on_the_lock_line(self):
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/calendar")
            self.assertEqual(status, 200)
            self.assertIn(b"Add event", body)
            post(server, "/api/calendar", {"action": "tick", "now": WHEN}, token)
            code, raw = post(server, "/api/calendar", {
                "action": "add", "title": "Secret Lunch", "date": "2026-11-10", "time": "15:00",
                "all_day": False, "rule": "once", "reminder": "10min",
            }, token)
            self.assertEqual(code, 200, raw)
            snap = json.loads(raw)
            self.assertEqual(snap["agenda"]["today"][0]["title"], "Secret Lunch")
            self.assertEqual(snap["next_lock"], "Next: 15:00")
            self.assertNotIn("Secret", snap["next_lock"])
            self.assertEqual(snap["reminder"]["text"], "Reminder: 15:00")
            self.assertNotIn("Secret", snap["reminder"]["text"])
            code, raw = post(server, "/api/calendar", {"action": "dismiss", "key": snap["reminder"]["key"]}, token)
            self.assertIsNone(json.loads(raw)["reminder"])
        self.assertFalse((Path(self.tmp.name) / "alarms.json").exists())
        calendar_page.reset()
        unknown = dict(self.env, OPOS_CLOCK_OK="0", OPOS_STATE_DIR=self.tmp.name + "-off")
        os.makedirs(unknown["OPOS_STATE_DIR"], exist_ok=True)
        with running(unknown) as server:
            token = server.RequestHandlerClass.token
            post(server, "/api/calendar", {
                "action": "add", "title": "Secret Lunch", "date": "2026-11-10", "time": "15:00", "reminder": "10min",
            }, token)
            code, raw = post(server, "/api/calendar", {"action": "tick", "now": LATE}, token)
            snap = json.loads(raw)
            self.assertIsNone(snap["reminder"])
            self.assertEqual(snap["next_lock"], "")
            self.assertFalse(snap["clock_ok"])
            self.assertEqual(len(snap["events"]), 1)
            self.assertEqual(snap["notices"], [])

    def test_import_and_export_round_trip_a_file(self):
        folder = Path(self.env["OPOS_CALENDAR_DIR"])
        folder.mkdir()
        (folder / "in.ics").write_text(ICS, encoding="utf-8")
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/calendar", {"action": "import"}, token)
            snap = json.loads(raw)
            self.assertEqual(code, 200, raw)
            self.assertEqual(snap["events"][0]["title"], "Café")
            self.assertIn("Imported 1.", snap["file_line"])
            code, raw = post(server, "/api/calendar", {"action": "export"}, token)
            self.assertEqual(code, 200, raw)
        exported = (folder / "calendar.ics").read_text(encoding="utf-8")
        self.assertIn("Café", exported)
        self.assertNotRegex(exported, r"IMEI|IMSI|ICCID|MAC")

    def test_tick_is_refused_without_the_test_hook_and_the_main_page_does_not_grow(self):
        before = page()
        env = dict(self.env, OPOS_CALENDAR_TEST="0")
        with running(env) as server:
            token = server.RequestHandlerClass.token
            code, _raw = post(server, "/api/calendar", {"action": "tick", "now": WHEN}, token)
        self.assertEqual(code, 403)
        with mock.patch.dict(os.environ, {"OPOS_APPS": "calendar"}):
            self.assertEqual(page(), before)
        served = served_home_page({"OPOS_APPS": "calendar"})
        self.assertLessEqual(len(served), WAVE_GATE)
        self.assertIn(b"/apps-home.js", served)
        self.assertNotIn(b"Add event", served)

    def test_a_broken_calendar_leaves_the_front_page(self):
        app = apps.by_id("calendar")
        original = app.render

        def boom(*_a, **_k):
            raise RuntimeError("broken calendar")

        app.render = boom
        try:
            with running(self.env) as server:
                status, body = get(server, "/calendar")
                front, home = get(server, "/")
        finally:
            app.render = original
        self.assertEqual(status, 500)
        self.assertIn(b"This app could not open", body)
        self.assertIn(b'id="home"', body)
        self.assertNotIn(b"broken calendar", body)
        self.assertEqual(front, 200)
        self.assertIn(b">Phone<", home)

    def test_the_helper_cannot_write_events_and_the_page_opens_no_socket(self):
        for name in ("assist.py", "advisor.py", "muse.py", "home.py"):
            text = (ROOT / "src/opos" / name).read_text(encoding="utf-8")
            self.assertNotIn("calendar_page", text)
            self.assertNotIn("calendar.ics", text)
        before = set(web.Phone().state())
        with mock.patch.dict(os.environ, {"OPOS_APPS": "calendar", "OPOS_STATE_DIR": self.tmp.name}):
            after = set(web.Phone().state())
            self.assertEqual(before, after)

            def refuse(*_a, **_k):
                raise AssertionError("connect")

            with mock.patch.object(socket.socket, "connect", refuse), mock.patch("socket.create_connection", refuse):
                calendar_page.render("tok", "nonce")
                calendar_page.snapshot()


if __name__ == "__main__":
    unittest.main()
