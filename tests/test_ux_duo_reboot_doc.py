# SPDX-License-Identifier: GPL-3.0-or-later;
import unittest
from pathlib import Path
import re
import opos.setup.cli

class TestDuoRebootDoc(unittest.TestCase):
    def setUp(self):
        repo_root = Path(__file__).resolve().parent.parent
        self.doc_path = repo_root / 'docs' / 'duo-usb-install.md'
        self.doc_text = self.doc_path.read_text(encoding='utf-8')
        self.lines = self.doc_text.splitlines()
        self.warning = opos.setup.cli.RAM_BOOT_WARNING

    def test_heading_matches_warning(self):
        m = re.search(r'section\s+"([^"]+)"', self.warning)
        self.assertIsNotNone(m, "RAM_BOOT_WARNING does not contain section name")
        section_name = m.group(1)
        expected_heading = f"## {section_name}"
        found = any(line == expected_heading for line in self.lines)
        self.assertTrue(found, f"Heading {expected_heading!r} not found")

    def test_section_content(self):
        m = re.search(r'section\s+"([^"]+)"', self.warning)
        self.assertIsNotNone(m)
        section_name = m.group(1)
        expected_heading = f"## {section_name}"
        try:
            idx = next(i for i, line in enumerate(self.lines) if line == expected_heading)
        except StopIteration:
            self.fail(f"Heading {expected_heading!r} not found")
        section_lines = []
        for line in self.lines[idx+1:]:
            if line.startswith('## '):
                break
            section_lines.append(line)
        section_text = '\n'.join(section_lines)
        for phrase in ('reboot bootloader', 'misc brake', 'fastboot boot', 'systemctl reboot'):
            self.assertIn(phrase, section_text)

if __name__ == '__main__':
    unittest.main()
