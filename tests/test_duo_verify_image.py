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
        self.assertIn("""trap 'cd /; rm -rf "$work"' EXIT""", TEXT)
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
            for flag in re.findall(r"\bpython3 (-\S*)", ln):
                self.assertEqual(flag, "-I", "the builder's python3 must run isolated: " + ln)

    def test_the_image_python_is_started_through_py_and_not_from_a_changed_directory(self):
        for ln in code_lines():
            self.assertNotRegex(ln, r"cd /tmp", ln)
        self.assertRegex(TEXT, r'(?m)^py\(\) \{ PYTHONPATH="\$mnt/opt/opos/src" PYTHONDONTWRITEBYTECODE=1 "\$q" -L "\$mnt" "\$mnt/usr/bin/python3" "\$@"; \}$')

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


if __name__ == "__main__":
    unittest.main()
