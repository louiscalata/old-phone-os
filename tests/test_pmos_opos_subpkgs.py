# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import re
import unittest


class TestOposSubpkgs(unittest.TestCase):
    def test_package_installs_kiosk(self):
        repo = pathlib.Path(__file__).resolve().parent.parent
        text = (repo / "packaging/pmos/opos/APKBUILD").read_text(encoding="utf-8")
        body = re.search(r"(?ms)^package\(\)[ \t]*\{[ \t]*\n(.*?)^\}", text).group(1)
        self.assertRegex(body, r"(?m)^[ \t]*install -Dm755 \"\$srcdir/opos-kiosk\" \"\$pkgdir/usr/bin/opos-kiosk\"[ \t]*$")


if __name__ == "__main__":
    unittest.main()
