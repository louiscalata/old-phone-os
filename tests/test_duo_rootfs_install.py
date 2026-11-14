# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
from pathlib import Path
import unittest

from tests.install_script_stubs import InstallStubs

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/duo-rootfs-install.sh"


class TestDuoRootfsInstall(unittest.TestCase):
    def setUp(self):
        self.stub = InstallStubs()
        self.addCleanup(self.stub.close)
        self.images = [self.stub.image(name) for name in
                       ("twrp image.img", "boot image.img", "rootfs image.img")]
        self.stub.set_rootfs(self.images[-1])

    def run_install(self, *options):
        return self.stub.run(SCRIPT, [*options, *self.images])

    def assert_refused(self, result, reason):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, result.stderr)
        for call in self.stub.commands():
            self.assertIn(call, [["fastboot", "devices"], ["fastboot", "getvar", "product"]])

    def test_default_prints_plan_and_runs_nothing(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stub.calls(), [])
        self.assertIn("ERASES userdata", result.stdout)
        self.assertIn("20 percent", result.stdout)
        lines = [line for line in result.stdout.splitlines() if line.startswith("+ ")]
        self.assertLess(next(i for i, line in enumerate(lines) if "format:ext4" in line),
                        next(i for i, line in enumerate(lines) if "adb push" in line))
        self.assertEqual(sum("fastboot boot" in line and "boot image.img" in line for line in lines), 1)
        self.assertEqual([line for line in lines if "reboot" in line], ["+ adb reboot bootloader"])

    def test_yes_requires_explicit_userdata_erasure_token(self):
        self.assert_refused(self.run_install("--yes"), "--erase-userdata")
        self.assertEqual(self.stub.calls(), [])

    def test_erasure_token_alone_stays_a_dry_run(self):
        result = self.run_install("--erase-userdata")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stub.calls(), [])

    def test_missing_empty_and_directory_images_refuse_before_device_access(self):
        for index in range(3):
            for kind in ("missing", "empty", "directory"):
                with self.subTest(image=index, kind=kind):
                    path = self.images[index]
                    path.unlink()
                    if kind == "empty":
                        path.touch()
                    elif kind == "directory":
                        path.mkdir()
                    self.assert_refused(self.run_install("--yes", "--erase-userdata"), "image")
                    self.assertEqual(self.stub.calls(), [])
                    if path.is_dir():
                        path.rmdir()
                    path.write_bytes(b"fixture image contents\n")

    def test_each_supplied_sha256_must_match(self):
        for flag in ("--twrp-sha256", "--boot-sha256", "--rootfs-sha256"):
            with self.subTest(flag=flag):
                self.assert_refused(self.run_install("--yes", "--erase-userdata", flag, "0" * 64), "SHA-256")
                self.assertEqual(self.stub.calls(), [])

    def test_invalid_sha256_refuses(self):
        for value in ("", "bad", "z" * 64, "0" * 63, "0" * 65):
            with self.subTest(value=value):
                self.assert_refused(self.run_install("--yes", "--erase-userdata", "--rootfs-sha256", value), "SHA-256")
                self.assertEqual(self.stub.calls(), [])

    def test_unavailable_or_failing_hash_check_refuses(self):
        for name in ("sha256sum", "shasum"):
            (self.stub.bin / name).unlink(missing_ok=True)
        self.assert_refused(self.run_install("--yes", "--erase-userdata"), "SHA-256")
        self.stub.write_tool("sha256sum", "#!/bin/sh\nexit 1\n")
        self.assert_refused(self.run_install("--yes", "--erase-userdata"), "SHA-256")
        self.assertEqual(self.stub.calls(), [])

    def test_hash_command_failure_is_not_hidden_by_output(self):
        self.stub.write_tool("sha256sum", "#!/bin/sh\nprintf '%s  image\\n' " + "0" * 64 + "\nexit 1\n")
        self.assert_refused(self.run_install("--yes", "--erase-userdata", "--rootfs-sha256", "0" * 64), "SHA-256")
        self.assertEqual(self.stub.calls(), [])

    def test_single_fastboot_device_is_required(self):
        for listing in ("", "\n", "unknown", "fixture\toffline\n",
                        "first\tfastboot\nsecond\tfastboot\n"):
            with self.subTest(listing=listing):
                self.stub.env["STUB_DEVICES"] = listing
                self.assert_refused(self.run_install("--yes", "--erase-userdata"), "one fastboot device")

    def test_device_listing_failure_refuses(self):
        self.stub.env["STUB_FAIL"] = "devices"
        self.assert_refused(self.run_install("--yes", "--erase-userdata"), "device")

    def test_product_mismatch_missing_malformed_or_ambiguous_refuses(self):
        for response in ("", "product: other\n", "product: surfaceduo2\n",
                         "not product: surfaceduo\n", "product: surfaceduo extra\n",
                         "product: surfaceduo\nproduct: other\n",
                         "product: surfaceduo\nproduct: surfaceduo\n"):
            with self.subTest(response=response):
                self.stub.env["STUB_PRODUCT"] = response
                self.assert_refused(self.run_install("--yes", "--erase-userdata"), "product")

    def test_failed_product_query_refuses(self):
        self.stub.env["STUB_FAIL"] = "getvar product"
        self.assert_refused(self.run_install("--yes", "--erase-userdata"), "product")

    def test_valid_images_hashes_and_product_precede_format_and_pin_target(self):
        self.stub.env["STUB_PRODUCT"] = "(bootloader) product: surfaceduo\r\nFinished. Total time: 0.1s\n"
        options = ["--yes", "--erase-userdata"]
        for flag, path in zip(("--twrp-sha256", "--boot-sha256", "--rootfs-sha256"), self.images):
            options += [flag, hashlib.sha256(path.read_bytes()).hexdigest().upper()]
        result = self.run_install(*options)
        self.assertEqual(result.returncode, 0, result.stderr)
        twrp, boot, rootfs = map(str, self.images)
        self.assertEqual(self.stub.commands(), [
            ["fastboot", "devices"],
            ["fastboot", "getvar", "product"],
            ["fastboot", "format:ext4", "userdata"],
            ["fastboot", "boot", twrp],
            ["adb", "wait-for-recovery"],
            ["adb", "shell", "mountpoint -q /data || mount -t ext4 "
             "/dev/block/platform/soc/1d84000.ufshc/by-name/userdata /data"],
            ["adb", "push", rootfs, "/data/rootfs.img"],
            ["adb", "shell", "sha256sum", "/data/rootfs.img"],
            ["adb", "shell", "wc", "-c", "/data/rootfs.img"],
            ["adb", "reboot", "bootloader"],
            ["fastboot", "getvar", "product"],
            ["fastboot", "boot", boot],
        ])
        for call in self.stub.calls()[1:]:
            self.assertEqual(call[1:3], ["-s", self.stub.device])

    def test_transcript_shows_the_real_commands_and_selected_device(self):
        result = self.run_install("--yes", "--erase-userdata")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("+ ")]
        device = self.stub.device
        self.assertEqual(lines[0], f"+ fastboot -s {device} format:ext4 userdata")
        self.assertIn(f"+ adb -s {device} push {self.images[2]} /data/rootfs.img", lines)
        self.assertEqual(lines[-1], f"+ fastboot -s {device} boot {self.images[1]}")
        for line in lines:
            self.assertFalse(line.startswith(("+ fb ", "+ ad ")), line)

    def replace_during(self, command, image):
        self.stub.env["STUB_REPLACE_ON"] = command
        self.stub.env["STUB_REPLACE_PATH"] = str(image)

    def test_rootfs_replaced_after_its_check_is_never_booted(self):
        rootfs = self.images[2]
        checked = hashlib.sha256(rootfs.read_bytes()).hexdigest()
        self.replace_during("format:ext4 userdata", rootfs)
        # The phone faithfully reports the replaced file that was pushed.
        replaced = b"replaced image contents\n"
        self.stub.env["ROOTFS_HASH"] = hashlib.sha256(replaced).hexdigest()
        self.stub.env["ROOTFS_SIZE"] = str(len(replaced))
        result = self.run_install("--yes", "--erase-userdata", "--rootfs-sha256", checked)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("verify failed", result.stderr)
        self.assertNotIn(["fastboot", "boot", str(self.images[1])], self.stub.commands())
        self.assertNotIn(["adb", "reboot", "bootloader"], self.stub.commands())

    def test_boot_image_replaced_after_its_check_is_never_booted(self):
        boot = self.images[1]
        checked = hashlib.sha256(boot.read_bytes()).hexdigest()
        self.replace_during(f"push {self.images[2]} /data/rootfs.img", boot)
        result = self.run_install("--yes", "--erase-userdata", "--boot-sha256", checked)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("boot image changed after it was checked", result.stderr)
        self.assertNotIn(["fastboot", "boot", str(boot)], self.stub.commands())
        self.assertNotIn(["adb", "reboot", "bootloader"], self.stub.commands())

    def test_twrp_replaced_while_userdata_is_erased_is_never_booted(self):
        twrp = self.images[0]
        checked = hashlib.sha256(twrp.read_bytes()).hexdigest()
        self.replace_during("format:ext4 userdata", twrp)
        result = self.run_install("--yes", "--erase-userdata", "--twrp-sha256", checked)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("TWRP image changed after it was checked", result.stderr)
        self.assertEqual(self.stub.commands()[-1], ["fastboot", "format:ext4", "userdata"])
        self.assertNotIn(["fastboot", "boot", str(twrp)], self.stub.commands())

    def test_boot_image_replaced_during_reboot_to_bootloader_is_never_booted(self):
        boot = self.images[1]
        checked = hashlib.sha256(boot.read_bytes()).hexdigest()
        self.replace_during("reboot bootloader", boot)
        result = self.run_install("--yes", "--erase-userdata", "--boot-sha256", checked)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("boot image changed after it was checked", result.stderr)
        self.assertEqual(self.stub.commands()[-1], ["fastboot", "getvar", "product"])
        self.assertNotIn(["fastboot", "boot", str(boot)], self.stub.commands())

    def test_copy_with_matching_hash_but_wrong_size_never_boots_final_image(self):
        self.stub.env["ROOTFS_SIZE"] = str(int(self.stub.env["ROOTFS_SIZE"]) + 1)
        result = self.run_install("--yes", "--erase-userdata")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verify failed", result.stderr)
        self.assertNotIn(["fastboot", "boot", str(self.images[1])], self.stub.commands())
        self.assertNotIn(["adb", "reboot", "bootloader"], self.stub.commands())

    def test_yes_stops_at_first_write_failure(self):
        self.stub.env["STUB_FAIL"] = "format:ext4 userdata"
        result = self.run_install("--yes", "--erase-userdata")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.stub.commands()[-1], ["fastboot", "format:ext4", "userdata"])
        self.assertEqual(len(self.stub.calls()), 3)

    def test_failed_copy_verification_never_boots_final_image(self):
        self.stub.env["STUB_HASH"] = "0" * 64
        result = self.run_install("--yes", "--erase-userdata")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verify failed", result.stderr)
        self.assertNotIn(["fastboot", "boot", str(self.images[1])], self.stub.commands())
        self.assertNotIn(["adb", "reboot", "bootloader"], self.stub.commands())


if __name__ == "__main__":
    unittest.main()
