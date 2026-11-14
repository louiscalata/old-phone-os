# SPDX-License-Identifier: GPL-3.0-or-later
"""packaging/duo-os/verify-image.sh runs as root on the builder. Two properties are pinned here.

1. It never runs Python from, or writes scratch files into, a directory another local user can write to: one private
   root-owned mktemp -d directory is the working directory, HOME and the home of every scratch file, and the
   Python search path is narrowed (PYTHONSAFEPATH, PYTHONNOUSERSITE, python3 -I on the builder's own python).
2. The authenticated /api/state probe is a check: a state that cannot be fetched, is not JSON or lacks the fields the page
   reads makes the verification FAIL instead of printing a traceback and then "VERIFY: PASS".

The shell is run against stubs; nothing here mounts an image or starts qemu."""
import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests import portable

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging" / "duo-os"
SCRIPT = DUO / "verify-image.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
POSIX = sys.platform != "win32"


def code_lines():
    return [ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#")]


def prelude():
    """The start of the script up to (and including) its `fail=0`: the root check, the private directory, the environment."""
    return TEXT[: TEXT.index("\nfail=0\n") + len("\nfail=0\n")]


def free_port():
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StaticPins(unittest.TestCase):
    def test_the_work_directory_is_a_private_root_owned_mktemp_directory(self):
        self.assertIn('work=$(mktemp -d "${TMPDIR:-/tmp}/opos-verify.XXXXXX")', TEXT)
        self.assertIn("umask 077", TEXT)
        self.assertIn("""[ "$(stat -c '%u %a' "$work")" = "0 700" ]""", TEXT)
        self.assertIn("trap cleanup EXIT", TEXT)
        self.assertIn('    rm -rf "$work" || status=1\n    exit "$status"\n}', TEXT, "cleanup failure propagates after removing the private directory")
        self.assertIn('cd "$work" || exit 2', TEXT)
        # all of it before the first mount and before anything runs from the image
        self.assertLess(TEXT.index('cd "$work"'), TEXT.index("mount -o ro"))

    def test_the_python_environment_is_narrowed_for_the_whole_script(self):
        m = re.search(r"^export (.*)$", TEXT, re.M)
        self.assertIsNotNone(m)
        for setting in ("PYTHONSAFEPATH=1", "PYTHONNOUSERSITE=1", 'HOME="$work"'):
            self.assertIn(setting, m.group(1))
        self.assertIn("unset PYTHONSTARTUP PYTHONINSPECT PYTHONHOME", TEXT)
        self.assertLess(TEXT.index("export PYTHONSAFEPATH=1"), TEXT.index("py() {"))

    def test_no_code_line_uses_tmp_or_a_bare_host_python(self):
        for ln in code_lines():
            if "TMPDIR:-/tmp" in ln:
                continue  # the one place mktemp is told where to make the private directory
            self.assertNotIn("/tmp", ln, ln)
            self.assertNotIn("HOME=/tmp", ln)
            # the builder's python3 is always isolated: no cwd, no environment, no user site
            # (a path in the image, such as the updater's shebang '#!/usr/bin/python3 -IB' that a check reads, is not the builder's python)
            for flag in re.findall(r"(?<![/\w])python3 (-\S*)", ln):
                self.assertEqual(flag, "-I", "the builder's python3 must run isolated: " + ln)

    def test_the_image_python_is_started_through_py_and_not_from_a_changed_directory(self):
        for ln in code_lines():
            self.assertNotRegex(ln, r"cd /tmp", ln)
        self.assertRegex(TEXT, r'(?m)^py\(\) \{ PYTHONPATH="\$mnt/opt/opos/src" PYTHONDONTWRITEBYTECODE=1 "\$q" -L "\$mnt" "\$mnt/usr/bin/python3" "\$@"; \}$')

    def test_servers_use_picked_ports_and_are_stopped_by_pid_alone(self):
        code = "\n".join(code_lines())
        self.assertNotRegex(code, r"1899[89]", "a fixed loopback port can be held by a stale or hostile listener")
        commands = "\n".join(ln for ln in code_lines() if not ln.startswith("check "))  # a check may name the tools it looks for in the image
        for word in ("pkill", "killall", "pgrep"):
            self.assertNotIn(word, commands, "servers are stopped by the pid this script started, never by name or command line")
        # picked ports: the smoke server, the integration server and the monitor's "off" server (pick_port), the monitor's "on"
        # server and the closed port its model address names (pick_port_apart: never the port of a server that is still binding)
        picks = [name for name in re.findall(r"(\w+)=\$\(pick_port(?:_apart)?\b", code) if name != "pa_p"]  # pa_p: pick_port_apart's own pick
        self.assertEqual(picks, ["port", "port2", "mport_off", "mport_on", "mport_dead"])
        self.assertEqual(code.count("-m opos.web"), 1, "one function starts a server")
        self.assertEqual(len(re.findall(r'(?:\bif |&& )serve "', code)), 4, "and the four servers are started through it")

    def test_every_way_out_runs_the_same_cleanup(self):
        for line in ("trap cleanup EXIT", "trap 'exit 130' INT", "trap 'exit 143' TERM", "trap 'exit 129' HUP"):
            self.assertIn(line, TEXT)
        cleanup = TEXT[TEXT.index("stop_children() {"):TEXT.index("trap cleanup EXIT")]
        self.assertIn("    stop_children\n", cleanup)
        self.assertIn("unmount_image || status=1", cleanup)
        self.assertIn('    exit "$status"', cleanup)
        for part in ("kill \"$p\"", "kill -9 \"$p\"", "umount \"$mnt\"", "rmdir \"$mnt\"", 'rm -rf "$work"', "trap '' INT TERM HUP"):
            self.assertIn(part, cleanup)

    def test_the_script_parses_and_keeps_its_header(self):
        if POSIX:
            r = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(TEXT.splitlines()[1], "# SPDX-License-Identifier: GPL-3.0-or-later")
        self.assertNotIn("\r", TEXT)


@unittest.skipUnless(POSIX and os.path.isdir("/tmp"), "needs a POSIX shell")
class PrivateDirectory(unittest.TestCase):
    """The start of the script run for real, from a hostile directory that holds a planted opos package."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        # mktemp's parent for the script: a real Linux file system (the owner and mode checks mean nothing on a mounted
        # Windows drive, where the test temp directory may live)
        self.tmpdir = Path(tempfile.mkdtemp(prefix="opos-verify-test-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.hostile = self.t / "hostile"
        (self.hostile / "opos").mkdir(parents=True)
        (self.hostile / "opos" / "__init__.py").write_text("open(%r, 'w').write('executed as root')\n" % str(self.t / "PWNED"))
        self.fake("qemu-aarch64-static", "exit 0")
        if os.geteuid() != 0:  # the script insists on root: pretend, and report the owner of the private directory as root
            self.fake("id", "echo 0")
            real = subprocess.run(["sh", "-c", "command -v stat"], capture_output=True, text=True).stdout.strip()
            shim = portable.gnu_stat_path(self.t / "gnu")  # None on Linux; off Linux, GNU `stat -c` answers over BSD stat
            if shim:
                real = str(Path(shim) / "stat")
            self.fake("stat", 'out=$(%s "$@") || exit $?\ncase $out in "%d "*) out="0 ${out#* }";; esac\necho "$out"' % (real, os.geteuid()))

    def fake(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)

    def run_prelude(self, trailer, tmpdir=None, extra_env=None):
        script = self.t / "run.sh"
        script.write_text(prelude() + trailer)
        env = {"PATH": "%s:/usr/bin:/bin" % self.bin, "TMPDIR": str(tmpdir or self.tmpdir), "PYBIN": sys.executable}
        env.update(extra_env or {})
        return subprocess.run(["sh", str(script), "OUT.img"], cwd=str(self.hostile), env=env, capture_output=True, text=True, timeout=60)

    TRAILER = (
        'echo "WORK=$work"\n'
        'echo "PWD=$(pwd -P)"\n'
        'echo "HOME=$HOME"\n'
        'echo "SAFE=$PYTHONSAFEPATH NOUSER=$PYTHONNOUSERSITE STARTUP=${PYTHONSTARTUP-unset}"\n'
        'echo "MODE=$(stat -c %a "$work")"\n'
        'echo "IMG=$img"\n'
        'if "$PYBIN" -c "import opos" 2>/dev/null; then echo IMPORTED-OPOS; else echo IMPORT-REFUSED; fi\n'
        'echo "SCRATCH" > "$work/opos-verify.log"\n'
    )

    def facts(self, out):
        return dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)

    def test_it_runs_from_a_private_directory_with_the_narrowed_environment(self):
        r = self.run_prelude(self.TRAILER)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        f = self.facts(r.stdout)
        work = Path(f["WORK"])
        self.assertEqual(work.parent.resolve(), self.tmpdir.resolve())
        self.assertTrue(work.name.startswith("opos-verify."))
        self.assertEqual(Path(f["PWD"]).resolve(), work.resolve())
        self.assertEqual(Path(f["HOME"]).resolve(), work.resolve())
        self.assertEqual(f["MODE"], "700")
        self.assertEqual(f["SAFE"], "1 NOUSER=1 STARTUP=unset")
        self.assertTrue(f["IMG"].endswith("/OUT.img") and f["IMG"].startswith("/"), "a relative image path is made absolute before the cd")
        self.assertFalse(work.exists(), "the trap removes the private directory (and its scratch files) at exit")

    def test_a_package_planted_in_the_callers_directory_is_not_imported(self):
        r = self.run_prelude(self.TRAILER)
        self.assertIn("IMPORT-REFUSED", r.stdout, r.stdout + r.stderr)
        self.assertNotIn("IMPORTED-OPOS", r.stdout)
        self.assertFalse((self.t / "PWNED").exists(), "the planted package ran")

    def test_the_planted_package_would_run_from_the_callers_directory_without_the_private_directory(self):
        # the control: the same python, started in the hostile directory the way the old script started it
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        p = subprocess.run([sys.executable, "-c", "import opos"], cwd=str(self.hostile), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((self.t / "PWNED").exists())

    def test_it_refuses_when_it_cannot_make_the_private_directory(self):
        r = self.run_prelude(self.TRAILER, tmpdir=self.t / "does-not-exist")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("cannot make a private work directory", r.stderr)

    def test_it_refuses_a_directory_that_is_not_mode_700_and_root_owned(self):
        self.fake("mktemp", 'd="$2.$$"; mkdir "$d" && chmod 755 "$d" && echo "$d"')
        r = self.run_prelude(self.TRAILER)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("is not a private root-owned directory", r.stderr)

    def test_it_needs_an_image_argument(self):
        script = self.t / "run.sh"
        script.write_text(prelude())
        env = {"PATH": "%s:/usr/bin:/bin" % self.bin, "TMPDIR": str(self.tmpdir)}
        r = subprocess.run(["sh", str(script)], cwd=str(self.hostile), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage:", r.stderr)


def state_probe_block():
    m = re.search(r"^# --- state probe: BEGIN.*?\n(.*?)^# --- state probe: END", TEXT, re.S | re.M)
    assert m, "verify-image.sh has the state probe block"
    return m.group(1)


GOOD = {"battery": {"percent": 61.5}, "lock_story": "All good", "helper": {"mode": "NORMAL"}}


class Stub(BaseHTTPRequestHandler):
    """Serves / with an opos-token and /api/state as set on the class; anything else is a 404."""
    state_status = 200
    state_body = json.dumps(GOOD)
    token_page = True

    def log_message(self, *a):
        pass

    def reply(self, status, body):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self.reply(200, '<meta name="opos-token" content="tok123">' if self.token_page else "<html></html>")
        elif self.path == "/api/state":
            if self.headers.get("X-OPOS-Token") != "tok123":
                return self.reply(403, "{}")
            self.reply(self.state_status, self.state_body)
        else:
            self.reply(404, "")


@unittest.skipUnless(POSIX, "needs a POSIX shell")
class StateProbe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        bin_dir = self.t / "bin"
        bin_dir.mkdir()
        py = bin_dir / "python3"
        py.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
        py.chmod(0o755)
        self.bin = bin_dir

    @contextlib.contextmanager
    def server(self, status=200, body=json.dumps(GOOD), token_page=True):
        handler = type("H", (Stub,), {"state_status": status, "state_body": body, "token_page": token_page})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            yield srv.server_address[1]
        finally:
            srv.shutdown()
            srv.server_close()
            th.join(10)

    def probe(self, port):
        """The probe block as the script runs it, with its check() and fail variable: (exit status of the script, stdout)."""
        script = ('set -u\nfail=0\nwork=%s\nport=%d\n'
                  'check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n%s\nexit $fail\n') % (self.t, port, state_probe_block())
        r = subprocess.run(["sh", "-c", script], env={"PATH": "%s:/usr/bin:/bin" % self.bin}, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout

    def test_a_good_state_passes(self):
        with self.server() as port:
            rc, out = self.probe(port)
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS the authenticated /api/state", out)
        self.assertIn("state: battery 61.5%, mode NORMAL, lock 'All good'", out)

    def test_a_state_without_the_helper_still_passes(self):
        with self.server(body=json.dumps({"battery": {"percent": 0}, "lock_story": ""})) as port:
            rc, out = self.probe(port)
        self.assertEqual(rc, 0, out)
        self.assertIn("mode -", out)

    def test_a_state_that_errors_fails_the_verification(self):
        with self.server(status=500, body='{"error": "boom"}') as port:
            rc, out = self.probe(port)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL the authenticated /api/state", out)
        self.assertIn("HTTPError", out)
        self.assertNotIn("state: battery", out)

    def test_malformed_or_incomplete_states_fail_the_verification(self):
        cases = {
            "not json": "<html>oops</html>",
            "a list": "[]",
            "no battery": json.dumps({"lock_story": "x"}),
            "battery is not an object": json.dumps({"battery": 5, "lock_story": "x"}),
            "percent missing": json.dumps({"battery": {}, "lock_story": "x"}),
            "percent is text": json.dumps({"battery": {"percent": "61"}, "lock_story": "x"}),
            "percent is a bool": json.dumps({"battery": {"percent": True}, "lock_story": "x"}),
            "percent above 100": json.dumps({"battery": {"percent": 101}, "lock_story": "x"}),
            "percent below 0": json.dumps({"battery": {"percent": -1}, "lock_story": "x"}),
            "no lock story": json.dumps({"battery": {"percent": 50}}),
            "helper is not an object": json.dumps({"battery": {"percent": 50}, "lock_story": "x", "helper": "on"}),
        }
        for name, body in cases.items():
            with self.subTest(name), self.server(body=body) as port:
                rc, out = self.probe(port)
                self.assertEqual(rc, 1, out)
                self.assertIn("FAIL the authenticated /api/state", out)
                self.assertNotIn("PASS the authenticated", out)

    def test_a_page_without_a_token_fails(self):
        with self.server(token_page=False) as port:
            rc, out = self.probe(port)
        self.assertEqual(rc, 1, out)
        self.assertIn("no opos-token", out)

    def test_a_server_that_is_not_there_fails(self):
        rc, out = self.probe(free_port())
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL the authenticated /api/state", out)

    def test_the_real_server_state_passes_the_probe(self):
        cfg = tempfile.mkdtemp(prefix="opos-verify-probe-")
        self.addCleanup(shutil.rmtree, cfg, True)
        port = free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_") and k != "TZ"}
        env.update(OPOS_CONFIG_DIR=cfg, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1", HOME=cfg)
        srv = subprocess.Popen([sys.executable, "-m", "opos.web", "--port", str(port)], env=env, cwd=cfg,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(srv.wait, 20)
        self.addCleanup(srv.terminate)
        for _ in range(100):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=2).read()
                break
            except OSError:
                time.sleep(0.2)
        rc, out = self.probe(port)
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS the authenticated /api/state", out)


SERVER_PY = r'''
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
GOOD = {"battery": {"percent": 61.5}, "lock_story": "All good", "helper": {"mode": "NORMAL"}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            body, status = b'<meta name="opos-token" content="tok123">', 200
        elif self.path == "/api/state" and self.headers.get("X-OPOS-Token") == "tok123":
            body, status = json.dumps(GOOD).encode(), 200
        else:
            body, status = b"", 404
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''

QEMU_STUB = '''#!/bin/sh
# qemu-aarch64-static -L ROOT PYTHON ARGS...: what py() and serve() in verify-image.sh run. Only "-m opos.web --port N" does anything.
shift 3
case "$1 $2" in
"-m opos.web")
    echo $$ > "$T/server.$4.pid"
    echo "$4 $OPOS_OSK$OPOS_NETWORK$OPOS_FIRSTRUN" >> "$T/server.started"
    echo "$4 ${OPOS_MONITOR-unset}" >> "$T/server.monitor"
    read -r mode < "$T/server_mode"
    [ "$mode" = hang ] && exec /bin/sleep 300
    exec "$PYBIN" "$T/server.py" "$4";;
esac
exit 0
'''


def alive(pid):
    try:
        state = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


@unittest.skipUnless(POSIX and os.path.isdir("/proc") and shutil.which("ps"), "needs Linux and ps")
class WholeScriptServers(unittest.TestCase):
    """The whole of verify-image.sh run against stubs (qemu, mount, strings, a stand-in image directory): everything about
    its two servers. The stub qemu starts a real HTTP server for "-m opos.web" (or hangs, if told to) and does nothing else."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        self.tmpdir = Path(tempfile.mkdtemp(prefix="opos-verify-test-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        (self.t / "server.py").write_text(SERVER_PY)
        (self.t / "server_mode").write_text("serve\n")
        self.fake("qemu-aarch64-static", QEMU_STUB.split("\n", 1)[1])
        for name in ("mount", "umount", "strings"):
            self.fake(name, "exit 0")
        self.fake("mountpoint", "exit 1")
        # python3 for the builder's own probes; the free-port question can be answered with a port somebody else holds
        self.fake("python3", 'case "$*" in\n*\'s.bind(("127.0.0.1", 0))\'*) if [ -n "${HOSTILE_PORT:-}" ]; then echo "$HOSTILE_PORT"; exit 0; fi;;\nesac\nexec "$PYBIN" "$@"')
        if os.geteuid() != 0:  # the script insists on root: pretend, and report the owner of the private directory as root
            self.fake("id", "echo 0")
            real = subprocess.run(["sh", "-c", "command -v stat"], capture_output=True, text=True).stdout.strip()
            shim = portable.gnu_stat_path(self.t / "gnu")  # None on Linux; off Linux, GNU `stat -c` answers over BSD stat
            if shim:
                real = str(Path(shim) / "stat")
            self.fake("stat", 'out=$(%s "$@") || exit $?\ncase $out in "%d "*) out="0 ${out#* }";; esac\necho "$out"' % (real, os.geteuid()))
        self.addCleanup(self.reap)

    def fake(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)

    def reap(self):
        for f in self.t.glob("server.*.pid"):
            try:
                os.kill(int(f.read_text()), signal.SIGKILL)
            except (ValueError, ProcessLookupError, PermissionError):
                pass

    def start(self, hostile_port=None, mode="serve", extra_env=None):
        (self.t / "server_mode").write_text(mode + "\n")
        env = {"PATH": "%s:/usr/bin:/bin" % self.bin, "TMPDIR": str(self.tmpdir), "T": str(self.t), "PYBIN": sys.executable}
        env.update(extra_env or {})
        if hostile_port:
            env["HOSTILE_PORT"] = str(hostile_port)
        def default_signals():  # an ignored signal cannot be trapped by a shell, and a runner started in the background may pass them on ignored
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, signal.SIG_DFL)

        return subprocess.Popen(["sh", str(SCRIPT), "OUT.img"], env=env, cwd=str(self.t), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True, preexec_fn=default_signals)

    def finish(self, p, timeout=120):
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out, err

    def server_pids(self):
        return [int(f.read_text()) for f in sorted(self.t.glob("server.*.pid"))]

    def work_dirs(self):
        return list(self.tmpdir.glob("opos-verify.*"))

    def test_with_free_ports_the_servers_answer_and_are_gone_afterwards(self):
        rc, out, err = self.finish(self.start())
        self.assertIn("PASS OPOS server from the image answers HTTP 200", out, out + err)
        self.assertIn("PASS the authenticated /api/state answers", out, out)
        ports = [ln.split()[0] for ln in (self.t / "server.started").read_text().splitlines()]
        self.assertEqual(len(ports), 4, "the smoke server, the integration server and the monitor's two servers")
        self.assertTrue(all(p.isdigit() and 1024 <= int(p) < 65536 for p in ports))
        self.assertTrue(all(p not in ("18999", "18998", "18996", "18997") for p in ports))
        self.assertNotEqual(ports[2], ports[3], "the monitor's two servers run at the same time, on two ports")
        self.assertEqual((self.t / "server.started").read_text().splitlines()[1].split(" ", 1)[1], "111", "the second server has the three switches")
        monitor = [ln.split(" ", 1)[1] for ln in (self.t / "server.monitor").read_text().splitlines()]
        self.assertEqual(monitor, ["unset", "unset", "unset", "1"], "only the monitor's second server has the Inference page on")
        for pid in self.server_pids():
            self.assertFalse(alive(pid), "a server outlived the script")
        self.assertEqual(self.work_dirs(), [])

    def test_a_switch_left_on_in_the_callers_environment_reaches_no_server(self):
        # root's shell may have OPOS_* set: the "off" servers must be off, and only the arguments of serve() switch a piece on
        self.finish(self.start(extra_env={"OPOS_MONITOR": "1", "OPOS_OSK": "1", "OPOS_NETWORK": "1", "OPOS_FIRSTRUN": "1"}))
        started = (self.t / "server.started").read_text().splitlines()
        self.assertEqual([ln.split(" ", 1)[1] if " " in ln else "" for ln in started], ["", "111", "", ""])
        monitor = [ln.split(" ", 1)[1] for ln in (self.t / "server.monitor").read_text().splitlines()]
        self.assertEqual(monitor, ["unset", "unset", "unset", "1"])

    def test_a_listener_on_the_port_before_the_server_starts_fails_the_check_and_is_never_read(self):
        # a hostile or stale listener answers on the port the script picked: it must not stand in for the image's server
        class Canned(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = b'<meta name="opos-token" content="tok123">' if self.path == "/" else json.dumps(GOOD).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Canned)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), th.join(10)))
        port = srv.server_address[1]
        rc, out, err = self.finish(self.start(hostile_port=port))
        self.assertEqual(rc, 1, out + err)
        self.assertIn("FAIL something already answers on 127.0.0.1:%d before the image's server was started" % port, out)
        self.assertIn("FAIL OPOS server from the image answers HTTP 200", out)
        self.assertIn("FAIL the authenticated /api/state was not probed", out)
        self.assertNotIn("PASS OPOS server from the image", out)
        self.assertNotIn("PASS the authenticated /api/state", out)
        self.assertIn("duo pieces FAILED: the image's server did not start", out)
        self.assertIn("FAIL /monitor and /api/monitor answer 404 with the switch off", out)
        self.assertIn("monitor routes FAILED: the image's server did not start", out)
        self.assertIn("VERIFY: FAIL", out)
        self.assertFalse((self.t / "server.started").exists(), "no server was started on a port that already answers")

    def test_a_signal_stops_the_servers_and_removes_everything(self):
        for name, sig, status in (("INT", signal.SIGINT, 130), ("TERM", signal.SIGTERM, 143), ("HUP", signal.SIGHUP, 129)):
            with self.subTest(name):
                for f in self.t.glob("server.*"):
                    f.unlink()
                p = self.start(mode="hang")  # the server never answers: the script sits in its readiness loop
                for _ in range(100):
                    if list(self.t.glob("server.*.pid")):
                        break
                    time.sleep(0.1)
                pids = self.server_pids()
                self.assertEqual(len(pids), 1, "one server, started by pid")
                self.assertTrue(alive(pids[0]))
                time.sleep(0.5)
                os.kill(p.pid, sig)  # the script alone, not its process group
                rc, out, err = self.finish(p, timeout=60)
                self.assertEqual(rc, status, out + err)
                for _ in range(50):
                    if not alive(pids[0]):
                        break
                    time.sleep(0.1)
                self.assertFalse(alive(pids[0]), "the server outlived the signal")
                self.assertEqual(self.work_dirs(), [])
                self.assertNotIn("VERIFY:", out, "it was interrupted, not finished")

    @unittest.skipUnless(shutil.which("bash"), "needs bash for exec -a")
    def test_only_its_own_server_is_stopped(self):
        # processes whose command lines look like the old fixed-port servers, started before the script, are left alone
        decoys = [subprocess.Popen(["bash", "-c", "exec -a 'qemu-aarch64-static python3 -m opos.web --port %d' /bin/sleep 300" % port],
                                   start_new_session=True) for port in (18999, 18998, 18997, 18996)]  # the smoke, integration and monitor ports of the old script
        for d in decoys:
            self.addCleanup(lambda d=d: (d.kill(), d.wait()))
        time.sleep(0.3)
        self.finish(self.start())
        self.assertEqual([d.poll() for d in decoys], [None] * 4, "the script killed a process it did not start")


if __name__ == "__main__":
    unittest.main()
