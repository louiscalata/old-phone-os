# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed units and package-hook failures, without real service managers."""
import configparser
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.pmos_lint import function_body

PACKAGE = Path(__file__).resolve().parents[1] / "packaging/pmos/opos"


class TestSystemdUnit(unittest.TestCase):
    def unit(self, name):
        path = PACKAGE / name
        self.assertTrue(path.is_file(), "missing installed unit " + name)
        parsed = configparser.ConfigParser(interpolation=None, strict=False)
        parsed.optionxform = str
        parsed.read(path)
        return parsed

    def test_server_uses_installed_runtime_and_safe_state_layout(self):
        service = self.unit("opos-server.service")["Service"]
        self.assertEqual(service["User"], "opos")
        self.assertEqual(service["Group"], "opos")
        self.assertEqual(shlex.split(service["ExecStart"]),
                         ["/usr/bin/python3", "-m", "opos.web", "--port", "8765"])
        self.assertEqual(shlex.split(service["ExecStartPre"]),
                         ["/usr/bin/python3", "/usr/libexec/opos-systemd-state", "--check"])
        environment = dict(value.split("=", 1) for value in shlex.split(service["Environment"]))
        self.assertEqual(environment["HOME"], "/var/lib/opos")
        self.assertEqual(environment["OPOS_CONFIG_DIR"], "/var/lib/opos/.config/opos")
        text = (PACKAGE / "opos-server.service").read_text()
        self.assertNotIn("/opt/", text)
        self.assertNotIn("PYTHONPATH", text)
        self.assertNotIn("StateDirectory=", text)
        self.assertEqual(service["ReadWritePaths"], "/var/lib/opos/.config/opos")

    def test_server_retains_sandbox_and_unprivileged_prestart(self):
        service = self.unit("opos-server.service")["Service"]
        for name, value in {
            "NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
            "PrivateTmp": "yes", "PrivateDevices": "yes", "CapabilityBoundingSet": "",
            "AmbientCapabilities": "", "UMask": "0077",
        }.items():
            with self.subTest(setting=name):
                self.assertEqual(service[name], value)
        self.assertFalse(service["ExecStartPre"].startswith(("+", "!")))

    def test_firstboot_has_explicit_shell_and_precedes_kiosk_and_server(self):
        unit = self.unit("opos-firstboot.service")
        self.assertEqual(shlex.split(unit["Service"]["ExecStart"]),
                         ["/bin/sh", "/usr/bin/opos-firstboot"])
        self.assertEqual(unit["Service"]["Type"], "oneshot")
        self.assertEqual(unit["Unit"]["ConditionPathExists"], "!/var/lib/opos/firstboot-done")
        self.assertTrue({"tinydm.service", "opos-server.service"}.issubset(
            set(unit["Unit"]["Before"].split())))
        self.assertIn("opos-firstboot.service", self.unit("opos-server.service")["Unit"]["After"].split())

    def test_recipe_installs_actual_unit_sources(self):
        body = function_body(PACKAGE / "APKBUILD", "_systemd")
        for name in ("opos-server.service", "opos-firstboot.service"):
            self.assertIn(f'install -Dm644 "$srcdir/{name}" '
                          f'"$subpkgdir/usr/lib/systemd/system/{name}"', body)

    def run_hook(self, failing=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            events = directory / "events"
            for name in ("python3", "systemctl"):
                program = directory / name
                program.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' '{name}'\" $*\" >> \"$EVENTS\"\n"
                    f'[ "${{FAIL_AT:-}}" != "{name}:$1" ] || exit 27\n',
                )
                program.chmod(0o755)
            result = subprocess.run(
                ["/bin/sh", str(PACKAGE / "opos-systemd.post-install")],
                env=dict(os.environ, PATH=temporary, EVENTS=str(events), FAIL_AT=failing or ""),
                capture_output=True, text=True, timeout=10, check=False,
            )
            return result, events.read_text().splitlines() if events.exists() else []

    def test_hook_prepares_then_enables_without_starting(self):
        result, events = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, [
            "python3 /usr/libexec/opos-systemd-state --prepare",
            "systemctl daemon-reload",
            "systemctl enable opos-firstboot.service opos-server.service",
        ])
        self.assertNotIn("--now", "\n".join(events))

    def test_hook_propagates_prepare_reload_and_enable_failures(self):
        for failure, count in (("python3:/usr/libexec/opos-systemd-state", 1),
                               ("systemctl:daemon-reload", 2), ("systemctl:enable", 3)):
            with self.subTest(failure=failure):
                result, events = self.run_hook(failure)
                self.assertEqual(result.returncode, 27, result.stderr)
                self.assertEqual(len(events), count)

    def test_shell_syntax(self):
        proc = subprocess.run(["sh", "-n", str(PACKAGE / "opos-systemd.post-install")], check=False)
        self.assertEqual(proc.returncode, 0)
