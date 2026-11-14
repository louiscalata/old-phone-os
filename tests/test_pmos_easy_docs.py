# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path
class TestEasyDocs(unittest.TestCase):
    def setUp(self):
        p = Path(__file__).resolve().parents[1] / "docs/pmos-easy-setup.md"
        self.text = p.read_text(encoding="utf-8")
    def test_header(self):
        n = [x for x in self.text.splitlines() if x.strip()]
        self.assertEqual(n[:2], ["<!-- SPDX-License-Identifier: GPL-3.0-or-later -->", "# Easy setup on postmarketOS"])
    def test_reboot_warning(self):
        script = (Path(__file__).resolve().parents[1] / "packaging/pmos/easy/confirm-reboot.sh").read_text()
        warning = "WARNING: the phone will reboot several times during setup. Do not unplug it or press buttons until it says Done."
        self.assertIn(warning, script)  # the doc quotes what the script prints
        self.assertIn("## Reboot warning\n\n> " + warning + "\n\n", self.text)
        self.assertIn("`confirm-reboot.sh` prints the warning above.", self.text)
        self.assertIn("not implement or verify a multi-reboot setup sequence", self.text)
        self.assertIn("Do not unplug the phone\nduring setup.", self.text)
    def test_checks_and_flash_refusal_are_documented(self):
        script = (Path(__file__).resolve().parents[1] / "packaging/pmos/easy/easy-install.sh").read_text()
        self.assertNotIn("confirm-reboot", script)  # the doc says no typed confirmation is asked
        for text in ('refuses with\n`refusing to flash` and exit status 1', 'asks for no typed confirmation',
                     '`--yes` is no longer accepted', 'readable, non-empty file',
                     'issues no erase or flash command', 'exit status 2', 'NOT_RUN'):
            self.assertIn(text, self.text)
        self.assertNotIn('fastboot flash', self.text)
        self.assertNotIn('system partition', self.text)
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
