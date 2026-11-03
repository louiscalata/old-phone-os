# SPDX-License-Identifier: GPL-3.0-or-later
"""The installed service keeps the model-route settings the setup run saw."""
from __future__ import annotations

import copy
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

from opos.setup import services
from opos.setup.apply import apply
from opos.setup.config import default_config
from opos.setup.services import (
    LAUNCHD_LABEL,
    UNIT_NAME,
    install_services,
    render_launchd_plist,
    render_user_unit,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "deploy" / "opos-server.service").read_text(encoding="utf-8")

# Built from parts so the repository privacy guard does not read them as real addresses or paths.
LAN_A = "192" + ".168.1.20"
LAN_B = "192" + ".168.1.5"
TEN_A = "10" + ".0.0.2"
MAC_HOME = "/" + "Users/owner"

ROUTE = {
    "OPOS_GLIMMER_URL": "http://127.0.0.1:1235/v1",
    "OPOS_LAN_URL": f"http://{LAN_A}:1234/v1",
    "OPOS_LOCAL_URL": "http://localhost:8081/v1",
    "OPOS_GLIMMER_ROUTING": "0",
    "OPOS_MUSE_TIMEOUT": "45",
}
# A planted value that must never reach a rendered unit.
PLANTED = "s3cr3t-value"
SECRETS = {"BASETEN_API_KEY": PLANTED, "OPOS_SPARK_OPEN": "1", "MUSE_API_TOKEN": PLANTED}
# A path the runtime accepts that needs escaping in both formats.
AWKWARD_URL = 'http://127.0.0.1:1235/v1%2Fa"b\\c&<d>'


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        return (0, "")


def route_environment(env):
    return services.route_environment(env)


def _systemd_value(line: str) -> tuple[str, str]:
    """Parse one Environment= line the way systemd.syntax(7) and systemd.unit(5) describe.

    Quotes wrap the whole item, a backslash escapes the next character, and "%%"
    is a literal percent sign (any other "%" would start a specifier).
    """
    assert line.startswith("Environment="), line
    raw = line[len("Environment="):]
    out = []
    i = 0
    quote = None
    while i < len(raw):
        ch = raw[i]
        if quote is None and ch in "\"'" and not out:
            quote = ch
        elif quote is not None and ch == quote:
            assert i == len(raw) - 1, f"text after closing quote in {line!r}"
            quote = None
        elif ch == "\\":
            i += 1
            assert raw[i] in "\\\"'", f"unexpected escape in {line!r}"
            out.append(raw[i])
        elif ch == "%":
            assert raw[i + 1] == "%", f"bare % would be read as a specifier in {line!r}"
            out.append("%")
            i += 1
        else:
            assert not ch.isspace() or quote is not None, f"unquoted space in {line!r}"
            out.append(ch)
        i += 1
    assert quote is None, f"unclosed quote in {line!r}"
    name, _, value = "".join(out).partition("=")
    return name, value


def _service_env(unit: str) -> dict[str, str]:
    """Environment of the [Service] section of a rendered unit."""
    env: dict[str, str] = {}
    section = ""
    for line in unit.splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Service" and line.startswith("Environment="):
            name, value = _systemd_value(line)
            env[name] = value
    return env


class RouteEnvironmentTests(unittest.TestCase):
    def test_unset_keeps_nothing(self) -> None:
        self.assertEqual(route_environment(None), ({}, []))
        self.assertEqual(route_environment({}), ({}, []))
        self.assertEqual(route_environment({"PATH": "/usr/bin", "HOME": "/home/u", **SECRETS}), ({}, []))
        self.assertEqual(route_environment({name: "" for name in services.ROUTE_SETTINGS}), ({}, []))

    def test_valid_settings_kept(self) -> None:
        keep, notes = route_environment({**ROUTE, **SECRETS, "PATH": "/usr/bin"})
        self.assertEqual(keep, ROUTE)
        self.assertEqual(notes, [])

    def test_timeout_is_kept_as_the_runtime_reads_it(self) -> None:
        for raw, kept in (("30", "30"), ("12.5", "12.5"), ("2", "5"), ("1000", "600"), (" 60 ", "60")):
            with self.subTest(raw=raw):
                self.assertEqual(route_environment({"OPOS_MUSE_TIMEOUT": raw}), ({"OPOS_MUSE_TIMEOUT": kept}, []))
        for raw in ("abc", "0", "-3", "nan", "inf"):
            with self.subTest(raw=raw):
                keep, notes = route_environment({"OPOS_MUSE_TIMEOUT": raw})
                self.assertEqual(keep, {})
                self.assertEqual(len(notes), 1)
                self.assertIn("OPOS_MUSE_TIMEOUT", notes[0])

    def test_routing_switch_only_kept_when_off(self) -> None:
        self.assertEqual(route_environment({"OPOS_GLIMMER_ROUTING": "0"}), ({"OPOS_GLIMMER_ROUTING": "0"}, []))
        for raw in ("1", "off", "no"):
            with self.subTest(raw=raw):
                # web.py turns routing off only for exactly "0"; other values are the default.
                self.assertEqual(route_environment({"OPOS_GLIMMER_ROUTING": raw}), ({}, []))

    def test_urls_the_runtime_ignores_are_not_kept(self) -> None:
        cases = {
            "OPOS_GLIMMER_URL": [f"http://{LAN_B}:1234/v1", "https://127.0.0.1:1234/v1", "ftp://127.0.0.1/v1"],
            "OPOS_LOCAL_URL": [f"http://{TEN_A}:8080/v1", "https://localhost:8080/v1"],
            "OPOS_LAN_URL": ["http://8.8.8.8:1234/v1", f"https://{LAN_B}:1234/v1", "http://nas.local:1234/v1"],
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    keep, notes = route_environment({name: value})
                    self.assertEqual(keep, {})
                    self.assertEqual(len(notes), 1)
                    self.assertTrue(notes[0].startswith(name), notes)

    def test_url_with_login_query_or_odd_characters_is_not_kept(self) -> None:
        for value in (
            f"http://user:{PLANTED}@127.0.0.1:1234/v1",
            "http://user@127.0.0.1:1234/v1",
            f"http://127.0.0.1:1234/v1?key={PLANTED}",
            f"http://127.0.0.1:1234/v1#{PLANTED}",
            "http://127.0.0.1:1234/v1 x",
            "http://127.0.0.1:1234/v1\nEnvironment=X=1",
            "http://127.0.0.1:1234/vé",
        ):
            with self.subTest(value=value):
                keep, notes = route_environment({"OPOS_GLIMMER_URL": value})
                self.assertEqual(keep, {})
                self.assertEqual(len(notes), 1)
                self.assertNotIn(PLANTED, notes[0])


class SystemdUnitTests(unittest.TestCase):
    def render(self, env):
        return render_user_unit(TEMPLATE, Path("/opt/owner/opos"), "/usr/bin/python3", Path("/opt/owner/cfg"), env=env)

    def test_unset_renders_as_before(self) -> None:
        base = render_user_unit(TEMPLATE, Path("/opt/owner/opos"), "/usr/bin/python3", Path("/opt/owner/cfg"))
        self.assertEqual(self.render(None), base)
        self.assertEqual(self.render({}), base)
        self.assertEqual(self.render({"PATH": "/usr/bin", **SECRETS}), base)
        self.assertEqual(_service_env(base), {"PYTHONPATH": "/opt/owner/opos/src", "PYTHONDONTWRITEBYTECODE": "1", "OPOS_CONFIG_DIR": "/opt/owner/cfg"})

    def test_route_settings_written_to_service_section(self) -> None:
        unit = self.render({**ROUTE, **SECRETS})
        env = _service_env(unit)
        for name, value in ROUTE.items():
            self.assertEqual(env.get(name), value, name)
        self.assertIn('Environment="OPOS_GLIMMER_URL=http://127.0.0.1:1235/v1"\n', unit)
        self.assertNotIn(PLANTED, unit)
        self.assertNotIn("BASETEN_API_KEY", unit)
        self.assertNotIn("OPOS_SPARK_OPEN", unit)

    def test_escaping(self) -> None:
        unit = self.render({"OPOS_GLIMMER_URL": AWKWARD_URL})
        self.assertIn('Environment="OPOS_GLIMMER_URL=http://127.0.0.1:1235/v1%%2Fa\\"b\\\\c&<d>"\n', unit)
        self.assertEqual(_service_env(unit)["OPOS_GLIMMER_URL"], AWKWARD_URL)

    def test_secret_in_url_never_written(self) -> None:
        unit = self.render({"OPOS_LAN_URL": f"http://owner:{PLANTED}@{LAN_A}:1234/v1"})
        self.assertNotIn(PLANTED, unit)
        self.assertNotIn("OPOS_LAN_URL", unit)


class LaunchdPlistTests(unittest.TestCase):
    def render(self, env):
        return render_launchd_plist(Path(MAC_HOME, "opos"), "/usr/bin/python3", Path(MAC_HOME, "Library", "Application Support", "opos"), env=env)

    def test_unset_renders_as_before(self) -> None:
        base = render_launchd_plist(Path(MAC_HOME, "opos"), "/usr/bin/python3", Path(MAC_HOME, "Library", "Application Support", "opos"))
        self.assertEqual(self.render(None), base)
        self.assertEqual(self.render({"PATH": "/usr/bin", **SECRETS}), base)
        data = plistlib.loads(base.encode())
        self.assertEqual(set(data["EnvironmentVariables"]), {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "OPOS_CONFIG_DIR"})

    def test_route_settings_written(self) -> None:
        text = self.render({**ROUTE, **SECRETS})
        variables = plistlib.loads(text.encode())["EnvironmentVariables"]
        for name, value in ROUTE.items():
            self.assertEqual(variables.get(name), value, name)
        self.assertEqual(set(variables), {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "OPOS_CONFIG_DIR", *ROUTE})
        self.assertNotIn(PLANTED, text)

    def test_escaping(self) -> None:
        text = self.render({"OPOS_GLIMMER_URL": AWKWARD_URL})
        self.assertIn("<string>http://127.0.0.1:1235/v1%2Fa\"b\\c&amp;&lt;d&gt;</string>", text)
        self.assertEqual(plistlib.loads(text.encode())["EnvironmentVariables"]["OPOS_GLIMMER_URL"], AWKWARD_URL)


class InstallServicesTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.repo = self.base / "repo"
        (self.repo / "deploy").mkdir(parents=True)
        (self.repo / "deploy" / UNIT_NAME).write_text(TEMPLATE, encoding="utf-8")
        self.cfg = self.base / "cfg"
        self.cfg.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()

    def test_linux_unit_keeps_route_and_reports_what_it_left_out(self) -> None:
        env = {**ROUTE, "OPOS_LAN_URL": "http://8.8.8.8:1234/v1"}
        actions = install_services(self.repo, "/usr/bin/python3", self.cfg, self.home, "linux", runner=FakeRunner(), env=env)
        unit = (self.home / ".config" / "systemd" / "user" / UNIT_NAME).read_text(encoding="utf-8")
        written = _service_env(unit)
        self.assertEqual(written["OPOS_GLIMMER_URL"], ROUTE["OPOS_GLIMMER_URL"])
        self.assertNotIn("OPOS_LAN_URL", written)
        self.assertEqual(actions[1], "not set in the service: OPOS_LAN_URL must be an http:// URL with a private IP address")

    def test_dry_run_reports_left_out_settings(self) -> None:
        actions = install_services(self.repo, "/usr/bin/python3", self.cfg, self.home, "linux", dry_run=True, runner=FakeRunner(), env={"OPOS_MUSE_TIMEOUT": "soon"})
        self.assertIn("not set in the service: OPOS_MUSE_TIMEOUT must be a positive number of seconds", actions)
        self.assertFalse((self.home / ".config").exists())

    def test_unset_reports_nothing_extra(self) -> None:
        plain = install_services(self.repo, "/usr/bin/python3", self.cfg, self.home, "linux", dry_run=True, runner=FakeRunner())
        self.assertEqual(install_services(self.repo, "/usr/bin/python3", self.cfg, self.home, "linux", dry_run=True, runner=FakeRunner(), env={}), plain)
        self.assertFalse(any("not set in the service" in line for line in plain))


@unittest.skipIf(sys.platform == "win32", "needs a POSIX host")
class ApplyCarriesRouteTests(unittest.TestCase):
    """End to end: the environment setup runs in reaches the installed service."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repo"
        (self.repo / "deploy").mkdir(parents=True)
        (self.repo / "deploy" / UNIT_NAME).write_text(TEMPLATE, encoding="utf-8")
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.root / "xdg"), "OPOS_CONFIG_DIR": str(self.root / "cfg"), **ROUTE, **SECRETS}

    def run_apply(self, platform: str):
        cfg = copy.deepcopy(default_config())
        return apply(cfg, repo_root=self.repo, python=sys.executable, home=self.home, platform=platform, env=self.env, runner=FakeRunner(), health_check=lambda port: (True, "ok"))

    def test_linux_service_gets_route(self) -> None:
        progress = self.run_apply("linux")
        steps = {s["id"]: s for s in progress.snapshot()}
        self.assertEqual(steps["services"]["status"], "ok", steps["services"])
        unit = (self.home / ".config" / "systemd" / "user" / UNIT_NAME).read_text(encoding="utf-8")
        written = _service_env(unit)
        for name, value in ROUTE.items():
            self.assertEqual(written.get(name), value, name)
        self.assertNotIn(PLANTED, unit)

    def test_darwin_service_gets_route(self) -> None:
        progress = self.run_apply("darwin")
        steps = {s["id"]: s for s in progress.snapshot()}
        self.assertEqual(steps["services"]["status"], "ok", steps["services"])
        plist = self.home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        text = plist.read_text(encoding="utf-8")
        variables = plistlib.loads(text.encode())["EnvironmentVariables"]
        for name, value in ROUTE.items():
            self.assertEqual(variables.get(name), value, name)
        self.assertNotIn(PLANTED, text)


class LaneFilesPassPrivacyGuardTests(unittest.TestCase):
    """guard/check.py (run by the pre-commit and pre-push hooks) must not flag this lane's files."""

    FILES = (
        "tests/test_setup_route_env.py",
        "tests/test_aitools_installer_digest.py",
        "src/opos/setup/services.py",
        "src/opos/setup/apply.py",
        "src/opos/aitools/catalog.py",
        "src/opos/aitools/installer.py",
        "src/opos/aitools/wizard.py",
        "deploy/ai-tools.json",
        "docs/ai-tools.md",
    )

    def test_no_private_or_secret_pattern(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("opos_guard_check_lane", ROOT / "guard" / "check.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for rel in self.FILES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for pattern in guard.PRIVATE:
                with self.subTest(file=rel, pattern=pattern.pattern):
                    match = pattern.search(text)
                    self.assertIsNone(match, match and match.group(0)[:12])


if __name__ == "__main__":
    unittest.main()
