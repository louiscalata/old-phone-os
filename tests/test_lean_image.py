# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest
from pathlib import Path

from tests.pmos_lint import function_body


ROOT = Path(__file__).resolve().parent.parent
OPOS_DEPENDS = {"python3", "modemmanager", "polkit", "dbus"}
UI_DEPENDS = {"postmarketos-base-ui-cage", "opos", "chromium", "psmisc"}
SERVICES = {"opos-server", "opos-firstboot"}
HEADLINE = "Focused package inputs; performance gains remain unmeasured."
README_LINE = f"**{HEADLINE}** See [docs/lean.md](docs/lean.md)."
RESULTS_HEADER = (
    "| date | target (QEMU/device) | boot_to_login_s | idle_ram_mb "
    "| idle_cpu_pct | pkg_count | enabled_services |"
)
MESSAGE = (
    "adding a package or service breaks the no-bloatware headline: "
    "update docs/lean.md and this test together with the owner's approval"
)


SUBPACKAGE_DEPENDS = {
    "opos": ["openrc $pkgname=$pkgver-r$pkgrel",
             "systemd $pkgname=$pkgver-r$pkgrel"],
    "postmarketos-ui-opos": ["networkmanager-openrc openrc"],
}


class TestLeanImage(unittest.TestCase):
    def read_apkbuild(self, package):
        return (ROOT / "packaging" / "pmos" / package / "APKBUILD").read_text(
            encoding="utf-8"
        )

    def depends(self, package):
        # Top-level depends, which may span several lines.
        matches = re.findall(r'^depends="([^"]*)"', self.read_apkbuild(package), re.MULTILINE)
        self.assertEqual(len(matches), 1, MESSAGE)
        return set(matches[0].split())

    def test_opos_depends_exact(self):
        self.assertEqual(self.depends("opos"), OPOS_DEPENDS, MESSAGE)

    def test_ui_depends_exact(self):
        self.assertEqual(self.depends("postmarketos-ui-opos"), UI_DEPENDS, MESSAGE)

    def test_subpackage_depends_exact(self):
        # Indented depends="..." inside subpackage functions are pinned too.
        found = {}
        for package in ("opos", "postmarketos-ui-opos"):
            blocks = re.findall(r'^[ \t]+depends="([^"]*)"', self.read_apkbuild(package), re.MULTILINE)
            found[package] = sorted(" ".join(b.split()) for b in blocks)
        self.assertEqual(found, SUBPACKAGE_DEPENDS, MESSAGE)

    def test_optional_systemd_route_stays_conditional_and_documented(self):
        body = function_body(ROOT / "packaging/pmos/opos/APKBUILD", "_systemd")
        self.assertIn('install_if="$pkgname=$pkgver-r$pkgrel systemd"', body)
        self.assertIn('depends="systemd $pkgname=$pkgver-r$pkgrel"', body)
        for package in ("opos", "postmarketos-ui-opos"):
            self.assertNotIn("systemd", self.depends(package), MESSAGE)
        options = re.search(r'^options="([^"\n]*)"',
                            self.read_apkbuild("postmarketos-ui-opos"), re.MULTILINE)
        self.assertIsNotNone(options, MESSAGE)
        self.assertIn("pmb:default-openrc", options.group(1).split(), MESSAGE)
        self.assertNotIn("pmb:default-systemd", options.group(1).split(), MESSAGE)
        text = (ROOT / "docs/lean.md").read_text(encoding="utf-8")
        for fact in ("## Optional init integration", "opos-systemd",
                     "opos-server.service", "opos-firstboot.service",
                     "source and staging checks", "systemd startup remain unverified",
                     "state-validation helper"):
            self.assertIn(fact, text, MESSAGE)

    def test_ui_no_recommends(self):
        # Reject a later assignment overriding the empty recommendations list.
        assignments = re.findall(
            r'^[ \t]*_pmb_recommends=.*$',
            self.read_apkbuild("postmarketos-ui-opos"),
            re.MULTILINE,
        )
        self.assertEqual(
            [a.strip() for a in assignments],
            ['_pmb_recommends=""'],
            MESSAGE,
        )

    def test_opos_init_scripts_exact(self):
        scripts = set(re.findall(
            r'"?\$(?:pkgdir|\{pkgdir\})"?/etc/init\.d/([A-Za-z0-9_-]+)', self.read_apkbuild("opos")
        ))
        self.assertEqual(scripts, SERVICES, MESSAGE)

    def test_lean_doc_lists_essentials(self):
        doc = ROOT / "docs" / "lean.md"
        self.assertTrue(doc.is_file(), MESSAGE)
        text = doc.read_text(encoding="utf-8")
        self.assertIn(HEADLINE, text, MESSAGE)
        # Exact table cells prevent "opos-server" from satisfying "opos", and
        # set equality catches a documented essential the test does not pin.
        self.assertEqual(
            set(self.table_rows(text, "| Essential | Why it is there |")),
            OPOS_DEPENDS | UI_DEPENDS | SERVICES,
            MESSAGE,
        )

    def test_lean_doc_results_not_estimated(self):
        # Rows may only come from real tools/opos-bench.sh runs, never estimates.
        text = (ROOT / "docs" / "lean.md").read_text(encoding="utf-8")
        self.assertEqual(self.table_rows(text, RESULTS_HEADER), [], MESSAGE)

    def test_readme_headline_under_title(self):
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        limits = lines.index("## Limits")
        self.assertEqual(lines[limits + 2], README_LINE, MESSAGE)

    def table_rows(self, text, header):
        lines = text.splitlines()
        self.assertIn(header, lines, MESSAGE)
        rows = []
        for line in lines[lines.index(header) + 2:]:
            if not line.startswith("|"):
                break
            rows.append(line.split("|")[1].strip())
        return rows


if __name__ == "__main__":
    unittest.main()
