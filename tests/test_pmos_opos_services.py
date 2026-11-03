# SPDX-License-Identifier: GPL-3.0-or-later
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
INITD = ROOT / "packaging" / "pmos" / "opos" / "opos-firstboot.initd"
POSTINSTALL = ROOT / "packaging" / "pmos" / "opos" / "opos-openrc.post-install"
STAGE = ROOT / "packaging" / "pmos" / "stage-aports.sh"
RE_FIRSTBOOT = "first-boot marker|first boot marker|opos-firstboot-marker|/var/lib/opos/firstboot"
RE_FIRSTBOOT_RAN = "opos-firstboot"
class TestOposServices(unittest.TestCase):
    def test_initd(self):
        text = INITD.read_text()
        self.assertTrue(text.startswith("#!/sbin/openrc-run"))
        self.assertIn("/usr/bin/opos-firstboot", text)
        self.assertIn(RE_FIRSTBOOT_RAN, text)
        self.assertIn("need localmount", text)
        self.assertIn("before tinydm", text)
        pattern = re.compile(RE_FIRSTBOOT)
        einfo_lines = [line for line in text.splitlines() if "einfo" in line]
        self.assertTrue(einfo_lines)
        self.assertTrue(any("first boot complete" in line and "/var/lib/opos/firstboot" in line and pattern.search(line) for line in einfo_lines))
    def test_post_install(self):
        text = POSTINSTALL.read_text()
        self.assertIn("rc-update add opos-firstboot default", text)
        result = subprocess.run(["sh", "-n", str(POSTINSTALL)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
    def test_firstboot_returns_helper_status_even_when_logging_succeeds(self):
        for status in (0, 7):
            for marker_exists in (False, True):
                with self.subTest(status=status, marker=marker_exists), tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    helper = base / "firstboot"
                    helper.write_text(f"#!/bin/sh\nexit {status}\n")
                    helper.chmod(0o755)
                    marker = base / "firstboot-done"
                    if marker_exists:
                        marker.touch()
                    initd = base / "initd"
                    initd.write_text(INITD.read_text().replace("/usr/bin/opos-firstboot", str(helper)).replace("/var/lib/opos/firstboot-done", str(marker)))
                    harness = '''
ebegin() { :; }
eend() { printf 'eend: %s\\n' "$1"; return 0; }
einfo() { printf 'einfo: %s\\n' "$*"; }
. "$1"
start
'''
                    result = subprocess.run(["/bin/sh", "-c", harness, "test-firstboot", str(initd)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertIn(f"eend: {status}\n", result.stdout)
                    self.assertEqual("first boot complete" in result.stdout, status == 0 and marker_exists)
    def test_post_install_propagates_each_rc_update_failure(self):
        for failed_call, expected_calls in (("opos-firstboot", "add opos-firstboot default\n"),
                                             ("opos-server", "add opos-firstboot default\nadd opos-server default\n"),
                                             ("none", "add opos-firstboot default\nadd opos-server default\n")):
            with self.subTest(failed_call=failed_call), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                log = base / "calls"
                stub = base / "rc-update"
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nif [ "$2" = "$FAILED_CALL" ]; then exit 8; fi\n')
                stub.chmod(0o755)
                env = dict(os.environ, PATH=str(base), CALL_LOG=str(log), FAILED_CALL=failed_call)
                result = subprocess.run(["/bin/sh", str(POSTINSTALL)], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0 if failed_call == "none" else 8, result.stderr)
                self.assertEqual(log.read_text(), expected_calls)
    def test_stage_copies_easy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            out = tmp_path / "out"
            (repo / "packaging" / "pmos" / "opos").mkdir(parents=True)
            (repo / "packaging" / "pmos" / "postmarketos-ui-opos").mkdir(parents=True)
            (repo / "packaging" / "pmos" / "easy").mkdir(parents=True)
            (repo / "deploy").mkdir(parents=True)
            (repo / "packaging" / "pmos" / "opos" / "APKBUILD").write_text("pkgver=1\n")
            (repo / "packaging" / "pmos" / "postmarketos-ui-opos" / "APKBUILD").write_text("pkgver=1\n")
            (repo / "deploy" / "50-opos-modemmanager.rules").write_text("rules\n")
            (repo / "deploy" / "opos-sysusers.conf").write_text("sysusers\n")
            (repo / "packaging" / "pmos" / "easy" / "opos-firstboot.sh").write_text("#!/bin/sh\nexit 0\n")
            (repo / "packaging" / "pmos" / "easy" / "opos-kiosk-off.sh").write_text("#!/bin/sh\nexit 0\n")
            shutil.copy(str(STAGE), str(repo / "packaging" / "pmos" / "stage-aports.sh"))
            result = subprocess.run(["sh", str(repo / "packaging" / "pmos" / "stage-aports.sh"), str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertTrue((out / "main" / "opos" / "opos-firstboot.sh").is_file())
            self.assertTrue((out / "main" / "opos" / "opos-kiosk-off.sh").is_file())
if __name__ == "__main__":
    unittest.main()
