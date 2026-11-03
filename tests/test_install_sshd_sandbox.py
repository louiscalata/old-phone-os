# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class InstallSshdSandboxTests(unittest.TestCase):
    def run_restore(self, root, kind="managed", config_symlink=False, setup=None):
        text = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        start = text.index('    _sshd="skipped"')
        end = text.index("\n  fi\n  opos_owned_path", start)
        body = text[start:end]
        prod = "/etc/" + "ssh/" + "sshd_config"
        marker = "# Managed by OPOS" + " - sshd drop-in\n"
        home, opos, stubs = (root / name for name in ("home", "opos", "stubs"))
        for directory in (home, opos, stubs):
            directory.mkdir()
        target = root / "python3.12"
        target.write_bytes(b"original executable bytes\n")
        dropin = root / "dropin"
        if kind == "symlink":
            dropin.symlink_to(target)
        elif kind == "modified":
            dropin.write_text(marker + "user edit\n")
        else:
            dropin.write_text(marker if kind == "managed" else "user configuration\n")
        (opos / "sshd_dropin.expected").write_text(marker)
        if setup:
            setup(opos)
        backup = opos / "sshd_config.bak"
        backup.write_bytes(b"saved configuration\n")
        dest = root / "dest"
        if config_symlink:
            dest.symlink_to(target)
        else:
            dest.write_bytes(b"live configuration\n")
        for name, content in (("stat", "printf '0 600\\n'"),
                              ("sshd", "exit 0"), ("systemctl", "exit 0")):
            stub = stubs / name
            stub.write_text("#!/bin/sh\n" + content + "\n")
            stub.chmod(0o755)
        script = body.replace(prod + ".d/60-opos.conf", str(dropin)).replace(prod, str(dest))
        self.assertNotIn(prod, script)
        script += '\nprintf "restore-status=%s\\n" "$_sshd"\n'
        env = dict(os.environ, HOME=str(home), OPOS_HOME=str(opos),
                   PATH=str(stubs) + os.pathsep + os.environ.get("PATH", ""))
        proc = subprocess.run(["sh"], input=script, text=True, capture_output=True,
                              env=env, cwd=root, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc, target, dropin, dest, backup

    def test_dropin_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, target, dropin, dest, backup = self.run_restore(Path(tmp), "symlink")
            self.assertEqual(target.read_bytes(), b"original executable bytes\n")
            self.assertTrue(dropin.is_symlink())
            self.assertIn("refusing to write sshd config through symlink:", proc.stderr)
            self.assertIn("restore-status=failed", proc.stdout)
            self.assertEqual(dest.read_bytes(), b"live configuration\n")
            self.assertTrue(backup.exists())

    def test_managed_dropin_restores_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, target, dropin, dest, backup = self.run_restore(Path(tmp))
            self.assertFalse(dropin.exists())
            self.assertEqual(dest.read_bytes(), b"saved configuration\n")
            self.assertFalse(backup.exists())
            self.assertIn("restore-status=succeeded", proc.stdout)

    def test_non_opos_dropin_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, target, dropin, dest, backup = self.run_restore(Path(tmp), "unmanaged")
            self.assertEqual(dropin.read_text(), "user configuration\n")
            self.assertIn("refusing to overwrite non-OPOS file:", proc.stderr)
            self.assertIn("restore-status=failed", proc.stdout)

    def test_config_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, target, dropin, dest, backup = self.run_restore(Path(tmp), config_symlink=True)
            self.assertEqual(target.read_bytes(), b"original executable bytes\n")
            self.assertTrue(dest.is_symlink())
            self.assertTrue(backup.exists())
            self.assertIn("refusing to write sshd config through symlink:", proc.stderr)
            self.assertIn("restore-status=failed", proc.stdout)

    def test_preserved_dangling_symlink_not_written_through(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as away:
            victim = Path(away) / "python3.12"
            proc, target, dropin, dest, backup = self.run_restore(
                Path(tmp), "modified",
                setup=lambda opos: (opos / "sshd_dropin.preserved").symlink_to(victim))
            self.assertFalse(os.path.lexists(victim))
            preserved = Path(tmp) / "opos" / "sshd_dropin.preserved.1"
            self.assertFalse(preserved.is_symlink())
            self.assertEqual(preserved.read_text(), dropin.read_text())
            self.assertIn("(preserved at %s)" % preserved, proc.stderr)
            self.assertIn("restore-status=failed", proc.stdout)

    def test_preserved_numbered_dangling_symlink_skipped(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as away:
            first, second = Path(away) / "first", Path(away) / "python3.12"

            def setup(opos):
                (opos / "sshd_dropin.preserved").symlink_to(first)
                (opos / "sshd_dropin.preserved.1").symlink_to(second)

            proc, target, dropin, dest, backup = self.run_restore(Path(tmp), "modified", setup=setup)
            self.assertFalse(os.path.lexists(first))
            self.assertFalse(os.path.lexists(second))
            preserved = Path(tmp) / "opos" / "sshd_dropin.preserved.2"
            self.assertFalse(preserved.is_symlink())
            self.assertEqual(preserved.read_text(), dropin.read_text())
            self.assertIn("(preserved at %s)" % preserved, proc.stderr)
