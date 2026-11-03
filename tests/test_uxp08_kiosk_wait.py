# SPDX-License-Identifier: GPL-3.0-or-later
import http.server
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

KIOSK = Path(__file__).resolve().parents[1] / "packaging/pmos/opos/opos-kiosk"
URL = "http://127.0.0.1:8765/"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class _Stall(_Quiet):
    # Trickles bytes without ever finishing the status line.
    def do_GET(self):
        try:
            for _ in range(60):
                self.wfile.write(b"H")
                self.wfile.flush()
                time.sleep(0.5)
        except OSError:
            pass


def _serve(handler, port=0):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestKioskWait(unittest.TestCase):
    def _stop(self, srv):
        srv.shutdown()
        srv.server_close()

    def _stub(self):
        # Fake chromium (run via sh) that records it was launched.
        stub = os.path.join(tempfile.mkdtemp(), "chromium")
        with open(stub, "w") as f:
            f.write('echo "$@" > "$0.ran"\n')
        return stub

    def _run(self, **extra):
        # Explicit overrides must replace the default Chromium command.
        env = dict(os.environ, OPOS_KIOSK_CHROMIUM="true")
        env.update(extra)
        start = time.monotonic()
        r = subprocess.run(["sh", str(KIOSK)], capture_output=True, text=True, env=env, timeout=20)
        return r, time.monotonic() - start

    def test_wait_loop_precedes_exec_and_is_bounded(self):
        text = KIOSK.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n# SPDX-License-Identifier: GPL-3.0-or-later\n"))
        exec_lines = [l for l in text.splitlines() if l.startswith("exec ")]
        self.assertEqual(len(exec_lines), 1)
        exec_line = exec_lines[0]
        loop = text.index("while ")
        self.assertLess(loop, text.index(exec_line))
        self.assertLess(text.index("urllib.request.urlopen"), text.index(exec_line))
        self.assertRegex(text[loop:text.index(exec_line)], r'\[ "\$i" -(lt|ge) "\$wait" \]')
        self.assertIn("i=$((i + 1))", text)
        self.assertIn("${OPOS_KIOSK_WAIT:-60}", text)
        self.assertIn("--kiosk", exec_line)
        self.assertIn("--ozone-platform=wayland", exec_line)
        self.assertEqual(exec_line.count(URL), 1)
        self.assertEqual(
            exec_line,
            "exec ${OPOS_KIOSK_CHROMIUM:-chromium} --ozone-platform=wayland --kiosk "
            "--noerrdialogs --disable-infobars --no-first-run " + URL,
        )
        self.assertNotRegex(exec_line, r"OPOS_KIOSK_URL|\$url")

    def test_runs_and_proceeds_when_server_is_down(self):
        # Port 9 (discard) on loopback: nothing listens, so every probe fails.
        r, took = self._run(OPOS_KIOSK_WAIT="2", OPOS_KIOSK_URL="http://127.0.0.1:9/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertLess(took, 10)

    def test_proceeds_immediately_when_server_is_up(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Quiet)
        self.addCleanup(srv.server_close)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(srv.shutdown)
        url = "http://127.0.0.1:%d/" % srv.server_address[1]
        r, took = self._run(OPOS_KIOSK_URL=url)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertLess(took, 5)

    def test_waits_for_late_server_then_launches(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        hits = []

        class Seen(_Quiet):
            def do_GET(self):
                hits.append(time.monotonic())
                super().do_GET()

        def late():
            time.sleep(2)
            self.addCleanup(self._stop, _serve(Seen, port))

        threading.Thread(target=late, daemon=True).start()
        stub = self._stub()
        start = time.monotonic()
        r, took = self._run(OPOS_KIOSK_WAIT="15", OPOS_KIOSK_CHROMIUM="sh " + stub,
                            OPOS_KIOSK_URL="http://127.0.0.1:%d/" % port)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(hits, "script never reached the late server")
        self.assertGreaterEqual(hits[0] - start, 2)
        self.assertGreaterEqual(took, 2)
        self.assertLess(took, 10)
        with open(stub + ".ran") as f:
            ran = f.read()
        self.assertIn("--kiosk", ran)
        self.assertIn(URL, ran)

    def test_stalled_server_hits_deadline_and_launches(self):
        srv = _serve(_Stall)
        self.addCleanup(self._stop, srv)
        stub = self._stub()
        r, took = self._run(OPOS_KIOSK_WAIT="2", OPOS_KIOSK_CHROMIUM="sh " + stub,
                            OPOS_KIOSK_URL="http://127.0.0.1:%d/" % srv.server_address[1])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(took, 10)
        self.assertTrue(os.path.exists(stub + ".ran"))


if __name__ == "__main__":
    unittest.main()
