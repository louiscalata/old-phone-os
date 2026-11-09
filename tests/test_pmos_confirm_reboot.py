# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import unittest
from pathlib import Path
class TestConfirmReboot(unittest.TestCase):
    def setUp(self):
        self.script = str(Path(__file__).resolve().parent.parent / "packaging" / "pmos" / "easy" / "confirm-reboot.sh")
        Path(self.script).read_text()
        self.warn = "WARNING: this script does not reboot the phone or verify a multi-reboot setup. Physical-device flashing is untested. Do not unplug a device you choose to flash.\n"
        self.prompt = "Type reboot and press Enter to continue:\n"
    def test_yes(self):
        p = subprocess.run(["/bin/sh", self.script, "--yes"], input="", capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, self.warn, ""))
    def test_confirmation_cases(self):
        cases = [([], "reboot\n", (0, self.warn + self.prompt, "")), ([], "no\n", (1, self.warn + self.prompt, "aborted\n")), ([], "", (1, self.warn + self.prompt, "aborted\n")), (["--bogus"], "", (2, self.warn, "usage: confirm-reboot.sh [--yes]\n"))]
        for args, inp, exp in cases:
            with self.subTest(args=args, inp=inp):
                p = subprocess.run(["/bin/sh", self.script] + args, input=inp, capture_output=True, text=True)
                self.assertEqual((p.returncode, p.stdout, p.stderr), exp)
if __name__ == "__main__":
    unittest.main()
