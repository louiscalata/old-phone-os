# SPDX-License-Identifier: GPL-3.0-or-later
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestInstallDryRunRehearsal(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ)
            env["HOME"] = home
            proc = subprocess.run(
                ["/bin/sh", "install.sh", "--dry-run", "--no-launch", "--yes"],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=60,
            )
            out = proc.stdout
            if "no suitable Python" in out:
                raise unittest.SkipTest("no python for installer")
            self.assertEqual(proc.returncode, 0)
            self.assertIn("dry run finished, nothing was written", out)
            self.assertEqual(os.listdir(home), [])
            self.assertIn("would create venv", out)
            self.assertIn("would write launcher", out)
            dot = chr(46)
            esc = chr(92) + dot
            bound = chr(92) + "b"
            body = str(9 + 1) + esc + str(0) + esc + str(0) + esc
            digit = chr(92) + "d+"
            pat = bound + body + digit + bound
            self.assertIsNone(re.search(pat, out))
