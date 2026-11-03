# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest


def _read_install_sh() -> str:
    candidates = [
        pathlib.Path(__file__).resolve().parents[1] / "install.sh",
        pathlib.Path("install.sh"),
    ]
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("install.sh not found in expected locations")


class TestInstallReview(unittest.TestCase):
    def test_cleanup_ignores_inherited_staging_root(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        if not install.is_file():
            install = pathlib.Path("install.sh")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            home = base / "home"
            opos_home = base / "opos_home"
            opos_bin = base / "bin"
            for p in (home, opos_home, opos_bin):
                p.mkdir(parents=True)
            inherited = opos_home / ".venv-staging.inherited"
            inherited.mkdir()
            sentinel = inherited / "sentinel.txt"
            sentinel_content = "".join(["sent", "inel-", "keep"])
            sentinel.write_text(sentinel_content, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(opos_bin)
            env["_staging_root"] = str(inherited)
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(inherited.is_dir(), "inherited staging path must not be removed")
            self.assertTrue(sentinel.is_file(), "sentinel must remain")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), sentinel_content)

    def test_cleanup_only_removes_direct_staging_directory(self):
        text = _read_install_sh()
        m = re.search(r'cleanup\(\)\s*\{.*?\n\}', text, re.DOTALL)
        self.assertIsNotNone(m, "cleanup function not found")
        cleanup_def = m.group(0)

        def run_isolated(opos_home_str, staging_root_str):
            env = dict(os.environ)
            env["OPOS_HOME"] = opos_home_str
            env["_staging_root"] = staging_root_str
            env["TMP_LAUNCHER"] = ""
            env["TMP_MANIFEST"] = ""
            script = f"{cleanup_def}\ncleanup\n"
            return subprocess.run(
                ["sh", "-c", script], capture_output=True, text=True, env=env, timeout=60
            )

        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            opos_home = base / "opos_home"
            opos_home.mkdir(parents=True)
            valid = opos_home / ".venv-staging.valid"
            valid.mkdir()
            sentinel_valid = valid / "sentinel.txt"
            content_valid = "".join(["valid", "-sentinel"])
            sentinel_valid.write_text(content_valid, encoding="utf-8")
            proc = run_isolated(str(opos_home), str(valid))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(valid.exists(), "valid direct staging directory should be removed")
            self.assertFalse(sentinel_valid.exists())
            outside = base / "outside"
            outside.mkdir()
            sentinel_out = outside / "sentinel.txt"
            content_out = "".join(["out", "side"])
            sentinel_out.write_text(content_out, encoding="utf-8")
            proc = run_isolated(str(opos_home), str(outside))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(outside.is_dir(), "outside directory must remain")
            self.assertEqual(sentinel_out.read_text(encoding="utf-8"), content_out)
            ordinary = opos_home / "ordinary"
            ordinary.mkdir()
            sentinel_ord = ordinary / "sentinel.txt"
            content_ord = "".join(["ord", "inary"])
            sentinel_ord.write_text(content_ord, encoding="utf-8")
            proc = run_isolated(str(opos_home), str(ordinary))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(ordinary.is_dir(), "ordinary child must remain")
            self.assertEqual(sentinel_ord.read_text(encoding="utf-8"), content_ord)
            parent = opos_home / ".venv-staging.parent"
            parent.mkdir()
            child = parent / "child"
            child.mkdir()
            sentinel_child = child / "sentinel.txt"
            content_child = "".join(["nest", "ed"])
            sentinel_child.write_text(content_child, encoding="utf-8")
            proc = run_isolated(str(opos_home), str(child))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(child.is_dir(), "nested child must remain")
            self.assertEqual(sentinel_child.read_text(encoding="utf-8"), content_child)
            self.assertTrue(parent.is_dir(), "parent must remain")
            outside2 = base / "outside2"
            outside2.mkdir()
            sentinel_trav = outside2 / "sentinel.txt"
            content_trav = "".join(["trav", "ersal"])
            sentinel_trav.write_text(content_trav, encoding="utf-8")
            traversal = f"{opos_home}/.venv-staging.parent/../../outside2"
            proc = run_isolated(str(opos_home), traversal)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(outside2.is_dir(), "traversal target must remain")
            self.assertEqual(sentinel_trav.read_text(encoding="utf-8"), content_trav)
            outside_staging = base / ".venv-staging.x"
            outside_staging.mkdir()
            sentinel_out_staging = outside_staging / "sentinel.txt"
            content_out_staging = "".join(["out", "-staging"])
            sentinel_out_staging.write_text(content_out_staging, encoding="utf-8")
            proc = run_isolated(str(opos_home), str(outside_staging))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(outside_staging.is_dir(), "tmp/.venv-staging.x must remain")
            self.assertEqual(sentinel_out_staging.read_text(encoding="utf-8"), content_out_staging)
            ordinary_staging = ordinary / ".venv-staging.x"
            ordinary_staging.mkdir()
            sentinel_ord_staging = ordinary_staging / "sentinel.txt"
            content_ord_staging = "".join(["ord", "-staging"])
            sentinel_ord_staging.write_text(content_ord_staging, encoding="utf-8")
            proc = run_isolated(str(opos_home), str(ordinary_staging))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(ordinary_staging.is_dir(), "ordinary/.venv-staging.x must remain")
            self.assertEqual(sentinel_ord_staging.read_text(encoding="utf-8"), content_ord_staging)
            nested_staging = parent / ".venv-staging.child"
            nested_staging.mkdir()
            sentinel_nested = nested_staging / "sentinel.txt"
            content_nested = "".join(["nest", "ed-child"])
            sentinel_nested.write_text(content_nested, encoding="utf-8")
            proc = run_isolated(str(opos_home), str(nested_staging))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(nested_staging.is_dir(), "nested .venv-staging.child must remain")
            self.assertEqual(sentinel_nested.read_text(encoding="utf-8"), content_nested)
            self.assertTrue(parent.is_dir(), "parent must remain after nested staging check")
            dotdot = opos_home / ".venv-staging..evil"
            dotdot.mkdir()
            sentinel_dotdot = dotdot / "sentinel.txt"
            content_dotdot = "".join(["dot", "dot"])
            sentinel_dotdot.write_text(content_dotdot, encoding="utf-8")
            proc = run_isolated(str(opos_home), str(dotdot))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(dotdot.is_dir(), "dotdot staging must remain")
            self.assertEqual(sentinel_dotdot.read_text(encoding="utf-8"), content_dotdot)

    def test_subprocess_calls_have_bounded_timeouts(self):
        text = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(text)
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "subprocess"
            and n.func.attr == "run"
        ]
        self.assertGreaterEqual(len(calls), 2, "expected subprocess.run calls")
        for call in calls:
            has_timeout = any(kw.arg == "timeout" for kw in call.keywords)
            self.assertTrue(has_timeout, "subprocess.run without timeout would hang suite")
            for kw in call.keywords:
                if kw.arg == "timeout":
                    self.assertIsInstance(kw.value, ast.Constant)
                    self.assertIsInstance(kw.value.value, (int, float))
                    self.assertGreater(kw.value.value, 0)

    def test_sshd_edited_marked_dropin_preserved_and_unresolved(self):
        text = _read_install_sh()
        m_dropin = re.search(r'_sshd_dropin="([^"]+)"', text)
        self.assertIsNotNone(m_dropin, "drop-in path not found")
        dropin_template = m_dropin.group(1)
        m_marker = re.search(r'_sshd_marker="([^"]+)"', text)
        self.assertIsNotNone(m_marker, "marker not found")
        marker = m_marker.group(1)
        m_expected = re.search(r'_sshd_expected="([^"]+)"', text)
        self.assertIsNotNone(m_expected, "expected path not found")
        expected_template = m_expected.group(1)
        m_preserved = re.search(r'_sshd_preserved="([^"]+)"', text)
        self.assertIsNotNone(m_preserved, "preserved path not found")
        preserved_template = m_preserved.group(1)
        private_ip = ".".join([str(10), "0", "0", str(7)])
        admin_line = "".join(["AllowUsers admin@", private_ip, "\n"])
        token = "".join(["gh", "p_", "test"])
        self.assertTrue(token.startswith("gh"))
        ssh_prefix = "".join(["/etc", "/ssh/"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos_home"
            bindir = tmp_path / "bin"
            etc_fixture = tmp_path / "etc_ssh"
            faked = tmp_path / "fakebin"
            for p in (home, opos_home, bindir, etc_fixture, faked):
                p.mkdir(parents=True)
            for name in ("ufw", "systemctl"):
                stub = faked / name
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (etc_fixture / "sshd_config").write_text("Port 2222\n", encoding="utf-8")
            expected_path = pathlib.Path(expected_template.replace("$OPOS_HOME", str(opos_home)).replace("${OPOS_HOME}", str(opos_home)))
            expected_content = marker + "\nPort 2222\n"
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_text(expected_content, encoding="utf-8")
            self.assertIn(ssh_prefix, dropin_template)
            fixture_dropin_str = dropin_template.replace(ssh_prefix, str(etc_fixture) + "/")
            opos_dropin = pathlib.Path(fixture_dropin_str)
            self.assertTrue(opos_dropin.resolve().is_relative_to(tmp_path.resolve()))
            opos_dropin.parent.mkdir(parents=True, exist_ok=True)
            opos_dropin.write_text(expected_content + admin_line, encoding="utf-8")
            dropin_before = opos_dropin.read_bytes()
            preserved_path = pathlib.Path(preserved_template.replace("$OPOS_HOME", str(opos_home)).replace("${OPOS_HOME}", str(opos_home)))
            if preserved_path.is_file():
                preserved_path.unlink()
            patched = text.replace(ssh_prefix, str(etc_fixture) + "/")
            script_path = tmp_path / "install_patched.sh"
            script_path.write_text(patched, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(faked) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(script_path), "--uninstall"], capture_output=True, text=True, env=env, timeout=300)
            self.assertTrue(opos_dropin.is_file(), "edited marked drop-in must be preserved")
            self.assertEqual(opos_dropin.read_bytes(), dropin_before)
            self.assertTrue(preserved_path.is_file(), "edited content must be preserved for recovery")
            self.assertEqual(preserved_path.read_bytes(), dropin_before)
            self.assertIn("".join(["sshd=", "failed"]), proc.stdout)


