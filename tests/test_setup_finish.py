# SPDX-License-Identifier: GPL-3.0-or-later
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opos import web
from opos.setup import prefs
from opos.setup.server import SetupContext, make_server


class TestSetupFinish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, OPOS_CONFIG_DIR=str(self.directory))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.runner = mock.Mock(side_effect=AssertionError("preferences must not execute commands"))
        self.ctx = SetupContext(repo_root=self.directory, python=sys.executable,
                                home=self.directory, platform=sys.platform,
                                env={"OPOS_CONFIG_DIR": str(self.directory)},
                                dry_run=True, runner=self.runner)
        self._start_server()
        self.addCleanup(self._stop_server)

    def _start_server(self):
        self.server = make_server(0, self.ctx)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def _request(self, path, payload=None, *, token=True, origin=None):
        headers = {"X-OPOS-Token": self.server.token if token else "invalid"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers.update({"Content-Type": "application/json",
                            "Origin": self.base if origin is None else origin})
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=2)
        try:
            connection.request("GET" if payload is None else "POST", path,
                               body=data, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_finish_persists_and_kiosk_honors_choices_after_reopen(self):
        choices = {"theme": "dark", "text_size": "large", "device_type": "tablet",
                   "helper": "on", "wifi": {"ssid": "private-setup-network"}}
        for step, value in choices.items():
            status, _ = self._request("/setup/api/prefs", {"step": step, "value": value})
            self.assertEqual(status, 200)
        self.assertEqual(web.Phone().state()["prefs"], prefs.defaults())
        self.assertFalse(web._install_helper_visible(False))
        self.assertEqual(self._request("/setup/api/prefs/skip-all", {})[0], 200)
        status, result = self._request("/setup/api/prefs/finish", {})
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"state", "resume", "effective"})
        self.assertTrue(result["state"]["applied"])
        self.assertIsNone(result["resume"])
        persisted = json.loads((self.directory / "prefs.json").read_text())
        self.assertEqual(persisted, result["state"])
        self.assertEqual(persisted["answers"], choices)
        self.assertEqual(set(persisted["skipped"]), set(prefs.STEPS) - set(choices))
        expected = prefs.defaults() | choices
        self.assertEqual(result["effective"], expected)
        self._stop_server()
        self._start_server()
        self.assertEqual(self._request("/setup/api/prefs"), (200, result))
        expected["wifi"] = prefs.defaults()["wifi"]
        self.assertEqual(web.Phone().state()["prefs"], expected)
        self.assertTrue(web._install_helper_visible(False))
        self.runner.assert_not_called()

    def test_finish_rejects_invalid_token_and_origin_without_persisting(self):
        path = "/setup/api/prefs/finish"
        self.assertEqual(self._request(path, {}, token=False)[0], 403)
        self.assertEqual(self._request(path, {}, origin="https://example.com")[0], 403)
        self.assertFalse((self.directory / "prefs.json").exists())
        self.assertFalse(web._install_helper_visible(False))
        self.runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
