# SPDX-License-Identifier: GPL-3.0-or-later
"""Proposed PC-side Duo boot-slot tooling. No phone operation runs by default."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


class Stop(Exception):
    pass


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def display(args, stdin=None):
    command = subprocess.list2cmdline([str(a) for a in args]) if os.name == 'nt' else shlex.join([str(a) for a in args])
    source = (' [stdin: ' + repr(str(stdin)) + ']') if stdin else ''
    print('+ ' + command + source, flush=True)


def command(args, stdin=None, fastboot=False, input_bytes=None):
    display(args, stdin)
    try:
        result = subprocess.run([str(a) for a in args], input=input_bytes if input_bytes is not None else (Path(stdin).read_bytes() if stdin else None),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120, check=False)
    except FileNotFoundError as exc:
        raise Stop('ssh-keygen is missing; install OpenSSH or put ssh-keygen on PATH' if not fastboot else 'fastboot executable not found; install it or pass --fastboot') from exc
    except subprocess.TimeoutExpired as exc:
        raise Stop('command timed out; stop and investigate') from exc
    output = result.stdout.decode('utf-8', errors='replace')
    if output:
        print(output, end='' if output.endswith('\n') else '\n', flush=True)
    if result.returncode or (fastboot and re.search(r'\bFAILED\b|remote:\s*(?:failure|error)', output, re.I)):
        raise Stop('fastboot reported failure' if fastboot else 'signature verification failed')
    return output


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='operation', required=True)
    for op in ('preflight', 'backup', 'install', 'restore'):
        s = subs.add_parser(op)
        s.add_argument('--execute', action='store_true', help='actually perform the listed operations')
        s.add_argument('--device', required=True, help='exact fastboot serial')
        s.add_argument('--slot', choices=('a', 'b'), required=True)
        s.add_argument('--fastboot', default='fastboot', help='fastboot executable on PATH or full path')
        if op != 'preflight':
            s.add_argument('--backup', type=Path, required=True)
        if op != 'restore':
            for name in ('boot', 'rootfs', 'manifest', 'signature', 'signers'):
                s.add_argument('--' + name, type=Path, required=True)
        if op == 'install':
            s.add_argument('--recovery-rehearsed', action='store_true')
    return p


class Tool:
    def __init__(self, a):
        self.a = a
        if not re.fullmatch(r'[A-Za-z0-9._-]+', a.device, flags=re.ASCII):
            raise Stop('invalid device serial')
        self.part = 'boot_' + a.slot
        # A Python fake is useful for offline tests, including on Windows.
        self.fbexe = [sys.executable, a.fastboot] if a.fastboot.lower().endswith('.py') else [a.fastboot]
        self.fbbase = self.fbexe + ['-s', a.device]
        self.vars = ['model', 'product', 'unlocked', 'current-slot', 'battery-level', 'partition-size:' + self.part]
        self.sigcmd = ['ssh-keygen', '-Y', 'verify', '-f', str(getattr(a, 'signers', '')), '-I', 'opos-install', '-n', 'opos-install', '-s', str(getattr(a, 'signature', ''))]
        if a.operation != 'preflight':
            self.image = a.backup / (self.part + '.img')
            self.tmp = a.backup / ('.duo-' + a.operation + '-readback')

    def fb(self, *args):
        return command(self.fbbase + list(args), fastboot=True)

    def planned(self):
        print('DRY RUN: no external command executed and no files created.')
        display(self.fbexe + ['--version'])
        display(self.fbbase + ['devices'])
        for v in self.vars:
            display(self.fbbase + ['getvar', v])
        if self.a.operation != 'restore':
            display(self.sigcmd, self.a.manifest)
            print('Python checks the signed manifest, image hashes and boot size locally.')
        if self.a.operation == 'backup':
            display(self.fbbase + ['fetch', self.part, str(self.image)])
        if self.a.operation in ('install', 'restore'):
            print('Python verifies the backup manifest and its full image locally.')
            if self.a.operation == 'install':
                display(self.fbbase + ['fetch', self.part, str(self.tmp)])
            print('Would overwrite device %s partition %s after all checks.' % (self.a.device, self.part))
            target = self.a.boot if self.a.operation == 'install' else self.image
            display(self.fbbase + ['flash', self.part, str(target)])
            display(self.fbbase + ['fetch', self.part, str(self.tmp)])
        self.warn()

    @staticmethod
    def warn():
        print('WARNING: Windows fastboot 37.0.1 reportedly crashes with bad_alloc on a 5.7 GB raw ext4 image; see docs/duo-usb-install.md. This tool does not flash the rootfs.')

    def identity(self):
        command(self.fbexe + ['--version'], fastboot=True)
        self.warn()
        lines = self.fb('devices').splitlines()
        if sum(line.split()[:2] == [self.a.device, 'fastboot'] for line in lines) != 1:
            raise Stop('named serial is absent or ambiguous')
        values = {}
        for name in self.vars:
            out = self.fb('getvar', name)
            matches = re.findall(r'^(?:\(bootloader\)\s*)?' + re.escape(name) + r':\s*(.*?)\s*$', out, re.M)
            if len(matches) != 1 or not matches[0]:
                raise Stop('missing or ambiguous fastboot getvar ' + name)
            values[name] = matches[0]
        if values['product'] != 'surfaceduo':
            raise Stop('product is not surfaceduo')
        if values['unlocked'] != 'yes':
            raise Stop('bootloader is not reported unlocked')
        if values['current-slot'] != self.a.slot:
            raise Stop('current slot differs from --slot')
        battery = values['battery-level']
        if not re.fullmatch(r'[0-9]+', battery) or not 20 <= int(battery) <= 100:
            raise Stop('battery level below 20 percent or invalid')
        size = values['partition-size:' + self.part]
        if not re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)', size):
            raise Stop('invalid partition size')
        self.size = int(size, 16 if size.lower().startswith('0x') else 10)
        if self.size <= 0:
            raise Stop('invalid partition size')

    def images(self):
        a = self.a
        for f in (a.boot, a.rootfs, a.manifest, a.signature, a.signers):
            if not f.is_file():
                raise Stop('missing file: ' + str(f))
        manifest_bytes = a.manifest.read_bytes()
        command(self.sigcmd, a.manifest, input_bytes=manifest_bytes)
        # Parse exactly the verified bytes; splitlines accepts Windows CRLF.
        expected = ['boot ' + digest(a.boot), 'rootfs ' + digest(a.rootfs)]
        if manifest_bytes.decode('utf-8').splitlines() != expected:
            raise Stop('signed manifest does not match image hashes')
        if a.boot.stat().st_size != self.size:
            raise Stop('signed boot image must equal the complete partition size')
        print('PASS: signature and image hashes; filesystem content and image compatibility NOT VERIFIED')

    def backup_check(self):
        manifest = self.a.backup / 'manifest'
        if not manifest.is_file() or not self.image.is_file():
            raise Stop('backup manifest or partition image missing')
        expected = ['serial ' + self.a.device, 'partition ' + self.part, 'size ' + str(self.size), 'sha256 ' + digest(self.image)]
        if self.image.stat().st_size != self.size or manifest.read_text(encoding='utf-8').splitlines() != expected:
            raise Stop('backup manifest, serial, size or hash mismatch')

    def backup(self):
        path = self.a.backup
        if path.exists() or path.is_symlink():
            raise Stop('backup path must not exist')
        parent = path.parent.resolve(strict=True)
        if any((p / '.git').exists() for p in (parent, *parent.parents)):
            raise Stop('backup must be outside a Git worktree')
        path.mkdir(mode=0o700)
        self.fb('fetch', self.part, str(self.image))
        if not self.image.is_file() or self.image.stat().st_size != self.size:
            raise Stop('backup is not the full reported partition size')
        (path / 'manifest').write_text('serial %s\npartition %s\nsize %s\nsha256 %s\n' % (self.a.device, self.part, self.size, digest(self.image)), encoding='utf-8', newline='\n')
        self.backup_check()
        print('PASS: full partition backup and manifest created; retain a separate private copy')

    def write(self):
        self.backup_check()
        if self.tmp.exists() or self.tmp.is_symlink():
            raise Stop('readback path already exists')
        try:
            if self.a.operation == 'install':
                self.fb('fetch', self.part, str(self.tmp))
                if not self.tmp.is_file() or self.tmp.stat().st_size != self.size or digest(self.tmp) != digest(self.image):
                    raise Stop('current partition differs from backup; do not overwrite')
                self.tmp.unlink()
                target = self.a.boot
            else:
                target = self.image
            if target.stat().st_size != self.size:
                raise Stop('write image size mismatch')
            expected = digest(target)
            print('OVERWRITE: device %s partition %s, %s bytes, source %s, SHA-256 %s' % (self.a.device, self.part, self.size, target, expected), flush=True)
            failure = None
            try:
                self.fb('flash', self.part, str(target))
            except Stop as exc:
                failure = exc
            except KeyboardInterrupt:
                failure = Stop('flash interrupted')
            # A failed flash can still have written bytes; always attempt full readback.
            try:
                self.fb('fetch', self.part, str(self.tmp))
                if not self.tmp.is_file() or self.tmp.stat().st_size != self.size or digest(self.tmp) != expected:
                    raise Stop('readback size or hash mismatch')
            except Stop as exc:
                raise Stop('readback failed; do not reboot: ' + str(exc)) from exc
            if failure:
                raise Stop('flash reported failure despite readback; do not reboot: ' + str(failure))
            print('PASS: entire partition readback matches; phone boot is NOT_RUN')
        finally:
            if self.tmp.is_file() and not self.tmp.is_symlink():
                self.tmp.unlink()

    def run(self):
        if not self.a.execute:
            return self.planned()
        if self.a.operation == 'install' and not self.a.recovery_rehearsed:
            raise Stop('owner must attest that complete Android recovery was physically rehearsed with --recovery-rehearsed')
        self.identity()
        if self.a.operation != 'restore':
            self.images()
        if self.a.operation == 'preflight':
            print('PASS: preflight; physical device acceptance is NOT_RUN')
        elif self.a.operation == 'backup':
            self.backup()
        else:
            self.write()


def main():
    try:
        Tool(parser().parse_args()).run()
    except (Stop, OSError, UnicodeError, ValueError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
