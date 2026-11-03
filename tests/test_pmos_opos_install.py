# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path
from tests.pmos_lint import function_body

APKBUILD = Path(__file__).resolve().parent.parent / "packaging/pmos/opos/APKBUILD"
BODY = function_body(APKBUILD, "package")

class TestOposInstall(unittest.TestCase):
    def test_launcher(self):
        self.assertTrue(all(s in BODY for s in (
            "/usr/bin/opos",
            "#!/bin/sh",
            "opos.setup",
            '"$@"',
            "chmod 755",
        )))

    def test_simulated_web_service_package_does_not_grant_modem_access(self):
        self.assertNotIn("polkit-1/rules.d", BODY)
        self.assertNotIn("50-opos-modemmanager.rules", APKBUILD.read_text())

    def test_sysusers_destination(self):
        self.assertIn("/usr/lib/sysusers.d/opos.conf", BODY)
