# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import subprocess
import unittest
from pathlib import Path

ARGS = [
    "--ozone-platform=wayland",
    "--kiosk",
    "--noerrdialogs",
    "--disable-infobars",
    "--no-first-run",
    "http://127.0.0.1:8765/",
]

class TestKiosk(unittest.TestCase):
    def setUp(self):
        self.launcher = Path(__file__).resolve().parents[1] / "packaging/pmos/opos/opos-kiosk"
        self.text = self.launcher.read_text()

    def test_chromium_launch(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        stub = Path(td.name) / "chromium"
        stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@"\nexit 0\n')
        os.chmod(stub, 0o755)
        env = dict(os.environ, PATH=td.name + os.pathsep + os.environ.get("PATH", ""))
        r = subprocess.run(["sh", str(self.launcher)], capture_output=True, text=True, env=env)
        self.assertEqual((r.returncode, r.stdout.splitlines(), r.stderr), (0, ARGS, ""))

    def test_posix_syntax(self):
        r = subprocess.run(["sh", "-n", str(self.launcher)], capture_output=True)
        self.assertEqual(r.returncode, 0)

if __name__ == "__main__":
    unittest.main()
