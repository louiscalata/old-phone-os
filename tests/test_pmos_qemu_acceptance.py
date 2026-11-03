# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import subprocess
import unittest

class TestQemuAcceptance(unittest.TestCase):
    def setUp(self):
        self.script = pathlib.Path(__file__).resolve().parent.parent.joinpath("packaging/pmos/qemu-acceptance.sh").read_text()
    def run_script(self, *args):
        result = subprocess.run(["sh", "-s", "--", *args], input=self.script, capture_output=True, text=True, timeout=5)
        return (result.returncode, result.stdout, result.stderr)
    def test_default_plan(self):
        self.assertEqual(self.run_script(), (0, "check 1: ssh -p 2222 user@127.0.0.1 'opos-firstboot'\ncheck 2: ssh -p 2222 user@127.0.0.1 'opos-kiosk-off --dry-run'\ncheck 3: ssh -p 2222 user@127.0.0.1 'sh -s' < tools/opos-bench.sh\n", ""))
    def test_help(self):
        self.assertEqual(self.run_script("--help"), (0, "usage: qemu-acceptance.sh [--help]\n", ""))
    def test_unknown_option(self):
        self.assertEqual(self.run_script("--bogus"), (2, "", "usage: qemu-acceptance.sh [--help]\n"))
