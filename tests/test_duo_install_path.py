# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class DuoInstallPathTest(unittest.TestCase):
    def test_path_block_is_written_once(self):
        ROOT = Path(__file__).resolve().parents[1]
        text = (ROOT / "install.sh").read_text()
        start = text.index('pp=":$PATH:"')
        end = text.index("\nesac\n", start) + 6
        block = text[start:end]
        with tempfile.TemporaryDirectory() as home:
            profile = Path(home) / ".profile"
            profile.write_bytes(b"# existing profile")
            bindir = home + "/.local/bin"
            env = {"HOME": home, "PATH": "/usr/bin:/bin", "OPOS_BIN_DIR": home + "/.local/bin", "DRY": "0"}
            first = subprocess.run(["/bin/sh", "-c", block], env=env, capture_output=True, text=True, check=True)
            first_bytes = profile.read_bytes()
            second = subprocess.run(["/bin/sh", "-c", block], env=env, capture_output=True, text=True, check=True)
            second_bytes = profile.read_bytes()
            self.assertEqual(first_bytes, b"# existing profile\n# added by Old Phone OS install\nexport PATH=\"" + bindir.encode() + b":$PATH\"\n")
            self.assertEqual(second_bytes, first_bytes)
            self.assertEqual((first.stdout, second.stdout), ("==> added " + bindir + " to PATH in " + home + "/.profile (open a new shell)\n", ""))
