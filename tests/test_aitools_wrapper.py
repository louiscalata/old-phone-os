# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from opos.aitools import launcher
from opos.aitools import power
from opos.aitools.catalog import Tool


def _tool(tid="bench", name="Test Tool", binary="testbin", run_env=None, lite_env=None):
    """Build tool."""
    return Tool(id=tid, name=name, binary=binary, installer_url="https://example.com/i.sh", interpreter="sh", installer_args=(), installer_env={}, archs=("aarch64", "x86_64"), min_free_mb=10, state_subdir=".state", run_env=dict(run_env or {}), lite_env=dict(lite_env or {}), signin="do signin", docs_url="https://example.com/docs")


def _supply(base, specs):
    """Create supply tree."""
    base.mkdir(parents=True, exist_ok=True)
    for e in specs:
        d = base / e["name"]
        d.mkdir(parents=True, exist_ok=True)
        if "type" in e:
            (d / "type").write_text(e["type"])
        if "status" in e:
            (d / "status").write_text(e["status"])
        if "online" in e:
            (d / "online").write_text(e["online"])
    return base


def _fake_bin(path, pid_file, args_file, env_file):
    """Create fake binary."""
    s = f"#!/bin/sh\nprintf '%s' \"$$\" > {shlex.quote(str(pid_file))}\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > {shlex.quote(str(args_file))}\nenv > {shlex.quote(str(env_file))}\n"
    path.write_text(s)
    path.chmod(0o755)


def _fake_py(path, log):
    """Create fake python."""
    s = f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> {shlex.quote(str(log))}\nexit 0\n"
    path.write_text(s)
    path.chmod(0o755)


def _run(tmp, tool, mode, specs, sandbox=None, args=None):
    """Render wrapper and execute it."""
    supply = tmp / "supply"
    _supply(supply, specs)
    sb = sandbox if sandbox is not None else tmp / "sandbox"
    sb.mkdir(parents=True, exist_ok=True)
    run_dir = tmp / "run"
    src_dir = tmp / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    pid_file = tmp / "pid.out"
    args_file = tmp / "args.out"
    env_file = tmp / "env.out"
    py_log = tmp / "py.log"
    real = tmp / "realbin"
    _fake_bin(real, pid_file, args_file, env_file)
    fpy = tmp / "fake_py"
    _fake_py(fpy, py_log)
    wrap = tmp / "wrapper.sh"
    txt = launcher.render_wrapper(tool, real, sb, run_dir, mode, str(fpy), src_dir)
    wrap.write_text(txt)
    wrap.chmod(0o755)
    env = os.environ.copy()
    env["OPOS_POWER_SUPPLY_DIR"] = str(supply)
    res = subprocess.run(["/bin/sh", str(wrap)] + (args or []), env=env, capture_output=True, text=True, timeout=5)
    # wait for background guard to create log so tempfile cleanup does not race with file creation
    if res.returncode != 75:
        for _ in range(250):
            if py_log.exists():
                break
            time.sleep(0.02)
    return res, {"supply": supply, "sandbox": sb, "run_dir": run_dir, "pid_file": pid_file, "args_file": args_file, "env_file": env_file, "real": real, "fpy": fpy, "wrap": wrap, "src": src_dir, "py_log": py_log}


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class WrapperTests(unittest.TestCase):
    """Wrapper behaviour."""

    def test_lite_blocked(self):
        """Lite blocked on discharging."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tool = _tool()
            specs = [{"name": "BAT0", "type": "Battery", "status": "Discharging"}]
            res, p = _run(tmp, tool, "lite", specs)
            self.assertEqual(res.returncode, 75)
            self.assertFalse(p["pid_file"].exists())
            msg = f"{tool.name} runs only while the phone is charging (Lite mode). Plug in power and try again, or switch to Full mode with: {p['fpy']} -m opos.aitools mode full"
            self.assertIn(msg, res.stderr)

    def test_lite_runs_charging_and_args(self):
        """Lite runs when charging and preserves args."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tool = _tool()
            specs = [{"name": "BAT0", "type": "Battery", "status": "Charging"}]
            args = ["one two", "a'b", "x  y"]
            res, p = _run(tmp, tool, "lite", specs, args=args)
            self.assertEqual(res.returncode, 0)
            self.assertTrue(p["pid_file"].exists())
            got = p["args_file"].read_text().splitlines()
            self.assertEqual(got, args)

    def test_lite_mains_online(self):
        """Lite runs with mains online."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tool = _tool()
            specs = [{"name": "AC0", "type": "Mains", "online": "1"}, {"name": "BAT0", "type": "Battery", "status": "Discharging"}]
            res, p = _run(tmp, tool, "lite", specs)
            self.assertEqual(res.returncode, 0)
            self.assertTrue(p["pid_file"].exists())

    def test_lite_no_battery(self):
        """Lite runs with no battery."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tool = _tool()
            specs = [{"name": "AC0", "type": "Mains", "online": "0"}]
            res, p = _run(tmp, tool, "lite", specs)
            self.assertEqual(res.returncode, 0)
            specs2 = []
            res2, _ = _run(Path(tempfile.mkdtemp()), tool, "lite", specs2)
            self.assertEqual(res2.returncode, 0)

    def test_full_ignores_power(self):
        """Full runs on battery."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tool = _tool()
            specs = [{"name": "BAT0", "type": "Battery", "status": "Discharging"}]
            res, p = _run(tmp, tool, "full", specs)
            self.assertEqual(res.returncode, 0)
            self.assertTrue(p["pid_file"].exists())
            self.assertNotIn("runs only while", res.stderr)

    def test_pidfile(self):
        """Pidfile matches binary pid."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tool = _tool(tid="my-tool")
            specs = [{"name": "BAT0", "type": "Battery", "status": "Charging"}]
            res, p = _run(tmp, tool, "lite", specs)
            self.assertEqual(res.returncode, 0)
            files = list(p["run_dir"].glob(f"{tool.id}.*"))
            self.assertEqual(len(files), 1)
            pf = files[0]
            self.assertEqual(pf.read_text(), tool.id)
            pid = pf.name.rsplit(".", 1)[1]
            self.assertEqual(pid, p["pid_file"].read_text().strip())

    def test_env(self):
        """Run and lite env."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sb = tmp / "sb"
            sb.mkdir()
            run_env = {"RUN_A": "rv1", "RUN_B": "{sandbox}/x"}
            lite_env = {"LITE_A": "lv1"}
            tool = _tool(run_env=run_env, lite_env=lite_env)
            specs = [{"name": "BAT0", "type": "Battery", "status": "Charging"}]
            res, p = _run(tmp, tool, "lite", specs, sandbox=sb)
            self.assertEqual(res.returncode, 0)
            env = p["env_file"].read_text()
            self.assertIn("RUN_A=rv1", env)
            self.assertIn(f"RUN_B={sb}/x", env)
            self.assertIn("LITE_A=lv1", env)
            with tempfile.TemporaryDirectory() as td2:
                tmp2 = Path(td2)
                sb2 = tmp2 / "sb2"
                sb2.mkdir()
                res2, p2 = _run(tmp2, tool, "full", specs, sandbox=sb2)
                self.assertEqual(res2.returncode, 0)
                env2 = p2["env_file"].read_text()
                self.assertIn("RUN_A=rv1", env2)
                self.assertIn(f"RUN_B={sb2}/x", env2)
                self.assertNotIn("LITE_A=lv1", env2)

    def test_sandbox_special(self):
        """Sandbox with space and quote."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sb = tmp / "a b'c"
            sb.mkdir(parents=True)
            tool = _tool(run_env={"FOO": "{sandbox}/out"}, lite_env={"BAR": "lite"})
            specs = [{"name": "BAT0", "type": "Battery", "status": "Full"}]
            args = ["hello world"]
            res, p = _run(tmp, tool, "lite", specs, sandbox=sb, args=args)
            self.assertEqual(res.returncode, 0)
            env = p["env_file"].read_text()
            self.assertIn(f"FOO={sb}/out", env)
            self.assertIn("BAR=lite", env)
            self.assertTrue(p["pid_file"].exists())

    def test_parity(self):
        """Wrapper verdict equals power module."""
        cases = [
            [],
            [{"name": "BAT0", "type": "Battery", "status": "Discharging"}],
            [{"name": "BAT0", "type": "Battery", "status": "Charging"}],
            [{"name": "BAT0", "type": "Battery", "status": "Full"}],
            [{"name": "BAT0", "type": "Battery", "status": "Not charging"}],
            [{"name": "AC0", "type": "Mains", "online": "1"}],
            [{"name": "AC0", "type": "Mains", "online": "0"}],
            [{"name": "AC0", "type": "Mains", "online": "1"}, {"name": "BAT0", "type": "Battery", "status": "Discharging"}],
            [{"name": "AC0", "type": "Mains", "online": "0"}, {"name": "BAT0", "type": "Battery", "status": "Discharging"}],
            [{"name": "USB0", "type": "USB", "online": "1"}, {"name": "BAT0", "type": "Battery", "status": "Discharging"}],
            [{"name": "BAT0", "type": "Battery"}],
            [{"name": "BAT0", "type": "Battery", "status": "Charging"}, {"name": "BAT1", "type": "Battery", "status": "Discharging"}],
            [{"name": "FOO0", "type": "Foo", "online": "0"}],
        ]
        for i, specs in enumerate(cases):
            with self.subTest(i=i):
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td)
                    tool = _tool()
                    res, p = _run(tmp, tool, "lite", specs)
                    exp = power.on_external_power(p["supply"])
                    allowed = res.returncode != 75
                    self.assertEqual(allowed, exp)
                    if exp:
                        self.assertEqual(res.returncode, 0)
                    else:
                        self.assertEqual(res.returncode, 75)
                        self.assertFalse(p["pid_file"].exists())
