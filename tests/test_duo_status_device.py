# SPDX-License-Identifier: GPL-3.0-or-later
"""Status prefers detected device over saved stub."""
from __future__ import annotations
import io
import unittest
from pathlib import Path
from unittest.mock import patch
from opos.setup.cli import main as cli_main


def _cfg(device):
    return {"applied": True, "device": device}


class StatusDeviceTest(unittest.TestCase):
    def _run_status(self, device, detected=None, side_effect=None):
        out = io.StringIO()
        with patch("opos.setup.config.load", return_value=_cfg(device)):
            with patch("opos.setup.config.config_path", return_value=Path("/tmp/opos-missing-config.json")):
                with patch("opos.setup.apply.http_health", return_value=(False, "down")):
                    with patch("opos.setup.device.detect_device", return_value=detected, side_effect=side_effect) as det:
                        rc = cli_main(["status"], env={}, platform="linux", out=out)
        return rc, out.getvalue(), det

    def test_status_prefers_detected_device_over_stub(self):
        stub_name = "Generic phone " + "(stub)"
        found_name = "Microsoft Surface " + "Duo"
        devices = [
            {"name": stub_name, "source": "stub", "id": "kept-id"},
            {"name": stub_name, "source": "profile", "id": "generic-phone"},
            {"name": stub_name, "source": "profile", "id": "unknown"},
        ]
        for device in devices:
            with self.subTest(device=device):
                found = {"name": found_name, "source": "profile"}
                rc, text, det = self._run_status(device, detected=found)
                self.assertEqual(rc, 0)
                det.assert_called_once_with("/")
                self.assertIn("device_name: " + found_name + " (detected)", text)
                self.assertNotIn(stub_name, text)

    def test_status_keeps_stub_when_detection_is_stub(self):
        stub_name = "Generic phone " + "(stub)"
        device = {"name": stub_name, "source": "stub", "id": "generic-phone"}
        cases = [
            {"name": "Other " + "Phone", "source": "stub"},
            None,
        ]
        for detected in cases:
            with self.subTest(detected=detected):
                rc, text, _ = self._run_status(device, detected=detected)
                self.assertEqual(rc, 0)
                self.assertIn("device_name: " + stub_name, text)
                self.assertNotIn(" (detected)", text)
        with self.subTest(detected="error"):
            rc, text, _ = self._run_status(device, side_effect=RuntimeError("no device"))
            self.assertEqual(rc, 0)
            self.assertIn("device_name: " + stub_name, text)
            self.assertNotIn(" (detected)", text)


if __name__ == "__main__":
    unittest.main()
