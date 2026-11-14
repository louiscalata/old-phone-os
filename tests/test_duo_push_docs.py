# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import re
import unittest

_DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "duo-usb-install.md"


def _section():
    text = _DOC.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line == "## Push a build with push-to-duo.sh":
            return "\n".join(lines[i:])
    return ""


class DuoPushDocsTest(unittest.TestCase):
    def test_section_content(self):
        section = _section()
        self.assertTrue(section)
        self.assertIn("deploy/push-to-duo.sh", section)
        self.assertIn("--dry-run", section)
        self.assertIn("<phone-usb-address>", section)
        self.assertIn("opos.bak-", section)
        self.assertIn("install.sh --no-launch --yes", section)
        self.assertIn("opos status", section)
        self.assertIn("running: no", section)
        self.assertIn("opos run --defaults", section)
        self.assertIn("~" + "/.ssh/config", section)

    def test_section_has_no_ipv4(self):
        section = _section()
        self.assertTrue(section)
        pat = "[0-9]{1,3}" + r"(\.[0-9]{1,3}){3}"
        self.assertIsNone(re.search(pat, section))


if __name__ == "__main__":
    unittest.main()
