# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path

from tests.pmos_lint import apkbuild_vars, function_body

ROOT = Path(__file__).resolve().parent.parent
APKBUILD = ROOT / "packaging/pmos/opos/APKBUILD"
ARCHIVE = "opos-runtime-source-20261102.tar.gz"
SOURCE_ROOT = "old-phone-os-source"
RELEASE_URL = (
    "https://github.com/louiscalata/old-phone-os/releases/download/"
    "v0.1.0-source-20261102/$_source_archive"
)



class TestOposApkbuild(unittest.TestCase):
    def setUp(self):
        self.text = APKBUILD.read_text(encoding="utf-8")
        self.vars = apkbuild_vars(APKBUILD)

    def test_metadata_contract(self):
        expected = {
            "pkgname": "opos",
            "pkgver": "0.1.0",
            "pkgrel": "3",
            "pkgdesc": "Old Phone OS policy and optional helper",
            "url": "https://github.com/louiscalata/old-phone-os",
            "arch": "noarch",
            "license": "GPL-3.0-or-later",
            "depends": "python3 modemmanager polkit dbus",
            "makedepends": "python3",
            "_source_archive": ARCHIVE,
            "_source_root": SOURCE_ROOT,
            "builddir": "$srcdir/$_source_root",
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(self.vars[key], value)
        self.assertNotIn("sudo", self.text)

    def test_release_asset_is_separate_from_private_git_commit(self):
        sources = self.vars["source"].split()
        self.assertEqual(sources[0], "$_source_archive::" + RELEASE_URL)
        self.assertNotIn("/archive/", sources[0])
        self.assertNotIn("example.invalid", sources[0])
        self.assertEqual(len(sources), 10)
        self.assertEqual(
            set(sources[1:]),
            {
                "opos-sysusers.conf",
                "opos-kiosk",
                "opos-server.initd",
                "opos-firstboot.sh",
                "opos-kiosk-off.sh",
                "opos-firstboot.initd",
                "opos-server.service",
                "opos-firstboot.service",
                "opos-systemd-state.py",
            },
        )
        tokens = self.vars["sha512sums"].split()
        self.assertRegex(tokens[0], r"^[0-9a-f]{128}$")
        self.assertEqual(tokens[1], "$_source_archive")
        self.assertNotIn("@SHA512@", self.vars["sha512sums"])
        self.assertNotIn("SKIP", self.vars["sha512sums"])

    def test_package_contract(self):
        body = function_body(APKBUILD, "package")
        for part in ("purelib", "src/opos", "$pkgdir$_sp", "__pycache__"):
            with self.subTest(part=part):
                self.assertIn(part, body)
        self.assertRegex(
            body,
            r'install -Dm755 "\$(?:builddir|srcdir)/opos-kiosk" '
            r'"\$pkgdir/usr/bin/opos-kiosk"',
        )

    def test_package_installs_root_license_and_third_party_notice(self):
        body = function_body(APKBUILD, "package")
        self.assertIn(
            'install -Dm644 "$builddir/LICENSE" '
            '"$pkgdir/usr/share/licenses/$pkgname/LICENSE"',
            body,
        )
        self.assertIn(
            'install -Dm644 "$builddir/THIRD_PARTY.md" '
            '"$pkgdir/usr/share/doc/$pkgname/THIRD_PARTY.md"',
            body,
        )
        for name in ("LICENSE", "THIRD_PARTY.md"):
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file())
                self.assertTrue((ROOT / name).read_bytes())

    def test_maintainer_uses_project_credit_without_a_fake_contact_address(self):
        for path in (
            "packaging/pmos/opos/APKBUILD",
            "packaging/pmos/postmarketos-ui-opos/APKBUILD",
        ):
            with self.subTest(path=path):
                values = apkbuild_vars(ROOT / path)
                self.assertEqual(values["maintainer"], "Old Phone OS <173626247+louiscalata@users.noreply.github.com>")
                self.assertNotIn("example.invalid", values["maintainer"])
