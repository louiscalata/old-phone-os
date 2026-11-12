# SPDX-License-Identifier: GPL-3.0-or-later
"""Clock verification keeps recovery keys separate from developer-only state."""
import unittest

from tests import test_duo_os_clock as clock


class ClockKeyRoles(unittest.TestCase):
    def setUp(self):
        self.fixture = clock.ImageBlocksTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_an_owner_key_does_not_enable_the_clock_drop_in_a_marked_image(self):
        f = self.fixture
        for mode in ("release", "dev"):
            with self.subTest(mode=mode):
                r = f.rootfs()
                f.build(r, f.repo(f.now_minus(60)))
                (r / "etc/opos").mkdir()
                (r / "etc/opos/build-mode").write_text(mode + "\n")
                (r / "srv/droidian/.ssh").mkdir(parents=True)
                (r / "srv/droidian/.ssh/authorized_keys").write_text("owner key fixture\n")
                self.assertIn("PASS no developer key: the time-file drop is not enabled", f.verify(r))
                (r / "etc/systemd/system/multi-user.target.wants/opos-clock-set.path").symlink_to(
                    "/etc/systemd/system/opos-clock-set.path")
                self.assertIn("FAIL no developer key: the time-file drop is not enabled", f.verify_fail(r))

    def test_a_marked_dev_image_still_requires_the_clock_drop(self):
        f = self.fixture
        r = f.rootfs()
        f.build(r, f.repo(f.now_minus(60)), dev_key="/dev/null")
        (r / "etc/opos").mkdir()
        (r / "etc/opos/build-mode").write_text("dev\n")
        (r / "etc/default").mkdir()
        (r / "etc/default/opos-kiosk").write_text("OPOS_KIOSK_DEBUG_PORT=9222\n")
        (r / "srv/droidian/.ssh").mkdir(parents=True)
        (r / "srv/droidian/.ssh/authorized_keys").write_text("developer key fixture\n")
        self.assertIn("PASS DEV image: the time-file drop is enabled", f.verify(r))
        (r / "etc/systemd/system/multi-user.target.wants/opos-clock-set.path").unlink()
        self.assertIn("FAIL DEV image: the time-file drop is enabled", f.verify_fail(r))
