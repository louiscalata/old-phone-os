# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import os
import runpy
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from opos.aitools import cli, installer, launcher
from opos.aitools import power


def _tool(tid="demo-tool", binary="mybin", archs=None, min_free=1):
    return {"id": tid, "name": f"Name {tid}", "binary": binary, "installer_url": "https://example.com/install.sh", "interpreter": "sh", "installer_args": [], "installer_env": {}, "archs": archs or ["aarch64", "x86_64"], "min_free_mb": min_free, "state_subdir": ".state", "run_env": {}, "lite_env": {}, "signin": f"signin for {tid}", "docs_url": "https://example.com/docs"}


def _write_catalog(p, tools):
    p.write_text(json.dumps({"version": 1, "tools": tools}), encoding="utf-8")


def _fake_fetch(url):
    assert url.startswith("https://")
    return b"#!/bin/sh\necho hi\n"


def _runner_for(bins):
    def _r(argv, env, timeout_s):
        s = Path(env["HOME"])
        for b in bins:
            q = s / ".local" / "bin" / b
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            q.chmod(0o755)
        return (0, "")
    return _r


class CliTests(unittest.TestCase):
    def test_list(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool("a-tool", "mybin"), _tool("b-tool", "otherbin")])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            out = io.StringIO()
            rc = cli.main(["list"], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 0)
            txt = out.getvalue()
            self.assertIn("a-tool: Name a-tool - signin for a-tool", txt)
            self.assertIn("b-tool: Name b-tool - signin for b-tool", txt)

    def test_install_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            out = io.StringIO()
            rc = cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix), "--dry-run"], env=env, home=home, machine="x86_64", out=out, fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            self.assertEqual(rc, 0)
            txt = out.getvalue()
            self.assertIn("prefix:", txt)
            self.assertIn("would install demo-tool", txt)
            self.assertIn("would fetch https://example.com/install.sh", txt)
            self.assertIn("Sign in yourself:", txt)
            self.assertFalse((prefix / "opos-tools").exists())

    def test_install_real(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            out = io.StringIO()
            rc = cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out, fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            self.assertEqual(rc, 0)
            txt = out.getvalue()
            self.assertIn("installed demo-tool", txt)
            self.assertIn("wrapper", txt)
            self.assertIn("Sign in yourself: signin for demo-tool", txt)
            self.assertTrue((prefix / "opos-tools" / "manifest.json").exists())
            self.assertTrue((prefix / "opos-tools" / "bin" / "mybin").exists())
            mf = installer.read_manifest(prefix)
            self.assertIn("demo-tool", mf.get("tools", {}))
            self.assertTrue((home / ".profile").exists())
            self.assertIn(launcher.BEGIN, (home / ".profile").read_text(encoding="utf-8"))

    @unittest.skipIf(sys.platform == "win32", "POSIX shell wrappers")
    def test_install_relative_prefix_wrapper_survives_chdir(self):
        def _run_case(use_relative):
            with tempfile.TemporaryDirectory() as td:
                tp = Path(td)
                inst = tp / "install"
                inst.mkdir()
                other = tp / "elsewhere"
                other.mkdir()
                cat = (tp / "cat.json").resolve()
                _write_catalog(cat, [_tool()])
                home = (tp / "home").resolve()
                home.mkdir()
                env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
                leaf = "tools space'quote"
                base = inst.resolve()
                prefix_arg = leaf if use_relative else str(base / leaf)
                expected = base / leaf
                troot = expected / "opos-tools"
                sandbox = troot / "demo-tool" / "home"
                real_bin = sandbox / ".local" / "bin" / "mybin"
                wrapper = troot / "bin" / "mybin"
                run_dir = troot / "run"
                bin_dir = troot / "bin"
                env_file = troot / "env.sh"
                marker = "opos-relprefix-marker"
                seen = {}

                def _runner(argv, renv, timeout_s):
                    seen["HOME"] = renv["HOME"]
                    q = Path(renv["HOME"]) / ".local" / "bin" / "mybin"
                    q.parent.mkdir(parents=True, exist_ok=True)
                    q.write_text("#!/bin/sh\nprintf '%s\\n' " + marker + "\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done\n", encoding="utf-8")
                    q.chmod(0o755)
                    return (0, "")

                stub = (tp / "stub-sh").resolve()
                log = tp / "guard.log"
                stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(log)) + "\n", encoding="utf-8")
                stub.chmod(0o755)
                real_free = cli.place.free_mb
                seen_prefix = []

                def _spy(p):
                    seen_prefix.append(p)
                    return real_free(p)

                old = os.getcwd()
                os.chdir(str(inst))
                try:
                    out = io.StringIO()
                    with unittest.mock.patch.object(cli.sys, "executable", str(stub)):
                        with unittest.mock.patch.object(cli.place, "free_mb", side_effect=_spy):
                            rc = cli.main(["install", "--tool", "demo-tool", "--mode", "full", "--prefix", prefix_arg], env=env, home=home, machine="x86_64", out=out, fetch=_fake_fetch, runner=_runner)
                    self.assertEqual(rc, 0)
                    self.assertEqual(seen.get("HOME"), str(sandbox))
                    self.assertIn(expected, seen_prefix)
                    for p in seen_prefix:
                        self.assertTrue(p.is_absolute())
                    mf = installer.read_manifest(expected)
                    self.assertEqual(mf["tools"]["demo-tool"]["sandbox"], str(sandbox))
                    wtext = wrapper.read_text(encoding="utf-8")
                    self.assertIn("exec " + shlex.quote(str(real_bin)) + ' "$@"', wtext)
                    self.assertIn("--run-dir " + shlex.quote(str(run_dir)), wtext)
                    self.assertIn(shlex.quote(str(bin_dir)), env_file.read_text(encoding="utf-8"))
                    self.assertIn(shlex.quote(str(env_file)), (home / ".profile").read_text(encoding="utf-8"))
                    args = ["hello world", "it's", "plain"]
                    proc = subprocess.run([str(wrapper)] + args, cwd=str(other), capture_output=True, text=True, timeout=30)
                    self.assertEqual(proc.returncode, 0)
                    self.assertEqual(proc.stdout, marker + "\n" + "\n".join(args) + "\n")
                    lines = []
                    end = time.time() + 10
                    while time.time() < end:
                        if log.is_file():
                            lines = log.read_text(encoding="utf-8").splitlines()
                            if len(lines) >= 5:
                                break
                        time.sleep(0.05)
                    self.assertEqual(lines, ["-m", "opos.aitools", "guard", "--run-dir", str(run_dir)])
                    self.assertFalse((other / leaf).exists())
                    self.assertFalse((other / "opos-tools").exists())
                finally:
                    os.chdir(old)

        _run_case(True)
        _run_case(False)

    def test_install_bad_tool_id(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            out = io.StringIO()
            rc = cli.main(["install", "--tool", "nope", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out, fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            self.assertEqual(rc, 1)
            self.assertIn("error: unknown tool", out.getvalue())
            self.assertIn("nope", out.getvalue())

    def test_install_unsupported_arch(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool(archs=["aarch64"])])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            out = io.StringIO()
            rc = cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out, fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            self.assertEqual(rc, 1)
            txt = out.getvalue()
            self.assertIn("error:", txt)
            self.assertIn("unsupported CPU", txt)
            self.assertIn("x86_64", txt)

    def test_install_no_tool_flag(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            out = io.StringIO()
            with self.assertRaises(SystemExit) as cm:
                cli.main(["install", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(cm.exception.code, 2)

    def test_mode(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=io.StringIO(), fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            out = io.StringIO()
            rc = cli.main(["mode", "full", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 0)
            self.assertIn("mode set to full", out.getvalue())
            self.assertEqual(installer.read_manifest(prefix).get("mode"), "full")

    def test_status_zero_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=io.StringIO(), fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            out = io.StringIO()
            rc = cli.main(["status", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 0)
            txt = out.getvalue()
            self.assertIn(f"prefix: {prefix}", txt)
            self.assertIn("mode:", txt)
            self.assertIn("demo-tool", txt)
            self.assertIn("sessions: 0", txt)

    def test_uninstall_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=io.StringIO(), fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            out = io.StringIO()
            rc = cli.main(["uninstall", "--prefix", str(prefix), "--dry-run"], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 0)
            self.assertIn("would remove", out.getvalue())
            self.assertTrue((prefix / "opos-tools").exists())

    def test_uninstall_real(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=io.StringIO(), fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            out = io.StringIO()
            rc = cli.main(["uninstall", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 0)
            self.assertIn("removed", out.getvalue())
            self.assertFalse((prefix / "opos-tools").exists())

    def test_uninstall_returns_1_on_could_not_remove(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=io.StringIO(), fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            bad = tp / "bad_dir"
            bad.mkdir()
            mp = installer.manifest_path(prefix)
            data = json.loads(mp.read_text(encoding="utf-8"))
            data["profiles"] = data.get("profiles", []) + [str(bad)]
            mp.write_text(json.dumps(data), encoding="utf-8")
            os.chmod(mp, 0o600)
            orig = launcher.remove_profile_block

            def fake(p):
                if str(p) == str(bad):
                    raise PermissionError("cannot remove")
                return orig(p)

            with unittest.mock.patch("opos.aitools.launcher.remove_profile_block", side_effect=fake):
                out = io.StringIO()
                rc = cli.main(["uninstall", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 1)
            self.assertIn("could not remove", out.getvalue())

    def test_power_external(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            out = io.StringIO()
            with unittest.mock.patch("opos.aitools.power.on_external_power", return_value=True):
                rc = cli.main(["power"], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 0)
            self.assertIn("external power", out.getvalue())

    def test_power_battery(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            out = io.StringIO()
            with unittest.mock.patch("opos.aitools.power.on_external_power", return_value=False):
                rc = cli.main(["power"], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 1)
            self.assertIn("battery", out.getvalue())

    def test_power_raise(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            out = io.StringIO()

            def boom(*a, **k):
                raise RuntimeError("fail")

            with unittest.mock.patch("opos.aitools.power.on_external_power", side_effect=boom):
                rc = cli.main(["power"], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 1)
            self.assertIn("battery", out.getvalue())

    def test_no_subcommand(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            out = io.StringIO()
            rc = cli.main([], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 2)

    def test_runpy_help(self):
        old = sys.argv
        try:
            sys.argv = ["opos.aitools", "--help"]
            sout = io.StringIO()
            serr = io.StringIO()
            with unittest.mock.patch.object(sys, "stdout", sout), unittest.mock.patch.object(sys, "stderr", serr):
                try:
                    runpy.run_module("opos.aitools", run_name="__main__", alter_sys=True)
                except SystemExit as e:
                    self.assertEqual(e.code, 0)
                txt = sout.getvalue() + serr.getvalue()
                self.assertIn("usage", txt.lower())
        finally:
            sys.argv = old


class TestSourceDir(unittest.TestCase):
    def test_src_dir_layout(self):
        self.assertEqual(cli._SRC_DIR.name, "src")
        self.assertTrue((cli._SRC_DIR / "opos" / "aitools" / "cli.py").is_file())

    def test_install_manifest_and_wrapper_use_src_dir(self):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            env = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            home = tp / "home"
            home.mkdir()
            prefix = tp / "prefix"
            out = io.StringIO()
            rc = cli.main(["install", "--tool", "demo-tool", "--prefix", str(prefix)], env=env, home=home, machine="x86_64", out=out, fetch=_fake_fetch, runner=_runner_for(["mybin"]))
            self.assertEqual(rc, 0)
            mf = installer.read_manifest(prefix)
            self.assertEqual(mf.get("src_dir"), str(cli._SRC_DIR))
            wrapper = prefix / "opos-tools" / "bin" / "mybin"
            txt = wrapper.read_text(encoding="utf-8")
            self.assertIn(shlex.quote(str(cli._SRC_DIR)), txt)


class SupplyDirTests(unittest.TestCase):
    def test_supply_dir_unset_empty_and_set(self):
        """Check _supply_dir fallback and override."""
        self.assertTrue(hasattr(cli, "_supply_dir"))
        fn = cli._supply_dir
        try:
            self.assertEqual(fn({}), power.SUPPLY_DIR)
            self.assertEqual(fn({"OPOS_POWER_SUPPLY_DIR": ""}), power.SUPPLY_DIR)
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "s"
                p.mkdir()
                self.assertEqual(fn({"OPOS_POWER_SUPPLY_DIR": str(p)}), p)
            return
        except TypeError:
            pass
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPOS_POWER_SUPPLY_DIR", None)
            self.assertEqual(fn(), power.SUPPLY_DIR)
        with unittest.mock.patch.dict(os.environ, {"OPOS_POWER_SUPPLY_DIR": ""}, clear=False):
            self.assertEqual(fn(), power.SUPPLY_DIR)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s"
            p.mkdir()
            with unittest.mock.patch.dict(os.environ, {"OPOS_POWER_SUPPLY_DIR": str(p)}, clear=False):
                self.assertEqual(fn(), p)


class PowerEnvTests(unittest.TestCase):
    def test_power_env_battery(self):
        """Power via env reports battery."""
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            home = tp / "home"
            home.mkdir()
            supply = tp / "supply"
            supply.mkdir()
            bat = supply / "BAT0"
            bat.mkdir()
            (bat / "type").write_text("Battery", encoding="utf-8")
            (bat / "status").write_text("Discharging", encoding="utf-8")
            ac = supply / "AC"
            ac.mkdir()
            (ac / "type").write_text("Mains", encoding="utf-8")
            (ac / "online").write_text("0", encoding="utf-8")
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_POWER_SUPPLY_DIR": str(supply)}
            out = io.StringIO()
            rc = cli.main(["power"], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 1)
            self.assertIn("battery", out.getvalue())

    def test_power_env_external(self):
        """Power via env reports external."""
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            home = tp / "home"
            home.mkdir()
            supply = tp / "supply"
            supply.mkdir()
            bat = supply / "BAT0"
            bat.mkdir()
            (bat / "type").write_text("Battery", encoding="utf-8")
            (bat / "status").write_text("Discharging", encoding="utf-8")
            ac = supply / "AC"
            ac.mkdir()
            (ac / "type").write_text("Mains", encoding="utf-8")
            (ac / "online").write_text("1", encoding="utf-8")
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_POWER_SUPPLY_DIR": str(supply)}
            out = io.StringIO()
            rc = cli.main(["power"], env=env, home=home, machine="x86_64", out=out)
            self.assertEqual(rc, 0)
            self.assertIn("external power", out.getvalue())


class GuardEnvTests(unittest.TestCase):
    def test_guard_passes_supply_dir(self):
        """Guard forwards supply_dir from env."""
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            cat = tp / "cat.json"
            _write_catalog(cat, [_tool()])
            home = tp / "home"
            home.mkdir()
            run_dir = tp / "run"
            run_dir.mkdir()
            supply = tp / "supply"
            supply.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_POWER_SUPPLY_DIR": str(supply)}
            out = io.StringIO()
            with unittest.mock.patch("opos.aitools.sessions.main_guard") as mg:
                mg.return_value = 0
                rc = cli.main(["guard", "--run-dir", str(run_dir)], env=env, home=home, machine="x86_64", out=out)
                self.assertEqual(rc, 0)
                mg.assert_called_once()
                _a, kw = mg.call_args
                self.assertIn("supply_dir", kw)
                self.assertEqual(kw["supply_dir"], Path(str(supply)))
                self.assertEqual(_a[0], run_dir)
