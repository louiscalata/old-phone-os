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
        # Different contents, so a test can tell which image a command was given.
        self.images = [self.stub.image(name, name.encode() + b" contents\n") for name in
                       ("twrp image.img", "boot image.img", "rootfs image.img")]
        self.hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in self.images]
        self.stub.set_rootfs(self.images[-1])

    def run_install(self, *options):
        return self.stub.run(SCRIPT, [*options, *self.images])

    def assert_refused(self, result, reason):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, result.stderr)
        for call in self.stub.commands():
            self.assertIn(call, [["fastboot", "devices"], ["fastboot", "getvar", "product"],
                                 ["fastboot", "getvar", "unlocked"]])
        self.assert_no_private_copy_left()

    def assert_no_private_copy_left(self):
        self.assertEqual(list(self.stub.private.iterdir()), [])

    def assert_booted_checked_copies(self, *expected):
        """Each RAM-boot got a private read-only copy holding the checked bytes."""
        booted = self.stub.booted()
        self.assertEqual([entry["sha256"] for entry in booted], list(expected))
        for entry in booted:
            path = Path(entry["path"])
            self.assertNotIn(path, self.images)
            self.assertEqual(path.parent.parent, self.stub.private)
            self.assertEqual(entry["mode"], 0o400)
            self.assertEqual(entry["folder_mode"], 0o700)
        self.assert_no_private_copy_left()

    def test_default_prints_plan_and_runs_nothing(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stub.calls(), [])
        self.assert_no_private_copy_left()
        self.assertIn("ERASES userdata", result.stdout)
        self.assertIn("20 percent", result.stdout)
        # The battery is a manual step: the script says it does not check it.
        self.assertIn("this script does not check the battery", result.stdout)
        lines = [line for line in result.stdout.splitlines() if line.startswith("+ ")]
        # The plan names the checks; the first one comes directly before the erase.
        self.assertEqual(lines[:2], ["+ recheck_images_before_erase", "+ fastboot format:ext4 userdata"])
        self.assertLess(lines.index("+ recheck_rootfs_image"),
                        next(i for i, line in enumerate(lines) if "adb push" in line))
        self.assertEqual(lines[-3:-1], ["+ fastboot getvar product", "+ recheck_boot_image"])
        self.assertNotIn("+ recheck_device_product", lines)
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
                    path.write_bytes(path.name.encode() + b" contents\n")

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
        rootfs = str(self.images[2])
        twrp, boot = (entry["path"] for entry in self.stub.booted())
        self.assert_booted_checked_copies(self.hashes[0], self.hashes[1])
        self.assertEqual(self.stub.commands(), [
            ["fastboot", "devices"],
            ["fastboot", "getvar", "product"],
            ["fastboot", "getvar", "unlocked"],
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
        self.assertEqual(lines[:2], ["+ recheck_images_before_erase",
                                     f"+ fastboot -s {device} format:ext4 userdata"])
        self.assertIn(f"+ adb -s {device} push {self.images[2]} /data/rootfs.img", lines)
        # The product check after the reboot is shown once, as the real command.
        self.assertEqual([line for line in lines if "product" in line],
                         [f"+ fastboot -s {device} getvar product"])
        self.assertEqual(lines[-3:-1], [f"+ fastboot -s {device} getvar product",
                                        "+ recheck_boot_image"])
        twrp, boot = (entry["path"] for entry in self.stub.booted())
        self.assertIn(f"+ fastboot -s {device} boot {twrp}", lines)
        self.assertEqual(lines[-1], f"+ fastboot -s {device} boot {boot}")
        for line in lines:
            self.assertFalse(line.startswith(("+ fb ", "+ ad ")), line)

    def replace_during(self, command, image):
        self.stub.env["STUB_REPLACE_ON"] = command
        self.stub.env["STUB_REPLACE_PATH"] = str(image)

    def private_copy(self, name):
        # The private folder's name is random: the stub expands the pattern when it fires.
        return self.stub.private / "*" / name

    def run_checked(self):
        options = ["--yes", "--erase-userdata"]
        for flag, value in zip(("--twrp-sha256", "--boot-sha256", "--rootfs-sha256"), self.hashes):
            options += [flag, value]
        return self.run_install(*options)

    def assert_refused_before_erase(self, result, reason):
        self.assert_refused(result, reason)
        self.assertEqual(self.stub.commands()[-1], ["fastboot", "getvar", "unlocked"])
        self.assertEqual(self.stub.booted(), [])

    # The last device command before the erase is the unlocked query.
    LAST_BEFORE_ERASE = "getvar unlocked"

    def test_each_image_replaced_before_the_erase_refuses_before_the_erase(self):
        for index, label in enumerate(("TWRP image", "boot image", "rootfs image")):
            with self.subTest(image=label):
                self.stub.log.unlink(missing_ok=True)
                original = self.images[index].read_bytes()
                self.replace_during(self.LAST_BEFORE_ERASE, self.images[index])
                self.assert_refused_before_erase(self.run_checked(),
                                                 f"{label} changed after it was checked")
                self.images[index].write_bytes(original)

    def test_each_private_copy_replaced_before_the_erase_refuses_before_the_erase(self):
        for name, label in (("twrp.img", "TWRP image"), ("boot.img", "boot image")):
            with self.subTest(copy=name):
                self.stub.log.unlink(missing_ok=True)
                self.replace_during(self.LAST_BEFORE_ERASE, self.private_copy(name))
                self.assert_refused_before_erase(self.run_checked(),
                                                 f"{label} changed after it was checked")

    def test_twrp_file_replaced_after_the_erase_boots_the_checked_copy(self):
        self.replace_during("format:ext4 userdata", self.images[0])
        result = self.run_checked()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_booted_checked_copies(self.hashes[0], self.hashes[1])

    def test_boot_file_replaced_after_the_erase_boots_the_checked_copy(self):
        for command in (f"push {self.images[2]} /data/rootfs.img", "reboot bootloader"):
            with self.subTest(during=command):
                self.stub.log.unlink(missing_ok=True)
                self.stub.booted_log.unlink(missing_ok=True)
                original = self.images[1].read_bytes()
                self.replace_during(command, self.images[1])
                result = self.run_checked()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_booted_checked_copies(self.hashes[0], self.hashes[1])
                self.images[1].write_bytes(original)

    def test_twrp_copy_replaced_while_userdata_is_erased_is_never_booted(self):
        self.replace_during("format:ext4 userdata", self.private_copy("twrp.img"))
        result = self.run_checked()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("TWRP image changed after it was checked", result.stderr)
        self.assertEqual(self.stub.commands()[-1], ["fastboot", "format:ext4", "userdata"])
        self.assertEqual(self.stub.booted(), [])
        self.assert_no_private_copy_left()

    def test_boot_copy_replaced_during_the_push_is_never_booted(self):
        self.replace_during(f"push {self.images[2]} /data/rootfs.img", self.private_copy("boot.img"))
        result = self.run_checked()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("boot image changed after it was checked", result.stderr)
        self.assertEqual([entry["sha256"] for entry in self.stub.booted()], [self.hashes[0]])
        self.assertNotIn(["adb", "reboot", "bootloader"], self.stub.commands())

    def test_boot_copy_replaced_during_reboot_to_bootloader_is_never_booted(self):
        self.replace_during("reboot bootloader", self.private_copy("boot.img"))
        result = self.run_checked()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("boot image changed after it was checked", result.stderr)
        self.assertEqual(self.stub.commands()[-1], ["fastboot", "getvar", "product"])
        self.assertEqual([entry["sha256"] for entry in self.stub.booted()], [self.hashes[0]])

    def test_rootfs_replaced_while_userdata_is_erased_is_never_pushed(self):
        self.replace_during("format:ext4 userdata", self.images[2])
        result = self.run_checked()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("rootfs image changed after it was checked", result.stderr)
        self.assertNotIn("push", [call[1] for call in self.stub.commands()])
        self.assertEqual([entry["sha256"] for entry in self.stub.booted()], [self.hashes[0]])

    def test_rootfs_replaced_during_the_push_is_never_booted(self):
        rootfs = self.images[2]
        self.replace_during(f"push {rootfs} /data/rootfs.img", rootfs)
        # The phone faithfully reports the replaced file that was pushed.
        replaced = b"replaced image contents\n"
        self.stub.env["ROOTFS_HASH"] = hashlib.sha256(replaced).hexdigest()
        self.stub.env["ROOTFS_SIZE"] = str(len(replaced))
        result = self.run_checked()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("verify failed", result.stderr)
        self.assertEqual([entry["sha256"] for entry in self.stub.booted()], [self.hashes[0]])
        self.assertNotIn(["adb", "reboot", "bootloader"], self.stub.commands())

    def test_product_after_the_reboot_must_be_the_product_checked_before_the_erase(self):
        for response in ("product: otherphone\n", "product: surfaceduo2\n", "",
                         "product: surfaceduo extra\n",
                         "product: surfaceduo\nproduct: surfaceduo\n"):
            with self.subTest(response=response):
                self.stub.log.unlink(missing_ok=True)
                self.stub.booted_log.unlink(missing_ok=True)
                self.stub.env["STUB_PRODUCT_AFTER_REBOOT"] = response
                result = self.run_checked()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("product", result.stderr)
                self.assertIn("after the reboot", result.stderr)
                self.assertEqual(self.stub.commands()[-1], ["fastboot", "getvar", "product"])
                # TWRP was booted earlier; the kernel is never RAM-booted.
                self.assertEqual([entry["sha256"] for entry in self.stub.booted()], [self.hashes[0]])
                self.assert_no_private_copy_left()

    def test_same_product_after_the_reboot_boots_the_kernel(self):
        self.stub.env["STUB_PRODUCT_AFTER_REBOOT"] = "(bootloader) product: surfaceduo\r\n"
        result = self.run_checked()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_booted_checked_copies(self.hashes[0], self.hashes[1])

    def test_bootloader_must_report_unlocked_before_the_erase(self):
        for response in ("unlocked: no\n", "", "unlocked:\n", "unlocked: yes please\n",
                         "unlocked: YES\n", "locked: yes\n", "not unlocked: yes\n",
                         "unlocked: yes\nunlocked: no\n", "unlocked: yes\nunlocked: yes\n"):
            with self.subTest(response=response):
                self.stub.log.unlink(missing_ok=True)
                self.stub.env["STUB_UNLOCKED"] = response
                self.assert_refused_before_erase(self.run_checked(), "unlocked")

    def test_failed_unlocked_query_refuses_before_the_erase(self):
        self.stub.env["STUB_FAIL"] = "getvar unlocked"
        self.assert_refused_before_erase(self.run_checked(), "unlocked")

    def test_unlocked_answer_in_bootloader_form_is_accepted(self):
        self.stub.env["STUB_UNLOCKED"] = "(bootloader) unlocked: yes\r\nFinished. Total time: 0.1s\n"
        result = self.run_checked()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_folder_failure_refuses_before_device_access(self):
        self.stub.write_tool("mktemp", "#!/bin/sh\nexit 1\n")
        self.assert_refused(self.run_checked(), "private folder")
        self.assertEqual(self.stub.calls(), [])

    def test_failed_private_copy_refuses_before_device_access(self):
        self.stub.write_tool("cp", "#!/bin/sh\nexit 1\n")
        self.assert_refused(self.run_checked(), "private copy")
        self.assertEqual(self.stub.calls(), [])

    def test_short_private_copy_refuses_before_device_access(self):
        # A copy that is not the named file must not pass on the copy's own hash.
        self.stub.write_tool("cp", "#!/bin/sh\nprintf 'other bytes\\n' > \"$2\"\n")
        result = self.run_install("--yes", "--erase-userdata")
        self.assert_refused(result, "private copy")
        self.assertEqual(self.stub.calls(), [])

    def test_copy_with_matching_hash_but_wrong_size_never_boots_final_image(self):
        self.stub.env["ROOTFS_SIZE"] = str(int(self.stub.env["ROOTFS_SIZE"]) + 1)
        result = self.run_install("--yes", "--erase-userdata")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verify failed", result.stderr)
        # Only TWRP was RAM-booted; the kernel never is.
        self.assertEqual([entry["sha256"] for entry in self.stub.booted()], [self.hashes[0]])
        self.assertNotIn(["adb", "reboot", "bootloader"], self.stub.commands())

    def test_yes_stops_at_first_write_failure(self):
        self.stub.env["STUB_FAIL"] = "format:ext4 userdata"
        result = self.run_install("--yes", "--erase-userdata")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.stub.commands()[-1], ["fastboot", "format:ext4", "userdata"])
        self.assertEqual(len(self.stub.calls()), 4)
        self.assertEqual(self.stub.booted(), [])
        self.assert_no_private_copy_left()

    def test_failed_copy_verification_never_boots_final_image(self):
        self.stub.env["STUB_HASH"] = "0" * 64
        result = self.run_install("--yes", "--erase-userdata")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verify failed", result.stderr)
        # Only TWRP was RAM-booted; the kernel never is.
        self.assertEqual([entry["sha256"] for entry in self.stub.booted()], [self.hashes[0]])
        self.assertNotIn(["adb", "reboot", "bootloader"], self.stub.commands())


if __name__ == "__main__":
    unittest.main()
