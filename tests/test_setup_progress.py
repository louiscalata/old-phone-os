# SPDX-License-Identifier: GPL-3.0-or-later
"""Setup progress contracts and deterministic completion regressions, without sockets."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from opos.setup import apply as apply_mod
from opos.setup import cli, config, server
from opos.setup.apply import Progress


STEPS = (
    ("config", "Save your preferences"),
    ("token", "Create the session token"),
    ("key", "Set aside your SSH key"),
    ("services", "Install the background service"),
    ("health", "Check that it is running"),
    ("tools", "Install AI coding tools"),
)
SAVED_DEFAULTS = b'''{
  "ai_tools": {
    "mode": "lite",
    "tools": []
  },
  "applied": true,
  "battery_mode": "auto",
  "device": {
    "battery_health": null,
    "id": "unknown",
    "name": "Unknown device",
    "source": "stub"
  },
  "goal": {
    "enabled": true,
    "time": "23:00"
  },
  "security": {
    "ssh_public_key": null
  },
  "services": [
    "calls",
    "sms",
    "alarms",
    "apps"
  ],
  "version": 1
}
'''


class SetupProgressCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {"OPOS_CONFIG_DIR": str(self.root / "config")}
        self.ctx = server.SetupContext(
            repo_root=self.root, python=sys.executable, home=self.root,
            platform="linux", env=self.env, runner=Mock(),
            health_check=lambda port: (True, "healthy"),
        )
        # Capture the actual handler and its closure without binding any socket.
        with patch.object(server, "ThreadingHTTPServer") as factory:
            factory.return_value.server_address = ("127.0.0.1", 8766)
            self.server = server.make_server(8766, self.ctx)
            handler_type = factory.call_args.args[1]
        self.handler = handler_type.__new__(handler_type)
        self.handler.client_address = ("127.0.0.1", 12345)
        self.handler._shrink_timeout = Mock()

    def request(self, method="GET", path="/setup/api/progress", body=None):
        handler = self.handler
        raw = json.dumps(body).encode() if body is not None else b""
        handler.command, handler.path = method, path
        handler.headers = {
            "Host": "127.0.0.1:8766", "X-OPOS-Token": self.server.token,
            "Origin": "http://127.0.0.1:8766", "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
        }
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        handler._dispatch()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue()

    def run_apply(self, cfg=None, **kwargs):
        with patch.object(apply_mod, "install_services", return_value=["service ready"]):
            return apply_mod.apply(
                config.default_config() if cfg is None else cfg,
                repo_root=self.root, python=sys.executable, home=self.root,
                platform="linux", env=self.env, runner=self.ctx.runner,
                health_check=self.ctx.health_check, **kwargs,
            )


class CharacterizationTests(SetupProgressCase):
    def test_idle_payload_bytes(self):
        self.assertEqual(self.request(), (200, b'{"running": false, "done": false, "ok": false, "steps": []}'))

    def test_status_words_order_labels_and_aggregate_values(self):
        cases = (
            (("pending",) * 6, False, False),
            (("running",) + ("pending",) * 5, False, False),
            (("failed",) + ("pending",) * 5, False, False),
            (("ok",) * 6, True, True),
            (("skipped",) * 6, True, True),
            (("ok",) * 5 + ("failed",), True, False),
        )
        for statuses, done, ok in cases:
            with self.subTest(statuses=statuses):
                progress = Progress()
                for (sid, _), status in zip(STEPS, statuses):
                    if status != "pending":
                        method = {"running": "start", "ok": "finish", "failed": "fail", "skipped": "skip"}[status]
                        args = (sid,) if status == "running" else (sid, "detail")
                        getattr(progress, method)(*args)
                self.server.progress_state.progress = progress
                expected = {"running": not done, "done": done, "ok": ok, "steps": [
                    {"id": sid, "label": label, "status": status,
                     "detail": "" if status in ("pending", "running") else "detail"}
                    for (sid, label), status in zip(STEPS, statuses)
                ]}
                self.assertEqual(self.request(), (200, json.dumps(expected).encode()))
                self.assertEqual((progress.done(), progress.ok()), (done, ok))

    def test_apply_http_codes_and_retry_replaces_progress(self):
        with patch.object(server.threading, "Thread") as thread:
            body = {"config": config.default_config()}
            self.assertEqual(self.request("POST", "/setup/api/apply", body), (202, b'{"started": true}'))
            first = self.server.progress_state.progress
            self.assertEqual(self.request("POST", "/setup/api/apply", body), (409, b'{"error": "already running"}'))
            self.assertEqual(thread.return_value.start.call_count, 1)
            for sid, _ in STEPS:
                first.skip(sid)
            self.assertEqual(self.request("POST", "/setup/api/apply", body), (202, b'{"started": true}'))
            self.assertIsNot(self.server.progress_state.progress, first)
            self.assertEqual(thread.return_value.start.call_count, 2)
        self.assertEqual(self.request("POST", "/setup/api/apply", {"config": None}),
                         (400, b'{"ok": false, "errors": ["config must be an object"]}'))

    def test_saved_config_and_existing_token_bytes(self):
        token = config.token_path(self.env, "linux")
        token.parent.mkdir()
        token.write_bytes(b"t" * 32 + b"\n")
        progress = self.run_apply()
        self.assertTrue(progress.ok())
        self.assertEqual(config.config_path(self.env, "linux").read_bytes(), SAVED_DEFAULTS)
        self.assertEqual(token.read_bytes(), b"t" * 32 + b"\n")
        self.ctx.runner.assert_not_called()

    def test_failed_optional_tools_still_persist_core_applied(self):
        cfg = config.default_config()
        cfg["ai_tools"]["tools"] = ["fake-tool"]
        progress = self.run_apply(cfg, tools_runner=Mock(side_effect=ValueError("private")))
        self.assertTrue(progress.done())
        self.assertFalse(progress.ok())
        self.assertEqual(progress.snapshot()[-1], {
            "id": "tools", "label": "Install AI coding tools", "status": "failed", "detail": "ValueError",
        })
        self.assertTrue(config.load(config.config_path(self.env, "linux"))["applied"])

    def test_cli_exact_output_and_exit_codes(self):
        for failed, dry_run in ((False, False), (True, False), (False, True)):
            with self.subTest(failed=failed, dry_run=dry_run):
                progress = Progress()
                for sid, _ in STEPS:
                    progress.skip(sid)
                if failed:
                    # Construct the failure before terminalizing this step.
                    progress = Progress()
                    progress.fail("config", "OSError")
                    for sid, _ in STEPS[1:]:
                        progress.skip(sid)
                out = io.StringIO()
                with patch.object(cli, "reboot_warning", return_value=None), \
                     patch.object(cli.opos.setup.device, "detect_device", return_value=SimpleNamespace()), \
                     patch.object(cli.opos.setup.device, "to_config_device", return_value=None), \
                     patch.object(apply_mod, "apply", return_value=progress):
                    args = ["run", "--defaults"] + (["--dry-run"] if dry_run else [])
                    rc = cli.main(args, env=self.env, home=self.root, platform="linux", out=out)
                if failed:
                    expected = "step Save your preferences: OSError\nsetup failed\n"
                elif dry_run:
                    expected = "Dry run finished. Nothing was configured or started.\n"
                else:
                    expected = ("Old Phone OS Interface is set up with the default settings. Device: Unknown device\n"
                                "Open it at http://127.0.0.1:8765/\n")
                self.assertEqual((rc, out.getvalue()), (int(failed), expected))


class CompletionRegressionTests(SetupProgressCase):
    def test_completion_after_snapshot_keeps_response_consistent(self):
        class CompletingProgress(Progress):
            complete_after_copy = True

            def snapshot(self):
                rows = super().snapshot()
                if self.complete_after_copy:
                    self.complete_after_copy = False
                    # The worker gets its turn just after snapshot releases the lock.
                    for sid, _ in STEPS:
                        self.finish(sid)
                return rows

        progress = CompletingProgress()
        progress.start("config")
        self.server.progress_state.progress = progress
        status, raw = self.request()
        result = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(result["steps"][0]["status"], "running")
        self.assertEqual((result["running"], result["done"], result["ok"]), (True, False, False))
        next_result = json.loads(self.request()[1])
        self.assertEqual((next_result["running"], next_result["done"], next_result["ok"]), (False, True, True))

    def test_final_save_failure_is_a_config_failure_and_cli_failure(self):
        real_save = apply_mod.save

        def save(cfg, path):
            if cfg["applied"]:
                raise OSError("private disk detail")
            return real_save(cfg, path)

        with patch.object(apply_mod, "save", side_effect=save):
            progress = self.run_apply()
        self.assertEqual(progress.snapshot()[0], {
            "id": "config", "label": "Save your preferences", "status": "failed", "detail": "OSError",
        })
        self.assertTrue(progress.done())
        self.assertFalse(progress.ok())
        self.assertFalse(config.load(config.config_path(self.env, "linux"))["applied"])
        self.server.progress_state.progress = progress
        payload = json.loads(self.request()[1])
        self.assertEqual((payload["running"], payload["done"], payload["ok"]), (False, True, False))
        out = io.StringIO()
        with patch.object(cli, "reboot_warning", return_value=None), \
             patch.object(cli.opos.setup.device, "to_config_device", return_value=None), \
             patch.object(cli.opos.setup.device, "detect_device", return_value=None), \
             patch.object(apply_mod, "apply", return_value=progress):
            rc = cli.main(["run", "--defaults"], env=self.env, home=self.root, platform="linux", out=out)
        self.assertEqual(rc, 1)
        self.assertTrue(out.getvalue().endswith("step Save your preferences: OSError\nsetup failed\n"))
        self.assertNotIn("private", out.getvalue())

    def test_final_save_must_settle_before_completion_or_retry(self):
        real_save = apply_mod.save
        progress = Progress()
        self.server.progress_state.progress = progress
        observations = []

        def save(cfg, path):
            if cfg["applied"]:
                snapshot = json.loads(self.request()[1])
                retry = self.request("POST", "/setup/api/apply", {"config": config.default_config()})
                observations.append((snapshot, retry))
            return real_save(cfg, path)

        with patch.object(apply_mod, "save", side_effect=save), patch.object(server.threading, "Thread") as thread:
            result = self.run_apply(progress=progress)
        self.assertEqual(len(observations), 1)
        payload, retry = observations[0]
        self.assertEqual((payload["running"], payload["done"], payload["ok"]), (True, False, False))
        self.assertEqual(retry, (409, b'{"error": "already running"}'))
        thread.assert_not_called()
        self.assertIs(self.server.progress_state.progress, progress)
        self.assertTrue(result.ok())


class TransitionTests(unittest.TestCase):
    def test_step_transition_matrix(self):
        events = {"running": "start", "ok": "finish", "failed": "fail", "skipped": "skip"}

        def move(progress, state):
            args = ("token",) if state == "running" else ("token", "detail")
            getattr(progress, events[state])(*args)

        for before in ("pending", *events):
            for after in events:
                with self.subTest(before=before, after=after):
                    progress = Progress()
                    if before != "pending":
                        move(progress, before)
                    if before in ("pending", "running", after):
                        move(progress, after)
                        self.assertEqual(progress.snapshot()[1]["status"], after)
                    else:
                        original = progress.snapshot()
                        with self.assertRaises(ValueError):
                            move(progress, after)
                        self.assertEqual(progress.snapshot(), original)

    def test_config_final_save_failure_is_the_only_terminal_outcome_change(self):
        progress = Progress()
        progress.finish("config", "initial save")
        progress.fail("config", "OSError")
        self.assertEqual(progress.snapshot()[0]["status"], "failed")
        with self.assertRaises(ValueError):
            progress.finish("config", "retry needs a new Progress")
