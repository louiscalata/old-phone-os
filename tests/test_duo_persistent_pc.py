# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline tests: the only fastboot is a Python file-backed simulator."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'duo_persistent.py'
KEYGEN = shutil.which('ssh-keygen')
FAKE = r'''# SPDX-License-Identifier: GPL-3.0-or-later
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as f:
    f.write(json.dumps(a) + '\n')
if a == ['--version']:
    print('fastboot version 37.0.1'); sys.exit(0)
if a[:2] != ['-s', 'SER1']:
    print('FAILED wrong serial'); sys.exit(0)
a = a[2:]
if a == ['devices']:
    print(os.getenv('FAKE_DEVICES', 'SER1\tfastboot'))
elif a[0] == 'getvar':
    vals = {'model': 'Surface Duo', 'product': 'surfaceduo', 'unlocked': 'yes',
            'current-slot': 'a', 'battery-level': '90', 'partition-size:boot_a': '0x10'}
    print('(bootloader) %s: %s' % (a[1], os.getenv('FAKE_' + a[1].upper().replace('-', '_').replace(':', '_'), vals.get(a[1], ''))), file=sys.stderr)
elif a[0] == 'fetch':
    data = Path(os.environ['FAKE_PART']).read_bytes()
    if os.getenv('FAKE_BAD_READBACK') and Path(os.environ['FAKE_WRITTEN']).exists(): data = b'bad'
    Path(a[2]).write_bytes(data)
elif a[0] == 'flash':
    Path(os.environ['FAKE_PART']).write_bytes(Path(a[2]).read_bytes())
    Path(os.environ['FAKE_WRITTEN']).touch()
    if os.getenv('FAKE_FLASH_FAIL'): print('FAILED simulated error')
else:
    print('FAILED forbidden operation'); sys.exit(1)
'''


@unittest.skipUnless(KEYGEN, 'needs ssh-keygen for real manifest signatures')
class DuoPersistentPC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.key = Path(cls.keys.name) / 'key'
        subprocess.run([KEYGEN, '-q', '-t', 'ed25519', '-N', '', '-f', str(cls.key)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='duo test space ')
        self.addCleanup(self.temp.cleanup)
        self.d = Path(self.temp.name)
        self.fake = self.d / 'fake fastboot.py'; self.fake.write_text(FAKE, encoding='utf-8')
        self.part = self.d / 'device part'; self.original = b'android-original'; self.part.write_bytes(self.original)
        self.boot = self.d / 'boot image'; self.boot.write_bytes(b'kernel' + b'\0' * 10)
        self.rootfs = self.d / 'rootfs image'; self.rootfs.write_bytes(b'rootfs')
        self.manifest = self.d / 'signed manifest'; self.sig = Path(str(self.manifest) + '.sig')
        self.signers = self.d / 'allowed signers'; self.signers.write_text('opos-install ' + Path(str(self.key) + '.pub').read_text())
        self.sign()
        self.backup = self.d / 'backup with spaces'; self.log = self.d / 'calls'; self.written = self.d / 'written'
        self.env = dict(os.environ, FAKE_LOG=str(self.log), FAKE_PART=str(self.part), FAKE_WRITTEN=str(self.written))
        self.common = ['--device', 'SER1', '--slot', 'a', '--fastboot', str(self.fake)]
        self.images = ['--boot', str(self.boot), '--rootfs', str(self.rootfs), '--manifest', str(self.manifest), '--signature', str(self.sig), '--signers', str(self.signers)]

    @staticmethod
    def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()

    def sign(self, crlf=False):
        self.manifest.write_bytes(('boot %s\nrootfs %s\n' % (self.digest(self.boot), self.digest(self.rootfs))).replace('\n', '\r\n' if crlf else '\n').encode())
        self.sig.unlink(missing_ok=True)
        subprocess.run([KEYGEN, '-Y', 'sign', '-f', str(self.key), '-n', 'opos-install', str(self.manifest)], check=True, capture_output=True)

    def call(self, operation, *extra, env=None, attest=True):
        args = [sys.executable, str(TOOL), operation, *self.common]
        if operation != 'preflight': args += ['--backup', str(self.backup)]
        if operation != 'restore': args += self.images
        if operation == 'install' and attest: args += ['--recovery-rehearsed']
        e = dict(self.env); e.update(env or {})
        return subprocess.run([*args, *extra], text=True, capture_output=True, env=e)

    def backup_ok(self):
        r = self.call('backup', '--execute')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_no_flash(self):
        self.assertFalse(any('flash' in c for c in self.calls()))

    def test_dry_runs_print_all_external_commands_without_running(self):
        for op in ('preflight', 'backup', 'install', 'restore'):
            with self.subTest(op=op):
                r = self.call(op)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('DRY RUN', r.stdout); self.assertIn('--version', r.stdout)
                self.assertIn('getvar', r.stdout)
                if op != 'restore': self.assertIn('ssh-keygen', r.stdout)
                if op in ('backup', 'install', 'restore'): self.assertIn('fetch', r.stdout)
                if op in ('install', 'restore'): self.assertIn('flash', r.stdout)
        self.assertFalse(self.log.exists()); self.assertFalse(self.backup.exists()); self.assertEqual(self.part.read_bytes(), self.original)

    def test_preflight_reports_version_and_warning(self):
        r = self.call('preflight', '--execute')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('37.0.1', r.stdout); self.assertIn('bad_alloc', r.stdout)
        self.assertIn('NOT VERIFIED', r.stdout)

    def test_serial_is_required_and_validated_before_commands(self):
        r = subprocess.run([sys.executable, str(TOOL), 'restore', '--execute', '--slot', 'a', '--backup', str(self.backup), '--fastboot', str(self.fake)], text=True, capture_output=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)
        r = self.call('restore', '--execute', '--device', 'SER1\nOTHER')
        self.assertNotEqual(r.returncode, 0); self.assertIn('invalid device serial', r.stderr)
        self.assertFalse(self.log.exists())

    def test_recovery_attestation_required_before_external_calls(self):
        r = self.call('install', '--execute', attest=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn('rehearsed', r.stderr); self.assertFalse(self.log.exists())

    def test_install_and_restore_require_verified_backup(self):
        for op in ('install', 'restore'):
            with self.subTest(op=op):
                r = self.call(op, '--execute'); self.assertNotEqual(r.returncode, 0)
        self.assert_no_flash()

    def test_corrupt_backup_refused_by_both_writers(self):
        self.backup_ok(); (self.backup / 'boot_a.img').write_bytes(b'x' * 16)
        for op in ('install', 'restore'):
            with self.subTest(op=op): self.assertNotEqual(self.call(op, '--execute').returncode, 0)
        self.assert_no_flash()

    def test_backup_manifest_identity_size_and_hash_refused(self):
        self.backup_ok(); manifest = self.backup / 'manifest'; good = manifest.read_text()
        for bad in (good.replace('SER1', 'OTHER'), good.replace('boot_a', 'boot_b'), good.replace('size 16', 'size 15'), good.replace('sha256 ', 'sha256 0')):
            with self.subTest(bad=bad):
                manifest.write_text(bad)
                self.assertNotEqual(self.call('restore', '--execute').returncode, 0)
        self.assert_no_flash()

    def test_identity_and_battery_refusals(self):
        self.backup_ok()
        for env in ({'FAKE_DEVICES': ''}, {'FAKE_DEVICES': 'SER1\tfastboot\nSER1\tfastboot'}, {'FAKE_PRODUCT': 'wrong'}, {'FAKE_UNLOCKED': 'no'}, {'FAKE_CURRENT_SLOT': 'b'}, {'FAKE_BATTERY_LEVEL': '19'}, {'FAKE_BATTERY_LEVEL': 'unknown'}, {'FAKE_PARTITION_SIZE_BOOT_A': '0xinvalid'}):
            with self.subTest(env=env): self.assertNotEqual(self.call('install', '--execute', env=env).returncode, 0)
        self.assertNotEqual(self.call('install', '--execute', '--device', 'OTHER').returncode, 0)
        self.assert_no_flash()

    def test_bad_signature_and_changed_signed_hash_refused(self):
        self.backup_ok(); self.sig.write_text('invalid')
        self.assertNotEqual(self.call('install', '--execute').returncode, 0)
        self.sign(); self.rootfs.write_bytes(b'changed')
        self.assertNotEqual(self.call('install', '--execute').returncode, 0)
        self.assert_no_flash()

    def test_wrong_boot_size_refused(self):
        self.backup_ok(); self.boot.write_bytes(b'short'); self.sign()
        self.assertNotEqual(self.call('install', '--execute').returncode, 0); self.assert_no_flash()

    def test_changed_partition_refused(self):
        self.backup_ok(); self.part.write_bytes(b'z' * 16)
        self.assertNotEqual(self.call('install', '--execute').returncode, 0); self.assert_no_flash()

    def test_install_restore_readbacks_and_paths_with_spaces(self):
        self.backup_ok()
        for op, expected in (('install', self.boot.read_bytes()), ('restore', self.original)):
            r = self.call(op, '--execute'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.part.read_bytes(), expected)
        calls = self.calls()
        self.assertEqual(sum('flash' in c for c in calls), 2)
        self.assertEqual(sum('fetch' in c for c in calls), 4)
        self.assertFalse(any('erase' in c or any(x.startswith('format') for x in c) for c in calls))
        self.assertTrue(any(str(self.backup / 'boot_a.img') in c for c in calls))

    def test_windows_style_paths_are_passed_as_single_arguments(self):
        # On Windows these are genuine drive-qualified temporary paths. On POSIX
        # the literal backslashes and drive prefix also exercise argument fidelity.
        name = 'Windows owner folder' if os.name == 'nt' else r'C:\Users\Owner Name\OPOS files'
        folder = self.d / name; folder.mkdir()
        self.boot = folder / 'boot image.img'; self.boot.write_bytes(b'kernel' + b'\0' * 10)
        self.rootfs = folder / 'rootfs image.img'; self.rootfs.write_bytes(b'rootfs')
        self.backup = folder / 'backup folder'
        self.images = ['--boot', str(self.boot), '--rootfs', str(self.rootfs), '--manifest', str(self.manifest), '--signature', str(self.sig), '--signers', str(self.signers)]
        self.sign(); self.backup_ok()
        r = self.call('install', '--execute'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        flash = next(c for c in self.calls() if 'flash' in c)
        self.assertEqual(flash[-1], str(self.boot))
        self.assertTrue(any(str(self.backup / 'boot_a.img') in c for c in self.calls()))

    def test_crlf_signed_and_backup_manifests(self):
        self.sign(crlf=True); self.backup_ok()
        manifest = self.backup / 'manifest'; manifest.write_bytes(manifest.read_bytes().replace(b'\n', b'\r\n'))
        r = self.call('install', '--execute'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.call('restore', '--execute'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_failed_flash_still_attempts_readback(self):
        self.backup_ok(); r = self.call('install', '--execute', env={'FAKE_FLASH_FAIL': '1'})
        self.assertNotEqual(r.returncode, 0); self.assertIn('do not reboot', r.stderr)
        ops = [c[2] for c in self.calls() if len(c) > 2]
        self.assertEqual(ops[-2:], ['flash', 'fetch'])

    def test_bad_postwrite_readback_fails(self):
        self.backup_ok(); r = self.call('install', '--execute', env={'FAKE_BAD_READBACK': '1'})
        self.assertNotEqual(r.returncode, 0); self.assertIn('readback failed', r.stderr)

    def test_existing_backup_and_readback_paths_refused(self):
        self.backup_ok(); self.assertNotEqual(self.call('backup', '--execute').returncode, 0)
        (self.backup / '.duo-restore-readback').write_text('keep')
        self.assertNotEqual(self.call('restore', '--execute').returncode, 0); self.assert_no_flash()

    def test_refuses_backup_inside_git_worktree(self):
        self.backup = ROOT / ('duo-test-backup-' + self.d.name)
        r = self.call('backup', '--execute')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('outside a Git worktree', r.stderr)
        self.assertFalse(self.backup.exists())
        self.assert_no_flash()

    def test_missing_image_files_are_refused(self):
        self.backup_ok(); self.sig.unlink()
        r = self.call('install', '--execute')
        self.assertNotEqual(r.returncode, 0); self.assertIn('missing file', r.stderr)
        self.assert_no_flash()

    def test_missing_ssh_keygen_clear_error(self):
        r = self.call('preflight', '--execute', env={'PATH': str(self.d)})
        self.assertNotEqual(r.returncode, 0); self.assertIn('ssh-keygen is missing', r.stderr)


if __name__ == '__main__': unittest.main()
