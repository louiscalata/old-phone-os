# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for opos.setup.cli."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opos.setup.cli import main as cli_main
from opos.setup.config import ConfigError, config_path, default_config, load, save


class TestSetupCli(unittest.TestCase):
    def test_run_unconfigured_hands_over(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            with patch("opos.setup.server.main", return_value=5) as srv:
                with patch("opos.setup.config.load", return_value=None), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=None, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main(["run"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 5)
                srv.assert_called_once()

    def test_run_configured_prints_url(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = default_config()
            cfg["applied"] = True
            with patch("opos.setup.server.main") as srv:
                with patch("opos.setup.config.load", return_value=cfg), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=cfg, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main(["run"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                srv.assert_not_called()
                txt = out.getvalue()
                self.assertIn("Old Phone OS is already set up.", txt)
                self.assertIn("http://127.0.0.1:8765/", txt)
                self.assertIn("opos run --reconfigure", txt)

    def test_run_unapplied_config_hands_over(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            cdir = Path(td) / "cfgdir"
            cdir.mkdir()
            env = {"OPOS_CONFIG_DIR": str(cdir)}
            cfg = default_config()
            save(cfg, config_path(env, "linux"))
            self.assertIs(load(config_path(env, "linux")).get("applied"), False)
            out = io.StringIO()
            with patch("opos.setup.server.main", return_value=5) as srv:
                rc = cli_main(["run"], env=env, home=home, platform="linux", out=out)
                self.assertEqual(rc, 5)
                srv.assert_called_once_with([])
                self.assertNotIn("Old Phone OS is already set up.", out.getvalue())

    def test_run_reconfigure_hands_over_even_when_configured(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["a"], "goal": "g", "battery_mode": "balanced"}
            with patch("opos.setup.server.main", return_value=0) as srv:
                with patch("opos.setup.config.load", return_value=cfg), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=cfg, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main(["run", "--reconfigure"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                srv.assert_called_once()

    def test_run_invalid_config_hands_over(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            with patch("opos.setup.server.main", return_value=0) as srv:
                with patch("opos.setup.config.load", side_effect=ConfigError("bad")), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", side_effect=ConfigError("bad"), create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main(["run"], env={}, home=home, platform="linux", out=out)
                srv.assert_called_once()
                self.assertEqual(rc, 0)

    def test_run_default_when_no_args(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            with patch("opos.setup.server.main", return_value=0) as srv:
                with patch("opos.setup.config.load", return_value=None), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=None, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main([], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                srv.assert_called_once()

    def test_run_passes_flags(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            with patch("opos.setup.server.main", return_value=0) as srv:
                with patch("opos.setup.config.load", return_value=None), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=None, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    cli_main(["run", "--port", "9123", "--dry-run", "--no-browser", "--config-dir", "/fake/dir"], env={}, home=home, platform="linux", out=out)
                argv = srv.call_args[0][0] if srv.call_args[0] else srv.call_args[1].get("argv", [])
                self.assertIn("--port", argv)
                self.assertIn("9123", argv)
                self.assertIn("--dry-run", argv)
                self.assertIn("--no-browser", argv)
                self.assertIn("--config-dir", argv)
                self.assertIn("/fake/dir", argv)

    def test_run_config_dir_sets_env(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            env: dict[str, str] = {}
            with patch("opos.setup.server.main", return_value=0):
                with patch("opos.setup.config.load", return_value=None), patch("opos.setup.cli.load", return_value=None, create=True), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    cli_main(["run", "--config-dir", "/fake/dir"], env=env, home=home, platform="linux", out=out)
                self.assertNotIn("OPOS_CONFIG_DIR", env)

    def test_status_unconfigured(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            with patch("opos.setup.config.load", return_value=None), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=None, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                with patch("opos.setup.apply.http_health", return_value=(False, "down")) as h1, patch("opos.setup.cli.http_health", return_value=(False, "down"), create=True) as h2:
                    rc = cli_main(["status"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                txt = out.getvalue()
                self.assertIn("configured: no", txt)
                self.assertIn(str(Path(td) / "cfg"), txt)
                self.assertIn("running: no", txt)
                called = h1.call_args or h2.call_args
                self.assertIsNotNone(called)
                self.assertEqual(called[0][0], 8765)

    def test_status_persist_marker_result(self):
        import json as _json
        import opos.setup.config as _cfgmod
        _sentinel = "".join(["s", "e", "n", "t", "i", "n", "e", "l", "-", "7", "q", "9"])
        _valid = [
            ("ok", "ok"),
            ("fail", "fail"),
            ("unverified", "unverified"),
        ]
        for _name, _expect in _valid:
            with self.subTest(name=_name):
                with tempfile.TemporaryDirectory() as _td:
                    _cdir = Path(_td) / "cfgdir"
                    _cdir.mkdir()
                    _cpath = _cfgmod.config_path({"OPOS_CONFIG_DIR": str(_cdir)}, "linux")
                    _marker = _cpath.parent / "persist-marker.json"
                    _marker.parent.mkdir(parents=True, exist_ok=True)
                    _doc = {"last_verify": {"result": _expect, "note": _sentinel}}
                    _before = _json.dumps(_doc).encode("utf-8")
                    _marker.write_bytes(_before)
                    _out = io.StringIO()
                    with patch("opos.setup.config.load", return_value=None), patch("opos.setup.apply.http_health", return_value=(False, "down")):
                        _rc = cli_main(["status", "--config-dir", str(_cdir)], env={}, platform="linux", out=_out)
                    self.assertEqual(_rc, 0)
                    _lines = _out.getvalue().splitlines()
                    _want = f"persist: {_expect}"
                    self.assertEqual(_lines.count(_want), 1)
                    self.assertEqual(_lines[_lines.index(_want) + 1], "running: no")
                    self.assertNotIn(_sentinel, _out.getvalue())
                    self.assertEqual(_marker.read_bytes(), _before)
        _edge = [
            ("missing", None, "unverified"),
            ("malformed", b"{not json", "unverified"),
            ("badutf8", b"\xff\xfe\x00bad", "unverified"),
            ("toplist", b"[1, 2]", "unverified"),
            ("empty", b"{}", "unverified"),
            ("nonobject", b'{"last_verify": "ok"}', "unverified"),
            ("badresult", b'{"last_verify": {"result": "maybe"}}', "unverified"),
        ]
        for _name, _payload, _expect in _edge:
            with self.subTest(name=_name):
                with tempfile.TemporaryDirectory() as _td:
                    _cdir = Path(_td) / "cfgdir"
                    _cdir.mkdir()
                    _cpath = _cfgmod.config_path({"OPOS_CONFIG_DIR": str(_cdir)}, "linux")
                    _marker = _cpath.parent / "persist-marker.json"
                    _marker.parent.mkdir(parents=True, exist_ok=True)
                    if _payload is None:
                        if _marker.exists():
                            _marker.unlink()
                    else:
                        _marker.write_bytes(_payload)
                        _before = _marker.read_bytes()
                    _out = io.StringIO()
                    with patch("opos.setup.config.load", return_value=None), patch("opos.setup.apply.http_health", return_value=(False, "down")):
                        _rc = cli_main(["status", "--config-dir", str(_cdir)], env={}, platform="linux", out=_out)
                    self.assertEqual(_rc, 0)
                    _lines = _out.getvalue().splitlines()
                    _want = f"persist: {_expect}"
                    self.assertEqual(_lines.count(_want), 1)
                    self.assertEqual(_lines[_lines.index(_want) + 1], "running: no")
                    self.assertNotIn(_sentinel, _out.getvalue())
                    if _payload is None:
                        self.assertFalse(_marker.exists())
                    else:
                        self.assertEqual(_marker.read_bytes(), _before)

    def test_status_configured_no_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "my-device", "services": ["svc1"], "goal": "my-goal", "battery_mode": "eco", "session_token": "not-shown-a", "ssh_key": "not-shown-b"}
            with patch("opos.setup.config.load", return_value=cfg), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=cfg, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                with patch("opos.setup.apply.http_health", return_value=(True, "up")), patch("opos.setup.cli.http_health", return_value=(True, "up"), create=True):
                    rc = cli_main(["status"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                txt = out.getvalue()
                self.assertIn("configured: yes", txt)
                self.assertIn("my-device", txt)
                self.assertIn("my-goal", txt)
                self.assertIn("eco", txt)
                self.assertNotIn("not-shown-a", txt)
                self.assertNotIn("not-shown-b", txt)
                self.assertIn("running: yes", txt)
                self.assertIn("ai_tools: none", txt)

    def test_status_running_uses_http_health(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            with patch("opos.setup.config.load", return_value=None), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=None, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                with patch("opos.setup.apply.http_health", return_value=(True, "ok")) as h1, patch("opos.setup.cli.http_health", return_value=(True, "ok"), create=True) as h2:
                    cli_main(["status"], env={}, home=home, platform="linux", out=out)
                called = h1.call_args or h2.call_args
                self.assertIsNotNone(called)
                args, kwargs = called
                self.assertEqual(args[0], 8765)
                self.assertEqual(kwargs.get("timeout_s"), 1.0)
                self.assertEqual(kwargs.get("interval_s"), 0.2)

    def test_status_ai_tools_none_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["svc1"], "goal": "g", "battery_mode": "balanced"}
            with patch("opos.setup.config.load", return_value=cfg), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=cfg, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                with patch("opos.setup.apply.http_health", return_value=(False, "down")), patch("opos.setup.cli.http_health", return_value=(False, "down"), create=True):
                    rc = cli_main(["status"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn("ai_tools: none", out.getvalue())

    def test_status_ai_tools_none_when_empty(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["svc1"], "goal": "g", "battery_mode": "balanced", "ai_tools": {"tools": []}}
            with patch("opos.setup.config.load", return_value=cfg), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=cfg, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                with patch("opos.setup.apply.http_health", return_value=(False, "down")), patch("opos.setup.cli.http_health", return_value=(False, "down"), create=True):
                    rc = cli_main(["status"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn("ai_tools: none", out.getvalue())

    def test_status_ai_tools_full(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["svc1"], "goal": "g", "battery_mode": "balanced", "ai_tools": {"tools": ["a", "b"], "mode": "full"}}
            with patch("opos.setup.config.load", return_value=cfg), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=cfg, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                with patch("opos.setup.apply.http_health", return_value=(True, "up")), patch("opos.setup.cli.http_health", return_value=(True, "up"), create=True):
                    rc = cli_main(["status"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn("ai_tools: a, b (full)", out.getvalue())

    def test_uninstall_delegates(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            prefix = Path(td) / "prefix"
            env = {"OPOS_TOOLS_PREFIX": str(prefix)}
            out = io.StringIO()
            with patch("opos.setup.apply.uninstall", return_value=["removed a", "removed b"]) as mock_un:
                with patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main(["uninstall"], env=env, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                txt = out.getvalue()
                self.assertIn("removed a", txt)
                self.assertIn("removed b", txt)
                kwargs = mock_un.call_args[1]
                self.assertEqual(kwargs.get("home"), home)
                self.assertEqual(kwargs.get("platform"), "linux")
                self.assertFalse(kwargs.get("dry_run"))
                self.assertFalse(kwargs.get("keep_config"))
                self.assertEqual(kwargs.get("env", {}).get("OPOS_TOOLS_PREFIX"), str(prefix))

    def test_uninstall_keep_config_and_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            prefix = Path(td) / "prefix"
            env = {"OPOS_TOOLS_PREFIX": str(prefix)}
            out = io.StringIO()
            with patch("opos.setup.apply.uninstall", return_value=[]) as mock_un:
                rc = cli_main(["uninstall", "--keep-config", "--dry-run"], env=env, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                kwargs = mock_un.call_args[1]
                self.assertTrue(kwargs.get("keep_config"))
                self.assertTrue(kwargs.get("dry_run"))
                self.assertEqual(kwargs.get("env", {}).get("OPOS_TOOLS_PREFIX"), str(prefix))

    def test_uninstall_config_dir_sets_env(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            prefix = Path(td) / "prefix"
            out = io.StringIO()
            env: dict[str, str] = {"OPOS_TOOLS_PREFIX": str(prefix)}
            with patch("opos.setup.apply.uninstall", return_value=[]) as mock_un:
                cli_main(["uninstall", "--config-dir", "/fake/dir"], env=env, home=home, platform="linux", out=out)
                self.assertNotIn("OPOS_CONFIG_DIR", env)
                env_passed = mock_un.call_args[1].get("env")
                self.assertEqual(env_passed.get("OPOS_CONFIG_DIR"), "/fake/dir")
                self.assertEqual(env_passed.get("OPOS_TOOLS_PREFIX"), str(prefix))

    def test_bad_option_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            with self.assertRaises(SystemExit) as cm:
                cli_main(["run", "--unknown-flag"], env={}, home=home, platform="linux", out=out)
            self.assertEqual(cm.exception.code, 2)
            with self.assertRaises(SystemExit) as cm2:
                cli_main(["status", "--bad"], env={}, home=home, platform="linux", out=out)
            self.assertEqual(cm2.exception.code, 2)
            with self.assertRaises(SystemExit) as cm3:
                cli_main(["uninstall", "--bad"], env={}, home=home, platform="linux", out=out)
            self.assertEqual(cm3.exception.code, 2)

    def test_status_goal_default_config(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["a"], "goal": {"enabled": True, "time": "23:00"}, "battery_mode": "balanced"}
            with patch("opos.setup.config.load", return_value=cfg), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=cfg, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                with patch("opos.setup.apply.http_health", return_value=(False, "down")), patch("opos.setup.cli.http_health", return_value=(False, "down"), create=True):
                    rc = cli_main(["status"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn("goal: 23:00", out.getvalue())

    def test_status_goal_off(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["a"], "goal": {"enabled": False, "time": "23:00"}, "battery_mode": "balanced"}
            with patch("opos.setup.config.load", return_value=cfg), patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.load", return_value=cfg, create=True), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                with patch("opos.setup.apply.http_health", return_value=(False, "down")), patch("opos.setup.cli.http_health", return_value=(False, "down"), create=True):
                    rc = cli_main(["status"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn("goal: off", out.getvalue())

    def test_run_defaults_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["a"], "goal": {"enabled": True, "time": "23:00"}, "battery_mode": "balanced"}
            from opos.setup.apply import Progress
            try:
                prog = Progress()
            except TypeError:
                try:
                    prog = Progress([])
                except Exception:
                    prog = Progress.__new__(Progress)
                    prog._steps = []
            try:
                for s in list(prog.snapshot()):
                    lbl = s.get("label")
                    if lbl and hasattr(prog, "finish"):
                        try:
                            prog.finish(lbl)
                        except Exception:
                            pass
            except Exception:
                pass
            with patch("opos.setup.config.default_config", return_value=cfg), patch("opos.setup.config.validate", return_value=[]), patch("opos.setup.apply.apply", return_value=prog) as mock_apply:
                with patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main(["run", "--defaults", "--dry-run"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn("Dry run finished. Nothing was configured or started.", out.getvalue())
                self.assertNotIn("Open it at", out.getvalue())
                kwargs = mock_apply.call_args[1]
                self.assertEqual(kwargs.get("home"), home)
                self.assertEqual(kwargs.get("platform"), "linux")
                self.assertTrue(kwargs.get("dry_run"))
                self.assertFalse((Path(td) / "cfg").exists())

    def test_run_defaults_failed(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["a"], "goal": {"enabled": True, "time": "23:00"}, "battery_mode": "balanced"}
            from opos.setup.apply import Progress
            try:
                prog = Progress()
            except TypeError:
                try:
                    prog = Progress([])
                except Exception:
                    prog = Progress.__new__(Progress)
                    prog._steps = []
            try:
                prog.fail("config", "boom")
            except Exception:
                try:
                    prog = Progress.fail("config", "boom")
                except Exception:
                    prog._steps = [{"label": "config", "status": "failed", "detail": "boom"}]
            with patch("opos.setup.config.default_config", return_value=cfg), patch("opos.setup.config.validate", return_value=[]), patch("opos.setup.apply.apply", return_value=prog):
                with patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main(["run", "--defaults"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 1)
                txt = out.getvalue()
                self.assertIn("setup failed", txt)
                self.assertIn("boom", txt)

    def test_run_defaults_success(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            out = io.StringIO()
            cfg = {"device_name": "desk", "services": ["a"], "goal": {"enabled": True, "time": "23:00"}, "battery_mode": "balanced"}
            from opos.setup.apply import Progress
            try:
                prog = Progress()
            except TypeError:
                try:
                    prog = Progress([])
                except Exception:
                    prog = Progress.__new__(Progress)
                    prog._steps = []
            try:
                for s in list(prog.snapshot()):
                    lbl = s.get("label")
                    if lbl and hasattr(prog, "finish"):
                        try:
                            prog.finish(lbl)
                        except Exception:
                            pass
            except Exception:
                pass
            with patch("opos.setup.config.default_config", return_value=cfg), patch("opos.setup.config.validate", return_value=[]), patch("opos.setup.apply.apply", return_value=prog):
                with patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"), patch("opos.setup.cli.config_path", return_value=Path(td) / "cfg", create=True):
                    rc = cli_main(["run", "--defaults"], env={}, home=home, platform="linux", out=out)
                self.assertEqual(rc, 0)
                self.assertIn("Old Phone OS is set up with the default settings.", out.getvalue())

    def test_run_defaults_detects_surface_duo(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            fake_root = Path(td) / "fake_root"
            (fake_root / "proc" / "device-tree").mkdir(parents=True)
            _model = "".join(["Surface", " ", "Duo", " ", "DV_B"])
            (fake_root / "proc" / "device-tree" / "model").write_text(_model)
            out = io.StringIO()
            captured: dict[str, object] = {}
            class _Prog:
                def snapshot(self):
                    return []
            import opos.setup.device as _dev_mod
            _orig = _dev_mod.detect_device
            def _fake_detect(root, config_dir=None):
                self.assertEqual(root, "/")
                return _orig(str(fake_root), config_dir=config_dir)
            def _fake_apply(cfg, **kw):
                captured["cfg"] = cfg
                return _Prog()
            with patch("opos.setup.device.detect_device", side_effect=_fake_detect):
                with patch("opos.setup.config.validate", return_value=[]):
                    with patch("opos.setup.apply.apply", side_effect=_fake_apply):
                        with patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"):
                            rc = cli_main(["run", "--defaults"], env={}, home=home, platform="linux", out=out)
            self.assertEqual(rc, 0)
            self.assertIn("Surface Duo", out.getvalue())
            cfg_val = captured.get("cfg")
            self.assertIsInstance(cfg_val, dict)
            dev = cfg_val.get("device") if isinstance(cfg_val, dict) else None
            self.assertIsInstance(dev, dict)
            self.assertEqual(dev.get("name"), "Surface Duo")
            self.assertEqual(dev.get("source"), "hw-adapt")

    def test_run_defaults_uses_generic_without_model(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            fake_root = Path(td) / "fake_root"
            fake_root.mkdir(parents=True)
            out = io.StringIO()
            captured: dict[str, object] = {}
            class _Prog:
                def snapshot(self):
                    return []
            import opos.setup.device as _dev_mod
            _orig = _dev_mod.detect_device
            def _fake_detect(root, config_dir=None):
                self.assertEqual(root, "/")
                return _orig(str(fake_root), config_dir=config_dir)
            def _fake_apply(cfg, **kw):
                captured["cfg"] = cfg
                return _Prog()
            with patch("opos.setup.device.detect_device", side_effect=_fake_detect):
                with patch("opos.setup.config.validate", return_value=[]):
                    with patch("opos.setup.apply.apply", side_effect=_fake_apply):
                        with patch("opos.setup.config.config_path", return_value=Path(td) / "cfg"):
                            rc = cli_main(["run", "--defaults"], env={}, home=home, platform="linux", out=out)
            self.assertEqual(rc, 0)
            cfg_val = captured.get("cfg")
            self.assertIsInstance(cfg_val, dict)
            dev = cfg_val.get("device") if isinstance(cfg_val, dict) else None
            self.assertIsInstance(dev, dict)
            self.assertEqual(dev.get("name"), "Generic phone")
            self.assertEqual(dev.get("source"), "hw-adapt")
            self.assertEqual(dev.get("id"), "generic-phone")
