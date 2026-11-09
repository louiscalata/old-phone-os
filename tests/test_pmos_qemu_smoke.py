# SPDX-License-Identifier: GPL-3.0-or-later
"""qemu-smoke.sh plan mode must not boot, and --guest is a tiny initramfs only."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "pmos" / "qemu-smoke.sh"


class TestQemuSmokePlan(unittest.TestCase):
    def test_plan_does_not_invoke_qemu(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "qemu-system-x86_64"
            marker = tmp_path / "qemu-invoked"
            fake.write_text("#!/bin/sh\necho INVOKED > \"$OPOS_QEMU_MARKER\"\nexit 99\n")
            fake.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            env["OPOS_QEMU_MARKER"] = str(marker)
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--plan"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("does not boot postmarketOS", proc.stdout)
            self.assertIn("Physical-device flashing is untested.", proc.stdout)
            self.assertNotIn("OPOS_QEMU_GUEST_SMOKE_OK", proc.stdout)
            self.assertFalse(marker.exists())

    def test_default_matches_plan(self):
        plan = subprocess.run(["sh", str(SCRIPT), "--plan"], capture_output=True, text=True, check=False)
        default = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(plan.returncode, 0)
        self.assertEqual((default.returncode, default.stdout, default.stderr), (0, plan.stdout, ""))

    def test_help_and_unknown(self):
        help_proc = subprocess.run(["sh", str(SCRIPT), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(help_proc.returncode, 0)
        self.assertIn("usage: qemu-smoke.sh", help_proc.stdout)
        bad = subprocess.run(["sh", str(SCRIPT), "--flash"], capture_output=True, text=True, check=False)
        self.assertEqual(bad.returncode, 2)


class TestQemuGuestSmoke(unittest.TestCase):
    def test_guest_prints_marker_when_tools_exist(self):
        if not shutil.which("qemu-system-x86_64") or not shutil.which("busybox"):
            self.skipTest("qemu guest tools are not installed")
        readable = [path for path in Path("/boot").glob("vmlinuz-*") if os.access(path, os.R_OK)]
        if not os.environ.get("OPOS_QEMU_KERNEL") and not readable:
            self.skipTest("no readable guest kernel")
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "guest.log"
            env = os.environ.copy()
            env["OPOS_QEMU_SMOKE_LOG"] = str(log)
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--guest"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + "\n" + log.read_text(encoding="utf-8", errors="replace"))
            text = log.read_text(encoding="utf-8", errors="replace")
            self.assertIn("OPOS_QEMU_GUEST_SMOKE_OK", text)
            self.assertIn("not a postmarketOS or OPOS UI boot", text)
            self.assertIn("Physical-device flashing was not performed.", text)


if __name__ == "__main__":
    unittest.main()
