# SPDX-License-Identifier: GPL-3.0-or-later
"""The image build binds the container debug port to loopback and closes a passwordless USB root shell."""
import os
import stat
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging" / "duo-os"
SCRIPT = DUO / "close-exposure.sh"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class CloseExposure(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.id().replace(".", "-"))
        base = Path(os.environ.get("TMPDIR", "/tmp")) / "opos-exposure"
        base.mkdir(exist_ok=True)
        self.root = base / self.id().split(".")[-1]
        if self.root.exists():
            subprocess.run(["rm", "-rf", str(self.root)], check=True)
        self.root.mkdir()
        write(self.root / "etc/systemd/system/adbd.service",
              "[Service]\nExecStart=/usr/bin/adbd -a -P 5555\nListenStream=0.0.0.0:5555\n")
        write(self.root / "lib/systemd/system/telnet.service",
              "[Service]\nExecStart=/usr/sbin/telnetd -l /bin/sh\n")
        wants = self.root / "etc/systemd/system/multi-user.target.wants"
        wants.mkdir(parents=True)
        os.symlink("/lib/systemd/system/telnet.service", wants / "telnet.service")
        write(self.root / "etc/inetd.conf",
              "telnet stream tcp nowait root /usr/sbin/telnetd telnetd -l /bin/sh\n")
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)], check=False)

    def run_script(self, **env):
        subprocess.run(["sh", str(SCRIPT), str(self.root)], check=True, env={**os.environ, **env})

    def test_default_binds_loopback_and_closes_the_shell(self):
        self.run_script()
        adbd = (self.root / "etc/systemd/system/adbd.service").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:5555", adbd)
        self.assertNotIn("0.0.0.0", adbd)
        self.assertNotIn(" -a", adbd)
        self.assertEqual((self.root / "etc/opos/debug-port.conf").read_text(encoding="utf-8"),
                         "listen=127.0.0.1\nport=5555\n")
        nft = (self.root / "etc/opos/wlan0-inbound.nft").read_text(encoding="utf-8")
        self.assertIn('iifname "wlan0" ct state established,related accept', nft)
        self.assertIn('iifname "wlan0" drop', nft)
        self.assertEqual((self.root / "etc/opos/usb-shell.conf").read_text(encoding="utf-8").strip(), "enabled=0")
        masked = self.root / "etc/systemd/system/telnet.service"
        self.assertTrue(masked.is_symlink())
        self.assertEqual(os.readlink(masked), "/dev/null")
        self.assertFalse(os.path.lexists(self.root / "etc/systemd/system/multi-user.target.wants/telnet.service"))
        inetd = (self.root / "etc/inetd.conf").read_text(encoding="utf-8")
        self.assertIn("# opos-closed ", inetd)
        self.assertNotIn("\ntelnet stream", "\n" + inetd)
        mode = stat.S_IMODE((self.root / "usr/libexec/opos/opos-debug-port").stat().st_mode)
        self.assertEqual(mode, 0o755)
        self.assertTrue((self.root / "etc/systemd/system/multi-user.target.wants/opos-debug-port.service").is_symlink())

    def test_inherited_dev_gate_cannot_keep_the_shell(self):
        self.run_script(OPOS_DEV_USB_SHELL="1")
        self.assertEqual((self.root / "etc/opos/usb-shell.conf").read_text(encoding="utf-8"), "enabled=0\n")
        self.assertFalse(os.path.lexists(self.root / "etc/systemd/system/multi-user.target.wants/telnet.service"))
        self.assertTrue((self.root / "etc/inetd.conf").read_text(encoding="utf-8").startswith("# opos-closed "))

    def test_a_second_run_stays_on_loopback(self):
        self.run_script()
        self.run_script()
        adbd = (self.root / "etc/systemd/system/adbd.service").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:5555", adbd)
        self.assertNotIn("0.0.0.0", adbd)


class ExposureWiring(unittest.TestCase):
    def test_build_calls_the_script_and_the_kiosk_stays_on_loopback(self):
        build = (DUO / "build-image.sh").read_text(encoding="utf-8")
        self.assertIn('sh "$here/close-exposure.sh" "$r"', build)
        kiosk = (DUO / "opos-kiosk").read_text(encoding="utf-8")
        self.assertIn("--remote-debugging-address=127.0.0.1", kiosk)
        self.assertNotIn("0.0.0.0", kiosk)
        verify = (DUO / "verify-image.sh").read_text(encoding="utf-8")
        self.assertIn("listen=127.0.0.1", verify)
        self.assertIn("wlan0", verify)
        for name in ("close-exposure.sh", "build-image.sh", "verify-image.sh", "opos-kiosk"):
            proc = subprocess.run(["sh", "-n", str(DUO / name)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, name + proc.stderr)

    def test_checklist_marks_every_phone_item_not_run(self):
        text = (ROOT / "docs/duo-on-device-checklist.md").read_text(encoding="utf-8")
        self.assertIn("NOT_RUN", text)
        rows = [line for line in text.splitlines() if line.startswith("|") and "---" not in line and "Item" not in line]
        self.assertGreaterEqual(len(rows), 15)
        for row in rows:
            self.assertIn("NOT_RUN", row)
        for banned in ("IMEI", "IMSI", "ICCID", "serial", "bootloader"):
            self.assertNotIn(banned.lower(), text.lower())


if __name__ == "__main__":
    unittest.main()
