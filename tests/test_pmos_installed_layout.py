# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise package functions and state checks in unprivileged temporary trees."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packaging/pmos/opos"
HELPER = PACKAGE / "opos-systemd-state.py"


def stage_package(destination):
    """Run the real recipe with a DESTDIR-only GNU install(1) adapter on macOS."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    inputs = destination / "inputs"
    inputs.mkdir()
    for source in PACKAGE.iterdir():
        if source.is_file():
            shutil.copyfile(source, inputs / source.name)
    for name in ("opos-sysusers.conf",):
        shutil.copyfile(ROOT / "deploy" / name, inputs / name)
    for name in ("opos-firstboot.sh", "opos-kiosk-off.sh"):
        shutil.copyfile(ROOT / "packaging/pmos/easy" / name, inputs / name)
    adapter = destination / "install_adapter.py"
    # Only install(1)'s -Dm file copy is adapted. The package functions are real.
    adapter.write_text(
        "import os, pathlib, shutil, sys\n"
        "option, source, target = sys.argv[1:]\n"
        "assert option.startswith('-Dm')\n"
        "target = pathlib.Path(target)\n"
        "assert target.resolve().is_relative_to(pathlib.Path(os.environ['TEST_DESTDIR']).resolve())\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "shutil.copyfile(source, target)\n"
        "target.chmod(int(option[3:], 8))\n",
        encoding="utf-8",
    )
    package, systemd = destination / "package", destination / "systemd"
    script = r'''
set -eu
adapter_python=$6
adapter=$7
install() { "$adapter_python" "$adapter" "$@"; }
srcdir=$2
pkgdir=$4
subpkgdir=$5
subpkgname=opos-systemd
. "$1"
builddir=$3
package
_systemd
'''
    result = subprocess.run(
        ["sh", "-c", script, "package-layout", str(PACKAGE / "APKBUILD"),
         str(inputs), str(ROOT), str(package), str(systemd), sys.executable,
         str(adapter)], cwd=ROOT, env=dict(os.environ, TEST_DESTDIR=str(destination)),
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return package, systemd


class InstalledPackageLayoutTests(unittest.TestCase):
    def test_actual_package_functions_ship_units_and_state_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, systemd = stage_package(temporary)
            for name in ("opos-server.service", "opos-firstboot.service"):
                with self.subTest(unit=name):
                    target = systemd / "usr/lib/systemd/system" / name
                    self.assertTrue(target.is_file(), name + " is not installed")
                    self.assertEqual(target.read_bytes(), (PACKAGE / name).read_bytes())
                    self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            target = package / "usr/libexec/opos-systemd-state"
            self.assertTrue(target.is_file(), "state helper is not installed")
            self.assertEqual(target.read_bytes(), HELPER.read_bytes())
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertTrue((package / "etc/init.d/opos-server").is_file())
            self.assertTrue((package / "usr/bin/opos-firstboot").is_file())
            self.assertFalse((package / "usr/share/polkit-1/rules.d").exists())


class SystemdStateLayoutTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HELPER.is_file(), "package state helper is missing")
        specification = importlib.util.spec_from_file_location("opos_package_state", HELPER)
        self.helper = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.helper)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "opos"
        self.owner = (os.getuid(), os.getgid())

    def prepare(self, readable=None):
        return self.helper.prepare_state(self.home, self.owner, root_owner=self.owner,
                                         readable=readable or (lambda path: os.access(path, os.R_OK)))

    def check(self, readable=None):
        return self.helper.check_state(self.home, self.owner, root_owner=self.owner,
                                       readable=readable or (lambda path: os.access(path, os.R_OK)))

    def test_prepare_creates_expected_directories_without_prefs_or_marker(self):
        self.prepare()
        for relative, mode in (("", 0o755), (".config", 0o755), (".config/opos", 0o750)):
            self.assertEqual((self.home / relative).stat().st_mode & 0o777, mode)
        self.assertFalse((self.home / "firstboot-done").exists())
        self.assertFalse((self.home / ".config/opos/prefs.json").exists())
        self.check()

    def test_check_does_not_create_missing_state(self):
        with self.assertRaises(self.helper.StateError):
            self.check()
        self.assertFalse(self.home.exists())

    def test_existing_prefs_and_root_marker_are_preserved(self):
        self.prepare()
        prefs, marker = self.home / ".config/opos/prefs.json", self.home / "firstboot-done"
        prefs.write_bytes(b'{"theme":"dark"}\n')
        prefs.chmod(0o600)
        marker.write_bytes(b"existing marker\n")
        marker.chmod(0o644)
        before = {path: (path.read_bytes(), path.stat().st_mode) for path in (prefs, marker)}
        self.prepare()
        self.check()
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mode) for path in before})

    def test_unsafe_directory_types_are_rejected_without_following(self):
        for relative in ("", ".config", ".config/opos"):
            for kind in ("symlink", "dangling", "file", "fifo"):
                with self.subTest(path=relative, kind=kind), tempfile.TemporaryDirectory() as temporary:
                    home = Path(temporary) / "opos"
                    target = home / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    for parent in (home, home / ".config"):
                        if parent.is_dir():
                            parent.chmod(0o755)
                    outside = Path(temporary) / "outside"
                    outside.mkdir()
                    sentinel = outside / "sentinel"
                    sentinel.write_bytes(b"unchanged")
                    if kind == "symlink":
                        target.symlink_to(outside, target_is_directory=True)
                    elif kind == "dangling":
                        target.symlink_to(outside / "absent")
                    elif kind == "file":
                        target.write_bytes(b"preserve")
                    else:
                        os.mkfifo(target)
                    with self.assertRaises(self.helper.StateError):
                        self.helper.prepare_state(home, self.owner, root_owner=self.owner,
                                                  readable=lambda path: True)
                    self.assertEqual(sentinel.read_bytes(), b"unchanged")
                    self.assertEqual(sorted(path.name for path in outside.iterdir()), ["sentinel"])

    def test_wrong_directory_mode_or_owner_is_rejected_without_repair(self):
        self.prepare()
        with self.assertRaises(self.helper.StateError):
            self.helper.check_state(self.home, self.owner,
                                    root_owner=(self.owner[0] + 1, self.owner[1]),
                                    readable=lambda path: True)
        with self.assertRaises(self.helper.StateError):
            self.helper.check_state(self.home, (self.owner[0] + 1, self.owner[1]),
                                    root_owner=self.owner, readable=lambda path: True)
        parent = self.home / ".config"
        parent.chmod(0o777)
        with self.assertRaises(self.helper.StateError):
            self.prepare()
        self.assertEqual(parent.stat().st_mode & 0o777, 0o777)

    def test_prefs_symlink_nonregular_and_unreadable_are_rejected(self):
        self.prepare()
        prefs = self.home / ".config/opos/prefs.json"
        outside = Path(self.temporary.name) / "private"
        outside.write_bytes(b"keep exact bytes")
        prefs.symlink_to(outside)
        calls = []
        with self.assertRaises(self.helper.StateError):
            self.prepare(readable=lambda path: calls.append(path) or True)
        self.assertEqual(calls, [])
        prefs.unlink()
        prefs.mkdir()
        with self.assertRaises(self.helper.StateError):
            self.prepare()
        prefs.rmdir()
        prefs.write_bytes(b"preserve prefs")
        prefs.chmod(0o600)
        before = prefs.stat()
        with self.assertRaises(self.helper.StateError):
            self.prepare(readable=lambda path: False)
        self.assertEqual(prefs.read_bytes(), b"preserve prefs")
        self.assertEqual((prefs.stat().st_uid, prefs.stat().st_gid, prefs.stat().st_mode),
                         (before.st_uid, before.st_gid, before.st_mode))
        self.assertEqual(outside.read_bytes(), b"keep exact bytes")

    def test_symlink_marker_is_rejected_without_touching_target(self):
        self.prepare()
        outside = Path(self.temporary.name) / "marker-target"
        outside.write_bytes(b"keep marker target")
        (self.home / "firstboot-done").symlink_to(outside)
        with self.assertRaises(self.helper.StateError):
            self.prepare()
        self.assertEqual(outside.read_bytes(), b"keep marker target")
