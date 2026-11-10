# SPDX-License-Identifier: GPL-3.0-or-later
"""Recents: a local call list, filled by the dialer, with the main page left unchanged."""
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from opos import apps, calllog, calls_page
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, page, served_home_page

START = datetime(2026, 11, 10, 13, 4, tzinfo=timezone.utc)


class CallLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        calllog.reset()
        self.env = {"OPOS_APPS": "calls", "OPOS_STATE_DIR": self.tmp.name}

    def tearDown(self):
        calllog.reset()

    def test_a_finished_call_reads_newest_first(self):
        with mock.patch.dict(os.environ, self.env):
            calllog.begin("+15550100", "Mom", True, START)
            calllog.end(START + timedelta(seconds=125))
            calllog.begin("+15550199", "", True, START + timedelta(hours=1))
            calllog.end(START + timedelta(hours=1, seconds=3720))
            snap = calllog.snapshot()
        self.assertEqual(snap["calls"][0]["title"], "+15550199")
        self.assertEqual(snap["calls"][0]["way"], "Out")
        self.assertEqual(snap["calls"][0]["length"], "1 h 2 min")
        self.assertEqual(snap["calls"][0]["when"], "10 Nov, 14:04")
        self.assertTrue(snap["calls"][0]["practice"])
        self.assertEqual(snap["calls"][1]["title"], "Mom")
        self.assertEqual(snap["calls"][1]["number"], "+15550100")
        self.assertEqual(snap["calls"][1]["length"], "2 min 5 s")
        self.assertEqual(snap["calls"][1]["when"], "10 Nov, 13:04")
        mode = stat.S_IMODE(Path(self.tmp.name, "calls.json").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_an_open_call_a_missed_call_and_the_cap(self):
        with mock.patch.dict(os.environ, self.env):
            calllog.begin("+15550142", "Clinic", True, START)
            snap = calllog.snapshot()
            self.assertEqual(snap["calls"][0]["length"], "Not ended")
            missed = calllog.missed("5550101", "Sam", False, START)
            self.assertEqual(missed["direction"], "missed")
            self.assertEqual(missed["seconds"], 0)
            self.assertEqual(calllog.snapshot()["calls"][0]["way"], "Missed")
            self.assertEqual(calllog.duration_words(0), "0 s")
            self.assertEqual(calllog.duration_words(60), "1 min")
            self.assertEqual(calllog.duration_words(3600), "1 h")
            first = calllog.snapshot()["calls"][-1]["id"]
            for n in range(100):
                calllog.begin("+1555%04d" % n, "", False, START)
            snap = calllog.snapshot()
        self.assertEqual(len(snap["calls"]), 100)
        self.assertNotIn(first, [row["id"] for row in snap["calls"]])

    def test_a_bad_number_and_a_quiet_dialer_write_nothing(self):
        with mock.patch.dict(os.environ, self.env):
            with self.assertRaises(ValueError):
                calllog.begin("12", "", True, START)
            self.assertEqual(calllog.snapshot()["calls"], [])
            with self.assertRaises(ValueError):
                calllog.begin("+15550100", "", True, START, "sideways")
        quiet = tempfile.TemporaryDirectory()
        self.addCleanup(quiet.cleanup)
        with mock.patch.dict(os.environ, {"OPOS_STATE_DIR": quiet.name}, clear=False):
            os.environ.pop("OPOS_APPS", None)
            self.assertIsNone(calllog.note_start("+15550100", "Mom", True, START))
            self.assertIsNone(calllog.note_end(START))
        self.assertFalse(Path(quiet.name, "calls.json").exists())

    def test_a_broken_file_starts_empty(self):
        Path(self.tmp.name, "calls.json").write_text("{", encoding="utf-8")
        with mock.patch.dict(os.environ, self.env):
            snap = calllog.snapshot()
        self.assertEqual(snap["calls"], [])
        self.assertIn("could not be read", snap["recovered"])


class RecentsPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        calllog.reset()
        self.env = {"OPOS_APPS": "calls", "OPOS_STATE_DIR": self.tmp.name}

    def tearDown(self):
        calllog.reset()

    def test_the_page_is_plain_and_uses_only_the_token_colours(self):
        html = calls_page.render("tok", "nonce")
        self.assertIn(">Recents<", html)
        self.assertIn("No calls yet.", html)
        self.assertIn("No call was placed. Use Phone to dial.", html)
        self.assertIn("Newsreader", html)
        self.assertIn("Inter Tight", html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        card = apps.by_id("calls").card()
        self.assertEqual(
            "Works offline: yes. Sends data: no. Uses: none. Real or practice: practice.",
            card,
        )

    def test_the_dialer_fills_the_list_and_the_main_page_does_not_grow(self):
        before = page()
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/calls")
            self.assertEqual(status, 200)
            self.assertIn(b"Practice calls are marked.", body)
            code, raw = post(server, "/api", {"action": "call", "number": "+15550100"}, token)
            self.assertEqual(code, 200, raw)
            self.assertEqual(json.loads(raw)["status"], "in call")
            code, raw = post(server, "/api/calls", {"action": "add"}, token)
            self.assertEqual(code, 404)
            status, body = get(server, "/api/calls", token)
            open_call = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(open_call["calls"][0]["title"], "Mom")
            self.assertEqual(open_call["calls"][0]["length"], "Not ended")
            self.assertTrue(open_call["calls"][0]["practice"])
            post(server, "/api", {"action": "hang_up"}, token)
            status, body = get(server, "/api/calls", token)
            done = json.loads(body)
            self.assertEqual(done["calls"][0]["way"], "Out")
            self.assertNotEqual(done["calls"][0]["length"], "Not ended")
            post(server, "/api", {"action": "call", "number": "911"}, token)
            post(server, "/api", {"action": "call", "number": "nope"}, token)
            status, body = get(server, "/api/calls", token)
            self.assertEqual(len(json.loads(body)["calls"]), 1)
            status, body = get(server, "/api/calls")
            self.assertEqual(status, 403)
        with mock.patch.dict(os.environ, {"OPOS_APPS": "calls"}):
            self.assertEqual(page(), before)
            self.assertLessEqual(len(page().encode("utf-8")), WAVE_GATE)
        served = served_home_page({"OPOS_APPS": "calls"})
        self.assertLessEqual(len(served), WAVE_GATE)
        self.assertNotIn(b"No calls yet", served)

    def test_a_phone_that_does_not_name_recents_keeps_no_list(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with running({"OPOS_STATE_DIR": folder.name}) as server:
            token = server.RequestHandlerClass.token
            post(server, "/api", {"action": "call", "number": "+15550100"}, token)
            post(server, "/api", {"action": "hang_up"}, token)
            status, _body = get(server, "/calls")
            self.assertEqual(status, 404)
        self.assertFalse(Path(folder.name, "calls.json").exists())


if __name__ == "__main__":
    unittest.main()
