# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the root-side OTA updater (packaging/duo-os/updater/opos-updater) and its image wiring.

Real signed bundles (ssh-keygen -Y sign with throw-away keys made at run time), a real tar/gzip pipeline, real
flock, a stub systemctl/pkill/runuser, a small HTTP server standing in for opos-server, and the updater's own
smoke test running a tiny fake `opos.web`. Nothing touches the real system: every path is a temp directory.
"""
import ast
import contextlib
import errno
import gzip
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import http.server
from unittest import mock

if not sys.platform.startswith('linux'):
    raise unittest.SkipTest('the updater is Linux-only (flock, O_NOFOLLOW, systemd)')

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / 'packaging' / 'duo-os'
UPDATER = PKG / 'updater' / 'opos-updater'
KEYGEN = shutil.which('ssh-keygen')
NS = 'opos-update'
DELETE = object()


def load_updater():
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader = importlib.machinery.SourceFileLoader('opos_updater', str(UPDATER))
        spec = importlib.util.spec_from_loader('opos_updater', loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = keep


U = load_updater()
needs_keygen = unittest.skipUnless(KEYGEN, 'ssh-keygen is not installed')
KEYS = {}
_keydir = None


def setUpModule():
    global _keydir
    if not KEYGEN:
        return
    _keydir = tempfile.mkdtemp(prefix='opos-updkeys-')
    for name in ('good', 'other'):
        path = os.path.join(_keydir, name)
        subprocess.run([KEYGEN, '-q', '-t', 'ed25519', '-N', '', '-C', 'test', '-f', path], check=True)
        KEYS[name] = path


def tearDownModule():
    if _keydir:
        shutil.rmtree(_keydir, ignore_errors=True)



def rd(path, mode='r'):
    with open(path, mode) as f:
        return f.read()


def wr(path, data, mode='w'):
    with open(path, mode) as f:
        f.write(data)


def pub_line(name):
    fields = rd(KEYS[name] + '.pub').split()
    return 'opos-release %s %s\n' % (fields[0], fields[1])


def sign(key, ns, data):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'm')
        with open(p, 'wb') as f:
            f.write(data)
        subprocess.run([KEYGEN, '-Y', 'sign', '-q', '-f', KEYS[key], '-n', ns, p], check=True, capture_output=True)
        return rd(p + '.sig', 'rb')


def sha(b):
    return hashlib.sha256(b).hexdigest()


WEB_PY = b'''import argparse, http.server, os, sys
p = argparse.ArgumentParser()
p.add_argument("--port", type=int, default=8765)
p.add_argument("--live-battery", action="store_true")
a = p.parse_args()
if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "BROKEN_SMOKE")):
    sys.exit(3)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a):
        pass
srv = http.server.HTTPServer(("127.0.0.1", a.port), H)
print("stand-in server: http://127.0.0.1:%d/" % srv.server_address[1], flush=True)
srv.serve_forever()
'''
OLD_OVERLAY = {
    'usr/libexec/opos/opos-kiosk': (b'#!/bin/sh\necho old kiosk\n', 0o755),
    'etc/systemd/system/opos-server.service': (b'[Service]\nold\n', 0o644),
}
OVERLAY_PATHS = ['usr/libexec/opos/opos-kiosk', 'etc/systemd/system/opos-server.service', 'usr/libexec/opos/opos-newtool']
NEWFILE = 'usr/libexec/opos/opos-newtool'


def default_app(serial):
    return {'src/opos/__init__.py': (b'', 0o644), 'src/opos/web.py': (WEB_PY, 0o644),
            'src/opos/marker.txt': (('build %d\n' % serial).encode(), 0o644), 'LICENSE': (b'GPL\n', 0o644)}


def default_overlay(serial):
    return {'usr/libexec/opos/opos-kiosk': (('#!/bin/sh\necho kiosk %d\n' % serial).encode(), 0o755),
            'etc/systemd/system/opos-server.service': (('[Service]\nbuild %d\n' % serial).encode(), 0o644),
            NEWFILE: (('#!/bin/sh\necho tool %d\n' % serial).encode(), 0o755)}


def revision(serial):
    return hashlib.sha1(('rev%d' % serial).encode()).hexdigest()


def release_name(serial):
    return '%d-%s' % (serial, revision(serial)[:12])


def targz(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz', format=tarfile.GNU_FORMAT) as tf:
        for name, data, mode, typ, link in members:
            ti = tarfile.TarInfo(name)
            ti.mode, ti.type, ti.linkname, ti.mtime = mode, typ, link, 0
            if typ in (tarfile.CHRTYPE, tarfile.BLKTYPE):
                ti.devmajor, ti.devminor = 1, 3
            if typ == tarfile.REGTYPE:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            else:
                tf.addfile(ti)
    return buf.getvalue()


def plain_tar(parts, fmt=tarfile.GNU_FORMAT):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w', format=fmt) as tf:
        for part in parts:
            name, data = part[0], part[1]
            ti = tarfile.TarInfo(name)
            ti.mode, ti.mtime = 0o644, 0
            if len(part) > 2:
                ti.type, ti.linkname = part[2], part[3]
                tf.addfile(ti)
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def make_bundle(serial=7, app=None, overlay=None, scope='app', key='good', ns=NS, requires_base=1, manifest=None, files=None,
                members=None, payload=None, sign_data=None, outer=None, manifest_bytes=None, tamper=None, outer_bytes=None):
    app = default_app(serial) if app is None else app
    overlay = default_overlay(serial) if overlay is None else overlay
    entries = [('opt/opos/' + k, v) for k, v in app.items()] + list(overlay.items())
    if members is None:
        members = [(p, d, m, tarfile.REGTYPE, '') for p, (d, m) in entries]
    if payload is None:
        payload = targz(members)
    if files is None:
        files = [{'path': p, 'sha256': sha(d), 'size': len(d), 'mode': '%04o' % m} for p, (d, m) in entries]
    mf = {'format': 1, 'serial': serial, 'version': '0.%d.0' % serial, 'revision': revision(serial), 'created': '2026-11-08T12:00:00Z',
          'requires_base': requires_base, 'scope': scope, 'payload': {'sha256': sha(payload), 'size': len(payload)}, 'files': files,
          'notes': 'test bundle'}
    for k, v in (manifest or {}).items():
        if v is DELETE:
            mf.pop(k, None)
        else:
            mf[k] = v
    mb = manifest_bytes if manifest_bytes is not None else json.dumps(mf).encode()
    sig = sign(key, ns, sign_data if sign_data is not None else mb)
    if outer_bytes is not None:
        return outer_bytes
    pl = tamper(payload) if tamper else payload
    parts = outer if outer is not None else [('manifest.json', mb), ('manifest.json.sig', sig), ('payload.tar.gz', pl)]
    return plain_tar(parts)


class HealthServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, env):
        super().__init__(('127.0.0.1', 0), _Handler)
        self.env = env


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body):
        self.send_response(code)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        env = self.server.env
        try:
            running = rd(env.running).strip()
        except OSError:
            running = ''
        if env.health_mode == 'down' or os.path.exists(os.path.join(running, 'src/opos/BROKEN')):
            return self.reply(500, b'broken')
        if self.path == '/':
            return self.reply(200, b'<html><head><meta name="opos-token" content="tok"><title>x</title></head></html>')
        if self.path == '/api/state':
            if self.headers.get('X-OPOS-Token') != 'tok':
                return self.reply(403, b'{}')
            return self.reply(200, json.dumps({'battery': {}} if env.health_mode != 'nobattery' else {'x': 1}).encode())
        self.reply(404, b'')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Crash(BaseException):
    """A simulated kill: nothing in the updater may catch it (it is not an Exception)."""


class Env:
    """A fake root file system, a fake systemd and everything the updater is configured with."""

    def __init__(self, signers=('good',), fast=False):
        self.tmp = tempfile.mkdtemp(prefix='opos-upd-')
        self.root = os.path.join(self.tmp, 'root')
        self.run = os.path.join(self.tmp, 'run')
        self.logs = []
        self.boot = 'boot-1'
        self.health_mode = 'ok'
        os.makedirs(self.run)
        r = self.root
        self.opt_link = os.path.join(r, 'opt/opos')
        self.releases = os.path.join(r, 'opt/opos-releases')
        self.var = os.path.join(r, 'var/lib/opos-update')
        self.inbox = os.path.join(self.var, 'inbox')
        self.staging = os.path.join(self.var, 'staging')
        self.signers = os.path.join(r, 'etc/opos/update/allowed_signers')
        self.running = os.path.join(self.run, 'running')
        base = os.path.join(self.releases, '0-aaaaaaaaaaaa')
        for d in ('src/opos',):
            os.makedirs(os.path.join(base, d))
        self.put(os.path.join(base, 'REVISION'), b'a' * 12 + b'0' * 28 + b'\n')
        self.put(os.path.join(base, 'src/opos/__init__.py'), b'')
        self.put(os.path.join(base, 'src/opos/web.py'), WEB_PY)
        self.put(os.path.join(base, 'src/opos/marker.txt'), b'build 0\n')
        os.symlink('opos-releases/0-aaaaaaaaaaaa', self.opt_link)
        os.makedirs(os.path.join(r, 'etc/opos/update'))
        os.makedirs(os.path.join(r, 'etc/systemd/system'))
        os.makedirs(os.path.join(r, 'usr/libexec/opos'))
        os.makedirs(os.path.join(r, 'usr/sbin'))
        os.makedirs(self.inbox)
        os.makedirs(self.staging, mode=0o700)
        self.put(os.path.join(r, 'etc/opos/base-version'), b'1\n')
        self.put(self.signers, ''.join(pub_line(k) for k in signers).encode())
        for p, (d, m) in OLD_OVERLAY.items():
            self.put(os.path.join(r, p), d, m)
        with open(self.running, 'w') as f:
            f.write(os.path.realpath(self.opt_link))
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.bin)
        self.callslog = os.path.join(self.run, 'calls.log')
        self.stub('systemctl', '''echo "systemctl $*" >> "%(log)s"
case "$1" in
 is-active) if [ -e "%(run)s/inactive" ]; then echo inactive; exit 3; fi; echo active; exit 0;;
 restart) [ -e "%(run)s/restart-fails" ] && exit 1; readlink -f "%(opt)s" > "%(run)s/running"; exit 0;;
esac
exit 0''')
        self.stub('pkill', 'echo "pkill $*" >> "%(log)s"\nexit 1')
        self.stub('runuser', 'echo "runuser $*" >> "%(log)s"\n[ "$1" = "-u" ] && shift 2\n[ "$1" = "--" ] && shift\nexec "$@"')
        self.server = HealthServer(self)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.cfg = U.Config(**self.cfg_kwargs())
        if fast:
            self.cfg.smoke = lambda cfg, d: (True, 'stub')
        self.cfg.log = self.logs.append
        self.cfg.statvfs = lambda p: 10**12
        self.cfg.sync = lambda: None
        self.cfg.boot_id = lambda: self.boot
        self.prime()

    def cfg_kwargs(self):
        """The serializable part of the Config (the hard-kill test rebuilds it in a child process)."""
        b = self.bin
        return dict(root=self.root, opt_link=self.opt_link, releases=self.releases, var=self.var, signers=self.signers,
                    base_version=os.path.join(self.root, 'etc/opos/base-version'), trusted_uid=os.geteuid(),
                    ssh_keygen=(KEYGEN,), systemctl=(os.path.join(b, 'systemctl'),),
                    runuser=(os.path.join(b, 'runuser'), '-u', 'droidian', '--'), pkill=(os.path.join(b, 'pkill'),),
                    env_bin='/usr/bin/env', python=sys.executable, health_port=self.server.server_address[1],
                    smoke_chown=False, smoke_timeout=15.0, health_timeout=1.5, health_interval=0.05,
                    confirm_timeout=0.5, lock_timeout=0.3, cmd_timeout=20.0, pace=0.0, fail_pause=0.0)

    def stub(self, name, body):
        path = os.path.join(self.bin, name)
        text = '#!/bin/sh\n' + body % {'log': self.callslog, 'run': self.run, 'opt': self.opt_link} + '\n'
        with open(path, 'w') as f:
            f.write(text)
        os.chmod(path, 0o755)

    @staticmethod
    def put(path, data, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        os.chmod(path, mode)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- driving
    def updater(self):
        return U.Updater(self.cfg)

    def prime(self):
        self.updater().apply_pending_boot()

    def deliver(self, data, name='opos-7.opos-update', mtime=None):
        part = os.path.join(self.inbox, name + '.partial')
        with open(part, 'wb') as f:
            f.write(data)
        if mtime:
            os.utime(part, (mtime, mtime))
        os.rename(part, os.path.join(self.inbox, name))

    def apply(self, data, name='opos-7.opos-update'):
        self.deliver(data, name)
        return self.updater().apply_inbox()

    def request_rollback(self):
        with open(os.path.join(self.inbox, 'rollback.request'), 'w'):
            pass

    # -- looking
    def state(self):
        return json.loads(rd(os.path.join(self.var, 'state.json')))

    def status(self):
        return json.loads(rd(os.path.join(self.var, 'status.json')))

    def calls(self):
        try:
            return rd(self.callslog).splitlines()
        except OSError:
            return []

    def file_info(self, rel):
        p = os.path.join(self.root, rel)
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return None
        return (sha(rd(p, 'rb')), stat.S_IMODE(st.st_mode))

    def overlay_state(self):
        return {p: self.file_info(p) for p in OVERLAY_PATHS}

    def live(self):
        st = self.state()
        return {'link': os.readlink(self.opt_link), 'releases': sorted(os.listdir(self.releases)), 'files': self.overlay_state(),
                'state': {k: st[k] for k in ('current', 'previous', 'failed', 'pending_boot', 'trial', 'txn')},
                'running': rd(self.running), 'restarts': [c for c in self.calls() if ' restart ' in c or 'daemon-reload' in c],
                'staging': sorted(os.listdir(self.staging))}

    def marker(self):
        return rd(os.path.join(self.opt_link, 'src/opos/marker.txt')).strip()

    def expect_overlay(self, serial):
        if serial == 0:
            return {p: (sha(d), m) for p, (d, m) in OLD_OVERLAY.items()} | {NEWFILE: None}
        return {p: (sha(d), m) for p, (d, m) in default_overlay(serial).items()}

    def consistent(self, clean=True):
        """The system is fully on one release: returns that release's serial (0 for the image's own).
        clean=False tolerates leftovers (a staging directory, an unreferenced release) that the next run removes."""
        st = self.state()
        cur = st['current']
        assert st['txn'] is None, 'a transaction is still open'
        assert os.path.basename(os.readlink(self.opt_link)) == cur['release'], 'symlink and state disagree'
        assert os.path.isdir(os.path.join(self.releases, cur['release']))
        assert self.overlay_state() == self.expect_overlay(cur['serial']), 'overlay files do not belong to release %s' % cur['serial']
        if st['trial']:
            assert st['trial']['release'] == cur['release']
        stray = [os.path.join(d, n) for d, _, fs in os.walk(self.root) for n in fs + os.listdir(d) if n.endswith(('.new', '.tmp'))]
        if clean:
            assert not stray, stray
            assert not [n for n in os.listdir(self.releases) if n.startswith('.staging-')]
            keep = {cur['release']} | {p['release'] for p in st['previous']} | {st[k]['release'] for k in ('pending_boot', 'trial') if st[k]}
            assert set(os.listdir(self.releases)) <= keep | {'unrelated-directory'}, os.listdir(self.releases)
        return cur['serial']


class EnvCase(unittest.TestCase):
    fast = False

    def setUp(self):
        self.env = Env(fast=self.fast)
        self.addCleanup(self.env.close)

    def reject(self, data, text, name='opos-7.opos-update', check_untouched=True):
        """Deliver a refused bundle: exit 3, status failed with `text`, inbox emptied, live system untouched."""
        e = self.env
        before = e.live()
        code = e.apply(data, name)
        self.assertEqual(code, 3, e.logs[-3:])
        st = e.status()
        self.assertEqual(st['phase'], 'failed', st)
        self.assertIn(text, st['message'])
        self.assertEqual([n for n in os.listdir(e.inbox) if n.endswith('.opos-update')], [])
        if check_untouched:
            self.assertEqual(e.live(), before)
        self.assertEqual(os.listdir(e.staging), [])
        return st


# ======================================================================================================== module

class TestModule(unittest.TestCase):
    def test_header(self):
        lines = UPDATER.read_text().splitlines()
        self.assertEqual(lines[0], '#!/usr/bin/python3 -IB')
        self.assertEqual(lines[1], '# SPDX-License-Identifier: GPL-3.0-or-later')
        self.assertNotIn(b'\r', UPDATER.read_bytes())

    def test_import_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            code = ('import importlib.machinery as m, importlib.util as u, sys\n'
                    'sys.dont_write_bytecode = True\n'
                    'l = m.SourceFileLoader("x", %r); s = u.spec_from_loader("x", l); mod = u.module_from_spec(s); l.exec_module(mod)\n'
                    'print(mod.Config().var)\n') % str(UPDATER)
            r = subprocess.run([sys.executable, '-I', '-c', code], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), '/var/lib/opos-update')
            self.assertEqual(r.stderr, '')
            self.assertEqual(os.listdir(d), [])

    def test_no_environment_overrides_and_no_dangerous_calls(self):
        tree = ast.parse(UPDATER.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ('environ', 'getenv', 'putenv', 'unsetenv'):
                self.fail('the root updater must not read the environment (line %d)' % node.lineno)
            if isinstance(node, ast.Name) and node.id in ('eval', 'exec', '__import__', 'compile'):
                self.fail('%s at line %d' % (node.id, node.lineno))
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == 'shell':
                        self.fail('shell= at line %d' % node.lineno)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, 'module', None) or '']
                self.assertFalse({'pickle', 'marshal', 'shelve', 'yaml', 'ctypes', 'urllib', 'requests'} & set(n.split('.')[0] for n in names), names)

    def test_production_config(self):
        c = U.Config()
        self.assertEqual((c.root, c.opt_link, c.releases, c.var), ('/', '/opt/opos', '/opt/opos-releases', '/var/lib/opos-update'))
        self.assertEqual(c.signers, '/etc/opos/update/allowed_signers')
        self.assertEqual(c.base_version, '/etc/opos/base-version')
        self.assertEqual((c.inbox, c.staging), ('/var/lib/opos-update/inbox', '/var/lib/opos-update/staging'))
        self.assertEqual(c.trusted_uid, 0)
        for cmd in (c.ssh_keygen, c.systemctl, c.runuser, c.pkill, (c.env_bin,), (c.python,)):
            self.assertTrue(cmd[0].startswith('/'), cmd)
        self.assertEqual(c.runuser, ('/usr/sbin/runuser', '-u', 'droidian', '--'))
        self.assertEqual((c.health_port, c.smoke_port), (8765, 0))
        self.assertEqual((c.max_files, c.max_unpacked), (20000, 256 << 20))
        self.assertEqual(c.keep_previous, 2)
        self.assertIsNone(c.hook)

    def test_unknown_config_key_is_an_error(self):
        with self.assertRaises(TypeError):
            U.Config(shell='sh')

    def test_systemctl_is_restricted_to_the_server_unit(self):
        e = Env(fast=True)
        self.addCleanup(e.close)
        u = e.updater()
        for bad in (('restart', 'opos.service'), ('stop', 'opos-server.service'), ('restart', 'phosh.service'), ('start', 'opos.service'),
                    ('isolate', 'multi-user.target'), ('reboot',), ('restart',)):
            with self.assertRaises(U.Fatal):
                u.systemctl(*bad)
        self.assertEqual(e.calls(), [])
        u.systemctl('restart', 'opos-server.service')
        u.systemctl('daemon-reload')
        self.assertEqual(e.calls(), ['systemctl restart opos-server.service', 'systemctl daemon-reload'])


# ================================================================================================ allowlist, schema

class TestPaths(unittest.TestCase):
    def test_allowed(self):
        for p, want in (('opt/opos/src/opos/web.py', ('app', 'src/opos/web.py')), ('opt/opos/LICENSE', ('app', 'LICENSE')),
                        ('usr/libexec/opos/opos-kiosk', ('overlay', None)), ('usr/sbin/opos-switch', ('overlay', None)),
                        ('etc/systemd/system/opos.service', ('overlay', None)), ('etc/systemd/system/opos-server.service', ('overlay', None)),
                        ('etc/default/opos-server', ('overlay', None)), ('etc/default/opos', ('overlay', None)), ('etc/opos/theme.conf', ('overlay', None))):
            kind, rest = U.classify(p)
            self.assertEqual(kind, want[0], p)
            self.assertEqual(rest, want[1] or p)

    def test_denied(self):
        for p in ('etc/passwd', 'etc/shadow', 'etc/opos/update/allowed_signers', 'etc/opos/update', 'etc/opos/update.json', 'etc/opos/base-version',
                  'etc/opos/update/x/y', 'usr/libexec/opos-updater/opos-updater', 'usr/libexec/opos/sub/x', 'usr/libexec/opos', 'usr/sbin/opos-update-x',
                  'usr/sbin/mount', 'usr/sbin/opos', 'etc/systemd/system/opos-update.service', 'etc/systemd/system/opos-update-boot.service',
                  'etc/systemd/system/opos-update-confirm.service', 'etc/systemd/system/opos-update.path', 'etc/systemd/system/sshd.service',
                  'etc/systemd/system/opos.service.d/x.conf', 'etc/systemd/system/multi-user.target.wants/opos.service',
                  'etc/systemd/system/opos.timer', 'etc/default/ssh', 'etc/default/opos/x', 'opt/opos', 'opt/opos/REVISION', 'opt/opos/manifest.json',
                  'opt/opos/manifest.json.sig', 'opt/opos/overlay/x', 'opt/opos/overlay-backup/x', 'opt/other/x', 'opt/opos-releases/x',
                  '/etc/passwd', '/opt/opos/src/x', '../x', 'opt/opos/../../etc/passwd', 'opt/opos/./x', 'opt/opos//x', 'opt/opos/x/', '', 'opt/opos/a b',
                  'opt/opos/é', 'opt/opos/a\\b', 'usr/libexec/opos/x.new', 'etc/opos/x.new', 'bin/sh', 'usr/bin/python3',
                  'root/.ssh/authorized_keys', 'home/droidian/.ssh/authorized_keys', 'usr/lib/systemd/system/opos.service', 'var/lib/opos-update/state.json',
                  'opt/opos/.hidden', 'a' * 600, 'opt/opos/' + '/'.join(['d'] * 20), None, 7, ['x'],
                  # documented as refused ("As built"), found accepted by the reviewers' differential fuzz
                  'etc/default/opos-update', 'etc/default/opos-update-x', 'etc/default/opos-updater', 'etc/default/opos-update.d',
                  # `$` matches before a final newline; a name must end where the text ends
                  'usr/libexec/opos/evil.new\n', 'usr/libexec/opos/a\n', 'etc/opos/x\n', 'etc/opos/update\n', 'opt/opos/src/opos/x.py\n'):
            with self.assertRaises(U.Reject, msg=repr(p)):
                U.classify(p)

    def test_text_that_ends_in_a_newline_is_not_a_hash_a_version_or_a_name(self):
        for rx, ok in ((U.HEX64, 'a' * 64), (U.HEX40, 'b' * 40), (U.VERSION_RE, '1.2.3'), (U.COMP_RE, 'web.py'), (U.RELEASE_RE, '7-' + 'c' * 12)):
            self.assertTrue(rx.match(ok), ok)
            self.assertIsNone(rx.match(ok + '\n'), rx.pattern)


def good_manifest(**over):
    f = b'hello'
    m = {'format': 1, 'serial': 3, 'version': '1.2.3', 'revision': 'a' * 40, 'created': '2026-11-08T12:00:00Z', 'requires_base': 1,
         'scope': 'app', 'payload': {'sha256': 'b' * 64, 'size': 10},
         'files': [{'path': 'opt/opos/src/opos/web.py', 'sha256': sha(f), 'size': 5, 'mode': '0644'}], 'notes': 'x'}
    m.update(over)
    return m


class TestManifest(unittest.TestCase):
    cfg = U.Config()

    def parse(self, m):
        return U.parse_manifest(m if isinstance(m, bytes) else json.dumps(m).encode(), self.cfg)

    def test_good(self):
        m, entries = self.parse(good_manifest())
        self.assertEqual(m['serial'], 3)
        self.assertEqual([(e.kind, e.tree, e.mode) for e in entries], [('app', 'src/opos/web.py', 0o644)])
        m = good_manifest()
        del m['notes']
        self.parse(m)

    def test_bad(self):
        f = good_manifest()['files'][0]
        cases = {
            'unknown key': good_manifest(extra=1), 'missing key': {k: v for k, v in good_manifest().items() if k != 'scope'},
            'format 2': good_manifest(format=2), 'format str': good_manifest(format='1'), 'format bool': good_manifest(format=True),
            'serial str': good_manifest(serial='3'), 'serial bool': good_manifest(serial=True), 'serial float': good_manifest(serial=3.0),
            'serial zero': good_manifest(serial=0), 'serial negative': good_manifest(serial=-1), 'serial huge': good_manifest(serial=2**40),
            'version empty': good_manifest(version=''), 'version space': good_manifest(version='1 2'), 'version long': good_manifest(version='1' * 65),
            'version int': good_manifest(version=1), 'revision upper': good_manifest(revision='A' * 40), 'revision short': good_manifest(revision='a' * 39),
            'revision non-hex': good_manifest(revision='g' * 40), 'created junk': good_manifest(created='yesterday'),
            'created local': good_manifest(created='2026-11-08T12:00:00+01:00'), 'created int': good_manifest(created=5),
            'requires_base zero': good_manifest(requires_base=0), 'requires_base str': good_manifest(requires_base='1'),
            'requires_base bool': good_manifest(requires_base=True), 'scope other': good_manifest(scope='now'), 'scope none': good_manifest(scope=None),
            'notes int': good_manifest(notes=5), 'notes long': good_manifest(notes='x' * 1001),
            'payload extra': good_manifest(payload={'sha256': 'b' * 64, 'size': 1, 'x': 1}), 'payload sha upper': good_manifest(payload={'sha256': 'B' * 64, 'size': 1}),
            'payload size zero': good_manifest(payload={'sha256': 'b' * 64, 'size': 0}), 'payload not dict': good_manifest(payload=[]),
            'files empty': good_manifest(files=[]), 'files dict': good_manifest(files={}), 'files none': good_manifest(files=None),
            'file extra key': good_manifest(files=[dict(f, x=1)]), 'file missing key': good_manifest(files=[{k: v for k, v in f.items() if k != 'mode'}]),
            'file sha short': good_manifest(files=[dict(f, sha256='ab')]), 'file sha upper': good_manifest(files=[dict(f, sha256='A' * 64)]),
            'mode 0777': good_manifest(files=[dict(f, mode='0777')]), 'mode 4755': good_manifest(files=[dict(f, mode='4755')]),
            'mode 0600': good_manifest(files=[dict(f, mode='0600')]), 'mode 644': good_manifest(files=[dict(f, mode='644')]),
            'mode int': good_manifest(files=[dict(f, mode=644)]), 'size negative': good_manifest(files=[dict(f, size=-1)]),
            'size float': good_manifest(files=[dict(f, size=5.0)]), 'size str': good_manifest(files=[dict(f, size='5')]),
            'size bool': good_manifest(files=[dict(f, size=True)]), 'file not dict': good_manifest(files=['opt/opos/x']),
            'duplicate path': good_manifest(files=[f, dict(f)]), 'path outside': good_manifest(files=[dict(f, path='etc/passwd')]),
            'file under file': good_manifest(files=[f, dict(f, path='opt/opos/src/opos/web.py/x')]),
            'overlay only': good_manifest(files=[dict(f, path='usr/libexec/opos/opos-kiosk')]),
            'top level list': [], 'top level str': 'x', 'top level null': None,
        }
        for name, m in cases.items():
            with self.assertRaises(U.Reject, msg=name):
                self.parse(m)

    def test_bad_bytes(self):
        good = json.dumps(good_manifest())
        for name, raw in (('bom', b'\xef\xbb\xbf' + good.encode()), ('latin1', good.replace('x', '\xe9').encode('latin-1')), ('empty', b''),
                          ('truncated', good[:-3].encode()), ('trailing', (good + ' x').encode()), ('nan', good.replace('"size": 10', '"size": NaN').encode()),
                          ('duplicate key', good.replace('"serial": 3', '"serial": 3, "serial": 4').encode()), ('infinity', good.replace('10}', 'Infinity}').encode())):
            with self.assertRaises(U.Reject, msg=name):
                self.parse(raw)

    def test_limits(self):
        f = good_manifest()['files'][0]
        many = [dict(f, path='opt/opos/f%d' % i) for i in range(20001)]
        with self.assertRaises(U.Reject):
            self.parse(good_manifest(files=many))
        self.parse(good_manifest(files=many[:20000]))
        big = U.Config(max_unpacked=100)
        with self.assertRaises(U.Reject):
            U.parse_manifest(json.dumps(good_manifest(files=[dict(f, size=60), dict(f, path='opt/opos/b', size=60)])).encode(), big)
        with self.assertRaises(U.Reject):
            U.parse_manifest(json.dumps(good_manifest(files=[dict(f, size=101)])).encode(), big)
        with self.assertRaises(U.Reject):
            U.parse_manifest(json.dumps(good_manifest(files=many[:5])).encode(), U.Config(max_files=4))


# ====================================================================================================== rejections

@needs_keygen
class TestRejections(EnvCase):
    fast = True

    def test_happy_control(self):
        """The control for every rejection below: the same builder with nothing wrong is accepted."""
        self.assertEqual(self.env.apply(make_bundle(7)), 0)
        self.assertEqual(self.env.consistent(), 7)

    def test_bad_signatures(self):
        self.reject(make_bundle(7, sign_data=b'something else'), 'signature')
        self.reject(make_bundle(7, outer=[('manifest.json', json.dumps({}).encode()), ('manifest.json.sig', b'garbage'), ('payload.tar.gz', b'x')]), 'signature')
        self.reject(make_bundle(7, outer=[('manifest.json', b'{}'), ('manifest.json.sig', b''), ('payload.tar.gz', b'x')]), 'signature')

    def test_signature_over_a_different_manifest(self):
        good = make_bundle(6)
        mb, sig = None, None
        with tarfile.open(fileobj=io.BytesIO(good)) as tf:
            sig = tf.extractfile('manifest.json.sig').read()
        mine = json.dumps(good_manifest()).encode()
        self.reject(make_bundle(7, manifest_bytes=mine, outer=[('manifest.json', mine), ('manifest.json.sig', sig), ('payload.tar.gz', b'x')]), 'signature')

    def test_unknown_signer(self):
        self.reject(make_bundle(7, key='other'), 'signature')

    def test_wrong_namespace(self):
        self.reject(make_bundle(7, ns='git'), 'signature')
        self.reject(make_bundle(7, ns='opos-updates'), 'signature')

    def test_tampered_payload(self):
        def flip(b):
            return b[:-20] + bytes([b[-20] ^ 1]) + b[-19:]
        self.reject(make_bundle(7, tamper=flip), 'payload does not match')
        self.reject(make_bundle(7, tamper=lambda b: b + b'x'), 'payload does not match')
        self.reject(make_bundle(7, tamper=lambda b: b[:-1]), 'payload does not match')

    def test_manifest_payload_mismatch(self):
        app = default_app(7)
        entries = [('opt/opos/' + k, v) for k, v in app.items()] + list(default_overlay(7).items())
        good_files = [{'path': p, 'sha256': sha(d), 'size': len(d), 'mode': '%04o' % m} for p, (d, m) in entries]
        # a listed file the payload does not have
        self.reject(make_bundle(7, files=good_files + [{'path': 'opt/opos/src/opos/ghost.py', 'sha256': sha(b'g'), 'size': 1, 'mode': '0644'}]), 'missing')
        # a payload member the manifest does not list
        members = [(p, d, m, tarfile.REGTYPE, '') for p, (d, m) in entries] + [('opt/opos/src/opos/extra.py', b'x', 0o644, tarfile.REGTYPE, '')]
        self.reject(make_bundle(7, members=members), 'not in the manifest')
        # wrong hash
        bad = [dict(f) for f in good_files]
        bad[1]['sha256'] = sha(b'other')
        self.reject(make_bundle(7, files=bad), 'signed hash')
        # wrong size
        bad = [dict(f) for f in good_files]
        bad[1]['size'] += 1
        self.reject(make_bundle(7, files=bad), 'differs from the manifest')
        # wrong mode in the tar header
        members = [(p, d, 0o755 if m == 0o644 else 0o644, tarfile.REGTYPE, '') for p, (d, m) in entries]
        self.reject(make_bundle(7, members=members), 'size or mode')
        # setuid bit in the tar header, harmless-looking manifest
        members = [(p, d, 0o4755 if p.endswith('web.py') else m, tarfile.REGTYPE, '') for p, (d, m) in entries]
        self.reject(make_bundle(7, members=members), 'size or mode')
        # member name spelled differently from the manifest
        members = [('./' + p if p.endswith('web.py') else p, d, m, tarfile.REGTYPE, '') for p, (d, m) in entries]
        self.reject(make_bundle(7, members=members), 'not in the manifest')

    def test_traversal_and_absolute_paths(self):
        for path in ('../etc/cron.d/x', 'opt/opos/../../etc/passwd', 'opt/opos/src/../../../x', 'usr/libexec/opos/../../../etc/x'):
            data = b'x'
            files = [{'path': path, 'sha256': sha(data), 'size': 1, 'mode': '0644'}]
            self.reject(make_bundle(7, files=files, members=[(path, data, 0o644, tarfile.REGTYPE, '')]), 'traversal')
        for path in ('/etc/passwd', '/opt/opos/src/opos/web.py', '/usr/libexec/opos/opos-kiosk'):
            data = b'x'
            files = [{'path': path, 'sha256': sha(data), 'size': 1, 'mode': '0644'}]
            self.reject(make_bundle(7, files=files, members=[(path, data, 0o644, tarfile.REGTYPE, '')]), 'absolute')

    def test_member_types(self):
        entries = [('opt/opos/' + k, v) for k, v in default_app(7).items()] + list(default_overlay(7).items())
        base = [(p, d, m, tarfile.REGTYPE, '') for p, (d, m) in entries]
        for label, extra in (('symlink', ('opt/opos/src/opos/link', b'', 0o777, tarfile.SYMTYPE, '/etc/passwd')),
                             ('hardlink', ('opt/opos/src/opos/hard', b'', 0o644, tarfile.LNKTYPE, 'opt/opos/src/opos/web.py')),
                             ('char device', ('opt/opos/src/opos/dev', b'', 0o666, tarfile.CHRTYPE, '')),
                             ('block device', ('opt/opos/src/opos/blk', b'', 0o666, tarfile.BLKTYPE, '')),
                             ('directory', ('opt/opos/src/opos/dir', b'', 0o755, tarfile.DIRTYPE, '')),
                             ('fifo', ('opt/opos/src/opos/fifo', b'', 0o644, tarfile.FIFOTYPE, ''))):
            with self.subTest(label):
                # unlisted extra member of a forbidden type
                self.reject(make_bundle(7, members=base + [extra]), 'not a regular file')
                # the same member listed in the manifest as a regular file
                listed = [{'path': p, 'sha256': sha(d), 'size': len(d), 'mode': '%04o' % m} for p, (d, m) in entries]
                listed.append({'path': extra[0], 'sha256': sha(b''), 'size': 0, 'mode': '0644'})
                self.reject(make_bundle(7, members=base + [extra], files=listed), 'not a regular file')
        # a listed file replaced by a symlink to a sensitive file
        members = [(p, d, m, tarfile.REGTYPE, '') if not p.endswith('opos-kiosk') else (p, b'', 0o755, tarfile.SYMTYPE, '/etc/shadow') for p, (d, m) in entries]
        self.reject(make_bundle(7, members=members), 'not a regular file')

    def test_duplicates(self):
        entries = [('opt/opos/' + k, v) for k, v in default_app(7).items()] + list(default_overlay(7).items())
        base = [(p, d, m, tarfile.REGTYPE, '') for p, (d, m) in entries]
        self.reject(make_bundle(7, members=base + [base[1]]), 'twice')
        listed = [{'path': p, 'sha256': sha(d), 'size': len(d), 'mode': '%04o' % m} for p, (d, m) in entries]
        self.reject(make_bundle(7, files=listed + [listed[0]]), 'twice')

    def test_paths_outside_the_allowlist(self):
        for path, text in (('etc/passwd', 'outside the updatable set'), ('etc/opos/update/allowed_signers', 'belongs to the updater'),
                           ('etc/opos/update', 'belongs to the updater'), ('usr/libexec/opos-updater/opos-updater', 'belongs to the updater'),
                           ('etc/opos/base-version', 'outside the updatable set'), ('etc/opos/update.json', 'outside the updatable set'),
                           ('etc/systemd/system/opos-update.service', 'outside the updatable set'), ('etc/systemd/system/sshd.service', 'outside the updatable set'),
                           ('opt/opos/REVISION', 'not a usable application path'), ('usr/sbin/opos-update-helper', 'outside the updatable set'),
                           ('root/.ssh/authorized_keys', 'malformed path'), ('root/authorized_keys', 'outside the updatable set'), ('usr/bin/python3', 'outside the updatable set')):
            with self.subTest(path):
                self.reject(make_bundle(7, overlay={path: (b'x', 0o644)}), text)

    def test_bad_modes(self):
        for mode in ('0777', '4755', '0600', '2755', '1755', '644'):
            with self.subTest(mode):
                entries = [('opt/opos/' + k, v) for k, v in default_app(7).items()] + list(default_overlay(7).items())
                listed = [{'path': p, 'sha256': sha(d), 'size': len(d), 'mode': mode if p.endswith('web.py') else '%04o' % m} for p, (d, m) in entries]
                self.reject(make_bundle(7, files=listed), 'mode')

    def test_outer_tar_structure(self):
        good = make_bundle(7)
        with tarfile.open(fileobj=io.BytesIO(good)) as tf:
            m, s, p = (tf.extractfile(n).read() for n in ('manifest.json', 'manifest.json.sig', 'payload.tar.gz'))
        cases = {
            'extra member': [('manifest.json', m), ('manifest.json.sig', s), ('payload.tar.gz', p), ('extra', b'x')],
            'wrong order': [('manifest.json.sig', s), ('manifest.json', m), ('payload.tar.gz', p)],
            'missing payload': [('manifest.json', m), ('manifest.json.sig', s)],
            'missing sig': [('manifest.json', m), ('payload.tar.gz', p)],
            'renamed': [('./manifest.json', m), ('manifest.json.sig', s), ('payload.tar.gz', p)],
            'nested': [('x/manifest.json', m), ('manifest.json.sig', s), ('payload.tar.gz', p)],
            'symlink member': [('manifest.json', m, tarfile.SYMTYPE, '/etc/passwd'), ('manifest.json.sig', s), ('payload.tar.gz', p)],
            'directory member': [('manifest.json', m, tarfile.DIRTYPE, ''), ('manifest.json.sig', s), ('payload.tar.gz', p)],
        }
        for label, parts in cases.items():
            with self.subTest(label):
                self.reject(plain_tar(parts), '', check_untouched=True)
        with tarfile.open(fileobj=io.BytesIO(good)) as tf:
            pm = tf.getmember('payload.tar.gz')
        # cut inside the payload (cutting only the tar's zero padding would still be a complete, valid bundle)
        for label, data in (('empty file', b''), ('random bytes', os.urandom(5000)), ('gzip of the tar', gzip.compress(good)),
                            ('text', b'hello\n' * 100), ('truncated payload', good[:pm.offset_data + pm.size - 5]),
                            ('truncated header', good[:700]), ('one block', good[:512])):
            with self.subTest(label):
                self.reject(data, '')
        self.assertEqual(self.env.apply(good[:pm.offset_data + pm.size + 600], 'opos-7.opos-update'), 0)  # padding cut short is harmless

    def test_trailing_garbage_after_the_archive(self):
        self.reject(make_bundle(7) + b'trailing junk', 'after its last member')
        self.assertEqual(self.env.apply(make_bundle(8) + b'\0' * 5000, 'opos-8.opos-update'), 0)

    def test_oversize_member(self):
        good = make_bundle(7)
        with tarfile.open(fileobj=io.BytesIO(good)) as tf:
            s, p = (tf.extractfile(n).read() for n in ('manifest.json.sig', 'payload.tar.gz'))
        self.reject(plain_tar([('manifest.json', b'{' + b' ' * (9 << 20) + b'}'), ('manifest.json.sig', s), ('payload.tar.gz', p)]), 'too large')
        self.reject(plain_tar([('manifest.json', b'{}'), ('manifest.json.sig', b'x' * (17 << 10)), ('payload.tar.gz', p)]), 'too large')

    def test_oversize_bundle_in_the_inbox(self):
        self.env.cfg.max_bundle = 100000
        self.reject(b'\0' * 150000, 'larger than')
        small = make_bundle(7)
        self.assertLess(len(small), 100000)
        self.assertEqual(self.env.apply(small), 0)

    def test_oversize_unpacked(self):
        self.env.cfg.max_unpacked = 1000
        app = {'src/opos/__init__.py': (b'y' * 600, 0o644), 'src/opos/web.py': (b'x' * 600, 0o644)}
        self.reject(make_bundle(7, app=app, overlay={}), 'unpacked size')
        app = {'src/opos/web.py': (b'x' * 1500, 0o644)}
        self.reject(make_bundle(7, app=app, overlay={}), 'file size')

    def test_too_many_files(self):
        self.env.cfg.max_files = 3
        self.reject(make_bundle(7), 'between 1 and 3')

    def test_too_many_files_at_the_production_limit(self):
        files = [{'path': 'opt/opos/f%d' % i, 'sha256': sha(b''), 'size': 0, 'mode': '0644'} for i in range(20001)]
        self.reject(make_bundle(7, files=files, members=[]), 'between 1 and 20000')

    def test_serial_replay_and_downgrade(self):
        e = self.env
        self.assertEqual(e.apply(make_bundle(5), 'opos-5.opos-update'), 0)
        for serial in (5, 4, 1):
            with self.subTest(serial):
                self.reject(make_bundle(serial), 'not newer', 'opos-%d.opos-update' % serial)
        self.assertEqual(e.marker(), 'build 5')

    def test_failed_serial(self):
        e = self.env
        st = e.state()
        st['failed'] = [9]
        atomic = json.dumps(st)
        with open(os.path.join(e.var, 'state.json'), 'w') as f:
            f.write(atomic)
        self.reject(make_bundle(9), 'failed on this phone before', 'opos-9.opos-update')
        self.assertEqual(e.apply(make_bundle(10), 'opos-10.opos-update'), 0)

    def test_requires_base_too_high(self):
        self.reject(make_bundle(7, requires_base=2), 'base level')
        self.assertEqual(self.env.apply(make_bundle(7, requires_base=1)), 0)

    def test_missing_base_version_fails_closed(self):
        os.unlink(os.path.join(self.env.root, 'etc/opos/base-version'))
        self.reject(make_bundle(7), 'base-version')

    def test_no_free_space(self):
        e = self.env
        e.cfg.statvfs = lambda p: 10**12 if not p.startswith(e.releases) else 1000
        self.reject(make_bundle(7), 'not enough free space')
        e.cfg.statvfs = lambda p: 10
        self.reject(make_bundle(7), 'not enough free space')
        e.cfg.statvfs = lambda p: 10**12
        self.assertEqual(e.apply(make_bundle(7)), 0)

    def test_scope_must_be_known(self):
        self.reject(make_bundle(7, scope='now'), 'scope')

    def test_manifest_with_unknown_key_is_refused_even_when_signed(self):
        self.reject(make_bundle(7, manifest={'run_as_root': 'yes'}), 'unknown keys')

    def test_overlay_only_bundle_is_refused(self):
        self.reject(make_bundle(7, app={}), 'no application files')

    def test_overlay_target_must_be_a_regular_file(self):
        e = self.env
        p = os.path.join(e.root, 'usr/libexec/opos/opos-newtool')
        os.symlink('/etc/passwd', p)
        self.reject(make_bundle(7), 'not a regular file', check_untouched=False)
        self.assertEqual(os.readlink(p), '/etc/passwd')

    def test_overlay_directory_must_exist_and_not_be_a_symlink(self):
        e = self.env
        shutil.rmtree(os.path.join(e.root, 'usr/libexec/opos'))
        self.reject(make_bundle(7), 'directory is missing', check_untouched=False)
        elsewhere = os.path.join(e.tmp, 'elsewhere')
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(e.root, 'usr/libexec/opos'))
        self.reject(make_bundle(7), 'directory is missing', check_untouched=False)
        self.assertEqual(os.listdir(elsewhere), [])


@needs_keygen
class TestSigners(EnvCase):
    fast = True

    def test_empty_allowed_signers_accepts_nothing(self):
        wr(self.env.signers, '')
        self.reject(make_bundle(7), 'no update signing keys')

    def test_comment_only_allowed_signers_accepts_nothing(self):
        wr(self.env.signers, '# opos-release ' + pub_line('good'))
        self.reject(make_bundle(7), 'no update signing keys')

    def test_missing_allowed_signers_accepts_nothing(self):
        os.unlink(self.env.signers)
        self.reject(make_bundle(7), 'no update signing keys')

    def test_world_writable_allowed_signers_accepts_nothing(self):
        os.chmod(self.env.signers, 0o666)
        self.reject(make_bundle(7), 'only root may write')
        os.chmod(self.env.signers, 0o644)
        os.chmod(os.path.dirname(self.env.signers), 0o777)
        self.reject(make_bundle(7), 'only root may write')

    def test_symlinked_allowed_signers_accepts_nothing(self):
        e = self.env
        real = os.path.join(e.tmp, 'signers')
        shutil.move(e.signers, real)
        os.symlink(real, e.signers)
        self.reject(make_bundle(7), 'only root may write')

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'needs root to chown')
    def test_allowed_signers_owned_by_someone_else_accepts_nothing(self):
        os.chown(self.env.signers, 12345, 12345)
        self.reject(make_bundle(7), 'only root may write')

    def test_second_key_in_the_file_is_accepted(self):
        e = Env(signers=('other', 'good'), fast=True)
        self.addCleanup(e.close)
        self.assertEqual(e.apply(make_bundle(7, key='other')), 0)
        self.assertEqual(e.apply(make_bundle(8, key='good'), 'opos-8.opos-update'), 0)

    def test_key_not_in_a_file_with_other_keys(self):
        e = Env(signers=('other',), fast=True)
        self.addCleanup(e.close)
        before = e.live()
        self.assertEqual(e.apply(make_bundle(7, key='good')), 3)
        self.assertEqual(e.live(), before)

    def test_principal_must_be_opos_release(self):
        fields = rd(KEYS['good'] + '.pub').split()
        wr(self.env.signers, 'someone-else %s %s\n' % (fields[0], fields[1]))
        self.reject(make_bundle(7), 'signature')


# =========================================================================================================== inbox

@needs_keygen
class TestInbox(EnvCase):
    fast = True

    def test_symlink_in_the_inbox_is_removed_and_never_followed(self):
        e = self.env
        elsewhere = os.path.join(e.tmp, 'valid.opos-update')
        wr(elsewhere, make_bundle(7), 'wb')
        link = os.path.join(e.inbox, 'opos-7.opos-update')
        os.symlink(elsewhere, link)
        before = e.live()
        self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(os.path.exists(elsewhere))
        self.assertEqual(e.live(), before)
        self.assertEqual(e.status()['phase'], 'failed')
        self.assertIn('not a regular file', e.status()['message'])

    def test_symlink_swapped_in_after_the_check(self):
        """copy_in opens with O_NOFOLLOW, so even a swap between listing and opening cannot redirect it."""
        e = self.env
        elsewhere = os.path.join(e.tmp, 'valid')
        wr(elsewhere, make_bundle(7), 'wb')
        os.symlink(elsewhere, os.path.join(e.inbox, 'x.opos-update'))
        dfd = os.open(e.inbox, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, dfd)
        scratch = os.path.join(e.tmp, 'scratch')
        os.makedirs(scratch)
        with self.assertRaises(U.Reject):
            e.updater().copy_in(dfd, 'x.opos-update', scratch)
        self.assertFalse(os.path.lexists(os.path.join(e.inbox, 'x.opos-update')))
        self.assertEqual(os.listdir(scratch), [])

    def test_fifo_is_refused_without_blocking(self):
        e = self.env
        fifo = os.path.join(e.inbox, 'opos-7.opos-update')
        os.mkfifo(fifo)
        before = e.live()
        old = signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(AssertionError('the updater blocked on a FIFO')))
        signal.alarm(20)
        try:
            self.assertEqual(e.updater().apply_inbox(), 3)
            # and the open path itself, as if the FIFO appeared after the directory listing
            os.mkfifo(fifo)
            dfd = os.open(e.inbox, os.O_RDONLY | os.O_DIRECTORY)
            scratch = os.path.join(e.tmp, 'scratch')
            os.makedirs(scratch)
            with self.assertRaises(U.Reject):
                e.updater().copy_in(dfd, 'opos-7.opos-update', scratch)
            os.close(dfd)
            self.assertEqual(os.listdir(scratch), [])
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        self.assertFalse(os.path.lexists(fifo))
        self.assertEqual(e.live(), before)

    def test_directory_named_like_a_bundle_is_removed(self):
        e = self.env
        d = os.path.join(e.inbox, 'opos-7.opos-update')
        os.makedirs(os.path.join(d, 'sub'))
        wr(os.path.join(d, 'sub', 'f'), 'x')
        before = e.live()
        self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertFalse(os.path.lexists(d))
        self.assertEqual(e.live(), before)

    def test_unrelated_files_are_left_alone(self):
        e = self.env
        for n in ('README.txt', 'opos-7.opos-update.bak', 'x.partial'):
            wr(os.path.join(e.inbox, n), 'x')
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertEqual(sorted(os.listdir(e.inbox)), ['README.txt', 'opos-7.opos-update.bak', 'x.partial'])

    def test_stale_partials_are_removed_fresh_ones_kept(self):
        e = self.env
        for n, age in (('fresh.partial', 60), ('stale.partial', 3 * 86400)):
            p = os.path.join(e.inbox, n)
            wr(p, 'x')
            t = time.time() - age
            os.utime(p, (t, t))
        e.updater().apply_inbox()
        self.assertEqual(os.listdir(e.inbox), ['fresh.partial'])

    def test_file_replaced_after_the_copy_has_no_effect(self):
        e = self.env
        evil = make_bundle(7, app={**default_app(7), 'src/opos/marker.txt': (b'EVIL\n', 0o644)})

        fired = []

        def hook(name):
            if name == 'copy-read' and not fired:  # after the bytes were copied, before the inbox entry is dropped
                fired.append(1)
                p = os.path.join(e.inbox, 'opos-7.opos-update')
                wr(p + '.swap', evil, 'wb')
                os.rename(p + '.swap', p)
        e.cfg.hook = hook
        e.deliver(make_bundle(7))
        code = e.updater().apply_inbox()
        self.assertEqual(e.marker(), 'build 7')  # the bytes that were verified are the bytes that were applied
        self.assertEqual(e.state()['current']['serial'], 7)
        self.assertEqual(code, 3)  # the replacement (same serial) was looked at on the next pass and refused
        self.assertEqual(os.listdir(e.inbox), [])
        self.assertEqual(e.consistent(), 7)

    def test_file_swapped_for_a_symlink_after_the_copy(self):
        e = self.env
        evil_path = os.path.join(e.tmp, 'evil')
        wr(evil_path, make_bundle(8, app={**default_app(8), 'src/opos/marker.txt': (b'EVIL\n', 0o644)}), 'wb')

        fired = []

        def hook(name):
            if name == 'copy-read' and not fired:
                fired.append(1)
                p = os.path.join(e.inbox, 'opos-7.opos-update')
                os.symlink(evil_path, p + '.swap')
                os.rename(p + '.swap', p)
        e.cfg.hook = hook
        e.deliver(make_bundle(7))
        self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertEqual(e.marker(), 'build 7')
        self.assertEqual(e.state()['current']['serial'], 7)  # the signed serial-8 bundle behind the symlink was never read
        self.assertTrue(os.path.exists(evil_path))
        self.assertEqual(os.listdir(e.inbox), [])

    def test_newest_bundle_wins_and_older_ones_are_discarded(self):
        e = self.env
        now = time.time()
        e.deliver(make_bundle(5), 'opos-5.opos-update', mtime=now - 100)
        e.deliver(make_bundle(6), 'opos-6.opos-update', mtime=now - 50)
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertEqual(e.state()['current']['serial'], 6)
        self.assertEqual(os.listdir(e.inbox), [])
        self.assertEqual(e.state()['previous'][0]['serial'], 0)

    def test_a_bad_newest_bundle_does_not_block_a_good_older_one(self):
        e = self.env
        now = time.time()
        e.deliver(make_bundle(5), 'opos-5.opos-update', mtime=now - 100)
        e.deliver(make_bundle(6, key='other'), 'opos-6.opos-update', mtime=now - 50)
        self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertEqual(e.state()['current']['serial'], 5)

    def test_a_bundle_that_arrives_during_a_run_is_applied_in_the_same_run(self):
        e = self.env
        fired = []

        def hook(name):
            if name == 'copied' and not fired:
                fired.append(1)
                e.deliver(make_bundle(8), 'opos-8.opos-update')
        e.cfg.hook = hook
        e.deliver(make_bundle(7), 'opos-7.opos-update')
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertEqual(e.state()['current']['serial'], 8)
        self.assertEqual([p['serial'] for p in e.state()['previous']], [7, 0])
        self.assertEqual(os.listdir(e.inbox), [])

    def test_rejected_bundles_are_always_consumed(self):
        """The path unit re-fires while a matching file exists, so a refused bundle must not stay in the inbox."""
        e = self.env
        for i, data in enumerate((b'junk', make_bundle(7, key='other'), make_bundle(7, sign_data=b'x'))):
            e.deliver(data, 'b%d.opos-update' % i)
        e.updater().apply_inbox()
        self.assertEqual(os.listdir(e.inbox), [])

    def test_hostile_rollback_request_is_not_honoured(self):
        e = self.env
        self.assertEqual(e.apply(make_bundle(7)), 0)
        link = os.path.join(e.inbox, 'rollback.request')
        os.symlink('/etc/passwd', link)
        before = e.live()
        self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(e.live(), before)


# ============================================================================================================ apply

@needs_keygen
class TestApply(EnvCase):
    def test_happy_path_with_the_real_smoke_test(self):
        e = self.env
        before_files = e.overlay_state()
        self.assertEqual(e.apply(make_bundle(7)), 0)
        rel = release_name(7)
        # the symlink moved atomically to the new release and is relative
        self.assertEqual(os.readlink(e.opt_link), 'opos-releases/' + rel)
        self.assertEqual(e.marker(), 'build 7')
        self.assertEqual(rd(os.path.join(e.opt_link, 'REVISION')), revision(7) + '\n')
        # overlay files replaced, mode kept, new file created
        self.assertEqual(e.overlay_state(), e.expect_overlay(7))
        self.assertNotEqual(e.overlay_state(), before_files)
        # the originals were saved in the new release
        idx = json.loads(rd(os.path.join(e.releases, rel, 'overlay-backup/index.json')))
        self.assertEqual({i['path']: i['existed'] for i in idx}, {'usr/libexec/opos/opos-kiosk': True, 'etc/systemd/system/opos-server.service': True, NEWFILE: False})
        # state and status
        st = e.state()
        self.assertEqual((st['current']['serial'], st['current']['release'], st['current']['version'], st['current']['revision']),
                         (7, rel, '0.7.0', revision(7)))
        self.assertEqual([p['release'] for p in st['previous']], ['0-aaaaaaaaaaaa'])
        self.assertEqual((st['failed'], st['pending_boot'], st['trial'], st['txn']), ([], None, None, None))
        s = e.status()
        self.assertEqual((s['phase'], s['serial']), ('done', 7))
        self.assertTrue(os.path.isdir(os.path.join(e.releases, '0-aaaaaaaaaaaa')))
        self.assertEqual(os.listdir(e.inbox), [])
        self.assertEqual(os.listdir(e.staging), [])
        e.consistent()

    def test_services_touched_and_not_touched(self):
        e = self.env
        e.apply(make_bundle(7))
        calls = e.calls()
        sc = [c for c in calls if c.startswith('systemctl')]
        self.assertEqual(sc, ['systemctl daemon-reload', 'systemctl restart opos-server.service', 'systemctl is-active opos-server.service'])
        self.assertTrue(any(c.startswith('runuser -u droidian --') and 'opos.web' in c and '--live-battery' in c for c in calls), calls)
        pk = [c for c in calls if c.startswith('pkill') and '--kiosk' in c]
        self.assertEqual(len(pk), 1)
        self.assertIn('-u droidian', pk[0])
        for c in calls:
            self.assertNotRegex(c, r'\bopos\.service\b')
            self.assertNotRegex(c, r'\b(phosh|graphical|reboot|poweroff|isolate)\b')

    def test_no_daemon_reload_without_unit_files(self):
        e = self.env
        e.apply(make_bundle(7, overlay={'usr/libexec/opos/opos-kiosk': (b'#!/bin/sh\n', 0o755)}))
        self.assertNotIn('systemctl daemon-reload', e.calls())

    def test_bundle_without_overlay_files(self):
        e = self.env
        before = e.overlay_state()
        self.assertEqual(e.apply(make_bundle(7, overlay={})), 0)
        self.assertEqual(e.overlay_state(), before)
        self.assertEqual(e.marker(), 'build 7')

    def test_release_tree_permissions(self):
        e = self.env
        e.apply(make_bundle(7, app={**default_app(7), 'src/opos/run.sh': (b'#!/bin/sh\n', 0o755)}))
        base = os.path.join(e.releases, release_name(7))
        for d, dirs, files in os.walk(base):
            for n in dirs:
                if n != 'overlay-backup':  # private to root
                    self.assertEqual(stat.S_IMODE(os.lstat(os.path.join(d, n)).st_mode), 0o755, os.path.join(d, n))
            for n in files:
                p = os.path.join(d, n)
                self.assertFalse(os.path.islink(p))
                if 'overlay-backup' not in p:
                    self.assertIn(stat.S_IMODE(os.lstat(p).st_mode), (0o644, 0o755), p)
        self.assertEqual(stat.S_IMODE(os.lstat(os.path.join(base, 'src/opos/run.sh')).st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(os.lstat(os.path.join(base, 'src/opos/web.py')).st_mode), 0o644)

    def test_state_and_status_files_are_world_readable(self):
        e = self.env
        e.apply(make_bundle(7))
        for n in ('state.json', 'status.json'):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(e.var, n)).st_mode), 0o644)

    def test_smoke_failure_leaves_the_old_release_live(self):
        e = self.env
        app = {**default_app(7), 'src/opos/BROKEN_SMOKE': (b'', 0o644)}
        before = e.live()
        code = e.apply(make_bundle(7, app=app))
        self.assertEqual(code, 3)
        self.assertEqual(e.status()['phase'], 'failed')
        self.assertIn('smoke test failed', e.status()['message'])
        self.assertEqual(e.live(), before)
        self.assertEqual(e.state()['failed'], [])
        self.assertEqual(e.marker(), 'build 0')
        self.assertEqual(e.overlay_state(), e.expect_overlay(0))
        self.assertEqual([r for r in os.listdir(e.releases)], ['0-aaaaaaaaaaaa'])
        self.assertEqual(e.live()['restarts'], [])
        e.consistent()

    @contextlib.contextmanager
    def smoke_ports(self):
        """Record the ports the smoke test talked to (the ones the server reported)."""
        seen = []
        real = U.smoke_port_of

        def spy(log):
            port = real(log)
            if port and port not in seen:
                seen.append(port)
            return port
        with mock.patch.object(U, 'smoke_port_of', spy):
            yield seen

    def test_smoke_server_is_stopped_afterwards(self):
        e = self.env
        with self.smoke_ports() as seen:
            self.assertEqual(e.apply(make_bundle(7)), 0)
        self.assertEqual(len(seen), 1)
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', seen[0]), timeout=1).close()

    def test_smoke_server_gets_a_port_from_the_kernel_not_a_well_known_one(self):
        e = self.env
        self.assertEqual(e.cfg.smoke_port, 0)
        with self.smoke_ports() as seen:
            self.assertEqual(e.apply(make_bundle(7)), 0)
            self.assertEqual(e.apply(make_bundle(8), 'opos-8.opos-update'), 0)
        self.assertEqual(len(seen), 2)
        runs = [c for c in e.calls() if c.startswith('runuser') and 'opos.web' in c]
        self.assertEqual(len(runs), 2)
        for c in runs:
            self.assertIn('opos.web --port 0 --live-battery', c)
            self.assertNotIn('18765', c)

    def test_a_listener_on_the_old_fixed_smoke_port_cannot_block_or_fake_an_update(self):
        """Review finding: a process of the service user that holds the fixed smoke port refused every update (or could
        answer 200 in the server's place). The port is now the kernel's choice, reported by the server itself."""
        e = self.env
        squat = []
        for _ in range(3):
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('127.0.0.1', 18765 if not squat else 0))
            except OSError:
                s.close()
                continue
            s.listen(5)
            self.addCleanup(s.close)
            squat.append(s)
        self.assertTrue(squat)
        e.cfg.smoke_port = U.Config(root='/').smoke_port   # the production default, whatever it is
        self.assertEqual(e.apply(make_bundle(7)), 0, e.status())
        self.assertEqual(e.consistent(), 7)
        for s in squat:                                # nobody connected to the squatters
            s.setblocking(False)
            with self.assertRaises(BlockingIOError):
                s.accept()

    def test_an_answering_squatter_does_not_turn_a_broken_server_into_a_pass(self):
        e = self.env
        class Ok(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')

            def log_message(self, *a):
                pass
        answer = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Ok)
        threading.Thread(target=answer.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True).start()
        self.addCleanup(answer.server_close)
        self.addCleanup(answer.shutdown)
        e.cfg.smoke_port = answer.server_address[1]    # worst case: the squatter sits exactly where the updater is told to look
        app = {**default_app(7), 'src/opos/BROKEN_SMOKE': (b'', 0o644)}
        before = e.live()
        self.assertEqual(e.apply(make_bundle(7, app=app)), 3)
        self.assertIn('smoke test failed', e.status()['message'])
        self.assertEqual(e.live(), before)

    def test_server_that_never_reports_a_port_fails_the_smoke_test(self):
        e = self.env
        e.cfg.smoke_timeout = 1.0
        silent = WEB_PY.replace(b'print("stand-in server: http://127.0.0.1:%d/" % srv.server_address[1], flush=True)', b'pass')
        self.assertNotEqual(silent, WEB_PY)
        before = e.live()
        self.assertEqual(e.apply(make_bundle(7, app={**default_app(7), 'src/opos/web.py': (silent, 0o644)})), 3)
        self.assertIn('reported no port', e.status()['message'])
        self.assertEqual(e.live(), before)

    def test_smoke_port_report_is_read_from_the_log_only(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, 'smoke.log')
            self.assertIsNone(U.smoke_port_of(log))
            for text, want in ((b'', None), (b'starting\n', None), (b'OPOS desktop demo: http://127.0.0.1:0/?demo=1\n', None),
                               (b'OPOS live battery (sysfs, health x): http://127.0.0.1:41234/ (Ctrl-C to stop)\n', 41234),
                               (b'stand-in: http://127.0.0.1:70000/\n', None), (b'http://127.0.0.2:5000/\n', None)):
                with open(log, 'wb') as f:
                    f.write(text)
                self.assertEqual(U.smoke_port_of(log), want, text)

    def test_smoke_test_that_answers_500_fails(self):
        e = self.env
        webpy = WEB_PY.replace(b'self.send_response(200)', b'self.send_response(500)')
        before = e.live()
        self.assertEqual(e.apply(make_bundle(7, app={**default_app(7), 'src/opos/web.py': (webpy, 0o644)})), 3)
        self.assertIn('smoke test failed', e.status()['message'])
        self.assertEqual(e.live(), before)

    def test_health_failure_rolls_everything_back(self):
        e = self.env
        before = e.live()
        before_overlay = e.overlay_state()
        app = {**default_app(7), 'src/opos/BROKEN': (b'', 0o644)}  # passes the smoke test, fails the live health check
        self.assertEqual(e.apply(make_bundle(7, app=app)), 3)
        s = e.status()
        self.assertEqual(s['phase'], 'rolled-back', s)
        self.assertEqual(e.overlay_state(), before_overlay)
        self.assertIsNone(e.file_info(NEWFILE))
        self.assertEqual(os.readlink(e.opt_link), 'opos-releases/0-aaaaaaaaaaaa')
        st = e.state()
        self.assertEqual(st['failed'], [7])
        self.assertEqual(st['current']['serial'], 0)
        self.assertEqual(e.marker(), 'build 0')
        self.assertEqual(os.listdir(e.releases), ['0-aaaaaaaaaaaa'])
        self.assertEqual(rd(e.running).strip(), os.path.realpath(e.opt_link))
        self.assertEqual(len([c for c in e.calls() if c == 'systemctl restart opos-server.service']), 2)
        self.assertEqual(e.live()['link'], before['link'])
        e.consistent()
        # and the same serial is now refused, a new one works
        self.assertEqual(e.apply(make_bundle(7), 'again.opos-update'), 3)
        self.assertIn('failed on this phone before', e.status()['message'])
        self.assertEqual(e.apply(make_bundle(8), 'opos-8.opos-update'), 0)

    def test_health_failure_when_the_server_is_not_active(self):
        e = self.env
        e.cfg.smoke = lambda cfg, d: (True, 'stub')
        wr(os.path.join(e.run, 'inactive'), '')
        self.assertEqual(e.apply(make_bundle(7)), 3)
        self.assertEqual(e.status()['phase'], 'rolled-back')
        self.assertEqual(e.consistent(), 0)

    def test_health_failure_when_the_restart_fails(self):
        e = self.env
        e.cfg.smoke = lambda cfg, d: (True, 'stub')
        wr(os.path.join(e.run, 'restart-fails'), '')
        self.assertEqual(e.apply(make_bundle(7)), 3)
        self.assertEqual(e.status()['phase'], 'rolled-back')
        self.assertIn('restart', e.status()['message'])
        self.assertEqual(e.consistent(), 0)
        self.assertEqual(e.state()['failed'], [7])

    def test_health_failure_when_api_state_has_no_battery(self):
        e = self.env
        e.cfg.smoke = lambda cfg, d: (True, 'stub')
        e.health_mode = 'nobattery'
        self.assertEqual(e.apply(make_bundle(7)), 3)
        self.assertEqual(e.status()['phase'], 'rolled-back')
        self.assertEqual(e.consistent(), 0)

    def test_health_check_waits_for_a_slow_server(self):
        e = self.env
        e.cfg.smoke = lambda cfg, d: (True, 'stub')
        e.cfg.health_timeout = 5
        t0 = time.time()

        def slow(cfg, calls=[0]):
            calls[0] += 1
            return (calls[0] > 5, 'warming up')
        e.cfg.health = slow
        self.assertEqual(e.apply(make_bundle(7)), 0)
        self.assertEqual(e.consistent(), 7)

    def test_retention_keeps_current_and_two_previous(self):
        e = self.env
        e.cfg.smoke = lambda cfg, d: (True, 'stub')
        os.makedirs(os.path.join(e.releases, '.staging-99'))
        os.makedirs(os.path.join(e.releases, 'unrelated-directory'))
        for s in range(1, 6):
            self.assertEqual(e.apply(make_bundle(s), 'opos-%d.opos-update' % s), 0, s)
        names = sorted(os.listdir(e.releases))
        self.assertEqual(names, sorted([release_name(5), release_name(4), release_name(3), 'unrelated-directory']))
        st = e.state()
        self.assertEqual([p['serial'] for p in st['previous']], [4, 3])
        self.assertEqual(st['current']['serial'], 5)
        self.assertEqual(e.consistent(), 5)

    def test_failed_update_does_not_disturb_retention(self):
        e = self.env
        e.cfg.smoke = lambda cfg, d: (True, 'stub')
        for s in (1, 2, 3):
            e.apply(make_bundle(s), 'opos-%d.opos-update' % s)
        e.apply(make_bundle(4, app={**default_app(4), 'src/opos/BROKEN': (b'', 0o644)}), 'opos-4.opos-update')
        self.assertEqual(sorted(os.listdir(e.releases)), sorted([release_name(3), release_name(2), release_name(1)]))
        self.assertEqual(e.consistent(), 3)

    def test_two_updates_in_a_row_chain_the_backups(self):
        e = self.env
        e.cfg.smoke = lambda cfg, d: (True, 'stub')
        e.apply(make_bundle(1), 'opos-1.opos-update')
        e.apply(make_bundle(2), 'opos-2.opos-update')
        idx = json.loads(rd(os.path.join(e.releases, release_name(2), 'overlay-backup/index.json')))
        kiosk = next(i for i in idx if i['path'].endswith('opos-kiosk'))
        self.assertEqual(kiosk['sha256'], sha(default_overlay(1)['usr/libexec/opos/opos-kiosk'][0]))

    def test_status_command(self):
        e = self.env
        e.apply(make_bundle(7))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(U.main(['status'], e.cfg), 0)
        self.assertIn('serial 7', out.getvalue())
        self.assertIn(release_name(7), out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(U.main(['status', '--json'], e.cfg), 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc['state']['current']['serial'], 7)
        self.assertEqual(doc['status']['phase'], 'done')


# ========================================================================================================= layout

@needs_keygen
class TestLayout(EnvCase):
    fast = True

    def test_state_is_created_from_the_image_layout(self):
        e = self.env
        os.unlink(os.path.join(e.var, 'state.json'))
        self.assertEqual(e.updater().apply_pending_boot(), 0)
        st = e.state()
        self.assertEqual(st['current'], {'serial': 0, 'version': 'image', 'revision': 'a' * 12 + '0' * 28, 'release': '0-aaaaaaaaaaaa'})
        self.assertEqual((st['previous'], st['failed'], st['pending_boot'], st['base']), ([], [], None, 1))

    def test_opt_opos_as_a_real_directory_is_refused(self):
        e = self.env
        os.unlink(e.opt_link)
        os.makedirs(e.opt_link)
        e.deliver(make_bundle(7))
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)
        self.assertEqual(os.listdir(e.opt_link), [])
        self.assertTrue(os.path.exists(os.path.join(e.inbox, 'opos-7.opos-update')))

    def test_corrupt_state_is_not_guessed(self):
        e = self.env
        wr(os.path.join(e.var, 'state.json'), '{not json')
        e.deliver(make_bundle(7))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)
        self.assertEqual(e.marker(), 'build 0')
        wr(os.path.join(e.var, 'state.json'), '{"current": 5}')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)

    def test_group_writable_state_directory_is_refused(self):
        e = self.env
        os.chmod(e.var, 0o775)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)
        os.chmod(e.var, 0o755)
        os.chmod(e.releases, 0o777)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)

    def test_world_writable_inbox_is_refused(self):
        e = self.env
        os.chmod(e.inbox, 0o777)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)
        os.chmod(e.inbox, 0o2770)
        self.assertEqual(U.main(['apply-inbox'], e.cfg), 0)

    def test_missing_state_directory_means_updates_are_not_available(self):
        e = self.env
        shutil.rmtree(e.var)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)
        self.assertIn('not available', err.getvalue())

    def test_must_run_as_root(self):
        e = self.env
        e.cfg.trusted_uid = os.geteuid() + 1
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)
        self.assertIn('root', err.getvalue())

    def test_unknown_subcommand(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            U.main(['frobnicate'], self.env.cfg)
        self.assertEqual(cm.exception.code, 2)


# ======================================================================================================== rollback

@needs_keygen
class TestRollback(EnvCase):
    fast = True

    def setUp(self):
        super().setUp()
        for s in (1, 2):
            self.assertEqual(self.env.apply(make_bundle(s), 'opos-%d.opos-update' % s), 0)

    def test_rollback_request_in_the_inbox(self):
        e = self.env
        e.request_rollback()
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertFalse(os.path.exists(os.path.join(e.inbox, 'rollback.request')))
        self.assertEqual(e.consistent(), 1)
        st = e.state()
        # a rollback asked for through the inbox is not burned: the same signed release can be installed again
        self.assertEqual((st['current']['serial'], [p['serial'] for p in st['previous']], st['failed'], st['stepped_back']), (1, [0], [], 2))
        self.assertEqual(e.status()['phase'], 'rolled-back')
        self.assertEqual(e.marker(), 'build 1')
        self.assertEqual(rd(e.running).strip(), os.path.realpath(e.opt_link))
        self.assertEqual(os.listdir(e.releases).count(release_name(2)), 0)

    def test_rollback_subcommand(self):
        e = self.env
        self.assertEqual(U.main(['rollback'], e.cfg), 0)
        self.assertEqual(e.consistent(), 1)
        self.assertEqual(U.main(['rollback'], e.cfg), 0)
        self.assertEqual(e.consistent(), 0)
        self.assertEqual(e.state()['failed'], [1, 2])
        # nothing left to go back to
        self.assertEqual(U.main(['rollback'], e.cfg), 1)
        self.assertEqual(e.status()['phase'], 'failed')
        self.assertEqual(e.consistent(), 0)

    def test_root_rollback_burns_the_serial_but_a_newer_one_works(self):
        e = self.env
        self.assertEqual(U.main(['rollback'], e.cfg), 0)
        self.assertEqual(e.state()['failed'], [2])
        self.assertEqual(e.apply(make_bundle(2), 'again.opos-update'), 3)
        self.assertIn('failed on this phone before', e.status()['message'])
        self.assertEqual(e.apply(make_bundle(3), 'opos-3.opos-update'), 0)
        self.assertEqual(e.consistent(), 3)

    def test_a_requested_rollback_does_not_burn_the_serial_it_leaves(self):
        """Review finding: a request needs no bundle and no signature, and used to burn the release it left, so the
        owner's own signed release could never be installed again. Now the same signed bundle is simply installed again."""
        e = self.env
        e.request_rollback()
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertEqual(e.consistent(), 1)
        self.assertEqual(e.state()['failed'], [])
        self.assertEqual(e.apply(make_bundle(2), 'opos-2.opos-update'), 0, e.status())
        self.assertEqual(e.consistent(), 2)
        self.assertEqual(e.state()['failed'], [])

    def test_only_one_requested_step_back_per_applied_update(self):
        """Review finding: repeated requests walked the phone back through up to two releases and burned each serial."""
        e = self.env
        e.request_rollback()
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertEqual(e.consistent(), 1)
        before = e.live()
        e.request_rollback()
        self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertFalse(os.path.exists(os.path.join(e.inbox, 'rollback.request')))
        self.assertEqual(e.status()['phase'], 'failed')
        self.assertIn('already went back from update 2', e.status()['message'])
        self.assertEqual(e.live(), before)
        self.assertEqual(e.consistent(), 1)
        for _ in range(3):
            e.request_rollback()
            e.updater().apply_inbox()
        self.assertEqual(e.consistent(), 1)
        self.assertEqual(e.state()['failed'], [])
        # an applied update clears the limit: one more step back is possible again
        self.assertEqual(e.apply(make_bundle(3), 'opos-3.opos-update'), 0)
        self.assertIsNone(e.state()['stepped_back'])
        e.request_rollback()
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertEqual(e.consistent(), 1)
        self.assertEqual(e.state()['stepped_back'], 3)

    def test_the_limit_holds_whichever_way_the_phone_went_back(self):
        e = self.env
        self.assertEqual(U.main(['rollback'], e.cfg), 0)   # root, on purpose
        self.assertEqual(e.state()['stepped_back'], 2)
        e.request_rollback()
        self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertEqual(e.consistent(), 1)
        self.assertEqual(U.main(['rollback'], e.cfg), 0)   # root is not limited
        self.assertEqual(e.consistent(), 0)
        self.assertEqual(e.state()['failed'], [1, 2])

    def test_status_says_when_a_request_would_be_refused(self):
        e = self.env
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            U.main(['status'], e.cfg)
        self.assertNotIn('went back', out.getvalue())
        e.request_rollback()
        e.updater().apply_inbox()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            U.main(['status'], e.cfg)
        self.assertIn('went back from serial 2', out.getvalue())

    def test_a_state_file_from_before_the_limit_still_reads(self):
        e = self.env
        st = e.state()
        del st['stepped_back']
        wr(os.path.join(e.var, 'state.json'), json.dumps(st))
        e.request_rollback()
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertEqual(e.consistent(), 1)

    def test_rollback_restarts_only_the_server(self):
        e = self.env
        wr(e.callslog, '')
        e.request_rollback()
        e.updater().apply_inbox()
        sc = [c for c in e.calls() if c.startswith('systemctl')]
        self.assertEqual(sc, ['systemctl daemon-reload', 'systemctl restart opos-server.service'])
        self.assertEqual(len([c for c in e.calls() if c.startswith('pkill') and '--kiosk' in c]), 1)

    def test_rollback_restores_files_created_by_the_update(self):
        e = self.env
        e.request_rollback()
        e.updater().apply_inbox()
        self.assertEqual(U.main(['rollback'], e.cfg), 0)   # a second request would be refused: one step back per update
        self.assertEqual(e.consistent(), 0)
        self.assertIsNone(e.file_info(NEWFILE))

    def test_rollback_also_cancels_a_staged_boot_update(self):
        e = self.env
        e.apply(make_bundle(3, scope='boot'), 'opos-3.opos-update')
        self.assertEqual(e.state()['pending_boot']['serial'], 3)
        e.request_rollback()
        e.updater().apply_inbox()
        self.assertIsNone(e.state()['pending_boot'])
        self.assertNotIn(release_name(3), os.listdir(e.releases))

    def test_rollback_fails_cleanly_when_the_previous_release_is_gone(self):
        e = self.env
        shutil.rmtree(os.path.join(e.releases, release_name(1)))
        before = e.live()
        e.request_rollback()
        self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertEqual(e.status()['phase'], 'failed')
        self.assertEqual(e.live()['link'], before['link'])
        self.assertEqual(e.live()['files'], before['files'])


# ============================================================================================================ boot

@needs_keygen
class TestBoot(EnvCase):
    fast = True

    def stage(self, serial=7, **kw):
        self.assertEqual(self.env.apply(make_bundle(serial, scope='boot', **kw), 'opos-%d.opos-update' % serial), 0)

    def test_boot_scope_is_staged_without_touching_the_running_system(self):
        e = self.env
        before = e.live()
        self.stage()
        after = e.live()
        self.assertEqual(after['link'], before['link'])
        self.assertEqual(after['files'], before['files'])
        self.assertEqual(after['restarts'], [])
        self.assertEqual(after['running'], before['running'])
        st = e.state()
        self.assertEqual(st['pending_boot']['serial'], 7)
        self.assertEqual(st['current']['serial'], 0)
        self.assertEqual(e.status()['phase'], 'done')
        self.assertIn('next boot', e.status()['message'])
        self.assertTrue(os.path.isdir(os.path.join(e.releases, release_name(7))))
        self.assertEqual(e.marker(), 'build 0')

    def test_boot_applies_the_staged_update_without_restarting_anything(self):
        e = self.env
        self.stage()
        e.boot = 'boot-2'
        wr(e.callslog, '')
        self.assertEqual(e.updater().apply_pending_boot(), 0)
        self.assertEqual(e.marker(), 'build 7')
        self.assertEqual(e.overlay_state(), e.expect_overlay(7))
        sc = [c for c in e.calls() if c.startswith('systemctl')]
        self.assertEqual(sc, ['systemctl daemon-reload'])
        self.assertEqual([c for c in e.calls() if c.startswith('pkill')], [])
        st = e.state()
        self.assertEqual((st['current']['serial'], st['pending_boot'], st['failed']), (7, None, []))
        self.assertEqual(st['trial'], {'serial': 7, 'release': release_name(7), 'failed_boots': 0, 'last_boot': 'boot-2', 'counted_boot': None})
        e.consistent()

    def test_confirm_boot_clears_the_trial(self):
        e = self.env
        self.stage()
        e.boot = 'boot-2'
        e.updater().apply_pending_boot()
        self.assertEqual(e.updater().confirm_boot(), 0)
        self.assertIsNone(e.state()['trial'])
        self.assertEqual(e.status()['phase'], 'done')
        self.assertIn('confirmed', e.status()['message'])
        self.assertEqual(e.updater().confirm_boot(), 0)  # nothing on trial: nothing to do

    def trial(self):
        self.stage()
        self.env.boot = 'boot-2'
        self.env.updater().apply_pending_boot()

    def test_two_failed_boots_roll_back(self):
        e = self.env
        self.trial()
        e.health_mode = 'down'
        # boot 2: the health check fails once
        self.assertEqual(e.updater().confirm_boot(), 3)
        self.assertEqual(e.state()['trial']['failed_boots'], 1)
        self.assertEqual(e.state()['current']['serial'], 7)
        self.assertEqual(e.status()['phase'], 'failed')
        # asking again in the same boot does not count twice
        self.assertEqual(e.updater().confirm_boot(), 3)
        self.assertEqual(e.state()['trial']['failed_boots'], 1)
        # boot 3 starts: the early step must not count boot 2 a second time
        e.boot = 'boot-3'
        e.updater().apply_pending_boot()
        self.assertEqual(e.state()['trial']['failed_boots'], 1)
        self.assertEqual(e.state()['current']['serial'], 7)
        # boot 3: fails again -> rolled back
        self.assertEqual(e.updater().confirm_boot(), 3)
        e.health_mode = 'ok'
        st = e.state()
        self.assertEqual((st['current']['serial'], st['failed'], st['trial']), (0, [7], None))
        self.assertEqual(e.status()['phase'], 'rolled-back')
        self.assertEqual(e.consistent(), 0)
        self.assertEqual(e.marker(), 'build 0')

    def test_a_boot_that_never_reaches_confirm_counts_as_failed(self):
        e = self.env
        self.trial()
        e.boot = 'boot-3'                       # boot 2 never confirmed: counted at the start of boot 3
        e.updater().apply_pending_boot()
        self.assertEqual(e.state()['trial']['failed_boots'], 1)
        self.assertEqual(e.state()['current']['serial'], 7)
        e.boot = 'boot-4'                       # boot 3 never confirmed either: rolled back at the start of boot 4
        wr(e.callslog, '')
        self.assertEqual(e.updater().apply_pending_boot(), 3)
        st = e.state()
        self.assertEqual((st['current']['serial'], st['failed'], st['trial']), (0, [7], None))
        self.assertEqual(e.consistent(), 0)
        self.assertEqual([c for c in e.calls() if 'restart' in c], [])  # early boot: nothing is running to restart

    def test_a_healthy_second_boot_confirms(self):
        e = self.env
        self.trial()
        e.health_mode = 'down'
        e.updater().confirm_boot()
        e.health_mode = 'ok'
        e.boot = 'boot-3'
        e.updater().apply_pending_boot()
        self.assertEqual(e.updater().confirm_boot(), 0)
        self.assertEqual(e.consistent(), 7)
        self.assertIsNone(e.state()['trial'])

    def test_a_new_boot_bundle_replaces_the_staged_one(self):
        e = self.env
        self.stage(7)
        self.stage(8)
        self.assertEqual(e.state()['pending_boot']['serial'], 8)
        self.assertNotIn(release_name(7), os.listdir(e.releases))
        self.assertEqual(e.apply(make_bundle(7, scope='boot'), 'late.opos-update'), 3)  # not newer than the staged one
        self.assertIn('staged', e.status()['message'])

    def test_an_app_update_supersedes_a_staged_boot_update(self):
        e = self.env
        self.stage(7)
        self.assertEqual(e.apply(make_bundle(8), 'opos-8.opos-update'), 0)
        self.assertIsNone(e.state()['pending_boot'])
        self.assertNotIn(release_name(7), os.listdir(e.releases))
        e.boot = 'boot-2'
        self.assertEqual(e.updater().apply_pending_boot(), 0)
        self.assertEqual(e.consistent(), 8)

    def test_staged_update_that_became_stale_is_dropped_at_boot(self):
        e = self.env
        self.stage(7)
        st = e.state()
        st['current']['serial'] = 9  # as if something newer ran meanwhile
        wr(os.path.join(e.var, 'state.json'), json.dumps(st))
        e.boot = 'boot-2'
        self.assertEqual(e.updater().apply_pending_boot(), 3)
        self.assertIsNone(e.state()['pending_boot'])
        self.assertEqual(e.state()['failed'], [7])

    def test_tampered_staged_release_is_refused_at_boot(self):
        e = self.env
        for label, tamper in (('file content', lambda d: wr(os.path.join(d, 'src/opos/web.py'), b'# evil\n', 'ab')),
                              ('extra file', lambda d: wr(os.path.join(d, 'src/opos/evil.py'), 'x')),
                              ('overlay content', lambda d: wr(os.path.join(d, 'overlay/usr/libexec/opos/opos-kiosk'), b'evil', 'ab')),
                              ('mode', lambda d: os.chmod(os.path.join(d, 'src/opos/web.py'), 0o4755)),
                              ('manifest', lambda d: wr(os.path.join(d, 'manifest.json'), b' ', 'ab')),
                              ('removed file', lambda d: os.unlink(os.path.join(d, 'LICENSE'))),
                              ('symlink', lambda d: (os.unlink(os.path.join(d, 'LICENSE')), os.symlink('/etc/passwd', os.path.join(d, 'LICENSE'))))):
            with self.subTest(label):
                e2 = Env(fast=True)
                self.addCleanup(e2.close)
                self.assertEqual(e2.apply(make_bundle(7, scope='boot')), 0)
                tamper(os.path.join(e2.releases, release_name(7)))
                before = e2.overlay_state()
                e2.boot = 'boot-2'
                self.assertEqual(e2.updater().apply_pending_boot(), 3)
                self.assertEqual(e2.consistent(), 0)
                self.assertEqual(e2.overlay_state(), before)
                self.assertIsNone(e2.state()['pending_boot'])

    def test_staged_release_is_rechecked_against_the_current_signers(self):
        e = self.env
        self.stage()
        wr(e.signers, '')  # the key is no longer trusted
        e.boot = 'boot-2'
        self.assertEqual(e.updater().apply_pending_boot(), 3)
        self.assertEqual(e.consistent(), 0)

    def test_boot_scope_smoke_test_runs_at_staging_time(self):
        e = self.env
        e.cfg.smoke = lambda cfg, d: (False, 'nope')
        before = e.live()
        self.assertEqual(e.apply(make_bundle(7, scope='boot')), 3)
        self.assertEqual(e.live(), before)
        self.assertIsNone(e.state()['pending_boot'])

    def test_an_app_update_during_a_trial_clears_the_trial_when_healthy(self):
        e = self.env
        self.trial()
        self.assertEqual(e.apply(make_bundle(8), 'opos-8.opos-update'), 0)
        self.assertIsNone(e.state()['trial'])
        self.assertEqual(e.consistent(), 8)

    def test_failed_app_update_during_a_trial_keeps_the_trial(self):
        e = self.env
        self.trial()
        self.assertEqual(e.apply(make_bundle(8, app={**default_app(8), 'src/opos/BROKEN': (b'', 0o644)}), 'opos-8.opos-update'), 3)
        self.assertEqual(e.state()['trial']['serial'], 7)
        self.assertEqual(e.consistent(), 7)

    def test_boot_with_nothing_to_do(self):
        e = self.env
        before = e.live()
        self.assertEqual(e.updater().apply_pending_boot(), 0)
        self.assertEqual(e.updater().confirm_boot(), 0)
        self.assertEqual(e.live()['files'], before['files'])


# ============================================================================================ crash and fault tests

_bundles = {}


def bundle(serial, scope='app', broken=False):
    """Signing costs a process per bundle; the fault matrix reuses them."""
    key = (serial, scope, broken)
    if key not in _bundles:
        app = {**default_app(serial), 'src/opos/BROKEN': (b'', 0o644)} if broken else None
        _bundles[key] = make_bundle(serial, app=app, scope=scope)
    return _bundles[key]


def scn_app(env):
    env.deliver(bundle(7))
    return lambda: env.updater().apply_inbox()


def scn_health_fail(env):
    env.deliver(bundle(7, broken=True))
    return lambda: env.updater().apply_inbox()


def scn_boot_scope_stage(env):
    env.deliver(bundle(7, 'boot'))
    return lambda: env.updater().apply_inbox()


def scn_boot_apply(env):
    env.apply(bundle(7, 'boot'))
    env.boot = 'boot-2'
    return lambda: env.updater().apply_pending_boot()


def scn_boot_rollback(env):
    env.apply(bundle(7, 'boot'))
    env.boot = 'boot-2'
    env.updater().apply_pending_boot()
    env.boot = 'boot-3'
    env.updater().apply_pending_boot()          # boot 2 never confirmed: one failed boot counted
    env.boot = 'boot-4'
    return lambda: env.updater().apply_pending_boot()   # boot 3 never confirmed: rolled back now


def scn_rollback_request(env):
    env.apply(bundle(6), 'opos-6.opos-update')
    env.apply(bundle(7), 'opos-7.opos-update')
    env.request_rollback()
    return lambda: env.updater().apply_inbox()


def scn_rollback_command(env):
    env.apply(bundle(7))
    return lambda: env.updater().rollback_command()


SCENARIOS = {'app update': scn_app, 'health failure': scn_health_fail, 'boot-scope staging': scn_boot_scope_stage, 'boot-time apply': scn_boot_apply,
             'boot-time rollback': scn_boot_rollback, 'rollback request': scn_rollback_request, 'rollback command': scn_rollback_command}


@needs_keygen
class TestCrashSafety(unittest.TestCase):
    def fresh(self):
        e = Env(fast=True)
        self.addCleanup(e.close)
        return e

    def steps_of(self, scn):
        e = self.fresh()
        run = scn(e)
        steps = []
        e.cfg.hook = steps.append
        run()
        self.assertTrue(steps)
        return steps

    def follow_up(self, e, label):
        """After any fault the system must still take the next update."""
        e.cfg.hook = None
        e.cfg.smoke = lambda cfg, d: (True, 'stub')
        e.boot = 'boot-next'
        self.assertEqual(e.apply(bundle(50), 'opos-50.opos-update'), 0, label)
        self.assertEqual(e.consistent(), 50, label)

    def fault_run(self, scn, index, kind, recover):
        e = self.fresh()
        run = scn(e)
        pre = e.live()
        count = [0]
        names = []

        def hook(name):
            names.append(name)
            count[0] += 1
            if count[0] - 1 == index:
                raise Crash(name) if kind == 'crash' else RuntimeError('injected at ' + name)
        e.cfg.hook = hook
        label = '%s at step %d (%s) [%s/%s]' % (scn.__name__, index, names[index] if index < len(names) else '?', kind, recover)
        try:
            run()
        except (Crash, Exception) as ex:
            if kind == 'crash':
                self.assertIsInstance(ex, Crash, label)
        e.cfg.hook = None
        return e, pre, label

    def check_after(self, e, label, recover):
        crashed = e.state()
        if crashed['txn'] is None:
            # no transaction open: the live system must already be consistent, without any recovery
            # (a half-built release directory may still lie around; the next run removes it)
            e.consistent(clean=False)
        for _ in range(2):  # recovery is idempotent
            if recover == 'boot':
                e.boot = 'boot-recover'
                e.updater().apply_pending_boot()
            else:
                e.updater().apply_inbox()
            self.assertIsNone(e.state()['txn'], label)
        serial = e.consistent()
        st = e.state()
        self.assertTrue(set(st['failed']) <= {6, 7}, (label, st['failed']))
        if serial == 0 or serial == 6:
            self.assertIsNone(st['trial'], label)
        return serial

    def test_every_step_boundary_crash_and_exception_for_every_scenario(self):
        for title, scn in SCENARIOS.items():
            names = self.steps_of(scn)
            for kind in ('crash', 'raise'):
                for i in range(len(names)):
                    recover = 'boot' if i % 2 else 'inbox'
                    with self.subTest(scenario=title, step=i, name=names[i], kind=kind):
                        e, pre, label = self.fault_run(scn, i, kind, recover)
                        self.check_after(e, label, recover)
                        self.follow_up(e, label)

    def test_scenarios_do_visit_the_boundaries_that_matter(self):
        names = self.steps_of(scn_app)
        for want in ('copied', 'verified', 'extracted', 'release-ready', 'smoked', 'backup', 'txn', 'overlay-0', 'overlay-2', 'units', 'switched',
                     'restarted', 'health', 'committed', 'status', 'gc'):
            self.assertIn(want, names)
        names = self.steps_of(scn_health_fail)
        for want in ('restore-0', 'restore-2', 'undo-switched', 'undo-saved'):
            self.assertIn(want, names)
        names = self.steps_of(scn_rollback_request)
        for want in ('rollback-txn', 'rollback-switched', 'rollback-saved'):
            self.assertIn(want, names)

    def test_a_crash_before_the_transaction_leaves_the_system_untouched(self):
        for name in ('copied', 'verified', 'extracted', 'release-ready', 'smoked', 'backup'):
            with self.subTest(name):
                names = self.steps_of(scn_app)
                e, pre, label = self.fault_run(scn_app, names.index(name), 'crash', 'inbox')
                self.assertEqual(e.live()['link'], pre['link'])
                self.assertEqual(e.live()['files'], pre['files'])
                self.assertEqual(e.live()['state']['current'], pre['state']['current'])
                self.assertIsNone(e.state()['txn'])
                self.assertEqual(e.live()['restarts'], [])

    def test_a_crash_after_the_transaction_is_undone_by_the_next_run(self):
        names = self.steps_of(scn_app)
        for name in ('txn', 'overlay-0', 'overlay-1', 'overlay-2', 'units', 'switched', 'restarted', 'health'):
            with self.subTest(name):
                e, pre, label = self.fault_run(scn_app, names.index(name), 'crash', 'inbox')
                self.assertIsNotNone(e.state()['txn'], name)
                e.updater().apply_inbox()
                self.assertEqual(e.consistent(), 0, name)
                self.assertEqual(e.state()['failed'], [7], name)
                self.assertEqual(e.status()['phase'], 'rolled-back')
                self.assertEqual(e.live()['files'], pre['files'])

    def test_a_crash_after_the_commit_leaves_the_new_release_in_place(self):
        names = self.steps_of(scn_app)
        for name in ('committed', 'status', 'gc'):
            with self.subTest(name):
                e, pre, label = self.fault_run(scn_app, names.index(name), 'crash', 'inbox')
                self.assertEqual(e.consistent(), 7)
                e.updater().apply_inbox()
                self.assertEqual(e.consistent(), 7)
                self.assertIn(e.status()['phase'], ('done',))

    def test_interrupted_rollback_is_completed(self):
        names = self.steps_of(scn_rollback_request)
        i = names.index('rollback-txn')
        e, pre, label = self.fault_run(scn_rollback_request, i, 'crash', 'inbox')
        self.assertEqual(e.state()['txn']['kind'], 'rollback')
        self.assertIs(e.state()['txn']['requested'], True)
        e.updater().apply_inbox()
        self.assertEqual(e.consistent(), 6)
        self.assertEqual(e.state()['failed'], [])        # asked for through the inbox: not burned, also when finished by recovery
        self.assertEqual(e.state()['stepped_back'], 7)

    def test_crash_during_recovery_then_recovery_again(self):
        names = self.steps_of(scn_app)
        e0, _, _ = self.fault_run(scn_app, names.index('switched'), 'crash', 'inbox')
        rec_steps = []
        e0.cfg.hook = rec_steps.append
        e0.updater().apply_inbox()
        self.assertTrue(rec_steps)
        for j in range(len(rec_steps)):
            with self.subTest(recovery_step=j, name=rec_steps[j]):
                e, _, _ = self.fault_run(scn_app, names.index('switched'), 'crash', 'inbox')
                n = [0]

                def hook(name):
                    n[0] += 1
                    if n[0] - 1 == j:
                        raise Crash(name)
                e.cfg.hook = hook
                with self.assertRaises(Crash):
                    e.updater().apply_inbox()
                e.cfg.hook = None
                e.updater().apply_inbox()
                self.assertEqual(e.consistent(), 0)
                self.assertEqual(e.state()['failed'], [7])
                self.follow_up(e, 'recovery step %d' % j)

    def test_crash_during_the_health_failure_rollback(self):
        names = self.steps_of(scn_health_fail)
        start = names.index('restore-0')
        for i in range(start, len(names)):
            with self.subTest(step=i, name=names[i]):
                e, pre, label = self.fault_run(scn_health_fail, i, 'crash', 'boot')
                self.assertEqual(self.check_after(e, label, 'boot'), 0)
                self.assertEqual(e.state()['failed'], [7])

    def test_status_is_never_left_in_a_working_phase(self):
        names = self.steps_of(scn_app)
        for name in ('verified', 'txn', 'switched', 'committed'):
            with self.subTest(name):
                e, _, _ = self.fault_run(scn_app, names.index(name), 'crash', 'inbox')
                e.updater().apply_pending_boot()
                self.assertNotIn(e.status()['phase'], ('verifying', 'applying', 'checking'), name)

    def test_hard_kill_with_os_exit_at_every_step_boundary(self):
        """The same matrix with a real process death: os._exit leaves no finally block, no atexit, no flush."""
        e0 = self.fresh()
        e0.deliver(bundle(7))
        steps = []
        e0.cfg.hook = steps.append
        e0.updater().apply_inbox()
        total = len(steps)
        child = (
            'import sys, os, json, importlib.machinery as m, importlib.util as u\n'
            'sys.dont_write_bytecode = True\n'
            'a = json.loads(sys.argv[1])\n'
            'l = m.SourceFileLoader("opos_updater", a["updater"]); s = u.spec_from_loader("opos_updater", l); U = u.module_from_spec(s); l.exec_module(U)\n'
            'k = a["cfg"]\n'
            'for key in ("ssh_keygen", "systemctl", "runuser", "pkill"):\n'
            '    k[key] = tuple(k[key])\n'
            'n = [0]\n'
            'def hook(name):\n'
            '    n[0] += 1\n'
            '    if n[0] - 1 == a["index"]:\n'
            '        os._exit(137)\n'
            'cfg = U.Config(**k, smoke=lambda c, d: (True, "stub"), statvfs=lambda p: 10**12, sync=lambda: None, boot_id=lambda: "boot-1", hook=hook)\n'
            'sys.exit(U.Updater(cfg).apply_inbox())\n')
        for i in range(total):
            with self.subTest(step=i, name=steps[i]):
                e = self.fresh()
                e.deliver(bundle(7))
                r = subprocess.run([sys.executable, '-I', '-c', child, json.dumps({'updater': str(UPDATER), 'index': i, 'cfg': e.cfg_kwargs()})],
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 137, r.stderr)
                e.cfg.hook = None
                e.updater().apply_pending_boot()
                self.assertIn(e.consistent(), (0, 7), steps[i])
                self.follow_up(e, 'hard kill at %s' % steps[i])


# =============================================================================================================== lock

@needs_keygen
class TestLock(EnvCase):
    fast = True

    def hold(self):
        fd = os.open(os.path.join(self.env.var, 'lock'), os.O_RDWR | os.O_CREAT, 0o600)
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def test_a_held_lock_blocks_every_mutating_command_and_changes_nothing(self):
        e = self.env
        e.deliver(make_bundle(7))
        before = e.live()
        fd = self.hold()
        try:
            for cmd in ('apply-inbox', 'apply-pending-boot', 'confirm-boot', 'rollback'):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(U.main([cmd], e.cfg), 75, cmd)
                self.assertIn('another update run', err.getvalue())
            self.assertTrue(os.path.exists(os.path.join(e.inbox, 'opos-7.opos-update')))
            self.assertEqual(e.live(), before)
            # status needs no lock
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(U.main(['status'], e.cfg), 0)
        finally:
            os.close(fd)
        self.assertEqual(U.main(['apply-inbox'], e.cfg), 0)
        self.assertEqual(e.consistent(), 7)

    def test_a_waiting_run_gets_the_lock_when_it_is_released(self):
        e = self.env
        e.cfg.lock_timeout = 10
        e.deliver(make_bundle(7))
        fd = self.hold()
        threading.Timer(0.3, os.close, [fd]).start()
        t0 = time.time()
        self.assertEqual(e.updater().apply_inbox(), 0)
        self.assertGreaterEqual(time.time() - t0, 0.25)
        self.assertEqual(e.consistent(), 7)

    def test_two_runs_cannot_overlap(self):
        e = self.env
        e.deliver(make_bundle(7))
        inside, release = threading.Event(), threading.Event()

        def hook(name):
            if name == 'smoked':
                inside.set()
                release.wait(10)
        e.cfg.hook = hook
        result = {}
        t = threading.Thread(target=lambda: result.update(code=e.updater().apply_inbox()))
        t.start()
        self.assertTrue(inside.wait(10))
        e.deliver(make_bundle(8), 'opos-8.opos-update')
        with self.assertRaises(U.Busy):
            e.updater().apply_inbox()
        # the second bundle is still there, untouched, for the running updater's next pass
        self.assertTrue(os.path.exists(os.path.join(e.inbox, 'opos-8.opos-update')))
        release.set()
        t.join(30)
        self.assertEqual(result['code'], 0)
        self.assertEqual(e.state()['current']['serial'], 8)

    def test_the_lock_dies_with_the_process(self):
        e = self.env
        child = ('import os, fcntl, sys\nfd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\nfcntl.flock(fd, fcntl.LOCK_EX)\nos._exit(9)\n')
        r = subprocess.run([sys.executable, '-I', '-c', child, os.path.join(e.var, 'lock')])
        self.assertEqual(r.returncode, 9)
        self.assertEqual(U.main(['apply-inbox'], e.cfg), 0)


# ============================================================================== no start limit, so no switch for the inbox's writer

@needs_keygen
class TestRunsWithoutAStartLimit(EnvCase):
    """opos-update.service has no start limit (a limit was a switch: see TestUnits). What replaces it is tested here."""
    fast = True

    def sleeps(self):
        rec = []
        self.env.cfg.sleep = rec.append
        return rec

    def test_a_run_that_did_its_job_lingers_pace_seconds(self):
        e = self.env
        e.cfg.pace, e.cfg.fail_pause = 2.0, 30.0
        rec = self.sleeps()
        e.deliver(make_bundle(7))
        self.assertEqual(U.main(['apply-inbox'], e.cfg), 0)
        self.assertEqual(rec[-1], 2.0)
        e.deliver(b'junk')
        self.assertEqual(U.main(['apply-inbox'], e.cfg), 3)
        self.assertEqual(rec[-1], 2.0)

    def test_a_run_that_could_not_work_lingers_longer(self):
        import fcntl
        e = self.env
        e.cfg.pace, e.cfg.fail_pause = 2.0, 30.0
        rec = self.sleeps()
        e.deliver(make_bundle(7))
        fd = os.open(os.path.join(e.var, 'lock'), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(U.main(['apply-inbox'], e.cfg), 75)
        finally:
            os.close(fd)
        self.assertEqual(rec[-1], 30.0)
        os.rmdir(e.staging)                                   # a broken layout is fatal, and stays so until a person looks
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(U.main(['apply-inbox'], e.cfg), 1)
        self.assertEqual(rec[-1], 30.0)
        self.assertTrue(os.path.exists(os.path.join(e.inbox, 'opos-7.opos-update')))

    def test_a_bug_that_escapes_still_lingers(self):
        e = self.env
        e.cfg.pace, e.cfg.fail_pause = 2.0, 30.0
        rec = self.sleeps()
        with mock.patch.object(U.Updater, 'apply_inbox', side_effect=ZeroDivisionError):
            with self.assertRaises(ZeroDivisionError):
                U.main(['apply-inbox'], e.cfg)
        self.assertEqual(rec[-1], 30.0)

    def test_only_apply_inbox_lingers(self):
        e = self.env
        e.cfg.pace, e.cfg.fail_pause = 2.0, 30.0
        rec = self.sleeps()
        with contextlib.redirect_stdout(io.StringIO()):
            U.main(['status'], e.cfg)
        U.main(['rollback'], e.cfg)
        U.main(['apply-pending-boot'], e.cfg)
        U.main(['confirm-boot'], e.cfg)
        self.assertNotIn(2.0, rec)
        self.assertNotIn(30.0, rec)

    def test_production_pace_is_short_and_the_pause_after_a_failure_is_longer(self):
        c = U.Config()
        self.assertTrue(1.0 <= c.pace <= 5.0, c.pace)
        self.assertGreater(c.fail_pause, c.pace)

    def test_an_unexpected_error_still_removes_the_inbox_file_it_looked_at(self):
        """Without a start limit a file that stays is read again at once: the generic error branch must remove it too."""
        e = self.env
        e.deliver(make_bundle(7))
        with mock.patch.object(U.Updater, 'copy_in', side_effect=OSError(errno.ENOSPC, 'No space left on device')):
            self.assertEqual(e.updater().apply_inbox(), 3)
        self.assertEqual(os.listdir(e.inbox), [])
        self.assertIn('internal error', e.status()['message'])
        self.assertEqual(e.state()['current']['serial'], 0)
        self.assertEqual(e.updater().apply_inbox(), 0)        # nothing is left to start another run
        self.assertEqual(e.apply(make_bundle(7)), 0)          # and the same bundle delivered again installs

    def test_an_unexpected_error_does_not_remove_a_newer_file_of_the_same_name(self):
        e = self.env
        e.deliver(make_bundle(7))

        def swap(self_, dfd, name, run_dir):
            os.unlink(os.path.join(e.inbox, name))             # the file that was looked at is replaced by a new delivery
            e.deliver(make_bundle(8), name)
            raise OSError(errno.EIO, 'Input/output error')
        with mock.patch.object(U.Updater, 'copy_in', swap):
            e.cfg.max_passes = 1
            e.updater().apply_inbox()
        self.assertEqual(os.listdir(e.inbox), ['opos-7.opos-update'])

    def test_every_kind_of_inbox_entry_is_gone_after_a_run(self):
        e = self.env
        victim = os.path.join(e.tmp, 'victim')
        wr(victim, 'keep')
        os.symlink(victim, os.path.join(e.inbox, 'a.opos-update'))
        os.mkdir(os.path.join(e.inbox, 'b.opos-update'))
        wr(os.path.join(e.inbox, 'b.opos-update', 'f'), 'x')
        os.mkfifo(os.path.join(e.inbox, 'c.opos-update'))
        os.mkdir(os.path.join(e.inbox, 'rollback.request'))
        wr(os.path.join(e.inbox, 'd.opos-update'), 'not a tar')
        wr(os.path.join(e.inbox, 'e.opos-update'), '')
        wr(os.path.join(e.inbox, 'f.opos-update'), 'x' * (2 << 20))
        self.assertEqual(e.updater().apply_inbox(), 3)
        left = [n for n in os.listdir(e.inbox) if n.endswith('.opos-update') or n == 'rollback.request']
        self.assertEqual(left, [])
        self.assertEqual(rd(victim), 'keep')


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid() == 0 and shutil.which('systemd-run') and os.path.isdir('/run/systemd/system'),
                     'needs root on a machine running systemd')
class TestRealSystemdStartLimit(unittest.TestCase):
    """The unit files' own directives on a real systemd: any number of files dropped in the inbox, one after the other,
    leave the service and the path unit healthy. (A transient pair stands in for the real ExecStart, which needs the image.)"""

    def run_pair(self, files):
        u = read_unit('opos-update.service')
        props = ['--property=Type=oneshot']
        for section in ('Unit', 'Service'):
            for key in ('StartLimitIntervalSec', 'StartLimitBurst', 'SuccessExitStatus'):
                for v in values(u, section, key):
                    props.append('--property=%s=%s' % (key, v))
        inbox = tempfile.mkdtemp(prefix='opos-sl-')
        self.addCleanup(shutil.rmtree, inbox, True)
        unit = 'opos-test-startlimit-%d' % os.getpid()

        def stop():
            subprocess.run(['systemctl', 'stop', unit + '.path', unit + '.service'], capture_output=True)
            subprocess.run(['systemctl', 'reset-failed', unit + '.path', unit + '.service'], capture_output=True)
        self.addCleanup(stop)
        # what the real updater does to the inbox: removes what it looked at, then exits 0, 3 or 75
        script = ('n=$(ls "%(d)s" | wc -l); rm -f "%(d)s"/*.opos-update "%(d)s"/rollback.request; '
                  'case $((n %% 3)) in 0) exit 0;; 1) exit 3;; *) exit 75;; esac') % {'d': inbox}
        r = subprocess.run(['systemd-run', '--quiet', '--unit=' + unit, '--path-property=PathExistsGlob=%s/*.opos-update' % inbox,
                            '--path-property=PathExists=%s/rollback.request' % inbox, *props, '/bin/sh', '-c', script], capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest('systemd-run is not usable here: %s' % r.stderr.strip()[:100])

        def state(what):
            return subprocess.run(['systemctl', *what], capture_output=True, text=True).stdout.strip()
        for i in range(files):
            name = 'rollback.request' if i % 5 == 4 else 'x%d.opos-update' % i
            with open(os.path.join(inbox, name), 'w') as f:
                f.write('junk\n')
            end = time.time() + 10
            while time.time() < end and [n for n in os.listdir(inbox) if n.endswith('.opos-update') or n == 'rollback.request']:
                time.sleep(0.05)
            self.assertEqual(os.listdir(inbox), [], 'file %d was not taken' % (i + 1))
            time.sleep(0.15)
            self.assertEqual(state(['is-active', unit + '.path']), 'active', 'path unit after file %d' % (i + 1))
            self.assertNotEqual(state(['is-active', unit + '.service']), 'failed', 'service after file %d' % (i + 1))
        self.assertEqual(state(['show', '-p', 'Result', '--value', unit + '.service']), 'success')
        self.assertEqual(state(['show', '-p', 'Result', '--value', unit + '.path']), 'success')

    def test_a_dozen_files_in_a_row_do_not_switch_the_update_path_off(self):
        self.run_pair(12)


# =========================================================================================== production probes

class TestProbes(unittest.TestCase):
    def setUp(self):
        self.env = Env(fast=True)
        self.addCleanup(self.env.close)

    def test_probe_http_contract(self):
        e = self.env
        port = e.server.server_address[1]
        self.assertEqual(U.probe_http('127.0.0.1', port), (True, 'healthy'))
        e.health_mode = 'nobattery'
        ok, msg = U.probe_http('127.0.0.1', port)
        self.assertFalse(ok)
        self.assertIn('battery', msg)
        e.health_mode = 'down'
        ok, msg = U.probe_http('127.0.0.1', port)
        self.assertFalse(ok)
        self.assertIn('500', msg)
        ok, msg = U.probe_http('127.0.0.1', free_port())
        self.assertFalse(ok)

    def test_probe_health_needs_an_active_unit(self):
        e = self.env
        self.assertTrue(U.probe_health(e.cfg)[0])
        wr(os.path.join(e.run, 'inactive'), '')
        ok, msg = U.probe_health(e.cfg)
        self.assertFalse(ok)
        self.assertIn('not active', msg)

    def test_run_never_raises_for_missing_binaries_or_timeouts(self):
        c = U.Config(cmd_timeout=0.2)
        self.assertEqual(U.run(c, ['/nonexistent/tool']).returncode, 127)
        self.assertEqual(U.run(c, [sys.executable, '-c', 'import time; time.sleep(5)']).returncode, 124)

    def test_commands_run_with_a_fixed_environment(self):
        os.environ['OPOS_UPDATER_PROBE'] = 'leaked'
        try:
            r = U.run(U.Config(), ['/usr/bin/env'])
        finally:
            del os.environ['OPOS_UPDATER_PROBE']
        self.assertNotIn(b'OPOS_UPDATER_PROBE', r.stdout)
        self.assertIn(b'PATH=/usr/sbin:/usr/bin:/sbin:/bin', r.stdout)


# ==================================================================================== image wiring: units, scripts

def read_unit(name):
    sections, cur = {}, None
    for line in (PKG / 'updater' / name).read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        if line.startswith('['):
            cur = sections.setdefault(line.strip('[]'), [])
        else:
            cur.append(tuple(line.split('=', 1)))
    return sections


def values(unit, section, key):
    return [v for k, v in unit[section] if k == key]


class TestUnits(unittest.TestCase):
    def test_path_unit_watches_for_finished_bundles_only(self):
        u = read_unit('opos-update.path')
        self.assertEqual(values(u, 'Path', 'PathExistsGlob'), ['/var/lib/opos-update/inbox/*.opos-update'])
        self.assertEqual(values(u, 'Path', 'PathExists'), ['/var/lib/opos-update/inbox/rollback.request'])
        self.assertEqual(values(u, 'Path', 'Unit'), ['opos-update.service'])
        # a half-downloaded .partial must not start the service, and events during a run must not be lost
        for bad in ('DirectoryNotEmpty', 'PathChanged', 'PathModified'):
            self.assertEqual(values(u, 'Path', bad), [])
        self.assertEqual(values(u, 'Install', 'WantedBy'), ['multi-user.target'])
        self.assertNotIn('.partial', ''.join(v for _, v in u['Path']))

    def test_service_is_a_root_oneshot_that_nobody_can_start_limit_out_of_action(self):
        u = read_unit('opos-update.service')
        self.assertEqual(values(u, 'Service', 'Type'), ['oneshot'])
        self.assertEqual(values(u, 'Service', 'User'), [])
        self.assertEqual(values(u, 'Service', 'ExecStart'), ['/usr/libexec/opos-updater/opos-updater apply-inbox'])
        self.assertTrue(int(values(u, 'Service', 'TimeoutStartSec')[0]) >= 300)
        # Review finding: every file dropped in the inbox is one start, the inbox is writable by the unprivileged server, and
        # the seventh start in ten minutes left the service and the path unit failed until root reset them.
        self.assertEqual(values(u, 'Unit', 'StartLimitIntervalSec'), ['0'])
        self.assertEqual(values(u, 'Unit', 'StartLimitBurst'), [])
        # 3: a bundle or request was refused or rolled back; 75: another run held the lock (a run that fails is a failed start)
        self.assertEqual(values(u, 'Service', 'SuccessExitStatus'), ['3 75'])
        self.assertEqual(values(read_unit('opos-update.path'), 'Unit', 'StartLimitIntervalSec'), [])
        self.assertIn('opos-update-boot.service', values(u, 'Unit', 'After')[0])
        self.assertEqual(values(u, 'Install', 'WantedBy') if 'Install' in u else [], [])  # started by the path unit only

    def test_boot_unit_runs_before_the_server_and_the_session(self):
        u = read_unit('opos-update-boot.service')
        self.assertEqual(values(u, 'Unit', 'Before'), ['opos-server.service opos.service'])
        self.assertEqual(values(u, 'Unit', 'After'), ['local-fs.target'])
        self.assertEqual(values(u, 'Service', 'Type'), ['oneshot'])
        self.assertEqual(values(u, 'Service', 'ExecStart'), ['/usr/libexec/opos-updater/opos-updater apply-pending-boot'])
        self.assertLessEqual(int(values(u, 'Service', 'TimeoutStartSec')[0]), 600)
        self.assertEqual(values(u, 'Install', 'WantedBy'), ['multi-user.target'])
        self.assertEqual(values(u, 'Unit', 'DefaultDependencies'), [])  # keep the default ordering (after basic.target)

    def test_confirm_unit_runs_after_multi_user(self):
        u = read_unit('opos-update-confirm.service')
        self.assertIn('multi-user.target', values(u, 'Unit', 'After')[0].split())
        self.assertIn('opos-server.service', values(u, 'Unit', 'After')[0].split())
        self.assertEqual(values(u, 'Service', 'ExecStart'), ['/usr/libexec/opos-updater/opos-updater confirm-boot'])
        self.assertEqual(values(u, 'Install', 'WantedBy'), ['multi-user.target'])

    def test_units_never_restart_the_session(self):
        for n in ('opos-update.path', 'opos-update.service', 'opos-update-boot.service', 'opos-update-confirm.service'):
            text = (PKG / 'updater' / n).read_text()
            code = '\n'.join(l for l in text.splitlines() if not l.startswith('#'))
            self.assertNotRegex(code, r'(Restart|Reload|Conflicts|PartOf|BindsTo|Requires|Wants|OnFailure)=.*\bopos\.service', n)
            self.assertNotIn('systemctl', code, n)
            self.assertTrue(text.startswith('# SPDX-License-Identifier: GPL-3.0-or-later\n'), n)
            self.assertNotIn('\r', text)

    def test_hardening_is_present_and_leaves_the_write_set_open(self):
        for n in ('opos-update.service', 'opos-update-boot.service', 'opos-update-confirm.service'):
            u = read_unit(n)
            for key in ('NoNewPrivileges', 'ProtectSystem', 'ProtectHome', 'PrivateTmp', 'ProtectKernelTunables', 'RestrictSUIDSGID', 'LockPersonality'):
                self.assertEqual(len(values(u, 'Service', key)), 1, (n, key))
            rw = values(u, 'Service', 'ReadWritePaths')[0].split()
            for need in ('/opt', '/var/lib/opos-update'):
                self.assertIn(need, rw)
            for need in ('/usr/libexec/opos', '/usr/sbin', '/etc/systemd/system', '/etc/default', '/etc/opos'):
                self.assertIn('-' + need, rw)
            # the updater's own files and the trust keys stay read-only to the service
            for p in rw:
                self.assertNotIn('opos-updater', p)
                self.assertNotIn('/etc/opos/update', p)
            caps = values(u, 'Service', 'CapabilityBoundingSet')[0].split()
            for need in ('CAP_SETUID', 'CAP_SETGID', 'CAP_KILL', 'CAP_DAC_OVERRIDE', 'CAP_CHOWN'):
                self.assertIn(need, caps)
            self.assertNotIn('CAP_SYS_ADMIN', caps)
            self.assertNotIn('AF_PACKET', values(u, 'Service', 'RestrictAddressFamilies')[0])


def extract_block(path, what):
    text = path.read_text()
    m = re.search(r'^# ---- BEGIN OPOS OTA %s.*?\n(.*?)^# ---- END OPOS OTA %s' % (what, what), text, re.S | re.M)
    assert m, 'block markers for %s are missing in %s' % (what, path.name)
    return m.group(1)


class TestScripts(unittest.TestCase):
    def test_shell_syntax(self):
        for n in ('build-image.sh', 'verify-image.sh'):
            r = subprocess.run(['sh', '-n', str(PKG / n)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_updater_compiles(self):
        compile(UPDATER.read_text(), str(UPDATER), 'exec')

    def test_the_ota_changes_are_in_one_delimited_block_per_script(self):
        for n, what in (('build-image.sh', 'updates'), ('verify-image.sh', 'checks')):
            text = (PKG / n).read_text()
            self.assertEqual(len(re.findall(r'^# ---- BEGIN OPOS OTA', text, re.M)), 1, n)
            self.assertEqual(len(re.findall(r'^# ---- END OPOS OTA', text, re.M)), 1, n)
            extract_block(PKG / n, what)
        text = (PKG / 'build-image.sh').read_text()
        outside = re.sub(r'# ---- BEGIN OPOS OTA.*?# ---- END OPOS OTA[^\n]*\n', '', text, flags=re.S)
        for needle in ('opos-update', 'allowed_signers', 'opos-releases', 'opos-updater'):
            self.assertNotIn(needle, outside)

    def test_new_files_have_spdx_and_lf(self):
        for p in sorted((PKG / 'updater').iterdir()) + [pathlib.Path(__file__)]:
            data = p.read_bytes()
            self.assertNotIn(b'\r', data, p.name)
            first = data.splitlines()[0] if not data.startswith(b'#!') else data.splitlines()[1]
            self.assertEqual(first.strip(), b'# SPDX-License-Identifier: GPL-3.0-or-later', p.name)


@needs_keygen
class TestImageBlocks(unittest.TestCase):
    """Run the build-image.sh OTA block and the verify-image.sh OTA checks against a throw-away root file system."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='opos-img-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = os.path.join(self.tmp, 'r')

    def make_root(self):
        r = self.r
        os.makedirs(os.path.join(r, 'opt/opos/src/opos'))
        wr(os.path.join(r, 'opt/opos/src/opos/web.py'), WEB_PY, 'wb')
        wr(os.path.join(r, 'opt/opos/REVISION'), '0123456789abcdef0123456789abcdef01234567\n')
        os.makedirs(os.path.join(r, 'etc/systemd/system/multi-user.target.wants'))
        os.makedirs(os.path.join(r, 'usr/libexec/opos'))
        wr(os.path.join(r, 'etc/passwd'), 'root:x:0:0:root:/root:/bin/sh\ndroidian:x:32011:32011::/nonexistent:/bin/bash\n')

    def build(self, signers=None, passwd=None):
        self.make_root()
        if passwd is not None:
            wr(os.path.join(self.r, 'etc/passwd'), passwd)
        env = dict(os.environ)
        env.pop('OPOS_UPDATE_SIGNERS', None)
        if signers is not None:
            env['OPOS_UPDATE_SIGNERS'] = signers
        script = 'set -eu\nr=%s\nhere=%s\n%s\n' % (self.r, PKG, '' if os.geteuid() == 0 else 'chown() { :; }\n') + extract_block(PKG / 'build-image.sh', 'updates')
        return subprocess.run(['sh', '-c', script], capture_output=True, text=True, env=env)

    def verify(self):
        script = ('set -u\nr=%s\nw=$r/etc/systemd/system\nfail=0\ncheck() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n%s'
                  % (self.r, extract_block(PKG / 'verify-image.sh', 'checks')))
        out = subprocess.run(['sh', '-c', script], capture_output=True, text=True).stdout
        fails = [l for l in out.splitlines() if l.startswith('FAIL')]
        if os.geteuid() != 0:  # ownership cannot be set without root
            fails = [l for l in fails if 'owned by root' not in l and 'root:droidian' not in l]
        return out, fails

    def keyfile(self, *names, extra=''):
        p = os.path.join(self.tmp, 'signers')
        with open(p, 'w') as f:
            for n in names:
                f.write(rd(KEYS[n] + '.pub'))
            f.write(extra)
        return p

    def test_layout_without_signers(self):
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('update signers: NONE', r.stdout)
        link = os.path.join(self.r, 'opt/opos')
        self.assertEqual(os.readlink(link), 'opos-releases/0-0123456789ab')
        self.assertTrue(os.path.isfile(os.path.join(self.r, 'opt/opos-releases/0-0123456789ab/src/opos/web.py')))
        self.assertEqual(rd(os.path.join(link, 'REVISION')).strip(), '0123456789abcdef0123456789abcdef01234567')
        self.assertEqual(os.listdir(os.path.join(self.r, 'opt/opos-releases')), ['0-0123456789ab'])
        self.assertEqual(os.path.getsize(os.path.join(self.r, 'etc/opos/update/allowed_signers')), 0)
        self.assertEqual(rd(os.path.join(self.r, 'etc/opos/base-version')), '1\n')
        inbox = os.stat(os.path.join(self.r, 'var/lib/opos-update/inbox'))
        self.assertEqual(stat.S_IMODE(inbox.st_mode), 0o2770)
        if os.geteuid() == 0:
            self.assertEqual((inbox.st_uid, inbox.st_gid), (0, 32011))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.r, 'var/lib/opos-update/staging')).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.r, 'var/lib/opos-update')).st_mode), 0o755)
        u = os.path.join(self.r, 'usr/libexec/opos-updater/opos-updater')
        self.assertEqual(stat.S_IMODE(os.stat(u).st_mode), 0o755)
        self.assertEqual(rd(u, 'rb'), UPDATER.read_bytes())
        w = os.path.join(self.r, 'etc/systemd/system')
        for n in ('opos-update.path', 'opos-update.service', 'opos-update-boot.service', 'opos-update-confirm.service'):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(w, n)).st_mode), 0o644)
        for n in ('opos-update.path', 'opos-update-boot.service', 'opos-update-confirm.service'):
            self.assertEqual(os.readlink(os.path.join(w, 'multi-user.target.wants', n)), '/etc/systemd/system/' + n)
        self.assertFalse(os.path.lexists(os.path.join(w, 'multi-user.target.wants/opos-update.service')))

    def test_signers_are_installed_in_allowed_signers_format(self):
        r = self.build(signers=self.keyfile('good', 'other', extra='\n# a comment\n'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('update signers: 2 public key(s)', r.stdout)
        lines = rd(os.path.join(self.r, 'etc/opos/update/allowed_signers')).splitlines()
        self.assertEqual(lines, [pub_line(k).strip() for k in ('good', 'other')])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.r, 'etc/opos/update/allowed_signers')).st_mode), 0o644)

    def test_signers_given_in_allowed_signers_format_are_accepted(self):
        p = os.path.join(self.tmp, 'signers')
        wr(p, pub_line('good'))
        r = self.build(signers=p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(os.path.join(self.r, 'etc/opos/update/allowed_signers')), pub_line('good'))

    def test_private_keys_and_junk_stop_the_build(self):
        for label, content in (('private key', rd(KEYS['good'])), ('other key type', 'ssh-rsa AAAAB3NzaC1yc2E user\n'),
                               ('junk', 'hello world\n'), ('empty', ''), ('comments only', '# nothing\n\n'), ('principal', 'someone ssh-ed25519 AAAA\n'),
                               ('shell injection', 'ssh-ed25519 AAAA$(touch /tmp/x)\n')):
            with self.subTest(label):
                shutil.rmtree(self.r, ignore_errors=True)
                p = os.path.join(self.tmp, 'bad')
                wr(p, content)
                r = self.build(signers=p)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn('OPOS_UPDATE_SIGNERS', r.stderr)

    def test_missing_signers_file_stops_the_build(self):
        r = self.build(signers=os.path.join(self.tmp, 'nope'))
        self.assertNotEqual(r.returncode, 0)

    def test_build_needs_a_droidian_user_and_a_revision(self):
        r = self.build(passwd='root:x:0:0::/root:/bin/sh\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('droidian', r.stderr)
        shutil.rmtree(self.r)
        self.make_root()
        wr(os.path.join(self.r, 'opt/opos/REVISION'), 'not-a-revision\n')
        script = 'set -eu\nr=%s\nhere=%s\n%s\n%s' % (self.r, PKG, 'chown() { :; }', extract_block(PKG / 'build-image.sh', 'updates'))
        r = subprocess.run(['sh', '-c', script], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('REVISION', r.stderr)

    def test_verify_passes_on_what_the_build_made(self):
        for signers in (None, self.keyfile('good')):
            with self.subTest(signers=signers):
                shutil.rmtree(self.r, ignore_errors=True)
                self.assertEqual(self.build(signers=signers).returncode, 0)
                out, fails = self.verify()
                self.assertEqual(fails, [], out)
                self.assertEqual(len([l for l in out.splitlines() if l.startswith(('PASS OTA', 'FAIL OTA'))]), 9)  # 8 for the updater, 1 for the optional update source

    def test_verify_can_fail(self):
        def link(r):
            os.unlink(os.path.join(r, 'opt/opos'))
            os.makedirs(os.path.join(r, 'opt/opos'))
        mutations = {
            'real /opt/opos directory': link,
            'wrong REVISION': lambda r: wr(os.path.join(r, 'opt/opos-releases/0-0123456789ab/REVISION'), 'f' * 40 + '\n'),
            'absolute symlink': lambda r: (os.unlink(os.path.join(r, 'opt/opos')), os.symlink('/opt/opos-releases/0-0123456789ab', os.path.join(r, 'opt/opos'))),
            'unit not enabled': lambda r: os.unlink(os.path.join(r, 'etc/systemd/system/multi-user.target.wants/opos-update.path')),
            'boot unit not enabled': lambda r: os.unlink(os.path.join(r, 'etc/systemd/system/multi-user.target.wants/opos-update-boot.service')),
            'service enabled directly': lambda r: os.symlink('/etc/systemd/system/opos-update.service', os.path.join(r, 'etc/systemd/system/multi-user.target.wants/opos-update.service')),
            'unit file changed': lambda r: wr(os.path.join(r, 'etc/systemd/system/opos-update.service'), '[Service]\nExecStart=/bin/true\n'),
            'updater missing': lambda r: os.unlink(os.path.join(r, 'usr/libexec/opos-updater/opos-updater')),
            'updater not executable': lambda r: os.chmod(os.path.join(r, 'usr/libexec/opos-updater/opos-updater'), 0o644),
            'updater writable': lambda r: os.chmod(os.path.join(r, 'usr/libexec/opos-updater/opos-updater'), 0o775),
            'updater broken': lambda r: wr(os.path.join(r, 'usr/libexec/opos-updater/opos-updater'), 'def (', 'a'),
            'signers has a private key': lambda r: wr(os.path.join(r, 'etc/opos/update/allowed_signers'), '-----' + 'BEGIN OPENSSH PRIVATE' + ' KEY-----\n'),
            'signers has a stray line': lambda r: wr(os.path.join(r, 'etc/opos/update/allowed_signers'), 'other ssh-ed25519 AAAA\n', 'a'),
            'signers world writable': lambda r: os.chmod(os.path.join(r, 'etc/opos/update/allowed_signers'), 0o666),
            'signers missing': lambda r: os.unlink(os.path.join(r, 'etc/opos/update/allowed_signers')),
            'base-version wrong': lambda r: wr(os.path.join(r, 'etc/opos/base-version'), '2\n'),
            'inbox world writable': lambda r: os.chmod(os.path.join(r, 'var/lib/opos-update/inbox'), 0o777),
            'inbox lost setgid': lambda r: os.chmod(os.path.join(r, 'var/lib/opos-update/inbox'), 0o770),
            'staging open': lambda r: os.chmod(os.path.join(r, 'var/lib/opos-update/staging'), 0o755),
            'path unit watches the directory': lambda r: wr(os.path.join(r, 'etc/systemd/system/opos-update.path'), '[Path]\nDirectoryNotEmpty=/x\n'),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                shutil.rmtree(self.r, ignore_errors=True)
                self.assertEqual(self.build(signers=self.keyfile('good')).returncode, 0)
                mutate(self.r)
                out, fails = self.verify()
                self.assertTrue(fails, 'verify did not notice: ' + label + '\n' + out)

    def test_updater_works_on_the_layout_the_build_makes(self):
        """The build's output is what the updater expects: bootstrap from the symlink, then a signed update applies."""
        self.assertEqual(self.build(signers=self.keyfile('good')).returncode, 0)
        r = self.r
        var = os.path.join(r, 'var/lib/opos-update')
        run = os.path.join(self.tmp, 'run')
        os.makedirs(run)
        wr(os.path.join(r, 'etc/systemd/system/opos-server.service'), 'x')
        stubs = os.path.join(self.tmp, 'bin')
        os.makedirs(stubs)
        for n in ('systemctl', 'pkill'):
            p = os.path.join(stubs, n)
            wr(p, '#!/bin/sh\nexit 0\n')
            os.chmod(p, 0o755)
        cfg = U.Config(root=r, opt_link=os.path.join(r, 'opt/opos'), releases=os.path.join(r, 'opt/opos-releases'), var=var,
                       signers=os.path.join(r, 'etc/opos/update/allowed_signers'), base_version=os.path.join(r, 'etc/opos/base-version'),
                       trusted_uid=os.geteuid(), ssh_keygen=(KEYGEN,), systemctl=(os.path.join(stubs, 'systemctl'),),
                       pkill=(os.path.join(stubs, 'pkill'),), smoke=lambda c, d: (True, 'stub'), health=lambda c: (True, 'ok'),
                       statvfs=lambda p: 10**12, sync=lambda: None, log=lambda m: None)
        u = U.Updater(cfg)
        self.assertEqual(u.apply_pending_boot(), 0)
        st = json.loads(rd(os.path.join(var, 'state.json')))
        self.assertEqual((st['current']['serial'], st['current']['release'], st['current']['revision']),
                         (0, '0-0123456789ab', '0123456789abcdef0123456789abcdef01234567'))
        wr(os.path.join(var, 'inbox', 'opos-1.opos-update'), make_bundle(1, overlay={}), 'wb')
        self.assertEqual(U.Updater(cfg).apply_inbox(), 0)
        self.assertEqual(os.readlink(os.path.join(r, 'opt/opos')), 'opos-releases/' + release_name(1))
        self.assertEqual(rd(os.path.join(r, 'opt/opos/src/opos/marker.txt')), 'build 1\n')


if __name__ == '__main__':
    unittest.main()
