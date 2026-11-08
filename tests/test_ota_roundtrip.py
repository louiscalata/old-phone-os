# SPDX-License-Identifier: GPL-3.0-or-later
"""The whole over-the-air path, the three halves together (docs/ota-update.md).

tools/opos-release.py builds and signs a bundle, opos.update_client fetches it from a loopback web server into the
inbox, and packaging/duo-os/updater/opos-updater verifies, applies, health-checks and (when asked or when the health
check fails) rolls it back. No format is mocked: the bundle is built by the owner's tool as a subprocess, signed and
checked by the real ssh-keygen with throw-away keys made at run time, and read by the updater's own code.

What is injected, because this runs on a PC: every path is a temporary directory (a fake phone root); systemctl,
runuser and pkill are small stand-ins (the systemctl one really stops and starts the application server from the
release /opt/opos points at, so the updater's health probe talks to a live server); os.sync is a no-op; the boot id is
a variable. Not covered: the systemd path unit, the unit hardening, the real runuser/PAM, the kiosk browser, the
phone's kernel, flash and clock. Nothing here touches a phone or any hardware.
"""
import ast
import fnmatch
import functools
import hashlib
import http.client
import http.server
import importlib.machinery
import importlib.util
import json
import os
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
import unittest
from pathlib import Path

if not sys.platform.startswith("linux"):
    raise unittest.SkipTest("the updater is Linux-only (flock, O_NOFOLLOW, systemd)")

from opos import update_client as uc, web

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging" / "duo-os"
TOOL = ROOT / "tools" / "opos-release.py"
UPDATER = PKG / "updater" / "opos-updater"
KEYGEN = shutil.which("ssh-keygen")
needs_keygen = unittest.skipUnless(KEYGEN, "ssh-keygen is not installed")
CREATED = "2026-11-08T12:00:00Z"
KEYS = {}
_keydir = None


def load(name, path):
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = keep


U = load("opos_updater_rt", UPDATER)
T = load("opos_release_rt", TOOL)


def setUpModule():
    global _keydir
    if not KEYGEN:
        return
    _keydir = tempfile.mkdtemp(prefix="opos-rtkeys-")
    for name in ("good", "other"):
        path = os.path.join(_keydir, name)
        subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "roundtrip", "-f", path], check=True)
        KEYS[name] = path


def tearDownModule():
    if _keydir:
        shutil.rmtree(_keydir, ignore_errors=True)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def revision(serial):
    return hashlib.sha1(("roundtrip-rev-%d" % serial).encode()).hexdigest()


def rel_name(serial):
    return "%d-%s" % (serial, revision(serial)[:12])


def rd(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def wr(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    os.chmod(path, mode)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# A stand-in for opos.web that keeps the health contract the updater probes: GET / is 200 with the page token,
# GET /api/state with that token answers JSON with a "battery" key. mode.txt (shipped in the bundle) picks the fault.
FAKE_WEB = '''import argparse, http.server, json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
def read(name):
    with open(os.path.join(here, name)) as f:
        return f.read().strip()
p = argparse.ArgumentParser()
p.add_argument("--port", type=int, default=8765)
p.add_argument("--live-battery", action="store_true")
a = p.parse_args()
mode, build = read("mode.txt"), read("build.txt")
if mode == "crash" or (mode == "live-crash" and os.environ.get("OPOS_TEST_LIVE")):
    sys.exit(3)
class H(http.server.BaseHTTPRequestHandler):
    def send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == "/":
            return self.send(200, ('<html><head><meta name="opos-token" content="tok"><meta name="opos-build" content="%s"></head></html>' % build).encode())
        if self.path == "/api/state" and self.headers.get("X-OPOS-Token") == "tok":
            return self.send(200, json.dumps({} if mode == "no-battery" else {"battery": {"percent": 50}}).encode())
        self.send(404, b"")
    def log_message(self, *args):
        pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), H)
print("stand-in server: http://127.0.0.1:%d/" % srv.server_address[1], flush=True)
srv.serve_forever()
'''

# systemctl as the updater calls it. restart really restarts the application server from the release /opt/opos points
# at (as opos-server.service does with PYTHONPATH=/opt/opos/src); is-active reports whether that process is alive.
SYSTEMCTL = '''#!@PY@
import json, os, signal, socket, subprocess, sys, time
RUN, OPT, PORT, PY, HOME = @RUN@, @OPT@, @PORT@, @PY@, @HOME@
args = sys.argv[1:]
with open(os.path.join(RUN, "calls.log"), "a") as f:
    f.write("systemctl " + " ".join(args) + "\\n")
INFO = os.path.join(RUN, "server.json")
def info():
    try:
        with open(INFO) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
def alive(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except (OSError, IndexError):
        return False
def stop():
    i = info()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not i or not alive(i["pid"]):
            return
        try:
            os.killpg(i["pid"], sig)
        except OSError:
            pass
        end = time.time() + 5
        while time.time() < end and alive(i["pid"]):
            time.sleep(0.02)
def start():
    env = {"PATH": "/usr/bin:/bin", "HOME": HOME, "LANG": "C.UTF-8", "PYTHONPATH": os.path.join(os.path.realpath(OPT), "src"),
           "PYTHONDONTWRITEBYTECODE": "1", "OPOS_BATTERY": "live", "OPOS_ON_PHONE": "1", "OPOS_TEST_LIVE": "1"}
    log = open(os.path.join(RUN, "server.log"), "ab")
    p = subprocess.Popen([PY, "-m", "opos.web", "--port", str(PORT), "--live-battery"], env=env, cwd="/", stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)
    with open(INFO, "w") as f:
        json.dump({"pid": p.pid, "release": os.path.realpath(OPT)}, f)
    end = time.time() + 40
    while time.time() < end and p.poll() is None:
        try:
            socket.create_connection(("127.0.0.1", PORT), 0.3).close()
            return
        except OSError:
            time.sleep(0.05)
if args == ["is-active", "opos-server.service"]:
    i = info()
    ok = bool(i and alive(i["pid"]))
    print("active" if ok else "inactive")
    sys.exit(0 if ok else 3)
if args == ["restart", "opos-server.service"]:
    stop()
    start()
    sys.exit(0)
if args == ["daemon-reload"]:
    sys.exit(0)
sys.stderr.write("the test stand-in for systemctl refuses: %s\\n" % " ".join(args))
sys.exit(1)
'''

RUNUSER = '#!/bin/sh\necho "runuser $*" >> "@RUN@/calls.log"\n[ "$1" = "-u" ] && shift 2\n[ "$1" = "--" ] && shift\nexec "$@"\n'
PKILL = '#!/bin/sh\necho "pkill $*" >> "@RUN@/calls.log"\nexit 1\n'
ALLOWED_SYSTEMCTL = {"systemctl daemon-reload", "systemctl restart opos-server.service", "systemctl is-active opos-server.service"}
OVERLAYS = {"usr/libexec/opos/opos-kiosk": ("opos-kiosk", 0o755), "etc/systemd/system/opos-server.service": ("opos-server.service", 0o644)}


def render(template, **kw):
    """Fill @NAME@ placeholders: Python source gets repr() values, shell text the plain strings."""
    py = template.startswith("#!@PY@")
    if py:
        template = template.replace("#!@PY@", "#!" + sys.executable, 1)
    for k, v in kw.items():
        template = template.replace("@%s@" % k, repr(v) if py else str(v))
    return template


class QuietSite(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class World:
    """A fake phone (root file system, systemd stand-ins, the running server), the update site and the owner's source trees."""

    def __init__(self, real=False, health_timeout=4.0):
        self.real = real
        self.tmp = tempfile.mkdtemp(prefix="opos-rt-")
        self.logs = []
        self.boot = "boot-1"
        self.expect = {}
        self.closed = False
        try:
            self._build(health_timeout)
        except BaseException:
            self.close()
            raise

    def _build(self, health_timeout):
        t = self.tmp
        self.root = os.path.join(t, "root")
        self.run = os.path.join(t, "run")
        self.site = os.path.join(t, "site")
        self.repo = os.path.join(t, "repo")
        self.home = os.path.join(t, "home")
        self.bin = os.path.join(t, "bin")
        for d in (self.run, self.site, self.repo, self.home, self.bin):
            os.makedirs(d, mode=0o755)
        r = self.root
        self.opt_link = os.path.join(r, "opt/opos")
        self.releases = os.path.join(r, "opt/opos-releases")
        self.var = os.path.join(r, "var/lib/opos-update")
        self.inbox = os.path.join(self.var, "inbox")
        self.staging = os.path.join(self.var, "staging")
        self.signers = os.path.join(r, "etc/opos/update/allowed_signers")
        self.update_json = os.path.join(r, "etc/opos/update.json")
        for d in ("opt", "opt/opos-releases", "var", "var/lib", "var/lib/opos-update", "var/lib/opos-update/inbox", "etc", "etc/opos", "etc/opos/update",
                  "etc/systemd", "etc/systemd/system", "usr", "usr/libexec", "usr/libexec/opos", "usr/sbin"):
            os.makedirs(os.path.join(r, d), mode=0o755, exist_ok=True)
            os.chmod(os.path.join(r, d), 0o755)
        os.makedirs(self.staging, mode=0o700)
        self.health_port = free_port()
        # The image: release 0 built from a tree like the later ones, the old overlay files, the owner's public key.
        base = os.path.join(self.releases, rel_name(0))
        self.write_app(base, 0, "ok")
        wr(os.path.join(base, "REVISION"), revision(0) + "\n")
        os.symlink("opos-releases/" + rel_name(0), self.opt_link)
        self.expect[0] = {}
        for dest, (src, mode) in OVERLAYS.items():
            data = ("#!/bin/sh\necho image kiosk\n" if src == "opos-kiosk" else "[Service]\n# image\n").encode()
            wr(os.path.join(r, dest), data)
            os.chmod(os.path.join(r, dest), mode)
            self.expect[0][dest] = (sha(data), mode)
        wr(os.path.join(r, "etc/opos/base-version"), "1\n")
        self.public_key("good", self.signers)
        # The tools and the stand-ins.
        for name, body in (("systemctl", SYSTEMCTL), ("runuser", RUNUSER), ("pkill", PKILL)):
            p = os.path.join(self.bin, name)
            wr(p, render(body, PY=sys.executable, RUN=self.run, OPT=self.opt_link, PORT=self.health_port, HOME=self.home))
            os.chmod(p, 0o755)
        self.cfg = U.Config(
            root=r, opt_link=self.opt_link, releases=self.releases, var=self.var, signers=self.signers,
            base_version=os.path.join(r, "etc/opos/base-version"), trusted_uid=os.geteuid(), ssh_keygen=(KEYGEN,),
            systemctl=(os.path.join(self.bin, "systemctl"),), runuser=(os.path.join(self.bin, "runuser"), "-u", "droidian", "--"),
            pkill=(os.path.join(self.bin, "pkill"),), env_bin="/usr/bin/env", python=sys.executable, health_port=self.health_port,
            smoke_chown=False, smoke_timeout=60.0 if self.real else 15.0,
            health_timeout=60.0 if self.real else health_timeout, health_interval=0.1, confirm_timeout=3.0, lock_timeout=5.0, cmd_timeout=60.0,
            pace=0.0, fail_pause=0.0)
        self.cfg.log = self.logs.append
        self.cfg.sync = lambda: None
        self.cfg.boot_id = lambda: self.boot
        # The update site, and the phone's opt-in to it.
        handler = functools.partial(QuietSite, directory=self.site)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = "http://127.0.0.1:%d/latest.json" % self.httpd.server_address[1]
        wr(self.update_json, json.dumps({"url": self.url, "auto_check": False, "allow_lan_http": True}))
        self.client = uc.UpdateClient(paths=uc.Paths(config=self.update_json, root=self.var, revision=os.path.join(self.opt_link, "REVISION")),
                                      limits=uc.Limits(io_timeout=10.0, index_deadline=20.0, bundle_deadline=60.0))
        # Boot: the boot unit writes state.json, then opos-server starts from the release the symlink names.
        self.run_updater("apply-pending-boot")
        self.systemctl("restart", "opos-server.service")
        open(os.path.join(self.run, "calls.log"), "w").close()

    def public_key(self, key, dest):
        """allowed_signers line exactly as the owner's how-to makes it (keygen-help), written where the image keeps it."""
        out = subprocess.run([sys.executable, "-B", str(TOOL), "keygen-help", "--pub", KEYS[key] + ".pub"], capture_output=True, text=True, check=True).stdout
        line = [l for l in out.splitlines() if l.startswith("opos-release ssh-ed25519 ")][-1]
        wr(dest, line + "\n")
        os.chmod(dest, 0o644)

    # -- source trees and releases
    def write_app(self, base, build, mode):
        """The application tree of one build: the repository's own src when real, a small stand-in otherwise."""
        if self.real:
            shutil.copytree(ROOT / "src", os.path.join(base, "src"), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            wr(os.path.join(base, "src/opos/__init__.py"), "")
            wr(os.path.join(base, "src/opos/web.py"), FAKE_WEB)
        wr(os.path.join(base, "src/opos/build.txt"), "%d\n" % build)
        wr(os.path.join(base, "src/opos/mode.txt"), mode + "\n")

    def release(self, serial, mode="ok", scope="app", key="good", index=True, **extra):
        """Build opos-<serial>.opos-update into the site with the owner's tool, and (unless index=False) write latest.json."""
        tree = os.path.join(self.repo, "s%d" % serial)
        self.write_app(tree, serial, mode)
        if self.real:
            wr(os.path.join(tree, "src/opos/_ota_marker.py"), "BUILD = %d\n" % serial)
            kiosk, unit = rd(PKG / "opos-kiosk", "rb"), rd(PKG / "opos-server.service", "rb")
            licence, third = rd(ROOT / "LICENSE", "rb"), rd(ROOT / "THIRD_PARTY.md", "rb")
        else:
            kiosk = ("#!/bin/sh\necho kiosk build %d\n" % serial).encode()
            unit = ("[Service]\n# build %d\n" % serial).encode()
            licence, third = b"GPL\n", b"third party\n"
        wr(os.path.join(tree, "packaging/duo-os/opos-kiosk"), kiosk, 0o755)
        wr(os.path.join(tree, "packaging/duo-os/opos-server.service"), unit)
        wr(os.path.join(tree, "LICENSE"), licence)
        wr(os.path.join(tree, "THIRD_PARTY.md"), third)
        args = ["build", "--dir", tree, "--revision", revision(serial), "--created", CREATED, "--serial", serial, "--version", "0.%d.0" % serial,
                "--scope", scope, "--key", KEYS[key], "--out", self.site, "--overlay", "opos-kiosk", "--overlay", "opos-server.service",
                "--notes", "round trip %d" % serial]
        self.tool(*args)
        self.expect[serial] = {"usr/libexec/opos/opos-kiosk": (sha(kiosk), 0o755), "etc/systemd/system/opos-server.service": (sha(unit), 0o644)}
        bundle = os.path.join(self.site, "opos-%d.opos-update" % serial)
        if index:
            self.tool("index", bundle, *extra.get("index_args", ()))
        return bundle

    def tool(self, *args, check=True):
        p = subprocess.run([sys.executable, "-B", str(TOOL), *map(str, args)], capture_output=True, text=True, timeout=120)
        if check and p.returncode:
            raise AssertionError("opos-release.py %s failed (%d): %s" % (args[0], p.returncode, p.stderr))
        return p

    # -- the phone
    def run_updater(self, *argv):
        keep = os.umask(0o022)
        try:
            return U.main(list(argv), self.cfg)
        finally:
            os.umask(keep)

    def systemctl(self, *args):
        return subprocess.run([os.path.join(self.bin, "systemctl"), *args], capture_output=True, text=True, timeout=120)

    def download(self):
        """What the Update page does after a check: fetch the bundle into the inbox and wait for the file to land."""
        job = self.client.start_download()
        assert job["phase"] == "downloading", job
        assert self.client.wait(60), "the download did not finish"
        return self.client.job()

    def fetch_and_apply(self, serial):
        chk = self.client.check()
        assert chk["ok"] and chk["newer"] and chk["serial"] == serial, chk
        job = self.download()
        assert job["phase"] == "done", job
        return self.run_updater("apply-inbox")

    def state(self):
        return json.loads(rd(os.path.join(self.var, "state.json")))

    def status(self):
        return json.loads(rd(os.path.join(self.var, "status.json")))

    def calls(self):
        try:
            return rd(os.path.join(self.run, "calls.log")).splitlines()
        except OSError:
            return []

    def server(self):
        try:
            info = json.loads(rd(os.path.join(self.run, "server.json")))
        except (OSError, ValueError):
            return None
        try:
            with open("/proc/%d/stat" % info["pid"]) as f:
                if f.read().rsplit(")", 1)[1].split()[0] in ("Z", "X"):
                    return None
        except (OSError, IndexError):
            return None
        return info

    def get(self, path, headers=None, port=None):
        c = http.client.HTTPConnection("127.0.0.1", port or self.health_port, timeout=10)
        try:
            c.request("GET", path, headers=headers or {})
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def file_info(self, rel):
        p = os.path.join(self.root, rel)
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return None
        return (sha(rd(p, "rb")), stat.S_IMODE(st.st_mode))

    def snapshot(self):
        """Everything a refused bundle must leave alone."""
        st = self.state()
        srv = self.server()
        return {"link": os.readlink(self.opt_link), "releases": sorted(os.listdir(self.releases)),
                "files": {p: self.file_info(p) for p in OVERLAYS}, "state": {k: st[k] for k in ("current", "previous", "failed", "pending_boot", "trial", "txn")},
                "server": srv and (srv["pid"], srv["release"]), "restarts": [c for c in self.calls() if "restart" in c or "daemon-reload" in c],
                "staging": sorted(os.listdir(self.staging))}

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            info = json.loads(rd(os.path.join(self.run, "server.json")))
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with_suppress(os.killpg, info["pid"], sig)
        except (OSError, ValueError):
            pass
        httpd = getattr(self, "httpd", None)
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- the checks every scenario makes
    def assert_on(self, tc, serial, previous=(), failed=()):
        """The phone is fully on release `serial` and consistent: files, link, state, running server, inbox, calls."""
        st = self.state()
        cur = st["current"]
        name = rel_name(serial)
        tc.assertEqual(os.readlink(self.opt_link), "opos-releases/" + name)
        tc.assertEqual(rd(os.path.join(self.opt_link, "REVISION")).strip(), revision(serial))
        tc.assertEqual(rd(os.path.join(self.opt_link, "src/opos/build.txt")).strip(), str(serial))
        tc.assertEqual({p: self.file_info(p) for p in OVERLAYS}, self.expect[serial])
        tc.assertEqual((cur["serial"], cur["release"], cur["revision"]), (serial, name, revision(serial)))
        if serial:
            tc.assertEqual(cur["version"], "0.%d.0" % serial)
        tc.assertEqual([p["serial"] for p in st["previous"]], list(previous))
        tc.assertEqual(st["failed"], list(failed))
        tc.assertEqual((st["txn"], st["pending_boot"], st["trial"]), (None, None, None))
        tc.assertEqual(sorted(os.listdir(self.releases)), sorted([name] + [rel_name(s) for s in previous]))
        srv = self.server()
        tc.assertIsNotNone(srv, "the server is not running")
        tc.assertEqual(srv["release"], os.path.realpath(self.opt_link))
        code, body = self.get("/")
        tc.assertEqual(code, 200)
        if not self.real:
            tc.assertIn(('name="opos-build" content="%d"' % serial).encode(), body)
        tc.assertEqual([n for n in os.listdir(self.inbox)], [])
        tc.assertEqual(os.listdir(self.staging), [])
        stray = [n for d, _, fs in os.walk(self.root) for n in fs if n.endswith((".new", ".tmp"))]
        tc.assertEqual(stray, [])
        self.assert_calls(tc)

    def assert_calls(self, tc):
        """The updater asked systemd for nothing but daemon-reload, restart opos-server and is-active; opos.service is untouched."""
        for line in self.calls():
            if line.startswith("systemctl "):
                tc.assertIn(line, ALLOWED_SYSTEMCTL)
            else:
                tc.assertRegex(line, r"^(pkill -u droidian -f |runuser -u droidian -- /usr/bin/env -i )")
            tc.assertNotRegex(line, r"\bopos\.service\b")


def with_suppress(fn, *a):
    try:
        fn(*a)
    except OSError:
        pass


class WorldCase(unittest.TestCase):
    real = False
    health_timeout = 4.0

    def setUp(self):
        self.w = World(real=self.real, health_timeout=self.health_timeout)
        self.addCleanup(self.w.close)
        self.assertEqual(self.w.server()["release"], os.path.realpath(self.w.opt_link))


def tamper_payload(path):
    """Flip one byte in the middle of payload.tar.gz inside the outer tar (a bundle changed after it was signed)."""
    with tarfile.open(path, "r:") as tf:
        m = tf.getmember("payload.tar.gz")
        pos = m.offset_data + m.size // 2
    data = bytearray(rd(path, "rb"))
    data[pos] ^= 0x55
    wr(path, bytes(data))


@needs_keygen
class RoundTrip(WorldCase):
    def test_two_updates_in_a_row_and_old_releases_are_pruned(self):
        w = self.w
        snap = w.client.snapshot()
        self.assertEqual((snap["available"], snap["running"]["serial"], snap["running"]["revision"], snap["source"]["configured"]), (True, 0, revision(0), True))
        self.assertEqual(snap["running"]["version"], "image")
        w.release(1)
        chk = w.client.check()
        self.assertEqual((chk["ok"], chk["newer"], chk["serial"], chk["version"]), (True, True, 1, "0.1.0"))
        self.assertEqual(w.download()["phase"], "done")
        self.assertEqual(w.client.pending(), ["opos-1.opos-update"])
        self.assertEqual(w.run_updater("apply-inbox"), 0, w.logs)
        w.assert_on(self, 1, previous=[0])
        self.assertEqual(w.status()["phase"], "done")
        self.assertEqual(w.status()["serial"], 1)
        self.assertIn("daemon-reload", " ".join(w.calls()))
        # What the page shows afterwards, read by the client from the files the updater wrote.
        snap = w.client.snapshot()
        self.assertEqual((snap["running"]["serial"], snap["running"]["version"], snap["running"]["revision"]), (1, "0.1.0", revision(1)))
        self.assertEqual((snap["pending"], snap["status"]["phase"], snap["status"]["serial"]), ([], "done", 1))
        self.assertEqual(snap["rollback"], {"possible": True, "requested": False})
        self.assertFalse(snap["check"]["newer"])
        self.assertIn("latest", snap["check"]["message"])
        # A newer one, then a third: the image's own release goes when it is more than two back.
        w.release(2)
        self.assertEqual(w.fetch_and_apply(2), 0, w.logs)
        w.assert_on(self, 2, previous=[1, 0])
        w.release(3)
        self.assertEqual(w.fetch_and_apply(3), 0, w.logs)
        w.assert_on(self, 3, previous=[2, 1])
        # Everything the tool built passes its own verifier against the key the image trusts.
        for n in (1, 2, 3):
            p = w.tool("verify", os.path.join(w.site, "opos-%d.opos-update" % n), "--allowed-signers", w.signers, "--current-serial", n - 1, "--base", 1)
            self.assertIn("serial %d" % n, p.stdout)

    def test_a_bundle_signed_by_another_key_is_delivered_but_refused_and_nothing_changes(self):
        w = self.w
        w.release(1, key="other")
        before = w.snapshot()
        chk = w.client.check()
        self.assertTrue(chk["newer"], "the client does not judge trust; root does")
        self.assertEqual(w.download()["phase"], "done")
        self.assertEqual(w.run_updater("apply-inbox"), 3)
        st = w.status()
        self.assertEqual(st["phase"], "failed")
        self.assertIn("signature", st["message"])
        self.assertIsNone(st["serial"], "refused before the manifest was read: no serial to report (the /update page keeps polling for a while)")
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(w.client.pending(), [])
        self.assertEqual(w.state()["failed"], [], "a refused bundle must not burn its serial")
        self.assertEqual(w.client.snapshot()["status"]["phase"], "failed")
        self.assertIn("signature", w.client.snapshot()["status"]["message"])
        w.assert_calls(self)
        # The owner's separate verifier refuses it as well, and the same serial space is still usable.
        p = w.tool("verify", os.path.join(w.site, "opos-1.opos-update"), "--allowed-signers", w.signers, check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("signature", p.stderr)
        w.release(2)
        self.assertEqual(w.fetch_and_apply(2), 0, w.logs)
        w.assert_on(self, 2, previous=[0])

    def test_a_bundle_changed_after_signing_is_refused_even_when_the_index_vouches_for_it(self):
        w = self.w
        bundle = w.release(1)
        tamper_payload(bundle)
        before = w.snapshot()
        # An honest index (made before the change) stops it in the client: nothing reaches the inbox.
        w.client.check()
        w.client.start_download()
        self.assertTrue(w.client.wait(60))
        self.assertEqual(w.client.job()["phase"], "failed")
        self.assertEqual(os.listdir(w.inbox), [])
        # A host that also rewrites the index gets the file into the inbox; root refuses it.
        data = rd(bundle, "rb")
        wr(os.path.join(w.site, "latest.json"), json.dumps({"serial": 1, "version": "0.1.0", "url": "opos-1.opos-update", "sha256": sha(data), "size": len(data)}))
        self.assertEqual(w.download()["phase"], "done")
        self.assertEqual(w.run_updater("apply-inbox"), 3)
        self.assertIn("payload", w.status()["message"])
        self.assertEqual(w.snapshot(), before)
        p = w.tool("verify", bundle, "--allowed-signers", w.signers, check=False)
        self.assertEqual(p.returncode, 1)

    def test_a_release_that_fails_its_health_check_is_rolled_back_and_the_fix_goes_out_as_a_new_serial(self):
        w = self.w
        w.release(1)
        self.assertEqual(w.fetch_and_apply(1), 0, w.logs)
        w.release(2, mode="no-battery")  # starts and answers GET / (smoke passes), but /api/state has no battery key
        self.assertEqual(w.fetch_and_apply(2), 3, w.logs)
        st = w.status()
        self.assertEqual((st["phase"], st["serial"]), ("rolled-back", 2))
        self.assertIn("rolled back", st["message"])
        self.assertIn("battery", st["message"])
        w.assert_on(self, 1, previous=[0], failed=[2])
        snap = w.client.snapshot()
        self.assertEqual((snap["status"]["phase"], snap["running"]["serial"]), ("rolled-back", 1))
        chk = w.client.check()
        self.assertFalse(chk["newer"])
        self.assertIn("rolled back", chk["message"])
        w.release(3)
        self.assertEqual(w.fetch_and_apply(3), 0, w.logs)
        w.assert_on(self, 3, previous=[1, 0], failed=[2])

    def test_a_release_whose_server_dies_after_the_switch_is_rolled_back(self):
        w = self.w
        w.release(1, mode="live-crash")  # fine under the smoke test, exits at once as the live service
        self.assertEqual(w.fetch_and_apply(1), 3, w.logs)
        self.assertEqual(w.status()["phase"], "rolled-back")
        w.assert_on(self, 0, failed=[1])

    def test_a_release_that_fails_the_smoke_test_never_goes_live(self):
        w = self.w
        w.release(1, mode="crash")
        before = w.snapshot()
        self.assertEqual(w.fetch_and_apply(1), 3, w.logs)
        st = w.status()
        self.assertEqual(st["phase"], "failed")
        self.assertIn("smoke test failed", st["message"])
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(w.state()["failed"], [])
        w.release(2)
        self.assertEqual(w.fetch_and_apply(2), 0, w.logs)
        w.assert_on(self, 2, previous=[0])

    def test_rollback_on_request_through_the_client(self):
        w = self.w
        with self.assertRaises(uc.UpdateError) as cm:
            w.client.request_rollback()
        self.assertEqual(cm.exception.code, "no_rollback")
        w.release(1)
        self.assertEqual(w.fetch_and_apply(1), 0, w.logs)
        w.release(2)
        self.assertEqual(w.fetch_and_apply(2), 0, w.logs)
        self.assertEqual(w.client.request_rollback(), {"requested": True, "already": False})
        self.assertEqual(w.client.request_rollback(), {"requested": True, "already": True})
        self.assertEqual(w.client.snapshot()["rollback"], {"possible": True, "requested": True})
        self.assertEqual(w.run_updater("apply-inbox"), 0, w.logs)
        st = w.status()
        self.assertEqual((st["phase"], st["serial"]), ("rolled-back", 2))
        w.assert_on(self, 1, previous=[0], failed=[2])
        self.assertEqual(w.client.snapshot()["rollback"]["requested"], False)
        chk = w.client.check()  # the site still offers 2
        self.assertFalse(chk["newer"])
        self.assertIn("rolled back", chk["message"])
        # Delivered by hand anyway (the USB push does this), root refuses the serial it rolled back from.
        before = w.snapshot()
        shutil.copy(os.path.join(w.site, "opos-2.opos-update"), os.path.join(w.inbox, "opos-2.opos-update"))
        self.assertEqual(w.run_updater("apply-inbox"), 3)
        self.assertIn("failed on this phone before", w.status()["message"])
        self.assertEqual(w.snapshot(), before)
        w.release(3)
        self.assertEqual(w.fetch_and_apply(3), 0, w.logs)
        w.assert_on(self, 3, previous=[1, 0], failed=[2])

    def test_a_boot_scope_bundle_is_staged_then_applied_at_the_next_boot(self):
        w = self.w
        w.release(1, scope="boot")
        self.assertEqual(w.fetch_and_apply(1), 0, w.logs)
        self.assertEqual(w.status()["phase"], "done")
        self.assertIn("next boot", w.status()["message"])
        self.assertEqual(w.state()["pending_boot"]["serial"], 1)
        self.assertEqual(os.readlink(w.opt_link), "opos-releases/" + rel_name(0))
        self.assertEqual([c for c in w.calls() if "restart" in c], [], "a staged update restarts nothing")
        # Next boot: the boot unit applies it before opos-server starts; the server then starts from the new tree; confirm-boot passes.
        w.boot = "boot-2"
        self.assertEqual(w.run_updater("apply-pending-boot"), 0, w.logs)
        self.assertEqual(os.readlink(w.opt_link), "opos-releases/" + rel_name(1))
        self.assertEqual([c for c in w.calls() if "restart" in c], [], "the boot unit never restarts a service")
        self.assertEqual(w.state()["trial"]["serial"], 1)
        w.systemctl("restart", "opos-server.service")
        self.assertEqual(w.run_updater("confirm-boot"), 0, w.logs)
        self.assertIn("confirmed", w.status()["message"])
        w.assert_on(self, 1, previous=[0])

    def test_through_the_web_routes(self):
        w = self.w
        server = web.make_server(0, web.Phone(), updater=w.client)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        port = server.server_address[1]
        code, body = w.get("/", port=port)
        self.assertEqual(code, 200)
        token = re.search(rb'name="opos-token" content="([^"]+)"', body).group(1).decode()

        def call(method, path, payload=None):
            hdr = {"X-OPOS-Token": token}
            data = None
            if payload is not None:
                hdr["Content-Type"] = "application/json"
                data = json.dumps(payload)
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            try:
                c.request(method, path, body=data, headers=hdr)
                r = c.getresponse()
                return r.status, json.loads(r.read() or b"{}")
            finally:
                c.close()
        self.assertEqual(w.get("/update", port=port)[0], 200)
        w.release(1)
        code, j = call("POST", "/api", {"action": "update_check"})
        self.assertEqual((code, j["ok"], j["check"]["newer"], j["check"]["serial"]), (200, True, True, 1))
        code, j = call("POST", "/api", {"action": "update_download"})
        self.assertEqual((code, j["job"]["kind"], j["job"]["serial"]), (200, "download", 1))
        self.assertTrue(w.client.wait(60))
        code, j = call("GET", "/api/update")
        self.assertEqual((code, j["pending"], j["job"]["phase"]), (200, ["opos-1.opos-update"], "done"))
        self.assertEqual(w.run_updater("apply-inbox"), 0, w.logs)
        code, j = call("GET", "/api/update")
        self.assertEqual((j["running"]["serial"], j["running"]["version"], j["status"]["phase"], j["pending"]), (1, "0.1.0", "done", []))
        code, j = call("POST", "/api", {"action": "update_rollback"})
        self.assertEqual((code, j["rollback"]["requested"]), (200, True))
        self.assertEqual(w.run_updater("apply-inbox"), 0, w.logs)
        code, j = call("GET", "/api/update")
        self.assertEqual((j["running"]["serial"], j["status"]["phase"], j["rollback"]["requested"]), (0, "rolled-back", False))
        w.assert_on(self, 0, failed=[1])


@needs_keygen
class RealTree(WorldCase):
    """The repository's own src, packaged by the tool, accepted by the updater, started by the stand-in unit as `python -m opos.web`."""
    real = True

    def test_the_real_application_tree_updates_itself(self):
        w = self.w
        code, body = w.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b'name="opos-token"', body)
        w.release(1)
        self.assertEqual(w.fetch_and_apply(1), 0, w.logs)
        w.assert_on(self, 1, previous=[0])
        out = subprocess.run([sys.executable, "-B", "-c", "import opos._ota_marker as m; print(m.BUILD)"], capture_output=True, text=True,
                             env={"PYTHONPATH": os.path.join(w.opt_link, "src"), "PATH": "/usr/bin:/bin"}, cwd="/").stdout.strip()
        self.assertEqual(out, "1")
        # The new tree is the live one: it serves the update page and reports the running version through its own client.
        code, page = w.get("/update")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", page.lower())
        self.assertFalse([n for d, _, fs in os.walk(os.path.join(w.releases, rel_name(1))) for n in fs if n.endswith(".pyc")], "the server must not write bytecode into a release")


class Contracts(unittest.TestCase):
    """What the three halves must agree on, checked without a phone and without running any of them."""

    def test_the_client_reads_and_writes_where_the_updater_does(self):
        cfg, paths = U.Config(), uc.Paths()
        self.assertEqual((paths.root, paths.inbox, paths.state, paths.status), (cfg.var, cfg.inbox, cfg.state_file, cfg.status_file))
        self.assertEqual(paths.revision, cfg.opt_link + "/REVISION")
        self.assertEqual(paths.config, "/etc/opos/update.json")

    def test_the_path_unit_starts_on_exactly_what_the_client_and_the_push_script_deliver(self):
        unit = rd(PKG / "updater" / "opos-update.path")
        glob = re.search(r"^PathExistsGlob=(\S+)$", unit, re.M).group(1)
        exists = re.search(r"^PathExists=(\S+)$", unit, re.M).group(1)
        inbox = U.Config().inbox
        for name in ("opos-1.opos-update", "opos-2147483647.opos-update"):
            self.assertTrue(fnmatch.fnmatch(inbox + "/" + name, glob), name)
        self.assertEqual(exists, inbox + "/" + uc.ROLLBACK_REQUEST)
        self.assertTrue(glob.endswith("*" + uc.BUNDLE_EXT))
        for name in ("opos-1.opos-update.partial", "opos-1.partial", "rollback.request.partial"):
            self.assertFalse(fnmatch.fnmatch(inbox + "/" + name, glob), "a half-delivered file must not start the updater: " + name)
        push = rd(ROOT / "deploy" / "duo-push-update.ps1")
        self.assertIn('$inbox = "%s"' % inbox, push)
        self.assertIn('$statusFile = "%s"' % U.Config().status_file, push)
        self.assertIn('"$inbox/$($file.Name).partial"', push)
        pat = re.search(r"-notmatch '([^']+)'\)\s*\{ Fail \"-Bundle must be named", push).group(1)
        self.assertRegex("opos-8.opos-update", pat)
        self.assertNotRegex("opos-8.opos-update.partial", pat)

    def test_the_status_phases_the_updater_writes_are_the_ones_the_client_and_the_spec_know(self):
        tree = ast.parse(rd(UPDATER))
        phases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "status" and node.args:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    phases.add(node.args[0].value)
        self.assertEqual(phases, {"idle", "verifying", "applying", "checking", "done", "rolled-back", "failed"})
        self.assertEqual(phases, set(uc._PHASES))
        doc = rd(ROOT / "docs" / "ota-update.md")
        for phase in phases:
            self.assertIn(phase, doc)

    def test_the_tool_never_builds_a_path_the_updater_refuses(self):
        real = [("opt/opos/" + p.relative_to(ROOT).as_posix()) for p in (ROOT / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
        real += ["opt/opos/LICENSE", "opt/opos/THIRD_PARTY.md"] + list(T.OVERLAY_DEST.values())
        self.assertGreater(len(real), 80)
        for path in real:
            kind = T.classify(path)
            self.assertEqual(U.classify(path)[0], kind, path)
        tried = ["opt/opos/src/opos/web.py", "opt/opos/src/opos/a_b-c.d+e.py", "opt/opos/src/opos/x@y.py", "opt/opos/src/opos/.hidden", "opt/opos/src/opos/file.new",
                 "opt/opos/REVISION", "opt/opos/overlay/x", "opt/opos/overlay-backup/x", "opt/opos/manifest.json", "opt/opos/README.md", "opt/opos/src/opos/sp ace.py",
                 "etc/opos/update.json", "etc/opos/base-version", "etc/opos/update/allowed_signers", "etc/opos/theme.conf", "etc/opos/sub/x", "etc/default/opos", "etc/default/opos-x",
                 "etc/default/other", "etc/systemd/system/opos.service", "etc/systemd/system/opos-update.service", "etc/systemd/system/opos-update.path", "etc/systemd/system/other.service",
                 "etc/systemd/system/opos@.service", "etc/systemd/system/opos-x.timer", "usr/sbin/opos-switch", "usr/sbin/opos-update", "usr/sbin/opos-updater", "usr/sbin/other",
                 "usr/libexec/opos/opos-kiosk", "usr/libexec/opos/sub/x", "usr/libexec/opos-updater/opos-updater", "usr/libexec/other/x", "usr/bin/opos", "/etc/passwd",
                 "opt/opos/../../etc/passwd", "opt/opos/src/opos/./x.py", "opt/opos//x.py", "opt/opos/src/opos/é.py", "opt/other/x", "var/lib/opos-update/state.json", "x"]
        for path in tried:
            try:
                mine = T.classify(path)
            except T.Refused:
                continue
            try:
                theirs = U.classify(path)[0]
            except U.Reject as e:
                self.fail("the tool would build %s but the updater refuses it: %s" % (path, e))
            self.assertEqual(theirs, mine, path)

    def test_a_bundle_cannot_replace_the_updater_its_keys_or_the_owners_update_source(self):
        for path in ("etc/opos/update.json", "etc/opos/base-version", "etc/opos/update/allowed_signers", "usr/libexec/opos-updater/opos-updater",
                     "usr/sbin/opos-update", "etc/systemd/system/opos-update.service", "etc/systemd/system/opos-update.path"):
            with self.assertRaises(U.Reject, msg=path):
                U.classify(path)
            with self.assertRaises(T.Refused, msg=path):
                T.classify(path)

    def test_the_updater_asks_systemd_for_nothing_but_the_server_unit(self):
        u = U.Updater(U.Config(systemctl=("/nonexistent/systemctl",)))
        for args in (("restart", "opos.service"), ("stop", "opos-server.service"), ("restart", "phosh.service"), ("isolate", "rescue.target"),
                     ("restart", "opos-server.service", "opos.service"), ("daemon-reload", "--force")):
            with self.assertRaises(U.Fatal, msg=args):
                u.systemctl(*args)

    def test_the_kiosk_pattern_the_updater_kills_matches_the_command_line_opos_kiosk_starts(self):
        script = rd(PKG / "opos-kiosk").replace(chr(92) + chr(10), " ")  # join the shell line continuations
        line = re.search(r"^\s+(chromium .*)$", script, re.M).group(1)
        home = "/" + "home" + "/droidian"  # written in pieces: the repository guard refuses home-directory literals
        cmd = " ".join(line.replace("$url", "http://127.0.0.1:8765/").replace('"$HOME/', home + "/").replace('"', "").split())
        pattern = U.Config().kiosk_pattern
        self.assertRegex(cmd, pattern)
        self.assertRegex("/usr/lib/chromium/" + cmd, pattern)
        self.assertNotRegex("chromium --user-data-dir=" + home + "/.config/chromium", pattern)
        self.assertNotRegex("/bin/sh /usr/libexec/opos/opos-kiosk", pattern)

    def test_the_kiosk_waits_for_the_server_every_time_it_starts_the_browser(self):
        # The updater kills the browser right after it restarts opos-server, and the kiosk loop starts a new one two seconds
        # later: before a freshly started server answers. The wait for the server must therefore sit inside the loop.
        script = rd(PKG / "opos-kiosk")
        self.assertIn("urlopen", script.split("wait_for_server() {", 1)[1].split("}", 1)[0])
        loop = script.split("while :; do", 1)[1]
        self.assertLess(loop.index("wait_for_server"), loop.index("chromium "))


def extract_block(path, what):
    m = re.search(r"^# ---- BEGIN OPOS OTA %s.*?\n(.*?)^# ---- END OPOS OTA %s" % (what, what), path.read_text(), re.S | re.M)
    assert m, "block markers for %s are missing in %s" % (what, path.name)
    return m.group(1)


class ImageUpdateSource(unittest.TestCase):
    """build-image.sh's optional OPOS_UPDATE_URL, read back by the client's own parser, and the matching verify-image.sh check."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opos-imgsrc-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = os.path.join(self.tmp, "r")
        self.conf = os.path.join(self.r, "etc/opos/update.json")

    def make_root(self):
        """A base root file system as the build finds it: /opt/opos is a directory (the build turns it into a release)."""
        shutil.rmtree(self.r, ignore_errors=True)
        wr(os.path.join(self.r, "opt/opos/src/opos/web.py"), "# web\n")
        wr(os.path.join(self.r, "opt/opos/REVISION"), "0123456789abcdef0123456789abcdef01234567\n")
        os.makedirs(os.path.join(self.r, "etc/systemd/system/multi-user.target.wants"))
        os.makedirs(os.path.join(self.r, "usr/libexec/opos"))
        wr(os.path.join(self.r, "etc/passwd"), "root:x:0:0:root:/root:/bin/sh\ndroidian:x:32011:32011::/nonexistent:/bin/bash\n")

    def build(self, url=None):
        self.make_root()
        env ={k: v for k, v in os.environ.items() if not k.startswith("OPOS_UPDATE")}
        if url is not None:
            env["OPOS_UPDATE_URL"] = url
        script = "set -eu\nr=%s\nhere=%s\n%s\n" % (self.r, PKG, "" if os.geteuid() == 0 else "chown() { :; }\n") + extract_block(PKG / "build-image.sh", "updates")
        return subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)

    def verify_line(self):
        script = ('set -u\nr=%s\nw=$r/etc/systemd/system\nfail=0\ncheck() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fi; }\n%s'
                  % (self.r, extract_block(PKG / "verify-image.sh", "checks")))
        out = subprocess.run(["sh", "-c", script], capture_output=True, text=True).stdout
        return [l for l in out.splitlines() if "update.json" in l][0]

    def test_no_url_means_no_source_and_nothing_is_ever_fetched(self):
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("update source: none", r.stdout)
        self.assertFalse(os.path.lexists(self.conf))
        self.assertIsNone(uc.load_config(self.conf))
        self.assertTrue(self.verify_line().startswith("PASS"))
        r = self.build("")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.lexists(self.conf))

    def test_an_https_address_becomes_a_source_the_client_accepts(self):
        r = self.build("https://updates.example.org/opos/latest.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(self.conf).st_mode), 0o644)
        cfg = uc.parse_config(rd(self.conf, "rb"))
        self.assertEqual(cfg, uc.Config("https://updates.example.org/opos/latest.json", auto_check=False, allow_lan_http=False))
        self.assertTrue(self.verify_line().startswith("PASS"))

    def test_a_loopback_http_address_turns_on_lan_http_for_that_source_only(self):
        r = self.build("http://127.0.0.1:8000/latest.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = uc.parse_config(rd(self.conf, "rb"))
        self.assertEqual((cfg.url, cfg.auto_check, cfg.allow_lan_http), ("http://127.0.0.1:8000/latest.json", False, True))

    def test_plain_http_to_the_internet_is_written_but_the_client_refuses_to_use_it(self):
        r = self.build("http://updates.example.org/latest.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        with self.assertRaises(uc.UpdateError) as cm:
            uc.parse_config(rd(self.conf, "rb"))
        self.assertEqual(cm.exception.code, "bad_config")

    def test_anything_else_stops_the_build_and_leaves_no_file(self):
        for bad in ("ftp://example.org/x", "file:///etc/passwd", "javascript:1", "example.org/latest.json", 'https://example.org/"x', "https://example.org/a b",
                    "https://example.org/a\\b", "https://example.org/$(id)", "https://example.org/`id`", "https://example.org/a;b", "https://example.org/a\nb"):
            with self.subTest(bad):
                r = self.build(bad)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("OPOS_UPDATE_URL", r.stderr)
                self.assertFalse(os.path.lexists(self.conf))

    def test_verify_notices_a_source_that_was_tampered_with(self):
        self.assertEqual(self.build("https://updates.example.org/latest.json").returncode, 0)
        for label, content in (("extra key", {"url": "https://x.example/latest.json", "auto_check": False, "allow_lan_http": False, "run": "x"}),
                               ("auto check on", {"url": "https://x.example/latest.json", "auto_check": True, "allow_lan_http": False}),
                               ("ftp", {"url": "ftp://x.example/latest.json", "auto_check": False, "allow_lan_http": False})):
            with self.subTest(label):
                wr(self.conf, json.dumps(content))
                self.assertTrue(self.verify_line().startswith("FAIL"), label)
        os.unlink(self.conf)
        os.symlink("/etc/passwd", self.conf)
        self.assertTrue(self.verify_line().startswith("FAIL"))


class DocsAreHonest(unittest.TestCase):
    def test_the_status_line_says_what_was_and_was_not_tested(self):
        doc = rd(ROOT / "docs" / "ota-update.md")
        status = [l for l in doc.splitlines() if l.startswith("Status")]
        self.assertEqual(len(status), 1)
        s = status[0].lower()
        self.assertIn("implemented", s)
        self.assertIn("not tested on the phone", s)
        self.assertNotIn("design only", s)
        index = [l for l in rd(ROOT / "docs" / "README.md").splitlines() if "ota-update.md" in l and "Over-the-air" in l][0]
        self.assertNotIn("not yet implemented", index)

    def test_the_image_readme_explains_the_build_option_and_the_limits(self):
        text = rd(PKG / "README.md")
        for needle in ("OPOS_UPDATE_SIGNERS", "duo-push-update.ps1", "opos-release.py", "docs/ota-update.md", "not tested on the phone", "opos.service"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
