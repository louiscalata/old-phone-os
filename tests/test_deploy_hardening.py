# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail if a deploy hardening directive is removed."""
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
SPDX = "SPDX-License-Identifier: GPL-3.0-or-later"
SERVER = DEPLOY / "opos-server.service"
KIOSK = DEPLOY / "opos-kiosk.service.d" / "10-hardening.conf"
SYSUSERS = DEPLOY / "opos-sysusers.conf"
RULES = DEPLOY / "50-opos-modemmanager.rules"


def read_text(path: Path) -> str:
    """Read a deploy file or fail closed."""
    if path != path.resolve().parent / path.name:
        raise ValueError("bad path")
    return path.read_text(encoding="utf-8")


def parse_unit(text: str) -> dict[str, list[tuple[str, str]]]:
    """Parse a unit file into section -> [(key, value)]."""
    out: dict[str, list[tuple[str, str]]] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise ValueError("empty section")
            out.setdefault(section, [])
            continue
        if not section or "=" not in line:
            raise ValueError("bad unit line")
        key, _, val = line.partition("=")
        if not key.strip():
            raise ValueError("empty key")
        out[section].append((key.strip(), val.strip()))
    return out


def values(parsed: dict[str, list[tuple[str, str]]], sec: str, key: str) -> list[str]:
    """Return all values for a section key."""
    return [v for k, v in parsed.get(sec, []) if k == key]


def has(parsed: dict[str, list[tuple[str, str]]], sec: str, key: str, val: str) -> bool:
    """Check a section holds an exact key=value pair."""
    return val in values(parsed, sec, key)


def first_line(path: Path) -> str:
    """Return first content line after an optional shebang."""
    lines = read_text(path).splitlines()
    if not lines:
        raise AssertionError(f"empty {path.name}")
    if lines[0].startswith("#!"):
        if len(lines) < 2:
            raise AssertionError(f"missing header {path.name}")
        return lines[1]
    return lines[0]


class SpdxTest(unittest.TestCase):
    """Check SPDX headers."""

    def test_spdx(self) -> None:
        """All known deploy files carry the SPDX line."""
        for path in (SERVER, KIOSK, SYSUSERS, RULES):
            with self.subTest(path=path.name):
                self.assertIn(SPDX, first_line(path))

    def test_f007_adb_staging_entrypoint_remains_absent(self) -> None:
        """Removed ADB staging entrypoint stays absent."""
        import tempfile

        def assert_no_staging(root: Path) -> None:
            target = root / "deploy" / "stage-install.sh"
            if target.exists() or target.is_symlink():
                raise AssertionError(f"staging entrypoint present: {target}")

        assert_no_staging(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp)
            (empty / "deploy").mkdir()
            assert_no_staging(empty)
            sentinel = empty / "deploy" / "stage-install.sh"
            sentinel.write_bytes(b"#!/bin/sh\nexit 0\n")
            before = sentinel.read_bytes()
            with self.assertRaises(AssertionError):
                assert_no_staging(empty)
            self.assertEqual(sentinel.read_bytes(), before)


class ServerTest(unittest.TestCase):
    """Check opos-server.service hardening."""

    def setUp(self) -> None:
        """Parse the server unit."""
        self.parsed = parse_unit(read_text(SERVER))

    def test_pairs(self) -> None:
        """Required [Service] pairs are present."""
        for key, val in [("User", "opos"), ("Group", "opos"), ("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"), ("ProtectHome", "yes"), ("PrivateTmp", "yes"), ("PrivateDevices", "yes"), ("MemoryDenyWriteExecute", "yes"), ("IPAddressDeny", "any"), ("LockPersonality", "yes")]:
            with self.subTest(key=key):
                self.assertTrue(has(self.parsed, "Service", key, val))

    def test_families(self) -> None:
        """Address families allow D-Bus/TCP, deny raw/netlink."""
        fams = values(self.parsed, "Service", "RestrictAddressFamilies")
        self.assertTrue(fams)
        toks = " ".join(fams).split()
        self.assertEqual(set(toks), {"AF_UNIX", "AF_INET", "AF_INET6"})

    def test_caps_empty(self) -> None:
        """CapabilityBoundingSet is empty."""
        self.assertIn("", values(self.parsed, "Service", "CapabilityBoundingSet"))

    def test_exec(self) -> None:
        """ExecStart keeps port, no host flag, non-root user."""
        execs = values(self.parsed, "Service", "ExecStart")
        self.assertTrue(execs)
        self.assertIn("--port 8765", execs[0])
        self.assertNotIn("--host", execs[0])
        users = values(self.parsed, "Service", "User")
        self.assertTrue(users)
        for user in users:
            self.assertNotIn(user, ("", "root"))

    def test_install(self) -> None:
        """Unit is wanted by multi-user.target."""
        self.assertTrue(has(self.parsed, "Install", "WantedBy", "multi-user.target"))


class KioskTest(unittest.TestCase):
    """Check kiosk drop-in hardening."""

    def test_pairs(self) -> None:
        """Required drop-in pairs are present."""
        parsed = parse_unit(read_text(KIOSK))
        for key, val in [("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"), ("PrivateTmp", "yes"), ("CapabilityBoundingSet", "")]:
            with self.subTest(key=key):
                self.assertTrue(has(parsed, "Service", key, val))

    def test_paths(self) -> None:
        """ReadWritePaths stays under the kiosk state dir."""
        parsed = parse_unit(read_text(KIOSK))
        paths = values(parsed, "Service", "ReadWritePaths")
        self.assertTrue(paths)
        toks = " ".join(paths).split()
        self.assertTrue(toks)
        for item in toks:
            self.assertTrue(item == "%h/.local/state/opos" or item.startswith("%h/.local/state/opos/"))


class SysusersTest(unittest.TestCase):
    """Check the opos system user entry."""

    def test_entry(self) -> None:
        """opos entry exists with nologin shell."""
        found = False
        for line in read_text(SYSUSERS).splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("u opos ") and s.endswith("/usr/sbin/nologin"):
                found = True
        self.assertTrue(found)


class RulesTest(unittest.TestCase):
    """Check the ModemManager polkit rule."""

    def test_scope(self) -> None:
        """Rule covers messaging/voice for opos only."""
        text = read_text(RULES)
        self.assertIn("org.freedesktop.ModemManager1.Messaging", text)
        self.assertIn("org.freedesktop.ModemManager1.Voice", text)
        self.assertIn('subject.user == "opos"', text)

    def test_allow(self) -> None:
        """Localhost allow stays so deny-any does not break D-Bus/TCP."""
        parsed = parse_unit(read_text(SERVER))
        self.assertTrue(has(parsed, "Service", "IPAddressAllow", "localhost"))

    def test_guarded_yes(self) -> None:
        """Every Result.YES grant sits under an if."""
        lines = read_text(RULES).splitlines()
        self.assertTrue(lines)
        for i, line in enumerate(lines):
            if "polkit.Result.YES" in line:
                window = " ".join(lines[max(0, i - 4):i + 1])
                self.assertIn("if (", window)
                self.assertIn("subject.user", window)
                self.assertIn("action.id", window)


class StagingTest(unittest.TestCase):
    """Guard absent SSH staging entry point."""

    def test_f006_ssh_staging_entrypoint_absent(self) -> None:
        """Staging entry point must stay absent."""
        target = DEPLOY / "stage-install.sh"
        self.assertFalse(os.path.lexists(target), "F006: restored staging requires destination validation and SSH/SCP option delimiters")


if __name__ == "__main__":
    unittest.main()
