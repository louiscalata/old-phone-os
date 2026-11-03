# SPDX-License-Identifier: GPL-3.0-or-later;
from pathlib import Path
import re
import unittest

class DuoRecoveryDocsTest(unittest.TestCase):
    def _recovery_bodies(self):
        root = Path(__file__).resolve().parent.parent
        guide = root / "docs" / "duo-usb-install.md"
        text = guide.read_text(encoding="utf-8")
        lines = text.splitlines()
        headings = ["## Rebooting safely / recovering", "## Button steps"]
        bodies = []
        for h in headings:
            count = sum(1 for line in lines if line == h)
            self.assertEqual(count, 1, f"heading {h!r} must occur exactly once")
            idx = next(i for i, line in enumerate(lines) if line == h)
            body_lines = []
            for line in lines[idx + 1:]:
                if line.startswith("## "):
                    break
                body_lines.append(line)
            body = "\n".join(body_lines)
            self.assertTrue(body.strip(), f"body for {h!r} must be non-empty")
            bodies.append(body)
        return "\n".join(bodies)

    def test_section_content(self):
        combined = self._recovery_bodies()
        literals = [
            "reboot bootloader",
            "misc brake",
            "fastboot boot",
            "systemctl reboot",
            "Volume-down",
            "20 percent",
            "Boot Select IS the fastboot screen",
            "Allow one RAM boot per confirmed attempt after completing D019 confirmation checks.",
            "If it crashes, stop and do not retry.",
            "Duo device work stays paused.",
        ]
        for lit in literals:
            with self.subTest(literal=lit):
                self.assertIn(lit, combined)

    def test_section_has_no_secrets(self):
        combined = self._recovery_bodies()
        ip_pat = r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}"
        hex_pat = r"\b[0-9A-Fa-f]{32,}\b"
        self.assertIsNone(re.search(ip_pat, combined))
        self.assertIsNone(re.search(hex_pat, combined))
