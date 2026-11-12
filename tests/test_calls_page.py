# SPDX-License-Identifier: GPL-3.0-or-later
"""The actual app handlers and script, without a listening socket or browser."""
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from opos import apps, apps_page, calllog, calls_page, contacts_page, firstrun, web
from opos.home import is_quiet
from tests.test_apps_shell import visible
from tests.test_calllog import event
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import SWITCHES, WAVE_GATE


def request(path, body=None, token="t" * 43, host="127.0.0.1:8765", **headers):
    """Run Handler._get/_post with in-memory streams, including auth and injection."""
    h = object.__new__(web.Handler)
    h.command = "GET" if body is None else "POST"
    h.path, h.port, h.token, h.span = path, 8765, "t" * 43, None
    h.span = web.parse_span(os.environ.get("OPOS_SPAN"))
    h.headers = {"Host": host, "X-OPOS-Token": token, **headers}
    raw = b"" if body is None else json.dumps(body).encode()
    if body is not None:
        h.headers.update({"Content-Type": "application/json", "Content-Length": str(len(raw))})
    h.rfile, h.wfile = BytesIO(raw), BytesIO()
    h.connection = SimpleNamespace(settimeout=lambda _value: None)
    h.lock, h.limiter = threading.Lock(), web._Limits()
    h.response_headers = {}
    h.send_response = lambda status: setattr(h, "status", status)
    h.send_header = lambda key, value: h.response_headers.update({key: value})
    h.end_headers = lambda: None
    if body is None:
        h._get()
    else:
        h._post()
    return h.status, h.wfile.getvalue(), h.response_headers


class CallsPage(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.dict(os.environ, {"OPOS_APPS": "calls", "OPOS_STATE_DIR": tmp, "OPOS_CONFIG_DIR": tmp, "OPOS_LOCK": "0"}))
        calllog.reset()
        contacts_page.reset()
        self.addCleanup(calllog.reset)
        self.addCleanup(contacts_page.reset)

    def test_routes_are_off_without_calls_even_after_import(self):
        for value in (None, "", "clock,contacts,notes,calc"):
            with self.subTest(apps=value), mock.patch.dict(os.environ):
                if value is None:
                    os.environ.pop("OPOS_APPS", None)
                else:
                    os.environ["OPOS_APPS"] = value
                self.assertEqual(request("/calls")[0], 404)
                self.assertEqual(request("/api/calls")[0], 404)
                self.assertEqual(request("/api/calls", {"action": "clear", "confirm": True})[0], 404)

    def test_index_registration_and_disclosure(self):
        status, body, _ = request("/apps")
        self.assertEqual(status, 200)
        self.assertIn(b'href="/calls"', body)
        self.assertIn(b"Works offline: yes. Sends data: no.", body)
        self.assertEqual(apps.by_id("calls").task_name, "call-log")
        self.assertIsNone(apps.pause_reason("calls", 1))

    def test_api_auth_confirmation_and_no_event_insertion(self):
        calllog.record(event(direction="incoming"))
        calllog.record(event(state="ended", direction="incoming"))
        self.assertEqual(request("/api/calls", token="wrong")[0], 403)
        status, body, _ = request("/api/calls")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["missed_count"], 1)
        self.assertIs(json.loads(body)["calls"][0]["practice"], True)
        for payload in ({"action": "clear"}, {"action": "clear", "confirm": "true"},
                        {"action": "clear", "confirm": 1}, {"action": "record", "practice": False},
                        {"action": "clear", "confirm": True, "calls": []}):
            self.assertEqual(request("/api/calls", payload)[0], 400, payload)
            self.assertEqual(len(calllog.snapshot()["calls"]), 1)
        self.assertEqual(request("/api/calls", {"action": "clear", "confirm": True}, token="wrong")[0], 403)
        self.assertEqual(request("/api/calls", {"action": "clear", "confirm": True}, Origin="https://elsewhere.invalid")[0], 403)
        status, body, _ = request("/api/calls", {"action": "clear", "confirm": True})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["calls"], [])
        self.assertEqual(json.loads(body)["missed_count"], 0)

    def test_app_errors_leave_home_available_and_clear_failure_is_not_success(self):
        with mock.patch.object(calllog, "clear", side_effect=OSError("private details")):
            status, body, _ = request("/api/calls", {"action": "clear", "confirm": True})
            self.assertEqual(status, 500)
            self.assertEqual(json.loads(body), {"error": "internal error"})
        with mock.patch.object(apps.by_id("calls"), "render", side_effect=OSError("private details")):
            status, body, _ = request("/calls")
            self.assertEqual(status, 500)
            self.assertIn(b"This app could not open", body)
            self.assertNotIn(b"private details", body)
            self.assertEqual(request("/")[0], 200)

    def test_shell_has_keyboard_panes_safe_labels_and_no_external_resource(self):
        with mock.patch.dict(os.environ, {"OPOS_OSK": "1", "OPOS_PANES": "1"}):
            status, raw, headers = request("/calls")
        html = raw.decode()
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["Content-Length"]), len(raw))
        for text in ('src="/osk.js"', 'src="/panes.js"', 'id="list"', 'id="thumb"', 'data-hand',
                     'id="home" class="btn" href="/"', 'Search calls <input', 'type="search"',
                     'aria-label="Calls, newest first"', 'role="status"', 'Confirm clear all calls', '>Cancel<'):
            self.assertIn(text, html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        self.assertNotIn("innerHTML", html)
        self.assertNotRegex(html, r'(?:src|href)="https?://')
        self.assertNotIn("action:'call'", html)
        self.assertIn("/?practice=phone&n=", html)

    def test_home_bytes_and_recorded_digests_are_unchanged(self):
        # The original digest test needs no fixture; its inherited setUp binds
        # an unrelated HTTP source that is unavailable in a socket sandbox.
        from tests.test_update_client import MainPageIsUntouched
        MainPageIsUntouched("test_recorded_digests").test_recorded_digests()
        plain = web.page("t" * 43, "n" * 22)
        with mock.patch.dict(os.environ, {"OPOS_APPS": ""}):
            self.assertEqual(web.page("t" * 43, "n" * 22), plain)
            off = request("/")[1]
        old = "clock,contacts,notes,calc"
        with mock.patch.dict(os.environ, {"OPOS_APPS": old}):
            before = request("/")[1]
        with mock.patch.dict(os.environ, {"OPOS_APPS": old + ",calls"}):
            after = request("/")[1]
        # Real handlers create random nonces; normalize only the nonce value.
        import re
        normalize = lambda raw: re.sub(rb'nonce="[A-Za-z0-9_-]+"', b'nonce="fixed"', raw)
        self.assertEqual(normalize(before), normalize(after))
        self.assertGreater(len(after), len(off))
        self.assertLessEqual(len(after), WAVE_GATE)
        with mock.patch.dict(os.environ, dict(SWITCHES, OPOS_APPS=old + ",calls")):
            undo = firstrun.install(web)
            try:
                status, served, _ = request("/")
            finally:
                if undo is not None:
                    undo()
        self.assertEqual(status, 200)
        self.assertLessEqual(len(served), WAVE_GATE)
        self.assertNotIn(b"/api/calls", served)
        self.assertNotIn(b"Missed calls in this log", served)
        with mock.patch.dict(os.environ, dict(SWITCHES, OPOS_APPS=old)):
            undo = firstrun.install(web)
            try:
                old_served = request("/")[1]
            finally:
                if undo is not None:
                    undo()
        self.assertEqual(normalize(old_served), normalize(served))
        print("Socket-free Handler._get home: %s bytes with every switch and calls" % len(served))

    def test_script_grouping_callback_search_confirmation_and_failures(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        result = subprocess.run([node, str(Path(__file__).with_name("calls_page_test.js"))],
                                input=json.dumps(calls_page._SCRIPT), text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("call log script checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
