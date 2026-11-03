# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class DuoKioskDocsTest(unittest.TestCase):
    def test_section_content(self):
        p = pathlib.Path(__file__).resolve().parent.parent / "docs" / "duo-usb-install.md"
        t = p.read_text(encoding="utf-8")
        self.assertIn("## Open OPOS full-screen at login", t)
        a = t.split("## Open OPOS full-screen at login", 1)[1]
        for s in ("install.sh --kiosk", "OPOS_KIOSK_BROWSER", "opos-kiosk.desktop", "--uninstall", "opos run --defaults", "no root"):
            self.assertIn(s, a)


if __name__ == "__main__":
    unittest.main()
