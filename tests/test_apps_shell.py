# SPDX-License-Identifier: GPL-3.0-or-later
"""Everyday app shell: local store, /apps, disclosure, and a main page that stays the same until an app is named."""
import http.client
import json
import os
import re
import stat
import threading
import unittest
from contextlib import contextmanager
from html import unescape
from pathlib import Path
from unittest import mock

from opos import appdoc, apps, apps_page, store as storemod, web
from opos.home import is_quiet
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, served_home_page

ROOT = Path(__file__).resolve().parents[1]


def render_sample(token, nonce):
    return appdoc.render("Sample", token, nonce, "<h1>Sample</h1><p>A stand-in page.</p>", "<p>Nothing is sent.</p>")


def post_sample(data):
    if data.get("action") != "ping":
        return 400, {"error": "bad request"}
    return 200, {"ok": True}


SAMPLE = apps.App(
    "sample", "Sample", "/sample", "A stand-in used by tests.",
    "yes", "no", "none", "real", "sample", "OPTIONAL", 0.1, render_sample, post=post_sample,
)


def visible(html):
    text = re.sub(r"<script[\s\S]*?</script>", " ", html)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    return [unescape(line).strip() for line in text.splitlines() if unescape(line).strip()]


@contextmanager
def running(env):
    """Serve one phone. Keys in env are set; OPOS_APPS is removed when env omits it."""
    with mock.patch.dict(os.environ, env, clear=False):
        if "OPOS_APPS" not in env:
            os.environ.pop("OPOS_APPS", None)
        server = web.make_server(0, web.Phone())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)


def get(server, path, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    headers = {}
    if token:
        headers["X-OPOS-Token"] = token
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


def post(server, path, payload, token):
    raw = json.dumps(payload).encode()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    conn.request("POST", path, raw, {
        "Content-Type": "application/json",
        "X-OPOS-Token": token,
        "Content-Length": str(len(raw)),
    })
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


class StoreTests(unittest.TestCase):
    def test_round_trip_mode_and_corrupt_recovery(self):
        folder = Path(self.enterContext(self._tmp()))
        saved = storemod.Store(folder)
        path = saved.write_json("contacts.json", {"people": [{"name": "Ana"}]})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        data, note = saved.read_json("contacts.json", {})
        self.assertEqual(note, "")
        self.assertEqual(data["people"][0]["name"], "Ana")
        self.assertEqual(data["schema"], 1)
        path.write_text("not json", encoding="utf-8")
        data, note = saved.read_json("contacts.json", {"people": []})
        self.assertEqual(data, {"people": []})
        self.assertIn("Started empty", note)
        self.assertFalse(path.exists())
        bad = list(folder.glob("contacts.json.bad-*"))
        self.assertEqual(len(bad), 1)
        again, note2 = saved.read_json("contacts.json", {"people": []})
        self.assertEqual(again, {"people": []})
        self.assertEqual(note2, "")
        self.assertFalse(list(folder.glob(".contacts.json.tmp")))

    def test_wrong_schema_is_set_aside_and_oversize_is_refused(self):
        folder = Path(self.enterContext(self._tmp()))
        saved = storemod.Store(folder)
        folder.mkdir(exist_ok=True)
        (folder / "notes.json").write_text('{"schema":0}', encoding="utf-8")
        data, note = saved.read_json("notes.json", {"items": []})
        self.assertEqual(data, {"items": []})
        self.assertIn("Started empty", note)
        with self.assertRaises(ValueError):
            saved.write_json("notes.json", {"items": ["x" * 100]}, limit=40)
        with self.assertRaises(ValueError):
            saved.write_json("../notes.json", {})
        rows = [{"n": i} for i in range(5)]
        saved.write_jsonl("calls.jsonl", rows, limit=3)
        got, note = saved.read_jsonl("calls.jsonl", limit=3)
        self.assertEqual([row["n"] for row in got], [2, 3, 4])
        self.assertEqual(note, "")
        self.assertEqual(stat.S_IMODE((folder / "calls.jsonl").stat().st_mode), 0o600)

    def _tmp(self):
        import tempfile
        return tempfile.TemporaryDirectory()


class CatalogTests(unittest.TestCase):
    def tearDown(self):
        apps.unregister("sample")
        apps.unregister("album")
        apps.MODULES.pop("zz", None)
        apps._BROKEN.discard("zz")

    def test_absent_switch_names_nothing(self):
        self.assertIsNone(apps.names_from({}))
        self.assertFalse(apps.switched_on({}))
        self.assertEqual(apps.names_from({"OPOS_APPS": ""}), [])
        self.assertFalse(apps.switched_on({"OPOS_APPS": "  , not a name"}))

    def test_disclosure_and_order(self):
        apps.register(SAMPLE)
        album = apps.App(
            "album", "Album", "/album", "A second stand-in.",
            "yes", "no", "speaker", "practice", "album", "USEFUL", 0, render_sample,
        )
        apps.register(album)
        self.assertEqual(SAMPLE.card(), "Works offline: yes. Sends data: no. Uses: none. Real or practice: real.")
        self.assertIn("Real or practice: practice", album.card())
        with mock.patch.dict(os.environ, {"OPOS_APPS": "sample,nope,album"}):
            titles = [row.title for row in apps.rows()]
        self.assertEqual(titles, ["Album", "Nope", "Sample"])

    def test_a_missing_module_does_not_stop_the_index(self):
        apps.MODULES["zz"] = "opos.no_such_module_zz"
        with mock.patch.dict(os.environ, {"OPOS_APPS": "zz"}):
            rows = apps.rows()
        self.assertEqual(rows[0].card(), "Not on this phone.")

    def test_pause_reasons_follow_the_battery_rules(self):
        apps.register(SAMPLE)
        alarms = apps.App(
            "bell", "Bell", "/bell", "Alarm stand-in.",
            "yes", "no", "speaker", "real", "alarms", "OPTIONAL", 0.1, render_sample,
        )
        apps.register(alarms)
        self.addCleanup(apps.unregister, "bell")
        self.assertIsNone(apps.pause_reason("sample", 90))
        self.assertEqual(apps.pause_reason("sample", 40), "Paused to save power.")
        self.assertIsNone(apps.pause_reason("sample", 40, in_use=True))
        self.assertEqual(apps.pause_reason("sample", 10, in_use=True), "Paused: the battery is very low.")
        self.assertEqual(apps.pause_reason("sample", 90, temp_c=46), "Paused: the phone is too hot.")
        self.assertIsNone(apps.pause_reason("bell", 10))
        self.assertTrue(is_quiet([SAMPLE.card(), apps.pause_reason("sample", 10) or ""]))


class PageTests(unittest.TestCase):
    def tearDown(self):
        apps.unregister("sample")

    def test_page_bytes_do_not_change_when_an_app_is_named(self):
        before = web.page("t" * 43, "n" * 22)
        apps.register(SAMPLE)
        with mock.patch.dict(os.environ, {"OPOS_APPS": "sample"}):
            after = web.page("t" * 43, "n" * 22)
        self.assertEqual(before, after)

    def test_the_link_is_absent_until_an_app_is_named_and_then_fits(self):
        plain = served_home_page({})
        self.assertNotIn(b"more-apps", plain)
        apps.register(SAMPLE)
        named = served_home_page({"OPOS_APPS": "sample"})
        self.assertIn(b"/apps-home.js", named)
        self.assertNotIn(b">More apps<", named)
        self.assertLessEqual(len(named), WAVE_GATE)
        self.assertLess(len(named) - len(plain), 2500)
        both = served_home_page(dict(served_switches(), **{"OPOS_APPS": "sample"}))
        self.assertLessEqual(len(both), WAVE_GATE)
        self.assertIn(b"/apps-home.js", both)

    def test_index_colours_disclosure_and_quiet_words(self):
        apps.register(SAMPLE)
        with mock.patch.dict(os.environ, {"OPOS_APPS": "sample"}):
            html = apps_page.index_html("tok", "nonce")
        css = style_of(html)
        self.assertEqual(set(), css_hexes(css) - TOKENS)
        self.assertNotRegex(css, r"@font-face|box-shadow|gradient")
        self.assertIn('id="home" class="btn" href="/"', html)
        self.assertIn("Works offline: yes. Sends data: no. Uses: none. Real or practice: real.", html)
        self.assertIn("A stand-in used by tests.", html)
        self.assertTrue(is_quiet(visible(html)))
        self.assertIn("Escape", html)
        self.assertIn("data-hand", html)
        self.assertIn('id="thumb"', html)

    def test_apps_are_not_served_when_the_switch_is_absent(self):
        with running({}) as server:
            status, body = get(server, "/apps")
        self.assertEqual(status, 404)
        self.assertNotIn(b"More apps", body)

    def test_index_post_and_fail_open(self):
        apps.register(SAMPLE)
        with running({"OPOS_APPS": "sample"}) as server:
            status, body = get(server, "/apps")
            self.assertEqual(status, 200)
            self.assertIn(b"Works offline: yes.", body)
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/sample", {"action": "ping"}, token)
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(raw)["ok"], True)
            denied, _ = post(server, "/api/sample", {"action": "ping"}, "nope")
            self.assertEqual(denied, 403)
            front, home = get(server, "/")
            self.assertEqual(front, 200)
            self.assertIn(b"Phone", home)
            self.assertIn(b"/apps-home.js", home)
            def broken(_token, _nonce):
                raise RuntimeError("broken render")
            SAMPLE.render = broken
            try:
                status, body = get(server, "/sample")
                self.assertEqual(status, 500)
                self.assertIn(b"This app could not open", body)
                self.assertIn(b'id="home"', body)
                self.assertNotIn(b"broken render", body)
                self.assertNotIn(b"RuntimeError", body)
                front, home = get(server, "/")
                self.assertEqual(front, 200)
                self.assertIn(b">Phone<", home)
            finally:
                SAMPLE.render = render_sample

    def test_state_does_not_gain_app_fields(self):
        before = set(web.Phone().state())
        apps.register(SAMPLE)
        with mock.patch.dict(os.environ, {"OPOS_APPS": "sample"}):
            after = set(web.Phone().state())
        self.assertEqual(before, after)

    def test_render_does_not_open_a_connection(self):
        import socket
        apps.register(SAMPLE)
        def refuse(*_a, **_k):
            raise AssertionError("connect")
        with mock.patch.dict(os.environ, {"OPOS_APPS": "sample"}):
            with mock.patch.object(socket.socket, "connect", refuse), mock.patch("socket.create_connection", refuse):
                html = apps_page.index_html("tok", "nonce")
                folder = self.enterContext(__import__("tempfile").TemporaryDirectory())
                storemod.Store(folder).write_json("contacts.json", {"people": []})
        self.assertIn("Sample", html)

    def test_image_drop_in_names_no_app_yet(self):
        conf = (ROOT / "packaging/duo-os/opos-apps.conf").read_text(encoding="utf-8")
        active = [line for line in conf.splitlines() if line.strip() and not line.strip().startswith("#")]
        self.assertIn("[Service]", active)
        self.assertIn("Environment=OPOS_APPS=clock,contacts,notes,calc,calls,convert", active)
        script = (ROOT / "packaging/duo-os/build-image.sh").read_text(encoding="utf-8")
        self.assertIn("20-opos-apps.conf", script)
        self.assertIn("docs/apps.md", (ROOT / "ROADMAP.md").read_text(encoding="utf-8"))


def served_switches():
    from tests.test_uxp30_budget import SWITCHES
    return dict(SWITCHES)


class FailOpenIndex(unittest.TestCase):
    def test_a_broken_index_still_leaves_the_front_page(self):
        apps.register(SAMPLE)
        self.addCleanup(apps.unregister, "sample")
        with mock.patch.object(apps_page, "index_html", side_effect=RuntimeError("index down")):
            with running({"OPOS_APPS": "sample"}) as server:
                status, body = get(server, "/apps")
                front, home = get(server, "/")
        self.assertEqual(status, 500)
        self.assertIn(b"This app could not open", body)
        self.assertIn(b'href="/"', body)
        self.assertNotIn(b"index down", body)
        self.assertEqual(front, 200)
        self.assertIn(b">Phone<", home)


if __name__ == "__main__":
    unittest.main()
