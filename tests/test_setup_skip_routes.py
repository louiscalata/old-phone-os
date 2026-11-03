# SPDX-License-Identifier: GPL-3.0-or-later
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from opos.setup import prefs
from opos.setup.server import SetupContext, make_server


class SetupSkipRoutesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = {"OPOS_CONFIG_DIR": str(self.root / "config")}
        environment = patch.dict(os.environ, env)
        environment.start()
        self.addCleanup(environment.stop)
        self.path = prefs.prefs_path()
        context = SetupContext(
            repo_root=self.root, python=sys.executable, home=self.root,
            platform=sys.platform, env=env, dry_run=True,
        )
        self.server = make_server(0, context)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=2)

    def post(self, route, payload, token=True):
        port = self.server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
        }
        if token:
            headers["X-OPOS-Token"] = self.server.token
        try:
            connection.request("POST", route, json.dumps(payload), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_invalid_step_returns_400_without_changing_preferences(self):
        prefs.answer("theme", "dark", self.path)
        before = self.path.read_bytes()
        for body in ({"step": "bogus"}, {}, {"step": None}, {"step": ["theme"]}):
            with self.subTest(body=body):
                status, data = self.post("/setup/api/prefs/skip", body)
                self.assertEqual(status, 400)
                self.assertIn("unknown step", data["error"])
                self.assertEqual(self.path.read_bytes(), before)

    def test_valid_skip_removes_answer_and_keeps_setup_unapplied(self):
        prefs.answer("theme", "dark", self.path)
        self.assertEqual(self.post("/setup/api/prefs/skip", {"step": "theme"}), (200, {}))
        state = prefs.load(self.path)
        self.assertNotIn("theme", state["answers"])
        self.assertEqual(state["skipped"], ["theme"])
        self.assertFalse(state["applied"])

    def test_skip_requires_token_before_validation(self):
        status, _ = self.post("/setup/api/prefs/skip", {"step": "bogus"}, token=False)
        self.assertEqual(status, 403)
        self.assertFalse(self.path.exists())

    def test_skip_all_preserves_answers_and_writes_stable_order(self):
        prefs.answer("theme", "dark", self.path)
        prefs.skip("helper", self.path)
        self.assertEqual(self.post("/setup/api/prefs/skip-all", {}), (200, {}))
        state = prefs.load(self.path)
        self.assertEqual(state["answers"], {"theme": "dark"})
        self.assertEqual(state["skipped"], sorted(set(prefs.STEPS) - {"theme"}))
        self.assertIsNone(prefs.resume_step(state))
        self.assertFalse(state["applied"])
        first = self.path.read_bytes()
        self.assertEqual(self.post("/setup/api/prefs/skip-all", {}), (200, {}))
        self.assertEqual(self.path.read_bytes(), first)
