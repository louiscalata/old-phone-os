# SPDX-License-Identifier: GPL-3.0-or-later
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
    def test_uninstall_dry_run_outcomes(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def run(home, bindir, args):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["OPOS_RECOVERY_MARKER"] = str(home / ".recovery_invoked")
            env["OPOS_CALL_LOG"] = str(home.parent / "calls.log")
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"] + args,
                capture_output=True,
                text=True,
                env=env,
            )

        def make_verified(home, bindir):
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nprintf '%s\n' \"$0 $*\" >> \"$OPOS_CALL_LOG\"\ntouch \"$OPOS_RECOVERY_MARKER\"\nexit 42\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nprintf '%s\n' \"$0 $*\" >> \"$OPOS_CALL_LOG\"\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            extra = home / "venv" / "lib" / "extra.dat"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("extra", encoding="utf-8")
            (home / "manifest").write_text(
                "launcher=" + str(bindir / "opos") + "\n"
                + "venv=" + str(home / "venv") + "\n"
                + "repo=" + str(root) + "\n",
                encoding="utf-8",
            )
            return venv_python

        def _snapshot(home, bindir):
            found = {}
            for base in (home, bindir):
                for dirpath, _dirnames, filenames in os.walk(base):
                    for name in filenames:
                        p = pathlib.Path(dirpath) / name
                        found[str(p)] = p.read_bytes()
            return found

        def _sentinel_content():
            return "".join(["dry", "run", "sentinel"])

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            sentinel_home = home / "sentinel.txt"
            sentinel_bin = bindir / "sentinel.txt"
            content = _sentinel_content()
            sentinel_home.write_text(content, encoding="utf-8")
            sentinel_bin.write_text(content, encoding="utf-8")
            venv_python = make_verified(home, bindir)
            launcher = bindir / "opos"
            manifest_before = (home / "manifest").read_text(encoding="utf-8")
            launcher_before = launcher.read_bytes()
            python_before = venv_python.read_bytes()
            before = _snapshot(home, bindir)
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(
                "==> would run " + str(launcher) + " uninstall --dry-run",
                proc.stdout.splitlines(),
                "dry-run must report the recovery entry point and launcher path on one exact line",
            )
            self.assertTrue(venv_python.exists(), "venv fixture must remain")
            self.assertEqual(venv_python.read_bytes(), python_before, "venv python must remain unchanged")
            self.assertTrue(launcher.exists(), "launcher must remain in dry-run")
            self.assertEqual(launcher.read_bytes(), launcher_before, "launcher must remain unchanged")
            self.assertEqual(_snapshot(home, bindir), before, "dry-run must not add, remove, or modify files")
            self.assertEqual((home / "manifest").read_text(encoding="utf-8"), manifest_before, "manifest must remain unchanged")
            self.assertTrue(sentinel_home.exists())
            self.assertEqual(sentinel_home.read_text(encoding="utf-8"), content)
            self.assertTrue(sentinel_bin.exists())
            self.assertEqual(sentinel_bin.read_text(encoding="utf-8"), content)
            self.assertFalse((home / ".recovery_invoked").exists(), "recovery must not be invoked in dry-run")
            call_log = home.parent / "calls.log"
            self.assertFalse(call_log.exists() and call_log.read_bytes().strip(), "recovery entry point must not run in dry-run")

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            sentinel_home = home / "sentinel.txt"
            sentinel_bin = bindir / "sentinel.txt"
            content = _sentinel_content()
            sentinel_home.write_text(content, encoding="utf-8")
            sentinel_bin.write_text(content, encoding="utf-8")
            launcher = bindir / "opos"
            launcher_before = "".join(["#!/bin/sh\n", "printf '%s\\n' \"$0 $*\" >> \"$OPOS_CALL_LOG\"\n", "exit 0\n"]).encode("utf-8")
            launcher.write_bytes(launcher_before)
            launcher.chmod(0o755)
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_before = "".join(["#!/bin/sh\n", "printf '%s\\n' \"$0 $*\" >> \"$OPOS_CALL_LOG\"\n", "exit 0\n"]).encode("utf-8")
            venv_python.write_bytes(venv_before)
            venv_python.chmod(0o755)
            noroot = "".join([str(pathlib.Path(tmp)), "/noroot"])
            manifest_before = ("".join(["launch", "er="]) + str(launcher) + "\n" + "".join(["ve", "nv="]) + str(home / "venv") + "\n" + "".join(["re", "po="]) + noroot + "\n").encode("utf-8")
            (home / "manifest").write_bytes(manifest_before)
            self.assertTrue(os.access(launcher, os.X_OK), "launcher spy must be executable")
            self.assertTrue(os.access(venv_python, os.X_OK), "venv spy must be executable")
            proc = run(home, bindir, ["--force"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(
                "==> would run " + str(launcher) + " uninstall --dry-run",
                proc.stdout.splitlines(),
                "dry-run must report the recovery entry point and launcher path on one exact line",
            )
            self.assertTrue(launcher.exists(), "launcher must survive dry-run")
            self.assertEqual(launcher.read_bytes(), launcher_before, "launcher must survive byte-for-byte")
            self.assertTrue(venv_python.exists(), "venv must survive dry-run")
            self.assertEqual(venv_python.read_bytes(), venv_before, "venv must survive byte-for-byte")
            self.assertTrue((home / "manifest").exists(), "manifest must survive dry-run")
            self.assertEqual((home / "manifest").read_bytes(), manifest_before, "manifest must survive byte-for-byte")
            self.assertTrue(sentinel_home.exists())
            self.assertEqual(sentinel_home.read_text(encoding="utf-8"), content)
            self.assertTrue(sentinel_bin.exists())
            self.assertEqual(sentinel_bin.read_text(encoding="utf-8"), content)
            self.assertFalse((home / ".recovery_invoked").exists(), "recovery must not be invoked in dry-run")
            call_log = home.parent / "calls.log"
            self.assertFalse(call_log.exists() and call_log.read_bytes().strip(), "recovery entry point must not run in dry-run")

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            sentinel_home = home / "sentinel.txt"
            sentinel_bin = bindir / "sentinel.txt"
            content = _sentinel_content()
            sentinel_home.write_text(content, encoding="utf-8")
            sentinel_bin.write_text(content, encoding="utf-8")
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("would fail: launcher not found", proc.stderr)
            self.assertTrue(sentinel_home.exists())
            self.assertEqual(sentinel_home.read_text(encoding="utf-8"), content)
            self.assertTrue(sentinel_bin.exists())
            self.assertEqual(sentinel_bin.read_text(encoding="utf-8"), content)
            self.assertFalse((home / ".recovery_invoked").exists(), "recovery must not be invoked in dry-run")
            call_log = home.parent / "calls.log"
            self.assertFalse(call_log.exists() and call_log.read_bytes().strip(), "recovery entry point must not run in dry-run")

    def test_dry_run_preserves_tree_shape_modes_and_link_targets(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def run(home, bindir, args):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["OPOS_RECOVERY_MARKER"] = str(home / ".recovery_invoked")
            env["OPOS_CALL_LOG"] = str(home.parent / "calls.log")
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"] + args,
                capture_output=True,
                text=True,
                env=env,
            )

        def _strong_snapshot(home, bindir):
            snap = {}
            for base in (home, bindir):
                for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                    for name in list(dirnames) + list(filenames):
                        p = pathlib.Path(dirpath) / name
                        key = str(p)
                        st = os.lstat(p)
                        if stat.S_ISLNK(st.st_mode):
                            snap[key] = ("symlink", os.readlink(p))
                        elif stat.S_ISDIR(st.st_mode):
                            snap[key] = ("dir", stat.S_IMODE(st.st_mode))
                        elif stat.S_ISREG(st.st_mode):
                            with open(p, "rb") as f:
                                snap[key] = ("file", stat.S_IMODE(st.st_mode), f.read())
                        else:
                            snap[key] = ("other", stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode))
                    top = str(pathlib.Path(dirpath))
                    if top not in snap:
                        st = os.lstat(dirpath)
                        snap[top] = ("dir", stat.S_IMODE(st.st_mode))
            for base in (home, bindir):
                st = os.lstat(base)
                snap[str(base)] = ("dir", stat.S_IMODE(st.st_mode))
            return snap

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("".join(["#!/bin/sh\n", "exit 0\n"]), encoding="utf-8")
            venv_python.chmod(0o755)
            launcher = bindir / "opos"
            launcher.write_text("".join(["#!/bin/sh\n", "exit 0\n"]), encoding="utf-8")
            launcher.chmod(0o755)
            doomed_dir = home / "venv" / "lib" / "".join(["doom", "ed_dir"])
            doomed_dir.mkdir(parents=True, exist_ok=True)
            (doomed_dir / "inner.dat").write_text("".join(["in", "ner"]), encoding="utf-8")
            empty_dir = home / "venv" / "lib" / "".join(["emp", "ty_dir"])
            empty_dir.mkdir(parents=True, exist_ok=True)
            mode_probe = home / "".join(["mode", "_probe.txt"])
            mode_probe.write_text("".join(["mo", "de"]), encoding="utf-8")
            mode_probe.chmod(0o640)
            link_probe = home / "".join(["link", "_probe.txt"])
            link_content = "".join(["lin", "k-target-bytes"])
            link_probe.write_text(link_content, encoding="utf-8")
            (home / "manifest").write_text(
                "launcher=" + str(bindir / "opos") + "\n"
                + "venv=" + str(home / "venv") + "\n"
                + "repo=" + str(root) + "\n",
                encoding="utf-8",
            )
            before = _strong_snapshot(home, bindir)
            mode_before = stat.S_IMODE(os.lstat(mode_probe).st_mode)
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            after = _strong_snapshot(home, bindir)
            self.assertEqual(after, before, "dry-run must not remove dirs, chmod, or swap files for symlinks")
            self.assertTrue(doomed_dir.is_dir() and not doomed_dir.is_symlink())
            self.assertTrue((doomed_dir / "inner.dat").is_file())
            self.assertTrue(empty_dir.is_dir() and not empty_dir.is_symlink(), "dry-run must preserve empty directories")
            self.assertEqual(list(empty_dir.iterdir()), [], "empty directory must remain empty")
            self.assertEqual(stat.S_IMODE(os.lstat(mode_probe).st_mode), mode_before)
            self.assertFalse(os.path.islink(link_probe))
            self.assertEqual(link_probe.read_bytes(), link_content.encode("utf-8"))

    def test_missing_expected_treated_as_unverified_and_preserved(self):
        text = _read_install_sh()
        start = text.index('_sshd="skipped"')
        end_candidates = []
        for marker_name in ['if [ -f "$OPOS_HOME/firewall.state" ]', 'command -v ufw']:
            try:
                end_candidates.append(text.index(marker_name, start))
            except ValueError:
                pass
        self.assertTrue(end_candidates, "firewall check not found")
        end = min(end_candidates)
        snippet = text[start:end]
        m_dropin = re.search(r'_sshd_dropin="([^"]+)"', snippet)
        self.assertIsNotNone(m_dropin, "dropin path not found")
        dropin_template = m_dropin.group(1)
        m_marker = re.search(r'_sshd_marker="([^"]+)"', snippet)
        self.assertIsNotNone(m_marker, "marker not found")
        marker = m_marker.group(1)
        m_expected = re.search(r'_sshd_expected="([^"]+)"', snippet)
        self.assertIsNotNone(m_expected, "expected path not found")
        m_preserved = re.search(r'_sshd_preserved="([^"]+)"', snippet)
        self.assertIsNotNone(m_preserved, "preserved path not found")
        preserved_template = m_preserved.group(1)
        private_ip = ".".join([str(192), "0", "2", str(7)])
        admin_line = "".join(["AllowUsers admin@", private_ip, "\n"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            opos_home = tmp_path / "opos_home"
            etc_fixture = tmp_path / "etc_ssh"
            opos_home.mkdir()
            etc_fixture.mkdir()
            fixture_dropin_str = dropin_template.replace("/etc/ssh/", str(etc_fixture) + "/")
            opos_dropin = pathlib.Path(fixture_dropin_str)
            opos_dropin.parent.mkdir(parents=True, exist_ok=True)
            dropin_content = marker + "\nPort 2222\n" + admin_line
            opos_dropin.write_text(dropin_content, encoding="utf-8")
            dropin_before = opos_dropin.read_bytes()
            self.assertFalse((opos_home / "sshd_dropin.expected").exists())
            self.assertFalse((opos_home / "sshd_dropin").exists())
            snippet_repl = snippet.replace("/etc/ssh/", str(etc_fixture) + "/")
            script_path = tmp_path / "run.sh"
            script_content = "set -eu\nOPOS_HOME=\"" + str(opos_home) + "\"\nexport OPOS_HOME\n" + snippet_repl + "\nprintf \"sshd=%s\\n\" \"$_sshd\"\n"
            script_path.write_text(script_content, encoding="utf-8")
            proc = subprocess.run(["sh", str(script_path)], capture_output=True, text=True, env=dict(os.environ, OPOS_HOME=str(opos_home)))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            preserved_path = pathlib.Path(preserved_template.replace("$OPOS_HOME", str(opos_home)))
            self.assertTrue(preserved_path.is_file(), "missing expected must preserve active drop-in")
            self.assertEqual(preserved_path.read_bytes(), dropin_before)
            self.assertTrue(opos_dropin.exists(), "active drop-in must remain for manual reconciliation")
            self.assertEqual(opos_dropin.read_bytes(), dropin_before)
            self.assertIn("failed", proc.stdout)
            self.assertIn("manual", (proc.stdout + proc.stderr).lower())
            self.assertIn("reconcil", (proc.stdout + proc.stderr).lower())
