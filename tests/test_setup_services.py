# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import os
import sys
import plistlib
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from opos.setup.services import LAUNCHD_LABEL, SYSTEM_ONLY, UNIT_NAME, ServiceError, install_services, read_unit_template, render_launchd_plist, render_user_unit, uninstall_services, unit_dir

TEMPLATE = """# SPDX-License-Identifier: GPL-3.0-or-later
[Unit]
Description=Old Phone OS server
After=network.target ModemManager.service
Wants=ModemManager.service

[Service]
Type=simple
# The opos user needs the polkit rule in deploy/50-opos-modemmanager.rules.
User=opos
Group=opos
ExecStart=/usr/bin/python3 -m opos.web --port 8765
WorkingDirectory=/opt/old-phone-os
Environment=PYTHONPATH=/opt/old-phone-os/src
Environment=PYTHONDONTWRITEBYTECODE=1
StateDirectory=opos
StateDirectoryMode=0700
UMask=0077
Restart=on-failure
RestartSec=2
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
# PrivateDevices is safe because the modem is reached over D-Bus.
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
ProcSubset=pid
# AF_UNIX is for the D-Bus system bus to ModemManager; /sys stays readable for the battery.
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
IPAddressDeny=any
IPAddressAllow=localhost
InaccessiblePaths=/home /root
ReadOnlyPaths=/opt/old-phone-os
TasksMax=64
MemoryMax=256M

[Install]
WantedBy=multi-user.target
"""

def _make_repo(base: Path, content: str = TEMPLATE) -> Path:
    repo = base / "repo"
    (repo / "deploy").mkdir(parents=True, exist_ok=True)
    (repo / "deploy" / "opos-server.service").write_text(content)
    return repo

class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.results: dict[tuple[str, ...], tuple[int, str]] = {}
    def set(self, cmd: list[str], rc: int, out: str):
        self.results[tuple(cmd)] = (rc, out)
    def __call__(self, cmd):
        self.calls.append(list(cmd))
        return self.results.get(tuple(cmd), (0, ""))

class TestConstants(unittest.TestCase):
    def test_unit_name(self):
        self.assertEqual(UNIT_NAME, "opos-server.service")
    def test_launchd_label(self):
        self.assertEqual(LAUNCHD_LABEL, "org.oldphoneos.opos")
    def test_system_only(self):
        self.assertEqual(SYSTEM_ONLY, frozenset({"User","Group","StateDirectory","StateDirectoryMode","InaccessiblePaths","ReadOnlyPaths","ProtectHome","PrivateDevices","IPAddressDeny","IPAddressAllow","MemoryDenyWriteExecute","SystemCallFilter","ProtectProc","ProcSubset","ProtectClock","ProtectKernelLogs","RestrictRealtime","CapabilityBoundingSet","AmbientCapabilities"}))
    def test_service_error_is_runtime(self):
        self.assertTrue(issubclass(ServiceError, RuntimeError))

class TestReadUnitTemplate(unittest.TestCase):
    def test_read_success(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            self.assertEqual(read_unit_template(repo), TEMPLATE)
    def test_missing_raises(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "empty"
            repo.mkdir()
            with self.assertRaises(FileNotFoundError):
                read_unit_template(repo)

class TestUnitDir(unittest.TestCase):
    def test_linux(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            self.assertEqual(unit_dir(home, "linux"), home / ".config" / "systemd" / "user")
    def test_darwin(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            self.assertEqual(unit_dir(home, "darwin"), home / "Library" / "LaunchAgents")
    def test_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            with self.assertRaises(ValueError):
                unit_dir(home, "win32")
            with self.assertRaises(ValueError):
                unit_dir(home, "freebsd")

class TestRenderUserUnit(unittest.TestCase):
    def test_replaces_execstart(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            cfg = base / "cfg"
            cfg.mkdir()
            out = render_user_unit(TEMPLATE, repo, "/usr/bin/python3", cfg, port=8765)
            self.assertIn(f"ExecStart=/usr/bin/python3 -m opos.web --port 8765 --token-file {cfg}/session.token", out)
    def test_port_param(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = base / "cfg"
            cfg.mkdir()
            repo = base / "repo"
            out = render_user_unit(TEMPLATE, repo, "/usr/bin/python3", cfg, port=9999)
            self.assertIn("--port 9999", out)
    def test_working_directory(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            cfg = base / "cfg"
            cfg.mkdir()
            out = render_user_unit(TEMPLATE, repo, "/usr/bin/python3", cfg)
            self.assertIn(f"WorkingDirectory={repo}", out)
            self.assertNotIn("WorkingDirectory=/opt/old-phone-os", out)
    def test_environment_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            cfg = base / "cfg"
            cfg.mkdir()
            out = render_user_unit(TEMPLATE, repo, "/usr/bin/python3", cfg)
            self.assertIn(f"Environment=PYTHONPATH={repo}/src", out)
            self.assertIn("Environment=PYTHONDONTWRITEBYTECODE=1", out)
            self.assertIn(f"Environment=OPOS_CONFIG_DIR={cfg}\n", out)
            self.assertNotIn("/opt/old-phone-os/src", out)
    def test_selected_config_directory_overrides_ambient_environment(self):
        cfg = Path("/srv/owner/isolated-opos")
        with mock.patch.dict(os.environ, {"OPOS_CONFIG_DIR": "/srv/owner/other-opos"}):
            out = render_user_unit(TEMPLATE, Path("/srv/owner/opos"), "/usr/bin/python3", cfg)
        self.assertIn(f"Environment=OPOS_CONFIG_DIR={cfg}\n", out)
        self.assertIn(f"--token-file {cfg}/session.token\n", out)
        self.assertNotIn("/srv/owner/other-opos", out)
    def test_description_kept(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            out = render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", base / "cfg")
            (base / "cfg").mkdir()
            self.assertIn("Description=Old Phone OS server", out)
    def test_drops_system_only(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "cfg").mkdir()
            out = render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", base / "cfg")
            for k in SYSTEM_ONLY:
                if k == "ProtectHome":
                    continue
                self.assertNotIn(f"\n{k}=", out)
            self.assertNotIn("User=opos", out)
            self.assertNotIn("Group=opos", out)
            self.assertNotIn("StateDirectory=", out)
            self.assertNotIn("InaccessiblePaths=", out)
            self.assertNotIn("ReadOnlyPaths=/opt/old-phone-os", out)
            self.assertNotIn("PrivateDevices=", out)
            self.assertNotIn("IPAddressDeny=", out)
            self.assertNotIn("MemoryDenyWriteExecute=", out)
            self.assertNotIn("SystemCallFilter=", out)
    def test_keeps_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "cfg").mkdir()
            out = render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", base / "cfg")
            for keep in ["NoNewPrivileges=yes","ProtectSystem=strict","PrivateTmp=yes","ProtectKernelTunables=yes","ProtectKernelModules=yes","ProtectControlGroups=yes","RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6","RestrictNamespaces=yes","RestrictSUIDSGID=yes","LockPersonality=yes","SystemCallArchitectures=native","TasksMax=64","MemoryMax=256M","UMask=0077","Restart=on-failure","RestartSec=2"]:
                self.assertIn(keep, out)
    def test_adds_readwrite_and_protecthome(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = base / "cfg"
            cfg.mkdir()
            out = render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", cfg)
            self.assertIn(f"ReadWritePaths={cfg}", out)
            self.assertIn("ProtectHome=read-only", out)
            self.assertNotIn("ProtectHome=yes", out)
    def test_drops_modemmanager(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "cfg").mkdir()
            out = render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", base / "cfg")
            self.assertNotIn("ModemManager", out)
            self.assertNotIn("ModemManager.service", out)
    def test_install_wantedby(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "cfg").mkdir()
            out = render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", base / "cfg")
            self.assertIn("WantedBy=default.target", out)
            self.assertNotIn("multi-user.target", out)
    def test_deterministic_and_newline(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "cfg").mkdir()
            a = render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", base / "cfg")
            b = render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", base / "cfg")
            self.assertEqual(a, b)
            self.assertTrue(a.endswith("\n"))
    def test_whitespace_raises(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = base / "cfg"
            cfg.mkdir()
            with self.assertRaises(ValueError):
                render_user_unit(TEMPLATE, Path("/tmp/has space"), "/usr/bin/python3", cfg)
            with self.assertRaises(ValueError):
                render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python 3", cfg)
            with self.assertRaises(ValueError):
                render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", Path("/tmp/with space"))
    def test_percent_raises(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = base / "cfg"
            cfg.mkdir()
            with self.assertRaises(ValueError):
                render_user_unit(TEMPLATE, Path("/tmp/%n"), "/usr/bin/python3", cfg)
            with self.assertRaises(ValueError):
                render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", Path("/tmp/a%b"))
    def test_control_raises(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = base / "cfg"
            cfg.mkdir()
            with self.assertRaises(ValueError):
                render_user_unit(TEMPLATE, Path("/tmp/\x01bad"), "/usr/bin/python3", cfg)
            with self.assertRaises(ValueError):
                render_user_unit(TEMPLATE, base / "repo", "/usr/bin/python3", Path("/tmp/\nbad"))

class TestRenderLaunchdPlist(unittest.TestCase):
    def test_plist_fields(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            cfg = base / "cfg"
            cfg.mkdir()
            s = render_launchd_plist(repo, "/usr/bin/python3", cfg, port=8765)
            data = plistlib.loads(s.encode())
            self.assertEqual(data["Label"], LAUNCHD_LABEL)
            self.assertEqual(data["ProgramArguments"], ["/usr/bin/python3","-m","opos.web","--port","8765","--token-file", str(cfg / "session.token")])
            self.assertEqual(data["WorkingDirectory"], str(repo))
            self.assertEqual(data["EnvironmentVariables"], {"PYTHONPATH": f"{repo}/src", "PYTHONDONTWRITEBYTECODE": "1", "OPOS_CONFIG_DIR": str(cfg)})
            self.assertTrue(data["RunAtLoad"])
            self.assertTrue(data["KeepAlive"])
            self.assertEqual(data["StandardOutPath"], str(cfg / "logs" / "stdout.log"))
            self.assertEqual(data["StandardErrorPath"], str(cfg / "logs" / "stderr.log"))
    def test_custom_port(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            cfg = base / "cfg"
            cfg.mkdir()
            s = render_launchd_plist(repo, "/opt/py", cfg, port=1234)
            data = plistlib.loads(s.encode())
            self.assertIn("1234", data["ProgramArguments"])
    def test_is_xml(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            cfg = base / "cfg"
            cfg.mkdir()
            s = render_launchd_plist(repo, "/usr/bin/python3", cfg)
            self.assertIn("<plist", s)
            self.assertTrue(s.strip().endswith("</plist>"))
    def test_launchd_allows_spaces(self):
        s = render_launchd_plist(Path("/home/x/My Repo"), "/usr/bin/python3", Path("/home/x/Application Support/opos"))
        self.assertIn("Application Support", s)
        self.assertIn("My Repo", s)
    def test_selected_config_directory_survives_plist_encoding(self):
        cfg = Path("/opt/owner/Application Support/OPOS & preferences")
        with mock.patch.dict(os.environ, {"OPOS_CONFIG_DIR": "/opt/owner/other-opos"}):
            data = plistlib.loads(render_launchd_plist(Path("/opt/owner/opos"), "/usr/bin/python3", cfg).encode())
        self.assertEqual(data["EnvironmentVariables"]["OPOS_CONFIG_DIR"], str(cfg))
        self.assertEqual(data["ProgramArguments"][-1], str(cfg / "session.token"))
    def test_launchd_rejects_newline(self):
        with self.assertRaises(ValueError):
            render_launchd_plist(Path("/home/x/a\nb"), "/usr/bin/python3", Path("/home/x/cfg"))

class TestInstallServices(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "file modes not honoured on Windows")
    def test_linux_writes_and_runs(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            res = install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=runner)
            unit_path = home / ".config" / "systemd" / "user" / UNIT_NAME
            self.assertTrue(unit_path.is_file())
            self.assertEqual(stat.S_IMODE(unit_path.stat().st_mode), 0o644)
            expected = render_user_unit(TEMPLATE, repo, "/usr/bin/python3", cfg)
            self.assertEqual(unit_path.read_text(), expected)
            self.assertEqual(runner.calls[0], ["systemctl","--user","daemon-reload"])
            self.assertEqual(runner.calls[1], ["systemctl","--user","enable","--now",UNIT_NAME])
            self.assertIsInstance(res, list)
    def test_linux_returns_linger_guidance_without_running_it(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            res = install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=runner)
            self.assertEqual(res[-1], 'To start at boot without login, run: sudo loginctl enable-linger "$USER"')
            self.assertEqual(runner.calls, [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", UNIT_NAME]])
    def test_linux_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            res = install_services(repo, "/usr/bin/python3", cfg, home, "linux", dry_run=True, runner=runner)
            unit_path = home / ".config" / "systemd" / "user" / UNIT_NAME
            self.assertFalse(unit_path.exists())
            self.assertEqual(len(runner.calls), 0)
            self.assertTrue(any("would write" in s and str(unit_path) in s for s in res))
            self.assertTrue(any("would run:" in s for s in res))
    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            r1 = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=r1)
            p = home / ".config" / "systemd" / "user" / UNIT_NAME
            c1 = p.read_text()
            r2 = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=r2)
            c2 = p.read_text()
            self.assertEqual(c1, c2)
    def test_linux_error_raises(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            runner.set(["systemctl","--user","daemon-reload"], 1, "boom fail")
            with self.assertRaises(ServiceError) as cm:
                install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=runner)
            self.assertIn("boom fail", str(cm.exception))
            runner2 = FakeRunner()
            runner2.set(["systemctl","--user","enable","--now",UNIT_NAME], 1, "enable fail")
            with self.assertRaises(ServiceError):
                install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=runner2)
    @unittest.skipIf(sys.platform == "win32", "launchd not available on Windows")
    def test_darwin_install(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            uid = os.getuid()
            with mock.patch("os.getuid", return_value=uid):
                res = install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=runner)
            plist_path = home / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist")
            self.assertTrue(plist_path.is_file())
            self.assertEqual(stat.S_IMODE(plist_path.stat().st_mode), 0o644)
            data = plistlib.loads(plist_path.read_bytes())
            self.assertEqual(data["Label"], LAUNCHD_LABEL)
            self.assertEqual(runner.calls[0][:3], ["launchctl","bootstrap",f"gui/{uid}"])
            self.assertEqual(runner.calls[0][3], str(plist_path))
            self.assertIsInstance(res, list)
    @unittest.skipIf(sys.platform == "win32", "launchd not available on Windows")
    def test_darwin_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            with mock.patch("os.getuid", return_value=501):
                res = install_services(repo, "/usr/bin/python3", cfg, home, "darwin", dry_run=True, runner=runner)
            plist_path = home / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist")
            self.assertFalse(plist_path.exists())
            self.assertEqual(runner.calls, [])
            self.assertTrue(any("would write" in s for s in res))
            self.assertTrue(any("would run:" in s for s in res))
    @unittest.skipIf(sys.platform == "win32", "launchd not available on Windows")
    def test_darwin_error_raises(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            uid = os.getuid()
            runner.set(["launchctl","bootstrap",f"gui/{uid}", str(home / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist"))], 1, "launch fail")
            with mock.patch("os.getuid", return_value=uid):
                with self.assertRaises(ServiceError):
                    install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=runner)
    @unittest.skipIf(sys.platform == "win32", "launchd not available on Windows")
    def test_darwin_rerun_unloads_previous_job_first(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            uid = os.getuid()
            plist_path = home / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist")
            with mock.patch("os.getuid", return_value=uid):
                install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=FakeRunner())
                runner = FakeRunner()
                runner.set(["launchctl","bootout",f"gui/{uid}/{LAUNCHD_LABEL}"], 3, "not loaded")
                res = install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=runner)
            self.assertEqual(runner.calls, [["launchctl","bootout",f"gui/{uid}/{LAUNCHD_LABEL}"], ["launchctl","bootstrap",f"gui/{uid}",str(plist_path)]])
            self.assertIn("previous service was not loaded", res)
    @unittest.skipIf(sys.platform == "win32", "launchd not available on Windows")
    def test_darwin_failed_write_keeps_running_service(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            with mock.patch("os.getuid", return_value=501):
                install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=FakeRunner())
                runner = FakeRunner()
                with mock.patch("opos.setup.services._write_atomic", side_effect=PermissionError("read-only")):
                    with self.assertRaises(PermissionError):
                        install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=runner)
            self.assertEqual(runner.calls, [])
    def test_linux_rerun_restarts_service(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            first = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=first)
            self.assertNotIn(["systemctl","--user","restart",UNIT_NAME], first.calls)
            again = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=again)
            self.assertEqual(again.calls[-1], ["systemctl","--user","restart",UNIT_NAME])
    def test_unsupported(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            res = install_services(repo, "/usr/bin/python3", cfg, home, "win32", runner=runner)
            self.assertEqual(res, ["services not supported on win32: run python -m opos.web manually"])
            self.assertEqual(runner.calls, [])
            self.assertFalse((home / ".config" / "systemd" / "user" / UNIT_NAME).exists())
            res2 = install_services(repo, "/usr/bin/python3", cfg, home, "otheros", runner=runner)
            self.assertIn("otheros", res2[0])

class TestUninstallServices(unittest.TestCase):
    def test_linux_uninstall(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            r = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=r)
            r.calls.clear()
            res = uninstall_services(home, "linux", runner=r)
            unit_path = home / ".config" / "systemd" / "user" / UNIT_NAME
            self.assertFalse(unit_path.exists())
            self.assertEqual(r.calls[0], ["systemctl","--user","disable","--now",UNIT_NAME])
            self.assertEqual(r.calls[1], ["systemctl","--user","daemon-reload"])
            self.assertIsInstance(res, list)
    def test_linux_failed_stop_keeps_unit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            r = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=r)
            fail = FakeRunner()
            fail.set(["systemctl","--user","disable","--now",UNIT_NAME], 1, "not loaded")
            with self.assertRaisesRegex(ServiceError, "Service stop failed; kept"):
                uninstall_services(home, "linux", runner=fail)
            self.assertTrue((home / ".config" / "systemd" / "user" / UNIT_NAME).exists())
            self.assertEqual(fail.calls, [["systemctl","--user","disable","--now",UNIT_NAME]])
    def test_linux_missing_ok(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            home.mkdir()
            r = FakeRunner()
            res = uninstall_services(home, "linux", runner=r)
            self.assertEqual(r.calls, [])
            self.assertEqual(len(res), 1)
            self.assertTrue(res[0].startswith("no service installed under"))
    def test_linux_missing_dry_run(self):
        """Dry run reports no service when linux unit is missing."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            home.mkdir()
            r = FakeRunner()
            res = uninstall_services(home, "linux", dry_run=True, runner=r)
            self.assertEqual(r.calls, [])
            self.assertEqual(len(res), 1)
            self.assertTrue(res[0].startswith("no service installed under"))
    def test_linux_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            r = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=r)
            dry = FakeRunner()
            res = uninstall_services(home, "linux", dry_run=True, runner=dry)
            self.assertTrue((home / ".config" / "systemd" / "user" / UNIT_NAME).exists())
            self.assertEqual(dry.calls, [])
            self.assertIsInstance(res, list)
            self.assertGreater(len(res), 0, f"dry-run should return actions: {res!r}")
            self.assertTrue(any(UNIT_NAME in s for s in res), f"dry-run should name {UNIT_NAME}: {res!r}")
            self.assertTrue(any("would" in s.lower() for s in res), f"dry-run should contain would-action: {res!r}")
            self.assertTrue(any(UNIT_NAME in s and "would" in s.lower() for s in res), f"dry-run action must name {UNIT_NAME} with would: {res!r}")
    def test_linux_dry_run_must_name_service(self):
        """Regression: dry-run must be non-empty and name the unit."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            runner = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home, "linux", runner=runner)
            unit_path = home / ".config" / "systemd" / "user" / UNIT_NAME
            self.assertTrue(unit_path.is_file())
            expected_text = render_user_unit(TEMPLATE, repo, "/usr/bin/python3", cfg)
            self.assertEqual(unit_path.read_text(), expected_text)
            real = uninstall_services(home, "linux", dry_run=True, runner=FakeRunner())
            self.assertIsInstance(real, list)
            self.assertGreater(len(real), 0, f"real dry-run should be non-empty: {real!r}")
            self.assertTrue(any(UNIT_NAME in s for s in real), f"real dry-run should name {UNIT_NAME}: {real!r}")
            self.assertTrue(any("would" in s.lower() for s in real), f"real dry-run should contain would: {real!r}")
            self.assertTrue(any(UNIT_NAME in s and "would" in s.lower() for s in real), f"real dry-run action must name {UNIT_NAME} with would: {real!r}")
            self.assertTrue(unit_path.exists())
            self.assertEqual(unit_path.read_text(), expected_text)
    def test_linux_other_home_untouched(self):
        """Uninstall leaves other home's service untouched."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home_a = base / "home_a"
            home_a.mkdir()
            home_b = base / "home_b"
            home_b.mkdir()
            r1 = FakeRunner()
            install_services(repo, "/usr/bin/python3", cfg, home_a, "linux", runner=r1)
            unit_a = home_a / ".config" / "systemd" / "user" / UNIT_NAME
            self.assertTrue(unit_a.exists())
            r2 = FakeRunner()
            res = uninstall_services(home_b, "linux", runner=r2)
            self.assertEqual(r2.calls, [])
            self.assertEqual(len(res), 1)
            self.assertTrue(res[0].startswith("no service installed under"))
            self.assertTrue(unit_a.exists())
    @unittest.skipIf(sys.platform == "win32", "os.getuid not available on Windows")
    def test_darwin_uninstall(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            uid = os.getuid()
            r = FakeRunner()
            with mock.patch("os.getuid", return_value=uid):
                install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=r)
                r.calls.clear()
                res = uninstall_services(home, "darwin", runner=r)
            plist_path = home / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist")
            self.assertFalse(plist_path.exists())
            self.assertTrue(any("bootout" in c for c in r.calls))
            self.assertIsInstance(res, list)
    @unittest.skipIf(sys.platform == "win32", "os.getuid not available on Windows")
    def test_darwin_failed_stop_keeps_plist(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            uid = os.getuid()
            r = FakeRunner()
            with mock.patch("os.getuid", return_value=uid):
                install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=r)
            fail = FakeRunner()
            fail.set(["launchctl","bootout",f"gui/{uid}/{LAUNCHD_LABEL}"], 1, "not running")
            # A successful print means the job remains loaded after bootout fails.
            fail.set(["launchctl","print",f"gui/{uid}/{LAUNCHD_LABEL}"], 0, "loaded job")
            with mock.patch("os.getuid", return_value=uid):
                with self.assertRaisesRegex(ServiceError, "Service stop failed; kept"):
                    uninstall_services(home, "darwin", runner=fail)
            self.assertTrue((home / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist")).exists())
            self.assertEqual(fail.calls, [
                ["launchctl","bootout",f"gui/{uid}/{LAUNCHD_LABEL}"],
                ["launchctl","print",f"gui/{uid}/{LAUNCHD_LABEL}"],
            ])
    @unittest.skipIf(sys.platform == "win32", "os.getuid not available on Windows")
    def test_darwin_uninstall_removes_plist_when_job_absent(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            uid = os.getuid()
            setup_runner = FakeRunner()
            with mock.patch("os.getuid", return_value=uid):
                install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=setup_runner)

            runner = FakeRunner()
            runner.set(["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"], 1, "service not loaded")
            runner.set(["launchctl", "print", f"gui/{uid}/{LAUNCHD_LABEL}"], 1, "could not find service")
            plist_path = home / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist")
            with mock.patch("os.getuid", return_value=uid):
                actions = uninstall_services(home, "darwin", runner=runner)

            self.assertEqual(runner.calls, [
                ["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"],
                ["launchctl", "print", f"gui/{uid}/{LAUNCHD_LABEL}"],
            ])
            self.assertFalse(plist_path.exists())
            self.assertIn("service was not loaded", actions)
            self.assertIn(f"deleted {plist_path}", actions)
    @unittest.skipIf(sys.platform == "win32", "os.getuid not available on Windows")
    def test_darwin_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = _make_repo(base)
            cfg = base / "cfg"
            cfg.mkdir()
            home = base / "home"
            home.mkdir()
            uid = os.getuid()
            r = FakeRunner()
            with mock.patch("os.getuid", return_value=uid):
                install_services(repo, "/usr/bin/python3", cfg, home, "darwin", runner=r)
            dry = FakeRunner()
            with mock.patch("os.getuid", return_value=uid):
                res = uninstall_services(home, "darwin", dry_run=True, runner=dry)
            self.assertTrue((home / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist")).exists())
            self.assertEqual(dry.calls, [])
            self.assertIsInstance(res, list)
    @unittest.skipIf(sys.platform == "win32", "os.getuid not available on Windows")
    def test_darwin_missing_ok(self):
        """No action when darwin plist is missing."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            home.mkdir()
            r = FakeRunner()
            uid = os.getuid()
            with mock.patch("os.getuid", return_value=uid):
                res = uninstall_services(home, "darwin", runner=r)
            self.assertEqual(r.calls, [])
            self.assertEqual(len(res), 1)
            self.assertTrue(res[0].startswith("no service installed under"))

if __name__ == "__main__":
    unittest.main()
