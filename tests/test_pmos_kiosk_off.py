# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestKioskOff(unittest.TestCase):
    def setUp(self):
        self.script = Path(__file__).resolve().parents[1] / "packaging" / "pmos" / "easy" / "opos-kiosk-off.sh"
        self.script.read_text()
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        state = os.path.join(self.td.name, "state")
        os.mkdir(state)
        self.state = state
        self.bin = Path(self.td.name) / "bin"
        self.bin.mkdir()
        self.tdm_log = os.path.join(self.td.name, "tdm.log")
        self.service_log = Path(self.td.name) / "service.log"
        fake = self.bin / "tinydm-set-session"
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TDM_LOG"\nexit "$TDM_EXIT"\n')
        fake.chmod(0o755)
        self.env = dict(os.environ, OPOS_STATE_DIR=state, OPOS_TDM=str(fake),
                        PATH=str(self.bin), TDM_LOG=self.tdm_log, TDM_EXIT="0",
                        SERVICE_LOG=str(self.service_log), SERVICE_EXIT="0")

    def service_stub(self, name):
        stub = self.bin / name
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SERVICE_LOG"\n'
                        'if [ "$1" = show ]; then printf " tinydm | default\\n"; fi\n'
                        'exit "$SERVICE_EXIT"\n')
        stub.chmod(0o755)

    def run_script(self, *args):
        return subprocess.run(["/bin/sh", self.script, *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_restore(self):
        Path(os.path.join(self.state, "prev-session")).write_text("/usr/share/wayland-sessions/phosh.desktop\n")
        result = subprocess.run(["/bin/sh", self.script], env=self.env, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr, Path(self.tdm_log).read_text()), (0, "kiosk: OFF (takes effect after reboot)\n", "", "-s /usr/share/wayland-sessions/phosh.desktop\n"))

    def test_no_previous_session_disables_tinydm(self):
        for manager, arguments in (("rc-update", "show default\ndel tinydm default\n"),
                                   ("systemctl", "disable tinydm.service\n")):
            for case in ("missing", "empty"):
                with self.subTest(manager=manager, case=case):
                    self.service_stub(manager)
                    prev = Path(self.state) / "prev-session"
                    prev.unlink(missing_ok=True)
                    if case == "empty":
                        prev.write_text("")
                    result = self.run_script()
                    self.assertEqual((result.returncode, result.stdout, result.stderr),
                                     (0, "kiosk: OFF (takes effect after reboot)\n", ""))
                    self.assertEqual(self.service_log.read_text(), arguments)
                    self.assertFalse(Path(self.tdm_log).exists())
                    self.service_log.unlink()
                    (self.bin / manager).unlink()

    def test_restore_failure_is_reported(self):
        prev = Path(self.state) / "prev-session"
        prev.write_text("/usr/share/wayland-sessions/phosh.desktop\n")
        self.service_stub("rc-update")
        self.env["TDM_EXIT"] = "7"
        result = self.run_script()
        self.assertEqual((result.returncode, result.stdout), (7, ""))
        self.assertEqual(Path(self.tdm_log).read_text(), "-s /usr/share/wayland-sessions/phosh.desktop\n")
        self.assertEqual(prev.read_text(), "/usr/share/wayland-sessions/phosh.desktop\n")
        self.assertFalse(self.service_log.exists())

    def test_disable_failure_is_reported(self):
        for manager in ("rc-update", "systemctl"):
            with self.subTest(manager=manager):
                self.service_stub(manager)
                self.env["SERVICE_EXIT"] = "9"
                result = self.run_script()
                self.assertEqual((result.returncode, result.stdout), (9, ""))
                self.assertTrue(self.service_log.exists())
                self.assertFalse(Path(self.tdm_log).exists())
                self.service_log.unlink()
                (self.bin / manager).unlink()

    def test_no_service_manager_fails(self):
        result = self.run_script()
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "cannot disable tinydm: no supported service manager found\n"))
        self.assertFalse(Path(self.tdm_log).exists())

    def test_dry_run_with_previous_session(self):
        prev = os.path.join(self.state, "prev-session")
        Path(prev).write_text("/usr/share/wayland-sessions/phosh.desktop\n")
        self.service_stub("rc-update")
        result = self.run_script("--dry-run")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "[dry-run] opos-kiosk-off plan: would run tinydm-set-session -s /usr/share/wayland-sessions/phosh.desktop (kiosk: OFF after reboot); nothing changed\n")
        self.assertEqual(result.stderr, "")
        self.assertFalse(Path(self.tdm_log).exists())
        self.assertFalse(self.service_log.exists())
        self.assertEqual(Path(prev).read_text(), "/usr/share/wayland-sessions/phosh.desktop\n")

    def test_dry_run_without_previous_session(self):
        self.service_stub("rc-update")
        for case in ("missing", "empty"):
            with self.subTest(case=case):
                prev = Path(self.state) / "prev-session"
                if case == "empty":
                    prev.write_text("")
                before = {p.name: p.read_bytes() for p in Path(self.state).iterdir()}
                result = self.run_script("--dry-run")
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "[dry-run] opos-kiosk-off plan: no previous session recorded, would disable tinydm (kiosk: OFF after reboot); nothing changed\n")
                self.assertEqual(result.stderr, "")
                self.assertFalse(Path(self.tdm_log).exists())
                self.assertFalse(self.service_log.exists())
                self.assertEqual({p.name: p.read_bytes() for p in Path(self.state).iterdir()}, before)

    def test_unknown_argument(self):
        result = subprocess.run(["/bin/sh", self.script, "--bogus"], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "usage: opos-kiosk-off [--dry-run]\n")
        self.assertFalse(Path(self.tdm_log).exists())


if __name__ == "__main__":
    unittest.main()
