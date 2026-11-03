# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class DuoButtonDocsTest(unittest.TestCase):
    def test_button_steps_required_guidance(self):
        doc = pathlib.Path(__file__).resolve().parent.parent / "docs" / "duo-usb-install.md"
        text = doc.read_text(encoding="utf-8")
        lines = text.splitlines()
        heading = "## Button steps"
        matches = [i for i, line in enumerate(lines) if line == heading]
        self.assertEqual(len(matches), 1, "expected exactly one Button steps heading")
        start = matches[0] + 1
        end = len(lines)
        for i in range(start, len(lines)):
            if lines[i].startswith("## "):
                end = i
                break
        body_lines = lines[start:end]
        body = "\n".join(body_lines)
        nonblank = [line for line in body_lines if line.strip()]
        self.assertGreater(len(nonblank), 0)
        self.assertEqual(nonblank[0], "The battery must be at 20 percent or more before any boot attempt.")
        steps = [
            "1. Hold Power for about 20 seconds until the screen stays dark; keep holding through any flash.",
            "2. With the phone off and connected by USB, hold Volume-down first, then add Power.",
            "3. Keep both held through the Surface logo and the orange 'unlocked' warning.",
            "4. Release Power when the screen changes and keep Volume-down held until Boot Select appears.",
        ]
        positions = []
        for step in steps:
            self.assertIn(step, body_lines)
            positions.append(body_lines.index(step))
        self.assertEqual(positions, sorted(positions))
        self.assertLess(body_lines.index(nonblank[0]), positions[0])
        self.assertIn("Boot Select IS the fastboot screen and has no fastboot label.", body)
        self.assertIn("Never press Power alone at the orange warning.", body)
        self.assertIn("Never pick Start, Recovery Mode or Factory data reset.", body)
        self.assertIn("If stock recovery displays Cannot load Android system, power off and repeat the sequence.", body)
        self.assertIn("`systemctl reboot`", body)
        self.assertIn("`reboot`", body)
        self.assertIn("`reboot bootloader`", body)
        self.assertIn("Allow one RAM boot per confirmed attempt after completing D019 confirmation checks.", body)
        self.assertIn("If it crashes, stop and do not retry.", body)
        self.assertIn("A factory reset restores stock encrypted Android userdata.", body)
        self.assertIn("`fastboot format:ext4 userdata`", body)
        self.assertIn("following D019 device-selection precautions", body)
        self.assertIn("This erases userdata.", body)
        self.assertIn("Duo device work stays paused.", body)


if __name__ == "__main__":
    unittest.main()
