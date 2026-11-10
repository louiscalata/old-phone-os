# SPDX-License-Identifier: GPL-3.0-or-later
"""UXP14: real-browser acceptance checks for the kiosk page.

Drives headless Chromium over the DevTools protocol with tests/uxr_cdp.js
(Node built-ins only). The page is opos.web.page(demo=True) written to a temp
file and opened by file://. Two views are measured: "full" (state fetches fail,
the fail-open shell) and "home" (a fetch stub serves real Phone().state()
output so the shell renders, then oposUI.openApp('home')). Each test starts its
own browser with --remote-debugging-port=0; where binding a local port is not
permitted (macOS sandbox), uxr_cdp.js starts it with --remote-debugging-pipe.
A third view, "boom", makes the new shell JS throw and then
taps Call/Send/the meter to prove the base phone still works (test_fail_open).
No browser or no node: every test skips. KNOWN_GAPS lists checks that fail
today for a product reason, with regexes for the exact expected violations;
any other violation fails, and a listed gap that passes fails so the entry is
removed. OPOS_UXR_VERBOSE=1 prints one "UXR <test> <violation>" line
per violation on stdout (tests/uxr_report.sh).
"""

import glob
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from opos.web import Phone, page

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "uxr_cdp.js"
VERBOSE = os.environ.get("OPOS_UXR_VERBOSE") == "1"

# test_touch_targets: selector -> (min CSS px, reason). Default minimum is 48.
ALLOW_SIZE = {
    "#meter": (0, "4px visual bar; taps land on the 44px #meter-hit laid over it"),
}
# test_touch_targets: selectors that float over other controls on purpose, and the views in which they may. In those views the 8 px gap rule
# is replaced by a stricter one for the controls under them: where they overlap, a 48 x 48 px part of the covered control must stay free
# (so it can still be tapped). The size rule still applies to the floating control.
ALLOW_OVERLAP = {
    "#reload-shell": (("full",), "the Reload button of a page that cannot reach its server is a fixed 48 px button at the bottom right (W1-02); "
                                 "in the boot-failure view (state fetches fail) it floats over whatever scrolls under it, at 360x640 over part of #number; "
                                 "in the 'home' view it is hidden and takes part in the gap rule like any control"),
}
# test_real_controls: selector -> reason for a non-native element that may be clickable.
ALLOW_CONTROLS = {}

# test -> (reason, regexes). Only violations matching one of the regexes are expected; any other one fails.
KNOWN_GAPS = {}


def _real_home():
    return pwd.getpwuid(os.getuid()).pw_dir


def _rev(path):
    m = re.search(r"-(\d+)" + re.escape(os.sep), path)
    return int(m.group(1)) if m else 0


def find_chrome():
    env = os.environ.get("OPOS_TEST_CHROME")
    if env:
        return env if os.access(env, os.X_OK) else None
    root = os.path.join(_real_home(), "Library", "Caches", "ms-playwright")
    pats = [
        "chromium_headless_shell-*/chrome-headless-shell*/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-*/headless_shell",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-linux*/chrome",
    ]
    for pat in pats:
        hits = [p for p in glob.glob(os.path.join(root, pat)) if os.access(p, os.X_OK)]
        if hits:
            return max(hits, key=_rev)
    for name in ("chromium", "chromium-browser", "google-chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _launch(chrome, tmp):
    udd = os.path.join(tmp, "profile")
    os.makedirs(udd)
    err_path = os.path.join(tmp, "chrome.err")
    with open(err_path, "wb") as err:
        proc = subprocess.Popen(
            [chrome, "--headless", "--use-mock-keychain", "--password-store=basic", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0",
             "--user-data-dir=" + udd, "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
    active = os.path.join(udd, "DevToolsActivePort")
    end = time.monotonic() + 15
    while time.monotonic() < end and proc.poll() is None:
        try:
            first = Path(active).read_text().split("\n", 1)[0].strip()
            if first.isdigit():
                return proc, int(first)
        except OSError:
            pass
        m = re.search(rb"DevTools listening on ws://127\.0\.0\.1:(\d+)/", Path(err_path).read_bytes())
        if m:
            return proc, int(m.group(1))
        time.sleep(0.05)
    tail = Path(err_path).read_bytes()[-2000:].decode("utf-8", "replace")
    _kill(proc)
    raise RuntimeError("headless chromium did not start: " + tail.strip())


def _kill(proc):
    if proc is not None and proc.poll() is None:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait(timeout=10)


class TestUxrAcceptance(unittest.TestCase):
    skip_reason = None
    mode = "port"

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chrome()
        cls.node = shutil.which("node")
        if not cls.chrome:
            cls.skip_reason = "no headless chromium"
            return
        if not cls.node:
            cls.skip_reason = "no node"
            return
        cls.tmp = tempfile.mkdtemp(prefix="uxr-")
        html = os.path.join(cls.tmp, "page.html")
        Path(html).write_text(page(demo=True), encoding="utf-8")
        cls.url = Path(html).as_uri()
        cls.state = os.path.join(cls.tmp, "state.json")
        Path(cls.state).write_text(json.dumps({
            "hidden": Phone().state(),
            "visible": Phone(visible=True).state(),
            "allow": {"size": {k: v[0] for k, v in ALLOW_SIZE.items()}, "controls": dict(ALLOW_CONTROLS), "overlap": {k: list(v[0]) for k, v in ALLOW_OVERLAP.items()}},
        }, default=str), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "tmp", None):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.proc = None
        if self.skip_reason:
            self.skipTest(self.skip_reason)
        self.run_dir = tempfile.mkdtemp(dir=self.tmp)
        self.pidfile = os.path.join(self.run_dir, "chrome.pid")
        if type(self).mode == "port":
            try:
                self.proc, self.port = _launch(self.chrome, self.run_dir)
            except (OSError, RuntimeError) as e:
                # Sandboxes that forbid binding a local port (or Mach rendezvous) still allow --remote-debugging-pipe.
                if not re.search(r"bind\(\) failed|mach_port_rendezvous", str(e)):
                    self.skipTest(str(e)[-200:])
                type(self).mode = "pipe"

    def tearDown(self):
        _kill(self.proc)
        try:
            pid = int(Path(self.pidfile).read_text())
            os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError):
            pass

    def _run(self, check):
        target = str(self.port) if self.proc is not None else "pipe"
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile, TMPDIR=self.run_dir)
        r = subprocess.run([self.node, str(DRIVER), target, self.url, check, self.state],
                           capture_output=True, text=True, timeout=40, env=env)
        self.assertEqual(r.returncode, 0, "uxr_cdp.js %s failed: %s" % (check, r.stderr[-800:]))
        return json.loads(r.stdout.strip().splitlines()[-1])["violations"]

    def _verdict(self, name, violations):
        if VERBOSE:
            for v in violations:
                print("UXR %s %s" % (name, v), flush=True)
        reason, pats = KNOWN_GAPS.get(name, ("", []))
        if pats and not violations:
            self.fail("%s passes now: remove it from KNOWN_GAPS (%s)" % (name, reason))
        new = [v for v in violations if not any(re.search(p, v) for p in pats)]
        self.assertEqual(new, [], "%d unexpected violation(s):\n%s" % (len(new), "\n".join(new)))

    def test_touch_targets(self):
        self._verdict("test_touch_targets", self._run("touch_targets"))

    def test_contrast(self):
        self._verdict("test_contrast", self._run("contrast"))

    def test_text_200(self):
        self._verdict("test_text_200", self._run("text_200"))

    def test_rotation_without_reload(self):
        self._verdict("test_rotation_without_reload", self._run("rotation_without_reload"))

    def test_real_controls(self):
        self._verdict("test_real_controls", self._run("real_controls"))

    def test_fail_open(self):
        self._verdict("test_fail_open", self._run("fail_open"))

    def test_helper_lazy(self):
        self._verdict("test_helper_lazy", self._run("helper_lazy"))


if __name__ == "__main__":
    unittest.main()
