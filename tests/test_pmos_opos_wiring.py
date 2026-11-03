# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import unittest
from pathlib import Path

from tests.pmos_lint import apkbuild_vars, function_body

ROOT = Path(__file__).resolve().parent.parent
APKBUILD = ROOT / "packaging/pmos/opos/APKBUILD"
AUXILIARY_SOURCES = {
    "opos-sysusers.conf": "deploy/opos-sysusers.conf",
    "opos-kiosk": "packaging/pmos/opos/opos-kiosk",
    "opos-server.initd": "packaging/pmos/opos/opos-server.initd",
    "opos-firstboot.sh": "packaging/pmos/easy/opos-firstboot.sh",
    "opos-kiosk-off.sh": "packaging/pmos/easy/opos-kiosk-off.sh",
    "opos-firstboot.initd": "packaging/pmos/opos/opos-firstboot.initd",
    "opos-server.service": "packaging/pmos/opos/opos-server.service",
    "opos-firstboot.service": "packaging/pmos/opos/opos-firstboot.service",
    "opos-systemd-state.py": "packaging/pmos/opos/opos-systemd-state.py",
}


class TestOposWiring(unittest.TestCase):
    def setUp(self):
        self.vars = apkbuild_vars(APKBUILD)

    def _checksum_pairs(self):
        tokens = self.vars["sha512sums"].split()
        self.assertEqual(len(tokens) % 2, 0)
        pairs = list(zip(tokens[::2], tokens[1::2]))
        names = [name for _, name in pairs]
        self.assertEqual(len(names), len(set(names)), "duplicate checksum name")
        for digest, name in pairs:
            with self.subTest(source=name):
                self.assertRegex(digest, r"^[0-9a-f]{128}$")
        return pairs

    def test_sources_have_exactly_one_real_checksum_each(self):
        source_names = [
            token.split("::", 1)[0] for token in self.vars["source"].split()
        ]
        self.assertEqual(len(source_names), len(set(source_names)))
        pairs = self._checksum_pairs()
        self.assertCountEqual([name for _, name in pairs], source_names)
        self.assertEqual(len(pairs), 10)
        self.assertEqual(set(source_names[1:]), set(AUXILIARY_SOURCES))
        self.assertEqual(source_names[0], "$_source_archive")

    def test_auxiliary_checksums_match_actual_source_bytes(self):
        checksums = {name: digest for digest, name in self._checksum_pairs()}
        for name, relative in AUXILIARY_SOURCES.items():
            with self.subTest(source=name):
                actual = hashlib.sha512((ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(checksums[name], actual)

    def test_pre_install(self):
        self.assertEqual(self.vars["install"], "$pkgname.pre-install")
        self.assertTrue(
            (ROOT / "packaging/pmos/opos/opos.pre-install").is_file()
        )

    def test_systemd_subpackage(self):
        self.assertIn("$pkgname-systemd:_systemd", self.vars["subpackages"].split())
        self.assertIn(
            'install="$subpkgname.post-install"',
            function_body(APKBUILD, "_systemd"),
        )
        self.assertTrue(
            (ROOT / "packaging/pmos/opos/opos-systemd.post-install").is_file()
        )
        self.assertIn(
            'depends="systemd $pkgname=$pkgver-r$pkgrel"',
            function_body(APKBUILD, "_systemd"),
        )

    def test_package_installs_initd(self):
        self.assertIn(
            'install -Dm755 "$srcdir/opos-server.initd" '
            '"$pkgdir/etc/init.d/opos-server"',
            function_body(APKBUILD, "package"),
        )

    def test_firstboot_source_and_checksums(self):
        names = {name for _, name in self._checksum_pairs()}
        for name in (
            "opos-firstboot.sh", "opos-kiosk-off.sh", "opos-firstboot.initd",
        ):
            with self.subTest(source=name):
                self.assertIn(name, self.vars["source"].split())
                self.assertIn(name, names)

    def test_package_installs_firstboot(self):
        body = function_body(APKBUILD, "package")
        for source, destination in (
            ("opos-firstboot.sh", "/usr/bin/opos-firstboot"),
            ("opos-kiosk-off.sh", "/usr/bin/opos-kiosk-off"),
            ("opos-firstboot.initd", "/etc/init.d/opos-firstboot"),
        ):
            with self.subTest(source=source):
                self.assertIn(
                    f'install -Dm755 "$srcdir/{source}" "$pkgdir{destination}"',
                    body,
                )

    def test_openrc_subpackage(self):
        self.assertIn("$pkgname-openrc:_openrc", self.vars["subpackages"].split())
        body = function_body(APKBUILD, "_openrc")
        self.assertIn('install="$subpkgname.post-install"', body)
        self.assertIn('depends="openrc $pkgname=$pkgver-r$pkgrel"', body)
        self.assertTrue(
            (ROOT / "packaging/pmos/opos/opos-openrc.post-install").is_file()
        )
