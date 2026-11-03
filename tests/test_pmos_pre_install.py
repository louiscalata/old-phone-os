# SPDX-License-Identifier: GPL-3.0-or-later
import shlex
import subprocess
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "packaging" / "pmos" / "opos" / "opos.pre-install"


class TestPreInstall(unittest.TestCase):
    def test_header_and_exit(self):
        lines = HOOK.read_text().splitlines()
        self.assertEqual((lines[0], lines[1], lines[-1]), ("#!/bin/sh", "# SPDX-License-Identifier: GPL-3.0-or-later", "exit 0"))

    def test_account_commands(self):
        lines = HOOK.read_text().splitlines()
        cmds = [shlex.split(l) for l in lines[:-1] if l.strip() and not l.strip().startswith("#")]
        self.assertEqual(cmds, [["addgroup", "-S", "opos", "2>/dev/null", "||", "true"], ["adduser", "-S", "-D", "-H", "-h", "/var/lib/opos", "-s", "/sbin/nologin", "-G", "opos", "-g", "Old Phone OS", "opos", "2>/dev/null", "||", "true"]])

    def test_shell_syntax(self):
        proc = subprocess.run(["sh", "-n", str(HOOK)], capture_output=True)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
