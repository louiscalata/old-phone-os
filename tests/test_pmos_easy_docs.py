# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestEasyDocs(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "docs/pmos-easy-setup.md").read_text(encoding="utf-8")
        self.confirm = (ROOT / "packaging/pmos/easy/confirm-reboot.sh").read_text(encoding="utf-8")
        self.install = (ROOT / "packaging/pmos/easy/easy-install.sh").read_text(encoding="utf-8")

    def test_header(self):
        nonempty = [line for line in self.text.splitlines() if line.strip()]
        self.assertEqual(nonempty[:2], ["<!-- SPDX-License-Identifier: GPL-3.0-or-later -->", "# Easy setup on postmarketOS"])

    def test_reboot_warning_quotes_the_script(self):
        warning = re.search(r"printf '%s\\n' '([^']+)'", self.confirm).group(1)
        self.assertIn("Physical-device flashing is untested.", warning)
        self.assertNotIn("several times", warning)
        self.assertIn("## Reboot warning\n\n`packaging/pmos/easy/confirm-reboot.sh` prints this line and nothing else until you answer:\n\n> " + warning + "\n\n", self.text)
        self.assertIn("does not reboot the computer or a phone", self.text)
        self.assertIn("do not implement or verify a multi-reboot setup sequence", self.text)
        self.assertIn("Do not unplug a device you choose to flash.", self.text)

    def test_plan_and_flash_match_the_script(self):
        self.assertIn('echo "step 3: skipped (no --flash)"', self.install)
        self.assertIn("skipped (no --flash)", self.text)
        self.assertIn("does not run `image-prep.sh`, `confirm-reboot.sh`, or `pmbootstrap`", self.text)
        self.assertIn("does not read or write `IMAGE`", self.text)
        self.assertIn("pmbootstrap flasher flash_rootfs", self.text)
        self.assertIn("not passed to `pmbootstrap`", self.text)
        self.assertIn("Physical-device flashing is untested.", self.text)
        self.assertGreaterEqual(self.text.count("Physical-device flashing is untested."), 2)

    def test_image_prep_and_firstboot_limits(self):
        self.assertIn("format: sparse", self.text)
        self.assertIn("3a ff 26 ed", self.text)
        self.assertIn("does not convert the image", self.text)
        self.assertIn("first boot: already done", self.text)
        self.assertIn("does not record a previous session", self.text)

    def test_undo_kiosk(self):
        self.assertIn("## Undo the kiosk\n\nRun `opos-kiosk-off` as root.", self.text)
        self.assertIn("If a previous session was recorded, it restores that session after a reboot.", self.text)
        self.assertIn("If no previous session was recorded, it disables tinydm for the next boot", self.text)
        self.assertIn("`rc-update del tinydm default` on OpenRC", self.text)
        self.assertIn("`systemctl disable tinydm.service` on systemd", self.text)
        self.assertIn("It does not stop the current kiosk session; reboot to apply the change.", self.text)

    def test_undo_failure_and_dry_run_are_documented(self):
        self.assertIn("returns a failure if restoring the session or disabling tinydm fails", self.text)
        self.assertIn("if no supported service manager is available", self.text)
        self.assertIn("`opos-kiosk-off --dry-run`", self.text)
        self.assertIn("without changing the session or services", self.text)


if __name__ == "__main__":
    unittest.main()
