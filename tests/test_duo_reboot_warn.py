# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Surface Duo RAM-boot reboot warning."""
import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import opos.setup.cli


EXPECTED = (
    "warning: Surface Duo detected. If this kernel was RAM-booted from a PC with fastboot boot, do not run reboot or systemctl reboot: the phone would start the stock kernel and be stranded.\n"
    "To restart safely run reboot bootloader, then re-arm the misc brake and RAM-boot once from the PC. See docs/duo-usb-install.md, section \"Rebooting safely / recovering\"."
)


class DuoRebootWarnTest(unittest.TestCase):
    def test_duo_root_gets_warning(self):
        with tempfile.TemporaryDirectory() as root:
            model = Path(root) / "proc" / "device-tree" / "model"
            model.parent.mkdir(parents=True)
            model.write_text("Microsoft Surface Duo", encoding="utf-8")
            self.assertEqual(opos.setup.cli.reboot_warning(root), EXPECTED)

    def test_other_and_empty_roots_get_none(self):
        with tempfile.TemporaryDirectory() as root:
            model = Path(root) / "proc" / "device-tree" / "model"
            model.parent.mkdir(parents=True)
            model.write_text("Generic Board", encoding="utf-8")
            self.assertIsNone(opos.setup.cli.reboot_warning(root))
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(opos.setup.cli.reboot_warning(root))

    def test_detection_exception_gets_none(self):
        with patch("opos.hw.detect.detect", side_effect=RuntimeError("x")):
            self.assertIsNone(opos.setup.cli.reboot_warning("/"))

    def test_run_prints_warning_first(self):
        with tempfile.TemporaryDirectory() as cfg_dir, tempfile.TemporaryDirectory() as home_dir:
            args = argparse.Namespace(config_dir=cfg_dir, defaults=True, dry_run=True, reconfigure=False, no_browser=True, port=None)
            out = io.StringIO()
            with patch("opos.setup.cli.reboot_warning", return_value="WARN-X") as mock_warn:
                with patch("opos.setup.device.detect_device", side_effect=RuntimeError("x")):
                    rc = opos.setup.cli._handle_run(args, {}, "linux", Path(home_dir), out)
            self.assertEqual(rc, 1)
            self.assertEqual(out.getvalue(), "WARN-X\ndevice detection failed: x\n")
            mock_warn.assert_called_once_with("/")

    def test_run_without_warning_preserves_error_output(self):
        with tempfile.TemporaryDirectory() as cfg_dir, tempfile.TemporaryDirectory() as home_dir:
            args = argparse.Namespace(config_dir=cfg_dir, defaults=True, dry_run=True, reconfigure=False, no_browser=True, port=None)
            out = io.StringIO()
            with patch("opos.setup.cli.reboot_warning", return_value=None):
                with patch("opos.setup.device.detect_device", side_effect=RuntimeError("x")):
                    rc = opos.setup.cli._handle_run(args, {}, "linux", Path(home_dir), out)
            self.assertEqual(rc, 1)
            self.assertEqual(out.getvalue(), "device detection failed: x\n")
