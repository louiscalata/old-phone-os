# SPDX-License-Identifier: GPL-3.0-or-later
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APKBUILD = ROOT / "packaging" / "pmos" / "postmarketos-ui-opos" / "APKBUILD"
POST_INSTALL = ROOT / "packaging" / "pmos" / "postmarketos-ui-opos" / "postmarketos-ui-opos-openrc.post-install"


class TestPmosUiOpenrc(unittest.TestCase):
    def test_apkbuild_subpackage(self):
        text = APKBUILD.read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r'^subpackages="\$pkgname-openrc:_openrc"$', text, re.M))

    def test_apkbuild_openrc_function(self):
        text = APKBUILD.read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r'_openrc\(\)\s*\{', text))
        self.assertIsNotNone(re.search(r'install_if="\$pkgname=\$pkgver-r\$pkgrel openrc"', text))
        self.assertIsNotNone(re.search(r'install="\$subpkgname\.post-install"', text))
        self.assertIsNotNone(re.search(r'depends="networkmanager-openrc openrc"', text))
        self.assertIsNotNone(re.search(r'mkdir -p "\$subpkgdir"', text))

    def test_post_install_syntax(self):
        result = subprocess.run(["sh", "-n", str(POST_INSTALL)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def _make_stubs(self, tmp, ifquery_code, log):
        bindir = Path(tmp) / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        ifquery = bindir / "ifquery"
        ifquery.write_text("#!/bin/sh\nexit " + str(ifquery_code) + "\n", encoding="utf-8")
        os.chmod(ifquery, 0o755)
        rc_update = bindir / "rc-update"
        rc_update.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"" + str(log) + "\"\n", encoding="utf-8")
        os.chmod(rc_update, 0o755)
        return bindir

    def _run(self, bindir, ifaces):
        env = dict(os.environ)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        env["OPOS_INTERFACES"] = str(ifaces)
        return subprocess.run(["sh", str(POST_INSTALL)], env=env, capture_output=True, text=True)

    def test_creates_loopback_when_unparseable_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "rc.log"
            log.write_text("", encoding="utf-8")
            bindir = self._make_stubs(tmp, 1, log)
            ifaces = Path(tmp) / "etc" / "interfaces"
            result = self._run(bindir, ifaces)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(ifaces.is_file())
            content = ifaces.read_text(encoding="utf-8")
            self.assertIn("iface lo inet loopback", content)
            self.assertIn("auto lo", content)
            self.assertIn("add networkmanager default", log.read_text(encoding="utf-8"))

    def test_preserves_orig_when_unparseable_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "rc.log"
            log.write_text("", encoding="utf-8")
            bindir = self._make_stubs(tmp, 1, log)
            ifaces = Path(tmp) / "interfaces"
            orig_text = "broken-interfaces-content\n"
            ifaces.write_text(orig_text, encoding="utf-8")
            result = self._run(bindir, ifaces)
            self.assertEqual(result.returncode, 0)
            backup = Path(str(ifaces) + ".opos-orig")
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.read_text(encoding="utf-8"), orig_text)
            content = ifaces.read_text(encoding="utf-8")
            self.assertIn("iface lo inet loopback", content)
            self.assertIn("add networkmanager default", log.read_text(encoding="utf-8"))

    def test_leaves_valid_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "rc.log"
            log.write_text("", encoding="utf-8")
            bindir = self._make_stubs(tmp, 0, log)
            ifaces = Path(tmp) / "interfaces"
            result = self._run(bindir, ifaces)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(ifaces.exists())
            self.assertIn("add networkmanager default", log.read_text(encoding="utf-8"))
            good_text = "auto lo\niface lo inet loopback\n"
            ifaces.write_text(good_text, encoding="utf-8")
            result = self._run(bindir, ifaces)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(ifaces.read_text(encoding="utf-8"), good_text)
            self.assertFalse(Path(str(ifaces) + ".opos-orig").exists())
            self.assertIn("add networkmanager default", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
