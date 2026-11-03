# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for opos.setup.apply."""

from __future__ import annotations

import ast
import copy
import json
import os
import platform
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opos.setup import apply as apply_mod
from opos.setup.apply import STEP_IDS, STEP_LABELS, Progress, apply, http_health, uninstall
from opos.setup.config import config_dir, config_path, default_config, token_path

try:
    from tests.test_setup_config import SSH_PUBLIC_KEYS
except ImportError:
    from test_setup_config import SSH_PUBLIC_KEYS


class FakeRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self.commands: list[object] = []
        self.error = error

    def __call__(self, command, *args, **kwargs):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return (0, '')


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def make_cfg(key: str | None = None) -> dict:
    cfg = copy.deepcopy(default_config())
    cfg["security"]["ssh_public_key"] = key
    return cfg


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        src = Path(__file__).resolve().parents[1] / "deploy" / "opos-server.service"
        dst = self.repo / "deploy" / "opos-server.service"
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            dst.write_bytes(src.read_bytes())
        except FileNotFoundError:
            dst.write_text("[Unit]\nDescription=opos\n", encoding="utf-8")
        self.tools_prefix = self.root / "tools"
        self.tools_prefix.mkdir(parents=True, exist_ok=True)
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.root / "xdg"), "OPOS_TOOLS_PREFIX": str(self.tools_prefix)}
        self.runner = FakeRunner()

    def cfg_dir(self, platform: str = "linux") -> Path:
        return config_dir(self.env, platform)

    def run_apply(self, cfg=None, platform="linux", **kwargs):
        kwargs.setdefault("repo_root", self.repo)
        kwargs.setdefault("python", sys.executable)
        kwargs.setdefault("home", self.home)
        kwargs.setdefault("platform", platform)
        kwargs.setdefault("env", self.env)
        kwargs.setdefault("runner", self.runner)
        kwargs.setdefault("health_check", lambda port: (True, "responding on port %d" % port))
        return apply(cfg if cfg is not None else make_cfg(), **kwargs)

    @staticmethod
    def by_id(progress: Progress) -> dict[str, dict]:
        return {step["id"]: step for step in progress.snapshot()}


class ProgressTests(unittest.TestCase):
    def test_initial_snapshot_labels_and_copy(self) -> None:
        progress = Progress()
        snap = progress.snapshot()
        self.assertEqual([s["id"] for s in snap], list(STEP_IDS))
        self.assertEqual({s["id"]: s["label"] for s in snap}, STEP_LABELS)
        self.assertTrue(all(s["status"] == "pending" and s["detail"] == "" for s in snap))
        self.assertFalse(progress.done())
        self.assertFalse(progress.ok())
        snap[0]["status"] = "mutated"
        self.assertEqual(progress.snapshot()[0]["status"], "pending")

    def test_transitions_truncation_and_control_chars(self) -> None:
        progress = Progress()
        progress.start("config")
        self.assertEqual(progress.snapshot()[0]["status"], "running")
        progress.finish("config", "a\x00b\nc" + "x" * 400)
        step = progress.snapshot()[0]
        self.assertEqual(step["status"], "ok")
        self.assertEqual(len(step["detail"]), 300)
        self.assertNotIn("\x00", step["detail"])
        self.assertNotIn("\n", step["detail"])
        progress.fail("token", "bad")
        progress.skip("key")
        self.assertEqual(progress.snapshot()[1]["status"], "failed")
        self.assertEqual(progress.snapshot()[2]["status"], "skipped")
        self.assertFalse(progress.done())
        for step_id in ("services", "health", "tools"):
            progress.finish(step_id)
        self.assertTrue(progress.done())
        self.assertFalse(progress.ok())

    def test_unknown_step_and_thread_safety(self) -> None:
        progress = Progress()
        with self.assertRaises(KeyError):
            progress.start("missing")
        threads = [threading.Thread(target=progress.finish, args=(step_id, "done")) for step_id in STEP_IDS]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(progress.ok())


class ApplyTests(BaseCase):
    def test_success_all_steps_and_applied_saved(self) -> None:
        progress = self.run_apply(make_cfg(SSH_PUBLIC_KEYS["ssh-ed25519"]))
        steps = self.by_id(progress)
        self.assertTrue(progress.ok())
        self.assertEqual(steps["config"]["status"], "ok")
        self.assertEqual(steps["token"]["detail"], "ready")
        self.assertIn("sudo deploy/harden-device.sh --apply --only ssh --add-key", steps["key"]["detail"])
        self.assertIn("--only ssh", steps["key"]["detail"])
        self.assertEqual(steps["services"]["status"], "ok")
        self.assertEqual(steps["health"]["status"], "ok")
        saved = json.loads(config_path(self.env, "linux").read_text(encoding="utf-8"))
        self.assertTrue(saved["applied"])
        self.assertTrue(self.runner.commands)

    def test_no_key_skips_key_step(self) -> None:
        steps = self.by_id(self.run_apply())
        self.assertEqual(steps["key"]["status"], "skipped")
        self.assertEqual(steps["key"]["detail"], "no key given")

    def test_token_never_in_details(self) -> None:
        progress = self.run_apply()
        token = token_path(self.env, "linux").read_text(encoding="utf-8").strip()
        self.assertTrue(token)
        for step in progress.snapshot():
            self.assertNotIn(token, step["detail"])

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes not honoured on Windows")
    def test_key_file_mode_content_and_no_ssh_dir(self) -> None:
        key = SSH_PUBLIC_KEYS["ssh-ed25519"]
        self.run_apply(make_cfg(key))
        key_path = self.cfg_dir() / "authorized_key.pub"
        self.assertEqual(key_path.read_text(encoding="utf-8"), key + "\n")
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.cfg_dir().stat().st_mode), 0o700)
        self.assertFalse((self.home / ".ssh").exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes not honoured on Windows")
    def test_logs_dir_mode(self) -> None:
        self.run_apply()
        logs = self.cfg_dir() / "logs"
        self.assertTrue(logs.is_dir())
        self.assertEqual(stat.S_IMODE(logs.stat().st_mode), 0o700)

    def test_dry_run_writes_nothing(self) -> None:
        progress = self.run_apply(make_cfg(SSH_PUBLIC_KEYS["ssh-ed25519"]), dry_run=True)
        steps = self.by_id(progress)
        self.assertTrue(steps["config"]["detail"].startswith("would write "))
        self.assertEqual(steps["token"]["detail"], "would create the token file")
        self.assertEqual(steps["key"]["detail"], "would save the key file")
        self.assertEqual(steps["health"]["status"], "skipped")
        self.assertEqual(steps["health"]["detail"], "dry run")
        self.assertFalse(self.cfg_dir().exists())
        self.assertEqual(self.runner.commands, [])

    def test_invalid_config_fails_and_skips_rest(self) -> None:
        cfg = make_cfg()
        cfg["version"] = "not-a-version"
        progress = self.run_apply(cfg)
        steps = self.by_id(progress)
        self.assertEqual(steps["config"]["status"], "failed")
        self.assertTrue(steps["config"]["detail"])
        for step_id in STEP_IDS[1:]:
            self.assertEqual(steps[step_id]["status"], "skipped")
            self.assertEqual(steps[step_id]["detail"], "skipped after an earlier failure")
        self.assertFalse(progress.ok())

    def test_service_failure_skips_health(self) -> None:
        progress = self.run_apply(python="")
        steps = self.by_id(progress)
        if steps["services"]["status"] != "failed":
            self.runner = FakeRunner(RuntimeError("boom"))
            progress = self.run_apply(runner=self.runner)
            steps = self.by_id(progress)
        self.assertEqual(steps["services"]["status"], "failed")
        self.assertEqual(steps["health"]["status"], "skipped")
        self.assertEqual(steps["health"]["detail"], "skipped after an earlier failure")

    def test_health_failure_and_unsupported_platform(self) -> None:
        progress = self.run_apply(health_check=lambda port: (False, "no answer on port 8765 after 10 s"))
        self.assertEqual(self.by_id(progress)["health"]["status"], "failed")
        progress = self.run_apply(platform="windows")
        steps = self.by_id(progress)
        self.assertEqual(steps["health"]["status"], "skipped")
        self.assertEqual(steps["health"]["detail"], "no service on windows")

    def test_unexpected_health_error_reports_type_only(self) -> None:
        def boom(port):
            raise RuntimeError("private detail text")

        progress = self.run_apply(health_check=boom)
        step = self.by_id(progress)["health"]
        self.assertEqual(step["status"], "failed")
        self.assertEqual(step["detail"], "RuntimeError")
        self.assertNotIn("private", step["detail"])

    def test_supplied_progress_is_returned(self) -> None:
        progress = Progress()
        result = self.run_apply(progress=progress)
        self.assertIs(result, progress)

    def test_apply_never_raises_on_bad_cfg_shape(self) -> None:
        progress = apply_mod.apply({}, repo_root=self.repo, python=sys.executable, home=self.home, platform="linux", env=self.env, runner=self.runner, health_check=lambda port: (True, "ok"))
        self.assertEqual(progress.snapshot()[0]["status"], "failed")


class ToolsApplyTests(BaseCase):
    def test_tools_skipped_when_no_ai_tools(self) -> None:
        progress = self.run_apply()
        steps = self.by_id(progress)
        self.assertEqual(steps["tools"]["status"], "skipped")
        self.assertEqual(steps["tools"]["detail"], "none chosen")

    def test_tools_skipped_when_empty_list(self) -> None:
        cfg = make_cfg()
        cfg["ai_tools"] = {"tools": [], "mode": "lite"}
        progress = self.run_apply(cfg)
        steps = self.by_id(progress)
        self.assertEqual(steps["tools"]["status"], "skipped")
        self.assertEqual(steps["tools"]["detail"], "none chosen")

    def test_tools_skipped_when_ai_tools_missing_key(self) -> None:
        cfg = make_cfg()
        del cfg["ai_tools"]
        progress = self.run_apply(cfg)
        steps = self.by_id(progress)
        self.assertEqual(steps["tools"]["status"], "skipped")
        self.assertEqual(steps["tools"]["detail"], "none chosen")

    def test_tools_success_calls_runner_with_expected_args(self) -> None:
        captured: dict[str, object] = {}
        count = 0

        def stub(tool_ids, mode, *, env, home, machine, platform, python, src_dir, dry_run):
            nonlocal count
            count += 1
            captured["tool_ids"] = tool_ids
            captured["mode"] = mode
            captured["env"] = env
            captured["home"] = home
            captured["machine"] = machine
            captured["platform"] = platform
            captured["python"] = python
            captured["src_dir"] = src_dir
            captured["dry_run"] = dry_run
            return ["installed x", "done"]

        cfg = make_cfg(SSH_PUBLIC_KEYS["ssh-ed25519"])
        cfg["ai_tools"] = {"tools": ["x"], "mode": "lite"}
        progress = self.run_apply(cfg, tools_runner=stub)
        self.assertEqual(count, 1)
        self.assertEqual(captured["tool_ids"], ["x"])
        self.assertEqual(captured["mode"], "lite")
        self.assertEqual(captured["env"], self.env)
        self.assertEqual(captured["home"], self.home)
        self.assertEqual(captured["platform"], "linux")
        self.assertEqual(captured["python"], sys.executable)
        self.assertEqual(captured["src_dir"], self.repo / "src")
        self.assertEqual(captured["dry_run"], False)
        self.assertEqual(captured["machine"], platform.machine())
        steps = self.by_id(progress)
        self.assertEqual(steps["tools"]["status"], "ok")
        self.assertEqual(steps["tools"]["detail"], "installed x; done")

    def test_tools_install_error_marks_failed_but_others_ok(self) -> None:
        def stub(tool_ids, mode, *, env, home, machine, platform, python, src_dir, dry_run):
            from opos.aitools.installer import InstallError

            raise InstallError("boom")

        cfg = make_cfg(SSH_PUBLIC_KEYS["ssh-ed25519"])
        cfg["ai_tools"] = {"tools": ["x"], "mode": "lite"}
        progress = self.run_apply(cfg, tools_runner=stub)
        steps = self.by_id(progress)
        self.assertEqual(steps["tools"]["status"], "failed")
        self.assertIn("boom", steps["tools"]["detail"])
        self.assertEqual(steps["config"]["status"], "ok")
        self.assertEqual(steps["token"]["status"], "ok")
        self.assertEqual(steps["services"]["status"], "ok")
        saved = json.loads(config_path(self.env, "linux").read_text(encoding="utf-8"))
        self.assertTrue(saved["applied"])

    def test_tools_value_error_gives_type_name(self) -> None:
        def stub(tool_ids, mode, *, env, home, machine, platform, python, src_dir, dry_run):
            raise ValueError("secret")

        cfg = make_cfg()
        cfg["ai_tools"] = {"tools": ["x"], "mode": "lite"}
        progress = self.run_apply(cfg, tools_runner=stub)
        steps = self.by_id(progress)
        self.assertEqual(steps["tools"]["status"], "failed")
        self.assertEqual(steps["tools"]["detail"], "ValueError")

    def test_tools_skipped_after_earlier_failure(self) -> None:
        called: list[int] = []

        def stub(tool_ids, mode, *, env, home, machine, platform, python, src_dir, dry_run):
            called.append(1)
            return ["ok"]

        self.runner = FakeRunner(RuntimeError("boom"))
        cfg = make_cfg()
        cfg["ai_tools"] = {"tools": ["x"], "mode": "lite"}
        progress = self.run_apply(cfg, runner=self.runner, tools_runner=stub)
        steps = self.by_id(progress)
        self.assertEqual(steps["services"]["status"], "failed")
        self.assertEqual(steps["tools"]["status"], "skipped")
        self.assertEqual(steps["tools"]["detail"], "skipped after an earlier failure")
        self.assertEqual(called, [])

    def test_tools_dry_run_passes_flag_and_writes_no_config(self) -> None:
        seen: dict[str, object] = {}

        def stub(tool_ids, mode, *, env, home, machine, platform, python, src_dir, dry_run):
            seen["dry_run"] = dry_run
            return ["would install x"]

        cfg = make_cfg(SSH_PUBLIC_KEYS["ssh-ed25519"])
        cfg["ai_tools"] = {"tools": ["x"], "mode": "lite"}
        progress = self.run_apply(cfg, dry_run=True, tools_runner=stub)
        self.assertEqual(seen["dry_run"], True)
        steps = self.by_id(progress)
        self.assertEqual(steps["tools"]["status"], "ok")
        self.assertIn("would install", steps["tools"]["detail"])
        self.assertFalse(self.cfg_dir().exists())
        self.assertFalse(config_path(self.env, "linux").exists())


class HttpHealthTests(unittest.TestCase):
    def test_fake_opener_success(self) -> None:
        calls = []

        def opener(url, timeout=None):
            calls.append((url, timeout))
            return FakeResponse(200, b'{"ok": true}')

        ok, text = http_health(8123, timeout_s=1.0, interval_s=0.01, opener=opener)
        self.assertTrue(ok)
        self.assertEqual(text, "responding on port 8123")
        self.assertEqual(calls[0][0], "http://127.0.0.1:8123/api/state")

    def test_fake_opener_failures_time_out(self) -> None:
        def bad_status(url, timeout=None):
            return FakeResponse(500, b"{}")

        def bad_json(url, timeout=None):
            return FakeResponse(200, b"[]")

        def raising(url, timeout=None):
            raise urllib.error.URLError("down")

        for opener in (bad_status, bad_json, raising):
            ok, text = http_health(8124, timeout_s=0.12, interval_s=0.02, opener=opener)
            self.assertFalse(ok)
            self.assertTrue(text.startswith("no answer on port 8124 after "))

    def test_real_server_on_loopback_ephemeral_port(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = b'{"ok": true}' if self.path == "/api/state" else b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                return None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            ok, text = http_health(server.server_address[1], timeout_s=3.0, interval_s=0.05)
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(ok)
        self.assertIn("responding on port", text)


class UninstallTests(BaseCase):
    def test_uninstall_leaves_nothing_behind(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text(SSH_PUBLIC_KEYS["ssh-ed25519"] + "\n", encoding="utf-8")
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "app.log").write_text("x", encoding="utf-8")
        lines = uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner())
        self.assertTrue(lines)
        self.assertFalse(d.exists())
        self.assertFalse(config_path(self.env, "linux").exists())

    def test_uninstall_dry_run_removes_nothing(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "a.log").write_text("x", encoding="utf-8")
        lines = uninstall(home=self.home, platform="linux", env=self.env, dry_run=True, runner=FakeRunner())
        self.assertTrue(any(line.startswith("would remove ") for line in lines))
        self.assertTrue(d.exists())
        self.assertTrue(config_path(self.env, "linux").exists())

    def test_uninstall_keep_config(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        (d / "logs").mkdir(parents=True, exist_ok=True)
        uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner(), keep_config=True)
        self.assertTrue(config_path(self.env, "linux").exists())

    def test_uninstall_symlink_logs_not_followed(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "old.log").write_text("old", encoding="utf-8")
        for child in logs.iterdir():
            child.unlink()
        logs.rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        logs.symlink_to(outside, target_is_directory=True)
        uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner())
        self.assertFalse(logs.is_symlink())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse(d.exists())

    def test_uninstall_symlink_file_not_followed(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        (d / "logs").mkdir(parents=True, exist_ok=True)
        (d / "logs" / "a.log").write_text("x", encoding="utf-8")
        outside = self.root / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        token = token_path(self.env, "linux")
        token.unlink()
        token.symlink_to(outside)
        uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner())
        self.assertFalse(token.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_uninstall_leaves_nonempty_config_dir(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "a.log").write_text("x", encoding="utf-8")
        extra = d / "extra.txt"
        extra.write_text("mine", encoding="utf-8")
        uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner())
        self.assertTrue(extra.exists())
        self.assertFalse(config_path(self.env, "linux").exists())
        self.assertFalse((d / "logs").exists())

    def test_uninstall_missing_files_is_fine(self) -> None:
        lines = uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner())
        self.assertIsInstance(lines, list)

    def test_uninstall_preserves_symlinked_config_base(self) -> None:
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                tag = "false" if not dry_run else "true"
                real = self.root.resolve() / ("realcfg-" + tag)
                (real / "logs").mkdir(parents=True, exist_ok=True)
                seeded = {
                    real / "config.json": ("cfg-bytes-" + tag).encode("utf-8"),
                    real / "session.token": ("tok-bytes-" + tag).encode("utf-8"),
                    real / "authorized_key.pub": ("key-bytes-" + tag).encode("utf-8"),
                    real / "drain-calibration.json": ("drain-bytes-" + tag).encode("utf-8"),
                    real / "logs" / "keep.log": ("log-bytes-" + tag).encode("utf-8"),
                }
                for path, data in seeded.items():
                    path.write_bytes(data)
                link = self.root.resolve() / ("linkcfg-" + tag)
                if os.path.lexists(link):
                    if link.is_symlink() or link.is_file():
                        link.unlink()
                link.symlink_to(real, target_is_directory=True)
                env = dict(self.env)
                env["OPOS_CONFIG_DIR"] = str(link)
                lines = uninstall(home=self.home, platform="linux", env=env, runner=FakeRunner(), dry_run=dry_run, tools_uninstaller=lambda **kwargs: [])
                self.assertTrue(link.is_symlink())
                self.assertEqual(os.readlink(link), str(real))
                for path, data in seeded.items():
                    self.assertEqual(path.read_bytes(), data)
                for line in lines:
                    if "removed" in line or "would remove" in line:
                        self.assertNotIn(str(link), line)
                        self.assertNotIn(str(real), line)


class UninstallToolsTests(BaseCase):
    def test_uninstall_calls_tools_uninstaller(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "a.log").write_text("x", encoding="utf-8")
        seen: dict[str, object] = {}

        def stub(*, env, home, dry_run):
            seen["dry_run"] = dry_run
            seen["env"] = env
            seen["home"] = home
            return ["tools line"]

        lines = uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner(), tools_uninstaller=stub)
        self.assertIn("tools line", lines)
        self.assertEqual(seen["dry_run"], False)
        self.assertEqual(seen["env"], self.env)
        self.assertEqual(seen["home"], self.home)

    def test_uninstall_dry_run_passes_flag(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "a.log").write_text("x", encoding="utf-8")
        seen: dict[str, object] = {}

        def stub(*, env, home, dry_run):
            seen["dry_run"] = dry_run
            return ["would remove tools"]

        lines = uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner(), dry_run=True, tools_uninstaller=stub)
        self.assertIn("would remove tools", lines)
        self.assertEqual(seen["dry_run"], True)
        self.assertTrue(d.exists())

    def test_uninstall_keep_config_calls_tools_uninstaller(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        (d / "logs").mkdir(parents=True, exist_ok=True)

        def stub(*, env, home, dry_run):
            return ["keep tools line"]

        lines = uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner(), keep_config=True, tools_uninstaller=stub)
        self.assertIn("keep tools line", lines)
        self.assertTrue(config_path(self.env, "linux").exists())

    def test_uninstall_failing_tools_uninstaller_does_not_break(self) -> None:
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "a.log").write_text("x", encoding="utf-8")

        def bad(*, env, home, dry_run):
            raise RuntimeError("boom")

        lines = uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner(), tools_uninstaller=bad)
        self.assertIsInstance(lines, list)
        self.assertFalse((d / "session.token").exists())

    def test_uninstall_tools_uninstaller_runtime_error_reports_type_only(self) -> None:
        """Tools uninstaller error is sanitized to type name."""
        d = config_dir(self.env, "linux")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "session.token").write_text("t", encoding="utf-8")
        (d / "authorized_key.pub").write_text("k\n", encoding="utf-8")
        logs = d / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "a.log").write_text("x", encoding="utf-8")

        def bad(*, env, home, dry_run):
            raise RuntimeError("x")

        lines = uninstall(home=self.home, platform="linux", env=self.env, runner=FakeRunner(), tools_uninstaller=bad)
        self.assertIn("could not remove AI tools: RuntimeError", lines)
        for m in lines:
            if m.startswith("could not remove AI tools:"):
                self.assertEqual(m, "could not remove AI tools: RuntimeError")
        self.assertNotIn("x", "".join(m for m in lines if m.startswith("could not remove AI tools:")))
        self.assertFalse((d / "session.token").exists())
        self.assertFalse((d / "config.json").exists())
        self.assertFalse((d / "logs").exists())


class SshKeySymlinkTests(BaseCase):
    @unittest.skipIf(sys.platform == "win32", "POSIX symlink/mode not available on Windows")
    def test_planted_symlink_victim_preserved_and_key_saved_atomically(self) -> None:
        key = SSH_PUBLIC_KEYS["ssh-ed25519"]
        cdir = self.cfg_dir()
        cdir.mkdir(parents=True, exist_ok=True)
        victim = self.root / "victim.txt"
        victim_bytes = b"victim known bytes 12345"
        victim.write_bytes(victim_bytes)
        os.chmod(victim, 0o644)
        tmp_link = cdir / f".authorized_key.pub.tmp.{os.getpid()}"
        if os.path.lexists(tmp_link):
            os.unlink(tmp_link)
        os.symlink(victim, tmp_link)
        cfg = make_cfg(key)
        prog = self.run_apply(cfg)
        self.assertEqual(victim.read_bytes(), victim_bytes)
        kpath = cdir / "authorized_key.pub"
        self.assertTrue(kpath.is_file())
        self.assertFalse(kpath.is_symlink())
        self.assertEqual(kpath.read_text(encoding="utf-8"), key + "\n")
        self.assertEqual(stat.S_IMODE(kpath.stat().st_mode), 0o600)
        self.assertTrue(os.path.islink(tmp_link))
        self.assertEqual(os.readlink(tmp_link), str(victim))
        self.assertTrue(prog.ok())
        self.assertEqual(self.by_id(prog)["key"]["status"], "ok")

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes not honoured on Windows")
    def test_key_save_without_symlink_holds_exact_key_line_and_mode(self) -> None:
        key = SSH_PUBLIC_KEYS["ssh-ed25519"]
        cfg = make_cfg(key)
        self.run_apply(cfg)
        kpath = self.cfg_dir() / "authorized_key.pub"
        self.assertEqual(kpath.read_text(encoding="utf-8"), key + "\n")
        self.assertEqual(stat.S_IMODE(kpath.stat().st_mode), 0o600)
        self.assertFalse(kpath.is_symlink())


class TestNoSilentExceptPass(unittest.TestCase):
    def test_no_except_handler_with_only_pass(self) -> None:
        target = Path(__file__).resolve().parents[1] / "src" / "opos" / "setup" / "apply.py"
        tree = ast.parse(target.read_text(encoding="utf-8"))
        lines = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and len(n.body) == 1 and isinstance(n.body[0], ast.Pass)]
        self.assertEqual(lines, [], f"silent except-pass at lines {lines}")


if __name__ == "__main__":
    unittest.main()
