# SPDX-License-Identifier: GPL-3.0-or-later
"""Music page: list local files, pause on a hard battery floor, and reuse the Clock timer."""
import http.client
import json
import os
import socket
import tempfile
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from opos import apps, music_page, web
from opos.home import is_quiet
from opos.store import Store
from tests.test_apps_shell import get, post, running, visible
from tests.test_panes_pages import TOKENS, css_hexes, style_of
from tests.test_uxp30_budget import WAVE_GATE, page, served_home_page

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def write_wav(path, seconds=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * int(8000 * seconds))


class MusicPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        music_page.reset()
        self.music = Path(self.tmp.name) / "Music"
        write_wav(self.music / "Kitchen" / "quiet.wav")
        self.env = {
            "OPOS_APPS": "music", "OPOS_STATE_DIR": self.tmp.name, "OPOS_MUSIC_DIR": str(self.music),
            "OPOS_MUSIC_TEST": "1",
        }

    def tearDown(self):
        music_page.reset()

    def test_the_page_is_text_only_and_names_the_speaker(self):
        html = music_page.render("tok", "nonce")
        self.assertIn(">Play<", html)
        self.assertIn(">Previous<", html)
        self.assertIn(">Next<", html)
        self.assertIn("0:00 / 0:00", html)
        self.assertNotIn("controls", html)
        self.assertEqual(set(), css_hexes(style_of(html)) - TOKENS)
        self.assertTrue(is_quiet(visible(html)))
        self.assertIn("Works offline: yes. Sends data: no. Uses: speaker. Real or practice: real.", apps.by_id("music").card())
        self.assertEqual(apps.by_id("music").priority, "OPTIONAL")

    def test_a_hard_floor_pauses_and_a_soft_pause_does_not_while_the_page_is_open(self):
        music_page.set_power(40, False, 25)
        self.assertFalse(music_page.snapshot()["paused"])
        music_page.set_power(10, False, 25)
        low = music_page.snapshot()
        self.assertTrue(low["paused"])
        self.assertEqual(low["reason"], "Paused: the battery is very low.")
        music_page.set_power(80, False, 46)
        self.assertEqual(music_page.snapshot()["reason"], "Paused: the phone is too hot.")
        with running(self.env) as server:
            server.RequestHandlerClass.phone.sim.level = 10
            server.RequestHandlerClass.phone.sim.health = 1
            token = server.RequestHandlerClass.token
            status, body = get(server, "/api/music", token)
        snap = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(snap["paused"])
        self.assertEqual(snap["reason"], "Paused: the battery is very low.")
        self.assertEqual(snap["albums"][0]["name"], "Kitchen")
        self.assertEqual(snap["albums"][0]["tracks"][0]["title"], "quiet")
        self.assertEqual(snap["albums"][0]["tracks"][0]["seconds"], 1.0)

    def test_the_file_is_served_only_with_the_token_and_a_symlink_is_skipped(self):
        outside = Path(self.tmp.name) / "outside.wav"
        write_wav(outside)
        (self.music / "linked.wav").symlink_to(outside)
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/api/music", token)
            snap = json.loads(body)
            titles = [track["title"] for album in snap["albums"] for track in album["tracks"]]
            self.assertEqual(titles, ["quiet"])
            ident = snap["albums"][0]["tracks"][0]["id"]
            missing, _ = get(server, "/media/music/" + ident)
            self.assertEqual(missing, 403)
            status, audio = get(server, "/media/music/" + ident, token)
            self.assertEqual(status, 200)
            self.assertTrue(audio.startswith(b"RIFF"))
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("GET", "/music")
            response = conn.getresponse()
            policy = response.getheader("Content-Security-Policy") or ""
            response.read()
            conn.close()
        self.assertIn("media-src 'self'", policy)
        self.assertNotIn("http://", policy)

    def test_the_api_token_is_rejected_in_the_media_query(self):
        html = music_page.render("tok", "nonce")
        self.assertIn("audio.src='/media/music/'+track.id;", html)
        self.assertNotIn("?t=", html)
        self.assertNotIn("encodeURIComponent(T)", html)
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            status, body = get(server, "/api/music", token)
            ident = json.loads(body)["albums"][0]["tracks"][0]["id"]
            leaked, _ = get(server, "/media/music/" + ident + "?t=" + token, token)
            self.assertEqual(leaked, 403)
            bare, _ = get(server, "/media/music/" + ident + "?t=" + token)
            self.assertEqual(bare, 403)
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("GET", "/music")
            response = conn.getresponse()
            set_cookie = response.getheader("Set-Cookie") or ""
            response.read()
            conn.close()
            self.assertIn("opos_media=", set_cookie)
            self.assertIn("HttpOnly", set_cookie)
            self.assertIn("SameSite=Strict", set_cookie)
            self.assertNotIn(token, set_cookie)
            grant = set_cookie.split(";", 1)[0].split("=", 1)[1]
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("GET", "/media/music/" + ident, headers={"Cookie": "opos_media=" + grant})
            played = conn.getresponse()
            audio = played.read()
            conn.close()
            self.assertEqual(played.status, 200)
            self.assertTrue(audio.startswith(b"RIFF"))
            self.assertEqual(status, 200)

    def test_the_sleep_timer_is_the_clock_timer_and_does_not_write_a_calendar(self):
        music_page._FAKE = NOW
        with running(self.env) as server:
            token = server.RequestHandlerClass.token
            code, raw = post(server, "/api/music", {"action": "sleep", "minutes": 15}, token)
            snap = json.loads(raw)
            self.assertEqual(code, 200, raw)
            self.assertEqual(snap["sleep"], "Sleep: On")
            self.assertFalse(snap["paused"])
            saved, _note = Store(self.tmp.name).read_json("alarms.json", {})
            self.assertEqual(saved["timer"]["label"], "Sleep")
            fires = datetime.fromisoformat(saved["timer"]["fires_at"])
            self.assertEqual(fires, NOW + timedelta(minutes=15))
            music_page._FAKE = NOW + timedelta(minutes=16)
            status, body = get(server, "/api/music", token)
            later = json.loads(body)
            self.assertEqual(status, 200)
            self.assertTrue(later["paused"])
            self.assertEqual(later["reason"], "Sleep timer: paused")
            code, raw = post(server, "/api/music", {"action": "sleep", "minutes": 0}, token)
            self.assertEqual(json.loads(raw)["sleep"], "")
        self.assertFalse((Path(self.tmp.name) / "calendar.ics").exists())

    def test_the_main_page_does_not_grow_and_a_broken_player_leaves_phone(self):
        before = page()
        with mock.patch.dict(os.environ, {"OPOS_APPS": "music"}):
            self.assertEqual(page(), before)
        served = served_home_page({"OPOS_APPS": "music"})
        self.assertLessEqual(len(served), WAVE_GATE)
        self.assertNotIn(b"Sleep timer", served)
        app = apps.by_id("music")
        original = app.render

        def boom(*_a, **_k):
            raise RuntimeError("broken music")

        app.render = boom
        try:
            with running(self.env) as server:
                status, body = get(server, "/music")
                front, home = get(server, "/")
        finally:
            app.render = original
        self.assertEqual(status, 500)
        self.assertIn(b'id="home"', body)
        self.assertNotIn(b"broken music", body)
        self.assertEqual(front, 200)
        self.assertIn(b">Phone<", home)

    def test_the_helper_cannot_write_music_and_listing_opens_no_socket(self):
        for name in ("assist.py", "advisor.py", "muse.py", "home.py"):
            text = (ROOT / "src/opos" / name).read_text(encoding="utf-8")
            self.assertNotIn("music_page", text)
            self.assertNotIn("OPOS_MUSIC_DIR", text)
        with mock.patch.dict(os.environ, self.env):
            before = set(web.Phone().state())
            after = set(web.Phone().state())
            self.assertEqual(before, after)

            def refuse(*_a, **_k):
                raise AssertionError("connect")

            with mock.patch.object(socket.socket, "connect", refuse), mock.patch("socket.create_connection", refuse):
                music_page.render("tok", "nonce")
                music_page.snapshot()


if __name__ == "__main__":
    unittest.main()
