# SPDX-License-Identifier: GPL-3.0-or-later
"""Installer regressions using local fixtures and static Windows checks."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.pmos_lint import apkbuild_vars
from tests.test_installer import fixture_git_env, fresh_sandbox

ROOT = Path(__file__).resolve().parents[1]
KIOSK = ROOT / "packaging/pmos/easy/opos-kiosk-off.sh"
HOOK = ROOT / "packaging/pmos/postmarketos-ui-opos/postmarketos-ui-opos.post-install"
OFF = "kiosk: OFF (takes effect after reboot)\n"


class KioskOffRegression(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.enabled = self.base / "enabled"
        self.enabled.write_text("tinydm\n")
        self.log = self.base / "rc.log"
        self.tdm_log = self.base / "tdm.log"
        self.installed = self.bin / "opos-kiosk-off"
        shutil.copyfile(KIOSK, self.installed)
        self.installed.chmod(0o755)
        rc = self.bin / "rc-update"
        rc.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$RC_LOG"\n'
            'case "$*" in\n'
            '  "show default")\n'
            '    [ "$SHOW_EXIT" -eq 0 ] || exit "$SHOW_EXIT"\n'
            '    printf " tinydm-other | default\\n"\n'
            '    if [ -s "$RC_ENABLED" ]; then printf "  tinydm | default\\n"; fi\n'
            '    ;;\n'
            '  "del tinydm default")\n'
            '    [ "$DEL_EXIT" -eq 0 ] || exit "$DEL_EXIT"\n'
            '    [ -s "$RC_ENABLED" ] || exit 1\n'
            '    : > "$RC_ENABLED"\n'
            '    ;;\n'
            '  *) exit 90 ;;\n'
            'esac\n'
        )
        rc.chmod(0o755)
        tdm = self.bin / "tinydm-set-session"
        tdm.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TDM_LOG"\n')
        tdm.chmod(0o755)
        self.env = dict(
            os.environ, PATH=str(self.bin), OPOS_STATE_DIR=str(self.base),
            OPOS_TDM=str(tdm), RC_LOG=str(self.log), RC_ENABLED=str(self.enabled),
            SHOW_EXIT="0", DEL_EXIT="0", TDM_LOG=str(self.tdm_log),
        )

    def run_script(self):
        # Direct execution exercises the installed helper's shebang, without sh's fallback.
        return subprocess.run([str(self.installed)], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_shebang_precedes_spdx(self):
        self.assertEqual(KIOSK.read_text().splitlines()[:2],
                         ["#!/bin/sh", "# SPDX-License-Identifier: GPL-3.0-or-later"])

    def test_disable_twice_succeeds_without_second_delete(self):
        for _ in range(2):
            result = self.run_script()
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, OFF, ""))
        self.assertEqual(self.log.read_text(), "show default\ndel tinydm default\nshow default\n")
        self.assertEqual(self.enabled.read_text(), "")
        self.assertFalse(self.tdm_log.exists())

    def test_similarly_named_service_is_not_tinydm(self):
        self.enabled.write_text("")
        result = self.run_script()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, OFF, ""))
        self.assertEqual(self.log.read_text(), "show default\n")

    def test_runlevel_query_failure_is_preserved(self):
        self.env["SHOW_EXIT"] = "7"
        result = self.run_script()
        self.assertEqual((result.returncode, result.stdout), (7, ""))
        self.assertEqual(self.log.read_text(), "show default\n")
        self.assertEqual(self.enabled.read_text(), "tinydm\n")

    def test_delete_failure_is_preserved(self):
        self.env["DEL_EXIT"] = "9"
        result = self.run_script()
        self.assertEqual((result.returncode, result.stdout), (9, ""))
        self.assertEqual(self.log.read_text(), "show default\ndel tinydm default\n")
        self.assertEqual(self.enabled.read_text(), "tinydm\n")

    def test_direct_execution_restores_previous_session(self):
        previous = "/usr/share/wayland-sessions/phosh.desktop"
        (self.base / "prev-session").write_text(previous + "\n")
        result = self.run_script()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, OFF, ""))
        self.assertEqual(self.tdm_log.read_text(), "-s " + previous + "\n")
        self.assertFalse(self.log.exists())


class WindowsInstallerRegression(unittest.TestCase):
    def test_uninstall_failure_exits_before_cleanup(self):
        text = (ROOT / "install.ps1").read_text()
        uninstall = text.split("if ($Uninstall) {", 1)[1].split("if ($env:OPOS_PYTHON)", 1)[0]
        self.assertRegex(
            uninstall,
            r"& \$Launcher @u\s+if \(\$LASTEXITCODE -ne 0\) \{ exit \$LASTEXITCODE \}\s+}\s+foreach",
        )
        self.assertLess(uninstall.index("exit $LASTEXITCODE"), uninstall.index("Remove-Item"))

    def test_next_steps_are_only_in_live_branch(self):
        text = (ROOT / "install.ps1").read_text()
        tail = text.split("# Print commands as PowerShell literals;", 1)[1]
        branches = re.search(
            r"if \(\$DryRun\) \{(?P<dry>.*?)\n} else \{(?P<live>.*?)\n}\nif \(-not \$NoLaunch",
            tail, re.S,
        )
        self.assertIsNotNone(branches)
        dry, live = branches.group("dry"), branches.group("live")
        self.assertEqual(dry.strip(), "Say 'Dry run finished. Nothing was installed.'")
        for command in ("$env:Path = ", " run --defaults", " web"):
            self.assertIn(command, live)
            self.assertNotIn(command, dry)

    def test_cmd_wrapper_preserves_arguments_and_uses_crlf(self):
        data = (ROOT / "install.cmd").read_bytes()
        self.assertTrue(data.endswith(b"\r\n"))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(
            b'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*\r\n',
            data,
        )
        self.assertRegex((ROOT / "README.md").read_text(), r"On Windows[^\n]*`install\.cmd`[^\n]*`install\.ps1`")


class UiPostInstallRegression(unittest.TestCase):
    def run_hook(self, completed, status):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            marker = base / "firstboot-done"
            if completed:
                marker.touch()
            text = HOOK.read_text()
            self.assertIn("if [ ! -e /var/lib/opos/firstboot-done ]; then", text)
            # Redirect only the fixed marker path; never write to the host's /var/lib.
            hook = base / "post-install"
            hook.write_text(text.replace("/var/lib/opos/firstboot-done", str(marker)))
            log = base / "session.log"
            tdm = base / "tinydm-set-session"
            tdm.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "$TDM_LOG"\nexit {status}\n')
            tdm.chmod(0o755)
            result = subprocess.run(["/bin/sh", str(hook)],
                                    env=dict(os.environ, PATH=str(base), TDM_LOG=str(log)),
                                    capture_output=True, text=True, timeout=10)
            return result, log.read_text() if log.exists() else None

    def test_first_install_selects_kiosk(self):
        result, log = self.run_hook(completed=False, status=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log, "-s /usr/share/wayland-sessions/cage-ui.desktop\n")

    def test_upgrade_after_firstboot_keeps_session(self):
        result, log = self.run_hook(completed=True, status=7)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(log)

    def test_first_install_failure_is_preserved(self):
        result, log = self.run_hook(completed=False, status=7)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIsNotNone(log)


@unittest.skipUnless(shutil.which("git"), "git unavailable")
class BootstrapCleanupRegression(unittest.TestCase):
    def test_bogus_commit_removes_clone_and_allows_retry(self):
        with fresh_sandbox() as tmp:
            base = Path(tmp)
            repo = base / "source.git"
            repo.mkdir()
            env = fixture_git_env(repo)
            git = shutil.which("git")
            # An empty bare repository needs no fixture commits or branch changes.
            subprocess.run([git, "init", "--bare", str(repo)], env=env,
                           capture_output=True, text=True, check=True, timeout=20)
            dest = base / "checkout"
            ref = "f" * 40
            env.update(OPOS_REPO_URL=str(repo), OPOS_REF=ref, OPOS_SRC_DIR=str(dest))
            message = (
                f"bootstrap: cannot clone {repo} (ref {ref}): the repository is unreachable, "
                "private or not yet published. Use a published OPOS_REPO_URL/OPOS_REF "
                "from the release manifest, or run install.sh from a retained checkout.\n"
            )
            for _ in range(2):
                result = subprocess.run(["/bin/sh", str(ROOT / "bootstrap.sh")], env=env,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertNotIn("destination exists", result.stderr)
                self.assertFalse(dest.exists())
                self.assertTrue(repo.is_dir())


class PackagingRegression(unittest.TestCase):
    def test_opos_release_and_changed_source_checksum(self):
        values = apkbuild_vars(ROOT / "packaging/pmos/opos/APKBUILD")
        self.assertEqual(values["pkgrel"], "3")
        tokens = values["sha512sums"].split()
        checksums = dict(zip(tokens[1::2], tokens[::2]))
        self.assertEqual(checksums[KIOSK.name], hashlib.sha512(KIOSK.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
