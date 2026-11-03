# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSTINSTALL = ROOT / "packaging" / "pmos" / "opos" / "opos-openrc.post-install"
SERVER = ROOT / "packaging" / "pmos" / "opos" / "opos-server.initd"


class OpenrcServerRunlevelTests(unittest.TestCase):
    def test_server_added_to_default(self):
        lines = [line.strip() for line in POSTINSTALL.read_text().splitlines()]
        self.assertEqual(lines.count("rc-update add opos-server default"), 1)

    def test_firstboot_still_added(self):
        lines = [line.strip() for line in POSTINSTALL.read_text().splitlines()]
        self.assertEqual(lines.count("rc-update add opos-firstboot default"), 1)

    def test_exit_zero_is_last(self):
        lines = [line.strip() for line in POSTINSTALL.read_text().splitlines() if line.strip()]
        self.assertEqual(lines[-1], "exit 0")
        for command in ("rc-update add opos-firstboot default", "rc-update add opos-server default"):
            self.assertLess(lines.index(command), lines.index("exit 0"))

    def test_server_service_exists(self):
        self.assertTrue(SERVER.is_file())
        self.assertIn('name="opos-server"', SERVER.read_text())

    def test_posix_syntax(self):
        result = subprocess.run(["sh", "-n", str(POSTINSTALL)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
