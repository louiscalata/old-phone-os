# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulated volume publication and HTTP/UI integration, without a browser."""
import http.client
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opos import volume, web
from tests.test_uxp30_budget import SWITCHES, WAVE_GATE, served_home_page

ROOT = Path(__file__).resolve().parents[1]


class State(unittest.TestCase):
    def test_atomic_publication_readback_permissions_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            self.assertIsNone(volume.read_percent(path))
            for percent in (0, 5, 60, 100):
                volume.write_percent(path, percent)
                self.assertEqual(volume.read_percent(path), percent)
                self.assertEqual(json.loads(path.read_text()), {"simulated": True, "percent": percent})
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                self.assertEqual(path.stat().st_uid, os.getuid())
                self.assertEqual(list(Path(folder).iterdir()), [path])
            with mock.patch.object(volume.os, "replace", side_effect=OSError("private failure")):
                with self.assertRaises(OSError):
                    volume.write_percent(path, 55)
            self.assertEqual(volume.read_percent(path), 100)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_invalid_writes_do_not_touch_previous_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            volume.write_percent(path, 60)
            for percent in (True, -1, 101, 60.0, None, "55"):
                with self.subTest(percent=percent), self.assertRaises(ValueError):
                    volume.write_percent(path, percent)
                self.assertEqual(volume.read_percent(path), 60)

    def test_bad_or_unreadable_reports_are_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            for data in ("", "{", "[]", "null", '{"percent":60}',
                         '{"simulated":false,"percent":60}',
                         '{"simulated":true,"percent":true}',
                         '{"simulated":true,"percent":-1}',
                         '{"simulated":true,"percent":101}',
                         '{"simulated":true,"percent":60.0}',
                         '{"simulated":true,"percent":"60"}'):
                path.write_text(data)
                self.assertIsNone(volume.read_percent(path), data)
            path.write_bytes(b"\xff")
            self.assertIsNone(volume.read_percent(path))
            with mock.patch.object(Path, "open", side_effect=PermissionError("private")):
                self.assertIsNone(volume.read_percent(path))

    def test_default_phone_state_and_page_stay_unchanged(self):
        with mock.patch.dict(os.environ, {"OPOS_VOLUME_KEYS": ""}):
            self.assertNotIn("volume_keys", web.Phone().state())
            body = web.page().encode()
            self.assertIs(volume.inject_bytes(body, "n"), body)
            self.assertNotIn(b"/volume.js", body)

    def test_opt_in_state_recovers_without_recreating_phone(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"OPOS_VOLUME_KEYS": "1"}):
            path = Path(folder) / "state.json"
            with mock.patch.object(volume, "STATE", path):
                phone = web.Phone()
                self.assertEqual(phone.state()["volume_keys"], {"simulated": True, "percent": None})
                volume.write_percent(path, 55)
                self.assertEqual(phone.state()["volume_keys"], {"simulated": True, "percent": 55})
                path.unlink()
                self.assertIsNone(phone.state()["volume_keys"]["percent"])
                volume.write_percent(path, 65)
                self.assertEqual(phone.state()["volume_keys"]["percent"], 65)
                self.assertFalse(web.apply_phone_action(phone, {"action": "volume", "percent": 100}))
                self.assertEqual(volume.read_percent(path), 65)

    def test_served_page_with_volume_and_all_existing_switches_fits_gate(self):
        with mock.patch.dict(os.environ, {"OPOS_VOLUME_KEYS": "1"}):
            body = served_home_page(SWITCHES)
        self.assertIn(b'src="/volume.js"', body)
        self.assertLessEqual(len(body), WAVE_GATE)
        print(f"served, volume and all switches: {len(body):,} bytes; gate: {WAVE_GATE:,}")


class Http(unittest.TestCase):
    def test_asset_state_auth_refresh_and_head(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"OPOS_VOLUME_KEYS": "1"}):
            path = Path(folder) / "state.json"
            with mock.patch.object(volume, "STATE", path):
                token = "v" * 43
                server = web.make_server(0, web.Phone(), token=token)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    def request(method, route, headers=None):
                        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                        conn.request(method, route, headers=headers or {})
                        response = conn.getresponse()
                        result = response.status, dict(response.getheaders()), response.read()
                        conn.close()
                        return result

                    status, headers, body = request("GET", "/")
                    self.assertEqual(status, 200)
                    self.assertIn(b'src="/volume.js"', body)
                    self.assertRegex(body, rb'src="/volume.js" nonce="[A-Za-z0-9_-]+" defer')
                    self.assertIn("Content-Security-Policy", headers)
                    status, headers, body = request("GET", "/volume.js")
                    self.assertEqual(status, 200)
                    self.assertIn(b"simulated; sound is not changed", body)
                    self.assertEqual(len(body), int(headers["Content-Length"]))
                    self.assertEqual(request("HEAD", "/volume.js")[2], b"")
                    self.assertEqual(request("GET", "/api/state")[0], 403)
                    for percent in (60, 55, 100):
                        volume.write_percent(path, percent)
                        status, _, body = request("GET", "/api/state", {"X-OPOS-Token": token})
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["volume_keys"], {"simulated": True, "percent": percent})
                    with mock.patch.dict(os.environ, {"OPOS_VOLUME_KEYS": ""}):
                        self.assertEqual(request("GET", "/volume.js")[0], 404)
                        self.assertNotIn(b'src="/volume.js"', request("GET", "/")[2])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(5)


class Script(unittest.TestCase):
    def test_actual_script_with_fake_dom_and_existing_render_paint(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node unavailable; browser was not requested")
        result = subprocess.run([node, str(ROOT / "tests/volume_ui_test.js"),
                                 str(ROOT / "src/opos/web_static/volume.js"),
                                 str(ROOT / "src/opos/web_static/js-3.js")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
