# SPDX-License-Identifier: GPL-3.0-or-later
"""Setup health checks against the real state-token gate, without sockets."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from opos.setup import apply as apply_mod, cli, config
from tests.test_security_fixes import handler, response, result


def state_opener(server_token):
    """Adapt urllib requests to the real in-memory web.Handler."""
    def open_state(request, timeout=None):
        if isinstance(request, str):
            request = urllib.request.Request(request)
        h = handler(token=request.get_header("X-opos-token"), host=request.host)
        h.path = request.selector
        h.token = server_token() if callable(server_token) else server_token
        h.do_GET()
        code, body = result(h)
        return response(request, body, code=code)
    return open_state


class HttpHealthTokenTests(unittest.TestCase):
    def test_http_health_accepts_valid_state_token(self):
        token = "t" * 43
        opener = mock.Mock(side_effect=state_opener(token))
        ok, detail = apply_mod.http_health(8765, timeout_s=0, opener=opener, token=token)
        self.assertTrue(ok)
        self.assertEqual(detail, "responding on port 8765")
        request = opener.call_args.args[0]
        self.assertIsInstance(request, urllib.request.Request)
        self.assertEqual(request.full_url, "http://127.0.0.1:8765/api/state")
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.get_header("X-opos-token") == token)
        self.assertNotIn(token, detail)

    def test_http_health_rejects_missing_state_token(self):
        ok, detail = apply_mod.http_health(8765, timeout_s=0, opener=state_opener("t" * 43))
        self.assertFalse(ok)
        self.assertEqual(detail, "no answer on port 8765 after 0 s")

    def test_http_health_rejects_wrong_state_token(self):
        token = "w" * 43  # a well-formed token that is not the server's
        ok, detail = apply_mod.http_health(8765, timeout_s=0, opener=state_opener("t" * 43), token=token)
        self.assertFalse(ok)
        self.assertNotIn(token, detail)


class SetupTokenTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = {"HOME": str(self.root), "OPOS_CONFIG_DIR": str(self.root / "config")}

    def test_apply_default_checker_receives_created_token(self):
        real_health = apply_mod.http_health
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                tpath = config.token_path(self.env, platform)
                tpath.unlink(missing_ok=True)
                opener = state_opener(lambda: tpath.read_text(encoding="utf-8").strip())

                def health(port, **kwargs):
                    return real_health(port, timeout_s=0, opener=opener, **kwargs)

                cfg = config.default_config()
                cfg["ai_tools"] = {"tools": ["claude"], "mode": "lite"}
                tools = mock.Mock(return_value=["installed selected tool"])
                with mock.patch.object(apply_mod, "install_services", return_value=["started"]), \
                        mock.patch.object(apply_mod, "http_health", side_effect=health) as checker:
                    progress = apply_mod.apply(cfg, repo_root=self.root, python=sys.executable,
                                               home=self.root, platform=platform, env=self.env,
                                               tools_runner=tools)
                self.assertTrue(progress.ok(), progress.snapshot())
                checker.assert_called_once()
                self.assertEqual(checker.call_args.args, (8765,))
                self.assertIn("token", checker.call_args.kwargs)
                token = tpath.read_text(encoding="utf-8").strip()
                self.assertTrue(token)
                self.assertTrue(checker.call_args.kwargs["token"] == token)
                tools.assert_called_once()
                self.assertTrue(config.load(config.config_path(self.env, platform))["applied"])
                self.assertNotIn(token, json.dumps(progress.snapshot()))

    def test_apply_injected_checker_keeps_one_argument(self):
        calls = []

        def checker(port):
            calls.append(port)
            return True, "responding"

        with mock.patch.object(apply_mod, "install_services", return_value=["started"]), \
                mock.patch.object(apply_mod, "http_health") as default_checker:
            progress = apply_mod.apply(config.default_config(), repo_root=self.root,
                                       python=sys.executable, home=self.root, platform="linux",
                                       env=self.env, port=8123, health_check=checker)
        self.assertTrue(progress.ok())
        self.assertEqual(calls, [8123])
        default_checker.assert_not_called()

    def test_status_reads_existing_token_without_printing_or_changing_it(self):
        real_health = apply_mod.http_health
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                # --config-dir must select both config and token over the environment.
                directory = self.root / platform
                effective_env = {**self.env, "OPOS_CONFIG_DIR": str(directory)}
                tpath = config.token_path(effective_env, platform)
                tpath.parent.mkdir(parents=True)
                token = "t" * 43
                contents = (token + "\n").encode()
                tpath.write_bytes(contents)
                out = io.StringIO()

                def health(port, **kwargs):
                    kwargs["timeout_s"] = 0
                    return real_health(port, opener=state_opener(token), **kwargs)

                with mock.patch.object(apply_mod, "http_health", side_effect=health) as checker, \
                        mock.patch.object(config, "ensure_token") as ensure:
                    rc = cli.main(["status", "--config-dir", str(directory)], env=self.env,
                                  home=self.root, platform=platform, out=out)
                self.assertEqual(rc, 0)
                self.assertIn("running: yes", out.getvalue())
                self.assertNotIn(token, out.getvalue())
                self.assertEqual(tpath.read_bytes(), contents)
                self.assertIn("token", checker.call_args.kwargs)
                self.assertTrue(checker.call_args.kwargs["token"] == token)
                ensure.assert_not_called()

    def test_status_missing_token_still_probes_without_creating_file(self):
        tpath = config.token_path(self.env, "linux")
        for running in (True, False):
            with self.subTest(running=running):
                out = io.StringIO()
                with mock.patch.object(apply_mod, "http_health", return_value=(running, "probe")) as checker, \
                        mock.patch.object(config, "ensure_token") as ensure:
                    rc = cli.main(["status"], env=self.env, home=self.root, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn(f"running: {'yes' if running else 'no'}", out.getvalue())
                checker.assert_called_once_with(8765, timeout_s=1.0, interval_s=0.2, token=None)
                ensure.assert_not_called()
                self.assertFalse(tpath.exists())
                self.assertFalse(tpath.parent.exists())

    def test_status_unreadable_token_still_probes(self):
        tpath = config.token_path(self.env, "linux")
        tpath.parent.mkdir(parents=True)
        tpath.write_text("unchanged", encoding="utf-8")
        read_text = Path.read_text

        def read(path, *args, **kwargs):
            if path == tpath:
                raise PermissionError("private token file detail")
            return read_text(path, *args, **kwargs)

        for running in (True, False):
            with self.subTest(running=running):
                out = io.StringIO()
                with mock.patch.object(Path, "read_text", read), \
                        mock.patch.object(apply_mod, "http_health", return_value=(running, "probe")) as checker, \
                        mock.patch.object(config, "ensure_token") as ensure:
                    rc = cli.main(["status"], env=self.env, home=self.root, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn(f"running: {'yes' if running else 'no'}", out.getvalue())
                self.assertNotIn("private token file detail", out.getvalue())
                checker.assert_called_once_with(8765, timeout_s=1.0, interval_s=0.2, token=None)
                ensure.assert_not_called()
                self.assertEqual(tpath.read_text(encoding="utf-8"), "unchanged")

    def test_status_invalid_utf8_token_still_probes(self):
        tpath = config.token_path(self.env, "linux")
        tpath.parent.mkdir(parents=True)
        tpath.write_bytes(b"\xff\xfe")
        out = io.StringIO()
        with mock.patch.object(apply_mod, "http_health", return_value=(True, "probe")) as checker:
            rc = cli.main(["status"], env=self.env, home=self.root, platform="linux", out=out)
        self.assertEqual(rc, 0)
        self.assertIn("running: yes", out.getvalue())
        checker.assert_called_once_with(8765, timeout_s=1.0, interval_s=0.2, token=None)
        self.assertEqual(tpath.read_bytes(), b"\xff\xfe")


if __name__ == "__main__":
    unittest.main()
