# SPDX-License-Identifier: GPL-3.0-or-later
"""Test the opos.setup module entry point."""
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

class TestSetupMain(unittest.TestCase):
    def _env(self):
        repo_root = Path(__file__).resolve().parents[1]
        src_dir = repo_root / 'src'
        env = os.environ.copy()
        env['PYTHONPATH'] = str(src_dir)
        return repo_root, env

    def test_help_contains_uninstall(self):
        repo_root, env = self._env()
        result = subprocess.run(
            [sys.executable, '-m', 'opos.setup', '--help'],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn('uninstall', result.stdout)

    def test_status_unconfigured(self):
        repo_root, env = self._env()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, '-m', 'opos.setup', 'status', '--config-dir', tmpdir],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn('configured: no', result.stdout)

if __name__ == '__main__':
    unittest.main()
