# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import unittest

from tests.install_script_stubs import InstallStubs

SCRIPT = Path(__file__).resolve().parents[1] / "packaging/pmos/easy/easy-install.sh"
NOTICE = "this helper does not flash: --flash checks the image and then refuses"


class TestEasyInstall(unittest.TestCase):
    def setUp(self):
        self.stub = InstallStubs()
        self.addCleanup(self.stub.close)
        self.image = self.stub.image("image file.img", bytes.fromhex("3aff26ed"))

    def run_install(self, *options, answer=""):
        return self.stub.run(SCRIPT, [*options, self.image], answer)

    def test_dry_run(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"step 2: none ({NOTICE}", result.stdout)
        self.assertNotIn("step 3", result.stdout)
        self.assertEqual(self.stub.calls(), [])

    def test_flash_says_up_front_that_it_does_not_flash_and_asks_nothing(self):
        # No answer changes the outcome, and no typed confirmation is requested.
        for answer in ("", "flash\n", "no\n"):
            with self.subTest(answer=answer):
                result = self.run_install("--flash", answer=answer)
                self.assertEqual(result.returncode, 1, result.stderr)
                lines = result.stdout.splitlines()
                self.assertTrue(lines[0].startswith(NOTICE), result.stdout)
                self.assertIn("ERASES the partition they write", lines[1])
                self.assertNotIn("Type flash", result.stdout)
                self.assertNotIn("system partition", result.stdout)
                self.assertNotIn("aborted", result.stderr)
                self.assertIn("refusing to flash", result.stderr)
                self.assertEqual(self.stub.calls(), [])

    def test_yes_is_no_longer_accepted(self):
        result = self.run_install("--flash", "--yes")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("usage:", result.stderr)
        self.assertEqual(self.stub.calls(), [])

    def test_flash_refuses_after_the_checks_and_issues_no_device_command(self):
        # Even a Surface Duo or several attached devices must not be written to.
        for devices in (None, "first\tfastboot\nsecond\tfastboot\n"):
            with self.subTest(devices=devices):
                if devices is not None:
                    self.stub.env["STUB_DEVICES"] = devices
                result = self.run_install("--flash")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("format: sparse", result.stdout)
                self.assertIn(f"Selected image: {self.image}", result.stdout)
                self.assertIn("refusing to flash", result.stderr)
                self.assertEqual(self.stub.calls(), [])

    def test_invalid_image_stops_at_the_check(self):
        for kind in ("missing", "empty", "directory"):
            with self.subTest(kind=kind):
                self.image.unlink()
                if kind == "empty":
                    self.image.touch()
                elif kind == "directory":
                    self.image.mkdir()
                result = self.run_install("--flash")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("Selected image", result.stdout)
                self.assertNotIn("refusing to flash", result.stderr)
                self.assertEqual(self.stub.calls(), [])
                if self.image.is_dir():
                    self.image.rmdir()
                self.image.write_bytes(b"fixture")

    def test_unreadable_image_check_cannot_be_hidden_by_pipeline(self):
        self.stub.write_tool("od", "#!/bin/sh\nexit 1\n")
        result = self.run_install("--flash")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("cannot read image", result.stderr)
        self.assertNotIn("Selected image", result.stdout)
        self.assertNotIn("refusing to flash", result.stderr)
        self.assertEqual(self.stub.calls(), [])


if __name__ == "__main__":
    unittest.main()
