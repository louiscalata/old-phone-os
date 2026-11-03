# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for opos.setup.selftest."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import opos.setup.apply  # noqa: F401  # bind the real config functions before the mock.patch calls below
from opos.setup import selftest

NAMES = ["python", "imports", "config", "token", "device", "services", "wizard", "app"]


class RunChecksTests(unittest.TestCase):
    """Tests for run_checks."""

    @classmethod
    def setUpClass(cls):
        """Run the checks once for the shared assertions."""
        cls.results = selftest.run_checks()

    def test_names_in_order_and_all_pass(self):
        """Return exactly the eight named checks, all passing."""
        self.assertEqual([name for name, _, _ in self.results], NAMES)
        self.assertTrue(all(ok for _, ok, _ in self.results), self.results)

    def test_isolation_leaves_patched_dirs_empty(self):
        """Leave patched home and config directories untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            dirs = [Path(tmp, name) for name in ("home", "opos", "xdg")]
            for path in dirs:
                path.mkdir()
            env = {"HOME": str(dirs[0]), "OPOS_CONFIG_DIR": str(dirs[1]), "XDG_CONFIG_HOME": str(dirs[2])}
            with mock.patch.dict(os.environ, env):
                selftest.run_checks()
            for path in dirs:
                self.assertEqual(os.listdir(path), [])

    def test_failure_is_contained(self):
        """Turn a config failure into a failed check without raising."""
        with mock.patch("opos.setup.config.save", side_effect=RuntimeError("x")):
            results = selftest.run_checks()
        self.assertEqual(len(results), 8)
        self.assertEqual([name for name, _, _ in results], NAMES)
        self.assertTrue(any(n == "config" and not ok and "RuntimeError" in d for n, ok, d in results))

    def test_nonexistent_repo_root_fails_services(self):
        """Fail only via the services check for a missing repo root."""
        results = selftest.run_checks(Path("/nonexistent/repo"))
        checks = {name: ok for name, ok, _ in results}
        self.assertFalse(checks["services"])

    def test_module_import_binds_real_apply_functions(self):
        """Importing this module binds real apply functions."""
        root = Path(__file__).resolve().parents[1]
        code = (
            "import sys\n"
            "import tests.test_setup_selftest\n"
            "from opos.setup import config\n"
            "mod = sys.modules.get(\"opos.setup.apply\")\n"
            "ok = mod is not None and mod.ensure_token is config.ensure_token and mod.save is config.save\n"
            "sys.exit(0 if ok else 1)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env=dict(os.environ, PYTHONPATH=str(root) + os.pathsep + str(root / "src")),
        )
        self.assertEqual(result.returncode, 0)


class MainTests(unittest.TestCase):
    """Tests for main."""

    def run_main(self, checks):
        """Run main with patched checks and captured stdout."""
        out = io.StringIO()
        with mock.patch.object(selftest, "run_checks", return_value=checks), contextlib.redirect_stdout(out):
            code = selftest.main()
        return code, out.getvalue()

    def test_main_all_passing(self):
        """Return 0 and report success when every check passes."""
        code, out = self.run_main([(name, True, "fine") for name in NAMES])
        self.assertEqual(code, 0)
        self.assertIn("self-test passed", out)
        self.assertIn("ok    python  fine", out.splitlines())

    def test_main_one_failing(self):
        """Return 1 and report failure when one check fails."""
        checks = [(name, name != "token", "bad" if name == "token" else "fine") for name in NAMES]
        code, out = self.run_main(checks)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  token  bad", out.splitlines())
        self.assertIn("self-test FAILED", out)


class OptimizedSelftestTests(unittest.TestCase):
    """Regression for explicit checks surviving python -O."""

    def _run_code(self, code, optimized):
        cmd = [sys.executable]
        if optimized:
            cmd.append("-O")
        cmd.extend(["-c", code])
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(res.returncode, 0, msg=res.stderr + res.stdout)
        line = res.stdout.strip().splitlines()[-1]
        return json.loads(line)

    def _by_name(self, checks):
        return {n: (ok, detail) for n, ok, detail in checks}

    def test_config_roundtrip_mismatch_survives_optimization(self):
        """Detect wrong config round trip in normal and optimized Python."""
        base = "import json\nfrom opos.setup.selftest import run_checks\nprint(json.dumps(run_checks()))\n"
        normal_ok = self._run_code(base, False)
        opt_ok = self._run_code(base, True)
        for checks in (normal_ok, opt_ok):
            by = self._by_name(checks)
            self.assertTrue(by["config"][0], checks)
            self.assertEqual(by["config"][1], "ok")
        from opos.setup.config import default_config, load, save
        cfg = default_config()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cfg.json"
            save(cfg, p)
            raw = p.read_bytes()
            loaded = load(p)
            self.assertEqual(loaded, cfg)
            self.assertEqual(p.read_bytes(), raw)
            if isinstance(cfg, dict):
                self.assertEqual(json.loads(raw.decode()), cfg)
        inj = (
            "import json\n"
            "from unittest import mock\n"
            "with mock.patch('opos.setup.config.load', return_value={'injected': True}):\n"
            "    from opos.setup.selftest import run_checks\n"
            "    print(json.dumps(run_checks()))\n"
        )
        normal_bad = self._run_code(inj, False)
        opt_bad = self._run_code(inj, True)
        for checks in (normal_bad, opt_bad):
            by = self._by_name(checks)
            self.assertFalse(by["config"][0], checks)
            self.assertIn("AssertionError", by["config"][1])
            self.assertIn("round-trip", by["config"][1])
            self.assertIn("config", [n for n, ok, _ in checks if not ok])

    def test_http_status_mismatch_survives_optimization(self):
        """Detect wrong HTTP status in normal and optimized Python."""
        base = "import json\nfrom opos.setup.selftest import run_checks\nprint(json.dumps(run_checks()))\n"
        normal_ok = self._run_code(base, False)
        opt_ok = self._run_code(base, True)
        for checks in (normal_ok, opt_ok):
            by = self._by_name(checks)
            self.assertTrue(by["wizard"][0], checks)
            self.assertTrue(by["app"][0], checks)
        inj = (
            "import json\n"
            "from unittest import mock\n"
            "with mock.patch('opos.setup.selftest._get', return_value=(500, b'')):\n"
            "    from opos.setup.selftest import run_checks\n"
            "    print(json.dumps(run_checks()))\n"
        )
        normal_bad = self._run_code(inj, False)
        opt_bad = self._run_code(inj, True)
        for checks in (normal_bad, opt_bad):
            by = self._by_name(checks)
            self.assertFalse(by["wizard"][0], checks)
            self.assertIn("AssertionError", by["wizard"][1])
            self.assertIn("500", by["wizard"][1])
            self.assertIn("wizard", [n for n, ok, _ in checks if not ok])
            self.assertFalse(by["app"][0], checks)
            self.assertIn("AssertionError", by["app"][1])
            self.assertIn("500", by["app"][1])


    def test_remaining_guards_survive_optimization(self):
        """Detect removed validations in normal and optimized Python."""
        cases = [
            (
                "config permissions",
                "config",
                "AssertionError: config permissions not 0o600",
                "import json\n"
                "from types import SimpleNamespace\n"
                "from unittest import mock\n"
                "import opos.setup.config as cfgmod\n"
                "import opos.setup.selftest as stmod\n"
                "real_save = cfgmod.save\n"
                "def _bad_save(c, p, *a, **k):\n"
                "    r = real_save(c, p, *a, **k)\n"
                "    import os as _os\n"
                "    _os.chmod(p, 0o644)\n"
                "    return r\n"
                "with mock.patch('opos.setup.config.save', side_effect=_bad_save):\n"
                "    with mock.patch('opos.setup.selftest.os', SimpleNamespace(name='posix')):\n"
                "        print(json.dumps(stmod.run_checks()))\n"
            ),
            (
                "token mismatch",
                "token",
                "AssertionError: token mismatch or too short",
                "import json\n"
                "from pathlib import Path\n"
                "from unittest import mock\n"
                "import opos.setup.config as cfgmod\n"
                "real_ensure = cfgmod.ensure_token\n"
                "calls = [0]\n"
                "def _fake(path, *a, **k):\n"
                "    if Path(path).name == 'tok':\n"
                "        calls[0] += 1\n"
                "        if calls[0] == 1:\n"
                "            return 'a' * 43\n"
                "        return 'b' * 43\n"
                "    return real_ensure(path, *a, **k)\n"
                "with mock.patch('opos.setup.config.ensure_token', side_effect=_fake):\n"
                "    from opos.setup.selftest import run_checks\n"
                "    print(json.dumps(run_checks()))\n"
            ),
            (
                "short token",
                "token",
                "AssertionError: token mismatch or too short",
                "import json\n"
                "from pathlib import Path\n"
                "from unittest import mock\n"
                "import opos.setup.config as cfgmod\n"
                "real_ensure = cfgmod.ensure_token\n"
                "def _fake(path, *a, **k):\n"
                "    if Path(path).name == 'tok':\n"
                "        return 'a' * 31\n"
                "    return real_ensure(path, *a, **k)\n"
                "with mock.patch('opos.setup.config.ensure_token', side_effect=_fake):\n"
                "    from opos.setup.selftest import run_checks\n"
                "    print(json.dumps(run_checks()))\n"
            ),
            (
                "device type",
                "device",
                "AssertionError: device missing required keys",
                "import json\n"
                "from unittest import mock\n"
                "with mock.patch('opos.setup.device.detect_device', return_value=[]):\n"
                "    from opos.setup.selftest import run_checks\n"
                "    print(json.dumps(run_checks()))\n"
            ),
            (
                "device keys",
                "device",
                "AssertionError: device missing required keys",
                "import json\n"
                "from unittest import mock\n"
                "with mock.patch('opos.setup.device.detect_device', return_value={'id': 'test', 'name': 'test'}):\n"
                "    from opos.setup.selftest import run_checks\n"
                "    print(json.dumps(run_checks()))\n"
            ),
            (
                "service hardening",
                "services",
                "AssertionError: services unit missing required entries",
                "import json\n"
                "from unittest import mock\n"
                "with mock.patch('opos.setup.services.render_user_unit', return_value='token-file'):\n"
                "    from opos.setup.selftest import run_checks\n"
                "    print(json.dumps(run_checks()))\n"
            ),
            (
                "service token option",
                "services",
                "AssertionError: services unit missing required entries",
                "import json\n"
                "from unittest import mock\n"
                "with mock.patch('opos.setup.services.render_user_unit', return_value='NoNewPrivileges=yes'):\n"
                "    from opos.setup.selftest import run_checks\n"
                "    print(json.dumps(run_checks()))\n"
            ),
            (
                "wizard state status",
                "wizard",
                "AssertionError: wizard state status 500 != 200",
                "import json\n"
                "from unittest import mock\n"
                "import opos.setup.selftest as stmod\n"
                "real_get = stmod._get\n"
                "def _fake(*a, **k):\n"
                "    vals = list(a) + list(k.values())\n"
                "    if '/setup/api/state' in vals:\n"
                "        return (500, json.dumps({'device': {}}).encode())\n"
                "    return real_get(*a, **k)\n"
                "with mock.patch('opos.setup.selftest._get', side_effect=_fake):\n"
                "    print(json.dumps(stmod.run_checks()))\n"
            ),
            (
                "wizard state content",
                "wizard",
                "AssertionError: wizard state missing device",
                "import json\n"
                "from unittest import mock\n"
                "import opos.setup.selftest as stmod\n"
                "real_get = stmod._get\n"
                "def _fake(*a, **k):\n"
                "    vals = list(a) + list(k.values())\n"
                "    if '/setup/api/state' in vals:\n"
                "        return (200, json.dumps({}).encode())\n"
                "    return real_get(*a, **k)\n"
                "with mock.patch('opos.setup.selftest._get', side_effect=_fake):\n"
                "    print(json.dumps(stmod.run_checks()))\n"
            ),
            (
                "app token length",
                "app",
                "AssertionError: app token length 40 < 43",
                "import json\n"
                "from pathlib import Path\n"
                "from unittest import mock\n"
                "import opos.setup.config as cfgmod\n"
                "real_ensure = cfgmod.ensure_token\n"
                "def _fake(path, *a, **k):\n"
                "    if Path(path).name == 'app-token':\n"
                "        return 'a' * 40\n"
                "    return real_ensure(path, *a, **k)\n"
                "with mock.patch('opos.setup.config.ensure_token', side_effect=_fake):\n"
                "    from opos.setup.selftest import run_checks\n"
                "    print(json.dumps(run_checks()))\n"
            ),
        ]
        for case, target, expected, code in cases:
            for optimized in (False, True):
                with self.subTest(case=case, optimized=optimized):
                    checks = self._run_code(code, optimized)
                    self.assertEqual([n for n, _, _ in checks], NAMES)
                    by = self._by_name(checks)
                    self.assertFalse(by[target][0], checks)
                    self.assertEqual(by[target][1], expected)


class AppTokenLengthTests(unittest.TestCase):
    """Regression for app token length enforcement."""

    def test_short_token_is_rejected(self):
        """Short token must fail; valid token must still pass."""
        with mock.patch("opos.setup.config.ensure_token", return_value="a" * 40):
            results = selftest.run_checks()
            by = {name: ok for name, ok, _ in results}
            self.assertFalse(by["app"], results)
            detail = {name: d for name, _, d in results}["app"]
            self.assertIn("AssertionError", detail)
            self.assertIn("token length", detail.lower())
            self.assertIn("43", detail)
        with mock.patch("opos.setup.config.ensure_token", return_value="b" * 43):
            results = selftest.run_checks()
            by = {name: ok for name, ok, _ in results}
            self.assertTrue(by["app"], results)

    def test_token_bytes_are_preserved(self):
        """Server must receive exact persisted bytes, not a repaired copy."""
        import opos.web as webmod

        orig = webmod.make_server
        seen: list[str | None] = []

        def spy(*args, **kwargs):
            tok = kwargs.get("token")
            seen.append(tok)
            return orig(*args, **kwargs)

        with mock.patch("opos.web.make_server", side_effect=spy):
            with mock.patch("opos.setup.config.ensure_token", return_value="x" * 40):
                results = selftest.run_checks()
                self.assertEqual(seen[-1], "x" * 40)
                self.assertFalse({n: ok for n, ok, _ in results}["app"], results)
        seen.clear()
        with mock.patch("opos.web.make_server", side_effect=spy):
            with mock.patch("opos.setup.config.ensure_token", return_value="y" * 43):
                results = selftest.run_checks()
                self.assertEqual(seen[-1], "y" * 43)
                self.assertTrue({n: ok for n, ok, _ in results}["app"], results)

    def test_long_token_is_accepted(self):
        token_base = "0123456789" + "abcdef"
        token = token_base * 4
        with mock.patch("opos.setup.config.ensure_token", return_value=token):
            results = selftest.run_checks()
            by = {name: ok for name, ok, _ in results}
            self.assertTrue(by["app"], results)


class NoAssertNodesTests(unittest.TestCase):
    """Ensure selftest contains no assert statements."""

    def test_no_assert_nodes(self):
        import ast
        from pathlib import Path
        path = Path(selftest.__file__)
        tree = ast.parse(path.read_text())
        asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
        self.assertEqual(len(asserts), 0, "selftest.py must not contain assert statements")


if __name__ == "__main__":
    unittest.main()
