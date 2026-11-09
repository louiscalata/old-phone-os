# SPDX-License-Identifier: GPL-3.0-or-later
"""easy-install.sh without --flash must not read or change anything."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EASY = ROOT / "packaging" / "pmos" / "easy"
SCRIPT = EASY / "easy-install.sh"


def snapshot(directory):
    found = {}
    for path in sorted(directory.iterdir()):
        info = path.stat()
        found[path.name] = (stat.S_IMODE(info.st_mode), info.st_mtime_ns, path.read_bytes())
    return found


class TestEasyInstallDryRun(unittest.TestCase):
    def test_plan_mode_does_not_mutate_or_invoke_tools(self):
        before = snapshot(EASY)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            image = tmp_path / "phone.img"
            image.write_bytes(b"not-a-real-phone-image")
            image.chmod(0)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            pm = bin_dir / "pmbootstrap"
            pm.write_text("#!/bin/sh\necho INVOKED > \"$OPOS_PM_LOG\"\nexit 99\n")
            pm.chmod(0o755)
            log = tmp_path / "pm.log"
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            env["OPOS_PM_LOG"] = str(log)
            proc = subprocess.run(
                ["sh", str(SCRIPT), str(image)],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("step 3: skipped (no --flash)", proc.stdout)
            self.assertNotIn("flashing", proc.stdout)
            self.assertEqual(proc.stderr, "")
            self.assertFalse(log.exists())
            self.assertEqual(stat.S_IMODE(image.stat().st_mode) & 0o777, 0)
            image.chmod(0o644)
            self.assertEqual(image.read_bytes(), b"not-a-real-phone-image")
            names = {path.name for path in tmp_path.iterdir()}
            self.assertEqual(names, {"phone.img", "bin"})
        self.assertEqual(snapshot(EASY), before)


if __name__ == "__main__":
    unittest.main()
