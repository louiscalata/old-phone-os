# SPDX-License-Identifier: GPL-3.0-or-later
"""Voice memos: a separate switch, local files, and no new package."""
import base64
import http.client
import json
import os
import socket
import stat
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from opos import apps, memos_page, web
from opos.home import is_quiet
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, page, served_home_page

ROOT = Path(__file__).resolve().parents[1]


def wav_bytes():
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)
    data = Path(tmp.name).read_bytes()
    os.unlink(tmp.name)
    return data


class MemosPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        memos_page.reset()
        self.env = {
            "OPOS_APPS": "memos", "OPOS_STATE_DIR": self.tmp.name,
            "OPOS_MEMOS_DIR": str(Path(self.tmp.name) / "memos"),
        }

    def tearDown(self):
        memos_page.reset()

    def test_the_page_is_its_own_switch_and_asks_for_the_microphone_only_there(self):
        html = memos_page.render("tok", "nonce")
        self.assertIn(">Record<", html)
        self.assertIn(">Stop<", html)
        self.assertNotIn("controls", html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        card = apps.by_id("memos").card()
        self.assertIn("Uses: microphone, speaker.", card)
        self.assertNotEqual(apps.MODULES["memos"], apps.MODULES.get("music"))
        self.assertNotIn("memos_page", (ROOT / "src/opos/music_page.py").read_text(encoding="utf-8"))
        with running(self.env) as server:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("GET", "/memos")
            response = conn.getresponse()
            policy = response.getheader("Permissions-Policy") or ""
            csp = response.getheader("Content-Security-Policy") or ""
            response.read()
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("GET", "/")
            home = conn.getresponse()
            home_policy = home.getheader("Permissions-Policy") or ""
            home.read()
            conn.close()
        self.assertIn("microphone=(self)", policy)
        self.assertIn("media-src 'self' blob: data:", csp)
        self.assertIn("microphone=()", home_policy)
        self.assertNotIn("microphone=(self)", home_policy)

    def test_chunks_round_trip_and_a_hard_floor_refuses_a_new_memo(self):
        raw = wav_bytes()
        pieces = [raw[index:index + 1200] for index in range(0, len(raw), 1200)]
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            code, body = post(server, "/api/memos", {"action": "begin", "mime": "audio/wav"}, token)
            self.assertEqual(code, 200, body)
            ident = json.loads(body)["id"]
            for number, piece in enumerate(pieces):
                code, body = post(server, "/api/memos", {
                    "action": "chunk", "id": ident, "n": number,
                    "data": base64.b64encode(piece).decode("ascii"),
                }, token)
                self.assertEqual(code, 200, body)
            code, body = post(server, "/api/memos", {"action": "finish", "id": ident, "count": len(pieces)}, token)
            snap = json.loads(body)
            self.assertEqual(code, 200, body)
            self.assertEqual(len(snap["memos"]), 1)
            self.assertTrue(snap["memos"][0]["title"].startswith("Memo "))
            leaked, _ = get(server, "/media/memos/" + ident + "?t=" + token, token)
            self.assertEqual(leaked, 403)
            status, audio = get(server, "/media/memos/" + ident, token)
            self.assertEqual(status, 200)
            self.assertEqual(audio, raw)
            saved = next((Path(self.env["OPOS_MEMOS_DIR"])).glob("memo-*.wav"))
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode) & 0o777, 0o600)
            code, body = post(server, "/api/memos", {"action": "delete", "id": ident}, token)
            self.assertEqual(json.loads(body)["memos"], [])
            server.RequestHandlerClass.phone.sim.level = 10
            server.RequestHandlerClass.phone.sim.health = 1
            code, body = post(server, "/api/memos", {"action": "begin", "mime": "audio/wav"}, token)
            self.assertEqual(code, 403)
            self.assertEqual(json.loads(body)["reason"], "Paused: the battery is very low.")
        self.assertFalse((Path(self.tmp.name) / "calendar.ics").exists())
        self.assertFalse((Path(self.tmp.name) / "alarms.json").exists())
        html = memos_page.render("tok", "nonce")
        self.assertIn("audio.src='/media/memos/'+memo.id;", html)
        self.assertNotIn("?t=", html)

    def test_the_main_page_does_not_grow_and_a_broken_memo_leaves_phone(self):
        before = page()
        with mock.patch.dict(os.environ, {"OPOS_APPS": "memos"}):
            self.assertEqual(page(), before)
        served = served_home_page({"OPOS_APPS": "memos"})
        self.assertLessEqual(len(served), WAVE_GATE)
        self.assertNotIn(b">Record<", served)
        app = apps.by_id("memos")
        original = app.render

        def boom(*_a, **_k):
            raise RuntimeError("broken memos")

        app.render = boom
        try:
            with running(self.env) as server:
                status, body = get(server, "/memos")
                front, home = get(server, "/")
        finally:
            app.render = original
        self.assertEqual(status, 500)
        self.assertIn(b'id="home"', body)
        self.assertNotIn(b"broken memos", body)
        self.assertEqual(front, 200)
        self.assertIn(b">Phone<", home)

    def test_the_helper_cannot_write_a_memo_and_the_page_opens_no_socket(self):
        for name in ("assist.py", "advisor.py", "muse.py", "home.py"):
            text = (ROOT / "src/opos" / name).read_text(encoding="utf-8")
            self.assertNotIn("memos_page", text)
            self.assertNotIn("OPOS_MEMOS_DIR", text)
        with mock.patch.dict(os.environ, self.env):

            def refuse(*_a, **_k):
                raise AssertionError("connect")

            with mock.patch.object(socket.socket, "connect", refuse), mock.patch("socket.create_connection", refuse):
                memos_page.render("tok", "nonce")
                memos_page.snapshot()


if __name__ == "__main__":
    unittest.main()
