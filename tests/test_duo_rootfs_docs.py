# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import re
import unittest

_DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "duo-usb-install.md"


def _section():
    text = _DOC.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = -1
    for i, line in enumerate(lines):
        if line == "## Rootfs install through TWRP":
            start = i
            break
    if start < 0:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


class DuoRootfsDocsTest(unittest.TestCase):
    def test_section_content(self):
        section = _section().lower()
        self.assertTrue(section)
        self.assertIn("rootfs.img", section)
        self.assertIn("inside", section)
        self.assertIn("ext4", section)
        self.assertIn("userdata", section)
        self.assertIn("halium rootfs is empty", section)
        self.assertIn("sparse", section)
        self.assertIn("raw", section)
        self.assertIn("deploy/duo-rootfs-install.sh", section)
        self.assertIn("only prints", section)
        self.assertIn("verify failed", section)
        self.assertIn("--yes", section)
        self.assertNotIn("planned", section)

    def test_refusals_and_remaining_limits_are_documented(self):
        section = _section()
        for text in ('--yes --erase-userdata', '--twrp-sha256', '--boot-sha256',
                     '--rootfs-sha256', 'exactly one fastboot device',
                     'product: surfaceduo', 'less guarded route', 'NOT_RUN'):
            self.assertIn(text, section)

    def test_section_has_no_ipv4(self):
        section = _section().lower()
        self.assertTrue(section)
        pat = "[0-9]{1,3}" + r"(\.[0-9]{1,3}){3}"
        self.assertIsNone(re.search(pat, section))

    def test_steps_in_order(self):
        section = _section().lower()
        self.assertTrue(section, "rootfs section not found")
        steps = [line for line in section.splitlines() if re.match(r"\d\. ", line)]
        self.assertEqual(len(steps), 5)
        for i in range(5):
            self.assertTrue(steps[i].startswith(f"{i + 1}. "))
        self.assertIn("fastboot format:ext4 userdata", steps[0])
        self.assertIn("twrp", steps[1])
        self.assertIn("fastboot boot", steps[1])
        self.assertIn("/data", steps[1])
        self.assertIn("adb push", steps[2])
        self.assertIn("/data/rootfs.img", steps[2])
        self.assertIn("sha-256", steps[3])
        self.assertIn("stop on mismatch", steps[3])
        self.assertIn("bootloader", steps[4])
        self.assertIn("single fastboot boot", steps[4])
        self.assertIn("stop on crash", steps[4])
        warn = section.find("step 1 erases everything on userdata.")
        self.assertNotEqual(warn, -1)
        self.assertLess(warn, section.find(steps[0]))


if __name__ == "__main__":
    unittest.main()
