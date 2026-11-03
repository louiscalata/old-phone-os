# SPDX-License-Identifier: GPL-3.0-or-later
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request


class TestDemoQuickstart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        except OSError as exc:
            raise unittest.SkipTest(str(exc))
        finally:
            sock.close()
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "opos.web", "--port", str(cls.port)],
            env={**os.environ, "PYTHONPATH": os.path.join(repo, "src")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.url = "http://127.0.0.1:%d/?demo=1" % cls.port
        end = time.time() + 10
        while True:
            try:
                with urllib.request.urlopen(cls.url, timeout=1):
                    break
            except Exception:
                if time.time() >= end:
                    break
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()

    def test_readme_documents_quickstart(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo, "README.md"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("PYTHONPATH=src python3 -m opos.web --port 8765", text)

    def test_demo_page_serves_battery(self):
        with urllib.request.urlopen(self.url, timeout=5) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            body = response.read().decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("Battery", body)

    def test_server_still_running(self):
        self.assertIsNone(self.proc.poll())
