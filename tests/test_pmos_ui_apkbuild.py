# SPDX-License-Identifier: GPL-3.0-or-later
"""OPOS UI package identity and source integrity."""
import hashlib
import shlex
import unittest
from pathlib import Path

from tests.pmos_lint import apkbuild_vars, function_body

ROOT = Path(__file__).resolve().parent.parent
APKBUILD = ROOT / "packaging/pmos/postmarketos-ui-opos/APKBUILD"


class TestUiApkbuild(unittest.TestCase):
    def setUp(self):
        self.vars = apkbuild_vars(APKBUILD)

    def test_identity(self):
        self.assertEqual(self.vars["pkgname"], "postmarketos-ui-opos")
        self.assertEqual(self.vars["pkgver"], "1")
        self.assertEqual(self.vars["pkgrel"], "2")
        self.assertEqual(self.vars["_pmb_groups"], "seat")
        self.assertEqual(
            self.vars["url"], "https://github.com/louiscalata/old-phone-os",
        )
        self.assertEqual(self.vars["license"], "GPL-3.0-or-later")

    def test_cage_source_checksum_matches_actual_bytes(self):
        self.assertEqual(self.vars["source"].split(), ["opos-cage.sh", "opos-tinydm-env.sh"])
        tokens = self.vars["sha512sums"].split()
        self.assertEqual(tokens[1::2], ["opos-cage.sh", "opos-tinydm-env.sh"])
        for digest, name in zip(tokens[0::2], tokens[1::2]):
            with self.subTest(name=name):
                self.assertRegex(digest, r"^[0-9a-f]{128}$")
                self.assertEqual(
                    digest,
                    hashlib.sha512((APKBUILD.parent / name).read_bytes()).hexdigest(),
                )

    def test_dependencies_and_options(self):
        self.assertEqual(
            self.vars["depends"].split() + self.vars["options"].split(),
            [
                "postmarketos-base-ui-cage", "opos", "chromium", "psmisc",
                "!check", "pmb:drm", "pmb:default-openrc",
                "pmb:support-openrc", "pmb:support-systemd",
            ],
        )

    def test_package_install(self):
        lines = function_body(APKBUILD, "package").splitlines()
        self.assertEqual(lines[0], "package() {")
        self.assertEqual(lines[-1], "}")
        installs = [shlex.split(line) for line in lines[1:-1] if line.strip().startswith("install")]
        self.assertEqual(len(installs), 2)
        for tokens in installs:
            self.assertTrue(any("D" in token and "755" in token for token in tokens))
        self.assertEqual(installs[0][-2:], ["$srcdir/opos-cage.sh", "$pkgdir/etc/cage-ui/opos.sh"])
        # tinydm sources env-wayland.d before it starts cage, so the renderer default reaches the compositor.
        self.assertEqual(installs[1][-2:], ["$srcdir/opos-tinydm-env.sh", "$pkgdir/usr/share/tinydm/env-wayland.d/50-opos-renderer.sh"])
